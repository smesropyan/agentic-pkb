"""The bot's durable state — channel bindings, the update ledger, the channel directory (TG-13, S, T).

``_supervise`` restarts the task on **any** exception with nothing carried across: every dict,
client and subscription of the previous invocation is gone. Measured against the real supervisor,
three restarts in 3.6 s produced three fresh invocations. So anything the adapter must remember
across a crash lives here, in Layer 3's own SQLite file, and not in the task.

**The unit of addressing is the channel, not the chat (TG-72, decision Y).** A channel is the pair
``(chat_id, topic_id)``, where ``topic_id`` is the ``message_thread_id`` a private-chat topic puts
on both directions of the wire, or :data:`GENERAL` — **0** — for the part of the chat that carries
none. Zero rather than ``None`` because this is a primary key, an index component and a dict key:
SQLite treats NULLs as *distinct* in a unique index, so a nullable topic column silently permits two
General rows for one chat, and nothing would ever notice. Telegram mints topic ids from the message
id sequence, which starts at 1, so 0 is permanently free and is never a real topic.

Three things are remembered, and each answers a failure that is otherwise silent. A fourth used to be
here — the approval prompts a gate posted while a run sat parked, waiting on a button press. It is
gone along with the gates themselves: no tool call can raise an ``InterruptEvent`` any more
(DESIGN.md §2.10 — the operator's instruction is the approval, so there is nothing left to park and
nothing left to remember about it).

**The channel → session binding.** A channel is one continuous conversation; a session is DESIGN.md
§2's durable, named unit of work (Task 7 repoints this table from the retired thread model: the
column the row keys on is a **session id**, not a thread id — ``DESIGN.md`` §2.5, "the operator opens
a channel and attaches it to a session"). Held in memory, one 502 from Telegram silently starts the
human's next message in a brand-new conversation while the old one is forgotten — an amnesiac bot,
and no error anywhere. Keyed per channel (TG-26 amended) because one session per *chat* under topics
would mean every expert in that chat sharing one conversation: the human sees distinct topics and
assumes distinct conversations, and the mis-file TG-1 exists to prevent happens with no configuration
change at all.

**The update ledger.** ``getUpdates`` is at-least-once: an update is confirmed only by the *next*
poll's offset, and an unconfirmed one is redelivered for 24 hours. With a supervisor that restarts
on anything, acknowledging *after* processing re-runs a turn that already wrote to a tree with no
undo (D6); acknowledging *before* processing loses the human's message with no trace. So the row is
written **before** dispatch and the offset is derived from the ledger: agent execution is
at-most-once, and a crash in the gap between the row and the run is a **named** loss the bot can
report on restart. It carries the topic beside the chat (TG-29 amended) so the notice reaches the
channel that lost the message rather than the right chat's wrong topic.

**The channel directory** (``pkb_telegram_channels``, TG-77). Which topic is which agent's, and how
many times it has had to be recreated. Durable because Telegram mints a topic id, shows it in no
client, and — decisively — **no API enumerates a chat's topics** (F-5). A directory held in memory
is a set of topics that exist on the human's phone and are addressable by nothing, permanently. It
is the bot's own bookkeeping, and TG-17 still holds word for word: the *decision* that an agent is
reachable from the phone is a human typing ``/channels``; the id Telegram mints in reply is an
address, not a decision (decision AB).

Layer 3 already reserves the ``pkb_`` prefix for its own tables (ST-7), and ``db_path`` defaults to
``<kb>/../pkb.sqlite`` — outside the tree, so I3 holds. This is deliberately **not** on
``PkbService``: it is one transport's bookkeeping and is useless to the TUI and to MCP.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only
    import aiosqlite

__all__ = [
    "BINDINGS_TABLE",
    "CHANNELS_TABLE",
    "GENERAL",
    "LEDGER_TABLE",
    "LEGACY_BINDINGS_TABLE",
    "SqliteTelegramStore",
    "TelegramStore",
]

GENERAL: Final = 0
"""The General area of a private chat — the part that carries no ``message_thread_id`` (TG-72).

Defined **here**, beside the ``NOT NULL DEFAULT 0`` columns it is the default of, and imported by
Layer 5 rather than declared a second time: two spellings of one sentinel is how a General row and a
topic row stop comparing equal. ``0`` and not ``None`` — see the module docstring.
"""

BINDINGS_TABLE: Final = "pkb_telegram_channel_bindings"
LEGACY_BINDINGS_TABLE: Final = "pkb_telegram_bindings"
LEDGER_TABLE: Final = "pkb_telegram_updates"
CHANNELS_TABLE: Final = "pkb_telegram_channels"

# `topic_id` is declared LAST on the migrated table on purpose. `ALTER TABLE … ADD COLUMN` appends,
# so a file that upgrades gets it last; declaring it anywhere else here would give a fresh file a
# different column order from an upgraded one, and `SELECT *` would then mean two different things
# depending on when the deployment was installed.
_SCHEMA: Final = f"""
CREATE TABLE IF NOT EXISTS {BINDINGS_TABLE} (
    chat_id    INTEGER NOT NULL,
    topic_id   INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    agent_id   TEXT NOT NULL,
    bound_at   TEXT NOT NULL,
    PRIMARY KEY (chat_id, topic_id)
);
CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
    update_id   INTEGER PRIMARY KEY,
    chat_id     INTEGER,
    kind        TEXT NOT NULL,
    seen_at     TEXT NOT NULL,
    dispatched  INTEGER NOT NULL DEFAULT 0,
    session_id  TEXT,
    run_id      TEXT,
    topic_id    INTEGER NOT NULL DEFAULT {GENERAL}
);
CREATE TABLE IF NOT EXISTS {CHANNELS_TABLE} (
    chat_id     INTEGER NOT NULL,
    topic_id    INTEGER NOT NULL,
    agent_id    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    recreations INTEGER NOT NULL DEFAULT 0,
    retired     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, agent_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS {CHANNELS_TABLE}_topic_idx
    ON {CHANNELS_TABLE} (chat_id, topic_id);
"""


class TelegramStore(Protocol):
    """What the adapter needs to remember. A Protocol so the whole suite runs against a fake.

    **No method taking a chat takes a default topic** (TG-72). A default here would let one missed
    call site file a topic's message under the chat's General binding — a message answered by the
    previous topic's expert, which is the exact mis-file TG-1 exists to prevent and the one the
    adapter cannot see in a review diff. Passing :data:`GENERAL` explicitly is one token and makes
    every call site say which channel it means.
    """

    async def setup(self) -> None: ...

    async def bound_session(self, chat_id: int, topic_id: int) -> str | None: ...

    async def binding(self, chat_id: int, topic_id: int) -> tuple[str, str] | None: ...

    async def bind(self, chat_id: int, topic_id: int, session_id: str, agent_id: str) -> None: ...

    async def unbind(self, chat_id: int, topic_id: int) -> None: ...

    async def next_offset(self) -> int | None: ...

    async def claim(
        self, update_id: int, chat_id: int | None, topic_id: int, kind: str
    ) -> bool: ...

    async def started(self, update_id: int, session_id: str, run_id: str) -> None: ...

    async def dispatched(self, update_id: int) -> None: ...

    async def orphans(self) -> list[tuple[int, int | None, int]]: ...

    async def unfinished(self) -> list[tuple[int, int | None, int, str]]: ...

    # -- the channel directory (TG-77, TG-82) ----------------------------------------

    async def channels(self, chat_id: int) -> Mapping[int, str]: ...

    async def channel(self, chat_id: int, agent_id: str) -> Mapping[str, Any] | None: ...

    async def channel_agents(self) -> frozenset[str]: ...

    async def retired_agents(self) -> frozenset[str]: ...

    async def open_channel(self, chat_id: int, topic_id: int, agent_id: str) -> None: ...

    async def rebind_channel(self, chat_id: int, agent_id: str, topic_id: int) -> int: ...

    async def retire_channel(self, chat_id: int, agent_id: str) -> None: ...


class SqliteTelegramStore:
    """:class:`TelegramStore` over Layer 3's own connection — short autocommit statements (ST-3)."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def setup(self) -> None:
        """Create the tables, migrate an older file, and **check the precondition every other
        method rests on**.

        Every statement here is a single short autocommit write, which is only true because Layer 3
        opens its connection with ``isolation_level=None`` (ST-1). Handed a default aiosqlite
        connection (``isolation_level=""``), :meth:`rebind_channel`'s ``execute → fetchall → commit``
        would hold an implicit transaction across two awaits — and ST-3 measured what that costs: a
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
        await self._migrate()

    # -- migration (TG-28 amended) ----------------------------------------------------

    async def _migrate(self) -> None:
        """Bring a pre-topics file up to the channel schema, and a pre-session one up to Task 7's
        session-keyed binding — **additively, and only once each**.

        Four statements at most, each one short and autocommitted, because ST-3 measured a long
        transaction on this connection killing a concurrent checkpointer run and this runs at
        startup, when the daemon is doing everything else at the same time.

        **The session rename (Task 7).** ``BINDINGS_TABLE``/``LEDGER_TABLE`` each carried a
        ``thread_id`` column under the retired channel-is-identity model; DESIGN.md §2 replaces a
        thread with a session, so :meth:`_rename_column` renames both to ``session_id`` — a
        metadata-only ``ALTER TABLE … RENAME COLUMN`` (SQLite ≥ 3.25), not a rebuild, so TG-28's own
        rule holds unchanged. This is a **structural** rename only: a row a previous build wrote
        holds a real thread id in what is now the ``session_id`` column, and that value names
        nothing — threads and sessions are disjoint id spaces (``pkb.service.sessions.
        mint_session_id`` mints a bare ``uuid4`` the same way ``mint_thread_id`` did, so the two
        cannot even be told apart by shape). Clearing those rows here would need a second migration
        flag and a judgment call about which rows are stale that this module has no way to make
        honestly. Instead the *runtime* self-heals it: ``TelegramAdapter._turn`` (Task 7) treats
        ``UnknownSessionError`` exactly like a stale binding — indistinguishable, deliberately — and
        rebinds fresh on the channel's very next message, so a leftover thread id here is corrected
        the first time anyone uses the channel again rather than requiring an operator to notice and
        clear it by hand.

        The ledger also takes an ``ADD COLUMN`` for ``topic_id``: its primary key (``update_id``) is
        still correct under topics, so the topic is one more column and nothing moves. ``NOT NULL
        DEFAULT 0`` is legal as an ``ADD COLUMN`` on a populated table and needs no rewrite — every
        existing row becomes a General row, which is exactly right.

        **The bindings could not be migrated in place**, and this is the one place §9.6.1's
        "``ADD COLUMN`` and a unique index" is not sufficient. The shipped table declares
        ``chat_id INTEGER PRIMARY KEY``, which in SQLite is a rowid alias: it permits exactly one
        row per chat, forever, and no column added beside it can change that. An upgraded
        deployment would therefore share one binding row across every topic in the chat — the
        human sees a topic per expert and gets one rotating conversation behind them, which is
        TG-1's mis-file with a UI that actively denies it. Rebuilding the table is the standard fix
        and is the one operation TG-28 forbids by name, for a measured reason.

        So the channel bindings get their own table and the shipped rows are **carried over** into
        it as General bindings: one ``INSERT … SELECT`` of at most one row per chat. The old table
        is left standing with every row intact — it is the only surviving record of what the
        deployment looked like before the upgrade, and this system has no undo (D6). It is never
        written again, because two records of one fact can disagree. Its own ``thread_id`` column is
        left exactly as it was minted (a real, pre-session thread id) and carried into the new
        table's ``session_id`` column under the identical "structural rename only" reasoning above.

        ``migrated_at`` is what makes the carry-over happen exactly once. Without it, a human who
        upgrades, types ``/new`` to rotate their General thread, and then restarts the daemon has
        the *pre-upgrade* thread resurrected under them — silently continuing a conversation they
        deliberately left. ``INSERT OR IGNORE`` alone cannot see that, because ``unbind`` deleted
        the row it would have conflicted with. A crash between the insert and the stamp re-runs
        both on the next start, which is harmless: nothing has polled yet, so no rotation can have
        happened in between.
        """
        await self._rename_column(BINDINGS_TABLE, "thread_id", "session_id")
        await self._rename_column(LEDGER_TABLE, "thread_id", "session_id")
        await self._add_column(LEDGER_TABLE, "topic_id", f"INTEGER NOT NULL DEFAULT {GENERAL}")
        if not await self._table_exists(LEGACY_BINDINGS_TABLE):
            return
        await self._add_column(LEGACY_BINDINGS_TABLE, "migrated_at", "TEXT")
        await self._connection.execute(
            f"INSERT OR IGNORE INTO {BINDINGS_TABLE} "
            f"(chat_id, topic_id, session_id, agent_id, bound_at) "
            f"SELECT chat_id, {GENERAL}, thread_id, agent_id, bound_at "
            f"FROM {LEGACY_BINDINGS_TABLE} WHERE migrated_at IS NULL"
        )
        await self._connection.commit()
        await self._connection.execute(
            f"UPDATE {LEGACY_BINDINGS_TABLE} SET migrated_at = ? WHERE migrated_at IS NULL",
            (_now(),),
        )
        await self._connection.commit()

    async def _table_exists(self, table: str) -> bool:
        """``sqlite_master``, not ``PRAGMA table_info``.

        ``table_info`` on a table that does not exist returns zero rows rather than raising, which
        is indistinguishable from a table with no columns — and a fresh installation must not be
        handed the pre-topics table just so the carry-over has something to read.
        """
        cursor = await self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        )
        return await cursor.fetchone() is not None

    async def _rename_column(self, table: str, old: str, new: str) -> None:
        """``ALTER TABLE … RENAME COLUMN``, guarded both ways: a no-op once ``new`` already exists
        (the common case — every file created after Task 7 never had ``old`` at all, since
        :data:`_SCHEMA` already declares the new name), and a no-op on a table with neither (nothing
        to rename yet, which :meth:`setup`'s own ``executescript`` handles a moment later).

        Metadata-only in SQLite ≥ 3.25 (bundled by every supported Python here) — it does not
        rewrite the table's rows or touch the primary key, so TG-28's "never rebuild a table" holds
        exactly as it does for :meth:`_add_column`.
        """
        columns = await self._columns(table)
        if new in columns or old not in columns:
            return
        await self._connection.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
        await self._connection.commit()

    async def _add_column(self, table: str, column: str, ddl: str) -> None:
        """``ALTER TABLE … ADD COLUMN``, guarded by ``PRAGMA table_info`` — the same check the
        schema init already uses, and idempotent because ``setup()`` runs on every daemon start.

        SQLite has no ``ADD COLUMN IF NOT EXISTS``, and a second attempt is a hard error that would
        take the daemon down on its second boot rather than its first — the worst place to learn
        about it.
        """
        if column in await self._columns(table):
            return
        await self._connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        await self._connection.commit()

    async def _columns(self, table: str) -> frozenset[str]:
        cursor = await self._connection.execute(f"PRAGMA table_info({table})")
        return frozenset(str(row[1]) for row in await cursor.fetchall())

    # -- the channel's current session (Q24, ruled; TG-26 amended; Task 7) ------------

    async def bound_session(self, chat_id: int, topic_id: int) -> str | None:
        """The session id this channel currently reaches, or ``None`` (S-4, S-6, S-13).

        Named for what it now holds (Task 7 repoints the whole table from thread to session — see
        the module and :meth:`_migrate` docstrings): a row here maps a chat/topic to a **session**
        id, never a thread id, whatever this method was called before.
        """
        cursor = await self._connection.execute(
            f"SELECT session_id FROM {BINDINGS_TABLE} WHERE chat_id = ? AND topic_id = ?",
            (chat_id, topic_id),
        )
        row = await cursor.fetchone()
        return str(row[0]) if row else None

    async def binding(self, chat_id: int, topic_id: int) -> tuple[str, str] | None:
        """The channel's current session **and the agent it was bound for** (TG-26).

        ``agent_id`` was written from the first commit and read by nothing, so a chat re-mapped in
        the configuration kept filing into the expert it was originally bound to — silently, with
        no undo. Handing both back in one statement is what lets the adapter notice. Under topics
        the same check answers a second question: a topic whose directory row now names a different
        agent (TG-79's revival, TG-87's rebind) must rotate rather than continue.
        """
        cursor = await self._connection.execute(
            f"SELECT session_id, agent_id FROM {BINDINGS_TABLE} WHERE chat_id = ? AND topic_id = ?",
            (chat_id, topic_id),
        )
        row = await cursor.fetchone()
        return (str(row[0]), str(row[1])) if row else None

    async def bind(self, chat_id: int, topic_id: int, session_id: str, agent_id: str) -> None:
        """Point a channel at a session. Replaces, because a rebind (``/threads``, a fresh
        ``_turn``) moves the channel explicitly (S-7: "a channel holds one session at a time")."""
        await self._connection.execute(
            f"INSERT INTO {BINDINGS_TABLE} (chat_id, topic_id, session_id, agent_id, bound_at) "
            f"VALUES (?,?,?,?,?) ON CONFLICT(chat_id, topic_id) DO UPDATE SET "
            f"session_id = excluded.session_id, agent_id = excluded.agent_id, "
            f"bound_at = excluded.bound_at",
            (chat_id, topic_id, session_id, agent_id, _now()),
        )
        await self._connection.commit()

    async def unbind(self, chat_id: int, topic_id: int) -> None:
        """Forget this channel's current session, so its next message opens a fresh one.

        Called on ``/close`` (S-17: the channel comes away from the session) and on a reactive
        rebind, when the bound session turns out stale (closed, sealed, or minted by a build before
        Task 7's session repoint — see ``TelegramAdapter._turn``). Scoped to the one channel (TG-27,
        carried over from ``/new``'s retired rotation): unbinding General must leave the Cooking
        conversation exactly where it was, because a channel this call says nothing about is a
        channel nothing here should touch.
        """
        await self._connection.execute(
            f"DELETE FROM {BINDINGS_TABLE} WHERE chat_id = ? AND topic_id = ?", (chat_id, topic_id)
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

    async def claim(self, update_id: int, chat_id: int | None, topic_id: int, kind: str) -> bool:
        """Record an update **before** it is dispatched. ``False`` if it was already claimed.

        The return value is what makes execution at-most-once across a redelivery: Telegram resends
        an unconfirmed update for 24 hours, and a bot that re-ran each one would re-file the same
        note as many times as it crashed — into a tree with no undo.

        ``topic_id`` travels with ``chat_id`` (TG-29 amended) so that :meth:`orphans` can name the
        channel that lost the message. An update with no chat at all (``chat_id is None``) is
        claimed with :data:`GENERAL`, which is meaningless and harmless: nothing addresses a notice
        to a chat that does not exist.
        """
        cursor = await self._connection.execute(
            f"INSERT OR IGNORE INTO {LEDGER_TABLE} "
            f"(update_id, chat_id, topic_id, kind, seen_at, dispatched) VALUES (?,?,?,?,?,0)",
            (update_id, chat_id, topic_id, kind, _now()),
        )
        await self._connection.commit()
        return bool(cursor.rowcount)

    async def started(self, update_id: int, session_id: str, run_id: str) -> None:
        """The run for this update has been **admitted** — it is no longer a loss (TG-29).

        Three states, not two, and the third is what makes the notice honest. ``dispatched`` used
        to flip only when the whole turn had been relayed, and a turn is 16 s on the cloud model
        and 284 s on the local fallback — so a task cancelled mid-stream left a row that looked
        identical to one that crashed before ``start_run``, and the bot told the human "I lost your
        message — please send it again" for a turn that had already run and may already have
        written. Re-sending then produces the duplicated, divergent write into a tree with no undo
        that decision T exists to stop.

        ``session_id`` and ``run_id`` are recorded here because this is the moment they first exist,
        and TG-31's restart re-sync has nothing to reattach to without them. The guard on
        ``dispatched = 0`` keeps this from resurrecting a finished row. The channel is not recorded
        here because :meth:`claim` already wrote it, before anything could go wrong.
        """
        await self._connection.execute(
            f"UPDATE {LEDGER_TABLE} SET dispatched = 1, session_id = ?, run_id = ? "
            f"WHERE update_id = ? AND dispatched = 0",
            (session_id, run_id, update_id),
        )
        await self._connection.commit()

    async def dispatched(self, update_id: int) -> None:
        """The update is finished with — nothing is owed to that channel (TG-29)."""
        await self._connection.execute(
            f"UPDATE {LEDGER_TABLE} SET dispatched = 2 WHERE update_id = ?", (update_id,)
        )
        await self._connection.commit()

    async def orphans(self) -> list[tuple[int, int | None, int]]:
        """Updates claimed but never **started** — a crash in the gap (decision T, TG-29).

        These are the losses the bot **names** on restart rather than silently retrying. Retrying
        would re-run a turn that may already have written; staying silent leaves the human believing
        their message was filed. Telling them to re-send is the only option they can act on.

        The ``chat_id`` **and the ``topic_id``** travel with the id because the notice has to reach
        **the channel that lost the message**. Returning bare update ids left the adapter with a
        total and no addressee, so it broadcast a count to every mapped chat that happened to be in
        the owner allow-list — measured against the suite's own constants, that is *zero* notices
        for a real orphan. Returning the chat alone is the same defect one level down: "I lost your
        message" arriving in General, about a message the human sent to Cooking, names the wrong
        conversation to re-send into.
        """
        cursor = await self._connection.execute(
            f"SELECT update_id, chat_id, topic_id FROM {LEDGER_TABLE} "
            f"WHERE dispatched = 0 ORDER BY update_id"
        )
        return [
            (int(row[0]), None if row[1] is None else int(row[1]), int(row[2]))
            for row in await cursor.fetchall()
        ]

    async def unfinished(self) -> list[tuple[int, int | None, int, str]]:
        """Updates whose run was **started** and never finished — TG-31's re-sync input.

        Distinct from :meth:`orphans` in exactly the way that matters: the agent ran, so nothing
        may be replayed, but the channel was never told how it ended. The session id is what the
        re-sync attaches to or re-reads, and the topic is where the outcome has to be posted — a
        restart that re-posts an outcome into General is TG-80's failure without anyone having
        deleted anything.
        """
        cursor = await self._connection.execute(
            f"SELECT update_id, chat_id, topic_id, session_id FROM {LEDGER_TABLE} "
            f"WHERE dispatched = 1 AND session_id IS NOT NULL ORDER BY update_id"
        )
        return [
            (int(row[0]), None if row[1] is None else int(row[1]), int(row[2]), str(row[3]))
            for row in await cursor.fetchall()
        ]

    # -- the channel directory (TG-77, TG-79, TG-82) ---------------------------------

    async def channels(self, chat_id: int) -> Mapping[int, str]:
        """``topic_id → agent_id`` for this chat's **live** channels — the inbound routing table.

        Retired channels (TG-82) are absent, and that is the useful default: their Telegram topic
        was deleted, so nothing can arrive from it, and an agent whose channel is retired genuinely
        has no reachable topic here — which is what ``/channels`` should list and what a send has to
        act on. The retired row is still there and is reached by :meth:`channel`, which is the one
        reader that needs to tell "no channel" from "a channel we gave up on".

        :data:`GENERAL` never appears: General's agent is ``config.chats[chat_id]``, human
        configuration and not directory state (TG-73), and a row at 0 would be a second answer to
        the one question TG-1 promises has one.
        """
        cursor = await self._connection.execute(
            f"SELECT topic_id, agent_id FROM {CHANNELS_TABLE} "
            f"WHERE chat_id = ? AND retired = 0 ORDER BY topic_id",
            (chat_id,),
        )
        return {int(row[0]): str(row[1]) for row in await cursor.fetchall()}

    async def channel(self, chat_id: int, agent_id: str) -> Mapping[str, Any] | None:
        """This agent's channel in this chat, retired or not — the **outbound** address (TG-84).

        Carries ``recreations`` and ``retired`` because the caller cannot decide anything without
        them: TG-82's bound is checked here, *before* a ``createForumTopic``, and TG-84's "never
        send with a stale id" is only enforceable by a caller that can see the channel is dead.
        ``None`` means the agent has no channel here at all, which is the ordinary case under
        decision AA and the one TG-88 falls back to General for.

        It is also TG-77's duplicate check: ``/channels <agent-id>`` for an agent that already has
        one creates nothing and points at what this returns.
        """
        cursor = await self._connection.execute(
            f"SELECT topic_id, agent_id, created_at, recreations, retired FROM {CHANNELS_TABLE} "
            f"WHERE chat_id = ? AND agent_id = ?",
            (chat_id, agent_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "chat_id": chat_id,
            "topic_id": int(row[0]),
            "agent_id": str(row[1]),
            "created_at": str(row[2]),
            "recreations": int(row[3]),
            "retired": bool(row[4]),
        }

    async def channel_agents(self) -> frozenset[str]:
        """Every agent with a channel anywhere — what the daemon seeds ``/health`` from (TG-11).

        Retired agents are **included**: their traffic still reaches the human, in General with the
        agent id as its first line (TG-82, TG-85), so reporting them as unreachable would be false.
        ``retired_channels`` is the field that says which ones are in that state.

        Read at composition time rather than from the running bot, because TG-11's whole stated
        property is that the answer survives a crash-looping adapter — which is precisely when
        ``/health`` gets read.
        """
        cursor = await self._connection.execute(f"SELECT DISTINCT agent_id FROM {CHANNELS_TABLE}")
        return frozenset(str(row[0]) for row in await cursor.fetchall())

    async def retired_agents(self) -> frozenset[str]:
        """Agents whose channel was given up on past ``MAX_RECREATIONS`` — ``/health`` (TG-82)."""
        cursor = await self._connection.execute(
            f"SELECT DISTINCT agent_id FROM {CHANNELS_TABLE} WHERE retired = 1"
        )
        return frozenset(str(row[0]) for row in await cursor.fetchall())

    async def open_channel(self, chat_id: int, topic_id: int, agent_id: str) -> None:
        """Record the channel a ``/channels`` command just created or bound (TG-76, TG-77, TG-87).

        Last-write-wins on ``(chat_id, agent_id)``, with the recreation count and the retirement
        **reset**, because that is exactly TG-87's revival: a human typing ``/channels`` for a
        retired agent is asking for a working channel, and carrying the old count forward would
        retire the new topic after one more deletion for a fight the human already conceded.

        It is deliberately **not** last-write-wins on ``(chat_id, topic_id)``: the unique index
        there refuses to let one topic address two agents, and the write fails loudly rather than
        silently re-pointing a conversation. That is TG-1's guarantee expressed as a constraint —
        the caller checks the topic is unbound first (TG-87), and a caller that did not has a bug
        that must not resolve itself into the human's Cooking history answering as Baking.
        """
        await self._connection.execute(
            f"INSERT INTO {CHANNELS_TABLE} "
            f"(chat_id, topic_id, agent_id, created_at, recreations, retired) VALUES (?,?,?,?,0,0) "
            f"ON CONFLICT(chat_id, agent_id) DO UPDATE SET "
            f"topic_id = excluded.topic_id, created_at = excluded.created_at, "
            f"recreations = 0, retired = 0",
            (chat_id, topic_id, agent_id, _now()),
        )
        await self._connection.commit()

    async def rebind_channel(self, chat_id: int, agent_id: str, topic_id: int) -> int:
        """Re-address a channel whose topic was deleted, and return the **new** count (TG-82).

        The count is durable, in the row, because a supervised restart carries nothing across (P-23)
        and an in-memory bound turns a human deleting a topic in anger into a recreation loop that
        survives exactly as long as the daemon's uptime. The caller checks ``recreations`` from
        :meth:`channel` **before** calling ``createForumTopic``, which is what makes TG-82's "no
        further ``createForumTopic`` is issued" true rather than approximately true; the returned
        count is the same fact read back after the write, for the caller that has to decide whether
        this repair was the last one.

        ``0`` means **no such channel** and is unambiguous: the increment guarantees every real
        answer is at least 1.

        The retirement flag is cleared for the same reason :meth:`open_channel` clears it — a
        channel that has just been given a live topic is not retired, whatever it was before.
        """
        cursor = await self._connection.execute(
            f"UPDATE {CHANNELS_TABLE} SET topic_id = ?, recreations = recreations + 1, retired = 0 "
            f"WHERE chat_id = ? AND agent_id = ? RETURNING recreations",
            (topic_id, chat_id, agent_id),
        )
        rows = list(await cursor.fetchall())
        await self._connection.commit()
        return int(rows[0][0]) if rows else 0

    async def retire_channel(self, chat_id: int, agent_id: str) -> None:
        """Give up on a channel past ``MAX_RECREATIONS``: its traffic goes to General (TG-82).

        Durable and permanent until a ``/channels`` command asks for a new one, because the human
        is told **once** and a flag that a restart clears is a notice they get again every time the
        daemon bounces — TG-13's exact lesson.

        The topic id is left on the row rather than nulled. It is dead and no send may carry it
        (TG-84), which :meth:`channels` enforces by omitting the row and :meth:`channel` by
        reporting ``retired``; what it is still good for is telling the human *which* topic the bot
        gave up on, on a system whose only surviving record of what happened is the chat itself.
        """
        await self._connection.execute(
            f"UPDATE {CHANNELS_TABLE} SET retired = 1 WHERE chat_id = ? AND agent_id = ?",
            (chat_id, agent_id),
        )
        await self._connection.commit()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
