"""The types that cross the agent/transport seam.

Invariant I2 says a transport never imports ``deepagents``. That is only structurally true if the
types Layer 3 binds against live somewhere the harness cannot reach — importing them from
``pkb.agents`` would drag the whole harness in through that package's ``__init__``. So this module
is a **leaf**: it imports ``pkb.core`` and the standard library, nothing else, ever. An import-linter
contract enforces it, and ``tests/agents/test_contracts.py`` asserts that importing this module
loads no harness module at all.

Everything here is a frozen dataclass of primitives. Nothing carries a LangChain object, a graph, or
a callable, because every one of these values is destined for JSON on an SSE stream or an inline
Telegram keyboard (arch §6).

``Finding``, ``Severity``, ``ScanRequest`` and ``FlushReport`` are re-exported from ``pkb.core``
rather than restated: Layer 1 already defines them, they are already harness-free, and both layers
must mean the same thing by them (Layer 1 rules, C18).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from pkb.core.errors import Finding, Severity
from pkb.core.models import FlushReport, ScanRequest

__all__ = [
    "ActionView",
    "AgentDescriptor",
    "AgentEvent",
    "ApprovalMode",
    "ApprovalPendingError",
    "ApprovalRequest",
    "Decision",
    "DecisionType",
    "Finding",
    "FlushReport",
    "InterruptEvent",
    "InvalidDecisionError",
    "MessageComplete",
    "MessageDelta",
    "MessageView",
    "PendingProposal",
    "PkbAgentError",
    "RunEnd",
    "RunError",
    "RunHandle",
    "ScanQueue",
    "ScanRequest",
    "ScanResult",
    "Severity",
    "StaleInterruptError",
    "SubagentEnd",
    "SubagentStart",
    "ThreadBusyError",
    "ToolEnd",
    "ToolStart",
    "UnknownAgentError",
]

LIBRARIAN_AGENT_ID = "librarian"
"""The root agent's id. Topic ids mirror the tree: ``topic/cooking/grilling`` (arch §4)."""


# --------------------------------------------------------------------------------------
# Identity and catalog
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentDescriptor:
    """One row of the agent catalog (RG-14).

    Produced by the registry, consumed by ``GET /agents`` and by the TUI's agent picker. The
    architecture doc calls this ``AgentInfo`` and places it in ``pkb.service``; it lives here
    because the registry produces it and ``pkb.agents`` must not import a transport (C13).
    """

    agent_id: str
    """``librarian`` or a topic id. Opaque to transports — it may contain ``/``."""

    title: str
    """``topic.md``'s title, falling back to the folder name for a degraded topic (GE-25)."""

    description: str
    """``topic.md``'s description — the same string the Librarian routes on (RG-10)."""

    has_custom_expert: bool
    """Whether the topic ships an ``expert.md`` (the root catalog's ``*(custom expert)*``, GE-13)."""

    model_id: str
    """Resolved by the registry. A transport never chooses a model (RG-21)."""


# --------------------------------------------------------------------------------------
# Streaming events (arch §5)
# --------------------------------------------------------------------------------------
#
# Normalized, never proxied: the TUI wants token deltas, Telegram renders on completion, and MCP
# wants only the final result. Proxying raw harness payloads would make every adapter understand
# deepagents internals, which is what I2 exists to prevent.


@dataclass(frozen=True, slots=True)
class MessageDelta:
    """One chunk of assistant text. Consumed by the TUI only."""

    run_id: str
    agent_id: str
    text: str


@dataclass(frozen=True, slots=True)
class MessageComplete:
    """A finished assistant message."""

    run_id: str
    agent_id: str
    text: str


@dataclass(frozen=True, slots=True)
class ToolStart:
    run_id: str
    agent_id: str
    tool: str
    summary: str
    """Argument summary, already rendered — never the raw arguments."""


@dataclass(frozen=True, slots=True)
class ToolEnd:
    run_id: str
    agent_id: str
    tool: str
    summary: str
    error: bool


@dataclass(frozen=True, slots=True)
class SubagentStart:
    """The Librarian routed to an expert. ``agent_id`` is the *delegate* (RT-44).

    This is what makes README §2.2's routing visible in the TUI instead of a silent pause.
    """

    run_id: str
    agent_id: str


@dataclass(frozen=True, slots=True)
class SubagentEnd:
    run_id: str
    agent_id: str
    status: str


@dataclass(frozen=True, slots=True)
class InterruptEvent:
    """A run paused for a human decision. Deduplicated by interrupt id (RT-41)."""

    run_id: str
    request: ApprovalRequest


@dataclass(frozen=True, slots=True)
class RunEnd:
    run_id: str
    final_text: str


@dataclass(frozen=True, slots=True)
class RunError:
    run_id: str
    message: str
    retryable: bool


AgentEvent = (
    MessageDelta
    | MessageComplete
    | ToolStart
    | ToolEnd
    | SubagentStart
    | SubagentEnd
    | InterruptEvent
    | RunEnd
    | RunError
)


# --------------------------------------------------------------------------------------
# Approvals
# --------------------------------------------------------------------------------------

DecisionType = Literal["approve", "edit", "reject", "respond"]

ApprovalMode = Literal["interactive", "propose_only"]
"""``propose_only`` is the MCP write path: an external agent cannot satisfy a human gate, so the
action is recorded as a :class:`PendingProposal` instead of hanging on an interrupt (arch §6)."""


@dataclass(frozen=True, slots=True)
class ActionView:
    """One action awaiting a decision, rendered for a human.

    What the human approves in a knowledge base is usually *content*, so ``description`` carries the
    diff of the proposed write (RT-34). It is rendered server-side, once, because the TUI's modal and
    Telegram's inline keyboard must be deciding about the same thing.
    """

    tool: str
    args: Mapping[str, str]
    """Primitives only — this crosses a JSON boundary."""

    description: str
    allowed_decisions: tuple[DecisionType, ...]
    """Server-side truth. A client may narrow its UI — Telegram drops ``edit`` because editing a
    document on a phone is impractical (arch §6) — but never widen it."""

    reason: str
    """Why the gate fired, as a stable slug: ``breadth-approval``, ``new-tag``, ``delete``…"""


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """Everything a client needs to render one approval, from any channel."""

    interrupt_id: str
    agent_id: str
    thread_id: str
    actions: tuple[ActionView, ...]
    """Positionally aligned with the decisions a client sends back (RT-41)."""


@dataclass(frozen=True, slots=True)
class Decision:
    """One human answer, positionally matched to an :class:`ActionView`."""

    type: DecisionType
    message: str | None = None
    """Required for ``reject`` and ``respond``: the agent needs to know *why*."""

    edited_args: Mapping[str, str] | None = None
    edited_tool: str | None = None


@dataclass(frozen=True, slots=True)
class PendingProposal:
    """A write an external agent proposed but cannot approve (arch §6, RT-42).

    The human sees these in the TUI. This is what keeps "human content wins" true when the caller is
    a robot.
    """

    proposal_id: str
    agent_id: str
    thread_id: str
    action: ActionView
    created_at: datetime


# --------------------------------------------------------------------------------------
# Runs, history and scans
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MessageView:
    """One message of a thread's replayed history."""

    role: str
    text: str
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class RunHandle:
    run_id: str
    agent_id: str
    thread_id: str


@dataclass(frozen=True, slots=True)
class ScanResult:
    """The outcome of one conflict scan (RT-58)."""

    topic_id: str
    thread_id: str
    tagged_paths: tuple[str, ...]
    summary: str


class ScanQueue(Protocol):
    """The queue Layer 1 schedules into and Layer 2 drains (RT-54, C18).

    Layer 1 returns :class:`~pkb.core.models.ScanRequest` values and opens no database (MA-11); the
    daemon owns the table. The middleware depends on this Protocol so its tests can use a list.
    """

    async def put(self, requests: Sequence[ScanRequest]) -> None: ...

    async def take(self, limit: int = 1) -> Sequence[ScanRequest]: ...

    async def done(self, topic_id: str) -> None: ...


# --------------------------------------------------------------------------------------
# Typed errors — Layer 3 maps these to status codes
# --------------------------------------------------------------------------------------


class PkbAgentError(Exception):
    """Base class for agent-layer failures a transport is expected to translate."""


class UnknownAgentError(PkbAgentError):
    """No such agent id — 404 (RG-13)."""


class ThreadBusyError(PkbAgentError):
    """A run is already active on this thread — 409 (RT-45).

    LangGraph OSS has no multitask strategy, so this is Layer 2's own registry, not a harness
    feature: two concurrent runs on one thread otherwise both succeed with interleaved writes.
    """


class ApprovalPendingError(PkbAgentError):
    """A new message arrived while the thread waits on a decision — 409 (RT-39).

    Sending one to the harness silently discards the pending interrupt and runs the turn as if the
    gated tool call never happened, so this is refused rather than forwarded.
    """


class StaleInterruptError(PkbAgentError):
    """The decisions answer an interrupt that is no longer current — 409 (RT-40)."""


class InvalidDecisionError(PkbAgentError):
    """Wrong number of decisions, or a type the action does not allow — 400 (RT-40)."""
