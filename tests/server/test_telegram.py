"""The Telegram adapter, driven end to end with no token and no socket (TG-8 … TG-66).

Everything here runs against three fixtures and nothing else: :class:`FakeBotApi`, which records
every Bot API call **in one shared journal** so "the callback was answered before the resume" is a
direct assertion rather than an inference; a **real** :class:`~pkb.service.telegram.
SqliteTelegramStore` over ``tmp_path``, because the durable row is the only thing that makes a
button pressed after a restart resolvable and a fake store would let the adapter pass while
remembering everything in the process; and :class:`~tests.server.stub.StubService`, subclassed here
so a refusal can be scripted per call.

Four properties of this channel decide what is worth asserting, and each one is a failure that is
silent in production:

* **The bot is a public inbound path.** A bot's username is discoverable, so anyone can produce an
  update. The chat mapping answers *which expert*; the owner allow-list answers *who may say yes* to
  a write with no undo, and it is the system's only authentication boundary.
* **A press arrives with no context.** Telegram redelivers an unconfirmed update for 24 hours, and
  ``callback_data`` holds 64 bytes — not enough for a thread id. So every press is resolved through
  the durable row and a fresh ``get_thread``; the tests that matter build a **second adapter** that
  never saw the message.
* **A Telegram message lives in the chat forever with its buttons live.** A TUI modal closes. Here
  the keyboard has to be taken off every message of an approval the moment it is answered, or a
  human scrolling back next week presses approve on a write that already happened.
* **The events the adapter consumes carry no status.** In process ``RunEnd`` has no ``status`` and
  ``RunError`` has no ``code``, so a consumer matching three states falls through to "done" on every
  provider failure — over an interrupted run, "done" means a parked, undoable write nobody answers.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio

from pkb.clients.approval import truncate
from pkb.contracts import (
    CANCELLED_MESSAGE,
    ActionView,
    AgentEvent,
    ApprovalRequest,
    Decision,
    MessageComplete,
    MessageDelta,
    RunEnd,
    RunError,
    RunHandle,
    SubagentEnd,
    SubagentStart,
    ThreadBusyError,
    ToolEnd,
    ToolStart,
)
from pkb.server.telegram import (
    _NEW_RETIRED,
    _STALE_SESSION,
    COMMANDS,
    Channel,
    TelegramAdapter,
    TelegramConfig,
    split_message,
    utf16_len,
)
from pkb.server.telegram_api import (
    CALLBACK_DATA_LIMIT,
    MESSAGE_LIMIT,
    POLL_TIMEOUT,
    TelegramError,
)
from pkb.service import RunSubscription, Thread, ThreadDetail
from pkb.service.sessions import Session
from pkb.service.telegram import GENERAL, SqliteTelegramStore
from tests.server.stub import AGENTS, COOKING, GRILLING, NOW, StubService

CHAT = 770001
"""The one mapped chat. Its agent is :data:`COOKING`."""

HOME = Channel(CHAT, GENERAL)
"""The General area of the one mapped chat — this whole file's channel (TG-72, TG-73).

Every rule here predates topics and none of them changed: §9 re-keyed the addressing unit from
the chat to the channel, and a chat with Threaded Mode off has exactly one channel, which is
this. Spelling it out at each call site is TG-72's point — a call that omits the topic files a
message under whichever binding happens to be General's, and that mis-file is invisible in a
diff.
"""

OTHER_CHAT = 770002
STRANGER_CHAT = 880002
OWNER = 42
STRANGER = 99

THREAD = "t-cooking-1"
RUN = "run-1"

DIFF = (
    "--- a/topics/Cooking/notes/steak.md\n"
    "+++ b/topics/Cooking/notes/steak.md\n"
    "@@ -1,3 +1,4 @@\n"
    "-rest for 5 minutes\n"
    "+rest for 8 minutes\n"
)

Journal = list[tuple[str, dict[str, Any]]]
"""Every Bot API call and every service call, in the order they happened.

Shared by the fake transport and the stub service on purpose: TG-61 is a statement about the order
of two calls to *different* objects, and two separate logs cannot say which came first.
"""


# --------------------------------------------------------------------------------------
# Fixtures — a fake transport, a real store, a scriptable service
# --------------------------------------------------------------------------------------


@dataclass
class FakeBotApi:
    """A recording ``BotApi``, and the arbiter of Telegram's two hard limits.

    The 4,096 **UTF-16 unit** message limit and the 64 **byte** ``callback_data`` limit are enforced
    here rather than only in the adapter, because that is where they are enforced in production: no
    Telegram client library checks either at construction, so both surface as a ``400`` from the
    server at the moment a human is waiting for an approval. A fake that accepted anything would let
    the adapter's own arithmetic be wrong and every test still pass.
    """

    journal: Journal = field(default_factory=list)
    next_message_id: int = 1000
    rejected: list[str] = field(default_factory=list)
    pending: list[Mapping[str, Any]] = field(default_factory=list)
    """What Telegram is still holding. Confirmed the way the real API confirms: by the next offset."""

    errors: list[BaseException | None] = field(default_factory=list)
    """Scripted refusals, one per ``get_updates`` call, popped in order (a ``None`` slot is a 200)."""

    send_error: BaseException | None = None
    """What every ``sendMessage`` raises, for the outbound failures nothing else can observe."""

    async def get_me(self) -> Mapping[str, Any]:
        return {"id": 1, "username": "pkb_test_bot"}

    async def get_updates(
        self, offset: int | None, *, timeout: int = POLL_TIMEOUT
    ) -> Sequence[Mapping[str, Any]]:
        """``offset=-1`` returns the **last** update only, exactly as Telegram documents it.

        The negative form is how a cold start learns where the backlog ends without receiving it,
        so a fake that ignored the sign would let TG-30's whole mechanism be a no-op and still pass.
        """
        # A real poll is a network round trip that suspends; a fake that never suspends turns the
        # adapter's `while True` into a tight loop that starves the test driving it.
        await asyncio.sleep(0)
        self.journal.append(("get_updates", {"offset": offset, "timeout": timeout}))
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        if offset is not None and offset < 0:
            return self.pending[offset:]
        if offset is not None:
            self.pending = [u for u in self.pending if int(u["update_id"]) >= offset]
        held, self.pending = list(self.pending), []
        return held

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: Sequence[Sequence[Mapping[str, str]]] | None = None,
    ) -> Mapping[str, Any]:
        if self.send_error is not None:
            raise self.send_error
        self._check_text("sendMessage", text)
        self._check_keyboard(keyboard)
        self.journal.append(("send_message", {"chat_id": chat_id, "text": text, "kb": keyboard}))
        self.next_message_id += 1
        return {"message_id": self.next_message_id, "chat": {"id": chat_id}}

    async def send_document(
        self, chat_id: int, filename: str, content: bytes, caption: str = ""
    ) -> Mapping[str, Any]:
        self.journal.append(
            (
                "send_document",
                {"chat_id": chat_id, "filename": filename, "content": content, "caption": caption},
            )
        )
        self.next_message_id += 1
        return {"message_id": self.next_message_id}

    async def answer_callback(
        self, callback_id: str, text: str = "", *, show_alert: bool = False
    ) -> None:
        self.journal.append(
            ("answer_callback", {"id": callback_id, "text": text, "alert": show_alert})
        )

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        self._check_text("editMessageText", text)
        self.journal.append(
            ("edit_message", {"chat_id": chat_id, "message_id": message_id, "text": text})
        )

    async def clear_keyboard(self, chat_id: int, message_id: int) -> None:
        """``editMessageReplyMarkup`` — the buttons go, the text the human read stays (TG-63)."""
        self.journal.append(("clear_keyboard", {"chat_id": chat_id, "message_id": message_id}))

    # -- what the tests read ----------------------------------------------------------

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
    def with_keyboard(self) -> list[dict[str, Any]]:
        return [entry for entry in self.sent if entry["kb"]]

    @property
    def documents(self) -> list[dict[str, Any]]:
        return self.of("send_document")

    @property
    def edits(self) -> list[dict[str, Any]]:
        return self.of("edit_message")

    @property
    def cleared(self) -> list[dict[str, Any]]:
        return self.of("clear_keyboard")

    @property
    def answers(self) -> list[dict[str, Any]]:
        return self.of("answer_callback")

    @property
    def polls(self) -> list[dict[str, Any]]:
        return self.of("get_updates")

    # -- the two limits ---------------------------------------------------------------

    def _check_text(self, method: str, text: str) -> None:
        if utf16_len(text) > MESSAGE_LIMIT:
            self.rejected.append(text)
            raise TelegramError(method, 400, "Bad Request: message is too long")

    def _check_keyboard(self, keyboard: Sequence[Sequence[Mapping[str, str]]] | None) -> None:
        for row in keyboard or ():
            for button in row:
                data = str(button.get("callback_data", ""))
                if not 1 <= len(data.encode()) <= CALLBACK_DATA_LIMIT:
                    self.rejected.append(data)
                    raise TelegramError("sendMessage", 400, "Bad Request: BUTTON_DATA_INVALID")


@dataclass
class FakeHealth:
    """The ``telegram`` block of ``/health``, reduced to what the adapter is allowed to touch.

    Layer 3 owns the real :class:`~pkb.server.health.SubsystemState`; the adapter only ever stamps
    connectivity (TG-12) and outbound failures (TG-13) onto it, and ``restarts`` is here to be
    asserted **unchanged** — a failed send is not the subsystem being down, and the day it starts
    incrementing that counter is the day the number arch §8 asks to be visible becomes noise.
    """

    last_error: str | None = None
    polls_ok: int = 0
    send_errors: list[str] = field(default_factory=list)
    restarts: int = 0

    def poll_ok(self) -> None:
        self.polls_ok += 1

    def send_failed(self, exc: BaseException) -> None:
        self.send_errors.append(f"{type(exc).__name__}: {exc}")


class ScriptedService(StubService):
    """A :class:`StubService` that can refuse a run and that reports into the shared journal.

    Two gaps the shipped stub leaves, and both are about *synchronous* refusals from ``start_run``
    (AP-10): ``ApprovalPendingError`` and ``ThreadBusyError`` arrive before anything is committed,
    and TG-37/TG-38 are entirely about what the chat is told when they do. ``details`` scripts a
    per-thread ``ThreadDetail`` so a fan-out gate can park on a child while the parent's ``pending``
    is null — the shape that makes a naive recovery conclude "no approval".
    """

    def __init__(
        self,
        journal: Journal,
        *,
        events: Sequence[AgentEvent] = (),
        refusals: Sequence[BaseException] = (),
    ) -> None:
        super().__init__(events=list(events))
        self.journal = journal
        self.refusals = list(refusals)
        self.details: dict[str, ThreadDetail] = {}
        self.resume_gate: asyncio.Event | None = None
        self.resumed: list[tuple[str, tuple[Decision, ...], str | None]] = []

    async def start_run(
        self,
        thread_id: str,
        message: str,
        *,
        approval_mode: str = "interactive",
        run_id: str | None = None,
    ) -> RunSubscription:
        self.journal.append(("start_run", {"thread_id": thread_id, "message": message}))
        if self.refusals:
            self.calls.append(("start_run", (thread_id, message)))
            raise self.refusals.pop(0)
        return await super().start_run(
            thread_id, message, approval_mode=approval_mode, run_id=run_id
        )

    async def start_session_run(self, session_id: str, message: str) -> RunSubscription:
        """The session-keyed successor (Task 7) — same refusal script, same journal kind, so every
        pre-existing ``kinds(journal).count("start_run")``-shaped assertion keeps meaning what it
        always meant: one admitted-or-refused turn, whichever entry point the adapter now calls."""
        self.journal.append(("start_run", {"thread_id": session_id, "message": message}))
        if self.refusals:
            self.calls.append(("start_run", (session_id, message)))
            raise self.refusals.pop(0)
        return await super().start_session_run(session_id, message)

    async def resume(
        self, thread_id: str, decisions: Sequence[Decision], *, interrupt_id: str | None = None
    ) -> RunSubscription:
        self.journal.append(
            ("resume", {"thread_id": thread_id, "interrupt_id": interrupt_id}),
        )
        self.resumed.append((thread_id, tuple(decisions), interrupt_id))
        if self.resume_gate is not None:
            await self.resume_gate.wait()
        return await super().resume(thread_id, decisions, interrupt_id=interrupt_id)

    async def get_thread(self, thread_id: str) -> ThreadDetail:
        self.journal.append(("get_thread", {"thread_id": thread_id}))
        scripted = self.details.get(thread_id)
        if scripted is not None:
            self.calls.append(("get_thread", (thread_id,)))
            return scripted
        return await super().get_thread(thread_id)

    async def get_session(self, session_id: str) -> Session:
        """TG-51/TG-52's re-sync read (Task 10 repoints the adapter's ``get_thread`` call here,
        since ``subscription.handle.thread_id`` has carried a session id since Task 7)."""
        self.journal.append(("get_session", {"session_id": session_id}))
        return await super().get_session(session_id)


@pytest_asyncio.fixture
async def connection(tmp_path: Path) -> Any:
    """Layer 3's own SQLite file, opened the way the daemon opens it (ST-1, autocommit)."""
    handle = await aiosqlite.connect(tmp_path / "pkb.sqlite", isolation_level=None)
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
    return FakeBotApi(journal)


@pytest.fixture
def service(journal: Journal) -> ScriptedService:
    return ScriptedService(journal, events=reply_script())


# --------------------------------------------------------------------------------------
# Helpers — updates as Telegram delivers them, and the outbox drained the way the task drains it
# --------------------------------------------------------------------------------------


def adapter(
    service: ScriptedService,
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
            token="000:fake",
            chats={CHAT: COOKING} if chats is None else chats,
            owner_user_ids=owners,
        ),
    )


def reply_script(text: str = "Filed under Cooking.") -> list[AgentEvent]:
    """One assistant reply, as Layer 2 really emits it: complete **and** again as ``final_text``."""
    return [
        MessageComplete(run_id=RUN, agent_id=COOKING, text=text),
        RunEnd(run_id=RUN, final_text=text),
    ]


def message_update(
    update_id: int = 1,
    *,
    chat_id: int = CHAT,
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
    if text is not None:
        message["text"] = text
    message.update(extra or {})
    return {"update_id": update_id, "message": message}


def callback_update(
    data: str,
    *,
    update_id: int = 1,
    sender: int = OWNER,
    chat_id: int = CHAT,
    query_id: str = "cbq-1",
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": query_id,
            "from": {"id": sender},
            "data": data,
            "message": {"message_id": 5, "chat": {"id": chat_id, "type": "private"}},
        },
    }


async def drain(bot: TelegramAdapter) -> None:
    """Run the outbox pump until it is empty — TG-49's queue is not part of any rule under test."""
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


async def press(bot: TelegramAdapter, handle: str, index: int, verb: str, **kwargs: Any) -> None:
    # `callback_data()`'s old format (Task 6, DESIGN.md §2.10) — the function is gone with the
    # approval-posting flow, but every caller below is `@pytest.mark.superseded` and still needs a
    # well-formed payload to drive the (now-dead) callback dispatch it is documenting.
    await deliver(bot, callback_update(f"v1|{handle}|{index}|{verb}", **kwargs))


def thread_row(
    thread_id: str = THREAD, agent_id: str = COOKING, title: str | None = None
) -> Thread:
    return Thread(
        thread_id=thread_id,
        agent_id=agent_id,
        created_at=NOW,
        updated_at=NOW,
        origin_channel="telegram",
        title=title,
    )


async def bind(
    service: ScriptedService,
    store: SqliteTelegramStore,
    *,
    thread_id: str = THREAD,
    chat_id: int = CHAT,
    agent_id: str = COOKING,
) -> Thread:
    """Give the chat a current thread, as a second message in a live chat always has."""
    thread = thread_row(thread_id, agent_id)
    service.rows[thread_id] = thread
    await store.bind(chat_id, GENERAL, thread_id, agent_id)
    return thread


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
    thread_id: str = THREAD,
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


# `PROMPTS_TABLE`'s old name (Task 6, DESIGN.md §2.10): the constant and the table it named are
# gone with the approval-prompt surface, but the helpers below are read only by tests marked
# `@pytest.mark.superseded`, which still document the table's shape until Task 10 removes them.
_PROMPTS_TABLE = "pkb_telegram_prompts"


async def handles(connection: aiosqlite.Connection) -> list[str]:
    """Every approval the adapter has staged, oldest first — read from the durable table."""
    cursor = await connection.execute(f"SELECT handle FROM {_PROMPTS_TABLE} ORDER BY rowid")
    return [str(row[0]) for row in await cursor.fetchall()]


async def prompt_rows(connection: aiosqlite.Connection) -> list[dict[str, Any]]:
    cursor = await connection.execute(
        f"SELECT handle, chat_id, thread_id, interrupt_id, action_count, resolved "
        f"FROM {_PROMPTS_TABLE} ORDER BY rowid"
    )
    return [
        {
            "handle": str(row[0]),
            "chat_id": int(row[1]),
            "thread_id": str(row[2]),
            "interrupt_id": str(row[3]),
            "action_count": int(row[4]),
            "resolved": bool(row[5]),
        }
        for row in await cursor.fetchall()
    ]


def kinds(journal: Journal) -> list[str]:
    return [kind for kind, _ in journal]


def tree_digest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def local_subscription(events: Sequence[AgentEvent], *, closes: list[int]) -> RunSubscription:
    """A stream that simply **stops**, and a ``close`` that records being called.

    ``RunHub`` always publishes a terminal frame, so the one case TG-51 is about — a subscriber
    dropped for being slow, whose stream is closed *without* one — cannot be produced through the
    supervisor. It is produced here, which is the only way to assert the adapter does not read the
    silence as success.
    """

    async def stream() -> Any:
        for event in events:
            yield event

    return RunSubscription(
        handle=RunHandle(run_id=RUN, agent_id=COOKING, thread_id=THREAD),
        events=stream(),
        close=lambda: closes.append(1),
    )


# --------------------------------------------------------------------------------------
# § the unmapped chat: instructions, and nothing else (TG-9, TG-10)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unmapped_chat_learns_its_own_id_and_no_agent_id_tg21(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The owner is told the one datum they cannot get elsewhere, and nothing about the tree.

    A bot's username is discoverable, so anybody can produce an unmapped chat — and the **topic
    titles are the sensitive part** of a private knowledge base. A reply that listed the agents
    would hand the shape of somebody's notes to whoever typed ``/start``. The chat id is worthless
    to a stranger and is the only thing the owner cannot look up any other way.

    The sender is an **owner** because TG-20 now runs the allow-list before the mapping: a stranger
    gets silence on every path (see ``..._tg20`` below), and the only person who ever needs this
    reply is the owner opening a chat they have not mapped yet. The rule id in this test's name was
    ``tg9`` — the 409 rule — which is a different rule about a different failure.
    """
    bot = adapter(service, store, api)

    await deliver(bot, message_update(chat_id=STRANGER_CHAT, sender=OWNER))

    assert len(api.sent) == 1
    reply = api.texts[0]
    assert str(STRANGER_CHAT) in reply
    for descriptor in AGENTS:
        assert descriptor.agent_id not in reply
        assert descriptor.title not in reply
    assert service.calls == [], "the catalog was read on a path that must not touch the service"


@pytest.mark.asyncio
async def test_an_unmapped_chat_runs_nothing_and_says_the_message_was_dropped_tg22(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
) -> None:
    """Nothing ran, nothing was stored, and the human is told so in the same breath.

    The two failure modes are symmetrical and both silent: half-stored text reappears in an
    unexpected topic later, and silently dropped text leaves the human believing their note was
    filed. Saying "I have not kept this" is the only thing that makes the outcome actionable —
    re-send it once the chat is mapped.
    """
    bot = adapter(service, store, api)

    await deliver(bot, message_update(chat_id=STRANGER_CHAT, sender=OWNER, text="steak notes"))

    assert service.calls == []
    assert await store.bound_session(STRANGER_CHAT, GENERAL) is None
    assert "not kept" in api.texts[0]


# --------------------------------------------------------------------------------------
# § the owner allow-list — the system's only authentication boundary (decision X)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_updates_leave_one_run_and_two_silences_tg20(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """The rule's own table: mapped+allowed runs; mapped+other and unmapped+other say **nothing**.

    TG-1's mapping was ruled about addressing. A chat id is a guessable integer and a bot token is a
    public inbound path into a process with no authentication that writes to a tree with no undo, so
    the sender is checked independently of the chat — one comparison that cannot be retrofitted
    after a token leaks.

    *Silently* is the part that was wrong and the part that matters. The build replied "this
    knowledge base does not accept messages from this account" to a stranger in a mapped chat, and
    the full unmapped explanation to a stranger anywhere else — a guaranteed response on every
    path, which is both a reply amplifier against the owner's per-chat send budget and an existence
    oracle for anyone who guesses a chat id. The previous version of this test asserted
    ``len(api.sent) == 1`` and ``"does not accept" in api.texts[0]``: it encoded the defect.
    """
    bot = adapter(service, store, api)

    await deliver(bot, message_update(1, text="file this under Cooking"))
    sent_after_owner = len(api.sent)
    await deliver(bot, message_update(2, sender=STRANGER, text="and this"))
    await deliver(bot, message_update(3, chat_id=STRANGER_CHAT, sender=STRANGER, text="and this"))

    assert kinds(journal).count("start_run") == 1
    assert len(api.sent) == sent_after_owner, "the second and third updates produced no reply"
    assert await store.bound_session(STRANGER_CHAT, GENERAL) is None


@pytest.mark.asyncio
async def test_a_group_chat_runs_nothing_tg19(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A group is many senders with no identity check, in front of a knowledge base with no undo.

    Telegram's group privacy mode also silently drops most messages, so a mapped group *half* works
    — which is worse than refusing, because the human cannot tell a dropped note from a filed one.
    """
    bot = adapter(service, store, api, chats={CHAT: COOKING})

    await deliver(bot, message_update(chat_type="supergroup", text="file this"))

    assert service.calls == []
    assert api.sent == []


# --------------------------------------------------------------------------------------
# § attachments: the caption is the part the human wrote (TG-36)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_photo_is_refused_with_its_caption_and_touches_no_file_tg36(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi, tmp_path: Path
) -> None:
    """There is no sanctioned path for a file arriving over Telegram, so nothing is downloaded.

    ``pkb.sources.stage`` writes into ``<kb>/.inbox``, which is *under* ``kb_root`` and forbidden to
    Layer 3 by any path, and ``PkbService`` has no ingest-by-path method at all. Ignoring the
    attachment silently would still lose the **caption**, which is the only part the human actually
    typed — so it comes back quoted, and the tree is untouched.
    """
    kb_root = tmp_path / "kb"
    (kb_root / "topics").mkdir(parents=True)
    (kb_root / "topics" / "steak.md").write_text("existing note\n", encoding="utf-8")
    before = tree_digest(kb_root)
    bot = adapter(service, store, api)

    await deliver(
        bot,
        message_update(
            text=None,
            extra={
                "photo": [{"file_id": "AgACAgQAA", "file_unique_id": "x", "width": 90}],
                "caption": "smoked brisket at 107C for 12 hours",
            },
        ),
    )

    # TG-79, §9: routing now checks the channel's agent against the live catalog on every message,
    # so `list_agents` — a synchronous registry read, no run and no thread — is on this path by
    # design. What must stay empty is everything that *does* something.
    assert [name for name, _ in service.calls if name != "list_agents"] == []
    assert "smoked brisket at 107C for 12 hours" in api.transcript
    assert tree_digest(kb_root) == before


# --------------------------------------------------------------------------------------
# § one reply per reply (TG-41, TG-42, TG-43)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_five_hundred_deltas_send_one_message_and_edit_none_tg41(
    store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """The fact channel only. Deltas are watched in a terminal; on a phone they are noise and 429s.

    Layer 2's token channel and its fact channel are independent, so **every** assistant message
    arrives twice — once token by token, once complete — and Layer 3 is forbidden from deduplicating
    it. A consumer that renders both sends the reply twice; one that edits per token issues hundreds
    of calls a second against a documented budget of about one message per second per chat, and the
    ``429`` that follows carries a ``retry_after`` that stalls the *approval* messages too.

    The stream is handed to the consumer directly rather than through the supervisor because 500
    frames published in one scheduler slot overflow the hub's 256-slot subscriber queue — the very
    back-pressure TG-49 is about, and a different rule from this one.
    """
    reply = "Filed under Cooking."
    script: list[AgentEvent] = [
        MessageDelta(run_id=RUN, agent_id=COOKING, text=f"tok{n} ") for n in range(500)
    ]
    script += reply_script(reply)
    service = ScriptedService(journal, events=[])
    service.rows[THREAD] = thread_row()
    bot = adapter(service, store, api)

    await bot._consume(HOME, COOKING, local_subscription(script, closes=[]))
    await drain(bot)

    assert api.edits == []
    assert api.texts == [reply]


@pytest.mark.asyncio
async def test_the_run_end_final_text_is_never_sent_again_tg42(
    store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """``RunEnd`` is read for its outcome, never for its text — the runtime yields the reply twice.

    ``_deliver`` emits ``MessageComplete(text=reply)`` and immediately ``RunEnd(final_text=reply)``
    with the identical string. Rendering both puts the same paragraph in the chat twice, which on a
    phone reads as the bot having sent the note twice — and there is no way for the human to tell
    that from a genuine double-filing.
    """
    reply = "Filed under Cooking, in steak.md."
    service = ScriptedService(journal, events=reply_script(reply))
    await bind(service, store)
    bot = adapter(service, store, api)

    await deliver(bot, message_update())

    assert api.transcript.count(reply) == 1


@pytest.mark.asyncio
async def test_a_fan_out_sends_one_roster_line_and_no_tool_lines_tg43(
    store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """Brackets and tool calls are terminal furniture; here they are a rate-limit incident.

    A four-expert fan-out's ``subagent`` brackets alone are eight messages in one turn, and measured,
    seven unpaced messages went out in eight milliseconds against a ~1/s per-chat budget. So the
    roster is coalesced into one line and tool activity produces nothing at all.
    """
    script: list[AgentEvent] = [
        SubagentStart(run_id=RUN, agent_id=COOKING),
        ToolStart(run_id=RUN, agent_id=COOKING, tool="write_file", summary="steak.md"),
        ToolEnd(run_id=RUN, agent_id=COOKING, tool="write_file", summary="steak.md", error=False),
        SubagentEnd(run_id=RUN, agent_id=COOKING, status="ok"),
        SubagentStart(run_id=RUN, agent_id=GRILLING),
        SubagentEnd(run_id=RUN, agent_id=GRILLING, status="ok"),
        *reply_script(),
    ]
    service = ScriptedService(journal, events=script)
    await bind(service, store)
    bot = adapter(service, store, api)

    await deliver(bot, message_update())

    roster = [text for text in api.texts if COOKING in text and GRILLING in text]
    assert len(roster) == 1
    assert "write_file" not in api.transcript
    assert len(api.sent) == 2


# --------------------------------------------------------------------------------------
# § four terminal states, derived in the adapter (TG-50, TG-51, TG-52)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cancelled_run_is_never_worded_as_a_failure_tg50(
    store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """A cancellation is something the human did; only the message body says which it was.

    The supervisor synthesises ``RunError(message=CANCELLED_MESSAGE)`` for a cancelled run because
    Layer 2 emits nothing at all on that path. The string is the *only* thing separating "you
    stopped this" from "the provider fell over", so a second copy of it — or a client that skipped
    the comparison — turns a deliberate stop into an alarming failure on a phone and a clean
    cancellation in the terminal, from the same run.
    """
    service = ScriptedService(
        journal, events=[RunError(run_id=RUN, message=CANCELLED_MESSAGE, retryable=True)]
    )
    await bind(service, store)
    bot = adapter(service, store, api)

    await deliver(bot, message_update())

    lowered = api.transcript.lower()
    assert "cancel" in lowered
    assert "fail" not in lowered
    assert "error" not in lowered
    assert CANCELLED_MESSAGE not in api.transcript, "the sentinel is a code, not copy for a human"


@pytest.mark.asyncio
async def test_a_stream_that_stops_is_an_unknown_outcome_not_a_success_tg51(
    store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """Silence is not completion, not failure, and never a reason to run the turn again.

    ``RunHub`` drops a subscriber whose queue overflows and closes its stream **without** a terminal
    frame, so the loss looks exactly like an ending. Rendering it as success tells the human their
    note was filed when it may not have been; re-starting the run writes to a tree with no undo a
    second time. One ``get_session`` and an honest "I do not know" is the only safe answer.

    ``get_session`` since Task 10, not ``get_thread``: ``subscription.handle.thread_id`` has carried
    a session id since Task 7 (``RuntimeService._launch_session`` mints the handle from
    ``session_id``), so the adapter's own re-sync call is repointed to match what it was actually
    holding — ``get_thread`` against a real service would have raised ``UnknownThreadError`` here,
    since the id was never a row in the ``threads`` table this fixture's ``thread_row()`` stands in
    for; the compatibility shim in ``tests/server/stub.py`` is what let it read back regardless.
    """
    service = ScriptedService(journal, events=[])
    service.rows[THREAD] = thread_row()
    closes: list[int] = []
    bot = adapter(service, store, api)
    journal.clear()

    await bot._consume(HOME, COOKING, local_subscription(reply_script()[:1], closes=closes))
    await drain(bot)

    assert kinds(journal).count("get_session") == 1
    assert "start_run" not in kinds(journal)
    assert "resume" not in kinds(journal)
    assert "do not know how it finished" in api.transcript


@pytest.mark.asyncio
async def test_the_subscription_is_closed_even_when_the_stream_raises_tg52(
    store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """Detaching is what a ``finally`` is for; a leaked subscriber holds a hub open for the process.

    Closing detaches and never cancels (AP-7), so the cost of forgetting it is not a dead run — it
    is a subscriber that is never removed and a hub that is never freed, in a daemon whose whole
    value proposition is staying up for weeks.

    The exception is **not** asserted to escape any more, and that change is TG-51's: a stream that
    raises has closed without a terminal frame just as surely as one that stopped, so it takes the
    same "outcome unknown" branch. Letting it propagate meant it landed in the poll loop's blanket
    suppression, and the chat was left holding half a reply with no re-sync and no notice —
    indistinguishable from success, on a turn that may already have written. This test pinned that
    propagation, so it pinned the gap; ``closes == [1]`` is the part that was about TG-52.
    """
    service = ScriptedService(journal, events=[])
    service.rows[THREAD] = thread_row()
    closes: list[int] = []

    async def exploding() -> Any:
        yield MessageComplete(run_id=RUN, agent_id=COOKING, text="half a reply")
        raise RuntimeError("the hub went away")

    subscription = RunSubscription(
        handle=RunHandle(run_id=RUN, agent_id=COOKING, thread_id=THREAD),
        events=exploding(),
        close=lambda: closes.append(1),
    )
    bot = adapter(service, store, api)

    await bot._consume(HOME, COOKING, subscription)
    await drain(bot)

    assert closes == [1]
    assert kinds(journal).count("get_session") == 1, "one re-sync, exactly as for a clean stop"
    assert "do not know how it finished" in api.transcript
    assert "start_run" not in kinds(journal) and "resume" not in kinds(journal)


@pytest.mark.asyncio
async def test_the_subscription_close_is_called_not_awaited_tg52(
    store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """``close`` is a plain function whose own docstring calls it an awaitable — it is not.

    Two shipped consumers already hedge with ``if callable(close): close()``. A third that trusted
    the docstring would raise ``TypeError`` from inside a ``finally`` during teardown, where it
    surfaces as a shutdown bug rather than as the read of a wrong docstring that it is. This close
    returns a non-awaitable on purpose: awaiting it fails the test.
    """
    service = ScriptedService(journal, events=[])
    service.rows[THREAD] = thread_row()
    closes: list[int] = []
    bot = adapter(service, store, api)

    await bot._consume(HOME, COOKING, local_subscription(reply_script(), closes=closes))
    await drain(bot)

    assert closes == [1]


# --------------------------------------------------------------------------------------
# § refusals from `start_run` — both of them are normal (TG-37, TG-38, TG-53)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_busy_thread_reads_as_progress_and_is_never_retried_tg38(
    store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """On a phone this is the **normal** case: people send three lines as three messages.

    Presented as an error it reads as a broken daemon and the human stops using the channel;
    retried automatically it is a second POST against a thread that may already have written. The
    honest answer is that the previous turn is still going and this text was not sent — quoted back
    so re-sending is one long-press away.
    """
    service = ScriptedService(journal, refusals=[ThreadBusyError("a run is already active")])
    await bind(service, store)
    bot = adapter(service, store, api)

    await deliver(bot, message_update(text="and use coarse salt"))

    assert kinds(journal).count("start_run") == 1
    assert "and use coarse salt" in api.transcript
    assert "error" not in api.transcript.lower()
    assert "still finishing" in api.transcript.lower()


# --------------------------------------------------------------------------------------
# § the deciding surface is never truncated (TG-56, TG-66)
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# § a press is resolved from durable state, never from memory (TG-58, TG-59, TG-60)
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# § the callback is answered first, always (TG-61)
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# § a message lives forever, and so do its buttons (TG-62, TG-63)
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# § the keyboard is derived, never drawn from memory (TG-54, TG-55, TG-57, TG-64)
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# § length is the only permitted reason to cut (TG-44, TG-45)
# --------------------------------------------------------------------------------------


def test_a_character_budget_is_not_a_telegram_budget_tg44() -> None:
    """The regression that motivates the whole of the adapter's arithmetic.

    ``truncate`` counts characters and is deliberately channel-agnostic; Telegram counts UTF-16 code
    units. Emoji are two units each, so the common case is silently **over** the wire limit while
    the shared helper reports it did not cut at all. Telegram answers that send with ``400 message
    is too long`` and the human sees *nothing* — not a short reply, nothing.
    """
    text = "\U0001f525" * 3000 + "\nplain tail\n" + "x" * 500
    cut, was_truncated = truncate(text, MESSAGE_LIMIT)

    assert was_truncated is False
    assert len(cut) <= MESSAGE_LIMIT
    assert utf16_len(cut) > MESSAGE_LIMIT
    assert all(utf16_len(part) <= MESSAGE_LIMIT for part in split_message(text))


@pytest.mark.asyncio
async def test_an_all_emoji_reply_is_sent_within_the_utf16_budget_tg44(
    store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """A knowledge base about food, travel or code carries astral-plane characters routinely.

    The fake refuses an over-long send exactly as ``api.telegram.org`` does — 4,097 UTF-16 units is
    a ``400``, confirmed against the real API — so a budget that is right about the text and wrong
    about what is prepended to it shows up here as a rejected message rather than as a green test.
    The failure in production is invisible: the send raises, the pump swallows it, and what reaches
    the phone is a reply that starts at "(2/2)" with nothing saying the rest existed.
    """
    reply = ("\U0001f525 hot\n" * 900).strip()
    service = ScriptedService(journal, events=reply_script(reply))
    await bind(service, store)
    bot = adapter(service, store, api)

    await deliver(bot, message_update())

    assert api.rejected == []
    assert len(api.sent) > 1
    assert all(utf16_len(text) <= MESSAGE_LIMIT for text in api.texts)


def test_a_long_reply_splits_on_line_boundaries_and_reassembles_exactly_tg45() -> None:
    """Cutting on meaning would be the same lie one layer down as inventing an expert's answer.

    LB-18 exists because a model composing a reply claimed the Cooking expert had checked the
    knowledge base when no expert ran. A transport that summarised, reflowed or reordered to fit
    4,096 units would be doing that to the human's own filed content. A length cut can be wrong; it
    cannot be a lie — so the parts concatenate back to the original byte for byte.
    """
    text = "".join(f"line {n}: {'note ' * 10}\n" for n in range(400))
    assert len(text) > 12000

    parts = split_message(text)

    assert len(parts) > 1
    assert "".join(parts) == text
    assert all(part.endswith("\n") for part in parts)
    assert all(utf16_len(part) <= MESSAGE_LIMIT for part in parts)


@pytest.mark.asyncio
async def test_a_split_reply_is_numbered_and_never_summarised_tg45(
    store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """The only thing the adapter is allowed to add is a mechanical counter.

    Four unannounced messages in a row read as four separate replies; ``(2/4)`` says one reply
    arrived in pieces. Anything beyond that — a summary line, a "continued" preamble the model wrote
    — would be content the adapter authored about content it was only carrying.
    """
    reply = "".join(f"line {n}: {'note ' * 10}\n" for n in range(400))
    service = ScriptedService(journal, events=reply_script(reply))
    await bind(service, store)
    bot = adapter(service, store, api)

    await deliver(bot, message_update())

    assert len(api.sent) > 1
    bodies = [text.split("\n", 1)[1] for text in api.texts]
    assert "".join(bodies) == reply
    assert all(text.startswith(f"({n + 1}/{len(api.texts)})") for n, text in enumerate(api.texts))


# --------------------------------------------------------------------------------------
# § one current thread per chat, rotated only on request (TG-26, TG-27)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_several_messages_share_one_thread_tg26(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """A chat is one continuous conversation; a thread is the turn-taking unit inside it.

    A thread per message destroys continuity, fires a titling call every time and turns
    "Cooking · 4 conversations" into "Cooking · 380" in the TUI's sidebar — the place D3 says the
    phone's conversations have to be findable.
    """
    bot = adapter(service, store, api)

    for n in range(6):
        await deliver(bot, message_update(update_id=n + 1, text=f"note {n}"))

    started = [entry["thread_id"] for kind, entry in journal if kind == "start_run"]
    assert len(started) == 6
    assert len(set(started)) == 1
    assert [name for name, _ in service.calls].count("create_session") == 1
    assert await store.bound_session(CHAT, GENERAL) == started[0]


@pytest.mark.asyncio
async def test_only_close_rotates_the_session_task7(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """Task 7 successor of ``test_only_new_rotates_the_thread_tg27``: with no ``/new`` left (S-15),
    the same "no invisible rotation" property now belongs to ``/close`` — ordinary messages, and
    even the retired ``/new`` keystroke itself, must never split one session into two, and only an
    explicit ``/close`` may.
    """
    bot = adapter(service, store, api)

    await deliver(bot, message_update(update_id=1, text="first"))
    await deliver(
        bot, message_update(update_id=2, text="/new")
    )  # the dead command: a reply, no rotation
    await deliver(bot, message_update(update_id=3, text="second"))

    started = [entry["thread_id"] for kind, entry in journal if kind == "start_run"]
    assert len(set(started)) == 1, (
        "an ordinary message and the retired /new must not rotate anything"
    )
    assert [name for name, _ in service.calls].count("create_session") == 1

    await deliver(bot, message_update(update_id=4, text="/close"))
    await deliver(bot, message_update(update_id=5, text="third"))

    started_after_close = [entry["thread_id"] for kind, entry in journal if kind == "start_run"]
    assert len(set(started_after_close)) == 2, (
        "/close is the one thing that may open a fresh session"
    )


@pytest.mark.asyncio
async def test_new_gets_the_retirement_pointer_not_the_generic_fallback_task7(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Fix round 1, finding 4: ``/new`` is recognized and answered with its own one-line pointer
    (:data:`~pkb.server.telegram._NEW_RETIRED`, per :data:`COMMANDS`'s own docstring) — not the
    generic "I know ..." unknown-command fallback (:data:`COMMANDS` no longer names it at all, so
    the dispatcher would fall through to that branch without the explicit ``elif``) and not silence.
    """
    bot = adapter(service, store, api)

    await deliver(bot, message_update(text="/new"))

    assert api.transcript == _NEW_RETIRED
    assert "I know" not in api.transcript


# --------------------------------------------------------------------------------------
# § the command surface (TG-39, TG-40, TG-35)
# --------------------------------------------------------------------------------------


def test_the_command_surface_is_exactly_the_seven_s15() -> None:
    """Task 7 successor of ``test_the_command_surface_is_exactly_six_and_has_no_connect_or_talk_
    tg39`` and of ``test_telegram_topics.py``'s own ``set(COMMANDS)`` pin: S-15, quoted, "the set
    settles at seven commands: ``/channels``, ``/threads``, ``/agents``, ``/cancel``, ``/name``,
    ``/close`` and ``/end``." Exact tuple, exact order, and every retired name still absent —
    fix round 1, finding 2: nothing pinned this, and an eighth command spliced into ``COMMANDS``
    passed the whole suite.
    """
    assert COMMANDS == ("/channels", "/threads", "/agents", "/cancel", "/name", "/close", "/end")
    for retired in ("/new", "/pending", "/connect", "/talk"):
        assert retired not in COMMANDS


@pytest.mark.asyncio
async def test_an_unknown_command_runs_nothing_and_lists_the_real_ones_tg39(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """A mistyped command must never fall through to being filed as a note.

    "/connect topic/cooking" filed verbatim into the knowledge base is a note nobody wrote, in a
    tree with no undo — and the human is left believing they rebound the chat.
    """
    bot = adapter(service, store, api)

    await deliver(bot, message_update(text="/connect topic/cooking"))

    assert "start_run" not in kinds(journal)
    # TG-79, §9: routing now checks the channel's agent against the live catalog on every message,
    # so `list_agents` — a synchronous registry read, no run and no thread — is on this path by
    # design. What must stay empty is everything that *does* something.
    assert [name for name, _ in service.calls if name != "list_agents"] == []
    for command in COMMANDS:
        assert command in api.transcript


@pytest.mark.asyncio
async def test_threads_are_listed_in_server_order_tg40(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Pending-first, then most recently updated — the order is the design, not a presentation whim.

    That ordering is the answer to the headline scenario: come back after two days and the thread
    waiting on you is at the top. A client-side sort by title or date buries exactly the row the
    human opened the chat to answer.
    """
    for title in ("zeta", "alpha", "middle"):
        thread = thread_row(f"t-{title}", COOKING, title=title)
        service.rows[thread.thread_id] = thread
    bot = adapter(service, store, api)

    await deliver(bot, message_update(text="/threads"))

    server_order = [thread.title for thread in await service.list_threads(COOKING)]
    rendered = [line for line in api.texts[0].splitlines() if "t-" in line]
    assert [line.split("  [")[0].lstrip("·● ") for line in rendered] == server_order
    assert server_order != sorted(server_order), "the fixture must be able to catch a sort"


@pytest.mark.asyncio
async def test_an_edited_message_is_acknowledged_and_never_re_run_tg35(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """The turn on the original text has already run, and may already have written.

    Re-running files near-identical material a second time with no way to remove the first; ignoring
    the edit silently leaves the human believing the correction landed. Telling them to send the
    correction as a new message is the only outcome they can act on.
    """
    await bind(service, store)
    bot = adapter(service, store, api)

    await deliver(
        bot,
        {
            "update_id": 7,
            "edited_message": {
                "message_id": 1,
                "chat": {"id": CHAT, "type": "private"},
                "from": {"id": OWNER},
                "text": "where does the *brisket* note go?",
            },
        },
    )

    assert "start_run" not in kinds(journal)
    assert len(api.sent) == 1
    assert "new message" in api.texts[0]


# --------------------------------------------------------------------------------------
# § two chats, two conversations (TG-25)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_chats_on_one_agent_hold_independent_threads_tg25(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Two chats may address the same expert; the dangerous direction is impossible by the type.

    One chat addressing two agents cannot be expressed by a ``chat_id -> agent_id`` mapping at all.
    The reverse is legitimate — a phone and a tablet, or two chats kept for two subjects — and each
    keeps its own current conversation, or one device would silently continue the other's turn.
    """
    bot = adapter(service, store, api, chats={CHAT: COOKING, OTHER_CHAT: COOKING})

    await deliver(bot, message_update(update_id=1, chat_id=CHAT, text="first"))
    await deliver(bot, message_update(update_id=2, chat_id=OTHER_CHAT, text="second"))

    first = await store.bound_session(CHAT, GENERAL)
    second = await store.bound_session(OTHER_CHAT, GENERAL)
    assert first is not None
    assert second is not None
    assert first != second


# --------------------------------------------------------------------------------------
# § the truncation marker the adapter supplies (decision U)
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# § the counter is part of what Telegram measures (TG-44, TG-45)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_reply_that_splits_on_the_boundary_is_sent_inside_the_limit_tg45(
    store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """The boundary case is the dangerous one, and it is the *last* part that carries the keyboard.

    A reply whose lines pack flush against 4,096 units produces parts that are exactly the limit;
    prepending ``(1/2)\\n`` afterwards puts them six units over, Telegram answers ``400 message is
    too long``, and the pump swallows the error. On an ordinary reply the human silently loses half
    of it; on an approval the refused message is the one carrying the buttons, so the write parks
    with nobody able to answer it. Measured as sent, counter included.
    """
    line = "y" * 63 + "\n"  # 64 UTF-16 units, so 64 lines are exactly one full message
    reply = line * 128
    assert utf16_len(reply) == 2 * MESSAGE_LIMIT
    service = ScriptedService(journal, events=reply_script(reply))
    await bind(service, store)
    bot = adapter(service, store, api)

    await deliver(bot, message_update())

    assert api.rejected == [], "Telegram refused a send, so the human received nothing"
    assert len(api.sent) > 1
    assert all(utf16_len(text) <= MESSAGE_LIMIT for text in api.texts)
    bodies = [text.split("\n", 1)[1] for text in api.texts]
    assert "".join(bodies) == reply, "the counter is the only thing added, and nothing is dropped"


# --------------------------------------------------------------------------------------
# § where the first poll starts (TG-29, TG-30)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cold_ledger_discards_the_backlog_instead_of_running_it_tg30(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    journal: Journal,
) -> None:
    """A daemon that was down for a day must not come back and file a day of chat.

    Telegram holds unconfirmed updates for 24 hours, so ``getUpdates(None)`` on a fresh ledger
    returns all of it — and every one would be claimed *and dispatched*, running as a turn against a
    tree with no undo. The human's model of a first start is "I turned it on", not "I have forty
    messages to file", and the second run of a message is not even the same run: the model's output
    differs, so they end up with two versions of a note they wrote once.
    """
    api.pending = [message_update(n, text=f"note {n}") for n in range(1, 41)]
    bot = adapter(service, store, api)

    offset = await bot._start_offset()

    assert offset == 41, "the first real poll starts past everything Telegram was holding"
    assert api.polls == [{"offset": -1, "timeout": 0}], "one probe, and never a long poll"
    assert "start_run" not in kinds(journal)
    assert "create_thread" not in kinds(journal)
    assert await store.next_offset() == 41, "the discard survives the next restart"
    assert [entry["chat_id"] for entry in api.sent] == [CHAT], "one notice per mapped chat"
    assert "not filed" in api.texts[0]


@pytest.mark.asyncio
async def test_a_warm_ledger_resumes_and_drains_nothing_tg29(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The backlog drain is for a *cold* ledger only; a warm one must not lose a message.

    ``MAX(update_id) + 1`` is the at-most-once guarantee — a restart mid-turn resumes exactly after
    what it recorded. Draining here would silently discard messages that arrived during the restart,
    which is the same lost note the cold-start rule is willing to accept only because there is no
    record that anything was ever expected.
    """
    await store.claim(100, CHAT, GENERAL, "message")
    await store.dispatched(100)
    bot = adapter(service, store, api)

    offset = await bot._start_offset()

    assert offset == 101
    assert api.polls == [], "a warm ledger asks Telegram nothing before it starts polling"
    assert api.sent == [], "and it does not tell anybody their messages were dropped"


# --------------------------------------------------------------------------------------
# § one poller per token (TG-9)
# --------------------------------------------------------------------------------------


async def spin(predicate: Any, *, ticks: int = 2000) -> None:
    """Let the event loop run until ``predicate()`` holds, or give up."""
    for _ in range(ticks):
        if predicate():
            return
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_second_consumer_of_the_token_stops_the_poll_and_names_both_causes_tg9(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``409`` is neither a blip nor a crash: it says somebody else is already holding the poll.

    Telegram permits one ``getUpdates`` per token. Retrying spins against a condition no retry can
    fix; raising restarts the task, and a restart against a *leaked* poller — the TG-7 failure —
    adds a third, so the harder the supervisor tries the worse it gets. The two causes are named
    because they need opposite fixes: stop the other daemon, or restart this one cleanly.
    """
    await store.claim(1, CHAT, GENERAL, "message")
    await store.dispatched(1)
    bot = adapter(service, store, api)
    bot.conflict_interval = 0
    bot.health = FakeHealth()
    api.errors = [TelegramError("getUpdates", 409, "Conflict: terminated by other getUpdates")]

    with caplog.at_level(logging.ERROR, logger="pkb.server.telegram"):
        task = asyncio.create_task(bot._poll())
        await spin(lambda: bool(caplog.records))
        alive = not task.done()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert alive, "a 409 must not crash the task into the restart that adds another poller"
    assert "second daemon" in caplog.text
    assert "previous generation" in caplog.text
    assert bot.health.last_error is not None and "second daemon" in bot.health.last_error
    assert "000:fake" not in caplog.text, "the token never reaches a log record"


@pytest.mark.asyncio
async def test_polling_resumes_when_the_other_poller_goes_away_tg9(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """Stopping is not giving up — the human's other daemon gets killed and this one must recover.

    The re-probe is on a slow fixed interval rather than a backoff, because a backoff is how six
    blips leave the bot at a permanent delay for the life of the process (the defect ``_supervise``
    already has), and because nothing about a 409 gets better by waiting longer each time.
    """
    await store.claim(1, CHAT, GENERAL, "message")
    await store.dispatched(1)
    bot = adapter(service, store, api)
    bot.conflict_interval = 0
    api.errors = [TelegramError("getUpdates", 409, "Conflict: terminated by other getUpdates")]
    api.pending = [message_update(2, text="the note that was waiting")]

    task = asyncio.create_task(bot._poll())
    await spin(lambda: "start_run" in kinds(journal))
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert "start_run" in kinds(journal), "a later 200 resumes the poll it stopped"


# --------------------------------------------------------------------------------------
# § the mapping is validated, not trusted, and never fatal (TG-18)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_chat_mapped_to_a_missing_agent_is_answered_not_routed_tg18(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    journal: Journal,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A topic can be renamed under a running config, so a stale entry is an ordinary Tuesday.

    Fatal-on-startup means one stale line takes the daemon down — every other chat, every parked
    approval and the TUI with it. Routing it anyway is the mis-file TG-1 was ruled to stop. So the
    entry is reported and that chat is answered exactly like an unmapped one, while the chats that
    are still correct keep working.
    """
    bot = adapter(service, store, api, chats={CHAT: COOKING, OTHER_CHAT: "topic/nope"})

    with caplog.at_level(logging.ERROR, logger="pkb.server.telegram"):
        bot.check_mapping()

    assert "topic/nope" in caplog.text and str(OTHER_CHAT) in caplog.text

    await deliver(bot, message_update(chat_id=OTHER_CHAT, text="file this"))

    assert "start_run" not in kinds(journal)
    assert str(OTHER_CHAT) in api.texts[0], "answered like an unmapped chat (TG-2)"

    await deliver(bot, message_update(update_id=2, text="and this"))

    assert "start_run" in kinds(journal), "one typo must not stop the other chats working"


# --------------------------------------------------------------------------------------
# § the unmapped reply is rate limited (TG-23)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ten_updates_from_one_unknown_chat_produce_one_reply_tg23(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Anyone who finds the bot can send it a hundred messages; answering each one costs the owner.

    Telegram rate limits per chat and per bot, so a bot that replies to a flood spends the budget
    its *owner's* approval keyboards need — the queue that goes slow is the one carrying an
    irreversible write to a human who is holding their phone.
    """
    bot = adapter(service, store, api)

    for update_id in range(1, 11):
        await deliver(bot, message_update(update_id, chat_id=STRANGER_CHAT, sender=OWNER))

    assert len(api.sent) == 1
    assert str(STRANGER_CHAT) in api.texts[0]


@pytest.mark.asyncio
async def test_the_window_is_per_chat_so_a_second_chat_is_still_answered_tg23(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Silencing a chat that has never been answered would hide the one thing it needs to be told.

    The reply carries the chat id, which is the only datum the owner cannot look up any other way —
    so a chat the bot has not yet explained itself to gets its explanation, whatever another chat
    has been doing.
    """
    bot = adapter(service, store, api)

    await deliver(bot, message_update(1, chat_id=STRANGER_CHAT, sender=OWNER))
    await deliver(bot, message_update(2, chat_id=STRANGER_CHAT, sender=OWNER))
    await deliver(bot, message_update(3, chat_id=OTHER_CHAT, sender=OWNER))

    assert [entry["chat_id"] for entry in api.sent] == [STRANGER_CHAT, OTHER_CHAT]


# --------------------------------------------------------------------------------------
# § a stale press is an alert, not a toast (TG-62)
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# § what the bot tells `/health` (TG-12, TG-13)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reachability_is_a_poll_that_returned_not_the_supervisors_state_tg12(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """``state == "running"`` is set *before* the first line of this class runs, so it proves nothing.

    Executed against the real supervisor: ``_supervise`` marks the subsystem running before awaiting
    the task, so a bot with a wrong token shows ``status: ok, restarts: 0`` for the whole first poll
    and the human debugging it learns nothing. ``last_poll_ok_at`` is the only field that means
    Telegram answered.
    """
    await store.claim(1, CHAT, GENERAL, "message")
    await store.dispatched(1)
    bot = adapter(service, store, api)
    bot.health = FakeHealth()

    task = asyncio.create_task(bot._poll())
    await spin(lambda: bot.health.polls_ok > 0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert bot.health.polls_ok > 0


@pytest.mark.asyncio
async def test_a_send_that_fails_is_reported_rather_than_swallowed_tg13(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The outbox suppresses a failed send so one 500 cannot stop the bot — which makes it invisible.

    That is the right call for the *task* and the wrong one for the human: a reply that never
    arrived looks exactly like a turn that never ran. Recording it on ``/health`` is what turns a
    silent drop into something answerable, and it deliberately does **not** make the daemon
    ``degraded`` — a 503 there invites the restart D9 forbids, killing runs and approvals that are
    perfectly healthy.
    """
    api.send_error = TelegramError("sendMessage", 403, "Forbidden: bot was blocked by the user")
    bot = adapter(service, store, api)
    bot.health = FakeHealth()

    await bot._queue(HOME, "filed under Cooking")
    await drain(bot)

    assert bot.health.send_errors, "a dropped reply that nothing records is a silent loss"
    assert bot.health.restarts == 0, "a send failure is not the subsystem being down (TG-13)"


# --------------------------------------------------------------------------------------
# § addressing: which chat talks to which expert (TG-1, TG-4, TG-26)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_chat_runs_against_its_own_agent_tg1(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The *pairing*, not the count — a mis-sent note landing in the wrong topic has no undo.

    Every earlier addressing test mapped both chats to one agent, so an adapter that routed every
    chat to the first agent in the mapping passed the whole suite: executed, replacing the routed
    agent with ``next(iter(config.chats.values()))`` broke nothing across 142 tests. Two chats, two
    different experts, and each chat's ``create_session`` asserted against the expert it was
    configured for is the only shape that bites (Task 7: repointed from ``create_thread``).
    """
    bot = adapter(service, store, api, chats={CHAT: COOKING, OTHER_CHAT: GRILLING})

    await deliver(bot, message_update(1, chat_id=CHAT, text="how long to rest a steak?"))
    await deliver(bot, message_update(2, chat_id=OTHER_CHAT, text="what temperature for coals?"))

    created = [call for call in service.calls if call[0] == "create_session"]
    assert [agent for _, (agent, *_rest) in created] == [COOKING, GRILLING]


@pytest.mark.asyncio
async def test_remapping_a_chat_starts_a_fresh_thread_on_the_new_expert_tg26(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Editing the mapping must not keep filing into the previous expert (TG-26, TG-1).

    ``bind`` wrote an ``agent_id`` column that nothing read, and ``_turn`` resolved the session from
    the chat alone. Executed: a chat bound under ``topic/cooking`` and then re-mapped to
    ``topic/grilling`` issued **zero** ``create_session`` calls and sent the new message to the
    original Cooking session — a write to the wrong topic, with no undo, invisible from the phone
    and from ``/health``. That is precisely the mis-file TG-1 was ruled to eliminate.

    The rotation is announced, because TG-27's reasoning is about *invisible* rotations rather than
    about rotation: a split the human cannot see is the failure class.
    """
    await bind(service, store, agent_id=COOKING)
    bot = adapter(service, store, api, chats={CHAT: GRILLING})

    await deliver(bot, message_update(text="what temperature for coals?"))

    assert (
        "create_session",
        (GRILLING, "what temperature for coals?", "telegram", None),
    ) in service.calls
    assert await store.bound_session(CHAT, GENERAL) != THREAD
    assert GRILLING in api.transcript


@pytest.mark.asyncio
async def test_a_stale_binding_is_announced_and_the_turn_still_completes(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The second kind of staleness ``_turn`` heals (a session closed/ended elsewhere, or a
    pre-Task-7 file's leftover thread id) must be **announced**, exactly like the agent-mismatch
    branch right above it in the source — not a silent rebind. Fix round 1, finding 1: the first
    build healed this without a word, contradicting the method's own docstring and the sibling
    ``_REMAPPED`` branch. Both the notice and the completed turn are asserted, because a fix that
    only announces and drops the message would be a different bug wearing the same diff.
    """
    await store.bind(CHAT, GENERAL, "a-session-nobody-minted", COOKING)

    await deliver(adapter(service, store, api), message_update(text="one more thing"))

    assert _STALE_SESSION in api.transcript
    assert (
        "create_session",
        (COOKING, "one more thing", "telegram", None),
    ) in service.calls
    assert "Filed under Cooking." in api.transcript, (
        "the turn must still complete, not just apologize"
    )
    assert await store.bound_session(CHAT, GENERAL) != "a-session-nobody-minted"


# --------------------------------------------------------------------------------------
# § the guards cover every inbound kind, not just `message` (TG-19, TG-20, TG-23, TG-35)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ten_edits_from_a_stranger_in_a_group_are_answered_never_tg19(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """``edited_message`` used to be dispatched above every guard, so it bypassed all of them.

    Executed against the build: ten ``edited_message`` updates from ``chat.id=-1001234567890,
    type="supergroup"`` produced **ten** outbound replies, where TG-19 allows at most one refusal
    and TG-20 allows none. ``ALLOWED_UPDATES`` subscribes to the kind, so this was reachable in
    production by anyone who found the bot's username — an unauthenticated, unbounded reply
    amplifier pointed at the owner's own per-chat send budget.
    """
    bot = adapter(service, store, api)

    for update_id in range(1, 11):
        await deliver(
            bot,
            {
                "update_id": update_id,
                "edited_message": {
                    "message_id": update_id,
                    "chat": {"id": -1001234567890, "type": "supergroup"},
                    "from": {"id": STRANGER},
                    "text": "file this",
                },
            },
        )

    assert api.sent == []
    assert service.calls == []


@pytest.mark.asyncio
async def test_ten_edits_from_one_unmapped_chat_are_rate_limited_like_a_message_tg23(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The per-chat send budget the rule protects cannot be drained by editing in a loop.

    Ten edits in an unmapped private chat produced ten replies before the guards were hoisted,
    where ten *messages* produced one. The window is what keeps a flood from spending the budget
    the owner's approval keyboards need, so it has to sit in front of every update kind.
    """
    bot = adapter(service, store, api)

    for update_id in range(1, 11):
        await deliver(
            bot,
            {
                "update_id": update_id,
                "edited_message": {
                    "message_id": update_id,
                    "chat": {"id": STRANGER_CHAT, "type": "private"},
                    "from": {"id": OWNER},
                    "text": "file this",
                },
            },
        )

    assert len(api.sent) == 1
    assert str(STRANGER_CHAT) in api.texts[0]


# --------------------------------------------------------------------------------------
# § the button verb table is read in both directions (TG-54, TG-64)
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# § a stale interrupt the daemon reports rather than the adapter predicts (TG-62)
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# § an approval nobody can read carries no buttons (TG-56, TG-66)
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# § a rejection carries no prose (TG-65)
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# § `/cancel` reaches a live run, and gives its subscriber back (TG-39, TG-52)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_gives_back_the_subscriber_it_attached_tg52(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """``attach`` holds a subscriber slot; ``/cancel`` walked away from it every single time.

    ``attach`` replays the hub from ``seq 0`` and keeps the subscriber until somebody detaches, so
    a ``/cancel`` with no ``finally`` leaked exactly the subscriber TG-52 exists to prevent — in
    the daemon whose whole value proposition is staying up for weeks. Executed against an
    attaching stub: after ``/cancel``, ``closed == []``.
    """
    closed: list[int] = []
    await bind(service, store)

    async def attaching(session_id: str) -> Any:
        service.calls.append(("attach_session", (session_id,)))
        return local_subscription([], closes=closed)

    service.attach_session = attaching  # type: ignore[method-assign]
    bot = adapter(service, store, api)

    await deliver(bot, message_update(text="/cancel"))

    assert service.cancelled == [RUN]
    assert closed == [1], "the attached subscription must be detached before returning"


@pytest.mark.asyncio
async def test_a_cancel_delivered_during_a_run_is_read_while_the_run_is_still_live_tg39(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Polling must not stop for the length of a turn — 16 s on the cloud model, 284 s locally.

    ``_poll`` awaited ``_dispatch``, and ``_dispatch`` awaits the whole run, so **no**
    ``getUpdates`` was issued while a turn ran. Executed against the build: a ``/cancel`` delivered
    half a second into a live run was not dispatched until the run had already finished, which is
    exactly the window the rule exists to make interruptible. Here the message's run is held open
    and the ``/cancel`` behind it must still be read and acted on.
    """
    gate = asyncio.Event()

    async def held() -> Any:
        await gate.wait()
        yield RunEnd(run_id=RUN, final_text="done")

    service.rows[THREAD] = thread_row()
    await store.bind(CHAT, GENERAL, THREAD, COOKING)
    holding = RunSubscription(
        handle=RunHandle(run_id=RUN, agent_id=COOKING, thread_id=THREAD),
        events=held(),
        close=lambda: None,
    )

    async def start_session_run(*_args: Any, **_kwargs: Any) -> Any:
        service.calls.append(("start_session_run", (THREAD, "")))
        return holding

    async def attaching(session_id: str) -> Any:
        return holding

    service.start_session_run = start_session_run  # type: ignore[method-assign]
    service.attach_session = attaching  # type: ignore[method-assign]
    await store.claim(
        0, CHAT, GENERAL, "message"
    )  # a warm ledger, so TG-30 does not drain the backlog
    await store.dispatched(0)
    api.pending = [
        message_update(1, text="file the steak note"),
        message_update(2, text="/cancel"),
    ]
    bot = adapter(service, store, api)

    async with asyncio.TaskGroup() as group:
        bot._group = group
        runner = group.create_task(bot._poll())
        await spin(lambda: bool(service.cancelled))
        gate.set()
        runner.cancel()

    assert service.cancelled == [RUN], "the cancel was read while the run it stops was still live"


# --------------------------------------------------------------------------------------
# § a restart owes each chat something (TG-29, TG-31)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_crash_before_start_run_names_the_loss_to_the_chat_that_lost_it_tg29(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The notice has to reach **the chat that lost the message**, with that chat's own count.

    ``orphans()`` selected ``update_id`` alone, so the adapter had a total and no addressee: it
    broadcast to every mapped chat whose id happened to be in ``owner_user_ids``, which for the
    suite's own constants — mapping ``{770001: 'topic/cooking'}``, owners ``{42}`` — is **zero
    notices** for a real orphan. A silent loss is the one outcome this rule names as unacceptable.
    """
    await store.claim(
        100, CHAT, GENERAL, "message"
    )  # claimed, and the process died before `start_run`
    bot = adapter(service, store, api)

    await bot._report_orphans()
    await drain(bot)

    assert [entry["chat_id"] for entry in api.sent] == [CHAT]
    assert "send them again" in api.texts[0]
    assert service.calls == [], "a named loss is never a retry"


@pytest.mark.asyncio
async def test_a_run_that_was_admitted_is_never_reported_as_lost_tg29(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """ "I lost your message — please re-send" for a turn that already ran is a duplicate write.

    ``dispatched()`` was called only after the whole turn had been relayed, and a turn is 8-12
    model calls, so a task cancelled mid-stream left a row indistinguishable from a crash before
    ``start_run``. Executed: an update whose run was still streaming when the task was cancelled
    gave ``orphans() == [100]`` with ``start_run`` already counted once — and re-sending produces
    the divergent second write into a tree with no undo that decision T exists to stop. Three
    states, and the middle one is what makes the notice honest.
    """
    await store.claim(100, CHAT, GENERAL, "message")
    await store.started(100, THREAD, RUN)

    assert await store.orphans() == []
    assert await store.unfinished() == [(100, CHAT, GENERAL, THREAD)]

    bot = adapter(service, store, api)
    await bot._report_orphans()
    await drain(bot)

    assert api.sent == []


@pytest.mark.asyncio
async def test_a_restarted_session_says_it_cannot_recover_the_text_task7(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Task 7 successor of ``test_a_restart_delivers_the_reply_the_chat_never_heard_tg31``: a
    session carries no message history on this Protocol, so branch (c) of TG-31's re-sync can no
    longer quote the reply — it says so honestly (:data:`~pkb.server.telegram._LATE_UNKNOWN`)
    rather than guessing at the text or staying silent, and it is still attributed and still one
    message, never a replay.
    """
    await bind(service, store)
    await store.claim(100, CHAT, GENERAL, "message")
    await store.started(100, THREAD, RUN)
    bot = adapter(service, store, api)

    await bot._recover()
    await drain(bot)

    assert len(api.sent) == 1
    assert "session file" in api.texts[0]
    assert "start_run" not in [kind for kind, _ in service.calls]


# --------------------------------------------------------------------------------------
# § what the bot never drops, and what a startup notice may never do (TG-49, TG-13)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_reply_is_never_dropped_for_a_full_outbox_tg49(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The drop path could only ever discard a frame the rule forbids dropping.

    TG-43 already makes ``ToolStart``/``ToolEnd``/``SubagentEnd`` produce nothing, so the only
    things that ever reach this queue are a ``MessageComplete``, the roster line and a terminal
    note — every one of them on the never-drop list. Executed with the outbox at capacity (the
    state a ``retry_after: 30`` inside the pump produces), a run ending in
    ``MessageComplete(text="THE REPLY THE HUMAN NEEDS")`` lost it, logged as "dropping a **progress**
    message". Filling the queue and asserting the reply still arrives is the shape that bites.
    """
    bot = adapter(service, store, api)

    assert bot._outbox.maxsize == 0, "a bounded outbox has a drop path, and every frame here is one"
    for index in range(200):  # far past the 64-slot cap the build shipped
        await bot._queue(HOME, f"earlier {index}")
    await bot._queue(HOME, "THE REPLY THE HUMAN NEEDS")
    await drain(bot)

    assert "THE REPLY THE HUMAN NEEDS" in api.texts
    assert len(api.texts) == 201, "nothing queued was dropped on the way out"


@pytest.mark.asyncio
async def test_a_chat_that_blocked_the_bot_never_makes_the_daemon_degraded_tg13(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A 403 on a startup notice used to raise straight into ``_supervise``.

    ``_report_orphans`` ran before the task group and outside any suppression, and the cold-start
    notice had the same shape. Executed against the real ``_supervise`` and the real
    ``HealthState``: one mapped chat whose sends all return ``403 bot was blocked by the user``
    plus one orphaned row gave ``restarts: 3`` and climbing, ``state: "restarting"`` and
    ``status: "degraded"``, forever — the restart loop D9 forbids, for a send failure the rule says
    must never change ``status``. Asserted against the real ``SubsystemState``, because the
    ``FakeHealth`` used elsewhere has a ``restarts`` field nothing in the codebase increments.
    """
    from pkb.server.health import HealthState

    health = HealthState(runtime_open=True)
    health.telegram.running()
    api.send_error = TelegramError("sendMessage", 403, "Forbidden: bot was blocked by the user")
    await store.claim(100, CHAT, GENERAL, "message")
    bot = adapter(service, store, api)
    bot.health = health.telegram

    await bot._report_orphans()

    assert health.status == "ok"
    assert health.telegram.restarts == 0
    assert health.telegram.last_send_error is not None
    assert "403" in health.telegram.last_send_error


@pytest.mark.asyncio
async def test_an_invalid_mapping_entry_is_visible_on_health_not_only_in_the_log_tg18(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """ "Reported, not fatal" has to mean somewhere a human looks.

    The only report was one ERROR line at startup, which is exactly the thing that has scrolled
    away by the time somebody reads ``/health`` — so a deployment naming a renamed topic served
    ``telegram: {chats: 2, unmapped_agents: [...], last_error: null, state: "running"}``,
    indistinguishable from a correct one, and the bad entry subtracted nothing from
    ``unmapped_agents`` either. It never changes ``status``: the subsystem is running, one line of
    configuration is wrong.
    """
    from pkb.server.health import HealthState

    health = HealthState(runtime_open=True)
    health.telegram.running()
    bot = adapter(service, store, api, chats={CHAT: COOKING, OTHER_CHAT: "topic/nope"})
    bot.health = health.telegram

    bot.check_mapping()

    assert health.telegram.payload()["invalid_chats"] == (OTHER_CHAT,)
    assert health.status == "ok"


# --------------------------------------------------------------------------------------
# § the token is not in any repr (TG-16, TG-24)
# --------------------------------------------------------------------------------------


def test_the_token_is_absent_from_every_repr_tg24() -> None:
    """``HttpBotApi`` was hardened for this; the object holding the same secret was not.

    Executed against the build: ``repr(TelegramConfig(token="123456789:AAF-SECRET", ...))`` printed
    the token whole, and ``repr(TelegramAdapter(...))`` embedded it because ``config`` is an
    ordinary dataclass field that a generated ``repr`` recurses into. The config is held by the
    adapter for the whole life of the supervised task, so it sits in a frame of every traceback the
    bot produces, and a ``repr`` is what an f-string, a ``%r`` log call, ``logging.exception``'s
    frame dump and pytest's locals dump all print — during the mutation run that produced this
    work, pytest's assertion rewriting printed the full ``TelegramConfig(token=...)`` into the
    failure output. The bot id survives masking because ``getMe`` publishes it anyway and an
    operator running two bots needs to know which one this is.
    """
    token = "123456789:AAF-FAKE-TOKEN-DO-NOT-USE-abcdefghijkl"
    config = TelegramConfig(token=token, chats={CHAT: COOKING}, owner_user_ids=frozenset({OWNER}))
    bot = TelegramAdapter(service=StubService(), store=object(), api=object(), config=config)  # type: ignore[arg-type]

    for rendered in (repr(config), f"{config!r}", str(config), repr(bot), f"{bot!r}"):
        assert token not in rendered
        assert "AAF-FAKE-TOKEN" not in rendered
    assert "123456789:***" in repr(config), (
        "the bot id stays, so two deployments stay tellable apart"
    )
