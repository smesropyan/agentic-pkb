"""The types that cross the agent/transport seam.

Invariant I2 says a transport never imports ``deepagents``. That is only structurally true if the
types Layer 3 binds against live somewhere the harness cannot reach — importing them from
``pkb.agents`` would drag the whole harness in through that package's ``__init__``. So this module
is a **leaf**: it imports ``pkb.core`` and the standard library, nothing else, ever. An import-linter
contract enforces it, and ``tests/agents/test_contracts.py`` asserts that importing this module
loads no harness module at all.

Everything here is a frozen dataclass of primitives, with one deliberate exception:
:func:`validate_decisions`. Nothing carries a LangChain object, a graph, or a callable, because every
one of these values is destined for JSON on an SSE stream or an inline Telegram keyboard (arch §6) —
that is a rule about the *values*, not about the module, and arch §6 requires one shared validator
here so the TUI and the Telegram adapter answer "which decisions is this action allowed" identically
instead of each keeping a copy (§5.1, RT-40).

``Finding``, ``Severity``, ``ScanRequest`` and ``FlushReport`` are re-exported from ``pkb.core``
rather than restated: Layer 1 already defines them, they are already harness-free, and both layers
must mean the same thing by them (Layer 1 rules, C18).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final, Literal, Protocol, get_args

from pkb.core.errors import Finding, Severity
from pkb.core.models import FlushReport, ScanRequest

__all__ = [
    "CANCELLED_CODE",
    "ERROR_CODES",
    "EVENT_NAMES",
    "EXPERT_THREAD_SEPARATOR",
    "INTERNAL_CODE",
    "RETRYABLE_CODES",
    "RUN_ERROR_CODE",
    "RUN_STATUSES",
    "SCAN_THREAD_PREFIX",
    "ActionView",
    "AgentDescriptor",
    "AgentEvent",
    "ApprovalMode",
    "ApprovalPendingError",
    "ApprovalRequest",
    "Decision",
    "DecisionType",
    "Escalation",
    "Finding",
    "FlushReport",
    "InterruptEvent",
    "InvalidDecisionError",
    "MessageComplete",
    "MessageDelta",
    "MessageView",
    "OriginChannel",
    "Pack",
    "PackEntry",
    "PackOmission",
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
    "ThreadKind",
    "ToolEnd",
    "ToolStart",
    "UnknownAgentError",
    "UnknownThreadError",
    "agent_for_thread",
    "code_for",
    "expert_thread_id",
    "is_retryable",
    "is_scan_thread",
    "librarian_thread_id",
    "validate_decisions",
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


def validate_decisions(
    pending: ApprovalRequest | None,
    decisions: Sequence[Decision],
    *,
    interrupt_id: str | None = None,
) -> ApprovalRequest:
    """Refuse a bad resume **before the graph is touched** (§5.1, arch §6, RT-40).

    This lives in the seam rather than in ``pkb.agents.approval`` on purpose. Arch §6 says both human
    channels must turn an interrupt into a :class:`Decision` consistently — "same action parsing,
    same validation of which decisions are allowed… that logic lives once" — and ``pkb.clients`` and
    ``pkb.tui`` cannot import ``pkb.agents.approval`` without dragging ``langgraph`` in, which is
    exactly what I2 forbids. So the one copy sits here, where every importer of the seam can reach
    it, and ``pkb.agents.approval`` re-exports it for the runtime.

    Every check has a live-verified failure mode on the other side. Without them the harness raises a
    bare ``ValueError`` from inside ``after_model``, which aborts the superstep, skips the
    ``after_agent`` flush (D-1) and leaves the human staring at a stack trace instead of a 400. An
    unmatched interrupt id is the worst of the three: it degrades into a confusing count-mismatch
    message about "hanging tool calls" that says nothing about the id.

    The signature is deliberately wider than §5.1's ``(request, decisions) -> None`` sketch. RT-40
    also requires refusing a resume against a thread that is not interrupted at all and refusing a
    stale id, neither of which the sketch can express; and returning the validated request lets a
    caller chain straight into building the resume without re-reading the thread's state.

    Args:
        pending: What the thread is currently waiting on — or ``None`` when it is not interrupted.
        decisions: The human's answers, positionally aligned with ``pending.actions`` (RT-41).
        interrupt_id: The id the client believes it is answering. When given and different from the
            current one, the decisions are stale.

    Returns:
        The validated ``pending`` request, so callers can chain into building a resume.

    Raises:
        StaleInterruptError: Nothing is pending, or ``interrupt_id`` names a different interrupt.
            The thread is left interrupted and the original approval is still resolvable.
        InvalidDecisionError: Wrong number of decisions, a type the action does not allow, or a
            ``respond`` with no message (the harness's ``_process_decision`` reads
            ``decision["message"]`` unconditionally and would ``KeyError`` inside the graph).
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


class UnknownThreadError(PkbAgentError):
    """No such thread — 404 (C-8, RO-20).

    Layer 3 owns the ``threads`` table, so it owns this 404; the type lives here because
    ``pkb.tui`` and ``pkb.clients`` must catch it and may not import ``pkb.service``. Before it
    existed the nearest thing was ``UnknownAgentError``, which reaches a client as "no such agent"
    when the agent is fine and the thread is the problem.
    """


# --------------------------------------------------------------------------------------
# Thread identity — the derivation, in the seam so both sides answer it identically (C-1)
# --------------------------------------------------------------------------------------

EXPERT_THREAD_SEPARATOR: Final = "::"
"""Joins a Librarian thread to the expert thread derived from it (LB-14).

Two colons rather than one because a thread id is minted by Layer 3 and a scan thread already uses
``scan:<agent_id>:<uuid4>`` (RT-58); a two-character separator cannot collide with that shape, and
:func:`librarian_thread_id` can therefore invert the derivation exactly.
"""

SCAN_THREAD_PREFIX: Final = "scan:"
"""Reserved thread-id prefix for a scan run (RT-58, Q9).

A conflict scan is machine bookkeeping, not a conversation: its context never enters a human thread,
and Layer 3 filters these ids out of the thread list.
"""

ThreadKind = Literal["user", "routed"]
"""Whether a human started this conversation or the Librarian routed it here (ST-6)."""

OriginChannel = Literal["tui", "telegram", "mcp", "http"]
"""Where a conversation **started** — set once, never updated when another channel continues it.

A closed set defined once, because every adapter has to stamp the same word or the column is
ethnography rather than data (ST-13). Deliberately *not* consulted by any authorization check:
D3's whole promise is that a thread started in the TUI is finishable from Telegram, and one
`if origin_channel == …` deletes that guarantee in exactly the case the design is proudest of
(RO-22).
"""


def expert_thread_id(thread_id: str, agent_id: str) -> str:
    """The thread an expert runs on when the Librarian routes to it (LB-14).

    Derived, not minted, and that is the whole point. D-6 established that the harness would
    otherwise checkpoint delegated work in an opaque nested ``checkpoint_ns`` under the parent's
    thread — durable, but not addressable: a client cannot open it, resume it, or continue the
    conversation with the expert, so "continue with the Cooking expert" was a suggestion rather than
    a link. A deterministic id makes it a real thread with real history that
    ``run``/``resume``/``history`` all accept, and it stays derivable from the Librarian's thread so
    Layer 3 needs no extra table to find it.

    Lives here rather than in ``pkb.agents.routing`` because Layer 3 must *produce* one (SS-10,
    ST-12) and the transports may not import the harness. Same precedent as
    :func:`validate_decisions`: one copy in the seam, because both sides must answer identically and
    a second implementation of an id convention fails silently — a thread resolving to the wrong
    agent shares a checkpoint (D-6). ``pkb.agents.routing`` re-exports this exact object.
    """
    return f"{thread_id}{EXPERT_THREAD_SEPARATOR}{agent_id}"


def librarian_thread_id(thread_id: str) -> str | None:
    """The Librarian thread an expert thread was derived from, or ``None`` if it was not.

    Used by :meth:`~pkb.agents.runtime.PkbRuntime.delete_thread`: erasing a conversation has to
    erase the expert threads it spawned, or the deleted material survives in a thread the human
    never knew existed (RT-48 with the derivation of LB-14). Layer 3 uses it for ``Thread.kind`` and
    ``Thread.parent_thread_id``, which are computed rather than stored (decision D, ST-6).
    """
    head, separator, _ = thread_id.partition(EXPERT_THREAD_SEPARATOR)
    return head if separator else None


def is_scan_thread(thread_id: str) -> bool:
    """True for a maintenance thread, which no user operation may touch (RT-58, SV-13)."""
    return thread_id.startswith(SCAN_THREAD_PREFIX)


def agent_for_thread(thread_id: str) -> str | None:
    """The agent a thread id names *by shape*, or ``None`` when only the table can say (SV-9).

    Three provably disjoint namespaces: a minted ``uuid4`` (no ``::``, no ``scan:`` prefix) answers
    ``None`` and is resolved from the ``threads`` row; ``<parent>::<agent-id>`` answers with
    everything after the **first** ``::``, which is what keeps a sub-topic id containing ``/`` intact;
    ``scan:<agent-id>:<uuid4>`` answers with the agent between the two colons.

    Shape first, table second, is what lets a derived thread be openable, runnable and resumable
    from the id alone with no row at all (SV-12) — the row is an index for discovery, never the
    authority on existence. That authority is the checkpoint.
    """
    if is_scan_thread(thread_id):
        rest = thread_id[len(SCAN_THREAD_PREFIX) :]
        agent, separator, _ = rest.rpartition(":")
        return agent if separator and agent else None
    _, separator, tail = thread_id.partition(EXPERT_THREAD_SEPARATOR)
    return tail if separator and tail else None


# --------------------------------------------------------------------------------------
# Context packs — the types, because they cross the seam (PK-7, decision G)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PackEntry:
    """One whole file in a pack, in the order the consumer should read it.

    ``role`` is what makes the ordering checkable rather than incidental: ``notes-summary`` is first
    in an implementation pack because human-approved experience outranks static knowledge (PK-10),
    and a consumer that wants to weight the pack can do so without re-deriving the role from the
    path. ``text`` is the file's whole content — a pack never carries a fragment, because a clipped
    file is a claim the consumer cannot check (PK-11).
    """

    path: str
    """KB-relative POSIX path, as every path in this seam is."""

    role: str
    text: str
    bytes: int


@dataclass(frozen=True, slots=True)
class PackOmission:
    """A file the ordering selected and the budget excluded, named so the gap is visible (PK-11).

    A silently clipped pack is worse than a short one: the consumer reasons over what arrived and
    has no way to know what did not.
    """

    path: str
    role: str
    reason: str
    bytes: int


@dataclass(frozen=True, slots=True)
class Escalation:
    """A file carrying ``status.conflict-review`` inside the pack's scope (MC-20, RT-59).

    Computed deterministically from the tag, never from what a model said it read, and it clears
    itself when the human resolves the tag. Delivered as a **successful** result with a discriminator
    rather than an error: a well-behaved agent retries errors, and a retried escalation is an
    escalation ignored.
    """

    path: str
    review_note: str
    agent_id: str


@dataclass(frozen=True, slots=True)
class Pack:
    """A deterministic, read-only slice of the knowledge base (PK-7 … PK-12).

    Assembly is a pure function of Layer 1's derived surface and runs **no model** (PK-8), which is
    what makes "``notes/summary.md`` is always first" a property a golden test can pin rather than a
    hope about a prompt.
    """

    kind: Literal["research", "implementation"]
    scope: tuple[str, ...]
    """The agent ids this pack covers, in snapshot order."""

    entries: tuple[PackEntry, ...]
    omitted: tuple[PackOmission, ...] = ()
    escalation: tuple[Escalation, ...] = ()

    @property
    def truncated(self) -> bool:
        return bool(self.omitted)

    @property
    def total_bytes(self) -> int:
        return sum(entry.bytes for entry in self.entries)


# --------------------------------------------------------------------------------------
# The event-name table — one copy, total, shared by the encoder and every decoder (SS-3)
# --------------------------------------------------------------------------------------

EVENT_NAMES: Final[Mapping[type, str]] = MappingProxyType(
    {
        MessageDelta: "message.delta",
        MessageComplete: "message.complete",
        ToolStart: "tool.start",
        ToolEnd: "tool.end",
        SubagentStart: "subagent.start",
        SubagentEnd: "subagent.end",
        InterruptEvent: "interrupt",
        RunEnd: "run.end",
        RunError: "run.error",
    }
)
"""Every :data:`AgentEvent` member to its wire name — arch §5's nine names, verbatim.

Here rather than in ``pkb.server.sse`` because the encoder and every decoder must agree, and a
decoder lives in ``pkb.tui`` and ``pkb.clients``, which may not import the server. **Total by
construction**: the assertion below runs at import, so a tenth event kind added to the union without
a name here is an ImportError at startup rather than a frame that vanishes on the floor.
"""

assert set(EVENT_NAMES) == set(get_args(AgentEvent)), (
    "EVENT_NAMES must name every AgentEvent member (SS-3): "
    f"missing {sorted(t.__name__ for t in set(get_args(AgentEvent)) - set(EVENT_NAMES))}"
)

RUN_STARTED_EVENT: Final = "run.started"
"""The one frame Layer 3 authors that is not an :data:`AgentEvent` (SS-8).

A transport frame carrying :class:`RunHandle`, written before anything is relayed so a client has
the run id (for cancel) and the agent id (for its header) before the first token.
"""


# --------------------------------------------------------------------------------------
# Machine error codes — one table, because four sides have to agree on it (decision P, RO-21)
# --------------------------------------------------------------------------------------

ERROR_CODES: Final[Mapping[type[BaseException], str]] = MappingProxyType(
    {
        UnknownAgentError: "unknown_agent",
        UnknownThreadError: "unknown_thread",
        ThreadBusyError: "thread_busy",
        ApprovalPendingError: "approval_pending",
        StaleInterruptError: "stale_interrupt",
        InvalidDecisionError: "invalid_decision",
        ValueError: "validation_error",
    }
)
"""Typed error → the stable machine code a client branches on.

Here rather than in ``pkb.server.errors`` because **four** things have to agree on it: the HTTP
exception handler, the MCP adapter, the TUI and the Telegram adapter — and the last two may not
import ``pkb.server`` at all (I2). The alternatives were a client branching on prose, or the same
nine rows copied into step 4 and again into step 5, which is exactly the drift that put
``validate_decisions``, ``expert_thread_id`` and :class:`UnknownThreadError` in this module.

The **HTTP status** half stays in ``pkb.server.errors``: a status code is a transport's concern and
a Telegram bot has no use for one. ``pkb.server`` re-exports these names and a test asserts the
objects are identical, not merely equal.
"""

INTERNAL_CODE: Final = "internal"
"""What an unmapped exception becomes. A new typed error is this until somebody gives it a row."""

RUN_ERROR_CODE: Final = "run_error"
"""A run that failed for a reason the transport could not type — the wire's ``run.error`` default."""

CANCELLED_CODE: Final = "cancelled"
"""A run the human or the daemon stopped (AP-11). Distinguished from a failure because it is not
one: a client offers "try again" for a failure and says nothing for a cancellation."""

RETRYABLE_CODES: Final = frozenset({"thread_busy", RUN_ERROR_CODE, CANCELLED_CODE})
"""Codes where retrying *the same call later* can succeed.

``approval_pending`` is deliberately absent: retrying does not help, because the thread stays parked
until a human decides. Neither is ``invalid_decision`` — the request was wrong, not the moment.
"""

RUN_STATUSES: Final = ("completed", "interrupted", "cancelled", "error")
"""The four values ``run.end``/``run.error`` carry (SS-9, amended).

**Four, not three.** ``completed`` and ``interrupted`` ride on ``run.end``; ``cancelled`` and
``error`` ride on ``run.error``, because a cancelled run never emits ``run.end`` at all — Layer 2
re-raises ``CancelledError`` without a terminal event. A client matching three ways either raises or
falls through to "done" on every provider failure, which is the most common failure a human sees.

``interrupted`` is the one that must never render as done: the thread is parked on a human decision.
"""


def code_for(exc: BaseException) -> str:
    """The machine code for one exception — MRO order, so a subclass inherits its mapping."""
    for klass in type(exc).__mro__:
        code = ERROR_CODES.get(klass)
        if code is not None:
            return code
    return INTERNAL_CODE


def is_retryable(exc: BaseException) -> bool:
    """Whether retrying the same call later could succeed. See :data:`RETRYABLE_CODES`."""
    return code_for(exc) in RETRYABLE_CODES
