"""``pkb_proposals`` — a durable home for a write an external agent could not approve (ST-14).

RT-42's propose-only mode auto-answers every gate with ``reject`` and records a
:class:`~pkb.contracts.PendingProposal`. ``PkbRuntime`` keeps those **in a list that dies with the
process**, offering only an optional ``proposal_sink``; without a sink, "the human sees them in the
TUI" is prose. One restart and a write nobody reviewed is simply gone — and the caller was told it
was proposed, so nothing anywhere records that it evaporated.

So the daemon passes a sink that persists here (AP-15), and the service reads back out of it. The
argument for the table is the same one that put ``threads`` here: Layer 2 must not grow a table for
it (RT-49), and the checkpointer cannot answer the question.

**v1 records, lists and dismisses; it cannot *apply*.** Applying an approved proposal needs a new
Layer 2 entry point that replays the stored bytes through validation, the write path, the flush and
the scan queue under the write lock — and an RT-18 amendment to sanction the writer. That is Layer 2
work and it is Q3, not something to smuggle into a transport (decision F). Re-prompting the model
instead is rejected outright: the human approved *specific content*, and handing the same prompt
back produces something else.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from pkb.contracts import ActionView, DecisionType, PendingProposal

if TYPE_CHECKING:  # pragma: no cover - typing only
    import aiosqlite

__all__ = ["PENDING", "TABLE", "ProposalStore"]

TABLE: Final = "pkb_proposals"
PENDING: Final = "pending"
DISMISSED: Final = "dismissed"

_SCHEMA: Final = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    proposal_id            TEXT PRIMARY KEY,
    agent_id               TEXT NOT NULL,
    thread_id              TEXT NOT NULL,
    tool                   TEXT NOT NULL,
    args_json              TEXT NOT NULL,
    description            TEXT NOT NULL,
    allowed_decisions_json TEXT NOT NULL,
    reason                 TEXT NOT NULL,
    created_at             TEXT NOT NULL,
    status                 TEXT NOT NULL,
    resolved_at            TEXT
);
CREATE INDEX IF NOT EXISTS idx_{TABLE}_status ON {TABLE}(status);
"""

_COLUMNS: Final = (
    "proposal_id, agent_id, thread_id, tool, args_json, description, "
    "allowed_decisions_json, reason, created_at, status, resolved_at"
)


class ProposalStore:
    """Short autocommit statements, exactly as :class:`~pkb.service.threads.ThreadStore` (ST-3)."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def setup(self) -> None:
        await self._connection.executescript(_SCHEMA)
        await self._connection.commit()

    async def record(self, proposal: PendingProposal) -> None:
        """Persist one proposal. Idempotent, because a sink may be called again after a retry.

        The action is stored **flattened** rather than pickled: ``args`` and ``allowed_decisions`` as
        JSON, everything else as a column. ``description`` already holds the server-rendered unified
        diff and any validation finding (RT-34, RT-35), so a client can render the proposal months
        later with the knowledge base in a different state — which is the whole point of keeping it.
        """
        action = proposal.action
        await self._connection.execute(
            f"INSERT OR IGNORE INTO {TABLE} ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,NULL)",
            (
                proposal.proposal_id,
                proposal.agent_id,
                proposal.thread_id,
                action.tool,
                json.dumps(action.args, ensure_ascii=False, default=str),
                action.description,
                json.dumps(list(action.allowed_decisions)),
                action.reason,
                _stamp(proposal.created_at),
                PENDING,
            ),
        )
        await self._connection.commit()

    async def list_proposals(self, *, status: str | None = PENDING) -> list[PendingProposal]:
        if status is None:
            cursor = await self._connection.execute(
                f"SELECT {_COLUMNS} FROM {TABLE} ORDER BY created_at DESC, proposal_id"
            )
        else:
            cursor = await self._connection.execute(
                f"SELECT {_COLUMNS} FROM {TABLE} WHERE status = ? "
                f"ORDER BY created_at DESC, proposal_id",
                (status,),
            )
        return [_row(row) for row in await cursor.fetchall()]

    async def get(self, proposal_id: str) -> PendingProposal | None:
        cursor = await self._connection.execute(
            f"SELECT {_COLUMNS} FROM {TABLE} WHERE proposal_id = ?", (proposal_id,)
        )
        row = await cursor.fetchone()
        return _row(row) if row else None

    async def dismiss(self, proposal_id: str, *, now: datetime | None = None) -> bool:
        """Take one off the human's queue.

        A status change rather than a delete: the proposal is the record that an agent tried to
        write something and was refused, and that record outliving the human's decision to ignore it
        is worth one row. Nothing in v1 ever flips it to *applied* (Q3).
        """
        cursor = await self._connection.execute(
            f"UPDATE {TABLE} SET status = ?, resolved_at = ? WHERE proposal_id = ? AND status = ?",
            (DISMISSED, _stamp(now or datetime.now(UTC)), proposal_id, PENDING),
        )
        await self._connection.commit()
        return bool(cursor.rowcount)

    async def count(self, *, status: str = PENDING) -> int:
        """One indexed count, for ``/health`` (AP-19)."""
        cursor = await self._connection.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE status = ?", (status,)
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0


def _row(row: Sequence[object]) -> PendingProposal:
    # `DecisionType` is a `Literal`, not a class, so the JSON strings are the values themselves —
    # validated on the way *in* by the gate table, which is the only thing that mints them.
    decisions: tuple[DecisionType, ...] = tuple(json.loads(str(row[6])))
    return PendingProposal(
        proposal_id=str(row[0]),
        agent_id=str(row[1]),
        thread_id=str(row[2]),
        action=ActionView(
            tool=str(row[3]),
            args=json.loads(str(row[4])),
            description=str(row[5]),
            allowed_decisions=decisions,
            reason=str(row[7]),
        ),
        created_at=datetime.fromisoformat(str(row[8]).replace("Z", "+00:00")),
    )


def _stamp(moment: datetime) -> str:
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
