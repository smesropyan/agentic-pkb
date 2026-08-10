"""Frontmatter rules FM-1 … FM-15 of the Layer 1 rules document.

Every test name ends in the rule id it covers, so ``grep -rn fm11 tests/`` finds the evidence for
a rule. ``README_BLOCKS`` below is copied verbatim out of ``README.md`` and one test asserts it is
still a byte-for-byte substring of it, so a README edit fails here rather than drifting away from
the implementation.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from pkb.core import frontmatter as fm
from pkb.core.models import Metadata

REPO_ROOT = Path(__file__).resolve().parents[2]

# --------------------------------------------------------------------------------------
# Golden frontmatter blocks. `README_BLOCKS` below is the set asserted verbatim against
# README.md; the rest exercise fields Layer 1 still defines.
# --------------------------------------------------------------------------------------

README_1_4 = """\
---
title: "Grill Performance in Windy Conditions"
description: "How wind affects grill temperature and how to compensate for it"
topic: "Cooking"
tags:
  - topic.cooking.grilling
  - topic.cooking.heat-management
  - type.note
  - status.approved
created: 2024-10-15
updated: 2024-10-16
related_topics: [ bbq, weather ]
source_type: note  # note, reference, solution, summary
---
"""

CONFLICT_TAGGED_NOTE = """\
---
title: "Preheat the grill"
description: "How long to preheat the grill before cooking"
topic: "Cooking"
tags:
  - topic.cooking.grilling
  - topic.cooking.heat-management
  - type.note
  - status.conflict-review
created: 2024-10-15
updated: 2024-12-16
related_topics: [ bbq.equipment ]
source_type: note
review_note: "Reference 'Grill Basics' says preheat for 10 min. Note says 15 min."
---
"""

REVIEWED_NOTE = """\
---
title: "Preheat the grill"
description: "How long to preheat the grill before cooking"
topic: "Cooking"
tags:
  - topic.cooking.grilling
  - topic.cooking.heat-management
  - type.note
  - status.approved
created: 2024-10-15
updated: 2024-12-17
related_topics: [ bbq.equipment ]
source_type: note
last_reviewed: 2024-12-17
---
"""

README_BLOCKS = {
    "readme_1_4": README_1_4,
}

# FM-9 lets the serializer drop inline comments, so §1.4 is the one block whose canonical form
# differs from its source — by exactly that comment.
README_1_4_CANONICAL = README_1_4.replace(
    "source_type: note  # note, reference, solution, summary", "source_type: note"
)

ROUND_TRIP_BLOCKS = {
    "readme_1_4": (README_1_4, README_1_4_CANONICAL),
}

MINIMAL_NOTE = """\
---
title: "A note"
description: "One line"
topic: "Cooking"
tags:
  - topic.cooking
  - type.note
  - status.draft
created: 2024-10-15
updated: 2024-10-16
source_type: note
---
Body text.
"""


def _meta(text: str) -> Metadata:
    """Parse and assert the block was well-formed — the precondition of most tests here."""
    doc = fm.parse(text)
    assert doc.error is None, doc.error
    assert doc.meta is not None
    return doc.meta


def _round_trip(text: str) -> str:
    doc = fm.parse(text)
    assert doc.meta is not None
    return fm.serialize(doc.meta, doc.body, extra=fm.unknown_values(doc))


# --------------------------------------------------------------------------------------
# FM-1 — the fence
# --------------------------------------------------------------------------------------


def test_parse_splits_at_the_fences_fm1() -> None:
    doc = fm.parse('---\ntitle: "X"\n---\nbody\n')
    assert doc.meta is not None
    assert doc.meta.title == "X"
    assert doc.body == "body\n"
    assert doc.has_frontmatter


def test_parse_without_a_leading_fence_returns_the_whole_file_as_body_fm1() -> None:
    text = "Intro paragraph\n---\ntitle: X\n---\n"
    doc = fm.parse(text)
    assert doc.meta is None
    assert doc.raw is None
    assert doc.body == text
    assert not doc.has_frontmatter


@pytest.mark.parametrize(
    "first_line",
    ["----", "--", " ---", "--- ", "---x", ""],
    ids=[
        "four_dashes_fm1",
        "two_dashes_fm1",
        "indented_fm1",
        "trailing_space_fm1",
        "suffix_fm1",
        "empty_fm1",
    ],
)
def test_only_an_exact_fence_opens_a_block_fm1(first_line: str) -> None:
    doc = fm.parse(f"{first_line}\ntitle: X\n---\n")
    assert doc.meta is None
    assert not doc.has_frontmatter


def test_body_is_preserved_byte_exactly_fm1() -> None:
    body = "# Heading\n\n\ttabbed\ntrailing spaces   \n\r\nlast line without newline"
    doc = fm.parse(f'---\ntitle: "X"\n---\n{body}')
    assert doc.body == body


def test_body_starting_with_a_fence_is_not_reconsumed_fm1() -> None:
    doc = fm.parse('---\ntitle: "X"\n---\n---\nnot frontmatter\n')
    assert doc.body == "---\nnot frontmatter\n"


# --------------------------------------------------------------------------------------
# FM-2 / FM-3 — the field sets
# --------------------------------------------------------------------------------------


def test_required_fields_are_exactly_seven_fm2() -> None:
    assert sorted(fm.REQUIRED_FIELDS) == [
        "created",
        "description",
        "source_type",
        "tags",
        "title",
        "topic",
        "updated",
    ]
    assert len(fm.REQUIRED_FIELDS) == 7


def test_optional_fields_are_never_required_fm3() -> None:
    assert sorted(fm.OPTIONAL_FIELDS) == ["last_reviewed", "related_topics", "review_note"]
    assert not (fm.REQUIRED_FIELDS & fm.OPTIONAL_FIELDS)
    assert fm.KNOWN_FIELDS == fm.REQUIRED_FIELDS | fm.OPTIONAL_FIELDS
    assert set(fm.CANONICAL_ORDER) == fm.KNOWN_FIELDS

    meta = _meta(MINIMAL_NOTE)
    assert meta.related_topics == ()
    assert meta.review_note is None
    assert meta.last_reviewed is None
    assert meta.bad_fields == ()
    assert meta.unknown_fields == ()


# --------------------------------------------------------------------------------------
# FM-4 — field types
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "field", "code"),
    [
        ('tags: "topic.cooking"', "tags", "FIELD_TYPE"),
        ("title: 123", "title", "FIELD_TYPE"),
        ("description:\n  - a\n  - b", "description", "FIELD_TYPE"),
        ("tags:\n  - 12", "tags", "FIELD_TYPE"),
        ("related_topics: bbq", "related_topics", "FIELD_TYPE"),
        ("topic: true", "topic", "FIELD_TYPE"),
        ('review_note: ""', "review_note", "EMPTY_FIELD"),
        ("tags: []", "tags", "EMPTY_FIELD"),
        ('title: "   "', "title", "EMPTY_FIELD"),
    ],
    ids=[
        "scalar_tags_fm4",
        "numeric_title_fm4",
        "list_description_fm4",
        "non_string_tag_item_fm4",
        "scalar_related_topics_fm4",
        "boolean_topic_fm4",
        "empty_review_note_fm4",
        "empty_tag_list_fm4",
        "blank_title_fm4",
    ],
)
def test_wrong_typed_field_becomes_a_problem_not_an_exception_fm4(
    line: str, field: str, code: str
) -> None:
    meta = _meta(f"---\n{line}\n---\n")
    assert [(p.field, p.code) for p in meta.bad_fields] == [(field, code)]
    # The typed view never guesses: it stays empty and validation reports from `bad_fields`.
    assert getattr(meta, field) in (None, ())
    assert field in meta.present_keys


def test_well_typed_fields_produce_no_problems_fm4() -> None:
    meta = _meta(CONFLICT_TAGGED_NOTE)
    assert meta.bad_fields == ()
    assert meta.title == "Preheat the grill"
    assert meta.tags == (
        "topic.cooking.grilling",
        "topic.cooking.heat-management",
        "type.note",
        "status.conflict-review",
    )
    assert meta.related_topics == ("bbq.equipment",)
    assert meta.review_note == "Reference 'Grill Basics' says preheat for 10 min. Note says 15 min."


# --------------------------------------------------------------------------------------
# FM-5 — dates
# --------------------------------------------------------------------------------------


def test_all_three_date_fields_parse_to_date_fm5() -> None:
    meta = _meta("---\ncreated: 2024-10-15\nupdated: 2024-12-16\nlast_reviewed: 2024-12-17\n---\n")
    assert meta.created == date(2024, 10, 15)
    assert meta.updated == date(2024, 12, 16)
    assert meta.last_reviewed == date(2024, 12, 17)
    assert meta.bad_fields == ()


def test_quoted_iso_date_string_is_accepted_fm5() -> None:
    assert _meta('---\ncreated: "2024-10-15"\n---\n').created == date(2024, 10, 15)


@pytest.mark.parametrize(
    "value",
    [
        "2024-10-15T09:00:00Z",
        "2024-1-5",
        "15/10/2024",
        "last tuesday",
        '"2024-13-45"',
        "2024",
        "2024-10-15 09:00:00",
    ],
    ids=[
        "datetime_fm5",
        "unpadded_fm5",
        "slashes_fm5",
        "prose_fm5",
        "impossible_fm5",
        "year_only_fm5",
        "space_separated_fm5",
    ],
)
def test_non_calendar_dates_are_date_format_problems_fm5(value: str) -> None:
    meta = _meta(f"---\ncreated: {value}\n---\n")
    assert meta.created is None
    assert [(p.field, p.code) for p in meta.bad_fields] == [("created", "DATE_FORMAT")]


def test_serializer_emits_unquoted_iso_dates_fm5() -> None:
    text = fm.serialize(Metadata(created=date(2024, 10, 15)), "")
    assert text == "---\ncreated: 2024-10-15\n---\n"


# --------------------------------------------------------------------------------------
# FM-6 — source_type
# --------------------------------------------------------------------------------------


def test_source_type_sets_are_closed_and_disjoint_fm6() -> None:
    # Decision A of the rules document keeps the authored enum at README §1.4's four values;
    # `topic.md` carries `source_type: summary` rather than a fifth member.
    assert sorted(fm.AUTHORED_SOURCE_TYPES) == ["note", "reference", "solution", "summary"]
    assert sorted(fm.DERIVED_SOURCE_TYPES) == ["catalog", "index", "tag-registry"]
    assert not (fm.AUTHORED_SOURCE_TYPES & fm.DERIVED_SOURCE_TYPES)
    assert fm.SOURCE_TYPES == fm.AUTHORED_SOURCE_TYPES | fm.DERIVED_SOURCE_TYPES


def test_unknown_source_type_survives_parsing_for_validation_fm6() -> None:
    # Membership is a validation finding (UNKNOWN_SOURCE_TYPE), which needs the offending value.
    meta = _meta("---\nsource_type: recipe\n---\n")
    assert meta.source_type == "recipe"
    assert meta.source_type not in fm.SOURCE_TYPES
    assert meta.bad_fields == ()


# --------------------------------------------------------------------------------------
# FM-7 / FM-8 — canonical serialization
# --------------------------------------------------------------------------------------


def test_serialize_uses_the_canonical_key_order_fm7() -> None:
    # Constructed in a deliberately scrambled order: the output order comes from CANONICAL_ORDER.
    meta = Metadata(
        review_note="Reference 'Grill Basics' says preheat for 10 min. Note says 15 min.",
        source_type="note",
        related_topics=("bbq.equipment",),
        updated=date(2024, 12, 16),
        created=date(2024, 10, 15),
        tags=(
            "topic.cooking.grilling",
            "topic.cooking.heat-management",
            "type.note",
            "status.conflict-review",
        ),
        topic="Cooking",
        description="How long to preheat the grill before cooking",
        title="Preheat the grill",
    )
    assert fm.serialize(meta, "") == CONFLICT_TAGGED_NOTE


def test_unknown_keys_serialize_after_the_known_ones_fm7() -> None:
    text = fm.serialize(Metadata(title="X", source_type="note"), "", extra={"servings": 4})
    assert text == '---\ntitle: "X"\nsource_type: note\nservings: 4\n---\n'


def test_golden_blocks_are_verbatim_from_the_readme_fm8() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for name, block in README_BLOCKS.items():
        assert block in readme, f"{name} has drifted from README.md"


@pytest.mark.parametrize(
    ("source", "expected"),
    list(ROUND_TRIP_BLOCKS.values()),
    ids=[f"{name}_fm8" for name in ROUND_TRIP_BLOCKS],
)
def test_readme_blocks_round_trip_byte_identically_fm8(source: str, expected: str) -> None:
    # §1.4 is the one block whose canonical form differs from the README's — by the inline comment
    # FM-9 explicitly excuses the serializer from reproducing. Every other byte matches.
    assert _round_trip(source) == expected


def test_canonical_style_is_quoted_text_block_tags_and_flow_related_topics_fm8() -> None:
    text = _round_trip(CONFLICT_TAGGED_NOTE)
    assert 'title: "Preheat the grill"' in text
    assert "tags:\n  - topic.cooking.grilling\n" in text
    assert "related_topics: [ bbq.equipment ]\n" in text
    assert "created: 2024-10-15\n" in text
    assert "source_type: note\n" in text


def test_serialize_keeps_the_body_untouched_fm8() -> None:
    body = "# Heading\n\nSome *markdown* with a --- inside.\n"
    text = fm.serialize(Metadata(title="X"), body)
    assert text == f'---\ntitle: "X"\n---\n{body}'
    assert fm.parse(text).body == body


# --------------------------------------------------------------------------------------
# FM-9 — inline comments
# --------------------------------------------------------------------------------------


def test_inline_comments_are_tolerated_and_never_leak_into_values_fm9() -> None:
    meta = _meta(README_1_4)
    assert meta.source_type == "note"
    assert "#" not in (meta.source_type or "")
    assert meta.title == "Grill Performance in Windy Conditions"


def test_surgical_edit_keeps_an_inline_comment_fm9() -> None:
    edited = fm.set_field(README_1_4, "updated", date(2030, 6, 6))
    assert "source_type: note  # note, reference, solution, summary\n" in edited


# --------------------------------------------------------------------------------------
# FM-10 — unknown keys
# --------------------------------------------------------------------------------------


def test_unknown_keys_are_preserved_and_listed_fm10() -> None:
    source = MINIMAL_NOTE.replace("source_type: note\n", "source_type: note\nservings: 4\n")
    doc = fm.parse(source)
    assert doc.meta is not None
    assert doc.meta.unknown_fields == ("servings",)
    assert doc.raw is not None
    assert doc.raw["servings"] == 4
    assert "servings" in doc.meta.present_keys

    rendered = fm.serialize(doc.meta, doc.body, extra=fm.unknown_values(doc))
    assert "servings: 4\n" in rendered
    assert fm.parse(rendered).raw == doc.raw


def test_a_known_key_is_never_reported_as_unknown_fm10() -> None:
    meta = _meta(REVIEWED_NOTE)
    assert meta.unknown_fields == ()
    assert meta.present_keys == (
        "title",
        "description",
        "topic",
        "tags",
        "created",
        "updated",
        "related_topics",
        "source_type",
        "last_reviewed",
    )


# --------------------------------------------------------------------------------------
# FM-11 — surgical writes
# --------------------------------------------------------------------------------------


def test_readme_resolution_edit_reproduces_the_after_block_fm11() -> None:
    body = "The note text, untouched.\n\n- a bullet\n"
    before = CONFLICT_TAGGED_NOTE + body

    edited = fm.remove_field(before, "review_note")
    edited = fm.set_field(edited, "updated", date(2024, 12, 17))
    edited = fm.set_field(
        edited,
        "tags",
        [
            "topic.cooking.grilling",
            "topic.cooking.heat-management",
            "type.note",
            "status.approved",
        ],
    )
    edited = fm.set_field(edited, "last_reviewed", date(2024, 12, 17))

    assert edited == REVIEWED_NOTE + body
    assert fm.parse(edited).body == body


def test_set_field_touches_exactly_one_line_fm11() -> None:
    source = README_1_4 + "body\n"
    edited = fm.set_field(source, "updated", date(2030, 6, 6))
    changed = [
        (before, after)
        for before, after in zip(source.split("\n"), edited.split("\n"), strict=True)
        if before != after
    ]
    assert changed == [("updated: 2024-10-16", "updated: 2030-06-06")]


def test_set_field_preserves_a_humans_layout_fm11() -> None:
    source = (
        "---\n"
        "source_type: note\n"
        "# a comment the human wrote\n"
        "title: 'single quoted'\n"
        "tags: [a, b]\n"
        "updated: 2024-01-01\n"
        "---\n"
        "body\n"
    )
    edited = fm.set_field(source, "updated", date(2030, 6, 6))
    assert edited == source.replace("updated: 2024-01-01", "updated: 2030-06-06")


def test_set_field_keeps_a_zero_indent_sequence_style_fm11() -> None:
    source = "---\ntags:\n- a\n- b\n---\n"
    assert fm.set_field(source, "tags", ["a", "c"]) == "---\ntags:\n- a\n- c\n---\n"


def test_set_field_inserts_a_missing_key_in_canonical_position_fm11() -> None:
    source = '---\ntitle: "X"\nsource_type: note\n---\nbody\n'
    edited = fm.set_field(source, "created", date(2024, 10, 15))
    assert edited == '---\ntitle: "X"\ncreated: 2024-10-15\nsource_type: note\n---\nbody\n'


def test_remove_field_deletes_the_key_rather_than_blanking_it_fm11() -> None:
    edited = fm.remove_field(CONFLICT_TAGGED_NOTE, "review_note")
    assert "review_note" not in edited
    assert edited == CONFLICT_TAGGED_NOTE.replace(
        "review_note: \"Reference 'Grill Basics' says preheat for 10 min. Note says 15 min.\"\n", ""
    )


def test_remove_field_deletes_every_line_of_a_block_value_fm11() -> None:
    edited = fm.remove_field(CONFLICT_TAGGED_NOTE, "tags")
    assert "topic.cooking.grilling" not in edited
    assert 'title: "Preheat the grill"\n' in edited


def test_remove_field_deletes_a_multi_line_flow_value_fm11() -> None:
    # A flow mapping whose continuation lines sit at column zero: `foo: 1,` looks exactly like the
    # next top-level key to a regex, so a line-guessing slicer cuts after the first line and leaves
    # the rest of the value stranded. The boundaries come from the parser instead.
    source = '---\ntitle: "X"\nextra: {\nfoo: 1,\nbar: 2\n}\nupdated: 2024-10-15\n---\nbody\n'
    edited = fm.remove_field(source, "extra")
    assert edited == '---\ntitle: "X"\nupdated: 2024-10-15\n---\nbody\n'
    assert fm.parse(edited).error is None


def test_remove_field_deletes_a_quoted_value_that_looks_like_a_key_fm11() -> None:
    # The silent one: a double-quoted scalar continuing at column zero. Cutting by regex leaves
    # `def: ghi"` behind, which still parses — the title is truncated and a phantom key appears.
    source = '---\ntitle: "abc\ndef: ghi"\nupdated: 2024-10-15\n---\nbody\n'
    assert fm.parse(source).raw == {"title": "abc def: ghi", "updated": date(2024, 10, 15)}
    edited = fm.remove_field(source, "title")
    assert edited == "---\nupdated: 2024-10-15\n---\nbody\n"
    assert fm.parse(edited).raw == {"updated": date(2024, 10, 15)}


def test_surgical_edits_keep_crlf_line_endings_fm11() -> None:
    source = '---\r\ntitle: "X"\r\nreview_note: "n"\r\nupdated: 2024-01-01\r\n---\r\nbody\r\n'
    stamped = fm.set_field(source, "updated", date(2030, 6, 6))
    assert stamped == source.replace("updated: 2024-01-01", "updated: 2030-06-06")
    assert fm.remove_field(stamped, "review_note") == stamped.replace('review_note: "n"\r\n', "")


def test_remove_field_keeps_a_neighbours_comment_fm11() -> None:
    source = "---\ntitle: x\n\n# about the tags\ntags:\n  - a\nupdated: 2024-01-01\n---\n"
    assert fm.remove_field(source, "tags") == (
        "---\ntitle: x\n\n# about the tags\nupdated: 2024-01-01\n---\n"
    )


@pytest.mark.parametrize(
    "source",
    ["no frontmatter here\n", "---\ntitle: x\n", "---\n"],
    ids=["no_block_fm11", "unterminated_fm11", "bare_fence_fm11"],
)
def test_edits_without_a_closed_block_change_nothing_fm11(source: str) -> None:
    # Without version control a speculative rewrite is unrecoverable; do nothing instead.
    assert fm.set_field(source, "updated", date(2030, 6, 6)) == source
    assert fm.remove_field(source, "updated") == source


UNREADABLE_BLOCKS = {
    # A half-written note: the quote is never closed, so the loader rejects the block.
    "half_quoted_fm11_ma5": '---\ntitle: "A note\n---\nbody\n',
    "unclosed_flow_fm11_ma5": "---\ntitle: [unclosed\n---\nbody\n",
    # A markdown file whose *body* opens with a thematic break. The fence is real by FM-1, but
    # what is inside it is a string, not a mapping of fields.
    "thematic_break_fm11_ma5": "---\nChangelog\n---\n\nThe file opens with a break.\n",
    "sequence_fm11_ma5": "---\n- a\n- b\n---\nbody\n",
    # The destructive one: `updated` is the last key a regex can see, so its line range ran to the
    # end of the block and a rewrite replaced the human's line with the rendered pair.
    "trailing_prose_fm11_ma5": (
        "---\nupdated: 2026-08-05\nSteak searing, part 1\n---\n\nReverse sear at 250F.\n"
    ),
}


@pytest.mark.parametrize("source", list(UNREADABLE_BLOCKS.values()), ids=list(UNREADABLE_BLOCKS))
def test_edits_to_a_block_layer_1_cannot_read_change_nothing_fm11_ma5(source: str) -> None:
    """A block ``parse`` rejected is not frontmatter Layer 1 may rewrite (FM-11, FM-13, MA-5).

    Layer 1 flags, it never repairs (§5), and arch D6 leaves no undo: an edit placed by guesswork
    inserts above — or, once the guessed key range runs to the end of the block, deletes — lines
    the human wrote. The file is still reported (``FRONTMATTER_PARSE_ERROR``, VA-39) by the walk.
    """
    assert fm.parse(source).meta is None
    stamped = fm.set_field(source, "updated", date(2030, 6, 6))
    assert stamped == source
    # MA-3: a second flush the same day must be a byte-level no-op too.
    assert fm.set_field(stamped, "updated", date(2030, 6, 6)) == source
    assert fm.remove_field(source, "updated") == source
    assert fm.remove_field(source, "title") == source


UNSLICEABLE_BLOCKS = {
    # A top-level flow mapping: both keys live on one line, so no line belongs to one key.
    "top_level_flow_map_fm11": "---\n{title: X, updated: 2026-08-05}\n---\nbody\n",
    "empty_flow_map_fm11": "---\n{}\n---\nbody\n",
    # A uniformly indented block mapping: rewriting a key at column zero would break it.
    "indented_mapping_fm11": "---\n  title: X\n  updated: 2026-08-05\n---\nbody\n",
    # An explicit key (`? k` / `: v`) owns two lines and starts in neither's column zero.
    "explicit_key_fm11": "---\n? title\n: X\nupdated: 2026-08-05\n---\nbody\n",
    # A merge key: `title` is a real key of the mapping that occupies no line of its own.
    "merge_key_fm11": "---\nbase: &b\n  title: X\n<<: *b\nupdated: 2026-08-05\n---\nbody\n",
}


@pytest.mark.parametrize("source", list(UNSLICEABLE_BLOCKS.values()), ids=list(UNSLICEABLE_BLOCKS))
def test_edits_to_a_block_that_does_not_slice_into_lines_change_nothing_fm11(source: str) -> None:
    """A surgical write rewrites whole lines; when a key owns none, refuse (FM-11).

    These blocks parse, but no line belongs to exactly one top-level key, so there is no edit that
    touches "the target key's lines and nothing else". Refusing keeps the promise; guessing spliced
    a key above a flow mapping and produced YAML that no longer parses.
    """
    assert fm.parse(source).meta is not None
    assert fm.set_field(source, "updated", date(2030, 6, 6)) == source
    assert fm.remove_field(source, "title") == source


def test_an_empty_block_still_gains_a_field_fm11() -> None:
    # The refusal is exactly "cannot read or cannot slice" — an empty block is both readable and
    # trivially sliceable, so MA-3's stamp still lands.
    assert fm.set_field("---\n---\nbody\n", "updated", date(2030, 6, 6)) == (
        "---\nupdated: 2030-06-06\n---\nbody\n"
    )


def test_removing_an_absent_key_changes_nothing_fm11() -> None:
    assert fm.remove_field(README_1_4, "last_reviewed") == README_1_4


# --------------------------------------------------------------------------------------
# FM-12 — derived-file frontmatter
# --------------------------------------------------------------------------------------


def test_generated_tags_md_frontmatter_is_two_keys_fm12() -> None:
    generated = '---\ntitle: "PKB Tag Registry"\nsource_type: tag-registry\n---\n'
    meta = _meta(generated)
    assert meta.present_keys == ("title", "source_type")
    assert fm.serialize(Metadata(title="PKB Tag Registry", source_type="tag-registry"), "") == (
        generated
    )


def test_generated_index_frontmatter_carries_no_date_key_fm12() -> None:
    generated = fm.serialize(
        Metadata(
            title="Cooking — Index",
            description="Canonical index of the Cooking topic",
            topic="Cooking",
            source_type="index",
        ),
        "",
    )
    assert generated == (
        "---\n"
        'title: "Cooking — Index"\n'
        'description: "Canonical index of the Cooking topic"\n'
        'topic: "Cooking"\n'
        "source_type: index\n"
        "---\n"
    )
    meta = _meta(generated)
    forbidden = {"tags", "created", "updated", "related_topics", "review_note", "last_reviewed"}
    assert not forbidden & set(meta.present_keys)


# --------------------------------------------------------------------------------------
# FM-13 — parsing is total
# --------------------------------------------------------------------------------------


def test_malformed_yaml_yields_a_parse_failure_fm13() -> None:
    doc = fm.parse("---\ntitle: [unclosed\n---\nbody\n")
    assert doc.meta is None
    assert doc.raw is None
    assert doc.error
    assert doc.error_line == 3
    assert doc.has_frontmatter


def test_unterminated_block_yields_a_parse_failure_fm13() -> None:
    doc = fm.parse('---\ntitle: "X"\ndescription: "Y"\n')
    assert doc.meta is None
    assert doc.error is not None
    assert "closing" in doc.error
    assert doc.error_line == 1


def test_non_mapping_frontmatter_yields_a_parse_failure_fm13() -> None:
    doc = fm.parse("---\n- a\n- b\n---\nbody\n")
    assert doc.meta is None
    assert doc.error is not None
    assert "mapping" in doc.error


def test_an_impossible_yaml_timestamp_yields_a_parse_failure_fm13() -> None:
    # The loader itself raises a bare ValueError here, not a YAMLError; parse must still be total.
    doc = fm.parse("---\ncreated: 2024-13-45\n---\n")
    assert doc.meta is None
    assert doc.error is not None


def test_duplicate_keys_yield_a_parse_failure_fm13() -> None:
    doc = fm.parse("---\ntitle: a\ntitle: b\n---\n")
    assert doc.meta is None
    assert doc.error is not None


def test_empty_block_parses_clean_and_empty_fm13() -> None:
    doc = fm.parse("---\n---\nbody\n")
    assert doc.error is None
    assert doc.meta == Metadata()
    assert doc.raw == {}
    assert doc.body == "body\n"


@given(st.text())
def test_parse_never_raises_fm13(text: str) -> None:
    doc = fm.parse(text)
    assert isinstance(doc.body, str)
    assert doc.meta is None or doc.error is None


# --------------------------------------------------------------------------------------
# FM-15 — related_topics normalization
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("bbq", "topic.bbq"),
        ("bbq.equipment", "topic.bbq.equipment"),
        ("topic.bbq.equipment", "topic.bbq.equipment"),
        ("  bbq.equipment  ", "topic.bbq.equipment"),
        ("status.draft", "status.draft"),
        ("type.note", "type.note"),
        ("domain.legal.compliance", "domain.legal.compliance"),
        ("", ""),
    ],
    ids=[
        "bare_fm15",
        "dotted_fm15",
        "already_prefixed_fm15",
        "whitespace_fm15",
        "status_namespace_fm15",
        "type_namespace_fm15",
        "domain_namespace_fm15",
        "empty_fm15",
    ],
)
def test_normalize_related_topic_fm15(raw: str, expected: str) -> None:
    assert fm.normalize_related_topic(raw) == expected


@given(st.text())
def test_normalize_related_topic_is_idempotent_fm15(value: str) -> None:
    once = fm.normalize_related_topic(value)
    assert fm.normalize_related_topic(once) == once


# --------------------------------------------------------------------------------------
# Properties over the canonical form (FM-8) and the surgical writer (FM-11)
# --------------------------------------------------------------------------------------

_TEXT = st.text(min_size=1).filter(lambda value: value.strip() != "")
_TAG = st.from_regex(r"\A[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*){0,3}\Z")

_METADATA = st.builds(
    Metadata,
    title=_TEXT,
    description=_TEXT,
    topic=_TEXT,
    tags=st.lists(_TAG, min_size=1, max_size=4).map(tuple),
    created=st.dates(),
    updated=st.dates(),
    related_topics=st.lists(_TAG, max_size=3).map(tuple),
    source_type=st.sampled_from(sorted(fm.AUTHORED_SOURCE_TYPES)),
    review_note=st.one_of(st.none(), _TEXT),
    last_reviewed=st.one_of(st.none(), st.dates()),
)


@given(meta=_METADATA, body=st.text())
def test_serialize_parse_serialize_is_stable_fm8(meta: Metadata, body: str) -> None:
    text = fm.serialize(meta, body)
    doc = fm.parse(text)
    assert doc.error is None, doc.error
    assert doc.meta is not None
    assert doc.body == body
    for key in fm.CANONICAL_ORDER:
        assert getattr(doc.meta, key) == getattr(meta, key), key
    assert fm.serialize(doc.meta, doc.body) == text


@given(meta=_METADATA, body=st.text(), day=st.dates())
def test_set_field_is_idempotent_and_body_safe_fm11(meta: Metadata, body: str, day: date) -> None:
    text = fm.serialize(meta, body)
    once = fm.set_field(text, "updated", day)
    assert fm.set_field(once, "updated", day) == once
    assert fm.parse(once).body == body
    reparsed = fm.parse(once).meta
    assert reparsed is not None
    assert reparsed.updated == day
    assert reparsed.title == meta.title


# Values a human might hand-write that occupy more than one line, or whose continuation lines start
# at column zero — the shapes a line-guessing slicer mis-cuts (FM-11).
_HAND_VALUES = st.sampled_from(
    [
        ' "X"',
        " 'single'",
        " 2024-10-15",
        " [ a, b ]",
        " {\nfoo: 1,\nbar: 2\n}",
        ' "abc\ndef: ghi"',
        ' "abc\n  indented continuation"',
        " |\n  line one\n  line two",
        " >\n  folded\n  text",
        " [\n  a,\n  b,\n]",
        "\n  - a\n  - b",
        "\n- zero-indent\n- sequence",
        " 1  # an inline comment",
    ]
)
_HAND_KEYS = st.sampled_from(["title", "description", "note", "extra", "tags", "updated"])


@st.composite
def _hand_written_block(draw: st.DrawFn) -> str:
    keys = draw(st.lists(_HAND_KEYS, min_size=1, max_size=4, unique=True))
    lines = [f"{key}:{draw(_HAND_VALUES)}" for key in keys]
    return "---\n" + "\n".join(lines) + "\n---\nbody text\n"


@given(text=_hand_written_block(), day=st.dates())
def test_surgical_edits_never_disturb_another_key_fm11(text: str, day: date) -> None:
    """Every key but the target survives a write byte-for-byte in *value*, not just in bytes.

    Asserting only that the result still parses is not enough: mis-slicing a double-quoted scalar
    that continues at column zero yields a document that parses fine, with the value truncated and
    a phantom key in its place (FM-11).
    """
    before = fm.parse(text)
    assume(before.error is None)
    assert before.raw is not None

    for key in before.raw:
        removed = fm.parse(fm.remove_field(text, key))
        assert removed.error is None, (key, removed.error)
        assert removed.raw == {k: v for k, v in before.raw.items() if k != key}
        assert removed.body == before.body

        stamped = fm.parse(fm.set_field(text, key, day))
        assert stamped.error is None, (key, stamped.error)
        assert stamped.raw is not None
        assert stamped.raw[key] == day
        assert {k: v for k, v in stamped.raw.items() if k != key} == {
            k: v for k, v in before.raw.items() if k != key
        }
        assert stamped.body == before.body


# --------------------------------------------------------------------------------------
# FM-4 / FM-13 — the coercion helpers stay total for exotic YAML values
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [None, 3, 3.5, True, datetime(2024, 10, 15, 9, 0)],
    ids=["null_fm4", "int_fm4", "float_fm4", "bool_fm4", "datetime_fm4"],
)
def test_odd_scalar_types_never_crash_the_parser_fm4(value: Any) -> None:
    rendered = "" if value is None else str(value)
    meta = _meta(f"---\ntitle: {rendered}\n---\n")
    assert meta.title is None
    assert len(meta.bad_fields) == 1
    assert meta.bad_fields[0].field == "title"
