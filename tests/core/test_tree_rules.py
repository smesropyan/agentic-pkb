"""Conformance tests for the T-rules (docs/superpowers/specs/2026-08-13-tree-T-rules.md).

Every test name ends in the rule id it covers, matching the convention in
``tests/core/test_frontmatter.py``. Tasks 3-9 of
``docs/superpowers/plans/2026-08-13-phase1-the-tree.md`` each add their tests here.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

from pkb.core import frontmatter, maintenance, paths, scaffold, tags
from pkb.core.errors import Severity, errors_only
from pkb.core.generators import topic_index
from pkb.core.generators.tags_registry import (
    CUSTOM_EXPERT_MARKER,
    MAPPINGS_HEADING,
    SKILLS_HEADING,
    render_root_tags,
    root_tags_findings,
)
from pkb.core.generators.topic_index import render_topic_index, topic_index_findings
from pkb.core.models import FileClass, FileRole
from pkb.core.scan import scan
from pkb.core.validation import validate_content, validate_tree
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


# --------------------------------------------------------------------------------------
# Task 6 — T-22 … T-27: the registry is the one derived root file
# --------------------------------------------------------------------------------------

_COOKING_DESCRIPTION = (
    "Home cooking end to end: equipment, technique and the dishes worth making again."
)
_BAKING_DESCRIPTION = "Bread and pastry, where the dough sets the schedule."


def _design_example_kb(tmp_path: Path) -> Path:
    """DESIGN.md §1.6's worked example tree, built for real (the section's own acceptance surface).

    Cooking carries the example's own description; its ``Baking`` sub-topic owns ``expert.md`` so
    the rendered node carries ``*(custom expert)*`` — the one marked line the example shows it on.
    One note under Cooking tags ``topic.cooking.grilling`` (no folder behind it: a bare node) and
    declares ``related_topics: [ bbq.equipment ]``, and one carries ``domain.legal.compliance`` —
    both copied verbatim from the example's own mapping and domain lines.
    """
    return write_kb(
        tmp_path / "KB",
        {
            "Cooking/topic.md": _knowledge_file(
                title="Cooking",
                description=_COOKING_DESCRIPTION,
                topic="Cooking",
                tags_=["topic.cooking", "type.summary"],
                source_type="summary",
            ),
            "Cooking/sub-topics/Baking/topic.md": _knowledge_file(
                title="Baking",
                description=_BAKING_DESCRIPTION,
                topic="Baking",
                tags_=["topic.cooking.baking", "type.summary"],
                source_type="summary",
            ),
            "Cooking/sub-topics/Baking/expert.md": "# Baking Topic Expert\n\nKnead gently.\n",
            "Cooking/notes/wind.md": _knowledge_file(
                title="Wind shelter",
                description="A windbreak beside the grill",
                topic="Cooking",
                tags_=["topic.cooking.grilling", "type.note"],
                source_type="note",
            ).replace("source_type: note", "related_topics: [ bbq.equipment ]\nsource_type: note"),
            "Cooking/notes/regulations.md": _knowledge_file(
                title="Open-flame rules",
                description="Local fire-safety regulations for open flame",
                topic="Cooking",
                tags_=["topic.cooking", "domain.legal.compliance", "type.note"],
                source_type="note",
            ),
        },
    )


def test_marked_lines_match_the_design_example_exactly_t23_t24_t25(tmp_path: Path) -> None:
    """T-23, T-24, T-25: the described node, the custom-expert node, a bare domain node and the
    ``## Skills`` head appear byte-exact, straight out of DESIGN §1.6's own worked example."""
    snapshot = scan(_design_example_kb(tmp_path))
    shipped = [
        ("brainstorming", "Work an objective wide."),
        ("voice", "Draft in the operator's own register."),
    ]
    text = render_root_tags(snapshot, shipped_skills=shipped)

    described_node = f"- `topic.cooking`{tags.TAG_DEF_SEP}{_COOKING_DESCRIPTION}"
    custom_expert_node = (
        f"    - `topic.cooking.baking`{CUSTOM_EXPERT_MARKER}{tags.TAG_DEF_SEP}{_BAKING_DESCRIPTION}"
    )
    assert described_node in text
    assert custom_expert_node in text
    assert "- `domain.legal`" in text
    assert "    - `domain.legal.compliance`" in text
    assert f"## {SKILLS_HEADING}" in text


def test_a_bare_tag_with_no_topic_folder_carries_no_summary_t23(tmp_path: Path) -> None:
    """T-23: ``topic.cooking.grilling`` is a tag inside Cooking, not a topic — it stays bare."""
    snapshot = scan(_design_example_kb(tmp_path))
    text = render_root_tags(snapshot)

    assert "    - `topic.cooking.grilling`\n" in text
    assert f"`topic.cooking.grilling`{tags.TAG_DEF_SEP}" not in text
    assert f"`topic.cooking.grilling`{CUSTOM_EXPERT_MARKER}" not in text


def test_losing_expert_md_removes_the_marker_t23(tmp_path: Path) -> None:
    """T-23: the marker follows ``expert.md``'s presence, not a cached fact about the topic."""
    kb = _design_example_kb(tmp_path)
    assert CUSTOM_EXPERT_MARKER in render_root_tags(scan(kb))

    (kb / "Cooking" / "sub-topics" / "Baking" / "expert.md").unlink()

    assert CUSTOM_EXPERT_MARKER not in render_root_tags(scan(kb))


def test_lifted_not_authored_description_follows_topic_md_t23(tmp_path: Path) -> None:
    """T-23: nothing here authors a description — changing ``topic.md`` changes exactly this line."""
    kb = _design_example_kb(tmp_path)
    before = render_root_tags(scan(kb))
    assert _COOKING_DESCRIPTION in before

    topic_md = kb / "Cooking" / "topic.md"
    topic_md.write_text(
        topic_md.read_text(encoding="utf-8").replace(_COOKING_DESCRIPTION, "Cooking, rewritten"),
        encoding="utf-8",
    )
    after = render_root_tags(scan(kb))

    assert "Cooking, rewritten" in after
    assert _COOKING_DESCRIPTION not in after
    before_lines = before.split("\n")
    after_lines = after.split("\n")
    assert len(before_lines) == len(after_lines)
    changed = sum(1 for a, b in zip(before_lines, after_lines, strict=True) if a != b)
    assert changed == 1


def test_a_degraded_topic_md_renders_a_placeholder_and_a_finding_t23(tmp_path: Path) -> None:
    """T-23, GE-25: a topic without a readable ``topic.md`` still gets a registry line — the
    retired root catalog used to own this totality guarantee, and the registry owns it now (T-37)."""
    kb = write_kb(
        tmp_path / "KB",
        {"Cooking/topic.md": "---\ntitle: [unclosed\n---\n\n# Cooking\n"},
    )
    snapshot = scan(kb)

    text = render_root_tags(snapshot)
    assert f"- `topic.cooking`{tags.TAG_DEF_SEP}*(missing topic metadata)*" in text

    findings = root_tags_findings(snapshot)
    assert [f.code for f in findings] == ["MISSING_TOPIC_METADATA"]
    assert findings[0].path == "Cooking/topic.md"


def test_domain_nodes_are_always_bare_t24(tmp_path: Path) -> None:
    """T-24: ``domain.*`` never carries a summary — no file backs it the way ``topic.md`` does."""
    snapshot = scan(_design_example_kb(tmp_path))
    domain_block = _block(render_root_tags(snapshot), "Namespace: domain")

    assert domain_block == ["- `domain.legal`", "    - `domain.legal.compliance`"]


def _block(text: str, heading: str) -> list[str]:
    lines = text.split("\n")
    start = lines.index(f"## {heading}") + 1
    rest = lines[start:]
    end = next((i for i, line in enumerate(rest) if line.startswith("## ")), len(rest))
    return [line for line in rest[:end] if line]


def test_skills_section_merges_shipped_and_root_root_shadows_by_name_t25(tmp_path: Path) -> None:
    """T-25: shipped entries plus the root's own ``skills/*/SKILL.md``; a root-owned name shadows
    the shipped one it names, mirroring DESIGN §4's "most specific entry wins" resolution order."""
    kb = write_kb(
        tmp_path / "KB",
        {
            "skills/voice/SKILL.md": (
                "---\nname: voice\ndescription: The operator's own overload.\n---\n\nBody.\n"
            ),
        },
    )
    snapshot = scan(kb)
    shipped = [
        ("brainstorming", "Work an objective wide."),
        ("voice", "The shipped starter profile."),
    ]

    skills_block = _block(render_root_tags(snapshot, shipped_skills=shipped), SKILLS_HEADING)

    assert skills_block == [
        f"- `brainstorming`{tags.TAG_DEF_SEP}Work an objective wide.",
        f"- `voice`{tags.TAG_DEF_SEP}The operator's own overload.",
    ]


def test_a_topics_own_skill_never_reaches_the_registry_t25(tmp_path: Path) -> None:
    """T-25: "A topic's own skills stay in that topic's index.md" — never the root registry."""
    kb = write_kb(
        tmp_path / "KB",
        {
            "Cooking/topic.md": _knowledge_file(
                title="Cooking",
                description=_COOKING_DESCRIPTION,
                topic="Cooking",
                tags_=["topic.cooking", "type.summary"],
                source_type="summary",
            ),
            "Cooking/skills/timing/SKILL.md": (
                "---\nname: timing\ndescription: Cooking's own overload.\n---\n\nBody.\n"
            ),
        },
    )
    snapshot = scan(kb)

    assert "timing" not in render_root_tags(snapshot)


def test_cross_topic_mappings_are_unchanged_by_the_registry_rework_t26(tmp_path: Path) -> None:
    """T-26: mappings still come from ``related_topics`` alone, rendered under the same heading.

    Only Cooking's note declares the relationship, so the declared direction stands — grilling
    left, ``bbq.equipment`` right — the same orientation DESIGN §1.6's own example renders.
    """
    snapshot = scan(_design_example_kb(tmp_path))
    mappings_block = _block(render_root_tags(snapshot), MAPPINGS_HEADING)

    expected = tags.render_mapping_line("topic.cooking.grilling", "topic.bbq.equipment")
    assert mappings_block == [expected]


def test_registry_regeneration_is_byte_idempotent_t27(tmp_path: Path) -> None:
    """T-27: "Regeneration is byte-idempotent" — calling the renderer twice changes nothing."""
    snapshot = scan(_design_example_kb(tmp_path))

    first = render_root_tags(snapshot)
    second = render_root_tags(snapshot)

    assert first == second


def test_sibling_order_is_case_insensitive_by_the_full_string_t27(tmp_path: Path) -> None:
    """T-27: siblings sort case-insensitively, not by codepoint — a tag itself is always lowercase
    (TG-4), so this is only observable where a name is not: the skills catalog's own ``name`` field.

    Plain codepoint order would put every uppercase letter ahead of every lowercase one (``Z`` is
    ``0x5A``, ``a`` is ``0x61``), rendering ``Zebra`` before ``apple``; case-insensitive order does
    not.
    """
    snapshot = scan(write_kb(tmp_path / "KB", {}))
    shipped = [("Zebra", "Capitalized on purpose."), ("apple", "Lowercase on purpose.")]

    skills_block = _block(render_root_tags(snapshot, shipped_skills=shipped), SKILLS_HEADING)

    assert skills_block == [
        f"- `apple`{tags.TAG_DEF_SEP}Lowercase on purpose.",
        f"- `Zebra`{tags.TAG_DEF_SEP}Capitalized on purpose.",
    ]


# --------------------------------------------------------------------------------------
# Task 7 — T-16, T-38: the topic index carries its own skills catalog and approach entries;
# T-32: the conflict machinery it used to render is gone outright
# --------------------------------------------------------------------------------------


def _cooking_topic(body: str = "Body.\n") -> str:
    return _knowledge_file(
        title="Cooking",
        description=_COOKING_DESCRIPTION,
        topic="Cooking",
        tags_=["topic.cooking", "type.summary"],
        source_type="summary",
        body=body,
    )


def test_topic_with_a_skill_and_an_approach_renders_both_sections_t16_t38(tmp_path: Path) -> None:
    """T-16, T-38: a topic's own ``SKILL.md`` and its ``topic.md``'s own ``## Approaches`` list
    each earn a section — the catalog from the skill's own name/description, the pointer copied
    verbatim from the breadth file that names it."""
    kb = write_kb(
        tmp_path / "KB",
        {
            "Cooking/topic.md": _cooking_topic(
                "# Cooking\n\n"
                "## Approaches\n\n"
                "- Reverse sear: Cooking/recipes/ribeye-on-gas.md#Reverse sear\n"
            ),
            "Cooking/skills/timing/SKILL.md": (
                "---\nname: timing\ndescription: When to pull it off the heat.\n---\n\nBody.\n"
            ),
        },
    )
    snapshot = scan(kb)
    text = render_topic_index(snapshot, "Cooking")

    assert "## Skills" in text
    assert f"{tags.BULLET}`timing`{tags.TAG_DEF_SEP}When to pull it off the heat." in text
    assert "## Approaches" in text
    assert "- Reverse sear: Cooking/recipes/ribeye-on-gas.md#Reverse sear" in text


def test_topic_with_neither_renders_neither_heading_t16_t38(tmp_path: Path) -> None:
    """T-16, T-38: no topic-owned skill and no ``## Approaches`` line means no heading at all — a
    promised, empty section is worse than an absent one (§4.3's own totality rule)."""
    kb = write_kb(tmp_path / "KB", {"Cooking/topic.md": _cooking_topic()})
    snapshot = scan(kb)
    text = render_topic_index(snapshot, "Cooking")

    assert "## Skills" not in text
    assert "## Approaches" not in text


def test_sub_topic_repeats_no_parent_skill_t16(tmp_path: Path) -> None:
    """T-16: a topic's catalog lists only what that level declared — a sub-topic never repeats its
    parent's ``SKILL.md``, and a parent never repeats a sub-topic's."""
    kb = write_kb(
        tmp_path / "KB",
        {
            "Cooking/topic.md": _cooking_topic(),
            "Cooking/skills/timing/SKILL.md": (
                "---\nname: timing\ndescription: Cooking's own overload.\n---\n\nBody.\n"
            ),
            "Cooking/sub-topics/Grilling/topic.md": _knowledge_file(
                title="Grilling",
                description="Charcoal and gas grilling",
                topic="Grilling",
                tags_=["topic.cooking.grilling", "type.summary"],
                source_type="summary",
            ),
        },
    )
    snapshot = scan(kb)

    grilling_text = render_topic_index(snapshot, "Cooking/sub-topics/Grilling")
    assert "## Skills" not in grilling_text
    assert "timing" not in grilling_text

    cooking_text = render_topic_index(snapshot, "Cooking")
    assert "## Skills" in cooking_text
    assert "timing" in cooking_text


def test_malformed_approach_line_is_a_finding_and_is_not_copied_t38(tmp_path: Path) -> None:
    """T-38: a line under ``## Approaches`` that does not match ``- <name>: <kb-path>#<heading>``
    becomes a ``MALFORMED_APPROACH_ENTRY`` finding, never a best-effort guess, and it is dropped
    rather than copied into the topic index."""
    kb = write_kb(
        tmp_path / "KB",
        {
            "Cooking/topic.md": _cooking_topic(
                "# Cooking\n\n## Approaches\n\n- Reverse sear, no target at all\n"
            ),
        },
    )
    snapshot = scan(kb)
    text = render_topic_index(snapshot, "Cooking")

    assert "Reverse sear, no target at all" not in text
    assert "## Approaches" not in text  # the only candidate line was malformed

    findings = topic_index_findings(snapshot, "Cooking")
    matches = [f for f in findings if f.code == "MALFORMED_APPROACH_ENTRY"]
    assert len(matches) == 1
    assert matches[0].path == "Cooking/topic.md"
    assert matches[0].rule_id == "T-38"


def test_approach_entries_note_their_source_breadth_file_t38(tmp_path: Path) -> None:
    """T-38: each approach entry carries the breadth file it was lifted from, so two identically
    shaped entries declared in different files stay distinguishable."""
    kb = write_kb(
        tmp_path / "KB",
        {
            "Cooking/topic.md": _cooking_topic(
                "# Cooking\n\n"
                "## Approaches\n\n"
                "- Reverse sear: Cooking/recipes/ribeye-on-gas.md#Reverse sear\n"
            ),
            "Cooking/notes/summary.md": _knowledge_file(
                title="Notes summary",
                description="Distilled rules from cooking experience",
                topic="Cooking",
                tags_=["topic.cooking", "type.summary"],
                source_type="summary",
                body=(
                    "# Notes summary\n\n"
                    "## Approaches\n\n"
                    "- Cold start: Cooking/notes/cold-start.md#Cold start\n"
                ),
            ),
        },
    )
    snapshot = scan(kb)
    text = render_topic_index(snapshot, "Cooking")

    assert "- Reverse sear: Cooking/recipes/ribeye-on-gas.md#Reverse sear (from `topic.md`)" in text
    assert "- Cold start: Cooking/notes/cold-start.md#Cold start (from `notes/summary.md`)" in text


def test_conflict_machinery_is_gone_t32() -> None:
    """T-32: "it grows no task queue, no runner and no status field" (§1.8 rule 9) — the topic
    index carries none of the conflict-review machinery it used to render."""
    for name in ("CONFLICT_TAG", "NEEDS_REVIEW", "NO_REVIEW_NOTE", "_needs_review"):
        assert not hasattr(topic_index, name)


# --------------------------------------------------------------------------------------
# Task 8 — T-15, T-39, T-40: the scaffolder writes the new topic shape
# --------------------------------------------------------------------------------------

_TODAY = date(2024, 9, 1)
_COOKING_DESC = "Home cooking: technique, equipment, and recipes"


def test_scaffolded_topic_md_carries_a_skills_heading_t15_t39_t40(tmp_path: Path) -> None:
    """T-15: the procedural pillar's breadth file is a ``## Skills`` section inside ``topic.md``
    rather than a fourth file class. T-39/T-40: the scaffolder seeds that heading in step 1 so the
    Topic Expert has somewhere to draft into in step 3 — Layer 1 asserts only the placeholder's
    shape, not its prose."""
    kb = tmp_path / "KB"
    kb.mkdir()

    scaffold.scaffold_topic(
        kb, "Cooking", title="Cooking", description=_COOKING_DESC, today=_TODAY, regenerate=False
    )

    body = (kb / "Cooking" / paths.TOPIC_FILE).read_text(encoding="utf-8")
    assert "## Skills" in body


def test_no_scaffolded_file_carries_a_status_tag_t39(tmp_path: Path) -> None:
    """T-39/T-17: there is no ``status.*`` namespace in this design, so no placeholder the
    scaffolder writes stamps one — a scaffolded file carries only its ``topic.*`` and ``type.*``
    tags."""
    kb = tmp_path / "KB"
    kb.mkdir()

    result = scaffold.scaffold_topic(
        kb, "Cooking", title="Cooking", description=_COOKING_DESC, today=_TODAY, regenerate=False
    )

    for rel_path in result.created:
        target = kb / rel_path
        if not target.is_file():
            continue
        meta = frontmatter.parse(target.read_text(encoding="utf-8")).meta
        assert meta is not None
        assert not any(tag.startswith("status.") for tag in meta.tags), rel_path


def test_scaffolded_tree_validates_with_zero_errors_under_the_new_rules_t39(tmp_path: Path) -> None:
    """T-39: every file the scaffolder writes — including the ``## Skills`` heading it now
    seeds — passes :func:`~pkb.core.validation.validate_content` with zero errors, and a
    freshly scaffolded, regenerated tree carries zero findings of any kind."""
    kb = tmp_path / "KB"
    kb.mkdir()

    result = scaffold.scaffold_topic(
        kb, "Cooking", title="Cooking", description=_COOKING_DESC, today=_TODAY, regenerate=True
    )

    written = [rel_path for rel_path in result.created if (kb / rel_path).is_file()]
    assert written
    for rel_path in written:
        text = (kb / rel_path).read_text(encoding="utf-8")
        assert errors_only(validate_content(kb, rel_path, text)) == [], rel_path

    assert validate_tree(kb) == []


# --------------------------------------------------------------------------------------
# Task 9 — T-41: maintenance regenerates and validates, nothing else
# --------------------------------------------------------------------------------------


def test_maintenance_exposes_no_scan_trigger_surface_t41() -> None:
    """T-41: "it grows no task queue, no runner and no status field" (T-32) applies to
    maintenance's own plumbing — no scan-trigger roles, no automatic changed-set-to-request
    builder, and no origin constant that named the automatic trigger's own requests."""
    assert not hasattr(maintenance, "_SCAN_TRIGGER_ROLES")
    assert not hasattr(maintenance, "build_scan_requests")
    assert not hasattr(maintenance, "MAINTENANCE_ORIGIN")


def test_per_run_regeneration_touches_exactly_the_indexes_and_the_registry_t41(
    tmp_path: Path,
) -> None:
    """T-41: the public API's per-run regeneration touches exactly the topic ``index.md`` files
    and the root ``tags.md``, and nothing else — no scan request rides along, even for a topic
    whose files were named as touched."""
    kb = tmp_path / "KB"
    kb.mkdir()
    scaffold.scaffold_topic(
        kb, "Cooking", title="Cooking", description=_COOKING_DESC, today=_TODAY, regenerate=False
    )
    scaffold.scaffold_subtopic(
        kb,
        "Cooking",
        "Grilling",
        title="Grilling",
        description="Charcoal and gas grilling",
        today=_TODAY,
        regenerate=False,
    )

    report = maintenance.flush(kb, ["Cooking/notes/summary.md"], today=_TODAY)

    assert set(report.derived) == {
        "tags.md",
        "Cooking/index.md",
        "Cooking/sub-topics/Grilling/index.md",
    }
    assert report.scan_requests == []
