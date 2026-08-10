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

The procedural pillar holds skill files. A skill file carries no PKB frontmatter, appears in no `index.md`, and
contributes no tags, so every rule below that reads frontmatter passes it by.

The external world supplies theoretical knowledge: books, papers and internet articles. The PKB ingests a source during
a session, when the operator points it at a web page or gives it a file that holds an eBook or a scientific paper.

The three pillars carry different weight. Practical and procedural knowledge outrank theoretical knowledge. Practical
knowledge states how to apply a theory to an objective.

Humans and agents reach a topic's knowledge through sessions. A session is a long-standing conversation that pursues an
objective. A session connects to the Librarian to gain expertise across several topics, or to one Topic Expert when the
operator already knows which area owns the work.

Both subject pillars carry a summary that the operator approves: `references/summary.md` and `notes/summary.md`. A
summary helps an agent find an analogous problem or solution in another area of expertise. It bounds what an agent reads
during the exploration stage, and the notes and references themselves supply the detail an agent needs during the
exploitation stage, when the task is defined.


---

# Part 1: PKB Design

## 1.1 PKB Harness Design Goals

1. **Fuse the three pillars** into one body of knowledge about each topic: what others established, what the operator
   established by doing, and how the operator and the agent work (The Three Pillars, above).
2. **Keep every interaction agent-mediated and frictionless**. The operator and external agents work through the
   Librarian and the Topic Experts. They capture, retrieve, and refine knowledge in dialog, over any connected channel,
   with no file management of their own and no external tools.
3. **Enforce common standards and preserve topic depth**. Harness hooks and shared skills keep structure, metadata,
   tags, and conflict handling identical across topics. Each Topic Expert adds its own domain knowledge and its own
   organization on top.
4. **Grow the PKB**. Acquire theoretical knowledge by ingesting books or articles. Acquire practical or procedural
   knowledge through generalizing on PKB.

## 1.2 Standard Topic Structure

The PKB is a folder tree. *The tree* names those folders throughout this document. Each topic root uses this structure:

```
[Topic Root]/
├── topic.md            # Human-approved overview and map. Helpful for exploration.
├── index.md            # Machine-generated canonical index (incl. tag subtree) Helpful for exploitation. 
├── references/         # THEORETICAL PILLAR – what others established (books, papers, articles)
│   ├── summary.md      # Human-approved overview of all references - helpful for exploration.
│   └── [source-name]/  # One folder per ingested document
│       ├── [source-name].md  # Map of the source: a section per part, a bullet per argument - helpful for exploitation. 
│       └── [source-files]
├── notes/              # PRACTICAL PILLAR – what the operator established by doing
│   ├── [note-title].md # Note content (standalone or main file in note folder)
│   ├── [note-title]/   # Optional folder for notes with media
│   │   ├── [note-title].md  # The note text
│   │   └── media/      # Images, screenshots, videos, etc.
│   └── summary.md      # Human-approved distilled rules and solutions from all notes Helpful for exploration.
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
where a folder cannot. A Cooking recipe is a note tagged `topic.cooking.recipes` rather than a file under `recipes/`,
and Section 1.5 carries the tag rules.

One `sessions/` folder sits at the PKB root (Part 3), and it holds every session summary, whether the session attached
to the Librarian or to one Topic Expert (Section 2.7). One place to sweep beats one folder per topic, because the
analysis that generalizes over sessions reads them all.

Do not put item content in an `index.md`. The topic-level `index.md` stays the machine-generated directory index.

One file per source, and it is a map of that source. `[source-name].md` carries the source's thesis, its provenance, one
section per part of the source as the source names them, one bullet per argument the topic cares about, and an honest
list of what nobody read. The word *summary* names the failure this shape prevents: a confident write-up of the part
that fit in one context window, with nothing recording that the rest was never opened. Re-ingest a source as often as it
is worth re-ingesting, because each pass reconciles with what is there and then appends what it covered, what it
skipped, and when.

## 1.3 File Types and Creation Rules

**AI + Human** means the expert drafts and the operator approves or edits the exact text before the file lands.
**Human + AI** reverses the order, and **Hooks** means harness code writes the file and nobody curates it.

| File                                       | Built By                     | Purpose                                                                                                                                                                                                                                                                                                                  |
|--------------------------------------------|------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `topic.md`                                 | **AI + Human**               | Breadth map for a breadth-first reader. The expert drafts and maintains the overview. The operator adds insight and approves.                                                                                                                                                                                            |
| `index.md` (topic root)                    | **Hooks**                    | Depth index for precise retrieval, with the topic's tag subtree and cross-topic mappings. Harness hooks regenerate it on change.                                                                                                                                                                                         |
| `expert.md` (optional)                     | **Human + AI**               | Topic override of the PKB Topic Expert template (Section 2.3). The operator writes it and the expert assists.                                                                                                                                                                                                            |
| `skills/[skill-name]/SKILL.md` (optional)  | **Human + AI**               | The procedural pillar for one topic: a skill only this topic's expert loads (Section 2.4). The operator writes or approves it and the expert assists. A write here is gated, and it is the one skill path inside the expert's own subtree.                                                                               |
| `references/summary.md`                    | **AI + Human**               | Breadth summary of the theoretical pillar. The expert drafts it. The operator edits and approves.                                                                                                                                                                                                                        |
| `references/[source]/[source].md`          | **AI**, then **AI + Human**  | Depth map of one source: thesis, provenance, a section per part of the source, a bullet per argument, and what nobody read. The ingestion skill writes the first pass un-gated, because the operator named the source, and a pass that rewrites it waits for their approval.                                             |
| `notes/[note-title].md`                    | **Human + AI**               | What the operator knows from their own practice: an observation, an opinion, or something that worked (tagged `type.solution`). They write it, or they settle it in the analysis after `/close`, once they tried the thing and the expert drafted what they settled (Section 2.7). The operator approves the exact text. |
| `notes/summary.md`                         | **AI + Human**               | Breadth summary of experience: distilled rules and notable solutions. The operator edits and approves. **Highest priority among the knowledge files.**                                                                                                                                                                   |
| `tags.md` (PKB root)                       | **Hooks**                    | Global tag registry, derived from file frontmatter. Regenerated whenever files change.                                                                                                                                                                                                                                   |
| `index.md` (PKB root)                      | **Hooks**                    | Root catalog: every topic with its description, aggregated from `topic.md` frontmatter – the Librarian's routing view.                                                                                                                                                                                                   |
| `sessions/[objective-title].md` (PKB root) | **AI + Human**               | A session's synthesis (Section 2.7): the objective, the questions asked, every source kept, every source rejected and why. Harness code renders it and a gated tool writes it, because the Librarian writes nothing and no expert writes outside its own subtree. The operator approves the exact text.                  |
| `skills/[skill-name]/SKILL.md` (PKB root)  | **Human + AI**               | The procedural pillar for every topic: a skill every expert loads (Section 2.4). The folder starts empty. A write here sits outside every expert's subtree, so it needs a gated tool that does not exist yet (Section 2.4).                                                                                              |

**Collaboration rule**: the practical and procedural pillars are **human-generated, AI-curated**. `notes/`, the
`skills/` folders and `expert.md` overrides carry the operator's own experience and their own ways of working, and the
expert assists with clarity, grammar and structure. Every other meaning-carrying file, `topic.md`, the breadth summaries
and a session summary, is **AI-generated, human-curated**: the expert drafts, and the operator adds insight and
approves. The expert writes the theoretical pillar's depth files on first ingestion, and the operator curates them at
the summary level and approves any later pass that rewrites one. Hooks generate `index.md` and the root `tags.md`, and
nobody curates them.

Four compound terms are fixed, and this document never varies them: `human content wins`, `human-approved`,
`human-generated, AI-curated`, and `AI-generated, human-curated`. They name a rule and three file classes, and harness
code cites them in these exact words. The person they name is the operator.

An expert may draft a note or a skill itself in the analysis, and both stay on the human-generated side of that line.
The experience in them is the operator's: they cooked it, they ran it, they came back and said what happened. The expert
drafts the wording from the session record, argues about what the experience means, files the text the operator approves
word for word, and never argues about whose experience it was.

Skill files are a file class of their own. Everything under a `skills/` folder, at the PKB root or inside a topic,
instructs an agent rather than describing a subject. It follows the DeepAgent harness's own skill format, and the PKB
rules for knowledge files pass it by (Section 1.4), because the PKB fields on a `SKILL.md` break the harness's parser.

## 1.4 Metadata Requirements

Every markdown file that carries knowledge includes YAML frontmatter. Three file classes exist, and this section governs
the first alone:

1. **Knowledge files** – notes, references, the breadth summaries, `topic.md` and session summaries. Full PKB
   frontmatter, as below. A session summary is one of these too, and no topic owns it: it sits in the root `sessions/`
   folder, its `topic` field reads `(session)`, it carries a `topic.*` tag for each expert that took part and names
   those topics in `related_topics` so the registry picks up any crossing, and the check comparing a declared topic
   against a file's location has nothing to compare (Section 2.7).
2. **Machine-generated files** – `index.md` at any level and the root `tags.md`. Minimal generated frontmatter only.
3. **Skill files** – everything under a `skills/` folder, at the PKB root or inside a topic, plus `expert.md`. These
   instruct an agent rather than describing a subject: nothing here appears in any `index.md` and nothing here
   contributes tags. A `SKILL.md` carries the DeepAgent harness's own two fields, `name` and `description`, and nothing
   else. The PKB fields break the harness's parser, and the harness then drops the skill without an error anywhere.

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

Every knowledge file, class 1 above, carries a `description`, because index generation extracts it (Section 1.9). The
generated files carry their own minimal frontmatter instead: the root `tags.md` in Section 1.5 has a `title` and a
`source_type` and no description, and a description there would ask the generators to fail their own validation.

`related_topics` lists related topic paths in tag notation, such as `bbq.equipment`. Declare a cross-topic relationship
here and nowhere else. Harness hooks aggregate these declarations into the registry (Section 1.9).

`provenance` is a proposed eighth field that says how the knowledge arrived. Layer 1 does not recognize it yet: the
schema fixes seven required fields plus `related_topics` and the two the design retired (Section 1.7), so a file
carrying `provenance` today draws an unknown-field warning and lands anyway. Everything below states the design the
session work needs (Section 2.7). The field takes one of four values:

| Value        | What it means                                                                                                 |
|--------------|---------------------------------------------------------------------------------------------------------------|
| `practised`  | The operator did the thing and this is what happened. A lesson the analysis settles carries it (Section 2.7). |
| `stated`     | The operator said it, and has not tried it yet.                                                               |
| `researched` | A session worked it out, by reading or by argument, weighed against the operator's own notes (Section 2.7).   |
| `ingested`   | It came in through a source: a book, an article, a paper.                                                     |

Nothing else in the frontmatter answers that question. The `type.*` tag restates the folder (Section 1.5), so a finding
taken off the internet and filed under `notes/` looks like experience the operator earned. An absent `provenance` means
unknown, and nothing guesses one for the files already in the tree.

The four routes rank in the order the table lists them, `practised`, `stated`, `researched`, `ingested`, highest first.
`stated` outranks `researched` because the operator's own claim is theirs, and the PKB knows nothing about their
conditions that they did not tell it. The ranking sits under rule 1 of Section 1.8 rather than beside it: the pillars
rank the folders, and `provenance` ranks two files inside one folder.

A session summary carries `provenance: researched` and a lesson the analysis settles carries `provenance: practised`.
Harness code stamps both, because it renders both files rather than typing them, and validation refuses a session
summary that arrives without one. No such check lands on `notes/`, because the notes already in the tree carry no
`provenance` and a presence rule there would report an error on every one of them.

The field keeps the name `researched` while the folder keeps the name `sessions/`. The folder names the producer and the
field names the route. Renaming the value to match would make the field restate the path, which is what rule 8 in
Section 1.8 already says the `type.*` tag does, and one field doing it is one too many.

## 1.5 Hierarchical Tags

Hierarchical tags improve context filtering, inheritance and agent retrieval: a nested tag states a relationship, and an
agent filters at any level of the tree.

### Tag namespaces and values

Use dot notation, and each dot adds a level, so `topic.cooking.grilling` sits under `topic.cooking`. Four namespaces
exist and nothing invents a fifth. `topic.*` and `domain.*` are open trees the operator grows a branch at a time, and
`type.*` and `status.*` are closed sets whose every value is below.

| Tag               | When to reach for it                                                                                                                     | Why it exists                                                                                                                                           |
|-------------------|------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|
| `topic.*`         | On every knowledge file, as deep as the content is specific. On a session summary, one per expert that took part.                        | A topic organizes by tag rather than by folder (Section 1.2), so the tag carries what a folder used to and reaches across topics where a folder cannot. |
| `domain.*`        | On a file whose subject cuts across topics by function rather than by area: `domain.legal.compliance` on a Cooking note about labelling. | One file sits in one folder, and a domain gathers files that share a way of working rather than a subject.                                              |
| `type.note`       | On an observation or an opinion the operator holds from their own practice.                                                              | Separates what the operator noticed from what they proved works, so a search for an answer does not return every opinion.                               |
| `type.solution`   | On a note recording something that worked and is worth reaching for again.                                                               | A defined task wants the answer first, and the tag is what ranks a solution above the notes around it.                                                  |
| `type.reference`  | On the map of an ingested source, under `references/`.                                                                                   | Marks the theoretical pillar, which loses to human content when the two disagree (Section 1.8).                                                         |
| `type.summary`    | On `topic.md`, on `notes/summary.md` and `references/summary.md`, and on a session summary.                                              | Exploration reads breadth and exploitation reads depth, and the tag is what keeps the two separable in a context pack.                                  |
| `status.draft`    | On a file the expert proposed and the operator has not answered yet.                                                                     | Un-approved text must not reach a context pack, and neither the folder nor the filename says a file is un-approved.                                     |
| `status.approved` | On every file the operator has approved, which is every file that counts as knowledge.                                                   | Approval is what gives a file standing (Section 1.7), and the tag is the record that it happened.                                                       |

### Tag rules

- Every knowledge file carries at least one `topic.*` tag, exactly one `type.*` tag and exactly one `status.*` tag.
- Start a tag with a broad namespace. Add narrower terms as the subject needs them.
- Create no ad-hoc tag. The expert proposes a new tag and the operator approves it before the expert files content that
  uses it. A tag carries what a folder used to, so an ad-hoc one loses the file rather than misfiling it.
- Keep tag depth to 4 levels or fewer.
- A nested tag implies its parent. `topic.cooking.grilling` also means `topic.cooking`.
- The expert assembles a context pack from tags, filtering by namespace and depth.
- Sessions add no namespace. A lesson the operator earned files as `type.solution`, a session summary files as
  `type.summary` with a `topic.*` tag for each expert that took part, and `provenance` carries the difference between
  the two (Section 1.4).

### Tag registry (`tags.md` at the PKB root)

Tags are flexible and relational, so the PKB keeps one `tags.md` registry at its root: the canonical tag tree and
lightweight ontology for agent ingest, holding namespace definitions, per-topic subtrees, and cross-topic mappings. Each
topic's `index.md` embeds its own subtree for local depth work.

Maintenance is mechanical. Harness code regenerates the registry, scanning the files, rendering the full hierarchy and
aggregating the cross-topic mappings from the `related_topics` declarations in file frontmatter, and it spends no LLM
tokens. The generator supplies static definitions for the standard namespaces (`type.*`, `status.*`). The registry is
derived, so it reflects the tags the files use by construction, and governance stays in the dialog rather than in the
file.

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

Every namespace renders the same way: a nested tag implies its parent, so `domain.*` is a tree as `topic.*` is, and the
generator sorts siblings case-insensitively by the full tag string, which is what makes regeneration idempotent. The
example is generator output rather than prose, and it still marks `topic.cooking.recipes` as a topic-specific extension
and still defines `status.conflict-review`: Layer 1 produces both from rules this document no longer states, and the
reconciliation pass owns the difference.

## 1.6 Human–AI Collaboration in the PKB

Every interaction is a session. The operator opens one on the Librarian or on one Topic Expert, neither by default, and
works in it for as long as the work lasts (Section 2.7). One collaboration model covers every artifact a session files:
the operator asks, the agent drafts, and the operator approves the exact text before it lands. The artifact changes only
who supplies the substance, the collaboration rule in Section 1.3 already draws that line, and Section 2.7 says when
each write lands, in the turn or at `/close`.

Three ordinary asks show the model.

The operator asks for a note. They say what they know, the expert drafts it in their `voice`, and they approve the exact
text. The expert changes no fact in a note without their approval, because the experience in it is theirs.

The operator asks to ingest a book, and asks afterwards for a note about it. The ingestion writes one reference file for
the source inside the turn, un-gated because the operator named it (Section 1.3). The note is a second ask and a
different pillar: what the operator now thinks, rather than a second account of what the book said.

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
what it found back into the session that wrote the file. Nothing else looks for a conflict: no tag records one, no queue
holds one, and no timer re-reads a pair a write already compared.

The report names both files and quotes both sides. It separates the pairs that genuinely oppose each other from the
pairs that are both true under conditions neither file states, because the two ask the operator different questions, and
it proposes no conflict type and no confidence score, because nothing stores either.

A note the analysis files after `/close` is a write like any other, so the same check runs and the operator answers it
in the learning channel (Section 2.8). A write with no session behind it gets no check at all, a `pkb_*` call from a
project agent being the one that reaches the tree today, because a report needs somebody to report to.

### What resolving one means

The operator resolves it in the session, in one of three ways. They edit one of the two files into the version that
holds. They say which file holds and why, and nothing changes, because the other file was already right. Or they say
both hold, and the note gains the conditions that separate them, which is a write and waits for their approval like any
other.

Silence is not a resolution. A session that ends with a conflict it did not settle names both files in its record and
says so, because nothing else remembers it and the next session starts on a tree that looks settled.

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
  The session's own record and summary say what that session raised, and nothing else remembers it (Section 2.7).
- It marks no note as a loser.
- It stores no resolution text outside the note, so the note content is the true state of knowledge.

### Fields this design no longer uses

`status.conflict-review`, `review_note` and `last_reviewed` have no job left. Nothing tags a file, nothing writes a note
describing an open conflict, and no review happens later for a date to record. Layer 1 still defines all three, so the
two blocks below stay as the design they served until the reconciliation pass takes them out of the schema and out of
the closed `status.*` set.

A note with a conflict open against it:

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

The same note after the review it no longer gets:

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

## 1.8 Critical Rules

1. **Human content wins**: the practical and procedural pillars outrank the theoretical one. An operator's note, a
   human-approved breadth summary and a skill take precedence over a reference. The expert detects a conflict when it
   writes and raises it in the session. The operator resolves it there. The expert changes no human content on its own.

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
   earned. A session files an accepted article under `references/` and its own synthesis under the root `sessions/`,
   stamped `provenance: researched` (Sections 1.4 and 2.7). The rule extends to ordinary turns: an expert that reached a
   tool outside the PKB on a turn may write no note on that turn, and hears that it should open a session instead.
   Nothing enforces that yet. Nothing tracks what a turn reached, so this half is designed and not built.

   A session **may** file under `notes/` the thing the operator went and tried: they cooked it, they ran it, they came
   back and said what happened. They settle that lesson in the analysis after `/close`, the expert drafts it from the
   session record of the experiments, and the operator approves it word for word. It lands tagged `type.solution` and
   stamped `provenance: practised` (Section 2.7). The line this rule draws runs between read and done, and `provenance`
   is where the tree records which side a file falls on.

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

Tier 1 schedules no conflict work. The session that made a write runs the check itself and reports into its own channel
(Section 1.7), and a hook has no channel to report into.

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
  search sub-agent per question, weigh what returns against the topic's notes and references, and draft the session
  summary and its synthesis. The questions carry the objective and none of the operator's beliefs (Section 2.7). The
  name of this skill is an identifier (Section 2.4).
- **Self-improvement** – after `/close`, read the session record from the beginning, decide whether the session
  established anything, and put what it found to the operator (Section 2.8). It calls the two drafting skills below for
  the text of each candidate. Most sessions end here with nothing.
- **Lesson proposal** – draft what the operator learned and what is worth filing, from the session record of what they
  tried (Section 2.7). The drafting is the skill's work. Harness code picks the pairs that lesson has to answer for, per
  Section 2.8, whether the expert wrote the lesson or the operator dictated it: a skill is a file the operator may adopt
  and then edit, and a guarantee that lives in an adopted copy leaves the day they edit it.
- **Skill proposal** – draft the `SKILL.md` for a way of working the session established, and say whether it belongs to
  one topic or to all of them (Section 2.8). It is a second skill rather than a second mode of lesson proposal, because
  the two answer to different tests and land under different approvals: a lesson says what is true and a skill says how
  to work.
- **Sub-topic proposals** – propose a split for a topic that has grown too large.

A Topic Expert may **overload** any of these with a topic version, so the Cooking expert's summarization skill may
require temperature and doneness tables in a recipe summary. An overload extends the common procedure and weakens no
general standard, because Tier 1 validates the output whichever skill version produced it. The same mechanism extends to
the collaboration skills of Section 2.4.

The four skills a session calls, research planning and synthesis, self-improvement, lesson proposal and skill proposal,
sit under that promise too, and they reach it by a different route. The analysis reads the session record rather than
the conversation (Section 2.8), so it cannot run as an ordinary expert turn, which is handed the conversation. The
DeepAgent harness resolves the drafting skill by name instead, the topic's own copy ahead of the root's and the shipped
one underneath, the order an expert's graph resolves in, and hands the body to the distillation. A Cooking session then
distils differently from a Trading one, and a pass that needed the conversation would have nothing to read once the
channel shut.

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

Every interaction with the PKB goes through an agent. The operator and external agents, project agents among them, read
and write no topic file directly. The agent layer and the DeepAgent harness's hooks (Section 1.9) enforce the standards
Part 1 defines, whichever channel a request arrives on.

Part 1 says the operator writes and edits a file, and both happen through this dialog too: the operator decides, and the
agent applies the change on their behalf.

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
rather than in the merged reply step 3 composes, and that is session work (Section 2.7), which is not built.

The Librarian's research skills live at the PKB root, because a cross-topic research skill is about no subject and no
topic can hold it. The root is where every process skill already lives (Section 2.4), and a cross-topic research skill
is the first one that belongs to a named agent rather than to all of them. **It has no file yet.** The shipped
`research` skill is the closest thing today and it covers one part of the work, the breadth-first pass over the tree,
and it says nothing about framing an objective or about two topics' answers interacting.

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
   them, "continue with the Cooking expert", rather than going back through the Librarian each time.

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
- **Work that crosses topics** – the operator attaches a session to the Librarian when the objective crosses topics
  (Section 2.7). Personal finance and investment cross portfolio management and trading, and neither expert holds the
  whole of it. An objective fans out like any other inbound item, and the analysis after `/close` fans out the same way
  with the session itself as the source, each expert filing inside its own topic or filing nothing. The synthesis lands
  in the root `sessions/` folder where every session summary lands, written by a gated tool so the Librarian still
  writes nothing.

## 2.3 Topic Experts

A **Topic Expert** runs each topic. One default **Topic Expert template** serves the whole PKB. A topic that needs
behavior beyond skill overloads overrides the template with an `expert.md` in its topic root. The DeepAgent harness
resolves this on the pattern the maintenance skills use: take `[Topic Root]/expert.md` when it exists, and otherwise
instantiate the PKB template with the topic's context, `topic.md`, `index.md`, the common skills, and any skill
overload. The resolution recurses, so a parent topic's expert serves a sub-topic that holds no `expert.md`.

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
- Escalate to the operator as Part 1 requires: summary approval, new tags, and conflict resolution.

An expert writes inside its own topic, and its session summary is the one file that lands outside it: every summary
lands in the root `sessions/` folder (Section 1.2), so the expert drafts the text, the operator approves it, and a gated
tool performs the write. That tool does not exist yet, and the root `skills/` folder waits on it too (Section 2.4).

A source too large for one turn is ingested as a loop. Classify, draft, file works for a link and fails for a book,
because nobody reads what does not fit the context window and one turn writes a confident account of the part it saw. So
harness code drives the reading: it segments the source, extracts argument by argument through a bounded window, writes
each section as it goes, records what it skipped and why, and survives a run that dies part way through a 300-page book.
The expert stays the author of the extraction and stops deciding when it is finished. A source arrives as a path, and
anything binary is extracted to text first, with the PKB keeping both.

### Example: a Cooking Topic Expert in action

The operator connects to the Cooking Topic Expert. They need no external tool, because the expert handles retrieval,
dialog and filing end to end.

- **Ingest from the web**: the operator asks for a steak grilling recipe. The expert fetches candidates, works with the
  operator on the rub and the target doneness, and files the final version under `notes/`, tagged
  `topic.cooking.recipes`.
- **Capture experience**: the operator reports back after cooking, "the grill behaves differently in windy weather". The
  expert files that as a note and proposes a regenerated `notes/summary.md` for approval.
- **Combine reference and experience**: the operator asks for a grilling recipe from an ingested cookbook. The expert
  pulls it from `references/` and applies the temperatures the operator filed for their own gas grill.
- **Ingest through its own lens**: the Librarian fans a food-science book out to Cooking and to Health. Cooking files
  what it says about heat, protein and technique. Health files what it says about nutrition.
- **Search for what the topic cannot answer**: the operator asks how long to dry-brine a brisket, and the topic holds no
  reference on it. The expert says so, sends three search sub-agents out with one question each, verifies the pages they
  cite, flags the two results that contradict the operator's own note, and offers one article for ingestion. The session
  stays open, because the operator has cooked nothing yet.
- **Work one objective over weeks**: the operator opens a session called `Cooking · Brisket Rub` and cooks three times,
  reporting back after each. The expert holds the experiments in the session record, contradicts the operator when their
  week-three conclusion disagrees with their own week-one report, and proposes one note in the learning channel once the
  session closes. Their other session, `Cooking · Sourdough Starter`, stays untouched.
- **Leave nothing behind**: the operator asks for a weeknight pasta, gets one, cooks it, and closes the session. The
  analysis reads the record, finds a request and an answer and no experience, and files nothing. No note, no summary, no
  folder. This is the ordinary outcome, and the closed channel still holds the recipe.

## 2.4 Common Skills and Skill Overloading

This section is the procedural pillar.

Every Topic Expert loads the common skills. They ship with the implementation and mount ahead of the PKB root's own
`skills/` folder, which starts empty (Part 3). The mount is read-only because it lives inside the installed package: a
write there edits the implementation for every PKB on the machine, so the permission layer denies it to every agent. The
tree's own `skills/` folders take writes, and a skill the operator adopts, writes or approves after a session lands in
one of them (Section 2.8).

The two homes differ in reach, and today they differ in what can reach them. A topic's `skills/` folder sits inside that
expert's own subtree, so the expert already writes there and the gate stops the write for the operator. The root's
folder sits outside every expert's subtree, where the catch-all deny refuses it, and no tool routes a write there, so
filling the root folder needs a gated tool that does not exist yet. The root `sessions/` folder waits on the same tool,
because every session summary lands there and no expert reaches it either (Section 2.7).

Each skill is a folder holding a `SKILL.md`, so `skills/voice/SKILL.md` and `skills/discovery/SKILL.md`. That is the
DeepAgent harness's own format, and it buys two things without code of ours: progressive disclosure, where the prompt
holds the skill's name and description and the harness opens the body when a turn needs it, and override resolution by
name collision. Anything else the skill needs sits beside its `SKILL.md`.

### The ten skills that ship

A shipped skill is a starter draft: it makes something sensible happen on day one, and it says so at the bottom of its
own text. They sort by the pillar each one serves, seven pointed at the operator's subject and three at the procedural
pillar. Section 1.9 calls those three the collaboration skills and the other seven the judgment maintenance skills.

A skill name is an identifier rather than a description. `research`, `researched` and *research planning and synthesis*
are names, and none of them carries the word's ordinary sense here. This document uses *research* for the Librarian's
breadth-first work across topics, and *search* for reaching the internet.

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
- **`tag-proposal`** proposes a tag the PKB has never used by writing the file that needs it and letting the gate hold
  the write, so the operator sees the tag and the file it would create in one decision.
- **`sub-topic-proposal`** proposes a split once `notes/summary.md` has stopped distilling and started enumerating. It
  brings the evidence and the full file assignment, and it creates nothing.

**Serving the procedural pillar: how the operator and the agent work together.**

- **`research`** explores breadth-first across the PKB and returns three to five options, each with its trade-off and
  the files behind it. Finding two files that disagree on the question, it says so and escalates rather than picking the
  reading that suits the answer. It is the breadth-first pass over the tree and no more, so it is not the Librarian's
  cross-topic research skill, which has no file yet.
- **`discovery`** runs a brainstorming session against PKB content. It names the tension between two notes and the gap a
  breadth summary keeps implying, pushes back, and files nothing. Anything worth keeping goes back through the front
  door as ordinary ingestion.
- **`voice`** carries the voice profile every draft is written in, narrowed per topic when one subject wants a different
  register from another. The operator corrects it from their own edits, and a change to the profile pauses for them like
  any other. It is the one shipped skill that knows something about the operator rather than about cooking, and Section
  2.8 opens the rest of the pillar to a session's own proposals.

Six of the skills Part 1 describes have no file yet. Two are source extractions, for an article, post or clip and for a
manual or reference work, and `ingestion-classification` files both until their own skills exist. The other four sit on
the session side, *research planning and synthesis*, *self-improvement*, *lesson proposal* and *skill proposal*, and
they are being written alongside the session work that calls them. All four mount and overload like the ten above,
resolved by name for the distillation call rather than by an expert's graph (Section 1.9). Part 2 owes a seventh that
Part 1 never names, the Librarian's *cross-topic research* skill (Section 2.2), and it mounts at the root like the rest.

Skills sit on the same side of the collaboration rule as notes, **human-generated, AI-curated** (Section 1.3): the
operator writes or approves every one of them, whoever typed the draft. A `SKILL.md` is no knowledge file (Section 1.4),
and its `name` must match its folder name, for the reason *Where a skill lives* gives below.

### Where a skill lives

The procedural pillar has two homes and the tree resolves both by name. This is the one place the rule is stated, and
every other section cites it.

- **A skill about one subject** lives in that topic's `skills/` folder, visible to that topic's expert and to nobody
  else.
- **A skill about how to work** is a process skill and lives in the PKB root's `skills/` folder, where every expert
  loads it, and the Librarian with them. The Librarian's cross-topic research skills live here for that reason: they are
  about no subject, so no topic can hold them (Section 2.2).

Changing a shipped skill uses the same two homes. **Adopting** it copies it to the root, where every expert loads the
copy from then on. **Overloading** it copies it into one topic, where that topic's expert loads the copy and the other
experts keep the shipped default. Both shadow by name, and the permanent-fork warning attaches to the name.

Resolution reads the shipped mount first, then the root folder, then the topic's, and the most specific entry wins
whole-record: a topic's `voice/SKILL.md` *replaces* the root one for that topic rather than merging with it, the pattern
the DeepAgent harness applies to `expert.md`. An overload extends the default with domain intelligence, a recipe-writing
voice for Cooking or a tasting-session discovery skill, and it redefines no general standard, because Tier 1 validates
the output whichever skill version produced it.

The name decides whether a file forks anything, and the name that decides is the `name` in the file's own frontmatter.
The DeepAgent harness reads the three skill locations in order and keeps the last skill declaring a given name, so
`skills/my-research/SKILL.md` declaring `name: research` shadows the shipped `research` from the moment it lands (Part
3), while `skills/research/SKILL.md` declaring `name: my-research` shadows nothing. Both spellings look right in a
directory listing and the harness only logs a warning, so a Layer 2 diagnostic reports the mismatch. It warns rather
than refusing, and nothing calls it yet. An analysis proposal that would shadow a shipped skill says so in its approval
(Section 2.8).

Re-read a skill as the work moves on, rather than writing it once. A procedure hardens around the conditions somebody
wrote it in, and those conditions move: the tool that failed gets fixed, the operator changes how they want to be argued
with, the topic grows past the shape the skill assumed. A skill goes stale by failing when somebody uses it, so the
evidence sits in the session record rather than in a knowledge file, and session learning holds the one route: a session
proposes a revision to a skill a session wrote (Section 2.8). The conflict check reads no skill, because a skill states
no claim and contradicts nothing (Section 1.7), so the operator is the only reader of a skill they wrote or adopted
themselves.

## 2.5 DeepAgent Harness and Access Channels

The agent layer runs on the **DeepAgent** harness. It hosts the Librarian and the Topic Experts and exposes them through
several channels:

- A dedicated TUI
- Telegram channels
- Other channels as needed: chat apps, APIs, and the rest

The operator states an objective to the Librarian or to one Topic Expert. Neither is a default, and Section 2.7 says how
they choose. Step 4 of the Librarian's workflow joins the two: every expert the Librarian consults is addressable in its
own right, so a reply saying *"the Cooking expert says…"* is also an offer to carry on with that expert.

A session is the unit of work and a channel is the surface it runs on, one session to one channel (Section 2.7).
`/close` locks the channel, so what the analysis proposes afterwards has nowhere to appear but the learning channel.

That is the one channel carrying no session. The **learning channel** (Section 2.8) binds to no topic, no objective and
no expert, and it holds every approval that has no channel of its own. `/pending` lists what waits there the way it
lists everything else.

The command set settles at six commands: `/channels`, `/threads`, `/agents`, `/pending`, `/cancel` and `/close`.
`/threads` lists the open sessions. The session design adds `/close` alone, and it does not ship yet: the Telegram
adapter ships the other five plus `/new` (`docs/how-to/telegram.md`). `/new` goes, because it rotates the conversation
inside a channel and one channel carries one session, so rotating splits one line of work in half and leaves both halves
named for the same objective. A new objective opens a new session.

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
└──────────────────────────────────────────────────────────────────────┘
```

## 2.7 Sessions

A session is how anyone works with the PKB in dialog, and it is how the operator reaches a topic's knowledge at all (The
Three Pillars, above). It runs in one channel, held open on one agent for one objective, for as long as that work lasts.
A session covers the objective the topic cannot meet, the experiments that follow, and the lesson the operator and the
expert settle at the end. A capture is one turn inside it and files what the operator already knew: they dictate a note
or name a source, the write lands in that turn un-gated, and nothing about it waits for `/close`, because that
instruction is the approval. Sessions are where the operator finds things out, and where most notes come from.

A search is one of the things a session does, rather than a kind of session. A session may discuss, argue about a
design, ask a question and take the answer, search the internet, or try things for weeks and report back, in any order.
The PKB holds one shape for all of them, and a session that searches nothing is an ordinary session.

**None of this section is built.** The implementation holds no `/close`, no analysis, no search sub-agent, no session
record and no session summary, and the schema holds no `provenance` (Section 1.4). Everything below states the design,
in the present tense the rest of the document uses. Section 2.8 marks what the design has left unsolved.

### A session runs in one channel

Work begins as a question and becomes whatever it becomes, an afternoon or four months of it: reading, then experiments,
then a verdict, or none of those. The PKB asks the operator to declare nothing in advance about what a conversation will
turn into, because they do not know yet and a wrong declaration is one more thing to maintain.

One expert holds several sessions at once, each named for its objective, so `Trading · Trend Signal` and
`Trading · Market Regime` run side by side, for two reasons, the second the stronger:

- On the operator's side, separate lines of work are separate places on the phone, and a phone is where this work
  happens.
- On the model's side, each session keeps its context on its own objective. A conversation replays its whole history on
  every turn until harness code compacts it. Two objectives in one conversation means every trend-signal turn re-reads
  the regime work and takes an invitation to blend the two.

The channel title names the agent and the objective, so a deliberate split is safe where an accidental one is not.

### The counterpart may be an agent

A project agent opens a session the way a person does, and for the same reasons: it needs knowledge the context packs of
Part 4 do not carry, it wants a question searched, or it implements something the topic holds the conditions for.
Everything in this section holds unchanged, and the dialog is the whole of the difference from Part 4's batch route.

One thing does differ, and it is now settled. Every approval in this document is the operator reading exact bytes, and
an agent counterpart puts none in front of them. So an unsupervised session leaves its proposals for a person, on the
route every session takes: `/close`, the learning queue, the learning channel, `/pending` (Section 2.8). It needs no
route of its own. One question stays open: whether a project agent may ever hold an approval, and over which writes.

### A session attaches to the Librarian or to a Topic Expert

A session pursues an objective the operator set, from grilling dinner to a new trading strategy. It attaches to the
Librarian or to one Topic Expert, neither is a default, and the operator chooses at the start.

Attach to a Topic Expert when one topic owns the objective. Grilling dinner is Cooking: one expert, one subtree, and
nothing else needs to happen.

Attach to the Librarian when the objective crosses topics. A new trading strategy crosses portfolio management and
trading, and neither expert holds the whole of it. The Librarian frames the objective, fans every turn out to the
applicable experts, and merges what they return by attribution (Section 2.2). Framing the objective and naming the
topics that bear on it is a competence of its own, and it is the Librarian's.

The Librarian still writes nothing, and each expert still writes inside its own topic, reaching the root `sessions/`
folder for its own summary through the gated tool a Librarian session uses for the same file (Section 2.3). A session
reaches outside the topic's three pillars through search sub-agents, and a page a search returns ranks below everything
the topic already holds until the operator accepts it (Section 1.7).

A session that opened on one expert and turns out to cross topics re-opens on the Librarian. Nothing copies a session
record from one session to another, so the operator names the objective again and the new session starts its own record.

### The expert argues with the operator, and about the operator's own conclusions

The operator works with the expert the way they would work with a human expert. A good expert objects during the work
rather than in a retrospective. The operator says the rub needs more sugar, and if their own note from March says sugar
burned at that temperature, the expert says so while there is time to change the rub. The same holds when the analysis
runs, where **the operator can be wrong**. Told during the session to file *sugar burns above 250*, an expert that reads
experiment two at 260 without burning says so beside the candidate it drafts. It then files what the operator decides,
because meaning is theirs (Sections 1.6 and 1.7).

### One command

`/close`, when the work is done. It is the session's only command, and it opens the only door into the tree. A search
reports back into the conversation and files nothing, experiments file nothing, and a note, a skill and a session
summary all wait for `/close`. It does three things:

1. It marks the session record closed and keeps it, because a source this session turned down should not appear again in
   the next session on the same objective (*The session record is the durable file*, below).
2. It locks the channel, and nothing more is said in it.
3. It puts the session in the learning queue, when the session produced something to read: an experiment, a source, a
   lesson the two of them argued about. A session that asked one question and took the answer enters no queue, because
   the analysis would have nothing to read and a queue of *what does my note say about brisket* is a queue nobody reads.
   The analysis is never synchronous with the command: the worker reads the record from the beginning when it reaches
   the entry, and what the session established parks in the learning channel (Section 2.5). The channel this session ran
   in stays shut, so the conversation about what was learned happens there. Section 2.8 runs that cycle and bounds what
   it may conclude.

Filing nothing is the ordinary outcome of a session that does queue, and the closed record and the closed channel keep
that work readable outside the tree while it claims nothing inside it. Rule 4 in Section 1.8 rules the same way for
ingestion.

Waiting for the close costs nothing. The record holds every experiment as it happened, so week two is still there in
week twelve and the analysis reads the record whole. An operator who has learned the thing they came for has met their
objective, and a met objective is a session to close.

Nothing brings the operator back to a session they left open, because returning is theirs to do and the open channel on
the phone is their only reminder.

### The loop is: try it, report back, distil

Search for a rub and a smoking approach, cook, come back with how it tasted, cook again, and distil after the third
attempt. Search for a trend-following signal, run several variants, watch which one holds and under what setup, and
distil once the data arrives. Feedback lands in pieces, from wherever the operator is, over weeks. The loop is where
`notes/` gets made, and where the procedural pillar picks up what the two of them worked out (Section 2.8).

### A session searches in seven steps

A session searches as often as the work needs. Each search runs as harness-encoded steps for the reason routing does
(Section 2.2): the expert's judgment sets the questions and weighs the answers, and code runs the search and the
checking.

1. **Take the objective.** The operator says what they want to know, and the expert writes the objective into the
   session's own summary file (below). A session holds one objective, so a later search joins the file this session
   already opened, and a new objective opens a new session.
2. **Survey the topic.** This is the exploration stage. The expert reads `topic.md`, both breadth summaries, and the
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
   fetches nothing itself (*Code verifies every quotation*, below, carries the measurements). It holds a URL it has no
   text for, and a quotation that text does not contain, and both land under their own heading in the session record
   with the reason they failed.
6. **Weigh.** The expert compares what survived verification against the topic's notes and references, claim by claim,
   and says which disagreements genuinely oppose each other and which are both true under conditions neither side
   states.
7. **Report back.** The expert brings what survived, with the evidence behind it, and the conversation carries on. The
   operator may name a source worth ingesting on the spot. Everything else waits for `/close`, because the search found
   things and the operator has tried none of them yet.

```
OPERATOR or PROJECT AGENT ── opens ──▶ SESSION, in one channel
                        │              one objective, named for it, attached
                        │              to the Librarian when it crosses topics,
                        ▼              or to one expert when one topic owns it
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
   /close  marks the record closed, locks the channel, and puts the
           session in the learning queue. The analysis reads the record
           from the beginning and parks each candidate as an approval
           that quotes the record; then the operator approves the bytes
                              │
                              ▼
   notes/ · root sessions/ · skills/ · references/ · nothing at all, the common outcome
   (What a session may file, below, gives the gate on each)
```

### A session writes instruction sets and executes nothing

Work turns up things to do: run this backtest over these three regimes, cook this at 250 for four hours. An objective
that needs a new tool or a process to follow makes a session write one or more **instruction sets**. An instruction set
states why the work is necessary and what it must achieve. It does not specify an implementation. One session may write
several, for separate experiments or for separate tools.

The operator follows the instruction set, or another agentic system implements it: the Project Manager (Part 4), a
coding tool, or the operator with a smoker. The operator reports the results of each experiment or tool back into the
session as conversation, and the session uses them to advance the objective. An instruction set stays a message until
the operator says it is worth keeping, and it then lands inside the session summary in the root `sessions/`. The PKB
grows no task queue, no runner and no status field, because a knowledge base that executes has to remember what it is
halfway through, and that is a second record of the work that can disagree with the first.

### The session record is the durable file

A session keeps a **session record** and writes into it as the work happens: the objective, each experiment and what it
produced, the sources kept and the ones turned down. The record lives in the session's workspace, outside the tree, and
it survives a restart of the daemon. `/close` marks it closed and keeps it, so the next session on the same objective
reads what this one declined. *The session summary* below is the separate thing the operator approves into the tree, and
the operator reads the record by asking the expert for it.

The reason is measured, and Section 2.8 spends it. The large-source ingestion loop settled this shape already:
*"There is deliberately no second store of progress: a second source of truth about what was read is a second thing that
can be wrong, and the one a human can check is the file."*

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
model, at a fraction of the speed. The channel stays open through all of it, and the next search runs the first time the
provider answers, so nothing polls and nothing queues.

### Code verifies every quotation

Published deep-research agents invent 3% to 13% of the URLs they cite, and 5% to 18% more of the URLs they give do not
resolve. In one shipped generative search product, 51.5% of the sentences it wrote were fully supported by the citation
attached to them. So no URL reaches a session summary on a model's word:

- Harness code holds the text of every cited page. Search hands the page's content back with the result, and a sub-agent
  that wants a page beyond what its search returned reads it while it is still searching, so the text joins the same
  session record. Verification fetches nothing itself. A citation the record holds no text for keeps its claim out of
  the synthesis, and the record says why the claim carries no weight.
- Harness code locates every quotation in that held text. It drops a quotation the text does not contain, and the record
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
in Section 1.8, quotation verification in code, and the harness-rendered approval.

### The budget bounds quality, and cost is not the reason

A long run is a worse run. Factual accuracy on one measured search agent fell from 79% to 17% as its tool calls rose
from 2 to 150. Between 77% and 94% of the steps in a long search add no new evidence, and a run that reaches the wrong
answer runs two to three times longer than one that reaches the right answer. Length is a symptom before it is a cost.

A single search carries a step budget and a wall-clock budget, and the session carries neither. Exhausting either budget
stops that search, and the expert says it stopped short of the objective. The operator can act on that: they say chase
it again with a narrower question, and the channel is still open for them to say it in.

### The session summary

A session that worked out enough to be worth summarizing produces one file, `sessions/[objective-title].md` in the PKB
root, whichever agent the session attached to. It follows the folder-hosted convention of Section 1.2 when it needs
media beside it. It is a knowledge file (Section 1.4) with `source_type: summary` and the tag `type.summary`. It carries
`provenance: researched`, the field that keeps it distinguishable from a note the operator earned. The tag namespaces of
Section 1.5 do not grow for sessions, and the `topic.*` tags do a job no folder can: one per expert that took part, so
the file names who answered and the analysis that generalizes over sessions can find its material (Section 1.4).

The session summary holds, in order: the objective, the questions the session asked, every source it kept, every source
it rejected and why, the claims verification held back, the conflicts it raised against the topic's notes, and the
synthesis. A session that searched nothing fills the source sections with nothing and keeps the objective and the
synthesis, because a discussion that reasoned from what the operator already holds and reached a conclusion reached one.

One shape is refused, and it is narrow. A session that searched, admitted nothing past verification, and then wrote a
confident synthesis anyway summarized a page it never read, so that filing is refused with the empty findings list
quoted back. A session that never searched is a different thing and files as usual.

A session that read one page, cooked from it, and learned one thing writes a note and no session summary. The session
summary holds what the session worked out, and the note holds what the operator did.

The session summary is append-only. A turn adds to the end and nothing rewrites an earlier entry, because a model asked
to revise a long report across turns removes correct material without saying so and introduces errors while it polishes.
A correction is a new entry naming what it corrects.

A rejection reaches the tree through this file alone. A candidate the operator turns down leaves no folder under
`references/`, no stub, and no copy, per rule 4 in Section 1.8. The reason is theirs, recorded in the words they typed.
Until a session summary lands, the rejection lives in the session record, which survives `/close` closed, so a session
that files no summary still tells the next session on that objective what it declined. A later session that finds the
same page shows it labeled with the date and the reason, at the bottom, rather than hiding it: the page they turned down
for one question may be the page they want for the next, and a result dropped in silence looks like a result never
found.

Candidates live with the session rather than in the tree. A page the search returned and the operator has not accepted
stays with the session: the text goes when the search that found it ends, and its line in the record stays with the
record. Nothing stages it, copies it or writes it under the PKB root. `.inbox/` is where an accepted source stages on
its way through ordinary ingestion, and nothing else puts anything there. The cost is small, because harness code
fetches a page the operator accepts a second time. The alternative was thirty browsed candidates leaving thirty
permanent folders in a tree with no undo, in a staging area no channel can list.

### What a session may file

| Outcome                                         | Where it lands                                                                                                                 | What gates it                                                                                                                                        |
|-------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| Something the operator tried, and what happened | `notes/`, stamped `provenance: practised`, tagged `type.solution` when it worked and `type.note` when it did not               | Settled in the analysis after `/close`; the operator then approves the rendered text                                                                 |
| The session's synthesis of what it worked out   | The PKB root's `sessions/[objective-title].md`; tagged `type.summary`, stamped `provenance: researched`                        | The operator approves the rendered text in the analysis, before it lands                                                                             |
| A way of working the session established        | `skills/[skill-name]/SKILL.md`, in the topic's folder or in the root's (Section 2.8)                                           | The operator approves the rendered text, on an approval that names the scope and any shipped skill it would shadow                                   |
| An article the operator accepts                 | `references/[source-name]/`, through the ordinary ingestion procedure (Section 2.3), with the topic's own copy of the original | The operator names the candidate; harness code will only ingest a page it printed for them and fetched itself. The first extraction is then un-gated |
| A candidate the operator rejects                | The rejection list inside the session summary; the session record holds it either way, open or closed                          | Nothing, and no other file changes                                                                                                                   |
| Nothing at all                                  | Nowhere: no note, no summary, no folder                                                                                        | The analysis found nothing to distil, and this is the common outcome. Filing nothing is the bar working rather than failing (Section 2.8)            |

Rule 8 in Section 1.8 is the line this table draws. A session files everything it read as a reference or as a synthesis,
and everything the operator did as a note, in their own words, after they did it, and `provenance` records which of the
two a file is. The skill row carries none, because Section 1.4 exempts that file class.

### Direction is conversation; the write is the approval

The operator steers a running session by talking to the expert: drop that question, chase this one instead, that source
is no good, that lesson is half right. An approval halts the conversation until somebody answers it, so spending one on
"those three look right, keep going, but drop the second" would stop the session to ask what it could have heard in
passing. An approval also accepts the answers it offered and no others, and that sentence is not one. So nothing inside
a running session is an approval, and a conflict the write-time check reports is direction like the rest: it halts
nothing, and the write it belongs to still waits on its own approval (Section 1.7).

The analysis is the other case. It runs after the close, with no conversation left to steer, so it proposes as an
approval on the exact rendered bytes (Section 2.8). The operator approves what enters the PKB. A skill asks on its own
terms, because the operator agrees to something different there. Accepting a source for ingestion is an instruction
rather than an approval, and harness code ingests a page it printed for the operator and fetched itself, and no other.

### The analysis of a Librarian session fans out

On a Librarian session, the analysis treats the session itself as a source and fans it out. It asks each applicable
expert what its own topic takes from it, with the grammar the ingestion loop already uses section by section: something
new, something better, something that contradicts what I hold, or nothing. An expert that takes nothing leaves no folder
and no stub. Each note lands inside its own topic, so the Librarian still writes nothing.

A Librarian session's analysis therefore proposes a set of notes. Each one asks for its own approval on its own text, so
the operator takes some and drops others and a rejection on one changes nothing about the rest. Four kept notes means
four texts to read.

A session that yields a portfolio lesson and a trading lesson yielded two lessons, the shape one book reaching two
topics takes. An insight that spans the topics rather than decomposing across them lands in one topic with
`related_topics` naming the others, and the hooks aggregate that into the root registry (Section 1.9).

A Librarian session files one session summary in the root `sessions/` folder like every other session, and it files one
rather than several because splitting the crossing into per-topic accounts loses the thing worth keeping. It carries the
frontmatter Section 1.4 gives it, and one `topic.*` tag per expert that answered. Harness code writes it on
the route a root process skill takes: the expert that ran the session drafts the text, the operator approves the exact
bytes, and a gated tool performs the write, so the Librarian still writes nothing (Section 2.8).

Layer 1 grows with it. The root walk learns one more folder beside `index.md`, `tags.md` and the root `skills/`, and the
files inside it are ordinary knowledge files, validated as such and tagged into the root registry. No context pack
reaches them, because a pack is a topic's own files and a session summary belongs to no topic, and Section 2.8 marks
that as a gap under *How an entry improves an expert*. The write-time check does not run on one either, because Section
1.7 puts the check on a note or a reference. A root process skill is the one other thing that analysis may propose, and
a cross-topic research skill is the kind it proposes most naturally: the Librarian's own competence has no topic to live
in (Sections 2.2 and 2.4).

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

One mechanism reasons over what the PKB holds, and one trigger starts it: a session closed. It runs in one worker, draws
from one queue, and ends with a proposal the operator answers in the learning channel. The mechanism distils a
**lesson**: practical knowledge, which a note carries, or a repeatable skill, which a `SKILL.md` carries. A session
summary is different, because it records what the session worked out and the analysis distils a lesson from it. The
self-improvement skill holds that competence (Section 1.9), and no command does.

### The one trigger

`/close` puts a session that produced something in the learning queue (Section 2.7), and the analysis reads the session
record from the beginning and checks each candidate against the tree (*The analysis cycle* below).

Time starts nothing. Conflict detection runs on the write, inside the session that made it (Section 1.7), so a timer
would re-read pairs a write already compared. Three cases lose their only catcher with it: two notes filed months apart,
where the earlier one predates this design and no write has touched it since, the operator's own direct edits, which no
agent sees, and truth that changes with no write at all, where a reference is superseded and nothing moved. A check the
operator asks for over a topic is the one route left to those, and that route works today.

### What the check compares

The Topic Expert compares on four axes, all of them knowledge against knowledge, and axes 1 to 3 run today.

1. `notes/summary.md` against `references/summary.md`.
2. Single notes against references.
3. Notes against notes, the same person at different times under conditions they did not write down.
4. References against references, two sources contradicting each other with no human side to decide them, and on a
   re-ingestion the fresh extraction of a source against that source's file on disk, argument by argument, because a
   bounded reader handed two long documents answers confidently about the part it read. Designed, not built.

The check reads for meaning with the expert's domain knowledge behind it, and it recognizes two statements that are both
true under different conditions. Section 1.7 says what a finding does: it goes back into the session that made the
write, and nothing in the tree records it. A finding on a write the analysis makes after `/close` reaches the operator
as a proposal in the learning channel instead, because that session's own channel is shut.

Harness code picks the pairs and the model labels them. The design takes the choice of pairs away from the model: code
picks them by claim-to-claim overlap, the model labels each one, and every pair the code picked reaches the operator
whatever the label says. **That code does not exist.** Today the expert compares whole files under the
`conflict-detection` skill, and Section 1.7's measurement is the risk the design carries until somebody builds it.

### The analysis cycle

The analysis walks all six steps.

1. **Read the record from the beginning.** The analysis reads the whole session record, first turn to last, and never a
   previous extraction. This is why it runs once and at the close: one-shot consolidation beats streaming, chained
   abstractions compound, and a distillation of a distillation carries an error nobody can trace back to the experiment
   that started it. A record too long for one turn walks through the bounded reader of Section 2.3 and comes back as one
   consolidated account, read beside the record.
2. **Draft the candidates.** The self-improvement skill drafts each kind with Section 1.9's drafting skill for it,
   resolved topic-copy first so the topic's overloads apply. It proposes what the session learned and what is worth
   filing. The record holds the operator's own words wherever they dictated a lesson during the session, and the draft
   carries them.
3. **Park each candidate as an approval.** The session is closed and its channel with it, so a candidate arrives as an
   approval rather than as a message (Section 2.7). It quotes the record: the objective, the close date, how long ago
   that was, and the log entries the candidate rests on, sliced out of the record by code rather than summarized. An
   operator reviewing a session they no longer remember needs the evidence in front of them.
4. **Pair it against what the topic holds.** Harness code selects the notes a candidate has to answer for, and Section
   1.7 governs one that contradicts a note the topic already holds: quote both sides, change nothing, let the operator
   settle it. The expert's objection arrives with the candidate rather than after it, because no conversation is left to
   raise it in.
5. **Approve the bytes.** Harness code renders each file that would land and the operator reads the exact text. Three
   candidates ask three times, and the operator may take one and drop two. An approval accepts the answers it offered
   and no others, so a candidate that is half right is rejected whole, and the route back is a new session on the same
   objective, which reads the closed record.
6. **Write, then record what landed.** The files land, the hooks regenerate the indexes and the registry (Section 1.9),
   and the session record notes each lesson beside the path it landed at.

One pass per session is enough, and the record is why. A session that runs for months compacts its early experiments out
of the conversation, so by week twelve the record holds the evidence and the transcript does not. A second pass over the
same experiments would file the lesson twice, and two near-identical solution notes in one topic both reach every
implementation pack and then drift apart, the harm rule 4 in Section 1.8 exists to prevent.

### The default is silence

Nous Research shipped **Hermes Agent** in February 2026, and it is the nearest shipped system to this one:
agent-curated memory about the human, plus skills the agent writes for itself after hard tasks. Its prompt states its
prior in capital letters: *"Be ACTIVE. Most sessions produce at least one skill update, even if small. A pass that does
nothing is a missed learning opportunity, not a neutral outcome."*

That prior is right for Hermes and wrong here, and the substrate is the reason. Hermes writes into a skill library with
an archive and a rollback, so somebody reverts a bad write. This design writes into a tree with no undo, where a bad
note reaches every implementation pack on its topic and stays until the operator rewrites it by hand. So the prior
inverts. The analysis runs on every session that queues and the filing does not, and a session that files nothing has
missed nothing. Hermes fires its review on accumulated tool iterations rather than at the end of a session, so its
cadence is no argument for this one either.

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
files what it read as a reference or a session summary, and what the operator did as a note.

### What a session may conclude

A session authors four outcomes, and the table in Section 2.7 gives the gate on each: a note, a session summary, a
skill, or nothing at all. That table carries two more rows a session does not author, an accepted article and a rejected
candidate. The bar on the note is three conditions and each one carries load: the operator did the thing, they came back
and said what happened, and they approved the exact text that lands. An expert holding the first two alone holds an
experiment, and it argues about what the experience means and never about whose it was (Section 1.3).

### How an entry improves an expert

Each entry improves the expert agents, and each kind reaches an expert by its own route:

- A note reaches `notes/summary.md` through the `summarization` skill, and a reference reaches `references/summary.md`
  through the same skill. Every context pack carries both, an implementation pack loads `notes/summary.md` first, and a
  reference's depth file reaches that pack too (Part 4). The expert reads the distilled rule on every turn that touches
  the topic.
- A skill loads into the expert's prompt at the start of the turn, so it shapes the next draft before anybody asks a
  question (*A session may also teach the system how to work*, below).

A session summary reaches no expert yet, and that is a gap. It belongs to no topic, and a pack is a topic's own files
(Section 2.7), so the ordering Part 4 describes for it is designed and not built. The three gaps below are the others.

### The learning queue holds work; the learning channel holds proposals

A session that produced something enters the learning queue at `/close` (Section 2.7). The queue is how a session ends
rather than a place a session lands when something else failed. `/close` queues the session whoever was in the channel,
so one the operator ran and one an agent ran alone take the same path and the same approval. The worker drains the queue
when it reaches the entry, and the proposal reaches the operator when the operator is available.

The bar runs inside the analysis rather than in front of the queue, so the queue holds work and the learning channel
holds what cleared the bar. A session that establishes nothing is analyzed like any other and leaves the channel as it
found it. Nothing about the bar moves with the channel the session ran in: the same five exclusions, the same three
conditions on a note, the same rendered bytes, and everything the analysis derives waits for the operator.

The split between the two is measured. Roughly one in seven self-evolution candidates establishes anything, and a filing
rate above about one in five is evidence that the bar is broken rather than generous. Put every closed session in front
of the operator instead and the review list is mostly nothing, so the operator stops reading it and the one that
mattered goes unread with the rest. The literature on lessons-learned databases reports that failure and agrees about
the cause. A record with no experiment in it ends the analysis at once, because it holds a request and an answer and no
experience to distil, and that predicate decides how long the analysis takes rather than whether it happens.

A proposal lands in a learning channel rather than in a topic. The operator's first thought was a special topic
`kb-learning`, and they ruled against it. A topic is an expert, a `topic.md`, three pillar folders, an agent id, an
entry in the catalog the Librarian routes on (Section 2.2), and a write confinement drawn around its own subtree, and a
list of pending proposals is none of those. Making it a topic produces an expert nobody wants to talk to, a routing
target nobody should route to, and folders that hold nothing.

The channel itself needs nothing new. A proposal is an approval, and an approval already parks durably, survives a
restart of the daemon, answers hours later from a different channel than the one that raised it, and appears under
`/pending` (Section 2.5). The missing piece is a home for an approval with no live session behind it, and the analysis
has none, because it runs after `/close` shut the channel. The learning channel is that home, a surface bound to no
topic and no objective, and the TUI gets it for free because it already carries an unfiltered *needs you* view.

The schedule takes most of the staleness out. `/close` queues the session the moment the work ends, rather than leaving
a closed record for a later sweep to find. The queue can still lag, so two things guard the far end. The proposal quotes
the record rather than asserting a conclusion (step 3 above), and an entry that has waited too long is discarded rather
than run, with the skip written into the record. Nobody has settled how long is too long.

Two things about the channel are not settled. The first is whether it binds to the Librarian or to no agent at all: a
proposal already names the topic it would write to, so the channel may need no agent behind it. The second is whether
the queue needs a cap, because an agent working overnight can pile up proposals nobody has seen.

### The skills that already generalize

Two shipped skills (Section 2.4) generalize outside the analysis, and neither files anything itself. `discovery`
finds the rule under two notes that never mention each other, so the finding goes back through the front door with its
own approval. `voice` watches for the same edit repeated across three drafts and proposes it as a rule, which is the
procedural pillar generalizing from experience about the operator. `summarization` and `conflict-detection` do the work
already described above, in the four axes and in *How an entry improves an expert*.

Section 1.9's self-improvement skill runs the analysis and its two drafting skills do the writing, and all three are
being written (Section 2.4). The checks a draft has to answer for stay in harness code, where the pair picker sits: a
skill is a file the operator may adopt and then edit, and a guarantee living in an adopted copy leaves the day they edit
it. That holds hardest for the skill draft, whose subject is what a skill should say.

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
draws that line as the collaboration rule. Harness code writes an **authorship file** and reads it back, which answers a
different question from `provenance` (Section 1.4): `provenance` says which route the content took, and authorship says
whose hand put it there. In a knowledge file the authorship block sits inside the file. In a skill it sits in a second
file beside the `SKILL.md`, whose two fields leave no room for it and whose body loads into a model's prompt, where an
origin block would read as one more line of procedure. A folder with no authorship file is the operator's.

### A session may also teach the system how to work

A session feeds the procedural pillar as well as the practical one. A session that established *brisket holds at 250*
fed `notes/`, and a session that established *a better way to run a session* has something for `skills/`, which the
analysis may propose. `voice` is the seed of that half: it holds a profile of the operator, corrected from their own
edits through the same propose-and-approve loop as everything else (Section 2.4).

A note says what is true and a skill says how to work. That line decides every proposal, and the test that separates the
two is who acts on the draft first. A skill shapes how the expert works before anybody asks a question, because the
DeepAgent harness loads it into the prompt at the start of the turn. A note answers a question about the subject once
somebody asks one, and harness code fetches it when it is relevant.

Run the test on the three cases that matter. *Brisket holds at 250 for four hours* waits for somebody to ask about
brisket, so it is a note. *Always preheat the grill for 15 minutes* reads as an instruction and is still a note, because
the operator at the grill acts on it and no draft changes shape until they ask. *Ask for the pit's own thermometer
offset before drafting any smoking lesson* changes the expert's next draft first, so it is a skill.

A procedure the operator proved by doing is a solution note, tagged `type.solution` (Section 1.5), and it becomes a
skill once it directs the expert's own drafting rather than the operator's own doing. Section 2.4 decides where the file
lands, so the filing decides one thing, the scope.

### The four decisions a written skill needs

A skill write asks for approval, and it asks in its own words. Every write under a `skills/` folder already stops for
the operator, so the question is which sentence they read while stopped. *A lesson is ready to file* is the wrong
sentence in front of a file that changes how the expert works on every later turn. The skill filing carries its own
approval naming the scope and any shipped skill it would shadow, with the exact `SKILL.md` text underneath. Agreeing in
the analysis that the session learned something is agreement about the lesson, and the procedure the expert then wrote
from it is a second object the operator has not read yet.

A session revises a skill a session wrote, and never one the operator wrote, and the two guards above reach skills
unamended. Read-before-write means a proposal to revise `skills/session-loop/SKILL.md` loads that file's current bytes
in the same turn and derives the revision from them. Authorship means a session amends a folder carrying the authorship
file a session wrote and no other, and a folder without one is the operator's. A session that wants such a skill changed
proposes the change in conversation and leaves the edit to the operator.

The expert that ran the session asks for a root process skill, and harness code writes it. The expert drafts the text
and calls a gated tool, and the tool performs the write once the operator approves. Every session summary takes the same
route into the root `sessions/` folder (Section 2.7). The Librarian's write capability stays at zero, as it does for
topic creation, and widening an expert's permission to write outside its own topic is refused, because that loosens the
subtree confinement on every turn to serve one filing that already has an approval in front of it. **No such tool exists
today**, and both root folders are denied to every agent until one does (Section 2.4).

A skill that shadows a shipped one by name says so three times. Section 2.4 gives the mechanism, and nothing in the tree
records the swap: no index lists the file and no tag points at it. So the proposal says it, the approval says it again
with the exact bytes, and the file opens with the line naming what it shadows (Part 3). The third one matters most,
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
procedural pillar outranks the theoretical one (Section 1.8, rule 1) and has no file that bounds the exploration stage.
Nothing is built for it, because the obvious placement contradicts Part 1: everything under a `skills/` folder is a
skill file (class 3 in Section 1.4), so a `summary.md` sitting inside one is either a knowledge file living in a folder
the rules exempt, or a fourth file class this document never defines. `Cooking/skills/summary.md` passes content
validation today and the tree walk then warns `LEGACY_SKILL_LAYOUT`, because a flat markdown file inside `skills/` is
the superseded layout and loads as no skill.

Three shapes answer it, and the recommended default is the first. A skills section inside `topic.md` needs no new file
class, puts the pillar's overview in the file the Librarian already routes on, and gives up only an approval the
operator already gives when they approve `topic.md`. `skills/summary.md` as a fourth file class carries its own
frontmatter rules and its own exemptions, and costs changes in Sections 1.2 and 1.4 and in Part 3. A generated file
gives up the operator's approval that makes a breadth file worth reading, and Section 1.6 refuses it on that ground. The
operator picks, and Section 1.2 grows a folder comment when they do.

### Three gaps in the self-learning loop

The PKB is meant to improve itself from what it learns in the work. Three things stand between the design as written and
that claim, and each one belongs to a pillar. **No route exists for any of them yet.**

The PKB notices nothing while nobody is in a channel. It notices a conflict on the write, because a session is there to
report to (Section 1.7), and it notices nothing else: the one mechanism that reasons over all three pillars waits on
`/close`, which does not ship, and the daemon builds its application without a worker, so `/health` reports the scanner
disabled and the only check that happens outside a session is one the operator asks for. An agent that reasons when
spoken to and never otherwise is a filing system with a good vocabulary. One wiring job starts the worker.

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
│   └── [skill-name]/       #   one folder per skill (voice/, discovery/, session-loop/, ...)
│       └── SKILL.md
├── (optional) sessions/         # What a session worked out, whichever agent it ran on (Section 2.7)
│   └── [objective-title].md     #   topic: "(session)", a topic.* tag per expert. Starts absent
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
   `ingest-book`, `summarization`, `conflict-detection`, `tag-proposal`, `sub-topic-proposal`, `research`,
   `discovery`, and `voice`. They load from the implementation itself, so the tree's own `skills/` folder starts empty
   and an untouched skill improves whenever the implementation does. They work out of the box and stay drafts, and the
   operator who wants to change one adopts it. A copy lands in a `skills/` folder in the tree, at the root or in one
   topic (Section 2.4), opening with one line naming the shipped skill it now shadows, and it shadows that skill
   permanently. Adoption is a decision and never an accident, because a seeded copy nobody touched is indistinguishable
   from one the operator rewrote, and with no undo the implementation would have to choose between overwriting their
   work and never shipping an improvement.

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

- **Research agents (breadth-first)** receive a Research Pack, which serves the exploration stage. It holds `topic.md`,
  the relevant subtrees of the root `tags.md`, and the `summary.md` files of the relevant topics. A research agent reads
  no `index.md` unless it asks for one.
- **Implementation agents (depth-first)** receive an Implementation Pack, which serves the exploitation stage, when the
  task is defined. It holds the full `index.md` of the selected topic, the `references/[source]/[source].md` files, and
  the relevant solution notes. `notes/summary.md` loads first, because the operator's rules hold the highest priority in
  a pack.

The split is rule 2 in Section 1.8, and it is what bounds a pack. A breadth reader handed the depth files of one topic
receives more than one context window holds, and a pack that recurses into sub-topics multiplies that. A pack therefore
carries a size budget and truncates at an entry boundary rather than mid-file, and it names what it omitted.

Rule 1 in Section 1.8 ranks the practical and procedural pillars above the theoretical one, and every pack follows that
order for the pillars it carries, because a pack that leads with references and appends the operator's notes inverts the
one rule the PKB exists to keep. No pack carries the procedural pillar, and the reason is the audience rather than the
standing: a skill instructs the agents that work this PKB, and a consumer of a context pack works elsewhere.

A session summary will enter a Research Pack once the operator has approved it (Section 2.7), ranked last of what the
pack carries, because it records how the topic came to know a thing rather than what the topic knows. A session summary
belongs to no topic and a pack is a topic's own files, so the pack builder has no route to one and that ordering is
designed and not built (Section 2.8, *How an entry improves an expert*). A lesson a session filed is an ordinary note
carrying `provenance: practised`, and it enters a pack as one.

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

The standards in Part 1 decide what needs the operator's approval, here and on every other channel, and the caller
decides none of it. Capturing a note the operator dictates and writing a first extraction of a source land unattended,
because capture must stay frictionless. That is no exception to *the operator approves what enters the PKB*: the
operator dictated the note or named the source, and that instruction is the approval (Section 2.7). Changing
human-approved content, adding a tag, and rewriting an extraction the operator has already read all wait. Every write
under a `skills/` folder waits, at the root and inside a topic, and it carries its own approval naming the scope
(Section 2.8), because a skill gates like human-approved content and never like a capture. Every write a session makes
waits too, the session summary and the lesson alike, and the operator approves the rendered text rather than a request
to write it (Section 2.7). Once a change lands, harness maintenance regenerates the indexes and the tag registry.

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

A conflict-detection sub-agent reads the tree before the note lands. It holds read tools and no write tool, it compares
the draft against the topic's notes and references on the axes of Section 2.8, and it comes back with one pair:
`references/grill-basics/grill-basics.md` says preheating for 10 minutes is sufficient.

The expert reports the pair into the session and says which kind of disagreement it is. Neither file names a pit, so the
two may both be true under conditions neither writes down. The operator answers that their pit runs cold and 15 minutes
is what it needs, which is the third resolution in Section 1.7: both hold, and the note gains the condition that
separates them. The expert redrafts, the operator approves the exact text, and one note lands, `status.approved`, with
the ordinary frontmatter of Section 1.4.

No tag records the conflict, no `review_note` describes it, and no date says a review happened later, because none did.
The reference is untouched, since the book was never wrong about the book. The conflict is over when the session says
so, and the only trace it leaves is a note that reads better than the dictated one. Had the operator decided the book
was right, they would have edited the draft to 10 minutes before approving it, and had they wanted to think about it,
the session record would name both files and say the pair is open, because nothing else remembers it.

## Fields this example no longer uses

The two blocks below are what the old design produced from this same conflict: the note tagged while the question was
open, then cleared by an operator review. Neither state happens now. The note goes straight to the second block's
`status.approved` without passing through the first, and it carries no `last_reviewed`, because no review happens for a
date to record. Layer 1 still defines all three names, and the reconciliation pass takes them out (Section 1.7).

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

The same note after the review it no longer gets:

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

---

# Summary

The **Personal Knowledge Base** is an AI-assisted expert for the subject areas the operator works in:

- It rests on three pillars, and every topic has a place for each. Theoretical is `references/`, what others
  established. Practical is `notes/`, what the operator established by doing. Procedural is `skills/`, how the two of
  them work together. The external world supplies the theoretical pillar: books, papers and internet articles.
- It stores memory, wisdom and context, through hierarchical tags that organize a topic where a folder would have, a
  machine-maintained global tag registry, and conflict handling that runs on the write and leaves nothing behind.
- Human content wins: the practical and procedural pillars outrank the theoretical one, so an operator's note, a
  human-approved breadth summary and a skill take precedence over a reference.
- Division of labor: the practical and procedural pillars are human-generated and AI-curated, everything else is
  AI-generated and human-curated, and hooks generate the mechanical files.
- Breadth vs. depth: `topic.md` and `summary.md` serve a breadth-first reader, `index.md` serves a depth-first reader,
  and the split is what bounds a context pack.
- Agents mediate every interaction. The Librarian researches across topics: it frames an objective so single topics can
  answer it, classifies each inbound item, and recognizes when two topics' answers interact. Harness code fans the item
  out to every applicable Topic Expert and merges their answers by attribution, so classifying is a model's judgment and
  fanning out and merging are code. Cross-topic research is a competence of its own, distinct from depth in one subject,
  and its skills live at the PKB root because they belong to no topic. The Librarian holds no topic knowledge and writes
  nothing into the tree.
- Topic Experts run the topics, one PKB template by default, overridden per topic through `expert.md`. Hooks enforce the
  mechanical standards, and the experts carry the judgment work through common, overloadable skills.
- Ten skills ship with the implementation (Section 2.4), sorted by pillar. They mount from the package ahead of the
  tree's own `skills/` folders, read-only. The tree's folders take writes, and a file declaring a shipped skill's name
  shadows it permanently. Seven more the design describes have no file yet, one of them the Librarian's own cross-topic
  research skill (Section 2.2).
- A session is how anyone works with the PKB in dialog: one objective, in one channel, attached to the Librarian when
  the objective crosses topics or to one Topic Expert when one topic owns it (Section 2.7). Neither is a default, and
  the operator chooses at the start. The counterpart is the operator, or a project agent that needs the knowledge. An
  objective that needs a new tool or a process to follow makes the session write instruction sets, which state why the
  work is necessary and what it must achieve and never an implementation. `/close` is the only session command: it marks
  the session record closed and keeps it, so the next session on that objective reads what this one declined, it locks
  the channel, and it puts a session that produced something in the learning queue. The channel stays shut, so what the
  analysis found parks in the learning channel and the operator settles it there (Section 2.8).
- A session that searches asks with the objective and none of the operator's beliefs, verifies every URL and quotation
  in code against the page text the provider returned, and weighs what survives against the operator's notes without
  touching one. **None of the session machinery is built.**
- A conflict is found on the write, by a read-only sub-agent that compares the file against the tree on four axes of
  which three run, and it is settled in the session that made the write (Section 1.7). Nothing tags a file, nothing
  queues a conflict, and no timer re-reads a pair a write already compared, so a pair whose files both predate the
  design, an operator's own direct edit and a truth that changed with no write reach nobody until the operator asks for
  a check.
- One mechanism reasons over what the PKB holds, on one trigger (Section 2.8): a session closed. It checks each
  candidate against the tree, draws from one queue, runs in one worker, and ends with a proposal the operator answers in
  the learning channel.
- The analysis reads the session record from the beginning, because chained abstractions compound and one-shot
  consolidation beats streaming. The self-improvement skill carries that competence: it drafts candidates, quotes the
  record each one rests on, and lands the bytes the operator approved and no others. Its conclusions are bounded,
  because five kinds of session output look like knowledge and are not: an approach that never worked, a failure the
  machine caused that week, a verdict that a tool cannot do something, an error a retry cleared, and the story of one
  afternoon. Every session that produced something enters the queue, whoever was in the channel, and filing nothing is
  the default result. The bar runs inside the analysis rather than in front of the queue, so the queue holds work and
  the channel holds proposals: about one candidate in seven establishes anything, and a review list of everything goes
  unread.
- A session may also teach the system how to work. A note says what is true and a skill says how to work, and the test
  is who acts on the draft first. A wrong skill is worse than a wrong note, and it marks nothing it shaped. The conflict
  check reads no skill, because a skill states no claim, so a stale one surfaces through the session that hits it and
  through the approval that let it land. Sections 2.7 and 2.8 record what the design has left unsolved: a session
  summary reaches no expert yet, the PKB reasons over nothing while nobody is in a channel, nothing holds how the
  operator decides, nothing decays with age, the procedural pillar has no breadth file, and four questions stay open:
  whether a project agent may ever hold an approval of its own, whether the learning channel binds to the Librarian or
  to no agent, whether the queue needs a cap, and how long a queued analysis stays eligible before it ages out.
- The DeepAgent harness hosts the agent layer and exposes it through a dedicated TUI, Telegram channels and other
  channels, and the operator connects to the Librarian or to one Topic Expert.
- The Project Manager (separate project) reads the PKB through context packs and feeds project outcomes and lessons
  learned back into it.

Every component works under **human strategic control**. AI stays tactically brilliant. Humans keep the strategic
vision.
