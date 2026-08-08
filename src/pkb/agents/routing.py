"""The Librarian's routing workflow — classify, fan out, merge, offer (LB-2, LB-12 … LB-19).

Routing used to be a *decision the model made*: the Librarian was a deep agent holding deepagents'
``task`` tool and was free to delegate or not. Measured against a real model it did not delegate.
Asked "what do my Cooking notes say about pulling a ribeye?" it ran ``ls``/``read_file``/``grep`` and
answered out of the raw files — no topic skills, no ``expert.md`` persona, no per-topic voice — and on
another run it stated *"The Cooking expert checked the knowledge base"* when no expert had run at all.

**The human ruled that routing becomes a harness-encoded workflow.** A Librarian turn is four steps
and only the first is the model's:

1. **Classify** — one model call, with the generated root catalog in context, whose entire output is a
   call to the :data:`ROUTE_TOOL` tool naming the applicable topic ids. This is the only discretion
   in the turn.
2. **Fan out** — ordinary Python invokes *every* applicable expert. Not a tool the model may decline
   to call; a step that always runs.
3. **Merge** — ordinary Python composes the reply by attribution, each expert's own final message
   under its own heading with its title and agent id. **Never a synthesis model call.** A merge
   written by a model is exactly how "the Cooking expert checked" gets said when no expert ran;
   attribution assembled from actual results cannot lie about who contributed.
4. **Offer** — the reply names the agent id and the thread of every expert that answered, so a client
   can offer "continue with the Cooking expert" and have it resolve to a real, openable thread.

**Ingestion fans out exactly like a question, and copies are the point.** The human's ruling: *"Same
thing can be ingested by multiple topic experts! … A management book can offer lessons on management
& parenting. The experts ingest each book/paper/article/clip from the lens of their expertise
therefore not duplicating but rather extracting different facets from the same source."* So one book
routed to Management and Parenting yields ``Management/references/<book>/<book>.md`` about leadership
and ``Parenting/references/<book>/<book>.md`` about raising children — two *different extractions*,
each written through its topic's own ``expert.md``, skills chain and voice overload. README §1.8 rule
4 ("a solution note lives in exactly one topic — there are no copies") is scoped to **solution
notes** and does not reach source ingestion; that misreading was made once and corrected.

Consequently there is **no intent-based split** anywhere in this module: questions and material take
the same path, with the same arity. An expert that has nothing to contribute says so and files
nothing, and a fan-out where two of four experts file and two decline is a success, not a partial
failure — which is why nothing here inspects an expert's answer to decide whether it "worked".

**When classification is uncertain, the human is asked with a menu.** The human's instruction: *"If
model has issue routing — harness can ask consumer: which of the following experts would you want to
engage for your question."* So the fallback is a *choice*, listing the candidate experts, never a
guess: a wrong guess files knowledge in the wrong place and there is no undo (D6). It is reached when
the model answers in prose instead of calling the tool — after :data:`MAX_ROUTE_ATTEMPTS` attempts,
the retry being forced by :class:`RouteMiddleware` rather than requested politely — or when it names
no topic while the catalog has candidates to choose from.

**Why a tool call and not structured output.** Structured output does not work on Ollama cloud: the
``format`` parameter is ignored (verified). A tool call is the one channel that carries a typed
decision on every provider this deployment uses.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, Final, NotRequired, Protocol

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelCallResult,
    ModelRequest,
    ModelResponse,
    hook_config,
)
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.runtime import Runtime

from pkb.agents.paths import to_kb_relative
from pkb.contracts import (
    EXPERT_THREAD_SEPARATOR,
    AgentDescriptor,
    AgentEvent,
    ApprovalMode,
    ApprovalPendingError,
    InterruptEvent,
    RunEnd,
    RunError,
    SubagentEnd,
    SubagentStart,
    ThreadBusyError,
    ToolEnd,
    ToolStart,
    expert_thread_id,
    librarian_thread_id,
)

__all__ = [
    "EXPERT_THREAD_SEPARATOR",
    "MAX_ROUTE_ATTEMPTS",
    "MENU_FOOTER",
    "MENU_HEADER",
    "MENU_QUESTION",
    "NO_MESSAGE",
    "RETRY_INSTRUCTION",
    "ROUTE_ACK",
    "ROUTE_ATTEMPTS",
    "ROUTE_DECISION",
    "ROUTE_TOOL",
    "TOPIC_GAP_INSTRUCTION",
    "ExpertOutcome",
    "ExpertStatus",
    "FanOut",
    "FanOutHost",
    "RouteMiddleware",
    "RoutingDecision",
    "RoutingState",
    "expert_thread_id",
    "librarian_thread_id",
    "merge_reply",
    "read_decision",
    "resolve_targets",
    "route_tool",
    "routing_envelope",
    "routing_menu",
]


# --------------------------------------------------------------------------------------
# The classification tool (step 1)
# --------------------------------------------------------------------------------------

ROUTE_TOOL: Final = "route"
"""The one tool the classification step exists to produce a call to.

Named as a verb the model already understands rather than ``classify``/``select_experts``: the
description does the work, and a short familiar name is what a small local model spells correctly.
"""

ROUTE_ACK: Final = "Routing recorded. The system is now running the experts you named."
"""The ToolMessage that answers a ``route`` call.

It is written by :class:`RouteMiddleware`, not by the tool body, because the middleware ends the run
at the call rather than letting it execute (see :meth:`RouteMiddleware.after_model`). It still says
something true and complete, because it is durable in the thread and a human reads it back.
"""

RETRY_INSTRUCTION: Final = (
    "You did not call the `route` tool. Do not answer this yourself and do not read any files: "
    "you hold no topic knowledge, and the experts do. Call `route` now with every topic id from "
    "the catalog whose description covers this item — several ids are normal and encouraged, and "
    "an empty list is the right answer only when no topic in the catalog fits at all."
)
"""The one forced retry, delivered inside the run rather than as a new turn (LB-13).

It is a ``HumanMessage`` and therefore visible in the thread's replayed history, deliberately: the
turn cost two model calls and hiding the reason would make that an unexplained pause. A model that
ignores it twice is not going to be persuaded by a third phrasing, which is why the fallback after
this is a question to the human (:func:`routing_menu`) rather than another nudge.
"""

TOPIC_GAP_INSTRUCTION: Final = (
    "No topic in this knowledge base covers that — the catalog is empty, so there is nothing to "
    "route to. Propose one topic with `create_topic`: what it would cover, the description it "
    "would carry, and why. Do not answer the item yourself and do not file anything."
)
"""The second turn a topic gap gets, when there is nothing to choose from (LB-19, LB-6).

Bootstrapping starts with zero topics and every inbound item is a gap. A menu of no experts is not a
choice, so the uncertain path cannot end with the human here; it ends by handing the turn back to the
Librarian for the one thing it *can* do about a gap — propose a topic, gated, for the human to decide
(LB-7). Delivered as a message on the thread rather than baked into the prompt because it applies to
one turn in the life of a knowledge base and reads as nonsense on every other.
"""

MAX_ROUTE_ATTEMPTS: Final = 1
"""How many times :class:`RouteMiddleware` forces the model back to the routing tool (LB-13).

One. The first prose answer is a slip and the retry catches it; the second is a model that will not
route, and the correct response to that is to ask the human which experts to engage — not to grind,
and never to guess.
"""

_TASK_TOOL: Final = "task"
"""deepagents' delegation tool, removed from the Librarian's model request (LB-12)."""


class RoutingDecision:
    """One classification result: which topics apply, and the model's one-line reason.

    Not a frozen dataclass of primitives because it is *never* a seam type — it lives entirely
    inside Layer 2, between the middleware that reads the tool call and the runtime that fans out.
    """

    __slots__ = ("reason", "topic_ids")

    def __init__(self, topic_ids: Sequence[str], reason: str = "") -> None:
        self.topic_ids = tuple(str(value).strip() for value in topic_ids if str(value).strip())
        self.reason = reason.strip()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RoutingDecision):
            return NotImplemented
        return self.topic_ids == other.topic_ids and self.reason == other.reason

    def __hash__(self) -> int:
        return hash((self.topic_ids, self.reason))

    def __repr__(self) -> str:
        return f"RoutingDecision(topic_ids={self.topic_ids!r}, reason={self.reason!r})"

    def as_state(self) -> dict[str, Any]:
        """The JSON-shaped form that survives a checkpoint round trip."""
        return {"topic_ids": list(self.topic_ids), "reason": self.reason}


def route_tool() -> BaseTool:
    """The ``route`` tool the Librarian carries (LB-12).

    Its body is a formality: :meth:`RouteMiddleware.after_model` intercepts the call, records the
    decision and answers it, so the function runs only in the unusual case where the model batched
    ``route`` alongside another tool call. It still returns something honest for that case.

    The description is the entire routing surface — it is what a model reads before deciding whether
    this is the tool to call — so it states the three things that go wrong without it: that several
    ids are normal, that the ids are the backticked strings from the catalog, and that answering in
    prose is not an option.
    """

    @tool(ROUTE_TOOL)
    def route(topic_ids: list[str], reason: str) -> str:
        """Hand this item to the Topic Experts that should handle it. Always call this first.

        You do not answer subject questions and you do not file anything yourself. This tool is how
        the item reaches the experts who do, and the system runs every expert you name and merges
        their answers for you afterwards.

        Args:
            topic_ids: Agent ids copied exactly from the catalog, e.g. ``topic/cooking``. Name every
                topic whose description covers the item — several is normal, and material that
                touches two subjects belongs with both. Leave the list empty only when no topic in
                the catalog fits at all.
            reason: One line saying what the item is about and why those topics cover it.
        """
        return ROUTE_ACK

    return route


# --------------------------------------------------------------------------------------
# Classification state (step 1's output, read by step 2)
# --------------------------------------------------------------------------------------

ROUTE_DECISION: Final = "route_decision"
"""State key holding this run's classification. Never spell the string elsewhere."""

ROUTE_ATTEMPTS: Final = "route_attempts"
"""State key counting the forced retries of the classification step (LB-13)."""


def take_last(left: Any, right: Any) -> Any:
    """Last write wins, ``None`` included — so ``before_agent`` can clear the key.

    A default ``LastValue`` channel would do this, but declaring the reducer explicitly is what
    documents that ``None`` is a *reset* rather than a no-op (the same argument as MW-6), and what
    keeps the annotation's last metadata element callable — langgraph reads ``__metadata__[-1]``
    only, and a non-callable there silently downgrades the channel.
    """
    return right


def add_attempt(left: int | None, right: int | None) -> int:
    """Accumulate retry counts; ``None`` resets, for the checkpointing reason MW-6 gives."""
    if right is None:
        return 0
    return (left or 0) + right


class RoutingState(AgentState):
    """``AgentState`` plus the two keys the routing step owns.

    Neither is marked ``PrivateStateAttr``, unlike the knowledge-base keys (MW-5), and that is the
    point: the runtime reads :data:`ROUTE_DECISION` back out of ``aget_state(...).values`` once the
    classification run has ended, which a private key would not survive. Leaking them into a
    delegated subagent's state is not a concern here — the Librarian registers no expert subagents
    any more (LB-12), and the general-purpose subagent has no use for either.
    """

    route_decision: NotRequired[Annotated[dict[str, Any] | None, take_last]]
    route_attempts: NotRequired[Annotated[int, add_attempt]]


def read_decision(values: Mapping[str, Any]) -> RoutingDecision | None:
    """Recover the classification from checkpointed state, tolerating a degraded shape."""
    raw = values.get(ROUTE_DECISION)
    if not isinstance(raw, Mapping):
        return None
    topic_ids = raw.get("topic_ids")
    if not isinstance(topic_ids, Sequence) or isinstance(topic_ids, str | bytes):
        return None
    reason = raw.get("reason")
    return RoutingDecision(list(topic_ids), reason if isinstance(reason, str) else "")


# --------------------------------------------------------------------------------------
# The middleware that makes step 1 terminate (LB-12, LB-13)
# --------------------------------------------------------------------------------------


class RouteMiddleware(AgentMiddleware[RoutingState, Any, Any]):
    """Ends the classification run at the routing decision, and forces one retry without it.

    Three jobs, all of them the harness doing what the prompt could only ask for:

    **It takes ``task`` away from the model** (:meth:`wrap_model_call`). deepagents auto-adds a
    ``general-purpose`` subagent to every deep agent and therefore always registers ``task``; the
    tool cannot be removed from the graph without a process-global harness profile keyed by model id
    (rejected for the same reason Q7-b was). Removing it from the *model request* is per-graph and
    achieves the thing that matters: with routing in code, a Librarian that can still call ``task``
    has a bypass, and a bypass is what this whole module exists to close.

    **It ends the run at the ``route`` call** (:meth:`after_model`). The call never executes: the
    hook records the decision, writes the ToolMessage that answers it, and returns
    ``{"jump_to": "end"}``. That is MW-15's proven pattern — ``end`` resolves to ``exit_node``, which
    *is* the ``after_agent`` chain, so the maintenance flush still runs and the thread stays
    resumable — and it saves the second model call whose only possible output would be a fabricated
    answer about experts that have not run yet.

    **It forces the retry** (:meth:`after_model` again). A model that answers in prose is sent back
    to the model node with :data:`RETRY_INSTRUCTION` appended, exactly :data:`MAX_ROUTE_ATTEMPTS`
    times. After that the run ends with the model's own words and no decision, and the runtime turns
    that into the human's menu.

    One case is deliberately left alone: an ``AIMessage`` that calls ``route`` *and* something else.
    The decision is recorded, nothing is jumped, and the tools node runs every call including
    ``route`` (whose body returns :data:`ROUTE_ACK`). Ending the run there would strand the sibling
    call unanswered, and an ``AIMessage`` with an unanswered ``tool_calls`` entry is rejected by real
    providers on the next turn — "the thread stays resumable" would become false.
    """

    name = "RouteMiddleware"
    state_schema = RoutingState

    # -- run entry ----------------------------------------------------------------------

    def before_agent(self, state: RoutingState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        """Clear both keys at run entry, so a decision found afterwards is *this* run's (LB-13).

        This is what removes the need for any consumption bookkeeping in the runtime: state is
        checkpointed, so without the reset turn 2 of a thread would find turn 1's decision and fan
        out a second time over an item nobody re-sent. ``before_agent`` deliberately does not re-run
        on an interrupt resume (verified on the pin), so a decision taken before an approval gate
        survives the human's pause.
        """
        return {ROUTE_DECISION: None, ROUTE_ATTEMPTS: None}

    async def abefore_agent(
        self, state: RoutingState, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        """Async twin of :meth:`before_agent` (MW-2). The runtime is async-only (RT-3)."""
        return self.before_agent(state, runtime)

    # -- the model request --------------------------------------------------------------

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelCallResult[Any]:
        """Run the model call with ``task`` withheld (LB-12)."""
        return handler(_without_task(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelCallResult[Any]:
        """Async twin of :meth:`wrap_model_call` (MW-2)."""
        return await handler(_without_task(request))

    # -- the decision -------------------------------------------------------------------

    @hook_config(can_jump_to=["end", "model"])
    def after_model(self, state: RoutingState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        """Record the routing decision, or force the one retry. See the class docstring."""
        return self._decide(state)

    @hook_config(can_jump_to=["end", "model"])
    async def aafter_model(
        self, state: RoutingState, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        """Async twin of :meth:`after_model` (MW-2). Pure state inspection, so no worker thread."""
        return self._decide(state)

    def _decide(self, state: RoutingState) -> dict[str, Any] | None:
        message = _last_ai_message(state.get("messages") or ())
        if message is None:
            return None
        route_calls = [call for call in message.tool_calls if call.get("name") == ROUTE_TOOL]
        if route_calls:
            return self._recorded(message, route_calls)
        if message.tool_calls:
            # It called something else — `create_topic` for a topic gap, or a read while it thinks.
            # Neither is a routing failure and neither is ours to interrupt.
            return None
        return self._retry(state)

    def _recorded(
        self, message: AIMessage, route_calls: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """The classification, plus the end of the run when ``route`` was the only call."""
        decision = _decision_of(route_calls[-1].get("args"))
        update: dict[str, Any] = {ROUTE_DECISION: decision.as_state()}
        if len(route_calls) != len(message.tool_calls):
            return update
        answers: list[AnyMessage] = [
            ToolMessage(content=ROUTE_ACK, name=ROUTE_TOOL, tool_call_id=str(call.get("id") or ""))
            for call in route_calls
        ]
        return {**update, "jump_to": "end", "messages": answers}

    def _retry(self, state: RoutingState) -> dict[str, Any] | None:
        """Send a prose answer back to the model once, then let the run end without a decision."""
        if state.get(ROUTE_DECISION):
            return None
        attempts = state.get(ROUTE_ATTEMPTS) or 0
        if attempts >= MAX_ROUTE_ATTEMPTS:
            return None
        return {
            ROUTE_ATTEMPTS: 1,
            "jump_to": "model",
            "messages": [HumanMessage(content=RETRY_INSTRUCTION)],
        }


def _without_task(request: ModelRequest[Any]) -> ModelRequest[Any]:
    """The same request with deepagents' ``task`` tool withheld from the model (LB-12)."""
    kept = [item for item in request.tools if _tool_name(item) != _TASK_TOOL]
    if len(kept) == len(request.tools):
        return request
    return request.override(tools=kept)


def _tool_name(item: Any) -> str:
    if isinstance(item, Mapping):
        return str(item.get("name") or "")
    return str(getattr(item, "name", "") or "")


def _last_ai_message(messages: Sequence[AnyMessage]) -> AIMessage | None:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message
    return None


def _decision_of(args: Any) -> RoutingDecision:
    """Read a ``route`` call's arguments, tolerating everything a small model does to them.

    A single string instead of a list, ``None``, a comma-joined string: each was cheaper to accept
    than to refuse, because the refusal path costs a whole extra turn and the accepted forms are
    unambiguous. An id that does not exist is *not* corrected here — it is reported to the human by
    :func:`merge_reply`, because silently dropping a name the model chose hides a routing fault.
    """
    if not isinstance(args, Mapping):
        return RoutingDecision(())
    raw = args.get("topic_ids")
    reason = args.get("reason")
    if isinstance(raw, str):
        ids: list[str] = [part for part in (piece.strip() for piece in raw.split(",")) if part]
    elif isinstance(raw, Sequence):
        ids = [str(value) for value in raw]
    else:
        ids = []
    return RoutingDecision(ids, reason if isinstance(reason, str) else "")


# --------------------------------------------------------------------------------------
# Threads (step 4's precondition)
# --------------------------------------------------------------------------------------

# `EXPERT_THREAD_SEPARATOR`, `expert_thread_id` and `librarian_thread_id` are **re-exported from
# `pkb.contracts`**, not defined here (C-1). Layer 3 must produce a derived thread id (SS-10, ST-12)
# and the transports may not import this module, so the one copy sits in the seam — the precedent
# `validate_decisions` set. A second implementation of an id convention is the class of duplication
# that fails silently: a thread resolving to the wrong agent shares a checkpoint (D-6).
#
# They stay importable from here because that is where LB-14 says the derivation belongs, and a test
# asserts the objects are *identical*, not merely equal.


# --------------------------------------------------------------------------------------
# Step 2 — the fan-out
# --------------------------------------------------------------------------------------

ExpertStatus = str
"""How one expert's run ended: ``answered``, ``failed``, ``awaiting-approval`` or ``busy``.

Deliberately **not** a "declined" value. Whether an expert had anything to contribute is its own
judgement, expressed in its own words in its own section; deriving a status from that would mean
reading the answer to decide what the answer meant. A fan-out where two of four experts file and two
decline is a success, and this taxonomy says so by having no way to express otherwise.
"""

ANSWERED: Final[ExpertStatus] = "answered"
FAILED: Final[ExpertStatus] = "failed"
AWAITING_APPROVAL: Final[ExpertStatus] = "awaiting-approval"
BUSY: Final[ExpertStatus] = "busy"

_WRITE_TOOLS: Final = frozenset({"write_file", "edit_file"})
"""Tools whose success means this expert filed something. Used for the reply's ``Filed:`` line."""


@dataclass(slots=True)
class ExpertOutcome:
    """What one expert's run produced, as the merge step sees it.

    Mutable and Layer-2-internal: it is filled in while the run streams. Nothing here crosses the
    seam — the merged *text* does.
    """

    agent_id: str
    title: str
    thread_id: str
    status: ExpertStatus = ANSWERED
    text: str = ""
    """The expert's own final message, verbatim. The merge never rewrites it."""

    filed: list[str] = field(default_factory=list)
    """Backend paths this run wrote successfully, in order, deduplicated."""

    error: str = ""


class FanOutHost(Protocol):
    """What :class:`FanOut` needs from the runtime, and nothing more.

    A Protocol rather than an import so this module stays independent of ``pkb.agents.runtime``,
    which imports the registry, which imports the Librarian factory, which imports this module.
    """

    @property
    def fanout_limit(self) -> int:
        """How many expert runs may be in flight at once (LB-15).

        Read-only in the Protocol so a host may satisfy it with a plain attribute *or* a property
        reading its own configuration — the runtime does the latter, and a settable declaration here
        would reject it.
        """
        ...

    def expert_stream(
        self,
        agent_id: str,
        thread_id: str,
        message: str,
        *,
        run_id: str,
        approval_mode: ApprovalMode,
    ) -> AsyncIterator[AgentEvent]:
        """One expert's run, with every runtime guarantee the direct path has."""
        ...


class FanOut:
    """Step 2: run every applicable expert, bounded, and report what each produced (LB-15 … LB-17).

    **Bounded on purpose.** The deployment's plan allows three concurrent cloud models, so a
    five-topic question must not fire five concurrent runs — the fourth and fifth would arrive as a
    ``429`` and be reported as failures of the knowledge base rather than of the plan. The cap is
    :attr:`FanOutHost.fanout_limit`, applied with a semaphore, and work runs concurrently up to it.

    **One expert failing must not lose the others** (LB-17). Every per-expert failure — the run
    raising, the thread already busy, the thread parked on an earlier approval — is captured into
    that expert's :class:`ExpertOutcome` and the remaining experts still deliver. The expert's own
    ``run.error`` event is *not* forwarded: the Librarian's turn did not fail, and RT-47's contract
    is one terminal event per run, which for this turn is the merged reply.

    **An expert that hits an approval gate parks on its own thread** (LB-16). Because each expert
    runs on its own derived thread (:func:`expert_thread_id`), its interrupt is durable there and
    resolvable there — the Librarian's thread is never left interrupted by a delegate, so the next
    turn on it is not refused by RT-39, and the other experts in the same fan-out are unaffected.
    The :class:`~pkb.contracts.InterruptEvent` *is* forwarded, carrying that expert's agent id and
    thread id, so a client can render the approval and answer it with
    ``resume(agent_id=<expert>, thread_id=<derived>)``. Ingestion fan-out makes this common: several
    experts may each want to file, and each gate belongs to the expert that raised it.
    """

    def __init__(
        self,
        host: FanOutHost,
        targets: Sequence[AgentDescriptor],
        message: str,
        *,
        thread_id: str,
        run_id: str,
        approval_mode: ApprovalMode = "interactive",
        reason: str = "",
    ) -> None:
        self.host = host
        self.targets = list(targets)
        self.message = message
        self.thread_id = thread_id
        self.run_id = run_id
        self.approval_mode: ApprovalMode = approval_mode
        self.reason = reason
        self.outcomes: list[ExpertOutcome] = [
            ExpertOutcome(
                agent_id=descriptor.agent_id,
                title=descriptor.title,
                thread_id=expert_thread_id(thread_id, descriptor.agent_id),
            )
            for descriptor in self.targets
        ]

    async def stream(self) -> AsyncIterator[AgentEvent]:
        """Run every target, yielding their events as they arrive.

        Events are interleaved through one bounded queue rather than gathered and replayed, because
        a client showing "→ routing to the Cooking expert" wants it while the expert is running, not
        after every expert has finished. The tasks are cancelled if the consumer walks away — an
        abandoned generator otherwise leaves expert runs parked on a full queue forever, and each of
        those holds an active-run slot (RT-45).
        """
        if not self.outcomes:
            return
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue(maxsize=_FANOUT_BUFFER)
        limit = max(1, self.host.fanout_limit)
        gate = asyncio.Semaphore(limit)
        tasks = [asyncio.create_task(self._one(outcome, queue, gate)) for outcome in self.outcomes]
        pending = len(tasks)
        try:
            while pending:
                event = await queue.get()
                if event is None:
                    pending -= 1
                    continue
                yield event
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def _one(
        self,
        outcome: ExpertOutcome,
        queue: asyncio.Queue[AgentEvent | None],
        gate: asyncio.Semaphore,
    ) -> None:
        """One expert's whole run, from the semaphore to its ``SubagentEnd``."""
        try:
            async with gate:
                await queue.put(SubagentStart(run_id=self.run_id, agent_id=outcome.agent_id))
                await self._consume(outcome, queue)
                await queue.put(
                    SubagentEnd(
                        run_id=self.run_id, agent_id=outcome.agent_id, status=outcome.status
                    )
                )
        finally:
            await queue.put(None)

    async def _consume(
        self, outcome: ExpertOutcome, queue: asyncio.Queue[AgentEvent | None]
    ) -> None:
        """Drive one expert and fold its stream into *outcome*, forwarding what a client needs."""
        envelope = routing_envelope(self.message, title=outcome.title, reason=self.reason)
        opened: dict[str, str] = {}
        try:
            async for event in self.host.expert_stream(
                outcome.agent_id,
                outcome.thread_id,
                envelope,
                run_id=self.run_id,
                approval_mode=self.approval_mode,
            ):
                forward = self._fold(outcome, event, opened)
                if forward is not None:
                    await queue.put(forward)
        except ThreadBusyError as exc:
            outcome.status = BUSY
            outcome.error = str(exc)
        except ApprovalPendingError as exc:
            # Its own thread is still parked on an earlier decision (RT-39). Not a failure of this
            # turn — a fact about that expert, and the human already knows what it is waiting for.
            outcome.status = AWAITING_APPROVAL
            outcome.error = str(exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one expert must not take the others down (LB-17)
            outcome.status = FAILED
            outcome.error = str(exc) or type(exc).__name__

    def _fold(
        self, outcome: ExpertOutcome, event: AgentEvent, opened: dict[str, str]
    ) -> AgentEvent | None:
        """Record what the merge needs; return the event to forward, or ``None`` to swallow it."""
        if isinstance(event, RunEnd):
            outcome.text = event.final_text
            return None
        if isinstance(event, RunError):
            outcome.status = FAILED
            outcome.error = event.message
            return None
        if isinstance(event, InterruptEvent):
            outcome.status = AWAITING_APPROVAL
            return event
        if isinstance(event, ToolStart) and event.tool in _WRITE_TOOLS:
            # The path is only on the *start* event — a `ToolEnd` carries the tool's own message —
            # so what landed is learned by pairing the two, and only a successful end counts (MW-17).
            opened[event.tool] = to_kb_relative(event.summary) or ""
        elif isinstance(event, ToolEnd) and event.tool in _WRITE_TOOLS:
            path = opened.pop(event.tool, "")
            if path and not event.error and path not in outcome.filed:
                outcome.filed.append(path)
        return event


_FANOUT_BUFFER: Final = 64
"""Bound on the queue between the expert runs and the caller, matching the runtime's own.

Bounded so a slow consumer applies backpressure to the fan-out instead of letting several parallel
generations accumulate in memory; large enough that no ordinary turn ever blocks on it.
"""


def routing_envelope(message: str, *, title: str, reason: str) -> str:
    """What one expert actually receives: the human's item, verbatim, inside a fixed frame.

    The human's words are last and unmodified — an expert that ingests a paraphrase files a
    paraphrase. The frame around them does two things no prompt can do from the expert's side: it
    says the Librarian routed this (so the expert knows it is not talking to the human directly),
    and it says declining is a correct outcome. That second sentence is what makes the human's
    ruling work: several experts get the same source and each extracts only its own facet, so an
    expert with nothing to extract must be free to file nothing rather than inventing relevance.
    """
    lines = [f"The Librarian routed this to you as the expert for {title}."]
    if reason:
        lines.append(f"Its reason: {reason}")
    lines.append(
        "Handle only the part that belongs to your topic, through your own lens. If none of it "
        "does, say so plainly in one line and file nothing — that is a correct outcome, not a "
        "failure, and another expert is handling the rest."
    )
    return "\n".join(lines) + "\n\n---\n\n" + message


# --------------------------------------------------------------------------------------
# Resolving what the model named
# --------------------------------------------------------------------------------------


def resolve_targets(
    decision: RoutingDecision | None, catalog: Sequence[AgentDescriptor]
) -> tuple[list[AgentDescriptor], list[str]]:
    """Turn named ids into catalog entries: ``(targets, unknown)``.

    Order is the model's, deduplicated — the classifier's ordering is its relevance judgement and it
    is the one part of the reply's shape the model still owns. Ids it invented are returned
    separately rather than dropped: a hallucinated ``topic/atlantis`` is a routing fault the human
    should see once, in the reply, not a silent narrowing of who was asked.
    """
    if decision is None:
        return [], []
    by_id = {descriptor.agent_id: descriptor for descriptor in catalog}
    targets: list[AgentDescriptor] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for agent_id in decision.topic_ids:
        if agent_id in seen:
            continue
        seen.add(agent_id)
        descriptor = by_id.get(agent_id)
        if descriptor is None:
            unknown.append(agent_id)
        else:
            targets.append(descriptor)
    return targets, unknown


# --------------------------------------------------------------------------------------
# Step 3 — the merge, and step 4 — the offer
# --------------------------------------------------------------------------------------

NO_MESSAGE: Final = "_(this expert returned no message)_"

_STATUS_NOTE: Final[Mapping[ExpertStatus, str]] = {
    FAILED: "This expert's run failed and its part of the answer is missing.",
    AWAITING_APPROVAL: "This expert is waiting on your decision before it can finish.",
    BUSY: "This expert is already busy on another turn and did not run.",
}


def merge_reply(outcomes: Sequence[ExpertOutcome], *, unknown: Sequence[str] = ()) -> str:
    """Step 3 and step 4: one reply, assembled from what the experts actually returned (LB-18).

    **Deterministic, and never a synthesis model call.** This is the rule the whole change turns on.
    A model asked to merge is a model free to say "the Cooking expert checked the knowledge base"
    when no expert ran — that sentence was observed, and it is unfalsifiable from the outside.
    A reply assembled here cannot make that claim: a section exists only because an expert ran, it
    carries that expert's own words verbatim under its own title and agent id, and an expert that
    failed gets a section saying so rather than being quietly summarised away.

    **Step 4 rides on the same structure.** Each section names the expert's agent id and the thread
    it ran on, and the closing line lists them together, so a client has everything it needs to
    offer "continue with the Cooking expert" as a link to a real, openable conversation.
    """
    parts: list[str] = [_headline(outcomes)]
    if unknown:
        named = ", ".join(f"`{agent_id}`" for agent_id in unknown)
        parts.append(f"No topic in this knowledge base answers to {named}; nothing was sent there.")
    parts.extend(_section(outcome) for outcome in outcomes)
    offer = _offer(outcomes)
    if offer:
        parts.append(offer)
    return "\n\n".join(parts)


def _headline(outcomes: Sequence[ExpertOutcome]) -> str:
    """One deterministic line: how many experts were asked, and how many are missing."""
    if not outcomes:
        return "No expert was asked."
    total = len(outcomes)
    missing = [outcome for outcome in outcomes if outcome.status != ANSWERED]
    asked = f"{total} expert{'s' if total != 1 else ''}"
    if not missing:
        return f"Asked {asked}; each answered for its own topic, below."
    return (
        f"Asked {asked}; {len(missing)} could not finish. "
        "Every expert's own answer is under its own heading, unchanged."
    )


def _section(outcome: ExpertOutcome) -> str:
    """One expert's contribution: its title, its id, its words, what it filed, how to continue."""
    lines = [f"## {outcome.title} — `{outcome.agent_id}`", ""]
    note = _STATUS_NOTE.get(outcome.status)
    if note:
        lines.append(f"_{note}_" if not outcome.error else f"_{note} ({outcome.error})_")
        lines.append("")
    lines.append(outcome.text.strip() or NO_MESSAGE)
    if outcome.filed:
        filed = ", ".join(f"`{path}`" for path in outcome.filed)
        lines.extend(["", f"_Filed: {filed}_"])
    lines.extend(
        [
            "",
            f"_Continue with this expert: agent `{outcome.agent_id}`, thread `{outcome.thread_id}`._",
        ]
    )
    return "\n".join(lines)


def _offer(outcomes: Sequence[ExpertOutcome]) -> str:
    """Step 4's closing line — the agent ids a client turns into "continue with…" buttons."""
    reachable = [outcome for outcome in outcomes if outcome.status != BUSY]
    if not reachable:
        return ""
    named = ", ".join(
        f"`{outcome.agent_id}` (thread `{outcome.thread_id}`)" for outcome in reachable
    )
    return f"You can carry on directly with any of them: {named}."


# --------------------------------------------------------------------------------------
# The uncertainty fallback — a menu, never a guess
# --------------------------------------------------------------------------------------

MENU_HEADER: Final = "I could not work out which expert should handle this."
MENU_QUESTION: Final = "Which of these would you like me to engage?"
MENU_FOOTER: Final = (
    "Reply with the ones you want. If none of them fit, say so and I will propose a new topic "
    "instead."
)


def routing_menu(candidates: Sequence[AgentDescriptor], *, prose: str = "") -> str:
    """Ask the human which experts to engage, listing them (LB-19).

    The human's instruction, verbatim: *"If model has issue routing — harness can ask consumer:
    which of the following experts would you want to engage for your question."* So this is a
    **choice**, not an open question and not a guess. A wrong guess files knowledge in the wrong
    topic and there is no undo (D6), which is the whole reason the uncertain path ends with the
    human rather than with the model's second-best idea.

    Whatever the model did say is quoted rather than discarded: it may be an escalation, or the one
    sentence that tells the human which option to pick, and hiding it would make the menu look like
    the system had nothing at all.
    """
    parts = [MENU_HEADER]
    quoted = _quote(prose)
    if quoted:
        parts.append(quoted)
    parts.append(MENU_QUESTION)
    parts.append(
        "\n".join(
            f"- `{descriptor.agent_id}` — **{descriptor.title}**: {descriptor.description}"
            for descriptor in candidates
        )
    )
    parts.append(MENU_FOOTER)
    return "\n\n".join(parts)


def _quote(text: str) -> str:
    """Render the model's own words as a markdown quote, or nothing at all."""
    body = text.strip()
    if not body:
        return ""
    return "\n".join(f"> {line}" if line else ">" for line in body.splitlines())
