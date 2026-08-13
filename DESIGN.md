# Personal Knowledge Base: Technical Design

This document is the technical design of the Personal Knowledge Base: the tree it writes into, the frontmatter every
file carries and the tag system the Librarian routes on, the sessions the work runs in and the artifacts a session
leaves behind, the agents that read and write, the skills they load, the workflows they run, the conflicts a write
raises, the loop that learns from a closed session, the work a session hands out, and the layout an empty PKB grows
from. The project description it serves lives in `README.md`, and that description settles any disagreement between
the two.

---

## 1. The Tree

### 1.1 Standard topic structure

The PKB is a folder tree. *The tree* names those folders throughout this document. Each topic root uses this structure:

```
[Topic Root]/
├── topic.md            # Operator-approved overview, map and skills section (Section 1.4). Helpful for exploration.
├── index.md            # Machine-generated canonical index (tag subtree, own skills). Helpful for exploitation.
├── references/         # THEORETICAL PILLAR – what others established (books, papers, articles)
│   ├── summary.md      # Operator-approved overview of all references. Helpful for exploration.
│   └── [source-name]/  # One folder per ingested document
│       ├── [source-name].md  # Map of the source: a section per part, a bullet per argument. Helpful for exploitation.
│       └── [source-files]
├── notes/              # PRACTICAL PILLAR – what the operator established by doing
│   ├── [note-title].md # Note content (standalone or main file in note folder)
│   ├── [note-title]/   # Optional folder for notes with media
│   │   ├── [note-title].md  # The note text
│   │   └── media/      # Images, screenshots, videos, etc.
│   └── summary.md      # Operator-approved distilled rules and solutions from all notes. Helpful for exploration.
├── (optional) skills/  # PROCEDURAL PILLAR – how this topic is worked (Section 4)
│   └── [skill-name]/
│       ├── SKILL.md
│       └── AUTHORSHIP.md  # Harness-written: whose hand put the skill there (Section 7.8)
├── (optional) expert.md   # Topic Expert override – defaults to the PKB template (Section 3.4)
└── (optional) sub-topics/ # Deeper nested topics with the same structure
```

The three pillar folders hold everything the topic knows, and `topic.md` and `index.md` map them: `topic.md` is the
topic's breadth file, holding its distilled ideas and the approaches that could be valuable, and `index.md` records
where the details of one approach sit, one entry per approach a breadth file lists, carrying the file and the section
that holds it (Section 1.9).

The project description calls that per-topic breadth file `summary.md`, and this design calls it `topic.md`.
`notes/summary.md` and `references/summary.md` sit beneath it as this design's own subdivision, a breadth file for each
subject pillar, and the procedural pillar's is the skills section inside `topic.md` (Section 1.4). A topic root holds
no `summary.md`, and nothing renames the three.

The topic's first approved skill creates the `skills/` folder, and that approval is the approval on the folder. The
procedural pillar has its place from the moment the topic exists, so an absent `skills/` says nobody has approved a
skill here rather than that the topic lacks a pillar, and the skills section inside `topic.md` carries what the operator
has learned about working the topic until one lands. Section 4 says which skills live there and which live at the PKB
root, and `index.md` catalogs this topic's own (Section 1.9).

**Naming convention for folder-hosted items**: give every item inside its own folder a main file named after it.

- `notes/[note-title]/[note-title].md`
- `references/[source-name]/[source-name].md`

A topic organizes its content by tag rather than by folder, so every topic has one shape and a tag reaches across topics
where a folder cannot. A Cooking recipe is a note tagged `topic.cooking.recipes`, and Section 1.5 carries the tag rules.

Do not put item content in an `index.md`. The topic-level `index.md` stays the machine-generated directory index.

One file per source, and it is a map of that source rather than a précis of it. `[source-name].md` carries the source's
thesis, its provenance, one section per part of the source as the source names them, one bullet per argument the topic
cares about, and an honest list of what nobody read. The shape prevents a confident write-up of the part that fit in one
context window, with nothing recording that the rest was never opened. Re-ingest a source as often as it is worth
re-ingesting, because each pass reconciles with what is there and then appends what it covered, what it skipped, and
when.

A source is a file by the time the map is written, and a web page becomes one at ingestion: the session captures the
page into the source folder beside the map, so `[source-files]` holds something for every kind of source the operator
points at, rule 4 in Section 1.8 has an original to copy into a topic that ingests it gainfully, and a link that dies
next year costs the topic nothing.

### 1.2 File types and creation rules

The Approval column says whether the operator reads the exact text before the file lands, and it says nothing else. It
names no author, because whoever establishes a session and sets its goal is that session's operator, a human or an agent
alike (Section 2.3). Three modes cover the tree:

- **Derived** – harness code writes the file, no agent may write it, and nobody approves it.
- **Approved** – an agent drafts, and the operator reads the exact bytes before the file lands.
- **On instruction** – the file lands inside the turn on the operator's ask, and that ask is the approval.

Two rows carry two modes, because one file moves between them over its life. Naming a source is the approval on the
first map of it, since the operator chose the source and the map claims nothing the source does not, and a later pass
appends what it covered inside the turn, while a pass that would reword an argument already filed stops for the operator
(Section 1.7). A session's running record lands as the session runs, because it says what happened rather than claiming
anything is true, and the synthesis the session ends with waits on the operator word for word.

| File                                        | Approval                              | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
|---------------------------------------------|---------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `topic.md`                                  | **Approved**                          | Breadth map for a breadth-first reader: the topic's distilled ideas and the approaches worth reaching for, each naming the file and section that holds its details, plus the skills section that is the procedural pillar's breadth file (Section 1.4). The expert drafts and maintains the overview. The operator adds insight and approves. Its `description` is the summary the root registry shows for this topic (Section 1.6).                                                                                                                                  |
| `index.md` (topic root)                     | **Derived**                           | Depth index for precise retrieval, with the topic's tag subtree, its cross-topic mappings, and a catalog of this topic's own skills. Harness code regenerates it on change, and no agent writes it.                                                                                                                                                                                                                                                                                                                                                                   |
| `expert.md` (optional)                      | **Approved**                          | Topic override of the PKB Topic Expert template (Section 3.4). The operator settles what it says and the expert drafts it.                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `skills/[skill-name]/SKILL.md` (optional)   | **Approved**                          | The procedural pillar for one topic: a skill this topic's expert and its sub-topics' experts load, and nobody else (Section 4). The operator settles what it says and the expert drafts it. It is the one skill path inside the expert's own subtree.                                                                                                                                                                                                                                                                                                               |
| `references/summary.md`                     | **Approved**                          | Breadth summary of the theoretical pillar: what the sources established and the approaches they offer, each naming the file and section that holds its details. The expert drafts it. The operator edits and approves.                                                                                                                                                                                                                                                                                                                                                |
| `references/[source-name]/[source-name].md` | **On instruction**, then **Approved** | Depth map of one source, in the shape Section 1.1 fixes. The ingestion skill writes the first pass, and the two modes above carry every later pass over the same file.                                                                                                                                                                                                                                                                                                                                                                                                |
| `notes/[note-title].md`                     | **Approved**                          | What the operator knows from their own practice: an observation, an opinion, or something that worked (tagged `type.solution`). They settle what it says in the turn, where the expert drafts it, or in the analysis after `/close` once they tried the thing, where the Learning agent drafts it and the topic's own expert files it (Sections 2.6 and 3.5). The operator approves the exact text.                                                                                                                                                                   |
| `notes/summary.md`                          | **Approved**                          | Breadth summary of experience: distilled rules, notable solutions and the approaches worth reaching for again, each naming the file and section that holds its details. The operator edits and approves. It is human content, so it outranks the theoretical pillar and nothing orders it against the notes it distils (Section 1.8, rule 1).                                                                                                                                                                                                                         |
| `tags.md` (PKB root)                        | **Derived**                           | Global tag registry and the Librarian's one routing read (Section 1.6): the tag tree, a one-line summary on every node a topic folder backs, the *(custom expert)* markers, the cross-topic mappings and the catalog of the shipped skills and the root's own. Every field is lifted from a file that already carries it, and harness code regenerates it once per agent run (Section 1.9).                                                                                                                                                                           |
| `sessions/[objective-title].md` (PKB root)  | **On instruction**, then **Approved** | One file per session, for its whole life (Section 2.7): the objective and the experts, the running record the session writes as it goes, the approach the operator settled landing in it in their own words, the synthesis of what it worked out, holding as sections of itself the instruction sets the operator kept, one per experiment or tool, and the distillation the analysis appends. Harness code renders it, and the write needs the root tool of the row below, because no expert writes outside its own subtree. The record says what happened and needs no approval, and the operator approves the synthesis word for word. |
| `skills/[skill-name]/SKILL.md` (PKB root)   | **Approved**                          | The procedural pillar across topics: a skill every expert loads, and the Librarian and the Learning agent with them (Section 4). The folder starts absent and the first skill approved into it creates it. A write here sits outside every expert's subtree, so it needs a root tool.                                                                                                                                                                                                                                                                               |

**Collaboration rule**: the practical and procedural pillars are human-generated, AI-curated. The notes themselves, the
`skills/` folders and `expert.md` overrides carry the operator's own experience and their own ways of working, and the
expert assists with clarity, grammar and structure. Every other meaning-carrying file, `topic.md`, the breadth summaries
and a session's synthesis, is AI-generated, human-curated, and carries the expert's own reading and reasoning. The
expert writes the theoretical pillar's depth files on first ingestion, and the operator curates them at the summary
level. That difference in substance is why the practical pillar outranks the theoretical one: a note holds what the
operator established under their own conditions, and a reference holds what somebody else claimed (Sections 6.1 and
1.8). The approval modes above cut across that line rather than restating it, since a note and a breadth summary both
wait on the same reading of the same bytes.

An agent may draft a note or a skill itself in the analysis, and both stay on the human-generated side of that line,
because the experience in them is the operator's: they cooked it, they ran it, they came back and said what happened.
The Learning agent argues about what that experience means and never about whose it was, and the topic's own expert
files the text the operator approved word for word (Section 7.1). Skill files are a file class of their own, and the PKB
rules for knowledge files pass them by (Section 1.3).

### 1.3 Metadata and the three file classes

Every markdown file that carries knowledge includes YAML frontmatter. Three file classes exist, and this section governs
the first alone:

1. **Knowledge files** – notes, references, the breadth summaries, `topic.md` and session files. Full PKB frontmatter,
   as below. A session file is one of these too, and no topic owns it: it sits in the root `sessions/` folder, its
   `topic` field reads `(session)`, it carries a `topic.*` tag for each expert that took part and declares no
   `related_topics`, and the check comparing a declared topic against a file's location has nothing to compare. Sitting
   two experts down together shows no crossing, and a crossing that proves real reaches the registry through the note
   the analysis files (Section 7). Its frontmatter is valid from the turn the session opens and gains a `topic.*` tag as
   each further expert answers, so a session file carrying a running record and no synthesis yet is a valid knowledge
   file. These rules govern the frontmatter, and Section 2.7 governs the body.
2. **Machine-generated files** – a topic's `index.md`, at a topic root or at any nesting depth below it, and the root
   `tags.md`. Minimal generated frontmatter only. The PKB root holds no `index.md`, and the registry is the one derived
   file above the topics (Section 1.6).
3. **Skill files** – everything under a `skills/` folder, at the PKB root or inside a topic, plus `expert.md`. These
   instruct an agent rather than describing a subject, so a skill file carries no PKB frontmatter, enters no knowledge
   index and contributes no tags, and every rule that reads PKB frontmatter passes it by. A `SKILL.md` carries the
   DeepAgent harness's own two fields, `name` and `description`, and nothing else. The PKB fields break the harness's
   parser, and the harness then drops the skill without an error anywhere. Those two fields are also what the skills
   catalog lists, in a topic's `index.md` for that topic's own skills and in the root registry for the shipped skills
   and the root's own (Section 1.9), a different artifact from the knowledge index. `AUTHORSHIP.md`, which
   harness code writes beside a `SKILL.md` to record whose hand put the skill there (Section 7.8), is one of these too,
   and the catalog generator and the tree walk read only `SKILL.md` inside a skill folder, so it trips no
   `LEGACY_SKILL_LAYOUT` warning.

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
generated files carry their own minimal frontmatter instead: the root `tags.md` in Section 1.6 has a `title` and a
`source_type` and no description, and asking it for one would ask the generators to fail their own validation.

A captured source is none of the three. `[source-files]` beside a map holds the source as it arrived, a PDF, an EPUB, a
web page saved as markdown or a text extraction of any of them, and the extension decides nothing: the map is the
knowledge file, the source is the material the map reads, and it carries no PKB frontmatter, enters no knowledge index
and contributes no tags (Section 1.1).

`related_topics` lists related topic paths in tag notation, such as `bbq.equipment`. The declared value is the bare
topic path, and the registry renders it under the `topic.` namespace as `topic.bbq.equipment` (Section 1.6), while a
value that already carries the namespace stands as written. Declare a cross-topic relationship here and nowhere else.
Harness code aggregates these declarations into the registry (Section 1.9).

### 1.4 The procedural pillar's breadth file is a skills section inside `topic.md`

Each subject pillar carries an operator-approved `summary.md` (Section 1.7), and the procedural pillar carries a skills
section inside `topic.md` instead. It is the procedural twin of `notes/summary.md`: what the operator has learned about
working this topic, distilled the way the two subject summaries are, and the file that bounds a breadth read over the
third pillar.

The section rides the approval the operator already gives when they approve `topic.md`, so the pillar gains its breadth
file and the design gains no file class. `skills/summary.md` was the obvious placement and Section 1 refuses it, because
everything under a `skills/` folder is a skill file (class 3 in Section 1.3), so a `summary.md` sitting inside one is
either a knowledge file living in a folder the rules exempt or a fourth file class this document never defines, and
`Cooking/skills/summary.md` passes content validation and then warns `LEGACY_SKILL_LAYOUT`, because a flat markdown file
inside `skills/` is the superseded layout and loads as no skill. A generated file was the other candidate, and Section
1.7 refuses it because the operator's approval is what makes a breadth file worth reading.

The skills catalog inside each topic's `index.md` (Section 1.9) is a different artifact and stays one. A catalog is
generated and says which skills that level declared, and the section is approved and says what the operator learned
about working, so a generated list would have to be operator-approved and a distilled summary would have to be generated
for the two to merge.

### 1.5 Hierarchical tags

Hierarchical tags improve context filtering, inheritance and agent retrieval: a nested tag states a relationship, and an
agent filters at any level of the tree.

#### Tag namespaces and values

Use dot notation, and each dot adds a level, so `topic.cooking.grilling` sits under `topic.cooking`. Three namespaces
exist and nothing invents a fourth. `topic.*` and `domain.*` are open trees the operator grows a branch at a time, and
`type.*` is a closed set whose every value is below.

| Tag              | When to reach for it                                                                                                                     |
|------------------|------------------------------------------------------------------------------------------------------------------------------------------|
| `topic.*`        | On every knowledge file, as deep as the content is specific. On a session file, one per expert that took part.                           |
| `domain.*`       | On a file whose subject cuts across topics by function rather than by area: `domain.legal.compliance` on a Cooking note about labelling. |
| `type.note`      | On an observation or an opinion the operator holds from their own practice.                                                              |
| `type.solution`  | On a note recording something that worked and is worth reaching for again.                                                               |
| `type.reference` | On the map of an ingested source, under `references/`.                                                                                   |
| `type.summary`   | On `topic.md`, on `notes/summary.md` and `references/summary.md`, and on a session file.                                                 |

#### Tag rules

- Every knowledge file carries at least one `topic.*` tag and exactly one `type.*` tag.
- Start a tag with a broad namespace. Add narrower terms as the subject needs them.
- Create no ad-hoc tag. The expert proposes a new tag and the operator approves it before the expert files content that
  uses it. A tag carries what a folder used to, so an ad-hoc one loses the file rather than misfiling it.
- Keep tag depth to 4 levels or fewer.
- A nested tag implies its parent. `topic.cooking.grilling` also means `topic.cooking`.
- The expert assembles a context pack from tags, filtering by namespace and depth.
- Sessions add no namespace. A lesson the operator earned files as `type.solution` when it worked and `type.note` when
  it did not, and a session file carries `type.summary` with a `topic.*` tag for each expert that took part.

### 1.6 The tag registry

Tags are flexible and relational, so the PKB keeps one `tags.md` registry at its root: the canonical tag tree and
lightweight ontology for agent ingest, holding namespace definitions, per-topic subtrees, and cross-topic mappings. The
`topic` namespace renders as one section per top-level topic root rather than as a single tree, so `topic.cooking` heads
its own section and its sub-topics nest inside it. Each topic's `index.md` embeds its own subtree for local depth work.
The PKB root holds no `index.md` beside the registry, and the registry is the file the Librarian reads to route (Section
5.1).

The registry is a complete map. Harness code regenerates it from the files themselves (Section 1.9), so it names every
topic the tree holds and a topic cannot be missing from it without being missing from the tree. Completeness is what the
breadth phase rests on: a `brainstorming` round surveys this one file to decide which topics bear on an objective and
whether the objective needs a topic the tree does not hold yet (Section 5.1), and a topic absent from the map is a topic
that round never considers.

A tag tree gives names, and the Librarian has to find the topic whose vocabulary does not match the objective it was
handed, because an analogy that crosses subjects shares no tags with the problem it solves. So every node with a file
behind it carries a one-line summary, and each summary is lifted rather than authored:

- A `topic.*` node that a topic folder backs takes the `description` out of that topic's `topic.md`, the sentence the
  operator already approved (Section 1.2), and gains the marker *(custom expert)* when the topic owns an `expert.md`, so
  the Librarian never walks the tree to find one. That one line carries the whole topic, so it says what the subject
  covers and what kind of knowledge the topic holds rather than naming the subject twice, and the operator makes it
  dense when they approve `topic.md`, because nothing else authors it and a Librarian reasoning over the map sees no
  more of the topic than this.
- A `topic.*` node with no folder behind it is a tag inside a topic rather than a topic, and it stays bare.
- `type.*` carries the static definitions the generator supplies.
- `domain.*` stays bare on purpose. No file sits behind a domain the way `topic.md` sits behind a topic, so a summary
  would have to be authored and stored somewhere new, and routing turns on topics. The gap is stated rather than filled.

The registry also catalogs the skills the root resolves, the shipped ones and the root's own, from the `name` and
`description` in each `SKILL.md` (Section 1.3). A topic's own skills stay in that topic's `index.md`.

Maintenance is mechanical. Harness code regenerates the registry, scanning the files, rendering the full hierarchy,
lifting each summary out of the file that already holds it and aggregating the cross-topic mappings from the
`related_topics` declarations in file frontmatter, and it spends no LLM tokens. Nothing writes a description for the
registry, so the registry and the `topic.md` it read cannot disagree, and a summary the operator wants changed is
changed in the `topic.md` where they approved it. The registry is derived, so it reflects the tags the files use by
construction, and governance stays in the dialog rather than in the file. Regeneration is byte-idempotent, which is what
makes it safe to run after every turn.

**Example root `tags.md` (excerpt showing the Cooking subtree)**:

```markdown
---
title: "PKB Tag Registry"
source_type: tag-registry
---

# PKB Tag Registry

## Namespace: topic.cooking

- `topic.cooking` – Home cooking end to end: equipment, technique and the dishes worth making again.
    - `topic.cooking.baking` *(custom expert)* – Bread and pastry, where the dough sets the schedule.
    - `topic.cooking.grilling`
        - `topic.cooking.grilling.charcoal`
        - `topic.cooking.grilling.gas`
    - `topic.cooking.heat-management`
    - `topic.cooking.recipes`

## Namespace: type

- `type.note` – an observation from the operator's own practice
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

## Skills (from each `SKILL.md`)

- `brainstorming` – Work an objective wide: survey the map, ask one question per callee, return candidate approaches.
- `voice` – Draft in the operator's own register.

## Cross-topic mappings (aggregated from `related_topics`)

- `topic.cooking.grilling` ↔ `topic.bbq.equipment`
- `topic.cooking.heat-management` ↔ `topic.physics.thermodynamics`
```

Two `topic.*` nodes there carry a summary and five do not, and the difference is a folder: `topic.cooking` and
`topic.cooking.baking` are topics with a `topic.md` behind them, and `topic.cooking.grilling` and the rest are tags
Cooking files under. Every namespace nests the same way, so `domain.*` is a tree as `topic.*` is. The generator sorts
siblings case-insensitively by the full tag string, which is what makes regeneration idempotent.

### 1.7 Human–AI collaboration in the PKB

Every conversation with the PKB is a session, and the PKB has no other door. The operator states an objective to the
Librarian, opens the session on one Topic Expert when they know the topic that owns the work, or establishes it to the
Learning agent for the analysis after `/close` (Sections 3.5 and 7), and they work in it for as long as the work lasts
(Section 2.1). One collaboration model covers what a session files: the operator asks, the agent drafts, and the
operator approves the exact text before it lands. Only who supplies the substance changes, and Section 1.2's
collaboration rule draws that line. Two things land on the ask alone, the mode Section 1.2 calls on instruction. A
session's running record needs no approval, because it says what happened rather than claiming anything is true. Naming
a source is the approval on the first extraction of it, because the operator chose the source and the extraction is the
reading of it (Section 1.2).

Three ordinary asks show the model.

The operator asks for a note. They say what they know, the expert drafts it in their `voice`, and they approve the exact
text. The expert changes no fact in a note without their approval, because the experience in it is theirs.

The operator asks to ingest a book, and asks afterwards for a note about it. The ingestion writes one reference file for
the source inside the turn, because the operator named it (Section 1.3). The note is a second ask and a different
pillar: what the operator now thinks, rather than a second account of what the book said.

The expert offers a line for a breadth summary while the operator is still there. The moment to propose breadth is the
moment they have just settled what a thing should say, so the expert drafts the entry, suggests the references and the
related topics it connects to, and the operator adds insight, removes errors and approves it or drops it. `topic.md` and
the two breadth summaries stay separate on purpose, because each one is then a small file the operator approves in one
read. A breadth file bounds both readers: the operator approves it in one sitting, and the Librarian loads it from many
topics at once and reasons over the ideas from a position of breadth (Section 5.3), while the topic's own `index.md`
carries the depth an agent goes to once the approach is settled.

Harness hooks keep the structure consistent (Section 1.9), the expert brainstorms over its own subject under
`brainstorming` (Section 5.7), and it finalizes no operator-approved content on its own.

### 1.8 Critical rules

1. **Human content wins**: the practical and procedural pillars outrank the theoretical one. An operator's note and an
   operator-approved breadth summary take precedence over a reference, and the expert changes no human content on its
   own. A write raises any conflict into the session that made it, and the operator settles it there (Section 6).
   This rule says which claim holds against another, and not what a pack lists first (Section 8.3).

2. **Breadth vs. depth**: `summary.md` and `topic.md` serve a breadth-first reader. A topic's `index.md` serves a
   depth-first reader. A Topic Expert assembles a context pack on that split, for the operator or the external tooling
   that carries the instruction sets out (Section 8). The split is what bounds a pack: a breadth reader that receives
   depth files receives more than one context window holds (Section 8).

3. **Derived vs. approved**: a machine builds every topic's `index.md` and the root registry. `summary.md` and
   `topic.md` need the operator, and the expert finalizes neither without their approval.

4. **Cross-topic solutions**: a solution note lives in one topic, the most relevant one, and nothing copies it. Tags,
   `related_topics` metadata, and Librarian routing carry the cross-topic discovery.

   This rule governs **solution notes** alone. It leaves the ingestion of sources open: several Topic Experts may ingest
   one source, each extracting what its own topic cares about (Section 3.4).

   The source material itself is copied on purpose. A topic that ingests a source gainfully, deriving at least one
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
   `sessions/` (Section 2). Every write reaches the tree through a session and the PKB has no other door (Section 1.7),
   so the rule has one place to hold. Nothing tracks what a turn merely read.

   A session may file under `notes/` the thing the operator went and tried: they cooked it, they ran it, they came
   back and said what happened. They settle that lesson in the analysis after `/close`, the Learning agent drafts it
   from the session file's record of the experiments, the operator approves it word for word, and the Learning agent
   files it through the topic's own expert, which performs the write inside its own subtree. It lands tagged
   `type.solution` when the thing worked and `type.note` when it did not (Section 2).

   The line this rule draws runs between read and done, and the tree records it by the folder a file sits in and by
   nothing else. No field on the page says how the knowledge arrived, so a finding misfiled under `notes/` reads as
   earned experience and nothing in the tree catches it afterwards. The write is the one guard, and this rule is what
   the operator holds to when they approve one.

9. **The PKB writes instructions and executes nothing**: it brainstorms a solution and plans its implementation, and a
   session states why work is necessary and what it must achieve, in one or more instruction sets, and hands them out
   (Section 8.1). The operator or external tooling carries a set out, a human following it or another agentic system
   implementing it, and the operator either reports the results back into the session or instructs the agent to run the
   tools that fetch them (Section 8). The PKB runs no experiment and builds no tool, and it grows no task queue, no
   runner and no status field, so nothing in the tree records work in progress and nothing in the tree can disagree with
   the system doing the work.

### 1.9 Mechanical maintenance (harness hooks)

> **Design principle**: *Enforce structure mechanically, curate meaning agentically.* No separate maintainer agent
> exists. Harness hooks that no agent can skip or forget perform the deterministic maintenance. The topic's Topic
> Expert (Section 3) performs the judgment work through common, overloadable skills. The Learning agent maintains
> nothing either: it reads one closed session after `/close` and proposes what that work established (Section 3.5).

Maintenance splits across two tiers. Tier 1 is below. Tier 2 runs as the common judgment skills of Section 4.2, and
Section 4.3 names the ones the design still owes.

#### Tier 1: Mechanical enforcement (harness hooks)

The DeepAgent harness does this work itself, and no agent judges any of it or can skip it. It runs on two clocks.
Validation fires per write, in front of the write, so a file that would break the standards never lands. Everything
derived fires once per agent run, after the turn, over the files that turn touched. Regeneration per write is forbidden,
because it would rewrite the root `tags.md` several times in one turn.

Per write:

- Validate the YAML frontmatter (required fields, tag syntax and depth), the file naming, and the agreement between the
  declared metadata (`topic`, `source_type`, `topic.*`/`type.*` tags) and the file's location. Validation checks a skill
  file's placement and never its PKB frontmatter, and a captured source under `[source-files]` passes the same way: the
  map beside it is the knowledge file and carries the frontmatter, while the source as it arrived carries none and the
  original of a reference the operator accepted stays the bytes the web served (Section 1.3).

Once per agent run, over the files the turn created, changed, renamed, or removed:

- Update the `updated` timestamps.
- Regenerate the topic's `index.md`, with its tag subtree and cross-topic mappings. Every knowledge file carries a
  `description` in its frontmatter (Section 1.3), so index generation is deterministic: walk the tree, read the
  frontmatter.
- Regenerate that `index.md`'s approach entries, one per approach the topic's breadth files name, each carrying the file
  and the section that holds its details. A breadth file names an approach together with where it sits (Section 1.2), so
  the entry is a lift rather than a judgment, and generation stays deterministic. This is what lets a Librarian that
  read the breadth files across many topics go straight to the details of the one approach it settled on, and a pointer
  into a file or a section the tree no longer holds flags as a broken link like any other.
- Regenerate the root `tags.md` registry from the tags the files use, carrying each topic's `topic.md` description on
  its node, the marker *(custom expert)* on a topic that owns an `expert.md` so the Librarian never walks the tree to
  find one, and the cross-topic mappings aggregated from the `related_topics` declarations. It is the Librarian's one
  routing read (Sections 1.6 and 5.1), and the PKB root holds no `index.md` for it to duplicate. Plain deterministic
  code, derived, and no LLM tokens spent.
- Regenerate the skills catalog, inside each topic's `index.md` for that topic's own skills and inside the registry for
  the shipped skills and the root's own, from the `name` and `description` in each `SKILL.md`, the one file inside a
  skill folder the catalog and the tree walk read (Section 1.3). Each section lists the skills that level declared and
  repeats no other level's, so the registry and the sections down to a topic read together give what resolves for that
  topic (Section 4).
- Flag broken links and orphaned files.

Scaffolding the standard structure (Section 1.1) for a new topic or sub-topic is mechanical in the same way, and it runs
on the operator's approval rather than on either clock.

Tier 1 schedules no conflict work. The session that made a write runs the check itself, and the report lands in that
session (Section 6).

### 1.10 Topic creation

The operator requests a new topic, or approves one the Librarian proposed (Section 5.1). Then:

1. Tier 1 scaffolds the standard structure from Section 1.1, with placeholder `summary.md` files. The `skills/` folder
   stays absent until the first approved skill creates it, and the topic still has all three pillars from this step,
   because the procedural one's breadth file is the skills section inside the `topic.md` of step 3 (Section 1.4).
2. The DeepAgent harness instantiates a Topic Expert for the topic.
3. The expert drafts `topic.md`, skills section and all, proposes the topic's first tag subtree, and asks the operator
   to approve.
4. The operator writes any skill overload with the expert's help.

---

## 2. Sessions

### 2.1 A session is durable and holds one objective

A session is durable, held on one agent for one objective, for as long as that work lasts: the objective the topic
cannot meet, the experiments that follow, and the lesson the operator and the expert settle at the end. Search for a rub
and a smoking approach, cook, come back with how it tasted, cook again, and distil after the third attempt. Search for a
trend-following signal, run several variants, watch which one holds and under what setup, and distil once the data
arrives. Feedback lands in pieces, from wherever the operator is, over weeks.

A capture is one turn inside a session and files what the operator already knew: they dictate a note, dictate a skill,
or name a source, and the write lands in that turn rather than waiting for `/close`. A skill they dictated is theirs the
way a note they dictated is theirs, so it lands the same way, with the scope it takes and any shipped skill it would
shadow named in the text they approve (Section 4). A lesson this session established is the other thing, and it waits
for the analysis however the operator phrases the instruction, because the file is the evidence for it. A skill the
analysis drafts out of that evidence asks in its own words rather than on a note's wording, because the operator has
agreed to the lesson and has not yet read the procedure written from it (Section 7).

A search is one of the things a session does, rather than a kind of session. A session may discuss, argue about a
design, ask a question and take the answer, search the internet, or try things for weeks and report back, in any order.
The PKB holds one shape for all of them, and a session that searches nothing is an ordinary session. How a search runs
belongs to the workflows and their skills rather than to this section: `brainstorming` decomposes the objective, harness
code fans the questions out to `web-search` sub-agents that hold no write tool of any kind, and code locates every
quotation in the bytes it holds (Sections 5 and 4). A session holds one objective, so a later search joins the
file this session already opened and a new objective opens a new session.

A session also writes instruction sets, and it executes nothing; the artifact and its handover are Section 8.1's.

### 2.2 A session carries its own name

Work begins as a question and becomes whatever it becomes, an afternoon or four months of it: reading, then experiments,
then a verdict, or none of those. The PKB asks the operator to declare nothing in advance about what a conversation will
turn into, because they do not know yet and a wrong declaration is one more thing to maintain.

A session holds its name itself and exists whether or not anything is looking at it, because it is a file in the root
`sessions/` folder while a channel is a surface that can go away. A session may run four months, and a name that lived
in a channel title would die with the channel and leave the file with nothing able to reach it again. The name is the
operator's: harness code takes the first one from the objective they stated, and `/name` sets a better one later
(Section 2.5). A session is one conversation whatever number of channels hold it, so its turns run one after another,
every attached channel sees the same thread, and the reply to a turn typed on the phone appears on the TUI too.

A session usually has an objective and some have none, a standing conversation with an expert among them. Harness code
still names the session from the operator's first turn, `/name` corrects it once the work shows what it is, and the
file's first section records that the operator stated no objective, so no heading stands with nothing under it. The
session is otherwise unchanged, because every rule here holds on the conversation and none of them holds on the
question at the front of it.

One expert holds as many sessions at once as the work needs, with no cap on the count, each named for its objective, so
`Trading · Trend Signal` and `Trading · Market Regime` run side by side, for two reasons, the second the stronger:

- On the operator's side, separate lines of work are separate places on the phone, and a phone is where this work
  happens. A long-running session gets a channel of its own by default, and the operator can break that attachment and
  remake it.
- On the model's side, each session keeps its context on its own objective. A conversation replays its whole history on
  every turn until harness code compacts it. Two objectives in one conversation means every trend-signal turn re-reads
  the regime work and takes an invitation to blend the two.

A channel holds one session at a time and its title names that session, so a deliberate split is safe where an
accidental one is not. Nothing caps the number an agent holds, for the reason nothing caps a session's rounds (Section
5.3): a cap protects a loop nobody is watching, and the operator opens each of these by hand and closes it by hand.

### 2.3 The counterpart may be an agent

An external agent opens a session the way a person does, and for the same reasons: it needs knowledge the context packs
of Section 8.3 do not carry, it wants a question searched, or it implements something the topic holds the conditions
for. Whoever establishes a session and sets its goal is that session's operator, a human or an agent alike, so every
approval, every bar and every write rule in this document reaches them unchanged. That includes approving a write the
tree has no undo for, and it is deliberate. A rule that let only a person approve one would need the PKB to tell an
agent from a person, and it cannot, so the rule would rest on a claim the caller makes about itself.

### 2.4 A session opens on the Librarian, on a Topic Expert or on the Learning agent

The operator names the target agent at creation: they state the objective to the Librarian, open on one Topic Expert
when they know the topic that owns the work, or establish the session to the Learning agent for the analysis after
`/close` (Sections 1.7 and 7). Grilling dinner is Cooking: one expert, one subtree, and nothing else needs to happen. A
new trading strategy crosses portfolio management and trading, neither expert holds the whole of it, and framing an
objective that crosses topics is a competence of its own (Section 5.1). A turn inside a Librarian session is one round,
and the operator is the boundary between rounds.

The Librarian still writes nothing by its own hand, the instruction sets of its deep phase included (Section 5.6), and
each expert still writes inside its own topic, reaching the root `sessions/` folder for its own session file through the
root tool a Librarian session uses for the same file (Section 3.4). A session reaches outside the topic's three pillars
through the `web-search` sub-agents its round fans out (Section 4), and a page one of them returns ranks below
everything the topic already holds until the operator accepts it (Section 6).

A session that opened on one expert and turns out to cross topics re-opens on the Librarian. Nothing copies one
session's file into another, so the operator names the objective again and the new session opens its own file.

### 2.5 Channels and commands

The operator states an objective to the Librarian, or opens on one Topic Expert when they know the topic that owns the
work (Section 1.7). Step 4 of the Librarian's workflow joins the two: every expert the Librarian consults is addressable
in its own right, so a reply saying *"the Cooking expert says…"* is also an offer to carry on with that expert.

A session is the unit of work and a channel is a way in: the operator opens a channel and attaches it to a session, and
several channels may hold one session at once (Section 2.2). `/close` brings every attached channel away from the
session, and the analysis of that session runs later in a session the operator establishes to the Learning agent
(Section 7). That analysis session attaches in the **learning channel**, a standing surface bound to no topic and no
objective, so the housekeeping never interrupts a topic conversation.

A channel holding no session offers the way in rather than waiting to be asked for it. Opening one on the TUI or on
Telegram lists the sessions still open, to attach to, and the agents a new session can open on, the Librarian, each
Topic Expert and the Learning agent, so attaching is a thing the operator picks rather than a thing they have to know
how to ask for. Inside a channel that already holds a session, `/threads` shows the same list and moves the channel to
the session they pick, and it offers no new one, because that would be the rotation the set refuses below.

Two commands come out of the project description, `/close` and `/end`, because the operator's own loop turns on them:
one says the work is done and the other says the analysis is. The rest is plumbing under that loop, and the set settles
at seven commands: `/channels`, `/threads`, `/agents`, `/cancel`, `/name`, `/close` and `/end`. The five move a channel
around, name a session or stop a turn, and none of them decides anything about knowledge. `/threads` lists the open
sessions and attaches the channel to the one the operator picks, `/name` names the session the channel holds, and
Section 2.6 carries the three that act on the session itself. The set holds no `/new` (`docs/how-to/telegram.md`),
because attaching already changes what a channel holds, and a rotation inside a channel splits one line of work in half
and leaves both halves named for the same objective. A new objective opens a new session, and the channel attaches to
it.

`/name` renames as well as names, and harness code moves the file. A session is named at the start, where the operator
knows least about the work, and a four-month session still carrying the question they abandoned in week one is a session
nobody can find. The path is the name a reader checks, so the rename moves `sessions/[objective-title].md` and the file
keeps everything it holds. Every session file also carries a `title` in its frontmatter (Section 1.3), and harness code
rewrites that field with the path in the same move, because a field left behind is a second name for one session and the
two can then disagree. Every channel attached to the session is retitled in the same move, on whichever surface it
sits, for the same reason: a channel still showing the old name is one more place the session answers to something it is
no longer called, and the operator working from the phone would never see the rename they made on the TUI. Harness code
refuses a name any session file already holds, because the path is the name and two sessions cannot answer to one, and
it refuses the rename once `/end` has sealed this file, because a sealed file is never reopened (Section 2.6).

### 2.6 Three commands act on the session itself

`/name` renames the session at any point before `/end` seals the file, on the rules of Section 2.5. An analysis session
opens no file of its own, because its writes land in the closed session's file it analyses, so `/name` there is refused
and says why: there is no file to rename.

`/close`, when the work is done, said in the session itself. A search reports back into the conversation and files
nothing, experiments file nothing, and what the session worked out waits for `/close`: a note it established, a way of
working it established, and the synthesis. A note or a skill the operator dictated is a capture and landed in its own
turn. `/close` does three things:

1. It marks the session file closed and keeps it, because the next session on the same objective should read what this
   one turned down and why rather than meeting the page fresh (*One file per session, for its whole life*, below).
2. It brings every attached channel away from the session, and the session takes no more turns.
3. It puts the session in the learning queue, every time and whatever the session produced. `/close` says the operator
   has nothing more they want to craft in this context and it judges nothing, because the filing bar runs inside the
   analysis. That analysis is never synchronous with the command: the Learning agent reads the file from the beginning
   when the worker, the harness loop that drains the queue one entry at a time, reaches the entry, and Section 7 bounds
   what it may conclude.

A session's operator is the one who closes it, and the expert sessions the Librarian opened are its to close: once the
human operator settles an approach, the Librarian closes the sessions to the experts that approach left out, and each of
those takes all three effects above (Section 5.5). The human operator sees the closes in the reply that follows, and the
analyses reach them in the learning channel afterwards like any other (Section 7). Their own Librarian session stays
open, because the deep phase runs in it.

`/end`, when the session is finished, analysis included. The operator says it in the analysis session they established
to the Learning agent (Section 7), and it seals the closed session's own file once the analysis has appended what it
distilled. Sealing is what archiving a session means here: the file stays at the path it has always had, it takes no
further turn and no further write, and a reader reaches it the way they reach any other file. Nothing moves it and
nothing folds it into an archive folder, because a move breaks every link that names it and the tree has no undo. An
analysis that found nothing to propose asks the operator for nothing: harness code writes the distillation section
saying so and seals the file itself, so both paths end at the same sealed file (Section 7.2). `/end` exists because an
analysis session that did raise something needs a way to conclude and `/close` cannot be it: `/close` queues a session
for analysis, so an analysis session closing that way would queue itself forever.

An operator who has learned the thing they came for has met their objective, and a met objective is a session to close.
Nothing brings them back to a session they left open, because returning is theirs to do and a channel left attached on
the phone is their reminder.

### 2.7 One file per session, for its whole life

A session keeps one file, `sessions/[objective-title].md` in the PKB root, whether it opened on the Librarian or on one
Topic Expert. A session established to the Learning agent opens no file of its own, because its writes land in the
closed session's file it analyses. The session file carries the whole arc as sections of one document, it is durable
because a file is durable, and nothing deletes it.

Harness code creates the file when the session opens, under a name no session file already holds, because the path is
the name and nothing overwrites a file, sealed or open (Section 2.5). The session writes into it as the work happens:
each experiment and what it produced, the sources kept, the ones turned down and why, the claims verification held back,
the conflicts the work raised against the topic's notes, and on a Librarian session the whole of each round's reply
(Section 5.3). `/close`, a rename and `/end` each append an entry naming the command and the date, because the body is
append-only and the frontmatter carries no state field (Sections 1.3 and 1.5). The rename entry names the path the file
had before, because nothing else remembers what a reader was looking for six months ago.

The approach the operator settles lands in the record like every other turn, in their own words, so a reader six months
later finds the approach the instruction sets rest on in the same file as the rounds that produced it (Section 5). A
session on one Topic Expert settles the same way (Section 5.7), and an approach the operator amends is another entry
rather than an edit to the first, because the body is append-only.

The whole arc sits in one file because a note that came out of a session is a claim and the file is the evidence for
that claim. The distillation section joins the two by recording how the lesson was reached as well as what it says, so a
reader who doubts the note six months later reads what was tried, what was turned down, and the reasoning that turned it
into a note, in one place. The large-source ingestion loop settled the shape already:
*"There is deliberately no second store of progress: a second source of truth about what was read is a second thing that
can be wrong, and the one a human can check is the file."*

The body is append-only. A turn adds to the end and nothing rewrites an earlier entry, because a model asked to revise a
long report across turns removes correct material without saying so and introduces errors while it polishes. A
correction is a new entry naming what it corrects, and the operator reads the file by asking the expert for it.

The mechanical tier sees an ordinary knowledge file (Section 1.3), and two of its fields plus the root folder keep it
distinguishable from a note the operator earned: `source_type: summary`, the tag `type.summary`, and one `topic.*` tag
per expert that took part, so the file names who answered and the analysis that generalizes over sessions finds its
material. A running record in the body changes none of that, because the schema constrains the frontmatter and leaves
the body to the session. A rename is a file the turn renamed, and Tier 1 regenerates the indexes and the registry over
it and flags a link left pointing at the old path (Section 1.9). The write-time check does not run on a session file at
all, because Section 6.3 puts the check on a note or a reference. The record itself lands on instruction and needs no
approval, because it says what happened rather than claiming anything is true, and the operator approves the synthesis
word for word in the analysis session (Section 1.2).

The sections run in the order the life does: the objective and the experts, the running record, the synthesis, and the
distillation. The synthesis holds the questions the session asked, every source it kept, every source it rejected and
why, the claims verification held back, the conflicts it raised against the topic's notes, the instruction sets the
operator kept, and what the session worked out. A session that searched nothing keeps the objective and the synthesis
and fills the source sections with nothing, because a discussion that reasoned from what the operator already holds and
reached a conclusion reached one. A session that produced nothing still has a file: it leaves no synthesis rather than
no file, and the distillation says so.

A Librarian session keeps one file like every other session, and one rather than several because splitting the crossing
into per-topic accounts loses the thing worth keeping. Harness code writes it on the route a root process skill takes:
the operator approves the exact bytes of the synthesis and a root tool performs the write, so the Librarian still writes
nothing (Sections 4 and 7).

One shape is refused, and it is narrow. A session that searched, admitted nothing past the verification of Section 4.5,
and then wrote a confident synthesis anyway summarized a page it never read, so that filing is refused and the refusal
quotes back the claims verification held back. A session that never searched is a different thing and files as usual. A
session that read one page, cooked from it, and learned one thing writes a note and no synthesis, because the synthesis
holds what the session worked out and the note holds what the operator did.

A rejection reaches the tree through this file alone. A candidate the operator turns down leaves no folder under
`references/`, no stub, and no copy, per rule 4 in Section 1.8. The reason is theirs, recorded in the words they typed,
and it lands in the running record on the turn they said it, so the next session on that objective reads what this one
declined whether or not the synthesis ever named it. That record is the whole of it: a session file reaches no expert,
which Section 7.6 marks as a gap, so a later search that returns the same page returns it unmarked.

Candidates live with the session rather than in the tree. A page the search returned and the operator has not accepted
stays with the session: the text goes when the search that found it ends, and its line in the running record stays.
Nothing stages it, copies it or writes it under the PKB root. `.inbox/` is where an accepted source stages on its way
through ordinary ingestion, and nothing else puts anything there. The cost is small, because harness code fetches a page
the operator accepts a second time. The alternative was thirty browsed candidates leaving thirty permanent folders in a
tree with no undo, in a staging area no channel can list.

### 2.8 What a session may file

| Outcome                                         | Where it lands                                                                                                                 |
|-------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------|
| Something the operator tried, and what happened | `notes/`, tagged `type.solution` when it worked and `type.note` when it did not                                                |
| The session's synthesis of what it worked out   | The synthesis section of its own `sessions/[objective-title].md` in the PKB root, in a file tagged `type.summary`              |
| A way of working the session established        | `skills/[skill-name]/SKILL.md`, in the topic's folder or in the root's (Section 4)                                           |
| An article the operator accepts                 | `references/[source-name]/`, through the ordinary ingestion procedure (Section 3.4), with the topic's own copy of the original |
| A candidate the operator rejects                | The rejection list inside the synthesis; the running record holds it either way, synthesized or not                            |
| Nothing at all                                  | Nowhere: no note, no synthesis, no folder, and the session file says so                                                        |

The first three rows are approved in the sense Section 1.2 fixes: the operator reads the rendered text before it lands,
in the session itself when they dictated the thing and in the analysis session when the analysis drafted it (Section 7).
An article lands on instruction instead: they name the candidate, harness code ingests only a page it printed for them
and fetched itself, and it writes the first extraction inside the turn.

The bar on the first row is three conditions and each one carries load: the operator did the thing, they came back and
said what happened, and they approved the exact text that lands. An expert holding the first two alone holds an
experiment, and it argues about what the experience means and never about whose it was (Section 1.2).

Rule 8 in Section 1.8 is the line this table draws. A session files everything it read as a reference or as a synthesis,
and everything the operator did as a note, in their own words, after they did it, and the folder is what records which
of the two a file is.

### 2.9 The expert argues with the operator, and about the operator's own conclusions

The operator works with the expert the way they would work with a human expert. A good expert objects during the work
rather than in a retrospective. The operator says the rub needs more sugar, and if their own note from March says sugar
burned at that temperature, the expert says so while there is time to change the rub. The same holds when the analysis
runs, where the operator can be wrong. Told during the session to file *sugar burns above 250*, the Learning agent reads
experiment two at 260 without burning and says so beside the candidate it drafts. The topic's expert then files what the
operator decides, because meaning is theirs (Sections 1.7 and 6).

### 2.10 Direction is conversation, and so is the instruction that writes

The operator steers a running session by talking to the expert: drop that question, chase this one instead, that source
is no good, that lesson is half right. None of that writes anything, and none of it halts the session either. The write
comes on its own instruction, once the two of them have worked the text into something the operator will stand behind,
and a conflict the write-time check reports is direction like the rest: the expert reports it, the operator settles it,
and the write follows their word (Section 6).

---

## 3. The Agents

### 3.1 Agent-mediated access

Every interaction with the PKB goes through an agent. External agents read and write no topic file directly. The agent
layer and the DeepAgent harness's hooks (Section 1.9) enforce the standards Section 1 defines, whichever channel a
request arrives on.

The agent layer runs on the **DeepAgent** harness. It hosts the Librarian, the Topic Experts and the Learning agent, and
there is one way in, the API: the dedicated TUI and the Telegram chat are two clients of it. The channels and commands
those clients offer are Section 2.5's.

### 3.2 Agent hierarchy

```
┌──────────────────────────────────────────────────────────────────────┐
│               OPERATOR / EXTERNAL TOOLING (Section 8)                │
└──────────────────────────────────────────────────────────────────────┘
           │                    │                      │
           ▼ crosses topics     ▼ one topic owns it    ▼ a session closed
┌──────────────────────────────────────────────────────────────────────┐
│                         DEEPAGENT HARNESS                            │
│         (one way in, the API: the TUI and the Telegram chat)         │
│                                                                      │
│  ┌──────────────────────────────┐  ┌──────────────────────────────┐  │
│  │  LIBRARIAN (Section 3.3)     │  │  LEARNING AGENT (Section 3.5)│  │
│  │  routes; it writes nothing   │  │  reads one closed session    │  │
│  └──────────────────────────────┘  └──────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
             │              │              │
             ▼              ▼              ▼
      ┌────────────┐ ┌────────────┐ ┌────────────┐
      │  TOPIC     │ │  TOPIC     │ │  TOPIC     │   (each addressable
      │  EXPERT A  │ │  EXPERT B  │ │  EXPERT C  │    on its own)
      └────────────┘ └────────────┘ └────────────┘
             │              │
             ▼              ▼
      judgment + collaboration skills (overloadable)
        + harness hooks (mechanical)
        + read-only sub-agents: web search (4.5), conflict detection (6.3)
             │              │
             ▼              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                            PKB TOPICS                                │
│         references/ · notes/ · skills/  (the three pillars)          │
│         + topic.md, each topic's index.md, the root tags.md          │
│                  + the root's skills/ and sessions/                  │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.3 The Librarian

The **Librarian** is the root agent of the PKB, and it researches breadth-first across all topics. Its breadth is the
set of experts it reaches, and the depth is theirs. It holds no topic knowledge of its own, it holds no topic's search
tools, and it writes nothing into the tree. The two-phase workflow a Librarian session runs is Section 5's.

### 3.4 Topic Experts

A **Topic Expert** runs each topic, and it holds deep knowledge of that one topic and no awareness of any other. The
root registry, the other experts' answers and the operator's wider objective reach it only through what a brief carries
(Section 5.4), so the isolation is a property of the agent rather than a discipline the Librarian keeps on its behalf.
One default **Topic Expert template** serves the whole PKB. A topic that needs behavior beyond skill overloads overrides
the template with an `expert.md` in its topic root. The DeepAgent harness resolves this on the pattern the maintenance
skills use: take `[Topic Root]/expert.md` when it exists, and otherwise instantiate the PKB template with the topic's
context, `topic.md`, the common skills, and any skill overload. The resolution recurses, so a parent topic's expert
serves a sub-topic that holds no `expert.md`.

The expert combines two layers of capability:

1. **PKB general standards (common layer)** – the structure, metadata, tag, summary, and conflict rules Sections 1 and 6
   define. Harness hooks enforce their deterministic parts. Their judgment parts run as common, overloadable skills
   (Section 4).
2. **Topic knowledge (expert layer)** – domain knowledge about the topic itself, and the best ways to work its content:
   how to query it, which files answer which kinds of question, and its own ingestion rules.

Responsibilities:

- Answer a question about the topic from the breadth files (`topic.md` and the two pillar summaries) or the depth
  files (the topic's own `index.md`, the source maps), as the request requires.
- Ingest what the Librarian routes: classify it as a reference or a note, tagging a solution `type.solution`, draft the
  files, and apply the metadata and tags the standards set. Ingest it through the lens of this topic, because one source
  reaching two experts should produce two different extractions and neither is a duplicate of the other, and decline
  material that holds nothing this topic cares about.
- Answer the brief the Librarian opens its session with, which is the whole of what the expert is handed there (Section
  5.4). `answering-a-brief` shapes that reply (Section 4): the breadth files answer first, and every claim names the
  file or the page it came from, which is what the checks in code have to work on. An expert whose topic holds nothing
  on that question says so, because a silence reads as an expert that never ran, and it answers no other expert's brief
  at all.
- Work a session with the operator for as long as the work lasts (Section 2): brainstorm and plan over the topic
  (Section 5.7), reach outside it through `web-search` when the topic cannot answer, weigh what comes back against the
  topic's notes, object while the operator can still act on it, and take the experiments' results back as they come in,
  whether the operator reports them or instructs the expert to run the tools that fetch them (Section 8.1). `/close`
  ends the expert's part in that session, and the Learning agent proposes what it established for the operator to settle
  (Section 7).
- Land what that analysis settled. The Learning agent drafts, the operator approves the exact bytes in the analysis
  session, and the topic's own expert performs the write inside its own subtree, because no other agent may write there.
- Run the conflict-detection sub-agent over the tree when the session writes or changes a note or a reference, and
  settle what it reports with the operator in that same session (Section 6).
- Carry out the judgment side of topic maintenance (Section 4). Harness hooks enforce the mechanical side.
- Bring every artifact to the operator as Section 1 requires, the two things that land on instruction aside (Sections
  1.2 and 1.7): they read the exact text before it lands, and a tag the PKB has never used is proposed before any file
  uses it (Section 1.5).

An expert writes inside its own topic, and its session file sits outside it, in the root `sessions/` folder (Section
1.1), so a root tool performs every write into that file: the expert's running record while the session runs, and the
synthesis the Learning agent drafts and the operator approves after the close. The root `skills/` folder takes a write
on that same route (Section 4).

A source too large for one turn is ingested as a loop. Classify, draft, file works for a link and fails for a book,
because nobody reads what does not fit the context window and one turn writes a confident account of the part it saw. So
harness code drives the reading: it segments the source, extracts argument by argument through a bounded window, writes
each section as it goes, records what it skipped and why, and survives a run that dies part way through a 300-page book.
The expert stays the author of the extraction and stops deciding when it is finished. A source arrives as a path, and
anything binary is extracted to text first, with the PKB keeping both. A web page the operator points at is an inbound
source like a file: harness code fetches it once and stages the capture the way every other inbound source stages, so
the path rule holds from there on and the source folder keeps what the page said on the day it was read (Section 1.1). A
later pass over the same source takes the two modes Section 1.2 gives that file.

#### Example: a Cooking Topic Expert in action

One topic makes the responsibilities concrete.

The operator connects to the Cooking Topic Expert. They need no external tool, because the expert handles retrieval,
dialog and filing end to end.

- **Combine reference and experience**: the operator asks for a grilling recipe from an ingested cookbook. The expert
  pulls it from `references/` and applies the temperatures the operator filed for their own gas grill.
- **Ingest through its own lens**: the Librarian fans a food-science book out to Cooking and to Health. Cooking files
  what it says about heat, protein and technique. Health files what it says about nutrition.
- **Search for what the topic cannot answer**: the operator asks how long to dry-brine a brisket, and the topic holds no
  reference on it. The expert says so and writes three questions, harness code runs a `web-search` sub-agent on each and
  verifies every quotation against the text it holds, and the expert flags the two results that contradict the
  operator's own note and offers one article for ingestion. The session stays open, because the operator has cooked
  nothing yet.

### 3.5 The Learning agent

The **Learning agent** is one of the three roles this design runs, beside the Librarian and the Topic Experts, and it
owns the self-learning loop the whole system exists to close. It runs the analysis in a session the operator establishes
to it, the way every session is established, and every closed session waits in the learning queue until then (Section
7.7): it summarizes the closed session, distils the knowledge or the process worth keeping, applies its own judgment
skills before it asks anybody anything (Section 4.3), puts what it found to the operator, and files what they approve.
It holds no topic knowledge, as the Librarian holds none, and its competence is reading one closed session file and
deciding what the work established. Section 7 carries the loop it runs, and Section 7.1 the route its filings take.

The expert that ran the closed session does not do this, and the reason is the question rather than the file. That
expert is deep in its subject, and *what did this session establish* is a question about the session: it weighs the
argument the operator and the expert had, the places the expert was talked out of something among them, and an agent
grading its own transcript must be two things at once. The Librarian is no better placed, because its
competence is the registry and the interaction between two topics' answers.

Telling what is new about a topic stays that topic's expert's judgment, so the Learning agent reaches whichever experts
an analysis needs, the way the Librarian does, through the harness rather than through the tree. An analysis of a Topic
Expert session asks the one expert that owns it. An analysis of a Librarian session asks each expert that took part
(Section 7.3).

---

## 4. The Skills

### 4.1 Common skills and overloading

Every Topic Expert loads the common skills. They ship with the implementation and mount ahead of the PKB root's own
`skills/` folder, which starts absent (Section 9). The mount is read-only because it lives inside the installed
package: a write there edits the implementation for every PKB on the machine, so the permission layer denies it to
every agent. The tree's own `skills/` folders take writes.

The two homes differ in reach, and they differ in what can reach them. A topic's `skills/` folder sits inside that
expert's own subtree, so the expert writes there on the operator's word. The root's folder sits outside every expert's
subtree, where the catch-all deny refuses it and no tool routes a write there, so filling the root folder needs a root
tool. The root `sessions/` folder needs the same tool, because every session file lives there and no expert reaches it
either (Section 2.7).

Each skill is a folder holding a `SKILL.md`, so `skills/voice/SKILL.md` and `skills/brainstorming/SKILL.md`. That is the
DeepAgent harness's own format, and it buys two things without code of ours: progressive disclosure, where the prompt
holds the skill's name and description and the harness opens the body when a turn needs it, and override resolution by
name collision. Anything else the skill needs sits beside its `SKILL.md`.

Skills sit on the same side of the collaboration rule as notes, human-generated, AI-curated (Section 1.2): the
operator writes or approves every one of them, whoever typed the draft. A `SKILL.md` is no knowledge file (Section 1.3).

### 4.2 The thirteen skills that ship

A shipped skill is a starter draft: it makes something sensible happen on day one, and it says so at the bottom of its
own text. They sort by the pillar each one serves, seven pointed at the operator's subject and six at the procedural
pillar.

A skill name is an identifier rather than a description. This document uses *research* for the breadth-first work an
agent does across what it can reach, and *search* for reaching the internet, which is the skill named `web-search`.
Section 8.3's research agents are a third thing, the breadth-first consumers of context packs, and they run in the
external tooling that carries the instruction sets out.

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
- **`conflict-detection`** runs in a sub-agent when a session writes or changes a note or a reference, compares the
  write against the tree, and reports the pairs that need resolving into that session (Section 6.3). It resolves nothing
  and it writes nothing, and the report stays in the conversation, because the PKB keeps no conflict register.
- **`tag-proposal`** proposes a tag the PKB has never used by drafting the file that needs it, so the operator sees the
  tag and the file it would create in one decision.
- **`sub-topic-proposal`** proposes a split once `notes/summary.md` has stopped distilling and started enumerating. It
  brings the evidence and the full file assignment, and it creates nothing.

**Serving the procedural pillar: how the operator and the agent work together.**

Five of these six carry an objective from a wide question to an instruction set, and each ships once for two callers.
`brainstorming` and `planning` are one pair run at two scopes: the Librarian runs them over every topic the registry
names and over the internet, a Topic Expert runs them over one subject, and it is the same workflow either way (*One
pair of skills, two scopes*, below). The pair replaces two earlier shipped skills that each did half of this at one
scope, `research`, which surveyed the tree breadth-first, and `discovery`, which ran a brainstorming session against PKB
content, and the pair is the cross-topic research skill the design owed the Librarian. A topic that had already adopted
`discovery` under that name shadows nothing afterwards, and renaming its folder is the whole of the migration.

- **`brainstorming`** runs the divergent half of an objective. It surveys the map its scope binds, decomposes the
  objective into one question per callee, reads the merged answers across the sections for an approach that transfers,
  and ends the round with the operator. It returns candidate approaches, each with its trade-off and the files behind
  it, and two files that disagree on the question go to the operator rather than to the reading that suits the answer.
  It files nothing, and anything worth keeping goes back through the front door as ordinary ingestion.
- **`planning`** runs the convergent half once the operator has settled an approach, and it opens on the objective, that
  approach and the topics the approach named. It holds the instruction set's two parts, the rule that a result clause be
  checkable, the boundary each set states in full about what it consumes and what it produces, the file named behind a
  claim the tree supports, and the one replan gate a finding against the approach stops at (Sections 5.6 and 8.1). Every
  check on a draft runs outside the turn that wrote it.
- **`briefing`** writes what one callee is handed, and both workflows call it: the objective verbatim, the one question
  and the shape its answer must take, the boundary against the other briefs in the round, and the fenced quotation a
  later round carries (Section 5.4). Vague briefs are the documented cause of two sub-agents searching the same thing
  while a third searches something nobody asked for. It carries the line the permission layer already enforces, that
  sub-agents read and the expert writes.
- **`web-search`** reaches the internet, and both workflows call it at either scope (*Search comes from the model
  provider*, below). It plans nothing and files nothing: it takes one question, spends a context window on it, and
  brings back the pages it read, with the quotations code then locates in the bytes the harness holds.
- **`answering-a-brief`** is the callee's side of `briefing`, and every Topic Expert loads it. It answers out of the
  breadth files and goes deeper only where the question needs it, names the file or the page behind every claim, says
  the topic holds nothing rather than filling the space, and answers no other expert.
- **`voice`** carries the voice profile every draft is written in, narrowed per topic when one subject wants a different
  register from another. The operator corrects it from their own edits: one edit is a preference in the moment and the
  same change across three drafts is a rule the profile is missing, proposed with those drafts behind it and approved
  like any other text. It is the one shipped skill that knows something about the operator rather than about cooking,
  and Section 7.9 opens the rest of the pillar to a session's own proposals.

### 4.3 The judgment skills the design owes

Work that needs an understanding of content is defined once, as common skills every Topic Expert loads: Section 4.2
says what each one does and which of them ship, and Section 4.6 says where each resolves. Beyond those thirteen the
design owes judgment skills, in Tier 2's remainder and in the Learning agent's own set, and they mount and overload the
same way. Two of them are source extractions: an article, post or clip comes down to the single claim and the evidence
offered for it, and a manual or reference work to the parts the topic will consult, because a reader looks things up in
a manual rather than reading it. With `ingest-paper` and `ingest-book` shipped, that makes source extraction one skill
per kind of source – a paper, a book, an article or clip, a manual. `ingestion-classification` files any source no
extraction skill of its own covers. Tier 2's *briefing and synthesis* is two jobs and ships as two: `briefing` writes
the questions a fan-out asks, inside the live session and resolving through the expert's graph there, and the synthesis
section of a session file is drafted after `/close`, which puts that half with the Learning agent.

The Learning agent is the third role the project description defines, and three judgment skills belong to it rather than
to every Topic Expert, because it runs them over a closed session after `/close` and never inside the work (Sections 3.5
and 7):

- **Self-improvement** – the pass over one closed session that reads the record and asks what the work established.
- **Lesson proposal** – draft what is worth filing, from the session file's record of what the operator tried.
- **Skill proposal** – draft the `SKILL.md` for a way of working the session established, and say whether it belongs to
  one topic or to all of them (Section 7).

It drafts the session's own synthesis with the second half of Tier 2's briefing and synthesis pair, and it calls
conflict detection on what the session produced. All of them are overloadable on the same promise as the Tier 2 skills,
and they reach the promise by a different route: the Learning agent holds no topic, so it has no expert's graph to
resolve a skill through and each one resolves by name for the topic that owns the closed session (Section 4.6).

### 4.4 One pair of skills, two scopes

`brainstorming` and `planning` ship once and run wherever the objective sits, and three bindings harness code supplies
are the whole of the difference between the Librarian running them and a Topic Expert running them: the map the survey
step reads, the callees a fan-out reaches, and the writes allowed. Section 5 fills the three at Librarian scope and
Section 5.7 fills them at expert scope, each beside the agent that gets it, so one binding list cannot become three that
disagree. Weighing takes its filling from the scope as well, and Section 5.7 says what it becomes.

The rest is the same text, so no model reads which scope it is running at and decides how to behave, and the pair cannot
drift into two pairs that disagree about the same round.

### 4.5 Search comes from the model provider

`web-search` takes one credential, and the account already running the experts on Ollama's cloud models serves it, so
the design signs up no second vendor. The daemon reads the credential at startup and hands it down, on the path the
Telegram token already walks (`docs/how-to/`), and no agent, log or health endpoint sees the value.

A result arrives as the page's text rather than as a link to it. Ollama returns thousands of characters of content per
result, so harness code holds what a sub-agent read at the moment it read it, and code then locates a quotation in the
exact bytes the claim came from. The check stops being a best effort against a page that may have changed since, and
every admissibility rule below rests on that. Search returns extracted text, so ingestion still fetches a source itself:
the copy a topic keeps beside an accepted reference is the original bytes off the web, because search serves the session
and ingestion serves the filing (Section 3.4).

Published deep-research agents invent 3% to 13% of the URLs they cite, and 5% to 18% more of the URLs they give do not
resolve. In one shipped generative search product, 51.5% of the sentences it wrote were fully supported by the citation
attached to them. So no URL reaches a synthesis on a model's word. Harness code holds the text of every cited page, the
pages a search returned and any page a sub-agent opened beyond them, and verification fetches nothing itself. It locates
every quotation in that held text and drops the ones the text does not contain, and the file then records the citation
and the reason instead of the claim. It asks no model where a quote sits, because models miscount positions and invent
spans: the sub-agent returns the quoted text and code finds it. The same rule governs an extraction, where a quotation a
model produced is a candidate until code finds it in the source.

Retrieved text is the first thing here that strangers chose, so it travels fenced as data under a standing instruction
that nothing inside the fence is an instruction, and every quotation the operator sees sits in a quoted block with its
source attached. That is mitigation rather than a cure, and four structural bounds hold behind it: the write tool the
sub-agent never received, rule 8 in Section 1.8, quotation verification in code, and the exact bytes the operator reads
before approving them.

A single search carries a step budget and a wall-clock budget, and the session carries neither, because a long run is
a worse run. Factual accuracy on one measured search agent fell from 79% to 17% as its tool calls rose from 2 to 150,
between 77% and 94% of the steps in a long search add no new evidence, and a run that reaches the wrong answer runs
two to three times longer than one that reaches the right answer. Length is a symptom before it is a cost. Exhausting
either budget stops that search and the expert says it stopped short of the objective, which the operator can act on
while the session is still open, by asking for it again with a narrower question. Three sub-agents at once is the
default width, it bounds how many run together rather than how many questions a round writes, and the deployment sets
it rather than anything in the tree, because configuration an agent can write is configuration an agent can grant
itself.

One provider serves the models and the search, so one outage takes both. The local fallback model runs in the hour the
cloud is unreachable and search is unreachable in that same hour, so the searching stops: a search that cannot reach the
provider says so and ends, and the expert goes on answering from the topic's own notes and references on the local
model, at a fraction of the speed. The session stays open through all of it, and the next search runs the first time the
provider answers, so nothing polls and nothing queues.

### 4.6 Where a skill lives

The procedural pillar has two homes and the tree resolves both by name.

- **A skill about one subject** lives in that topic's `skills/` folder, visible to that topic's expert and to its
  sub-topics' experts, and to nobody else.
- **A skill about how to work** is a process skill and resolves at root scope, where every expert loads it, and the
  Librarian and the Learning agent with them. It ships in the read-only mount, or it lands in the PKB root's own
  `skills/` folder, which starts absent and is created by the first adopted copy or the first skill an analysis wrote.
  `brainstorming`, `planning`, `briefing` and `web-search` resolve there for that reason: a workflow is about no
  subject, so no topic can hold it. The scope reaches them from harness code rather than from the file, so the text the
  Librarian runs and the text a topic's expert runs is one text, and adopting it forks both callers at once.

Changing a shipped skill uses the same two homes. **Adopting** it copies it to the root, where every expert loads the
copy from then on. **Overloading** it copies it into one topic, where that topic's expert loads the copy and the other
experts keep the shipped default. Both shadow by name, and the permanent-fork warning attaches to the name.

Resolution reads the shipped mount first, then the root folder, then the topic's, and the most specific entry wins
whole-record: a topic's `voice/SKILL.md` *replaces* the root one for that topic rather than merging with it, the pattern
the DeepAgent harness applies to `expert.md`. A Topic Expert may overload any of the common skills with a topic
version, so the Cooking expert's summarization skill may require temperature and doneness tables in a recipe summary. An
overload extends the default with domain intelligence, a recipe-writing voice for Cooking or a tasting-session
brainstorming skill, and it redefines no general standard, because Tier 1 validates the output whichever skill version
produced it. `conflict-detection` is the exception, and it is the one worth watching: its whole output is a report into
a session, so Tier 1 has no file to validate and an overload can weaken the check Section 6 rests on.
`answering-a-brief` is the second of that kind, for the same reason: its output is a reply into a Librarian round rather
than a file, so an overload that stops naming the file or the page behind a claim leaves the verification with nothing
to check (Section 5.3). The same mechanism extends to the procedural-pillar skills of Section 4.2, `brainstorming`,
`planning`, `briefing`, `web-search`, `answering-a-brief` and `voice`.

The generated catalogs show the result when they are read together: a topic that overloads `voice` carries its own
`voice` in the catalog inside its `index.md`, the shipped one stays in the registry's catalog at the root, each level
lists what it declared and repeats no other's, and the pair says which one that topic's expert loads (Section 1.9).

The name decides whether a file forks anything, and the name that decides is the `name` in the file's own frontmatter.
The DeepAgent harness reads the three skill locations in order and keeps the last skill declaring a given name, so
`skills/my-brainstorming/SKILL.md` declaring `name: brainstorming` shadows the shipped `brainstorming` from the moment
it lands (Section 9), while `skills/brainstorming/SKILL.md` declaring `name: my-brainstorming` shadows nothing. Both
spellings look right in a directory listing and the harness only logs a warning, so the agent layer reports the
mismatch itself. It warns rather than refusing. An analysis proposal that would shadow a shipped skill says so in the
text the operator approves (Section 7.10).

A procedure hardens around the conditions somebody wrote it in, and those conditions move: the tool that failed gets
fixed, the operator changes how they want to be argued with, the topic grows past the shape the skill assumed. A skill
goes stale by failing when somebody uses it, so the evidence sits in a session file rather than in a note or a
reference, and the one route to a revision runs through session learning (Section 7). The conflict check reads no
skill, because a skill states no claim and contradicts nothing (Section 6.1).

---

## 5. The Workflows

A Librarian session runs in two phases and the operator owns the boundary between them. The breadth phase reasons over
the objective: the Librarian works it against the tag registry, the research sub-agents its rounds fan out work it
against the internet, the experts the registry names answer out of their own topics, and the operator and the
Librarian settle which approach is worth taking. The deep phase builds the instructions: the Librarian closes the
sessions to the experts the approach left out, interrogates the ones it kept, and the instruction sets come out of
that. Both phases are one pair of skills, `brainstorming` for the wide half and `planning` for the deep half, and a
Topic Expert loads the same pair (Section 4.4). Scope is what differs between the two callers, and a scope is three
bindings harness code supplies rather than a difference in the workflow: the map the survey step reads is the root
registry, the callees the fan-out reaches are Topic Experts and web-search sub-agents, and the one write is the
session file through the root tool. The rest runs identically at either scope, so a change to how a round works
reaches the expert's own brainstorming without a second edit.

### 5.1 Routing is a workflow rather than a decision

Three parts of the research belong to the Librarian, and each lands in a different place. Naming the topics that bear on
the objective, the second topic the operator did not think of among them, is the classify step every turn runs. Framing
the objective so that it decomposes into questions single topics can answer is what the Librarian says when it opens a
session on an expert. Recognizing that two topics' answers interact is what the reply carries back to the operator. The
merge is none of the three, because it composes what came back and judges none of it.

The operator's objective is what a Librarian turn ordinarily carries, and the four steps below run on it: the operator
states the objective, the Librarian names the topics that bear on it, and each of those experts answers from its own
knowledge. Material somebody wants filed runs the identical steps with filing as the answer instead, so one workflow
covers the research the objective needs and the routing an inbound source needs, and *item* below means whichever of the
two the turn carries.

A Librarian turn is four steps. The first is a judgment call and the other three are harness code that always runs.

1. **Classify.** The Librarian reads the root `tags.md`, the tag registry harness code regenerates (Section 1.6), and
   decides which topics the objective bears on, or which an inbound item concerns. The registry is its one read, and it
   is searchable by meaning rather than by name, because every `topic.*` node a topic folder backs carries the one-line
   summary lifted out of that topic's own `topic.md`. A tag tree on its own gives names, and the topic that answers an
   objective is often the one whose vocabulary shares no tag with it. The same file marks a topic that owns an
   `expert.md` with *(custom expert)*, so the Librarian walks the tree for nothing, and its cross-topic mappings,
   aggregated from the `related_topics` declarations, are where it notices the second topic worth involving. It answers
   with a routing call naming the applicable topics and a one-line reason, never prose. This is the one step where a
   model holds discretion.
2. **Fan out.** One decomposition of the objective issues both halves of the fan-out, and the Librarian can skip
   neither. Harness code invokes every applicable Topic Expert, reaching each one by opening a session on it the way
   the operator opens a session on an expert directly (Section 2.4), and the brief below is what that opening carries.
   Alongside them it starts one web-search sub-agent per question the registry cannot answer from inside the tree,
   through the shared `web-search` skill (Section 4.5). Each expert answers out of its own topic, and an expert whose
   topic holds nothing on the question says so. On a filing turn the same freedom applies to the material: a fan-out
   where two of four experts file and two decline is a success.
3. **Merge by attribution.** Harness code composes one reply from what came back: each expert's own answer under its
   own heading, named by its title and its agent id, and each web section under the query that produced it, carrying
   only the quotations code located in bytes the harness holds. The headings keep the two kinds apart, so a reader
   never takes a page for a filed claim. This is deterministic code rather than a second model writing a summary of the
   first. A model asked to write the merge reports that *"the Cooking expert checked the knowledge base"* when no
   expert ever ran. A reply assembled from real results cannot say that.
4. **Offer the experts directly.** The reply names the agents that answered, so the operator can carry on with one of
   them, "continue with the Cooking expert", rather than going back through the Librarian each time. Harness code opens
   that session on that expert, with its own file named for the narrower objective, and opens the channel that holds it,
   so the operator never goes looking for the session they just asked for. The Librarian session stays open and keeps
   its own channel (Section 2.5).

A research sub-agent cannot reach a Topic Expert. It holds no write tool of any kind, the permission layer having
handed it none, and an agent that cannot write must not command one that can. A question it could not answer off the
internet comes back as a question, and code turns that into an expert brief in the next round, which costs one round of
latency and keeps the read and write line where it stands. Budgets bind the sub-agent alone: a step cap and a wall
clock, because nobody is watching that loop, while a round ends in a turn that waits for the operator and needs no cap
at all.

A Librarian free to decide whether to delegate sometimes read the topic folders itself and answered from raw files, and
it lost the topic's skills, its `expert.md` persona and its voice. Everything that makes a Topic Expert an expert lives
one layer down, so harness code closes that.

Classification that comes back uncertain goes to the operator as a menu of the candidate experts, never a guess, because
filing knowledge in the wrong place cannot be undone. "None of these" is always an option, and it leads to the topic
gap: an inbound item that fits no existing topic is a new topic for the Librarian to propose and the operator to
approve, on the creation flow of Section 1.10, and that gated proposal is the Librarian's only write into the tree. The
instruction sets it drafts in the deep phase below are prose into the session file, on the root tool's route and with
the operator's approval, so the tree stays closed to it there too.

### 5.2 A turn says how deep it is going

A research turn carries a depth as well as a list of topics, and the reply states which depth it took so the operator
overrides it in a word. There are three. An `answer` turn is one the map the scope binds already answers, the registry
here and the topic's own breadth files at expert scope, and it ends there with the answer and the files behind it,
because reaching outside for something already filed spends a budget and invites a page that contradicts a note the
operator approved. A `breadth` turn runs one round, merges it and replies. An `objective` turn runs rounds until the
operator settles an approach, and the deep phase follows. A round that surfaces a gap or a contradiction goes deeper on
the next one.

### 5.3 A session runs that turn, and the operator is the round boundary

A **round** is one fan-out, what comes back from it, and the reply that carries it to the operator. The turn ends there,
the operator answers, and their answer is the next round, so a session runs as many rounds as the work needs and the
Librarian ends none of them by itself. Nothing caps the rounds, because a cap stops a loop nobody answers and every
round here ends in a turn that waits for the operator's answer.

The round is the `brainstorming` workflow's own loop, and a Topic Expert runs that same loop over its own subtree and
its own web sub-agents (Sections 5.7 and 4.4). The map binding is what makes surveying here the classify step above,
and the early exit out of a survey belongs to the `answer` depth instead, because the registry holds one-line
summaries rather than knowledge. Weighing is the one slot the scope fills (Section 4.4), and here it becomes saying
what you noticed, while the budgets read the same at either scope and bind the sub-agents alone. Everything else runs
the same at both scopes, verification included: code locates every quotation in bytes the harness holds, the page a
sub-agent fetched and the file in the tree alike, checks that every KB-relative path an expert cites exists, and holds
back a citation it cannot land, under its own heading with the reason it failed.

The Librarian cannot weigh the way an expert does. The expert has the topic's knowledge behind it and the Librarian has
none, so it cannot tell a genuine contradiction from two claims both true under conditions neither side states. It can
see that two answers bear on each other, because it holds both and neither expert holds the other, and that observation
is a question to put to the operator rather than a verdict to publish.

A session turn's reply carries three things: the merged answer, what the Librarian noticed across the sections, and the
question it would ask next. The last two are prose, and with the instruction sets the deep phase below drafts they are
the whole of what the Librarian writes, none of it authorizing anything. The operator reads the observation and asks for
the next round, or ignores it and asks for something else, so an interaction the Librarian imagined costs a sentence
rather than a round.

Code composes the merge and the Librarian then reasons over it, which is the work the operator opened on it for.
Holding several topics' distilled answers at once is the thing no expert can do, so the Librarian reads across the
sections for the approach one topic takes to a problem shaped like the objective, and it offers those crossings as
candidate approaches with the sections behind each one. The operator drives that pass: they hold the objective's
constraints and the Librarian holds none of the topics' knowledge, so the two of them settle which approach is worth
taking and which topics hold it. Reading two answers for an approach that transfers between them is a different act
from judging which of the two is right, and the second stays out of reach for the reason above.

The same approaches come back as the rounds go on. Ideation measured over four thousand seeds fell to roughly 5%
non-duplicate output, so the plateau is real, and the operator reads it off the candidate approaches each reply carries.
Calling it is theirs rather than code's: an automatic ranker of ideas, checked against the humans ranking the same
ideas, agreed with them no better than chance.

A round ends by naming what the operator can do next, and each of those destinations exists elsewhere in this design
already: keep exploring, settle on approach N, propose a new topic (Section 1.10), ingest this source (Section 3.4),
file nothing. Selecting one is the operator's act and nothing writes without it. The last three stay open in any phase,
so a topic gap the operator spots in the middle of the deep phase goes through the door it would have gone through in
the first round.

No round debates. The experts stay isolated until the merge, no critic persona reads another expert's answer, and
nothing asks a model to argue a position it does not hold. Across 3,600 logged multi-agent consultations the agents
repeated their opening position in 98.42% of cases rather than re-reading the evidence, so a debate round buys the
appearance of scrutiny and moves few answers. Isolation until the merge is the control that works, and this design has
it with nothing crossing between two experts but the one quotation a later brief carries.

A part of the objective no expert answered goes in the reply under its own heading, beside the experts' answers, named
as a gap, and the whole reply lands in the session's running record so the operator can chase the gap with a narrower
question while the session is open and the analysis reads it after the close (Section 2.7). The large-source ingestion
loop names the sections that yielded nothing for the same reason.

A later round is narrow by construction, because two topics' answers can be seen to interact only once those answers
exist. It reaches the experts the operator's answer names, plus a topic in the registry the first round missed, and each
brief carries the earlier claim as a quotation attributed to the expert that made it, asking what this topic holds about
it rather than whether it is true. No expert answers another and nothing goes back to the expert that made the claim, so
that fenced quotation is the whole of what crosses between them.

### 5.4 A brief carries the objective and withholds the rest

The **brief** is what the Librarian says when it opens its session on an expert, and it carries:

- The **objective**, verbatim from the session file, because a model asked to restate it restates the operator's beliefs
  along with it, and the expert then answers the beliefs.
- The one question this expert is asked, and the shape its answer must take. A first round asks the standing question
  of the breadth pass, what this topic holds on the objective and which of its approaches could reach it, answered from
  the breadth files rather than from the tree at large. A later round asks the narrow question the operator's answer
  raised. A deep-phase brief asks the expert to check an instruction set, and it carries the settled approach verbatim
  beside the objective, because the question it puts is what this topic holds that tells against that approach, and no
  breadth brief can ask that while the approach is still unsettled (below).
- Its boundary against the other briefs in this round, naming the other topic and never that topic's answer, so two
  experts are not asked one question and neither is asked nothing.
- In a later round, the attributed quotation of the claim the operator's answer picked up, fenced as data the way a page
  off the internet is.

Every answer comes back in one shape, whichever round asked for it. A claim a file in the topic holds names that file
and a claim off a page names the page, because code checks both: the path against the tree, the quotation against the
bytes the harness holds (Section 4.5).

The brief is the expert's whole starting context, and what it withholds is as deliberate as what it carries: the
operator's raw turn, their beliefs, the root registry, and every other expert's full answer. An expert returns its
findings rather than its working, so a round costs the Librarian a bounded amount of context, and an expert whose topic
holds nothing says so rather than returning silence.

### 5.5 The operator settles the approach, and the deep phase opens on it

The operator ends the breadth rounds by settling which approach is worth taking, and their confirmation is what carries
the session from `brainstorming` to `planning`. That is one session using its second skill, so nothing else marks the
boundary and no machinery sits on it.

`planning` opens on the objective, the settled approach and the topics that approach named, and the rounds behind them
stay in the session file where the operator can ask for them. The restatement is the skill's own instruction and it
happens once. A model that carries every surveyed topic's summary into the detailed work blends them, and a run that
repeated the accumulated state at every turn scored below one that restated it once at the end, where that single
restatement recovered about half of what splitting an instruction across turns had cost.

The Librarian then closes the sessions to the experts the approach left out. A model cannot take a session out of its
own request, so this is an action rather than a line a prompt asks for, and it is the same close said anywhere else
(Section 2.6). A closed session leaves the request altogether rather than sitting inside it marked inactive, because a
model choosing among four callees selects better than the same model choosing among eleven. Each of those sessions
enters the learning queue like every other closed session (Section 7), so one Librarian objective hands the Learning
agent several analyses at once.

An instruction set drafted before the operator settles has no approach to serve, which is the whole reason `planning`
opens on one. A model that committed to an answer inside the first fifth of a conversation scored 30.9 where one that
waited scored 64.4.

### 5.6 The deep phase drafts the instruction sets and the selected experts check them

The deep phase produces the plan, and the plan is the instruction sets: why the work is necessary and what it must
achieve, one per experiment and one per tool the approach needs (Section 8.1). The Librarian drafts them, because the
crossing between topics is what the approach rests on and the crossing is the thing it holds. A draft names the topics
its reasoning came from, so an expert checking it sees which claim of its own the Librarian used.

Three rules shape a set beyond its two parts. The result clause has to be checkable, so wording nobody can settle,
"properly configured" or "works well", comes back for a rewrite; most of the work this PKB plans has no mechanical check
at all, and the honest clause names the operator as the check and says so rather than manufacturing an assertion nothing
runs. Each set states in full what it consumes and what it produces, because each expert saw only its own subtree and
the reader works under conditions the PKB does not hold, so "as in set 2" points at something the reader cannot resolve;
a boundary states no method, which leaves the no-steps rule where it stands. And a claim a file in the tree supports
names that file, the way the experts' answers do, so the check below has a path to test and the operator reads which
line of the plan rests on the tree.

The checks on a draft are external, and none of them runs inside the turn that wrote it. Code scans for placeholders,
locates every quotation in bytes the harness holds, checks that every KB-relative path the draft cites exists, and
checks every `topic.*` it names against the registry. A draft that answers an open question the operator was meant to
answer is a line `planning` carries and the operator catches at the replan gate below, because no check in code tells
that answer from any other. A model grading its own draft measured worse than the same model checking nothing, where an
external verifier that was itself sound measured a gain, and the split above is that result written into the machine.

Each selected expert then checks the sets that touch its topic, on a brief of the ordinary shape: here is the
instruction set, here is the claim of yours it rests on, and here is the approach it serves. The questions are bounded
and factual, what this topic holds that supports the claim, qualifies it or contradicts it, and what it holds that tells
against the approach. Nothing asks an expert what is wrong with the draft, because a reviewer told to find gaps returns
some whether or not the work has any, and the standing rule that an expert holding nothing says so is the licence that
lets one return nothing here. The description asks the selected experts to check the approach as well as the plan, and
the last question is where they do it. An expert reads its own instruction set and never another topic's, and the
answers come back attributed under each expert's heading the way a wide round's do.

A finding against a claim revises the draft, and code reruns the checks above over the revision. A finding against the
approach revises nothing, because the approach is the operator's: the machine stops at one replan gate they see, with
the expert's words quoted, and they either amend the approach, which settles a new one and closes whatever it leaves
out, or accept the set as drafted. A disagreement between two experts the Librarian cannot resolve reaches them by the
same route.

The session hands the sets out as messages while it runs, and the ones the operator keeps land in the session file, in
the synthesis they approve word for word, with a root tool performing that write (Sections 4.1 and 2.7). Nothing about
them reaches a topic folder, and a set the operator drops leaves the record of the round that drafted it and nothing
else.

### 5.7 The expert brainstorms and plans over one subject

A Topic Expert loads `brainstorming` and `planning`, the pair the Librarian runs, and runs them over its own subject
(Section 4.4). It is one workflow at two scopes, and the scope is three bindings harness code supplies. The map the
survey step reads is `topic.md`, the two breadth summaries and the approach entries in the topic's own `index.md`, with
the depth files behind them when a question reaches that far. The callees a fan-out reaches are web-search sub-agents
and nothing else, because an expert reaches no other expert and no brief routes from one topic to another. The writes
are the topic's own subtree, plus the session file through the root tool.

The rest is the same text at either scope, so a change to how a round works reaches both callers in one edit: the depth
a reply states, one question per callee, the brief's four parts and what it withholds, retrieved text fenced as data,
the operator settling an approach before anything is drafted, the file or page named behind every claim, the placeholder
scan, and the instruction set's schema (Section 5.6).

One slot inside the workflow takes its filling from the scope, and code chooses the filling rather than the model.
Weighing is that slot: the expert compares a verified claim against the topic's own notes and references, claim by
claim, and says which disagreements genuinely oppose each other and which are both true under conditions neither side
states. The Librarian holds no topic knowledge and so cannot do that, and it says the two answers bear on each other
instead (Section 5.3). The budgets read the same at either scope, a step cap and a wall clock on a search sub-agent and
nothing on a round (Section 4.5).

A topic session reaches its deep phase on the same settling. The operator says which approach is worth taking, and
`planning` opens on it and drafts the instruction sets out of the work the session did (Section 8.1). No expert
interrogates the draft here, because the one topic it rests on is this one, so the external checks and the operator are
the whole of the check: code scans for placeholders, locates every quotation in bytes it holds, and tests every
KB-relative path the draft cites.

### 5.8 The notes weigh the results, and they never travel with the questions

The operator's notes outrank a reference (Section 1.8, rule 1), so the obvious move is to hand them to the search
sub-agents and let a search start from what the operator already believes. Measurement says to do the opposite. A model
told what the operator believes stops finding evidence against it: disconfirmation detection falls by 16 to 93
percentage points across four models once the belief sits in the prompt. Humans do this to themselves too, and a search
conversation that agrees with the searcher raises the rate of confirming queries from 16% to 43%, with the questions
asked doing the damage rather than the answers given.

A question a sub-agent carries therefore holds the objective and nothing else. The notes come back at the weighing,
where the expert reads a verified result against them. A prior multiplies the evidence. It chooses no evidence.

---

## 6. Conflict Handling

### 6.1 General rule

The practical and procedural pillars outrank the theoretical one (Section 1.8, rule 1), and that ranking does two jobs.
It fixes precedence when an agent acts, and an agent follows the practical and the procedural where their guidance
differs from a reference's. It also settles a contradiction between two claims, and that is the job of this section. It
says nothing about the order a context pack lists its files in, which Section 8.3 settles on its reader's needs. Human
content is the operator's own notes, the operator-approved breadth summaries, and the skill files, and the skills belong
to the first job alone: a skill says how to do something and asserts nothing about the subject, so no note and no
reference can contradict one.

A conflict is therefore knowledge against knowledge. An operator's note that conflicts with a reference is correct,
because the note is their own view of what the reference asserts, established under their own conditions, and the
operator edits a wrong note until it wins. The PKB overwrites no human content. It brings a conflict to the operator's
attention and settles nothing on its own.

Two cases get no answer from the ranking. Two notes that conflict have two human sides, so the expert shows the operator
both and they pick. A second reading of a source that contradicts the one in that source's file has no human side at
all, both sides are extractions, and the file at stake is a reference rather than a note. The handling is the same in
both: the pair goes to the operator and nothing changes until they answer.

### 6.2 What the check compares

A conflict-detection sub-agent compares on four axes, all of them knowledge against knowledge; Section 6.3 says when
they run.

1. `notes/summary.md` against `references/summary.md`.
2. Single notes against references.
3. Notes against notes, the same person at different times under conditions they did not write down.
4. References against references, two sources contradicting each other with no human side to decide them, and on a
   re-ingestion the fresh extraction of a source against that source's file on disk, argument by argument, because a
   bounded reader handed two long documents answers confidently about the part it read.

The check reads for meaning out of the two files it holds, and it recognizes two statements that are both true under
different conditions. Section 6.3 says what a finding does, and says why harness code picks the pairs and the model only
labels them: it goes back into the session that made the write, and nothing in the tree records it. A finding on a write
the analysis makes after `/close` reaches the operator in the analysis session instead, because the closed session takes
no more turns.

Running on the write leaves three cases unreached, and a check the operator asks for over a topic is the one route left
to them: two notes filed months apart that both predate this design, the operator's own direct edits, which no agent
sees, and truth that changes with no write at all, where a reference is superseded and nothing moved.

### 6.3 Detection runs after `/close`, and on the write

The Learning agent runs the check the project description names. It picks a closed session off the learning queue,
compares what that session produced against what the PKB already holds, and puts every pair it found to the operator
inside the analysis session (Section 7.2, step 4). That pass is the occasion for conflict work, because it is the one
place an agent reads a whole session's product against the whole tree.

Detection runs on the write as well, inside the session that made it, and this design adds that pass. A conflict a write
raises is answered while the operator is still in the channel and the text is still a draft, so the note lands correct
rather than landing wrong and waiting on `/close`, and a note in a session nobody ever closes gets checked at all. A
conflict lives no longer than the session that raised it or, for a write the analysis makes after `/close`, than the
analysis session (Section 2).

A session that writes or changes a note or a reference runs a conflict-detection sub-agent over the tree, and all four
axes of Section 6.2 run on that write. The first axis, `notes/summary.md` against `references/summary.md`, needs no
trigger of its own: one trigger is simpler than two, and an axis with a trigger of its own is the axis that never runs.
Harness code picks the candidate pairs and the sub-agent labels each one under the `conflict-detection` skill, holding
read tools and no write tool of any kind. Every pair the code picked reaches the session that wrote the file whatever
the label says. A check the operator asks for over a topic is the only other route, and nothing looks on its own: no tag
records a conflict, no queue holds one, and no timer re-reads a pair a write already compared.

The report names both files and quotes both sides. It separates the pairs that genuinely oppose each other from the
pairs that are both true under conditions neither file states, because the two ask the operator different questions, and
it proposes no conflict type and no confidence score, because nothing stores either.

A note the analysis files after `/close` is a write like any other, so the same check runs and the operator answers it
in the analysis session they establish to the Learning agent (Section 7). Every write reaches the tree through a
session and the PKB has no other door (Section 1.7), so every report has somebody to report to.

### 6.4 What resolving one means

The operator resolves it, in the session that raised it or in the analysis session that follows the close, in one of
three ways. They edit one of the two into the version that holds, whether it is on disk yet or still the text the
session is filing. They say which of the two holds and why, and nothing changes, because the other was already right. Or
they say both hold, and the note gains the conditions that separate them, which is a write the operator approves like
any other.

Silence is not a resolution. A session that ends with a conflict it did not settle names both files in its running
record and says so, because nothing else remembers it and the next session starts on a tree that looks settled.

One run, end to end. The operator dictates a note to the Cooking Topic Expert: preheat the grill for 15 minutes. The
expert drafts the text in the operator's register, the draft is what starts the check, and the sub-agent labels the one
pair the code picked, `references/grill-basics/grill-basics.md`, which says ten minutes is sufficient. The expert
reports the pair into the session and says whether the two genuinely oppose each other. Neither file names a pit, so
both may hold under conditions neither writes down. The operator answers that their pit runs cold and 15 minutes is
what it needs, the third resolution: the note gains the condition that separates them, the expert redrafts, and the
operator approves the exact text, which lands with the ordinary frontmatter of Section 1.3. Had they decided the book
was right, they would have edited the draft to ten minutes before approving it, and had they wanted to think about it,
the record would say the pair is open. The reference is untouched in all three, since the book was never wrong about
the book, and two traces survive: a note that reads better than the dictated one, and the running record naming both
files and what the operator decided.

### 6.5 A page with no standing is not a conflict

A session that searches compares what it found on the internet against the topic's notes. A page that contradicts a note
raises the pair and changes nothing: the expert shows the operator both quotes side by side and leaves the note byte for
byte as it was. A conflict starts once the operator accepts the source, which feeds the theoretical pillar through
ordinary ingestion, lands the page as a reference, and puts the write-time check on it like any other write. The same
holds for a lesson the operator dictated during the session: the analysis raises the pair, quotes both sides, and
changes nothing until the operator says which one stands (Section 7.2, step 4).

Two properties of models make this the only safe handling.

- A model handed retrieved text abandons its own correct answer for it, at a rate running from 0.16 to 0.31 measured
  across six domains and six models. A page with no standing therefore changes no note of the operator's, because
  letting it would hand that bias write access to a tree with no undo.
- A model given two passages that contradict each other misses the contradiction, scoring under 11% on pairs human
  annotators had already marked. The check is a reporter and never an editor for that reason: the operator reads both
  quotes and decides, a pair the code never picked is expected rather than a defect, and a pair it picked reaches
  the operator whatever the sub-agent labels it.

### 6.6 Escalation from an external agent

Section 6.1's general rule reaches an external agent unchanged: the pair goes to the operator and nothing changes until
they answer. An external agent that finds two files in its pack disagreeing on the question it is working raises the
pair rather than picking the reading that suits its task. Nothing in the tree marks the pair (Section 6.7), so a pack
carries no flag and the raising rests on the reader noticing. That is a real loss against a durable flag, and what it
buys is a tree where nothing waits flagged for later. Raising it means opening a session (Section 2), and it settles
there like any other conflict.

### 6.7 What the PKB does not do

- It builds no separate conflict registry, so nothing pollutes a context window after the resolution.
- It puts no marker on the two files that held the conflict, it marks neither as a loser, and it stores no resolution
  text outside them, so the note content is the true state of knowledge. The session's own file says what that session
  raised, and nothing else remembers it (Section 2.7).

---

## 7. The Self-Learning Loop

The loop distils a lesson: practical knowledge, which a note carries, or a repeatable skill, which a `SKILL.md`
carries. A session's synthesis is a different thing and records what the session worked out, and the analysis drafts it
in the same pass it distils the lesson from the same file (*The analysis cycle*, below). One mechanism reasons over all
three pillars, and the self-improvement skill holds that competence (Section 4.3).

### 7.1 The Learning agent runs the analysis, and it holds no topic

The operator establishes an analysis session to the Learning agent, the way every session is established (Section 2.5),
and it attaches in the learning channel. The agent reads one closed session file and decides what the work established.

Holding no topic knowledge decides the rest of the cycle. Everything the analysis needs about the subject comes out of
the tree or out of a skill resolved by name, the closed session's topic ahead of the root, so a Cooking session distils
on Cooking's own overloads while the agent reading it knows nothing about cooking (Sections 4.3 and 4.6). The
conflict-detection sub-agent it fires reads the pairs themselves, so the domain knowledge the labelling needs comes out
of the files rather than out of the agent that asked for it, which is the same route the check takes inside a live
session (Section 6.3).

Filing works the same way. The Learning agent has no subtree, and widening one agent to write into every topic would
undo the confinement every other agent works under, so a note the operator approved lands through the topic's own expert
and a root process skill or a session file lands through the root tool (Sections 3.4 and 4.1). The expert performs the
write rather than redrafting it, because the operator approved bytes and a second drafting pass would change them. Both
root folders are denied to every agent and the root tool is the only way into them. The Librarian's write capability
stays at zero, as it does for topic creation, and widening an expert's permission to write outside its own topic is
refused, because that loosens the subtree confinement on every turn to serve one filing the operator instructed.

### 7.2 The analysis cycle

1. **Read the file from the beginning.** The analysis reads the whole session file, first turn to last, and never a
   previous extraction. This is why it runs once and at the close: one-shot consolidation beats streaming, chained
   abstractions compound, and a distillation of a distillation carries an error nobody can trace back to the experiment
   that started it. A file too long for one turn walks through the bounded reader of Section 3.4 and comes back as one
   consolidated account, read beside the file.
2. **Draft the candidates.** The self-improvement skill drafts each kind with Section 4.3's drafting skill for it, the
   session's own synthesis among them, resolved topic-copy first so the topic's overloads apply. It proposes what the
   session learned and what is worth filing. The running record holds the operator's own words wherever they dictated a
   lesson during the session, and the draft carries them.
3. **Ask the topic's expert what is new.** The expert that owns the topic answers on the grammar the ingestion loop
   already uses: something new, something better, something that contradicts what I hold, or nothing (*The analysis of a
   Librarian session fans out*, below).
4. **Pair it against what the topic holds.** Harness code selects the notes a candidate has to answer for (Section
   6.2), and Section 6 governs one that contradicts a note the topic already holds: quote both sides, change nothing,
   let the operator settle it. The expert's objection arrives with the candidate rather than after it, because the
   operator reads both in one sitting.
5. **Put the candidates to the operator.** The analysis raises them in the session the operator established to the
   Learning agent, quoting the file: the objective, the close date, how long ago that was, and the running-record
   entries each candidate rests on, sliced out by code rather than summarized, because an operator reviewing a session
   they no longer remember needs the evidence in front of them. Harness code renders each file that would land and the
   session's own synthesis with them, and the operator reads the exact text. Three candidates ask three times, and the
   operator may take one and drop two. A candidate that is half right is worked until it is right, because the two of
   them are in a session.
6. **Write.** The files land and the hooks regenerate the indexes and the registry (Section 1.9).
7. **Append the distillation, then `/end`.** The analysis writes into the closed session's own file what it distilled
   and how it got there: the candidates it drafted, the ones the operator took and the ones they dropped and why, and
   the paths the kept ones landed at. The operator then says `/end`, which seals the file and ends the analysis session
   (Section 2.6). An analysis that reached step 5 with nothing puts nothing to the operator: it writes a distillation
   section saying the session established nothing, and harness code seals the file without asking them for anything,
   which archives it on the one definition Section 2.6 gives.

### 7.3 The analysis of a Librarian session fans out

A Librarian session took several experts, so its analysis asks each of them what its own topic takes from the session,
on the same grammar step 3 uses for one topic: something new, something better, something that contradicts what I hold,
or nothing. An expert that takes nothing leaves no folder and no stub. Each note lands inside its own topic, so the
Librarian still writes nothing and the Learning agent, which has no subtree of its own, writes nothing either. The
fan-out is one pass rather than the rounds a live Librarian session runs, because a round opens when the operator
answers and a closed session takes no more turns from them.

A Librarian session's analysis therefore proposes a set of notes. The operator approves each one's text on its own, so
they take some and drop others and a rejection on one changes nothing about the rest. Four kept notes means four texts
to read.

A session that yields a portfolio lesson and a trading lesson yielded two lessons, the shape one book reaching two
topics takes. An insight that spans the topics rather than decomposing across them lands in one topic with
`related_topics` naming the others, and the hooks aggregate that into the root registry (Section 1.9). A root process
skill is the one other thing such an analysis may propose, and something the crossing taught about running a round is
the kind it proposes most naturally, because the Librarian's own competence has no topic to live in. `brainstorming` and
`planning` ship, so such a proposal usually adopts one of them into the root folder with the lesson written in rather
than writing a new skill, and adoption forks that skill for both callers from then on (Sections 5 and 4.6).

The Librarian closes the sessions to the experts the settled approach left out (Section 5.5), and each of those enters
the learning queue like every other closed session. Most of them establish nothing and end in the silent path
of step 7: the expert answered one brief out of files it already held, and an answer drawn from `notes/summary.md`
teaches that topic nothing it did not hold that morning, so harness code writes the distillation saying so and seals the
file without putting anything to the operator. One that did more reaches the operator like any other analysis,
in the learning channel, with each text approved on its own. The load lands on the queue rather than on them: one
objective closes several sessions at once, the worker drains them one entry at a time, and the channel holds one
analysis at a time (below).

### 7.4 The default is silence

Nous Research shipped Hermes Agent in February 2026, and it is the nearest shipped system to this one:
agent-curated memory about the human, plus skills the agent writes for itself after hard tasks. Its prompt states its
prior in capital letters: *"Be ACTIVE. Most sessions produce at least one skill update, even if small. A pass that does
nothing is a missed learning opportunity, not a neutral outcome."*

That prior is right for Hermes and wrong here, and the substrate is the reason. Hermes writes into a skill library with
an archive and a rollback, so somebody reverts a bad write. This design writes into a tree with no undo, where a bad
note reaches every implementation pack on its topic and stays until the operator rewrites it by hand. So the prior
inverts. The analysis runs on every session and the filing does not, and a session that files nothing has missed
nothing.

### 7.5 Five things a session produces that look like knowledge

The same Hermes prompt carries an exclusion list. Each entry names a way a session manufactures something that reads
like a lesson. All five hold here, and the first is the dangerous one:

1. **An approach that never worked.** The session tried several things, none worked, and it ended by telling the
   operator to check by hand. Hermes names the harm of writing those attempts up as a reliable workflow:
   *"That presents an untested sequence of failures as validated guidance a future session will trust and repeat."*
   Three briskets that all came out dry support a note about three briskets. They support no note about how to
   dry-brine.
2. **A failure the environment caused.** The smoker's thermostat read 40 degrees low that week, or the data feed was
   down that afternoon. Filing that as a property of the technique blames the method for the machine.
3. **A verdict that a tool cannot do something.** Hermes on why this one earns its own entry: *"These harden into
   refusals the agent cites against itself for months after the actual problem was fixed."* A note saying the search
   provider returns nothing on a subject outlives the outage that produced it, and every later session reads it as a
   fact about the subject.
4. **An error a retry cleared.** *"If retrying worked, the lesson is the retry pattern, not the original failure."* The
   error is noise, and the patience might be knowledge.
5. **The story of one afternoon.** A narrative of what happened once is no rule, and `notes/summary.md` holds rules. The
   loop in Section 2.1 asks the operator to cook three times before distilling for this reason.

A sixth exclusion is already law in Section 1: nothing off the internet becomes a note (rule 8 in Section 1.8).

### 7.6 How an entry improves an expert

Each lesson improves the expert agents, and each kind reaches an expert by its own route:

- A note reaches `notes/summary.md` through the `summarization` skill, and a reference reaches `references/summary.md`
  through the same skill. An implementation pack loads `notes/summary.md` first, and a reference's depth file reaches
  that pack too (Section 8.3). The expert reads the distilled rule on every turn that touches the topic.
- A skill's name and description sit in the expert's prompt from the start of the turn (Section 4.1), so a skill shapes
  the next draft before anybody asks a question (*A session may also teach the system how to work*, below).

A session file reaches no expert, and that is a gap. It belongs to no topic, so nothing routes it back to the topic's
own expert the way `notes/summary.md` reaches every turn, and Section 8.3 carries its synthesis section last of what
a Research Pack holds and the rest of the file not at all. The three gaps of Section 10 are the others.

### 7.7 The learning queue holds work; the learning channel holds the conversation about it

Time starts nothing here, and a closed session is the one trigger (Section 2.6). The queue is how a session ends rather
than a place a session lands when something else failed. The worker drains it when it reaches the entry, and the
analysis reaches the operator when the operator is available. The filing bar runs inside the analysis rather than in
front of the queue, so the queue holds work and the learning channel holds the conversation about what cleared the bar.

The split between the two is the design's expectation rather than a measurement: roughly one in seven closed sessions
establishes anything, and a filing rate above about one in five is evidence that the bar is broken rather than generous.
So only a candidate that cleared it asks the operator to decide anything. Put every closed session in front of them as a
candidate instead and the review list is mostly nothing, so the operator stops reading it and the one that mattered goes
unread with the rest, which is how a lessons-learned archive dies. The bar governs what the analysis raises by itself
and governs nothing the operator asks for: a session they want something filed out of is filed on their word, and the
bar keeps the analysis from filling their queue rather than keeping them out of their own knowledge base.

The analysis session attaches in the learning channel rather than in a topic. The operator's first thought was a special
topic `kb-learning`, and they ruled against it. A topic is an expert, a `topic.md`, three pillar folders, an agent id,
an entry in the registry the Librarian routes on (Section 5.1), and a write confinement drawn around its own subtree,
and the place these sessions run is none of those. Making it a topic produces an expert nobody wants to talk to, a
routing target nobody should route to, and folders that hold nothing. The Learning agent is none of those things
either: it holds no `topic.md` and no pillar folders, the registry never lists it, and the Librarian never routes to it.

The channel exists so that housekeeping never interrupts a topic conversation, and so the operator has one place to go
for what the system has learned lately. Its sessions run one after another rather than at once, because a channel holds
one session at a time (Section 2.2), and each one names the closed session it came from and the topic that owns it,
because the channel says neither.

The queue holds every closed session, with no cap and no expiry. An entry waits as long as it waits and is analysed
whenever the operator gets to it, because a session worth closing was worth doing, and a queue that drops the oldest
entry drops the one the operator has had least chance to see. Queuing at the close takes most of the staleness out, and
one thing guards the far end: the analysis quotes the file rather than asserting a conclusion (step 5 above). That guard
carries the whole load because the file holds the whole arc, so an operator settling a session they last touched in
March reads what happened rather than recalling it.

### 7.8 Two guards Hermes puts in code, and both belong here

Nothing rewrites a file whose current text it has not read this turn, and `RS-141` is Hermes's own id for that rule.
Hermes refuses a patch to a file the reviewer has not loaded verbatim in that same turn, because *"the autonomous review
fork is allowed to evolve skills, but it must not patch or rewrite content it has only inferred from the transcript."*
An analysis proposing to revise a note the operator filed in March works from an impression of that note, the impression
came out of a conversation that has since compacted, and the operator may have edited the note meanwhile. Read the file,
or leave it alone. The rule lives in harness code for the reason every other guarantee here does: a skill is a file the
operator may adopt and then edit, and a guard written into a skill leaves the day they edit their copy.

Authorship decides what may be curated, and `RS-142` is Hermes's id for that one. Hermes tags every skill write with its
origin, so curation touches the skills the process itself created and no others:
*"Skills a user asks a foreground agent to write belong to the user and must never be auto-curated."* Section 1 already
draws that line as the collaboration rule. Harness code writes `AUTHORSHIP.md` beside the `SKILL.md` and reads it
back, and authorship says whose hand put the content there. It is a second file rather than a block inside the skill,
because the `SKILL.md`'s two fields leave no room for it and its body loads into a model's prompt, where an origin block
would read as one more line of procedure. It is a skill file like the `SKILL.md` beside it, exempt from PKB frontmatter
and from every index and tag artifact, and the catalog generator and the tree walk read only `SKILL.md` inside a skill
folder, so it trips no `LEGACY_SKILL_LAYOUT` warning (Section 1.3). A folder with no `AUTHORSHIP.md` is the operator's.

The two guards reach a skill revision unamended. A proposal to revise `skills/session-loop/SKILL.md` loads that file's
current bytes in the same turn and derives the revision from them, and it amends a folder carrying the `AUTHORSHIP.md` a
session wrote and no other. A session that wants the operator's own skill changed proposes the change in conversation
and leaves the edit to them.

### 7.9 A session may also teach the system how to work

A session that established *brisket holds at 250* fed `notes/`, and a session that established *a better way to run a
session* has something for `skills/`, which the analysis may propose.

A note says what is true and a skill says how to work. That line decides every proposal, and the test that separates the
two is who acts on the draft first. A skill shapes how the expert works before anybody asks a question, because the
DeepAgent harness holds its name and description in the prompt from the start of the turn and opens the body when the
turn needs it (Section 4.1). A note answers a question about the subject once somebody asks one, and harness code
fetches it when it is relevant.

Run the test on the three cases that matter. *Brisket holds at 250 for four hours* waits for somebody to ask about
brisket, so it is a note. *Always preheat the grill for 15 minutes* reads as an instruction and is still a note, because
the operator at the grill acts on it and no draft changes shape until they ask. *Ask for the pit's own thermometer
offset before drafting any smoking lesson* changes the expert's next draft first, so it is a skill.

A procedure the operator proved by doing is a solution note, tagged `type.solution` (Section 1.5), and it becomes a
skill once it directs the expert's own drafting rather than the operator's own doing. Section 4.6 decides where the file
lands, so the filing decides one thing, the scope.

The same test settles where a lesson about the operator goes. `voice` keeps the operator's register and nothing else
(Section 4.2), so a procedure about running a session goes to a root process skill and a preference about wording goes
to `voice`. Splitting them costs one judgment in the analysis. Merging them would put *ask for the pit's thermometer
offset before drafting* into the file every draft is style-checked against.

### 7.10 A wrong skill is worse than a wrong note

A wrong note is wrong about brisket: somebody pulls it into a pack, cooks the thing, and the result on the plate
corrects them the same afternoon. A wrong skill is wrong about every session that follows.

A session in March concludes that the pit runs hot and writes a skill saying *treat every stated temperature as twenty
degrees high before drafting*. The operator reads it once, agrees, and it lands. In April the operator replaces the pit.
Every draft after that subtracts twenty degrees from a correct number, in every session on that topic, before anybody
asks a question. The drafts carry no mark saying which skill shaped them, so the operator reads a wrong temperature and
corrects the draft rather than the file.

No check catches that, and none is meant to. The check compares claims, and *treat every stated temperature as twenty
degrees high* instructs an agent rather than asserting anything about the pit, so no note in the tree agrees or
disagrees with it (Section 6.1). A skill fails where somebody uses it, so the session that hits it is what reports it
(Section 4.6), and the guard in front of it is the approval the operator gave in March.

That approval therefore asks in its own words. *A lesson is ready to file* is the wrong sentence in front of a file that
changes how the expert works on every later turn, so the skill filing names its scope and any shipped skill it would
shadow, with the exact `SKILL.md` text underneath. Agreeing in the analysis that the session learned something is
agreement about the lesson, and the procedure the expert then wrote from it is a second object the operator has not read
yet.

A skill that shadows a shipped one by name says so three times: in the proposal, in the exact bytes the operator
approves, and in the line the file opens with (Section 9). Nothing in the tree records the swap as a swap (Section 4.6),
and the third statement matters most, because whoever approved the skill is not the person who opens that file six
months later. The collision is never refused, because improving a shipped skill for one topic is the most useful thing
an operator does. The worst version is a shadow of `conflict-detection`, which runs on every write, so a bad shadow
there removes the only reader that would have caught the bad note rather than one of several. Exclusion 3 above is the
same failure at a smaller scale, and a skill is where that hardening happens, because a skill is the file the system
follows without being asked.

---

## 8. Handing Work Out

### 8.1 A session writes instruction sets and executes nothing

An instruction set carries two parts and no third: why the work is necessary, which is the piece of the objective it
answers, and what it must achieve, which is the result that would satisfy that piece. It states no steps and names no
tool, because whoever carries it out works under conditions the PKB does not hold, and a set that dictates the method
turns the reader into a hand rather than an implementer.

A session produces as many instruction sets as the objective needs, one per experiment and one per tool, and the
operator keeps or drops each on its own, so a session that proposed three experiments and saw one run keeps one set and
records the two they turned down. A Librarian session drafts them in its deep phase and the selected experts check each
one against their topics (Section 5.6), and a Topic Expert session drafts its own out of the work it did with the
operator.

The operator follows an instruction set, at the smoker or at the keyboard, or external tooling implements it: another
agentic system, or a coding tool (Section 8.2). The results of each experiment or tool come back into the session on two
routes: the operator reports them as conversation, or instructs the agent to run the tools that fetch them, and the
session uses them to advance the objective. A set goes out of the live session on the route any turn takes, and its
results land back in that same session while the work is still going, because a session that cannot use the result
cannot advance the objective (Section 8.2). An instruction set stays a message until the operator says it is worth
keeping, and it then lands inside the session's own synthesis, where a later reader finds the ones that survived beside
the record of the ones they dropped. The PKB grows no task queue, no runner and no status field, because a knowledge
base that executes has to remember what it is halfway through, and that is a second record of the work that can disagree
with the first.

### 8.2 Results return into the session

A session hands out instruction sets and the PKB executes nothing (*A session writes instruction sets and executes
nothing*, Section 8.1). The operator or external tooling picks a set up out of the live session on the route any turn
takes and carries the work out under conditions the PKB does not hold. The sets the operator keeps land in the
synthesis the analysis after `/close` drafts and they approve word for word (Sections 2.7 and 7.2), so the crossing
runs in both directions through one artifact and one file.

An external agent reaches the PKB the way every other counterpart does (Section 2.3): it opens a session on the
Librarian, or on a Topic Expert when it knows the topic that owns the work, and the PKB has no door that is not a
session (rule 8, Section 1.8). Whoever establishes a session and sets its goal is that session's operator, a human or an
agent alike, so every approval and write rule in this document reaches an external agent unchanged. Section 1.2's three
approval modes turn on whether the operator reads the exact text and say nothing about who the operator is, and that
lets an external agent approve a write no undo reverses.

### 8.3 Context packs

An instruction set states what the work must achieve and carries none of the topic knowledge behind it, so an agent
carrying one out asks for what it needs to read. A Topic Expert assembles a context pack on request, matched to what the
reader is doing with it, and the pack is a read path beside the handover rather than the thing the PKB hands over.
Research agent and implementation agent name the two readers by which side of rule 2's split they read on, brainstorming
or implementation (Section 1.8), whether a human follows the set or another agentic system implements it.

- **Research agents (breadth-first)** receive a Research Pack. It holds `topic.md` less its skills section, the
  relevant subtrees of the root `tags.md`, `notes/summary.md` and `references/summary.md` for each relevant topic, and
  a session's synthesis last of all. A subtree travels with the one-line summary on every node a topic folder backs
  (Section 1.6), so a research agent selects among topics off the same surface the Librarian routes on. It reads no
  topic's `index.md` unless it asks for one.
- **Implementation agents (depth-first)** receive an Implementation Pack, once the task is defined. It holds
  `notes/summary.md`, the selected topic's `index.md` in full less its skills catalog, the
  `references/[source-name]/[source-name].md` files, and the relevant solution notes. `notes/summary.md` loads first,
  because the operator's rules bound the work its reader is about to do.

The split is rule 2 in Section 1.8, and it bounds a pack. A breadth reader handed the depth files of one topic
receives more than one context window holds, and a pack that recurses into sub-topics multiplies that. A pack therefore
carries a size budget and truncates at an entry boundary rather than mid-file, and it names what it omitted.

A pack orders its files on its reader's needs, and rule 1 in Section 1.8 says which claim holds against another rather
than what a pack lists first (Section 6.1). An Implementation Pack places the reference maps ahead of the solution
notes, because a solution note cites the theoretical material it rests on and the referenced material belongs in context
ahead of the note citing it. No pack carries the procedural pillar, because of who reads a pack rather than the
pillar's standing in the tree: a skill instructs the agents that work this PKB, and a consumer of a context pack works
elsewhere. Both skills catalogs fall under that reason (Section 1.9): the pack builder drops the catalog section out of
the topic `index.md` an Implementation Pack carries. A Research Pack takes the topic subtrees it needs rather than the
whole registry, so the registry's catalog of the shipped skills never reaches it. The skills section inside `topic.md`
is the procedural pillar's own breadth file (Section 1.4), so the builder drops it on the same rule and a Research Pack
carries the rest of that file without it.

A session's synthesis ranks last of what a Research Pack carries (Section 7.6), because it records how the topic came to
know a thing rather than what the topic knows, and the running record and the distillation around it travel in no pack
at all. A session file belongs to no topic, so the pack builder lifts that one section by name rather than through a
topic's walk. A lesson a session filed is an ordinary note, and it enters a pack as one.

### 8.4 Knowledge feedback

The operator or the tooling reports and the Learning agent distils. After a project, or after a retrospective, they
bring back into the session what the work produced: what it ran and what happened, plus any observation or reference
the work turned up, "referral program required legal review" being one of them. Those land in the running record on the
turn they arrive (Section 2.7), and a source the operator accepts goes through ordinary ingestion from there (Section
3.4). The operator may instead instruct the agent to run the tools that fetch the results, and that squares with the
executes-nothing rule: the PKB implements and executes nothing itself, and fetching a result is the agent running a
tool on the operator's instruction inside the session (Section 2.1).

A general rule, and a reusable approach filed as a note tagged `type.solution`, are what the analysis after `/close`
draws out of that record, on the bar and the exclusions of Sections 7.7 and 7.5, with the operator approving the words.
A project's session closes with `/close` and enters the learning queue like every other session, so the loop has one
entrance and a project's experience reaches the tree the way every other session's does. A write proposed inside the
session follows the outcomes and the approvals of Section 2.8's table, and Section 1's standards hold here as on every
other channel, rule 8 in Section 1.8 included.

The five exclusions in Section 7.5 bind a project retrospective as hard as they bind any other session's analysis, and a
retrospective is where they break most easily. A project that tried four approaches and shipped none of them still
reports the fourth as the one to recommend, and a rule distilled from that reads the same as a rule somebody earned.
Whether the record says the four never shipped rests on the report the operator or the tooling brings back, and nothing
here can check it, so the exclusions reach only as far as the session file does.

---

## 9. Layout and Bootstrapping

### 9.1 The full tree

The full PKB is a tree of topic roots, each following the standard structure of Section 1.1:

```
KnowledgeBase/
├── tags.md                 # Global tag registry, the Librarian's one routing read (machine-maintained)
│                           #   tag tree, a summary per topic node, cross-topic mappings,
│                           #   plus a catalog of the shipped skills and the root's own (1.6)
├── .inbox/                 # Staging for sources on their way in – dot-prefixed, indexed nowhere
├── (optional) skills/      # PROCEDURAL – process skills every expert loads, plus adopted ones. Starts absent
│   └── [skill-name]/       #   one folder per skill (session-loop/, ...)
│       ├── SKILL.md
│       └── AUTHORSHIP.md   #   Harness-written: whose hand put the skill there (7.8)
├── (optional) sessions/         # One file per session on the Librarian or one expert, whole life. Starts absent
│   └── [objective-title].md     #   Objective and experts, record, synthesis with the instruction sets the
│                                #   operator kept, distillation
│                                #   topic: "(session)", a tag per expert (2.7)
│                                #   the operator names it, and /name renames the file (2.5)
│                                #   a session on the Learning agent opens no file of its own (7.1)
├── [Topic Root]/           # Section 1.1's structure, once per topic and again per sub-topic
└── (other topic roots...)
```

That is the whole tree, and the learning queue is not in it. A closed session enters the queue, the worker drains it one
entry at a time, and the queue lives in harness state rather than in a file, so nothing under a topic root records
that a session is waiting to be analysed (Section 7.7).

The root holds one derived file, and the registry is it: the tag tree, the summaries and the skills catalog in one
place. So every `index.md` in the tree belongs to a topic and sits at that topic's root or at any nesting depth below
it, and nothing generates one at the root (Sections 1.3, 1.6 and 1.9).

### 9.2 Bootstrapping an empty PKB

The PKB starts with a root and nothing else: no topic, no `skills/` folder and no `sessions/` folder. The path to the
steady state:

1. **The default skills ship with the implementation, mounted rather than copied in.** The implementation supplies
   starter versions of thirteen common skills: `ingestion-classification`, `ingest-paper`, `ingest-book`,
   `summarization`, `conflict-detection`, `tag-proposal`, `sub-topic-proposal`, `brainstorming`, `planning`, `briefing`,
   `web-search`, `answering-a-brief`, and `voice`. They load from the implementation itself, so the tree's own `skills/`
   folder starts absent and an untouched skill improves whenever the implementation does. They work out of the box, and
   the operator who wants to change one adopts it: a copy lands in a `skills/` folder in the tree, opening with one line
   naming the shipped skill it now shadows, and it shadows that skill permanently (*Where a skill lives*, Section 4.6).
   The Learning agent's own three, self-improvement, lesson proposal and skill proposal, mount the same way and belong
   to no topic (Section 4.3), so the self-learning loop runs on shipped rules from the first `/close` rather than on
   whatever the model decides is worth keeping. Section 4.3 owes their text along with the two source extractions, and a
   loop that opens before they land applies the thirteen and nothing of its own.
2. **`voice` ships with an opinionated starter profile, corrected from the operator's own writing.** Every draft has a
   voice whether or not somebody wrote one down, and without a profile it is the model's own, chosen by nobody. A wrong
   default shows up in the first draft and gets fixed, and an absent one never does. So the shipped skill states real
   rules, and the operator corrects it from whatever writing they already have. A topic may hold its own voice, which
   replaces the root profile for that topic.
3. **The operator creates the first topics on demand.** The first topic is created inside a session, opened on the
   Librarian the way every other write reaches the tree (Sections 1.7 and 2), and it follows the creation flow of
   Section 1.10 from there. With zero topics, every inbound item is a topic gap. Nobody designs a taxonomy up front: the
   tree grows from what the operator captures, and the hooks generate each topic's own `index.md` and the root registry
   as soon as files exist. Before the first topic lands, the registry holds the static `type.*` definitions and the
   catalog of the shipped skills, so the Librarian routing on it reads an empty topic tree rather than a missing file.

---

## 10. What the Design Does Not Do

Three limits stay stated beside the mechanism each one bounds: domain nodes stay bare in the registry (Section
1.6), a session file reaches no expert (Section 7.6), and the write-time check leaves three cases unreached
(Section 6.2). The three gaps below are the rest.

The PKB is meant to improve itself from what it learns in the work. Three things stand between the design as written and
that claim, and the design answers none of them.

The PKB notices nothing while nobody is in a channel. It notices a conflict on the write, because a session is there to
report to (Section 6.3), and it notices nothing else: the one mechanism that reasons over all three pillars waits on
`/close`, and `/close` waits on whoever was in the channel. An agent that reasons when spoken to and never otherwise is
a filing system with a good vocabulary.

The PKB does not know the operator. `voice` holds how they write, and nothing holds how they decide: what they have
turned down and why, which arguments have moved them, which kinds of evidence they ask for before they will try
something. A record of that would make every proposal better, and it would be the most sensitive file in the tree, which
is the reason to design it rather than accumulate it.

Nothing decays. A note from two years ago carries the same weight as one from this morning, and `updated` is the only
field that records the difference. The practical pillar needs an answer here and the theoretical pillar does not,
because a book stays as true as it was while a note about a pit the operator no longer owns goes stale without ever
becoming false. The write-time check catches a later note contradicting an earlier one, and it catches nothing where
they stopped doing it.
