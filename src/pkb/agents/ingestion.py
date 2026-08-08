"""The chunked ingestion loop — a source read section by section, by code (LS-1 … LS-12).

Classify-draft-file is right for a link and wrong for a book. A 400-page book does not fit a context
window, so a single-turn ingestion reads what fits, writes a confident account of the part it saw,
and **nothing anywhere records that the rest was never opened**. That is the same failure class as a
Librarian claiming an expert had checked when none ran, and it is fixed the same way: the harness
drives the reading, and the model no longer decides when it is finished.

**The loop is code, not a tool the model may decline to call.** :func:`ingest` walks
:attr:`pkb.sources.ExtractedSource.sections` in order and asks the expert one bounded question per
section — *what does your topic take from this?* — until every section is accounted for. What the
model contributes is judgement about one chapter at a time; what the harness contributes is that
every chapter is opened and that the ones that were not are named in the file.

Five properties, each of which is a ruling rather than a preference:

* **The window is a section, not a byte range (LS-9, LS-10).** :mod:`pkb.sources` recovers the
  source's own structure, so the expert is asked about *chapter 3*, not about characters
  40000 to 48000. That is both a better prompt and the stable key a second pass reconciles on — a
  chapter is chapter 3 on every reading, with no machine ids in a file a human reads. A section
  longer than :data:`SECTION_WINDOW_CHARS` is asked about in several windows, but the *section* is
  still what the answers are filed under.
* **Write as you go, not at the end.** After a section yields an argument the file on disk reflects
  it. A run that dies at chapter 14 leaves fourteen chapters of work behind, not nothing.
* **The file is the resume state.** On re-entry the reading record in the file says which sections
  this pass has already opened, and the loop continues from the first one it has not. There is
  deliberately no second store of progress: a second source of truth about what was read is a second
  thing that can be wrong, and the one a human can check is the file.
* **Gainful means at least one argument (LS-6).** A topic that takes nothing gets **no trace at
  all** — no folder, no stub file, no copy of the source — rather than an empty folder implying the
  source was considered and is somehow relevant. So nothing is written until the first argument
  exists.
* **The map records the reading, not the source (LS-5).** Each pass appends what it covered, what it
  read and took nothing from, what it never reached, and what it flagged. A book read three times
  carries three readings' worth of provenance, which is what makes "the expert got smarter"
  checkable rather than asserted.

**Reconciliation is per section, never per document (LS-5, LS-12).** Handing a model two long
documents and asking "is anything new?" reproduces the exact failure this design exists to prevent.
So a second pass asks about chapter 3 with chapter 3's existing bullets in front of it, and each
answer falls into one of four outcomes:

============================  ==========================================================
The pass finds                What happens
============================  ==========================================================
an argument the file lacks    **lands**, marked for review — pure addition, nothing lost
a better statement of one     **flagged, not applied** — it would replace text the human
                              may have read and relied on, and there is no undo (D6)
a contradiction               **flagged**: ``status.conflict-review`` plus a one-line
                              ``review_note`` on the *source file*, §1.7's machinery
                              extended to the one conflict with no human side
nothing, where the file has   **kept**. Layer 1 never deletes, and an argument this pass
an argument                   missed is not thereby wrong; the record says so.
============================  ==========================================================

**Two conflicts with existing rules, and how they are resolved.**

*RT-18 versus LS-1's copy.* RT-18's corollary says no other ``pkb.agents`` code writes under
``kb_root``; LS-1 says the original is copied into every topic that gainfully ingests it. The
resolution is the one the design names: **the ingestion workflow makes the copy, not the model**, and
RT-18 gains a sanctioned writer rather than being quietly broken. The rule's *intent* was "no ad-hoc
writers" — a middleware or a scan pass deciding to fix up an index — not "one writer forever", and
this writer meets every property that made ``adopt_skill``'s carve-out safe: it is harness code on a
path no prompt can reach, it writes only under ``<topic>/references/<slug>/``, it validates its own
output through :func:`pkb.core.validate_content` before writing (so nothing lands that the tool layer
would have refused), it consults :func:`pkb.agents.gates.requires_approval` before writing (so
nothing lands that a human would have had to approve), it takes the process-wide write lock exactly
as the scaffolder does, and it records what it wrote in ``kb_touched`` so MW-17 … MW-20's single
flush stamps and indexes it. A binary copy could not go through ``write_file`` in any case — that
tool takes text, and MW-7 intercepts exactly ``write_file`` and ``edit_file``.

*The gate on a rewrite.* RT-31 put no gate on reference depth files, which was right when one was
written once and never touched. It is amended, and the amendment lives in :mod:`pkb.agents.gates` as
:attr:`~pkb.agents.gates.GateReason.REFERENCE_REWRITE`: the **first** write of a source file stays
un-gated, and a write that would **remove or alter** text already in one stops for a human. The loop
itself is non-destructive by construction — it only ever appends, which is why a re-ingestion that
adds lands unattended (LS-12) — so the gate is the mechanical backstop for every other path to that
file, including a model's own ``write_file``. The loop still asks the gate before each write and
withholds the write if it fires, so the two can never disagree.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Final, Protocol

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, StructuredTool
from langgraph.types import Command

from pkb.agents.gates import (
    CONFLICT_TAG,
    WRITE_FILE_TOOL,
    GateReason,
    requires_approval,
)
from pkb.agents.middleware.maintenance import NULL_WRITE_LOCK, KbWriteLock
from pkb.agents.middleware.state import KB_TOUCHED
from pkb.core import Finding, Metadata, errors_only, has_errors, render_findings, validate_content
from pkb.core.frontmatter import parse, serialize, set_field
from pkb.core.models import KbSnapshot, TopicRecord
from pkb.core.paths import REFERENCES_DIR
from pkb.sources import ExtractedSource, Section, SourceError, StagedSource

if TYPE_CHECKING:  # pragma: no cover - typing only
    from langchain_core.language_models import BaseChatModel

__all__ = [
    "ACROSS_HEADING",
    "ACROSS_INSTRUCTION",
    "INGEST_TOOL",
    "NOTHING_MARKER",
    "PROVENANCE_HEADING",
    "READING_RECORD_HEADING",
    "SECTION_INSTRUCTION",
    "SECTION_WINDOW_CHARS",
    "Asker",
    "IngestHost",
    "IngestionReport",
    "PassRecord",
    "SourceFile",
    "Take",
    "TakeKind",
    "ingest",
    "ingest_source_tool",
    "ingest_tools",
    "model_asker",
    "parse_takes",
    "reference_file_path",
]


# --------------------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------------------

INGEST_TOOL: Final = "ingest_source"
"""The expert's entry point into the loop.

Load-bearing as a *name*: it is what the model spells, and it is deliberately **not** a member of
:data:`pkb.agents.gates.GATED_TOOLS`. Gating the call would ask a human to approve a reading before
anyone knows what the reading found; the writes the loop then makes are each checked against the
gate table individually, which is the question a human can actually answer.
"""

PROVENANCE_HEADING: Final = "Provenance"
"""Where the source came from and where its copy sits. Written once and never rewritten, so a later
pass appending to the file stays a pure extension (see :func:`SourceFile.render`)."""

ACROSS_HEADING: Final = "Across the source"
"""LS-10's final section: the ideas no single chapter owns."""

READING_RECORD_HEADING: Final = "Reading record"
"""One entry per pass — what it covered, what it skipped, and why (LS-5)."""

NOTHING_MARKER: Final = "NOTHING"
"""What the expert answers when a section holds nothing for its topic (LS-6, decision G)."""

SECTION_WINDOW_CHARS: Final = 12_000
"""How much of one section is put in front of the model at a time (LS-9).

A bound rather than a limit: a section longer than this is asked about in several windows and the
answers are filed under the same section, so the *anchor* stays the source's own structure. Without
a bound the loop would hand a 90-page chapter to a model that reads the first part and answers
confidently about the whole — the failure this design exists to prevent, one level down.
"""

_STRUCTURAL_HEADINGS: Final = frozenset(
    {PROVENANCE_HEADING, ACROSS_HEADING, READING_RECORD_HEADING}
)
"""Body sections that are not chapters. Everything else under ``##`` is a chapter (LS-10)."""

_TRAILING_HEADINGS: Final = frozenset({ACROSS_HEADING, READING_RECORD_HEADING})
"""The file's own two closing sections. A new chapter is inserted before them, never after."""

_TOOK: Final = "Took something from"
_NOTHING: Final = "Read, took nothing from"
_HELD: Final = "Read, nothing new landed for"
"""Distinct from :data:`_NOTHING` on purpose (§6.9's "read-and-took-nothing is kept distinct").

A section that produced only a rewording or a contradiction was read and *did* give this topic
something — it is simply something no pass is allowed to apply on its own. Filing it as "took
nothing" would tell a later reader that the section was barren, which is the one thing the reading
record exists not to get wrong.
"""

_NO_TEXT: Final = "No text was extracted for"
_REWORDING: Final = "Proposed rewording, not applied, for"
_CONTRADICTION: Final = "Contradicts an earlier reading of"
_COMPLETE: Final = "Pass complete"

_TITLE_MARK: Final = "**"
"""Section titles are bolded in the reading record, which is also how they are parsed back.

The record is the resume state, so it has to be readable *and* re-readable. Bold delimiters survive
a title containing a colon, a dash or a semicolon — the separators a comma-joined list would break
on — and they read as emphasis rather than as machine syntax to a human.
"""

_DRAFT_TAG: Final = "status.draft"
"""Landing content is a draft (LS-12): ingest first, mark for review."""

_REFERENCE_TAG: Final = "type.reference"
"""An extracted argument is a reference, never a note. README §1.3 makes notes the human's own
experience; an argument the AI lifted out of a book is source-derived wherever it lands."""

_REFERENCE_SOURCE_TYPE: Final = "reference"


# --------------------------------------------------------------------------------------
# What the expert is asked, and what it answers
# --------------------------------------------------------------------------------------


class TakeKind(StrEnum):
    """The three things one reading of a section can produce about an existing file (LS-5).

    The names are the wire format: the model answers ``NEW:``, ``BETTER:`` or ``CONTRADICTS:``, and
    a bare ``- `` bullet is read as :attr:`NEW` because that is what a model writes when it forgets
    the grammar and the additive reading is the safe one.
    """

    NEW = "new"
    """An argument the file does not have. Lands, marked for review — pure addition (LS-12)."""

    BETTER = "better"
    """A clearer statement of an argument already there. **Flagged, never applied**: it replaces
    text the human may have read and relied on, and arch D6 leaves no undo."""

    CONTRADICTS = "contradicts"
    """An argument that disagrees with one already there. Flagged with ``status.conflict-review``
    and a one-line ``review_note`` on the source file — §1.7's machinery, extended to the one
    conflict where neither side is human (README §1.7, and the `conflict-detection` skill)."""


@dataclass(frozen=True, slots=True)
class Take:
    """One thing this reading took from one section."""

    kind: TakeKind
    text: str


_PREFIXES: Final[Mapping[str, TakeKind]] = {
    "NEW:": TakeKind.NEW,
    "BETTER:": TakeKind.BETTER,
    "CONTRADICTS:": TakeKind.CONTRADICTS,
}

SECTION_INSTRUCTION: Final = (
    "Answer with one line per argument this topic takes from this section, and nothing else:\n"
    "  NEW: <the argument in one sentence — the claim, and the reasoning or condition it rests on>\n"
    "  BETTER: <a clearer statement of an argument already recorded below for this section>\n"
    "  CONTRADICTS: <an argument here that disagrees with one already recorded below>\n"
    f"Answer {NOTHING_MARKER} on its own line if this section holds nothing your topic cares "
    "about. That is a correct answer, not a failure: a file of twenty entries of which six are "
    "real is worse than a file of six, because a reader cannot tell which six. Do not summarise "
    "the section, do not restate its headings, and do not repeat an argument already recorded."
)
"""The grammar every section answer is read with (:func:`parse_takes`).

Stated as a grammar rather than as prose because the loop has to *act* on the answer — the three
outcomes have three different consequences (LS-12), and only one of them is allowed to change the
file. A free-form answer would have to be classified by a second model call, which is the sort of
"read the answer to decide what the answer meant" step LB-17 already refuses elsewhere.
"""

ACROSS_INSTRUCTION: Final = (
    "Those are the arguments recorded so far, chapter by chapter. Answer with one line per idea "
    "that belongs to the source as a whole rather than to any single section:\n"
    "  NEW: <the idea in one sentence>\n"
    f"Answer {NOTHING_MARKER} on its own line if every idea worth keeping already sits under the "
    "section that introduced it. Do not restate an argument already recorded."
)
"""The one question asked about the whole source, and only after every section was read.

It is asked over the *arguments already collected*, never over the source text: the collected
bullets are bounded by construction while the source is not, and asking a bounded reader about an
unbounded input is exactly what the loop exists to avoid.
"""


def parse_takes(answer: str, *, allow: Iterable[TakeKind] = tuple(TakeKind)) -> tuple[Take, ...]:
    """Read a section answer into takes, tolerating the shapes a small model actually emits.

    A bare ``- `` or ``* `` bullet counts as :attr:`TakeKind.NEW`; a prefix may be bulleted, bolded
    or lower-cased. ``NOTHING`` anywhere on its own line ends the answer with no takes, which is how
    LS-6's "no trace at all" is reached without the loop having to interpret prose.

    *allow* narrows the grammar: the "across the source" question can only produce additions, so a
    ``BETTER:`` there is dropped rather than filed against a section it does not name.
    """
    permitted = set(allow)
    takes: list[Take] = []
    for raw in answer.splitlines():
        line = raw.strip().lstrip("-*").strip()
        line = line.removeprefix(_TITLE_MARK).strip()
        if not line:
            continue
        if line.upper().rstrip(".") == NOTHING_MARKER:
            return ()
        kind, text = _classify(line)
        if kind in permitted and text:
            takes.append(Take(kind=kind, text=text))
    return tuple(takes)


def _classify(line: str) -> tuple[TakeKind, str]:
    """One answer line as ``(kind, text)``; an unprefixed line is an addition."""
    upper = line.upper()
    for prefix, kind in _PREFIXES.items():
        if upper.startswith(prefix):
            return kind, line[len(prefix) :].strip().lstrip("*").strip()
    return TakeKind.NEW, line


Asker = Callable[[str], Awaitable[str]]
"""How the loop reaches the expert: one question in, one answer out.

Injected rather than imported so this module holds no model and no graph, and so the whole loop is
drivable by a ``ScriptedChatModel`` with no key and no network (SK-18). The runtime supplies
:func:`model_asker` bound to the expert's own system prompt, which is what keeps the *persona* the
expert's while the *sequence* stays the harness's.
"""


def model_asker(model: BaseChatModel, system_prompt: str) -> Asker:
    """An :data:`Asker` that puts one section in front of one model call.

    Deliberately **not** a run of the expert's compiled graph. A graph run per section would give
    the model tools to write with — and the whole point of the loop is that the file is written by
    code that cannot decide it is finished. What the expert keeps is its own system prompt: the
    non-overridable standards preamble plus its ``expert.md`` or the shipped template (EX-4), so the
    lens each topic reads through is its own (decision G, PR-9).
    """

    async def ask(question: str) -> str:
        message = await model.ainvoke([SystemMessage(system_prompt), HumanMessage(question)])
        return str(getattr(message, "text", "") or "")

    return ask


# --------------------------------------------------------------------------------------
# The file on disk
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class PassRecord:
    """One reading of the source, as the reading record renders and re-reads it (LS-5).

    A pass is *complete* when every section of the extraction was opened. Incompleteness is what
    distinguishes a resume from a re-ingestion, and it is read back off the file rather than kept
    anywhere else — the file is the resume state.
    """

    number: int
    read_on: date | None = None
    header: str = ""
    took: list[str] = field(default_factory=list)
    nothing: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    no_text: list[str] = field(default_factory=list)
    flagged: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    complete: bool = False

    @property
    def accounted(self) -> set[str]:
        """Section titles this pass has already opened — the resume frontier."""
        return {*self.took, *self.nothing, *self.held, *self.no_text}


def reference_file_path(topic_path: str, slug: str) -> str:
    """``<topic>/references/<slug>/<slug>.md`` — the one file per source (README §1.2).

    Layer 1 already validates this shape and this is the whole of LS's storage decision: one
    physical file with the arguments as sections inside it, not one file per argument. With one file
    there is nothing to match across passes, so identity — the hard problem a file-per-argument
    layout creates — is a non-question.
    """
    return f"{topic_path}/{REFERENCES_DIR}/{slug}/{slug}.md"


@dataclass(slots=True)
class _Span:
    """One ``##`` block: its heading, and the half-open line range of its body."""

    heading: str
    start: int
    stop: int


@dataclass(slots=True)
class SourceFile:
    """The reference depth file, as a thing the loop can only ever **add to** (LS-10, LS-12).

    Held as two byte-exact strings and edited by line insertion, rather than parsed into a model and
    re-rendered. That is the design decision the whole reconciliation story rests on: every write
    after the first has to be a **pure extension** of what is on disk, or it stops for a human
    (:attr:`~pkb.agents.gates.GateReason.REFERENCE_REWRITE`) and the pass's work is withheld.

    A parse-and-re-render model cannot promise that. It would round-trip its *own* output exactly and
    silently normalise anybody else's — a human who added a blank line, indented a bullet, or
    reflowed a paragraph would find the next pass proposing to undo it, and a re-ingestion that
    should have landed one new argument would instead park on a gate about whitespace. Inserting into
    the original bytes makes "the human's edit survives" and "an addition never gates" the same
    property, and it makes both true by construction rather than by care.
    """

    frontmatter: str
    """Everything up to and including the closing ``---`` line, byte-exact and newline-terminated."""

    body: str
    """Everything after it, byte-exact."""

    # -- construction --------------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        topic: TopicRecord,
        source: ExtractedSource,
        slug: str,
        today: date,
        original: str | None,
    ) -> SourceFile:
        """A fresh source file with its provenance and nothing else yet (LS-6).

        Called only once the first argument exists: a topic that takes nothing leaves no file at
        all, so this constructor is never reached for it.
        """
        meta = Metadata(
            title=source.title,
            description=_description(topic, source),
            topic=(topic.meta.topic if topic.meta and topic.meta.topic else topic.name),
            tags=(topic.tag, _REFERENCE_TAG, _DRAFT_TAG),
            created=today,
            updated=today,
            source_type=_REFERENCE_SOURCE_TYPE,
        )
        del slug  # the file's own name already carries it; the block states the origin instead
        opening = [f"# {source.title}", "", _thesis(source), "", f"## {PROVENANCE_HEADING}", ""]
        body = "\n" + "\n".join([*opening, *_provenance(source, today, original)]) + "\n"
        return cls(frontmatter=serialize(meta, ""), body=body)

    @classmethod
    def load(cls, text: str) -> SourceFile:
        """Read a source file back, keeping every byte the previous pass — or the human — wrote."""
        document = parse(text)
        return cls(frontmatter=text[: len(text) - len(document.body)], body=document.body)

    # -- reading -------------------------------------------------------------------

    @property
    def chapters(self) -> list[str]:
        """The headings that name a section of the source itself (LS-10)."""
        return [span.heading for span in self._spans() if span.heading not in _STRUCTURAL_HEADINGS]

    def bullets(self, heading: str) -> tuple[str, ...]:
        """The arguments already recorded under one heading."""
        span = self._span(heading)
        if span is None:
            return ()
        lines = self._lines()
        return tuple(_bullet_text(line) for line in _bullets(lines[span.start : span.stop]))

    def passes(self) -> list[PassRecord]:
        """Every reading recorded in the file, oldest first — the resume state (LS-5)."""
        span = self._span(READING_RECORD_HEADING)
        if span is None:
            return []
        return _parse_passes(self._lines()[span.start : span.stop])

    @property
    def has_arguments(self) -> bool:
        """True once at least one argument is recorded — LS-6's "gainful".

        Counts chapters and ``Across the source`` only. The provenance block and the reading record
        are bullet lists too, and they exist whether or not the topic took anything, so counting
        them would make every file look gainful the moment it was created.
        """
        return any(self.bullets(heading) for heading in [*self.chapters, ACROSS_HEADING])

    # -- appending -----------------------------------------------------------------

    def add_arguments(
        self, heading: str, texts: Sequence[str], *, order: Sequence[str] = ()
    ) -> list[str]:
        """Append arguments under *heading*, creating the section, and report what was added.

        Duplicates are dropped: a second reading that restates an argument already in the file is
        the common case, and the file must not grow a near-copy every pass. Comparison is on
        casefolded, whitespace-collapsed text, which catches a re-phrased *bullet* but deliberately
        not a re-phrased *argument* — that one is the model's ``BETTER:`` judgement, and it is
        flagged rather than applied.

        *order* is the source's own section titles, in the source's own order (LS-10). A section
        this pass is filing for the first time goes where the **source** puts it, not where the pass
        happened to reach it: a second reading that finally takes something from chapter 2 must not
        leave the file reading 1, 3, 2. Placement is still an insertion, so the write stays a pure
        extension of what is on disk.
        """
        known = {_key(text) for text in self.bullets(heading)}
        added: list[str] = []
        for text in texts:
            if _key(text) in known:
                continue
            known.add(_key(text))
            added.append(text)
        if not added:
            return []
        bullets = [f"- {text}\n" for text in added]
        span = self._span(heading)
        if span is not None:
            self._insert(self._append_point(span.start, span.stop), bullets)
        else:
            self._add_block(heading, bullets, before=_later_headings(heading, order))
        return added

    def record_pass(self, record: PassRecord) -> None:
        """Append this pass's entries to the reading record — never rewrite an earlier one.

        Only the *missing* lines are inserted, in order, at the end of this pass's entry, so a
        resume adds what it learned without touching a word of what the run before it recorded. An
        earlier pass's record is history: a loop that edited one would be rewriting the very
        statement of what was and was not read that makes the whole design checkable.
        """
        wanted = _entry_lines(record)
        span = self._span(READING_RECORD_HEADING)
        if span is None:
            self._add_block(
                READING_RECORD_HEADING, [*_pass_lines(record), *wanted], before=frozenset()
            )
            return
        entry = self._pass_span(span, record.number)
        if entry is None:
            self._insert(
                self._append_point(span.start, span.stop),
                ["\n", *_pass_lines(record), *wanted],
            )
            return
        present = {line.strip() for line in self._lines()[entry.start : entry.stop]}
        missing = [line for line in wanted if line.strip() not in present]
        if missing:
            self._insert(self._append_point(entry.start, entry.stop), missing)

    def set_status(self, status: str) -> None:
        """Move the file's single ``status.*`` tag, leaving every other tag alone (VA-9, FM-11).

        A *replacement*, not an addition: Layer 1 allows exactly one ``status.*`` tag per file, so a
        conflict flag added beside ``status.draft`` is refused outright — the write never lands and
        the flag the human is supposed to see never appears. That failure is silent from the model's
        side, which is why it is Layer 1's rule that decides the shape here rather than a reading of
        README §1.7's "add the tag".
        """
        meta = parse(self.frontmatter).meta
        tags = [tag for tag in (meta.tags if meta else ()) if not tag.startswith("status.")]
        if meta and status in meta.tags and len(meta.tags) == len(tags) + 1:
            return
        self.set_field("tags", [*tags, status])

    def set_field(self, key: str, value: object) -> None:
        """Write one frontmatter field surgically (FM-11) — ``review_note``, ``updated``."""
        self.frontmatter = set_field(self.frontmatter, key, value)

    # -- rendering -----------------------------------------------------------------

    def render(self) -> str:
        """The whole file — the bytes that were read back, plus the lines this pass inserted."""
        return self.frontmatter + self.body

    # -- internals -----------------------------------------------------------------

    def _lines(self) -> list[str]:
        return self.body.splitlines(keepends=True)

    def _insert(self, at: int, new_lines: Sequence[str]) -> None:
        lines = self._lines()
        lines[at:at] = new_lines
        self.body = "".join(lines)

    def _spans(self) -> list[_Span]:
        """One entry per ``##`` block, with the half-open line range of its body."""
        spans: list[_Span] = []
        lines = self._lines()
        for index, line in enumerate(lines):
            if not line.startswith("## "):
                continue
            if spans:
                spans[-1].stop = index
            spans.append(_Span(line[3:].strip(), index + 1, len(lines)))
        return spans

    def _span(self, heading: str) -> _Span | None:
        return next((span for span in self._spans() if span.heading == heading), None)

    def _pass_span(self, block: _Span, number: int) -> _Span | None:
        """The line range of one ``### Pass n`` entry inside the reading record."""
        lines = self._lines()
        found: _Span | None = None
        for index in range(block.start, block.stop):
            if not lines[index].startswith("### Pass"):
                continue
            if found is not None:
                found.stop = index
                return found
            record = _pass_header(lines[index].strip())
            if record is not None and record.number == number:
                found = _Span(lines[index].strip(), index + 1, block.stop)
        return found

    def _append_point(self, start: int, stop: int) -> int:
        """Where a line appended to ``[start, stop)`` goes: after its last non-blank line.

        Backing over the trailing blank lines is what keeps the file's shape: an append that landed
        after them would put a bullet under the *next* heading's blank line, and the blank line that
        separates two blocks would drift one further down on every pass.
        """
        lines = self._lines()
        at = min(stop, len(lines))
        while at > start and not lines[at - 1].strip():
            at -= 1
        return at

    def _add_block(self, heading: str, content: Sequence[str], *, before: frozenset[str]) -> None:
        """Insert a whole new ``##`` block ahead of the first block in *before* (LS-10).

        *before* is every heading this one must precede: the source's own later sections, plus
        ``Across the source`` and the reading record, which are the *file's* own closing sections —
        a chapter placed after them would read as if the source had a chapter called "Reading
        record". Inserting rather than appending keeps the write a pure extension either way.
        """
        lines = self._lines()
        at = len(lines)
        for index, line in enumerate(lines):
            if line.startswith("## ") and line[3:].strip() in before | _TRAILING_HEADINGS:
                at = index
                break
        block = [f"## {heading}\n", "\n", *content]
        if at == len(lines):
            self._insert(at, ["\n", *block])
        else:
            self._insert(at, [*block, "\n"])


def _description(topic: TopicRecord, source: ExtractedSource) -> str:
    """The single line deterministic index generation extracts (§1.9, VA-*).

    It names the lens as well as the source, because two topics ingesting one book produce two files
    whose titles are identical and whose contents are not — and the description is what a human
    reading the topic index has to tell them apart by.
    """
    return (
        f"What {topic.title} takes from {source.title}: one section per part of the source "
        "that gave this topic something, and a record of what was read"
    )


def _thesis(source: ExtractedSource) -> str:
    """One line saying what the source is, from what the extraction actually knows.

    Never a claim about the argument of a book nobody has read yet: at the moment the file is
    created the loop has seen one section. What it can state truthfully is the source's identity and
    how much of it there is, and the arguments below say the rest.
    """
    parts = [f"A {source.kind} source"]
    if source.author:
        parts.append(f"by {source.author}")
    if source.published:
        parts.append(f"({source.published})")
    return " ".join(parts) + f", read in {len(source.sections)} sections for this topic."


def _provenance(source: ExtractedSource, today: date, original: str | None) -> list[str]:
    """Where the source came from — written once, never rewritten.

    Everything that varies per pass lives in the reading record instead, so this block is stable and
    a later pass appending to the file cannot turn into a rewrite of it. The link to the copied
    original is the part an implementer meets first: without it MA-8's ``ORPHAN_ASSET`` flags the
    copy in every topic that took one, once per topic index (`analysis.py:352-370`).
    """
    lines = [f"- Origin: `{source.origin}`"]
    if original is not None:
        lines.append(f"- Original in this topic: [{original}]({original})")
    method = source.structure_method
    qualifier = "the source's own" if method.is_intrinsic else "inferred, not the source's own"
    lines.append(f"- Structure: `{method.value}` — {qualifier}")
    if source.page_count is not None:
        lines.append(f"- Pages: {source.page_count}")
    lines.append(f"- First read: {today.isoformat()}")
    lines.extend(f"- Extraction warning: {warning}" for warning in source.warnings)
    if not method.is_intrinsic:
        lines.append(
            "- Section titles here were inferred rather than read from the source, so a later "
            "reading may name the same part differently."
        )
    return lines


def _later_headings(heading: str, order: Sequence[str]) -> frozenset[str]:
    """Every section the source puts *after* ``heading`` — where a new block must not go (LS-10)."""
    titles = list(order)
    if heading not in titles:
        return frozenset()
    return frozenset(titles[titles.index(heading) + 1 :])


def _bullets(lines: Sequence[str]) -> list[str]:
    return [line for line in lines if line.lstrip().startswith(("- ", "* "))]


def _bullet_text(line: str) -> str:
    return line.lstrip().lstrip("-*").strip()


def _key(text: str) -> str:
    return " ".join(text.split()).casefold()


# --------------------------------------------------------------------------------------
# The reading record — rendered so it can be read back (LS-5)
# --------------------------------------------------------------------------------------


def _pass_lines(record: PassRecord) -> list[str]:
    """The heading of one reading, and the one line of context under it."""
    stamp = f" — {record.read_on.isoformat()}" if record.read_on else ""
    lines = [f"### Pass {record.number}{stamp}\n", "\n"]
    if record.header:
        lines += [f"{record.header}\n", "\n"]
    return lines


def _entry_lines(record: PassRecord) -> list[str]:
    """Everything one reading has to say about which sections it opened, in a stable order.

    Stable because :meth:`SourceFile.record_pass` inserts only the lines that are not there yet: a
    resumed pass appends what it learned after what the run before it recorded, and no line the
    earlier run wrote is touched.
    """
    entries: list[str] = []
    entries += [_entry(_TOOK, title) for title in record.took]
    entries += [_entry(_NOTHING, title) for title in record.nothing]
    entries += [_entry(_HELD, title) for title in record.held]
    entries += [_entry(_NO_TEXT, title) for title in record.no_text]
    entries += [_entry(_REWORDING, note) for note in record.flagged]
    entries += [_entry(_CONTRADICTION, note) for note in record.conflicts]
    if record.complete:
        entries.append(f"- {_COMPLETE}: every section of this reading was opened.")
    return [f"{entry}\n" for entry in entries]


def _entry(label: str, subject: str) -> str:
    return f"- {label}: {_TITLE_MARK}{subject}{_TITLE_MARK}"


_LABELS: Final[Mapping[str, str]] = {
    _TOOK: "took",
    _NOTHING: "nothing",
    _HELD: "held",
    _NO_TEXT: "no_text",
    _REWORDING: "flagged",
    _CONTRADICTION: "conflicts",
}


def _parse_passes(lines: Sequence[str]) -> list[PassRecord]:
    """Read the reading record back — the only place progress is stored.

    Tolerant on purpose: a line it cannot read is skipped rather than raising, because the
    alternative is a hand-edited file that no longer resumes and no longer says why.
    """
    records: list[PassRecord] = []
    current: PassRecord | None = None
    for raw in lines:
        line = raw.strip()
        if line.startswith("### Pass"):
            current = _pass_header(line)
            if current is not None:
                records.append(current)
            continue
        if current is None:
            continue
        if not line.startswith("- "):
            if line and not current.header:
                current.header = line
            continue
        body = line[2:]
        if body.startswith(_COMPLETE):
            current.complete = True
            continue
        for label, attribute in _LABELS.items():
            if body.startswith(f"{label}: "):
                subject = body[len(label) + 2 :].strip()
                subject = subject.removeprefix(_TITLE_MARK).removesuffix(_TITLE_MARK)
                getattr(current, attribute).append(subject)
                break
    return records


def _pass_header(line: str) -> PassRecord | None:
    rest = line[len("### Pass") :].strip()
    number_text, _, stamp = rest.partition("—")
    try:
        number = int(number_text.strip())
    except ValueError:
        return None
    read_on: date | None = None
    try:
        read_on = date.fromisoformat(stamp.strip())
    except ValueError:
        read_on = None
    return PassRecord(number=number, read_on=read_on)


# --------------------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IngestionReport:
    """What one run of the loop did — the whole of what the expert and the human are told.

    Frozen primitives plus :class:`~pkb.core.errors.Finding`, which ``pkb.contracts`` already
    re-exports from Layer 1 — so nothing here is a harness type and the tool's answer and a
    transport's rendering are the same facts.

    **Open seam decision for Layer 3.** This type belongs in ``pkb.contracts`` the moment a
    transport calls :meth:`~pkb.agents.runtime.PkbRuntime.ingest`, because importing it from here
    imports this module, and this module imports langchain and langgraph — which is exactly the
    ``from pkb.agents import …`` leak decision B made structural (I2, D-20). The move is a
    cut-and-paste: no field is typed by anything ``pkb.contracts`` cannot already name. It is not
    made here only because ``pkb/contracts.py`` belongs to the seam's owner.
    """

    agent_id: str
    topic_path: str
    origin: str
    slug: str
    path: str | None = None
    """The source file, or ``None`` when the topic took nothing (LS-6: zero trace)."""

    gainful: bool = False
    pass_number: int = 0
    resumed: bool = False
    offered_reingest: bool = False
    """LS-11: the source is already here and nothing was read. The human decides."""

    sections_total: int = 0
    covered: tuple[str, ...] = ()
    nothing: tuple[str, ...] = ()
    held: tuple[str, ...] = ()
    """Sections that gave this pass something no pass may apply on its own — see :data:`_HELD`."""

    unread: tuple[str, ...] = ()
    flagged: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    touched: tuple[str, ...] = ()
    copied_original: str | None = None
    gate: str | None = None
    """The gate slug when a write was withheld for a human — see the module docstring."""

    findings: tuple[Finding, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.unread

    def summary(self) -> str:
        """What the expert reads back, and what it can tell the human without a second tool call."""
        if self.offered_reingest:
            return (
                f"`{self.origin}` is already ingested into {self.topic_path} at `{self.path}`, and "
                "the last reading covered every section. Nothing was read. Say so and ask whether "
                "to re-ingest it — a second reading is expected to be a better one, and it "
                "reconciles with what is there rather than replacing it. Call this tool again with "
                "confirm_reingest=true if they want it."
            )
        if not self.gainful:
            return (
                f"Read `{self.origin}` — {self.sections_total} sections, and this topic took "
                "nothing from any of them. Nothing was written: no file, no folder, no copy of the "
                "source. Say so in one line; that is a correct outcome, not a failure."
            )
        lines = [
            f"{'Resumed' if self.resumed else 'Read'} `{self.origin}` as pass "
            f"{self.pass_number} — {len(self.covered)} of {self.sections_total} sections gave this "
            f"topic something. Filed to `{self.path}`.",
        ]
        if self.copied_original:
            lines.append(f"The original was copied beside it as `{self.copied_original}`.")
        if self.nothing:
            lines.append(f"Read and took nothing from: {_named(self.nothing)}.")
        if self.unread:
            lines.append(
                f"NOT READ in this pass, and recorded as such in the file: {_named(self.unread)}. "
                "Run this tool again on the same source to continue from there."
            )
        if self.flagged:
            lines.append(
                "Flagged but not applied — a rewrite of text already in the file needs the human: "
                f"{_named(self.flagged)}."
            )
        if self.conflicts:
            lines.append(
                "This reading contradicts the earlier one. The file is tagged "
                f"`{CONFLICT_TAG}` with a review note; change nothing and let the human settle it: "
                f"{_named(self.conflicts)}."
            )
        if self.gate:
            lines.append(
                f"One write was withheld for a human decision ({self.gate}) and is not on disk."
            )
        if self.findings:
            lines.append("The proposed file did not validate:\n" + render_findings(self.findings))
        return "\n".join(lines)


def _named(items: Sequence[str]) -> str:
    return "; ".join(items)


# --------------------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------------------


async def ingest(
    kb_root: Path,
    topic: TopicRecord,
    staged: StagedSource,
    *,
    ask: Asker,
    snapshot: Callable[[], KbSnapshot],
    today: date,
    agent_id: str = "",
    lock: KbWriteLock = NULL_WRITE_LOCK,
) -> IngestionReport:
    """Walk the source section by section and build one file per source (LS-1 … LS-12).

    Code decides when the reading is finished. The model decides only what one section gives this
    topic, which is a judgement small enough to be worth trusting and small enough to check.

    Args:
        kb_root: The knowledge base on disk.
        topic: The topic doing the reading. Everything this function writes lands under
            ``topic.path``, which is what keeps the harness write inside RT-15's scope without
            depending on the permission layer it deliberately bypasses.
        staged: The source in ``<kb>/.inbox/`` (LS-8), already extracted (LS-7). Staging is
            :func:`pkb.sources.stage`'s job and happens once, before any topic has earned a copy.
        ask: How the expert is reached, one bounded question at a time.
        snapshot: The current tree, for the gate table. Called per write.
        today: Injected date (CX-2). The reading record is dated, so a date boundary is testable.
        agent_id: Reported back, never used to decide anything.
        lock: The process-wide knowledge-base write lock (RT-51), held around each write and each
            copy and across nothing else — never across an ``ask`` (RT-52).

    Returns:
        An :class:`IngestionReport`. A topic that took nothing gets ``gainful=False`` and leaves no
        trace at all: no file, no folder, no copy (LS-6).
    """
    source = staged.extracted
    rel_path = reference_file_path(topic.path, staged.slug)
    existing = _read(kb_root / rel_path)
    document = SourceFile.load(existing) if existing is not None else None
    record, resumed = _pass_for(document, today)
    if not record.header:
        # Stated once, in the entry, rather than in the provenance block: the number of sections is
        # a fact about *this* reading, and a re-extraction can legitimately produce a different one.
        # Anything that varies per pass has to live where a later pass can add to it without
        # rewriting it (see `SourceFile`).
        record.header = (
            f"Read {len(source.sections)} sections; structure recovered as "
            f"`{source.structure_method.value}`."
        )
    state = _State(
        kb_root=kb_root,
        topic=topic,
        staged=staged,
        rel_path=rel_path,
        document=document,
        record=record,
        snapshot=snapshot,
        today=today,
        lock=lock,
    )

    for index, section in enumerate(source.sections, start=1):
        if section.title in record.accounted:
            continue
        if section.is_empty:
            record.no_text.append(section.title)
            await state.persist()
            continue
        takes = await _read_section(ask, source, section, index, state)
        await state.apply(section.title, takes)
        await state.persist()

    if state.document is not None and not record.complete:
        await _read_across(ask, state)
        record.complete = True
        await state.persist()

    unread = tuple(
        section.title for section in source.sections if section.title not in record.accounted
    )
    return IngestionReport(
        agent_id=agent_id,
        topic_path=topic.path,
        origin=source.origin,
        slug=staged.slug,
        path=rel_path if state.document is not None else None,
        gainful=state.document is not None,
        pass_number=record.number,
        resumed=resumed,
        sections_total=len(source.sections),
        covered=tuple(record.took),
        nothing=tuple(record.nothing),
        held=tuple(record.held),
        unread=unread,
        flagged=tuple(record.flagged),
        conflicts=tuple(record.conflicts),
        touched=tuple(state.touched),
        copied_original=state.copied,
        gate=state.gate.value if state.gate is not None else None,
        findings=tuple(state.findings),
    )


def _pass_for(document: SourceFile | None, today: date) -> tuple[PassRecord, bool]:
    """The pass this run belongs to: resume the last one, or open the next (LS-5, LS-11).

    An incomplete last pass is a run that died, and continuing it is the resume the design promises.
    A complete one means this is a genuine re-reading, and it gets its own record so the file carries
    one entry per reading rather than one entry that keeps being rewritten.
    """
    records = document.passes() if document is not None else []
    if records and not records[-1].complete:
        return records[-1], True
    return PassRecord(number=len(records) + 1, read_on=today), False


@dataclass(slots=True)
class _State:
    """Everything one run of the loop mutates. Not part of the public surface."""

    kb_root: Path
    topic: TopicRecord
    staged: StagedSource
    rel_path: str
    document: SourceFile | None
    record: PassRecord
    snapshot: Callable[[], KbSnapshot]
    today: date
    lock: KbWriteLock
    touched: list[str] = field(default_factory=list)
    copied: str | None = None
    gate: GateReason | None = None
    findings: list[Finding] = field(default_factory=list)

    async def apply(self, title: str, takes: Sequence[Take]) -> None:
        """Fold one section's answer into the file, per LS-12's destructiveness line."""
        additions = [take.text for take in takes if take.kind is TakeKind.NEW]
        rewordings = [take.text for take in takes if take.kind is TakeKind.BETTER]
        contradictions = [take.text for take in takes if take.kind is TakeKind.CONTRADICTS]

        if additions and self.document is None:
            # LS-6: the first argument is what earns the folder, the file and the copy. Until then
            # a topic that takes nothing leaves nothing at all behind.
            self.copied = await asyncio.to_thread(self._copy_original)
            self.document = SourceFile.create(
                topic=self.topic,
                source=self.staged.extracted,
                slug=self.staged.slug,
                today=self.today,
                original=self.copied,
            )
        landed: list[str] = []
        if additions and self.document is not None:
            order = [section.title for section in self.staged.extracted.sections]
            landed = self.document.add_arguments(title, additions, order=order)

        for text in rewordings:
            # Flagged, never applied: it would replace text the human may have read and relied on.
            self.record.flagged.append(f"{title} — {text}")
        for text in contradictions:
            self.record.conflicts.append(f"{title} — {text}")

        if landed:
            self.record.took.append(title)
        elif title in self.record.accounted:
            pass
        elif rewordings or contradictions:
            self.record.held.append(title)
        else:
            self.record.nothing.append(title)

        if contradictions and self.document is not None:
            self._flag_conflict()

    async def persist(self) -> None:
        """Write the file if there is one — after every section, not at the end.

        A run that dies at chapter 14 must leave fourteen chapters behind. Before the first argument
        there is nothing to write and nothing is created, which is the same rule seen from the other
        side (LS-6).

        The write goes through :func:`asyncio.to_thread` because the knowledge-base write lock is a
        mutex a synchronous hook can also hold (RT-51): taking it on the event-loop thread would
        block every other run's streaming for as long as some other flush held it.
        """
        if self.document is None:
            return
        self.document.record_pass(self.record)
        self.document.set_field("updated", self.today)
        written, reason, findings = await asyncio.to_thread(
            _write,
            self.kb_root,
            self.rel_path,
            self.document.render(),
            lock=self.lock,
            snapshot=self.snapshot,
        )
        if reason is not None:
            self.gate = reason
        if findings:
            self.findings = findings
        if written and self.rel_path not in self.touched:
            self.touched.append(self.rel_path)

    def _flag_conflict(self) -> None:
        """§1.7's three steps, applied to the **source file** (README §1.7, LS-5).

        The one conflict with no human side: one reading of a source against another. "Human content
        wins" decides nothing there, so the file to flag is the reference itself — tag it, add the
        one-line note, change nothing. Adding the flag is deliberately un-gated (RT-26): README
        instructs the AI to tag, it changes no content, and gating it would block every background
        scan on a human.
        """
        if self.document is None:
            return
        self.document.set_status(CONFLICT_TAG)
        self.document.set_field(
            "review_note",
            f"Pass {self.record.number} of this source contradicts an earlier reading: "
            f"{self.record.conflicts[-1]}",
        )

    def _copy_original(self) -> str | None:
        """LS-1's copy, made by the workflow rather than by the model.

        Deterministic consequence of LS-6 — this expert filed at least one argument — so there is
        nothing to gain from routing it through a tool call the model must remember to make, and a
        binary cannot go through ``write_file`` in any case. See the module docstring for how this
        sits with RT-18.
        """
        name = _copy_name(self.staged, self.rel_path)
        destination = f"{self.topic.path}/{REFERENCES_DIR}/{self.staged.slug}/{name}"
        target = self.kb_root / destination
        try:
            with self.lock:
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    shutil.copy2(self.staged.original, target)
        except OSError:
            # A missing copy costs the topic folder its self-containedness; a raised exception
            # costs the whole reading. The provenance block simply omits the link.
            return None
        if destination not in self.touched:
            self.touched.append(destination)
        return name


_MARKDOWN_SUFFIXES: Final = frozenset({".md", ".markdown", ".mdown"})
"""Suffixes Layer 1 treats as authored markdown, and therefore validates for frontmatter."""


def _copy_name(staged: StagedSource, rel_path: str) -> str:
    """What the copied original is called inside the reference folder.

    Almost always ``<slug><ext>``, byte-identical to what arrived.

    The exception is a source that *is* markdown. Two things go wrong with ``<slug>.md``: it collides
    with the extraction file's own name, and — the one that bites — a ``.md`` file inside the tree is
    an **authored** file to Layer 1, which requires the seven frontmatter fields and reports
    ``MISSING_FRONTMATTER`` when a raw source has none. A PDF or an HTML original is an asset and
    exempt (FM-14, VA-7); a markdown original is not.

    So a markdown original is copied as ``<slug>.source.txt``. The bytes are unchanged; only the name
    is, and ``.txt`` puts it in the class Layer 1 already leaves alone. Adding frontmatter instead
    would make the copy no longer the source, and exempting it in Layer 1 would mean changing the
    layer this feature promised not to touch.
    """
    name = f"{staged.slug}{staged.original.suffix}"
    if name != Path(rel_path).name and staged.original.suffix.lower() not in _MARKDOWN_SUFFIXES:
        return name
    return f"{staged.slug}.source.txt"


# --------------------------------------------------------------------------------------
# Asking about one section
# --------------------------------------------------------------------------------------


async def _read_section(
    ask: Asker, source: ExtractedSource, section: Section, index: int, state: _State
) -> tuple[Take, ...]:
    """One section, in as many bounded windows as it needs (LS-9)."""
    recorded = state.document.bullets(section.title) if state.document is not None else ()
    takes: list[Take] = []
    windows = list(_windows(section.text))
    for position, window in enumerate(windows, start=1):
        question = _section_question(
            source, section, index, position, len(windows), window, recorded
        )
        takes.extend(parse_takes(await ask(question)))
    return tuple(takes)


async def _read_across(ask: Asker, state: _State) -> None:
    """The one question about the whole source, asked last and over the arguments, not the text."""
    document = state.document
    if document is None:
        return
    collected = [
        f"## {heading}\n" + "\n".join(f"- {text}" for text in document.bullets(heading))
        for heading in document.chapters
    ]
    if not collected:
        return
    question = "\n\n".join([*collected, ACROSS_INSTRUCTION])
    takes = parse_takes(await ask(question), allow=(TakeKind.NEW,))
    added = [take.text for take in takes]
    if added:
        document.add_arguments(ACROSS_HEADING, added)


def _section_question(
    source: ExtractedSource,
    section: Section,
    index: int,
    position: int,
    windows: int,
    window: str,
    recorded: Sequence[str],
) -> str:
    """The whole of what the expert sees about one section.

    It names where the section sits in the source, because "chapter 3 of 20" is what makes an
    expert's judgement about *this* chapter rather than about the book — and because a model told it
    is reading a fragment stops trying to summarise the whole.
    """
    header = f"Source: {source.title} ({source.kind}). Section {index} of {len(source.sections)}."
    if windows > 1:
        header += f" Part {position} of {windows} of this section."
    lines = [header, "", f"## {section.title}", "", window]
    if recorded:
        lines += [
            "",
            "Already recorded for this section by an earlier reading:",
            *(f"- {text}" for text in recorded),
        ]
    lines += ["", SECTION_INSTRUCTION]
    return "\n".join(lines)


def _windows(text: str, limit: int = SECTION_WINDOW_CHARS) -> Iterator[str]:
    """Split one section's text into bounded windows on paragraph boundaries (LS-9).

    Paragraphs rather than characters so a window never cuts an argument in half, and a single
    paragraph longer than the bound is emitted whole rather than sliced — a mid-sentence cut costs
    more than an over-long window.
    """
    if len(text) <= limit:
        yield text
        return
    current: list[str] = []
    size = 0
    for paragraph in text.split("\n\n"):
        if current and size + len(paragraph) > limit:
            yield "\n\n".join(current)
            current, size = [], 0
        current.append(paragraph)
        size += len(paragraph) + 2
    if current:
        yield "\n\n".join(current)


# --------------------------------------------------------------------------------------
# The one write site (RT-18's sanctioned writer — see the module docstring)
# --------------------------------------------------------------------------------------


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _write(
    kb_root: Path,
    rel_path: str,
    text: str,
    *,
    lock: KbWriteLock,
    snapshot: Callable[[], KbSnapshot],
) -> tuple[bool, GateReason | None, list[Finding]]:
    """Validate, gate, then write — in that order, and never any other.

    The order is the whole safety argument for a harness write that bypasses the tool layer. Layer 1
    decides whether the content is legal (MW-9's single call site is on the *validation* path; this
    is a second sanctioned caller in the same spirit), the gate table decides whether a human must
    see it first (RT-21), and only then does anything land. A write that skipped either would be
    exactly the ad-hoc writer RT-18's corollary exists to forbid.
    """
    findings = validate_content(kb_root, rel_path, text)
    if has_errors(findings):
        return False, None, errors_only(findings)
    reason = requires_approval(WRITE_FILE_TOOL, rel_path, {"content": text}, snapshot())
    if reason is not None:
        return False, reason, []
    target = kb_root / rel_path
    with lock:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return True, None, []


# --------------------------------------------------------------------------------------
# The entry point the expert reaches (LS-11)
# --------------------------------------------------------------------------------------


class IngestHost(Protocol):
    """What :func:`ingest_source_tool` needs from the runtime, and nothing more.

    A Protocol rather than an import because :mod:`pkb.agents.runtime` imports the registry, which
    imports the expert factory, which is handed these tools — so a nominal type would close the
    circle.
    """

    async def ingest(
        self,
        agent_id: str,
        origin: str,
        *,
        confirm: bool = False,
        refresh: bool = False,
        maintain: bool = True,
    ) -> IngestionReport: ...


def ingest_source_tool(host: IngestHost, agent_id: str) -> BaseTool:
    """The expert's way into the loop — and the point past which it cannot stop reading.

    Calling this tool is the model's last decision about the reading. Everything after it is code:
    which sections are opened, in what order, what is written, and when the pass is finished. That is
    the difference between this and the ingestion the ``ingestion-classification`` skill describes
    for a link, and it is why the skill branches on size *first* (§6.4).

    The tool returns a ``Command`` rather than a string so the paths the loop wrote reach
    ``kb_touched`` (MW-18's mechanism). Without that the file lands on disk, no ``updated`` stamp is
    bumped, the topic index never lists it and no conflict scan is queued — the exact hole LS-1's
    second amendment names.
    """

    async def ingest_source(
        origin: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        confirm_reingest: bool = False,
    ) -> Command[Any]:
        try:
            report = await host.ingest(agent_id, origin, confirm=confirm_reingest, maintain=False)
        except SourceError as exc:
            # Extraction quality is visible rather than assumed (LS-7): a scanned PDF, an encrypted
            # one or a URL that would not load fails loudly here rather than producing a confident
            # summary of nothing. It is a refusal the model can relay, never an exception — one
            # escaping a tool body aborts the superstep and takes the maintenance flush with it.
            return _answer(tool_call_id, f"Refused — nothing was ingested. {exc}", ())
        return _answer(tool_call_id, report.summary(), report.touched)

    return StructuredTool.from_function(
        coroutine=ingest_source,
        name=INGEST_TOOL,
        description=(
            "Read a source that is too large for one turn — a book, a paper, a long report — into "
            "this topic's references, section by section. `origin` is a filesystem path or a URL. "
            "The system stages the source, extracts it, walks its own chapters or sections in "
            "order, asks you what your topic takes from each, and writes the file as it goes; you "
            "do not decide when the reading is finished and you never write the file yourself. "
            "Use it instead of drafting a reference by hand whenever the material will not fit in "
            "one turn. If the source is already ingested here you are told so and asked to check "
            "with the human before re-reading it; call again with confirm_reingest=true once they "
            "agree."
        ),
    )


def _answer(tool_call_id: str, text: str, touched: Sequence[str]) -> Command[Any]:
    """One tool answer, carrying the touched paths the flush needs (MW-18).

    The ``messages`` entry is mandatory: ``ToolNode._validate_tool_command`` raises unless the update
    carries a ``ToolMessage`` whose ``tool_call_id`` matches, and a bare ``ToolMessage`` cannot carry
    a state update at all.
    """
    message = ToolMessage(content=text, name=INGEST_TOOL, tool_call_id=tool_call_id)
    return Command(update={"messages": [message], KB_TOUCHED: list(touched)})


def ingest_tools(host: IngestHost, agent_id: str, *, is_expert: bool) -> list[BaseTool]:
    """The ingestion tool for a Topic Expert, and nothing for the Librarian.

    The Librarian holds no knowledge-base write capability at all (RT-16) and no topic lens to read
    through (LB-5). A source reaches an expert the same way everything else does — the fan-out —
    and each expert reads the whole of it through its own lens (LS-4, decision G).
    """
    return [ingest_source_tool(host, agent_id)] if is_expert else []
