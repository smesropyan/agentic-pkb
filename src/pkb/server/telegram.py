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

_DROPPED: Final[tuple[DecisionType, ...]] = ("edit",)
"""What this channel narrows away (CL-9). Editing a document on a phone is impractical, and the
human is directed to the TUI for anything that needs one."""

_HANDLE_BYTES: Final = 4
_VERSION: Final = "v1"
_APPROVE, _REJECT, _CONFIRM = "a", "r", "c"
_OUTBOX_SIZE: Final = 64


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
    """
    if utf16_len(text) <= limit:
        return text, False
    budget = limit
    while budget > 0:
        candidate, _ = truncate(text, budget)
        candidate = candidate.removesuffix("\n… (truncated — open the TUI for the whole diff)")
        if utf16_len(candidate + marker) <= limit:
            return candidate + marker, True
        budget = int(budget * 0.8)
    return text[: limit // 2] + marker, True


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split on **length, never on meaning** (TG-45).

    Cuts fall on line boundaries, in order, with nothing summarized, reflowed, reordered or dropped —
    the parts reassemble byte-identically. LB-18 exists because a model composing a reply claimed an
    expert had checked the knowledge base when none ran; a transport that cut on meaning would be
    the same lie one layer down. A length cut can be wrong; it cannot be a lie.
    """
    if utf16_len(text) <= limit:
        return [text]
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
    kinds = offered(action, drop=_DROPPED)
    if not kinds:
        return None
    labels = {"approve": "Approve", "reject": "Reject", "respond": "Respond"}
    verbs = {"approve": _APPROVE, "reject": _REJECT, "respond": "s"}
    return [
        [
            {
                "text": labels.get(kind, kind.title()),
                "callback_data": callback_data(handle, index, verbs[kind]),
            }
        ]
        for kind in kinds
    ]


@dataclass
class TelegramConfig:
    """Deployment configuration. **Never** read from the knowledge base (I3, TG-5)."""

    token: str = ""
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
    _outbox: asyncio.Queue[tuple[int, str, Any]] = field(
        default_factory=lambda: asyncio.Queue(_OUTBOX_SIZE)
    )

    async def run(self) -> None:
        """Poll, dispatch and send — all inside one task group (TG-7).

        The group is the point: ``_supervise`` awaits this coroutine and has no handle on anything
        it spawns, so a detached child would survive a crash and the restart would add another. With
        a group, a failure anywhere cancels the rest and the supervisor gets a clean slate.
        """
        await self.store.setup()
        await self._report_orphans()
        async with asyncio.TaskGroup() as group:
            group.create_task(self._pump_outbox(), name="pkb-telegram-outbox")
            group.create_task(self._poll(), name="pkb-telegram-poll")

    # -- inbound ----------------------------------------------------------------------

    async def _poll(self) -> None:
        offset = await self.store.next_offset()
        while True:
            # Bound explicitly: `offset` is reassigned below, and a lambda closing over the
            # loop variable would poll from whatever it had become by the time a retry fired.
            updates = await with_retry(lambda at=offset: self.api.get_updates(at))
            for update in updates:
                update_id = int(update["update_id"])
                offset = update_id + 1
                kind = next((k for k in update if k != "update_id"), "unknown")
                # Claimed BEFORE dispatch, so a redelivery cannot re-run a turn that already wrote.
                if not await self.store.claim(update_id, _chat_of(update), kind):
                    continue
                with contextlib.suppress(Exception):
                    await self._dispatch(update)
                await self.store.dispatched(update_id)

    async def _dispatch(self, update: Mapping[str, Any]) -> None:
        if "callback_query" in update:
            await self._on_callback(update["callback_query"])
        elif "message" in update:
            await self._on_message(update["message"])
        elif "edited_message" in update:
            # TG-35: the turn on the original has already run and may already have written.
            await self._say(
                int(update["edited_message"]["chat"]["id"]),
                "I already acted on the original — send the correction as a new message.",
            )

    async def _on_message(self, message: Mapping[str, Any]) -> None:
        chat_id = int(message["chat"]["id"])
        sender = int(message.get("from", {}).get("id", 0))

        if message["chat"].get("type") != "private":
            return  # TG-8: a group is many senders with no identity check, and there is no auth
        agent_id = self.config.chats.get(chat_id)
        if agent_id is None:
            # TG-9: the chat id and where to add it. No agent ids — the bot's username is
            # discoverable, and a listing sent to a stranger leaks the shape of a private KB.
            await self._say(chat_id, _UNMAPPED.format(chat_id=chat_id))
            return
        if sender not in self.config.owner_user_ids:
            await self._say(
                chat_id, "This knowledge base does not accept messages from this account."
            )
            return

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
            await self._command(chat_id, agent_id, text.strip())
            return
        await self._turn(chat_id, agent_id, text)

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

    async def _turn(self, chat_id: int, agent_id: str, text: str) -> None:
        thread_id = await self.store.bound_thread(chat_id)
        if thread_id is None:
            thread = await self.service.create_thread(agent_id, origin_channel="telegram")
            thread_id = thread.thread_id
            await self.store.bind(chat_id, thread_id, agent_id)
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
        await self._consume(chat_id, subscription)

    # -- the run ----------------------------------------------------------------------

    async def _consume(self, chat_id: int, subscription: Any) -> None:
        """Relay one run into the chat. The pump never blocks on a Bot API call (TG-49)."""
        interrupted = False
        roster: list[str] = []
        terminal: RunEnd | RunError | None = None
        try:
            async for event in subscription.events:
                if isinstance(event, MessageComplete):
                    # TG-41: the fact channel only. Deltas arrive too, and editing per token is
                    # hundreds of calls a second against a one-per-second budget.
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
        finally:
            # TG-52: synchronous, and never awaited — the same idiom the routes and MCP use.
            close = getattr(subscription, "close", None)
            if callable(close):
                close()

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
            await self._post_action(chat_id, handle, index, action)

    async def _post_action(self, chat_id: int, handle: str, index: int, action: ActionView) -> None:
        description = action.description
        if utf16_len(description) > MESSAGE_LIMIT:
            # The whole thing arrives first, as a file. A delete embeds the entire current file and
            # a new-file write embeds the whole proposal; truncating hides half of it behind an
            # irreversible button.
            with contextlib.suppress(TelegramError):
                await self.api.send_document(
                    chat_id,
                    f"{action.tool}-{index}.diff"
                    if is_diff(description)
                    else f"{action.tool}-{index}.txt",
                    description.encode("utf-8"),
                    caption=f"{action.tool} · {action.reason}",
                )
        else:
            await self._say(chat_id, description)

        preview, _ = fit(description, 1200, marker="\n… (full text above)")
        lines = []
        label = _validation_line(description)
        if label:
            lines.append(label)  # TG-66: above everything, not at the bottom of 9,000 characters
            lines.append("Approving this will still fail validation.")
        lines.append(f"{action.tool} · {action.reason}")
        if action.reason in NO_UNDO_REASONS:
            lines.append("There is no undo for this.")
        lines.append("")
        lines.append(preview)

        keyboard = keyboard_for(action, handle, index)
        if keyboard is None:
            # TG-55: a hand-off, never an empty keyboard. `validate_decisions` would reject every
            # type, so an approval nobody can answer parks the thread and RT-39 bricks the chat.
            await self._say(chat_id, "\n".join(lines) + _NO_DECISIONS)
            return
        sent = await self._send(chat_id, "\n".join(lines), keyboard)
        message_id = sent.get("message_id") if isinstance(sent, Mapping) else None
        if isinstance(message_id, int):
            await self.store.record_message(handle, message_id)

    async def _on_callback(self, query: Mapping[str, Any]) -> None:
        """A button press. **Answer the callback first, unconditionally** (TG-61).

        A resume starts a turn of 8-12 model calls. Answer it afterwards and the button spins, the
        query expires, and the human — who has no other feedback — presses again, producing a second
        press against an interrupt the first already resolved.
        """
        callback_id = str(query.get("id", ""))
        with contextlib.suppress(TelegramError):
            await self.api.answer_callback(callback_id)

        sender = int(query.get("from", {}).get("id", 0))
        if sender not in self.config.owner_user_ids:
            return
        parsed = parse_callback(str(query.get("data", "")))
        if parsed is None:
            return
        handle, index, verb = parsed

        prompt = await self.store.prompt(handle)
        if prompt is None or prompt["resolved"]:
            # TG-62/TG-63: a message lives in the chat forever with its buttons live. A press a week
            # later must not answer whatever interrupt is pending now.
            await self._say(int(query["message"]["chat"]["id"]), _STALE)
            await self._clear_keyboard(prompt)
            return

        chat_id = int(prompt["chat_id"])
        try:
            if verb in {_APPROVE, _REJECT} and await self._needs_confirm(prompt, index):
                await self._ask_confirm(chat_id, handle, index, verb, query)
                return

            answers = await self.store.record_answer(handle, index, _CONFIRM_VERBS.get(verb, verb))
            if len(answers) < int(prompt["action_count"]):
                return  # TG-60: a partial set submits nothing, and the interrupt stays parked
            await self._resolve(prompt, answers)
        except Exception as exc:
            # The poll loop suppresses exceptions so one bad update cannot stop the bot — which
            # means a failure here would be *silent*, and the human would press again on a keyboard
            # that has already stopped working. Saying so is the whole difference.
            _log.warning("telegram: a button press could not be applied", exc_info=True)
            await self._say(chat_id, _PRESS_FAILED.format(reason=exc))

    async def _resolve(self, prompt: Mapping[str, Any], answers: Mapping[int, str]) -> None:
        """Re-read the live approval, then resolve (TG-58).

        The durable row supplies the thread; the *server* supplies the request. Any in-memory map is
        a cache and never the authority — which is what makes a press that arrives after a restart
        work, and makes the restart case exercised by every test.
        """
        chat_id = int(prompt["chat_id"])
        thread_id = str(prompt["thread_id"])
        detail = await self.service.get_thread(thread_id)
        request = detail.pending
        if request is None or request.interrupt_id != prompt["interrupt_id"]:
            await self._say(chat_id, _STALE)
            await self.store.resolve_prompt(prompt["handle"])
            await self._clear_keyboard(prompt)
            return

        built = {
            index: Answer(type="approve" if verb == _APPROVE else "reject")
            for index, verb in answers.items()
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
        except Exception as exc:
            await self._say(chat_id, f"That approval could not be applied: {exc}")
            return
        finally:
            await self.store.resolve_prompt(prompt["handle"])
            await self._clear_keyboard(prompt)
        await self._consume(chat_id, subscription)

    async def _needs_confirm(self, prompt: Mapping[str, Any], index: int) -> bool:
        detail = await self.service.get_thread(str(prompt["thread_id"]))
        request = detail.pending
        if request is None or index >= len(request.actions):
            return False
        return request.actions[index].reason in NO_UNDO_REASONS

    async def _ask_confirm(
        self, chat_id: int, handle: str, index: int, verb: str, query: Mapping[str, Any]
    ) -> None:
        """A second tap for the three destructive reasons (TG-64)."""
        keyboard = [
            [
                {
                    "text": "Yes, do it",
                    "callback_data": callback_data(handle, index, _CONFIRM + verb),
                }
            ],
            [{"text": "Cancel", "callback_data": callback_data(handle, index, "x")}],
        ]
        await self._send(chat_id, "There is no undo for this. Confirm?", keyboard)

    async def _clear_keyboard(self, prompt: Mapping[str, Any] | None) -> None:
        """Every message of the approval loses its buttons (TG-63)."""
        if prompt is None:
            return
        for message_id in prompt.get("message_ids", []):
            with contextlib.suppress(TelegramError):
                await self.api.edit_message(
                    int(prompt["chat_id"]), int(message_id), "This approval has been answered."
                )

    async def _repost_pending(self, chat_id: int, thread_id: str) -> None:
        """Re-post whatever is parked, including a fan-out gate on a child (TG-53).

        Never concludes "no approval" from the parent's ``pending is None``: LB-16 parks an expert's
        gate on its own derived thread, so the parent's is null while the child holds the interrupt.
        """
        detail = await self.service.get_thread(thread_id)
        if detail.pending is not None:
            await self._post_approval(chat_id, detail.pending)
            return
        for child in detail.children:
            child_detail = await self.service.get_thread(child.thread_id)
            if child_detail.pending is not None:
                await self._post_approval(chat_id, child_detail.pending)
                return

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
        thread_id = await self.store.bound_thread(chat_id)
        if thread_id is None:
            await self._say(chat_id, "Nothing is running here.")
            return
        subscription = await self.service.attach(thread_id)
        if subscription is None:
            await self._say(chat_id, "Nothing is running here.")
            return
        await self.service.cancel(subscription.handle.run_id)
        await self._say(chat_id, "Cancelled.")

    # -- outbound ---------------------------------------------------------------------

    async def _queue(self, chat_id: int, text: str) -> None:
        """Hand a message to the outbox. Drops **progress** only, never a decision (TG-49)."""
        try:
            self._outbox.put_nowait((chat_id, text, None))
        except asyncio.QueueFull:
            _log.warning("telegram outbox full; dropping a progress message for chat %s", chat_id)

    async def _say(self, chat_id: int, text: str) -> None:
        await self._send(chat_id, text, None)

    async def _send(
        self, chat_id: int, text: str, keyboard: list[list[dict[str, str]]] | None
    ) -> Mapping[str, Any]:
        """Send now, split on length, honouring a 429 (TG-45, TG-48).

        This retry is a **transport** retry of an idempotent send, and is explicitly not the run
        retry the TUI forbids: a dropped reply after an approved write means the human never learns
        what was written, and a dropped keyboard means a parked interrupt nobody is told about.
        """
        parts = split_message(text)
        sent: Mapping[str, Any] = {}
        for position, part in enumerate(parts):
            label = f"({position + 1}/{len(parts)})\n" if len(parts) > 1 else ""
            last = position == len(parts) - 1
            sent = await with_retry(
                lambda part=part, label=label, last=last: self.api.send_message(
                    chat_id, label + part, keyboard=keyboard if last else None
                )
            )
        return sent

    async def _pump_outbox(self) -> None:
        while True:
            chat_id, text, _ = await self._outbox.get()
            with contextlib.suppress(TelegramError):
                await self._send(chat_id, text, None)

    async def _report_orphans(self) -> None:
        """Name what a crash lost, rather than retrying it or staying silent (decision T)."""
        lost = await self.store.orphans()
        if not lost:
            return
        _log.warning("telegram: %d update(s) were claimed but never dispatched", len(lost))
        for chat_id in {c for c in self.config.chats if c in self.config.owner_user_ids}:
            await self._say(chat_id, _ORPHANS.format(count=len(lost)))


def _chat_of(update: Mapping[str, Any]) -> int | None:
    for key in ("message", "edited_message"):
        if key in update:
            return int(update[key]["chat"]["id"])
    if "callback_query" in update:
        message = update["callback_query"].get("message") or {}
        chat = message.get("chat") or {}
        return int(chat["id"]) if "id" in chat else None
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


def _validation_line(description: str) -> str:
    for line in description.splitlines():
        if line.startswith("This draft currently fails validation:"):
            return line.strip()
    return ""


_CONFIRM_VERBS: Final = {f"{_CONFIRM}{_APPROVE}": _APPROVE, f"{_CONFIRM}{_REJECT}": _REJECT}

_UNMAPPED: Final = (
    "This chat is not connected to anything, so I have not kept this message and nothing has been "
    "filed.\n\nTo connect it, add chat {chat_id} to the daemon's Telegram configuration."
)
_ATTACHMENT: Final = "I can only read text — I have not downloaded or stored the attachment."
_NO_DECISIONS: Final = (
    "\n\nThis one cannot be answered from here. It is still waiting, and the TUI can resolve it."
)
_PRESS_FAILED: Final = (
    "I could not apply that decision: {reason}\n"
    "Nothing was sent. The approval is still waiting and the TUI can resolve it."
)
_STALE: Final = (
    "That approval was already answered — from another channel, or earlier here. Nothing was sent."
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
    "I restarted while handling {count} message(s) and cannot tell whether they ran. "
    "Nothing was retried — please re-send anything you were expecting a reply to."
)
_TERMINAL: Final = {
    "interrupted": "Waiting on your decision above.",
    "cancelled": "Cancelled.",
    "error": "That run failed. Nothing further was sent.",
    "completed": "",
}
