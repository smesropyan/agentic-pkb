"""`build_librarian` — LB-1 … LB-11, plus the delegation half of EX-10/EX-11 and RG-7.

The Librarian is only interesting in relation to the experts, so most of this file compiles a real
Librarian *and* real expert graphs over one fixture knowledge base and delegates between them. Three
harness facts make that testable without a key, and each of them shaped the tests:

* An expert registered as a `CompiledSubAgent` is a separate compiled graph with **its own model**,
  so each agent in a delegation test gets its own `ScriptedChatModel` and the scripts are
  order-independent. (The shared-instance rule in `conftest` applies to *declarative* subagents,
  which inherit the parent's model — that is why the `general-purpose` tests script one model.)
* Delegated work checkpoints under the **parent's** `thread_id` in a nested `checkpoint_ns` (D-6),
  and an interrupt raised inside the delegate is resolvable on the parent's thread (LB-10).
* Both graphs run `after_agent`, so a delegated turn flushes twice — which is safe only because the
  touched-path key is `PrivateStateAttr` and the parent's flush is therefore a genuine no-op (LB-11).

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

from pkb.agents import librarian as librarian_module
from pkb.agents.librarian import build_librarian, librarian_prompt
from pkb.agents.middleware.breadth import BLOCK_OPEN
from pkb.agents.paths import KB_MOUNT, SKILLS_MOUNT, to_backend_path
from pkb.agents.permissions import kb_permissions
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
BBQ_NOTE_REL = "BBQ/notes/charcoal-chimney.md"

# `updated` is deliberately *older* than the injected today, so the flush has an `updated` line to
# rewrite and `FlushReport.stamped` says something. A note already stamped today is stamped by
# nobody, and LB-11's assertion would then pass for the wrong reason.
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

BBQ_NOTE = """---
title: "Charcoal chimney"
description: "Lighting charcoal evenly without lighter fluid"
topic: "BBQ"
tags:
  - topic.bbq
  - type.note
  - status.draft
created: 2026-08-06
updated: 2026-08-06
source_type: note
---

# Charcoal chimney

Fill the chimney, light one sheet of newspaper underneath, wait for grey ash.
"""

COOKING_SUMMARY = """---
title: "Notes summary"
description: "Distilled rules and solutions from Cooking notes"
topic: "Cooking"
tags:
  - topic.cooking
  - type.summary
  - status.draft
created: 2026-08-06
updated: 2026-08-06
source_type: summary
---

# Notes summary

Sear late, rest longer than you think.
"""


# --------------------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------------------


def librarian(
    kb: Path,
    model: ScriptedChatModel,
    *,
    runtime: FakeRuntime | None = None,
    subagents: list[dict[str, Any]] | None = None,
    tools: list[BaseTool] | None = None,
) -> Any:
    return build_librarian(
        kb,
        runtime or FakeRuntime(kb),
        model=model,
        subagents=subagents or [],
        tools=tools or [],
    )


def expert_subagent(graph: Any, agent_id: str, description: str) -> dict[str, Any]:
    """One registry entry: exactly `{name, description, runnable}` and nothing else (RG-7, RG-9)."""
    return {"name": agent_id, "description": description, "runnable": graph}


def delegate(agent_id: str, call_id: str, instruction: str = "handle this") -> Any:
    return calls(call("task", {"description": instruction, "subagent_type": agent_id}, call_id))


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
    """LB-1: a deep agent in its own right, not a dispatcher function in front of the experts."""
    graph = librarian(kb, scripted(says("hello")))

    assert graph.name == LIBRARIAN_AGENT_ID
    assert run(graph, thread="t-lb1")["messages"][-1].content == "hello"


def test_the_librarian_prompt_is_kb_independent_lb3(kb: Path, tmp_path: Path) -> None:
    """LB-3: no topic names, no descriptions, no per-topic instructions — byte-identical across KBs.

    The routing view is the *generated* root `index.md`, which arrives as context each turn; nothing
    about routing is maintained by hand, so nothing about it can go stale.
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


def test_the_root_catalog_is_in_context_every_turn_lb4(kb: Path) -> None:
    """LB-4: root `index.md` is bounded (GE-12) so it is loaded; root `tags.md` is read on demand."""
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
    behaviour — the whole reason the expert exists.
    """
    model = scripted(says("routed"))
    with captured(librarian_module) as seen:
        run(librarian(kb, model), thread="t-lb5")

    kwargs = seen[0]
    assert kwargs["skills"] == [SKILLS_MOUNT]
    assert kwargs["permissions"] == kb_permissions(None)
    assert middleware_names(kwargs) == [
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
    model = scripted(says("nothing is filed yet — shall I create a topic?"))
    with captured(librarian_module) as seen:
        graph = librarian(empty_kb, model)
        result = run(graph, thread="t-lb6")

    assert [spec["name"] for spec in seen[0]["subagents"]] == ["general-purpose"]
    assert seen[0]["skills"] == [SKILLS_MOUNT]
    assert result["messages"][-1].content.startswith("nothing is filed yet")
    assert BLOCK_OPEN in system_text(model)


# --------------------------------------------------------------------------------------
# LB-7 — topic creation
# --------------------------------------------------------------------------------------


def test_topic_creation_is_gated_and_then_scaffolds_through_layer_one_lb7(kb: Path) -> None:
    """LB-7: propose → interrupt → `scaffold_topic` under the lock. Nothing is created unattended.

    Topic creation is an agent-invoked tool rather than a transport endpoint — all interactions are
    agent-mediated — and `pkb.core.scaffold_topic` carries no gate of its own (SC-8), so this is the
    only thing standing between a model's idea and six new files.
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
# LB-8 … LB-11 — delegation
# --------------------------------------------------------------------------------------


def test_two_parallel_delegations_both_land_lb8(kb: Path) -> None:
    """LB-8: multi-topic routing is several `task()` calls in one turn.

    Every expert graph must therefore be safe to invoke concurrently inside one Librarian run: no
    per-graph mutable state, one shared backend and checkpointer, and no per-run attribute on any
    middleware instance (MW-4).
    """
    runtime = FakeRuntime(kb)
    cooking = build(
        kb,
        scripted(write_note(f"{KB_MOUNT}{COOKING_NOTE_REL}", COOKING_NOTE, "a1"), says("filed")),
        COOKING,
        runtime=runtime,
    )
    bbq = build(
        kb,
        scripted(write_note(f"{KB_MOUNT}{BBQ_NOTE_REL}", BBQ_NOTE, "b1"), says("filed")),
        BBQ,
        runtime=runtime,
    )

    model = scripted(
        calls(
            call("task", {"description": "the steak part", "subagent_type": COOKING_AGENT}, "p1"),
            call("task", {"description": "the fuel part", "subagent_type": BBQ_AGENT}, "p2"),
        ),
        says("both experts have filed their part"),
    )
    graph = librarian(
        kb,
        model,
        runtime=runtime,
        subagents=[
            expert_subagent(cooking, COOKING_AGENT, "Home cooking"),
            expert_subagent(bbq, BBQ_AGENT, "Barbecue"),
        ],
    )
    run(graph, thread="t-lb8")

    assert (kb / COOKING_NOTE_REL).is_file()
    assert (kb / BBQ_NOTE_REL).is_file()


def test_delegated_work_checkpoints_under_the_parents_thread_lb9(kb: Path) -> None:
    """LB-9/D-6: the checkpointer keys on `thread_id` alone; `checkpoint_ns` is not a second axis.

    Architecture §4 says a thread is `(agent_id, thread_id)` and that delegated work runs in its own
    thread. It does not: delegated work lands in a nested `tools:<uuid>` namespace under the
    *Librarian's* thread. The consequences the architecture draws still hold — a direct conversation
    with the expert is a different conversation — but Layer 3 must build its `threads` table against
    the mechanism that exists, and must never register a row for a delegated run.
    """
    runtime = FakeRuntime(kb)
    cooking = build(kb, scripted(says("nothing to file")), COOKING, runtime=runtime)
    model = scripted(delegate(COOKING_AGENT, "d1"), says("asked the expert"))
    graph = librarian(
        kb,
        model,
        runtime=runtime,
        subagents=[expert_subagent(cooking, COOKING_AGENT, "Home cooking")],
    )
    run(graph, thread="t-lb9")

    namespaces = {
        (tuple_.config["configurable"]["thread_id"], tuple_.config["configurable"]["checkpoint_ns"])
        for tuple_ in runtime.checkpointer.list(None)
    }
    assert ("t-lb9", "") in namespaces
    assert any(thread == "t-lb9" and ns.startswith("tools:") for thread, ns in namespaces)
    assert {thread for thread, _ in namespaces} == {"t-lb9"}


def test_a_delegated_interrupt_resolves_on_the_librarians_thread_lb10(kb: Path) -> None:
    """LB-10: approval resolution is routed by **thread**, never by agent.

    A gate that fired inside the Cooking expert surfaces on the Librarian's thread and is answered
    with the ordinary `Command(resume=...)` there. Any other design would need a client to know
    which delegate raised an approval before it could answer it.
    """
    runtime = FakeRuntime(kb)
    summary = f"{KB_MOUNT}Cooking/notes/summary.md"
    cooking = build(
        kb,
        scripted(write_note(summary, COOKING_SUMMARY, "s1"), says("summary drafted")),
        COOKING,
        runtime=runtime,
    )
    model = scripted(delegate(COOKING_AGENT, "d2", "distil the notes"), says("the human approved"))
    graph = librarian(
        kb,
        model,
        runtime=runtime,
        subagents=[expert_subagent(cooking, COOKING_AGENT, "Home cooking")],
    )
    run(graph, thread="t-lb10")

    state = graph.get_state(config("t-lb10"))
    assert len(state.interrupts) == 1
    assert state.interrupts[0].value["action_requests"][0]["args"]["file_path"] == summary
    assert "Sear late" not in (kb / "Cooking" / "notes" / "summary.md").read_text()

    graph.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config("t-lb10"))

    assert "Sear late" in (kb / "Cooking" / "notes" / "summary.md").read_text()


def test_delegation_flushes_twice_and_the_parents_flush_is_a_no_op_lb11(kb: Path) -> None:
    """LB-11: two `after_agent` flushes per delegated turn — safe only because the key is private.

    deepagents merges a subagent's whole state back into the parent except `messages`, `todos`,
    `structured_response` and the keys marked `PrivateStateAttr`. Without that marker the expert's
    touched paths would leak up and the Librarian would re-stamp `updated` on files it never wrote.
    """
    runtime = FakeRuntime(kb)
    cooking = build(
        kb,
        scripted(write_note(f"{KB_MOUNT}{COOKING_NOTE_REL}", COOKING_NOTE, "f1"), says("filed")),
        COOKING,
        runtime=runtime,
    )
    model = scripted(delegate(COOKING_AGENT, "d3"), says("filed by the expert"))
    graph = librarian(
        kb,
        model,
        runtime=runtime,
        subagents=[expert_subagent(cooking, COOKING_AGENT, "Home cooking")],
    )
    run(graph, thread="t-lb11")

    expert_flush, librarian_flush = runtime.reports
    assert len(runtime.reports) == 2
    assert expert_flush.stamped == [COOKING_NOTE_REL]
    assert expert_flush.written != []
    # The parent's flush sees an empty touched set and a tree that is already consistent.
    assert librarian_flush.stamped == []
    assert librarian_flush.written == []


# --------------------------------------------------------------------------------------
# RG-7, EX-10, EX-11 on the delegated path
# --------------------------------------------------------------------------------------


def test_expert_subagents_are_passed_through_verbatim_rg7(kb: Path) -> None:
    """RG-7: `CompiledSubAgent` is `{name, description, runnable}` — the factory adds no key.

    A declarative dict `SubAgent` is compiled fresh per invocation and cannot hold the multi-turn
    approval dialog README §1.6 requires, so the registry's compiled entries must survive untouched.
    """
    runtime = FakeRuntime(kb)
    entries = [
        expert_subagent(
            build(kb, scripted(says("x")), COOKING, runtime=runtime), COOKING_AGENT, "Home cooking"
        ),
        expert_subagent(
            build(kb, scripted(says("y")), BBQ, runtime=runtime), BBQ_AGENT, "Barbecue"
        ),
    ]
    with captured(librarian_module) as seen:
        librarian(kb, scripted(says("ok")), runtime=runtime, subagents=entries)

    passed = seen[0]["subagents"]
    assert passed[:-1] == entries
    assert all(set(spec) == {"name", "description", "runnable"} for spec in passed[:-1])
    assert passed[-1]["name"] == "general-purpose"


def test_a_delegated_expert_still_refuses_a_derived_write_ex10(kb: Path) -> None:
    """EX-10: a `CompiledSubAgent` inherits nothing, so the expert's own permissions are what hold.

    Omitting a piece of configuration on one expert would open the derived-write path for the
    delegated route only — the route no direct-connection test covers.
    """
    runtime = FakeRuntime(kb)
    cooking = build(
        kb,
        scripted(
            write_note(f"{KB_MOUNT}Cooking/index.md", "# rewritten\n", "x1"),
            says("the write was denied"),
        ),
        COOKING,
        runtime=runtime,
    )
    model = scripted(delegate(COOKING_AGENT, "d4", "fix the index"), says("it could not"))
    graph = librarian(
        kb,
        model,
        runtime=runtime,
        subagents=[expert_subagent(cooking, COOKING_AGENT, "Home cooking")],
    )
    run(graph, thread="t-ex10")

    assert "# rewritten" not in (kb / "Cooking" / "index.md").read_text()


def test_the_librarians_general_purpose_subagent_is_also_guarded_ex11(kb: Path) -> None:
    """EX-11/D-2: the auto-added subagent is on *every* deep agent, the Librarian included.

    The Librarian's own permissions deny writes under `/kb/**`, which the auto-added subagent does
    inherit — but validation and the flush are what the explicit spec restores, and the Librarian is
    the graph a channel connects to by default.
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


@pytest.mark.parametrize("subagent_count", [0, 2])
def test_the_general_purpose_spec_is_appended_not_substituted_ex11(
    kb: Path, subagent_count: int
) -> None:
    """EX-11: the guard is added alongside the registry's experts, never in place of one."""
    runtime = FakeRuntime(kb)
    entries = [
        expert_subagent(build(kb, scripted(says("x")), topic, runtime=runtime), agent_id, "d")
        for topic, agent_id in list(zip([COOKING, BBQ], [COOKING_AGENT, BBQ_AGENT], strict=True))[
            :subagent_count
        ]
    ]
    with captured(librarian_module) as seen:
        librarian(kb, scripted(says("ok")), runtime=runtime, subagents=entries)

    names = [spec["name"] for spec in seen[0]["subagents"]]
    assert names == [entry["name"] for entry in entries] + ["general-purpose"]
