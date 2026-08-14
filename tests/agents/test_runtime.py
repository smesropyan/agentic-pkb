"""`PkbRuntime` — RT-1 … RT-7, RT-20, RT-36 … RT-53, MW-26 … MW-28, LB-7, EX-12.

Every test here opens a **real** runtime over a real SQLite file and drives real compiled graphs
with `ScriptedChatModel`. That is the point: the four behaviours this module exists for — the flush
on the failure path, the 409, the pending-approval refusal and the write lock — are all things the
harness does *not* do, and each was verified by execution rather than by reading. Asserting them
against a mock would assert the mock.

No API key, no network (SK-18). The model is scripted; the only I/O is a temporary directory and a
temporary SQLite file.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, Final

import pytest
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from deepagents.backends.protocol import SandboxBackendProtocol

from pkb.agents import PkbRuntime as ExportedRuntime
from pkb.agents import runtime as runtime_module
from pkb.agents.expert import build_expert
from pkb.agents.ingestion import INGEST_TOOL
from pkb.agents.librarian import build_librarian
from pkb.agents.paths import KB_MOUNT, SKILLS_MOUNT
from pkb.agents.registry import AgentRegistry
from pkb.agents.runtime import (
    _ACQUIRE_POLL_SECONDS,
    PkbRuntime,
    ReentrantWriteLock,
    RuntimeConfig,
)
from pkb.agents.tools.topics import CREATE_SUBTOPIC, CREATE_TOPIC, TopicToolEnv, topic_tools
from pkb.contracts import (
    ApprovalPendingError,
    Decision,
    InterruptEvent,
    InvalidDecisionError,
    RunEnd,
    RunError,
    StaleInterruptError,
    ThreadBusyError,
    ToolEnd,
    UnknownAgentError,
)
from pkb.core import regenerate_all
from pkb.core.scan import scan
from tests.agents.conftest import TODAY, ScriptedChatModel, call, calls, raises, says, scripted

COOKING = "topic/cooking"
GRILLING = "topic/cooking/grilling"
LIBRARIAN = "librarian"

NOTE_PATH = "Cooking/notes/reverse-sear.md"
SUMMARY_PATH = "Cooking/notes/summary.md"

VALID_NOTE = """---
title: "Reverse sear"
description: "Low oven first, then a very hot pan, for a thick steak"
topic: "Cooking"
tags:
  - topic.cooking
  - type.note
created: 2026-08-01
updated: 2026-08-01
source_type: note
---

# Reverse sear

Low oven, then a hot pan.
"""

NEW_SUMMARY = """---
title: "Notes summary"
description: "Distilled rules and solutions from the Cooking notes"
topic: "Cooking"
tags:
  - topic.cooking
  - type.summary
created: 2026-08-06
updated: 2026-08-06
source_type: summary
---

# Notes summary

Sear hot, rest long.
"""


# --------------------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------------------


def _expert_factory(model: ScriptedChatModel) -> Any:
    """`build_expert` with the scripted model substituted for the configured model id.

    The registry resolves a model *string* (RG-21) because that is what a deployment configures; a
    fake chat model is the test seam D-8 forces, and swapping it in the factory keeps every other
    part of the wiring — permissions, middleware, gates, the runtime's singletons — exactly the
    production one.
    """

    def factory(
        kb_root: Path,
        topic_path: str,
        runtime: Any,
        *,
        registry: Any = None,
        tools: Sequence[Any] = (),
        **_ignored: Any,
    ) -> Any:
        return build_expert(
            kb_root, topic_path, runtime, model=model, registry=registry, tools=tools
        )

    return factory


def _librarian_factory(model: ScriptedChatModel) -> Any:
    def factory(
        kb_root: Path,
        runtime: Any,
        *,
        registry: Any = None,
        tools: Sequence[Any] = (),
        **_ignored: Any,
    ) -> Any:
        return build_librarian(kb_root, runtime, model=model, registry=registry, tools=tools)

    return factory


def registry_factory(model: ScriptedChatModel, *, tools: bool = True) -> Any:
    def make(runtime: PkbRuntime) -> AgentRegistry:
        return AgentRegistry(
            runtime.kb_root,
            runtime,
            default_model="scripted",
            tool_factory=runtime.tools_for if tools else None,
            expert_factory=_expert_factory(model),
            librarian_factory=_librarian_factory(model),
        )

    return make


@asynccontextmanager
async def opened(
    kb: Path,
    model: ScriptedChatModel,
    *,
    clock: Callable[[], date] = lambda: TODAY,
    db: Path | None = None,
    tools: bool = True,
    **config: Any,
) -> AsyncIterator[PkbRuntime]:
    async with PkbRuntime.open(
        kb,
        db or kb.parent / "pkb.sqlite",
        config=RuntimeConfig(clock=clock, **config),
        registry_factory=registry_factory(model, tools=tools),
    ) as rt:
        yield rt


async def drain(
    rt: PkbRuntime, agent_id: str, thread_id: str, text: str = "go", **kwargs: Any
) -> list[Any]:
    return [event async for event in rt.run(agent_id, thread_id, text, **kwargs)]


async def drain_resume(
    rt: PkbRuntime, agent_id: str, thread_id: str, decisions: Sequence[Decision], **kwargs: Any
) -> list[Any]:
    return [event async for event in rt.resume(agent_id, thread_id, decisions, **kwargs)]


def writes(path: str, content: str, id_: str) -> Any:
    return calls(call("write_file", {"file_path": f"{KB_MOUNT}{path}", "content": content}, id_))


def kinds(events: Sequence[Any], kind: type) -> list[Any]:
    return [event for event in events if isinstance(event, kind)]


# --------------------------------------------------------------------------------------
# RT-A · lifecycle and shared singletons
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_graph_shares_the_runtimes_singletons_rt1(kb: Path) -> None:
    """One checkpointer, one store, one backend, one lock — for every agent at every depth."""
    async with opened(kb, scripted(says("hi"))) as rt:
        graphs = [rt._registry.get(agent) for agent in (LIBRARIAN, COOKING, GRILLING)]

        for graph in graphs:
            assert graph.checkpointer is rt.checkpointer
            assert graph.store is rt.store
        assert rt.checkpointer is not None
        # The lock is shared by construction: a second one would provide no mutual exclusion at all.
        assert rt._maintenance.lock is rt.write_lock


@pytest.mark.asyncio
async def test_the_runtime_is_a_scoped_resource_not_a_singleton_rt2(kb: Path) -> None:
    """`from_conn_string` closes the connection on exit, so the runtime cannot be built at import."""
    db = kb.parent / "pkb.sqlite"
    async with opened(kb, scripted(says("hi")), db=db) as rt:
        saver = rt.checkpointer
        assert saver is not None
        await drain(rt, COOKING, "T1")

    tables = {
        row[0]
        for row in sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"checkpoints", "writes"} <= tables

    with pytest.raises(Exception):  # noqa: B017 - the harness's own "no active connection"
        await saver.aget_tuple({"configurable": {"thread_id": "T1"}})


@pytest.mark.asyncio
async def test_the_run_api_is_async_only_rt3(kb: Path) -> None:
    """A synchronous checkpointer call from the saver's own loop raises, so there is no sync API."""
    public = {name for name in dir(PkbRuntime) if not name.startswith("_")}
    assert not (public & {"invoke", "stream", "run_sync", "stream_sync", "resume_sync"})
    assert inspect.isasyncgenfunction(PkbRuntime.run)
    assert inspect.isasyncgenfunction(PkbRuntime.resume)
    for name in ("cancel", "pending_approval", "history", "delete_thread", "regenerate"):
        assert inspect.iscoroutinefunction(getattr(PkbRuntime, name)), name

    async with opened(kb, scripted(says("hi"))) as rt:
        assert rt.checkpointer is not None
        with pytest.raises(asyncio.InvalidStateError):
            rt.checkpointer.get_tuple(rt.thread_config("T1"))


@pytest.mark.asyncio
async def test_a_second_connection_shares_the_file_rt4(kb: Path) -> None:
    """Layer 3's `threads` table and Layer 2's queue live in the checkpointer's file, WAL, own conn."""
    db = kb.parent / "pkb.sqlite"
    async with opened(kb, scripted(says("hi")), db=db) as rt:
        await drain(rt, COOKING, "T1")
        assert rt.db_path == db
        side = sqlite3.connect(db)
        assert side.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        side.execute("CREATE TABLE IF NOT EXISTS threads (thread_id TEXT PRIMARY KEY)")
        side.execute("INSERT INTO threads VALUES ('T1')")
        side.commit()
        assert side.execute("SELECT count(*) FROM checkpoints").fetchone()[0] > 0
        side.close()


@pytest.mark.asyncio
async def test_one_store_is_shared_by_every_graph_rt5(kb: Path) -> None:
    """A forward-compatibility placeholder, but a real one: over the daemon's own file (Q12)."""
    async with opened(kb, scripted(says("hi"))) as rt:
        assert rt.store is not None
        assert rt._registry.get(LIBRARIAN).store is rt._registry.get(COOKING).store is rt.store


@pytest.mark.asyncio
async def test_the_one_backend_routes_scratch_kb_and_skills_rt6(kb: Path) -> None:
    """`StateBackend` default, `/kb/` on disk shared by all, `/skills/` the packaged mount."""
    backend = None
    async with opened(kb, scripted(says("hi"))) as rt:
        backend = rt.backend
        assert isinstance(backend, CompositeBackend)
        assert isinstance(backend.default, StateBackend)
        assert set(backend.routes) == {KB_MOUNT, SKILLS_MOUNT}
        assert all(isinstance(route, FilesystemBackend) for route in backend.routes.values())

    model = scripted(
        calls(
            call("read_file", {"file_path": f"{KB_MOUNT}Cooking/topic.md"}, "r1"),
            call("read_file", {"file_path": f"{SKILLS_MOUNT}voice/SKILL.md"}, "r2"),
        ),
        says("read both"),
    )
    async with opened(kb, model) as rt:
        events = await drain(rt, COOKING, "T1")
    reads = kinds(events, ToolEnd)
    assert [event.error for event in reads] == [False, False]
    both = "\n".join(event.summary for event in reads)
    assert "Home cooking" in both  # the shared on-disk tree
    assert "voice" in both  # the packaged mount, read through the same backend


@pytest.mark.asyncio
async def test_a_scratch_write_is_thread_scoped_rt6(kb: Path) -> None:
    """Two threads write `/scratch.md`; neither sees the other's — that is what `StateBackend` buys."""
    model = scripted(
        calls(call("write_file", {"file_path": "/scratch.md", "content": "one"}, "w1")),
        says("wrote"),
        calls(call("read_file", {"file_path": "/scratch.md"}, "r1")),
        says("read"),
    )
    async with opened(kb, model) as rt:
        await drain(rt, COOKING, "T-a")
        second = await drain(rt, COOKING, "T-b")
    result = kinds(second, ToolEnd)[0]
    assert result.error is True


@pytest.mark.asyncio
async def test_startup_regenerates_a_stale_tree_and_touches_a_clean_one_rt7(kb: Path) -> None:
    """`after_agent` covers neither a crashed run (D-1) nor an abandoned approval (D-14)."""
    index = kb / "Cooking" / "index.md"
    index.write_text("stale\n", encoding="utf-8")
    async with opened(kb, scripted(says("hi"))) as rt:
        assert rt is not None
    assert index.read_text(encoding="utf-8") != "stale\n"

    before = {path: path.stat().st_mtime_ns for path in kb.rglob("*.md")}
    async with opened(kb, scripted(says("hi"))) as rt:
        assert rt is not None
    assert {path: path.stat().st_mtime_ns for path in kb.rglob("*.md")} == before


@pytest.mark.asyncio
async def test_execute_stays_inert_rt20(kb: Path) -> None:
    """`execute` bypasses permissions entirely, so it must never reach a live sandbox backend."""
    model = scripted(
        calls(call("execute", {"command": "touch /kb/Cooking/notes/pwned.md"}, "e1")),
        says("done"),
    )
    async with opened(kb, model) as rt:
        assert not isinstance(rt.backend, SandboxBackendProtocol)
        events = await drain(rt, COOKING, "T1")
    assert any(event.error for event in kinds(events, ToolEnd))
    assert not (kb / "Cooking" / "notes" / "pwned.md").exists()


# --------------------------------------------------------------------------------------
# RT-D · the run API
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_ids_are_always_explicit_rt36(kb: Path) -> None:
    """Layer 2 never invents a thread id for a user conversation, and never stores the pairing."""
    for method in (PkbRuntime.run, PkbRuntime.resume):
        params = list(inspect.signature(method).parameters)
        assert params[1:3] == ["agent_id", "thread_id"], method.__name__
    source = Path(runtime_module.__file__).read_text(encoding="utf-8")
    # The only uuid in this module mints a *run* id; scan threads are `scans.scan_thread_id`.
    assert source.count("uuid4()") == 1
    assert "def _new_run_id" in source


@pytest.mark.asyncio
async def test_the_only_config_key_is_the_thread_id_rt37(kb: Path) -> None:
    """An explicit `checkpoint_ns` breaks `aget_state` outright; the harness owns that dimension."""
    async with opened(kb, scripted(says("hi"))) as rt:
        assert rt.thread_config("T1") == {"configurable": {"thread_id": "T1"}}


@pytest.mark.asyncio
async def test_an_approval_survives_a_process_boundary_rt38(kb: Path) -> None:
    """The interrupt is durable in the checkpoint, so any client in any process can resolve it."""
    db = kb.parent / "pkb.sqlite"
    async with opened(kb, scripted(writes(SUMMARY_PATH, NEW_SUMMARY, "w1"), says("done")), db=db):
        pass  # the first runtime only creates the file

    first = scripted(writes(SUMMARY_PATH, NEW_SUMMARY, "w1"), says("done"))
    async with opened(kb, first, db=db) as rt:
        events = await drain(rt, COOKING, "T-approve")
        assert len(kinds(events, InterruptEvent)) == 1

    second = scripted(says("done"))
    async with opened(kb, second, db=db) as rt:
        pending = await rt.pending_approval(COOKING, "T-approve")
        assert pending is not None
        assert pending.actions[0].reason == "breadth-approval"
        await drain_resume(rt, COOKING, "T-approve", [Decision(type="approve")])
    assert "Sear hot, rest long." in (kb / SUMMARY_PATH).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_a_new_message_during_a_pending_approval_is_refused_rt39(kb: Path) -> None:
    """The harness would silently discard the interrupt and run the turn as if it never existed."""
    model = scripted(writes(SUMMARY_PATH, NEW_SUMMARY, "w1"), says("done"))
    async with opened(kb, model) as rt:
        await drain(rt, COOKING, "T1")
        before = await rt.pending_approval(COOKING, "T1")
        assert before is not None

        with pytest.raises(ApprovalPendingError):
            await drain(rt, COOKING, "T1", "another message")

        after = await rt.pending_approval(COOKING, "T1")
        assert after is not None
        assert after.interrupt_id == before.interrupt_id


@pytest.mark.asyncio
async def test_a_stale_interrupt_id_is_refused_before_the_graph_rt40(kb: Path) -> None:
    """A stale id degrades into a confusing count-mismatch error inside the graph; refuse first."""
    model = scripted(writes(SUMMARY_PATH, NEW_SUMMARY, "w1"), says("done"))
    async with opened(kb, model) as rt:
        await drain(rt, COOKING, "T1")
        with pytest.raises(StaleInterruptError):
            await drain_resume(
                rt, COOKING, "T1", [Decision(type="approve")], interrupt_id="not-the-one"
            )
        assert await rt.pending_approval(COOKING, "T1") is not None
        assert "Sear hot, rest long." not in (kb / SUMMARY_PATH).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_a_disallowed_decision_and_a_count_mismatch_are_refused_rt40(kb: Path) -> None:
    """`respond` reports success while skipping the tool, so no write gate ever allows it (RT-32)."""
    model = scripted(writes(SUMMARY_PATH, NEW_SUMMARY, "w1"), says("done"))
    async with opened(kb, model) as rt:
        await drain(rt, COOKING, "T1")
        with pytest.raises(InvalidDecisionError):
            await drain_resume(rt, COOKING, "T1", [Decision(type="respond", message="no")])
        with pytest.raises(InvalidDecisionError):
            await drain_resume(rt, COOKING, "T1", [])
        assert await rt.pending_approval(COOKING, "T1") is not None


@pytest.mark.asyncio
async def test_propose_only_records_a_proposal_and_writes_nothing_rt42(kb: Path) -> None:
    """An MCP caller cannot satisfy a human gate, so the run completes instead of hanging."""
    seen: list[Any] = []
    model = scripted(writes(SUMMARY_PATH, NEW_SUMMARY, "w1"), says("noted"))
    async with opened(kb, model, proposal_sink=seen.append) as rt:
        events = await drain(rt, COOKING, "T1", approval_mode="propose_only")
        proposals = rt.pending_proposals()

    assert kinds(events, InterruptEvent) == []
    assert len(kinds(events, RunEnd)) == 1
    assert len(proposals) == 1
    assert proposals[0].action.reason == "breadth-approval"
    assert proposals[0].thread_id == "T1"
    assert [proposal.proposal_id for proposal in seen] == [proposals[0].proposal_id]
    assert "Sear hot, rest long." not in (kb / SUMMARY_PATH).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_two_concurrent_runs_on_one_thread_are_refused_rt45(kb: Path) -> None:
    """LangGraph OSS has no multitask strategy: both would succeed with interleaved writes."""
    async with opened(kb, scripted(says("hi"))) as rt:
        results = await asyncio.gather(
            drain(rt, COOKING, "T-same"),
            drain(rt, COOKING, "T-same"),
            return_exceptions=True,
        )
        busy = [result for result in results if isinstance(result, ThreadBusyError)]
        assert len(busy) == 1

        both = await asyncio.gather(drain(rt, COOKING, "T-a"), drain(rt, COOKING, "T-b"))
        assert all(len(kinds(events, RunEnd)) == 1 for events in both)
        assert rt._active == {}


@pytest.mark.asyncio
async def test_a_client_that_stops_reading_still_frees_the_thread_rt45(kb: Path) -> None:
    """RT-45: the active-run slot belongs to the *run*, not to the caller's generator.

    Breaking out of the loop on the terminal event is the natural way to consume this stream, and an
    SSE handler or a stored iterator keeps the generator alive afterwards. Releasing the slot in the
    generator's `finally` therefore released it when the *caller* was collected — never, in that
    shape — and the next turn on that `(agent_id, thread_id)` was refused with a 409 against a run
    that had already finished, permanently.

    Keeping `agen` on a local is the whole test: without it, refcounting finalizes the temporary
    generator within a few ticks and the bug hides. That is why 1022 green tests missed it.
    """
    model = scripted(writes(NOTE_PATH, VALID_NOTE, "w1"), says("filed"), says("still here"))
    async with opened(kb, model) as rt:
        agen = rt.run(COOKING, "T1", "go", run_id="R1")
        events: list[Any] = []
        async for event in agen:
            events.append(event)
            if isinstance(event, RunEnd):
                break
        assert len(kinds(events, RunEnd)) == 1

        # The run's tail — MW-26's flush — is still in flight at `RunEnd`, so the slot is tied to
        # the drive task rather than to the event, and this is what "the run ended" means.
        await asyncio.wait_for(rt._tasks["R1"], timeout=20)
        assert rt._active == {}

        again = await drain(rt, COOKING, "T1", "carry on")
        assert len(kinds(again, RunEnd)) == 1
        assert agen is not None  # the abandoned generator is still referenced, as a client would


@pytest.mark.asyncio
async def test_the_active_slot_is_released_by_identity_rt45(kb: Path) -> None:
    """RT-45, the other direction: a stale release must not evict the run that took the slot next.

    The slot is now freed from two places that can run out of order — the drive task when the run
    ends, and an abandoned generator's finalization some unspecified number of ticks later. An
    unguarded `pop` would let the second evict a *later* run's registration, and a third concurrent
    run on that thread would then be admitted: the 409 this registry exists for, gone.
    """
    async with opened(kb, scripted(says("hi"))) as rt:
        key = (COOKING, "T1")
        rt._active[key] = "R2"
        rt._release_slot(key, "R1")
        assert rt._active == {key: "R2"}
        rt._release_slot(key, "R2")
        assert rt._active == {}


@pytest.mark.asyncio
async def test_a_cancelled_run_leaves_the_thread_resumable_rt46(kb: Path) -> None:
    """LangGraph has no server-side cancel, so the runtime owns `run_id -> asyncio.Task`."""

    def slow() -> Any:
        time.sleep(0.4)
        return says("late")

    model = scripted(writes(NOTE_PATH, VALID_NOTE, "w1"), slow)
    async with opened(kb, model) as rt:
        collected: list[Any] = []

        async def consume() -> None:
            async for event in rt.run(COOKING, "T1", "go", run_id="R1"):
                collected.append(event)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.15)
        await rt.cancel("R1")
        await task

        assert kinds(collected, RunEnd) == []
        state = await rt._registry.get(COOKING).aget_state(rt.thread_config("T1"))
        assert state.values["messages"]
    assert (kb / NOTE_PATH).exists()


@pytest.mark.asyncio
async def test_a_provider_error_is_one_normalized_event_rt47(kb: Path) -> None:
    """Never swallowed into a normal completion, never marked finished: the thread stays resumable."""
    model = scripted(writes(NOTE_PATH, VALID_NOTE, "w1"), raises(RuntimeError("overloaded")))
    async with opened(kb, model) as rt:
        events = await drain(rt, COOKING, "T1")
        errors = kinds(events, RunError)
        assert len(errors) == 1
        assert errors[0].retryable is True
        assert kinds(events, RunEnd) == []
        state = await rt._registry.get(COOKING).aget_state(rt.thread_config("T1"))
        assert state.next == ("model",)


@pytest.mark.asyncio
async def test_delete_thread_removes_the_experts_derived_threads_rt48(kb: Path) -> None:
    """A Librarian conversation is erased together with the expert threads it spawned.

    This rule changed shape when routing became a workflow, and the change is the whole reason the
    test is interesting. Delegated work used to checkpoint under the *Librarian's* own `thread_id` in
    a nested `tools:<uuid>` namespace (D-6), so one `adelete_thread` swept it up for free. Now the
    fan-out gives every expert its own addressable thread, `<thread>::<agent id>` (LB-14) — which was
    the point, because "continue with the Cooking expert" has to resolve to something a client can
    open — and an addressable thread does not vanish with its parent.

    A "delete this conversation" that left the expert's copy of the material behind would be the
    worst kind of lie in a system with no version control and no undo (D6), so `delete_thread`
    enumerates the catalog and deletes the derived ids too. The `writes` table is asserted as well:
    that is where the expert's exchange actually sits.
    """
    db = kb.parent / "pkb.sqlite"
    model = scripted(
        calls(call("route", {"topic_ids": [COOKING], "reason": "steak"}, "r1")),
        says("filed it"),
    )
    async with opened(kb, model, db=db) as rt:
        await drain(rt, LIBRARIAN, "T-gone", "file this for me")

        threads = {
            row[0]
            for row in sqlite3.connect(db).execute("SELECT DISTINCT thread_id FROM checkpoints")
        }
        assert "T-gone" in threads
        assert f"T-gone::{COOKING}" in threads, "the expert ran on its own addressable thread"
        assert _rows(db, "checkpoints") > 0
        assert _rows(db, "writes") > 0, "the expert's exchange is in `writes`, not only in state"

        await rt.delete_thread("T-gone")

        assert _rows(db, "checkpoints") == 0
        assert _rows(db, "writes") == 0


def _rows(db: Path, table: str) -> int:
    """Rows `T-gone` and its derived expert threads own in *table* (RT-48, LB-14)."""
    connection = sqlite3.connect(db)
    try:
        return int(
            connection.execute(
                f"SELECT count(*) FROM {table} WHERE thread_id = 'T-gone' OR thread_id LIKE"
                " 'T-gone::%'"
            ).fetchone()[0]
        )
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_no_thread_listing_is_exposed_rt49(kb: Path) -> None:
    """The checkpointer cannot answer "which agent owns this thread"; that is Layer 3's table."""
    public = {name for name in dir(PkbRuntime) if not name.startswith("_")}
    assert not (public & {"list_threads", "threads", "get_thread", "create_thread", "thread_title"})
    assert ExportedRuntime is PkbRuntime


def test_layer_two_runs_no_supervision_loop_rt50() -> None:
    """No `/health`, no restart loop, no origin-channel tracking, no logging for a transport."""
    source = Path(runtime_module.__file__).read_text(encoding="utf-8")
    for banned in (
        "logging",
        "origin_channel",
        "/health",
        "while True:\n        await asyncio.sleep",
    ):
        assert banned not in source, banned


def test_an_unknown_agent_is_a_typed_404_rg13(kb: Path) -> None:
    """`run` resolves the graph before anything else, so a bad id never opens a stream."""

    async def go() -> None:
        async with opened(kb, scripted(says("hi"))) as rt:
            with pytest.raises(UnknownAgentError):
                await drain(rt, "topic/atlantis", "T1")

    asyncio.run(go())


# --------------------------------------------------------------------------------------
# RT-E · the write lock
# --------------------------------------------------------------------------------------


def test_the_write_lock_is_reentrant_for_one_owner_rt53() -> None:
    """`create_topic` takes it from inside a tool call while an outer flush may also want it."""
    lock = ReentrantWriteLock()
    with lock:
        assert lock.depth == 1
        with lock:
            assert lock.depth == 2
        assert lock.depth == 1
    assert lock.depth == 0
    assert lock.acquisitions == 1


def test_the_write_lock_excludes_two_owners_rt51() -> None:
    """One process-wide lock: the counter never shows two holders, from either world."""
    lock = ReentrantWriteLock()
    peak = 0
    order: list[str] = []

    async def hold(name: str) -> None:
        nonlocal peak
        async with lock:
            order.append(f"in-{name}")
            peak = max(peak, lock.depth)
            await asyncio.sleep(0.02)
            order.append(f"out-{name}")

    asyncio.run(_gather(hold("a"), hold("b")))
    assert peak == 1
    assert order == ["in-a", "out-a", "in-b", "out-b"]
    assert lock.acquisitions == 2
    assert lock.depth == 0


@pytest.mark.asyncio
async def test_the_lock_is_not_held_across_a_model_call_rt52(kb: Path) -> None:
    """Approvals sit pending for hours; a lock held across one freezes every other thread's flush."""
    held: list[int] = []

    def probe() -> Any:
        held.append(runtime_lock.depth)
        return says("done")

    model = scripted(writes(NOTE_PATH, VALID_NOTE, "w1"), probe)
    async with opened(kb, model) as rt:
        runtime_lock = rt.write_lock
        assert isinstance(runtime_lock, ReentrantWriteLock)
        await drain(rt, COOKING, "T1")
        assert held == [0]
        assert runtime_lock.depth == 0
        assert runtime_lock.acquisitions >= 1


@pytest.mark.asyncio
async def test_two_concurrent_runs_leave_the_derived_files_consistent_rt51(kb: Path) -> None:
    """Only the flush is serialized; the derived files must look like one full regeneration."""
    second = VALID_NOTE.replace("Reverse sear", "Pan sauce").replace("reverse", "pan")
    model = scripted(
        writes(NOTE_PATH, VALID_NOTE, "w1"),
        says("filed one"),
        writes("Cooking/notes/pan-sauce.md", second, "w2"),
        says("filed two"),
    )
    async with opened(kb, model) as rt:
        lock = rt.write_lock
        assert isinstance(lock, ReentrantWriteLock)
        await asyncio.gather(drain(rt, COOKING, "T-a"), drain(rt, COOKING, "T-b"))
        assert lock.depth == 0
        assert lock.acquisitions >= 2

    index = (kb / "Cooking" / "index.md").read_text(encoding="utf-8")
    assert "reverse-sear" in index
    assert "pan-sauce" in index
    # The strongest available consistency check: a full regeneration changes nothing.
    assert regenerate_all(kb).written == []


async def _hold_the_lock(lock: ReentrantWriteLock) -> None:
    """Take the lock and give it straight back — a waiter a test can cancel mid-acquire."""
    async with lock:
        pass


@pytest.mark.asyncio
async def test_a_cancelled_waiter_never_orphans_the_write_mutex_rt51() -> None:
    """RT-51: a cancelled waiter leaves the one process-wide lock exactly as it found it — free.

    `cancel(run_id)` (RT-46) and a consumer that stops iterating both cancel a task that may be
    parked in `__aenter__`, and cancelling the awaiting coroutine does **not** stop the worker
    thread doing the acquire: it goes on to take the mutex, `asyncio.futures._copy_future_state`
    drops the result because the destination future is already cancelled, and the lock is then held
    by nobody — `depth == 0`, `_owner is None`, and not even detectably locked. Every later flush,
    `create_topic` scaffold and `regenerate()` in the process blocks forever. That is a wedged
    daemon, not a degraded one, and no test in the suite cancelled a task parked in the acquire.

    The holder is *synchronous*, on another thread, on purpose: that is what parks the waiter in the
    off-loop acquire rather than on the per-loop gate, and it is exactly the shape of a `create_topic`
    scaffold taking the lock from inside a tool call (RT-53). The cancellation is followed
    immediately by the holder's release, so the acquire in flight is the one that wins the mutex
    after its waiter is already gone — the precise race that leaked.
    """
    lock = ReentrantWriteLock()
    holding = threading.Event()
    release = threading.Event()

    def sync_holder() -> None:
        with lock:
            holding.set()
            release.wait(5)

    thread = threading.Thread(target=sync_holder, daemon=True)
    thread.start()
    try:
        assert await asyncio.to_thread(holding.wait, 5)
        parked = asyncio.create_task(_hold_the_lock(lock))
        await asyncio.sleep(5 * _ACQUIRE_POLL_SECONDS)
        parked.cancel()
        release.set()  # the holder lets go while the cancelled attempt is still in flight
        with pytest.raises(asyncio.CancelledError):
            await parked
    finally:
        release.set()
        await asyncio.to_thread(thread.join, 5)

    await asyncio.sleep(5 * _ACQUIRE_POLL_SECONDS)
    assert lock.depth == 0
    assert lock._owner is None
    assert lock._mutex.acquire(blocking=False), "the cancelled waiter orphaned the write mutex"
    lock._mutex.release()
    await asyncio.wait_for(_hold_the_lock(lock), timeout=5)


@pytest.mark.asyncio
async def test_a_cancelled_waiter_leaves_the_next_task_free_to_acquire_rt51() -> None:
    """The same guarantee for the other place an async waiter waits: the per-loop gate (RT-51).

    Async waiters queue on an `asyncio.Lock` rather than in a worker thread (see the class
    docstring), so a cancellation now has two places to leak. A leaked gate is as fatal as a leaked
    mutex — every task on that loop stops at `__aenter__` — and it is a hazard the thread-only
    construction did not have, so it is pinned here rather than assumed.
    """
    lock = ReentrantWriteLock()
    second: asyncio.Task[None] | None = None
    async with lock:
        first = asyncio.create_task(_hold_the_lock(lock))
        await asyncio.sleep(0.05)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(_hold_the_lock(lock))
        await asyncio.sleep(0.05)
        assert not second.done(), "the lock is still held; nobody else may be inside it"

    await asyncio.wait_for(second, timeout=5)
    assert lock.depth == 0
    assert lock.acquisitions == 2
    assert lock._mutex.acquire(blocking=False), "the cancelled waiter wedged the gate"
    lock._mutex.release()


_STARVATION_PROBE: Final = """
import asyncio
from concurrent.futures import ThreadPoolExecutor

from pkb.agents.runtime import ReentrantWriteLock

WORKERS = 2
FLUSHES = 8


async def main() -> None:
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=WORKERS))
    lock = ReentrantWriteLock()

    async def flush() -> None:
        # Exactly the shape of KbMaintenanceMiddleware.aflush_turn: the critical section itself
        # needs a worker from the same pool every waiter is competing for.
        async with lock:
            await asyncio.to_thread(lambda: None)

    await asyncio.wait_for(asyncio.gather(*(flush() for _ in range(FLUSHES))), timeout=30)
    print("serialized")


asyncio.run(main())
"""


def test_concurrent_flushes_never_starve_the_lock_holder_rt51() -> None:
    """RT-51: concurrent flushes serialize; they must not deadlock the process.

    A waiter that parks a default-executor worker for the whole wait starves the holder, whose
    critical section needs a worker from that same pool: at `min(32, cpu_count + 4) + 1` concurrent
    flushes every worker sits on the mutex, the holder's `to_thread(flush, …)` never gets scheduled,
    and nothing ever releases the lock. RT-45 makes N concurrent runs on N threads supported load
    and RT-53 demands they complete "without deadlock", so this is a guarantee, not an abuse.

    In a subprocess with a deliberately tiny executor: core-count independent, and a regression
    fails here instead of wedging the pytest process — the blocked workers are non-daemon and
    `concurrent.futures.thread._python_exit` joins them at interpreter exit.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", _STARVATION_PROBE],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired:  # pragma: no cover - the regression path
        pytest.fail("8 concurrent flushes on a 2-worker executor deadlocked the process")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "serialized"


def test_the_runtime_constructs_no_resume_command_of_its_own_rt33() -> None:
    """The AI never resolves its own interrupt; `approval.py` is the one construction site.

    AST rather than the text grep the rule sketches, for the reason the registry's audits give: the
    phrase now appears legitimately in three docstrings that *explain* the rule, and a text grep
    would teach the next author to delete the explanation rather than the breach.
    """
    root = Path(runtime_module.__file__).parent
    offenders: list[str] = []
    for source in root.rglob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            keywords = {keyword.arg for keyword in node.keywords}
            if node.func.id == "Command" and "resume" in keywords:
                offenders.append(source.relative_to(root).as_posix())
    assert sorted(set(offenders)) == ["approval.py"]


async def _gather(*coros: Any) -> None:
    await asyncio.gather(*coros)


# --------------------------------------------------------------------------------------
# The flush guarantee (MW-26 … MW-28)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_run_that_raises_still_flushes_mw26(kb: Path) -> None:
    """`after_agent` never runs on a failure path — verified across four failure shapes (D-1)."""
    reports: list[Any] = []
    model = scripted(writes(NOTE_PATH, VALID_NOTE, "w1"), raises(RuntimeError("boom")))
    async with opened(kb, model, flush_sink=reports.append) as rt:
        events = await drain(rt, COOKING, "T1")
        assert len(kinds(events, RunError)) == 1

    assert (kb / NOTE_PATH).exists()
    assert "reverse-sear" in (kb / "Cooking" / "index.md").read_text(encoding="utf-8")
    # The startup regeneration is report 0; the failure-path flush is the one that stamped.
    assert any(NOTE_PATH in report.stamped for report in reports)


@pytest.mark.asyncio
async def test_the_recovered_paths_are_cleared_after_a_failure_mw27(kb: Path) -> None:
    """A recovered set that is not cleared is a set that gets flushed again.

    The observable is the checkpointed key itself, not a second flush, and deliberately so: on this
    pin `before_agent` *does* re-run for a fresh message on a crashed thread, so it is a second
    guard against the re-stamp. The key is a published contract read from outside the graph (MW-26),
    so leaving it populated after the paths have already been flushed is wrong in its own right —
    every later reader, including a Layer 3 recovery pass, would flush them a second time and stamp
    `updated` on a day nothing was touched.
    """
    today = [date(2026, 8, 6)]
    reports: list[Any] = []
    model = scripted(writes(NOTE_PATH, VALID_NOTE, "w1"), raises(RuntimeError("boom")))

    async with opened(kb, model, clock=lambda: today[0], flush_sink=reports.append) as rt:
        await drain(rt, COOKING, "T1")
        assert [report.stamped for report in reports if report.stamped] == [[NOTE_PATH]]
        state = await rt._registry.get(COOKING).aget_state(rt.thread_config("T1"))
        assert state.values["kb_touched"] == []

        model.script = [says("recovered")]
        model.idx = 0
        reports.clear()
        today[0] = date(2026, 8, 7)
        await drain(rt, COOKING, "T1", "carry on")

    assert [path for report in reports for path in report.stamped] == []
    assert "updated: 2026-08-06" in (kb / NOTE_PATH).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_an_interrupted_turn_flushes_nothing_and_the_resume_flushes_once_mw29(
    kb: Path,
) -> None:
    """The failure-path guard must not mistake a pending approval for a crash.

    Both end without `after_agent` and both reach the runtime with a populated touched set. Treating
    the approval as a crash is destructive twice over: `aupdate_state` writes a fresh checkpoint and
    the pending `__interrupt__` does not survive it, so the human's decision vanishes; and flushing
    without clearing would let the resumed turn re-stamp the pre-interrupt paths on whichever day
    the human happened to answer.
    """
    today = [date(2026, 8, 6)]
    reports: list[Any] = []
    model = scripted(
        writes(NOTE_PATH, VALID_NOTE, "w1"),
        writes(SUMMARY_PATH, NEW_SUMMARY, "w2"),
        says("done"),
    )

    async with opened(kb, model, clock=lambda: today[0], flush_sink=reports.append) as rt:
        events = await drain(rt, COOKING, "T1")
        assert len(kinds(events, InterruptEvent)) == 1
        assert [report for report in reports if report.stamped] == []  # zero flushes while parked

        pending = await rt.pending_approval(COOKING, "T1")
        assert pending is not None  # the interrupt survived the failure-path guard

        reports.clear()
        today[0] = date(2026, 8, 7)
        await drain_resume(rt, COOKING, "T1", [Decision(type="approve")])

    stamped = [path for report in reports for path in report.stamped]
    assert sorted(stamped) == sorted([NOTE_PATH, SUMMARY_PATH])
    assert stamped.count(NOTE_PATH) == 1


@pytest.mark.asyncio
async def test_the_terminal_sentinel_survives_a_raising_flush_rt43(kb: Path) -> None:
    """Every run terminates, even when the exit chain's own flush blows up (RT-43, MW-25).

    `_drive`'s `finally` flushes and *then* queues the sentinel the consumer is waiting on. With
    nothing between them, a raise in the flush — a `FlushSink` that is down (MW-24 makes one
    mandatory), a `sqlite3.OperationalError: database is locked` out of the scan queue — meant the
    sentinel never landed: `run()` never yielded a terminal event, never returned, and held its
    `(agent_id, thread_id)` slot for as long as the caller lived. A stream that never closes is the
    one failure a daemon cannot recover from without a restart.

    The flush is broken here at the runtime's own seam, deliberately: the sentinel must not depend
    on the flush path being total, however carefully the middleware beneath it is guarded.
    """

    async def explode(graph: Any, config: Any) -> None:
        msg = "the scan database is locked"
        raise RuntimeError(msg)

    async with opened(kb, scripted(says("hi"))) as rt:
        rt._flush_pending = explode  # type: ignore[method-assign]
        events: list[Any] = []

        async def consume() -> None:
            async for event in rt.run(COOKING, "T1", "go", run_id="R1"):
                events.append(event)

        with pytest.raises(RuntimeError, match="the scan database is locked"):
            await asyncio.wait_for(consume(), timeout=30)

        assert len(kinds(events, RunEnd)) == 1, "the run still terminates"
        assert rt._active == {}, "and it does not brick the thread on its way out"


@pytest.mark.asyncio
async def test_a_flush_sink_that_is_down_cannot_take_the_run_down_mw25(kb: Path) -> None:
    """MW-25: a failed flush is logged and reported, never re-raised into the message stream.

    The sink is the one collaborator MW-24 makes mandatory and the one most likely to be a daemon's
    event stream rather than a list. Raising out of it used to abort `after_agent` — turning a
    completed run into a `RunError` — and then abort `_drive`'s failure-path flush as well, hanging
    the run for good. The answer has already been produced by the time any of this runs; it must
    still be delivered.
    """
    seen: list[Any] = []

    def sink(report: Any) -> None:
        seen.append(report)
        if len(seen) > 1:  # let the startup regeneration through, then break for good
            msg = "the event stream is down"
            raise RuntimeError(msg)

    model = scripted(writes(NOTE_PATH, VALID_NOTE, "w1"), says("filed"), says("still here"))
    async with opened(kb, model, flush_sink=sink) as rt:
        events = await asyncio.wait_for(drain(rt, COOKING, "T1"), timeout=30)
        assert len(kinds(events, RunEnd)) == 1
        assert kinds(events, RunError) == []
        assert rt._active == {}
        assert len(seen) == 2, "the report was delivered; the sink is what failed"

        again = await asyncio.wait_for(drain(rt, COOKING, "T1", "carry on"), timeout=30)
        assert len(kinds(again, RunEnd)) == 1, "the thread is not wedged"

    assert (kb / NOTE_PATH).exists()
    assert "reverse-sear" in (kb / "Cooking" / "index.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_a_successful_run_flushes_exactly_once_mw28(kb: Path) -> None:
    """A double flush is harmless to the tree but would enqueue every conflict scan twice."""
    model = scripted(writes(NOTE_PATH, VALID_NOTE, "w1"), says("filed"))
    async with opened(kb, model) as rt:
        await drain(rt, COOKING, "T1")
        queued = await rt.queue.pending()

    assert [request.topic_id for request in queued] == [COOKING]
    assert queued[0].changed_paths == (NOTE_PATH,)


# --------------------------------------------------------------------------------------
# The gated scaffolding tools (LB-7, EX-12)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_librarian_carries_the_gated_create_topic_lb7(kb: Path) -> None:
    """Propose → interrupt → approve → six scaffold paths → the new id is routable with no restart."""
    model = scripted(
        calls(
            call(
                CREATE_TOPIC,
                {"name": "Physics", "title": "Physics", "description": "Mechanics and optics"},
                "t1",
            )
        ),
        says("created"),
    )
    async with opened(kb, model) as rt:
        assert [tool.name for tool in rt.tools_for(LIBRARIAN)] == [CREATE_TOPIC]

        events = await drain(rt, LIBRARIAN, "T1", "I have a physics paper")
        assert len(kinds(events, InterruptEvent)) == 1
        assert kinds(events, InterruptEvent)[0].request.actions[0].reason == "topic-creation"
        assert not (kb / "Physics").exists()

        await drain_resume(rt, LIBRARIAN, "T1", [Decision(type="approve")])
        assert (kb / "Physics" / "topic.md").exists()
        assert (kb / "Physics" / "notes" / "summary.md").exists()
        assert (kb / "Physics" / "references" / "summary.md").exists()
        assert "topic/physics" in {agent.agent_id for agent in rt.list_agents()}


@pytest.mark.asyncio
async def test_a_rejected_topic_creates_nothing_lb7(kb: Path) -> None:
    """The AI never resolves its own gate; a rejection leaves the tree byte-identical."""
    model = scripted(
        calls(call(CREATE_TOPIC, {"name": "Physics", "title": "P", "description": "d"}, "t1")),
        says("understood"),
    )
    async with opened(kb, model) as rt:
        await drain(rt, LIBRARIAN, "T1", "I have a physics paper")
        await drain_resume(rt, LIBRARIAN, "T1", [Decision(type="reject", message="not yet")])
    assert not (kb / "Physics").exists()


@pytest.mark.asyncio
async def test_an_expert_carries_a_scope_limited_create_subtopic_ex12(kb: Path) -> None:
    """Sub-topic creation belongs to the expert that owns the parent, and only inside its subtree."""
    async with opened(kb, scripted(says("hi"))) as rt:
        # `ingest_source` joined the expert's tools with large-source ingestion (LS-11): it is the
        # other per-topic tool, bound to this agent's own topic the same way `create_subtopic` is.
        assert [tool.name for tool in rt.tools_for(COOKING)] == [CREATE_SUBTOPIC, INGEST_TOOL]
        assert rt.tools_for("topic/nobody") == []

        env = TopicToolEnv(kb_root=kb, snapshot=rt.snapshot, clock=lambda: TODAY)
        subtopic = topic_tools(env, COOKING)[0]

        outside = subtopic.invoke(
            {"name": "Fuel", "title": "Fuel", "description": "d", "parent_topic_path": "BBQ"}
        )
        assert "outside" in outside
        assert not (kb / "BBQ" / "sub-topics").exists()

        inside = subtopic.invoke(
            {"name": "Braising", "title": "Braising", "description": "Low, slow"}
        )
        assert "Created the topic" in inside
        assert (kb / "Cooking" / "sub-topics" / "Braising" / "topic.md").exists()


@pytest.mark.asyncio
async def test_a_too_deep_subtopic_is_refused_not_crashed_ex12(kb: Path) -> None:
    """`TopicDepthExceededError` reaches the model as a refusal naming the limit (SC-9)."""
    async with opened(kb, scripted(says("hi"))) as rt:
        env = TopicToolEnv(kb_root=kb, snapshot=rt.snapshot, clock=lambda: TODAY)
        deep = topic_tools(env, GRILLING)[0]
        first = deep.invoke(
            {"name": "Charcoal", "title": "Charcoal", "description": "Lump vs brick"}
        )
        assert "Created the topic" in first

        rt.invalidate()
        env = TopicToolEnv(kb_root=kb, snapshot=rt.snapshot, clock=lambda: TODAY)
        deeper = topic_tools(env, "topic/cooking/grilling/charcoal")[0]
        refusal = deeper.invoke({"name": "Lump", "title": "Lump", "description": "Hardwood lump"})

    assert "Refused" in refusal
    assert "4 levels" in refusal
    assert not (kb / "Cooking/sub-topics/Grilling/sub-topics/Charcoal/sub-topics/Lump").exists()


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_invalidate_snapshot_drops_the_tree_and_keeps_the_graphs_rt25(kb: Path) -> None:
    """RT-25/RT-28: the gates need a tree that includes what this run has already written.

    `snapshot()` is scanned once and cached, and until now only a *flush* dropped it — and the flush
    is end-of-run. So the `recipes/` folder the previous write minted, or a `topic.*` tag the human
    approved one tool call ago, was still invisible when the next write was gated, and the same
    decision was demanded again: RT-28's "the second does not gate" and RT-25's "gates once", both
    broken for every file after the first in a single run.

    This is the seam that closes it, and it is deliberately narrower than `invalidate()`: dropping
    the compiled graphs too (RG-16) would recompile the Librarian and every expert once per written
    note, which is a far bigger hammer than the problem. `KbMaintenanceMiddleware` calls this after
    every successful knowledge-base mutation; see `PkbRuntime.snapshot` for the full contract.
    """
    async with opened(kb, scripted(says("hi"))) as rt:
        before = rt.snapshot()
        graph = rt._registry.get(COOKING)
        recipes = kb / "Cooking" / "recipes"
        recipes.mkdir()
        (recipes / "a.md").write_text(VALID_NOTE, encoding="utf-8")

        assert rt.snapshot() is before, "the cache is event-driven, never mtime-based"
        assert before.topics["Cooking"].extension_folders == ()

        rt.invalidate_snapshot()

        fresh = rt.snapshot()
        assert fresh is not before
        assert fresh.topics["Cooking"].extension_folders == ("recipes",)
        assert rt._registry.get(COOKING) is graph, "the compiled graphs are still correct"


@pytest.mark.asyncio
async def test_a_scaffold_invalidates_the_snapshot_and_the_registry_rg16(kb: Path) -> None:
    """A topic created mid-session must be listed *and* routable without a restart."""
    async with opened(kb, scripted(says("hi"))) as rt:
        before = rt.snapshot()
        assert before is rt.snapshot()

        env = TopicToolEnv(
            kb_root=kb, snapshot=rt.snapshot, lock=rt.write_lock, registry=rt, clock=lambda: TODAY
        )
        topic_tools(env, LIBRARIAN)[0].invoke(
            {"name": "Physics", "title": "Physics", "description": "Mechanics"}
        )

        assert rt.snapshot() is not before
        assert "Physics" in scan(kb).topics
        assert "topic/physics" in {agent.agent_id for agent in rt.list_agents()}
