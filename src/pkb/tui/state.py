"""What a run looks like to a client — pure, and testable without a terminal (TU-24 … TU-36).

Almost every rule about the run view is a rule about *state* rather than about pixels: which message
replaces which, which pane a frame belongs to, when a branch stops spinning, what the four terminal
states are. Keeping them here rather than inside a widget is what makes them assertable without a
pilot, and what stops the next widget from answering them differently.

Four of them are corrections to what a naive client does:

* **``message.complete`` replaces the delta buffer; it is never appended** (TU-24). Layer 2's token
  channel and fact channel are independent and its dedup is by message id *within* the fact channel,
  so every assistant message arrives **twice** — once as deltas, once complete. Layer 3 is forbidden
  from fixing that (SS-13), so the client must, or every reply renders twice.
* **``run.end.final_text`` is never a message** (TU-25). The runtime yields ``MessageComplete(text)``
  immediately followed by ``RunEnd(final_text)`` carrying the *identical* string; rendering it puts a
  third copy of the reply directly under the second.
* **``subagent.*`` are brackets over concurrent branches, not nesting** (TU-27), and they need not
  balance — the terminal frame closes every branch still open. A spinner that only clears on
  ``subagent.end`` spins forever on the branch that raised the approval or failed.
* **Frames are routed by their envelope ``thread_id``** (TU-26), never by re-deriving one from
  ``agent_id``. The server's derivation is gated on the catalog, which is what keeps an expert's own
  ``general-purpose`` delegation on the expert's thread; a client re-deriving would invent a thread
  for it and split one conversation across two panes.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from pkb.clients.sse import Frame
from pkb.contracts import (
    CANCELLED_CODE,
    ApprovalRequest,
    InterruptEvent,
    MessageComplete,
    MessageDelta,
    RunEnd,
    RunError,
    SubagentEnd,
    SubagentStart,
    ToolEnd,
    ToolStart,
)

__all__ = [
    "Branch",
    "Entry",
    "RunView",
    "Terminal",
]

Terminal = Literal["running", "completed", "interrupted", "cancelled", "error", "unknown"]
"""The five endings plus the one non-ending.

``unknown`` is not a server value: it is what a client records when a stream ends **without** a
terminal frame (SS-7, TU-33), which is the primary shutdown path rather than a rare one. It is
deliberately not collapsed into ``error`` — assuming failure invites a retry of a turn that may
still be writing, and assuming success leaves a pending approval invisible.
"""

EntryKind = Literal["human", "message", "tool", "note", "offer"]

_ELAPSED_QUIET: Final = 8.0
"""Seconds of silence before the UI starts showing elapsed time (TU-30).

Below a model call's own pace, so it appears when something is genuinely slow rather than on every
turn. It never becomes an assertion that the run is hung: a filing turn is 8-12 calls at ~16 s each,
and a silent failover to the local model makes 284 s a *correct* turn.
"""


@dataclass
class Entry:
    """One rendered line or block in a transcript, in arrival order."""

    kind: EntryKind
    agent_id: str
    text: str
    error: bool = False
    thread_id: str = ""
    """For an ``offer``: the derived thread "continue with this expert" resumes (TU-18)."""

    title: str = ""


@dataclass
class Branch:
    """One expert's concurrent branch of a fan-out — a bracket, not a nesting level (TU-27)."""

    agent_id: str
    thread_id: str
    status: str = "running"
    open: bool = True

    @property
    def spinning(self) -> bool:
        return self.open and self.status == "running"


@dataclass
class RunView:
    """Everything one turn shows, assembled frame by frame.

    Fed by :meth:`apply` and read by the widgets. Holds no Textual object, so the whole of the run
    view's behaviour is assertable without a terminal.
    """

    thread_id: str
    agent_id: str
    run_id: str = ""
    """From the most recent ``run.started`` — **the cancel target** (TU-36, decision M).

    A resume mints a *new* run id, and ``DELETE /runs/{unknown}`` is a deliberate 204, so a client
    holding the pre-interrupt id cancels nothing and reports success.
    """

    entries: list[Entry] = field(default_factory=list)
    branches: dict[str, Branch] = field(default_factory=dict)
    terminal: Terminal = "running"
    code: str | None = None
    retryable: bool = False
    pending: ApprovalRequest | None = None
    last_frame_at: float = field(default_factory=time.monotonic)
    _buffers: dict[str, int] = field(default_factory=dict, repr=False)
    """``(run_id, agent_id)`` → the index of its open delta entry, so a ``complete`` can replace it."""

    # -- the pump feeds this ----------------------------------------------------------

    def apply(self, frame: Frame) -> ApprovalRequest | None:
        """Fold one frame in. Returns an approval when this frame raised one (TU-23).

        The caller **queues** that approval for a different worker rather than awaiting a decision
        here: a consumer that stops consuming while a human reads a diff is a consumer the hub drops
        (``SUBSCRIBER_QUEUE_SIZE`` is 256 and a human takes minutes), and the drop closes the stream
        with no terminal frame — so the loss would look like an unknown outcome.
        """
        self.last_frame_at = time.monotonic()
        if frame.handle is not None:  # run.started
            self.run_id = frame.handle.run_id
            return None

        event = frame.event
        if event is None:
            return None
        if isinstance(event, SubagentStart):
            self._open_branch(event.agent_id, frame.thread_id)
        elif isinstance(event, SubagentEnd):
            self._close_branch(event.agent_id, event.status)
        elif isinstance(event, MessageDelta):
            self._delta(event)
        elif isinstance(event, MessageComplete):
            self._complete(event)
        elif isinstance(event, ToolStart):
            self.entries.append(Entry(kind="tool", agent_id=event.agent_id, text=event.summary))
        elif isinstance(event, ToolEnd):
            self.entries.append(
                Entry(kind="tool", agent_id=event.agent_id, text=event.summary, error=event.error)
            )
        elif isinstance(event, InterruptEvent):
            self.pending = event.request
            return event.request
        elif isinstance(event, RunEnd | RunError):
            self._finish(frame, event)
        return None

    def ended(self, terminal: Terminal = "unknown") -> None:
        """Called when the stream stopped without telling us how it ended (TU-33).

        Assuming completion leaves the transcript wrong and a pending approval invisible; assuming
        failure invites a retry of a turn that may still be writing. So it says so, and the caller
        re-reads the thread.
        """
        if self.terminal == "running":
            self.terminal = terminal
        self._close_open_branches("unknown")

    # -- what the widgets read --------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.terminal == "running"

    @property
    def quiet_for(self) -> float:
        """Seconds since the last domain frame — a **local** clock, never the ping (TU-30, DC-8)."""
        return time.monotonic() - self.last_frame_at

    @property
    def waiting_note(self) -> str:
        """What to show during a long gap. Never the word "hung"."""
        if not self.running or self.quiet_for < _ELAPSED_QUIET:
            return ""
        waiting = [b.agent_id for b in self.branches.values() if b.spinning] or [self.agent_id]
        return f"waiting on {', '.join(waiting)} — {int(self.quiet_for)}s"

    @property
    def offers(self) -> list[Entry]:
        """ "Continue with the `<X>` expert" links, from the **envelope**, never from prose (TU-18)."""
        return [entry for entry in self.entries if entry.kind == "offer"]

    def messages_for(self, agent_id: str) -> list[Entry]:
        return [e for e in self.entries if e.kind == "message" and e.agent_id == agent_id]

    # -- internals --------------------------------------------------------------------

    def _open_branch(self, agent_id: str, thread_id: str) -> None:
        self.branches[agent_id] = Branch(agent_id=agent_id, thread_id=thread_id)
        if thread_id and thread_id != self.thread_id:
            # TU-18: the offer's target is the envelope's thread id. Parsing the merged reply for it
            # would make `merge_reply`'s rendering a wire protocol; and once the reply scrolls away
            # the live frames are gone, which is why `children` is the second source and text never.
            self.entries.append(
                Entry(kind="offer", agent_id=agent_id, text="", thread_id=thread_id)
            )

    def _close_branch(self, agent_id: str, status: str) -> None:
        branch = self.branches.get(agent_id)
        if branch is None:
            branch = Branch(agent_id=agent_id, thread_id="")
            self.branches[agent_id] = branch
        branch.status = status
        branch.open = False

    def _close_open_branches(self, status: str) -> None:
        """The terminal frame closes every branch still open (TU-27).

        The brackets need not balance: an expert that raised an approval or failed may never emit
        ``subagent.end`` at all, and a UI that waits for one leaves that branch spinning forever —
        on precisely the branch the human needs to look at.
        """
        for branch in self.branches.values():
            if branch.open:
                branch.open = False
                if branch.status == "running":
                    branch.status = status

    def _delta(self, event: MessageDelta) -> None:
        key = f"{event.run_id}\x00{event.agent_id}"
        index = self._buffers.get(key)
        if index is None:
            self.entries.append(Entry(kind="message", agent_id=event.agent_id, text=event.text))
            self._buffers[key] = len(self.entries) - 1
            return
        self.entries[index].text += event.text

    def _complete(self, event: MessageComplete) -> None:
        """**Replace**, never append (TU-24).

        The deltas and the complete are the same message arriving on two independent channels. The
        complete wins even when it differs from the concatenation — it is the fact channel, and a
        client that keeps the tokens is keeping the draft.
        """
        key = f"{event.run_id}\x00{event.agent_id}"
        index = self._buffers.get(key)
        if index is None:
            self.entries.append(Entry(kind="message", agent_id=event.agent_id, text=event.text))
            self._buffers[key] = len(self.entries) - 1
            return
        self.entries[index].text = event.text

    def _finish(self, frame: Frame, event: RunEnd | RunError) -> None:
        """The four terminal states, with **no fall-through** (TU-31, SS-9).

        ``run.end.final_text`` is deliberately not rendered: the runtime emits ``MessageComplete``
        with the identical string immediately before it, so rendering both shows the reply twice.
        """
        if isinstance(event, RunError):
            self.code = frame.code
            self.retryable = event.retryable
            self.terminal = "cancelled" if frame.code == CANCELLED_CODE else "error"
            if self.terminal == "error":
                self.entries.append(
                    Entry(kind="note", agent_id=self.agent_id, text=event.message, error=True)
                )
        else:
            status = frame.status
            self.terminal = "interrupted" if status == "interrupted" else "completed"
        self._close_open_branches(self.terminal)


def replay(detail: Mapping[str, object], agent_id: str) -> list[Entry]:
    """The conversation, from ``GET /threads/{id}`` — **authoritative on open** (TU-19, decision L).

    Live per-expert frames are additive for the run in flight only. Replaying a Librarian thread
    returns its own graph's messages plus the appended merged reply; the experts' deltas and tool
    calls live in the derived threads' checkpoints and are simply not in that history. A UI whose
    live view is richer than its replayed view teaches the human that reopening loses information,
    when it is one click away in ``children``.
    """
    out: list[Entry] = []
    for message in _sequence(detail.get("messages")):
        role = str(message.get("role", ""))
        out.append(
            Entry(
                kind="human" if role == "human" else "message",
                agent_id="" if role == "human" else agent_id,
                text=str(message.get("text", "")),
            )
        )
    return out


def _sequence(value: object) -> Iterator[Mapping[str, object]]:
    if isinstance(value, Iterable) and not isinstance(value, str | bytes):
        for item in value:
            if isinstance(item, Mapping):
                yield item


def offers_from_children(children: Sequence[Mapping[str, object]]) -> list[Entry]:
    """ "Continue with the expert" offers reconstructed after a reload (TU-18).

    The second of the two sources, and the reason text is never one: live frames are gone once the
    reply scrolls away, and ``children`` is where the parentage survives.
    """
    return [
        Entry(
            kind="offer",
            agent_id=str(child.get("agent_id", "")),
            text="",
            thread_id=str(child.get("thread_id", "")),
            title=str(child.get("title") or ""),
        )
        for child in children
    ]
