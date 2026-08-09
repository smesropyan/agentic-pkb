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
    dispatched  INTEGER NOT NULL DEFAULT 0,
    thread_id   TEXT,
    run_id      TEXT
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

    async def binding(self, chat_id: int) -> tuple[str, str] | None: ...

    async def bind(self, chat_id: int, thread_id: str, agent_id: str) -> None: ...

    async def unbind(self, chat_id: int) -> None: ...

    async def next_offset(self) -> int | None: ...

    async def claim(self, update_id: int, chat_id: int | None, kind: str) -> bool: ...

    async def started(self, update_id: int, thread_id: str, run_id: str) -> None: ...

    async def dispatched(self, update_id: int) -> None: ...

    async def orphans(self) -> list[tuple[int, int | None]]: ...

    async def unfinished(self) -> list[tuple[int, int | None, str]]: ...

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
        """Create the three tables, and **check the precondition every other method rests on**.

        Every statement here is a single short autocommit write, which is only true because Layer 3
        opens its connection with ``isolation_level=None`` (ST-1). Handed a default aiosqlite
        connection (``isolation_level=""``), :meth:`_merged`'s ``execute → fetchall → commit`` would
        hold an implicit transaction across two awaits — and ST-3 measured what that costs: a
        handler holding a write transaction across an ``await`` killed a concurrent checkpointer run
        after the victim's 5 s timeout, surfacing as a failed agent run with a written file and no
        flush. The bot writes on the inbound path, which is exactly when a run is streaming.

        An unusable connection is an exception rather than a finding (house rule): there is no
        degraded mode here, and the failure it prevents appears somewhere else entirely.
        """
        if getattr(self._connection, "isolation_level", None) is not None:
            raise ValueError(
                "the telegram store needs an autocommit connection "
                "(aiosqlite.connect(..., isolation_level=None)); anything else holds a transaction "
                "across an await and stalls the checkpointer (TG-28, ST-1, ST-3)"
            )
        await self._connection.executescript(_SCHEMA)
        await self._connection.commit()

    # -- the chat's current thread (Q24, ruled) --------------------------------------

    async def bound_thread(self, chat_id: int) -> str | None:
        cursor = await self._connection.execute(
            f"SELECT thread_id FROM {BINDINGS_TABLE} WHERE chat_id = ?", (chat_id,)
        )
        row = await cursor.fetchone()
        return str(row[0]) if row else None

    async def binding(self, chat_id: int) -> tuple[str, str] | None:
        """The chat's current thread **and the agent it was bound for** (TG-26).

        ``agent_id`` was written from the first commit and read by nothing, so a chat re-mapped in
        the configuration kept filing into the expert it was originally bound to — silently, with
        no undo. Handing both back in one statement is what lets the adapter notice.
        """
        cursor = await self._connection.execute(
            f"SELECT thread_id, agent_id FROM {BINDINGS_TABLE} WHERE chat_id = ?", (chat_id,)
        )
        row = await cursor.fetchone()
        return (str(row[0]), str(row[1])) if row else None

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

    async def started(self, update_id: int, thread_id: str, run_id: str) -> None:
        """The run for this update has been **admitted** — it is no longer a loss (TG-29).

        Three states, not two, and the third is what makes the notice honest. ``dispatched`` used
        to flip only when the whole turn had been relayed, and a turn is 16 s on the cloud model
        and 284 s on the local fallback — so a task cancelled mid-stream left a row that looked
        identical to one that crashed before ``start_run``, and the bot told the human "I lost your
        message — please send it again" for a turn that had already run and may already have
        written. Re-sending then produces the duplicated, divergent write into a tree with no undo
        that decision T exists to stop.

        ``thread_id`` and ``run_id`` are recorded here because this is the moment they first exist,
        and TG-31's restart re-sync has nothing to reattach to without them. The guard on
        ``dispatched = 0`` keeps this from resurrecting a finished row.
        """
        await self._connection.execute(
            f"UPDATE {LEDGER_TABLE} SET dispatched = 1, thread_id = ?, run_id = ? "
            f"WHERE update_id = ? AND dispatched = 0",
            (thread_id, run_id, update_id),
        )
        await self._connection.commit()

    async def dispatched(self, update_id: int) -> None:
        """The update is finished with — nothing is owed to that chat (TG-29)."""
        await self._connection.execute(
            f"UPDATE {LEDGER_TABLE} SET dispatched = 2 WHERE update_id = ?", (update_id,)
        )
        await self._connection.commit()

    async def orphans(self) -> list[tuple[int, int | None]]:
        """Updates claimed but never **started** — a crash in the gap (decision T, TG-29).

        These are the losses the bot **names** on restart rather than silently retrying. Retrying
        would re-run a turn that may already have written; staying silent leaves the human believing
        their message was filed. Telling them to re-send is the only option they can act on.

        The ``chat_id`` travels with the id because the notice has to reach **the chat that lost the
        message**. Returning bare update ids left the adapter with a total and no addressee, so it
        broadcast a count to every mapped chat that happened to be in the owner allow-list —
        measured against the suite's own constants, that is *zero* notices for a real orphan. A
        silent loss is the one outcome this rule names as unacceptable.
        """
        cursor = await self._connection.execute(
            f"SELECT update_id, chat_id FROM {LEDGER_TABLE} WHERE dispatched = 0 ORDER BY update_id"
        )
        return [
            (int(row[0]), None if row[1] is None else int(row[1]))
            for row in await cursor.fetchall()
        ]

    async def unfinished(self) -> list[tuple[int, int | None, str]]:
        """Updates whose run was **started** and never finished — TG-31's re-sync input.

        Distinct from :meth:`orphans` in exactly the way that matters: the agent ran, so nothing
        may be replayed, but the chat was never told how it ended. The thread id is what the
        re-sync attaches to or re-reads.
        """
        cursor = await self._connection.execute(
            f"SELECT update_id, chat_id, thread_id FROM {LEDGER_TABLE} "
            f"WHERE dispatched = 1 AND thread_id IS NOT NULL ORDER BY update_id"
        )
        return [
            (int(row[0]), None if row[1] is None else int(row[1]), str(row[2]))
            for row in await cursor.fetchall()
        ]

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
        """Remember every message of this approval, so all of them lose their keyboard (TG-63).

        The id is appended by ``json_insert`` inside the ``UPDATE`` instead of being read out,
        extended in Python and written back — the same fix, and for the same reason, as
        :meth:`record_answer` (TG-60). A read-modify-write across an ``await`` lets two callers
        observe the same array and the second write erase the first, and the id that vanishes is a
        message whose keyboard is never cleared: a live approve button sitting in the chat on a
        write that already happened, pressable a week later.
        """
        await self._merged(
            f"UPDATE {PROMPTS_TABLE} SET message_ids = json_insert(message_ids, '$[#]', ?) "
            f"WHERE handle = ? RETURNING message_ids",
            (message_id, handle),
        )

    async def record_answer(self, handle: str, index: int, verb: str) -> Mapping[int, str]:
        """Accumulate one action's answer and return the set so far (TG-60).

        Durable because a partial answer must survive a restart: CL-6 forbids padding a missing one,
        so a lost accumulator means the human's earlier taps are gone and the approval can only be
        finished from the TUI.

        The merge happens **inside the database**, in one statement, for the same reason. Reading the
        blob, awaiting, then writing it back loses an answer whenever two taps are in flight at once:
        both callers observe the older set and the second ``UPDATE`` overwrites the first. Because
        CL-6 forbids padding the missing one, the accumulator then never reaches ``action_count``,
        ``resolve`` is never called, and the interrupt stays parked with both of the human's taps
        already spent — silent from the phone, where both buttons appeared to work. It is latent only
        because ``_poll`` happens to dispatch serially today; a per-chat dispatch task, a second
        store user or a second connection makes it reachable, and none of those is forbidden.

        The key is written as a JSON object label (``$."0"``), so it comes back a string and is
        re-keyed to ``int`` by :meth:`prompt`'s own convention — the indices order the decisions
        against ``request.actions``.
        """
        merged = await self._merged(
            f"UPDATE {PROMPTS_TABLE} SET answers_json = json_set(answers_json, ?, ?) "
            f"WHERE handle = ? RETURNING answers_json",
            (f'$."{int(index)}"', verb, handle),
        )
        return {int(k): str(v) for k, v in (merged or {}).items()}

    async def _merged(self, statement: str, parameters: tuple[Any, ...]) -> Any:
        """Run one merging ``UPDATE`` and hand back what it wrote, or ``None`` for an unknown handle.

        ``RETURNING`` is what keeps the read and the write one statement (TG-60): a follow-up
        ``SELECT`` would reopen the very gap the merge was moved into SQL to close. The rows are
        drained before the commit because a ``RETURNING`` cursor with unstepped rows is a statement
        still in progress. No match means no row: a stale handle must stay unresolvable rather than
        conjure a prompt of its own (TG-58).
        """
        cursor = await self._connection.execute(statement, parameters)
        rows = list(await cursor.fetchall())
        await self._connection.commit()
        if not rows:
            return None
        return json.loads(str(rows[0][0]))

    async def resolve_prompt(self, handle: str) -> None:
        await self._connection.execute(
            f"UPDATE {PROMPTS_TABLE} SET resolved = 1 WHERE handle = ?", (handle,)
        )
        await self._connection.commit()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
