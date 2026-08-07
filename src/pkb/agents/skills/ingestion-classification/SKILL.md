---
name: ingestion-classification
description: Use when filing inbound content into a topic — a document, an article, a message from the human, or project feedback. Decides reference vs note vs solution, drafts the file with complete metadata, and files it where the classification implies.
---

# Ingestion and classification

Everything that enters a topic is one of three things. Deciding which comes first, because that one
decision settles three others at the same time: where the file goes, what `source_type` it declares,
and which `type.*` tag it carries. Get the classification right and the rest follows; get it wrong
and every part of the file is wrong together.

## The decision

**Is it static knowledge that someone else authored?** — a book, a paper, an article, a manual, a
transcript. That is a **reference**. It goes in `references/`, in its own folder named after the
source, with the main file named after the folder: `references/<source-name>/<source-name>.md`. Any
files that came with it live in the same folder.

**Is it the human's own experience?** — something they observed, concluded, decided, or feel
strongly about. That is a **note**. It goes in `notes/`, one file per note. If it has no media, it
is a single file: `notes/<note-title>.md`. If it carries images, screenshots, or recordings, give it
a folder and use the same naming convention: `notes/<note-title>/<note-title>.md`, with the media
alongside it.

**Is it a note that solves a recurring problem?** — a way of doing something that worked and would
work again. That is a **solution**: still a note, filed the same way, but declared as one so it can
be found as one. It may live under `notes/`, or inside a topic's own extension folder if the human
has approved one for exactly this.

When it is genuinely two things — a reference the human has annotated with what actually happened —
file them as two files and link them. Mixing them makes the human's experience read as if the book
said it.

## The naming convention, once

Any item that gets its own folder names its main file after the folder:
`<item-name>/<item-name>.md`. Never `index.md` for an item's content — that name belongs to the
machine-generated index at the topic root, and a file that takes it will be reported as a mistake.

## Metadata

Every file carries seven required fields (Layer 1 rule VA-4). A file missing any of them will not be
written, so fill them all in the draft rather than after a refusal:

```yaml
---
title: "The title, phrased the way a person would say it"
description: "One line: what is in here and why someone would open it"
topic: "The topic this file belongs to"
tags:
  - topic.<topic>.<narrower-area>
  - type.note
  - status.draft
created: 2026-01-31
updated: 2026-01-31
related_topics: [ other-topic.sub-area ]
source_type: note
---
```

`source_type` is one of `note`, `reference`, `solution`, `summary`, and it must agree with the single
`type.*` tag and with the folder the file sits in — that is the same one decision, written down
three times.

**`description` deserves real attention.** It is extracted verbatim into the topic's index and into
the knowledge base's routing view, so it is what everyone — human and agent — sees before deciding
whether to open the file. One line, never empty, and never a restatement of the title. "Notes on the
grill" is worthless; "why the temperature reading is wrong for the first ten minutes and what to do
about it" is what someone actually needs.

`related_topics` is the **only** thing that creates a cross-topic link. Shared `domain.*` tags,
sitting near each other in the folder tree, and links in the body text all produce nothing. Write
them in the unprefixed dotted form — `equipment.grills`, not `topic.equipment.grills` — and fill it
in whenever a real relationship exists. This is cheap to do while filing and nearly impossible to
reconstruct later.

## Media

Binary files live in a `media/` folder inside the item's own folder. The item's markdown file then
carries a **textual description of every medium it embeds** — what the screenshot shows, what the
diagram means, what was said in the recording. Agents read the text instead of opening binaries, so
an undescribed image is, for most purposes, not in the knowledge base at all.

## One topic, no copies

A solution note lives in exactly one topic: the most relevant one. There are no copies anywhere,
ever. If it seems to belong to two topics, pick the one whose expert would maintain it, and connect
it to the other through `related_topics` and tags. Reach across topics is a routing problem, and
routing is handled for you.

If the content does not fit this topic at all, say so and hand it back rather than filing it
approximately. A misfiled note is harder to find than one that was never filed.

## Filing the human's own words

When the source is something the human said or wrote, the boundary is strict:

- **You may** restructure it, split it into paragraphs, fix grammar, add the metadata, and choose
  the tags.
- **You may not** add facts, remove facts, or change what a fact says — not even one they seem to
  have got wrong. If it looks wrong, say so and ask.

Write the file. Do not paste it into the conversation instead — a write that needs the human's
decision is held before it lands and shown to them as the exact text, so calling the tool *is* how
you show it. Describing the file in chat files nothing and asks no one. That held write is the step
that keeps "the AI curates, the human authors" true in practice.

## Where things land

Anything you authored lands as `status.draft`: it is a proposal until the human has looked at it.
Reference depth files are the exception — they are yours to generate on ingestion, they land
`status.approved`, and there is no per-file dialog for them. The human curates references at the
summary level instead, which is where their judgement actually adds something. Twenty ingested
documents should not produce twenty approval requests.

## The approval gate

This procedure ends at the show-before-write step for anything the human authored, and at whatever
gate the file itself triggers: a new tag pauses, an extension folder that does not exist yet pauses,
a change to an existing file's body pauses, and a change to one of the topic's breadth files pauses.
An ordinary new note, tagged `status.draft`, does not pause — capture is meant to be frictionless.

A topic that rewrites this skill can add to it — its own classification rules, extra fields it wants
in the body, a house convention for reference names. It cannot lower the bar: the same checks run on
the output regardless of which version of this skill produced it.

---

*This is a starter draft.* It ships with the implementation so that something sensible happens on
day one, and it is not meant to survive contact with the way you actually work. Rewrite it — with
your agent's help — until it says what you want done, in your words. Ask your agent to adopt this
skill and it will place an editable copy in a `skills/` folder: at the knowledge-base root to change
the procedure everywhere, or inside a single topic to change it for that topic alone. From that
moment your copy replaces this text and later shipped improvements no longer reach it; deleting your
copy brings this one back.
