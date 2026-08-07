"""The agent catalog and the lazy graph cache — ``RG-1`` … ``RG-22``.

Two kinds of agent exist and no more: one Librarian and one Topic Expert per topic root, at every
depth (RG-1, RG-12). This module is the only place that knows which agents exist, what they are
called, which model each runs on, and when a compiled graph has gone stale.

**One scan, no walk of our own** (RG-2). The catalog is a projection of a single
:func:`pkb.core.scan.scan` call. ``KbSnapshot.topics`` already carries ``path``, ``agent_id``,
``tag``, ``parent``, ``children``, ``has_expert`` and ``meta``, so nothing here re-derives an id, a
slug or a parent chain — Layer 1 owns all of it (RG-11), and a second implementation would drift the
first time a folder name grows a character Layer 1 slugifies differently.

**Nothing is compiled until it is used** (RG-3, RG-4, RG-8). Building the registry compiles zero
graphs; so does listing the agents, and so does compiling the Librarian — fifty topics must not mean
fifty graphs at boot. That used to need a lazy ``Runnable`` proxy, because the Librarian's
``subagents=`` list was materialized while the *parent* graph compiled. With the roster gone (LB-12)
there is nothing to reconcile: the Librarian's graph names no topic at all, and the routing workflow
calls :meth:`AgentRegistry.get` for exactly the experts it routed to, when it routes to them.

**One graph per topic, one way in** (RG-6). Every access path — a direct conversation with an expert,
a run the Librarian's fan-out starts, a conflict scan — resolves through :meth:`AgentRegistry.get`,
so an expert's ``expert.md``, skills and model configuration cannot differ between them.

**RG-18 — invalidation cannot reach a running thread.** deepagents' ``SkillsMiddleware`` loads the
skill set once per thread and stores it in checkpointed state; its ``before_agent`` returns early
whenever ``skills_metadata`` is already present. So a skill added mid-session is invisible to every
thread that has already taken a turn, no matter how many times :meth:`AgentRegistry.invalidate` is
called: the stale value lives in the *checkpoint*, not in this cache. A fresh skill set needs a new
thread (or a state key this layer deliberately does not reach into). This is a harness property, not
a defect here — do not "fix" it by eagerly rebuilding graphs, which changes nothing observable and
costs the laziness RG-4 exists for.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol

from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from pkb.agents.expert import GraphRuntime, build_expert
from pkb.agents.librarian import build_librarian
from pkb.agents.models import model_id_of, with_fallback
from pkb.contracts import AgentDescriptor, UnknownAgentError
from pkb.core.errors import NotATopicRootError
from pkb.core.generators import base as render
from pkb.core.models import KbSnapshot, TopicRecord
from pkb.core.paths import LIBRARIAN_AGENT_ID, topic_path_for_agent_id
from pkb.core.scan import scan

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from langchain_core.tools import BaseTool

__all__ = [
    "DEFAULT_FALLBACK_MODEL",
    "DEFAULT_MODEL",
    "LIBRARIAN_DESCRIPTION",
    "LIBRARIAN_TITLE",
    "AgentGraph",
    "AgentRegistry",
    "ExpertFactory",
    "LibrarianFactory",
]


type AgentGraph = CompiledStateGraph[Any, Any, Any, Any]
"""What a factory returns. Layer-2-internal: no type Layer 3 imports mentions it (RG-14)."""


DEFAULT_MODEL: Final = "ollama:deepseek-v4-flash:cloud"
"""The model every agent runs on unless ``models`` names a different one (RG-21).

Chosen on measured evidence rather than on reputation: 5/5 on a five-task live evaluation of this
workload, ~16s per filing turn, and cheap. The deployment is an Ollama Pro plan — three concurrent
cloud models, usage weighted per model, quota resetting on 5-hour and weekly windows — so the ceiling
this default runs into is a ``429``, not a bad answer, which is exactly what
:data:`DEFAULT_FALLBACK_MODEL` exists to absorb.

``init_chat_model`` splits the spec on its first colon only, so the ``:cloud`` tag survives into
``ChatOllama(model="deepseek-v4-flash:cloud")`` (verified on the pin). Deliberately not deepagents'
own fallback (``claude-sonnet-4-6``): ``model=None`` is deprecated and silently shadows whatever
default the deployment configured, so the registry always passes a model explicitly.
"""

DEFAULT_FALLBACK_MODEL: Final = "ollama:gemma4:31b"
"""The model that answers when :data:`DEFAULT_MODEL` cannot (RG-21).

The **local** tag — no ``-cloud`` suffix — and that is the entire point: running it locally is never
metered, so it keeps the knowledge base working precisely in the situation the default cannot, when
the cloud quota is exhausted or the endpoint is down. It also scored 5/5 on the same evaluation, so
a failover degrades cost and latency rather than judgement.

It is **not pulled on this machine** and nothing here pulls it — a ~20GB download must not happen as
a side effect of compiling a graph. So it is constructed lazily (see
:func:`pkb.agents.models.with_fallback`) and an absent local model costs nothing until the day it is
needed, at which point :class:`~pkb.agents.models.ModelNotInstalledError` names ``ollama pull``.
"""

LIBRARIAN_TITLE: Final = "Librarian"
"""Display title of the root agent. Fixed, because the Librarian prompt is KB-independent (LB-3)."""

LIBRARIAN_DESCRIPTION: Final = (
    "Routes each item to the right Topic Expert, merges multi-topic answers, "
    "and proposes a new topic when an item fits none."
)
"""The root agent's one-line gloss — its four responsibilities in the order LB-2 fixes them."""

ToolFactory = Callable[[str], Sequence["BaseTool"]]
"""Builds the extra tools one agent carries, given its agent id.

The Librarian's gated ``create_topic`` (LB-7) and an expert's scope-limited ``create_subtopic``
(EX-12) are both *per-agent*: the expert's may create sub-topics only under its own root, so one
shared instance cannot serve every topic. The registry is the only place that knows which agent is
being built, so it is where the hook belongs — but constructing the tools is not its business, which
is why this is an injected callable and not an import of ``pkb.agents.tools``.
"""


class ExpertFactory(Protocol):
    """The call ``pkb.agents.expert.build_expert`` must answer (EX-1, RG-1).

    Deliberately narrower than the real function in one place, which is what makes the real one
    assignable here — parameters are contravariant, so a factory that accepts *more* still answers
    this call: ``registry`` is this class because the registry passes itself, where ``build_expert``
    only asks for something it can ``invalidate()``.

    ``model`` is ``str | BaseChatModel`` because the registry resolves the *choice* (RG-21) and then
    may hand over an object: a configured fallback makes the resolved model a
    :class:`~pkb.agents.models.FallbackChatModel` instance rather than a spec string. The choice is
    still the registry's; only its representation changed.
    """

    def __call__(
        self,
        kb_root: Path,
        topic_path: str,
        runtime: GraphRuntime,
        *,
        model: str | BaseChatModel,
        registry: AgentRegistry,
        tools: Sequence[BaseTool],
    ) -> AgentGraph: ...


class LibrarianFactory(Protocol):
    """The call ``pkb.agents.librarian.build_librarian`` must answer (LB-1, RG-1).

    **There is no ``subagents`` parameter, and its absence is the point** (LB-12). Handing the
    Librarian the expert roster is what made delegation a choice the model could decline, and the
    human ruled that choice out on 2026-08-07: the fan-out is code in
    :class:`~pkb.agents.routing.FanOut`, driven by the runtime over :meth:`AgentRegistry.get`, and it
    runs whether the model would have called ``task`` or not. A Librarian that still carried the
    roster would carry the bypass with it.

    A pleasant consequence: the compiled Librarian no longer depends on the catalog at all, so a
    topic created mid-session is routable without recompiling it. RG-16 still drops it — the cost is
    one recompile of one graph, and depending on that independence is a promise this layer does not
    need to make.
    """

    def __call__(
        self,
        kb_root: Path,
        runtime: GraphRuntime,
        *,
        model: str | BaseChatModel,
        registry: AgentRegistry,
        tools: Sequence[BaseTool],
    ) -> AgentGraph: ...


_EXPERT_MATCHES: ExpertFactory = build_expert
_LIBRARIAN_MATCHES: LibrarianFactory = build_librarian
"""Static proof that the two factories still answer the calls above.

These bindings exist for the type checker alone: a rename or a signature change in ``expert.py`` or
``librarian.py`` fails ``mypy`` here rather than at runtime on the first delegation.
"""


@dataclass(frozen=True, slots=True)
class _Catalog:
    """One scan, projected (RG-2). Rebuilt wholesale by :meth:`AgentRegistry.invalidate`."""

    snapshot: KbSnapshot
    descriptors: tuple[AgentDescriptor, ...]
    """Librarian first, then topics in snapshot order — the root index's own order (RG-15)."""

    topics: Mapping[str, TopicRecord]
    """Topic records keyed by agent id. First wins on the id collision Layer 1 already reports."""


class AgentRegistry:
    """The catalog of agents and the cache of their compiled graphs (RG-1 … RG-22).

    Public surface is exactly :meth:`list_agents`, :meth:`get`, :meth:`subagents` and
    :meth:`invalidate` (RG-20). No method takes or returns a ``thread_id`` or a ``run_id``: threads
    are Layer 3's bookkeeping and delegated work is not separately resumable (LB-9), so a registry
    that knew about either would invite a table that cannot be kept true.

    The registry is **read-only over the tree** (RG-19): it scans, it reads, and it never scaffolds,
    flushes, writes a derived file or touches frontmatter. ``invalidate`` re-scans — that is a read.
    """

    def __init__(
        self,
        kb_root: Path,
        runtime: GraphRuntime,
        *,
        default_model: str | BaseChatModel = DEFAULT_MODEL,
        models: Mapping[str, str | BaseChatModel] | None = None,
        fallback_model: str | BaseChatModel | None = DEFAULT_FALLBACK_MODEL,
        tool_factory: ToolFactory | None = None,
        expert_factory: ExpertFactory = build_expert,
        librarian_factory: LibrarianFactory = build_librarian,
    ) -> None:
        """Wire the registry to one knowledge base.

        Args:
            kb_root: The knowledge base on disk. Scanned once per catalog build (RG-2).
            runtime: The shared singletons every graph is handed — one checkpointer, one store, one
                backend, one write lock (RT-1). Required, not optional: a registry that could build
                a graph without them would let a delegated expert and the same expert reached
                directly keep different durable histories.
            default_model: The model every agent runs unless ``models`` overrides it (RG-21).
            models: Per-agent-id model overrides. The model is a *registry* concern: no transport,
                route or channel picks one, and it is never read from KB content — a ``model:`` key
                in ``topic.md`` is an ``UNKNOWN_FIELD`` warning (VA-32), and putting deployment
                configuration in the tree would drag it through the human-approval workflow.
            fallback_model: The model that answers when the chosen one hits quota, concurrency or an
                unreachable endpoint (:func:`pkb.agents.models.with_fallback`). One setting for the
                whole registry rather than a second per-agent table: the fallback's job is to be the
                thing that always works, and a per-agent safety net is a per-agent way to not have
                one. ``None`` disables it and passes the chosen model through untouched — which is
                also what makes a registry configured that way byte-identical to one with no
                failover code at all.
            tool_factory: Builds the extra tools for one agent id — ``create_topic`` for the
                Librarian (LB-7), ``create_subtopic`` for an expert (EX-12). ``None`` means the
                agents carry only the built-ins.
            expert_factory: Injection point for :func:`pkb.agents.expert.build_expert`, which is
                the default. Only a test replaces it; RG-1 allows the registry exactly these two.
            librarian_factory: Injection point for :func:`pkb.agents.librarian.build_librarian`.
        """
        self.kb_root = kb_root
        self.default_model = default_model
        self.models: Mapping[str, str | BaseChatModel] = dict(models or {})
        self.fallback_model = fallback_model
        self._runtime = runtime
        self._tool_factory = tool_factory
        self._expert_factory = expert_factory
        self._librarian_factory = librarian_factory
        # RLock, not Lock: `get` holds it across the build, and `build_librarian` calls back into
        # `subagents()` -> `_catalog()`, which takes it again on the same thread.
        self._lock = threading.RLock()
        self._cached_catalog: _Catalog | None = None
        self._graphs: Mapping[str, AgentGraph] = {}

    # -- catalog ------------------------------------------------------------------------

    def list_agents(self) -> list[AgentDescriptor]:
        """Every addressable agent: the Librarian, then the topics in snapshot order (RG-1, RG-15).

        Snapshot order is depth-first pre-order, parent before child (PA-5) — the same order root
        ``index.md`` renders, so a TUI listing this and a human reading the catalog see one tree.
        Sub-topics are first-class entries at every depth (RG-12); ids are opaque strings that may
        contain ``/`` and are returned verbatim.
        """
        return list(self._catalog().descriptors)

    # -- graphs -------------------------------------------------------------------------

    def get(self, agent_id: str) -> AgentGraph:
        """The compiled graph for ``agent_id``, built on first use and cached (RG-4, RG-5).

        Concurrency-safe by double-checked caching: the fast path reads a mapping that is only ever
        *replaced*, never mutated in place, so it needs no lock; the slow path holds the lock across
        the whole build, so ten simultaneous first-uses of one id compile exactly one graph and all
        ten callers receive it. A lock is genuinely required — the daemon streams runs concurrently
        and Layer 2 also compiles from worker threads, so an unguarded check-then-build races.

        Raises:
            UnknownAgentError: No agent answers to that id (RG-13). Layer 3 maps this to 404, which
                is why Layer 1's :class:`~pkb.core.errors.NotATopicRootError` is translated rather
                than allowed to escape as a 500.
        """
        cached = self._graphs.get(agent_id)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._graphs.get(agent_id)
            if cached is not None:
                return cached
            graph = self._compile(agent_id)
            self._graphs = {**self._graphs, agent_id: graph}
            return graph

    def invalidate(self, agent_id: str | None = None) -> None:
        """Re-scan the tree and drop the graphs the scan makes wrong (RG-16, RG-17).

        Always a re-scan, never an mtime staleness check: mtime granularity is a second on the
        network and sync filesystems a personal knowledge base lives on, and a missed invalidation
        is silent.

        Three things happen every time, whatever ``agent_id`` says:

        * the catalog is rebuilt, so a topic created mid-session appears in :meth:`list_agents`;
        * ids no longer in the catalog are **evicted** — a renamed or removed topic must never hand
          out a stale graph;
        * the Librarian's graph is dropped. Its subagent list and the ``task`` tool description are
          a snapshot taken at compile time (``subagents.py:457-462``), so without this a new topic
          is listed but unroutable.

        Args:
            agent_id: ``None`` drops every cached graph — the safe default, and what
                ``KbMaintenanceMiddleware`` calls (MW-30). A topic id additionally drops that
                topic's own graph **and its descendants'**: an ``expert.md`` appearing or vanishing
                changes the prompt source for the whole subtree beneath it (RG-17, PA-13).
                Descendants come from ``TopicRecord.children``, never from splitting the id — ids
                are opaque (RG-12).
        """
        with self._lock:
            catalog = self._build_catalog()
            self._cached_catalog = catalog
            if agent_id is None:
                self._graphs = {}
                return
            dropped = {agent_id} | self._descendant_ids(catalog, agent_id)
            # `in catalog.topics` does double duty and both halves are load-bearing: it evicts ids
            # the re-scan no longer knows, and — because the Librarian is not a topic — it is also
            # what drops the Librarian unconditionally. Relaxing it to "keep anything not dropped"
            # leaves a Librarian whose `task` tool cannot reach the topic just created.
            self._graphs = {
                cached_id: graph
                for cached_id, graph in self._graphs.items()
                if cached_id in catalog.topics and cached_id not in dropped
            }

    # -- internals ----------------------------------------------------------------------

    def _catalog(self) -> _Catalog:
        with self._lock:
            if self._cached_catalog is None:
                self._cached_catalog = self._build_catalog()
            return self._cached_catalog

    def _build_catalog(self) -> _Catalog:
        """Project one :func:`pkb.core.scan.scan` into descriptors (RG-2, RG-3).

        Reads no ``expert.md``, no ``SKILL.md`` as a prompt and compiles nothing: prompt source
        selection is ``resolve_expert``'s job at *build* time, and paying for it here would make
        boot cost proportional to the tree.
        """
        snapshot = scan(self.kb_root)
        topics: dict[str, TopicRecord] = {}
        for record in snapshot.topics.values():
            # setdefault, not assignment: two topic folders whose every segment slugifies away
            # share the fallback id, and Layer 1 has already reported UNADDRESSABLE_TOPIC_ROOT for
            # both. First wins here; neither is dropped from `descriptors`.
            topics.setdefault(record.agent_id, record)
        descriptors = (
            self._librarian_descriptor(),
            *(self._topic_descriptor(record) for record in snapshot.topics.values()),
        )
        return _Catalog(snapshot=snapshot, descriptors=descriptors, topics=topics)

    def _librarian_descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            agent_id=LIBRARIAN_AGENT_ID,
            title=LIBRARIAN_TITLE,
            description=LIBRARIAN_DESCRIPTION,
            has_custom_expert=False,
            model_id=self._model_id_for(LIBRARIAN_AGENT_ID),
        )

    def _topic_descriptor(self, record: TopicRecord) -> AgentDescriptor:
        """One catalog row (RG-14). A degraded ``topic.md`` degrades the row, never drops it."""
        return AgentDescriptor(
            agent_id=record.agent_id,
            title=record.title,
            description=_catalog_description(record),
            has_custom_expert=record.has_expert,
            model_id=self._model_id_for(record.agent_id),
        )

    def _model_for(self, agent_id: str) -> str | BaseChatModel:
        """The model this agent runs on, as configured (RG-21). Private: RG-20 pins the surface."""
        return self.models.get(agent_id, self.default_model)

    def _model_id_for(self, agent_id: str) -> str:
        """The id a catalog row carries (RG-14). Always the **primary** — a descriptor describes the
        deployment's choice, and a failover is a runtime event, not a different configuration."""
        return model_id_of(self._model_for(agent_id))

    def _chat_model_for(self, agent_id: str) -> str | BaseChatModel:
        """What a factory is handed: the configured model, wrapped in its fallback (RG-21).

        Wrapping is what makes the failover a *registry* property. Doing it here rather than in
        ``expert.py``/``librarian.py`` is the same argument RG-21 already makes about the model
        itself — there are two factories and both would have to remember, and a graph compiled
        without the wrapper is one whose quota exhaustion is a hard failure with no signal.

        With :attr:`fallback_model` set to ``None`` the configured value is passed through
        unchanged, so ``create_deep_agent`` resolves it exactly as it always did.
        """
        model = self._model_for(agent_id)
        if self.fallback_model is None:
            return model
        return with_fallback(model, self.fallback_model)

    def _compile(self, agent_id: str) -> AgentGraph:
        """Call exactly one of the two factories (RG-1, RG-22).

        The lock is held throughout; ``_catalog`` and ``subagents`` re-enter it on this thread,
        which is why it is an :class:`~threading.RLock`.
        """
        model = self._chat_model_for(agent_id)
        tools = self._tool_factory(agent_id) if self._tool_factory is not None else ()
        if agent_id == LIBRARIAN_AGENT_ID:
            return self._librarian_factory(
                self.kb_root,
                self._runtime,
                model=model,
                registry=self,
                tools=tools,
            )
        catalog = self._catalog()
        record = catalog.topics.get(agent_id)
        if record is None:
            raise UnknownAgentError(f"no agent is registered under the id {agent_id!r}")
        # Called for its refusal, not its return value: it is Layer 1's sanctioned inverse of
        # `agent_id_for` (RG-11), and the `NotATopicRootError` it raises is exactly RG-13's signal
        # that the catalog is stale — the topic was renamed or removed since the scan. The path
        # handed to the factory is `TopicRecord.path`, Layer 1's own KB-relative string (EX-1).
        try:
            topic_path_for_agent_id(self.kb_root, agent_id)
        except NotATopicRootError as exc:
            raise UnknownAgentError(f"no agent is registered under the id {agent_id!r}") from exc
        return self._expert_factory(
            self.kb_root,
            record.path,
            self._runtime,
            model=model,
            registry=self,
            tools=tools,
        )

    @staticmethod
    def _descendant_ids(catalog: _Catalog, agent_id: str) -> set[str]:
        """Agent ids beneath ``agent_id``, read off ``TopicRecord.children`` (RG-12, RG-17)."""
        record = catalog.topics.get(agent_id)
        if record is None:
            return set()
        found: set[str] = set()
        pending = list(record.children)
        while pending:
            child = catalog.snapshot.topics.get(pending.pop())
            if child is None or child.agent_id in found:
                continue
            found.add(child.agent_id)
            pending.extend(child.children)
        return found


def _catalog_description(record: TopicRecord) -> str:
    """The description the root catalog renders for this topic (RG-10, GE-25).

    One string serves three consumers — the root ``index.md`` line, ``AgentDescriptor.description``
    in a transport's agent list, and ``CompiledSubAgent.description``, which deepagents interpolates
    into the ``task`` tool description the Librarian routes on. Composed out of Layer 1's own
    rendering pieces (:func:`pkb.core.generators.base.inline` and its two degradation placeholders)
    rather than re-escaped here, so a description containing brackets or a newline reaches the
    routing prompt in exactly the form the human reads in the catalog.
    """
    if record.meta is None:
        return render.MISSING_TOPIC_METADATA
    if not record.meta.description:
        return render.NO_DESCRIPTION
    return render.inline(record.meta.description)
