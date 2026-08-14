"""The Telegram adapter — a channel per expert, inside the daemon (TG-1 … TG-95).

D9: a supervised background task calling :class:`~pkb.service.PkbService` **directly**. No HTTP
round trip to our own daemon, no second process, no auth boundary between them. Layer 3 built the
slot; this is what goes in it.

**No HTTP import lives here.** The Bot API is reached through the :class:`~pkb.server.telegram_api.
BotApi` Protocol, whose only implementation is the sibling module ``telegram_api.py``. That split
is what lets every rule below be driven against a fake with no token and no socket — and a *sibling*
rather than a subpackage because five built seam scans glob non-recursively, so code inside a
``telegram/`` package would be invisible to them.

Three properties shape the whole module. A fourth used to — "an approval is decided against the
whole thing," the description reaching the chat before the buttons did — and is gone along with the
buttons themselves: no tool call can raise an :class:`~pkb.contracts.InterruptEvent` any more
(DESIGN.md §2.10 — the operator's instruction is the approval), so there is nothing left to decide
against and nothing left to post a keyboard for.

* **Structured concurrency, because the supervisor has no handle on what a task spawns.** Measured
  against the real ``_supervise``: a task that detaches a child and raises leaves it running and
  gets a second on restart — three restarts gave three live pollers, which Telegram answers with
  ``409 Conflict`` because it permits one ``getUpdates`` per token. Everything here runs in one
  :class:`asyncio.TaskGroup`, so a crash takes its children with it.
* **Nothing is remembered in the process.** The chat's current thread and the update ledger live in
  SQLite. A message that arrives while the daemon was down is redelivered up to 24 hours later into
  an adapter with no memory of having seen it, and the durable path is the *only* path — so the
  restart case is exercised by every test rather than by an incident.
* **The pump never blocks on a Bot API call** (TG-49). Frames drain into a bounded outbox that a
  separate task sends from. ``RunHub`` drops a subscriber whose queue exceeds 256 and the drop
  closes the stream *without a terminal frame*, so one ``429`` with ``retry_after: 30`` inside the
  pump would lose the reply the human is waiting for and make it look like an unknown outcome.

**The addressing unit is the channel, not the chat** (§9, TG-72). A channel is ``(chat_id,
topic_id)``, with ``topic_id == 0`` for the General area of a private chat. Bot API 9.3 put topics
in private chats and 9.4 let a bot create them, which is what lifted TG-1's one-agent-per-human
ceiling: a *topic* maps to exactly one agent, so TG-1's guarantee survives word for word while a
human reaches every expert from one phone.

One Bot API fact shapes everything below, and it is not an error path. **A send into a deleted
private-chat topic returns ``ok: true``** — the ``message_thread_id`` is ignored and the message
lands in General (tdlib/telegram-bot-api#854). No update announces a deleted topic either, so the
*response* is the only evidence that exists: every send carrying a topic compares the
``message_thread_id`` on the returned ``Message`` against the one it asked for (TG-80). Without that
comparison the human deletes the Cooking topic, and Cooking's approval keyboards keep arriving in
General indistinguishable from the Librarian's — an approve button for an irreversible write under
the wrong expert's name, with no undo (D6). When it fires, the stray message is **disarmed first**
(TG-81): a message is dangerous only while its buttons are live, and the very response that revealed
the problem carries the ``message_id`` needed to kill them.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from pkb.contracts import (
    ApprovalPendingError,
    MessageComplete,
    RunEnd,
    RunError,
    SubagentEnd,
    SubagentStart,
    ThreadBusyError,
    terminal_status,
)
from pkb.server.telegram_api import (
    CALLBACK_DATA_LIMIT,
    GENERAL,
    MAX_RECREATIONS,
    MESSAGE_LIMIT,
    BotApi,
    TelegramError,
    landed_topic_id,
    with_retry,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pkb.service import PkbService
    from pkb.service.telegram import TelegramStore

__all__ = [
    "COMMANDS",
    "PICKER_BUDGET",
    "PICKER_DIGEST_SIZE",
    "PICKER_LABEL_UNITS",
    "PICKER_MESSAGES",
    "PICKER_PREFIX",
    "PICKER_ROWS",
    "Channel",
    "TelegramAdapter",
    "TelegramConfig",
    "counter",
    "parse_picker",
    "picker_callback",
    "resolve_picker",
    "split_message",
    "utf16_len",
]

_log = logging.getLogger(__name__)

COMMANDS: Final = ("/new", "/threads", "/agents", "/cancel", "/channels")
"""The whole command surface, five of them, **each acting on the channel it was typed in** (TG-86).

**No ``/connect`` and no ``/talk``** (decision AF). A channel is bound to its agent by the topic the
human is typing in — visible above the keyboard at the moment they hit send — and the ambiguity
``/connect`` created is what made a mis-sent note land in the wrong topic. An in-band agent selector
would restore that hidden mode under a new name; a topic title cannot be invisible.

**No ``/pending`` any more.** It listed every thread with a parked approval and re-posted its
keyboard — both gone along with the gates themselves (DESIGN.md §2.10): no thread can hold a pending
decision, so the command had nothing left to list.
"""

_OUTBOX_WARN: Final = 64
"""How deep the outbox gets before it says so (TG-48, TG-49).

Not a cap: the queue is unbounded, because every frame that reaches it is one the rule forbids
dropping and blocking would push back-pressure onto ``RunHub``'s 256-slot subscriber queue, whose
overflow closes the stream without a terminal frame. This is the depth at which a stuck pump stops
being weather and starts being worth a log line.
"""
_CONFLICT: Final = 409
"""Telegram's answer to a second ``getUpdates`` on one token (TG-9). Never a transport blip."""

_WARNED_CAP: Final = 512
"""How many channels the TG-23 window remembers. A stranger picks their own chat id, so it is
bounded — and it is per channel (TG-23 amended), because an unbound topic and an unmapped chat are
different explanations and rate limiting them together silences the second one."""

_SERVICE_KEYS: Final = frozenset(
    {
        "forum_topic_created",
        "forum_topic_edited",
        "forum_topic_closed",
        "forum_topic_reopened",
    }
)
"""Topic lifecycle, which arrives as a service message inside an ordinary ``message`` (TG-92, F-5).

There is no update *kind* for any of this and none at all for a deletion, which is exactly why
TG-80 has to read the send response. What these updates need here is to run nothing and to be
answered with nothing — as built, a ``message`` with no ``text`` fell into TG-36 and was answered
*"I can only read text"*, so the human's own first act after enabling Threaded Mode — creating a
topic — would have been met with a refusal about a photo they did not send.
"""

_MEDIA_KEYS: Final = frozenset(
    {
        "animation",
        "audio",
        "contact",
        "dice",
        "document",
        "game",
        "location",
        "photo",
        "poll",
        "sticker",
        "story",
        "venue",
        "video",
        "video_note",
        "voice",
    }
)
"""What makes a text-less message a **human's** attachment rather than a service message (TG-92).

TG-36's refusal exists to tell a human that the file they just sent was not downloaded or stored.
A message carrying none of these and no text was not sent by a human at all, and answering it is
answering Telegram.
"""

_SEND_ATTEMPTS: Final = MAX_RECREATIONS + 2
"""How many times one message may be re-addressed before it is given up on (TG-80, TG-82).

Bounded by construction rather than by hope: each attempt either succeeds, or discovers a dead topic
and consumes one of :data:`~pkb.server.telegram_api.MAX_RECREATIONS`. Past the bound the channel is
retired to General, and a General send is never checked for a thread id — so the loop terminates on
the send after the last recreation.
"""


@dataclass(frozen=True, slots=True)
class Channel:
    """``(chat_id, topic_id)`` — the routing key, and never ``chat_id`` alone (TG-72, decision Y).

    ``topic_id == 0`` is General, the part of a private chat that carries no ``message_thread_id``.
    **Zero rather than ``None``**: this is a database key, a unique-index component and a dict key
    on three code paths, and SQLite treats NULLs as *distinct* in a unique index — a nullable topic
    column lets two General rows for one chat coexist, which is two conversations claiming to be the
    same one. Telegram mints topic ids from the message-id sequence, which starts at 1, so ``0`` is
    permanently free.

    Frozen because it is a dict key in the turn locks (TG-93), the unmapped-reply window (TG-23) and
    the re-addressing map (TG-84); one accidental mutation there is a lock that stops serializing.
    """

    chat_id: int
    topic_id: int = GENERAL

    @property
    def general(self) -> Channel:
        """This chat's General area — where a message goes when its topic is gone (TG-82, TG-84)."""
        return Channel(self.chat_id)

    @property
    def is_general(self) -> bool:
        return self.topic_id == GENERAL


def utf16_len(text: str) -> int:
    """Telegram's unit. Characters are **not** it (TG-44).

    Measured: 3,517 characters of emoji are 6,517 UTF-16 units, so a character-based budget passes a
    message Telegram then refuses with ``message is too long`` — and the human sees *nothing*, not
    the reply it cut short. The arithmetic that knows about Telegram lives here.
    """
    return len(text.encode("utf-16-le")) // 2


def counter(position: int, total: int) -> str:
    """``(2/4)`` and a newline — the **only** thing the adapter adds to a reply (TG-45).

    Empty for a single part, because an unsplit reply has nothing to number and the common case
    must reach the phone exactly as the agent wrote it.
    """
    return f"({position + 1}/{total})\n" if total > 1 else ""


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split on **length, never on meaning**, with room for the counter (TG-45, TG-44).

    Cuts fall on line boundaries, in order, with nothing summarized, reflowed, reordered or dropped —
    the parts reassemble byte-identically. LB-18 exists because a model composing a reply claimed an
    expert had checked the knowledge base when none ran; a transport that cut on meaning would be
    the same lie one layer down. A length cut can be wrong; it cannot be a lie.

    The counter's units are reserved **here**, before the cut, because :meth:`TelegramAdapter._send`
    prepends it to what this returns: a part filled flush to 4,096 units becomes 4,102 on the wire,
    Telegram answers ``400 message is too long``, and the outbox swallows the error — so the human
    receives a reply that starts at "(2/2)" with nothing saying the first half existed. The reserved
    width depends on the number of parts, which depends on the width, so the budget is re-measured
    until it stops moving; the loop is bounded because the width only grows at a power of ten.
    """
    if utf16_len(text) <= limit:
        return [text]  # one message carries no counter, so it gets the whole budget
    budget = limit
    parts = [text]
    for _ in range(8):
        parts = _split_at(text, budget)
        narrowed = limit - utf16_len(counter(len(parts) - 1, len(parts)))
        if narrowed >= budget:
            break
        budget = narrowed
    return parts


def _split_at(text: str, limit: int) -> list[str]:
    """The cut itself: line boundaries first, a hard cut only for a line that cannot fit."""
    parts: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if current and utf16_len(current + line) > limit:
            parts.append(current)
            current = ""
        while utf16_len(line) > limit:  # a single line longer than the limit still has to go
            head = line
            while utf16_len(head) > limit:
                head = head[: int(len(head) * 0.9)]
            parts.append(head)
            line = line[len(head) :]
        current += line
    if current:
        parts.append(current)
    return parts


# -- the channel picker (§10, TG-96 … TG-101) -----------------------------------------------

PICKER_PREFIX: Final = "c1"
"""The picker's own namespace inside ``callback_data`` (TG-97).

Disjoint from :func:`callback_data`'s ``v1|handle|index|verb`` on two counts, and the second one is
what makes the split safe. The prefixes differ, and the **field counts** differ: no agent id can
hold a ``|`` (``pkb.core.paths._SLUG_ALPHABET`` is ``[a-z0-9-]`` and ``/`` joins the segments), so a
picker payload has two fields where an approval payload has four. Each parser refuses the other's
grammar, so a press on a keyboard drawn months ago reaches the handler that drew it.
"""

_PICKER_DIGEST: Final = "#"
"""What marks a token as a digest of an agent id rather than the id itself (TG-97).

Illegal in an agent id, so an inline token and a digest token can never be read for each other.
"""

PICKER_DIGEST_SIZE: Final = 8
"""``blake2b`` bytes, giving 16 hex characters, so ``c1|#<digest>`` is 20 bytes flat (TG-97).

``hashlib`` rather than ``xxhash``: xxhash reaches this process through langgraph and is not a
declared dependency of this project, and decision R already forbids repeating that mistake.
"""

PICKER_BUDGET: Final = CALLBACK_DATA_LIMIT - len(f"{PICKER_PREFIX}|".encode())
"""How many bytes of the 64 are left for the agent id: **61** (TG-97).

Agent ids are ASCII by construction, so bytes equal characters and this arithmetic is exact.
``librarian`` spends 9 of the 61 and ``topic/cooking/grilling`` spends 22, so the ordinary id rides
inline. ``topic/`` plus one legal 80-character slug (``pkb.core.paths.MAX_SLUG_LENGTH``) is 86,
which is 25 over — a topic name a human may type, which is why the digest fallback exists
rather than a comment saying the case cannot happen. Neither PTB nor aiogram checks this at
construction; the real API answers ``BUTTON_DATA_INVALID`` at 65 bytes, at the moment a thumb lands.
"""

PICKER_ROWS: Final = 12
"""Rows per picker message — about one phone screen (TG-99, Q33)."""

PICKER_MESSAGES: Final = 3
"""Picker messages per ``/channels`` (TG-99, Q33).

The bound is the **send budget**, which Telegram meters per chat rather than per topic (F-7), so
every extra message here is a second an approval keyboard may be queued behind. Reasoned rather
than measured, exactly as ``MAX_RECREATIONS`` was: nobody has run this against a thirty-agent
catalog on a real phone.
"""

PICKER_LABEL_UNITS: Final = 48
"""UTF-16 units a button label may take, TG-44's arithmetic (TG-96)."""


def picker_callback(agent_id: str) -> str:
    """``c1|<agent-id>``, or ``c1|#<digest>`` when the id does not fit 64 bytes (TG-97).

    **No index, no position, no chat id and no topic id.** A positional encoding is the tempting one
    and it is the dangerous one: a Telegram message keeps its buttons live forever while the catalog
    moves under it, so row 7 of a month-old keyboard is a different expert, and the human gets a
    channel for one they did not choose. That is TG-1's mis-file arriving through the affordance
    built to prevent it.
    """
    token = agent_id
    if len(token.encode()) > PICKER_BUDGET:
        token = _PICKER_DIGEST + _picker_digest(agent_id)
    data = f"{PICKER_PREFIX}|{token}"
    if len(data.encode()) > CALLBACK_DATA_LIMIT:  # pragma: no cover - guarded by construction
        raise ValueError(f"callback_data is {len(data.encode())} bytes, over Telegram's 64")
    return data


def _picker_digest(agent_id: str) -> str:
    return hashlib.blake2b(agent_id.encode(), digest_size=PICKER_DIGEST_SIZE).hexdigest()


def parse_picker(data: str) -> str | None:
    """The token a press carries, and never the agent (TG-97, TG-98).

    Resolution is a separate step because it reads the **live** catalog. A parser that answered with
    an agent would be answering from the keyboard's drawing, which is a claim about a moment that
    has passed.
    """
    prefix, separator, token = data.partition("|")
    if not separator or prefix != PICKER_PREFIX or not token or "|" in token:
        return None
    return token


def resolve_picker(token: str, catalog: Collection[str]) -> str | None:
    """Which agent the token names, against the catalog as it stands now (TG-98).

    A digest matching two catalog ids resolves to ``None`` and the press is refused rather than
    picking one of them: the two answers are a channel for the expert the human tapped and a channel
    for a different expert, and nothing on the screen would say which one they got.

    An inline token comes back as it was written, catalog membership unchecked, because
    :meth:`TelegramAdapter._channels` is where an unknown agent is answered by name — one refusal,
    shared by the typed command and the press.
    """
    if not token.startswith(_PICKER_DIGEST):
        return token
    digest = token[len(_PICKER_DIGEST) :]
    matched = {agent for agent in catalog if _picker_digest(agent) == digest}
    return matched.pop() if len(matched) == 1 else None


def _picker_label(agent_id: str, *, bound: bool) -> str:
    """A marker and the agent **id**, cut to :data:`PICKER_LABEL_UNITS` keeping the tail (TG-96).

    The id rather than the catalog title, because ids are unique by construction (GE-25, RG-11) and
    titles are not: two sibling topics both titled *Cooking* would draw two identical rows over two
    different experts.

    The tail rather than the head, because ``topic/`` and the upper path repeat down the whole
    keyboard and the leaf is what tells one row from the next.
    """
    marker = "✓" if bound else "+"
    return f"{marker} {_tail(agent_id, PICKER_LABEL_UNITS - utf16_len(f'{marker} '))}"


def _tail(text: str, limit: int) -> str:
    """The last ``limit`` UTF-16 units, with the cut marked (TG-44)."""
    if utf16_len(text) <= limit:
        return text
    cut = text
    while cut and utf16_len(f"…{cut}") > limit:
        cut = cut[1:]
    return f"…{cut}"


@dataclass
class TelegramConfig:
    """Deployment configuration. **Never** read from the knowledge base (I3, TG-5)."""

    token: str = field(default="", repr=False)
    """The bot token, and **never** in a ``repr`` (TG-16, TG-24).

    ``repr=False`` plus the explicit :meth:`__repr__` below, for the same reason
    :class:`~pkb.server.telegram_api.HttpBotApi` carries both: this object is held by the adapter
    for the whole life of the supervised task, so it sits in a frame of every traceback the bot
    produces, and a ``repr`` is what an f-string, a ``%r`` log call and pytest's locals dump all
    print. Measured before this was fixed: a failing assertion printed the whole token into the
    test report. The masked form keeps the numeric bot id — which ``getMe`` publishes anyway — so
    an operator running two bots can still tell which one this is.
    """

    chats: Mapping[int, str] = field(default_factory=dict)
    """``chat_id`` → ``agent_id``. Human-configured; the bot never writes to it (TG-1, TG-3)."""

    owner_user_ids: frozenset[int] = frozenset()
    """Who may say yes. **The system's only authentication boundary** (decision X).

    D9 says the bot has "no auth boundary" and arch §10 defers multi-user because the daemon binds
    localhost — both stop being true the moment a bot token exists, because a bot's username is
    discoverable and the token is a public inbound path into a process that writes to a knowledge
    base with no undo. TG-1's mapping answers *which expert*, never *who may say yes*. Empty means
    the bot refuses everyone, which is the safe default for a misconfigured deployment.
    """

    def __repr__(self) -> str:
        """TG-16, TG-24: identifies the bot, never the secret."""
        bot_id, _, secret = self.token.partition(":")
        masked = f"{bot_id}:***" if secret else "***"
        return (
            f"TelegramConfig(token={masked}, chats={dict(self.chats)!r}, "
            f"owner_user_ids={set(self.owner_user_ids)!r})"
        )

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chats)


@dataclass
class TelegramAdapter:
    """One supervised task. Everything it needs to survive a restart is in the store."""

    service: PkbService
    store: TelegramStore
    api: BotApi
    config: TelegramConfig
    health: Any | None = None
    """The ``telegram`` block of ``/health``, when the daemon built one (TG-11, TG-12, TG-13).

    ``state == "running"`` says the *task* is alive — ``_supervise`` sets it before the first line
    of this class runs — so connectivity is reported separately: ``poll_ok()`` after every
    successful poll, ``send_failed()`` for an outbound failure. ``None`` means a test is driving the
    adapter directly and nothing else changes.
    """

    conflict_interval: float = 60.0
    """How long a 409 waits before re-probing (TG-9). Slow and fixed, never a backoff loop."""

    unmapped_window: float = 3600.0
    """One unmapped-channel reply per channel per window (TG-23, amended)."""

    _outbox: asyncio.Queue[tuple[Channel, str, str | None]] = field(default_factory=asyncio.Queue)
    _unroutable: frozenset[int] = frozenset()
    _warned_at: dict[Channel, float] = field(default_factory=dict)
    _conflicted: bool = False
    _group: Any | None = field(default=None, repr=False)
    _locks: dict[Channel, asyncio.Lock] = field(default_factory=dict, repr=False)
    _repairs: dict[Channel, asyncio.Lock] = field(default_factory=dict, repr=False)
    """One repair at a time per channel (TG-82, TG-84).

    Not an optimisation and not the turn lock: :attr:`_moved` is written after two awaits, so
    without this two frames that discovered the same deletion each issued a ``createForumTopic``
    and one deletion produced two topics — spending both of ``MAX_RECREATIONS`` at once and leaving
    one of them addressed by nothing. See :meth:`_channel_died`.
    """
    _creations: dict[int, asyncio.Lock] = field(default_factory=dict, repr=False)
    """One ``/channels`` at a time per **chat** (TG-77, TG-101).

    The same defect :attr:`_repairs` names, on the creation path, and measured the same way. TG-77's
    "creates nothing" is a check-then-act across three awaits: read the directory, read it again for
    this agent, call ``createForumTopic``. Two updates that arrive together each get their own child
    of the task group (:meth:`_poll`), so both read an empty directory and both create. Driven
    against the fake, one double tap on a picker row produced **two** ``createForumTopic`` calls and
    one directory row, because the row's primary key is ``(chat_id, agent_id)`` and the second write
    replaced the first: the human is left with two topics of the same name, one of them addressed by
    nothing, which the bot may never delete (TG-78) and no API can enumerate (F-5).

    Per chat rather than per agent, because ``/channels all`` decides over the whole directory.
    Nothing routes a turn under it, so the 284-second local-fallback argument that made
    :meth:`_channel_lock` per-channel does not reach here: what waits is a second ``/channels``, and
    what it waits for is one ``createForumTopic``.
    """
    _topics: bool = False
    """``getMe.has_topics_enabled``, probed once at startup (TG-75).

    ``False`` — the default, and what the real bot answers today — means this adapter behaves
    **exactly** as the pre-topics build: no ``message_thread_id`` on any send, no
    ``createForumTopic``, no directory writes. The BotFather *Threaded Mode* toggle is the human's
    to flip and they may never flip it; a deployment that upgrades and finds its bot broken by a
    feature it did not ask for is the worst possible outcome of an additive change.
    """

    _moved: dict[Channel, Channel] = field(default_factory=dict, repr=False)
    """Dead channel → where its traffic goes now (TG-84).

    A channel known to be dead is **never** sent to with its stale id again, and a queued outbox
    item addressed to one is re-addressed rather than dropped or sent blind. TG-80 detects one stray
    per send; without this map a fan-out with eight queued frames produces eight strays and eight
    corrections, at exactly the moment something needs approving.
    """

    _retired: dict[tuple[int, str], Channel] = field(default_factory=dict, repr=False)
    """``(chat_id, agent_id)`` past :data:`~pkb.server.telegram_api.MAX_RECREATIONS` (TG-82).

    Keyed on the agent rather than on the channel because retirement outlives the topic id that
    triggered it: the directory may already point at a topic created moments before the bound was
    crossed, and a retired agent must not be sent to at *any* topic id. **Chat-qualified**, because
    TG-25 permits one agent to hold a channel in two chats deliberately and every other key in this
    layer names the chat — an agent retired on the phone whose laptop channel went quiet is a live
    topic the human never touched, silently abandoned with no command that says why.

    The value is the last channel retirement condemned, and it is what :meth:`_revive` re-points:
    ``/channels <agent-id>`` is the way out TG-82's own notice names, and a way out that leaves a
    queued frame still chained to General is a fresh topic carrying the expert's name and receiving
    nothing.
    """

    async def run(self) -> None:
        """Poll, dispatch and send — all inside one task group (TG-7).

        The group is the point: ``_supervise`` awaits this coroutine and has no handle on anything
        it spawns, so a detached child would survive a crash and the restart would add another. With
        a group, a failure anywhere cancels the rest and the supervisor gets a clean slate.

        Startup — the orphan report and the TG-31 re-sync — runs **inside** the group rather than
        ahead of it, so a chat that cannot be written to fails the way every other send does
        instead of raising into ``_supervise`` before the group exists.

        The topic probe (TG-75) is awaited in the group's body, before any child exists: it is one
        fast call, it decides whether a single ``message_thread_id`` may leave this process, and
        running it concurrently with the poller would let the first inbound message be answered
        under an unknown answer.
        """
        await self.store.setup()
        self.check_mapping()
        async with asyncio.TaskGroup() as group:
            self._group = group
            await self._probe_topics()
            await self._load_directory()
            group.create_task(self._pump_outbox(), name="pkb-telegram-outbox")
            group.create_task(self._recover(), name="pkb-telegram-recover")
            group.create_task(self._poll(), name="pkb-telegram-poll")

    async def _spawn(self, coro: Any, *, name: str) -> None:
        """Start one child **in the group**, never with a bare ``asyncio.create_task`` (TG-7).

        ``_supervise`` restarts the callable and cancels nothing the previous invocation started —
        measured, a task that detached one child left three live pollers after three generations,
        which against the real API is three concurrent ``getUpdates`` on one token and a ``409``
        for two of them. Everything this adapter starts is therefore a child of the one group, so a
        crash anywhere takes the rest with it before the supervisor tries again.

        With no group — a test driving ``_poll`` directly rather than through :meth:`run` — the
        work is awaited inline instead. That is not a fallback for production: nothing outside
        :meth:`run` has a group, and awaiting in the caller leaks nothing.
        """
        group = self._group
        if group is None:
            await coro
            return
        group.create_task(coro, name=name)

    def check_mapping(self) -> None:
        """Validate the mapping against the live catalog — **report, never die** (TG-18).

        A topic can be renamed under a running config, so a mapping that names an agent
        ``list_agents()`` no longer has is an ordinary Tuesday. Exiting would take the daemon —
        every other chat, every parked approval and the TUI with it — down for one stale line of
        configuration; routing the chat anyway is the mis-file TG-1 was ruled to stop. So the entry
        is logged and the chat is answered exactly like an unmapped one, and the other chats keep
        working.

        "Reported" has to mean somewhere a human looks, so the entry also lands on ``/health`` as
        ``telegram.invalid_chats``. A startup ERROR line is the first thing to scroll away, and a
        deployment whose configuration names a renamed topic otherwise serves a telegram block
        indistinguishable from a correct one.
        """
        known = {descriptor.agent_id for descriptor in self.service.list_agents()}
        self._unroutable = frozenset(
            chat_id for chat_id, agent_id in self.config.chats.items() if agent_id not in known
        )
        if self.health is not None:
            with contextlib.suppress(AttributeError):
                self.health.invalid_chats = tuple(sorted(self._unroutable))
        for chat_id in sorted(self._unroutable):
            _log.error(
                "telegram: chat %s is mapped to agent %r, which does not exist; that chat will be "
                "answered as unmapped until the configuration names a real agent",
                chat_id,
                self.config.chats[chat_id],
            )
        # TG-73's "General is not the Librarian" warning is **not** here: it is configuration, so
        # `pkb.daemon._warn_general_not_librarian` says it once at wiring time, where it stays true
        # while this task is crash-looping. Saying it in both places would print it again on every
        # supervised restart, which is TG-13's exact lesson about notices a human learns to ignore.
        # What this adapter owes TG-73 is the *visible* half — that channel's `/agents` naming its
        # own agent, in `_agents_text`.

    async def _probe_topics(self) -> None:
        """Ask **once** whether this bot has topics, and say so at info (TG-75).

        ``has_topics_enabled`` is returned by ``getMe`` and by nothing else, and it reflects a
        per-bot BotFather toggle that is **off by default** — the deployment's own bot answers
        ``false`` today. So the honest startup posture is the pre-topics one, and the flag is what
        turns the rest of §9 on.

        A failed probe is a warning and a ``False``, never a raise: ``_supervise`` restarts this task
        on any exception, and restarting a bot because ``getMe`` timed out is the loop TG-13 forbids.
        ``False`` is also the safe direction to be wrong in — every send goes to General, which is
        visible and recoverable, where a wrong ``True`` would address topics that may not exist.
        Inbound routing deliberately does **not** consult this flag: an inbound ``message_thread_id``
        is evidence that a topic exists, and a mis-*route* is a write to the wrong expert's tree.
        """
        try:
            me = await with_retry(lambda: self.api.get_me())
        except Exception as exc:
            _log.warning(
                "telegram: getMe failed (%s), so topic mode is unknown; running without topics, "
                "which is exactly how this bot behaved before per-expert channels existed",
                exc,
            )
            me = {}
        self._topics = bool(me.get("has_topics_enabled"))
        if self.health is not None:
            with contextlib.suppress(AttributeError):
                self.health.topics_enabled = self._topics
        if self._topics:
            _log.info(
                "telegram: topic mode is on; each expert can have its own channel (/channels)"
            )
        else:
            _log.info(
                "telegram: topic mode is off, so this bot runs one chat to one agent exactly as "
                "before. Turn on Threaded Mode for this bot in BotFather to get a channel per expert"
            )

    async def _load_directory(self) -> None:
        """Publish how many channels exist, without creating any (TG-11, TG-76).

        Read-only on purpose. The daemon seeds ``health.agents`` from ``store.channel_agents()`` at
        composition time — before this task runs and while it may be crash-looping, which is when
        ``/health`` is read — and this only counts what is already there. **Booting creates
        nothing** (TG-76): eager creation buries the four channels a human uses under twenty-six
        they do not, and no API can enumerate a chat's topics to undo it.
        """
        retired = await self.store.retired_agents()
        for chat_id in self.config.chats:
            for agent_id in retired:
                # Seeded from the store, not remembered: retirement is durable precisely so a
                # restart does not hand a human who deleted a topic three times a fresh allowance
                # of two, and a bot that forgot would also re-announce the retirement on every
                # bounce (TG-82).
                #
                # Read back **per chat** (TG-25). `retired_agents()` answers with bare agent ids,
                # which is right for `/health` — TG-11 asks which experts are in that state, not
                # where — and wrong as a routing input: pairing every id in it with every mapped
                # chat retires an agent's live, untouched channel in the second chat, whose replies
                # then move to General with a prefix as though a topic nobody deleted had been.
                # `channel()` is the only reader that knows both halves of the key.
                row = await self.store.channel(chat_id, agent_id)
                if row is None or not row["retired"]:
                    continue
                self._retired[chat_id, agent_id] = Channel(chat_id, int(row["topic_id"]))
        if self.health is None:
            return
        total = 0
        for chat_id in self.config.chats:
            total += len(await self.store.channels(chat_id))
        with contextlib.suppress(AttributeError):
            self.health.channels = total
            self.health.retired_channels = tuple(sorted(retired))

    # -- inbound ----------------------------------------------------------------------

    async def _poll(self) -> None:
        """Receive, claim, hand off — and **keep polling** while a turn runs (TG-39, TG-6).

        The build awaited ``_dispatch`` inline, and ``_dispatch`` awaits the whole run: measured, a
        ``/cancel`` delivered in the same batch as the message it was meant to stop was not read
        until after the run had already finished, because no ``getUpdates`` was issued for the
        entire duration — 16 s on the cloud model, **284 s** on the local fallback. That window is
        precisely what ``/cancel`` exists to make interruptible, so the update goes to its own
        child of the group and the loop comes straight back to the socket.

        No reachable ``return`` and no ``break``: ``_supervise`` reports a task that returns
        cleanly as ``stopped``/``degraded`` with no error and nothing to revive it.
        """
        offset = await self._start_offset()
        while True:
            try:
                # Bound explicitly: `offset` is reassigned below, and a lambda closing over the
                # loop variable would poll from whatever it had become by the time a retry fired.
                updates = await with_retry(lambda at=offset: self.api.get_updates(at))
            except TelegramError as exc:
                if exc.code != _CONFLICT:
                    raise
                await self._await_sole_poller(exc)
                continue
            self._note_poll_ok()
            for update in updates:
                update_id = int(update["update_id"])
                offset = update_id + 1
                kind = next((k for k in update if k != "update_id"), "unknown")
                # Claimed BEFORE dispatch, so a redelivery cannot re-run a turn that already wrote.
                # The channel travels with the claim (TG-29 amended): the "I lost your message"
                # notice has to reach the channel that lost it, and a notice landing in General for
                # a message typed in Cooking is the same defect one level down from the one §8.3
                # already fixed in the chat dimension.
                where = _channel_of(update)
                if not await self.store.claim(
                    update_id,
                    None if where is None else where.chat_id,
                    GENERAL if where is None else where.topic_id,
                    kind,
                ):
                    continue
                await self._spawn(self._handle(update), name=f"pkb-telegram-update-{update_id}")

    async def _handle(self, update: Mapping[str, Any]) -> None:
        """One update, off the poll loop, serialized **per channel** (TG-39, TG-38, TG-93).

        The lock is what keeps the concurrency honest: Telegram delivers a chat's messages in
        order and three lines typed as three messages are the normal case on a phone, so running
        them at once would turn an ordinary conversation into a stream of ``ThreadBusyError``
        refusals. Commands and button presses take no lock — ``/cancel`` exists to reach a run
        that is holding one.

        Per **channel** rather than per chat (TG-93): nothing in that argument reaches across
        topics, and a lock held per chat means one 284-second local-fallback turn in Cooking makes
        every other expert on the phone unresponsive for five minutes with no visible cause.

        Exceptions are swallowed here rather than escaping into the group: one malformed update
        must not restart the bot, and everything that can be reported to the human already has
        been by the time it gets this far.
        """
        update_id = int(update["update_id"])
        try:
            await self._dispatch(update, update_id=update_id)
        except Exception:
            _log.warning("telegram: update %s could not be handled", update_id, exc_info=True)
        # Deliberately **not** in a `finally` (TG-29, TG-31). A cancellation here is the crash the
        # ledger's third state exists to describe, and marking it finished on the way out would
        # erase the only record that a run was admitted and its outcome never told to the chat.
        await self.store.dispatched(update_id)

    def _channel_lock(self, channel: Channel) -> asyncio.Lock:
        """One turn at a time per **channel** (TG-93, TG-39).

        Bounded by the directory: only a channel with an agent ever runs a turn, and a channel only
        gets an agent from a ``/channels`` command.
        """
        lock = self._locks.get(channel)
        if lock is None:
            lock = self._locks[channel] = asyncio.Lock()
        return lock

    async def _start_offset(self) -> int | None:
        """Where the first poll starts — and on a **cold** ledger that is *past* the backlog (TG-30).

        ``getUpdates(None)`` returns everything Telegram still holds, which is up to 24 hours of
        chat: a daemon that was down for a day would come back and run a day of messages as turns,
        into a tree with no undo. The first start after the token is configured, and any start where
        the SQLite file was moved or reset, is exactly that case — the human's model is "I turned it
        on", not "I have forty messages to file".

        So a cold ledger asks for the last update only (``offset=-1``, no long poll), records it as
        seen-and-handled, and starts after it. A **warm** ledger is untouched: it resumes at
        ``MAX(update_id) + 1``, which is the at-most-once guarantee TG-29 is built on, and skipping
        a backlog there would drop messages the bot really was going to run.
        """
        offset = await self.store.next_offset()
        if offset is not None:
            return offset
        updates = await with_retry(lambda: self.api.get_updates(-1, timeout=0))
        last = max((int(update["update_id"]) for update in updates), default=None)
        if last is None:
            return None
        for update in updates:
            if int(update["update_id"]) == last:
                kind = next((key for key in update if key != "update_id"), "unknown")
                where = _channel_of(update)
                await self.store.claim(
                    last,
                    None if where is None else where.chat_id,
                    GENERAL if where is None else where.topic_id,
                    kind,
                )
                await self.store.dispatched(last)
        _log.warning("telegram: cold start — discarding the backlog up to update %d", last)
        for chat_id in self.config.chats:
            # TG-13: a chat that blocked the bot is one recipient, not the subsystem being down.
            # General, not every channel: the notice is about the daemon, not about one expert, and
            # a copy in each of twelve topics is a notice a human learns to swipe away.
            await self._announce(Channel(chat_id), _COLD_START)
        return last + 1

    async def _await_sole_poller(self, exc: TelegramError) -> None:
        """A ``409`` means somebody else is holding the poll — stop, say so, re-probe slowly (TG-9).

        Telegram permits one ``getUpdates`` per token, so this is never a transport blip and never
        retryable: either a second daemon is running against the same token, or a poller from a
        previous generation of this task leaked and is still alive (the failure TG-7's task group
        exists to prevent). Raising would restart the task, and a restart against a leaked poller
        adds a *third* — the loop gets hotter the longer it runs, and `restarts` climbs for a
        condition no restart can fix.
        """
        if self.health is not None:
            with contextlib.suppress(AttributeError):
                self.health.last_error = _CONFLICT_REASON
        if not self._conflicted:  # once per outage: a line every 60s is a line nobody reads
            _log.error("telegram: %s. %s", exc.description or "409 Conflict", _CONFLICT_REASON)
        self._conflicted = True
        await asyncio.sleep(self.conflict_interval)

    def _note_poll_ok(self) -> None:
        """TG-12: reachability is a poll that returned, never the supervisor's ``state``."""
        if self._conflicted:
            _log.warning("telegram: polling resumed; the other consumer of this token is gone")
            self._conflicted = False
        if self.health is not None:
            self.health.poll_ok()

    def _note_send_failed(self, exc: Exception) -> None:
        """TG-13: a failed send is reported, and never makes the daemon look degraded."""
        if self.health is not None:
            self.health.send_failed(exc)

    async def _dispatch(self, update: Mapping[str, Any], *, update_id: int | None = None) -> None:
        """Every inbound kind goes through the **same** admission check (TG-19, TG-20, TG-23, TG-35).

        The edited-message acknowledgement used to sit here, above the message branch and therefore
        above every guard: measured, ten ``edited_message`` updates from a stranger in an unmapped
        supergroup produced ten outbound replies. Telegram delivers that kind because
        ``ALLOWED_UPDATES`` subscribes to it, so an unauthenticated, unbounded reply amplifier was
        reachable by anyone who found the bot's username. One admission gate in front of the branch
        is what makes "acknowledged once" true per *human* rather than merely per update.
        """
        if "callback_query" in update:
            await self._on_callback(update["callback_query"])
            return
        edited = "edited_message" in update
        message = update.get("edited_message") if edited else update.get("message")
        if not isinstance(message, Mapping):
            return
        if not self._sender_ok(message):
            return
        channel = _channel_of_message(message)
        service = _SERVICE_KEYS & set(message)
        if service or _is_empty(message):
            # TG-92, and it has to come before the text branch: a service message is Telegram
            # talking, not the human, and TG-36's refusal would answer the human's own act of
            # creating a topic with an apology about a photo they did not send.
            await self._on_service(message, channel, service)
            return
        # TG-87: the two commands that *make* a channel have to work in a topic that does not have
        # one yet, or the bind-here form is unreachable and TG-74's reply names a command the human
        # cannot type. Checked ahead of routing, because routing's answer for an unbound topic is
        # the offer itself.
        text = message.get("text")
        command = text.strip() if not edited and isinstance(text, str) else ""
        if command.startswith("/") and await self._bootstrap(channel, command):
            return
        agent_id = await self._route(channel, message)
        if agent_id is None:
            return
        if edited:
            # TG-35: the turn on the original has already run and may already have written.
            await self._say(channel, _EDITED, agent_id=agent_id)
            return
        await self._on_message(message, agent_id, channel, update_id=update_id)

    def _sender_ok(self, message: Mapping[str, Any]) -> bool:
        """Who, before where and before what — and the order is the rule (TG-19, TG-20, TG-95).

        * **private only** (TG-19). A group is many senders with no identity check in front of a
          knowledge base with no undo, and Telegram's group privacy mode silently drops most
          messages anyway — a mapped group *half* works, which is worse than refusing. A topic is
          *inside* a private chat, which is why per-expert channels are compatible with this rule
          rather than an exception to it.
        * **the owner allow-list, before the mapping** (TG-20). It used to run after, so a
          non-allow-listed sender got a refusal in a mapped chat and the full unmapped explanation
          in any other: a guaranteed reply on every path, which is a reply amplifier and an
          existence oracle for anyone who finds the bot's username. Silence is the rule's own
          wording — "ignored silently" — and its own assertion is zero replies for both.

        **One allow-list, and it is not per topic** (TG-95). A channel changes what a message is
        addressed *to*, never who may say yes. Stated as a rule because "a channel per expert"
        invites per-channel permissions, and a second, weaker authorization boundary beside the
        only real one is how the real one stops being checked.
        """
        chat = message.get("chat") or {}
        if chat.get("type") != "private":
            return False
        sender = int((message.get("from") or {}).get("id", 0))
        return sender in self.config.owner_user_ids

    async def _route(self, channel: Channel, message: Mapping[str, Any]) -> str | None:
        """Which agent this **channel** addresses, or ``None`` with the reason posted (TG-72).

        Four outcomes, and the difference between the last three is what keeps a message from being
        filed by whichever expert was nearest:

        * a **bound** channel — General with a mapped chat, or a topic in the directory — is the
          agent's, and the only one it can ever be (TG-1 amended, TG-73).
        * an **unbound topic of a mapped chat** gets TG-74's offer, posted in that topic, naming the
          exact command. It **may list agent ids**: TG-21 withholds them because the bot's username
          is discoverable and topic titles are the sensitive part of a private knowledge base, and
          neither is true once TG-20's allow-list has admitted the sender — for whom the list is the
          one thing that makes ``/channels <agent-id>`` usable from a phone.
        * an **unmapped chat** gets TG-2's two facts and nothing else.
        * a channel whose agent **left the catalog** is answered like an unmapped one and never
          routed to whatever is nearest (TG-18, TG-79). The Telegram topic is left standing: it
          holds the human's history of a topic they may be in the middle of splitting.
        """
        chat_id = channel.chat_id
        directory = await self._directory(chat_id)
        agent_id = directory.get(channel.topic_id)
        if agent_id is not None and agent_id not in self._catalog():
            self._note_invalid(agent_id)
            if self._may_warn(channel):
                await self._say(
                    channel,
                    _UNMAPPED.format(chat_id=chat_id)
                    if channel.is_general
                    else _AGENT_GONE.format(agent_id=agent_id),
                )
            return None
        if agent_id is not None:
            return agent_id
        if self._may_warn(channel):
            if channel.is_general or chat_id not in self.config.chats:
                # TG-2/TG-21: the chat id and where to add it. No agent ids — the bot's username is
                # discoverable, and a listing sent to a stranger leaks the shape of a private KB.
                await self._say(channel, _UNMAPPED.format(chat_id=chat_id))
            else:
                await self._say(channel, await self._binding_offer(chat_id))
        return None

    async def _bootstrap(self, channel: Channel, text: str) -> bool:
        """``/channels`` and ``/agents`` in a topic with no expert yet — ``True`` if handled (TG-87).

        The only two commands admitted here, and they are admitted because they are how a topic
        stops being unbound. Everything else falls through to TG-74's offer: a ``/new`` or a
        ``/cancel`` in a topic that addresses nobody has nothing to act on, and answering it as
        though it did would be the modal addressing TG-1 deletes.

        TG-20's allow-list has already run, so this admits no one new — it changes what an *owner*
        can do in a topic they just made, not who may do it (TG-95).
        """
        if channel.is_general or channel.chat_id not in self.config.chats:
            return False
        if channel.topic_id in await self._directory(channel.chat_id):
            return False
        parts = text.split()
        if parts[0] == "/channels":
            await self._channels(channel, None, parts[1:])
            return True
        if parts[0] == "/agents":
            await self._say(channel, await self._agents_text(channel, None))
            return True
        return False

    async def _binding_offer(self, chat_id: int) -> str:
        """TG-74's reply: what this topic is not, and the one command that fixes it.

        The unchannelled agent ids are the payload. Without them the human is told to type
        ``/channels <agent-id>`` with no way to learn an agent id from a phone, which is a
        instruction that cannot be followed.
        """
        directory = await self._directory(chat_id)
        spare = [agent for agent in sorted(self._catalog()) if agent not in set(directory.values())]
        listing = "\n".join(f"· {agent}" for agent in spare[:_AGENT_LIST_CAP])
        return _UNBOUND_TOPIC + (f"\n\nWith no channel here yet:\n{listing}" if spare else "")

    async def _on_service(
        self, message: Mapping[str, Any], channel: Channel, service: frozenset[str]
    ) -> None:
        """Telegram's own bookkeeping — run nothing, answer nothing, with one exception (TG-92).

        The exception is ``forum_topic_created`` from an owner in a mapped chat: a topic the human
        made by hand is inert until something tells them how to bind it, and the moment they made it
        is the moment they are looking at it. Everything else — an edit, a close, a reopen, a
        text-less message that carries no attachment either — is silence, because it was not sent by
        a human and TG-36's refusal is addressed to one.
        """
        if "forum_topic_created" not in service or channel.is_general:
            return
        if channel.chat_id not in self.config.chats:
            return
        directory = await self._directory(channel.chat_id)
        if channel.topic_id in directory or not self._may_warn(channel):
            return
        await self._say(channel, await self._binding_offer(channel.chat_id))

    async def _on_message(
        self,
        message: Mapping[str, Any],
        agent_id: str,
        channel: Channel,
        *,
        update_id: int | None = None,
    ) -> None:
        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            # TG-36: nothing is downloaded and nothing is written. The caption is the part the human
            # actually wrote, so it is quoted back rather than lost.
            caption = str(message.get("caption") or "")
            await self._say(
                channel,
                _ATTACHMENT + (f"\n\nYour caption:\n{caption}" if caption else ""),
                agent_id=agent_id,
            )
            return

        if text.startswith("/"):
            # No lock: `/cancel` is the one thing that has to reach a run holding it (TG-39).
            await self._command(channel, agent_id, text.strip())
            return
        async with self._channel_lock(channel):
            await self._turn(channel, agent_id, text, update_id=update_id)

    def _may_warn(self, channel: Channel) -> bool:
        """One explanation per **channel** per window; a repeat inside it gets silence (TG-23).

        Anyone who finds the bot can send it a hundred messages, and a bot that answers each one
        earns a Telegram rate limit that then delays the *owner's* approval keyboards. The window is
        process-local on purpose: it protects the send budget, and a restart re-explaining the
        mapping once is the harmless direction to be wrong in.

        Per channel rather than per chat (TG-23 amended): an unbound topic and an unmapped chat are
        different explanations, and sharing one window means the first unbound topic silences the
        explanation for the second — leaving a human with a topic that answers nothing and says
        nothing about why.
        """
        now = time.monotonic()
        last = self._warned_at.get(channel)
        if last is not None and now - last < self.unmapped_window:
            return False
        if len(self._warned_at) >= _WARNED_CAP:
            # A stranger picks their own chat id, so this dict is attacker-sized unless it is
            # bounded. Dropping the oldest half re-explains at worst once more per channel.
            oldest = sorted(self._warned_at.items(), key=lambda item: item[1])
            self._warned_at = dict(oldest[len(oldest) // 2 :])
        self._warned_at[channel] = now
        return True

    # -- the channel directory (TG-77) ------------------------------------------------

    async def _directory(self, chat_id: int) -> dict[int, str]:
        """``topic_id → agent_id`` for one chat, **including General** (TG-72, TG-73).

        General is folded in from ``config.chats`` rather than stored, which is what makes it a
        mapped channel like any other while leaving TG-17 intact: the human's decision about which
        agent answers in General is still a line in a configuration file the bot never writes.
        Folding it in here is also what stops ``/channels all`` creating a second channel for the
        agent that already answers in General — two channels for one agent in one chat is that
        expert's history split in half, invisibly (TG-25 amended, TG-77).

        Read from the store on every use rather than cached: a stale directory routes a message to
        the previous expert, and the read is one indexed statement on a local SQLite file — the same
        cost as the binding lookup that already happens on this path.
        """
        rows = dict(await self.store.channels(chat_id))
        general = self.config.chats.get(chat_id)
        if general is not None:
            rows.setdefault(GENERAL, general)
        return rows

    async def _channel_of(self, chat_id: int, agent_id: str) -> Channel | None:
        """Where this agent is reached in this chat, or ``None`` if it has no channel here."""
        for topic_id, owner in (await self._directory(chat_id)).items():
            if owner == agent_id:
                return Channel(chat_id, topic_id)
        return None

    def _catalog(self) -> dict[str, str]:
        """``agent_id → title`` from the live registry (RG-14, GE-25).

        Read per use, not cached at startup: TG-79 turns on an agent *leaving* the catalog, and a
        cached answer would keep routing to it — filing into a topic the knowledge base no longer
        has. ``list_agents`` is the same synchronous registry read ``check_mapping`` already makes.
        """
        return {descriptor.agent_id: descriptor.title for descriptor in self.service.list_agents()}

    def _note_invalid(self, agent_id: str) -> None:
        """TG-79: an agent that left the catalog is *reported*, never routed around silently.

        Onto ``retired_channels``, beside the channels TG-82 gave up on, because to a human they are
        one question — *"why is Grilling not answering in its own topic?"* — and the answer to both
        is that its traffic is taking a longer road while nothing has been deleted.
        """
        if self.health is None:
            return
        with contextlib.suppress(AttributeError):
            existing = tuple(self.health.retired_channels)
            if agent_id not in existing:
                self.health.retired_channels = tuple(sorted({*existing, agent_id}))

    async def _command(self, channel: Channel, agent_id: str, text: str) -> None:
        """Five commands, each acting on **the channel it was typed in** (TG-86).

        A ``/cancel`` that reaches the wrong turn is worse than no ``/cancel``: it stops a turn the
        human wanted and leaves the one they were trying to stop writing into a tree with no undo.
        The same argument applies one at a time to ``/new`` and ``/threads``, which is why none of
        them takes an agent argument — the topic is the argument, and it is on the screen.
        """
        parts = text.split()
        command = parts[0]
        if command == "/new":
            await self.store.unbind(channel.chat_id, channel.topic_id)
            await self._say(
                channel,
                "Started a new conversation. The previous one is still in your thread list.",
                agent_id=agent_id,
            )
        elif command == "/threads":
            threads = await self.service.list_threads(agent_id)
            await self._say(channel, _threads_text(threads), agent_id=agent_id)
        elif command == "/agents":
            await self._say(channel, await self._agents_text(channel, agent_id), agent_id=agent_id)
        elif command == "/cancel":
            await self._cancel(channel, agent_id)
        elif command == "/channels":
            await self._channels(channel, agent_id, parts[1:])
        else:
            await self._say(channel, f"I know {', '.join(COMMANDS)}.", agent_id=agent_id)

    async def _agents_text(self, channel: Channel, agent_id: str | None) -> str:
        """Who answers **here**, and where the others are (TG-73, TG-86).

        Naming this channel's own agent first is not decoration: General is the only channel whose
        title names no agent, so a human whose General talks to Cooking has nothing on the screen
        saying so. This is the place they can find out (Q29 ruled: here rather than a per-start
        notice, because a notice on a daemon that stays up for weeks is either invisible or, under
        a restart loop, spam).
        """
        directory = await self._directory(channel.chat_id)
        catalog = self._catalog()
        rows = []
        for topic_id, owner in sorted(directory.items()):
            where = "General" if topic_id == GENERAL else catalog.get(owner, owner)
            here = "  ← you are here" if topic_id == channel.topic_id else ""
            rows.append(f"· {owner} — {where}{here}")
        spare = [agent for agent in sorted(catalog) if agent not in set(directory.values())]
        header = (
            f"This channel talks to {agent_id}."
            if agent_id is not None
            else "This topic is not connected to an expert yet."
        )
        text = f"{header}\n\nChannels in this chat:\n" + "\n".join(rows)
        if spare:
            text += "\n\nNo channel here yet (use /channels <agent-id>):\n" + "\n".join(
                f"· {agent}" for agent in spare[:_AGENT_LIST_CAP]
            )
        return text

    # -- /channels: the whole creation surface (TG-76, TG-87) --------------------------

    def _creation_lock(self, chat_id: int) -> asyncio.Lock:
        """One ``/channels`` at a time per chat (TG-77, TG-101). See :attr:`_creations`."""
        lock = self._creations.get(chat_id)
        if lock is None:
            lock = self._creations[chat_id] = asyncio.Lock()
        return lock

    async def _channels(
        self, channel: Channel, agent_id: str | None, arguments: Sequence[str]
    ) -> None:
        """List, bind or create — and **always say which happened** (TG-87, decision AA).

        This is the only code path in the adapter that calls ``createForumTopic`` (TG-76). Nothing
        is created at startup, when the catalog gains an agent, on an inbound message or on a
        restart: eager creation buries the four channels a human uses under twenty-six they do not,
        cannot be undone in one action, and — because **no API enumerates a chat's topics** — leaves
        a state after a partial failure that nothing can reconstruct.

        The bind-here form exists for exactly that last reason: it is the only recovery path for a
        lost SQLite file. One command with two behaviours is a real ambiguity (Q30, ruled), and it
        is paid for by the reply naming which one occurred.

        Everything from the directory read to the creation runs under :attr:`_creations`, one lock
        per chat. TG-77's *"creates nothing"* is a check-then-act across three awaits, and a picker
        row makes a second concurrent ``/channels`` one thumb movement rather than a re-typed
        command: measured against the fake, a double tap produced two ``createForumTopic`` calls and
        one directory row, leaving a duplicate topic the bot may never delete. The second caller now
        reads the directory the first one wrote and answers with TG-77's pointer.
        """
        if not self._topics:
            await self._say(channel, _NO_TOPICS)
            return
        chat_id = channel.chat_id
        if not arguments:
            # TG-96: the roster is a keyboard, because `_binding_offer` telling a human to type
            # `/channels <agent-id>` on a phone is an instruction that cannot be followed.
            await self._picker(channel)
            return
        async with self._creation_lock(chat_id):
            catalog = self._catalog()
            directory = await self._directory(chat_id)
            if arguments[0] == _ALL_AGENTS:
                wanted = [
                    agent for agent in sorted(catalog) if agent not in set(directory.values())
                ]
                if not wanted:
                    await self._say(channel, "Every agent already has a channel in this chat.")
                    return
                for agent in wanted:
                    await self._create_channel(channel, agent)
                return
            agent_id = arguments[0]
            if agent_id not in catalog:
                await self._say(channel, _NO_SUCH_AGENT.format(agent_id=agent_id))
                return
            existing = await self._channel_of(chat_id, agent_id)
            if existing is not None:
                # TG-77: creating nothing is the rule, not an optimisation. A second channel for one
                # agent in one chat is that expert's history split in half, with nothing on screen
                # saying which half a message went to.
                await self._point_at(channel, existing, agent_id)
                return
            if not channel.is_general and channel.topic_id not in directory:
                await self.store.open_channel(chat_id, channel.topic_id, agent_id)
                self._revive(chat_id, agent_id, channel.topic_id)
                self._note_channel(agent_id)
                # TG-105: the bot has just taken this topic as a channel, so the bot names it. The
                # durable write goes first (TG-106), so a rename that fails leaves a channel that
                # works and a reply that says the name did not change.
                title = self._catalog().get(agent_id, agent_id)
                reason = await self._name_channel(channel, agent_id, title)
                if reason is None:
                    await self._say(channel, _BOUND.format(agent_id=agent_id, title=title))
                else:
                    await self._say(
                        channel,
                        _BOUND_UNNAMED.format(agent_id=agent_id, title=title, reason=reason),
                    )
                return
            await self._create_channel(channel, agent_id)

    async def _point_at(self, typed_in: Channel, existing: Channel, agent_id: str) -> None:
        """TG-77's pointer, sent **into the channel it names** (TG-102, TG-104, decisions AI, AJ).

        A human deleted the Cooking topic, tapped its picker row, and was told *"topic/cooking
        already has a channel in this chat (topic 101), so I created nothing"* about a topic
        Telegram had deleted, in a channel that was not it. The channel was then unrecoverable from
        the phone. TG-82 and TG-83 recreate a dead channel and **both are triggered by a failed
        send**; nothing will ever send into a deleted topic, because no message can arrive from one
        (F-5) and the one message that named it went somewhere else. The bot held the address of a
        dead topic, the code that repairs a dead topic, and no path between them.

        Sending the pointer through the channel it names is that path. On a live channel it points
        at a place the human can open, rather than at a topic id every Telegram client hides
        (§9.10 struck the same id from TG-74's reply). On a dead one it raises ``message thread not
        found``, which is TG-83's trigger, so the repair that already exists, is already tested and
        was already unreachable runs. **There is no second recovery path here**, and there must not
        be: §9.10 defect 3 and §9.11 defect A are both what this layer does when repair grows a
        second door.

        **The agent id goes with the send.** An unattributed send cannot be routed by TG-82's
        retirement, resolves its repair through the directory rather than the row, and is what
        §9.12 defect 4 measured as a channel pinned to General for the life of the daemon.

        **The typing channel gets its own line, and only when it is a different channel.** The
        human is standing in General when they tap; TG-100 leaves the picker keyboard live and
        unmarked after a press, so a press answered only somewhere else reads as a press that did
        nothing, and the next thing the thumb does is press again. Two lines in one topic is the
        noise that shape avoids, so a command typed in the channel it names gets one message.

        **The count is cleared first** (TG-103, decision AJ). ``MAX_RECREATIONS`` bounds the repairs
        the bot makes on its own, and a human tapping a row is the decision the bound defers to.
        Cleared afterwards it would be too late: the repair this call triggers would read a count
        already at the bound and retire the channel, telling the human their expert had moved to
        General by way of the tap meant to bring it back.

        **The clear reads and writes under :meth:`_repair_lock`, and writes the row's own topic id**
        (TG-103, §11.8). ``open_channel`` sets ``topic_id`` as well as the count, so the two
        statements are a check-then-act on a column :meth:`_repair` owns and writes two awaits away.
        That is the third instance of the shape in this file, after ``_channel_died``'s ``known``
        guard and the picker's double press. Measured against the fake: an unattended send that
        discovered the same deletion inside the row read finished its repair first, and the clear
        then wrote the **dead** topic id back over it. The directory named a topic Telegram had
        deleted, and the topic the bot had just created stood on the human's phone addressed by
        nothing, which is §9.12 defect 4's silent topic produced by the repair itself. The lock is
        released before the pointer goes out, because the pointer's own failure takes that lock.

        **A General pin left by a failed recreation is dropped here** (TG-104). ``_repair`` answers a
        ``createForumTopic`` failure by re-addressing the channel to General and leaving the row
        naming the topic, and that mapping has no expiry: every later send is re-addressed before it
        can fail, so nothing reaches TG-83 again and ``_POINTER_LOST`` keeps naming a command that
        provably does nothing. The human asking for the channel by name is what clears it, which is
        the argument :meth:`_revive` makes for a retirement.
        """
        chat_id = existing.chat_id
        async with self._repair_lock(existing):
            row = await self.store.channel(chat_id, agent_id)
            if row is not None and int(row["recreations"]):
                # `open_channel` is the write TG-87's revival already makes, so both doors leave a
                # channel the human just asked for with the same allowance. Skipped at zero so the
                # ordinary pointer writes nothing and `created_at` keeps naming the moment the human
                # made the channel rather than the last time they asked about it.
                await self.store.open_channel(chat_id, int(row["topic_id"]), agent_id)
            if not existing.is_general and self._moved.get(existing) == existing.general:
                # TG-104: a `createForumTopic` that failed leaves the row naming the topic and pins
                # the channel to General in process memory. Every later send is then re-addressed
                # **before** it can fail, so TG-83's trigger never fires for that channel again and
                # the pointer below reaches General rather than the topic. Measured: two
                # `/channels topic/cooking` after one failed recreation issued zero further
                # `createForumTopic` calls and answered `_POINTER_LOST` both times, which names that
                # exact command as the way out. The human asking for the channel by name is the act
                # `_revive` answers for a retirement, so the pin goes and the pointer can die again.
                del self._moved[existing]
        await self._say(existing, _ALREADY_HERE.format(agent_id=agent_id), agent_id=agent_id)
        if existing == typed_in:
            return
        # Read after the send, from the function every send already asks: the same channel means the
        # topic was alive, a different topic means TG-83 repaired it, and General means the
        # recreation failed and TG-82 fell back. Nothing new is recorded to make this decidable.
        landed = self._route_out(existing, agent_id)
        if landed == existing:
            where = "General" if existing.is_general else f"topic {existing.topic_id}"
            await self._say(typed_in, _ALREADY.format(agent_id=agent_id, where=where))
        elif landed.is_general:
            await self._say(typed_in, _POINTER_LOST.format(agent_id=agent_id))
        else:
            title = self._catalog().get(agent_id, agent_id)
            await self._say(typed_in, _REOPENED.format(agent_id=agent_id, title=title))

    async def _picker(self, channel: Channel) -> None:
        """The catalog as buttons, one row per agent, in the order the service returned them.

        No client-side sort of any kind (TG-96): not alphabetical, not unbound-first, not
        most-recently-used. The TUI sidebar and ``/agents`` show the catalog's order, and a phone
        that re-ranks makes the human's two views of one knowledge base disagree about where an
        expert sits. TG-40 already ruled this for ``/threads`` and gave the reason — re-sorting
        buries the row the human came back for.

        The roster **is** the keyboard, so the body carries one instruction line and no second
        listing. Two orderings on one screen is one that can disagree with the other.

        A catalog too large for one keyboard becomes more keyboards, and the remainder is counted
        (TG-99). Silent truncation would leave a human whose expert is missing unable to tell that
        from an expert that does not exist, which is the distinction TG-3 exists to make visible. A
        paging cursor is refused for the reason a positional payload is: the keyboard outlives the
        catalog it was drawn from, so page 2 after a rename skips an agent without saying so.
        """
        catalog = self._catalog()
        if not catalog:
            # Never an empty keyboard: on a phone it reads as a delivery that went wrong.
            await self._say(channel, _PICKER_EMPTY)
            return
        bound = set((await self._directory(channel.chat_id)).values())
        agents = list(catalog)
        drawn = agents[: PICKER_ROWS * PICKER_MESSAGES]
        pages = [drawn[at : at + PICKER_ROWS] for at in range(0, len(drawn), PICKER_ROWS)]
        undrawn = len(agents) - len(drawn)
        for position, page in enumerate(pages):
            body = counter(position, len(pages)) + _PICKER_HEADER
            if undrawn and position == len(pages) - 1:
                body += "\n\n" + _PICKER_MORE.format(count=undrawn)
            await self._send(
                channel,
                body,
                [
                    [
                        {
                            "text": _picker_label(agent, bound=agent in bound),
                            "callback_data": picker_callback(agent),
                        }
                    ]
                    for agent in page
                ],
            )

    async def _on_picker(self, query: Mapping[str, Any], callback_id: str, token: str) -> None:
        """A row of the channel menu: answer, re-read the world, then run the typed command (TG-98).

        **The drawing is never trusted.** A Telegram message keeps its buttons live forever, so the
        state a row was marked against is a claim about a moment that has passed. Every authority is
        read again here — the allow-list above, this chat's place in the mapping, the live catalog —
        and the rest is read inside :meth:`_channels`, which is also what answers a press on an agent
        that has left the catalog, on one that already has a channel, and on a bot whose Threaded
        Mode was turned off after the keyboard was drawn.

        **The same function the typed command calls**, so bind-or-create (Q30) is answered
        identically through both doors. A second implementation of it would pass every other test in
        the suite and then disagree with the typed form on the one case a human hits.

        The callback is answered **before** the work (TG-61). A press that starts a
        ``createForumTopic`` and answers afterwards leaves the button spinning until the query
        expires, and a human with no other feedback presses again. The two checks that precede the
        answer are a dict lookup and a registry read, because a refusal has to carry an alert and
        Telegram keeps only the first answer to a query.
        """
        channel = _channel_of_query(query)
        if channel is None or channel.chat_id not in self.config.chats:
            await self._answer(callback_id, _PICKER_UNMAPPED, alert=True)
            return
        agent_id = resolve_picker(token, self._catalog())
        if agent_id is None or agent_id == _ALL_AGENTS:
            # TG-101: one press, one channel. `/channels all` stays typed, so the `all` branch is
            # unreachable from a button by construction rather than by the picker declining to draw
            # one — a fabricated payload reaches this handler too. Decision AA's three arguments
            # against a burst of creations all survive being moved to a thumb.
            await self._answer(callback_id, _PICKER_UNKNOWN, alert=True)
            return
        await self._answer(callback_id)
        try:
            here = (await self._directory(channel.chat_id)).get(channel.topic_id)
            await self._channels(channel, here, [agent_id])
        except Exception as exc:
            # The poll loop swallows exceptions so one bad update cannot stop the bot, which would
            # make a failure here silent — and TG-100 leaves the keyboard live, so the human would
            # press again on a button that has already stopped working.
            _log.warning("telegram: a channel button could not be applied", exc_info=True)
            await self._say(channel, _PRESS_FAILED.format(reason=exc))

    async def _create_channel(self, where: Channel, agent_id: str) -> None:
        """One ``createForumTopic``, the agent's catalog title, and nothing else (TG-78).

        No ``icon_color`` and no ``icon_custom_emoji_id``: every parameter on this Protocol has to
        be implemented by every fake, and neither has a rule behind it. The binding is by topic
        **id**, so a human renaming the topic afterwards changes nothing, and the bot leaves that
        rename standing (TG-105). The bot closes and deletes nothing, ever, because the topic is the
        human's record of what they approved.

        **This path issues no ``editForumTopic``** and a test pins that. The name arrives with the
        creation, so a second call would be the policing TG-105 refuses, on the one path where it
        would also be redundant.

        A failure is reported in the chat rather than raised: the human asked for a channel and the
        useful answer is that they did not get one, not a restarted bot.
        """
        title = self._catalog().get(agent_id, agent_id)
        try:
            created = await with_retry(
                lambda: self.api.create_forum_topic(where.chat_id, title),
            )
        except TelegramError as exc:
            self._note_send_failed(exc)
            _log.warning("telegram: could not create a topic for %s: %s", agent_id, exc)
            await self._say(where, _CREATE_FAILED.format(agent_id=agent_id, reason=exc))
            return
        topic_id = _created_topic_id(created)
        if not topic_id:
            await self._say(where, _CREATE_FAILED.format(agent_id=agent_id, reason="no topic id"))
            return
        await self.store.open_channel(where.chat_id, topic_id, agent_id)
        self._revive(where.chat_id, agent_id, topic_id)
        self._note_channel(agent_id)
        await self._say(where, _CREATED.format(agent_id=agent_id, title=title))

    async def _name_channel(self, channel: Channel, agent_id: str, title: str) -> str | None:
        """Name a topic the bot has just taken as a channel (TG-78 amended, TG-105, TG-106).

        Called from the bind branch of :meth:`_channels` and from nowhere else. The create branch
        needs no rename, because ``createForumTopic`` carries the title already.

        **Why the bot renames at all**, after a rule that said it never would. A human made a topic
        in their own client, where Telegram called it *New Chat*, and bound it with ``/channels
        topic/cooking`` from inside it. The bind wrote the row, answered *"Bound this topic"* and
        left the name, so the human then talked to an expert whose name appeared nowhere on the
        screen. Decision AE prints no agent id on an ordinary reply inside a channel, and the ground
        it gives is that the topic header names the expert. A channel called *New Chat* removes that
        ground and leaves an unattributed conversation in front of a tree with no undo, which is the
        ambiguity TG-1 deleted ``/connect`` for. TG-78 was right about the human's furniture and
        wrong about ownership: taking a topic as a channel is the act that stops it being furniture.

        **The rename is unconditional** (decision AK). A build that renamed only a topic still
        carrying a Telegram default, or only one whose name differs from the title, needs the
        current name. No Bot API method returns one. One message carries one: ``forum_topic_created``
        reaches :meth:`_on_service` with the name on it, for a topic the human made by hand as well
        as for one the bot made, so the condition would run off a **copy** of a value Telegram owns
        and the human may change. That is TG-102's shape: ``forum_topic_edited`` carries every later
        rename and TG-92 silences it, a restart drops the copy, and a topic that predates the mapping
        never produced one. The condition would then fire by uptime, answering *"already Cooking"*
        for a topic titled *New Chat*, which is the failure this method exists to end; the *New Chat*
        form of it would fire by locale on top, because Telegram picks that string and translates it.
        Losing a deliberate title costs two taps and is announced in the reply the same second;
        skipping costs a permanently unnamed expert, in silence.

        **Nothing after this call renames anything** (TG-105). No inbound message, no restart, no
        catalog title change (Q36), and a human who renames the channel afterwards has made a
        decision that stands. TG-78's original instinct holds from here on.

        **A failure is a reason, never a repair** (TG-106). The caller has already written the
        durable row, so the channel works and the reply says the name did not change. This never
        reaches TG-82 or TG-83: a rename delivers nothing, so nothing was lost and there is nothing
        to re-send, and creating a topic in answer would give an agent a second channel one line
        after it got its first, which is TG-77's split history produced by the fix for a missing
        title. **The outcome is not a liveness signal in either direction** (TG-102): F-13 measured
        one method of this API answering ``ok: true`` for a topic Telegram had deleted, so success
        proves nothing, and failure proves nothing either. Nothing is written from it.

        ``with_retry`` is the same transport rule every other call here runs under, and it needs no
        branch for a dead topic: ``400`` is absent from ``RETRY_CODES``, so ``message thread not
        found`` propagates on the first attempt and lands in the same reply as any other refusal.

        ``agent_id`` names the log line and ``title`` goes on the wire. The caller computes the
        title once and passes it, so the call and the reply can never name two different titles.
        """
        try:
            await with_retry(
                lambda: self.api.edit_forum_topic(channel.chat_id, channel.topic_id, title)
            )
        except TelegramError as exc:
            self._note_send_failed(exc)
            _log.warning("telegram: could not name the topic for %s: %s", agent_id, exc)
            return str(exc)
        return None

    def _revive(self, chat_id: int, agent_id: str, topic_id: int) -> None:
        """A channel the bot had given up on is answering again (TG-82, TG-84).

        ``open_channel`` clears ``retired`` and ``recreations`` in the durable row, and that is only
        half of it: :meth:`_route_out` consults the in-process sets **before** it reads anything,
        so a retirement left standing here sends every one of that expert's messages to General with
        a prefix — for the life of the daemon, into a topic the human created because the retirement
        notice told them to. They would be left with a fresh topic carrying their expert's name,
        permanently silent, and nothing anywhere saying why.

        The ``_moved`` re-point is the second half. Retirement ends the re-addressing chain at
        General; re-pointing the channel retirement condemned makes every frame still queued for the
        old id follow the agent to its new topic instead of arriving in General under a prefix
        (TG-84 — re-addressed, never dropped and never sent blind).
        """
        dead = self._retired.pop((chat_id, agent_id), None)
        if dead is not None and dead != Channel(chat_id, topic_id):
            self._moved[dead] = Channel(chat_id, topic_id)

    def _note_channel(self, agent_id: str) -> None:
        """TG-11: a created channel makes its agent reachable, so ``unmapped_agents`` must shrink.

        The endpoint's computation is untouched — it is still ``{catalog} - health.telegram.agents``
        — and the daemon seeds that set from the store at composition time so the answer survives a
        crash-looping bot. This adds the one that was just made.
        """
        if self.health is None:
            return
        with contextlib.suppress(AttributeError):
            self.health.agents = frozenset({*self.health.agents, agent_id})
            self.health.channels = int(getattr(self.health, "channels", 0)) + 1

    async def _turn(
        self, channel: Channel, agent_id: str, text: str, *, update_id: int | None = None
    ) -> None:
        """One message, one turn on the **channel's** current thread (TG-26, TG-4, TG-29).

        Per channel rather than per chat (TG-26 amended): one thread per chat under topics would
        mean every expert in a chat sharing one conversation, which is worse than the mis-file TG-1
        exists to prevent — the human would see distinct topics and reasonably assume distinct
        conversations.

        The binding carries the agent it was made for, and a mismatch with the *configured* agent
        rotates the chat onto a fresh thread. Without that check, editing the mapping kept filing
        into the previous expert forever: measured, a chat bound under ``topic/cooking`` and then
        re-mapped to ``topic/grilling`` issued **zero** ``create_thread`` calls and sent the new
        message to the Cooking thread — a write to the wrong topic, with no undo, invisible from
        the phone and from ``/health``. That is the mis-file TG-1 was ruled to eliminate, so the
        rotation is announced rather than silent (TG-27's reasoning applies to a configuration
        change too: an invisible rotation is the failure class, not the rotation).
        """
        binding = await self.store.binding(channel.chat_id, channel.topic_id)
        if binding is not None and binding[1] != agent_id:
            await self._say(channel, _REMAPPED.format(agent_id=agent_id), agent_id=agent_id)
            binding = None
        if binding is None:
            # TG-4: stamped `telegram` exactly once, so a conversation started on the phone is
            # recognisable in the TUI. Never read back in a conditional (TG-33).
            thread = await self.service.create_thread(agent_id, origin_channel="telegram")
            thread_id = thread.thread_id
            await self.store.bind(channel.chat_id, channel.topic_id, thread_id, agent_id)
        else:
            thread_id = binding[0]
        try:
            subscription = await self.service.start_run(thread_id, text)
        except ApprovalPendingError:
            # TG-37: neither rotate nor retry. No gate can park a *new* interrupt any more
            # (DESIGN.md §2.10), so this is unreachable except for a thread whose checkpoint still
            # carries one from before that change — a truthful compile-keeper (RT-39), not a live
            # path. There is no keyboard left to re-post for it (the approval-prompt machinery this
            # branch used to call into is gone), so the chat is simply told, once, why nothing sent.
            await self._say(channel, _PENDING_BLOCKS.format(text=text), agent_id=agent_id)
            return
        except ThreadBusyError:
            # TG-38: the normal case on a phone, where people send three lines as three messages.
            await self._say(channel, _BUSY.format(text=text), agent_id=agent_id)
            return
        if update_id is not None:
            # TG-29: admitted, so this update is no longer a loss the bot may ask the human to
            # re-send. The thread and run ids are recorded here because this is where they first
            # exist and TG-31's re-sync has nothing to reattach to without them.
            await self.store.started(update_id, thread_id, subscription.handle.run_id)
        await self._consume(channel, agent_id, subscription)

    # -- the run ----------------------------------------------------------------------

    async def _consume(
        self, channel: Channel, agent_id: str | None, subscription: Any, *, replay: bool = False
    ) -> None:
        """Relay one run into the chat. The pump never blocks on a Bot API call (TG-49).

        ``replay=True`` is TG-31 branch (b): the stream was reattached after a restart, and
        ``attach`` replays the hub from ``seq 0``, so every assistant message it yields is one the
        chat may already hold. Only the **outcome** is rendered; the frames are consumed and
        discarded, which is the difference between re-syncing and double-posting.
        """
        # `interrupted` is never set True any more: no gate can raise an `InterruptEvent` (DESIGN.md
        # §2.10), so the branch that used to watch for one and post its keyboard is gone. The flag
        # stays, passed to `terminal_status` below unchanged, matching the same truthful
        # compile-keeper `pkb.service.runtime` keeps on the event type itself.
        interrupted = False
        roster: list[str] = []
        terminal: RunEnd | RunError | None = None
        try:
            async for event in subscription.events:
                if isinstance(event, MessageComplete):
                    # TG-41: the fact channel only. Deltas arrive too, and editing per token is
                    # hundreds of calls a second against a one-per-second budget.
                    if not replay:
                        await self._queue(channel, event.text, agent_id)
                elif isinstance(event, SubagentStart):
                    roster.append(event.agent_id)
                elif isinstance(event, SubagentEnd):
                    pass  # coalesced into the single roster line below (TG-43)
                elif isinstance(event, RunEnd | RunError):
                    terminal = event
        except Exception:
            # TG-51: a stream that *raises* closed without a terminal frame just as surely as one
            # that stopped, so it takes the same branch. It used to propagate into `_poll`'s
            # blanket `suppress(Exception)`, which left the human with half a reply, no
            # "outcome unknown" line and no re-sync — indistinguishable from success, on a turn
            # that may already have written. Not re-raised, because re-raising is what makes it
            # invisible; not restarted, because D2 says a run outlives the client watching it.
            _log.warning("telegram: the event stream for channel %s ended abnormally", channel)
            terminal = None
        finally:
            # TG-52: synchronous, and never awaited — the same idiom the routes and MCP use.
            _detach(subscription)

        if roster:
            await self._queue(channel, "Asked: " + ", ".join(dict.fromkeys(roster)), agent_id)
        if terminal is None:
            # TG-51: outcome unknown. Never success, never failure, and never a re-start.
            await self.service.get_thread(subscription.handle.thread_id)
            await self._queue(channel, _UNKNOWN, agent_id)
            return
        status = terminal_status(terminal, interrupted=interrupted)
        note = _TERMINAL.get(status)
        if note:
            await self._queue(channel, note, agent_id)

    # -- button presses -----------------------------------------------------------------

    async def _on_callback(self, query: Mapping[str, Any]) -> None:
        """A button press. **The callback is answered first, on every path** (TG-61, TG-20, TG-62).

        Answered **once**: Telegram accepts one ``answerCallbackQuery`` per query, so a second call
        carrying the alert is simply discarded and the refusal never reaches the phone.

        A press from anyone outside the allow-list is refused **with an alert** and logged. On a
        phone a silent answer is indistinguishable from a successful one, and this allow-list is the
        system's only authentication boundary, which makes an attempt against it the one event the
        operator has to see (decision X).

        **The channel picker is the only grammar this bot draws any more** (TG-97). No gate can
        raise an approval keyboard now that none can interrupt a run (DESIGN.md §2.10 — the
        operator's instruction is the approval), so the whole approve/reject/confirm flow that used
        to live here is gone with it — there is no prompt store left to resolve a press against, and
        nothing this bot posts today carries any other ``callback_data``. A press carrying anything
        else is a button from before that change, still sitting live in some chat, and
        :data:`_UNREADABLE` is the honest answer for it: this bot cannot read that button any more.
        """
        callback_id = str(query.get("id", ""))
        sender = int(query.get("from", {}).get("id", 0))
        if sender not in self.config.owner_user_ids:
            _log.warning(
                "telegram: refused a button press from user %s in chat %s — not in the owner "
                "allow-list, which is this deployment's only authentication boundary",
                sender,
                _channel_of_query(query),
            )
            await self._answer(callback_id, _REFUSED, alert=True)
            return

        data = str(query.get("data", ""))
        token = parse_picker(data)
        if token is not None:
            await self._on_picker(query, callback_id, token)
            return

        await self._answer(callback_id, _UNREADABLE, alert=True)

    async def _answer(self, callback_id: str, text: str = "", *, alert: bool = False) -> None:
        """Stop the spinner — once per press, and carrying the outcome when there is one (TG-61)."""
        if not callback_id:
            return
        with contextlib.suppress(TelegramError):
            await self.api.answer_callback(callback_id, text, show_alert=alert)

    async def _cancel(self, channel: Channel, agent_id: str) -> None:
        """``/cancel`` — the one place ``service.cancel`` is reachable from (TG-32, TG-39).

        The attached subscription is closed in a ``finally`` (TG-52). ``attach`` replays the hub
        from ``seq 0`` and holds a subscriber slot for as long as nobody detaches, so a ``/cancel``
        that walked away from it leaked exactly the subscriber that rule exists to prevent — in the
        daemon whose whole value proposition is staying up for weeks. Closing is **synchronous**:
        ``RunSubscription.close`` is a plain function despite its docstring, and awaiting it raises
        ``TypeError`` inside a ``finally`` during teardown (C-31).

        It cancels **this channel's** run and no other (TG-86). A ``/cancel`` that reaches the wrong
        turn is worse than no ``/cancel``: it stops a turn the human wanted and leaves the one they
        were trying to stop writing into a tree with no undo.
        """
        thread_id = await self.store.bound_thread(channel.chat_id, channel.topic_id)
        if thread_id is None:
            await self._say(channel, "Nothing is running here.", agent_id=agent_id)
            return
        subscription = await self.service.attach(thread_id)
        if subscription is None:
            await self._say(channel, "Nothing is running here.", agent_id=agent_id)
            return
        try:
            await self.service.cancel(subscription.handle.run_id)
        finally:
            _detach(subscription)
        await self._say(channel, "Cancelled.", agent_id=agent_id)

    # -- outbound ---------------------------------------------------------------------

    async def _queue(self, channel: Channel, text: str, agent_id: str | None = None) -> None:
        """Hand a message to the outbox. Drops **progress** only, never a decision (TG-49).

        Which, measured, means it drops nothing: TG-43 already makes ``ToolStart``/``ToolEnd``/
        ``SubagentEnd`` produce no message at all, so the only things that ever reach this queue are
        a ``MessageComplete``, the roster line and a terminal note — every one of them on the rule's
        never-drop list. The bounded queue therefore had a drop path that could *only* ever discard
        a forbidden frame, and it did: with the outbox full behind a ``retry_after: 30``, the reply
        the human was waiting for was thrown away and logged as "dropping a progress message".

        The queue is **unbounded** rather than blocking, and that is the second half of the same
        rule. Blocking here would suspend the ``async for``, and ``RunHub`` drops a subscriber whose
        own queue exceeds 256 — closing the stream *without* a terminal frame, which is
        indistinguishable from an unknown outcome. One ``429`` with ``retry_after: 30`` inside the
        pump is enough to reach that during a fan-out at model pace, so back-pressure has to be
        absorbed here, in memory, rather than pushed back onto the hub. The depth is bounded in
        practice by one run's message count; a queue that keeps growing is logged, because it means
        the pump has been stuck for a long time and that is worth seeing.
        """
        self._outbox.put_nowait((channel, text, agent_id))
        depth = self._outbox.qsize()
        if depth and depth % _OUTBOX_WARN == 0:
            _log.warning(
                "telegram: %d message(s) are queued for chat %s and not going out; the Bot API has "
                "been refusing or rate limiting sends",
                depth,
                channel.chat_id,
            )

    async def _say(
        self,
        channel: Channel,
        text: str,
        *,
        agent_id: str | None = None,
        prefix: bool = False,
        attributed: bool = False,
    ) -> None:
        await self._send(
            channel, text, None, agent_id=agent_id, prefix=prefix, attributed=attributed
        )

    async def _announce(self, channel: Channel, text: str) -> None:
        """A startup notice, whose failure is **reported and swallowed** (TG-13).

        The cold-start notice and the orphan report are both sent before the poll loop is running,
        outside any suppression, so a chat the bot was removed from — or one whose owner blocked
        it, the ordinary case — used to raise a 403 straight into ``_supervise``. Measured: one such
        chat gave ``restarts: 3`` and climbing, ``state: "restarting"`` and ``/health`` permanently
        ``degraded``, forever, in a restart loop D9 forbids. A chat that cannot be written to is not
        the subsystem being down; it is one recipient, and ``last_send_error`` is where that
        belongs.
        """
        try:
            await self._send(channel, text, None)
        except TelegramError as exc:  # already recorded on `/health` by `_send`
            _log.warning("telegram: could not deliver a startup notice to %s: %s", channel, exc)

    async def _send(
        self,
        channel: Channel,
        text: str,
        keyboard: list[list[dict[str, str]]] | None,
        *,
        agent_id: str | None = None,
        prefix: bool = False,
        attributed: bool = False,
    ) -> Mapping[str, Any]:
        """Send now, split on length, honouring a 429 — and **check where it landed** (TG-80).

        This retry is a **transport** retry of an idempotent send, and is explicitly not the run
        retry the TUI forbids: a dropped reply after an approved write means the human never learns
        what was written, and a dropped keyboard means a parked interrupt nobody is told about.

        ``split_message`` has already reserved the counter's units, so ``label + part`` is inside
        the wire limit — the counter is part of the payload Telegram measures (TG-44).

        **The comparison at the bottom is the whole of §9.** A send into a deleted private-chat
        topic returns ``ok: true``: the ``message_thread_id`` is ignored and the message lands in
        General (F-2, tdlib#854). Nothing raises, nothing is logged by Telegram, and no update
        announces the deletion — so the only evidence that exists is the ``message_thread_id`` on
        the returned ``Message``. It is compared **unconditionally**, not sampled and not limited to
        approvals, because the message that reveals the problem is whichever one happens to be next
        and the one that must not be missed is an approval keyboard.

        A message whose topic died is **re-sent whole** into wherever the channel now points, up to
        :data:`_SEND_ATTEMPTS`. Re-sending parts already delivered is deliberate: they went to
        General under the wrong expert's name, and a human reading the repaired topic needs the
        whole message, not its tail.
        """
        for _ in range(_SEND_ATTEMPTS):
            target = self._route_out(channel, agent_id)
            # TG-85(b)/(c): the prefix follows **exposure**. Falling back to General is the only
            # re-addressing that takes a message out of its agent's own channel — a repaired
            # channel is still that agent's, and prefixing there would train the human to skip the
            # first line of every reply in a conversation they are already inside.
            #
            # `attributed` is the caller saying the body already names its agent on a line of its
            # own — an approval, whose attribution TG-85(a) requires whatever the channel, and which
            # carries the derived thread id this generic prefix cannot. Adding one on top printed
            # the agent id as line 1 and again as line 2, on the single message in this layer that
            # carries an irreversible button and whose first lines are the only attribution a
            # notification preview shows. One line, moved, not repeated.
            exposed = target.is_general and not channel.is_general
            body = (
                text if attributed else _prefixed(text, agent_id) if (prefix or exposed) else text
            )
            parts = split_message(body)
            dead = False
            for position, part in enumerate(parts):
                label = counter(position, len(parts))
                last = position == len(parts) - 1
                markup = keyboard if last else None
                sent: Mapping[str, Any]
                try:
                    # Every free variable is bound explicitly: `target` is reassigned by the outer
                    # loop on a repair, and a lambda closing over it would re-send into whichever
                    # channel it had become by the time a retry fired — which is the dead one.
                    sent = await with_retry(
                        lambda part=part, label=label, markup=markup, to=target: self._api_send(
                            to, label + part, markup
                        )
                    )
                except TelegramError as exc:
                    if exc.is_missing_thread and not target.is_general:
                        # TG-83: `400 message thread not found` is the same fact as a TG-80
                        # mismatch — Bot API 10.0 reportedly answers this where 9.3 silently
                        # relocated (F-3, tdlib#847), and both mean the topic is gone. Never
                        # retryable: retrying re-issues the same dead id to the retry bound, three
                        # 400s per message forever. Nothing was delivered, so there is no stray
                        # message to disarm and no correction to post.
                        await self._channel_died(target, agent_id, stray=None, armed=False)
                        dead = True
                        break
                    self._note_send_failed(exc)
                    raise
                except Exception as exc:
                    # TG-13: reported, never `degraded`. A send failure is not the subsystem being
                    # down, and a 503 here invites the restart D9 forbids.
                    self._note_send_failed(exc)
                    raise
                if not target.is_general and _landed(sent) != target.topic_id:
                    await self._channel_died(target, agent_id, stray=sent, armed=markup is not None)
                    dead = True
                    break
                if last:
                    return sent
            if not dead:
                break
        return {}

    async def _api_send(
        self, target: Channel, text: str, keyboard: list[list[dict[str, str]]] | None
    ) -> Mapping[str, Any]:
        """The one place ``message_thread_id`` leaves this process (TG-75).

        A General send is made **without** the parameter rather than with a zero, so a deployment
        that never turns Threaded Mode on emits byte-identical requests to the pre-topics build —
        which is TG-75's migration guarantee, and the reason a whole test file can re-run the old
        suite with the flag off and assert that no payload anywhere carries a thread id.
        """
        if target.is_general:
            return await self.api.send_message(target.chat_id, text, keyboard=keyboard)
        return await self.api.send_message(
            target.chat_id, text, keyboard=keyboard, topic_id=target.topic_id
        )

    # -- the deleted-topic hazard (TG-80 … TG-84) -------------------------------------

    def _route_out(self, channel: Channel, agent_id: str | None) -> Channel:
        """Where a message for this channel actually goes **now** (TG-84, TG-75).

        Three re-addressings, all of them refusals to send to an id known to be dead: topic mode is
        off, so nothing may carry a thread id at all; the agent is retired, so its traffic goes to
        General permanently (TG-82); or this exact channel died and was repaired, so the queued
        frame addressed to the old id follows it (TG-84). A queued item is **re-addressed, never
        dropped and never sent blind** — TG-80 detects one stray per send, and without this a
        fan-out with eight queued frames produces eight strays and eight corrections at exactly the
        moment something needs approving.
        """
        if not self._topics:
            return channel.general
        if agent_id is not None and (channel.chat_id, agent_id) in self._retired:
            return channel.general
        # `visited`, not `seen`: SS-13 forbids any identifier in `pkb.server` containing "seen" or
        # "dedup", because on the event path such a name is a second deduplication pass by
        # definition. This one is a cycle guard on a re-addressing chain and has nothing to do with
        # events — the scan is structural on purpose, so the name moves rather than the rule.
        visited: set[Channel] = set()
        while channel in self._moved and channel not in visited:
            visited.add(channel)
            channel = self._moved[channel]
        return channel

    async def _channel_died(
        self,
        channel: Channel,
        agent_id: str | None,
        *,
        stray: Mapping[str, Any] | None,
        armed: bool,
    ) -> None:
        """The topic is gone. **Disarm first, explain second, repair third** (TG-81, decision AC).

        The order is the rule. A message is dangerous only while its buttons are live, and the very
        response that revealed the problem carries the ``message_id`` needed to kill them — so
        clearing the keyboard costs one call that cannot fail for want of information. Repairing
        first means a ``createForumTopic`` failure leaves an Approve button for an irreversible
        write sitting in General under the wrong expert's name, which is the exact failure this
        whole section is arranged around.

        The stray's **text is never deleted** (decision AD) and ``delete_message`` is not on the
        Protocol. The chat is the only surviving record of what the human was asked, on a system
        with no undo; a message that vanishes tells them nothing and reads as a bug in the bot,
        while one with dead buttons and a correction under it tells them exactly what happened.

        **The disarm is per message; only the repair is per channel** (TG-81 over TG-84). ``known``
        says another frame already discovered this death and re-addressed the channel — which is not
        a reason to leave *this* stray armed. Two sends overlap on the ordinary path, not on an
        exotic one: :meth:`_pump_outbox` is a separate task and a run can emit a queued message from
        it while ``_consume`` is sending the reply concurrently on the same channel. Returning early
        for a channel already known dead left a still-armed message (once, an Approve button) live in
        General under the wrong expert's name — the exact failure this section is arranged around,
        reached through the one branch written to keep the *corrections* from multiplying.

        **The repair is serialized per channel, and the check is inside the lock** (TG-82, TG-84).
        ``known`` is a check-then-act on a value only :meth:`_repair` writes, and it writes it after
        two awaits — so two frames that discovered the same deletion both read ``False``, both
        called ``createForumTopic``, and one deletion produced *two* topics. Measured: the directory
        named the second and ``recreations`` was already **2**, so the very next deletion retired a
        channel the human had deleted once; the first new topic stood on their phone carrying the
        expert's title, holding the reply that was re-sent into it, addressed by nothing and
        answering every message with TG-74's "not connected to an expert"; and ``_carry_binding``
        carried the conversation into it and then found nothing left to carry into the topic the
        directory actually named — the amnesiac bot of decision S, produced by the repair itself.
        The disarm stays **outside** the lock: it is per message, it needs nothing but the response
        in hand, and waiting for another frame's ``createForumTopic`` before killing a live Approve
        button is the "repair first" ordering TG-81 forbids.
        """
        if agent_id is None and not channel.is_general:
            # The caller did not know whose channel this was; the directory does, and it is the same
            # read the routing path already makes. Without it the messages **most likely** to find a
            # deleted topic cannot repair the channel they died in: the orphan report (TG-29) and
            # every other startup notice are sent unattributed, into the channel that lost the
            # message, before anything else the bot does — which is exactly when a topic deleted
            # during the outage is discovered. `_repair` would then pin the channel to General,
            # performing zero of TG-82's two permitted recreations and blocking the later attributed
            # sends that could have performed them.
            agent_id = (await self._directory(channel.chat_id)).get(channel.topic_id)
        if stray is not None and armed:
            message_id = stray.get("message_id")
            if isinstance(message_id, int):
                # TG-90: by chat and message id, with **no** topic — the send family is the only
                # family that takes one.
                with contextlib.suppress(TelegramError):
                    await self.api.clear_keyboard(channel.chat_id, message_id)
        async with self._repair_lock(channel):
            known = channel in self._moved
            if stray is not None and (armed or not known):
                # A plain stray on an already-known-dead channel is the eighth frame of a fan-out
                # and gets no ninth correction (TG-84). An **armed** one always does: a message
                # whose buttons were just killed, sitting in General with no line under it saying
                # why, is a human pressing Approve on a write with no undo and learning nothing.
                await self._plain(
                    channel.general,
                    _STRAY.format(
                        agent_id=agent_id or "an expert", armed="" if not armed else _DISARMED
                    ),
                )
            if known:
                return  # repaired by a concurrent frame; one repair, not eight (TG-84)
            _log.warning(
                "telegram: topic %s in chat %s is gone; %s traffic is being re-addressed",
                channel.topic_id,
                channel.chat_id,
                agent_id or "its",
            )
            await self._repair(channel, agent_id)

    def _repair_lock(self, channel: Channel) -> asyncio.Lock:
        """One repair at a time per channel (TG-82, TG-84).

        Separate from :meth:`_channel_lock`, which serializes *turns*: a repair runs inside a send,
        and a send happens on the outbox pump and on the ``_recover`` task as well as inside a turn,
        so borrowing the turn lock here would both miss those callers and deadlock the ones it
        caught. Bounded by the same thing the turn locks are — a channel only exists because a
        ``/channels`` command made one.
        """
        lock = self._repairs.get(channel)
        if lock is None:
            lock = self._repairs[channel] = asyncio.Lock()
        return lock

    async def _repair(self, channel: Channel, agent_id: str | None) -> None:
        """Recreate the channel at most twice, then retire it (TG-82).

        Unbounded recreation is a loop against a human deliberately deleting a topic, and each turn
        of it costs a ``createForumTopic`` and a notification. Refusing to repair at all is worse:
        the expert's approvals become undeliverable, which is precisely the outcome Q20 rejected.
        Two is the smallest bound that survives an accidental deletion and a fat-fingered second one
        without becoming a fight.

        The count is durable — ``rebind_channel`` returns it — so a restart between deletions does
        not reset it. A create that cannot be attributed to an agent, or one this deployment has no
        topic mode for, falls back to General rather than inventing a channel.
        """
        if agent_id is None or not self._topics:
            self._moved[channel] = channel.general
            return
        row = await self.store.channel(channel.chat_id, agent_id)
        if row is not None and int(row["topic_id"]) != channel.topic_id:
            # **The directory already moved this channel, so there is nothing to repair** (TG-84).
            # Every durable row that names a topic keeps naming the dead one after a repair — a
            # prompt row (TG-57), a ledger row (TG-29/TG-31) — and `_moved` is process memory a
            # supervised restart does not carry, so the ordinary way to arrive here with a stale
            # channel is a bounce, which is also the moment TG-31 re-posts approval keyboards.
            # Creating instead would abandon the live topic the human is sitting in for a second
            # one, split that expert's history across both, and spend one of TG-82's two
            # recreations on a channel that never needed repairing.
            if row["retired"]:
                await self._retire(channel, agent_id)
            else:
                self._moved[channel] = Channel(channel.chat_id, int(row["topic_id"]))
            return
        if row is not None and (row["retired"] or int(row["recreations"]) >= MAX_RECREATIONS):
            # Checked **before** the create, from the durable row, which is what makes TG-82's "no
            # further createForumTopic is issued" true rather than approximately true. An in-memory
            # count survives exactly as long as the daemon's uptime, and a supervised restart
            # carries nothing across — so a human deleting a topic in anger would get a fresh
            # allowance of two every time the bot bounced.
            await self._retire(channel, agent_id)
            return
        try:
            created = await with_retry(
                lambda: self.api.create_forum_topic(
                    channel.chat_id, self._catalog().get(agent_id, agent_id)
                )
            )
        except TelegramError as exc:
            self._note_send_failed(exc)
            _log.warning("telegram: could not recreate a topic for %s: %s", agent_id, exc)
            self._moved[channel] = channel.general
            return
        topic_id = _created_topic_id(created)
        if not topic_id:
            self._moved[channel] = channel.general
            return
        count = await self.store.rebind_channel(channel.chat_id, agent_id, topic_id)
        if count == 0:
            # No directory row at all — this channel came from somewhere else (General's mapping,
            # or a directory lost with its SQLite file). There is nothing to count against and
            # nothing to retire, so the new topic is used and the repair simply is not bounded by a
            # row that does not exist.
            await self.store.open_channel(channel.chat_id, topic_id, agent_id)
        await self._carry_binding(channel, topic_id)
        self._moved[channel] = Channel(channel.chat_id, topic_id)
        self._retired.pop((channel.chat_id, agent_id), None)

    async def _carry_binding(self, channel: Channel, topic_id: int) -> None:
        """A recreation moves the channel; the conversation has to move with it (TG-82, decision S).

        A repair is not a rotation. Nothing about it is the human asking for a fresh start, and the
        message being re-sent into the new topic is frequently an approval keyboard for a write
        still parked on the old thread. The binding is keyed on ``(chat_id, topic_id)``, so left
        where it is it belongs to a topic that no longer exists: the human's next message in the
        recreated topic opens a **new** thread while the old one holds the approval they were just
        shown, and the old thread is reachable from no channel on the phone — ``/threads`` is a
        read-only listing (TG-40 amended) and ``/cancel`` reads the new topic's binding. That is
        decision S's amnesiac bot, produced by the repair itself.

        Bind first, unbind second: a crash between the two leaves the thread reachable from both the
        dead topic and the live one, which the next inbound message resolves. The other order leaves
        it reachable from neither, and there is no undo (D6).
        """
        carried = await self.store.binding(channel.chat_id, channel.topic_id)
        if carried is None:
            return
        thread_id, owner = carried
        await self.store.bind(channel.chat_id, topic_id, thread_id, owner)
        await self.store.unbind(channel.chat_id, channel.topic_id)

    async def _retire(self, channel: Channel, agent_id: str) -> None:
        """Past the bound: General, with a prefix, permanently, and the human is told **once**.

        Told once because a notice per deletion is a notification loop, and the human is deleting
        the topic on purpose by this point. Durable (``retire_channel``) for the same reason the
        count is: a flag a restart clears is a notice the human gets again every time the daemon
        bounces, which is TG-13's exact lesson. The Telegram topic itself is left standing (TG-78) —
        it holds their record of what they approved there — and ``/channels <agent-id>`` revives the
        channel with a fresh allowance, because a human typing that has conceded the fight.
        """
        # The re-addressing is written **before** the "told once" guard, not after it. A second dead
        # topic for an already-retired agent still has to stop being sent to, and an early return
        # that skipped this left the channel absent from `_moved`, so the next frame addressed to it
        # strayed into General all over again — one more stray, one more disarm, forever.
        already = (channel.chat_id, agent_id) in self._retired
        self._retired[channel.chat_id, agent_id] = channel
        self._moved[channel] = channel.general
        if already:
            return
        await self.store.retire_channel(channel.chat_id, agent_id)
        if self.health is not None:
            with contextlib.suppress(AttributeError):
                existing = tuple(self.health.retired_channels)
                self.health.retired_channels = tuple(sorted({*existing, agent_id}))
        await self._plain(channel.general, _RETIRED_NOTICE.format(agent_id=agent_id))

    async def _plain(self, channel: Channel, text: str) -> None:
        """One unchecked message into General — the correction and the retirement notice.

        Unchecked because General cannot be deleted: it is the part of a private chat that carries
        no ``message_thread_id`` at all, so there is no id to go stale and nothing to compare. It
        also has to bypass :meth:`_send`, which is in the middle of handling the failure that
        produced this text.
        """
        try:
            # `len(parts)`, not `1` (TG-45). `counter(position, 1)` is the empty string for every
            # position, so a text that split arrived as two unnumbered messages — with the units
            # `split_message` had already reserved for the label going unused. Both texts sent here
            # are short constants today, so this is a latent bug rather than an observed one; it is
            # fixed rather than argued away because the only thing keeping it latent is the length
            # of a sentence somebody may lengthen.
            parts = split_message(text)
            for position, part in enumerate(parts):
                await self.api.send_message(
                    channel.chat_id, counter(position, len(parts)) + part, keyboard=None
                )
        except TelegramError as exc:
            self._note_send_failed(exc)
            _log.warning(
                "telegram: could not post a correction to chat %s: %s", channel.chat_id, exc
            )

    async def _pump_outbox(self) -> None:
        """Drain the outbox. One failed send must not stop the bot — but it must not be silent.

        ``_send`` has already put the reason on ``/health`` (TG-13); the log line is what names the
        **channel** and the fact that a specific message was lost, which ``last_send_error`` cannot
        (TG-48). A reply that never arrived otherwise looks exactly like a turn that never ran.

        One queue per **chat's worth of traffic**, not one per topic (TG-94). Telegram's roughly
        one-message-per-second budget is a chat-level limit and topics are subdivisions of one chat,
        so N channels do not buy N times the budget: a pump per topic buys nothing and earns 429s
        whose ``retry_after`` stalls the approval messages too.
        """
        while True:
            channel, text, agent_id = await self._outbox.get()
            try:
                await self._send(channel, text, None, agent_id=agent_id)
            except TelegramError as exc:
                _log.warning(
                    "telegram: a message to %s was not delivered and is lost (%s)",
                    channel,
                    exc,
                )

    async def _recover(self) -> None:
        """What a restart owes each chat: a named loss, or the outcome it never heard (TG-29, TG-31).

        Two ledger states, two different debts, and confusing them is what made both silent:

        * **claimed, never started** — the crash landed in the gap before ``start_run``, so nothing
          ran and nothing was written. The chat is told, by name, to re-send. It is never retried:
          a repeated turn is a repeated write into a tree with no undo, and the second run's
          content differs from the first, so the human ends up with two versions of a note they
          wrote once. A named loss is survivable; a duplicated write is not.
        * **started, never finished** — the agent ran and may already have written, so this must
          **re-sync, never replay** (TG-31). Two branches, in order: a run still live is reattached
          and only its outcome rendered, because ``attach`` replays from ``seq 0`` and re-rendering
          the replay double-posts text already in the chat; and once the hub is gone, the last
          assistant message is posted from ``ThreadDetail``, marked delivered late.

        A third branch used to come first here — a parked approval getting its keyboard back,
        including a fan-out gate on a child (TG-53) — and is gone along with the gates themselves: no
        thread can hold a pending decision any more (DESIGN.md §2.10), so there is nothing left to
        re-post on a restart.
        """
        await self._report_orphans()
        for update_id, chat_id, topic_id, thread_id in await self.store.unfinished():
            if chat_id is None or chat_id not in self.config.chats:
                continue
            channel = Channel(chat_id, topic_id)
            try:
                await self._resync(channel, thread_id)
            except Exception:
                _log.warning(
                    "telegram: could not re-sync %s on thread %s after a restart",
                    channel,
                    thread_id,
                    exc_info=True,
                )
            await self.store.dispatched(update_id)

    async def _resync(self, channel: Channel, thread_id: str) -> None:
        """One channel's unfinished turn, re-synced (TG-31). Never a re-run, never a ``cancel``.

        Into the channel the update came from (TG-31 amended): a restart that re-posted a reply into
        the wrong topic was TG-80's failure without anyone having deleted anything.
        """
        detail = await self.service.get_thread(thread_id)
        subscription = await self.service.attach(thread_id)
        if subscription is not None:
            # `detail.thread.agent_id`, not `detail.agent_id`: :class:`~pkb.service.ThreadDetail`
            # has no such attribute, so the `getattr` this replaced answered ``None`` on **every**
            # restart. That is not cosmetic under §9 — an unattributed frame is invisible to
            # `_route_out`'s retirement check (TG-82), carries no TG-85(b) prefix when it falls back
            # to General, and cannot repair the channel it dies in (TG-84), which is exactly the
            # path a restart takes: `_moved` is process memory and the ledger row still names the
            # topic that was deleted while the daemon was down.
            await self._consume(channel, _agent_of(detail), subscription, replay=True)  # branch (a)
            return
        await self._post_late_reply(channel, detail)  # branch (b)

    async def _post_late_reply(self, channel: Channel, detail: Any) -> None:
        """The reply the chat never got, marked as late (TG-31 branch (c)).

        ``attach`` returns ``None`` once the hub is closed, so a late restart cannot reach the
        reply through the supervisor at all — ``ThreadDetail.messages`` is the only place it still
        exists. Marked, because a reply arriving hours after the question reads as a non-sequitur.
        """
        last = next(
            (m.text for m in reversed(list(detail.messages)) if m.role == "assistant" and m.text),
            None,
        )
        if last:
            await self._queue(channel, _LATE + last, _agent_of(detail))

    async def _report_orphans(self) -> None:
        """Name what a crash lost, rather than retrying it or staying silent (decision T, TG-29).

        The notice goes to **the channel that lost the message**, with that channel's own count
        (TG-29 amended). It used to broadcast a total to every mapped chat whose id happened to be
        in the owner allow-list, which for the ordinary deployment is no chat at all — measured,
        zero notices for a real orphan, which is the silent loss the rule names as unacceptable.
        Reaching the right chat but the wrong topic is that same defect one level down: a human
        told in General that a message was lost has no way to know which expert never heard it.
        """
        lost = await self.store.orphans()
        if not lost:
            return
        _log.warning("telegram: %d update(s) were claimed but never started", len(lost))
        counts: dict[Channel, int] = {}
        for _update_id, chat_id, topic_id in lost:
            if chat_id is not None and chat_id in self.config.chats:
                where = Channel(chat_id, topic_id)
                counts[where] = counts.get(where, 0) + 1
        for where, count in sorted(
            counts.items(), key=lambda item: (item[0].chat_id, item[0].topic_id)
        ):
            await self._announce(where, _ORPHANS.format(count=count))
        for update_id, _chat_id, _topic_id in lost:
            # Reported once. A row left claimed would re-announce the same loss on every restart,
            # which is how a human learns to ignore the notice that matters.
            await self.store.dispatched(update_id)


def _detach(subscription: Any) -> None:
    """Give back the subscriber slot — **synchronously**, on every path (TG-52, C-31).

    ``RunSubscription.close`` is documented as "an awaitable that unsubscribes" and is a plain
    function whose real docstring says *"Detach. Never cancels the run"*. Awaiting it raises
    ``TypeError``, and it would raise inside a ``finally`` during teardown, where it gets reported
    as a shutdown bug. The ``callable`` hedge is the idiom the routes and MCP already use.
    """
    close = getattr(subscription, "close", None)
    if callable(close):
        close()


def _agent_of(detail: Any) -> str | None:
    """Whose conversation this is, read off ``ThreadDetail.thread`` (TG-85, TG-82).

    ``ThreadDetail`` carries the :class:`~pkb.service.Thread`, not the agent id — a ``getattr`` on
    the detail itself answers ``None`` for every thread that has ever existed, which is how the
    re-sync path shipped every restarted run as an unattributed frame. Tolerant of a detail that is
    not a ``ThreadDetail`` because both callers are on the restart path, where raising would take
    the whole re-sync down for one malformed row.
    """
    agent_id = getattr(getattr(detail, "thread", None), "agent_id", None)
    return str(agent_id) if agent_id else None


def _channel_of_message(message: Mapping[str, Any]) -> Channel:
    """The channel a message was sent in (TG-72).

    ``message_thread_id`` is absent in General and is a topic id everywhere else (F-1). It is read
    on **every** inbound message, whatever ``has_topics_enabled`` said at startup: an inbound thread
    id is proof that a topic exists, and treating it as General because a probe failed is the
    mis-file TG-1 exists to prevent, with no configuration change to blame.
    """
    chat_id = int((message.get("chat") or {}).get("id", 0))
    return Channel(chat_id, int(message.get("message_thread_id") or GENERAL))


def _channel_of_query(query: Mapping[str, Any]) -> Channel | None:
    """Where a button press came from — a ``callback_query`` carries its whole message (F-1)."""
    message = query.get("message") or {}
    chat = message.get("chat") or {}
    if "id" not in chat:
        return None
    return Channel(int(chat["id"]), int(message.get("message_thread_id") or GENERAL))


def _channel_of(update: Mapping[str, Any]) -> Channel | None:
    for key in ("message", "edited_message"):
        if key in update:
            return _channel_of_message(update[key])
    if "callback_query" in update:
        return _channel_of_query(update["callback_query"])
    return None


def _created_topic_id(created: Mapping[str, Any] | Any) -> int:
    """The id of a topic ``createForumTopic`` just made, or ``0`` if the answer is unusable.

    A ``ForumTopic`` carries its id as ``message_thread_id`` — the same field name the send family
    echoes, which is not a coincidence: a topic id *is* the id of the message that opened it.
    ``0`` rather than an exception because both callers already have a "this did not work" branch
    that tells the human, and a creation path that raises would restart the bot for a malformed
    response to a command somebody typed.
    """
    if not isinstance(created, Mapping):
        return GENERAL
    topic_id = created.get("message_thread_id")
    return topic_id if isinstance(topic_id, int) else GENERAL


def _landed(sent: Mapping[str, Any] | Any) -> int:
    """Where a send actually went (TG-80), tolerating a response that is not a mapping.

    The arithmetic itself is ``telegram_api.landed_topic_id``, beside the wire format it reads —
    absence means General, which is exactly what a stray send looks like, and a caller comparing
    ``response.get("message_thread_id")`` directly gets the General case wrong because ``None != 0``.
    This wrapper only adds the fake-and-future-proofing: a response this module cannot read is
    treated as General, which routes it into TG-81's disarm-and-repair rather than into a crash on
    the path that is handling a failure.
    """
    return landed_topic_id(sent) if isinstance(sent, Mapping) else GENERAL


def _prefixed(text: str, agent_id: str | None) -> str:
    """The agent id as a first line, for a message delivered outside its agent's channel (TG-85).

    Attribution follows **exposure**, not the channel. A topic header attributes a message only
    while you are inside the topic: scrollback in General, a forward, a lock-screen notification
    preview and TG-82's retired-to-General fallback all strip the header, and none of them strips a
    first line. The inverse is why an ordinary reply inside its own channel carries nothing —
    prefixing every reply in a conversation the human is already in trains them to skip the first
    line, which is exactly where the approval attribution has to be legible.
    """
    return f"{agent_id}\n{text}" if agent_id else text


def _is_empty(message: Mapping[str, Any]) -> bool:
    """A message with no text and no attachment was not sent by a human (TG-92).

    TG-36's refusal exists to tell a human that the file they sent was not downloaded or stored.
    Answering Telegram's own bookkeeping with it means the human's first act after enabling Threaded
    Mode — creating a topic — is met with an apology about a photo they did not send.

    **A caption is a human typing**, and it is asked about because :data:`_MEDIA_KEYS` is a list and
    a list is never finished: Telegram keeps adding media kinds, and one that is not on it turns
    TG-36's refusal into silence — the attachment vanishes with no reply at all, which reads as a bot
    that is down rather than as a bot that does not take files. Telegram's own bookkeeping carries no
    caption, and :data:`_SERVICE_KEYS` is matched ahead of this function either way, so admitting a
    captioned message admits nothing of Telegram's.
    """
    text = message.get("text")
    if isinstance(text, str) and text.strip():
        return False
    caption = message.get("caption")
    if isinstance(caption, str) and caption.strip():
        return False
    return not (_MEDIA_KEYS & set(message))


def _threads_text(threads: Sequence[Any]) -> str:
    """Server order, verbatim — pending first, then most recent (TG-40)."""
    if not threads:
        return "No conversations here yet."
    rows = [
        f"{'● ' if t.pending_interrupt_id else '· '}{t.title or 'untitled'}  [{t.thread_id}]"
        for t in threads
    ]
    return "Your conversations, most in need of attention first:\n" + "\n".join(rows)


_AGENT_LIST_CAP: Final = 40
"""How many agent ids one reply lists (TG-74, TG-87).

A knowledge base with three hundred topics would otherwise put three hundred lines on a phone
screen, split across four messages, to answer "which of these has no channel?". The cap is generous
because the reply is only ever sent to an owner, and a truncated list is still a usable one.
"""

_UNMAPPED: Final = (
    "This chat is not connected to anything, so I have not kept this message and nothing has been "
    "filed.\n\nTo connect it, add chat {chat_id} to the daemon's Telegram configuration."
)
_UNBOUND_TOPIC: Final = (
    "This topic is not connected to an expert yet, so I have not kept this message and nothing has "
    "been filed.\n\nSend /channels <agent-id> here to make this topic that expert's channel."
)
_AGENT_GONE: Final = (
    "This topic's expert ({agent_id}) is no longer in the knowledge base, so I have not kept this "
    "message and nothing has been filed. I have left the topic and everything in it alone."
)
_NO_TOPICS: Final = (
    "This bot does not have topics turned on, so there is one channel here and it is this one. "
    "Turn on Threaded Mode for this bot in BotFather to get a channel per expert."
)
_NO_SUCH_AGENT: Final = (
    "There is no agent called {agent_id}, so nothing was created.\n"
    "Send /agents to see what there is."
)
_ALREADY: Final = "{agent_id} already has a channel in this chat ({where}), so I created nothing."
_ALREADY_HERE: Final = (
    "{agent_id} already has a channel in this chat, and this is it. I created nothing."
)
"""TG-77 amended: the pointer, delivered **into** the channel it names (TG-102, decision AI).

On a live channel this is the useful half of the answer, because it points at a place the human can
open rather than at a topic id every Telegram client hides. On a dead one the send raises
``message thread not found``, which is TG-83's trigger, so the repair that was already built runs.
"""

_REOPENED: Final = (
    "{agent_id}'s topic had been deleted, so I made a new one, {title}. Everything for that expert "
    "goes there from now on."
)
"""The typing channel's answer when the pointer found the topic gone and TG-83 repaired it.

The human has to be told a new topic exists. Without this line the tap that recovered their expert
looks exactly like the tap that found it healthy, while their chat quietly gained a topic.
"""

_POINTER_LOST: Final = (
    "{agent_id}'s topic had been deleted and I could not make a new one, so that expert's messages "
    "are arriving in this chat's General for now. Send /channels {agent_id} to try again."
)
"""The typing channel's answer when the recreation itself failed and TG-82 fell back to General."""

_BOUND: Final = (
    "Bound this topic to {agent_id} and named it {title}. Nothing was created. Anything you send "
    "here from now on goes to that expert."
)
"""TG-87's bind, and the name TG-105 gives the topic in the same breath.

The title is stated because the bot changed it. A human who made this topic in their own client saw
Telegram call it *New Chat*, and a rename they learn about by noticing it later is a rename they
read as their client misbehaving. Renaming it back is theirs to do and the bot leaves it alone from
here (TG-78 amended).
"""

_BOUND_UNNAMED: Final = (
    "Bound this topic to {agent_id}. Anything you send here from now on goes to that expert.\n"
    "I could not name it {title}: {reason}\n"
    "The binding stands. You can rename the topic yourself."
)
"""TG-106: the bind succeeded and the rename did not, and the reply says both.

The durable write runs first, so this is a channel that works. Reporting the rename as done would
leave the human reading *New Chat* under a line claiming the topic is called *Cooking*, and the next
thing they doubt is the binding.
"""
_ALL_AGENTS: Final = "all"
"""``/channels all``'s one argument, and a word no agent id can be (§9.13.5).

Named rather than written twice, so the branch :meth:`TelegramAdapter._on_picker` refuses is the
same branch :meth:`TelegramAdapter._channels` runs (TG-101).
"""

_PICKER_HEADER: Final = (
    "Tap an expert to give it a channel in this chat. A ✓ marks one that has a channel here."
)
_PICKER_EMPTY: Final = (
    "This knowledge base has no agents, so there is no channel to open. Add a topic to the tree and "
    "its expert appears here."
)
_PICKER_MORE: Final = (
    "{count} more agent(s) did not fit these buttons. Send /channels <agent-id> to reach one of "
    "them, or /channels all to give a channel to every agent without one."
)
_PICKER_UNMAPPED: Final = (
    "This chat is no longer connected to the knowledge base. Nothing was made."
)
_PICKER_UNKNOWN: Final = (
    "That button names an expert I cannot find in the catalog. Nothing was made."
)
_CREATED: Final = "Created a new topic, {title}, for {agent_id}. Send it anything from there."
_CREATE_FAILED: Final = (
    "I could not create a topic for {agent_id}: {reason}\nNothing was created and nothing changed."
)
_DISARMED: Final = " Its buttons no longer work, so nothing can be done from it."
_STRAY: Final = (
    "The topic for {agent_id} has been deleted, so the message above this one was delivered here "
    "instead of there — Telegram accepted it without an error.{armed}\n\n"
    "I am re-sending it where it belongs."
)
_RETIRED_NOTICE: Final = (
    "The topic for {agent_id} has been deleted more than twice, so I have stopped making new ones. "
    "Everything from that expert will arrive here, with its name on the first line, until you send "
    "/channels {agent_id} to give it a channel again."
)
_ATTACHMENT: Final = "I can only read text — I have not downloaded or stored the attachment."
_EDITED: Final = "I already acted on the original — send the correction as a new message."
_PRESS_FAILED: Final = "I could not apply that: {reason}\nNothing was sent."
_REFUSED: Final = (
    "This knowledge base does not accept decisions from this account. Nothing was done."
)
_UNREADABLE: Final = "I cannot read that button any more. Open the TUI to answer this approval."
_CONFLICT_REASON: Final = (
    "another consumer of this bot token is polling — either a second daemon is running against it, "
    "or a poller from a previous generation of this task is still alive. Polling is stopped; "
    "restarting this one would add a third."
)
_COLD_START: Final = (
    "I have just started with no record of this chat, so anything sent while I was down was not "
    "filed and has been discarded. Please re-send whatever you were expecting a reply to."
)
_UNKNOWN: Final = (
    "The connection to the run ended before it did, so I do not know how it finished. "
    "I have re-read the conversation; check it before sending anything else."
)
_BUSY: Final = (
    "Still finishing your last message — send this again in a moment. It was not sent:\n\n{text}"
)
_PENDING_BLOCKS: Final = (
    "There is an approval waiting on this conversation, so this was not sent:\n\n{text}"
)
_ORPHANS: Final = (
    "I restarted before {count} of your message(s) reached an expert, so nothing ran and nothing "
    "was filed. Nothing was retried — please send them again."
)
_LATE: Final = "This reply was finished while I was restarting, so it is arriving late:\n\n"
_REMAPPED: Final = (
    "This chat now talks to {agent_id}, so I have started a new conversation here. The previous "
    "one is still in the thread list of the expert it belonged to."
)
_TERMINAL: Final = {
    "interrupted": "Waiting on your decision above.",
    "cancelled": "Cancelled.",
    "error": "That run failed. Nothing further was sent.",
    "completed": "",
}
