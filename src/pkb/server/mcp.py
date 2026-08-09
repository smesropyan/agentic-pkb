"""The MCP surface — four tools, two resources and a template (MC-1 … MC-23).

This is how the Project Manager (and any other external agent) reaches the knowledge base. It is a
transport like the TUI and Telegram: it imports ``pkb.contracts`` and the :class:`PkbService`
Protocol, and nothing else — never ``pkb.agents``, never a harness module, never an HTTP client
(MC-7). A tool call goes down the same ``PkbService`` methods a human's keystroke does.

Four rules make the difference between this being a channel and being a hole:

* **Every MCP run is ``propose_only``** (MC-8). The mode is a property of the *channel*, not a tool
  argument: no tool exposes it and no path here passes ``"interactive"``. Interactive mode needs a
  human on the call path and there is none behind ``/mcp``, so an interactive write that gated would
  hang forever on a decision no robot can make. The contract is that MCP sees **zero** ``interrupt``
  events — it must never block and never poll for an approval.
* **Propose-only is not read-only** (MC-18). The gate table is the boundary and it is the same table
  for every channel: a plain note lands unattended, a breadth summary becomes a proposal. Every
  result therefore has to **distinguish filed from proposed**, because conflating them makes an
  external agent believe a summary update landed when it did not.
* **An escalation is a success with a discriminator, never an error** (MC-20). A well-behaved agent
  retries errors, and a retried escalation is an escalation ignored. The trigger is computed
  deterministically from ``status.conflict-review`` intersected with the participating topics —
  never from what a model said it read — and it self-clears when the human resolves the tag.
* **A tool's return annotation names only its success envelope**, even though a coded failure
  returns a ``CallToolResult`` at runtime: the SDK refuses ``CallToolResult`` inside a ``Union``
  outright (``InvalidSignature``) because it is the wire type rather than a payload. The annotation
  is what produces the output schema; the runtime value is what carries ``is_error``.
* **Errors are *returned*, never raised** (MC-14). Every exception path in this SDK yields
  ``structured_content: null`` and a message prefixed with the tool's own name, so a raise cannot
  carry the machine ``code`` a program branches on. A coded failure is a ``CallToolResult`` with
  ``is_error=True`` and the code inside ``structured_content``.

The mount is a bare ``Route``, not ``app.mount`` (MC-2) — see :func:`mount_mcp`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel, Field
from starlette.routing import Route

from pkb.contracts import (
    LIBRARIAN_AGENT_ID,
    MessageComplete,
    RunEnd,
    RunError,
    SubagentEnd,
    SubagentStart,
)
from pkb.packs import implementation_pack, research_pack
from pkb.server.errors import status_and_code
from pkb.server.sse import thread_for_event

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pkb.service import PkbService

__all__ = [
    "MCP_PATH",
    "TOOL_NAMES",
    "build_mcp_server",
    "mount_mcp",
]

MCP_PATH = "/mcp"
TOOL_NAMES = ("pkb_ask", "pkb_ingest", "pkb_research_pack", "pkb_implementation_pack")
"""Exactly four (MC-5). No ``pkb_approve`` — an external agent cannot satisfy a human gate — and no
write tool that bypasses the agent layer."""

_TRIM = "`*_~.,;:!?()[]{}<>\"'" + "\u201c\u201d\u2018\u2019"
"""Punctuation stripped from a token before it is matched against the catalog.

Everything a sentence can put next to an id — including the quotes and dashes a model reaches for
when it lists options — and nothing an id itself contains.
"""

DEFAULT_DEADLINE_SECONDS = 300.0
"""How long a tool waits before cancelling and returning ``timeout`` (MC-15).

An MCP call is bounded and cancellable: the adapter takes the ``RunHandle`` from ``start_run`` and,
on timeout or disconnect, calls ``cancel(run_id)`` — which covers a Librarian fan-out's whole family
of graphs under one id.
"""


# --------------------------------------------------------------------------------------
# Result types — one discriminated union carrying success, escalation and failure (MC-14, MC-22)
# --------------------------------------------------------------------------------------


class ExpertReply(BaseModel):
    """One expert's own contribution to a fan-out, assembled from the event stream (MC-10)."""

    agent_id: str
    title: str = ""
    thread_id: str
    status: str
    text: str = ""


class EscalationView(BaseModel):
    path: str
    review_note: str
    agent_id: str


class ProposalView(BaseModel):
    proposal_id: str
    tool: str
    path: str
    reason: str


class Answered(BaseModel):
    status: Literal["answered"] = "answered"
    answer: str
    thread_id: str
    agent_id: str
    run_id: str
    experts: list[ExpertReply] = Field(default_factory=list)
    proposals: list[ProposalView] = Field(default_factory=list)


class Menu(BaseModel):
    """Classification did not land. **An ordinary successful result** (MC-19).

    Not an error and not an interrupt: the caller may answer it as the next message on the same
    thread. The candidates are structured so a program can choose — and the adapter must never
    choose for it, because guessing a topic is what the whole layer refuses to do.
    """

    status: Literal["menu"] = "menu"
    answer: str
    thread_id: str
    agent_id: str
    run_id: str
    candidates: list[str] = Field(default_factory=list)


class Escalated(BaseModel):
    status: Literal["escalation"] = "escalation"
    answer: str = ""
    thread_id: str = ""
    agent_id: str = ""
    run_id: str = ""
    escalation: list[EscalationView] = Field(default_factory=list)


class Failed(BaseModel):
    status: Literal["error", "timeout"] = "error"
    code: str
    message: str
    retryable: bool = False
    thread_id: str = ""


class AskResult(BaseModel):
    """One envelope for every outcome, so MC-14 and MC-20 cannot drift apart."""

    outcome: Answered | Menu | Escalated | Failed = Field(discriminator="status")


class PackEntryView(BaseModel):
    path: str
    role: str
    bytes: int
    text: str


class PackOmissionView(BaseModel):
    path: str
    role: str
    reason: str
    bytes: int


class PackOk(BaseModel):
    status: Literal["ok"] = "ok"
    kind: str
    scope: list[str]
    entries: list[PackEntryView]
    omitted: list[PackOmissionView] = Field(default_factory=list)
    truncated: bool = False
    escalation: list[EscalationView] = Field(default_factory=list)


class PackResult(BaseModel):
    outcome: PackOk | Escalated | Failed = Field(discriminator="status")


# --------------------------------------------------------------------------------------
# The server
# --------------------------------------------------------------------------------------


def build_mcp_server(
    service_of: Callable[[], PkbService],
    snapshot_of: Callable[[], Any],
    *,
    deadline: float = DEFAULT_DEADLINE_SECONDS,
) -> MCPServer:
    """The four tools and the resources, over a service resolved at call time.

    ``service_of`` is a callable rather than a captured service for the same reason the router takes
    one: the app is a factory, and two apps in one process must not share a runtime (AP-1).
    """
    server = MCPServer(name="pkb", instructions=_INSTRUCTIONS)

    @server.tool(description=_ASK_DESCRIPTION)
    async def pkb_ask(
        question: Annotated[str, Field(description="The question, in full.")],
        agent_id: Annotated[
            str,
            Field(
                description=(
                    "A catalog id, verbatim — 'librarian' or 'topic/cooking/grilling'. "
                    "Read pkb://agents for the list; ids are never guessed or fuzzy-matched."
                )
            ),
        ] = LIBRARIAN_AGENT_ID,
        thread_id: Annotated[
            str | None,
            Field(
                description=(
                    "Continue an existing conversation. Omit to start one; the id comes back in "
                    "the result. Must not contain '::' or start with 'scan:'."
                )
            ),
        ] = None,
    ) -> AskResult:
        # The annotation is the SDK's *output schema*; a coded failure returns `CallToolResult`
        # at runtime, which the SDK requires and refuses to allow in the annotation. See §MC-14.
        return await _run_tool(  # type: ignore[no-any-return]
            service_of(), snapshot_of(), agent_id, question, thread_id, deadline=deadline
        )

    @server.tool(description=_INGEST_DESCRIPTION)
    async def pkb_ingest(
        content: Annotated[str, Field(description="The material to file, verbatim.")],
        source_type: Annotated[
            str | None, Field(description="Advisory context, e.g. 'retrospective'. Never a bypass.")
        ] = None,
        topic_hint: Annotated[
            str | None,
            Field(description="Advisory context. It does not select an expert or skip routing."),
        ] = None,
        thread_id: Annotated[str | None, Field(description="Continue an existing thread.")] = None,
    ) -> AskResult:
        """Always enters at the Librarian (MC-17).

        Fan-out applies to information exactly as to questions: several experts may file their own
        extraction and any may decline. The hints are appended as **labelled context**, never
        written into frontmatter and never used to pick a topic.
        """
        return await _run_tool(  # type: ignore[no-any-return]
            service_of(),
            snapshot_of(),
            LIBRARIAN_AGENT_ID,
            _with_hints(content, source_type, topic_hint),
            thread_id,
            deadline=deadline,
        )

    @server.tool(description=_RESEARCH_DESCRIPTION)
    async def pkb_research_pack(
        query: Annotated[str, Field(description="What the research is about.")],
        topics: Annotated[
            list[str] | None,
            Field(description="Catalog ids. Required in v1 — see the tool description."),
        ] = None,
        include_index: Annotated[bool, Field(description="Include each topic's index.md.")] = False,
        budget_bytes: Annotated[int, Field(description="0 means no budget.")] = 0,
    ) -> PackResult:
        del query  # v1 needs explicit topics; the classifier that would use it is Layer 2's (PK-8)
        if not topics:
            return _failure(  # type: ignore[return-value]
                "invalid_argument",
                "pkb_research_pack needs explicit `topics` in v1: read pkb://agents and name them. "
                "Topic selection by classification is a Layer 2 call and is not wired to this tool "
                "yet (PK-8, PK-9).",
                PackResult,
            )
        return _pack_result(  # type: ignore[no-any-return]
            lambda: research_pack(
                snapshot_of(),
                topics=topics,
                include_index=include_index,
                budget_bytes=budget_bytes or None,
            )
        )

    @server.tool(description=_IMPLEMENTATION_DESCRIPTION)
    async def pkb_implementation_pack(
        topic: Annotated[
            str,
            Field(
                description=(
                    "An **agent id** — 'topic/cooking/grilling'. Not a folder path, not a topic "
                    "tag, not a display name."
                )
            ),
        ],
        include_subtopics: Annotated[bool, Field(description="Include descendant topics.")] = False,
        budget_bytes: Annotated[int, Field(description="0 means no budget.")] = 0,
    ) -> PackResult:
        return _pack_result(  # type: ignore[no-any-return]
            lambda: implementation_pack(
                snapshot_of(),
                topic=topic,
                include_subtopics=include_subtopics,
                budget_bytes=budget_bytes or None,
            )
        )

    @server.resource(
        "pkb://agents",
        description="Every agent id, title and description — the ids the tools accept verbatim.",
        mime_type="application/json",
    )
    async def agents_resource() -> str:
        """Discovery as a **resource**, not a fifth tool (MC-6).

        RG-9 forbids fuzzy-matching an id, so an external agent that cannot enumerate them can only
        guess — and a guess that resolves to the wrong topic is indistinguishable from a right one
        at the caller.
        """
        service = service_of()
        return json.dumps(
            {
                "agents": [
                    {
                        "agent_id": d.agent_id,
                        "title": d.title,
                        "description": d.description,
                        "has_custom_expert": d.has_custom_expert,
                        "model_id": d.model_id,
                    }
                    for d in service.list_agents()
                ]
            }
        )

    @server.resource(
        "pkb://proposals",
        description="Writes this knowledge base refused and recorded, awaiting a human.",
        mime_type="application/json",
    )
    async def proposals_resource() -> str:
        """Closes README Part 4's feedback loop: without it a project cannot learn its write landed."""
        service = service_of()
        proposals = await service.list_proposals()
        return json.dumps({"proposals": [_proposal_json(p) for p in proposals]})

    @server.resource(
        "pkb://proposals/{proposal_id}",
        description="One recorded proposal, including the rendered diff a human would approve.",
        mime_type="application/json",
    )
    async def proposal_resource(proposal_id: str) -> str:
        """A **template**, which appears only in ``list_resource_templates()`` — never in
        ``list_resources()``. A client that calls one and not the other will not see it (MC-6)."""
        service = service_of()
        return json.dumps(_proposal_json(await service.get_proposal(proposal_id)))

    return server


def mount_mcp(app: Any, server: MCPServer, *, host: str = "127.0.0.1") -> Route:
    """Attach the MCP endpoint as a **bare ``Route``**, never ``app.mount`` (MC-2, MC-3).

    ``app.mount("/mcp", …)`` fails three separate ways, all measured:

    1. ``streamable_http_path`` defaults to ``/mcp`` *inside* the sub-app, so the endpoint lands at
       ``/mcp/mcp`` — the failure an implementer hits first, before ever seeing the redirect;
    2. the sub-app's router 307-redirects ``/mcp`` → ``/mcp/``, and arch §6's URL is ``/mcp``
       exactly, which a stricter client will not follow;
    3. ``Mount`` does not run the sub-app's lifespan, and that lifespan **is**
       ``session_manager.run()`` — so nothing serves and every request raises ``RuntimeError: Task
       group is not initialized``.

    ``streamable_http_app`` is called for its **side effect**: it constructs and stashes the session
    manager (reading ``server.session_manager`` before that raises), and the Starlette app it
    returns is discarded. The daemon's own lifespan then drives ``session_manager.run()`` (AP-3).

    DNS-rebinding lockdown stays **on** — ``host`` defaults to localhost and the daemon binds
    localhost anyway, so the protection is free. A test client must therefore use a ``base_url``
    carrying a **port** (``http://127.0.0.1:8000``): a portless Host header is rejected with 421,
    exactly like ``testserver``.
    """
    server.streamable_http_app(streamable_http_path=MCP_PATH, host=host)
    route = Route(
        MCP_PATH,
        StreamableHTTPASGIApp(server.session_manager),
        methods=["GET", "POST", "DELETE"],
    )
    app.router.routes.append(route)
    return route


# --------------------------------------------------------------------------------------
# Running one tool
# --------------------------------------------------------------------------------------


async def _run_tool(
    service: PkbService,
    snapshot: Any,
    agent_id: str,
    message: str,
    thread_id: str | None,
    *,
    deadline: float,
) -> Any:
    """One bounded, cancellable, propose-only run, reported as one discriminated result."""
    if thread_id is not None and not _is_addressable(thread_id):
        return _failure(
            "invalid_argument",
            f"{thread_id!r} names a derived or maintenance thread. Those are functions of "
            f"something the daemon already has; an external caller may not write into a "
            f"conversation, or a maintenance run, it does not own.",
            AskResult,
        )

    try:
        if thread_id is None:
            thread = await service.create_thread(agent_id, origin_channel="mcp")
            thread_id = thread.thread_id
        subscription = await service.start_run(thread_id, message, approval_mode="propose_only")
    except Exception as exc:
        return _failure_from(exc, AskResult, thread_id or "")

    handle = subscription.handle
    try:
        collected = await asyncio.wait_for(_collect(subscription, handle, service), deadline)
    except TimeoutError:
        await service.cancel(handle.run_id)
        return _coded(
            AskResult,
            Failed(
                status="timeout",
                code="timeout",
                message=f"the run exceeded {deadline:.0f}s and was cancelled",
                retryable=True,
                thread_id=handle.thread_id,
            ),
        )
    finally:
        close = subscription.close
        if callable(close):
            close()

    escalations = _escalations_for(snapshot, collected.agents or [agent_id])
    if escalations:
        return _result(
            AskResult,
            Escalated(
                answer=collected.final_text,
                thread_id=handle.thread_id,
                agent_id=handle.agent_id,
                run_id=handle.run_id,
                escalation=escalations,
            ),
        )
    if collected.error is not None:
        # MC-14: the same payload must not have two wire shapes depending on which branch produced
        # it. A run that failed is a coded failure — `is_error` true, the code in
        # `structured_content` — exactly like a refusal caught before the run started.
        return _coded(AskResult, collected.error)

    proposals = [
        _proposal_view(p)
        for p in await service.list_proposals()
        if p.thread_id == handle.thread_id or p.thread_id.startswith(f"{handle.thread_id}::")
    ]
    candidates = _menu_candidates(collected.final_text, service, bool(collected.experts), agent_id)
    if candidates:
        return _result(
            AskResult,
            Menu(
                answer=collected.final_text,
                thread_id=handle.thread_id,
                agent_id=handle.agent_id,
                run_id=handle.run_id,
                candidates=candidates,
            ),
        )
    return _result(
        AskResult,
        Answered(
            answer=collected.final_text,
            thread_id=handle.thread_id,
            agent_id=handle.agent_id,
            run_id=handle.run_id,
            experts=collected.experts,
            proposals=proposals,
        ),
    )


class _Collected:
    """What one run produced, assembled **from the event stream** (MC-9, MC-10)."""

    def __init__(self) -> None:
        self.final_text = ""
        self.experts: list[ExpertReply] = []
        self.agents: list[str] = []
        self.error: Failed | None = None


async def _collect(subscription: Any, handle: Any, service: PkbService) -> _Collected:
    """Drop deltas and tool frames **at the adapter**, and never parse the merged reply.

    ``experts`` comes from ``SubagentStart``/``SubagentEnd`` for the roster and status, each
    expert's own ``MessageComplete`` for its text, and SS-10's derivation for its thread id. Parsing
    ``RunEnd.final_text`` instead would make the merge's rendering format a wire protocol, and the
    golden test that pins that rendering would then be pinning one (MC-10).

    ``final_text`` is returned **verbatim** (MC-9). A transport that summarizes the merged reply is
    the same lie LB-18 exists to prevent, one layer up.
    """
    collected = _Collected()
    catalog = [d.agent_id for d in service.list_agents()]
    titles = {d.agent_id: d.title for d in service.list_agents()}
    texts: dict[str, str] = {}
    statuses: dict[str, str] = {}

    async for event in subscription.events:
        if isinstance(event, SubagentStart) and event.agent_id in catalog:
            if event.agent_id not in collected.agents:
                collected.agents.append(event.agent_id)
        elif isinstance(event, SubagentEnd) and event.agent_id in catalog:
            statuses[event.agent_id] = event.status
        elif isinstance(event, MessageComplete):
            if event.agent_id in catalog and event.agent_id != handle.agent_id:
                texts[event.agent_id] = event.text
        elif isinstance(event, RunEnd):
            collected.final_text = event.final_text
        elif isinstance(event, RunError):
            collected.error = Failed(
                code="run_failed",
                message=event.message,
                retryable=event.retryable,
                thread_id=handle.thread_id,
            )

    for agent_id in collected.agents:
        collected.experts.append(
            ExpertReply(
                agent_id=agent_id,
                title=titles.get(agent_id, ""),
                thread_id=thread_for_event(
                    SubagentStart(run_id=handle.run_id, agent_id=agent_id), handle, catalog
                ),
                status=statuses.get(agent_id, "answered"),
                text=texts.get(agent_id, ""),
            )
        )
    if not collected.agents:
        collected.agents = [handle.agent_id]
    return collected


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _is_addressable(thread_id: str) -> bool:
    """MC-12: a caller may not supply a derived or maintenance thread id."""
    from pkb.contracts import EXPERT_THREAD_SEPARATOR, is_scan_thread

    return EXPERT_THREAD_SEPARATOR not in thread_id and not is_scan_thread(thread_id)


def _with_hints(content: str, source_type: str | None, topic_hint: str | None) -> str:
    """Hints as **labelled context appended to the item**, never a bypass (MC-17)."""
    lines = [content]
    if source_type or topic_hint:
        lines.append("")
        lines.append("Context supplied by the caller (advisory — it does not choose a topic):")
        if source_type:
            lines.append(f"- source type: {source_type}")
        if topic_hint:
            lines.append(f"- topic hint: {topic_hint}")
    return "\n".join(lines)


def _menu_candidates(text: str, service: PkbService, ran_experts: bool, agent_id: str) -> list[str]:
    """Catalog ids the reply offers, when classification did not land (MC-19).

    Three conditions, and each removes a false positive the others do not. **Only the Librarian
    classifies** — tested against the agent the *caller asked for*, which the tool knows without
    consulting the run — a direct ask to an expert never fans out, so without this every expert answer
    that cross-references a sibling topic came back as a menu, telling a program to choose a topic
    when it already had its answer. **No expert ran**, because a reply that fanned out is an answer
    whatever it mentions. And the text names catalog ids as **whole tokens**, tested against the
    catalog: the ids are known and are the thing being looked for, so nothing here depends on how
    the menu is worded.

    That is deliberately not a parse of the merged reply. Parsing it would make ``merge_reply``'s
    rendering format a wire protocol and turn LB-18's golden test into a contract (MC-10, SS-14) —
    and a containment test against ids the daemon already holds needs none of it.

    The adapter surfaces the candidates and **never chooses**: guessing a topic is the thing this
    system refuses to do at every layer.
    """
    if agent_id != LIBRARIAN_AGENT_ID or ran_experts or not text:
        return []
    # Whole-token, not substring: `topic/cooking` is a substring of `topic/cooking/grilling`, so a
    # menu offering only the sub-topic came back offering its ancestor too — an option the Librarian
    # never wrote, which a caller then picks, filing the material one level up from where it belongs.
    #
    # Deliberately a split-and-strip rather than a regex over the reply. The rule this sits next to
    # (MC-10) forbids *parsing* the merged reply, and the line between "look for known ids" and
    # "read the rendering" is easier to hold when there is no pattern here to grow one.
    offered = {token.strip(_TRIM) for token in text.split()}
    return [d.agent_id for d in service.list_agents() if d.agent_id in offered]


def _escalations_for(snapshot: Any, agents: Sequence[str]) -> list[EscalationView]:
    """Conflict-flagged files inside the participating topics (MC-20, RT-59)."""
    from pkb.packs import escalations

    known = {t.agent_id for t in snapshot.topics.values()}
    scope = [agent for agent in agents if agent in known]
    return [
        EscalationView(path=e.path, review_note=e.review_note, agent_id=e.agent_id)
        for e in escalations(snapshot, scope)
    ]


def _pack_result(build: Callable[[], Any]) -> Any:
    try:
        pack = build()
    except Exception as exc:
        return _failure_from(exc, PackResult, "")
    if pack.escalation:
        return _result(
            PackResult,
            Escalated(
                escalation=[
                    EscalationView(path=e.path, review_note=e.review_note, agent_id=e.agent_id)
                    for e in pack.escalation
                ]
            ),
        )
    return PackResult(
        outcome=PackOk(
            kind=pack.kind,
            scope=list(pack.scope),
            entries=[
                PackEntryView(path=e.path, role=e.role, bytes=e.bytes, text=e.text)
                for e in pack.entries
            ],
            omitted=[
                PackOmissionView(path=o.path, role=o.role, reason=o.reason, bytes=o.bytes)
                for o in pack.omitted
            ],
            truncated=pack.truncated,
        )
    )


def _result(envelope: type[Any], outcome: BaseModel) -> Any:
    """A **successful** result carrying a discriminator — the shape MC-19 and MC-20 both need."""
    return envelope(outcome=outcome)


def _coded(envelope: type[Any], outcome: Failed) -> CallToolResult:
    """A :class:`Failed` outcome on the wire — always with ``is_error`` set (MC-14, MC-15).

    One helper for every coded failure, whatever produced it: a refusal caught before the run
    started, a run that errored, a deadline. Two shapes for one payload is worse than either shape,
    because the only caller who could act on ``retryable`` is exactly the one branching on
    ``is_error``.
    """
    return CallToolResult(
        content=[TextContent(type="text", text=outcome.message)],
        structured_content=envelope(outcome=outcome).model_dump(mode="json"),
        is_error=True,
    )


def _failure(code: str, message: str, envelope: type[Any]) -> CallToolResult:
    """A coded failure, **returned** rather than raised (MC-14).

    Every exception path in this SDK produces ``structured_content: null`` and prefixes the message
    with the tool's name, so a raise cannot carry a code a program can branch on. Returning a
    ``CallToolResult`` keeps ``is_error`` true *and* the code machine-readable.
    """
    return _coded(envelope, Failed(code=code, message=message))


def _failure_from(exc: BaseException, envelope: type[Any], thread_id: str) -> CallToolResult:
    """Map a typed error through the **same table** the HTTP handler uses (MC-14, RO-20)."""
    _, code = status_and_code(exc)
    message = str(exc) if code != "internal" else "an unexpected error occurred"
    return _coded(envelope, Failed(code=code, message=message, thread_id=thread_id))


def _proposal_view(proposal: Any) -> ProposalView:
    return ProposalView(
        proposal_id=proposal.proposal_id,
        tool=proposal.action.tool,
        path=str(proposal.action.args.get("file_path", "")),
        reason=proposal.action.reason,
    )


def _proposal_json(proposal: Any) -> dict[str, Any]:
    return {
        "proposal_id": proposal.proposal_id,
        "agent_id": proposal.agent_id,
        "thread_id": proposal.thread_id,
        "created_at": proposal.created_at.isoformat().replace("+00:00", "Z"),
        "tool": proposal.action.tool,
        "args": dict(proposal.action.args),
        "description": proposal.action.description,
        "allowed_decisions": list(proposal.action.allowed_decisions),
        "reason": proposal.action.reason,
    }


_INSTRUCTIONS = """\
A Personal Knowledge Base. Ask it what the human knows, or hand it something to file.

Writes follow the same standards as every other channel. A plain note and a first extraction of a
source land unattended; changing human-approved content, adding a tag, resolving a conflict and
rewriting an extraction the human has read all become proposals for the human instead — the result
tells you which. You cannot approve one from here.

A result with status "escalation" means material in scope is under human review. Stop; do not retry.
"""

_ASK_DESCRIPTION = """\
Ask the knowledge base a question. Defaults to the Librarian, which routes to whichever topic
experts are relevant and returns their answers attributed. Name an agent_id to ask one expert
directly. Read pkb://agents for the ids.
"""

_INGEST_DESCRIPTION = """\
Hand the knowledge base material to file — an observation, a retrospective note, an article. It
always enters at the Librarian, which decides which topics it belongs to; several may file their own
extraction of the same material and any may decline. The result distinguishes what was filed from
what became a proposal awaiting the human.
"""

_RESEARCH_DESCRIPTION = """\
Breadth-first context: the tag vocabulary in scope, then each topic's overview and its summaries,
then anything under human review. Excludes index.md unless you ask for it. Name the topics.
"""

_IMPLEMENTATION_DESCRIPTION = """\
Depth-first context for one topic: the human's own rules first, then the topic index, the ingested
references, and reusable solution notes. Read it top-down — the order is the priority.
"""
