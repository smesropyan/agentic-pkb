"""A channel per expert, driven end to end with no token and no socket (TG-72 … TG-95).

Everything here runs against the **real** :class:`~pkb.server.telegram.TelegramAdapter`, the **real**
:class:`~pkb.service.telegram.SqliteTelegramStore` over ``tmp_path`` — opened
``isolation_level=None`` with WAL, exactly as the daemon opens it (ST-1, AP-4) — and
:class:`~tests.server.stub.StubService`. Only the transport is fake, and it is fake because it has
to be: *Threaded Mode* is a per-bot BotFather toggle that is **off** on this deployment's bot today,
so ``createForumTopic`` answers ``400 the chat is not a forum`` and no live call can settle anything
in this file (F-4, executed 2026-08-09).

Three facts about Telegram decide what is worth asserting here, and the first one is the whole
design:

* **A send into a deleted private-chat topic returns ``ok: true``** (F-2, tdlib#854). The
  ``message_thread_id`` is ignored and the message lands in General. Nothing raises, and **no update
  kind exists for a deleted topic** (F-5), so the only evidence in the universe is the
  ``message_thread_id`` on the returned ``Message``. :class:`FakeBotApi` models that exactly —
  ``delete_topic`` makes no call fail — which is what lets a test tell an adapter that reads the
  response from one that only inspects exceptions. The second one detects nothing, forever, and
  keeps posting one expert's approve buttons under another expert's name.
* **Bot API 10.0 reportedly answers ``400 message thread not found`` instead** (F-3, tdlib#847),
  unresolved. Same fact, same handling — so one fixture switch drives both paths.
* **Only the send family takes a topic** (F-6). ``editMessageReplyMarkup`` addresses a message by
  ``chat_id`` + ``message_id``, which is what makes TG-81's disarm-before-repair possible: the very
  response that revealed the stray already carries the id needed to kill its buttons.

The migration guarantee gets its own section and is the most valuable thing in the file (TG-75): the
toggle is the human's to flip and they may never flip it, so with ``has_topics_enabled: false`` no
payload anywhere may carry a ``message_thread_id`` and nothing may be created.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import inspect
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio

from pkb.contracts import (
    ActionView,
    AgentDescriptor,
    AgentEvent,
    ApprovalRequest,
    MessageComplete,
    RunEnd,
    RunHandle,
)
from pkb.server import telegram as adapter_module
from pkb.server.health import SubsystemState
from pkb.server.telegram import (
    COMMANDS,
    Channel,
    TelegramAdapter,
    TelegramChannelNotifier,
    TelegramConfig,
)
from pkb.server.telegram_api import (
    ALLOWED_UPDATES,
    GENERAL,
    MAX_RECREATIONS,
    POLL_TIMEOUT,
    BotApi,
    TelegramError,
)
from pkb.service import RunSubscription, Session, Thread, ThreadDetail
from pkb.service.runtime import RuntimeService
from pkb.service.telegram import CHANNELS_TABLE, SqliteTelegramStore
from tests.server.stub import AGENTS, COOKING, GRILLING, LIBRARIAN, NOW, StubService

CHAT = 990001
"""The one mapped chat. Its **General** is the Librarian (TG-73's recommendation)."""

OTHER_CHAT = 990002
"""A **second** mapped chat. Nothing requires exactly one, and TG-25 permits one agent to hold a
channel in each of them deliberately — which is why every key in this layer is chat-qualified."""

STRANGER_CHAT = 990009
OWNER = 987654321
"""Fictional. This repository is public and no real user id may appear in it."""

STRANGER = 111000111
RUN = "run-1"

DIFF = (
    "--- a/topics/Cooking/notes/steak.md\n"
    "+++ b/topics/Cooking/notes/steak.md\n"
    "@@ -1,3 +1,4 @@\n"
    "-rest for 5 minutes\n"
    "+rest for 8 minutes\n"
)

KEYBOARD: list[list[dict[str, str]]] = [[{"text": "Approve", "callback_data": "v1|abcd|0|a"}]]
"""One live button — the thing that makes a stray message dangerous rather than merely wrong."""

OMITTED = -1
"""The fake's default for ``topic_id``, so that *omitting* the argument is observable (TG-75).

The Protocol's default is :data:`GENERAL`, and at the Python boundary ``send_message(chat, text)``
and ``send_message(chat, text, topic_id=0)`` are the same call — but on the **wire** they are not:
``_address`` omits the key for General, and ``message_thread_id: 0`` is not a value Telegram has a
meaning for, so a build that sent one would 400 on every General message. TG-75's migration
guarantee is precisely that the request is byte-identical to the pre-topics build, so the fixture
has to be able to see which of the two calls was made. ``-1`` accepts everything the Protocol's
default accepts and is treated as General everywhere below.
"""

Journal = list[tuple[str, dict[str, Any]]]
"""Every Bot API call and every service call, in the order they happened.

Shared between the fake transport and the stub service deliberately: TG-81 is a statement about the
*order* of calls to different objects — the keyboard is cleared before the repair — and two separate
logs cannot say which came first.
"""


# --------------------------------------------------------------------------------------
# Fixtures — a transport with a topic model, a real store, a scriptable service
# --------------------------------------------------------------------------------------


@dataclass
class FakeBotApi:
    """A recording ``BotApi`` **with a topic model**, including the deleted-topic hazard.

    The one thing this fake must get right is F-2: deleting a topic makes **no call fail**. A send
    that names a dead topic is answered ``ok: true`` with the ``message_thread_id`` stripped from the
    echoed ``Message``, because that is what Telegram does in a private chat. A fake that raised
    instead would let an adapter that only inspects exceptions pass every test in this file while
    silently dropping one expert's approval keyboards into another expert's conversation in
    production.

    ``missing_thread_errors`` flips the same deletion to Bot API 10.0's reported behaviour (F-3), so
    TG-80 and TG-83 are driven from one fixture rather than from two that can disagree.
    """

    journal: Journal = field(default_factory=list)
    has_topics_enabled: bool = False
    """Off by default, exactly as the real bot answers today (F-4) — so a test that forgets to turn
    topics on is testing the pre-topics build rather than silently getting the new one."""

    next_message_id: int = 500
    next_topic_id: int = 100
    """Telegram mints topic ids from the message-id sequence, so they start at 1 and 0 is free."""

    deleted: set[int] = field(default_factory=set)
    missing_thread_errors: bool = False
    get_me_error: BaseException | None = None
    create_error: BaseException | None = None
    rename_error: BaseException | None = None
    send_error: BaseException | None = None
    pending: list[Mapping[str, Any]] = field(default_factory=list)

    names: dict[int, str] = field(default_factory=dict)
    """What each topic is called, as a human reads it off the tab (TG-105).

    A topic the human made in their own client is absent from here until the bot names it, which is
    the state the defect was reported from: Telegram called it *New Chat* and the bot left it.
    """

    # -- the calls ---------------------------------------------------------------------

    async def get_me(self) -> Mapping[str, Any]:
        """``has_topics_enabled`` is on the ``User`` and on nothing else (TG-75, F-1)."""
        self.journal.append(("get_me", {}))
        if self.get_me_error is not None:
            raise self.get_me_error
        return {
            "id": 1,
            "username": "pkb_test_bot",
            "has_topics_enabled": self.has_topics_enabled,
        }

    async def get_updates(
        self, offset: int | None, *, timeout: int = POLL_TIMEOUT
    ) -> Sequence[Mapping[str, Any]]:
        await asyncio.sleep(0)
        self.journal.append(("get_updates", {"offset": offset, "timeout": timeout}))
        if offset is not None and offset < 0:
            return self.pending[offset:]
        held, self.pending = list(self.pending), []
        return held

    async def create_forum_topic(self, chat_id: int, name: str) -> Mapping[str, Any]:
        self.journal.append(("create_forum_topic", {"chat_id": chat_id, "name": name}))
        if self.create_error is not None:
            raise self.create_error
        self.next_topic_id += 1
        self.names[self.next_topic_id] = name
        return {"message_thread_id": self.next_topic_id, "name": name}

    async def edit_forum_topic(self, chat_id: int, topic_id: int, name: str) -> None:
        """TG-105. The name a human would read on the tab, so the tests can read it too.

        ``rename_error`` drives TG-106, and the fake keeps answering for a topic it has already
        deleted unless a test asks otherwise: F-13 measured one method of this API doing exactly
        that, and a fake that raised on a dead topic by default would hide a build that read the
        answer as proof the topic is alive.
        """
        self.journal.append(
            ("edit_forum_topic", {"chat_id": chat_id, "topic_id": topic_id, "name": name})
        )
        if self.rename_error is not None:
            raise self.rename_error
        self.names[topic_id] = name

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: Sequence[Sequence[Mapping[str, str]]] | None = None,
        topic_id: int = OMITTED,
    ) -> Mapping[str, Any]:
        if self.send_error is not None:
            raise self.send_error
        # Journalled **before** the topic is resolved, so a send that 400s on a dead topic (F-3) is
        # still visible as an attempt. TG-83's whole assertion is about what happens *around* that
        # attempt — no disarm, no correction, one repair — and a fake that hid the attempt would
        # make the ordering unassertable.
        self.next_message_id += 1
        message_id = self.next_message_id
        self.journal.append(
            (
                "send_message",
                {
                    "chat_id": chat_id,
                    "text": text,
                    "kb": keyboard,
                    "topic_id": GENERAL if topic_id == OMITTED else topic_id,
                    "topic_arg": None if topic_id == OMITTED else topic_id,
                    "message_id": message_id,
                },
            )
        )
        landed = self._land("sendMessage", GENERAL if topic_id == OMITTED else topic_id)
        message: dict[str, Any] = {"message_id": message_id, "chat": {"id": chat_id}}
        if landed != GENERAL:
            message["message_thread_id"] = landed
        return message

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
            (
                "send_document",
                {
                    "chat_id": chat_id,
                    "filename": filename,
                    "topic_id": GENERAL if topic_id == OMITTED else topic_id,
                    "topic_arg": None if topic_id == OMITTED else topic_id,
                },
            )
        )
        landed = self._land("sendDocument", GENERAL if topic_id == OMITTED else topic_id)
        self.next_message_id += 1
        message: dict[str, Any] = {"message_id": self.next_message_id}
        if landed != GENERAL:
            message["message_thread_id"] = landed
        return message

    async def answer_callback(
        self, callback_id: str, text: str = "", *, show_alert: bool = False
    ) -> None:
        self.journal.append(("answer_callback", {"id": callback_id, "alert": show_alert}))

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        self.journal.append(("edit_message", {"chat_id": chat_id, "message_id": message_id}))

    async def clear_keyboard(self, chat_id: int, message_id: int) -> None:
        """No topic, and the signature is the assertion (TG-90, F-6)."""
        self.journal.append(("clear_keyboard", {"chat_id": chat_id, "message_id": message_id}))

    # -- the topic model ---------------------------------------------------------------

    def delete_topic(self, topic_id: int) -> None:
        """The human deletes a topic. **Nothing else changes and no call starts failing** (F-2)."""
        self.deleted.add(topic_id)

    def _land(self, method: str, topic_id: int) -> int:
        if topic_id == GENERAL or topic_id not in self.deleted:
            return topic_id
        if self.missing_thread_errors:
            raise TelegramError(method, 400, "Bad Request: message thread not found")
        return GENERAL

    # -- what the tests read ------------------------------------------------------------

    def of(self, name: str) -> list[dict[str, Any]]:
        return [entry for kind, entry in self.journal if kind == name]

    @property
    def sent(self) -> list[dict[str, Any]]:
        return self.of("send_message")

    @property
    def texts(self) -> list[str]:
        return [str(entry["text"]) for entry in self.sent]

    @property
    def transcript(self) -> str:
        return "\n".join(self.texts)

    @property
    def creates(self) -> list[dict[str, Any]]:
        return self.of("create_forum_topic")

    @property
    def renames(self) -> list[dict[str, Any]]:
        return self.of("edit_forum_topic")

    @property
    def cleared(self) -> list[dict[str, Any]]:
        return self.of("clear_keyboard")

    def to(self, topic_id: int) -> list[str]:
        """Every text delivered into one channel of the mapped chat."""
        return [str(e["text"]) for e in self.sent if int(e["topic_id"]) == topic_id]


class TopicService(StubService):
    """A :class:`StubService` whose catalog can change and whose calls reach the shared journal.

    ``catalog`` is mutable because TG-79 is entirely about an agent *leaving* — the adapter reads
    ``list_agents()`` per use rather than caching it at startup, and a fixed catalog would make that
    property untestable.
    """

    def __init__(self, journal: Journal, *, events: Sequence[AgentEvent] = ()) -> None:
        super().__init__(events=list(events))
        self.journal = journal
        self.catalog: list[AgentDescriptor] = list(AGENTS)
        self.details: dict[str, ThreadDetail] = {}

    def list_agents(self) -> Sequence[AgentDescriptor]:
        return tuple(self.catalog)

    async def create_thread(
        self, agent_id: str, *, title: str | None = None, origin_channel: str = "http"
    ) -> Thread:
        self.journal.append(("create_thread", {"agent_id": agent_id}))
        return await super().create_thread(agent_id, title=title, origin_channel=origin_channel)

    async def create_session(
        self,
        agent_id: str,
        *,
        objective: str | None = None,
        operator: str = "operator",
        name: str | None = None,
    ) -> Session:
        # Task 7: `_turn` opens a session, not a thread — same journal kind as `create_thread`
        # above, so every pre-existing `kind == "create_thread"`-shaped assertion in this file
        # keeps meaning what it always meant: one fresh conversation opened for this agent.
        self.journal.append(("create_thread", {"agent_id": agent_id}))
        return await super().create_session(
            agent_id, objective=objective, operator=operator, name=name
        )

    async def start_run(
        self,
        thread_id: str,
        message: str,
        *,
        approval_mode: str = "interactive",
        run_id: str | None = None,
    ) -> RunSubscription:
        self.journal.append(("start_run", {"thread_id": thread_id, "message": message}))
        return await super().start_run(
            thread_id, message, approval_mode=approval_mode, run_id=run_id
        )

    async def start_session_run(self, session_id: str, message: str) -> RunSubscription:
        self.journal.append(("start_run", {"thread_id": session_id, "message": message}))
        return await super().start_session_run(session_id, message)

    async def get_thread(self, thread_id: str) -> ThreadDetail:
        scripted = self.details.get(thread_id)
        if scripted is not None:
            self.journal.append(("get_thread", {"thread_id": thread_id}))
            return scripted
        return await super().get_thread(thread_id)


@pytest_asyncio.fixture
async def connection(tmp_path: Path) -> Any:
    """The daemon's own connection settings: autocommit and WAL (ST-1, ST-2, AP-4)."""
    handle = await aiosqlite.connect(tmp_path / "pkb.sqlite", isolation_level=None)
    try:
        await handle.execute("PRAGMA journal_mode=WAL")
        yield handle
    finally:
        await handle.close()


@pytest_asyncio.fixture
async def store(connection: aiosqlite.Connection) -> SqliteTelegramStore:
    telegram_store = SqliteTelegramStore(connection)
    await telegram_store.setup()
    return telegram_store


@pytest.fixture
def journal() -> Journal:
    return []


@pytest.fixture
def api(journal: Journal) -> FakeBotApi:
    return FakeBotApi(journal)


@pytest.fixture
def service(journal: Journal) -> TopicService:
    return TopicService(journal, events=reply_script())


class FakeRuntime:
    """Satisfies ``pkb.service.runtime.Runtime`` structurally — no harness (mirrors
    ``test_session_routes.py``'s own fake). Fix round 1, findings 3 and 5: proving ``/name`` and
    ``/end`` — and rename's notifier=None case — needs the **real** ``RuntimeService``, because
    ``TopicService``/``StubService`` never compose ``SessionStore.channels`` or
    ``ChannelNotifier`` at all, so a reviewer turning either command handler into a no-op leaves
    every ``TopicService``-driven assertion unable to tell the difference.
    """

    db_path = Path("never-opened.sqlite")

    def list_agents(self) -> Any:
        return AGENTS

    def run(self, agent_id: str, thread_id: str, message: str, **_: Any) -> Any:
        async def stream() -> AsyncIterator[Any]:
            yield MessageComplete(run_id="r1", agent_id=agent_id, text="Filed under Cooking.")
            yield RunEnd(run_id="r1", final_text="Filed under Cooking.")

        return stream()

    async def cancel(self, run_id: str) -> None:
        return None

    async def history(self, agent_id: str, thread_id: str) -> Any:
        return []

    async def delete_thread(self, thread_id: str) -> None:
        return None

    async def request_scan(self, request: Any) -> Any:
        raise NotImplementedError

    async def regenerate(self) -> None:
        raise NotImplementedError


@pytest_asyncio.fixture
async def real_service(connection: aiosqlite.Connection, tmp_path: Path) -> RuntimeService:
    """The real service over the same connection ``store`` uses (production shares one, per
    ``pkb.daemon``'s own composition) and a real, writable ``kb_root``."""
    built = RuntimeService(FakeRuntime(), connection, kb_root=tmp_path / "kb")
    await built.setup()
    return built


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def reply_script(text: str = "Filed under Cooking.") -> list[AgentEvent]:
    return [
        MessageComplete(run_id=RUN, agent_id=COOKING, text=text),
        RunEnd(run_id=RUN, final_text=text),
    ]


def adapter(
    service: TopicService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    *,
    chats: Mapping[int, str] | None = None,
    owners: frozenset[int] = frozenset({OWNER}),
) -> TelegramAdapter:
    return TelegramAdapter(
        service=service,
        store=store,
        api=api,
        config=TelegramConfig(
            token="123456789:AA-fake",
            chats={CHAT: LIBRARIAN} if chats is None else chats,
            owner_user_ids=owners,
        ),
    )


async def boot(bot: TelegramAdapter) -> None:
    """The two startup steps ``run()`` awaits before it creates a single child (TG-75, TG-11).

    Driven rather than faked, because "probed once, from ``getMe``, and nothing else decides it" is
    itself a rule — setting ``bot._topics`` by hand would test a field instead of the probe.
    """
    await bot._probe_topics()
    await bot._load_directory()


async def topical(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi, **kwargs: Any
) -> TelegramAdapter:
    """An adapter that has discovered Threaded Mode is on. The ordinary case in this file."""
    api.has_topics_enabled = True
    bot = adapter(service, store, api, **kwargs)
    await boot(bot)
    return bot


def message_update(
    update_id: int = 1,
    *,
    chat_id: int = CHAT,
    topic_id: int = GENERAL,
    sender: int = OWNER,
    text: str | None = "where does the steak note go?",
    chat_type: str = "private",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": update_id,
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": sender},
    }
    if topic_id != GENERAL:
        # Telegram omits the key entirely in General and carries the topic id everywhere else
        # (F-1). A fixture that sent `0` would let the adapter read a value the wire never has.
        message["message_thread_id"] = topic_id
        message["is_topic_message"] = True
    if text is not None:
        message["text"] = text
    message.update(extra or {})
    return {"update_id": update_id, "message": message}


async def drain(bot: TelegramAdapter) -> None:
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
    """One update in, everything it produced out."""
    await bot._dispatch(update)
    await drain(bot)


async def say(bot: TelegramAdapter, text: str, *, topic_id: int = GENERAL, **kwargs: Any) -> None:
    await deliver(bot, message_update(topic_id=topic_id, text=text, **kwargs))


async def channel_for(
    bot: TelegramAdapter, api: FakeBotApi, agent_id: str, *, chat_id: int = CHAT
) -> int:
    """Give ``agent_id`` a channel the way a human does — ``/channels`` — and return the topic id."""
    before = len(api.creates)
    await say(bot, f"/channels {agent_id}", chat_id=chat_id, update_id=api.next_topic_id)
    assert len(api.creates) == before + 1, "the fixture itself expected exactly one creation"
    return int(api.next_topic_id)


def action(
    *,
    tool: str = "write_file",
    description: str = DIFF,
    allowed: tuple[str, ...] = ("approve", "reject"),
    reason: str = "breadth-approval",
) -> ActionView:
    return ActionView(
        tool=tool,
        args={"file_path": "topics/Cooking/notes/steak.md"},
        description=description,
        allowed_decisions=allowed,  # type: ignore[arg-type]
        reason=reason,
    )


def approval(
    *,
    thread_id: str = "t-1",
    interrupt_id: str = "i-1",
    agent_id: str = COOKING,
) -> ApprovalRequest:
    return ApprovalRequest(
        interrupt_id=interrupt_id,
        agent_id=agent_id,
        thread_id=thread_id,
        actions=(action(),),
    )


def thread_row(thread_id: str, agent_id: str, *, pending: str | None = None) -> Thread:
    return Thread(
        thread_id=thread_id,
        agent_id=agent_id,
        created_at=NOW,
        updated_at=NOW,
        origin_channel="telegram",
        title=f"{agent_id} conversation",
        pending_interrupt_id=pending,
    )


def kinds(journal: Journal) -> list[str]:
    return [kind for kind, _ in journal]


# --------------------------------------------------------------------------------------
# § addressing: the channel is the routing key (TG-72, TG-73, TG-74)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_topics_of_one_chat_are_two_conversations_tg72(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """Two experts in one chat must not share one thread, or topics are a lie the UI tells.

    Keyed on the chat alone — which is what every structure in this adapter was before §9 — the
    human sees a topic per expert, sends a note to each, and both land in whichever conversation was
    bound last. That is TG-1's mis-file with a screen actively denying it, reachable with no
    configuration change at all.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    grilling = await channel_for(bot, api, GRILLING)

    await say(bot, "the steak note", topic_id=cooking)
    await say(bot, "the fire note", topic_id=grilling)

    created = [entry["agent_id"] for kind, entry in journal if kind == "create_thread"]
    assert created == [COOKING, GRILLING]
    cooking_binding = await store.binding(CHAT, cooking)
    grilling_binding = await store.binding(CHAT, grilling)
    assert cooking_binding is not None and grilling_binding is not None
    assert cooking_binding[0] != grilling_binding[0], "one thread behind two topics is the mis-file"
    assert (cooking_binding[1], grilling_binding[1]) == (COOKING, GRILLING)


@pytest.mark.asyncio
async def test_general_and_a_topic_never_share_a_thread_tg72(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """General is a channel, not the chat's default — so it has its own conversation.

    The tempting shortcut is ``topic_id or None``: it makes General fall back to the chat-keyed row
    and every pre-topics test keeps passing, while the Librarian's conversation and Cooking's become
    one thread that rotates under the human.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)

    await say(bot, "a general question")
    await say(bot, "a cooking note", topic_id=cooking)

    general_thread = await store.bound_session(CHAT, GENERAL)
    cooking_thread = await store.bound_session(CHAT, cooking)
    assert general_thread is not None and cooking_thread is not None
    assert general_thread != cooking_thread


@pytest.mark.asyncio
async def test_general_answers_as_the_agent_the_configuration_names_tg73(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """A non-Librarian General is legal, and that is the migration promise, not an oversight.

    Every deployment shipped before §9 mapped its one chat to whatever agent the human wanted on
    their phone — frequently one expert. A hard rule that General must be the Librarian would break
    all of them at upgrade for a stylistic gain, so it is a recommendation with a startup warning
    and a self-naming ``/agents``.
    """
    bot = await topical(service, store, api, chats={CHAT: COOKING})

    await say(bot, "a note typed into General")

    assert ("create_thread", {"agent_id": COOKING}) in journal
    binding = await store.binding(CHAT, GENERAL)
    assert binding is not None and binding[1] == COOKING


@pytest.mark.asyncio
async def test_agents_in_general_names_the_agent_that_answers_there_tg73(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """General is the only channel whose title names no agent, so it is the one place to say it.

    Q29 ruled this is said here rather than as a per-daemon-start notice: a notice on a process
    whose value proposition is staying up for weeks is either invisible or, under a restart loop,
    spam. The cost is that a human who never types ``/agents`` never learns — which is exactly why
    the answer has to name the agent unambiguously when they do.
    """
    bot = await topical(service, store, api, chats={CHAT: COOKING})

    await say(bot, "/agents")

    assert api.texts[0].splitlines()[0] == f"This channel talks to {COOKING}."


@pytest.mark.asyncio
async def test_an_unbound_topic_is_answered_in_itself_and_runs_nothing_tg74(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """An unbound topic must not fall back to the chat's General agent — that is a silent mis-file.

    And the reply has to arrive **in the topic**: posted to General it is an explanation about a
    conversation the human is not looking at, for a message they sent somewhere else.
    """
    bot = await topical(service, store, api)

    await say(bot, "a note into a topic I made by hand", topic_id=4242)

    assert "start_run" not in kinds(journal)
    assert len(api.sent) == 1
    assert api.sent[0]["topic_id"] == 4242
    assert "/channels" in api.texts[0]


@pytest.mark.asyncio
async def test_an_unbound_topic_lists_agent_ids_the_owner_can_use_tg74(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """TG-21 withholds agent ids from a **stranger**; the allow-list has already run here.

    Withholding them from the owner makes ``/channels <agent-id>`` an instruction that cannot be
    followed: a phone has no other way to learn an agent id, and the reply's whole purpose is to be
    actionable. The contrast is asserted in the same test so the two cases cannot drift.
    """
    bot = await topical(service, store, api)

    await say(bot, "hello", topic_id=4242)
    await say(bot, "hello", update_id=2, chat_id=STRANGER_CHAT)

    offer, unmapped = api.texts[0], api.texts[1]
    assert COOKING in offer and GRILLING in offer
    for descriptor in AGENTS:
        assert descriptor.agent_id not in unmapped, "an unmapped chat still leaks nothing (TG-21)"


@pytest.mark.asyncio
async def test_one_explanation_per_topic_per_window_tg23(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Rate limited per channel, because two unbound topics are two different explanations.

    Shared per chat, the first unbound topic silences the second one — leaving a human with a topic
    that answers nothing and says nothing about why, which is indistinguishable from a broken bot.
    """
    bot = await topical(service, store, api)

    for index in range(10):
        await say(bot, "hello", update_id=index + 1, topic_id=4242)
    await say(bot, "hello", update_id=99, topic_id=4343)

    assert len(api.to(4242)) == 1
    assert len(api.to(4343)) == 1


@pytest.mark.asyncio
async def test_a_stranger_is_ignored_in_a_bound_topic_too_tg95(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """One authorization boundary, and a topic is not a second, weaker one.

    "A channel per expert" invites the idea of per-channel permissions, and a second boundary beside
    the only real one is how the real one stops being checked. A topic changes what a message is
    addressed *to*, never who may say yes to a write with no undo.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    api.journal.clear()

    await say(bot, "file this", topic_id=cooking, sender=STRANGER)

    assert api.sent == [], "silence, not a refusal: a reply is an existence oracle"
    assert "start_run" not in kinds(journal)


# --------------------------------------------------------------------------------------
# § the migration guarantee: Threaded Mode is off until a human flips it (TG-75)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_with_threaded_mode_off_channels_explains_the_toggle_and_creates_nothing_tg75(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi, connection: Any
) -> None:
    """``/channels`` is inert rather than absent, because "nothing happened" is unactionable.

    The toggle lives in BotFather, not in this daemon's configuration, so the only useful answer to
    a human asking for a channel on a bot without Threaded Mode is the name of the setting.
    """
    bot = adapter(service, store, api)
    await boot(bot)

    await say(bot, f"/channels {COOKING}")
    await say(bot, "/channels all", update_id=2)

    assert api.creates == []
    assert api.transcript.count("Threaded Mode") == 2
    cursor = await connection.execute(f"SELECT COUNT(*) FROM {CHANNELS_TABLE}")
    assert (await cursor.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_a_general_send_omits_the_key_even_with_topics_on_tg75(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """``0`` is this codebase's spelling of General; it is not a value Telegram has a meaning for.

    A build that read TG-75's "every send carrying 0 for General" as a *wire* fact would put
    ``message_thread_id: 0`` on every General message and 400 on all of them. The Python argument is
    ``0``; the payload has no key at all, in both modes.
    """
    bot = await topical(service, store, api)

    await say(bot, "a general note")

    assert api.sent, "nothing was sent, so nothing was asserted"
    assert [entry["topic_arg"] for entry in api.sent] == [None] * len(api.sent)


@pytest.mark.asyncio
async def test_a_failed_probe_runs_without_topics_rather_than_restarting_tg75(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """``getMe`` timing out must not restart the bot, and must not guess ``True``.

    ``_supervise`` restarts this task on any exception, so raising here is the loop TG-13 forbids.
    ``False`` is also the safe direction to be wrong in: every send goes to General, which is
    visible and recoverable, where a wrong ``True`` addresses topics that may not exist.
    """
    api.get_me_error = TelegramError("getMe", 0, "transport failure: ReadTimeout")
    bot = adapter(service, store, api)
    bot.health = SubsystemState(name="telegram")

    await boot(bot)

    assert bot._topics is False
    assert bot.health.topics_enabled is False


@pytest.mark.asyncio
async def test_topic_mode_is_probed_once_and_published_tg75(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """One ``getMe``, and the answer is a fact about the deployment rather than a per-send question.

    Published on ``/health`` because the toggle and the daemon's belief about it are two different
    things, and only one of them is visible in BotFather — so a human certain they enabled it needs
    somewhere to check what the daemon actually saw.
    """
    api.has_topics_enabled = True
    bot = adapter(service, store, api)
    bot.health = SubsystemState(name="telegram")

    await boot(bot)
    await say(bot, "a note")

    assert kinds(journal).count("get_me") == 1
    assert bot.health.topics_enabled is True
    assert bot.health.payload()["topics_enabled"] is True


# --------------------------------------------------------------------------------------
# § creation is a human act, and only ever one (TG-76, TG-77, TG-78, TG-87)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_starting_up_creates_no_topics_at_all_tg76(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Eager creation buries the four channels a human uses under twenty-six they do not.

    It also cannot be undone in one action, and — because **no API enumerates a chat's topics**
    (F-5) — a partial failure mid-burst leaves a state nothing can reconstruct. So a boot against a
    large catalog and an empty directory issues zero creates, and so does a catalog that grows
    afterwards.
    """
    api.has_topics_enabled = True
    service.catalog = [
        AgentDescriptor(
            agent_id=f"topic/t{index}",
            title=f"T{index}",
            description="",
            has_custom_expert=False,
            model_id="m",
        )
        for index in range(30)
    ]
    bot = adapter(service, store, api)

    await boot(bot)
    service.catalog.append(
        AgentDescriptor(
            agent_id="topic/new", title="New", description="", has_custom_expert=False, model_id="m"
        )
    )
    await say(bot, "a note")

    assert api.creates == []


@pytest.mark.asyncio
async def test_a_second_channels_for_one_agent_creates_nothing_tg77(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi, connection: Any
) -> None:
    """Two channels for one agent in one chat is that expert's history split in half, invisibly.

    TG-25 permits two channels for one agent **across chats** deliberately, because that is a
    visible arrangement the human made. Within one chat nothing on the screen says which half a
    message went to.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)

    await say(bot, f"/channels {COOKING}", update_id=2)

    assert len(api.creates) == 1
    assert f"topic {cooking}" in api.texts[-1]
    cursor = await connection.execute(
        f"SELECT COUNT(*) FROM {CHANNELS_TABLE} WHERE chat_id = ? AND agent_id = ?", (CHAT, COOKING)
    )
    assert (await cursor.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_two_channels_commands_arriving_together_create_one_topic_tg77(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi, connection: Any
) -> None:
    """TG-77's "creates nothing" is a check-then-act across three awaits, so it needs a lock.

    ``_poll`` gives every update its own child of the task group, so two ``/channels`` for one agent
    delivered in one batch both read a directory with no row for it and both create. Measured before
    the lock: two ``createForumTopic`` calls and **one** directory row, because the row is keyed on
    ``(chat_id, agent_id)`` and the second write replaced the first. The chat is left holding a
    second topic of the same name that nothing addresses, that the bot may never delete (TG-78) and
    that no API can enumerate (F-5).

    ``_repairs`` already names this defect on the recreation path. This is the same one, on the path
    that runs when the human asks for a channel.
    """
    bot = await topical(service, store, api)

    await asyncio.gather(
        bot._dispatch(message_update(update_id=2, text=f"/channels {COOKING}")),
        bot._dispatch(message_update(update_id=3, text=f"/channels {COOKING}")),
    )
    await drain(bot)

    assert len(api.creates) == 1
    cursor = await connection.execute(
        f"SELECT COUNT(*) FROM {CHANNELS_TABLE} WHERE chat_id = ? AND agent_id = ?", (CHAT, COOKING)
    )
    assert (await cursor.fetchone())[0] == 1
    assert "so I created nothing" in api.texts[-1]


@pytest.mark.asyncio
async def test_a_fresh_adapter_routes_an_existing_channel_without_recreating_it_tg77(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """The directory is durable because ``_supervise`` carries nothing across a restart.

    Held in memory it would be a set of topics that exist on the human's phone and are addressable
    by nothing, permanently — no API enumerates a chat's topics (F-5), so there is no way back.
    """
    first = await topical(service, store, api)
    cooking = await channel_for(first, api, COOKING)

    second = await topical(service, store, api)
    await say(second, "a note after the restart", topic_id=cooking)

    assert len(api.creates) == 1
    assert ("create_thread", {"agent_id": COOKING}) in journal


@pytest.mark.asyncio
async def test_create_forum_topic_is_given_the_title_and_nothing_else_tg78(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Every parameter on this Protocol is one more thing every fake must implement.

    ``icon_color`` and ``icon_custom_emoji_id`` have no rule behind them, so they are not on the
    Protocol at all — the same precedent that dropped ``send_chat_action``.
    """
    bot = await topical(service, store, api)

    await say(bot, f"/channels {COOKING}")

    title = {descriptor.agent_id: descriptor.title for descriptor in AGENTS}[COOKING]
    assert api.creates == [{"chat_id": CHAT, "name": title}]
    assert set(inspect.signature(BotApi.create_forum_topic).parameters) == {
        "self",
        "chat_id",
        "name",
    }


def test_nothing_on_the_protocol_can_remove_or_silence_a_topic_tg78() -> None:
    """A bot that tidies the human's chat destroys evidence on a system with no undo (D6).

    The topic is their record of what they approved, so ``closeForumTopic``, ``reopenForumTopic``,
    ``deleteForumTopic``, ``unpinAllForumTopicMessages`` and ``deleteMessage`` are absent by
    construction rather than by convention — a method that does not exist cannot be called from a
    ``finally``.

    ``edit_forum_topic`` is on the surface (TG-78 amended, TG-105) and it is the one exception, so
    the assertion is that the adapter reaches for it from exactly the two places S-16 justifies
    (Task 7): ``_name_channel``, at the moment a topic is taken as a channel, and
    ``TelegramChannelNotifier.retitle``, the one caller of ``/name``'s fan-out. A third call site is
    how "name it once, at the moment of ownership, or on the operator's own ``/name``" becomes
    "police the name", and the human's own title reverts on a schedule they cannot see.
    """
    surface = {name for name in vars(BotApi) if not name.startswith("_")}
    assert surface == {
        "get_me",
        "get_updates",
        "create_forum_topic",
        "edit_forum_topic",
        "send_message",
        "send_document",
        "answer_callback",
        "edit_message",
        "clear_keyboard",
    }
    # The attribute names the adapter actually reaches for, from its AST rather than its text: the
    # module *documents* why `delete_message` is absent, and a substring scan over prose would fail
    # on the explanation instead of on the call.
    reached = [
        node.attr
        for node in ast.walk(ast.parse(inspect.getsource(adapter_module)))
        if isinstance(node, ast.Attribute)
    ]
    forbidden = ("delete_message", "delete_forum", "close_forum", "reopen_forum", "unpin_all")
    for name in forbidden:
        assert name not in surface
        assert not [attr for attr in reached if name in attr], f"the adapter reaches for {name}"
    assert reached.count("edit_forum_topic") == 2, "one wire call each for bind-time and /name"
    assert reached.count("_name_channel") == 1, "and one caller of the bind-time half"


@pytest.mark.asyncio
async def test_channels_with_no_arguments_answers_with_one_row_per_agent_tg87(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The no-argument form is the picker, and the roster it draws is the keyboard (TG-96, §10).

    ``_binding_offer`` names ``/channels <agent-id>`` as the way out of an unbound topic, and a
    phone has no other way to learn an agent id — the rule's own docstring calls that an instruction
    that cannot be followed. A listing closed half of it; a row that creates the channel closes the
    rest. The row count is the assertion, because a keyboard that drops an agent is a human unable
    to tell a missing expert from one that does not exist.
    """
    bot = await topical(service, store, api)

    await say(bot, "/channels")

    keyboard = api.sent[-1]["kb"]
    assert [len(row) for row in keyboard] == [1, 1, 1]
    assert [button["callback_data"] for row in keyboard for button in row] == [
        f"c1|{LIBRARIAN}",
        f"c1|{COOKING}",
        f"c1|{GRILLING}",
    ]


@pytest.mark.asyncio
async def test_with_threaded_mode_off_the_picker_draws_no_buttons_tg87(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A button that creates a topic in a chat that cannot hold one fails in the human's hand.

    The toggle lives in BotFather rather than in this daemon's configuration, so the only useful
    answer is the name of the setting, with nothing to press beside it.
    """
    bot = adapter(service, store, api)
    await boot(bot)

    await say(bot, "/channels")

    assert [entry["kb"] for entry in api.sent] == [None]
    assert "Threaded Mode" in api.texts[-1]
    assert api.creates == []


@pytest.mark.asyncio
async def test_channels_binds_the_topic_it_was_typed_in_when_that_topic_is_free_tg87(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Bind-here is the **only** recovery path for a lost SQLite file (F-5), so it must be reachable.

    Routing refuses an unbound topic before the command branch, so without a bootstrap the reply to
    a message in a hand-made topic names a command the human then cannot type. One command with two
    behaviours is a real ambiguity (Q30) and it is paid for by the reply naming which one happened.
    """
    bot = await topical(service, store, api)

    await say(bot, f"/channels {COOKING}", topic_id=555)

    assert api.creates == [], "binding creates nothing, and the reply has to say so"
    assert "Bound this topic" in api.texts[-1]
    assert dict(await store.channels(CHAT)) == {555: COOKING}


@pytest.mark.asyncio
async def test_channels_in_general_creates_a_new_topic_and_says_so_tg87(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The same command in General cannot bind General — General is already a channel (TG-73).

    So it creates, and the reply distinguishes that from a bind. Told the wrong one, a human goes
    looking for their expert in the topic they were typing in.
    """
    bot = await topical(service, store, api)

    await say(bot, f"/channels {COOKING}")

    assert len(api.creates) == 1
    assert "Created a new topic" in api.texts[-1]


@pytest.mark.asyncio
async def test_channels_all_skips_the_agent_that_already_answers_in_general_tg87(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """``/channels all`` is the set-it-up-once path, and General is a channel it must count.

    Without folding ``config.chats[chat_id]`` into the directory, ``all`` gives the Librarian a
    second channel beside the General it already answers in — TG-77's split history, created by the
    convenience command rather than by a mistake.
    """
    bot = await topical(service, store, api)

    await say(bot, "/channels all")

    assert [entry["name"] for entry in api.creates] == ["Cooking", "Grilling"]
    assert set((await store.channels(CHAT)).values()) == {COOKING, GRILLING}


@pytest.mark.asyncio
async def test_channels_for_an_unknown_agent_creates_nothing_tg87(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A typo on a phone must not mint a topic named after it.

    The topic would be real, addressable, and bound to an agent that does not exist — and deleting
    it is the human's problem, in a client where topics are easy to make and awkward to find.
    """
    bot = await topical(service, store, api)

    await say(bot, "/channels topic/cookng")

    assert api.creates == []
    assert "no agent called topic/cookng" in api.texts[-1]


@pytest.mark.asyncio
async def test_a_failed_creation_is_reported_and_changes_nothing_tg87(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The human asked for a channel; the useful answer is that they did not get one.

    Raising would restart the bot for a command somebody typed, which takes every other chat and
    every parked approval with it.
    """
    bot = await topical(service, store, api)
    api.create_error = TelegramError(
        "createForumTopic", 400, "Bad Request: the chat is not a forum"
    )

    await say(bot, f"/channels {COOKING}")

    assert dict(await store.channels(CHAT)) == {}
    assert "Nothing was created" in api.texts[-1]


@pytest.mark.asyncio
async def test_an_agent_that_left_the_catalog_keeps_its_topic_and_stops_running_tg79(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """The Telegram topic outlives the knowledge-base one, deliberately.

    It holds the human's history of a topic they may be in the middle of splitting, and this system
    has no undo. Routing to whatever agent is nearest instead would be the mis-file TG-1 exists to
    prevent, with a configuration that looks correct.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    await say(bot, "a first note", topic_id=cooking)
    thread = await store.bound_session(CHAT, cooking)
    bot.health = SubsystemState(name="telegram")
    service.catalog = [d for d in service.catalog if d.agent_id != COOKING]
    api.journal.clear()

    await say(bot, "a note to a departed expert", update_id=9, topic_id=cooking)

    assert "start_run" not in kinds(journal), "nothing runs for an agent that is gone"
    assert COOKING in api.texts[-1] and "left the topic" in api.texts[-1]
    assert bot.health.retired_channels == (COOKING,)

    service.catalog = list(AGENTS)
    await say(bot, "and now it is back", update_id=10, topic_id=cooking)

    assert await store.bound_session(CHAT, cooking) == thread, "the binding survived intact"


# --------------------------------------------------------------------------------------
# § the deleted-topic hazard — a send Telegram accepts and misdelivers (TG-80 … TG-84)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_a_topic_makes_no_call_fail_f2(
    api: FakeBotApi,
) -> None:
    """The companion test for TG-80, and the one that keeps the fixture honest.

    If this ever starts raising, every hazard test below would also pass against an adapter that
    inspects only exceptions — which is the adapter that ships the failure §9 exists to prevent. The
    real API answers ``ok: true`` and relocates the message; the only evidence is the response.
    """
    created = await api.create_forum_topic(CHAT, "Cooking")
    topic_id = int(created["message_thread_id"])
    api.delete_topic(topic_id)

    sent = await api.send_message(CHAT, "hello", topic_id=topic_id)

    assert "message_thread_id" not in sent, "a stray send echoes a Message that is in General"
    assert sent["message_id"], "and it is a successful send, not an error"


@pytest.mark.asyncio
async def test_a_send_into_a_deleted_topic_is_detected_from_the_response_tg80(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The ``message_thread_id`` sent is a request; the one returned is the truth.

    Nothing raises, nothing is logged by Telegram and no update announces the deletion, so an
    adapter that watches only the error path detects this never — and keeps posting Cooking's
    approve buttons into General under the Librarian's name for the life of the deployment.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    api.delete_topic(cooking)
    api.journal.clear()

    await bot._say(Channel(CHAT, cooking), "the reply", agent_id=COOKING)

    assert [kind for kind, _ in api.journal] == [
        "send_message",
        "send_message",
        "create_forum_topic",
        "send_message",
    ]
    assert api.sent[0]["topic_id"] == cooking, "the stray went out before anything was known"
    assert api.sent[1]["topic_id"] == GENERAL and "has been deleted" in api.texts[1]
    assert api.sent[2]["topic_id"] == api.next_topic_id, "the message is re-sent where it belongs"


@pytest.mark.asyncio
async def test_a_stray_approval_is_disarmed_before_anything_else_tg81(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A message is dangerous only while its buttons are live — so they die first.

    Repairing first means a ``createForumTopic`` failure leaves an Approve button for an
    irreversible write sitting in General under the wrong expert's name, which is the exact failure
    this whole section is arranged around. The response that revealed the problem already carries
    the ``message_id`` needed to kill them, so the disarm cannot fail for want of information.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    api.delete_topic(cooking)
    api.journal.clear()

    await bot._send(Channel(CHAT, cooking), "Approve this write?", KEYBOARD, agent_id=COOKING)

    assert [kind for kind, _ in api.journal] == [
        "send_message",  # the stray, which Telegram accepted and delivered to General
        "clear_keyboard",  # its buttons die first, with the message_id that response just gave
        "send_message",  # only then the correction
        "create_forum_topic",  # and only then the repair, which is allowed to fail
        "send_message",
    ]
    assert api.cleared == [{"chat_id": CHAT, "message_id": api.sent[0]["message_id"]}]


@pytest.mark.asyncio
async def test_a_stray_plain_message_issues_no_clear_keyboard_tg81(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Nothing to disarm means no call — an edit on a message with no markup is noise on the wire.

    It also matters for the failure mode: ``clear_keyboard`` inside the hazard path is suppressed,
    so a call that 400s here would be invisible and would teach nobody anything.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    api.delete_topic(cooking)
    api.journal.clear()

    await bot._say(Channel(CHAT, cooking), "just a reply", agent_id=COOKING)

    assert api.cleared == []


@pytest.mark.asyncio
async def test_message_thread_not_found_takes_the_same_path_without_a_stray_tg83(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Bot API 10.0 reportedly errors where 9.3 silently relocated (F-3), and both mean one thing.

    Nothing was delivered, so there is no stray to disarm and no correction to post — and it is
    never retried, because retrying re-issues the same dead id to the retry bound, three 400s per
    message forever.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    bot.health = SubsystemState(name="telegram")
    api.missing_thread_errors = True
    api.delete_topic(cooking)
    api.journal.clear()

    await bot._say(Channel(CHAT, cooking), "the reply", agent_id=COOKING)

    assert [kind for kind, _ in api.journal] == [
        "send_message",
        "create_forum_topic",
        "send_message",
    ]
    assert api.cleared == []
    assert api.sent[-1]["topic_id"] == api.next_topic_id
    assert bot.health.restarts == 0, "a dead topic is never a transport failure"


@pytest.mark.asyncio
async def test_a_dead_channel_is_never_addressed_twice_tg84(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """TG-80 detects one stray per send; without this a fan-out produces eight of them.

    Eight strays and eight corrections land in General at exactly the moment something needs
    approving, and the chat becomes unreadable. A queued item addressed to a channel that died while
    it waited is **re-addressed, never dropped and never sent blind**.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    api.delete_topic(cooking)
    api.journal.clear()
    for index in range(3):
        await bot._queue(Channel(CHAT, cooking), f"frame {index}", COOKING)

    await drain(bot)

    strays = [entry for entry in api.sent if int(entry["topic_id"]) == cooking]
    assert len(strays) == 1, "one frame discovers the deletion; the rest follow the repair"
    assert api.transcript.count("has been deleted") == 1
    repaired = api.next_topic_id
    assert api.to(repaired) == ["frame 0", "frame 1", "frame 2"]


@pytest.mark.asyncio
async def test_a_channel_is_recreated_twice_and_then_retired_tg82(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Unbounded recreation is a loop against a human deliberately deleting a topic.

    Each turn of it costs a ``createForumTopic`` and a notification. Refusing to repair at all is
    worse — the expert's approvals become undeliverable, which is the outcome Q20 rejected. Two
    survives an accidental deletion and a fat-fingered second one without becoming a fight, and past
    it the traffic goes to General with the agent id on the first line.
    """
    bot = await topical(service, store, api)
    bot.health = SubsystemState(name="telegram")
    cooking = await channel_for(bot, api, COOKING)
    api.journal.clear()

    live = cooking
    for attempt in range(MAX_RECREATIONS + 1):
        api.delete_topic(live)
        await bot._say(Channel(CHAT, cooking), f"reply {attempt}", agent_id=COOKING)
        live = api.next_topic_id

    assert len(api.creates) == MAX_RECREATIONS, "past the bound, nothing is created"
    assert api.transcript.count("stopped making new ones") == 1
    assert api.to(GENERAL)[-1] == f"{COOKING}\nreply {MAX_RECREATIONS}"
    assert bot.health.retired_channels == (COOKING,)
    row = await store.channel(CHAT, COOKING)
    assert row is not None and row["retired"] is True


@pytest.mark.asyncio
async def test_the_recreation_count_survives_a_restart_tg82(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """An in-memory bound hands a human deleting a topic in anger a fresh allowance every bounce.

    ``_supervise`` carries nothing across, so a count kept in the task is a count that resets on the
    very failure it is meant to bound — and a bot in a restart loop would recreate forever.
    """
    first = await topical(service, store, api)
    cooking = await channel_for(first, api, COOKING)
    live = cooking
    for attempt in range(MAX_RECREATIONS):
        api.delete_topic(live)
        await first._say(Channel(CHAT, cooking), f"reply {attempt}", agent_id=COOKING)
        live = api.next_topic_id
    creates_before = len(api.creates)

    second = await topical(service, store, api)
    api.delete_topic(live)
    await second._say(Channel(CHAT, live), "after the restart", agent_id=COOKING)

    assert len(api.creates) == creates_before, "the restart did not restore the allowance"
    assert "stopped making new ones" in api.transcript


@pytest.mark.asyncio
async def test_a_retired_channel_stays_retired_across_a_restart_tg82(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Told once. A notice a restart re-announces is a notice the human learns to ignore (TG-13).

    ``_load_directory`` seeds the retirement from the store for exactly this reason, and the traffic
    keeps its General prefix rather than silently trying the dead topic again.
    """
    first = await topical(service, store, api)
    cooking = await channel_for(first, api, COOKING)
    live = cooking
    for attempt in range(MAX_RECREATIONS + 1):
        api.delete_topic(live)
        await first._say(Channel(CHAT, cooking), f"reply {attempt}", agent_id=COOKING)
        live = api.next_topic_id
    api.journal.clear()

    second = await topical(service, store, api)
    await second._say(Channel(CHAT, live), "after the restart", agent_id=COOKING)

    assert api.creates == []
    assert "stopped making new ones" not in api.transcript
    assert api.to(GENERAL) == [f"{COOKING}\nafter the restart"]


@pytest.mark.asyncio
async def test_channels_revives_a_retired_agent_with_a_fresh_allowance_tg82(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A human typing ``/channels`` has conceded the fight, so the bound starts over.

    Carrying the old count forward would retire the new topic after one more deletion, which reads
    as the bot refusing to do the thing it was just asked to do.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    live = cooking
    for attempt in range(MAX_RECREATIONS + 1):
        api.delete_topic(live)
        await bot._say(Channel(CHAT, cooking), f"reply {attempt}", agent_id=COOKING)
        live = api.next_topic_id

    await say(bot, f"/channels {COOKING}")

    row = await store.channel(CHAT, COOKING)
    assert row is not None
    assert (row["retired"], row["recreations"]) == (False, 0)


@pytest.mark.asyncio
async def test_a_revived_channel_actually_receives_its_agents_messages_tg82(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Reviving a retired channel has to make it *work*, not merely make a row say it does.

    TG-82's notice names ``/channels <agent-id>`` as the way out, and a way out that leaves the
    expert talking in General under a prefix is worse than no way out: the human has a fresh topic
    with their expert's name on it, sitting permanently silent, and nothing anywhere says why.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    live = cooking
    for attempt in range(MAX_RECREATIONS + 1):
        api.delete_topic(live)
        await bot._say(Channel(CHAT, cooking), f"reply {attempt}", agent_id=COOKING)
        live = api.next_topic_id
    revived = await channel_for(bot, api, COOKING)
    api.journal.clear()

    await bot._say(Channel(CHAT, revived), "back in business", agent_id=COOKING)

    assert api.to(revived) == ["back in business"]


@pytest.mark.asyncio
async def test_retirement_in_one_chat_does_not_retire_the_agent_in_another_tg82(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Two home chats is a supported arrangement, and every key in this layer is chat-qualified.

    The failure is silent and survives every restart: the second chat's topic exists, the human
    keeps typing into it, and the expert's replies appear in that chat's General with a prefix as
    though the topic had been deleted — which nobody did.
    """
    chats = {CHAT: LIBRARIAN, OTHER_CHAT: LIBRARIAN}
    first = await topical(service, store, api, chats=chats)
    here = await channel_for(first, api, COOKING)
    there = await channel_for(first, api, COOKING, chat_id=OTHER_CHAT)
    live = here
    for attempt in range(MAX_RECREATIONS + 1):
        api.delete_topic(live)
        await first._say(Channel(CHAT, here), f"reply {attempt}", agent_id=COOKING)
        live = api.next_topic_id

    second = await topical(service, store, api, chats=chats)
    api.journal.clear()
    await second._say(Channel(OTHER_CHAT, there), "a note in the other chat", agent_id=COOKING)

    assert [entry["topic_id"] for entry in api.sent] == [there]


# --------------------------------------------------------------------------------------
# § the row is not proof its topic exists (TG-102, TG-103, TG-77 amended)
#
# The human's own sequence, found on the live bot on 2026-08-09: the Cooking channel exists in the
# directory, the human deleted the Cooking topic from their phone, and they tap the Cooking row of
# the `/channels` picker. Before the fix the bot answered "topic/cooking already has a channel in
# this chat (topic 101), so I created nothing" about a topic Telegram had deleted, in a channel
# that was not it. The channel was then unrecoverable from the phone, because TG-82 and TG-83
# repair a dead channel and both are triggered by a **failed send**, while no message can ever
# arrive from a deleted topic (F-5).
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_pointer_into_a_deleted_topic_repairs_the_channel_tg102(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The whole defect, end to end: a durable row is not proof its topic still exists.

    The pointer is delivered **into** the channel it names, so the send raises ``message thread not
    found`` (F-11) and TG-83's repair runs. Nothing else recovers this channel: no update announces
    a deletion, no inbound message can come from a topic that is gone, and the only message that
    named the dead topic used to go to the channel the command was typed in.

    Without the routing this file asserts, every other assertion about the pointer still passes,
    which is how the defect shipped.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    api.missing_thread_errors = True
    api.delete_topic(cooking)

    await say(bot, f"/channels {COOKING}", update_id=2)

    live = int(api.next_topic_id)
    pointer = adapter_module._ALREADY_HERE.format(agent_id=COOKING)
    assert live != cooking
    # The send that carried the dead id is the detection. A build that answers only in the typing
    # channel never makes it, and the repair below never runs.
    assert pointer in api.to(cooking)
    assert len(api.creates) == 2
    assert await store.channels(CHAT) == {live: COOKING}
    assert api.to(live) == [pointer]
    assert adapter_module._REOPENED.format(agent_id=COOKING, title="Cooking") in api.to(GENERAL)


@pytest.mark.asyncio
async def test_the_pointer_for_a_live_channel_creates_nothing_tg102(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """TG-77 is unchanged where it was right: a second channel for one agent is still refused.

    The pointer lands in the channel it names, which is the useful place for it on a live channel:
    the topic is somewhere the human can open, and a topic id is invisible in every Telegram client
    (§9.10 struck exactly that id from TG-74's reply).
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)

    await say(bot, f"/channels {COOKING}", update_id=2)

    assert len(api.creates) == 1
    assert await store.channels(CHAT) == {cooking: COOKING}
    assert api.to(cooking) == [adapter_module._ALREADY_HERE.format(agent_id=COOKING)]
    assert api.texts[-1] == adapter_module._ALREADY.format(
        agent_id=COOKING, where=f"topic {cooking}"
    )


@pytest.mark.asyncio
async def test_a_pointer_typed_in_the_channel_it_names_answers_once_tg77(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Two messages exist because the human is looking somewhere else. Here they are not.

    ``/channels topic/cooking`` typed inside Cooking's own topic gets one line. A second line
    saying "it is over there" about the topic it is printed in is the noise the shape avoids.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    sent = len(api.sent)

    await say(bot, f"/channels {COOKING}", topic_id=cooking, update_id=3)

    assert [entry["text"] for entry in api.sent[sent:]] == [
        adapter_module._ALREADY_HERE.format(agent_id=COOKING)
    ]


@pytest.mark.asyncio
async def test_the_pointer_repairs_a_silently_relocated_channel_too_tg80(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The other shape of the same fact, and the one F-13 says is live on this API.

    ``sendChatAction`` answers ``ok: true`` for a topic Telegram deleted, so a send that succeeds is
    a real way to lose a message. The pointer is checked by TG-80's response comparison exactly as
    every other send is, and the repair it reaches is the same one.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    api.delete_topic(cooking)

    await say(bot, f"/channels {COOKING}", update_id=2)

    live = int(api.next_topic_id)
    assert len(api.creates) == 2
    assert await store.channels(CHAT) == {live: COOKING}
    assert api.to(live) == [adapter_module._ALREADY_HERE.format(agent_id=COOKING)]


@pytest.mark.asyncio
async def test_a_human_request_never_spends_the_recreation_allowance_tg103(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """TG-82's cap bounds a loop, and a thumb is not a loop (decision AJ).

    A person exploring the picker deletes the topic and taps the row three times inside a minute.
    Counted against the cap, the third tap answers with a retirement notice instead of a channel:
    the button stops doing what its label says, on the deployment's own owner, at the moment they
    are learning what the feature does.
    """
    bot = await topical(service, store, api)
    live = await channel_for(bot, api, COOKING)
    api.missing_thread_errors = True

    for attempt in range(MAX_RECREATIONS + 1):
        api.delete_topic(live)
        await say(bot, f"/channels {COOKING}", update_id=10 + attempt)
        live = int(api.next_topic_id)
        row = await store.channel(CHAT, COOKING)
        assert row is not None and row["retired"] is False
        assert row["recreations"] == 1, "the count is cleared by the request that triggered it"

    assert len(api.creates) == MAX_RECREATIONS + 2
    assert "stopped making new ones" not in api.transcript
    assert await store.channels(CHAT) == {live: COOKING}


@pytest.mark.asyncio
async def test_a_request_clears_a_count_the_bot_ran_up_on_its_own_tg103(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Repairs the bot made unattended still count, and the human's request still clears them.

    The clear happens **before** the pointer goes out. Afterwards it would be too late: the repair
    the request triggers would read a count already at the bound and retire the channel, telling the
    human their expert had moved to General by way of the tap meant to bring it back.
    """
    bot = await topical(service, store, api)
    live = await channel_for(bot, api, COOKING)
    api.missing_thread_errors = True
    for attempt in range(MAX_RECREATIONS):
        api.delete_topic(live)
        await bot._say(Channel(CHAT, live), f"reply {attempt}", agent_id=COOKING)
        live = int(api.next_topic_id)
    spent = await store.channel(CHAT, COOKING)
    assert spent is not None and spent["recreations"] == MAX_RECREATIONS

    await say(bot, f"/channels {COOKING}", update_id=20)

    cleared = await store.channel(CHAT, COOKING)
    assert cleared is not None and cleared["recreations"] == 0
    api.delete_topic(live)
    await bot._say(Channel(CHAT, live), "one more reply", agent_id=COOKING)
    repaired = await store.channel(CHAT, COOKING)
    assert repaired is not None and repaired["retired"] is False
    assert repaired["recreations"] == 1
    assert "stopped making new ones" not in api.transcript


@pytest.mark.asyncio
async def test_a_channel_pinned_to_general_by_a_failed_create_is_asked_again_tg104(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A ``createForumTopic`` failure pinned the channel to General for the life of the daemon.

    ``_repair`` writes ``_moved[channel] = channel.general`` and leaves the durable row naming the
    topic. ``_route_out`` reads that mapping before anything else, so every later send is
    re-addressed **before** it can fail: TG-83's trigger never fires for that channel again, the
    pointer TG-102 relies on arrives in General, and ``_POINTER_LOST`` answers by naming the very
    command that just did nothing. Measured before TG-104: two more ``/channels topic/cooking``,
    zero further ``createForumTopic`` calls, two identical lines, and a restart as the only cure.

    The failed request also costs the human nothing durable: ``recreations`` stays at zero, so the
    retry repairs rather than answering with a retirement notice.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    api.missing_thread_errors = True
    api.delete_topic(cooking)
    api.create_error = TelegramError("createForumTopic", 400, "Bad Request: nope")

    await say(bot, f"/channels {COOKING}", update_id=2)

    row = await store.channel(CHAT, COOKING)
    assert row is not None
    assert row["recreations"] == 0 and row["retired"] is False
    assert adapter_module._POINTER_LOST.format(agent_id=COOKING) in api.to(GENERAL)
    assert bot._moved[Channel(CHAT, cooking)] == Channel(CHAT, GENERAL)

    api.create_error = None
    creates = len(api.creates)
    await say(bot, f"/channels {COOKING}", update_id=3)

    live = int(api.next_topic_id)
    assert len(api.creates) == creates + 1, "the pointer has to reach the topic to fail again"
    assert live != cooking
    assert await store.channels(CHAT) == {live: COOKING}
    assert api.to(live) == [adapter_module._ALREADY_HERE.format(agent_id=COOKING)]
    assert adapter_module._REOPENED.format(agent_id=COOKING, title="Cooking") in api.to(GENERAL)
    sent = len(api.sent)
    await bot._say(Channel(CHAT, cooking), "a reply for that expert", agent_id=COOKING)
    assert [entry["topic_id"] for entry in api.sent[sent:]] == [live]


@pytest.mark.asyncio
async def test_the_count_clear_never_writes_a_topic_id_a_repair_moved_tg103(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The clear reads the row and writes it back, and a repair owns the column it writes.

    The two statements are a check-then-act across an await, and this file has shipped that shape
    twice: ``_channel_died``'s ``known`` guard and the picker's double press. An unattended send
    that discovers the same deletion inside the row read finishes its repair first; the clear then
    writes the **dead** topic id back over it, so the directory names a topic Telegram deleted and
    the topic the bot had just created is addressed by nothing. That is §9.12 defect 4's silent
    topic, and this time the repair produces it.

    Driven with the competing send as a real task, because that is how it arrives: the outbox pump
    and every run reply are separate tasks from the one handling a command.
    """
    bot = await topical(service, store, api)
    first = await channel_for(bot, api, COOKING)
    api.missing_thread_errors = True
    api.delete_topic(first)
    await bot._say(Channel(CHAT, first), "an unattended reply", agent_id=COOKING)
    second = int(api.next_topic_id)
    spent = await store.channel(CHAT, COOKING)
    assert spent is not None and spent["recreations"] == 1, "the clear only writes above zero"
    api.delete_topic(second)

    settled = store.channel
    racing: list[asyncio.Task[None]] = []

    async def contended(chat_id: int, agent_id: str) -> Mapping[str, Any] | None:
        row = await settled(chat_id, agent_id)
        if not racing:
            racing.append(
                asyncio.ensure_future(
                    bot._say(Channel(CHAT, second), "one more unattended", agent_id=COOKING)
                )
            )
            for _ in range(20):
                await asyncio.sleep(0.005)
        return row

    store.channel = contended  # type: ignore[method-assign]
    try:
        await say(bot, f"/channels {COOKING}", update_id=50)
    finally:
        store.channel = settled  # type: ignore[method-assign]
        await asyncio.gather(*racing)

    row = await store.channel(CHAT, COOKING)
    assert row is not None
    assert int(row["topic_id"]) not in api.deleted, "the directory names a topic Telegram deleted"
    assert await store.channels(CHAT) == {int(api.next_topic_id): COOKING}


def test_no_method_of_this_api_is_used_as_a_liveness_probe_tg102() -> None:
    """``sendChatAction`` answers ``ok: true`` for a topic Telegram deleted (F-13, executed).

    A control on a topic created minutes earlier answered ``ok: true`` as well, so the call reports
    the same success either way and a check built on it would confirm every dead channel as alive.
    The only thing that proves a topic exists is a send the human can see, which is why the pointer
    goes through the channel rather than beside it.
    """
    source = inspect.getsource(adapter_module)

    assert "chat_action" not in source
    assert not [name for name in dir(BotApi) if "chat_action" in name]


# --------------------------------------------------------------------------------------
# § attribution follows exposure (TG-85, TG-88, TG-89)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_ordinary_reply_in_its_own_channel_carries_no_prefix_tg85(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The topic title above the keyboard already says whose conversation this is.

    Prefixing every reply inside a conversation the human is already in trains them to skip the
    first line — which is exactly where the approval attribution has to be legible when it matters.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    api.journal.clear()

    await say(bot, "the steak note", topic_id=cooking)

    assert api.to(cooking) == ["Filed under Cooking."]


@pytest.mark.asyncio
async def test_a_message_outside_its_agents_channel_leads_with_the_agent_id_tg85(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A topic header is attribution only while you are inside the topic.

    Scrollback in General, a forward and a lock-screen notification preview all strip it, and none
    of them strips a first line. This is the case where the human cannot otherwise tell which expert
    is talking, because General is where everything falls back to.
    """
    bot = await topical(service, store, api)

    await bot._say(Channel(CHAT), "an expert's line", agent_id=COOKING, prefix=True)

    assert api.to(GENERAL) == [f"{COOKING}\nan expert's line"]


# --------------------------------------------------------------------------------------
# § the command surface, per channel (TG-86)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_ends_only_the_channel_it_was_typed_in_task7(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Task 7 successor of ``test_new_rotates_only_the_channel_it_was_typed_in_tg86``: the same
    "a human acting on one topic has said nothing about the others" property, now over ``/close``
    (S-17) rather than the retired ``/new`` — General's session must survive a ``/close`` typed in
    Cooking's own topic untouched.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    await say(bot, "a general note")
    await say(bot, "a cooking note", topic_id=cooking)
    kept = await store.bound_session(CHAT, GENERAL)

    await say(bot, "/close", topic_id=cooking)

    assert await store.bound_session(CHAT, cooking) is None
    assert await store.bound_session(CHAT, GENERAL) == kept


@pytest.mark.asyncio
async def test_cancel_reaches_only_this_channels_run_tg86(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A ``/cancel`` that reaches the wrong turn is worse than no ``/cancel``.

    It stops a turn the human wanted and leaves the one they were trying to stop writing into a tree
    with no undo — and on a phone there is nothing on screen to reveal the mix-up.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    await store.bind(CHAT, GENERAL, "t-general", LIBRARIAN)
    await store.bind(CHAT, cooking, "t-cooking", COOKING)
    api.journal.clear()

    await say(bot, "/cancel", topic_id=cooking)

    attached = [args[0] for name, args in service.calls if name == "attach_session"]
    assert attached == ["t-cooking"]


@pytest.mark.asyncio
async def test_threads_lists_the_agent_of_the_channel_it_was_typed_in_tg86(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """``/threads`` takes no agent argument because the topic is the argument, and it is on screen.

    Listing every agent's threads here would put a Cooking conversation under a Grilling title, and
    the ids are what the TUI is opened with.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    service.rows["t-cook"] = thread_row("t-cook", COOKING)
    service.rows["t-lib"] = thread_row("t-lib", LIBRARIAN)

    await say(bot, "/threads", topic_id=cooking)

    assert "t-cook" in api.texts[-1]
    assert "t-lib" not in api.texts[-1]


@pytest.mark.asyncio
async def test_the_command_surface_is_the_seven_and_offers_no_agent_selector_task7(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Task 7 successor of ``test_the_command_surface_is_six_and_offers_no_agent_selector_tg86``
    (S-15). The exact, bare tuple is pinned once in ``test_telegram.py``
    (``test_the_command_surface_is_exactly_the_seven_s15``); this is the same "no hidden
    agent-selector mode" property, driven through the running bot's own unknown-command fallback
    over the new seven-command set — fix round 1, finding 2.
    """
    bot = await topical(service, store, api)

    await say(bot, "/nope")

    assert set(COMMANDS) == {
        "/channels",
        "/threads",
        "/agents",
        "/cancel",
        "/name",
        "/close",
        "/end",
    }
    assert "/talk" not in api.texts[-1]
    assert "/connect" not in api.texts[-1]
    for command in COMMANDS:
        assert command in api.texts[-1]


@pytest.mark.asyncio
async def test_only_the_two_bootstrap_commands_work_in_an_unbound_topic_tg87(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """``/new`` in a topic that addresses nobody has nothing to rotate.

    Answering it as though it did would be the modal addressing TG-1 deletes — and the useful reply
    is the one that tells the human how to make the topic mean something.
    """
    bot = await topical(service, store, api)

    await say(bot, "/new", topic_id=606)
    await say(bot, "/agents", update_id=2, topic_id=606)

    assert "/channels" in api.to(606)[0]
    assert api.to(606)[1].splitlines()[0] == "This topic is not connected to an expert yet."


# --------------------------------------------------------------------------------------
# § mechanics that must not be assumed (TG-90 … TG-94)
# --------------------------------------------------------------------------------------


def test_no_edit_or_answer_call_takes_a_topic_tg90() -> None:
    """The obvious assumption is the opposite, and acting on it 400s the disarm call.

    ``editMessageReplyMarkup`` addresses a message by ``chat_id`` + ``message_id``, which are unique
    within a chat whichever topic the message sits in — and the disarm runs inside a ``finally``,
    where a 400 is reported as a shutdown bug and the buttons stay live.
    """
    for name in ("edit_message", "clear_keyboard", "answer_callback"):
        parameters = set(inspect.signature(getattr(BotApi, name)).parameters)
        assert "topic_id" not in parameters
        assert "message_thread_id" not in parameters


def test_allowed_updates_did_not_change_for_topics_tg91() -> None:
    """The reflex on a new Telegram feature is to subscribe to a new update kind.

    Here that would mean subscribing to something that does not exist while implying the deletion
    case is covered. It is not covered and **cannot** be (F-5), which is precisely why TG-80 reads
    the send response instead.
    """
    assert ALLOWED_UPDATES == ("message", "edited_message", "callback_query")


@pytest.mark.asyncio
async def test_creating_a_topic_by_hand_gets_an_offer_not_an_attachment_refusal_tg92(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """As built, a ``message`` with no ``text`` fell into TG-36's refusal.

    So the human's own first act after enabling Threaded Mode — creating a topic — would have been
    answered *"I can only read text; I have not downloaded the attachment"* about a photo they did
    not send. And a hand-made topic is inert until something tells them how to bind it.
    """
    bot = await topical(service, store, api)

    await deliver(
        bot,
        message_update(
            topic_id=707,
            text=None,
            extra={"forum_topic_created": {"name": "Cooking"}},
        ),
    )

    assert "start_run" not in kinds(journal)
    assert "I can only read text" not in api.transcript
    assert len(api.to(707)) == 1 and "/channels" in api.to(707)[0]


@pytest.mark.asyncio
async def test_the_other_topic_service_messages_are_silent_tg92(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A close, an edit and a reopen were not sent by a human, so answering them answers Telegram.

    Every one of them would otherwise produce a message in the chat, which on a phone is a
    notification for something the human just did themselves.
    """
    bot = await topical(service, store, api)

    for index, key in enumerate(
        ("forum_topic_closed", "forum_topic_reopened", "forum_topic_edited")
    ):
        await deliver(
            bot,
            message_update(update_id=index + 1, topic_id=707, text=None, extra={key: {}}),
        )

    assert api.sent == []


@pytest.mark.asyncio
async def test_a_captioned_attachment_of_an_unlisted_kind_is_still_a_humans_tg92(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """The "is this Telegram or a human?" test is a **list**, and a list is never finished.

    ``_MEDIA_KEYS`` names the attachment kinds that existed when TG-92 was written. Telegram keeps
    adding them — ``paid_media``, ``checklist``, ``invoice`` — and a kind that is not on the list
    made a message with no ``text`` indistinguishable from Telegram's own bookkeeping, so TG-36's
    refusal was skipped and the human's attachment vanished **with no reply at all**. Silence is the
    one answer that reads as a bot that is down rather than as a bot that does not take files, on
    the surface whose whole promise is that what you send is either filed or refused out loud.

    A caption is the human typing, so it settles the question without anyone maintaining the list.
    """
    bot = await topical(service, store, api)

    await deliver(
        bot,
        message_update(
            text=None,
            extra={"paid_media": {"star_count": 1}, "caption": "the receipt for the steak"},
        ),
    )

    assert "start_run" not in kinds(journal), "TG-36: nothing is downloaded and nothing runs"
    assert "I can only read text" in api.transcript
    assert "the receipt for the steak" in api.transcript, "TG-22: the human's own words come back"


@pytest.mark.asyncio
async def test_a_turn_in_one_topic_does_not_block_another_tg93(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A single 284-second local-fallback turn would otherwise freeze every expert on the phone.

    The lock exists because three lines typed as three messages in **one** conversation must not
    become three ``ThreadBusyError`` refusals. Nothing in that argument reaches across topics, and
    left per chat the failure has no visible cause at all.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    grilling = await channel_for(bot, api, GRILLING)

    held = bot._channel_lock(Channel(CHAT, cooking))
    await held.acquire()
    try:
        blocked = asyncio.create_task(bot._dispatch(message_update(topic_id=cooking)))
        free = asyncio.create_task(bot._dispatch(message_update(update_id=2, topic_id=grilling)))
        for _ in range(200):
            if free.done():
                break
            await asyncio.sleep(0)

        assert free.done(), "one topic's turn must not wait on another's"
        assert not blocked.done()
    finally:
        held.release()
        for task in (blocked, free):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    await drain(bot)


@pytest.mark.asyncio
async def test_the_outbox_is_one_queue_for_the_whole_chat_tg94(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Telegram's roughly one-message-per-second budget is a **chat** limit (F-7).

    Topics multiply the appearance of independence without multiplying the budget, so the tempting
    fix — a pump per topic — buys nothing and earns 429s whose ``retry_after`` stalls the approval
    messages too.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    grilling = await channel_for(bot, api, GRILLING)

    await bot._queue(Channel(CHAT, cooking), "one", COOKING)
    await bot._queue(Channel(CHAT, grilling), "two", GRILLING)
    await bot._queue(Channel(CHAT), "three", LIBRARIAN)
    assert bot._outbox.qsize() == 3, "one queue, not one per channel"
    await drain(bot)

    assert api.texts[-3:] == ["one", "two", "three"], "one pump preserves one order"


# --------------------------------------------------------------------------------------
# § a description that never arrived carries no buttons (TG-56, TG-80)
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# § the channel carries the expert's name (TG-105, TG-106, TG-78 amended)
# --------------------------------------------------------------------------------------

HUMAN_TOPIC = 101
"""The topic the human made in their own Telegram client, where Telegram called it *New Chat*.

Masked, as every id in this file is: the repository is public. The number matches §11's, which
stands for the same object for the same reason.
"""

BOUND_AND_NAMED = (
    "Bound this topic to topic/cooking and named it Cooking. Nothing was created. Anything you "
    "send here from now on goes to that expert."
)
"""Golden text (TG-105). The title has to be in the reply, so the human learns the name changed in
the same second rather than by noticing their tab later and doubting their client."""


@pytest.mark.asyncio
async def test_binding_a_hand_made_topic_names_it_after_the_expert_tg105(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The human's exact sequence, and the one the live bot got wrong.

    They made the topic in their own client, where Telegram named it *New Chat*, then bound it with
    ``/channels topic/cooking`` from inside it (TG-87's bind-here form, the recovery path for a lost
    database). The bind wrote the row and left the name, so the tab said *New Chat* and decision AE
    printed no agent id on any ordinary reply in it — a conversation with an unnamed expert that
    writes to a tree with no undo, which is the ambiguity TG-1 deleted ``/connect`` for.
    """
    bot = await topical(service, store, api)

    await say(bot, f"/channels {COOKING}", topic_id=HUMAN_TOPIC)

    assert api.creates == [], "the bind creates nothing, which is how the name got left behind"
    assert api.renames == [{"chat_id": CHAT, "topic_id": HUMAN_TOPIC, "name": "Cooking"}]
    assert api.names[HUMAN_TOPIC] == "Cooking", "the tab the human reads"
    assert dict(await store.channels(CHAT)) == {HUMAN_TOPIC: COOKING}
    assert api.texts[-1] == BOUND_AND_NAMED


@pytest.mark.asyncio
async def test_creating_a_channel_names_the_topic_and_renames_nothing_tg105(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The create path already names what it makes, so a rename there is a second call for nothing.

    This is the assertion a later change drops silently: a build that renamed after every ownership
    event would pass the bind test above and send two calls where one is needed, and the second one
    is the call TG-78 spent a section refusing.
    """
    bot = await topical(service, store, api)

    await say(bot, f"/channels {COOKING}")

    assert [entry["name"] for entry in api.creates] == ["Cooking"]
    assert api.renames == [], "createForumTopic carried the title; a rename would be policing"
    assert api.names[api.next_topic_id] == "Cooking"


@pytest.mark.asyncio
async def test_nothing_after_the_bind_renames_the_channel_again_tg105(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Naming once is ownership; naming again is policing, and the failure is silent.

    A human who renames the channel has made a decision and TG-78's original instinct holds from
    that moment. Without this, their chosen title reverts on a schedule they cannot see — on the
    next message, or on a restart.
    """
    bot = await topical(service, store, api)
    await say(bot, f"/channels {COOKING}", topic_id=HUMAN_TOPIC)
    api.journal.clear()

    await say(bot, "where does the steak note go?", topic_id=HUMAN_TOPIC)
    await say(bot, f"/channels {COOKING}", topic_id=HUMAN_TOPIC)
    await boot(bot)

    assert api.renames == [], "an ordinary message, a second /channels and a restart rename nothing"
    assert api.creates == [], "TG-77's pointer still creates nothing"


@pytest.mark.asyncio
async def test_a_failed_rename_leaves_the_channel_bound_and_says_so_tg106(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The bind is the thing the human asked for and it must survive the rename failing.

    The durable write runs first, so this is a channel that works. Rolling the bind back would
    answer the one recovery path for a lost database (F-5) with nothing, and reporting the rename as
    done would leave the human reading *New Chat* under a line claiming the topic is called
    *Cooking* — after which the next thing they doubt is the binding.
    """
    api.rename_error = TelegramError("editForumTopic", 500, "Internal Server Error")
    bot = await topical(service, store, api)

    await say(bot, f"/channels {COOKING}", topic_id=HUMAN_TOPIC)

    assert dict(await store.channels(CHAT)) == {HUMAN_TOPIC: COOKING}, "the binding stands"
    assert api.texts[-1] == (
        "Bound this topic to topic/cooking. Anything you send here from now on goes to that "
        "expert.\n"
        "I could not name it Cooking: editForumTopic failed: 500 Internal Server Error\n"
        "The binding stands. You can rename the topic yourself."
    )
    assert "named it" not in api.texts[-1], "a reply that claims the name is a reply that lies"


@pytest.mark.asyncio
async def test_a_rename_that_names_a_dead_topic_never_repairs_tg106(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A rename delivers nothing, so its failure is not TG-83's trigger and must not reach it.

    Repairing here would give an agent a second channel one line after it got its first — TG-77's
    split history, produced by the fix for a missing title — and it would be the second recovery
    path §11.5 refuses. The reply carries the reason and the bot stops there.
    """
    api.rename_error = TelegramError("editForumTopic", 400, "Bad Request: message thread not found")
    bot = await topical(service, store, api)

    await say(bot, f"/channels {COOKING}", topic_id=HUMAN_TOPIC)

    assert api.creates == [], "a failed rename never recreates a topic"
    assert dict(await store.channels(CHAT)) == {HUMAN_TOPIC: COOKING}
    assert bot._route_out(Channel(CHAT, HUMAN_TOPIC), COOKING) == Channel(CHAT, HUMAN_TOPIC), (
        "no channel was marked dead by a call that delivered nothing"
    )
    assert "message thread not found" in api.texts[-1]


@pytest.mark.asyncio
async def test_a_rename_that_answers_for_a_deleted_topic_proves_nothing_tg106(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """F-13's shape: one method of this API answers ``ok: true`` for a topic Telegram deleted.

    So a rename that answers is not evidence the topic is alive, and nothing is recorded from it.
    The **send** under it is what finds the deletion, exactly as TG-102 says, and the repair that
    runs is TG-83's, reached from the send it has always been reached from. The journal order is the
    assertion: rename, then send, then create. A build that read the rename's answer as a liveness
    check would produce the same three calls in the wrong order, or skip the repair outright.
    """
    bot = await topical(service, store, api)
    api.missing_thread_errors = True
    api.delete_topic(HUMAN_TOPIC)

    await say(bot, f"/channels {COOKING}", topic_id=HUMAN_TOPIC)

    assert api.renames == [{"chat_id": CHAT, "topic_id": HUMAN_TOPIC, "name": "Cooking"}]
    order = [kind for kind, _ in api.journal if kind in {"edit_forum_topic", "create_forum_topic"}]
    assert order == ["edit_forum_topic", "create_forum_topic"], "the send found it, not the rename"
    assert len(api.creates) == 1, "one repair, from TG-83, and none from the rename"


@pytest.mark.asyncio
async def test_two_binds_of_one_agent_at_once_name_one_topic_tg105(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The fourth check-then-act in this file would be the rename, so it gets the same test.

    Three have shipped here already: the deleted-topic repair, two picker presses making two topics,
    and two concurrent ``/channels`` for one agent. The bind reads the directory, writes the row and
    then renames, which is a decision carried across three awaits. Both callers taking the bind
    branch would name two topics for one agent and leave the second row's write to the unique index,
    so the human would read *Cooking* on a topic nothing routes to.

    :attr:`_creations` is what makes this one caller, and the second caller answers with TG-77's
    pointer because it reads the directory the first one wrote.
    """
    bot = await topical(service, store, api)

    await asyncio.gather(
        bot._dispatch(message_update(1, topic_id=HUMAN_TOPIC, text=f"/channels {COOKING}")),
        bot._dispatch(message_update(2, topic_id=HUMAN_TOPIC + 1, text=f"/channels {COOKING}")),
    )

    assert api.renames == [{"chat_id": CHAT, "topic_id": HUMAN_TOPIC, "name": "Cooking"}]
    assert api.creates == [], "the second caller creates nothing, so it names nothing"
    assert dict(await store.channels(CHAT)) == {HUMAN_TOPIC: COOKING}


@pytest.mark.asyncio
async def test_a_stranger_cannot_name_a_topic_tg105(
    service: TopicService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The rename is a write to the human's chat, so it sits behind the one boundary this layer has.

    ``editForumTopic`` changes something every client displays, and the allow-list (TG-20, decision
    X) is the only authentication in the deployment. A stranger who found the bot's username could
    otherwise retitle the human's topics one command at a time, and nothing in the chat would say who
    did it.
    """
    bot = await topical(service, store, api)

    await say(bot, f"/channels {COOKING}", topic_id=HUMAN_TOPIC, sender=STRANGER)

    assert api.renames == []
    assert dict(await store.channels(CHAT)) == {}


# --------------------------------------------------------------------------------------
# § TelegramChannelNotifier — S-16's retitle fan-out over the real BotApi surface (Task 7)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_notifier_sends_one_edit_forum_topic_call_s16(api: FakeBotApi) -> None:
    """The concrete half of ``RuntimeService.rename_session``'s fan-out (S-16): one
    ``editForumTopic`` per Telegram channel, over the same fake-transport-visible ``BotApi`` every
    other send in this module goes through."""
    notifier = TelegramChannelNotifier(api)

    await notifier.retitle("telegram:770001:71", "Sear Timing")

    assert api.renames == [{"chat_id": 770001, "topic_id": 71, "name": "Sear Timing"}]


@pytest.mark.asyncio
async def test_the_notifier_is_a_silent_no_op_for_general_and_other_transports_s18(
    api: FakeBotApi,
) -> None:
    """General has no forum topic to retitle under this Bot API surface (no ``setChatTitle`` call
    exists here), and a non-Telegram ref (a future TUI's ``"tui:…"``) is not this notifier's to
    act on — both are recorded honestly by doing nothing, never by raising (S-18)."""
    notifier = TelegramChannelNotifier(api)

    await notifier.retitle("telegram:770001:0", "Sear Timing")  # General
    await notifier.retitle("tui:client-a", "Sear Timing")  # a different transport's namespace

    assert api.renames == []


@pytest.mark.asyncio
async def test_a_dead_topic_is_logged_and_never_raised_tg106(api: FakeBotApi) -> None:
    """TG-106's rule one layer up, restated for the fan-out: "a failure is a reason, never a
    repair." The caller (``RuntimeService.rename_session``) has already committed the store rename
    and moved the file — a retitle failing here must not be allowed to look like the rename itself
    failed, so a dead topic's ``editForumTopic`` error is swallowed rather than propagated."""
    notifier = TelegramChannelNotifier(api)
    api.rename_error = TelegramError("editForumTopic", 400, "message thread not found")

    await notifier.retitle("telegram:770001:71", "Sear Timing")  # does not raise

    assert [entry for kind, entry in api.journal if kind == "edit_forum_topic"] == [
        {"chat_id": 770001, "topic_id": 71, "name": "Sear Timing"}
    ]


# --------------------------------------------------------------------------------------
# § /name and /end as real Telegram commands (S-16, S-22; Task 7 fix round 1, finding 3)
# --------------------------------------------------------------------------------------
#
# Driven through the real ``RuntimeService`` (the ``real_service`` fixture), never
# ``TopicService``/``StubService``: the whole point is that a mutation turning ``_rename``/``_end``
# into no-ops — as the round 1 reviewer's did — leaves the store unrenamed, the file unsealed and no
# retitle sent, all of which only a real service can tell apart from success.


@pytest.mark.asyncio
async def test_name_renames_and_retitles_the_channel_it_was_typed_in_task7(
    real_service: RuntimeService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """``/name``'s happy path, proven end to end: the store renames the session, and the retitle
    actually reaches the Bot API for the channel ``/name`` was typed in — S-16's fan-out, composed
    inside ``RuntimeService`` and wired through the real ``TelegramChannelNotifier``, not something
    ``telegram.py``'s own command handler does by hand.
    """
    real_service.notifier = TelegramChannelNotifier(api)
    bot = await topical(real_service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    await say(bot, "a rub that doesn't burn", topic_id=cooking)

    await say(bot, "/name Sear Timing", topic_id=cooking)

    session_id = await store.bound_session(CHAT, cooking)
    assert session_id is not None
    session = await real_service.get_session(session_id)
    assert session.name == "sear-timing"
    assert api.renames == [{"chat_id": CHAT, "topic_id": cooking, "name": "sear-timing"}]
    assert "Renamed" in api.to(cooking)[-1]


@pytest.mark.asyncio
async def test_name_refuses_a_collision_with_a_reply_not_a_crash_task7(
    real_service: RuntimeService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """``/name``'s refusal path: a name collision (S-16, ``SessionNameTakenError``) must reach the
    chat as a plain reply, not crash the turn or leave the store or the file touched."""
    await real_service.create_session(COOKING, name="brisket")
    bot = await topical(real_service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    await say(bot, "a rub for the other cut", topic_id=cooking)
    session_id = await store.bound_session(CHAT, cooking)
    assert session_id is not None

    await say(bot, "/name brisket", topic_id=cooking)

    assert "Could not rename" in api.to(cooking)[-1]
    session = await real_service.get_session(session_id)
    assert session.name != "brisket", "the collision must not have gone through"
    assert api.renames == [], "a refused rename sends no retitle"


@pytest.mark.asyncio
async def test_rename_with_no_notifier_configured_still_commits_task7(
    real_service: RuntimeService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A TUI-only deployment never assigns ``RuntimeService.notifier`` (it stays ``None`` — see
    that attribute's own docstring), and Task 7's fan-out loop is guarded by ``if self.notifier is
    not None`` for exactly that deployment. Fix round 1, finding 5: nothing exercised the guard
    itself, so a reviewer who deleted it and let the loop call ``None.retitle(...)`` was never
    caught — the per-channel ``try/except`` around that call swallows the resulting
    ``AttributeError`` either way, so the rename still commits and the chat still sees success; the
    only externally visible difference the guard makes is that nothing is even attempted, so nothing
    is logged. The rename committing is asserted directly; the log assertion is what actually falls
    over without the guard (see also the route-level sibling in ``test_session_routes.py``, the more
    literal reading of this finding — "a TUI-only deployment" reaches sessions over the API, not
    through this transport at all).
    """
    assert real_service.notifier is None
    bot = await topical(real_service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    await say(bot, "a rub that doesn't burn", topic_id=cooking)
    session_id = await store.bound_session(CHAT, cooking)
    assert session_id is not None

    with caplog.at_level("WARNING", logger="pkb.service.runtime"):
        await say(bot, "/name Sear Timing", topic_id=cooking)

    session = await real_service.get_session(session_id)
    assert session.name == "sear-timing"
    assert api.renames == [], "no notifier, no send — and no crash either"
    assert "Renamed" in api.to(cooking)[-1]
    assert "could not retitle" not in caplog.text, (
        "the guard must skip the fan-out entirely, not attempt it and swallow the failure"
    )


@pytest.mark.asyncio
async def test_end_seals_the_file_and_unbinds_the_channel_from_closed_task7(
    real_service: RuntimeService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """``/end``'s happy path (S-22): legal from ``closed``, seals the file, and this transport's
    own local binding is cleared so the very next message opens a fresh session rather than
    reactively discovering the sealed one (:meth:`TelegramAdapter._end`'s own docstring)."""
    bot = await topical(real_service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    await say(bot, "a rub that doesn't burn", topic_id=cooking)
    session_id = await store.bound_session(CHAT, cooking)
    assert session_id is not None
    await real_service.close_session(session_id)

    await say(bot, "/end", topic_id=cooking)

    session = await real_service.get_session(session_id)
    assert session.state == "ended"
    assert session.ended_at is not None
    assert await store.bound_session(CHAT, cooking) is None
    assert "sealed" in api.to(cooking)[-1]


@pytest.mark.asyncio
async def test_end_refuses_an_open_session_with_the_409_class_reply_task7(
    real_service: RuntimeService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """``/end`` on a session still ``open`` is refused (S-22: legal only from ``closed``) — the
    chat gets the same class of error ``IllegalSessionTransitionError`` maps to over HTTP (409),
    worded as a plain reply, and nothing about the session or the binding changes."""
    bot = await topical(real_service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    await say(bot, "a rub that doesn't burn", topic_id=cooking)
    session_id = await store.bound_session(CHAT, cooking)
    assert session_id is not None

    await say(bot, "/end", topic_id=cooking)

    assert "Could not end" in api.to(cooking)[-1]
    session = await real_service.get_session(session_id)
    assert session.state == "open"
    assert await store.bound_session(CHAT, cooking) == session_id, "an open session stays bound"


# --------------------------------------------------------------------------------------
# Small local helpers, kept beside the tests that need them
# --------------------------------------------------------------------------------------


def _stream(events: Sequence[AgentEvent]) -> Any:
    """A subscription whose stream simply yields and stops — no hub, no supervisor."""

    async def generate() -> Any:
        for event in events:
            yield event

    return RunSubscription(
        handle=RunHandle(run_id=RUN, agent_id=LIBRARIAN, thread_id="t-parent"),
        events=generate(),
        close=lambda: None,
    )


def _park(service: TopicService, thread_id: str, agent_id: str) -> None:
    """One approval parked on one expert's own thread, as ``/pending`` finds it."""
    service.rows[thread_id] = thread_row(thread_id, agent_id, pending="i-1")
    service.details[thread_id] = ThreadDetail(
        thread=service.rows[thread_id],
        messages=(),
        pending=approval(thread_id=thread_id, agent_id=agent_id),
        children=(),
    )
