"""The Librarian graph factory — LB-1 … LB-13.

The Librarian is the front door: a compiled deep agent in its own right, with its own id and its own
threads, not a dispatcher function in front of the experts (LB-1). Every channel connects to it by
default. What it *does* with an inbound item changed on 2026-08-07, by the human's ruling, and the
change is the reason to read this file rather than skim it.

**Routing is no longer a decision the model makes.** The Librarian used to hold deepagents' ``task``
tool and register every expert as a :class:`~deepagents.CompiledSubAgent`, leaving it free to
delegate or not. Measured against a real model it did not delegate: asked what the Cooking notes said
about pulling a ribeye it ran ``ls``/``read_file``/``grep`` and answered out of the raw files — no
topic skills, no ``expert.md`` persona, no per-topic voice — and on another run it claimed *"The
Cooking expert checked the knowledge base"* when no expert had run. So the turn is now four steps and
only the first is the model's: classify, fan out, merge, offer. Steps 2 to 4 are ordinary Python in
:mod:`pkb.agents.routing` and :class:`~pkb.agents.runtime.PkbRuntime`; this factory builds the graph
that performs step 1 and nothing else.

Three consequences are visible right here:

**No expert subagents, and no ``task``** (LB-12). ``subagents=`` carries the ``general-purpose`` spec
alone. The experts are reached by the runtime, by code, on their own threads — not through a tool the
model may decline to call. ``task`` itself cannot be removed from a deep agent without a
process-global harness profile keyed by model id (rejected for the same reason Q7-b was), so
:class:`~pkb.agents.routing.RouteMiddleware` withholds it from every model request instead. With
routing in code, a Librarian that can still call ``task`` has a bypass, and the bypass is the bug.

**One extra middleware, first in the list** (LB-12, LB-13).
:class:`~pkb.agents.routing.RouteMiddleware` ends the classification run at the ``route`` call and
forces exactly one retry when the model answers in prose. It sits ahead of the breadth middleware so
its ``wrap_model_call`` is outermost; maintenance stays last, because ``after_agent`` runs in reverse
registration order and the flush must be the graph's exit node (EX-14, MW-15).

**It still holds no knowledge and no write capability.** ``kb_permissions(None)`` denies every write
under ``/kb/**`` (RT-16), and its only skill sources are the packaged mount and the knowledge base's
own ``skills/`` — nothing topic-scoped (LB-5). Filing a note needs the topic's skills, its voice
overload and its ``expert.md`` behaviour, none of which the Librarian loads. Its one mutation is the
``create_topic`` tool, which the registry passes in and which writes through
:func:`pkb.core.scaffold_topic` under the write lock (LB-7, RT-18) — and which stays here, because a
topic gap is still the Librarian's to notice. It is no longer *gated*: Task 6 stops composing
``interrupt_on`` for every graph in this package, this one included, so the model's call lands in the
turn like any other write (DESIGN.md §2.10, "the operator's instruction is the approval").

**Its prompt is knowledge-base-independent (LB-3).** No topic names, no descriptions, no per-topic
instructions — the routing view is the *generated* root ``index.md``, loaded fresh each turn by
:class:`~pkb.agents.middleware.breadth.KbBreadthMiddleware` (LB-4). With the experts no longer
registered as subagents, that catalog is now the *only* place topic descriptions reach the model,
which makes LB-4's "nothing about routing is maintained by hand" strictly true.

**An empty knowledge base must compile (LB-6).** Bootstrapping starts with zero topics and every
inbound item is a topic gap. The Librarian still gets its general-purpose subagent and its flush, and
``route`` with an empty catalog is answered by the topic-creation flow rather than by a menu of
nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from pkb.agents.expert import (
    LIBRARIAN_PROMPT,
    GraphRuntime,
    base_skill_sources,
    general_purpose_subagent,
    kb_middleware,
    load_prompt,
    render_prompt,
)
from pkb.agents.middleware.breadth import KbBreadthMiddleware
from pkb.agents.middleware.maintenance import SupportsInvalidate
from pkb.agents.paths import KB_MOUNT
from pkb.agents.permissions import kb_permissions
from pkb.agents.routing import RouteMiddleware, route_tool
from pkb.core.paths import LIBRARIAN_AGENT_ID

__all__ = ["build_librarian", "librarian_prompt"]


def librarian_prompt() -> str:
    """The Librarian's system prompt — one file, one substitution (LB-3, PR-6).

    ``prompts/standards.md`` is deliberately **not** prepended. That file addresses a Topic Expert in
    the second person (*"You are the Topic Expert for …"*) and carries topic placeholders the
    Librarian has no values for; ``prompts/librarian.md`` is self-contained by design and covers the
    two tools the Librarian ever holds, ``route`` and ``create_topic``.

    The only substitution is the knowledge-base root as the *agent* sees it. The prompt names
    ``/kb/index.md`` and ``/kb/tags.md`` through that token rather than spelling the mount, so RT-8's
    "the mount is written in exactly one module" survives into the package data — and a mount change
    cannot silently desynchronise the prompt from the backend routes.
    """
    return render_prompt(load_prompt(LIBRARIAN_PROMPT), {"KB_ROOT": KB_MOUNT.rstrip("/")})


def build_librarian(
    kb_root: Path,
    runtime: GraphRuntime,
    *,
    model: str | BaseChatModel,
    registry: SupportsInvalidate | None = None,
    tools: Sequence[BaseTool] = (),
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the root Librarian agent — the classification step of the routing turn (LB-1 … LB-13).

    There is deliberately **no ``subagents`` parameter**. Registering the experts here is what gave
    the model the choice of whether to use them, and the human ruled that choice out; the fan-out is
    :class:`~pkb.agents.routing.FanOut`, driven by the runtime, and it runs whether the model would
    have wanted it or not. What the model is left with is the judgement that genuinely needs one:
    which topics the item concerns, expressed as a call to ``route``.

    Args:
        kb_root: The knowledge base on disk.
        runtime: The shared singletons (RT-1) — the same objects every expert receives, which is
            what lets an expert reached by the fan-out and the same expert reached directly share
            one durable history, one backend and one write lock.
        model: Always explicit (RG-21, EX-9).
        registry: Invalidated after a turn that changed an ``expert.md``, a skill or a ``topic.md``
            (MW-30). Topic creation invalidates through the ``create_topic`` tool itself (LB-7).
        tools: Additional tools. This is where the registry passes ``create_topic`` (LB-7); passing
            it is the whole of the wiring, and it is no longer gated (Task 6 stops composing
            ``interrupt_on`` for this graph). The ``route`` tool is **not** passed in — it is
            intrinsic to what a Librarian is, like its middleware, and a Librarian compiled without
            it could not route at all.

    Returns:
        A compiled graph, always with the general-purpose subagent attached (EX-11) and never with an
        expert attached (LB-12).
    """
    breadth = KbBreadthMiddleware.for_librarian(kb_root)
    main, delegated = kb_middleware(kb_root, runtime, breadth, registry=registry)
    skills = base_skill_sources(kb_root)

    return create_deep_agent(
        model=model,
        system_prompt=librarian_prompt(),
        tools=[*tools, route_tool()],
        middleware=[RouteMiddleware(), *main],
        subagents=[general_purpose_subagent(delegated, skills)],
        skills=skills,
        permissions=kb_permissions(None),
        backend=runtime.backend,
        checkpointer=runtime.checkpointer,
        store=runtime.store,
        name=LIBRARIAN_AGENT_ID,
    )
