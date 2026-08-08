"""The Telegram adapter — a channel per expert, inside the daemon (TG-1 … TG-66).

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
    terminal_status,
)
from pkb.server.telegram_api import (
    CALLBACK_DATA_LIMIT,
    MESSAGE_LIMIT,
    BotApi,
    TelegramError,
    with_retry,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pkb.contracts import ActionView
    from pkb.service import PkbService
    from pkb.service.telegram import TelegramStore

__all__ = [
    "COMMANDS",
    "TelegramAdapter",
    "TelegramConfig",
    "callback_data",
    "counter",
    "keyboard_for",
    "split_message",
    "utf16_len",
]

_log = logging.getLogger(__name__)

COMMANDS: Final = ("/new", "/threads", "/agents", "/pending", "/cancel")
"""The whole command surface. **No ``/connect``** — a chat is bound to its agent by configuration
(TG-1), and the ambiguity ``/connect`` created is what made a mis-sent note land in the wrong topic."""

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
"""How many chats the TG-23 window remembers. A stranger picks their own chat id, so it is bounded."""


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
    """One unmapped-chat reply per chat per window (TG-23)."""

    _outbox: asyncio.Queue[tuple[int, str, Any]] = field(default_factory=asyncio.Queue)
    _unroutable: frozenset[int] = frozenset()
    _warned_at: dict[int, float] = field(default_factory=dict)
    _conflicted: bool = False
    _group: Any | None = field(default=None, repr=False)
    _locks: dict[int, asyncio.Lock] = field(default_factory=dict, repr=False)

    async def run(self) -> None:
        """Poll, dispatch and send — all inside one task group (TG-7).

        The group is the point: ``_supervise`` awaits this coroutine and has no handle on anything
        it spawns, so a detached child would survive a crash and the restart would add another. With
        a group, a failure anywhere cancels the rest and the supervisor gets a clean slate.

        Startup — the orphan report and the TG-31 re-sync — runs **inside** the group rather than
        ahead of it, so a chat that cannot be written to fails the way every other send does
        instead of raising into ``_supervise`` before the group exists.
        """
        await self.store.setup()
        self.check_mapping()
        async with asyncio.TaskGroup() as group:
            self._group = group
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
                if not await self.store.claim(update_id, _chat_of(update), kind):
                    continue
                await self._spawn(self._handle(update), name=f"pkb-telegram-update-{update_id}")

    async def _handle(self, update: Mapping[str, Any]) -> None:
        """One update, off the poll loop, serialized **per chat** (TG-39, TG-38).

        The lock is what keeps the concurrency honest: Telegram delivers a chat's messages in
        order and three lines typed as three messages are the normal case on a phone, so running
        them at once would turn an ordinary conversation into a stream of ``ThreadBusyError``
        refusals. Commands and button presses take no lock — ``/cancel`` exists to reach a run
        that is holding one.

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

    def _chat_lock(self, chat_id: int) -> asyncio.Lock:
        """One turn at a time per chat (TG-39). Bounded by the mapping — only mapped chats run."""
        lock = self._locks.get(chat_id)
        if lock is None:
            lock = self._locks[chat_id] = asyncio.Lock()
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
                await self.store.claim(last, _chat_of(update), kind)
                await self.store.dispatched(last)
        _log.warning("telegram: cold start — discarding the backlog up to update %d", last)
        for chat_id in self.config.chats:
            # TG-13: a chat that blocked the bot is one recipient, not the subsystem being down.
            await self._announce(chat_id, _COLD_START)
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
        agent_id = await self._admit(message)
        if agent_id is None:
            return
        if edited:
            # TG-35: the turn on the original has already run and may already have written.
            await self._say(int(message["chat"]["id"]), _EDITED)
            return
        await self._on_message(message, agent_id, update_id=update_id)

    async def _admit(self, message: Mapping[str, Any]) -> str | None:
        """Who and where, before what — the agent this message is addressed to, or ``None``.

        Three checks, in this order, and the order is the rule (TG-19, TG-20, TG-21, TG-23):

        * **private only** (TG-19). A group is many senders with no identity check in front of a
          knowledge base with no undo, and Telegram's group privacy mode silently drops most
          messages anyway — a mapped group *half* works, which is worse than refusing.
        * **the owner allow-list, before the mapping** (TG-20). It used to run after, so a
          non-allow-listed sender got a refusal in a mapped chat and the full unmapped explanation
          in any other: a guaranteed reply on every path, which is a reply amplifier and an
          existence oracle for anyone who finds the bot's username. Silence is the rule's own
          wording — "ignored silently" — and its own assertion is zero replies for both.
        * **the mapping** (TG-2, TG-18, TG-21), rate limited per chat (TG-23). This reply survives
          the reordering because the sender who reaches it is an *owner* opening a chat that is not
          mapped yet, and the chat id is the one datum they cannot look up any other way.
        """
        chat = message.get("chat") or {}
        chat_id = int(chat.get("id", 0))
        if chat.get("type") != "private":
            return None
        sender = int((message.get("from") or {}).get("id", 0))
        if sender not in self.config.owner_user_ids:
            return None
        agent_id = self.config.chats.get(chat_id)
        if agent_id is None or chat_id in self._unroutable:
            # TG-2/TG-21: the chat id and where to add it. No agent ids — the bot's username is
            # discoverable, and a listing sent to a stranger leaks the shape of a private KB.
            # TG-18 lands here too: a mapping naming an agent that no longer exists is answered
            # like an unmapped chat rather than routed to whatever is nearest.
            if self._may_warn(chat_id):
                await self._say(chat_id, _UNMAPPED.format(chat_id=chat_id))
            return None
        return agent_id

    async def _on_message(
        self, message: Mapping[str, Any], agent_id: str, *, update_id: int | None = None
    ) -> None:
        chat_id = int(message["chat"]["id"])
        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            # TG-36: nothing is downloaded and nothing is written. The caption is the part the human
            # actually wrote, so it is quoted back rather than lost.
            caption = str(message.get("caption") or "")
            await self._say(
                chat_id, _ATTACHMENT + (f"\n\nYour caption:\n{caption}" if caption else "")
            )
            return

        if text.startswith("/"):
            # No chat lock: `/cancel` is the one thing that has to reach a run holding it (TG-39).
            await self._command(chat_id, agent_id, text.strip())
            return
        async with self._chat_lock(chat_id):
            await self._turn(chat_id, agent_id, text, update_id=update_id)

    def _may_warn(self, chat_id: int) -> bool:
        """One unmapped-chat reply per chat per window; a repeat inside it gets silence (TG-23).

        Anyone who finds the bot can send it a hundred messages, and a bot that answers each one
        earns a Telegram rate limit that then delays the *owner's* approval keyboards. The window is
        process-local on purpose: it protects the send budget, and a restart re-explaining the
        mapping once is the harmless direction to be wrong in.
        """
        now = time.monotonic()
        last = self._warned_at.get(chat_id)
        if last is not None and now - last < self.unmapped_window:
            return False
        if len(self._warned_at) >= _WARNED_CAP:
            # A stranger picks their own chat id, so this dict is attacker-sized unless it is
            # bounded. Dropping the oldest half re-explains at worst once more per chat.
            oldest = sorted(self._warned_at.items(), key=lambda item: item[1])
            self._warned_at = dict(oldest[len(oldest) // 2 :])
        self._warned_at[chat_id] = now
        return True

    async def _command(self, chat_id: int, agent_id: str, text: str) -> None:
        command = text.split()[0]
        if command == "/new":
            await self.store.unbind(chat_id)
            await self._say(
                chat_id,
                "Started a new conversation. The previous one is still in your thread list.",
            )
        elif command == "/threads":
            threads = await self.service.list_threads(agent_id)
            await self._say(chat_id, _threads_text(threads))
        elif command == "/agents":
            names = ", ".join(sorted(set(self.config.chats.values())))
            await self._say(chat_id, f"This chat talks to {agent_id}. Configured here: {names}.")
        elif command == "/pending":
            await self._pending(chat_id)
        elif command == "/cancel":
            await self._cancel(chat_id)
        else:
            await self._say(chat_id, f"I know {', '.join(COMMANDS)}.")

    async def _turn(
        self, chat_id: int, agent_id: str, text: str, *, update_id: int | None = None
    ) -> None:
        """One message, one turn on the chat's current thread (TG-26, TG-4, TG-29).

        The binding carries the agent it was made for, and a mismatch with the *configured* agent
        rotates the chat onto a fresh thread. Without that check, editing the mapping kept filing
        into the previous expert forever: measured, a chat bound under ``topic/cooking`` and then
        re-mapped to ``topic/grilling`` issued **zero** ``create_thread`` calls and sent the new
        message to the Cooking thread — a write to the wrong topic, with no undo, invisible from
        the phone and from ``/health``. That is the mis-file TG-1 was ruled to eliminate, so the
        rotation is announced rather than silent (TG-27's reasoning applies to a configuration
        change too: an invisible rotation is the failure class, not the rotation).
        """
        binding = await self.store.binding(chat_id)
        if binding is not None and binding[1] != agent_id:
            await self._say(chat_id, _REMAPPED.format(agent_id=agent_id))
            binding = None
        if binding is None:
            # TG-4: stamped `telegram` exactly once, so a conversation started on the phone is
            # recognisable in the TUI. Never read back in a conditional (TG-33).
            thread = await self.service.create_thread(agent_id, origin_channel="telegram")
            thread_id = thread.thread_id
            await self.store.bind(chat_id, thread_id, agent_id)
        else:
            thread_id = binding[0]
        try:
            subscription = await self.service.start_run(thread_id, text)
        except ApprovalPendingError:
            # TG-37: neither rotate nor retry. RT-39 exists because sending to an interrupted thread
            # silently discards the interrupt — and on a phone the original keyboard has scrolled
            # away, so re-posting it is the only thing that makes the state resolvable from here.
            await self._say(chat_id, _PENDING_BLOCKS.format(text=text))
            await self._repost_pending(chat_id, thread_id)
            return
        except ThreadBusyError:
            # TG-38: the normal case on a phone, where people send three lines as three messages.
            await self._say(chat_id, _BUSY.format(text=text))
            return
        if update_id is not None:
            # TG-29: admitted, so this update is no longer a loss the bot may ask the human to
            # re-send. The thread and run ids are recorded here because this is where they first
            # exist and TG-31's re-sync has nothing to reattach to without them.
            await self.store.started(update_id, thread_id, subscription.handle.run_id)
        await self._consume(chat_id, subscription)

    # -- the run ----------------------------------------------------------------------

    async def _consume(self, chat_id: int, subscription: Any, *, replay: bool = False) -> None:
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
                        await self._queue(chat_id, event.text)
                elif isinstance(event, SubagentStart):
                    roster.append(event.agent_id)
                elif isinstance(event, SubagentEnd):
                    pass  # coalesced into the single roster line below (TG-43)
                elif isinstance(event, InterruptEvent):
                    interrupted = True
                    await self._post_approval(chat_id, event.request)
                elif isinstance(event, RunEnd | RunError):
                    terminal = event
        except Exception:
            # TG-51: a stream that *raises* closed without a terminal frame just as surely as one
            # that stopped, so it takes the same branch. It used to propagate into `_poll`'s
            # blanket `suppress(Exception)`, which left the human with half a reply, no
            # "outcome unknown" line and no re-sync — indistinguishable from success, on a turn
            # that may already have written. Not re-raised, because re-raising is what makes it
            # invisible; not restarted, because D2 says a run outlives the client watching it.
            _log.warning("telegram: the event stream for chat %s ended abnormally", chat_id)
            terminal = None
        finally:
            # TG-52: synchronous, and never awaited — the same idiom the routes and MCP use.
            _detach(subscription)

        if roster:
            await self._queue(chat_id, "Asked: " + ", ".join(dict.fromkeys(roster)))
        if terminal is None:
            # TG-51: outcome unknown. Never success, never failure, and never a re-start.
            await self.service.get_thread(subscription.handle.thread_id)
            await self._queue(chat_id, _UNKNOWN)
            return
        status = terminal_status(terminal, interrupted=interrupted)
        note = _TERMINAL.get(status)
        if note:
            await self._queue(chat_id, note)

    # -- approvals --------------------------------------------------------------------

    async def _post_approval(self, chat_id: int, request: ApprovalRequest) -> None:
        """One message per action, each carrying its own whole description (TG-56, TG-60)."""
        handle = secrets.token_hex(_HANDLE_BYTES)
        await self.store.open_prompt(
            handle, chat_id, request.thread_id, request.interrupt_id, len(request.actions)
        )
        for index, action in enumerate(request.actions):
            await self._post_action(
                chat_id,
                handle,
                index,
                action,
                thread_id=request.thread_id,
                agent_id=request.agent_id,
            )

    async def _post_action(
        self,
        chat_id: int,
        handle: str,
        index: int,
        action: ActionView,
        *,
        thread_id: str,
        agent_id: str,
    ) -> None:
        description = action.description
        whole_text_present = True
        if utf16_len(description) > MESSAGE_LIMIT:
            # The whole thing arrives first, as a file. A delete embeds the entire current file and
            # a new-file write embeds the whole proposal; truncating hides half of it behind an
            # irreversible button.
            try:
                await self.api.send_document(
                    chat_id,
                    f"{action.tool}-{index}.diff"
                    if is_diff(description)
                    else f"{action.tool}-{index}.txt",
                    description.encode("utf-8"),
                    caption=f"{action.tool} · {action.reason}",
                )
            except TelegramError as exc:
                # TG-56: the upload is the *only* place the whole description exists in this chat,
                # so its failure is not cosmetic. The build swallowed it and went on to attach the
                # keyboard to a cut preview ending in "… (full text above)" — a marker that was now
                # a lie — putting an irreversible Approve button under bullets 0-30 of 300. A 413,
                # a 429 or a dropped tether is the first thing that goes wrong on a phone. A
                # description the human cannot see never carries a button; they get the hand-off
                # naming the thread instead.
                self._note_send_failed(exc)
                _log.warning(
                    "telegram: the description for %s could not be uploaded to chat %s (%s); "
                    "handing this approval off to the TUI rather than showing a cut one",
                    action.tool,
                    chat_id,
                    exc,
                )
                whole_text_present = False
        else:
            await self._say(chat_id, description)

        lines = []
        label = _validation_line(description)
        if label:
            lines.append(label)  # TG-66: above everything, not at the bottom of 9,000 characters
            lines.append("Approving this will still fail validation.")
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
                chat_id,
                "\n".join(lines) + _NO_DECISIONS.format(agent_id=agent_id, thread_id=thread_id),
            )
            return
        sent = await self._send(chat_id, "\n".join(lines), keyboard)
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
                _chat_of_query(query),
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
            where = _chat_of_query(query)
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
            where = _chat_of_query(query) or int(prompt["chat_id"])
            await self._say(where, _STALE)
            return
        await self._answer(callback_id)

        chat_id = int(prompt["chat_id"])
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
                await self._say(chat_id, _NOT_DECIDED)
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
                await self._say(chat_id, _CANNOT_ANSWER.format(thread_id=prompt["thread_id"]))
                return
            if verb == plain and _needs_confirm(detail.pending, index):
                await self._ask_confirm(chat_id, handle, index, verb)
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
            await self._say(chat_id, _PRESS_FAILED.format(reason=exc))

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
        chat_id = int(prompt["chat_id"])
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
            await self._say(chat_id, _STALE)
            return
        except Exception as exc:
            await self._say(chat_id, f"That approval could not be applied: {exc}")
            return
        finally:
            await self.store.resolve_prompt(prompt["handle"])
            await self._clear_keyboard(prompt)
        # The outcome is a **new** message, not an edit over the description (TG-63, D6): the chat
        # is the only surviving record of what was approved, and overwriting the text the human read
        # before tapping destroys it.
        await self._say(chat_id, _outcome_text(answers))
        await self._consume(chat_id, subscription)

    async def _note_stale(self, prompt: Mapping[str, Any], callback_id: str) -> None:
        """Somebody else answered it — one alert, one line, every keyboard off (TG-62, TG-63).

        ``show_alert`` rather than a toast because a toast on a phone appears over the keyboard for
        a second while the human is already scrolling, and the state they then believe they are in
        is wrong: they think their tap landed on a write with no undo. Never retried — a retry
        either spins or applies the human's taps to a **different** write.
        """
        await self._answer(callback_id, _STALE_ALERT, alert=True)
        await self._say(int(prompt["chat_id"]), _STALE)
        await self.store.resolve_prompt(str(prompt["handle"]))
        await self._clear_keyboard(prompt)

    async def _ask_confirm(self, chat_id: int, handle: str, index: int, verb: str) -> None:
        """A second tap for the three destructive reasons (TG-64) — and its id is **recorded**.

        ``record_message`` matters as much as the keyboard (TG-63): without it this message is the
        one part of the approval whose buttons are never cleared, so "Yes, do it" sits live in the
        chat forever over a write that already happened. Every message of an approval loses its
        keyboard at the terminal outcome, and that has to mean *every*.
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
        sent = await self._send(chat_id, "There is no undo for this. Confirm?", keyboard)
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
        """
        if prompt is None:
            return
        for message_id in prompt.get("message_ids", []):
            with contextlib.suppress(TelegramError):
                await self.api.clear_keyboard(int(prompt["chat_id"]), int(message_id))

    async def _repost_pending(self, chat_id: int, thread_id: str) -> None:
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
            await self._post_approval(chat_id, detail.pending)
            return
        for child in detail.children:
            child_detail = await self.service.get_thread(child.thread_id)
            if child_detail.pending is not None:
                await self._post_approval(chat_id, child_detail.pending)

    async def _pending(self, chat_id: int) -> None:
        """``/pending`` — everything waiting, across every expert (Q19, ruled pull-only)."""
        threads = await self.service.list_threads()
        waiting = [t for t in threads if t.pending_interrupt_id]
        if not waiting:
            await self._say(chat_id, "Nothing is waiting on you.")
            return
        await self._say(
            chat_id,
            "Waiting on you:\n"
            + "\n".join(f"· {t.agent_id} — {t.title or 'untitled'}" for t in waiting),
        )
        for thread in waiting:
            detail = await self.service.get_thread(thread.thread_id)
            if detail.pending is not None:
                await self._post_approval(chat_id, detail.pending)

    async def _cancel(self, chat_id: int) -> None:
        """``/cancel`` — the one place ``service.cancel`` is reachable from (TG-32, TG-39).

        The attached subscription is closed in a ``finally`` (TG-52). ``attach`` replays the hub
        from ``seq 0`` and holds a subscriber slot for as long as nobody detaches, so a ``/cancel``
        that walked away from it leaked exactly the subscriber that rule exists to prevent — in the
        daemon whose whole value proposition is staying up for weeks. Closing is **synchronous**:
        ``RunSubscription.close`` is a plain function despite its docstring, and awaiting it raises
        ``TypeError`` inside a ``finally`` during teardown (C-31).
        """
        thread_id = await self.store.bound_thread(chat_id)
        if thread_id is None:
            await self._say(chat_id, "Nothing is running here.")
            return
        subscription = await self.service.attach(thread_id)
        if subscription is None:
            await self._say(chat_id, "Nothing is running here.")
            return
        try:
            await self.service.cancel(subscription.handle.run_id)
        finally:
            _detach(subscription)
        await self._say(chat_id, "Cancelled.")

    # -- outbound ---------------------------------------------------------------------

    async def _queue(self, chat_id: int, text: str) -> None:
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
        self._outbox.put_nowait((chat_id, text, None))
        depth = self._outbox.qsize()
        if depth and depth % _OUTBOX_WARN == 0:
            _log.warning(
                "telegram: %d message(s) are queued for chat %s and not going out; the Bot API has "
                "been refusing or rate limiting sends",
                depth,
                chat_id,
            )

    async def _say(self, chat_id: int, text: str) -> None:
        await self._send(chat_id, text, None)

    async def _announce(self, chat_id: int, text: str) -> None:
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
            await self._send(chat_id, text, None)
        except TelegramError as exc:  # already recorded on `/health` by `_send`
            _log.warning(
                "telegram: could not deliver a startup notice to chat %s: %s", chat_id, exc
            )

    async def _send(
        self, chat_id: int, text: str, keyboard: list[list[dict[str, str]]] | None
    ) -> Mapping[str, Any]:
        """Send now, split on length, honouring a 429 (TG-45, TG-48).

        This retry is a **transport** retry of an idempotent send, and is explicitly not the run
        retry the TUI forbids: a dropped reply after an approved write means the human never learns
        what was written, and a dropped keyboard means a parked interrupt nobody is told about.

        ``split_message`` has already reserved the counter's units, so ``label + part`` is inside
        the wire limit — the counter is part of the payload Telegram measures (TG-44).
        """
        parts = split_message(text)
        sent: Mapping[str, Any] = {}
        for position, part in enumerate(parts):
            label = counter(position, len(parts))
            last = position == len(parts) - 1
            try:
                sent = await with_retry(
                    lambda part=part, label=label, last=last: self.api.send_message(
                        chat_id, label + part, keyboard=keyboard if last else None
                    )
                )
            except Exception as exc:
                # TG-13: reported, never `degraded`. A send failure is not the subsystem being
                # down, and a 503 here invites the restart D9 forbids.
                self._note_send_failed(exc)
                raise
        return sent

    async def _pump_outbox(self) -> None:
        """Drain the outbox. One failed send must not stop the bot — but it must not be silent.

        ``_send`` has already put the reason on ``/health`` (TG-13); the log line is what names the
        **chat** and the fact that a specific message was lost, which ``last_send_error`` cannot
        (TG-48). A reply that never arrived otherwise looks exactly like a turn that never ran.
        """
        while True:
            chat_id, text, _ = await self._outbox.get()
            try:
                await self._send(chat_id, text, None)
            except TelegramError as exc:
                _log.warning(
                    "telegram: a message to chat %s was not delivered and is lost (%s)",
                    chat_id,
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
        for update_id, chat_id, thread_id in await self.store.unfinished():
            if chat_id is None or chat_id not in self.config.chats:
                continue
            try:
                await self._resync(chat_id, thread_id)
            except Exception:
                _log.warning(
                    "telegram: could not re-sync chat %s on thread %s after a restart",
                    chat_id,
                    thread_id,
                    exc_info=True,
                )
            await self.store.dispatched(update_id)

    async def _resync(self, chat_id: int, thread_id: str) -> None:
        """One chat's unfinished turn, re-synced (TG-31). Never a re-run, never a ``cancel``."""
        detail = await self.service.get_thread(thread_id)
        parked = detail.pending is not None
        for child in detail.children:
            # TG-53: a fan-out gate parks on the expert's derived thread, so the parent's `pending`
            # being null proves nothing. Concluding "no approval" from it leaves the human's
            # buttons dead after a restart with nothing logged.
            parked = parked or (await self.service.get_thread(child.thread_id)).pending is not None
        if parked:
            await self._repost_pending(chat_id, thread_id)  # branch (a)
            return
        subscription = await self.service.attach(thread_id)
        if subscription is not None:
            await self._consume(chat_id, subscription, replay=True)  # branch (b)
            return
        await self._post_late_reply(chat_id, detail)  # branch (c)

    async def _post_late_reply(self, chat_id: int, detail: Any) -> None:
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
            await self._queue(chat_id, _LATE + last)

    async def _report_orphans(self) -> None:
        """Name what a crash lost, rather than retrying it or staying silent (decision T, TG-29).

        The notice goes to **the chat that lost the message**, with that chat's own count. It used
        to broadcast a total to every mapped chat whose id happened to be in the owner allow-list,
        which for the ordinary deployment is no chat at all — measured, zero notices for a real
        orphan, which is the silent loss the rule names as unacceptable.
        """
        lost = await self.store.orphans()
        if not lost:
            return
        _log.warning("telegram: %d update(s) were claimed but never started", len(lost))
        counts: dict[int, int] = {}
        for _update_id, chat_id in lost:
            if chat_id is not None and chat_id in self.config.chats:
                counts[chat_id] = counts.get(chat_id, 0) + 1
        for chat_id, count in sorted(counts.items()):
            await self._announce(chat_id, _ORPHANS.format(count=count))
        for update_id, _chat_id in lost:
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


def _chat_of_query(query: Mapping[str, Any]) -> int | None:
    message = query.get("message") or {}
    chat = message.get("chat") or {}
    return int(chat["id"]) if "id" in chat else None


def _chat_of(update: Mapping[str, Any]) -> int | None:
    for key in ("message", "edited_message"):
        if key in update:
            return int(update[key]["chat"]["id"])
    if "callback_query" in update:
        return _chat_of_query(update["callback_query"])
    return None


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

_UNMAPPED: Final = (
    "This chat is not connected to anything, so I have not kept this message and nothing has been "
    "filed.\n\nTo connect it, add chat {chat_id} to the daemon's Telegram configuration."
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
