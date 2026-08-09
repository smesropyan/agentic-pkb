"""The wire, decoded — the exact inverse of :mod:`pkb.server.sse` (DC-1 … DC-17).

Three properties carry this module, and each is a defect that was found rather than a preference:

* **The name table is inverted from the seam's, never copied** (SS-3, DC-1). ``EVENT_NAMES`` is
  asserted total at import, so a tenth :data:`~pkb.contracts.AgentEvent` kind is an ImportError at
  startup rather than a frame that vanishes. A copied table turns that loud failure into a silently
  dropped event class, and no literal wire name appears anywhere in this package.
* **Envelope stripping is field-driven, never key-driven** (decision K, DC-2). Subtracting a fixed
  key set is what an implementer writes first, and it destroys ``SubagentEnd``: ``status`` is a
  *dataclass field* there (a delegate's outcome) and an *envelope field* on ``run.end`` (SS-9's
  parked-or-done). Keeping only keys that are fields of the target class is both correct and total —
  a new event kind decodes with zero changes here.
* **A frame is an envelope plus an event, never a bare event** (decision J, DC-3). ``thread_id``,
  ``seq``, ``status`` and ``code`` belong to no dataclass. A decoder that returned only the event
  would throw away SS-9's ``interrupted`` and AP-11's ``cancelled`` — which is precisely the bug the
  Layer 3 suite found and fixed server-side, re-introduced one layer up, and it renders a thread
  parked on a human decision as "done".

Two things this module deliberately does **not** do. It never dispatches on payload *shape*:
``MessageDelta`` and ``MessageComplete`` have identical fields, and the client must append one and
finalize the other. And an unknown event name is skipped rather than raised on — an unknown name
means the daemon is newer than the client, and killing the reader there loses the whole turn instead
of one frame.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Mapping
from typing import Any, Final, get_args

from pkb.contracts import (
    EVENT_NAMES,
    RUN_STARTED_EVENT,
    ActionView,
    AgentEvent,
    ApprovalRequest,
    DecisionType,
    InterruptEvent,
    RunEnd,
    RunError,
    RunHandle,
)

__all__ = [
    "TERMINAL",
    "Frame",
    "decode_frame",
    "decode_request",
]

_log = logging.getLogger(__name__)

_BY_NAME: Final[Mapping[str, type]] = {name: cls for cls, name in EVENT_NAMES.items()}
"""Wire name → dataclass, inverted from the seam's table once (SS-3)."""

_FIELDS: Final[Mapping[str, frozenset[str]]] = {
    name: frozenset(field.name for field in dataclasses.fields(cls))
    for name, cls in _BY_NAME.items()
}
"""Wire name → that dataclass's own field names — the only keys a constructor may be given."""

TERMINAL: Final = frozenset({EVENT_NAMES[RunEnd], EVENT_NAMES[RunError]})
"""The two names that end a stream. A client stops reading at the first one it sees (DC-14)."""


@dataclasses.dataclass(frozen=True, slots=True)
class Frame:
    """One decoded frame: the envelope Layer 3 owns, plus the event the seam defines.

    ``event`` is ``None`` only for ``run.started``, which is a transport frame carrying a
    :class:`~pkb.contracts.RunHandle` rather than an :data:`~pkb.contracts.AgentEvent` — it is
    deliberately absent from ``EVENT_NAMES``, so a decoder that only inverts the table raises on
    frame 0 of every stream.
    """

    type: str
    seq: int
    """A **per-response** ordering cursor and nothing more (SS-5 as amended, DC-16).

    The encoder is constructed per response, so two responses over one run both number from zero:
    ``(run_id, seq)`` is not an event identity, and a transcript keyed on it silently overwrites the
    first half of a resumed run.
    """

    run_id: str
    thread_id: str
    """Which conversation this frame belongs to — for a fan-out, the expert's derived thread."""

    agent_id: str
    event: AgentEvent | None = None
    handle: RunHandle | None = None
    status: str | None = None
    """One of :data:`~pkb.contracts.RUN_STATUSES`, on a terminal frame only."""

    code: str | None = None
    """A machine code on ``run.error`` only. An **open set** — match the ones you know and show
    ``message`` for the rest, because the server's table can grow without the client changing."""

    @property
    def terminal(self) -> bool:
        return self.type in TERMINAL


def decode_frame(name: str, data: str) -> Frame | None:
    """One SSE frame as a :class:`Frame`, or ``None`` when the name is unknown (DC-4).

    ``None`` rather than an exception: an unrecognised event name means the daemon is newer than
    this client, and one frame it cannot render is not a reason to abandon a turn that is otherwise
    arriving correctly.
    """
    payload = json.loads(data)
    envelope = _envelope(payload, name)

    if name == RUN_STARTED_EVENT:
        return Frame(
            **envelope,
            handle=RunHandle(
                run_id=str(payload["run_id"]),
                agent_id=str(payload["agent_id"]),
                thread_id=str(payload["thread_id"]),
            ),
        )

    cls = _BY_NAME.get(name)
    if cls is None:
        _log.debug("skipping an unknown event name %r — the daemon is newer than this client", name)
        return None

    kwargs: dict[str, Any] = {key: value for key, value in payload.items() if key in _FIELDS[name]}
    if cls is InterruptEvent:
        kwargs["request"] = decode_request(payload["request"])
    terminal = name in TERMINAL
    return Frame(
        **envelope,
        event=cls(**kwargs),
        # Read **only** on a terminal frame. Read generically, `status` would pick up
        # `SubagentEnd`'s delegate outcome and report a mid-run branch as the run's own ending.
        status=str(payload["status"]) if terminal and "status" in payload else None,
        code=str(payload["code"]) if terminal and "code" in payload else None,
    )


def decode_request(raw: Mapping[str, Any]) -> ApprovalRequest:
    """An :class:`~pkb.contracts.ApprovalRequest` from its JSON — the **one** parser (CL-19).

    Shared by the ``interrupt`` frame and by ``ThreadDetail.pending_interrupt``, which carry the
    same shape and reach a client by different routes. Two parsers would be two answers to "what am
    I approving", and the second is always the one nobody tests.

    ``agent_id`` and ``thread_id`` name **the run the interrupt parks on**: in a fan-out that is the
    expert and its derived thread, not the Librarian whose stream the client is reading (LB-16). A
    client that resumes on the wrong thread gets a 409 for a perfectly valid approval.
    """
    return ApprovalRequest(
        interrupt_id=str(raw["interrupt_id"]),
        agent_id=str(raw["agent_id"]),
        thread_id=str(raw["thread_id"]),
        actions=tuple(_decode_action(action) for action in raw.get("actions", ())),
    )


_DECISION_TYPES: Final[frozenset[str]] = frozenset(get_args(DecisionType))
"""The closed vocabulary. Anything else on the wire is dropped rather than trusted — see below."""


def _decode_action(raw: Mapping[str, Any]) -> ActionView:
    """One :class:`~pkb.contracts.ActionView`, with its decisions **filtered to the literal** (CL-19).

    ``allowed_decisions`` is typed ``tuple[DecisionType, ...]`` and is what a UI builds its controls
    from. Passing the wire through verbatim puts an arbitrary string into that typed slot, and the
    client then draws a button for it — on an approval the server can only answer with a 400 the
    human caused by pressing a control the client invented. Layer 2 filters on the way out for the
    same reason; this is the mirror of it, and a decoder that trusts the wire is a decoder that
    trusts whatever is between it and the daemon.
    """
    decisions = tuple(
        value for value in raw.get("allowed_decisions", ()) if value in _DECISION_TYPES
    )
    return ActionView(
        tool=str(raw["tool"]),
        args={str(k): str(v) for k, v in dict(raw.get("args", {})).items()},
        description=str(raw.get("description", "")),
        allowed_decisions=decisions,
        reason=str(raw.get("reason", "")),
    )


def _envelope(payload: Mapping[str, Any], name: str) -> dict[str, Any]:
    return {
        "type": str(payload.get("type", name)),
        "seq": int(payload.get("seq", 0)),
        "run_id": str(payload.get("run_id", "")),
        "thread_id": str(payload.get("thread_id", "")),
        "agent_id": str(payload.get("agent_id", "")),
    }
