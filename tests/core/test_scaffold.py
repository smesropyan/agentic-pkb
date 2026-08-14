"""Rules covered: SC-1 … SC-12, plus the PA-4 placement refusal the scaffolder owns.

The load-bearing test here is ``test_every_scaffolded_file_validates_with_zero_errors_sc3``: a
scaffolder that emits invalid placeholders poisons every topic at the moment it is created, and the
defect only surfaces later, on someone else's write.
"""

from __future__ import annotations

import ast
import itertools
import re
import sys
from datetime import date
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from pkb.core import frontmatter, paths, scaffold
from pkb.core.errors import (
    KbNotFoundError,
    NotATopicRootError,
    ScaffoldError,
    TopicDepthExceededError,
    errors_only,
)
from pkb.core.models import ScaffoldResult
from pkb.core.scaffold import member_paths, scaffold_subtopic, scaffold_topic
from pkb.core.validation import validate_content, validate_tree

TODAY = date(2024, 9, 1)
"""Injected everywhere: no test in this file reads the wall clock (CX-2, MA-3)."""

COOKING = "Home cooking: technique, equipment, and recipes"

_KB_COUNTER = itertools.count()
"""Unique knowledge-base names for the Hypothesis test, whose ``tmp_path`` is reused per example."""


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def entries(kb_root: Path, base: Path) -> set[str]:
    """Every path at or under ``base``, as knowledge-base-relative POSIX strings."""
    return {paths.rel(kb_root, base), *(paths.rel(kb_root, path) for path in base.rglob("*"))}


def make_kb(tmp_path: Path, name: str = "KB") -> Path:
    root = tmp_path / name
    root.mkdir()
    return root


def cooking(kb_root: Path, *, regenerate: bool = False) -> ScaffoldResult:
    return scaffold_topic(
        kb_root, "Cooking", title="Cooking", description=COOKING, today=TODAY, regenerate=regenerate
    )


def normalized(value: str) -> str:
    """What the scaffolder writes for a caller-supplied ``title`` / ``description``."""
    return re.sub(r"\s+", " ", value).strip()


# --------------------------------------------------------------------------------------
# SC-1 / SC-4 — the member set
# --------------------------------------------------------------------------------------


def test_scaffold_creates_exactly_the_standard_members_sc1(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    result = cooking(kb)

    assert entries(kb, kb / "Cooking") == set(member_paths(kb, "Cooking"))
    assert result.created == member_paths(kb, "Cooking")
    assert result.skipped == []
    assert sorted(member_paths(kb, "Cooking")) == [
        "Cooking",
        "Cooking/notes",
        "Cooking/notes/summary.md",
        "Cooking/references",
        "Cooking/references/summary.md",
        "Cooking/topic.md",
    ]
    assert [entry.name for entry in kb.iterdir()] == ["Cooking"]


def test_deleting_a_breadth_summary_is_reported_by_validate_tree_sc1(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    cooking(kb)
    (kb / "Cooking" / "notes" / "summary.md").unlink()

    findings = [f for f in validate_tree(kb) if f.code == "MISSING_REQUIRED_FILE"]
    assert [(f.rule_id, f.path) for f in findings] == [("SC-1", "Cooking/notes/summary.md")]


@pytest.mark.superseded
def test_scaffold_creates_no_optional_members_sc4(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    cooking(kb, regenerate=True)
    topic = kb / "Cooking"

    assert not (topic / paths.EXPERT_FILE).exists()
    assert not (topic / paths.SKILLS_DIR).exists()
    assert not (topic / paths.SUBTOPICS_DIR).exists()
    assert paths.extension_folders(topic) == []
    assert validate_tree(kb) == []  # not even a warning about what is missing


# --------------------------------------------------------------------------------------
# SC-2 — the placeholder topic.md makes the topic addressable
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_scaffolded_topic_is_discoverable_and_catalogued_sc2(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    cooking(kb, regenerate=True)

    assert paths.find_topic_roots(kb) == [kb / "Cooking"]
    assert paths.agent_id_for(kb, kb / "Cooking") == "topic/cooking"

    meta = frontmatter.parse((kb / "Cooking" / paths.TOPIC_FILE).read_text(encoding="utf-8")).meta
    assert meta is not None
    assert meta.title == "Cooking"
    assert meta.description == COOKING
    assert meta.topic == "Cooking"
    assert meta.tags == ("topic.cooking", "type.summary", "status.draft")
    assert meta.created == meta.updated == TODAY
    assert meta.source_type == "summary"

    catalog = (kb / paths.INDEX_FILE).read_text(encoding="utf-8")
    assert f"- [Cooking](Cooking/{paths.TOPIC_FILE}) `topic/cooking`" in catalog


# --------------------------------------------------------------------------------------
# SC-3 — every placeholder validates
# --------------------------------------------------------------------------------------


def test_every_scaffolded_file_validates_with_zero_errors_sc3(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    result = scaffold_topic(
        kb,
        "Cooking",
        title="Cooking",
        description=COOKING,
        today=TODAY,
        regenerate=True,
    )
    scaffold_subtopic(
        kb,
        "Cooking",
        "Grilling",
        title="Grilling",
        description="Charcoal and gas grilling",
        today=TODAY,
    )

    written = [rel_path for rel_path in result.created if (kb / rel_path).is_file()]
    assert len(written) == 3
    for rel_path in written:
        text = (kb / rel_path).read_text(encoding="utf-8")
        assert errors_only(validate_content(kb, rel_path, text)) == [], rel_path

    assert validate_tree(kb) == []


def test_a_placeholder_topic_md_validates_before_it_is_written_sc3(tmp_path: Path) -> None:
    """The pre-write gate (VA-1) must accept the scaffolder's own bytes, or SC-7 could never run."""
    source = make_kb(tmp_path, "Source")
    cooking(source)
    text = (source / "Cooking" / paths.TOPIC_FILE).read_text(encoding="utf-8")

    empty = make_kb(tmp_path, "Empty")
    assert errors_only(validate_content(empty, f"Cooking/{paths.TOPIC_FILE}", text)) == []


@settings(
    max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(title=st.text(min_size=1, max_size=60), description=st.text(min_size=1, max_size=120))
def test_placeholders_validate_for_any_title_and_description_sc3(
    tmp_path: Path, title: str, description: str
) -> None:
    """SC-3 is a property of the writer, not of one fixture: any caller string must come out valid."""
    assume(normalized(title) and normalized(description))
    kb = make_kb(tmp_path, f"KB{next(_KB_COUNTER)}")

    result = scaffold_topic(
        kb, "Cooking", title=title, description=description, today=TODAY, regenerate=False
    )
    for rel_path in result.created:
        target = kb / rel_path
        if not target.is_file():
            continue
        assert errors_only(validate_content(kb, rel_path, target.read_text(encoding="utf-8"))) == []

    meta = frontmatter.parse((kb / "Cooking" / paths.TOPIC_FILE).read_text(encoding="utf-8")).meta
    assert meta is not None
    assert meta.title == normalized(title)
    assert meta.description == normalized(description)
    assert errors_only(validate_tree(kb)) == []


def test_a_blank_title_is_refused_sc3(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    with pytest.raises(ScaffoldError, match="title"):
        scaffold_topic(
            kb, "Cooking", title=" \n\t ", description=COOKING, today=TODAY, regenerate=False
        )
    assert list(kb.iterdir()) == []


def test_a_blank_description_is_refused_sc3(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    with pytest.raises(ScaffoldError, match="description"):
        scaffold_topic(
            kb, "Cooking", title="Cooking", description="\n\n", today=TODAY, regenerate=False
        )
    assert list(kb.iterdir()) == []


# --------------------------------------------------------------------------------------
# SC-5 / SC-6 — depth agnosticism
# --------------------------------------------------------------------------------------


def test_scaffold_is_depth_agnostic_sc5(tmp_path: Path) -> None:
    flat = make_kb(tmp_path, "Flat")
    scaffold_topic(
        flat, "Grilling", title="Grilling", description="x", today=TODAY, regenerate=False
    )

    nested = make_kb(tmp_path, "Nested")
    cooking(nested)
    scaffold_subtopic(
        nested,
        "Cooking",
        "Grilling",
        title="Grilling",
        description="x",
        today=TODAY,
        regenerate=False,
    )

    flat_members = {p.removeprefix("Grilling") for p in entries(flat, flat / "Grilling")}
    nested_root = nested / "Cooking" / paths.SUBTOPICS_DIR / "Grilling"
    nested_members = {
        p.removeprefix("Cooking/sub-topics/Grilling") for p in entries(nested, nested_root)
    }
    assert flat_members == nested_members


def test_scaffold_subtopic_writes_under_sub_topics_sc6(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    cooking(kb)

    result = scaffold_subtopic(
        kb,
        "Cooking",
        "Grilling",
        title="Grilling",
        description="Charcoal and gas grilling",
        today=TODAY,
        regenerate=False,
    )

    assert result.topic_path == "Cooking/sub-topics/Grilling"
    assert (kb / "Cooking" / "sub-topics" / "Grilling" / paths.TOPIC_FILE).is_file()
    assert paths.topic_tag_for(kb, kb / result.topic_path) == "topic.cooking.grilling"

    meta = frontmatter.parse(
        (kb / result.topic_path / paths.TOPIC_FILE).read_text(encoding="utf-8")
    ).meta
    assert meta is not None
    assert meta.topic == "Grilling"  # the nearest owning root's display name (VA-12, Q4)
    assert meta.tags[0] == "topic.cooking.grilling"


def test_scaffold_subtopic_requires_a_real_parent_topic_root_sc6(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    (kb / "Physics").mkdir()
    with pytest.raises(NotATopicRootError, match=paths.TOPIC_FILE):
        scaffold_subtopic(
            kb, "Physics", "Optics", title="Optics", description="x", today=TODAY, regenerate=False
        )


@pytest.mark.parametrize("location", ["Cooking/notes/Grilling", "Cooking/recipes/Grilling"])
def test_a_nested_topic_must_go_through_sub_topics_pa4(tmp_path: Path, location: str) -> None:
    kb = make_kb(tmp_path)
    cooking(kb)
    with pytest.raises(ScaffoldError, match="sub-topics"):
        scaffold_topic(kb, location, title="Grilling", description="x", today=TODAY)


# --------------------------------------------------------------------------------------
# SC-7 — regeneration is part of the operation
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_scaffold_regenerates_derived_files_in_the_same_operation_sc7(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    result = scaffold_topic(
        kb, "Cooking", title="Cooking", description=COOKING, today=TODAY, regenerate=True
    )

    assert result.flush is not None
    assert result.flush.written == ["index.md", "tags.md", "Cooking/index.md"]
    assert (kb / "Cooking" / paths.INDEX_FILE).is_file()
    assert "Cooking" in (kb / paths.INDEX_FILE).read_text(encoding="utf-8")
    # None of it came from the scaffolder itself.
    assert paths.INDEX_FILE not in {Path(p).name for p in result.created}


def test_scaffold_without_regeneration_writes_no_derived_file_sc7(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    result = scaffold_topic(
        kb, "Cooking", title="Cooking", description=COOKING, today=TODAY, regenerate=False
    )

    assert result.flush is None
    assert not (kb / paths.INDEX_FILE).exists()
    assert not (kb / paths.TAGS_FILE).exists()
    assert not (kb / "Cooking" / paths.INDEX_FILE).exists()


# --------------------------------------------------------------------------------------
# SC-8 — no approval gate, no agent machinery
# --------------------------------------------------------------------------------------


def test_scaffold_has_no_approval_gate_and_no_agent_imports_sc8() -> None:
    source = Path(scaffold.__file__).read_text(encoding="utf-8")
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert roots <= sys.stdlib_module_names | {"pkb"}

    forbidden = ("approve", "approval", "interrupt", "hitl", "confirm")
    for function in (scaffold_topic, scaffold_subtopic):
        names = " ".join(function.__code__.co_varnames)
        assert not any(word in names.lower() for word in forbidden), function.__name__


# --------------------------------------------------------------------------------------
# SC-9 — the four-level tag budget
# --------------------------------------------------------------------------------------


def test_scaffold_refuses_a_fifth_topic_tag_level_sc9(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    cooking(kb)
    chain = "Cooking"
    for name in ("Grilling", "Charcoal"):
        scaffold_subtopic(
            kb, chain, name, title=name, description="x", today=TODAY, regenerate=False
        )
        chain = f"{chain}/{paths.SUBTOPICS_DIR}/{name}"

    # Four levels is the maximal legal form and must have been created.
    assert paths.topic_tag_for(kb, kb / chain) == "topic.cooking.grilling.charcoal"
    assert (kb / chain / paths.TOPIC_FILE).is_file()

    with pytest.raises(TopicDepthExceededError, match=r"at most 4 levels"):
        scaffold_subtopic(kb, chain, "Gas", title="Gas", description="x", today=TODAY)
    assert not (kb / chain / paths.SUBTOPICS_DIR).exists()


# --------------------------------------------------------------------------------------
# SC-10 — never overwrite
# --------------------------------------------------------------------------------------


def test_rescaffolding_creates_only_what_is_missing_sc10(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    cooking(kb)
    original = {
        rel_path: (kb / rel_path).read_bytes()
        for rel_path in member_paths(kb, "Cooking")
        if (kb / rel_path).is_file()
    }

    again = scaffold_topic(
        kb,
        "Cooking",
        title="A completely different title",
        description="A completely different description",
        today=date(2030, 6, 6),
        regenerate=False,
    )
    assert again.created == []
    assert again.skipped == member_paths(kb, "Cooking")
    assert {p: (kb / p).read_bytes() for p in original} == original

    (kb / "Cooking" / "notes" / "summary.md").unlink()
    repaired = scaffold_topic(
        kb, "Cooking", title="Cooking", description=COOKING, today=TODAY, regenerate=False
    )
    assert repaired.created == ["Cooking/notes/summary.md"]
    assert "Cooking/topic.md" in repaired.skipped
    assert (kb / "Cooking" / paths.TOPIC_FILE).read_bytes() == original["Cooking/topic.md"]


# --------------------------------------------------------------------------------------
# SC-11 — name validation
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("!!!", id="slugifies-to-empty"),
        pytest.param("---", id="slugifies-to-empty-dashes"),
        pytest.param("notes", id="structural-notes"),
        pytest.param("Notes", id="structural-by-slug"),
        pytest.param("sub-topics", id="structural-sub-topics"),
        pytest.param("skills", id="structural-skills"),
        pytest.param("index", id="reserved-index"),
        pytest.param("summary", id="reserved-summary"),
        pytest.param("topic.md", id="reserved-file-name"),
    ],
)
def test_scaffold_rejects_an_unusable_topic_name_sc11(tmp_path: Path, name: str) -> None:
    kb = make_kb(tmp_path)
    with pytest.raises(ScaffoldError):
        scaffold_topic(kb, name, title="T", description="x", today=TODAY, regenerate=False)
    assert list(kb.iterdir()) == []


def test_scaffold_rejects_a_sibling_slug_collision_sc11(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    scaffold_topic(
        kb,
        "Heat Management",
        title="Heat Management",
        description="x",
        today=TODAY,
        regenerate=False,
    )
    with pytest.raises(ScaffoldError, match="heat-management"):
        scaffold_topic(
            kb,
            "heat_management",
            title="Heat Management",
            description="x",
            today=TODAY,
            regenerate=False,
        )
    assert [entry.name for entry in kb.iterdir()] == ["Heat Management"]


def test_a_legal_name_with_punctuation_and_accents_is_accepted_sc11(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    result = scaffold_topic(
        kb, "Café Noir", title="Café Noir", description="Coffee", today=TODAY, regenerate=False
    )
    assert result.topic_path == "Café Noir"
    assert paths.topic_tag_for(kb, kb / "Café Noir") == "topic.cafe-noir"
    assert errors_only(validate_tree(kb)) == []


def test_scaffold_requires_an_existing_knowledge_base_root(tmp_path: Path) -> None:
    with pytest.raises(KbNotFoundError):
        scaffold_topic(
            tmp_path / "missing",
            "Cooking",
            title="C",
            description="x",
            today=TODAY,
            regenerate=False,
        )


# --------------------------------------------------------------------------------------
# SC-12 — no invented tags
# --------------------------------------------------------------------------------------


def test_scaffold_seeds_no_tags_beyond_the_placeholders_sc12(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    cooking(kb, regenerate=True)
    registry = (kb / paths.TAGS_FILE).read_text(encoding="utf-8")

    assert "## Namespace: topic.cooking" in registry
    # The section's only node is the root topic itself: no child was invented (SC-12).
    assert "topic.cooking." not in registry
    assert "## Cross-topic mappings" not in registry
    assert "## Namespace: domain" not in registry
