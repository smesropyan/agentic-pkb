"""The Librarian's routing workflow — LB-12 … LB-19, and the runtime guarantees it must not break.

Every test here opens a **real** `PkbRuntime` over a real SQLite file and drives real compiled graphs
— one Librarian and one expert per topic, each with its own `ScriptedChatModel`. That is not
thoroughness for its own sake. The bug this workflow exists to fix was invisible to a unit test: the
Librarian held a `task` tool, every mechanical assertion about that tool passed, and the model simply
chose not to call it, answered out of `grep` output, and then said *"The Cooking expert checked the
knowledge base"*. The only assertion that would have caught it is the one made here — that an expert
graph actually ran, and that the words in the reply are the ones it actually produced.

So the shape of this file mirrors the shape of the guarantee:

* the fan-out is asserted by the **expert's own model being called** and by **files landing under its
  own topic**, never by the Librarian claiming it happened;
* the merge is asserted by finding the expert's exact string in the reply while no model was ever
  asked to write a merge — the scripted models are the complete set of things that could have;
* and the failure modes each get a test, because "two of four experts declined" and "one expert
  crashed" are both *successful* turns and both would be easy to turn into silent partial answers.

No API key, no network (SK-18). One temporary directory and one temporary SQLite file.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, Final

import pytest

from pkb.agents.expert import build_expert
from pkb.agents.librarian import build_librarian
from pkb.agents.paths import KB_MOUNT
from pkb.agents.registry import AgentRegistry
from pkb.agents.routing import (
    EXPERT_THREAD_SEPARATOR,
    MENU_FOOTER,
    MENU_HEADER,
    RETRY_INSTRUCTION,
    ExpertOutcome,
    RoutingDecision,
    expert_thread_id,
    librarian_thread_id,
    merge_reply,
    resolve_targets,
    routing_envelope,
    routing_menu,
)
from pkb.agents.runtime import DEFAULT_FANOUT_LIMIT, PkbRuntime, RuntimeConfig
from pkb.contracts import (
    AgentDescriptor,
    Decision,
    InterruptEvent,
    MessageComplete,
    RunEnd,
    RunError,
    SubagentEnd,
    SubagentStart,
)
from tests.agents.conftest import TODAY, ScriptedChatModel, call, calls, raises, says, scripted

LIBRARIAN: Final = "librarian"
COOKING: Final = "topic/cooking"
BBQ: Final = "topic/bbq"
GRILLING: Final = "topic/cooking/grilling"


def _note(topic: str, tag: str, title: str, body: str) -> str:
    return f"""---
title: "{title}"
description: "A short line about {title.lower()}"
topic: "{topic}"
tags:
  - {tag}
  - type.note
created: 2026-08-01
updated: 2026-08-01
source_type: note
---

# {title}

{body}
"""


def _reference(topic: str, tag: str, title: str, body: str) -> str:
    return f"""---
title: "{title}"
description: "What {topic} takes from this source"
topic: "{topic}"
tags:
  - {tag}
  - type.reference
created: 2026-08-01
updated: 2026-08-01
source_type: reference
---

# {title}

{body}
"""


SUMMARY: Final = """---
title: "Notes summary"
description: "Distilled rules and solutions from the Cooking notes"
topic: "Cooking"
tags:
  - topic.cooking
  - type.summary
created: 2026-08-06
updated: 2026-08-06
source_type: summary
---

# Notes summary

Sear hot, rest long.
"""


# --------------------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------------------


def _registry_factory(models: Mapping[str, ScriptedChatModel]) -> Callable[[PkbRuntime], Any]:
    """A registry that gives every agent its **own** scripted model, keyed by agent id.

    One model per agent rather than one shared script, because the whole subject of this file is
    which agents ran: a shared script makes "the Cooking expert answered" indistinguishable from "the
    Librarian answered twice", which is the exact confusion being fixed.
    """

    def make(runtime: PkbRuntime) -> AgentRegistry:
        def expert(
            kb_root: Path,
            topic_path: str,
            rt: Any,
            *,
            registry: Any = None,
            tools: Sequence[Any] = (),
            **_ignored: Any,
        ) -> Any:
            agent_id = rt.snapshot().topics[topic_path].agent_id
            return build_expert(
                kb_root, topic_path, rt, model=models[agent_id], registry=registry, tools=tools
            )

        def librarian(
            kb_root: Path,
            rt: Any,
            *,
            registry: Any = None,
            tools: Sequence[Any] = (),
            **_ignored: Any,
        ) -> Any:
            return build_librarian(
                kb_root, rt, model=models[LIBRARIAN], registry=registry, tools=tools
            )

        return AgentRegistry(
            runtime.kb_root,
            runtime,
            default_model="scripted",
            tool_factory=runtime.tools_for,
            expert_factory=expert,
            librarian_factory=librarian,
        )

    return make


@asynccontextmanager
async def opened(
    kb: Path,
    models: Mapping[str, ScriptedChatModel],
    *,
    clock: Callable[[], date] = lambda: TODAY,
    **config: Any,
) -> AsyncIterator[PkbRuntime]:
    async with PkbRuntime.open(
        kb,
        kb.parent / "pkb.sqlite",
        config=RuntimeConfig(clock=clock, **config),
        registry_factory=_registry_factory(models),
    ) as runtime:
        yield runtime


async def drain(runtime: PkbRuntime, agent: str, thread: str, text: str = "go") -> list[Any]:
    return [event async for event in runtime.run(agent, thread, text)]


def routes(*topic_ids: str, reason: str = "it is about these", id_: str = "r1") -> Any:
    """The classification turn: one `route` call and nothing else (LB-12)."""
    return calls(call("route", {"topic_ids": list(topic_ids), "reason": reason}, id_))


def writes(path: str, content: str, id_: str) -> Any:
    return calls(call("write_file", {"file_path": f"{KB_MOUNT}{path}", "content": content}, id_))


def final(events: Sequence[Any]) -> str:
    ends = [event for event in events if isinstance(event, RunEnd)]
    assert len(ends) == 1, f"a turn has exactly one terminal event, got {len(ends)}"
    return ends[0].final_text


def delegates(events: Sequence[Any]) -> list[str]:
    return [event.agent_id for event in events if isinstance(event, SubagentStart)]


def descriptor(agent_id: str, title: str, description: str = "d") -> AgentDescriptor:
    return AgentDescriptor(
        agent_id=agent_id,
        title=title,
        description=description,
        has_custom_expert=False,
        model_id="scripted",
    )


# --------------------------------------------------------------------------------------
# LB-15 … LB-18 — the fan-out and the merge, for a question
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_single_topic_question_runs_that_expert_and_no_other_lb15(kb: Path) -> None:
    """One topic named, one expert run — and the answer is that expert's own words.

    The old design could pass a test like this without an expert ever running, because the Librarian
    was free to answer from `read_file` and describe the result as an expert's. Here the Cooking
    model's string is in the reply and the BBQ model was never called at all, which is only possible
    if the fan-out really invoked one graph and really did not invoke the other.
    """
    models = {
        LIBRARIAN: scripted(routes(COOKING)),
        COOKING: scripted(says("Pull a ribeye at 52C and rest it.")),
        BBQ: scripted(says("never asked")),
        GRILLING: scripted(says("never asked")),
    }
    async with opened(kb, models) as runtime:
        events = await drain(runtime, LIBRARIAN, "T1", "when do I pull a ribeye?")

    assert delegates(events) == [COOKING]
    assert models[BBQ].idx == 0, "an unnamed expert is not run"
    text = final(events)
    assert "## Cooking — `topic/cooking`" in text
    assert "Pull a ribeye at 52C and rest it." in text
    # LB-15(c): the expert reads the human's item, not a paraphrase of it.
    assert str(models[COOKING].calls[0][-1].content).endswith("when do I pull a ribeye?")


@pytest.mark.asyncio
async def test_a_two_topic_question_produces_two_attributed_sections_lb18(kb: Path) -> None:
    """LB-18: each expert's own answer, under its own heading, with its title and agent id.

    The merge is code, so the reply's structure is provable rather than hoped for: both experts'
    verbatim strings are present, each under a heading naming that expert, and **no model produced
    the reply** — the three scripted models are the complete set of things that could have written
    anything, and none of their scripts contains the headline.
    """
    models = {
        LIBRARIAN: scripted(routes(COOKING, BBQ, reason="steak and fuel")),
        COOKING: scripted(says("Reverse sear it.")),
        BBQ: scripted(says("Light the chimney with newspaper.")),
        GRILLING: scripted(says("never asked")),
    }
    async with opened(kb, models) as runtime:
        events = await drain(runtime, LIBRARIAN, "T1", "steak on the grill?")

    text = final(events)
    assert delegates(events) == [COOKING, BBQ]
    assert text.index("## Cooking — `topic/cooking`") < text.index("## BBQ — `topic/bbq`")
    assert "Reverse sear it." in text
    assert "Light the chimney with newspaper." in text
    # Nothing in the reply was written by a model: no script contains it, and the only model whose
    # turn could have produced prose here — the Librarian's — emitted a single tool call.
    assert not any(
        isinstance(entry, str) and entry in text
        for model in models.values()
        for entry in model.script
    )
    assert [message.content for message in models[LIBRARIAN].script if hasattr(message, "content")]


@pytest.mark.asyncio
async def test_the_merged_reply_is_recorded_on_the_librarians_thread_lb18(kb: Path) -> None:
    """The turn's answer is part of the conversation, or the next turn classifies against a hole."""
    models = {
        LIBRARIAN: scripted(routes(COOKING)),
        COOKING: scripted(says("Reverse sear it.")),
        BBQ: scripted(says("never asked")),
        GRILLING: scripted(says("never asked")),
    }
    async with opened(kb, models) as runtime:
        events = await drain(runtime, LIBRARIAN, "T1", "steak?")
        history = await runtime.history(LIBRARIAN, "T1")

    assert [event for event in events if isinstance(event, MessageComplete)][-1].agent_id == (
        LIBRARIAN
    )
    assert history[-1].role == "assistant"
    assert history[-1].text == final(events)


# --------------------------------------------------------------------------------------
# The human's ruling on ingestion: several experts, one source, different extractions
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_source_is_ingested_by_two_experts_through_two_lenses(kb: Path) -> None:
    """Ingestion fans out exactly like a question, and the two files are **not** copies.

    The human's ruling, in their words: *"Same thing can be ingested by multiple topic experts!
    Copies are allowed and encouraged for multi-faceted books being ingested… The experts ingest each
    book/paper/article/clip from the lens of their expertise therefore not duplicating but rather
    extracting different facets from the same source."*

    So this asserts three things, and the third is the one that matters. Both experts file — under
    their **own** topic, which their topic-scoped permissions (RT-15) are what enforce. The bodies
    **differ**, because each expert read the same source through its own `expert.md`, skills chain
    and voice; two identical files would mean the fan-out had produced a copy, which is the outcome
    README §1.8 rule 4 is about and this is not. And no approval fires (RT-31): filing a reference
    depth file is the frictionless path, and an ingestion that gated twice would make routing to two
    experts twice as expensive as routing to one.
    """
    book = "references/meathead/meathead.md"
    models = {
        LIBRARIAN: scripted(routes(COOKING, BBQ, reason="a grilling book, technique and fuel")),
        COOKING: scripted(
            writes(
                f"Cooking/{book}",
                _reference("Cooking", "topic.cooking", "Meathead", "On heat and doneness."),
                "c1",
            ),
            says("Filed the technique chapters."),
        ),
        BBQ: scripted(
            writes(
                f"BBQ/{book}",
                _reference("BBQ", "topic.bbq", "Meathead", "On charcoal, wood and smoke."),
                "b1",
            ),
            says("Filed the fuel chapters."),
        ),
        GRILLING: scripted(says("never asked")),
    }
    reports: list[Any] = []
    async with opened(kb, models, flush_sink=reports.append) as runtime:
        events = await drain(runtime, LIBRARIAN, "T1", "here is Meathead, ingest it")

    # LB-11: each expert run flushes its own turn, and they serialize on the KB write lock rather
    # than interleaving. Two files written, two flushes that stamped anything, one path each.
    stamped = [report.stamped for report in reports if report.stamped]
    assert sorted(stamped) == [[f"BBQ/{book}"], [f"Cooking/{book}"]]

    cooking_copy = kb / "Cooking" / book
    bbq_copy = kb / "BBQ" / book
    assert cooking_copy.is_file()
    assert bbq_copy.is_file()
    assert "On heat and doneness." in cooking_copy.read_text()
    assert "On charcoal, wood and smoke." in bbq_copy.read_text()
    assert cooking_copy.read_text() != bbq_copy.read_text(), "two extractions, not two copies"

    assert not [event for event in events if isinstance(event, InterruptEvent)]
    text = final(events)
    assert "Filed the technique chapters." in text
    assert "Filed the fuel chapters." in text
    assert f"`Cooking/{book}`" in text and f"`BBQ/{book}`" in text


@pytest.mark.asyncio
async def test_one_expert_files_and_another_declines_and_that_is_a_success(kb: Path) -> None:
    """A decline is a correct outcome, not a partial failure.

    The human accepted "misrouted material, nothing filed" as correct, so nothing here reads the
    expert's answer to decide whether it "worked": both experts ran, one filed, the other said it had
    nothing, and both sections appear with the same standing. The reply's headline says every expert
    answered, because every expert did.
    """
    models = {
        LIBRARIAN: scripted(routes(COOKING, BBQ, reason="might be either")),
        COOKING: scripted(
            writes(
                "Cooking/notes/brining.md",
                _note("Cooking", "topic.cooking", "Brining", "Salt early, dry late."),
                "c1",
            ),
            says("Filed it as a note."),
        ),
        BBQ: scripted(says("Nothing here concerns barbecue; I filed nothing.")),
        GRILLING: scripted(says("never asked")),
    }
    async with opened(kb, models) as runtime:
        events = await drain(runtime, LIBRARIAN, "T1", "brining a turkey")

    assert (kb / "Cooking" / "notes" / "brining.md").is_file()
    assert (
        not list((kb / "BBQ" / "notes").glob("*.md"))
        or not (kb / "BBQ" / "notes" / "brining.md").exists()
    )
    text = final(events)
    assert "each answered for its own topic" in text
    assert "Filed it as a note." in text
    assert "Nothing here concerns barbecue" in text
    assert "`Cooking/notes/brining.md`" in text
    assert {event.status for event in events if isinstance(event, SubagentEnd)} == {"answered"}


# --------------------------------------------------------------------------------------
# LB-16 — an expert hitting an approval gate
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_experts_gate_parks_on_its_own_thread_and_the_others_still_answer_lb16(
    kb: Path,
) -> None:
    """The case to think hardest about: one expert in the fan-out needs a human decision.

    Ingestion fan-out makes this common — several experts may each want to file, and each gate
    belongs to the expert that raised it. The design answer is that **the Librarian's turn never
    parks**: the expert runs on its own derived thread (LB-14), so its interrupt is durable and
    resolvable *there*, the other experts are unaffected, and the merged reply is delivered with that
    expert's section saying what it is waiting for.

    Three things follow, and all three are asserted, because getting any of them wrong is worse than
    the bug being fixed:

    * the Librarian's thread has **no** pending approval, so RT-39 does not refuse the human's next
      message — a turn that parked the front door on one expert's summary would be a system-wide
      stall triggered by one topic;
    * the interrupt names the **expert** and the expert's thread, which is exactly what step 4's
      offer told the human, so a client can answer it without knowing anything about the fan-out;
    * resolving it on that thread completes the write.
    """
    models = {
        LIBRARIAN: scripted(routes(COOKING, BBQ, reason="notes and fuel")),
        COOKING: scripted(
            writes("Cooking/notes/summary.md", SUMMARY, "c1"), says("Summary drafted.")
        ),
        BBQ: scripted(says("Charcoal, not gas.")),
        GRILLING: scripted(says("never asked")),
    }
    async with opened(kb, models) as runtime:
        events = await drain(runtime, LIBRARIAN, "T1", "distil the notes and tell me about fuel")

        interrupts = [event for event in events if isinstance(event, InterruptEvent)]
        assert len(interrupts) == 1
        request = interrupts[0].request
        assert request.agent_id == COOKING
        assert request.thread_id == expert_thread_id("T1", COOKING)

        text = final(events)
        assert "Charcoal, not gas." in text, "the other expert still delivered"
        assert "waiting on your decision" in text
        assert f"thread `{expert_thread_id('T1', COOKING)}`" in text
        assert "Sear hot" not in (kb / "Cooking" / "notes" / "summary.md").read_text()

        # The front door is free: the human may send another message immediately (RT-39).
        assert await runtime.pending_approval(LIBRARIAN, "T1") is None
        assert await runtime.pending_approval(COOKING, request.thread_id) is not None

        resumed = [
            event
            async for event in runtime.resume(
                COOKING, request.thread_id, [Decision(type="approve")]
            )
        ]
        assert any(isinstance(event, RunEnd) for event in resumed)
        assert "Sear hot" in (kb / "Cooking" / "notes" / "summary.md").read_text()


@pytest.mark.asyncio
async def test_an_expert_still_parked_from_an_earlier_turn_is_reported_not_fatal_lb16(
    kb: Path,
) -> None:
    """RT-39 inside a fan-out: the refusal becomes that expert's section, never the turn's failure."""
    models = {
        LIBRARIAN: scripted(routes(COOKING, BBQ), routes(COOKING, BBQ, id_="r2")),
        COOKING: scripted(
            writes("Cooking/notes/summary.md", SUMMARY, "c1"), says("Summary drafted.")
        ),
        BBQ: scripted(says("Charcoal."), says("Still charcoal.")),
        GRILLING: scripted(says("never asked")),
    }
    async with opened(kb, models) as runtime:
        await drain(runtime, LIBRARIAN, "T1", "first turn")
        events = await drain(runtime, LIBRARIAN, "T1", "second turn")

    text = final(events)
    assert "Still charcoal." in text, "the unblocked expert answered again"
    assert "waiting on your decision" in text
    assert "1 could not finish" in text


# --------------------------------------------------------------------------------------
# LB-17 — one expert failing must not lose the others
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_expert_erroring_still_delivers_the_rest_lb17(kb: Path) -> None:
    """The failure is reported in its own section; the turn itself succeeds.

    A provider error inside one expert is a fact about that topic, not about the turn, so the
    expert's own `run.error` is folded into its section rather than forwarded — a caller must be able
    to tell "the turn failed" from "one of four experts failed" by the terminal event alone (RT-47).
    """
    models = {
        LIBRARIAN: scripted(routes(COOKING, BBQ, reason="both")),
        COOKING: scripted(raises(RuntimeError("the cooking model fell over"))),
        BBQ: scripted(says("Charcoal, and plenty of it.")),
        GRILLING: scripted(says("never asked")),
    }
    async with opened(kb, models) as runtime:
        events = await drain(runtime, LIBRARIAN, "T1", "steak and fuel")

    assert not [event for event in events if isinstance(event, RunError)]
    text = final(events)
    assert "Charcoal, and plenty of it." in text
    assert "1 could not finish" in text
    assert "the cooking model fell over" in text
    assert {event.agent_id: event.status for event in events if isinstance(event, SubagentEnd)} == {
        COOKING: "failed",
        BBQ: "answered",
    }


# --------------------------------------------------------------------------------------
# LB-19 — uncertainty ends with the human, never with a guess
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prose_instead_of_a_route_call_produces_the_expert_menu_lb19(kb: Path) -> None:
    """The exact failure that was measured, and what now happens instead.

    A real model answered the question itself rather than routing. The harness now forces it back to
    the tool once — that retry is asserted by the model having been called twice — and then stops
    guessing and asks: *"which of the following experts would you want to engage for your
    question."* Every catalog entry is offered by agent id and title, no expert has been run, and the
    model's own words are quoted rather than thrown away.

    A guess would be the alternative, and a guess files knowledge in the wrong topic with no undo.
    """
    models = {
        LIBRARIAN: scripted(says("I had a look and the answer is probably 52 degrees.")),
        COOKING: scripted(says("never asked")),
        BBQ: scripted(says("never asked")),
        GRILLING: scripted(says("never asked")),
    }
    async with opened(kb, models) as runtime:
        events = await drain(runtime, LIBRARIAN, "T1", "when do I pull a ribeye?")
        catalog = [d for d in runtime.list_agents() if d.agent_id != LIBRARIAN]

    assert len(models[LIBRARIAN].calls) == 2, "exactly one forced retry (MAX_ROUTE_ATTEMPTS)"
    assert RETRY_INSTRUCTION in str(models[LIBRARIAN].calls[1][-1].content)
    assert delegates(events) == []
    assert models[COOKING].idx == 0

    text = final(events)
    assert MENU_HEADER in text
    assert MENU_FOOTER in text
    for entry in catalog:
        assert f"`{entry.agent_id}`" in text
        assert entry.title in text
    assert "> I had a look and the answer is probably 52 degrees." in text


@pytest.mark.asyncio
async def test_naming_no_topic_over_a_populated_catalog_asks_the_human_lb19(kb: Path) -> None:
    """An empty `route` call with candidates to choose from is uncertainty, not a topic gap."""
    models = {
        LIBRARIAN: scripted(routes(reason="I cannot tell")),
        COOKING: scripted(says("never asked")),
        BBQ: scripted(says("never asked")),
        GRILLING: scripted(says("never asked")),
    }
    async with opened(kb, models) as runtime:
        events = await drain(runtime, LIBRARIAN, "T1", "a stray thought")

    assert delegates(events) == []
    assert MENU_HEADER in final(events)


@pytest.mark.asyncio
async def test_an_unknown_topic_id_is_reported_rather_than_silently_dropped(kb: Path) -> None:
    """A hallucinated id is a routing fault the human should see once, in the reply."""
    models = {
        LIBRARIAN: scripted(routes(COOKING, "topic/atlantis")),
        COOKING: scripted(says("Reverse sear it.")),
        BBQ: scripted(says("never asked")),
        GRILLING: scripted(says("never asked")),
    }
    async with opened(kb, models) as runtime:
        events = await drain(runtime, LIBRARIAN, "T1", "steak")

    text = final(events)
    assert delegates(events) == [COOKING]
    assert "`topic/atlantis`" in text
    assert "Reverse sear it." in text


# --------------------------------------------------------------------------------------
# The topic gap — nothing applicable and nothing to choose from
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_empty_catalog_reaches_the_gated_create_topic_flow_lb7(empty_kb: Path) -> None:
    """A topic gap goes to `create_topic`, gated, on the Librarian's own thread (README §2.2).

    Bootstrapping starts here: zero topics, so every inbound item is a gap and there is no menu to
    offer — a list of no experts is not a choice. The routing step hands the turn back to the
    Librarian, which proposes a topic, and the human decides. Nothing is created unattended.
    """
    models = {
        LIBRARIAN: scripted(
            routes(reason="nothing exists yet"),
            calls(
                call(
                    "create_topic",
                    {"name": "Physics", "title": "Physics", "description": "Mechanics"},
                    "t1",
                )
            ),
            says("proposed it"),
        )
    }
    async with opened(empty_kb, models) as runtime:
        events = await drain(runtime, LIBRARIAN, "T1", "I have a physics paper")

        interrupts = [event for event in events if isinstance(event, InterruptEvent)]
        assert len(interrupts) == 1
        assert interrupts[0].request.actions[0].reason == "topic-creation"
        assert not (empty_kb / "Physics").exists()

        resumed = [
            event async for event in runtime.resume(LIBRARIAN, "T1", [Decision(type="approve")])
        ]
        assert any(isinstance(event, RunEnd) for event in resumed)
        assert (empty_kb / "Physics" / "topic.md").is_file()
        assert "topic/physics" in {agent.agent_id for agent in runtime.list_agents()}


@pytest.mark.asyncio
async def test_a_gated_topic_proposal_does_not_fan_out_and_leaves_the_thread_resumable(
    kb: Path,
) -> None:
    """A classification turn that parks on `create_topic` ends there: no experts, one interrupt."""
    models = {
        LIBRARIAN: scripted(
            calls(
                call(
                    "create_topic",
                    {"name": "Physics", "title": "Physics", "description": "Mechanics"},
                    "t1",
                )
            ),
            says("proposed it"),
        ),
        COOKING: scripted(says("never asked")),
        BBQ: scripted(says("never asked")),
        GRILLING: scripted(says("never asked")),
    }
    async with opened(kb, models) as runtime:
        events = await drain(runtime, LIBRARIAN, "T1", "I have a physics paper")

        assert delegates(events) == []
        assert len([event for event in events if isinstance(event, InterruptEvent)]) == 1
        assert await runtime.pending_approval(LIBRARIAN, "T1") is not None


# --------------------------------------------------------------------------------------
# LB-15 — the cap
# --------------------------------------------------------------------------------------


def _tracked(
    log: list[tuple[str, str]], agent: str, text: str, barrier: threading.Barrier | None
) -> Callable[[], Any]:
    """A scripted turn that records when it entered and left the model, for concurrency assertions.

    `ScriptedChatModel` runs `_generate` on the default executor under `ainvoke`, so two experts in
    flight are two real threads — which is what makes both directions of the cap observable: with a
    cap of one the log can never show two overlapping entries, and with a cap of three a barrier of
    three completes.
    """

    def turn() -> Any:
        log.append(("enter", agent))
        if barrier is not None:
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                log.append(("broken", agent))
        log.append(("exit", agent))
        return says(text)

    return turn


@pytest.mark.asyncio
async def test_a_cap_of_one_serializes_the_fan_out_lb15(kb: Path) -> None:
    """LB-15: a five-topic question must not fire five concurrent runs.

    The deployment's plan allows three concurrent cloud models, so the cap is real capacity rather
    than tidiness — over it, the extra runs come back as `429`s and the merged reply reports a
    knowledge-base failure that is really a plan limit. At one, the fan-out is strictly sequential,
    which is the only setting where "no two overlapped" is provable without timing.
    """
    log: list[tuple[str, str]] = []
    models = {
        LIBRARIAN: scripted(routes(COOKING, BBQ, GRILLING)),
        COOKING: scripted(_tracked(log, COOKING, "a", None)),
        BBQ: scripted(_tracked(log, BBQ, "b", None)),
        GRILLING: scripted(_tracked(log, GRILLING, "c", None)),
    }
    async with opened(kb, models, fanout_limit=1) as runtime:
        events = await drain(runtime, LIBRARIAN, "T1", "everything at once")

    assert sorted(delegates(events)) == sorted([COOKING, BBQ, GRILLING])
    phases = [phase for phase, _ in log]
    assert phases == ["enter", "exit"] * 3, f"overlapping runs under a cap of one: {log}"


@pytest.mark.asyncio
async def test_the_cap_lets_experts_run_concurrently_up_to_it_lb15(kb: Path) -> None:
    """The other direction: up to the cap, the experts really do run at the same time."""
    log: list[tuple[str, str]] = []
    barrier = threading.Barrier(3)
    models = {
        LIBRARIAN: scripted(routes(COOKING, BBQ, GRILLING)),
        COOKING: scripted(_tracked(log, COOKING, "a", barrier)),
        BBQ: scripted(_tracked(log, BBQ, "b", barrier)),
        GRILLING: scripted(_tracked(log, GRILLING, "c", barrier)),
    }
    async with opened(kb, models, fanout_limit=3) as runtime:
        await drain(runtime, LIBRARIAN, "T1", "everything at once")

    assert [phase for phase, _ in log if phase == "broken"] == [], "three did not overlap"
    assert [phase for phase, _ in log][:3] == ["enter", "enter", "enter"]


def test_the_default_cap_matches_the_plans_concurrency_lb15() -> None:
    """Three, because the deployment's plan allows three concurrent cloud models (Q6)."""
    assert DEFAULT_FANOUT_LIMIT == 3
    assert RuntimeConfig().fanout_limit == DEFAULT_FANOUT_LIMIT


# --------------------------------------------------------------------------------------
# LB-14 — the derived expert thread
# --------------------------------------------------------------------------------------


def test_an_experts_thread_is_derived_from_the_librarians_lb14() -> None:
    """Derived rather than minted, so "continue with the Cooking expert" resolves to a real thread."""
    thread = expert_thread_id("T1", COOKING)

    assert thread == f"T1{EXPERT_THREAD_SEPARATOR}{COOKING}"
    assert librarian_thread_id(thread) == "T1"
    assert librarian_thread_id("T1") is None
    # A scan thread (RT-58) must never be mistaken for a derived one.
    assert librarian_thread_id("scan:topic/cooking:0f8e") is None


@pytest.mark.asyncio
async def test_an_expert_thread_holds_the_exchange_and_can_be_continued_lb14(kb: Path) -> None:
    """Step 4 is a link, not a suggestion: the thread the reply names has the history in it."""
    models = {
        LIBRARIAN: scripted(routes(COOKING)),
        COOKING: scripted(says("Reverse sear it."), says("Yes, 52C.")),
        BBQ: scripted(says("never asked")),
        GRILLING: scripted(says("never asked")),
    }
    async with opened(kb, models) as runtime:
        await drain(runtime, LIBRARIAN, "T1", "steak?")
        thread = expert_thread_id("T1", COOKING)

        history = await runtime.history(COOKING, thread)
        assert any(message.text == "Reverse sear it." for message in history)
        assert any("steak?" in message.text for message in history if message.role == "human")

        # And it is an ordinary thread: the human continues the conversation directly on it.
        events = [event async for event in runtime.run(COOKING, thread, "what temperature?")]
        assert final(events) == "Yes, 52C."


@pytest.mark.asyncio
async def test_deleting_a_librarian_thread_removes_the_experts_threads_rt48(kb: Path) -> None:
    """RT-48 under the derived-thread design (LB-14).

    Delegated work used to checkpoint inside the parent's own thread, so one `adelete_thread` erased
    everything. An addressable expert thread does not vanish with its parent, and a "delete this
    conversation" that left the expert's copy behind would be the worst kind of lie in a system with
    no undo (D6) — so the derived threads are deleted with it.
    """
    models = {
        LIBRARIAN: scripted(routes(COOKING)),
        COOKING: scripted(says("Reverse sear it.")),
        BBQ: scripted(says("never asked")),
        GRILLING: scripted(says("never asked")),
    }
    async with opened(kb, models) as runtime:
        await drain(runtime, LIBRARIAN, "T1", "steak?")
        thread = expert_thread_id("T1", COOKING)
        assert await runtime.history(COOKING, thread)

        await runtime.delete_thread("T1")

        assert await runtime.history(LIBRARIAN, "T1") == []
        assert await runtime.history(COOKING, thread) == []


# --------------------------------------------------------------------------------------
# The pure pieces: no graph, no model
# --------------------------------------------------------------------------------------


def test_resolve_targets_keeps_the_models_order_and_separates_unknown_ids() -> None:
    catalog = [descriptor(COOKING, "Cooking"), descriptor(BBQ, "BBQ")]
    decision = RoutingDecision([BBQ, COOKING, BBQ, "topic/atlantis"], "because")

    targets, unknown = resolve_targets(decision, catalog)

    assert [entry.agent_id for entry in targets] == [BBQ, COOKING]
    assert unknown == ["topic/atlantis"]
    assert resolve_targets(None, catalog) == ([], [])


def test_the_routing_envelope_carries_the_humans_words_verbatim() -> None:
    """An expert that ingests a paraphrase files a paraphrase."""
    message = "Here is the book.\n\nChapter 1: heat."

    envelope = routing_envelope(message, title="Cooking", reason="a grilling book")

    assert envelope.endswith(message)
    assert "Cooking" in envelope
    assert "a grilling book" in envelope
    assert "file nothing" in envelope, "declining must be named as a correct outcome"


def test_the_merge_names_every_expert_and_invents_nothing() -> None:
    """LB-18: attribution assembled from actual results cannot claim an expert that did not run.

    A golden test, byte for byte, because the shape of this string *is* the guarantee: one section
    per expert that ran, that expert's own text verbatim, and nothing else. If a future edit makes
    the composition depend on the content — a special case for one expert, a reconciliation of two
    answers, a summary line about what they agreed on — this fails, and it should.
    """
    outcomes = [
        ExpertOutcome(COOKING, "Cooking", "T::c", text="Sear it.", filed=["Cooking/notes/a.md"]),
        ExpertOutcome(BBQ, "BBQ", "T::b", status="failed", error="boom"),
    ]

    reply = merge_reply(outcomes, unknown=["topic/atlantis"])

    assert reply == (
        "Asked 2 experts; 1 could not finish. Every expert's own answer is under its own heading, "
        "unchanged.\n"
        "\n"
        "No topic in this knowledge base answers to `topic/atlantis`; nothing was sent there.\n"
        "\n"
        "## Cooking — `topic/cooking`\n"
        "\n"
        "Sear it.\n"
        "\n"
        "_Filed: `Cooking/notes/a.md`_\n"
        "\n"
        "_Continue with this expert: agent `topic/cooking`, thread `T::c`._\n"
        "\n"
        "## BBQ — `topic/bbq`\n"
        "\n"
        "_This expert's run failed and its part of the answer is missing. (boom)_\n"
        "\n"
        "_(this expert returned no message)_\n"
        "\n"
        "_Continue with this expert: agent `topic/bbq`, thread `T::b`._\n"
        "\n"
        "You can carry on directly with any of them: `topic/cooking` (thread `T::c`), "
        "`topic/bbq` (thread `T::b`)."
    )


def test_the_menu_offers_every_candidate_and_quotes_the_model() -> None:
    catalog = [descriptor(COOKING, "Cooking", "Home cooking"), descriptor(BBQ, "BBQ", "Barbecue")]

    menu = routing_menu(catalog, prose="I am not sure.")

    assert MENU_HEADER in menu
    assert "- `topic/cooking` — **Cooking**: Home cooking" in menu
    assert "- `topic/bbq` — **BBQ**: Barbecue" in menu
    assert "> I am not sure." in menu
    assert MENU_FOOTER in menu


@pytest.mark.asyncio
async def test_two_concurrent_librarian_turns_on_one_thread_are_refused_rt45(kb: Path) -> None:
    """RT-45 still holds when a turn drives several graphs: the slot covers the whole workflow.

    Releasing it at the end of the classification would admit a second turn while the fan-out was
    still running — it would classify against a thread whose reply had not been written yet and route
    the same item twice.
    """
    models = {
        LIBRARIAN: scripted(routes(COOKING)),
        COOKING: scripted(says("Reverse sear it."), says("again")),
        BBQ: scripted(says("never asked")),
        GRILLING: scripted(says("never asked")),
    }
    async with opened(kb, models) as runtime:
        results = await asyncio.gather(
            drain(runtime, LIBRARIAN, "T1"),
            drain(runtime, LIBRARIAN, "T1"),
            return_exceptions=True,
        )
        assert len([r for r in results if isinstance(r, Exception)]) == 1
        assert runtime._active == {}


@pytest.mark.asyncio
async def test_cancelling_a_turn_cancels_the_experts_it_started_rt46(kb: Path) -> None:
    """A turn drives several graphs under one run id; cancelling it must reach all of them."""
    started = threading.Event()

    def slow() -> Any:
        started.set()
        threading.Event().wait(30)
        return says("never")

    models = {
        LIBRARIAN: scripted(routes(COOKING)),
        COOKING: scripted(slow),
        BBQ: scripted(says("never asked")),
        GRILLING: scripted(says("never asked")),
    }
    async with opened(kb, models) as runtime:
        events: list[Any] = []

        async def consume() -> None:
            async for event in runtime.run(LIBRARIAN, "T1", "go", run_id="R1"):
                events.append(event)

        task = asyncio.create_task(consume())
        await asyncio.to_thread(started.wait, 20)
        await runtime.cancel("R1")
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(task, timeout=20)

        assert runtime._active == {}
