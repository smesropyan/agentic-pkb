"""`KbBreadthMiddleware` — breadth in context, read fresh every model call (EX-6, EX-7, LB-4, MW-2).

Every assertion here is made against a real `create_deep_agent` graph driven by `ScriptedChatModel`,
and against the *system message the model actually received* — never against the middleware's return
value. That is deliberate: this middleware exists because `create_deep_agent`'s memory parameter
supplies breadth through a mechanism whose failure is invisible at the middleware boundary (a
checkpointed cache that keeps serving turn one's copy of a file the human has since edited, D-11).
A test that inspected a rendered string would have passed against that bug too.

The load-bearing test is `test_an_edit_between_two_turns_of_one_thread_is_seen_ex7`: one thread, one
graph, a human edit in between, and the second model call must see it.
"""

from __future__ import annotations

from pathlib import Path

import deepagents.middleware
import pytest
from deepagents import create_deep_agent
from deepagents.middleware.memory import MEMORY_SYSTEM_PROMPT
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import InMemorySaver

from pkb.agents.middleware.breadth import (
    BLOCK_CLOSE,
    BLOCK_OPEN,
    MAX_SOURCE_BYTES,
    KbBreadthMiddleware,
    librarian_breadth_sources,
    topic_breadth_sources,
)
from tests.agents.conftest import ScriptedChatModel, says, scripted

BASE_PROMPT = "BASE-PROMPT-STANDS-FOR-THE-STANDARDS-PREAMBLE"

GRILLING = "Cooking/sub-topics/Grilling"


# --------------------------------------------------------------------------------------
# Local helpers
# --------------------------------------------------------------------------------------


def system_texts(model: ScriptedChatModel) -> list[str]:
    """The system message of each recorded turn, as text.

    conftest's `system_prompts` stringifies `.content`, which becomes a list of content blocks as
    soon as any middleware appends to it — and `str()` of that list escapes the newlines, which
    defeats every multi-line assertion below. `.text` joins the blocks back into prose.
    """
    return [turn[0].text for turn in model.calls if turn and turn[0].type == "system"]


def breadth_block(text: str) -> str:
    """The breadth block out of a rendered system prompt, delimiters included."""
    assert BLOCK_OPEN in text, f"no breadth block in:\n{text}"
    assert BLOCK_CLOSE in text
    return text[text.index(BLOCK_OPEN) : text.index(BLOCK_CLOSE) + len(BLOCK_CLOSE)]


def build(
    middleware: KbBreadthMiddleware,
    model: ScriptedChatModel,
    *,
    checkpointer: InMemorySaver | None = None,
):
    """A minimal deep agent carrying only this middleware."""
    return create_deep_agent(
        model=model,
        system_prompt=BASE_PROMPT,
        middleware=[middleware],
        checkpointer=checkpointer,
    )


def one_turn(kb: Path, middleware: KbBreadthMiddleware) -> str:
    """Run one turn and return the breadth block the model saw."""
    model = scripted(says("done"))
    build(middleware, model).invoke({"messages": [{"role": "user", "content": "hello"}]})
    return breadth_block(system_texts(model)[0])


def append_line(path: Path, line: str) -> None:
    """Append a line to a knowledge-base file, the way a human editing it would."""
    path.write_text(f"{path.read_text(encoding='utf-8').rstrip()}\n\n{line}\n", encoding="utf-8")


# --------------------------------------------------------------------------------------
# EX-6 — the memory mechanism is not in play
# --------------------------------------------------------------------------------------


def test_the_memory_prompt_never_reaches_the_model_ex6(kb: Path) -> None:
    """The agent is never instructed to persist knowledge by editing its breadth files (EX-6).

    `MemoryMiddleware` would wrap the same two files in `<agent_memory>` and tell the model, in the
    system prompt, to call `edit_file` on them to save what it learns — the two files README §1.6
    says the AI never finalizes on its own. Asserting on the delivered prompt (rather than on a
    constructor kwarg) is what makes this true no matter how the graph was assembled.
    """
    model = scripted(says("done"))
    graph = build(KbBreadthMiddleware.for_topic(kb, "Cooking"), model)
    graph.invoke({"messages": [{"role": "user", "content": "hello"}]})

    prompt = system_texts(model)[0]
    assert "<agent_memory>" not in prompt
    persist = next(
        line for line in MEMORY_SYSTEM_PROMPT.splitlines() if "persist new knowledge" in line
    )
    assert persist.strip() not in prompt
    assert not any(node.startswith("MemoryMiddleware") for node in graph.nodes)


# --------------------------------------------------------------------------------------
# EX-7 — what is loaded
# --------------------------------------------------------------------------------------


def test_the_topics_own_breadth_files_reach_the_system_prompt_ex7(kb: Path) -> None:
    """`topic.md` and `notes/summary.md` are in context, addressed by their agent-visible path."""
    append_line(kb / "Cooking" / "topic.md", "MARKER-TOPIC-CARD")
    append_line(kb / "Cooking" / "notes" / "summary.md", "MARKER-NOTES-SUMMARY")

    block = one_turn(kb, KbBreadthMiddleware.for_topic(kb, "Cooking"))

    assert "MARKER-TOPIC-CARD" in block
    assert "MARKER-NOTES-SUMMARY" in block
    assert '<file path="/kb/Cooking/topic.md">' in block
    assert '<file path="/kb/Cooking/notes/summary.md">' in block


def test_the_topic_index_is_deliberately_not_loaded_ex7(kb: Path) -> None:
    """`index.md` is the derived depth directory, read on demand — never carried every turn.

    It is unbounded (one line per file in the topic) and it is regenerated from the files
    themselves, so paying for it in the system prompt every turn buys context the expert can get
    with one `read_file` when it actually needs to find something.
    """
    append_line(kb / "Cooking" / "index.md", "MARKER-TOPIC-INDEX")

    block = one_turn(kb, KbBreadthMiddleware.for_topic(kb, "Cooking"))

    assert "MARKER-TOPIC-INDEX" not in block
    assert "index.md" not in block


def test_a_sub_topic_loads_its_own_breadth_not_its_parents_ex7(kb: Path) -> None:
    """A sub-topic is its own scope, so it is its own breadth (EX-7, EX-2).

    `resolve_expert` decides whose *persona* runs a sub-topic; it never decides whose files it
    reads. Grilling running Cooking's summary would answer Grilling questions out of the parent's
    distilled experience, which is precisely the mis-scoping sub-topics exist to prevent.
    """
    append_line(kb / "Cooking" / "notes" / "summary.md", "MARKER-PARENT-SUMMARY")
    append_line(kb / GRILLING / "notes" / "summary.md", "MARKER-CHILD-SUMMARY")

    block = one_turn(kb, KbBreadthMiddleware.for_topic(kb, GRILLING))

    assert "MARKER-CHILD-SUMMARY" in block
    assert "MARKER-PARENT-SUMMARY" not in block
    assert f'<file path="/kb/{GRILLING}/topic.md">' in block


def test_the_block_is_appended_not_substituted_ex7(kb: Path) -> None:
    """The system prompt the factory built survives underneath the breadth block (EX-4).

    The standards preamble is prepended in code and must not be reachable from any file in the
    tree; a middleware that replaced `system_message` instead of appending to it would delete it
    from every turn, silently.
    """
    model = scripted(says("done"))
    build(KbBreadthMiddleware.for_topic(kb, "Cooking"), model).invoke(
        {"messages": [{"role": "user", "content": "hello"}]}
    )

    prompt = system_texts(model)[0]
    assert BASE_PROMPT in prompt
    assert prompt.index(BASE_PROMPT) < prompt.index(BLOCK_OPEN)


# --------------------------------------------------------------------------------------
# EX-7 — the guarantee: fresh on every model call
# --------------------------------------------------------------------------------------


def test_an_edit_between_two_turns_of_one_thread_is_seen_ex7(kb: Path) -> None:
    """The failure `memory=` would have shipped: one thread, a human edit, the next turn sees it.

    This is the whole reason this middleware exists (D-11). `MemoryMiddleware` loads its sources
    once and stores them in checkpointed state — `if "memory_contents" in state: return None` — so
    on turn two of the *same thread* the model is handed turn one's bytes. In a knowledge base whose
    first rule is that the human's version of a breadth file is the one that counts, an agent
    arguing from a summary the human rewrote an hour ago is not a stale cache, it is a wrong answer
    delivered confidently.
    """
    summary = kb / "Cooking" / "notes" / "summary.md"
    append_line(summary, "MARKER-BEFORE-THE-HUMAN-EDIT")

    model = scripted(says("first"), says("second"))
    graph = build(KbBreadthMiddleware.for_topic(kb, "Cooking"), model, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "one-thread"}}

    graph.invoke({"messages": [{"role": "user", "content": "turn one"}]}, config)
    append_line(summary, "MARKER-AFTER-THE-HUMAN-EDIT")
    graph.invoke({"messages": [{"role": "user", "content": "turn two"}]}, config)

    first, second = (breadth_block(text) for text in system_texts(model)[:2])
    assert "MARKER-BEFORE-THE-HUMAN-EDIT" in first
    assert "MARKER-AFTER-THE-HUMAN-EDIT" not in first
    assert "MARKER-AFTER-THE-HUMAN-EDIT" in second


def test_a_file_created_between_turns_appears_on_the_next_turn_ex7(kb: Path) -> None:
    """Absence is re-checked every call too, not decided once at the start of the thread.

    A topic scaffolded, or a `topic.md` drafted, part-way through a conversation is the ordinary
    path through README §1.9's topic-creation flow — the expert must not spend the rest of the
    thread believing the file it just approved does not exist.
    """
    summary = kb / "Cooking" / "notes" / "summary.md"
    original = summary.read_text(encoding="utf-8")
    summary.unlink()

    model = scripted(says("first"), says("second"))
    graph = build(KbBreadthMiddleware.for_topic(kb, "Cooking"), model, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "one-thread"}}

    graph.invoke({"messages": [{"role": "user", "content": "turn one"}]}, config)
    summary.write_text(f"{original.rstrip()}\n\nMARKER-WRITTEN-MID-THREAD\n", encoding="utf-8")
    graph.invoke({"messages": [{"role": "user", "content": "turn two"}]}, config)

    first, second = (breadth_block(text) for text in system_texts(model)[:2])
    assert '<file path="/kb/Cooking/notes/summary.md" note="not present" />' in first
    assert "MARKER-WRITTEN-MID-THREAD" in second


@pytest.mark.asyncio
async def test_both_hook_variants_render_the_same_block_mw2(kb: Path) -> None:
    """Sync and async hooks are both implemented and agree (MW-2).

    The daemon is async-only (RT-3) while these tests drive `invoke()`; a middleware defining only
    `wrap_model_call` raises `NotImplementedError` under `ainvoke()`, so "it works in the suite" and
    "it works in production" are different claims until both are exercised.
    """
    append_line(kb / "Cooking" / "notes" / "summary.md", "MARKER-BOTH-VARIANTS")

    sync_model = scripted(says("done"))
    build(KbBreadthMiddleware.for_topic(kb, "Cooking"), sync_model).invoke(
        {"messages": [{"role": "user", "content": "hello"}]}
    )
    async_model = scripted(says("done"))
    await build(KbBreadthMiddleware.for_topic(kb, "Cooking"), async_model).ainvoke(
        {"messages": [{"role": "user", "content": "hello"}]}
    )

    assert "MARKER-BOTH-VARIANTS" in breadth_block(system_texts(async_model)[0])
    assert breadth_block(system_texts(sync_model)[0]) == breadth_block(system_texts(async_model)[0])


# --------------------------------------------------------------------------------------
# EX-7 — the degraded cases, which are ordinary
# --------------------------------------------------------------------------------------


def test_a_missing_breadth_file_still_builds_and_is_named_ex7(kb: Path) -> None:
    """A topic mid-creation is the normal case: the turn runs and the block says what is missing.

    Naming the absent file beats omitting it. An expert whose `topic.md` does not exist yet is
    usually about to draft one, and "not present" is information; a silently shorter block is
    indistinguishable from an empty file.
    """
    (kb / "Cooking" / "topic.md").unlink()
    append_line(kb / "Cooking" / "notes" / "summary.md", "MARKER-SURVIVING-SIBLING")

    block = one_turn(kb, KbBreadthMiddleware.for_topic(kb, "Cooking"))

    assert '<file path="/kb/Cooking/topic.md" note="not present" />' in block
    assert "MARKER-SURVIVING-SIBLING" in block


def test_a_topic_with_no_files_at_all_still_builds_ex7(kb: Path) -> None:
    """Both files absent is still a working agent, not a construction failure (EX-7)."""
    block = one_turn(kb, KbBreadthMiddleware.for_topic(kb, "Physics/does/not/exist"))

    assert 'note="not present"' in block
    assert BLOCK_OPEN in block


def test_a_freshly_scaffolded_placeholder_is_passed_through_verbatim_ex7(kb: Path) -> None:
    """The scaffolder's placeholder is content, and is neither detected nor stripped.

    `scaffold_topic` writes a body that opens with "Placeholder." and then says what the file is
    for, so it already tells the model exactly what it is. Any detector here would be a second copy
    of Layer 1's scaffold prose (§8) that stops matching the day the wording changes — and it would
    fail silently, dropping a real file, rather than loudly.
    """
    block = one_turn(kb, KbBreadthMiddleware.for_topic(kb, "Cooking"))

    for relative in ("topic.md", "notes/summary.md"):
        body = (kb / "Cooking" / relative).read_text(encoding="utf-8").strip()
        assert body in block


def test_an_unreadable_breadth_file_does_not_take_down_the_turn_ex7(kb: Path) -> None:
    """Bytes that are not UTF-8 are reported in the block, not raised out of the model call.

    The conversation in which the human would fix a broken file is the conversation this exception
    would have killed — and an exception in `wrap_model_call` also aborts the superstep, which
    skips the maintenance flush with it (D-1).
    """
    (kb / "Cooking" / "notes" / "summary.md").write_bytes(b"---\ntitle: \xff\xfe broken\n---\n")

    block = one_turn(kb, KbBreadthMiddleware.for_topic(kb, "Cooking"))

    assert '<file path="/kb/Cooking/notes/summary.md" note="not valid UTF-8" />' in block
    assert '<file path="/kb/Cooking/topic.md">' in block


def test_an_empty_breadth_file_is_reported_as_empty_ex7(kb: Path) -> None:
    """An empty file is not the same as a missing one, and the block distinguishes them."""
    (kb / "Cooking" / "notes" / "summary.md").write_text("   \n\n", encoding="utf-8")

    block = one_turn(kb, KbBreadthMiddleware.for_topic(kb, "Cooking"))

    assert '<file path="/kb/Cooking/notes/summary.md" note="empty" />' in block


def test_an_oversized_breadth_file_is_truncated_with_a_notice_ex7(kb: Path) -> None:
    """A file large enough to matter is cut at the cap, and the block says so (EX-7).

    Breadth files are supposed to get sharper rather than longer, so reaching the cap is itself a
    signal — but paying for a pasted transcript on every model call, forever, is not a signal, it is
    a bill. The notice names the path so the model can read the rest deliberately.
    """
    summary = kb / "Cooking" / "notes" / "summary.md"
    lines = [f"line {number:06d} " + "x" * 60 for number in range(2000)]
    summary.write_text("MARKER-FIRST-LINE\n" + "\n".join(lines) + "\nMARKER-LAST-LINE\n", "utf-8")
    assert summary.stat().st_size > MAX_SOURCE_BYTES

    block = one_turn(kb, KbBreadthMiddleware.for_topic(kb, "Cooking"))

    assert "MARKER-FIRST-LINE" in block
    assert "MARKER-LAST-LINE" not in block
    assert "truncated" in block
    assert "/kb/Cooking/notes/summary.md" in block
    assert len(block) < 2 * MAX_SOURCE_BYTES + len(BLOCK_OPEN) + len(BLOCK_CLOSE) + 4096


def test_a_multibyte_character_on_the_cap_boundary_is_not_reported_as_broken_ex7(kb: Path) -> None:
    """Truncation must not turn a valid file into a "not valid UTF-8" report.

    The cap is a byte count, so it can land in the middle of a multi-byte character. Without the
    back-off in the decoder this test's file — perfectly valid UTF-8 — would be dropped from the
    block entirely, and only for files of an unlucky length.
    """
    summary = kb / "Cooking" / "notes" / "summary.md"
    summary.write_text("MARKER-HEAD\n" + "é" * MAX_SOURCE_BYTES, encoding="utf-8")

    block = one_turn(kb, KbBreadthMiddleware.for_topic(kb, "Cooking"))

    assert "not valid UTF-8" not in block
    assert "MARKER-HEAD" in block
    assert "truncated" in block


# --------------------------------------------------------------------------------------
# LB-4 / LB-5 — the Librarian's block
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_the_librarian_block_is_the_root_catalog_only_lb4(kb: Path) -> None:
    """The Librarian carries the generated routing view and nothing topic-scoped (LB-4, LB-5).

    Root `index.md` is one line per topic and bounded under 8 KB, which is what makes it affordable
    every turn. A topic's breadth files in the Librarian's context would make it answer instead of
    route, which is the one thing README §1.1 goal 2 asks it not to do.

    Superseded by Phase 1's Task 6: there is no root ``index.md`` any more (T-37) and the fixture's
    ``append_line(kb / "index.md", ...)`` now raises ``FileNotFoundError`` before the assertions run
    — ``scaffold_topic``'s bare rebuild writes only ``tags.md``. Fixing this is Phase 3's job
    (rebuilding ``pkb.agents`` against DESIGN.md), not a one-line repoint: swapping
    ``librarian_breadth_sources()`` from ``INDEX_FILE`` to ``TAGS_FILE`` would falsify this
    middleware's whole "bounded under 8 KB, so it is loaded every turn; the registry is unbounded,
    so it is named instead" split, baked into ``MAX_SOURCE_BYTES``'s own docstring and
    ``librarian_breadth_sources``'s (breadth.py:136-144) — the registry now carries a tag tree, a
    skills catalog and cross-topic mappings and is no longer the small, line-per-topic file that
    reasoning depends on.
    """
    append_line(kb / "index.md", "MARKER-ROOT-CATALOG")
    append_line(kb / "Cooking" / "notes" / "summary.md", "MARKER-TOPIC-SCOPED")

    block = one_turn(kb, KbBreadthMiddleware.for_librarian(kb))

    assert "MARKER-ROOT-CATALOG" in block
    assert "MARKER-TOPIC-SCOPED" not in block
    assert '<file path="/kb/index.md">' in block
    assert "/kb/Cooking" not in block
    assert "tags.md" not in block


def test_an_empty_knowledge_base_still_produces_a_working_librarian_lb4(empty_kb: Path) -> None:
    """Bootstrapping: no root catalog yet, and the turn still completes (LB-6)."""
    block = one_turn(empty_kb, KbBreadthMiddleware.for_librarian(empty_kb))

    assert '<file path="/kb/index.md" note="not present" />' in block


# --------------------------------------------------------------------------------------
# Shape — MW-4, EX-15, and the source lists themselves
# --------------------------------------------------------------------------------------


def test_the_middleware_holds_no_mutable_state_mw4(kb: Path) -> None:
    """One instance serves every run of a compiled graph, so nothing may be written on `self`.

    Two delegated experts can run concurrently inside a single Librarian turn (LB-8); an attribute
    mutated during a run would be shared between unrelated conversations. This is also why there is
    no content cache — see the module docstring of `pkb.agents.middleware.breadth`.
    """
    middleware = KbBreadthMiddleware.for_topic(kb, "Cooking")
    before = dict(vars(middleware))

    one_turn(kb, middleware)

    assert vars(middleware) == before


def test_the_name_does_not_collide_with_a_core_stack_member_ex15(kb: Path) -> None:
    """A custom middleware named after a core one *replaces* it in place rather than appending.

    `_apply_custom_middleware` merges by `.name`, so a collision does not raise — it silently
    removes the harness member the KB depends on (the filesystem tools, the gates, the skills).
    """
    core_names = {
        cls.__name__
        for cls in (
            deepagents.middleware.FilesystemMiddleware,
            deepagents.middleware.MemoryMiddleware,
            deepagents.middleware.SkillsMiddleware,
            deepagents.middleware.SubAgentMiddleware,
            deepagents.middleware.SummarizationMiddleware,
            HumanInTheLoopMiddleware,
        )
    }
    middleware = KbBreadthMiddleware.for_topic(kb, "Cooking")

    assert middleware.name == "KbBreadthMiddleware"
    assert middleware.name not in core_names


def test_the_source_lists_are_kb_relative_and_exclude_the_index_ex7() -> None:
    """The two source lists, stated once, in Layer 1's vocabulary — no mount, no `index.md`."""
    assert topic_breadth_sources("Cooking") == ("Cooking/topic.md", "Cooking/notes/summary.md")
    assert topic_breadth_sources(GRILLING) == (
        f"{GRILLING}/topic.md",
        f"{GRILLING}/notes/summary.md",
    )
    assert librarian_breadth_sources() == ("index.md",)
    assert not any(source.startswith("/") for source in topic_breadth_sources("Cooking"))


def test_an_empty_topic_path_is_refused_ex7(kb: Path) -> None:
    """An empty topic path would silently address the knowledge-base root's own `topic.md`.

    The same refusal happens at construction rather than at the first model call: an exception
    raised inside `wrap_model_call` aborts the superstep, and that takes the maintenance flush with
    it (D-1). A misconfigured factory should fail while the graph is being built.
    """
    for bad in ("", "   ", "/", "//"):
        with pytest.raises(ValueError, match="topic path"):
            topic_breadth_sources(bad)

    with pytest.raises(ValueError):
        KbBreadthMiddleware(kb, ["/"])
