# Personal Knowledge Base: Design Specification

---

## Guiding Principles

- A successful AI-driven future depends on human–AI synergy. This synergy needs a clearly defined, active human role.
- Breadth of knowledge drives creativity and novel insights more than depth of knowledge does (David Epstein, *Range*).
- When solving problems, humans go wide; AI goes deep (Marc Andreessen).
- AI agents are tactically brilliant but strategically inept (author).
- Every failure is a learning opportunity. Every success is a template for future action. (AI agent)

---

## System Overview

This project is the **Personal Knowledge Base (PKB)** – a structured, hierarchical repository of knowledge. It
contains static sources and human experience. *(Knowledge + Experience = Wisdom)*

The Knowledge Base is one of two components of the **Personal Companion**, an agentic system that enables a
self-improving AI companion/assistant that learns from its own experiences and from those of its human operators. The
other component, the **Project Manager**, is maintained as a separate project. It is an orchestration engine that
decomposes project objectives into a hierarchy of OKRs and creates specialized agents to execute them.

All interactions with the Knowledge Base are agent-mediated. A root PKB agent – the **Librarian** – routes inbound
information and requests to per-topic **Topic Expert Agents**. The agent layer runs on the **DeepAgent** agentic
harness, which exposes the PKB through a dedicated TUI, Telegram channels, and other access channels.

**Feedback Loop**: Projects use the Personal Knowledge Base to find the best ways to achieve objectives. Project
outcomes and insights feed back into the Knowledge Base.

**This document specifies the design; it is not a manual.** To go from a clone to a knowledge base with something
in it, read [`docs/how-to/getting-started.md`](docs/how-to/getting-started.md) — and then, for the phone,
[`docs/how-to/telegram.md`](docs/how-to/telegram.md).

---

# Part 1: Knowledge Base Design

## 1.1 Goals & Concepts

The Knowledge Base serves five primary goals:

1. **Fuse human experience with static knowledge** to create a richer, more practical body of knowledge.
2. **Optimize agent context windows** based on the agent's role:
    - **Research agents** need a broad, shallow view across many topics and solutions.
    - **Implementation agents** need a deep, focused view of a specific domain.
3. **Make every interaction agent-mediated and frictionless**. Users and external agents work through the Librarian
   and Topic Expert Agents (see Part 2), capturing, retrieving, and refining knowledge in dialog – with no manual file
   management or external tools – over any connected channel (TUI, Telegram, ...).
4. **Enforce common standards while preserving topic depth**. Harness maintenance hooks and shared skills keep
   structure, metadata, tags, and conflict handling identical across topics; each Topic Expert Agent adds unique
   domain knowledge and topic-specific organization on top.
5. **Grow the Knowledge Base by working, as well as by capture**. The human opens a channel on an expert and works
   in it for as long as the work lasts. That work might be research on a goal the topic cannot meet, a discussion, or
   weeks of trying things and reporting how they went, and the human declares none of that in advance. Most of it
   leaves nothing behind. The work that does leave something behind leaves practical knowledge the human earned by
   doing it (Sections 2.7 and 2.8).

## 1.2 Standard Topic Structure

The Knowledge Base is a hierarchical folder tree. Each topic root uses the following structure:

```
[Topic Root]/
├── topic.md            # BREADTH – Human-approved overview and map
├── index.md            # DEPTH – Machine-generated canonical index (incl. tag subtree)
├── references/         # Static knowledge (books, papers, articles)
│   ├── summary.md      # BREADTH – Human-approved overview of all references
│   └── [source-name]/  # One folder per ingested document
│       ├── [source-name].md  # DEPTH – Map of the source: one section per argument
│       └── [source-files]
├── notes/              # Human experience: observations, opinions, and solutions
│   ├── [note-title].md # Note content (standalone or main file in note folder)
│   ├── [note-title]/   # Optional folder for notes with media
│   │   ├── [note-title].md  # The note text
│   │   └── media/      # Images, screenshots, videos, etc.
│   └── summary.md      # BREADTH – Human-approved distilled rules and solutions from all notes
├── (optional) sessions/   # What a session worked out (Section 2.7) – an extension folder
│   └── [goal-title].md    #   the goal, what was asked, the sources kept and rejected, the synthesis
├── (optional) expert.md   # Topic Expert override – defaults to the PKB template (Section 2.3)
├── (optional) skills/     # Topic-specific skill overloads – one folder per skill (Section 2.4)
│   └── [skill-name]/      #   same-name folder overrides the common skill
│       └── SKILL.md
├── (optional) [topic-specific]/ # Human-approved extension folders, e.g., recipes/ for Cooking
└── (optional) sub-topics/ # Deeper nested topics with the same structure
```

**Naming convention for folder-hosted items**: Every item placed inside its own folder uses a main content file named
after the item itself:

- `notes/[note-title]/[note-title].md`
- `references/[source-name]/[source-name].md`

The same convention applies inside `sessions/` and inside topic-specific extension folders (e.g.,
`recipes/[recipe-title].md`, or `recipes/[recipe-title]/[recipe-title].md` with media).

`sessions/` is a topic extension folder, like `recipes/` on Cooking, and the human approves it once: the first summary
a topic files mints the folder, and minting an extension folder is already a decision that waits for the human
(Section 1.9). A topic whose sessions have never produced a summary carries no `sessions/` folder, for the same reason
a topic that derived nothing from a source gets no folder under `references/`: an empty folder claims work that nobody
did. A topic whose sessions only ever produced practical knowledge carries none either, because that knowledge is a
note (Section 2.7).

The folder is named for the conversation rather than for one thing a conversation sometimes does. A session is a long
conversation on one line of work, and it might be research, a discussion, a design argument, or a stretch of trying
things and reporting back. A session that read nothing files no summary of what it read, and the folder name should
stop suggesting that reading is what a session is for.

Do not use generic `index.md` for item content. The topic-level `index.md` remains the machine-generated canonical
directory index.

**One file per source, and it is a map rather than a summary.** `[source-name].md` carries the source's thesis, its
provenance, one section per argument the source actually makes – argument-scoped, not chapter-scoped – and an honest
record of what was not read. The word *summary* names the failure this shape exists to prevent: a confident write-up
of the part that fit in one context window, with nothing anywhere recording that the rest was never opened. A source
may be re-ingested as often as it is worth re-ingesting; each pass reconciles with what is already there and appends
what it covered, what it skipped, and when.

## 1.3 File Types and Creation Rules

| File                              | Built By        | Purpose                                                                                                                              |
|-----------------------------------|-----------------|--------------------------------------------------------------------------------------------------------------------------------------|
| `topic.md`                        | **AI + Human**¹ | Breadth map for research agents. AI drafts and maintains the overview; the human adds insight and approves.                          |
| `index.md` (topic root)           | **Hooks**       | Depth index for precise retrieval, incl. the topic's tag subtree and cross-topic mappings. Regenerated by harness hooks on change.   |
| `expert.md` (optional)            | **Human + AI**  | Topic-specific override of the PKB Topic Expert template (Section 2.3). Human-created; AI assists.                                   |
| `skills/[skill-name]/SKILL.md` (optional) | **Human + AI** | Topic-specific overload of the common skill with the same name (Section 2.4). Human-created; AI assists.                     |
| `references/summary.md`           | **AI + Human**¹ | Breadth overview of static knowledge. AI drafts the summary. Human edits and approves it.                                            |
| `references/[source]/[source].md` | **AI**, then **AI + Human**² | Depth map of one source: thesis, provenance, one section per argument, and what was not read. Generated by the ingestion skill.  |
| `notes/[note-title].md`           | **Human + AI**  | What the human knows from their own practice: an observation, an opinion, or something that worked (tagged `type.solution`). They write it, or they settle it at `/learn` after trying the thing and the expert drafts what they settled (Section 2.7). AI assists with clarity and structure; the human approves the exact text. |
| `notes/summary.md`                | **AI + Human**¹ | Breadth overview of experience: distilled rules and notable solutions. Human edits and approves. **Highest priority for decisions.** |
| `sessions/[goal-title].md`        | **AI + Human**³ | A session's synthesis of what it **read and worked out** (Section 2.7): the goal, the questions asked, every source kept, every source rejected and why. Rendered by the harness from the session record and approved by the human before it lands. |
| `tags.md` (PKB root)              | **Hooks**       | Global tag registry, purely derived from file frontmatter. Regenerated mechanically whenever files change.                           |
| `index.md` (PKB root)             | **Hooks**       | Root catalog: every topic with its description, aggregated from `topic.md` frontmatter – the Librarian's routing view.               |
| `skills/[skill-name]/SKILL.md` (PKB root) | **Human + AI** | An adopted common skill – judgment maintenance or collaboration (`voice/`, `discovery/`, ...). Present only once the human adopts it (Section 2.4). |

¹ **"AI + Human"** means the AI proposes a draft and the human approves or edits it before finalization.

² The **first** write of a source file is un-gated – capture stays frictionless. A **re-ingestion that rewrites one**
is approved by the human first: the rewrite lands on top of an extraction they have already read and relied on.

³ The **session record** is a file in the session's own workspace, outside the Knowledge Base tree, and it survives
a restart of the daemon. A channel that runs for months compacts its own early trials out of the
conversation, so the expert reads the file rather than the transcript. The Knowledge Base gets a file only when the
human approves one. Every write under `sessions/` waits for the human, first file and every later one, and the human
approves the exact text that will land rather than an intention to write one (Section 2.7).

**Collaboration rule**: Notes, skills, and `expert.md` overrides are **human-generated, AI-curated** – they carry
the human's experience and ways of working; the AI assists with clarity, grammar, and structure. Every other
meaning-carrying file (`topic.md`, the breadth summaries, a session's summary) is **AI-generated, human-curated** –
the AI drafts, the human adds insight and approves. Reference depth files are AI-generated on first ingestion; the
human curates them at the summary level, and approves any later pass that rewrites one. Mechanical files (`index.md`,
root `tags.md`) are generated by hooks and curated by no one.

A note settled at `/learn` stays on the human-generated side of that line, and the reason is worth stating. The
experience in it is theirs: they cooked it, they ran it, they came back and said what happened. The expert drafts the
wording from the session record, argues with the human about what it means (Section 2.7), and files the text they
approve word for word. The expert argues about what the experience means. It never argues about whose it was.

**Skill files are a file class of their own.** Everything under a `skills/` folder – at the PKB root or inside a
topic – is instruction for agents, not knowledge about a subject. It follows the harness's own skill format and is
exempt from the PKB rules that govern knowledge files: no PKB frontmatter, no place in any `index.md`, no
contribution to the tag registry (Section 1.4). Forcing the PKB fields onto a `SKILL.md` breaks the harness's parser
and the skill silently disappears.

## 1.4 Metadata Requirements

Every human- or AI-authored markdown file **that carries knowledge** includes YAML frontmatter. There are three file
classes and only the first is governed by this section:

1. **Knowledge files** – notes, references, the breadth summaries, `topic.md`, session summaries, and anything in a
   topic-specific extension folder. Full PKB frontmatter, as below.
2. **Machine-generated files** – `index.md` at any level and the root `tags.md`. Minimal generated frontmatter only.
3. **Skill files** – `skills/[skill-name]/SKILL.md`, at the PKB root or inside a topic, plus `expert.md`. These are
   agent instructions, not knowledge: neither appears in any `index.md` and neither contributes tags. A `SKILL.md`
   carries the harness's own two fields, `name` and `description`, and nothing else – adding the PKB fields to one
   breaks the harness's parser, and the skill is then dropped without an error anywhere.

```yaml
---
title: "Grill Performance in Windy Conditions"
description: "How wind affects grill temperature and how to compensate for it"
topic: "Cooking"
tags:
  - topic.cooking.grilling
  - topic.cooking.heat-management
  - type.note
  - status.approved
created: 2024-10-15
updated: 2024-10-16
related_topics: [ bbq, weather ]
source_type: note  # note, reference, solution, summary
---
```

The `description` field is required on every **knowledge** file – class 1 above. It is what deterministic `index.md`
generation extracts (Section 1.9). The generated files carry their own minimal frontmatter instead: the root
`tags.md` in Section 1.5 has a `title` and a `source_type` and no description, and asking it for one would be asking
the generators to fail their own validation.

`related_topics` lists related topic paths in tag notation (e.g., `bbq.equipment`). It is the single place where
cross-topic relationships are declared – harness hooks aggregate these declarations into the registry's cross-topic
mappings (Section 1.9).

Conflict handling adds transient fields: `review_note` while a conflict is open, `last_reviewed` after resolution
(Section 1.7).

`provenance` is an optional field that says **how the knowledge was acquired**, and it takes one of four values:

| Value | What it means |
|-------|---------------|
| `practised` | The human did the thing and this is what happened. A lesson settled at `/learn` carries it (Section 2.7). |
| `stated` | The human said it, without having tried it yet. |
| `researched` | A session read it and worked it out, weighed against the human's own notes (Section 2.7). |
| `ingested` | It came in through a source: a book, an article, a paper. |

Nothing else in the frontmatter can answer that question. The `type.*` tag restates the folder (Section 1.5), so a
finding taken off the internet and filed under `notes/` looks exactly like experience the human earned. An absent
`provenance` means unknown, and nothing guesses one for the files already in the tree. It records a route, not a
score: the human decides which routes outrank which, and Section 1.7 is where that order is written down. A session
record always carries `provenance: researched` and a note settled at `/learn` always carries `provenance: practised`.
The harness stamps both, because the harness renders both files rather than typing them, and validation refuses a
session summary that arrives without one. No such check lands on `notes/`: every note already in the tree carries no
`provenance`, so a presence rule there would turn the human's existing notes into a wall of errors, and nothing
guesses a value for them. Those two values carry the whole distinction between what a session read and what the human
did, and Section 2.7 is the section that turns on it.

## 1.5 Hierarchical Tags

### Why use hierarchical tags?

Hierarchical tags improve context filtering, inheritance, and agent retrieval. A nested tag makes relationships
explicit. Agents can filter at any level of the tree.

### Tag syntax

Use dot notation. Each dot adds a level.

Examples:

- `topic.cooking.grilling`
- `topic.cooking.heat-management`
- `status.conflict-review`
- `domain.legal.compliance`
- `type.note`

### Tag namespaces

| Namespace  | Purpose                                | Example                                                                                                  |
|------------|----------------------------------------|----------------------------------------------------------------------------------------------------------|
| `topic.*`  | Subject area, matches folder structure | `topic.cooking.grilling`                                                                                 |
| `status.*` | Workflow state                         | `status.draft` (proposed, awaiting approval), `status.approved`, `status.conflict-review`                |
| `type.*`   | Source type                            | `type.note`, `type.reference`, `type.solution`, `type.summary`                                           |
| `domain.*` | Cross-topic functional domain          | `domain.marketing.ads`, `domain.legal.compliance`                                                        |

### Tag rules

- Start tags with a broad namespace. Add narrower terms as needed.
- Do not create ad-hoc tags. The AI proposes new tags. The human approves them.
- Keep tag depth to 4 levels or fewer.
- A nested tag implies its parent. `topic.cooking.grilling` also means `topic.cooking`.
- The AI uses tags to assemble context packs. It filters by namespace and depth.
- Sessions add no namespace (Section 2.7). A lesson the human earned files as `type.solution`, a session summary files
  as `type.summary`, and `provenance` carries the difference between the two (Section 1.4).

### Tag registry (`tags.md` at the PKB root)

Because tags are flexible and relational, the PKB maintains a single **`tags.md`** registry at its root – the
canonical relational tree and lightweight ontology for AI ingest: namespace definitions, per-topic subtrees, and
cross-topic mappings. Each topic's `index.md` embeds its own subtree for local depth work.

**Maintenance is fully mechanical**: harness code regenerates the registry by scanning all files and rendering the
full hierarchy, and aggregates the cross-topic mappings from `related_topics` declarations in file frontmatter –
simple deterministic code, no LLM tokens spent. Definitions for the standard namespaces (`type.*`, `status.*`) are
static text supplied by the generator. The registry is purely derived: by construction it is always an accurate
reflection of the tags actually used in the files. Governance stays in the dialog: a Topic Expert proposes a
new tag and the human approves it before the expert files content that uses it.

**Example root `tags.md` (excerpt showing the Cooking subtree)**:

```markdown
---
title: "PKB Tag Registry"
source_type: tag-registry
---

# PKB Tag Registry

## Namespace: topic.cooking

- `topic.cooking` – root topic
    - `topic.cooking.baking`
    - `topic.cooking.grilling`
        - `topic.cooking.grilling.charcoal`
        - `topic.cooking.grilling.gas`
    - `topic.cooking.heat-management`
    - `topic.cooking.recipes` *(topic-specific extension)*

## Namespace: type

- `type.note` – human-written note
- `type.reference` – static source
- `type.solution` – reusable solution (a note tagged as a solution)
- `type.summary` – breadth overview

## Namespace: status

- `status.draft` – proposed, awaiting human approval
- `status.approved`
- `status.conflict-review`

## Namespace: domain

- `domain.engineering`
    - `domain.engineering.security`
- `domain.legal`
    - `domain.legal.compliance`
- `domain.marketing`
    - `domain.marketing.ads`

## Cross-topic mappings (aggregated from `related_topics`)

- `topic.cooking.grilling` ↔ `topic.bbq.equipment`
- `topic.cooking.heat-management` ↔ `topic.physics.thermodynamics`
```

Every namespace renders the same way: a nested tag implies its parent, so `domain.*` is a tree exactly as `topic.*`
is. Sibling order is not editorial – the generator sorts siblings case-insensitively by the full tag string, which is
what makes regeneration idempotent, so an example written in any other order will not be reproduced.

## 1.6 Human–AI Collaboration in the Knowledge Base

The Knowledge Base is a dialog between human and AI. The division of labor is simple: **notes, skills, and
`expert.md` are mostly human-generated and AI-curated; everything else is AI-generated and human-curated** (purely
mechanical files are generated by hooks and curated by no one).

### Notes

- The human writes the first draft of each note.
- The AI assists with clarity, grammar, and structure.
- The human approves the final content.
- The AI does not change the factual content of a note without human approval.

### Summaries

- The AI proposes drafts for `references/summary.md` and `notes/summary.md`.
- The human reviews the draft. The human adds insight, removes errors, and decides what rules to keep.
- The AI helps the human incorporate new insight with the existing depth of knowledge. It suggests connections to
  references, related topics, and existing notes.
- The human approves the final summary.

Keeping `topic.md` and the two summaries as separate small files is deliberate: each is a compact approval surface.
Breadth files manage the *human's* context window, just as `index.md` manages the agents'.

### Sessions

- The human opens a channel and works in it (Section 2.7). A session is a long conversation on one line of work, and
  the work decides what it turns into: research, a discussion, a design argument, or weeks of trying things and
  reporting back.
- The session record is a file in the session's own workspace rather than in the tree, so a session that stops
  halfway keeps what it learned and writes nothing.
- The expert argues throughout, and it argues about the human's own conclusions as readily as about a page it found.
  Objecting while the human can still act on it is the job; a retrospective is too late.
- `/learn` proposes what was learned, in an ordinary message the human can edit by talking back. The harness then
  renders the file and the human approves the exact text before it lands, under `notes/` or under `sessions/`.
  Section 2.8 says what a session is allowed to conclude and what it may never conclude.
- The AI files an article the session found only after the human names that article as one they want.
- Most sessions close with nothing filed, and nobody treats that as a failure.

### Agent roles in the dialog

The topic's Topic Expert Agent (see Part 2) facilitates this dialog with the human. Using the common judgment skills
(Section 1.9) it proposes drafts and detects conflicts, while harness maintenance hooks keep the structure consistent.
Collaboration skills (Section 2.4) shape the dialog itself: drafts are written in the human's `voice` skill, and idea
discovery sessions follow the `discovery` skill. Work in a channel follows Section 2.7. The expert never finalizes
human-approved content on its own.

## 1.7 Conflict Detection & Resolution

### General rule

Human content always wins over static knowledge.

Human content includes human-written notes and human-approved summaries. If a human note conflicts with a static
reference, the note is correct. If it is not correct, the human edits the note until it wins.

A page found on the internet ranks below both, and it ranks there until the human accepts it. Accepting it makes it a
static reference, and it then ranks as one (Section 2.7).

The system does not overwrite human content. The system only brings conflicts to human attention and tracks resolution.

One case has no human side, and the rule above does not decide it: a second reading of a source producing an argument
that contradicts the one already in that source's file. Both sides are extractions. The machinery is the same – flag,
change nothing, let the human settle it – but what gets flagged is a reference (see *Conflict tagging* below).

### Conflict types

| Type            | Description                                             | Example                                            |
|-----------------|---------------------------------------------------------|----------------------------------------------------|
| `contradiction` | Statements directly oppose each other                   | "Preheat grill 15 min" vs. "Preheat grill 10 min"  |
| `nuance`        | Statements are both true but under different conditions | "High heat for searing" vs. "Low heat for smoking" |
| `outdated`      | Static knowledge is older and no longer accurate        | 2010 book vs. 2024 human note                      |

### Detection process

1. **Trigger**: A harness maintenance hook schedules a conflict scan whenever files under `notes/`, `references/`,
   `sessions/`, or a topic-specific extension folder are created **or modified** – an edited note can newly
   contradict a reference that was fine yesterday. A session summary enters the scan like any other knowledge file
   once it lands, and a session in progress writes nothing for the scan to find (Section 2.7). The human can also
   request a scan on demand.
2. **Method**: The Topic Expert Agent executes the scan. It compares `notes/summary.md` against
   `references/summary.md`, individual notes against references, and notes against notes. On a re-ingestion it also
   compares the fresh extraction of a source against the source file already on disk – argument by argument, not
   document against document, because a bounded reader handed two long documents will answer confidently about the
   part it managed to read. It uses semantic analysis informed by its domain knowledge (e.g., recognizing when two
   statements are both true under different conditions).
3. **Classification**: The AI proposes a conflict type and a confidence score.

### Conflict tagging

When the AI detects a conflict with human content, it must do these steps:

1. Add the tag `status.conflict-review` to the human content file.
2. Add a short `review_note` to the file metadata. The note describes the conflict.
3. Do not change the file content automatically.

The reference is neither tagged nor edited – except in the one case where the conflict is between two readings of the
same source. There the same three steps apply to the **source file**: tag it `status.conflict-review`, add the
one-line `review_note`, change nothing. The human settles which reading is right, exactly as they would between a
note and a reference.

Example metadata for a note with a pending conflict:

```yaml
---
title: "Preheat the grill"
description: "How long to preheat the grill before cooking"
topic: "Cooking"
tags:
  - topic.cooking.grilling
  - topic.cooking.heat-management
  - type.note
  - status.conflict-review
created: 2024-10-15
updated: 2024-12-16
related_topics: [ bbq.equipment ]
source_type: note
review_note: "Reference 'Grill Basics' says preheat for 10 min. Note says 15 min."
---
```

### Human review

A human must review the tagged file. Resolution has two steps:

1. **Decide the content**: edit the file so it becomes the winning version, or keep it unchanged if it is already
   correct.
2. **Clear the tag**: change `status.conflict-review` back to `status.approved` and remove the `review_note`. The
   `last_reviewed` date is the only trace of the review.

Example metadata after resolution:

```yaml
---
title: "Preheat the grill"
description: "How long to preheat the grill before cooking"
topic: "Cooking"
tags:
  - topic.cooking.grilling
  - topic.cooking.heat-management
  - type.note
  - status.approved
created: 2024-10-15
updated: 2024-12-17
related_topics: [ bbq.equipment ]
source_type: note
last_reviewed: 2024-12-17
---
```

### Handling conflicting human notes

When two human notes conflict, the Topic Expert Agent does these steps:

1. Detect the conflict.
2. Add the tag `status.conflict-review` and a `review_note` to each note.
3. Present both notes to the human for review.
4. The human edits one or both notes to make the winning version.
5. The human changes the tag back to `status.approved` and removes the `review_note` from each note.

### Conflicts found during a session

A round of research compares what it found on the internet against the topic's notes (Section 2.7). A page that
contradicts a note **raises the pair and changes nothing**: the expert shows the human both quotes side by side, and
the note is left byte for byte as it was. No tag, no `review_note`, no reordering. The three steps above start only
once the human accepts the source and it is filed as a reference, at which point the ordinary scan runs on it like any
other.

The same machinery runs against a lesson the human dictates at `/learn`, and it is the same mechanism rather than a
second one. Told to file a conclusion the topic's own notes disagree with, the expert raises the pair, quotes both
sides, and changes nothing until the human says which one stands (Section 2.7).

Two properties of models make this the only safe handling.

- **A model handed retrieved text abandons its own correct answer for it.** Measured across six domains and six
  models, the rate at which a model drops a correct answer in favour of wrong retrieved text runs from 0.16 to 0.31.
  A page with no standing therefore never marks the human's own note as under review. Letting it hands that bias
  write access to a tree with no undo.
- **A model given two passages that contradict each other misses the contradiction.** Handed both sides of a
  contradiction human annotators had already marked, models score under 11%. So the model never decides which pairs
  the human sees: the harness picks the pairs by claim-to-claim overlap, the model only labels each one, and every
  pair it picked is shown whatever the label says. The expert names how many pairs the overlap test rejected when it
  reports back, because that number is the part a reader would otherwise assume was zero.

### What the system does not do

- The system does not create a separate conflict registry.
- The system does not record past conflict details after resolution.
- The system does not mark any note as a loser.
- The system does not store resolution text outside the note.
- The system does not keep any marker that a conflict ever occurred – resolved notes are simply `status.approved`
  with a fresh `last_reviewed` date.

### Benefits

- Conflicts stay with the note. They do not pollute the context window after resolution.
- The true state of knowledge is the note content itself.
- The logic is simple: detect, tag, review, resolve.

## 1.8 Critical Rules

1. **Human content wins**: Human-written notes and human-approved summaries always take precedence over static
   references. The AI detects conflicts and tags them with `status.conflict-review`. The human resolves the tag. The AI
   does not change human content automatically.

2. **Breadth vs. Depth**: `summary.md` files and `topic.md` are used for breadth-first research. `index.md` files are
   used for depth-first implementation. Topic Expert Agents assemble context packs accordingly for consumers such as
   the Project Manager (separate project).

3. **Machine vs. Human**: `index.md` files are always machine-built. `summary.md` files and `topic.md` require human
   collaboration. The AI never finalizes them without human approval.

4. **Cross-Topic Solutions**: A solution note lives in exactly one topic – the most relevant one. There are no copies
   of it. Cross-topic discovery is handled by tags, `related_topics` metadata, and Librarian routing.

   This rule is about **solution notes**, and only about them. It does not govern the ingestion of sources: one
   book, paper, article, or clip may be ingested by several Topic Experts, each extracting what its own topic cares
   about (Section 2.2). Those are different extractions of one source, not copies of one file.

   The source material itself is copied on purpose. A topic that **gainfully** ingests a source – meaning it derived
   at least one insight from it – gets its own copy of the original alongside its own extraction, so the topic folder
   stays self-contained and portable; storing a large file more than once is the price, and it is worth paying. A
   topic that derives nothing gets no folder, no stub, and no copy: zero trace, rather than an empty folder implying
   the source was considered and is somehow relevant.

5. **Sub-Topics**: Deeper nested topics follow the same structure recursively. A sub-topic is served by its parent
   topic's Topic Expert unless it has its own `expert.md` – the same resolution pattern as the template override.

6. **Media Handling**: Notes with media use a dedicated folder. The `[note-title].md` inside contains the note text (or
   a machine-extracted textual description of embedded media). Agents read the text instead of parsing binary files.

7. **Tag Discipline**: Use hierarchical tags. The root `tags.md` registry is maintained mechanically by harness
   hooks. Propose new tags to the human. Do not create ad-hoc tags.

8. **Nothing off the internet becomes a note**: `notes/` holds what the human proved in practice, and the `type.*`
   tag restates the path rather than adding to it (Section 1.5). A finding taken off the internet and filed under
   `notes/` is then indistinguishable from experience the human earned. A session files an accepted article under
   `references/` and its own synthesis under `sessions/`, stamped `provenance: researched`
   (Sections 1.4 and 2.7). The rule holds on ordinary turns too: an expert that reached a tool outside the Knowledge
   Base on a turn cannot write a note on that turn, and is told to open a channel instead.

   A session **may** file under `notes/` the thing the human went and tried: they cooked it, they ran it, they came
   back and said what happened. That lesson is settled at `/learn`, drafted by the expert from the
   session's own record of the trials, approved word for word by the human, tagged `type.solution` and stamped
   `provenance: practised` (Section 2.7). The line this rule draws is between read and done, and `provenance` is
   where the tree records which side a file is on.

## 1.9 Topic Maintenance Model

> **Design principle**: *Enforce structure mechanically, curate meaning agentically.* There is no separate maintainer
> agent. Deterministic maintenance is performed by harness hooks that cannot be skipped or forgotten; judgment work is
> performed by the topic's Topic Expert Agent (see Part 2) through common, overloadable skills.

Maintenance is split across three tiers.

### Tier 1: Mechanical enforcement (harness hooks)

The DeepAgent harness performs this work itself. No agent judgment is involved, and no agent can skip it. It runs on
two clocks, and the difference matters: **validation fires per write**, as a gate in front of the write itself, so a
file that would break the standards never lands; **everything derived fires once per agent run**, after the turn, over
the files that turn touched. Regenerating per write is forbidden – it would rewrite the root `tags.md` several times
in a single turn.

Per write:

- Validate YAML frontmatter (required fields, tag syntax and depth), file naming conventions, and consistency between
  declared metadata (`topic`, `source_type`, `topic.*`/`type.*` tags) and the file's actual location. Skill files are
  the third file class (Section 1.4): they are checked for placement only, never for PKB frontmatter, and they are
  excluded from index and tag generation everywhere below.

Once per agent run, over the files the turn created, changed, renamed, or removed:

- Update `updated` timestamps.
- Regenerate the topic's `index.md` – including its tag subtree and cross-topic mappings. Because every knowledge file
  carries a `description` in its frontmatter (Section 1.4), index generation is fully deterministic: walk the tree,
  extract the frontmatter.
- Regenerate the root `tags.md` registry from the tags actually used in files, aggregating cross-topic mappings from
  `related_topics` declarations – plain deterministic code, no LLM tokens spent, purely derived.
- Regenerate the root `index.md` – a catalog of every topic and its `topic.md` description – the Librarian's
  one-file routing view.
- Flag broken links and orphaned files.
- Schedule a conflict scan covering the changed files.

Scaffolding the standard structure (Section 1.2) for a new topic or sub-topic is mechanical in the same way, and
happens on human approval rather than on either clock.

> **Implementation note**: "Schedule a conflict scan" means Tier 1 only *queues* the work – the scan itself is
> executed by the Topic Expert Agent (Tier 2). The DeepAgent harness therefore needs a lightweight queue or trigger
> mechanism that hands scheduled scans to the topic's expert (e.g., on its next activation or on a timer).

### Tier 2: Common judgment skills (overloadable)

Work that requires understanding content is defined once, as common skills loaded by every Topic Expert Agent:

- **Summarization** – Draft all three breadth files – `topic.md`, `references/summary.md`, and `notes/summary.md` –
  following the dialog rules in Section 1.6. `topic.md`'s `description` is what the root catalog is built from, and
  therefore what the Librarian routes on.
- **Conflict detection and classification** – Execute the scans scheduled by Tier 1 and classify findings per
  Section 1.7.
- **Tag proposal** – Propose new hierarchical tags for human approval before filing content that uses them; the
  registry picks them up mechanically once used.
- **Ingestion classification** – Classify inbound content as a reference or a note (observation, opinion, or a
  solution tagged `type.solution`); draft the files with metadata, including the `description` that Tier 1 relies
  on and the textual descriptions of embedded media required by rule 6 in Section 1.8. A source too large to read in
  one turn is handed to the chunked ingestion loop (Section 2.3) rather than drafted in place.
- **Source extraction, one skill per kind of source** – The skeleton an extraction follows, which is what makes it
  useful rather than a paraphrase: a **paper** (question · method · results · limitations · *does this apply to me*),
  a **book** (thesis, then one section per argument it actually makes), an **article, post, or clip** (the single
  claim and the evidence offered for it), a **manual or reference work** (the parts the topic will actually consult –
  a manual is looked things up in, not read).
- **Research planning and synthesis** – Turn the human's goal into the questions a search will ask, brief one search
  sub-agent per question, weigh what returns against the topic's notes and references, and draft the session summary
  and its synthesis (Section 2.7). The questions carry the goal and none of the human's beliefs; the notes come back
  at the weighing step.
- **Lesson proposal** – At `/learn`, read the session's own record of what the human tried and draft what was learned
  and what is worth filing (Sections 1.7 and 2.7). The drafting is the skill's work. Picking the pairs that lesson has
  to answer for is harness code, per Section 1.7, and stays code whether the expert wrote the lesson or the human
  dictated it: a skill is a file the human may adopt and then edit, and a guarantee that lives in an adopted copy is a
  guarantee that leaves the day they edit it.
- **Sub-topic proposals** – Propose splitting a topic that has grown too large.

A Topic Expert Agent may **overload** a common skill with a human-created, AI-assisted topic-specific version. For
example, the Cooking expert's summarization skill may require temperature and doneness tables in recipe summaries.
An overload extends the common procedure but never weakens the general standards: Tier 1 validates the output
regardless of which skill version produced it. The same mechanism extends to the collaboration skills (voice,
discovery, research) defined in Section 2.4.

### Tier 3: Topic Expert dialog

The Topic Expert Agent runs the judgment skills in dialog with the human: proposing drafts, presenting conflicts, and
collecting approvals (Sections 1.6 and 2.3). It authors content so that Tier 1 stays deterministic – for example, it
writes the `description` frontmatter when filing new content.

Cross-topic mappings are not curated by anyone: Tier 1 aggregates them mechanically from `related_topics`
declarations into the root `tags.md`. The **Librarian** (Section 2.2) consults them when routing across topics.

### Topic creation

When a human requests a new topic (directly, or by approving a topic proposed by the Librarian – see Section 2.2):

1. Tier 1 scaffolds the standard structure from Section 1.2, with placeholder `summary.md` files.
2. A Topic Expert Agent is instantiated for the topic.
3. The expert drafts `topic.md`, proposes the topic's initial tag subtree, and asks the human for approval.
4. Topic-specific folders (e.g., `recipes/` for Cooking) are added with human approval; skill overloads are created
   by the human with AI assistance.

---

# Part 2: PKB Agent Architecture

## 2.1 Agent-Mediated Access

All interactions with the Knowledge Base go through AI agents. Humans and external agents (e.g., project agents) do
not read or write topic files directly. The agent layer and the harness's mechanical maintenance hooks (Section 1.9)
enforce the standards defined in Part 1 – structure, metadata, tags, conflict tagging, and human approval – no matter
which channel a request arrives from.

Where Part 1 says the human writes, edits, or re-tags a file, that action also happens through this dialog: the human
decides, and the agent applies the change on the human's behalf.

## 2.2 The Librarian (Root PKB Agent)

The **Librarian** is the root agent of the Knowledge Base and the default entry point for all inbound information and
requests.

### Routing is a workflow, not a decision

A Librarian turn is four steps. Only the first is a judgment call; the other three are harness code that always runs.

1. **Classify.** Reading the generated root catalog, the Librarian decides which topics the inbound item concerns. It
   answers with a routing call naming the applicable topics and a one-line reason – not with prose. This is the only
   step where a model has discretion.
2. **Fan out.** The harness invokes every applicable Topic Expert. This is not something the Librarian may decide to
   skip; it is a step that runs.
3. **Merge by attribution.** The harness composes one reply from what the experts actually returned: each expert's
   own answer, under its own heading, named by its title and its agent id. This is deterministic code, never a second
   model writing a summary of the first. A model asked to write the merge will happily report that *"the Cooking
   expert checked the knowledge base"* when no expert ever ran; a reply assembled from real results cannot say that.
4. **Offer the experts directly.** The reply names the agents that answered, so the human can carry on with one of
   them – "continue with the Cooking expert" – instead of going back through the Librarian each time.

The step that used to be missing is step 2. When the Librarian was free to decide whether to delegate, it sometimes
read the topic folders itself and answered from raw files: no topic skills, no `expert.md` persona, no per-topic
voice. Everything that makes a Topic Expert an expert lives one layer down, so routing around it loses all of it.
Making the fan-out harness code rather than a model's choice is what closes that.

### When classification is uncertain, ask – with a menu

If the Librarian cannot classify an item confidently, the harness asks the human **which experts to engage**, listing
the candidates. Not an open question, and never a guess: filing knowledge in the wrong place is not undoable.

The menu appears when the Librarian answers in prose instead of routing (after one stricter retry), when it names no
topic for an item that plainly concerns existing knowledge, or when it says it is unsure. "None of these" is always
an option, and it leads to the topic-gap flow below.

### One source, several experts

Fan-out is not only for questions. **Information fans out the same way, and several experts ingesting one source is
not duplication.** A management book can carry lessons on management *and* on parenting; routed to both, it yields a
reference under Management about leading teams and a reference under Parenting about raising children. Two
extractions of one source, each written through its topic's lens – which is exactly what makes them different, and
exactly what a Librarian that answered from raw files could never produce.

Each expert decides for itself whether the material has anything for it, and **may decline**. Material that reaches
an expert with nothing in it for that topic, and is not filed, is a correct outcome. A fan-out where two of four
experts file and two decline is a success, not a partial failure.

This is not in tension with rule 4 in Section 1.8, which is about a *solution note* living in exactly one topic.

Responsibilities:

- **Routing** – Classify each inbound request or piece of information and fan it out to every applicable Topic Expert
  Agent, then merge what they return into one attributed answer.
- **Topic catalog** – Classify using the root `index.md`: a hook-generated catalog of every topic and its
  description, aggregated from `topic.md` frontmatter. A topic that owns an `expert.md` is marked in the catalog
  itself with *(custom expert)*, so the Librarian sees it in the one file it already reads and never walks the tree
  for it. Nothing is maintained by hand.
- **Topic gaps** – When inbound information fits no existing topic, propose a new topic to the human (following the
  topic creation flow in Section 1.9). When there is nothing applicable *and* nothing worth choosing between, that is
  the gap flow rather than a menu.
- **Cross-topic coordination** – Use the cross-topic mappings in the root `tags.md` (aggregated mechanically from
  `related_topics` declarations) to notice the second topic worth involving.
- **Work that crosses topics** – Complicated work opens its channel on the Librarian: personal finance and investment
  cross portfolio management and trading, and neither expert holds the whole of it. A goal fans out like any other
  inbound item, each applicable expert works it under its own skills and voice, and the harness merges what they
  return by attribution. `/learn` fans out the same way, with the session itself as the source, and each expert files
  inside its own topic or files nothing (Section 2.7).

The Librarian holds no deep topic knowledge itself, writes nothing into the Knowledge Base, and never answers a
subject question out of its own head. It goes wide; Topic Expert Agents go deep. It also holds no topic's tools:
a topic's search sub-agents and its domain tool servers belong to that topic's expert, and the Librarian reaches
them by fanning out to the expert that owns them.

## 2.3 Topic Expert Agents

Each topic is run by a **Topic Expert Agent**. There is one default **Topic Expert template** for the whole PKB. A
topic that needs behaviour beyond skill overloads may override the template by placing an `expert.md` agent file in
its topic root. The harness resolves this mechanically, following the same pattern as maintenance skills: if
`[Topic Root]/expert.md` exists, use it; otherwise instantiate the PKB template with the topic's context
(`topic.md`, `index.md`, the common skills, and any skill overloads). The same resolution applies recursively: a
sub-topic without its own `expert.md` is served by its parent topic's expert.

The expert combines two layers of capability:

1. **PKB general standards (common layer)** – The standard structure, metadata, tag, summary, and conflict rules
   defined in Part 1. Their deterministic parts are enforced mechanically by harness maintenance hooks; their
   judgment parts run as common, overloadable skills (Sections 1.9 and 2.4).
2. **Unique topic knowledge (expert layer)** – Domain knowledge about the topic itself, its file organization beyond
   the common standard (e.g., a `recipes/` folder for Cooking), and the best ways to interact with its content: how
   to query it, which files answer which kinds of questions, and topic-specific ingestion rules.

Responsibilities:

- Answer questions about the topic, using breadth files (`topic.md`, `summary.md`) or depth files (`index.md`,
  detailed sources) as the request requires.
- Ingest information routed by the Librarian: classify it as a reference or a note (tagging solutions
  `type.solution`); draft the files; apply metadata and tags per the standards. Ingest it **through the lens of this
  topic** – the same source reaching two experts should produce two different extractions – and **decline** when the
  material holds nothing this topic cares about, rather than filing something to look useful.
- Work a channel with the human for as long as the work lasts (Section 2.7): research a goal the topic cannot meet,
  brief read-only search sub-agents, weigh what they bring back against the topic's notes, object while the human can
  still act on it, take their results back as the trials come in, and propose at `/learn` what was learned.
- Carry out the judgment side of topic maintenance (Section 1.9); the mechanical side is enforced by harness hooks.
- Manage topic-specific extensions (with human approval).
- Escalate to the human as required by Part 1: summary approval, new tags, and conflict resolution.

**A source too large for one turn is ingested as a loop, not as a turn.** Classify, draft, file works for a link and
fails for a book: what does not fit the context window is not read, and a single turn will write a confident account
of the part it saw with nothing recording that the rest was never opened. So the harness drives the reading rather
than the model – segment the source, extract argument by argument through a bounded window, write each section as it
goes rather than all at the end, record what was skipped and why – and it survives a run that dies part way through a
300-page book instead of starting over. The expert stays the author of the extraction; it no longer gets to decide
when it is finished. A source arrives as a path: anything binary is extracted to text first, and both are kept.

### Example: a Cooking Topic Expert in action

A user connects directly to the **Cooking** Topic Expert Agent. The user needs no external tools – the agent handles
retrieval, dialog, and filing end to end:

- **Ingest from the web**: The user asks for a steak grilling recipe. The agent fetches candidates from the internet,
  works with the user to adjust the rub and target doneness, and files the final version under `notes/` (or the
  topic-specific `recipes/` folder) with proper metadata and tags.
- **Capture experience**: The user gives feedback after cooking ("the grill behaves differently in windy weather").
  The agent files it as a note and proposes a regenerated `notes/summary.md` for human approval.
- **Combine reference and experience**: The user asks for a grilling recipe from an ingested reference cookbook. The
  agent pulls it from `references/` and applies the temperature specifics of the user's own gas grill from notes
  filed earlier – human experience refining static knowledge.
- **Ingest through its own lens**: The Librarian fans a food-science book out to Cooking and to Health. Cooking files
  what it says about heat, protein, and technique; Health files what it says about nutrition. Neither is a copy of
  the other, and neither expert is filing the parts it has no use for.
- **Research a goal the topic cannot meet**: The user asks how long to dry-brine a brisket, and the topic holds no
  reference on it. The agent says so, sends three search sub-agents out with one question each, verifies the pages
  they cite, flags the two results that contradict the user's own note about their grill, and offers one article for
  ingestion alongside what it found. The channel stays open, because the user has not cooked anything yet
  (Section 2.7).
- **Work one goal over weeks**: The user opens a channel called `Cooking · Brisket Rub` and works the loop in it.
  They cook three times and report back after each. The expert holds the trials in the session record,
  contradicts them when their week-three conclusion disagrees with their own week-one report, and at `/learn`
  proposes one note: the rub, the temperature, and what failed. Their other channel, `Cooking · Sourdough Starter`,
  is untouched by any of it.
- **Leave nothing behind**: The user asks for a weeknight pasta, gets one, cooks it, and closes the channel without
  filing. No note, no record, no folder. This is the ordinary outcome of a session, and the closed Telegram topic
  still holds the recipe if they want it again.

## 2.4 Common Skills and Skill Overloading

The common skills are loaded by every Topic Expert Agent. They ship with the implementation and are mounted read-only
ahead of the PKB root's own `skills/` folder, which stays empty until the human adopts one (Part 3). Each skill is a
folder holding a `SKILL.md` – `skills/voice/SKILL.md`, `skills/discovery/SKILL.md` – which is the harness's own
format, and what buys progressive disclosure (only the skill's name and description sit in the prompt; the body is
opened when it is needed) and name-collision override resolution without any code of ours. Anything else the skill
needs sits beside its `SKILL.md` in the same folder. They fall into two families: **judgment maintenance skills**
(Section 1.9, Tier 2), which do the work that needs an opinion about content, and **collaboration skills**, which
govern how agents work *with the human*, the same pattern coding harnesses use for their own skills.

### The ten skills that ship

Ten skills ship in the package today. A shipped skill is a starter draft: it makes something sensible happen on day
one, and it says so at the bottom of its own text. It is mounted read-only from the implementation, ahead of the
Knowledge Base's `skills/` folder, so an untouched skill improves whenever the implementation does. That folder stays
empty until the human adopts one, and adoption is a permanent fork: the copy shadows the shipped default from that
moment and later improvements stop reaching it (Part 3).

They sort by what each one is pointed at, and nine of the ten are pointed at the human's subject.

**Pointed at something arriving from outside the topic.**

- **`ingestion-classification`** decides whether an inbound thing is a reference, a note, or a solution, and drafts
  the file with the metadata that decision implies. One decision settles the folder, the `source_type` and the
  `type.*` tag together, so a wrong classification makes every part of the file wrong at once.
- **`ingest-paper`** extracts a paper, study, whitepaper or technical report into question, method, results,
  limitations, and the section no paper contains: *does this apply to me*, with the mismatch that drives the answer
  named.
- **`ingest-book`** extracts a book or long report through the source's own chapters, one bullet per argument this
  topic cares about, and keeps *read and took nothing* separate from *never opened* in a reading record at the end.

**Pointed at what the topic already holds.**

- **`summarization`** drafts and revises the three breadth files, `topic.md` and the two summaries. It treats length
  growth as a defect: every revision distils and replaces, and none appends.
- **`conflict-detection`** runs the scan on four axes, tags what it finds for the human, and resolves nothing. The
  conflict type and the confidence stay in the conversation and never reach a file, because the Knowledge Base keeps
  no conflict register.
- **`tag-proposal`** proposes a tag the Knowledge Base has never used by writing the file that needs it and letting
  the write be held, so the human sees the tag and the file it would create in one decision.
- **`sub-topic-proposal`** proposes a split once `notes/summary.md` has stopped distilling and started enumerating. It
  brings the evidence and the full file assignment, and it creates nothing.

**Pointed at a conversation in progress.**

- **`research`** explores breadth-first across the Knowledge Base and returns three to five options, each with its
  trade-off and the files that support it. Reaching a note tagged `status.conflict-review` that bears on the question,
  it stops and escalates rather than picking the reading that suits the answer.
- **`discovery`** runs a brainstorming session against Knowledge Base content. It names the tension between two notes,
  names the gap a summary keeps implying, pushes back, and files nothing as a side effect. Anything worth keeping goes
  back through the front door as ordinary ingestion.

**Pointed at the human.**

- **`voice`** carries the voice profile every draft is written in, narrowed per topic when one subject wants a
  different register from another. The human corrects it from their own edits, and a change to the profile pauses for
  them like any other. It is the one shipped skill that knows something about the human instead of about cooking, and
  Section 2.8 turns on that fact.

**Four of the skills Part 1 describes have not been written yet, and the gap is on the session side.** Section 1.9
names four kinds of source extraction and two of them ship; an article, post or clip and a manual or reference work
are filed by `ingestion-classification` until their own skills exist. The other two are the ones Sections 2.7 and 2.8
lean on hardest. **Research planning and synthesis**, which turns a goal into questions and weighs what comes back,
has no file: the shipped `research` skill covers the breadth-first pass over the Knowledge Base alone and says
nothing about reaching the internet or about the rounds of Section 2.7. **Lesson proposal**, which drafts at
`/learn`, has no file either. Both workflows are specified in this document and neither one lives in a skill yet,
which is the honest reading of Section 2.8: the rules it states are rules nothing has been asked to follow.

Skills are like notes: **human-created, AI-assisted** (Section 1.3). They encode how the human wants agents to work
– something only the human can author. (The mechanical Tier 1 work is deterministic harness code, not skill files.)

A `SKILL.md` is not a knowledge file (Section 1.4): it carries the harness's `name` and `description` fields and no
PKB frontmatter, it is indexed nowhere, and it contributes no tags. `name` must match the folder name exactly, or the
harness drops the skill silently.

The extension rule: a Topic Expert Agent may **overload** any non-mechanical skill with a topic-specific version
that extends the default with domain intelligence – a recipe-writing voice for Cooking, a tasting-session discovery
skill, and so on. Like all skills, overloads are human-created with AI assistance. Overloads live in the topic's
`skills/` folder; a folder with the same name as a common skill overrides it, resolved by the harness exactly like
`expert.md`. An override is whole-record: a topic's `voice/SKILL.md` *replaces* the root one for that topic rather
than merging with it. An overload never redefines the general standards – the mechanical skills validate all output
regardless of which skill version produced it.

## 2.5 DeepAgent Harness and Access Channels

The agent layer runs on the **DeepAgent** agentic harness. DeepAgent hosts the Librarian and the Topic Expert Agents
and exposes them through multiple access channels:

- A dedicated **TUI**
- **Telegram channels**
- Other channels as needed (chat apps, APIs, etc.)

A PKB user can connect to the **Librarian** – the default entry point, which routes to the right experts – or connect
**directly to a specific Topic Expert Agent** when they already know which topic they are working with.

The two are joined at step 4 of the Librarian's workflow (Section 2.2). Every expert the Librarian consults is
addressable in its own right, so a reply that says *"the Cooking expert says…"* is also an offer: continue with that
expert directly, in the conversation it has already had, without repeating the question.

**On a channel-based transport, a channel is a session** (Section 2.7). One expert holds several channels at once,
each named for the goal it works on, and `/close` ends the session and the channel together. Two commands act on the
work itself, `/learn` and `/close`; the rest of the surface lists and cancels.

## 2.6 Agent Hierarchy

```
┌──────────────────────────────────────────────────────────────────────┐
│                    PKB USERS / EXTERNAL AGENTS                       │
│                 (humans, Project Manager agents)                     │
└──────────────────────────────────────────────────────────────────────┘
                     │                                    │
                     ▼ default entry                      ▼ direct
┌──────────────────────────────────────────────────────────────────────┐
│                         DEEPAGENT HARNESS                            │
│               (dedicated TUI, Telegram channels, ...)                │
│                                                                      │
│   ┌────────────────────────────────────────────────────────────┐     │
│   │  LIBRARIAN TURN                                            │     │
│   │                                                            │     │
│   │  1. CLASSIFY  ── the one model call ──▶ applicable topics  │     │
│   │        │            (root index.md = the catalog)          │     │
│   │        │            unsure ─▶ ask the human: which         │     │
│   │        │                      experts? (menu)              │     │
│   │        ▼                                                   │     │
│   │  2. FAN OUT  (harness code – always runs, question or      │     │
│   │        │      information, up to N experts at a time)      │     │
│   │        ▼                                                   │     │
│   │  3. MERGE BY ATTRIBUTION  (harness code – never a model)   │     │
│   │        │      one section per expert that actually ran     │     │
│   │        ▼                                                   │     │
│   │  4. OFFER the answering experts for direct connection ─────┼─────┼──┐
│   └────────────────────────────────────────────────────────────┘     │  │
└──────────────────────────────────────────────────────────────────────┘  │
             │              │              │                              │
             ▼              ▼              ▼                              │
      ┌────────────┐ ┌────────────┐ ┌────────────┐                        │
      │  TOPIC     │ │  TOPIC     │ │  TOPIC     │◀───────────────────────┘
      │  EXPERT A  │ │  EXPERT B  │ │  EXPERT C  │   (each addressable
      │            │ │            │ │  declines  │    on its own)
      └────────────┘ └────────────┘ └────────────┘
             │              │
   each ingests the same source through its own lens
   (own expert.md, own skills, own voice) – different
   extractions, not copies
             │              │
             ▼              ▼
      judgment + collaboration skills (overloadable)
        + harness maintenance hooks (mechanical)
        + read-only search sub-agents (Section 2.7)
             │              │
             ▼              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        KNOWLEDGE BASE TOPICS                         │
│              topic.md, index.md, references/, notes/                 │
│                 + root index.md, tags.md, skills/                    │
└──────────────────────────────────────────────────────────────────────┘
```

## 2.7 Sessions

A Topic Expert Agent answers from what its topic already holds. A session covers everything past that: the goal the
topic cannot meet, the trials that follow it, and the lesson the human and the expert settle at the end. Capture files
what the human already knew. This is where they find things out, and it is where most of their notes come from.

**A session is a long conversation on one line of work, and research is one of the things it might turn into.** It
might instead be a discussion, an argument about a design, a stretch of trying things and reporting back, or all of
those in sequence. The system holds one shape for every one of them, so the human never has to say in advance which
kind of conversation this is going to be. Research gets the most words below because it has the most machinery behind
it, and a session that never searches anything is an ordinary session rather than a lesser one.

### A channel is a session

The human opens a channel on an expert and works in it. The channel holds one line of work and lasts as long as that
work lasts, an afternoon or four months. There is no second object to open, no session to start, and no form to fill
in. Work begins as a question and becomes whatever it becomes: reading, then trials, then a verdict, or none of
those. The system never asks the human to declare in advance what a conversation will turn into, because they do not
know yet and a wrong declaration is furniture they then have to maintain.

One expert holds several channels at once, each named for its goal. `Trading · Trend Signal` and `Trading · Market
Regime` run side by side. There are two reasons, and the second one is the stronger:

- On the human's side, separate lines of work are separate places on the phone, and a phone is where this work
  happens.
- On the model's side, each channel keeps its context on its own goal. A conversation replays its whole history on
  every turn, and it keeps doing that until it reaches 170,000 tokens and compacts to a summary plus the last six
  messages. Two goals in one conversation means every trend-signal turn re-reads the regime work and gets an
  invitation to blend the two.

A deliberate split is safe where an accidental one is not: the channel title says which line of work a message went
to, and it names the expert as well as the goal.

### Simple topics stay direct; complicated ones cross

Cooking is one expert, one subtree, one channel: the human talks to the Cooking expert and nothing else needs to
happen. Personal finance and investment cross, and neither portfolio management nor trading holds the whole of it.
That work opens a channel on the Librarian, which fans every turn out to the applicable experts and merges what they
return by attribution (Section 2.2). The Librarian still writes nothing into the Knowledge Base, and each expert
still writes only inside its own topic.

### Three pillars, in order of standing

A session draws on three bodies of knowledge, and they do not carry equal weight:

1. **The human's experience** – the topic's `notes/`, what the human proved in practice. Highest standing, per rule 1
   in Section 1.8.
2. **Static knowledge** – the topic's `references/`, everything already ingested through this topic's lens.
3. **The internet** – everything else, reached through search sub-agents. No standing until the human accepts a page,
   and accepting it makes it a reference that ranks as one.

Nothing arrives from the internet already trusted. A session brings candidates to the human, with the evidence behind
them. The human decides.

### The expert argues with you, and it argues about your own conclusions

Work with the expert the way you would work with a human expert. A good expert objects during the work rather than in
a retrospective. Say the rub needs more sugar, and if the human's own note from March says sugar burned at that
temperature, the expert says so while there is still time to change the rub.

The same holds at `/learn`, where **the human can be wrong**. Told to file *sugar burns above 250*, an expert that
reads trial two running at 260 without burning says so before it drafts a word. Then it files what the human decides,
because meaning is theirs to curate (Section 1.6). The checks behind that objection are the ones Section 1.7 already
defines, run against a proposed lesson instead of against a retrieved page: one mechanism, not a second one.

### Two commands

**`/learn`, at any time, as often as it is worth asking.** The expert proposes what it thinks was learned and what is
worth filing. The proposal arrives as **an ordinary message in the channel**, never as an approval. An approval halts
every other message on its thread until it is answered, so *"that lesson is half right, drop the second part"* would
not be expressible: an approval accepts only the answers it offered, and that sentence is not one of them. Mid-work
use is the point. A channel that runs for months compacts its own early trials out of the conversation, and something
learned in week two is gone by the time the verdict lands if nobody captured it.

A repeat `/learn` files nothing twice. The session record holds what already landed and where, so a second
`/learn` over the same trials shows that lesson as filed, with its path, instead of proposing it again. Two
near-identical solution notes in one topic both reach every implementation pack and then drift apart, which is the
harm rule 4 in Section 1.8 exists to prevent.

**`/close`, when the work is done.** It ends the session and the channel together, because they are one object. On a
channel where `/learn` never ran, the expert offers once before closing, the way a good expert says *"before you go, I
think there is something here"* rather than letting the human walk out. Say no and the channel closes with nothing
filed.

There is nothing else: no session type to pick, no lifecycle to declare, no form to fill in.

### The loop is: try it, report back, distil

Research a rub and a smoking approach, cook, come back with how it tasted, cook again, and distil after the third
attempt. Research a trend-following signal, run several variants, watch which one holds and under what setup, and
distil once the data arrives. Feedback lands in pieces, from wherever the human is, over weeks.

This loop is the half of the design that manufactures the prior. The three pillars above treat the human's notes as
something to consult; the loop is where those notes get made.

### A round of research is a workflow, not a decision

A channel runs as many rounds of research as the work needs, and each round runs as harness-encoded steps for the
same reason routing does (Section 2.2). The expert's judgment sets the questions and weighs the answers; the search
itself and the checking of what it returns are code.

1. **Take the goal.** The human says what they want to know, and the expert writes the goal into the session's own
   file (below). A channel sets as many goals over its life as the work turns up.
2. **Survey the topic.** The expert reads `topic.md`, both summaries, and the notes and references that touch the
   goal. A goal the topic already meets ends the round here, with the answer and the files it came from. Searching
   the internet for something already filed spends the budget and invites a page that contradicts the human's own
   note.
3. **Write the questions.** The expert turns the goal into one question per line of enquiry. Each question carries an
   objective, the shape its answer should take, the sources worth trying, and its boundary against the other
   questions. Vague briefs are the documented cause of two sub-agents researching the same thing while a third
   researches something nobody asked for.
4. **Search.** The harness starts one search sub-agent per question and runs them in parallel. This step is code. A
   model free to decide whether to delegate does not delegate, and Section 2.2 records what that cost the Librarian
   before its fan-out became a step that always runs.
5. **Verify.** Harness code locates every quotation in the text the search returned for the page it came from, and
   fetches nothing itself. A sub-agent that wants a page its search did not return reads that page back in step 4,
   and the text joins the same record. A URL the harness holds no text for and a quotation that text does
   not contain are both **held**: not carried into the
   synthesis, and not thrown away either. They land under their own heading in the session record, with the reason
   they failed, and in the session summary if one ever lands. A human who never sees a fabricated quotation never
   learns that the researcher fabricates.
6. **Weigh.** The expert compares what survived verification against the topic's notes and references, claim by
   claim, and classifies each disagreement by the three conflict types in Section 1.7.
7. **Report back.** The expert brings what survived verification, with the evidence behind it, and the conversation
   carries on from there. The human may name a source worth ingesting on the spot. Everything else waits for
   `/learn`, because the round found things and the human has not tried them yet.

```
HUMAN ── opens ──▶ CHANNEL = SESSION   one line of work, named for its goal,
                        │              on one expert, or on the Librarian
                        │              when the work crosses topics
                        ▼
  ┌──────────────── a round of research ─────────────────────────┐
  │ 1. take the goal      into the session record                │
  │ 2. survey the topic   notes first, then references           │
  │ 3. write the questions (the goal only, no beliefs attached)  │
  │                       ▼                                      │
  │ 4. SEARCH   (harness code, one sub-agent per question)       │
  │      ┌────────────────┼────────────────┐                     │
  │      ▼                ▼                ▼                     │
  │  ┌───────┐        ┌───────┐        ┌───────┐  read-only: no  │
  │  │SEARCH │        │SEARCH │        │SEARCH │  write tool of  │
  │  │ SUB-  │        │ SUB-  │        │ SUB-  │  any kind. 3 by │
  │  │ AGENT │        │ AGENT │        │ AGENT │  default, and   │
  │  └───┬───┘        └───┬───┘        └───┬───┘  budgeted.      │
  │      └────────────────┼────────────────┘                     │
  │                       ▼                                      │
  │ 5. VERIFY   (code: every quotation found in the held bytes)  │
  │ 6. WEIGH    against the topic's notes, claim by claim        │
  │ 7. REPORT BACK ──▶ the conversation carries on               │
  └───────────────────────────┬──────────────────────────────────┘
                              │
      the human goes and tries it, then comes back with what
      happened. Rounds and trials repeat for as long as the
      work lasts: an afternoon, or four months.
                              │
          ┌───────────────────┴───────────────────┐
          ▼                                       ▼
   /learn  any time, repeatable            /close  ends the session
   an ordinary message the human                   and the channel;
   argues with; then they approve                  offers /learn once
   the exact text that lands                       if it never ran
          │
   ┌──────┴─────┬──────────────┬─────────────────────┐
   ▼            ▼              ▼                     ▼
 notes/      sessions/     references/          nothing at all
 what the    what the      the accepted         the common outcome:
 human       session       article and its      no note, no summary,
 tried, and  worked out:   original copy        no folder, no trace.
 it worked:  type.summary  (ordinary            The closed Telegram
 type.       provenance:   ingestion)           topic keeps the
 solution    researched                         conversation.
 provenance:
 practised
```

### A session writes goal specs and executes nothing

Work turns up things to do: run this backtest over these three regimes, cook this at 250 for four hours. A session
may write that down as a **goal spec**, which says why the work is being done and what a good outcome looks like, and
stops there. It never says how to build the thing and it never runs it. Something else picks the spec up: the Project
Manager (Part 4), a coding harness, or the human with a smoker. Results come back into the channel as ordinary
conversation and the work carries on.

A spec is a message until the human says it is worth keeping. Then it lands as part of the session's synthesis under
`sessions/`, the same route as everything else the session read or wrote. The Knowledge Base never grows a task
queue, a runner, or a status field, because a knowledge base that executes has to remember what it is halfway
through, and that is a second store of truth that can be wrong.

### A session's durable record is a file, not the conversation

A session keeps a **session record** and writes into it as the work happens: the goal, each trial and what it
produced, the sources kept and the ones turned down. The record lives in the session's workspace, outside the
Knowledge Base tree, and it survives a restart of the daemon. It is the running file this section names throughout,
and the session summary of Section 2.7 is the separate thing the human approves into the tree.

The reason is measured. A channel that runs for months compacts, and by the time the verdict lands, week two is one
line of a summary. The expert therefore reads the file rather than the transcript, and `/learn` drafts from the file.
The large-source ingestion loop settled this shape already, and its docstring is worth quoting: *"There is
deliberately no second store of progress: a second source of truth about what was read is a second thing that can be
wrong, and the one a human can check is the file."*

### Most sessions leave nothing behind, and that is correct

Ask for a recipe, get one, `/close`. No note, no summary, no folder, no trace in the tree. Discard is an outcome this
design expects, and it is the common one. Rule 4 in Section 1.8 rules the same way for ingestion: a topic that
derived nothing from a source gets no folder and no stub, because an empty folder claims the source was considered
and is somehow relevant.

The asymmetry is deliberate. The Telegram topic stays, closed and readable, holding the conversation and the
suggestion the human turned down. The phone becomes the archive of what the tree chose not to keep.

That archive belongs to Telegram, not to the Knowledge Base, and the honest version of the sentence says so. The tree
keeps nothing by rule, and the channel keeps the conversation for as long as Telegram and the human keep the topic.
Delete the topic and the discarded work is gone, with nothing in the tree able to reconstruct it. That costs the rule
nothing: discarded work has no claim on the tree either way.

### The search sub-agents read; the expert writes

A search sub-agent holds retrieval tools and no write tool of any kind. The permission layer enforces that, the same
way it confines a Topic Expert Agent to its own subtree: a write tool it never received is a write it cannot make.
The expert authors everything a session produces.

Each sub-agent spends a whole context window on one question and returns a page or two. That compression is the
reason to run one: the expert reads the findings in place of everything the sub-agent had to read to write them.

Three sub-agents is the default width, set by the deployment rather than by anything in the Knowledge Base, and the
expert names the width it used the first time a round reports back. A budget that a topic's own files could raise is
a budget an agent's own write could raise. (Section 1.1's *research agents* are a different thing: they are the
breadth-first consumers of context packs described in Part 4.)

### The notes weigh the results; they never travel with the questions

The human's notes hold the highest standing in the Knowledge Base, so the obvious move is to hand them to the search
sub-agents and let the search start from what the human already believes. Measurement says to do the opposite.

A model told what the human believes stops finding evidence against it: disconfirmation detection falls by 16 to 93
percentage points across four models once the belief sits in the prompt. Humans do the same thing to themselves. A
search conversation that agrees with the searcher raises the rate of confirming queries from 16% to 43%, and the
damage is done by the questions asked rather than by the answers given.

The questions in step 3 therefore carry the goal and nothing else. The notes return in step 6, where the expert
weighs a verified result against them. A prior multiplies the evidence. It does not choose which evidence gets
collected.

### Search comes from the model provider

The experts already run on Ollama's cloud models, and the same account serves search. The system asks that account for
results and for the text of a page, so the design signs up no second vendor and opens no second account. Search
takes one credential, `OLLAMA_API_KEY`, and it sits beside the Telegram token in the same gitignored file outside the
repository. The daemon reads both at startup. Neither value reaches an agent, a log, or the health endpoint. The models
themselves have been running without that key, so in this deployment the key buys search and nothing else. The plan's
treatment of search, included in the subscription or metered on top of it, stays unconfirmed until a bill settles it.

**A result arrives as the page's text rather than as a link to it.** Ollama returns thousands of characters of content
per result, so the harness holds what a search sub-agent read at the moment it read it. A quotation is then located in
the exact bytes the claim came from. The check stops being a best effort against a page that may have changed since the
search and becomes a comparison. Every admissibility rule below rests on that.

Search returns extracted text, so ingestion still fetches a source itself. The copy a topic keeps beside an accepted
reference is the original bytes off the web (Section 2.3): search and page reads serve the research, and the ingestion
path serves the filing.

**One provider serves the models and the search, so one outage takes both.** The local fallback model exists for the
hour the cloud is unreachable, and search is unreachable in that same hour. The offline story is therefore blunt:
research stops. A round that cannot search says so and ends, and the expert goes on answering from the topic's own
notes and references on the local model, at a fraction of the speed. The human keeps a working expert over what is
already filed. They do not keep a researcher, and a session that sits waiting for the cloud to come back is a session
doing nothing. The channel stays open through all of it, and the next round searches again the first time the provider
answers, so nothing polls and nothing queues.

### Verification is code, not trust

Published deep-research agents invent 3% to 13% of the URLs they cite, and 5% to 18% more of the URLs they give do
not resolve. In one shipped generative search product, 51.5% of the sentences it wrote were fully supported by the
citation attached to them. So no URL reaches a session summary on a model's word:

- **Harness code holds the text of every cited page.** Search hands the page's content back with the result, so the
  harness already has the bytes a sub-agent read. A sub-agent that wants a page beyond what its search returned reads
  that page while it is still searching, and the text joins the same session record. Verification itself fetches
  nothing. A citation the session record holds no text for keeps its claim out of the synthesis, and the record names
  the claim and says why it carries no weight.
- **Harness code locates every quotation in the text that page returned.** A quotation the text does not contain is
  dropped, and the session record says a quotation was dropped. The comparison runs against the bytes the claim came
  from, never against a page fetched again later and possibly rewritten in between.
- **The harness never asks a model where a quote sits.** Models miscount positions and invent spans. The sub-agent
  returns the quoted text; code finds it.

The same rule governs an extraction: a quotation a model produces is a candidate until code finds it in the source.

### A page can be written to be read by an agent

Research is the first thing here that pulls text chosen by strangers into the conversation. Retrieved text is
therefore fenced as data everywhere it travels, with a standing instruction that nothing inside the fence is an
instruction, and every quotation a session shows the human is rendered inside a quoted block with its source attached,
so a page's prose never appears in the system's own voice.

That is mitigation and not a cure, and the design says so rather than leaving it to be assumed. What bounds the damage
is structural: a search sub-agent holds no write tool and reads no Knowledge Base file, an expert cannot write a note
on a turn where it reached outside the tree, every claim needs a quotation found in the bytes the harness itself holds,
and the file the human approves is rendered by the harness rather than typed by a model.

### The budget bounds quality, and cost is not the reason

Cost is not the constraint on research. The budget exists because a long run is a worse run. Factual accuracy on one
measured search agent fell from 79% to 17% as its tool calls rose from 2 to 150. Between 77% and 94% of the steps in
a long search add no new evidence, and a run that reaches the wrong answer runs two to three times longer than one
that reaches the right answer. Length is a symptom before it is a cost.

A **round** carries a step budget and a wall-clock budget. The channel carries neither: the work lasts as long as it
lasts, and the search inside it is the thing that gets bounded. Exhausting either budget stops that round, and the
expert says it stopped short of the goal. A round that admits it ran out is a result the human can act on: they say
chase it again with a narrower question, and the channel is still open for them to say it in.

### The session summary

A session that worked out enough to be worth summarising produces one file:

```
sessions/[goal-title].md
```

It follows the folder-hosted convention of Section 1.2 when it needs media beside it. It is a knowledge file
(Section 1.4) with `source_type: summary` and the tag `type.summary`, because a session summary is an overview of what
one line of work found. It carries `provenance: researched` (Section 1.4), which is the field that keeps it
distinguishable from a note the human earned. The tag namespaces of Section 1.5 do not grow for sessions: a tag would
only restate the folder, and it would disagree with the folder the first time somebody moved a file.

The summary holds, in order: the goal, the questions the session asked, every source it kept, every source it rejected
and why, the conflicts it raised against the topic's notes, and the synthesis. A session that searched nothing fills
the source sections with nothing and keeps the goal and the synthesis, because a discussion that reached a conclusion
still reached it by thinking rather than by doing.

Two files carry a session's memory and they are not the same file. **The session summary** lands in the tree, holds
what the session worked out, and waits for the human's approval on its exact text. **The session record** lives in
the session's workspace outside the tree, holds the running log of trials and candidates, and nobody approves it
because nobody else reads it.

A session with nothing to summarise writes no summary. A session that read one page, cooked from it, and learned one
thing writes a note and no summary. The summary exists to hold what the session **worked out**, and the note exists
to hold what the human **did**.

**The summary is append-only.** A turn adds to the end. Nothing rewrites an earlier entry, because a model asked to
revise a long report across turns removes correct material without saying so and introduces errors while it polishes.
A correction is a new entry naming what it corrects.

**Rejections live here and nowhere else.** A candidate the human turns down leaves no folder under `references/`, no
stub, and no copy, per rule 4 in Section 1.8. The reason is theirs, recorded in the words they typed, and it outlives
the source. A later session that finds the same page shows it **labelled with the date and the reason**, at the
bottom, rather than hiding it: the page they turned down for one question may be the page they want for the next one,
and a result silently dropped is indistinguishable from a result never found. A session that files no summary keeps
its rejections in the session record and in the closed channel, which is where everything else it discarded stays too.

**Candidates live with the session, not in the tree.** A page the search returned and the human has not accepted is
held with the session: the text goes when the research that found it ends, and its line in the session record goes
when `/close` ends the channel. It is not staged, not copied, and not written anywhere under the PKB root. `.inbox/`
is where an
**accepted** source stages on its way through ordinary ingestion, and nothing else puts anything there. The cost is
honest and small: a page the human accepts is fetched a second time. The alternative was thirty browsed candidates
leaving thirty permanent folders in a tree with no undo, in a staging area no channel can list.

**Nothing lands until the human approves the text.** The session record holds the running state and survives a
restart, so a session that dies halfway loses nothing. Once the human has finished arguing with a `/learn` proposal,
the harness renders the file that would land, they read the exact text, and only then does it land. Every write under
`sessions/` waits for them, the first file and every later one.

### What a session may file

| Outcome                                | Where it lands                                     | What gates it                                                          |
|----------------------------------------|----------------------------------------------------|------------------------------------------------------------------------|
| Something the human tried, and it worked | `notes/`, tagged `type.solution`, stamped `provenance: practised` | Settled at `/learn` in conversation; the human then approves the rendered text |
| The session's synthesis of what it worked out | `sessions/[goal-title].md`, tagged `type.summary`, stamped `provenance: researched` | The human approves the rendered text, before it lands |
| An article the human accepts           | `references/[source-name]/`, through the ordinary ingestion procedure (Section 2.3), with the topic's own copy of the original | The human names the candidate; the harness will only ingest a page it printed for them and fetched itself. The first extraction is then un-gated, like any other first ingestion |
| A candidate the human rejects          | The rejection list inside the session summary, and the session record until one lands | Nothing, and no other file changes                    |
| Nothing at all                         | Nowhere: no note, no summary, no folder              | `/close`, and this is the common outcome                               |

Rule 8 in Section 1.8 is the line this table draws. A session files everything it **read** as a reference or as a
synthesis, and it files everything the human **did** as a note, in their own words, after they did it. `provenance`
records which of the two a file is, and it is the only field that can (Section 1.4).

### Direction is conversation; the write is the approval

The human steers a running session by talking to the expert: drop that question, chase this one instead, that source
is no good, that lesson is half right. An approval halts the conversation until it is answered, so spending one on
"those three look right, keep going, but drop the second" would stop the session to ask what the session could have
heard in passing. Worse, an approval only accepts the answers it offered, and that sentence is not one of them. This
is why `/learn` proposes in an ordinary message.

One approval remains, and it sits on the bytes. For each file that would land, note or summary, the human reads the
exact text and says yes or no. A `/learn` that proposes three notes asks three times, and they may take one and drop
two. Accepting a source for ingestion is an instruction rather than an approval, and what makes it safe is the fact
that the harness will only ingest a page it printed for the human and fetched itself.

### `/learn` on a Librarian channel fans out

On a Librarian channel, `/learn` treats the session itself as a source and fans it out. Each applicable expert is
asked what its own topic takes from it, which is the question the ingestion loop already asks section by section,
with the same fixed grammar: something new, something better, something that contradicts what I hold, or nothing. An
expert that takes nothing files nothing and leaves no folder and no stub. Each note lands inside its own topic, so
every expert stays in its own subtree and the Librarian still writes nothing.

A Librarian `/learn` therefore proposes a **set** of notes. The human takes some and drops others, naming them by the
label printed beside each one. Each kept note then asks for its own approval on its own text, and each of those
belongs to the expert that wrote it, so a rejection on one changes nothing about the rest. The cost is honest: four
kept notes means four texts to read and four answers to give.

This is not a breach of rule 4 in Section 1.8. That rule forbids one lesson living in two places and drifting apart.
A session that yields a portfolio lesson and a trading lesson yielded two lessons, which is the same shape as one
book reaching two topics. An insight that genuinely spans rather than decomposes lands in one topic with
`related_topics` naming the others, and the hooks aggregate that into the root registry (Section 1.9).

The Librarian runs no search of its own and holds no topic's search tools. Holding them would let it answer a subject
question out of its own head, which Section 2.2 forbids. It reaches a topic's tools through the expert that owns
them.

### Domain tool servers belong to a topic

A topic may bring tool servers of its own: a recipe service for Cooking, a case-law service for a legal topic. The
**deployment** binds them to that topic's expert, in the daemon's own configuration, for the same reason the model is
chosen there and not in the tree: configuration an agent can write is configuration an agent can grant itself.
`expert.md` may describe what a topic uses; it never decides what a topic holds. A server is declared for the
expert's own turns or for its search sub-agents, and a server declared for the sub-agents never reaches the expert
directly, so a page off the internet cannot enter a note by the side door. They reach no other topic and they never
reach the Librarian.

A narrow goal then stays with one expert and never becomes a routing problem. "Adjust this smoking recipe for the
grill in my garage" is one topic's work, with one topic's tools, in one conversation.

## 2.8 What a Session Is Allowed to Conclude

`/learn` is the moment a session turns a conversation into a claim. Section 2.7 says how it runs and where its output
lands. This section says what the system may conclude at that moment and what it may never conclude, because those are
two questions and the second one has more ways to go wrong.

### The default is silence

Nous Research shipped **Hermes Agent** in February 2026, and it is the nearest shipped system to this one:
agent-curated memory about the human, plus skills the agent writes for itself after hard tasks. Everything this
section says about it was read out of its source, and every quotation names the file it came from. Its landing page
describes the same behaviour in looser words and is cited for nothing here. The prompt in `background_review.py`
states its prior in capital letters: *"Be ACTIVE. Most sessions produce at least one skill update, even if small. A
pass that does nothing is a missed learning opportunity, not a neutral outcome."* And forty lines later: *"'Nothing
to save.' is a real option but should NOT be the default."*

That prior is right for Hermes and wrong here, and the substrate is the reason. Hermes writes into a skill library
with an archive and a rollback, so a bad write costs somebody a revert. This design writes into a tree with **no
undo**, where a bad note reaches every implementation pack that touches its topic and stays there until a human
notices and rewrites it by hand. So the prior inverts. At `/learn`, filing nothing is the default and the ordinary
outcome, and a session that files nothing has missed nothing. Section 2.7 says most sessions close with nothing filed;
this is that sentence turned into an instruction for the agent rather than an observation about the human.

### Five things a session produces that look like knowledge

The same `background_review.py` prompt carries an exclusion list, and it is the most reusable thing in that system.
Each entry names a way a session manufactures something that reads like a lesson and is not one. All five hold here,
and the first one is the dangerous one:

1. **An approach that never worked.** The session tried several things, none of them worked, and it ended by telling
   the human to check by hand. Hermes names the harm: *"do NOT write those attempts up as a 'reliable
   workflow' or 'recommended approach'. That presents an untested sequence of failures as validated guidance a future
   session will trust and repeat."* Three briskets that all came out dry support a note about three briskets. They
   support no note about how to dry-brine.
2. **A failure the environment caused.** The smoker's thermostat read 40 degrees low that week; the data feed was down
   that afternoon. Filing that as a property of the technique blames the method for the machine.
3. **A verdict that a tool cannot do something.** Hermes on why this one earns its own entry: *"These harden into
   refusals the agent cites against itself for months after the actual problem was fixed."* A note saying the search
   provider returns nothing on a subject outlives the outage that produced it, and every later session reads it as a
   fact about the subject.
4. **An error a retry cleared.** *"If retrying worked, the lesson is the retry pattern, not the original failure."*
   The error itself is noise. The patience might be knowledge.
5. **The story of one afternoon.** A narrative of what happened once is not a rule, and `notes/summary.md` holds
   rules. The loop in Section 2.7 asks the human to cook three times before distilling for this reason.

A sixth exclusion is already law in Part 1 and needs no argument from Hermes: nothing off the internet becomes a note
(rule 8 in Section 1.8). A session files what it read as a reference or as a session summary, and it files what the
human did as a note.

### What a session may conclude

Three outcomes, and the table in Section 2.7 gives the gate on each: a note holding what the human tried and what
happened, a session summary holding what the session worked out, or nothing at all.

The bar on the first is three conditions and each one is load-bearing. The human did the thing. They came back and
said what happened. They approved the exact text that lands. An expert that has only the first two has a trial, not a
lesson. The expert argues about what the experience means and it never argues about whose it was (Section 1.3).

### The skills that already generalize

Four of the shipped skills (Section 2.4) do the generalizing work, and `/learn` is one call site among several rather
than the only one:

- **`summarization`** turns individual notes into the distilled rules of `notes/summary.md`, which is the file every
  implementation pack loads first. A lesson filed at `/learn` reaches decisions through this skill.
- **`conflict-detection`** catches the human contradicting their own earlier self, which is the case a single session
  cannot see: the week-one note and the week-twelve note were written in different conversations.
- **`discovery`** finds the rule under two notes that never mention each other, and it files nothing, so the finding
  goes back through the front door with its own approval.
- **`voice`** watches for the same edit repeated across three drafts and proposes it as a rule. That is learning from
  experience about the human rather than about the subject, and it is the reason the open question below has a seed to
  point at.

A skill for the drafting at `/learn` is described in Section 1.9 as **lesson proposal** and does not ship yet
(Section 2.4). The checks that lesson has to answer for stay in harness code either way, per Section 1.7: a skill is a
file the human may adopt and then edit, and a guarantee living in an adopted copy leaves the day they edit it.

### Two guards Hermes puts in code, and both belong here

Hermes enforces two of its safeguards in the tool layer rather than in prose, which is the same choice Section 2.2
made about fan-out and Section 2.7 made about verification.

**Nothing rewrites a file whose current text it has not read this turn.** Hermes's `skill_manager_tool.py` refuses
a patch to a file the reviewer has not loaded verbatim in that same turn, because *"the autonomous review fork is
allowed to evolve skills, but it must not patch or rewrite content it has only inferred from the transcript."* A
`/learn` proposing to revise a note the human filed in March is working from an impression of that note, the
impression came out of a conversation that has since compacted, and the note may have been edited by somebody else in
the meantime. Read the file, or leave it alone.

**Authorship decides what may be curated.** Hermes's `skill_provenance.py` tags every skill write with its origin so
that autonomous curation only ever touches skills the autonomous process itself created: *"Skills a user asks a
foreground agent to write belong to the user and must never be auto-curated."* Part 1 already draws that line as the
collaboration rule, and `provenance` (Section 1.4) is where the tree records which side of it a file sits on.

### What Hermes does not license

Hermes has **no session-end ceremony at all**, and the source is plain about it: `agent_init.py` sets both nudge
intervals to ten, so skill review fires at the end of a turn once ten tool iterations have accumulated and memory
review fires every ten user turns, and `turn_finalizer.py` carries an explicit note that `on_session_end()` is not
called there. The landing page says Hermes writes a reusable skill when it solves a hard problem, which is true of the
behaviour and silent about the timing, and the timing is the whole question here. So `/learn` at close copies nothing
Hermes does, and none of Hermes's numbers are evidence for a once-per-session write. The cadence here belongs to the
human, because the human types the command.

Two more Hermes decisions arrive here already satisfied. `background_review.py` forks a **separate** reviewer agent
restricted by runtime whitelist to memory and skill tools, at roughly 30,000 tokens an event, so the reviewer cannot
reach the dangerous half of the tool surface. This design reaches the same containment through the approval gate: the
expert drafts, the human reads the rendered bytes, and nothing lands without that. `turn_finalizer.py` also suppresses
the review when no human was in the loop, on the grounds that *"cron sessions have no human-in-the-loop benefit from
the review"*. Every `/learn` here is typed by a human, so there is nothing to suppress.

### The open question: may a session teach the system how to work?

**Hermes learns how to work. This design learns what is true.** Hermes writes skills, which are procedure. Every route
in Section 2.7 ends in a note or a session summary, and both of those say something about the human's own subject. A
session that established *brisket holds at 250* produced knowledge of the second kind and the design has a route for
it. A session that established *a better way to run a session* produced knowledge of the first kind and the design has
no route for it at all.

The seed of the missing half is already here. `voice` holds a profile of the human, corrected from their own edits
through the same propose-and-approve loop as everything else (Section 2.4). It is procedural knowledge about how to
work with this person, and it is the one place the system knows something other than cooking.

**A self-written skill has nowhere to live today.** The shipped skills mount read-only out of package data. The
Knowledge Base's own `skills/` folder is the only writable home, and writing there *is* adoption, which forks that
skill permanently and is meant to be a deliberate act by the human (Part 3). So a `/learn` wanting to record a better
way to run a session would have to fork a shipped skill in order to say it, and the fork stops later improvements
reaching the thing it forked. The cost of the missing route is a fork nobody asked for.

These are the human's to settle, and this document raises them rather than answering them:

- May `/learn` ever propose a **skill** as well as a note?
- If it may, where does that skill live? An adopted fork at the root, a topic's own `skills/` folder, or a file class
  that is neither shipped nor adopted?
- Does a machine-proposed, human-approved adoption still count as the deliberate act Part 3 asks for, or does it
  hollow out the reason adoption is deliberate?
- Does a lesson about how to work belong in a skill at all, or does it belong in `voice`, which already holds what the
  system has learned about the human?

---

# Part 3: Knowledge Base Layout and Bootstrapping

The full Knowledge Base is a tree of topic roots, each following the standard structure defined in Section 1.2:

```
KnowledgeBase/
├── index.md                # Root catalog: every topic + description (machine-maintained)
├── tags.md                 # Global tag registry (machine-maintained)
├── .inbox/                 # Staging for sources on their way in – dot-prefixed, indexed nowhere
├── (optional) skills/      # Adopted skills only – empty until the human adopts one
│   └── [skill-name]/       #   one folder per adopted skill (voice/, discovery/, ...)
│       └── SKILL.md
├── [Topic Root]/
│   ├── topic.md
│   ├── index.md
│   ├── references/
│   │   ├── summary.md
│   │   └── [source-name]/
│   │       ├── [source-name].md
│   │       └── [source-files]
│   ├── notes/
│   │   ├── [note-title].md
│   │   ├── [note-title]/
│   │   │   ├── [note-title].md
│   │   │   └── media/
│   │   └── summary.md
│   ├── sessions/           # Extension folder – appears with the topic's first approved session summary
│   │   └── [goal-title].md
│   ├── (optional) expert.md
│   ├── (optional) skills/
│   │   └── [skill-name]/
│   │       └── SKILL.md
│   ├── (optional) [topic-specific]/
│   └── (optional) sub-topics/
│       └── (same structure recursively)
└── (other topic roots...)
```

## Bootstrapping an empty PKB

The PKB starts empty. The path to the steady state:

1. **Default skills ship with the implementation, and are mounted rather than copied in.** The PKB implementation
   provides starter versions of ten common skills, named one by one in Section 2.4: `ingestion-classification`,
   `ingest-paper`, `ingest-book`, `summarization`, `conflict-detection`, `tag-proposal`, `sub-topic-proposal`,
   `research`, `discovery`, and `voice`. They are loaded from the implementation itself, so the Knowledge Base's own
   `skills/` folder starts empty and an untouched skill improves whenever the implementation does. They are functional out of the box but are treated as drafts: when the human wants to change
   one, they **adopt** it – a copy lands in the KB's `skills/` folder and from then on shadows the shipped default
   permanently, human-created and AI-curated like all skills. Adoption is a decision rather than an accident, and it
   has to be: a seeded copy nobody touched is indistinguishable from one the human rewrote, and the Knowledge Base
   has no undo, so the implementation would have to choose between overwriting their work and never shipping an
   improvement again.
2. **`voice` ships with an opinionated starter profile, and is corrected from the human's own writing.** Every draft
   has a voice whether or not one is written down – without a profile it is simply the model's own, chosen by nobody.
   A wrong default shows up in the first draft and gets fixed; an absent one never does. So the shipped skill states
   real rules rather than asking questions, and the human corrects it from whatever writing they already have (early
   notes, or documents they supply). It improves over time as notes accumulate, through the same propose-and-approve
   loop as everything else. A topic may hold its own voice, which replaces the root profile for that topic.
3. **The first topics are created on demand.** With zero topics, every inbound item is a topic gap: the human either
   requests topics directly or approves the Librarian's proposals, and each new topic follows the topic creation
   flow in Section 1.9. There is no need to design a topic taxonomy up front – the tree grows from what the human
   actually captures.
4. **Structure catches up mechanically.** As soon as files exist, the hooks generate the indexes and the tag
   registry. Nothing is seeded by hand.

---

# Part 4: How Projects Use the Knowledge Base

The Project Manager (separate project) orchestrates projects that consume and enrich this Knowledge Base. Like all
PKB interactions, project access is agent-mediated (see Part 2): project agents send requests to the Librarian, which
routes them to the right Topic Expert Agents, or they connect directly to a known Topic Expert Agent.

## Context packs

Topic Expert Agents assemble context packs on request, matched to the requesting agent's role:

- **Research agents (breadth-first)** receive Research Packs built from `topic.md`, the relevant subtrees of the root
  `tags.md`, and the `summary.md` files of relevant topics, plus any notes tagged `status.conflict-review` that touch
  the research area, plus any approved session summaries that touch it. Research agents do not read `index.md` files
  unless explicitly asked.
- **Implementation agents (depth-first)** receive Implementation Packs built from the full `index.md` of the
  selected topic, detailed `references/[source]/[source].md` files, and relevant solution notes.
  `notes/summary.md` is always loaded first – human rules have the highest priority.

Every pack ranks human content above static knowledge, the same order rule 1 in Section 1.8 sets. A pack that leads
with references and appends the human's notes inverts the one rule the Knowledge Base exists to keep.

A session summary enters a pack only once it has landed, which means only once the human approved it (Section 2.7).
It sits after the topic's summaries and before the conflict-review notes: below what the human distilled themselves,
above what the topic merely disagrees about. A lesson a session filed at `/learn` is an ordinary note carrying
`provenance: practised`, and it enters a pack as one, ranked with the rest of the human's own experience.

## Conflict escalation

Any project agent that encounters a file tagged `status.conflict-review` affecting its task must pause and escalate to
the human before proceeding.

## Knowledge feedback

After a project (or a retrospective), the Project Manager proposes Knowledge Base updates:

| Update Type             | Description                                         | Example                                                              |
|-------------------------|-----------------------------------------------------|----------------------------------------------------------------------|
| **New Note**            | A specific observation or event from the project    | "Referral program required legal review"                             |
| **Summary Update**      | A generalized rule distilled from experience        | "Always check legal requirements before launching referral programs" |
| **New Solution Note**   | A reusable approach, filed as a note tagged `type.solution` | "Referral program with legal review framework"               |
| **Reference Update**    | New static knowledge ingested (if discovered)       | A relevant article on referral program compliance                    |
| **Conflict Tag Update** | A conflict resolved by the human (tag returns to `status.approved`) | Note updated after review                            |

What requires the human's explicit approval is the same here as on every other channel – the standards in Part 1
decide, not the caller. Filing a plain note and writing a first extraction of a source land unattended, because
capture must stay frictionless (goal 3 in Section 1.1). Changing human-approved content, adding a tag, creating an
extension folder, resolving a conflict, and rewriting an extraction the human has already read all wait for the
human. **Every write a session makes waits too**, the summary and the lesson alike, the first and every later one, and
what the human approves is the rendered text rather than a request to write it (Section 2.7). Once a change lands,
harness maintenance regenerates the relevant `index.md` files and the root `tags.md` registry.

The five exclusions in Section 2.8 bind a project retrospective as hard as they bind `/learn`, and a retrospective is
where they are easiest to break. A project that tried four approaches and shipped none of them produces a **Summary
Update** proposing the fourth as the recommended one, and the rule it writes reads the same as a rule somebody earned.
A project agent proposing an update names what the project shipped, and a proposal covering work that never worked
says so in the proposal.

---

# Part 5: Conflict Management Example

## Scenario

A human note says "Always preheat the grill for 15 minutes." A reference book says "Preheating for 10 minutes is
sufficient."

## Detection

A harness maintenance hook schedules a conflict scan; the Cooking Topic Expert Agent executes it and detects a
contradiction. It tags the note:

```yaml
---
title: "Preheat the grill for 15 minutes"
description: "Always preheat the grill for 15 minutes before cooking"
topic: "Cooking"
tags:
  - topic.cooking.grilling
  - topic.cooking.heat-management
  - type.note
  - status.conflict-review
created: 2024-12-15
updated: 2024-12-16
related_topics: [ bbq.equipment ]
source_type: note
review_note: "Reference 'Grill Basics' says preheat for 10 min. Note says 15 min."
---
```

The AI does not change the note text.

## Human Review

The human decides that the note is correct. The human does not edit the note. The human changes the tag back to
`status.approved` and removes the `review_note`:

```yaml
---
title: "Preheat the grill for 15 minutes"
description: "Always preheat the grill for 15 minutes before cooking"
topic: "Cooking"
tags:
  - topic.cooking.grilling
  - topic.cooking.heat-management
  - type.note
  - status.approved
created: 2024-12-15
updated: 2024-12-17
related_topics: [ bbq.equipment ]
source_type: note
last_reviewed: 2024-12-17
---
```

## Alternative Resolution

If the human decides that the reference is more accurate, the human edits the note to "Preheat the grill for 10
minutes." Then the human changes the tag back to `status.approved` and removes the `review_note`.

## Impact

- The note content is the true state of knowledge.
- No separate conflict registry is needed.
- The resolved conflict does not pollute the context window.
- Future agents read the note and follow the human-approved content.

---

# Summary

The **Personal Knowledge Base** is the memory of the Personal Companion:

- It stores memory, wisdom, and context. It uses hierarchical tags, a machine-maintained global tag registry,
  lightweight tag-based conflict management, and human-approved topic-specific extensions.
- **Human content wins**: human-written notes and human-approved summaries always take precedence over static
  references.
- **Division of labor**: notes, skills, and `expert.md` are mostly human-generated and AI-curated; everything else
  is AI-generated and human-curated, with mechanical files generated by hooks.
- **Breadth vs. depth**: `topic.md` and `summary.md` files serve breadth-first research; `index.md` files serve
  depth-first implementation.
- All interactions are **agent-mediated**. The **Librarian** – the root PKB agent – classifies each inbound item,
  and the harness then fans it out to every applicable **Topic Expert Agent** and merges their answers by
  attribution. Classifying is a model's judgment; fanning out and merging are code. One source may be ingested by
  several experts, each extracting what its own topic cares about.
- **Topic Expert Agents** run each topic – a single PKB template by default, overridable per topic via `expert.md`.
  Harness hooks enforce the mechanical PKB standards; the experts carry out the judgment work through common,
  overloadable skills and add unique domain knowledge, topic-specific file organization, and the best ways to
  interact with their topic.
- **Ten skills ship with the implementation** (Section 2.4): three that file and extract what comes in, four that
  exercise judgment over what is already there, two that work a session with the human, and `voice/`, which holds the
  human's own writing style. They mount read-only from the package ahead of the Knowledge Base's own `skills/` folder,
  which stays empty until the human adopts one, and adoption forks that skill permanently. Overloadable per topic,
  like every other skill.
- **A session** is a channel the human works in for as long as the work lasts, opened on one expert or on the
  Librarian when the work crosses topics. It might be research, a discussion, or weeks of trying things and reporting
  back, and the system asks the human to declare none of that in advance. A session that does research has the expert
  searching with the goal and none of the human's beliefs, verifying every URL and quotation in code, weighing what
  survives against the human's notes without touching one, and arguing with the human while they can still act on it.
  Search comes from the model provider the deployment already pays and returns page text rather than links, so a
  quotation is checked against the bytes it came from; an outage of that provider stops research and leaves the expert
  answering from what the topic already holds. `/learn` proposes what was learned, at any time and as often as it is
  worth asking; `/close` ends the channel. Most channels file nothing, which is the ordinary outcome. A channel that
  files something leaves practical knowledge the human earned, as a note stamped `provenance: practised`, and the
  **session summary** under `sessions/`, stamped `provenance: researched`. The session's running file, the **session
  record**, stays in the session's own workspace and never enters the tree.
- **What a session may conclude is bounded** (Section 2.8). Five kinds of session output look like knowledge and are
  not: an approach that never worked, a failure caused by the machine that week, a verdict that a tool cannot do
  something, an error that a retry cleared, and the story of one afternoon. The system files a lesson only when the
  human earned it and approved its exact text, and filing nothing is the default rather than a missed opportunity.
  One question stays open, and Section 2.8 raises it without settling it: may a session teach the system **how to
  work**, and not only what is true? A self-written skill has nowhere to live today.
- The **DeepAgent harness** hosts the agent layer and exposes it through a dedicated TUI, Telegram channels, and other
  channels. Users can connect to the Librarian or directly to a specific Topic Expert Agent.
- The **Project Manager** (separate project) consumes the Knowledge Base through context packs and feeds project
  outcomes and lessons learned back into it.

All components work under **human strategic control**. AI remains tactically brilliant. Humans retain the strategic
vision.