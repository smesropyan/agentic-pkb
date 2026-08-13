---
name: de-slop
description: Rewrite a piece of text to sound more human by stripping out AI "slop" — throat-clearing openers, business jargon, adverbs, meta-commentary, binary contrasts, false agency, passive voice, and formulaic rhythm. Use whenever the user runs /de-slop, or asks to de-slop, humanize, or clean up the AI voice in an output, draft, essay, email, or any block of text.
---

# de-slop

Take a piece of text and return a rewritten version with the slop removed. The goal is prose a careful human would actually write: direct, specific, and free of the tics that mark machine-generated writing.

## Input

The text to de-slop is one of, in priority order:

1. Whatever the user passed as arguments to the command.
2. A file the user pointed at (read it first).
3. The most recent substantial output in the conversation (the thing they just generated). If it's ambiguous which block they mean, ask.

If no candidate text exists, ask the user to paste or point at what they want de-slopped.

## Process

1. Read `references/phrases.md`, `references/structures.md`, and `references/ste100.md`. These hold the full rule set — the specific phrases, jargon swaps, structural patterns to remove, and the ASD-STE100 (https://www.asd-ste100.org/) controlled-language rules to apply on top. Load them every run; don't rely on memory.
2. Read the target text closely. Identify every instance of a flagged phrase or structure.
3. Rewrite. For each offender, apply the fix the references prescribe:
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
   - Apply ASD-STE100: short, single-idea sentences, active voice, simple tenses, imperative for instructions, one word per meaning, no noun clusters, no nominalized verbs.
4. Preserve the author's meaning, facts, structure, and length intent. De-slopping is subtraction and substitution, not rewriting the argument. Don't invent claims, add examples, or change the conclusion.
5. Keep the author's register. A casual note stays casual; a formal memo stays formal. Removing slop should not make text stiffer.

## Output

Return the de-slopped text and nothing else by default — no preamble, no "here's your rewrite."

Then, only if the user asks or if the changes are extensive, offer a short list of the notable cuts (e.g. "removed 3 throat-clearing openers, 6 adverbs, converted 2 passive sentences"). Keep this to a few lines.

If the text was in a file, write the rewrite back as a sibling file (`<name>.deslopped.<ext>`) rather than overwriting the original, and present it.

## Judgment

The references are a checklist, not a straitjacket. A rule may occasionally be wrong for a specific sentence — a real em-dash in a quote, an adverb that carries actual meaning, a genuine three-item list of concrete things. When a mechanical fix would damage the sentence, keep the meaning and note the exception rather than forcing the rule. Bias toward cutting: when a word or phrase adds no meaning, remove it.

ASD-STE100 and "sound more human" mostly pull the same direction — both cut passive voice, filler, and needless complexity. Where they conflict (STE100's rigid one-word-per-meaning vs. natural variety in casual writing), match the target register: apply STE100 fully for technical/procedural text, more loosely for a casual note or email.
