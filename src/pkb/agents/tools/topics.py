"""``create_topic`` and ``create_subtopic`` — the two gated scaffolding tools (LB-7, EX-12).

Creating a topic is an **agent-invoked tool**, not a transport endpoint: README's "all interactions
are agent-mediated" means the human proposes a topic in conversation and the agent carries it out,
so there is no ``POST /topics``. The Librarian owns :func:`create_topic_tool` because it is the
agent that notices an inbound item fits no existing topic; the Topic Expert owns
:func:`create_subtopic_tool`, scope-limited to its own subtree, because splitting a topic is a
judgment about *that* topic's contents.

**Approval comes first, and not from here.** ``HumanInTheLoopMiddleware`` fires in ``after_model``,
strictly before any tool body runs, and :func:`pkb.agents.gates.build_interrupt_on` already carries
an entry for both tool names — so by the time the functions below execute, a human has approved,
edited or the call never happened. The tool names are therefore load-bearing: rename either one and
topic creation silently stops gating. Layer 1's scaffolder has no approval parameter of its own by
design (SC-8), which is exactly why the gate has to be real.

**Nothing here re-implements scaffolding.** :func:`pkb.core.scaffold_topic` and
:func:`pkb.core.scaffold_subtopic` write the six standard paths, refuse an illegal location, refuse
a fifth tag level and never overwrite (SC-1 … SC-12). This module contributes three things Layer 1
deliberately does not: the write lock (RT-51), the scope limit (EX-12), and turning Layer 1's
exceptions into a *refusal the model can act on* rather than a crash — an exception escaping a tool
body aborts the pregel superstep, which also skips the maintenance flush (D-1).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

from langchain_core.tools import BaseTool, StructuredTool

from pkb.agents.middleware.maintenance import (
    NULL_WRITE_LOCK,
    KbWriteLock,
    SupportsInvalidate,
)
from pkb.core import ScaffoldResult, scaffold_subtopic, scaffold_topic
from pkb.core.errors import PkbError
from pkb.core.paths import LIBRARIAN_AGENT_ID, SUBTOPICS_DIR, topic_tag_for
from pkb.core.tags import MAX_TAG_DEPTH

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from pkb.core.models import KbSnapshot, TopicRecord

__all__ = [
    "CREATE_SUBTOPIC",
    "CREATE_TOPIC",
    "TopicToolEnv",
    "create_subtopic_tool",
    "create_topic_tool",
    "topic_tools",
]

CREATE_TOPIC: Final = "create_topic"
"""The Librarian's tool name. A key of :data:`pkb.agents.gates.GATED_TOOLS` — see the module
docstring for why renaming it un-gates topic creation."""

CREATE_SUBTOPIC: Final = "create_subtopic"
"""The Topic Expert's tool name. Same contract, same warning."""


@dataclass(frozen=True, slots=True)
class TopicToolEnv:
    """Everything the two tools need from the runtime, injected rather than imported.

    Keeps this module below :mod:`pkb.agents.runtime` — the runtime builds one of these per tool
    request — and lets a test drive the tools with a temporary knowledge base, a spy lock and no
    registry at all.
    """

    kb_root: Path
    snapshot: Callable[[], KbSnapshot]
    """The current tree. Used to resolve an agent id to its topic and to enforce the scope limit,
    so this module walks nothing and re-derives no id (RG-2, RG-11)."""

    lock: KbWriteLock = NULL_WRITE_LOCK
    """The process-wide knowledge-base write lock (RT-51). Held around the scaffold and nothing
    else — a scaffold ends with a full regeneration (SC-7), which is precisely the critical section
    the flush also takes, and reentrancy is what keeps the two safe together (RT-53)."""

    registry: SupportsInvalidate | None = None
    """Invalidated after a successful scaffold. Without it the new topic is on disk and in the root
    catalog but unroutable: the Librarian's subagent list and its ``task`` tool description are a
    snapshot taken when its graph was compiled (RG-16, LB-7)."""

    clock: Callable[[], date] = date.today
    """Injected ``today`` (CX-2). Layer 1 takes the date as an argument so a scaffold is
    reproducible; reading the wall clock here would put it back."""


# --------------------------------------------------------------------------------------
# The tools
# --------------------------------------------------------------------------------------


def create_topic_tool(env: TopicToolEnv) -> BaseTool:
    """The Librarian's gated topic creator (LB-7).

    Creates a top-level topic only. A sub-topic belongs to the expert that owns the parent (EX-12),
    which is not a permission technicality: deciding that "Grilling" should split out of "Cooking"
    needs to know what is in Cooking, and the Librarian deliberately holds no topic knowledge
    (LB-5).
    """

    def create(name: str, title: str, description: str) -> str:
        return _scaffold(
            env,
            lambda today: scaffold_topic(
                env.kb_root, name, title=title, description=description, today=today
            ),
        )

    async def acreate(name: str, title: str, description: str) -> str:
        return await asyncio.to_thread(create, name, title, description)

    return StructuredTool.from_function(
        func=create,
        coroutine=acreate,
        name=CREATE_TOPIC,
        description=(
            "Create a new top-level topic in the knowledge base. Propose this when an inbound item "
            "fits no existing topic. `name` is the folder name as the human should see it in the "
            "tree (e.g. 'Cooking'); `title` is its display title; `description` is the single line "
            "the root catalog renders and every agent routes on, so it must say what the topic "
            "covers rather than restate the title. The human approves, edits or rejects this "
            "before anything is written."
        ),
    )


def create_subtopic_tool(env: TopicToolEnv, topic_path: str) -> BaseTool:
    """A Topic Expert's gated sub-topic creator, limited to its own subtree (EX-12).

    ``topic_path`` is the expert's own :attr:`~pkb.core.models.TopicRecord.path`. A parent outside
    that subtree is refused rather than scaffolded: an expert that could create a sub-topic under a
    neighbour would be filing into a tree it cannot read the breadth of, which is the same mistake
    RT-15's write scoping exists to prevent.

    Depth is pre-checked so SC-9's :class:`~pkb.core.errors.TopicDepthExceededError` reaches the
    model as a refusal naming the limit. That matters for the retry loop: a crash costs the run,
    while a refusal costs one turn and tells the model to propose a flatter split instead.
    """

    def create(name: str, title: str, description: str, parent_topic_path: str = "") -> str:
        parent = parent_topic_path.strip() or topic_path
        refusal = _scope_refusal(env, topic_path, parent) or _depth_refusal(env, parent, name)
        if refusal is not None:
            return refusal
        return _scaffold(
            env,
            lambda today: scaffold_subtopic(
                env.kb_root, parent, name, title=title, description=description, today=today
            ),
        )

    async def acreate(name: str, title: str, description: str, parent_topic_path: str = "") -> str:
        return await asyncio.to_thread(create, name, title, description, parent_topic_path)

    return StructuredTool.from_function(
        func=create,
        coroutine=acreate,
        name=CREATE_SUBTOPIC,
        description=(
            f"Create a sub-topic under {topic_path!r} or one of its existing sub-topics. Propose "
            "this when the topic has grown past what its breadth files can summarize honestly. "
            "`name` is the folder name; `title` is its display title; `description` is the single "
            "line the catalogs render. `parent_topic_path` defaults to this expert's own topic and "
            "must name a topic inside its subtree. The human approves, edits or rejects this "
            "before anything is written."
        ),
    )


def topic_tools(env: TopicToolEnv, agent_id: str) -> list[BaseTool]:
    """The scaffolding tool this agent carries, if any — the registry's ``tool_factory`` body.

    The Librarian gets :func:`create_topic_tool`; every topic expert gets its own
    :func:`create_subtopic_tool`, bound to *its* topic path. An id the current snapshot does not
    know gets nothing rather than an error: the registry is what answers a bad id with a typed 404
    (RG-13), and a tool factory that raised would turn a stale catalog entry into a failed graph
    build instead.
    """
    if agent_id == LIBRARIAN_AGENT_ID:
        return [create_topic_tool(env)]
    topic = _topic_for(env.snapshot(), agent_id)
    if topic is None:
        return []
    return [create_subtopic_tool(env, topic.path)]


# --------------------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------------------


def _scaffold(env: TopicToolEnv, call: Callable[[date], ScaffoldResult]) -> str:
    """Run one scaffold under the write lock and report it, refusing rather than raising.

    The lock covers the scaffold because it ends with a full regeneration (SC-7) that rewrites the
    root catalog and root ``tags.md`` — the same files every other thread's flush rewrites. It is
    released before this returns, so it is never held across a model call or an approval (RT-52).

    Every :class:`~pkb.core.errors.PkbError` becomes text. Layer 1 refuses an illegal location, an
    unusable name, a missing parent and a fifth tag level by raising (SC-9, SC-11, PA-4), and each
    of those is something the model can fix in its next turn — but an exception escaping a tool body
    aborts the superstep and takes the maintenance flush with it (D-1), so the run would end with
    the tree half-maintained and the human none the wiser.
    """
    try:
        with env.lock:
            result = call(env.clock())
    except PkbError as exc:
        return f"Refused — nothing was created. {exc}"
    if env.registry is not None:
        env.registry.invalidate()
    return _render(result)


def _render(result: ScaffoldResult) -> str:
    """What the model is told it just did.

    Names every created path so the agent can say so to the human without a second tool call, and
    names the skipped ones so a re-scaffold of a partially created topic reads as the repair it is
    (SC-10) rather than as a silent success.
    """
    lines = [f"Created the topic {result.topic_path!r}."]
    if result.created:
        lines += ["", "Created:", *(f"- {path}" for path in result.created)]
    if result.skipped:
        lines += [
            "",
            "Already present and left untouched:",
            *(f"- {path}" for path in result.skipped),
        ]
    return "\n".join(lines)


def _topic_for(snapshot: KbSnapshot, agent_id: str) -> TopicRecord | None:
    """The topic an agent id addresses, read off the snapshot Layer 1 produced (RG-11)."""
    for record in snapshot.topics.values():
        if record.agent_id == agent_id:
            return record
    return None


def _scope_refusal(env: TopicToolEnv, own_path: str, parent_path: str) -> str | None:
    """EX-12's scope limit: *parent_path* must be *own_path* or a topic beneath it.

    Ancestry is walked over :attr:`~pkb.core.models.TopicRecord.parent`, not by comparing path
    prefixes: ``Cooking`` is not an ancestor of ``Cooking Extra`` however similar the strings look,
    and Layer 1 already publishes the real relationship.
    """
    snapshot = env.snapshot()
    record = snapshot.topics.get(parent_path)
    if record is None:
        return (
            f"Refused — nothing was created. {parent_path!r} is not a topic root in this knowledge "
            "base."
        )
    seen: set[str] = set()
    current: TopicRecord | None = record
    while current is not None and current.path not in seen:
        if current.path == own_path:
            return None
        seen.add(current.path)
        current = snapshot.topics.get(current.parent) if current.parent else None
    return (
        f"Refused — nothing was created. {parent_path!r} is outside {own_path!r}; this expert may "
        f"only create sub-topics inside its own topic. Hand the proposal to the expert that owns "
        f"{parent_path!r}, or to the Librarian if it needs a new top-level topic."
    )


def _depth_refusal(env: TopicToolEnv, parent_path: str, name: str) -> str | None:
    """SC-9's depth cap, pre-checked so it reaches the model as a refusal (EX-12).

    The prospective tag is computed by :func:`pkb.core.paths.topic_tag_for`, which is pure path
    arithmetic and does not require the topic to exist — the same function the scaffolder itself
    uses, so this cannot disagree with the refusal it would raise a moment later.
    """
    candidate = env.kb_root.joinpath(*parent_path.split("/"), SUBTOPICS_DIR, name)
    tag = topic_tag_for(env.kb_root, candidate)
    depth = tag.count(".") + 1
    if depth <= MAX_TAG_DEPTH:
        return None
    return (
        f"Refused — nothing was created. A sub-topic {name!r} under {parent_path!r} would need the "
        f"{depth}-level tag {tag!r}, and a topic tag carries at most {MAX_TAG_DEPTH} levels "
        "including the 'topic' namespace. Propose a flatter split, or a sibling topic instead."
    )
