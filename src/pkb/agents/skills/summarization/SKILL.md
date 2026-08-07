---
name: summarization
description: Use when drafting or revising a topic's breadth files — topic.md, notes/summary.md, or references/summary.md — or when new material should change one of them. Produces a proposal for human review, never a finished file.
---

# Summarization

A topic has three breadth files, and they are the only compact way in. `topic.md` says what the
topic is and how it is organised. `notes/summary.md` distils the human's own experience into rules
and notable solutions. `references/summary.md` gives the shape of the static knowledge that has been
ingested. Someone should be able to read all three and understand the topic without opening anything
else.

**Length growth is a defect, not progress.** These files manage the *human's* attention. Every
revision distils and replaces; none appends. Keep each one readable in a minute or two. If a breadth
file has turned into a list of everything in the topic, it has stopped doing its job — that is a
signal to propose a split, not to write a longer file. Say plainly when new material does not earn a
place; "nothing here changes the summary" is a good answer.

## The procedure

1. **Read the current file.** Whatever is there, the human approved. Treat it as the baseline, and
   keep the rules they wrote in their words. If new material genuinely contradicts one, that is a
   conflict to surface — not an edit to slip in.
2. **Read what changed** — the notes or references that prompted this revision, in full.
3. **Draft the revision as a whole file.** Propose the complete text, not a patch. The human is
   approving the file they will live with.
4. **Say what changed and why**, in two or three lines, before the draft. Name anything you removed;
   removals are the part a person will want to check.

## Connections, not copies

Every revision suggests connections rather than restating bodies:

- to the references that support or complicate a rule;
- to related topics, so cross-topic work has somewhere to start;
- to the existing notes a new rule generalises.

A breadth file that repeats note bodies has become a second copy of the topic. Point at the depth
material instead. The topic's `index.md` already lists every file with its description and is
machine-generated, so a summary never needs to enumerate anything.

## Each file has its own job

**`topic.md`** carries the topic's description, and that one line is what the knowledge base's
routing view shows for the entire topic. It has to help someone decide whether their question
belongs here. A description that only restates the title tells a reader nothing the folder name did
not already tell them, and it makes the topic effectively invisible when work is being routed.
Rewrite it whenever the topic's real scope drifts.

**`notes/summary.md`** is the highest-priority input for any decision taken from this topic: it is
the human's own experience, distilled. When it and a reference disagree, the human's rule wins, and
the disagreement is something to raise rather than average out. Prefer rules that can be acted on
("preheat longer in wind, the thermometer lies for the first ten minutes") over observations that
cannot.

**`references/summary.md`** describes what the ingested sources cover, where they disagree with each
other, and which one to reach for when. It is a map of the shelf, not a précis of every book.

## The approval gate

Proposing a change to any of these three files pauses for the human. That is the breadth-approval
gate; it fires whether or not this skill ran, and it shows them your proposed text alongside a diff
against what is there now. They can approve it, edit it before it lands, or reject it.

Never treat silence as approval, never write the approved version ahead of the decision to save a
step, and when the human edits your draft, their version is the text — do not re-polish it
afterwards. If they reject it, ask what was wrong before drafting again.

A topic that rewrites this skill can add to this procedure — a required section, a house style, a
tighter length budget. It cannot remove the gate: the human approves their own breadth files
everywhere in the knowledge base.

---

*This is a starter draft.* It ships with the implementation so that something sensible happens on
day one, and it is not meant to survive contact with the way you actually work. Rewrite it — with
your agent's help — until it says what you want done, in your words. Ask your agent to adopt this
skill and it will place an editable copy in a `skills/` folder: at the knowledge-base root to change
the procedure everywhere, or inside a single topic to change it for that topic alone. From that
moment your copy replaces this text and later shipped improvements no longer reach it; deleting your
copy brings this one back.
