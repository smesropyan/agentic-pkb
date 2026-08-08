"""The ``threads`` table and the id algebra it rests on (SV-9 … SV-13, SV-24, SV-27, ST-5 … ST-13).

Two things are being pinned here, and they fail in opposite directions.

The **id algebra** fails silently. A thread id is the checkpointer's only key (D-6: ``checkpoint_ns``
is unusable as a second dimension), so two threads that share an id share a conversation, with no
error anywhere — the Librarian graph reading the Cooking expert's four messages verbatim is a
measured fact, not a worry. Everything in § namespaces exists so that the three shapes a thread id
can take can never collide, and so that the one parse that could plausibly be written wrong — an
agent id containing ``/``, split on the wrong separator — is written right.

The **table** fails loudly but late. It is an index for discovery and never the authority on
existence (the checkpoint is), so the rules that matter are the ones about a row that is *missing*:
a derived thread still resolves without one, and touching it makes one. The single worst outcome
Layer 3 can produce is a pending approval no channel can list — arch §8 promises it cannot happen —
which is why ST-10 … ST-12 are asserted through the real ``RuntimeService`` observation path against
a scripted runtime rather than against ``ThreadStore`` alone: the bookkeeping is what could drop it.

Everything runs on a real ``aiosqlite`` connection over ``tmp_path``. No harness, no model, no
network: ``RuntimeService`` is constructor-injected with a *structural* runtime precisely so this is
possible (SV-4).
"""

from __future__ import annotations

import asyncio
import dataclasses
import sqlite3
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import aiosqlite
import pytest
from hypothesis import given
from hypothesis import strategies as st

from pkb.contracts import (
    EXPERT_THREAD_SEPARATOR,
    SCAN_THREAD_PREFIX,
    ActionView,
    AgentDescriptor,
    AgentEvent,
    ApprovalMode,
    ApprovalRequest,
    Decision,
    InterruptEvent,
    MessageDelta,
    MessageView,
    OriginChannel,
    RunEnd,
    ScanRequest,
    ScanResult,
    SubagentStart,
    ThreadBusyError,
    UnknownThreadError,
    agent_for_thread,
    expert_thread_id,
    is_scan_thread,
    librarian_thread_id,
)
from pkb.core.models import FlushReport
from pkb.service import RunSubscription, Thread
from pkb.service.proposals import TABLE as PROPOSALS_TABLE
from pkb.service.runtime import RuntimeService
from pkb.service.threads import MIGRATIONS_TABLE, TABLE, ThreadStore, mint_thread_id
from tests.server.stub import AGENTS, COOKING, LIBRARIAN

GRILLING = "topic/cooking/grilling"
"""A sub-topic of ``topic/cooking``: the id that makes prefix matching and naive splitting wrong."""

ACTION = ActionView(
    tool="write_file",
    args={"path": "Cooking/notes/steak.md"},
    description="+ Sear it hot, then rest it.",
    allowed_decisions=("approve", "reject"),
    reason="breadth-approval",
)


def at(hour: int, minute: int = 0) -> datetime:
    """A fixed instant. ``_now`` renders to whole seconds, so ordering needs explicit stamps."""
    return datetime(2026, 8, 7, hour, minute, tzinfo=UTC)


def approval(thread_id: str, agent_id: str, interrupt_id: str) -> ApprovalRequest:
    return ApprovalRequest(
        interrupt_id=interrupt_id, agent_id=agent_id, thread_id=thread_id, actions=(ACTION,)
    )


# --------------------------------------------------------------------------------------
# A structural runtime — SV-4's promise, used as a test double
# --------------------------------------------------------------------------------------


class FakeRuntime:
    """The shape ``RuntimeService`` depends on, with a scripted stream instead of a graph.

    ``RuntimeService`` is injected with a *structural* runtime rather than a concrete ``PkbRuntime``
    (SV-4), which is what lets the real service class be driven with no harness, no checkpointer and
    no model. The event script is a plain attribute so a test can build one that names the thread id
    the service minted a moment earlier.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.script: list[AgentEvent] = []
        self.pending: ApprovalRequest | None = None
        self.messages: tuple[MessageView, ...] = ()
        self.deleted: list[str] = []
        self.on_delete: Callable[[str], Awaitable[None]] | None = None

    def list_agents(self) -> Sequence[AgentDescriptor]:
        return AGENTS

    async def run(
        self,
        agent_id: str,
        thread_id: str,
        message: str,
        *,
        approval_mode: ApprovalMode = "interactive",
        run_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        for event in self.script:
            yield event

    async def resume(
        self,
        agent_id: str,
        thread_id: str,
        decisions: Sequence[Decision],
        *,
        interrupt_id: str | None = None,
        run_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        for event in self.script:
            yield event

    async def cancel(self, run_id: str) -> None:
        return None

    async def pending_approval(self, agent_id: str, thread_id: str) -> ApprovalRequest | None:
        return self.pending

    async def history(self, agent_id: str, thread_id: str) -> Sequence[MessageView]:
        return self.messages

    async def delete_thread(self, thread_id: str) -> None:
        if self.on_delete is not None:
            await self.on_delete(thread_id)
        self.deleted.append(thread_id)

    async def request_scan(self, request: ScanRequest) -> ScanResult:
        raise NotImplementedError

    async def regenerate(self) -> FlushReport:
        raise NotImplementedError


@asynccontextmanager
async def thread_store(db_path: Path) -> AsyncIterator[ThreadStore]:
    """Layer 3's own connection, in autocommit, over a real file (ST-2, ST-3)."""
    connection = await aiosqlite.connect(str(db_path), isolation_level=None)
    try:
        store = ThreadStore(connection)
        await store.setup()
        yield store
    finally:
        await connection.close()


@asynccontextmanager
async def service_over(
    db_path: Path, runtime: FakeRuntime
) -> AsyncIterator[tuple[RuntimeService, ThreadStore]]:
    """The real service, plus a store over the same connection to read what it wrote."""
    connection = await aiosqlite.connect(str(db_path), isolation_level=None)
    try:
        service = RuntimeService(runtime, connection)
        await service.setup()
        yield service, ThreadStore(connection)
    finally:
        await connection.close()


async def drain(subscription: RunSubscription) -> list[AgentEvent]:
    """Consume a run to its end, then let the off-critical-path titling task settle (TT-2)."""
    events = [event async for event in subscription.events]
    await asyncio.sleep(0.05)
    return events


def table_columns(db_path: Path, table: str) -> list[tuple[str, str, int, int]]:
    """``(name, declared type, NOT NULL, primary key)`` for one table, straight from SQLite."""
    with sqlite3.connect(db_path) as raw:
        return [
            (str(r[1]), str(r[2]), int(r[3]), int(r[5]))
            for r in raw.execute(f"PRAGMA table_info({table})")
        ]


def table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as raw:
        rows = raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {str(row[0]) for row in rows if not str(row[0]).startswith("sqlite_")}


# --------------------------------------------------------------------------------------
# § namespaces — SV-9, SV-10, SV-11
# --------------------------------------------------------------------------------------

AGENT_IDS = st.lists(
    st.sampled_from(["topic", "cooking", "grilling", "a_b", "x-y", "librarian", "Sourdough"]),
    min_size=1,
    max_size=4,
).map("/".join)


def test_a_minted_id_is_safe_for_both_reserved_shapes_sv9() -> None:
    """A minted id that could be mistaken for a derived or a scan id merges two conversations.

    ``agent_for_thread`` answers by *shape*, so if a ``uuid4`` could ever contain ``::`` or start
    with ``scan:`` it would resolve to an agent it has nothing to do with, and the checkpointer —
    which keys on the thread id alone (D-6) — would hand that agent another agent's messages. There
    is no error anywhere on that path, so the property is asserted over many draws rather than
    reasoned about once.
    """
    minted = [mint_thread_id() for _ in range(500)]

    assert len(set(minted)) == 500
    assert not any(EXPERT_THREAD_SEPARATOR in thread_id for thread_id in minted)
    assert not any(thread_id.startswith(SCAN_THREAD_PREFIX) for thread_id in minted)
    # Shape says nothing about a minted id: only the table can name its agent (SV-9, SV-12).
    assert {agent_for_thread(thread_id) for thread_id in minted} == {None}
    assert {librarian_thread_id(thread_id) for thread_id in minted} == {None}


@given(agent_id=AGENT_IDS, parent=st.uuids().map(str))
def test_a_derived_id_survives_an_agent_id_full_of_slashes_sv9(agent_id: str, parent: str) -> None:
    """The one parse that is plausibly written wrong: splitting a derived id on the wrong thing.

    A topic id mirrors the tree, so ``topic/cooking/grilling`` is a perfectly ordinary agent id and
    a scan id already spells ``scan:<agent>:<uuid>``. Split a derived id on ``/`` or on a single
    ``:`` and the expert resolves to ``topic`` — a *different, existing* agent — which under D-6
    means the wrong graph reads the right thread's checkpoint. Everything after the **first** ``::``
    is the agent, whatever it contains.
    """
    derived = expert_thread_id(parent, agent_id)

    assert agent_for_thread(derived) == agent_id
    assert librarian_thread_id(derived) == parent
    assert not is_scan_thread(derived)


@given(agent_id=AGENT_IDS, parent=st.uuids().map(str), scan_id=st.uuids().map(str))
def test_the_three_namespaces_never_overlap_sv9(agent_id: str, parent: str, scan_id: str) -> None:
    """Disjointness is what makes "resolve by shape first, table second" total and unambiguous.

    Every id belongs to exactly one of the three namespaces — minted, derived, maintenance — so the
    resolution order can never be a coin toss between two readings of the same string. If the shapes
    overlapped, a scan's bookkeeping context could enter a human conversation (RT-58) or a user
    operation could be refused as maintenance.
    """
    minted = mint_thread_id()
    derived = expert_thread_id(parent, agent_id)
    scan = f"{SCAN_THREAD_PREFIX}{agent_id}:{scan_id}"

    assert [is_scan_thread(minted), is_scan_thread(derived), is_scan_thread(scan)] == [
        False,
        False,
        True,
    ]
    assert librarian_thread_id(scan) is None
    assert agent_for_thread(scan) == agent_id
    assert len({minted, derived, scan}) == 3


@pytest.mark.asyncio
async def test_a_duplicate_thread_id_is_refused_by_the_table_sv11(tmp_path: Path) -> None:
    """The PRIMARY KEY is the only thing that makes a collision loud instead of silent.

    The checkpointer keys on ``thread_id`` alone and ``checkpoint_ns`` is unusable as a second
    dimension (D-6, verified: the Librarian graph on the Cooking expert's thread id read its four
    messages verbatim). So two agents sharing an id do not conflict — they *merge*, into one
    checkpoint, with no error raised anywhere and no way to tell afterwards. The insert has to be the
    thing that fails.
    """
    async with thread_store(tmp_path / "pkb.sqlite") as store:
        thread_id = mint_thread_id()
        await store.create(thread_id, LIBRARIAN, title=None, origin_channel="tui")

        with pytest.raises(sqlite3.IntegrityError):
            await store.create(thread_id, COOKING, title=None, origin_channel="telegram")

        # The loser of the race changed nothing: one row, still the Librarian's.
        rows = await store.list_threads()
        assert [(row.thread_id, row.agent_id) for row in rows] == [(thread_id, LIBRARIAN)]


@pytest.mark.asyncio
async def test_a_librarian_thread_and_the_expert_it_routed_to_never_collide_sv11(
    tmp_path: Path,
) -> None:
    """The derivation, not luck, is what keeps a fan-out from writing into its own parent.

    Both rows exist at once for the whole of a routed turn, and each resolves to its own agent. If
    the derivation ever produced the parent's id back — an empty agent id, say — the expert would
    run on the Librarian's checkpoint and quietly inherit its conversation.
    """
    async with thread_store(tmp_path / "pkb.sqlite") as store:
        parent = mint_thread_id()
        derived = store.derived_id(parent, COOKING)
        assert derived != parent

        await store.create(parent, LIBRARIAN, title=None, origin_channel="tui")
        await store.create(derived, COOKING, title=None, origin_channel="tui")

        assert await store.resolve_agent(parent) == LIBRARIAN
        assert await store.resolve_agent(derived) == COOKING


# --------------------------------------------------------------------------------------
# § the row is an index, not the authority — SV-12
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_derived_thread_with_no_row_still_resolves_sv12(tmp_path: Path) -> None:
    """The checkpoint is the authority on existence; the row is an index for discovery.

    Registration is a separate step, and any separate step can be missed — a crash between the
    fan-out and the insert, a row deleted, a client that only ever saw the id in a merged reply. If
    resolution needed the row, that thread would become unopenable and any approval parked in its
    checkpoint would be unreachable from every channel.
    """
    async with thread_store(tmp_path / "pkb.sqlite") as store:
        derived = store.derived_id(mint_thread_id(), GRILLING)

        assert await store.get(derived) is None
        assert await store.resolve_agent(derived) == GRILLING


@pytest.mark.asyncio
async def test_opening_an_unregistered_derived_thread_registers_it_sv12(tmp_path: Path) -> None:
    """Discovery repairs itself: touching an unregistered thread is what puts it back in the list.

    Without this the asymmetry is only half useful — the thread would work for whoever already knew
    its id and stay invisible to everyone else, including the human coming back from another channel
    to answer the approval it is parked on (arch §8).
    """
    runtime = FakeRuntime(tmp_path / "pkb.sqlite")
    async with service_over(tmp_path / "pkb.sqlite", runtime) as (service, store):
        parent = (await service.create_thread(LIBRARIAN, origin_channel="tui")).thread_id
        derived = store.derived_id(parent, COOKING)
        runtime.pending = approval(derived, COOKING, "int-9")

        detail = await service.get_thread(derived)

        assert detail.thread.thread_id == derived
        assert detail.thread.agent_id == COOKING
        assert detail.pending is not None and detail.pending.interrupt_id == "int-9"
        # The row now exists, carries the live approval, and is listed under the expert (RO-7).
        row = await store.get(derived)
        assert row is not None and row.pending_interrupt_id == "int-9"
        assert [t.thread_id for t in await store.list_threads(COOKING)] == [derived]


@pytest.mark.asyncio
async def test_resuming_an_unregistered_derived_thread_registers_it_sv12(tmp_path: Path) -> None:
    """SV-12's own assertion: resume on a rowless derived id resolves, and the row reappears.

    This is the reachable version of the case: the human follows "continue with the Cooking expert"
    from a merged reply, answers the gate, and the run completes cleanly. The approval resolved from
    the id alone, which is the point — but if the row is not restored on the way past, that thread is
    now a conversation with real history that no channel will ever list again, and nobody will
    notice, because nobody misses a thread they cannot see.
    """
    db = tmp_path / "pkb.sqlite"
    runtime = FakeRuntime(db)
    async with service_over(db, runtime) as (service, store):
        parent = (await service.create_thread(LIBRARIAN, origin_channel="tui")).thread_id
        derived = store.derived_id(parent, COOKING)
        runtime.pending = approval(derived, COOKING, "int-2")
        runtime.script = [RunEnd(run_id="r1", final_text="filed under Cooking")]

        events = await drain(await service.resume(derived, [Decision(type="approve")]))

        assert [getattr(event, "final_text", None) for event in events] == ["filed under Cooking"]
        assert await store.get(derived) is not None


# --------------------------------------------------------------------------------------
# § maintenance threads are not conversations — SV-13
# --------------------------------------------------------------------------------------

SCAN_THREAD = f"{SCAN_THREAD_PREFIX}{COOKING}:{uuid.uuid4()}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda s: s.get_thread(SCAN_THREAD), id="get_thread"),
        pytest.param(lambda s: s.start_run(SCAN_THREAD, "hello"), id="start_run"),
        pytest.param(lambda s: s.resume(SCAN_THREAD, [Decision(type="approve")]), id="resume"),
        pytest.param(lambda s: s.delete_thread(SCAN_THREAD), id="delete_thread"),
    ],
)
async def test_every_user_operation_on_a_scan_thread_is_refused_sv13(
    tmp_path: Path, operation: Callable[[RuntimeService], Awaitable[object]]
) -> None:
    """A conflict scan is machine bookkeeping, and its context must never reach a human.

    A scan reads across a whole topic to find contradictions; its thread holds that raw material.
    Let a human open, run or resume it and the next turn continues *that* transcript — the scan's
    findings, phrasing and half-formed judgements — as if the human had said them. ``UnknownThread``
    rather than a bespoke error because 404 is the honest answer: there is no such conversation.
    """
    runtime = FakeRuntime(tmp_path / "pkb.sqlite")
    runtime.pending = approval(SCAN_THREAD, COOKING, "int-1")
    async with service_over(tmp_path / "pkb.sqlite", runtime) as (service, _):
        with pytest.raises(UnknownThreadError):
            await operation(service)

        # And the refusal never left a trace behind: no row, and the runtime was never touched.
        assert await store_rows(service) == []
        assert runtime.deleted == []


async def store_rows(service: RuntimeService) -> list[str]:
    return [thread.thread_id for thread in await service.list_threads()]


@pytest.mark.asyncio
async def test_a_scan_thread_is_invisible_to_every_listing_sv13(tmp_path: Path) -> None:
    """Filtering scans out at the source is what keeps them out of every channel at once.

    ``list_threads`` feeds the TUI sidebar, Telegram's thread picker and ``/health``'s counts. If the
    exclusion lived in a client instead of here, each new channel would have to remember it, and the
    first one that forgot would offer the human a "conversation" it must never open (SV-13).
    """
    async with thread_store(tmp_path / "pkb.sqlite") as store:
        real = mint_thread_id()
        await store.create(real, COOKING, title=None, origin_channel="tui")
        await store.register(SCAN_THREAD, COOKING)

        assert [t.thread_id for t in await store.list_threads()] == [real]
        assert [t.thread_id for t in await store.list_threads(COOKING)] == [real]
        assert [row[0] for row in await store.all_ids()] == [real]
        assert await store.counts() == (1, 0)


# --------------------------------------------------------------------------------------
# § the delete cascade, both directions — SV-24
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_a_parent_takes_every_thread_it_routed_to_sv24(tmp_path: Path) -> None:
    """Deleting a conversation has to delete what it spawned, or the material survives unseen.

    A routed expert thread holds the same content the human just erased — the Librarian handed it
    over verbatim. Leaving those rows behind means the deleted conversation is still readable in a
    thread the human never knew existed, and there is no undo to fix it afterwards (D6).
    """
    async with thread_store(tmp_path / "pkb.sqlite") as store:
        parent = mint_thread_id()
        cooking = store.derived_id(parent, COOKING)
        grilling = store.derived_id(parent, GRILLING)
        bystander = mint_thread_id()
        for thread_id, agent_id in (
            (parent, LIBRARIAN),
            (cooking, COOKING),
            (grilling, GRILLING),
            (bystander, LIBRARIAN),
        ):
            await store.create(thread_id, agent_id, title=None, origin_channel="tui")

        assert await store.delete_cascade(parent) == 3

        assert [t.thread_id for t in await store.list_threads()] == [bystander]


@pytest.mark.asyncio
async def test_deleting_a_derived_thread_reaches_neither_sideways_nor_upwards_sv24(
    tmp_path: Path,
) -> None:
    """The cascade is one-directional, mirroring the runtime's own (RT-48).

    Dropping the Cooking expert's copy of a turn is not a decision about the Grilling expert's copy,
    and it is certainly not a decision about the conversation the human is still holding. A cascade
    that walked upwards would turn "tidy this one branch" into "erase the whole thread", with no
    undo (D6).
    """
    async with thread_store(tmp_path / "pkb.sqlite") as store:
        parent = mint_thread_id()
        cooking = store.derived_id(parent, COOKING)
        grilling = store.derived_id(parent, GRILLING)
        for thread_id, agent_id in (
            (parent, LIBRARIAN),
            (cooking, COOKING),
            (grilling, GRILLING),
        ):
            await store.create(thread_id, agent_id, title=None, origin_channel="tui")

        assert await store.delete_cascade(cooking) == 1

        assert {t.thread_id for t in await store.list_threads()} == {parent, grilling}


@pytest.mark.asyncio
async def test_the_checkpoints_go_before_the_rows_sv24(tmp_path: Path) -> None:
    """Order matters: rows outliving their checkpoints offer conversations that open empty.

    The row is a pointer into the checkpointer. Delete the rows first and a crash in between leaves
    checkpoints nothing indexes — invisible, but still on disk. Delete the checkpoints first and the
    same crash leaves rows that resolve to nothing, which the next open repairs into an honest 404.
    The probe asserts the *observable* order: when the runtime is called, all three rows are still
    there.
    """
    db = tmp_path / "pkb.sqlite"
    runtime = FakeRuntime(db)
    async with service_over(db, runtime) as (service, store):
        parent = mint_thread_id()
        await store.create(parent, LIBRARIAN, title=None, origin_channel="tui")
        await store.register(store.derived_id(parent, COOKING), COOKING)
        await store.register(store.derived_id(parent, GRILLING), GRILLING)

        seen: list[int] = []

        async def probe(thread_id: str) -> None:
            seen.append(len(await store.list_threads()))

        runtime.on_delete = probe
        await service.delete_thread(parent)

        assert runtime.deleted == [parent]
        assert seen == [3]
        assert await store.list_threads() == []


class ParkedRuntime(FakeRuntime):
    """A runtime whose turn emits its first event at once and then stays in flight.

    ``RuntimeService`` admits a run by racing its first event against a 250 ms deadline, so a stream
    that yields immediately is a run the supervisor has filed under a real id and is still driving —
    which is exactly the state RO-16 is about. ``release`` ends the turn, so the test never depends
    on a sleep.
    """

    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self.release = asyncio.Event()

    async def run(
        self,
        agent_id: str,
        thread_id: str,
        message: str,
        *,
        approval_mode: ApprovalMode = "interactive",
        run_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        yield MessageDelta(run_id="run-1", agent_id=agent_id, text="sear it hot, ")
        await self.release.wait()
        yield RunEnd(run_id="run-1", final_text="then rest it for five minutes")


@pytest.mark.asyncio
async def test_deleting_a_thread_under_a_live_run_is_refused_ro16(tmp_path: Path) -> None:
    """The one delete that cannot be taken back is the one taken while the turn is still writing.

    Deletion is already irreversible — checkpoints and every derived expert thread go, and there is
    no undo (D6). Doing it to a *running* turn adds a second failure on top: the run keeps going
    against a checkpoint that no longer exists, and whatever it files lands in a conversation the
    human believes they erased. Refusing costs the human one extra call — cancel, then delete — and
    that call is the one that says they meant it. A 204 here is a promise the system cannot keep.
    """
    db = tmp_path / "pkb.sqlite"
    runtime = ParkedRuntime(db)
    async with service_over(db, runtime) as (service, _store):
        thread_id = (await service.create_thread(LIBRARIAN, origin_channel="tui")).thread_id
        subscription = await service.start_run(thread_id, "how do I sear a steak?")
        try:
            # Admitted, filed under its run id, and still being driven by the daemon: exactly the
            # window RO-16 names. Asserted through `attach`, which is how RO-17 answers the same
            # question for a reconnecting client.
            assert await service.attach(thread_id) is not None

            with pytest.raises(ThreadBusyError):
                await service.delete_thread(thread_id)
        finally:
            runtime.release.set()
            await drain(subscription)


# --------------------------------------------------------------------------------------
# § the schema — ST-5, ST-6, ST-7
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_table_has_exactly_the_seven_stored_columns_st5(tmp_path: Path) -> None:
    """``parent_thread_id`` and ``kind`` are absent on purpose, and their absence is the rule.

    Both are pure functions of the thread id (LB-14). A stored copy is a second answer to a question
    the id already answers exactly, and the two disagree the moment anything writes one without the
    other — at which point a thread's parent is whatever the stale column says. ``title`` is nullable
    for the opposite reason: null and empty are different states, and a client that cannot tell "not
    titled yet" from "titled empty" renders a placeholder over a name somebody chose (TT-1, SV-26).
    """
    db = tmp_path / "pkb.sqlite"
    async with thread_store(db):
        pass

    assert table_columns(db, TABLE) == [
        ("thread_id", "TEXT", 0, 1),
        ("agent_id", "TEXT", 1, 0),
        ("title", "TEXT", 0, 0),
        ("created_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
        ("origin_channel", "TEXT", 1, 0),
        ("pending_interrupt_id", "TEXT", 0, 0),
    ]


@pytest.mark.asyncio
async def test_an_untitled_row_round_trips_as_null_not_empty_st5(tmp_path: Path) -> None:
    """ "Not titled yet" and "titled empty" are two states, and only one wants a placeholder."""
    async with thread_store(tmp_path / "pkb.sqlite") as store:
        untitled = await store.create(mint_thread_id(), LIBRARIAN, title=None, origin_channel="tui")
        blank = await store.create(mint_thread_id(), LIBRARIAN, title="", origin_channel="tui")

        assert untitled.title is None
        assert blank.title == ""
        assert (await store.get(untitled.thread_id) or untitled).title is None
        assert (await store.get(blank.thread_id) or untitled).title == ""


@pytest.mark.asyncio
async def test_kind_and_parent_are_computed_from_the_id_st6(tmp_path: Path) -> None:
    """A client must be able to tell a routed thread from a direct one without sniffing strings.

    Under RO-7 the work the Librarian routed to Cooking sits in the same list as the conversations
    the human held with Cooking directly. If telling them apart meant looking for ``::`` in the id,
    every channel would carry its own copy of the id convention — and the TUI, Telegram and MCP would
    disagree about the same list the day the convention changed.
    """
    async with thread_store(tmp_path / "pkb.sqlite") as store:
        parent = mint_thread_id()
        derived = store.derived_id(parent, COOKING)
        await store.create(parent, LIBRARIAN, title=None, origin_channel="tui")
        await store.create(derived, COOKING, title=None, origin_channel="tui")

        parent_row = await store.get(parent)
        derived_row = await store.get(derived)
        assert parent_row is not None and derived_row is not None

        assert (parent_row.kind, parent_row.parent_thread_id) == ("user", None)
        assert (derived_row.kind, derived_row.parent_thread_id) == ("routed", parent)
        # Computed, not stored: neither is a field, so neither can go stale.
        assert {field.name for field in dataclasses.fields(Thread)} == {
            "thread_id",
            "agent_id",
            "title",
            "created_at",
            "updated_at",
            "origin_channel",
            "pending_interrupt_id",
        }


FOREIGN_TABLES = (
    "checkpoints",
    "writes",
    "store",
    "store_vectors",
    "store_migrations",
    "vector_migrations",
    "scan_queue",
)


@pytest.mark.asyncio
async def test_setup_creates_only_the_tables_layer_3_owns_st7(tmp_path: Path) -> None:
    """Four independent writers share this file, and only one of them is Layer 3.

    ``threads`` and ``pkb_*`` are Layer 3's; ``checkpoints``/``writes`` belong to the checkpointer,
    the ``store*`` tables to the langgraph store, ``scan_queue`` to Layer 2. Creating, migrating or
    dropping any of those from here corrupts a component that has its own migrations and its own
    idea of the schema — and the damage is to the durable record of every conversation, in the one
    file that is supposed to be the single thing to back up (ST-1).
    """
    db = tmp_path / "pkb.sqlite"
    with sqlite3.connect(db) as raw:
        for name in FOREIGN_TABLES:
            raw.execute(f"CREATE TABLE {name} (id TEXT PRIMARY KEY)")
        raw.execute("INSERT INTO checkpoints(id) VALUES ('untouched')")

    runtime = FakeRuntime(db)
    async with service_over(db, runtime) as (service, _):
        await service.setup()  # twice: setup is idempotent and startup may repeat it

    added = table_names(db) - set(FOREIGN_TABLES)
    assert added == {TABLE, MIGRATIONS_TABLE, PROPOSALS_TABLE}
    assert all(name == TABLE or name.startswith("pkb_") for name in added)
    with sqlite3.connect(db) as raw:
        assert [row[0] for row in raw.execute("SELECT id FROM checkpoints")] == ["untouched"]


# --------------------------------------------------------------------------------------
# § writes are idempotent and single-statement — ST-9, ST-10, ST-13
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registering_twice_never_clobbers_the_first_row_st9(tmp_path: Path) -> None:
    """Registration runs from event callbacks while a stream is live, so it must be repeatable.

    The same derived thread is registered at fan-out (ST-12) and again from an interrupt naming it
    (ST-11), and a crash mid-run means the whole sequence replays. An upsert would reset
    ``created_at`` and overwrite a title on every repeat; ``INSERT OR IGNORE`` makes a repeat cost
    nothing and change nothing.
    """
    async with thread_store(tmp_path / "pkb.sqlite") as store:
        derived = store.derived_id(mint_thread_id(), COOKING)
        await store.register(derived, COOKING, title="Cooking — via the Librarian", now=at(9))
        await store.set_pending(derived, "int-1", now=at(9, 30))

        await store.register(derived, LIBRARIAN, title="something else", now=at(10))

        row = await store.get(derived)
        assert row is not None
        assert (row.agent_id, row.title) == (COOKING, "Cooking — via the Librarian")
        assert row.created_at == at(9)
        assert row.pending_interrupt_id == "int-1"


@pytest.mark.asyncio
async def test_touching_a_thread_moves_updated_at_and_not_created_at_st10(tmp_path: Path) -> None:
    """``updated_at`` is what the thread list sorts on, so it has to track activity, not birth.

    A conversation the human returned to yesterday sorting below one they opened and abandoned last
    month makes the list useless as the place to find unfinished work (arch §8, RO-6).
    """
    async with thread_store(tmp_path / "pkb.sqlite") as store:
        thread_id = mint_thread_id()
        await store.create(thread_id, LIBRARIAN, title=None, origin_channel="tui", now=at(9))

        await store.touch(thread_id, now=at(17))

        row = await store.get(thread_id)
        assert row is not None
        assert (row.created_at, row.updated_at) == (at(9), at(17))


@pytest.mark.asyncio
async def test_an_experts_gate_parks_on_the_derived_row_st10(tmp_path: Path) -> None:
    """The case arch §8 cares most about: an approval raised inside a fan-out must stay findable.

    The client is streaming the Librarian's thread, but the gate fired inside the Cooking expert and
    the interrupt is parked in the *derived* thread's checkpoint (LB-16). Record it against the
    thread being streamed and the badge lands on a thread that has nothing pending, while the thread
    that does have something pending looks idle — so the human, coming back hours later from another
    channel, resumes the wrong id and never finds the approval that stalled their filing.
    """
    db = tmp_path / "pkb.sqlite"
    runtime = FakeRuntime(db)
    async with service_over(db, runtime) as (service, store):
        parent = (await service.create_thread(LIBRARIAN, origin_channel="tui")).thread_id
        derived = store.derived_id(parent, COOKING)
        runtime.script = [
            SubagentStart(run_id="r1", agent_id=COOKING),
            InterruptEvent(run_id="r1", request=approval(derived, COOKING, "int-1")),
            RunEnd(run_id="r1", final_text="Cooking is awaiting your approval."),
        ]

        await drain(await service.start_run(parent, "how do I sear a steak?"))

        derived_row = await store.get(derived)
        parent_row = await store.get(parent)
        assert derived_row is not None and parent_row is not None
        assert derived_row.pending_interrupt_id == "int-1"
        assert parent_row.pending_interrupt_id is None
        # And the badge is visible from the expert's own list, where RO-7 files it.
        assert [t.pending_interrupt_id for t in await store.list_threads(COOKING)] == ["int-1"]


@pytest.mark.asyncio
async def test_a_terminal_event_without_an_interrupt_clears_the_badge_st10(tmp_path: Path) -> None:
    """A badge that outlives its approval sends the human to a thread with nothing to answer.

    The column is an index, not the authority (decision E), so it has to be cleared by the same
    stream that set it. What it must *not* do is clear on a terminal event that followed an
    interrupt — the run really did end, and the approval really is still parked.
    """
    db = tmp_path / "pkb.sqlite"
    runtime = FakeRuntime(db)
    async with service_over(db, runtime) as (service, store):
        thread_id = (await service.create_thread(COOKING, origin_channel="tui")).thread_id

        runtime.script = [
            InterruptEvent(run_id="r1", request=approval(thread_id, COOKING, "int-1")),
            RunEnd(run_id="r1", final_text="waiting on you"),
        ]
        await drain(await service.start_run(thread_id, "file this"))
        parked = await store.get(thread_id)
        assert parked is not None and parked.pending_interrupt_id == "int-1"

        runtime.script = [RunEnd(run_id="r2", final_text="filed")]
        await drain(await service.start_run(thread_id, "go ahead"))

        cleared = await store.get(thread_id)
        assert cleared is not None and cleared.pending_interrupt_id is None


@pytest.mark.asyncio
async def test_an_interrupt_naming_a_rowless_thread_creates_the_row_st11(tmp_path: Path) -> None:
    """A pending approval no channel can list is the one failure arch §8 promises cannot happen.

    It is reachable, because registration is a separate step: the fan-out gives the expert a derived
    thread and something has to insert the row. If that insert was missed — a crash, a deleted row,
    an event the service never saw — the interrupt event itself is the last chance to notice, and
    dropping it strands work the human can neither see nor answer.
    """
    db = tmp_path / "pkb.sqlite"
    runtime = FakeRuntime(db)
    async with service_over(db, runtime) as (service, store):
        parent = (await service.create_thread(LIBRARIAN, origin_channel="tui")).thread_id
        derived = store.derived_id(parent, COOKING)
        # No SubagentStart: the fan-out's registration never happened.
        runtime.script = [
            InterruptEvent(run_id="r1", request=approval(derived, COOKING, "int-4")),
            RunEnd(run_id="r1", final_text="Cooking is awaiting your approval."),
        ]

        await drain(await service.start_run(parent, "file this under cooking"))

        row = await store.get(derived)
        assert row is not None
        assert (row.agent_id, row.pending_interrupt_id) == (COOKING, "int-4")
        assert [t.thread_id for t in await store.list_threads(COOKING)] == [derived]


@pytest.mark.asyncio
async def test_a_fan_out_registers_one_row_per_expert_and_none_for_a_helper_st12(
    tmp_path: Path,
) -> None:
    """The catalog check is what separates a routed expert from an expert's own helper subagent.

    Both arrive as ``SubagentStart`` and neither event carries a thread id (C-10). A routed expert
    runs on its own derived thread and needs a row; an expert delegating to ``general-purpose`` runs
    in a nested namespace under the *same* thread (RT-44) and must get none — a row for it would put
    an internal implementation detail in the human's thread list, under an agent id that resolves to
    nothing.
    """
    db = tmp_path / "pkb.sqlite"
    runtime = FakeRuntime(db)
    async with service_over(db, runtime) as (service, store):
        parent = (await service.create_thread(LIBRARIAN, origin_channel="tui")).thread_id
        runtime.script = [
            SubagentStart(run_id="r1", agent_id=COOKING),
            SubagentStart(run_id="r1", agent_id=GRILLING),
            SubagentStart(run_id="r1", agent_id="general-purpose"),
            RunEnd(run_id="r1", final_text="done"),
        ]

        await drain(await service.start_run(parent, "sear a steak and light the grill"))

        assert {t.thread_id for t in await store.children(parent)} == {
            store.derived_id(parent, COOKING),
            store.derived_id(parent, GRILLING),
        }
        assert await store.get(store.derived_id(parent, "general-purpose")) is None
        # The generated name states the thread's provenance, since no human ever named it (SV-28).
        titles = {t.title for t in await store.children(parent)}
        assert titles == {"Cooking — via the Librarian", "Grilling — via the Librarian"}


@pytest.mark.asyncio
async def test_origin_channel_records_where_a_thread_started_st13(tmp_path: Path) -> None:
    """It is where the conversation *started*, not where it was last seen — and nothing branches on it.

    D3's promise is that a thread started in the TUI is finishable from Telegram, so continuing one
    from another channel must leave the column alone: a "last seen" column would invite exactly the
    ``if origin_channel == …`` that deletes the guarantee (RO-22). The closed ``Literal`` is what
    makes every adapter stamp the same word instead of its own spelling.
    """
    assert get_args(OriginChannel) == ("tui", "telegram", "mcp", "http")

    async with thread_store(tmp_path / "pkb.sqlite") as store:
        thread_id = mint_thread_id()
        await store.create(thread_id, COOKING, title=None, origin_channel="tui")

        # Telegram picks the thread up and answers the approval it was parked on.
        await store.register(thread_id, COOKING, origin_channel="telegram")
        await store.set_pending(thread_id, None)
        await store.touch(thread_id)

        row = await store.get(thread_id)
        assert row is not None and row.origin_channel == "tui"


@pytest.mark.asyncio
async def test_a_derived_thread_inherits_its_parents_origin_channel_st13(tmp_path: Path) -> None:
    """A routed thread was started by the same human, in the same place, as the turn that spawned it.

    ST-13 says the derived thread inherits its parent's value. Stamping ``http`` on it instead makes
    the column ethnography rather than data: "where did this conversation start" answers with the
    transport default for every thread the Librarian ever routed, and the one question the column
    exists to answer becomes unanswerable for exactly the threads a fan-out produced.
    """
    db = tmp_path / "pkb.sqlite"
    runtime = FakeRuntime(db)
    async with service_over(db, runtime) as (service, store):
        parent = (await service.create_thread(LIBRARIAN, origin_channel="telegram")).thread_id
        runtime.script = [
            SubagentStart(run_id="r1", agent_id=COOKING),
            RunEnd(run_id="r1", final_text="done"),
        ]

        await drain(await service.start_run(parent, "how do I sear a steak?"))

        derived = await store.get(store.derived_id(parent, COOKING))
        assert derived is not None
        assert derived.origin_channel == "telegram"


# --------------------------------------------------------------------------------------
# § titles — SV-27, TT-3, TT-4
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_title_is_written_once_and_a_second_call_is_a_no_op_sv27(tmp_path: Path) -> None:
    """ "Titled once" has to be a property of the statement, not of the caller's care.

    The titling call fires after every turn's reply (TT-2) and the thread list is where the human
    looks for the approval they left pending yesterday. A name that moves under them each turn makes
    that list unusable — you cannot come back to something you can no longer recognise. ``WHERE title
    IS NULL`` means a second caller cannot get it wrong, and the boolean lets it log the no-op rather
    than guess.
    """
    async with thread_store(tmp_path / "pkb.sqlite") as store:
        thread_id = mint_thread_id()
        await store.create(thread_id, LIBRARIAN, title=None, origin_channel="tui")

        assert await store.title_once(thread_id, "Searing a ribeye") is True
        assert await store.title_once(thread_id, "Something the model liked better") is False

        row = await store.get(thread_id)
        assert row is not None and row.title == "Searing a ribeye"


@pytest.mark.asyncio
async def test_a_human_set_title_is_never_overwritten_by_a_later_turn_tt4(tmp_path: Path) -> None:
    """The human's name for a thread wins permanently, whether they set it first or later.

    Both directions matter: a title supplied at creation (Telegram supplies none, the TUI may) and a
    rename after the fact. Either way the next turn's titling call must find the column non-null and
    do nothing — there is no undo, so a name overwritten is a name gone (D6).
    """
    async with thread_store(tmp_path / "pkb.sqlite") as store:
        supplied = mint_thread_id()
        renamed = mint_thread_id()
        await store.create(supplied, LIBRARIAN, title="Weeknight cooking", origin_channel="mcp")
        await store.create(renamed, LIBRARIAN, title=None, origin_channel="tui")
        await store.set_title(renamed, "Grill notes", now=at(12))

        assert await store.title_once(supplied, "Model's idea") is False
        assert await store.title_once(renamed, "Model's idea") is False

        titles = {t.thread_id: t.title for t in await store.list_threads()}
        assert titles == {supplied: "Weeknight cooking", renamed: "Grill notes"}


@pytest.mark.asyncio
async def test_a_routed_thread_cannot_be_renamed_sv27(tmp_path: Path) -> None:
    """Its name states its provenance, and the human never held that conversation to name it."""
    db = tmp_path / "pkb.sqlite"
    runtime = FakeRuntime(db)
    async with service_over(db, runtime) as (service, store):
        parent = (await service.create_thread(LIBRARIAN, origin_channel="tui")).thread_id
        derived = store.derived_id(parent, COOKING)
        await store.register(derived, COOKING, title="Cooking — via the Librarian")

        with pytest.raises(UnknownThreadError):
            await service.set_title(derived, "My steak thread")

        renamed = await service.set_title(parent, "My steak thread")
        assert renamed.title == "My steak thread"


# --------------------------------------------------------------------------------------
# § the listing — RO-6
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_pending_approval_sorts_ahead_of_a_newer_conversation_ro6(tmp_path: Path) -> None:
    """The list is designed around the abandoned approval, so that row cannot be buried.

    Arch §8's headline case is somebody answering on a phone at lunch an approval the TUI raised that
    morning. Sort by recency alone and a morning of other work pushes it down the list; sort by
    creation date and it never surfaces at all. Pending first, then most recent, is what makes the
    thread list the place that question gets answered.
    """
    async with thread_store(tmp_path / "pkb.sqlite") as store:
        for thread_id, hour in (("oldest-pending", 9), ("middle", 10), ("newest", 11)):
            await store.create(thread_id, LIBRARIAN, title=None, origin_channel="tui", now=at(hour))
        await store.set_pending("oldest-pending", "int-1", now=at(9))

        assert [t.thread_id for t in await store.list_threads()] == [
            "oldest-pending",
            "newest",
            "middle",
        ]
        assert await store.counts() == (3, 1)


@pytest.mark.asyncio
async def test_the_agent_filter_is_exact_and_never_a_prefix_ro6(tmp_path: Path) -> None:
    """``topic/cooking`` must not return ``topic/cooking/grilling``'s threads.

    Topic ids mirror the tree, so every parent topic is a prefix of its children. A ``LIKE
    'topic/cooking%'`` filter would fold a sub-topic's conversations into its parent's list — and
    the deeper the tree, the more the top-level topics become a dumping ground for everything below
    them, which is precisely the navigation RO-7's per-expert grouping exists to provide.
    """
    async with thread_store(tmp_path / "pkb.sqlite") as store:
        parent_topic = mint_thread_id()
        child_topic = mint_thread_id()
        routed = store.derived_id(mint_thread_id(), GRILLING)
        await store.create(parent_topic, COOKING, title=None, origin_channel="tui", now=at(9))
        await store.create(child_topic, GRILLING, title=None, origin_channel="tui", now=at(10))
        await store.register(routed, GRILLING, now=at(11))

        assert [t.thread_id for t in await store.list_threads(COOKING)] == [parent_topic]
        # The routed thread is filed under the expert that ran it, beside its own conversations.
        assert [t.thread_id for t in await store.list_threads(GRILLING)] == [routed, child_topic]
        assert [t.kind for t in await store.list_threads(GRILLING)] == ["routed", "user"]
