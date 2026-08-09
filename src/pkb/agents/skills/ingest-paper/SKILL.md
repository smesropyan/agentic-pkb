---
name: ingest-paper
description: Use when extracting a research paper, a study, a whitepaper or a technical report into this topic's reference file. Walks the paper's own sections, records the question, method, results and limitations, and ends with the judgement the knowledge base actually needs — whether the finding applies to this human.
---

# Ingesting a paper

A paper has a skeleton, and the skeleton is what makes an extraction useful instead of a paraphrase.
Use the paper's own sections as you are handed them — method is method, results are results — and
write each part as you reach it rather than saving one summary for the end. A long paper, a
supplement, or a report with forty pages of appendix arrives in windows for the same reason a book
does: what does not fit in front of you must not be written about as though it did.

## The shape of the file

Five parts, in this order, above whatever the paper's own sections add:

```markdown
## Question
## Method
## Results
## Limitations
## Does this apply to me
```

**Question** — what they actually asked, which is usually narrower than the title. "Does caffeine
improve performance" is a title; "does 3 mg/kg taken 60 minutes before a session improve time to
exhaustion in trained cyclists" is the question, and the difference is the whole value of the file.

**Method** — what they did, in enough detail that a reader can tell whether it transfers: who the
subjects were and how many, how long it ran, what was measured and how, what it was compared
against. A result whose method is missing cannot be judged by anyone later, and nobody re-reads the
paper to recover it.

**Results** — what they found, in the paper's own numbers and direction. "It worked" is not a
result. Effect size, the comparison it is against, and how certain the authors are about it.

**Limitations** — the ones the authors state, and the ones you notice they do not: a sample nothing
like this human, a duration far shorter than the effect being claimed, a single site, an outcome
measured by the people who wanted it. Say which is which. Reporting an author's caveat as your own
scepticism is as misleading as leaving it out.

Where the paper carries substantial material these five parts do not hold — a protocol, a taxonomy,
a dataset description worth knowing about — add a section named as the paper names it. Those names
are the anchor a later reading lines up against: the method section is the method section on every
reading, which is what makes a second pass a comparison rather than a fresh guess.

## Does this apply to me

This section is why the file is worth having, and it is the one no paper contains.

A result is not yet knowledge here. It becomes knowledge when someone has said whether it applies to
this human's circumstances — their situation, their constraints, their equipment, their body, the
scale they work at. So write the judgement, and name the mismatch that drives it: *the subjects were
trained cyclists under laboratory conditions; the human trains three times a week outdoors and has
never measured time to exhaustion, so treat the direction as suggestive and the magnitude as not
applicable.*

"Probably not, and here is why" is a good answer and a common one. A paper that plainly does not
apply, ingested honestly, saves everyone the trouble of finding it again and wondering.

Mark it as what it is: an argument you are making, not a finding the paper reports. It is yours to
draft and the human's to correct, and it is the part of this file most likely to be wrong.

## The paper's claims are not the human's experience

Everything above the applicability section belongs to the paper. This file is a reference: it
records what the paper found, never what happened when someone tried it. The note that says "we
tried this and it worked" is the human's to write, in their own words, and it outranks this file
from the moment it exists.

So do not turn a finding into a rule. A rule distilled from experience lives in the topic's notes
and their summary, and it belongs to the human. One study is one study; say so plainly when the
evidence is a single result, a preprint, or a replication that has not happened.

Where existing notes bear on the paper, point at them. Where a note contradicts it, raise the
conflict rather than settling it — the human's experience wins, and the paper is not thereby wrong.

## What not to extract

A paper carries a great deal that is not for you. The related-work section is somebody else's paper,
summarized by an author with a reason to frame it a certain way. The methods appendix matters only
where it changes whether the result transfers. A discussion section speculating three steps past the
data is speculation, and if it is worth recording at all it is recorded as such.

And if the paper's question is not one this topic cares about, take nothing from it and say so in
one line. A near-miss extraction filed to look diligent is harder to find than nothing at all, and
it leaves the topic looking as though it knows something it does not.

## Provenance and the reading record

Say where the paper came from — authors, venue, year, DOI or link — and link the copy of the
original sitting beside this file in the same folder. A source file that nothing points at is
reported as an orphan.

Then record what you actually read: which sections, what you skipped and why, and when. "Results and
discussion read; the statistical appendix was not opened" is a fact someone can act on. A file that
does not say what it left out reads as finished, and nobody goes back to it.

## Reading it again

Papers get re-read when the topic's questions change, or when the human's circumstances do — which
is the one that matters most here, because the applicability judgement was written about a situation
that may no longer hold. A second pass reconciles with the file on disk section by section rather
than replacing it wholesale: new material is added, a better statement of something already there is
proposed rather than applied, a contradiction between two readings is flagged and left for the
human, and anything the earlier pass recorded that this one did not find is kept.

## The approval gate

The first extraction lands unattended — capture stays frictionless and there is nothing of the
human's underneath it yet. Reading the same paper again is a different act, because the file you
would rewrite is one they have already read and relied on, so a re-ingestion ends at the
**re-ingestion gate**: one proposal for the whole reconciled file, with a summary of what changed.

Two ordinary gates fire along the way whether or not this skill ran: a tag this knowledge base has
not seen before pauses before the file lands, and the first file in a folder that does not yet exist
pauses.

Nothing here lets you write into the topic's notes or its summaries. The applicability judgement
argues that a finding is worth trying; the human is the one who tries it and the one who writes down
what happened.

A topic that rewrites this skill can add to it — an evidence-grading scale, a required section on
study design, a house rule about preprints. It cannot drop the applicability judgement or promote a
paper's claim into experience.

---

*This is a starter draft.* It ships with the implementation so that something sensible happens on
day one, and it is not meant to survive contact with the way you actually work. Rewrite it — with
your agent's help — until it says what you want done, in your words. Ask your agent to adopt this
skill and it will place an editable copy in a `skills/` folder: at the knowledge-base root to change
the procedure everywhere, or inside a single topic to change it for that topic alone. From that
moment your copy replaces this text and later shipped improvements no longer reach it; deleting your
copy brings this one back.
