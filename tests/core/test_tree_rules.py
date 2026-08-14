"""Conformance tests for the T-rules (docs/superpowers/specs/2026-08-13-tree-T-rules.md).

Every test name ends in the rule id it covers, matching the convention in
``tests/core/test_frontmatter.py``. Tasks 3-9 of
``docs/superpowers/plans/2026-08-13-phase1-the-tree.md`` each add their tests here.
"""

from __future__ import annotations

from pkb.core import frontmatter


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
