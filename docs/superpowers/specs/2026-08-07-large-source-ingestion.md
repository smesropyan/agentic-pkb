# Large-source ingestion — books, papers, and anything that does not fit a turn

**Date**: 2026-08-07
**Status**: Designed, not built. Scheduled after build-order step 3.
**Scope**: crosses all three layers — a new file role in `pkb.core`, new skills and a chunked
workflow in `pkb.agents`, and the pack selection in `pkb.service`.

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
├── radical-candor.md        # the source map — thesis, provenance, what is here, what was skipped
├── parts/
│   ├── care-personally-and-challenge-directly.md
│   └── praise-in-public-criticise-in-private.md
└── radical-candor.pdf
```

The source-level file stops being a compression of the source and becomes a **map** of it, carrying
the thesis, the provenance, an index of the parts, and — load-bearing — an honest record of what was
*not* extracted. A part is a first-class file: its own frontmatter, its own `description`, its own
tags, listed in the topic index, reachable by tag, selectable into an implementation pack.

**Parts are argument-scoped, not chapter-scoped** (ruling 2). Chapters are convenient and arbitrary:
some carry three ideas and some carry none. The unit is the claim the source is making. This is
harder to make deterministic than counting chapters, and that cost is accepted deliberately.

---

## Rulings of 2026-08-07

| # | Ruling | Consequence |
|---|--------|-------------|
| **LS-1** | **The source file is copied into each topic that gainfully ingests it.** | The copy follows the *extraction*, not the routing: an expert that reads the source and files nothing does not get a copy. A topic folder stays self-contained and portable, at the cost of storing a large binary more than once — the deliberate trade. |
| **LS-2** | **Parts are argument-scoped, not chapter-scoped.** | Segmentation is a judgement the extraction skill makes, not a mechanical split. A chapter carrying three arguments becomes three parts; a chapter carrying none becomes zero. |
| **LS-3** | **A part carries the tags relevant to it — its own, inherited from the source, or both.** | Inheritance is not automatic and not forbidden. What matters is that a part is findable on its own terms; a part tagged only with the source's topic tag is as unreachable as no part at all. |
| **LS-4** | **Every expert ingests every part, and may ignore the ones its topic does not care about.** | *Not* a split of the parts between experts. Both the Management and the Parenting expert read the whole of a management book; each files what its lens sees, and the same argument may be extracted twice with different framings. That is the multi-expert ingestion ruling (README §2.2, Layer 2 LB-*) applied at part granularity, not a new rule. |

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

**Layer 1 (`pkb.core`)** — a `REFERENCE_PART` file role for `references/<source>/parts/*.md`;
location-consistency rules for it (a part is `type.reference`, `source_type: reference`, and belongs
to the topic that owns the folder); topic-index rendering that lists parts under their source rather
than flat, so a twenty-part book does not swamp the index; and a validation rule that a `parts/`
directory implies a source map file beside it.

**Layer 2 (`pkb.agents`)** — the `ingest-paper` and `ingest-book` skills; and the chunked ingestion
workflow, which is the hard part. It must: segment the source, extract part by part with a bounded
window, write each part as it goes rather than at the end, record what it skipped and why, and be
resumable — a 300-page book will not finish in one turn, and a run that dies at part 14 must not
start over. Approval gates fire per part, which makes the "several gates in one turn" behaviour from
routing (an expert parks on its own thread) directly relevant.

**Layer 3 (`pkb.service`)** — packs select parts, not sources. An implementation pack asking for
"what do I know about feedback" wants the three arguments, not four whole books.

---

## Open items

- **Segmentation determinism.** Argument-scoped parts are better and less repeatable than chapters.
  Re-ingesting the same source may produce different part boundaries, and there is no rule yet for
  what happens then — a second ingestion of a source that already has parts is undefined.
- **Part naming and stability.** A part's filename is derived from its claim, so a re-extraction can
  orphan the old file. Layer 1 never deletes, so this needs an answer before re-ingestion is allowed.
- **Progress visibility.** A twenty-part ingestion is minutes of work. The human should be able to
  see it progressing and stop it, which is a Layer 3/4 concern once the loop exists.
- **Very large sources.** Nothing here bounds the size. A 900-page reference work may need the
  "consult, do not read" treatment rather than full extraction.
