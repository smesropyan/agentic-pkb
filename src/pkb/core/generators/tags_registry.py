"""Root ``tags.md`` — the tag registry (GE-21 … GE-24, §4.4).

The registry is the KB's ontology: the one file that teaches an agent which tags exist and how they
relate. Three kinds of content and nothing else (GE-21) — namespace sections, per-topic subtrees,
cross-topic mappings. No file listings, no inverted tag→file index, no counts, because all three
would churn on every note and none is what the reader needs.

Two shapes are worth naming before reading the code:

* **The ``type`` and ``status`` sections are generator text, not derived content** (TG-12, C17).
  They render identically for an empty KB and a full one — an ontology that vanishes when unused
  cannot teach an agent how to file the first note (GE-29).
* **``domain.*`` renders as a nested tree**, the same renderer as ``topic.*`` (Q1 / C8). README
  §1.5's worked example lists three ``domain.*`` tags flat, but the same section's own rule says a
  nested tag implies its parent and calls the registry "the canonical relational tree". One
  renderer, one shape.
"""

from __future__ import annotations

from pathlib import Path

from pkb.core import paths, tags
from pkb.core.errors import Finding
from pkb.core.generators import base, derive
from pkb.core.models import KbSnapshot, Metadata, TopicRecord

__all__ = [
    "MAPPINGS_HEADING",
    "SOURCE_TYPE",
    "TITLE",
    "generate_root_tags",
    "render_root_tags",
    "root_tags_findings",
]

TITLE = "PKB Tag Registry"
SOURCE_TYPE = "tag-registry"
"""Frontmatter is exactly ``title`` + ``source_type``, pinned verbatim by README §1.5 (GE-21)."""

MAPPINGS_HEADING = "Cross-topic mappings (aggregated from `related_topics`)"
"""Including its inline code span — the heading is pinned to the byte (§4.4)."""

_NAMESPACE_HEADING = "Namespace: {name}"


def render_root_tags(snapshot: KbSnapshot) -> str:
    """Render the tag registry (GE-21 … GE-24). Pure: no I/O, no clock (GE-9).

    Section order is fixed by GE-22 and follows README §1.5's rendered example rather than its
    namespace *table* (C15): one section per top-level topic root, then ``type``, ``status``,
    ``domain``, then the mappings. Sub-topics get no heading of their own — they nest inside their
    root topic's tree, which is what keeps the file readable as one ontology instead of a list of
    folders.
    """
    tree = tags.build_tag_tree(snapshot)
    annotations = derive.extension_annotations(snapshot)
    blocks: list[str] = []

    for topic in _root_topics(snapshot):
        node = tree.subtree(topic.tag) or tags.TagNode(topic.tag)
        section_annotations = {**annotations, topic.tag: tags.ROOT_TOPIC_ANNOTATION}
        blocks += base.section(
            _NAMESPACE_HEADING.format(name=topic.tag),
            tags.render_tag_tree([node], annotations=section_annotations),
        )

    blocks += base.section(
        _NAMESPACE_HEADING.format(name=tags.Namespace.TYPE.value),
        tags.render_definition_list(tags.TYPE_DEFINITIONS),
    )
    blocks += base.section(
        _NAMESPACE_HEADING.format(name=tags.Namespace.STATUS.value),
        tags.render_definition_list(tags.STATUS_DEFINITIONS),
    )
    blocks += base.section(
        _NAMESPACE_HEADING.format(name=tags.Namespace.DOMAIN.value),
        tags.render_tag_tree(tree.namespace_children(tags.Namespace.DOMAIN)),
    )

    pairs, _ = derive.cross_topic_pairs(snapshot)
    blocks += base.section(
        MAPPINGS_HEADING, [tags.render_mapping_line(left, right) for left, right in pairs]
    )

    meta = Metadata(title=TITLE, source_type=SOURCE_TYPE)
    return base.document(meta, TITLE, blocks, banner=False)


def _root_topics(snapshot: KbSnapshot) -> list[TopicRecord]:
    """Top-level topic roots, one section each, sorted by tag and deduplicated (GE-22).

    Deduplication matters because :func:`pkb.core.paths.slugify` is lossy: ``Heat Management`` and
    ``heat_management`` are two folders with one tag, and two identical sections would be worse
    than one.
    """
    seen: dict[str, TopicRecord] = {}
    for topic in sorted(snapshot.top_level_topics(), key=lambda item: tags.tag_sort_key(item.tag)):
        seen.setdefault(topic.tag, topic)
    return list(seen.values())


def root_tags_findings(snapshot: KbSnapshot) -> list[Finding]:
    """Diagnostics raised while deriving the registry (TG-10, GE-19).

    Two sources: tags that were excluded from the tree because they are invalid (rendering an
    ``UNKNOWN_TYPE_TAG`` into the ontology would teach the next agent to repeat it), and
    ``related_topics`` values that cannot be rendered as a mapping.
    """
    _, mapping_findings = derive.cross_topic_pairs(snapshot)
    return [*tags.build_tag_tree(snapshot).findings, *mapping_findings]


def generate_root_tags(kb_root: Path, snapshot: KbSnapshot) -> bool:
    """Render and write ``<kb>/tags.md``; True when the bytes changed (GE-8, GE-9)."""
    return base.write_derived(kb_root / paths.TAGS_FILE, render_root_tags(snapshot))
