"""`build_librarian` — LB-1 … LB-13: the graph that performs the classification step.

The Librarian used to be interesting mainly in relation to the experts, because it held deepagents'
`task` tool and the experts were its subagents. It is not any more, and that is the change: routing
is a harness workflow now, so this file is about the *one model call* the Librarian still makes and
about the two things the harness does to it — end the run at the routing decision, and force one
retry when the model answers in prose instead. The fan-out, the merge and the offer are code, and
they are tested where they live, in `test_routing.py`, against a real runtime driving real experts.

What remains here is everything that is true of the compiled graph itself: its identity, its
knowledge-base-independent prompt, the catalog it routes on, the fact that it carries nothing
topic-scoped and cannot write, its gated `create_topic`, and — new — that it can no longer reach an
expert through a tool at all (LB-12).

The harness (`FakeRuntime`, `system_text`, `captured`, …) is imported from `test_expert` rather than
duplicated: two copies of a fake runtime is exactly the kind of drift that makes one suite pass while
the other tests something else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.tools import BaseTool, tool
from langgraph.types import Command
from pydantic import Field

from pkb.agents import librarian as librarian_module
from pkb.agents.librarian import build_librarian, librarian_prompt
from pkb.agents.middleware.breadth import BLOCK_OPEN
from pkb.agents.paths import KB_MOUNT, SKILLS_MOUNT, to_backend_path
from pkb.agents.permissions import kb_permissions
from pkb.agents.routing import (
    MAX_ROUTE_ATTEMPTS,
    RETRY_INSTRUCTION,
    ROUTE_ACK,
    ROUTE_ATTEMPTS,
    ROUTE_DECISION,
    ROUTE_TOOL,
    read_decision,
)
from pkb.core import ScaffoldResult, regenerate_all, scaffold_topic
from pkb.core.paths import LIBRARIAN_AGENT_ID
from tests.agents.conftest import TODAY, ScriptedChatModel, call, calls, says, scripted
from tests.agents.test_expert import (
    BBQ,
    COOKING,
    NO_FRONTMATTER,
    FakeRuntime,
    breadth_block,
    build,
    captured,
    config,
    middleware_names,
    run,
    system_text,
    write_note,
)

COOKING_AGENT = "topic/cooking"
BBQ_AGENT = "topic/bbq"

COOKING_NOTE_REL = "Cooking/notes/reverse-sear.md"

COOKING_NOTE = """---
title: "Reverse sear"
description: "Low oven then a hot pan for an even steak crust"
topic: "Cooking"
tags:
  - topic.cooking
  - type.note
  - status.draft
created: 2026-07-01
updated: 2026-07-01
source_type: note
---

# Reverse sear

Start the steak in a low oven, then finish it in a screaming-hot pan.
"""


# --------------------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------------------


class ToolRecordingModel(ScriptedChatModel):
    """A scripted model that also records the tool suite it was offered each call.

    `create_agent` calls `bind_tools` on every model call (`_get_bound_model`), so this is the only
    place the *model's own view* of its capabilities is observable — which is exactly what LB-12 is
    about. Asserting on the graph's tool node instead would prove the wrong thing: `task` is still
    registered there, and cannot be removed without a process-global harness profile.
    """

    bound: list[list[str]] = Field(default_factory=list)

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        self.bound.append([str(getattr(item, "name", item)) for item in tools])
        return self


def recording(*script: Any) -> ToolRecordingModel:
    return ToolRecordingModel(script=list(script), idx=0, calls=[], bound=[])


def librarian(
    kb: Path,
    model: ScriptedChatModel,
    *,
    runtime: FakeRuntime | None = None,
    tools: list[BaseTool] | None = None,
) -> Any:
    return build_librarian(kb, runtime or FakeRuntime(kb), model=model, tools=tools or [])


def routes(*topic_ids: str, reason: str = "it is about these", id_: str = "r1") -> Any:
    return calls(call(ROUTE_TOOL, {"topic_ids": list(topic_ids), "reason": reason}, id_))


def make_create_topic(kb: Path, log: list[ScaffoldResult]) -> BaseTool:
    """A stand-in for `pkb.agents.tools.topics.create_topic` (LB-7).

    That module is another agent's file. What `build_librarian` owns is the *wiring*: the gate table
    is keyed on the tool name, so a tool called `create_topic` interrupts before it ever runs, and it
    scaffolds through Layer 1 only once the human has approved. A rename on either side silently
    un-gates topic creation, which is what this test is really pinning.
    """

    @tool
    def create_topic(name: str, title: str, description: str) -> str:
        """Create a new topic at the knowledge base root."""
        result = scaffold_topic(kb, name, title=title, description=description, today=TODAY)
        log.append(result)
        return f"created {result.topic_path}"

    return create_topic


# --------------------------------------------------------------------------------------
# LB-1, LB-3 — identity and a knowledge-base-independent prompt
# --------------------------------------------------------------------------------------


def test_the_librarian_is_a_compiled_agent_with_its_own_id_lb1(kb: Path) -> None:
    """LB-1: a deep agent in its own right, not a dispatcher function in front of the experts.

    Still true after routing moved into code, and deliberately so: the classification needs the
    catalog, the skills mount, the gates and the flush, and a bare model call would have none of
    them. So the demonstration that it runs is a *routing* turn, which is what a Librarian turn is.
    """
    graph = librarian(kb, scripted(routes(COOKING_AGENT)))

    assert graph.name == LIBRARIAN_AGENT_ID
    state = run(graph, thread="t-lb1")
    assert state["messages"][-1].content == ROUTE_ACK
    assert read_decision(state) is not None


def test_the_librarian_prompt_is_kb_independent_lb3(kb: Path, tmp_path: Path) -> None:
    """LB-3: no topic names, no descriptions, no per-topic instructions — byte-identical across KBs.

    The routing view is the *generated* root `index.md`, which arrives as context each turn; nothing
    about routing is maintained by hand, so nothing about it can go stale. With the experts no longer
    registered as subagents (LB-12), that catalog is now the *only* route by which a topic
    description reaches the model — it used to also arrive inside the `task` tool description.
    """
    other = tmp_path / "Other"
    other.mkdir()
    scaffold_topic(other, "Astronomy", title="Astronomy", description="Stars", today=TODAY)

    first, second = scripted(says("a")), scripted(says("b"))
    run(librarian(kb, first), thread="t-a")
    run(librarian(other, second), thread="t-b")

    prompt = librarian_prompt()
    assert system_text(first).index(prompt) == system_text(second).index(prompt) == 0
    assert "Cooking" not in prompt
    assert "Astronomy" not in prompt


# --------------------------------------------------------------------------------------
# LB-4, LB-5 — what the Librarian carries
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_the_root_catalog_is_in_context_every_turn_lb4(kb: Path) -> None:
    """LB-4: root `index.md` is bounded (GE-12) so it is loaded; root `tags.md` is read on demand.

    Superseded by Phase 1's Task 6: there is no root ``index.md`` any more (T-37), so the breadth
    block now renders ``<file path="/kb/index.md" note="not present" />`` and carries no agent id at
    all — see the same reasoning on ``test_the_librarian_block_is_the_root_catalog_only_lb4`` in
    ``test_breadth_middleware.py``. Phase 3 rebuilds the Librarian's routing view against DESIGN.md;
    it is not a one-line repoint to ``tags.md`` (the registry is deliberately unbounded, unlike the
    8 KB root catalog this test's own docstring cites).
    """
    regenerate_all(kb)
    model = scripted(says("routed"))
    run(librarian(kb, model), thread="t-lb4")

    prompt = system_text(model)
    block = breadth_block(prompt)
    assert to_backend_path("index.md") in block
    assert COOKING_AGENT in block  # the generated catalog renders each topic's agent id
    # Unbounded, so it is named in the prompt rather than injected (GE-19).
    assert to_backend_path("tags.md") in prompt
    assert to_backend_path("tags.md") not in block


def test_the_librarian_carries_nothing_topic_scoped_lb5(kb: Path) -> None:
    """LB-5/RT-16: it goes wide — no topic breadth, no topic skills, and no write capability.

    Loading a topic's breadth files here would defeat README §1.1 goal 2, and a note the Librarian
    filed itself would be a note written without the topic's skills, voice overload and `expert.md`
    behaviour — the whole reason the expert exists, and the whole reason routing had to stop being
    optional.
    """
    model = scripted(says("routed"))
    with captured(librarian_module) as seen:
        run(librarian(kb, model), thread="t-lb5")

    kwargs = seen[0]
    assert kwargs["skills"] == [SKILLS_MOUNT]
    assert kwargs["permissions"] == kb_permissions(None)
    assert middleware_names(kwargs) == [
        "RouteMiddleware",
        "KbBreadthMiddleware",
        "KbValidationMiddleware",
        "KbMaintenanceMiddleware",
    ]

    block = breadth_block(system_text(model))
    assert to_backend_path(COOKING) not in block
    assert to_backend_path(BBQ) not in block


def test_the_librarian_cannot_write_into_the_tree_rt16(kb: Path) -> None:
    """RT-16: every `write_file` under `/kb/**` from the Librarian is refused at the tool layer."""
    model = scripted(write_note(f"{KB_MOUNT}{COOKING_NOTE_REL}", COOKING_NOTE, "w1"), says("nope"))
    result = run(librarian(kb, model), thread="t-rt16")

    assert any(getattr(m, "status", None) == "error" for m in result["messages"])
    assert not (kb / COOKING_NOTE_REL).exists()


# --------------------------------------------------------------------------------------
# LB-6 — bootstrapping
# --------------------------------------------------------------------------------------


def test_an_empty_knowledge_base_still_compiles_a_working_librarian_lb6(empty_kb: Path) -> None:
    """LB-6: zero topics is the *first* state of every knowledge base, not a degenerate one.

    Every inbound item is then a topic gap, and the Librarian is the agent that proposes filling it.
    """
    regenerate_all(empty_kb)
    # `route` with an empty list is the honest classification over an empty catalog: nothing fits,
    # because nothing exists. The runtime turns that into the topic-creation flow (LB-7).
    model = scripted(routes(reason="the catalog is empty"))
    with captured(librarian_module) as seen:
        graph = librarian(empty_kb, model)
        result = run(graph, thread="t-lb6")

    assert [spec["name"] for spec in seen[0]["subagents"]] == ["general-purpose"]
    assert seen[0]["skills"] == [SKILLS_MOUNT]
    decision = read_decision(result)
    assert decision is not None and decision.topic_ids == ()
    assert BLOCK_OPEN in system_text(model)


# --------------------------------------------------------------------------------------
# LB-7 — topic creation stays here, and stays gated
# --------------------------------------------------------------------------------------


def test_topic_creation_is_gated_and_then_scaffolds_through_layer_one_lb7(kb: Path) -> None:
    """LB-7: propose → interrupt → `scaffold_topic` under the lock. Nothing is created unattended.

    Topic creation is an agent-invoked tool rather than a transport endpoint — all interactions are
    agent-mediated — and `pkb.core.scaffold_topic` carries no gate of its own (SC-8), so this is the
    only thing standing between a model's idea and six new files. It stays on the Librarian because a
    gap is what the Librarian is uniquely placed to notice: it is the one agent that sees the whole
    catalog and nothing else.
    """
    log: list[ScaffoldResult] = []
    model = scripted(
        calls(
            call(
                "create_topic",
                {"name": "Physics", "title": "Physics", "description": "Mechanics and optics"},
                "t1",
            )
        ),
        says("created it"),
    )
    graph = librarian(kb, model, tools=[make_create_topic(kb, log)])
    run(graph, thread="t-lb7")

    state = graph.get_state(config("t-lb7"))
    assert len(state.interrupts) == 1
    assert state.interrupts[0].value["action_requests"][0]["name"] == "create_topic"
    assert state.interrupts[0].value["review_configs"][0]["allowed_decisions"] == [
        "approve",
        "edit",
        "reject",
    ]
    assert log == []
    assert not (kb / "Physics").exists()

    graph.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config("t-lb7"))

    assert len(log) == 1
    assert len(log[0].created) == 6
    assert (kb / "Physics" / "topic.md").is_file()
    assert (kb / "Physics" / "notes" / "summary.md").is_file()


def test_a_rejected_topic_proposal_creates_nothing_lb7(kb: Path) -> None:
    """LB-7: `reject` is a real answer — the human declines and the tree is untouched."""
    log: list[ScaffoldResult] = []
    model = scripted(
        calls(call("create_topic", {"name": "Physics", "title": "P", "description": "d"}, "t2")),
        says("understood"),
    )
    graph = librarian(kb, model, tools=[make_create_topic(kb, log)])
    run(graph, thread="t-lb7-reject")
    graph.invoke(
        Command(resume={"decisions": [{"type": "reject", "message": "too broad"}]}),
        config("t-lb7-reject"),
    )

    assert log == []
    assert not (kb / "Physics").exists()


# --------------------------------------------------------------------------------------
# LB-12 — the Librarian cannot delegate, and cannot decline to route
# --------------------------------------------------------------------------------------


def test_no_expert_is_registered_as_a_subagent_lb12(kb: Path) -> None:
    """LB-12: the roster is gone, so `task` has nowhere to route even if it were offered.

    Registering the experts here is what made delegation a choice, and measured against a real model
    the choice went the wrong way: it answered from `grep` output and then claimed an expert had
    checked. There is now no `subagents` parameter at all — the factory cannot be handed a roster by
    a caller who thinks they are being helpful.
    """
    import inspect

    with captured(librarian_module) as seen:
        librarian(kb, scripted(says("ok")))

    assert [spec["name"] for spec in seen[0]["subagents"]] == ["general-purpose"]
    assert "subagents" not in inspect.signature(build_librarian).parameters


def test_the_model_is_never_offered_the_task_tool_lb12(kb: Path) -> None:
    """LB-12: `task` is withheld from every model request, and `route` takes its place.

    The tool cannot be removed from the graph: deepagents auto-adds a `general-purpose` subagent to
    every deep agent and therefore always registers `task`, and suppressing that needs a
    process-global harness profile keyed by model id — the same objection that sank Q7-b and Q8-b.
    Withholding it from the *request* is per-graph and achieves the thing that matters, since a tool
    the model is never shown is a tool the model cannot call.
    """
    model = recording(routes(COOKING_AGENT))
    run(librarian(kb, model), thread="t-lb12")

    assert model.bound, "create_agent binds tools on every model call"
    for offered in model.bound:
        assert "task" not in offered
        assert ROUTE_TOOL in offered

    # And the withholding is the Librarian's alone: an expert keeps `task`, because delegating to
    # its own general-purpose subagent is the one place the tool still belongs.
    expert_model = recording(says("nothing to file"))
    run(build(kb, expert_model, COOKING), thread="t-lb12-expert")
    assert expert_model.bound
    assert all("task" in offered for offered in expert_model.bound)


def test_the_run_ends_at_the_route_call_lb12(kb: Path) -> None:
    """LB-12: classification is *one* model call, ended by the harness at the routing decision.

    The second model call is the dangerous one: with the experts not yet run, its only possible
    output is an answer about work that has not happened — which is precisely the fabricated *"The
    Cooking expert checked the knowledge base"* that was observed. So `RouteMiddleware.after_model`
    records the decision, answers the tool call itself and jumps to `end`, which resolves to
    `exit_node` and therefore still runs the maintenance flush (MW-15, MW-16).
    """
    model = scripted(routes(COOKING_AGENT, BBQ_AGENT, reason="steak and fuel"), says("unreached"))
    graph = librarian(kb, model)
    run(graph, thread="t-lb12b")

    assert len(model.calls) == 1, "no second model call"
    state = graph.get_state(config("t-lb12b"))
    decision = read_decision(state.values)
    assert decision is not None
    assert decision.topic_ids == (COOKING_AGENT, BBQ_AGENT)
    assert decision.reason == "steak and fuel"
    # The tool call is answered, or the next turn is rejected by a real provider for a dangling call.
    assert state.values["messages"][-1].content == ROUTE_ACK
    assert not state.next, "the run ended normally and the thread is resumable"


def test_a_route_call_batched_with_another_tool_does_not_end_the_run_lb12(kb: Path) -> None:
    """The one case the middleware leaves alone: ending there would strand the sibling call.

    An `AIMessage` whose `tool_calls` carry no matching `ToolMessage` is rejected by real providers
    on the next turn, so "the thread stays resumable" would stop being true. The decision is recorded
    anyway, and the tools node runs `route` normally.
    """
    model = scripted(
        calls(
            call(ROUTE_TOOL, {"topic_ids": [COOKING_AGENT], "reason": "steak"}, "r1"),
            call("ls", {"path": f"{KB_MOUNT}Cooking"}, "l1"),
        ),
        says("done"),
    )
    graph = librarian(kb, model)
    run(graph, thread="t-lb12c")

    state = graph.get_state(config("t-lb12c"))
    decision = read_decision(state.values)
    assert decision is not None and decision.topic_ids == (COOKING_AGENT,)
    assert len(model.calls) == 2, "the sibling call ran, so the model got its turn"
    assert ROUTE_ACK in [
        message.content for message in state.values["messages"] if message.type == "tool"
    ]


# --------------------------------------------------------------------------------------
# LB-13 — one forced retry, then the human
# --------------------------------------------------------------------------------------


def test_prose_is_sent_back_to_the_model_exactly_once_lb13(kb: Path) -> None:
    """LB-13: the retry is forced by the harness, not requested by the prompt.

    A prompt can ask a model to call a tool; only the graph can make it try again. The nudge is a
    `HumanMessage` and therefore shows up in the thread's replayed history on purpose — the turn cost
    two model calls, and hiding the reason would make that an unexplained pause.
    """
    model = scripted(says("I looked it up and the answer is 52 degrees."), routes(COOKING_AGENT))
    graph = librarian(kb, model)
    run(graph, thread="t-lb13")

    assert len(model.calls) == 2
    assert RETRY_INSTRUCTION in str(model.calls[1][-1].content)
    state = graph.get_state(config("t-lb13"))
    decision = read_decision(state.values)
    assert decision is not None and decision.topic_ids == (COOKING_AGENT,)


def test_a_second_prose_answer_ends_the_run_without_a_decision_lb13(kb: Path) -> None:
    """LB-13/LB-19: a model that will not route is not persuaded by a third phrasing.

    The run ends with the model's own words and no decision, and the runtime turns that into the
    menu the human chooses from — never a guess, because a guess files knowledge in the wrong topic
    and there is no undo.
    """
    model = scripted(says("Probably 52 degrees."), says("Still 52 degrees."), says("unreached"))
    graph = librarian(kb, model)
    run(graph, thread="t-lb13b")

    assert len(model.calls) == MAX_ROUTE_ATTEMPTS + 1
    state = graph.get_state(config("t-lb13b"))
    assert read_decision(state.values) is None
    assert state.values[ROUTE_ATTEMPTS] == MAX_ROUTE_ATTEMPTS
    assert not state.next


def test_the_decision_is_cleared_at_the_start_of_every_turn_lb13(kb: Path) -> None:
    """LB-13: state is checkpointed, so turn 2 would otherwise fan out turn 1's classification.

    `before_agent` clearing both keys is what removes the need for any consumption bookkeeping in the
    runtime: a decision found after a run is necessarily that run's.
    """
    model = scripted(routes(COOKING_AGENT), says("no route this time"), says("still none"))
    graph = librarian(kb, model)
    run(graph, thread="t-lb13c")
    assert read_decision(graph.get_state(config("t-lb13c")).values) is not None

    run(graph, thread="t-lb13c", text="something else")

    state = graph.get_state(config("t-lb13c"))
    assert state.values[ROUTE_DECISION] is None
    assert read_decision(state.values) is None


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"topic_ids": [COOKING_AGENT], "reason": "r"}, (COOKING_AGENT,)),
        ({"topic_ids": COOKING_AGENT, "reason": "r"}, (COOKING_AGENT,)),
        ({"topic_ids": f"{COOKING_AGENT}, {BBQ_AGENT}", "reason": "r"}, (COOKING_AGENT, BBQ_AGENT)),
        ({"topic_ids": [], "reason": "none fit"}, ()),
        ({"reason": "forgot the list"}, ()),
    ],
)
def test_the_route_arguments_survive_what_a_small_model_does_to_them(
    kb: Path, args: dict[str, Any], expected: tuple[str, ...]
) -> None:
    """A bare string, a comma-joined string or a missing list are accepted; a refusal costs a turn.

    An id that does not exist is deliberately *not* corrected here — the runtime reports it to the
    human in the reply, because silently dropping a name the model chose hides a routing fault.
    """
    graph = librarian(kb, scripted(calls(call(ROUTE_TOOL, args, "r1"))))
    run(graph, thread="t-args")

    decision = read_decision(graph.get_state(config("t-args")).values)
    assert decision is not None
    assert decision.topic_ids == expected


# --------------------------------------------------------------------------------------
# EX-11 on the Librarian's own delegated path
# --------------------------------------------------------------------------------------


def test_the_librarians_general_purpose_subagent_is_also_guarded_ex11(kb: Path) -> None:
    """EX-11/D-2: the auto-added subagent is on *every* deep agent, the Librarian included.

    The Librarian's own permissions deny writes under `/kb/**`, which the auto-added subagent does
    inherit — but validation and the flush are what the explicit spec restores, and the Librarian is
    the graph a channel connects to by default. It remains reachable through `task` from *within*
    the graph even though the model is not offered that tool (LB-12), which is why the guard stays.
    """
    model = scripted(
        calls(call("task", {"description": "file it", "subagent_type": "general-purpose"}, "g1")),
        write_note(f"{KB_MOUNT}{COOKING_NOTE_REL}", NO_FRONTMATTER, "g2"),
        says("refused"),
        says("I could not file it"),
    )
    with captured(librarian_module) as seen:
        run(librarian(kb, model), thread="t-lb-gp")

    specs = seen[0]["subagents"]
    assert [spec["name"] for spec in specs] == ["general-purpose"]
    assert [m.name for m in specs[0]["middleware"]] == [
        "KbValidationMiddleware",
        "KbMaintenanceMiddleware",
    ]
    assert not (kb / COOKING_NOTE_REL).exists()
