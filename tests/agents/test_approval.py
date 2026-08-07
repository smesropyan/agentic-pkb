"""Approval normalization and resume construction — RT-40, RT-41, RT-42, RT-43.

The unit tests here build ``Interrupt`` values by hand; the end-to-end tests drive a real deep agent
so the four resume shapes are verified against the pin rather than against a reading of it. Every
one of them runs with no API key and no network: the model is
:class:`~tests.agents.conftest.ScriptedChatModel`.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Interrupt

from pkb.agents.approval import (
    DEFAULT_REASON,
    PROPOSE_ONLY_MESSAGE,
    normalize_interrupt,
    normalize_interrupts,
    propose_only_command,
    to_resume_command,
    validate_decisions,
)
from pkb.contracts import (
    ApprovalRequest,
    Decision,
    InvalidDecisionError,
    StaleInterruptError,
)
from tests.agents.conftest import call, calls, says, scripted

GATE = {
    "write_file": {
        "allowed_decisions": ["approve", "edit", "reject"],
        "description": lambda tool_call, state, runtime: f"REVIEW {tool_call['args']['file_path']}",
    }
}


def build_agent(kb: Path, model: Any, gate: dict[str, Any] = GATE) -> Any:
    """One gated deep agent over the fixture KB, with no middleware of its own.

    Deliberately built from `create_deep_agent` directly rather than from `pkb.agents.expert`: these
    rules are about the harness↔contracts translation, and must not fail because a sibling module's
    prompt or permission list changed.
    """
    backend = CompositeBackend(
        default=StateBackend(),
        routes={"/kb/": FilesystemBackend(root_dir=str(kb), virtual_mode=True)},
    )
    return create_deep_agent(
        model=model,
        system_prompt="test agent",
        backend=backend,
        interrupt_on=gate,
        checkpointer=InMemorySaver(),
    )


def interrupt_with(*actions: tuple[str, dict[str, Any], list[str]], id_: str = "i1") -> Interrupt:
    """A `HITLRequest`-shaped interrupt: the exact payload the grounding pass captured live."""
    return Interrupt(
        value={
            "action_requests": [
                {"name": name, "args": args, "description": f"REVIEW {name}"}
                for name, args, _ in actions
            ],
            "review_configs": [
                {"action_name": name, "allowed_decisions": allowed} for name, _, allowed in actions
            ],
        },
        id=id_,
    )


async def run_to_interrupt(agent: Any, thread_id: str) -> ApprovalRequest:
    """Run one turn, then normalize whatever the thread is now waiting on."""
    config = {"configurable": {"thread_id": thread_id}}
    await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, config)
    state = await agent.aget_state(config)
    requests = normalize_interrupts(state.interrupts, agent_id="librarian", thread_id=thread_id)
    assert len(requests) == 1
    return requests[0]


# --------------------------------------------------------------------------------------
# RT-41 — normalization
# --------------------------------------------------------------------------------------


def test_batched_calls_become_one_request_with_aligned_actions_rt41() -> None:
    """Two gated calls in one AIMessage are one interrupt with two positionally aligned actions."""
    interrupt = interrupt_with(
        ("write_file", {"file_path": "/kb/a.md", "content": "A"}, ["approve", "reject"]),
        ("delete", {"file_path": "/kb/b.md"}, ["approve", "reject"]),
    )

    request = normalize_interrupt(interrupt, agent_id="librarian", thread_id="T")

    assert request.interrupt_id == "i1"
    assert [action.tool for action in request.actions] == ["write_file", "delete"]
    assert request.actions[0].args["file_path"] == "/kb/a.md"
    assert request.actions[1].args["file_path"] == "/kb/b.md"
    assert request.actions[0].allowed_decisions == ("approve", "reject")


def test_repeated_interrupt_id_yields_one_request_rt41() -> None:
    """The same approval seen twice — the delegated case — is one request, not two.

    `subgraphs=True` emits a delegated interrupt under both `('tools:<uuid>',)` and `()`, and
    `aget_state` exposes it on both `.interrupts` and `.tasks[0].interrupts`.
    """
    one = interrupt_with(("write_file", {"file_path": "/kb/a.md"}, ["approve"]), id_="same")
    again = interrupt_with(("write_file", {"file_path": "/kb/a.md"}, ["approve"]), id_="same")

    requests = normalize_interrupts([one, again], agent_id="librarian", thread_id="T")

    assert len(requests) == 1


@pytest.mark.asyncio
async def test_delegated_approval_names_the_parent_thread_rt41(kb: Path) -> None:
    """An `ApprovalRequest` carries the *run's* agent and thread, because resume routes by thread.

    A gated write inside a delegated expert propagates to the parent's thread (LB-10); handing a
    client the delegate's id would send the resume somewhere that is not interrupted.
    """
    model = scripted(
        calls(call("write_file", {"file_path": "/kb/Cooking/notes/s.md", "content": "D"}, "w1")),
        says("done"),
    )
    request = await run_to_interrupt(build_agent(kb, model), "T-parent")

    assert request.agent_id == "librarian"
    assert request.thread_id == "T-parent"


# --------------------------------------------------------------------------------------
# RT-40 — validation before the graph is touched
# --------------------------------------------------------------------------------------


def test_stale_interrupt_id_is_refused_rt40() -> None:
    """Decisions naming a different interrupt are refused; the harness would say nothing useful."""
    pending = normalize_interrupt(
        interrupt_with(("write_file", {}, ["approve"]), id_="current"),
        agent_id="librarian",
        thread_id="T",
    )

    with pytest.raises(StaleInterruptError) as excinfo:
        validate_decisions(pending, [Decision(type="approve")], interrupt_id="old")

    assert "old" in str(excinfo.value)
    assert "current" in str(excinfo.value)


def test_nothing_pending_is_refused_rt40() -> None:
    """Resuming a thread that is not interrupted is stale, not a count mismatch."""
    with pytest.raises(StaleInterruptError):
        validate_decisions(None, [Decision(type="approve")], interrupt_id="gone")


def test_decision_count_mismatch_is_refused_rt40() -> None:
    """One decision for two batched actions — the harness raises a bare ValueError in-graph."""
    pending = normalize_interrupt(
        interrupt_with(
            ("write_file", {}, ["approve"]),
            ("delete", {}, ["approve"]),
        ),
        agent_id="librarian",
        thread_id="T",
    )

    with pytest.raises(InvalidDecisionError) as excinfo:
        validate_decisions(pending, [Decision(type="approve")])

    assert "2" in str(excinfo.value)


def test_disallowed_decision_type_is_refused_rt40() -> None:
    """`respond` against `["approve","reject"]` — the shape RT-32 forbids on a KB write gate."""
    pending = normalize_interrupt(
        interrupt_with(("write_file", {}, ["approve", "reject"])),
        agent_id="librarian",
        thread_id="T",
    )

    with pytest.raises(InvalidDecisionError) as excinfo:
        validate_decisions(pending, [Decision(type="respond", message="hi")])

    assert "respond" in str(excinfo.value)


def test_respond_without_a_message_is_refused_rt40() -> None:
    """`_process_decision` reads `decision["message"]` unconditionally and would KeyError in-graph."""
    pending = normalize_interrupt(
        interrupt_with(("ask_human", {}, ["approve", "respond"])),
        agent_id="librarian",
        thread_id="T",
    )

    with pytest.raises(InvalidDecisionError):
        validate_decisions(pending, [Decision(type="respond")])


@pytest.mark.asyncio
async def test_refused_decisions_leave_the_thread_interrupted_rt40(kb: Path) -> None:
    """The graph is never invoked, so the original approval is still resolvable afterwards."""
    model = scripted(
        calls(call("write_file", {"file_path": "/kb/Cooking/notes/s.md", "content": "D"}, "w1")),
        says("done"),
    )
    agent = build_agent(kb, model)
    config = {"configurable": {"thread_id": "T-refuse"}}
    request = await run_to_interrupt(agent, "T-refuse")

    with pytest.raises(InvalidDecisionError):
        to_resume_command(request, [])

    state = await agent.aget_state(config)
    assert [i.id for i in state.interrupts] == [request.interrupt_id]
    assert not (kb / "Cooking" / "notes" / "s.md").exists()


# --------------------------------------------------------------------------------------
# RT-40/RT-41 — the four resume shapes, end to end
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_resume_performs_the_write_rt40(kb: Path) -> None:
    model = scripted(
        calls(call("write_file", {"file_path": "/kb/Cooking/notes/s.md", "content": "OK"}, "w1")),
        says("filed"),
    )
    agent = build_agent(kb, model)
    config = {"configurable": {"thread_id": "T-approve"}}
    request = await run_to_interrupt(agent, "T-approve")

    await agent.ainvoke(to_resume_command(request, [Decision(type="approve")]), config)

    assert (kb / "Cooking" / "notes" / "s.md").read_text() == "OK"


@pytest.mark.asyncio
async def test_edit_resume_performs_the_humans_version_rt40(kb: Path) -> None:
    """`edit` replaces the whole action, so the tool name is resent from the request."""
    model = scripted(
        calls(call("write_file", {"file_path": "/kb/Cooking/notes/s.md", "content": "AI"}, "w1")),
        says("filed"),
    )
    agent = build_agent(kb, model)
    config = {"configurable": {"thread_id": "T-edit"}}
    request = await run_to_interrupt(agent, "T-edit")

    command = to_resume_command(
        request,
        [
            Decision(
                type="edit",
                edited_args={"file_path": "/kb/Cooking/notes/s.md", "content": "HUMAN"},
            )
        ],
    )
    await agent.ainvoke(command, config)

    assert (kb / "Cooking" / "notes" / "s.md").read_text() == "HUMAN"


@pytest.mark.asyncio
async def test_reject_resume_leaves_the_tree_untouched_rt40(kb: Path) -> None:
    model = scripted(
        calls(call("write_file", {"file_path": "/kb/Cooking/notes/s.md", "content": "AI"}, "w1")),
        says("understood"),
    )
    agent = build_agent(kb, model)
    config = {"configurable": {"thread_id": "T-reject"}}
    request = await run_to_interrupt(agent, "T-reject")

    result = await agent.ainvoke(
        to_resume_command(request, [Decision(type="reject", message="wrong voice")]), config
    )

    assert not (kb / "Cooking" / "notes" / "s.md").exists()
    assert any(getattr(m, "content", None) == "wrong voice" for m in result["messages"])


@pytest.mark.asyncio
async def test_respond_resume_skips_the_tool_rt32(kb: Path) -> None:
    """`respond` yields a *success* ToolMessage with nothing written — which is why RT-32 keeps it
    off every KB write gate."""
    model = scripted(
        calls(call("write_file", {"file_path": "/kb/Cooking/notes/s.md", "content": "AI"}, "w1")),
        says("ok"),
    )
    gate = {"write_file": {"allowed_decisions": ["approve", "reject", "respond"]}}
    agent = build_agent(kb, model, gate)
    config = {"configurable": {"thread_id": "T-respond"}}
    request = await run_to_interrupt(agent, "T-respond")

    result = await agent.ainvoke(
        to_resume_command(request, [Decision(type="respond", message="I filed it myself")]), config
    )

    assert not (kb / "Cooking" / "notes" / "s.md").exists()
    tool_messages = [m for m in result["messages"] if m.type == "tool"]
    assert [m.status for m in tool_messages] == ["success"]


# --------------------------------------------------------------------------------------
# RT-42 — propose-only
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_only_rejects_every_action_and_writes_nothing_rt42(kb: Path) -> None:
    """The one Layer-2-authored decision: an MCP caller cannot satisfy a gate, so it never hangs."""
    model = scripted(
        calls(call("write_file", {"file_path": "/kb/Cooking/notes/s.md", "content": "AI"}, "w1")),
        says("noted"),
    )
    agent = build_agent(kb, model)
    config = {"configurable": {"thread_id": "T-propose"}}
    request = await run_to_interrupt(agent, "T-propose")

    result = await agent.ainvoke(propose_only_command(request), config)
    state = await agent.aget_state(config)

    assert not (kb / "Cooking" / "notes" / "s.md").exists()
    assert state.interrupts == ()
    assert any(getattr(m, "content", None) == PROPOSE_ONLY_MESSAGE for m in result["messages"])


# --------------------------------------------------------------------------------------
# RT-43 — the seam carries primitives only
# --------------------------------------------------------------------------------------


def test_approval_request_is_json_serializable_rt43() -> None:
    """It goes onto an SSE stream and into a Telegram keyboard; nothing harness-shaped may cross."""
    interrupt = interrupt_with(
        (
            "edit_file",
            {"file_path": "/kb/a.md", "old_string": "x", "new_string": "y", "replace_all": False},
            ["approve", "edit", "reject"],
        )
    )

    request = normalize_interrupt(interrupt, agent_id="topic/cooking", thread_id="T")
    encoded = json.dumps(dataclasses.asdict(request))

    assert json.loads(encoded)["actions"][0]["args"]["replace_all"] == "False"
    assert dataclasses.is_dataclass(request)


def test_action_reason_comes_from_the_injected_resolver_rt34() -> None:
    """`ActionRequest` has no field for a gate reason (RT-34), so the runtime injects the resolver."""
    interrupt = interrupt_with(("write_file", {"file_path": "/kb/a/notes/summary.md"}, ["approve"]))

    default = normalize_interrupt(interrupt, agent_id="a", thread_id="T")
    resolved = normalize_interrupt(
        interrupt, agent_id="a", thread_id="T", reason_for=lambda tool, args: "breadth-approval"
    )

    assert default.actions[0].reason == DEFAULT_REASON
    assert resolved.actions[0].reason == "breadth-approval"
