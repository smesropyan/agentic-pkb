---
name: conflict-detection
description: Use when running a scheduled or on-demand conflict scan for a topic, or when new material appears to contradict existing knowledge. Compares notes, summaries, and references, tags findings for human review, and never resolves a conflict itself.
---

# Conflict detection

## The rule that comes before everything else

**Human content wins over static knowledge, always.**

Human content means the human's own notes and the summaries they have approved. A published book, a
well-regarded article, a manufacturer's manual — none of them outrank a note the human wrote from
their own experience. So:

- never resolve a conflict in the reference's favour;
- never rewrite a note so that it agrees with a reference;
- never suppress a finding because the reference looks authoritative.

If a human note is in fact wrong, the human edits it until it is right. That is their call, not
yours. Your job ends at *this disagrees with that, here is why I think so*.

This rule settles every conflict that has a human side to it. Two sources disagreeing with each
other have none, and nothing above decides them — see the fourth axis below.

## What to compare

Four axes, all four every time a full scan runs:

1. **`notes/summary.md` against `references/summary.md`** — the two breadth views of the same topic.
   Disagreement here is the most valuable kind to find, because both files are read constantly.
2. **Individual notes against references** — the most common source of real conflicts, and where
   ingested material meets lived experience.
3. **Notes against notes** — the same person, at different times, under conditions they did not
   write down.
4. **References against references** — two sources that contradict each other, including a fresh
   reading of a source against the extraction of that same source already on disk. Neither side is
   the human's, so this is the axis the rule at the top cannot decide.

Compare meaning, not strings. Two statements can use none of the same words and say opposite things;
two statements can look opposite and both be true under conditions neither one states. Use what you
know about the subject to tell those apart — that judgement is the reason a person is running this
scan instead of a text search.

## Classifying what you find

Sort each finding into one of three kinds, and say how confident you are:

- **contradiction** — the statements directly oppose each other.
- **nuance** — both are true, under different conditions. Very often the honest answer, and the one
  a naive comparison gets wrong.
- **outdated** — the static knowledge is older and has been overtaken.

The kind and the confidence belong **in the conversation only**. Never write them into a file. The
knowledge base deliberately keeps no conflict register, no confidence score, no resolution log and
no record that a conflict ever happened; a file that carries such a field is rejected outright. The
note's own content is the true state of knowledge, and once a conflict is resolved there is nothing
left to say about it.

## Tagging a finding

When a conflict touches human content, do exactly three things to **the human content file**:

1. add the tag `status.conflict-review`;
2. add a short `review_note` saying what the conflict is, concretely enough to act on — name the
   other file and the disagreement in one or two sentences;
3. leave the body completely unchanged.

When the other side is a human's, the reference is neither tagged nor edited. It is not wrong; it
merely disagrees with someone who was there.

**When neither side is human**, there is no experience to prefer, so the flag goes on a reference
instead — the same three acts, unchanged. Tag both when both are files in the tree; when one side is
a reading you have just done and have not filed, the file on disk is the one that carries the flag.
Do not merge the two accounts into one, do not prefer the later reading for being later, and do not
drop the older text. Which source the human trusts on this point is exactly what they have to
decide.

Tagging is deliberately not gated, so a scan can run unattended and leave its findings for the human
to walk through later. That freedom exists only because tagging changes no content. Adding one word
of "clarification" to the note while you are in there is the thing this rule exists to prevent.

When two of the human's own notes conflict, tag **both**, present both, and pick no winner. There is
no basis on which an agent could choose between two things the same person believed.

## Resolution

Resolution happens only when the human tells you what they decided. When they do, and only then:

- change `status.conflict-review` back to `status.approved`;
- remove `review_note`;
- set `last_reviewed` to today.

Nothing else changes. `last_reviewed` is the only trace the review is allowed to leave. If the human
also wants the note's text changed, that is an ordinary edit to their content and gets its own
approval.

## The approval gate

Clearing a conflict pauses for the human — that is the conflict-resolution gate, and it is the one
place in this procedure where an agent must stop and wait. Adding the flag does not pause; clearing
it always does.

So a scan ends in one of two states, never a third: findings tagged and presented, or nothing found
and said so. It never ends with a conflict quietly resolved. While a file carries
`status.conflict-review` it appears under a review heading in the topic's `index.md`, which is
machine-generated — you never edit it, and it stays correct on its own.

---

*This is a starter draft.* It ships with the implementation so that something sensible happens on
day one, and it is not meant to survive contact with the way you actually work. Rewrite it — with
your agent's help — until it says what you want done, in your words. Ask your agent to adopt this
skill and it will place an editable copy in a `skills/` folder: at the knowledge-base root to change
the procedure everywhere, or inside a single topic to change it for that topic alone. From that
moment your copy replaces this text and later shipped improvements no longer reach it; deleting your
copy brings this one back.
