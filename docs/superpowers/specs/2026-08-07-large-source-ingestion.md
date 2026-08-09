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

---

## Rulings of 2026-08-07 (second round)

**LS-10 — the file is organised by the source's own structure.** Ideas stay argument-scoped (LS-2),
but they are **grouped under the chapter or section that introduced them**, with a final section for
the ideas that span the whole source:

```markdown
## Chapter 2 — Challenge directly
- <argument>
- <argument>

## Chapter 3 — …

## Across the book
- <the ideas no single chapter owns>
```

This replaces the stable-id proposal, and it is better. A chapter is **intrinsic to the source**:
chapter 3 is chapter 3 on every re-reading, so reconciliation gets a stable key for free without
putting machine ids in a file the human reads. A paper generalises the same way — its own sections
(method, results) are the anchor; an article has one claim and needs no grouping.

**LS-11 — ingestion is explicit, and a repeat is offered rather than assumed.** The human says
"ingest this". If the source is already in the tree, the agent **says so and asks** whether to
re-ingest, rather than silently re-reading a book (expensive) or silently skipping it (surprising).
Whether the loop windows the source is not a user-facing choice — it depends on length, and the
system can see the length.

**LS-12 — ingest first, mark for review; but only what is non-destructive.** For the
AI-generated / human-curated classes, content lands **immediately with a review marker** rather than
blocking on an interrupt: capture stays frictionless and the human reviews a queue instead of
answering a modal mid-turn.

The line that keeps this safe is destructiveness, and it falls exactly where the reconciliation table
already put it:

| | Lands immediately, marked for review | Flagged, not applied |
|---|---|---|
| **New file** | ✅ nothing is lost | — |
| **New argument or chapter section in an existing file** | ✅ pure addition | — |
| **A reworded argument** | — | ⚠️ replaces text the human may have approved |
| **A contradiction** | — | ⚠️ `status.conflict-review`, §1.7 unchanged |

There is no undo (arch D6), so a write that *replaces* human-approved text is the one thing that
cannot be walked back after the fact — which is why it is the one thing still held. Everything
additive lands.

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

## As built (2026-08-07)

Built as `src/pkb/sources.py` (a leaf module: extraction and staging, no harness import) and
`src/pkb/agents/ingestion.py` (the loop). 1302 tests pass; ruff, mypy-strict and the three import
contracts are green. Everything above holds as designed except where noted here.

An adversarial audit on 2026-08-07 — eight lenses, each required to reproduce what it claimed, then
three skeptics per finding told to refute it — confirmed **19 defects, three of them critical**, plus
four more from a completeness sweep. All are fixed, and each fix carries a test that fails without
it (verified by mutation: 22 mutations of the fixed code, 22 killed). The three critical ones are
recorded below in full, because each was a *silent* failure of the exact property this design exists
to provide.

**The staging layout.** `<kb>/.inbox/<slug>/` holds exactly four files:

| File | What it is |
|---|---|
| `source.json` | the manifest — origin, slug, kind, schema version, and the three names below |
| `<slug><ext>` | **the original, byte for byte**, under the extension it arrived with |
| `<slug>.extracted.json` | the structured extraction: the section tree the loop walks |
| `<slug>.extracted.md` | the same extraction rendered, so `.inbox` is readable without a JSON parser |

Both *derived* files carry `.extracted.`, and the original keeps its own name. That asymmetry is the
fix for a defect the first implementation shipped: the rendered text was written to `<slug>.md`,
which is also what a markdown source is called once staged, so **staging a `.md` file overwrote the
preserved original with its own re-rendering** while the manifest went on calling it the original.
Every LS-1 copy into a topic then carried the extraction while claiming to carry the source. The
whole suite was green — nothing staged a markdown source, and for a PDF the two names differ so the
collision could not occur. Naming the derived files defensively closes it for every suffix at once.
The manifest carries a schema version and a directory at another version is re-staged rather than
trusted, so no `.inbox` written by the broken layout can serve a bad original.

**LS-1's copy is renamed for exactly one kind of source.** A copied original normally keeps its
extension: `references/progit/progit.pdf` beside `progit.md`. A **markdown** original cannot, because
a `.md` file inside the tree is an *authored* file to Layer 1 — it requires the seven frontmatter
fields, and a raw source has none, so `validate_tree` reports `MISSING_FRONTMATTER` in the topic that
did the ingesting. PDF, EPUB and HTML copies are assets and exempt (FM-14, VA-7); markdown is not. So
a markdown original is copied as `<slug>.source.txt`: the bytes are unchanged, only the name is, and
`.txt` is in the class Layer 1 already leaves alone. The two alternatives were both worse — adding
frontmatter would make the copy no longer the source, and exempting `.md` inside reference folders
would change the layer this feature promised not to touch.

Both defects were found by **running the loop end to end and validating the result**, not by the unit
tests, which is the same lesson the earlier layers recorded: a suite that exercises the pieces can be
completely green while the composition writes the wrong bytes.

**Verified on a real book, after the audit.** Pro Git, 501 pages, 18.8 MB: `pdf-outline` recovers
**123 sections in 2.4 s**, re-staging is a cache hit at ~0 s, two topics each ingest it and get their
own copy, and `validate_tree` and `find_orphans` both report nothing afterwards. The expert is asked
**161 questions** and every one of the 123 sections is named in the reading record —
`covered ∪ nothing ∪ held ∪ unread` covers all 123 *headings*, which is the property the whole design
exists to produce. Before the audit the same run asked 113 questions and reported the same success.

### The three critical defects, and what they change

**A repeated section title meant the section was never opened — and the file said otherwise.** The
resume frontier was a set of section *titles*, so the second section called "Summary" was already
accounted for before the loop reached it: never windowed, never asked about, never read. `unread` was
computed by the same membership test, so it was not reported missing either — `complete` was set and
the file wrote *"Pass complete: every section of this reading was opened."* The skip was
deterministic, so re-ingesting skipped it again.

This was live on the run this document cited as proof the design worked. **Pro Git has 123 sections
and 111 distinct titles, so eleven chapters were never opened**, and the acceptance criterion stated
here — "covered ∪ nothing ∪ held ∪ unread names all 123" — was met only because it counted titles.
"Summary", "Exercises", "Notes", "Discussion" are what real books call their sections.

So **a section's identity is its heading in the file, and two sections may not share one**: a repeat
is numbered (`Summary`, then `Summary (2)`), and the file's own structural headings are reserved the
same way, which also stops a source section called "Provenance" from writing its arguments into the
provenance block. Still no machine ids in a file a human reads (LS-10), and still positional, so the
same source extracts to the same headings every time and a second pass reconciles chapter against
chapter. Re-verified on Pro Git: **123 distinct headings, 161 model questions where there were 113,
and all 123 sections named in the reading record.**

**An unreadable source file was read as "no file", so the whole file was overwritten.** `_read`
returned `None` both for "not there" and for "could not be decoded", and those send the loop opposite
ways — the second opens pass 1 again and writes a new document over the old one. The gate could not
stop it, because `gates._read` swallowed the same exception and saw no current content to diff
against, so `REFERENCE_REWRITE` never fired. A human editing their own reference file in an editor
whose default encoding is not UTF-8 lost that edit and every argument of every previous pass, with a
report that read like a normal first pass. This is the one write the design says can never be walked
back (D6). Both readers now distinguish three states; the gate's third state is a sentinel no
proposed content can extend, so every content rule fires and the write stops for a human.

**A reference folder's name came from a cache the spec says may be deleted.** The slug came off the
staging directory, whose name is decided by what happens to be in `.inbox` — the first source to slug
to `report` gets `report`, the next gets `report-2`. LS-9 declares `.inbox` clearable, so after
`rm -rf .inbox` two sources swapped names and each one's arguments were appended to the *other's*
file as a fresh pass: a provenance block naming a different document, beside a copy of a different
original, `validate_tree` reporting nothing, and no undo. **Identity now resolves against the tree**
— a folder whose source file records this origin wins, whatever it is called — so it survives a cache
clear, a re-stage, and a different spelling of the path. A folder holding a file with a different
origin is never joined, which also covers a hand-filed reference that happens to share a slug.

### The rest, by what they were

*Silent wrongness in what reached the model.* PDF outline anchors were placed by unanchored substring
search, so a short title ("5", "VI") matched inside the previous chapter's prose and one chapter's
body was filed under the next — on the `pdf-outline` path, the one this document reports as verified.
A UTF-16 text source was decoded by cp1252 (which almost never fails) into mojibake and passed the
"fails loudly" guard, NUL bytes and all. An EPUB chapter's own `encoding=` declaration was discarded,
so every non-English book arrived as `Kierkegaardï¿½s rï¿½sumï¿½`. A UTF-8 BOM stopped the first
heading matching `^#`, so the opening chapter became `(front matter)` and the title became the
filename. A multi-line section title — an ordinary wrapped EPUB `navLabel` — grew a duplicate heading
block per pass. All fixed at the extraction seam, once, rather than at each consumer.

*The cache outliving what it cached.* Keyed on the origin string alone, a re-ingestion re-read the
bytes staged the first time — so the corrected draft the human re-ingested *because* they had
corrected it was never seen. The manifest now carries the original's digest, and `refresh` is
reachable from the tool (`reread_source`). A failed re-stage used to leave the new original beside
the old extraction under a manifest asserting they were one document; staging is now written aside
and swapped in only on success.

*Reporting success for work not done.* LS-1's copy was made before the first write was validated or
gated, so a refused write left an orphan copy, a `MISSING_MAIN_FILE` in a tree that is supposed to
stay valid, and a report saying "Filed to …" — triggered by any source whose slug is one of Layer 1's
reserved names, an ordinary `…/guides/index.html`. The copy now follows the write. A gate firing
mid-loop did not stop the loop: it kept reading, kept appending to `covered`, and reported every
chapter filed while the later half was never written. It now stops and names what it did not reach.

*Rules that were not mechanical.* LS-6's "no trace at all" depended on the model spelling `NOTHING`
exactly — "Nothing relevant to this topic." was filed as an *argument*, and one argument is what
earns the folder and the copy. LS-3 was not implemented at all and had zero citations anywhere: a
grilling book carried only `topic.cooking`, so packs and searches for `topic.cooking.grilling` never
returned it. Sections now answer `TAGS:`; a tag the tree already knows is written, one it does not is
proposed for the human (RT-25). A re-ingestion landing new content on a file the human had moved to
`status.approved` left it approved; it is re-marked `status.draft`. And a bullet the human *deleted*
came back on the next pass, un-gated, recorded as a fresh discovery — the reading record now carries
how many arguments each pass filed, which is what tells "removed by hand" from "never seen", and the
withheld text is reported to the expert rather than written back into the file.

*Reads nobody had bounded.* `origin` is a string the **model** chooses, and it reached
`Path(origin).read_bytes()` with no confinement while every other read an expert can make is
confined to the knowledge base by the backend. One `ingest_source` call read any file the daemon's
user could read and copied it into the tree — reproduced with `~/.ssh/id_rsa`. The URL branch had no
host restriction, so cloud-metadata and loopback addresses were reachable from inside a turn.
`RuntimeConfig.source_roots` bounds the filesystem (defaulting to the human's home) and
`allow_url_sources` plus a private-address guard bound the network.

*One thing that was simply forgotten.* The loop built its model with `init_chat_model` directly and
so was the only path in the system with no fallback (RG-21) — on the operation that makes 100+
sequential calls and is by far the most quota-exposed. It goes through the registry now, which is
what RG-21 said all along; `AgentRegistry.chat_model_for` is the public seam a third consumer needed.

**Six claims were refuted** by the skeptics and are *not* defects: MW-26 coverage of a mid-book model
failure, the accounting of sections with no extracted text, the reachability of the copied original
through a model's own `write_file`, the concurrency of two ingestions of one source into one topic
(RT-60/RT-61 already serialise them), the gate sequence on a re-ingestion, and the import contract's
coverage of `pkb.sources`.

### Section identity, resolved

Sections are keyed by the **heading they take in the file**, derived from the source's own title and
numbered on a repeat. That is what makes LS-5's reconciliation a per-section comparison rather than
the two-long-documents question this design exists to avoid. A source whose sections are *reordered*
between extractor versions is the one case this cannot survive, and it is the case the reading record
makes visible rather than silent.

## Open items

- ~~**Section identity within the file.**~~ Resolved by the section title — see *As built*.
- **Progress visibility.** A twenty-argument ingestion is minutes of work; the human should see it
  progress and be able to stop it. Layer 3/4, once the loop exists.
- **Very large sources.** Nothing bounds the size. A 900-page reference work may want the "consult,
  do not read" treatment rather than full extraction — and a source file with 200 sections is its own
  kind of unusable.
- **When granularity stops being enough.** If packs routinely pull whole books for one argument,
  revisit — pack-side slicing first, per-argument files only if that fails.
