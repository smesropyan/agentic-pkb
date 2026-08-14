"""Conformance tests for the T-rules (docs/superpowers/specs/2026-08-13-tree-T-rules.md).

Every test name ends in the rule id it covers, matching the convention in
``tests/core/test_frontmatter.py``. Tasks 3-9 of
``docs/superpowers/plans/2026-08-13-phase1-the-tree.md`` each add their tests here.
"""

from __future__ import annotations

from pathlib import Path

from pkb.core import frontmatter, tags
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
