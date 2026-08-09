"""An adversarial pass over the deleted-topic hazard: the cases the first suite could not reach.

Everything here was constructed by asking *what input makes this fail*, not by reading the code for
intent. Four properties are asserted that ``test_telegram_topic_hazards.py`` cannot state, because
each needs either two sends in flight at once, a channel the bot has already given up on, or a
process that has restarted since the repair:

* **The disarm is per message; only the repair is per channel** (TG-81 over TG-84). ``_channel_died``
  returns early when the channel is already known dead — which is right for the *correction* and
  wrong for the *keyboard*. Two sends into one channel overlap on the ordinary path: the outbox pump
  is its own task (TG-49) and a run emits its reply and its approval from another, so the pump can be
  inside a send when ``_consume`` reaches the ``InterruptEvent``.
* **Every part of an approval obeys TG-82 and TG-85, including the confirm step.** The second tap
  (TG-64) is the message that carries *"Yes, do it"* over an irreversible write, and it was the one
  part sent with no agent id at all — invisible to the retirement re-addressing and to the
  attribution.
* **A restarted run is still somebody's.** ``ThreadDetail`` carries the ``Thread``, not an
  ``agent_id``, so a ``getattr(detail, "agent_id", …)`` answers ``None`` for every thread that has
  ever existed.
* **A repair survives the process that made it, but only for a caller that knows the agent.**
  ``_moved`` is process memory and a prompt row keeps naming the topic that was deleted, so the
  re-addressing that TG-84 promises has to come back out of the directory — which it can only do
  when the send names its expert.

The fake is deliberately its own rather than imported: the file it would have imported from is being
written by another session in this worktree, and a regression test that stops compiling when someone
else renames a fixture is not a regression test.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio

from pkb.contracts import ActionView, ApprovalRequest, MessageView, RunEnd
from pkb.server.telegram import Channel, TelegramAdapter, TelegramConfig, _agent_of
from pkb.server.telegram_api import GENERAL, POLL_TIMEOUT
from pkb.service import Thread, ThreadDetail
from pkb.service.telegram import PROMPTS_TABLE, SqliteTelegramStore
from tests.server.stub import COOKING, LIBRARIAN, StubService

pytestmark = pytest.mark.asyncio

CHAT = 770009
OWNER = 987654321
"""Fictional. The repo is public and no real id ever reaches a file."""

COOK_TOPIC = 71
LIVE_TOPIC = 88
"""The topic a repair moved Cooking to before the daemon last bounced."""

DIFF = "--- a/topics/Cooking/notes/steak.md\n+++ b/topics/Cooking/notes/steak.md\n-5\n+8\n"


@dataclass
class Delivered:
    chat_id: int
    topic_id: int
    text: str
    message_id: int
    armed: bool


@dataclass
class AuditBotApi:
    """Telegram's silent branch, plus a gate that lets two sends be in flight at once.

    ``hold_armed`` is what makes the race deterministic without ``sleep``: a send carrying a keyboard
    parks until the test releases it, which is the shape of the real thing — the keyboard's HTTP call
    is in flight while the pump's next frame discovers the same topic is gone.
    """

    journal: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    topics: dict[int, set[int]] = field(default_factory=dict)
    delivered: list[Delivered] = field(default_factory=list)
    next_message_id: int = 6000
    next_topic_id: int = 200
    topics_enabled: bool = True

    hold_armed: bool = False
    gate: asyncio.Event = field(default_factory=asyncio.Event)
    armed_seen: asyncio.Event = field(default_factory=asyncio.Event)

    async def get_me(self) -> Mapping[str, Any]:
        return {"id": 1, "username": "pkb_test_bot", "has_topics_enabled": self.topics_enabled}

    async def get_updates(
        self, offset: int | None, *, timeout: int = POLL_TIMEOUT
    ) -> Sequence[Mapping[str, Any]]:  # pragma: no cover - no poll loop in this file
        await asyncio.sleep(0)
        return []

    async def create_forum_topic(self, chat_id: int, name: str) -> Mapping[str, Any]:
        self.journal.append(("create_forum_topic", {"chat_id": chat_id, "name": name}))
        self.next_topic_id += 1
        self.topics.setdefault(chat_id, set()).add(self.next_topic_id)
        return {"message_thread_id": self.next_topic_id, "name": name}

    async def edit_forum_topic(self, chat_id: int, topic_id: int, name: str) -> None:
        """TG-105. Present because every fake implements every method, and never reached here."""
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
        if keyboard is not None and self.hold_armed and not self.gate.is_set():
            self.armed_seen.set()
            await self.gate.wait()
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
    ) -> Mapping[str, Any]:  # pragma: no cover - not on this file's paths
        self.journal.append(("send_document", {"filename": filename, "topic": topic_id}))
        return self._deliver(chat_id, f"[document {filename}]", topic_id, armed=False)

    async def answer_callback(
        self, callback_id: str, text: str = "", *, show_alert: bool = False
    ) -> None:
        self.journal.append(("answer_callback", {"id": callback_id, "text": text}))

    async def edit_message(
        self, chat_id: int, message_id: int, text: str
    ) -> None:  # pragma: no cover - TG-63 uses clear_keyboard
        self.journal.append(("edit_message", {"message_id": message_id}))

    async def clear_keyboard(self, chat_id: int, message_id: int) -> None:
        self.journal.append(("clear_keyboard", {"chat_id": chat_id, "message_id": message_id}))
        for message in self.delivered:
            if message.chat_id == chat_id and message.message_id == message_id:
                message.armed = False

    def _deliver(self, chat_id: int, text: str, topic_id: int, *, armed: bool) -> Mapping[str, Any]:
        if topic_id != GENERAL and topic_id not in self.topics.get(chat_id, set()):
            topic_id = GENERAL  # F-2: accepted, relocated, and the response is the only witness
        self.next_message_id += 1
        self.delivered.append(Delivered(chat_id, topic_id, text, self.next_message_id, armed))
        message: dict[str, Any] = {"message_id": self.next_message_id, "chat": {"id": chat_id}}
        if topic_id != GENERAL:
            message["message_thread_id"] = topic_id
        return message

    # -- what the tests read -----------------------------------------------------------

    def of(self, name: str) -> list[dict[str, Any]]:
        return [entry for kind, entry in self.journal if kind == name]

    def landed_in(self, topic_id: int) -> list[Delivered]:
        return [message for message in self.delivered if message.topic_id == topic_id]

    def texts_in(self, topic_id: int) -> list[str]:
        return [message.text for message in self.landed_in(topic_id)]


@pytest_asyncio.fixture
async def connection(tmp_path: Path) -> Any:
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
def api() -> AuditBotApi:
    return AuditBotApi(topics={CHAT: {COOK_TOPIC}})


@pytest.fixture
def service() -> StubService:
    return StubService(events=[RunEnd(run_id="run-1", final_text="done")])


async def make_bot(
    service: StubService, store: SqliteTelegramStore, api: AuditBotApi
) -> TelegramAdapter:
    """``run()``'s two startup awaits and nothing else — the same calls, without a poll loop."""
    bot = TelegramAdapter(
        service=service,
        store=store,
        api=api,
        config=TelegramConfig(
            token="123456789:AA-fake",
            chats={CHAT: LIBRARIAN},
            owner_user_ids=frozenset({OWNER}),
        ),
    )
    await bot._probe_topics()
    await bot._load_directory()
    return bot


def action(*, reason: str = "breadth-approval") -> ActionView:
    return ActionView(
        tool="write_file",
        args={"file_path": "topics/Cooking/notes/steak.md"},
        description=DIFF,
        allowed_decisions=("approve", "reject"),
        reason=reason,
    )


def approval(
    *, thread_id: str = "t-cooking-1", reason: str = "breadth-approval"
) -> ApprovalRequest:
    return ApprovalRequest(
        interrupt_id="i-1",
        agent_id=COOKING,
        thread_id=thread_id,
        actions=(action(reason=reason),),
    )


def thread_row(thread_id: str, agent_id: str = COOKING) -> Thread:
    now = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)
    return Thread(
        thread_id=thread_id,
        agent_id=agent_id,
        created_at=now,
        updated_at=now,
        origin_channel="telegram",
    )


async def handle_of(connection: aiosqlite.Connection) -> str:
    cursor = await connection.execute(f"SELECT handle FROM {PROMPTS_TABLE}")
    row = await cursor.fetchone()
    assert row is not None
    return str(row[0])


def press(data: str) -> dict[str, Any]:
    return {
        "id": "cb-1",
        "from": {"id": OWNER},
        "data": data,
        "message": {"message_id": 1, "chat": {"id": CHAT, "type": "private"}},
    }


def button_data(api: AuditBotApi, verb: str) -> str:
    """The ``callback_data`` the adapter actually drew — never one the test made up (TG-57)."""
    for entry in reversed(api.of("send_message")):
        for row in entry["kb"] or ():
            for button in row:
                if str(button["callback_data"]).endswith(f"|{verb}"):
                    return str(button["callback_data"])
    raise AssertionError(f"no {verb!r} button was ever drawn")


# --------------------------------------------------------------------------------------
# TG-81 — the disarm is per message, and two sends overlap on the ordinary path
# --------------------------------------------------------------------------------------


async def test_a_stray_keyboard_is_disarmed_even_when_the_death_is_already_known_tg81(
    service: StubService, store: SqliteTelegramStore, api: AuditBotApi
) -> None:
    """The failure this whole section is arranged around, reached through the *dedup* branch.

    ``_channel_died`` skips its work when the channel is already in ``_moved``, which is correct for
    the correction — TG-84's "one correction, not eight" — and catastrophic for the keyboard. The
    interleaving is not exotic: the outbox pump is a separate task (TG-49), so while an approval's
    keyboard is in flight the pump can send the run's reply into the same topic, discover the
    deletion first and re-address the channel. The keyboard's own response then comes back stray with
    the death already recorded, and an early return leaves an **Approve button for an irreversible
    write live in General under Cooking's name** — indistinguishable, in scrollback, from the
    Librarian's own work, on a system with no undo (D6).
    """
    bot = await make_bot(service, store, api)
    await store.open_channel(CHAT, COOK_TOPIC, COOKING)
    cooking = Channel(CHAT, COOK_TOPIC)
    api.hold_armed = True

    posting = asyncio.create_task(bot._post_approval(cooking, approval()))
    await asyncio.wait_for(api.armed_seen.wait(), 2)  # the keyboard's send is in flight
    api.topics[CHAT] = set()  # the human long-presses the topic and taps Delete
    await bot._say(cooking, "Filed under Cooking.", agent_id=COOKING)  # the pump's next frame
    api.gate.set()
    await asyncio.wait_for(posting, 2)

    assert [m.text for m in api.landed_in(GENERAL) if m.armed] == []


async def test_the_second_stray_is_explained_and_the_repair_is_still_not_repeated_tg84(
    service: StubService, store: SqliteTelegramStore, api: AuditBotApi
) -> None:
    """Both halves of the same branch, so a fix for one cannot quietly undo the other.

    A message whose buttons were just killed, sitting in General with nothing under it saying why,
    is a human pressing a dead Approve and learning nothing — so an armed stray always gets TG-81's
    correction. The *repair* stays once per channel: TG-84 exists because a fan-out with eight queued
    frames must not produce eight ``createForumTopic`` calls and eight notifications at exactly the
    moment something needs approving.
    """
    bot = await make_bot(service, store, api)
    await store.open_channel(CHAT, COOK_TOPIC, COOKING)
    cooking = Channel(CHAT, COOK_TOPIC)
    api.hold_armed = True

    posting = asyncio.create_task(bot._post_approval(cooking, approval()))
    await asyncio.wait_for(api.armed_seen.wait(), 2)
    api.topics[CHAT] = set()
    await bot._say(cooking, "Filed under Cooking.", agent_id=COOKING)
    api.gate.set()
    await asyncio.wait_for(posting, 2)

    corrections = [t for t in api.texts_in(GENERAL) if "has been deleted" in t]
    assert len(corrections) == 2, corrections
    assert sum("buttons no longer work" in t for t in corrections) == 1
    assert len(api.of("create_forum_topic")) == 1, "the channel was repaired more than once"


# --------------------------------------------------------------------------------------
# TG-64/TG-85 — the confirm step is part of the approval
# --------------------------------------------------------------------------------------


async def test_the_confirm_step_is_addressed_and_named_like_the_approval_it_belongs_to_tg85(
    service: StubService,
    store: SqliteTelegramStore,
    api: AuditBotApi,
    connection: aiosqlite.Connection,
) -> None:
    """*"Yes, do it"* is the last thing a human taps before an irreversible write. It has an owner.

    Sent with no ``agent_id``, this message was invisible to both mechanisms §9 built for a channel
    that is no longer its agent's: ``_route_out``'s retirement check (TG-82) and ``_prefixed``'s
    exposure line (TG-85(b)). On a retired channel the human therefore received a bare *"There is no
    undo for this. Confirm?"* with a live **Yes, do it** button in General, naming no expert and no
    write — beside the Librarian's messages, for a delete in Cooking's tree.
    """
    await store.open_channel(CHAT, COOK_TOPIC, COOKING)
    await store.retire_channel(CHAT, COOKING)  # TG-82: given up on before this daemon started
    bot = await make_bot(service, store, api)
    request = approval(reason="delete")
    service.pending = request
    service.rows[request.thread_id] = thread_row(request.thread_id)

    await bot._post_approval(Channel(CHAT, COOK_TOPIC), request)
    await bot._on_callback(press(button_data(api, "a")))

    confirm = next(m for m in api.delivered if m.text.endswith("Confirm?"))
    assert confirm.topic_id == GENERAL, "the retired channel's traffic did not fall back to General"
    assert confirm.text.startswith(f"{COOKING}\n"), confirm.text
    assert await handle_of(connection)  # the prompt row is still open; nothing was decided


# --------------------------------------------------------------------------------------
# TG-82/TG-84 — a repair outlives the process that made it
# --------------------------------------------------------------------------------------


async def test_an_approvals_outcome_follows_its_agent_to_the_repaired_topic_tg84(
    service: StubService,
    store: SqliteTelegramStore,
    api: AuditBotApi,
    connection: aiosqlite.Connection,
) -> None:
    """The one line that records an irreversible act, sent from a row that names a dead topic.

    ``_moved`` is process memory and a prompt row keeps naming the topic it was posted in (TG-57), so
    after a bounce the outcome of a press is addressed to a topic that was repaired before the
    restart. TG-84's amended re-addressing — *"a row naming a different topic means the channel
    already moved, so re-address and create nothing"* — can only fire for a send that names its
    expert; unattributed, this line was pinned to General for the life of the daemon and every later
    message on that prompt's channel with it.
    """
    await store.open_channel(CHAT, COOK_TOPIC, COOKING)
    await store.rebind_channel(CHAT, COOKING, LIVE_TOPIC)  # the repair the last process performed
    api.topics[CHAT] = {LIVE_TOPIC}
    bot = await make_bot(service, store, api)  # the restart: `_moved` is empty

    request = approval()
    service.pending = request
    service.rows[request.thread_id] = thread_row(request.thread_id)
    handle = "deadbeef"
    await store.open_prompt(handle, CHAT, COOK_TOPIC, request.thread_id, request.interrupt_id, 1)

    await bot._on_callback(press(f"v1|{handle}|0|a"))

    assert api.of("create_forum_topic") == [], "a second topic was created for a live channel"
    assert any(text.startswith("Answered:") for text in api.texts_in(LIVE_TOPIC)), api.delivered
    # The first attempt still strays once — nothing announces a deletion, so the send *is* the probe
    # (TG-80) — and that copy stands with a correction under it (TG-81, decision AD). What must not
    # happen is the line stopping there, which is where it stopped when the send named no expert.
    assert [t for t in api.texts_in(GENERAL) if "has been deleted" in t]


async def test_a_restarted_runs_frames_still_name_their_agent_tg85(
    service: StubService, store: SqliteTelegramStore, api: AuditBotApi
) -> None:
    """``ThreadDetail`` has no ``agent_id``, so the re-sync path attributed nothing, ever.

    A ``getattr(detail, "agent_id", "")`` is not a fallback here — it is a constant ``None``, because
    the field does not exist on the class and never has. Every frame TG-31 re-synced after a restart
    was therefore unattributed: not routed by TG-82's retirement, not prefixed by TG-85(b) on arrival
    in General, and unable to repair the channel it died in (TG-84) — on the one code path whose
    whole premise is that the topic may have been deleted while the daemon was down.
    """
    assert not hasattr(ThreadDetail, "agent_id"), "the getattr this replaced would now be live"
    detail = ThreadDetail(
        thread=thread_row("t-cooking-1"),
        messages=(MessageView(role="assistant", text="Filed under Cooking.", created_at=None),),
    )
    assert _agent_of(detail) == COOKING

    await store.open_channel(CHAT, COOK_TOPIC, COOKING)
    await store.retire_channel(CHAT, COOKING)
    bot = await make_bot(service, store, api)

    await bot._post_late_reply(Channel(CHAT, COOK_TOPIC), detail)
    await drain(bot)

    late = next(m for m in api.landed_in(GENERAL) if "Filed under Cooking." in m.text)
    assert late.text.startswith(f"{COOKING}\n"), late.text


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


# --------------------------------------------------------------------------------------
# The properties the audit checked and found sound, pinned so a change has to argue with them
# --------------------------------------------------------------------------------------


async def test_only_the_send_family_ever_carries_a_topic_out_of_this_module_tg90(
    service: StubService, store: SqliteTelegramStore, api: AuditBotApi
) -> None:
    """Stated over the whole outbound surface rather than over one call.

    A grep is the right shape for this one: ``message_thread_id`` is a wire word, and the module has
    exactly one place it may leave the process (``_api_send``) plus ``send_document``'s keyword. If a
    third appears, TG-80's comparison has to appear beside it or the new call is a silent relocation
    nobody checks.
    """
    source = Path("src/pkb/server/telegram.py").read_text(encoding="utf-8")
    call_sites = [
        line.strip() for line in source.splitlines() if "topic_id=target.topic_id" in line
    ]
    assert len(call_sites) == 2, call_sites  # send_message in `_api_send`, send_document

    await store.open_channel(CHAT, COOK_TOPIC, COOKING)
    bot = await make_bot(service, store, api)
    request = approval()
    service.pending = request
    service.rows[request.thread_id] = thread_row(request.thread_id)
    await bot._post_approval(Channel(CHAT, COOK_TOPIC), request)
    await bot._on_callback(press(button_data(api, "r")))

    assert api.of("clear_keyboard"), "the disarm never ran, so its shape was not exercised"
    assert all("topic" not in entry for entry in api.of("clear_keyboard"))


async def test_a_press_from_a_relocated_message_still_resolves_the_rows_thread_tg57(
    service: StubService,
    store: SqliteTelegramStore,
    api: AuditBotApi,
    connection: aiosqlite.Connection,
) -> None:
    """A stray message's press carries General, and the thread must come from the row regardless.

    ``callback_data`` holds 64 bytes and an opaque handle (TG-57), so the channel a press is answered
    in comes from the durable row and the thread from the row's ``thread_id`` — never from where the
    human happened to be standing. A stray relocated into General is exactly the case where those two
    disagree, and resolving by the query's location would answer the wrong interrupt.
    """
    await store.open_channel(CHAT, COOK_TOPIC, COOKING)
    api.topics[CHAT] = set()  # deleted before the approval is posted
    bot = await make_bot(service, store, api)
    request = approval()
    service.pending = request
    service.rows[request.thread_id] = thread_row(request.thread_id)

    await bot._post_approval(Channel(CHAT, COOK_TOPIC), request)
    await bot._on_callback(press(button_data(api, "a")))

    assert ("resume", (request.thread_id, request.interrupt_id)) in service.calls
    cursor = await connection.execute(f"SELECT answers_json FROM {PROMPTS_TABLE}")
    row = await cursor.fetchone()
    assert row is not None and json.loads(str(row[0])) == {"0": "a"}
