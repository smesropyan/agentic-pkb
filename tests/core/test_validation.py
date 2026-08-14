"""Tests for `pkb.core.validation` — one test per VA rule, plus the rules other modules deferred.

Every test name ends in the rule id it covers so `grep -n va13 tests/core/test_validation.py`
lands on the contract. Rules stated as a negative ("never flagged", "no such check") get a passing
fixture asserting silence; rules stated as an error get both a failing and a passing case.

The last section is about the *walk* rather than about any one rule. `validate_tree` reads
`pkb.core.scan.scan`'s snapshot and no other — decision C — and `degraded()` builds the tree that
holds one of every defect the walk itself can see, so the tests there can assert both halves of
CX-5's one-defect-one-finding property: no code the rules re-derive is reported twice, and no code
only the walk can see goes missing.
"""

from __future__ import annotations

import builtins
import dataclasses
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pkb.core import diagnostics, validation
from pkb.core.errors import Finding, Severity
from pkb.core.maintenance import find_broken_links
from pkb.core.models import KbSnapshot
from pkb.core.scan import scan
from pkb.core.validation import (
    validate_content,
    validate_file,
    validate_tree,
)

# --------------------------------------------------------------------------------------
# Fixture helpers
# --------------------------------------------------------------------------------------

_DEFAULT_FIELDS: Mapping[str, str] = {
    "title": '"Grill Performance"',
    "description": '"How wind affects grill temperature"',
    "topic": '"Cooking"',
    "tags": "\n  - topic.cooking.grilling\n  - type.note",
    "created": "2024-10-15",
    "updated": "2024-10-16",
    "source_type": "note",
}

NOTE_PATH = "Cooking/notes/grill-performance.md"


def tag_block(*values: str) -> str:
    """A block sequence for the `tags` field, in the canonical two-space style (FM-8)."""
    return "".join(f"\n  - {value}" for value in values)


def md(**overrides: str | None) -> str:
    """A markdown file with frontmatter: override a field's YAML, or pass None to drop it."""
    fields = dict(_DEFAULT_FIELDS)
    for key, value in overrides.items():
        if value is None:
            fields.pop(key, None)
        else:
            fields[key] = value
    lines = ["---"]
    for key, value in fields.items():
        lines.append(f"{key}:{value}" if value.startswith("\n") else f"{key}: {value}")
    lines += ["---", "", "Body text.", ""]
    return "\n".join(lines)


def note_at(rel_path: str, **overrides: str | None) -> str:
    """A note whose title matches its file stem, so VA-35 stays quiet unless a test asks for it."""
    stem = PurePosixPath(rel_path).stem
    fields: dict[str, str | None] = {"title": '"' + stem.replace("-", " ").title() + '"'}
    fields.update(overrides)
    return md(**fields)


SESSION_PATH = "sessions/trading-plan.md"


def session_at(rel_path: str, **overrides: str | None) -> str:
    """A session file (`FileRole.SESSION`): no owning topic, `topic: "(session)"`, and no
    `topic.*` tag by default (P5) — zero experts have taken part, so there is none yet."""
    fields: dict[str, str | None] = {
        "title": f'"{rel_path}"',
        "topic": '"(session)"',
        "tags": tag_block("type.summary"),
        "source_type": "summary",
    }
    fields.update(overrides)
    return md(**fields)


def topic_md(name: str, tag: str) -> str:
    """A `topic.md` placeholder: `source_type: summary` + `type.summary` (decision A)."""
    return md(
        title=f'"{name}"',
        description=f'"Everything about {name}"',
        topic=f'"{name}"',
        tags=tag_block(tag, "type.summary"),
        source_type="summary",
    )


def summary_md(name: str, topic: str, tag: str) -> str:
    return md(
        title=f'"{name}"',
        description=f'"Breadth overview of {name}"',
        topic=f'"{topic}"',
        tags=tag_block(tag, "type.summary"),
        source_type="summary",
    )


def write(kb: Path, rel_path: str, text: str) -> Path:
    path = kb / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_topic(kb: Path, rel_path: str, tag: str) -> None:
    """A structurally complete topic root: `topic.md` plus both breadth summaries (SC-1)."""
    name = PurePosixPath(rel_path).name
    write(kb, f"{rel_path}/topic.md", topic_md(name, tag))
    write(kb, f"{rel_path}/notes/summary.md", summary_md("Notes", name, tag))
    write(kb, f"{rel_path}/references/summary.md", summary_md("References", name, tag))


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    """A minimal, valid two-topic knowledge base."""
    root = tmp_path / "KB"
    root.mkdir()
    make_topic(root, "Cooking", "topic.cooking")
    make_topic(root, "BBQ", "topic.bbq")
    return root


def codes(findings: Iterable[Finding]) -> list[str]:
    return [finding.code for finding in findings]


def rule_ids(findings: Iterable[Finding]) -> list[str]:
    return [finding.rule_id for finding in findings]


def errors(findings: Iterable[Finding]) -> list[Finding]:
    return [finding for finding in findings if finding.severity is Severity.ERROR]


def only(findings: list[Finding], code: str) -> Finding:
    """The single finding carrying `code` — asserts there is exactly one."""
    matches = [finding for finding in findings if finding.code == code]
    assert len(matches) == 1, f"expected exactly one {code}, got {codes(findings)}"
    return matches[0]


# --------------------------------------------------------------------------------------
# Entry-point contract (VA-1, VA-2, CX-5, CX-6)
# --------------------------------------------------------------------------------------


def test_validate_content_never_opens_the_file_under_test_va1(
    kb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-write gate works for a path that does not exist and reads no file (VA-1)."""

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("validate_content must not open any file")

    monkeypatch.setattr(builtins, "open", explode)
    monkeypatch.setattr(Path, "read_text", explode)

    findings = validate_content(kb, "Cooking/notes/brand-new.md", "no frontmatter here\n")
    assert codes(findings) == ["MISSING_FRONTMATTER"]
    assert not (kb / "Cooking/notes/brand-new.md").exists()


def test_validate_content_on_a_valid_note_is_silent_va1(kb: Path) -> None:
    assert validate_content(kb, NOTE_PATH, note_at(NOTE_PATH)) == []


def test_validate_content_is_stateless_va2(kb: Path) -> None:
    """Identical arguments, identical findings — the retry bound lives in Layer 2 (VA-2)."""
    text = note_at(NOTE_PATH, tags=tag_block("topic.cooking", "type.note"))
    assert validate_content(kb, NOTE_PATH, text) == validate_content(kb, NOTE_PATH, text)


@settings(max_examples=50, deadline=None)
@given(text=st.text(max_size=200))
def test_validate_content_is_deterministic_for_any_text_va2(text: str) -> None:
    """Determinism is a property, not three examples (VA-2). No KB on disk is needed."""
    root = Path("/nonexistent-knowledge-base")
    assert validate_content(root, "Cooking/notes/x.md", text) == validate_content(
        root, "Cooking/notes/x.md", text
    )


def test_three_defects_yield_three_distinct_codes_cx5(kb: Path) -> None:
    """One call reports every defect it can see, never just the first (CX-5)."""
    text = note_at(
        NOTE_PATH,
        description=None,
        source_type="essay",
        tags=tag_block("topic.cooking.grilling", "type.note", "type.solution"),
    )
    found = validate_content(kb, NOTE_PATH, text)
    assert set(codes(found)) == {
        "MISSING_REQUIRED_FIELD",
        "UNKNOWN_SOURCE_TYPE",
        "MULTIPLE_TYPE_TAGS",
    }
    assert len(found) == 3


def test_every_finding_carries_a_code_and_a_message_cx6(kb: Path) -> None:
    """Both halves of CX-6's assertion: the declared field set, then the emitted findings.

    The field set is decision B's, and it is what Layer 2's error `ToolMessage` reads. Asserting
    only the second half let a *tenth* field slip in unnoticed — dropping one already fails loudly,
    because production code passes every one of these by keyword at construction.
    """
    assert {f.name for f in dataclasses.fields(Finding)} == {
        "code",
        "severity",
        "path",
        "message",
        "field",
        "value",
        "line",
        "rule_id",
        "hint",
    }
    findings = validate_content(kb, NOTE_PATH, "nothing\n")
    assert findings and all(f.code and f.message and f.rule_id and f.path for f in findings)


# --------------------------------------------------------------------------------------
# Frontmatter presence and required fields (VA-3, VA-4, VA-39, VA-41)
# --------------------------------------------------------------------------------------


def test_missing_frontmatter_is_exactly_one_finding_va3(kb: Path) -> None:
    findings = validate_content(kb, NOTE_PATH, "# Just a heading\n")
    assert codes(findings) == ["MISSING_FRONTMATTER"]
    assert rule_ids(findings) == ["VA-3"]


def test_empty_frontmatter_block_is_missing_frontmatter_va3(kb: Path) -> None:
    assert codes(validate_content(kb, NOTE_PATH, "---\n---\n\nbody\n")) == ["MISSING_FRONTMATTER"]


@pytest.mark.parametrize(
    "field",
    ["title", "description", "topic", "tags", "created", "updated", "source_type"],
)
def test_each_required_field_is_reported_by_name_va4(kb: Path, field: str) -> None:
    findings = validate_content(kb, NOTE_PATH, note_at(NOTE_PATH, **{field: None}))
    missing = [f for f in findings if f.code == "MISSING_REQUIRED_FIELD"]
    assert [f.field for f in missing] == [field]
    assert missing[0].severity is Severity.ERROR


def test_an_empty_required_value_counts_as_missing_va4(kb: Path) -> None:
    findings = validate_content(kb, NOTE_PATH, note_at(NOTE_PATH, description='""'))
    assert only(findings, "MISSING_REQUIRED_FIELD").field == "description"


def test_a_complete_note_reports_no_missing_field_va4(kb: Path) -> None:
    assert "MISSING_REQUIRED_FIELD" not in codes(
        validate_content(kb, NOTE_PATH, note_at(NOTE_PATH))
    )


def test_badly_typed_fields_are_reported_not_raised_fm4(kb: Path) -> None:
    """A scalar `tags` is one defect: the cardinality rules stay quiet rather than pile on (VA-9)."""
    findings = validate_content(kb, NOTE_PATH, note_at(NOTE_PATH, tags=' "topic.cooking"'))
    assert codes(findings) == ["FIELD_TYPE"]
    assert findings[0].field == "tags"


def test_a_datetime_created_is_a_date_format_finding_fm5(kb: Path) -> None:
    findings = validate_content(kb, NOTE_PATH, note_at(NOTE_PATH, created="2024-10-15T09:00:00Z"))
    assert only(findings, "DATE_FORMAT").rule_id == "FM-5"


def test_unparseable_yaml_is_a_finding_va39(kb: Path) -> None:
    findings = validate_content(kb, NOTE_PATH, "---\ntitle: [unclosed\n---\n\nbody\n")
    finding = only(findings, "FRONTMATTER_PARSE_ERROR")
    assert finding.severity is Severity.ERROR
    assert finding.line is not None


def test_an_unparseable_file_does_not_abort_validate_tree_va39(kb: Path) -> None:
    write(kb, "Cooking/notes/broken.md", "---\ntitle: [unclosed\n---\n")
    write(kb, "Cooking/notes/fine.md", note_at("Cooking/notes/fine.md"))
    findings = validate_tree(kb)
    assert "FRONTMATTER_PARSE_ERROR" in codes(findings)
    assert [f.path for f in findings if f.code == "FRONTMATTER_PARSE_ERROR"] == [
        "Cooking/notes/broken.md"
    ]


def test_topic_md_is_not_exempt_from_required_fields_va41(kb: Path) -> None:
    """The root catalog reads `topic.md`'s frontmatter, so it is authored, not derived (VA-41)."""
    text = topic_md("Cooking", "topic.cooking").replace(
        'description: "Everything about Cooking"\n', ""
    )
    findings = validate_content(kb, "Cooking/topic.md", text)
    assert only(findings, "MISSING_REQUIRED_FIELD").field == "description"


# --------------------------------------------------------------------------------------
# File-class exemptions (VA-5, VA-6, VA-7, VA-24)
# --------------------------------------------------------------------------------------

GENERATED_INDEX = '---\ntitle: "Cooking — Index"\ndescription: "d"\ntopic: "Cooking"\nsource_type: index\n---\n\n# Cooking — Index\n'


def test_derived_files_are_exempt_from_the_authored_schema_va5(kb: Path) -> None:
    assert validate_content(kb, "Cooking/index.md", GENERATED_INDEX) == []
    assert "MISSING_REQUIRED_FIELD" in codes(
        validate_content(kb, "Cooking/notes/x.md", GENERATED_INDEX)
    )


def test_a_derived_by_name_index_keeps_its_path_rules_va5(kb: Path) -> None:
    """`notes/x/index.md` is exempt from field checks but VA-17 still names it (PA-11 vs PA-12)."""
    findings = validate_content(kb, "Cooking/notes/steak/index.md", GENERATED_INDEX)
    assert codes(findings) == ["ITEM_NAMED_INDEX"]


SKILL = '---\nname: voice\ndescription: "How the human writes"\n---\n\n# Voice\n'


def test_skill_files_are_checked_only_for_name_and_description_va6(kb: Path) -> None:
    assert validate_content(kb, "skills/voice/SKILL.md", SKILL) == []
    findings = validate_content(kb, "skills/voice/SKILL.md", '---\ndescription: "d"\n---\n')
    assert codes(findings) == ["MISSING_SKILL_FIELD"]
    assert only(findings, "MISSING_SKILL_FIELD").field == "name"


def test_skills_never_carry_pkb_required_fields_va6(kb: Path) -> None:
    write(kb, "skills/voice/SKILL.md", SKILL)
    write(kb, "Cooking/skills/voice/SKILL.md", SKILL)
    assert "MISSING_REQUIRED_FIELD" not in codes(validate_tree(kb))


def test_expert_md_carries_no_frontmatter_findings_va6(kb: Path) -> None:
    """`expert.md` is a prompt override, not a deepagents skill — neither schema applies."""
    write(kb, "Cooking/expert.md", "# Cooking expert\n\nBe precise.\n")
    assert [f for f in validate_tree(kb) if f.path == "Cooking/expert.md"] == []


def test_non_markdown_files_are_never_parsed_va7(kb: Path) -> None:
    assert validate_content(kb, "Cooking/notes/steak/media/pan.jpg", "\x00\x01binary") == []


def test_reference_source_files_produce_no_findings_va7_va24(kb: Path) -> None:
    """A reference folder holds arbitrary source files by design (VA-24)."""
    write(
        kb,
        "Cooking/references/grill-basics/grill-basics.md",
        md(
            source_type="reference",
            title='"Grill Basics"',
            tags=tag_block("topic.cooking.grilling", "type.reference", "status.approved"),
        ),
    )
    (kb / "Cooking/references/grill-basics/grill-basics.pdf").write_bytes(b"%PDF-1.4")
    (kb / "Cooking/references/grill-basics/scan.png").write_bytes(b"\x89PNG")
    assert [f for f in validate_tree(kb) if (f.path or "").endswith((".pdf", ".png"))] == []


# --------------------------------------------------------------------------------------
# Tags (VA-8, VA-9, VA-10, VA-11, VA-40)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "code"),
    [
        pytest.param("Topic.Cooking", "TAG_SYNTAX", id="va8-syntax-uppercase"),
        pytest.param("topic..cooking", "TAG_SYNTAX", id="va8-syntax-double-dot"),
        pytest.param("topic.heat_management", "TAG_SYNTAX", id="va8-syntax-underscore"),
        pytest.param("project.alpha", "UNKNOWN_TAG_NAMESPACE", id="va8-namespace"),
        pytest.param("topic.a.b.c.d", "TAG_DEPTH_EXCEEDED", id="va8-depth"),
        pytest.param("type.article", "UNKNOWN_TYPE_TAG", id="va8-type-vocabulary"),
    ],
)
def test_tag_syntax_namespace_depth_and_vocabulary_va8(kb: Path, tag: str, code: str) -> None:
    text = note_at(NOTE_PATH, tags=tag_block("topic.cooking", "type.note", "status.approved", tag))
    assert code in codes(validate_content(kb, NOTE_PATH, text))


def test_open_namespaces_never_yield_a_vocabulary_finding_va8(kb: Path) -> None:
    text = note_at(
        NOTE_PATH,
        tags=tag_block("topic.cooking.grilling", "domain.legal.compliance", "type.note"),
    )
    assert validate_content(kb, NOTE_PATH, text) == []


@pytest.mark.parametrize(
    ("declared", "code"),
    [
        pytest.param(("type.note", "status.draft"), "MISSING_TOPIC_TAG", id="va9-topic-zero"),
        pytest.param(("topic.cooking", "status.draft"), "MISSING_TYPE_TAG", id="va9-type-zero"),
        pytest.param(
            ("topic.cooking", "type.note", "type.summary", "status.draft"),
            "MULTIPLE_TYPE_TAGS",
            id="va9-type-two",
        ),
    ],
)
def test_tag_cardinality_va9(kb: Path, declared: tuple[str, ...], code: str) -> None:
    """VA-9, T-19: "at least one ``topic.*`` tag and exactly one ``type.*`` tag" — zero of either
    and two ``type.*`` tags are each their own error. ``TAG_DEPTH_EXCEEDED``, T-19's fourth code,
    is TG-3's own (``test_four_segments_pass_five_fail_tg3``, ``tests/core/test_tags.py``)."""
    text = note_at(NOTE_PATH, tags=tag_block(*declared))
    finding = only(validate_content(kb, NOTE_PATH, text), code)
    assert finding.severity is Severity.ERROR
    assert finding.field == "tags"


def test_a_session_file_needs_no_topic_tag_with_zero_experts_p5(kb: Path) -> None:
    """P5 (`docs/superpowers/plans/2026-08-14-phase2-sessions.md`, "Three rulings"; T-19 amended):
    the `topic.*` floor T-19/VA-9 fixes for every knowledge file does not bind `FileRole.SESSION`
    — a session opened directly on the Librarian, before any Topic Expert has joined it, has zero
    participating experts and so zero `topic.*` tags, and that validates clean. The `type.*` floor
    (exactly one) stays in force, on a session file as on every other."""
    text = session_at(SESSION_PATH)
    assert errors(validate_content(kb, SESSION_PATH, text)) == []


def test_a_non_session_file_still_needs_a_topic_tag_p5(kb: Path) -> None:
    """The floor P5 scopes away from `FileRole.SESSION` stays in force for everything else — the
    contrast case: an ordinary note with zero `topic.*` tags is still `MISSING_TOPIC_TAG`
    (mirrors `test_tag_cardinality_va9`'s `va9-topic-zero` case, named here for the P5 boundary)."""
    text = note_at(NOTE_PATH, tags=tag_block("type.note", "status.draft"))
    assert "MISSING_TOPIC_TAG" in codes(validate_content(kb, NOTE_PATH, text))


def test_duplicate_tag_is_a_warning_va10(kb: Path) -> None:
    text = note_at(
        NOTE_PATH,
        tags=tag_block("topic.cooking", "topic.cooking", "type.note", "status.approved"),
    )
    finding = only(validate_content(kb, NOTE_PATH, text), "DUPLICATE_TAG")
    assert finding.severity is Severity.WARNING
    assert finding.value == "topic.cooking"


def test_source_type_and_type_tag_are_a_bijection_va11(kb: Path) -> None:
    text = note_at(NOTE_PATH, tags=tag_block("topic.cooking", "type.solution", "status.approved"))
    finding = only(validate_content(kb, NOTE_PATH, text), "SOURCE_TYPE_TAG_MISMATCH")
    assert "type.note" in (finding.hint or "")


def test_topic_md_pairs_summary_with_type_summary_va11(kb: Path) -> None:
    """Decision A: `topic.md` is `source_type: summary` + `type.summary`, with no special case."""
    assert validate_content(kb, "Cooking/topic.md", topic_md("Cooking", "topic.cooking")) == []


def test_a_novel_topic_tag_is_accepted_va40(kb: Path) -> None:
    """Layer 1 keeps no approved-tag list; governance is a Layer 2 dialog concern (VA-40)."""
    text = note_at(NOTE_PATH, tags=tag_block("topic.cooking.sous-vide", "type.note"))
    assert validate_content(kb, NOTE_PATH, text) == []


# --------------------------------------------------------------------------------------
# Location consistency (VA-12 … VA-15)
# --------------------------------------------------------------------------------------


def test_topic_field_must_name_the_owning_topic_va12(kb: Path) -> None:
    make_topic(kb, "Physics", "topic.physics")
    path = "Physics/notes/x.md"
    text = note_at(
        path, topic='"Cooking"', tags=tag_block("topic.physics", "type.note", "status.draft")
    )
    finding = only(validate_content(kb, path, text), "TOPIC_LOCATION_MISMATCH")
    assert "Physics" in (finding.hint or "")


@pytest.mark.parametrize(
    "declared",
    [
        pytest.param('"topic.cooking"', id="va12-tag"),
        pytest.param('"Cooking/notes"', id="va12-path"),
    ],
)
def test_topic_field_is_a_display_name_va12(kb: Path, declared: str) -> None:
    findings = validate_content(kb, NOTE_PATH, note_at(NOTE_PATH, topic=declared))
    assert only(findings, "TOPIC_FIELD_FORMAT").field == "topic"


def test_topic_field_comparison_is_slug_based_va12(kb: Path) -> None:
    make_topic(kb, "Cooking/sub-topics/Heat Management", "topic.cooking.heat-management")
    path = "Cooking/sub-topics/Heat Management/notes/x.md"
    text = note_at(
        path,
        topic='"heat management"',
        tags=tag_block("topic.cooking.heat-management", "type.note"),
    )
    assert validate_content(kb, path, text) == []


def test_source_type_must_match_the_location_va13(kb: Path) -> None:
    path = "Cooking/references/grill-basics/grill-basics.md"
    text = note_at(
        path,
        source_type="note",
        tags=tag_block("topic.cooking", "type.note", "status.approved"),
    )
    finding = only(validate_content(kb, path, text), "SOURCE_TYPE_LOCATION_MISMATCH")
    assert finding.value == "note"


@pytest.mark.parametrize(
    ("path", "source_type", "type_tag"),
    [
        pytest.param("Cooking/notes/x.md", "note", "type.note", id="va13-note"),
        pytest.param("Cooking/notes/x.md", "solution", "type.solution", id="va13-solution"),
        pytest.param(
            "Cooking/notes/summary.md", "summary", "type.summary", id="va13-notes-summary"
        ),
        pytest.param(
            "Cooking/references/grill-basics/grill-basics.md",
            "reference",
            "type.reference",
            id="va13-reference",
        ),
        pytest.param("Cooking/topic.md", "summary", "type.summary", id="va13-topic-overview"),
    ],
)
def test_the_location_table_accepts_its_own_rows_va13(
    kb: Path, path: str, source_type: str, type_tag: str
) -> None:
    """VA-13; the ``va13-reference`` case is also T-7's mechanical half: a first-written
    ``references/<src>/<src>.md`` — all seven required fields, ``source_type: reference``,
    ``type.reference`` — validates with zero findings, which is as much of "naming the source is
    the approval on the first map of it" as Layer 1 checks without a turn history."""
    text = note_at(
        path,
        source_type=source_type,
        tags=tag_block("topic.cooking", type_tag),
    )
    assert errors(validate_content(kb, path, text)) == []


def test_a_solution_may_not_live_under_references_va14(kb: Path) -> None:
    """VA-14, T-31: ``references/**`` accepts only ``type.reference`` — the mechanical proxy T-34
    enforces for §1.8 rule 8's read/done line."""
    path = "Cooking/references/grill-basics/grill-basics.md"
    text = note_at(
        path,
        source_type="solution",
        tags=tag_block("topic.cooking", "type.solution", "status.approved"),
    )
    assert "TYPE_TAG_LOCATION_MISMATCH" in codes(validate_content(kb, path, text))


def test_a_note_may_not_be_tagged_type_reference_va14(kb: Path) -> None:
    """VA-14, T-31: ``notes/**`` accepts only ``type.note``/``type.solution`` — the same folder-vs-
    tag mechanical proxy, the other direction."""
    path = "Cooking/notes/x.md"
    text = note_at(
        path,
        source_type="reference",
        tags=tag_block("topic.cooking", "type.reference"),
    )
    assert "TYPE_TAG_LOCATION_MISMATCH" in codes(validate_content(kb, path, text))


def test_topic_tags_must_sit_under_the_owning_topic_va15(kb: Path) -> None:
    make_topic(kb, "Cooking/sub-topics/Grilling", "topic.cooking.grilling")
    path = "Cooking/sub-topics/Grilling/notes/x.md"
    text = note_at(
        path,
        topic='"Grilling"',
        tags=tag_block("topic.physics.heat", "type.note", "status.draft"),
    )
    finding = only(validate_content(kb, path, text), "TOPIC_TAG_LOCATION_MISMATCH")
    assert finding.value == "topic.physics.heat"


def test_a_deeper_topic_tag_needs_no_folder_va15(kb: Path) -> None:
    """Prefix containment, not folder existence — tags describe subject matter, not the tree."""
    make_topic(kb, "Cooking/sub-topics/Grilling", "topic.cooking.grilling")
    path = "Cooking/sub-topics/Grilling/notes/x.md"
    text = note_at(
        path,
        topic='"Grilling"',
        tags=tag_block("topic.cooking.grilling.charcoal", "type.note"),
    )
    assert validate_content(kb, path, text) == []


# --------------------------------------------------------------------------------------
# Path shape (VA-17 … VA-20, VA-25, VA-27, VA-37, VA-38)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel_path",
    [
        pytest.param("Cooking/notes/steak/index.md", id="va17-notes-item"),
        pytest.param("Cooking/recipes/ribeye/index.md", id="va17-extension-item"),
        pytest.param("Cooking/media/index.md", id="va17-media"),
        pytest.param("Cooking/media/deep/index.md", id="va17-media-nested"),
        pytest.param("Cooking/sub-topics/index.md", id="va17-sub-topics"),
        pytest.param("Cooking/sub-topics/NotATopic/index.md", id="va17-sub-topics-nested"),
        pytest.param("Cooking/skills/index.md", id="va17-skills"),
        pytest.param("Misc/index.md", id="va17-outside-every-topic"),
    ],
)
def test_an_item_may_never_be_named_index_va17(kb: Path, rel_path: str) -> None:
    """VA-17 is "at any depth", and PA-12 fixes the set: derived by name, generated by nobody.

    `media/`, `sub-topics/` and `skills/` host no *items*, which is why the rule used to be gated on
    the item-hosting-section notion — but the content parked there is just as invisible: exempt from
    the authored schema (VA-5), on Layer 2's deny list (PA-11), rendered into no index (GE-15), and
    maintained by no generator. Nothing else in Layer 1 can see it, so VA-17 has to.
    """
    finding = only(validate_content(kb, rel_path, note_at(rel_path)), "ITEM_NAMED_INDEX")
    assert finding.severity is Severity.ERROR
    assert finding.rule_id == "VA-17"


def test_a_generated_index_is_never_an_item_va17_pa12(kb: Path) -> None:
    """The other half of PA-12: the three paths a generator does write stay clean."""
    make_topic(kb, "Cooking/sub-topics/Grilling", "topic.cooking.grilling")
    for rel_path in ("index.md", "Cooking/index.md", "Cooking/sub-topics/Grilling/index.md"):
        assert validate_content(kb, rel_path, GENERATED_INDEX) == [], rel_path


def test_every_stale_index_in_the_tree_is_flagged_once_va17_pa12(kb: Path) -> None:
    """PA-12: never written and never deleted by Layer 1 — so the finding is the only signal."""
    squatters = (
        "Cooking/media/index.md",
        "Cooking/notes/steak/index.md",
        "Cooking/sub-topics/index.md",
    )
    for rel_path in squatters:
        write(kb, rel_path, "# hand written\n")
    findings = validate_tree(kb)
    assert Counter(f.path for f in findings if f.code == "ITEM_NAMED_INDEX") == Counter(squatters)
    for rel_path in squatters:
        assert (kb / rel_path).read_text(encoding="utf-8") == "# hand written\n"


def test_summary_is_not_an_item_name_va18(kb: Path) -> None:
    path = "Cooking/notes/summary/summary.md"
    finding = only(validate_content(kb, path, note_at(path)), "RESERVED_NAME_AS_ITEM")
    assert finding.rule_id == "VA-18"


def test_expert_md_is_valid_only_at_a_topic_root_va20(kb: Path) -> None:
    findings = validate_content(kb, "Cooking/notes/expert.md", "# Expert\n")
    assert codes(findings) == ["MISPLACED_RESERVED_FILE"]
    assert validate_content(kb, "Cooking/expert.md", "# Expert\n") == []


def test_a_standalone_reference_warns_va25(kb: Path) -> None:
    path = "Cooking/references/grill-basics.md"
    text = note_at(
        path,
        source_type="reference",
        tags=tag_block("topic.cooking", "type.reference"),
    )
    finding = only(validate_content(kb, path, text), "REFERENCE_NOT_FOLDER_HOSTED")
    assert finding.severity is Severity.WARNING


def test_a_folder_hosted_reference_is_clean_va25(kb: Path) -> None:
    path = "Cooking/references/grill-basics/grill-basics.md"
    text = note_at(
        path,
        source_type="reference",
        tags=tag_block("topic.cooking", "type.reference"),
    )
    assert validate_content(kb, path, text) == []


def test_a_topic_level_tags_file_is_reserved_va27(kb: Path) -> None:
    findings = validate_content(kb, "Cooking/tags.md", "# Tags\n")
    assert codes(findings) == ["RESERVED_TOPIC_TAGS_FILE"]


def test_a_topic_level_tags_file_is_never_regenerated_or_deleted_va27(kb: Path) -> None:
    write(kb, "Cooking/tags.md", "# hand written\n")
    assert "RESERVED_TOPIC_TAGS_FILE" in codes(validate_tree(kb))
    assert (kb / "Cooking/tags.md").read_text() == "# hand written\n"


def test_an_over_deep_topic_warns_rather_than_blocking_va37(kb: Path) -> None:
    deep = "Cooking/sub-topics/Grilling/sub-topics/Charcoal/sub-topics/Briquettes"
    make_topic(kb, "Cooking/sub-topics/Grilling", "topic.cooking.grilling")
    make_topic(
        kb, "Cooking/sub-topics/Grilling/sub-topics/Charcoal", "topic.cooking.grilling.charcoal"
    )
    make_topic(kb, deep, "topic.cooking.grilling.charcoal.briquettes")
    findings = [f for f in validate_tree(kb) if f.code == "TOPIC_PATH_EXCEEDS_TAG_DEPTH"]
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].path == f"{deep}/topic.md"


def test_a_loose_file_at_a_topic_root_warns_va38(kb: Path) -> None:
    finding = only(
        validate_content(kb, "Cooking/scratch.md", note_at("Cooking/scratch.md")),
        "UNEXPECTED_TOPIC_ROOT_FILE",
    )
    assert finding.severity is Severity.WARNING


def test_a_loose_asset_at_a_topic_root_warns_too_va38(kb: Path) -> None:
    """VA-38's contrast is file-vs-directory, not markdown-vs-asset.

    VA-7 and FM-14 exempt a non-markdown file from *frontmatter* validation; VA-38 is a path rule.
    Nothing else in Layer 1 can see this file — it is excluded from every index (GE-15) and from
    orphan analysis (MA-8 scopes `ORPHAN_ASSET` to `media/` and reference folders) — so without
    VA-38 it is completely invisible.
    """
    (kb / "Cooking/photo.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    finding = only(
        [f for f in validate_tree(kb) if f.path == "Cooking/photo.jpg"],
        "UNEXPECTED_TOPIC_ROOT_FILE",
    )
    assert finding.severity is Severity.WARNING
    assert validate_content(kb, "Cooking/photo.jpg", "") == [finding]


# --------------------------------------------------------------------------------------
# Field semantics (VA-26, VA-28 … VA-35)
# --------------------------------------------------------------------------------------


def test_a_multiline_description_is_rejected_va26(kb: Path) -> None:
    text = note_at(NOTE_PATH, description='"first line\\nsecond line"')
    finding = only(validate_content(kb, NOTE_PATH, text), "MULTILINE_DESCRIPTION")
    assert finding.severity is Severity.ERROR


def test_a_derived_source_type_on_an_authored_file_va31(kb: Path) -> None:
    text = note_at(
        NOTE_PATH,
        source_type="tag-registry",
        tags=tag_block("topic.cooking", "type.note", "status.approved"),
    )
    assert (
        only(validate_content(kb, NOTE_PATH, text), "RESERVED_SOURCE_TYPE").value == "tag-registry"
    )


def test_an_unrecognized_source_type_is_reported_fm6(kb: Path) -> None:
    text = note_at(NOTE_PATH, source_type="recipe")
    finding = only(validate_content(kb, NOTE_PATH, text), "UNKNOWN_SOURCE_TYPE")
    assert "note" in (finding.hint or "") and "solution" in (finding.hint or "")


def test_an_unknown_key_warns_and_is_not_a_missing_field_va32(kb: Path) -> None:
    text = note_at(NOTE_PATH, description=None, descripton='"typo"')
    found = validate_content(kb, NOTE_PATH, text)
    assert only(found, "UNKNOWN_FIELD").field == "descripton"
    assert only(found, "MISSING_REQUIRED_FIELD").field == "description"
    assert only(found, "UNKNOWN_FIELD").severity is Severity.WARNING


def test_a_prefixed_related_topic_warns_va33(kb: Path) -> None:
    text = note_at(NOTE_PATH, related_topics="[ topic.bbq ]")
    finding = only(validate_content(kb, NOTE_PATH, text), "RELATED_TOPICS_PREFIXED")
    assert finding.severity is Severity.WARNING
    assert finding.value == "topic.bbq"


def test_a_dangling_related_topic_warns_va34(kb: Path) -> None:
    text = note_at(NOTE_PATH, related_topics="[ atlantis ]")
    finding = only(validate_content(kb, NOTE_PATH, text), "DANGLING_RELATED_TOPIC")
    assert finding.severity is Severity.WARNING
    assert "topic.atlantis" in finding.message


def test_an_existing_related_topic_is_clean_va34(kb: Path) -> None:
    assert validate_content(kb, NOTE_PATH, note_at(NOTE_PATH, related_topics="[ bbq ]")) == []


def test_a_filename_diverging_from_its_title_warns_va35(kb: Path) -> None:
    path = "Cooking/notes/wg.md"
    text = md(title='"Grill Performance in Windy Conditions"')
    finding = only(validate_content(kb, path, text), "FILENAME_TITLE_DIVERGENCE")
    assert finding.severity is Severity.WARNING
    assert "grill-performance-in-windy-conditions" in (finding.hint or "")


def test_a_breadth_file_never_diverges_from_its_title_va35(kb: Path) -> None:
    assert "FILENAME_TITLE_DIVERGENCE" not in codes(validate_tree(kb))


# --------------------------------------------------------------------------------------
# Cross-file rules (VA-16, VA-21 … VA-23, VA-36, PA-1, PA-14, PA-17, SC-1)
# --------------------------------------------------------------------------------------


def test_a_clean_knowledge_base_yields_no_findings_va1(kb: Path) -> None:
    assert validate_tree(kb) == []


def test_a_folder_hosted_item_needs_its_main_file_va16(kb: Path) -> None:
    """VA-16, T-2: "give every item inside its own folder a main file named after it" — a folder
    whose main file's stem diverges from the folder name is ``MISSING_MAIN_FILE``."""
    write(kb, "Cooking/notes/steak-sear/note.md", note_at("Cooking/notes/steak-sear/note.md"))
    finding = only(validate_tree(kb), "MISSING_MAIN_FILE")
    assert finding.value == "steak-sear.md"
    assert finding.rule_id == "VA-16"
    assert finding.path == "Cooking/notes/steak-sear"


@pytest.mark.parametrize(
    ("folder", "stray"),
    [
        pytest.param("references", "references/grill-basics/scan.md", id="va16-references"),
    ],
)
def test_the_main_file_rule_covers_every_item_section_va16(
    kb: Path, folder: str, stray: str
) -> None:
    """`notes/`, `references/` and every extension folder host items the same way (VA-16)."""
    write(kb, f"Cooking/{stray}", note_at(f"Cooking/{stray}"))
    item = PurePosixPath(stray).parent.name
    finding = only(validate_tree(kb), "MISSING_MAIN_FILE")
    assert finding.value == f"{item}.md"
    assert finding.path == f"Cooking/{folder}/{item}"


def test_skill_folders_are_exempt_from_the_main_file_rule_va16(kb: Path) -> None:
    write(kb, "skills/voice/SKILL.md", SKILL)
    write(kb, "Cooking/skills/voice/SKILL.md", SKILL)
    assert "MISSING_MAIN_FILE" not in codes(validate_tree(kb))


def test_the_main_file_name_is_compared_case_exactly_pa17(kb: Path) -> None:
    write(kb, "Cooking/notes/Steak/steak.md", note_at("Cooking/notes/Steak/steak.md"))
    finding = only(validate_tree(kb), "MAIN_FILE_CASE_MISMATCH")
    assert finding.value == "steak.md"
    assert finding.rule_id == "PA-17"


def test_a_note_is_standalone_or_folder_hosted_never_both_va21(kb: Path) -> None:
    write(kb, "Cooking/notes/steak.md", note_at("Cooking/notes/steak.md"))
    write(kb, "Cooking/notes/steak/steak.md", note_at("Cooking/notes/steak/steak.md"))
    finding = only(validate_tree(kb), "DUPLICATE_NOTE_IDENTITY")
    assert finding.severity is Severity.ERROR
    assert finding.path == "Cooking/notes/steak.md"


def test_a_note_folder_is_never_text_free_va22(kb: Path) -> None:
    (kb / "Cooking/notes/trip/media").mkdir(parents=True)
    (kb / "Cooking/notes/trip/media/a.png").write_bytes(b"\x89PNG")
    finding = only(validate_tree(kb), "MISSING_MAIN_FILE")
    assert finding.rule_id == "VA-22"
    assert finding.value == "trip.md"


def test_media_beside_note_text_warns_va23(kb: Path) -> None:
    """VA-23, T-2: "media for a folder-hosted note stays inside the note's own ``media/``
    subfolder" — a sibling outside it is ``MEDIA_OUTSIDE_MEDIA_FOLDER``."""
    write(
        kb,
        "Cooking/notes/steak-sear/steak-sear.md",
        note_at("Cooking/notes/steak-sear/steak-sear.md"),
    )
    (kb / "Cooking/notes/steak-sear/pan.jpg").write_bytes(b"\xff\xd8")
    finding = only(validate_tree(kb), "MEDIA_OUTSIDE_MEDIA_FOLDER")
    assert finding.severity is Severity.WARNING
    assert finding.path == "Cooking/notes/steak-sear/pan.jpg"


def test_media_inside_the_media_folder_is_clean_va23(kb: Path) -> None:
    write(
        kb,
        "Cooking/notes/steak-sear/steak-sear.md",
        note_at("Cooking/notes/steak-sear/steak-sear.md"),
    )
    (kb / "Cooking/notes/steak-sear/media").mkdir()
    (kb / "Cooking/notes/steak-sear/media/pan.jpg").write_bytes(b"\xff\xd8")
    assert validate_tree(kb) == []


def test_a_topic_root_outside_sub_topics_warns_but_stays_visible_va36(kb: Path) -> None:
    make_topic(kb, "Cooking/notes/Grilling", "topic.cooking.grilling")
    findings = validate_tree(kb)
    finding = only(findings, "MISPLACED_TOPIC_ROOT")
    assert finding.severity is Severity.WARNING
    assert finding.path == "Cooking/notes/Grilling/topic.md"
    assert "MISSING_MAIN_FILE" not in codes(findings)


def test_a_sub_topic_reached_through_sub_topics_is_clean_va36(kb: Path) -> None:
    make_topic(kb, "Cooking/sub-topics/Grilling", "topic.cooking.grilling")
    assert validate_tree(kb) == []


def test_an_unexpected_root_entry_warns_pa1(kb: Path) -> None:
    write(kb, "Cooking.md", "# stray\n")
    finding = only(validate_tree(kb), "UNEXPECTED_ROOT_ENTRY")
    assert finding.severity is Severity.WARNING
    assert finding.path == "Cooking.md"


def test_ignored_entries_never_reach_a_finding_pa16(kb: Path) -> None:
    """`.DS_Store` and friends must not surface as unexpected entries or orphans (PA-16)."""
    (kb / ".DS_Store").write_bytes(b"\x00")
    (kb / ".obsidian").mkdir()
    (kb / ".obsidian/workspace.json").write_text("{}")
    (kb / "Cooking/__pycache__").mkdir()
    assert validate_tree(kb) == []


def test_a_flat_skill_file_is_reported_as_legacy_pa14(kb: Path) -> None:
    write(kb, "skills/voice.md", "---\nname: voice\ndescription: d\n---\n")
    finding = only(validate_tree(kb), "LEGACY_SKILL_LAYOUT")
    assert finding.severity is Severity.WARNING
    assert "skills/voice/SKILL.md" in (finding.hint or "")


def test_a_topic_without_its_breadth_summaries_is_reported_sc1(kb: Path) -> None:
    (kb / "Cooking/notes/summary.md").unlink()
    finding = only(validate_tree(kb), "MISSING_REQUIRED_FILE")
    assert finding.path == "Cooking/notes/summary.md"
    assert finding.severity is Severity.WARNING


# --------------------------------------------------------------------------------------
# validate_file and the single walk (VA-1, CX-5, decision C)
# --------------------------------------------------------------------------------------


def degraded(kb: Path) -> Path:
    """Every defect the *walk* can see, one of each, on top of the clean two-topic fixture.

    One tree per producer-collision: a stray root entry (PA-1), an unparseable note (VA-39), an
    undecodable one (MA-14), a topic root smuggled into `notes/` and one into an extension folder
    (VA-36's two discoverable routes), a `topic.md` under `media/` that PA-5 does *not* discover,
    and a topic folder whose every segment slugifies away (PA-8).
    """
    write(kb, "Cooking.md", "# stray\n")
    write(kb, "Cooking/notes/bad.md", "---\ntitle: [unclosed\n---\nbody\n")
    (kb / "Cooking/notes/undecodable.md").write_bytes(b"---\ntitle: \xff\xfe\n---\nbody\n")
    make_topic(kb, "Cooking/notes/Grilling", "topic.cooking.grilling")
    make_topic(kb, "Cooking/recipes/Ghost", "topic.cooking.ghost")
    make_topic(kb, "Cooking/media/Undiscovered", "topic.cooking.undiscovered")
    make_topic(kb, "!!!", "topic.unnameable")
    return kb


def test_validate_file_agrees_with_validate_content_va1(kb: Path) -> None:
    """Both entry points run the same per-file rules; only the source of the parse differs."""
    text = note_at("Cooking/notes/x.md", topic='"BBQ"', related_topics="[ atlantis ]")
    write(kb, "Cooking/notes/x.md", text)
    record = scan(kb).files["Cooking/notes/x.md"]
    from_record = validate_file(kb, record)
    assert codes(from_record) == codes(validate_content(kb, "Cooking/notes/x.md", text))
    assert set(codes(from_record)) == {"TOPIC_LOCATION_MISMATCH", "DANGLING_RELATED_TOPIC"}


def test_validate_tree_agrees_with_and_without_a_snapshot_va1(kb: Path) -> None:
    """Decision C: one walk, so one verdict — whoever supplied the view (F9, F22).

    This test was vacuous before: it compared `build_snapshot(kb)` with `build_snapshot(kb)`, a
    walk against itself. `validate_tree(kb)` and `validate_tree(kb, scan(kb))` returned different
    findings for the same tree, and the tree they disagreed about is the one below.
    """
    degraded(kb)
    assert validate_tree(kb) == validate_tree(kb, scan(kb))


def capture_walk(monkeypatch: pytest.MonkeyPatch) -> list[KbSnapshot]:
    """Every snapshot `validate_tree` builds for itself, in call order — appended as it goes."""
    seen: list[KbSnapshot] = []
    walk = validation.scan

    def spy(root: Path) -> KbSnapshot:
        snapshot = walk(root)
        seen.append(snapshot)
        return snapshot

    monkeypatch.setattr(validation, "scan", spy)
    return seen


def test_the_default_view_is_the_one_walk_decision_c(
    kb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`validate_tree` walks exactly once, through `pkb.core.scan.scan` — it owns no walker."""
    seen = capture_walk(monkeypatch)
    validate_tree(degraded(kb))
    assert len(seen) == 1
    assert seen[0].root == kb


def test_the_walk_behind_validate_tree_keeps_crlf_byte_exact_ma7(
    kb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second reader is a second answer: `Path.read_text` would translate the line endings (F25).

    The body is what `maintenance.find_broken_links` scans, offset from the end of the file. A
    normalized body no longer matches the tail of the CRLF bytes on disk, the scan window silently
    widens to the whole file, and a markdown link sitting in the *frontmatter* is reported broken.
    """
    (kb / "Cooking/notes/crlf.md").write_bytes(
        b'---\r\ntitle: "Crlf"\r\ndescription: "see [the guide](nowhere.md)"\r\n'
        b'topic: "Cooking"\r\ntags:\r\n  - topic.cooking\r\n  - type.note\r\n'
        b"  - status.approved\r\ncreated: 2024-10-15\r\nupdated: 2024-10-16\r\n"
        b"source_type: note\r\n---\r\n\r\nline one\r\n"
    )
    seen = capture_walk(monkeypatch)
    validate_tree(kb)

    document = seen[0].files["Cooking/notes/crlf.md"].doc
    assert document is not None
    assert document.body == "\r\nline one\r\n"
    assert find_broken_links(kb, seen[0]) == []


def test_the_rules_own_the_codes_they_re_derive_cx5(kb: Path) -> None:
    """Which producer wins, asserted on the finding itself and not just on the count.

    The message is the one `pkb.core.diagnostics` builds, which is also what a bare `flush` emits —
    that identity is the point (CX-6): an agent must not be able to tell which entry point noticed.
    """
    findings = validate_tree(degraded(kb), scan(kb))
    parse_error = only(
        [f for f in findings if f.path == "Cooking/notes/bad.md"], "FRONTMATTER_PARSE_ERROR"
    )
    assert parse_error == diagnostics.frontmatter_parse_error(
        "Cooking/notes/bad.md", parse_error.message.split(": ", 1)[1], parse_error.line
    )
    assert parse_error.line is not None
    misplaced = only(
        [f for f in findings if f.code == "MISPLACED_TOPIC_ROOT" and "Grilling" in (f.path or "")],
        "MISPLACED_TOPIC_ROOT",
    )
    assert misplaced.path == "Cooking/notes/Grilling/topic.md"  # VA-36's own worked example


def test_a_finding_only_the_walk_can_see_survives_cx5(kb: Path) -> None:
    """The filter drops duplicates, never a code the rule functions cannot re-derive.

    `UNADDRESSABLE_TOPIC_ROOT` (PA-8) and `UNREADABLE_FILE` (MA-14) are answers only the walk has:
    the first needs the topic set, the second needs the bytes. The private walk used to *drop* the
    unaddressable topic entirely, which invented a false `UNEXPECTED_ROOT_ENTRY` for a directory
    holding `topic.md` — a topic root by PA-3 (F26e).
    """
    findings = validate_tree(degraded(kb))
    assert only(findings, "UNADDRESSABLE_TOPIC_ROOT").path == "!!!"
    assert "!!!" not in [f.path for f in findings if f.code == "UNEXPECTED_ROOT_ENTRY"]
    unreadable = only(findings, "UNREADABLE_FILE")
    assert unreadable.path == "Cooking/notes/undecodable.md"
    assert [f.code for f in findings if f.path == unreadable.path] == ["UNREADABLE_FILE"]


def test_a_topic_md_validates_before_its_topic_exists_va1_sc3(kb: Path) -> None:
    """The scaffolder's placeholder must pass the gate *before* it is written (VA-1, SC-3).

    `topic.md` is what makes a directory a topic root, so the gate has to treat the proposed write
    as already applied — otherwise no topic could ever be created through it.
    """
    for path, tag in (
        ("Physics/topic.md", "topic.physics"),
        ("Cooking/sub-topics/Grilling/topic.md", "topic.cooking.grilling"),
    ):
        name = PurePosixPath(path).parent.name
        assert errors(validate_content(kb, path, topic_md(name, tag))) == [], path


def test_a_path_outside_the_knowledge_base_is_a_caller_bug_va1(kb: Path) -> None:
    with pytest.raises(ValueError, match="relative"):
        validate_content(kb, "../escape.md", "x")
