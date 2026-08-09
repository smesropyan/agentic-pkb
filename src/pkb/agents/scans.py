"""The conflict-scan queue and the scan run — RT-54 … RT-59, PR-8, C12.

Layer 1 decides *that* a topic needs a conflict scan and returns
:class:`~pkb.core.models.ScanRequest` values as pure data; it opens no database (MA-11, C18). This
module is the other half: a durable queue behind the :class:`~pkb.contracts.ScanQueue` Protocol, and
the run that actually performs a scan.

**Why the run lives here and not in the daemon (C12).** Arch §7 puts the dequeue loop "in the
daemon", but the thing it runs is a deepagents graph and I2 forbids a transport from importing the
harness at all. So the split is: Layer 3 owns the timer and the dequeue loop, calling through
``PkbService``; :func:`run_scan` owns the graph run.

**Why the queue is stdlib ``sqlite3`` on a worker thread and not ``aiosqlite``.** An ``aiosqlite``
connection binds itself to the event loop that created it, and this queue is written from
``KbMaintenanceMiddleware``'s *synchronous* ``after_agent`` as well as its async twin (MW-2) — the
sync path drives the coroutine on a private loop in a worker thread, which would poison a
loop-affine connection. A short-lived synchronous connection inside :func:`asyncio.to_thread` has no
affinity at all and needs no extra dependency. The file is the runtime's, opened WAL, so it shares
the checkpointer's database without sharing its connection (RT-4).

**What the schema deliberately does not carry (RT-59).** No finding text, no conflict type, no
confidence, no resolution, no loser marker. README §1.7 is explicit that the note content is the
true state of knowledge and that no conflict registry exists — in the tree *or* here. The only trace
a resolved conflict leaves anywhere is ``last_reviewed``.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import date
from typing import TYPE_CHECKING, Any, Final, Protocol
from uuid import uuid4

from pkb.agents.gates import CONFLICT_TAG
from pkb.agents.paths import to_kb_relative
from pkb.contracts import (
    SCAN_THREAD_PREFIX,
    AgentEvent,
    ApprovalMode,
    RunEnd,
    RunError,
    ScanRequest,
    ScanResult,
    ToolStart,
)
from pkb.core.maintenance import ON_DEMAND_ORIGIN, scan_request_for
from pkb.core.tags import files_with_tag

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from pkb.core.models import KbSnapshot

__all__ = [
    "PENDING",
    "RUNNING",
    "SCAN_THREAD_PREFIX",
    "ScanRunner",
    "SqliteScanQueue",
    "on_demand_request",
    "run_scan",
    "scan_prompt",
    "scan_thread_id",
]

# `SCAN_THREAD_PREFIX` is **re-exported from `pkb.contracts`** (C-1): Layer 3 must recognise a scan
# thread to keep it out of every user-facing list (RT-58, SV-13) and cannot import this module.


PENDING: Final = "pending"
RUNNING: Final = "running"

_WRITE_TOOLS: Final = frozenset({"write_file", "edit_file"})
"""The tools a scan uses to flag a finding. Tagging goes through the ordinary write path (MW-31)."""

_TABLE = "scan_queue"

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    topic_id      TEXT NOT NULL,
    status        TEXT NOT NULL,
    topic_path    TEXT NOT NULL,
    changed_paths TEXT NOT NULL,
    origin        TEXT NOT NULL,
    requested_at  TEXT NOT NULL,
    PRIMARY KEY (topic_id, status)
)
"""
"""One row per topic per status — which is what makes RT-56's coalescing a schema property.

A burst of turns touching one topic upserts onto the same ``(topic_id, 'pending')`` row with the
union of the changed paths, so the queue never holds N identical whole-topic scans. A scan already
``running`` occupies a different row, so a change arriving mid-scan still queues a fresh pass rather
than being folded into the one that is about to finish and can no longer see it.
"""


# --------------------------------------------------------------------------------------
# The queue (RT-54, RT-56, RT-59)
# --------------------------------------------------------------------------------------


class SqliteScanQueue:
    """The default :class:`~pkb.contracts.ScanQueue`, over the runtime's SQLite file (RT-54).

    The middleware depends on the Protocol, never on this class, which is what lets a unit test pass
    a list and touch no database. This implementation exists because a crash between the file writes
    and the enqueue loses the scan *permanently*: the next flush only ever sees its own turn's
    touched paths, so an unqueued conflict scan is never re-derived (RT-55).
    """

    def __init__(self, db_path: Path) -> None:
        """Args:
        db_path: The runtime's SQLite file (RT-4). Shared with the checkpointer, on its own
            connection — the saver's ``aiosqlite`` connection is never handed out.
        """
        self.db_path = db_path
        self._lock = threading.Lock()

    async def setup(self) -> None:
        """Create the table. Idempotent, so a restart over an existing file is free."""
        await asyncio.to_thread(self._setup)

    async def put(self, requests: Sequence[ScanRequest]) -> None:
        """Persist a flush's requests, coalescing onto the pending row per topic (RT-56).

        Layer 1 has already coalesced *within* the flush (MA-12, one request per topic). This
        coalesces *across* flushes: the merged row carries the union of the changed paths and the
        latest ``requested_at``, so twenty turns in one topic leave one scan to run, over everything
        those turns touched.
        """
        if requests:
            await asyncio.to_thread(self._put, list(requests))

    async def take(self, limit: int = 1) -> Sequence[ScanRequest]:
        """Claim up to *limit* pending scans, oldest first, marking them running.

        Claiming is a state change rather than a delete so that a worker crashing mid-scan leaves a
        visible ``running`` row instead of silently losing the request.
        """
        return await asyncio.to_thread(self._take, limit)

    async def done(self, topic_id: str) -> None:
        """Retire the running scan for *topic_id*. A no-op if there is none."""
        await asyncio.to_thread(self._done, topic_id)

    async def pending(self) -> Sequence[ScanRequest]:
        """Every queued-but-unclaimed request, oldest first. Read-only; nothing is claimed."""
        return await asyncio.to_thread(self._pending)

    # -- synchronous halves, always run on a worker thread ------------------------------

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """One short-lived connection, always closed.

        ``closing`` and not a bare ``with sqlite3.connect(...)``: the connection's own context
        manager commits or rolls back a transaction and leaves the handle **open**, which over a
        long-running daemon is one leaked file descriptor per flush. The table is created on every
        call rather than once at startup so that a queue used without :meth:`setup` — a test, a
        Layer 3 worker that only drains — still works.
        """
        with self._lock, closing(sqlite3.connect(self.db_path, isolation_level=None)) as connection:
            # WAL so this connection and the checkpointer's can hold the same file open (RT-4). The
            # saver sets it too; the pragma is idempotent and whichever gets there first wins.
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(_SCHEMA)
            yield connection

    def _setup(self) -> None:
        with self._connect():
            pass

    def _put(self, requests: Sequence[ScanRequest]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for request in requests:
                row = connection.execute(
                    f"SELECT changed_paths, requested_at FROM {_TABLE} "
                    "WHERE topic_id = ? AND status = ?",
                    (request.topic_id, PENDING),
                ).fetchone()
                merged = list(request.changed_paths)
                requested_at = request.requested_at.isoformat()
                if row is not None:
                    merged = _merge_paths(json.loads(row[0]), request.changed_paths)
                    requested_at = max(requested_at, str(row[1]))
                connection.execute(
                    f"INSERT OR REPLACE INTO {_TABLE} "
                    "(topic_id, status, topic_path, changed_paths, origin, requested_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        request.topic_id,
                        PENDING,
                        request.topic_path,
                        json.dumps(merged),
                        request.origin,
                        requested_at,
                    ),
                )
            connection.execute("COMMIT")

    def _take(self, limit: int) -> list[ScanRequest]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"SELECT topic_id, topic_path, changed_paths, origin, requested_at FROM {_TABLE} "
                "WHERE status = ? ORDER BY requested_at, topic_id LIMIT ?",
                (PENDING, limit),
            ).fetchall()
            for row in rows:
                connection.execute(
                    f"DELETE FROM {_TABLE} WHERE topic_id = ? AND status = ?",
                    (row[0], RUNNING),
                )
                connection.execute(
                    f"UPDATE {_TABLE} SET status = ? WHERE topic_id = ? AND status = ?",
                    (RUNNING, row[0], PENDING),
                )
            connection.execute("COMMIT")
        return [_row_to_request(row) for row in rows]

    def _done(self, topic_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                f"DELETE FROM {_TABLE} WHERE topic_id = ? AND status = ?",
                (topic_id, RUNNING),
            )

    def _pending(self) -> list[ScanRequest]:
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT topic_id, topic_path, changed_paths, origin, requested_at FROM {_TABLE} "
                "WHERE status = ? ORDER BY requested_at, topic_id",
                (PENDING,),
            ).fetchall()
        return [_row_to_request(row) for row in rows]


def _merge_paths(existing: Sequence[str], incoming: Sequence[str]) -> list[str]:
    """The union of two changed-path sets, deduplicated and ordered (RT-56)."""
    return sorted({*existing, *incoming})


def _row_to_request(row: Sequence[Any]) -> ScanRequest:
    return ScanRequest(
        topic_id=str(row[0]),
        topic_path=str(row[1]),
        changed_paths=tuple(json.loads(row[2])),
        origin=str(row[3]),
        requested_at=date.fromisoformat(str(row[4])),
    )


def on_demand_request(snapshot: KbSnapshot, topic_path: str, *, today: date) -> ScanRequest:
    """A whole-topic scan nothing in particular triggered (RT-57).

    Layer 2 never constructs a :class:`~pkb.core.models.ScanRequest` by hand: an empty
    ``changed_paths`` is legitimate and means "re-scan this topic", and
    :func:`pkb.core.maintenance.scan_request_for` is what fills in the agent id and the canonical
    topic path — which is why ``ScanRequest.topic_id`` resolves through the registry directly, with
    no re-parsing (PA-10).

    Raises:
        NotATopicRootError: *topic_path* names no topic root in *snapshot*.
    """
    return scan_request_for(snapshot, topic_path, origin=ON_DEMAND_ORIGIN, requested_at=today)


# --------------------------------------------------------------------------------------
# The scan run (RT-58, PR-8)
# --------------------------------------------------------------------------------------


class ScanRunner(Protocol):
    """What :func:`run_scan` needs from the runtime, structurally (RT-58).

    A Protocol rather than an import so this module stays below :mod:`pkb.agents.runtime` and the
    dependency runs one way only.
    """

    kb_root: Path

    def snapshot(self) -> KbSnapshot: ...

    def run(
        self,
        agent_id: str,
        thread_id: str,
        message: str,
        *,
        approval_mode: ApprovalMode = "interactive",
    ) -> AsyncIterator[AgentEvent]: ...


def scan_thread_id(topic_id: str, *, new_id: Callable[[], str] = lambda: str(uuid4())) -> str:
    """A reserved thread id for one scan run: ``scan:<agent_id>:<uuid4>`` (RT-58, Q9).

    Globally unique because the checkpointer keys on ``thread_id`` alone (D-6), and prefixed so
    Layer 3 can exclude scan runs from the human's thread list without a second table.
    """
    return f"{SCAN_THREAD_PREFIX}{topic_id}:{new_id()}"


def scan_prompt(request: ScanRequest) -> str:
    """The conflict-scan turn (PR-8).

    Names the skill that carries the procedure, the topic under scan and the three comparison axes,
    and says plainly that this run answers no user question. The *procedure* lives in the
    ``conflict-detection`` SKILL.md body and not here (SK-8): duplicating it would defeat the
    progressive disclosure the packaged skills are mounted for, and would put a second copy outside
    the human's reach when they rewrite the skill.

    The changed paths are named when the flush raised the request and omitted when it did not, so an
    on-demand whole-topic scan reads as a whole-topic scan rather than as a scan of nothing.
    """
    lines = [
        f"Run a conflict scan for the topic `{request.topic_path}`.",
        "",
        "Use the `conflict-detection` skill. Compare along its three axes:",
        "1. the topic's `notes/summary.md` against its `references/summary.md`;",
        "2. individual notes against the references they draw on;",
        "3. notes against other notes.",
        "",
        "This run answers no user question and produces no reply for a human to read as an answer. "
        "Its only output is the review flags the skill tells you to add, written through the "
        "ordinary file tools, plus a short report of what you compared and what you found.",
    ]
    if request.changed_paths:
        lines += [
            "",
            "These files changed since the last scan; start there, but the scan is of the whole "
            "topic:",
            *(f"- {path}" for path in request.changed_paths),
        ]
    return "\n".join(lines)


async def run_scan(runner: ScanRunner, request: ScanRequest) -> ScanResult:
    """Run one topic's conflict scan on its own reserved thread (RT-58).

    ``ScanRequest.topic_id`` is already an agent id (PA-10), so it addresses the topic's expert
    directly — the same compiled graph a human talks to, with the same skills, permissions and
    gates. That matters: the scan's judgment is a Topic Expert skill, not a separate maintainer
    agent (RG-1).

    The run is ``propose_only``. Tagging a conflict is deliberately ungated (RT-26) precisely so a
    background scan never waits on a human, but a scan that wanders into a gated write — a breadth
    file, a new tag — would otherwise park forever on a decision nobody is watching for. In
    propose-only mode the gate still fires, Layer 2 auto-rejects with its fixed message, the action
    is recorded as a :class:`~pkb.contracts.PendingProposal` for the human, and the scan finishes
    (RT-42).

    ``tagged_paths`` is the intersection of what the run actually wrote with what now carries
    ``status.conflict-review`` on disk. Both halves are needed: a write the validator refused must
    not be reported as a flag, and a file flagged by an *earlier* scan must not be re-reported by
    this one.
    """
    thread_id = scan_thread_id(request.topic_id)
    written: set[str] = set()
    summary = ""
    async for event in runner.run(
        request.topic_id, thread_id, scan_prompt(request), approval_mode="propose_only"
    ):
        if isinstance(event, ToolStart) and event.tool in _WRITE_TOOLS:
            # `ToolStart.summary` is the rendered path for a filesystem tool (RT-43); the raw
            # arguments never appear in an event, so this is the only place a scan can learn which
            # files it touched without reaching back into the harness.
            relative = to_kb_relative(event.summary)
            if relative is not None:
                written.add(relative)
        elif isinstance(event, RunEnd):
            summary = event.final_text
        elif isinstance(event, RunError):
            summary = f"The scan did not complete: {event.message}"

    flagged = files_with_tag(runner.snapshot(), CONFLICT_TAG)
    return ScanResult(
        topic_id=request.topic_id,
        thread_id=thread_id,
        tagged_paths=tuple(path for path in flagged if path in written),
        summary=summary,
    )
