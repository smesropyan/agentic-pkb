"""The proves-itself test (Task 10 of ``docs/superpowers/plans/2026-08-13-phase1-the-tree.md``).

Written to pass, not TDD-red-first: this is the phase's living end-to-end proof that the tree
built in Tasks 3-9 matches ``DESIGN.md`` §1.6's own worked example, kept in the suite rather than
thrown away once green. It exercises the whole surface in one scenario — scaffold a topic and a
sub-topic through the real scaffolder, file a note and a reference through the real frontmatter
serializer, scan and validate the result, regenerate the derived files twice, and check the bytes
that come out.

Every asserted registry line below was copied by hand out of ``DESIGN.md`` §1.6 itself — never out
of ``tests/core/golden/tags.md``, which the same generator under test also writes and so cannot
serve as independent evidence of anything. The document is the acceptance surface; a golden that
quietly drifted from it would still make a golden-only test pass, and this test exists so that
cannot happen unnoticed.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pkb.core import frontmatter, scaffold, tags
from pkb.core.errors import Severity, errors_only
from pkb.core.generators import regenerate_all
from pkb.core.generators.tags_registry import (
    CUSTOM_EXPERT_MARKER,
    MAPPINGS_HEADING,
    SKILLS_HEADING,
    render_root_tags,
)
from pkb.core.models import Metadata
from pkb.core.scan import scan
from pkb.core.validation import validate_tree

_TODAY = date(2024, 1, 1)

# Copied by hand from DESIGN.md §1.6, "Example root tags.md (excerpt showing the Cooking
# subtree)" — the two topic-backed descriptions and the two shipped-skill entries the example
# renders. Nothing here is read from the document at test time; the point of hand-copying is that
# a future edit to the document without a matching edit here fails loudly instead of silently.
_COOKING_DESCRIPTION = (
    "Home cooking end to end: equipment, technique and the dishes worth making again."
)
_BAKING_DESCRIPTION = "Bread and pastry, where the dough sets the schedule."
_SHIPPED_SKILLS = [
    (
        "brainstorming",
        "Work an objective wide: survey the map, ask one question per callee, return candidate "
        "approaches.",
    ),
    ("voice", "Draft in the operator's own register."),
]


def _tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file in the tree, keyed by KB-relative POSIX path — derived files included."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _build_design_example_kb(kb: Path) -> None:
    """DESIGN §1.6's worked example, built for real rather than copied as a fixture dict.

    Cooking and its Baking sub-topic come from the real scaffolder (SC-1 … SC-12), carrying the
    example's own descriptions verbatim. Baking's ``expert.md`` is what earns it the example's
    ``*(custom expert)*`` marker (T-23). The note and the reference are the two knowledge files
    Task 10 asks to be filed "through the frontmatter serializer, not hand-written YAML strings" —
    :func:`pkb.core.frontmatter.serialize` builds their bytes, the way the scaffolder already builds
    every placeholder's (``scaffold._document``). The note's ``related_topics`` and the reference's
    ``domain.legal.compliance`` tag are what the example's own cross-topic mapping line and bare
    ``domain.legal`` node come from.
    """
    scaffold.scaffold_topic(
        kb,
        "Cooking",
        title="Cooking",
        description=_COOKING_DESCRIPTION,
        today=_TODAY,
        regenerate=False,
    )
    scaffold.scaffold_subtopic(
        kb,
        "Cooking",
        "Baking",
        title="Baking",
        description=_BAKING_DESCRIPTION,
        today=_TODAY,
        regenerate=False,
    )
    (kb / "Cooking" / "sub-topics" / "Baking" / "expert.md").write_text(
        "# Baking Topic Expert\n\nKnead gently, and give the dough the time it asks for.\n",
        encoding="utf-8",
    )

    note_meta = Metadata(
        title="Wind shelter",
        description="A windbreak beside the grill cuts fuel use in gusty weather",
        topic="Cooking",
        tags=("topic.cooking.grilling", "type.note"),
        created=_TODAY,
        updated=_TODAY,
        related_topics=("bbq.equipment",),
        source_type="note",
    )
    note_text = frontmatter.serialize(
        note_meta,
        "\n# Wind shelter\n\nA windbreak beside the grill holds the temperature steady in gusty "
        "weather.\n",
    )
    note_path = kb / "Cooking" / "notes" / "wind-shelter.md"
    note_path.write_text(note_text, encoding="utf-8")

    reference_meta = Metadata(
        title="Open-flame rules",
        description="Local fire-safety regulations for open flame",
        topic="Cooking",
        tags=("topic.cooking", "domain.legal.compliance", "type.reference"),
        created=_TODAY,
        updated=_TODAY,
        source_type="reference",
    )
    reference_text = frontmatter.serialize(
        reference_meta, "\n# Open-flame rules\n\nNo open flame on the balcony after dusk.\n"
    )
    reference_dir = kb / "Cooking" / "references" / "open-flame-rules"
    reference_dir.mkdir(parents=True, exist_ok=True)
    (reference_dir / "open-flame-rules.md").write_text(reference_text, encoding="utf-8")


def test_the_tree_proves_itself_against_design_1_6(tmp_path: Path) -> None:
    """Scaffold, file, validate, regenerate twice, and check DESIGN §1.6's own words come back out.

    Five things in one scenario, in the order Task 10 names them:

    1. the DESIGN §1.6 example topics exist, built through the real scaffolder;
    2. a note and a reference are filed through ``frontmatter.serialize``;
    3. ``scan`` + ``validate_tree`` raise zero errors over the freshly authored tree (a
       ``DANGLING_RELATED_TOPIC`` warning survives — ``bbq.equipment`` names no topic root in this
       small a tree, exactly as it would in DESIGN's own excerpt, which shows the mapping without
       showing the BBQ topic behind it — and a warning is not an error);
    4. regenerating every derived file twice produces byte-identical trees both times;
    5. the registry that comes out carries DESIGN §1.6's own lines back, byte for byte.
    """
    kb = tmp_path / "KB"
    kb.mkdir()

    _build_design_example_kb(kb)

    snapshot = scan(kb)
    findings = validate_tree(kb, snapshot)
    assert errors_only(findings) == []
    non_errors = [f for f in findings if f.severity is not Severity.ERROR]
    assert len(non_errors) == 1
    assert non_errors[0].code == "DANGLING_RELATED_TOPIC"
    assert non_errors[0].path == "Cooking/notes/wind-shelter.md"

    first = regenerate_all(kb)
    assert first.findings == []
    assert set(first.written) == {
        "tags.md",
        "Cooking/index.md",
        "Cooking/sub-topics/Baking/index.md",
    }
    after_first = _tree_bytes(kb)

    second = regenerate_all(kb)
    assert second.written == []
    assert _tree_bytes(kb) == after_first

    registry = render_root_tags(scan(kb), shipped_skills=_SHIPPED_SKILLS)

    # Every line asserted below is DESIGN.md §1.6, quoted — see the module docstring. The
    # separator between a tag and its gloss (an EN DASH, TG-13) and the cross-topic mapping arrow
    # are composed through the real constants rather than a literal character in this file, so
    # ruff's ambiguous-unicode check (RUF001) cannot silently swap one for a hyphen here; the
    # prose on either side of them is still hand-copied straight out of the document.
    assert '---\ntitle: "PKB Tag Registry"\nsource_type: tag-registry\n---\n' in registry
    assert f"- `topic.cooking`{tags.TAG_DEF_SEP}{_COOKING_DESCRIPTION}" in registry
    assert (
        f"    - `topic.cooking.baking`{CUSTOM_EXPERT_MARKER}{tags.TAG_DEF_SEP}{_BAKING_DESCRIPTION}"
    ) in registry
    assert "    - `topic.cooking.grilling`\n" in registry
    assert (
        f"- `type.note`{tags.TAG_DEF_SEP}an observation from the operator's own practice"
    ) in registry
    assert f"- `type.reference`{tags.TAG_DEF_SEP}static source" in registry
    assert (
        f"- `type.solution`{tags.TAG_DEF_SEP}reusable solution (a note tagged as a solution)"
    ) in registry
    assert f"- `type.summary`{tags.TAG_DEF_SEP}breadth overview" in registry
    assert "- `domain.legal`" in registry
    assert "    - `domain.legal.compliance`" in registry
    assert f"## {SKILLS_HEADING}" in registry
    assert (
        f"- `brainstorming`{tags.TAG_DEF_SEP}Work an objective wide: survey the map, ask one "
        "question per callee, return candidate approaches."
    ) in registry
    assert f"- `voice`{tags.TAG_DEF_SEP}Draft in the operator's own register." in registry
    assert f"## {MAPPINGS_HEADING}" in registry
    assert tags.render_mapping_line("topic.cooking.grilling", "topic.bbq.equipment") in registry
