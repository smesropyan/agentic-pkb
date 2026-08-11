# Personal Knowledge Base: Design Specification

---

## Guiding Principles

- A successful AI-driven future depends on human–AI synergy, and that synergy needs a defined, active human role.
- Breadth of knowledge drives creativity and novel insight more than depth does (David Epstein, *Range*).
- On a problem, humans go wide and AI goes deep (Marc Andreessen).
- AI agents are tactically brilliant but strategically retarded (author).

---

## System Goal

The Personal Knowledge Base (PKB) is an AI-assisted expert for the subject areas the operator works in. It fuses
theoretical, practical and procedural knowledge about each topic. It learns from the experience of working with the
operator on operator-led objectives. The operator states an objective to the Librarian, which researches breadth-first
across all topics. The operator may address a single Topic Expert instead, when the owning topic is known. When an
objective requires a new tool or a process to follow, the session produces one or more instruction sets. An instruction
set states why the work is necessary and what it must achieve. It does not specify an implementation. A human follows
the instruction set, or another agentic system implements it. One session may produce several instruction sets, for
separate experiments or for separate tools. The operator reports the results of each experiment or tool back into the
session. The session uses them to advance the objective.

The session ends in success or in failure. In both cases an agent and the operator analyze the record together and
distil a repeatable skill or a piece of practical knowledge. The operator approves what enters the PKB. Each entry
improves the expert agents. This creates a self-learning loop.

---

## The Three Pillars

The PKB holds three kinds of knowledge, and every topic has a place for each:

| Pillar          | Folder        | It holds                                                                           |
|-----------------|---------------|------------------------------------------------------------------------------------|
| **Theoretical** | `references/` | Knowledge others established. Books, papers, articles, anything read.              |
| **Practical**   | `notes/`      | Knowledge the operator established by doing, under their own conditions.           |
| **Procedural**  | `skills/`     | How the operator and the agent work together toward an objective the operator set. |

Knowledge + Experience = Wisdom names the first two pillars. The third says how the operator and the agent spend that
wisdom. Scaffolding a new topic creates `references/` and `notes/`. The topic's first approved skill creates `skills/`
(Section 1.2).

The procedural pillar holds skill files. Skills emphasize learned procedures on doing research, or simply on searching
or on handling requests in a specific knowledge area.

The external world supplies theoretical knowledge: books, papers and internet articles. The PKB ingests a source during
a session, when the operator points it at a web page or gives it a file that holds an eBook or a scientific paper.

The three pillars carry different weight. Practical and procedural knowledge outrank theoretical knowledge. Practical
knowledge states how to apply a theory to an objective.

Humans and agents reach a topic's knowledge through sessions. A session is a long-standing conversation that pursues an
objective. A session connects to the Librarian to gain expertise across several topics, or to one Topic Expert when the
operator already knows which area owns the work.

Both subject pillars carry a summary that the operator approves: `references/summary.md` and `notes/summary.md`. A
summary helps an agent find an analogous problem or solution in another area of expertise. It bounds what an agent reads
in exploration mode, and the notes and references themselves supply the detail an agent needs in exploitation mode, when
the task is defined.


---

## Sessions and the Self-Learning Loop

The operator can ingest theoretical knowledge from outside, because someone else already wrote it down. Practical and
procedural knowledge exist nowhere to ingest: nobody has run the operator's experiment under the operator's conditions,
and nobody has written down how the operator and an agent reach an objective together. That knowledge comes from
working, and a session is the working.

A session opens on an objective, and the work happens inside it. The operator brings a question, the agent answers out
of what the topic already holds, and the two of them decide what to try next. The operator goes away and tries it, then
comes back with what happened, days or weeks later, and the session picks up where it left off.

Everything is dialog: the operator asks, the agent drafts, the two of them work the text until it says what the operator
means, and the operator instructs the write. No form asks the operator to classify anything, and they manage no file.
Their instruction is the whole of what makes a write happen.

`/close` says the operator has nothing more they want to craft in this context. It judges nothing, because a session
that failed teaches as much as one that succeeded. Every closed session is then analyzed, and the analysis brings back
what it found in dialog rather than as a report: what the session established, what is worth keeping, and where it
disagrees with what the topic already holds. The operator settles them one at a time, and `/end` finishes the session.

Most sessions leave nothing behind, and that is the correct outcome. A loop that files something every time files noise,
and the operator is the one who has to read it later. The rest becomes knowledge the expert agents hold, so the next
session on the same ground starts where this one finished rather than where it began. That is the sense in which the
system learns: the operator taught it, one objective at a time.

---

## The Librarian and the Topic Experts

No agent is good at both reach and depth, because the same thing that makes an expert an expert is what blinds it
outside its own subject. A Topic Expert carries one subject: its sources, its notes, its own way of working, and the
judgment that subject rewards. That load is what makes its answers precise, and it is also what keeps the expert from
noticing that the answer lies somewhere else entirely. Reach and depth pull against each other, so the PKB declines to
choose between them and holds each in its own kind of agent.

The Librarian works across topics and holds none of them. It runs in exploration mode: the ground an objective sits on,
the topics that bear on it, and the second topic the operator did not think of. Breadth is what lets an analogy from one
subject reach a problem in another, and that is where novel insight comes from. A Topic Expert works inside one topic
and runs in exploitation mode: it goes to that subject's sources and its notes, and returns an answer precise enough to
act on. Neither agent is a lesser form of the other, and neither is the other's manager. One finds the ground and the
other stands on it.

Research uses both, in that order. Wide first, because an objective as the operator first states it is rarely aimed at
the right ground. Deep second, because breadth returns a map and never an answer the operator can act on. Exploration
without exploitation is a list of promising directions, and exploitation without exploration answers only the question
the operator already knew to ask.

---

# Part 1: PKB Design

## 1.1 PKB Harness Design Goals

1. **Fuse the three pillars** into one body of knowledge about each topic: what others established, what the operator
   established by doing, and how the operator and the agent work (The Three Pillars, above).
2. **Keep every interaction agent-mediated and frictionless**. The operator and external agents work through the
   Librarian and the Topic Experts. They capture, retrieve, and refine knowledge in dialog, over any connected channel,
   with no file management of their own and no external tools.
3. **Enforce common standards and preserve topic depth**. Harness hooks keep structure, metadata and tags identical
   across topics, and the shared skills give every expert the same default judgment, conflict detection at the write
   among them. Each Topic Expert adds its own domain knowledge, its own organization and its own overload of a shared
   skill on top.
4. **Grow the PKB**. Acquire theoretical knowledge by ingesting books or articles. Acquire practical and procedural
   knowledge by distilling what the operator tried and reported back, in the analysis every closed session gets (Section
   2.8).

## 1.2 Standard Topic Structure

The PKB is a folder tree. *The tree* names those folders throughout this document. Each topic root uses this structure:

```
[Topic Root]/
├── topic.md            # Human-approved overview and map. Helpful for exploration.
├── index.md            # Machine-generated canonical index (incl. tag subtree). Helpful for exploitation.
├── references/         # THEORETICAL PILLAR – what others established (books, papers, articles)
│   ├── summary.md      # Human-approved overview of all references. Helpful for exploration.
│   └── [source-name]/  # One folder per ingested document
│       ├── [source-name].md  # Map of the source: a section per part, a bullet per argument. Helpful for exploitation.
│       └── [source-files]
├── notes/              # PRACTICAL PILLAR – what the operator established by doing
│   ├── [note-title].md # Note content (standalone or main file in note folder)
│   ├── [note-title]/   # Optional folder for notes with media
│   │   ├── [note-title].md  # The note text
│   │   └── media/      # Images, screenshots, videos, etc.
│   └── summary.md      # Human-approved distilled rules and solutions from all notes. Helpful for exploration.
├── (optional) skills/  # PROCEDURAL PILLAR – how this topic is worked (Section 2.4)
│   └── [skill-name]/
│       └── SKILL.md
├── (optional) expert.md   # Topic Expert override – defaults to the PKB template (Section 2.3)
└── (optional) sub-topics/ # Deeper nested topics with the same structure
```

The three pillar folders hold everything the topic knows, and `topic.md` and `index.md` map them. The topic's first
approved skill creates the `skills/` folder, and that approval is the approval on the folder. Section 2.4 says which
skills live there and which live at the PKB root.

**Naming convention for folder-hosted items**: give every item inside its own folder a main file named after it.

- `notes/[note-title]/[note-title].md`
- `references/[source-name]/[source-name].md`

A topic organizes its content by tag rather than by folder, so every topic has one shape and a tag reaches across topics
where a folder cannot. A Cooking recipe is a note tagged `topic.cooking.recipes`, and Section 1.5 carries the tag rules.

One `sessions/` folder sits at the PKB root (Part 3), and it holds one file per session for that session's whole life,
whether the session opened on the Librarian or on one Topic Expert (Section 2.7). One place to sweep is what the
analysis needs, because it generalizes over sessions, and it reads the whole of one at every close (Section 2.8).

Do not put item content in an `index.md`. The topic-level `index.md` stays the machine-generated directory index.

One file per source, and it is a map of that source rather than a précis of it. `[source-name].md` carries the source's
thesis, its provenance, one section per part of the source as the source names them, one bullet per argument the topic
cares about, and an honest list of what nobody read. The shape prevents a confident write-up of the part that fit in one
context window, with nothing recording that the rest was never opened. Re-ingest a source as often as it is worth
re-ingesting, because each pass reconciles with what is there and then appends what it covered, what it skipped, and
when.

## 1.3 File Types and Creation Rules

**AI + Human** and **Human + AI** both mean the expert drafts and the operator approves or edits the exact text before
the file lands, and a session's running record is the one exception, because it says what happened rather than claiming
anything is true (Section 1.6). The labels differ in whose substance it is: the operator's own experience, or the
expert's own reading and reasoning. **AI** alone means the expert writes the file inside the turn, on the operator's
instruction, with nothing waiting on their approval before it lands. **Hooks** means harness code writes the file and
nobody curates it.

| File                                        | Built By                    | Purpose                                                                                                                                                                                                                                                                                                                                |
|---------------------------------------------|-----------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `topic.md`                                  | **AI + Human**              | Breadth map for a breadth-first reader. The expert drafts and maintains the overview. The operator adds insight and approves.                                                                                                                                                                                                          |
| `index.md` (topic root)                     | **Hooks**                   | Depth index for precise retrieval, with the topic's tag subtree and cross-topic mappings. Harness hooks regenerate it on change.                                                                                                                                                                                                       |
| `expert.md` (optional)                      | **Human + AI**              | Topic override of the PKB Topic Expert template (Section 2.3). The operator settles what it says and the expert drafts it.                                                                                                                                                                                                             |
| `skills/[skill-name]/SKILL.md` (optional)   | **Human + AI**              | The procedural pillar for one topic: a skill only this topic's expert loads (Section 2.4). The operator settles what it says and the expert drafts it. It is the one skill path inside the expert's own subtree.                                                                                                                       |
| `references/summary.md`                     | **AI + Human**              | Breadth summary of the theoretical pillar. The expert drafts it. The operator edits and approves.                                                                                                                                                                                                                                      |
| `references/[source-name]/[source-name].md` | **AI**, then **AI + Human** | Depth map of one source: thesis, provenance, a section per part of the source, a bullet per argument, and what nobody read. The ingestion skill writes the first pass inside the turn, because the operator named the source, and the operator approves any later pass that rewrites it.                                               |
| `notes/[note-title].md`                     | **Human + AI**              | What the operator knows from their own practice: an observation, an opinion, or something that worked (tagged `type.solution`). They settle what it says, in the turn or in the analysis after `/close` once they tried the thing, and the expert drafts it (Section 2.7). The operator approves the exact text.                       |
| `notes/summary.md`                          | **AI + Human**              | Breadth summary of experience: distilled rules and notable solutions. The operator edits and approves. **Highest priority among the knowledge files.**                                                                                                                                                                                 |
| `tags.md` (PKB root)                        | **Hooks**                   | Global tag registry, derived from file frontmatter. Regenerated whenever files change.                                                                                                                                                                                                                                                 |
| `index.md` (PKB root)                       | **Hooks**                   | Root catalog: every topic with its description, aggregated from `topic.md` frontmatter – the Librarian's routing view.                                                                                                                                                                                                                 |
| `sessions/[objective-title].md` (PKB root)  | **AI + Human**              | One file per session, for its whole life (Section 2.7): the objective and the experts, the running record the session writes as it goes, the synthesis of what it worked out, and the distillation the analysis appends. Harness code renders it, and the write needs the root tool of the row below, because the Librarian writes nothing and no expert writes outside its own subtree. The record says what happened and needs no approval, and the operator approves the synthesis word for word. |
| `skills/[skill-name]/SKILL.md` (PKB root)   | **Human + AI**              | The procedural pillar for every topic: a skill every expert loads (Section 2.4). The folder starts empty. A write here sits outside every expert's subtree, so it needs a root tool.                                                                                                                                                   |

**Collaboration rule**: the practical and procedural pillars are **human-generated, AI-curated**. `notes/`, the
`skills/` folders and `expert.md` overrides carry the operator's own experience and their own ways of working, and the
expert assists with clarity, grammar and structure. Every other meaning-carrying file, `topic.md`, the breadth summaries
and a session's synthesis, is **AI-generated, human-curated**: the expert drafts, and the operator adds insight and
approves. The expert writes the theoretical pillar's depth files on first ingestion, and the operator curates them at
the summary level and approves any later pass that rewrites one. Hooks generate `index.md` and the root `tags.md`, and
nobody curates them.

An expert may draft a note or a skill itself in the analysis, and both stay on the human-generated side of that line.
The experience in them is the operator's: they cooked it, they ran it, they came back and said what happened. The expert
drafts the wording from the session file's running record, argues about what the experience means, files the text the
operator approves word for word, and never argues about whose experience it was.

Skill files are a file class of their own. Everything under a `skills/` folder, at the PKB root or inside a topic, plus
`expert.md`, instructs an agent rather than describing a subject, and the PKB rules for knowledge files pass it by
(Section 1.4).

## 1.4 Metadata Requirements

Every markdown file that carries knowledge includes YAML frontmatter. Three file classes exist, and this section governs
the first alone:

1. **Knowledge files** – notes, references, the breadth summaries, `topic.md` and session files. Full PKB frontmatter,
   as below. A session file is one of these too, and no topic owns it: it sits in the root `sessions/` folder, its
   `topic` field reads `(session)`, it carries a `topic.*` tag for each expert that took part and names those topics in
   `related_topics` so the registry picks up any crossing, and the check comparing a declared topic against a file's
   location has nothing to compare (Section 2.7). Its frontmatter is valid from the turn the session opens, carrying the
   objective, the dates and the experts the session started with, and it gains a `topic.*` tag as each further expert
   answers, so a session file carrying a running record and no synthesis yet is a valid knowledge file. These rules
   govern the frontmatter, and Section 2.7 governs the body.
2. **Machine-generated files** – `index.md` at any level and the root `tags.md`. Minimal generated frontmatter only.
3. **Skill files** – everything under a `skills/` folder, at the PKB root or inside a topic, plus `expert.md`. These
   instruct an agent rather than describing a subject, so a skill file carries no PKB frontmatter, appears in no
   `index.md` and contributes no tags, and every rule that reads frontmatter passes it by. A `SKILL.md` carries the
   DeepAgent harness's own two fields, `name` and `description`, and nothing else. The PKB fields break the harness's
   parser, and the harness then drops the skill without an error anywhere.

```yaml
---
title: "Grill Performance in Windy Conditions"
description: "How wind affects grill temperature and how to compensate for it"
topic: "Cooking"
tags:
  - topic.cooking.grilling
  - topic.cooking.heat-management
  - type.note
created: 2024-10-15
updated: 2024-10-16
related_topics: [ bbq, weather ]
source_type: note  # note, reference, solution, summary
---
```

Every knowledge file, class 1 above, carries a `description`, because index generation extracts it (Section 1.9). The
generated files carry their own minimal frontmatter instead: the root `tags.md` in Section 1.5 has a `title` and a
`source_type` and no description, and asking it for one would ask the generators to fail their own validation.

`related_topics` lists related topic paths in tag notation, such as `bbq.equipment`. Declare a cross-topic relationship
here and nowhere else. Harness hooks aggregate these declarations into the registry (Section 1.9).

## 1.5 Hierarchical Tags

Hierarchical tags improve context filtering, inheritance and agent retrieval: a nested tag states a relationship, and an
agent filters at any level of the tree.

### Tag namespaces and values

Use dot notation, and each dot adds a level, so `topic.cooking.grilling` sits under `topic.cooking`. Three namespaces
exist and nothing invents a fourth. `topic.*` and `domain.*` are open trees the operator grows a branch at a time, and
`type.*` is a closed set whose every value is below.

| Tag               | When to reach for it                                                                                                                     | Why it exists                                                                                                                                           |
|-------------------|------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|
| `topic.*`         | On every knowledge file, as deep as the content is specific. On a session file, one per expert that took part.                           | A topic organizes by tag rather than by folder (Section 1.2), so the tag carries what a folder used to and reaches across topics where a folder cannot. |
| `domain.*`        | On a file whose subject cuts across topics by function rather than by area: `domain.legal.compliance` on a Cooking note about labelling. | One file sits in one folder, and a domain gathers files that share a way of working rather than a subject.                                              |
| `type.note`       | On an observation or an opinion the operator holds from their own practice.                                                              | Separates what the operator noticed from what they proved works, so a search for an answer does not return every opinion.                               |
| `type.solution`   | On a note recording something that worked and is worth reaching for again.                                                               | A defined task wants the answer first, and the tag is what ranks a solution above the notes around it.                                                  |
| `type.reference`  | On the map of an ingested source, under `references/`.                                                                                   | Marks the theoretical pillar, which loses to human content when the two disagree (Section 1.8).                                                         |
| `type.summary`    | On `topic.md`, on `notes/summary.md` and `references/summary.md`, and on a session file.                                                 | Exploration reads breadth and exploitation reads depth, and the tag is what keeps the two separable in a context pack.                                  |

### Tag rules

- Every knowledge file carries at least one `topic.*` tag and exactly one `type.*` tag.
- Start a tag with a broad namespace. Add narrower terms as the subject needs them.
- Create no ad-hoc tag. The expert proposes a new tag and the operator approves it before the expert files content that
  uses it. A tag carries what a folder used to, so an ad-hoc one loses the file rather than misfiling it.
- Keep tag depth to 4 levels or fewer.
- A nested tag implies its parent. `topic.cooking.grilling` also means `topic.cooking`.
- The expert assembles a context pack from tags, filtering by namespace and depth.
- Sessions add no namespace. A lesson the operator earned files as `type.solution` when it worked and `type.note` when
  it did not, and a session file carries `type.summary` with a `topic.*` tag for each expert that took part.

### Tag registry (`tags.md` at the PKB root)

Tags are flexible and relational, so the PKB keeps one `tags.md` registry at its root: the canonical tag tree and
lightweight ontology for agent ingest, holding namespace definitions, per-topic subtrees, and cross-topic mappings. The
`topic` namespace renders as one section per top-level topic root rather than as a single tree, so `topic.cooking` heads
its own section and its sub-topics nest inside it. Each topic's `index.md` embeds its own subtree for local depth work.

Maintenance is mechanical. Harness code regenerates the registry, scanning the files, rendering the full hierarchy and
aggregating the cross-topic mappings from the `related_topics` declarations in file frontmatter, and it spends no LLM
tokens. The generator supplies the static definition of the standard namespace, `type.*`. The registry is derived, so it
reflects the tags the files use by construction, and governance stays in the dialog rather than in the file.

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
    - `topic.cooking.recipes`

## Namespace: type

- `type.note` – human-written note
- `type.reference` – static source
- `type.solution` – reusable solution (a note tagged as a solution)
- `type.summary` – breadth overview

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

Every namespace renders the same way: a nested tag implies its parent, so `domain.*` is a tree as `topic.*` is, and the
generator sorts siblings case-insensitively by the full tag string, which is what makes regeneration idempotent.

## 1.6 Human–AI Collaboration in the PKB

Every conversation with the PKB is a session, and Part 4's batch route is the one way in that is not one. The operator
opens one on the Librarian or on one Topic Expert, neither by default, and works in it for as long as the work lasts
(Section 2.7). One collaboration model covers what a session files: the operator asks, the agent drafts, and the
operator approves the exact text before it lands. The artifact changes only who supplies the substance, the
collaboration rule in Section 1.3 already draws that line, and Section 2.7 says when each write lands, in the turn that
asked for it or in the analysis session after `/close`. Two things sit outside the model. A session's running record
needs no approval, because it says what happened rather than claiming anything is true, and naming a source is the
approval on the first extraction of it, because the operator chose the source and the extraction is the reading of it
(Section 1.3).

Three ordinary asks show the model.

The operator asks for a note. They say what they know, the expert drafts it in their `voice`, and they approve the exact
text. The expert changes no fact in a note without their approval, because the experience in it is theirs.

The operator asks to ingest a book, and asks afterwards for a note about it. The ingestion writes one reference file for
the source inside the turn, because the operator named it (Section 1.3). The note is a second ask and a different
pillar: what the operator now thinks, rather than a second account of what the book said.

The expert offers a line for `summary.md` while the operator is still there. The moment to propose breadth is the moment
they have just settled what a thing should say, so the expert drafts the entry, suggests the references and the related
topics it connects to, and the operator adds insight, removes errors and approves it or drops it. `topic.md` and the two
breadth summaries stay separate on purpose, because each one is then a small file the operator approves in one read. A
breadth file manages the *operator's* context window, as `index.md` manages the agents'.

The expert argues throughout, and it argues about the operator's own conclusions as readily as about a page it found.
Objecting while the operator can still act on it is the job, and a retrospective is too late. A write is the other place
it speaks up: a session that files a note or a reference runs a conflict-detection sub-agent over the tree, which
reports what it found back into the session, and the session settles it there (Section 1.7).

Harness hooks keep the structure consistent, the expert runs idea discovery under `discovery`, and it finalizes no
human-approved content on its own. Most sessions file nothing.

## 1.7 Conflict Handling

Detection runs on the write, inside the session that made it, and a conflict lives no longer than that session.

### General rule

The practical and procedural pillars outrank the theoretical one (The Three Pillars, above), and that ranking does two
jobs. It fixes precedence when an agent acts: a context pack carries human content first, and an agent follows the
practical and the procedural where its guidance differs. It also settles a contradiction between two claims, and that is
the job of this section. Human content is the operator's own notes, the human-approved breadth summaries, and the skill
files, and the skills belong to the first job alone: a skill says how to do something and asserts nothing about the
subject, so no note and no reference can contradict one.

A conflict is therefore knowledge against knowledge. An operator's note that conflicts with a reference is correct,
because the note is their own view of what the reference asserts, established under their own conditions, and the
operator edits a wrong note until it wins. The PKB overwrites no human content. It brings a conflict to the operator's
attention and settles nothing on its own.

Two cases get no answer from the ranking. Two notes that conflict have two human sides, so the expert shows the operator
both and they pick. A second reading of a source that contradicts the one in that source's file has no human side at
all, both sides are extractions, and the file at stake is a reference rather than a note. The handling is the same in
both: the pair goes to the operator and nothing changes until they answer.

### Detection runs on the write

A session that files a note or a reference runs a conflict-detection sub-agent over the tree. The sub-agent holds read
tools and no write tool of any kind, it runs the `conflict-detection` skill on the axes of Section 2.8, and it reports
what it found back into the session that wrote the file. A check the operator asks for over a topic is the only other
route, and nothing looks on its own: no tag records a conflict, no queue holds one, and no timer re-reads a pair a write
already compared.

The report names both files and quotes both sides. It separates the pairs that genuinely oppose each other from the
pairs that are both true under conditions neither file states, because the two ask the operator different questions, and
it proposes no conflict type and no confidence score, because nothing stores either.

A note the analysis files after `/close` is a write like any other, so the same check runs and the operator answers it
in the analysis session the learning channel holds (Section 2.8). A write with no session behind it gets no check at
all, a `pkb_*` call from a project agent being the one that reaches the tree, because a report needs somebody to
report to.

### What resolving one means

The operator resolves it in the session, in one of three ways. They edit one of the two into the version that holds,
whether it is on disk yet or still the text the session is filing. They say which of the two holds and why, and nothing
changes, because the other was already right. Or they say both hold, and the note gains the conditions that separate
them, which is a write the operator approves like any other.

Silence is not a resolution. A session that ends with a conflict it did not settle names both files in its running
record and says so, because nothing else remembers it and the next session starts on a tree that looks settled.

### A page with no standing is not a conflict

A session that searches compares what it found on the internet against the topic's notes. A page that contradicts a note
raises the pair and changes nothing: the expert shows the operator both quotes side by side and leaves the note byte for
byte as it was. A conflict starts once the operator accepts the source, which feeds the theoretical pillar through
ordinary ingestion, lands the page as a reference, and puts the write-time check on it like any other write. The same
holds for a lesson the operator dictated during the session: the analysis raises the pair, quotes both sides, and
changes nothing until the operator says which one stands (Section 2.8, step 4).

Two properties of models make this the only safe handling.

- A model handed retrieved text abandons its own correct answer for it, at a rate running from 0.16 to 0.31 measured
  across six domains and six models. A page with no standing therefore changes no note of the operator's, because
  letting it would hand that bias write access to a tree with no undo.
- A model given two passages that contradict each other misses the contradiction, scoring under 11% on pairs human
  annotators had already marked. The check is a reporter and never an editor for that reason: the operator reads both
  quotes and decides, and a pair the sub-agent walked past is expected rather than a defect.

### What the PKB does not do

- It builds no separate conflict registry, so nothing pollutes a context window after the resolution.
- It puts no marker on the two files that held the conflict, and no note or reference records that one ever happened.
  The session's own file says what that session raised, and nothing else remembers it (Section 2.7).
- It marks no note as a loser.
- It stores no resolution text outside the note, so the note content is the true state of knowledge.

## 1.8 Critical Rules

1. **Human content wins**: the practical and procedural pillars outrank the theoretical one. An operator's note and a
   human-approved breadth summary take precedence over a reference. A session that files a note or a reference runs a
   sub-agent that reads the tree for a conflict and reports back, and the operator settles it in that session, or in the
   analysis session when the write came from an analysis after `/close` (Section 1.7). The expert changes no human
   content on its own.

2. **Breadth vs. depth**: `summary.md` and `topic.md` serve a breadth-first reader. `index.md` serves a depth-first
   reader. A Topic Expert assembles a context pack on that split, for a consumer such as the Project Manager (separate
   project). The split is what bounds a pack: a breadth reader that receives depth files receives more than one context
   window holds (Part 4).

3. **Machine vs. human**: a machine builds every `index.md`. `summary.md` and `topic.md` need the operator, and the
   expert finalizes neither without their approval.

4. **Cross-topic solutions**: a solution note lives in one topic, the most relevant one, and nothing copies it. Tags,
   `related_topics` metadata, and Librarian routing carry the cross-topic discovery.

   This rule governs **solution notes** alone. It leaves the ingestion of sources open: several Topic Experts may ingest
   one book, paper, article, or clip, each extracting what its own topic cares about (Section 2.2). Those are different
   extractions of one source rather than copies of one file.

   The source material itself is copied on purpose. A topic that ingests a source **gainfully**, deriving at least one
   insight from it, gets its own copy of the original beside its own extraction, so the topic folder stays
   self-contained and portable. Storing a large file more than once is the price, and it is worth paying. A topic that
   derives nothing gets no folder, no stub, and no copy: zero trace, rather than an empty folder implying the source was
   considered and is somehow relevant.

5. **Sub-topics**: a nested topic follows the same structure recursively. Its parent topic's Topic Expert serves it
   unless it holds its own `expert.md`, the same resolution the template override uses.

6. **Media handling**: a note with media takes its own folder. The `[note-title].md` inside holds the note text, and a
   machine-extracted description of any embedded media. Agents read that text rather than parsing a binary file.

7. **Tag discipline**: use hierarchical tags. Harness hooks maintain the root `tags.md` registry. Propose a new tag to
   the operator. Create no ad-hoc tag.

8. **Nothing off the internet becomes a note**: an internet article feeds the theoretical pillar, while `notes/` holds
   what the operator proved in practice, and the `type.*` tag restates the path rather than adding to it (Section 1.5).
   A finding taken off the internet and filed under `notes/` is then indistinguishable from experience the operator
   earned. A session files an accepted article under `references/` and its own synthesis into its file under the root
   `sessions/` (Section 2.7). The rule extends to a turn arriving on Part 4's batch route, the one way in that is no
   session (Section 1.6): an expert that reached a tool outside the PKB on such a turn may write no note on it, and the
   caller hears that it should open a session instead. Nothing tracks what a turn reached.

   A session **may** file under `notes/` the thing the operator went and tried: they cooked it, they ran it, they came
   back and said what happened. They settle that lesson in the analysis after `/close`, the expert drafts it from the
   session file's record of the experiments, and the operator approves it word for word. It lands tagged
   `type.solution` when the thing worked and `type.note` when it did not (Section 2.7).

   The line this rule draws runs between read and done, and the tree records it by the folder a file sits in and by
   nothing else. No field on the page says how the knowledge arrived, so a finding misfiled under `notes/` reads as
   earned experience and nothing in the tree catches it afterwards. The guard is the write itself: the operator
   instructs every one and approves the exact text, so this rule is what they hold to when they do.

## 1.9 Topic Maintenance Model

> **Design principle**: *Enforce structure mechanically, curate meaning agentically.* No separate maintainer agent
> exists. Harness hooks that no agent can skip or forget perform the deterministic maintenance. The topic's Topic
> Expert (Part 2) performs the judgment work through common, overloadable skills.

Maintenance splits across three tiers.

### Tier 1: Mechanical enforcement (harness hooks)

The DeepAgent harness does this work itself, and no agent judges any of it or can skip it. It runs on two clocks.
Validation fires per write, in front of the write, so a file that would break the standards never lands. Everything
derived fires once per agent run, after the turn, over the files that turn touched. Regeneration per write is forbidden,
because it would rewrite the root `tags.md` several times in one turn.

Per write:

- Validate the YAML frontmatter (required fields, tag syntax and depth), the file naming, and the agreement between the
  declared metadata (`topic`, `source_type`, `topic.*`/`type.*` tags) and the file's location. Skill files are the third
  file class (Section 1.4): validation checks their placement and never their PKB frontmatter, and every index and tag
  generator below skips them.

Once per agent run, over the files the turn created, changed, renamed, or removed:

- Update the `updated` timestamps.
- Regenerate the topic's `index.md`, with its tag subtree and cross-topic mappings. Every knowledge file carries a
  `description` in its frontmatter (Section 1.4), so index generation is deterministic: walk the tree, read the
  frontmatter.
- Regenerate the root `tags.md` registry from the tags the files use, and aggregate the cross-topic mappings from the
  `related_topics` declarations. Plain deterministic code, derived, and no LLM tokens spent.
- Regenerate the root `index.md`, a catalog of every topic and its `topic.md` description, the Librarian's one-file
  routing view.
- Flag broken links and orphaned files.

Scaffolding the standard structure (Section 1.2) for a new topic or sub-topic is mechanical in the same way, and it runs
on the operator's approval rather than on either clock.

Tier 1 schedules no conflict work. The session that made a write runs the check itself, and the report lands in that
session (Section 1.7).

### Tier 2: Common judgment skills (overloadable)

Work that needs an understanding of content is defined once, as common skills every Topic Expert loads:

- **Summarization** – draft all three breadth files, `topic.md`, `references/summary.md`, and `notes/summary.md`,
  following the dialog rules in Section 1.6. The root catalog is built from `topic.md`'s `description`, so the Librarian
  routes on that field.
- **Conflict detection** – compare a note or a reference a session is filing against the tree, on the axes of Section
  2.8, and report what needs resolving into that session (Section 1.7).
- **Tag proposal** – propose a new hierarchical tag for the operator's approval before filing content that uses it. The
  registry picks it up once a file uses it.
- **Ingestion classification** – classify inbound content as a reference or a note (an observation, an opinion, or a
  solution tagged `type.solution`), and draft the files with their metadata, including the `description` Tier 1 relies
  on and the media descriptions rule 6 in Section 1.8 requires. Hand a source too large for one turn to the chunked
  ingestion loop (Section 2.3).
- **Source extraction, one skill per kind of source** – the skeleton an extraction follows, which is what makes it
  useful rather than a paraphrase: a **paper** (question · method · results · limitations · *does this apply to me*), a
  **book** (thesis, then one section per chapter and one bullet per argument), an **article, post, or clip** (the single
  claim and the evidence offered for it), a **manual or reference work** (the parts the topic will consult, because a
  reader looks things up in a manual rather than reading it).
- **Research planning and synthesis** – turn the operator's objective into the questions a search will ask, brief one
  search sub-agent per question, weigh what returns against the topic's notes and references, and draft the synthesis
  section of the session file. The questions carry the objective and none of the operator's beliefs (Section 2.7). The
  name of this skill is an identifier (Section 2.4).
- **Self-improvement** – after `/close`, read the session file from the beginning, decide whether the session
  established anything, and open a session in the learning channel with what it found (Section 2.8). It calls research
  planning and synthesis for the session's own synthesis and the two drafting skills below for each lesson it proposes.
  Most sessions establish nothing, and the skill opens that session anyway and says so, because the distillation section
  is written there and the operator says `/end` there.
- **Lesson proposal** – draft what the operator learned and what is worth filing, from the session file's record of
  what they tried (Section 2.7). The drafting is the skill's work. Harness code picks the pairs that lesson has to
  answer for, per Section 2.8, whether the expert wrote the lesson or the operator dictated it: a skill is a file the
  operator may adopt and then edit, and a guarantee that lives in an adopted copy leaves the day they edit it.
- **Skill proposal** – draft the `SKILL.md` for a way of working the session established, and say whether it belongs to
  one topic or to all of them (Section 2.8). It is a second skill rather than a second mode of lesson proposal, because
  the two answer to different tests: a lesson says what is true and a skill says how to work.
- **Sub-topic proposals** – propose a split for a topic that has grown too large.

A Topic Expert may **overload** any of these with a topic version, so the Cooking expert's summarization skill may
require temperature and doneness tables in a recipe summary. An overload extends the common procedure and weakens no
general standard, because Tier 1 validates the output whichever skill version produced it. `conflict-detection` is the
exception, and it is the one worth watching: its whole output is a report into a session, so Tier 1 has no file to
validate and an overload can weaken the check Section 1.7 rests on. The same mechanism extends to the three
procedural-pillar skills of Section 2.4, `research`, `discovery` and `voice`.

The three skills the analysis calls, self-improvement, lesson proposal and skill proposal, sit under that promise too,
and they reach it by a different route. The analysis reads the session file rather than the conversation (Section 2.8),
so it cannot run as an ordinary expert turn, which is handed the conversation. The DeepAgent harness resolves the
drafting skill by name instead, the topic's own copy ahead of the root's and the shipped one underneath, the order an
expert's graph resolves in, and hands the body to the distillation. A Cooking session then distils differently from a
Trading one, and a pass that needed the conversation would have nothing to read once the session closed. Research
planning and synthesis overloads through the expert's graph as the others do, because the expert runs it inside the live
session (Section 2.7), and by name for the synthesis it drafts after `/close`.

### Tier 3: Topic Expert dialog

The Topic Expert runs the judgment skills in dialog with the operator, proposing drafts, presenting conflicts and
collecting approvals (Sections 1.6 and 2.3). It writes the `description` frontmatter when it files new content, which
keeps Tier 1 deterministic. Nobody curates the cross-topic mappings: Tier 1 aggregates them from the `related_topics`
declarations into the root `tags.md`, and the Librarian reads them when it routes across topics.

### Topic creation

The operator requests a new topic, or approves one the Librarian proposed (Section 2.2). Then:

1. Tier 1 scaffolds the standard structure from Section 1.2, with placeholder `summary.md` files.
2. The DeepAgent harness instantiates a Topic Expert for the topic.
3. The expert drafts `topic.md`, proposes the topic's first tag subtree, and asks the operator to approve.
4. The operator writes any skill overload with the expert's help.

---

# Part 2: PKB Agent Architecture

## 2.1 Agent-Mediated Access

Every interaction with the PKB goes through an agent. External agents, project agents among them, read and write no
topic file directly. The agent layer and the DeepAgent harness's hooks (Section 1.9) enforce the standards Part 1
defines, whichever channel a request arrives on.

Part 1 says the operator writes and edits a file, and the PKB's own route for both is this dialog: the operator decides,
and the agent applies the change on their behalf. An edit they make by hand goes through nothing, and Section 2.8 counts
what that costs.

## 2.2 The Librarian (Root PKB Agent)

The **Librarian** is the root agent of the PKB and a researcher, and it researches breadth-first across all topics. Its
breadth is the set of experts it reaches, and the depth is theirs. It holds no topic knowledge of its own, it holds no
topic's search tools, and it writes nothing into the tree.

### Cross-topic research is a competence of its own

Three parts of the research belong to the Librarian:

- **Frame the objective** so that it decomposes into questions single topics can answer.
- **Name the topics that bear on it**, from the root catalog, including the second topic the operator did not think of.
- **Recognize when two topics' answers interact**, and say so, rather than leaving the operator to notice.

A Topic Expert holds the other competence, and it goes deep in one subject, with that subject's sources, its notes and
its own way of working. Depth in one topic and reach across many are different skills, so the PKB keeps them in
different agents. The fan-out below is how the Librarian reaches depth, one step of the research rather than all of it.

The merge is not where the third part happens. An interaction the Librarian notices lands in the question it frames next
rather than in the merged reply step 3 composes, and that is session work (Section 2.7).

The Librarian's cross-topic research skill lives at the PKB root, because it is about no subject and no topic can hold
it. The root is where every process skill already lives (Section 2.4), and a cross-topic research skill is the first one
that belongs to a named agent rather than to all of them. The shipped `research` skill is a different thing: it covers
one part of the work, the breadth-first pass over the tree, and it says nothing about framing an objective or about two
topics' answers interacting.

### Routing is a workflow rather than a decision

A Librarian turn is four steps. The first is a judgment call and the other three are harness code that always runs.

1. **Classify.** The Librarian reads the generated root catalog and decides which topics the inbound item concerns. It
   answers with a routing call naming the applicable topics and a one-line reason, never prose. This is the one step
   where a model holds discretion.
2. **Fan out.** Harness code invokes every applicable Topic Expert. The Librarian cannot decide to skip it. It is a step
   that runs.
3. **Merge by attribution.** Harness code composes one reply from what the experts returned: each expert's own answer,
   under its own heading, named by its title and its agent id. This is deterministic code rather than a second model
   writing a summary of the first. A model asked to write the merge reports that *"the Cooking expert checked the
   knowledge base"* when no expert ever ran. A reply assembled from real results cannot say that.
4. **Offer the experts directly.** The reply names the agents that answered, so the operator can carry on with one of
   them, "continue with the Cooking expert", which opens a session on that expert with its own file and attaches the
   operator's channel to it, rather than going back through the Librarian each time.

A Librarian free to decide whether to delegate sometimes read the topic folders itself and answered from raw files, and
it lost the topic's skills, its `expert.md` persona and its voice. Everything that makes a Topic Expert an expert lives
one layer down, so harness code closes that.

### Uncertain classification asks with a menu

Classification that comes back uncertain goes to the operator instead. Harness code asks which experts to engage and
lists the candidates, because filing knowledge in the wrong place cannot be undone. The menu appears when the Librarian
answers in prose instead of routing, after one stricter retry, when it names no topic for an item that plainly concerns
existing knowledge, or when it says it is unsure. "None of these" is always an option, and it leads to the topic-gap
flow below.

### One source, several experts

Information fans out the same way a question does, and several experts ingesting one source is no duplication. A
management book can carry lessons on management *and* on parenting. Routed to both, it yields a reference under
Management about leading teams and a reference under Parenting about raising children: two extractions of one source,
each written through its topic's lens, which a Librarian answering from raw files could never produce.

Each expert decides for itself whether the material holds anything for it, and material that lands nowhere is a correct
outcome: a fan-out where two of four experts file and two decline is a success.

Responsibilities:

- **Routing** – classify each inbound request or piece of information, fan it out to every applicable Topic Expert, and
  merge what they return into one attributed answer.
- **Topic catalog** – classify from the root `index.md`, a hook-generated catalog of every topic and its description,
  aggregated from `topic.md` frontmatter. The catalog marks a topic that owns an `expert.md` with *(custom expert)*, so
  the Librarian sees it in the one file it already reads and walks the tree for nothing.
- **Topic gaps** – propose a new topic to the operator when inbound information fits no existing one, following the
  topic creation flow in Section 1.9. Nothing applicable *and* nothing worth choosing between is the gap flow, never a
  menu.
- **Cross-topic coordination** – read the cross-topic mappings in the root `tags.md`, aggregated from the
  `related_topics` declarations, to notice the second topic worth involving.
- **Work that crosses topics** – the operator opens a session on the Librarian when the objective crosses topics
  (Section 2.7). Personal finance and investment cross portfolio management and trading, and neither expert holds the
  whole of it. An objective fans out like any other inbound item, and the analysis after `/close` fans out the same way
  with the session itself as the source, each expert filing inside its own topic or filing nothing. The synthesis lands
  in the session's own file in the root `sessions/` folder, written by a root tool so the Librarian still writes
  nothing.

## 2.3 Topic Experts

A **Topic Expert** runs each topic. One default **Topic Expert template** serves the whole PKB. A topic that needs
behavior beyond skill overloads overrides the template with an `expert.md` in its topic root. The DeepAgent harness
resolves this on the pattern the maintenance skills use: take `[Topic Root]/expert.md` when it exists, and otherwise
instantiate the PKB template with the topic's context, `topic.md`, the common skills, and any skill overload. The
resolution recurses, so a parent topic's expert serves a sub-topic that holds no `expert.md`.

The expert combines two layers of capability:

1. **PKB general standards (common layer)** – the structure, metadata, tag, summary, and conflict rules Part 1 defines.
   Harness hooks enforce their deterministic parts. Their judgment parts run as common, overloadable skills (Sections
   1.9 and 2.4).
2. **Topic knowledge (expert layer)** – domain knowledge about the topic itself, and the best ways to work its content:
   how to query it, which files answer which kinds of question, and its own ingestion rules.

Responsibilities:

- Answer a question about the topic from the breadth files (`topic.md`, `summary.md`) or the depth files (`index.md`,
  the source maps), as the request requires.
- Ingest what the Librarian routes: classify it as a reference or a note, tagging a solution `type.solution`, draft the
  files, and apply the metadata and tags the standards set. Ingest it through the lens of this topic, because one source
  reaching two experts should produce two different extractions, and decline material that holds nothing this topic
  cares about.
- Work a session with the operator for as long as the work lasts (Section 2.7): search for what the topic cannot answer,
  brief read-only search sub-agents, weigh what they bring back against the topic's notes, object while the operator can
  still act on it, take their results back as the experiments come in, and propose after `/close` what they learned.
- Run the conflict-detection sub-agent over the tree when the session files a note or a reference, and settle what it
  reports with the operator in that same session (Section 1.7).
- Carry out the judgment side of topic maintenance (Section 1.9). Harness hooks enforce the mechanical side.
- Bring every artifact to the operator as Part 1 requires, the two exceptions of Section 1.6 aside: they read the exact
  text before it lands, and a tag the PKB has never used is proposed before any file uses it (Sections 1.5 and 1.6).

An expert writes inside its own topic, and its session file sits outside it, in the root `sessions/` folder (Section
1.2), so the expert drafts the text, the operator approves the synthesis, and a root tool performs every write into the
file. The root `skills/` folder takes a write on that same route (Section 2.4).

A source too large for one turn is ingested as a loop. Classify, draft, file works for a link and fails for a book,
because nobody reads what does not fit the context window and one turn writes a confident account of the part it saw. So
harness code drives the reading: it segments the source, extracts argument by argument through a bounded window, writes
each section as it goes, records what it skipped and why, and survives a run that dies part way through a 300-page book.
The expert stays the author of the extraction and stops deciding when it is finished. A source arrives as a path, and
anything binary is extracted to text first, with the PKB keeping both.

### Example: a Cooking Topic Expert in action

The operator connects to the Cooking Topic Expert. They need no external tool, because the expert handles retrieval,
dialog and filing end to end.

- **Capture experience**: the operator reports back after cooking, "the grill behaves differently in windy weather". The
  expert drafts a note, the conflict-detection sub-agent reports what the tree already says, the operator approves the
  exact text, and the expert proposes a regenerated `notes/summary.md` with it.
- **Combine reference and experience**: the operator asks for a grilling recipe from an ingested cookbook. The expert
  pulls it from `references/` and applies the temperatures the operator filed for their own gas grill.
- **Ingest through its own lens**: the Librarian fans a food-science book out to Cooking and to Health. Cooking files
  what it says about heat, protein and technique. Health files what it says about nutrition.
- **Search for what the topic cannot answer**: the operator asks how long to dry-brine a brisket, and the topic holds no
  reference on it. The expert says so and writes three questions, harness code runs a search sub-agent on each and
  verifies every quotation against the text it holds, and the expert flags the two results that contradict the
  operator's own note and offers one article for ingestion. The session stays open, because the operator has cooked
  nothing yet.
- **Work one objective over weeks**: the operator opens a session called `Cooking · Brisket Rub` and cooks three times,
  reporting back after each. The expert writes the experiments into the session file, contradicts the operator when
  their week-three conclusion disagrees with their own week-one report, and proposes one note in the analysis session
  once this one closes. Their other session, `Cooking · Sourdough Starter`, stays untouched.
- **Leave nothing behind**: the operator asks for a weeknight pasta, gets one, and closes the session. The analysis
  reads the file, finds a request and an answer and no experience, and files nothing: no note, no synthesis, no folder,
  and the session file says so. This is the ordinary outcome, and the closed session file still holds the recipe.

## 2.4 Common Skills and Skill Overloading

This section is the procedural pillar.

Every Topic Expert loads the common skills. They ship with the implementation and mount ahead of the PKB root's own
`skills/` folder, which starts empty (Part 3). The mount is read-only because it lives inside the installed package: a
write there edits the implementation for every PKB on the machine, so the permission layer denies it to every agent. The
tree's own `skills/` folders take writes, and a skill the operator adopts, writes or approves after a session lands in
one of them (Section 2.8).

The two homes differ in reach, and they differ in what can reach them. A topic's `skills/` folder sits inside that
expert's own subtree, so the expert writes there on the operator's word. The root's folder sits outside every expert's
subtree, where the catch-all deny refuses it and no tool routes a write there, so filling the root folder needs a root
tool. The root `sessions/` folder needs the same tool, because every session file lives there and no expert reaches it
either (Section 2.7).

Each skill is a folder holding a `SKILL.md`, so `skills/voice/SKILL.md` and `skills/discovery/SKILL.md`. That is the
DeepAgent harness's own format, and it buys two things without code of ours: progressive disclosure, where the prompt
holds the skill's name and description and the harness opens the body when a turn needs it, and override resolution by
name collision. Anything else the skill needs sits beside its `SKILL.md`.

### The ten skills that ship

A shipped skill is a starter draft: it makes something sensible happen on day one, and it says so at the bottom of its
own text. They sort by the pillar each one serves, seven pointed at the operator's subject and three at the procedural
pillar.

A skill name is an identifier rather than a description. `research` and *research planning and synthesis* are names, and
neither carries the word's ordinary sense here. This document uses *research* for the Librarian's breadth-first work
across topics, and *search* for reaching the internet.

**Taking in what arrives from outside the topic.**

- **`ingestion-classification`** decides whether an inbound thing is a reference, a note, or a solution, and drafts the
  file with the metadata that decision implies. It routes to the theoretical pillar or to the practical one, and one
  decision settles the folder, the `source_type` and the `type.*` tag together, so a wrong classification makes every
  part of the file wrong at once.
- **`ingest-paper`** extracts a paper, study, whitepaper or technical report into question, method, results,
  limitations, and the section no paper contains: *does this apply to me*, naming the mismatch that drives the answer.
- **`ingest-book`** extracts a book or long report through the source's own chapters, one bullet per argument this topic
  cares about, and keeps *read and took nothing* apart from *never opened* in a reading list at the end.

**Tending the two subject pillars: what the topic already holds.**

- **`summarization`** drafts and revises the three breadth files, `topic.md` and the two breadth summaries. It treats
  length growth as a defect: every revision distils and replaces, and none appends.
- **`conflict-detection`** runs in a sub-agent when a session files a note or a reference, compares the write against
  the tree, and reports the pairs that need resolving into that session (Section 1.7). It resolves nothing and it writes
  nothing, and the report stays in the conversation, because the PKB keeps no conflict register.
- **`tag-proposal`** proposes a tag the PKB has never used by drafting the file that needs it, so the operator sees the
  tag and the file it would create in one decision.
- **`sub-topic-proposal`** proposes a split once `notes/summary.md` has stopped distilling and started enumerating. It
  brings the evidence and the full file assignment, and it creates nothing.

**Serving the procedural pillar: how the operator and the agent work together.**

- **`research`** explores breadth-first across the PKB and returns three to five options, each with its trade-off and
  the files behind it. Finding two files that disagree on the question, it says so and escalates rather than picking the
  reading that suits the answer. It is the breadth-first pass over the tree and no more, so it is not the Librarian's
  cross-topic research skill (Section 2.2).
- **`discovery`** runs a brainstorming session against PKB content. It names the tension between two notes and the gap a
  breadth summary keeps implying, pushes back, and files nothing. Anything worth keeping goes back through the front
  door as ordinary ingestion.
- **`voice`** carries the voice profile every draft is written in, narrowed per topic when one subject wants a different
  register from another. The operator corrects it from their own edits, and they approve a changed profile like any
  other text. It is the one shipped skill that knows something about the operator rather than about cooking, and Section
  2.8 opens the rest of the pillar to a session's own proposals.

The design names seven more skills. Two are source extractions, for an article, post or clip and for a manual or
reference work, and `ingestion-classification` files any source no extraction skill of its own covers. Four sit on the
session side, *research planning and synthesis*, *self-improvement*, *lesson proposal* and *skill proposal*, and all
four mount and overload like the ten above. The expert runs research planning and synthesis inside the live session, so
it resolves through the expert's graph there and by name for the synthesis it drafts after `/close`, and the three the
analysis calls resolve by name alone (Section 1.9). Part 2 owes a seventh that Part 1 never names, the Librarian's
*cross-topic research* skill (Section 2.2), and it mounts at the root like the rest.

Skills sit on the same side of the collaboration rule as notes, **human-generated, AI-curated** (Section 1.3): the
operator writes or approves every one of them, whoever typed the draft. A `SKILL.md` is no knowledge file (Section 1.4),
and its `name` and its folder name are meant to agree, because the `name` alone decides what it shadows and a mismatch
only warns (*Where a skill lives*, below).

### Where a skill lives

The procedural pillar has two homes and the tree resolves both by name. This is the one place the rule is stated, and
every other section cites it.

- **A skill about one subject** lives in that topic's `skills/` folder, visible to that topic's expert and to its
  sub-topics' experts, and to nobody else.
- **A skill about how to work** is a process skill and lives in the PKB root's `skills/` folder, where every expert
  loads it, and the Librarian with them. The Librarian's cross-topic research skill lives here for that reason: it is
  about no subject, so no topic can hold it (Section 2.2).

Changing a shipped skill uses the same two homes. **Adopting** it copies it to the root, where every expert loads the
copy from then on. **Overloading** it copies it into one topic, where that topic's expert loads the copy and the other
experts keep the shipped default. Both shadow by name, and the permanent-fork warning attaches to the name.

Resolution reads the shipped mount first, then the root folder, then the topic's, and the most specific entry wins
whole-record: a topic's `voice/SKILL.md` *replaces* the root one for that topic rather than merging with it, the pattern
the DeepAgent harness applies to `expert.md`. An overload extends the default with domain intelligence, a recipe-writing
voice for Cooking or a tasting-session discovery skill, and it redefines no general standard, because Tier 1 validates
the output whichever skill version produced it. `conflict-detection` is the exception, because its whole output is a
report into a session and Tier 1 has no file to validate (Sections 1.9 and 2.8).

The name decides whether a file forks anything, and the name that decides is the `name` in the file's own frontmatter.
The DeepAgent harness reads the three skill locations in order and keeps the last skill declaring a given name, so
`skills/my-research/SKILL.md` declaring `name: research` shadows the shipped `research` from the moment it lands (Part
3), while `skills/research/SKILL.md` declaring `name: my-research` shadows nothing. Both spellings look right in a
directory listing and the harness only logs a warning, so the agent layer reports the mismatch itself. It warns rather
than refusing. An analysis proposal that would shadow a shipped skill says so in the text the operator approves (Section
2.8).

Re-read a skill as the work moves on, rather than writing it once. A procedure hardens around the conditions somebody
wrote it in, and those conditions move: the tool that failed gets fixed, the operator changes how they want to be argued
with, the topic grows past the shape the skill assumed. A skill goes stale by failing when somebody uses it, so the
evidence sits in a session file rather than in a note or a reference, and session learning holds the one route: a
session proposes a revision to a skill a session wrote (Section 2.8). The conflict check reads no skill, because a skill
states no claim and contradicts nothing (Section 1.7).

## 2.5 DeepAgent Harness and Access Channels

The agent layer runs on the **DeepAgent** harness. It hosts the Librarian and the Topic Experts and exposes them through
several channels:

- A dedicated TUI
- Telegram channels
- Other channels as needed: chat apps, APIs, and the rest

The operator states an objective to the Librarian or to one Topic Expert. Neither is a default, and Section 2.7 says how
they choose. Step 4 of the Librarian's workflow joins the two: every expert the Librarian consults is addressable in its
own right, so a reply saying *"the Cooking expert says…"* is also an offer to carry on with that expert.

A session is the unit of work and a channel is a way in: the operator opens a channel and attaches it to a session, and
several channels may hold one session at once (Section 2.7). `/close` brings every attached channel away from the
session, and the analysis of that session runs later in the **learning channel** (Section 2.8), a standing surface bound
to no topic, no objective and no agent. The analysis session attaches there, so that housekeeping never interrupts a
topic conversation.

The command set settles at six commands: `/channels`, `/threads`, `/agents`, `/cancel`, `/close` and `/end`. `/threads`
lists the open sessions, and the session design adds the two the operator says (Section 2.7): `/close` in the session
itself, and `/end` in the analysis session the learning channel holds. The set holds no `/new`
(`docs/how-to/telegram.md`), because attaching already changes what a channel holds, and a rotation inside a channel
splits one line of work in half and leaves both halves named for the same objective. A new objective opens a new
session, and the channel attaches to it.

## 2.6 Agent Hierarchy

```
┌──────────────────────────────────────────────────────────────────────┐
│                    OPERATOR / EXTERNAL AGENTS                        │
│                     (Project Manager agents)                         │
└──────────────────────────────────────────────────────────────────────┘
                     │                                    │
                     ▼ crosses topics                     ▼ one topic owns it
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
        + harness hooks (mechanical)
        + read-only sub-agents: search, conflict detection (2.7)
             │              │
             ▼              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                            PKB TOPICS                                │
│         references/ · notes/ · skills/  (the three pillars)          │
│             + topic.md, index.md, root index.md, tags.md             │
│                  + the root's skills/ and sessions/                  │
└──────────────────────────────────────────────────────────────────────┘
```

## 2.7 Sessions

A session is how anyone works with the PKB in dialog, and it is how the operator reaches a topic's knowledge at all (The
Three Pillars, above). It is a durable thing of its own, held on one agent for one objective, for as long as that work
lasts. A session may cover the objective the topic cannot meet, the experiments that follow, and the lesson the operator
and the expert settle at the end. A capture is one turn inside it and files what the operator already knew: they dictate
a note or name a source, and the write lands in that turn rather than waiting for `/close`. Sessions are where the
operator finds things out, and where most notes come from.

A search is one of the things a session does, rather than a kind of session. A session may discuss, argue about a
design, ask a question and take the answer, search the internet, or try things for weeks and report back, in any order.
The PKB holds one shape for all of them, and a session that searches nothing is an ordinary session.

Section 2.8 marks what the design has left unsolved.

### A session carries its own name, and a channel is a way in

Work begins as a question and becomes whatever it becomes, an afternoon or four months of it: reading, then experiments,
then a verdict, or none of those. The PKB asks the operator to declare nothing in advance about what a conversation will
turn into, because they do not know yet and a wrong declaration is one more thing to maintain.

A session holds its name itself and exists whether or not anything is looking at it, because it is a file in the root
`sessions/` folder while a channel is a surface that can go away. A session may run four months, and a name that lived
in a channel title would die with the channel and leave the file with nothing able to reach it again.

The operator gets in by opening a channel and attaching it to a session that already exists, or by starting a new
session there on the Librarian or on one Topic Expert. Attaching is the ordinary way in rather than a recovery path, so
a session touched twice a month needs no permanent place in the chat. Several channels may hold one session at once, so
the operator types into the TUI and answers from the phone. A session is one conversation whatever number of channels
are looking at it: its turns run one after another, every attached channel sees the same thread, and the reply to a turn
typed on the phone appears on the TUI too.

One expert holds several sessions at once, each named for its objective, so `Trading · Trend Signal` and
`Trading · Market Regime` run side by side, for two reasons, the second the stronger:

- On the operator's side, separate lines of work are separate places on the phone, and a phone is where this work
  happens. A long-running session gets a channel of its own by default, and the operator can break that attachment and
  remake it.
- On the model's side, each session keeps its context on its own objective. A conversation replays its whole history on
  every turn until harness code compacts it. Two objectives in one conversation means every trend-signal turn re-reads
  the regime work and takes an invitation to blend the two.

A channel holds one session at a time and its title names that session, so a deliberate split is safe where an
accidental one is not.

### The counterpart may be an agent

A project agent opens a session the way a person does, and for the same reasons: it needs knowledge the context packs of
Part 4 do not carry, it wants a question searched, or it implements something the topic holds the conditions for.
Everything in this section holds unchanged, and the dialog is the whole of the difference from Part 4's batch route.

One thing does differ, and it is now settled. Every approval in this document is the operator reading exact bytes, and
an agent counterpart puts none in front of them. Nothing but the session's own running record is written until the
operator instructed it inside a session, and the record claims nothing about the topic, so an unsupervised session takes
the route every session takes: `/close`, the learning queue, and the analysis session the learning channel holds
(Section 2.8). It needs no route of its own. One question stays open: whether anyone but the operator may ever instruct
a write, and over which files.

### A session opens on the Librarian or on a Topic Expert

A session pursues an objective the operator set, from grilling dinner to a new trading strategy. It opens on the
Librarian or on one Topic Expert, neither is a default, and the operator chooses at the start.

Open it on a Topic Expert when one topic owns the objective. Grilling dinner is Cooking: one expert, one subtree, and
nothing else needs to happen.

Open it on the Librarian when the objective crosses topics. A new trading strategy crosses portfolio management and
trading, and neither expert holds the whole of it. The Librarian frames the objective, fans every turn out to the
applicable experts, and merges what they return by attribution (Section 2.2). Framing the objective and naming the
topics that bear on it is a competence of its own, and it is the Librarian's.

The Librarian still writes nothing, and each expert still writes inside its own topic, reaching the root `sessions/`
folder for its own session file through the root tool a Librarian session uses for the same file (Section 2.3). A
session reaches outside the topic's three pillars through search sub-agents, and a page a search returns ranks below
everything the topic already holds until the operator accepts it (Section 1.7).

A session that opened on one expert and turns out to cross topics re-opens on the Librarian. Nothing copies one
session's file into another, so the operator names the objective again and the new session opens its own file.

### The expert argues with the operator, and about the operator's own conclusions

The operator works with the expert the way they would work with a human expert. A good expert objects during the work
rather than in a retrospective. The operator says the rub needs more sugar, and if their own note from March says sugar
burned at that temperature, the expert says so while there is time to change the rub. The same holds when the analysis
runs, where **the operator can be wrong**. Told during the session to file *sugar burns above 250*, an expert that reads
experiment two at 260 without burning says so beside the candidate it drafts. It then files what the operator decides,
because meaning is theirs (Sections 1.6 and 1.7).

### Two commands

`/close`, when the work is done, said in the session itself. A search reports back into the conversation and files
nothing, experiments file nothing, and what the session worked out waits for `/close`: a note, a skill and the
synthesis. It does three things:

1. It marks the session file closed and keeps it, because the next session on the same objective should read what this
   one turned down and why rather than meeting the page fresh (*One file per session, for its whole life*, below).
2. It brings every attached channel away from the session, and the session takes no more turns.
3. It puts the session in the learning queue, every time and whatever the session produced. `/close` says the operator
   has nothing more they want to craft in this context and it judges nothing, because the filing bar runs inside the
   analysis. That analysis is never synchronous with the command: the worker reads the file from the beginning when it
   reaches the entry, and it opens a session in the learning channel whatever it found, saying it has nothing to propose
   where that is the outcome, because the distillation section is written there and the operator says `/end` there
   (Section 2.5). The learning channel is an ordinary channel the analysis session attaches to, and it keeps
   housekeeping from interrupting a topic conversation. Section 2.8 runs that cycle and bounds what it may conclude.

`/end`, when the session is finished, analysis included. The operator says it in the analysis session the learning
channel holds, and it seals the closed session's own file once the analysis has appended what it distilled. It exists
because that analysis session needs a way to conclude and `/close` cannot be it: `/close` queues a session for analysis,
so an analysis session closing that way would queue itself forever.

Filing nothing is the ordinary outcome, and the closed session file keeps that work readable while it asserts nothing
about the topic. Rule 4 in Section 1.8 rules the same way for ingestion.

Waiting for the close costs nothing. The file holds every experiment as it happened, so week two is still there in week
twelve and the analysis reads it whole. An operator who has learned the thing they came for has met their objective, and
a met objective is a session to close.

Nothing brings the operator back to a session they left open, because returning is theirs to do and a channel left
attached on the phone is their reminder.

### The loop is: try it, report back, distil

Search for a rub and a smoking approach, cook, come back with how it tasted, cook again, and distil after the third
attempt. Search for a trend-following signal, run several variants, watch which one holds and under what setup, and
distil once the data arrives. Feedback lands in pieces, from wherever the operator is, over weeks. The loop is where
`notes/` gets made, and where the procedural pillar picks up what the two of them worked out (Section 2.8).

### A session searches in seven steps

A session searches as often as the work needs. Each search runs as harness-encoded steps for the reason routing does
(Section 2.2): the expert's judgment sets the questions and weighs the answers, and code runs the search and the
checking.

1. **Take the objective.** The operator says what they want to know, and the expert takes it from the objective the
   session file already carries (below). A session holds one objective, so a later search joins the file this session
   already opened, and a new objective opens a new session.
2. **Survey the topic.** This is exploration mode. The expert reads `topic.md`, both breadth summaries, and the
   notes and references that touch the objective. An objective the topic already meets ends the search here, with the
   answer and the files it came from, because searching the internet for something already filed spends the budget and
   invites a page that contradicts the operator's note.
3. **Write the questions.** The expert turns the objective into one question per line of enquiry. Each question carries
   the objective alone (*The notes weigh the results*, below), the shape its answer must take, the sources worth trying,
   and its boundary against the other questions. Vague briefs are the documented cause of two sub-agents searching the
   same thing while a third searches something nobody asked for.
4. **Search.** Harness code starts one search sub-agent per question and runs them in parallel. This step is code,
   because a model free to decide whether to delegate does not delegate, and Section 2.2 records what that cost the
   Librarian.
5. **Verify.** Harness code locates every quotation in the text the search returned for the page it came from, and it
   fetches nothing itself (*Code verifies every quotation*, below, carries the measurements). It holds back a URL it has
   no text for, and a quotation that text does not contain, and both land under their own heading in the session file
   with the reason they failed.
6. **Weigh.** The expert compares what survived verification against the topic's notes and references, claim by claim,
   and says which disagreements genuinely oppose each other and which are both true under conditions neither side
   states.
7. **Report back.** The expert brings what survived, with the evidence behind it, and the conversation carries on. The
   operator may name a source worth ingesting on the spot. Everything else waits for `/close`, because the search found
   things and the operator has tried none of them yet.

```
OPERATOR or PROJECT AGENT ── opens or attaches a channel to ──▶ SESSION
                        │              one objective, named for it, opened on
                        │              the Librarian when it crosses topics,
                        ▼              or on one expert when one topic owns it
  ┌──── a search: steps 1-7 above ───────────────────────────────┐
  │  the expert judges at 1, 2, 3, 6 and 7                       │
  │  harness code runs 4 (one read-only sub-agent per question)  │
  │  and 5 (every quotation found in the held bytes)             │
  └───────────────────────────┬──────────────────────────────────┘
                              │
      the operator goes and tries it, then comes back with what
      happened. Searches and experiments repeat for as long as
      the work lasts: an afternoon, or four months.
                              │
                              ▼
   /close  marks the session file closed, brings every attached channel away, and
           puts the session in the learning queue. The analysis reads the file from
           the beginning and opens a session in the learning channel quoting it, the
           operator approves the bytes there, the analysis appends what it distilled
           and how to the same file, and /end seals it
                              │
                              ▼
   notes/ · the file's own synthesis · skills/ · references/ · nothing at all, the common outcome
   (What a session may file, below, says who approves each)
```

### A session writes instruction sets and executes nothing

Work turns up things to do: run this backtest over these three regimes, cook this at 250 for four hours. An objective
that needs a new tool or a process to follow makes a session write one or more **instruction sets**. An instruction set
states why the work is necessary and what it must achieve. It does not specify an implementation. One session may write
several, for separate experiments or for separate tools.

The operator follows the instruction set, at the smoker or at the keyboard, or another agentic system implements it: the
Project Manager (Part 4) or a coding tool. The operator reports the results of each experiment or tool back into the
session as conversation, and the session uses them to advance the objective. An instruction set stays a message until
the operator says it is worth keeping, and it then lands inside the session's own synthesis. The PKB grows no task
queue, no runner and no status field, because a knowledge base that executes has to remember what it is halfway
through, and that is a second record of the work that can disagree with the first.

### One file per session, for its whole life

A session keeps one file, `sessions/[objective-title].md` in the PKB root, whichever agent runs it. The **session file**
carries the whole arc as sections of one document, it is durable because a file is durable, and nothing deletes it.

Its life runs in order:

1. **The session opens**, and harness code creates the file. Its name is the objective the operator gave the session,
   held by the session itself rather than by any channel, and the file opens with that objective and the experts taking
   part. Harness code mints the name a second session on that same objective gets, and it may be no name a sealed file
   already holds, because a sealed file is never reopened and nothing overwrites one.
2. **The work happens**, and the session writes into the file as it goes: each experiment and what it produced, the
   sources kept, the ones turned down and why, the claims verification held back, and the conflicts the work raised
   against the topic's notes.
3. **`/close`**, said when the operator has nothing more they want to craft in this context, marks the file closed and
   puts the session in the learning queue.
4. **The analysis reads the file from the beginning**, drafts the synthesis of what the session worked out, and opens a
   session in the learning channel, where the operator approves that text word for word and settles the rest of what the
   analysis found (Section 2.8).
5. **The analysis appends what it distilled, and how**, to this same document rather than to a file somewhere else.
6. **The operator says `/end`**, which marks the file finished, analysis included.

Each mark is an appended entry naming the command and the date, because the body is append-only and the frontmatter
carries no state field (Sections 1.4 and 1.5).

The whole arc sits in one file because a note that came out of a session is a claim and the file is the evidence for
that claim. The distillation section joins the two by recording how the lesson was reached as well as what it says, so a
reader who doubts the note six months later reads what was tried, what was turned down, and the reasoning that turned it
into a note, in one place. The large-source ingestion loop settled the shape already:
*"There is deliberately no second store of progress: a second source of truth about what was read is a second thing that
can be wrong, and the one a human can check is the file."*

The body is append-only. A turn adds to the end and nothing rewrites an earlier entry, because a model asked to revise a
long report across turns removes correct material without saying so and introduces errors while it polishes. A
correction is a new entry naming what it corrects, and the operator reads the file by asking the expert for it.

The mechanical tier sees an ordinary knowledge file (Section 1.4), and two of its fields plus the root folder keep it
distinguishable from a note the operator earned: `source_type: summary`, the tag `type.summary`, and one `topic.*` tag
per expert that took part, so the file names who answered and the analysis that generalizes over sessions finds its
material. A running record in the body changes none of that, because the schema constrains the frontmatter and leaves
the body to the session. The file follows the folder-hosted convention of Section 1.2 when it needs media beside it, and
the tag namespaces of Section 1.5 do not grow for sessions. The record itself needs no approval, because it says what
happened rather than claiming anything is true, and the operator approves the synthesis word for word in the analysis
session.

The sections run in the order the life does: the objective and the experts, the running record, the synthesis, and the
distillation. The synthesis holds the questions the session asked, every source it kept, every source it rejected and
why, the claims verification held back, the conflicts it raised against the topic's notes, the instruction sets the
operator kept, and what the session worked out. A session that searched nothing keeps the objective and the synthesis
and fills the source sections with nothing, because a discussion that reasoned from what the operator already holds and
reached a conclusion reached one. A session that produced nothing still has a file: it leaves no synthesis rather than
no file, and the distillation says so.

One shape is refused, and it is narrow. A session that searched, admitted nothing past verification, and then wrote a
confident synthesis anyway summarized a page it never read, so that filing is refused with the empty findings list
quoted back. A session that never searched is a different thing and files as usual. A session that read one page, cooked
from it, and learned one thing writes a note and no synthesis, because the synthesis holds what the session worked out
and the note holds what the operator did.

A rejection reaches the tree through this file alone. A candidate the operator turns down leaves no folder under
`references/`, no stub, and no copy, per rule 4 in Section 1.8. The reason is theirs, recorded in the words they typed,
and it lands in the running record on the turn they said it, so the next session on that objective reads what this one
declined whether or not the synthesis ever named it. A later session that finds the same page shows it labeled with the
date and the reason, at the bottom, rather than hiding it: the page they turned down for one question may be the page
they want for the next, and a result dropped in silence looks like a result never found.

Candidates live with the session rather than in the tree. A page the search returned and the operator has not accepted
stays with the session: the text goes when the search that found it ends, and its line in the running record stays.
Nothing stages it, copies it or writes it under the PKB root. `.inbox/` is where an accepted source stages on its way
through ordinary ingestion, and nothing else puts anything there. The cost is small, because harness code fetches a page
the operator accepts a second time. The alternative was thirty browsed candidates leaving thirty permanent folders in a
tree with no undo, in a staging area no channel can list.

### A session's sub-agents read; the expert writes

A session's sub-agents hold no write tool of any kind, the search sub-agents of the steps above and the
conflict-detection sub-agent a write fires (Section 1.7) alike. The permission layer enforces that, the way it confines
a Topic Expert to its own subtree: a write tool it never received is a write it cannot make. The expert authors
everything a session produces. Each search sub-agent spends a whole context window on one question and returns a page or
two, and that compression is the reason to run one.

Three sub-agents is the default width. The deployment sets it and nothing in the tree does, and the expert names the
width it used the first time a search reports back, because a budget that a topic's own files could raise is a budget an
agent's own write could raise. Part 4's research agents are a different thing, the breadth-first consumers of context
packs, and the name is the Project Manager's.

### The notes weigh the results; they never travel with the questions

The operator's notes hold the highest standing among the knowledge files, so the obvious move is to hand them to the
search sub-agents and let the search start from what the operator already believes. Measurement says to do the opposite.

A model told what the operator believes stops finding evidence against it: disconfirmation detection falls by 16 to 93
percentage points across four models once the belief sits in the prompt. Humans do the same thing to themselves. A
search conversation that agrees with the searcher raises the rate of confirming queries from 16% to 43%, and the
questions asked do the damage rather than the answers given.

The questions in step 3 therefore carry the objective and nothing else. The notes return in step 6, where the expert
weighs a verified result against them. A prior multiplies the evidence. It chooses no evidence.

### Search comes from the model provider

The experts already run on Ollama's cloud models, and the same account serves search, so the design signs up no second
vendor and opens no second account. Search takes one credential. The daemon reads it at startup and hands it down, on
the path the Telegram token already walks (`docs/how-to/`), and no agent, log or health endpoint sees the value.

A result arrives as the page's text rather than as a link to it. Ollama returns thousands of characters of content per
result, so harness code holds what a search sub-agent read at the moment it read it, and code then locates a quotation
in the exact bytes the claim came from. The check stops being a best effort against a page that may have changed and
becomes a comparison, and every admissibility rule below rests on that.

Search returns extracted text, so ingestion still fetches a source itself. The copy a topic keeps beside an accepted
reference is the original bytes off the web (Section 2.3): search serves the session, and ingestion serves the filing.

One provider serves the models and the search, so one outage takes both. The local fallback model runs in the hour the
cloud is unreachable, and search is unreachable in that same hour, so the searching stops. A search that cannot reach
the provider says so and ends, and the expert goes on answering from the topic's own notes and references on the local
model, at a fraction of the speed. The session stays open through all of it, and the next search runs the first time the
provider answers, so nothing polls and nothing queues.

### Code verifies every quotation

Published deep-research agents invent 3% to 13% of the URLs they cite, and 5% to 18% more of the URLs they give do not
resolve. In one shipped generative search product, 51.5% of the sentences it wrote were fully supported by the citation
attached to them. So no URL reaches a synthesis on a model's word:

- Harness code holds the text of every cited page. Search hands the page's content back with the result, and a sub-agent
  that wants a page beyond what its search returned reads it while it is still searching, so harness code holds that
  text too, for as long as the search runs. Verification fetches nothing itself. A citation harness code holds no text
  for keeps its claim out of the synthesis, and the file records the citation and the reason rather than the page's
  bytes.
- Harness code locates every quotation in that held text. It drops a quotation the text does not contain, and the file
  says so. The comparison runs against the bytes the claim came from, never against a page fetched again later and
  rewritten in between.
- Harness code asks no model where a quote sits, because models miscount positions and invent spans. The sub-agent
  returns the quoted text and code finds it.

The same rule governs an extraction: a quotation a model produces is a candidate until code finds it in the source.

### A page can be written to be read by an agent

A search is the first thing here that pulls text chosen by strangers into the conversation. Retrieved text therefore
travels fenced as data, under a standing instruction that nothing inside the fence is an instruction. Every quotation a
session shows the operator sits in a quoted block with its source attached, so a page's prose never speaks in the
system's voice.

That is mitigation rather than a cure. Four structural bounds hold behind it: the sub-agent's missing write tool, rule 8
in Section 1.8, quotation verification in code, and the exact bytes the operator reads before approving them.

### The budget bounds quality, and cost is not the reason

A long run is a worse run. Factual accuracy on one measured search agent fell from 79% to 17% as its tool calls rose
from 2 to 150. Between 77% and 94% of the steps in a long search add no new evidence, and a run that reaches the wrong
answer runs two to three times longer than one that reaches the right answer. Length is a symptom before it is a cost.

A single search carries a step budget and a wall-clock budget, and the session carries neither. Exhausting either budget
stops that search, and the expert says it stopped short of the objective. The operator can act on that: they say chase
it again with a narrower question, and the session is still open for them to say it in.

### What a session may file

| Outcome                                         | Where it lands                                                                                                                 |
|-------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------|
| Something the operator tried, and what happened | `notes/`, tagged `type.solution` when it worked and `type.note` when it did not                                                |
| The session's synthesis of what it worked out   | The synthesis section of its own `sessions/[objective-title].md` in the PKB root, in a file tagged `type.summary`              |
| A way of working the session established        | `skills/[skill-name]/SKILL.md`, in the topic's folder or in the root's (Section 2.4)                                           |
| An article the operator accepts                 | `references/[source-name]/`, through the ordinary ingestion procedure (Section 2.3), with the topic's own copy of the original |
| A candidate the operator rejects                | The rejection list inside the synthesis; the running record holds it either way, synthesized or not                            |
| Nothing at all                                  | Nowhere: no note, no synthesis, no folder, and the session file says so                                                        |

The operator approves the rendered text of the first three, in the session itself when they dictated the thing and in
the analysis session the learning channel holds when the analysis drafted it, and the skill filing names its scope and
any shipped skill it would shadow. An article goes in on their instruction instead: they name the candidate, harness
code ingests only a page it printed for them and fetched itself, and it writes the first extraction inside the turn. A
rejection changes no file but the session's own, and filing nothing is the common outcome and the bar working rather
than failing (Section 2.8).

Rule 8 in Section 1.8 is the line this table draws. A session files everything it read as a reference or as a synthesis,
and everything the operator did as a note, in their own words, after they did it, and the folder is what records which
of the two a file is.

### Direction is conversation, and so is the instruction that writes

The operator steers a running session by talking to the expert: drop that question, chase this one instead, that source
is no good, that lesson is half right. None of that writes anything, and none of it halts the session either. The write
comes on its own instruction, once the two of them have worked the text into something the operator will stand behind,
and a conflict the write-time check reports is direction like the rest: the expert reports it, the operator settles it,
and the write follows their word (Section 1.7).

The analysis differs only in where that conversation happens. It runs after the close, so it opens a session in the
learning channel and renders the exact bytes there (Section 2.8). The operator approves what enters the PKB. A skill
asks on its own terms, because the operator agrees to something different there. Accepting a source is one of these
instructions, and harness code ingests a page it printed for the operator and fetched itself, and no other.

### The analysis of a Librarian session fans out

On a Librarian session, the analysis treats the session itself as a source and fans it out. It asks each applicable
expert what its own topic takes from it, with the grammar the ingestion loop already uses section by section: something
new, something better, something that contradicts what I hold, or nothing. An expert that takes nothing leaves no folder
and no stub. Each note lands inside its own topic, so the Librarian still writes nothing.

A Librarian session's analysis therefore proposes a set of notes. The operator approves each one's text on its own, so
they take some and drop others and a rejection on one changes nothing about the rest. Four kept notes means four texts
to read.

A session that yields a portfolio lesson and a trading lesson yielded two lessons, the shape one book reaching two
topics takes. An insight that spans the topics rather than decomposing across them lands in one topic with
`related_topics` naming the others, and the hooks aggregate that into the root registry (Section 1.9).

A Librarian session keeps one file in the root `sessions/` folder like every other session, and one rather than several
because splitting the crossing into per-topic accounts loses the thing worth keeping. It carries the frontmatter Section
1.4 gives it, and one `topic.*` tag per expert that answered. Harness code writes it on the route a root process skill
takes: the expert that ran the session drafts the text, the operator approves the exact bytes of the synthesis, and a
root tool performs every write into the folder, so the Librarian still writes nothing (Section 2.8).

The mechanical tier covers it. The root walk takes this folder beside `index.md`, `tags.md` and the root `skills/`, and
the files inside it are ordinary knowledge files, validated as such and tagged into the root registry. A pack carries
the synthesis section alone, ranked last of what a Research Pack holds, and the running record and the distillation
travel in no pack at all (Part 4). A session file belongs to no topic, so the pack builder lifts that one section by
name rather than through a topic's walk, and the file itself reaches no expert, which Section 2.8 marks as a gap under
*How an entry improves an expert*. The write-time check does not run on one either, because Section 1.7 puts the check
on a note or a reference. A root process skill is the one other thing that analysis may propose, and a cross-topic
research skill is the kind it proposes most naturally: the Librarian's own competence has no topic to live in (Sections
2.2 and 2.4).

The Librarian holds no topic's search tools, which would let it answer a subject question out of its own head (Section
2.2). It reaches a topic's tools through the expert that owns them.

### Domain tool servers belong to a topic

A topic may bring tool servers of its own: a recipe service for Cooking, a case-law service for a legal topic. The
deployment binds them to that topic's expert, in the daemon's own configuration, for the reason the deployment picks the
model too: configuration an agent can write is configuration an agent can grant itself. `expert.md` may describe what a
topic uses, and it decides nothing about what a topic holds. A server is declared for the expert's own turns or for its
search sub-agents, and one declared for the sub-agents never reaches the expert directly, so a page off the internet
cannot enter a note by another route. The servers reach no other topic and no Librarian, so a narrow objective stays
with one expert and never becomes a routing problem.

## 2.8 The Self-Learning Loop: How the System Reasons Over What It Holds

One mechanism reasons over all three pillars, and one trigger starts it: a session closed. It runs in a session in the
learning channel, where the operator settles what it found, and it ends by writing the distillation back into the closed
session's own file. The mechanism distils a **lesson**: practical knowledge, which a note carries, or a repeatable
skill, which a `SKILL.md` carries. A session's synthesis is different: it records what the session worked out, the
analysis drafts it in the same pass (*The analysis cycle*, below), and a lesson is what the analysis distils from it.
The self-improvement skill holds that competence (Section 1.9).

### The one trigger

`/close` puts every closed session in the learning queue (Section 2.7), and the analysis reads the session file from the
beginning and checks each candidate against the tree (*The analysis cycle* below).

Time starts nothing. Conflict detection runs on the write, inside the session that made it (Section 1.7), and it does
not reach three cases: two notes filed months apart that both predate this design, the operator's own direct edits,
which no agent sees, and truth that changes with no write at all, where a reference is superseded and nothing moved. A
check the operator asks for over a topic is the one route left to those.

### What the check compares

A conflict-detection sub-agent compares on four axes, all of them knowledge against knowledge.

1. `notes/summary.md` against `references/summary.md`.
2. Single notes against references.
3. Notes against notes, the same person at different times under conditions they did not write down.
4. References against references, two sources contradicting each other with no human side to decide them, and on a
   re-ingestion the fresh extraction of a source against that source's file on disk, argument by argument, because a
   bounded reader handed two long documents answers confidently about the part it read.

The check reads for meaning with the expert's domain knowledge behind it, and it recognizes two statements that are both
true under different conditions. Section 1.7 says what a finding does: it goes back into the session that made the
write, and nothing in the tree records it. A finding on a write the analysis makes after `/close` reaches the operator
in the analysis session instead, because the closed session takes no more turns.

Harness code picks the pairs and the model labels them. The design takes the choice of pairs away from the model: code
picks them by claim-to-claim overlap, the model labels each one, and every pair the code picked reaches the operator
whatever the label says. A model scores under 11% on a marked contradiction pair (Section 1.7), so a model that chose
the pairs as well as labelling them would decide in silence which conflicts the operator never sees.

### The analysis cycle

1. **Read the file from the beginning.** The analysis reads the whole session file, first turn to last, and never a
   previous extraction. This is why it runs once and at the close: one-shot consolidation beats streaming, chained
   abstractions compound, and a distillation of a distillation carries an error nobody can trace back to the experiment
   that started it. A file too long for one turn walks through the bounded reader of Section 2.3 and comes back as one
   consolidated account, read beside the file.
2. **Draft the candidates.** The self-improvement skill drafts each kind with Section 1.9's drafting skill for it, the
   session's own synthesis among them, resolved topic-copy first so the topic's overloads apply. It proposes what the
   session learned and what is worth filing. The running record holds the operator's own words wherever they dictated a
   lesson during the session, and the draft carries them.
3. **Open a session in the learning channel.** The closed session takes no more turns, so the analysis raises what it
   found in a session of its own, attached to the learning channel where housekeeping interrupts no topic conversation
   (Section 2.7). It quotes the file: the objective, the close date, how long ago that was, and the running-record
   entries each candidate rests on, sliced out by code rather than summarized. An operator reviewing a session they no
   longer remember needs the evidence in front of them.
4. **Pair it against what the topic holds.** Harness code selects the notes a candidate has to answer for (*What the
   check compares*, above), and Section 1.7 governs one that contradicts a note the topic already holds: quote both
   sides, change nothing, let the operator settle it. The expert's objection arrives with the candidate rather than
   after it, because the operator reads both in one sitting.
5. **Approve the bytes.** Harness code renders each file that would land and the session's own synthesis with them, and
   the operator reads the exact text. Three candidates ask three times, and the operator may take one and drop two. A
   candidate that is half right is worked until it is right, because the two of them are in a session, and the write
   follows the operator's word.
6. **Write.** The files land and the hooks regenerate the indexes and the registry (Section 1.9).
7. **Append the distillation, then `/end`.** The analysis writes into the closed session's own file what it distilled
   and how it got there: the candidates it drafted, the ones the operator took and the ones they dropped and why, and
   the paths the kept ones landed at. A session that distilled nothing says so in that section rather than leaving none,
   because a file with no section reads as a session nobody analysed. The operator then says `/end`, which seals the
   file and ends the analysis session (Section 2.7).

### The default is silence

Nous Research shipped **Hermes Agent** in February 2026, and it is the nearest shipped system to this one:
agent-curated memory about the human, plus skills the agent writes for itself after hard tasks. Its prompt states its
prior in capital letters: *"Be ACTIVE. Most sessions produce at least one skill update, even if small. A pass that does
nothing is a missed learning opportunity, not a neutral outcome."*

That prior is right for Hermes and wrong here, and the substrate is the reason. Hermes writes into a skill library with
an archive and a rollback, so somebody reverts a bad write. This design writes into a tree with no undo, where a bad
note reaches every implementation pack on its topic and stays until the operator rewrites it by hand. So the prior
inverts. The analysis runs on every session and the filing does not, and a session that files nothing has missed
nothing. Hermes fires its review on accumulated tool iterations rather than at the end of a session, so its cadence is
no argument for this one either.

### Five things a session produces that look like knowledge

The same Hermes prompt carries an exclusion list. Each entry names a way a session manufactures something that reads
like a lesson. All five hold here, and the first is the dangerous one:

1. **An approach that never worked.** The session tried several things, none worked, and it ended by telling the
   operator to check by hand. Hermes names the harm: *"do NOT write those attempts up as a 'reliable workflow' or
   'recommended approach'. That presents an untested sequence of failures as validated guidance a future session will
   trust and repeat."* Three briskets that all came out dry support a note about three briskets. They support no note
   about how to dry-brine.
2. **A failure the environment caused.** The smoker's thermostat read 40 degrees low that week, or the data feed was
   down that afternoon. Filing that as a property of the technique blames the method for the machine.
3. **A verdict that a tool cannot do something.** Hermes on why this one earns its own entry: *"These harden into
   refusals the agent cites against itself for months after the actual problem was fixed."* A note saying the search
   provider returns nothing on a subject outlives the outage that produced it, and every later session reads it as a
   fact about the subject.
4. **An error a retry cleared.** *"If retrying worked, the lesson is the retry pattern, not the original failure."* The
   error is noise, and the patience might be knowledge.
5. **The story of one afternoon.** A narrative of what happened once is no rule, and `notes/summary.md` holds rules. The
   loop in Section 2.7 asks the operator to cook three times before distilling for this reason.

A sixth exclusion is already law in Part 1: nothing off the internet becomes a note (rule 8 in Section 1.8). A session
files what it read as a reference or as its own synthesis, and what the operator did as a note.

### What a session may conclude

A session authors four outcomes, and the table in Section 2.7 says where each lands: a note, its own synthesis, a skill,
or nothing at all. That table carries two more rows a session does not author, an accepted article and a rejected
candidate. The bar on the note is three conditions and each one carries load: the operator did the thing, they came back
and said what happened, and they approved the exact text that lands. An expert holding the first two alone holds an
experiment, and it argues about what the experience means and never about whose it was (Section 1.3).

### How an entry improves an expert

Each lesson improves the expert agents, and each kind reaches an expert by its own route:

- A note reaches `notes/summary.md` through the `summarization` skill, and a reference reaches `references/summary.md`
  through the same skill. An implementation pack loads `notes/summary.md` first, and a reference's depth file reaches
  that pack too (Part 4). The expert reads the distilled rule on every turn that touches the topic.
- A skill's name and description sit in the expert's prompt from the start of the turn and the harness opens the body
  when the turn needs it (Section 2.4), so a skill shapes the next draft before anybody asks a question (*A session may
  also teach the system how to work*, below).

A session file reaches no expert, and that is a gap. It belongs to no topic, so nothing routes it back to the topic's
own expert the way `notes/summary.md` reaches every turn, and Part 4 carries its synthesis section last of what a
Research Pack holds and the rest of the file not at all. The three gaps below are the others.

### The learning queue holds work; the learning channel holds the conversation about it

Every closed session enters the learning queue (Section 2.7). The queue is how a session ends rather than a place a
session lands when something else failed. `/close` queues the session whoever was attached to it, so one the operator
ran and one an agent ran alone take the same path, and the operator instructs either write. The worker drains the queue
when it reaches the entry, and the analysis reaches the operator when the operator is available.

The filing bar runs inside the analysis rather than in front of the queue, so the queue holds work and the learning
channel holds what cleared the bar. A session that establishes nothing is analyzed like any other, and the analysis
opens its session in the learning channel anyway and says so, because the distillation section is written there and the
operator says `/end` there (Section 2.7). Nothing about the bar moves with the channels a session was worked from: the
same five exclusions, the same three conditions on a note, the same rendered bytes, and nothing lands until the operator
instructs the write.

The split between the two is measured. Roughly one in seven closed sessions establishes anything, and a filing rate
above about one in five closed sessions is evidence that the bar is broken rather than generous. Put every closed
session in front of the operator instead and the review list is mostly nothing, so the operator stops reading it and the
one that mattered goes unread with the rest. The literature on lessons-learned databases reports that failure and agrees
about the cause.

The analysis opens its session in a learning channel rather than in a topic. The operator's first thought was a special
topic `kb-learning`, and they ruled against it. A topic is an expert, a `topic.md`, three pillar folders, an agent id,
an entry in the catalog the Librarian routes on (Section 2.2), and a write confinement drawn around its own subtree, and
the place these sessions run is none of those. Making it a topic produces an expert nobody wants to talk to, a routing
target nobody should route to, and folders that hold nothing.

The channel exists so that housekeeping never interrupts a topic conversation, and so the operator has one place to go
for what the system has learned lately. It is an ordinary channel that analysis sessions attach to, bound to no topic,
no objective and no agent, and its sessions run one after another rather than at once, because a channel holds one
session at a time (Section 2.7). Each one names the closed session it came from and the topic that owns it, because the
channel says neither.

Queuing at the close takes most of the staleness out. `/close` queues the session the moment the work ends. The queue
can still lag, so two things guard the far end. The analysis quotes the file rather than asserting a conclusion (step 3
above), and an entry that has waited too long is skipped rather than analysed, and the session in the learning channel
says so, so the skip lands in the file and the operator seals it with `/end`. Nobody has settled how long is too long.

Two things about the channel are not settled. The first is which agent runs an analysis session: the channel binds to
none, and naming the topic that owns the closed session points at that topic's expert without saying so. The second is
whether the queue needs a cap, because an agent working overnight can pile up sessions nobody has opened.

### The skills that already generalize

Two shipped skills (Section 2.4) generalize outside the analysis, and neither files anything itself. `discovery` finds
the rule under two notes that never mention each other, so the finding goes back through the front door and the operator
approves the text like any other. `voice` watches for the same edit repeated across three drafts and proposes it as a
rule, which is the procedural pillar generalizing from experience about the operator. `summarization` and
`conflict-detection` do the work already described above, in the four axes and in *How an entry improves an expert*.

Section 1.9's self-improvement skill runs the analysis and its two drafting skills do the writing (Section 2.4). The
checks a draft has to answer for stay in harness code: a skill is a file the operator may adopt and then edit, and a
guarantee living in an adopted copy leaves the day they edit it. That holds hardest for the skill draft, whose subject
is what a skill should say.

### Two guards Hermes puts in code, and both belong here

Nothing rewrites a file whose current text it has not read this turn, and `RS-141` is the rule that carries it. Hermes
refuses a patch to a file the reviewer has not loaded verbatim in that same turn, because *"the autonomous review fork
is allowed to evolve skills, but it must not patch or rewrite content it has only inferred from the transcript."*
An analysis proposing to revise a note the operator filed in March works from an impression of that note, the impression
came out of a conversation that has since compacted, and the operator may have edited the note meanwhile. Read the file,
or leave it alone. The rule lives in harness code for the reason every other guarantee here does: a guard written into a
skill leaves the day somebody edits their copy of that skill.

Authorship decides what may be curated, and `RS-142` is the rule that carries it. Hermes tags every skill write with its
origin, so autonomous curation touches the skills the autonomous process itself created and no others:
*"Skills a user asks a foreground agent to write belong to the user and must never be auto-curated."* Part 1 already
draws that line as the collaboration rule. Harness code writes an **authorship file** beside the `SKILL.md` and reads it
back, and authorship says whose hand put the content there. It is a second file rather than a block inside the skill,
because the `SKILL.md`'s two fields leave no room for it and its body loads into a model's prompt, where an origin block
would read as one more line of procedure. A folder with no authorship file is the operator's.

### A session may also teach the system how to work

A session feeds the procedural pillar as well as the practical one. A session that established *brisket holds at 250*
fed `notes/`, and a session that established *a better way to run a session* has something for `skills/`, which the
analysis may propose. `voice` is the seed of that half: it holds a profile of the operator, corrected from their own
edits through the same propose-and-approve loop as everything else (Section 2.4).

A note says what is true and a skill says how to work. That line decides every proposal, and the test that separates the
two is who acts on the draft first. A skill shapes how the expert works before anybody asks a question, because the
DeepAgent harness holds its name and description in the prompt from the start of the turn and opens the body when the
turn needs it (Section 2.4). A note answers a question about the subject once somebody asks one, and harness code
fetches it when it is relevant.

Run the test on the three cases that matter. *Brisket holds at 250 for four hours* waits for somebody to ask about
brisket, so it is a note. *Always preheat the grill for 15 minutes* reads as an instruction and is still a note, because
the operator at the grill acts on it and no draft changes shape until they ask. *Ask for the pit's own thermometer
offset before drafting any smoking lesson* changes the expert's next draft first, so it is a skill.

A procedure the operator proved by doing is a solution note, tagged `type.solution` (Section 1.5), and it becomes a
skill once it directs the expert's own drafting rather than the operator's own doing. Section 2.4 decides where the file
lands, so the filing decides one thing, the scope.

### The four decisions a written skill needs

A skill write asks in its own words. The operator instructs every write under a `skills/` folder, so the question is
which sentence they read when they do. *A lesson is ready to file* is the wrong sentence in front of a file that changes
how the expert works on every later turn. The skill filing names the scope and any shipped skill it would shadow, with
the exact `SKILL.md` text underneath. Agreeing in the analysis that the session learned something is agreement about the
lesson, and the procedure the expert then wrote from it is a second object the operator has not read yet.

A session revises a skill a session wrote, and never one the operator wrote, and the two guards above reach skills
unamended. Read-before-write means a proposal to revise `skills/session-loop/SKILL.md` loads that file's current bytes
in the same turn and derives the revision from them. Authorship means a session amends a folder carrying the authorship
file a session wrote and no other, and a folder without one is the operator's. A session that wants such a skill changed
proposes the change in conversation and leaves the edit to the operator.

The expert that ran the session asks for a root process skill, and harness code writes it. The expert drafts the text
and calls a root tool, and the tool performs the write once the operator approves. Every session file takes the same
route into the root `sessions/` folder (Section 2.7). The Librarian's write capability stays at zero, as it does for
topic creation, and widening an expert's permission to write outside its own topic is refused, because that loosens the
subtree confinement on every turn to serve one filing the operator instructed in the first place. Both root folders are
denied to every agent, and the root tool is the only way into them (Section 2.4).

A skill that shadows a shipped one by name says so three times. Section 2.4 gives the mechanism, and nothing in the tree
records the swap: no index lists the file and no tag points at it. So the proposal says it, the exact bytes the operator
approves say it again, and the file opens with the line naming what it shadows (Part 3). The third one matters most,
because whoever approved the skill is not the person who opens that file six months later. The collision is never
refused, because improving a shipped skill for one topic is the most useful thing an operator does.

### A wrong skill is worse than a wrong note

A wrong note is wrong about brisket: somebody pulls it into a pack, cooks the thing, and the result on the plate
corrects them the same afternoon. A wrong skill is wrong about every session that follows.

A session in March concludes that the pit runs hot and writes a skill saying *treat every stated temperature as twenty
degrees high before drafting*. The operator reads it once, agrees, and it lands. In April the operator replaces the pit.
Every draft after that subtracts twenty degrees from a correct number, in every session on that topic, before anybody
asks a question. The drafts carry no mark saying which skill shaped them, so the operator reads a wrong temperature and
corrects the draft rather than the file.

No check catches that, and none is meant to. The check compares claims, and *treat every stated temperature as twenty
degrees high* instructs an agent rather than asserting anything about the pit, so no note in the tree agrees or
disagrees with it (Section 1.7). A skill fails where somebody uses it, so the session that hits it is what reports it
(Section 2.4), and the guard in front of it is the approval the operator gave in March.

The worst version is a shadow. A topic skill declaring `name: conflict-detection` replaces the shipped one for that
topic, and that skill now runs on every write, so a bad shadow removes the only reader that would have caught the bad
note rather than one of several. Exclusion 3 above is the same failure at a smaller scale, and a skill is where that
hardening happens, because a skill is the file the system follows without being asked. An approach that never worked,
written up as a procedure, becomes a procedure.

### Where a lesson about the operator goes, settled

`voice` keeps the operator's register and nothing else. A procedure about running a session goes to a root process
skill, and a preference about wording goes to `voice`. Splitting them costs one judgment in the analysis. Merging them
would put *ask for the pit's thermometer offset before drafting* into the file every draft is style-checked against.

### The procedural pillar has no breadth file, and adding one is a decision

Each subject pillar carries a human-approved `summary.md` (Section 1.6), and the operator has asked for a third one. The
procedural pillar outranks the theoretical one (Section 1.8, rule 1) and has no file that bounds exploration mode.
The design places none, because the obvious placement contradicts Part 1: everything under a `skills/` folder is a skill
file (class 3 in Section 1.4), so a `summary.md` sitting inside one is either a knowledge file living in a folder the
rules exempt, or a fourth file class this document never defines. `Cooking/skills/summary.md` passes content validation
and the tree walk then warns `LEGACY_SKILL_LAYOUT`, because a flat markdown file inside `skills/` is the superseded
layout and loads as no skill.

Three shapes answer it, and the recommended default is the first. A skills section inside `topic.md` needs no new file
class, puts the pillar's overview in the file the Librarian already routes on, and gives up only an approval the
operator already gives when they approve `topic.md`. `skills/summary.md` as a fourth file class carries its own
frontmatter rules and its own exemptions, and costs changes in Sections 1.2 and 1.4 and in Part 3. A generated file
gives up the operator's approval that makes a breadth file worth reading, and Section 1.6 refuses it on that ground. The
operator picks, and Section 1.2 grows a folder comment when they do.

### Three gaps in the self-learning loop

The PKB is meant to improve itself from what it learns in the work. Three things stand between the design as written and
that claim, and each one belongs to a pillar. The design answers none of them.

The PKB notices nothing while nobody is in a channel. It notices a conflict on the write, because a session is there to
report to (Section 1.7), and it notices nothing else: the one mechanism that reasons over all three pillars waits on
`/close`, and `/close` waits on the operator. An agent that reasons when spoken to and never otherwise is a filing
system with a good vocabulary.

The PKB does not know the operator. `voice` holds how they write, and nothing holds how they decide: what they have
turned down and why, which arguments have moved them, which kinds of evidence they ask for before they will try
something. A record of that would make every proposal better, and it would be the most sensitive file in the tree, which
is the reason to design it rather than accumulate it.

Nothing decays. A note from two years ago carries the same weight as one from this morning, and `updated` is the only
field that records the difference. The practical pillar needs an answer here and the theoretical pillar does not,
because a book stays as true as it was while a note about a pit the operator no longer owns goes stale without ever
becoming false. The write-time check catches a later note contradicting an earlier one, and it catches nothing where
they stopped doing it.

---

# Part 3: PKB Layout and Bootstrapping

The full PKB is a tree of topic roots, each following the standard structure of Section 1.2:

```
KnowledgeBase/
├── index.md                # Root catalog: every topic + description (machine-maintained)
├── tags.md                 # Global tag registry (machine-maintained)
├── .inbox/                 # Staging for sources on their way in – dot-prefixed, indexed nowhere
├── (optional) skills/      # PROCEDURAL – process skills every expert loads, plus adopted ones. Starts empty
│   └── [skill-name]/       #   one folder per skill (session-loop/, ...)
│       └── SKILL.md
├── (optional) sessions/         # One file per session, for its whole life, whichever agent ran it. Starts absent
│   └── [objective-title].md     #   Objective and experts, record, synthesis, distillation
│                                #   topic: "(session)", a tag per expert (2.7)
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
│   ├── (optional) expert.md
│   └── (optional) sub-topics/
│       └── (same structure recursively)
└── (other topic roots...)
```

## Bootstrapping an empty PKB

The PKB starts empty. The path to the steady state:

1. **The default skills ship with the implementation, mounted rather than copied in.** The implementation supplies
   starter versions of ten common skills, named one by one in Section 2.4: `ingestion-classification`, `ingest-paper`,
   `ingest-book`, `summarization`, `conflict-detection`, `tag-proposal`, `sub-topic-proposal`, `research`, `discovery`,
   and `voice`. They load from the implementation itself, so the tree's own `skills/` folder starts empty and an
   untouched skill improves whenever the implementation does. They work out of the box, and the operator who wants to
   change one adopts it. A copy lands in a `skills/` folder in the tree, at the root or in one topic (Section 2.4),
   opening with one line naming the shipped skill it now shadows, and it shadows that skill permanently. Adoption is a
   decision and never an accident, because a seeded copy nobody touched is indistinguishable from one the operator
   rewrote, and with no undo the implementation would have to choose between overwriting their work and never shipping
   an improvement.

   Three routes fill the tree's own `skills/` folders: the operator dictates a skill to the expert, the operator adopts
   a shipped one, or a session's analysis proposes one and the operator approves its exact text (Section 2.8). The
   permanent-fork warning attaches to the name rather than to the route, and *Where a skill lives* in Section 2.4 states
   that rule once for all three.
2. **`voice` ships with an opinionated starter profile, corrected from the operator's own writing.** Every draft has a
   voice whether or not somebody wrote one down, and without a profile it is the model's own, chosen by nobody. A wrong
   default shows up in the first draft and gets fixed, and an absent one never does. So the shipped skill states real
   rules, and the operator corrects it from whatever writing they already have. A topic may hold its own voice, which
   replaces the root profile for that topic.
3. **The operator creates the first topics on demand.** With zero topics, every inbound item is a topic gap. The
   operator requests a topic, or approves one the Librarian proposes, and each new topic follows the topic creation flow
   in Section 1.9. Nobody designs a taxonomy up front, and the tree grows from what the operator captures.
4. **Structure catches up mechanically.** As soon as files exist, the hooks generate the indexes and the tag registry,
   and nobody seeds anything by hand.

---

# Part 4: How Projects Use the PKB

The Project Manager (separate project) orchestrates projects that consume and enrich this PKB. Project access is
agent-mediated like every other PKB interaction (Part 2): a project agent sends its request to the Librarian, which
routes it to the right Topic Experts, or it connects to a known Topic Expert.

## Context packs

A Topic Expert assembles a context pack on request, matched to the requesting agent's role. Research agent and
implementation agent are the Project Manager's own names for those roles.

- **Research agents (breadth-first)** receive a Research Pack, which serves exploration mode. It holds `topic.md`,
  the relevant subtrees of the root `tags.md`, and the `summary.md` files of the relevant topics. A research agent reads
  no `index.md` unless it asks for one.
- **Implementation agents (depth-first)** receive an Implementation Pack, which serves exploitation mode, when the
  task is defined. It holds `notes/summary.md`, the full `index.md` of the selected topic, the
  `references/[source-name]/[source-name].md` files, and the relevant solution notes. `notes/summary.md` loads first,
  because the operator's rules hold the highest priority in a pack.

The split is rule 2 in Section 1.8, and it is what bounds a pack. A breadth reader handed the depth files of one topic
receives more than one context window holds, and a pack that recurses into sub-topics multiplies that. A pack therefore
carries a size budget and truncates at an entry boundary rather than mid-file, and it names what it omitted.

Rule 1 in Section 1.8 ranks the practical and procedural pillars above the theoretical one, and every pack follows that
order for the pillars it carries, because a pack that leads with references and appends the operator's notes inverts the
one rule the PKB exists to keep. No pack carries the procedural pillar, and the reason is the audience rather than the
standing: a skill instructs the agents that work this PKB, and a consumer of a context pack works elsewhere.

A session's synthesis ranks last of what a Research Pack carries (Section 2.7), because it records how the topic came to
know a thing rather than what the topic knows, and the running record and the distillation around it travel in no pack
at all. A session file belongs to no topic, so the pack builder lifts that one section by name rather than through a
topic's walk, and no expert ever reads the whole file, which Section 2.8 counts among the gaps (*How an entry improves
an expert*). A lesson a session filed is an ordinary note, and it enters a pack as one.

## Conflict escalation

A project agent that finds two files in its pack disagreeing on the question it is working escalates to the operator
rather than picking the reading that suits its task. Nothing in the tree marks the pair for it: a conflict is detected
when a session writes one of the two files and settled in that session (Section 1.7), so a pack carries no flag and the
escalation rests on the agent noticing. That is a real loss against a durable flag, and what it buys is a tree where
nothing waits flagged for later. The operator settles what a project agent raises in a session, like any other conflict.

## Knowledge feedback

After a project, or after a retrospective, the Project Manager proposes PKB updates:

| Update Type                | Description                                                 | Example                                                              |
|----------------------------|-------------------------------------------------------------|----------------------------------------------------------------------|
| **New Note**               | One observation or event from the project                   | "Referral program required legal review"                             |
| **Breadth summary update** | A general rule distilled from experience                    | "Always check legal requirements before launching referral programs" |
| **New Solution Note**      | A reusable approach, filed as a note tagged `type.solution` | "Referral program with legal review framework"                       |
| **Reference Update**       | A new reference, when the project found one                 | A relevant article on referral program compliance                    |

Part 1's standards hold here as on every other channel, and the caller decides none of it: a project agent proposes an
update and approves nothing, and what it proposed lands only when the operator instructs the write. The operator
approves the rendered text rather than a request to write it (Section 2.7). The ingestion skill writes the first
extraction of a named source inside the turn, because the operator named the source and that instruction is the approval
(Section 1.3). A skill write names its scope and any shipped skill it would shadow, with the exact `SKILL.md` text
underneath (Section 2.8), because a skill changes how the expert works on every later turn. Once a change lands, harness
maintenance regenerates the indexes and the tag registry.

The five exclusions in Section 2.8 bind a project retrospective as hard as they bind a session's analysis, and a
retrospective is where they break most easily. A project that tried four approaches and shipped none of them produces a
breadth summary update proposing the fourth as the recommended one, and the rule it writes reads the same as a rule
somebody earned. A project agent proposing an update names what the project shipped, and says so when the work never
worked.

---

# Part 5: Conflict Handling Example

Section 1.7 carries the rules. This part runs one conflict from the write that found it to the session that settled it.

The operator opens a session on the Cooking Topic Expert and dictates a note: always preheat the grill for 15 minutes.
The expert drafts the text in the operator's register, and the draft is what starts the check.

Harness code picks the pairs by claim-to-claim overlap and hands them to a conflict-detection sub-agent, which reads the
tree before the note lands and holds read tools and no write tool. It labels the one pair the code found, on the axes of
Section 2.8: `references/grill-basics/grill-basics.md` says preheating for 10 minutes is sufficient.

The expert reports the pair into the session and says whether the two genuinely oppose each other. Neither file names a
pit, so the two may both be true under conditions neither writes down. The operator answers that their pit runs cold and
15 minutes is what it needs, which is the third resolution in Section 1.7: both hold, and the note gains the condition
that separates them. The expert redrafts, the operator approves the exact text, and one note lands with the ordinary
frontmatter of Section 1.4.

No tag records the conflict and no date says a review happened later, because none did. The reference is untouched,
since the book was never wrong about the book. The conflict is over when the session says so, and two traces survive: a
note that reads better than the dictated one, and the running record in the session's own file, which names both files
and what the operator decided, because nothing else remembers it (Section 2.7). Had the operator decided the book was
right, they would have edited the draft to 10 minutes before approving it, and had they wanted to think about it, the
record would say the pair is open.

---

# Summary

The **Personal Knowledge Base** is an AI-assisted expert for the subject areas the operator works in:

- It rests on three pillars, and every topic has a place for each. Theoretical is `references/`, what others
  established. Practical is `notes/`, what the operator established by doing. Procedural is `skills/`, how the two of
  them work together. The external world supplies the theoretical pillar: books, papers and internet articles.
- It fuses the three pillars through hierarchical tags that organize a topic where a folder would have, a
  machine-maintained global tag registry, and conflict handling that runs on the write and leaves nothing behind.
- Human content wins: the practical and procedural pillars outrank the theoretical one, so an operator's note and a
  human-approved breadth summary take precedence over a reference.
- Division of labor: the practical and procedural pillars are human-generated, AI-curated, everything else is
  AI-generated, human-curated, and hooks generate the mechanical files.
- Breadth vs. depth: `topic.md` and `summary.md` serve a breadth-first reader, `index.md` serves a depth-first reader,
  and the split is what bounds a context pack.
- Agents mediate every interaction. The Librarian researches across topics: it frames an objective so single topics can
  answer it, classifies each inbound item, and recognizes when two topics' answers interact. Harness code fans the item
  out to every applicable Topic Expert and merges their answers by attribution, so classifying is a model's judgment and
  fanning out and merging are code. Cross-topic research is a competence of its own, distinct from depth in one subject,
  and its skill lives at the PKB root because it belongs to no topic. The Librarian holds no topic knowledge and writes
  nothing into the tree.
- Topic Experts run the topics, one PKB template by default, overridden per topic through `expert.md`. Hooks enforce the
  mechanical standards, and the experts carry the judgment work through common, overloadable skills.
- Ten skills ship with the implementation (Section 2.4), sorted by pillar. They mount from the package ahead of the
  tree's own `skills/` folders, read-only. The tree's folders take writes, and a file declaring a shipped skill's name
  shadows it permanently. The design names seven more, one of them the Librarian's own cross-topic research skill
  (Section 2.2).
- A session is how anyone works with the PKB in dialog: one objective, opened on the Librarian when the objective
  crosses topics or on one Topic Expert when one topic owns it (Section 2.7). Neither is a default, and the operator
  chooses at the start. The session holds its own name and a channel is a way in that attaches to it, so several
  channels may hold one session at once and it stays one conversation whatever number are looking at it. The counterpart
  is the operator, or a project agent that needs the knowledge. An objective that needs a new tool or a process to
  follow makes the session write instruction sets, which state why the work is necessary and what it must achieve and
  never an implementation. One file in the root `sessions/` folder holds the session for its whole life: the objective
  and the experts, the running record the session writes as it goes, the synthesis of what it worked out, and the
  distillation the analysis appends, so a note that came out of a session has its whole evidence in one document and
  nothing deletes it. Two commands mark that file. `/close` says the operator has nothing more to craft in this context
  rather than judging the session's worth: it marks the file closed and keeps it, so the next session on that objective
  reads what this one declined, it brings every attached channel away, and it puts the session in the learning queue,
  every time. The analysis then opens a session in the learning channel, naming the closed session and the topic that
  owns it, the operator settles it there one such session at a time, and the analysis appends what it distilled and how
  it got there before `/end` seals the file (Section 2.8).
- A session that searches asks with the objective and none of the operator's beliefs, verifies every URL and quotation
  in code against the page text the provider returned, and weighs what survives against the operator's notes without
  touching one.
- A conflict is found on the write, by a read-only sub-agent that compares the file against the tree on four axes, and
  it is settled in the session that made the write (Section 1.7). Nothing tags a file, nothing queues a conflict, and no
  timer re-reads a pair a write already compared, so a pair whose files both predate the design, an operator's own
  direct edit and a truth that changed with no write reach nobody until the operator asks for a check.
- One mechanism reasons over all three pillars, on one trigger (Section 2.8): a session closed. It checks each candidate
  against the tree, runs in a session in the learning channel where the operator settles what it found, and ends by
  writing the distillation back into the closed session's own file.
- The analysis reads the session file from the beginning, because chained abstractions compound and one-shot
  consolidation beats streaming. The self-improvement skill carries that competence: it drafts candidates, quotes the
  record each one rests on, and lands the bytes the operator approved and no others. Its conclusions are bounded,
  because five kinds of session output look like knowledge and are not: an approach that never worked, a failure the
  environment caused that week, a verdict that a tool cannot do something, an error a retry cleared, and the story of
  one afternoon. Every closed session enters the queue, whoever was attached to it, and filing nothing is the default
  result. The filing bar runs inside the analysis rather than in front of the queue, so the queue holds work and the
  learning channel holds what cleared the bar: about one closed session in seven establishes anything, and a review list
  of everything goes unread.
- A session may also teach the system how to work. A note says what is true and a skill says how to work, and the test
  is who acts on the draft first. A wrong skill is worse than a wrong note, and it marks nothing it shaped. The conflict
  check reads no skill, because a skill states no claim, so a stale one surfaces through the session that hits it and
  through the exact text the operator read before it landed. Sections 2.7 and 2.8 record what the design has left
  unsolved: a session file reaches no expert, the PKB notices nothing while nobody is in a channel, nothing holds how
  the operator decides, nothing decays with age, the procedural pillar has no breadth file, and four questions stay
  open: whether anyone but the operator may ever instruct a write, which agent runs an analysis session, whether the
  queue needs a cap, and how long a queued analysis stays eligible before it ages out.
- The DeepAgent harness hosts the agent layer and exposes it through a dedicated TUI, Telegram channels and other
  channels, and the operator connects to the Librarian or to one Topic Expert.
- The Project Manager (separate project) reads the PKB through context packs and feeds project outcomes and lessons
  learned back into it.

Every component works under **human strategic control**. AI stays tactically brilliant. Humans keep the strategic
vision.
