"""Tags: the model, the validator, the derived tree, and the shared tree renderer (TG-1 … TG-13).

The namespace set itself is governed by the T-rules (T-17 … T-21), which supersede this file's own
TG-2/TG-7: three namespaces, ``topic.*`` and ``domain.*`` open trees the operator grows a branch at
a time, ``type.*`` the one closed set, and no ``status.*`` — a ``status.*`` tag is an unrecognized
namespace like any other invented one. Layer 1 never invents, approves, or rewrites a tag: it
parses, checks syntax/depth/vocabulary, derives the tree that is actually in use, and renders it.
Governance is a Layer 2 dialog concern (T-21).

Two rendered surfaces share one renderer (GE-17, GE-23): the root ``tags.md`` registry and every
topic ``index.md``'s ``## Tag subtree`` block. The renderer is pure and takes its annotations from
the caller, so the *generators* decide semantics (the root-topic annotation, the extension marker,
a static definition) while the *bytes* — bullet, backticks, indent, separators — are pinned here
(TG-13).

Two decisions worth knowing before reading:

* ``domain.*`` renders as a nested tree, exactly like ``topic.*`` (contradiction C8 / Q1). The
  README's worked example lists ``domain.legal.compliance`` flat, but §1.5's own rule says a nested
  tag implies its parent and the registry is "the canonical relational tree"; one renderer for both
  is the resolution. ``type.*`` stays flat because it *is* a flat static list (T-18).
* The ancestor closure (TG-5) runs all the way to the bare namespace, so ``topic`` is a node of the
  forest even though no section ever starts there. Sections start at ``TagTree.subtree("topic.x")``
  or ``TagTree.namespace_children(Namespace.DOMAIN)``, which is what GE-22's section shapes need.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from pkb.core.errors import Finding, Severity, has_errors
from pkb.core.models import KbSnapshot

__all__ = [
    "BULLET",
    "EXTENSION_MARKER",
    "INDENT",
    "MAPPING_SEP",
    "MAX_TAG_DEPTH",
    "ROOT_TOPIC_ANNOTATION",
    "STATIC_ANNOTATIONS",
    "STATIC_DEFINITIONS",
    "TAG_DEF_SEP",
    "TAG_RE",
    "TAG_SEGMENT_RE",
    "TYPE_DEFINITIONS",
    "TYPE_TAGS",
    "Namespace",
    "Tag",
    "TagNode",
    "TagTree",
    "ancestor_closure",
    "build_tag_forest",
    "build_tag_tree",
    "definition_annotation",
    "files_with_tag",
    "render_definition_list",
    "render_mapping_line",
    "render_tag_tree",
    "tag_sort_key",
    "validate_tag",
]


# --------------------------------------------------------------------------------------
# Syntax (TG-3, TG-4)
# --------------------------------------------------------------------------------------

MAX_TAG_DEPTH: Final = 4
"""Segments per tag, **inclusive of the namespace** — ``topic.cooking.grilling.charcoal`` (TG-3)."""

_SEGMENT_PATTERN: Final = r"[a-z0-9]+(?:-[a-z0-9]+)*"

TAG_SEGMENT_RE: Final = re.compile(rf"^{_SEGMENT_PATTERN}$")
"""One lowercase kebab-case segment (TG-4). Also the shape ``slugify`` must produce (PA-8)."""

TAG_RE: Final = re.compile(rf"^{_SEGMENT_PATTERN}(?:\.{_SEGMENT_PATTERN})*$")
"""A whole tag: segments joined by single dots, no leading/trailing/double dots (TG-4)."""


class Namespace(StrEnum):
    """The closed namespace set: three, and nothing invents a fourth (T-17).

    Closed because the registry renderer has exactly three section kinds and supplies a static
    definition for one of them (``type.*``, T-18); a fourth namespace would have no defined
    rendering. There is no ``status.*`` member — the PKB writes instructions and executes nothing,
    so nothing in the tree records work in progress or a status field (T-32).
    """

    TOPIC = "topic"
    TYPE = "type"
    DOMAIN = "domain"


# --------------------------------------------------------------------------------------
# Static generator text (TG-12) and rendering constants (TG-13)
# --------------------------------------------------------------------------------------

TAG_DEF_SEP: Final = " \u2013 "
"""EN DASH (U+2013) with single spaces. Used only in tag-definition lines (TG-13).

Written as an escape so a code review, a diff, or a stray autocorrect cannot swap it for a
hyphen-minus: the byte sequence is what TG-13's golden test asserts.
"""

MAPPING_SEP: Final = " \u2194 "
"""LEFT RIGHT ARROW (U+2194) with single spaces, for cross-topic mapping lines (TG-13)."""

EXTENSION_MARKER: Final = " *(topic-specific extension)*"
"""Appended after the backticked tag with **no** dash (TG-13, GE-24)."""

ROOT_TOPIC_ANNOTATION: Final = f"{TAG_DEF_SEP}root topic"
"""The annotation a topic section's root node carries (GE-23)."""

BULLET: Final = "- "
INDENT: Final = "    "
"""4 spaces per nesting level (GE-7, GE-23)."""

TYPE_DEFINITIONS: Final[Mapping[str, str | None]] = MappingProxyType(
    {
        "type.note": "an observation from the operator's own practice",
        "type.reference": "static source",
        "type.solution": "reusable solution (a note tagged as a solution)",
        "type.summary": "breadth overview",
    }
)
"""The closed ``type.*`` vocabulary with its static gloss (T-18). Order is render order."""

STATIC_DEFINITIONS: Final[Mapping[str, str | None]] = MappingProxyType(dict(TYPE_DEFINITIONS))
"""Every tag whose gloss is generator text rather than derived from files (TG-12, C17).

``type.*`` is the only closed vocabulary left (T-17, T-18) — kept as its own mapping, rather than
folded into :data:`TYPE_DEFINITIONS` directly, so a future closed namespace has one join point.
"""

STATIC_ANNOTATIONS: Final[Mapping[str, str]] = MappingProxyType(
    {tag: f"{TAG_DEF_SEP}{gloss}" for tag, gloss in STATIC_DEFINITIONS.items() if gloss}
)
"""Ready-to-render suffixes for :func:`render_tag_tree`'s ``annotations`` (TG-12, TG-13)."""

TYPE_TAGS: Final = frozenset(TYPE_DEFINITIONS)

_NO_ANNOTATIONS: Final[Mapping[str, str]] = MappingProxyType({})


def definition_annotation(tag: str) -> str:
    """The rendered suffix for a tag carrying a static definition, or ``""`` (TG-12, TG-13).

    A ``None`` gloss in the vocabulary renders bare — the empty string, not a dangling separator.
    """
    gloss = STATIC_DEFINITIONS.get(tag)
    return f"{TAG_DEF_SEP}{gloss}" if gloss else ""


def tag_sort_key(tag: str) -> tuple[str, str]:
    """Total, locale-independent sibling order: case-insensitive, then codepoint (GE-4, GE-23)."""
    return (tag.lower(), tag)


# --------------------------------------------------------------------------------------
# The tag model (TG-1)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Tag:
    """A dot-separated tag parsed into ordered segments (TG-1).

    Parsing is total and lossless: ``raw`` is kept byte-exact and nothing is normalized on read
    (TG-4 — the validator rejects and the agent self-corrects, so a silent fix would hide the
    defect). ``segments`` is derived from ``raw`` when omitted, so ``Tag("topic.cooking")`` and
    ``Tag.parse("topic.cooking")`` agree.
    """

    raw: str
    segments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.segments and self.raw:
            object.__setattr__(self, "segments", tuple(self.raw.split(".")))

    @classmethod
    def parse(cls, raw: str) -> Tag:
        """Split a tag into segments. Total — never raises, whatever the input (TG-1)."""
        return cls(raw, tuple(raw.split(".")) if raw else ())

    @property
    def namespace(self) -> Namespace | None:
        """The first segment as a :class:`Namespace`, or ``None`` when it is outside the set (TG-2)."""
        if not self.segments:
            return None
        try:
            return Namespace(self.segments[0])
        except ValueError:
            return None

    @property
    def depth(self) -> int:
        """Segment count, **including** the namespace (TG-3)."""
        return len(self.segments)

    @property
    def parent(self) -> Tag | None:
        """The tag one level up, or ``None`` for a bare namespace (TG-1, TG-5)."""
        if len(self.segments) < 2:
            return None
        return Tag.parse(".".join(self.segments[:-1]))

    @property
    def ancestors(self) -> tuple[Tag, ...]:
        """Every proper prefix, shallowest first — ``topic``, ``topic.cooking`` (TG-1, TG-5)."""
        return tuple(Tag.parse(".".join(self.segments[:i])) for i in range(1, len(self.segments)))

    @property
    def is_valid(self) -> bool:
        """True when :func:`validate_tag` reports no error (TG-2, TG-3, TG-4, TG-6, TG-7)."""
        return not has_errors(validate_tag(self.raw))

    def is_descendant_of(self, other: Tag | str) -> bool:
        """True when this tag is ``other`` or lies below it (TG-5).

        Compares whole segments, so ``topic.cooking`` does not swallow ``topic.cooking-extra``.
        """
        ancestor = other.raw if isinstance(other, Tag) else other
        return self.raw == ancestor or self.raw.startswith(f"{ancestor}.")

    def __str__(self) -> str:
        return self.raw


# --------------------------------------------------------------------------------------
# Validation (T-17, T-18, TG-3, TG-4)
# --------------------------------------------------------------------------------------


def validate_tag(raw: str, *, path: str | None = None) -> list[Finding]:
    """Check one tag's syntax, namespace, depth, and the one closed vocabulary (T-17, T-18).

    Returns findings; never raises (CX-5). A syntax failure returns on its own because the segments
    of a malformed tag are not meaningful — reporting "unknown namespace ``Topic``" alongside
    "not kebab-case" tells the agent the same thing twice and hides the one fix that matters.

    ``topic.*`` and ``domain.*`` are open: no vocabulary lookup, no allowlist, ever (TG-8, TG-9).
    """
    if not TAG_RE.fullmatch(raw):
        return [
            Finding(
                code="TAG_SYNTAX",
                severity=Severity.ERROR,
                message=(
                    f"Tag {raw!r} is not a dot-separated path of lowercase kebab-case segments."
                ),
                rule_id="TG-4",
                path=path,
                field="tags",
                value=raw,
                hint=(
                    "Use segments matching [a-z0-9]+(-[a-z0-9]+)* joined by single dots, "
                    "e.g. topic.cooking.heat-management."
                ),
            )
        ]

    tag = Tag.parse(raw)
    findings: list[Finding] = []
    namespace = tag.namespace

    if namespace is None:
        findings.append(
            Finding(
                code="UNKNOWN_TAG_NAMESPACE",
                severity=Severity.ERROR,
                message=(
                    f"Tag {raw!r} starts with namespace {tag.segments[0]!r}, which is not one of "
                    f"{_joined(n.value for n in Namespace)}."
                ),
                rule_id="TG-2",
                path=path,
                field="tags",
                value=raw,
                hint="Re-file the tag under topic, type or domain.",
            )
        )

    if tag.depth > MAX_TAG_DEPTH:
        findings.append(
            Finding(
                code="TAG_DEPTH_EXCEEDED",
                severity=Severity.ERROR,
                message=(
                    f"Tag {raw!r} has {tag.depth} segments; the limit is {MAX_TAG_DEPTH} "
                    "including the namespace."
                ),
                rule_id="TG-3",
                path=path,
                field="tags",
                value=raw,
                hint=f"Shorten the tag to at most {MAX_TAG_DEPTH} segments, e.g. "
                f"{'.'.join(tag.segments[:MAX_TAG_DEPTH])}.",
            )
        )

    if namespace is Namespace.TYPE and raw not in TYPE_TAGS:
        findings.append(
            Finding(
                code="UNKNOWN_TYPE_TAG",
                severity=Severity.ERROR,
                message=(
                    f"Tag {raw!r} is not one of the four type tags: {_joined(sorted(TYPE_TAGS))}."
                ),
                rule_id="TG-6",
                path=path,
                field="tags",
                value=raw,
                hint="The type.* vocabulary is closed; pick the tag matching the file's source_type.",
            )
        )

    return findings


def _joined(values: Iterable[str]) -> str:
    return ", ".join(values)


# --------------------------------------------------------------------------------------
# The derived tree (TG-5, TG-10)
# --------------------------------------------------------------------------------------


def ancestor_closure(tags: Iterable[str]) -> list[str]:
    """Every input tag plus every proper prefix, deduplicated and sorted (TG-5).

    A nested tag implies its ancestors, so a tree materializes them even though frontmatter is
    never required to list them (TG-5, and the README §1.4 example that carries
    ``topic.cooking.grilling`` without ``topic.cooking``). The closure runs down to the bare
    namespace; sections are cut from the forest by the generator, not by truncating here.

    Pure string arithmetic: no validation, no normalization. Callers filter first (see
    :func:`build_tag_tree`).
    """
    closed: set[str] = set()
    for raw in tags:
        if not raw:
            continue
        segments = raw.split(".")
        for i in range(1, len(segments) + 1):
            prefix = ".".join(segments[:i])
            if prefix:
                closed.add(prefix)
    return sorted(closed, key=tag_sort_key)


@dataclass(frozen=True, slots=True)
class TagNode:
    """One node of the tag tree: the **full dotted tag** plus its children (TG-10, GE-23).

    The full tag is carried (not just the leaf segment) because that is what gets rendered — a
    bullet reading ``grilling`` would be ambiguous across sections.
    """

    tag: str
    children: tuple[TagNode, ...] = ()

    @property
    def segment(self) -> str:
        """The leaf segment, for callers that need the display fragment (GE-24)."""
        return self.tag.rpartition(".")[2]

    @property
    def depth(self) -> int:
        """Segment count of this node's tag (TG-3)."""
        return self.tag.count(".") + 1

    def walk(self) -> Iterator[TagNode]:
        """This node then its descendants, pre-order, siblings in render order."""
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass(frozen=True, slots=True)
class TagTree:
    """The tags in use across the KB, as data (TG-10).

    Rendering is a separate pure function (:func:`render_tag_tree`) because research packs need the
    tree as data, not only as markdown (README Part 4).
    """

    roots: tuple[TagNode, ...]
    """One node per namespace actually in use, sorted by :func:`tag_sort_key`."""

    files_by_tag: Mapping[str, tuple[str, ...]]
    """Tag → the KB-relative paths that *literally* carry it. Implied ancestors are absent; use
    :func:`files_with_tag` for the at-or-below query (TG-5, TG-11)."""

    findings: tuple[Finding, ...] = ()
    """Tags skipped while building because they were invalid — reported, never silently dropped."""

    @property
    def tags(self) -> tuple[str, ...]:
        """Every node's tag — the ancestor closure of the tags in use — sorted (TG-5)."""
        return tuple(
            sorted((node.tag for root in self.roots for node in root.walk()), key=tag_sort_key)
        )

    def subtree(self, tag: str | Tag) -> TagNode | None:
        """The branch rooted at ``tag``, or ``None`` when the tag is unused (TG-10, GE-17)."""
        wanted = tag.raw if isinstance(tag, Tag) else tag
        for root in self.roots:
            if not (wanted == root.tag or wanted.startswith(f"{root.tag}.")):
                continue
            for node in root.walk():
                if node.tag == wanted:
                    return node
        return None

    def namespace_children(self, namespace: Namespace | str) -> tuple[TagNode, ...]:
        """The nodes one level below a bare namespace — the ``domain.*`` section's roots (GE-22)."""
        root = self.subtree(str(namespace))
        return root.children if root else ()

    def tags_in_namespace(
        self, namespace: Namespace | str, *, max_depth: int | None = None
    ) -> list[str]:
        """Tags inside one namespace, optionally capped by depth (TG-11).

        The bare namespace node is a structural root, not a usable tag, so it is excluded.
        """
        prefix = f"{namespace!s}."
        return [
            tag
            for tag in self.tags
            if tag.startswith(prefix) and (max_depth is None or tag.count(".") + 1 <= max_depth)
        ]


def build_tag_forest(tags: Iterable[str]) -> tuple[TagNode, ...]:
    """Build the node forest for a tag set, materializing implied ancestors (TG-5, TG-10).

    Siblings are sorted by :func:`tag_sort_key` at build time so every consumer — renderer, pack
    builder, golden test — sees one total order (GE-4, GE-23).
    """
    closed = ancestor_closure(tags)
    children: dict[str, list[str]] = {tag: [] for tag in closed}
    roots: list[str] = []
    for tag in closed:
        parent = tag.rpartition(".")[0]
        if parent in children:
            children[parent].append(tag)
        else:
            roots.append(tag)

    def node(tag: str) -> TagNode:
        return TagNode(tag, tuple(node(child) for child in sorted(children[tag], key=tag_sort_key)))

    return tuple(node(tag) for tag in sorted(roots, key=tag_sort_key))


def build_tag_tree(snapshot: KbSnapshot) -> TagTree:
    """Derive the tag tree from one KB walk (TG-10, TG-5, GE-3, GE-23).

    Takes a :class:`~pkb.core.models.KbSnapshot` rather than a ``kb_root`` (decision C): validation,
    generation and maintenance must all see the same tree, and three walks would drift.

    Only tags on non-derived markdown feed the tree — tags rendered *inside* ``tags.md`` are output,
    never evidence of use (GE-3). Invalid tags are excluded and reported in ``findings``: an
    ``UNKNOWN_TYPE_TAG`` rendered into the ontology would teach the next agent to repeat it.
    """
    used: dict[str, set[str]] = {}
    findings: list[Finding] = []

    for record in snapshot.content_files():
        meta = record.meta
        if meta is None:
            continue
        for raw in meta.tags:
            problems = validate_tag(raw, path=record.path)
            findings.extend(problems)
            if has_errors(problems):
                continue
            used.setdefault(raw, set()).add(record.path)

    files_by_tag = {
        tag: tuple(sorted(paths, key=_path_sort_key)) for tag, paths in sorted(used.items())
    }
    return TagTree(
        roots=build_tag_forest(used),
        files_by_tag=MappingProxyType(files_by_tag),
        findings=tuple(findings),
    )


def files_with_tag(snapshot: KbSnapshot, tag: str) -> list[str]:
    """KB-relative paths of the non-derived files tagged at or below ``tag`` (TG-11, TG-5).

    Honours parent implication: a file tagged only ``topic.cooking.grilling.charcoal`` answers a
    query for ``topic.cooking``. Whole-segment prefix match, so ``topic.cooking-extra`` does not.
    """
    prefix = f"{tag}."
    matches: set[str] = set()
    for record in snapshot.content_files():
        meta = record.meta
        if meta is None:
            continue
        if any(used == tag or used.startswith(prefix) for used in meta.tags):
            matches.add(record.path)
    return sorted(matches, key=_path_sort_key)


def _path_sort_key(path: str) -> tuple[str, str]:
    """KB-relative POSIX path order: case-insensitive, then codepoint (GE-4, GE-27)."""
    return (path.lower(), path)


# --------------------------------------------------------------------------------------
# Rendering (TG-12, TG-13, GE-23) — one renderer, two callers (GE-17)
# --------------------------------------------------------------------------------------


def render_tag_tree(
    nodes: Sequence[TagNode],
    *,
    level: int = 0,
    annotations: Mapping[str, str] = _NO_ANNOTATIONS,
) -> list[str]:
    """Render a tag forest as markdown bullet lines (GE-23, TG-13, TG-12).

    One line per node: ``- `` marker, the **full dotted tag** in backticks, then the caller's
    annotation verbatim; ``INDENT`` (4 spaces) per level below the section root. The renderer knows
    nothing about root topics, extension folders or static definitions — ``annotations`` maps a full
    tag to an already-formatted suffix (``ROOT_TOPIC_ANNOTATION``, :data:`EXTENSION_MARKER`,
    :data:`STATIC_ANNOTATIONS`), which keeps this function pure and the semantics in the generators.

    Siblings are re-sorted here as well as at build time, so output is invariant to input order
    (GE-32). Returns lines without terminators; the caller joins them (GE-7, GE-9).
    """
    lines: list[str] = []
    for node in sorted(nodes, key=lambda item: tag_sort_key(item.tag)):
        suffix = annotations.get(node.tag, "")
        lines.append(f"{INDENT * level}{BULLET}`{node.tag}`{suffix}")
        lines.extend(render_tag_tree(node.children, level=level + 1, annotations=annotations))
    return lines


def render_definition_list(definitions: Mapping[str, str | None] = STATIC_DEFINITIONS) -> list[str]:
    """Render a static definition block — the ``type`` section, the one closed vocabulary left
    (TG-12, TG-13, T-18).

    Deliberately *not* :func:`render_tag_tree`: this is a flat static list whose order is the
    vocabulary's own (``note``, ``reference``, ``solution``, ``summary``), which sorting would
    scramble. A ``None`` gloss renders bare — no separator, no text.

    Pass ``TYPE_DEFINITIONS`` (the default, ``STATIC_DEFINITIONS``, is the same mapping); the block
    is identical for an empty KB and a full one, because it is generator text rather than derived
    content (C17, GE-29).
    """
    return [
        f"{BULLET}`{tag}`" + (f"{TAG_DEF_SEP}{gloss}" if gloss else "")
        for tag, gloss in definitions.items()
    ]


def render_mapping_line(left: str, right: str) -> str:
    """One cross-topic mapping line (TG-13, GE-18, GE-20).

    Lives here so root ``tags.md`` and the topic indexes emit byte-identical lines for the same
    pair; orientation and ordering are the generators' call.
    """
    return f"{BULLET}`{left}`{MAPPING_SEP}`{right}`"
