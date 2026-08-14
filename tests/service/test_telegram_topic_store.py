"""The channel key, the topic registry and the upgrade of a pre-topics database (§9, TG-72 … TG-84).

Three questions, and every one of them is about a file that outlives the process that wrote it:

* **Is the key the channel, or the chat?** ``(chat_id, topic_id)`` with ``0`` for General
  (TG-72, decision Y). A missed key files a topic's message under the chat's General binding, so it
  is answered by the *previous* topic's expert — the mis-file TG-1 exists to prevent, now reachable
  with no configuration change at all. The sentinel is ``0`` rather than ``NULL`` for a reason this
  file demonstrates by building the nullable schema and watching SQLite accept two General rows for
  one chat: NULLs are *distinct* in a unique index, so nothing would ever notice.
* **Where is an agent's topic recorded?** In ``pkb_telegram_channels`` — because Telegram mints a
  topic id, shows it in no client, and **no API enumerates a chat's topics** (F-5). A directory held
  in memory is a set of topics that exist on the human's phone and are addressable by nothing.
* **What happens to a deployment that upgrades?** This is the headline. The shipped bindings table
  declares ``chat_id INTEGER PRIMARY KEY`` — a rowid alias, one row per chat forever — so the topic
  column could not be added beside it and the rows are carried into a new table instead. If that
  carry-over is wrong the human's next message after the upgrade starts a **brand-new conversation**
  while the old one still holds their pending approval: an amnesiac bot, with no error anywhere.

So the migration is driven end to end rather than asserted at the store: a real
:class:`~pkb.service.telegram.SqliteTelegramStore` over ``tmp_path`` opened the way the daemon opens
it (``isolation_level=None``, WAL — ST-1, ST-2), the **real** :class:`~pkb.server.telegram.
TelegramAdapter` on top of it, a fake ``BotApi`` with a topic model, and
:class:`~tests.server.stub.StubService`. No key, no socket, no model.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import sqlite3
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from pkb.contracts import ActionView, ApprovalRequest, MessageComplete, RunEnd
from pkb.server.telegram import Channel, TelegramAdapter, TelegramConfig, callback_data
from pkb.server.telegram_api import GENERAL, MAX_RECREATIONS, POLL_TIMEOUT, TelegramError
from pkb.service.telegram import (
    BINDINGS_TABLE,
    CHANNELS_TABLE,
    LEDGER_TABLE,
    LEGACY_BINDINGS_TABLE,
    PROMPTS_TABLE,
    SqliteTelegramStore,
)
from tests.server.stub import COOKING, GRILLING, LIBRARIAN, NOW, StubService

CHAT = 770101
"""The one mapped chat. Its General talks to :data:`LIBRARIAN` (TG-73)."""

OTHER_CHAT = 770102
OWNER = 987654321
"""Fictional, and deliberately so: this repository is public (house rule)."""

COOKING_TOPIC = 7
GRILLING_TOPIC = 9

LEGACY_THREAD = "t-before-the-upgrade"
"""The thread a deployment was in the middle of when it was upgraded."""

TOPIC_THREAD = "t-cooking-topic"
HANDLE = "9c1f2e3d"
INTERRUPT = "int-legacy-1"
LEGACY_UPDATE = 11
"""An update claimed by the pre-topics build and never dispatched — an orphan across the upgrade."""

LEGACY_UNFINISHED = 12
"""An update whose run was **started** and never finished — TG-31's re-sync input, across the same
upgrade. Distinct from an orphan in the way that matters: the agent ran and may already have
written, so it must be re-synced and never replayed."""

OMITTED = -1
"""What :class:`FakeBotApi` records when ``topic_id`` was not passed to a send at all.

A sentinel rather than :data:`GENERAL`, because the difference between the two is the whole of
TG-75's migration guarantee and ``0`` cannot express it: a build that sent ``message_thread_id: 0``
on a General message would satisfy every "the topic is General" assertion and ``400`` on every
message a real bot sent, and nothing in a fake that defaults to ``0`` could tell them apart.
"""


# --------------------------------------------------------------------------------------
# The pre-topics file, written exactly as the shipped build left it
# --------------------------------------------------------------------------------------

LEGACY_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {LEGACY_BINDINGS_TABLE} (
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
"""The schema of the shipped build, verbatim — the thing an upgrade actually finds on disk.

Copied rather than imported because the module no longer contains it: importing whatever the source
says today would make every migration test below tautological, asserting that a file written by the
new code can be read by the new code.
"""


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """The daemon's SQLite file, which lives *outside* ``kb_root`` (decision S, I3)."""
    return tmp_path / "pkb.sqlite"


@asynccontextmanager
async def connected(db_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """A connection with the two settings the daemon uses: autocommit and WAL (ST-1, ST-2)."""
    connection = await aiosqlite.connect(str(db_path), isolation_level=None)
    try:
        await connection.execute("PRAGMA journal_mode=WAL")
        yield connection
    finally:
        await connection.close()


@asynccontextmanager
async def opened(db_path: Path) -> AsyncIterator[SqliteTelegramStore]:
    """A store over its own connection — every re-entry over one path is a **restart**.

    ``_supervise`` carries nothing across, so a new connection, a new object and a second ``setup()``
    is exactly what the daemon does seconds after a crash.
    """
    async with connected(db_path) as connection:
        store = SqliteTelegramStore(connection)
        await store.setup()
        yield store


async def pre_topics_database(
    db_path: Path, *, thread_id: str = LEGACY_THREAD, agent_id: str = LIBRARIAN
) -> None:
    """A file the shipped build wrote: one binding, one orphaned ledger row, one open approval.

    All four, together, because the upgrade has to keep all four: the binding is the conversation,
    the orphaned ledger row is the message a crash lost and must still be *named* rather than
    replayed (decision T), the unfinished one is a run that already went and owes the chat only its
    outcome (TG-31), and the prompt is a keyboard sitting live in the chat that a human may press
    hours after the daemon was restarted into a new version.
    """
    async with connected(db_path) as connection:
        await connection.executescript(LEGACY_SCHEMA)
        await connection.execute(
            f"INSERT INTO {LEGACY_BINDINGS_TABLE} (chat_id, thread_id, agent_id, bound_at) "
            f"VALUES (?,?,?,?)",
            (CHAT, thread_id, agent_id, "2026-08-01T09:00:00Z"),
        )
        await connection.execute(
            f"INSERT INTO {LEDGER_TABLE} (update_id, chat_id, kind, seen_at, dispatched) "
            f"VALUES (?,?,?,?,0)",
            (LEGACY_UPDATE, CHAT, "message", "2026-08-01T09:01:00Z"),
        )
        await connection.execute(
            f"INSERT INTO {LEDGER_TABLE} "
            f"(update_id, chat_id, kind, seen_at, dispatched, thread_id, run_id) "
            f"VALUES (?,?,?,?,1,?,?)",
            (LEGACY_UNFINISHED, CHAT, "message", "2026-08-01T09:01:30Z", thread_id, "run-legacy"),
        )
        await connection.execute(
            f"INSERT INTO {PROMPTS_TABLE} (handle, chat_id, thread_id, interrupt_id, message_ids, "
            f"answers_json, action_count, created_at, resolved) VALUES (?,?,?,?,?,?,?,?,0)",
            (HANDLE, CHAT, thread_id, INTERRUPT, "[4001]", "{}", 1, "2026-08-01T09:02:00Z"),
        )


def catalog(db_path: Path) -> dict[str, str]:
    """``name → CREATE statement`` for everything SQLite did not add itself."""
    with sqlite3.connect(db_path) as raw:
        rows = raw.execute("SELECT name, sql FROM sqlite_master")
        return {str(r[0]): str(r[1]) for r in rows if not str(r[0]).startswith("sqlite_")}


def columns(db_path: Path, table: str) -> list[tuple[str, str]]:
    """``(name, declared type)`` in **declaration order** — which is what ``SELECT *`` means."""
    with sqlite3.connect(db_path) as raw:
        return [(str(row[1]), str(row[2])) for row in raw.execute(f"PRAGMA table_info({table})")]


def rows_of(db_path: Path, table: str, *columns_wanted: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(db_path) as raw:
        projection = ", ".join(columns_wanted) if columns_wanted else "*"
        return [tuple(row) for row in raw.execute(f"SELECT {projection} FROM {table}")]


# --------------------------------------------------------------------------------------
# A fake Bot API with a topic model (§9.7)
# --------------------------------------------------------------------------------------


@dataclass
class FakeBotApi:
    """Every call recorded, and topics modelled the way tdlib#854 says they behave.

    The one behaviour that cannot be guessed: a send into a **deleted** private-chat topic answers
    ``ok: true``, drops the parameter and delivers to General (F-2). So ``delete_topic`` makes
    nothing fail — it strips ``message_thread_id`` from the echoed ``Message``, which is the only
    evidence of the deletion that exists anywhere.
    """

    journal: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    next_message_id: int = 5000
    next_topic_id: int = 21
    has_topics_enabled: bool = False
    """Off by default (TG-75): the real bot answers ``false`` and the human may never flip it."""

    deleted: set[int] = field(default_factory=set)

    async def get_me(self) -> Mapping[str, Any]:
        self.journal.append(("get_me", {}))
        return {"id": 1, "username": "pkb_topic_bot", "has_topics_enabled": self.has_topics_enabled}

    async def get_updates(
        self, offset: int | None, *, timeout: int = POLL_TIMEOUT
    ) -> Sequence[Mapping[str, Any]]:
        await asyncio.sleep(0)
        self.journal.append(("get_updates", {"offset": offset, "timeout": timeout}))
        return []

    async def create_forum_topic(self, chat_id: int, name: str) -> Mapping[str, Any]:
        self.journal.append(("create_forum_topic", {"chat_id": chat_id, "name": name}))
        topic_id = self.next_topic_id
        self.next_topic_id += 1
        return {"message_thread_id": topic_id, "name": name}

    async def edit_forum_topic(self, chat_id: int, topic_id: int, name: str) -> None:
        """TG-105. Every fake implements every method on the Protocol, which is TG-78's own cost."""
        self.journal.append(
            ("edit_forum_topic", {"chat_id": chat_id, "topic_id": topic_id, "name": name})
        )

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: Sequence[Sequence[Mapping[str, str]]] | None = None,
        topic_id: int = OMITTED,
    ) -> Mapping[str, Any]:
        self.journal.append(
            ("send_message", {"chat_id": chat_id, "text": text, "kb": keyboard, "topic": topic_id})
        )
        return self._echo(chat_id, topic_id)

    async def send_document(
        self,
        chat_id: int,
        filename: str,
        content: bytes,
        caption: str = "",
        *,
        topic_id: int = OMITTED,
    ) -> Mapping[str, Any]:
        self.journal.append(
            ("send_document", {"chat_id": chat_id, "filename": filename, "topic": topic_id})
        )
        return self._echo(chat_id, topic_id)

    async def answer_callback(
        self, callback_id: str, text: str = "", *, show_alert: bool = False
    ) -> None:
        self.journal.append(("answer_callback", {"id": callback_id, "text": text}))

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        self.journal.append(("edit_message", {"chat_id": chat_id, "message_id": message_id}))

    async def clear_keyboard(self, chat_id: int, message_id: int) -> None:
        self.journal.append(("clear_keyboard", {"chat_id": chat_id, "message_id": message_id}))

    # -- the topic model --------------------------------------------------------------

    def _echo(self, chat_id: int, topic_id: int) -> Mapping[str, Any]:
        self.next_message_id += 1
        message: dict[str, Any] = {"message_id": self.next_message_id, "chat": {"id": chat_id}}
        if topic_id > GENERAL and topic_id not in self.deleted:
            message["message_thread_id"] = topic_id
        return message

    def delete_topic(self, topic_id: int) -> None:
        """The human deletes a topic. Nothing starts failing — that is the whole hazard (F-2)."""
        self.deleted.add(topic_id)

    # -- what the tests read ----------------------------------------------------------

    def of(self, name: str) -> list[dict[str, Any]]:
        return [entry for kind, entry in self.journal if kind == name]

    @property
    def sent(self) -> list[dict[str, Any]]:
        return self.of("send_message")

    @property
    def transcript(self) -> str:
        return "\n".join(str(entry["text"]) for entry in self.sent)


def reply_script(text: str = "Filed under Cooking.") -> list[Any]:
    return [
        MessageComplete(run_id="run-1", agent_id=COOKING, text=text),
        RunEnd(run_id="run-1", final_text=text),
    ]


def build(
    store: SqliteTelegramStore,
    api: FakeBotApi,
    *,
    service: StubService | None = None,
    chats: Mapping[int, str] | None = None,
) -> tuple[TelegramAdapter, StubService]:
    """The **real** adapter over the real store — nothing about the channel is stubbed."""
    scripted = service or StubService(events=reply_script())
    bot = TelegramAdapter(
        service=scripted,
        store=store,
        api=api,
        config=TelegramConfig(
            token="123456789:AA-fake-token-never-a-real-one",
            chats={CHAT: LIBRARIAN} if chats is None else chats,
            owner_user_ids=frozenset({OWNER}),
        ),
    )
    return bot, scripted


def message_update(
    update_id: int = 1,
    *,
    chat_id: int = CHAT,
    topic_id: int = GENERAL,
    text: str = "where does the steak note go?",
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": update_id,
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": OWNER},
        "text": text,
    }
    if topic_id:
        # F-1: an inbound message carries `message_thread_id` only inside a topic; General has none.
        message["message_thread_id"] = topic_id
    return {"update_id": update_id, "message": message}


async def drain(bot: TelegramAdapter) -> None:
    """Empty the outbox the way the task's own pump does (TG-49)."""
    pump = asyncio.create_task(bot._pump_outbox())
    try:
        for _ in range(200):
            if bot._outbox.empty():
                break
            await asyncio.sleep(0)
        await asyncio.sleep(0.01)
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump


async def deliver(bot: TelegramAdapter, update: Mapping[str, Any]) -> None:
    await bot._dispatch(update)
    await drain(bot)


async def with_topics(bot: TelegramAdapter, api: FakeBotApi) -> None:
    """Turn Threaded Mode on and let the adapter learn it the way it does at startup (TG-75)."""
    api.has_topics_enabled = True
    await bot._probe_topics()
    await bot._load_directory()


def approval(thread_id: str, *, agent_id: str = LIBRARIAN) -> ApprovalRequest:
    return ApprovalRequest(
        interrupt_id=INTERRUPT,
        agent_id=agent_id,
        thread_id=thread_id,
        actions=(
            ActionView(
                tool="write_file",
                args={"file_path": "topics/Cooking/notes/steak.md"},
                description="rest for 8 minutes",
                allowed_decisions=("approve", "reject"),
                reason="breadth-approval",
            ),
        ),
    )


# --------------------------------------------------------------------------------------
# The key is the channel, and General is 0 rather than NULL (TG-72, decision Y)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_nullable_topic_column_would_hold_two_general_rows_for_one_chat_tg72(
    db_path: Path, tmp_path: Path
) -> None:
    """Decision Y's argument, executed rather than believed: NULLs are **distinct** in an index.

    ``topic_id INTEGER NULL`` with ``UNIQUE (chat_id, topic_id)`` reads like the obvious way to say
    "General has no topic", and SQLite then accepts two General rows for one chat without a word.
    Two rows both claiming to be the chat's current conversation is a coin toss over row order for
    every message the human sends — and, because a binding carries an agent, a coin toss over which
    expert writes into a tree with no undo. The shipped schema is asserted beside it to show the
    same insert collapsing to one row.
    """
    nullable = tmp_path / "nullable.sqlite"
    with sqlite3.connect(nullable) as raw:
        raw.execute(
            "CREATE TABLE bindings (chat_id INTEGER NOT NULL, topic_id INTEGER, thread_id TEXT)"
        )
        raw.execute("CREATE UNIQUE INDEX bindings_idx ON bindings (chat_id, topic_id)")
        raw.execute("INSERT INTO bindings VALUES (?, NULL, ?)", (CHAT, "first"))
        raw.execute("INSERT INTO bindings VALUES (?, NULL, ?)", (CHAT, "second"))
        duplicated = raw.execute(
            "SELECT COUNT(*) FROM bindings WHERE chat_id = ?", (CHAT,)
        ).fetchone()[0]

    assert duplicated == 2, "the premise of decision Y is that SQLite allows exactly this"

    async with opened(db_path) as store:
        await store.bind(CHAT, GENERAL, "first", LIBRARIAN)
        await store.bind(CHAT, GENERAL, "second", LIBRARIAN)

        assert await store.bound_thread(CHAT, GENERAL) == "second"

    assert len(rows_of(db_path, BINDINGS_TABLE)) == 1


@pytest.mark.asyncio
async def test_two_topics_of_one_chat_hold_two_independent_conversations_tg72(
    db_path: Path,
) -> None:
    """A human sees a topic per expert and reasonably assumes a conversation per expert.

    One binding row per *chat* would put every expert on the phone behind one rotating thread, so a
    note typed in Cooking would continue — and be answered by — whatever Grilling was last saying.
    That is TG-1's mis-file with a user interface actively denying it, which is worse than the
    unmapped case because nothing on screen is wrong.
    """
    async with opened(db_path) as store:
        await store.bind(CHAT, GENERAL, "t-general", LIBRARIAN)
        await store.bind(CHAT, COOKING_TOPIC, "t-cooking", COOKING)
        await store.bind(CHAT, GRILLING_TOPIC, "t-grilling", GRILLING)

        assert await store.bound_thread(CHAT, GENERAL) == "t-general"
        assert await store.bound_thread(CHAT, COOKING_TOPIC) == "t-cooking"
        assert await store.bound_thread(CHAT, GRILLING_TOPIC) == "t-grilling"
        assert await store.binding(CHAT, COOKING_TOPIC) == ("t-cooking", COOKING)


@pytest.mark.asyncio
async def test_new_in_one_topic_leaves_every_other_channel_alone_tg27(db_path: Path) -> None:
    """``/new`` rotates the channel it was typed in, and the human said nothing about the others.

    A rotation nobody asked for is invisible: the next message simply starts a fresh conversation,
    the previous one keeps whatever approval was parked on it, and there is no notice anywhere. The
    scoping is asserted across a restart because the deletion has to be the *row's*, not an object's.
    """
    async with opened(db_path) as store:
        await store.bind(CHAT, GENERAL, "t-general", LIBRARIAN)
        await store.bind(CHAT, COOKING_TOPIC, "t-cooking", COOKING)

        await store.unbind(CHAT, GENERAL)

    async with opened(db_path) as restarted:
        assert await restarted.bound_thread(CHAT, GENERAL) is None
        assert await restarted.bound_thread(CHAT, COOKING_TOPIC) == "t-cooking"


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_the_ledger_and_the_prompt_row_carry_the_topic_beside_the_chat_tg29(
    db_path: Path,
) -> None:
    """ "I lost your message" has to reach the channel that lost it, not the right chat's wrong topic.

    A human told in General that a message was lost has no way to know which expert never heard it,
    and the only action the notice supports — re-send it — needs the conversation it belonged to. The
    same holds for a parked approval: ``/pending`` and TG-82's repair both need to know which channel
    a keyboard belongs to, and ``callback_data`` has 64 bytes and cannot carry it (TG-57).

    Superseded (Task 6 rebuilds this): the prompt-row half dies with the approval-prompt surface —
    the ledger's own "the topic travels beside the chat" half survives and keeps its own coverage in
    ``test_telegram_store.py``'s ledger tests, so nothing here needs a lone successor.
    """
    async with opened(db_path) as store:
        await store.claim(41, CHAT, COOKING_TOPIC, "message")
        await store.claim(42, CHAT, GENERAL, "message")
        await store.open_prompt(HANDLE, CHAT, COOKING_TOPIC, TOPIC_THREAD, INTERRUPT, 1)

    async with opened(db_path) as restarted:
        assert await restarted.orphans() == [(41, CHAT, COOKING_TOPIC), (42, CHAT, GENERAL)]
        row = await restarted.prompt(HANDLE)
        assert row is not None
        assert row["topic_id"] == COOKING_TOPIC


@pytest.mark.asyncio
async def test_no_store_method_that_takes_a_chat_gives_the_topic_a_default_tg72(
    db_path: Path,
) -> None:
    """A default here is the one mistake nothing downstream can catch.

    ``bound_thread(chat_id)`` with ``topic_id: int = GENERAL`` compiles, type-checks and reviews
    clean, and it files a topic's message under the chat's General binding — answered by the
    previous topic's expert, invisible in a diff and invisible on the phone. Every call site saying
    which channel it means costs one token and is the only place this is catchable.
    """
    offenders = []
    for name, method in inspect.getmembers(SqliteTelegramStore, inspect.isfunction):
        if name.startswith("_"):
            continue
        parameters = inspect.signature(method).parameters
        takes_a_channel = "chat_id" in parameters and "topic_id" in parameters
        if takes_a_channel and parameters["topic_id"].default is not inspect.Parameter.empty:
            offenders.append(name)

    assert offenders == []


# --------------------------------------------------------------------------------------
# The topic registry (TG-77, TG-79, TG-82, TG-84, TG-11)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_topic_may_not_address_two_agents_tg77(db_path: Path) -> None:
    """The store refuses rather than silently re-pointing a conversation.

    Last-write-wins on the topic would make the human's Cooking history start answering as Baking,
    with every message above the change still in the same topic and still attributed to Cooking by
    the title above it. TG-87 has the caller check the topic is unbound first; a caller that did not
    has a bug, and a bug that resolves itself into a mis-file is the one this whole layer is about.
    """
    async with opened(db_path) as store:
        await store.open_channel(CHAT, COOKING_TOPIC, COOKING)

        with pytest.raises(sqlite3.IntegrityError):
            await store.open_channel(CHAT, COOKING_TOPIC, GRILLING)

        assert dict(await store.channels(CHAT)) == {COOKING_TOPIC: COOKING}


@pytest.mark.asyncio
async def test_one_agent_gets_one_channel_per_chat_tg77(db_path: Path) -> None:
    """Two channels for one agent in one chat is that expert's history split in half, invisibly.

    Nothing on screen would say which half a message went to, and both topics would carry the same
    title. So a second ``/channels`` for an agent that already has one creates nothing — asserted
    here at the row level, because the adapter's "creates nothing" branch is only correct if the
    directory cannot hold the second row in the first place.
    """
    async with opened(db_path) as store:
        await store.open_channel(CHAT, COOKING_TOPIC, COOKING)
        await store.open_channel(OTHER_CHAT, GRILLING_TOPIC, COOKING)

        assert dict(await store.channels(CHAT)) == {COOKING_TOPIC: COOKING}
        assert dict(await store.channels(OTHER_CHAT)) == {GRILLING_TOPIC: COOKING}
        assert len(rows_of(db_path, CHANNELS_TABLE)) == 2


@pytest.mark.asyncio
async def test_general_is_never_a_directory_row_tg73(db_path: Path) -> None:
    """General's agent is configuration, and a row at 0 would be a second answer to one question.

    TG-1 promises a channel has exactly one agent. ``config.chats[chat_id]`` is the human's decision
    and the bot never writes it (TG-17); a directory row at :data:`GENERAL` would be the bot holding
    an opinion about it, and the two could then disagree with nothing to arbitrate.
    """
    async with opened(db_path) as store:
        await store.open_channel(CHAT, COOKING_TOPIC, COOKING)

        assert GENERAL not in await store.channels(CHAT)
        assert await store.channel(CHAT, LIBRARIAN) is None


@pytest.mark.asyncio
async def test_the_recreation_count_outlives_the_daemon_tg82(db_path: Path) -> None:
    """An in-memory bound gives a human deleting a topic in anger a fresh allowance every bounce.

    ``_supervise`` carries nothing across, so a count in the task resets on the restart that a
    deleted topic tends to be near. Durable, the third deletion is refused — which is what makes
    TG-82's "no further ``createForumTopic`` is issued" true rather than approximately true.
    """
    async with opened(db_path) as store:
        await store.open_channel(CHAT, COOKING_TOPIC, COOKING)

        assert await store.rebind_channel(CHAT, COOKING, 31) == 1

    async with opened(db_path) as restarted:
        row = await restarted.channel(CHAT, COOKING)
        assert row is not None
        assert row["recreations"] == 1
        assert row["topic_id"] == 31

        assert await restarted.rebind_channel(CHAT, COOKING, 32) == MAX_RECREATIONS


@pytest.mark.asyncio
async def test_rebinding_a_channel_that_does_not_exist_counts_nothing_tg82(db_path: Path) -> None:
    """``0`` has to mean "no such channel" unambiguously, because the increment makes every real
    answer at least 1.

    The caller uses this to tell "repaired, and that was its second chance" from "there is no
    directory row here at all" — the case where a channel came from General's mapping or from a lost
    SQLite file. Conflating them either retires a channel that was never counted or leaves an
    unbounded repair loop.
    """
    async with opened(db_path) as store:
        assert await store.rebind_channel(CHAT, COOKING, 31) == 0
        assert await store.channel(CHAT, COOKING) is None


@pytest.mark.asyncio
async def test_a_retired_channel_leaves_the_routing_table_but_not_the_directory_tg84(
    db_path: Path,
) -> None:
    """A dead topic id must be unreachable to routing and still legible to the human.

    ``channels`` is what an inbound message and a listing read, so a retired row there is a stale id
    handed straight back to a send (TG-84). ``channel`` is the one reader that has to tell "no
    channel" from "a channel we gave up on", because those produce different messages and different
    repairs — and the row keeps its topic id so the human can be told *which* topic was abandoned,
    on a system whose only surviving record is the chat itself.
    """
    async with opened(db_path) as store:
        await store.open_channel(CHAT, COOKING_TOPIC, COOKING)
        await store.retire_channel(CHAT, COOKING)

    async with opened(db_path) as restarted:
        assert dict(await restarted.channels(CHAT)) == {}
        row = await restarted.channel(CHAT, COOKING)
        assert row is not None
        assert row["retired"] is True
        assert row["topic_id"] == COOKING_TOPIC
        assert await restarted.retired_agents() == frozenset({COOKING})
        # TG-11: still reachable, in General with its name on the first line — so reporting it as
        # unmapped would be false.
        assert await restarted.channel_agents() == frozenset({COOKING})


@pytest.mark.asyncio
async def test_asking_for_a_retired_channel_again_gives_it_a_fresh_allowance_tg87(
    db_path: Path,
) -> None:
    """A human typing ``/channels`` for a retired agent has conceded the fight and wants it working.

    Carrying the old count forward would retire the new topic after one more deletion — for a
    disagreement the human just ended — and leave them with a command that appears to do nothing.
    """
    async with opened(db_path) as store:
        await store.open_channel(CHAT, COOKING_TOPIC, COOKING)
        await store.rebind_channel(CHAT, COOKING, 31)
        await store.rebind_channel(CHAT, COOKING, 32)
        await store.retire_channel(CHAT, COOKING)

        await store.open_channel(CHAT, 33, COOKING)

        row = await store.channel(CHAT, COOKING)
        assert row is not None
        assert (row["recreations"], row["retired"], row["topic_id"]) == (0, False, 33)
        assert dict(await store.channels(CHAT)) == {33: COOKING}


@pytest.mark.asyncio
async def test_the_directory_answers_health_while_the_bot_is_crash_looping_tg11(
    db_path: Path,
) -> None:
    """``/health`` is read exactly when the adapter is not running, so the answer comes off the disk.

    The daemon seeds ``telegram.agents`` from the store at composition time — before the task starts
    and while it may be restarting — because TG-11's whole stated property is that "which agents can
    a human reach from their phone" survives a bot that cannot start. Reading it from the live task
    makes the answer *"none"* precisely when somebody is asking why.
    """
    async with opened(db_path) as store:
        await store.open_channel(CHAT, COOKING_TOPIC, COOKING)
        await store.open_channel(OTHER_CHAT, GRILLING_TOPIC, GRILLING)
        await store.retire_channel(OTHER_CHAT, GRILLING)

    async with connected(db_path) as connection:
        # Deliberately *not* through `opened`: this is the daemon's own read, made by an object that
        # is not the adapter and never polls.
        seeded = SqliteTelegramStore(connection)
        await seeded.setup()

        assert await seeded.channel_agents() == frozenset({COOKING, GRILLING})
        assert await seeded.retired_agents() == frozenset({GRILLING})


@pytest.mark.asyncio
async def test_a_registered_topic_routes_its_messages_to_its_own_expert_tg72(
    db_path: Path,
) -> None:
    """The registry's whole purpose, driven through the real adapter rather than asserted at the row.

    Two messages in two topics of one chat produce two ``create_thread`` calls for two different
    agents, and each reply goes back into the topic it came from. A directory read that fell back to
    the chat's General agent would pass every store-level test above and still file the human's
    grilling note under the Librarian.
    """
    api = FakeBotApi()
    async with opened(db_path) as store:
        bot, service = build(store, api)
        await with_topics(bot, api)
        await store.open_channel(CHAT, COOKING_TOPIC, COOKING)
        await store.open_channel(CHAT, GRILLING_TOPIC, GRILLING)

        await deliver(bot, message_update(1, topic_id=COOKING_TOPIC))
        await deliver(bot, message_update(2, topic_id=GRILLING_TOPIC))

        created = [args[0] for name, args in service.calls if name == "create_thread"]
        assert created == [COOKING, GRILLING]
        assert await store.binding(CHAT, COOKING_TOPIC) is not None
        assert await store.binding(CHAT, GRILLING_TOPIC) is not None
        assert (await store.binding(CHAT, COOKING_TOPIC)) != (
            await store.binding(CHAT, GRILLING_TOPIC)
        )

    topics = {entry["topic"] for entry in api.sent}
    assert topics == {COOKING_TOPIC, GRILLING_TOPIC}


@pytest.mark.asyncio
async def test_a_topic_message_and_a_general_message_never_share_a_thread_tg73(
    db_path: Path,
) -> None:
    """General is a mapped channel like any other, and its agent is not the topic's.

    The failure this rules out is the quiet one: a chat whose General talks to the Librarian and
    whose one topic talks to Cooking, sharing a thread, would route the human's General question
    into the Cooking conversation and answer it with Cooking's context — with the topic header above
    it saying otherwise.
    """
    api = FakeBotApi()
    async with opened(db_path) as store:
        bot, service = build(store, api)
        await with_topics(bot, api)
        await store.open_channel(CHAT, COOKING_TOPIC, COOKING)

        await deliver(bot, message_update(1, topic_id=GENERAL))
        await deliver(bot, message_update(2, topic_id=COOKING_TOPIC))

        created = [args[0] for name, args in service.calls if name == "create_thread"]
        assert created == [LIBRARIAN, COOKING]
        assert await store.bound_thread(CHAT, GENERAL) != await store.bound_thread(
            CHAT, COOKING_TOPIC
        )


@pytest.mark.asyncio
async def test_a_retirement_in_one_chat_does_not_silence_the_same_expert_in_another_tg82(
    db_path: Path,
) -> None:
    """Two chats may address one agent deliberately (TG-25); retirement is a **channel's** state.

    ``retired_agents()`` answers with bare agent ids and no chat, which is right for ``/health``
    (TG-11 asks "which experts are in that state", not "where") and wrong as a routing input. Pair
    every id in it with every configured chat and a topic deleted three times on the phone retires
    that expert's live, untouched channel on the laptop: its replies silently move to General with
    a prefix, nothing was deleted there, and no command the human can type says why. The seeding has
    to go back through ``channel(chat_id, agent_id)``, which is the only reader that knows both.
    """
    api = FakeBotApi()
    async with opened(db_path) as store:
        await store.open_channel(CHAT, COOKING_TOPIC, COOKING)
        await store.open_channel(OTHER_CHAT, GRILLING_TOPIC, COOKING)
        await store.retire_channel(CHAT, COOKING)

        bot, _service = build(store, api, chats={CHAT: LIBRARIAN, OTHER_CHAT: LIBRARIAN})
        await with_topics(bot, api)

        assert (OTHER_CHAT, COOKING) not in bot._retired
        assert bot._route_out(Channel(OTHER_CHAT, GRILLING_TOPIC), COOKING) == Channel(
            OTHER_CHAT, GRILLING_TOPIC
        )


# --------------------------------------------------------------------------------------
# The migration — a database written by the previous schema (TG-28)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_a_pre_topics_database_keeps_its_binding_its_ledger_and_its_approval_tg28(
    db_path: Path,
) -> None:
    """The upgrade's whole promise, in one assertion per thing a deployment cannot afford to lose.

    The binding is the conversation the human is in the middle of; the orphaned ledger row is the
    message a crash lost, which must still be *named* rather than replayed into a tree with no undo;
    the prompt is a keyboard sitting live in the chat that a human may press hours later, and its
    ``message_ids`` are the only way those buttons are ever taken off. Every one of them becomes a
    **General** row, because a chat that had no topics had exactly one channel and this is it.

    Superseded (Task 6 rebuilds this): the approval leg (the ``prompt`` row and its fields) has no
    successor once the approval-prompt table is gone; the binding-survives-an-upgrade and the
    ledger-survives-an-upgrade legs are real migration-durability properties that need a rebuild
    without it, not a deletion.
    """
    await pre_topics_database(db_path)

    async with opened(db_path) as store:
        assert await store.bound_thread(CHAT, GENERAL) == LEGACY_THREAD
        assert await store.binding(CHAT, GENERAL) == (LEGACY_THREAD, LIBRARIAN)
        assert await store.orphans() == [(LEGACY_UPDATE, CHAT, GENERAL)]
        assert await store.unfinished() == [(LEGACY_UNFINISHED, CHAT, GENERAL, LEGACY_THREAD)]
        assert await store.next_offset() == LEGACY_UNFINISHED + 1

        row = await store.prompt(HANDLE)
        assert row is not None
        assert row["thread_id"] == LEGACY_THREAD
        assert row["interrupt_id"] == INTERRUPT
        assert row["message_ids"] == [4001]
        assert row["topic_id"] == GENERAL
        assert row["resolved"] is False


@pytest.mark.asyncio
async def test_an_upgraded_deployment_resumes_its_conversation_rather_than_starting_a_new_one_tg26(
    db_path: Path,
) -> None:
    """The amnesiac bot, produced by an upgrade instead of by a crash — and it has no error anywhere.

    If the carry-over is missing the human's next message after the upgrade looks completely normal:
    a reply arrives, the chat scrolls on, and a brand-new thread has been created behind them while
    the old one still holds their pending approval. Driven through the **real** adapter because that
    is the only place "resumes" is observable: zero ``create_thread`` calls and a ``start_run`` on
    the thread the previous version bound.
    """
    await pre_topics_database(db_path)
    api = FakeBotApi()

    async with opened(db_path) as store:
        bot, service = build(store, api)
        service.rows[LEGACY_THREAD] = await _row(service, LEGACY_THREAD)

        await deliver(bot, message_update(50))

        assert [name for name, _ in service.calls if name == "create_thread"] == []
        assert [args[0] for name, args in service.calls if name == "start_run"] == [LEGACY_THREAD]
        assert await store.bound_thread(CHAT, GENERAL) == LEGACY_THREAD


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_a_button_from_before_the_upgrade_still_answers_its_approval_tg58(
    db_path: Path,
) -> None:
    """Telegram redelivers an unconfirmed press for 24 hours, and an upgrade fits inside that window.

    The keyboard was posted by the previous version and is still live in the chat. If the prompt row
    did not survive the migration the press is answered *"that approval could not be located"* on an
    approval that is perfectly live — and because the row is the only index a press carries
    (``callback_data`` holds 64 bytes, TG-57), there is nothing else to find it by. The row's new
    ``topic_id`` defaulting to General is what puts the reply back where the keyboard is.

    Superseded (Task 6 rebuilds this): there is no keyboard left over from a previous version to
    answer, because there is no approval-prompt surface left to post one.
    """
    await pre_topics_database(db_path)
    api = FakeBotApi()

    async with opened(db_path) as store:
        bot, service = build(store, api)
        service.rows[LEGACY_THREAD] = await _row(service, LEGACY_THREAD)
        service.pending = approval(LEGACY_THREAD)

        await deliver(
            bot,
            {
                "update_id": 60,
                "callback_query": {
                    "id": "cbq-1",
                    "from": {"id": OWNER},
                    "data": callback_data(HANDLE, 0, "a"),
                    "message": {"message_id": 4001, "chat": {"id": CHAT, "type": "private"}},
                },
            },
        )

        assert [args[0] for name, args in service.calls if name == "resume"] == [LEGACY_THREAD]
        row = await store.prompt(HANDLE)
        assert row is not None
        assert row["resolved"] is True

    # TG-63: the message the previous version posted loses its buttons, addressed by chat and
    # message id with no topic at all (TG-90, F-6).
    assert api.of("clear_keyboard") == [{"chat_id": CHAT, "message_id": 4001}]


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_the_migration_adds_columns_and_rebuilds_nothing_tg28(db_path: Path) -> None:
    """ST-3 measured what a long transaction on this connection costs, so the upgrade is three
    short statements and never a table rewrite.

    A rebuild is the standard way to add a column to a table with the wrong primary key, and it is
    the one operation TG-28 forbids by name: it runs at startup, on the connection the checkpointer
    is using, while the daemon is doing everything else — and the victim of the lock it takes is an
    agent run, which surfaces as a failed turn with a written file and no flush.

    Proven **physically**, by rowid, because every structural fingerprint of a rebuild can be
    forged: the schema text of a rebuilt table is the same text the ``ADD COLUMN`` produces, and the
    columns are identical by construction. Rowids are not. ``pkb_telegram_prompts`` keys on
    ``handle``, so its rows carry implicit rowids with gaps wherever an approval was resolved and
    cleaned up, and ``INSERT INTO new SELECT … FROM old`` renumbers them from 1. The gap surviving
    is the one thing only an in-place alter can do.

    Superseded (Task 6 rebuilds this): the proof vehicle *is* ``PROMPTS_TABLE`` — its rowid-gap
    shape is the whole reason this table rather than another was chosen — and it is deleted with the
    approval-prompt surface. The no-rebuild-on-``ADD COLUMN`` principle is permanent and needs a
    successor proven against a table Task 6 leaves standing, such as the ledger.
    """
    await pre_topics_database(db_path)
    async with connected(db_path) as connection:
        for handle in ("aaaa", "bbbb"):
            await connection.execute(
                f"INSERT INTO {PROMPTS_TABLE} (handle, chat_id, thread_id, interrupt_id, "
                f"message_ids, answers_json, action_count, created_at, resolved) "
                f"VALUES (?,?,?,?,?,?,?,?,0)",
                (handle, CHAT, LEGACY_THREAD, "int-x", "[]", "{}", 1, "2026-08-01T09:03:00Z"),
            )
        await connection.execute(f"DELETE FROM {PROMPTS_TABLE} WHERE handle = 'aaaa'")

    before = catalog(db_path)
    with sqlite3.connect(db_path) as raw:
        rowids_before = [r[0] for r in raw.execute(f"SELECT rowid FROM {PROMPTS_TABLE}")]

    async with opened(db_path):
        pass

    after = catalog(db_path)
    with sqlite3.connect(db_path) as raw:
        rowids_after = [r[0] for r in raw.execute(f"SELECT rowid FROM {PROMPTS_TABLE}")]

    assert rowids_before == [1, 3], "the fixture has to leave a gap, or this asserts nothing"
    assert rowids_after == rowids_before
    assert after[LEDGER_TABLE] != before[LEDGER_TABLE], "the topic column has to be there"
    assert LEGACY_BINDINGS_TABLE in after
    assert set(after) - set(before) == {
        BINDINGS_TABLE,
        CHANNELS_TABLE,
        f"{CHANNELS_TABLE}_topic_idx",
    }


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_an_upgraded_file_and_a_fresh_one_have_the_same_columns_in_the_same_order_tg28(
    db_path: Path, tmp_path: Path
) -> None:
    """``SELECT *`` must mean one thing regardless of when the deployment was installed.

    ``ADD COLUMN`` appends, so ``topic_id`` lands last on an upgraded file; a fresh file gets
    whatever order the schema literal declares. Let those disagree and every positional read is
    correct on one half of the deployments and silently transposed on the other — a chat id read as
    a topic id, which is a message filed by the wrong expert.

    Superseded (Task 6 rebuilds this): the loop below checks ``PROMPTS_TABLE`` beside
    ``LEDGER_TABLE`` and cannot be split without editing it — the ledger's half of "same columns,
    same order" survives and needs a rebuild that drops the deleted table from the loop.
    """
    await pre_topics_database(db_path)
    async with opened(db_path):
        pass

    fresh = tmp_path / "fresh.sqlite"
    async with opened(fresh):
        pass

    for table in (LEDGER_TABLE, PROMPTS_TABLE):
        assert columns(db_path, table) == columns(fresh, table)
        assert columns(fresh, table)[-1][0] == "topic_id"


@pytest.mark.asyncio
async def test_the_pre_topics_table_is_left_standing_and_never_written_again_tg28(
    db_path: Path,
) -> None:
    """It is the only surviving record of what the deployment looked like before, and there is no
    undo (D6).

    Two records of one fact can disagree, so after the carry-over the old table is read-only: a
    ``/new`` and a re-bind on the new table must leave every one of its original columns untouched.
    A migration that kept writing both would give a later reader — a human with ``sqlite3``, or a
    recovery path — two different answers to "which thread was this chat on".
    """
    await pre_topics_database(db_path)
    async with opened(db_path) as store:
        await store.unbind(CHAT, GENERAL)
        await store.bind(CHAT, GENERAL, "a-completely-different-thread", COOKING)
        await store.bind(CHAT, COOKING_TOPIC, TOPIC_THREAD, COOKING)

    assert rows_of(db_path, LEGACY_BINDINGS_TABLE, "chat_id", "thread_id", "agent_id") == [
        (CHAT, LEGACY_THREAD, LIBRARIAN)
    ]


@pytest.mark.asyncio
async def test_a_rotated_thread_is_not_resurrected_by_the_next_restart_tg28(db_path: Path) -> None:
    """``INSERT OR IGNORE`` alone cannot see a row the human deliberately deleted.

    The sequence is ordinary: upgrade, type ``/new`` to start a fresh conversation, and let the
    daemon bounce — which it does on any exception. The carry-over then finds nothing to conflict
    with, re-inserts the pre-upgrade binding, and the human is silently returned to a conversation
    they left, holding an approval the new one knows nothing about. Nothing logs it and nothing on
    the phone changes.
    """
    await pre_topics_database(db_path)

    async with opened(db_path) as store:
        assert await store.bound_thread(CHAT, GENERAL) == LEGACY_THREAD
        await store.unbind(CHAT, GENERAL)

    async with opened(db_path) as restarted:
        assert await restarted.bound_thread(CHAT, GENERAL) is None


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_setup_on_an_upgraded_file_is_safe_to_run_on_every_start_tg28(db_path: Path) -> None:
    """SQLite has no ``ADD COLUMN IF NOT EXISTS``, and a second attempt is a hard error.

    That error would land on the daemon's *second* boot rather than its first — the worst possible
    place to learn about it, because the deployment looked healthy for a whole session and now
    crash-loops with a schema error nobody changed anything to cause.

    Superseded (Task 6 rebuilds this): the last assertion reads back a surviving prompt row, which
    has no successor once the approval-prompt table is gone; the binding half of "setup is safe to
    repeat" survives and needs a rebuild without it.
    """
    await pre_topics_database(db_path)

    async with opened(db_path) as store:
        await store.setup()
        await store.setup()

        assert await store.bound_thread(CHAT, GENERAL) == LEGACY_THREAD
        assert await store.prompt(HANDLE) is not None


@pytest.mark.asyncio
async def test_a_fresh_installation_is_not_handed_the_pre_topics_table_tg28(db_path: Path) -> None:
    """The migration must not *create* the thing it migrates from.

    ``PRAGMA table_info`` on a missing table returns zero rows rather than raising, which is
    indistinguishable from a table with no columns — so the obvious existence check silently invents
    a legacy table on every new deployment, and every one of them then carries a ``migrated_at``
    stamp for an upgrade that never happened.
    """
    async with opened(db_path) as store:
        await store.bind(CHAT, GENERAL, "t-fresh", LIBRARIAN)

    assert LEGACY_BINDINGS_TABLE not in catalog(db_path)


@pytest.mark.asyncio
async def test_the_upgrade_never_shares_one_binding_across_a_chats_topics_tg1(
    db_path: Path,
) -> None:
    """The reason the bindings could not be migrated in place, stated as an assertion.

    ``chat_id INTEGER PRIMARY KEY`` is a rowid alias: one row per chat, forever, whatever column is
    added beside it. Had the topic gone onto that table, an upgraded deployment would share a single
    binding across every topic in the chat — the human sees a topic per expert and gets one rotating
    conversation behind them. This asserts the outcome rather than the mechanism, so a future
    migration that reaches it another way still passes.
    """
    await pre_topics_database(db_path)

    async with opened(db_path) as store:
        await store.bind(CHAT, COOKING_TOPIC, TOPIC_THREAD, COOKING)

        assert await store.bound_thread(CHAT, GENERAL) == LEGACY_THREAD
        assert await store.bound_thread(CHAT, COOKING_TOPIC) == TOPIC_THREAD


@pytest.mark.asyncio
async def test_an_upgraded_deployment_that_never_turns_topics_on_sends_no_thread_id_tg75(
    db_path: Path,
) -> None:
    """The migration guarantee: the human may never flip BotFather's Threaded Mode, and must not
    have to.

    ``has_topics_enabled`` is off by default and the real bot answers ``false`` today. With it off
    this adapter has to be byte-identical to the pre-topics build on the wire — a deployment that
    upgrades and finds its bot broken by a feature it did not ask for is the worst outcome of an
    additive change. Asserted on the *payload*, because ``topic_id=0`` is a Python default that must
    never reach the request.
    """
    await pre_topics_database(db_path)
    api = FakeBotApi()

    async with opened(db_path) as store:
        bot, service = build(store, api)
        await bot._probe_topics()
        service.rows[LEGACY_THREAD] = await _row(service, LEGACY_THREAD)

        assert bot._topics is False
        await deliver(bot, message_update(50))
        # Even a message that arrived *inside* a topic is answered in General while the toggle is
        # off: the bot cannot address a topic it has no permission to use (TG-75, TG-84).
        await deliver(bot, message_update(51, topic_id=COOKING_TOPIC))

    assert api.sent, "the adapter must have replied at all, or this asserts nothing"
    assert {entry["topic"] for entry in api.sent} == {OMITTED}


@pytest.mark.asyncio
async def test_a_general_reply_omits_the_topic_parameter_even_with_topics_on_tg75(
    db_path: Path,
) -> None:
    """``0`` is the **key**, not the wire: General is the absence of ``message_thread_id``.

    The sentinel is a database and dict decision (decision Y) and it must not escape the process.
    Telegram mints topic ids from the message-id sequence, so ``message_thread_id: 0`` names a
    message that cannot exist and the send is refused — every General message in the deployment,
    the moment the human turns Threaded Mode on, which is the one change §9 promised was additive.
    A naive reading of TG-75 ("General carries 0") passes without this and 400s in production.
    """
    api = FakeBotApi()
    async with opened(db_path) as store:
        bot, service = build(store, api)
        await with_topics(bot, api)
        await store.open_channel(CHAT, COOKING_TOPIC, COOKING)

        await deliver(bot, message_update(1, topic_id=GENERAL))
        await deliver(bot, message_update(2, topic_id=COOKING_TOPIC))

    assert service.calls, "both messages must have run, or the sends below prove nothing"
    general = [entry["topic"] for entry in api.sent if entry["topic"] != COOKING_TOPIC]
    assert general, "the General message must have been answered"
    assert set(general) == {OMITTED}, "a General send passes no topic at all — not 0, not null"


async def _row(service: StubService, thread_id: str, agent_id: str = LIBRARIAN) -> Any:
    """A thread row the stub will hand back — the shape ``create_thread`` would have made."""
    from pkb.service import Thread

    return Thread(
        thread_id=thread_id,
        agent_id=agent_id,
        created_at=NOW,
        updated_at=NOW,
        origin_channel="telegram",
        title=None,
    )


# --------------------------------------------------------------------------------------
# ST-7 and ST-1 — the file the bot shares with everything else
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_channel_directory_takes_the_reserved_prefix_st7(db_path: Path) -> None:
    """This file also holds ``threads``, ``checkpoints`` and ``writes`` — the runs' memory.

    ST-7 reserves ``pkb_`` for Layer 3 precisely so one file can be shared, and the cost of an
    unprefixed name is not a failed test but a corrupted graph. The new table and its index are
    named here so a rename has to pass through this assertion.
    """
    async with opened(db_path):
        pass

    names = set(catalog(db_path))
    assert {BINDINGS_TABLE, CHANNELS_TABLE, f"{CHANNELS_TABLE}_topic_idx"} <= names
    assert all(name.startswith("pkb_telegram_") for name in names)


@pytest.mark.asyncio
async def test_the_directory_never_locks_the_file_under_concurrent_channels_st3(
    db_path: Path,
) -> None:
    """A ``/channels all`` writes one row per agent while a run is streaming into the same file.

    ST-3 measured a handler holding a transaction across an ``await`` killing a concurrent
    checkpointer run after the victim's own five-second timeout — surfacing as a failed agent run,
    not as a Telegram error. Every statement in the directory is a single short autocommit write,
    which is what makes this load boring.
    """
    async with opened(db_path) as store:
        await asyncio.gather(
            *(store.open_channel(CHAT, 100 + n, f"topic/agent-{n}") for n in range(25))
        )

        assert len(await store.channels(CHAT)) == 25
        assert len(await store.channel_agents()) == 25


@pytest.mark.asyncio
async def test_the_topic_columns_never_reach_the_knowledge_base_i3(tmp_path: Path) -> None:
    """Layer 5 writes nothing under ``kb_root``, and the database is deliberately beside the tree.

    The structural half of this is stronger — no telegram module imports ``os``, ``shutil`` or
    ``tempfile`` — but that is about imports, and this is about the tree: a whole channel session,
    including the two writes §9 added, has to leave every file and every mtime alone.
    """
    kb_root = tmp_path / "kb"
    (kb_root / "topics" / "Cooking").mkdir(parents=True)
    (kb_root / "topics" / "Cooking" / "steak.md").write_text("existing\n", encoding="utf-8")
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(kb_root.rglob("*"))
        if path.is_file()
    }

    async with opened(tmp_path / "pkb.sqlite") as store:
        await store.open_channel(CHAT, COOKING_TOPIC, COOKING)
        await store.bind(CHAT, COOKING_TOPIC, TOPIC_THREAD, COOKING)
        await store.claim(1, CHAT, COOKING_TOPIC, "message")
        await store.open_prompt(HANDLE, CHAT, COOKING_TOPIC, TOPIC_THREAD, INTERRUPT, 1)
        await store.rebind_channel(CHAT, COOKING, 31)
        await store.retire_channel(CHAT, COOKING)

    after = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(kb_root.rglob("*"))
        if path.is_file()
    }
    assert after == before
    assert (tmp_path / "pkb.sqlite").is_file(), "the database is beside the tree, never inside it"


@pytest.mark.asyncio
async def test_a_transactional_connection_is_still_refused_after_the_migration_landed_tg28(
    db_path: Path,
) -> None:
    """The precondition guards the migration too, and the migration is where it matters most.

    ``_migrate`` runs at startup, on the connection the checkpointer shares, and it is the longest
    sequence of statements this store ever issues. Handed a default aiosqlite connection those
    statements would sit inside one implicit transaction held across several awaits — the exact
    shape ST-3 measured killing an agent run.
    """
    await pre_topics_database(db_path)
    connection = await aiosqlite.connect(db_path)  # the default: deferred transactions
    try:
        with pytest.raises(ValueError) as caught:
            await SqliteTelegramStore(connection).setup()
    finally:
        await connection.close()

    assert "isolation_level=None" in str(caught.value)
    # Nothing was migrated on the way to the refusal: a half-upgraded file is worse than none.
    assert LEDGER_TABLE in catalog(db_path)
    assert [name for name, _ in columns(db_path, LEDGER_TABLE)] == [
        "update_id",
        "chat_id",
        "kind",
        "seen_at",
        "dispatched",
        "thread_id",
        "run_id",
    ]


def test_the_store_still_opens_no_transaction_it_has_to_hold_tg28() -> None:
    """``BEGIN`` appears nowhere, including in the three statements the migration added.

    Asserted over the source rather than by behaviour because the failure is a *timing* one: with
    autocommit the same code is correct, and the day somebody wraps the carry-over and its stamp in
    a transaction to make them atomic, every test in this file still passes while a concurrent
    checkpointer write starts failing five seconds later in another layer.
    """
    import ast

    import pkb.service.telegram as store_module

    source = Path(store_module.__file__).read_text(encoding="utf-8")
    literals = [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert any("INSERT" in text.upper() for text in literals), "the walk found no SQL; it is broken"
    for text in literals:
        assert "BEGIN " not in text.upper(), text


@pytest.mark.asyncio
async def test_a_send_that_lands_in_general_is_read_off_the_response_not_off_an_error_tg80(
    db_path: Path,
) -> None:
    """The fixture's own premise, asserted so a broken fake cannot make the hazard tests vacuous.

    A deleted private-chat topic makes **nothing** fail: the send answers ``ok: true``, the
    parameter is dropped and the message lands in General (F-2, tdlib#854). Every test that drives
    TG-80 through this fake is worthless if the fake raises instead, so the shape is pinned here —
    and it is pinned as a fact about ``message_thread_id`` being *absent*, which is what makes
    ``None != 0`` the wrong comparison and ``landed_topic_id`` the right one.
    """
    api = FakeBotApi(has_topics_enabled=True)
    live = await api.send_message(CHAT, "into a live topic", topic_id=COOKING_TOPIC)
    api.delete_topic(COOKING_TOPIC)
    stray = await api.send_message(CHAT, "into a deleted topic", topic_id=COOKING_TOPIC)

    assert live["message_thread_id"] == COOKING_TOPIC
    assert "message_thread_id" not in stray
    assert stray["message_id"] > 0, "the send succeeded; that is the whole problem"


@pytest.mark.asyncio
async def test_a_deleted_topic_repairs_itself_and_the_directory_row_follows_tg82(
    db_path: Path,
) -> None:
    """The durable half of TG-81's repair: the count and the new address are in the row.

    Held in the task, both are lost on the restart a deleted topic tends to be near — the count so
    the human gets a fresh allowance of two every bounce, and the address so the next start sends to
    the dead id again and produces another stray. The store is the only thing that makes "at most
    twice" and "never again with the stale id" survive ``_supervise``.
    """
    api = FakeBotApi()
    async with opened(db_path) as store:
        bot, _service = build(store, api)
        await with_topics(bot, api)
        await store.open_channel(CHAT, COOKING_TOPIC, COOKING)
        api.delete_topic(COOKING_TOPIC)

        await bot._say(Channel(CHAT, COOKING_TOPIC), "a reply for cooking", agent_id=COOKING)

        row = await store.channel(CHAT, COOKING)
        assert row is not None
        assert row["recreations"] == 1
        assert row["topic_id"] != COOKING_TOPIC
        assert dict(await store.channels(CHAT)) == {row["topic_id"]: COOKING}

    assert api.of("create_forum_topic"), "the repair has to have asked for a topic"


@pytest.mark.asyncio
async def test_a_repaired_channel_keeps_the_conversation_it_was_in_the_middle_of_tg82(
    db_path: Path,
) -> None:
    """The repair says *"I am re-sending it where it belongs"*, and the conversation has to go too.

    A recreation is not a rotation: nothing about it is a human asking for a fresh start, and the
    message being re-sent is frequently an approval keyboard for a write that is still parked on the
    old thread. Left behind, that thread is reachable from no channel on the phone — ``/threads`` is
    a read-only listing (TG-40 amended) and ``/cancel`` reads the *new* topic's binding — so the
    human's pending approval and everything they said before the deletion are stranded, with the
    recreated topic looking exactly like the one they lost.
    """
    api = FakeBotApi()
    async with opened(db_path) as store:
        bot, service = build(store, api)
        await with_topics(bot, api)
        await store.open_channel(CHAT, COOKING_TOPIC, COOKING)

        await deliver(bot, message_update(1, topic_id=COOKING_TOPIC))
        before = await store.bound_thread(CHAT, COOKING_TOPIC)
        assert before is not None

        api.delete_topic(COOKING_TOPIC)
        await bot._say(Channel(CHAT, COOKING_TOPIC), "an approval for cooking", agent_id=COOKING)
        repaired = next(iter(await store.channels(CHAT)))
        assert repaired != COOKING_TOPIC, "the repair has to have moved the channel"

        service.calls.clear()
        await deliver(bot, message_update(2, topic_id=repaired))

        assert [name for name, _ in service.calls if name == "create_thread"] == []
        assert [args[0] for name, args in service.calls if name == "start_run"] == [before]


@pytest.mark.asyncio
async def test_a_repaired_channel_is_still_addressable_after_a_restart_tg84(db_path: Path) -> None:
    """``_moved`` is process memory; the directory is what a new adapter reads.

    After a repair the old topic id is dead and the new one exists only because ``rebind_channel``
    wrote it. A restarted adapter that read anything else would send to the deleted id — one more
    stray, one more correction, one more recreation consumed — for a channel that was already fixed.
    """
    api = FakeBotApi()
    async with opened(db_path) as store:
        bot, _service = build(store, api)
        await with_topics(bot, api)
        await store.open_channel(CHAT, COOKING_TOPIC, COOKING)
        api.delete_topic(COOKING_TOPIC)
        await bot._say(Channel(CHAT, COOKING_TOPIC), "a reply for cooking", agent_id=COOKING)
        repaired = dict(await store.channels(CHAT))

    async with opened(db_path) as restarted:
        fresh, _ = build(restarted, api)
        await with_topics(fresh, api)

        assert dict(await restarted.channels(CHAT)) == repaired
        assert fresh._moved == {}, "a restart starts with no re-addressing memory at all"
        assert await fresh._channel_of(CHAT, COOKING) == Channel(CHAT, next(iter(repaired)))


@pytest.mark.asyncio
async def test_the_turn_lock_is_per_channel_so_one_slow_expert_does_not_freeze_the_phone_tg93(
    db_path: Path,
) -> None:
    """A turn is 16 s on the cloud model and **284 s** on the local fallback.

    A lock held per chat means one local-fallback turn in Cooking makes every other expert on the
    phone unresponsive for five minutes, with no message, no error and nothing on ``/health`` — the
    human concludes the bot is dead. Two channels of one chat hold turns at the same time by design,
    which is also why the prompt accumulator had to move into SQLite (TG-60).
    """
    api = FakeBotApi()
    async with opened(db_path) as store:
        bot, _service = build(store, api)

        cooking = bot._channel_lock(Channel(CHAT, COOKING_TOPIC))
        grilling = bot._channel_lock(Channel(CHAT, GRILLING_TOPIC))
        general = bot._channel_lock(Channel(CHAT, GENERAL))

        assert cooking is not grilling
        assert cooking is not general
        assert bot._channel_lock(Channel(CHAT, COOKING_TOPIC)) is cooking

        async with cooking:
            assert not grilling.locked()


@pytest.mark.asyncio
async def test_a_send_error_that_is_not_a_missing_thread_is_not_treated_as_a_deleted_topic_tg83(
    db_path: Path,
) -> None:
    """The discriminator is a substring match on a 400, and a false positive costs a topic.

    Telegram returns a bare ``400`` for the whole Bad Request family, so ``chat not found``,
    ``message is too long`` and ``BUTTON_DATA_INVALID`` all arrive looking the same. Reading any of
    them as "the topic is gone" spends one of two recreations and leaves a topic the human did not
    ask for; the code gate is what stops a 5xx body echoing the phrase from doing the same.
    """
    assert TelegramError(
        "sendMessage", 400, "Bad Request: message thread not found"
    ).is_missing_thread
    assert not TelegramError("sendMessage", 400, "Bad Request: chat not found").is_missing_thread
    assert not TelegramError("sendMessage", 500, "message thread not found").is_missing_thread
