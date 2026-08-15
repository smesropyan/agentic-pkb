"""``RuntimeService`` — the composition root, and the **only** Layer 3 module that imports the
harness (SV-2, SV-3, decision C).

It reaches ``pkb.agents`` through the two names that package exports — ``PkbRuntime`` and
``RuntimeConfig`` — and names no harness module directly. Naming exactly one module keeps I2
structural rather than exempting a whole package: a later ``pkb/service/anything.py`` cannot inherit
the exemption silently, which is the same trick ``pkb/contracts.py`` used at the Layer 2 seam.

The class itself is **constructor-injected with a structural runtime**, never with a concrete
``PkbRuntime``. That is the property that lets the *real* service class run in a harness-banned
subprocess against a fake runtime (SV-4, SV-30) — the acceptance test Layer 2 already passes for the
seam, promoted to cover Layer 3. ``open_service`` is where the concrete runtime is actually built,
and it is an async context manager held for the daemon's lifetime because
``AsyncSqliteSaver`` closes its connection on context exit and pins itself to its creating loop
(RT-2): a module-level singleton cannot work.

**The service adds no behaviour to a run** (SV-5). Every method is either SQL over Layer 3's own
tables or one forward. It does not retry, reorder, synthesize or swallow. Where it *does* act — the
startup reconciliation, landing a completed turn in the session's own file (Task 8) — each is a rule
with an id and a reason, and each is bookkeeping about a run rather than a change to one.

**Task 10.** ``pkb.service.threads`` — the ``threads`` table ``sessions.py`` superseded — is deleted
whole, and this module is its one production importer, so three things move here rather than die
with it: :func:`open_connection` and :data:`BUSY_TIMEOUT_MS` (AP-4, ST-2 … ST-4 — the connection
discipline every Layer 3 table shares, ``sessions.py`` included, and this is the only module that
ever opens that connection) and :func:`mint_run_id` (RO-11, SS-8 — a run id, never a thread id, and
this is its only caller). ``ThreadStore`` itself, ``mint_thread_id``, and the whole thread-keyed
surface built on them (``create_thread``, ``list_threads``, ``get_thread``, ``set_title``,
``delete_thread``, ``start_run``, the thread-keyed ``attach``, and the ``_observe``/``_launch``/
``_channel``/titling machinery that served them) go with the module: sessions carry no
``origin_channel`` column, no derived-thread fan-out row and no titling call, and the one production
caller still reading a thread through this service (``pkb.server.telegram``'s TG-51 branch) is
repointed to ``get_session`` in the same commit.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import aiosqlite

from pkb.contracts import (
    AgentDescriptor,
    AgentEvent,
    MessageView,
    RunEnd,
    RunHandle,
    ScanRequest,
    ScanResult,
    UnknownAgentError,
)
from pkb.service import RunSubscription
from pkb.service.runs import RunSupervisor
from pkb.service.session_file import (
    LEARNING_AGENT_ID,
    SessionFileNoOwnFileError,
    SessionFileWriter,
    topic_tag_for_agent,
)
from pkb.service.sessions import (
    IllegalSessionTransitionError,
    Session,
    SessionList,
    SessionState,
    SessionStore,
    UnknownSessionError,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pkb.core.models import FlushReport

__all__ = [
    "ADMISSION_DEADLINE",
    "BUSY_TIMEOUT_MS",
    "RuntimeService",
    "mint_run_id",
    "open_connection",
    "open_service",
]

ADMISSION_DEADLINE = 0.25
"""How long ``start_run`` waits to learn whether a run was refused (AP-10).

A **race**, not an await. The refusals — ``ThreadBusyError``, ``ApprovalPendingError``,
``UnknownAgentError`` — are raised on the first ``__anext__`` of the runtime's generator and cost
0.01 ms, while an *admitted* run's first event is a whole model call away: measured at 2.06 s
against a 2.0 s model. Awaiting unconditionally would hold the response headers open for the length
of that call — the thing AP-10 exists to prevent, reintroduced by the obvious implementation of it.

Two thousand times the measured refusal cost, and small enough that a client never notices it.
"""

BUSY_TIMEOUT_MS = 5000
"""SQLite's default, restated so nobody lowers it (ST-4). Moved from ``threads.py`` at Task 10 —
this module is its only caller now that ``sessions.py`` takes an already-opened connection rather
than opening its own (see :func:`open_connection`'s own docstring)."""

_log = logging.getLogger(__name__)


async def open_connection(db_path: Path) -> aiosqlite.Connection:
    """Layer 3's own connection, opened **after** the runtime and in autocommit (AP-4, ST-2, ST-3).

    Moved from ``threads.py`` at Task 10 (that module is deleted whole): this is the one place Layer
    3 opens its SQLite connection, and both ``ThreadStore`` before it and ``SessionStore`` today take
    an already-opened connection as a constructor argument rather than opening their own, so the
    discipline belongs with the one caller, not with either table.

    Three settings, each measured rather than assumed:

    * **``isolation_level=None``.** aiosqlite's default is ``''`` — deferred — so a bare ``INSERT``
      opens an implicit transaction, and on a *shared* connection one coroutine's ``commit()``
      commits every other coroutine's pending statement. Measured: six coroutines, one raising
      before its own commit, six rows persisted where five were expected. Autocommit is the only
      setting consistent with ST-3's short statements and ST-9's per-write idempotence.
    * **The WAL assertion.** ``AsyncSqliteSaver.from_conn_string`` is a bare ``aiosqlite.connect``
      and the WAL pragma lives in ``setup()``, so a connection opened before the runtime talks to a
      rollback-journal file where a reader blocks a writer — measured ``delete`` before,
      ``wal`` after. Asserting turns an ordering mistake into a startup error rather than a
      deployment that is quietly slow under load.
    * **The default 5000 ms ``busy_timeout``, restated rather than changed.** At 1 ms, 600 writes
      over four connections gave ``ok=528 locked=72``; at the default, ``ok=600 locked=0``.
    """
    connection = await aiosqlite.connect(str(db_path), isolation_level=None)
    cursor = await connection.execute("PRAGMA journal_mode")
    row = await cursor.fetchone()
    mode = str(row[0]).lower() if row else "unknown"
    if mode != "wal":
        await connection.close()
        raise RuntimeError(
            f"AP-4: Layer 3's SQLite connection was opened before the runtime — journal_mode is "
            f"{mode!r}, not 'wal'. Open it after PkbRuntime.open(), which runs the saver's setup()."
        )
    await connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return connection


def mint_run_id() -> str:
    """A fresh run id, minted **before** the run starts (RO-11, SS-8).

    Moved from ``threads.py`` at Task 10 — this module is its only caller, on both the deleted
    thread-keyed ``start_run`` and today's ``start_session_run``. Layer 2 will invent one if Layer 3
    does not, but by then it is too late for two things that matter: ``run.started`` is frame 0 and
    carries the id a client cancels with, and the supervisor keys its hubs and tasks on it. A run
    that has not emitted anything yet still has to be addressable — otherwise cancelling is a race
    with the first token.
    """
    return f"run-{uuid.uuid4().hex[:12]}"


class Runtime(Protocol):
    """The structural shape :class:`RuntimeService` depends on — never a concrete ``PkbRuntime``.

    Written out rather than imported so the dependency is *structural*: the real service class can
    then be imported and driven with ``deepagents``/``langgraph``/``langchain`` banned from
    ``sys.meta_path`` (SV-4, SV-30), which is the only proof of I2 that a linter cannot give.
    """

    db_path: Path

    def list_agents(self) -> Sequence[AgentDescriptor]: ...

    def run(
        self,
        agent_id: str,
        thread_id: str,
        message: str,
        *,
        run_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]: ...

    async def cancel(self, run_id: str) -> None: ...

    async def history(self, agent_id: str, thread_id: str) -> Sequence[MessageView]: ...

    async def delete_thread(self, thread_id: str) -> None: ...

    async def request_scan(self, request: ScanRequest) -> ScanResult: ...

    async def regenerate(self) -> FlushReport: ...


class ChannelNotifier(Protocol):
    """Whatever can retitle an attached channel wherever it lives (S-16's fan-out; Task 7).

    Written out structurally, mirroring :class:`Runtime` above, for the identical reason: I2 forbids
    ``pkb.service`` from naming a transport module (a Telegram-backed implementation lives in
    ``pkb.server.telegram`` — above this layer, not below it — and the layers contract enforces
    that directly, not just for the harness). :meth:`RuntimeService.rename_session` calls this on
    every channel :class:`~pkb.service.sessions.SessionStore.channels` returns for the session being
    renamed; a ref the concrete implementation does not recognize (a non-Telegram one — Task 7's
    brief names ``"tui:<client-id>"`` as an example) is a no-op it records honestly rather than
    raises on (S-18 — that boundary is deep client UX, Phase 5's, not this file's).

    ``RuntimeService`` starts with no notifier at all (``notifier=None`` — see its constructor): the
    composition root builds the concrete Telegram one only once the bot's ``BotApi`` exists, which is
    after the service itself (``pkb.daemon._telegram_task``'s own ordering), so it is wired in by
    assignment rather than through the constructor. No notifier configured means the retitle fan-out
    is a no-op everywhere, which is correct for a deployment running no chat client at all.
    """

    async def retitle(self, channel_ref: str, name: str) -> None: ...


class RuntimeService:
    """:class:`~pkb.service.PkbService` over a real runtime and Layer 3's own SQLite tables."""

    def __init__(
        self,
        runtime: Runtime,
        connection: aiosqlite.Connection,
        *,
        kb_root: Path,
        supervisor: RunSupervisor | None = None,
    ) -> None:
        self._runtime = runtime
        self._connection = connection
        self._kb_root = kb_root
        """Held alongside ``self._session_files`` (rather than reached for through it) for Task 8's
        ``topic_tag_for_agent(self._kb_root, agent_id)`` call in :meth:`_land_turn` — the same root
        :class:`~pkb.service.session_file.SessionFileWriter` was built from, so the two can never
        resolve a topic against two different trees."""
        self._sessions = SessionStore(connection)
        self._session_files = SessionFileWriter(kb_root)
        self._runs = supervisor or RunSupervisor()
        self.notifier: ChannelNotifier | None = None
        """S-16's retitle fan-out, wired in by assignment after construction (see
        :class:`ChannelNotifier`'s own docstring for why it cannot be a constructor parameter).
        ``None`` — the default — makes :meth:`rename_session`'s fan-out a no-op, which is correct
        for a deployment with no chat client at all."""

    @property
    def runs(self) -> RunSupervisor:
        """The supervisor, for ``/health`` and for shutdown. Not part of the Protocol."""
        return self._runs

    @property
    def connection(self) -> aiosqlite.Connection:
        """Layer 3's own SQLite connection, for a transport that needs a table of its own.

        The Telegram adapter keeps durable per-chat state (ST-7's ``pkb_`` prefix), and the
        composition root is the only place that has both the service and the connection. Exposed
        rather than passed around because ``open_service`` owns the connection's lifetime and a
        second one would defeat AP-4's ordering assertion.
        """
        return self._connection

    async def setup(self) -> None:
        await self._sessions.setup()

    # ----------------------------------------------------------------------------------
    # Catalog
    # ----------------------------------------------------------------------------------

    def list_agents(self) -> Sequence[AgentDescriptor]:
        """Verbatim from the registry — no field added, nothing reordered, no model chosen (RO-4)."""
        return self._runtime.list_agents()

    def _catalog_ids(self) -> frozenset[str]:
        return frozenset(descriptor.agent_id for descriptor in self._runtime.list_agents())

    # ----------------------------------------------------------------------------------
    # Sessions (DESIGN.md §2; S-1 … S-39)
    # ----------------------------------------------------------------------------------

    def _session_catalog_ids(self) -> frozenset[str]:
        """The Librarian, every topic expert, and the Learning agent (S-9).

        The Learning agent has no registry entry yet — Phase 4 mints it — so it is added here by
        its own literal placeholder id (:data:`~pkb.service.session_file.LEARNING_AGENT_ID`, the
        same one :class:`~pkb.service.session_file.SessionFileWriter` already refuses to open a
        file for) rather than left for the catalog check to reject a legitimate target for want of
        a row nobody can add yet.
        """
        return self._catalog_ids() | {LEARNING_AGENT_ID}

    async def create_session(
        self,
        agent_id: str,
        *,
        objective: str | None = None,
        operator: str = "operator",
        name: str | None = None,
    ) -> Session:
        """S-9: validate the agent, then the store row, then the file — in that order.

        **Failure order and its observable state.** The row lands first because it is what makes
        the session *discoverable and named* (mirrors ``create_thread``'s own ordering, SV-8) and
        because the file's own path is a function of the row's disambiguated ``name`` — there is no
        name to create a file under until the store has minted one. If the file step then refuses
        (:class:`~pkb.service.session_file.SessionFileError`, most likely
        :class:`~pkb.service.session_file.SessionFileInvalidError` or an ``OSError`` wrapped by one
        of that module's typed errors), the row is **not** rolled back: :class:`SessionStore`
        exposes no delete, by design ("nothing moves or deletes operator content", `CLAUDE.md`), so
        there is nothing here that could remove it even if leaving a bare index row were the wrong
        answer. It is the right one in practice, though, because the store's own ``UNIQUE``
        constraint on ``name`` already forecloses the common cause of a collision — two sessions
        racing for the same slug — before the file step is ever reached; what can still fail there
        is an untracked file already sitting at the computed path, or a disk fault, and either way
        the operator sees the raised error immediately rather than a silently-vanished session. A
        row with no file is visible in ``GET /sessions`` and named clearly by the state of things:
        recoverable by hand, never lost.

        A session opened on the Learning agent (S-19, S-26) gets the row and **no** file — the file
        writer refuses one by design, so this method does not call it for that agent at all, rather
        than calling it and treating the refusal as an error it is not.
        """
        if agent_id not in self._session_catalog_ids():
            raise UnknownAgentError(f"no agent answers to the id {agent_id!r}")
        session = await self._sessions.create(agent_id, objective, operator, name=name)
        if agent_id != LEARNING_AGENT_ID:
            self._session_files.create(session)
        return session

    async def get_session(self, session_id: str) -> Session:
        session = await self._sessions.get(session_id)
        if session is None:
            raise UnknownSessionError(f"no session {session_id!r}")
        return session

    async def list_sessions(
        self, agent_id: str | None = None, *, state: SessionState | None = None
    ) -> SessionList:
        """``state='closed'`` **is** the learning queue (S-23, S-25/P4): ``closed_at`` order, not
        creation order. ``queue()`` itself takes no ``agent_id`` — P4 fixes it as one global view
        with no second structure — so a caller-supplied filter is applied here, in Python, over
        that same view, never by asking the store for a second, agent-scoped query.
        """
        if state == "closed":
            sessions = await self._sessions.queue()
            return [s for s in sessions if agent_id is None or s.agent_id == agent_id]
        return await self._sessions.list(agent_id, state=state)

    async def rename_session(self, session_id: str, name: str) -> Session:
        """``/name`` (S-16, S-19): store rename, then the file's own move and retitle.

        Store-then-file, the same order and the same documented trade-off as
        :meth:`create_session`: the store's ``UNIQUE`` constraint on ``name`` is what keeps a
        collision at the file step rare rather than routine, and there is no store method to undo a
        committed rename (no delete, no second name, consistent with "nothing moves or deletes
        operator content"). A failure at the file step therefore leaves the store's ``name`` already
        the new one while the bytes are still at the old path; the raised error is the operator's
        signal, and a further ``/name`` call — to the same target or another — is how it is resolved
        by hand, the same recovery :meth:`create_session` documents.

        Refused with a distinct "no file to rename" error (S-19) for a session opened on the
        Learning agent, which never had a file to begin with (S-26) — checked *before* the store is
        touched, so a Learning-agent session's row-level ``name`` cannot drift out of step with a
        file that was never there to rename.

        The last step is S-16's retitle fan-out: every channel :meth:`session_channels` reports for
        this session gets :attr:`notifier`'s ``retitle`` call, in the channels' own deterministic
        order (:meth:`~pkb.service.sessions.SessionStore.channels`). Best-effort and *after* the
        store and the file already hold the new name — a failed retitle is logged and never rolls
        either of those back, matching :meth:`~pkb.server.telegram.TelegramAdapter._name_channel`'s
        own TG-106 rule one layer up ("a failure is a reason, never a repair"); one broken channel
        must not stop the rest of the fan-out either, so each call is isolated.
        """
        before = await self.get_session(session_id)
        if before.agent_id == LEARNING_AGENT_ID:
            raise SessionFileNoOwnFileError(
                f"session {session_id!r} opened on the Learning agent has no file of its own "
                f"(S-19, S-26); there is nothing to rename"
            )
        old_path = before.file_path
        after = await self._sessions.rename(session_id, name)
        self._session_files.rename(after, old_path)
        if self.notifier is not None:
            for channel_ref in await self._sessions.channels(session_id):
                try:
                    await self.notifier.retitle(channel_ref, after.name)
                except Exception:
                    _log.warning(
                        "could not retitle channel %r for session %r",
                        channel_ref,
                        session_id,
                        exc_info=True,
                    )
        return after

    async def close_session(self, session_id: str) -> Session:
        """``/close`` (S-17, S-20, S-21): store transition, the file's own marker entry, then every
        attached channel let go (S-17: "brings every attached channel away from the session").

        The detach loop reads :meth:`session_channels` and calls
        :meth:`~pkb.service.sessions.SessionStore.detach` per ref rather than a single bulk
        statement, matching the three-method surface Task 7's brief fixes on
        :class:`~pkb.service.sessions.SessionStore` (``attach``/``detach``/``channels``) — a fourth,
        bulk-delete method would be one more thing for a test asserting the store's own shape to
        know about, for a session that rarely holds more than a handful of channels. No retitle here
        (contrast :meth:`rename_session`): a detached channel holds no session to be titled for.

        A Learning-agent session carries no file, so the marker write is skipped for it exactly as
        :meth:`create_session` skips file creation.
        """
        session = await self._sessions.close(session_id)
        if session.agent_id != LEARNING_AGENT_ID:
            self._session_files.mark_closed(session)
        for channel_ref in await self._sessions.channels(session_id):
            await self._sessions.detach(session_id, channel_ref)
        return session

    async def attach_channel(self, session_id: str, channel_ref: str) -> None:
        """``attach`` (S-6, S-14): validated against a live session first (mirrors
        :meth:`rename_session`'s own ordering) — the store method itself stays session-agnostic, the
        same split this module keeps for every other session write."""
        await self.get_session(session_id)
        await self._sessions.attach(session_id, channel_ref)

    async def detach_channel(self, session_id: str, channel_ref: str) -> None:
        """``detach`` (S-17): never an error, mirroring the store's own discipline — no existence
        check here, unlike :meth:`attach_channel`, because refusing to detach a channel from a
        session that turned out not to exist would be refusing the caller the very state they asked
        for."""
        await self._sessions.detach(session_id, channel_ref)

    async def session_channels(self, session_id: str) -> list[str]:
        """Every channel currently attached to ``session_id`` (S-6)."""
        return await self._sessions.channels(session_id)

    async def end_session(self, session_id: str) -> Session:
        """``/end`` (S-22): legal only from ``closed``; seals the file (S-24/P3).

        The store's ``state`` is the single source of truth for sealed-ness (P3) — the file's own
        ``## Ended`` marker is a human-readable echo of it, not a second authority, which is why a
        Learning-agent session (no file) still ends cleanly with the marker step simply skipped.
        """
        session = await self._sessions.end(session_id)
        if session.agent_id != LEARNING_AGENT_ID:
            self._session_files.mark_ended(session)
        return session

    async def start_session_run(self, session_id: str, message: str) -> RunSubscription:
        """``POST /sessions/{id}/runs``: resolve, admit, and hand back a subscription.

        Re-homed from ``start_run``'s thread-keyed admission race (AP-10) rather than sharing it:
        a session carries no ``pending_interrupt_id`` column and forks into no derived row on a
        fan-out (S-12 — a session that crosses topics re-opens fresh, on the Librarian, rather than
        forking), so there is no *table* this needs to keep honest as the stream goes by, the way
        ``_observe`` does for a thread. The events themselves still relay untouched (SV-5) — a
        subscriber sees exactly the objects the runtime yielded, in order, nothing added or dropped
        — but Task 8 wraps the stream in :meth:`_observe_session`, whose only side effect is landing
        the completed turn in the session's own *file* once ``RunEnd`` carries its ``final_text``
        (S-28, S-30); see that method and :meth:`_land_turn` for the write itself and why it can
        never fail the run.

        Carries no ``approval_mode`` (Task 6). Before the gates died, MCP set ``propose_only``
        in-process (RT-42, SV-17) so its writes auto-rejected a gate no robot could answer; no graph
        composes a gate any longer (``pkb.agents.gates.build_interrupt_on`` is no longer called from
        ``build_expert``/``build_librarian``), so there is nothing left to route around and nothing
        left to pass. Removing the parameter is the honest default over keeping an inert one: RO-11's
        reasoning that it must never reach HTTP has nothing left to guard either, since the whole
        distinction it named is gone.
        """
        session = await self.get_session(session_id)
        if session.state != "open":
            raise IllegalSessionTransitionError(
                f"session {session_id!r} is {session.state!r}; a run is refused on any session "
                f"that is not open (S-20: a closed session 'takes no more turns'; S-24/P3: a "
                f"sealed one never reopens)"
            )
        minted = mint_run_id()
        stream = self._runtime.run(session.agent_id, session_id, message, run_id=minted)
        return await self._launch_session(session_id, session.agent_id, minted, message, stream)

    async def attach_session(self, session_id: str) -> RunSubscription | None:
        return self._runs.attach(session_id)

    async def _launch_session(
        self,
        session_id: str,
        agent_id: str,
        run_id: str,
        message: str,
        stream: AsyncIterator[AgentEvent],
    ) -> RunSubscription:
        """The admission race (AP-10) — mirrors ``_launch`` with no *thread-table* bookkeeping, plus
        :meth:`_observe_session`'s own bookkeeping of the session's *file* (Task 8).

        ``message`` is threaded through only so :meth:`_observe_session` can compose the record
        entry once the run ends; it is never inspected before then.
        """

        async def starter() -> tuple[RunHandle, AsyncIterator[AgentEvent]]:
            iterator = stream.__aiter__()
            first = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({first}, timeout=ADMISSION_DEADLINE)
            if done:
                try:
                    admitted: AgentEvent | None = first.result()
                except StopAsyncIteration:
                    admitted = None
                head = _prepend(admitted, iterator) if admitted is not None else _drain(iterator)
            else:
                head = _await_first(first, iterator)
            handle = RunHandle(run_id=run_id, agent_id=agent_id, thread_id=session_id)
            observed = self._observe_session(head, session_id, agent_id, message)
            return handle, observed

        return await self._runs.start(session_id, starter)

    async def _observe_session(
        self,
        stream: AsyncIterator[AgentEvent],
        session_id: str,
        agent_id: str,
        message: str,
    ) -> AsyncIterator[AgentEvent]:
        """Relay a session run's events untouched, landing the completed turn on the way past
        (Task 8, S-28, S-30).

        Mirrors ``_observe``'s own shape — a side effect keyed off the events going by, never a
        change to one of them — but what it keeps honest is the session's *file* rather than a
        thread row. ``RunEnd`` is the one frame :meth:`start_session_run`'s docstring promises the
        write depends on: it is the only event carrying ``final_text``, and S-28 asks for exactly
        that text, verbatim.

        **A failed or cancelled run records nothing — deliberately, and narrower than it could be.**
        The plan's own words are "after **each completed run**", and ``RunError`` carries no
        ``final_text`` to satisfy S-28's "verbatim" with; inventing a different template for an
        error nobody specified would be asserting a design choice this task was never given. It is
        also the honest boundary rather than a chosen one on one path: a run the daemon cancels
        (``pkb.service.runs.RunSupervisor._drive``'s own ``except asyncio.CancelledError``)
        synthesizes its terminal ``RunError`` *after* this generator's own stream has already ended
        — downstream of every event this method ever sees — so that frame could not be caught here
        even if the decision above went the other way; catching it would mean teaching the
        harness-free ``RunSupervisor`` about sessions, which is a larger redesign Task 8 does not
        take on. A genuine in-graph failure (Layer 2 itself yielding ``RunError`` mid-stream,
        ``pkb.agents.events``) *is* visible here and is skipped by this same rule.

        The write happens **before** the event is yielded downstream, so a subscriber that
        observes ``RunEnd`` on the wire is guaranteed the record already holds it — mirrors
        ``_observe``'s own touch-before-``yield`` ordering for a thread's ``updated_at``.
        """
        async for event in stream:
            if isinstance(event, RunEnd):
                await self._land_turn(session_id, agent_id, message, event.final_text)
            yield event

    async def _land_turn(self, session_id: str, agent_id: str, message: str, reply: str) -> None:
        """Append the turn to the record, verbatim, and tag the expert that answered it (S-28,
        S-30) — best-effort, because "the run already succeeded, the record is bookkeeping."

        A session founded on the Learning agent has no file at all (S-19, S-26) and is skipped
        before either write is attempted — the same guard :meth:`close_session`/:meth:`end_session`
        already use for the identical reason. ``session`` is re-read fresh rather than reusing the
        row :meth:`start_session_run` already validated as ``open``: a rename during the run would
        otherwise write the turn to a stale, already-moved path, and a race with ``/close``/``/end``
        (neither of which refuses on an active run) is exactly the sealed-file case the ``except``
        below turns into a log line rather than a crash.

        Every failure here — a sealed session raced closed mid-run, an ``OSError``, a validation
        refusal that should structurally never fire against this module's own output — is logged
        and swallowed, never raised, mirroring :meth:`_title`'s identical choice a few methods below
        (TT-2: "never the turn's failure"). The run the operator is waiting on already finished
        successfully by the time this runs; failing it now over a bookkeeping write would make the
        record more load-bearing than the design ever asks it to be.
        """
        if agent_id == LEARNING_AGENT_ID:
            return
        try:
            session = await self.get_session(session_id)
            self._session_files.append_record(session, _turn_entry(message, reply))
            topic_tag = topic_tag_for_agent(self._kb_root, agent_id)
            if topic_tag is not None:
                self._session_files.add_expert_tag(session, topic_tag)
        except Exception:
            _log.warning(
                "could not land the turn in session %r's record; the run itself already "
                "completed and is unaffected",
                session_id,
                exc_info=True,
            )

    # ----------------------------------------------------------------------------------
    # Runs
    # ----------------------------------------------------------------------------------

    async def cancel(self, run_id: str) -> None:
        """Cancel the daemon's task **and** tell the runtime, which cancels the whole family.

        Both, because they mean different things: the task owns the relay and the hub, and the
        runtime owns the graphs — a Librarian turn drives several under one run id (SV-19, RT-46).
        """
        with contextlib.suppress(Exception):
            await self._runtime.cancel(run_id)
        await self._runs.cancel(run_id)

    # ----------------------------------------------------------------------------------
    # Maintenance
    # ----------------------------------------------------------------------------------
    # No proposals surface and no startup reconciliation (Task 6). `list_proposals`,
    # `get_proposal`, `dismiss_proposal`, `proposal_count` and `reconcile` all existed to serve the
    # interrupt-resume surface: the first four read `pkb_proposals`, `pkb.service.proposals`'s durable
    # record of a write `propose_only` mode auto-rejected; `reconcile` repaired `pending_interrupt_id`
    # against the checkpointer at startup because that column could go stale across a restart. No
    # graph composes `interrupt_on` any longer, so nothing is ever pending and nothing is ever
    # proposed — there is nothing left for any of the five to do.

    async def thread_counts(self) -> tuple[int, int]:
        """``(total, pending_approvals)`` for ``/health`` (AP-19). ``pending_approvals`` is always
        ``0`` (Task 6: no graph composes ``interrupt_on``, so nothing is ever pending).

        Counts sessions, not threads, since Task 10: the ``threads`` table this method read is
        deleted along with ``pkb.service.threads``, and every session-affecting operation already
        has exactly one durable index — ``SessionStore``'s own ``sessions`` table — to count instead.
        The method keeps its Task-6-era name because ``/health``'s wire shape (``pkb.server.health``)
        is a client-facing contract this task does not touch.
        """
        total = len(await self._sessions.list(None))
        return (total, 0)

    async def run_scan(self, request: ScanRequest) -> ScanResult:
        """One scan, forwarded. The dequeue timer is the daemon's; the graph run is Layer 2's."""
        return await self._runtime.request_scan(request)

    async def regenerate(self) -> None:
        await self._runtime.regenerate()


def _prepend(first: AgentEvent, rest: AsyncIterator[AgentEvent]) -> AsyncIterator[AgentEvent]:
    """Put the admission event back on the front of the stream.

    Admission consumes one event to surface the refusals before a response commits (AP-10). That
    event is a real event and belongs on the stream — dropping it would make the service's output
    differ from the runtime's, which is exactly what SV-5 forbids.
    """

    async def stream() -> AsyncIterator[AgentEvent]:
        yield first
        async for event in rest:
            yield event

    return stream()


def _await_first(
    pending: asyncio.Future[AgentEvent], rest: AsyncIterator[AgentEvent]
) -> AsyncIterator[AgentEvent]:
    """The stream for a run admitted but not yet speaking: await the in-flight first event, then go.

    The future is already running — cancelling and re-calling ``__anext__`` would start a second
    turn — so it is awaited here, on the run task, where waiting costs nothing.
    """

    async def stream() -> AsyncIterator[AgentEvent]:
        try:
            yield await pending
        except StopAsyncIteration:
            return
        async for event in rest:
            yield event

    return stream()


def _drain(rest: AsyncIterator[AgentEvent]) -> AsyncIterator[AgentEvent]:
    """A run that ended before it said anything. Nothing to prepend, and nothing to lose."""

    async def stream() -> AsyncIterator[AgentEvent]:
        async for event in rest:
            yield event

    return stream()


def _blockquote(text: str) -> str:
    """``text``, every line prefixed ``> `` (a bare ``>`` for an empty line) — markdown's own
    quoting convention, applied line-by-line so the whole payload survives unabridged.

    The one piece of machinery that makes :func:`_turn_entry` safe against its own payload (see
    that function's docstring for the rule this exists to satisfy) — factored out because
    ``message`` and ``reply`` both need the identical treatment and there is nothing turn-specific
    about it.
    """
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))


def _turn_entry(message: str, reply: str) -> str:
    """One completed turn, verbatim (S-28): the operator's message and the run's final text, with
    no summarization and no model call — :meth:`RuntimeService._land_turn` is the only caller.

    **Both payloads are blockquoted (S-28's "verbatim" read together with S-31's fixed section
    order).** ``message`` and ``reply`` are operator/model text this module does not control, and
    ``pkb.service.session_file``'s own section surgery — :func:`~pkb.service.session_file.
    _insert_before_heading` (what ``append_record`` calls) and :func:`~pkb.service.session_file.
    _replace_section` (what ``write_synthesis`` calls) — locates the fixed sections by a raw
    ``str.find`` for the literal heading text over the *whole* document body, with no notion of
    "inside a turn's own content" versus "a real section boundary." A message whose text happens to
    contain a bare line reading ``## Synthesis`` (or ``## Distillation``) would, unquoted, hand that
    search its own fake heading before the genuine one — the write still succeeds and
    ``validate_content`` stays clean, because the result is syntactically valid markdown with every
    required field intact, but every later append lands inside what looks like ``## Synthesis`` (not
    ``## Record``) and a later ``write_synthesis`` replaces the genuine section right past a fake
    "``## Distillation``" it stops at first — silent structural corruption no schema check catches,
    because nothing about the *shape* is wrong, only which words ended up in which section.
    Prefixing every line with ``> `` makes a turn's payload structurally inert: markdown recognizes
    a heading only on a line that begins with a bare ``#``, and a quoted line never does, no matter
    what text follows the ``> ``. Quoting is lossless and mechanically reversible — every word the
    operator or the model wrote is still there, unaltered, just inside a blockquote instead of bare
    — so it satisfies "verbatim" exactly as literally as an unquoted copy would, while being the one
    transformation that cannot itself introduce a bare ``## `` line. (The sibling exposure —
    ``write_synthesis``'s own content, which the operator approves and might itself contain a
    heading-shaped line — has no caller before Phase 4 wires one; ``pkb.service.session_file``'s
    module docstring flags it there rather than fixing it speculatively here.)

    ``###`` rather than ``##`` for the entry's own heading: the four fixed section headings
    ``## Record`` through ``## Distillation`` (S-31) and the three command markers ``## Closed``/
    ``## Renamed``/``## Ended`` (S-29) all sit one level up, and a turn heading at that same level
    would risk colliding with one of them one day even quoted. No date, unlike a command marker:
    S-29 names a date for ``/close``, a rename and ``/end`` alone ("each append an entry naming the
    command and the date"), and S-28 asks for none on an ordinary turn — mirrors
    ``pkb.service.session_file._marker_entry``'s own heading-then-detail shape, but for a turn
    rather than a command.
    """
    return (
        f"### Turn\n\n**Operator:**\n\n{_blockquote(message)}\n\n"
        f"**Reply:**\n\n{_blockquote(reply)}\n"
    )


@asynccontextmanager
async def open_service(
    kb_root: Path,
    db_path: Path,
    *,
    config: Any | None = None,
    runtime_factory: Callable[..., Any] | None = None,
) -> AsyncIterator[RuntimeService]:
    """Open the runtime, then Layer 3's connection, in that order (SV-3, AP-3, AP-4).

    **The order is the rule.** ``AsyncSqliteSaver.from_conn_string`` is a bare ``aiosqlite.connect``
    and ``PRAGMA journal_mode=WAL`` is set in ``setup()``, which ``PkbRuntime.open`` calls. A
    connection opened before that talks to a rollback-journal file, where a reader blocks a writer —
    measured as ``journal_mode == 'delete'`` before and ``'wal'`` after.
    """
    # The harness import is **inside** this function, and that is SV-4 rather than style: the real
    # `RuntimeService` class has to import and run in a subprocess with `deepagents`/`langgraph`/
    # `langchain` banned from `sys.meta_path` (SV-30), driven against a fake runtime. A module-level
    # import would make that impossible while changing nothing about what this module may name —
    # `PkbRuntime` and `RuntimeConfig`, the two names `pkb.agents` exports, and no harness module.
    if runtime_factory is None:
        from pkb.agents import PkbRuntime

        runtime_factory = PkbRuntime.open
    opener = runtime_factory
    async with opener(kb_root, db_path, config=config) as runtime:
        # Opened *after*, in autocommit, with the WAL assertion — see `open_connection`. AP-4 is one
        # line away from being a bug nobody finds until a reader blocks a writer under load.
        connection = await open_connection(runtime.db_path)
        try:
            service = RuntimeService(runtime, connection, kb_root=kb_root)
            await service.setup()
            yield service
        finally:
            await connection.close()
