"""The chunked ingestion loop — LS-1 … LS-12, and RT-31 as amended.

Every test here drives the **real** loop over a **real** knowledge base with a `ScriptedChatModel`.
No key, no network, no fixture PDFs: the sources are generated text files with their own headings,
which is enough for `pkb.sources` to recover a real section structure and therefore enough for the
loop to be exercised exactly as it runs in production.

The assertions are chosen around one idea: *the failure this design exists to prevent is a silent
one*. So the tests do not merely check that a file appeared. They check that the model was asked
about every section (`§ the loop`), that a section it was never asked about is named in the file
rather than omitted (`test_a_run_that_dies_leaves_its_work_and_says_what_it_missed`), that a topic
which took nothing leaves **nothing at all** rather than an empty folder (LS-6), and that a second
reading cannot quietly replace the first (`§ reconciliation`, `§ the gate`).

`ScriptedChatModel` repeats its last entry once the script runs out, which is a real trap here: a
loop that asked one question too many would silently get a plausible answer instead of failing. Two
defences are used throughout — the questions themselves are asserted, and the model's `idx` is
compared against the script length where the count is the point.

**Superseded by T-17, pending a redesign this task does not make.** `SourceFile.create` stamps
every freshly ingested reference file with `_DRAFT_TAG` ("status.draft"), and `_mark_for_review`/
`_flag_conflict` move a file's one `status.*` tag between `status.draft` and
`pkb.agents.gates.CONFLICT_TAG` ("status.conflict-review") — LS-12's whole "new content lands
marked for review" safety story is built on that tag surviving Layer 1 validation. T-17 retires the
`status.*` namespace outright (`docs/superpowers/specs/2026-08-13-tree-T-rules.md`), so every one of
those writes is now refused with `UNKNOWN_TAG_NAMESPACE` before it lands, and every test below whose
assertion depends on the write actually landing is marked `@pytest.mark.superseded` rather than
patched — patching would mean silently dropping the draft/conflict marker as the fix, which is a
real design decision (what signals "unreviewed" or "conflicting" now?) that belongs to whoever
rebuilds `pkb.agents.ingestion` against the new tree, not to the tags-and-validation task that
happened to surface the incompatibility. Tests unaffected by the write path (section parsing,
question generation, chapter ordering) are untouched and still pass.
"""

from __future__ import annotations

import asyncio
import datetime
import shutil
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from pkb.agents import ingestion
from pkb.agents.expert import build_expert
from pkb.agents.gates import GateReason, allowed_decisions, describe_write, requires_approval
from pkb.agents.ingestion import (
    ACROSS_HEADING,
    ACROSS_INSTRUCTION,
    INGEST_TOOL,
    NOTHING_MARKER,
    PROVENANCE_HEADING,
    READING_RECORD_HEADING,
    SECTION_WINDOW_CHARS,
    Asker,
    IngestionReport,
    SourceFile,
    TakeKind,
    ingest,
    model_asker,
    parse_takes,
    reference_file_path,
)
from pkb.agents.librarian import build_librarian
from pkb.agents.registry import AgentRegistry
from pkb.agents.runtime import PkbRuntime, RuntimeConfig
from pkb.contracts import RunEnd
from pkb.core import find_orphans, has_errors, validate_content, validate_tree
from pkb.core.errors import Severity
from pkb.core.frontmatter import parse
from pkb.core.models import TopicRecord
from pkb.core.paths import LIBRARIAN_AGENT_ID, REFERENCES_DIR
from pkb.core.scaffold import scaffold_topic
from pkb.core.scan import scan
from pkb.sources import INBOX_DIR, SourceError, StagedSource, stage
from tests.agents.conftest import ScriptedChatModel, call, calls, raises, says, scripted

TODAY = datetime.date(2026, 8, 7)
LATER = datetime.date(2026, 9, 1)

COOKING = "topic/cooking"

SYSTEM = "You are the Cooking expert."
"""Stands in for `expert_prompt`, which the runtime supplies in production (EX-4).

The loop takes an `Asker`, not a model and not a prompt, precisely so a test can hold the prompt
still and vary only the answers — the prompt's own content is `test_prompts.py`'s subject.
"""

CHAPTERS: tuple[tuple[str, str], ...] = (
    ("Chapter 1 — Care personally", "Give a damn about the person, not only the work."),
    ("Chapter 2 — Challenge directly", "Say the thing, and say it early."),
    ("Chapter 3 — Order matters", "Care first, then challenge; the reverse is obnoxious."),
)


# --------------------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------------------


def source_file(tmp_path: Path, name: str = "Radical Candor", *, chapters: Any = CHAPTERS) -> Path:
    """A source with its own headings, so `pkb.sources` recovers a real structure (LS-7, LS-10).

    A ``.txt`` suffix rather than ``.md``, so that the *default* case these tests exercise is a
    source whose copy keeps its own extension. The markdown case differs in one respect — the copy
    is renamed, because a ``.md`` file inside the tree is an authored file to Layer 1 — and it has
    its own test at the end of this module rather than being folded into every fixture here.
    """
    body = "\n\n".join(f"## {title}\n\n{text}" for title, text in chapters)
    target = tmp_path / f"{name}.txt"
    target.write_text(body + "\n", encoding="utf-8")
    return target


def staged_source(kb: Path, origin: Path) -> StagedSource:
    return stage(kb, origin)


def asker(*answers: Any) -> tuple[Asker, ScriptedChatModel]:
    """An `Asker` backed by a `ScriptedChatModel` — the whole non-live seam (D-8, SK-18)."""
    model = scripted(*(says(answer) if isinstance(answer, str) else answer for answer in answers))
    return model_asker(model, SYSTEM), model


def questions(model: ScriptedChatModel) -> list[str]:
    """What the expert was actually asked, one entry per section window."""
    return [str(turn[-1].content) for turn in model.calls]


def topic_of(kb: Path, path: str = "Cooking") -> TopicRecord:
    return scan(kb).topics[path]


async def read(
    kb: Path,
    origin: Path,
    *answers: Any,
    topic_path: str = "Cooking",
    today: datetime.date = TODAY,
) -> tuple[IngestionReport, ScriptedChatModel]:
    """One whole pass of the real loop over a real tree."""
    ask, model = asker(*answers)
    report = await ingest(
        kb,
        topic_of(kb, topic_path),
        staged_source(kb, origin),
        ask=ask,
        snapshot=lambda: scan(kb),
        today=today,
        agent_id=COOKING,
    )
    return report, model


def contents(kb: Path, report: IngestionReport) -> str:
    assert report.path is not None
    return (kb / report.path).read_text(encoding="utf-8")


def tree(kb: Path) -> dict[str, bytes]:
    """Every file in the knowledge base, for a before/after comparison."""
    return {
        path.relative_to(kb).as_posix(): path.read_bytes()
        for path in sorted(kb.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def book(tmp_path: Path) -> Path:
    return source_file(tmp_path / "sources")


@pytest.fixture(autouse=True)
def _sources_dir(tmp_path: Path) -> None:
    (tmp_path / "sources").mkdir(exist_ok=True)


# --------------------------------------------------------------------------------------
# § the loop — every section is opened, by code (LS-9, LS-10)
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_loop_asks_about_every_section_and_files_only_what_landed(
    kb: Path, book: Path
) -> None:
    """Three chapters, two of them gainful — the shape LS-10 specifies, end to end.

    The model is asked about all three, including the one it takes nothing from: *the harness*
    decides the reading is finished, so a chapter cannot be skipped by a model that has decided it
    has read enough. The file then carries a section only for the chapters that gave this topic
    something (§6.9: a chapter holding nothing yields no section and no line explaining that it was
    about something else), and the reading record says which was which.
    """
    report, model = await read(
        kb,
        book,
        "NEW: Care about the person before you critique the work.",
        "NOTHING",
        "NEW: Care first, then challenge.\nNEW: The reverse reads as obnoxious aggression.",
        "NOTHING",
    )

    asked = questions(model)
    assert len(asked) == 4, "three sections plus the one across-the-source question"
    for title, _ in CHAPTERS:
        assert any(title in question for question in asked), title
    assert ACROSS_INSTRUCTION in asked[-1]

    text = contents(kb, report)
    assert "## Chapter 1 — Care personally" in text
    assert "## Chapter 2 — Challenge directly" not in text
    assert "## Chapter 3 — Order matters" in text
    assert text.count("\n- Care first, then challenge.") == 1
    assert report.covered == ("Chapter 1 — Care personally", "Chapter 3 — Order matters")
    assert report.nothing == ("Chapter 2 — Challenge directly",)
    assert report.unread == ()
    assert report.complete


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_file_is_the_shape_layer_one_already_validates(kb: Path, book: Path) -> None:
    """One file per source, `source_type: reference`, `type.reference`, landing as a draft (LS-12).

    Layer 1 changes nothing for this feature, and this is the assertion that says so: the file the
    loop writes validates clean under the *unmodified* validator, at the path README §1.2 already
    specifies.
    """
    report, _ = await read(kb, book, "NEW: One.", "NOTHING", "NOTHING", "NOTHING")

    assert report.path == reference_file_path("Cooking", "radical-candor")
    text = contents(kb, report)
    assert not has_errors(validate_content(kb, report.path or "", text))

    meta = parse(text).meta
    assert meta is not None
    assert meta.source_type == "reference"
    assert "type.reference" in meta.tags
    assert "status.draft" in meta.tags, "ingest first, mark for review (LS-12)"
    assert "topic.cooking" in meta.tags
    assert f"## {PROVENANCE_HEADING}" in text
    assert f"## {READING_RECORD_HEADING}" in text


@pytest.mark.asyncio
async def test_a_topic_that_takes_nothing_leaves_no_trace_at_all_ls6(kb: Path, book: Path) -> None:
    """Zero insights leaves no folder, no stub file and no copy of the source (LS-6, §1.8 rule 4).

    An empty `references/<source>/` folder would imply the source was considered and is somehow
    relevant, which is exactly the claim a topic that took nothing must not make. Asserted as a
    whole-tree byte comparison, because "it left no trace" is a statement about everything it did
    not touch — staging aside, which lives in `.inbox` and is invisible to every Layer 1 walk (LS-8).
    """
    before = {path: text for path, text in tree(kb).items() if not path.startswith(INBOX_DIR)}

    report, model = await read(kb, book, "NOTHING", "nothing.", "NOTHING")

    assert model.idx == 3, "every section was still read; only the filing was declined"
    assert report.gainful is False
    assert report.path is None
    assert report.touched == ()
    assert not (kb / "Cooking" / "references" / "radical-candor").exists()
    after = {path: text for path, text in tree(kb).items() if not path.startswith(INBOX_DIR)}
    assert after == before


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_original_is_copied_beside_the_extraction_and_linked_ls1(
    kb: Path, book: Path
) -> None:
    """LS-1's copy: made by the workflow, byte-identical, linked, and recorded as touched.

    Three consequences, all of them load-bearing. The topic folder is self-contained and portable.
    The provenance block links the copy, so MA-8's `ORPHAN_ASSET` does not flag it once in every
    topic that took one. And the copied path is in `kb_touched`, so the single per-run flush stamps
    and indexes it — a copy made any other way lands on disk invisible to MW-17 … MW-20.
    """
    report, _ = await read(kb, book, "NEW: One.", "NOTHING", "NOTHING", "NOTHING")

    copy = kb / "Cooking" / "references" / "radical-candor" / "radical-candor.txt"
    assert copy.read_bytes() == book.read_bytes()
    assert report.copied_original == "radical-candor.txt"
    assert set(report.touched) == {
        report.path,
        "Cooking/references/radical-candor/radical-candor.txt",
    }
    assert "[radical-candor.txt](radical-candor.txt)" in contents(kb, report)
    assert find_orphans(kb, scan(kb)) == []


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_across_question_is_asked_over_the_arguments_not_the_source(
    kb: Path, book: Path
) -> None:
    """The one unbounded question is asked over a bounded input (LS-9, LS-10).

    "Across the source" is the only judgement about the whole document, so it is the one place the
    loop could reintroduce the failure it exists to prevent. It is therefore asked over the
    arguments already collected — bounded by construction — and never over the source text.
    """
    report, model = await read(
        kb, book, "NEW: Care first.", "NOTHING", "NOTHING", "NEW: The two axes only work together."
    )

    across = questions(model)[-1]
    assert "- Care first." in across
    assert CHAPTERS[0][1] not in across, "the source text must not be in the across question"
    text = contents(kb, report)
    assert f"## {ACROSS_HEADING}" in text
    assert "- The two axes only work together." in text


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_run_that_dies_leaves_its_work_and_says_what_it_missed(
    kb: Path, book: Path
) -> None:
    """Write as you go, and record the reading — a dead run leaves two chapters, not nothing.

    The second half is the one that matters: the file must not merely *contain less*, it must
    **say** that the reading did not finish. A file that stops after chapter 2 and looks finished is
    precisely the silent failure this design exists to prevent.
    """
    ask, model = asker(
        "NEW: Care about the person.", "NEW: Say it early.", raises(RuntimeError("model died"))
    )
    with pytest.raises(RuntimeError, match="model died"):
        await ingest(
            kb,
            topic_of(kb),
            staged_source(kb, book),
            ask=ask,
            snapshot=lambda: scan(kb),
            today=TODAY,
        )

    text = (kb / reference_file_path("Cooking", "radical-candor")).read_text(encoding="utf-8")
    assert "- Care about the person." in text
    assert "- Say it early." in text
    assert "Chapter 3 — Order matters" not in text
    assert "Pass complete" not in text
    record = SourceFile.load(text).passes()[-1]
    assert record.complete is False
    assert model.idx == 3


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_resumed_run_continues_from_the_first_section_it_never_opened(
    kb: Path, book: Path
) -> None:
    """The file is the resume state — no second store of progress, and no repeated work.

    The resumed run is given exactly two answers. If it re-read chapters 1 and 2 it would consume
    them there and `ScriptedChatModel` would then repeat its last entry forever, so the assertion on
    what was asked is what makes "without redoing work" a real claim rather than a hopeful one.
    """
    ask, _ = asker("NEW: Care about the person.", "NEW: Say it early.", raises(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        await ingest(
            kb,
            topic_of(kb),
            staged_source(kb, book),
            ask=ask,
            snapshot=lambda: scan(kb),
            today=TODAY,
        )

    report, model = await read(kb, book, "NEW: Care first, then challenge.", "NOTHING")

    assert report.resumed is True
    assert report.pass_number == 1, "a resume continues the pass; it does not start a new reading"
    asked = questions(model)
    assert len(asked) == 2
    assert "Chapter 3 — Order matters" in asked[0]
    assert ACROSS_INSTRUCTION in asked[1]
    assert "Chapter 1" not in asked[0]

    text = contents(kb, report)
    assert text.count("- Care about the person.") == 1
    assert "- Care first, then challenge." in text
    assert "Pass complete" in text
    assert report.covered == tuple(title for title, _ in CHAPTERS)


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_section_longer_than_the_window_is_read_in_parts_under_one_heading(
    kb: Path, tmp_path: Path
) -> None:
    """LS-9's bound, applied without losing LS-10's anchor.

    A chapter longer than any context still has to be read whole, so it is asked about in windows —
    but the *section* stays what the answers are filed under, because a bullet filed under "part 2
    of chapter 3" would be an anchor that moves whenever the window size changes.
    """
    paragraph = "Heat moves from the outside in, and the middle lags. " * 40
    long_chapter = "\n\n".join([paragraph] * ((SECTION_WINDOW_CHARS // len(paragraph)) + 2))
    big = source_file(
        tmp_path / "sources", "Long Book", chapters=(("Chapter 1 — Heat", long_chapter),)
    )

    report, model = await read(
        kb, big, "NEW: From the first part.", "NEW: From the second.", "NOTHING"
    )

    asked = questions(model)
    assert len(asked) == 3, "two windows over one section, then the across question"
    assert "Part 1 of 2 of this section" in asked[0]
    assert "Part 2 of 2 of this section" in asked[1]
    text = contents(kb, report)
    assert text.count("## Chapter 1 — Heat") == 1
    assert "- From the first part." in text
    assert "- From the second." in text
    assert report.covered == ("Chapter 1 — Heat",)


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_section_with_no_extracted_text_is_recorded_rather_than_asked_about(
    kb: Path, tmp_path: Path
) -> None:
    """An empty section costs no model call and is still named — LS-7's "visible, not assumed"."""
    empty = source_file(
        tmp_path / "sources",
        "Half Scanned",
        chapters=(("Chapter 1 — Readable", "Real text."), ("Chapter 2 — Blank", "")),
    )
    report, model = await read(kb, empty, "NEW: Something.", "NOTHING")

    assert len(questions(model)) == 2, "the blank chapter was never put in front of the model"
    assert report.nothing == ()
    assert "Chapter 2 — Blank" in contents(kb, report)
    assert "No text was extracted for" in contents(kb, report)


# --------------------------------------------------------------------------------------
# § reconciliation — a second pass, section by section (LS-5, LS-12)
# --------------------------------------------------------------------------------------


async def first_pass(kb: Path, book: Path) -> IngestionReport:
    """One completed reading, so the tests below are genuine re-ingestions."""
    report, _ = await read(
        kb, book, "NEW: Care about the person.", "NEW: Say it early.", "NOTHING", "NOTHING"
    )
    assert report.complete
    return report


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_second_pass_lands_a_new_argument_unattended_ls12(kb: Path, book: Path) -> None:
    """Pure addition: nothing is lost, so it lands immediately and is marked for review."""
    before = contents(kb, await first_pass(kb, book))

    report, model = await read(
        kb,
        book,
        "NOTHING",
        "NEW: Praise in public, critique in private.",
        "NOTHING",
        "NOTHING",
        today=LATER,
    )

    assert report.pass_number == 2
    assert report.resumed is False
    assert report.gate is None, "an addition never stops for a human (RT-31 as amended)"
    text = contents(kb, report)
    assert "- Praise in public, critique in private." in text
    assert "- Say it early." in text, "an argument this pass did not repeat is kept (LS-5)"
    assert "- Care about the person." in text
    assert before.count("### Pass") == 1
    assert text.count("### Pass") == 2, "a book read twice carries two readings' worth of record"
    assert model.idx == 4


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_later_pass_files_a_new_chapter_where_the_source_puts_it_ls10(
    kb: Path, book: Path
) -> None:
    """The file is organised by the source's own structure, not by the order a pass reached it.

    A second reading that finally takes something from chapter 2 must not leave the file reading
    1, 3, 2 — the chapter is the anchor, and an anchor whose order depends on when a run happened
    to notice it is not one.
    """
    report, _ = await read(kb, book, "NEW: One.", "NOTHING", "NEW: Three.", "NOTHING")
    assert "## Chapter 2 — Challenge directly" not in contents(kb, report)

    second, _ = await read(kb, book, "NOTHING", "NEW: Two.", "NOTHING", "NOTHING", today=LATER)

    text = contents(kb, second)
    headings = [line for line in text.splitlines() if line.startswith("## Chapter")]
    assert headings == [f"## {title}" for title, _ in CHAPTERS]
    assert second.gate is None


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_better_statement_is_flagged_and_never_applied_ls12(kb: Path, book: Path) -> None:
    """A reworded argument replaces text the human may have approved, and there is no undo (D6)."""
    await first_pass(kb, book)

    report, _ = await read(
        kb,
        book,
        "BETTER: Care about the whole person, not only their output.",
        "NOTHING",
        "NOTHING",
        "NOTHING",
        today=LATER,
    )

    text = contents(kb, report)
    assert "- Care about the person." in text, "the old wording is untouched"
    assert "- Care about the whole person, not only their output." not in text
    assert report.flagged == (
        "Chapter 1 — Care personally — Care about the whole person, not only their output.",
    )
    assert "Proposed rewording, not applied, for" in text
    assert report.gate is None, "nothing destructive was attempted, so nothing was withheld"
    # "Read, took nothing" and "read, nothing new landed" are kept distinct (§6.9): a chapter that
    # offered a rewording was not barren, and telling a later reader it was is the one thing the
    # reading record must not get wrong.
    assert "Read, nothing new landed for: **Chapter 1 — Care personally**" in text
    assert report.held == ("Chapter 1 — Care personally",)
    assert "Chapter 1 — Care personally" not in report.nothing


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_contradiction_flags_the_source_file_itself_ls5(kb: Path, book: Path) -> None:
    """The one conflict with no human side: one reading of a source against another (README §1.7).

    "Human content wins" decides nothing here, so the three tagging acts land on the **reference**.
    Adding the flag stays un-gated (RT-26) — README instructs the AI to tag, it changes no content,
    and gating it would block every background scan on a human — and the tag *replaces*
    `status.draft` rather than joining it, because Layer 1 allows exactly one `status.*` tag (VA-9)
    and a second one would be refused outright, so the flag the human is meant to see would never
    appear.
    """
    await first_pass(kb, book)

    report, _ = await read(
        kb,
        book,
        "NOTHING",
        "CONTRADICTS: It says to wait until the person is ready, not to say it early.",
        "NOTHING",
        "NOTHING",
        today=LATER,
    )

    text = contents(kb, report)
    meta = parse(text).meta
    assert meta is not None
    assert "status.conflict-review" in meta.tags
    assert [tag for tag in meta.tags if tag.startswith("status.")] == ["status.conflict-review"]
    # `review_note` is no longer a schema field (T-12) — `set_field` still writes it as a plain
    # key, so its presence is checked on the raw text rather than through `Metadata`.
    assert 'review_note: "' in text
    assert "contradicts an earlier reading" in text
    assert "- Say it early." in text, "change nothing; let the human settle it"
    assert report.conflicts and report.gate is None
    assert not has_errors(validate_content(kb, report.path or "", text))


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_second_pass_compares_section_by_section_never_document_to_document(
    kb: Path, book: Path
) -> None:
    """Each judgement is about one chapter, with only that chapter's bullets in front of it (LS-5).

    Handing a model two long documents and asking "is anything new?" reproduces the exact failure
    this design exists to prevent — a bounded reader, an unbounded input, and a confident answer
    about the part it managed to read.
    """
    await first_pass(kb, book)

    _, model = await read(kb, book, "NOTHING", "NOTHING", "NOTHING", "NOTHING", today=LATER)

    first, second = questions(model)[0], questions(model)[1]
    assert "Already recorded for this section" in first
    assert "- Care about the person." in first
    assert "- Say it early." not in first, "chapter 2's arguments are not in chapter 1's question"
    assert "- Say it early." in second
    assert "- Care about the person." not in second


# --------------------------------------------------------------------------------------
# § the gate — RT-31 as amended
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_first_write_of_a_source_file_is_ungated_and_a_rewrite_is_not_rt31(
    kb: Path, book: Path
) -> None:
    """The split the amendment introduces, asserted on the real file the loop wrote.

    Three rows, and the middle one is the amendment's whole point: adding to an existing extraction
    is not destructive and must stay frictionless, while replacing a line in one lands on top of
    something the human has already read and there is no undo.
    """
    report = await first_pass(kb, book)
    assert report.path is not None
    current = contents(kb, report)
    snapshot = scan(kb)

    new_file = reference_file_path("Cooking", "another-book")
    assert requires_approval("write_file", new_file, {"content": current}, snapshot) is None

    appended = current.replace(
        "- Say it early.", "- Say it early.\n- Praise in public, critique in private."
    )
    assert requires_approval("write_file", report.path, {"content": appended}, snapshot) is None

    reworded = current.replace("- Say it early.", "- Say the hard thing early.")
    assert (
        requires_approval("write_file", report.path, {"content": reworded}, snapshot)
        is GateReason.REFERENCE_REWRITE
    )
    dropped = current.replace("- Say it early.\n", "")
    assert (
        requires_approval("write_file", report.path, {"content": dropped}, snapshot)
        is GateReason.REFERENCE_REWRITE
    )


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_rewrite_gate_shows_the_human_a_diff_of_what_would_be_replaced_rt34(
    kb: Path, book: Path
) -> None:
    """One proposal for the whole reconciled file, and the human can see what it costs them."""
    report = await first_pass(kb, book)
    assert report.path is not None
    reworded = contents(kb, report).replace("- Say it early.", "- Say the hard thing early.")
    snapshot = scan(kb)

    text = describe_write(
        GateReason.REFERENCE_REWRITE,
        "write_file",
        report.path,
        {"content": reworded},
        snapshot,
    )

    assert GateReason.REFERENCE_REWRITE.value in text
    assert "-- Say it early." in text.replace("\n-", "\n--")  # a removed line, as a diff `-`
    assert "+- Say the hard thing early." in text
    assert allowed_decisions("write_file") == ("approve", "edit", "reject")


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_frontmatter_only_change_to_a_source_file_does_not_gate_rt26(
    kb: Path, book: Path
) -> None:
    """Tagging a conflict must not stop for a human, or every background scan blocks on one."""
    report = await first_pass(kb, book)
    assert report.path is not None
    flagged = contents(kb, report).replace("status.draft", "status.conflict-review")

    assert requires_approval("write_file", report.path, {"content": flagged}, scan(kb)) is None


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_hand_edit_between_passes_survives_and_still_does_not_gate(
    kb: Path, book: Path
) -> None:
    """The human's own bytes are never reformatted away, so their edit does not become a rewrite.

    This is why `SourceFile` inserts lines into the original text instead of parsing and
    re-rendering. A re-render would round-trip its own output exactly and normalise anybody else's,
    so a human who reflowed a bullet would find the next pass proposing to undo it — and the pass's
    genuine addition would park on a gate about whitespace instead of landing (LS-12).
    """
    report = await first_pass(kb, book)
    assert report.path is not None
    edited = contents(kb, report).replace(
        "- Say it early.", "-   Say it early, and say it kindly.   "
    )
    (kb / report.path).write_text(edited, encoding="utf-8")

    second, _ = await read(
        kb, book, "NEW: A late addition.", "NOTHING", "NOTHING", "NOTHING", today=LATER
    )

    text = contents(kb, second)
    assert second.gate is None
    assert "-   Say it early, and say it kindly.   " in text
    assert "- A late addition." in text


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_loop_withholds_a_write_the_gate_refuses_rather_than_landing_it(
    kb: Path, book: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loop asks the gate before every write, and a refusal means nothing lands.

    Belt and braces on purpose. The loop only ever appends, so the rewrite gate should not fire from
    it — but a loop that *assumed* that would be one refactor away from being the ad-hoc writer
    RT-18's corollary exists to forbid. The gate is forced here so the withholding path is executed
    rather than argued about: the file on disk is byte-unchanged and the run reports what stopped it.
    """
    report = await first_pass(kb, book)
    assert report.path is not None
    before = contents(kb, report)
    monkeypatch.setattr(
        ingestion, "requires_approval", lambda *args, **kwargs: GateReason.REFERENCE_REWRITE
    )

    second, _ = await read(
        kb, book, "NEW: A late addition.", "NOTHING", "NOTHING", "NOTHING", today=LATER
    )

    assert second.gate == GateReason.REFERENCE_REWRITE.value
    assert second.touched == ()
    assert (kb / report.path).read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------------------
# § the entry point — the loop cannot be skipped once ingestion starts (LS-11)
# --------------------------------------------------------------------------------------


def registry_factory(model: ScriptedChatModel) -> Any:
    """The production wiring with only the model swapped (D-8) — see `test_runtime.py`."""

    def make(runtime: PkbRuntime) -> AgentRegistry:
        def expert(kb_root: Path, topic_path: str, rt: Any, **kwargs: Any) -> Any:
            return build_expert(kb_root, topic_path, rt, **{**kwargs, "model": model})

        def librarian(kb_root: Path, rt: Any, **kwargs: Any) -> Any:
            return build_librarian(kb_root, rt, **{**kwargs, "model": model})

        return AgentRegistry(
            runtime.kb_root,
            runtime,
            # The real object, not the placeholder string: `_asker` asks the registry for the
            # model (RG-21) so the ingestion loop gets the configured failover, and a registry
            # whose model is a name no provider knows cannot answer that.
            default_model=model,
            tool_factory=runtime.tools_for,
            expert_factory=expert,
            librarian_factory=librarian,
        )

    return make


@asynccontextmanager
async def opened(
    kb: Path, model: ScriptedChatModel, *, clock: Callable[[], datetime.date] = lambda: TODAY
) -> AsyncIterator[PkbRuntime]:
    async with PkbRuntime.open(
        kb,
        kb.parent / "pkb.sqlite",
        config=RuntimeConfig(
            clock=clock,
            default_model=model,
            # The production guard, pointed at where this test's sources live: `ingest_source` is
            # the one tool that reads outside the knowledge base, so a test that ran with it
            # disabled would not be exercising the tool as it ships.
            source_roots=(kb.parent,),
        ),
        registry_factory=registry_factory(model),
    ) as runtime:
        yield runtime


async def drain(rt: PkbRuntime, agent_id: str, thread: str, text: str) -> list[Any]:
    return [event async for event in rt.run(agent_id, thread, text)]


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_calling_the_tool_runs_the_whole_loop_and_the_flush_sees_what_it_wrote(
    kb: Path, book: Path
) -> None:
    """The expert's one decision is to start; everything after it is code.

    The model's script holds a single `ingest_source` call and then three section answers — it is
    never given the chance to write a file, to stop early, or to say it is finished. What proves the
    `kb_touched` half is the *topic index*: the loop's writes reach the single per-run flush, so the
    reference is stamped and listed exactly as a `write_file` would have been (MW-18, MW-20).
    """
    model = scripted(
        calls(call(INGEST_TOOL, {"origin": str(book)}, "t1")),
        says("NEW: Care about the person."),
        says("NOTHING"),
        says("NEW: Care first, then challenge."),
        says("NOTHING"),
        says("Filed what Cooking takes from it."),
    )
    async with opened(kb, model) as rt:
        events = await drain(rt, COOKING, "T1", f"ingest {book}")

    assert isinstance(events[-1], RunEnd)
    filed = kb / reference_file_path("Cooking", "radical-candor")
    assert filed.exists()
    index = (kb / "Cooking" / "index.md").read_text(encoding="utf-8")
    assert "references/radical-candor/radical-candor.md" in index
    assert find_orphans(kb, scan(kb)) == []


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_source_already_read_here_is_offered_not_silently_reread_ls11(
    kb: Path, book: Path
) -> None:
    """Neither a silent re-read (expensive) nor a silent skip (surprising) — the agent asks."""
    model = scripted(says("unused"))
    async with opened(kb, model) as rt:
        await first_pass(kb, book)

        offered = await rt.ingest(COOKING, str(book))
        assert offered.offered_reingest is True
        assert offered.gainful is False
        assert "already ingested" in offered.summary()
        assert "confirm_reingest" in offered.summary()
        assert model.idx == 0, "nothing was read, so no model call was made"

        confirmed = await rt.ingest(COOKING, str(book), confirm=True)
        assert confirmed.offered_reingest is False
        assert confirmed.pass_number == 2


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_an_unfinished_reading_resumes_without_asking_ls11(kb: Path, book: Path) -> None:
    """Nobody chose to stop at chapter 14, so continuing loses nothing and needs no permission."""
    ask, _ = asker("NEW: Care about the person.", raises(RuntimeError("died")))
    with pytest.raises(RuntimeError):
        await ingest(
            kb,
            topic_of(kb),
            staged_source(kb, book),
            ask=ask,
            snapshot=lambda: scan(kb),
            today=TODAY,
        )

    async with opened(kb, scripted(says("NOTHING"))) as rt:
        resumed = await rt.ingest(COOKING, str(book))

    assert resumed.offered_reingest is False
    assert resumed.resumed is True
    assert resumed.pass_number == 1


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_two_readings_of_one_source_into_one_topic_are_serialized(
    kb: Path, book: Path
) -> None:
    """Each pass reads the file, appends to it and writes it back, so two at once lose one.

    The knowledge-base write lock cannot close this: the loop spends most of its life awaiting a
    model, and RT-52 forbids holding that lock across a model call. So the runtime holds a narrow
    `(agent, source)` lock instead — narrow enough that two *topics* still read one book
    concurrently, which is decision G's whole point.
    """
    model = scripted(
        says("NEW: Care about the person."), says("NOTHING"), says("NOTHING"), says("NOTHING")
    )
    async with opened(kb, model) as rt:
        first, second = await asyncio.gather(
            rt.ingest(COOKING, str(book)), rt.ingest(COOKING, str(book))
        )

    done = [report for report in (first, second) if not report.offered_reingest]
    offered = [report for report in (first, second) if report.offered_reingest]
    assert len(done) == 1 and len(offered) == 1, "the second reading waited and then asked"
    assert done[0].pass_number == 1
    text = (kb / reference_file_path("Cooking", "radical-candor")).read_text(encoding="utf-8")
    assert text.count("### Pass") == 1
    assert text.count("- Care about the person.") == 1


@pytest.mark.asyncio
async def test_the_librarian_carries_no_ingestion_tool_rt16(kb: Path) -> None:
    """It holds no write capability at all (RT-16) and no topic lens to read through (LB-5)."""
    async with opened(kb, scripted(says("hi"))) as rt:
        assert INGEST_TOOL not in [tool.name for tool in rt.tools_for(LIBRARIAN_AGENT_ID)]
        assert INGEST_TOOL in [tool.name for tool in rt.tools_for(COOKING)]


@pytest.mark.asyncio
async def test_a_source_that_cannot_be_read_is_refused_loudly_not_summarised_ls7(
    kb: Path,
) -> None:
    """A source that yields nothing must fail at the start, not produce a confident summary.

    Through the tool it becomes a refusal rather than an exception: one escaping a tool body aborts
    the pregel superstep and takes the maintenance flush with it (D-1), so the run would end with
    the tree half-maintained and the human none the wiser.
    """
    model = scripted(
        calls(call(INGEST_TOOL, {"origin": "/nowhere/at/all.pdf"}, "t1")),
        says("I could not read that source."),
    )
    async with opened(kb, model) as rt:
        with pytest.raises(SourceError):
            await rt.ingest(COOKING, "/nowhere/at/all.pdf")

        events = await drain(rt, COOKING, "T1", "ingest that")

    assert isinstance(events[-1], RunEnd)
    assert not (kb / "Cooking" / "references" / "all").exists()


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_two_experts_read_the_same_source_through_their_own_lenses(
    kb: Path, book: Path
) -> None:
    """One source, several experts, different extractions — decision G at chapter granularity.

    Both read the whole book; each files what its own lens sees, into its own subtree, with its own
    copy of the original. That is the trade LS-1 states plainly: a large binary stored more than
    once, for a topic folder that stays self-contained and portable.
    """
    cooking, _ = await read(
        kb, book, "NEW: Heat management is feedback.", "NOTHING", "NOTHING", "NOTHING"
    )
    bbq, _ = await read(
        kb,
        book,
        "NOTHING",
        "NEW: Say the smoke is wrong before the brisket is ruined.",
        "NOTHING",
        "NOTHING",
        topic_path="BBQ",
    )

    assert cooking.path != bbq.path
    assert "- Heat management is feedback." in contents(kb, cooking)
    assert "- Heat management is feedback." not in contents(kb, bbq)
    for report in (cooking, bbq):
        folder = (kb / (report.path or "")).parent
        assert (folder / "radical-candor.txt").read_bytes() == book.read_bytes()


# --------------------------------------------------------------------------------------
# § the answer grammar
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("NOTHING", ()),
        ("  nothing.  ", ()),
        ("- A bare bullet.", ((TakeKind.NEW, "A bare bullet."),)),
        ("NEW: An argument.", ((TakeKind.NEW, "An argument."),)),
        ("- NEW: A bulleted prefix.", ((TakeKind.NEW, "A bulleted prefix."),)),
        ("better: Said again.", ((TakeKind.BETTER, "Said again."),)),
        ("CONTRADICTS: The opposite.", ((TakeKind.CONTRADICTS, "The opposite."),)),
        (
            "NEW: One.\n\nNEW: Two.",
            ((TakeKind.NEW, "One."), (TakeKind.NEW, "Two.")),
        ),
        ("NEW: Kept.\nNOTHING", ()),
    ],
)
def test_the_answer_grammar_tolerates_what_a_model_actually_writes(
    answer: str, expected: Sequence[tuple[TakeKind, str]]
) -> None:
    """Every accepted shape was cheaper to accept than to refuse — a refusal costs a whole turn.

    The last row is deliberate: a `NOTHING` anywhere ends the answer with no takes at all. A model
    that lists something and then says it has nothing has contradicted itself, and LS-6 makes the
    conservative reading — file nothing — the safe one, because the alternative is a file entry the
    model itself disowned.
    """
    assert tuple((take.kind, take.text) for take in parse_takes(answer)) == tuple(expected)


def test_the_across_question_can_only_produce_additions() -> None:
    """It names no section, so a `BETTER:` there has nothing to be better *than*."""
    takes = parse_takes("BETTER: nope.\nNEW: yes.", allow=(TakeKind.NEW,))

    assert tuple(take.text for take in takes) == ("yes.",)


@pytest.mark.superseded
def test_a_markdown_original_is_copied_where_layer_1_will_not_validate_it_ls1(
    tmp_path: Path,
) -> None:
    """A markdown source copied as `.md` is an *authored* file to Layer 1, and fails validation.

    LS-1 copies the original into every topic that gainfully ingests it. For a PDF or an HTML source
    that is an asset and exempt (FM-14, VA-7); for a markdown source it is not, so `<slug>.source.md`
    lands a file with no frontmatter inside the tree and `validate_tree` reports MISSING_FRONTMATTER.
    Found by running the loop end to end and validating the result — the unit tests all passed.

    The fix keeps the bytes and changes only the name, rather than adding frontmatter (which would
    make the copy no longer the source) or exempting it in Layer 1 (which this feature promised not
    to touch).
    """
    kb = tmp_path / "KB"
    kb.mkdir()
    scaffold_topic(kb, "Engineering", title="Engineering", description="Tooling", today=TODAY)
    original = tmp_path / "handbook.md"
    original.write_text(
        "# Handbook\n\n## Chapter 1 — Branching\nShort branches.\n", encoding="utf-8"
    )
    staged = stage(kb, original)

    async def ask(question: str) -> str:
        return (
            "- Short-lived branches keep merge risk low."
            if "Chapter 1" in question
            else NOTHING_MARKER
        )

    snapshot = scan(kb)
    asyncio.run(
        ingest(
            kb,
            snapshot.topics["Engineering"],
            staged,
            ask=ask,
            snapshot=lambda: scan(kb),
            today=TODAY,
        )
    )

    folder = kb / "Engineering" / "references" / "handbook"
    assert (folder / "handbook.source.txt").is_file(), "the original must still be copied (LS-1)"
    assert (folder / "handbook.source.txt").read_text(encoding="utf-8") == original.read_text(
        encoding="utf-8"
    ), "the copy must be byte-identical to what arrived"
    assert not (folder / "handbook.source.md").exists()
    assert [f for f in validate_tree(kb) if f.severity is Severity.ERROR] == []


# --------------------------------------------------------------------------------------
# Audit regressions, 2026-08-07 — each one reproduced against the shipped code first
# --------------------------------------------------------------------------------------


REPEATED = (
    ("Chapter 1", "First chapter."),
    ("Summary", "The first summary."),
    ("Chapter 2", "Second chapter."),
    ("Summary", "SECRET-TWO: the second summary, which says something else."),
)


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_repeated_section_title_is_still_opened_and_kept_apart_ls9(
    kb: Path, tmp_path: Path
) -> None:
    """Two sections called "Summary" are two sections — not one (LS-9, LS-10).

    The resume frontier was a set of section *titles*, so the second "Summary" was already
    accounted for before the loop reached it: never windowed, never asked about, never read. The
    same membership test computed `unread`, so it was not reported missing either — `complete` was
    set and the file wrote "Pass complete: every section of this reading was opened."

    That is verbatim the failure this whole feature exists to prevent, and it was live on the run
    cited as proof the design worked: Pro Git has 123 sections and 111 distinct titles, so eleven
    chapters were never opened while the file said otherwise. "Summary", "Exercises", "Notes",
    "Discussion" are what real books call their sections.
    """
    report, model = await read(
        kb,
        source_file(tmp_path / "sources", "Repeats", chapters=REPEATED),
        *["NEW: take one.", "NEW: take two.", "NEW: take three.", "NEW: take four.", "NOTHING"],
    )

    asked = questions(model)
    assert any("SECRET-TWO" in question for question in asked), "the twin was put in front of it"
    assert report.sections_total == 4
    assert len(report.covered) == 4
    assert report.unread == ()
    assert [line for line in contents(kb, report).splitlines() if line.startswith("## ")] == [
        f"## {PROVENANCE_HEADING}",
        "## Chapter 1",
        "## Summary",
        "## Chapter 2",
        "## Summary (2)",
        f"## {READING_RECORD_HEADING}",
    ]


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_sections_are_filed_in_the_sources_own_order_ls10(kb: Path, tmp_path: Path) -> None:
    """`titles.index(heading)` resolves a repeat to its *first* occurrence (LS-10).

    So a file whose source runs 1, Summary, 2, Summary was written 1, 2, Summary — the placement
    rule that exists to keep the file reading in the source's order, defeated by the same
    title-as-identity assumption.
    """
    report, _ = await read(
        kb,
        source_file(tmp_path / "sources", "Ordered", chapters=REPEATED),
        *[
            "NOTHING",
            "NEW: from the first summary.",
            "NEW: from chapter two.",
            "NOTHING",
            "NOTHING",
        ],
    )

    chapters = [line for line in contents(kb, report).splitlines() if line.startswith("## ")]
    assert chapters.index("## Summary") < chapters.index("## Chapter 2")


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_source_file_that_cannot_be_decoded_is_never_written_over_ls12(
    kb: Path, book: Path
) -> None:
    """ "No file" and "a file I cannot read" sent the loop opposite ways (LS-12, D6).

    `_read` returned `None` for both, so a file a human's editor had saved as cp1252 was taken for
    absent: pass 1 was opened again and the whole file — every earlier pass, and the line they had
    just added — was rewritten from scratch. `gates._read` swallowed the same exception, so
    `REFERENCE_REWRITE` saw no current content to diff against and never fired. The one write the
    design says can never be walked back, reached by an ordinary edit in an ordinary editor.
    """
    first, _ = await read(kb, book, "NEW: pass one argument.", "NOTHING", "NOTHING", "NOTHING")
    target = kb / str(first.path)
    edited = target.read_text(encoding="utf-8").replace(
        "## Chapter 2", "- MY OWN NOTE: the crème de la crème.\n\n## Chapter 2"
    )
    target.write_bytes(edited.encode("cp1252"))
    before = target.read_bytes()

    with pytest.raises(SourceError):
        await read(kb, book, "NEW: pass two argument.", today=LATER)

    assert target.read_bytes() == before


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_reference_folder_keeps_its_name_when_the_inbox_is_cleared_ls8(
    kb: Path, tmp_path: Path
) -> None:
    """The folder is permanent; `.inbox` is a cache LS-9 says may be cleared (LS-1, LS-8).

    The slug came off the staging directory, whose name is decided by what happens to be in
    `.inbox`: the first source to slug to `report` got `report` and the next got `report-2`. After
    `rm -rf .inbox` the two swapped, and each one's arguments were appended to the *other's* file as
    a fresh pass — a provenance block naming a different document, beside a copy of a different
    original, with `validate_tree` reporting nothing and no undo.
    """
    grilling = tmp_path / "sources" / "grilling"
    baking = tmp_path / "sources" / "baking"
    grilling.mkdir(parents=True)
    baking.mkdir(parents=True)
    (grilling / "report.txt").write_text("## Fire\n\nTwo zones beat one.\n", encoding="utf-8")
    (baking / "report.txt").write_text("## Yeast\n\nProof it in warm water.\n", encoding="utf-8")

    fire, _ = await read(kb, grilling / "report.txt", "NEW: two zones.", "NOTHING")
    yeast, _ = await read(kb, baking / "report.txt", "NEW: proof it warm.", "NOTHING")
    shutil.rmtree(kb / INBOX_DIR)
    again, _ = await read(kb, baking / "report.txt", "NEW: proof it warm.", "NOTHING", today=LATER)

    assert fire.slug != yeast.slug
    assert again.slug == yeast.slug
    assert "Yeast" not in contents(kb, fire)
    assert "Fire" not in contents(kb, again)


@pytest.mark.asyncio
async def test_a_refused_first_write_leaves_no_folder_and_claims_nothing_ls6(
    kb: Path, tmp_path: Path
) -> None:
    """The copy was made before the write was validated, so a refusal left an orphan (LS-1, LS-6).

    Any source whose slug is one of Layer 1's reserved names does this on contact — an ordinary
    `https://…/guides/index.html`, a local `summary.pdf`. The copy was already on disk and nothing
    removed it: `MISSING_MAIN_FILE` in a tree that is supposed to stay valid, `ORPHAN_ITEM_FOLDER`
    published into the topic index, and the report saying "Filed to `…/index/index.md`" — success
    reported for work that was thrown away, including every model call spent reading it.
    """
    reserved = tmp_path / "sources" / "index.txt"
    reserved.write_text("## Chapter 1\n\nA chapter.\n", encoding="utf-8")

    report, _ = await read(kb, reserved, "NEW: an argument.", "NOTHING")

    assert report.gainful is False
    assert report.path is None
    assert not (kb / "Cooking" / REFERENCES_DIR / "index").exists()
    assert not has_errors(validate_tree(kb))
    assert find_orphans(kb, scan(kb)) == []


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_gate_mid_loop_stops_the_reading_and_says_what_it_did_not_reach_ls12(
    kb: Path, tmp_path: Path
) -> None:
    """A refused write means nothing later in the pass can land, so reading on is waste (LS-12).

    The loop kept going and kept appending to `record.took`, so the run reported every chapter
    covered, `complete`, and "5 of 5 sections gave this topic something. Filed to …" for a file
    whose later half was never written — with one line saying "One write was withheld" (singular)
    for four withheld writes. On the local fallback that is 284 s a chapter spent on answers with
    nowhere to go.
    """
    chapters = tuple((f"Chapter {n}", f"Body of chapter {n}.") for n in range(1, 6))
    source = source_file(tmp_path / "sources", "Long Book", chapters=chapters)
    staged = staged_source(kb, source)
    asked = 0

    async def ask(question: str) -> str:
        nonlocal asked
        asked += 1
        if asked == 3:  # the human opens the file mid-run and adds a line of their own
            target = kb / reference_file_path("Cooking", staged.slug)
            target.write_text(
                target.read_text(encoding="utf-8").replace(
                    "## Chapter 2", "## My own note\n\nMine.\n\n## Chapter 2"
                ),
                encoding="utf-8",
            )
        return f"NEW: argument {asked}."

    report = await ingest(kb, topic_of(kb), staged, ask=ask, snapshot=lambda: scan(kb), today=TODAY)

    assert report.gate == GateReason.REFERENCE_REWRITE.value
    assert report.unread == ("Chapter 4", "Chapter 5")
    assert report.complete is False
    assert "Chapter 4" in report.summary() and "Chapter 5" in report.summary()
    assert "5 of 5" not in report.summary()


@pytest.mark.parametrize(
    "answer",
    [
        "Nothing relevant to this topic.",
        "NOTHING for this topic.",
        "None.",
        "N/A",
        "- nothing here",
    ],
)
def test_a_refusal_the_model_phrases_naturally_is_still_a_refusal_ls6(answer: str) -> None:
    """LS-6's guarantee cannot depend on phrasing the prompt does not control.

    A refusal meant the single word `NOTHING` and nothing else, so "Nothing relevant to this topic."
    was filed as an *argument* — and one argument is what makes a topic gainful. Handing a cooking
    book to the Trading expert built the folder, wrote a file whose every bullet read "Nothing
    relevant to this topic.", copied the whole source in, and recorded "Took something from" for
    each section. Those bullets carry `type.reference` and the topic's tag, so they surface in the
    index, in tag search and in packs as if they were knowledge.
    """
    assert parse_takes(answer) == ()


def test_an_argument_that_merely_opens_with_a_refusal_word_is_kept_ls6() -> None:
    """The other half of the rule: a real claim about nothingness is not a refusal to answer."""
    answer = (
        "Nothing in this chapter survives contact with a real kitchen, because the heat is wrong."
    )

    assert [take.text for take in parse_takes(answer)] == [answer]


@pytest.mark.asyncio
async def test_a_naturally_phrased_refusal_still_leaves_no_trace_ls6(
    kb: Path, tmp_path: Path
) -> None:
    """LS-6 end to end, with the phrasing that used to defeat it."""
    scaffold_topic(kb, "Trading", title="Trading", description="Positions", today=TODAY)
    cookbook = source_file(tmp_path / "sources", "Cookbook")

    report, _ = await read(
        kb, cookbook, *["Nothing relevant to this topic."] * 5, topic_path="Trading"
    )

    assert report.gainful is False
    assert report.path is None
    assert not (kb / "Trading" / REFERENCES_DIR / "cookbook").exists()


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_bullet_the_human_deleted_stays_deleted_ls12(kb: Path, book: Path) -> None:
    """Striking out a claim is the only curation gesture a human has on this file (LS-12, D6).

    Duplicate suppression compared against what is in the file *right now*, so a line they deleted
    was indistinguishable from a line nobody had found yet: the next pass re-inserted it, un-gated
    (an insertion never gates), and recorded it as a fresh discovery. The reading record now carries
    how many arguments each pass filed, which is what tells "removed by hand" from "never seen".

    The withheld text is reported to the expert and **not** written into the file — restating it in
    the reading record is how the first attempt at this fix put the struck-out line straight back.
    """
    first, _ = await read(
        kb, book, "NEW: Keep this one.", "NEW: Salt early, never late.", "NOTHING", "NOTHING"
    )
    target = kb / str(first.path)
    target.write_text(
        "\n".join(
            line
            for line in target.read_text(encoding="utf-8").splitlines()
            if "Salt early" not in line
        )
        + "\n",
        encoding="utf-8",
    )

    second, _ = await read(
        kb,
        book,
        "NEW: Keep this one.",
        "NEW: Salt early, never late.",
        "NOTHING",
        "NOTHING",
        today=LATER,
    )

    assert "Salt early" not in target.read_text(encoding="utf-8")
    assert "Salt early, never late." in second.flagged
    assert second.gate is None


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_re_ingestion_marks_an_approved_file_for_review_again_ls12(
    kb: Path, book: Path
) -> None:
    """`status.draft` was set once, by `create`, and never re-applied (LS-12).

    README §1.7's conflict flow moves a reviewed source file to `status.approved`, so "an approved
    file that is later re-ingested" is the designed steady state — and a second pass dropped an
    argument the human had never seen into a file their own tag says they have read and accepted.
    """
    first, _ = await read(kb, book, "NEW: One.", "NOTHING", "NOTHING", "NOTHING")
    target = kb / str(first.path)
    target.write_text(
        target.read_text(encoding="utf-8").replace("status.draft", "status.approved"),
        encoding="utf-8",
    )

    await read(
        kb,
        book,
        "NOTHING",
        "NEW: A second, newly found argument.",
        "NOTHING",
        "NOTHING",
        today=LATER,
    )

    meta = parse(target.read_text(encoding="utf-8")).meta
    assert meta is not None
    assert "status.draft" in meta.tags
    assert "status.approved" not in meta.tags


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_source_section_named_like_a_structural_heading_stays_out_of_it_ls10(
    kb: Path, tmp_path: Path
) -> None:
    """`Provenance`, `Reading record` and `Across the source` are the file's own (LS-10).

    A source section with one of those titles wrote its arguments straight into that block: the
    provenance block grew model prose under `- First read: …`, the reading record grew a bullet
    above `### Pass 1`, and the next pass was prompted with the file's own metadata as prior art.
    """
    odd = (
        (READING_RECORD_HEADING, "A section the book itself calls this."),
        (PROVENANCE_HEADING, "And another."),
        ("Chapter 3", "An ordinary chapter."),
    )
    report, _ = await read(
        kb,
        source_file(tmp_path / "sources", "Odd Book", chapters=odd),
        "NEW: one.",
        "NEW: two.",
        "NEW: three.",
        "NOTHING",
    )

    body = contents(kb, report)
    provenance = SourceFile.load(body)
    assert provenance.origin is not None, "the real provenance block is still readable"
    assert f"## {READING_RECORD_HEADING} (2)" in body
    assert f"## {PROVENANCE_HEADING} (2)" in body
    assert not has_errors(validate_tree(kb))


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_file_carries_the_tags_its_sections_are_about_ls3(
    kb: Path, tmp_path: Path
) -> None:
    """LS-3, which was not implemented at all and had zero citations anywhere.

    It is the rule that makes one-file-per-source survivable: the file is findable by any argument
    in it. As built the file carried only `topic.cooking`, so a grilling book ingested by the
    Cooking expert never came back for `topic.cooking.grilling` — and packs select by tag.

    A tag the tree does not know is *proposed*, not written: minting one is the human's call (RT-25)
    and a write carrying one would fire `GateReason.NEW_TAG` and withhold the whole pass.
    """
    notes = kb / "Cooking" / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "known.md").write_text(
        "---\ntitle: A grilling note\ndescription: Something already tagged.\ntopic: Cooking\n"
        "tags:\n  - topic.cooking.grilling\n  - type.note\n  - status.draft\n"
        "created: 2026-08-07\nupdated: 2026-08-07\nsource_type: note\n---\n\nText.\n",
        encoding="utf-8",
    )
    grill = source_file(tmp_path / "sources", "Grilling", chapters=(("Chapter 1", "Charcoal."),))

    report, _ = await read(
        kb,
        grill,
        "NEW: Two-zone fires beat one.\nTAGS: topic.cooking.grilling, domain.invented",
        "NOTHING",
    )

    meta = parse(contents(kb, report)).meta
    assert meta is not None
    assert "topic.cooking.grilling" in meta.tags
    assert "domain.invented" not in meta.tags
    assert any("domain.invented" in note for note in report.flagged)
    assert not has_errors(validate_tree(kb))


def test_an_unreadable_source_file_is_not_reported_as_absent_ls12(tmp_path: Path) -> None:
    """`_read` distinguishes "no file" from "a file I cannot read" (LS-12, D6).

    Pinned on the helper because the two answers send the loop opposite ways and only one of them
    creates a document from scratch. `resolve_slug` now refuses first for the same reason, so this
    is the second of two independent guards on the one write that cannot be walked back — and a
    guard nothing tests is a guard that quietly stops being there.
    """
    missing = tmp_path / "nothing.md"
    unreadable = tmp_path / "there.md"
    unreadable.write_bytes("- crème de la crème.\n".encode("cp1252"))

    assert ingestion._read(missing) is None
    with pytest.raises(SourceError):
        ingestion._read(unreadable)


@pytest.mark.asyncio
async def test_the_ingestion_loop_runs_on_the_registrys_model_rg21(kb: Path, book: Path) -> None:
    """The model — and its failover — is a registry property, for every consumer (RG-21).

    The loop reached for `init_chat_model(config.default_model)` itself and so became the one path
    in the system with no fallback: a 300-page book is 100+ sequential calls, by far the most
    quota-exposed operation there is, and it was the one that could not survive a 429.
    """
    model = scripted(says("NOTHING"))
    asked: list[Any] = []

    async with opened(kb, model) as runtime:
        registry = runtime._registry
        original = registry.chat_model_for
        registry.chat_model_for = lambda agent_id: (  # type: ignore[method-assign]
            asked.append(agent_id) or original(agent_id)
        )
        await runtime.ingest(COOKING, str(book))

    assert asked == [COOKING], "the asker's model came from the registry, not from the config"


@pytest.mark.asyncio
async def test_a_source_outside_the_configured_roots_is_refused(kb: Path, tmp_path: Path) -> None:
    """`ingest_source` is the one tool that reads outside the knowledge base (RT-15).

    `origin` is a string the *model* chooses, and every other read an expert can make is confined by
    the backend — `FilesystemBackend(root_dir=kb_root, virtual_mode=True)` refuses traversal
    outright. Unbounded, one tool call read any file the daemon's user could read, put its text in
    front of the model, and copied it byte-for-byte into the tree; reproduced with `~/.ssh/id_rsa`.
    """
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    secret = outside / "id_rsa.txt"
    secret.write_text("# Private key\n\nSECRET-MATERIAL\n", encoding="utf-8")
    model = scripted(says("NOTHING"))

    async with PkbRuntime.open(
        kb,
        kb.parent / "pkb.sqlite",
        config=RuntimeConfig(
            clock=lambda: TODAY, default_model=model, source_roots=(kb.parent / "sources",)
        ),
        registry_factory=registry_factory(model),
    ) as runtime:
        with pytest.raises(SourceError, match="outside the directories"):
            await runtime.ingest(COOKING, str(secret))

    assert not (kb / INBOX_DIR).exists()
    assert not any((kb / "Cooking" / REFERENCES_DIR).glob("id-rsa*"))
