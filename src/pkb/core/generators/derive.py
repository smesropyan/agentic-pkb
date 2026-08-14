"""What the generators compute from a snapshot before any byte is rendered (GE-3, GE-19 … GE-24).

Four derivations are needed by more than one artifact, and each is subtle enough that two
implementations would silently disagree:

* **Cross-topic mappings.** Root ``tags.md`` aggregates them (GE-19, GE-20) and every topic index
  shows its own slice (GE-18). GE-18's assertion — "the same unordered pair appears exactly once in
  root ``tags.md``" — is only checkable because both read the same list.
* **Skill entries.** A ``SKILL.md``'s ``name``/``description`` are read out of the ``ParsedDocument``
  the walk already parsed (T-25, T-16) — never a second file open — so the root registry's ``##
  Skills`` section and each topic index's own catalog agree on what one ``SKILL.md`` says.
* **Topic-node annotations.** A topic-backed ``topic.*`` node's summary is lifted from its own
  ``topic.md`` and marked when the topic owns an ``expert.md`` (T-23), and it has to read the same
  whether it appears in the registry's own namespace section or in that topic's own ``## Tag
  subtree`` (T-16's "the same renderer for the tag subtree") — :func:`test_topic_tag_subtree_equals
  _the_registry_block_ge17` in ``tests/core/test_generators.py`` is exactly the check that two
  drifting implementations would fail.

There is no *folder*-driven tag-section annotation left (T-1): the extension-folder mechanism the
old ``EXTENSION_MARKER`` depended on is retired outright, not merely renamed.

Everything here is pure over the snapshot: no filesystem, no clock, no absolute path in any result.
"""

from __future__ import annotations

from collections.abc import Iterable

from pkb.core import frontmatter, paths, tags
from pkb.core.errors import Finding, Severity, has_errors
from pkb.core.generators import base
from pkb.core.models import FileRole, KbSnapshot, ParsedDocument, TopicRecord

__all__ = [
    "CUSTOM_EXPERT_MARKER",
    "Pair",
    "SkillEntry",
    "cross_topic_pairs",
    "is_at_or_below",
    "pairs_for_topic",
    "read_skill_entry",
    "root_skills",
    "topic_node_annotations",
]

CUSTOM_EXPERT_MARKER = " *(custom expert)*"
"""Appended ahead of a topic-backed node's summary when the topic owns an ``expert.md`` (T-23).

The one place this is spelled: both the registry (``tags_registry.py``, re-exporting it for its own
already-established public import path) and a topic's own ``## Tag subtree`` (``topic_index.py``)
read it from here rather than each defining their own.
"""

Pair = tuple[str, str]
"""An oriented cross-topic mapping: ``(left, right)`` as rendered."""

SkillEntry = tuple[str, str]
"""A cataloguable skill: ``(name, description)``, both straight out of one ``SKILL.md`` (T-25)."""


def _pair_sort_key(pair: Pair) -> tuple[tuple[str, str], tuple[str, str]]:
    """Mapping lines sort by ``(left, right)`` under the shared tag order (GE-20, GE-4)."""
    return (tags.tag_sort_key(pair[0]), tags.tag_sort_key(pair[1]))


def is_at_or_below(tag: str, root: str) -> bool:
    """True when ``tag`` is ``root`` or a descendant of it, comparing whole segments (TG-5).

    Whole segments matter: ``topic.cooking`` must not swallow ``topic.cooking-extra``.
    """
    return tag == root or tag.startswith(f"{root}.")


def _is_topic_tag(raw: str) -> bool:
    """True for a syntactically valid tag in the ``topic`` namespace (TG-2, TG-3, TG-4)."""
    tag = tags.Tag.parse(raw)
    return tag.namespace is tags.Namespace.TOPIC and not has_errors(tags.validate_tag(raw))


# --------------------------------------------------------------------------------------
# Cross-topic mappings (GE-19, GE-20)
# --------------------------------------------------------------------------------------


def cross_topic_pairs(snapshot: KbSnapshot) -> tuple[list[Pair], list[Finding]]:
    """Every cross-topic mapping in the KB, oriented, deduplicated and sorted (GE-19, GE-20).

    For each non-derived file: the cartesian product of its ``topic.*`` tags and its normalized
    ``related_topics`` targets (FM-15). ``related_topics`` is the *only* source — a mapping is never
    inferred from a shared ``domain.*`` tag, folder proximity, or a body link (GE-19), because those
    are correlations while ``related_topics`` is a human's assertion.

    Orientation follows GE-20 and contradiction C19: the declared direction when only one side
    declared the relationship, the lexicographically smaller tag on the left when both did. The
    consequence is deliberate and worth knowing — adding the reciprocal declaration to the other
    topic *flips* an existing line. That is the price of reproducing README §1.5's own example,
    which renders ``topic.cooking.grilling`` first.

    Returns the pairs plus findings for ``related_topics`` values that cannot be rendered as a tag
    at all; dropping one silently would lose a relationship the human wrote down.
    """
    declared: dict[Pair, list[Pair]] = {}
    findings: list[Finding] = []

    for record in snapshot.content_files():
        meta = record.meta
        if meta is None:
            continue
        sources = [raw for raw in meta.tags if _is_topic_tag(raw)]
        targets: list[str] = []
        for raw in meta.related_topics:
            target = frontmatter.normalize_related_topic(raw)
            if _is_topic_tag(target):
                targets.append(target)
            else:
                findings.append(_unrenderable(record.path, raw, target))
        for source in sources:
            for target in targets:
                if source == target:
                    continue  # an identity pair is not a relationship (GE-19)
                key = min((source, target), (target, source), key=_pair_sort_key)
                declared.setdefault(key, []).append((source, target))

    oriented: list[Pair] = []
    for key in sorted(declared, key=_pair_sort_key):
        # Both directions declared: neither side's declaration outranks the other, so fall back to
        # the total order. Only one: honour it, which is what makes README §1.5 reproducible.
        oriented.append(key if len(set(declared[key])) > 1 else declared[key][0])

    return sorted(dict.fromkeys(oriented), key=_pair_sort_key), findings


def _unrenderable(path: str, raw: str, normalized: str) -> Finding:
    return Finding(
        code="UNRENDERABLE_RELATED_TOPIC",
        severity=Severity.WARNING,
        message=(
            f"related_topics entry {raw!r} normalizes to {normalized!r}, which is not a valid "
            "topic.* tag, so no cross-topic mapping was rendered for it"
        ),
        rule_id="GE-19",
        path=path,
        field="related_topics",
        value=raw,
        hint="use the target topic's name, e.g. 'bbq' or 'bbq.equipment'",
    )


def pairs_for_topic(pairs: Iterable[Pair], topic_tag: str) -> list[Pair]:
    """The mappings involving one topic, always oriented local-topic-left (GE-18).

    "Involving" means at or below the topic's own tag, so ``Cooking``'s index shows a mapping
    declared on ``topic.cooking.grilling``. When *both* sides are local — two branches of the same
    topic pointing at each other — local-left is ambiguous, so the total tag order decides.
    """
    out: list[Pair] = []
    for left, right in pairs:
        left_local = is_at_or_below(left, topic_tag)
        right_local = is_at_or_below(right, topic_tag)
        if not (left_local or right_local):
            continue
        if left_local and right_local:
            out.append(min((left, right), (right, left), key=_pair_sort_key))
        elif right_local:
            out.append((right, left))
        else:
            out.append((left, right))
    return sorted(dict.fromkeys(out), key=_pair_sort_key)


# --------------------------------------------------------------------------------------
# Topic-node annotations (T-23, GE-23)
# --------------------------------------------------------------------------------------


def topic_node_annotations(snapshot: KbSnapshot) -> dict[str, str]:
    """One rendered suffix per topic-backed ``topic.*`` node, keyed by full dotted tag (T-23).

    A tag with no entry here has no topic folder behind it, so
    :func:`~pkb.core.tags.render_tag_tree` renders it bare — the lookup miss *is* the "stays bare"
    half of T-23, not a case this function special-cases. Covers every topic in the snapshot, not
    only top-level ones, because a sub-topic (``topic.cooking.baking``) is just as topic-backed as
    its parent, and a caller that wants only one topic's own subtree simply looks up fewer keys.

    There is no extension-folder mechanism any more (T-1), so this no longer also marks a folder
    that happens to sit under the topic root — a name ``STRUCTURAL_DIRS`` does not recognize is
    reported once, cross-file, as ``UNEXPECTED_TOPIC_ENTRY`` (:func:`pkb.core.paths.extension_folders`),
    never rendered into a tag-tree annotation.
    """
    return {topic.tag: _topic_node_suffix(topic) for topic in snapshot.topics.values()}


def _topic_node_suffix(topic: TopicRecord) -> str:
    """``*(custom expert)*`` (if any) then the lifted, degraded-total summary (T-23, GE-25)."""
    marker = CUSTOM_EXPERT_MARKER if topic.has_expert else ""
    return f"{marker}{tags.TAG_DEF_SEP}{_topic_node_summary(topic)}"


def _topic_node_summary(topic: TopicRecord) -> str:
    """The topic's own ``description``, degraded rather than dropped (T-23, GE-25).

    Never authored here: a missing or unparseable ``topic.md`` renders a placeholder — the caller
    reports the accompanying diagnostic (``root_tags_findings``, T-37).
    """
    if topic.meta is None:
        return base.MISSING_TOPIC_METADATA
    description = topic.meta.description
    return base.inline(description) if description else base.NO_DESCRIPTION


# --------------------------------------------------------------------------------------
# Skill entries (T-16, T-25)
# --------------------------------------------------------------------------------------


def read_skill_entry(doc: ParsedDocument | None) -> SkillEntry | None:
    """Read ``name``/``description`` straight out of a ``SKILL.md``'s own frontmatter block.

    ``SKILL.md`` carries no PKB schema (skills are a file class of their own, §1.3/§1.9), so this
    reads ``doc.raw`` — the generic YAML mapping :func:`pkb.core.frontmatter.parse` already loaded
    while the walk recorded the file — as a two-key document in its own right, rather than routing
    through :class:`~pkb.core.models.Metadata`'s known/unknown split, which exists for a different
    schema and would make the coincidence that both schemas spell one field ``description`` load-
    bearing by accident.

    Costs no second file open (GE-9): every skill markdown file is parsed once, by the walk itself.
    Returns ``None`` when the block is missing, unparseable, or names no usable ``name`` — a skill
    with no name cannot be catalogued, and this function never invents one from the folder.
    """
    if doc is None or doc.raw is None:
        return None
    name = doc.raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    description = doc.raw.get("description")
    return (name.strip(), description.strip() if isinstance(description, str) else "")


def root_skills(snapshot: KbSnapshot) -> list[SkillEntry]:
    """The knowledge base's own root-level skills, sorted by name (T-25).

    Root-level means ``skills/<name>/SKILL.md`` directly under the KB root — ``record.topic_path is
    None`` is what tells one apart from a topic's own overload folder (``Cooking/skills/...``, which
    carries its owning topic's path). A topic's own skills stay out of this list on purpose: they
    belong in that topic's own index (T-25), never in the root registry.
    """
    entries: list[SkillEntry] = []
    for record in snapshot.files.values():
        if record.role is not FileRole.SKILL or record.topic_path is not None:
            continue
        parts = record.path.split("/")
        if len(parts) != 3 or parts[0] != paths.SKILLS_DIR or parts[2] != paths.SKILL_FILE:
            continue
        entry = read_skill_entry(record.doc)
        if entry is not None:
            entries.append(entry)
    return sorted(entries, key=lambda entry: tags.tag_sort_key(entry[0]))
