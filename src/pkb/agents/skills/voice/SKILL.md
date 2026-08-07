---
name: voice
description: Use when drafting any prose the human will read as their own — curated notes, summaries, topic.md. Describes the human's writing voice, and, until a voice profile exists, how to build one from their existing writing.
---

# Voice

Everything an agent drafts for this knowledge base is read by one person, and much of it is read as
if they had written it. It should sound like them.

**This file does not contain a voice profile yet.** It contains the procedure for building one. Once
the human has adopted this skill and rewritten it, this file *is* their voice profile, and it should
be read as a description of how they write — not as instructions about how to find out.

## What voice covers, and what it does not

Voice applies to **prose bodies**: the text of notes, the summaries, the paragraphs in `topic.md`.

It does not apply to:

- **frontmatter values.** A `description` stays a single factual line whatever the house style is;
  it is read by machinery and by people skimming a list, and personality there costs clarity.
- **anything generated.** Machine-generated files have their own fixed shape and are never written
  by hand.
- **messages to the human in conversation.** Talk normally; voice is about what gets filed.

## Until a profile exists

Do not invent one. A plausible-sounding default voice would shape every draft invisibly and would be
wrong in ways nobody could point at. Instead:

- **Mirror the source.** When filing something the human said or wrote, keep their phrasing, their
  vocabulary, and their level of formality. Their words are the best available sample of their
  voice.
- **Otherwise write plainly.** Short sentences. Concrete nouns. No throat-clearing, no summary of
  what you are about to say, no closing paragraph restating it. Plain writing is never wrong; it is
  merely not yet personal.

## Building the profile

1. **Gather samples.** Notes they have already written, documents they hand over, long messages they
   send in conversation. Anything they wrote for themselves is a better sample than anything they
   wrote for an audience.
2. **Draft a description of the voice** — not rules, observations. Sentence length and rhythm. How
   much hedging. Whether they use humour, and what kind. Whether they write in the first person.
   Words they reach for and words they avoid. Whether they explain from the general to the specific
   or the other way round.
3. **Show it to them and let them correct it.** People are unreliable narrators of their own writing
   and reliable critics of a description of it — which is why this is a draft to react to rather
   than an interview.
4. **Refine it as notes accumulate.** Every note the human writes or edits is a fresh sample. When a
   draft comes back edited, the edits are the most informative sample of all: note what they
   changed and fold it in.

## The hard boundary

Applying voice **never changes what a human-written note says.** Style and facts are not adjacent
here — rewriting a sentence "for flow" is how a claim quietly becomes a different claim.

For anything the human wrote, style changes are proposed, not applied: show the before and after and
let them choose. For text you drafted yourself, write it in their voice from the start.

## The approval gate

Two gates matter here. Editing the body of a note the human wrote pauses for them, so a style pass
over their own text arrives as a proposal they can see line by line. And the voice profile itself is
theirs: this file is human-authored and agent-assisted like any other skill, so a change to it pauses
in exactly the same way.

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
