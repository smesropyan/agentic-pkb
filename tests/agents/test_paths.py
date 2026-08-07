"""The mount seam — RT-8, RT-9, RT-10.

The interesting tests here are not the string mappings; they are the two that drive a **real deep
agent** to show why the mappings exist. `test_naive_prefix_test_misses_a_real_write_rt9` writes a
file that a `startswith` check calls "not a knowledge-base path" and then finds it on disk, and
`test_traversal_error_comes_from_the_harness_rt10` shows the harness already says the right thing.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage

from pkb.agents.paths import KB_MOUNT, SKILLS_MOUNT, to_backend_path, to_kb_relative
from tests.agents.conftest import call, calls, says, scripted

SRC_PKB = Path(__file__).resolve().parents[2] / "src" / "pkb"


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _agent(kb: Path, model: BaseChatModel) -> Any:
    """A deep agent over the fixture knowledge base, with no permission rules at all.

    Deliberately unrestricted: these tests are about what a *middleware* can and cannot see, so the
    permission layer must not be what stops the write.
    """
    backend = CompositeBackend(
        default=StateBackend(),
        routes={KB_MOUNT: FilesystemBackend(root_dir=str(kb), virtual_mode=True)},
    )
    return create_deep_agent(model=model, backend=backend, system_prompt="File what you are told.")


def _tool_messages(messages: list[BaseMessage]) -> list[ToolMessage]:
    return [m for m in messages if isinstance(m, ToolMessage)]


def _generated_rel_paths() -> list[str]:
    """A hundred knowledge-base-relative paths covering the shapes Layer 1 actually produces."""
    topics = ["Cooking", "BBQ", "Physics", "Wood Working", "Émile"]
    folders = ["notes", "references/nyt", "media", "sub-topics/Grilling/notes"]
    names = ["a.md", "steak-2026.md", "summary.md", "b.c.md", "note (1).md"]
    return [f"{topic}/{folder}/{name}" for topic in topics for folder in folders for name in names]


# --------------------------------------------------------------------------------------
# RT-8 — one mount literal, one conversion pair
# --------------------------------------------------------------------------------------


def _code_strings(path: Path) -> list[str]:
    """Every string literal in a module *except* its docstrings.

    Comments never enter the AST, so they are excluded for free. This is the difference between
    testing the rule — the mount is spelled in one module — and testing the characters, which would
    forbid the docstring in `validation.py` that explains why a naive `startswith("/kb/")` is the
    wrong check (D-3). That prose is the reason the bug does not come back.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    # A string used as a *statement* is documentation by construction — it evaluates to nothing.
    # That covers module, class and function docstrings and the attribute docstrings this codebase
    # uses after a constant assignment.
    docstrings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_mount_literal_is_spelled_in_exactly_one_module_rt8() -> None:
    """`KB_MOUNT` is the only place the backend mount appears in code under `src/pkb` (RT-8, CX-3)."""
    offenders = sorted(
        path.relative_to(SRC_PKB).as_posix()
        for path in SRC_PKB.rglob("*.py")
        if any("/kb/" in literal for literal in _code_strings(path))
    )
    assert offenders == ["agents/paths.py"]


def test_backend_and_relative_paths_round_trip_rt8() -> None:
    """`to_kb_relative(to_backend_path(p)) == p` over a hundred generated paths (RT-8)."""
    generated = _generated_rel_paths()
    assert len(generated) == 100
    for rel in generated:
        backend_path = to_backend_path(rel)
        assert backend_path.startswith(KB_MOUNT)
        assert to_kb_relative(backend_path) == rel


def test_to_backend_path_tolerates_a_leading_slash_rt8() -> None:
    """Callers hold paths in either shape; both land on the same backend path (RT-8)."""
    assert to_backend_path("/Cooking/notes/a.md") == to_backend_path("Cooking/notes/a.md")


def test_to_backend_path_refuses_an_empty_relative_path_rt8() -> None:
    """An empty relative path would widen a topic scope to the whole tree, so it raises (RT-8)."""
    for empty in ("", "/", "//"):
        try:
            to_backend_path(empty)
        except ValueError:
            continue
        raise AssertionError(f"to_backend_path({empty!r}) should have raised")


def test_skills_mount_is_distinct_from_the_kb_mount_rt8() -> None:
    """The packaged-skill route is its own mount: nothing under it is a KB path (RT-6, RT-17)."""
    assert to_kb_relative(SKILLS_MOUNT + "voice/SKILL.md") is None
    assert to_kb_relative(SKILLS_MOUNT) is None


# --------------------------------------------------------------------------------------
# RT-9 — normalize first, because the middleware sees the raw model string
# --------------------------------------------------------------------------------------


def test_raw_model_strings_all_normalize_to_one_relative_path_rt9() -> None:
    """The four shapes a model actually emits collapse to the same KB-relative path (RT-9)."""
    assert to_kb_relative("/kb/x.md") == "x.md"
    assert to_kb_relative("kb/x.md") == "x.md"
    assert to_kb_relative("/kb//x.md") == "x.md"
    assert to_kb_relative("/kb/./x.md") == "x.md"
    assert to_kb_relative("/kb/Cooking/") == "Cooking"


def test_paths_outside_the_mount_are_not_kb_paths_rt9() -> None:
    """Scratch space, the skills mount, and a sibling directory are all `None` (RT-9)."""
    assert to_kb_relative("/scratch/x.md") is None
    assert to_kb_relative("scratch/x.md") is None
    assert to_kb_relative("/kbx/y.md") is None
    assert to_kb_relative("/kb") is None


def test_non_string_tool_arguments_are_not_kb_paths_rt9() -> None:
    """A model can put anything in `args["file_path"]`; none of it is a KB path (RT-9).

    An uncaught `AttributeError` in `wrap_tool_call` would abort the run — and an aborted run also
    skips the maintenance flush (D-1), so the tree would be left stale by a malformed tool call.
    """
    for junk in (None, 17, ["/kb/x.md"], {"file_path": "/kb/x.md"}):
        assert to_kb_relative(junk) is None


def test_naive_prefix_test_misses_a_real_write_rt9(kb: Path) -> None:
    """The bypass D-3 records, executed: `startswith` says no, the file lands anyway (RT-9).

    This is the whole reason `to_kb_relative` calls the harness normalizer. deepagents normalizes
    inside the tool body, *after* every `wrap_tool_call` middleware, so the middleware sees
    `kb/Cooking/notes/b.md` verbatim.
    """
    raw = "kb/Cooking/notes/b.md"
    model = scripted(
        calls(call("write_file", {"file_path": raw, "content": "landed"}, "t1")),
        says("filed"),
    )
    result = _agent(kb, model).invoke({"messages": [HumanMessage("file it")]})

    assert _tool_messages(result["messages"])[0].status == "success"
    assert (kb / "Cooking" / "notes" / "b.md").read_text() == "landed"

    assert raw.startswith(KB_MOUNT) is False  # the naive test a middleware must not use
    assert to_kb_relative(raw) == "Cooking/notes/b.md"  # what the tool actually did


@pytest.mark.asyncio
async def test_naive_prefix_test_misses_a_real_write_async_rt9(kb: Path) -> None:
    """The same bypass on the async path — the runtime is async-only (RT-3), so it must hold."""
    model = scripted(
        calls(call("write_file", {"file_path": "kb/BBQ/notes/c.md", "content": "landed"}, "t1")),
        says("filed"),
    )
    await _agent(kb, model).ainvoke({"messages": [HumanMessage("file it")]})
    assert (kb / "BBQ" / "notes" / "c.md").read_text() == "landed"


# --------------------------------------------------------------------------------------
# RT-10 — forward the harness's own refusal, never re-word it
# --------------------------------------------------------------------------------------


def test_harness_rejected_paths_are_not_kb_paths_rt10() -> None:
    """Traversal, home-relative and drive-prefixed paths yield `None`, never an exception (RT-10).

    `None` routes the call to `handler(request)` untouched, which is how deepagents gets to produce
    its own error. Raising here would abort the run; swallowing and inventing a message would give
    the model two different texts for one failure.
    """
    for rejected in ("/kb/../etc/passwd", "~/evil.md", "~", r"C:\evil.md", "/kb/../../x.md"):
        assert to_kb_relative(rejected) is None


def test_traversal_error_comes_from_the_harness_rt10(kb: Path) -> None:
    """One error ToolMessage, in deepagents' words, and nothing written (RT-10)."""
    model = scripted(
        calls(call("write_file", {"file_path": "/kb/../etc/passwd", "content": "x"}, "t1")),
        says("refused"),
    )
    result = _agent(kb, model).invoke({"messages": [HumanMessage("go")]})

    tool_messages = _tool_messages(result["messages"])
    assert len(tool_messages) == 1
    assert tool_messages[0].status == "error"
    assert str(tool_messages[0].content) == "Error: Path traversal not allowed: /kb/../etc/passwd"
    assert not (kb.parent / "etc" / "passwd").exists()
