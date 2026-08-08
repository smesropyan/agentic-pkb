"""`AgentRegistry` — one test per RG rule, named so the rule is greppable.

The whole file runs with no API key and no network. Where the *graph* is not what is under test the
factories are recording spies, which is also how "the registry built exactly one" is counted; where
it is, the graphs are real deep agents driven by `ScriptedChatModel`, including one pass through the
real `build_expert`/`build_librarian` so the seam is proven rather than assumed.

The structural rules (RG-2's "no tree walk", RG-11's "no second id implementation", RG-20's
"thread-free", RG-21's "never `model=None`", RG-22's "two call sites") are asserted over the **AST**
rather than by grepping the source text: every one of those names appears legitimately in a
docstring explaining why it must not appear in code, and a text grep cannot tell the two apart.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import inspect
import json
import re
import sys
import threading
import time
import types
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from pkb.agents.expert import build_expert
from pkb.agents.librarian import build_librarian
from pkb.agents.middleware.maintenance import NULL_WRITE_LOCK
from pkb.agents.models import FallbackChatModel
from pkb.agents.paths import KB_MOUNT, SKILLS_MOUNT
from pkb.agents.registry import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_MODEL,
    AgentRegistry,
)
from pkb.agents.skills import packaged_skills_root
from pkb.contracts import AgentDescriptor, UnknownAgentError
from pkb.core import agent_id_for, regenerate_all, resolve_expert, resolve_skills, scaffold_topic
from pkb.core.errors import NotATopicRootError
from pkb.core.models import KbSnapshot
from pkb.core.paths import LIBRARIAN_AGENT_ID
from pkb.core.scan import scan
from tests.agents.conftest import TODAY, call, calls, says, scripted

SRC = Path(__file__).resolve().parents[2] / "src" / "pkb"
REGISTRY_PATH = SRC / "agents" / "registry.py"

OVERRIDE_MODEL = "ollama:qwen4:32b-thinking"
"""A per-agent override. An Ollama spec on purpose: it resolves with no credentials, so a test
that does reach the resolver stays offline."""

COOKING = "topic/cooking"
GRILLING = "topic/cooking/grilling"
BBQ = "topic/bbq"

BACKTICKED = re.compile(r"`([^`]+)`")


# --------------------------------------------------------------------------------------
# Source audits — AST, so prose about a rule is not mistaken for a breach of it
# --------------------------------------------------------------------------------------


def module_ast(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def called_names(path: Path) -> set[str]:
    """Every function or method name this module *calls*."""
    names = set()
    for node in ast.walk(module_ast(path)):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


NAME_FIELDS: dict[type[ast.AST], str] = {
    ast.Name: "id",
    ast.Attribute: "attr",
    ast.arg: "arg",
    ast.keyword: "arg",
}


def identifiers(path: Path) -> set[str]:
    """Every name this module binds, reads, or passes as a keyword."""
    names = set()
    for node in ast.walk(module_ast(path)):
        field = NAME_FIELDS.get(type(node))
        value = getattr(node, field) if field else None
        if isinstance(value, str):
            names.add(value)
    return names


def code_strings(path: Path) -> set[str]:
    """String literals that are code, not documentation.

    Every bare string *statement* is excluded: that covers module, class and function docstrings
    and the attribute docstrings this codebase writes under its constants.
    """
    tree = module_ast(path)
    documentation = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in documentation
    }


def passes_model_none(path: Path) -> bool:
    return any(
        keyword.arg == "model"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is None
        for node in ast.walk(module_ast(path))
        if isinstance(node, ast.Call)
        for keyword in node.keywords
    )


# --------------------------------------------------------------------------------------
# Fixtures and doubles
# --------------------------------------------------------------------------------------


@dataclasses.dataclass
class FakeRuntime:
    """The shared singletons a graph factory reads (RT-1), minus the daemon.

    Everything a `PkbRuntime` would own and nothing it would do: one backend, no checkpointer
    unless a test wants one, a null write lock and a frozen clock.
    """

    backend: Any
    kb_root: Path
    checkpointer: Any = None
    store: Any = None
    write_lock: Any = NULL_WRITE_LOCK
    scan_queue: Any = None
    flush_sink: Any = None
    clock: Callable[[], date] = lambda: TODAY

    def snapshot(self) -> KbSnapshot:
        return scan(self.kb_root)


def runtime_for(kb_root: Path, *, checkpointer: Any = None) -> FakeRuntime:
    backend = CompositeBackend(
        default=StateBackend(),
        routes={
            SKILLS_MOUNT: FilesystemBackend(packaged_skills_root(), virtual_mode=True),
            KB_MOUNT: FilesystemBackend(kb_root, virtual_mode=True),
        },
    )
    return FakeRuntime(backend=backend, kb_root=kb_root, checkpointer=checkpointer)


class RecordingFactories:
    """Stand-ins for `build_expert`/`build_librarian`, counting builds and capturing kwargs.

    The signatures are the ones `pkb.agents.registry.ExpertFactory` and `LibrarianFactory` declare;
    the real functions are checked against those Protocols statically, and end to end by
    `test_the_real_factories_are_the_defaults_rg1`.
    """

    def __init__(self, *, delay: float = 0.0, real: bool = False) -> None:
        self.expert_calls: list[dict[str, Any]] = []
        self.librarian_calls: list[dict[str, Any]] = []
        self.delay = delay
        self.real = real

    @property
    def builds(self) -> int:
        return len(self.expert_calls) + len(self.librarian_calls)

    def _make(self, label: str) -> Any:
        if self.delay:
            time.sleep(self.delay)
        if self.real:
            return create_deep_agent(model=scripted(says(label)), system_prompt=label, tools=[])
        return types.SimpleNamespace(label=label)

    def expert(
        self,
        kb_root: Path,
        topic_path: str,
        runtime: Any,
        *,
        model: str | BaseChatModel,
        registry: AgentRegistry,
        tools: Sequence[Any] = (),
    ) -> Any:
        self.expert_calls.append(
            {
                "kb_root": kb_root,
                "topic_path": topic_path,
                "runtime": runtime,
                "model": model,
                "registry": registry,
                "tools": list(tools),
            }
        )
        return self._make(f"expert:{topic_path}")

    def librarian(
        self,
        kb_root: Path,
        runtime: Any,
        *,
        model: str | BaseChatModel,
        registry: AgentRegistry,
        tools: Sequence[Any] = (),
    ) -> Any:
        # No `subagents` parameter, deliberately (LB-12): the signature is the assertion, because a
        # roster the Librarian cannot be given is a bypass that cannot be reintroduced by accident.
        self.librarian_calls.append(
            {
                "kb_root": kb_root,
                "runtime": runtime,
                "model": model,
                "registry": registry,
                "tools": list(tools),
            }
        )
        return self._make("librarian")


def registry_for(kb_root: Path, **kwargs: Any) -> tuple[AgentRegistry, RecordingFactories]:
    factories = RecordingFactories(delay=kwargs.pop("delay", 0.0), real=kwargs.pop("real", False))
    registry = AgentRegistry(
        kb_root,
        kwargs.pop("runtime", runtime_for(kb_root)),
        expert_factory=factories.expert,
        librarian_factory=factories.librarian,
        **kwargs,
    )
    return registry, factories


def index_ids(kb_root: Path) -> list[str]:
    """The agent ids the generated root catalog renders, top to bottom (RG-9, RG-15)."""
    return BACKTICKED.findall((kb_root / "index.md").read_text(encoding="utf-8"))


def index_lines(kb_root: Path) -> dict[str, str]:
    """Each root-catalog line, keyed by the agent id it renders."""
    lines = {}
    for line in (kb_root / "index.md").read_text(encoding="utf-8").splitlines():
        found = BACKTICKED.findall(line)
        if found:
            lines[found[0]] = line
    return lines


def write_topic(kb_root: Path, name: str, *, description: str) -> None:
    """Rewrite a topic's `topic.md` description and refresh the derived files."""
    path = kb_root / name / "topic.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        re.sub(r"^description: .*$", f'description: "{description}"', text, count=1, flags=re.M),
        encoding="utf-8",
    )
    regenerate_all(kb_root)


@pytest.fixture
def big_kb(tmp_path: Path) -> Path:
    """Fifty topic roots — the fixture RG-4 and RG-8 are stated against."""
    root = tmp_path / "BigKnowledgeBase"
    root.mkdir()
    for index in range(50):
        scaffold_topic(
            root,
            f"Topic{index:02d}",
            title=f"Topic {index:02d}",
            description=f"Everything about subject {index:02d}",
            today=TODAY,
        )
    return root


# --------------------------------------------------------------------------------------
# RG-1 … RG-3 — the catalog
# --------------------------------------------------------------------------------------


def test_exactly_one_librarian_and_one_expert_per_topic_root_rg1(kb: Path) -> None:
    registry, factories = registry_for(kb)
    snapshot = scan(kb)

    descriptors = registry.list_agents()

    assert len(descriptors) == len(snapshot.topics) + 1
    assert {d.agent_id for d in descriptors} == {LIBRARIAN_AGENT_ID} | {
        agent_id_for(kb, kb / record.path) for record in snapshot.topics.values()
    }
    # Two kinds of agent and no more: every id resolves through one of exactly two factories.
    for descriptor in descriptors:
        registry.get(descriptor.agent_id)
    assert len(factories.librarian_calls) == 1
    assert len(factories.expert_calls) == len(snapshot.topics)


def test_the_real_factories_are_the_defaults_rg1(kb: Path) -> None:
    """No injection: the registry drives the real `build_expert`/`build_librarian` (RG-1, RG-22).

    This is the seam. It compiles a genuine Librarian and a genuine expert through the real
    factories, which is the only way to know that the registry's keyword call and the
    `TopicRecord.path` it hands over still match what those factories expect.
    """
    defaults = inspect.signature(AgentRegistry.__init__).parameters
    assert defaults["expert_factory"].default is build_expert
    assert defaults["librarian_factory"].default is build_librarian

    model = scripted(
        calls(call("task", {"description": "file it", "subagent_type": COOKING}, "d1")),
        says("expert answer"),
        says("librarian answer"),
    )
    # A model *instance* rather than an id string: `build_expert` accepts `str | BaseChatModel`
    # (EX-9), and this is the whole reason the non-live suite can drive a real graph at all.
    registry = AgentRegistry(kb, runtime_for(kb, checkpointer=InMemorySaver()), default_model=model)

    librarian = registry.get(LIBRARIAN_AGENT_ID)
    result = librarian.invoke(
        {"messages": [HumanMessage(content="hello")]}, {"configurable": {"thread_id": "T"}}
    )

    assert result["messages"][-1].content == "librarian answer"
    assert registry.get(COOKING) is not librarian


def test_the_catalog_is_one_scan_and_no_walk_of_our_own_rg2(
    kb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pkb.agents.registry as registry_module

    seen: list[Path] = []
    real_scan = registry_module.scan

    def counting_scan(kb_root: Path) -> Any:
        seen.append(kb_root)
        return real_scan(kb_root)

    monkeypatch.setattr(registry_module, "scan", counting_scan)
    registry, _ = registry_for(kb)

    registry.list_agents()
    registry.list_agents()
    assert len(seen) == 1, "the catalog is built once and cached"

    registry.invalidate()
    assert len(seen) == 2, "invalidate re-scans, exactly once"

    # Discovery is recursive and parent-first: the sub-topic is a catalog entry of its own.
    ids = [d.agent_id for d in registry.list_agents()]
    assert ids.index(COOKING) < ids.index(GRILLING)

    # Layer 1's single walk is the only one anywhere in pkb.agents (RG-2, D-18).
    offenders = {
        path.relative_to(SRC).as_posix()
        for path in (SRC / "agents").rglob("*.py")
        if called_names(path) & {"walk", "rglob", "glob", "iglob"}
    }
    assert offenders == set()


def test_building_the_catalog_compiles_nothing_rg3(
    kb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("catalog construction resolved a prompt source")

    # `resolve_expert`/`resolve_skills` are what a factory calls to find `expert.md` and the
    # SKILL.md chain. If the catalog touched either, boot cost would scale with the tree.
    for target in ("pkb.core.paths", "pkb.core"):
        monkeypatch.setattr(f"{target}.resolve_expert", refuse)
        monkeypatch.setattr(f"{target}.resolve_skills", refuse)

    registry, factories = registry_for(kb)
    registry.list_agents()
    registry.invalidate()
    registry.list_agents()

    assert factories.builds == 0
    assert "create_deep_agent" not in called_names(REGISTRY_PATH)


# --------------------------------------------------------------------------------------
# RG-4 … RG-8 — laziness, concurrency, and the two access paths
# --------------------------------------------------------------------------------------


def test_graphs_are_built_lazily_and_cached_rg4(big_kb: Path) -> None:
    registry, factories = registry_for(big_kb)

    assert len(registry.list_agents()) == 51
    assert factories.builds == 0, "fifty topics must not mean fifty graphs at boot"

    first = registry.get("topic/topic00")
    assert factories.builds == 1
    second = registry.get("topic/topic00")
    assert factories.builds == 1
    assert first is second


@pytest.mark.asyncio
async def test_concurrent_first_uses_compile_exactly_one_graph_rg5(kb: Path) -> None:
    # A deliberately slow factory: without a lock the ten builds overlap and nine are thrown away,
    # which is the bug — two callers holding different graphs for one agent id.
    registry, factories = registry_for(kb, delay=0.05)

    results = await asyncio.gather(*(asyncio.to_thread(registry.get, COOKING) for _ in range(10)))

    assert len(factories.expert_calls) == 1
    assert all(result is results[0] for result in results)


def test_concurrent_first_uses_are_serialized_under_a_barrier_rg5(kb: Path) -> None:
    """The same rule, with the race forced rather than hoped for."""
    registry, factories = registry_for(kb, delay=0.02)
    start = threading.Barrier(8)
    results: list[Any] = []
    lock = threading.Lock()

    def worker() -> None:
        start.wait(timeout=5)
        graph = registry.get(COOKING)
        with lock:
            results.append(graph)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(factories.expert_calls) == 1
    assert len(results) == 8
    assert all(result is results[0] for result in results)


def test_get_is_the_only_way_to_reach_an_expert_rg6(kb: Path) -> None:
    """RG-6: one compiled graph per topic, and now exactly one door to it.

    A direct conversation, the Librarian's fan-out and a conflict scan all call `get`, so the two
    access paths cannot diverge in configuration by construction rather than by a proxy identity
    check. Invalidation therefore moves every path at once, because there is only one.
    """
    registry, factories = registry_for(kb)

    first = registry.get(COOKING)
    assert registry.get(COOKING) is first
    assert len(factories.expert_calls) == 1

    registry.invalidate()
    rebuilt = registry.get(COOKING)

    assert rebuilt is not first
    assert registry.get(COOKING) is rebuilt
    assert len(factories.expert_calls) == 2


def test_no_expert_is_registered_with_the_librarian_rg7(kb: Path) -> None:
    """RG-7, retired 2026-08-07 (decision F): there is no roster and no `subagents()` accessor.

    Handing the Librarian a `CompiledSubAgent` list is what made delegation a decision the model
    could decline, and measured against a real model it declined: it answered from `grep` output and
    then claimed an expert had checked. The fan-out is code in `pkb.agents.routing` now, resolving
    each expert through `get`, so there is no roster to decline — and no argument through which one
    could be reintroduced by a caller who thinks they are being helpful.

    The reasoning behind the retired rule survives and is why *expert* graphs are compiled objects
    rather than declarative specs: a dict-subagent is recompiled per invocation and cannot hold the
    multi-turn approval dialog README §1.6 requires.
    """
    registry, factories = registry_for(kb)

    assert not hasattr(registry, "subagents")
    registry.get(LIBRARIAN_AGENT_ID)
    assert "subagents" not in factories.librarian_calls[0]
    assert "CompiledSubAgent" not in identifiers(REGISTRY_PATH)


def test_compiling_the_librarian_builds_zero_experts_rg8(big_kb: Path) -> None:
    """RG-8, retired: the lazy proxy is gone, and the property it protected is unchanged.

    The proxy existed to reconcile `CompiledSubAgent` registration with RG-4's laziness. With no
    registration there is nothing to reconcile — fifty topics still mean zero graphs at boot, and a
    fan-out compiles exactly the experts it routed to.
    """
    registry, factories = registry_for(big_kb, real=True)

    registry.get(LIBRARIAN_AGENT_ID)
    assert factories.expert_calls == [], "compiling the Librarian must not compile fifty experts"

    registry.get("topic/topic07")
    registry.get("topic/topic19")

    assert [c["topic_path"] for c in factories.expert_calls] == ["Topic07", "Topic19"]


# --------------------------------------------------------------------------------------
# RG-9 … RG-12 — ids and descriptions
# --------------------------------------------------------------------------------------


def test_every_backticked_id_in_the_root_index_is_a_subagent_key_rg9(kb: Path) -> None:
    registry, _ = registry_for(kb)

    keys = {d.agent_id for d in registry.list_agents() if d.agent_id != LIBRARIAN_AGENT_ID}

    rendered = index_ids(kb)
    assert rendered, "the generated catalog renders the ids the Librarian routes on"
    assert keys == set(rendered)


def test_the_subagent_description_is_the_catalog_line_rg10(kb: Path) -> None:
    # A description carrying markdown-reserved characters is the case where "one string" and
    # "two strings that usually agree" come apart.
    write_topic(kb, "BBQ", description="Smokers, fuel [charcoal] and long cooks")
    registry, _ = registry_for(kb)

    lines = index_lines(kb)
    descriptors = [d for d in registry.list_agents() if d.agent_id != LIBRARIAN_AGENT_ID]
    for entry in descriptors:
        assert entry.description in lines[entry.agent_id]
    assert {d.agent_id: d for d in descriptors}[
        BBQ
    ].description == r"Smokers, fuel \[charcoal\] and long cooks"


def test_agent_ids_come_only_from_layer_one_rg11(tmp_path: Path) -> None:
    root = tmp_path / "KnowledgeBase"
    root.mkdir()
    scaffold_topic(root, "Librarian", title="Librarian", description="Books", today=TODAY)
    registry, _ = registry_for(root)

    ids = [d.agent_id for d in registry.list_agents()]

    assert LIBRARIAN_AGENT_ID in ids
    assert "topic/librarian" in ids
    assert len(set(ids)) == len(ids), "the prefix makes the collision impossible"

    snapshot = scan(root)
    for record in snapshot.topics.values():
        assert record.agent_id == agent_id_for(root, root / record.path)

    # No second implementation of slugification, `sub-topics` elision or id parsing lives here.
    assert "slugify" not in identifiers(REGISTRY_PATH)
    for literal in code_strings(REGISTRY_PATH):
        assert "sub-topics" not in literal
        assert not literal.startswith("topic/")


def test_sub_topics_are_addressable_at_every_depth_rg12(kb: Path) -> None:
    registry, factories = registry_for(kb)

    assert GRILLING in {d.agent_id for d in registry.list_agents()}
    registry.get(GRILLING)
    assert factories.expert_calls[0]["topic_path"] == "Cooking/sub-topics/Grilling"

    # Ids are opaque: only the raw slashed form is accepted, never a re-encoding.
    for reencoded in ("topic%2Fcooking%2Fgrilling", "topic.cooking.grilling", "grilling"):
        with pytest.raises(UnknownAgentError):
            registry.get(reencoded)


# --------------------------------------------------------------------------------------
# RG-13 … RG-15 — descriptors and errors
# --------------------------------------------------------------------------------------


def test_an_unknown_id_is_a_typed_404_rg13(kb: Path) -> None:
    registry, _ = registry_for(kb)

    with pytest.raises(UnknownAgentError) as unknown:
        registry.get("topic/atlantis")
    assert "topic/atlantis" in str(unknown.value)
    assert not isinstance(unknown.value, NotATopicRootError)


def test_a_topic_that_vanished_since_the_scan_is_also_a_404_rg13(kb: Path) -> None:
    """`NotATopicRootError` must not escape: a stale cache read is a 404, never a 500."""
    registry, _ = registry_for(kb)
    registry.list_agents()
    (kb / "BBQ" / "topic.md").unlink()

    with pytest.raises(UnknownAgentError) as unknown:
        registry.get(BBQ)
    assert BBQ in str(unknown.value)
    assert isinstance(unknown.value.__cause__, NotATopicRootError)


def test_a_corrupt_topic_md_still_gets_a_descriptor_rg14(kb: Path) -> None:
    (kb / "BBQ" / "topic.md").write_text("---\ntitle: [unclosed\n---\n\nbody\n", encoding="utf-8")
    registry, _ = registry_for(kb)

    descriptors = {d.agent_id: d for d in registry.list_agents()}

    assert BBQ in descriptors, "a topic is never silently dropped from routing"
    assert descriptors[BBQ].title == "BBQ", "the folder name is the fallback title (GE-25)"
    assert descriptors[BBQ].description

    # No field is typed by a harness class: the whole descriptor crosses a JSON boundary.
    for field in dataclasses.fields(AgentDescriptor):
        assert field.type in {"str", "bool"}
    json.dumps([dataclasses.asdict(d) for d in descriptors.values()])


def test_list_agents_is_the_librarian_then_root_index_order_rg15(kb: Path) -> None:
    registry, _ = registry_for(kb)

    ids = [d.agent_id for d in registry.list_agents()]

    assert ids[0] == LIBRARIAN_AGENT_ID
    assert ids[1:] == index_ids(kb)


# --------------------------------------------------------------------------------------
# RG-16 … RG-19 — invalidation
# --------------------------------------------------------------------------------------


def test_a_new_topic_is_listed_and_routable_after_invalidate_rg16(kb: Path) -> None:
    registry, factories = registry_for(kb, real=True)
    registry.get(LIBRARIAN_AGENT_ID)

    scaffold_topic(kb, "Physics", title="Physics", description="Mechanics", today=TODAY)
    registry.invalidate()

    assert "topic/physics" in {d.agent_id for d in registry.list_agents()}
    # The Librarian's graph is dropped unconditionally. Since LB-12 took the expert roster off it,
    # nothing in that graph names a topic any more and the drop is cheap insurance rather than a
    # correctness requirement — but a rule that says "always" is one nobody has to reason about.
    assert len(factories.librarian_calls) == 1
    registry.get(LIBRARIAN_AGENT_ID)
    assert len(factories.librarian_calls) == 2
    # And it is reachable: the fan-out resolves every id it routes to through `get`, so a topic
    # created mid-session is routable the moment the catalog regenerates.
    assert registry.get("topic/physics") is not None
    assert [c["topic_path"] for c in factories.expert_calls] == ["Physics"]


def test_a_renamed_topic_never_hands_out_its_stale_graph_rg16(kb: Path) -> None:
    registry, _ = registry_for(kb)
    assert registry.get(BBQ) is not None

    (kb / "BBQ").rename(kb / "Barbecue")
    regenerate_all(kb)
    # A *targeted* invalidate, to prove eviction is unconditional rather than a side effect of
    # clearing the whole cache.
    registry.invalidate(COOKING)

    with pytest.raises(UnknownAgentError):
        registry.get(BBQ)
    assert "topic/barbecue" in {d.agent_id for d in registry.list_agents()}


def test_an_expert_md_invalidates_the_whole_subtree_rg17(kb: Path) -> None:
    registry, factories = registry_for(kb)
    for agent_id in (COOKING, GRILLING, BBQ):
        registry.get(agent_id)
    assert len(factories.expert_calls) == 3

    (kb / "Cooking" / "expert.md").write_text("You are a chef.\n", encoding="utf-8")
    registry.invalidate(COOKING)

    registry.get(BBQ)
    assert len(factories.expert_calls) == 3, "an unrelated topic keeps its graph"
    registry.get(COOKING)
    registry.get(GRILLING)
    assert len(factories.expert_calls) == 5, "the topic and its descendants are both rebuilt"
    assert {d.agent_id for d in registry.list_agents() if d.has_custom_expert} == {COOKING}


def test_a_topic_description_edit_reaches_the_routing_view_rg17(kb: Path) -> None:
    registry, _ = registry_for(kb)
    before = {d.agent_id: d.description for d in registry.list_agents()}

    write_topic(kb, "BBQ", description="Offset smokers, lump charcoal, and long cooks")
    registry.invalidate()

    after = {d.agent_id: d.description for d in registry.list_agents()}
    assert after[BBQ] != before[BBQ]
    assert after[BBQ] == "Offset smokers, lump charcoal, and long cooks"
    assert after[BBQ] in index_lines(kb)[BBQ]


def test_a_running_thread_cannot_see_a_new_skill_rg18(tmp_path: Path) -> None:
    """RG-18 is a harness property, not something `invalidate` can fix.

    deepagents' `SkillsMiddleware.before_agent` returns early whenever `skills_metadata` is already
    in state, and that key is checkpointed — so the skill set a thread saw on its first turn is the
    skill set it keeps. Pinned here rather than merely documented, so a deepagents bump that changes
    the caching shows up as a failing test next to the docstring that promises it.
    """
    skills = tmp_path / "skills"
    (skills / "voice").mkdir(parents=True)
    (skills / "voice" / "SKILL.md").write_text(
        "---\nname: voice\ndescription: Use when writing in the human's own words.\n---\n\nBody.\n",
        encoding="utf-8",
    )
    model = scripted(says("one"), says("two"), says("three"))
    agent = create_deep_agent(
        model=model,
        system_prompt="sys",
        tools=[],
        backend=FilesystemBackend(tmp_path, virtual_mode=True),
        skills=[SKILLS_MOUNT],
        checkpointer=InMemorySaver(),
    )
    thread = {"configurable": {"thread_id": "T"}}
    agent.invoke({"messages": [HumanMessage(content="a")]}, thread)

    (skills / "gardening").mkdir()
    (skills / "gardening" / "SKILL.md").write_text(
        "---\nname: gardening\ndescription: Use when pruning tomatoes.\n---\n\nBody.\n",
        encoding="utf-8",
    )

    agent.invoke({"messages": [HumanMessage(content="b")]}, thread)
    assert "gardening" not in str(model.system_prompts[-1])

    agent.invoke({"messages": [HumanMessage(content="c")]}, {"configurable": {"thread_id": "U"}})
    assert "gardening" in str(model.system_prompts[-1])

    # And the constraint is written where the next reader will look for it.
    module_doc = sys.modules[AgentRegistry.__module__].__doc__ or ""
    assert "RG-18" in module_doc
    assert "checkpoint" in module_doc


def test_the_registry_never_writes_to_the_tree_rg19(kb: Path) -> None:
    def reading_expert(
        kb_root: Path,
        topic_path: str,
        runtime: Any,
        *,
        model: str,
        registry: AgentRegistry,
        tools: Sequence[Any] = (),
    ) -> Any:
        # What a real factory reads: the prompt source and the skill chain, nothing else.
        expert = resolve_expert(kb_root, kb_root / topic_path)
        if expert is not None:
            expert.read_text(encoding="utf-8")
        resolve_skills(kb_root, kb_root / topic_path)
        return types.SimpleNamespace(label=topic_path)

    (kb / "Cooking" / "expert.md").write_text("You are a chef.\n", encoding="utf-8")
    (kb / "skills" / "voice").mkdir(parents=True)
    (kb / "skills" / "voice" / "SKILL.md").write_text(
        "---\nname: voice\ndescription: Use when writing.\n---\n\nBody.\n", encoding="utf-8"
    )
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(kb.rglob("*"))
        if path.is_file()
    }

    registry = AgentRegistry(
        kb,
        runtime_for(kb),
        expert_factory=reading_expert,
        librarian_factory=lambda *args, **kwargs: types.SimpleNamespace(),
    )
    for descriptor in registry.list_agents():
        registry.get(descriptor.agent_id)
    registry.invalidate()
    registry.list_agents()

    after = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(kb.rglob("*"))
        if path.is_file()
    }
    assert after == before


# --------------------------------------------------------------------------------------
# RG-20 … RG-22 — the shape of the surface
# --------------------------------------------------------------------------------------


def test_the_public_surface_is_thread_free_and_delegation_free_rg20() -> None:
    public = {
        name
        for name, _ in inspect.getmembers(AgentRegistry, predicate=inspect.isfunction)
        if not name.startswith("_")
    }

    # `chat_model_for` is RG-21's answer to a third consumer: the ingestion loop needs the same
    # model-with-failover the two factories get, and reaching for `init_chat_model` itself is how it
    # became the only path in the system with no fallback. `subagents` retired: RG-7.
    assert public == {"list_agents", "get", "invalidate", "chat_model_for"}
    for name in public:
        parameters = set(inspect.signature(getattr(AgentRegistry, name)).parameters)
        assert not parameters & {"thread_id", "run_id"}
    assert not identifiers(REGISTRY_PATH) & {"thread_id", "run_id", "threads_for_delegation"}
    assert not code_strings(REGISTRY_PATH) & {"thread_id", "run_id"}


def test_the_model_is_a_registry_concern_rg21(kb: Path) -> None:
    registry, factories = registry_for(
        kb, models={COOKING: OVERRIDE_MODEL}, default_model=DEFAULT_MODEL
    )

    descriptors = {d.agent_id: d for d in registry.list_agents()}
    assert descriptors[COOKING].model_id == OVERRIDE_MODEL
    assert descriptors[BBQ].model_id == DEFAULT_MODEL
    assert descriptors[LIBRARIAN_AGENT_ID].model_id == DEFAULT_MODEL

    registry.get(COOKING)
    registry.get(BBQ)
    registry.get(LIBRARIAN_AGENT_ID)
    # The per-agent override still reaches the factory; what changed is that a configured fallback
    # makes the model an object rather than a spec string, and the object names its own primary.
    assert [c["model"].primary_id for c in factories.expert_calls] == [
        OVERRIDE_MODEL,
        DEFAULT_MODEL,
    ]
    assert factories.librarian_calls[0]["model"].primary_id == DEFAULT_MODEL

    # `model=None` is deprecated and silently falls back to deepagents' own default, shadowing
    # whatever the deployment configured — so no call site may pass it.
    offenders = [
        path.relative_to(SRC).as_posix() for path in SRC.rglob("*.py") if passes_model_none(path)
    ]
    assert offenders == []


def test_every_agent_carries_the_configured_fallback_rg21(kb: Path) -> None:
    """A failover is registry configuration, so no agent may be left without one."""
    registry, factories = registry_for(kb, models={COOKING: OVERRIDE_MODEL})

    for descriptor in registry.list_agents():
        registry.get(descriptor.agent_id)

    handed = [call["model"] for call in factories.expert_calls + factories.librarian_calls]
    assert handed, "the fixture must compile at least one graph"
    for model in handed:
        assert isinstance(model, FallbackChatModel)
        assert model.fallback_id == DEFAULT_FALLBACK_MODEL


def test_the_fallback_is_overridable_and_disableable_rg21(kb: Path) -> None:
    """Both models are deployment configuration; ``None`` opts out of the failover entirely."""
    registry, factories = registry_for(kb, fallback_model=OVERRIDE_MODEL)
    registry.get(COOKING)
    assert factories.expert_calls[0]["model"].fallback_id == OVERRIDE_MODEL

    # Disabled, the registry hands over exactly what it was configured with — a graph compiled this
    # way is indistinguishable from one compiled before the failover existed.
    off, off_factories = registry_for(kb, fallback_model=None)
    off.get(COOKING)
    assert off_factories.expert_calls[0]["model"] == DEFAULT_MODEL


def test_compiling_a_graph_resolves_no_model_rg3_rg21(kb: Path) -> None:
    """Neither model is constructed by ``get``: RG-3/RG-4 make first *use* pay, not first compile.

    This is what keeps a provider SDK — and the credentials or the 20GB local download it wants —
    out of the compile path. The spec here is deliberately unresolvable, so any attempt to build it
    raises rather than passing silently.
    """
    registry, factories = registry_for(kb, default_model="not-a-real-provider:nope")

    registry.get(COOKING)

    model = factories.expert_calls[0]["model"]
    assert isinstance(model, FallbackChatModel)
    assert model.primary_id == "not-a-real-provider:nope"
    with pytest.raises(ValueError, match="Unable to infer model provider"):
        _ = model.primary


def test_create_deep_agent_is_called_from_exactly_two_places_rg22() -> None:
    callers = {
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if "create_deep_agent" in called_names(path)
    }

    assert callers == {"agents/expert.py", "agents/librarian.py"}


def test_the_tool_hook_reaches_both_factories_lb7_ex12(kb: Path) -> None:
    """`create_topic` (LB-7) and `create_subtopic` (EX-12) are per-agent, so the hook takes an id."""
    seen: list[str] = []

    def tool_factory(agent_id: str) -> Sequence[Any]:
        seen.append(agent_id)
        return [f"tool-for-{agent_id}"]

    registry, factories = registry_for(kb, tool_factory=tool_factory)
    registry.get(LIBRARIAN_AGENT_ID)
    registry.get(COOKING)

    assert seen == [LIBRARIAN_AGENT_ID, COOKING]
    assert factories.librarian_calls[0]["tools"] == [f"tool-for-{LIBRARIAN_AGENT_ID}"]
    assert factories.expert_calls[0]["tools"] == [f"tool-for-{COOKING}"]


def test_an_empty_knowledge_base_registers_only_the_librarian_lb6(empty_kb: Path) -> None:
    """LB-6's registry half: bootstrapping starts with zero topics and must still work."""
    registry, factories = registry_for(empty_kb)

    assert [d.agent_id for d in registry.list_agents()] == [LIBRARIAN_AGENT_ID]
    registry.get(LIBRARIAN_AGENT_ID)
    assert len(factories.librarian_calls) == 1
    assert "subagents" not in factories.librarian_calls[0]  # LB-12
