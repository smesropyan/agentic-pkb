"""Root ``tags.md`` — the tag registry (T-22 … T-27, DESIGN §1.6).

The registry is the KB's ontology, and it is now the *only* derived file above the topics (T-37):
there is no root ``index.md`` any more, so this module also carries what that file used to —
degraded-topic totality (GE-25) and the ``*(custom expert)*`` marker.

Four kinds of content (T-22, T-25): namespace sections (one per top-level topic root, sub-topics
nested inside), the ``type`` static definitions, a skills catalog, and cross-topic mappings. No file
listings, no inverted tag→file index, no counts — all three would churn on every note and none is
what the reader needs.

Three shapes are worth naming before reading the code:

* **A topic-backed node's summary is lifted, never authored** (T-23). ``derive.topic_node_annotations``
  builds one suffix per topic in ``snapshot.topics`` — the ``description`` its ``topic.md`` already
  carries, preceded by :data:`CUSTOM_EXPERT_MARKER` when the topic owns an ``expert.md`` — and a tag
  with no topic folder behind it simply has no entry, so :func:`~pkb.core.tags.render_tag_tree`
  renders it bare. Nothing here authors a description; changing one changes the ``topic.md`` an
  operator already approved (§1.2), and this module only reads it back. The same function backs a
  topic index's own ``## Tag subtree`` (``topic_index.py``), so the two never render it two ways.
* **The ``type`` section is generator text, not derived content** (TG-12, T-18). It renders
  identically for an empty KB and a full one — an ontology that vanishes when unused cannot teach an
  agent how to file the first note (GE-29). There is no ``status`` section (T-17).
* **``domain.*`` renders as a nested tree**, the same renderer as ``topic.*`` (T-24). It stays bare
  on purpose — no file sits behind a domain the way ``topic.md`` sits behind a topic.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pkb.core import paths, tags
from pkb.core.errors import Finding
from pkb.core.generators import base, derive
from pkb.core.generators.derive import CUSTOM_EXPERT_MARKER, SkillEntry
from pkb.core.models import KbSnapshot, Metadata, TopicRecord

__all__ = [
    "CUSTOM_EXPERT_MARKER",
    "MAPPINGS_HEADING",
    "SKILLS_HEADING",
    "SOURCE_TYPE",
    "TITLE",
    "generate_root_tags",
    "render_root_tags",
    "root_tags_findings",
]

TITLE = "PKB Tag Registry"
SOURCE_TYPE = "tag-registry"
"""Frontmatter is exactly ``title`` + ``source_type``, pinned verbatim by DESIGN §1.6 (T-22)."""

MAPPINGS_HEADING = "Cross-topic mappings (aggregated from `related_topics`)"
"""Including its inline code span — the heading is pinned to the byte (T-26)."""

SKILLS_HEADING = "Skills (from each `SKILL.md`)"
"""Including its inline code span — pinned to the byte, verbatim from DESIGN §1.6 (T-25)."""

_NAMESPACE_HEADING = "Namespace: {name}"


def render_root_tags(snapshot: KbSnapshot, *, shipped_skills: Sequence[SkillEntry] = ()) -> str:
    """Render the tag registry (T-22 … T-27). Pure: no I/O, no clock (GE-9).

    Section order follows DESIGN §1.6's worked example: one section per top-level topic root, then
    ``type``, ``domain``, the skills catalog, then the mappings. Sub-topics get no heading of their
    own — they nest inside their root topic's tree, which is what keeps the file readable as one
    ontology instead of a list of folders.

    ``shipped_skills`` is the read-only package-data mount's own catalog (T-25) — Layer 1 has no
    knowledge of where it lives on disk, so the caller supplies it and an empty default keeps every
    Layer 1 test and bare rebuild honest about that. A root-owned skill with the same name shadows
    the shipped one it names, mirroring DESIGN §4's own resolution order ("the shipped mount first,
    then the root folder... the most specific entry wins") rather than listing a shadowed entry a
    Topic Expert would never actually load.
    """
    tree = tags.build_tag_tree(snapshot)
    annotations = derive.topic_node_annotations(snapshot)
    blocks: list[str] = []

    for topic in _root_topics(snapshot):
        node = tree.subtree(topic.tag) or tags.TagNode(topic.tag)
        blocks += base.section(
            _NAMESPACE_HEADING.format(name=topic.tag),
            tags.render_tag_tree([node], annotations=annotations),
        )

    blocks += base.section(
        _NAMESPACE_HEADING.format(name=tags.Namespace.TYPE.value),
        tags.render_definition_list(tags.TYPE_DEFINITIONS),
    )
    blocks += base.section(
        _NAMESPACE_HEADING.format(name=tags.Namespace.DOMAIN.value),
        tags.render_tag_tree(tree.namespace_children(tags.Namespace.DOMAIN)),
    )
    skills = _render_skills(shipped_skills, derive.root_skills(snapshot))
    blocks += base.section(SKILLS_HEADING, skills)

    pairs, _ = derive.cross_topic_pairs(snapshot)
    blocks += base.section(
        MAPPINGS_HEADING, [tags.render_mapping_line(left, right) for left, right in pairs]
    )

    meta = Metadata(title=TITLE, source_type=SOURCE_TYPE)
    return base.document(meta, TITLE, blocks, banner=False)


def _render_skills(shipped: Sequence[SkillEntry], root_owned: Sequence[SkillEntry]) -> list[str]:
    """The ``## Skills`` body: shipped entries, the root's own shadowing by name (T-25).

    Descriptions pass through :func:`~pkb.core.generators.base.inline` like every other authored
    string embedded in derived output (GE-26) — a ``SKILL.md`` description is as human-authored as a
    ``topic.md`` one, and the registry escapes both the same way.
    """
    merged: dict[str, str] = dict(shipped)
    merged.update(root_owned)
    lines = []
    for name, description in sorted(merged.items(), key=lambda entry: tags.tag_sort_key(entry[0])):
        gloss = base.inline(description) if description else ""
        lines.append(f"{tags.BULLET}`{name}`" + (f"{tags.TAG_DEF_SEP}{gloss}" if gloss else ""))
    return lines


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
    """Diagnostics raised while deriving the registry (TG-10, T-26, GE-25).

    Three sources: tags that were excluded from the tree because they are invalid (rendering an
    ``UNKNOWN_TYPE_TAG`` into the ontology would teach the next agent to repeat it),
    ``related_topics`` values that cannot be rendered as a mapping, and a topic-backed node whose
    summary is degraded. The last of these used to be the retired root catalog's job — its own
    docstring said so ("the topic's own ``topic.md`` is diagnosed by the root catalog... otherwise a
    single missing description would produce two findings for one fix"); with no root ``index.md``
    left (T-37) this is the one place that promise can still be kept, so ``topic_index_findings``
    keeps skipping the topic's own ``topic.md`` and this function is why that is still safe.
    """
    findings: list[Finding] = list(tags.build_tag_tree(snapshot).findings)
    for topic in snapshot.topics.values():
        path = f"{topic.path}/{paths.TOPIC_FILE}"
        if topic.meta is None:
            findings.append(base.missing_topic_metadata(path))
        elif not topic.meta.description:
            findings.append(base.missing_description(path))
    _, mapping_findings = derive.cross_topic_pairs(snapshot)
    findings += mapping_findings
    return findings


def generate_root_tags(
    kb_root: Path, snapshot: KbSnapshot, *, shipped_skills: Sequence[SkillEntry] = ()
) -> bool:
    """Render and write ``<kb>/tags.md``; True when the bytes changed (GE-8, GE-9)."""
    text = render_root_tags(snapshot, shipped_skills=shipped_skills)
    return base.write_derived(kb_root / paths.TAGS_FILE, text)
