"""The Topic Expert graph factory — EX-1 … EX-16.

One compiled graph per topic root, with everything that makes it a *PKB* agent attached in code:
the layered system prompt, the topic's skill chain, the three knowledge-base middleware, the
topic-scoped permission list, the approval gates, and the shared runtime singletons. Nothing here is
optional and nothing here is reachable from a file inside the knowledge base — that separation is
EX-5, and it is the reason a human may rewrite ``expert.md`` freely without being able to weaken the
system.

This module also holds the pieces :mod:`pkb.agents.librarian` shares with it — prompt loading and
rendering, the skill-source list, and the general-purpose subagent spec — because two copies of any
of them would drift, and the one that drifts silently is the one that matters (EX-11).

Three things look redundant and are not. Each was executed against the pinned harness.

**The explicit ``general-purpose`` subagent (EX-11, D-2).** deepagents auto-adds a
``general-purpose`` subagent to *every* deep agent, including one that declares no subagents at all
(``graph.py:745-812``). That subagent inherits only middleware whose ``.name`` collides with one of
its own default slots — ``_gp_inheritable = [m for m in middleware if m.name in
_gp_original_name_to_index]`` — and ours never collide. So a model that delegates a write through
``task(subagent_type="general-purpose")`` bypasses :class:`~pkb.agents.middleware.validation.
KbValidationMiddleware` *and* the maintenance flush entirely: verified, the guard saw only
``['task']`` and the file landed on disk. It *does* inherit ``permissions``, so invariant I3 still
holds, which is exactly why the hole is quiet. Supplying an explicit spec with that name suppresses
the auto-add and routes our middleware through ``_apply_custom_middleware``.
:func:`general_purpose_subagent` builds it, and every graph in this package carries one.

No graph in this package passes ``interrupt_on`` to ``create_deep_agent`` at all (Task 6,
`docs/superpowers/plans/2026-08-14-phase2-sessions.md`; DESIGN.md §2.10: "the operator's instruction
is the approval"). A gate table still exists in :mod:`pkb.agents.gates` and :mod:`pkb.agents.approval`
— composing it back in is Phase 3's call, not this module's — but nothing here wires it in, so a tool
call, delegated or direct, simply executes.

**The middleware order (EX-14).** ``[breadth, validation, maintenance]``. ``wrap_tool_call`` composes
first-in-list-outermost, so validation wraps the tool call from outside; ``after_agent`` hooks run in
*reverse* registration order, so the last-registered middleware's ``after_agent`` is the graph's
``exit_node``. Maintenance must be last, because ``KbValidationMiddleware``'s escalation returns
``{"jump_to": "end"}`` and ``end`` resolves to ``exit_node`` — put another ``after_agent`` middleware
after maintenance and the escalation would jump past the flush (MW-15, MW-16).

**Nothing is passed to ``create_deep_agent``'s memory parameter (EX-6, D-11).** deepagents'
``MemoryMiddleware`` injects a prompt telling the model to persist knowledge by calling ``edit_file``
on the memory files, and those files would be exactly the two the human approves by hand.
:class:`~pkb.agents.middleware.breadth.KbBreadthMiddleware` supplies the same context with none of
that. The parameter's *name* is deliberately not spelled with its ``=`` anywhere in this package, so
that EX-6's grep stays a one-line answer.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any, Final, Protocol

from deepagents import SubAgent, create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Checkpointer

from pkb.agents.middleware.breadth import KbBreadthMiddleware
from pkb.agents.middleware.maintenance import (
    FlushSink,
    KbMaintenanceMiddleware,
    KbWriteLock,
    SupportsInvalidate,
)
from pkb.agents.middleware.validation import KbValidationMiddleware
from pkb.agents.paths import SKILLS_MOUNT, to_backend_path
from pkb.agents.permissions import kb_permissions
from pkb.contracts import ScanQueue, UnknownAgentError
from pkb.core import resolve_expert
from pkb.core.frontmatter import parse
from pkb.core.models import KbSnapshot, TopicRecord
from pkb.core.paths import SKILLS_DIR

__all__ = [
    "EXPERT_TEMPLATE_PROMPT",
    "LIBRARIAN_PROMPT",
    "PROMPT_SEPARATOR",
    "STANDARDS_PROMPT",
    "GraphRuntime",
    "base_skill_sources",
    "build_expert",
    "expert_prompt",
    "general_purpose_subagent",
    "kb_middleware",
    "load_prompt",
    "render_prompt",
    "topic_skill_sources",
]


# --------------------------------------------------------------------------------------
# What a graph factory needs from the runtime (RT-1)
# --------------------------------------------------------------------------------------


class GraphRuntime(Protocol):
    """The shared singletons every compiled graph is handed (RT-1, RT-6, RT-51, RT-54).

    A structural :class:`~typing.Protocol` rather than an import of ``pkb.agents.runtime``: the
    runtime constructs graphs through these factories, so a nominal type would make the dependency
    circular. ``PkbRuntime`` satisfies it by having the attributes; mypy checks the match at the
    registry's call site, which is the only place both modules meet.

    RT-1 is a *sharing* rule, not a plumbing detail. One ``AsyncSqliteSaver``, one store, one
    backend, one write lock, for the Librarian, every expert at every depth, and every scan run — a
    graph that built its own checkpointer would give a delegated expert a different durable history
    from the same expert reached directly, and a second write lock would provide no mutual exclusion
    at all (RT-51).
    """

    backend: BackendProtocol
    """The one ``CompositeBackend``: ``StateBackend`` for scratch, ``/kb/`` and ``/skills/`` routed
    to disk (RT-6). Every graph shares it, which is what makes ``/kb/`` one tree for all agents."""

    checkpointer: Checkpointer | None
    """The one saver. ``None`` only in a test that does not resume."""

    store: BaseStore | None
    """The one store (RT-5). Nothing in v1 reads it; it is passed so a restart is not lossy later."""

    write_lock: KbWriteLock
    """The process-wide knowledge-base write lock (RT-51 … RT-53). Held only around the flush."""

    scan_queue: ScanQueue | None
    """Where ``FlushReport.scan_requests`` are persisted (RT-54)."""

    flush_sink: FlushSink | None
    """Where the whole ``FlushReport`` goes — the daemon's log or event stream (MW-24)."""

    clock: Callable[[], date]
    """Injected ``today`` for the ``updated`` stamp (MW-20). Never the wall clock inside a hook."""

    def snapshot(self) -> KbSnapshot:
        """The current tree, cached by the runtime and invalidated when it changes.

        Called once per gated tool call in ``after_model`` (RT-21), so a bare
        ``lambda: scan(kb_root)`` would walk the tree several times a turn.
        """
        ...


# --------------------------------------------------------------------------------------
# Prompts (PR-1, EX-3, EX-4)
# --------------------------------------------------------------------------------------

STANDARDS_PROMPT: Final = "standards.md"
EXPERT_TEMPLATE_PROMPT: Final = "expert_template.md"
LIBRARIAN_PROMPT: Final = "librarian.md"

PROMPT_SEPARATOR: Final = "\n\n---\n\n"
"""How the two layers of an expert prompt are joined: standards on top, domain layer beneath.

A horizontal rule rather than a heading, because the domain layer's own headings must keep their
level — an ``expert.md`` written by a human starts at ``#`` and would otherwise sit oddly under a
section title it never asked for.
"""

_TOKEN = re.compile(r"\{\{([A-Z_]+)\}\}")
"""``{{NAME}}`` — the shipped prompts' placeholder syntax.

Deliberately not :meth:`str.format` and not :class:`string.Template`: the prompt bodies are markdown
and a future edit may legitimately contain braces or a ``$``. A literal, explicitly-delimited token
substituted by regex cannot be broken by the prose around it.
"""


def load_prompt(name: str) -> str:
    """Read one shipped prompt file (PR-1).

    Through :mod:`importlib.resources` so it works from an editable checkout and an installed wheel
    alike — the three files are package data under ``pkb/agents/prompts/``, never stored in the
    knowledge base. There is exactly one expert template for the whole PKB.
    """
    return (files("pkb.agents") / "prompts" / name).read_text(encoding="utf-8")


def render_prompt(text: str, values: Mapping[str, str]) -> str:
    """Substitute the ``{{NAME}}`` placeholders in a shipped prompt (PR-1).

    Raises:
        KeyError: If the text carries a placeholder *values* has no entry for. Loud on purpose: an
            unrendered prompt reaches the model as a literal ``{{KB_ROOT}}``, which is a routing
            instruction pointing nowhere, and a silent pass-through would make LB-4's assertion
            (the prompt names the root catalog's path) pass while the agent could not use it.
    """

    def substitute(match: re.Match[str]) -> str:
        token = match.group(1)
        try:
            return values[token]
        except KeyError:
            msg = f"prompt placeholder {{{{{token}}}}} has no value"
            raise KeyError(msg) from None

    return _TOKEN.sub(substitute, text)


def expert_prompt(kb_root: Path, topic: TopicRecord) -> str:
    """The Topic Expert's system prompt: a fixed preamble above an overridable domain layer (EX-4).

    ``prompts/standards.md`` is prepended **unconditionally** and is not reachable from anything
    inside the knowledge base. Beneath it sits either the body of the ``expert.md``
    :func:`pkb.core.resolve_expert` selects — the topic's own, or the nearest ancestor's (EX-2, PA-13)
    — or the one shipped default template.

    Full replacement (README §2.3 read literally, C2) was rejected because the *mechanical* standards
    survive it — permissions, the middleware and the gates are attached in code — while the
    *prompt-level* ones would not: approval etiquette, proposing a tag before using it, escalating a
    conflict, writing in the human's voice. Those have no other enforcement point, so a well-meaning
    ``expert.md`` that simply forgot to mention them would silently switch them off.

    Only the **body** of ``expert.md`` is used (EX-3). The file sits outside the PKB frontmatter
    regime — Layer 1 validates its placement and nothing else (VA-20) — so a human's YAML block, if
    they wrote one, must not leak into the system prompt. It is also *not* token-substituted: it is
    the human's own text and may contain braces for any reason.
    """
    values = {"TOPIC_TITLE": topic.title, "TOPIC_ROOT": to_backend_path(topic.path)}
    standards = render_prompt(load_prompt(STANDARDS_PROMPT), values)
    return standards + PROMPT_SEPARATOR + _domain_layer(kb_root, topic, values)


def _domain_layer(kb_root: Path, topic: TopicRecord, values: Mapping[str, str]) -> str:
    """The half of the prompt a human may replace: their ``expert.md``, or the shipped template.

    An ``expert.md`` that cannot be read falls back to the template rather than failing the build.
    :func:`~pkb.core.resolve_expert` already guarantees a case-exact regular file, so this is the
    exotic case — a permission bit, a truncated sync, bytes that are not UTF-8 — and in every one of
    them the human's route to fixing it is a conversation with the very agent that would otherwise
    refuse to compile.
    """
    source = resolve_expert(kb_root, kb_root.joinpath(*topic.path.split("/")))
    if source is not None:
        try:
            return parse(source.read_text(encoding="utf-8")).body
        except (OSError, UnicodeDecodeError):
            pass
    return render_prompt(load_prompt(EXPERT_TEMPLATE_PROMPT), values)


# --------------------------------------------------------------------------------------
# Skill sources (EX-8, LB-5, SK-3)
# --------------------------------------------------------------------------------------


def base_skill_sources(kb_root: Path) -> list[str]:
    """The two sources every agent carries: the packaged mount, then the KB's own (EX-8, LB-5).

    Order is precedence: deepagents merges skills last-wins by name, so a ``skills/voice/`` in the
    knowledge base shadows the shipped ``voice`` whole-record (D7/SK-5). The packaged mount is always
    present; the KB's ``skills/`` is included only when it exists, because a nonexistent source makes
    ``_list_skills_with_errors`` return a source error that becomes prompt noise on every turn — and
    a KB with no adopted skills is the normal case, since nothing seeds them (SK-3).
    """
    sources = [SKILLS_MOUNT]
    if (kb_root / SKILLS_DIR).is_dir():
        sources.append(_skills_source(SKILLS_DIR))
    return sources


def topic_skill_sources(kb_root: Path, snapshot: KbSnapshot, topic: TopicRecord) -> list[str]:
    """A Topic Expert's full skill chain, outermost source first (EX-8, SK-16).

    ``[packaged, KB root] + [each ancestor topic, outermost first] + [the topic itself]``, filtered
    to directories that exist. Because deepagents resolves last-wins by name, that ordering yields
    precedence *own topic > ancestor topic > KB root > packaged default* — the same precedence
    :func:`pkb.core.resolve_skills` computes. Layer 1's function is the assertion oracle in the
    tests, never a second implementation here: this builds *source directories* for the harness to
    scan, Layer 1 resolves *files*, and the property that the two agree is what SK-16 pins.

    The ancestor chain is read off :attr:`~pkb.core.models.TopicRecord.parent` in the snapshot, so
    this module walks no tree (RG-2).
    """
    sources = base_skill_sources(kb_root)
    for ancestor in _topic_chain(snapshot, topic):
        candidate = kb_root.joinpath(*ancestor.path.split("/"), SKILLS_DIR)
        if candidate.is_dir():
            sources.append(_skills_source(f"{ancestor.path}/{SKILLS_DIR}"))
    return sources


def _skills_source(relative: str) -> str:
    """A skill source directory as the backend sees it, with the trailing slash deepagents uses."""
    return to_backend_path(relative) + "/"


def _topic_chain(snapshot: KbSnapshot, topic: TopicRecord) -> list[TopicRecord]:
    """*topic* and its ancestor topic roots, outermost first.

    Guarded against a cycle in ``parent`` — the snapshot never produces one, but a factory that hangs
    while compiling a graph is a much worse failure than one that stops early.
    """
    chain: list[TopicRecord] = []
    seen: set[str] = set()
    current: TopicRecord | None = topic
    while current is not None and current.path not in seen:
        seen.add(current.path)
        chain.append(current)
        current = snapshot.topics.get(current.parent) if current.parent else None
    chain.reverse()
    return chain


# --------------------------------------------------------------------------------------
# The pieces every KB graph carries (EX-5, EX-10, EX-11, EX-14)
# --------------------------------------------------------------------------------------


def kb_middleware(
    kb_root: Path,
    runtime: GraphRuntime,
    breadth: KbBreadthMiddleware,
    *,
    registry: SupportsInvalidate | None = None,
) -> tuple[list[AgentMiddleware[Any, Any, Any]], list[AgentMiddleware[Any, Any, Any]]]:
    """The main stack and the general-purpose subagent's stack, in that order (EX-14, EX-11).

    Returns ``(main, delegated)``. ``main`` is ``[breadth, validation, maintenance]`` — the order is
    load-bearing, see the module docstring. ``delegated`` is ``[validation, maintenance]``: the
    general-purpose subagent assembles its own filesystem and summarization middleware and supplies
    its own context, so it needs neither this topic's breadth block nor a second copy of anything
    deepagents already gives it — but it absolutely needs the two that make a write valid and the
    tree consistent afterwards.

    The *same instances* appear in both lists. Middleware hold read-only configuration (MW-4), one
    instance already serves every run of a compiled graph, and sharing them makes it structurally
    impossible for the delegated path to validate against a different knowledge base than the
    parent — which is precisely the class of bug D-2 was.
    """
    validation = KbValidationMiddleware(kb_root)
    maintenance = KbMaintenanceMiddleware(
        kb_root,
        queue=runtime.scan_queue,
        sink=runtime.flush_sink,
        clock=runtime.clock,
        lock=runtime.write_lock,
        registry=registry,
    )
    return [breadth, validation, maintenance], [validation, maintenance]


def general_purpose_subagent(
    middleware: Sequence[AgentMiddleware[Any, Any, Any]], skills: Sequence[str]
) -> SubAgent:
    """The explicit ``general-purpose`` spec that closes D-2's hole (EX-11).

    deepagents auto-adds this subagent to every deep agent unless the caller declares one with the
    same name (``graph.py:745``), and the auto-added one silently drops our middleware. Declaring it
    suppresses the auto-add; ``_apply_custom_middleware`` then splices *middleware* into the stack it
    builds, and a delegated ``write_file`` is validated and its path flushed like any other.

    Everything else is taken verbatim from deepagents' own ``GENERAL_PURPOSE_SUBAGENT``: the name
    (which is what suppresses the auto-add), its description, and its system prompt. ``permissions``,
    ``interrupt_on`` and ``tools`` are deliberately **not** set — an unset key inherits the parent's
    (``graph.py:664``, ``:718``, ``:725``), so I3 reaches this path exactly as it reached the
    auto-added version, and a divergence cannot be introduced by forgetting to repeat one here. Since
    no graph in this package passes ``interrupt_on`` to its parent either (Task 6), the inherited
    value is simply absent both places — the same mechanism that would propagate a gate now
    propagates its absence. ``skills`` *is* set, because an unset one gives the subagent no skills at
    all while the auto-add would have passed the parent's (``graph.py:757``).
    """
    return {**GENERAL_PURPOSE_SUBAGENT, "middleware": list(middleware), "skills": list(skills)}


# --------------------------------------------------------------------------------------
# The factory (EX-1)
# --------------------------------------------------------------------------------------


def build_expert(
    kb_root: Path,
    topic_path: str,
    runtime: GraphRuntime,
    *,
    model: str | BaseChatModel,
    registry: SupportsInvalidate | None = None,
    tools: Sequence[BaseTool] = (),
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile one topic's Topic Expert (EX-1 … EX-16).

    Args:
        kb_root: The knowledge base on disk.
        topic_path: A :attr:`~pkb.core.models.TopicRecord.path` — ``Cooking`` or
            ``Cooking/sub-topics/Grilling``. **Every topic root gets its own graph** at every depth
            (EX-2/C1): its own id, its own breadth files, its own skill chain, its own threads. What
            a missing ``expert.md`` changes is only *whose persona* runs it.
        runtime: The shared singletons (RT-1).
        model: Always explicit, never ``None`` (RG-21, EX-9). ``model=None`` is deprecated in
            deepagents and falls back to a different default than the configured one, which would
            shadow the registry's choice without an error.
        registry: Invalidated by the maintenance middleware when the turn rewrote an ``expert.md``,
            a skill or a ``topic.md`` (MW-30, RG-17).
        tools: Additional tools, appended to the built-ins. This is where the registry passes the
            scope-limited ``create_subtopic`` (EX-12) and any retrieval tool (EX-13); both are
            additive and neither carries filesystem write capability of its own.

    Returns:
        A compiled graph. Two topics yield two distinct graphs with different configuration — there
        is no shared graph parameterized per call, because the prompt, the breadth files, the skill
        chain and the permission list all differ per topic and a delegated subagent inherits none of
        them from its parent (EX-10).

    Raises:
        UnknownAgentError: If *topic_path* names no topic root in the current snapshot.
    """
    snapshot = runtime.snapshot()
    topic = snapshot.topics.get(topic_path)
    if topic is None:
        msg = f"no topic root at {topic_path!r}"
        raise UnknownAgentError(msg)

    skills = topic_skill_sources(kb_root, snapshot, topic)
    breadth = KbBreadthMiddleware.for_topic(kb_root, topic.path)
    main, delegated = kb_middleware(kb_root, runtime, breadth, registry=registry)

    return create_deep_agent(
        model=model,
        system_prompt=expert_prompt(kb_root, topic),
        tools=list(tools),
        middleware=main,
        subagents=[general_purpose_subagent(delegated, skills)],
        skills=skills,
        permissions=kb_permissions(topic.path),
        backend=runtime.backend,
        checkpointer=runtime.checkpointer,
        store=runtime.store,
        name=topic.agent_id,
    )
