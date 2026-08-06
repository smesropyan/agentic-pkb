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

---

# Part 1: Knowledge Base Design

## 1.1 Goals & Concepts

The Knowledge Base serves four primary goals:

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

## 1.2 Standard Topic Structure

The Knowledge Base is a hierarchical folder tree. Each topic root uses the following structure:

```
[Topic Root]/
├── topic.md            # BREADTH – Human-approved overview and map
├── index.md            # DEPTH – Machine-generated canonical index (incl. tag subtree)
├── references/         # Static knowledge (books, papers, articles)
│   ├── summary.md      # BREADTH – Human-approved overview of all references
│   └── [source-name]/  # One folder per ingested document
│       ├── [source-name].md  # DEPTH – Main source summary/content
│       └── [source-files]
├── notes/              # Human experience: observations, opinions, and solutions
│   ├── [note-title].md # Note content (standalone or main file in note folder)
│   ├── [note-title]/   # Optional folder for notes with media
│   │   ├── [note-title].md  # The note text
│   │   └── media/      # Images, screenshots, videos, etc.
│   └── summary.md      # BREADTH – Human-approved distilled rules and solutions from all notes
├── (optional) expert.md   # Topic Expert override – defaults to the PKB template (Section 2.3)
├── (optional) skills/     # Topic-specific skill overloads – same-name file overrides the common skill (Section 2.4)
├── (optional) [topic-specific]/ # Human-approved extension folders, e.g., recipes/ for Cooking
└── (optional) sub-topics/ # Deeper nested topics with the same structure
```

**Naming convention for folder-hosted items**: Every item placed inside its own folder uses a main content file named
after the item itself:

- `notes/[note-title]/[note-title].md`
- `references/[source-name]/[source-name].md`

The same convention applies inside topic-specific extension folders (e.g., `recipes/[recipe-title].md`, or
`recipes/[recipe-title]/[recipe-title].md` with media).

Do not use generic `index.md` for item content. The topic-level `index.md` remains the machine-generated canonical
directory index.

## 1.3 File Types and Creation Rules

| File                              | Built By        | Purpose                                                                                                                              |
|-----------------------------------|-----------------|--------------------------------------------------------------------------------------------------------------------------------------|
| `topic.md`                        | **AI + Human**¹ | Breadth map for research agents. AI drafts and maintains the overview; the human adds insight and approves.                          |
| `index.md` (topic root)           | **Hooks**       | Depth index for precise retrieval, incl. the topic's tag subtree and cross-topic mappings. Regenerated by harness hooks on change.   |
| `expert.md` (optional)            | **Human + AI**  | Topic-specific override of the PKB Topic Expert template (Section 2.3). Human-created; AI assists.                                   |
| `skills/[skill].md` (optional)    | **Human + AI**  | Topic-specific overload of the common skill with the same name (Section 2.4). Human-created; AI assists.                             |
| `references/summary.md`           | **AI + Human**¹ | Breadth overview of static knowledge. AI drafts the summary. Human edits and approves it.                                            |
| `references/[source]/[source].md` | **AI**          | Depth summary/content for a specific reference. Generated by the ingestion skill.                                                    |
| `notes/[note-title].md`           | **Human + AI**  | Human-written note: an observation, opinion, or solution (tagged `type.solution`). AI assists with clarity and structure only.       |
| `notes/summary.md`                | **AI + Human**¹ | Breadth overview of experience: distilled rules and notable solutions. Human edits and approves. **Highest priority for decisions.** |
| `tags.md` (PKB root)              | **Hooks**       | Global tag registry, purely derived from file frontmatter. Regenerated mechanically whenever files change.                           |
| `index.md` (PKB root)             | **Hooks**       | Root catalog: every topic with its description, aggregated from `topic.md` frontmatter – the Librarian's routing view.               |
| `skills/[skill].md` (PKB root)    | **Human + AI**  | Common skill – judgment maintenance or collaboration (`voice.md`, `discovery.md`, ...). Human-created; AI assists.                   |

¹ **"AI + Human"** means the AI proposes a draft and the human approves or edits it before finalization.

**Collaboration rule**: Notes, skills, and `expert.md` overrides are **human-generated, AI-curated** – they carry
the human's experience and ways of working; the AI assists with clarity, grammar, and structure. Every other
meaning-carrying file (`topic.md`, the summaries) is **AI-generated, human-curated** – the AI drafts, the human adds
insight and approves. Reference depth files are AI-generated on ingestion; the human curates them at the summary
level. Mechanical files (`index.md`, root `tags.md`) are generated by hooks and curated by no one.

## 1.4 Metadata Requirements

Every human- or AI-authored markdown file includes YAML frontmatter (machine-generated files – `index.md` and the
root `tags.md` – carry only minimal generated frontmatter):

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

The `description` field is required on every file. It is what deterministic `index.md` generation extracts
(Section 1.9).

`related_topics` lists related topic paths in tag notation (e.g., `bbq.equipment`). It is the single place where
cross-topic relationships are declared – harness hooks aggregate these declarations into the registry's cross-topic
mappings (Section 1.9).

Conflict handling adds transient fields: `review_note` while a conflict is open, `last_reviewed` after resolution
(Section 1.7).

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
    - `topic.cooking.grilling`
        - `topic.cooking.grilling.charcoal`
        - `topic.cooking.grilling.gas`
    - `topic.cooking.heat-management`
    - `topic.cooking.baking`
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

- `domain.legal.compliance`
- `domain.marketing.ads`
- `domain.engineering.security`

## Cross-topic mappings (aggregated from `related_topics`)

- `topic.cooking.grilling` ↔ `topic.bbq.equipment`
- `topic.cooking.heat-management` ↔ `topic.physics.thermodynamics`
```

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

### Agent roles in the dialog

The topic's Topic Expert Agent (see Part 2) facilitates this dialog with the human. Using the common judgment skills
(Section 1.9) it proposes drafts and detects conflicts, while harness maintenance hooks keep the structure consistent.
Collaboration skills (Section 2.4) shape the dialog itself: drafts are written in the human's `voice.md`, and idea
discovery or research sessions follow their respective skills. It never finalizes human-approved content on its own.

## 1.7 Conflict Detection & Resolution

### General rule

Human content always wins over static knowledge.

Human content includes human-written notes and human-approved summaries. If a human note conflicts with a static
reference, the note is correct. If it is not correct, the human edits the note until it wins.

The system does not overwrite human content. The system only brings conflicts to human attention and tracks resolution.

### Conflict types

| Type            | Description                                             | Example                                            |
|-----------------|---------------------------------------------------------|----------------------------------------------------|
| `contradiction` | Statements directly oppose each other                   | "Preheat grill 15 min" vs. "Preheat grill 10 min"  |
| `nuance`        | Statements are both true but under different conditions | "High heat for searing" vs. "Low heat for smoking" |
| `outdated`      | Static knowledge is older and no longer accurate        | 2010 book vs. 2024 human note                      |

### Detection process

1. **Trigger**: A harness maintenance hook schedules a conflict scan whenever new notes or references are added. The
   human can also request a scan on demand.
2. **Method**: The Topic Expert Agent executes the scan. It compares `notes/summary.md` against
   `references/summary.md`, individual notes against references, and notes against notes. It uses semantic analysis
   informed by its domain knowledge (e.g., recognizing when two statements are both true under different conditions).
3. **Classification**: The AI proposes a conflict type and a confidence score.

### Conflict tagging

When the AI detects a conflict with human content, it must do these steps:

1. Add the tag `status.conflict-review` to the human content file.
2. Add a short `review_note` to the file metadata. The note describes the conflict.
3. Do not change the file content automatically.

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

4. **Cross-Topic Solutions**: A solution note lives in exactly one topic – the most relevant one. There are no
   copies. Cross-topic discovery is handled by tags, `related_topics` metadata, and Librarian routing.

5. **Sub-Topics**: Deeper nested topics follow the same structure recursively. A sub-topic is served by its parent
   topic's Topic Expert unless it has its own `expert.md` – the same resolution pattern as the template override.

6. **Media Handling**: Notes with media use a dedicated folder. The `[note-title].md` inside contains the note text (or
   a machine-extracted textual description of embedded media). Agents read the text instead of parsing binary files.

7. **Tag Discipline**: Use hierarchical tags. The root `tags.md` registry is maintained mechanically by harness
   hooks. Propose new tags to the human. Do not create ad-hoc tags.

## 1.9 Topic Maintenance Model

> **Design principle**: *Enforce structure mechanically, curate meaning agentically.* There is no separate maintainer
> agent. Deterministic maintenance is performed by harness hooks that cannot be skipped or forgotten; judgment work is
> performed by the topic's Topic Expert Agent (see Part 2) through common, overloadable skills.

Maintenance is split across three layers.

### Layer 1: Mechanical enforcement (harness hooks)

Whenever a file in a topic is created, changed, renamed, or removed, the DeepAgent harness programmatically invokes
the mechanical maintenance skills. No agent judgment is involved, and no agent can skip them:

- Validate YAML frontmatter (required fields, tag syntax and depth), file naming conventions, and consistency between
  declared metadata (`topic`, `source_type`, `topic.*`/`type.*` tags) and the file's actual location.
- Regenerate the topic's `index.md` – including its tag subtree and cross-topic mappings. Because every file carries
  a `description` in its frontmatter (Section 1.4), index generation is fully deterministic: walk the tree, extract
  the frontmatter.
- Regenerate the root `tags.md` registry from the tags actually used in files, aggregating cross-topic mappings from
  `related_topics` declarations – plain deterministic code, no LLM tokens spent, purely derived.
- Regenerate the root `index.md` – a catalog of every topic and its `topic.md` description – the Librarian's
  one-file routing view.
- Update `updated` timestamps.
- Flag broken links and orphaned files.
- Scaffold the standard structure (Section 1.2) for new topics and sub-topics after human approval.
- Schedule a conflict scan covering the changed files.

> **Implementation note**: "Schedule a conflict scan" means Layer 1 only *queues* the work – the scan itself is
> executed by the Topic Expert Agent (Layer 2). The DeepAgent harness therefore needs a lightweight queue or trigger
> mechanism that hands scheduled scans to the topic's expert (e.g., on its next activation or on a timer).

### Layer 2: Common judgment skills (overloadable)

Work that requires understanding content is defined once, as common skills loaded by every Topic Expert Agent:

- **Summarization** – Draft `references/summary.md` and `notes/summary.md` following the dialog rules in Section 1.6.
- **Conflict detection and classification** – Execute the scans scheduled by Layer 1 and classify findings per
  Section 1.7.
- **Tag proposal** – Propose new hierarchical tags for human approval before filing content that uses them; the
  registry picks them up mechanically once used.
- **Ingestion classification** – Classify inbound content as a reference or a note (observation, opinion, or a
  solution tagged `type.solution`); draft the files with metadata, including the `description` that Layer 1 relies
  on and the textual descriptions of embedded media required by rule 6 in Section 1.8.
- **Sub-topic proposals** – Propose splitting a topic that has grown too large.

A Topic Expert Agent may **overload** a common skill with a human-created, AI-assisted topic-specific version. For
example, the Cooking expert's summarization skill may require temperature and doneness tables in recipe summaries.
An overload extends the common procedure but never weakens the general standards: Layer 1 validates the output
regardless of which skill version produced it. The same mechanism extends to the collaboration skills (voice,
discovery, research) defined in Section 2.4.

### Layer 3: Topic Expert dialog

The Topic Expert Agent runs the judgment skills in dialog with the human: proposing drafts, presenting conflicts, and
collecting approvals (Sections 1.6 and 2.3). It authors content so that Layer 1 stays deterministic – for example, it
writes the `description` frontmatter when filing new content.

Cross-topic mappings are not curated by anyone: Layer 1 aggregates them mechanically from `related_topics`
declarations into the root `tags.md`. The **Librarian** (Section 2.2) consults them when routing across topics.

### Topic creation

When a human requests a new topic (directly, or by approving a topic proposed by the Librarian – see Section 2.2):

1. A Layer 1 skill scaffolds the standard structure from Section 1.2, with placeholder `summary.md` files.
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

Responsibilities:

- **Routing** – Analyze each inbound request or piece of information. Route it to the appropriate Topic Expert Agent.
  Route requests that span multiple topics to all relevant experts and merge their responses.
- **Topic catalog** – Route using the root `index.md`: a hook-generated catalog of every topic and its description,
  aggregated from `topic.md` frontmatter. Expert overrides are visible in the folder tree (`expert.md`). Nothing is
  maintained by hand.
- **Topic gaps** – When inbound information fits no existing topic, propose a new topic to the human (following the
  topic creation flow in Section 1.9).
- **Cross-topic coordination** – Use the cross-topic mappings in the root `tags.md` (aggregated mechanically from
  `related_topics` declarations) to involve secondary topics when relevant.

The Librarian holds no deep topic knowledge itself. It goes wide; Topic Expert Agents go deep.

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
  `type.solution`); draft the files; apply metadata and tags per the standards.
- Carry out the judgment side of topic maintenance (Section 1.9); the mechanical side is enforced by harness hooks.
- Manage topic-specific extensions (with human approval).
- Escalate to the human as required by Part 1: summary approval, new tags, and conflict resolution.

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

## 2.4 Common Skills and Skill Overloading

The PKB root `skills/` folder holds the common skill files loaded by every Topic Expert Agent. There are two
families:

- **Judgment maintenance skills** (Section 1.9, Layer 2) – summarization, conflict detection and classification, tag
  proposal, ingestion classification, and sub-topic proposals.
- **Collaboration skills** – how agents work *with the human*, the same pattern coding harnesses use for their
  skills:
    - `voice.md` – the human's writing voice and style. Every draft an agent produces (curated notes, summaries,
      `topic.md`) is written in this voice.
    - `discovery.md` – how to run idea-discovery and brainstorming sessions against KB content.
    - `research.md` – how to explore breadth-first, generate options, and present them for direction selection.
    - Others as needed (e.g., interviewing the human to draw out experience for notes).

Skills are like notes: **human-created, AI-assisted** (Section 1.3). They encode how the human wants agents to work
– something only the human can author. (The mechanical Layer 1 skills are deterministic harness code, not skill
files.)

The extension rule: a Topic Expert Agent may **overload** any non-mechanical skill with a topic-specific version
that extends the default with domain intelligence – a recipe-writing voice for Cooking, a tasting-session discovery
skill, and so on. Like all skills, overloads are human-created with AI assistance. Overloads live in the topic's
`skills/` folder; a file with the same name as a common skill overrides it, resolved by the harness exactly like
`expert.md`. An overload never redefines the general standards – the mechanical skills validate all output
regardless of which skill version produced it.

## 2.5 DeepAgent Harness and Access Channels

The agent layer runs on the **DeepAgent** agentic harness. DeepAgent hosts the Librarian and the Topic Expert Agents
and exposes them through multiple access channels:

- A dedicated **TUI**
- **Telegram channels**
- Other channels as needed (chat apps, APIs, etc.)

A PKB user can connect to the **Librarian** – the default entry point, which routes to the right experts – or connect
**directly to a specific Topic Expert Agent** when they already know which topic they are working with.

## 2.6 Agent Hierarchy

```
┌────────────────────────────────────────────────────┐
│             PKB USERS / EXTERNAL AGENTS            │
│          (humans, Project Manager agents)          │
└────────────────────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────┐
│                 DEEPAGENT HARNESS                  │
│       (dedicated TUI, Telegram channels, ...)      │
└────────────────────────────────────────────────────┘
          │                              │
          ▼ default entry                ▼ direct connection
┌────────────────────┐      ┌──────────────────────────┐
│     LIBRARIAN      │─────▶│   TOPIC EXPERT AGENTS    │
│  (root PKB agent:  │      │  (one per topic: common  │
│      routing)      │      │  standards + unique      │
└────────────────────┘      │  topic expertise)        │
                            └──────────────────────────┘
                                         │
                  judgment + collaboration skills (overloadable)
                      + harness maintenance hooks (mechanical)
                                         │
                                         ▼
┌────────────────────────────────────────────────────┐
│               KNOWLEDGE BASE TOPICS                │
│      topic.md, index.md, references/, notes/       │
│         + root index.md, tags.md, skills/          │
└────────────────────────────────────────────────────┘
```

---

# Part 3: Knowledge Base Layout and Bootstrapping

The full Knowledge Base is a tree of topic roots, each following the standard structure defined in Section 1.2:

```
KnowledgeBase/
├── index.md                # Root catalog: every topic + description (machine-maintained)
├── tags.md                 # Global tag registry (machine-maintained)
├── skills/                 # Common skills: judgment maintenance + collaboration (voice.md, discovery.md, ...)
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
│   ├── (optional) expert.md
│   ├── (optional) skills/
│   ├── (optional) [topic-specific]/
│   └── (optional) sub-topics/
│       └── (same structure recursively)
└── (other topic roots...)
```

## Bootstrapping an empty PKB

The PKB starts empty. The path to the steady state:

1. **Default skills ship with the implementation.** The PKB implementation provides starter versions of the common
   skills (summarization, conflict detection, tag proposal, ingestion classification, sub-topic proposals,
   `discovery.md`, `research.md`). They are functional out of the box but are treated as drafts: the human rewrites
   them – with AI assistance – as their preferences become clear. From that point on they are human-created,
   AI-curated like all skills.
2. **`voice.md` is drafted from the human's existing writing.** The natural first samples are whatever writing the
   human already has (early notes, or documents they supply). The AI drafts the voice description from those
   samples; the human corrects it. It improves over time as notes accumulate, through the same propose-and-approve
   loop as everything else.
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
  the research area. Research agents do not read `index.md` files unless explicitly asked.
- **Implementation agents (depth-first)** receive Implementation Packs built from the full `index.md` of the
  selected topic, detailed `references/[source]/[source].md` files, and relevant solution notes.
  `notes/summary.md` is always loaded first – human rules have the highest priority.

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

Knowledge Base commits require explicit human approval. After approval, harness maintenance hooks regenerate the
relevant `index.md` files and the root `tags.md` registry.

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
- All interactions are **agent-mediated**. The **Librarian** – the root PKB agent – routes inbound information and
  requests to the appropriate **Topic Expert Agents**.
- **Topic Expert Agents** run each topic – a single PKB template by default, overridable per topic via `expert.md`.
  Harness hooks enforce the mechanical PKB standards; the experts carry out the judgment work through common,
  overloadable skills and add unique domain knowledge, topic-specific file organization, and the best ways to
  interact with their topic.
- **Collaboration skills** – `voice.md` for writing, discovery and research skills for ideation – shape how agents
  work with the human. Common by default, overloadable per topic, like every other skill.
- The **DeepAgent harness** hosts the agent layer and exposes it through a dedicated TUI, Telegram channels, and other
  channels. Users can connect to the Librarian or directly to a specific Topic Expert Agent.
- The **Project Manager** (separate project) consumes the Knowledge Base through context packs and feeds project
  outcomes and lessons learned back into it.

All components work under **human strategic control**. AI remains tactically brilliant. Humans retain the strategic
vision.