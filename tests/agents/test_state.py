"""The shared middleware state (MW-5, MW-6, D-7).

Two things are asserted here that no other test can see, because both fail *silently* when they
break: that the reducers are actually wired as reducers (langgraph falls back to a last-write-wins
channel without a word if the metadata order is wrong), and that both keys are stripped in both
directions at the subagent boundary (without which a delegated expert's touched paths are merged
into the Librarian's state and the parent flushes over files it never wrote).
"""

from __future__ import annotations

from typing import Annotated, Any, get_args, get_origin, get_type_hints

from deepagents.middleware._state import private_state_field_names
from deepagents.middleware.subagents import _EXCLUDED_STATE_KEYS
from langchain.agents.middleware.types import AgentState, PrivateStateAttr
from langgraph.channels.binop import BinaryOperatorAggregate
from langgraph.graph.state import StateGraph

from pkb.agents.middleware.state import (
    KB_ATTEMPTS,
    KB_TOUCHED,
    KbAgentState,
    merge_attempt_counts,
    merge_touched_paths,
)

STATE_KEYS = (KB_TOUCHED, KB_ATTEMPTS)


def _metadata(key: str) -> tuple[Any, ...]:
    """The `Annotated` metadata of one state key, resolved the way both consumers resolve it."""
    hints = get_type_hints(KbAgentState, include_extras=True)
    annotation = hints[key]
    while get_origin(annotation) is not Annotated:
        annotation = get_args(annotation)[0]
    return get_args(annotation)[1:]


# --------------------------------------------------------------------------------------
# MW-5 — shape
# --------------------------------------------------------------------------------------


def test_state_extends_agent_state_with_exactly_two_keys_mw5() -> None:
    """`KbAgentState` is `AgentState` plus `kb_touched` and `kb_attempts` — nothing else (MW-5)."""
    base = set(get_type_hints(AgentState, include_extras=True))
    ours = set(get_type_hints(KbAgentState, include_extras=True))
    assert ours - base == {KB_TOUCHED, KB_ATTEMPTS}
    assert "messages" in ours


def test_both_keys_carry_the_private_marker_mw5() -> None:
    """`PrivateStateAttr` must be the singleton: `_has_marker` compares with `is` (MW-5, D-7)."""
    for key in STATE_KEYS:
        assert any(meta is PrivateStateAttr for meta in _metadata(key)), key


def test_reducers_are_wired_as_reducers_mw6() -> None:
    """langgraph reads `__metadata__[-1]` only, and silently falls back to `LastValue` (MW-6).

    This is the regression that would make every other state rule pass while the second tool call
    of a turn quietly overwrote the first.
    """
    channels = StateGraph(KbAgentState).channels
    for key, reducer in ((KB_TOUCHED, merge_touched_paths), (KB_ATTEMPTS, merge_attempt_counts)):
        channel = channels[key]
        assert isinstance(channel, BinaryOperatorAggregate), key
        assert channel.operator is reducer, key


# --------------------------------------------------------------------------------------
# MW-6 — reducer behaviour
# --------------------------------------------------------------------------------------


def test_touched_paths_merge_rather_than_replace_mw6() -> None:
    """Two tool calls in one turn must both survive (MW-6, MW-18)."""
    assert merge_touched_paths([], ["Cooking/notes/a.md"]) == ["Cooking/notes/a.md"]
    assert merge_touched_paths(["Cooking/notes/a.md"], ["Cooking/notes/b.md"]) == [
        "Cooking/notes/a.md",
        "Cooking/notes/b.md",
    ]


def test_touched_paths_deduplicate_preserving_first_seen_order_mw6() -> None:
    """Writing the same path twice in one run yields one entry (MW-6)."""
    merged = merge_touched_paths(["b", "a"], ["a", "c", "b"])
    assert merged == ["b", "a", "c"]


def test_touched_paths_reset_on_none_mw6() -> None:
    """`after_agent` returns `{"kb_touched": None}`; state is checkpointed, so it must clear."""
    assert merge_touched_paths(["Cooking/notes/a.md"], None) == []


def test_touched_paths_tolerate_a_bare_string_mw6() -> None:
    """A string is iterable; without the guard a path would splat into characters and flush none."""
    assert merge_touched_paths([], "Cooking/notes/a.md") == ["Cooking/notes/a.md"]


def test_attempt_counts_accumulate_across_tool_calls_mw14() -> None:
    """The update is a delta, so two blocked writes on one path count as two attempts (MW-14)."""
    first = merge_attempt_counts({}, {"Cooking/notes/a.md": 1})
    second = merge_attempt_counts(first, {"Cooking/notes/a.md": 1})
    third = merge_attempt_counts(second, {"Cooking/notes/a.md": 1})
    assert third == {"Cooking/notes/a.md": 3}


def test_attempt_counts_are_kept_per_path_mw14() -> None:
    """A `write_file` failure and an `edit_file` failure on the same file share one counter, and
    two different files do not (MW-14)."""
    merged = merge_attempt_counts({"a.md": 2}, {"b.md": 1})
    assert merged == {"a.md": 2, "b.md": 1}


def test_attempt_counts_reset_on_none_mw6() -> None:
    """`before_agent` clears the counter at run entry: three attempts per *run*, not per thread."""
    assert merge_attempt_counts({"a.md": 3}, None) == {}


def test_reducers_never_mutate_their_left_argument_mw4() -> None:
    """One middleware instance serves every run; a reducer that aliased state would leak across."""
    touched = ["a"]
    counts = {"a.md": 1}
    merge_touched_paths(touched, ["b"])
    merge_attempt_counts(counts, {"a.md": 1})
    assert touched == ["a"]
    assert counts == {"a.md": 1}


# --------------------------------------------------------------------------------------
# D-7 — the subagent boundary
# --------------------------------------------------------------------------------------


def test_both_keys_are_recognised_as_private_d7() -> None:
    """This frozenset is exactly what `task` strips in both directions (D-7, MW-5, LB-11)."""
    private = private_state_field_names(KbAgentState)
    assert {KB_TOUCHED, KB_ATTEMPTS} <= private


def test_touched_paths_do_not_reach_a_delegated_expert_d7() -> None:
    """`subagents.py:538` — the parent's state minus excluded and private keys goes down."""
    private = private_state_field_names(KbAgentState)
    parent = {
        "messages": ["m"],
        "todos": [],
        KB_TOUCHED: ["Cooking/notes/a.md"],
        KB_ATTEMPTS: {"Cooking/notes/a.md": 1},
    }
    passed_down = {
        k: v for k, v in parent.items() if k not in _EXCLUDED_STATE_KEYS and k not in private
    }
    assert passed_down == {}


def test_expert_touched_paths_do_not_leak_up_to_the_librarian_d7() -> None:
    """`subagents.py:484` — without the marker the parent would flush over the expert's paths.

    That is not a harmless repeat: `flush` re-stamps `updated`, so the Librarian would bump files
    it never wrote, and a second `ScanRequest` would be queued for the same topic (LB-11, MW-28).
    """
    private = private_state_field_names(KbAgentState)
    expert_result = {
        "messages": ["final"],
        KB_TOUCHED: ["Cooking/notes/steak.md"],
        KB_ATTEMPTS: {"Cooking/notes/steak.md": 2},
    }
    merged_up = {
        k: v for k, v in expert_result.items() if k not in _EXCLUDED_STATE_KEYS and k not in private
    }
    assert merged_up == {}


# --------------------------------------------------------------------------------------
# End to end through a graph — the reducers as the runtime actually applies them
# --------------------------------------------------------------------------------------


def test_state_survives_two_writes_and_clears_on_exit_mw6() -> None:
    """The whole lifecycle in one graph: accumulate, read, reset (MW-5, MW-6, MW-20)."""
    seen: list[list[str]] = []

    def first(state: KbAgentState) -> dict[str, Any]:
        return {KB_TOUCHED: ["Cooking/notes/a.md"], KB_ATTEMPTS: {"Cooking/notes/a.md": 1}}

    def second(state: KbAgentState) -> dict[str, Any]:
        return {KB_TOUCHED: ["Cooking/notes/b.md"], KB_ATTEMPTS: {"Cooking/notes/a.md": 1}}

    def exit_node(state: KbAgentState) -> dict[str, Any]:
        seen.append(list(state[KB_TOUCHED]))
        assert state[KB_ATTEMPTS] == {"Cooking/notes/a.md": 2}
        return {KB_TOUCHED: None, KB_ATTEMPTS: None}

    builder = StateGraph(KbAgentState)
    builder.add_node("first", first)
    builder.add_node("second", second)
    builder.add_node("exit", exit_node)
    builder.set_entry_point("first")
    builder.add_edge("first", "second")
    builder.add_edge("second", "exit")
    builder.set_finish_point("exit")

    final = builder.compile().invoke({"messages": []})
    assert seen == [["Cooking/notes/a.md", "Cooking/notes/b.md"]]
    assert final[KB_TOUCHED] == []
    assert final[KB_ATTEMPTS] == {}
