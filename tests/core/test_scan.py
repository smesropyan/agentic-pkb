"""Rules covered: PA-1, PA-3, PA-5, PA-7, PA-9, PA-10, PA-13, PA-15, PA-16, FM-14, GE-3, GE-4,
GE-5, GE-15, GE-25, GE-27, GE-29, VA-6, VA-36, VA-39, CX-5, MA-14.

The fixture knowledge base itself lives in ``conftest.py``; the last test in this file pins it,
because every later phase's golden output is rendered from it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pkb.core import frontmatter, paths
from pkb.core.errors import KbNotFoundError, Severity
from pkb.core.models import FileClass, FileRole, KbSnapshot
from pkb.core.scan import RECORD_ONLY_DIRS, scan
from tests.core.conftest import SAMPLE_KB_FILES, reversed_directory_order, write_kb

# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def codes(snapshot: KbSnapshot) -> list[str]:
    return [finding.code for finding in snapshot.findings]


def fingerprint(snapshot: KbSnapshot) -> object:
    """Everything about a snapshot that must not depend on filesystem order (GE-4).

    ``abs_path`` is excluded so the same fingerprint can compare two knowledge bases materialised
    at different roots; ordering is preserved because the mappings are flattened to lists.
    """
    return (
        [
            (record.path, record.role, record.file_class, record.topic_path, record.meta)
            for record in snapshot.files.values()
        ],
        [
            (
                topic.path,
                topic.name,
                topic.tag,
                topic.agent_id,
                topic.parent,
                topic.children,
                topic.has_expert,
                topic.meta,
            )
            for topic in snapshot.topics.values()
        ],
        [(f.code, f.severity, f.path, f.line) for f in snapshot.findings],
    )


MINIMAL_TOPIC = """\
---
title: "{title}"
description: "{title} topic"
topic: "{title}"
tags:
  - {tag}
  - type.summary
  - status.draft
created: 2024-01-01
updated: 2024-01-01
source_type: summary
---

# {title}
"""


def topic_md(title: str, tag: str) -> str:
    return MINIMAL_TOPIC.format(title=title, tag=tag)


# --------------------------------------------------------------------------------------
# Root handling
# --------------------------------------------------------------------------------------


def test_empty_directory_is_a_valid_empty_snapshot_ge29(empty_kb: Path) -> None:
    snapshot = scan(empty_kb)

    assert snapshot.topics == {}
    assert snapshot.files == {}
    assert snapshot.findings == ()
    assert snapshot.root == empty_kb


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_a_root_that_is_not_a_directory_raises_ge29(tmp_path: Path, kind: str) -> None:
    """The negative half of GE-29: empty is fine, absent is not."""
    target = tmp_path / "nope"
    if kind == "file":
        target.write_text("not a knowledge base\n", encoding="utf-8")

    with pytest.raises(KbNotFoundError):
        scan(target)


def test_unexpected_root_entry_pa1(tmp_path: Path) -> None:
    root = write_kb(
        tmp_path / "KB",
        {
            # A root index.md is not one of these any more (T-37, P2) — the registry is the one
            # derived file above the topics, and sessions/ replaces it below as a legal entry.
            "tags.md": "generated\n",
            "skills/voice/SKILL.md": "---\nname: voice\ndescription: d\n---\n",
            "sessions/2024-01-01-standup.md": "raw session text\n",
            "Cooking/topic.md": topic_md("Cooking", "topic.cooking"),
        },
    )
    assert scan(root).findings == ()

    (root / "Cooking.md").write_text("stray\n", encoding="utf-8")
    (root / "scratch").mkdir()
    snapshot = scan(root)

    unexpected = [f for f in snapshot.findings if f.code == "UNEXPECTED_ROOT_ENTRY"]
    assert [f.path for f in unexpected] == ["Cooking.md", "scratch"]
    assert {f.rule_id for f in unexpected} == {"PA-1"}
    assert {f.severity for f in unexpected} == {Severity.WARNING}


# --------------------------------------------------------------------------------------
# Topic discovery
# --------------------------------------------------------------------------------------


def test_topic_discovery_is_recursive_and_preorder_pa5(sample_kb: Path) -> None:
    snapshot = scan(sample_kb)

    assert list(snapshot.topics) == ["BBQ", "Cooking", "Cooking/sub-topics/Grilling"]
    # The one discovery order: whatever paths.py's canonical walk says.
    assert list(snapshot.topics) == [
        paths.rel(sample_kb, topic) for topic in paths.find_topic_roots(sample_kb)
    ]


def test_files_inside_structural_directories_are_still_recorded_pa5(sample_kb: Path) -> None:
    """Discovery never routes through notes/ or references/, but the walk records what is there."""
    snapshot = scan(sample_kb)

    assert "Cooking/notes/preheat-the-grill.md" in snapshot.files
    assert "Cooking/references/grill-basics/grill-basics.md" in snapshot.files
    assert "Cooking/notes/old-idea/media/photo.jpg" in snapshot.files
    assert "skills/voice/SKILL.md" in snapshot.files


def _smuggled_topics_kb(root: Path) -> Path:
    """A tree with a ``topic.md`` in every structural directory, legal and illegal alike."""
    return write_kb(
        root,
        {
            "skills/voice/SKILL.md": "---\nname: voice\ndescription: d\n---\n",
            "skills/voice/topic.md": topic_md("RootSkill", "topic.rootskill"),
            "Cooking/topic.md": topic_md("Cooking", "topic.cooking"),
            "Cooking/sub-topics/Grilling/topic.md": topic_md("Grilling", "topic.cooking.grilling"),
            "Cooking/notes/Smoking/topic.md": topic_md("Smoking", "topic.cooking.smoking"),
            "Cooking/references/Borrowed/topic.md": topic_md("Borrowed", "topic.cooking.borrowed"),
            "Cooking/recipes/Braising/topic.md": topic_md("Braising", "topic.cooking.braising"),
            "Cooking/media/Framed/topic.md": topic_md("Framed", "topic.cooking.framed"),
            "Cooking/skills/voice/SKILL.md": "---\nname: voice\ndescription: d\n---\n",
            "Cooking/skills/voice/topic.md": topic_md("Voice", "topic.cooking.voice"),
            "Cooking/skills/voice/sub-topics/Deep/topic.md": topic_md("Deep", "topic.cooking.deep"),
            "Cooking/notes/trip/media/topic.md": topic_md("Trip", "topic.cooking.trip"),
        },
    )


def test_record_only_dirs_is_structural_minus_the_three_topic_routes_pa5_va36() -> None:
    """The seam is derived from ``STRUCTURAL_DIRS`` (PA-6's shared constant), so pin the result."""
    record_only = set(RECORD_ONLY_DIRS)
    routes = set(paths.STRUCTURAL_DIRS) - record_only

    assert record_only == {paths.MEDIA_DIR, paths.SKILLS_DIR}
    assert record_only < set(paths.STRUCTURAL_DIRS)
    assert routes == {
        paths.SUBTOPICS_DIR,  # PA-4, the documented route
        paths.NOTES_DIR,  # VA-36, discovered and warned about
        paths.REFERENCES_DIR,  # VA-36, discovered and warned about
    }


@pytest.mark.superseded
def test_media_and_skills_yield_files_but_never_a_topic_root_pa5_ge15(tmp_path: Path) -> None:
    """The walk enters ``media/`` and ``skills/`` to *record*, never to *discover* (PA-5, GE-15).

    Promoting a smuggled ``topic.md`` there is not a cosmetic slip: the topic gets a ``topic.*`` tag
    and an agent id (VA-6 forbids skills participating in tag generation at all), the root catalog
    routes to it, and ``regenerate_all`` writes a derived ``index.md`` *into the human's media or
    skills folder* — which GE-15 lists among the topic-index exclusions. VA-36 re-opens discovery
    for exactly three routes, ``notes/``, ``references/`` and an extension folder, and names neither
    of these two.
    """
    kb = _smuggled_topics_kb(tmp_path / "KB")
    snapshot = scan(kb)

    assert list(snapshot.topics) == [
        "Cooking",
        "Cooking/notes/Smoking",
        "Cooking/recipes/Braising",
        "Cooking/references/Borrowed",
        "Cooking/sub-topics/Grilling",
    ]
    # No address may route through a directory the walk does not discover through.
    for topic in snapshot.topics.values():
        assert paths.STRUCTURAL_DIRS.isdisjoint(topic.tag.split(".")[1:])
        assert paths.STRUCTURAL_DIRS.isdisjoint(topic.agent_id.split("/")[1:])

    # The other concern, kept: every file under those directories is still recorded, because
    # orphan and link analysis need them (GE-15) and a dropped file is invisible to every agent.
    for smuggled in (
        "skills/voice/SKILL.md",
        "skills/voice/topic.md",
        "Cooking/media/Framed/topic.md",
        "Cooking/skills/voice/SKILL.md",
        "Cooking/skills/voice/topic.md",
        "Cooking/skills/voice/sub-topics/Deep/topic.md",
        "Cooking/notes/trip/media/topic.md",
    ):
        assert smuggled in snapshot.files, smuggled


def test_non_discovery_is_sticky_below_media_and_skills_pa5(tmp_path: Path) -> None:
    """``sub-topics/`` inside ``skills/`` is still inside ``skills/``.

    The flag has to be threaded, not recomputed per directory name: PA-5 stops the *descent*, so
    every descendant of a stopped directory is equally undiscoverable, however it is spelled.
    """
    kb = _smuggled_topics_kb(tmp_path / "KB")
    snapshot = scan(kb)

    assert "Cooking/skills/voice/sub-topics/Deep" not in snapshot.topics
    assert "Cooking/notes/trip/media" not in snapshot.topics
    # ...while the same nesting outside them is discovered, so the stop is the only thing at work.
    assert "Cooking/sub-topics/Grilling" in snapshot.topics


def test_a_directory_symlink_is_never_a_topic_root_pa5_ge5(sample_kb: Path) -> None:
    """``DirEntry.is_dir()`` follows links, and a followed link aliases a whole topic (GE-5).

    ``AAA -> Cooking`` is listed as a directory, satisfies ``is_topic_root`` *through* the link and
    is recorded as a second topic root holding the same ``topic.md``. ``_first_visit``'s
    ``(st_dev, st_ino)`` guard then refuses whichever alias the sorted walk reaches second, so one
    topic's files land under the other's address and disappear from their own (GE-25) — and because
    both aliases are published, every regeneration rewrites both indexes forever (GE-5).
    """
    clean = scan(sample_kb)
    clean_topics, clean_files = list(clean.topics), set(clean.files)
    try:
        (sample_kb / "AAA").symlink_to(sample_kb / "Cooking")
        (sample_kb / "Cooking" / "self").symlink_to(sample_kb / "Cooking")
    except (OSError, NotImplementedError):  # pragma: no cover - host without symlink privileges
        pytest.skip("this host cannot create symlinks")

    snapshot = scan(sample_kb)

    assert list(snapshot.topics) == clean_topics
    assert clean_files <= set(snapshot.files)
    # A link is not a directory the walk owns — but it is not dropped either (GE-25): it is
    # recorded as an entry, and the one at the root is reported for a human to remove (PA-1).
    assert {"AAA", "Cooking/self"} <= set(snapshot.files)
    assert [f.path for f in snapshot.findings if f.code == "UNEXPECTED_ROOT_ENTRY"] == ["AAA"]


def test_topic_record_tag_and_agent_id_pa9_pa10(sample_kb: Path) -> None:
    grilling = scan(sample_kb).topic("Cooking/sub-topics/Grilling")

    assert grilling.tag == "topic.cooking.grilling"
    assert grilling.agent_id == "topic/cooking/grilling"
    assert grilling.name == "Grilling"
    assert grilling.depth == 2


def test_topic_record_parent_and_children_pa5(sample_kb: Path) -> None:
    snapshot = scan(sample_kb)

    assert snapshot.topic("Cooking").children == ("Cooking/sub-topics/Grilling",)
    assert snapshot.topic("Cooking").parent is None
    assert snapshot.topic("Cooking/sub-topics/Grilling").parent == "Cooking"
    assert snapshot.topic("BBQ").children == ()
    assert [t.path for t in snapshot.top_level_topics()] == ["BBQ", "Cooking"]


@pytest.mark.superseded
def test_topic_record_extension_folders_pa7(sample_kb: Path) -> None:
    snapshot = scan(sample_kb)

    assert snapshot.topic("Cooking").extension_folders == ("recipes",)
    assert snapshot.topic("BBQ").extension_folders == ()


def test_topic_record_has_expert_pa13(sample_kb: Path) -> None:
    snapshot = scan(sample_kb)

    assert snapshot.topic("Cooking").has_expert is True
    assert snapshot.topic("BBQ").has_expert is False
    assert snapshot.topic("Cooking/sub-topics/Grilling").has_expert is False


def _has_expert_agrees_with_the_resolver(kb: Path, snapshot: KbSnapshot) -> None:
    """PA-13's own contract: ``has_expert`` is true exactly when the resolver stops at the topic."""
    for path in snapshot.topics:
        topic = kb / path
        expected = paths.resolve_expert(kb, topic) == topic / paths.EXPERT_FILE
        assert snapshot.topic(path).has_expert is expected, path


@pytest.mark.parametrize("kind", ["directory", "symlink-to-directory"])
def test_only_a_real_file_makes_a_custom_expert_pa13(tmp_path: Path, kind: str) -> None:
    """``has_expert`` answers the question :func:`paths.resolve_expert` answers, or it lies (PA-13).

    A *directory* named ``expert.md`` is a legal tree — PA-7 makes any unknown directory an
    extension folder and VA-38 flags only loose files — so the name alone proves nothing. The
    membership test this used to run (``"Cooking/expert.md" in files``) is file-only for a plain
    directory but not for a symlinked one: the walk lists a link as a non-directory and records it,
    so the topic claimed a custom expert while ``resolve_expert`` handed Layer 2 ``None``. GE-13
    then marks the catalog line for a prompt no agent can read.
    """
    kb = write_kb(tmp_path / "KB", {"Cooking/topic.md": topic_md("Cooking", "topic.cooking")})
    target = kb / "Cooking" / "expert.md"
    if kind == "directory":
        target.mkdir()
        (target / "notes.md").write_text("not a prompt\n", encoding="utf-8")
    else:
        (kb / "Cooking" / "notes").mkdir()
        try:
            target.symlink_to(kb / "Cooking" / "notes")
        except (OSError, NotImplementedError):  # pragma: no cover - no symlink privileges
            pytest.skip("this host cannot create symlinks")

    snapshot = scan(kb)

    assert snapshot.topic("Cooking").has_expert is False
    assert paths.resolve_expert(kb, kb / "Cooking") is None
    _has_expert_agrees_with_the_resolver(kb, snapshot)


def test_has_expert_agrees_with_resolve_expert_on_the_fixture_pa13(sample_kb: Path) -> None:
    _has_expert_agrees_with_the_resolver(sample_kb, scan(sample_kb))


def test_topic_meta_falls_back_when_topic_md_is_degraded_ge25(tmp_path: Path) -> None:
    root = write_kb(
        tmp_path / "KB",
        {
            "Physics/topic.md": "---\ntitle: [unclosed\n---\n\n# Physics\n",
            "Cooking/topic.md": topic_md("Cooking", "topic.cooking"),
        },
    )
    snapshot = scan(root)

    assert snapshot.topic("Physics").meta is None
    assert snapshot.topic("Physics").title == "Physics"  # folder name is the fallback
    assert snapshot.topic("Cooking").title == "Cooking"


def test_misplaced_topic_root_va36(tmp_path: Path) -> None:
    root = write_kb(
        tmp_path / "KB",
        {
            "Cooking/topic.md": topic_md("Cooking", "topic.cooking"),
            "Cooking/sub-topics/Grilling/topic.md": topic_md("Grilling", "topic.cooking.grilling"),
            "Cooking/notes/Smoking/topic.md": topic_md("Smoking", "topic.cooking.smoking"),
        },
    )
    snapshot = scan(root)

    misplaced = [f for f in snapshot.findings if f.code == "MISPLACED_TOPIC_ROOT"]
    # Anchored at the file that has to move, not at the directory, and worded by
    # pkb.core.diagnostics so the flush and validate_tree describe this defect identically (CX-6).
    assert [f.path for f in misplaced] == ["Cooking/notes/Smoking/topic.md"]
    assert [f.value for f in misplaced] == ["Cooking/notes/Smoking"]
    assert misplaced[0].rule_id == "VA-36"
    assert misplaced[0].severity is Severity.WARNING
    # Still discovered, so it is never invisible.
    assert "Cooking/notes/Smoking" in snapshot.topics
    assert "Cooking/sub-topics/Grilling" in snapshot.topics


def test_unaddressable_topic_root_is_reported_not_dropped_pa8(tmp_path: Path) -> None:
    root = write_kb(tmp_path / "KB", {"!!!/topic.md": topic_md("Punctuation", "topic.x")})
    snapshot = scan(root)

    assert "UNADDRESSABLE_TOPIC_ROOT" in codes(snapshot)
    assert snapshot.topic("!!!").tag == "topic.untitled"
    assert snapshot.topic("!!!").agent_id == "topic/untitled"


# --------------------------------------------------------------------------------------
# Files: classification, parsing, ordering
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("relative", "role", "file_class"),
    [
        ("Cooking/topic.md", FileRole.TOPIC_OVERVIEW, FileClass.AUTHORED),
        ("Cooking/expert.md", FileRole.EXPERT, FileClass.SKILL),
        ("Cooking/notes/summary.md", FileRole.NOTES_SUMMARY, FileClass.AUTHORED),
        ("Cooking/notes/preheat-the-grill.md", FileRole.NOTE, FileClass.AUTHORED),
        ("Cooking/notes/old-idea/media/photo.jpg", FileRole.ASSET, FileClass.ASSET),
        ("Cooking/references/summary.md", FileRole.REFERENCES_SUMMARY, FileClass.AUTHORED),
        (
            "Cooking/references/grill-basics/grill-basics.md",
            FileRole.REFERENCE,
            FileClass.AUTHORED,
        ),
        # There is no extension-folder mechanism any more (T-1): a file inside an unrecognized
        # topic-root directory is UNKNOWN, not a role of its own; the directory itself is a
        # separate UNEXPECTED_TOPIC_ENTRY finding, covered in tests/core/test_tree_rules.py.
        ("Cooking/recipes/ribeye-on-gas.md", FileRole.UNKNOWN, FileClass.AUTHORED),
        ("skills/voice/SKILL.md", FileRole.SKILL, FileClass.SKILL),
    ],
)
def test_every_file_is_classified_by_location_pa19(
    sample_kb: Path, relative: str, role: FileRole, file_class: FileClass
) -> None:
    record = scan(sample_kb).files[relative]

    assert (record.role, record.file_class) == (role, file_class)


def test_file_topic_path_matches_owning_topic_root_pa15(sample_kb: Path) -> None:
    snapshot = scan(sample_kb)

    for record in snapshot.files.values():
        owner = paths.owning_topic_root(sample_kb, record.abs_path)
        expected = None if owner is None else paths.rel(sample_kb, owner)
        assert record.topic_path == expected, record.path
        # Every owner named by a file is a topic the snapshot knows about.
        assert expected is None or expected in snapshot.topics


def test_assets_are_recorded_but_never_parsed_fm14(
    sample_kb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    original = frontmatter.parse

    def spy(text: str) -> object:
        seen.append(text)
        return original(text)

    monkeypatch.setattr("pkb.core.scan.frontmatter.parse", spy)
    snapshot = scan(sample_kb)

    photo = snapshot.files["Cooking/notes/old-idea/media/photo.jpg"]
    assert photo.doc is None
    assert photo.meta is None
    assert photo.is_markdown is False
    assert snapshot.files["Cooking/references/grill-basics/grill-basics.pdf"].doc is None
    # Exactly the markdown files were parsed, exactly once each.
    markdown = [p for p in SAMPLE_KB_FILES if p.endswith(paths.MARKDOWN_SUFFIX)]
    assert len(seen) == len(markdown)
    assert all(SAMPLE_KB_FILES[p] in seen for p in markdown)


def test_frontmatter_parse_error_is_a_finding_not_an_exception_va39(tmp_path: Path) -> None:
    root = write_kb(
        tmp_path / "KB",
        {
            "Cooking/topic.md": topic_md("Cooking", "topic.cooking"),
            "Cooking/notes/broken.md": "---\ntitle: [unclosed\n---\n\n# Broken\n",
            "Cooking/notes/fine.md": topic_md("Fine", "topic.cooking"),
        },
    )
    snapshot = scan(root)

    parse_errors = [f for f in snapshot.findings if f.code == "FRONTMATTER_PARSE_ERROR"]
    assert [f.path for f in parse_errors] == ["Cooking/notes/broken.md"]
    assert parse_errors[0].rule_id == "VA-39"
    assert parse_errors[0].severity is Severity.ERROR
    assert parse_errors[0].line is not None
    # The walk continued: the broken file is still recorded, and its neighbours parsed.
    assert snapshot.files["Cooking/notes/broken.md"].meta is None
    assert snapshot.files["Cooking/notes/fine.md"].meta is not None


def test_undecodable_markdown_is_a_finding_not_an_exception_ma14(tmp_path: Path) -> None:
    root = write_kb(tmp_path / "KB", {"Cooking/topic.md": topic_md("Cooking", "topic.cooking")})
    (root / "Cooking" / "notes").mkdir(parents=True)
    (root / "Cooking" / "notes" / "bytes.md").write_bytes(b"---\ntitle: \xff\xfe\n---\n")

    snapshot = scan(root)

    assert "UNREADABLE_FILE" in codes(snapshot)
    assert snapshot.files["Cooking/notes/bytes.md"].is_markdown is True
    assert snapshot.files["Cooking/notes/bytes.md"].meta is None


def test_files_are_ordered_by_relative_path_ge27(sample_kb: Path) -> None:
    order = list(scan(sample_kb).files)

    assert order == sorted(order, key=paths.sort_key)
    assert order[:3] == ["BBQ/notes/summary.md", "BBQ/references/summary.md", "BBQ/topic.md"]


def test_derived_files_are_excluded_from_content_files_ge3(sample_kb: Path) -> None:
    write_kb(
        sample_kb,
        {
            "index.md": '---\ntitle: "PKB Topic Catalog"\nsource_type: catalog\n---\n',
            "tags.md": '---\ntitle: "PKB Tag Registry"\nsource_type: tag-registry\n---\n',
            "Cooking/index.md": '---\ntitle: "Cooking"\nsource_type: index\n---\n',
        },
    )
    snapshot = scan(sample_kb)
    content = {record.path for record in snapshot.content_files()}

    assert snapshot.files["index.md"].file_class is FileClass.DERIVED
    assert snapshot.files["Cooking/index.md"].role is FileRole.TOPIC_INDEX
    assert content.isdisjoint({"index.md", "tags.md", "Cooking/index.md"})
    # ...and skills and assets are not content either.
    assert content.isdisjoint({"skills/voice/SKILL.md", "Cooking/expert.md"})
    assert "Cooking/notes/preheat-the-grill.md" in content


# --------------------------------------------------------------------------------------
# Determinism and totality
# --------------------------------------------------------------------------------------


def test_ignored_entries_do_not_change_the_snapshot_pa16(sample_kb: Path, tmp_path: Path) -> None:
    clean = fingerprint(scan(sample_kb))

    noisy = write_kb(tmp_path / "Noisy", SAMPLE_KB_FILES)
    write_kb(
        noisy,
        {
            ".DS_Store": "junk\n",
            ".obsidian/workspace.json": "{}\n",
            "Cooking/.DS_Store": "junk\n",
            "Cooking/notes/__pycache__/x.pyc": "junk\n",
            "Cooking/.trash/topic.md": topic_md("Trash", "topic.trash"),
        },
    )

    assert fingerprint(scan(noisy)) == clean


def test_snapshot_does_not_depend_on_scandir_order_ge4(sample_kb: Path) -> None:
    forwards = fingerprint(scan(sample_kb))
    with reversed_directory_order():
        backwards = fingerprint(scan(sample_kb))

    assert backwards == forwards


_SEGMENT = st.sampled_from(["Alpha", "alpha", "Beta", "notes", "references", "sub-topics", "zz"])
_RELATIVE_DIR = st.lists(_SEGMENT, min_size=1, max_size=3).map("/".join)


@given(
    topic_dirs=st.lists(_RELATIVE_DIR, max_size=4, unique=True),
    note_dirs=st.lists(_RELATIVE_DIR, max_size=4, unique=True),
)
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_snapshot_is_invariant_to_filesystem_order_ge4(
    tmp_path_factory: pytest.TempPathFactory, topic_dirs: list[str], note_dirs: list[str]
) -> None:
    """The property GE-4 actually states: output depends on contents, never on iteration order."""
    files = {f"{d}/{paths.TOPIC_FILE}": topic_md(d.split("/")[-1], "topic.x") for d in topic_dirs}
    files.update({f"{d}/note.md": topic_md("Note", "topic.x") for d in note_dirs})
    root = write_kb(tmp_path_factory.mktemp("kb") / "KB", files)

    forwards = fingerprint(scan(root))
    with reversed_directory_order():
        backwards = fingerprint(scan(root))

    assert backwards == forwards


def test_three_defects_yield_three_distinct_findings_cx5(tmp_path: Path) -> None:
    root = write_kb(
        tmp_path / "KB",
        {
            "Cooking.md": "a stray root file\n",
            "Cooking/topic.md": topic_md("Cooking", "topic.cooking"),
            "Cooking/notes/Smoking/topic.md": topic_md("Smoking", "topic.cooking.smoking"),
            "Cooking/notes/broken.md": "---\ntitle: [unclosed\n---\n",
        },
    )
    snapshot = scan(root)

    assert sorted(codes(snapshot)) == [
        "FRONTMATTER_PARSE_ERROR",
        "MISPLACED_TOPIC_ROOT",
        "UNEXPECTED_ROOT_ENTRY",
    ]


def test_scan_is_total_over_a_degraded_tree_ma14(tmp_path: Path) -> None:
    root = write_kb(
        tmp_path / "KB",
        {
            "Cooking/topic.md": "---\ntitle: [unclosed\n---\n",
            "Cooking/notes/half-written.md": '---\ntitle: "Half"\n',
            "Cooking/notes/no-frontmatter.md": "# Just a heading\n",
            "Cooking/notes/steak/media/pan.jpg": "binary\n",
            "Cooking/notes/index.md": '---\ntitle: "Wrong"\n---\n',
            "Loose/README.md": "# Not a topic\n",
        },
    )
    snapshot = scan(root)

    assert len(snapshot.files) == 6
    assert snapshot.topic("Cooking").meta is None
    # An index.md nobody generates is derived by name (PA-11) and generated by nobody (PA-12).
    assert snapshot.files["Cooking/notes/index.md"].file_class is FileClass.DERIVED
    assert paths.is_generated(root, snapshot.files["Cooking/notes/index.md"].abs_path) is False
    # A file with no frontmatter parses clean: absent is not broken.
    plain = snapshot.files["Cooking/notes/no-frontmatter.md"]
    assert plain.doc is not None and plain.doc.error is None and plain.doc.meta is None
    assert {f.code for f in snapshot.findings} == {
        "FRONTMATTER_PARSE_ERROR",
        "UNEXPECTED_ROOT_ENTRY",
    }


def test_scan_is_repeatable(sample_kb: Path) -> None:
    assert fingerprint(scan(sample_kb)) == fingerprint(scan(sample_kb))


# --------------------------------------------------------------------------------------
# The shared fixture itself
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_sample_kb_is_the_spec_fixture_ge31(sample_kb: Path) -> None:
    """§4.1's tree, from which every later golden file is rendered."""
    snapshot = scan(sample_kb)

    assert snapshot.findings == ()
    assert [(t.path, t.tag, t.agent_id) for t in snapshot.topics.values()] == [
        ("BBQ", "topic.bbq", "topic/bbq"),
        ("Cooking", "topic.cooking", "topic/cooking"),
        ("Cooking/sub-topics/Grilling", "topic.cooking.grilling", "topic/cooking/grilling"),
    ]
    assert set(snapshot.files) == set(SAMPLE_KB_FILES)

    cooking = snapshot.topic("Cooking")
    assert cooking.meta is not None
    assert cooking.meta.description == "Home cooking: technique, equipment, and recipes"

    # The tags §4.4's registry renders come from these files and nowhere else.
    used = {
        tag
        for record in snapshot.content_files()
        for tag in (record.meta.tags if record.meta else ())
    }
    assert used == {
        "domain.legal.compliance",
        "status.approved",
        "status.conflict-review",
        "status.draft",
        "topic.bbq",
        "topic.bbq.equipment",
        "topic.cooking",
        "topic.cooking.grilling",
        "topic.cooking.heat-management",
        "topic.cooking.recipes",
        "type.note",
        "type.reference",
        "type.summary",
    }

    # The open conflict §4.3's "Needs review" section renders.
    preheat = snapshot.files["Cooking/notes/preheat-the-grill.md"].meta
    assert preheat is not None
    assert "status.conflict-review" in preheat.tags
    assert preheat.review_note == (
        "Reference 'Grill Basics' says preheat for 10 min. Note says 15 min."
    )

    # The two related_topics declarations §4.4's mappings aggregate.
    declared = {
        record.path: record.meta.related_topics
        for record in snapshot.content_files()
        if record.meta and record.meta.related_topics
    }
    assert declared == {
        "Cooking/notes/grill-performance-in-windy-conditions.md": ("bbq.equipment",),
        "Cooking/notes/preheat-the-grill.md": ("bbq.equipment",),
    }
