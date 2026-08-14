"""`KbValidationMiddleware` — MW-1 … MW-19, plus RT-9/RT-10 at the middleware's own door.

Two kinds of test, deliberately:

* **Graph tests** compile a real `create_deep_agent` over a real knowledge base and assert what is
  on disk, what the model was told, and what the checkpoint holds afterwards. Inspecting a decision
  proves the decision; only a run proves the guarantee — and arch §7's whole claim is about a run.
* **Unit tests** call `wrap_tool_call` directly with a spy handler. That is the only way to assert
  the thing MW-13 actually promises: not "the write failed", but "the handler was never invoked",
  so the bytes never reached the backend at all.

Everything runs with no API key and no network (SK-18): `ScriptedChatModel` drives the graph.
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from pkb.agents.middleware import validation as validation_module
from pkb.agents.middleware.state import KB_ATTEMPTS, KB_TOUCHED, KbAgentState
from pkb.agents.middleware.validation import (
    ADVISORY_HEADER,
    MAX_ATTEMPTS,
    KbValidationMiddleware,
)
from pkb.agents.paths import KB_MOUNT
from pkb.agents.permissions import kb_permissions
from pkb.core import errors_only, flush, render_findings, validate_content
from tests.agents.conftest import TODAY, ScriptedChatModel, call, calls, says, scripted

COOKING = "Cooking"
NOTE_REL = "Cooking/notes/reverse-sear.md"
NOTE_PATH = f"{KB_MOUNT}{NOTE_REL}"

# A note that Layer 1 accepts with zero findings at `notes/reverse-sear.md`.
CLEAN_NOTE = """---
title: "Reverse sear"
description: "Low oven then a hot pan for an even steak crust"
topic: "Cooking"
tags:
  - topic.cooking
  - type.note
created: 2026-08-06
updated: 2026-08-06
source_type: note
---

# Reverse sear

Start the steak in a low oven, then finish it in a screaming-hot pan.
"""

# The same bytes at a file name that diverges from the title: exactly one finding, VA-35, at
# *warning* severity — one of the four severities MW-12 exists to keep out of the attempt budget.
WARNING_NOTE_REL = "Cooking/notes/sear.md"
WARNING_NOTE_PATH = f"{KB_MOUNT}{WARNING_NOTE_REL}"

NO_FRONTMATTER = "This note has no frontmatter block at all.\n"


# --------------------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------------------


class FlushSpy(AgentMiddleware[KbAgentState]):
    """Stands in for `KbMaintenanceMiddleware`: the `after_agent` chain, doing the real flush.

    The escalation rules (MW-15/MW-16) are only meaningful if the derived files are still
    regenerated on the way out, so the spy calls `pkb.core.flush` for real and records the touched
    set it was handed. `reports` is empty when the exit chain never ran.
    """

    state_schema = KbAgentState

    def __init__(self, kb_root: Path) -> None:
        super().__init__()
        self.kb_root = kb_root
        self.reports: list[list[str]] = []

    def after_agent(self, state: KbAgentState, runtime: Any) -> dict[str, Any] | None:
        touched = list(state.get(KB_TOUCHED) or [])
        self.reports.append(touched)
        flush(self.kb_root, touched, today=TODAY)
        return {KB_TOUCHED: None}

    async def aafter_agent(self, state: KbAgentState, runtime: Any) -> dict[str, Any] | None:
        return self.after_agent(state, runtime)


def build_agent(
    kb: Path,
    model: ScriptedChatModel,
    *,
    topic_path: str | None = COOKING,
    permissions: bool = True,
    flush_spy: FlushSpy | None = None,
    middleware: KbValidationMiddleware | None = None,
    interrupt_on: dict[str, Any] | None = None,
) -> Any:
    """A deep agent wired the way the runtime wires one (RT-6, EX-14) with the validator attached."""
    stack: list[AgentMiddleware[Any]] = [middleware or KbValidationMiddleware(kb)]
    if flush_spy is not None:
        stack.append(flush_spy)
    return create_deep_agent(
        model=model,
        backend=CompositeBackend(
            default=StateBackend(),
            routes={KB_MOUNT: FilesystemBackend(root_dir=str(kb), virtual_mode=True)},
        ),
        permissions=kb_permissions(topic_path) if permissions else None,
        middleware=stack,
        interrupt_on=interrupt_on,
        system_prompt="File what you are told.",
        checkpointer=InMemorySaver(),
    )


def run(agent: Any, thread: str = "t1", text: str = "do it") -> dict[str, Any]:
    return dict(agent.invoke({"messages": [HumanMessage(text)]}, config(thread)))


def arun(agent: Any, thread: str = "t1", text: str = "do it") -> dict[str, Any]:
    """The async path (MW-2). The production runtime never calls `invoke` at all (RT-3)."""
    return dict(asyncio.run(agent.ainvoke({"messages": [HumanMessage(text)]}, config(thread))))


DRIVERS: tuple[Callable[[Any], dict[str, Any]], ...] = (run, arun)


def config(thread: str = "t1") -> dict[str, Any]:
    return {"configurable": {"thread_id": thread}}


def tool_messages(result: Mapping[str, Any]) -> list[ToolMessage]:
    messages: Sequence[BaseMessage] = result["messages"]
    return [m for m in messages if isinstance(m, ToolMessage)]


def write(path: str, content: str, id_: str) -> dict[str, Any]:
    return call("write_file", {"file_path": path, "content": content}, id_)


def edit(path: str, old: str, new: str, id_: str) -> dict[str, Any]:
    return call("edit_file", {"file_path": path, "old_string": old, "new_string": new}, id_)


class SpyHandler:
    """A `wrap_tool_call` handler that records whether it was reached at all (MW-13)."""

    def __init__(self, result: ToolMessage | Command[Any] | None = None) -> None:
        self.calls: list[ToolCallRequest] = []
        self.result = result

    def __call__(self, request: ToolCallRequest) -> ToolMessage | Command[Any]:
        self.calls.append(request)
        if self.result is not None:
            return self.result
        return ToolMessage(
            content="Updated file",
            name=request.tool_call["name"],
            tool_call_id=request.tool_call["id"],
        )


def request_for(
    tool: str, args: Mapping[str, Any], *, state: Mapping[str, Any] | None = None, id_: str = "c1"
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": tool, "args": dict(args), "id": id_, "type": "tool_call"},
        tool=None,
        state=dict(state or {"messages": []}),
        runtime=None,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------------------
# MW-1 / MW-4 / MW-5 — shape
# --------------------------------------------------------------------------------------

CORE_STACK_NAMES = frozenset(
    {
        "FilesystemMiddleware",
        "SubAgentMiddleware",
        "SkillsMiddleware",
        "SummarizationMiddleware",
        "PatchToolCallsMiddleware",
        "MemoryMiddleware",
        "HumanInTheLoopMiddleware",
    }
)


def test_the_middleware_occupies_its_own_slot_in_the_compiled_stack_mw1(kb: Path) -> None:
    """MW-1/EX-15: `_apply_custom_middleware` merges by `.name`, and a collision *replaces* the
    core member in place rather than appending — so a name clash would silently delete the
    filesystem tools this middleware exists to guard."""
    middleware = KbValidationMiddleware(kb)
    assert middleware.name == "KbValidationMiddleware"
    assert middleware.name not in CORE_STACK_NAMES

    agent = build_agent(kb, scripted(says("nothing to do")))
    hooks = sorted(n for n in agent.nodes if n.startswith("KbValidationMiddleware."))
    assert hooks == ["KbValidationMiddleware.after_model", "KbValidationMiddleware.before_agent"]

    tools = set(agent.nodes["tools"].bound.tools_by_name)  # type: ignore[attr-defined]
    assert {"write_file", "edit_file", "delete", "read_file"} <= tools


@pytest.mark.parametrize("drive", DRIVERS, ids=["sync", "async"])
def test_both_hook_variants_refuse_and_record_mw2(
    kb: Path, drive: Callable[[Any], dict[str, Any]]
) -> None:
    """MW-2: a sync-only `wrap_tool_call` raises `NotImplementedError` under `ainvoke()`, and the
    daemon is async-only (RT-3) while this suite is sync. Both variants must reject the invalid
    write, let the valid one through, and reach the `after_agent` flush."""
    spy = FlushSpy(kb)
    model = scripted(
        calls(write(NOTE_PATH, NO_FRONTMATTER, "c1")),
        calls(write(WARNING_NOTE_PATH, CLEAN_NOTE, "c2")),
        says("one refused, one filed"),
    )
    result = drive(build_agent(kb, model, flush_spy=spy))

    assert [m.status for m in tool_messages(result)] == ["error", "success"]
    assert not (kb / NOTE_REL).exists()
    assert (kb / WARNING_NOTE_REL).exists()
    assert spy.reports == [[WARNING_NOTE_REL]]


def test_the_async_gate_hands_layer_one_to_a_worker_thread_mw3(kb: Path) -> None:
    """MW-3: the decision walks the tree and reads files. On the event loop that stalls every other
    run in the daemon, so the blocking half goes to `asyncio.to_thread`."""
    model = scripted(calls(write(NOTE_PATH, NO_FRONTMATTER, "c1")), says("done"))
    agent = build_agent(kb, model)

    with patch.object(validation_module.asyncio, "to_thread", wraps=asyncio.to_thread) as to_thread:
        arun(agent)

    assert to_thread.call_count >= 1


def test_configuration_is_the_only_instance_state_mw4(kb: Path) -> None:
    """MW-4: one instance serves every run of a compiled graph (and every parallel delegation
    inside one Librarian turn, LB-8), so anything per-run written to `self` is a cross-run leak."""
    middleware = KbValidationMiddleware(kb)
    before = dict(vars(middleware))

    model = scripted(
        calls(write(NOTE_PATH, NO_FRONTMATTER, "c1")),
        says("first"),
        calls(write(NOTE_PATH, CLEAN_NOTE, "c2")),
        says("second"),
    )
    agent = build_agent(kb, model, middleware=middleware)
    run(agent, "t1")
    run(agent, "t2")

    assert dict(vars(middleware)) == before


def test_the_state_schema_carries_the_shared_kb_keys_mw5(kb: Path) -> None:
    """MW-5: langchain merges every middleware's `state_schema` into the resolved graph schema.
    Declaring it nowhere loses both keys and the counter silently never accumulates."""
    assert KbValidationMiddleware.state_schema is KbAgentState

    model = scripted(
        calls(
            write(NOTE_PATH, NO_FRONTMATTER, "c1"),
            write(f"{KB_MOUNT}Cooking/notes/other.md", NO_FRONTMATTER, "c2"),
        ),
        says("both refused"),
    )
    agent = build_agent(kb, model)
    run(agent)

    assert agent.get_state(config()).values[KB_ATTEMPTS] == {
        NOTE_REL: 1,
        "Cooking/notes/other.md": 1,
    }


# --------------------------------------------------------------------------------------
# MW-7 / MW-8 — what the middleware is about
# --------------------------------------------------------------------------------------


def test_a_read_tool_is_forwarded_and_records_nothing_mw7(kb: Path) -> None:
    """MW-7: exactly `write_file` and `edit_file` are validated. Everything else passes through."""
    handler = SpyHandler()
    middleware = KbValidationMiddleware(kb)

    result = middleware.wrap_tool_call(
        request_for("read_file", {"file_path": f"{KB_MOUNT}Cooking/topic.md"}), handler
    )

    assert len(handler.calls) == 1
    assert isinstance(result, ToolMessage)  # no state update, so no touched path


def test_a_scratch_path_is_forwarded_with_zero_validation_calls_mw8(kb: Path) -> None:
    """MW-8: anything outside the KB mount belongs to the thread-scoped `StateBackend`. Validating
    an agent's scratch file against knowledge-base rules would refuse every one of them.

    The knowledge-base write in the same run is the positive control: it proves the spy is wired
    and that "zero calls" is a fact about the scratch path, not about the patch."""
    model = scripted(
        calls(
            write("/scratch/plan.md", "just thinking\n", "c1"),
            write(NOTE_PATH, CLEAN_NOTE, "c2"),
        ),
        says("done"),
    )
    agent = build_agent(kb, model)

    with patch.object(validation_module, "validate_content", wraps=validate_content) as validator:
        result = run(agent)

    assert validator.call_count == 1
    assert [c.args[1] for c in validator.call_args_list] == [NOTE_REL]
    assert [m.status for m in tool_messages(result)] == ["success", "success"]
    assert not (kb / "scratch").exists()


def test_layer_one_is_consulted_through_exactly_one_call_site_mw9() -> None:
    """MW-9: required fields, tag syntax and depth, naming and location are entirely Layer 1's. A
    second opinion here would drift, and the agent would be told two different things."""
    source = Path(validation_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    call_sites = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "validate_content"
    ]
    assert len(call_sites) == 1

    # Whole-file grep, prose included: the derived and skill file classes are Layer 1's vocabulary,
    # and a Layer 2 module that names one has started keeping its own copy of the rules.
    for banned in (
        "REQUIRED_FIELDS",
        "yaml",
        "frontmatter",
        "re.compile",
        "index.md",
        "tags.md",
        "SKILL.md",
    ):
        assert banned not in source


# --------------------------------------------------------------------------------------
# MW-10 — edit_file carries no content (D-4)
# --------------------------------------------------------------------------------------


def test_an_edit_is_validated_against_the_resulting_file_mw10(kb: Path) -> None:
    """MW-10: `edit_file`'s args are `old_string`/`new_string` — the *fragment*. Validating the
    fragment would pass anything; the post-edit text is reconstructed with the very function the
    tool itself uses, so the simulation cannot diverge from the write."""
    target = kb / NOTE_REL
    target.write_text(CLEAN_NOTE, encoding="utf-8")
    before = target.read_bytes()

    model = scripted(
        calls(
            edit(NOTE_PATH, "  - topic.cooking\n", "  - topic.cooking.grilling.gas.searing\n", "c1")
        ),
        says("blocked"),
    )
    result = run(build_agent(kb, model))

    (message,) = tool_messages(result)
    assert message.status == "error"
    assert "TAG_DEPTH_EXCEEDED" in str(message.content)
    assert target.read_bytes() == before


def test_an_absent_old_string_yields_the_harness_error_not_a_finding_mw10(kb: Path) -> None:
    """MW-10: a zero-occurrence or non-unique match is deepagents' own error. Layer 2 forwards to
    the handler and never re-words it — one failure, one message, written by its owner (RT-10)."""
    target = kb / NOTE_REL
    target.write_text(CLEAN_NOTE, encoding="utf-8")

    model = scripted(calls(edit(NOTE_PATH, "text that is not there", "x", "c1")), says("ok"))
    result = run(build_agent(kb, model))

    (message,) = tool_messages(result)
    assert message.status == "error"
    assert "String not found" in str(message.content)
    assert "VA-" not in str(message.content)


# --------------------------------------------------------------------------------------
# MW-11 — derived paths defer to I3 (D-13)
# --------------------------------------------------------------------------------------


def test_a_derived_write_produces_exactly_one_tool_message_mw11(kb: Path) -> None:
    """MW-11: arch §7 assumed the validator never sees these, because I3 forbids them. It does —
    permissions are enforced *inside* the tool body, after every `wrap_tool_call`. Refusing here
    too would give one write two contradictory refusals."""
    model = scripted(
        calls(write(f"{KB_MOUNT}Cooking/index.md", NO_FRONTMATTER, "c1")), says("refused")
    )
    result = run(build_agent(kb, model))

    (message,) = tool_messages(result)
    assert message.status == "error"
    assert "permission denied" in str(message.content)
    assert "VA-" not in str(message.content)


def test_a_derived_write_is_never_validated_mw11(kb: Path) -> None:
    """The same rule at the seam: zero `validate_content` calls, so zero findings can be emitted."""
    handler = SpyHandler()
    middleware = KbValidationMiddleware(kb)

    with patch.object(validation_module, "validate_content", wraps=validate_content) as validator:
        middleware.wrap_tool_call(
            request_for("write_file", {"file_path": f"{KB_MOUNT}index.md", "content": "x"}),
            handler,
        )

    assert validator.call_count == 0
    assert len(handler.calls) == 1


# --------------------------------------------------------------------------------------
# MW-12 / MW-13 — the block decision
# --------------------------------------------------------------------------------------


def test_a_warning_only_write_lands_with_an_advisory_mw12(kb: Path) -> None:
    """MW-12: Layer 1 chose warning severity for VA-25/VA-29/VA-33/VA-35 *so that* they would not
    cost one of three attempts. A warning that blocks is indistinguishable from an error to the
    model; riding along on the success message converges the corpus for free."""
    model = scripted(calls(write(WARNING_NOTE_PATH, CLEAN_NOTE, "c1")), says("filed"))
    result = run(build_agent(kb, model))

    (message,) = tool_messages(result)
    assert message.status != "error"
    assert (kb / WARNING_NOTE_REL).exists()
    assert ADVISORY_HEADER in str(message.content)
    assert "FILENAME_TITLE_DIVERGENCE" in str(message.content)


def test_an_invalid_write_never_reaches_the_handler_mw13(kb: Path) -> None:
    """MW-13's headline: the bytes do not reach the backend at all. "The write failed" is a weaker
    claim than "the write was never attempted", and only the second one makes arch §7 true."""
    handler = SpyHandler()
    middleware = KbValidationMiddleware(kb)

    result = middleware.wrap_tool_call(
        request_for("write_file", {"file_path": NOTE_PATH, "content": NO_FRONTMATTER}), handler
    )

    assert handler.calls == []
    assert isinstance(result, Command)
    (message,) = result.update["messages"]
    assert message.status == "error"
    assert not (kb / NOTE_REL).exists()


def test_the_refusal_carries_layer_ones_findings_verbatim_mw13(kb: Path) -> None:
    """MW-13: `Finding.render()` already emits the code, rule id, field and hint an agent needs
    (CX-6) and `sort_findings` already orders them. Layer 2 prepends its counter and next-step line
    and touches nothing else — a paraphrase here becomes a second copy of every Layer 1 message."""
    model = scripted(calls(write(NOTE_PATH, NO_FRONTMATTER, "c1")), says("refused"))
    result = run(build_agent(kb, model))

    expected = render_findings(errors_only(validate_content(kb, NOTE_REL, NO_FRONTMATTER)))
    (message,) = tool_messages(result)
    assert expected in str(message.content)
    assert "MISSING_FRONTMATTER" in expected and "VA-3" in expected


# --------------------------------------------------------------------------------------
# MW-14 / MW-15 / MW-16 — the attempt bound and the escalation
# --------------------------------------------------------------------------------------


def test_attempts_are_keyed_by_path_not_by_tool_mw14(kb: Path) -> None:
    """MW-14: keyed by the normalized KB-relative path, so a failed `write_file` and a failed
    `edit_file` on one file share a budget. Keying by tool name (or `tool_call_id`) would give a
    determined model six tries instead of three."""
    target = kb / NOTE_REL
    target.write_text(CLEAN_NOTE, encoding="utf-8")

    model = scripted(
        calls(write(NOTE_PATH, NO_FRONTMATTER, "c1")),
        calls(edit(NOTE_PATH, 'topic: "Cooking"\n', "", "c2")),
        says("gave up"),
    )
    agent = build_agent(kb, model)
    result = run(agent)

    contents = [str(m.content) for m in tool_messages(result)]
    assert "Attempt 1 of 3" in contents[0]
    assert "Attempt 2 of 3" in contents[1]
    assert agent.get_state(config()).values[KB_ATTEMPTS] == {NOTE_REL: 2}


def test_attempts_reset_at_every_run_entry_mw14(kb: Path) -> None:
    """MW-14: "three attempts" means one graph invocation, not the thread's lifetime. Without the
    `before_agent` reset the counter is checkpointed and turn 2 escalates on its first refusal."""
    model = scripted(
        calls(write(NOTE_PATH, NO_FRONTMATTER, "c1")),
        says("first turn"),
        calls(write(NOTE_PATH, NO_FRONTMATTER, "c2")),
        says("second turn"),
    )
    agent = build_agent(kb, model)

    run(agent, "t1", "first")
    assert agent.get_state(config("t1")).values[KB_ATTEMPTS] == {NOTE_REL: 1}

    result = run(agent, "t1", "second")
    assert "Attempt 1 of 3" in str(tool_messages(result)[-1].content)
    assert agent.get_state(config("t1")).values[KB_ATTEMPTS] == {NOTE_REL: 1}


@pytest.mark.parametrize("drive", DRIVERS, ids=["sync", "async"])
def test_sibling_calls_in_one_message_share_the_bound_mw14(
    kb: Path, drive: Callable[[Any], dict[str, Any]]
) -> None:
    """MW-14: the bound is "per file per run", and a run is not a sequence of single tool calls.

    `request.state` is the pre-superstep snapshot — every call in one `AIMessage` reads the same
    object and none of them sees the reducer's deltas from its siblings. Reading the counter from it
    alone made four writes to one path four separate "Attempt 1 of 3"s: the bound never bound, and
    the model was told it had three tries left each time it had fewer. Both halves matter here — the
    counters have to *read* 1/2/3, and the fourth has to stop instead of consuming a fifth try.

    Driven through both hook variants because `awrap_tool_call` shares `_decide` and is the one the
    daemon actually runs (MW-2, RT-3)."""
    model = scripted(
        calls(*(write(NOTE_PATH, NO_FRONTMATTER, f"c{i}") for i in range(1, 5))),
        says("all four answered"),
    )
    agent = build_agent(kb, model)
    result = drive(agent)

    contents = [str(m.content) for m in tool_messages(result)]
    assert len(contents) == 4
    assert [c for c in contents if "Attempt" in c] == contents[:MAX_ATTEMPTS]
    assert "Attempt 1 of 3" in contents[0]
    assert "Attempt 2 of 3" in contents[1]
    assert "Attempt 3 of 3" in contents[2]
    assert "Attempt" not in contents[3] and NOTE_REL in contents[3]

    assert [m.status for m in tool_messages(result)] == ["error"] * 4
    assert agent.get_state(config()).values[KB_ATTEMPTS] == {NOTE_REL: MAX_ATTEMPTS}
    assert not (kb / NOTE_REL).exists()


def test_a_batch_that_exhausts_the_bound_escalates_on_the_next_turn_mw15(kb: Path) -> None:
    """MW-15: the enforcement point stays in `after_model`, which is the only hook that may end the
    run (MW-16). A batch that burns the whole budget therefore caps the counter at `MAX_ATTEMPTS`
    and the *next* retry escalates — exactly as three sequential refusals do. Escalating from inside
    the batch instead would hand the human a decision before Layer 1's findings had been shown even
    once."""
    spy = FlushSpy(kb)
    model = scripted(
        calls(*(write(NOTE_PATH, NO_FRONTMATTER, f"c{i}") for i in range(1, 5))),
        calls(write(NOTE_PATH, NO_FRONTMATTER, "c5")),
        says("never reached"),
    )
    agent = build_agent(kb, model, flush_spy=spy)
    result = run(agent)

    escalation = result["messages"][-1]
    assert isinstance(escalation, AIMessage)
    assert NOTE_REL in str(escalation.content)
    assert "MISSING_FRONTMATTER" in str(escalation.content)
    assert agent.get_state(config()).next == ()
    assert spy.reports == [[]]
    assert not (kb / NOTE_REL).exists()


def test_a_recovered_draft_inside_the_batch_still_lands_mw15(kb: Path) -> None:
    """The bound stops a loop; it does not blacklist a path — in a batch as much as across turns.

    The exhausted-budget check therefore sits *after* Layer 1's verdict, never before it:
    short-circuiting on the counter alone would be cheaper and would refuse the one call in this
    batch that is actually correct."""
    spy = FlushSpy(kb)
    model = scripted(
        calls(
            *(write(NOTE_PATH, NO_FRONTMATTER, f"c{i}") for i in range(1, 4)),
            write(NOTE_PATH, CLEAN_NOTE, "c4"),
        ),
        says("filed at last"),
    )
    agent = build_agent(kb, model, flush_spy=spy)
    result = run(agent)

    assert [m.status for m in tool_messages(result)] == ["error", "error", "error", "success"]
    assert (kb / NOTE_REL).exists()
    assert spy.reports == [[NOTE_REL]]


@pytest.mark.parametrize("drive", DRIVERS, ids=["sync", "async"])
def test_the_run_escalates_to_the_human_instead_of_looping_mw15(
    kb: Path, drive: Callable[[Any], dict[str, Any]]
) -> None:
    """MW-15: after three refusals on one file the agent stops and hands the decision over. The run
    ends *normally*, so the thread stays resumable on any channel. Driven through both hook
    variants because `aafter_model` is the one the daemon actually runs (MW-2, RT-3)."""
    spy = FlushSpy(kb)
    model = scripted(
        *(calls(write(NOTE_PATH, NO_FRONTMATTER, f"c{i}")) for i in range(1, 6)),
        says("never reached"),
    )
    agent = build_agent(kb, model, flush_spy=spy)
    result = drive(agent)

    refusals = [m for m in tool_messages(result) if "Attempt" in str(m.content)]
    assert len(refusals) == MAX_ATTEMPTS

    escalation = result["messages"][-1]
    assert isinstance(escalation, AIMessage)
    text = str(escalation.content)
    assert NOTE_REL in text
    assert "MISSING_FRONTMATTER" in text and "VA-3" in text
    assert agent.get_state(config()).next == ()
    assert not (kb / NOTE_REL).exists()


def test_the_escalation_answers_every_pending_tool_call_mw15(kb: Path) -> None:
    """An `AIMessage` whose `tool_calls` have no matching `ToolMessage` is rejected by real
    providers on the next turn, which would make "the thread stays resumable" false."""
    model = scripted(
        *(calls(write(NOTE_PATH, NO_FRONTMATTER, f"c{i}")) for i in range(1, 4)),
        calls(
            write(NOTE_PATH, NO_FRONTMATTER, "c4"),
            call("read_file", {"file_path": f"{KB_MOUNT}Cooking/topic.md"}, "c5"),
        ),
        says("never reached"),
    )
    result = run(build_agent(kb, model))

    messages: list[BaseMessage] = result["messages"]
    last_ai = next(m for m in reversed(messages[:-1]) if isinstance(m, AIMessage) and m.tool_calls)
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    assert {tc["id"] for tc in last_ai.tool_calls} <= answered


def test_a_recovered_draft_is_not_blacklisted_mw15(kb: Path) -> None:
    """The bound stops a loop; it does not blacklist a path. If the fourth proposal is finally
    valid it must land, or a model that fixes its draft is punished for having been wrong."""
    spy = FlushSpy(kb)
    model = scripted(
        *(calls(write(NOTE_PATH, NO_FRONTMATTER, f"c{i}")) for i in range(1, 4)),
        calls(write(NOTE_PATH, CLEAN_NOTE, "c4")),
        says("filed at last"),
    )
    agent = build_agent(kb, model, flush_spy=spy)
    run(agent)

    assert (kb / NOTE_REL).exists()
    assert spy.reports == [[NOTE_REL]]


def test_the_escalation_still_runs_the_after_agent_chain_mw16(kb: Path) -> None:
    """MW-16: `Command(goto=END)` reaches a *different* node from the graph's `exit_node`, and only
    `exit_node` is the `after_agent` chain. Jumping to `END` would leave the derived files stale —
    the exact state arch §7 calls worse than the bad write itself."""
    spy = FlushSpy(kb)
    model = scripted(
        calls(write(WARNING_NOTE_PATH, CLEAN_NOTE, "w1")),
        *(calls(write(NOTE_PATH, NO_FRONTMATTER, f"c{i}")) for i in range(1, 6)),
        says("never reached"),
    )
    run(build_agent(kb, model, flush_spy=spy))

    assert spy.reports == [[WARNING_NOTE_REL]]
    index = (kb / COOKING / "index.md").read_text(encoding="utf-8")
    assert "sear.md" in index


@pytest.mark.superseded
def test_the_escalation_survives_the_hitl_hook_sharing_the_chain_mw15(kb: Path) -> None:
    """The graph an expert actually compiles has `interrupt_on`, which appends
    `HumanInTheLoopMiddleware` — whose `after_model` shares the chain with this one and runs first
    (reverse-registration order). The jump has to end the run from that position too, or the
    escalation would work in this suite and loop in production.

    Superseded (Phase 3 rebuilds this): the premise — "the graph an expert actually compiles has
    `interrupt_on`" — is exactly what Task 6 deletes at the composition point; production graphs no
    longer wire `HumanInTheLoopMiddleware` in at all, so there is no shared `after_model` position
    left to survive from. The underlying claim this test protects (the attempt-bound escalation
    still ends the run cleanly) is already covered gate-free by
    `test_the_run_escalates_to_the_human_instead_of_looping_mw15`; this test's specific worry — a
    second `after_model` hook sharing the chain — has no successor because the hook it shared with
    is gone.
    """
    spy = FlushSpy(kb)
    model = scripted(
        *(calls(write(NOTE_PATH, NO_FRONTMATTER, f"c{i}")) for i in range(1, 6)),
        says("never reached"),
    )
    agent = build_agent(
        kb,
        model,
        flush_spy=spy,
        interrupt_on={"delete": {"allowed_decisions": ["approve", "reject"]}},
    )
    result = run(agent)

    assert "HumanInTheLoopMiddleware.after_model" in agent.nodes
    assert isinstance(result["messages"][-1], AIMessage)
    assert NOTE_REL in str(result["messages"][-1].content)
    assert agent.get_state(config()).next == ()
    assert spy.reports == [[]]


def test_no_jump_is_expressed_as_a_goto_command_mw16() -> None:
    """The same rule, statically: `Command(goto=...)` must not appear in this module at all."""
    tree = ast.parse(Path(validation_module.__file__).read_text(encoding="utf-8"))
    gotos = [
        keyword
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "goto"
    ]
    assert gotos == []


# --------------------------------------------------------------------------------------
# MW-17 / MW-18 / MW-19 — the touched-path record
# --------------------------------------------------------------------------------------


def test_a_refused_or_denied_write_records_no_touched_path_mw17(kb: Path) -> None:
    """MW-17: a denied `index.md` write must not get its `updated` line bumped by the next flush.
    Only a handler result that reported success counts."""
    spy = FlushSpy(kb)
    model = scripted(
        calls(
            write(NOTE_PATH, NO_FRONTMATTER, "c1"),
            write(f"{KB_MOUNT}Cooking/index.md", CLEAN_NOTE, "c2"),
        ),
        says("nothing landed"),
    )
    run(build_agent(kb, model, flush_spy=spy))

    assert spy.reports == [[]]


def test_a_command_wrapped_success_is_still_recorded_mw17(kb: Path) -> None:
    """MW-17's parenthetical: a handler may return a `Command` carrying the result rather than a
    bare `ToolMessage`. Reading only bare messages would stop recording every write the day a
    middleware is inserted below this one, and nothing would fail — the tree would just go stale."""
    message = ToolMessage(content="Updated file", name="write_file", tool_call_id="c1")
    handler = SpyHandler(result=Command(update={"messages": [message], "files": {}}))

    result = KbValidationMiddleware(kb).wrap_tool_call(
        request_for("write_file", {"file_path": NOTE_PATH, "content": CLEAN_NOTE}), handler
    )

    assert isinstance(result, Command)
    assert result.update[KB_TOUCHED] == [NOTE_REL]
    assert result.update["files"] == {}
    assert [m.tool_call_id for m in result.update["messages"]] == ["c1"]


def test_a_successful_write_is_recorded_as_touched_mw18(kb: Path) -> None:
    """MW-18: a bare `ToolMessage` cannot carry a state update, and `ToolNode` rejects a `Command`
    whose update lacks the matching `ToolMessage`. The pair travels together or not at all."""
    spy = FlushSpy(kb)
    model = scripted(calls(write(NOTE_PATH, CLEAN_NOTE, "c1")), says("filed"))
    agent = build_agent(kb, model, flush_spy=spy)
    result = run(agent)

    assert spy.reports == [[NOTE_REL]]
    assert len([m for m in tool_messages(result) if m.tool_call_id == "c1"]) == 1


def test_one_path_written_twice_is_recorded_once_mw18(kb: Path) -> None:
    """De-duplication is the reducer's job — two calls in one message never see each other's
    update — but the middleware must not pre-filter it away either."""
    spy = FlushSpy(kb)
    model = scripted(
        calls(write(NOTE_PATH, CLEAN_NOTE, "c1")),
        calls(write(NOTE_PATH, CLEAN_NOTE, "c2")),
        says("filed twice"),
    )
    run(build_agent(kb, model, flush_spy=spy))

    assert spy.reports == [[NOTE_REL]]


def test_a_successful_delete_is_recorded_as_touched_mw19(kb: Path) -> None:
    """MW-19: a deleted note still listed in `index.md` and `tags.md` is the stale-derived-file
    state arch §7 calls worse than the bad write. Deletes need no validation, only the record."""
    target = kb / NOTE_REL
    target.write_text(CLEAN_NOTE, encoding="utf-8")
    flush(kb, [NOTE_REL], today=TODAY)
    assert "reverse-sear.md" in (kb / COOKING / "index.md").read_text(encoding="utf-8")

    spy = FlushSpy(kb)
    model = scripted(calls(call("delete", {"file_path": NOTE_PATH}, "c1")), says("removed"))
    run(build_agent(kb, model, flush_spy=spy))

    assert spy.reports == [[NOTE_REL]]
    assert not target.exists()
    assert "reverse-sear.md" not in (kb / COOKING / "index.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# RT-9 / RT-10 — the path seam
# --------------------------------------------------------------------------------------


def test_an_unnormalized_kb_path_is_still_validated_rt9(kb: Path) -> None:
    """RT-9/D-3: the arg is the raw model string. `kb/Cooking/...` — no leading slash — reaches the
    knowledge base on disk while `startswith("/kb/")` says False. Live-verified bypass; the
    middleware normalizes with the harness's own `validate_path` first."""
    model = scripted(
        calls(write(f"kb/{NOTE_REL}", NO_FRONTMATTER, "c1")),
        calls(write(f"/kb/./{NOTE_REL}", NO_FRONTMATTER, "c2")),
        says("both refused"),
    )
    agent = build_agent(kb, model)
    result = run(agent)

    assert [m.status for m in tool_messages(result)] == ["error", "error"]
    assert all("MISSING_FRONTMATTER" in str(m.content) for m in tool_messages(result))
    assert agent.get_state(config()).values[KB_ATTEMPTS] == {NOTE_REL: 2}
    assert not (kb / NOTE_REL).exists()


def test_a_path_the_harness_refuses_is_forwarded_untouched_rt10(kb: Path) -> None:
    """RT-10: on `..`, `~` or a drive prefix Layer 2 neither raises nor swallows — it forwards, so
    deepagents emits its own error. Re-wording a harness error would give the model two different
    messages for one failure and put Layer 2 in charge of someone else's text."""
    outside = kb.parent / "passwd"

    with patch.object(validation_module, "validate_content", wraps=validate_content) as validator:
        model = scripted(calls(write(f"{KB_MOUNT}../passwd", "x", "c1")), says("refused"))
        result = run(build_agent(kb, model))

    assert validator.call_count == 0
    (message,) = tool_messages(result)
    assert message.status == "error"
    assert "VA-" not in str(message.content)
    assert not outside.exists()
