---
name: voice
description: Use when drafting any prose the human will read as their own — curated notes, summaries, topic.md. Holds the voice profile every draft is written in, and the rule for narrowing it per topic when one subject wants a different register from another.
---

# Voice

Everything an agent drafts for this knowledge base is read by one person, and much of it is read as
if they had written it. It should sound like them.

This file is the voice profile. It ships with a starter profile so that day-one drafts have a
definite style instead of an accidental one — but it is a guess about a person the implementation
has never met. Treat every line below as a claim to be corrected, and correct it early: a profile
nobody has edited is the one most likely to be wrong.

## The profile

Write **plainly, concretely, and without ceremony**.

- **Lead with the claim.** The first sentence says the thing. Reasons, caveats and background come
  after, if at all. Do not open by announcing what the note is about — the title does that.
- **Short sentences, ordinary words.** Prefer *use* to *utilise*, *before* to *prior to*, *because*
  to *due to the fact that*. A long sentence is fine when the thought is genuinely long; two clauses
  bolted together with a semicolon usually is not.
- **Concrete over abstract.** Numbers, temperatures, durations, names, prices. "Fifteen minutes at
  high heat" beats "an adequate preheating period". Where a number is known, it goes in.
- **First person for experience, plain statement for fact.** "I tried the reverse sear and the crust
  was better" is experience. "Reverse searing cooks the interior before the crust forms" is fact.
  Do not dress a fact as experience, and do not launder an opinion into a fact.
- **Hedge only where the uncertainty is real**, and say what would settle it. "Probably the wind —
  worth trying again on a still day" is useful. "It may perhaps be somewhat affected by conditions"
  is noise wearing a lab coat.
- **No throat-clearing and no summary paragraph.** No "it's important to note that", no "in
  conclusion". When the content ends, the note ends.
- **Dry humour, sparingly, never at the reader's expense.** A wry aside in a note about a failure is
  in character. A joke in a summary that someone will rely on is not.
- **Second person for instructions, imperative for steps.** A procedure reads "pull the steak at
  52 °C", not "the steak should be pulled".

Formatting follows the same instinct: prose by default, a list when the content is genuinely a list,
a table when there are three or more things with the same attributes. Do not impose structure on a
paragraph that is just a paragraph.

## What voice covers, and what it does not

Voice applies to **prose bodies**: the text of notes, the summaries, the paragraphs in `topic.md`.

It does not apply to:

- **frontmatter values.** A `description` stays a single factual line whatever the house style is;
  it is read by machinery and by people skimming a list, and personality there costs clarity.
- **anything generated.** Machine-generated files have their own fixed shape and are never written
  by hand.
- **messages to the human in conversation.** Talk normally; voice is about what gets filed.

## One voice per topic, where a topic needs one

A single voice is not right everywhere. Notes about cooking are personal, forgiving and often
narrative — what was tried, what happened, what to do differently. Notes about trading are
adversarial and expensive to get wrong: they want dates, sizes, the reasoning at the time, and no
retrospective smoothing of what was actually thought. The same sentence rhythm serves both badly.

So voice is **overloadable per topic**, like any skill. A topic that holds a copy of this file at
`skills/voice/` inside its own folder uses that copy; a topic without one uses the profile at the
knowledge-base root; the nearest copy on the way up wins. A sub-topic may narrow its parent again.

**A topic copy replaces this file for that topic — it does not merge with it.** Whatever a topic
voice does not say, it does not inherit. So a topic profile should be a whole profile: restate the
parts of the general voice it keeps, then say what changes. That is more text than a diff, and it is
the only version that behaves predictably a year later when nobody remembers what the root said.

Propose a topic voice when the evidence is in the notes, not on a hunch: when drafts for one topic
keep coming back edited in the same direction, that direction is a topic voice asking to exist. Say
what you have noticed, show two or three of the edits, and let the human decide whether it is a
topic rule or a general one. Filing it in the wrong place is not fatal — a general rule in one topic
just fails to apply elsewhere — but a topic voice invented from a single note is noise.

## Keeping the profile honest

Every note the human writes or edits is a fresh sample. The most informative sample of all is a
draft that comes back edited: the edits say precisely where the profile is wrong.

- Watch for a change repeated across three or more drafts. One edit is a preference in the moment;
  three is a rule this file is missing.
- Watch for lines here that never survive contact — a rule that gets edited out every time it is
  applied is wrong, not under-applied.
- When you propose a change, bring the evidence: the drafts, the edits, and the line you want to
  change. People are unreliable narrators of their own writing and reliable critics of a description
  of it.

## The hard boundary

Applying voice **never changes what a human-written note says.** Style and facts are not adjacent
here — rewriting a sentence "for flow" is how a claim quietly becomes a different claim.

For anything the human wrote, style changes are proposed, not applied: show the before and after and
let them choose. For text you drafted yourself, write it in their voice from the start.

## The approval gate

Two gates matter here. Editing the body of a note the human wrote pauses for them, so a style pass
over their own text arrives as a proposal they can see line by line. And the voice profile itself is
theirs: this file is human-authored and agent-assisted like any other skill, so a change to it —
whether to this general profile or to a topic's own copy — pauses in exactly the same way.

The profile is never updated silently in the background from what you inferred this week. Propose,
show the evidence, let them decide.

---

*This is a starter draft.* It ships with the implementation so that something sensible happens on
day one, and it is not meant to survive contact with the way you actually work. Rewrite it — with
your agent's help — until it says what you want done, in your words. Ask your agent to adopt this
skill and it will place an editable copy in a `skills/` folder: at the knowledge-base root to change
the procedure everywhere, or inside a single topic to change it for that topic alone. From that
moment your copy replaces this text and later shipped improvements no longer reach it; deleting your
copy brings this one back.
