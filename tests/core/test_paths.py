"""Rules PA-1 … PA-19 and CX-8 — the path vocabulary of the knowledge-base tree.

CX-8: the addressing helpers Layer 2 consumes rather than reimplementing live in
``pkb/core/paths.py`` — ``agent_id_for`` (:func:`test_agent_ids_are_bijective_with_topic_paths_pa10`),
``resolve_expert`` (:func:`test_expert_resolves_to_the_nearest_ancestor_topic_pa13`) and ``slugify``
(:func:`test_slugify_examples_pa8`) are exercised here, through ``pkb.core.paths``, as the exports
they are.

Every test name ends in the rule id it covers so a rule change is greppable. No network, no env
vars, no wall clock: the whole file runs against ``tmp_path``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pkb.core import paths
from pkb.core.errors import NotATopicRootError
from pkb.core.models import FileClass, FileRole

TAG_SEGMENT_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def write(path: Path, text: str = "") -> Path:
    """Create ``path`` (and its parents) with ``text``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    """The fixture tree of the rules document §4.1: two topics, a sub-topic, an extension folder."""
    write(tmp_path / "index.md")
    write(tmp_path / "tags.md")
    write(tmp_path / "skills" / "voice" / "SKILL.md")

    write(tmp_path / "BBQ" / "topic.md")
    write(tmp_path / "BBQ" / "notes" / "summary.md")
    write(tmp_path / "BBQ" / "references" / "summary.md")

    cooking = tmp_path / "Cooking"
    write(cooking / "topic.md")
    write(cooking / "index.md")
    write(cooking / "expert.md")
    write(cooking / "notes" / "summary.md")
    write(cooking / "notes" / "grill-performance-in-windy-conditions.md")
    write(cooking / "notes" / "old-idea" / "old-idea.md")
    write(cooking / "notes" / "old-idea" / "media" / "photo.jpg")
    write(cooking / "references" / "summary.md")
    write(cooking / "references" / "grill-basics" / "grill-basics.md")
    write(cooking / "references" / "grill-basics" / "grill-basics.pdf")
    write(cooking / "recipes" / "summary.md")
    write(cooking / "recipes" / "ribeye-on-gas.md")

    grilling = cooking / "sub-topics" / "Grilling"
    write(grilling / "topic.md")
    write(grilling / "index.md")
    write(grilling / "notes" / "summary.md")
    return tmp_path


# --------------------------------------------------------------------------------------
# PA-1 / PA-2 — the knowledge-base root
# --------------------------------------------------------------------------------------


def test_root_reserved_entries_are_not_topics_pa1(kb: Path) -> None:
    """The root holds ``index.md``, ``tags.md``, ``skills/`` plus one directory per topic."""
    assert paths.find_topic_roots(kb) == [
        kb / "BBQ",
        kb / "Cooking",
        kb / "Cooking" / "sub-topics" / "Grilling",
    ]
    # A stray root markdown file is not a topic and is not silently swallowed either.
    write(kb / "Cooking.md")
    assert paths.find_topic_roots(kb) == [
        kb / "BBQ",
        kb / "Cooking",
        kb / "Cooking" / "sub-topics" / "Grilling",
    ]
    assert paths.classify(kb, kb / "Cooking.md") == (FileRole.UNKNOWN, FileClass.AUTHORED)


def test_kb_root_is_never_a_topic_root_pa2(tmp_path: Path) -> None:
    assert paths.is_topic_root(tmp_path) is False
    assert paths.find_topic_roots(tmp_path) == []
    write(tmp_path / "index.md")
    assert paths.owning_topic_root(tmp_path, tmp_path / "index.md") is None
    assert paths.owning_topic_root(tmp_path, tmp_path) is None
    with pytest.raises(NotATopicRootError):
        paths.topic_tag_for(tmp_path, tmp_path)


def test_rel_rejects_paths_outside_the_kb_cx3(kb: Path) -> None:
    assert paths.rel(kb, kb / "Cooking" / "notes" / "summary.md") == "Cooking/notes/summary.md"
    assert paths.rel(kb, kb) == "."
    with pytest.raises(ValueError, match="not inside"):
        paths.rel(kb, kb.parent / "elsewhere" / "x.md")


# --------------------------------------------------------------------------------------
# PA-3 … PA-7 — topichood, discovery, structure
# --------------------------------------------------------------------------------------


def test_topic_md_is_the_sole_marker_of_topichood_pa3(tmp_path: Path) -> None:
    shaped = tmp_path / "LooksLikeATopic"
    write(shaped / "notes" / "summary.md")
    write(shaped / "references" / "summary.md")
    assert paths.is_topic_root(shaped) is False

    bare = tmp_path / "Bare"
    write(bare / "topic.md")
    assert paths.is_topic_root(bare) is True


def test_subtopics_is_a_literal_directory_name_pa4(kb: Path) -> None:
    assert paths.SUBTOPICS_DIR == "sub-topics"
    grilling = kb / "Cooking" / paths.SUBTOPICS_DIR / "Grilling"
    assert paths.is_topic_root(grilling) is True
    assert grilling in paths.find_topic_roots(kb)


def test_discovery_is_recursive_and_ordered_pa5(tmp_path: Path) -> None:
    for name in ("cooking", "BBQ", "Alpha"):
        write(tmp_path / name / "topic.md")
    write(tmp_path / "cooking" / "sub-topics" / "Grilling" / "topic.md")
    write(tmp_path / "cooking" / "sub-topics" / "Grilling" / "sub-topics" / "Charcoal" / "topic.md")

    assert paths.find_topic_roots(tmp_path) == [
        tmp_path / "Alpha",
        tmp_path / "BBQ",
        tmp_path / "cooking",
        tmp_path / "cooking" / "sub-topics" / "Grilling",
        tmp_path / "cooking" / "sub-topics" / "Grilling" / "sub-topics" / "Charcoal",
    ]


def test_discovery_never_descends_into_structural_dirs_pa5(kb: Path) -> None:
    write(kb / "Cooking" / "notes" / "Smuggled" / "topic.md")
    write(kb / "Cooking" / "skills" / "Hidden" / "topic.md")
    assert paths.find_topic_roots(kb) == [
        kb / "BBQ",
        kb / "Cooking",
        kb / "Cooking" / "sub-topics" / "Grilling",
    ]
    # VA-36 still needs to see the misplaced ones, so the widened walk is opt-in, never invisible.
    # It covers exactly the directories VA-36 names — notes/ and references/. skills/ is the
    # negative control: VA-6 makes a topic.md there not a topic, so neither walk publishes it, and
    # pkb.core.scan.RECORD_ONLY_DIRS says the same thing from the walk's side.
    assert paths.find_topic_roots(kb, include_misplaced=True) == [
        kb / "BBQ",
        kb / "Cooking",
        kb / "Cooking" / "notes" / "Smuggled",
        kb / "Cooking" / "sub-topics" / "Grilling",
    ]
    assert kb / "Cooking" / "skills" / "Hidden" not in paths.find_topic_roots(
        kb, include_misplaced=True
    )


def test_structural_dirs_contribute_no_tag_segment_pa6(kb: Path) -> None:
    assert sorted(paths.STRUCTURAL_DIRS) == [
        "media",
        "notes",
        "references",
        "skills",
        "sub-topics",
    ]
    note = kb / "Cooking" / "notes" / "grill-performance-in-windy-conditions.md"
    owner = paths.owning_topic_root(kb, note)
    assert owner is not None
    assert paths.topic_tag_for(kb, owner) == "topic.cooking"

    # The branch the file case cannot reach: a topic root *under* a structural directory. VA-36
    # warns about it and keeps it, so it carries an address — and no structural directory may
    # appear in that address, or the ontology grows a phantom `topic.cooking.notes` node and the
    # tag burns two of TG-3's four levels.
    smuggled = write(kb / "Cooking" / "notes" / "Smuggled" / "topic.md").parent
    assert paths.topic_tag_for(kb, smuggled) == "topic.cooking.smuggled"
    assert paths.agent_id_for(kb, smuggled) == "topic/cooking/smuggled"

    for structural in paths.STRUCTURAL_DIRS:
        nested = write(kb / "BBQ" / structural / "Deep" / "topic.md").parent
        assert paths.topic_tag_for(kb, nested) == "topic.bbq.deep"
        assert paths.agent_id_for(kb, nested) == "topic/bbq/deep"


# --------------------------------------------------------------------------------------
# PA-8 — slugify
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Cooking", "cooking"),
        ("Heat Management", "heat-management"),
        ("Café Noir", "cafe-noir"),
        ("Ünïcödé", "unicode"),
        ("heat_management", "heat-management"),
        ("sub/topic", "sub-topic"),
        ("  spaced   out  ", "spaced-out"),
        ("Grilling 101", "grilling-101"),
        ("C++", "c"),
        ("--dashes--", "dashes"),
        ("!!!", ""),
        ("Привет", ""),
        ("x" * 120, "x" * 80),
    ],
    ids=[
        "pa8-simple",
        "pa8-two-words",
        "pa8-accents",
        "pa8-umlauts",
        "pa8-underscore",
        "pa8-slash",
        "pa8-whitespace-runs",
        "pa8-digits",
        "pa8-symbols",
        "pa8-dash-collapse",
        "pa8-empty-result",
        "pa8-unmappable-script",
        "pa8-length-cap",
    ],
)
def test_slugify_examples_pa8(name: str, expected: str) -> None:
    assert paths.slugify(name) == expected


@given(st.text(max_size=200))
def test_slugify_is_total_and_idempotent_pa8(name: str) -> None:
    """The property the tag layer depends on: a slug is a legal tag segment, or empty (TG-4)."""
    slug = paths.slugify(name)
    assert slug == "" or TAG_SEGMENT_RE.match(slug)
    assert len(slug) <= paths.MAX_SLUG_LENGTH
    assert paths.slugify(slug) == slug


# --------------------------------------------------------------------------------------
# PA-9 / PA-10 — addressing
# --------------------------------------------------------------------------------------


def test_topic_tag_elides_subtopics_and_round_trips_pa9(tmp_path: Path) -> None:
    write(tmp_path / "Cooking" / "topic.md")
    grilling = tmp_path / "Cooking" / "sub-topics" / "Grilling"
    write(grilling / "topic.md")
    charcoal = grilling / "sub-topics" / "Charcoal Grilling"
    write(charcoal / "topic.md")

    expected = {
        tmp_path / "Cooking": "topic.cooking",
        grilling: "topic.cooking.grilling",
        charcoal: "topic.cooking.grilling.charcoal-grilling",
    }
    for topic, tag in expected.items():
        assert paths.topic_tag_for(tmp_path, topic) == tag
        assert "sub-topics" not in tag
        assert paths.path_for_topic_tag(tmp_path, tag) == topic

    assert paths.path_for_topic_tag(tmp_path, "topic.atlantis") is None


def test_agent_ids_are_bijective_with_topic_paths_pa10(kb: Path) -> None:
    grilling = kb / "Cooking" / "sub-topics" / "Grilling"
    assert paths.agent_id_for(kb, grilling) == "topic/cooking/grilling"
    assert paths.topic_path_for_agent_id(kb, "topic/cooking/grilling") == grilling

    assert paths.agent_id_for(kb, kb) == paths.LIBRARIAN_AGENT_ID
    assert paths.topic_path_for_agent_id(kb, paths.LIBRARIAN_AGENT_ID) == kb

    for topic in paths.find_topic_roots(kb):
        assert paths.topic_path_for_agent_id(kb, paths.agent_id_for(kb, topic)) == topic

    with pytest.raises(NotATopicRootError):
        paths.topic_path_for_agent_id(kb, "topic/atlantis")


# --------------------------------------------------------------------------------------
# PA-11 / PA-12 — derived by name vs written by a generator
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel_path", "expected"),
    [
        ("index.md", True),
        ("Cooking/index.md", True),
        ("Cooking/notes/old-idea/index.md", True),
        ("tags.md", True),
        ("Cooking/tags.md", False),
        ("Cooking/topic.md", False),
        ("Cooking/notes/summary.md", False),
    ],
    ids=[
        "pa11-root-index",
        "pa11-topic-index",
        "pa11-nested-index",
        "pa11-root-tags",
        "pa11-topic-tags-is-not-derived",
        "pa11-topic-md",
        "pa11-summary",
    ],
)
def test_is_derived_name_matches_the_deny_globs_pa11(
    kb: Path, rel_path: str, expected: bool
) -> None:
    assert paths.is_derived_name(kb, kb / rel_path) is expected


@pytest.mark.parametrize(
    ("rel_path", "expected"),
    [
        ("tags.md", True),
        ("Cooking/index.md", True),
        ("Cooking/sub-topics/Grilling/index.md", True),
        ("Cooking/notes/old-idea/index.md", False),
        ("Cooking/tags.md", False),
        ("Cooking/topic.md", False),
    ],
    ids=[
        "pa12-root-tags",
        "pa12-topic-index",
        "pa12-subtopic-index",
        "pa12-item-index-is-not-generated",
        "pa12-topic-tags",
        "pa12-topic-md",
    ],
)
def test_is_generated_is_the_generator_owned_set_pa12(
    kb: Path, rel_path: str, expected: bool
) -> None:
    assert paths.is_generated(kb, kb / rel_path) is expected


# --------------------------------------------------------------------------------------
# PA-13 / PA-14 — expert and skill resolution
# --------------------------------------------------------------------------------------


def test_expert_resolves_to_the_nearest_ancestor_topic_pa13(kb: Path) -> None:
    grilling = kb / "Cooking" / "sub-topics" / "Grilling"
    assert paths.resolve_expert(kb, grilling) == kb / "Cooking" / "expert.md"

    write(grilling / "expert.md")
    assert paths.resolve_expert(kb, grilling) == grilling / "expert.md"

    assert paths.resolve_expert(kb, kb / "BBQ") is None
    assert paths.resolve_expert(kb, kb) is None


def test_a_directory_named_expert_md_is_not_an_expert_pa13(kb: Path) -> None:
    """PA-13 resolves a prompt Layer 2 will read; a directory is an extension folder (PA-7).

    ``mkdir expert.md`` is a legal tree — VA-38 flags loose *files* at a topic root, never
    directories — so the resolver cannot trust the name alone, or the caller gets an
    ``IsADirectoryError`` instead of a prompt.
    """
    bbq = kb / "BBQ"
    (bbq / "expert.md").mkdir()
    assert paths.has_case_exact_entry(bbq, "expert.md") is True
    assert paths.has_case_exact_file(bbq, "expert.md") is False
    assert paths.resolve_expert(kb, bbq) is None

    # The nearest *file* still wins over a nearer directory of the same name.
    grilling = kb / "Cooking" / "sub-topics" / "Grilling"
    (grilling / "expert.md").mkdir()
    assert paths.resolve_expert(kb, grilling) == kb / "Cooking" / "expert.md"


def test_topic_skills_shadow_root_skills_pa14(kb: Path) -> None:
    write(kb / "skills" / "discovery" / "SKILL.md")
    write(kb / "skills" / "legacy.md")  # flat layout: not a skill (C2), reported by validation
    cooking = kb / "Cooking"
    write(cooking / "skills" / "voice" / "SKILL.md")
    grilling = cooking / "sub-topics" / "Grilling"

    root_skills = paths.resolve_skills(kb, kb)
    assert root_skills == {
        "discovery": kb / "skills" / "discovery" / "SKILL.md",
        "voice": kb / "skills" / "voice" / "SKILL.md",
    }

    for topic in (cooking, grilling):
        resolved = paths.resolve_skills(kb, topic)
        assert set(resolved) == {"discovery", "voice"}
        assert resolved["voice"] == cooking / "skills" / "voice" / "SKILL.md"
        assert resolved["discovery"] == kb / "skills" / "discovery" / "SKILL.md"


def test_a_directory_named_skill_md_is_not_a_skill_pa14(kb: Path) -> None:
    """The PA-13 twin: a skill is a ``SKILL.md`` *file*, not any entry wearing the name."""
    (kb / "skills" / "ghost").mkdir(parents=True, exist_ok=True)
    (kb / "skills" / "ghost" / "SKILL.md").mkdir()
    assert paths.has_case_exact_entry(kb / "skills" / "ghost", "SKILL.md") is True
    assert paths.has_case_exact_file(kb / "skills" / "ghost", "SKILL.md") is False
    assert set(paths.resolve_skills(kb, kb)) == {"voice"}


# --------------------------------------------------------------------------------------
# PA-15 / PA-16 — ownership and the ignore set
# --------------------------------------------------------------------------------------


def test_owning_topic_root_is_the_nearest_ancestor_pa15(kb: Path) -> None:
    grilling = kb / "Cooking" / "sub-topics" / "Grilling"
    write(grilling / "notes" / "x.md")
    assert paths.owning_topic_root(kb, grilling / "notes" / "x.md") == grilling
    assert paths.owning_topic_root(kb, grilling) == grilling
    assert (
        paths.owning_topic_root(kb, kb / "Cooking" / "notes" / "old-idea" / "media" / "photo.jpg")
        == kb / "Cooking"
    )
    # Works for a path that does not exist yet — validate_content is a pre-write gate (VA-1).
    assert paths.owning_topic_root(kb, kb / "Cooking" / "notes" / "unwritten.md") == kb / "Cooking"
    assert paths.owning_topic_root(kb, kb / "skills" / "voice" / "SKILL.md") is None


def test_a_topic_md_under_skills_or_media_never_owns_anything_pa15_pa5_va6(kb: Path) -> None:
    """Ownership has to close the same doors discovery closes, or VA-6 stops holding.

    A ``topic.md`` dropped inside ``skills/<name>/`` is not a topic — no walk can reach it (PA-5).
    If ownership disagreed, the ``SKILL.md`` beside it would resolve against that pseudo-topic,
    classify as an ordinary authored file, and collect the seven required-field errors VA-6 exists
    to prevent — from a file whose schema is deepagents', not the PKB's.
    """
    skill_dir = kb / "Cooking" / "skills" / "voice"
    write(skill_dir / "SKILL.md", "---\nname: voice\ndescription: How we write\n---\n")
    write(skill_dir / "topic.md")
    framed = kb / "Cooking" / "notes" / "old-idea" / "media" / "Framed"
    write(framed / "topic.md")

    assert paths.owning_topic_root(kb, skill_dir / "SKILL.md") == kb / "Cooking"
    assert paths.owning_topic_root(kb, skill_dir / "topic.md") == kb / "Cooking"
    assert paths.owning_topic_root(kb, framed / "topic.md") == kb / "Cooking"
    assert paths.classify(kb, skill_dir / "SKILL.md") == (FileRole.SKILL, FileClass.SKILL)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (".DS_Store", True),
        (".obsidian", True),
        ("__pycache__", True),
        ("notes", False),
        ("Cooking", False),
    ],
    ids=["pa16-dsstore", "pa16-dot-dir", "pa16-pycache", "pa16-notes", "pa16-topic"],
)
def test_is_ignored_pa16(name: str, expected: bool) -> None:
    assert paths.is_ignored(name) is expected


def test_the_ignore_set_is_configurable_pa16(kb: Path) -> None:
    """PA-16's second sentence — "configurable, with that default" — is part of the rule.

    The keyword-only parameter replaces the default rather than extending it, so a host adding its
    own noise passes the union; dot-prefixed names are ignored by rule and are never in the set.
    """
    assert paths.is_ignored("Thumbs.db") is False
    assert paths.is_ignored("Thumbs.db", ignored=paths.IGNORED_NAMES | {"Thumbs.db"}) is True
    assert paths.is_ignored("__pycache__", ignored=frozenset()) is False
    assert paths.is_ignored(".DS_Store", ignored=frozenset()) is True


def test_walks_skip_ignored_entries_pa16(kb: Path) -> None:
    """Host noise must never reach a walk, or it lands in a golden file as an orphan."""
    baseline = paths.find_topic_roots(kb)
    write(kb / ".DS_Store")
    write(kb / ".obsidian" / "Ghost" / "topic.md")
    write(kb / "__pycache__" / "Ghost" / "topic.md")
    write(kb / "Cooking" / ".DS_Store")

    assert paths.find_topic_roots(kb) == baseline
    assert paths.classify(kb, kb / ".DS_Store") == (FileRole.UNKNOWN, FileClass.IGNORED)
    assert paths.classify(kb, kb / "Cooking" / ".DS_Store") == (
        FileRole.UNKNOWN,
        FileClass.IGNORED,
    )


# --------------------------------------------------------------------------------------
# PA-17 — case-exact folder-hosted items
# --------------------------------------------------------------------------------------


def test_main_file_match_is_case_exact_pa17(kb: Path) -> None:
    """``Path.exists()`` would pass this on APFS; the scandir listing does not."""
    mismatched = kb / "Cooking" / "notes" / "Steak"
    write(mismatched / "steak.md")
    assert paths.main_file_for_item(mismatched) == mismatched / "Steak.md"
    assert paths.has_case_exact_entry(mismatched, "Steak.md") is False
    assert paths.has_case_exact_entry(mismatched, "steak.md") is True

    matched = kb / "Cooking" / "notes" / "steak-sear"
    write(matched / "steak-sear.md")
    assert paths.has_case_exact_entry(matched, paths.main_file_for_item(matched).name) is True
    assert paths.has_case_exact_entry(kb / "Cooking" / "notes" / "absent", "x.md") is False


def test_has_case_exact_file_is_the_file_only_sibling_pa17(kb: Path) -> None:
    """Same case-exact name test, restricted to files — the form every *reader* needs.

    ``has_case_exact_entry`` answers "does this name exist here", which is the right question for
    the folder-hosted item convention (VA-16) and the wrong one for anything that will be opened.
    """
    item = kb / "Cooking" / "notes" / "steak-sear"
    write(item / "steak-sear.md")
    (item / "decoy.md").mkdir()

    assert paths.has_case_exact_file(item, "steak-sear.md") is True
    assert paths.has_case_exact_file(item, "Steak-Sear.md") is False
    assert paths.has_case_exact_entry(item, "decoy.md") is True
    assert paths.has_case_exact_file(item, "decoy.md") is False
    assert paths.has_case_exact_file(kb / "Cooking" / "notes" / "absent", "x.md") is False


# --------------------------------------------------------------------------------------
# PA-18 — link targets
# --------------------------------------------------------------------------------------


def test_link_target_is_relative_and_encoded_pa18(kb: Path) -> None:
    cooking = kb / "Cooking"
    assert (
        paths.link_target(cooking, cooking / "sub-topics" / "Heat Management" / "index.md")
        == "sub-topics/Heat%20Management/index.md"
    )
    assert paths.link_target(cooking, cooking / "topic.md") == "topic.md"
    assert (
        paths.link_target(cooking / "notes", cooking / "references" / "grill-basics.md")
        == "../references/grill-basics.md"
    )
    rendered = paths.link_target(cooking, cooking / "notes" / "Café Noir.md")
    assert rendered == "notes/Caf%C3%A9%20Noir.md"
    assert "\\" not in rendered
    assert not rendered.startswith(("/", "file://"))


@given(
    st.lists(
        st.text(alphabet="abcXY Z1é-", min_size=1, max_size=6),
        min_size=1,
        max_size=3,
    )
)
def test_link_target_does_not_depend_on_the_kb_location_pa18(segments: list[str]) -> None:
    """GE-4: the same tree generated from two different roots must render identical bytes."""
    left = Path("/a/KB/Cooking")
    right = Path("/b/other/Knowledge Base/Cooking")
    assert paths.link_target(left, left.joinpath(*segments, "index.md")) == paths.link_target(
        right, right.joinpath(*segments, "index.md")
    )


# --------------------------------------------------------------------------------------
# PA-19 — reserved names
# --------------------------------------------------------------------------------------


def test_reserved_names_pa19() -> None:
    assert sorted(paths.RESERVED_NAMES) == [
        "expert.md",
        "index.md",
        "summary.md",
        "tags.md",
        "topic.md",
    ]
    assert sorted(paths.RESERVED_ITEM_NAMES) == ["expert", "index", "summary", "tags", "topic"]
    for name in ("topic", "topic.md", "summary", "index.md"):
        assert paths.is_reserved_item_name(name) is True
    for name in ("steak-sear", "grill-basics", "topics", "summaries"):
        assert paths.is_reserved_item_name(name) is False


def test_reserved_name_as_item_is_detectable_pa19(kb: Path) -> None:
    """The name check is the whole rule: location alone cannot see the violation."""
    summary_item = write(kb / "Cooking" / "notes" / "summary" / "summary.md")
    assert paths.is_reserved_item_name(summary_item.parent.name) is True
    assert paths.classify(kb, summary_item) == (FileRole.NOTE, FileClass.AUTHORED)

    # ``recipes/topic/topic.md`` is doubly wrong: a reserved item name *and* a topic root reached
    # outside ``sub-topics/`` (VA-36). It stays visible to discovery either way.
    topic_item = write(kb / "Cooking" / "recipes" / "topic" / "topic.md")
    assert paths.is_reserved_item_name(topic_item.parent.name) is True
    assert paths.classify(kb, topic_item) == (FileRole.TOPIC_OVERVIEW, FileClass.AUTHORED)
    assert topic_item.parent in paths.find_topic_roots(kb)


# --------------------------------------------------------------------------------------
# classify — the location → role table (VA-13, VA-14, VA-38)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel_path", "role", "file_class"),
    [
        # No generator owns a root index.md any more (T-37, P2): it falls to the same
        # "derived by name, generated by nobody" bucket a stray notes/x/index.md already
        # occupies — scan's own root-entries check is what reports it, as UNEXPECTED_ROOT_ENTRY.
        ("index.md", FileRole.UNKNOWN, FileClass.DERIVED),
        ("tags.md", FileRole.ROOT_TAGS, FileClass.DERIVED),
        ("skills/voice/SKILL.md", FileRole.SKILL, FileClass.SKILL),
        ("skills/legacy.md", FileRole.SKILL, FileClass.SKILL),
        ("Cooking/topic.md", FileRole.TOPIC_OVERVIEW, FileClass.AUTHORED),
        ("Cooking/index.md", FileRole.TOPIC_INDEX, FileClass.DERIVED),
        ("Cooking/expert.md", FileRole.EXPERT, FileClass.SKILL),
        ("Cooking/skills/voice/SKILL.md", FileRole.SKILL, FileClass.SKILL),
        ("Cooking/notes/summary.md", FileRole.NOTES_SUMMARY, FileClass.AUTHORED),
        ("Cooking/notes/steak.md", FileRole.NOTE, FileClass.AUTHORED),
        ("Cooking/notes/old-idea/old-idea.md", FileRole.NOTE, FileClass.AUTHORED),
        ("Cooking/notes/old-idea/media/photo.jpg", FileRole.ASSET, FileClass.ASSET),
        ("Cooking/notes/old-idea/index.md", FileRole.UNKNOWN, FileClass.DERIVED),
        ("Cooking/references/summary.md", FileRole.REFERENCES_SUMMARY, FileClass.AUTHORED),
        (
            "Cooking/references/grill-basics/grill-basics.md",
            FileRole.REFERENCE,
            FileClass.AUTHORED,
        ),
        # T-14: a captured source beside the map is CAPTURED_SOURCE, not the generic ASSET role a
        # non-markdown file gets everywhere else — whatever its extension, and never opened for YAML.
        (
            "Cooking/references/grill-basics/grill-basics.pdf",
            FileRole.CAPTURED_SOURCE,
            FileClass.ASSET,
        ),
        # There is no extension-folder mechanism any more (T-1): a name STRUCTURAL_DIRS does not
        # recognize classifies UNKNOWN, whatever it would once have meant to an extension folder;
        # the directory itself is a separate UNEXPECTED_TOPIC_ENTRY finding (tests/core/
        # test_tree_rules.py), which ``classify`` alone cannot see.
        ("Cooking/recipes/summary.md", FileRole.UNKNOWN, FileClass.AUTHORED),
        ("Cooking/recipes/ribeye-on-gas.md", FileRole.UNKNOWN, FileClass.AUTHORED),
        ("Cooking/scratch.md", FileRole.UNKNOWN, FileClass.AUTHORED),
        ("Cooking/tags.md", FileRole.UNKNOWN, FileClass.AUTHORED),
        ("Cooking/sub-topics/Grilling/topic.md", FileRole.TOPIC_OVERVIEW, FileClass.AUTHORED),
        (
            "Cooking/sub-topics/Grilling/notes/summary.md",
            FileRole.NOTES_SUMMARY,
            FileClass.AUTHORED,
        ),
        ("Inbox/loose.md", FileRole.UNKNOWN, FileClass.AUTHORED),
        ("Inbox/loose.png", FileRole.ASSET, FileClass.ASSET),
    ],
    ids=[
        "root-index",
        "root-tags",
        "root-skill",
        "root-legacy-skill",
        "topic-overview",
        "topic-index",
        "expert",
        "topic-skill-overload",
        "notes-summary-va13",
        "standalone-note-va13",
        "folder-hosted-note-va13",
        "note-media-asset",
        "item-named-index-va17",
        "references-summary-va13",
        "reference-va13",
        "reference-source-file-va24",
        "extension-summary-va13",
        "extension-item-va13",
        "loose-topic-root-file-va38",
        "topic-tags-file-va27",
        "subtopic-overview",
        "subtopic-notes-summary",
        "markdown-outside-any-topic-ma8",
        "asset-outside-any-topic-ma8",
    ],
)
def test_classify_location_role_table_va13(
    kb: Path, rel_path: str, role: FileRole, file_class: FileClass
) -> None:
    write(kb / rel_path)
    assert paths.classify(kb, kb / rel_path) == (role, file_class)


def test_classify_works_before_the_file_exists_va1(kb: Path) -> None:
    """``validate_content`` gates a write, so classification must not need the file on disk."""
    unwritten = kb / "Cooking" / "notes" / "not-yet.md"
    assert not unwritten.exists()
    assert paths.classify(kb, unwritten) == (FileRole.NOTE, FileClass.AUTHORED)


def test_classify_is_total_over_directories_ge25(kb: Path) -> None:
    """A directory is a location, not a file — classification must degrade, never raise (GE-25)."""
    assert paths.classify(kb, kb) == (FileRole.UNKNOWN, FileClass.IGNORED)
    assert paths.classify(kb, kb / "Cooking") == (FileRole.UNKNOWN, FileClass.IGNORED)


def test_classify_rejects_paths_outside_the_kb_cx3(kb: Path) -> None:
    with pytest.raises(ValueError, match="not inside"):
        paths.classify(kb, kb.parent / "elsewhere.md")
