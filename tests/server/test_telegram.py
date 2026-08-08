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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio

from pkb.clients.approval import TRUNCATION_MARKER, truncate
from pkb.contracts import (
    CANCELLED_MESSAGE,
    ActionView,
    AgentEvent,
    ApprovalPendingError,
    ApprovalRequest,
    Decision,
    InterruptEvent,
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
    expert_thread_id,
)
from pkb.server.telegram import (
    COMMANDS,
    TelegramAdapter,
    TelegramConfig,
    callback_data,
    keyboard_for,
    split_message,
    utf16_len,
)
from pkb.server.telegram_api import CALLBACK_DATA_LIMIT, MESSAGE_LIMIT, TelegramError
from pkb.service import RunSubscription, Thread, ThreadDetail
from pkb.service.telegram import PROMPTS_TABLE, SqliteTelegramStore
from tests.server.stub import AGENTS, COOKING, GRILLING, LIBRARIAN, NOW, StubService

CHAT = 770001
"""The one mapped chat. Its agent is :data:`COOKING`."""

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

    async def get_me(self) -> Mapping[str, Any]:
        return {"id": 1, "username": "pkb_test_bot"}

    async def get_updates(self, offset: int | None) -> Sequence[Mapping[str, Any]]:
        self.journal.append(("get_updates", {"offset": offset}))
        return []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: Sequence[Sequence[Mapping[str, str]]] | None = None,
    ) -> Mapping[str, Any]:
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

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.journal.append(("answer_callback", {"id": callback_id, "text": text}))

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        self._check_text("editMessageText", text)
        self.journal.append(
            ("edit_message", {"chat_id": chat_id, "message_id": message_id, "text": text})
        )

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
    def answers(self) -> list[dict[str, Any]]:
        return self.of("answer_callback")

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
    await deliver(bot, callback_update(callback_data(handle, index, verb), **kwargs))


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
    await store.bind(chat_id, thread_id, agent_id)
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


async def handles(connection: aiosqlite.Connection) -> list[str]:
    """Every approval the adapter has staged, oldest first — read from the durable table."""
    cursor = await connection.execute(f"SELECT handle FROM {PROMPTS_TABLE} ORDER BY rowid")
    return [str(row[0]) for row in await cursor.fetchall()]


async def prompt_rows(connection: aiosqlite.Connection) -> list[dict[str, Any]]:
    cursor = await connection.execute(
        f"SELECT handle, chat_id, thread_id, interrupt_id, action_count, resolved "
        f"FROM {PROMPTS_TABLE} ORDER BY rowid"
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
async def test_an_unmapped_chat_learns_its_own_id_and_no_agent_id_tg9(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A stranger is told the one datum they cannot get elsewhere, and nothing about the tree.

    A bot's username is discoverable, so anybody can produce an unmapped chat — and the **topic
    titles are the sensitive part** of a private knowledge base. A reply that listed the agents
    would hand the shape of somebody's notes to whoever typed ``/start``. The chat id is worthless
    to a stranger and is the only thing the owner cannot look up any other way.
    """
    bot = adapter(service, store, api)

    await deliver(bot, message_update(chat_id=STRANGER_CHAT, sender=STRANGER))

    assert len(api.sent) == 1
    reply = api.texts[0]
    assert str(STRANGER_CHAT) in reply
    for descriptor in AGENTS:
        assert descriptor.agent_id not in reply
        assert descriptor.title not in reply
    assert service.calls == [], "the catalog was read on a path that must not touch the service"


@pytest.mark.asyncio
async def test_an_unmapped_chat_runs_nothing_and_says_the_message_was_dropped_tg10(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    connection: aiosqlite.Connection,
) -> None:
    """Nothing ran, nothing was stored, and the human is told so in the same breath.

    The two failure modes are symmetrical and both silent: half-stored text reappears in an
    unexpected topic later, and silently dropped text leaves the human believing their note was
    filed. Saying "I have not kept this" is the only thing that makes the outcome actionable —
    re-send it once the chat is mapped.
    """
    bot = adapter(service, store, api)

    await deliver(bot, message_update(chat_id=STRANGER_CHAT, sender=STRANGER, text="steak notes"))

    assert service.calls == []
    assert await store.bound_thread(STRANGER_CHAT) is None
    assert await prompt_rows(connection) == []
    assert "not kept" in api.texts[0]


# --------------------------------------------------------------------------------------
# § the owner allow-list — the system's only authentication boundary (decision X)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stranger_in_a_mapped_chat_runs_nothing_decision_x(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The mapping says *which expert*; it never says *who may write*.

    TG-1's mapping was ruled about addressing. A chat id is a guessable integer and a bot token is a
    public inbound path into a process with no authentication that writes to a tree with no undo, so
    the sender is checked independently of the chat — one comparison that cannot be retrofitted
    after a token leaks.
    """
    bot = adapter(service, store, api)

    await deliver(bot, message_update(sender=STRANGER, text="file this under Cooking"))

    assert service.calls == []
    assert await store.bound_thread(CHAT) is None
    assert len(api.sent) == 1
    assert "does not accept" in api.texts[0]


@pytest.mark.asyncio
async def test_a_stranger_pressing_a_button_resolves_nothing_decision_x(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    connection: aiosqlite.Connection,
    journal: Journal,
) -> None:
    """A button in a chat is visible to whoever the chat is shared with; the press is still checked.

    An approve button is the one control in this system that commits an irreversible write, and a
    forwarded message carries its keyboard. Without the sender check on ``callback_query.from.id``
    the allow-list would guard the cheap path (typing) and leave the expensive one (deciding) open.
    """
    await bind(service, store)
    bot = adapter(service, store, api)
    service.pending = approval()
    await bot._post_approval(CHAT, approval())
    handle = (await handles(connection))[0]
    journal.clear()

    await press(bot, handle, 0, "a", sender=STRANGER)

    assert "resume" not in kinds(journal)
    assert (await prompt_rows(connection))[0]["resolved"] is False
    assert api.edits == [], "a stranger's press must not clear the owner's keyboard either"


@pytest.mark.xfail(
    reason="TG-20: a non-allowed callback gets no refusal — answer_callback is sent with empty "
    "text, so the button simply goes quiet and the presser learns nothing",
    strict=True,
)
@pytest.mark.asyncio
async def test_a_stranger_pressing_a_button_is_told_why_decision_x(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    connection: aiosqlite.Connection,
) -> None:
    """Silence and success look identical on a phone, so a refused press has to say it was refused.

    Telegram shows a spinner until the query is answered and then shows whatever text came back —
    nothing if it was empty. A stranger who presses approve and sees the spinner stop has every
    reason to believe the write happened, and the owner is never told somebody tried.
    """
    await bind(service, store)
    bot = adapter(service, store, api)
    service.pending = approval()
    await bot._post_approval(CHAT, approval())
    handle = (await handles(connection))[0]

    await press(bot, handle, 0, "a", sender=STRANGER)

    assert api.answers[-1]["text"] != ""


@pytest.mark.asyncio
async def test_a_group_chat_runs_nothing_tg8(
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

    assert service.calls == []
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

    await bot._consume(CHAT, local_subscription(script, closes=[]))
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
async def test_four_runs_end_four_distinguishable_ways_tg50(
    store: SqliteTelegramStore, journal: Journal
) -> None:
    """No fall-through, because in process the terminal events carry nothing to fall through on.

    ``RunEnd`` has no ``status`` field and ``RunError`` has no ``code`` — both are authored by the
    SSE encoder for wire clients and do not exist on the dataclasses this adapter receives. A client
    that matches three states therefore renders "done" for every provider failure, and over an
    interrupted run "done" means a parked, irreversible write that nobody is ever asked about.
    """
    request = approval()
    scripts: dict[str, list[AgentEvent]] = {
        "completed": reply_script(),
        "interrupted": [
            InterruptEvent(run_id=RUN, request=request),
            RunEnd(run_id=RUN, final_text=""),
        ],
        "cancelled": [RunError(run_id=RUN, message=CANCELLED_MESSAGE, retryable=True)],
        "error": [RunError(run_id=RUN, message="provider timeout", retryable=True)],
    }

    transcripts: dict[str, str] = {}
    for name, script in scripts.items():
        api = FakeBotApi(list(journal))
        service = ScriptedService(api.journal, events=script)
        service.pending = request
        await bind(service, store)
        bot = adapter(service, store, api)
        await deliver(bot, message_update())
        transcripts[name] = api.transcript

    assert len(set(transcripts.values())) == 4, transcripts
    assert "decision" in transcripts["interrupted"].lower()
    assert "cancel" in transcripts["cancelled"].lower()
    assert "failed" in transcripts["error"].lower()


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
    second time. One ``get_thread`` and an honest "I do not know" is the only safe answer.
    """
    service = ScriptedService(journal, events=[])
    service.rows[THREAD] = thread_row()
    closes: list[int] = []
    bot = adapter(service, store, api)
    journal.clear()

    await bot._consume(CHAT, local_subscription(reply_script()[:1], closes=closes))
    await drain(bot)

    assert kinds(journal).count("get_thread") == 1
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

    with pytest.raises(RuntimeError, match="the hub went away"):
        await bot._consume(CHAT, subscription)

    assert closes == [1]


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

    await bot._consume(CHAT, local_subscription(reply_script(), closes=closes))
    await drain(bot)

    assert closes == [1]


# --------------------------------------------------------------------------------------
# § refusals from `start_run` — both of them are normal (TG-37, TG-38, TG-53)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_pending_approval_reposts_the_keyboard_and_quotes_the_message_tg37(
    store: SqliteTelegramStore,
    api: FakeBotApi,
    connection: aiosqlite.Connection,
    journal: Journal,
) -> None:
    """RT-39 refuses the turn; the chat still has to be able to *resolve* what is blocking it.

    Sending to an interrupted thread silently discards the interrupt, which is why the refusal
    exists. But on a phone the original keyboard has scrolled away hours ago, so telling the human
    "there is an approval pending" without re-posting it makes the state unresolvable from the only
    channel they are in — and the message they just typed has to come back, because it was not sent.
    """
    live = approval(interrupt_id="i-live")
    service = ScriptedService(journal, refusals=[ApprovalPendingError("an approval is pending")])
    await bind(service, store)
    service.pending = live
    bot = adapter(service, store, api)

    await deliver(bot, message_update(text="also: try 8 minutes of rest"))

    assert "also: try 8 minutes of rest" in api.transcript
    assert len(api.with_keyboard) == 1
    rows = await prompt_rows(connection)
    assert [row["interrupt_id"] for row in rows] == ["i-live"]


@pytest.mark.asyncio
async def test_a_pending_approval_neither_rotates_nor_retries_tg37(
    store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """The blocked message is dropped deliberately, and the thread it was aimed at is kept.

    Rotating to a fresh thread would hide the pending approval behind a new conversation and leave
    an irreversible write parked forever; retrying is a second POST at a thread that may already
    have written. Neither is recoverable, and both look like success from the chat.
    """
    service = ScriptedService(journal, refusals=[ApprovalPendingError("an approval is pending")])
    await bind(service, store)
    service.pending = approval()
    bot = adapter(service, store, api)

    await deliver(bot, message_update(text="one more thing"))

    assert kinds(journal).count("start_run") == 1
    assert "create_thread" not in [name for name, _ in service.calls]
    assert await store.bound_thread(CHAT) == THREAD


@pytest.mark.asyncio
async def test_a_fan_out_gate_parked_on_a_child_is_still_reposted_tg53(
    store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """The parent's ``pending`` is null while the expert holds the interrupt — do not believe it.

    A Librarian turn that routes to an expert parks the gate on the expert's **derived** thread, so
    a recovery that reads only the chat's own thread concludes "no approval" and the human's buttons
    stay dead, with nothing logged anywhere. The children have to be walked.
    """
    child_id = expert_thread_id(THREAD, COOKING)
    service = ScriptedService(journal, refusals=[ApprovalPendingError("an approval is pending")])
    await bind(service, store, agent_id=LIBRARIAN)
    service.details[THREAD] = ThreadDetail(
        thread=thread_row(THREAD, LIBRARIAN),
        pending=None,
        children=(thread_row(child_id, COOKING),),
    )
    service.details[child_id] = ThreadDetail(
        thread=thread_row(child_id, COOKING),
        pending=approval(thread_id=child_id, interrupt_id="i-child"),
    )
    bot = adapter(service, store, api, chats={CHAT: LIBRARIAN})

    await deliver(bot, message_update(text="and another note"))

    assert len(api.with_keyboard) == 1


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


@pytest.mark.asyncio
async def test_a_nine_thousand_character_description_arrives_whole_before_the_buttons_tg56(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """Change the container, never the text — the human approves what is in front of them.

    Measured on real ``describe_write`` output, a 120-bullet note approval is 9,218 characters and a
    delete is 7,868, because a delete embeds the whole current file. Truncating to 4,096 shows
    bullets 0-59 and hides 60-119 under an irreversible approve button. The document goes first so
    the complete text is already in the chat when the keyboard arrives.
    """
    description = "\n".join(
        f"- bullet {n}: something the human wrote about heat" for n in range(200)
    )
    assert len(description) > 9000
    bot = adapter(service, store, api)

    await bot._post_approval(CHAT, approval(actions=(action(description=description),)))

    assert len(api.documents) == 1
    assert api.documents[0]["content"] == description.encode("utf-8")
    order = kinds(api.journal)
    assert order.index("send_document") < order.index("send_message")
    assert api.rejected == []


@pytest.mark.asyncio
async def test_the_validation_label_leads_the_button_message_tg66(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """A label at the bottom of a 9,000-character description is a label nobody reads.

    The gate already ran the validator server-side and said the draft will fail; approving it burns
    one of three bounded attempts on content the human explicitly endorsed. ``approve`` stays
    offered because it is legally allowed — but the warning has to be the first thing on the screen,
    not the last, and the adapter never re-runs validation to find out.
    """
    label = "This draft currently fails validation: FM-3 frontmatter is missing 'title'"
    description = f"Proposed content:\n\n# Steak\n\n{label}\n"
    bot = adapter(service, store, api)

    await bot._post_approval(CHAT, approval(actions=(action(description=description),)))

    button_message = api.with_keyboard[0]["text"]
    assert button_message.splitlines()[0] == label
    assert "still fail validation" in button_message


@pytest.mark.asyncio
async def test_a_clean_description_gets_no_validation_line_tg66(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The label is a prefix match on the server's own text, so a clean draft is silent about it.

    A warning that appears on every approval is a warning that is scrolled past on the one where it
    matters — and inventing it client-side would mean a second implementation of validation in a
    module that cannot even read the tree.
    """
    bot = adapter(service, store, api)

    await bot._post_approval(CHAT, approval())

    assert "fails validation" not in api.with_keyboard[0]["text"]


# --------------------------------------------------------------------------------------
# § a press is resolved from durable state, never from memory (TG-58, TG-59, TG-60)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_fresh_adapter_resolves_a_press_it_never_sent_tg58(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    connection: aiosqlite.Connection,
    journal: Journal,
) -> None:
    """This is the whole answer to "what happens to a button pressed after a restart".

    The supervisor restarts the task carrying **nothing** across — every dict, client and
    subscription of the previous invocation is gone — and Telegram redelivers an unconfirmed update
    for 24 hours. So the press below arrives at an adapter that never sent the message: the durable
    row supplies the thread and the *server* supplies the request. Making the durable path the only
    path is what gets the restart case exercised by every other test in this file.
    """
    request = approval(interrupt_id="i-live")
    await bind(service, store)
    service.pending = request
    poster = adapter(service, store, api)
    await poster._post_approval(CHAT, request)
    handle = (await handles(connection))[0]

    restarted = adapter(service, store, FakeBotApi(journal))
    journal.clear()
    await press(restarted, handle, 0, "a")

    assert service.resumed == [(THREAD, (Decision(type="approve"),), "i-live")]


@pytest.mark.asyncio
async def test_an_approval_the_adapter_cannot_locate_resumes_nothing_tg58(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """A handle with no row is a press the adapter cannot place — and it never guesses a thread.

    Guessing means resolving against whatever is pending *now*, which is a different write than the
    one the human looked at, applied silently and with no undo. Saying the approval could not be
    located, and pointing at the TUI, is the only honest option.
    """
    await bind(service, store)
    bot = adapter(service, store, api)
    journal.clear()

    await press(bot, "deadbeef", 0, "a")

    assert "resume" not in kinds(journal)
    assert "already answered" in api.transcript or "could not be located" in api.transcript


@pytest.mark.asyncio
async def test_a_fan_out_approval_resolves_against_the_experts_own_thread_tg59(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    connection: aiosqlite.Connection,
) -> None:
    """The decisions go where the request says, not where the chat happens to be looking.

    In a fan-out the gate parks on the expert's derived thread while the chat is bound to the
    Librarian's. Posting a delegate's decisions to the parent is a ``409`` on a perfectly valid
    approval — the failure hardest to debug from a client, because everything about the approval
    looks right.
    """
    child_id = expert_thread_id(THREAD, COOKING)
    await bind(service, store, agent_id=LIBRARIAN)
    request = approval(thread_id=child_id, interrupt_id="i-child")
    service.details[child_id] = ThreadDetail(thread=thread_row(child_id, COOKING), pending=request)
    bot = adapter(service, store, api, chats={CHAT: LIBRARIAN})
    await bot._post_approval(CHAT, request)
    handle = (await handles(connection))[0]

    await press(bot, handle, 0, "a")

    assert [thread for thread, _, _ in service.resumed] == [child_id]


@pytest.mark.asyncio
async def test_answering_an_experts_approval_does_not_rebind_the_chat_tg59(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    connection: aiosqlite.Connection,
) -> None:
    """Answering a delegate is not switching conversations.

    Rebinding would silently move the chat into the expert's thread, so the human's *next* message —
    which they think continues the same conversation — would start a turn with a different agent,
    against a different history, and file it somewhere else.
    """
    child_id = expert_thread_id(THREAD, COOKING)
    await bind(service, store, agent_id=LIBRARIAN)
    request = approval(thread_id=child_id, interrupt_id="i-child")
    service.details[child_id] = ThreadDetail(thread=thread_row(child_id, COOKING), pending=request)
    bot = adapter(service, store, api, chats={CHAT: LIBRARIAN})
    await bot._post_approval(CHAT, request)

    await press(bot, (await handles(connection))[0], 0, "a")

    assert await store.bound_thread(CHAT) == THREAD


@pytest.mark.asyncio
async def test_one_of_two_actions_answered_submits_nothing_tg60(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    connection: aiosqlite.Connection,
    journal: Journal,
) -> None:
    """A partial answer is not an answer, and padding the gap is how a human approves a write blind.

    ``resolve`` is total: one ``Answer`` per action or nothing. One approval can carry several
    writes and the phone shows them one message at a time, so submitting after the first press would
    commit a second file the human has not scrolled to yet. Stopping halfway is also a legitimate
    "later" — the interrupt stays parked and the TUI can still answer it.
    """
    request = approval(
        actions=(
            action(tool="write_file", description="first write"),
            action(tool="delete_file", description="second write", reason="breadth-approval"),
        )
    )
    await bind(service, store)
    service.pending = request
    bot = adapter(service, store, api)
    await bot._post_approval(CHAT, request)
    assert len(api.with_keyboard) == 2, "one message per action, each with its own description"
    handle = (await handles(connection))[0]
    journal.clear()

    await press(bot, handle, 0, "a")

    assert "resume" not in kinds(journal)
    assert service.resumed == []


@pytest.mark.asyncio
async def test_the_last_answer_submits_every_decision_in_action_order_tg60(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    connection: aiosqlite.Connection,
) -> None:
    """Decisions are positional, so index 1's verb must never land on index 0's action.

    One ``resolve`` and one ``resume``, with the answers ordered by the freshly re-read request
    rather than by the order the human's thumbs arrived in. Answer index 1 with index 0's decision
    and the human approves a write they rejected, silently, with no undo.
    """
    request = approval(
        actions=(
            action(tool="write_file", description="first write"),
            action(tool="write_file", description="second write"),
        )
    )
    await bind(service, store)
    service.pending = request
    bot = adapter(service, store, api)
    await bot._post_approval(CHAT, request)
    handle = (await handles(connection))[0]

    await press(bot, handle, 1, "r")
    await press(bot, handle, 0, "a")

    assert len(service.resumed) == 1
    _, decisions, interrupt_id = service.resumed[0]
    assert [decision.type for decision in decisions] == ["approve", "reject"]
    assert interrupt_id == request.interrupt_id


# --------------------------------------------------------------------------------------
# § the callback is answered first, always (TG-61)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_callback_is_answered_before_the_resume_tg61(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    connection: aiosqlite.Connection,
    journal: Journal,
) -> None:
    """Telegram spins until the query is answered; a resume is 8-12 model calls away from returning.

    Answer it afterwards and the button spins for the length of a turn — about 16 seconds on the
    cloud model and **284** on the local fallback — the query expires, and the human, who has no
    other feedback, presses again against an interrupt the first press already resolved.
    """
    await bind(service, store)
    service.pending = approval()
    bot = adapter(service, store, api)
    await bot._post_approval(CHAT, approval())
    handle = (await handles(connection))[0]
    journal.clear()

    await press(bot, handle, 0, "a")

    order = kinds(journal)
    assert order.index("answer_callback") < order.index("resume")


@pytest.mark.asyncio
async def test_the_callback_is_answered_while_the_resume_is_still_blocked_tg61(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    connection: aiosqlite.Connection,
    journal: Journal,
) -> None:
    """ "First" has to mean before the slow work starts, not merely earlier in the source.

    A ``resume`` that never returns — a wedged provider, a 284-second local turn — must still leave
    the button answered. This one is held open deliberately: the answer is asserted while the resume
    is provably still in flight.
    """
    await bind(service, store)
    service.pending = approval()
    bot = adapter(service, store, api)
    await bot._post_approval(CHAT, approval())
    handle = (await handles(connection))[0]
    gate = asyncio.Event()
    service.resume_gate = gate
    journal.clear()

    pressing = asyncio.create_task(bot._dispatch(callback_update(callback_data(handle, 0, "a"))))
    for _ in range(200):
        if service.resumed:
            break
        await asyncio.sleep(0)
    await asyncio.sleep(0.02)

    assert not pressing.done(), "the resume is meant to be blocked at this point"
    assert api.answers, "the human's button was still spinning while the resume was in flight"
    gate.set()
    await pressing
    await drain(bot)


@pytest.mark.asyncio
async def test_a_stale_press_is_answered_before_anything_else_tg61(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """Including the paths that do nothing — an unanswered query is a spinner either way.

    The refused and stale paths are exactly where an implementation is tempted to return early, and
    a press that returns without answering leaves the phone showing progress for a decision that was
    never going to be applied.
    """
    await bind(service, store)
    bot = adapter(service, store, api)
    journal.clear()

    await press(bot, "cafef00d", 0, "a")

    assert kinds(journal)[0] == "answer_callback"


# --------------------------------------------------------------------------------------
# § a message lives forever, and so do its buttons (TG-62, TG-63)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_press_on_an_interrupt_another_channel_answered_resumes_nothing_tg62(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    connection: aiosqlite.Connection,
    journal: Journal,
) -> None:
    """Two channels on one approval is the design, not an edge case.

    The TUI answered it at the desk; the phone still has the keyboard. Re-reading the live approval
    before resolving is what turns that into a clean "already answered" instead of applying the
    human's taps to whatever interrupt is pending now — and the press is never retried, because a
    retry either spins or answers a different write.
    """
    await bind(service, store)
    service.pending = approval(interrupt_id="i-live")
    bot = adapter(service, store, api)
    await bot._post_approval(CHAT, approval(interrupt_id="i-live"))
    handle = (await handles(connection))[0]
    service.pending = None  # the TUI answered it in the meantime
    journal.clear()

    await press(bot, handle, 0, "a")

    assert "resume" not in kinds(journal)
    assert service.resumed == []
    assert "already answered" in api.transcript


@pytest.mark.asyncio
async def test_every_message_of_an_approval_loses_its_buttons_tg63(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    connection: aiosqlite.Connection,
) -> None:
    """A TUI modal closes; a Telegram message sits in the chat forever with its buttons live.

    Without removal the human scrolls back a week later, presses approve on a write that already
    happened, and either gets a stale answer (lucky) or answers whatever interrupt is pending now
    (not lucky). Every message of the approval has to be edited, not just the one that was pressed.
    """
    request = approval(
        actions=(action(description="first write"), action(description="second write"))
    )
    await bind(service, store)
    service.pending = request
    bot = adapter(service, store, api)
    await bot._post_approval(CHAT, request)
    posted = {entry["chat_id"] for entry in api.with_keyboard}
    assert posted == {CHAT}
    handle = (await handles(connection))[0]

    await press(bot, handle, 0, "a")
    await press(bot, handle, 1, "a")

    assert len({entry["message_id"] for entry in api.edits}) == 2
    assert all("answered" in entry["text"] for entry in api.edits)


@pytest.mark.asyncio
async def test_a_press_replayed_after_the_answer_resumes_nothing_tg63(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    connection: aiosqlite.Connection,
) -> None:
    """Telegram redelivers unconfirmed updates for 24 hours, so a duplicate press is not exotic.

    The durable row is marked resolved the moment the decisions go out, and that flag — not the
    presence of a keyboard in the chat — is what stops the second press. A resume issued twice
    against one interrupt applies a decision to a run that has already moved on.
    """
    await bind(service, store)
    service.pending = approval()
    bot = adapter(service, store, api)
    await bot._post_approval(CHAT, approval())
    handle = (await handles(connection))[0]

    await press(bot, handle, 0, "a")
    await press(bot, handle, 0, "a", query_id="cbq-2")

    assert len(service.resumed) == 1
    assert (await prompt_rows(connection))[0]["resolved"] is True
    assert "already answered" in api.transcript


@pytest.mark.asyncio
async def test_a_press_that_cannot_be_applied_says_so_tg58(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    connection: aiosqlite.Connection,
    journal: Journal,
) -> None:
    """The poll loop swallows exceptions so one bad update cannot stop the bot — so this must not.

    A failure here would otherwise be completely silent: no reply, no edit, no alert, and a human
    pressing a button that has quietly stopped working. Saying "I could not apply that, nothing was
    sent, the TUI can still resolve it" is the whole difference between a dead button and a
    recoverable one.
    """
    await bind(service, store)
    service.pending = approval(thread_id="t-vanished")
    bot = adapter(service, store, api)
    await bot._post_approval(CHAT, approval(thread_id="t-vanished"))
    handle = (await handles(connection))[0]
    journal.clear()

    await press(bot, handle, 0, "a")

    assert "resume" not in kinds(journal)
    assert "could not" in api.transcript
    assert "Nothing was sent" in api.transcript


# --------------------------------------------------------------------------------------
# § the keyboard is derived, never drawn from memory (TG-54, TG-55, TG-57, TG-64)
# --------------------------------------------------------------------------------------


def test_the_keyboard_is_built_from_allowed_decisions_not_a_hardcoded_pair_tg54() -> None:
    """Today's gate table and a hardcoded approve/reject agree — which is what hides the bug.

    Dropping ``edit`` from all twelve shipped gate reasons happens to leave approve/reject every
    time, so the wrong implementation passes every test that exists. The day a gate ships
    ``('edit', 'reject')`` the hardcoded bar draws an Approve button the server will reject, at the
    moment a human is deciding on an irreversible write. Deriving also preserves the server's
    ordering, which decides which button a hurried thumb lands on first.
    """
    narrowed = keyboard_for(action(allowed=("edit", "reject")), "7f3a2b1c", 0)
    assert narrowed is not None
    assert [button["text"] for row in narrowed for button in row] == ["Reject"]

    ordered = keyboard_for(action(allowed=("reject", "approve")), "7f3a2b1c", 0)
    assert ordered is not None
    assert [button["text"] for row in ordered for button in row] == ["Reject", "Approve"]


def test_approve_and_reject_never_share_a_row_tg64() -> None:
    """On a phone, two buttons in one row are neighbouring keys under one thumb.

    A thumb on a moving train is a worse input device than a keyboard, and the two outcomes here are
    "write this irreversibly" and "do not". Separate rows is the cheapest possible mitigation and it
    costs nothing.
    """
    keyboard = keyboard_for(action(allowed=("approve", "reject")), "7f3a2b1c", 0)
    assert keyboard is not None
    assert [len(row) for row in keyboard] == [1, 1]


@pytest.mark.asyncio
async def test_an_action_nobody_can_answer_is_a_hand_off_not_an_empty_keyboard_tg55(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    journal: Journal,
) -> None:
    """An empty keyboard reads as a delivery failure; a message with none reads as a hand-off.

    ``allowed_decisions=()`` comes from a malformed review config, and ``validate_decisions`` would
    then reject *every* decision type — so an approval nobody can answer parks the thread forever
    and RT-39 refuses the chat's next message. The chat is bricked with no visible cause unless this
    message explains itself.
    """
    bot = adapter(service, store, api)

    await bot._post_approval(CHAT, approval(actions=(action(allowed=()),)))

    assert api.with_keyboard == []
    assert "resume" not in kinds(journal)
    assert "TUI" in api.transcript


@pytest.mark.xfail(
    reason="TG-55: the hand-off message names neither the thread id nor the agent, so the human "
    "cannot find the parked approval in the TUI it points them at",
    strict=True,
)
@pytest.mark.asyncio
async def test_the_hand_off_names_the_thread_it_parked_on_tg55(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """ "Open the TUI" is only actionable if the human knows *which* conversation to open.

    A knowledge base has many threads and this approval is now invisible from the phone. Without the
    thread id the message describes a problem the human cannot act on, which is the same outcome as
    not sending it.
    """
    bot = adapter(service, store, api)

    await bot._post_approval(CHAT, approval(thread_id=THREAD, actions=(action(allowed=()),)))

    assert THREAD in api.transcript


def test_callback_data_carries_a_handle_and_fits_the_budget_tg57() -> None:
    """64 **bytes**, and nothing meaningful fits in them.

    Measured against the real seam: a derived thread id is 60 characters on its own, so
    ``verb|thread|interrupt|index`` is 97 bytes — over the limit, for the fan-out case that is the
    common one. Neither Telegram client library checks at construction; it 400s at the server, which
    is to say at the moment a human is waiting for an approval.
    """
    for index in range(100):
        data = callback_data("7f3a2b1c", index, "a")
        assert len(data.encode()) <= CALLBACK_DATA_LIMIT
    assert len(callback_data("7f3a2b1c", 0, "a").encode()) == 15


@pytest.mark.asyncio
async def test_no_emitted_button_carries_a_thread_or_interrupt_id_tg57(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The button is a key into durable state, not a wire protocol carrying it.

    The moment a thread id rides in ``callback_data`` the fan-out case exceeds 64 bytes and the
    keyboard is refused; and rendering would have become the place state lives, which is exactly
    what makes a press unresolvable after a restart.
    """
    child_id = expert_thread_id(THREAD, COOKING)
    request = approval(thread_id=child_id, interrupt_id="i-child-0123456789abcdef")
    bot = adapter(service, store, api)

    await bot._post_approval(CHAT, request)

    for entry in api.with_keyboard:
        for row in entry["kb"]:
            for button in row:
                data = str(button["callback_data"])
                assert len(data.encode()) <= CALLBACK_DATA_LIMIT
                assert child_id not in data
                assert request.interrupt_id not in data
                assert str(CHAT) not in data


@pytest.mark.asyncio
async def test_a_destructive_reason_takes_a_second_tap_tg64(
    service: ScriptedService,
    store: SqliteTelegramStore,
    api: FakeBotApi,
    connection: aiosqlite.Connection,
) -> None:
    """Delete, topic creation and conflict resolution get a confirm step and the no-undo line.

    These are the three reasons where a mis-tap cannot be walked back at any layer — nothing moves
    or deletes human content anywhere else in this system without a gate. One extra tap is a small
    price against a thumb that landed on the wrong row of a moving screen.
    """
    request = approval(actions=(action(tool="delete_file", reason="delete"),))
    await bind(service, store)
    service.pending = request
    bot = adapter(service, store, api)
    await bot._post_approval(CHAT, request)
    handle = (await handles(connection))[0]

    await press(bot, handle, 0, "a")
    assert service.resumed == []
    assert "no undo" in api.transcript.lower()

    await press(bot, handle, 0, "ca")
    assert [decision.type for _, decisions, _ in service.resumed for decision in decisions] == [
        "approve"
    ]


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


@pytest.mark.xfail(
    reason="TG-44/TG-45: split_message spends the whole 4096-unit budget, then _send prepends a "
    "'(1/2)\\n' counter — 4095 + 6 = 4101 units. Telegram 400s it and _pump_outbox suppresses "
    "TelegramError, so the human silently receives only the second half of their reply",
    strict=True,
)
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
    assert [name for name, _ in service.calls].count("create_thread") == 1
    assert await store.bound_thread(CHAT) == started[0]


@pytest.mark.asyncio
async def test_only_new_rotates_the_thread_tg27(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi, journal: Journal
) -> None:
    """Every automatic rotation is invisible, and invisible is the property TG-1 was ruled to kill.

    A timer or a message count would silently split a conversation, so the human's follow-up lands
    in a thread with none of the context they think it has — and the note is filed against the wrong
    discussion, with no undo and no signal that anything moved.
    """
    bot = adapter(service, store, api)

    await deliver(bot, message_update(update_id=1, text="first"))
    await deliver(bot, message_update(update_id=2, text="/new"))
    await deliver(bot, message_update(update_id=3, text="second"))

    started = [entry["thread_id"] for kind, entry in journal if kind == "start_run"]
    assert len(set(started)) == 2
    assert [name for name, _ in service.calls].count("create_thread") == 2


# --------------------------------------------------------------------------------------
# § the command surface (TG-39, TG-40, TG-35)
# --------------------------------------------------------------------------------------


def test_the_command_surface_is_exactly_five_and_has_no_connect_tg39() -> None:
    """``/connect`` is gone, and with it "which expert am I talking to?".

    A chat is bound to its agent by deployment configuration. A runtime ``/connect`` is a binding
    the human can change from the phone and then forget, which is precisely how a mis-sent note
    lands in the wrong topic — a write with no undo, filed under the wrong subject.
    """
    assert COMMANDS == ("/new", "/threads", "/agents", "/pending", "/cancel")
    assert "/connect" not in COMMANDS


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
    assert service.calls == []
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

    first = await store.bound_thread(CHAT)
    second = await store.bound_thread(OTHER_CHAT)
    assert first is not None
    assert second is not None
    assert first != second


# --------------------------------------------------------------------------------------
# § the truncation marker the adapter supplies (decision U)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_preview_never_tells_the_human_to_open_the_tui_for_text_above_it_tg56(
    service: ScriptedService, store: SqliteTelegramStore, api: FakeBotApi
) -> None:
    """The shared marker says "open the TUI for the whole diff" — here the whole diff is one message up.

    Under TG-56 the complete description is already in the chat, so the shared wording would send the
    human to another machine for something they can scroll to. The preview is a preview; the
    deciding surface arrived first.
    """
    description = "\n".join(f"- bullet {n}: something the human wrote" for n in range(200))
    bot = adapter(service, store, api)

    await bot._post_approval(CHAT, approval(actions=(action(description=description),)))

    button_message = api.with_keyboard[0]["text"]
    assert TRUNCATION_MARKER.strip() not in button_message
    assert "full text above" in button_message
