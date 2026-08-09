"""The ``threads`` table — Layer 3's index of conversations (ST-1 … ST-13).

It lives in the checkpointer's own SQLite file, on **Layer 3's own ``aiosqlite`` connection**. Both
halves of that matter:

* *Same file* — RT-4 opens it WAL, and one file is one thing to back up, one thing to move, one
  thing to reason about. But the WAL pragma is set in ``AsyncSqliteSaver.setup()``, not by
  ``from_conn_string``, so **this connection must be opened after the runtime** (AP-4). Opened
  earlier it talks to a rollback-journal file, where a reader blocks a writer.
* *Own connection* — never the saver's. The saver pins itself to its creating loop and closes its
  connection on context exit; handing it out breaks both properties, and ``PkbRuntime`` exposes
  ``db_path`` for exactly this reason (ST-1).

**Never hold a write transaction open across an ``await``** (ST-3). WAL has exactly one writer, and
a handler doing ``BEGIN IMMEDIATE`` → ``await`` → ``COMMIT`` was measured killing a concurrent
checkpointer run with ``database is locked`` after sixteen seconds — which reaches the human as a
failed run with a written file and no flush. Every write here is a single short autocommit
statement, and that is the whole answer to "what breaks under concurrent access".

**The row is an index for discovery, never the authority on existence** (SV-12). The checkpoint is
the authority. A derived ``<parent>::<agent-id>`` thread is openable, runnable and resumable from
its id alone with no row of its own — **provided its parent is a thread that exists** (SV-12 amended
2026-08-09) — and touching one registers the row as a side effect. That asymmetry is what stops a
missing row from hiding a pending approval, the one failure arch §8 promises cannot happen; the
parent condition is what stops a fabricated id from manufacturing one.

What is *not* here is as deliberate as what is: no ``parent_thread_id`` column and no ``kind``
column. Both are pure functions of the thread id (LB-14), and a cached parentage column is a second
answer to a question ``librarian_thread_id()`` already answers exactly (decision D, ST-5).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import aiosqlite

from pkb.contracts import (
    EXPERT_THREAD_SEPARATOR,
    SCAN_THREAD_PREFIX,
    OriginChannel,
    UnknownThreadError,
    agent_for_thread,
    expert_thread_id,
    is_scan_thread,
    librarian_thread_id,
)
from pkb.service import Thread

__all__ = [
    "TABLE",
    "ThreadStore",
    "mint_run_id",
    "mint_thread_id",
    "open_connection",
]

TABLE: Final = "threads"
"""Layer 3 owns this name and anything prefixed ``pkb_`` — and nothing else (ST-7).

The file already holds ``checkpoints`` and ``writes`` (the checkpointer), ``store``,
``store_vectors``, ``store_migrations``, ``vector_migrations`` (the langgraph store) and
``scan_queue`` (Layer 2). Layer 3 never writes to, migrates or drops a table it does not own.
"""

MIGRATIONS_TABLE: Final = "pkb_service_migrations"
"""Layer 3's **own** version table, mirroring the langgraph store's ``store_migrations`` precedent.

Deliberately not ``PRAGMA user_version``. That pragma is verified unused by the checkpointer, the
store and the scan queue today, and that is exactly why it must not be claimed: it is one counter
per *file*, shared by four independent writers, and taking a global for a per-table concern is the
mistake ``checkpoint_ns`` was (ST-8, D-6).
"""

_SCHEMA: Final = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    thread_id            TEXT PRIMARY KEY,
    agent_id             TEXT NOT NULL,
    title                TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    origin_channel       TEXT NOT NULL,
    pending_interrupt_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_{TABLE}_agent ON {TABLE}(agent_id);
CREATE INDEX IF NOT EXISTS idx_{TABLE}_pending ON {TABLE}(pending_interrupt_id);
CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} (version INTEGER PRIMARY KEY);
"""

_SCHEMA_VERSION: Final = 1

BUSY_TIMEOUT_MS: Final = 5000
"""SQLite's default, restated so nobody lowers it (ST-4)."""

_COLUMNS: Final = (
    "thread_id, agent_id, title, created_at, updated_at, origin_channel, pending_interrupt_id"
)


async def open_connection(db_path: Path) -> aiosqlite.Connection:
    """Layer 3's own connection, opened **after** the runtime and in autocommit (AP-4, ST-2, ST-3).

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


def mint_thread_id() -> str:
    """A fresh user thread id — a bare ``uuid4``, and the assertion that it is safe (SV-9, SV-10).

    The three thread-id namespaces are disjoint *provided* a minted id contains no ``::`` and does
    not start with ``scan:``. ``uuid4`` satisfies both, and this asserts it rather than assuming it:
    the cost of the assertion is nothing and the cost of the assumption is two agents silently
    sharing one checkpoint (SV-11, D-6), which has no error anywhere.
    """
    minted = str(uuid.uuid4())
    assert EXPERT_THREAD_SEPARATOR not in minted, minted
    assert not minted.startswith(SCAN_THREAD_PREFIX), minted
    return minted


def mint_run_id() -> str:
    """A fresh run id, minted **before** the run starts (RO-11, SS-8).

    Layer 2 will invent one if Layer 3 does not, but by then it is too late for two things that
    matter: ``run.started`` is frame 0 and carries the id a client cancels with, and the supervisor
    keys its hubs and tasks on it. A run that has not emitted anything yet still has to be
    addressable — otherwise cancelling is a race with the first token.
    """
    return f"run-{uuid.uuid4().hex[:12]}"


class ThreadStore:
    """Short autocommit statements over one ``aiosqlite`` connection Layer 3 owns.

    Not a repository abstraction: every method here is one statement, because ST-3's rule is easier
    to keep when there is nowhere to put a second one.
    """

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def setup(self) -> None:
        """Create the table if it is not there, and record the schema version (ST-8).

        Additive by construction: later versions add columns with defaults rather than rewriting
        rows, so opening a v1 file with a v2 build keeps every row.
        """
        await self._connection.executescript(_SCHEMA)
        await self._connection.execute(
            f"INSERT OR IGNORE INTO {MIGRATIONS_TABLE}(version) VALUES (?)", (_SCHEMA_VERSION,)
        )
        await self._connection.commit()

    # -- writes ----------------------------------------------------------------------

    async def create(
        self,
        thread_id: str,
        agent_id: str,
        *,
        title: str | None,
        origin_channel: OriginChannel,
        now: datetime | None = None,
    ) -> Thread:
        """Insert a row, or raise on a duplicate id.

        A plain ``INSERT``: ``thread_id`` must be globally unique **across agents**, because the
        checkpointer keys on it alone and ``checkpoint_ns`` is unusable as a second dimension (D-6,
        verified). Two agents sharing an id silently merge two conversations into one checkpoint,
        with no error anywhere — so the PRIMARY KEY is the thing that makes it loud (SV-11).
        """
        stamp = _now(now)
        await self._connection.execute(
            f"INSERT INTO {TABLE} ({_COLUMNS}) VALUES (?,?,?,?,?,?,NULL)",
            (thread_id, agent_id, title, stamp, stamp, origin_channel),
        )
        await self._connection.commit()
        return Thread(
            thread_id=thread_id,
            agent_id=agent_id,
            title=title,
            created_at=_parse(stamp),
            updated_at=_parse(stamp),
            origin_channel=origin_channel,
        )

    async def register(
        self,
        thread_id: str,
        agent_id: str,
        *,
        title: str | None = None,
        origin_channel: OriginChannel = "http",
        now: datetime | None = None,
    ) -> None:
        """Record a thread that already exists in the checkpoint — idempotently (SV-12, ST-9, ST-11).

        ``INSERT OR IGNORE``, because this runs from event callbacks while a stream is live and must
        be safe to repeat after a crash. It is how a derived thread gets a row: at fan-out time from
        a ``SubagentStart`` (ST-12), and again from an ``InterruptEvent`` naming a thread with no row
        (ST-11) — a pending approval no channel can list is the one failure arch §8 promises cannot
        happen, and it is reachable, because registration is a separate step that can be missed.
        """
        stamp = _now(now)
        await self._connection.execute(
            f"INSERT OR IGNORE INTO {TABLE} ({_COLUMNS}) VALUES (?,?,?,?,?,?,NULL)",
            (thread_id, agent_id, title, stamp, stamp, origin_channel),
        )
        await self._connection.commit()

    async def touch(self, thread_id: str, *, now: datetime | None = None) -> None:
        """Bump ``updated_at`` — on every interrupt and every terminal event (ST-10)."""
        await self._connection.execute(
            f"UPDATE {TABLE} SET updated_at = ? WHERE thread_id = ?", (_now(now), thread_id)
        )
        await self._connection.commit()

    async def set_title(self, thread_id: str, title: str, *, now: datetime | None = None) -> None:
        await self._connection.execute(
            f"UPDATE {TABLE} SET title = ?, updated_at = ? WHERE thread_id = ?",
            (title, _now(now), thread_id),
        )
        await self._connection.commit()

    async def title_once(self, thread_id: str, title: str) -> bool:
        """Write a model-written title **only if the thread has none** (TT-3, TT-4).

        The ``WHERE title IS NULL`` is what makes "titled once" a property of the statement rather
        than of the caller's care: a human-set title is never overwritten, and neither is an earlier
        turn's, so a name somebody has learned to recognise does not move under them. Returns
        whether it landed, so a caller can log the no-op rather than guess.
        """
        cursor = await self._connection.execute(
            f"UPDATE {TABLE} SET title = ? WHERE thread_id = ? AND title IS NULL",
            (title, thread_id),
        )
        await self._connection.commit()
        return bool(cursor.rowcount)

    async def set_pending(
        self, thread_id: str, interrupt_id: str | None, *, now: datetime | None = None
    ) -> None:
        """Set or clear the pending-approval index (ST-10, decision E).

        Set on every ``InterruptEvent`` — **on the row of ``request.thread_id``, which may be a
        derived thread rather than the one the client is streaming** (LB-16) — and cleared on the
        first terminal event for a run on that thread that is not followed by a new interrupt.
        """
        await self._connection.execute(
            f"UPDATE {TABLE} SET pending_interrupt_id = ?, updated_at = ? WHERE thread_id = ?",
            (interrupt_id, _now(now), thread_id),
        )
        await self._connection.commit()

    async def delete_cascade(self, thread_id: str) -> int:
        """Delete a thread and every thread derived from it (SV-24, RT-48).

        Mirrors the runtime's checkpoint cascade exactly, and is called **after** it — if the SQL
        cascade did not mirror it, the table would keep rows pointing at erased checkpoints and the
        list would offer conversations that open empty. Deleting a *derived* thread deletes only
        that row: no sideways reach to siblings, no upwards reach to the parent, matching the
        runtime's own asymmetry.
        """
        cursor = await self._connection.execute(
            f"DELETE FROM {TABLE} WHERE thread_id = ? OR thread_id LIKE ?",
            (thread_id, f"{thread_id}{EXPERT_THREAD_SEPARATOR}%"),
        )
        await self._connection.commit()
        return cursor.rowcount

    # -- reads -----------------------------------------------------------------------

    async def get(self, thread_id: str) -> Thread | None:
        cursor = await self._connection.execute(
            f"SELECT {_COLUMNS} FROM {TABLE} WHERE thread_id = ?", (thread_id,)
        )
        row = await cursor.fetchone()
        return _row_to_thread(row) if row else None

    async def list_threads(self, agent_id: str | None = None) -> list[Thread]:
        """Threads for one agent, or all of them — never a ``scan:`` thread (RO-6, RO-7, RT-58).

        Exact match on ``agent_id``, never a prefix: ``topic/cooking`` must not return
        ``topic/cooking/grilling``'s threads, and ``LIKE 'topic/cooking%'`` would.

        Ordered so the thread that needs a human comes first. Arch §8 says the list "should be
        designed around" the abandoned-approval case, and a list sorted by creation date buries the
        very thread the human came back to answer.
        """
        order = "ORDER BY (pending_interrupt_id IS NOT NULL) DESC, updated_at DESC, thread_id"
        if agent_id is None:
            cursor = await self._connection.execute(
                f"SELECT {_COLUMNS} FROM {TABLE} WHERE thread_id NOT LIKE ? {order}",
                (f"{SCAN_THREAD_PREFIX}%",),
            )
        else:
            cursor = await self._connection.execute(
                f"SELECT {_COLUMNS} FROM {TABLE} "
                f"WHERE agent_id = ? AND thread_id NOT LIKE ? {order}",
                (agent_id, f"{SCAN_THREAD_PREFIX}%"),
            )
        return [_row_to_thread(row) for row in await cursor.fetchall()]

    async def children(self, thread_id: str) -> list[Thread]:
        """The threads this one routed to — provenance, not their primary home (SV-14, RO-7)."""
        cursor = await self._connection.execute(
            f"SELECT {_COLUMNS} FROM {TABLE} WHERE thread_id LIKE ? ORDER BY thread_id",
            (f"{thread_id}{EXPERT_THREAD_SEPARATOR}%",),
        )
        return [_row_to_thread(row) for row in await cursor.fetchall()]

    async def all_ids(self) -> list[tuple[str, str, str | None]]:
        """``(thread_id, agent_id, pending_interrupt_id)`` for the startup reconciliation (AP-5)."""
        cursor = await self._connection.execute(
            f"SELECT thread_id, agent_id, pending_interrupt_id FROM {TABLE} WHERE thread_id NOT LIKE ?",
            (f"{SCAN_THREAD_PREFIX}%",),
        )
        return [(row[0], row[1], row[2]) for row in await cursor.fetchall()]

    async def counts(self) -> tuple[int, int]:
        """``(total, pending_approvals)`` for ``/health`` — two indexed counts, no walk (AP-19)."""
        cursor = await self._connection.execute(
            f"SELECT COUNT(*), COUNT(pending_interrupt_id) FROM {TABLE} WHERE thread_id NOT LIKE ?",
            (f"{SCAN_THREAD_PREFIX}%",),
        )
        row = await cursor.fetchone()
        return (int(row[0]), int(row[1])) if row else (0, 0)

    # -- resolution ------------------------------------------------------------------

    async def resolve_agent(self, thread_id: str) -> str:
        """Which agent owns this thread — **shape first, table second** (SV-9, SV-12).

        Shape first is what lets a derived thread work with no row: after a fan-out the expert's
        thread is addressable immediately, and a client that lost its row (or never had one) can
        still open, run and resume it. The table answers only for a minted ``uuid4``, which carries
        no agent in its own name.

        **But the parent has to exist** (SV-12 amended 2026-08-09). ``<parent>::<agent-id>`` is
        openable, runnable, resumable and self-registering *only when ``<parent>`` is a thread that
        is there*; otherwise the id names no conversation and this raises. Measured before the
        amendment: ``POST /threads/<fresh-uuid4>::topic/cooking/runs`` answered **200** with a full
        event stream, ran a real expert turn against a checkpoint nothing had ever written, and left
        a permanent ``kind:"routed"`` row whose ``parent_thread_id`` 404s. SV-12's own reasoning —
        the row is an index for discovery and the checkpoint is the authority — is about a thread
        the fan-out really created and whose row was lost. A derived id whose parent never existed
        has no checkpoint and never had one: nothing was lost, so there is nothing to recover, and
        self-registering it manufactures an orphan that ``/threads`` lists forever and that no
        cascade will ever delete (``delete_cascade`` reaches children from a parent that is gone).
        The real case is untouched, because after a fan-out ``create_thread`` made the parent row
        before the derivation existed.

        A ``scan:`` thread refuses outright rather than resolving: it is machine bookkeeping whose
        context must never enter a human conversation (RT-58, SV-13).
        """
        if is_scan_thread(thread_id):
            raise UnknownThreadError(f"{thread_id!r} is a maintenance thread and cannot be opened")
        by_shape = agent_for_thread(thread_id)
        if by_shape is not None:
            parent = librarian_thread_id(thread_id)
            if parent is not None and await self.get(parent) is None:
                raise UnknownThreadError(
                    f"no thread {parent!r}, so {thread_id!r} names no conversation: a derived "
                    f"thread is the record of a routing that happened, and its parent is what says "
                    f"it did"
                )
            return by_shape
        row = await self.get(thread_id)
        if row is None:
            raise UnknownThreadError(f"no thread {thread_id!r}")
        return row.agent_id

    def derived_id(self, thread_id: str, agent_id: str) -> str:
        """The seam's derivation, re-exported so callers never spell ``::`` themselves (C-1)."""
        return expert_thread_id(thread_id, agent_id)


# --------------------------------------------------------------------------------------
# Row mapping
# --------------------------------------------------------------------------------------


def _row_to_thread(row: Sequence[object]) -> Thread:
    return Thread(
        thread_id=str(row[0]),
        agent_id=str(row[1]),
        title=str(row[2]) if row[2] is not None else None,
        created_at=_parse(str(row[3])),
        updated_at=_parse(str(row[4])),
        origin_channel=str(row[5]),  # type: ignore[arg-type]
        pending_interrupt_id=str(row[6]) if row[6] is not None else None,
    )


def _now(now: datetime | None) -> str:
    """ISO-8601 UTC with a trailing ``Z``, which is what the wire contract shows (§5.1)."""
    moment = now or datetime.now(UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
