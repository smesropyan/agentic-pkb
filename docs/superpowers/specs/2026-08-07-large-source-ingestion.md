# Large-source ingestion — books, papers, and anything that does not fit a turn

**Date**: 2026-08-07
**Status**: Designed, not built. Scheduled after build-order step 3.
**Scope**: crosses all three layers — **no change in `pkb.core`** (the existing
`references/<source>/<source>.md` shape carries it, with no new file role), new skills and a chunked
workflow in `pkb.agents`, and pack granularity in `pkb.packs`.

---

## The problem

The tree treats every source identically: one `references/<source>/<source>.md`, however large the
source is. That is right for a link and wrong for a book.

The failure it produces is not a poor summary — it is a **silent one**. A 400-page book does not fit
a context window, so a single-turn ingestion reads what fits, writes a confident summary of the part
it saw, and nothing anywhere records that the rest was never opened. The file looks finished. This is
the same failure class as a Librarian claiming an expert had checked when none ran: the system
reporting success for work it did not do.

Two consequences follow, and they are the whole design:

1. **A source needs parts**, because the unit a human or an agent actually reuses is one argument,
   not one book.
2. **Ingesting a large source is a harness-driven loop, not an agent turn.** If the model can decide
   it is finished, it will, and it will say so convincingly. Same lesson as routing (LB-*).

---

## Structure

```
references/radical-candor/
├── radical-candor.md        # ONE file: thesis, provenance, one section per argument, what was skipped
└── radical-candor.pdf
```

**One physical file per source, with the arguments as sections inside it** — not one file per
argument. This is the existing Layer 1 shape (`references/<source>/<source>.md`), unchanged, which is
the first thing to like about it: no new file role, no index-rendering change, no new validation
rules, nothing for Layer 1 to learn.

The file carries the thesis, the provenance, one section per argument the source actually makes, and
an honest record of what was not extracted. **Arguments are argument-scoped, not chapter-scoped**
(LS-2): a chapter carrying three arguments becomes three sections, one carrying none becomes zero.

### Why this beats a file per argument

A file per argument sounds better and is worse here, because it makes *identity* the hard problem.
Argument boundaries are a judgement, so a second reading re-phrases a claim, the filename derives
from the claim, and the old file is orphaned rather than updated. Layer 1 never deletes, so the tree
slowly fills with near-identical parts under slightly different names — and nobody notices, because
each one looks fine on its own. Reconciliation would have to match arguments across passes by
meaning, with no stable key to match on.

With one file, there is nothing to match. Two versions of one document are compared, and identity is
a non-question.

### What this trades away, stated plainly

**Retrieval granularity.** An argument cannot carry its own tags, so the file carries the union of
its arguments' tags and a search for `topic.cooking.grilling` returns the whole book because one
section mentioned grilling. A pack asking "what do I know about feedback" gets the entire source file
rather than the three relevant sections.

That cost is accepted for the first draft, on the judgement that a well-structured document is more
usable to a reasoning agent than twenty fragments — context survives — and that this is reversible:
promoting a section to its own file later is additive, while un-splitting a tree full of orphaned
parts is not. If retrieval proves too coarse in use, the fix is pack-side slicing of the source file
before it is per-argument storage.

## Rulings of 2026-08-07

| # | Ruling | Consequence |
|---|--------|-------------|
| **LS-1** | **The source file is copied into each topic that gainfully ingests it.** | The copy follows the *extraction*, not the routing: an expert that reads the source and files nothing does not get a copy. A topic folder stays self-contained and portable, at the cost of storing a large binary more than once — the deliberate trade. The copy is **not** a `write_file`; the mechanism and the two Layer 2 amendments it needs are below. |
| **LS-2** | **Parts are argument-scoped, not chapter-scoped.** | Segmentation is a judgement the extraction skill makes, not a mechanical split. A chapter carrying three arguments becomes three parts; a chapter carrying none becomes zero. |
| **LS-3** | **The source file carries the tags relevant to the arguments it holds** — the union of what its sections are about. | With one file per source there is one place for tags, so the file is findable by any argument in it. The cost is coarseness, stated above and accepted. |
| **LS-4** | **Every expert ingests every part, and may ignore the ones its topic does not care about.** | *Not* a split of the parts between experts. Both the Management and the Parenting expert read the whole of a management book; each files what its lens sees, and the same argument may be extracted twice with different framings. That is the multi-expert ingestion ruling (README §2.2, Layer 2 LB-*) applied at part granularity, not a new rule. |
| **LS-5** | **A source may be re-ingested as often as it is worth re-ingesting**, and each pass **reconciles** with what is already there rather than replacing it. | Topics change and experts get better; the second reading of a book is expected to be a better one. Reconciliation is specified below — it is the part of this design with the most ways to go quietly wrong. |
| **LS-6** | **"Gainfully ingested" means at least one insight was derived.** | Hand a cooking book to the Trading expert and it yields nothing: no reference folder, no stub file, no copy of the source. Zero insights leaves **no trace at all** in that topic, rather than an empty folder implying the source was considered and is somehow relevant. |

### How the copy of LS-1 is actually made

A binary copy cannot go through `write_file` — it takes text, and MW-7 intercepts exactly
`write_file` and `edit_file` and nothing else. Nor can it be a stray `shutil.copy`: RT-18 asserts by
AST/grep that **no other `pkb.agents` code writes under `kb_root`**, permitting only
`maintenance.py`'s `flush` call and `tools/topics.py`'s scaffold call. So the copy needs a named
mechanism, and naming it is the difference between an amendment and a rule quietly broken.

**The chunked ingestion workflow makes the copy, not the model.** It is the same harness-driven loop
that stages the source in `.inbox` (LS-8) and reads it through the windowed reader (LS-9), and the
copy is a deterministic consequence of LS-6 — this expert filed at least one insight — not a
judgement, so there is nothing to gain by routing it through a tool call the model must remember to
make. Concretely: one function in `pkb.agents` beside `tools/topics.py`, copying **the original only**
(the extracted text stays in `.inbox` as the cache LS-9 describes) into `references/<source>/` of the
topic that just filed, under the process-wide KB write lock (RT-51) exactly as the scaffold does.

Two Layer 2 amendments follow, and both are load-bearing:

1. **RT-18 gains a third sanctioned writer.** Its test enumerates the permitted write sites, so the
   ingestion copy must be named there or the assertion fails. The rationale is unchanged — the deny
   list constrains the *agent*, and this is harness code, not a model-issued write.
2. **The copied path must be recorded as touched.** MW-17 and MW-19 record touched paths only from
   the results of the tools MW-7 intercepts, so a copy made any other way is invisible to MW-20's
   single per-run flush: the file lands on disk, no `updated` stamp is bumped, and the topic index
   never lists it. The copy therefore writes its destination into `kb_touched` itself, exactly as a
   successful tool call would.

The alternative — a copy helper in Layer 1 — was rejected: it buys nothing (the write lock and the
flush are already reachable from Layer 2) and it costs the "Layer 1 changes nothing" property that is
this design's strongest argument.

---

## Extraction shapes, per kind of source

Different sources have different skeletons, and the skeleton is what makes an extraction useful
rather than a paraphrase. These become skills, so a human can rewrite them and a topic can overload
them — the Cooking expert wanting doneness tables in a recipe extraction is exactly that mechanism.

| Kind | Shape |
|------|-------|
| **paper** | question · method · results · limitations · *does this apply to me* |
| **book** | thesis, then one part per argument the book actually makes |
| **article, post, clip** | the single claim and the evidence offered for it |
| **manual, reference work** | the parts the topic will actually consult; a manual is looked things up in, not read |

The last column of the paper shape is the one that earns its place: a result is only knowledge here
once someone has said whether it applies to this human's circumstances.

---

## Two boundaries that must hold

**An extracted argument is a reference, never a note.** README §1.3 makes notes human-authored — the
human's own experience. An argument the AI lifted out of a book is source-derived, so it is
`type.reference` wherever it lands. When the human *adopts* one into practice, they write a note and
it becomes theirs. Blur this and the tree fills with AI-written "experience", and "human content
wins" (§1.7) stops meaning anything, because there is no longer a human side to win.

**Parts do not go into `references/summary.md`.** §1.6 makes the breadth files *"a compact approval
surface"* that manage the **human's** context window. Twenty arguments from one book would blow it,
and a summary nobody reads is an approval surface that has stopped working. The summary stays a
distillation of what the topic knows and points at the parts.

---

## What each layer has to change

**Layer 1 (`pkb.core`)** — **no rule changes.** One file per source is the shape Layer 1 already
implements and validates; checked rule by rule against `FM-*`, `VA-*`, TG-3, GE-8/14/27 and
MA-3/4/5/12, none of them move. That is the strongest argument for this design: the layer with no
undo and 603 tests behind it does not have to change at all.

One **consequence** follows anyway, from LS-1 rather than from a rule change, and an implementer
meets it on the first ingestion: the copied original has to be **linked from the extraction file**.
MA-8's `ORPHAN_ASSET` covers "a file under `media/` *or inside a reference folder* that the sibling
main `.md` never references" — built, `src/pkb/core/analysis.py:352-370` — so an unlinked
`radical-candor.pdf` is flagged, once in every topic that took a copy, in that topic index's
`## Maintenance flags` section (MA-10). The extraction file's provenance block is where the link goes.

**Layer 2 (`pkb.agents`)** — the `ingest-paper` and `ingest-book` skills, which take the shipped set
from eight to **ten**. SK-1 pins the set at exactly eight with a test over `DEFAULT_SKILL_NAMES`, so
**SK-1 and its test must move with them**; the `ingestion-classification` skill (Layer 2 §6.4) also gains the branch
that hands a source too large for one turn to the chunked workflow, or the new skills ship where no
expert's prompt ever reaches them.

Then the chunked ingestion workflow, which is the hard part. It must: segment the source, extract
argument by argument with a bounded window (LS-9), write each section as it goes rather than at the
end, record what it skipped and why, and be **resumable** — a 300-page book will not finish in one
turn, and a run that dies at part 14 must not start over.

Resumability is the part Layer 2 cannot express today. A multi-turn expert branch breaks three rules
that all assume a branch begins and ends inside one fan-out, and each has to be amended deliberately
rather than discovered during the build:

- **LB-17** gives a branch exactly four statuses — `answered`, `failed`, `awaiting-approval`, `busy`
  — with no way to report *"still ingesting, part 14 of 20"*. Either the taxonomy grows a status or
  the ingestion has to leave the fan-out before it is finished.
- **LB-15** emits exactly one `SubagentStart`/`SubagentEnd` pair per expert, so the bracket a client
  draws progress inside closes when the branch returns — for a resumable loop, long before the work
  is done.
- **RT-45**'s active-run registry and **RT-39**'s refusal to start a turn while an interrupt is
  pending both key on the derived thread, so a long-running ingestion **holds that expert's slot**: a
  second item routed to the same expert comes back `busy` rather than queued. That is correct
  behaviour and the human sees it, so it belongs in the design rather than in a bug report.

**Which gates fire, precisely** — three statements in this document have to agree. On a **first**
pass the source-file write itself is **un-gated** (the gate amendment below), and reconciliation ends
in **one proposal, not twenty**. So the only gates that fire per part are the incidental ones a part
happens to trigger: RT-25 (a `topic.*`/`domain.*` tag not yet in the tree) and RT-28 (the first file
in a new extension folder). On a **re-ingestion** there is additionally the single gate on the rewrite
itself, at the end, on the one proposal. That is what makes the "several gates in one turn" behaviour
from routing — an expert parks on its own derived thread — directly relevant.

**Layer 3 (`pkb.packs`)** — **nothing, for the first draft.** Packs select **whole source files**:
a pack entry is a file, PK-9/PK-10 pin golden ordered path lists, and PK-11 truncates deterministically
**at an entry boundary, never mid-file**. Section-level selection would make the extraction file's
internal headings a wire contract and PK-11 unimplementable. So an implementation pack asking "what do
I know about feedback" gets the whole of each source that argues about it — the coarseness stated and
accepted above — and **pack-side slicing is the deferred fix** if that proves too coarse in use, ahead
of per-argument storage.

---

---

## How a source arrives (ruled 2026-08-07)

The design above assumed the source material was somehow in front of the agent. It was not: an
agent's `read_file` returns a whole file through the backend, no text extraction exists, and a
400-page book cannot be pasted into a turn. Two rulings close that.

**LS-7 — a path in, extraction only if needed.** The human names a file. If it is already text or
markdown, it is read as it is. If it is a PDF, an EPUB or anything else binary, it is extracted to
text first and **both are kept** — the extraction is what the loop reads, the original is what the
topic gets a copy of. Extraction quality is visible rather than assumed: a scanned PDF that yields
mush must fail loudly at the start, not produce a confident summary of nothing.

**LS-8 — staging is `<kb>/.inbox/`, inside the tree and invisible to it.** A source has to exist
somewhere before any topic has earned a copy (LS-1 gives the copy only on *gainful* ingestion, and
gainfulness is not known until the reading is done). `.inbox` is dot-prefixed, so Layer 1's PA-16
already skips it — **verified**: the walk records no file from it, validation reports nothing about
it, and neither the root index nor the tag registry mentions it. No permission change, no second
mount, and no change to Layer 1.

An expert cannot write there — topic-scoped permissions (RT-15) confine it to its own subtree — which
is correct: the tool stages the file, the agent only reads it.

**LS-9 — the loop needs a window, and `read_file` is not one.** "Extract argument by argument with a
bounded window" is unbuildable with a tool that returns whole files. Ingestion needs a reader that
answers *"the next N characters of this source, from offset K"*, so the loop can walk a document
larger than any context. Without it the model reads what fits and reports success — the exact failure
this design exists to prevent.

The extracted text stays in `.inbox` as a cache: a re-ingestion (LS-5) reads it again without
re-extracting, and if the cache is cleared, the original copied into the topic can be extracted
afresh.

## Reconciliation — what a second pass does (LS-5)

Re-ingestion is a first-class flow. A second pass produces a fresh extraction, and the two documents
are compared: **what is new, what is better said, what now disagrees.**

| The comparison finds | What happens |
|---|---|
| **An argument the old file does not have** | Added as a new section. |
| **A better statement of an argument already there** | An **edit of existing content**, proposed rather than applied — the human may have read and relied on the old wording. |
| **An argument that contradicts one already there** | A **conflict**: flag the source file with `status.conflict-review` and a one-line `review_note`, change nothing, let the human settle it. This is a deliberate **extension** of §1.7's machinery, not a reuse of it — see the note below the table. |
| **Nothing where the old file has an argument** | Keep it. Layer 1 never deletes, and an argument this pass missed is not thereby wrong. The map records that this pass did not cover it. |

**The conflict flag extends §1.7; it does not reuse it.** §1.7 and the shipped `conflict-detection`
skill both scope the three tagging acts to **the human content file** — *"the reference is neither
tagged nor edited. It is not wrong; it merely disagrees with someone who was there."* Under
re-ingestion neither side is human: it is one reading of a source against another, "human content
wins" decides nothing, and the file to flag is a **reference**. That requires amending Layer 2's
`conflict-detection` skill (§6.2) with a reference-vs-reference branch, and it cuts against RT-27's
exemption of reference depth files from the `status.approved` gate (SK-13 / Layer 2's Q4), which rests
on the human curating references at the summary level. Until that amendment lands, an implementer
building conflict detection from §1.7 will not build this case at all. Flagging some human file that
happens to cite the source instead is worse: it asks the human to review content they neither wrote
nor changed.

**Compare per argument, not per document.** The storage is one file; the *comparison* walks section
by section. Handing an LLM two long documents and asking "is anything new?" reproduces the exact
failure this design exists to prevent — a bounded reader, an unbounded input, and a confident answer
about the part it managed to read. Section-wise comparison keeps each judgement small enough to be
trustworthy, and makes "it found nothing new in section 9" a statement someone can check.

**One proposal, not twenty.** The pass ends with a single proposed version of the file and a summary
of what changed, so the human makes one decision about one document instead of twenty about
fragments. That is the approval surface §1.6 asks for.

**The map is a record of the readings, not of the source.** Each pass appends what it covered, what
it skipped and why, and when. A book read three times carries three readings' worth of provenance,
which is what makes "the expert got smarter" checkable rather than asserted.

**Amendment this forces to the gate table.** RT-31 puts no gate on reference depth files, which was
right when a reference was written once and never touched again. Under re-ingestion an un-gated write
overwrites an extraction the human has already read. So: **the first write of a source file stays
un-gated; a re-ingestion that rewrites one is gated.** Without that split, "human content wins" holds
for notes and quietly fails for everything derived from a source — which is most of what a knowledge
base accumulates.

## Open items

- **Section identity within the file.** Matching is a smaller problem than it was, but not zero: the
  comparison still has to line up "the same argument, said differently" across two versions. It is
  now bounded by one document rather than a directory, and a stable section anchor would make it
  cheaper still.
- **Progress visibility.** A twenty-argument ingestion is minutes of work; the human should see it
  progress and be able to stop it. Layer 3/4, once the loop exists.
- **Very large sources.** Nothing bounds the size. A 900-page reference work may want the "consult,
  do not read" treatment rather than full extraction — and a source file with 200 sections is its own
  kind of unusable.
- **When granularity stops being enough.** If packs routinely pull whole books for one argument,
  revisit — pack-side slicing first, per-argument files only if that fails.
