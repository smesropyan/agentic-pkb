---
name: de-slop-v2
description: Rewrite a piece of text to sound more human by stripping out AI "slop" — throat-clearing openers, business jargon, adverbs, meta-commentary, binary contrasts, false agency, passive voice, and formulaic rhythm — while preserving the author's own voice. Use whenever the user runs /de-slop-v2, or asks to de-slop, humanize, or clean up the AI voice in an output, draft, essay, email, document, or any block of text.
---

# de-slop-v2

Take a piece of text and return a rewritten version with the slop removed. The goal is prose a careful human would actually write: direct, specific, and free of the tics that mark machine-generated writing.

## Input

The text to de-slop is one of, in priority order:

1. Whatever the user passed as arguments to the command.
2. A file the user pointed at (read it first).
3. The most recent substantial output in the conversation (the thing they just generated). If it's ambiguous which block they mean, ask.

If no candidate text exists, ask the user to paste or point at what they want de-slopped.

**Also look for a voice sample.** If the user supplied one, use it. If the target text sits inside a larger document the
author wrote, the rest of that document is the sample. Read it before rewriting anything (`references/voice.md`).

## Process

1. **Decide the register first, because it decides which rules apply.** Ask which of these the text is, and read it
   to check the answer rather than taking it on trust:
   - **Procedural** — a runbook, an install guide, a rule table, an instruction a reader follows while doing something.
   - **Prose that argues** — a design document, an essay, a memo, a README that explains why something is shaped as it
     is.
   - **Casual** — a note, an email, a message.
2. Read `references/phrases.md`, `references/structures.md` and `references/voice.md`. Load them every run; don't rely
   on memory.
   **Read `references/ste100.md` only for procedural text.** ASD-STE100 (https://www.asd-ste100.org/) is a
   controlled language for maintenance manuals. Applied to prose that argues it removes gerunds that carry actions,
   figures that do real work and sentences whose clauses depend on each other, and the result reads cleaner while
   saying less. For casual text, apply neither STE100 nor the stricter structural rules.
3. Read the target text closely. Identify every instance of a flagged phrase or structure.
4. Rewrite. For each offender, apply the fix the references prescribe:
   - Cut throat-clearing openers and state the point directly.
   - Swap business jargon for plain words.
   - Delete adverbs, softeners, intensifiers, and filler phrases.
   - Remove meta-commentary and self-referential asides.
   - Replace binary contrasts ("not X, but Y") and negative listing with a direct statement of the actual point.
   - Name the human actor instead of giving inanimate things agency ("the team fixed it," not "the complaint becomes a fix").
   - Convert passive voice to active — put the actor at the front.
   - Restructure Wh- sentence openers to lead with subject or verb.
   - Remove all em-dashes; use commas or periods.
   - Break up staccato fragmentation and vary sentence and paragraph endings.
   - Replace lazy extremes (every, always, never, everyone) with specifics.
   - For procedural text only, apply ASD-STE100: short, single-idea sentences, active voice, simple tenses,
     imperative for instructions, one word per meaning, no noun clusters, no nominalized verbs.
5. **Check what you kept, not only what you cut.** Walk the "Do not strip" list in `references/voice.md` against your
   rewrite. Then walk the patterns to reach for: a claim joined to its reason, a term glossed in an appositive, a colon
   that unpacks, a plain verb, a named instance. Removal alone cannot produce these, so a rewrite that only subtracts
   lands somewhere between inoffensive and flat.
6. Preserve the author's meaning, facts, structure, and length intent. De-slopping is subtraction and substitution, not rewriting the argument. Don't invent claims, add examples, or change the conclusion.
7. Keep the author's register. A casual note stays casual; a formal memo stays formal. Removing slop should not make text stiffer.

## Output

**A standalone passage**: return the de-slopped text and nothing else — no preamble, no "here's your rewrite." Then,
only if the user asks or the changes are extensive, list the notable cuts in a few lines ("removed 3 throat-clearing
openers, 6 adverbs, converted 2 passive sentences").

**A whole file the user wants a clean copy of**: write the rewrite to a sibling file (`<name>.deslopped.<ext>`) rather
than overwriting the original, and present it.

**A section inside a document the author is working on**: edit it in place and report what changed, because a sibling
copy of a 2,000-line specification is unusable and the author needs to see the edits rather than the text. Separate the
typos and grammar from the judgment calls, and name every term you changed for consistency, since those are the ones
they may want back.

## Judgment

The references are a checklist, not a straitjacket. A rule may occasionally be wrong for a specific sentence — a real em-dash in a quote, an adverb that carries actual meaning, a genuine three-item list of concrete things. When a mechanical fix would damage the sentence, keep the meaning and note the exception rather than forcing the rule. Bias toward cutting: when a word or phrase adds no meaning, remove it.

ASD-STE100 and "sound more human" pull the same direction on passive voice, filler and needless complexity, and apart
on almost everything else. STE100 bans gerunds, nominalizations and figurative language, and prose that argues needs all
three. Load it for procedural text and leave it out otherwise; that is step 1's job, not a judgment call at the end.

**Bias toward cutting a word that adds no meaning, and toward keeping a sentence that works.** Those two are not in
tension: the first is about words, the second about structure. When a rule would flatten a sentence that carries its
point, keep the sentence and say which rule you declined and why. A reader can accept a declined rule. They cannot
recover a fact that died inside a compression.
