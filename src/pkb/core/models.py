"""The data model shared by every Layer 1 module.

These types are the seams: ``frontmatter`` produces :class:`ParsedDocument`, ``scan`` produces
:class:`KbSnapshot`, and validation, generation, and maintenance all read from them (decision C in
the rules document). Nothing here touches the filesystem.

Paths in this module are **KB-relative POSIX strings** unless the attribute name says ``abs_``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from pkb.core.errors import Finding

# --------------------------------------------------------------------------------------
# Frontmatter
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldProblem:
    """A known frontmatter key whose value could not be coerced.

    Parsing is total (FM-13): it records the problem here and leaves the typed field ``None`` so
    validation can turn it into a :class:`~pkb.core.errors.Finding`.
    """

    field: str
    code: str
    """``FIELD_TYPE``, ``DATE_FORMAT`` or ``EMPTY_FIELD``."""

    detail: str


@dataclass(frozen=True, slots=True)
class Metadata:
    """The typed view of a file's frontmatter (FM-2, FM-3, FM-4).

    Every field is optional at this level; requiredness is a validation rule, not a parse rule.
    """

    title: str | None = None
    description: str | None = None
    topic: str | None = None
    tags: tuple[str, ...] = ()
    created: date | None = None
    updated: date | None = None
    related_topics: tuple[str, ...] = ()
    source_type: str | None = None

    unknown_fields: tuple[str, ...] = ()
    """Keys outside the known schema, first-seen order. Preserved on write (FM-10)."""

    bad_fields: tuple[FieldProblem, ...] = ()
    """Known keys whose value failed coercion (FM-4, FM-5)."""

    present_keys: tuple[str, ...] = ()
    """Every key literally present in the block, in document order."""

    def has(self, key: str) -> bool:
        return key in self.present_keys


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """The result of parsing a markdown file. Never raises (FM-13)."""

    body: str
    """Everything after the closing ``---``, byte-exact. The whole file when there is no block."""

    raw: Mapping[str, Any] | None = None
    """The YAML mapping as loaded, or ``None`` when the file carries no frontmatter block."""

    meta: Metadata | None = None
    """The typed view, or ``None`` when there is no block or the block failed to parse."""

    error: str | None = None
    """YAML error text when the block could not be parsed."""

    error_line: int | None = None

    @property
    def has_frontmatter(self) -> bool:
        return self.raw is not None or self.error is not None


# --------------------------------------------------------------------------------------
# Tree vocabulary
# --------------------------------------------------------------------------------------


class FileClass(StrEnum):
    """Which validation regime a file falls under.

    ``AUTHORED`` files carry the seven required fields; ``DERIVED`` files are generated and exempt
    (VA-5); ``SKILL`` files use deepagents' own schema (VA-6); ``ASSET`` files are never parsed
    (FM-14, VA-7); ``IGNORED`` covers dot-entries and ``__pycache__`` (PA-16).
    """

    AUTHORED = "authored"
    DERIVED = "derived"
    SKILL = "skill"
    ASSET = "asset"
    IGNORED = "ignored"


class FileRole(StrEnum):
    """What a file *is*, decided by its location. Drives the location-consistency table (VA-13).

    There is no extension-folder mechanism (T-1): ``ROOT_INDEX``, ``EXTENSION_SUMMARY`` and
    ``EXTENSION_ITEM`` are gone. ``SESSION`` (T-9) and ``CAPTURED_SOURCE`` (T-14) are new — a
    session file is a knowledge file with nothing to compare its location against, and a captured
    source is none of the three file classes T-8 names, whatever its extension.
    """

    ROOT_TAGS = "root-tags"
    TOPIC_OVERVIEW = "topic-overview"
    TOPIC_INDEX = "topic-index"
    NOTES_SUMMARY = "notes-summary"
    REFERENCES_SUMMARY = "references-summary"
    NOTE = "note"
    REFERENCE = "reference"
    CAPTURED_SOURCE = "captured-source"
    SESSION = "session"
    EXPERT = "expert"
    SKILL = "skill"
    ASSET = "asset"
    UNKNOWN = "unknown"
    """A markdown file in a place the standard structure does not describe (VA-38, MA-8)."""


class ApprovalMode(StrEnum):
    """Who is allowed to put bytes on disk, and whether a human read them first (T-6).

    Three modes cover the tree (DESIGN §1.2, quoted): **derived** — harness code writes the file,
    no agent may write it, and nobody approves it; **approved** — an agent drafts, and the operator
    reads the exact bytes before the file lands; **on instruction** — the file lands inside the turn
    on the operator's ask, and that ask is the approval. This is data, not enforcement: Layer 1
    exposes the table as the contract surface, and gating a write on it is Layer 2's job.
    """

    DERIVED = "derived"
    APPROVED = "approved"
    ON_INSTRUCTION = "on-instruction"


APPROVAL_MODES: Final[Mapping[FileRole, tuple[ApprovalMode, ...]]] = MappingProxyType(
    {
        FileRole.TOPIC_OVERVIEW: (ApprovalMode.APPROVED,),
        FileRole.TOPIC_INDEX: (ApprovalMode.DERIVED,),
        FileRole.EXPERT: (ApprovalMode.APPROVED,),
        FileRole.SKILL: (ApprovalMode.APPROVED,),
        FileRole.REFERENCES_SUMMARY: (ApprovalMode.APPROVED,),
        FileRole.REFERENCE: (ApprovalMode.ON_INSTRUCTION, ApprovalMode.APPROVED),
        FileRole.NOTE: (ApprovalMode.APPROVED,),
        FileRole.NOTES_SUMMARY: (ApprovalMode.APPROVED,),
        FileRole.ROOT_TAGS: (ApprovalMode.DERIVED,),
        FileRole.SESSION: (ApprovalMode.ON_INSTRUCTION, ApprovalMode.APPROVED),
    }
)
"""The role → approval-mode table DESIGN §1.2's 11-row table fixes, verbatim (T-6).

Ten :class:`FileRole` keys carry the design table's eleven rows: ``SKILL`` stands for both the
topic-level and the root ``skills/[skill-name]/SKILL.md`` rows, which name the identical mode. A
role naming two modes carries them **in order** — naming a source is the approval on the first
map of it, and a session's running record needs no approval while its synthesis waits on the
operator word for word (§1.2) — so ``REFERENCE`` and ``SESSION`` are the only two-element values.
``CAPTURED_SOURCE``, ``ASSET`` and ``UNKNOWN`` carry no PKB frontmatter and name no row in the
design table, so they have no entry here; a lookup for one of them is a caller error; not a
degraded case this table quietly answers.

Data only, matching Layer 1's own rule (CLAUDE.md, "Conventions"): nothing here enforces approval,
gates a tool call, or reads a session's turn history — that is Layer 2's concern once it reaches
the file lifecycle rules (`S-*`). Layer 1 exposes the table so both layers cite the same contract.
"""


# --------------------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FileRecord:
    """One file in the tree, classified and (for markdown) parsed."""

    path: str
    abs_path: Path
    role: FileRole
    file_class: FileClass
    topic_path: str | None
    """KB-relative path of the owning topic root — the nearest ancestor with ``topic.md`` (PA-15)."""

    doc: ParsedDocument | None = None
    """``None`` for non-markdown files."""

    @property
    def meta(self) -> Metadata | None:
        return self.doc.meta if self.doc else None

    @property
    def is_markdown(self) -> bool:
        return self.doc is not None


@dataclass(frozen=True, slots=True)
class TopicRecord:
    """A topic root: a directory holding ``topic.md`` (PA-3)."""

    path: str
    """KB-relative POSIX path, e.g. ``Cooking/sub-topics/Grilling``."""

    name: str
    """The directory's display name, e.g. ``Grilling``."""

    agent_id: str
    """``topic/cooking/grilling`` — ``sub-topics`` elided, segments slugified (PA-10)."""

    tag: str
    """``topic.cooking.grilling`` (PA-9)."""

    parent: str | None
    """KB-relative path of the enclosing topic root, or ``None`` for a top-level topic."""

    children: tuple[str, ...] = ()
    """KB-relative paths of immediate sub-topics, in render order."""

    has_expert: bool = False

    meta: Metadata | None = None
    """``topic.md``'s frontmatter, or ``None`` when it is missing or unparseable (GE-25)."""

    @property
    def title(self) -> str:
        """Display title, falling back to the folder name for a degraded topic (GE-25)."""
        if self.meta and self.meta.title:
            return self.meta.title
        return self.name

    @property
    def depth(self) -> int:
        """1 for a top-level topic, 2 for its sub-topic, and so on."""
        return self.tag.count(".")


@dataclass(frozen=True, slots=True)
class KbSnapshot:
    """One deterministic walk of the tree, shared by validation, generation, and maintenance.

    ``topics`` and ``files`` are insertion-ordered: topics depth-first pre-order with siblings
    sorted case-insensitively then by codepoint (PA-5), files by KB-relative path under the same
    sort key (GE-27).
    """

    root: Path
    topics: Mapping[str, TopicRecord]
    files: Mapping[str, FileRecord]
    findings: tuple[Finding, ...] = ()
    """Problems observed while walking: parse failures, unexpected root entries (VA-39, PA-1)."""

    def topic(self, path: str) -> TopicRecord:
        return self.topics[path]

    def top_level_topics(self) -> list[TopicRecord]:
        return [t for t in self.topics.values() if t.parent is None]

    def files_in_topic(
        self, topic_path: str, *, include_subtopics: bool = False
    ) -> list[FileRecord]:
        """Files whose owning topic root is ``topic_path`` (or below it, when asked)."""
        out = []
        for record in self.files.values():
            if record.topic_path == topic_path:
                out.append(record)
            elif include_subtopics and record.topic_path is not None:
                owner = record.topic_path
                if owner.startswith(f"{topic_path}/"):
                    out.append(record)
        return out

    def content_files(self) -> Iterator[FileRecord]:
        """Authored markdown only — the input to every generator (GE-3)."""
        for record in self.files.values():
            if record.file_class is FileClass.AUTHORED and record.is_markdown:
                yield record


# --------------------------------------------------------------------------------------
# Operation results
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScanRequest:
    """A conflict scan Layer 1 schedules and Layer 2 executes (MA-11, MA-12).

    Data only: Layer 1 opens no queue and no database.
    """

    topic_id: str
    topic_path: str
    changed_paths: tuple[str, ...]
    origin: str
    """``maintenance`` or ``on-demand``."""

    requested_at: date


@dataclass(slots=True)
class FlushReport:
    """What one maintenance flush did (MA-13)."""

    written: list[str] = field(default_factory=list)
    """Derived files whose bytes changed."""

    unchanged: list[str] = field(default_factory=list)
    """Derived files that rendered identically and were not touched (GE-8)."""

    stamped: list[str] = field(default_factory=list)
    """Content files whose ``updated`` was bumped (MA-3)."""

    findings: list[Finding] = field(default_factory=list)
    scan_requests: list[ScanRequest] = field(default_factory=list)

    @property
    def derived(self) -> list[str]:
        return sorted([*self.written, *self.unchanged])


@dataclass(slots=True)
class ScaffoldResult:
    """What one scaffold call created (SC-10)."""

    topic_path: str
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    """Paths that already existed and were left byte-identical."""

    flush: FlushReport | None = None
