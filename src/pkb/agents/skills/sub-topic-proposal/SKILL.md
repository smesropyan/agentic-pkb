---
name: sub-topic-proposal
description: Use when a topic has grown large enough that its breadth files can no longer summarize it honestly. Proposes a split with the evidence and a per-file assignment, and creates nothing until the human approves.
---

# Sub-topic proposal

A topic should be splittable long before it is unmanageable, and splitting it is a decision with
consequences that are hard to undo. So this skill produces a *proposal*, in full, and stops.

## The signal to watch for

The reliable one is not file count — it is that the topic's `notes/summary.md` has stopped being a
distillation and become a list. When a summary can no longer say "here is what we know" without
enumerating, the topic is holding two subjects that deserve their own summaries.

Supporting evidence worth gathering before proposing:

- how many notes and references the topic holds, and how that has changed;
- the distinct tag clusters in it — if the tags fall into two groups that barely co-occur, that is
  the split, already drawn for you;
- whether questions about the topic keep needing only half of it;
- whether the breadth files have grown past a comfortable read.

Bring the evidence with the proposal. "This topic feels large" is not a proposal a person can weigh.

## What a proposal contains

1. **The name and description of each proposed sub-topic.** The description is a real one — it will
   be what everyone sees when work is routed, so write it as carefully as any other description.
2. **The full file assignment.** Every existing file, listed, with the sub-topic it would move to or
   a note that it stays where it is. All of it, up front. A partial plan hides exactly the awkward
   cases that decide whether the split is right.
3. **What happens to the breadth files** — what each new sub-topic's summaries would say, and what
   is left in the parent's after the move.

## The depth budget

Topics nest, and the tag that mirrors that nesting may be at most 4 segments deep including its
namespace. So a topic already three levels down cannot hold sub-topics: every file inside one would
be unable to carry a tag that matches where it lives, and the scaffolder refuses to create it at
all. Check the depth before proposing, and if there is no room, say so and propose something else —
a new top-level topic, or an extension folder inside the existing one.

## Moving files is a separate decision

Creating the sub-topic and moving notes into it are two different acts, and the second one is
approved **per file**.

There is no undo here. Moving a note is a write followed by a delete, the knowledge base keeps no
version control and no backups, and a deletion is permanent the moment it is approved. That is why
the whole file list is presented before the first move: the human should see the entire plan while
it is still cheap to change, not discover its shape one approval at a time.

If a move goes wrong, the only remedy is rewriting the note from whatever is left of it. Say that
plainly when the human is deciding, rather than after.

## The approval gate

Creating a sub-topic pauses for the human, and the pause lets them approve it, rename it, or rewrite
its description before anything is created. Nothing is scaffolded before that decision, and each
subsequent file move pauses on its own.

So this skill's output is a document, not a change: the evidence, the proposed sub-topics, and the
file assignment. The knowledge base looks exactly the same after running it as it did before.

---

*This is a starter draft.* It ships with the implementation so that something sensible happens on
day one, and it is not meant to survive contact with the way you actually work. Rewrite it — with
your agent's help — until it says what you want done, in your words. Ask your agent to adopt this
skill and it will place an editable copy in a `skills/` folder: at the knowledge-base root to change
the procedure everywhere, or inside a single topic to change it for that topic alone. From that
moment your copy replaces this text and later shipped improvements no longer reach it; deleting your
copy brings this one back.
