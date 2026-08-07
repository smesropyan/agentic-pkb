---
name: discovery
description: Use when running an idea-discovery or brainstorming session against knowledge-base content. Reads breadth-first, generates ideas with the human, and files nothing as a side effect.
---

# Discovery

A discovery session is thinking out loud with someone who has read everything. The knowledge base is
the raw material; the session is where connections that nobody wrote down get made.

## Read in this order

1. **`notes/summary.md` first.** It holds the human's own distilled experience, and their rules
   outrank everything else in the room. Starting anywhere else means spending the session
   rediscovering things they already know.
2. **`topic.md`** — what the topic covers and how it is organised, so a suggestion lands somewhere
   real.
3. **`references/summary.md`** — what static knowledge is available, and where it disagrees with
   itself.
4. **Depth files only on demand**, when a specific thread needs them. Pulling in everything up front
   buries the session in detail before it has a direction.

## Running the session

**Surface tensions and gaps rather than restating content.** The human has read their own notes.
What they have not done is notice that two of them assume opposite things, or that the topic's
summary keeps referring to something no note actually covers. That noticing is the whole value.

Useful moves:

- **Name the tension.** "These two notes both work, but never in the same conditions — is there a
  rule underneath that you have not written down?"
- **Find the gap.** "Everything here is about the setup. Nothing says what to do when it goes
  wrong."
- **Go across topics.** The knowledge base tracks which topics are related, drawn from what the files
  themselves declare. Use it: the most interesting ideas in a personal knowledge base usually come
  from the seam between two topics the human never thought about together.
- **Push back.** A discovery session in which the agent agrees with everything is a wasted hour.

Generate more than you keep. Volume first, judgement second, and let the human do the judging.

## Nothing is filed as a side effect

A discovery session writes nothing. Not a summary of the session, not "capturing" a promising idea
while you both remember it, not an update to the summary because a good rule emerged.

This is deliberate. Session output is unreviewed by construction — it is the middle of a
conversation, not a conclusion — and a knowledge base that fills up with half-formed session
artefacts stops being trustworthy. Anything worth keeping goes back in the front door: it gets filed
through the ordinary ingestion procedure, with its own metadata, its own classification, and its own
approval. If that feels like too much ceremony for the idea, it was not ready.

## The approval gate

This skill ends in a hand-off, not a write. Close the session by listing what came out of it — the
ideas worth keeping, the tensions worth resolving, the gaps worth filling — and ask which of them
the human wants filed.

Whatever they choose then goes through ingestion and whatever gates that raises. There is no path
from this skill to a file.

---

*This is a starter draft.* It ships with the implementation so that something sensible happens on
day one, and it is not meant to survive contact with the way you actually work. Rewrite it — with
your agent's help — until it says what you want done, in your words. Ask your agent to adopt this
skill and it will place an editable copy in a `skills/` folder: at the knowledge-base root to change
the procedure everywhere, or inside a single topic to change it for that topic alone. From that
moment your copy replaces this text and later shipped improvements no longer reach it; deleting your
copy brings this one back.
