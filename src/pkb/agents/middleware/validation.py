"""The gate every agent write passes through — MW-7 … MW-19.

This is the mechanism the architecture's §7 rests on: an invalid write **never lands**, and the
agent sees exactly what Layer 1 objected to, in Layer 1's own words, in time to fix it inside the
same run. Nothing here decides *what* is valid — :func:`pkb.core.validate_content` does, through
the single call site in :meth:`KbValidationMiddleware._findings` (MW-9). A second opinion about
required fields, tag depth or naming living in this file would be a defect, not a shortcut.

Four behaviours look like accidents and are not. Each exists because the harness does something the
approved architecture did not describe, and each would be "simplified" straight back into the bug:

1. **The raw path is normalized before anything else** (D-3, RT-9). ``request.tool_call["args"]``
   holds the *model's* string; deepagents normalizes only later, inside the tool body. A model that
   emits ``kb/Cooking/notes/b.md`` reaches this middleware verbatim, a ``startswith("/kb/")`` test
   says "not mine", and the tool then writes it into the tree unvalidated. That bypass was executed
   against the pin. :func:`pkb.agents.paths.to_kb_relative` is the only sanctioned entry point.

2. **``edit_file`` carries no content** (D-4). Its arguments are ``file_path``/``old_string``/
   ``new_string``/``replace_all``, so the *resulting* file has to be reconstructed before it can be
   validated — with ``deepagents.backends.utils.perform_string_replacement``, the exact function
   ``FilesystemBackend.edit`` calls, so the simulation cannot diverge from the write. That is
   :func:`pkb.agents.gates.proposed_content`, shared with the approval gate so there is one
   simulation, not two (MW-10).

3. **Derived paths early-return** (D-13, MW-11). Arch §7 assumed the validator never sees a write to
   a generated file because I3 forbids it. It does see it: permissions are enforced *inside* the
   tool body, after every ``wrap_tool_call``. If this middleware also refused, one write would
   produce two contradictory refusals. It defers, and the permission denial is the only message.
   Which names are derived is :func:`pkb.core.is_derived_name`'s answer — this module names no
   knowledge-base file, so a grep for a derived file name in the validation path finds nothing
   (MW-9).

4. **Warnings ride along on the success message instead of blocking** (MW-12). Layer 1 chose
   warning severity for VA-25/VA-29/VA-33/VA-35 precisely so they would not cost a retry, and a
   blocking warning is indistinguishable from an error to the model.

The attempt bound (MW-14, MW-15) closes the loop that all of this opens: three refusals per file
per run, then :meth:`~KbValidationMiddleware.after_model` ends the run with an escalation instead of
letting the agent grind. It ends it by returning ``{"jump_to": "end"}``, **never**
``Command(goto=END)`` — ``END`` and the graph's ``exit_node`` are different nodes, and only
``exit_node`` runs the ``after_agent`` chain that flushes the derived files (MW-16).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

from langchain.agents.middleware.types import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from pkb.agents.gates import proposed_content
from pkb.agents.middleware.state import KB_ATTEMPTS, KB_TOUCHED, KbAgentState
from pkb.agents.paths import to_kb_relative
from pkb.core import (
    Finding,
    Severity,
    errors_only,
    has_errors,
    is_derived_name,
    render_findings,
    validate_content,
)

__all__ = [
    "ADVISORY_HEADER",
    "MAX_ATTEMPTS",
    "RECORDED_TOOLS",
    "VALIDATED_TOOLS",
    "KbValidationMiddleware",
]


# --------------------------------------------------------------------------------------
# The harness's vocabulary (MW-7) — deepagents 0.7.5 tool and argument names
# --------------------------------------------------------------------------------------

WRITE_FILE_TOOL: Final = "write_file"
EDIT_FILE_TOOL: Final = "edit_file"
DELETE_TOOL: Final = "delete"
FILE_PATH_ARG: Final = "file_path"

VALIDATED_TOOLS: Final[frozenset[str]] = frozenset({WRITE_FILE_TOOL, EDIT_FILE_TOOL})
"""The two tools whose proposal is validated before it is allowed to run (MW-7)."""

RECORDED_TOOLS: Final[frozenset[str]] = frozenset({WRITE_FILE_TOOL, EDIT_FILE_TOOL, DELETE_TOOL})
"""The tools whose success marks a KB path touched. ``delete`` is here for MW-19: a removed note
still listed in the generated index and tag registry is the stale-derived-file state arch §7 calls
worse than the write itself. It is never *validated* — there is nothing proposed to validate."""

MAX_ATTEMPTS: Final = 3
"""Refused writes allowed per file per run before the human is asked (MW-14)."""

ADVISORY_LIMIT: Final = 3
"""How many non-blocking findings ride along on a success message (MW-12's "a few lines")."""

ADVISORY_HEADER: Final = "Advisory — the write succeeded; these findings do not block it:"

NOT_RUN_MESSAGE: Final = "Not run: the agent stopped to ask the human about another tool call."


# --------------------------------------------------------------------------------------
# What one intercepted tool call resolves to
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Forward:
    """Run the tool.

    ``rel`` is the KB-relative path to record as touched **if** the tool reports success (MW-17),
    or ``None`` when this call is none of our business — a scratch path, a read tool, a path the
    harness itself will refuse. ``advisory`` is the non-blocking findings block appended to the
    success message (MW-12).
    """

    rel: str | None = None
    advisory: str = ""


@dataclass(frozen=True, slots=True)
class _Refuse:
    """Do not run the tool; return this text as an error ``ToolMessage`` instead (MW-13)."""

    rel: str
    content: str


_Decision = _Forward | _Refuse


# --------------------------------------------------------------------------------------
# The middleware
# --------------------------------------------------------------------------------------


class KbValidationMiddleware(AgentMiddleware[KbAgentState]):
    """Refuses a knowledge-base write that Layer 1 would reject, before it reaches the backend.

    Implements MW-2 … MW-19 plus RT-9/RT-10. Attach it to every graph that can write to the
    knowledge base — including the explicit ``general-purpose`` subagent, which inherits no custom
    middleware of its own (D-2, EX-11).

    Instance attributes are read-only configuration (MW-4): one instance serves every run of a
    compiled graph, and every concurrent delegation within one Librarian turn (LB-8), so per-run
    state lives in :class:`~pkb.agents.middleware.state.KbAgentState` and nowhere else.
    """

    state_schema = KbAgentState

    def __init__(self, kb_root: Path, *, max_attempts: int = MAX_ATTEMPTS) -> None:
        """Configure the middleware.

        Args:
            kb_root: The knowledge base root on disk. Layer 1 speaks paths relative to it; the
                ``/kb/`` mount is spelled only in :mod:`pkb.agents.paths` (RT-8).
            max_attempts: Refused writes allowed per file per run before escalating (MW-14).
        """
        super().__init__()
        self.kb_root = kb_root
        self.max_attempts = max_attempts

    # -- run entry ----------------------------------------------------------------------

    def before_agent(self, state: KbAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        """Clear the attempt counter at run entry (MW-14).

        The factory wires ``before_agent`` as a once-per-run entry node, so "three attempts per
        file" means per graph invocation — not per model turn, and not per thread. State is
        checkpointed, so without this reset turn 2 of a thread would inherit turn 1's exhausted
        counters and escalate on its first refusal.

        It deliberately does **not** re-run on an interrupt resume (verified against the pin), so
        attempts made before an approval survive the pause rather than silently resetting.
        """
        return {KB_ATTEMPTS: None}

    async def abefore_agent(
        self, state: KbAgentState, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        """Async twin of :meth:`before_agent` (MW-2). The runtime is async-only (RT-3)."""
        return self.before_agent(state, runtime)

    # -- the escalation (MW-15, MW-16) --------------------------------------------------

    @hook_config(can_jump_to=["end"])
    def after_model(self, state: KbAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        """End the run with an escalation when a file has exhausted its attempts (MW-15).

        Fires when the model asks to write a path that has already been refused
        :data:`MAX_ATTEMPTS` times *and* whose new proposal still fails validation. Both halves
        matter: if the model finally fixed the draft, the write must go through — the bound exists
        to stop a loop, not to blacklist a path.

        Returning ``{"jump_to": "end"}`` resolves to the graph's ``exit_node``, which **is** the
        ``after_agent`` chain, so the maintenance flush still runs and the derived files match the
        tree. ``Command(goto=END)`` would reach a different node and skip it (MW-16). The run ends
        normally, so the thread stays resumable on any channel.

        The escalation also answers every pending tool call in the message. An ``AIMessage`` whose
        ``tool_calls`` have no matching ``ToolMessage`` is rejected by real providers on the next
        turn, which would make "the thread stays resumable" false.
        """
        return self._escalation(state)

    @hook_config(can_jump_to=["end"])
    async def aafter_model(
        self, state: KbAgentState, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        """Async twin of :meth:`after_model` (MW-2).

        The escalation check re-validates a proposal, which reads the tree; that blocking Layer 1
        work goes to a worker thread so the event loop keeps serving other runs (MW-3).
        """
        return await asyncio.to_thread(self._escalation, state)

    # -- the write gate (MW-7 … MW-19) --------------------------------------------------

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Validate a proposed knowledge-base write, or forward the call untouched.

        On refusal the handler is **never invoked** (MW-13): the write does not reach the backend,
        the agent receives Layer 1's findings verbatim, and it self-corrects inside the same run.
        """
        decision = self._decide(request)
        if isinstance(decision, _Refuse):
            return self._refusal(request, decision)
        return self._record(decision, handler(request), request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Async twin of :meth:`wrap_tool_call` (MW-2).

        A sync-only ``wrap_tool_call`` raises ``NotImplementedError`` under ``ainvoke()``, and the
        runtime exposes no sync run API at all (RT-3) — so this variant is the production path and
        the sync one is what the test suite drives. The decision itself walks the tree and reads
        files, so it runs in a worker thread (MW-3).
        """
        decision = await asyncio.to_thread(self._decide, request)
        if isinstance(decision, _Refuse):
            return self._refusal(request, decision)
        return self._record(decision, await handler(request), request)

    # -- decision ------------------------------------------------------------------------

    def _decide(self, request: ToolCallRequest) -> _Decision:
        """Everything the middleware knows before the tool runs. Pure apart from reading the tree.

        Ordered exactly as MW-7/MW-8/MW-11 require: tool name, then normalization, then the
        derived-path deferral, then Layer 1's verdict. Each early return is a rule.
        """
        tool = request.tool_call["name"]
        if tool not in RECORDED_TOOLS:
            return _Forward()  # MW-7: every other tool is forwarded and records nothing.

        args = request.tool_call["args"]
        rel = to_kb_relative(args.get(FILE_PATH_ARG))
        if rel is None:
            # MW-8/RT-10: agent scratch under the StateBackend default route, another mount, or a
            # path the harness itself refuses. Zero validation calls, and deepagents owns the error.
            return _Forward()

        if tool not in VALIDATED_TOOLS:
            return _Forward(rel=rel)  # MW-19: a delete is recorded, never validated.

        if is_derived_name(self.kb_root, self.kb_root / rel):
            # MW-11/D-13: I3 refuses this inside the tool body a moment from now. Emitting a
            # finding here would give one refused write two ToolMessages that disagree.
            return _Forward()

        findings = self._findings(tool, rel, args)
        if findings is None:
            # The proposal could not be reconstructed — deepagents' own error (a missing file, an
            # `old_string` that does not match). Forward it and let the tool report it (MW-10).
            return _Forward(rel=rel)

        if has_errors(findings):
            attempt = self._attempts(request.state).get(rel, 0) + 1
            return _Refuse(rel=rel, content=self._refusal_text(rel, findings, attempt))
        return _Forward(rel=rel, advisory=_advisory_block(findings))

    def _findings(self, tool: str, rel: str, args: Mapping[str, Any]) -> list[Finding] | None:
        """Layer 1's verdict on what this call would leave on disk (MW-9, MW-10).

        The **only** ``validate_content`` call site in this module, and the only place Layer 2
        forms an opinion about a file's contents at all. ``None`` means "the resulting text cannot
        be determined", which is not a defect in the content — it is deepagents' error to report.

        ``ValueError`` from Layer 1 means a path outside the tree, which ``to_kb_relative`` has
        already excluded; catching it anyway keeps a surprise from aborting the superstep, because
        an aborted superstep also skips the maintenance flush (D-1).
        """
        proposal = proposed_content(tool, rel, args, self.kb_root)
        if proposal is None:
            return None
        try:
            return validate_content(self.kb_root, rel, proposal)
        except ValueError:
            return None

    # -- results -------------------------------------------------------------------------

    def _refusal(self, request: ToolCallRequest, decision: _Refuse) -> Command[Any]:
        """The refusal the agent reads, plus the attempt this refusal consumed (MW-13, MW-14).

        A bare ``ToolMessage`` cannot carry a state update, and the counter has to be part of the
        same update or a refusal would be free; ``ToolNode._validate_tool_command`` requires the
        matching ``ToolMessage`` inside ``Command.update["messages"]`` (MW-18). The counter update
        is a **delta** — the reducer adds — so two refused sibling calls on one path in a single
        ``AIMessage`` count as two attempts even though neither sees the other's update.
        """
        tool_call = request.tool_call
        message = ToolMessage(
            content=decision.content,
            name=tool_call["name"],
            tool_call_id=tool_call["id"],
            status="error",
        )
        return Command(update={"messages": [message], KB_ATTEMPTS: {decision.rel: 1}})

    def _record(
        self,
        decision: _Decision,
        result: ToolMessage | Command[Any],
        request: ToolCallRequest,
    ) -> ToolMessage | Command[Any]:
        """Record a successful KB write as touched, and attach any advisory (MW-17, MW-18, MW-12).

        A path is recorded **only** when the tool reported success. A permission denial, a backend
        error and a refused write all come back as error messages and must record nothing, or a
        *denied* write would still get that file's ``updated`` line bumped by the next flush, and
        the tree would claim a change that never happened (MW-17).
        """
        if not isinstance(decision, _Forward) or decision.rel is None:
            return result
        message = _tool_message_of(result, request.tool_call["id"])
        if message is None or message.status == "error":
            return result
        updated = _with_advisory(message, decision.advisory)
        return _with_state(result, updated, {KB_TOUCHED: [decision.rel]})

    # -- escalation ----------------------------------------------------------------------

    def _escalation(self, state: KbAgentState) -> dict[str, Any] | None:
        """The MW-15 check: is the model retrying a file whose attempts are exhausted?"""
        pending = _pending_tool_calls(state.get("messages") or [])
        attempts = self._attempts(state)
        for tool_call in pending:
            tool = tool_call["name"]
            if tool not in VALIDATED_TOOLS:
                continue
            rel = to_kb_relative(tool_call["args"].get(FILE_PATH_ARG))
            if rel is None or attempts.get(rel, 0) < self.max_attempts:
                continue
            if is_derived_name(self.kb_root, self.kb_root / rel):
                continue
            findings = self._findings(tool, rel, tool_call["args"])
            if findings is None or not has_errors(findings):
                continue
            return _escalation_update(
                pending=pending,
                offender_id=tool_call["id"],
                text=self._escalation_text(rel, findings, attempts[rel]),
                offender_note=(
                    f"Refused: {rel} failed validation on all {attempts[rel]} attempts. "
                    "Stopping and asking the human."
                ),
            )
        return None

    def _escalation_text(self, rel: str, findings: Sequence[Finding], attempts: int) -> str:
        """What the human is asked to decide (MW-15): the path, the blocking rules, the choice."""
        return (
            f"I have stopped trying to write {rel}. {attempts} attempts were refused by the "
            f"knowledge base's own rules and the current draft still does not pass, so rather "
            f"than keep retrying I am handing this to you. Nothing was written.\n\n"
            f"{render_findings(errors_only(findings))}\n\n"
            f"Please decide how to proceed: tell me what these fields should contain, edit the "
            f"file yourself, or drop the change."
        )

    def _refusal_text(self, rel: str, findings: Sequence[Finding], attempt: int) -> str:
        """Layer 1's findings, verbatim, under Layer 2's counter and next-step line (MW-13).

        Layer 2 adds exactly those two things. It never re-words, truncates or re-orders a Layer 1
        message: ``Finding.render()`` already emits the code, the rule id, the field and the hint
        the agent needs (CX-6), and ``sort_findings`` already puts errors first. Paraphrasing here
        would create a second, driftable copy of every Layer 1 message.
        """
        return (
            f"Refused — nothing was written to {rel}. "
            f"Attempt {attempt} of {self.max_attempts}; fix every finding below, then call the "
            f"tool again.\n{render_findings(errors_only(findings))}"
        )

    @staticmethod
    def _attempts(state: Any) -> Mapping[str, int]:
        """This run's refusal counts per path, tolerating a state that has never held the key."""
        if not isinstance(state, Mapping):
            return {}
        counts = state.get(KB_ATTEMPTS)
        return counts if isinstance(counts, Mapping) else {}


# --------------------------------------------------------------------------------------
# Message plumbing
# --------------------------------------------------------------------------------------


def _advisory_block(findings: Sequence[Finding]) -> str:
    """Non-blocking findings, capped, for the success message (MW-12).

    Warning- and info-severity findings never block: Layer 1 chose those severities for VA-25,
    VA-29, VA-33 and VA-35 *because* they should not cost one of three attempts, and a blocking
    warning is indistinguishable from an error to the model. Riding along on the success message is
    what lets the corpus converge on canonical form for free. The cap keeps a noisy file from
    burying the tool's own output.
    """
    advisory = [f for f in findings if f.severity is not Severity.ERROR]
    if not advisory:
        return ""
    shown = advisory[:ADVISORY_LIMIT]
    block = f"{ADVISORY_HEADER}\n{render_findings(shown)}"
    remaining = len(advisory) - len(shown)
    return block if remaining == 0 else f"{block}\n… and {remaining} more."


def _with_advisory(message: ToolMessage, advisory: str) -> ToolMessage:
    """Append the advisory block to a success message, leaving everything else alone."""
    if not advisory or not isinstance(message.content, str):
        return message
    return message.model_copy(update={"content": f"{message.content}\n\n{advisory}"})


def _tool_message_of(
    result: ToolMessage | Command[Any], tool_call_id: str | None
) -> ToolMessage | None:
    """The ``ToolMessage`` a handler produced, whether returned bare or inside a ``Command``.

    Every filesystem tool on this pin returns a bare ``ToolMessage`` — including ``StateBackend``
    writes, which mutate state through a config channel rather than a command. The ``Command``
    branch is what MW-17's "or a ``Command`` whose embedded ToolMessage is success" asks for, so a
    middleware added inside this one later cannot silently turn every write into an unrecorded one.
    """
    if isinstance(result, ToolMessage):
        return result
    update = result.update
    if not isinstance(update, dict):
        return None
    messages = update.get("messages")
    if not isinstance(messages, list):
        return None
    for message in messages:
        if isinstance(message, ToolMessage) and message.tool_call_id == tool_call_id:
            return message
    return None


def _with_state(
    result: ToolMessage | Command[Any], message: ToolMessage, update: Mapping[str, Any]
) -> Command[Any]:
    """Carry ``update`` back to the graph alongside the handler's own message (MW-18).

    The ``messages`` entry is mandatory: ``ToolNode._validate_tool_command`` raises unless the
    update holds a ``ToolMessage`` whose ``tool_call_id`` matches the call. This is the only way a
    tool result and a state update travel together.
    """
    if isinstance(result, ToolMessage):
        return Command(update={"messages": [message], **update})
    existing = result.update if isinstance(result.update, dict) else {}
    messages = [
        message if isinstance(m, ToolMessage) and m.tool_call_id == message.tool_call_id else m
        for m in existing.get("messages", [])
    ]
    return replace(result, update={**existing, "messages": messages, **update})


def _pending_tool_calls(messages: Sequence[AnyMessage]) -> list[dict[str, Any]]:
    """Tool calls of the last ``AIMessage`` that no ``ToolMessage`` has answered yet.

    Mirrors the routing edge's own reading of the message list: an answered call has already run
    (HumanInTheLoopMiddleware injects a ``ToolMessage`` for a rejected one before this hook sees
    the state), so only the unanswered ones are about to execute.
    """
    last_ai: AIMessage | None = None
    answered: set[str] = set()
    for message in messages:
        if isinstance(message, AIMessage):
            last_ai = message
            answered = set()
        elif isinstance(message, ToolMessage):
            answered.add(message.tool_call_id)
    if last_ai is None:
        return []
    return [dict(tc) for tc in last_ai.tool_calls if tc["id"] not in answered]


def _escalation_update(
    *,
    pending: Sequence[Mapping[str, Any]],
    offender_id: str,
    text: str,
    offender_note: str,
) -> dict[str, Any]:
    """The state update that ends the run at ``exit_node`` with an explanation (MW-15, MW-16)."""
    answers: list[AnyMessage] = [
        ToolMessage(
            content=offender_note if tool_call["id"] == offender_id else NOT_RUN_MESSAGE,
            name=tool_call["name"],
            tool_call_id=tool_call["id"],
            status="error",
        )
        for tool_call in pending
    ]
    return {"jump_to": "end", "messages": [*answers, AIMessage(content=text)]}
