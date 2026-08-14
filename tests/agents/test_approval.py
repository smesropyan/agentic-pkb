"""Approval normalization and resume construction — RT-40, RT-41, RT-42, RT-43, RT-30, §5.1.

The unit tests here build ``Interrupt`` values by hand; the end-to-end tests drive a real deep agent
so the four resume shapes are verified against the pin rather than against a reading of it. Every
one of them runs with no API key and no network: the model is
:class:`~tests.agents.conftest.ScriptedChatModel`.

Two sections deliberately reach further than the rest:

* **§5.1 / arch §6** — :func:`pkb.contracts.validate_decisions` is the one copy of "which decisions
  is this action allowed" that the TUI and the Telegram adapter both use, so it is asserted from
  the *seam* with the harness banned from the interpreter, not only through this package.
* **RT-30** — the delete gate's rule has three clauses and only the first ("it gates") can be
  answered by the table in ``test_gates.py``. "Approving removes it, rejecting leaves it" is an
  effect on the tree, so it is driven through a real :func:`~pkb.agents.expert.build_expert` graph
  here, where the rest of this module's decision-effect assertions live.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Interrupt

import pkb.contracts as contracts_module
from pkb.agents.approval import (
    DEFAULT_REASON,
    PROPOSE_ONLY_MESSAGE,
    normalize_interrupt,
    normalize_interrupts,
    propose_only_command,
    to_resume_command,
    validate_decisions,
)
from pkb.agents.expert import build_expert
from pkb.agents.middleware.maintenance import NULL_WRITE_LOCK
from pkb.agents.paths import KB_MOUNT, SKILLS_MOUNT
from pkb.agents.skills import packaged_skills_root
from pkb.contracts import (
    ApprovalRequest,
    Decision,
    FlushReport,
    InvalidDecisionError,
    StaleInterruptError,
)
from pkb.core import KbSnapshot, regenerate_all
from pkb.core.scan import scan
from tests.agents.conftest import TODAY, call, calls, says, scripted

HARNESS = ("deepagents", "langgraph", "langchain", "langchain_core")
"""The modules a transport may never load (I2). Same list `test_contracts.py` bans."""

GATE = {
    "write_file": {
        "allowed_decisions": ["approve", "edit", "reject"],
        "description": lambda tool_call, state, runtime: f"REVIEW {tool_call['args']['file_path']}",
    }
}

pytestmark = pytest.mark.superseded
"""Superseded (Phase 3 rebuilds this): every test in this file exercises the interrupt/resume
surface directly — `normalize_interrupt(s)`, `validate_decisions`, `to_resume_command`,
`propose_only_command`, `ApprovalRequest`, `Decision`, `InvalidDecisionError`, `StaleInterruptError`
— all retired with the gates (DESIGN.md §2: no gates, no parked proposals, no pending queue
anywhere; the operator's instruction is the approval). Task 6 turns the gate composition off at the
point `build_expert`/`build_librarian` call into `create_deep_agent`, so nothing in production ever
produces an `Interrupt` for this module to normalize; RT-30's own delete-gate rule ("approving
removes it, rejecting leaves it") has no gate left to drive it through, and RT-42's propose-only mode
is one of the three named-retired approval modes (`propose_only`/`interactive`) outright. Whole-file
marked because every test here is centrally about the approval/gate mechanism — normalizing an
interrupt, validating a decision, building a resume command, or driving RT-30's delete gate end to
end — not merely a test that happens to construct one alongside other machinery. §5.1's own
principle (one shared decision validator, reachable from the seam with the harness banned) is
permanent, but its subject — a `Decision` against an `ApprovalRequest` — is exactly what is retired,
so it has nothing left to validate until a successor approval shape exists, if one ever does; the
plan is silent beyond "the operator's instruction is the approval."
"""

NOTE_REL = "Cooking/notes/reverse-sear.md"
NOTE_PATH = f"{KB_MOUNT}{NOTE_REL}"
VALID_NOTE = """---
title: "Reverse sear"
description: "Low oven first, then a very hot pan, for a thick steak"
topic: "Cooking"
tags:
  - topic.cooking
  - type.note
  - status.draft
created: 2026-08-01
updated: 2026-08-01
source_type: note
---

# Reverse sear

Low oven, then a hot pan.
"""


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


class ExpertRuntime:
    """The shared singletons `build_expert` wants (`pkb.agents.expert.GraphRuntime`), in-memory.

    Structural, so this file needs no import from `pkb.agents.runtime`. `snapshot()` rescans on every
    call rather than caching: the RT-30 test deletes a file mid-thread, and a cached snapshot would
    make the post-delete gate decisions answer a question about a tree that no longer exists.
    """

    def __init__(self, kb_root: Path) -> None:
        self.kb_root = kb_root
        self.backend = CompositeBackend(
            default=StateBackend(),
            routes={
                KB_MOUNT: FilesystemBackend(root_dir=str(kb_root), virtual_mode=True),
                SKILLS_MOUNT: FilesystemBackend(
                    root_dir=str(packaged_skills_root()), virtual_mode=True
                ),
            },
        )
        self.checkpointer = InMemorySaver()
        self.store = None
        self.write_lock = NULL_WRITE_LOCK
        self.scan_queue = None
        self.reports: list[FlushReport] = []
        self.flush_sink: Callable[[FlushReport], None] | None = self.reports.append
        self.clock: Callable[[], date] = lambda: TODAY

    def snapshot(self) -> KbSnapshot:
        return scan(self.kb_root)


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


# --------------------------------------------------------------------------------------
# §5.1 / arch §6 — the shared decision validator lives in the seam
# --------------------------------------------------------------------------------------

SEAM_VALIDATOR_DRIVER = '''
"""Validate a human's decision using `pkb.contracts` alone, with the harness banned."""

import importlib.abc
import sys

BANNED = {banned!r}


class Ban(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in BANNED:
            raise ImportError(f"the harness is banned in this process: {{fullname}}")
        return None


sys.meta_path.insert(0, Ban())

from pkb.contracts import (  # noqa: E402
    ActionView,
    ApprovalRequest,
    Decision,
    InvalidDecisionError,
    StaleInterruptError,
    validate_decisions,
)

pending = ApprovalRequest(
    interrupt_id="current",
    agent_id="topic/cooking",
    thread_id="T",
    actions=(
        ActionView(
            tool="delete",
            args={{"file_path": "/kb/Cooking/notes/reverse-sear.md"}},
            description="Approval required: delete",
            allowed_decisions=("approve", "reject"),
            reason="delete",
        ),
    ),
)


def refuses(error, *args, **kwargs):
    try:
        validate_decisions(*args, **kwargs)
    except error:
        return
    raise AssertionError(f"expected {{error.__name__}} from {{args!r}} {{kwargs!r}}")


refuses(InvalidDecisionError, pending, [])                                    # wrong count
refuses(InvalidDecisionError, pending, [Decision(type="approve")] * 2)        # wrong count
refuses(InvalidDecisionError, pending, [Decision(type="respond", message="done")])  # not allowed
refuses(InvalidDecisionError, pending, [Decision(type="edit")])               # not allowed
refuses(StaleInterruptError, pending, [Decision(type="approve")], interrupt_id="old")
refuses(StaleInterruptError, None, [Decision(type="approve")], interrupt_id="gone")

assert validate_decisions(pending, [Decision(type="approve")]) is pending
assert validate_decisions(pending, [Decision(type="approve")], interrupt_id="current") is pending

leaked = sorted(name for name in sys.modules if name.split(".")[0] in BANNED)
assert leaked == [], leaked
print("OK")
'''


def test_the_shared_decision_validator_lives_in_the_seam_51(tmp_path: Path) -> None:
    """A transport must be able to validate a decision without importing the harness (§5.1, I2).

    §5.1 declares `validate_decisions` inside `pkb/contracts.py` — "shared with
    `pkb.clients.approval`" — because arch §6 gives the TUI and the Telegram adapter *one* answer to
    "which decisions is this action allowed", and a second copy in a client is the duplication that
    section exists to prevent. Defined in `pkb.agents.approval` it is unreachable from a transport:
    that module imports `langgraph.types`, which I2 bans outright.

    Run in a subprocess whose `sys.meta_path` refuses every harness root, so the assertion is that
    the function *is importable there*, not that someone remembered to keep it importable. The three
    refusals RT-40 names — wrong decision count, a type the action does not allow, a stale interrupt
    id — are exercised through that import, which is also the only proof that the typed errors and
    the validator can be reached from the same harness-free namespace.
    """
    driver = tmp_path / "seam_validator_driver.py"
    driver.write_text(SEAM_VALIDATOR_DRIVER.format(banned=HARNESS), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(driver)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("OK")


def test_the_agent_layer_re_exports_the_seams_validator_rather_than_copying_it_51() -> None:
    """One function, two import paths — never two functions (arch §6).

    `runtime.resume` imports it from `pkb.agents.approval` and a client will import it from
    `pkb.contracts`; identity is what makes "the two channels validate identically" a fact rather
    than a convention. A copy would satisfy every other test in this file.
    """
    assert validate_decisions is contracts_module.validate_decisions
    assert "validate_decisions" in contracts_module.__all__


# --------------------------------------------------------------------------------------
# RT-30 — a delete gates, and the human's decision is final
# --------------------------------------------------------------------------------------


def _seed_note(kb: Path) -> bytes:
    """Put one authored note on disk and regenerate, so `Cooking/index.md` lists it."""
    target = kb / NOTE_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(VALID_NOTE, encoding="utf-8")
    regenerate_all(kb)
    assert "reverse-sear" in (kb / "Cooking" / "index.md").read_text(encoding="utf-8")
    return target.read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_a_kb_delete_gates_and_the_decision_is_final_rt30(kb: Path, decision: str) -> None:
    """RT-30 in full: the delete gates, approving removes it, rejecting leaves it.

    Driven through a real `build_expert` graph rather than through this module's bare
    `build_agent`, because two of RT-30's three clauses are effects on the tree that a literal
    `interrupt_on` dict cannot produce: the gate text comes from `gates.describe_write`'s delete
    branch (the sentence a human reads before an irreversible delete — otherwise executed by no test
    in the suite), and the index effect comes from `KbMaintenanceMiddleware`'s flush.

    The reject case asserts *bytes*, not existence: a flush that ran over a rejected delete and
    re-stamped the file would leave it present and still be a loss of the human's content.
    """
    original = _seed_note(kb)
    model = scripted(calls(call("delete", {"file_path": NOTE_PATH}, "d1")), says("done"))
    agent = build_expert(kb, "Cooking", ExpertRuntime(kb), model=model)
    config = {"configurable": {"thread_id": f"T-delete-{decision}"}}

    await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, config)
    state = await agent.aget_state(config)
    requests = normalize_interrupts(
        state.interrupts,
        agent_id="topic/cooking",
        thread_id=str(config["configurable"]["thread_id"]),
    )

    assert len(requests) == 1, "a KB delete must stop for a human exactly once (RT-30)"
    action = requests[0].actions[0]
    assert action.tool == "delete"
    assert action.allowed_decisions == ("approve", "reject"), "a delete cannot be edited into one"
    assert "delete — permanent, there is no undo" in action.description
    assert (kb / NOTE_REL).read_bytes() == original, "the file moved before the human decided"

    await agent.ainvoke(to_resume_command(requests[0], [Decision(type=decision)]), config)

    index = (kb / "Cooking" / "index.md").read_text(encoding="utf-8")
    if decision == "approve":
        assert not (kb / NOTE_REL).exists(), "an approved delete did not remove the file"
        assert "reverse-sear" not in index, "the flush left the deleted note in the topic index"
    else:
        assert (kb / NOTE_REL).read_bytes() == original, "a rejected delete altered the file"
        assert "reverse-sear" in index, "a rejected delete dropped the note from the topic index"
