"""Conformance tests for the T-rules (docs/superpowers/specs/2026-08-13-tree-T-rules.md).

Every test name ends in the rule id it covers, matching the convention in
``tests/core/test_frontmatter.py``. Tasks 3-9 of
``docs/superpowers/plans/2026-08-13-phase1-the-tree.md`` each add their tests here.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pkb.core import frontmatter, tags
from pkb.core.errors import Severity
from pkb.core.models import FileClass, FileRole
from pkb.core.scan import scan
from pkb.core.validation import validate_tree
from tests.core.conftest import write_kb


def test_related_topics_is_the_only_optional_field_t12() -> None:
    """T-12: ``related_topics`` is the only recognized optional field."""
    assert frozenset({"related_topics"}) == frontmatter.OPTIONAL_FIELDS


def test_retired_fields_are_unknown_not_known_t12() -> None:
    """T-12: the seven required fields plus ``related_topics`` are the whole known schema."""
    for retired in ("review_note", "last_reviewed", "provenance", "status"):
        assert retired not in frontmatter.KNOWN_FIELDS


def test_a_retired_field_round_trips_as_unknown_t12() -> None:
    """T-12: a retired key survives parsing as an unknown field rather than a known one (FM-10)."""
    text = (
        '---\ntitle: "T"\ndescription: "D"\ntopic: "Cooking"\n'
        "tags:\n  - topic.cooking\n  - type.note\ncreated: 2024-01-01\nupdated: 2024-01-02\n"
        'source_type: note\nreview_note: "old"\n---\nbody\n'
    )
    doc = frontmatter.parse(text)
    assert doc.meta is not None
    assert "review_note" in doc.meta.unknown_fields


# --------------------------------------------------------------------------------------
# Task 4 — T-17: three namespaces, nothing invents a fourth; no status.* namespace
# --------------------------------------------------------------------------------------


def test_three_namespaces_and_type_is_the_closed_set_t17() -> None:
    """T-17: `topic.*`/`domain.*` stay open, `type.*` is the one closed set, `status.*` is gone."""
    assert {n.value for n in tags.Namespace} == {"topic", "type", "domain"}
    assert not hasattr(tags, "STATUS_DEFINITIONS")


def test_a_status_tag_is_an_unknown_namespace_finding_t17(tmp_path: Path) -> None:
    """T-17: a `status.*` tag is `UNKNOWN_TAG_NAMESPACE`, like any other unrecognized namespace.

    Built by hand rather than through the scaffolder, the way `test_scan.py`'s fixtures are: one
    topic root plus one note, the note carrying `status.approved` alongside otherwise-legal tags.
    """
    kb = write_kb(
        tmp_path / "KB",
        {
            "Cooking/topic.md": (
                "---\n"
                'title: "Cooking"\n'
                'description: "Home cooking"\n'
                'topic: "Cooking"\n'
                "tags:\n"
                "  - topic.cooking\n"
                "  - type.summary\n"
                "created: 2024-01-01\n"
                "updated: 2024-01-01\n"
                "source_type: summary\n"
                "---\n\n# Cooking\n"
            ),
            "Cooking/notes/idea.md": (
                "---\n"
                'title: "Idea"\n'
                'description: "An idea worth keeping"\n'
                'topic: "Cooking"\n'
                "tags:\n"
                "  - topic.cooking\n"
                "  - type.note\n"
                "  - status.approved\n"
                "created: 2024-01-01\n"
                "updated: 2024-01-01\n"
                "source_type: note\n"
                "---\n\n# Idea\n"
            ),
        },
    )

    snapshot = scan(kb)
    findings = validate_tree(kb, snapshot)

    matches = [
        finding
        for finding in findings
        if finding.path == "Cooking/notes/idea.md" and finding.code == "UNKNOWN_TAG_NAMESPACE"
    ]
    assert len(matches) == 1


# --------------------------------------------------------------------------------------
# Task 5 — the new tree shape: FileRole loses extension/root-index members, gains
# SESSION and CAPTURED_SOURCE; root layout; skills/AUTHORSHIP.md; the tree's shape at a
# topic root
# --------------------------------------------------------------------------------------


def _knowledge_file(
    *,
    title: str,
    description: str,
    topic: str,
    tags_: Sequence[str],
    source_type: str,
    body: str = "Body.\n",
    created: str = "2024-01-01",
    updated: str = "2024-01-01",
) -> str:
    """A minimal, valid class-1 knowledge file — the seven required fields, nothing else."""
    tag_lines = "\n".join(f"  - {tag}" for tag in tags_)
    return (
        "---\n"
        f'title: "{title}"\n'
        f'description: "{description}"\n'
        f'topic: "{topic}"\n'
        f"tags:\n{tag_lines}\n"
        f"created: {created}\n"
        f"updated: {updated}\n"
        f"source_type: {source_type}\n"
        "---\n\n"
        f"{body}"
    )


def test_file_role_drops_extension_and_root_index_members_t1() -> None:
    """T-1: "There is no extension-folder mechanism" — FileRole carries none of its former markers."""
    assert not hasattr(FileRole, "EXTENSION_SUMMARY")
    assert not hasattr(FileRole, "EXTENSION_ITEM")
    assert not hasattr(FileRole, "ROOT_INDEX")


def test_unknown_topic_root_directory_is_a_finding_t1(tmp_path: Path) -> None:
    """T-1: an unrecognized directory at a topic root is a warning, not silence.

    The rules name no finding code for this case — T-1's own test-assertion cell cites T-34, the
    per-write location-agreement rules, which do not cover directories — so ``UNEXPECTED_TOPIC_ENTRY``
    is minted for it (see ``validation._t1_unexpected_topic_entry``).
    """
    kb = write_kb(
        tmp_path / "KB",
        {
            "Cooking/topic.md": _knowledge_file(
                title="Cooking",
                description="Home cooking",
                topic="Cooking",
                tags_=["topic.cooking", "type.summary"],
                source_type="summary",
            ),
            "Cooking/recipes/steak.md": _knowledge_file(
                title="Steak",
                description="A steak recipe",
                topic="Cooking",
                tags_=["topic.cooking.recipes", "type.note"],
                source_type="note",
            ),
        },
    )

    snapshot = scan(kb)
    findings = validate_tree(kb, snapshot)

    matches = [
        finding
        for finding in findings
        if finding.code == "UNEXPECTED_TOPIC_ENTRY" and finding.path == "Cooking/recipes"
    ]
    assert len(matches) == 1
    assert matches[0].severity is Severity.WARNING


def test_root_layout_flags_a_stray_index_but_not_tags_skills_sessions_t37(tmp_path: Path) -> None:
    """T-37 (P2): root ``index.md`` is ``UNEXPECTED_ROOT_ENTRY``; ``skills/``/``sessions/``/topics
    are not — the root layout is ``tags.md``, ``skills/``, ``sessions/`` and topic directories."""
    kb = write_kb(
        tmp_path / "KB",
        {
            "Cooking/topic.md": _knowledge_file(
                title="Cooking",
                description="Home cooking",
                topic="Cooking",
                tags_=["topic.cooking", "type.summary"],
                source_type="summary",
            ),
            "skills/voice/SKILL.md": (
                "---\nname: voice\ndescription: How the assistant writes\n---\n\nVoice.\n"
            ),
            "sessions/2024-01-01-standup.md": _knowledge_file(
                title="Standup",
                description="A running record of the day's session",
                topic="(session)",
                tags_=["topic.cooking", "type.summary"],
                source_type="summary",
            ),
            "index.md": "# hand-placed\n\nSomeone dropped this here by hand.\n",
        },
    )

    snapshot = scan(kb)
    findings = validate_tree(kb, snapshot)

    unexpected_root_entries = [f for f in findings if f.code == "UNEXPECTED_ROOT_ENTRY"]
    assert [f.path for f in unexpected_root_entries] == ["index.md"]
    assert snapshot.files["index.md"].role is FileRole.UNKNOWN
    assert snapshot.files["index.md"].file_class is FileClass.DERIVED


def test_authorship_md_beside_skill_is_never_parsed_and_trips_no_legacy_warning_t11(
    tmp_path: Path,
) -> None:
    """T-11: ``AUTHORSHIP.md`` beside a ``SKILL.md`` is class 3, never parsed, no
    ``LEGACY_SKILL_LAYOUT`` warning, and excluded from the knowledge-file walk."""
    kb = write_kb(
        tmp_path / "KB",
        {
            "skills/voice/SKILL.md": (
                "---\nname: voice\ndescription: How the assistant writes\n---\n\nVoice.\n"
            ),
            "skills/voice/AUTHORSHIP.md": "Drafted by the voice skill's own author, 2024-01-01.\n",
        },
    )

    snapshot = scan(kb)
    record = snapshot.files["skills/voice/AUTHORSHIP.md"]
    assert record.doc is None
    assert record.file_class is FileClass.ASSET
    assert record not in list(snapshot.content_files())

    findings = validate_tree(kb, snapshot)
    assert not [f for f in findings if f.code == "LEGACY_SKILL_LAYOUT"]


def test_flat_skill_file_still_warns_legacy_layout_pa14(tmp_path: Path) -> None:
    """PA-14, contrast with T-11: a flat ``skills/<name>.md`` (no folder of its own) still warns,
    unaffected by the new ``AUTHORSHIP.md`` carve-out."""
    kb = write_kb(tmp_path / "KB", {"skills/voice.md": "How the assistant writes.\n"})

    snapshot = scan(kb)
    findings = validate_tree(kb, snapshot)

    matches = [
        f for f in findings if f.code == "LEGACY_SKILL_LAYOUT" and f.path == "skills/voice.md"
    ]
    assert len(matches) == 1


def test_captured_source_files_classify_distinctly_from_the_map_t14(tmp_path: Path) -> None:
    """T-14: every file under ``references/<src>/`` except ``<src>.md`` is ``CAPTURED_SOURCE``,
    ``doc is None``, whatever its extension; only ``<src>.md`` is ``REFERENCE`` and is parsed."""
    kb = write_kb(
        tmp_path / "KB",
        {
            "Reading/topic.md": _knowledge_file(
                title="Reading",
                description="Books and articles",
                topic="Reading",
                tags_=["topic.reading", "type.summary"],
                source_type="summary",
            ),
            "Reading/references/summary.md": _knowledge_file(
                title="References summary",
                description="Overview of ingested sources",
                topic="Reading",
                tags_=["topic.reading", "type.summary"],
                source_type="summary",
            ),
            "Reading/references/book/book.md": _knowledge_file(
                title="Some Book",
                description="A captured book",
                topic="Reading",
                tags_=["topic.reading", "type.reference"],
                source_type="reference",
            ),
            "Reading/references/book/extract.md": "raw captured text, not a knowledge file\n",
        },
    )

    snapshot = scan(kb)
    main = snapshot.files["Reading/references/book/book.md"]
    extract = snapshot.files["Reading/references/book/extract.md"]

    assert main.role is FileRole.REFERENCE
    assert main.doc is not None

    assert extract.role is FileRole.CAPTURED_SOURCE
    assert extract.doc is None


def test_session_file_classifies_as_session_and_skips_location_agreement_t9(
    tmp_path: Path,
) -> None:
    """T-9: a root ``sessions/*.md`` file is ``SESSION``, parsed as a knowledge file, and the
    topic-location agreement checks (T-34) have nothing to compare against — ``topic: "(session)"``
    is accepted where every other knowledge file would fail ``TOPIC_LOCATION_MISMATCH``."""
    kb = write_kb(
        tmp_path / "KB",
        {
            "sessions/2024-01-01-standup.md": _knowledge_file(
                title="Standup",
                description="A running record of the day's session",
                topic="(session)",
                tags_=["topic.cooking", "type.summary"],
                source_type="summary",
            ),
        },
    )

    snapshot = scan(kb)
    record = snapshot.files["sessions/2024-01-01-standup.md"]
    assert record.role is FileRole.SESSION
    assert record.file_class is FileClass.AUTHORED
    assert record.doc is not None and record.doc.meta is not None  # parsed as a knowledge file
    assert record.topic_path is None  # owns no topic (T-9): nothing to compare its location to

    findings = validate_tree(kb, snapshot)
    assert not [f for f in findings if f.code == "TOPIC_LOCATION_MISMATCH"]
