"""The Telegram adapter — a channel per expert, inside the daemon (TG-1 … TG-95).

D9: a supervised background task calling :class:`~pkb.service.PkbService` **directly**. No HTTP
round trip to our own daemon, no second process, no auth boundary between them. Layer 3 built the
slot; this is what goes in it.

**No HTTP import lives here.** The Bot API is reached through the :class:`~pkb.server.telegram_api.
BotApi` Protocol, whose only implementation is the sibling module ``telegram_api.py``. That split
is what lets every rule below be driven against a fake with no token and no socket — and a *sibling*
rather than a subpackage because five built seam scans glob non-recursively, so code inside a
``telegram/`` package would be invisible to them.

Four properties shape the whole module:

* **Structured concurrency, because the supervisor has no handle on what a task spawns.** Measured
  against the real ``_supervise``: a task that detaches a child and raises leaves it running and
  gets a second on restart — three restarts gave three live pollers, which Telegram answers with
  ``409 Conflict`` because it permits one ``getUpdates`` per token. Everything here runs in one
  :class:`asyncio.TaskGroup`, so a crash takes its children with it.
* **Nothing is remembered in the process.** The chat's current thread, the update ledger and every
  open approval live in SQLite. A button pressed while the daemon was down arrives up to 24 hours
  later into an adapter with no memory of sending it, and the durable path is the *only* path — so
  the restart case is exercised by every test rather than by an incident.
* **The pump never blocks on a Bot API call** (TG-49). Frames drain into a bounded outbox that a
  separate task sends from. ``RunHub`` drops a subscriber whose queue exceeds 256 and the drop
  closes the stream *without a terminal frame*, so one ``429`` with ``retry_after: 30`` inside the
  pump would lose the approval the human is waiting for and make it look like an unknown outcome.
* **An approval is decided against the whole thing.** The complete description reaches the chat
  before the buttons do — as a document when it does not fit, because a real delete embeds the whole
  current file and runs to ~8,000 characters against a 4,096-unit limit. Truncating puts bullets
  60-119 behind an irreversible approve button.

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
import logging
import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from pkb.clients.approval import Answer, is_diff, offered, resolve, truncate
from pkb.contracts import (
    ApprovalPendingError,
    ApprovalRequest,
    DecisionType,
    InterruptEvent,
    MessageComplete,
    RunEnd,
    RunError,
    StaleInterruptError,
    SubagentEnd,
    SubagentStart,
    ThreadBusyError,
    librarian_thread_id,
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
    from pkb.contracts import ActionView
    from pkb.service import PkbService
    from pkb.service.telegram import TelegramStore

__all__ = [
    "COMMANDS",
    "Channel",
    "TelegramAdapter",
    "TelegramConfig",
    "callback_data",
    "counter",
    "keyboard_for",
    "split_message",
    "utf16_len",
]

_log = logging.getLogger(__name__)

COMMANDS: Final = ("/new", "/threads", "/agents", "/pending", "/cancel", "/channels")
"""The whole command surface, six of them, **each acting on the channel it was typed in** (TG-86).

**No ``/connect`` and no ``/talk``** (decision AF). A channel is bound to its agent by the topic the
human is typing in — visible above the keyboard at the moment they hit send — and the ambiguity
``/connect`` created is what made a mis-sent note land in the wrong topic. An in-band agent selector
would restore that hidden mode under a new name; a topic title cannot be invisible.
"""

NO_UNDO_REASONS: Final = frozenset({"delete", "topic-creation", "conflict-resolution"})
"""Reasons that take a second tap and carry the no-undo line (TG-64).

On a phone two buttons in one row *are* neighbouring keys, and a thumb on a moving train is a worse
input device than a keyboard.
"""

_DROPPED: Final[tuple[DecisionType, ...]] = ("edit", "respond")
"""What this channel narrows away (CL-9, TG-54). Narrowing is legitimate; widening is not.

``edit`` because editing a document on a phone is impractical (arch §6), and ``respond`` because
``validate_decisions`` **requires** a message on it — *"it becomes the tool's result"* — while
TG-65 forbids this channel from demanding prose from a phone. A ``Respond`` button that cannot
produce a valid decision is precisely the button TG-54 exists to stop being drawn: it would be
refused by ``validate_decisions`` rather than by Telegram, but the human's experience is the same
dead approval either way. Diverges from TG-54's parenthetical ``drop=("edit",)`` and agrees with
arch §6's "narrows to approve/reject"; no shipped ``GATE_DECISIONS`` row offers ``respond``, and
Q21(b) — a rejection with a typed reason — is explicitly deferred. An action left with nothing
offerable becomes TG-55's hand-off, which names the thread and the TUI.
"""

_HANDLE_BYTES: Final = 4
_VERSION: Final = "v1"
_APPROVE, _REJECT, _CONFIRM = "a", "r", "c"
_CANCEL: Final = "x"
"""The confirm step's *"Cancel"*. Deliberately **not** a member of :data:`VERBS` (TG-64)."""

VERBS: Final[Mapping[str, DecisionType]] = {_APPROVE: "approve", _REJECT: "reject"}
"""One button verb → one :class:`~pkb.contracts.DecisionType`, read in **both** directions (TG-54).

:func:`keyboard_for` draws from this and :meth:`TelegramAdapter._resolve` reads back through it, so
a verb the keyboard can emit is exactly a decision the resolver can build, and a verb it cannot is
refused rather than converted. Before it existed the two halves disagreed: the keyboard drew a
third button for ``respond`` while the resolver was ``"approve" if verb == "a" else "reject"``, so
pressing *Respond* submitted a **reject** — ``validate_decisions`` refused it, the ``finally``
marked the prompt resolved and cleared every keyboard, and the approval became unanswerable from
Telegram forever. The same silent conversion turned any verb from an older adapter version still
sitting in the chat into a rejection of a write the human never looked at.

Two entries rather than a hardcoded pair: this is the *table* both directions read, and
:data:`_DROPPED` is what decides which of the server's decisions reach it. Adding a third here is
one line and needs no change on either side.
"""

_VERB_FOR: Final[Mapping[DecisionType, str]] = {kind: verb for verb, kind in VERBS.items()}

_OUTCOME_WORDS: Final[Mapping[DecisionType, str]] = {"approve": "approved", "reject": "rejected"}
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
    message Telegram then refuses with ``message is too long`` — and the human sees *nothing*, not a
    short diff. The shared ``truncate`` stays character-based and channel-agnostic on purpose; the
    arithmetic that knows about Telegram lives here.
    """
    return len(text.encode("utf-16-le")) // 2


def fit(
    text: str, limit: int = MESSAGE_LIMIT, *, marker: str = "\n… (continues)"
) -> tuple[str, bool]:
    """Cut ``text`` to ``limit`` **UTF-16 units**, on a line boundary, and say whether it was cut.

    Searches down from a character budget rather than computing one: the ratio of characters to
    UTF-16 units depends on the content, so the only reliable method is to cut, measure, and cut
    again.

    ``marker`` is handed **through** to :func:`~pkb.clients.approval.truncate` (TG-56, decision U).
    That parameter was added to the shared helper for this one caller, because its default says
    *"open the TUI for the whole diff"* and under TG-56 the whole diff is in the same chat, one
    message up. The build shipped a ``removesuffix`` of a hand-copied literal instead, which is the
    per-channel drift ``truncate`` exists to prevent: reword ``TRUNCATION_MARKER`` and the strip
    silently stops matching, and the preview then tells the human to open a terminal to read what
    is on the screen above it — the exact outcome C-35 was written to stop, with no test able to
    see it because the rendered output is checked and the call is not.
    """
    if utf16_len(text) <= limit:
        return text, False
    budget = limit
    while budget > 0:
        candidate, _ = truncate(text, budget, marker=marker)
        if utf16_len(candidate) <= limit:
            return candidate, True
        budget = int(budget * 0.8)
    return text[: limit // 2] + marker, True


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


def callback_data(handle: str, index: int, verb: str) -> str:
    """``v1|<handle>|<index>|<verb>`` — and **nothing else** (TG-57).

    No thread id, no interrupt id, no chat id. The budget is **64 bytes** and a derived thread id
    alone is 60 characters, so a fan-out approval — the common case — would not fit. The handle is
    an opaque key into the durable prompts row, which is where the real state lives.

    Neither Telegram library enforces this at construction; it 400s at the server, which is to say
    at the moment a human is waiting for an approval.
    """
    data = f"{_VERSION}|{handle}|{index}|{verb}"
    if len(data.encode()) > CALLBACK_DATA_LIMIT:  # pragma: no cover - guarded by construction
        raise ValueError(f"callback_data is {len(data.encode())} bytes, over Telegram's 64")
    return data


def parse_callback(data: str) -> tuple[str, int, str] | None:
    parts = data.split("|")
    if len(parts) != 4 or parts[0] != _VERSION or not parts[3]:
        return None
    try:
        return parts[1], int(parts[2]), parts[3]
    except ValueError:
        return None


def keyboard_for(action: ActionView, handle: str, index: int) -> list[list[dict[str, str]]] | None:
    """The buttons for one action — from ``allowed_decisions``, never hardcoded (TG-54, TG-64).

    ``offered(action, drop=("edit",))`` because editing a document on a phone is impractical; it
    narrows and never widens, and it preserves the server's ordering, which decides which button a
    hurried human presses first.

    ``None`` when nothing is offerable: an empty keyboard reads as a delivery failure, while a
    message with no buttons at least reads as a hand-off (TG-55).

    Approve and reject go in **different rows** (TG-64) — on a phone, one row is one thumb.
    """
    kinds = [kind for kind in offered(action, drop=_DROPPED) if kind in _VERB_FOR]
    if not kinds:
        return None
    labels = {"approve": "Approve", "reject": "Reject", "respond": "Respond"}
    return [
        [
            {
                "text": labels.get(kind, kind.title()),
                "callback_data": callback_data(handle, index, _VERB_FOR[kind]),
            }
        ]
        for kind in kinds
    ]


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
        """Six commands, each acting on **the channel it was typed in** (TG-86).

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
        elif command == "/pending":
            await self._pending(channel)
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
        """
        if not self._topics:
            await self._say(channel, _NO_TOPICS)
            return
        chat_id = channel.chat_id
        if not arguments:
            await self._say(channel, await self._agents_text(channel, agent_id))
            return
        catalog = self._catalog()
        directory = await self._directory(chat_id)
        if arguments[0] == "all":
            wanted = [agent for agent in sorted(catalog) if agent not in set(directory.values())]
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
            await self._say(
                channel,
                _ALREADY.format(
                    agent_id=agent_id,
                    where="General" if existing.is_general else f"topic {existing.topic_id}",
                ),
            )
            return
        if not channel.is_general and channel.topic_id not in directory:
            await self.store.open_channel(chat_id, channel.topic_id, agent_id)
            self._revive(chat_id, agent_id, channel.topic_id)
            self._note_channel(agent_id)
            await self._say(channel, _BOUND.format(agent_id=agent_id))
            return
        await self._create_channel(channel, agent_id)

    async def _create_channel(self, where: Channel, agent_id: str) -> None:
        """One ``createForumTopic``, the agent's catalog title, and nothing else (TG-78).

        No ``icon_color`` and no ``icon_custom_emoji_id``: every parameter on this Protocol has to
        be implemented by every fake, and neither has a rule behind it. The binding is by topic
        **id**, so a human renaming the topic afterwards changes nothing — and the bot never renames,
        closes or deletes one, because the topic is the human's record of what they approved.

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
            # TG-37: neither rotate nor retry. RT-39 exists because sending to an interrupted thread
            # silently discards the interrupt — and on a phone the original keyboard has scrolled
            # away, so re-posting it is the only thing that makes the state resolvable from here.
            await self._say(channel, _PENDING_BLOCKS.format(text=text), agent_id=agent_id)
            await self._repost_pending(channel, thread_id)
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
                elif isinstance(event, InterruptEvent):
                    interrupted = True
                    # TG-89: the **originating** channel, not the expert's. Q20 was re-ruled once
                    # per-expert channels genuinely existed and re-affirmed with a new reason: under
                    # decision AA most agents have no channel at all, so routing a fan-out approval
                    # to the expert makes it undeliverable in the ordinary case — arch §8's headline
                    # failure with an approve button on it.
                    await self._post_approval(channel, event.request)
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

    # -- approvals --------------------------------------------------------------------

    async def _post_approval(
        self, channel: Channel, request: ApprovalRequest, *, prefixed: bool = False
    ) -> None:
        """One message per action, each carrying its own whole description (TG-56, TG-60).

        The channel is recorded on the prompt row (TG-57 unchanged, the *row* gains the topic), so a
        press that arrives after a restart still knows where the conversation was — ``callback_data``
        holds 64 bytes and a chat id was already refused at that budget, let alone a chat id and a
        topic id.
        """
        handle = secrets.token_hex(_HANDLE_BYTES)
        await self.store.open_prompt(
            handle,
            channel.chat_id,
            channel.topic_id,
            request.thread_id,
            request.interrupt_id,
            len(request.actions),
        )
        for index, action in enumerate(request.actions):
            await self._post_action(
                channel,
                handle,
                index,
                action,
                thread_id=request.thread_id,
                agent_id=request.agent_id,
                prefixed=prefixed,
            )

    async def _post_action(
        self,
        channel: Channel,
        handle: str,
        index: int,
        action: ActionView,
        *,
        thread_id: str,
        agent_id: str,
        prefixed: bool = False,
    ) -> None:
        # TG-85(b): an approval this channel will deliver into General — a retired channel, or one
        # whose repair failed — is *outside* its agent's channel just as surely as TG-88's fallback
        # is, and the attribution has to lead for the same reason. Asked here rather than left to
        # `_send` because only the caller can put the line somewhere other than the very top.
        prefixed = prefixed or self._exposed(channel, agent_id)
        description = action.description
        whole_text_present = True
        if utf16_len(description) > MESSAGE_LIMIT:
            # The whole thing arrives first, as a file. A delete embeds the entire current file and
            # a new-file write embeds the whole proposal; truncating hides half of it behind an
            # irreversible button.
            try:
                # TG-56: the upload is the *only* place the whole description exists in this chat,
                # so its failure is not cosmetic. The build swallowed it and went on to attach the
                # keyboard to a cut preview ending in "… (full text above)" — a marker that was now
                # a lie — putting an irreversible Approve button under bullets 0-30 of 300. A 413,
                # a 429 or a dropped tether is the first thing that goes wrong on a phone. A
                # description the human cannot see never carries a button; they get the hand-off
                # naming the thread instead.
                #
                # The returned `False` is the same fact reached the other way: the upload never
                # raised, it simply ran out of channels to try. It is not reachable at today's
                # constants and it is handled anyway, because what stands between here and a button
                # under a description that is not there is arithmetic in another module.
                whole_text_present = await self._send_document(
                    channel,
                    f"{action.tool}-{index}.diff"
                    if is_diff(description)
                    else f"{action.tool}-{index}.txt",
                    description.encode("utf-8"),
                    caption=f"{agent_id} · {action.tool} · {action.reason}",
                    agent_id=agent_id,
                )
                reason = "" if whole_text_present else "it never landed in a live channel"
            except TelegramError as exc:
                self._note_send_failed(exc)
                whole_text_present = False
                reason = str(exc)
            if not whole_text_present:
                _log.warning(
                    "telegram: the description for %s could not be shown in %s (%s); handing this "
                    "approval off to the TUI rather than showing a cut one",
                    action.tool,
                    channel,
                    reason,
                )
        else:
            await self._say(channel, description, agent_id=agent_id, prefix=prefixed)

        # TG-85(a) / defect 3: an approval names its agent, **always**, and names the derived thread
        # whenever there is one. Measured on the shipped build, this line was `tool · reason` and
        # nothing else — so a fan-out approval arrived as an unattributed diff with an Approve
        # button, which under topics is an Approve button in the Librarian's channel for a write
        # into Cooking, indistinguishable from the Librarian's own. Q20's own wording required the
        # naming ("the originating chat, naming the expert and the derived thread") and the build
        # never did it.
        #
        # Where it goes is the one place TG-66 and TG-85(b) genuinely compete, and each wins the
        # case it was written for. Inside the agent's own channel the topic title already says whose
        # this is, so the validation block keeps the first line: a human who cannot see that a draft
        # already fails validation approves it and burns one of three bounded attempts. **Outside**
        # it, the attribution is the first line, because that is what a notification preview, a
        # forward and General's scrollback all show — and being wrong about *which expert* there is
        # an irreversible write to the wrong topic. It is never both: one line, moved, not repeated.
        lines = []
        attribution = _attribution(agent_id, thread_id)
        if prefixed:
            lines.append(attribution)
        label = _validation_line(description)
        if label:
            lines.append(label)  # TG-66: above everything, not at the bottom of 9,000 characters
            lines.append("Approving this will still fail validation.")
        if not prefixed:
            lines.append(attribution)
        lines.append(f"{action.tool} · {action.reason}")
        if action.reason in NO_UNDO_REASONS:
            lines.append("There is no undo for this.")
        lines.append("")
        if whole_text_present:
            lines.append(fit(description, 1200, marker="\n… (full text above)")[0])
        else:
            lines.append(_UPLOAD_FAILED)

        keyboard = keyboard_for(action, handle, index) if whole_text_present else None
        if keyboard is None:
            # TG-55: a hand-off, never an empty keyboard — and it has to name **where**.
            # `validate_decisions` would reject every type, so this approval parks the thread
            # forever and RT-39 then refuses every later message in this chat. "Open the TUI" is
            # only actionable with the thread the approval is parked on, and a knowledge base has
            # many; without it the chat is bricked with no visible cause, which is the outcome the
            # rule exists to prevent. TG-56's failed upload lands here too, for the same reason
            # stated the other way round: an approval nobody can read is one nobody may approve.
            await self._say(
                channel,
                "\n".join(lines) + _NO_DECISIONS.format(agent_id=agent_id, thread_id=thread_id),
                agent_id=agent_id,
                attributed=True,
            )
            return
        # `attributed=True`, never `prefix=`: the attribution line above is this message's agent id,
        # hoisted to the top when `prefixed`, and a generic prefix on top of it — whether asked for
        # or added by `_send` on noticing the message left its agent's channel — says the same thing
        # twice on the one message that carries an irreversible button.
        sent = await self._send(
            channel, "\n".join(lines), keyboard, agent_id=agent_id, attributed=True
        )
        message_id = sent.get("message_id") if isinstance(sent, Mapping) else None
        if isinstance(message_id, int):
            await self.store.record_message(handle, message_id)

    async def _on_callback(self, query: Mapping[str, Any]) -> None:
        """A button press. **The callback is answered first, on every path** (TG-61, TG-20, TG-62).

        A resume starts a turn of 8-12 model calls — 16 s on the cloud model, 284 s on the local
        fallback. Answer it afterwards and the button spins, the query expires, and the human, who
        has no other feedback, presses again against an interrupt the first press already resolved.

        Answered **once**, with the outcome, rather than answered blank and then again: Telegram
        accepts one ``answerCallbackQuery`` per query, so a second call carrying the alert is simply
        discarded and the refusal or the stale notice never reaches the phone. The only work that
        precedes it is the durable-row read, which is one indexed statement on a local SQLite file —
        the spinner is about the model calls, not about a microsecond.

        A press from anyone outside the allow-list is refused **with an alert** and logged. On a
        phone a silent answer is indistinguishable from a successful one, so a stranger who presses
        Approve on a delete would have every reason to believe the irreversible write happened; and
        this allow-list is the system's only authentication boundary, which makes an attempt against
        it the one event the operator has to see (decision X).
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

        parsed = parse_callback(str(query.get("data", "")))
        if parsed is None:
            await self._answer(callback_id, _UNREADABLE, alert=True)
            return
        handle, index, verb = parsed

        prompt = await self.store.prompt(handle)
        if prompt is None:
            # TG-58: **not located** is a different fact from **already answered**, and the two
            # send the human to different places. "Already answered" tells them to stop looking;
            # this one tells them the approval may still be parked and that the TUI can reach it.
            # The row is the only index a press carries — `callback_data` holds 64 bytes and a
            # handle, never an interrupt id (TG-57) — so there is nothing to scan `list_threads`
            # *by*, and the adapter never guesses a thread.
            await self._answer(callback_id, _NOT_LOCATED_ALERT, alert=True)
            # The query's own channel, which is where the human is looking: a press carries the
            # message it was attached to, and that message carries its `message_thread_id` (F-1).
            where = _channel_of_query(query)
            if where is not None:
                await self._say(where, _NOT_LOCATED)
            return
        if prompt["resolved"]:
            # TG-62/TG-63: a message lives in the chat forever with its buttons live. A press a week
            # later must not answer whatever interrupt is pending now — and an alert rather than a
            # toast, because a toast on a phone is missed and the human then believes the wrong
            # thing about a write with no undo.
            await self._answer(callback_id, _STALE_ALERT, alert=True)
            await self._clear_keyboard(prompt)
            where = _channel_of_query(query) or _channel_of_prompt(prompt)
            await self._say(where, _STALE)
            return
        await self._answer(callback_id)

        channel = _channel_of_prompt(prompt)
        try:
            # One `get_thread` per press (TG-62): the live request decides both whether this needs
            # a second tap and what the decisions are applied to.
            detail = await self.service.get_thread(str(prompt["thread_id"]))
            if verb == _CANCEL:
                # TG-64: *Cancel* on "there is no undo — confirm?" means "I have not decided", not
                # "reject". It used to fall through as an unknown verb, be recorded as this
                # action's answer and become a `reject` one function along — so a human backing out
                # of a delete had just rejected the write and closed the approval, and on a
                # multi-action approval that last press fired the whole `resume`. Nothing is
                # recorded, the prompt stays open and the keyboards stay live.
                await self._say(channel, _NOT_DECIDED)
                return

            if detail.pending is None or detail.pending.interrupt_id != prompt["interrupt_id"]:
                # TG-62, checked **before** the button is validated: an approval another channel
                # answered is not a malformed press, and the two say different things to the human.
                await self._note_stale(prompt, callback_id)
                return

            plain = _CONFIRM_VERBS.get(verb, verb)
            if _offered_type(detail.pending, index, plain) is None:
                # TG-60/TG-54: validated against the **freshly-read** request, never against the
                # count stored when the message was posted. An index past the live action list — a
                # button from an older adapter still sitting in the chat, or an approval whose
                # actions shrank between the post and the press — used to be recorded anyway, and
                # `resolve` then raised "every action needs an answer", the `finally` closed the
                # prompt and every keyboard went dead with the human's genuine taps already spent.
                # A refusal leaves the prompt **open** and the buttons intact.
                await self._say(channel, _CANNOT_ANSWER.format(thread_id=prompt["thread_id"]))
                return
            if verb == plain and _needs_confirm(detail.pending, index):
                await self._ask_confirm(
                    channel, handle, index, verb, agent_id=str(detail.pending.agent_id)
                )
                return

            answers = await self.store.record_answer(handle, index, plain)
            if len(answers) < len(detail.pending.actions if detail.pending else ()):
                return  # TG-60: a partial set submits nothing, and the interrupt stays parked
            await self._resolve(prompt, answers, detail.pending, callback_id)
        except Exception as exc:
            # The poll loop suppresses exceptions so one bad update cannot stop the bot — which
            # means a failure here would be *silent*, and the human would press again on a keyboard
            # that has already stopped working. Saying so is the whole difference.
            _log.warning("telegram: a button press could not be applied", exc_info=True)
            await self._say(channel, _PRESS_FAILED.format(reason=exc))

    async def _resolve(
        self,
        prompt: Mapping[str, Any],
        answers: Mapping[int, str],
        request: ApprovalRequest | None,
        callback_id: str = "",
    ) -> None:
        """Resolve against the **live** approval the server just handed back (TG-58, TG-62).

        The durable row supplies the thread; the *server* supplies the request. Any in-memory map is
        a cache and never the authority — which is what makes a press that arrives after a restart
        work, and makes the restart case exercised by every test.

        A request that is gone, or that carries a different interrupt id, is the two-channel case
        the design expects rather than an edge: the TUI answered it at the desk while the phone
        still had the keyboard. It is never retried — a retry either spins or applies the human's
        taps to a **different** write — and it is reported as an alert, because a toast on a phone
        is missed and the state the human then believes they are in is wrong. The alert is attempted
        even though this query was already answered plainly — Telegram keeps the first answer, so
        the chat message beside it is what actually carries the news on this particular path.

        The decisions carry **no prose** (TG-65): :class:`~pkb.clients.approval.Answer` leaves
        ``message`` ``None`` and nothing here fills it in. A phone is a bad place to demand a
        typed reason, ``pkb.clients.approval`` deliberately holds no policy requiring one so that
        both channels answer identically, and the harness substitutes its own "do not retry unless
        the user asks" text. A bot that insisted would be refusing a resume the daemon accepts —
        a client-only refusal, invisible server-side (CL-13, Q14 RULED).
        """
        channel = _channel_of_prompt(prompt)
        if request is None or request.interrupt_id != prompt["interrupt_id"]:
            await self._note_stale(prompt, callback_id)
            return

        built = {
            index: Answer(type=VERBS[verb]) for index, verb in answers.items() if verb in VERBS
        }
        try:
            resolution = resolve(request, built)
            subscription = await self.service.resume(
                # TG-59: the request's OWN thread. A fan-out gate parks on the expert's derived
                # thread, and posting a delegate's decisions to the parent is a 409.
                resolution.thread_id,
                resolution.decisions,
                interrupt_id=resolution.interrupt_id,
            )
        except StaleInterruptError:
            # TG-62, the *race* rather than the common case: the TUI answered between this press's
            # `get_thread` and its `resume`, so the pre-emptive check above saw a live interrupt
            # and the daemon refused anyway. Two channels on one approval is the design, so this
            # window is expected — and it has to land on the same **alert** the pre-emptive branch
            # uses. It used to fall into the generic handler below, which sends a plain chat line
            # that scrolls away, and the rule's whole rationale is that a toast on a phone is
            # missed. Never retried: a retry applies the human's taps to a different write.
            await self._answer(callback_id, _STALE_ALERT, alert=True)
            await self._say(channel, _STALE)
            return
        except Exception as exc:
            await self._say(channel, f"That approval could not be applied: {exc}")
            return
        finally:
            await self.store.resolve_prompt(prompt["handle"])
            await self._clear_keyboard(prompt)
        # The outcome is a **new** message, not an edit over the description (TG-63, D6): the chat
        # is the only surviving record of what was approved, and overwriting the text the human read
        # before tapping destroys it.
        #
        # Attributed, because the channel on the prompt row may be a topic that has since been
        # repaired or retired (TG-82). Unattributed, this one line — the record that an irreversible
        # write just happened — could neither follow the agent to its live topic nor name it on
        # arrival in General.
        await self._say(channel, _outcome_text(answers), agent_id=str(request.agent_id))
        # The resume streams into the channel the approval was **posted** in, which under TG-89 is
        # the originating one — the human is looking at the conversation they answered from, and a
        # fan-out's reply arriving in an expert's channel would be the outcome of one paste split
        # across three places.
        await self._consume(channel, str(request.agent_id), subscription)

    async def _note_stale(self, prompt: Mapping[str, Any], callback_id: str) -> None:
        """Somebody else answered it — one alert, one line, every keyboard off (TG-62, TG-63).

        ``show_alert`` rather than a toast because a toast on a phone appears over the keyboard for
        a second while the human is already scrolling, and the state they then believe they are in
        is wrong: they think their tap landed on a write with no undo. Never retried — a retry
        either spins or applies the human's taps to a **different** write.
        """
        await self._answer(callback_id, _STALE_ALERT, alert=True)
        await self._say(_channel_of_prompt(prompt), _STALE)
        await self.store.resolve_prompt(str(prompt["handle"]))
        await self._clear_keyboard(prompt)

    async def _ask_confirm(
        self, channel: Channel, handle: str, index: int, verb: str, *, agent_id: str | None = None
    ) -> None:
        """A second tap for the three destructive reasons (TG-64) — and its id is **recorded**.

        ``record_message`` matters as much as the keyboard (TG-63): without it this message is the
        one part of the approval whose buttons are never cleared, so "Yes, do it" sits live in the
        chat forever over a write that already happened. Every message of an approval loses its
        keyboard at the terminal outcome, and that has to mean *every*.

        ``agent_id`` is the **live** request's, and it is what makes this message obey TG-82 and
        TG-85(b) like every other part of the approval. Sent without one it was invisible to
        :meth:`_route_out`'s retirement check and to :func:`_prefixed`, so on a retired or
        topic-less channel the human received a bare *"There is no undo for this. Confirm?"* with a
        **Yes, do it** button in General, naming no expert and no write — the one message in the
        layer where being wrong about which expert is an irreversible write to the wrong topic.
        """
        keyboard = [
            [
                {
                    "text": "Yes, do it",
                    "callback_data": callback_data(handle, index, _CONFIRM + verb),
                }
            ],
            [{"text": "Cancel", "callback_data": callback_data(handle, index, _CANCEL)}],
        ]
        sent = await self._send(
            channel, "There is no undo for this. Confirm?", keyboard, agent_id=agent_id
        )
        message_id = sent.get("message_id") if isinstance(sent, Mapping) else None
        if isinstance(message_id, int):
            await self.store.record_message(handle, message_id)

    async def _answer(self, callback_id: str, text: str = "", *, alert: bool = False) -> None:
        """Stop the spinner — once per press, and carrying the outcome when there is one (TG-61)."""
        if not callback_id:
            return
        with contextlib.suppress(TelegramError):
            await self.api.answer_callback(callback_id, text, show_alert=alert)

    async def _clear_keyboard(self, prompt: Mapping[str, Any] | None) -> None:
        """Every message of the approval loses its buttons — and **keeps its text** (TG-63, D6).

        ``editMessageReplyMarkup`` rather than ``editMessageText``: the description is the surface
        the human decided against, and this chat is the only place it survives. Overwriting it with
        an outcome line answers "was this answered?" by destroying the answer to "what did I
        approve?" — for a write that has no undo. The outcome arrives as a new message instead.

        **No topic is passed, and that is a rule** (TG-90, F-6). ``editMessageReplyMarkup`` addresses
        a message by ``chat_id`` + ``message_id`` and takes no thread parameter; only the send
        family carries one. The natural assumption is the opposite, and acting on it puts an unknown
        parameter on the one call that disarms an irreversible button — inside a ``finally``, where
        a 400 is reported as a shutdown bug and the buttons stay live.
        """
        if prompt is None:
            return
        for message_id in prompt.get("message_ids", []):
            with contextlib.suppress(TelegramError):
                await self.api.clear_keyboard(int(prompt["chat_id"]), int(message_id))

    async def _repost_pending(self, channel: Channel, thread_id: str) -> None:
        """Re-post whatever is parked, including a fan-out gate on a child (TG-53).

        Never concludes "no approval" from the parent's ``pending is None``: LB-16 parks an expert's
        gate on its own derived thread, so the parent's is null while the child holds the interrupt.

        **Every** child with a ``pending`` is re-posted, not the first (TG-53). A fan-out can gate
        two experts at once, and stopping at the first leaves the second approval with no
        affordance on the phone — dead buttons with nothing logged, which is the silent failure
        this rule is about. Duplicate keyboards for one interrupt are impossible here because each
        child carries its own.
        """
        detail = await self.service.get_thread(thread_id)
        if detail.pending is not None:
            await self._post_approval(channel, detail.pending)
            return
        for child in detail.children:
            child_detail = await self.service.get_thread(child.thread_id)
            if child_detail.pending is not None:
                await self._post_approval(channel, child_detail.pending)

    async def _pending(self, channel: Channel) -> None:
        """``/pending`` — everything waiting, across every expert (Q19 ruled pull-only, TG-88).

        The summary goes to the channel the command was typed in; each keyboard goes to **that
        approval's agent's channel** when one exists, and to the typing channel with TG-85's agent
        prefix when it does not.

        This looks like it contradicts TG-89 and does not. A fan-out approval has an *originating*
        channel — the human typed there seconds ago and is looking at it. A ``/pending`` approval has
        none: it may have been raised in the TUI hours earlier, and its agent's own channel is the
        only place with a claim on it and the place the human will look for that expert tomorrow.

        Nothing here creates a channel (decision AA, Q31): creating a topic as a side effect of a
        *listing* command is the daemon deciding what is reachable from the phone, which is the one
        thing TG-3 has ruled against from the beginning.
        """
        threads = await self.service.list_threads()
        waiting = [t for t in threads if t.pending_interrupt_id]
        if not waiting:
            await self._say(channel, "Nothing is waiting on you.")
            return
        await self._say(
            channel,
            "Waiting on you:\n"
            + "\n".join(f"· {t.agent_id} — {t.title or 'untitled'}" for t in waiting),
        )
        for thread in waiting:
            detail = await self.service.get_thread(thread.thread_id)
            if detail.pending is None:
                continue
            home = await self._channel_of(channel.chat_id, detail.pending.agent_id)
            # `prefixed` only on the fallback: a keyboard that reached its agent's own channel is
            # already attributed by the topic title above it, and a prefix on every reply inside a
            # conversation the human is already in trains them to skip the first line — which is
            # exactly where the attribution has to be legible when it matters (decision AE).
            await self._post_approval(home or channel, detail.pending, prefixed=home is None)

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

    async def _send_document(
        self,
        channel: Channel,
        filename: str,
        content: bytes,
        *,
        caption: str,
        agent_id: str | None = None,
    ) -> bool:
        """TG-56's overflow upload, addressed to the channel and checked like every other send.

        The document is the **only** place the whole description exists in the chat, so a copy of it
        landing in General while its keyboard lands in a topic is the split TG-80 exists to catch.
        A stray document carries no buttons, so it is disarmed by having none: the correction line
        is posted and the description is re-sent into the repaired channel by the retry above it.

        Returns whether the whole description reached the chat, because the caller attaches an
        irreversible button on the strength of that answer (TG-56). Exhausting the loop is not
        reachable at today's constants — :data:`_SEND_ATTEMPTS` is exactly one more than the deaths
        :data:`~pkb.server.telegram_api.MAX_RECREATIONS` allows, and the General send it terminates
        on is never checked — but "unreachable" here rests on a relationship between two constants
        in two modules, and what it buys if it ever stops holding is a keyboard under a 1,200
        character preview ending *"… (full text above)"* when there is no full text above. Saying
        so in the return type costs one branch and does not depend on the arithmetic.
        """
        for _ in range(_SEND_ATTEMPTS):
            target = self._route_out(channel, agent_id)
            try:
                if target.is_general:
                    sent = await self.api.send_document(
                        target.chat_id, filename, content, caption=caption
                    )
                else:
                    sent = await self.api.send_document(
                        target.chat_id, filename, content, caption=caption, topic_id=target.topic_id
                    )
            except TelegramError as exc:
                if not (exc.is_missing_thread and not target.is_general):
                    # Everything else is TG-56's upload failure and belongs to the caller, which
                    # answers it with a hand-off rather than a keyboard over a fragment.
                    raise
                await self._channel_died(target, agent_id, stray=None, armed=False)
                continue
            if target.is_general or _landed(sent) == target.topic_id:
                return True
            await self._channel_died(target, agent_id, stray=sent, armed=False)
        return False

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

    def _exposed(self, channel: Channel, agent_id: str | None) -> bool:
        """Will a message for this channel be delivered outside its agent's own channel (TG-85(b))?

        Falling back to General is the only re-addressing that takes a message out of the topic
        whose title attributes it — a repaired channel is still that agent's. Asked *before* the
        send by :meth:`_post_action`, which has to decide whether the attribution line leads or
        follows the validation label, and again inside :meth:`_send`, which is the only place that
        knows a channel died mid-message.
        """
        return self._route_out(channel, agent_id).is_general and not channel.is_general

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
        exotic one: :meth:`_pump_outbox` is a separate task and a run emits its reply and its
        approval from another, so the pump can be inside a send while ``_consume`` reaches the
        ``InterruptEvent``. Returning early there left an Approve button live in General under the
        wrong expert's name — the exact failure this section is arranged around, reached through the
        one branch written to keep the *corrections* from multiplying.

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
          **re-sync, never replay** (TG-31). Three branches, in order: a parked approval gets its
          keyboard back (including a fan-out gate on a child, TG-53); a run still live is
          reattached and only its outcome rendered, because ``attach`` replays from ``seq 0`` and
          re-rendering the replay double-posts text already in the chat; and once the hub is gone,
          the last assistant message is posted from ``ThreadDetail``, marked delivered late.

        Without the re-sync the human's approve/reject buttons are simply dead after a supervised
        restart — the interrupt survives, no keyboard is re-posted, nothing logs it, and RT-39 then
        refuses every later message in that chat.
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

        Into the channel the update came from (TG-31 amended): a restart that re-posts Cooking's
        approval keyboard into General is TG-80's failure without anyone having deleted anything.
        """
        detail = await self.service.get_thread(thread_id)
        parked = detail.pending is not None
        for child in detail.children:
            # TG-53: a fan-out gate parks on the expert's derived thread, so the parent's `pending`
            # being null proves nothing. Concluding "no approval" from it leaves the human's
            # buttons dead after a restart with nothing logged.
            parked = parked or (await self.service.get_thread(child.thread_id)).pending is not None
        if parked:
            await self._repost_pending(channel, thread_id)  # branch (a)
            return
        subscription = await self.service.attach(thread_id)
        if subscription is not None:
            # `detail.thread.agent_id`, not `detail.agent_id`: :class:`~pkb.service.ThreadDetail`
            # has no such attribute, so the `getattr` this replaced answered ``None`` on **every**
            # restart. That is not cosmetic under §9 — an unattributed frame is invisible to
            # `_route_out`'s retirement check (TG-82), carries no TG-85(b) prefix when it falls back
            # to General, and cannot repair the channel it dies in (TG-84), which is exactly the
            # path a restart takes: `_moved` is process memory and the ledger row still names the
            # topic that was deleted while the daemon was down.
            await self._consume(channel, _agent_of(detail), subscription, replay=True)  # branch (b)
            return
        await self._post_late_reply(channel, detail)  # branch (c)

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


def _needs_confirm(request: ApprovalRequest | None, index: int) -> bool:
    """Whether this action takes a second tap (TG-64) — read from the **live** request.

    Never from the button or the durable row: the reason decides, and the reason is the server's.
    """
    if request is None or index >= len(request.actions):
        return False
    return request.actions[index].reason in NO_UNDO_REASONS


def _offered_type(request: ApprovalRequest | None, index: int, verb: str) -> DecisionType | None:
    """What this press means to the **live** request, or ``None`` if it means nothing (TG-54, TG-60).

    The inverse of :func:`keyboard_for`, over the same :data:`VERBS` table and the same
    ``offered(..., drop=("edit",))`` narrowing, re-derived from the request the server just handed
    back rather than from anything the button carried. Three ways to get ``None``, and each is a
    press that must be refused with the prompt left open rather than recorded:

    * an unknown verb — a button from an adapter version that shipped a decision this one dropped;
    * an index past the live action list — an approval whose actions shrank since it was posted;
    * a decision the action no longer allows — the case TG-54 exists for, where the keyboard drew
      something ``validate_decisions`` will refuse.
    """
    kind = VERBS.get(verb)
    if request is None or kind is None or not 0 <= index < len(request.actions):
        return None
    if kind not in offered(request.actions[index], drop=_DROPPED):
        return None
    return kind


def _outcome_text(answers: Mapping[int, str]) -> str:
    """What was just sent, mechanically counted — the chat's record of an irreversible act (D6).

    Counted through :data:`VERBS` rather than against ``"a"``, so a verb this table gains is
    reported rather than silently folded into "rejected" (TG-54).
    """
    parts = []
    for verb, kind in VERBS.items():
        count = sum(1 for answer in answers.values() if answer == verb)
        if count:
            parts.append(f"{count} {_OUTCOME_WORDS[kind]}")
    return "Answered: " + ", ".join(parts) + "."


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


def _channel_of_prompt(prompt: Mapping[str, Any]) -> Channel:
    """The channel an approval was posted in, from the durable row (TG-57).

    The **row**, never ``callback_data``: the budget is 64 bytes and a chat id alone was already
    refused at it, let alone a chat id and a topic id. A press that arrives after a restart is
    answered in the channel it was raised in because the row remembers, not because the button does.
    """
    return Channel(int(prompt["chat_id"]), int(prompt.get("topic_id") or GENERAL))


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


def _attribution(agent_id: str, thread_id: str) -> str:
    """An approval's own naming line: the expert always, the derived thread when there is one.

    TG-85(a), and defect 3 — Q20's wording required *"naming the expert and the derived thread"* and
    the shipped build named neither, sending ``tool · reason`` and nothing else. A fan-out approval
    therefore arrived as an unattributed diff with an Approve button; with per-expert channels that
    becomes an Approve button in the Librarian's channel for a write into Cooking, indistinguishable
    from the Librarian's own work.

    The thread id is added only when the thread is derived, because on a direct conversation it is
    36+ characters of noise that the human cannot act on — and its presence is precisely what says
    "this write is happening somewhere other than where you are reading".
    """
    if librarian_thread_id(thread_id) is not None:
        return f"{agent_id} · {thread_id}"
    return agent_id


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


_VALIDATION_HEADER: Final = "This draft currently fails validation:"
"""``gates._validation_label``'s exact prefix — matched, never recomputed (TG-66, RT-35).

Layer 2 authors the label and Layer 1 owns validation; I2/I3 make it impossible for this module to
re-run either, and that is the point. A prefix match cannot disagree with the server about whether
a draft is valid.
"""


def _validation_line(description: str) -> str:
    """The whole validation **block**, hoisted to the top of the button message (TG-66).

    ``_validation_label`` is the header followed by ``render_findings(...)`` — one finding per
    line, ``FM-3 …``, ``PA-7 …`` — so returning only the matching line hoisted the announcement and
    left every rule id behind, at the bottom of a description TG-56 may have just uploaded as a
    file. The human read "this draft currently fails validation:" with no indication of *what*
    fails, above an Approve button, which is the failure this rule names in its own words. The
    block ends at the first blank line, because that is exactly where ``render_findings`` stops.
    """
    lines = description.splitlines()
    for start, line in enumerate(lines):
        if line.startswith(_VALIDATION_HEADER):
            block = [line.rstrip()]
            for follow in lines[start + 1 :]:
                if not follow.strip():
                    break
                block.append(follow.rstrip())
            return "\n".join(block)
    return ""


_CONFIRM_VERBS: Final = {f"{_CONFIRM}{verb}": verb for verb in VERBS}
"""The confirmed form of every verb (TG-64), derived from :data:`VERBS` so the two cannot drift."""

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
_BOUND: Final = (
    "Bound this topic to {agent_id}. Nothing was created — anything you send here from now on goes "
    "to that expert."
)
_CREATED: Final = "Created a new topic, {title}, for {agent_id}. Send it anything from there."
_CREATE_FAILED: Final = (
    "I could not create a topic for {agent_id}: {reason}\nNothing was created and nothing changed."
)
_DISARMED: Final = " Its buttons no longer work, so nothing can be approved from it."
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
_NO_DECISIONS: Final = (
    "\n\nThis one cannot be answered from here. It is still waiting on {agent_id}, in thread "
    "{thread_id}, and the TUI can resolve it. Until it is answered, nothing else sent to this "
    "conversation will run."
)
_UPLOAD_FAILED: Final = (
    "This one is too long for a message and the upload failed, so I cannot show you what would be "
    "written. There are no buttons on it for that reason."
)
_PRESS_FAILED: Final = (
    "I could not apply that decision: {reason}\n"
    "Nothing was sent. The approval is still waiting and the TUI can resolve it."
)
_STALE: Final = (
    "That approval was already answered — from another channel, or earlier here. Nothing was sent."
)
_STALE_ALERT: Final = "Already answered — from another channel, or earlier here. Nothing was sent."
_NOT_LOCATED: Final = (
    "That approval could not be located, so nothing was sent. It may still be waiting — the TUI "
    "lists everything pending and can answer it."
)
_NOT_LOCATED_ALERT: Final = "That approval could not be located. Nothing was sent."
_NOT_DECIDED: Final = (
    "Nothing was answered. That approval is still waiting, and the buttons above still work."
)
_CANNOT_ANSWER: Final = (
    "That button no longer matches the approval it belongs to, so nothing was sent and nothing "
    "was recorded. It is still waiting in thread {thread_id}, and the TUI can answer it."
)
_REFUSED: Final = (
    "This knowledge base does not accept decisions from this account. Nothing was approved, "
    "rejected or written."
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
