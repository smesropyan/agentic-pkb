"""The Librarian graph factory — LB-1 … LB-11.

The Librarian is the front door: a compiled deep agent in its own right, with its own id and its own
threads, not a dispatcher function in front of the experts (LB-1). Every channel connects to it by
default, it routes each inbound item to the expert whose ``topic.md`` description covers it, it goes
wide to several experts when an item spans topics and merges what they return, and it proposes a new
topic when nothing fits.

Three properties of this factory are worth stating, because each is a rule that would otherwise be
invisible in the code:

**It holds no knowledge and no write capability.** ``kb_permissions(None)`` denies every write under
``/kb/**`` (RT-16), and its only skill sources are the packaged mount and the knowledge base's own
``skills/`` — nothing topic-scoped (LB-5). Filing a note needs the topic's skills, its voice overload
and its ``expert.md`` behaviour, none of which the Librarian loads; a note it filed itself would be a
note written without the expertise the note is supposed to carry. Its one mutation is the gated
``create_topic`` tool, which the registry passes in and which writes through
:func:`pkb.core.scaffold_topic` under the write lock (LB-7, RT-18).

**Its prompt is knowledge-base-independent (LB-3).** No topic names, no descriptions, no per-topic
instructions — the routing view is the *generated* root ``index.md``, loaded fresh each turn by
:class:`~pkb.agents.middleware.breadth.KbBreadthMiddleware` (LB-4), and the topic descriptions reach
the model a second way, through the ``task`` tool description deepagents builds from the registered
subagents (RG-10). Nothing about routing is maintained by hand, so nothing about it can go stale.

**An empty knowledge base must compile (LB-6).** Bootstrapping starts with zero topics and every
inbound item is a topic gap. ``subagents=()`` is the normal first-boot state, not an error, and the
Librarian still gets its general-purpose subagent, its gates and its flush.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from deepagents import CompiledSubAgent, create_deep_agent
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
from pkb.agents.gates import GateEnv, build_interrupt_on
from pkb.agents.middleware.breadth import KbBreadthMiddleware
from pkb.agents.middleware.maintenance import SupportsInvalidate
from pkb.agents.paths import KB_MOUNT
from pkb.agents.permissions import kb_permissions
from pkb.core.paths import LIBRARIAN_AGENT_ID

__all__ = ["build_librarian", "librarian_prompt"]


def librarian_prompt() -> str:
    """The Librarian's system prompt — one file, one substitution (LB-3, PR-6).

    ``prompts/standards.md`` is deliberately **not** prepended. That file addresses a Topic Expert in
    the second person (*"You are the Topic Expert for …"*) and carries topic placeholders the
    Librarian has no values for; ``prompts/librarian.md`` is self-contained by design and covers the
    one gate the Librarian ever meets, ``create_topic``.

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
    subagents: Sequence[CompiledSubAgent] = (),
    registry: SupportsInvalidate | None = None,
    tools: Sequence[BaseTool] = (),
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the root Librarian agent (LB-1 … LB-11).

    Args:
        kb_root: The knowledge base on disk.
        runtime: The shared singletons (RT-1) — the same objects every expert receives, which is
            what lets a delegated expert's work checkpoint under the Librarian's own thread (D-6).
        model: Always explicit (RG-21, EX-9).
        subagents: One :class:`~deepagents.CompiledSubAgent` per topic, built by the registry. They
            are passed through verbatim: each is ``{name, description, runnable}`` and nothing else
            (RG-7), the name **is** the agent id the generated root index renders in backticks
            (RG-9), and the description **is** the topic's own ``topic.md`` description so routing
            and the human's routing view are driven by one string (RG-10). A *compiled* subagent —
            rather than a declarative spec — is what lets one graph serve both access paths, direct
            and delegated, and hold the multi-turn approval dialog README §1.6 needs.
        registry: Invalidated after a turn that changed an ``expert.md``, a skill or a ``topic.md``
            (MW-30). Topic creation invalidates through the ``create_topic`` tool itself (LB-7).
        tools: Additional tools. This is where the registry passes the gated ``create_topic``
            (LB-7); the gate table already carries an entry for that tool name, so passing it is the
            whole of the wiring.

    Returns:
        A compiled graph, always with at least the general-purpose subagent attached (EX-11).
    """
    breadth = KbBreadthMiddleware.for_librarian(kb_root)
    main, delegated = kb_middleware(kb_root, runtime, breadth, registry=registry)
    skills = base_skill_sources(kb_root)

    return create_deep_agent(
        model=model,
        system_prompt=librarian_prompt(),
        tools=list(tools),
        middleware=main,
        subagents=[*subagents, general_purpose_subagent(delegated, skills)],
        skills=skills,
        permissions=kb_permissions(None),
        backend=runtime.backend,
        interrupt_on=build_interrupt_on(GateEnv(snapshot=runtime.snapshot)),
        checkpointer=runtime.checkpointer,
        store=runtime.store,
        name=LIBRARIAN_AGENT_ID,
    )
