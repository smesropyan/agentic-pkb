"""The deleted-topic hazard, end to end (TG-80 … TG-85).

This file exists because of one measured property of the Bot API and nothing else: **a send into a
deleted topic of a private chat succeeds**. Telegram answers ``ok: true``, drops the
``message_thread_id`` it was given and delivers the message to General (tdlib/telegram-bot-api#854).
No exception is raised, no update announces the deletion, and no method enumerates a chat's topics —
so an adapter that watches only for errors detects nothing, forever.

What that costs, concretely, is the failure TG-1 was ruled against and topics were supposed to
retire: the human deletes the Cooking topic, and the bot keeps posting Cooking's replies and
Cooking's **approval keyboards** into the general chat, where they are indistinguishable from the
Librarian's. An Approve button for an irreversible write, under the wrong expert's name, on a system
with no undo (D6). Every assertion below is a station on the path between that send and that button.

Four things are therefore asserted here that no other file can assert:

* **Detection is from the response, not from an exception** (TG-80). The fake never raises on the
  silent path, so a test that passes here cannot be passing because something threw.
* **Absence is General, not "unknown"** (TG-80). Telegram omits ``message_thread_id`` entirely for a
  General message, which is exactly what a stray looks like — so the naive comparison
  ``response.get("message_thread_id") != sent`` is right by accident for the stray and reports a
  phantom deletion on **every** General message.
* **Disarm, explain, repair — in that order** (TG-81). A message is dangerous only while its buttons
  are live, and the very response that revealed the problem carries the ``message_id`` needed to
  kill them. Repairing first leaves an Approve button live in General for as long as
  ``createForumTopic`` takes, and forever if it fails.
* **The dead id is never used twice** (TG-84) and the repair is bounded (TG-82) — because a human
  deleting a topic in anger must not produce one ``createForumTopic`` and one notification per frame
  of a fan-out.

The fake models Telegram's behaviour rather than the adapter's expectations: ``send_message`` into a
topic that is not in :attr:`FakeBotApi.topics` returns a ``Message`` with **no** ``message_thread_id``
at all, and only in ``missing_thread`` mode does it raise the ``400`` that Bot API 10.0 reportedly
returns instead (tdlib/telegram-bot-api#847). The store is the real
:class:`~pkb.service.telegram.SqliteTelegramStore` over a real ``aiosqlite`` connection opened the
way the daemon opens it, because the recreation bound is durable on purpose: an in-memory count
hands a human who deleted a topic three times a fresh allowance of two on every restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio

from pkb.contracts import (
    ActionView,
    AgentEvent,
    ApprovalRequest,
    InterruptEvent,
    MessageComplete,
    RunEnd,
)
from pkb.server.telegram import (
    Channel,
    TelegramAdapter,
    TelegramConfig,
    utf16_len,
)
from pkb.server.telegram_api import (
    GENERAL,
    MAX_RECREATIONS,
    MESSAGE_LIMIT,
    POLL_TIMEOUT,
    BotApi,
    TelegramError,
)
from pkb.service.telegram import SqliteTelegramStore
from tests.server.stub import COOKING, LIBRARIAN, StubService

pytestmark = pytest.mark.asyncio

CHAT = 770001
"""The one mapped chat. Its **General** area answers as the Librarian (TG-73)."""

OWNER = 987654321
"""A fictional Telegram user id. The repo is public; no real id ever goes in a file."""

COOK_TOPIC = 71
"""The topic the human made for Cooking — and the one they are about to delete."""

RUN = "run-1"

DIFF = (
    "--- a/topics/Cooking/notes/steak.md\n"
    "+++ b/topics/Cooking/notes/steak.md\n"
    "@@ -1,3 +1,4 @@\n"
    "-rest for 5 minutes\n"
    "+rest for 8 minutes\n"
)

Journal = list[tuple[str, dict[str, Any]]]
"""Every Bot API call in the order it happened.

One journal rather than one list per method, because TG-81 is entirely a statement about the
**order** of three calls to three different Telegram methods, and per-method lists cannot say which
came first.
"""


# --------------------------------------------------------------------------------------
# The fake — Telegram's behaviour, not the adapter's expectations
# --------------------------------------------------------------------------------------


@dataclass
class Delivered:
    """One message as it actually exists in the chat: where it landed, and whether it is armed."""

    chat_id: int
    topic_id: int
    text: str
    message_id: int
    armed: bool


@dataclass
class FakeBotApi:
    """A ``BotApi`` whose topics can be deleted, and which lies about it exactly as Telegram does.

    The whole point is :meth:`send_message`'s silent branch. A send naming a topic that is no longer
    in :attr:`topics` is **accepted**: the message is delivered to General and the returned
    ``Message`` simply has no ``message_thread_id``. That is the measured behaviour of a private
    chat (F-2, tdlib#854) and it is why nothing here raises unless ``missing_thread`` is set.

    A fake that raised on the ordinary path would make every test below pass against an adapter that
    only ever inspects exceptions — i.e. against the bug this file exists to prevent.
    """

    journal: Journal = field(default_factory=list)
    topics: dict[int, set[int]] = field(default_factory=dict)
    delivered: list[Delivered] = field(default_factory=list)
    next_message_id: int = 5000
    next_topic_id: int = 100
    topics_enabled: bool = True
    missing_thread: bool = False
    """HAZARD 2: Bot API 10.0 reportedly answers ``400 message thread not found`` where 9.3 silently
    relocated (tdlib#847). Same fact, so TG-83 requires the same handling — with no stray to clean
    up, because nothing was delivered."""

    create_error: BaseException | None = None
    kill_after: int | None = None
    """Delete every topic in the chat once this many topic-addressed sends have **succeeded**.

    The human does not delete a topic between two of the bot's turns; they delete it while one is
    running, which on the local fallback is 284 seconds long. ``kill_after=1`` is how an approval's
    description gets delivered and its *keyboard* becomes the stray — the exact shape of the hazard.
    """

    _topic_sends: int = 0

    # -- the Protocol ------------------------------------------------------------------

    async def get_me(self) -> Mapping[str, Any]:
        """TG-75: ``has_topics_enabled`` is on ``getMe`` and on nothing else."""
        self.journal.append(("get_me", {}))
        return {"id": 1, "username": "pkb_test_bot", "has_topics_enabled": self.topics_enabled}

    async def get_updates(
        self, offset: int | None, *, timeout: int = POLL_TIMEOUT
    ) -> Sequence[Mapping[str, Any]]:
        await asyncio.sleep(0)
        self.journal.append(("get_updates", {"offset": offset, "timeout": timeout}))
        return []

    async def create_forum_topic(self, chat_id: int, name: str) -> Mapping[str, Any]:
        self.journal.append(("create_forum_topic", {"chat_id": chat_id, "name": name}))
        if self.create_error is not None:
            raise self.create_error
        self.next_topic_id += 1
        self.topics.setdefault(chat_id, set()).add(self.next_topic_id)
        return {"message_thread_id": self.next_topic_id, "name": name}

    async def edit_forum_topic(self, chat_id: int, topic_id: int, name: str) -> None:
        """TG-105. Recorded so the hazard tests can assert a repair renames nothing (TG-106)."""
        self.journal.append(
            ("edit_forum_topic", {"chat_id": chat_id, "topic_id": topic_id, "name": name})
        )

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: Sequence[Sequence[Mapping[str, str]]] | None = None,
        topic_id: int = GENERAL,
    ) -> Mapping[str, Any]:
        self.journal.append(
            ("send_message", {"chat_id": chat_id, "text": text, "kb": keyboard, "topic": topic_id})
        )
        return self._deliver(chat_id, text, topic_id, armed=bool(keyboard))

    async def send_document(
        self,
        chat_id: int,
        filename: str,
        content: bytes,
        caption: str = "",
        *,
        topic_id: int = GENERAL,
    ) -> Mapping[str, Any]:
        self.journal.append(
            (
                "send_document",
                {"chat_id": chat_id, "filename": filename, "caption": caption, "topic": topic_id},
            )
        )
        return self._deliver(chat_id, f"[document {filename}]", topic_id, armed=False)

    async def answer_callback(
        self, callback_id: str, text: str = "", *, show_alert: bool = False
    ) -> None:
        self.journal.append(("answer_callback", {"id": callback_id, "text": text}))

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        self.journal.append(
            ("edit_message", {"chat_id": chat_id, "message_id": message_id, "text": text})
        )

    async def clear_keyboard(self, chat_id: int, message_id: int) -> None:
        """No ``topic_id``, deliberately — TG-90/F-6, and the signature is the assertion.

        ``editMessageReplyMarkup`` addresses a message by ``chat_id`` + ``message_id``. An adapter
        that passed a thread id here would raise ``TypeError`` inside the ``finally`` that disarms an
        irreversible button, which is worse than the ``400`` the real API would answer with.
        """
        self.journal.append(("clear_keyboard", {"chat_id": chat_id, "message_id": message_id}))
        for message in self.delivered:
            if message.chat_id == chat_id and message.message_id == message_id:
                message.armed = False

    # -- the wire behaviour under test -------------------------------------------------

    def _deliver(self, chat_id: int, text: str, topic_id: int, *, armed: bool) -> Mapping[str, Any]:
        live = self.topics.get(chat_id, set())
        if topic_id != GENERAL and topic_id not in live:
            if self.missing_thread:
                raise TelegramError("sendMessage", 400, "Bad Request: message thread not found")
            # F-2: accepted, relocated to General, and the response is the only witness.
            topic_id = GENERAL
        self.next_message_id += 1
        self.delivered.append(Delivered(chat_id, topic_id, text, self.next_message_id, armed))
        message: dict[str, Any] = {"message_id": self.next_message_id, "chat": {"id": chat_id}}
        if topic_id != GENERAL:
            # Telegram omits the key entirely in General. Emitting a `0` here would let an adapter
            # comparing `response.get(...)` to the sent id pass while being wrong about General.
            message["message_thread_id"] = topic_id
            self._topic_sends += 1
            if self.kill_after is not None and self._topic_sends >= self.kill_after:
                self.topics[chat_id] = set()
        return message

    # -- what the tests read -----------------------------------------------------------

    def of(self, name: str) -> list[dict[str, Any]]:
        return [entry for kind, entry in self.journal if kind == name]

    def order(self, *names: str) -> list[str]:
        """The journal reduced to the calls under test, in order — TG-81 is an ordering rule."""
        return [kind for kind, _ in self.journal if kind in names]

    def landed_in(self, topic_id: int) -> list[Delivered]:
        return [message for message in self.delivered if message.topic_id == topic_id]

    def texts_in(self, topic_id: int) -> list[str]:
        return [message.text for message in self.landed_in(topic_id)]

    @property
    def sends(self) -> list[dict[str, Any]]:
        return self.of("send_message")

    @property
    def creates(self) -> list[dict[str, Any]]:
        return self.of("create_forum_topic")

    @property
    def cleared(self) -> list[dict[str, Any]]:
        return self.of("clear_keyboard")


@dataclass
class FakeHealth:
    """The ``/health`` telegram block, reduced to what the hazard path is allowed to touch."""

    last_error: str | None = None
    agents: frozenset[str] = frozenset()
    channels: int = 0
    retired_channels: tuple[str, ...] = ()
    topics_enabled: bool = False
    restarts: int = 0
    send_errors: list[str] = field(default_factory=list)

    def poll_ok(self) -> None:  # pragma: no cover - not exercised by the hazard path
        pass

    def send_failed(self, exc: BaseException) -> None:
        self.send_errors.append(f"{type(exc).__name__}: {exc}")


class ScriptedService(StubService):
    """A :class:`StubService` that reports into the shared journal and scripts one run's events."""

    def __init__(self, journal: Journal, *, events: Sequence[AgentEvent] = ()) -> None:
        super().__init__(events=list(events))
        self.journal = journal


# --------------------------------------------------------------------------------------
# Fixtures — a real store, on a real connection, opened the way the daemon opens it
# --------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def connection(tmp_path: Path) -> Any:
    """ST-1: autocommit and WAL, exactly as ``pkb.daemon`` opens the checkpointer's file."""
    handle = await aiosqlite.connect(tmp_path / "pkb.sqlite", isolation_level=None)
    await handle.execute("PRAGMA journal_mode=WAL")
    try:
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
    return FakeBotApi(journal, topics={CHAT: {COOK_TOPIC}})


@pytest.fixture
def service(journal: Journal) -> ScriptedService:
    return ScriptedService(journal, events=reply_script())


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def reply_script(text: str = "Filed under Cooking.") -> list[AgentEvent]:
    return [
        MessageComplete(run_id=RUN, agent_id=COOKING, text=text),
        RunEnd(run_id=RUN, final_text=text),
    ]


def approval_script(request: ApprovalRequest) -> list[AgentEvent]:
    """A run that parks on an approval — the frame whose keyboard must never go astray."""
    return [InterruptEvent(run_id=RUN, request=request)]


def action(*, description: str = DIFF, reason: str = "breadth-approval") -> ActionView:
    return ActionView(
        tool="write_file",
        args={"file_path": "topics/Cooking/notes/steak.md"},
        description=description,
        allowed_decisions=("approve", "reject"),
        reason=reason,
    )


def approval(
    *,
    thread_id: str = "t-cooking-1",
    interrupt_id: str = "i-1",
    agent_id: str = COOKING,
    actions: tuple[ActionView, ...] = (),
) -> ApprovalRequest:
    return ApprovalRequest(
        interrupt_id=interrupt_id,
        agent_id=agent_id,
        thread_id=thread_id,
        actions=actions or (action(),),
    )


def message_update(
    update_id: int = 1,
    *,
    topic_id: int = COOK_TOPIC,
    text: str = "where does the steak note go?",
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": update_id,
        "chat": {"id": CHAT, "type": "private"},
        "from": {"id": OWNER},
        "text": text,
    }
    if topic_id != GENERAL:
        message["message_thread_id"] = topic_id
    return {"update_id": update_id, "message": message}


async def make_bot(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    *,
    health: FakeHealth | None = None,
    channel_for: str | None = COOKING,
    topic_id: int = COOK_TOPIC,
) -> TelegramAdapter:
    """A bot with topic mode probed the way ``run()`` probes it, and one channel in the directory.

    ``_probe_topics`` and ``_load_directory`` are the two startup steps ``run()`` awaits before any
    child task exists, so calling them here is not a shortcut around the adapter — it is the same
    two calls, without a poll loop the hazard tests do not need.
    """
    bot = TelegramAdapter(
        service=service,
        store=store,
        api=api,
        config=TelegramConfig(
            token="123456789:AA-fake",
            chats={CHAT: LIBRARIAN},
            owner_user_ids=frozenset({OWNER}),
        ),
        health=health,
    )
    if channel_for is not None:
        await store.open_channel(CHAT, topic_id, channel_for)
    await bot._probe_topics()
    await bot._load_directory()
    return bot


async def drain(bot: TelegramAdapter) -> None:
    """Run the outbox pump until it is empty — TG-49's queue is not a rule under test here."""
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


def kill_topics(api: FakeBotApi) -> None:
    """The human long-presses the topic and taps Delete. Nothing tells the bot."""
    api.topics[CHAT] = set()


# `PROMPTS_TABLE`'s old name (Task 6, DESIGN.md §2.10): the constant is deleted with the
# approval-prompt surface; this helper is read only by a `@pytest.mark.superseded` test.
_PROMPTS_TABLE = "pkb_telegram_prompts"


async def prompt_message_ids(connection: aiosqlite.Connection) -> list[int]:
    cursor = await connection.execute(f"SELECT message_ids FROM {_PROMPTS_TABLE}")
    rows = await cursor.fetchall()
    return [message_id for row in rows for message_id in json.loads(str(row[0]))]


# --------------------------------------------------------------------------------------
# TG-80 — the response is the only witness
# --------------------------------------------------------------------------------------


async def test_a_stray_send_is_detected_with_nothing_raised_tg80(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The fake never raises here, so detection can only have come from the response.

    This is the whole premise. Telegram accepts the send, ignores the dead ``message_thread_id`` and
    answers ``ok: true``; an adapter written around exceptions sees a completely successful send and
    keeps posting one expert's traffic under another's name until someone notices by eye.
    """
    bot = await make_bot(service, store, api)
    kill_topics(api)

    await bot._say(Channel(CHAT, COOK_TOPIC), "Filed under Cooking.", agent_id=COOKING)

    assert api.creates, "the deletion went undetected: no repair was attempted"
    assert not any(isinstance(entry, TelegramError) for entry in api.journal)


async def test_a_general_send_is_never_read_as_a_deletion_tg80(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Absence of ``message_thread_id`` means General, not "gone" — and General cannot be deleted.

    The naive comparison is ``response.get("message_thread_id") != sent``, which is accidentally
    right for a stray and wrong for every ordinary General message, where ``None != 0``. That
    version reports a phantom deletion on the Librarian's every reply: a correction line, a
    ``createForumTopic``, and a recreation burned, on a chat where nothing happened.
    """
    bot = await make_bot(service, store, api)

    await bot._say(Channel(CHAT, GENERAL), "Filed under the Librarian.", agent_id=LIBRARIAN)

    assert not api.creates, "a General send was mistaken for a deleted topic"
    assert not api.cleared
    assert api.texts_in(GENERAL) == ["Filed under the Librarian."]


async def test_the_check_runs_on_an_ordinary_reply_not_only_on_approvals_tg80(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Unconditional, because the message that reveals the deletion is whichever one is next.

    Checking only approvals would leave the *first* stray — an ordinary reply — undetected, and the
    approval that follows it goes to the same dead id: the keyboard reaches General anyway, one
    message later, with the bot having had every chance to notice.
    """
    bot = await make_bot(service, store, api)
    kill_topics(api)

    await bot._say(Channel(CHAT, COOK_TOPIC), "Filed under Cooking.", agent_id=COOKING)

    assert api.creates, "an unarmed reply did not trigger the check"


async def test_the_message_is_re_sent_whole_and_not_only_its_tail_tg80(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A split message whose first part strayed is re-sent **entire** into the repaired channel.

    The parts already delivered went to General under the wrong expert's name. A human reading the
    repaired topic needs the whole message; re-sending only the undelivered tail leaves the first
    half of an answer in a different conversation, which is how a note gets filed from a sentence
    the human never saw in context.
    """
    bot = await make_bot(service, store, api)
    kill_topics(api)
    long_text = "steak. " * 900
    assert utf16_len(long_text) > MESSAGE_LIMIT

    await bot._say(Channel(CHAT, COOK_TOPIC), long_text, agent_id=COOKING)

    repaired = api.creates[0] and api.next_topic_id
    parts = api.texts_in(repaired)
    assert len(parts) == 2, f"the whole message was not re-sent: {len(parts)} part(s)"
    whole = "".join(part.split("\n", 1)[-1] for part in parts)
    assert whole == long_text, "the repaired topic received a fragment, not the message"


# --------------------------------------------------------------------------------------
# TG-81 — disarm, explain, repair, in that order
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
async def test_a_stray_approval_keyboard_is_disarmed_before_anything_else_tg81(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The order **is** the rule: ``clear_keyboard`` first, then the correction, then the repair.

    Repairing first means a ``createForumTopic`` that is slow — or that fails, which is the case the
    ordering was chosen for — leaves an Approve button for an irreversible write live in General,
    attributed by nothing, for however long that takes. The response that revealed the stray already
    carries the ``message_id``, so disarming costs one call that cannot fail for want of information.

    Superseded (Task 6 rebuilds this): the whole scenario is ``_post_approval`` posting a keyboard —
    with no gates, no writes are ever parked behind an Approve button, so nothing here is armed and
    there is nothing to disarm. The deleted-topic hazard on an *ordinary* reply is what survives (see
    the TG-80 tests above); this file needs no successor for the disarm-first ordering itself.
    """
    bot = await make_bot(service, store, api)
    api.kill_after = 1  # the description lands; the keyboard is the stray

    await bot._post_approval(Channel(CHAT, COOK_TOPIC), approval())

    steps = api.order("clear_keyboard", "create_forum_topic")
    assert steps[:2] == ["clear_keyboard", "create_forum_topic"], steps
    correction = next(i for i, entry in enumerate(api.journal) if "deleted" in str(entry[1]))
    disarm = next(i for i, entry in enumerate(api.journal) if entry[0] == "clear_keyboard")
    create = next(i for i, entry in enumerate(api.journal) if entry[0] == "create_forum_topic")
    assert disarm < correction < create


@pytest.mark.superseded
async def test_the_disarmed_message_is_the_one_the_response_named_tg81(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The stray's own ``message_id``, off the send response — no lookup, no guess, no topic.

    Clearing anything else leaves the live button live. This is also why TG-90 matters here: the
    ``message_id`` is enough, so nothing on this path needs the topic that has just ceased to exist.

    Superseded (Task 6 rebuilds this): with no gates there is no armed keyboard for a stray to carry,
    so nothing needs disarming — see the sibling test above.
    """
    bot = await make_bot(service, store, api)
    api.kill_after = 1

    await bot._post_approval(Channel(CHAT, COOK_TOPIC), approval())

    stray = next(m for m in api.delivered if m.topic_id == GENERAL and m.text.startswith(COOKING))
    assert api.cleared == [{"chat_id": CHAT, "message_id": stray.message_id}]
    assert not stray.armed, "an Approve button is still live in the general chat"


@pytest.mark.superseded
async def test_no_message_delivered_to_the_general_chat_keeps_its_buttons_tg81(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The property, stated over the whole chat rather than over one call.

    A per-call assertion passes if the adapter disarms the first stray and not the second. What has
    to be true at the end of the incident is that **nothing** in General carries a button that was
    meant for a topic — because in General, under the Librarian's name, that button is
    indistinguishable from the Librarian's own work.

    Superseded (Task 6 rebuilds this): with no gates nothing the bot ever sends carries a button, so
    the property holds vacuously and needs no successor.
    """
    bot = await make_bot(service, store, api)
    api.kill_after = 1

    await bot._post_approval(Channel(CHAT, COOK_TOPIC), approval())

    assert [m.text for m in api.landed_in(GENERAL) if m.armed] == []


@pytest.mark.superseded
async def test_the_correction_names_the_expert_and_the_dead_buttons_tg81(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A message with dead buttons and no explanation reads as a bug in the bot.

    The chat is the only surviving record of what the human was asked, so the correction has to say
    three things: whose topic went, that the message above is the one that went astray, and that its
    buttons no longer work. Without the last one the human presses a dead Approve and learns nothing.

    Superseded (Task 6 rebuilds this): the "buttons no longer work" clause is about an approval
    keyboard that no longer exists. The surviving principle — a stray ordinary reply is named and
    explained — is covered by the TG-80/TG-81 tests that use ``_say`` instead of ``_post_approval``.
    """
    bot = await make_bot(service, store, api)
    api.kill_after = 1

    await bot._post_approval(Channel(CHAT, COOK_TOPIC), approval())

    correction = next(m.text for m in api.landed_in(GENERAL) if "has been deleted" in m.text)
    assert COOKING in correction
    assert "buttons no longer work" in correction


async def test_the_correction_is_posted_in_general_and_carries_no_topic_tg81(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The correction explains a topic that no longer exists, so it cannot be posted in it.

    It also must not go through the checked send path: it is the message that *handles* the failure,
    and General is the one destination with no id that can go stale.
    """
    bot = await make_bot(service, store, api)
    kill_topics(api)

    await bot._say(Channel(CHAT, COOK_TOPIC), "Filed under Cooking.", agent_id=COOKING)

    corrections = [entry for entry in api.sends if "has been deleted" in str(entry["text"])]
    assert corrections and all(entry["topic"] == GENERAL for entry in corrections)


@pytest.mark.superseded
async def test_the_stray_text_is_never_deleted_tg81(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Buttons die; the text stands (decision AD). ``delete_message`` is not on the Protocol at all.

    On a system with no undo the chat is the human's only record of what they were asked to approve.
    A message that vanishes tells them nothing and reads as a bug; one with dead buttons and a
    correction under it tells them exactly what happened. The Protocol assertion is the durable half
    — a method that does not exist cannot be called by a later change.

    Superseded (Task 6 rebuilds this): mixed body — the static ``not hasattr(BotApi,
    "delete_message")`` half is a durable Protocol fact independent of approvals and could stand
    alone, but the second assertion depends on ``_post_approval`` having delivered the diff via a
    stray keyboard message, which no longer happens. Marked whole because the two share one test; the
    "no undo means the chat is the record, so nothing is ever deleted" principle survives and needs a
    successor against an ordinary stray reply.
    """
    bot = await make_bot(service, store, api)
    api.kill_after = 1

    await bot._post_approval(Channel(CHAT, COOK_TOPIC), approval())

    assert not hasattr(BotApi, "delete_message")
    assert any(DIFF.splitlines()[0] in m.text for m in api.landed_in(GENERAL))


@pytest.mark.superseded
async def test_clearing_a_keyboard_never_takes_a_topic_tg90(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """F-6: only the send family carries ``message_thread_id``. The obvious assumption is the wrong

    one, and acting on it puts an unknown parameter on the single call that disarms an irreversible
    button — inside a ``finally``, where the failure is silent. The fake's own signature is the
    enforcement: a ``topic_id=`` here would be a ``TypeError`` mid-incident.

    Superseded (Task 6 rebuilds this): mixed body — the static Protocol-signature checks are generic
    Bot API surface and would stand alone, but the whole reason ``clear_keyboard`` is ever called is
    the approval-keyboard disarm path this file drives via ``_post_approval``, which is retired.
    Marked whole; if a picker keyboard (Task 7) still calls ``clear_keyboard`` on a topic hazard, that
    needs its own test rather than a revival of this one.
    """
    for method in ("clear_keyboard", "edit_message", "answer_callback"):
        assert "topic_id" not in inspect.signature(getattr(BotApi, method)).parameters
    for method in ("send_message", "send_document"):
        assert "topic_id" in inspect.signature(getattr(BotApi, method)).parameters

    bot = await make_bot(service, store, api)
    api.kill_after = 1
    await bot._post_approval(Channel(CHAT, COOK_TOPIC), approval())
    assert api.cleared, "the incident did not reach the disarm path at all"


# --------------------------------------------------------------------------------------
# TG-80/TG-82 — the repair, and where the approval ends up
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
async def test_the_approval_is_re_sent_into_the_new_topic_with_its_keyboard_tg82(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Repair is not cosmetic: the approval has to arrive somewhere the human can act on it.

    Q20 rejected the outcome where an expert's approvals simply become undeliverable, so a repaired
    channel that receives the text but not the buttons is the same failure with extra steps — a
    parked interrupt that RT-39 then uses to refuse every later message in the chat.

    Superseded (Task 6 rebuilds this): there is no parked interrupt and no keyboard to lose in a
    repair — every write lands immediately. The surviving principle, "a repaired channel has to
    receive the content that strayed, not just a marker that it strayed," is covered for ordinary
    replies by the TG-80 tests using ``_say``.
    """
    bot = await make_bot(service, store, api)
    api.kill_after = 1

    await bot._post_approval(Channel(CHAT, COOK_TOPIC), approval())

    new_topic = api.next_topic_id
    armed = [m for m in api.landed_in(new_topic) if m.armed]
    assert len(armed) == 1, api.texts_in(new_topic)
    assert DIFF.splitlines()[0] in "\n".join(api.texts_in(new_topic))


@pytest.mark.superseded
async def test_the_prompt_row_records_the_repaired_message_not_the_stray_tg63(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    connection: aiosqlite.Connection,
) -> None:
    """TG-63 clears the keyboards the row remembers, so the row must remember the live one.

    If the stray's id were recorded instead, the press that answers this approval would clear a
    keyboard that was already dead and leave the real one armed — an Approve button for a write that
    already happened, sitting in the human's Cooking topic for as long as they scroll back.

    Superseded (Task 6 rebuilds this): ``PROMPTS_TABLE`` is the durable record of a pending
    approval's live message id, which is exactly the parked-interrupt bookkeeping Task 6 removes —
    there is no press to answer later because nothing is ever parked.
    """
    bot = await make_bot(service, store, api)
    api.kill_after = 1

    await bot._post_approval(Channel(CHAT, COOK_TOPIC), approval())

    recorded = await prompt_message_ids(connection)
    live = [m.message_id for m in api.landed_in(api.next_topic_id) if m.armed]
    assert recorded == live, "the durable row points at the wrong message"


async def test_the_directory_follows_the_repair_and_counts_it_tg82(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The new topic id is durable, and so is the fact that one recreation has been spent.

    Both halves matter after a restart: without the id the next send goes back to the dead topic and
    strays again; without the count the bound is reset by every bounce of the daemon, which turns
    TG-82 into no bound at all.
    """
    bot = await make_bot(service, store, api)
    kill_topics(api)

    await bot._say(Channel(CHAT, COOK_TOPIC), "Filed under Cooking.", agent_id=COOKING)

    row = await store.channel(CHAT, COOKING)
    assert row is not None
    assert row["topic_id"] == api.next_topic_id != COOK_TOPIC
    assert row["recreations"] == 1
    assert row["retired"] is False


@pytest.mark.superseded
async def test_the_recreated_topic_routes_inbound_to_the_same_expert_tg77(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A repair the human can talk to. The end-to-end proof that the channel is really back.

    A directory row that moved but does not route means the human's next message in the new Cooking
    topic is answered with "this topic is not connected to an expert yet" — a repair that looks like
    one and is not.

    Superseded (Task 3/7 rebuild this): the assertion pins a single ``create_thread(agent_id,
    origin_channel="telegram")`` call — the old shape where an inbound channel message directly
    stamps and creates a thread. Task 7 replaces it with two steps, ``create_session`` (no channel
    argument) plus a separate ``attach(session_id, channel_ref)``, so this exact call recording has
    no successor as written. The underlying principle — an inbound message in a *repaired* topic must
    still reach the right expert, not "not connected" — survives and needs a new assertion against
    the session-and-attach shape.
    """
    bot = await make_bot(service, store, api)
    kill_topics(api)
    await bot._say(Channel(CHAT, COOK_TOPIC), "Filed under Cooking.", agent_id=COOKING)
    new_topic = api.next_topic_id

    await deliver(bot, message_update(2, topic_id=new_topic))

    created = [arguments for call, arguments in service.calls if call == "create_thread"]
    assert created == [(COOKING, "telegram")], "the repaired topic reached the wrong expert"
    binding = await store.binding(CHAT, new_topic)
    assert binding is not None and binding[1] == COOKING


# --------------------------------------------------------------------------------------
# TG-83 — the 400 variant (HAZARD 2, tdlib#847)
# --------------------------------------------------------------------------------------


async def test_message_thread_not_found_takes_the_repair_path_tg83(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Bot API 10.0 reportedly errors where 9.3 relocated. Same fact, so the same handling.

    Treating it as an ordinary send failure would leave the channel pointing at a dead topic and
    every later message failing identically — the expert goes quiet with a ``last_send_error`` and
    nothing else, which is the undeliverable-approvals outcome Q20 rejected.
    """
    bot = await make_bot(service, store, api)
    api.missing_thread = True
    kill_topics(api)

    await bot._say(Channel(CHAT, COOK_TOPIC), "Filed under Cooking.", agent_id=COOKING)

    assert len(api.creates) == 1
    assert api.texts_in(api.next_topic_id) == ["Filed under Cooking."]


@pytest.mark.superseded
async def test_message_thread_not_found_leaves_nothing_to_disarm_or_correct_tg83(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The identical path **minus** the stray cleanup: this 400 means nothing was delivered.

    A correction line pointing at "the message above this one" when no message went astray tells the
    human their approval landed in General when it did not — sending them to look for a keyboard
    that is exactly where it should be.

    Superseded (Task 6 rebuilds this): built on ``_post_approval``, and ``api.cleared == []`` is
    asserting the absence of a keyboard-clear that no longer exists as a concept once gates are gone.
    The 400-means-nothing-delivered principle for an ordinary reply is covered by TG-83's other two
    tests, which use ``_say``.
    """
    bot = await make_bot(service, store, api)
    api.missing_thread = True
    api.kill_after = 1

    await bot._post_approval(Channel(CHAT, COOK_TOPIC), approval())

    assert api.cleared == []
    assert [m.text for m in api.landed_in(GENERAL) if "has been deleted" in m.text] == []


async def test_message_thread_not_found_is_never_retried_tg83(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """400 is outside ``RETRY_CODES``, and that is load-bearing rather than incidental.

    Retrying re-issues the same dead id to the retry bound: three 400s per message, forever, with
    the backoff between them delaying every other message in the chat's one-per-second budget.
    """
    bot = await make_bot(service, store, api)
    api.missing_thread = True
    kill_topics(api)

    await bot._say(Channel(CHAT, COOK_TOPIC), "Filed under Cooking.", agent_id=COOKING)

    to_dead_topic = [entry for entry in api.sends if entry["topic"] == COOK_TOPIC]
    assert len(to_dead_topic) == 1, f"the dead id was re-issued {len(to_dead_topic)} times"


# --------------------------------------------------------------------------------------
# TG-84 — a dead id is never used twice
# --------------------------------------------------------------------------------------


async def test_a_dead_channel_is_never_addressed_with_its_stale_id_again_tg84(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """One deletion, one stray, one correction — however many frames follow it.

    A fan-out at model pace queues eight frames. Without the re-addressing map each one strays,
    each one is corrected, and the human gets eight "the topic has been deleted" messages
    interleaved with eight relocated replies at exactly the moment something needs approving.
    """
    bot = await make_bot(service, store, api)
    kill_topics(api)
    channel = Channel(CHAT, COOK_TOPIC)

    for index in range(8):
        await bot._say(channel, f"frame {index}", agent_id=COOKING)

    assert len(api.creates) == 1
    assert len([entry for entry in api.sends if entry["topic"] == COOK_TOPIC]) == 1
    assert len([m for m in api.landed_in(GENERAL) if "has been deleted" in m.text]) == 1


async def test_a_queued_message_for_a_dead_channel_is_re_addressed_not_dropped_tg84(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Re-addressed, never dropped and never sent blind — all three, in one drain of the outbox.

    TG-49 already forbids dropping a reply; the topic hazard adds the third option, which is the
    tempting one: quietly send it to the old id anyway. That is how eight strays happen.
    """
    bot = await make_bot(service, store, api)
    kill_topics(api)
    channel = Channel(CHAT, COOK_TOPIC)
    for index in range(3):
        await bot._queue(channel, f"frame {index}", COOKING)

    await drain(bot)

    assert sorted(api.texts_in(api.next_topic_id)) == ["frame 0", "frame 1", "frame 2"]


# --------------------------------------------------------------------------------------
# TG-82 — the bound, and what happens past it
# --------------------------------------------------------------------------------------


async def test_repair_stops_after_two_recreations_and_retires_the_channel_tg82(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Bounded, because unbounded recreation is a fight with a human deleting a topic on purpose.

    Each round of that fight costs a ``createForumTopic`` and a notification on their phone. Past
    the bound the traffic goes to General with the expert's name on it — still deliverable, which
    is the half Q20 insisted on, and no longer a loop.
    """
    bot = await make_bot(service, store, api)
    channel = Channel(CHAT, COOK_TOPIC)

    for _ in range(MAX_RECREATIONS + 1):
        kill_topics(api)
        await bot._say(channel, "Filed under Cooking.", agent_id=COOKING)

    assert len(api.creates) == MAX_RECREATIONS
    row = await store.channel(CHAT, COOKING)
    assert row is not None and row["retired"] is True
    assert row["recreations"] == MAX_RECREATIONS


async def test_the_retirement_is_announced_exactly_once_tg82(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Told once. A notice per deletion is a notification loop aimed at someone already annoyed.

    It also has to name the way back — ``/channels <agent-id>`` — because a human who has conceded
    the fight and wants their topic again otherwise has nothing on screen telling them how.
    """
    bot = await make_bot(service, store, api)
    channel = Channel(CHAT, COOK_TOPIC)
    for _ in range(MAX_RECREATIONS + 1):
        kill_topics(api)
        await bot._say(channel, "Filed under Cooking.", agent_id=COOKING)

    await bot._say(channel, "another reply", agent_id=COOKING)
    await bot._say(channel, "and another", agent_id=COOKING)

    notices = [m.text for m in api.landed_in(GENERAL) if "stopped making new ones" in m.text]
    assert len(notices) == 1
    assert f"/channels {COOKING}" in notices[0]


async def test_a_retired_channel_carries_its_agent_id_on_the_first_line_tg85(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Attribution follows exposure. In General the topic title that attributed this is gone.

    A lock-screen preview, a forward and General's own scrollback all show the first line and
    nothing else, so a reply from Cooking arriving in General unprefixed is a reply the human will
    read as the Librarian's.
    """
    bot = await make_bot(service, store, api)
    channel = Channel(CHAT, COOK_TOPIC)
    for _ in range(MAX_RECREATIONS + 1):
        kill_topics(api)
        await bot._say(channel, "Filed under Cooking.", agent_id=COOKING)

    await bot._say(channel, "the next reply", agent_id=COOKING)

    exiled = next(m.text for m in api.landed_in(GENERAL) if m.text.endswith("the next reply"))
    assert exiled.splitlines()[0] == COOKING


async def test_the_recreation_bound_survives_a_restart_tg82(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The count is read from the durable row before the create, not from process memory.

    ``_supervise`` restarts this task carrying nothing across, so an in-memory bound hands a human
    deleting a topic in anger a fresh allowance of two on every bounce — and the bot's restart rate
    goes up while they are doing it.
    """
    first = await make_bot(service, store, api)
    for _ in range(MAX_RECREATIONS):
        kill_topics(api)
        await first._say(Channel(CHAT, COOK_TOPIC), "Filed under Cooking.", agent_id=COOKING)
    live_topic = api.next_topic_id
    creates_before = len(api.creates)

    second = await make_bot(service, store, api, channel_for=None)
    kill_topics(api)
    await second._say(Channel(CHAT, live_topic), "Filed under Cooking.", agent_id=COOKING)

    assert len(api.creates) == creates_before, "a restart handed back a fresh allowance"
    row = await store.channel(CHAT, COOKING)
    assert row is not None and row["retired"] is True


async def test_a_retirement_seeded_from_the_store_needs_no_second_notice_tg82(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A restarted bot must not re-announce a retirement the human was already told about.

    ``_load_directory`` seeds the retired set from the store for exactly this reason: a flag a
    restart clears is a notice they get again on every bounce, which is how the notice that matters
    gets ignored.
    """
    first = await make_bot(service, store, api)
    for _ in range(MAX_RECREATIONS + 1):
        kill_topics(api)
        await first._say(Channel(CHAT, COOK_TOPIC), "Filed under Cooking.", agent_id=COOKING)
    notices_before = len([m for m in api.landed_in(GENERAL) if "stopped making new ones" in m.text])

    second = await make_bot(service, store, api, channel_for=None)
    await second._say(Channel(CHAT, COOK_TOPIC), "a reply after the restart", agent_id=COOKING)

    notices = [m for m in api.landed_in(GENERAL) if "stopped making new ones" in m.text]
    assert len(notices) == notices_before == 1
    assert not any(entry["topic"] == COOK_TOPIC for entry in api.sends[-1:])


async def test_a_failed_recreation_falls_back_to_general_rather_than_losing_the_message_tg82(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """``createForumTopic`` can fail — Threaded Mode off, a 429, a dropped tether.

    The message is already half-delivered to General at this point, so the one thing that must not
    happen is that the re-send is abandoned: the human would be left with a correction line saying
    "I am re-sending it where it belongs" and no re-sent message anywhere.
    """
    bot = await make_bot(service, store, api)
    api.create_error = TelegramError(
        "createForumTopic", 400, "Bad Request: the chat is not a forum"
    )
    kill_topics(api)

    await bot._say(Channel(CHAT, COOK_TOPIC), "Filed under Cooking.", agent_id=COOKING)

    exiled = [m.text for m in api.landed_in(GENERAL) if m.text.endswith("Filed under Cooking.")]
    assert len(exiled) == 2, "the message was not re-sent after the failed recreation"
    assert exiled[-1].splitlines()[0] == COOKING


# --------------------------------------------------------------------------------------
# The document half — an approval's description is the only place the whole text exists
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
async def test_a_stray_document_is_corrected_and_follows_the_repaired_channel_tg80(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """An oversized description goes as a file, and a file that lands in General is half an approval.

    The keyboard would then be in the topic and the only complete copy of the write in General — the
    human approves what they can see, which is a 1,200-character preview of a diff whose remainder
    is in another conversation.

    Superseded (Task 6 rebuilds this): the whole scenario is an approval's oversized action
    description sent as a document alongside its keyboard, via ``_post_approval``; there is no
    approval and no keyboard left to split across a stray and a repair. An oversized *document* in an
    ordinary reply going astray has no test of its own here and would need one if that path exists
    post-refactor.
    """
    bot = await make_bot(service, store, api)
    kill_topics(api)
    huge = action(description="+ line of a very long diff\n" * 400)
    assert utf16_len(huge.description) > MESSAGE_LIMIT

    await bot._post_approval(Channel(CHAT, COOK_TOPIC), approval(actions=(huge,)))

    new_topic = api.next_topic_id
    documents = api.of("send_document")
    assert [entry["topic"] for entry in documents] == [COOK_TOPIC, new_topic]
    assert any(m.armed for m in api.landed_in(new_topic))


# --------------------------------------------------------------------------------------
# Defects found while writing this file, since fixed — regressions now
# --------------------------------------------------------------------------------------


async def test_an_agentless_stray_still_repairs_the_channel_it_died_in_tg82(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A restart's orphan notice is the message most likely to find a topic deleted.

    It is sent by ``_report_orphans`` into the channel that lost the message, with no agent id,
    before anything else the bot does — which is precisely when a topic deleted during the outage is
    discovered. TG-82 bounds repair at two recreations; this path performs zero, and then blocks the
    ones that would have been attributable.
    """
    bot = await make_bot(service, store, api)
    kill_topics(api)

    await bot._announce(Channel(CHAT, COOK_TOPIC), "I lost a message you sent. Please re-send it.")
    await bot._say(Channel(CHAT, COOK_TOPIC), "Filed under Cooking.", agent_id=COOKING)

    assert api.creates, "the channel was never repaired"
    row = await store.channel(CHAT, COOKING)
    assert row is not None and row["topic_id"] != COOK_TOPIC


async def test_a_stale_reference_re_addresses_rather_than_creating_a_second_topic_tg84(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """TG-84's rule stated the other way round: a repaired channel is only repaired once.

    Every durable row that names a topic — a prompt row, a ledger row — keeps naming the dead one
    after a repair, and ``_moved`` is process memory that a supervised restart does not carry. So
    the ordinary way to reach this is a bounce, which is also the moment TG-31 re-sends approval
    keyboards. The directory is the authority on where an agent is reached now, and it already says
    the right answer at the moment the second topic is created.
    """
    first = await make_bot(service, store, api)
    kill_topics(api)
    await first._say(Channel(CHAT, COOK_TOPIC), "Filed under Cooking.", agent_id=COOKING)
    repaired = api.next_topic_id

    second = await make_bot(service, store, api, channel_for=None)
    await second._say(Channel(CHAT, COOK_TOPIC), "after the restart", agent_id=COOKING)

    assert len(api.creates) == 1, "a live topic was abandoned for a freshly created one"
    row = await store.channel(CHAT, COOKING)
    assert row is not None and row["topic_id"] == repaired
    assert "after the restart" in api.texts_in(repaired)


@pytest.mark.superseded
async def test_an_exiled_approval_names_its_agent_once_not_twice_tg85(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The retired-to-General path is where the two attribution mechanisms meet, and both fire.

    Superseded (Task 6 rebuilds this): built on ``_post_approval`` and checks the armed message's
    attribution specifically; the retired-channel-attribution principle itself (TG-85) survives and
    is covered for ordinary replies by ``test_a_retired_channel_carries_its_agent_id_on_the_first_line_tg85``
    above, which uses ``_say``.
    """
    bot = await make_bot(service, store, api)
    channel = Channel(CHAT, COOK_TOPIC)
    for _ in range(MAX_RECREATIONS + 1):
        kill_topics(api)
        await bot._say(channel, "Filed under Cooking.", agent_id=COOKING)

    await bot._post_approval(channel, approval())

    exiled = next(m.text for m in api.landed_in(GENERAL) if m.armed)
    assert exiled.splitlines()[0] == COOKING
    assert exiled.splitlines()[1] != COOKING, exiled.splitlines()[:2]


# --------------------------------------------------------------------------------------
# Two frames, one deletion (TG-82, TG-84) — the check-then-act the whole section rests on
# --------------------------------------------------------------------------------------


@dataclass
class SlowBotApi(FakeBotApi):
    """A fake whose ``sendMessage`` **suspends**, the way a network round trip does.

    Every other fake in this file completes a send with no ``await`` that yields, so two overlapping
    sends can never actually overlap and the adapter is only ever tested against itself running
    alone. That is not the deployment: :meth:`~pkb.server.telegram.TelegramAdapter._pump_outbox` is
    its own child of the task group and a run emits its reply and its ``InterruptEvent`` from
    another, so a send is in flight while a second one starts on the same channel as a matter of
    course. The one-line ``sleep`` is what lets a test see it.
    """

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: Sequence[Sequence[Mapping[str, str]]] | None = None,
        topic_id: int = GENERAL,
    ) -> Mapping[str, Any]:
        await asyncio.sleep(0.01)
        return await super().send_message(chat_id, text, keyboard=keyboard, topic_id=topic_id)


@pytest.fixture
def slow_api(journal: Journal) -> SlowBotApi:
    return SlowBotApi(journal, topics={CHAT: {COOK_TOPIC}})


async def test_one_deletion_creates_one_topic_however_many_frames_find_it_tg82(
    service: ScriptedService, store: SqliteTelegramStore, slow_api: SlowBotApi
) -> None:
    """``_moved`` is written two awaits after it is read, so the guard was a check-then-act.

    Two frames overlapping on one channel is the ordinary path, not an exotic one, and both used to
    reach ``createForumTopic``. One deletion then cost **both** of ``MAX_RECREATIONS``, so the next
    deletion — the human's second, on a topic they had deleted once — retired the channel outright
    and sent that expert to General forever.
    """
    bot = await make_bot(service, store, slow_api)
    channel = Channel(CHAT, COOK_TOPIC)
    kill_topics(slow_api)

    await asyncio.gather(
        bot._say(channel, "Filed under Cooking.", agent_id=COOKING),
        bot._say(channel, "And the sear goes in the notes.", agent_id=COOKING),
    )

    assert len(slow_api.creates) == 1, slow_api.creates
    row = await store.channel(CHAT, COOKING)
    assert row is not None and row["recreations"] == 1, row


async def test_neither_frame_is_left_in_a_topic_the_directory_does_not_name_tg84(
    service: ScriptedService, store: SqliteTelegramStore, slow_api: SlowBotApi
) -> None:
    """A second ``createForumTopic`` abandons the first new topic *with a message already in it*.

    The human is left looking at two topics carrying their expert's title. One of them holds the
    reply they were waiting for and is in the directory under nobody, so every message they type
    there is answered with TG-74's "this topic is not connected to an expert" — a channel that
    exists, is named after their expert, and can never be talked to.
    """
    bot = await make_bot(service, store, slow_api)
    channel = Channel(CHAT, COOK_TOPIC)
    kill_topics(slow_api)

    await asyncio.gather(
        bot._say(channel, "Filed under Cooking.", agent_id=COOKING),
        bot._say(channel, "And the sear goes in the notes.", agent_id=COOKING),
    )

    row = await store.channel(CHAT, COOKING)
    assert row is not None
    live = int(row["topic_id"])
    delivered = {message.topic_id for message in slow_api.delivered}
    assert delivered <= {GENERAL, COOK_TOPIC, live}, delivered


async def test_a_repair_race_does_not_strand_the_conversation_on_a_dead_topic_tg82(
    service: ScriptedService, store: SqliteTelegramStore, slow_api: SlowBotApi
) -> None:
    """``_carry_binding`` carries once; the second repair finds nothing left to carry.

    So the thread followed the *first* new topic and the directory named the *second*, and the
    human's next message in the topic the bot was actually using opened a brand-new conversation
    while the old one still held the approval they had just been shown. That is decision S's
    amnesiac bot produced by the repair itself, and there is no undo (D6).
    """
    bot = await make_bot(service, store, slow_api)
    channel = Channel(CHAT, COOK_TOPIC)
    await store.bind(CHAT, COOK_TOPIC, "t-cooking-1", COOKING)
    kill_topics(slow_api)

    await asyncio.gather(
        bot._say(channel, "Filed under Cooking.", agent_id=COOKING),
        bot._say(channel, "And the sear goes in the notes.", agent_id=COOKING),
    )

    row = await store.channel(CHAT, COOKING)
    assert row is not None
    carried = await store.binding(CHAT, int(row["topic_id"]))
    assert carried == ("t-cooking-1", COOKING), carried
    # And in exactly one place. Two repairs each carried the binding into their own new topic, so
    # the thread was reachable from a topic the directory does not name — a conversation the human
    # can open, type in, and be answered "not connected to an expert" from.
    reachable = [
        topic_id
        for topic_id in (COOK_TOPIC, *range(101, 110))
        if await store.bound_session(CHAT, topic_id) == "t-cooking-1"
    ]
    assert reachable == [int(row["topic_id"])], reachable


async def test_two_frames_finding_one_deletion_post_one_correction_tg84(
    service: ScriptedService, store: SqliteTelegramStore, slow_api: SlowBotApi
) -> None:
    """TG-84's own words — one correction, not eight — held only for frames that arrived after
    the first had finished repairing. Frames that overlap it are the common case, and each posted
    its own."""
    bot = await make_bot(service, store, slow_api)
    channel = Channel(CHAT, COOK_TOPIC)
    kill_topics(slow_api)

    await asyncio.gather(
        bot._say(channel, "Filed under Cooking.", agent_id=COOKING),
        bot._say(channel, "And the sear goes in the notes.", agent_id=COOKING),
    )

    corrections = [m for m in slow_api.landed_in(GENERAL) if "has been deleted" in m.text]
    assert len(corrections) == 1, [m.text for m in corrections]


async def test_a_correction_long_enough_to_split_still_carries_its_counter_tg45(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """``_plain`` bypasses :meth:`~pkb.server.telegram.TelegramAdapter._send`, and with it the one
    place the part counter is applied correctly.

    It labelled every part ``counter(position, 1)``, which is the empty string for every position,
    so a correction that split arrived as two unnumbered messages — while ``split_message`` had
    already narrowed the budget to make room for a label that never appeared. The texts it sends
    are short constants today; what makes this worth a test is that nothing anywhere says they have
    to stay short, and the failure is a human reading half a correction about an approval whose
    buttons just died.
    """
    bot = await make_bot(service, store, api)
    long_correction = "\n".join(f"line {index} of the correction" for index in range(400))

    await bot._plain(Channel(CHAT, GENERAL), long_correction)

    parts = [message.text for message in api.landed_in(GENERAL)]
    assert len(parts) > 1, "the fixture text has to actually split for this to test anything"
    assert [part[:5] for part in parts] == [
        f"({n}/{len(parts)})"[:5] for n in range(1, len(parts) + 1)
    ]
    assert all(utf16_len(part) <= MESSAGE_LIMIT for part in parts)
