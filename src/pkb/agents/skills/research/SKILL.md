---
name: research
description: Use when exploring a question breadth-first across the knowledge base to generate options for the human to choose between. Reads topic.md, tag subtrees, and summaries — not index.md — and escalates rather than proceeding on contested knowledge.
---

# Research

Research is breadth-first by definition: cover the ground, find the candidates, present the choice.
It is not implementation, and it is not a search for the one right answer.

## What to read

- **`topic.md`** of every topic that plausibly touches the question.
- **The relevant subtrees of the tag registry** — the fastest way to find out which topics have
  anything to say about a subject, and what the neighbouring subjects are.
- **The `summary.md` files** — the human's distilled experience first, then the shape of the static
  knowledge.

**Do not read `index.md` files unless explicitly asked.** They are complete directories of a topic,
and completeness is exactly the wrong shape for this: they are depth-first material for
implementation work. Reading them here fills the context with files that were never going to matter
to the question.

Follow a specific thread into a depth file when the breadth pass has actually identified one. That
is the difference between depth and dredging.

## Contested knowledge stops the work

Include notes tagged `status.conflict-review` that touch the research area — they are often the most
informative files in the topic, because someone already found the hard part.

But when such a note **bears on the question being researched, stop and escalate.** Say which note it
is, what the open conflict is, and what the answer would depend on. Do not pick the reading that
suits the research, do not average the two positions, and do not quietly leave the contested note
out of the answer.

Proceeding on contested knowledge produces confident options built on a disagreement the human has
not settled yet — which is worse than no options, because the disagreement is now invisible.

## What to produce

An enumerated set of options, not a recommendation dressed as a summary. For each one:

- what it is, in a sentence;
- what it costs and what it gives up — the trade-off, stated as a trade-off;
- what in the knowledge base supports it, named specifically, so the human can go and look;
- what would have to be true for it to be the right choice.

Three to five options is usually right. One option is a recommendation. Ten is an unsorted list.

Say what is *not* covered, too. A gap the human can see is useful; a gap papered over with a
plausible answer is a liability.

## The approval gate

This skill ends with an explicit request for direction. Lay out the options, say which questions
would resolve the choice, and ask the human to pick — then stop.

Research files nothing on its own. If the human wants the conclusion recorded, it goes through the
ordinary ingestion procedure and its approvals like anything else. And if a conflict was found along
the way, that escalation comes before the options, not appended after them.

---

*This is a starter draft.* It ships with the implementation so that something sensible happens on
day one, and it is not meant to survive contact with the way you actually work. Rewrite it — with
your agent's help — until it says what you want done, in your words. Ask your agent to adopt this
skill and it will place an editable copy in a `skills/` folder: at the knowledge-base root to change
the procedure everywhere, or inside a single topic to change it for that topic alone. From that
moment your copy replaces this text and later shipped improvements no longer reach it; deleting your
copy brings this one back.
