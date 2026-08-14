"""The scan queue and the scan run — RT-54 … RT-59, PR-8, C12.

The queue is tested against a real SQLite file rather than a fake, because the two rules that matter
are both *storage* rules: coalescing across flushes (RT-56) and the columns the schema is forbidden
to have (RT-59). A dictionary would satisfy neither honestly.

`run_scan` is tested twice: once against a stub runner, where the interesting property is which
paths it reports as flagged, and once end to end through `PkbRuntime` with a scripted expert, where
the interesting property is that the run really happens on a reserved thread and leaves the human's
own thread untouched.

No API key, no network (SK-18).
"""

from __future__ import annotations

import ast
import sqlite3
from collections.abc import AsyncIterator, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from pkb.agents import scans
from pkb.agents.scans import (
    PENDING,
    RUNNING,
    SCAN_THREAD_PREFIX,
    SqliteScanQueue,
    on_demand_request,
    run_scan,
    scan_prompt,
    scan_thread_id,
)
from pkb.contracts import RunEnd, RunError, ScanQueue, ScanRequest, ToolStart
from pkb.core.maintenance import ON_DEMAND_ORIGIN
from pkb.core.scan import scan
from tests.agents.conftest import TODAY, call, calls, says, scripted
from tests.agents.test_runtime import COOKING, drain, opened

TOPIC_PATH = "Cooking"

FLAGGED_NOTE = """---
title: "Reverse sear"
description: "Low oven first, then a very hot pan, for a thick steak"
topic: "Cooking"
tags:
  - topic.cooking
  - type.note
  - status.conflict-review
created: 2026-08-01
updated: 2026-08-01
review_note: "The references summary says to sear first; this note says to sear last."
source_type: note
---

# Reverse sear

Low oven, then a hot pan.
"""


def request(
    topic_id: str = COOKING,
    *,
    paths: Sequence[str] = (),
    day: date = TODAY,
    origin: str = "maintenance",
) -> ScanRequest:
    return ScanRequest(
        topic_id=topic_id,
        topic_path=TOPIC_PATH,
        changed_paths=tuple(paths),
        origin=origin,
        requested_at=day,
    )


# --------------------------------------------------------------------------------------
# The queue
# --------------------------------------------------------------------------------------


def test_the_queue_satisfies_the_protocol_the_middleware_depends_on_rt54(tmp_path: Path) -> None:
    """The middleware takes a `ScanQueue`, never a database handle — that is what C18 buys."""
    queue: ScanQueue = SqliteScanQueue(tmp_path / "pkb.sqlite")
    assert isinstance(queue, SqliteScanQueue)


@pytest.mark.asyncio
async def test_a_burst_of_flushes_leaves_one_pending_row_rt56(tmp_path: Path) -> None:
    """Layer 1 coalesces within a flush; Layer 2 coalesces across them, or a chatty day queues N."""
    queue = SqliteScanQueue(tmp_path / "pkb.sqlite")
    await queue.setup()

    await queue.put([request(paths=["Cooking/notes/a.md"], day=date(2026, 8, 4))])
    await queue.put([request(paths=["Cooking/notes/b.md"], day=date(2026, 8, 5))])
    await queue.put([request(paths=["Cooking/notes/a.md"], day=date(2026, 8, 6))])

    pending = await queue.pending()
    assert len(pending) == 1
    assert pending[0].changed_paths == ("Cooking/notes/a.md", "Cooking/notes/b.md")
    assert pending[0].requested_at == date(2026, 8, 6)
    assert pending[0].topic_path == TOPIC_PATH


@pytest.mark.asyncio
async def test_taking_a_scan_claims_it_and_done_retires_it_rt54(tmp_path: Path) -> None:
    """Claiming is a state change, not a delete: a worker that dies leaves a visible running row."""
    db = tmp_path / "pkb.sqlite"
    queue = SqliteScanQueue(db)
    await queue.setup()
    await queue.put([request(paths=["Cooking/notes/a.md"]), request("topic/bbq", day=TODAY)])

    claimed = await queue.take(1)
    assert len(claimed) == 1
    assert len(await queue.pending()) == 1

    statuses = dict(sqlite3.connect(db).execute("SELECT topic_id, status FROM scan_queue"))
    assert statuses[claimed[0].topic_id] == RUNNING

    await queue.done(claimed[0].topic_id)
    remaining = dict(sqlite3.connect(db).execute("SELECT topic_id, status FROM scan_queue"))
    assert claimed[0].topic_id not in remaining
    assert set(remaining.values()) == {PENDING}


@pytest.mark.asyncio
async def test_a_change_arriving_mid_scan_queues_a_fresh_pass_rt56(tmp_path: Path) -> None:
    """A running scan cannot see a file written after it started, so folding it in would lose it."""
    queue = SqliteScanQueue(tmp_path / "pkb.sqlite")
    await queue.setup()
    await queue.put([request(paths=["Cooking/notes/a.md"])])
    await queue.take(1)

    await queue.put([request(paths=["Cooking/notes/b.md"])])
    pending = await queue.pending()
    assert [row.changed_paths for row in pending] == [("Cooking/notes/b.md",)]


def test_the_schema_keeps_no_record_that_a_conflict_occurred_rt59(tmp_path: Path) -> None:
    """No registry, in the tree *or* here; `last_reviewed` is the only permitted trace."""
    db = tmp_path / "pkb.sqlite"
    connection = sqlite3.connect(db)
    connection.execute(scans._SCHEMA)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(scan_queue)")}
    assert columns == {
        "topic_id",
        "status",
        "topic_path",
        "changed_paths",
        "origin",
        "requested_at",
    }
    forbidden = {"finding", "findings", "conflict_type", "confidence", "resolution", "loser"}
    assert not (columns & forbidden)


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_flush_enqueues_through_the_runtimes_own_queue_rt54(kb: Path) -> None:
    """End to end: the middleware's report reaches the SQLite queue the runtime owns.

    Not a conflict-flagged write — ``status.conflict-review`` is stripped outright rather than
    downgraded to ``status.draft``, because T-17 retires the whole ``status.*`` namespace and
    either value would now be UNKNOWN_TAG_NAMESPACE. This test is about the generic write-triggers-
    a-scan plumbing, not about conflict detection.

    Superseded (Phase 5 rebuilds this): T-41 deletes `pkb.core.maintenance.build_scan_requests` and
    its `MAINTENANCE_ORIGIN` — the "write triggers a scan" plumbing this test is about no longer
    exists in Layer 1 at all, so an ordinary write enqueues nothing and ``queued[0]`` no longer
    exists to assert against. `test_a_request_can_carry_an_empty_changed_set_ma12` in
    `tests/core/test_maintenance.py` covers what remains: `scan_request_for`, named by the caller.
    """
    note = FLAGGED_NOTE.replace("  - status.conflict-review\n", "").replace(
        '\nreview_note: "The references summary says to sear first; this note says to sear last."',
        "",
    )
    model = scripted(
        calls(
            call(
                "write_file",
                {"file_path": "/kb/Cooking/notes/reverse-sear.md", "content": note},
                "w1",
            )
        ),
        says("filed"),
    )
    async with opened(kb, model) as rt:
        await drain(rt, COOKING, "T1")
        queued = await rt.queue.pending()

    assert [row.topic_id for row in queued] == [COOKING]
    assert queued[0].origin == "maintenance"


class LockSpyQueue:
    """A `ScanQueue` that records the write lock's depth at the moment it is written to."""

    def __init__(self, lock: Any) -> None:
        self._lock = lock
        self.depths: list[int] = []
        self.requests: list[ScanRequest] = []

    async def put(self, requests: Sequence[ScanRequest]) -> None:
        self.depths.append(self._lock.depth)
        self.requests.extend(requests)

    async def take(self, limit: int = 1) -> Sequence[ScanRequest]:
        return []

    async def done(self, topic_id: str) -> None:
        return None


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_enqueue_happens_inside_the_flushs_critical_section_rt55(kb: Path) -> None:
    """A crash between the file writes and the enqueue loses the scan permanently.

    The next flush only ever sees *its own* turn's touched paths, so a request that never reached
    the queue is never re-derived — which is why the two must share one critical section rather than
    merely happening in the same method.

    Not a conflict-flagged write, for the same reason as RT-54's sibling test above: T-17 retires
    ``status.*`` outright, so the tag is stripped rather than downgraded.

    Superseded (Phase 5 rebuilds this): the same T-41 removal as RT-54's sibling test — an ordinary
    write no longer produces a `ScanRequest`, so `spy` never sees a `put` call and both `spy.depths`
    and `spy.requests` stay empty. The critical-section ordering this pins is still true of whatever
    a `scan_request_for`-based caller enqueues; it needs that caller to exist again to exercise it.
    """
    note = FLAGGED_NOTE.replace("  - status.conflict-review\n", "").replace(
        '\nreview_note: "The references summary says to sear first; this note says to sear last."',
        "",
    )
    model = scripted(
        calls(
            call(
                "write_file",
                {"file_path": "/kb/Cooking/notes/reverse-sear.md", "content": note},
                "w1",
            )
        ),
        says("filed"),
    )
    async with opened(kb, model) as rt:
        spy = LockSpyQueue(rt.write_lock)
        rt.scan_queue = spy  # read by the factory when the graph is first compiled, below
        await drain(rt, COOKING, "T1")

    assert spy.depths == [1]  # the lock was held for the whole flush-and-enqueue section
    assert [request.topic_id for request in spy.requests] == [COOKING]
    assert rt.write_lock.depth == 0


# --------------------------------------------------------------------------------------
# Building a request (RT-57)
# --------------------------------------------------------------------------------------


def test_an_on_demand_request_is_built_by_layer_one_rt57(kb: Path) -> None:
    """`ScanRequest.topic_id` is already an agent id (PA-10), so it resolves through the registry."""
    snapshot = scan(kb)
    built = on_demand_request(snapshot, TOPIC_PATH, today=TODAY)
    assert built.topic_id == COOKING
    assert built.topic_path == TOPIC_PATH
    assert built.changed_paths == ()
    assert built.origin == ON_DEMAND_ORIGIN
    assert built.requested_at == TODAY


def test_layer_two_never_hand_builds_a_scan_request_rt57() -> None:
    """A hand-built request would re-derive the agent id and drift from `agent_id_for` (RG-11).

    The one sanctioned construction site is `scans._row_to_request`, which *rehydrates* a request
    Layer 1 built and this module stored — it invents no field, and the alternative is storing a
    pickle.
    """
    root = Path(scans.__file__).parent
    offenders: list[str] = []
    for source in root.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name == "_row_to_request":
                continue
            for inner in ast.walk(node):
                is_call = isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                if is_call and inner.func.id == "ScanRequest":  # type: ignore[attr-defined]
                    offenders.append(f"{source.name}:{inner.lineno}")
    assert offenders == []


# --------------------------------------------------------------------------------------
# The scan prompt (PR-8)
# --------------------------------------------------------------------------------------


def test_the_scan_prompt_names_the_skill_the_topic_and_the_three_axes_pr8() -> None:
    """The procedure lives in the SKILL.md body (SK-8); the prompt only points at it."""
    rendered = scan_prompt(request(paths=["Cooking/notes/a.md"]))
    assert "conflict-detection" in rendered
    assert TOPIC_PATH in rendered
    assert "notes/summary.md" in rendered
    assert "references/summary.md" in rendered
    assert "notes against other notes" in rendered
    assert "answers no user question" in rendered
    assert "Cooking/notes/a.md" in rendered


def test_a_whole_topic_scan_names_no_changed_files_pr8() -> None:
    """An on-demand scan of an untouched topic must not read as a scan of nothing."""
    rendered = scan_prompt(request(origin=ON_DEMAND_ORIGIN))
    assert "changed since the last scan" not in rendered


def test_a_scan_thread_id_is_reserved_and_unique_rt58() -> None:
    """Reserved so Layer 3 can exclude it; unique because the checkpointer keys on it alone (D-6)."""
    first = scan_thread_id(COOKING)
    second = scan_thread_id(COOKING)
    assert first.startswith(SCAN_THREAD_PREFIX)
    assert COOKING in first
    assert first != second


# --------------------------------------------------------------------------------------
# The scan run (RT-58)
# --------------------------------------------------------------------------------------


class StubRunner:
    """A `ScanRunner` that replays a fixed event list — the tagged-path logic, without a graph."""

    def __init__(self, kb_root: Path, events: Sequence[Any]) -> None:
        self.kb_root = kb_root
        self._events = list(events)
        self.calls: list[tuple[str, str, str, str]] = []

    def snapshot(self) -> Any:
        return scan(self.kb_root)

    def run(
        self,
        agent_id: str,
        thread_id: str,
        message: str,
        *,
        approval_mode: str = "interactive",
    ) -> AsyncIterator[Any]:
        self.calls.append((agent_id, thread_id, message, approval_mode))

        async def stream() -> AsyncIterator[Any]:
            for event in self._events:
                yield event

        return stream()


@pytest.mark.asyncio
async def test_only_files_this_scan_wrote_and_actually_flagged_are_reported_rt58(kb: Path) -> None:
    """A refused write is not a flag, and an older scan's flag is not this scan's finding."""
    flagged = kb / "Cooking" / "notes" / "reverse-sear.md"
    flagged.write_text(FLAGGED_NOTE, encoding="utf-8")
    stale = kb / "Cooking" / "notes" / "older.md"
    stale.write_text(FLAGGED_NOTE.replace("Reverse sear", "Older"), encoding="utf-8")

    events = [
        ToolStart(run_id="r", agent_id=COOKING, tool="write_file", summary=str(flagged)),
        ToolStart(
            run_id="r",
            agent_id=COOKING,
            tool="write_file",
            summary="/kb/Cooking/notes/reverse-sear.md",
        ),
        ToolStart(
            run_id="r", agent_id=COOKING, tool="write_file", summary="/kb/Cooking/notes/plain.md"
        ),
        ToolStart(run_id="r", agent_id=COOKING, tool="read_file", summary="/kb/Cooking/topic.md"),
        RunEnd(run_id="r", final_text="One contradiction, flagged."),
    ]
    runner = StubRunner(kb, events)
    result = await run_scan(runner, request(paths=["Cooking/notes/reverse-sear.md"]))

    assert result.topic_id == COOKING
    assert result.thread_id.startswith(SCAN_THREAD_PREFIX)
    assert result.tagged_paths == ("Cooking/notes/reverse-sear.md",)
    assert result.summary == "One contradiction, flagged."
    assert runner.calls[0][0] == COOKING
    assert runner.calls[0][3] == "propose_only"


@pytest.mark.asyncio
async def test_a_scan_that_fails_reports_the_failure_rt58(kb: Path) -> None:
    """`ScanResult` has no error field, so a failed scan must not read as a clean one."""
    runner = StubRunner(kb, [RunError(run_id="r", message="overloaded", retryable=True)])
    result = await run_scan(runner, request())
    assert "did not complete" in result.summary
    assert result.tagged_paths == ()


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_scan_runs_on_its_own_thread_and_leaves_the_humans_alone_rt58(kb: Path) -> None:
    """The scan's context never enters a human conversation (Q9).

    Superseded by T-17: the write this test simulates carries ``status.conflict-review`` — the tag
    that makes a path "flagged" for the scan to find — and Layer 1 now refuses any ``status.*`` tag
    outright (UNKNOWN_TAG_NAMESPACE), so the write never lands and nothing is ever tagged. The
    conflict-review signal this test (and RT-58's `tagged_paths` mechanism generally) depends on
    needs a replacement that does not route through a retired tag namespace; no replacement exists
    yet, so this is deselected rather than patched into asserting something untrue.
    """
    model = scripted(
        says("hello"),
        calls(
            call(
                "write_file",
                {"file_path": "/kb/Cooking/notes/reverse-sear.md", "content": FLAGGED_NOTE},
                "w1",
            )
        ),
        says("Flagged one contradiction against the references summary."),
    )
    async with opened(kb, model) as rt:
        await drain(rt, COOKING, "T-human", "hi")
        before = await rt.history(COOKING, "T-human")

        result = await rt.request_scan(on_demand_request(rt.snapshot(), TOPIC_PATH, today=TODAY))

        assert result.thread_id.startswith(SCAN_THREAD_PREFIX)
        assert result.tagged_paths == ("Cooking/notes/reverse-sear.md",)
        assert "Flagged one contradiction" in result.summary
        assert await rt.history(COOKING, "T-human") == before
        assert await rt.history(COOKING, result.thread_id) != before

    assert "status.conflict-review" in (kb / "Cooking" / "notes" / "reverse-sear.md").read_text(
        encoding="utf-8"
    )
