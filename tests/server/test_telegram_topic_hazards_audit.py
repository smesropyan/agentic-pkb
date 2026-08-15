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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio

from pkb.contracts import ActionView, ApprovalRequest, RunEnd
from pkb.server.telegram import TelegramAdapter, TelegramConfig
from pkb.server.telegram_api import GENERAL, POLL_TIMEOUT
from pkb.service import Thread
from pkb.service.telegram import SqliteTelegramStore
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


# `PROMPTS_TABLE`'s old name (Task 6, DESIGN.md §2.10): the constant is deleted with the
# approval-prompt surface; these helpers are read only by tests marked `@pytest.mark.superseded`.
_PROMPTS_TABLE = "pkb_telegram_prompts"


async def handle_of(connection: aiosqlite.Connection) -> str:
    cursor = await connection.execute(f"SELECT handle FROM {_PROMPTS_TABLE}")
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


# --------------------------------------------------------------------------------------
# TG-64/TG-85 — the confirm step is part of the approval
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# TG-82/TG-84 — a repair outlives the process that made it
# --------------------------------------------------------------------------------------


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
