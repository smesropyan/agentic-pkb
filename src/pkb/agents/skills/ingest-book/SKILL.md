---
name: ingest-book
description: Use when extracting a book, a long report, or anything else with chapters into this topic's reference file. Walks the source's own structure chapter by chapter, records what each one gives this topic and what it does not, and leaves a file that says plainly which parts were read.
---

# Ingesting a book

A book arrives one chapter at a time, and the chapters are handed to you — you do not decide when
the reading is finished. That is the whole reason this is not an ordinary filing turn. A book does
not fit in front of you, and an agent left to judge its own progress writes a confident account of
the part it saw and stops there, with nothing anywhere recording that the rest was never opened.

So: write what the section in front of you actually contains, and let the loop bring you the next
one. Never write about a chapter you have not been shown.

## The shape of the file

One file for the whole source, organised by the source's own structure:

```markdown
## Chapter 2 — Challenge directly
- <an argument this topic cares about>
- <another>

## Chapter 5 — Care personally
- <an argument>

## Across the book
- <the ideas no single chapter owns>
```

Above the chapters, a few lines of **thesis**: what the book argues, as the book would put it. Below
them, the ideas that belong to the whole book rather than to any one part of it.

The chapter headings are not decoration. Chapter 3 is chapter 3 on every reading, which is what lets
a second pass line up with the first without anyone inventing identifiers for arguments. Name each
section after the chapter as the book names it.

Inside a section, **one bullet per argument, not one per chapter**. A chapter making three arguments
this topic cares about becomes three bullets; a chapter making one becomes one.

## What this topic takes, and what it leaves

The question at each chapter is not *what does this chapter say* but *what does this chapter give
this topic*. Those have different answers, and the second one is often nothing.

A management book read for a topic about raising children yields arguments about raising children.
It stays silent on org design, headcount and performance cycles, however good those chapters are —
that material belongs to whichever topic cares about it, and the same book was very likely handed
there too. Extract through the lens you have, and leave the rest.

**A chapter that gives this topic nothing yields nothing.** No section, no bullet, and no line
explaining that the chapter was about something else. This is the failure worth guarding against:
an agent that feels every chapter is owed an entry writes one, and a file with twenty entries of
which six are real is worse than a file with six, because nobody can tell which six.

Padding is not thoroughness. Silence is a correct outcome, and a common one.

## Read and took nothing is not the same as never opened

Both leave no section in the body, and they are completely different facts. One says this topic
found nothing there; the other says nobody has looked yet. The reading record below is where they
are told apart, and keeping them apart is most of what makes this file trustworthy later.

## What an argument looks like once written down

Each bullet carries three things, in whatever order reads well:

- **the claim**, in a sentence someone could disagree with;
- **the reasoning or evidence the book offers** for it — this is what makes it reusable instead of
  an assertion someone has to take on faith;
- **the conditions it depends on**, where the book states them.

A bullet that would fit in a table of contents is not an argument. "Chapter 4 is about feedback"
tells a later reader nothing they could act on or argue with; "praise publicly and criticise
privately backfires when the team reads public praise as ranking" does.

Quotes are for the sentence the book says better than you can. A file that is mostly quotes has
extracted nothing — it has moved the reading to whoever opens it next.

## It is the book's claim, not the human's experience

Everything here is source-derived and stays that way. This file says what the book argues; it never
says what worked. When the human tries something out of it and tells you how it went, that is a note
and it is theirs to write — "we tried this and it worked" is not a sentence you may put in their
mouth, however well the book argued for it.

Where a note already covers the same ground, point at it rather than absorbing it. Where a note
disagrees with the book, that is a conflict to raise, and the human's own experience wins.

## The reading record

Every pass appends what it covered, what it skipped, why, and when. Not a formality — it is the only
thing standing between this file and the failure at the top of this page.

Be specific enough to act on: "chapters 1-9 read; 10-14 are extended case studies, skimmed and not
extracted; chapter 12 is worth a second look for its account of hiring" is useful. "Partially read"
is not. A file that does not say what it left out reads as finished, so nobody goes back to it,
while an honestly partial file invites the second pass it needs.

Record the tags the file carries as the union of what its sections are actually about. One file
holds every argument you extracted, so it is findable only by what you say it contains.

## Provenance

Say where the source came from — title, author, edition or printing, and how it arrived — and link
the copy of the original that sits beside this file in the same folder. The link is not decoration:
a source file that nothing points at is reported as an orphan, once in every topic that took a copy.
It is also what lets a later reading start from the book instead of from your account of it.

## Reading it again

A source is worth re-reading when the topic has moved on or the extraction was thin. A second pass
**reconciles** with the file on disk rather than replacing it, and it compares section by section —
handing two long documents to one reading and asking "is anything new here" reproduces exactly the
failure this whole procedure exists to prevent.

Four outcomes, and only one of them writes freely:

- **An argument the file does not have** — add it, under its chapter.
- **A better statement of an argument already there** — propose the rewording; do not apply it. The
  human may have read and relied on the old wording.
- **An argument that contradicts one already there** — neither side is the human's, so nothing is
  overwritten and nothing is merged. Flag it and let them settle it.
- **Nothing where the file has an argument** — keep the argument. This pass not finding it is not
  evidence that it is wrong; note the coverage and move on.

## The approval gate

The first extraction of a source lands unattended: capture stays frictionless and there is nothing
of the human's underneath it yet. Reading the same source again is a different act — the file you
would rewrite is one they have already read and relied on — so a re-ingestion ends at the
**re-ingestion gate**, with one proposal for the whole reconciled file and a summary of what
changed. One decision about one document, never one decision per chapter.

Two ordinary gates fire along the way whether or not this skill ran: a tag this knowledge base has
not seen before pauses before the file lands, and the first file in a folder that does not yet exist
pauses. Neither is a reason to avoid the tag or the folder — propose it and wait.

And if the book gave this topic nothing at all, file nothing at all: no folder, no stub, and no copy
of the source. An empty folder implies the source was considered and is somehow relevant; zero trace
says what actually happened, and the human hears it from you in one line instead.

A topic that rewrites this skill can add to it — a house convention for chapter naming, a required
section, a rule about which kinds of book are worth re-reading. It cannot lower the bar: what a
chapter was worth is still recorded, and the same checks run on the file regardless of which version
of this skill produced it.

---

*This is a starter draft.* It ships with the implementation so that something sensible happens on
day one, and it is not meant to survive contact with the way you actually work. Rewrite it — with
your agent's help — until it says what you want done, in your words. Ask your agent to adopt this
skill and it will place an editable copy in a `skills/` folder: at the knowledge-base root to change
the procedure everywhere, or inside a single topic to change it for that topic alone. From that
moment your copy replaces this text and later shipped improvements no longer reach it; deleting your
copy brings this one back.
