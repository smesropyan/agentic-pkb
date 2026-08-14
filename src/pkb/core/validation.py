"""The mechanical rule engine over one file and over the whole tree (VA-1 … VA-41).

Two entry points, deliberately different in what they are allowed to touch:

* :func:`validate_content` is the **pre-write gate**. It is pure over ``(rel_path, text)`` and is
  correct for a path that does not exist yet, so Layer 2 can reject a proposed ``write_file``
  before it lands (VA-1). It never opens the file under test. It does consult the *tree* for the
  questions a path cannot answer alone — which topic root owns this path, and whether a
  ``related_topics`` target names a real topic.
* :func:`validate_tree` runs the same per-file rules over a :class:`~pkb.core.models.KbSnapshot`,
  plus the cross-file rules that need the whole tree: folder-hosted main files, duplicate note
  identity, media placement, dangling ``related_topics``, misplaced topic roots. The snapshot is
  always :func:`pkb.core.scan.scan`'s, supplied or not — decision C's single walk. This module
  walks nothing itself; it only lists directories, for the shape questions a file map cannot
  answer (an empty item folder has no file to be found by).

Every check is a small function named for the rule it implements (``_va9_tag_cardinality``), and
the dispatcher below picks the applicable set from ``paths.classify``'s ``(FileRole, FileClass)``.
Adding a rule means adding a function and one table entry; a reviewer can grep a rule id and land
on its implementation.

Three exemptions run through the dispatcher rather than through each rule:

* files the generators own (root ``index.md`` / ``tags.md``, a topic root's ``index.md``) and every
  other derived-by-name path are exempt from the required-field and tag checks (VA-5);
* ``skills/**`` is a third file class checked only for deepagents' ``name`` / ``description``
  (VA-6);
* non-markdown files are never parsed and never carry frontmatter findings (VA-7, FM-14).

Findings, never exceptions, for content defects (CX-5): one call reports every defect it can see,
so an agent gets the whole correction list at once instead of one problem per retry. Severity is
load-bearing — an ``error`` blocks the write and burns one of Layer 2's three attempts, so anything
a human can legitimately fix in a second edit (a review note that has not landed yet, a forward
reference to a topic that does not exist yet) is a ``warning``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pkb.core import diagnostics, frontmatter, paths, tags
from pkb.core.errors import (
    Finding,
    NotATopicRootError,
    Severity,
    sort_findings,
)
from pkb.core.models import (
    FileClass,
    FileRecord,
    FileRole,
    KbSnapshot,
    Metadata,
    ParsedDocument,
)
from pkb.core.scan import scan

__all__ = [
    "TYPE_TAG_FOR_SOURCE_TYPE",
    "validate_content",
    "validate_file",
    "validate_tree",
]


# --------------------------------------------------------------------------------------
# Location tables (VA-11, VA-13, VA-14)
# --------------------------------------------------------------------------------------

TYPE_TAG_FOR_SOURCE_TYPE: Mapping[str, str] = {
    source_type: f"{tags.Namespace.TYPE.value}.{source_type}"
    for source_type in sorted(frontmatter.AUTHORED_SOURCE_TYPES)
}
"""The ``source_type`` ↔ ``type.*`` bijection (VA-11).

Decision A keeps it exact: ``topic.md`` carries ``source_type: summary`` and ``type.summary``
rather than inventing a fifth enum value, so there is no special case to forget here.
"""

_SOURCE_TYPES_BY_ROLE: Mapping[FileRole, frozenset[str]] = {
    FileRole.TOPIC_OVERVIEW: frozenset({"summary"}),
    FileRole.NOTES_SUMMARY: frozenset({"summary"}),
    FileRole.REFERENCES_SUMMARY: frozenset({"summary"}),
    FileRole.NOTE: frozenset({"note", "solution"}),
    FileRole.REFERENCE: frozenset({"reference"}),
}
"""Location → ``source_type`` (VA-13). A role absent from the table constrains nothing."""

_TYPE_TAGS_BY_ROLE: Mapping[FileRole, frozenset[str]] = {
    role: frozenset(TYPE_TAG_FOR_SOURCE_TYPE[value] for value in allowed)
    for role, allowed in _SOURCE_TYPES_BY_ROLE.items()
}
"""Location → ``type.*`` (VA-14) — the same table as VA-13 pushed through the bijection.

Derived rather than typed out twice: the two rules are one table in the spec, and a hand-written
copy would eventually disagree with it.
"""

_ROLE_LABEL: Mapping[FileRole, str] = {
    FileRole.TOPIC_OVERVIEW: "a topic overview (topic.md)",
    FileRole.NOTES_SUMMARY: "the notes/ breadth summary",
    FileRole.REFERENCES_SUMMARY: "the references/ breadth summary",
    FileRole.NOTE: "a note under notes/",
    FileRole.REFERENCE: "a reference under references/",
}

_FIELD_HINTS: Mapping[str, str] = {
    "title": 'Add title: "…" — the display name every generated index links with.',
    "description": 'Add description: "…" — one line; it is the gloss the topic index renders.',
    "topic": 'Add topic: "…" — the display name of the folder holding topic.md.',
    "tags": "Add tags: at least one topic.* tag and exactly one type.* tag.",
    "created": "Add created: YYYY-MM-DD (a calendar date, unquoted, no time).",
    "updated": "Add updated: YYYY-MM-DD, on or after created.",
    "source_type": (
        "Add source_type: one of " + ", ".join(sorted(frontmatter.AUTHORED_SOURCE_TYPES)) + "."
    ),
}

_FORBIDDEN_CONFLICT_FIELDS: frozenset[str] = frozenset(
    {
        "confidence",
        "confidence_score",
        "loser",
        "losing_file",
        "resolution",
        "resolution_note",
        "resolution_text",
        "resolved",
        "resolved_at",
        "resolved_by",
        "supersedes",
        "superseded_by",
        "winner",
    }
)
"""Keys that would record that a conflict happened (VA-30).

Anything starting with ``conflict`` is forbidden too: the spec forbids a conflict registry at
every layer, and a frontmatter key is a registry with extra steps.
"""

_SECTION_EXCLUDED: frozenset[str] = frozenset(
    {paths.SUBTOPICS_DIR, paths.SKILLS_DIR, paths.MEDIA_DIR}
)
"""Directories directly under a topic root that host no items (PA-6, PA-7)."""

_TOPIC_NAMESPACE = tags.Namespace.TOPIC.value
_TYPE_NAMESPACE = tags.Namespace.TYPE.value


# --------------------------------------------------------------------------------------
# Rule context
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Context:
    """Everything a rule may read about one file, resolved once (decision C).

    ``meta`` is never ``None``: a file with no usable frontmatter gets an empty
    :class:`~pkb.core.models.Metadata`, and the dispatcher makes sure the field rules never run on
    one. That keeps fifteen rule bodies free of a ``None`` guard that would say nothing.
    """

    kb_root: Path
    path: str
    """KB-relative POSIX path — the form every :class:`Finding` carries."""

    role: FileRole
    file_class: FileClass
    doc: ParsedDocument | None
    """``None`` for non-markdown files, which are never parsed (FM-14)."""

    meta: Metadata
    topic_path: str | None
    """KB-relative path of the owning topic root, or ``None`` outside every topic (PA-15)."""

    topic_name: str | None
    """The owning topic root's display name — what the ``topic`` field must hold (VA-12, Q4)."""

    topic_tag: str | None
    """The owning topic root's ``topic.*`` tag — the prefix every topic tag must sit under."""

    inner: tuple[str, ...]
    """Path parts relative to the owning topic root; empty when there is no owner."""

    known_topic_tags: frozenset[str] | None = None
    """Every existing topic's tag, when the caller resolved it; ``None`` means "do not check"."""

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def stem(self) -> str:
        return PurePosixPath(self.path).stem

    @property
    def section(self) -> str | None:
        """``notes`` / ``references`` — the item-hosting sections (no extension folders, T-1)."""
        if len(self.inner) < 2 or self.inner[0] in _SECTION_EXCLUDED:
            return None
        return self.inner[0]

    def finding(
        self,
        code: str,
        severity: Severity,
        message: str,
        rule_id: str,
        *,
        field: str | None = None,
        value: str | None = None,
        line: int | None = None,
        hint: str | None = None,
    ) -> Finding:
        """A finding already carrying this file's path — every rule reports through here."""
        return Finding(
            code=code,
            severity=severity,
            message=message,
            rule_id=rule_id,
            path=self.path,
            field=field,
            value=value,
            line=line,
            hint=hint,
        )


_Rule = Callable[[_Context], list[Finding]]


# --------------------------------------------------------------------------------------
# Frontmatter presence and shape (VA-3, VA-4, VA-39, FM-4, FM-5)
# --------------------------------------------------------------------------------------


def _va39_frontmatter_parse_error(ctx: _Context) -> list[Finding]:
    """Unparseable YAML is a finding, never an exception (VA-39, FM-13).

    Reported alone: no other field rule can say anything useful about a block that did not load,
    and a wall of derived complaints would hide the syntax error that caused them.
    """
    if ctx.doc is None or ctx.doc.error is None:
        return []
    return [
        diagnostics.frontmatter_parse_error(ctx.path, ctx.doc.error, ctx.doc.error_line),
    ]


def _va3_missing_frontmatter(ctx: _Context) -> list[Finding]:
    """An authored markdown file with no frontmatter block, or an empty one (VA-3).

    Exactly one finding: the seven missing fields are a consequence, not seven separate defects.
    """
    return [
        ctx.finding(
            "MISSING_FRONTMATTER",
            Severity.ERROR,
            "The file carries no YAML frontmatter.",
            "VA-3",
            hint=(
                "Open the file with a --- block holding "
                + ", ".join(frontmatter.CANONICAL_ORDER[:6])
                + " and source_type, then close it with ---."
            ),
        )
    ]


def _va4_required_fields(ctx: _Context) -> list[Finding]:
    """Each of the seven required fields, absent or empty, named individually (VA-4, FM-2).

    "Empty" is a *present* key whose value did not survive coercion as an empty value — the parser
    records that as an ``EMPTY_FIELD`` problem, so ``description: ""`` is reported here rather than
    as a type error.
    """
    empty = {problem.field for problem in ctx.meta.bad_fields if problem.code == "EMPTY_FIELD"}
    findings = []
    for name in frontmatter.CANONICAL_ORDER:
        if name not in frontmatter.REQUIRED_FIELDS:
            continue
        if ctx.meta.has(name) and name not in empty:
            continue
        findings.append(
            ctx.finding(
                "MISSING_REQUIRED_FIELD",
                Severity.ERROR,
                f"Required frontmatter field {name!r} is missing or empty.",
                "VA-4",
                field=name,
                hint=_FIELD_HINTS.get(name),
            )
        )
    return findings


def _fm4_field_types(ctx: _Context) -> list[Finding]:
    """Known fields whose value has the wrong type or date shape (FM-4, FM-5).

    The parser is total: it leaves the typed attribute empty and records a ``FieldProblem`` so this
    layer can turn it into a finding (CX-5). ``EMPTY_FIELD`` problems are skipped — VA-4 owns them
    for required fields.
    """
    findings = []
    for problem in ctx.meta.bad_fields:
        if problem.code == "EMPTY_FIELD":
            continue
        findings.append(
            ctx.finding(
                problem.code,
                Severity.ERROR,
                f"Frontmatter field {problem.field!r} has the wrong shape: {problem.detail}.",
                "FM-5" if problem.code == "DATE_FORMAT" else "FM-4",
                field=problem.field,
                hint=_FIELD_HINTS.get(problem.field),
            )
        )
    return findings


def _va26_description_shape(ctx: _Context) -> list[Finding]:
    """``description`` is a single line (VA-26).

    The topic index renders it inline after an em dash; a newline there would break the bullet, and
    GE-26's collapse would silently change what the human wrote.
    """
    description = ctx.meta.description
    if description is None or ("\n" not in description and "\r" not in description):
        return []
    return [
        ctx.finding(
            "MULTILINE_DESCRIPTION",
            Severity.ERROR,
            "The description spans more than one line.",
            "VA-26",
            field="description",
            value=" ".join(description.split()),
            hint="Rewrite the description as one line; move the detail into the body.",
        )
    ]


def _va30_forbidden_conflict_fields(ctx: _Context) -> list[Finding]:
    """No frontmatter key may record that a conflict happened (VA-30).

    The spec forbids a conflict registry, resolution log, loser marker and confidence score at
    every layer; a frontmatter key is all of those in miniature.
    """
    return [
        ctx.finding(
            "FORBIDDEN_CONFLICT_FIELD",
            Severity.ERROR,
            f"Frontmatter key {key!r} records conflict state, which is never stored in the KB.",
            "VA-30",
            field=key,
            hint="Remove the key; the spec forbids a conflict registry at every layer.",
        )
        for key in ctx.meta.unknown_fields
        if _is_conflict_residue(key)
    ]


def _is_conflict_residue(key: str) -> bool:
    normalized = "".join(character if character.isalnum() else "_" for character in key).lower()
    return normalized.startswith("conflict") or normalized in _FORBIDDEN_CONFLICT_FIELDS


def _va31_reserved_source_type(ctx: _Context) -> list[Finding]:
    """A generated-file ``source_type`` on an authored file (VA-31, FM-6)."""
    value = ctx.meta.source_type
    if value is None or value not in frontmatter.DERIVED_SOURCE_TYPES:
        return []
    return [
        ctx.finding(
            "RESERVED_SOURCE_TYPE",
            Severity.ERROR,
            f"source_type {value!r} is reserved for generated files.",
            "VA-31",
            field="source_type",
            value=value,
            hint=(
                "Authored files use "
                + ", ".join(sorted(frontmatter.AUTHORED_SOURCE_TYPES))
                + "; index, catalog and tag-registry belong to the generators."
            ),
        )
    ]


def _fm6_unknown_source_type(ctx: _Context) -> list[Finding]:
    """``source_type`` is a closed enum (FM-6).

    Vocabulary is checked here rather than at parse time: the parser must let the offending value
    survive so this message can quote it.
    """
    value = ctx.meta.source_type
    if value is None or value in frontmatter.SOURCE_TYPES:
        return []
    return [
        ctx.finding(
            "UNKNOWN_SOURCE_TYPE",
            Severity.ERROR,
            f"source_type {value!r} is not a recognized value.",
            "FM-6",
            field="source_type",
            value=value,
            hint="Use one of " + ", ".join(sorted(frontmatter.AUTHORED_SOURCE_TYPES)) + ".",
        )
    ]


def _va32_unknown_fields(ctx: _Context) -> list[Finding]:
    """Unrecognized frontmatter keys are preserved and reported (VA-32, FM-10).

    A warning rather than an error because a file can legitimately carry a domain-specific key
    outside the seven-field schema; the value of the check is catching ``descripton:``, which would
    otherwise surface only as a confusing missing-field error. Keys VA-30 rejects are left to VA-30
    so one defect yields one finding.
    """
    return [
        ctx.finding(
            "UNKNOWN_FIELD",
            Severity.WARNING,
            f"Frontmatter key {key!r} is not part of the PKB schema.",
            "VA-32",
            field=key,
            hint=(
                "Check for a typo of a known field ("
                + ", ".join(frontmatter.CANONICAL_ORDER[:4])
                + ", …); the key is preserved either way."
            ),
        )
        for key in ctx.meta.unknown_fields
        if not _is_conflict_residue(key)
    ]


def _va33_related_topics_prefixed(ctx: _Context) -> list[Finding]:
    """A ``related_topics`` entry that already carries a namespace prefix (VA-33, FM-15).

    Normalized rather than rejected, so the registry renders the same mapping either way; the
    warning is what makes the corpus converge on the canonical unprefixed form.
    """
    findings = []
    for value in ctx.meta.related_topics:
        head = value.strip().split(".", 1)[0]
        if head not in {namespace.value for namespace in tags.Namespace}:
            continue
        findings.append(
            ctx.finding(
                "RELATED_TOPICS_PREFIXED",
                Severity.WARNING,
                f"related_topics entry {value!r} already carries a namespace prefix.",
                "VA-33",
                field="related_topics",
                value=value,
                hint=(
                    "Write the bare topic path, e.g. "
                    f"{frontmatter.normalize_related_topic(value).removeprefix('topic.')!r}."
                ),
            )
        )
    return findings


def _va34_dangling_related_topic(ctx: _Context) -> list[Finding]:
    """A ``related_topics`` target that names no existing topic root (VA-34).

    Never an error: forward references to topics that do not exist yet are legitimate during
    bootstrapping, and the mapping still renders, so a missing target costs nothing but the
    warning.
    """
    if ctx.known_topic_tags is None:
        return []
    findings = []
    for value in ctx.meta.related_topics:
        normalized = frontmatter.normalize_related_topic(value)
        if not normalized or normalized in ctx.known_topic_tags:
            continue
        findings.append(
            ctx.finding(
                "DANGLING_RELATED_TOPIC",
                Severity.WARNING,
                f"related_topics entry {value!r} resolves to {normalized!r}, "
                "which no topic root carries.",
                "VA-34",
                field="related_topics",
                value=value,
                hint="Create the topic, or fix the spelling; the mapping renders either way.",
            )
        )
    return findings


def _va35_filename_title_divergence(ctx: _Context) -> list[Finding]:
    """The file's stem should slugify to the same thing as its ``title`` (VA-35).

    Warning only, and only for item files. The one naming rule both source documents state is
    folder name == main file stem (VA-16); hard-failing on a title would force a rename, and
    without version control a rename is destructive.
    """
    title = ctx.meta.title
    if title is None or ctx.role not in {FileRole.NOTE, FileRole.REFERENCE}:
        return []
    expected = paths.slugify(title)
    if not expected or paths.slugify(ctx.stem) == expected:
        return []
    return [
        ctx.finding(
            "FILENAME_TITLE_DIVERGENCE",
            Severity.WARNING,
            f"The file name {ctx.name!r} does not match its title {title!r}.",
            "VA-35",
            field="title",
            value=title,
            hint=f"Consider naming the file {expected}{paths.MARKDOWN_SUFFIX}.",
        )
    ]


# --------------------------------------------------------------------------------------
# Tags (VA-8, VA-9, VA-10, VA-11)
# --------------------------------------------------------------------------------------


def _unique_tags(meta: Metadata) -> list[str]:
    """The declared tags, first-seen order, duplicates collapsed (VA-10 reports the duplicates)."""
    seen: dict[str, None] = {}
    for value in meta.tags:
        seen.setdefault(value, None)
    return list(seen)


def _in_namespace(meta: Metadata, namespace: str) -> list[str]:
    """Declared tags whose first segment is exactly ``namespace``.

    Membership is decided on the raw first segment, not on validity: ``Type.Note`` is not a
    ``type.*`` tag, so a file carrying only that one is genuinely missing its type tag and gets
    both findings.
    """
    return [value for value in _unique_tags(meta) if value.split(".", 1)[0] == namespace]


def _va8_tag_syntax_and_vocabulary(ctx: _Context) -> list[Finding]:
    """Namespace, syntax, depth, and the two closed vocabularies, per tag (VA-8, TG-2…TG-7).

    Delegated to :func:`pkb.core.tags.validate_tag` so the tag rules have exactly one
    implementation. ``topic.*`` and ``domain.*`` are open and never produce a vocabulary finding
    (VA-40, TG-8, TG-9).
    """
    return [
        finding
        for value in _unique_tags(ctx.meta)
        for finding in tags.validate_tag(value, path=ctx.path)
    ]


def _va9_tag_cardinality(ctx: _Context) -> list[Finding]:
    """At least one ``topic.*`` tag and exactly one ``type.*`` tag (T-19, formerly VA-9).

    Errors rather than warnings: a file with no ``topic.*`` tag cannot be placed in the registry or
    a topic index, and a file carrying two ``type.*`` tags contradicts itself about what it is.
    There is no ``status.*`` cardinality to check — T-17 retires the namespace outright, and a
    ``status.*`` tag on a file is reported once, as an unknown namespace (VA-8), not here.

    **Amended 2026-08-14 (P5).** The ``topic.*`` floor does not bind ``FileRole.SESSION``: T-21
    already ties a session file's ``topic.*`` tags to "one per expert that took part," and a
    session opened directly on the Librarian, before any Topic Expert has joined it, has taken
    part with zero — a valid state, not ``MISSING_TOPIC_TAG``
    (``docs/superpowers/plans/2026-08-14-phase2-sessions.md``, "Three rulings," P5;
    ``docs/superpowers/specs/2026-08-13-tree-T-rules.md``, T-19's own amendment). Only that one
    finding is scoped away — the ``type.*`` floor below is unconditional, on a session file as on
    every other, and a session file that *does* carry a ``topic.*`` tag is checked exactly like
    any other file's.

    Silent when the field is missing (VA-4 said so) or unusable — ``tags: "topic.cooking"`` is one
    defect, and answering it with more cardinality errors buries the fix.
    """
    unusable = any(problem.field == "tags" for problem in ctx.meta.bad_fields)
    if not ctx.meta.has("tags") or unusable:
        return []

    findings = []
    if ctx.role is not FileRole.SESSION and not _in_namespace(ctx.meta, _TOPIC_NAMESPACE):
        findings.append(
            ctx.finding(
                "MISSING_TOPIC_TAG",
                Severity.ERROR,
                "The file carries no topic.* tag.",
                "T-19",
                field="tags",
                hint=(
                    f"Add {ctx.topic_tag}."
                    if ctx.topic_tag
                    else "Add the topic.* tag of the folder that owns this file."
                ),
            )
        )

    declared = _in_namespace(ctx.meta, _TYPE_NAMESPACE)
    if not declared:
        findings.append(
            ctx.finding(
                "MISSING_TYPE_TAG",
                Severity.ERROR,
                f"The file carries no {_TYPE_NAMESPACE}.* tag; exactly one is required.",
                "T-19",
                field="tags",
                hint=_cardinality_hint(ctx),
            )
        )
    elif len(declared) > 1:
        findings.append(
            ctx.finding(
                "MULTIPLE_TYPE_TAGS",
                Severity.ERROR,
                f"The file carries {len(declared)} {_TYPE_NAMESPACE}.* tags "
                f"({', '.join(declared)}); exactly one is allowed.",
                "T-19",
                field="tags",
                value=", ".join(declared),
                hint=(
                    f"Keep the one {_TYPE_NAMESPACE}.* tag that describes the file and drop the "
                    "rest."
                ),
            )
        )
    return findings


def _cardinality_hint(ctx: _Context) -> str:
    expected = _TYPE_TAGS_BY_ROLE.get(ctx.role)
    if expected:
        return "Add " + " or ".join(sorted(expected)) + " to match this file's location."
    return "Add one of " + ", ".join(sorted(tags.TYPE_TAGS)) + "."


def _va10_duplicate_tags(ctx: _Context) -> list[Finding]:
    """The same tag listed twice (VA-10).

    Warning: the registry is a set-derived tree, so a duplicate carries no meaning and changes no
    output — it is noise to clean up, not a reason to reject a write.
    """
    counts: dict[str, int] = {}
    for value in ctx.meta.tags:
        counts[value] = counts.get(value, 0) + 1
    return [
        ctx.finding(
            "DUPLICATE_TAG",
            Severity.WARNING,
            f"Tag {value!r} is listed {count} times.",
            "VA-10",
            field="tags",
            value=value,
            hint="Remove the duplicate entry.",
        )
        for value, count in counts.items()
        if count > 1
    ]


def _va11_source_type_tag_bijection(ctx: _Context) -> list[Finding]:
    """``source_type`` and the ``type.*`` tag say the same thing (VA-11)."""
    source_type = ctx.meta.source_type
    declared = _in_namespace(ctx.meta, _TYPE_NAMESPACE)
    if source_type is None or len(declared) != 1:
        return []
    expected = TYPE_TAG_FOR_SOURCE_TYPE.get(source_type)
    if expected is None:
        return []  # VA-9 / VA-31 / FM-6 own an unusable source_type.
    if declared[0] == expected:
        return []
    return [
        ctx.finding(
            "SOURCE_TYPE_TAG_MISMATCH",
            Severity.ERROR,
            f"source_type {source_type!r} pairs with {expected}, but the file is tagged "
            f"{declared[0]}.",
            "VA-11",
            field="tags",
            value=declared[0],
            hint=f"Set the tag to {expected}, or change source_type to match the tag.",
        )
    ]


# --------------------------------------------------------------------------------------
# Location consistency (VA-12, VA-13, VA-14, VA-15)
# --------------------------------------------------------------------------------------


def _va12_topic_field(ctx: _Context) -> list[Finding]:
    """The ``topic`` field is the display name of the owning topic root (VA-12, Q4).

    A display name, not a tag path and not a filesystem path: the ``topic.*`` tag already carries
    the machine-checkable location (VA-15), so this field is the human-readable owner. Compared
    through :func:`~pkb.core.paths.slugify` so ``Heat Management`` and ``heat management`` agree.
    """
    declared = ctx.meta.topic
    if declared is None or ctx.topic_name is None:
        return []

    head = declared.strip().split(".", 1)[0]
    looks_like_a_tag = head in {namespace.value for namespace in tags.Namespace} and "." in declared
    if looks_like_a_tag or "/" in declared or "\\" in declared:
        return [
            ctx.finding(
                "TOPIC_FIELD_FORMAT",
                Severity.ERROR,
                f"topic must be a display name, not a tag or a path; got {declared!r}.",
                "VA-12",
                field="topic",
                value=declared,
                hint=f'Set topic: "{ctx.topic_name}".',
            )
        ]
    if paths.slugify(declared) == paths.slugify(ctx.topic_name):
        return []
    return [
        ctx.finding(
            "TOPIC_LOCATION_MISMATCH",
            Severity.ERROR,
            f"topic is {declared!r} but the file lives under the topic root {ctx.topic_path!r}.",
            "VA-12",
            field="topic",
            value=declared,
            hint=f'Set topic: "{ctx.topic_name}", or move the file into the {declared} topic.',
        )
    ]


def _va13_source_type_location(ctx: _Context) -> list[Finding]:
    """Location decides which ``source_type`` values are legal (VA-13)."""
    allowed = _SOURCE_TYPES_BY_ROLE.get(ctx.role)
    value = ctx.meta.source_type
    if allowed is None or value is None or value not in frontmatter.SOURCE_TYPES:
        return []
    if value in allowed:
        return []
    return [
        ctx.finding(
            "SOURCE_TYPE_LOCATION_MISMATCH",
            Severity.ERROR,
            f"{_ROLE_LABEL[ctx.role]} must declare source_type "
            f"{' or '.join(sorted(allowed))}; this file declares {value!r}.",
            "VA-13",
            field="source_type",
            value=value,
            hint=(
                f"Set source_type: {sorted(allowed)[0]}, or move the file to where "
                f"{value!r} belongs."
            ),
        )
    ]


def _va14_type_tag_location(ctx: _Context) -> list[Finding]:
    """Location decides which ``type.*`` tag is legal — VA-13's table as tags (VA-14).

    A ``type.solution`` file lives under ``notes/``, never under ``references/``: a solution is
    experience, and references are static source material.
    """
    allowed = _TYPE_TAGS_BY_ROLE.get(ctx.role)
    if allowed is None:
        return []
    return [
        ctx.finding(
            "TYPE_TAG_LOCATION_MISMATCH",
            Severity.ERROR,
            f"{_ROLE_LABEL[ctx.role]} cannot be tagged {value}; "
            f"expected {' or '.join(sorted(allowed))}.",
            "VA-14",
            field="tags",
            value=value,
            hint=f"Replace {value} with {sorted(allowed)[0]}, or move the file.",
        )
        for value in _in_namespace(ctx.meta, _TYPE_NAMESPACE)
        if value in tags.TYPE_TAGS and value not in allowed
    ]


def _va15_topic_tag_location(ctx: _Context) -> list[Finding]:
    """Every ``topic.*`` tag sits at or below the owning topic root's tag (VA-15).

    Prefix containment, not folder existence: ``topic.cooking.heat-management`` is legal in
    ``Cooking/notes/`` whether or not a ``Heat Management`` folder exists, because tags describe
    subject matter at a finer grain than the tree.
    """
    if ctx.topic_tag is None:
        return []
    return [
        ctx.finding(
            "TOPIC_TAG_LOCATION_MISMATCH",
            Severity.ERROR,
            f"Tag {value} is outside this file's topic; it lives under {ctx.topic_tag}.",
            "VA-15",
            field="tags",
            value=value,
            hint=(
                f"Use {ctx.topic_tag} or a tag below it, or file the content under the topic the "
                "tag names."
            ),
        )
        for value in _in_namespace(ctx.meta, _TOPIC_NAMESPACE)
        if not tags.Tag.parse(value).is_descendant_of(ctx.topic_tag)
    ]


# --------------------------------------------------------------------------------------
# Path shape (VA-17 … VA-20, VA-25, VA-27, VA-37, VA-38)
# --------------------------------------------------------------------------------------


def _offending_item_name(ctx: _Context) -> str | None:
    """The outermost reserved name used as an item name, or ``None`` (PA-19).

    Only the outermost is reported: ``notes/summary/summary.md`` is one mistake — an item called
    ``summary`` — not two. ``<section>/summary.md`` is the legal breadth file and never matches.
    """
    rest = ctx.inner[1:]
    if ctx.section is None or not rest or rest == (paths.SUMMARY_FILE,):
        return None
    for index, segment in enumerate(rest):
        is_leaf = index == len(rest) - 1
        name = segment.removesuffix(paths.MARKDOWN_SUFFIX) if is_leaf else segment
        if paths.is_reserved_item_name(name):
            return name
    return None


def _va17_item_named_index(ctx: _Context) -> list[Finding]:
    """An item's content file may never be named ``index.md``, at any depth (VA-17).

    Checked from the path alone, regardless of frontmatter: ``index.md`` is derived by name
    everywhere (PA-11), so an item called ``index`` shadows the machine-generated index and is
    denied to agents by Layer 2 — but nothing regenerates it, so Layer 1 must say why it is wrong.

    Deliberately *not* routed through :func:`_offending_item_name`, which answers a narrower
    question — the outermost reserved name inside an item-hosting section. PA-12 states this rule's
    set without reference to sections: "an ``index.md`` that is derived-by-name but not generated
    is a validation error (VA-17)", with ``notes/x/index.md`` as an example rather than a scope. An
    ``index.md`` under ``media/``, ``sub-topics/`` or ``skills/``, or outside every topic, is
    exactly as invisible — exempt from the authored schema (VA-5), on Layer 2's deny list (PA-11),
    in no index (GE-15) and maintained by nobody — so the section notion must not gate it.

    No extra guard is needed and none should be added: :func:`_rules_result` returns before the
    path rules for every :func:`~pkb.core.paths.is_generated` path, so any context reaching here
    named ``index.md`` is derived-by-name-but-not-generated by construction — PA-12's set exactly.
    """
    if ctx.name != paths.INDEX_FILE:
        return []
    return [
        ctx.finding(
            "ITEM_NAMED_INDEX",
            Severity.ERROR,
            "index.md is the machine-generated topic index and may not hold item content.",
            "VA-17",
            hint=(
                "Rename the file after the item itself — notes/<item>/<item>.md — and leave "
                "index.md to the generator."
            ),
        )
    ]


def _va18_reserved_summary_name(ctx: _Context) -> list[Finding]:
    """``summary.md`` is a breadth file, not an item name (VA-18).

    Legal at ``notes/summary.md`` and ``references/summary.md`` — the only two sections a topic
    root recognizes (T-1). ``_offending_item_name`` exempts a ``summary.md`` under any other
    directory too, since it does not itself judge whether the section is recognized; an unrecognized
    directory is its own defect, reported once as ``UNEXPECTED_TOPIC_ENTRY`` (T-1) rather than here.
    """
    if _offending_item_name(ctx) != paths.SUMMARY_FILE.removesuffix(paths.MARKDOWN_SUFFIX):
        return []
    return [
        ctx.finding(
            "RESERVED_NAME_AS_ITEM",
            Severity.ERROR,
            "'summary' is the reserved breadth-file name and cannot name an item.",
            "VA-18",
            value="summary",
            hint=(
                f"Rename the item; the only legal summaries are {ctx.section}/summary.md and its "
                "siblings at the section root."
            ),
        )
    ]


def _va19_reserved_item_name(ctx: _Context) -> list[Finding]:
    """Any other structural name used as an item name (VA-19, PA-19)."""
    name = _offending_item_name(ctx)
    if name is None or name in {"index", "summary"}:
        return []
    return [
        ctx.finding(
            "RESERVED_NAME_AS_ITEM",
            Severity.ERROR,
            f"{name!r} is a reserved structural name and cannot name an item.",
            "VA-19",
            value=name,
            hint=(
                "Reserved names are "
                + ", ".join(sorted(paths.RESERVED_NAMES))
                + "; pick a name describing the content."
            ),
        )
    ]


def _va20_misplaced_expert(ctx: _Context) -> list[Finding]:
    """``expert.md`` is valid only directly at a topic root (VA-20).

    Checked by name against the role, because ``classify`` decides by location: a
    ``notes/expert.md`` classifies as a note, which is exactly the confusion this finding removes.
    """
    if ctx.name != paths.EXPERT_FILE or ctx.role is FileRole.EXPERT:
        return []
    return [
        ctx.finding(
            "MISPLACED_RESERVED_FILE",
            Severity.ERROR,
            "expert.md overrides the Topic Expert and is only read at a topic root.",
            "VA-20",
            hint=(
                f"Move it to {ctx.topic_path}/{paths.EXPERT_FILE}."
                if ctx.topic_path
                else "Move it to the topic root it is meant to govern."
            ),
        )
    ]


def _va25_standalone_reference(ctx: _Context) -> list[Finding]:
    """A reference that is not folder-hosted (VA-25, Q7).

    Warning, not error: the tree diagram shows references only in folder form, but a URL-only
    reference has nothing to put in a folder, so the asymmetry is flagged rather than enforced.
    """
    if ctx.role is not FileRole.REFERENCE or len(ctx.inner) != 2:
        return []
    return [
        ctx.finding(
            "REFERENCE_NOT_FOLDER_HOSTED",
            Severity.WARNING,
            "References are normally folder-hosted so their source files travel with them.",
            "VA-25",
            hint=f"Consider {paths.REFERENCES_DIR}/{ctx.stem}/{ctx.name} instead.",
        )
    ]


def _va27_topic_tags_file(ctx: _Context) -> list[Finding]:
    """There is no per-topic tag registry (VA-27, Q20).

    ``tags.md`` exists once, at the knowledge-base root. A topic-level one is maintained by nobody
    and drifts silently — and Layer 1 will never regenerate or delete it, so the finding is the
    only signal the human gets.
    """
    if ctx.inner != (paths.TAGS_FILE,):
        return []
    return [
        ctx.finding(
            "RESERVED_TOPIC_TAGS_FILE",
            Severity.ERROR,
            "tags.md is the root tag registry; topics have no tag file of their own.",
            "VA-27",
            hint=(
                "Delete it — the topic's tag subtree is rendered into its generated index.md, and "
                "the global registry lives at the KB root."
            ),
        )
    ]


def _va37_topic_depth(ctx: _Context) -> list[Finding]:
    """A topic root too deep for a legal ``topic.*`` tag (VA-37, Q15).

    Warning at validation time so existing trees are not broken; the scaffolder refuses outright
    (SC-9). Every file inside such a topic additionally fails VA-15, because no legal tag can
    express its location.
    """
    if ctx.role is not FileRole.TOPIC_OVERVIEW or ctx.topic_tag is None:
        return []
    depth = ctx.topic_tag.count(".") + 1
    if depth <= tags.MAX_TAG_DEPTH:
        return []
    return [
        ctx.finding(
            "TOPIC_PATH_EXCEEDS_TAG_DEPTH",
            Severity.WARNING,
            f"This topic's tag {ctx.topic_tag} needs {depth} segments; the limit is "
            f"{tags.MAX_TAG_DEPTH}.",
            "VA-37",
            value=ctx.topic_tag,
            hint=(
                "Flatten the sub-topic nesting: no file inside this topic can carry a "
                "location-consistent topic.* tag."
            ),
        )
    ]


def _va38_unexpected_topic_root_file(ctx: _Context) -> list[Finding]:
    """A loose file — markdown or asset — directly at a topic root (VA-38).

    Only ``topic.md``, ``index.md`` and ``expert.md`` belong there; content belongs in ``notes/``
    or ``references/``. Unknown *directories* are a separate finding, ``UNEXPECTED_TOPIC_ENTRY``
    (T-1, :func:`_t1_unexpected_topic_entry`) — there is no extension-folder mechanism any more, so
    this rule stays about loose files, matching its file-vs-directory contrast below. ``tags.md`` is
    left to VA-27, which names the real fix.

    The rule's contrast is file-vs-directory, so a ``Cooking/photo.jpg`` is in scope: VA-7 and
    FM-14 exempt non-markdown from *frontmatter* validation, not from the path rules, and no other
    rule can see a topic-root asset — GE-15 keeps assets out of every index and MA-8 scopes
    ``ORPHAN_ASSET`` to ``media/`` and reference folders.
    """
    expected = {paths.TOPIC_FILE, paths.INDEX_FILE, paths.EXPERT_FILE, paths.TAGS_FILE}
    if len(ctx.inner) != 1 or ctx.name in expected:
        return []
    return [
        ctx.finding(
            "UNEXPECTED_TOPIC_ROOT_FILE",
            Severity.WARNING,
            f"{ctx.name!r} sits directly at a topic root, where only topic.md, index.md and "
            "expert.md belong.",
            "VA-38",
            hint=f"Move it under {paths.NOTES_DIR}/ or {paths.REFERENCES_DIR}/.",
        )
    ]


# --------------------------------------------------------------------------------------
# Skills (VA-6, PA-14)
# --------------------------------------------------------------------------------------


def _va6_skill_fields(ctx: _Context) -> list[Finding]:
    """``SKILL.md`` carries deepagents' own schema: ``name`` and ``description`` (VA-6, C3).

    The seven PKB fields do not apply — forcing them would break deepagents' parsing — and skills
    participate in no index and no tag tree. ``expert.md`` is exempt from this check too: it is a
    prompt override, not a deepagents skill.
    """
    if ctx.name != paths.SKILL_FILE:
        return []
    raw = ctx.doc.raw if ctx.doc else None
    return [
        ctx.finding(
            "MISSING_SKILL_FIELD",
            Severity.ERROR,
            f"SKILL.md must declare {name!r} in its frontmatter.",
            "VA-6",
            field=name,
            hint=f"Add {name}: … — deepagents reads it to decide when to load the skill.",
        )
        for name in ("name", "description")
        if raw is None or not str(raw.get(name, "")).strip()
    ]


# --------------------------------------------------------------------------------------
# Rule tables and the dispatcher
# --------------------------------------------------------------------------------------

_TERMINAL_PATH_RULES: tuple[_Rule, ...] = (
    _va20_misplaced_expert,
    _va27_topic_tags_file,
)
"""Rules whose finding means the file does not belong at this path at all.

They short-circuit: telling an agent to add seven frontmatter fields to a ``notes/expert.md`` — a
file that carries no PKB frontmatter once it is in the right place — would send it in the wrong
direction, and one clear instruction beats a correct-but-misleading list.
"""

_PATH_RULES: tuple[_Rule, ...] = (
    _va17_item_named_index,
    _va18_reserved_summary_name,
    _va19_reserved_item_name,
    _va25_standalone_reference,
    _va37_topic_depth,
    _va38_unexpected_topic_root_file,
)
"""Rules decided by the path alone — they apply to every markdown file a generator does not own."""

_CONTENT_RULES: tuple[_Rule, ...] = (
    _va4_required_fields,
    _fm4_field_types,
    _va26_description_shape,
    _va30_forbidden_conflict_fields,
    _va31_reserved_source_type,
    _fm6_unknown_source_type,
    _va32_unknown_fields,
    _va33_related_topics_prefixed,
    _va34_dangling_related_topic,
    _va35_filename_title_divergence,
    _va8_tag_syntax_and_vocabulary,
    _va9_tag_cardinality,
    _va10_duplicate_tags,
    _va11_source_type_tag_bijection,
    _va12_topic_field,
    _va13_source_type_location,
    _va14_type_tag_location,
    _va15_topic_tag_location,
)
"""Rules over an authored file's frontmatter. ``topic.md`` runs the full set — it is not exempt
(VA-41): the root catalog reads its frontmatter, so a degraded one degrades the catalog."""

_SKILL_RULES: tuple[_Rule, ...] = (_va6_skill_fields,)


def _apply(rules: Sequence[_Rule], ctx: _Context) -> list[Finding]:
    return [finding for rule in rules for finding in rule(ctx)]


def _rules_result(ctx: _Context) -> list[Finding]:
    """Pick and run the applicable rule set for one file (VA-5, VA-6, VA-7).

    The exemption ladder, in order:

    1. a non-markdown file is exempt from *frontmatter* validation and from nothing else — it keeps
       VA-38, which contrasts loose files with directories rather than markdown with assets (VA-7,
       FM-14); ignored entries are skipped entirely (PA-16);
    2. the three artifacts the generators own produce no findings at all — validating our own
       output against the authored-file schema is how a validator ends up rejecting its own
       generator (C4);
    3. a file whose *name* says it is in the wrong place stops there (VA-20, VA-27);
    4. everything else derived *by name* (``notes/x/index.md``) keeps the path rules, which is
       where VA-17 lives, but is exempt from required fields and tag checks (VA-5, C14);
    5. ``skills/**`` gets the deepagents check instead of the PKB one (VA-6);
    6. an unparseable or absent block short-circuits to a single finding (VA-39, VA-3).

    The ASSET branch has to come *before* the ``doc is None`` test rather than beside it: a
    non-markdown file is never parsed, so ``ctx.doc`` is always ``None`` for one, and a combined
    short-circuit would keep swallowing VA-38. Only VA-38 is run, not the whole path table — the
    rest of it reads item *names*, and ``Cooking/recipes/topic/photo.jpg`` would draw a
    ``RESERVED_NAME_AS_ITEM`` for its folder that VA-19 does not mean.
    """
    if ctx.file_class is FileClass.ASSET:
        return _va38_unexpected_topic_root_file(ctx)
    if ctx.doc is None or ctx.file_class is FileClass.IGNORED:
        return []
    if paths.is_generated(ctx.kb_root, ctx.kb_root / ctx.path):
        return []

    terminal = _apply(_TERMINAL_PATH_RULES, ctx)
    if terminal:
        return terminal

    findings = _apply(_PATH_RULES, ctx)
    if ctx.file_class is FileClass.DERIVED:
        return findings
    if ctx.file_class is FileClass.SKILL:
        return findings + _apply(_SKILL_RULES, ctx)
    if ctx.doc.error is not None:
        return findings + _va39_frontmatter_parse_error(ctx)
    if ctx.doc.meta is None or not ctx.doc.meta.present_keys:
        return findings + _va3_missing_frontmatter(ctx)
    return findings + _apply(_CONTENT_RULES, ctx)


# --------------------------------------------------------------------------------------
# Context construction
# --------------------------------------------------------------------------------------


def _topic_tag(kb_root: Path, topic_path: str) -> str | None:
    try:
        return paths.topic_tag_for(kb_root, kb_root / topic_path)
    except (NotATopicRootError, ValueError):
        return None


def _inner_parts(path: str, topic_path: str | None) -> tuple[str, ...]:
    if topic_path is None:
        return ()
    return PurePosixPath(path).relative_to(topic_path).parts


def _known_topic_tags(kb_root: Path) -> frozenset[str]:
    """Every tag an existing topic root carries — the VA-34 lookup table.

    Misplaced topic roots are included: a topic under ``notes/`` is a structural warning (VA-36),
    not a reason to call every reference to it dangling.

    This is the one place :func:`validate_content` consults the tree, so it walks *paths* rather
    than taking :func:`pkb.core.scan.scan`'s view: VA-1 makes the pre-write gate pure over
    ``(rel_path, text)`` and Layer 2 runs it on every proposed write, and a snapshot would open and
    parse every markdown file in the knowledge base to answer one lookup. Deliberately the widest
    plausible topic set, so the answer errs toward *silence* — VA-34 is a warning about forward
    references, and a false "no topic carries that tag" is worse than a missed one.
    """
    found = set()
    for topic in paths.find_topic_roots(kb_root, include_misplaced=True):
        try:
            found.add(paths.topic_tag_for(kb_root, topic))
        except NotATopicRootError:
            continue
    return frozenset(found)


def _context_for_text(kb_root: Path, rel_path: str, text: str) -> _Context:
    """Resolve one proposed write into a rule context (VA-1).

    The file under test is never opened — the caller already holds its bytes. The tree is consulted
    only for ownership and for the ``related_topics`` lookup.

    One override earns its keep: a ``topic.md`` that does not exist yet *makes* its directory a
    topic root, but ``classify`` decides from what is on disk and would call it an unknown file
    outside every topic. Without the override the scaffolder's own placeholder could not be
    validated before it is written (SC-3).
    """
    normalized = PurePosixPath(rel_path.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError(f"{rel_path!r} is not a knowledge-base-relative path")
    path = normalized.as_posix()
    abs_path = kb_root / normalized

    role, file_class = paths.classify(kb_root, abs_path)
    owner = paths.owning_topic_root(kb_root, abs_path)
    if role is FileRole.UNKNOWN and _creates_a_topic_root(normalized):
        owner = abs_path.parent
        role, file_class = FileRole.TOPIC_OVERVIEW, FileClass.AUTHORED

    doc = frontmatter.parse(text) if path.endswith(paths.MARKDOWN_SUFFIX) else None
    topic_path = paths.rel(kb_root, owner) if owner is not None else None
    meta = doc.meta if doc and doc.meta else Metadata()
    return _Context(
        kb_root=kb_root,
        path=path,
        role=role,
        file_class=file_class,
        doc=doc,
        meta=meta,
        topic_path=topic_path,
        topic_name=PurePosixPath(topic_path).name if topic_path else None,
        topic_tag=_topic_tag(kb_root, topic_path) if topic_path else None,
        inner=_inner_parts(path, topic_path),
        known_topic_tags=_known_topic_tags(kb_root) if meta.related_topics else None,
    )


def _creates_a_topic_root(relative: PurePosixPath) -> bool:
    """True when writing this path would legitimately create a topic root (PA-3, PA-4).

    A ``topic.md`` directly under the KB root or under a ``sub-topics/`` directory. A ``topic.md``
    anywhere else is *not* granted the override: it is an item called ``topic`` (VA-19) or a
    misplaced topic root (VA-36), and both findings need the file judged where it actually sits.
    """
    parts = relative.parts
    if relative.name != paths.TOPIC_FILE:
        return False
    return len(parts) == 2 or (len(parts) > 2 and parts[-3] == paths.SUBTOPICS_DIR)


def _context_for_record(
    kb_root: Path, record: FileRecord, known_topic_tags: frozenset[str] | None = None
) -> _Context:
    """Rule context for an already-walked file — no re-parse, no second stat (decision C)."""
    topic_path = record.topic_path
    meta = record.meta or Metadata()
    return _Context(
        kb_root=kb_root,
        path=record.path,
        role=record.role,
        file_class=record.file_class,
        doc=record.doc,
        meta=meta,
        topic_path=topic_path,
        topic_name=PurePosixPath(topic_path).name if topic_path else None,
        topic_tag=_topic_tag(kb_root, topic_path) if topic_path else None,
        inner=_inner_parts(record.path, topic_path),
        known_topic_tags=known_topic_tags,
    )


# --------------------------------------------------------------------------------------
# Public entry points (VA-1, VA-2)
# --------------------------------------------------------------------------------------


def validate_content(kb_root: Path, rel_path: str, text: str) -> list[Finding]:
    """Validate proposed bytes for ``rel_path`` before they are written (VA-1, VA-2).

    Pure over ``(rel_path, text)`` and correct for a path that does not exist: this is the gate
    Layer 2's middleware runs before it lets a ``write_file`` through, so its findings are what an
    agent reads when its write is rejected. The file under test is never opened. Reading the *tree*
    is allowed and necessary — a path cannot say which topic owns it.

    Stateless (VA-2): no counters, no caches, no memory of previous calls. The three-attempt bound
    belongs to Layer 2. Two identical calls return identical findings.

    Returns every defect it can see (CX-5), ordered errors-first by
    :func:`~pkb.core.errors.sort_findings`. Raises :class:`ValueError` only for a path outside the
    knowledge base, which is a caller bug rather than a content defect.
    """
    return sort_findings(_rules_result(_context_for_text(kb_root, rel_path, text)))


def validate_file(kb_root: Path, record: FileRecord) -> list[Finding]:
    """The per-file rules over an already-walked :class:`~pkb.core.models.FileRecord`.

    Same rule set and same findings as :func:`validate_content` for the same bytes, reading the
    snapshot's parse rather than re-reading the file (decision C). The cross-file rules are not
    included; :func:`validate_tree` adds them.
    """
    meta = record.meta
    known = _known_topic_tags(kb_root) if meta and meta.related_topics else None
    return sort_findings(_rules_result(_context_for_record(kb_root, record, known)))


_RULE_OWNED_CODES: frozenset[str] = frozenset(
    {
        "FRONTMATTER_PARSE_ERROR",
        "MISPLACED_TOPIC_ROOT",
        "UNEXPECTED_ROOT_ENTRY",
    }
)
"""Codes both the walk and a rule function derive; the rule function is the single owner (CX-5).

Each has two producers today — ``scan._Walk._read`` vs :func:`_va39_frontmatter_parse_error`,
``scan._Walk._record_topic`` vs :func:`_va36_misplaced_topic_roots`, and
``scan._Walk._check_root_entries`` vs :func:`_pa1_unexpected_root_entries`.

The walk has to keep emitting them — :func:`~pkb.core.maintenance.flush` reports
``snapshot.findings`` directly and never calls :func:`validate_tree`, and MA-14 requires the flush
to report an unparseable file. But on this side the two producers would collide: they phrase the
``message`` differently and, for VA-36, anchor a different ``path``, so :func:`_deduplicate` sees
two distinct findings and CX-5's one-defect-one-finding property breaks. Layer 2 feeds ``message``
verbatim into its error ``ToolMessage``, so the duplicate reaches an agent as the same defect told
twice in two different sentences.

The rule functions win rather than the walk because they are the shape the spec pins: VA-36's test
assertion anchors the warning at ``Cooking/notes/Grilling/topic.md``, and the ``hint`` on every
rule finding names the fix an agent should apply (CX-6).

Stated as the codes validation re-derives, not as the codes only the walk can see: a new walk-only
code — the walk answers questions a rule function cannot, needing the topic set
(``UNADDRESSABLE_TOPIC_ROOT``) or the raw bytes (``UNREADABLE_FILE``) — must reach the caller, and
a whitelist would silently swallow it.
"""


def validate_tree(kb_root: Path, snapshot: KbSnapshot | None = None) -> list[Finding]:
    """Validate a whole knowledge base: every file, plus the cross-file structure (VA-1).

    Total over a degraded tree (MA-14): an unparseable file, a topic without ``topic.md``, a note
    folder with no main file — each becomes a finding and none stops the pass.

    The view is :func:`pkb.core.scan.scan`'s, whether the caller supplied it or not — decision C's
    single walk, the same one ``regenerate_all`` and ``flush`` read. This module owns no walker:
    a second one drifts, and the drift is invisible because both answers look plausible. The one
    that used to live here disagreed with ``scan`` about which directories are topic roots, about
    whether a CRLF file's body keeps its line endings, and about whether a topic folder with no
    addressable name exists at all — so the *same* public function returned different findings for
    the same tree depending on who supplied the snapshot.

    Findings the walk recorded are carried through except the ones a rule function re-derives
    (:data:`_RULE_OWNED_CODES`), and the result is deduplicated and sorted so two runs over the
    same tree return the same list.
    """
    view = snapshot if snapshot is not None else scan(kb_root)
    known = frozenset(topic.tag for topic in view.topics.values())
    unreadable = {finding.path for finding in view.findings if finding.code == "UNREADABLE_FILE"}

    findings: list[Finding] = [f for f in view.findings if f.code not in _RULE_OWNED_CODES]
    for record in view.files.values():
        findings.extend(
            finding
            for finding in _rules_result(_context_for_record(kb_root, record, known))
            # A file whose bytes are not UTF-8 has no frontmatter block to complain about: the walk
            # already said the real thing (MA-14, "re-save the file as UTF-8"), and VA-39's
            # "fix the YAML syntax" on top of it is one defect reported twice (CX-5).
            if not (finding.code == "FRONTMATTER_PARSE_ERROR" and finding.path in unreadable)
        )

    findings.extend(_pa1_unexpected_root_entries(kb_root, view))
    findings.extend(_t1_unexpected_topic_entry(kb_root, view))
    findings.extend(_va16_folder_hosted_items(kb_root, view))
    findings.extend(_va21_duplicate_note_identity(kb_root, view))
    findings.extend(_va23_media_placement(view))
    findings.extend(_va36_misplaced_topic_roots(view))
    findings.extend(_pa14_legacy_skill_layout(view))
    findings.extend(_sc1_required_topic_members(kb_root, view))
    return sort_findings(_deduplicate(findings))


def _deduplicate(findings: Iterable[Finding]) -> list[Finding]:
    """Drop exact repeats — a caller-supplied snapshot may already carry a walk-time finding."""
    seen: dict[Finding, None] = {}
    for finding in findings:
        seen.setdefault(finding, None)
    return list(seen)


# --------------------------------------------------------------------------------------
# Cross-file rules (PA-1, PA-14, VA-16, VA-21, VA-22, VA-23, VA-36, SC-1)
# --------------------------------------------------------------------------------------


def _tree_finding(
    code: str,
    severity: Severity,
    message: str,
    rule_id: str,
    path: str,
    *,
    value: str | None = None,
    hint: str | None = None,
) -> Finding:
    return Finding(
        code=code,
        severity=severity,
        message=message,
        rule_id=rule_id,
        path=path,
        value=value,
        hint=hint,
    )


def _pa1_unexpected_root_entries(kb_root: Path, view: KbSnapshot) -> list[Finding]:
    """The knowledge-base root holds ``tags.md``, ``skills/``, ``sessions/`` and topics (PA-1).

    A warning, not an error: a stray root entry is untidy rather than corrupting, and the root is
    the one place a human is most likely to drop something by hand. A root ``index.md`` is one of
    these strays now, not an allowed entry (T-37, P2) — a copy on disk is reported here like any
    other unrecognized name and never touched or deleted, whether a human dropped it there by hand
    or a generator wrote it (one still does, pending the task that retires it).
    """
    allowed = {paths.TAGS_FILE, paths.SKILLS_DIR, paths.SESSIONS_DIR}
    findings = []
    for name in _entry_names(kb_root):
        if paths.is_ignored(name) or name in allowed or name in view.topics:
            continue
        findings.append(
            _tree_finding(
                "UNEXPECTED_ROOT_ENTRY",
                Severity.WARNING,
                f"{name!r} is neither a reserved root entry nor a topic root.",
                "PA-1",
                name,
                hint=(
                    f"The KB root holds {', '.join(sorted(allowed))} plus one directory per "
                    "top-level topic; a topic root is a directory containing topic.md."
                ),
            )
        )
    return findings


def _va16_folder_hosted_items(kb_root: Path, view: KbSnapshot) -> list[Finding]:
    """Every item folder holds a main file named after itself (VA-16, VA-22, PA-17).

    Applies inside ``notes/`` and ``references/`` only — there is no extension-folder mechanism
    (T-1) — never inside ``skills/``. The name comparison is case-exact against a directory listing
    (PA-17): the development host is case-insensitive APFS, so ``Path.exists()`` would accept
    ``Steak/steak.md`` for ``Steak/Steak.md`` and the tree would break on a case-sensitive deploy
    host.

    A folder that holds a ``media/`` subdirectory but no main file is reported under VA-22 instead
    of VA-16 — same code, same fix, but it names the rule that says a note folder is never
    text-free.
    """
    findings = []
    for topic in view.topics.values():
        topic_abs = kb_root / topic.path
        sections: list[tuple[str, bool]] = [(paths.NOTES_DIR, True), (paths.REFERENCES_DIR, False)]
        for section, recurse in sections:
            findings.extend(
                _check_item_folders(kb_root, view, topic_abs / section, recurse=recurse)
            )
    return findings


def _t1_unexpected_topic_entry(kb_root: Path, view: KbSnapshot) -> list[Finding]:
    """A topic root holds only the directories DESIGN §1.1 names (T-1).

    There is no extension-folder mechanism any more: a directory directly under a topic root
    outside ``references/``, ``notes/``, ``skills/`` and ``sub-topics/`` is unrecognized. The
    T-rules name no finding code for this case (T-1's own assertion cites T-34, the per-write
    location-agreement rules, which do not cover directories), so ``UNEXPECTED_TOPIC_ENTRY`` is
    minted here, mirroring PA-1's root-level ``UNEXPECTED_ROOT_ENTRY``. A warning, like PA-1: the
    directory is untidy rather than corrupting, and Layer 1 never touches or deletes it.
    """
    findings = []
    for topic in view.topics.values():
        for name in paths.extension_folders(kb_root / topic.path):
            findings.append(
                _tree_finding(
                    "UNEXPECTED_TOPIC_ENTRY",
                    Severity.WARNING,
                    f"{name!r} is not one of this topic's structural directories.",
                    "T-1",
                    f"{topic.path}/{name}",
                    hint=(
                        "A topic root holds references/, notes/, skills/ and sub-topics/ only; "
                        "there is no extension-folder mechanism."
                    ),
                )
            )
    return findings


def _check_item_folders(
    kb_root: Path, view: KbSnapshot, parent: Path, *, recurse: bool
) -> list[Finding]:
    """Item folders directly under ``parent``; ``recurse`` descends into nested item folders.

    ``references/<src>/`` is never recursed into: a reference folder holds arbitrary source files
    with no naming constraint (VA-24), so its subdirectories are source material, not items.
    """
    findings: list[Finding] = []
    for name in paths.dir_names(parent):
        if paths.is_ignored(name) or name == paths.MEDIA_DIR:
            continue
        item = parent / name
        item_rel = paths.rel(kb_root, item)
        if item_rel in view.topics:
            continue  # a nested topic root, not an item folder — VA-36 owns it.
        findings.extend(_check_main_file(kb_root, item, item_rel))
        if recurse:
            findings.extend(_check_item_folders(kb_root, view, item, recurse=True))
    return findings


def _check_main_file(kb_root: Path, item: Path, item_rel: str) -> list[Finding]:
    expected = f"{item.name}{paths.MARKDOWN_SUFFIX}"
    entries = _entry_names(item)
    if expected in entries:
        return []
    lookalike = next(
        (name for name in sorted(entries, key=paths.sort_key) if name.lower() == expected.lower()),
        None,
    )
    if lookalike is not None:
        return [
            _tree_finding(
                "MAIN_FILE_CASE_MISMATCH",
                Severity.ERROR,
                f"The item folder {item.name!r} holds {lookalike!r}; the main file name must match "
                "the folder byte-for-byte.",
                "PA-17",
                item_rel,
                value=lookalike,
                hint=f"Rename it to {expected} — a case-sensitive host will not find it otherwise.",
            )
        ]
    has_media = paths.MEDIA_DIR in paths.dir_names(item)
    return [
        _tree_finding(
            "MISSING_MAIN_FILE",
            Severity.ERROR,
            f"The item folder {item.name!r} has no main content file {expected!r}.",
            "VA-22" if has_media else "VA-16",
            item_rel,
            value=expected,
            hint=(
                f"Add {item_rel}/{expected} — agents read the text, not the binaries."
                if has_media
                else f"Add {item_rel}/{expected}, named after the folder itself."
            ),
        )
    ]


def _va21_duplicate_note_identity(kb_root: Path, view: KbSnapshot) -> list[Finding]:
    """A note is standalone or folder-hosted, never both (VA-21).

    Two files claim the same note; every link, tag and index entry then has two plausible targets
    and the human cannot tell which one is real.
    """
    findings = []
    for topic in view.topics.values():
        notes_dir = kb_root / topic.path / paths.NOTES_DIR
        folders = {name for name in paths.dir_names(notes_dir) if not paths.is_ignored(name)}
        for name in sorted(_entry_names(notes_dir), key=paths.sort_key):
            stem = name.removesuffix(paths.MARKDOWN_SUFFIX)
            if not name.endswith(paths.MARKDOWN_SUFFIX) or stem not in folders:
                continue
            findings.append(
                _tree_finding(
                    "DUPLICATE_NOTE_IDENTITY",
                    Severity.ERROR,
                    f"The note {stem!r} exists both as {name} and as the folder {stem}/.",
                    "VA-21",
                    f"{topic.path}/{paths.NOTES_DIR}/{name}",
                    value=stem,
                    hint=(
                        f"Keep one form: merge the standalone note into "
                        f"{paths.NOTES_DIR}/{stem}/{name}, or delete the folder."
                    ),
                )
            )
    return findings


def _va23_media_placement(view: KbSnapshot) -> list[Finding]:
    """Binaries under ``notes/`` live in a ``media/`` folder (VA-23).

    Warning: the file is still readable, but a note folder that mixes text and binaries makes the
    "agents read the text" rule impossible to apply mechanically. ``references/`` is deliberately
    exempt — a reference folder holds arbitrary source files by design (VA-24).
    """
    findings = []
    for record in view.files.values():
        if record.is_markdown or record.file_class is not FileClass.ASSET:
            continue
        if record.topic_path is None:
            continue
        inner = _inner_parts(record.path, record.topic_path)
        if not inner or inner[0] != paths.NOTES_DIR or paths.MEDIA_DIR in inner[1:]:
            continue
        findings.append(
            _tree_finding(
                "MEDIA_OUTSIDE_MEDIA_FOLDER",
                Severity.WARNING,
                f"{PurePosixPath(record.path).name!r} sits beside note text instead of in a "
                "media/ folder.",
                "VA-23",
                record.path,
                hint=(
                    f"Move it to {'/'.join((record.topic_path, *inner[:-1], paths.MEDIA_DIR))}/"
                    f"{PurePosixPath(record.path).name}."
                ),
            )
        )
    return findings


def _va36_misplaced_topic_roots(view: KbSnapshot) -> list[Finding]:
    """A topic root reached other than through ``sub-topics/`` (VA-36).

    Warning, and the topic is still discovered and still catalogued — an invisible topic is worse
    than a misfiled one, because nothing would ever route to it.
    """
    findings = []
    for topic in view.topics.values():
        if topic.parent is None:
            expected_ok = len(PurePosixPath(topic.path).parts) == 1
            expected = "a directory directly under the KB root"
        else:
            expected_ok = topic.path == f"{topic.parent}/{paths.SUBTOPICS_DIR}/{topic.name}"
            expected = f"{topic.parent}/{paths.SUBTOPICS_DIR}/{topic.name}"
        if expected_ok:
            continue
        findings.append(diagnostics.misplaced_topic_root(topic.path, expected))
    return findings


def _pa14_legacy_skill_layout(view: KbSnapshot) -> list[Finding]:
    """A flat ``skills/<name>.md`` instead of ``skills/<name>/SKILL.md`` (PA-14, C2).

    Warning: the file is not discovered as a skill, so the overload it was meant to provide is
    silently absent — which is exactly the failure this finding makes visible.
    """
    findings = []
    for record in view.files.values():
        parts = PurePosixPath(record.path).parts
        if record.role is not FileRole.SKILL or len(parts) < 2:
            continue
        if parts[-2] != paths.SKILLS_DIR or parts[-1] == paths.SKILL_FILE:
            continue
        if not parts[-1].endswith(paths.MARKDOWN_SUFFIX):
            continue
        stem = parts[-1].removesuffix(paths.MARKDOWN_SUFFIX)
        findings.append(
            _tree_finding(
                "LEGACY_SKILL_LAYOUT",
                Severity.WARNING,
                f"{parts[-1]!r} uses the superseded flat skill layout and is not loaded as a skill.",
                "PA-14",
                record.path,
                value=stem,
                hint=f"Move it to {'/'.join(parts[:-1])}/{stem}/{paths.SKILL_FILE}.",
            )
        )
    return findings


def _sc1_required_topic_members(kb_root: Path, view: KbSnapshot) -> list[Finding]:
    """The breadth files every topic root owns (SC-1).

    ``notes/summary.md`` and ``references/summary.md`` are what the scaffolder creates and what the
    breadth-first reading order depends on. A warning: a topic missing one is incomplete, not
    broken, and Layer 1 never creates files behind the human's back.
    """
    findings = []
    for topic in view.topics.values():
        for section in (paths.NOTES_DIR, paths.REFERENCES_DIR):
            member = f"{topic.path}/{section}/{paths.SUMMARY_FILE}"
            if member in view.files:
                continue
            findings.append(
                _tree_finding(
                    "MISSING_REQUIRED_FILE",
                    Severity.WARNING,
                    f"The topic {topic.name!r} has no {section}/{paths.SUMMARY_FILE}.",
                    "SC-1",
                    member,
                    hint=f"Add {member} — the breadth overview of this topic's {section}.",
                )
            )
    return findings


# --------------------------------------------------------------------------------------
# Directory listings for the cross-file rules
# --------------------------------------------------------------------------------------
#
# The snapshot answers every question about *files*, so these two exist for the questions that are
# about the tree's shape instead: which entries sit at the KB root (PA-1), which item folders exist
# under a section (VA-16), and whether a note name is claimed twice (VA-21). An empty item folder
# has no file in the snapshot to be found by, so it has to be listed. There is deliberately no
# reader and no recursive walker here — decision C puts both in :mod:`pkb.core.scan`.


def _entry_names(directory: Path) -> list[str]:
    """Every entry name in ``directory``, in the one sibling order; missing reads as empty (MA-14)."""
    try:
        with os.scandir(directory) as entries:
            return sorted((entry.name for entry in entries), key=paths.sort_key)
    except OSError:
        return []
