"""``AgentEvent`` → SSE frame. One table, one direction, no interpretation (SS-3 … SS-16).

The transport's whole job on the event path is **envelope and encode**. Layer 3 does not
deduplicate, filter, enrich, reorder, coalesce or batch: interrupt deduplication and message dedup
already happen in Layer 2's normalizer (RT-41, RT-43), and a second pass in the transport can only
diverge from the first (SS-13).

Three things this module *does* compute, and each is a stated rule rather than a convenience:

* **``thread_id`` on every frame** (SS-10). Only :class:`~pkb.contracts.InterruptEvent` carries one
  today; the rest carry ``agent_id`` alone, so without this every client re-implements LB-14's
  derivation in its own language. The derivation is total and gated on the catalog — which is what
  keeps an expert's own ``general-purpose`` delegation, whose agent id is not a catalog id, on the
  expert's own thread rather than inventing a thread for it (RT-44).
* **``run.end.status``** (SS-9). The harness's ``astream`` returns normally when a graph interrupts,
  so ``run_end`` is emitted either way. A run that emitted ``interrupt`` and then ``run.end`` is
  **parked, not complete**, and getting this wrong shows up as a client saying "done" over a thread
  waiting on a human.
* **``seq``** (SS-5), a per-run monotonic cursor starting at 0 — a within-run cursor, not a global
  one, which is what makes the replay buffer additive rather than a redesign.

**Frames are handed to ``sse-starlette`` as :class:`ServerSentEvent` objects, never as dicts and
never as pre-formatted strings.** ``ensure_bytes`` *mutates* a yielded dict — the caller's object
comes back with a ``sep`` key added — so a replay buffer holding the same dicts it handed the
response would replay mutated objects; and an unknown key raises ``TypeError`` at
``ServerSentEvent.__init__``. Framing is the library's job: it emits CRLF-separated fields in the
fixed order *comment, id, event, data, retry*, whatever order they were constructed in.

**No ``retry:`` field is ever written.** Both streams are responses to a POST, which the browser's
``EventSource`` cannot issue, so they are deliberately not ``EventSource``-compatible. Reconnection
is an explicit client act against ``GET /threads/{id}/events``, never a transport-level auto-retry
that would silently re-POST a run.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable, Mapping
from typing import Any, Final

from sse_starlette import ServerSentEvent

from pkb.contracts import (
    CANCELLED_CODE,
    EVENT_NAMES,
    RUN_ERROR_CODE,
    RUN_STARTED_EVENT,
    AgentEvent,
    InterruptEvent,
    RunEnd,
    RunError,
    RunHandle,
    expert_thread_id,
    terminal_status,
)

__all__ = [
    "ENVELOPE_KEYS",
    "RunStatus",
    "SseEncoder",
    "encode_frame",
    "event_name",
    "thread_for_event",
]

ENVELOPE_KEYS: Final = ("type", "seq", "run_id", "thread_id", "agent_id")
"""The flat envelope merged over ``dataclasses.asdict(event)`` (SS-4).

Flat rather than nested so a frame is matchable to its dataclass in ``pkb.contracts`` with no
mapping table on the client side. A test asserts **no envelope key collides with any field name**
across the union — ``run_id`` and ``agent_id`` are deliberately the same key in both, carrying the
same value, and a *third* meaning appearing under one of these names is the failure to catch.
"""

RunStatus = str
"""``completed`` | ``interrupted`` | ``cancelled`` — computed by Layer 3 from what it saw (SS-9)."""

# `CANCELLED_CODE` and `RUN_ERROR_CODE` come from the seam (decision P): four things have to agree
# on these strings and two of them may not import this module.


def event_name(event: AgentEvent) -> str:
    """The wire name for one event, from the seam's total table (SS-3).

    ``KeyError`` rather than a default: the table is asserted total at import, so reaching this
    branch means the union grew and the assertion was removed — and a silent default is exactly how
    a new event kind vanishes from every client without anyone noticing.
    """
    return EVENT_NAMES[type(event)]


def thread_for_event(event: AgentEvent, handle: RunHandle, catalog: Iterable[str]) -> str:
    """Which thread this frame belongs to (SS-10).

    Total, deterministic, and gated on the catalog:

    * the run's own thread when the event's agent is the run's agent;
    * ``expert_thread_id(run.thread_id, event.agent_id)`` when the agent resolves in the catalog —
      the fan-out case, where a delegate really does run on its own derived thread (LB-14);
    * the run's own thread otherwise — which is the ``general-purpose`` case (RT-44): an expert's
      internal delegation runs in a nested namespace under the *same* thread and must not be given
      a thread of its own.

    :class:`~pkb.contracts.InterruptEvent` is the one event that already knows: its nested
    ``ApprovalRequest.thread_id`` is authoritative, because the approval parks on whichever thread
    raised it and Layer 3 must not second-guess that (LB-16).
    """
    if isinstance(event, InterruptEvent):
        return event.request.thread_id
    agent_id = getattr(event, "agent_id", None)
    if not agent_id or agent_id == handle.agent_id:
        return handle.thread_id
    return expert_thread_id(handle.thread_id, agent_id) if agent_id in catalog else handle.thread_id


def _agent_of(event: AgentEvent) -> str | None:
    """Whose event this is.

    Most events carry ``agent_id``; ``RunEnd`` and ``RunError`` carry none and belong to the run's
    own agent. :class:`~pkb.contracts.InterruptEvent` carries none *either*, and its agent is the one
    inside the nested request — the expert that raised the gate, which in a fan-out is not the agent
    the client is streaming. Falling back to the run's agent there would label an expert's approval
    with the Librarian's name, on the one frame a human acts on.
    """
    if isinstance(event, InterruptEvent):
        return event.request.agent_id
    return getattr(event, "agent_id", None)


def encode_frame(name: str, seq: int, payload: Mapping[str, Any]) -> ServerSentEvent:
    """One SSE frame: ``id``, ``event``, and a **single line** of compact JSON (SS-4, SS-5).

    ``json.dumps`` escapes newlines, so a multi-line ``data:`` can never arise — which is what keeps
    the framing unambiguous without the encoder having to know anything about the payload. The
    object is returned rather than a formatted string: ``sse-starlette`` owns the wire bytes, and a
    second formatter here would be a second answer to "what does a frame look like".
    """
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str)
    return ServerSentEvent(id=str(seq), event=name, data=body)


class SseEncoder:
    """One run's frames, in order, with the envelope Layer 3 owns.

    Stateful because ``seq`` and ``run.end.status`` both are: the status of a ``run.end`` depends on
    whether an ``interrupt`` was seen earlier on *this* stream, which is precisely the thing a
    stateless encoder cannot know and a client should not have to reconstruct.
    """

    def __init__(
        self,
        handle: RunHandle,
        catalog: Iterable[str] = (),
        codes: Mapping[str, str] | None = None,
    ) -> None:
        self.handle = handle
        self._catalog = frozenset(catalog)
        self._codes = codes
        self._seq = 0
        self._interrupted = False

    @property
    def seq(self) -> int:
        """The next sequence number — 0 before anything is written."""
        return self._seq

    @property
    def interrupted(self) -> bool:
        """Whether this run parked on an approval, which is what ``run.end.status`` reports."""
        return self._interrupted

    def started(self) -> ServerSentEvent:
        """Frame 0: ``run.started``, carrying exactly :class:`~pkb.contracts.RunHandle` (SS-8).

        Written before any event is relayed, so the client has the run id (for cancel) and the agent
        id (for its header) before the first token — and cancelling is never a race with a run that
        has not yet emitted anything.
        """
        return self._frame(
            RUN_STARTED_EVENT,
            {
                "run_id": self.handle.run_id,
                "thread_id": self.handle.thread_id,
                "agent_id": self.handle.agent_id,
            },
        )

    def event(self, event: AgentEvent) -> ServerSentEvent:
        """One :data:`~pkb.contracts.AgentEvent` as a frame, envelope merged over its fields."""
        if isinstance(event, InterruptEvent):
            self._interrupted = True
        payload: dict[str, Any] = dict(dataclasses.asdict(event))
        payload["agent_id"] = _agent_of(event) or self.handle.agent_id
        thread_id = thread_for_event(event, self.handle, self._catalog)
        if isinstance(event, RunEnd | RunError):
            # **Both** terminal kinds carry a status, and a `run.error` carries a machine `code`
            # besides. Only `RunEnd` used to, which left a cancelled run's terminal frame with
            # neither — so a client had to string-match the sentence "the run was cancelled" to tell
            # a cancellation from a provider failure, and SS-9's third status was unreachable on the
            # wire (a cancelled run never emits `run.end` at all: Layer 2 re-raises CancelledError).
            payload["status"] = self.status_for(event)
            if isinstance(event, RunError):
                payload["code"] = self._code_for(event, payload["status"])
        return self._frame(event_name(event), payload, thread_id=thread_id)

    def _code_for(self, event: RunError, status: str) -> str:
        """The machine code on a terminal error frame (SS-15, AP-11).

        A cancellation is not a failure, so it gets its own code and a client says nothing rather
        than offering "try again". Otherwise the code is the one the supervisor recorded from the
        exception's own type — which is what lets a client tell "the thread is busy, wait" from
        "this run died" without reading the sentence in ``message``.
        """
        if status == "cancelled":
            return CANCELLED_CODE
        recorded = self._codes.get(event.run_id) if self._codes else None
        return recorded or RUN_ERROR_CODE

    def status_for(self, event: RunEnd | RunError) -> RunStatus:
        """One of :data:`~pkb.contracts.RUN_STATUSES` — **four** values, not three (SS-9, amended).

        ``completed`` and ``interrupted`` ride on ``run.end``; ``cancelled`` and ``error`` ride on
        ``run.error``, because a cancelled run never emits ``run.end`` at all — Layer 2 re-raises
        ``CancelledError`` without a terminal event. A client matching three ways either raises or
        falls through to "done" on every provider failure, which is the most common failure a human
        sees.

        The :class:`~pkb.contracts.RunEnd` dataclass is **unmodified** — this is an envelope field.
        Layer 3 computes it from what it saw on the stream, because Layer 2 cannot: ``astream``
        returns normally on an interrupt and the harness has no notion of "parked".
        """
        return terminal_status(event, interrupted=self._interrupted)

    def cancelled(self) -> ServerSentEvent:
        """The farewell frame a stream writes when the daemon is shutting down (AP-11, AP-12).

        The same shape the supervisor synthesizes for a cancelled run, because it is the same fact
        from the client's side: the run ended and nothing more is coming. Without it a shutdown
        merely cancels the generator and every attached client is left on a stream that stopped
        mid-sentence, with no way to tell that from a slow model.
        """
        return self._frame(
            EVENT_NAMES[RunError],
            {
                "run_id": self.handle.run_id,
                "message": "the daemon is shutting down; the run was cancelled",
                "retryable": True,
                "code": CANCELLED_CODE,
                "status": "cancelled",
            },
        )

    def _frame(
        self, name: str, payload: Mapping[str, Any], thread_id: str | None = None
    ) -> ServerSentEvent:
        envelope = {
            "type": name,
            "seq": self._seq,
            "run_id": self.handle.run_id,
            "thread_id": thread_id or self.handle.thread_id,
            "agent_id": payload.get("agent_id") or self.handle.agent_id,
        }
        frame = encode_frame(name, self._seq, {**payload, **envelope})
        self._seq += 1
        return frame
