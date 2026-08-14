"""Graph stream → `AgentEvent` normalization — RT-41, RT-43, RT-44, RT-47.

The delegation fixture is a real Librarian-shaped deep agent with a real `CompiledSubAgent`, so the
event sequence under test is the one the daemon will see, including the two `__interrupt__`
emissions and the `('tools:<uuid>',)` namespace. No API key, no network.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from deepagents import CompiledSubAgent, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from pkb.agents.approval import normalize_interrupts, to_resume_command
from pkb.agents.events import (
    STREAM_MODES,
    SUBGRAPHS,
    EventNormalizer,
    is_retryable,
    stream_events,
)
from pkb.contracts import (
    AgentEvent,
    Decision,
    InterruptEvent,
    MessageComplete,
    MessageDelta,
    RunEnd,
    RunError,
    SubagentEnd,
    SubagentStart,
    ToolEnd,
    ToolStart,
)
from tests.agents.conftest import call, calls, raises, says, scripted

DELEGATE = "topic/cooking"

GATE = {"write_file": {"allowed_decisions": ["approve", "edit", "reject"]}}


def backend_for(kb: Path) -> CompositeBackend:
    return CompositeBackend(
        default=StateBackend(),
        routes={"/kb/": FilesystemBackend(root_dir=str(kb), virtual_mode=True)},
    )


def build_librarian(kb: Path, model: Any, gate: dict[str, Any] | None = None) -> Any:
    """A parent agent with one compiled expert registered under its agent id (RG-7, RG-9).

    Built from `create_deep_agent` directly rather than from `pkb.agents.librarian` so these rules
    stay about the stream translation and cannot fail because a sibling module changed its prompt.
    """
    backend = backend_for(kb)
    expert = create_deep_agent(
        model=model,
        system_prompt="Cooking expert",
        backend=backend,
        interrupt_on=gate,
        checkpointer=InMemorySaver(),
    )
    return create_deep_agent(
        model=model,
        system_prompt="Librarian",
        backend=backend,
        subagents=[CompiledSubAgent(name=DELEGATE, description="Home cooking", runnable=expert)],
        checkpointer=InMemorySaver(),
    )


def delegating_script(*, content: str = "steak") -> Any:
    """Parent delegates, delegate writes and reports, parent answers — one global call order."""
    return scripted(
        calls(call("task", {"description": "file it", "subagent_type": DELEGATE}, "p1")),
        calls(
            call(
                "write_file",
                {"file_path": "/kb/Cooking/notes/steak.md", "content": content},
                "d1",
            )
        ),
        says("Filed the steak note."),
        says("All done."),
    )


async def collect(graph: Any, thread_id: str, *, agent_id: str = "librarian") -> list[AgentEvent]:
    return [
        event
        async for event in stream_events(
            graph,
            {"messages": [{"role": "user", "content": "file this"}]},
            {"configurable": {"thread_id": thread_id}},
            run_id="run-1",
            agent_id=agent_id,
            thread_id=thread_id,
        )
    ]


def kinds(events: list[AgentEvent]) -> set[type]:
    return {type(event) for event in events}


# --------------------------------------------------------------------------------------
# RT-43 — the driver, and the protocol it must not use
# --------------------------------------------------------------------------------------


def test_astream_events_v3_is_a_different_protocol_rt43(kb: Path) -> None:
    """Regression pin on D-12: arch §5's `astream_events(version="v3")` is not iterable here.

    On langgraph 1.2.10 it returns a coroutine that must be awaited before iteration and yields
    JSON-RPC envelopes, not `on_chat_model_stream` events. If a version bump makes this an async
    generator, this test fails and the choice in `events.py` can be revisited on purpose.
    """
    graph = create_deep_agent(
        model=scripted(says("hi")), system_prompt="x", backend=backend_for(kb)
    )

    result = graph.astream_events({"messages": []}, version="v3")
    try:
        assert asyncio.iscoroutine(result)
    finally:
        result.close()

    v2 = graph.astream_events({"messages": []}, version="v2")
    assert not asyncio.iscoroutine(v2)


@pytest.mark.asyncio
async def test_the_stream_is_updates_messages_and_subgraphs_rt43() -> None:
    """`subgraphs=True` is required, or a delegated expert's messages are invisible."""
    seen: dict[str, Any] = {}

    class SpyGraph:
        def astream(self, payload: Any, config: Any, **kwargs: Any) -> AsyncIterator[Any]:
            seen.update(kwargs)

            async def empty() -> AsyncIterator[Any]:
                return
                yield  # pragma: no cover - makes this an async generator

            return empty()

    events = [
        event
        async for event in stream_events(
            SpyGraph(), {}, {}, run_id="r", agent_id="librarian", thread_id="T"
        )
    ]

    assert seen == {"stream_mode": ["updates", "messages"], "subgraphs": True}
    assert STREAM_MODES == ("updates", "messages")
    assert SUBGRAPHS is True
    assert events == [RunEnd(run_id="r", final_text="")]


@pytest.mark.asyncio
async def test_every_event_kind_is_a_frozen_json_serializable_dataclass_rt43(kb: Path) -> None:
    """All nine kinds, from three real runs, each surviving `asdict` → `json.dumps`."""
    delegation = await collect(build_librarian(kb, delegating_script()), "T-all")
    gated = await collect(build_librarian(kb, delegating_script(), GATE), "T-all-gate")
    failed = await collect(
        build_librarian(kb, scripted(raises(RuntimeError("provider exploded")))), "T-all-fail"
    )
    events = delegation + gated + failed

    assert kinds(events) == {
        MessageDelta,
        MessageComplete,
        ToolStart,
        ToolEnd,
        SubagentStart,
        SubagentEnd,
        InterruptEvent,
        RunEnd,
        RunError,
    }
    for event in events:
        assert dataclasses.is_dataclass(event)
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.run_id = "mutated"  # type: ignore[misc]
        json.dumps(dataclasses.asdict(event))


# --------------------------------------------------------------------------------------
# RT-44 — a subagent event names the delegate
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_delegation_yields_one_start_and_end_naming_the_delegate_rt44(kb: Path) -> None:
    """`args["subagent_type"]` is the only reliable label: the namespace carries no agent name."""
    events = await collect(build_librarian(kb, delegating_script()), "T-delegate")

    starts = [e for e in events if isinstance(e, SubagentStart)]
    ends = [e for e in events if isinstance(e, SubagentEnd)]

    assert [e.agent_id for e in starts] == [DELEGATE]
    assert [(e.agent_id, e.status) for e in ends] == [(DELEGATE, "success")]


@pytest.mark.asyncio
async def test_the_task_call_is_not_also_a_tool_event_rt44(kb: Path) -> None:
    """Delegation is reported once, as `subagent.*` — never additionally as a `task` tool call."""
    events = await collect(build_librarian(kb, delegating_script()), "T-no-task-tool")

    tools = [e.tool for e in events if isinstance(e, ToolStart | ToolEnd)]

    assert "task" not in tools
    assert tools == ["write_file", "write_file"]


@pytest.mark.asyncio
async def test_delegated_work_is_attributed_to_the_delegate_rt44(kb: Path) -> None:
    """The expert's own tool call and report carry its id, not the Librarian's."""
    events = await collect(build_librarian(kb, delegating_script()), "T-attrib")

    tool_starts = [e for e in events if isinstance(e, ToolStart)]
    reports = [e for e in events if isinstance(e, MessageComplete) and e.text.startswith("Filed")]

    assert [e.agent_id for e in tool_starts] == [DELEGATE]
    assert [e.agent_id for e in reports] == [DELEGATE]


@pytest.mark.asyncio
async def test_tool_events_summarize_the_path_never_the_content_rt43(kb: Path) -> None:
    """`ToolStart.summary` is rendered, not raw args: proposed content belongs in the approval."""
    events = await collect(build_librarian(kb, delegating_script(content="SECRET BODY")), "T-sum")

    start = next(e for e in events if isinstance(e, ToolStart))

    assert start.summary == "/kb/Cooking/notes/steak.md"
    assert all("SECRET BODY" not in json.dumps(dataclasses.asdict(e)) for e in events)


@pytest.mark.asyncio
async def test_run_end_carries_the_root_agents_final_text_rt43(kb: Path) -> None:
    """The delegate's closing report is its answer to the parent, not the human's answer."""
    events = await collect(build_librarian(kb, delegating_script()), "T-final")

    assert events[-1] == RunEnd(run_id="run-1", final_text="All done.")


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_resumed_delegation_still_names_the_delegate_rt44(kb: Path) -> None:
    """The path a delegated approval actually takes: interrupt, human approves, resume.

    Nothing in the resumed stream replays the `task` call, and the delegate's namespace opens with
    a `HumanInTheLoopMiddleware.after_model` chunk that carries no metadata — so a naive normalizer
    labels the expert's write, and its own `subagent.end`, as the Librarian's.

    Superseded (Phase 3 rebuilds this): its whole vehicle is the interrupt/resume surface —
    `normalize_interrupts`, `Decision`, `to_resume_command` — retired with the gates (DESIGN.md §2:
    no gates, no parked proposals anywhere). The attribution-survives-a-resume principle needs a
    non-gate vehicle once there is a delegated action that pauses for something other than a gate.
    """
    graph = build_librarian(kb, delegating_script(), GATE)
    config = {"configurable": {"thread_id": "T-resume"}}
    await collect(graph, "T-resume")
    state = await graph.aget_state(config)
    request = normalize_interrupts(state.interrupts, agent_id="librarian", thread_id="T-resume")[0]

    resumed = [
        event
        async for event in stream_events(
            graph,
            to_resume_command(request, [Decision(type="approve")]),
            config,
            run_id="run-2",
            agent_id="librarian",
            thread_id="T-resume",
        )
    ]

    delegated = [
        e
        for e in resumed
        if isinstance(e, ToolStart | ToolEnd | SubagentEnd)
        or (isinstance(e, MessageComplete) and e.text.startswith("Filed"))
    ]
    assert delegated, "the delegate's work must be visible at all"
    assert {e.agent_id for e in delegated} == {DELEGATE}
    assert (kb / "Cooking" / "notes" / "steak.md").exists()


# --------------------------------------------------------------------------------------
# RT-41 — one approval, one event
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_delegated_interrupt_yields_exactly_one_event_rt41(kb: Path) -> None:
    """`subgraphs=True` emits `__interrupt__` twice — subgraph namespace and root — with one id.

    Superseded (Phase 3 rebuilds this): the subject is `InterruptEvent` normalization itself, the
    interrupt-resume surface DESIGN.md §2 retires wholesale (no gates, no parked proposals anywhere).
    """
    events = await collect(build_librarian(kb, delegating_script(), GATE), "T-interrupt")

    interrupts = [e for e in events if isinstance(e, InterruptEvent)]

    assert len(interrupts) == 1
    assert interrupts[0].request.agent_id == "librarian"
    assert interrupts[0].request.thread_id == "T-interrupt"
    assert [a.tool for a in interrupts[0].request.actions] == ["write_file"]


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_two_gated_writes_in_one_message_yield_one_event_rt41(kb: Path) -> None:
    """All interruptible calls in one AIMessage batch into a single interrupt with two actions.

    Superseded (Phase 3 rebuilds this): the batching guarantee is stated over `InterruptEvent`, the
    interrupt-resume surface DESIGN.md §2 retires wholesale (no gates, no parked proposals anywhere).
    """
    model = scripted(
        calls(
            call("write_file", {"file_path": "/kb/Cooking/notes/a.md", "content": "A"}, "w1"),
            call("write_file", {"file_path": "/kb/Cooking/notes/b.md", "content": "B"}, "w2"),
        ),
        says("done"),
    )
    graph = create_deep_agent(
        model=model,
        system_prompt="x",
        backend=backend_for(kb),
        interrupt_on=GATE,
        checkpointer=InMemorySaver(),
    )

    events = await collect(graph, "T-batch", agent_id="topic/cooking")

    interrupts = [e for e in events if isinstance(e, InterruptEvent)]
    assert len(interrupts) == 1
    assert [a.args["file_path"] for a in interrupts[0].request.actions] == [
        "/kb/Cooking/notes/a.md",
        "/kb/Cooking/notes/b.md",
    ]


def test_an_unidentifiable_subgraph_is_drained_not_dropped_rt44() -> None:
    """A delegate that never reaches its model is never named; its work must still be reported."""
    normalizer = EventNormalizer(run_id="r", agent_id="librarian", thread_id="T")
    message = AIMessage(
        content="",
        id="ai-1",
        tool_calls=[call("write_file", {"file_path": "/kb/a.md", "content": "x"}, "w1")],
    )

    immediate = normalizer.feed((("tools:unknown",), "updates", {"model": {"messages": [message]}}))
    drained = normalizer.drain()

    assert immediate == []
    assert drained == [
        ToolStart(run_id="r", agent_id="librarian", tool="write_file", summary="/kb/a.md")
    ]
    assert normalizer.drain() == []


@pytest.mark.superseded
def test_the_same_interrupt_id_is_never_reported_twice_rt41() -> None:
    """The dedupe is by id and spans namespaces — feeding both emissions yields one event.

    Superseded (Phase 3 rebuilds this): its vehicle is `EventNormalizer`'s `__interrupt__` dedupe,
    part of the interrupt-resume surface DESIGN.md §2 retires wholesale. SS-13's sibling
    `test_identical_events_are_not_coalesced_ss13` (server-side, `MessageDelta`) already shows the
    no-dedup-at-the-transport principle surviving with a non-interrupt vehicle; this file's own
    dedup-by-id rule needs an analogous non-interrupt vehicle if anything here still needs one.
    """
    from langgraph.types import Interrupt

    payload = {
        "action_requests": [{"name": "delete", "args": {"file_path": "/kb/a.md"}}],
        "review_configs": [{"action_name": "delete", "allowed_decisions": ["approve", "reject"]}],
    }
    normalizer = EventNormalizer(run_id="r", agent_id="librarian", thread_id="T")
    chunk_child = (
        ("tools:abc",),
        "updates",
        {"__interrupt__": (Interrupt(value=payload, id="x"),)},
    )
    chunk_root = ((), "updates", {"__interrupt__": (Interrupt(value=payload, id="x"),)})

    first = normalizer.feed(chunk_child)
    second = normalizer.feed(chunk_root)

    assert len(first) == 1
    assert second == []


# --------------------------------------------------------------------------------------
# RT-47 — failures are events, not exceptions
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_model_failure_yields_exactly_one_run_error_rt47(kb: Path) -> None:
    """One normalized `run.error`, no `RunEnd`, and the thread is left resumable."""
    graph = create_deep_agent(
        model=scripted(raises(RuntimeError("provider exploded"))),
        system_prompt="x",
        backend=backend_for(kb),
        checkpointer=InMemorySaver(),
    )

    events = await collect(graph, "T-error")

    errors = [e for e in events if isinstance(e, RunError)]
    assert len(errors) == 1
    assert "provider exploded" in errors[0].message
    assert not any(isinstance(e, RunEnd) for e in events)
    assert events[-1] is errors[0]


def test_transient_failures_are_flagged_retryable_rt47() -> None:
    """The flag is advice to a client's retry button; Layer 2 imports no provider SDK to decide."""
    assert is_retryable(TimeoutError("gone"))
    assert is_retryable(RuntimeError("Error code: 529 - overloaded_error"))
    assert not is_retryable(ValueError("invalid tool arguments"))
