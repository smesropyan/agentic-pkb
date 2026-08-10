# Personal Knowledge Base: Design Specification

---

## Guiding Principles

- A successful AI-driven future depends on human–AI synergy, and that synergy needs a defined, active human role.
- Breadth of knowledge drives creativity and novel insight more than depth does (David Epstein, *Range*).
- On a problem, humans go wide and AI goes deep (Marc Andreessen).
- AI agents are tactically brilliant but strategically inept (author).

---

## System Overview

This project is the **Personal Knowledge Base (PKB)**: a hierarchical repository of knowledge, built on the three
pillars named below. *(Knowledge + Experience = Wisdom)*

The Knowledge Base is one of two components of the **Personal Companion**, a self-improving AI assistant that learns
from its own experience and from its operators'. The other component, the **Project Manager**, is a separate project:
an orchestration engine that decomposes project objectives into a hierarchy of OKRs and creates agents to execute them.

Agents mediate every interaction with the Knowledge Base. A root PKB agent, the **Librarian**, routes inbound
information and requests to per-topic **Topic Expert Agents**. The agent layer runs on the **DeepAgent** harness,
which exposes the PKB through a dedicated TUI, Telegram channels, and other channels.

**Feedback loop**: projects read the Knowledge Base to find the best way to reach an objective, and their outcomes
feed back into it.

**This document specifies the design. It is not a manual.** To go from a clone to a knowledge base with something in
it, read [`docs/how-to/getting-started.md`](docs/how-to/getting-started.md), and then, for the phone,
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
wisdom. A new topic is scaffolded with `references/` and `notes/`, and `skills/` appears with its first approved skill
(Section 1.2).

The procedural pillar is file class 3 of Section 1.4. A skill carries no PKB frontmatter, appears in no `index.md`,
and contributes no tags, so every rule below that reads frontmatter passes it by.

**The internet is a source, and it feeds the theoretical pillar.** A page a session finds enters the way a book does,
once the human accepts it and the topic ingests it (Section 2.7). The tree gives a fetched article no category of its
own, and `provenance` (Section 1.4) records the route it took.

**Order of standing is a separate question.** Rule 1 in Section 1.8 ranks the practical pillar above the theoretical
one: a human note that disagrees with a reference wins, and the reference takes no edit and no tag. The procedural
pillar sits on its own axis, because a skill says how to work rather than what is true (Section 2.8).

The pillars classify what a topic knows. `sessions/` records how the topic came to know it, so a session summary
takes its own rank in a context pack, below the human's own summaries and above the notes under review (Part 4).

Each subject pillar carries a human-approved breadth file, `references/summary.md` and `notes/summary.md`
(Section 1.6). The human has asked for a third over the procedural pillar, and Section 2.8 records the decision.

---

# Part 1: Knowledge Base Design

## 1.1 Goals & Concepts

The Knowledge Base serves five goals:

1. **Fuse the practical pillar with the theoretical one** into a richer, more usable body of knowledge, and capture in
   the procedural pillar the ways of working that fusion produces (The Three Pillars, above).
2. **Fit the context window to the agent's role**:
    - **Research agents** need a broad, shallow view across many topics and solutions.
    - **Implementation agents** need a deep, focused view of one domain.
3. **Keep every interaction agent-mediated and frictionless**. Users and external agents work through the Librarian
   and the Topic Expert Agents (Part 2). They capture, retrieve, and refine knowledge in dialog, over any connected
   channel, with no file management of their own and no external tools.
4. **Enforce common standards and preserve topic depth**. Harness maintenance hooks and shared skills keep structure,
   metadata, tags, and conflict handling identical across topics. Each Topic Expert Agent adds its own domain
   knowledge and organization on top.
5. **Grow the Knowledge Base by working, as well as by capture**. The human opens a channel on an expert and works in
   it for as long as the work lasts: research on a goal the topic cannot meet, a discussion, or weeks of trying things
   and reporting how they went. Work that leaves something behind feeds the practical pillar with knowledge the human
   earned by doing, or the procedural pillar with a way of working they settled (Sections 2.7 and 2.8).

## 1.2 Standard Topic Structure

The Knowledge Base is a folder tree. Each topic root uses this structure:

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
approval on the folder. Section 2.4 says which skills live there and which live at the Knowledge Base root.

**Naming convention for folder-hosted items**: give every item inside its own folder a main file named after it.

- `notes/[note-title]/[note-title].md`
- `references/[source-name]/[source-name].md`

The same convention applies inside `sessions/` and inside topic extension folders, so a recipe is
`recipes/[recipe-title].md`, or `recipes/[recipe-title]/[recipe-title].md` when it has media.

`sessions/` is a topic extension folder, like `recipes/` on Cooking, and the human approves it once: the first summary
a topic files mints the folder, and minting an extension folder already waits for them (Section 1.9). A topic whose
sessions produced no summary carries no `sessions/` folder, for the reason a topic that derived nothing from a source
gets no folder under `references/`: an empty folder claims work that nobody did.

**A second `sessions/` folder sits at the Knowledge Base root** (Part 3) and holds the summary of a session that
crossed topics (Section 2.7). Same file shape, different scope: a topic's folder holds what one expert worked out
inside its own subject, and the root's holds what one session worked out across several.

Do not put item content in an `index.md`. The topic-level `index.md` stays the machine-generated directory index.

**One file per source, and it is a map of that source.** `[source-name].md` carries the source's thesis, its
provenance, one section per part of the source as the source names them, one bullet per argument the topic cares
about, and an honest record of what nobody read. The word *summary* names the failure this shape prevents: a confident
write-up of the part that fit in one context window, with nothing recording that the rest was never opened. Re-ingest
a source as often as it is worth re-ingesting. Each pass reconciles with what is there and appends what it covered,
what it skipped, and when.

## 1.3 File Types and Creation Rules

| File                              | Built By        | Purpose                                                                                                                              |
|-----------------------------------|-----------------|--------------------------------------------------------------------------------------------------------------------------------------|
| `topic.md`                        | **AI + Human**¹ | Breadth map for research agents. The AI drafts and maintains the overview; the human adds insight and approves.                      |
| `index.md` (topic root)           | **Hooks**       | Depth index for precise retrieval, with the topic's tag subtree and cross-topic mappings. Harness hooks regenerate it on change.     |
| `expert.md` (optional)            | **Human + AI**  | Topic override of the PKB Topic Expert template (Section 2.3). The human writes it; the AI assists.                                  |
| `skills/[skill-name]/SKILL.md` (optional) | **Human + AI** | The procedural pillar for one topic: a skill only this topic's expert loads (Section 2.4). The human writes or approves it; the AI assists. A write here is gated, and it is the one skill path inside the expert's own subtree. |
| `references/summary.md`           | **AI + Human**¹ | Breadth overview of the theoretical pillar. The AI drafts it; the human edits and approves.                                          |
| `references/[source]/[source].md` | **AI**, then **AI + Human**² | Depth map of one source: thesis, provenance, a section per part of the source, a bullet per argument, and what nobody read. The ingestion skill writes it. |
| `notes/[note-title].md`           | **Human + AI**  | What the human knows from their own practice: an observation, an opinion, or something that worked (tagged `type.solution`). They write it, or they settle it at `/learn` after trying the thing and the expert drafts what they settled (Section 2.7). The AI assists with clarity and structure; the human approves the exact text. |
| `notes/summary.md`                | **AI + Human**¹ | Breadth overview of experience: distilled rules and notable solutions. The human edits and approves. **Highest priority for decisions.** |
| `sessions/[goal-title].md`        | **AI + Human**³ | A session's synthesis of what it **read and worked out** (Section 2.7): the goal, the questions asked, every source kept, every source rejected and why. The harness renders it from the session record and the human approves it before it lands. |
| `tags.md` (PKB root)              | **Hooks**       | Global tag registry, derived from file frontmatter. Regenerated whenever files change.                                               |
| `index.md` (PKB root)             | **Hooks**       | Root catalog: every topic with its description, aggregated from `topic.md` frontmatter – the Librarian's routing view.               |
| `sessions/[goal-title].md` (PKB root) | **AI + Human**³ | The synthesis of a session that crossed topics (Section 2.7), same shape as a topic's. The expert that ran the session drafts it and a gated tool writes it, because the Librarian writes nothing and no expert writes outside its own subtree. |
| `skills/[skill-name]/SKILL.md` (PKB root) | **Human + AI** | The procedural pillar for every topic: a skill every expert loads (Section 2.4). The folder starts empty. A write here sits outside every expert's subtree, so it needs a gated tool the implementation does not yet provide (Section 2.4). |

¹ **"AI + Human"** means the AI proposes a draft and the human approves or edits it before it lands.

² The **first** write of a source file is un-gated, because capture stays frictionless. The human approves a
**re-ingestion that rewrites one**: the rewrite lands on top of an extraction they have already read and relied on.

³ A session drafts from its own **session record**, which lives outside the Knowledge Base tree (Section 2.7). The
`/learn` cycle is the only thing that files anything, whether the human runs it or the harness runs it over a record
whose channel has closed (Section 2.8). Every write under `sessions/` waits for the human, on the exact text.

**Collaboration rule**: the **practical and procedural pillars are human-generated, AI-curated**. `notes/`, the
`skills/` folders, and `expert.md` overrides carry the human's own experience and their own ways of working, and the
AI assists with clarity, grammar, and structure. Every other meaning-carrying file (`topic.md`, the breadth summaries,
a session's summary) is **AI-generated, human-curated**: the AI drafts, the human adds insight and approves. The AI
writes the theoretical pillar's depth files on first ingestion; the human curates them at the summary level and
approves any later pass that rewrites one. Hooks generate `index.md` and the root `tags.md`, and nobody curates them.

An expert may draft a note or a skill itself at `/learn`, and both stay on the human-generated side of that line. The
experience in them is the human's: they cooked it, they ran it, they came back and said what happened. The expert
drafts the wording from the session record, argues about what the experience means (Section 2.7), files the text the
human approves word for word (Section 2.8), and never argues about whose experience it was.

**Skill files are a file class of their own.** Everything under a `skills/` folder, at the PKB root or inside a topic,
instructs an agent rather than describing a subject. It follows the harness's own skill format, and the PKB rules for
knowledge files pass it by (Section 1.4). Forcing the PKB fields onto a `SKILL.md` breaks the harness's parser.

## 1.4 Metadata Requirements

Every human- or AI-authored markdown file **that carries knowledge** includes YAML frontmatter. Three file classes
exist, and this section governs the first alone:

1. **Knowledge files** – notes, references, the breadth summaries, `topic.md`, session summaries, and anything in a
   topic extension folder. Full PKB frontmatter, as below. A session summary in the **root** `sessions/` folder is one
   of these too, and no topic owns it: its `topic` field reads `(cross-topic)`, it carries a `topic.*` tag for each
   topic the session crossed and names them in `related_topics`, and the check comparing a declared topic against a
   file's location has nothing to compare (Section 2.7).
2. **Machine-generated files** – `index.md` at any level and the root `tags.md`. Minimal generated frontmatter only.
3. **Skill files** – everything under a `skills/` folder, at the PKB root or inside a topic, plus `expert.md`. These
   instruct an agent rather than describing a subject: nothing here appears in any `index.md` and nothing here
   contributes tags. A `SKILL.md` carries the harness's own two fields, `name` and `description`, and nothing else.
   Adding the PKB fields breaks the harness's parser, and the harness then drops the skill without an error anywhere.

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

Every **knowledge** file, class 1 above, carries a `description`. Index generation extracts it (Section 1.9). The
generated files carry their own minimal frontmatter instead: the root `tags.md` in Section 1.5 has a `title` and a
`source_type` and no description, and asking it for one would ask the generators to fail their own validation.

`related_topics` lists related topic paths in tag notation, such as `bbq.equipment`. Declare a cross-topic
relationship here and nowhere else. Harness hooks aggregate these declarations into the registry (Section 1.9).

Conflict handling adds two transient fields: `review_note` while a conflict is open, `last_reviewed` after the human
resolves it (Section 1.7).

`provenance` is a **proposed eighth field** that says **how the knowledge arrived**. Layer 1 does not recognise it
yet: the schema fixes seven required fields plus `related_topics`, `review_note` and `last_reviewed`, so a file
carrying `provenance` today draws an unknown-field warning and lands anyway. Everything below states the design the
sessions work needs (Section 2.7). The field takes one of four values:

| Value | What it means |
|-------|---------------|
| `practised` | The human did the thing and this is what happened. A lesson settled at `/learn` carries it (Section 2.7). |
| `stated` | The human said it, without having tried it yet. |
| `researched` | A session worked it out, by reading or by argument, weighed against the human's own notes (Section 2.7). |
| `ingested` | It came in through a source: a book, an article, a paper. |

Nothing else in the frontmatter answers that question. The `type.*` tag restates the folder (Section 1.5), so a
finding taken off the internet and filed under `notes/` looks like experience the human earned. An absent
`provenance` means unknown, and nothing guesses one for the files already in the tree.

**The four routes rank in the order the table lists them: `practised`, `stated`, `researched`, `ingested`, highest
first.** `stated` outranks `researched` because the human's own claim is theirs, and the system knows nothing about
their conditions that they did not tell it. The ranking sits under rule 1 of Section 1.7 rather than beside it: the
pillars rank the folders, `provenance` ranks two files inside one folder.

A session summary carries `provenance: researched` and a note settled at `/learn` carries `provenance: practised`.
The harness stamps both, because it renders both files rather than typing them, and validation refuses a session
summary that arrives without one. No such check lands on `notes/`: the notes already in the tree carry no
`provenance`, so a presence rule there would turn them into a wall of errors.

The field keeps the name `researched` while the folder keeps the name `sessions/`. The folder names the producer and
the field names the route, so renaming the value to match would make the field restate the path, the failure
Section 1.5 keeps tags away from.

## 1.5 Hierarchical Tags

Hierarchical tags improve context filtering, inheritance, and agent retrieval. A nested tag states a relationship, and
an agent filters at any level of the tree.

### Tag syntax

Use dot notation, and each dot adds a level. Examples:

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

- Start a tag with a broad namespace. Add narrower terms as the subject needs them.
- Do not create ad-hoc tags. The AI proposes a new tag and the human approves it.
- Keep tag depth to 4 levels or fewer.
- A nested tag implies its parent. `topic.cooking.grilling` also means `topic.cooking`.
- The AI assembles a context pack from tags, filtering by namespace and depth.
- Sessions add no namespace (Section 2.7). A lesson the human earned files as `type.solution`, a session summary files
  as `type.summary`, and `provenance` carries the difference between the two (Section 1.4).

### Tag registry (`tags.md` at the PKB root)

Tags are flexible and relational, so the PKB keeps one **`tags.md`** registry at its root: the canonical tag tree and
lightweight ontology for AI ingest, holding namespace definitions, per-topic subtrees, and cross-topic mappings. Each
topic's `index.md` embeds its own subtree for local depth work.

**Maintenance is mechanical.** Harness code regenerates the registry by scanning the files and rendering the full
hierarchy, and it aggregates the cross-topic mappings from the `related_topics` declarations in file frontmatter. It
spends no LLM tokens. The generator supplies static definitions for the standard namespaces (`type.*`, `status.*`).
The registry is derived, so it reflects the tags the files use by construction. Governance stays in the dialog: a
Topic Expert proposes a new tag and the human approves it before the expert files content that uses it.

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

Every namespace renders the same way: a nested tag implies its parent, so `domain.*` is a tree as `topic.*` is.
Sibling order is mechanical rather than editorial. The generator sorts siblings case-insensitively by the full tag
string, which is what makes regeneration idempotent, so it will not reproduce an example written in any other order.

## 1.6 Human–AI Collaboration in the Knowledge Base

The Knowledge Base is a dialog between the human and the AI, and the division of labor follows the pillars
(Section 1.3).

### Notes

- The human writes the first draft of each note.
- The AI assists with clarity, grammar, and structure.
- The human approves the final content.
- The AI changes no fact in a note without the human's approval.

### Summaries

- The AI proposes drafts for `references/summary.md` and `notes/summary.md`.
- The human reads the draft, adds insight, removes errors, and decides which rules to keep.
- The AI helps the human fit the new insight to the depth already there, and suggests connections to references,
  related topics, and existing notes.
- The human approves the final summary.

`topic.md` and the two summaries stay separate small files on purpose: each is a compact approval surface. A breadth
file manages the *human's* context window, as `index.md` manages the agents'.

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

The topic's Topic Expert Agent (Part 2) runs this dialog. It proposes drafts and detects conflicts through the common
judgment skills (Section 1.9), and harness maintenance hooks keep the structure consistent. The collaboration skills
(Section 2.4) shape the dialog itself: the expert writes drafts in the human's `voice` and runs idea discovery under
`discovery`. Work in a channel follows Section 2.7. The expert finalizes no human-approved content on its own.

## 1.7 Conflict Detection & Resolution

### General rule

The practical pillar outranks the theoretical one (The Three Pillars, above).

Human content is the human-written notes and the human-approved summaries. A human note that conflicts with a
reference is correct, and a wrong note gets edited by the human until it wins.

A page a session found on the internet holds no standing until the human accepts it. Accepting it feeds the
theoretical pillar through ordinary ingestion, and it then ranks as any reference does (Section 2.7).

The procedural pillar takes no place on that ladder. A skill and a note disagree about how work is done rather than
about what is true, so the scan raises the pair and the human settles it (*Conflict tagging* below).

The system overwrites no human content. It brings a conflict to the human's attention and tracks the resolution.

One case has no human side, and the rule above leaves it undecided: a second reading of a source produces an argument
that contradicts the one in that source's file. Both sides are extractions. The machinery is the same, flag, change
nothing, let the human settle it, and the file flagged is a reference (*Conflict tagging* below).

### Conflict types

| Type            | Description                                             | Example                                            |
|-----------------|---------------------------------------------------------|----------------------------------------------------|
| `contradiction` | Statements directly oppose each other                   | "Preheat grill 15 min" vs. "Preheat grill 10 min"  |
| `nuance`        | Statements are both true but under different conditions | "High heat for searing" vs. "Low heat for smoking" |
| `outdated`      | Static knowledge is older and no longer accurate        | 2010 book vs. 2024 human note                      |

### Detection process

1. **Trigger**: a harness maintenance hook schedules a conflict scan whenever a turn creates **or modifies** a file
   under `notes/`, `references/`, a topic's `sessions/`, or a topic extension folder, because an edited note can
   contradict a reference that was fine yesterday. A topic's session summary enters the scan like any other knowledge
   file once it lands, and a session in progress writes nothing for the scan to find (Section 2.7). **The scan cannot
   reach two things**: a `skills/` file schedules nothing, so axis five below has no trigger, and the root `sessions/`
   folder belongs to no topic. The human can also request a scan, the route that works today. The daemon starts no
   scan worker, so requests pile up in the queue on every filing turn and nothing drains them (Section 1.9).
2. **Method**: the Topic Expert Agent runs the scan on five axes.

   1. `notes/summary.md` against `references/summary.md`.
   2. Single notes against references.
   3. Notes against notes, the same person at different times under conditions they did not write down.
   4. On a re-ingestion, the fresh extraction of a source against the source file on disk, argument by argument,
      because a bounded reader handed two long documents answers confidently about the part it managed to read.
   5. The topic's notes against the skills that topic loads, where a skill that hardened around a transient failure
      meets the note saying the thing works now (Section 2.8).

   Skill files sit outside every `index.md` and contribute no tags (Section 1.4), so the scan reaches them by path
   rather than through the registry. The scan reads for meaning with the expert's domain knowledge behind it, and it
   recognises two statements that are both true under different conditions.

   **Three of the five run today**, axes 1 to 3. The re-ingestion axis and the practice-against-procedure axis are
   designed and not built.
3. **Classification**: the AI proposes a conflict type and a confidence score.

### Conflict tagging

On a conflict with human content, the AI does these three steps:

1. Add the tag `status.conflict-review` to the human content file.
2. Add a short `review_note` to the file metadata. The note describes the conflict.
3. Change no file content.

The reference takes no tag and no edit, except where the conflict lies between two readings of the same source. There
the same three steps apply to the **source file**: tag it `status.conflict-review`, add the one-line `review_note`,
change nothing. The human settles which reading is right, as they would between a note and a reference.

A skill takes no tag either, and it cannot: a `SKILL.md` carries the harness's own two fields and no PKB frontmatter
(Section 1.4), so `status.conflict-review` has nowhere to sit. The expert raises a note that disagrees with a skill in
conversation, with both texts quoted, and no file changes until the human edits one of them.

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

The human reviews the tagged file in two steps:

1. **Decide the content.** Edit the file into the winning version, or leave it unchanged when it is already correct.
2. **Clear the tag.** Change `status.conflict-review` back to `status.approved` and remove the `review_note`. The
   `last_reviewed` date is the only trace the review leaves.

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

On a conflict between two human notes, the Topic Expert Agent does these steps:

1. Detect the conflict.
2. Add the tag `status.conflict-review` and a `review_note` to each note.
3. Show both notes to the human.
4. The human edits one note or both into the winning version.
5. The human changes each tag back to `status.approved` and removes each `review_note`.

### Conflicts found during a session

A session that searches compares what it found on the internet against the topic's notes (Section 2.7). A page that
contradicts a note **raises the pair and changes nothing**: the expert shows the human both quotes side by side and
leaves the note byte for byte as it was. No tag, no `review_note`, no reordering. The three steps above start once the
human accepts the source and it lands as a reference, and the ordinary scan then runs on it like any other.

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
  pairs away from the model: harness code will pick the pairs by claim-to-claim overlap, the model will label each one,
  and every pair the code picked gets shown whatever the label says. **That code does not exist.** Today the expert
  compares whole files under the `conflict-detection` skill, so the 11% figure is the risk the design carries until
  somebody builds the pair picker (Section 2.8).

### What the system does not do

- It builds no separate conflict registry, so nothing pollutes a context window after the resolution.
- It records no detail of a past conflict once the human resolves it.
- It marks no note as a loser.
- It stores no resolution text outside the note, so the note content is the true state of knowledge.
- It keeps no marker that a conflict ever happened. A resolved note is `status.approved` with a fresh `last_reviewed`
  date.

## 1.8 Critical Rules

1. **Human content wins**: the practical pillar outranks the theoretical one. A human-written note and a
   human-approved summary take precedence over a reference. The AI detects a conflict and tags it
   `status.conflict-review`. The human resolves the tag. The AI changes no human content on its own.

2. **Breadth vs. depth**: `summary.md` and `topic.md` serve breadth-first research. `index.md` serves depth-first
   implementation. A Topic Expert Agent assembles a context pack on that split, for a consumer such as the Project
   Manager (separate project).

3. **Machine vs. human**: a machine builds every `index.md`. `summary.md` and `topic.md` need the human, and the AI
   finalizes neither without their approval.

4. **Cross-topic solutions**: a solution note lives in one topic, the most relevant one, and nothing copies it. Tags,
   `related_topics` metadata, and Librarian routing carry the cross-topic discovery.

   This rule governs **solution notes** alone. It leaves the ingestion of sources open: several Topic Experts may
   ingest one book, paper, article, or clip, each extracting what its own topic cares about (Section 2.2). Those are
   different extractions of one source rather than copies of one file.

   The source material itself is copied on purpose. A topic that ingests a source **gainfully**, deriving at least one
   insight from it, gets its own copy of the original beside its own extraction, so the topic folder stays
   self-contained and portable. Storing a large file more than once is the price, and it is worth paying. A topic that
   derives nothing gets no folder, no stub, and no copy: zero trace, rather than an empty folder implying the source
   was considered and is somehow relevant.

5. **Sub-topics**: a nested topic follows the same structure recursively. Its parent topic's Topic Expert serves it
   unless it holds its own `expert.md`, the same resolution the template override uses.

6. **Media handling**: a note with media takes its own folder. The `[note-title].md` inside holds the note text, and a
   machine-extracted description of any embedded media. Agents read that text rather than parsing a binary file.

7. **Tag discipline**: use hierarchical tags. Harness hooks maintain the root `tags.md` registry. Propose a new tag to
   the human. Create no ad-hoc tag.

8. **Nothing off the internet becomes a note**: the internet is a source and it feeds the theoretical pillar. `notes/`
   holds what the human proved in practice, and the `type.*` tag restates the path rather than adding to it
   (Section 1.5). A finding taken off the internet and filed under `notes/` is then indistinguishable from experience
   the human earned. A session files an accepted article under `references/` and its own synthesis under `sessions/`,
   stamped `provenance: researched` (Sections 1.4 and 2.7). The rule extends to ordinary turns: an expert that reached
   a tool outside the Knowledge Base on a turn may write no note on that turn, and hears that it should open a channel
   instead. Nothing enforces that yet. No gate tracks what a turn reached, so this half is designed and not built.

   A session **may** file under `notes/` the thing the human went and tried: they cooked it, they ran it, they came
   back and said what happened. They settle that lesson at `/learn`, the expert drafts it from the session's own record
   of the trials, and the human approves it word for word. It lands tagged `type.solution` and stamped
   `provenance: practised` (Section 2.7). The line this rule draws runs between read and done, and `provenance` is
   where the tree records which side a file falls on.

## 1.9 Topic Maintenance Model

> **Design principle**: *Enforce structure mechanically, curate meaning agentically.* No separate maintainer agent
> exists. Harness hooks that no agent can skip or forget perform the deterministic maintenance; the topic's Topic
> Expert Agent (Part 2) performs the judgment work through common, overloadable skills.

Maintenance splits across three tiers.

### Tier 1: Mechanical enforcement (harness hooks)

The DeepAgent harness does this work itself. No agent judges any of it and no agent can skip it. It runs on two
clocks. **Validation fires per write**, as a gate in front of the write, so a file that would break the standards
never lands. **Everything derived fires once per agent run**, after the turn, over the files that turn touched.
Regenerating per write is forbidden, because it would rewrite the root `tags.md` several times in one turn.

Per write:

- Validate the YAML frontmatter (required fields, tag syntax and depth), the file naming, and the agreement between
  the declared metadata (`topic`, `source_type`, `topic.*`/`type.*` tags) and the file's location. Skill files are the
  third file class (Section 1.4): validation checks their placement and never their PKB frontmatter, and every index
  and tag generator below skips them.

Once per agent run, over the files the turn created, changed, renamed, or removed:

- Update the `updated` timestamps.
- Regenerate the topic's `index.md`, with its tag subtree and cross-topic mappings. Every knowledge file carries a
  `description` in its frontmatter (Section 1.4), so index generation is deterministic: walk the tree, read the
  frontmatter.
- Regenerate the root `tags.md` registry from the tags the files use, aggregating cross-topic mappings from the
  `related_topics` declarations. Plain deterministic code, derived, no LLM tokens spent.
- Regenerate the root `index.md`, a catalog of every topic and its `topic.md` description, the Librarian's one-file
  routing view.
- Flag broken links and orphaned files.
- Schedule a conflict scan over the changed files.

Scaffolding the standard structure (Section 1.2) for a new topic or sub-topic is mechanical in the same way, and it
runs on human approval rather than on either clock.

> **Implementation note**: "schedule a conflict scan" means Tier 1 *queues* the work and the Topic Expert Agent
> (Tier 2) runs the scan. The queue exists and no worker drains it yet (Section 1.7).

### Tier 2: Common judgment skills (overloadable)

Work that needs an understanding of content is defined once, as common skills every Topic Expert Agent loads:

- **Summarization** – draft all three breadth files, `topic.md`, `references/summary.md`, and `notes/summary.md`,
  following the dialog rules in Section 1.6. The root catalog is built from `topic.md`'s `description`, so the
  Librarian routes on that field.
- **Conflict detection and classification** – run the scans Tier 1 scheduled and classify the findings per
  Section 1.7.
- **Tag proposal** – propose a new hierarchical tag for the human's approval before filing content that uses it. The
  registry picks it up once a file uses it.
- **Ingestion classification** – classify inbound content as a reference or a note (an observation, an opinion, or a
  solution tagged `type.solution`), and draft the files with their metadata, including the `description` Tier 1 relies
  on and the media descriptions rule 6 in Section 1.8 requires. Hand a source too large for one turn to the chunked
  ingestion loop (Section 2.3).
- **Source extraction, one skill per kind of source** – the skeleton an extraction follows, which is what makes it
  useful rather than a paraphrase: a **paper** (question · method · results · limitations · *does this apply to me*),
  a **book** (thesis, then one section per chapter and one bullet per argument), an **article, post, or clip** (the
  single claim and the evidence offered for it), a **manual or reference work** (the parts the topic will consult,
  because a manual is looked things up in rather than read).
- **Research planning and synthesis** – turn the human's goal into the questions a search will ask, brief one search
  sub-agent per question, weigh what returns against the topic's notes and references, and draft the session summary
  and its synthesis. The questions carry the goal and none of the human's beliefs (Section 2.7).
- **Lesson proposal** – at `/learn`, read the session's own record of what the human tried, and draft what they
  learned and what is worth filing (Sections 1.7 and 2.7). The drafting is the skill's work. Harness code picks the
  pairs that lesson has to answer for, per Section 1.7, whether the expert wrote the lesson or the human dictated it:
  a skill is a file the human may adopt and then edit, and a guarantee that lives in an adopted copy leaves the day
  they edit it.
- **Skill proposal** – at `/learn`, draft the `SKILL.md` for a way of working the session established, and say whether
  it belongs to one topic or to all of them (Section 2.8). It is a second skill rather than a second mode of lesson
  proposal, because the two answer to different tests and land under different approvals: a lesson says what is true
  and a skill says how to work.
- **Sub-topic proposals** – propose a split for a topic that has grown too large.

A Topic Expert Agent may **overload** any of these with a topic version, so the Cooking expert's summarization skill
may require temperature and doneness tables in a recipe summary. An overload extends the common procedure and weakens
no general standard, because Tier 1 validates the output whichever skill version produced it. The same mechanism
extends to the collaboration skills of Section 2.4.

The three skills a session calls, research planning and synthesis, lesson proposal and skill proposal, sit under that
promise too, and they reach it by a different route. `/learn` reads the session record rather than the conversation
(Section 2.8), so it cannot run as an ordinary expert turn, which is handed the conversation. **The harness resolves
the drafting skill by name instead**, the topic's own copy ahead of the root's and the shipped one underneath, the
order an expert's graph resolves in, and hands the body to the distillation. A Cooking session then distils
differently from a Trading one. The same property lets the harness distil a session whose channel has closed: a pass
that needed the conversation would have nothing left to read.

### Tier 3: Topic Expert dialog

The Topic Expert Agent runs the judgment skills in dialog with the human, proposing drafts, presenting conflicts, and
collecting approvals (Sections 1.6 and 2.3). It writes the `description` frontmatter when it files new content, which
keeps Tier 1 deterministic. Nobody curates the cross-topic mappings: Tier 1 aggregates them from the `related_topics`
declarations into the root `tags.md`, and the **Librarian** (Section 2.2) reads them when routing across topics.

### Topic creation

The human requests a new topic, or approves one the Librarian proposed (Section 2.2). Then:

1. Tier 1 scaffolds the standard structure from Section 1.2, with placeholder `summary.md` files.
2. The harness instantiates a Topic Expert Agent for the topic.
3. The expert drafts `topic.md`, proposes the topic's first tag subtree, and asks the human to approve.
4. The human approves each extension folder the topic adds, such as `recipes/` for Cooking, and writes any skill
   overload with the AI's help.

---

# Part 2: PKB Agent Architecture

## 2.1 Agent-Mediated Access

Every interaction with the Knowledge Base goes through an AI agent. Humans and external agents, project agents among
them, read and write no topic file directly. The agent layer and the harness's maintenance hooks (Section 1.9) enforce
the standards Part 1 defines, whichever channel a request arrives from.

Part 1 says the human writes, edits, and re-tags a file, and each of those actions happens through this dialog too:
the human decides, and the agent applies the change on their behalf.

## 2.2 The Librarian (Root PKB Agent)

The **Librarian** is the root agent of the Knowledge Base and the default entry point for everything inbound.

### Routing is a workflow rather than a decision

A Librarian turn is four steps. The first is a judgment call and the other three are harness code that always runs.

1. **Classify.** The Librarian reads the generated root catalog and decides which topics the inbound item concerns. It
   answers with a routing call naming the applicable topics and a one-line reason, never prose. This is the one step
   where a model holds discretion.
2. **Fan out.** The harness invokes every applicable Topic Expert. The Librarian cannot decide to skip it; it is a
   step that runs.
3. **Merge by attribution.** The harness composes one reply from what the experts returned: each expert's own answer,
   under its own heading, named by its title and its agent id. This is deterministic code rather than a second model
   writing a summary of the first. A model asked to write the merge reports that *"the Cooking expert checked the
   knowledge base"* when no expert ever ran; a reply assembled from real results cannot say that.
4. **Offer the experts directly.** The reply names the agents that answered, so the human can carry on with one of
   them, "continue with the Cooking expert", rather than going back through the Librarian each time.

A Librarian free to decide whether to delegate sometimes read the topic folders itself and answered from raw files,
losing the topic's skills, its `expert.md` persona and its voice. Everything that makes a Topic Expert an expert lives
one layer down, so harness code closes that.

### Uncertain classification asks with a menu

Classification that comes back uncertain goes to the human instead: the harness asks **which experts to engage** and
lists the candidates, because filing knowledge in the wrong place cannot be undone.

The menu appears when the Librarian answers in prose instead of routing, after one stricter retry, when it names no
topic for an item that plainly concerns existing knowledge, or when it says it is unsure. "None of these" is always an
option, and it leads to the topic-gap flow below.

### One source, several experts

Fan-out serves more than questions. **Information fans out the same way, and several experts ingesting one source is
no duplication.** A management book can carry lessons on management *and* on parenting. Routed to both, it yields a
reference under Management about leading teams and a reference under Parenting about raising children: two extractions
of one source, each written through its topic's lens, which a Librarian answering from raw files could never produce.

Each expert decides for itself whether the material holds anything for it, and **may decline**. Material that reaches
an expert with nothing in it for that topic, and lands nowhere, is a correct outcome: a fan-out where two of four
experts file and two decline is a success.

Responsibilities:

- **Routing** – classify each inbound request or piece of information, fan it out to every applicable Topic Expert
  Agent, and merge what they return into one attributed answer.
- **Topic catalog** – classify from the root `index.md`, a hook-generated catalog of every topic and its description,
  aggregated from `topic.md` frontmatter. The catalog marks a topic that owns an `expert.md` with *(custom expert)*,
  so the Librarian sees it in the one file it already reads and walks the tree for nothing.
- **Topic gaps** – propose a new topic to the human when inbound information fits no existing one, following the topic
  creation flow in Section 1.9. Nothing applicable *and* nothing worth choosing between is the gap flow, never a menu.
- **Cross-topic coordination** – read the cross-topic mappings in the root `tags.md`, aggregated from the
  `related_topics` declarations, to notice the second topic worth involving.
- **Work that crosses topics** – complicated work opens its channel on the Librarian. Personal finance and investment
  cross portfolio management and trading, and neither expert holds the whole of it. A goal fans out like any other
  inbound item, and `/learn` fans out the same way with the session itself as the source, each expert filing inside
  its own topic or filing nothing. The crossing itself lands in the root `sessions/` folder, written by a gated tool
  so the Librarian still writes nothing (Section 2.7).

The Librarian holds no deep topic knowledge, writes nothing into the Knowledge Base, and answers no subject question
out of its own head. It goes wide; Topic Expert Agents go deep. It holds no topic's tools either: a topic's search
sub-agents and its domain tool servers belong to that topic's expert, and the Librarian reaches them by fanning out.

## 2.3 Topic Expert Agents

A **Topic Expert Agent** runs each topic. One default **Topic Expert template** serves the whole PKB. A topic that
needs behaviour beyond skill overloads overrides the template with an `expert.md` in its topic root. The harness
resolves this on the pattern the maintenance skills use: take `[Topic Root]/expert.md` when it exists, and otherwise
instantiate the PKB template with the topic's context, `topic.md`, `index.md`, the common skills, and any skill
overload. The resolution recurses, so a parent topic's expert serves a sub-topic that holds no `expert.md`.

The expert combines two layers of capability:

1. **PKB general standards (common layer)** – the structure, metadata, tag, summary, and conflict rules Part 1
   defines. Harness maintenance hooks enforce their deterministic parts; their judgment parts run as common,
   overloadable skills (Sections 1.9 and 2.4).
2. **Topic knowledge (expert layer)** – domain knowledge about the topic itself, its file organization beyond the
   common standard such as a `recipes/` folder for Cooking, and the best ways to work its content: how to query it,
   which files answer which kinds of question, and its own ingestion rules.

Responsibilities:

- Answer a question about the topic from the breadth files (`topic.md`, `summary.md`) or the depth files (`index.md`,
  the source maps), as the request requires.
- Ingest what the Librarian routes: classify it as a reference or a note, tagging a solution `type.solution`, draft
  the files, and apply the metadata and tags the standards set. Ingest it **through the lens of this topic**, because
  one source reaching two experts should produce two different extractions, and **decline** material that holds
  nothing this topic cares about.
- Work a channel with the human for as long as the work lasts (Section 2.7): research a goal the topic cannot meet,
  brief read-only search sub-agents, weigh what they bring back against the topic's notes, object while the human can
  still act on it, take their results back as the trials come in, and propose at `/learn` what they learned.
- Carry out the judgment side of topic maintenance (Section 1.9). Harness hooks enforce the mechanical side.
- Manage the topic's extension folders, with the human's approval.
- Escalate to the human as Part 1 requires: summary approval, new tags, and conflict resolution.

**A source too large for one turn is ingested as a loop.** Classify, draft, file works for a link and fails for a
book: nobody reads what does not fit the context window, and one turn writes a confident account of the part it saw.
So the harness drives the reading. It segments the source, extracts argument by argument through a bounded window,
writes each section as it goes, records what it skipped and why, and survives a run that dies part way through a
300-page book. The expert stays the author of the extraction and stops deciding when it is finished. A source arrives
as a path. Anything binary is extracted to text first, and both are kept.

### Example: a Cooking Topic Expert in action

A user connects to the **Cooking** Topic Expert Agent. They need no external tool: the agent handles retrieval,
dialog, and filing end to end.

- **Ingest from the web**: the user asks for a steak grilling recipe. The agent fetches candidates, works with the
  user on the rub and the target doneness, and files the final version under `notes/`, or under `recipes/`, with its
  metadata and tags.
- **Capture experience**: the user reports back after cooking, "the grill behaves differently in windy weather". The
  agent files that as a note and proposes a regenerated `notes/summary.md` for the user to approve.
- **Combine reference and experience**: the user asks for a grilling recipe from an ingested cookbook. The agent pulls
  it from `references/` and applies the temperatures the user filed earlier for their own gas grill, human experience
  refining what others established.
- **Ingest through its own lens**: the Librarian fans a food-science book out to Cooking and to Health. Cooking files
  what it says about heat, protein, and technique; Health files what it says about nutrition. Neither expert files the
  parts it has no use for.
- **Research a goal the topic cannot meet**: the user asks how long to dry-brine a brisket, and the topic holds no
  reference on it. The agent says so, sends three search sub-agents out with one question each, verifies the pages
  they cite, flags the two results that contradict the user's own note about their grill, and offers one article for
  ingestion. The channel stays open, because the user has cooked nothing yet (Section 2.7).
- **Work one goal over weeks**: the user opens a channel called `Cooking · Brisket Rub` and works the loop in it. They
  cook three times and report back after each. The expert holds the trials in the session record, contradicts them
  when their week-three conclusion disagrees with their own week-one report, and at `/learn` proposes one note: the
  rub, the temperature, and what failed. Their other channel, `Cooking · Sourdough Starter`, stays untouched.
- **Leave nothing behind**: the user asks for a weeknight pasta, gets one, cooks it, and closes the channel without
  filing. No note, no summary, no folder. This is the ordinary outcome of a session, and the closed channel still
  holds the recipe when they want it again.

## 2.4 Common Skills and Skill Overloading

**This section is the procedural pillar.**

Every Topic Expert Agent loads the common skills. They ship with the implementation and mount ahead of the PKB root's
own `skills/` folder, which starts empty (Part 3). The mount is read-only because it lives inside the installed
package: a write there edits the implementation for every Knowledge Base on the machine, so the permission layer
denies it to every agent. The tree's own `skills/` folders take writes, and a skill the human adopts, writes, or
approves at `/learn` lands in one of them (Section 2.8).

**The two homes differ in reach, and today they differ in what can reach them.** A topic's `skills/` folder sits
inside that expert's own subtree, so the expert already writes there and the gate stops the write for the human. The
root's folder sits outside every expert's subtree, where the catch-all deny refuses it, and no tool routes a write
there. Filling the root folder needs a gated tool the implementation does not yet provide.

Each skill is a folder holding a `SKILL.md`, so `skills/voice/SKILL.md` and `skills/discovery/SKILL.md`. That is the
harness's own format, and it buys two things without code of ours: progressive disclosure, where the prompt holds the
skill's name and description and the harness opens the body when a turn needs it, and override resolution by name
collision. Anything else the skill needs sits beside its `SKILL.md`.

### The ten skills that ship

A shipped skill is a starter draft: it makes something sensible happen on day one, and it says so at the bottom of its
own text. They sort by the pillar each one serves, seven pointed at the human's subject and three at the procedural
pillar. Section 1.9 calls those three the collaboration skills and the other seven the judgment maintenance skills.

**Taking in what arrives from outside the topic.**

- **`ingestion-classification`** decides whether an inbound thing is a reference, a note, or a solution, and drafts the
  file with the metadata that decision implies. It routes to the theoretical pillar or to the practical one, and one
  decision settles the folder, the `source_type` and the `type.*` tag together, so a wrong classification makes every
  part of the file wrong at once.
- **`ingest-paper`** extracts a paper, study, whitepaper or technical report into question, method, results,
  limitations, and the section no paper contains: *does this apply to me*, naming the mismatch that drives the answer.
- **`ingest-book`** extracts a book or long report through the source's own chapters, one bullet per argument this
  topic cares about, and keeps *read and took nothing* apart from *never opened* in a reading record at the end.

**Tending the two subject pillars: what the topic already holds.**

- **`summarization`** drafts and revises the three breadth files, `topic.md` and the two summaries. It treats length
  growth as a defect: every revision distils and replaces, and none appends.
- **`conflict-detection`** runs the scan, tags what it finds for the human, and resolves nothing. The conflict type
  and the confidence stay in the conversation, because the Knowledge Base keeps no conflict register.
- **`tag-proposal`** proposes a tag the Knowledge Base has never used by writing the file that needs it and letting the
  gate hold the write, so the human sees the tag and the file it would create in one decision.
- **`sub-topic-proposal`** proposes a split once `notes/summary.md` has stopped distilling and started enumerating. It
  brings the evidence and the full file assignment, and it creates nothing.

**Serving the procedural pillar: how the human and the agent work together.**

- **`research`** explores breadth-first across the Knowledge Base and returns three to five options, each with its
  trade-off and the files behind it. Reaching a note tagged `status.conflict-review` that bears on the question, it
  stops and escalates rather than picking the reading that suits the answer.
- **`discovery`** runs a brainstorming session against Knowledge Base content. It names the tension between two notes
  and the gap a summary keeps implying, pushes back, and files nothing. Anything worth keeping goes back through the
  front door as ordinary ingestion.
- **`voice`** carries the voice profile every draft is written in, narrowed per topic when one subject wants a
  different register from another. The human corrects it from their own edits, and a change to the profile pauses for
  them like any other. It is the one shipped skill that knows something about the human rather than about cooking, and
  Section 2.8 opens the rest of the pillar to a session's own proposals.

**Five of the skills Part 1 describes have no file yet, and three of the five sit on the session side.** Section 1.9
names four kinds of source extraction and two of them ship; `ingestion-classification` files an article, post or clip
and a manual or reference work until their own skills exist. The other three, **research planning and synthesis**,
**lesson proposal** and **skill proposal**, are being written alongside the session work that calls them. The shipped
`research` skill stands in for none of them: it covers the breadth-first pass over the Knowledge Base and says nothing
about reaching the internet. All three mount and overload like the ten above, resolved by name for the distillation
call rather than by an expert's graph (Section 1.9).

Skills sit on the same side of the collaboration rule as notes, **human-generated and AI-curated** (Section 1.3): the
human writes or approves every one of them, whoever typed the draft. A `SKILL.md` is no knowledge file (Section 1.4),
and its `name` must match its folder name, for the reason *Where a skill lives* gives below.

### Where a skill lives

The procedural pillar has two homes and the tree resolves both by name. **This is the one place the rule is stated,
and every other section cites it.**

- **A skill about one subject** lives in that topic's `skills/` folder, visible to that topic's expert and to nobody
  else.
- **A skill about how to work** is a process skill and lives in the Knowledge Base root's `skills/` folder, where
  every expert loads it.

Changing a shipped skill uses the same two homes. **Adopting** it copies it to the root, where every expert loads the
copy from then on. **Overloading** it copies it into one topic, where that topic's expert loads the copy and the other
experts keep the shipped default. Both shadow by name, and the permanent-fork warning attaches to the name.

Resolution reads the shipped mount first, then the root folder, then the topic's, and the most specific entry wins
whole-record: a topic's `voice/SKILL.md` *replaces* the root one for that topic rather than merging with it, the
pattern the harness applies to `expert.md`. An overload extends the default with domain intelligence, a
recipe-writing voice for Cooking or a tasting-session discovery skill, and it redefines no general standard, because
Tier 1 validates the output whichever skill version produced it.

**The name decides whether a file forks anything, and the name that decides is the `name` in the file's own
frontmatter.** The harness reads every source in order and keeps the last skill declaring a given name, so
`skills/my-research/SKILL.md` declaring `name: research` shadows the shipped `research` from the moment it lands
(Part 3), while `skills/research/SKILL.md` declaring `name: my-research` shadows nothing. Both spellings look right in
a directory listing and the harness only logs a warning, so a Layer 2 diagnostic reports the mismatch. It warns rather
than refusing, and nothing calls it yet. A `/learn` proposal that would shadow a shipped skill says so in its approval
(Section 2.8).

**Re-read a skill as the work moves on, rather than writing it once.** A procedure hardens around the conditions
somebody wrote it in, and those conditions move: the tool that failed gets fixed, the human changes how they want to
be argued with, the topic grows past the shape the skill assumed. Two routes answer that. The conflict scan reads a
topic's skills against that topic's notes (Section 1.7), and a session proposes a revision to a skill a session wrote
(Section 2.8). Neither route reaches a skill the human wrote or adopted, and the scan axis is designed and not built,
so today the human is the only reader of a skill that has gone stale.

## 2.5 DeepAgent Harness and Access Channels

The agent layer runs on the **DeepAgent** harness. DeepAgent hosts the Librarian and the Topic Expert Agents and
exposes them through several channels:

- A dedicated **TUI**
- **Telegram channels**
- Other channels as needed: chat apps, APIs, and the rest

A PKB user connects to the **Librarian**, the default entry point that routes to the right experts, or **to one Topic
Expert Agent** when they already know which topic they are working in.

Step 4 of the Librarian's workflow joins the two (Section 2.2). Every expert the Librarian consults is addressable in
its own right, so a reply saying *"the Cooking expert says…"* is also an offer: carry on with that expert, in the
conversation it has had.

**A channel is a session** (Section 2.7). One expert holds several channels at once, each named for the goal it works
on, a channel holds several threads, and `/close` locks the channel and marks the session closed.

**One channel is not a session.** The **learning channel** (Section 2.8) binds to no topic, no goal and no expert,
and it holds the approvals whose own sessions have closed. It carries no conversation and files nothing itself, and
`/pending` lists what waits there the way it lists everything else.

The surface settles at seven commands: `/channels`, `/threads`, `/agents`, `/pending`, `/cancel`, `/learn` and
`/close`. The session design adds `/learn` and `/close`, and neither ships yet. **`/new` goes**: it rotates the
conversation inside a channel, and a channel is a session, so rotating splits one line of work in half and leaves both
halves named for the same goal. New work opens a new channel.

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

A session is how anyone works with the Knowledge Base in dialog: one channel, held open on an expert for one line of
work, for as long as that work lasts. A Topic Expert Agent answers a question from what its topic holds, and a session
covers everything past that, the goal the topic cannot meet, the trials that follow, and the lesson the human and the
expert settle at the end. Capture files what the human already knew. Sessions are where they find things out, and
where most notes come from.

**Research is one of the things a session does, rather than a kind of session.** A session may discuss, argue about a
design, ask a question and take the answer, search the internet, or try things for weeks and report back, in any
order. The system holds one shape for all of them, and a session that searches nothing is an ordinary session.

**None of this section is built.** The implementation holds no `/learn`, no `/close`, no search sub-agent, no session
record and no session summary, and the schema holds no `provenance` (Section 1.4). Everything below states the design,
in the present tense the rest of the document uses. Section 2.8 marks what the design has left unsolved, its last
subsection collecting the largest three.

### A channel is a session

The human opens a channel on an expert and works in it. The channel holds one line of work and lasts as long as that
work lasts, an afternoon or four months. Work begins as a question and becomes whatever it becomes: reading, then
trials, then a verdict, or none of those. The system asks the human to declare nothing in advance about what a
conversation will turn into, because they do not know yet and a wrong declaration is furniture they then maintain.

One expert holds several channels at once, each named for its goal. `Trading · Trend Signal` and `Trading · Market
Regime` run side by side, for two reasons, the second the stronger:

- On the human's side, separate lines of work are separate places on the phone, and a phone is where this work
  happens.
- On the model's side, each channel keeps its context on its own goal. A conversation replays its whole history on
  every turn until the history gets large and the harness compacts it. Two goals in one conversation means every
  trend-signal turn re-reads the regime work and takes an invitation to blend the two.

The channel title says which line of work a message went to, and it names the expert as well as the goal, so a
deliberate split is safe where an accidental one is not.

### The counterpart may be an agent

A project agent opens a session the way a person does, and for the same reasons: it needs knowledge the context packs
of Part 4 do not carry, it wants a question researched, or it implements something the topic holds the conditions for.
Everything in this section holds unchanged, and the dialog is the whole of the difference from Part 4's batch route.

One thing does differ, and it is now **settled**. Every approval in this document is a human reading exact bytes, and
an agent counterpart puts none in front of them, so **an unsupervised session never issues `/learn`**: it runs its
course and ends at `/close` like any other. Hermes suppresses its self-improvement review on a cron run, on the ground
that a session with nobody in the loop gains nothing from a review, and a tree with no undo sharpens that argument
here. The silence loses nothing: the harness picks the closed record up afterwards and generalizes it into a proposal
the human drains with `/pending` (Section 2.8). An agent session contributes what a human session does, later and
through the same door. One question stays open: whether a project agent may ever hold an approval, and over which
writes.

### Simple topics stay direct; complicated ones cross

Cooking is one expert, one subtree, one channel: the human talks to the Cooking expert and nothing else needs to
happen. Personal finance and investment cross, and neither portfolio management nor trading holds the whole of it.
That work opens a channel on the Librarian, which fans every turn out to the applicable experts and merges what they
return by attribution (Section 2.2). The Librarian still writes nothing, and each expert still writes inside its own
topic alone. A session reaches outside the topic's three pillars through search sub-agents, and a page the search
returns ranks below everything the topic already holds until the human accepts it (Section 1.7).

The system requires one declaration, which expert a channel opens on. Work that turns out to cross topics re-opens on
the Librarian, and the session record does not carry over: the human names the goal again and the new channel starts
a record of its own.

### The expert argues with you, and it argues about your own conclusions

Work with the expert the way you would work with a human expert. A good expert objects during the work rather than in
a retrospective. Say the rub needs more sugar, and if the human's own note from March says sugar burned at that
temperature, the expert says so while there is time to change the rub. The same holds at `/learn`, where **the human
can be wrong**. Told to file *sugar burns above 250*, an expert that reads trial two running at 260 without burning
says so before it drafts a word, then files what the human decides, because meaning is theirs (Sections 1.6 and 1.7).

### Two commands

**`/learn`, at any time, as often as it is worth asking.** It is the **only door into the tree**. A search reports
back into the conversation and files nothing; trials file nothing; a note, a skill and a session summary all wait
here. Mid-work use is the point: something learned in week two is gone by the time the verdict lands if nobody asked.
Section 2.8 runs the mechanism and bounds what it may conclude. The harness puts a session that closes without ever
running it through the same cycle, so the door has a second way of being knocked on and no second way of being opened.

**`/close`, when the work is done.** It locks the channel and **keeps** the session record, closed, because a source
this session turned down should not resurface in the next one on the same goal (*The session record is the durable
file*, below). With trials landed since the last `/learn`, the expert offers once before closing, the way a good
expert says *"before you go, I think there is something here"* rather than letting the human walk out. Say no and the
channel closes with nothing filed, and the closed record becomes eligible for the deferred pass over the sessions
nobody distilled (Section 2.8), so saying no ends the conversation rather than the question. Nothing else brings them
back to an open channel. Returning is theirs to do, and the open channel on the phone is their only reminder.

### The loop is: try it, report back, distil

Research a rub and a smoking approach, cook, come back with how it tasted, cook again, and distil after the third
attempt. Research a trend-following signal, run several variants, watch which one holds and under what setup, and
distil once the data arrives. Feedback lands in pieces, from wherever the human is, over weeks. The loop is where
`notes/` gets made, and where the procedural pillar picks up what the two of them worked out (Section 2.8).

### A session searches in seven steps

A session searches as often as the work needs. Each search runs as harness-encoded steps for the reason routing does
(Section 2.2): the expert's judgment sets the questions and weighs the answers, and code runs the search and the
checking.

1. **Take the goal.** The human says what they want to know, and the expert writes the goal into the session's own
   file (below). A channel sets as many goals over its life as the work turns up, and each goal takes its own summary
   file. A later turn joins an existing file when the goal recorded in it matches, and otherwise starts a new one.
2. **Survey the topic.** The expert reads `topic.md`, both summaries, and the notes and references that touch the
   goal. A goal the topic already meets ends the search here, with the answer and the files it came from. Searching
   the internet for something already filed spends the budget and invites a page that contradicts the human's note.
3. **Write the questions.** The expert turns the goal into one question per line of enquiry, and each question carries
   the goal alone (*The notes weigh the results*, below), an objective, the shape its answer should take, the sources
   worth trying, and its boundary against the other questions. Vague briefs are the documented cause of two sub-agents
   researching the same thing while a third researches something nobody asked for.
4. **Search.** The harness starts one search sub-agent per question and runs them in parallel. This step is code. A
   model free to decide whether to delegate does not delegate, and Section 2.2 records what that cost the Librarian
   before its fan-out became a step that always runs.
5. **Verify.** Harness code locates every quotation in the text the search returned for the page it came from, and it
   fetches nothing itself (*Code verifies every quotation*, below, carries the measurements). It **holds** a URL it has
   no text for, and a quotation that text does not contain. Both land under their own heading in the session record
   with the reason they failed.
6. **Weigh.** The expert compares what survived verification against the topic's notes and references, claim by claim,
   and classifies each disagreement by the three conflict types in Section 1.7.
7. **Report back.** The expert brings what survived, with the evidence behind it, and the conversation carries on. The
   human may name a source worth ingesting on the spot. Everything else waits for `/learn`, because the search found
   things and the human has tried none of them yet.

```
HUMAN or PROJECT AGENT ── opens ──▶ CHANNEL = SESSION
                        │              one line of work, named for its goal,
                        │              on one expert, or on the Librarian
                        ▼              when the work crosses topics
  ┌──── a search: steps 1-7 above ───────────────────────────────┐
  │  the expert judges at 1, 2, 3, 6 and 7                       │
  │  harness code runs 4 (one read-only sub-agent per question)  │
  │  and 5 (every quotation found in the held bytes)             │
  └───────────────────────────┬──────────────────────────────────┘
                              │
      the human goes and tries it, then comes back with what
      happened. Searches and trials repeat for as long as the
      work lasts: an afternoon, or four months.
                              │
          ┌───────────────────┴───────────────────┐
          ▼                                       ▼
   /learn  the only door into          /close  locks the channel,
   the tree; any time, repeatable;             marks the record closed;
   an ordinary message the human               offers /learn once if
   argues with; then they approve              trials went unfiled
   the exact text that lands
          │
          ▼
   notes/ · sessions/ · skills/ · references/ · nothing at all, the common outcome
   (What a session may file, below, gives the gate on each)
```

### A session writes goal specs and executes nothing

Work turns up things to do: run this backtest over these three regimes, cook this at 250 for four hours. A session may
write that down as a **goal spec**, which says why the work is happening and what a good outcome looks like, and stops
there. Something else picks the spec up: the Project Manager (Part 4), a coding harness, or the human with a smoker.
Results come back into the channel as conversation. A spec stays a message until the human says it is worth keeping,
and it then lands inside the session's synthesis under `sessions/`. The Knowledge Base grows no task queue, no runner
and no status field, because a knowledge base that executes has to remember what it is halfway through, a second store
of truth that can be wrong.

### The session record is the durable file

A session keeps a **session record** and writes into it as the work happens: the goal, each trial and what it
produced, the sources kept and the ones turned down. The record lives in the session's workspace, outside the
Knowledge Base tree, and it survives a restart of the daemon. `/close` marks it closed and keeps it, so the next
session on the same goal reads what this one declined. It is the running file this section names throughout, and *The
session summary* below is the separate thing the human approves into the tree. They read the record by asking the
expert for it, the way they ask for anything else in the channel.

The reason is measured, and Section 2.8 spends it: a channel that runs for months compacts its own early trials out
of the conversation, so the expert reads the file rather than the transcript. The large-source ingestion loop settled
this shape already: *"There is deliberately no second store of progress: a second
source of truth about what was read is a second thing that can be wrong, and the one a human can check is the file."*

### Most sessions leave nothing behind, and that is correct

Ask for a recipe, get one, `/close`. No note, no summary, no folder, no trace, and rule 4 in Section 1.8 rules the
same way for ingestion.

The asymmetry is deliberate. The closed record keeps the goal, the trials and the declines, and the closed Telegram
topic keeps the conversation around them, so discarded work stays readable outside the tree and claims nothing inside
it. Closing puts none of it beyond reach: the harness reads a closed record that never ran `/learn`, and over this one
it finds no lesson and files nothing (Section 2.8).

### The search sub-agents read; the expert writes

A search sub-agent holds retrieval tools and no write tool of any kind. The permission layer enforces that, the way it
confines a Topic Expert Agent to its own subtree: a write tool it never received is a write it cannot make. The expert
authors everything a session produces. Each sub-agent spends a whole context window on one question and returns a page
or two, and that compression is the reason to run one.

Three sub-agents is the default width. The deployment sets it and nothing in the Knowledge Base does, and the expert
names the width it used the first time a search reports back. A budget that a topic's own files could raise is a
budget an agent's own write could raise. Section 1.1's *research agents* are a different thing, the breadth-first
consumers of Part 4's context packs.

### The notes weigh the results; they never travel with the questions

The human's notes hold the highest standing in the Knowledge Base, so the obvious move is to hand them to the search
sub-agents and let the search start from what the human already believes. Measurement says to do the opposite.

A model told what the human believes stops finding evidence against it: disconfirmation detection falls by 16 to 93
percentage points across four models once the belief sits in the prompt. Humans do the same thing to themselves. A
search conversation that agrees with the searcher raises the rate of confirming queries from 16% to 43%, and the
questions asked do the damage rather than the answers given.

The questions in step 3 therefore carry the goal and nothing else. The notes return in step 6, where the expert weighs
a verified result against them. A prior multiplies the evidence. It chooses no evidence.

### Search comes from the model provider

The experts already run on Ollama's cloud models, and the same account serves search. The system asks that account for
results and for the text of a page, so the design signs up no second vendor and opens no second account. Search takes
one credential. The daemon reads it at startup and hands it down, on the path the Telegram token already walks
(`docs/how-to/`). No agent, log or health endpoint sees the value.

**A result arrives as the page's text rather than as a link to it.** Ollama returns thousands of characters of content
per result, so the harness holds what a search sub-agent read at the moment it read it. Code then locates a quotation
in the exact bytes the claim came from. The check stops being a best effort against a page that may have changed since
the search and becomes a comparison. Every admissibility rule below rests on that.

Search returns extracted text, so ingestion still fetches a source itself. The copy a topic keeps beside an accepted
reference is the original bytes off the web (Section 2.3): search serves the research, and ingestion serves the filing.

**One provider serves the models and the search, so one outage takes both.** The local fallback model runs in the hour
the cloud is unreachable, and search is unreachable in that same hour, so the searching stops. A search that cannot
reach the provider says so and ends, and the expert goes on answering from the topic's own notes and references on the
local model, at a fraction of the speed. The channel stays open through all of it, and the next search runs the first
time the provider answers, so nothing polls and nothing queues.

### Code verifies every quotation

Published deep-research agents invent 3% to 13% of the URLs they cite, and 5% to 18% more of the URLs they give do not
resolve. In one shipped generative search product, 51.5% of the sentences it wrote were fully supported by the
citation attached to them. So no URL reaches a session summary on a model's word:

- **Harness code holds the text of every cited page.** Search hands the page's content back with the result. A
  sub-agent that wants a page beyond what its search returned reads it while it is still searching, and the text joins
  the same session record. Verification fetches nothing itself. A citation the record holds no text for keeps its
  claim out of the synthesis, and the record says why the claim carries no weight.
- **Harness code locates every quotation in that held text.** It drops a quotation the text does not contain, and the
  record says so. The comparison runs against the bytes the claim came from, never against a page fetched again later
  and rewritten in between.
- **The harness asks no model where a quote sits.** Models miscount positions and invent spans. The sub-agent returns
  the quoted text; code finds it.

The same rule governs an extraction: a quotation a model produces is a candidate until code finds it in the source.

### A page can be written to be read by an agent

Research is the first thing here that pulls text chosen by strangers into the conversation. Retrieved text therefore
travels fenced as data, under a standing instruction that nothing inside the fence is an instruction. Every quotation
a session shows the human sits in a quoted block with its source attached, so a page's prose never speaks in the
system's voice.

That is mitigation rather than a cure. Four structural bounds hold behind it: the sub-agent's missing write tool, rule
8 in Section 1.8, quotation verification in code, and the harness-rendered approval.

### The budget bounds quality, and cost is not the reason

The budget exists because a long run is a worse run. Factual accuracy on one measured search agent fell from 79% to
17% as its tool calls rose from 2 to 150. Between 77% and 94% of the steps in a long search add no new evidence, and a
run that reaches the wrong answer runs two to three times longer than one that reaches the right answer. Length is a
symptom before it is a cost.

A **single search** carries a step budget and a wall-clock budget. The session carries neither. Exhausting either
budget stops that search, and the expert says it stopped short of the goal. The human can act on that: they say chase
it again with a narrower question, and the channel is still open for them to say it in.

### The session summary

A session that worked out enough to be worth summarising produces one file, `sessions/[goal-title].md`. It follows the
folder-hosted convention of Section 1.2 when it needs media beside it. It is a knowledge file (Section 1.4) with
`source_type: summary` and the tag `type.summary`, because a session summary is an overview of what one line of work
found. It carries `provenance: researched`, the field that keeps it distinguishable from a note the human earned. The
tag namespaces of Section 1.5 do not grow for sessions: a tag would restate the folder, and disagree with it the first
time somebody moved a file.

The summary holds, in order: the goal, the questions the session asked, every source it kept, every source it rejected
and why, the claims verification held back, the conflicts it raised against the topic's notes, and the synthesis. A
session that searched nothing fills the source sections with nothing and keeps the goal and the synthesis, because a
discussion that reasoned from what the human already holds and reached a conclusion reached one.

**One shape is refused, and it is narrow.** A session that searched, admitted nothing past verification, and then
wrote a confident synthesis anyway summarised a page it never read. That filing is refused with the empty findings
list quoted back. A session that never searched is a different thing and files as usual.

A session with nothing to summarise writes no summary. A session that read one page, cooked from it, and learned one
thing writes a note and no summary. The summary holds what the session **worked out**; the note holds what the human
**did**.

**The summary is append-only.** A turn adds to the end. Nothing rewrites an earlier entry, because a model asked to
revise a long report across turns removes correct material without saying so and introduces errors while it polishes.
A correction is a new entry naming what it corrects.

**A rejection reaches the tree through this file alone.** A candidate the human turns down leaves no folder under
`references/`, no stub, and no copy, per rule 4 in Section 1.8. The reason is theirs, recorded in the words they
typed. Until a summary lands, the rejection lives in the session record, which survives `/close` closed, so a session
that files no summary still tells the next session on that goal what it declined. A later session that finds the same
page shows it **labelled with the date and the reason**, at the bottom, rather than hiding it: the page they turned
down for one question may be the page they want for the next, and a result dropped in silence looks like a result
never found.

**Candidates live with the session rather than in the tree.** A page the search returned and the human has not
accepted stays with the session: the text goes when the search that found it ends, and its line in the record stays
with the record. Nothing stages it, copies it, or writes it under the PKB root. `.inbox/` is where an **accepted**
source stages on its way through ordinary ingestion, and nothing else puts anything there. The cost is honest and
small: the harness fetches a page the human accepts a second time. The alternative was thirty browsed candidates
leaving thirty permanent folders in a tree with no undo, in a staging area no channel can list.

### What a session may file

| Outcome                                | Where it lands                                     | What gates it                                                          |
|----------------------------------------|----------------------------------------------------|------------------------------------------------------------------------|
| Something the human tried, and what happened | `notes/`, stamped `provenance: practised`, tagged `type.solution` when it worked and `type.note` when it did not | Settled at `/learn` in conversation; the human then approves the rendered text |
| The session's synthesis of what it worked out | `sessions/[goal-title].md` in the topic, or the **root** `sessions/` when the session crossed topics; tagged `type.summary`, stamped `provenance: researched` | The human approves the rendered text at `/learn`, before it lands |
| A way of working the session established | `skills/[skill-name]/SKILL.md`, in the topic's folder or in the root's (Section 2.8) | The human approves the rendered text, on an approval that names the scope and any shipped skill it would shadow |
| An article the human accepts           | `references/[source-name]/`, through the ordinary ingestion procedure (Section 2.3), with the topic's own copy of the original | The human names the candidate; the harness will only ingest a page it printed for them and fetched itself. The first extraction is then un-gated, like any other first ingestion |
| A candidate the human rejects          | The rejection list inside the session summary; the session record holds it either way, open or closed | Nothing, and no other file changes                    |
| Nothing at all                         | Nowhere: no note, no summary, no folder              | Never running `/learn`, and this is the common outcome. The deferred pass over the closed record files nothing either, which is the gate working rather than failing (Section 2.8) |

Rule 8 in Section 1.8 is the line this table draws. A session files everything it **read** as a reference or as a
synthesis, and everything the human **did** as a note, in their own words, after they did it. `provenance` records
which of the two a file is. The skill row carries none, because Section 1.4 exempts that file class.

### Direction is conversation; the write is the approval

The human steers a running session by talking to the expert: drop that question, chase this one instead, that source
is no good, that lesson is half right. An approval halts the conversation until somebody answers it, so spending one
on "those three look right, keep going, but drop the second" would stop the session to ask what it could have heard in
passing. An approval also accepts the answers it offered and no others, and that sentence is not one. So `/learn`
proposes in an ordinary message.

One approval remains, and it sits on the bytes (Section 2.8, step 5). A skill asks on its own terms, because the human
agrees to something different there. Accepting a source for ingestion is an instruction rather than an approval, and
the harness ingests a page it printed for the human and fetched itself, and no other.

### `/learn` on a Librarian channel fans out

On a Librarian channel, `/learn` treats the session itself as a source and fans it out. It asks each applicable expert
what its own topic takes from it, with the grammar the ingestion loop already uses section by section: something new,
something better, something that contradicts what I hold, or nothing. An expert that takes nothing leaves no folder
and no stub. Each note lands inside its own topic, so the Librarian still writes nothing.

A Librarian `/learn` therefore proposes a **set** of notes. The human takes some and drops others, naming them by the
label printed beside each one. Each kept note then asks for its own approval on its own text, so a rejection on one
changes nothing about the rest. The cost is honest: four kept notes means four texts to read.

A session that yields a portfolio lesson and a trading lesson yielded two lessons, the shape one book reaching two
topics takes. An insight that spans the topics rather than decomposing across them lands in one topic with
`related_topics` naming the others, and the hooks aggregate that into the root registry (Section 1.9).

**A cross-topic session files one session summary, at the root.** The crossing is the thing worth keeping, and
splitting it into per-topic accounts loses that, so the synthesis lands in the Knowledge Base root's own `sessions/`
folder rather than in any topic's, with the frontmatter Section 1.4 gives it. Harness code writes it on the route a
root process skill takes: the expert that ran the session drafts the text, the human approves the exact bytes, and a
gated tool performs the write, so the Librarian still writes nothing (Section 2.8).

Layer 1 grows with it. The root walk learns one more folder beside `index.md`, `tags.md` and the root `skills/`, and
the files inside it are ordinary knowledge files, validated as such and tagged into the root registry. **The conflict
scan does not reach them**: every axis in Section 1.7 compares a topic's own files against each other, and this file
belongs to no topic, so the crossing is the one knowledge file nothing scans. It reaches no context pack for the same
reason. A root process skill is the one other thing a Librarian `/learn` may propose.

The Librarian runs no search of its own and holds no topic's search tools, which would let it answer a subject
question out of its own head (Section 2.2). It reaches a topic's tools through the expert that owns them.

### Domain tool servers belong to a topic

A topic may bring tool servers of its own: a recipe service for Cooking, a case-law service for a legal topic. The
**deployment** binds them to that topic's expert, in the daemon's own configuration, for the reason the deployment
picks the model too: configuration an agent can write is configuration an agent can grant itself. `expert.md` may
describe what a topic uses; it decides nothing about what a topic holds. A server is declared for the expert's own
turns or for its search sub-agents, and one declared for the sub-agents never reaches the expert directly, so a page
off the internet cannot enter a note by the side door. The servers reach no other topic and no Librarian. A narrow
goal then stays with one expert and never becomes a routing problem.

## 2.8 Self-Improvement: How a Session Learns and What It May Conclude

`/learn` is the moment a session turns a conversation into a claim, and it is the only door into the tree
(Section 2.7). This section runs the cycle first and bounds what it may conclude after.

### The `/learn` cycle

1. **Read the record from the beginning.** `/learn` reads the whole session record every time and never the previous
   extraction. One-shot consolidation beats streaming, and chained abstractions compound: a distillation of a
   distillation carries an error nobody can trace back to the trial that started it. A record too long for one turn
   walks through the bounded reader of Section 2.3 and comes back as one consolidated account, read beside the record.
2. **Draft the candidates.** The expert drafts each kind with Section 1.9's drafting skill for it, resolved topic-copy
   first so the topic's overloads apply. It proposes what it thinks the session learned and what is worth filing, and
   the human may dictate the lesson themselves instead.
3. **Show them in a message.** The proposal arrives as an ordinary message in the channel, never as an approval, for
   the reason *Direction is conversation* gives (Section 2.7). Each candidate names what it rests on: which trials,
   which sources, which turn of the conversation.
4. **Argue.** The human drops one, rewrites another, and adds the thing the expert missed. The expert argues back
   before it drafts a word, and Section 1.7 governs a candidate that contradicts a note the topic already holds: quote
   both sides, change nothing, let the human settle it.
5. **Approve the bytes.** The harness renders each file that would land and the human reads the exact text. Three
   candidates ask three times, and the human may take one and drop two.
6. **Write, then record what landed.** The files land, the hooks regenerate the indexes and the registry
   (Section 1.9), and the session record notes each lesson beside the path it landed at.

**The cycle repeats for the life of the session, and the record is what lets it.** A channel that runs for months
compacts its own early trials out of the conversation, so by week twelve the record holds the evidence and the
transcript does not. A repeat `/learn` therefore files nothing twice: a second pass over the same trials shows that
lesson as already filed, with its path. Two near-identical solution notes in one topic both reach every implementation
pack and then drift apart, the harm rule 4 in Section 1.8 exists to prevent.

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

Hermes runs no session-end pass either: its review fires on accumulated tool iterations, and its own
`turn_finalizer.py` notes that `on_session_end()` never runs there. Its cadence is therefore no argument for this one,
and this design takes the opposite one. No clock runs `/learn` inside a live channel, whoever holds the channel types
the command, and a closed record gets one pass rather than a recurring one (*A closed session is generalized*, below).

### Five things a session produces that look like knowledge

The same Hermes prompt carries an exclusion list, and it is the most reusable thing in that system. Each entry names a
way a session manufactures something that reads like a lesson. All five hold here, and the first one is the dangerous
one:

1. **An approach that never worked.** The session tried several things, none of them worked, and it ended by telling
   the human to check by hand. Hermes names the harm: *"do NOT write those attempts up as a 'reliable workflow' or
   'recommended approach'. That presents an untested sequence of failures as validated guidance a future session will
   trust and repeat."* Three briskets that all came out dry support a note about three briskets. They support no note
   about how to dry-brine.
2. **A failure the environment caused.** The smoker's thermostat read 40 degrees low that week; the data feed was down
   that afternoon. Filing that as a property of the technique blames the method for the machine.
3. **A verdict that a tool cannot do something.** Hermes on why this one earns its own entry: *"These harden into
   refusals the agent cites against itself for months after the actual problem was fixed."* A note saying the search
   provider returns nothing on a subject outlives the outage that produced it, and every later session reads it as a
   fact about the subject.
4. **An error a retry cleared.** *"If retrying worked, the lesson is the retry pattern, not the original failure."*
   The error itself is noise. The patience might be knowledge.
5. **The story of one afternoon.** A narrative of what happened once is no rule, and `notes/summary.md` holds rules.
   The loop in Section 2.7 asks the human to cook three times before distilling for this reason.

A sixth exclusion is already law in Part 1 and needs no argument from Hermes: nothing off the internet becomes a note
(rule 8 in Section 1.8). A session files what it read as a reference or a session summary, and what the human did as
a note.

### What a session may conclude

A session authors four outcomes, and the table in Section 2.7 gives the gate on each: a note, a session summary, a
skill, or nothing at all. That table carries two more rows a session does not author, an accepted article and a
rejected candidate. The bar on the note is three conditions and each one carries load. The human did the thing.
They came back and said what happened. They approved the exact text that lands. An expert holding the first two alone
holds a trial, and it argues about what the experience means and never about whose it was (Section 1.3).

### A closed session is generalized even if nobody ran `/learn`

`/learn` distils while the human's judgment is fresh, which is when it works best. Most sessions never run it, and
*The default is silence* above is why that is the right outcome rather than a loss. The loss is the other case: a
session that closed with something in it and nobody to ask. Two of those are routine, the human who meant to distil
and did not, and the session that had no human in it at all.

**An unsupervised session never issues `/learn`** (Section 2.7). The command proposes to whoever holds the channel,
and on a project agent's channel that is nobody.

**The harness generalizes a closed session whose trials nobody distilled.** The predicate is the one `/close` already
offers on (Section 2.7): trials landed since the last `/learn`, whether the session ran the command earlier and worked
on afterwards or never ran it at all. The harness picks the record up, runs the cycle above over it, and shows what it
finds for approval. Nothing about the bar moves: the same five exclusions, the same three conditions on a note, the
same rendered bytes. **Everything derived from a `/learn` extraction waits for the human, a note and a skill alike**,
a rule already stated for skills and general.

So the door stays where it is. `/learn` is still the only thing that files, and the deferred pass is that same cycle
run by the harness over a record whose channel has shut. Only the knock changes. Both routes exist and neither
replaces the other, and the one that runs while the human still remembers the week is the better one.

### The queue holds proposals rather than sessions

**The harness runs the gate before it queues anything.** Only a session that clears the gate produces a proposal, and
a session that clears nothing leaves the queue as it found it.

The reason is measured. Roughly one in seven self-evolution candidates establishes anything, and a filing rate above
about one in five is evidence that the gate is broken rather than generous. Queue every closed session instead and the
queue is mostly nothing: the human stops reading it, and the one that mattered goes unread with the rest. That is how
lessons-learned databases die, and the literature on why they go unread agrees about it.

*Most sessions leave nothing behind* (Section 2.7) therefore survives all of this unchanged. The harness picks up the
recipe the human asked for on Tuesday, reads it, and finds no lesson in it. Silence is the correct result of the
deferred pass over it, the gate working rather than failing.

### Staleness is the cost to design against

A session closed in March and generalized in June arrives after the human's memory of it has gone. The record
survives, so the material is still there, and their judgment about it was sharpest the day it happened.

Two mitigations, both cheap. **The proposal quotes the record** rather than asserting a conclusion, because a human
reviewing a session they do not remember needs the evidence in front of them. And **the harness picks a closed session
up promptly** rather than on a slow sweep, which costs nothing and is the whole difference between June and Tuesday.

The eligibility window is **not settled**. A record kept forever means a session from last year can still produce a
proposal, and everything above is the argument against it.

### The learning channel

**A learning proposal lands in a learning channel rather than in a topic.** The human's first thought was a special
topic `kb-learning`, and they ruled against it. A topic is an expert, a `topic.md`, three pillar folders, an agent id,
an entry in the catalog the Librarian routes on (Section 2.2), and a write confinement drawn around its own subtree. A
queue of pending proposals is none of those. Making it a topic produces an expert nobody wants to talk to, a routing
target nobody should route to, and folders that hold nothing.

**The queue itself needs nothing new.** A proposal is an approval, and an approval already parks durably, survives a
restart of the daemon, answers hours later from a different channel than the one that raised it, and appears under
`/pending` (Section 2.5).

The missing piece is **a home for an approval whose session has closed**. An approval appears today in the channel
whose run raised it, and a generalization has no live channel to appear in. The learning channel is that home: a
surface bound to no topic and no goal, holding the approvals that have no channel of their own. The TUI gets it for
free, because the TUI already carries an unfiltered *needs you* view.

**It runs in the worker slot that has never been filled.** The daemon builds its application without a scan worker, so
the conflict scan has reported itself disabled since the day somebody wrote it (Section 1.7, and *Three gaps* below).
This is that slot's second tenant, and two tenants make the wiring unavoidable.

Two things about the channel are **not settled**. Whether it binds to the Librarian or to no agent at all: a proposal
already names the topic it would write to, so the channel may need no agent behind it. And whether the queue needs a
cap, because an agent working overnight can pile up proposals nobody has seen.

### The skills that already generalize

Four of the shipped skills (Section 2.4) do the generalizing work, and `/learn` is one call site among several:

- **`summarization`** turns single notes into the distilled rules of `notes/summary.md`, the file every implementation
  pack loads first. A lesson filed at `/learn` reaches decisions through this skill.
- **`conflict-detection`** catches the human contradicting their own earlier self, the case one session cannot see:
  the week-one note and the week-twelve note were written in different conversations.
- **`discovery`** finds the rule under two notes that never mention each other, and it files nothing, so the finding
  goes back through the front door with its own approval.
- **`voice`** watches for the same edit repeated across three drafts and proposes it as a rule. That is the procedural
  pillar generalizing from experience about the human (Section 2.4).

Section 1.9's three drafting skills do the writing at `/learn`, and all three are being written (Section 2.4). The
checks a draft has to answer for stay in harness code, per Section 1.7: a skill is a file the human may adopt and then
edit, and a guarantee living in an adopted copy leaves the day they edit it. That holds hardest for the skill draft,
whose subject is what a skill should say.

### Two guards Hermes puts in code, and both belong here

Hermes enforces two of its safeguards in the tool layer, the choice Section 2.2 made about fan-out and Section 2.7
made about verification.

**Nothing rewrites a file whose current text it has not read this turn**, and `RS-141` is the rule that carries it.
Hermes refuses a patch to a file the reviewer has not loaded verbatim in that same turn, because *"the autonomous
review fork is allowed to evolve skills, but it must not patch or rewrite content it has only inferred from the
transcript."* A `/learn` proposing to revise a note the human filed in March works from an impression of that note,
the impression came out of a conversation that has since compacted, and somebody else may have edited the note
meanwhile. Read the file, or leave it alone. The rule lives in harness code for the reason Section 1.7 gives about
every other guarantee here: a guard written into a skill leaves the day somebody edits their copy of that skill.

**Authorship decides what may be curated**, and `RS-142` is the rule that carries it. Hermes tags every skill write
with its origin, so autonomous curation touches the skills the autonomous process itself created and no others:
*"Skills a user asks a foreground agent to write belong to the user and must never be auto-curated."* Part 1 already
draws that line as the collaboration rule. The harness writes an authorship record and reads it back, which answers a
different question from `provenance` (Section 1.4): `provenance` says which route the content took, authorship says
whose hand put it there. In a knowledge file that record is a block inside the file. In a skill it sits in a second
file beside the `SKILL.md`, whose two fields leave no room for it and whose body loads into a model's prompt, where an
origin block would read as one more line of procedure. A folder with no such record is the human's.

### A session may also teach the system how to work

**A session feeds the procedural pillar as well as the practical one.** A session that established *brisket holds at
250* fed `notes/`. A session that established *a better way to run a session* has something for `skills/`, and
`/learn` may propose it. `voice` is the seed of that half: it holds a profile of the human, corrected from their own
edits through the same propose-and-approve loop as everything else (Section 2.4).

**A note says what is true. A skill says how to work.** That one line decides every `/learn` proposal, and the test
that separates the two is **who acts on the draft first**. A skill shapes how the expert works before anybody asks a
question, because the harness loads it into the prompt at the start of the turn. A note answers a question about the
subject once somebody asks one, and the harness fetches it when it is relevant.

Run the test on the three cases that matter. *Brisket holds at 250 for four hours* waits for somebody to ask about
brisket, so it is a note. *Always preheat the grill for 15 minutes* reads as an instruction and is still a note,
because the human at the grill acts on it and no draft changes shape until they ask. *Ask for the pit's own
thermometer offset before drafting any smoking lesson* changes the expert's next draft first, so it is a skill.

**A procedure the human proved by doing is a solution note**, tagged `type.solution` (Section 1.5). It becomes a skill
once it directs the expert's own drafting rather than the human's own doing. That fork is the one a reader at `/learn`
faces most often, and the loading test settles it.

Section 2.4 decides where the file lands, so a `/learn` filing decides one thing, the scope.

### The four decisions a written skill needs

**A skill write asks for approval, and it asks in its own words.** Every write under a `skills/` folder already stops
for the human, so the question is which sentence they read while stopped. *A lesson is ready to file* is the wrong
sentence in front of a file that changes how the expert works on every later turn, so the skill filing carries its own
approval naming the scope and any shipped skill it would shadow, with the exact `SKILL.md` text underneath. Agreeing
at `/learn` that the session learned something is agreement about the lesson. The procedure the expert then wrote from
it is a second object the human has not read yet.

**A session revises a skill a session wrote, and never one the human wrote.** The two guards above reach skills
unamended. Read-before-write means a proposal to revise `skills/session-loop/SKILL.md` loads that file's current bytes
in the same turn and derives the revision from them. Authorship means a session amends a folder carrying the origin
record a session wrote and no other, and a folder without one is the human's: they typed it, or they adopted it. A
session that wants such a skill changed proposes the change in conversation and leaves the edit to the human.

**The expert that ran the session asks for a root process skill, and harness code writes it.** The expert drafts the
text and calls a gated tool, and the tool performs the write once the human approves. The root session summary of a
cross-topic `/learn` takes the same route (Section 2.7). The Librarian's write capability stays at zero, as it does
for topic creation. Widening an expert's permission to write outside its own topic is refused, because that loosens
the subtree confinement on every turn to serve one filing that already has an approval in front of it. **No such tool
exists today**, and both root folders are denied to every agent until one does (Section 2.4).

**A skill that shadows a shipped one by name says so three times.** Section 2.4 gives the mechanism, and nothing in
the tree records the swap: no index lists the file and no tag points at it. So the proposal says it, the approval says
it again with the exact bytes, and the file opens with the line naming what it shadows, the same line adoption writes
(Part 3). The third one matters most, because whoever ran `/learn` is not the person who opens that file six months
later. The collision is never refused: improving a shipped skill for one topic is the most useful thing a human does.

### A wrong skill is worse than a wrong note

A wrong note is wrong about brisket. Somebody pulls it into a pack, cooks the thing, and the meat corrects them the
same afternoon. A wrong skill is wrong about every session that follows.

A session in March concludes that the pit runs hot and writes a skill saying *treat every stated temperature as twenty
degrees high before drafting*. The human reads it once, agrees, and it lands. In April the human replaces the pit.
Every draft after that subtracts twenty degrees from a correct number, in every session on that topic, before anybody
asks a question. The drafts carry no mark saying which skill shaped them, so the human reads a wrong temperature and
corrects the draft rather than the file. The note they file to correct it says the pit runs true. Catching that pair
is the job of the scan's fifth axis, practice against procedure (Section 1.7), which this design adds for this case
and has not built. Built, that axis is still the weakest reader in the system. A skill appears in no `index.md` and
contributes no tags (Section 1.4), so the scan reaches it by path, `discovery` never surfaces it, and one model call
has to notice that a procedure and a note disagree about a number neither states the same way. The reader most likely
to catch it is the human who approved it once, in March.

That axis also has a case it cannot reach. The scan compares claims, and a skill phrased as procedure makes none.
*Ask for the pit's own thermometer offset before drafting any smoking lesson* is the same lesson written the way this
section prefers, and no note in the tree agrees or disagrees with it. So the approval in front of a skill is the guard.

The worst version is a shadow. A topic skill declaring `name: conflict-detection` replaces the shipped scan for that
topic, so a bad skill can switch off the check that would have caught the bad note. Exclusion 3 above is the same
failure at a smaller scale, and Hermes states it: *"These harden into refusals the agent cites against itself for
months after the actual problem was fixed."* A skill is where that hardening happens, because a skill is the file the
system follows without being asked. An approach that never worked, written up as a procedure, becomes a procedure.

### Where a lesson about the human goes, settled

`voice` keeps the human's register and nothing else. A procedure about running a session goes to a root process skill,
and a preference about wording goes to `voice`. Splitting them costs one judgment at `/learn`. Merging them would put
*ask for the pit's thermometer offset before drafting* into the file every draft is style-checked against.

### The procedural pillar has no breadth file, and adding one is a decision

Each subject pillar carries a human-approved `summary.md` (Section 1.6), and the human has asked for the third.
Nothing is built for it, because the obvious placement contradicts Part 1. Everything under a `skills/` folder is a
skill file (class 3 in Section 1.4): no PKB frontmatter, no place in any `index.md`, no contribution to the tag
registry. A `summary.md` sitting inside one is either a knowledge file living in a folder the rules exempt, or a
fourth file class this document never defines. Today `Cooking/skills/summary.md` passes content validation with no
findings, because the skill class exempts everything under that folder, and the tree walk then warns
`LEGACY_SKILL_LAYOUT`, because a flat markdown file inside `skills/` is the superseded layout and loads as no skill.

Three shapes answer it. A **skills section inside `topic.md`** needs no new file class and gives up a separate
approval surface. **`skills/summary.md` as a fourth file class** carries its own frontmatter rules and its own
exemptions, and costs changes in Sections 1.2 and 1.4 and in Part 3. A **generated file** gives up the human approval
that makes a breadth file worth reading, and Section 1.6 refuses it on that ground.

**Recommended default: the first.** It costs nothing in Part 1, it puts the pillar's overview in the file the
Librarian already routes on, and the approval it gives up is one the human already gives when they approve `topic.md`.
The human picks, and Section 1.2 grows a folder comment when they do.

### Three gaps in the self-improvement loop

The system is meant to improve itself from what it learns in the work. Three things stand between the design as
written and that claim, and each one belongs to a pillar. **No route exists for any of them yet.**

**The system notices nothing on its own.** The conflict scan is the only machinery that reasons over the Knowledge
Base unprompted, and it runs across all three pillars, so it is the natural home for this. It does not run: the daemon
builds its application without a scan worker, so requests pile up in the queue on every filing turn, `/health` reports
the scanner disabled, and the only scan that happens today is one a human asks for (Section 1.7). An agent that
reasons when spoken to and never otherwise is a filing system with a good vocabulary. The deferred `/learn` pass above
is the second tenant of that unfilled slot, and it closes none of this gap: it reasons over closed session records
outside the tree and never over the Knowledge Base itself.

**The system does not know the human.** `voice` holds how they write, and nothing holds how they decide: what they
have turned down at `/learn` and why, which arguments have moved them, which kinds of evidence they ask for before
they will try something. A record of that would make every proposal better, and it would be the most sensitive file in
the tree, which is the reason to design it rather than accumulate it.

**Nothing decays.** A note from two years ago carries the same weight as one from this morning, and `updated` is the
only field that records the difference. The practical pillar needs an answer here and the theoretical pillar does not:
a book stays as true as it was, while a note about a pit the human no longer owns goes stale without ever becoming
false. The scan catches a later note contradicting an earlier one. It catches nothing where they stopped doing it.

---

# Part 3: Knowledge Base Layout and Bootstrapping

The full Knowledge Base is a tree of topic roots, each following the standard structure of Section 1.2:

```
KnowledgeBase/
├── index.md                # Root catalog: every topic + description (machine-maintained)
├── tags.md                 # Global tag registry (machine-maintained)
├── .inbox/                 # Staging for sources on their way in – dot-prefixed, indexed nowhere
├── (optional) skills/      # PROCEDURAL – process skills every expert loads, plus adopted ones. Starts empty
│   └── [skill-name]/       #   one folder per skill (voice/, discovery/, session-loop/, ...)
│       └── SKILL.md
├── (optional) sessions/    # What a session that crossed topics worked out (Section 2.7)
│   └── [goal-title].md     #   topic: "(cross-topic)", written by a gated tool. Starts absent
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

1. **The default skills ship with the implementation, mounted rather than copied in.** The implementation provides
   starter versions of ten common skills, named one by one in Section 2.4: `ingestion-classification`, `ingest-paper`,
   `ingest-book`, `summarization`, `conflict-detection`, `tag-proposal`, `sub-topic-proposal`, `research`,
   `discovery`, and `voice`. They load from the implementation itself, so the Knowledge Base's own `skills/` folder
   starts empty and an untouched skill improves whenever the implementation does. They work out of the box and stay
   drafts: the human who wants to change one **adopts** it. A copy lands in a `skills/` folder in the tree, at the
   root or in one topic (Section 2.4), opening with one line naming the shipped skill it now shadows, and it shadows
   that skill permanently. Adoption is a decision and never an accident, because a seeded copy nobody touched is
   indistinguishable from one the human rewrote, and with no undo the implementation would have to choose between
   overwriting their work and never shipping an improvement.

   Three routes fill the tree's own `skills/` folders. The human dictates a skill to the expert; the human adopts a
   shipped one; or a session proposes one at `/learn` and the human approves its exact text (Section 2.8). The
   permanent-fork warning attaches to the **name** rather than to the route, and *Where a skill lives* in Section 2.4
   states that rule once for all three.
2. **`voice` ships with an opinionated starter profile, corrected from the human's own writing.** Every draft has a
   voice whether or not somebody wrote one down, and without a profile it is the model's own, chosen by nobody. A
   wrong default shows up in the first draft and gets fixed; an absent one never does. So the shipped skill states
   real rules, and the human corrects it from whatever writing they already have. A topic may hold its own voice,
   which replaces the root profile for that topic.
3. **The human creates the first topics on demand.** With zero topics, every inbound item is a topic gap. The human
   requests a topic, or approves one the Librarian proposes, and each new topic follows the topic creation flow in
   Section 1.9. Nobody designs a taxonomy up front. The tree grows from what the human captures.
4. **Structure catches up mechanically.** As soon as files exist, the hooks generate the indexes and the tag registry.
   Nobody seeds anything by hand.

---

# Part 4: How Projects Use the Knowledge Base

The Project Manager (separate project) orchestrates projects that consume and enrich this Knowledge Base. Project
access is agent-mediated like every other PKB interaction (Part 2): a project agent sends its request to the
Librarian, which routes it to the right Topic Expert Agents, or it connects to a known Topic Expert Agent.

## Context packs

A Topic Expert Agent assembles a context pack on request, matched to the requesting agent's role:

- **Research agents (breadth-first)** receive a Research Pack built from `topic.md`, the relevant subtrees of the root
  `tags.md`, and the `summary.md` files of the relevant topics, plus any note tagged `status.conflict-review` that
  touches the research area. A research agent reads no `index.md` unless it asks for one.
- **Implementation agents (depth-first)** receive an Implementation Pack built from the full `index.md` of the
  selected topic, the `references/[source]/[source].md` files, and the relevant solution notes. `notes/summary.md`
  loads first, because the human's rules hold the highest priority.

Every pack ranks the practical pillar above the theoretical one, the order rule 1 in Section 1.8 sets. A pack that
leads with references and appends the human's notes inverts the one rule the Knowledge Base exists to keep. No pack
carries the procedural pillar: a skill instructs the agents that work this Knowledge Base, and a consumer of a context
pack works elsewhere.

A session summary will enter a Research Pack once the human has approved it (Section 2.7), ranked after the topic's
summaries and before the conflict-review notes. The pack builder has no session role yet, so that ordering is designed
and not built. A lesson a session filed at `/learn` is an ordinary note carrying `provenance: practised`, and it enters
a pack as one.

## Conflict escalation

A project agent that meets a file tagged `status.conflict-review` bearing on its task pauses and escalates to the
human before it goes on.

## Knowledge feedback

After a project, or after a retrospective, the Project Manager proposes Knowledge Base updates:

| Update Type             | Description                                         | Example                                                              |
|-------------------------|-----------------------------------------------------|----------------------------------------------------------------------|
| **New Note**            | One observation or event from the project           | "Referral program required legal review"                             |
| **Summary Update**      | A general rule distilled from experience            | "Always check legal requirements before launching referral programs" |
| **New Solution Note**   | A reusable approach, filed as a note tagged `type.solution` | "Referral program with legal review framework"               |
| **Reference Update**    | A new reference, when the project found one         | A relevant article on referral program compliance                    |
| **Conflict Tag Update** | A conflict the human resolved, tag back to `status.approved` | Note updated after review                               |

The standards in Part 1 decide what needs the human's approval, here and on every other channel, and the caller
decides none of it. Capturing a note the human dictates and writing a first extraction of a source land unattended,
because capture must stay frictionless (goal 3 in Section 1.1). Changing human-approved content, adding a tag,
creating an extension folder, resolving a conflict, and rewriting an extraction the human has already read all wait.
**Every write under a `skills/` folder waits**, at the root and inside a topic, and it carries its own approval naming
the scope (Section 2.8): a skill gates like human-approved content and never like a capture. **Every write a session
makes waits too**, the summary and the lesson alike, and the human approves the rendered text rather than a request to
write it (Section 2.7). Once a change lands, harness maintenance regenerates the indexes and the tag registry.

The five exclusions in Section 2.8 bind a project retrospective as hard as they bind `/learn`, and a retrospective is
where they break most easily. A project that tried four approaches and shipped none of them produces a **Summary
Update** proposing the fourth as the recommended one, and the rule it writes reads the same as a rule somebody earned.
A project agent proposing an update names what the project shipped, and says so when the work never worked.

---

# Part 5: Conflict Management Example

A human note says "Always preheat the grill for 15 minutes." A reference book says "Preheating for 10 minutes is
sufficient." Section 1.7 carries the rules, and this part shows the two states of the file.

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

Had they decided the reference was right, they would have edited the note to "Preheat the grill for 10 minutes" and
cleared the tag the same way.

---

# Summary

The **Personal Knowledge Base** is the memory of the Personal Companion:

- It rests on **three pillars**, and every topic has a place for each. **Theoretical** is `references/`, what others
  established. **Practical** is `notes/`, what the human established by doing. **Procedural** is `skills/`, how the
  two of them work together. The internet is a source feeding the first and no pillar of its own.
- It stores memory, wisdom, and context, through hierarchical tags, a machine-maintained global tag registry,
  lightweight tag-based conflict management, and human-approved topic extensions.
- **Human content wins**: the practical pillar outranks the theoretical one, so a human-written note and a
  human-approved summary take precedence over a reference. The procedural pillar holds no rank on that ladder.
- **Division of labor**: the practical and procedural pillars are mostly human-generated and AI-curated. Everything
  else is AI-generated and human-curated, and hooks generate the mechanical files.
- **Breadth vs. depth**: `topic.md` and `summary.md` serve breadth-first research; `index.md` serves depth-first
  implementation.
- Agents mediate every interaction. The **Librarian** classifies each inbound item, and the harness then fans it out
  to every applicable **Topic Expert Agent** and merges their answers by attribution. Classifying is a model's
  judgment; fanning out and merging are code. Several experts may ingest one source, each extracting what its own
  topic cares about.
- **Topic Expert Agents** run the topics, one PKB template by default, overridden per topic through `expert.md`. Hooks
  enforce the mechanical standards; the experts carry the judgment work through common, overloadable skills and add
  their own domain knowledge.
- **Ten skills ship with the implementation** (Section 2.4), sorted by pillar: three that take in what arrives from
  outside, four that tend what the topic already holds, and three that serve the procedural pillar. They mount from
  the package ahead of the tree's own `skills/` folders, read-only. The tree's folders take writes, and a file
  declaring a shipped skill's **name** shadows it permanently. Five more the design describes have no file yet.
- **A session** is how anyone works with the Knowledge Base in dialog: one channel, held open on one expert or on the
  Librarian when the work crosses topics, for as long as the work lasts (Section 2.7). The counterpart is the human,
  or a project agent that needs the knowledge. A session may discuss, search, or try things for weeks and report back,
  and nobody declares which in advance. `/learn` is the only door into the tree, repeatable at any time. `/close`
  locks the channel and keeps the session record, closed, so the next session on that goal reads what this one
  declined. **An unsupervised session never issues `/learn`**, and the harness afterwards generalizes any closed
  record nobody distilled, putting the proposal in the **learning channel** (Section 2.8). Most channels file nothing.
  A channel that files something leaves a note stamped `provenance: practised`, a **session summary** stamped
  `provenance: researched`, a skill, or any combination.
- A session that searches asks with the goal and none of the human's beliefs, verifies every URL and quotation in code
  against the page text the provider returned, weighs what survives against the human's notes without touching one,
  and argues with the human while they can still act on it. **None of the session machinery is built.**
- **`/learn` reads the session record from the beginning every time** (Section 2.8), never its own last extraction,
  because chained abstractions compound. It drafts candidates, shows them as an ordinary message, argues, and lands
  the bytes the human approved and no others. **What it may conclude is bounded.** Five kinds of session output look
  like knowledge and are not: an approach that never worked, a failure the machine caused that week, a verdict that a
  tool cannot do something, an error a retry cleared, and the story of one afternoon. Filing nothing is the default.
  The same bar gates the deferred pass, so the queue holds proposals rather than sessions: about one candidate in
  seven establishes anything, and a queue of everything is a queue nobody reads.
- **A session may also teach the system how to work.** A note says what is true and a skill says how to work, and the
  test is who acts on the draft first: a skill shapes the expert's next turn before anybody asks a question. A wrong
  skill is worse than a wrong note for the same reason, and it marks nothing it shaped. The scan's fifth axis reads a
  topic's notes against its skills to catch that, and it is not built. Sections 2.7 and 2.8 record what the design has
  left unsolved: the daemon runs no unprompted scan, nothing holds how the human decides, nothing decays with age, the
  procedural pillar has no breadth file, and four questions stay open. Whether a project agent may ever hold an
  approval of its own, how long a closed session stays eligible for generalization, whether the learning channel binds
  to the Librarian or to no agent, and whether the queue needs a cap.
- The **DeepAgent harness** hosts the agent layer and exposes it through a dedicated TUI, Telegram channels, and other
  channels. A user connects to the Librarian, or to one Topic Expert Agent.
- The **Project Manager** (separate project) reads the Knowledge Base through context packs and feeds project outcomes
  and lessons learned back into it.

Every component works under **human strategic control**. AI stays tactically brilliant. Humans keep the strategic
vision.