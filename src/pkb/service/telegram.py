"""The bot's durable state — bindings and an update ledger (TG-13, decision S, T).

``_supervise`` restarts the task on **any** exception with nothing carried across: every dict,
client and subscription of the previous invocation is gone. Measured against the real supervisor,
three restarts in 3.6 s produced three fresh invocations. So anything the adapter must remember
across a crash lives here, in Layer 3's own SQLite file, and not in the task.

Two things are remembered, and each answers a failure that is otherwise silent.

**The chat → thread binding.** A chat is one continuous conversation; a thread is a turn-taking unit
(Q24, ruled). Held in memory, one 502 from Telegram silently starts the human's next message in a
brand-new conversation while the old one still holds their pending approval — an amnesiac bot, and
no error anywhere.

**The update ledger.** ``getUpdates`` is at-least-once: an update is confirmed only by the *next*
poll's offset, and an unconfirmed one is redelivered for 24 hours. With a supervisor that restarts
on anything, acknowledging *after* processing re-runs a turn that already wrote to a tree with no
undo (D6); acknowledging *before* processing loses the human's message with no trace. So the row is
written **before** dispatch and the offset is derived from the ledger: agent execution is
at-most-once, and a crash in the gap between the row and the run is a **named** loss the bot can
report on restart. "Re-send it" is a thing a human can act on; silence is not.

Layer 3 already reserves the ``pkb_`` prefix for its own tables (ST-7), and ``db_path`` defaults to
``<kb>/../pkb.sqlite`` — outside the tree, so I3 holds. This is deliberately **not** on
``PkbService``: it is one transport's bookkeeping and is useless to the TUI and to MCP.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only
    import aiosqlite

__all__ = [
    "BINDINGS_TABLE",
    "LEDGER_TABLE",
    "PROMPTS_TABLE",
    "SqliteTelegramStore",
    "TelegramStore",
]

BINDINGS_TABLE: Final = "pkb_telegram_bindings"
LEDGER_TABLE: Final = "pkb_telegram_updates"
PROMPTS_TABLE: Final = "pkb_telegram_prompts"

_SCHEMA: Final = f"""
CREATE TABLE IF NOT EXISTS {BINDINGS_TABLE} (
    chat_id    INTEGER PRIMARY KEY,
    thread_id  TEXT NOT NULL,
    agent_id   TEXT NOT NULL,
    bound_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
    update_id   INTEGER PRIMARY KEY,
    chat_id     INTEGER,
    kind        TEXT NOT NULL,
    seen_at     TEXT NOT NULL,
    dispatched  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS {PROMPTS_TABLE} (
    handle       TEXT PRIMARY KEY,
    chat_id      INTEGER NOT NULL,
    thread_id    TEXT NOT NULL,
    interrupt_id TEXT NOT NULL,
    message_ids  TEXT NOT NULL,
    answers_json TEXT NOT NULL DEFAULT '{{}}',
    action_count INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    resolved     INTEGER NOT NULL DEFAULT 0
);
"""


class TelegramStore(Protocol):
    """What the adapter needs to remember. A Protocol so the whole suite runs against a fake."""

    async def setup(self) -> None: ...

    async def bound_thread(self, chat_id: int) -> str | None: ...

    async def bind(self, chat_id: int, thread_id: str, agent_id: str) -> None: ...

    async def unbind(self, chat_id: int) -> None: ...

    async def next_offset(self) -> int | None: ...

    async def claim(self, update_id: int, chat_id: int | None, kind: str) -> bool: ...

    async def dispatched(self, update_id: int) -> None: ...

    async def orphans(self) -> list[int]: ...

    async def open_prompt(
        self, handle: str, chat_id: int, thread_id: str, interrupt_id: str, action_count: int
    ) -> None: ...

    async def prompt(self, handle: str) -> Mapping[str, Any] | None: ...

    async def record_message(self, handle: str, message_id: int) -> None: ...

    async def record_answer(self, handle: str, index: int, verb: str) -> Mapping[int, str]: ...

    async def resolve_prompt(self, handle: str) -> None: ...


class SqliteTelegramStore:
    """:class:`TelegramStore` over Layer 3's own connection — short autocommit statements (ST-3)."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def setup(self) -> None:
        await self._connection.executescript(_SCHEMA)
        await self._connection.commit()

    # -- the chat's current thread (Q24, ruled) --------------------------------------

    async def bound_thread(self, chat_id: int) -> str | None:
        cursor = await self._connection.execute(
            f"SELECT thread_id FROM {BINDINGS_TABLE} WHERE chat_id = ?", (chat_id,)
        )
        row = await cursor.fetchone()
        return str(row[0]) if row else None

    async def bind(self, chat_id: int, thread_id: str, agent_id: str) -> None:
        """Point a chat at a thread. Replaces, because ``/new`` rotates explicitly."""
        await self._connection.execute(
            f"INSERT INTO {BINDINGS_TABLE} (chat_id, thread_id, agent_id, bound_at) "
            f"VALUES (?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET "
            f"thread_id = excluded.thread_id, agent_id = excluded.agent_id, "
            f"bound_at = excluded.bound_at",
            (chat_id, thread_id, agent_id, _now()),
        )
        await self._connection.commit()

    async def unbind(self, chat_id: int) -> None:
        """Forget the current thread, so the next message starts a fresh one (``/new``)."""
        await self._connection.execute(
            f"DELETE FROM {BINDINGS_TABLE} WHERE chat_id = ?", (chat_id,)
        )
        await self._connection.commit()

    # -- the ledger (decision T) ------------------------------------------------------

    async def next_offset(self) -> int | None:
        """``MAX(update_id) + 1``, or ``None`` on a first run.

        Derived from the ledger rather than kept alongside it, because two records of the same fact
        can disagree and this one is reconstructible. On a fresh database ``None`` means "whatever
        Telegram still has", which is correct: an update nobody claimed was never processed.
        """
        cursor = await self._connection.execute(f"SELECT MAX(update_id) FROM {LEDGER_TABLE}")
        row = await cursor.fetchone()
        return int(row[0]) + 1 if row and row[0] is not None else None

    async def claim(self, update_id: int, chat_id: int | None, kind: str) -> bool:
        """Record an update **before** it is dispatched. ``False`` if it was already claimed.

        The return value is what makes execution at-most-once across a redelivery: Telegram resends
        an unconfirmed update for 24 hours, and a bot that re-ran each one would re-file the same
        note as many times as it crashed — into a tree with no undo.
        """
        cursor = await self._connection.execute(
            f"INSERT OR IGNORE INTO {LEDGER_TABLE} (update_id, chat_id, kind, seen_at, dispatched) "
            f"VALUES (?,?,?,?,0)",
            (update_id, chat_id, kind, _now()),
        )
        await self._connection.commit()
        return bool(cursor.rowcount)

    async def dispatched(self, update_id: int) -> None:
        await self._connection.execute(
            f"UPDATE {LEDGER_TABLE} SET dispatched = 1 WHERE update_id = ?", (update_id,)
        )
        await self._connection.commit()

    async def orphans(self) -> list[int]:
        """Updates claimed but never dispatched — a crash in the gap (decision T).

        These are the losses the bot **names** on restart rather than silently retrying. Retrying
        would re-run a turn that may already have written; staying silent leaves the human believing
        their message was filed. Telling them to re-send is the only option they can act on.
        """
        cursor = await self._connection.execute(
            f"SELECT update_id FROM {LEDGER_TABLE} WHERE dispatched = 0 ORDER BY update_id"
        )
        return [int(row[0]) for row in await cursor.fetchall()]

    # -- approval prompts (TG-57, TG-58, TG-60) -------------------------------------

    async def open_prompt(
        self, handle: str, chat_id: int, thread_id: str, interrupt_id: str, action_count: int
    ) -> None:
        """Record an approval the human is being shown.

        Durable because a button pressed after the daemon restarted arrives into an adapter with no
        memory of the message — Telegram redelivers an unconfirmed update for 24 hours, and RT-38
        makes the interrupt itself durable. Making this the *only* path means the restart case is
        exercised by every test rather than by an incident.
        """
        await self._connection.execute(
            f"INSERT OR REPLACE INTO {PROMPTS_TABLE} "
            f"(handle, chat_id, thread_id, interrupt_id, message_ids, answers_json, "
            f" action_count, created_at, resolved) VALUES (?,?,?,?,?,?,?,?,0)",
            (handle, chat_id, thread_id, interrupt_id, "[]", "{}", action_count, _now()),
        )
        await self._connection.commit()

    async def prompt(self, handle: str) -> Mapping[str, Any] | None:
        cursor = await self._connection.execute(
            f"SELECT handle, chat_id, thread_id, interrupt_id, message_ids, answers_json, "
            f"action_count, resolved FROM {PROMPTS_TABLE} WHERE handle = ?",
            (handle,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "handle": str(row[0]),
            "chat_id": int(row[1]),
            "thread_id": str(row[2]),
            "interrupt_id": str(row[3]),
            "message_ids": json.loads(str(row[4])),
            "answers": {int(k): v for k, v in json.loads(str(row[5])).items()},
            "action_count": int(row[6]),
            "resolved": bool(row[7]),
        }

    async def record_message(self, handle: str, message_id: int) -> None:
        """Remember every message of this approval, so all of them lose their keyboard (TG-63)."""
        current = await self.prompt(handle)
        if current is None:
            return
        ids = [*current["message_ids"], message_id]
        await self._connection.execute(
            f"UPDATE {PROMPTS_TABLE} SET message_ids = ? WHERE handle = ?",
            (json.dumps(ids), handle),
        )
        await self._connection.commit()

    async def record_answer(self, handle: str, index: int, verb: str) -> Mapping[int, str]:
        """Accumulate one action's answer and return the set so far (TG-60).

        Durable because a partial answer must survive a restart: CL-6 forbids padding a missing one,
        so a lost accumulator means the human's earlier taps are gone and the approval can only be
        finished from the TUI.
        """
        current = await self.prompt(handle)
        if current is None:
            return {}
        answers = {**current["answers"], index: verb}
        await self._connection.execute(
            f"UPDATE {PROMPTS_TABLE} SET answers_json = ? WHERE handle = ?",
            (json.dumps({str(k): v for k, v in answers.items()}), handle),
        )
        await self._connection.commit()
        return answers

    async def resolve_prompt(self, handle: str) -> None:
        await self._connection.execute(
            f"UPDATE {PROMPTS_TABLE} SET resolved = 1 WHERE handle = ?", (handle,)
        )
        await self._connection.commit()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
