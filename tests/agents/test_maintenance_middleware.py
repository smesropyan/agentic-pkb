"""The once-per-turn maintenance flush — MW-20 … MW-30.

Almost every test here compiles a real deep agent over a real knowledge base and asserts what is on
disk, in the checkpoint, or in the injected queue afterwards. That is deliberate: the rules this
module implements are all about *when* something runs, and the two facts that shape them —
``after_agent`` never fires when a run raises (D-1) and the touched paths survive in the checkpoint
anyway (MW-27) — are only observable through the harness. A unit test of ``flush_turn`` would pass
against a middleware that never gets called.

`pkb.core.flush` itself is Layer 1's, tested there. What is asserted here is the plumbing around it:
which paths reach it, how often, under which lock, and where its report goes.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import time
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from pkb.agents.middleware.maintenance import (
    NULL_WRITE_LOCK,
    KbMaintenanceMiddleware,
)
from pkb.agents.middleware.state import KB_TOUCHED, KbAgentState
from pkb.agents.paths import KB_MOUNT
from pkb.agents.permissions import kb_permissions
from pkb.contracts import ScanRequest
from pkb.core import FlushReport, flush, regenerate_all
from tests.agents.conftest import TODAY, call, calls, raises, says, scripted

TOMORROW = TODAY + timedelta(days=1)


# --------------------------------------------------------------------------------------
# Test doubles — the three collaborators the middleware takes as configuration (MW-4)
# --------------------------------------------------------------------------------------


class ListScanQueue:
    """The :class:`~pkb.contracts.ScanQueue` Protocol over a list (RT-54).

    RT-54's whole point is that the middleware depends on a Protocol rather than on SQLite, so a
    test can watch the enqueue without a database. Each ``put`` is kept as its own batch, because
    "one batch per flush" is what MW-28 is about.
    """

    def __init__(self) -> None:
        self.batches: list[list[ScanRequest]] = []

    async def put(self, requests: Sequence[ScanRequest]) -> None:
        self.batches.append(list(requests))

    async def take(self, limit: int = 1) -> Sequence[ScanRequest]:
        return [request for batch in self.batches for request in batch][:limit]

    async def done(self, topic_id: str) -> None:
        return None

    @property
    def requests(self) -> list[ScanRequest]:
        return [request for batch in self.batches for request in batch]


class RecordingLock:
    """A :class:`~pkb.agents.middleware.maintenance.KbWriteLock` that writes to a shared timeline.

    Both context-manager protocols are implemented because MW-2 makes the middleware run the same
    critical section from a synchronous and an asynchronous hook.
    """

    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline
        self.held = False
        self.max_depth = 0
        self._depth = 0

    def _acquire(self) -> None:
        self._depth += 1
        self.max_depth = max(self.max_depth, self._depth)
        self.held = True
        self.timeline.append("lock-acquired")

    def _release(self) -> None:
        self._depth -= 1
        self.held = self._depth > 0
        self.timeline.append("lock-released")

    def __enter__(self) -> RecordingLock:
        self._acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._release()

    async def __aenter__(self) -> RecordingLock:
        self._acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._release()


class SpyRegistry:
    """The slice of ``AgentRegistry`` MW-30 is allowed to touch."""

    def __init__(self) -> None:
        self.invalidations = 0

    def invalidate(self) -> None:
        self.invalidations += 1


class SpySnapshots:
    """The slice of ``PkbRuntime`` that owns the cached tree the gates read (RT-25, RT-28)."""

    def __init__(self) -> None:
        self.invalidations = 0

    def invalidate_snapshot(self) -> None:
        self.invalidations += 1


class ExplodingQueue(ListScanQueue):
    """A :class:`~pkb.contracts.ScanQueue` whose ``put`` fails the way SQLite actually fails."""

    async def put(self, requests: Sequence[ScanRequest]) -> None:
        msg = "database is locked"
        raise sqlite3.OperationalError(msg)


# --------------------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------------------


def note(
    title: str, description: str, *, body: str = "Body.\n", updated: str = "2026-08-01"
) -> str:
    """A note that passes Layer 1's schema, so the flush treats it as ordinary authored content.

    ``updated`` defaults to *before* the injected clock so that a stamp is visible: a file already
    carrying today's date renders identical bytes and is deliberately not rewritten (MA-3).
    """
    return (
        f"---\ntitle: {title}\ndescription: {description}\ntopic: Cooking\n"
        "tags:\n  - topic.cooking\n  - type.note\n  - status.draft\n"
        f"created: 2026-08-01\nupdated: {updated}\nsource_type: note\n---\n\n{body}"
    )


@pytest.fixture
def reports() -> list[FlushReport]:
    """The injected sink MW-24 requires the report to reach."""
    return []


@pytest.fixture
def queue() -> ListScanQueue:
    return ListScanQueue()


@pytest.fixture
def middleware(
    kb: Path, reports: list[FlushReport], queue: ListScanQueue
) -> KbMaintenanceMiddleware:
    """The middleware wired the way the runtime wires it, minus the real lock and registry."""
    return KbMaintenanceMiddleware(kb, queue=queue, sink=reports.append, clock=lambda: TODAY)


def build_agent(
    kb: Path,
    model: Any,
    middleware: KbMaintenanceMiddleware,
    **kwargs: Any,
) -> Any:
    """A deep agent over the knowledge base mount, with this middleware and nothing else."""
    return create_deep_agent(
        model=model,
        backend=CompositeBackend(
            default=StateBackend(),
            routes={KB_MOUNT: FilesystemBackend(root_dir=str(kb), virtual_mode=True)},
        ),
        middleware=[middleware],
        system_prompt="Do exactly what you are told.",
        **kwargs,
    )


def run(agent: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"messages": [HumanMessage("go")]}
    result: dict[str, Any] = agent.invoke(payload, config) if config else agent.invoke(payload)
    return result


def write_call(path: str, content: str, id_: str) -> dict[str, Any]:
    return call("write_file", {"file_path": path, "content": content}, id_)


def final_text(result: dict[str, Any]) -> str:
    messages: list[BaseMessage] = result["messages"]
    return str(messages[-1].content)


def tool_messages(result: dict[str, Any]) -> list[ToolMessage]:
    return [m for m in result["messages"] if isinstance(m, ToolMessage)]


def mtimes(kb: Path) -> dict[str, int]:
    return {p.relative_to(kb).as_posix(): p.stat().st_mtime_ns for p in sorted(kb.rglob("*.md"))}


@contextlib.contextmanager
def read_only(kb: Path) -> Any:
    """Make every directory in the tree unwritable, and put it back afterwards (MW-25)."""
    directories = [p for p in kb.rglob("*") if p.is_dir()] + [kb]
    for directory in directories:
        directory.chmod(0o555)
    try:
        yield
    finally:
        for directory in directories:
            directory.chmod(0o755)


# --------------------------------------------------------------------------------------
# MW-20 / MW-21 — one flush per turn, with KB-relative paths
# --------------------------------------------------------------------------------------


def test_one_flush_per_turn_carries_every_touched_path_mw20(
    kb: Path, middleware: KbMaintenanceMiddleware, reports: list[FlushReport]
) -> None:
    """Three writes in one turn produce exactly one flush, holding all three paths (MW-20)."""
    model = scripted(
        calls(
            write_call("/kb/Cooking/notes/searing.md", note("Searing", "Crust on a steak"), "c1"),
            write_call("/kb/Cooking/notes/resting.md", note("Resting", "Why meat rests"), "c2"),
            write_call(
                "/kb/Cooking/notes/brining.md", note("Brining", "Wet versus dry brine"), "c3"
            ),
        ),
        says("filed all three"),
    )

    run(build_agent(kb, model, middleware))

    assert len(reports) == 1, "regeneration is whole-tree; per-write flushing is forbidden"
    assert reports[0].stamped == [
        "Cooking/notes/brining.md",
        "Cooking/notes/resting.md",
        "Cooking/notes/searing.md",
    ]
    assert reports[0].written == ["Cooking/index.md"]


def test_the_paths_handed_to_the_flush_are_kb_relative_mw21(
    kb: Path, middleware: KbMaintenanceMiddleware, reports: list[FlushReport]
) -> None:
    """Layer 1 knows nothing about the mount, and gets the relative form (MW-21).

    The second half is the regression that makes the first half matter: handing ``flush`` the
    agent-visible ``/kb/...`` spelling is not an error, it is *silently useless* — no timestamp is
    bumped and no scan is queued.
    """
    model = scripted(
        calls(
            write_call("/kb/Cooking/notes/searing.md", note("Searing", "Crust on a steak"), "c1")
        ),
        says("filed"),
    )

    run(build_agent(kb, model, middleware))

    assert reports[0].stamped == ["Cooking/notes/searing.md"]

    mounted = flush(kb, ["/kb/Cooking/notes/searing.md"], today=TOMORROW)
    assert mounted.stamped == [], "the mounted spelling is dropped without an error"


def test_an_unnormalized_mount_spelling_is_still_recorded_mw21(
    kb: Path, middleware: KbMaintenanceMiddleware, reports: list[FlushReport]
) -> None:
    """``kb/...`` with no leading slash reaches the tree, so it must reach the flush too (D-3)."""
    model = scripted(
        calls(write_call("kb/Cooking/notes/searing.md", note("Searing", "Crust on a steak"), "c1")),
        says("filed"),
    )

    run(build_agent(kb, model, middleware))

    assert (kb / "Cooking" / "notes" / "searing.md").exists()
    assert reports[0].stamped == ["Cooking/notes/searing.md"]


# --------------------------------------------------------------------------------------
# MW-22 — the empty turn
# --------------------------------------------------------------------------------------


def test_a_turn_that_wrote_nothing_still_flushes_and_changes_nothing_mw22(
    kb: Path, middleware: KbMaintenanceMiddleware, reports: list[FlushReport]
) -> None:
    """The flush runs unconditionally, and on a clean tree that costs zero bytes (MW-22)."""
    before = mtimes(kb)

    run(build_agent(kb, scripted(says("nothing to file")), middleware))

    assert len(reports) == 1, "an empty touched set is still a flush"
    assert reports[0].written == []
    assert reports[0].stamped == []
    assert mtimes(kb) == before, "skip-if-identical regeneration must not touch mtimes"


def test_the_unconditional_flush_repairs_a_hand_edit_between_turns_mw22(
    kb: Path, middleware: KbMaintenanceMiddleware, reports: list[FlushReport]
) -> None:
    """Why the empty flush is not a wasted walk: the human edited the tree between turns."""
    (kb / "Cooking" / "notes" / "handwritten.md").write_text(
        note("Handwritten", "A note the human filed by hand between two turns")
    )

    run(build_agent(kb, scripted(says("nothing to file")), middleware))

    assert reports[0].written == ["Cooking/index.md"]
    assert "handwritten" in (kb / "Cooking" / "index.md").read_text()


# --------------------------------------------------------------------------------------
# MW-23 / RT-55 — the lock, and what is inside it
# --------------------------------------------------------------------------------------


def test_the_flush_and_the_enqueue_share_one_critical_section_mw23(
    kb: Path, reports: list[FlushReport], queue: ListScanQueue
) -> None:
    """The lock opens, the flush and the enqueue happen, the lock closes (MW-23, RT-55).

    The enqueue must be inside: a crash between the file writes and the queue write loses the scan
    permanently, because the next flush only ever sees its own turn's touched paths.
    """
    timeline: list[str] = []
    lock = RecordingLock(timeline)

    class TimedQueue(ListScanQueue):
        async def put(self, requests: Sequence[ScanRequest]) -> None:
            timeline.append(f"enqueue(held={lock.held})")
            await super().put(requests)

    timed = TimedQueue()

    def clock() -> date:
        timeline.append(f"flush(held={lock.held})")
        return TODAY

    def sink(report: FlushReport) -> None:
        timeline.append(f"sink(held={lock.held})")
        reports.append(report)

    middleware = KbMaintenanceMiddleware(kb, queue=timed, sink=sink, clock=clock, lock=lock)
    model = scripted(
        calls(
            write_call("/kb/Cooking/notes/searing.md", note("Searing", "Crust on a steak"), "c1")
        ),
        says("filed"),
    )

    run(build_agent(kb, model, middleware))

    assert timeline == [
        "lock-acquired",
        "flush(held=True)",
        "enqueue(held=True)",
        "lock-released",
        "sink(held=False)",
    ]
    assert lock.max_depth == 1


def test_the_lock_is_not_held_across_a_model_or_tool_call_rt52(
    kb: Path, reports: list[FlushReport]
) -> None:
    """Nothing but the flush runs under the lock — approvals sit pending for hours (RT-52)."""
    timeline: list[str] = []
    lock = RecordingLock(timeline)
    middleware = KbMaintenanceMiddleware(kb, sink=reports.append, clock=lambda: TODAY, lock=lock)

    class Watched(FilesystemBackend):
        def write(self, *args: Any, **kwargs: Any) -> Any:
            timeline.append(f"tool-write(held={lock.held})")
            return super().write(*args, **kwargs)

    agent = create_deep_agent(
        model=scripted(
            calls(
                write_call("/kb/Cooking/notes/searing.md", note("Searing", "Crust"), "c1"),
            ),
            says("filed"),
        ),
        backend=CompositeBackend(
            default=StateBackend(),
            routes={KB_MOUNT: Watched(root_dir=str(kb), virtual_mode=True)},
        ),
        middleware=[middleware],
        system_prompt="file it",
    )
    agent.invoke({"messages": [HumanMessage("go")]})

    assert "tool-write(held=False)" in timeline
    assert timeline.index("tool-write(held=False)") < timeline.index("lock-acquired")


# --------------------------------------------------------------------------------------
# MW-24 / MW-25 — the report is never discarded, and a failed flush never kills the run
# --------------------------------------------------------------------------------------


def test_the_report_reaches_the_sink_with_its_findings_mw24(
    kb: Path, middleware: KbMaintenanceMiddleware, reports: list[FlushReport]
) -> None:
    """A broken link is invisible outside the topic index unless the report is delivered (MW-24)."""
    (kb / "Cooking" / "notes" / "broken.md").write_text(
        note(
            "Broken",
            "A note pointing at a file that is not there",
            body="See [gone](missing.md).\n",
        )
    )

    run(build_agent(kb, scripted(says("done")), middleware))

    assert len(reports) == 1
    assert "BROKEN_LINK" in {finding.code for finding in reports[0].findings}


def test_a_read_only_knowledge_base_still_answers_the_question_mw25(
    kb: Path, middleware: KbMaintenanceMiddleware, reports: list[FlushReport]
) -> None:
    """A failed flush is reported, never re-raised into the agent's message stream (MW-25)."""
    (kb / "Cooking" / "notes" / "searing.md").write_text(note("Searing", "Crust on a steak"))

    with read_only(kb):
        result = run(build_agent(kb, scripted(says("Sear it hot and dry.")), middleware))

    assert final_text(result) == "Sear it hot and dry."
    assert "DERIVED_WRITE_FAILED" in {finding.code for finding in reports[0].findings}


def test_an_unexpected_flush_failure_is_reported_not_raised_mw25(
    kb: Path, reports: list[FlushReport]
) -> None:
    """Layer 1 does not raise for content defects; this covers everything nobody predicted (MW-25)."""

    def exploding_clock() -> date:
        msg = "the clock is on fire"
        raise RuntimeError(msg)

    middleware = KbMaintenanceMiddleware(kb, sink=reports.append, clock=exploding_clock)

    result = run(build_agent(kb, scripted(says("Sear it hot and dry.")), middleware))

    assert final_text(result) == "Sear it hot and dry."
    codes = {finding.code for finding in reports[0].findings}
    assert codes == {"DERIVED_WRITE_FAILED"}
    assert "the clock is on fire" in reports[0].findings[0].message


def test_a_queue_that_is_down_is_reported_not_raised_mw25(
    kb: Path, reports: list[FlushReport]
) -> None:
    """The enqueue is real I/O and it is inside the run's exit chain (MW-25, RT-54).

    ``sqlite3.OperationalError: database is locked`` out of ``ScanQueue.put`` used to escape
    ``after_agent``, which aborts the graph node: a completed run becomes a ``RunError``, and on the
    failure path the same raise escapes ``PkbRuntime._drive`` between its flush and the terminal
    sentinel, hanging the run for good. Both MW-25 tests that existed injected their failure inside
    ``_flush`` — the one call that was already guarded — so neither could see this.

    The scan is genuinely lost (the next flush only sees its own turn's paths), which is why it is
    reported as an error finding rather than swallowed.
    """
    middleware = KbMaintenanceMiddleware(
        kb, queue=ExplodingQueue(), sink=reports.append, clock=lambda: TODAY
    )
    model = scripted(
        calls(
            write_call("/kb/Cooking/notes/searing.md", note("Searing", "Crust on a steak"), "c1")
        ),
        says("Sear it hot and dry."),
    )

    result = run(build_agent(kb, model, middleware))

    assert final_text(result) == "Sear it hot and dry."
    assert reports[0].stamped == ["Cooking/notes/searing.md"], "the flush itself completed"
    assert "SCAN_ENQUEUE_FAILED" in {finding.code for finding in reports[0].findings}


@pytest.mark.asyncio
async def test_a_sink_that_is_down_still_refreshes_the_registry_mw25(
    kb: Path, queue: ListScanQueue
) -> None:
    """The report's two consumers are independent, and the async twin is the one that ships (MW-2).

    A daemon's event stream going down must not cost a rewritten ``expert.md`` its registry
    invalidation (MW-30) — the compiled graph would keep serving the old persona because a *log*
    failed. The sink's own failure has nowhere left to be reported, so it is swallowed there and
    nowhere else.
    """
    registry = SpyRegistry()

    def sink(report: FlushReport) -> None:
        msg = "the event stream is down"
        raise RuntimeError(msg)

    middleware = KbMaintenanceMiddleware(
        kb, queue=queue, sink=sink, clock=lambda: TODAY, registry=registry
    )
    model = scripted(
        calls(write_call("/kb/Cooking/expert.md", "You are a fussy cook.\n", "c1")),
        says("persona updated"),
    )

    result = await build_agent(kb, model, middleware).ainvoke({"messages": [HumanMessage("go")]})

    assert final_text(result) == "persona updated"
    assert registry.invalidations == 1


# --------------------------------------------------------------------------------------
# RT-25 / RT-28 — the cached tree the gates read
# --------------------------------------------------------------------------------------


def test_a_successful_write_drops_the_cached_tree_rt28(
    kb: Path, reports: list[FlushReport]
) -> None:
    """The gate predicate reads a tree the runtime scanned once and cached (RT-25, RT-28).

    Until the flush at the end of the run, that cache does not contain the file the last tool call
    wrote — so the first note into a new ``recipes/`` folder gates, and so does the second, and the
    human is asked again for a decision they gave one tool call earlier. This hook is the innermost
    ``wrap_tool_call`` in the stack (EX-14), so the refresh lands after the backend write and
    strictly before the next ``after_model`` gate evaluation.
    """
    snapshots = SpySnapshots()
    middleware = KbMaintenanceMiddleware(
        kb, sink=reports.append, clock=lambda: TODAY, snapshots=snapshots
    )
    model = scripted(
        calls(
            write_call("/kb/Cooking/recipes/a.md", note("A", "First recipe"), "c1"),
            write_call("/kb/Cooking/recipes/b.md", note("B", "Second recipe"), "c2"),
        ),
        says("filed both"),
    )

    run(build_agent(kb, model, middleware))

    assert snapshots.invalidations == 2, "one per landed write, before the next gate is evaluated"


@pytest.mark.asyncio
async def test_the_async_hook_drops_the_cached_tree_too_rt28(
    kb: Path, reports: list[FlushReport]
) -> None:
    """`awrap_tool_call` is the variant the daemon runs, so it carries the same refresh (MW-2)."""
    snapshots = SpySnapshots()
    middleware = KbMaintenanceMiddleware(
        kb, sink=reports.append, clock=lambda: TODAY, snapshots=snapshots
    )
    model = scripted(
        calls(write_call("/kb/Cooking/recipes/a.md", note("A", "First recipe"), "c1")),
        says("filed"),
    )

    await build_agent(kb, model, middleware).ainvoke({"messages": [HumanMessage("go")]})

    assert snapshots.invalidations == 1


def test_a_write_that_never_landed_leaves_the_cached_tree_alone_rt28(
    kb: Path, reports: list[FlushReport]
) -> None:
    """A denial changed nothing, so re-scanning would be pure cost (RT-28, MW-17).

    Same identity test as the touched-path record: a refusal comes back as the handler's own error
    ``ToolMessage``, a landed mutation comes back re-wrapped. The Librarian's rule set denies every
    KB write (RT-16), which is the cheapest way to produce a real denial rather than a simulated one.
    """
    snapshots = SpySnapshots()
    middleware = KbMaintenanceMiddleware(
        kb, sink=reports.append, clock=lambda: TODAY, snapshots=snapshots
    )
    model = scripted(
        calls(write_call("/kb/Cooking/recipes/a.md", note("A", "First recipe"), "c1")),
        says("could not file it"),
    )
    agent = create_deep_agent(
        model=model,
        backend=CompositeBackend(
            default=StateBackend(),
            routes={KB_MOUNT: FilesystemBackend(root_dir=str(kb), virtual_mode=True)},
        ),
        middleware=[middleware],
        permissions=kb_permissions(None),
        system_prompt="file it",
    )

    result = agent.invoke({"messages": [HumanMessage("go")]})

    assert tool_messages(result)[0].status == "error"
    assert snapshots.invalidations == 0


def test_a_scratch_write_leaves_the_cached_tree_alone_rt28(
    kb: Path, reports: list[FlushReport]
) -> None:
    """Paths on the default ``StateBackend`` route are not the tree at all (MW-8)."""
    snapshots = SpySnapshots()
    middleware = KbMaintenanceMiddleware(
        kb, sink=reports.append, clock=lambda: TODAY, snapshots=snapshots
    )
    model = scripted(
        calls(write_call("/scratch/plan.md", "just thinking out loud\n", "c1")),
        says("thought about it"),
    )

    run(build_agent(kb, model, middleware))

    assert snapshots.invalidations == 0


# --------------------------------------------------------------------------------------
# MW-26 / MW-27 — the failure path
# --------------------------------------------------------------------------------------


def test_a_run_that_dies_after_a_write_still_gets_its_flush_mw26(
    kb: Path, middleware: KbMaintenanceMiddleware, reports: list[FlushReport]
) -> None:
    """The honest proof: the model raises after a write, and the tree still ends up consistent.

    ``after_agent`` fires in *neither* half of this test — that is D-1, and it is why the runtime
    owns a ``try/finally`` at all (MW-26). What the middleware guarantees is that the paths are
    recoverable and that one call puts the tree right.
    """
    model = scripted(
        calls(
            write_call("/kb/Cooking/notes/searing.md", note("Searing", "Crust on a steak"), "c1")
        ),
        raises(RuntimeError("the provider fell over")),
    )
    agent = build_agent(kb, model, middleware, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t-fail"}}

    with pytest.raises(RuntimeError, match="the provider fell over"):
        run(agent, config)

    assert reports == [], "after_agent is a node on the normal exit edge; it never ran (D-1)"
    assert (kb / "Cooking" / "notes" / "searing.md").exists()
    assert "searing" not in (kb / "Cooking" / "index.md").read_text()

    # What `pkb.agents.runtime` does in its `finally`.
    touched = agent.get_state(config).values.get(KB_TOUCHED, ())
    assert middleware.flush_pending(touched) is not None

    assert "searing" in (kb / "Cooking" / "index.md").read_text()
    assert regenerate_all(kb).written == [], "the tree matches a full regeneration"


def test_the_touched_paths_are_recoverable_from_the_checkpoint_mw27(
    kb: Path, middleware: KbMaintenanceMiddleware
) -> None:
    """The tools node commits its update before the model node raises (MW-27).

    The recovery source is the checkpoint, not an in-memory side channel: the daemon may be a
    different process by the time anything looks.
    """
    model = scripted(
        calls(
            write_call("/kb/Cooking/notes/searing.md", note("Searing", "Crust on a steak"), "c1")
        ),
        raises(RuntimeError("boom")),
    )
    agent = build_agent(kb, model, middleware, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t-recover"}}

    with pytest.raises(RuntimeError):
        run(agent, config)

    state = agent.get_state(config)
    assert state.values.get(KB_TOUCHED) == ["Cooking/notes/searing.md"]
    assert state.next == ("model",), "the thread is still resumable"


def test_the_recovered_paths_must_be_cleared_or_they_restamp_mw27(
    kb: Path, middleware: KbMaintenanceMiddleware
) -> None:
    """Why MW-27 makes the runtime clear the key after its flush.

    Re-flushing the same paths on a later day bumps ``updated`` a second time on files that day did
    not touch. The middleware cannot clear a checkpoint it does not own, so this pins the
    consequence the runtime is responsible for.
    """
    (kb / "Cooking" / "notes" / "searing.md").write_text(note("Searing", "Crust on a steak"))
    recovered = ["Cooking/notes/searing.md"]

    first = middleware.flush_pending(recovered)
    assert first is not None
    assert first.stamped == recovered

    later = KbMaintenanceMiddleware(kb, clock=lambda: TOMORROW)
    assert later.flush_pending(()) is None, "a cleared key is a no-op (MW-28)"
    replayed = later.flush_pending(recovered)
    assert replayed is not None
    assert replayed.stamped == recovered, "an uncleared key restamps across a date boundary"


# --------------------------------------------------------------------------------------
# MW-28 — the two paths never both flush
# --------------------------------------------------------------------------------------


def test_a_successful_run_enqueues_one_scan_per_topic_mw28(
    kb: Path, middleware: KbMaintenanceMiddleware, queue: ListScanQueue
) -> None:
    """Two notes in one topic, one turn: one request, and the failure path adds none (MW-28)."""
    model = scripted(
        calls(
            write_call("/kb/Cooking/notes/searing.md", note("Searing", "Crust on a steak"), "c1"),
            write_call("/kb/Cooking/notes/resting.md", note("Resting", "Why meat rests"), "c2"),
        ),
        says("filed both"),
    )
    agent = build_agent(kb, model, middleware, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t-ok"}}

    run(agent, config)

    assert [request.topic_id for request in queue.requests] == ["topic/cooking"]

    touched = agent.get_state(config).values.get(KB_TOUCHED, ())
    assert touched == [], "after_agent clears the key — that emptiness is the sentinel"
    assert middleware.flush_pending(touched) is None
    assert len(queue.requests) == 1, "the runtime's guard must not double-enqueue"


def test_flush_pending_refuses_an_empty_set_mw28(
    kb: Path, middleware: KbMaintenanceMiddleware, reports: list[FlushReport], queue: ListScanQueue
) -> None:
    """The guard is mechanical, not a heuristic: nothing at all happens (MW-28)."""
    assert middleware.flush_pending([]) is None
    assert reports == []
    assert queue.batches == []


@pytest.mark.asyncio
async def test_aflush_pending_refuses_an_empty_set_mw28(
    kb: Path, middleware: KbMaintenanceMiddleware, reports: list[FlushReport]
) -> None:
    """The async twin the daemon actually calls behaves identically (MW-2, MW-28)."""
    assert await middleware.aflush_pending(()) is None
    assert reports == []


# --------------------------------------------------------------------------------------
# MW-29 — the interrupted turn
# --------------------------------------------------------------------------------------


def test_an_interrupted_turn_flushes_once_on_resume_mw29(
    kb: Path, middleware: KbMaintenanceMiddleware, reports: list[FlushReport]
) -> None:
    """Zero flushes while the human thinks, exactly one when they answer (MW-29)."""
    model = scripted(
        calls(
            write_call("/kb/Cooking/notes/searing.md", note("Searing", "Crust on a steak"), "c1")
        ),
        says("filed"),
    )
    agent = build_agent(
        kb,
        model,
        middleware,
        checkpointer=InMemorySaver(),
        interrupt_on={"write_file": True},
    )
    config = {"configurable": {"thread_id": "t-hitl"}}

    run(agent, config)
    assert agent.get_state(config).interrupts, "the turn is parked on a decision"
    assert reports == [], "an unresolved approval leaves the tree unflushed (RT-7 closes this)"

    agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config)

    assert len(reports) == 1
    assert reports[0].stamped == ["Cooking/notes/searing.md"]


# --------------------------------------------------------------------------------------
# MW-17 / MW-19 — what is recorded, and what is not
# --------------------------------------------------------------------------------------


def test_a_denied_write_records_nothing_mw17(
    kb: Path, reports: list[FlushReport], queue: ListScanQueue
) -> None:
    """A permission denial must not bump anything's ``updated`` (MW-17).

    The Librarian's rule set is the harshest available and denies every KB write (RT-16), which is
    the cheapest way to produce a real denial rather than a simulated one.
    """
    middleware = KbMaintenanceMiddleware(kb, queue=queue, sink=reports.append, clock=lambda: TODAY)
    model = scripted(
        calls(write_call("/kb/Cooking/notes/searing.md", note("Searing", "Crust"), "c1")),
        says("could not file it"),
    )
    agent = create_deep_agent(
        model=model,
        backend=CompositeBackend(
            default=StateBackend(),
            routes={KB_MOUNT: FilesystemBackend(root_dir=str(kb), virtual_mode=True)},
        ),
        middleware=[middleware],
        permissions=kb_permissions(None),
        system_prompt="file it",
    )

    result = agent.invoke({"messages": [HumanMessage("go")]})

    assert tool_messages(result)[0].status == "error"
    assert not (kb / "Cooking" / "notes" / "searing.md").exists()
    assert reports[0].stamped == []
    assert queue.requests == []


def test_a_scratch_write_is_not_a_knowledge_base_write_mw17(
    kb: Path, middleware: KbMaintenanceMiddleware, reports: list[FlushReport]
) -> None:
    """Paths on the default ``StateBackend`` route are thread-scoped scratch, not the tree (MW-8)."""
    model = scripted(
        calls(write_call("/scratch/plan.md", "just thinking out loud\n", "c1")),
        says("thought about it"),
    )

    run(build_agent(kb, model, middleware))

    assert reports[0].stamped == []
    assert reports[0].written == []


def test_a_successful_delete_is_recorded_and_queues_no_scan_mw19(
    kb: Path, middleware: KbMaintenanceMiddleware, reports: list[FlushReport], queue: ListScanQueue
) -> None:
    """A removed note must leave the topic index too — but a deletion is not a conflict (MW-19)."""
    (kb / "Cooking" / "notes" / "searing.md").write_text(note("Searing", "Crust on a steak"))
    flush(kb, [], today=TODAY)
    assert "searing" in (kb / "Cooking" / "index.md").read_text()

    model = scripted(
        calls(call("delete", {"file_path": "/kb/Cooking/notes/searing.md"}, "d1")),
        says("removed"),
    )
    run(build_agent(kb, model, middleware))

    assert "searing" not in (kb / "Cooking" / "index.md").read_text()
    assert reports[0].written == ["Cooking/index.md"]
    assert queue.requests == [], "MA-12's triggers are creates and modifies, not deletions"


# --------------------------------------------------------------------------------------
# MW-30 — registry invalidation
# --------------------------------------------------------------------------------------


def test_writing_an_expert_persona_invalidates_the_registry_mw30(
    kb: Path, reports: list[FlushReport]
) -> None:
    """``expert.md`` is a compiled graph's prompt source, so the cached graph is stale (MW-30)."""
    registry = SpyRegistry()
    middleware = KbMaintenanceMiddleware(
        kb, sink=reports.append, clock=lambda: TODAY, registry=registry
    )
    model = scripted(
        calls(write_call("/kb/Cooking/expert.md", "You are a fussy cook.\n", "c1")),
        says("persona updated"),
    )

    run(build_agent(kb, model, middleware))

    assert registry.invalidations == 1


def test_filing_an_ordinary_note_does_not_invalidate_the_registry_mw30(
    kb: Path, reports: list[FlushReport]
) -> None:
    """Everything else leaves every compiled graph correct (MW-30)."""
    registry = SpyRegistry()
    middleware = KbMaintenanceMiddleware(
        kb, sink=reports.append, clock=lambda: TODAY, registry=registry
    )
    model = scripted(
        calls(write_call("/kb/Cooking/notes/searing.md", note("Searing", "Crust"), "c1")),
        says("filed"),
    )

    run(build_agent(kb, model, middleware))

    assert registry.invalidations == 0


# --------------------------------------------------------------------------------------
# MW-1 … MW-5 — shape, both hook variants, and no per-run state on `self`
# --------------------------------------------------------------------------------------


def test_the_middleware_appears_once_and_leaves_the_filesystem_stack_alone_mw1(
    kb: Path, middleware: KbMaintenanceMiddleware
) -> None:
    """A custom name that collided with a core stack member would replace it (MW-1, EX-15)."""
    assert middleware.name == "KbMaintenanceMiddleware"

    agent = build_agent(kb, scripted(says("hi")), middleware)
    owned = [name for name in agent.nodes if name.startswith(middleware.name)]

    assert sorted(owned) == [
        f"{middleware.name}.after_agent",
        f"{middleware.name}.before_agent",
    ]
    assert "tools" in agent.nodes, "FilesystemMiddleware's tool node survives"


def test_the_middleware_declares_the_shared_state_schema_mw5(
    middleware: KbMaintenanceMiddleware,
) -> None:
    """Declaring it nowhere loses both keys; langchain merges every middleware's schema (MW-5)."""
    assert middleware.state_schema is KbAgentState


@pytest.mark.asyncio
async def test_both_hook_variants_flush_the_same_way_mw2(
    kb: Path, middleware: KbMaintenanceMiddleware, reports: list[FlushReport]
) -> None:
    """One compiled graph, driven synchronously and asynchronously — both flush (MW-2)."""
    sync_model = scripted(
        calls(write_call("/kb/Cooking/notes/searing.md", note("Searing", "Crust"), "c1")),
        says("filed"),
    )
    build_agent(kb, sync_model, middleware).invoke({"messages": [HumanMessage("go")]})

    async_model = scripted(
        calls(write_call("/kb/Cooking/notes/resting.md", note("Resting", "Why meat rests"), "c2")),
        says("filed"),
    )
    await build_agent(kb, async_model, middleware).ainvoke({"messages": [HumanMessage("go")]})

    assert [report.stamped for report in reports] == [
        ["Cooking/notes/searing.md"],
        ["Cooking/notes/resting.md"],
    ]


@pytest.mark.asyncio
async def test_the_async_flush_does_not_block_the_event_loop_mw3(kb: Path) -> None:
    """The tree walk runs on a worker thread; the sync twin, by contrast, stops the world (MW-3)."""
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.001)
            ticks += 1

    def slow_clock() -> date:
        time.sleep(0.15)
        return TODAY

    middleware = KbMaintenanceMiddleware(kb, clock=slow_clock, lock=NULL_WRITE_LOCK)
    task = asyncio.create_task(ticker())
    await asyncio.sleep(0.01)

    before_sync = ticks
    middleware.flush_turn([])
    assert ticks == before_sync, "the synchronous hook holds the loop for the whole walk"

    before_async = ticks
    await middleware.aflush_turn([])
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert ticks > before_async, "the asynchronous hook lets the loop keep running"


def test_the_middleware_keeps_no_per_run_state_mw4(
    kb: Path, middleware: KbMaintenanceMiddleware
) -> None:
    """One instance serves every run of a compiled graph, so per-run state on `self` leaks (MW-4)."""
    before = dict(vars(middleware))

    model = scripted(
        calls(write_call("/kb/Cooking/notes/searing.md", note("Searing", "Crust"), "c1")),
        says("filed"),
    )
    agent = build_agent(kb, model, middleware)
    agent.invoke({"messages": [HumanMessage("go")]})

    after = dict(vars(middleware))
    assert after.keys() == before.keys()
    assert all(after[key] is before[key] for key in before)


def test_the_touched_set_does_not_survive_into_the_next_turn_mw6(
    kb: Path, middleware: KbMaintenanceMiddleware, reports: list[FlushReport]
) -> None:
    """State is checkpointed; without the ``before_agent`` reset turn 2 re-flushes turn 1 (MW-6)."""
    agent = build_agent(
        kb,
        scripted(
            calls(write_call("/kb/Cooking/notes/searing.md", note("Searing", "Crust"), "c1")),
            says("filed"),
            says("nothing more to file"),
        ),
        middleware,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "t-two-turns"}}

    run(agent, config)
    agent.invoke({"messages": [HumanMessage("anything else?")]}, config)

    assert len(reports) == 2
    assert reports[1].stamped == [], "turn 2 must not re-stamp turn 1's file"
