# Personal Knowledge Base: Design Specification

---

## Guiding Principles

- A successful AI-driven future depends on human–AI synergy. This synergy needs a clearly defined, active human role.
- Breadth of knowledge drives creativity and novel insights more than depth of knowledge does (David Epstein, *Range*).
- When solving problems, humans go wide; AI goes deep (Marc Andreessen).
- AI agents are tactically brilliant but strategically inept (author).

---

## System Overview

This project is the **Personal Knowledge Base (PKB)**: a structured, hierarchical repository of knowledge, built on
the three pillars named below. *(Knowledge + Experience = Wisdom)*

The Knowledge Base is one of two components of the **Personal Companion**, an agentic system that enables a
self-improving AI companion/assistant that learns from its own experiences and from those of its human operators. The
other component, the **Project Manager**, is maintained as a separate project. It is an orchestration engine that
decomposes project objectives into a hierarchy of OKRs and creates specialized agents to execute them.

All interactions with the Knowledge Base are agent-mediated. A root PKB agent, the **Librarian**, routes inbound
information and requests to per-topic **Topic Expert Agents**. The agent layer runs on the **DeepAgent** agentic
harness, which exposes the PKB through a dedicated TUI, Telegram channels, and other access channels.

**Feedback Loop**: Projects use the Personal Knowledge Base to find the best ways to achieve objectives. Project
outcomes and insights feed back into the Knowledge Base.

**This document specifies the design; it is not a manual.** To go from a clone to a knowledge base with something
in it, read [`docs/how-to/getting-started.md`](docs/how-to/getting-started.md), and then, for the phone,
[`docs/how-to/telegram.md`](docs/how-to/telegram.md).

---

## The Three Pillars

The Knowledge Base holds three kinds of knowledge, and every topic has a place for each:

| Pillar | Folder | It holds |
|--------|--------|-----------|
| **Theoretical** | `references/` | Knowledge others established. Books, papers, articles, anything read. |
| **Practical** | `notes/` | Knowledge the human established by doing, under their own conditions. |
| **Procedural** | `skills/` | How the human and the agent work together toward a goal the human set. |

*Knowledge + Experience = Wisdom* names the first two pillars. The third says how the human and the agent spend that
wisdom. Scaffolding a new topic creates `references/` and `notes/`; `skills/` appears with the topic's first approved
skill (Section 1.2).

The procedural pillar is file class 3 of Section 1.4. A skill carries no PKB frontmatter, appears in no `index.md`,
and contributes no tags, so every rule below that reads frontmatter passes it by.

**The internet is a source, and it feeds the theoretical pillar.** A page a session finds enters the way a book does,
once the human accepts it and the topic ingests it (Section 2.7). The tree gives a fetched article no category of its
own, and `provenance` (Section 1.4) records the route it took.

**Order of standing is a separate question.** Rule 1 in Section 1.8 ranks the practical pillar above the theoretical
one: a human note that disagrees with a reference wins, and the reference is neither edited nor tagged. The
procedural pillar sits on its own axis and takes no place on that ladder, because a skill says how to work and not
what is true (Section 2.8).

The pillars classify what a topic knows. `sessions/` records how the topic came to know it, so a session summary
takes its own rank in a context pack, below the human's own summaries and above the notes under review (Part 4).

The theoretical and practical pillars each carry a human-approved breadth file, `references/summary.md` and
`notes/summary.md` (Section 1.6). The human has asked for a third over the procedural pillar, and Section 2.8 records
the decision it needs.

---

# Part 1: Knowledge Base Design

## 1.1 Goals & Concepts

The Knowledge Base serves five primary goals:

1. **Fuse the practical pillar with the theoretical one** to create a richer, more usable body of knowledge, and
   capture in the procedural pillar the ways of working that fusion produces (The Three Pillars, above).
2. **Optimize agent context windows** based on the agent's role:
    - **Research agents** need a broad, shallow view across many topics and solutions.
    - **Implementation agents** need a deep, focused view of a specific domain.
3. **Make every interaction agent-mediated and frictionless**. Users and external agents work through the Librarian
   and Topic Expert Agents (see Part 2), capturing, retrieving, and refining knowledge in dialog, with no manual file
   management and no external tools, over any connected channel (TUI, Telegram, ...).
4. **Enforce common standards while preserving topic depth**. Harness maintenance hooks and shared skills keep
   structure, metadata, tags, and conflict handling identical across topics; each Topic Expert Agent adds unique
   domain knowledge and topic-specific organization on top.
5. **Grow the Knowledge Base by working, as well as by capture**. The human opens a channel on an expert and works
   in it for as long as the work lasts: research on a goal the topic cannot meet, a discussion, or weeks of trying
   things and reporting how they went. The work that leaves something behind feeds the practical pillar with
   knowledge the human earned by doing, or the procedural pillar with a way of working they settled (Sections 2.7
   and 2.8).

## 1.2 Standard Topic Structure

The Knowledge Base is a hierarchical folder tree. Each topic root uses the following structure:

```
[Topic Root]/
├── topic.md            # BREADTH – Human-approved overview and map
├── index.md            # DEPTH – Machine-generated canonical index (incl. tag subtree)
├── references/         # THEORETICAL PILLAR – what others established (books, papers, articles)
│   ├── summary.md      # BREADTH – Human-approved overview of all references
│   └── [source-name]/  # One folder per ingested document
│       ├── [source-name].md  # DEPTH – Map of the source: a section per part, a bullet per argument
│       └── [source-files]
├── notes/              # PRACTICAL PILLAR – what the human established by doing
│   ├── [note-title].md # Note content (standalone or main file in note folder)
│   ├── [note-title]/   # Optional folder for notes with media
│   │   ├── [note-title].md  # The note text
│   │   └── media/      # Images, screenshots, videos, etc.
│   └── summary.md      # BREADTH – Human-approved distilled rules and solutions from all notes
├── (optional) skills/  # PROCEDURAL PILLAR – how this topic is worked (Section 2.4)
│   └── [skill-name]/
│       └── SKILL.md
├── (optional) sessions/   # What a session worked out (Section 2.7) – an extension folder
│   └── [goal-title].md    #   the goal, what was asked, the sources kept and rejected, the synthesis
├── (optional) expert.md   # Topic Expert override – defaults to the PKB template (Section 2.3)
├── (optional) [topic-specific]/ # Human-approved extension folders, e.g., recipes/ for Cooking
└── (optional) sub-topics/ # Deeper nested topics with the same structure
```

The three pillar folders hold everything the topic knows. `topic.md` and `index.md` map them, and `sessions/` records
how the topic came to know it. The topic's first approved skill mints the `skills/` folder, and that approval is the
approval on the folder; Section 2.4 says which skills live there and which live at the Knowledge Base root.

**Naming convention for folder-hosted items**: Every item placed inside its own folder uses a main content file named
after the item itself:

- `notes/[note-title]/[note-title].md`
- `references/[source-name]/[source-name].md`

The same convention applies inside `sessions/` and inside topic-specific extension folders (e.g.,
`recipes/[recipe-title].md`, or `recipes/[recipe-title]/[recipe-title].md` with media).

`sessions/` is a topic extension folder, like `recipes/` on Cooking, and the human approves it once: the first summary
a topic files mints the folder, and minting an extension folder already waits for the human (Section 1.9). A topic
whose sessions never produced a summary carries no `sessions/` folder, for the same reason a topic that derived
nothing from a source gets no folder under `references/`: an empty folder claims work that nobody did.

Do not use generic `index.md` for item content. The topic-level `index.md` remains the machine-generated canonical
directory index.

**One file per source, and it is a map of that source.** `[source-name].md` carries the source's thesis, its
provenance, one section per part of the source as the source names them, one bullet per argument the topic cares
about, and an honest record of what was not read. The word *summary* names the failure this shape prevents: a
confident write-up of the part that fit in one context window, with nothing recording that the rest was never opened.
A source may be re-ingested as often as it is worth re-ingesting, and each pass reconciles with what is already there
and appends what it covered, what it skipped, and when.

## 1.3 File Types and Creation Rules

| File                              | Built By        | Purpose                                                                                                                              |
|-----------------------------------|-----------------|--------------------------------------------------------------------------------------------------------------------------------------|
| `topic.md`                        | **AI + Human**¹ | Breadth map for research agents. AI drafts and maintains the overview; the human adds insight and approves.                          |
| `index.md` (topic root)           | **Hooks**       | Depth index for precise retrieval, incl. the topic's tag subtree and cross-topic mappings. Regenerated by harness hooks on change.   |
| `expert.md` (optional)            | **Human + AI**  | Topic-specific override of the PKB Topic Expert template (Section 2.3). Human-created; AI assists.                                   |
| `skills/[skill-name]/SKILL.md` (optional) | **Human + AI** | The procedural pillar for one topic: a skill only this topic's expert loads (Section 2.4). Human-created or approved; AI assists. A write here is gated, and it is the one skill path that sits inside the expert's own subtree. |
| `references/summary.md`           | **AI + Human**¹ | Breadth overview of the theoretical pillar. AI drafts the summary. Human edits and approves it.                                      |
| `references/[source]/[source].md` | **AI**, then **AI + Human**² | Depth map of one source: thesis, provenance, a section per part of the source, a bullet per argument, and what was not read. Generated by the ingestion skill. |
| `notes/[note-title].md`           | **Human + AI**  | What the human knows from their own practice: an observation, an opinion, or something that worked (tagged `type.solution`). They write it, or they settle it at `/learn` after trying the thing and the expert drafts what they settled (Section 2.7). AI assists with clarity and structure; the human approves the exact text. |
| `notes/summary.md`                | **AI + Human**¹ | Breadth overview of experience: distilled rules and notable solutions. Human edits and approves. **Highest priority for decisions.** |
| `sessions/[goal-title].md`        | **AI + Human**³ | A session's synthesis of what it **read and worked out** (Section 2.7): the goal, the questions asked, every source kept, every source rejected and why. Rendered by the harness from the session record and approved by the human before it lands. |
| `tags.md` (PKB root)              | **Hooks**       | Global tag registry, purely derived from file frontmatter. Regenerated mechanically whenever files change.                           |
| `index.md` (PKB root)             | **Hooks**       | Root catalog: every topic with its description, aggregated from `topic.md` frontmatter – the Librarian's routing view.               |
| `skills/[skill-name]/SKILL.md` (PKB root) | **Human + AI** | The procedural pillar for every topic: a skill every expert loads (Section 2.4). The folder starts empty. A write here sits outside every expert's subtree, so it needs a gated tool the implementation does not yet provide (Section 2.4). |

¹ **"AI + Human"** means the AI proposes a draft and the human approves or edits it before finalization.

² The **first** write of a source file is un-gated, because capture stays frictionless. A **re-ingestion that rewrites one**
is approved by the human first: the rewrite lands on top of an extraction they have already read and relied on.

³ A session drafts from its own **session record**, which lives outside the Knowledge Base tree (Section 2.7). Every
write under `sessions/` waits for the human, first file and every later one, and they approve the exact text that will
land.

**Collaboration rule**: The **practical and procedural pillars are human-generated, AI-curated**. `notes/`, the
`skills/` folders, and `expert.md` overrides carry the human's own experience and their own ways of working, and the
AI assists with clarity, grammar, and structure. Every other meaning-carrying file (`topic.md`, the breadth
summaries, a session's summary) is **AI-generated, human-curated**: the AI drafts, the human adds insight and
approves. The theoretical pillar's depth files are AI-generated on first ingestion; the human curates them at the
summary level, and approves any later pass that rewrites one. Mechanical files (`index.md`, root `tags.md`) are
generated by hooks and curated by no one.

An expert may draft a note or a skill itself at `/learn`, and both stay on the human-generated side of that line. The
experience in them is the human's: they cooked it, they ran it, they came back and said what happened. The expert
drafts the wording from the session record, argues about what the experience means (Section 2.7), files the text the
human approves word for word (Section 2.8), and never argues about whose experience it was.

**Skill files are a file class of their own.** Everything under a `skills/` folder, at the PKB root or inside a topic,
is instruction for agents and not knowledge about a subject. It follows the harness's own skill format and is exempt
from the PKB rules that govern knowledge files (Section 1.4). Forcing the PKB fields onto a `SKILL.md` breaks the
harness's parser and the skill silently disappears.

## 1.4 Metadata Requirements

Every human- or AI-authored markdown file **that carries knowledge** includes YAML frontmatter. There are three file
classes and only the first is governed by this section:

1. **Knowledge files** – notes, references, the breadth summaries, `topic.md`, session summaries, and anything in a
   topic-specific extension folder. Full PKB frontmatter, as below.
2. **Machine-generated files** – `index.md` at any level and the root `tags.md`. Minimal generated frontmatter only.
3. **Skill files** – everything under a `skills/` folder, at the PKB root or inside a topic, plus `expert.md`. These
   are agent instructions, not knowledge: nothing here appears in any `index.md` and nothing here contributes tags. A
   `SKILL.md` carries the harness's own two fields, `name` and `description`, and nothing else. Adding the PKB fields
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

The `description` field is required on every **knowledge** file, class 1 above. It is what deterministic `index.md`
generation extracts (Section 1.9). The generated files carry their own minimal frontmatter instead: the root
`tags.md` in Section 1.5 has a `title` and a `source_type` and no description, and asking it for one would be asking
the generators to fail their own validation.

`related_topics` lists related topic paths in tag notation (e.g., `bbq.equipment`). It is the single place where
cross-topic relationships are declared. Harness hooks aggregate these declarations into the registry's cross-topic
mappings (Section 1.9).

Conflict handling adds transient fields: `review_note` while a conflict is open, `last_reviewed` after resolution
(Section 1.7).

`provenance` is a **proposed eighth field** that says **how the knowledge was acquired**. Layer 1 does not recognise
it yet: the schema fixes seven required fields plus `related_topics`, `review_note` and `last_reviewed`, so a file
carrying `provenance` today draws an unknown-field warning and lands anyway. Everything below describes the design
the sessions work needs (Section 2.7). It takes one of four values:

| Value | What it means |
|-------|---------------|
| `practised` | The human did the thing and this is what happened. A lesson settled at `/learn` carries it (Section 2.7). |
| `stated` | The human said it, without having tried it yet. |
| `researched` | A session worked it out, by reading or by argument, weighed against the human's own notes (Section 2.7). |
| `ingested` | It came in through a source: a book, an article, a paper. |

Nothing else in the frontmatter can answer that question. The `type.*` tag restates the folder (Section 1.5), so a
finding taken off the internet and filed under `notes/` looks exactly like experience the human earned. An absent
`provenance` means unknown, and nothing guesses one for the files already in the tree. It records a route and not a
score: the human decides which routes outrank which, and Section 1.7 is where that order is written down.

A session summary carries `provenance: researched` and a note settled at `/learn` carries `provenance: practised`.
The harness stamps both, because the harness renders both files rather than typing them, and validation refuses a
session summary that arrives without one. No such check lands on `notes/`: the notes already in the tree carry no
`provenance`, so a presence rule there would turn them into a wall of errors.

The field keeps the name `researched` even though the folder is called `sessions/`. The folder names the producer and
the field names the route, so renaming the value to match the folder would make the field restate the path, which is
the failure Section 1.5 keeps tags away from.

## 1.5 Hierarchical Tags

Hierarchical tags improve context filtering, inheritance, and agent retrieval. A nested tag makes relationships
explicit, and agents filter at any level of the tree.

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

Because tags are flexible and relational, the PKB maintains a single **`tags.md`** registry at its root: the
canonical relational tree and lightweight ontology for AI ingest: namespace definitions, per-topic subtrees, and
cross-topic mappings. Each topic's `index.md` embeds its own subtree for local depth work.

**Maintenance is fully mechanical**: harness code regenerates the registry by scanning all files and rendering the
full hierarchy, and aggregates the cross-topic mappings from `related_topics` declarations in file frontmatter, with
no LLM tokens spent. The generator supplies static definitions for the standard namespaces (`type.*`, `status.*`).
The registry is purely derived, so by construction it reflects the tags the files use. Governance stays in the
dialog: a Topic Expert proposes a new tag and the human approves it before the expert files content that uses it.

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
is. Sibling order is not editorial. The generator sorts siblings case-insensitively by the full tag string, which is
what makes regeneration idempotent, so an example written in any other order will not be reproduced.

## 1.6 Human–AI Collaboration in the Knowledge Base

The Knowledge Base is a dialog between human and AI. The division of labor follows the pillars: **the practical and
procedural pillars are mostly human-generated and AI-curated; everything else is AI-generated and human-curated**
(purely mechanical files are generated by hooks and curated by no one).

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

### Skills

- The human writes or approves every skill, whoever typed the draft.
- The AI drafts the wording, from the human's dictation or from a session's own record.
- The human approves the exact `SKILL.md` text, on an approval naming the scope and any shipped skill it shadows.
- Section 2.4 decides where the file lands.

### Sessions

- The human opens a channel on an expert and works in it for as long as the work lasts (Section 2.7).
- The expert argues throughout, and it argues about the human's own conclusions as readily as about a page it found.
  Objecting while the human can still act on it is the job; a retrospective is too late.
- The expert drafts what the session concluded and the human approves the exact text that lands, a note or a skill
  alike. Most sessions file nothing. Section 2.8 says what a session is allowed to conclude.

### Agent roles in the dialog

The topic's Topic Expert Agent (see Part 2) runs this dialog. It proposes drafts and detects conflicts through the
common judgment skills (Section 1.9), while harness maintenance hooks keep the structure consistent. Collaboration
skills (Section 2.4) shape the dialog itself: the expert writes drafts in the human's `voice` and runs idea discovery
under `discovery`. Work in a channel follows Section 2.7. The expert never finalizes human-approved content on its
own.

## 1.7 Conflict Detection & Resolution

### General rule

The practical pillar outranks the theoretical one (The Three Pillars, above).

Human content includes human-written notes and human-approved summaries. If a human note conflicts with a reference,
the note is correct. If it is not correct, the human edits the note until it wins.

A page a session found on the internet holds no standing at all, and it holds none until the human accepts it.
Accepting it feeds the theoretical pillar through ordinary ingestion, and it then ranks as any reference does
(Section 2.7).

The procedural pillar takes no place on that ladder. A skill and a note disagree about how work is done rather than
about what is true, so the scan raises the pair and the human settles it (see *Conflict tagging* below).

The system does not overwrite human content. The system only brings conflicts to human attention and tracks resolution.

One case has no human side, and the rule above does not decide it: a second reading of a source producing an argument
that contradicts the one already in that source's file. Both sides are extractions. The machinery is the same, flag,
change nothing, let the human settle it, and what gets flagged is a reference (see *Conflict tagging* below).

### Conflict types

| Type            | Description                                             | Example                                            |
|-----------------|---------------------------------------------------------|----------------------------------------------------|
| `contradiction` | Statements directly oppose each other                   | "Preheat grill 15 min" vs. "Preheat grill 10 min"  |
| `nuance`        | Statements are both true but under different conditions | "High heat for searing" vs. "Low heat for smoking" |
| `outdated`      | Static knowledge is older and no longer accurate        | 2010 book vs. 2024 human note                      |

### Detection process

1. **Trigger**: A harness maintenance hook schedules a conflict scan whenever files under `notes/`, `references/`,
   `skills/`, `sessions/`, or a topic-specific extension folder are created **or modified**, because an edited note can newly
   contradict a reference that was fine yesterday. A session summary enters the scan like any other knowledge file
   once it lands, and a session in progress writes nothing for the scan to find (Section 2.7). The human can also
   request a scan on demand, and that request is the route that works today: the daemon starts no scan worker, so
   requests accumulate in the queue on every filing turn and nothing drains them (Section 1.9).
2. **Method**: The Topic Expert Agent executes the scan on five axes.
   Two compare the practical pillar against the theoretical one: `notes/summary.md` against `references/summary.md`,
   and individual notes against references. A third compares notes against notes inside the practical pillar, the same
   person at different times under conditions they did not write down. On a re-ingestion a fourth compares the fresh
   extraction of a source against the source file already on disk, argument by argument, because a bounded reader
   handed two long documents answers confidently about the part it managed to read. The fifth compares the practical
   pillar against the procedural one: the topic's notes against the skills that topic loads, which is where a skill
   that hardened around a transient failure meets the note saying the thing works now (Section 2.8). Skill files sit
   outside every `index.md` and contribute no tags (Section 1.4), so the scan reaches them by path and not through the
   registry. The scan uses semantic analysis informed by the expert's domain knowledge, recognizing when two
   statements are both true under different conditions.

   **Three of the five run today.** The shipped prompt asks for the two summaries, notes against references, and notes
   against notes. The re-ingestion axis and the practice-against-procedure axis are designed and not built.
3. **Classification**: The AI proposes a conflict type and a confidence score.

### Conflict tagging

When the AI detects a conflict with human content, it must do these steps:

1. Add the tag `status.conflict-review` to the human content file.
2. Add a short `review_note` to the file metadata. The note describes the conflict.
3. Do not change the file content automatically.

The reference is neither tagged nor edited, except in the one case where the conflict is between two readings of the
same source. There the same three steps apply to the **source file**: tag it `status.conflict-review`, add the
one-line `review_note`, change nothing. The human settles which reading is right, exactly as they would between a
note and a reference.

A skill takes no tag either, and it cannot: a `SKILL.md` carries the harness's own two fields and no PKB frontmatter
(Section 1.4), so `status.conflict-review` has nowhere to sit. A note that disagrees with a skill is raised in
conversation with both texts quoted, and no file changes until the human edits one of them.

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

The same machinery runs against a lesson the human dictates at `/learn`. Told to file a conclusion the topic's own
notes disagree with, the expert raises the pair, quotes both sides, and changes nothing until the human says which one
stands (Section 2.7).

Two properties of models make this the only safe handling.

- **A model handed retrieved text abandons its own correct answer for it.** Measured across six domains and six
  models, the rate at which a model drops a correct answer in favour of wrong retrieved text runs from 0.16 to 0.31.
  A page with no standing therefore never marks the human's own note as under review. Letting it hands that bias
  write access to a tree with no undo.
- **A model given two passages that contradict each other misses the contradiction.** Handed both sides of a
  contradiction human annotators had already marked, models score under 11%. The design therefore takes the choice of
  pairs away from the model: harness code will pick the pairs by claim-to-claim overlap, the model will only label
  each one, and every pair the code picked gets shown whatever the label says. **That code does not exist.** Today the
  expert compares whole files under the `conflict-detection` skill, so the 11% figure is the risk the design carries
  until the pair picker is built (Section 2.8).

### What the system does not do

- The system does not create a separate conflict registry, so nothing pollutes a context window after resolution.
- The system does not record past conflict details after resolution.
- The system does not mark any note as a loser.
- The system does not store resolution text outside the note, so the note content is the true state of knowledge.
- The system does not keep any marker that a conflict ever occurred: resolved notes are simply `status.approved`
  with a fresh `last_reviewed` date.

## 1.8 Critical Rules

1. **Human content wins**: the practical pillar outranks the theoretical one. Human-written notes and human-approved
   summaries take precedence over references. The AI detects conflicts and tags them with
   `status.conflict-review`. The human resolves the tag. The AI does not change human content automatically.

2. **Breadth vs. Depth**: `summary.md` files and `topic.md` are used for breadth-first research. `index.md` files are
   used for depth-first implementation. Topic Expert Agents assemble context packs accordingly for consumers such as
   the Project Manager (separate project).

3. **Machine vs. Human**: `index.md` files are always machine-built. `summary.md` files and `topic.md` require human
   collaboration. The AI never finalizes them without human approval.

4. **Cross-Topic Solutions**: A solution note lives in exactly one topic, the most relevant one. There are no copies
   of it. Cross-topic discovery is handled by tags, `related_topics` metadata, and Librarian routing.

   This rule is about **solution notes**, and only about them. It does not govern the ingestion of sources: one
   book, paper, article, or clip may be ingested by several Topic Experts, each extracting what its own topic cares
   about (Section 2.2). Those are different extractions of one source, not copies of one file.

   The source material itself is copied on purpose. A topic that **gainfully** ingests a source, meaning it derived
   at least one insight from it, gets its own copy of the original alongside its own extraction, so the topic folder
   stays self-contained and portable; storing a large file more than once is the price, and it is worth paying. A
   topic that derives nothing gets no folder, no stub, and no copy: zero trace, rather than an empty folder implying
   the source was considered and is somehow relevant.

5. **Sub-Topics**: Deeper nested topics follow the same structure recursively. A sub-topic is served by its parent
   topic's Topic Expert unless it has its own `expert.md`, the same resolution pattern as the template override.

6. **Media Handling**: Notes with media use a dedicated folder. The `[note-title].md` inside contains the note text (or
   a machine-extracted textual description of embedded media). Agents read the text instead of parsing binary files.

7. **Tag Discipline**: Use hierarchical tags. The root `tags.md` registry is maintained mechanically by harness
   hooks. Propose new tags to the human. Do not create ad-hoc tags.

8. **Nothing off the internet becomes a note**: the internet is a source and it feeds the theoretical pillar. `notes/`
   holds what the human proved in practice, and the `type.*` tag restates the path rather than adding to it
   (Section 1.5). A finding taken off the internet and filed under `notes/` is then indistinguishable from experience
   the human earned. A session files an accepted article under `references/` and its own synthesis under `sessions/`,
   stamped `provenance: researched` (Sections 1.4 and 2.7). The rule extends to ordinary turns: an expert that
   reached a tool outside the Knowledge Base on a turn may not write a note on that turn, and is told to open a
   channel instead. Nothing enforces that yet. No gate tracks what a turn reached, so this half is designed and not
   built.

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
two clocks: **validation fires per write**, as a gate in front of the write itself, so a file that would break the
standards never lands; **everything derived fires once per agent run**, after the turn, over the files that turn
touched. Regenerating per write is forbidden, because it would rewrite the root `tags.md` several times in one turn.

Per write:

- Validate YAML frontmatter (required fields, tag syntax and depth), file naming conventions, and consistency between
  declared metadata (`topic`, `source_type`, `topic.*`/`type.*` tags) and the file's actual location. Skill files are
  the third file class (Section 1.4): they are checked for placement only, never for PKB frontmatter, and they are
  excluded from index and tag generation everywhere below.

Once per agent run, over the files the turn created, changed, renamed, or removed:

- Update `updated` timestamps.
- Regenerate the topic's `index.md`, including its tag subtree and cross-topic mappings. Because every knowledge file
  carries a `description` in its frontmatter (Section 1.4), index generation is fully deterministic: walk the tree,
  extract the frontmatter.
- Regenerate the root `tags.md` registry from the tags actually used in files, aggregating cross-topic mappings from
  `related_topics` declarations: plain deterministic code, no LLM tokens spent, purely derived.
- Regenerate the root `index.md`, a catalog of every topic and its `topic.md` description, the Librarian's
  one-file routing view.
- Flag broken links and orphaned files.
- Schedule a conflict scan covering the changed files.

Scaffolding the standard structure (Section 1.2) for a new topic or sub-topic is mechanical in the same way, and
happens on human approval rather than on either clock.

> **Implementation note**: "Schedule a conflict scan" means Tier 1 only *queues* the work; the Topic Expert Agent
> (Tier 2) executes the scan. The queue exists and no worker drains it yet (Section 1.7).

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
  a **book** (thesis, then one section per chapter and one bullet per argument), an **article, post, or clip** (the single
  claim and the evidence offered for it), a **manual or reference work** (the parts the topic will actually consult –
  a manual is looked things up in, not read).
- **Research planning and synthesis** – Turn the human's goal into the questions a search will ask, brief one search
  sub-agent per question, weigh what returns against the topic's notes and references, and draft the session summary
  and its synthesis. The questions carry the goal and none of the human's beliefs (Section 2.7).
- **Lesson proposal** – At `/learn`, read the session's own record of what the human tried and draft what was learned
  and what is worth filing (Sections 1.7 and 2.7). The drafting is the skill's work. Picking the pairs that lesson has
  to answer for is harness code, per Section 1.7, and stays code whether the expert wrote the lesson or the human
  dictated it: a skill is a file the human may adopt and then edit, and a guarantee that lives in an adopted copy is a
  guarantee that leaves the day they edit it.
- **Skill proposal** – At `/learn`, draft the `SKILL.md` for a way of working the session established, and say whether
  it belongs to one topic or to every one of them (Section 2.8). It is a second skill rather than a second mode of
  lesson proposal, because the two answer to different tests and land under different approvals: a lesson says what is
  true and a skill says how to work.
- **Sub-topic proposals** – Propose splitting a topic that has grown too large.

A Topic Expert Agent may **overload** any of these with a topic-specific version, so the Cooking expert's
summarization skill may require temperature and doneness tables in recipe summaries. An overload extends the common
procedure and never weakens the general standards, because Tier 1 validates the output whichever skill version
produced it. The same mechanism extends to the collaboration skills of Section 2.4.

### Tier 3: Topic Expert dialog

The Topic Expert Agent runs the judgment skills in dialog with the human: proposing drafts, presenting conflicts, and
collecting approvals (Sections 1.6 and 2.3). It authors content so that Tier 1 stays deterministic, writing the
`description` frontmatter when it files new content. Nobody curates the cross-topic mappings: Tier 1 aggregates them
from `related_topics` declarations into the root `tags.md`, and the **Librarian** (Section 2.2) consults them when
routing across topics.

### Topic creation

When a human requests a new topic (directly, or by approving a topic proposed by the Librarian, Section 2.2):

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
enforce the standards defined in Part 1 (structure, metadata, tags, conflict tagging, and human approval) no matter
which channel a request arrives from.

Where Part 1 says the human writes, edits, or re-tags a file, that action also happens through this dialog: the human
decides, and the agent applies the change on the human's behalf.

## 2.2 The Librarian (Root PKB Agent)

The **Librarian** is the root agent of the Knowledge Base and the default entry point for all inbound information and
requests.

### Routing is a workflow, not a decision

A Librarian turn is four steps. Only the first is a judgment call; the other three are harness code that always runs.

1. **Classify.** Reading the generated root catalog, the Librarian decides which topics the inbound item concerns. It
   answers with a routing call naming the applicable topics and a one-line reason, never prose. This is the only
   step where a model has discretion.
2. **Fan out.** The harness invokes every applicable Topic Expert. This is not something the Librarian may decide to
   skip; it is a step that runs.
3. **Merge by attribution.** The harness composes one reply from what the experts actually returned: each expert's
   own answer, under its own heading, named by its title and its agent id. This is deterministic code, never a second
   model writing a summary of the first. A model asked to write the merge will happily report that *"the Cooking
   expert checked the knowledge base"* when no expert ever ran; a reply assembled from real results cannot say that.
4. **Offer the experts directly.** The reply names the agents that answered, so the human can carry on with one of
   them, "continue with the Cooking expert", instead of going back through the Librarian each time.

A Librarian free to decide whether to delegate sometimes read the topic folders itself and answered from raw files,
losing the topic's skills, its `expert.md` persona and its voice. Everything that makes a Topic Expert an expert
lives one layer down. Harness code closes that.

### When classification is uncertain, ask with a menu

If the Librarian cannot classify an item confidently, the harness asks the human **which experts to engage**, listing
the candidates. The harness lists them, because filing knowledge in the wrong place is not undoable.

The menu appears when the Librarian answers in prose instead of routing (after one stricter retry), when it names no
topic for an item that plainly concerns existing knowledge, or when it says it is unsure. "None of these" is always
an option, and it leads to the topic-gap flow below.

### One source, several experts

Fan-out is not only for questions. **Information fans out the same way, and several experts ingesting one source is
not duplication.** A management book can carry lessons on management *and* on parenting; routed to both, it yields a
reference under Management about leading teams and a reference under Parenting about raising children. Two
extractions of one source, each written through its topic's lens, which is what makes them different and what a
Librarian answering from raw files could never produce.

Each expert decides for itself whether the material has anything for it, and **may decline**. Material that reaches
an expert with nothing in it for that topic, and is not filed, is a correct outcome. A fan-out where two of four
experts file and two decline is a success.

Responsibilities:

- **Routing** – Classify each inbound request or piece of information and fan it out to every applicable Topic Expert
  Agent, then merge what they return into one attributed answer.
- **Topic catalog** – Classify using the root `index.md`: a hook-generated catalog of every topic and its
  description, aggregated from `topic.md` frontmatter. A topic that owns an `expert.md` is marked in the catalog
  itself with *(custom expert)*, so the Librarian sees it in the one file it already reads and never walks the tree
  for it. Nothing is maintained by hand.
- **Topic gaps** – When inbound information fits no existing topic, propose a new topic to the human (following the
  topic creation flow in Section 1.9). Nothing applicable *and* nothing worth choosing between is the gap flow, never
  a menu.
- **Cross-topic coordination** – Use the cross-topic mappings in the root `tags.md` (aggregated mechanically from
  `related_topics` declarations) to notice the second topic worth involving.
- **Work that crosses topics** – Complicated work opens its channel on the Librarian: personal finance and investment
  cross portfolio management and trading, and neither expert holds the whole of it. A goal fans out like any other
  inbound item, and `/learn` fans out the same way with the session itself as the source, each expert filing inside
  its own topic or filing nothing (Section 2.7).

The Librarian holds no deep topic knowledge, writes nothing into the Knowledge Base, and never answers a subject
question out of its own head. It goes wide; Topic Expert Agents go deep. It holds no topic's tools either: a topic's
search sub-agents and its domain tool servers belong to that topic's expert, and the Librarian reaches them by fanning
out to the expert that owns them.

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
  topic**, since the same source reaching two experts should produce two different extractions, and **decline** when
  the material holds nothing this topic cares about.
- Work a channel with the human for as long as the work lasts (Section 2.7): research a goal the topic cannot meet,
  brief read-only search sub-agents, weigh what they bring back against the topic's notes, object while the human can
  still act on it, take their results back as the trials come in, and propose at `/learn` what was learned.
- Carry out the judgment side of topic maintenance (Section 1.9); the mechanical side is enforced by harness hooks.
- Manage topic-specific extensions (with human approval).
- Escalate to the human as required by Part 1: summary approval, new tags, and conflict resolution.

**A source too large for one turn is ingested as a loop.** Classify, draft, file works for a link and fails for a
book: what does not fit the context window is not read, and a single turn writes a confident account of the part it
saw with nothing recording that the rest was never opened. So the harness drives the reading. It segments the source,
extracts argument by argument through a bounded window, writes each section as it goes, records what was skipped and
why, and survives a run that dies part way through a 300-page book. The expert stays the author of the extraction and
no longer decides when it is finished. A source arrives as a path: anything binary is extracted to text first, and
both are kept.

### Example: a Cooking Topic Expert in action

A user connects directly to the **Cooking** Topic Expert Agent. The user needs no external tools, because the agent handles
retrieval, dialog, and filing end to end:

- **Ingest from the web**: The user asks for a steak grilling recipe. The agent fetches candidates from the internet,
  works with the user to adjust the rub and target doneness, and files the final version under `notes/` (or the
  topic-specific `recipes/` folder) with proper metadata and tags.
- **Capture experience**: The user gives feedback after cooking ("the grill behaves differently in windy weather").
  The agent files it as a note and proposes a regenerated `notes/summary.md` for human approval.
- **Combine reference and experience**: The user asks for a grilling recipe from an ingested reference cookbook. The
  agent pulls it from `references/` and applies the temperature specifics of the user's own gas grill from notes
  filed earlier: human experience refining what others established.
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

**This section is the procedural pillar.**

The common skills are loaded by every Topic Expert Agent. They ship with the implementation and are mounted ahead of
the PKB root's own `skills/` folder, which starts empty (Part 3). The mount is read-only because it lives inside the
installed package: a write there edits the implementation for every Knowledge Base on the machine, so the permission
layer denies it to every agent. The tree's own `skills/` folders take writes and are where a skill the human adopts,
writes, or approves at `/learn` lands (Section 2.8).

**The two homes differ in reach, and today they differ in what can reach them.** A topic's `skills/` folder sits
inside that expert's own subtree, so the expert already writes there and the gate stops the write for the human. The
root's folder sits outside every expert's subtree, where the catch-all deny refuses it, and no tool routes a write
there. Filling the root folder therefore needs a gated tool the implementation does not yet provide.

Each skill is a folder holding a `SKILL.md` (`skills/voice/SKILL.md`, `skills/discovery/SKILL.md`), which is the
harness's own format, and what buys progressive disclosure (only the skill's name and description sit in the prompt;
the body is opened when it is needed) and name-collision override resolution without any code of ours. Anything else
the skill needs sits beside its `SKILL.md` in the same folder.

### The ten skills that ship

Ten skills ship in the package today. A shipped skill is a starter draft: it makes something sensible happen on day
one, and it says so at the bottom of its own text. They sort by the pillar each one serves, seven pointed at the
human's subject and three at the procedural pillar. (Section 1.9 calls those three the collaboration skills and the
other seven the judgment maintenance skills.)

**Taking in what arrives from outside the topic.**

- **`ingestion-classification`** decides whether an inbound thing is a reference, a note, or a solution, and drafts
  the file with the metadata that decision implies. It routes to the theoretical pillar or to the practical one, and
  one decision settles the folder, the `source_type` and the `type.*` tag together, so a wrong classification makes
  every part of the file wrong at once.
- **`ingest-paper`** extracts a paper, study, whitepaper or technical report into question, method, results,
  limitations, and the section no paper contains: *does this apply to me*, with the mismatch that drives the answer
  named.
- **`ingest-book`** extracts a book or long report through the source's own chapters, one bullet per argument this
  topic cares about, and keeps *read and took nothing* separate from *never opened* in a reading record at the end.

**Tending the two subject pillars: what the topic already holds.**

- **`summarization`** drafts and revises the three breadth files, `topic.md` and the two summaries. It treats length
  growth as a defect: every revision distils and replaces, and none appends.
- **`conflict-detection`** runs the scan, tags what it finds for the human, and resolves nothing. The conflict type
  and the confidence stay in the conversation, because the Knowledge Base keeps no conflict register.
- **`tag-proposal`** proposes a tag the Knowledge Base has never used by writing the file that needs it and letting
  the write be held, so the human sees the tag and the file it would create in one decision.
- **`sub-topic-proposal`** proposes a split once `notes/summary.md` has stopped distilling and started enumerating. It
  brings the evidence and the full file assignment, and it creates nothing.

**Serving the procedural pillar: how the human and the agent work together.**

- **`research`** explores breadth-first across the Knowledge Base and returns three to five options, each with its
  trade-off and the files that support it. Reaching a note tagged `status.conflict-review` that bears on the question,
  it stops and escalates rather than picking the reading that suits the answer.
- **`discovery`** runs a brainstorming session against Knowledge Base content. It names the tension between two notes
  and the gap a summary keeps implying, pushes back, and files nothing. Anything worth keeping goes back through the
  front door as ordinary ingestion.
- **`voice`** carries the voice profile every draft is written in, narrowed per topic when one subject wants a
  different register from another. The human corrects it from their own edits, and a change to the profile pauses for
  them like any other. It is the one shipped skill that knows something about the human instead of about cooking, and
  Section 2.8 opens the rest of the pillar to a session's own proposals.

**Five of the skills Part 1 describes have not been written, and three of the five are on the session side.**
Section 1.9 names four kinds of source extraction and two of them ship; an article, post or clip and a manual or
reference work are filed by `ingestion-classification` until their own skills exist. **Research planning and
synthesis** has no file: the shipped `research` skill covers the breadth-first pass over the Knowledge Base alone and
says nothing about reaching the internet. **Lesson proposal** and **skill proposal**, which draft the note and the
`SKILL.md` at `/learn`, have no files either. Sections 2.7 and 2.8 specify all three workflows, so the rules those
sections state are rules nothing has been asked to follow yet.

Skills sit on the same side of the collaboration rule as notes, **human-generated and AI-curated** (Section 1.3): the
human authors or approves every one of them, whoever typed the draft. A `SKILL.md` is not a knowledge file
(Section 1.4), and its `name` must match its folder name exactly, for the reason *Where a skill lives* gives below.

### Where a skill lives

The procedural pillar has two homes and the tree resolves both by name. **This is the one place the rule is stated;
every other section cites it.**

- **A skill about one subject** lives in that topic's `skills/` folder, visible to that topic's expert and to nobody
  else.
- **A skill about how to work** is a process skill and lives in the Knowledge Base root's `skills/` folder, where
  every expert loads it.

Changing a shipped skill uses the same two homes. **Adopting** it copies it to the root, where every expert keeps
loading the copy. **Overloading** it copies it into one topic, where that topic's expert loads the copy and the
others keep the shipped default. Both shadow by name, and the permanent-fork warning attaches to the name.

Resolution reads the shipped mount first, then the root folder, then the topic's, and the most specific entry wins
whole-record: a topic's `voice/SKILL.md` *replaces* the root one for that topic instead of merging with it, the same
pattern the harness applies to `expert.md`. An overload extends the default with domain intelligence, a
recipe-writing voice for Cooking or a tasting-session discovery skill, and it never redefines the general standards,
because Tier 1 validates the output whichever skill version produced it.

**The name decides whether a file forks anything, and the name that decides is the `name` in the file's own
frontmatter.** The harness reads every source in order and keeps the last skill declaring a given name, so
`skills/my-research/SKILL.md` declaring `name: research` shadows the shipped `research` from the moment it lands
(Part 3), while `skills/research/SKILL.md` declaring `name: my-research` shadows nothing. Both spellings look right in
a directory listing and the harness only logs a warning, so a Layer 2 diagnostic reports the mismatch. It warns
instead of refusing, and nothing in a running system calls it yet. A `/learn` proposal that would shadow a shipped
skill says so in its approval (Section 2.8).

**A skill is re-evaluated as the work moves on, and not written once.** A procedure hardens around the conditions it
was written in, and those conditions move: the tool that failed gets fixed, the human changes how they want to be
argued with, the topic grows past the shape the skill assumed. Two routes answer that. The conflict scan reads a
topic's skills against that topic's notes (Section 1.7), and a session proposes a revision to a skill a session wrote
(Section 2.8). Neither route reaches a skill the human wrote or adopted, and the scan axis is designed and not built,
so today the human is the only reader of a skill that has gone stale.

## 2.5 DeepAgent Harness and Access Channels

The agent layer runs on the **DeepAgent** agentic harness. DeepAgent hosts the Librarian and the Topic Expert Agents
and exposes them through multiple access channels:

- A dedicated **TUI**
- **Telegram channels**
- Other channels as needed (chat apps, APIs, etc.)

A PKB user can connect to the **Librarian**, the default entry point that routes to the right experts, or connect
**directly to a specific Topic Expert Agent** when they already know which topic they are working with.

The two are joined at step 4 of the Librarian's workflow (Section 2.2). Every expert the Librarian consults is
addressable in its own right, so a reply saying *"the Cooking expert says…"* is also an offer: continue with that
expert directly, in the conversation it has already had.

**A channel is a session** (Section 2.7). One expert holds several channels at once, each named for the goal it works
on, and `/close` ends the session and the channel together. The shipped Telegram surface is six commands, `/new`,
`/threads`, `/agents`, `/pending`, `/cancel` and `/channels`, and a channel there holds several threads. `/learn` and
`/close` are the two commands the session design adds, and neither ships.

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
│   │  LIBRARIAN TURN (Section 2.2)                              │     │
│   │   1 CLASSIFY  ▶  2 FAN OUT  ▶  3 MERGE  ▶  4 OFFER ────────┼─────┼──┐
│   │   model call     harness code, all three                   │     │  │
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
             ▼              ▼
      judgment + collaboration skills (overloadable)
        + harness maintenance hooks (mechanical)
        + read-only search sub-agents (Section 2.7)
             │              │
             ▼              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        KNOWLEDGE BASE TOPICS                         │
│         references/ · notes/ · skills/  (the three pillars)          │
│             + topic.md, index.md, root index.md, tags.md             │
└──────────────────────────────────────────────────────────────────────┘
```

## 2.7 Sessions

A Topic Expert Agent answers from what its topic already holds. A session covers everything past that: the goal the
topic cannot meet, the trials that follow it, and the lesson the human and the expert settle at the end. Capture files
what the human already knew. This is where they find things out, and it is where most of their notes come from.

**A session is a long conversation on one line of work, and research is one of the things it might turn into.** It
might instead be a discussion, an argument about a design, a stretch of trying things and reporting back, or all of
those in sequence. The system holds one shape for every one of them, and a session that searches nothing is an
ordinary session.

**None of this section is built.** No `/learn`, no `/close`, no search sub-agent, no session record and no session
summary exists in the implementation, and `provenance` is not in the schema (Section 1.4). Everything below states
the design, in the present tense the rest of the document uses, and Section 2.8's last subsection lists what the
design itself has not solved.

### A channel is a session

The human opens a channel on an expert and works in it. The channel holds one line of work and lasts as long as that
work lasts, an afternoon or four months. Work begins as a question and becomes whatever it becomes: reading, then
trials, then a verdict, or none of those. The system never asks the human to declare in advance what a conversation
will turn into, because they do not know yet and a wrong declaration is furniture they then have to maintain.

One expert holds several channels at once, each named for its goal. `Trading · Trend Signal` and `Trading · Market
Regime` run side by side, for two reasons, the second the stronger:

- On the human's side, separate lines of work are separate places on the phone, and a phone is where this work
  happens.
- On the model's side, each channel keeps its context on its own goal. A conversation replays its whole history on
  every turn until the history gets large and the harness compacts it. Two goals in one conversation means every
  trend-signal turn re-reads the regime work and gets an invitation to blend the two.

The channel title says which line of work a message went to, and it names the expert as well as the goal, so a
deliberate split is safe where an accidental one is not.

### Simple topics stay direct; complicated ones cross

Cooking is one expert, one subtree, one channel: the human talks to the Cooking expert and nothing else needs to
happen. Personal finance and investment cross, and neither portfolio management nor trading holds the whole of it.
That work opens a channel on the Librarian, which fans every turn out to the applicable experts and merges what they
return by attribution (Section 2.2). The Librarian still writes nothing into the Knowledge Base, and each expert
still writes only inside its own topic. A session reaches outside the topic's own three pillars through search
sub-agents, and a page the search returns ranks below everything the topic already holds until the human accepts it
(Section 1.7).

The one declaration the system does require is which expert a channel opens on. Work that turns out to cross topics
re-opens on the Librarian, and the session record does not carry over: the human names the goal again and the new
channel starts its own record.

### The expert argues with you, and it argues about your own conclusions

Work with the expert the way you would work with a human expert. A good expert objects during the work rather than in
a retrospective. Say the rub needs more sugar, and if the human's own note from March says sugar burned at that
temperature, the expert says so while there is still time to change the rub.

The same holds at `/learn`, where **the human can be wrong**. Told to file *sugar burns above 250*, an expert that
reads trial two running at 260 without burning says so before it drafts a word. Then it files what the human decides,
because meaning is theirs to curate (Section 1.6). Section 1.7 defines the checks behind that objection, run against a
proposed lesson instead of against a retrieved page.

### Two commands

**`/learn`, at any time, as often as it is worth asking.** The expert proposes what it thinks was learned and what is
worth filing, and the human may instead dictate the lesson themselves. Either way the proposal arrives as **an
ordinary message in the channel**, never as an approval, for the reason *Direction is conversation* gives below, and
either way the file that lands takes the same approval on its rendered text. Mid-work use is the point. A channel
that runs for months compacts its own early trials out of the conversation, and something learned in week two is gone
by the time the verdict lands if nobody captured it.

A repeat `/learn` files nothing twice. The session record holds what already landed and where, so a second `/learn`
over the same trials shows that lesson as filed, with its path. Two near-identical solution notes in one topic both
reach every implementation pack and then drift apart, the harm rule 4 in Section 1.8 exists to prevent.

**`/close`, when the work is done.** It ends the session and the channel together, because they are one object. Where
trials have landed since the last `/learn`, the expert offers once before closing, the way a good expert says
*"before you go, I think there is something here"* rather than letting the human walk out. Say no and the channel
closes with nothing filed. Nothing else in the design brings the human back to an open channel; returning is theirs
to do, and the open channel on the phone is the only reminder they get.

### The loop is: try it, report back, distil

Research a rub and a smoking approach, cook, come back with how it tasted, cook again, and distil after the third
attempt. Research a trend-following signal, run several variants, watch which one holds and under what setup, and
distil once the data arrives. Feedback lands in pieces, from wherever the human is, over weeks. The loop is where
`notes/` gets made, and where the procedural pillar picks up what the two of them worked out about working
(Section 2.8).

### A round of research runs as seven steps

A channel runs as many rounds of research as the work needs, and each round runs as harness-encoded steps for the
same reason routing does (Section 2.2). The expert's judgment sets the questions and weighs the answers; the search
and the checking of what it returns are code.

1. **Take the goal.** The human says what they want to know, and the expert writes the goal into the session's own
   file (below). A channel sets as many goals over its life as the work turns up, and each goal gets its own summary
   file: a later turn joins an existing file only when the goal recorded in it matches.
2. **Survey the topic.** The expert reads `topic.md`, both summaries, and the notes and references that touch the
   goal. A goal the topic already meets ends the round here, with the answer and the files it came from. Searching
   the internet for something already filed spends the budget and invites a page that contradicts the human's own
   note.
3. **Write the questions.** The expert turns the goal into one question per line of enquiry, carrying the goal only
   (*The notes weigh the results*, below). Each question carries an objective, the shape
   its answer should take, the sources worth trying, and its boundary against the other questions. Vague briefs are
   the documented cause of two sub-agents researching the same thing while a third researches something nobody asked
   for.
4. **Search.** The harness starts one search sub-agent per question and runs them in parallel. This step is code. A
   model free to decide whether to delegate does not delegate, and Section 2.2 records what that cost the Librarian
   before its fan-out became a step that always runs.
5. **Verify.** Harness code locates every quotation in the text the search returned for the page it came from, and it
   fetches nothing itself (*Code verifies every quotation*, below, carries the measurements). A URL
   the harness holds no text for and a quotation that text does not contain are both **held**, landing under their own
   heading in the session record with the reason they failed.
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
  ┌──── a round of research: steps 1-7 above ────────────────────┐
  │  the expert judges at 1, 2, 3, 6 and 7                       │
  │  harness code runs 4 (one read-only sub-agent per question)  │
  │  and 5 (every quotation found in the held bytes)             │
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
   argues with; then they approve                  offers /learn once if
   the exact text that lands                       trials went unfiled
          │
 ┌────────┴──────┬────────────────┬───────────────┬────────────────┐
 ▼               ▼                ▼               ▼                ▼
 notes/          skills/          sessions/       references/      nothing at all
 what the human  how the work     what the        the accepted     the common
 tried and what  is done, scope   session worked  article and its  outcome: no
 happened:       named in the     out:            original copy    file, no folder,
 provenance:     approval         type.summary    (ordinary        no trace. The
 practised                        provenance:     ingestion)       closed Telegram
                                  researched                       topic keeps it
```

### A session writes goal specs and executes nothing

Work turns up things to do: run this backtest over these three regimes, cook this at 250 for four hours. A session
may write that down as a **goal spec**, which says why the work is being done and what a good outcome looks like, and
stops there. Something else picks the spec up: the Project Manager (Part 4), a coding harness, or the human with a
smoker. Results come back into the channel as conversation and the work carries on. A spec is a message until the
human says it is worth keeping, and then it lands inside the session's synthesis under `sessions/`. The Knowledge
Base grows no task queue, no runner and no status field, because a knowledge base that executes has to remember what
it is halfway through, and that is a second store of truth that can be wrong.

### The session record is the durable file

A session keeps a **session record** and writes into it as the work happens: the goal, each trial and what it
produced, the sources kept and the ones turned down. The record lives in the session's workspace, outside the
Knowledge Base tree, and it survives a restart of the daemon. `/close` ends it with the channel. It is the running
file this section names throughout, and *The session summary* below is the separate thing the human approves into the
tree. The human reads the record by asking the expert for it, the way they ask for anything else in the channel.

The reason is measured. A channel that runs for months compacts, and by the time the verdict lands, week two is one
line of a summary. The expert therefore reads the file and not the transcript, and `/learn` drafts from the file. The
large-source ingestion loop settled this shape already: *"There is deliberately no second store of progress: a second
source of truth about what was read is a second thing that can be wrong, and the one a human can check is the
file."*

### Most sessions leave nothing behind, and that is correct

Ask for a recipe, get one, `/close`. No note, no summary, no folder, no trace in the tree, and rule 4 in Section 1.8
rules the same way for ingestion.

The asymmetry is deliberate. The Telegram topic stays, closed and readable, holding the conversation and the
suggestion the human turned down. The phone becomes the archive of what the tree chose not to keep. That archive
belongs to Telegram: delete the topic and the discarded work is gone, with nothing in the tree able to reconstruct
it. That costs the rule nothing, because discarded work has no claim on the tree either way.

### The search sub-agents read; the expert writes

A search sub-agent holds retrieval tools and no write tool of any kind. The permission layer enforces that, the same
way it confines a Topic Expert Agent to its own subtree: a write tool it never received is a write it cannot make.
The expert authors everything a session produces.

Each sub-agent spends a whole context window on one question and returns a page or two. That compression is the
reason to run one: the expert reads the findings in place of everything the sub-agent had to read to write them.

Three sub-agents is the default width, set by the deployment and never by anything in the Knowledge Base, and the
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
results and for the text of a page, so the design signs up no second vendor and opens no second account. Search takes
one credential, which the daemon reads at startup and hands down, on the path the Telegram token already walks
(`docs/how-to/`). No agent, log or health endpoint sees the value.

**A result arrives as the page's text, and not as a link to it.** Ollama returns thousands of characters of content
per result, so the harness holds what a search sub-agent read at the moment it read it. A quotation is then located in
the exact bytes the claim came from. The check stops being a best effort against a page that may have changed since the
search and becomes a comparison. Every admissibility rule below rests on that.

Search returns extracted text, so ingestion still fetches a source itself. The copy a topic keeps beside an accepted
reference is the original bytes off the web (Section 2.3): search and page reads serve the research, and the ingestion
path serves the filing.

**One provider serves the models and the search, so one outage takes both.** The local fallback model runs in the
hour the cloud is unreachable, and search is unreachable in that same hour, so research stops. A round that cannot
search says so and ends, and the expert goes on answering from the topic's own notes and references on the local
model, at a fraction of the speed. The channel stays open through all of it, and the next round searches again the
first time the provider answers, so nothing polls and nothing queues.

### Code verifies every quotation

Published deep-research agents invent 3% to 13% of the URLs they cite, and 5% to 18% more of the URLs they give do
not resolve. In one shipped generative search product, 51.5% of the sentences it wrote were fully supported by the
citation attached to them. So no URL reaches a session summary on a model's word:

- **Harness code holds the text of every cited page.** Search hands the page's content back with the result. A
  sub-agent that wants a page beyond what its search returned reads it while it is still searching, and the text joins
  the same session record. Verification fetches nothing itself. A citation the record holds no text for keeps its
  claim out of the synthesis, and the record says why the claim carries no weight.
- **Harness code locates every quotation in that held text.** A quotation the text does not contain is dropped, and
  the record says so. The comparison runs against the bytes the claim came from, never against a page fetched again
  later and possibly rewritten in between.
- **The harness never asks a model where a quote sits.** Models miscount positions and invent spans. The sub-agent
  returns the quoted text; code finds it.

The same rule governs an extraction: a quotation a model produces is a candidate until code finds it in the source.

### A page can be written to be read by an agent

Research is the first thing here that pulls text chosen by strangers into the conversation. Retrieved text is
therefore fenced as data everywhere it travels, under a standing instruction that nothing inside the fence is an
instruction. Every quotation a session shows the human is rendered inside a quoted block with its source attached, so
a page's prose never appears in the system's own voice.

That is mitigation and not a cure. Four structural bounds hold behind it: the sub-agent's missing write tool, rule 8
in Section 1.8, quotation verification in code, and the harness-rendered approval.

### The budget bounds quality, and cost is not the reason

The budget exists because a long run is a worse run. Factual accuracy on one measured search agent fell from 79% to
17% as its tool calls rose from 2 to 150. Between 77% and 94% of the steps in a long search add no new evidence, and a
run that reaches the wrong answer runs two to three times longer than one that reaches the right answer. Length is a
symptom before it is a cost.

A **round** carries a step budget and a wall-clock budget. The channel carries neither. Exhausting either budget stops
that round, and the expert says it stopped short of the goal. The human can act on that: they say chase it again with
a narrower question, and the channel is still open for them to say it in.

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
and why, the claims verification held back, the conflicts it raised against the topic's notes, and the synthesis. A
session that searched nothing fills the source sections with nothing and keeps the goal and the synthesis, because a
discussion that argued its way to a conclusion still reached one.

A session with nothing to summarise writes no summary. A session that read one page, cooked from it, and learned one
thing writes a note and no summary. The summary exists to hold what the session **worked out**, and the note exists
to hold what the human **did**.

**The summary is append-only.** A turn adds to the end. Nothing rewrites an earlier entry, because a model asked to
revise a long report across turns removes correct material without saying so and introduces errors while it polishes.
A correction is a new entry naming what it corrects.

**A rejection reaches the tree only through this file.** A candidate the human turns down leaves no folder under
`references/`, no stub, and no copy, per rule 4 in Section 1.8. The reason is theirs, recorded in the words they
typed. Until a summary lands, the rejection lives in the session record; `/close` ends the record, so a session that
files no summary leaves its rejections in the closed channel and nowhere else. A rejection that reaches a filed
summary outlives the channel, and a later session that finds the same page shows it **labelled with the date and the
reason**, at the bottom, rather than hiding it: the page they turned down for one question may be the page they want
for the next one, and a result silently dropped is indistinguishable from a result never found.

**Candidates live with the session and not in the tree.** A page the search returned and the human has not accepted
is held with the session: the text goes when the research that found it ends, and its line in the session record goes
at `/close`. It is not staged, not copied, and not written anywhere under the PKB root. `.inbox/` is where an
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
| Something the human tried, and what happened | `notes/`, stamped `provenance: practised`, tagged `type.solution` when it worked and `type.note` when it did not | Settled at `/learn` in conversation; the human then approves the rendered text |
| The session's synthesis of what it worked out | `sessions/[goal-title].md`, tagged `type.summary`, stamped `provenance: researched` | The human approves the rendered text, before it lands |
| A way of working the session established | `skills/[skill-name]/SKILL.md`, in the topic's folder or in the root's (Section 2.8) | The human approves the rendered text, on an approval that names the scope and any shipped skill it would shadow |
| An article the human accepts           | `references/[source-name]/`, through the ordinary ingestion procedure (Section 2.3), with the topic's own copy of the original | The human names the candidate; the harness will only ingest a page it printed for them and fetched itself. The first extraction is then un-gated, like any other first ingestion |
| A candidate the human rejects          | The rejection list inside the session summary, and the session record until one lands | Nothing, and no other file changes                    |
| Nothing at all                         | Nowhere: no note, no summary, no folder              | `/close`, and this is the common outcome                               |

Rule 8 in Section 1.8 is the line this table draws. A session files everything it **read** as a reference or as a
synthesis, and everything the human **did** as a note, in their own words, after they did it. `provenance` records
which of the two a file is (Section 1.4). The skill row carries no `provenance`, because Section 1.4 exempts the whole
file class from the knowledge fields.

### Direction is conversation; the write is the approval

The human steers a running session by talking to the expert: drop that question, chase this one instead, that source
is no good, that lesson is half right. An approval halts the conversation until it is answered, so spending one on
"those three look right, keep going, but drop the second" would stop the session to ask what the session could have
heard in passing. An approval also accepts only the answers it offered, and that sentence is not one of them. So
`/learn` proposes in an ordinary message.

One approval remains, and it sits on the bytes. For each file that would land, note, summary or skill, the human reads
the exact text and says yes or no. A `/learn` that proposes three notes asks three times, and they may take one and
drop two. A skill asks on its own terms, because the human is agreeing to something different (Section 2.8).
Accepting a source for ingestion is an instruction and not an approval, and the harness will only ingest a page it
printed for the human and fetched itself.

### `/learn` on a Librarian channel fans out

On a Librarian channel, `/learn` treats the session itself as a source and fans it out. Each applicable expert is
asked what its own topic takes from it, with the grammar the ingestion loop already uses section by section:
something new, something better, something that contradicts what I hold, or nothing. An expert that takes nothing
leaves no folder and no stub. Each note lands inside its own topic, so every expert stays in its own subtree and the
Librarian still writes nothing.

A Librarian `/learn` therefore proposes a **set** of notes. The human takes some and drops others, naming them by the
label printed beside each one. Each kept note then asks for its own approval on its own text, so a rejection on one
changes nothing about the rest. The cost is honest: four kept notes means four texts to read.

A session that yields a portfolio lesson and a trading lesson yielded two lessons, the same shape as one book reaching
two topics. An insight that spans the topics instead of decomposing across them lands in one topic with
`related_topics` naming the others, and the hooks aggregate that into the root registry (Section 1.9).

A cross-topic session files **no** session summary. `sessions/` is a topic extension folder and the Librarian writes
nothing, so a Librarian channel's synthesis stays in the channel. A root process skill is the one other thing a
Librarian `/learn` may propose, and the expert that drafted it asks for it (Section 2.8).

The Librarian runs no search of its own and holds no topic's search tools, which would let it answer a subject
question out of its own head (Section 2.2). It reaches a topic's tools through the expert that owns them.

### Domain tool servers belong to a topic

A topic may bring tool servers of its own: a recipe service for Cooking, a case-law service for a legal topic. The
**deployment** binds them to that topic's expert, in the daemon's own configuration, for the same reason the model is
chosen there and not in the tree: configuration an agent can write is configuration an agent can grant itself.
`expert.md` may describe what a topic uses; it never decides what a topic holds. A server is declared for the
expert's own turns or for its search sub-agents, and one declared for the sub-agents never reaches the expert
directly, so a page off the internet cannot enter a note by the side door. They reach no other topic and no
Librarian. A narrow goal then stays with one expert and never becomes a routing problem.

## 2.8 What a Session Is Allowed to Conclude

`/learn` is the moment a session turns a conversation into a claim. Section 2.7 says how it runs and where its output
lands.

### The default is silence

Nous Research shipped **Hermes Agent** in February 2026, and it is the nearest shipped system to this one:
agent-curated memory about the human, plus skills the agent writes for itself after hard tasks. Its prompt states its
prior in capital letters: *"Be ACTIVE. Most sessions produce at least one skill update, even if small. A pass that
does nothing is a missed learning opportunity, not a neutral outcome."*

That prior is right for Hermes and wrong here, and the substrate is the reason. Hermes writes into a skill library
with an archive and a rollback, so a bad write costs somebody a revert. This design writes into a tree with **no
undo**, where a bad note reaches every implementation pack that touches its topic and stays there until a human
notices and rewrites it by hand. So the prior inverts. At `/learn`, filing nothing is the default and the ordinary
outcome, and a session that files nothing has missed nothing.

Hermes also runs no session-end pass at all: its review fires on accumulated tool iterations, and its own
`turn_finalizer.py` notes that `on_session_end()` never runs there. Its cadence is therefore no argument for this
one. Every `/learn` here is typed by a human.

### Five things a session produces that look like knowledge

The same Hermes prompt carries an exclusion list, and it is the most reusable thing in that system. Each entry names a
way a session manufactures something that reads like a lesson. All five hold here, and the first one is the dangerous
one:

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

A session authors four outcomes, and the table in Section 2.7 gives the gate on each: a note holding what the human
tried and what happened, a session summary holding what the session worked out, a skill holding a way of working the
session established, or nothing at all. The table carries two more rows the session does not author, an accepted
article and a rejected candidate.

The bar on the first is three conditions and each one is load-bearing. The human did the thing. They came back and
said what happened. They approved the exact text that lands. An expert that has only the first two has a trial, not a
lesson. The expert argues about what the experience means and it never argues about whose it was (Section 1.3).

### The skills that already generalize

Four of the shipped skills (Section 2.4) do the generalizing work, and `/learn` is one call site among several:

- **`summarization`** turns individual notes into the distilled rules of `notes/summary.md`, which is the file every
  implementation pack loads first. A lesson filed at `/learn` reaches decisions through this skill.
- **`conflict-detection`** catches the human contradicting their own earlier self, which is the case a single session
  cannot see: the week-one note and the week-twelve note were written in different conversations.
- **`discovery`** finds the rule under two notes that never mention each other, and it files nothing, so the finding
  goes back through the front door with its own approval.
- **`voice`** watches for the same edit repeated across three drafts and proposes it as a rule. That is the
  procedural pillar generalizing from experience about the human (Section 2.4).

Two skills for the drafting at `/learn` are described in Section 1.9, **lesson proposal** and **skill proposal**, and
neither ships yet (Section 2.4). The checks either draft has to answer for stay in harness code, per Section 1.7: a
skill is a file the human may adopt and then edit, and a guarantee living in an adopted copy leaves the day they edit
it. That holds hardest for the skill draft, whose own subject is what a skill should say.

### Two guards Hermes puts in code, and both belong here

Hermes enforces two of its safeguards in the tool layer, which is the same choice Section 2.2 made about fan-out and
Section 2.7 made about verification.

**Nothing rewrites a file whose current text it has not read this turn**, and `RS-141` is the rule that carries it.
Hermes refuses a patch to a file the reviewer has not loaded verbatim in that same turn, because *"the autonomous
review fork is allowed to evolve skills, but it must not patch or rewrite content it has only inferred from the
transcript."* A `/learn` proposing to revise a note the human filed in March is working from an impression of that
note, the impression came out of a conversation that has since compacted, and the note may have been edited by
somebody else in the meantime. Read the file, or leave it alone. The rule lives in harness code for the reason
Section 1.7 gives about every other guarantee here: a guard written into a skill leaves the day somebody edits their
copy of that skill.

**Authorship decides what may be curated**, and `RS-142` is the rule that carries it. Hermes tags every skill write
with its origin so that autonomous curation only ever touches skills the autonomous process itself created: *"Skills
a user asks a foreground agent to write belong to the user and must never be auto-curated."* Part 1 already draws that
line as the collaboration rule. The harness writes an authorship record and reads it back, which answers a different
question from `provenance` (Section 1.4): `provenance` says which route the content took, authorship says whose hand
put it there. In a knowledge file that record is a block inside the file. In a skill it sits in a second file beside
the `SKILL.md`, because a `SKILL.md` carries the harness's two fields and no others, and its body loads into a model's
prompt where an origin block would arrive as one more line of procedure. A skill folder with no such record is the
human's, and no autonomous pass amends it.

### A session may also teach the system how to work

**A session feeds the procedural pillar as well as the practical one.** A session that established *brisket holds at
250* fed `notes/`. A session that established *a better way to run a session* has something for `skills/`, and
`/learn` may propose it. `voice` is the seed of that half: it holds a profile of the human, corrected from their own
edits through the same propose-and-approve loop as everything else (Section 2.4).

**A note says what is true. A skill says how to work.** That one line decides every `/learn` proposal, and the test
that separates the two is **who acts on the draft first**. A skill shapes how the expert works before anybody asks a
question, because the harness loads it into the prompt at the start of the turn. A note answers a question about the
subject once somebody asks one, and it is fetched when it is relevant.

Run the test on the three cases that matter. *Brisket holds at 250 for four hours* waits for somebody to ask about
brisket, so it is a note. *Always preheat the grill for 15 minutes* reads as an instruction and is still a note,
because the human at the grill acts on it and no draft changes shape until they ask. *Ask for the pit's own
thermometer offset before drafting any smoking lesson* changes the expert's next draft before the human says anything,
so it is a skill.

**A procedure the human proved by doing is a solution note**, tagged `type.solution` (Section 1.5). It becomes a skill
only when it directs the expert's own drafting instead of the human's own doing. That fork is the one a reader at
`/learn` faces most often, and the loading test settles it.

Section 2.4 decides where the file lands, so a `/learn` filing decides one thing, the scope.

### The four decisions a written skill needs

**A skill write asks for approval, and it asks in its own words.** Every write under a `skills/` folder is already
stopped for the human at either level, so the question is which sentence the human reads while stopped. *A lesson is
ready to file* is the wrong sentence in front of a file that changes how the expert works on every later turn, so the
skill filing carries its own approval naming the scope and any shipped skill it would shadow, with the exact
`SKILL.md` text underneath. Agreeing at `/learn` that the session learned something is agreement about the lesson. The
procedure the expert then wrote from it is a second object the human has not read yet.

**A session revises a skill a session wrote, and never one the human wrote.** The two guards above reach skills
without amendment. Read-before-write means a proposal to revise `skills/session-rounds/SKILL.md` loads that file's
current bytes in the same turn and derives the revision from them. Authorship means a session amends only a folder
carrying the origin record a session wrote, and a folder without one is the human's: they typed it, or they adopted
it, and no autonomous pass touches it. A session that wants such a skill changed proposes the change in conversation
and leaves the edit to the human.

**The expert that ran the session asks for a root process skill, and harness code writes it.** The expert drafts the
text and calls a gated tool; the tool performs the write once the human approves. The Librarian's write capability
stays at zero, as it does for topic creation. Widening an expert's permission so it can write outside its own topic is
refused outright, because that loosens the subtree confinement on every turn to serve one filing that already has an
approval in front of it. **No such tool exists today**, and the root folder is denied to every agent until one does
(Section 2.4).

**A skill that shadows a shipped one by name says so three times.** Section 2.4 gives the mechanism. Nothing in the
tree records that it happened: no index lists the file, no tag points at it, and the harness swaps one skill for the
other without a word. So the proposal says it, the approval says it again with the exact bytes, and the file opens
with the line naming what it shadows, the same line adoption writes (Part 3). The third one matters most, because
whoever ran `/learn` is not the person who opens that file six months later. The collision is never refused.
Improving a shipped skill for one topic is the most useful thing a human can do here, and adoption already blesses it.

### A wrong skill is worse than a wrong note

A wrong note is wrong about brisket. Somebody pulls it into a pack, cooks the thing, and the meat corrects them the
same afternoon. A wrong skill is wrong about every session that follows.

A session in March concludes that the pit runs hot and writes a skill saying *treat every stated temperature as
twenty degrees high before drafting*. The human reads it once, agrees, and it lands. In April the human replaces the
pit. Every draft after that subtracts twenty degrees from a correct number, in every session on that topic, before
anybody asks a question, because the harness loads a skill into the prompt at the start of the turn. The drafts carry
no mark saying which skill shaped them, so the human reads a wrong temperature and corrects the draft instead of the
file. The note they file to correct it says the pit runs true, and catching
that pair is the job of the scan's fifth axis, practice against procedure (Section 1.7), which this design adds for
exactly this case and has not built. Even built, that axis is the weakest reader in the system. A skill appears in no
`index.md` and contributes no tags (Section 1.4), so the scan reaches it by path, `discovery` never surfaces it, and
one model call has to notice that a procedure and a note disagree about a number neither of them states the same way.
The reader most likely to catch it is the human who approved it once, in March.

That axis also has a case it cannot reach at all. The scan compares claims, and a skill phrased as procedure makes
none. *Ask for the pit's own thermometer offset before drafting any smoking lesson* is the same lesson written the way
this section prefers, and no note in the tree agrees or disagrees with it. So the approval in front of a skill is the
guard.

The worst version is a shadow. A topic skill declaring `name: conflict-detection` replaces the shipped scan for that
topic, so a bad skill can switch off the check that would have caught the bad note. Exclusion 3 above is the same
failure at a smaller scale, and Hermes states it: *"These harden into refusals the agent cites against itself for
months after the actual problem was fixed."* A skill is where that hardening happens, because a skill is the file the
system follows without being asked. An approach that never worked, written up as a procedure, becomes a procedure.

### Where a lesson about the human goes, settled

`voice` keeps the human's register and nothing else. A procedure about running a session goes to a root process
skill; a preference about wording goes to `voice`. Splitting them costs one judgment at `/learn`, and merging them
would put *ask for the pit's thermometer offset before drafting* into the file every draft is style-checked against,
where it reads as a rule about prose.

### The procedural pillar has no breadth file, and adding one is a decision

The theoretical and practical pillars each carry a human-approved `summary.md` (Section 1.6), and the human has asked
for the third. Nothing is built for it, because the obvious placement contradicts Part 1. Everything under a `skills/`
folder is a skill file (class 3 in Section 1.4): no PKB frontmatter, no place in any `index.md`, no contribution to
the tag registry. A `summary.md` sitting inside one is either a knowledge file living in a folder the rules exempt,
or a fourth file class this document never defines. Today `Cooking/skills/summary.md` passes content validation with
no findings at all, because the skill class exempts everything under that folder, and the tree walk then warns
`LEGACY_SKILL_LAYOUT`, because a flat markdown file inside `skills/` is the superseded layout and loads as no skill.

Three shapes answer it. A **skills section inside `topic.md`** needs no new file class and gives up a separate
approval surface. **`skills/summary.md` as a fourth file class** carries its own frontmatter rules and its own
exemptions, and costs changes in Sections 1.2 and 1.4 and in Part 3. A **generated file** gives up the human approval
that makes a breadth file worth reading, and Section 1.6 refuses it on that ground.

**Recommended default: the first.** It costs nothing in Part 1, it puts the pillar's overview in the file the
Librarian already routes on, and the approval it gives up is one the human already gives when they approve
`topic.md`. The human picks, and Section 1.2 grows a folder comment when they do.

### Three gaps in the self-improvement loop

The system is meant to improve itself from what it learns in the work. Three things stand between the design as
written and that claim, and each one belongs to a pillar. **No route exists for any of them yet.**

**The system notices nothing on its own.** The conflict scan is the only machinery that reasons over the Knowledge
Base unprompted, and it runs across all three pillars, so it is the natural place for this to live. It does not run:
the daemon builds its application without a scan worker, so requests pile up in the queue on every filing turn,
`/health` reports the scanner disabled, and the only scan that happens today is one a human asks for (Section 1.7). An
agent that reasons when spoken to and never otherwise is a filing system with a good vocabulary.

**The system does not know the human.** `voice` holds how they write, and nothing holds how they decide: what they
have turned down at `/learn` and why, which arguments have moved them, which kinds of evidence they ask for before
they will try something. A record of that would make every proposal better and would also be the most sensitive file
in the tree, which is the reason to design it deliberately instead of accumulating it.

**Nothing decays.** A note from two years ago carries the same weight as one from this morning, and `updated` is the
only field that records the difference. The practical pillar needs an answer here and the theoretical pillar does not:
a book stays as true as it was, while a note about a pit the human no longer owns goes stale without ever becoming
false. The scan catches a later note contradicting an earlier one. It catches nothing where the human stopped doing it
that way.

---

# Part 3: Knowledge Base Layout and Bootstrapping

The full Knowledge Base is a tree of topic roots, each following the standard structure defined in Section 1.2:

```
KnowledgeBase/
├── index.md                # Root catalog: every topic + description (machine-maintained)
├── tags.md                 # Global tag registry (machine-maintained)
├── .inbox/                 # Staging for sources on their way in – dot-prefixed, indexed nowhere
├── (optional) skills/      # PROCEDURAL – process skills every expert loads, plus adopted ones. Starts empty
│   └── [skill-name]/       #   one folder per skill (voice/, discovery/, session-rounds/, ...)
│       └── SKILL.md
├── [Topic Root]/
│   ├── topic.md
│   ├── index.md
│   ├── references/         # THEORETICAL
│   │   ├── summary.md
│   │   └── [source-name]/
│   │       ├── [source-name].md
│   │       └── [source-files]
│   ├── notes/              # PRACTICAL
│   │   ├── [note-title].md
│   │   ├── [note-title]/
│   │   │   ├── [note-title].md
│   │   │   └── media/
│   │   └── summary.md
│   ├── (optional) skills/  # PROCEDURAL – subject skills only this expert loads
│   │   └── [skill-name]/
│   │       └── SKILL.md
│   ├── (optional) sessions/ # Extension folder – appears with the topic's first approved session summary
│   │   └── [goal-title].md
│   ├── (optional) expert.md
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
   `skills/` folder starts empty and an untouched skill improves whenever the implementation does. They are
   functional out of the box and treated as drafts: when the human wants to change one, they **adopt** it. A copy
   lands in a `skills/` folder in the tree, at the root or in one topic (Section 2.4), opening with one line naming
   the shipped skill it now shadows, and it shadows that skill permanently from then on. Adoption is a decision and
   never an accident, because a seeded copy nobody touched is indistinguishable from one the human rewrote, and with
   no undo the implementation would have to choose between overwriting their work and never shipping an improvement.

   The tree's own `skills/` folders take writes, and adoption is one of three routes that fill them. The human
   dictates a skill to the expert; the human adopts a shipped one; or a session proposes one at `/learn` and the human
   approves its exact text (Section 2.8). The permanent-fork warning attaches to the **name** and not to the route,
   and *Where a skill lives* in Section 2.4 states that rule once for all three.
2. **`voice` ships with an opinionated starter profile, corrected from the human's own writing.** Every draft has a
   voice whether or not one is written down, and without a profile it is the model's own, chosen by nobody. A wrong
   default shows up in the first draft and gets fixed; an absent one never does. So the shipped skill states real
   rules, and the human corrects it from whatever writing they already have. A topic may hold its own voice, which
   replaces the root profile for that topic.
3. **The first topics are created on demand.** With zero topics, every inbound item is a topic gap: the human either
   requests topics directly or approves the Librarian's proposals, and each new topic follows the topic creation flow
   in Section 1.9. Nobody designs a taxonomy up front; the tree grows from what the human captures.
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
  the research area. Research agents do not read `index.md` files unless explicitly asked.
- **Implementation agents (depth-first)** receive Implementation Packs built from the full `index.md` of the selected
  topic, detailed `references/[source]/[source].md` files, and relevant solution notes. `notes/summary.md` loads
  first, because human rules have the highest priority.

Every pack ranks the practical pillar above the theoretical one, the same order rule 1 in Section 1.8 sets. A pack
that leads with references and appends the human's notes inverts the one rule the Knowledge Base exists to keep. No
pack carries the procedural pillar: a skill instructs the agents that work this Knowledge Base, and a consumer of a
context pack works somewhere else.

A session summary will enter a Research Pack once the human has approved it (Section 2.7), ranked after the topic's
summaries and before the conflict-review notes. The pack builder has no session role yet, so that ordering is
designed and not built. A lesson a session filed at `/learn` is an ordinary note carrying `provenance: practised`,
and it enters a pack as one.

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
| **Reference Update**    | A new reference ingested (if discovered)            | A relevant article on referral program compliance                    |
| **Conflict Tag Update** | A conflict resolved by the human (tag returns to `status.approved`) | Note updated after review                            |

The standards in Part 1 decide what needs the human's explicit approval, here and on every other channel, and the
caller never decides it. Capturing a note the human dictates and writing a first extraction of a source land
unattended, because capture must stay frictionless (goal 3 in Section 1.1). Changing human-approved content, adding a
tag, creating an extension folder, resolving a conflict, and rewriting an extraction the human has already read all
wait for the human. **Every write under a `skills/` folder waits**, at the root and inside a topic, and it carries its
own approval naming the scope (Section 2.8): a skill gates like human-approved content and never like a capture.
**Every write a session makes waits too**, the summary and the lesson alike, the first and every later one, and what
the human approves is the rendered text and not a request to write it (Section 2.7). Once a change lands, harness
maintenance regenerates the relevant `index.md` files and the root `tags.md` registry.

The five exclusions in Section 2.8 bind a project retrospective as hard as they bind `/learn`, and a retrospective is
where they are easiest to break. A project that tried four approaches and shipped none of them produces a **Summary
Update** proposing the fourth as the recommended one, and the rule it writes reads the same as a rule somebody earned.
A project agent proposing an update names what the project shipped, and a proposal covering work that never worked
says so in the proposal.

---

# Part 5: Conflict Management Example

A human note says "Always preheat the grill for 15 minutes." A reference book says "Preheating for 10 minutes is
sufficient." Section 1.7 carries the rules; this part shows the two states of the file.

The Cooking Topic Expert Agent detects the contradiction and tags the note, changing no text:

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

The human decides the note is correct, leaves the text alone, changes the tag back to `status.approved` and removes
the `review_note`:

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

Had they decided the reference was more accurate, they would have edited the note to "Preheat the grill for 10
minutes" and cleared the tag the same way.

---

# Summary

The **Personal Knowledge Base** is the memory of the Personal Companion:

- It rests on **three pillars**, and every topic has a place for each. The **theoretical** pillar is `references/`,
  what others established. The **practical** pillar is `notes/`, what the human established by doing. The
  **procedural** pillar is `skills/`, how the two of them work together, and it appears with a topic's first approved
  skill. The internet is a source feeding the first and it is no pillar of its own.
- It stores memory, wisdom, and context. It uses hierarchical tags, a machine-maintained global tag registry,
  lightweight tag-based conflict management, and human-approved topic-specific extensions.
- **Human content wins**: the practical pillar outranks the theoretical one, so human-written notes and
  human-approved summaries take precedence over references. The procedural pillar holds no rank on that ladder.
- **Division of labor**: the practical and procedural pillars are mostly human-generated and AI-curated; everything
  else is AI-generated and human-curated, with mechanical files generated by hooks.
- **Breadth vs. depth**: `topic.md` and `summary.md` files serve breadth-first research; `index.md` files serve
  depth-first implementation.
- All interactions are **agent-mediated**. The **Librarian**, the root PKB agent, classifies each inbound item,
  and the harness then fans it out to every applicable **Topic Expert Agent** and merges their answers by
  attribution. Classifying is a model's judgment; fanning out and merging are code. One source may be ingested by
  several experts, each extracting what its own topic cares about.
- **Topic Expert Agents** run each topic, a single PKB template by default, overridable per topic via `expert.md`.
  Harness hooks enforce the mechanical PKB standards; the experts carry out the judgment work through common,
  overloadable skills and add unique domain knowledge, topic-specific file organization, and the best ways to
  interact with their topic.
- **Ten skills ship with the implementation** (Section 2.4), sorted by pillar: three that take in what arrives from
  outside, four that tend what the topic already holds, and three that serve the procedural pillar (`research`,
  `discovery`, `voice`). They mount from the package ahead of the tree's own `skills/` folders, read-only because the
  mount sits inside the installed implementation. The tree's folders take writes, and a file declaring a shipped
  skill's **name** shadows it permanently. Five more skills the design describes have not been written.
- **A session** is a channel the human works in for as long as the work lasts, opened on one expert or on the
  Librarian when the work crosses topics (Section 2.7). It might be research, a discussion, or weeks of trying things
  and reporting back, and the human declares none of that in advance. `/learn` proposes what was learned, at any time
  and as often as it is worth asking; `/close` ends the channel. Most channels file nothing. A channel that files
  something leaves a note stamped `provenance: practised`, or a **session summary** under `sessions/` stamped
  `provenance: researched`, or a skill, or any combination of the three.
- A session that researches searches with the goal and none of the human's beliefs, verifies every URL and quotation
  in code against the page text the provider returned, weighs what survives against the human's notes without
  touching one, and argues with the human while they can still act on it. An outage of that provider stops research
  and leaves the expert answering from what the topic already holds. **None of the session machinery is built.**
- **What a session may conclude is bounded** (Section 2.8). Five kinds of session output look like knowledge and are
  not: an approach that never worked, a failure caused by the machine that week, a verdict that a tool cannot do
  something, an error that a retry cleared, and the story of one afternoon. The system files a lesson only when the
  human earned it and approved its exact text, and filing nothing is the default.
- **A session may also teach the system how to work.** A note says what is true and a skill says how to work, and the
  test is who acts on the draft first: a skill shapes the expert's next turn before anybody asks a question. A wrong
  skill is worse than a wrong note for the same reason, and it marks nothing it shaped. The scan's fifth axis reads a
  topic's notes against its skills to catch that; it is the weakest reader in the system and it is not built, so the
  human who approved the file stays the one most likely to catch it. Section 2.8 also records what the design has not
  solved: the daemon runs no unprompted scan, nothing holds what the system has learned about how the human decides,
  nothing decays with age, and the procedural pillar has no breadth file yet.
- The **DeepAgent harness** hosts the agent layer and exposes it through a dedicated TUI, Telegram channels, and other
  channels. Users can connect to the Librarian or directly to a specific Topic Expert Agent.
- The **Project Manager** (separate project) consumes the Knowledge Base through context packs and feeds project
  outcomes and lessons learned back into it.

All components work under **human strategic control**. AI remains tactically brilliant. Humans retain the strategic
vision.