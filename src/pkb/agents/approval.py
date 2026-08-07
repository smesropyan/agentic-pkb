"""Translation between the harness's HITL payloads and the approval types in :mod:`pkb.contracts`.

This module is one half of the Layer 2 → Layer 3 seam: nothing harness-shaped crosses it. A
:class:`~langgraph.types.Interrupt` goes in and a frozen
:class:`~pkb.contracts.ApprovalRequest` of primitives comes out; a sequence of
:class:`~pkb.contracts.Decision` goes in and a :class:`~langgraph.types.Command` comes out. Layer 3
never sees an ``Interrupt``, a ``HITLRequest`` or a ``Command`` (RT-43, I2).

Three harness facts shape everything here, each executed against the pin rather than read:

* The interrupt value is a ``HITLRequest`` — ``{"action_requests": [...], "review_configs": [...]}``
  — with the two lists **positionally aligned** (`human_in_the_loop.py:429`). All interruptible
  tool calls in one ``AIMessage`` batch into a *single* interrupt, so one ``ApprovalRequest`` may
  carry several actions and the decisions must come back in the same order (RT-41).
* The resume payload is ``Command(resume={"decisions": [...]})`` with four decision shapes, each
  live-verified: ``approve`` runs the call, ``edit`` replaces name+args, ``reject`` returns an error
  ``ToolMessage``, ``respond`` skips the tool and returns a *success* ``ToolMessage``
  (`human_in_the_loop.py:299-349`).
* ``_process_decision`` raises a bare ``ValueError`` **inside the graph** for a disallowed decision
  type, and ``after_model`` raises another for a count mismatch. Both kill the run and leave a
  confusing message on the thread. That is why :func:`validate_decisions` exists and why RT-40
  requires it to run *before* the graph is touched.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Final, cast, get_args

from langgraph.types import Command, Interrupt

from pkb.contracts import (
    ActionView,
    ApprovalRequest,
    Decision,
    DecisionType,
    InvalidDecisionError,
    StaleInterruptError,
)

__all__ = [
    "DECISION_TYPES",
    "DEFAULT_REASON",
    "PROPOSE_ONLY_MESSAGE",
    "ReasonResolver",
    "normalize_interrupt",
    "normalize_interrupts",
    "propose_only_command",
    "to_resume_command",
    "validate_decisions",
]

DECISION_TYPES: Final[frozenset[str]] = frozenset(get_args(DecisionType))
"""The four decision types the harness understands, read off the ``Literal`` in the seam."""

DEFAULT_REASON: Final = "approval"
"""The ``ActionView.reason`` slug used when no gate reason resolver is supplied.

``ActionRequest`` carries only ``name``/``args``/``description`` — the harness has nowhere to put a
gate reason (RT-34). Rather than smuggle one into the description text and parse it back out, the
caller injects a resolver (``gates.requires_approval`` bound to the current snapshot) and this slug
is the honest fallback for an interrupt no resolver claims.
"""

PROPOSE_ONLY_MESSAGE: Final = (
    "Rejected automatically: this run is in propose-only mode, where the caller cannot satisfy a "
    "human approval gate. The action was recorded as a pending proposal for the human to decide."
)
"""The fixed rejection text for RT-42's propose-only auto-reject."""

ReasonResolver = Callable[[str, Mapping[str, Any]], str]
"""``(tool_name, raw_args) -> gate reason slug``. Supplied by the runtime from ``gates.py``."""

# --------------------------------------------------------------------------------------
# Harness -> contracts
# --------------------------------------------------------------------------------------


def normalize_interrupt(
    interrupt: Interrupt,
    *,
    agent_id: str,
    thread_id: str,
    reason_for: ReasonResolver | None = None,
) -> ApprovalRequest:
    """Turn one harness ``Interrupt`` into an :class:`ApprovalRequest` (RT-41, RT-43).

    ``agent_id`` is the agent whose *run* was interrupted, never the delegate that raised it. A
    gated write inside a delegated expert propagates to the Librarian's thread and is resolved there
    with the Librarian's id (LB-10) — approval resolution is routed by thread, never by agent, so
    handing a client the delegate's id would send its resume to a thread that is not interrupted.

    ``action_requests[i]`` and ``review_configs[i]`` are positionally aligned by the harness, and
    :attr:`ApprovalRequest.actions` preserves that order because the decisions a client sends back
    are matched positionally too (RT-41).

    Args:
        interrupt: The harness interrupt, whose ``value`` is a ``HITLRequest``.
        agent_id: The interrupted run's agent id.
        thread_id: The interrupted run's thread id — the only routing key that works.
        reason_for: Optional ``(tool, args) -> slug`` resolver for :attr:`ActionView.reason`.
    """
    value = interrupt.value if isinstance(interrupt.value, Mapping) else {}
    requests = _as_sequence(value.get("action_requests"))
    configs = _as_sequence(value.get("review_configs"))

    actions: list[ActionView] = []
    for index, request in enumerate(requests):
        if not isinstance(request, Mapping):
            continue
        config = configs[index] if index < len(configs) else {}
        tool = str(request.get("name", ""))
        raw_args = request.get("args")
        raw_args = raw_args if isinstance(raw_args, Mapping) else {}
        actions.append(
            ActionView(
                tool=tool,
                args=_view_args(raw_args),
                description=str(request.get("description", "")),
                allowed_decisions=_allowed_decisions(config),
                reason=reason_for(tool, raw_args) if reason_for is not None else DEFAULT_REASON,
            )
        )

    return ApprovalRequest(
        interrupt_id=interrupt.id,
        agent_id=agent_id,
        thread_id=thread_id,
        actions=tuple(actions),
    )


def normalize_interrupts(
    interrupts: Iterable[Interrupt],
    *,
    agent_id: str,
    thread_id: str,
    reason_for: ReasonResolver | None = None,
) -> list[ApprovalRequest]:
    """Normalize several interrupts, **deduplicated by interrupt id**, order preserved (RT-41).

    Deduplication is not defensive tidiness: with ``subgraphs=True`` a delegated expert's interrupt
    is emitted twice — once under namespace ``('tools:<uuid>',)`` and once under ``()`` — carrying
    the same ``Interrupt.id``. ``aget_state(cfg)`` likewise exposes the same interrupt on both
    ``.interrupts`` and ``.tasks[0].interrupts``. Both are one approval and must produce one
    request, or the human is asked to decide the same write twice.
    """
    seen: set[str] = set()
    out: list[ApprovalRequest] = []
    for interrupt in interrupts:
        if interrupt.id in seen:
            continue
        seen.add(interrupt.id)
        out.append(
            normalize_interrupt(
                interrupt, agent_id=agent_id, thread_id=thread_id, reason_for=reason_for
            )
        )
    return out


# --------------------------------------------------------------------------------------
# Contracts -> harness
# --------------------------------------------------------------------------------------


def validate_decisions(
    pending: ApprovalRequest | None,
    decisions: Sequence[Decision],
    *,
    interrupt_id: str | None = None,
) -> ApprovalRequest:
    """Refuse a bad resume **before the graph is touched** (RT-40).

    Every check here has a live-verified failure mode on the other side. Without them the harness
    raises a bare ``ValueError`` from inside ``after_model``, which aborts the superstep, skips the
    ``after_agent`` flush (D-1) and leaves the human staring at a stack trace instead of a 400. An
    unmatched interrupt id is the worst of the three: it degrades into a confusing count-mismatch
    message about "hanging tool calls" that says nothing about the id.

    Args:
        pending: What ``aget_state(cfg).interrupts`` currently holds, normalized — or ``None`` when
            the thread is not interrupted at all.
        decisions: The human's answers, positionally aligned with ``pending.actions``.
        interrupt_id: The id the client believes it is answering. When given and different from the
            current one, the decisions are stale.

    Returns:
        The validated ``pending`` request, so callers can chain into :func:`to_resume_command`.

    Raises:
        StaleInterruptError: Nothing is pending, or ``interrupt_id`` names a different interrupt.
            The thread is left interrupted and the original approval is still resolvable.
        InvalidDecisionError: Wrong number of decisions, a type the action does not allow, or a
            ``respond`` with no message (``_process_decision`` reads ``decision["message"]``
            unconditionally and would ``KeyError`` inside the graph).
    """
    if pending is None:
        message = "no approval is pending on this thread"
        if interrupt_id is not None:
            message = f"{message}; interrupt {interrupt_id!r} is no longer current"
        raise StaleInterruptError(message)

    if interrupt_id is not None and interrupt_id != pending.interrupt_id:
        raise StaleInterruptError(
            f"decisions answer interrupt {interrupt_id!r}, but the thread is waiting on "
            f"{pending.interrupt_id!r}"
        )

    if len(decisions) != len(pending.actions):
        raise InvalidDecisionError(
            f"expected {len(pending.actions)} decision(s) for interrupt "
            f"{pending.interrupt_id!r}, got {len(decisions)}"
        )

    for index, (decision, action) in enumerate(zip(decisions, pending.actions, strict=True)):
        if decision.type not in action.allowed_decisions:
            raise InvalidDecisionError(
                f"decision {index} is {decision.type!r}, but {action.tool!r} allows only "
                f"{list(action.allowed_decisions)}"
            )
        if decision.type == "respond" and not decision.message:
            raise InvalidDecisionError(
                f"decision {index} is 'respond' and needs a message: it becomes the tool's result"
            )

    return pending


def to_resume_command(request: ApprovalRequest, decisions: Sequence[Decision]) -> Command[Any]:
    """Build the ``Command(resume=...)`` that answers ``request`` (RT-40, RT-41).

    Validates first, so an invalid ``Command`` is not constructible through this function — the one
    sanctioned construction site outside the propose-only auto-reject (RT-33).

    The four payload shapes are the ones verified against the pin. ``edit`` is the subtle one: the
    harness replaces the whole action, so an edit that only rewrites the args must still resend the
    tool name, and an edit that only renames the tool must still resend the args. Both defaults come
    from ``request``.
    """
    validate_decisions(request, decisions)
    payload = [
        _harness_decision(decision, action)
        for decision, action in zip(decisions, request.actions, strict=True)
    ]
    return Command(resume={"decisions": payload})


def propose_only_command(request: ApprovalRequest) -> Command[Any]:
    """Auto-reject every action of ``request`` with the fixed propose-only message (RT-42).

    This is the **only** decision Layer 2 authors on its own behalf. An MCP caller cannot satisfy a
    human gate, so a propose-only run rejects rather than hanging: nothing is written, the action is
    recorded as a :class:`~pkb.contracts.PendingProposal` for the human, and the run completes.
    ``reject`` — never ``respond`` — because ``respond`` returns ``status="success"`` and would tell
    the model the write happened (RT-32).
    """
    payload = [
        {"type": "reject", "message": PROPOSE_ONLY_MESSAGE} for _ in range(len(request.actions))
    ]
    return Command(resume={"decisions": payload})


# --------------------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------------------


def _harness_decision(decision: Decision, action: ActionView) -> dict[str, Any]:
    """One entry of the ``decisions`` list, in the shape ``_process_decision`` expects."""
    if decision.type == "approve":
        return {"type": "approve"}
    if decision.type == "edit":
        return {
            "type": "edit",
            "edited_action": {
                "name": decision.edited_tool or action.tool,
                "args": dict(decision.edited_args)
                if decision.edited_args is not None
                else dict(action.args),
            },
        }
    if decision.type == "respond":
        return {"type": "respond", "message": decision.message or ""}
    # reject: the message is optional here even though the seam documents it as expected — the
    # harness substitutes its own "do not retry unless the user asks" text, which is strictly
    # better than refusing the resume over a missing string.
    rejection: dict[str, Any] = {"type": "reject"}
    if decision.message:
        rejection["message"] = decision.message
    return rejection


def _allowed_decisions(config: object) -> tuple[DecisionType, ...]:
    """The ``allowed_decisions`` of one ``ReviewConfig``, kept verbatim as server-side truth.

    A client may narrow this for its own UI — Telegram drops ``edit`` because editing a document on
    a phone is impractical — but the set published here is what :func:`validate_decisions` enforces.
    """
    if not isinstance(config, Mapping):
        return ()
    raw = config.get("allowed_decisions")
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return ()
    return tuple(cast("DecisionType", d) for d in raw if isinstance(d, str) and d in DECISION_TYPES)


def _as_sequence(value: object) -> list[Any]:
    """A list view of a possibly-missing HITLRequest field."""
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return list(value)
    return []


def _view_args(args: Mapping[str, Any]) -> dict[str, str]:
    """Coerce raw tool args to the ``Mapping[str, str]`` the seam declares.

    ``ActionView`` crosses a JSON boundary onto an SSE stream and an inline Telegram keyboard, so
    every value is rendered to a string here rather than at each transport. A plain ``dict`` is
    returned deliberately: ``dataclasses.asdict`` rebuilds mappings by calling ``type(obj)(...)``,
    which a read-only mapping proxy cannot satisfy.
    """
    return {str(key): _view_value(value) for key, value in args.items()}


def _view_value(value: object) -> str:
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, bool | int | float):
        return str(value)
    return json.dumps(value, default=str, ensure_ascii=False)
