"""What the generators compute from a snapshot before any byte is rendered (GE-3, GE-19 … GE-24).

Two derivations are needed by more than one artifact, and both are subtle enough that two
implementations would silently disagree:

* **Cross-topic mappings.** Root ``tags.md`` aggregates them (GE-19, GE-20) and every topic index
  shows its own slice (GE-18). GE-18's assertion — "the same unordered pair appears exactly once in
  root ``tags.md``" — is only checkable because both read the same list.
* **Tag-section annotations.** The extension marker (GE-24) is derived from the *tree* (a directory
  under a topic root), not from the tag, so the registry and the topic index must consult the same
  folder listing or one of them will mark a node the other does not.

Everything here is pure over the snapshot: no filesystem, no clock, no absolute path in any result.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pkb.core import frontmatter, paths, tags
from pkb.core.errors import Finding, Severity, has_errors
from pkb.core.models import KbSnapshot

__all__ = [
    "Pair",
    "cross_topic_pairs",
    "extension_annotations",
    "is_at_or_below",
    "pairs_for_topic",
    "topic_annotations",
]

Pair = tuple[str, str]
"""An oriented cross-topic mapping: ``(left, right)`` as rendered."""


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
# Tag-section annotations (GE-23, GE-24)
# --------------------------------------------------------------------------------------


def extension_annotations(snapshot: KbSnapshot) -> dict[str, str]:
    """Tags that carry ``*(topic-specific extension)*``, keyed by full dotted tag (GE-24).

    The marker is derived from the tree, not from the tag: a node is marked iff a non-structural
    directory whose name slugifies to the node's leaf segment sits directly under the topic root
    that owns the node's parent tag. Deleting the folder therefore removes the marker while the tag
    (still used by a file) keeps its node — which is exactly GE-24's test.

    Built by enumerating the folders rather than the tags, so the result is a pure function of the
    snapshot's topic records and can annotate any tree slice.
    """
    marked: dict[str, str] = {}
    for topic in snapshot.topics.values():
        # Read live rather than cached (T-1 retired ``TopicRecord.extension_folders``): this whole
        # function is EXTENSION_MARKER machinery a later task removes outright.
        for folder in paths.extension_folders(snapshot.root / topic.path):
            segment = paths.slugify(folder)
            if segment:
                marked[f"{topic.tag}.{segment}"] = tags.EXTENSION_MARKER
    return marked


def topic_annotations(snapshot: KbSnapshot, root_tag: str) -> Mapping[str, str]:
    """Annotations for one ``topic.*`` section: the root gloss plus extension markers (GE-23).

    ``root_tag`` wins over an extension marker on the same tag: a section's root node is a topic,
    whatever else the folder tree says about a directory with that name.
    """
    return {**extension_annotations(snapshot), root_tag: tags.ROOT_TOPIC_ANNOTATION}
