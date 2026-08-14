"""Tests for `pkb.core.tags` — one test per TG rule, plus the properties GE-32 asks for.

Snapshots are built by hand here: `scan.py` does not exist yet, and `KbSnapshot` is a plain frozen
dataclass, so a fixture is a dict of `FileRecord`s (decision C).
"""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pkb.core.errors import Severity
from pkb.core.models import (
    FileClass,
    FileRecord,
    FileRole,
    KbSnapshot,
    Metadata,
    ParsedDocument,
)
from pkb.core.tags import (
    MAX_TAG_DEPTH,
    ROOT_TOPIC_ANNOTATION,
    STATIC_ANNOTATIONS,
    TAG_RE,
    TAG_SEGMENT_RE,
    TYPE_DEFINITIONS,
    Namespace,
    Tag,
    TagNode,
    ancestor_closure,
    build_tag_forest,
    build_tag_tree,
    files_with_tag,
    render_definition_list,
    render_mapping_line,
    render_tag_tree,
    validate_tag,
)

# --------------------------------------------------------------------------------------
# Fixture helpers — hand-built snapshots
# --------------------------------------------------------------------------------------


def _record(
    root: Path,
    path: str,
    tags: Sequence[str],
    *,
    file_class: FileClass = FileClass.AUTHORED,
) -> FileRecord:
    """One markdown file carrying `tags`, classified as authored unless told otherwise."""
    meta = Metadata(title="T", description="D", tags=tuple(tags))
    doc = ParsedDocument(body="", raw={"tags": list(tags)}, meta=meta)
    return FileRecord(
        path=path,
        abs_path=root / path,
        role=FileRole.NOTE,
        file_class=file_class,
        topic_path=path.split("/")[0],
        doc=doc,
    )


def _snapshot(root: Path, records: Sequence[FileRecord]) -> KbSnapshot:
    return KbSnapshot(root=root, topics={}, files={r.path: r for r in records})


def _codes(findings: Sequence[Any]) -> list[str]:
    return [f.code for f in findings]


def _parse_tag_tree(lines: Sequence[str]) -> list[str]:
    """Inverse of `render_tag_tree`: the full dotted tags, in the order rendered (GE-32)."""
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip(" ")
        assert stripped.startswith("- `")
        out.append(stripped[3:].split("`", 1)[0])
    return out


# Legal tags: a real namespace plus up to three kebab-case segments (depth <= 4, TG-3/TG-4).
_SEGMENT = st.builds(
    "-".join,
    st.lists(st.text(alphabet="abz019", min_size=1, max_size=3), min_size=1, max_size=2),
)
_LEGAL_TAG = st.builds(
    lambda ns, rest: ".".join([ns, *rest]),
    st.sampled_from([n.value for n in Namespace]),
    st.lists(_SEGMENT, max_size=3),
)


# --------------------------------------------------------------------------------------
# TG-1 — the tag model
# --------------------------------------------------------------------------------------


def test_tag_parse_exposes_ordered_segments_tg1() -> None:
    tag = Tag.parse("topic.cooking.grilling")
    assert tag.raw == "topic.cooking.grilling"
    assert tag.segments == ("topic", "cooking", "grilling")
    assert tag.depth == 3
    assert tag.namespace is Namespace.TOPIC
    assert tag.parent == Tag.parse("topic.cooking")
    assert [str(a) for a in tag.ancestors] == ["topic", "topic.cooking"]
    assert str(tag) == "topic.cooking.grilling"


def test_tag_parse_bare_namespace_has_no_parent_tg1() -> None:
    tag = Tag.parse("topic")
    assert tag.parent is None
    assert tag.ancestors == ()
    assert Tag("topic.cooking").segments == ("topic", "cooking")  # constructor derives segments


@given(st.text(max_size=30))
def test_tag_parse_is_total_tg1(raw: str) -> None:
    """Parsing never raises, whatever a hand-edited file holds, and depth == len(segments)."""
    tag = Tag.parse(raw)
    assert tag.depth == len(tag.segments)
    assert tag.namespace is None or isinstance(tag.namespace, Namespace)


def test_tag_is_descendant_of_matches_whole_segments_tg1() -> None:
    assert Tag.parse("topic.cooking.grilling").is_descendant_of("topic.cooking")
    assert Tag.parse("topic.cooking").is_descendant_of("topic.cooking")
    assert not Tag.parse("topic.cooking-extra").is_descendant_of("topic.cooking")


# --------------------------------------------------------------------------------------
# TG-2 — closed namespace set
# --------------------------------------------------------------------------------------


def test_unknown_namespace_is_an_error_tg2() -> None:
    findings = validate_tag("project.alpha", path="Cooking/notes/x.md")
    assert _codes(findings) == ["UNKNOWN_TAG_NAMESPACE"]
    assert findings[0].severity is Severity.ERROR
    assert findings[0].rule_id == "TG-2"
    assert findings[0].path == "Cooking/notes/x.md"
    assert findings[0].value == "project.alpha"


@pytest.mark.parametrize(
    "raw",
    [
        "topic.cooking",
        "type.note",
        "domain.legal",
    ],
)
def test_known_namespaces_validate_tg2(raw: str) -> None:
    assert validate_tag(raw) == []


# --------------------------------------------------------------------------------------
# TG-3 — depth limit counts the namespace
# --------------------------------------------------------------------------------------


def test_four_segments_pass_five_fail_tg3() -> None:
    assert MAX_TAG_DEPTH == 4
    assert validate_tag("topic.cooking.grilling.charcoal") == []
    findings = validate_tag("topic.cooking.grilling.charcoal.briquettes")
    assert _codes(findings) == ["TAG_DEPTH_EXCEEDED"]
    assert findings[0].rule_id == "TG-3"
    assert "5 segments" in findings[0].message


@given(_LEGAL_TAG)
def test_depth_equals_segment_count_tg3(raw: str) -> None:
    assert Tag.parse(raw).depth == len(raw.split("."))


# --------------------------------------------------------------------------------------
# TG-4 — segment syntax
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "Topic.Cooking",
        "topic..cooking",
        "topic.cooking.",
        ".topic.cooking",
        "topic cooking",
        "topic.heat_management",
        "topic.-cooking",
        "topic.cooking-",
        "topic.cooking--grilling",
        "",
        "topic.cooking\n",
    ],
)
def test_bad_syntax_is_the_only_finding_tg4(raw: str) -> None:
    findings = validate_tag(raw)
    assert _codes(findings) == ["TAG_SYNTAX"]
    assert findings[0].rule_id == "TG-4"


@pytest.mark.parametrize(
    "raw",
    ["topic", "topic.cooking", "topic.heat-management", "status.conflict-review", "d0main.x1"],
)
def test_good_syntax_passes_the_regex_tg4(raw: str) -> None:
    assert TAG_RE.fullmatch(raw)


def test_segment_regex_is_anchored_tg4() -> None:
    assert TAG_SEGMENT_RE.fullmatch("heat-management")
    assert not TAG_SEGMENT_RE.fullmatch("heat.management")
    assert not TAG_SEGMENT_RE.fullmatch("Heat")


def test_tags_are_not_auto_normalized_tg4() -> None:
    """The validator rejects; it never rewrites (the agent self-corrects)."""
    assert Tag.parse("Topic.Cooking").raw == "Topic.Cooking"
    assert not Tag.parse("Topic.Cooking").is_valid


# --------------------------------------------------------------------------------------
# TG-5 — a nested tag implies every ancestor
# --------------------------------------------------------------------------------------


def test_nested_tag_needs_no_declared_ancestor_tg5(tmp_path: Path) -> None:
    snapshot = _snapshot(
        tmp_path, [_record(tmp_path, "Cooking/notes/x.md", ["topic.cooking.grilling"])]
    )
    tree = build_tag_tree(snapshot)

    assert tree.findings == ()  # no "missing ancestor" finding exists
    assert "topic.cooking" in tree.tags
    assert "topic.cooking.grilling" in tree.tags
    topic_root = next(node for node in tree.roots if node.tag == "topic")
    assert render_tag_tree(topic_root.children) == [
        "- `topic.cooking`",
        "    - `topic.cooking.grilling`",
    ]


@given(st.lists(_LEGAL_TAG, max_size=8))
def test_ancestor_closure_is_prefix_closed_and_idempotent_tg5(tags: list[str]) -> None:
    closed = ancestor_closure(tags)
    assert set(tags) <= set(closed)
    assert closed == sorted(set(closed))
    for tag in closed:
        parent = tag.rpartition(".")[0]
        if parent:
            assert parent in closed
    assert ancestor_closure(closed) == closed
    assert ancestor_closure(list(reversed(tags))) == closed


# --------------------------------------------------------------------------------------
# TG-6 / TG-7 — closed type and status vocabularies
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw", sorted(TYPE_DEFINITIONS))
def test_type_vocabulary_members_validate_tg6(raw: str) -> None:
    assert validate_tag(raw) == []


def test_unknown_type_tag_is_an_error_tg6() -> None:
    """TG-6, T-18: ``type.*`` is the closed set of exactly the four values DESIGN §1.5's table
    names; a fifth (``type.article``) is ``UNKNOWN_TYPE_TAG``."""
    findings = validate_tag("type.article")
    assert _codes(findings) == ["UNKNOWN_TYPE_TAG"]
    assert findings[0].rule_id == "TG-6"
    assert set(TYPE_DEFINITIONS) == {"type.note", "type.reference", "type.solution", "type.summary"}


# --------------------------------------------------------------------------------------
# TG-8 / TG-9 — open namespaces
# --------------------------------------------------------------------------------------


def test_domain_namespace_is_open_tg8() -> None:
    """TG-8, T-20: ``domain.*`` is checked for syntax and depth only — open, unconstrained by file
    location, and no allowlist exists for it."""
    assert validate_tag("domain.finance.tax") == []
    # location never constrains a domain tag: a Cooking note may carry a legal one
    assert validate_tag("domain.legal.compliance", path="Cooking/notes/x.md") == []


def test_topic_namespace_is_open_and_has_no_registry_api_tg9() -> None:
    """TG-9, T-21: "create no ad-hoc tag" is a Layer 2 dialog concern, not a Layer 1 gate —
    ``pkb.core`` exposes no ``approve_tag``/``register_tag`` function, and a novel syntactically
    valid ``topic.*`` tag validates clean regardless."""
    import pkb.core.tags as tags_module

    assert validate_tag("topic.cooking.sous-vide") == []
    for forbidden in ("add_tag", "register_tag", "approve_tag", "APPROVED_TAGS"):
        assert not hasattr(tags_module, forbidden)


# --------------------------------------------------------------------------------------
# TG-10 — the tree is data; rendering is a separate pure function
# --------------------------------------------------------------------------------------


def _cooking_snapshot(tmp_path: Path) -> KbSnapshot:
    return _snapshot(
        tmp_path,
        [
            _record(
                tmp_path,
                "Cooking/notes/wind.md",
                [
                    "topic.cooking.grilling",
                    "topic.cooking.heat-management",
                    "type.note",
                    "status.approved",
                ],
            ),
            _record(tmp_path, "Cooking/recipes/ribeye.md", ["topic.cooking.recipes", "type.note"]),
            _record(tmp_path, "BBQ/notes/fuel.md", ["topic.bbq.equipment", "type.note"]),
            _record(tmp_path, "Cooking/notes/law.md", ["domain.legal.compliance", "type.note"]),
            # Derived files are never evidence of tag usage (GE-3).
            _record(tmp_path, "tags.md", ["topic.invented"], file_class=FileClass.DERIVED),
        ],
    )


def test_build_tag_tree_returns_a_navigable_structure_tg10(tmp_path: Path) -> None:
    tree = build_tag_tree(_cooking_snapshot(tmp_path))

    cooking = tree.subtree(Tag("topic.cooking"))
    assert cooking is not None
    assert [child.tag for child in cooking.children] == [
        "topic.cooking.grilling",
        "topic.cooking.heat-management",
        "topic.cooking.recipes",
    ]
    assert cooking.depth == 2
    assert cooking.children[0].segment == "grilling"
    assert tree.subtree("topic.nope") is None
    assert tree.files_by_tag["topic.cooking.grilling"] == ("Cooking/notes/wind.md",)
    assert "topic.invented" not in tree.tags  # derived file ignored
    assert [n.tag for n in tree.namespace_children(Namespace.DOMAIN)] == ["domain.legal"]


def test_tree_building_and_rendering_do_no_io_tg10(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tree comes from the snapshot alone, and the renderer is pure (GE-9)."""
    snapshot = _cooking_snapshot(tmp_path)

    def _no_open(*args: object, **kwargs: object) -> Any:
        raise AssertionError("tags.py must not touch the filesystem")

    monkeypatch.setattr(builtins, "open", _no_open)
    tree = build_tag_tree(snapshot)
    assert render_tag_tree(tree.roots)


def test_invalid_tags_are_excluded_and_reported_tg10(tmp_path: Path) -> None:
    snapshot = _snapshot(
        tmp_path, [_record(tmp_path, "Cooking/notes/x.md", ["topic.cooking", "Type.Note"])]
    )
    tree = build_tag_tree(snapshot)
    assert tree.tags == ("topic", "topic.cooking")
    assert _codes(tree.findings) == ["TAG_SYNTAX"]
    assert tree.findings[0].path == "Cooking/notes/x.md"


def test_tree_is_independent_of_file_order_tg10(tmp_path: Path) -> None:
    records = list(_cooking_snapshot(tmp_path).files.values())
    forward = render_tag_tree(build_tag_tree(_snapshot(tmp_path, records)).roots)
    backward = render_tag_tree(build_tag_tree(_snapshot(tmp_path, records[::-1])).roots)
    assert forward == backward


# --------------------------------------------------------------------------------------
# TG-11 — query surface honouring parent implication
# --------------------------------------------------------------------------------------


def test_files_with_tag_includes_descendants_tg11(tmp_path: Path) -> None:
    snapshot = _snapshot(
        tmp_path,
        [
            _record(tmp_path, "Cooking/notes/charcoal.md", ["topic.cooking.grilling.charcoal"]),
            _record(tmp_path, "Cooking/notes/preheat.md", ["status.conflict-review"]),
            _record(tmp_path, "BBQ/notes/fuel.md", ["topic.bbq.equipment"]),
            _record(tmp_path, "Cooking/index.md", ["topic.cooking"], file_class=FileClass.DERIVED),
        ],
    )

    assert files_with_tag(snapshot, "topic.cooking") == ["Cooking/notes/charcoal.md"]
    assert files_with_tag(snapshot, "status.conflict-review") == ["Cooking/notes/preheat.md"]
    assert files_with_tag(snapshot, "topic") == [
        "BBQ/notes/fuel.md",
        "Cooking/notes/charcoal.md",
    ]
    assert files_with_tag(snapshot, "topic.cook") == []  # whole-segment match only
    assert files_with_tag(snapshot, "topic.physics") == []


def test_tags_in_namespace_filters_by_depth_tg11(tmp_path: Path) -> None:
    tree = build_tag_tree(_cooking_snapshot(tmp_path))
    assert tree.tags_in_namespace(Namespace.TOPIC, max_depth=2) == ["topic.bbq", "topic.cooking"]
    assert tree.tags_in_namespace("domain") == ["domain.legal", "domain.legal.compliance"]
    assert "topic" not in tree.tags_in_namespace(Namespace.TOPIC)


# --------------------------------------------------------------------------------------
# TG-12 — static definitions, supplied by the generator, never read from files
# --------------------------------------------------------------------------------------


def test_static_annotations_feed_the_shared_renderer_tg12() -> None:
    """`STATIC_ANNOTATIONS` lets the tree renderer emit the same suffixes where a tree is wanted."""
    nodes = [TagNode(tag) for tag in TYPE_DEFINITIONS]
    rendered = render_tag_tree(nodes, annotations=STATIC_ANNOTATIONS)
    assert rendered == render_definition_list(TYPE_DEFINITIONS)
    assert render_tag_tree(nodes[::-1], annotations=STATIC_ANNOTATIONS) == rendered


# --------------------------------------------------------------------------------------
# TG-13 — separator constants, asserted at byte level
# --------------------------------------------------------------------------------------


def test_rendered_bytes_carry_en_dash_and_arrow_tg13() -> None:
    line = render_tag_tree(
        [TagNode("topic.cooking")], annotations={"topic.cooking": ROOT_TOPIC_ANNOTATION}
    )[0]
    assert line == "- `topic.cooking` \u2013 root topic"
    encoded = line.encode("utf-8")
    assert encoded == b"- `topic.cooking` \xe2\x80\x93 root topic"
    assert encoded.index(b"\xe2\x80\x93") == len("- `topic.cooking` ")

    mapping = render_mapping_line("topic.cooking.grilling", "topic.bbq.equipment")
    assert mapping == "- `topic.cooking.grilling` \u2194 `topic.bbq.equipment`"
    assert mapping.encode("utf-8").index(b"\xe2\x86\x94") == len("- `topic.cooking.grilling` ")


# --------------------------------------------------------------------------------------
# GE-23 / GE-17 — the shared renderer, and the shape both callers emit
# --------------------------------------------------------------------------------------


def test_domain_renders_as_a_nested_tree_ge23_c8(tmp_path: Path) -> None:
    """Contradiction C8 / Q1: `domain.*` nests like `topic.*`, unlike README's flat example."""
    tree = build_tag_tree(_cooking_snapshot(tmp_path))
    assert render_tag_tree(tree.namespace_children(Namespace.DOMAIN)) == [
        "- `domain.legal`",
        "    - `domain.legal.compliance`",
    ]


def test_full_chain_appears_and_disappears_with_its_only_tag_ge23(tmp_path: Path) -> None:
    tagged = _snapshot(
        tmp_path, [_record(tmp_path, "Cooking/notes/gas.md", ["topic.cooking.grilling.gas"])]
    )
    assert render_tag_tree(build_tag_tree(tagged).roots) == [
        "- `topic`",
        "    - `topic.cooking`",
        "        - `topic.cooking.grilling`",
        "            - `topic.cooking.grilling.gas`",
    ]
    untagged = _snapshot(tmp_path, [_record(tmp_path, "Cooking/notes/gas.md", [])])
    assert render_tag_tree(build_tag_tree(untagged).roots) == []


def test_renderer_accepts_a_starting_level_ge17() -> None:
    """The topic index embeds the same subtree the registry renders (one renderer, two callers)."""
    node = TagNode("topic.cooking", (TagNode("topic.cooking.grilling"),))
    assert render_tag_tree([node], level=1) == [
        "    - `topic.cooking`",
        "        - `topic.cooking.grilling`",
    ]


# --------------------------------------------------------------------------------------
# GE-32 — properties behind the renderer
# --------------------------------------------------------------------------------------


@given(st.lists(_LEGAL_TAG, max_size=8))
def test_render_parse_round_trips_to_the_ancestor_closure_ge32(tags: list[str]) -> None:
    lines = render_tag_tree(build_tag_forest(tags))
    assert sorted(_parse_tag_tree(lines)) == ancestor_closure(tags)


@given(st.lists(_LEGAL_TAG, max_size=8))
def test_rendering_is_invariant_to_input_order_ge32(tags: list[str]) -> None:
    assert render_tag_tree(build_tag_forest(tags)) == render_tag_tree(
        build_tag_forest(list(reversed(tags)))
    )


@given(st.lists(_LEGAL_TAG, max_size=8))
def test_rendered_indent_matches_depth_and_depth_stays_bounded_ge32(tags: list[str]) -> None:
    for line in render_tag_tree(build_tag_forest(tags)):
        tag = _parse_tag_tree([line])[0]
        indent = len(line) - len(line.lstrip(" "))
        assert indent == 4 * tag.count(".")
        assert tag.count(".") + 1 <= MAX_TAG_DEPTH
        assert TAG_RE.fullmatch(tag)
