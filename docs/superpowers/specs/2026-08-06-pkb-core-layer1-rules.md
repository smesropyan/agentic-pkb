# PKB Core (Layer 1) — Requirements and Rules

**Date**: 2026-08-06
**Status**: Implementing. Every open question below has a default applied; none blocks the build.
**Scope**: `pkb.core` — build-order step 1 of the [architecture design](2026-08-06-pkb-architecture-design.md).

---

## 0. How to read this document

`README.md` says *what* the PKB is; the architecture spec says *how the system is built*. This document
is the mechanical contract for Layer 1: every rule the knowledge-base tree must obey, with a stable id
(`FM-1`, `PA-3`, `VA-12`, …) that the implementing test cites.

It was produced by mining both documents through five independent lenses (structure, metadata, tags,
generators, lifecycle) and consolidating the results. §2 lists the places the two source documents
contradict each other and how each is resolved; §3 lists what neither document decides, with the
default that was applied.

**Rule ids are stable.** A test that changes must cite the rule that changed.

### Decisions applied on top of the mined recommendations

| # | Decision | Instead of | Why |
|---|----------|-----------|-----|
| A | `topic.md` carries `source_type: summary` and `type.summary`. | A new `source_type: topic` (Q5's recommendation). | Keeps the authored `source_type` enum at the four values README §1.4 documents, and keeps `source_type` ↔ `type.*` an exact bijection (VA-11) with no special case. `topic.md` is already distinguished by location, which is what VA-13 checks. |
| B | Findings are `Finding(code, severity, path, message, field, value, line, rule_id, hint)`; `Severity` is a `StrEnum`. | — | Fixes CX-6's shape. `code` is the machine-readable failure; `rule_id` ties it to this document; `hint` carries the fix an agent should apply. |
| C | A single tree walk produces a `KbSnapshot`, and validation, generation, and maintenance all read from it. | Each consumer walking the tree itself. | Three walks would drift; GE-4's determinism requirements and MA-8's orphan analysis need the same view of the tree. |
| D | Q1–Q21 defaults are applied exactly as recommended below, except where row A says otherwise. | — | Every one is cheap to revisit: each changes a golden fixture or one severity constant, never a module boundary. The ones worth a human's attention are Q1, Q2, Q3, Q9 and Q11 — they shape what the human sees in the KB. |

---

**Sources**: `README.md` (WHAT, PKB design specification) · `docs/superpowers/specs/2026-08-06-pkb-architecture-design.md` (HOW, approved architecture, cited as *arch*).
**Scope**: build-order step 1 (arch §11). Plain Python over a directory tree. No LLM, no agent imports, no network.
**Consolidated from five independent lens reports** (structure / metadata / tags / generators / lifecycle). Duplicate findings merged into one rule under the sharpest phrasing.

**Severity convention.** For *validation* rules, severity is the severity of the emitted `Finding`. For *structural / behavioural* rules (paths, generators, scaffold, maintenance), severity means how load-bearing the rule is: `error` = must hold or the layer is wrong; `warning` = should hold, deviation needs a written reason; `info` = advisory / classification only, never a failure.

**Every rule ID below is stable and must be referenced in the test that covers it** (`Finding.rule_id`, or a test-name suffix for non-validation rules).

---

## 1. Rule table

### 1.0 Cross-cutting (CX)

| ID | Rule | Source | Sev | Test assertion |
|----|------|--------|-----|----------------|
| CX-1 | `pkb.core` imports nothing from `pkb.agents`, `pkb.server`, `deepagents`, `langgraph`, `langchain`, `anthropic`, or any HTTP/network client. Enforced by an import-linter contract in CI, not convention. | arch I1 L91-95; §9 L395 | error | import-linter contract `pkb.core independent of pkb.agents, pkb.server, deepagents, langgraph` passes. |
| CX-2 | The entire `pkb.core` suite runs against `tmp_path` with `ANTHROPIC_API_KEY` unset and sockets disabled. | arch I1 L92-93; §9 L386 | error | `pytest tests/core` passes with a socket-blocking conftest fixture and no env vars. |
| CX-3 | Every public core function takes an explicit `kb_root: Path`. No module-level constant names a directory; no string `"/kb/"` appears anywhere in `pkb.core`. `/kb/` is a Layer 2 backend mount prefix. | arch I3 L114; §4 L166-173; D6 L33 | error | `grep -r '"/kb/' pkb/core` returns nothing; every exported callable's signature includes `kb_root`. |
| CX-4 | No `subprocess`, no `git`, no backup/undo/rollback machinery anywhere in core. Derived files are rebuildable from content alone; that is the only recovery mechanism in the first draft. | arch D6 L33; §10 L403-408 | error | `grep -r 'subprocess\|import git' pkb/core` returns nothing; deleting all derived files and running `regenerate_all` restores them byte-identically. |
| CX-5 | Validation and maintenance return structured findings; they never raise for content defects and never stop at the first problem. A file violating N rules returns N findings in one call. | arch §7 L330-332; §8 L371 | error | A fixture file with three distinct defects returns exactly three findings, each with a distinct `code`. |
| CX-6 | `Finding` carries: `code` (stable UPPER_SNAKE), `severity`, `path` (kb-root-relative POSIX), `message`, and optionally `field`, `value`, `line`, `rule_id`. A boolean pass/fail return is insufficient — the message is consumed verbatim by Layer 2's error `ToolMessage`. | arch §7 L330-332 | error | `dataclasses.fields(Finding)` matches the declared set; every emitted finding has non-empty `code` and `message`. |
| CX-7 | Test strategy: golden files for all three generators, property tests for tag rules. | arch §9 L386 | info | `tests/core/golden/` exists; a Hypothesis strategy covers tag syntax/depth/ancestor closure. |
| CX-8 | Addressing helpers (agent-id ↔ path, expert resolution, slugify) live in `pkb/core/paths.py`, extending the arch §3 package listing. They are pure path logic with no agent dependency; Layer 2 consumes them rather than reimplementing tree walking. | arch §4 L158-160 ("implemented as path resolution"); §3 L121-128 | warning | `pkb/core/paths.py` exists and exports `agent_id_for`, `resolve_expert`, `slugify`; `pkb.agents` contains no second implementation. |

### 1.1 `frontmatter.py` (FM)

| ID | Rule | Source | Sev | Test assertion |
|----|------|--------|-----|----------------|
| FM-1 | A frontmatter block is a line containing exactly `---` as the **first line of the file**, terminated by the next line containing exactly `---`. Everything after the closing delimiter is the body, preserved byte-exactly. | README §1.4 L117-132; §1.5 L195-199; §1.7 L307-322 | error | `parse("---\ntitle: \"X\"\n---\nbody\n")` → `(meta={'title':'X'}, body="body\n")`; a file not starting with `---` → `meta is None`, body = whole file. |
| FM-2 | The required-field set for authored files is exactly seven: `title`, `description`, `topic`, `tags`, `created`, `updated`, `source_type`. | arch §7 L319-320; README §1.4 L117-132 | error | `REQUIRED_FIELDS` is a frozenset of exactly those seven names. |
| FM-3 | Optional recognized fields: `related_topics` (defaults to `[]`), `review_note`, `last_reviewed`. None is ever required. | arch §7 L319-320 (omits them); README §1.4 L137-142 | error | A note lacking all three parses clean and `meta.related_topics == []`. |
| FM-4 | Field types: `title`/`description`/`topic`/`review_note` = non-empty `str`; `tags` = non-empty `list[str]`; `related_topics` = `list[str]`; `created`/`updated`/`last_reviewed` = `datetime.date`; `source_type` = enum member. | README §1.4 L117-132; §1.7 L307-322 | error | `tags: "topic.cooking"` (scalar) → `FIELD_TYPE`; `title: 123` → `FIELD_TYPE`. |
| FM-5 | Dates are calendar dates in `YYYY-MM-DD` with no time and no timezone. Parser accepts a YAML date scalar or a string matching `^\d{4}-\d{2}-\d{2}$` (normalized to `date`); anything else (datetime, `2024-1-5`, prose) is a `DATE_FORMAT` error. Serializer always emits unquoted `YYYY-MM-DD`. | README §1.4 L127-128; §1.7 L317-318, L346-350 | error | `parse` returns `date` for all three fields; `created: 2024-10-15T09:00Z` → `DATE_FORMAT`; `serialize(date(2024,10,15))` emits `created: 2024-10-15`. |
| FM-6 | `source_type` is a closed enum split into two disjoint sets. **Authored**: `note`, `reference`, `solution`, `summary`, `topic`. **Derived (reserved, never on an authored file)**: `index`, `catalog`, `tag-registry`. | README §1.4 L130 (four values); §1.5 L198 (`tag-registry`); extension resolved here — see Contradiction C6 | error | `AUTHORED_SOURCE_TYPES` and `DERIVED_SOURCE_TYPES` are disjoint frozensets; `source_type: recipe` → `UNKNOWN_SOURCE_TYPE` listing the authored values. |
| FM-7 | Canonical serialization key order: `title`, `description`, `topic`, `tags`, `created`, `updated`, `related_topics`, `source_type`, `review_note`, `last_reviewed`, then any preserved unknown keys in first-seen order. | README §1.4 L119-130; §1.7 L309-321, L338-350; Part 5 L728-740 | warning | `serialize()` of a full metadata object reproduces README §1.7 L307-322 verbatim, including field order. |
| FM-8 | Canonical serialization style: double-quoted scalars for `title`/`description`/`topic`/`review_note`; block sequence (one `  - tag` per line) for `tags`; flow sequence `[ a, b ]` for `related_topics`; unquoted dates; `source_type` bare. | README §1.4 L117-132; §1.7 L307-352; Part 5 L726-767 | error | Parse→serialize of every frontmatter block in README §1.4, §1.7 (both), and Part 5 (both) is a byte-identical round trip. |
| FM-9 | The parser tolerates YAML inline comments (the canonical example carries `source_type: note  # note, reference, solution, summary`). The serializer is not required to reproduce them. | README §1.4 L130 | error | Parsing the exact README §1.4 block yields `source_type == "note"` with no comment text in the value. |
| FM-10 | Unknown frontmatter keys are **preserved** through parse→serialize (never silently dropped) and reported by validation at warning severity. | Neither doc specifies; extension folders (README §1.2 L72) plausibly want domain fields | error | A note with `servings: 4` round-trips with the key intact and produces one `UNKNOWN_FIELD` warning. |
| FM-11 | A targeted field write (`set_field` / `remove_field`) rewrites exactly the frontmatter, leaving the markdown body byte-identical and all other keys, their order, and their style untouched. `remove_field` deletes the key rather than blanking it. | README §1.7 L328-332 ("remove the `review_note`"); arch §10 L403-408 (no VCS ⇒ gratuitous rewrites are unrecoverable noise) | error | Applying the README §1.7 resolution edit to the "before" block yields the "after" block byte-for-byte; body bytes unchanged. |
| FM-12 | Derived-file frontmatter is *minimal generated frontmatter*: never `tags`, `created`, `updated`, `related_topics`, `review_note`, or `last_reviewed`. Exact per-artifact shapes in §4 below. | README §1.4 L114-115; §1.5 L196-199; arch §7 L326-328 | error | Generated `tags.md` parses to exactly `{title, source_type}`; generated `index.md` files carry no date key. |
| FM-13 | Parsing malformed YAML or an unterminated block returns a structured parse failure (`meta is None`, `error` populated) — it never raises. Generators and maintenance must remain total over a hand-edited tree. | arch §7 L348-350 (flush runs after failure ⇒ one bad file must not block regeneration) | error | A file whose frontmatter is `title: [unclosed` yields a `FrontmatterParseError` result and `regenerate_all` still completes. |
| FM-14 | Frontmatter handling applies only to `.md` files. Non-markdown files (media, `[source-files]`) are never parsed. | README §1.2 L61-68; §1.8 rule 6 L398-399 | error | `validate_file("notes/x/media/a.png", b"...")` returns zero findings and performs no YAML parse. |
| FM-15 | `normalize_related_topic(value)`: strip; if the value's first dot-segment is one of `topic`/`status`/`type`/`domain`, use as-is; otherwise prefix `topic.`. `bbq` → `topic.bbq`; `bbq.equipment` → `topic.bbq.equipment`; `topic.bbq.equipment` → unchanged. | README §1.4 L129, L137-139; §1.7 L319 vs rendered §1.5 L234 | error | The four cases above; function is idempotent. |

### 1.2 `paths.py` (PA)

| ID | Rule | Source | Sev | Test assertion |
|----|------|--------|-----|----------------|
| PA-1 | The PKB root contains exactly three reserved entries — `index.md`, `tags.md`, `skills/` — plus one directory per top-level topic root. Anything else at the root is unexpected. | README Part 3 L626-651; §1.3 L100-102 | warning | `list_topic_roots(kb)` over `{index.md, tags.md, skills/, Cooking/, Physics/}` → `[Cooking, Physics]`; adding root `Cooking.md` → one `UNEXPECTED_ROOT_ENTRY` warning. |
| PA-2 | The PKB root is **not** a topic root: no `topic.md`, no `notes/`, no `references/`, no `expert.md`. Topic-scoped generators and validators never apply to it. | README Part 3 L626-651; §1.2 L55-74 | error | `is_topic_root(kb_root)` is False even for an empty KB; `generate_topic_index(kb_root, kb_root)` raises `NotATopicRoot`. |
| PA-3 | A directory is a **topic root** iff it directly contains a file named `topic.md`. That file is the sole structural marker of topichood. | README §1.2 L56-57; §1.3 L92; arch §4 L162 | error | A dir with `notes/` and `references/` but no `topic.md` is not a topic root; a dir with only `topic.md` is. |
| PA-4 | `sub-topics` is a **literal** directory name. Nested topic roots live at `<topic root>/sub-topics/<Sub Topic Name>/`. (Both tree diagrams bracket every placeholder — `[Topic Root]`, `[note-title]`, `[source-name]`, `[topic-specific]` — and leave `sub-topics/`, `notes/`, `references/`, `media/`, `skills/` unbracketed.) | README §1.2 L73; Part 3 L647-649 | error | `subtopic_path(cooking, "Grilling") == cooking/"sub-topics"/"Grilling"`. |
| PA-5 | Topic discovery is **recursive**, not depth-1. `find_topic_roots(kb_root)` walks the tree in deterministic depth-first pre-order (siblings sorted case-insensitively then by codepoint), never descending into `references/`, `notes/`, `media/`, `skills/`, or dot-directories. Resolves arch §4 L162's `*/topic.md` shorthand against arch §4 L158's `topic/cooking/grilling` agent id — see C1. | README §1.8 rule 5 L395-396; arch §4 L158-163 | error | A tree with `Cooking/topic.md` and `Cooking/sub-topics/Grilling/topic.md` yields both, parent before child. |
| PA-6 | `STRUCTURAL_DIRS = {"references", "notes", "media", "skills", "sub-topics"}` is a single shared constant used by discovery, tag derivation, extension-folder detection, and the index walker. They contribute no segment to a `topic.*` tag. | README §1.2 L55-74 vs §1.5 L205-211; arch §4 L158 | error | `topic_tag_for("Cooking/notes/x.md") == "topic.cooking"`, never `topic.cooking.notes`. |
| PA-7 | An **extension folder** is any directory directly under a topic root that is not in `STRUCTURAL_DIRS` and is not dot-prefixed. It is human-approved, arbitrarily named, open-set, and governed only by the folder-hosted item convention. Its presence is never a finding. | README §1.2 L72, L82-83; §1.9 L471-472 | info | `Cooking/recipes/` classifies as `EXTENSION_FOLDER`; no `UNKNOWN_*` finding is emitted for the folder itself. |
| PA-8 | `slugify(name)` is the single canonical display-name → tag-segment / id-segment mapping: NFKD-normalize, strip combining marks, casefold to lowercase, replace runs of whitespace/underscore/slash/punctuation with a single `-`, drop anything outside `[a-z0-9-]`, collapse and strip `-`, cap at 80 chars. Used by tag derivation, agent ids, location consistency, and scaffold name checking. | README §1.5 L167 + folder `Cooking` ↔ tag `topic.cooking`; multi-word segments hyphenated (L204-211) | error | `slugify("Heat Management") == "heat-management"`; `slugify("Café Noir") == "cafe-noir"`; result always matches the tag-segment regex or is empty. |
| PA-9 | `topic_tag_for(path)` = `"topic."` + dot-join of `slugify()` over the topic-folder names from the KB root down, with every `sub-topics` segment elided. `Cooking/sub-topics/Grilling` → `topic.cooking.grilling`. The inverse `path_for_topic_tag` round-trips for existing topics. | README §1.5 L167; arch §4 L158 | error | Round-trip property holds for three nesting levels; `sub-topics` never appears in a tag. |
| PA-10 | `agent_id_for(path)` mirrors the tree with `sub-topics/` elided and segments slugified: `topic/cooking/grilling`. The KB root's agent is `librarian`. Bijective with `topic_path_for_agent_id`. | arch §4 L158-159 | error | `agent_id_for(kb/"Cooking"/"sub-topics"/"Grilling") == "topic/cooking/grilling"` and back. |
| PA-11 | `is_derived_name(path)` matches exactly the I3 deny globs: any `index.md` at any depth, plus the root `tags.md`. This is the single definition consumed by the validation exemption **and** by Layer 2's deny-permission list. | arch I3 L100-112; README §1.4 L114-115 | error | True for `index.md`, `Cooking/index.md`, `Cooking/notes/x/index.md`, `tags.md`; False for `Cooking/tags.md`, `Cooking/topic.md`. |
| PA-12 | `is_generated(kb_root, path)` is the narrower set the generators actually write: `<kb>/index.md`, `<kb>/tags.md`, and `<topic root>/index.md` at any depth. An `index.md` that is derived-by-name but not generated (e.g. `notes/x/index.md`) is a validation error (VA-17) and a stale-file flag — never written and never deleted by Layer 1. | README §1.2 L85-86; §1.3 L93, L101; arch I3 | error | After full regeneration of a KB with 2 topics + 1 sub-topic, exactly 4 `index.md` files exist (root + 3 topic roots). |
| PA-13 | `resolve_expert(topic_path)` returns the nearest ancestor topic root — itself first — that holds an `expert.md`; `None` (⇒ default template) if none up to the PKB root. Pure path logic, no agent involvement. | arch §4 L158-160; README §2.3 L509-514; §1.8 rule 5 | error | With `Cooking/expert.md` and none in `Cooking/sub-topics/Grilling`, resolve → `Cooking/expert.md`; adding `Grilling/expert.md` flips it. |
| PA-14 | **Amended format (arch supersedes README)**: a skill is a directory — `skills/<skill-name>/SKILL.md` — at both the PKB root and topic-level overload folders. `resolve_skills(topic)` returns root skills with same-named topic-level entries shadowing them, in that precedence order. | arch D7 L35; §12 L437-441 (supersedes README §1.3 L95, §2.4 L570-572, Part 3) | error | `skills/voice/SKILL.md` is discovered as skill `voice`; `skills/voice.md` yields `LEGACY_SKILL_LAYOUT`; topic-level `voice` shadows root `voice`. |
| PA-15 | `owning_topic_root(path)` returns the nearest ancestor directory containing `topic.md`, or `None` for files outside any topic. Used by every location-consistency check. | README §1.9 L416-417; arch §7 L323-324 | error | `owning_topic_root(kb/"Cooking"/"sub-topics"/"Grilling"/"notes"/"x.md")` is the Grilling dir. |
| PA-16 | Every walk (discovery, validation, generation, orphan scanning) skips entries whose name starts with `.` and the names `__pycache__`. The ignore set is configurable, with that default. | Not specified; macOS `.DS_Store` would otherwise pollute every golden file and be flagged as an orphan | error | A KB containing `.DS_Store` and `.obsidian/` produces byte-identical derived output to one without them. |
| PA-17 | Folder-hosted item resolution compares the directory entry name **byte-exactly** (via `os.scandir` listing, not `Path.exists()`), because the dev host is case-insensitive APFS and the deploy host may be case-sensitive. | Not specified; environment fact (macOS) | error | `notes/Steak/steak.md` yields `MAIN_FILE_CASE_MISMATCH` on macOS, not a silent pass. |
| PA-18 | `link_target(from_dir, to_path)` produces a relative, POSIX-separated link, percent-encoding spaces and non-ASCII in the target while leaving link text human-readable. No absolute paths, no `file://`, no backslashes, no `/kb/` prefix. | arch §4 L166-173; README §1.9 L424-425 | error | Generating the same KB from `/a/KB` and `/b/other/KB` yields byte-identical output; `sub-topics/Heat Management/index.md` renders as `sub-topics/Heat%20Management/index.md`. |
| PA-19 | Reserved names table. At a topic root: `topic.md`, `index.md`, `expert.md`, `tags.md` (reserved *against* use — see VA-27). Inside `references/` and `notes/`: `summary.md`. Everywhere: `index.md`. None may be used as an item name at any depth of a topic tree. | README §1.2 L57-58, L70, L85-86; §1.3 L92-100 | error | `RESERVED_NAMES` constant exists; `recipes/topic/topic.md` yields `RESERVED_NAME_AS_ITEM`. |

### 1.3 `tags.py` (TG)

| ID | Rule | Source | Sev | Test assertion |
|----|------|--------|-----|----------------|
| TG-1 | A `Tag` is a dot-separated path parsed into ordered segments, exposing `.namespace` (first segment), `.segments`, `.depth`, `.parent`, `.ancestors`. | README §1.5 L151-161 | error | `Tag.parse("topic.cooking.grilling").segments == ("topic","cooking","grilling")`; `.depth == 3`. |
| TG-2 | The namespace set is **closed**: `topic`, `status`, `type`, `domain`. A first segment outside it is an error. (The registry renderer has exactly four section kinds and supplies static definitions for two — a fifth namespace has no defined rendering.) | README §1.5 L165-171, L174 | error | `set(Namespace) == {"topic","status","type","domain"}`; `project.alpha` → `UNKNOWN_TAG_NAMESPACE`. |
| TG-3 | Tag depth ≤ 4 segments **inclusive of the namespace** (≤ 3 dots). `topic.cooking.grilling.charcoal` is the maximal legal form. | README §1.5 L176; §1.5 L207; arch §7 L322 | error | 4 segments pass; 5 → `TAG_DEPTH_EXCEEDED`; property: `depth == len(segments)`. |
| TG-4 | A tag segment matches `[a-z0-9]+(-[a-z0-9]+)*` — lowercase kebab-case. No uppercase, spaces, underscores, empty segments, leading/trailing/double dots. Full tag regex: `^[a-z0-9]+(-[a-z0-9]+)*(\.[a-z0-9]+(-[a-z0-9]+)*)*$`. Not auto-normalized on read; the validator rejects and the agent self-corrects. | README §1.5 L155-161, L205-230; arch §7 L322, L330-332 | error | `Topic.Cooking`, `topic..cooking`, `topic.cooking.`, `topic cooking`, `topic.heat_management` each → `TAG_SYNTAX`. |
| TG-5 | A nested tag implies every ancestor. Tag *trees* materialize all ancestor nodes; tag *frontmatter* must not be required to list them. | README §1.5 L177; §1.4 L122-126 (example carries `topic.cooking.grilling` without `topic.cooking`) | error | A file tagged only `topic.cooking.grilling` produces no "missing ancestor" finding, and the rendered tree shows both nodes. |
| TG-6 | `type.*` is a closed vocabulary of four: `type.note`, `type.reference`, `type.solution`, `type.summary`. | README §1.5 L169, L215-218; §1.4 L130 | error | `type.article` → `UNKNOWN_TYPE_TAG`; all four validate. |
| TG-7 | `status.*` is a closed vocabulary of three: `status.draft`, `status.approved`, `status.conflict-review`. Layer 1 checks membership only; it never enforces transitions. | README §1.5 L168, L220-224; §1.7 | error | `status.wip` → `UNKNOWN_STATUS_TAG`; a draft→conflict-review jump is not rejected. |
| TG-8 | `domain.*` is **open** and nestable, and is never constrained by file location. Layer 1 checks syntax and depth only — no allowlist. | README §1.5 L170, L226-230 | info | `domain.finance.tax` validates with no lookup; a Cooking note tagged `domain.legal.compliance` validates. |
| TG-9 | `topic.*` is **open**: Layer 1 never rejects an unseen topic tag on vocabulary grounds and maintains no approved-tag list. The registry is purely derived; governance is a Layer 2 dialog concern. | README §1.5 L186-191; §1.9 L442-444 | error | No `add_tag`/`register_tag`/`approve_tag` exists in `pkb.core`; a file introducing `topic.cooking.sous-vide` validates and the tag appears on the next regeneration. |
| TG-10 | `build_tag_tree(kb_root)` returns a **data structure** (`TagTree` with `.subtree(tag)`), and rendering is a separate pure function over it. Research packs (Part 4) need the tree as data, not only as markdown. | README Part 4 L685-687; arch §9 L386 | info | `build_tag_tree(kb).subtree(Tag("topic.cooking"))` returns a node tree; `render_tag_tree(node)` is pure. |
| TG-11 | Query surface, honouring parent implication: `files_with_tag(tag)` returns files tagged at or below `tag`; `tags_in_namespace(ns, max_depth=None)` filters. | README §1.5 L178; Part 4 L686-687, L694-695 | info | `files_with_tag("topic.cooking")` includes a file tagged only `topic.cooking.grilling.charcoal`; `files_with_tag("status.conflict-review")` returns exactly the conflict-tagged files KB-wide. |
| TG-12 | Static definition constants, supplied by the generator and never read from files, rendered verbatim: `type.note` – human-written note · `type.reference` – static source · `type.solution` – reusable solution (a note tagged as a solution) · `type.summary` – breadth overview · `status.draft` – proposed, awaiting human approval. `status.approved`, `status.conflict-review`, and all `domain.*`/`topic.*` non-root nodes render **bare** (no separator, no gloss). | README §1.5 L186-189, L213-224, L228-230 | error | The `## Namespace: type` and `## Namespace: status` blocks of any generated registry equal the golden blocks regardless of KB contents. |
| TG-13 | Separator constants: `TAG_DEF_SEP = " – "` (EN DASH U+2013, single spaces) used only in tag-definition lines; `MAPPING_SEP = " ↔ "` (LEFT RIGHT ARROW U+2194); `EXTENSION_MARKER = " *(topic-specific extension)*"` appended after the backticked tag with no dash. | README §1.5 L205, L211, L234-235 | error | Byte-level golden test asserts U+2013 and U+2194 at the expected offsets. |

### 1.4 `validation.py` (VA)

| ID | Rule | Source | Sev | Test assertion |
|----|------|--------|-----|----------------|
| VA-1 | Two entry points. `validate_content(kb_root, rel_path, text) -> list[Finding]` is **pure over (path, proposed content)** and must work for a path that does not yet exist on disk — it is a pre-write gate. `validate_tree(kb_root) -> list[Finding]` runs the cross-file structural checks that need the tree. | arch §7 L315-317, L330-333 | error | `validate_content` on a nonexistent path returns findings without touching the filesystem (assert via a patched `open` that raises). |
| VA-2 | `validate_content` is stateless — no attempt counters, no caches keyed on run. The 3-attempt bound lives in Layer 2. | arch §8 L371 | error | Calling `validate_content` twice with identical arguments returns identical findings. |
| VA-3 | Missing or empty frontmatter on an authored `.md` file → `MISSING_FRONTMATTER`. | README §1.4 L114-115 | error | `Cooking/notes/x.md` with no `---` block → exactly one `MISSING_FRONTMATTER`. |
| VA-4 | Each of the seven required fields, absent or empty → one `MISSING_REQUIRED_FIELD` naming that field. | arch §7 L319-320 | error | Removing each field in turn from a valid note yields exactly one finding naming it; the intact note yields none. |
| VA-5 | Files matching `is_derived_name` (PA-11) are **exempt** from the required-field check and from tag checks. This resolves README §1.4 L134's literal "every file". | arch §7 L326-329; README §1.4 L114-115 | error | `validate_content("Cooking/index.md", minimal_generated_frontmatter)` returns zero findings; the same content at `Cooking/notes/x.md` returns missing-field errors. |
| VA-6 | `skills/**` is a **third file class** (SKILL), alongside AUTHORED and DERIVED. `SKILL.md` is validated only for the presence of `name` and `description` (deepagents format); PKB required fields do not apply, and skills never participate in index or tag generation. Requires a third README amendment (see C3). | arch D7 L35; §12 L437-441 vs README §1.4 L114 | error | `skills/voice/SKILL.md` with `name`/`description` validates clean; it produces no `MISSING_REQUIRED_FIELD` and appears in no index. |
| VA-7 | Non-markdown files are skipped entirely by frontmatter validation. | README §1.2 L61-68; §1.8 rule 6 | error | `validate_tree` reports zero frontmatter findings for `references/book/scan.pdf` and `notes/x/media/a.png`. |
| VA-8 | Every tag is checked for namespace (TG-2), syntax (TG-4), and depth (TG-3). Vocabulary membership is checked for `type.*` and `status.*` only (TG-6, TG-7); `topic.*` and `domain.*` are open (TG-8, TG-9). | README §1.5; arch §7 L322 | error | Table-driven: one finding per rule per offending tag; open-namespace tags never yield a vocabulary finding. |
| VA-9 | Tag cardinality on an authored file: **≥ 1** `topic.*`, **exactly 1** `type.*`, **exactly 1** `status.*`. `domain.*` unlimited. | README §1.4/§1.7/Part 5 examples all show exactly this shape; §1.7 L328-332 presumes one status | error | Zero status → `MISSING_STATUS_TAG`; two → `MULTIPLE_STATUS_TAGS`; zero topic → `MISSING_TOPIC_TAG`; two type → `MULTIPLE_TYPE_TAGS`. |
| VA-10 | Duplicate tags within one file's `tags` list → `DUPLICATE_TAG`. | README §1.5 (registry is a set-derived tree; duplicates carry no meaning) | warning | `tags` containing `topic.cooking` twice → one `DUPLICATE_TAG`. |
| VA-11 | `source_type` and the `type.*` tag are a **bijection**: `note↔type.note`, `reference↔type.reference`, `solution↔type.solution`, `summary↔type.summary`. (`topic` source_type pairs with `type.summary` — see Q5.) | arch §7 L323-324; README §1.5 L169; §1.4 L130 | error | `source_type: reference` + `type.note` → `SOURCE_TYPE_TAG_MISMATCH`. |
| VA-12 | **Location → `topic` field**: the `topic` frontmatter value must be the display name of the file's owning topic root (nearest ancestor holding `topic.md`), compared via `slugify()`. It is a display name, not a tag path and not a filesystem path. | README §1.4 L121; §1.9 L416-417; arch §7 L323-324 | error | `Physics/notes/x.md` declaring `topic: "Cooking"` → `TOPIC_LOCATION_MISMATCH`; `topic: "topic.cooking"` or `"Cooking/notes"` → `TOPIC_FIELD_FORMAT`. |
| VA-13 | **Location → `source_type`**: `notes/**` ⇒ `note` or `solution`; `references/<src>/<src>.md` ⇒ `reference`; `references/summary.md` and `notes/summary.md` ⇒ `summary`; `<topic root>/topic.md` ⇒ `topic`; extension folders ⇒ `note`, `solution`, or `summary`. | README §1.3 L96-99; §1.4 L130; §1.9 L416-417; arch §7 L323-324 | error | `references/grill-basics/grill-basics.md` with `source_type: note` → `SOURCE_TYPE_LOCATION_MISMATCH`. |
| VA-14 | **Location → `type.*`**: the same table as VA-13, expressed as tags. A `type.solution` file must live under `notes/` or an extension folder — never under `references/`. | README §1.3 L97-98; §1.8 rule 4; §2.3 L541-542 | error | `notes/x.md` tagged `type.reference` → `TYPE_TAG_LOCATION_MISMATCH`; `references/x/x.md` tagged `type.solution` → same. |
| VA-15 | **Location → `topic.*` tags**: every `topic.*` tag on a file must be equal to, or a descendant of, `topic_tag_for(owning_topic_root)`. Prefix containment, **not** folder existence — `topic.cooking.heat-management` is legal in `Cooking/notes/` with no such folder. | README §1.5 L167; §1.9 L416-417; arch §7 L323-324 | error | A file in `Cooking/sub-topics/Grilling/notes/` tagged only `topic.physics.heat` → `TOPIC_TAG_LOCATION_MISMATCH`; `topic.cooking.grilling.charcoal` passes. |
| VA-16 | **Folder-hosted item rule**: a directory that hosts an item must contain a main content file whose stem equals the directory name (`notes/<t>/<t>.md`, `references/<src>/<src>.md`, `recipes/<r>/<r>.md`). Byte-exact name match (PA-17). Applies inside `notes/`, `references/`, and every extension folder — never inside `skills/`. | README §1.2 L76-83; arch §7 L322 | error | `notes/steak-sear/note.md` → `MISSING_MAIN_FILE` naming `steak-sear.md`; `skills/voice/SKILL.md` is exempt. |
| VA-17 | An item's content file may never be named `index.md`, at any depth. | README §1.2 L85-86 | error | `notes/steak/index.md` → `ITEM_NAMED_INDEX` regardless of frontmatter. |
| VA-18 | `summary.md` is a reserved breadth-file name valid only at `references/summary.md`, `notes/summary.md`, and (optionally) `<extension folder>/summary.md`. It is not a legal item name. | README §1.2 L59, L69; §1.3 L96-99 | error | `notes/summary/summary.md` → `RESERVED_NAME_AS_ITEM`. |
| VA-19 | Reserved names (PA-19) used as item names anywhere in a topic tree → `RESERVED_NAME_AS_ITEM`. | README §1.2 L57-58, L70, L85-86 | error | `recipes/topic/topic.md` → `RESERVED_NAME_AS_ITEM`. |
| VA-20 | `expert.md` is valid **only** at a topic root. Elsewhere → `MISPLACED_RESERVED_FILE`. | README §1.2 L70; §2.3 L509-514 | error | `Cooking/notes/expert.md` → finding; `Cooking/expert.md` clean. |
| VA-21 | A note is either standalone (`notes/<t>.md`) or folder-hosted (`notes/<t>/<t>.md`) — never both. | README §1.2 L64-67 | error | Both present → `DUPLICATE_NOTE_IDENTITY`. |
| VA-22 | A note folder is never text-free: `notes/<t>/media/` with no `notes/<t>/<t>.md` → `MISSING_MAIN_FILE`. (Agents read the text, not the binaries.) | README §1.8 rule 6 L398-399 | error | `notes/x/media/a.png` with no `notes/x/x.md` → finding. |
| VA-23 | Media for a folder-hosted note belongs in the literal `notes/<t>/media/` subdirectory. Non-markdown siblings of the main note file outside `media/` → `MEDIA_OUTSIDE_MEDIA_FOLDER`. | README §1.2 L68; §1.8 rule 6 | warning | `notes/steak-sear/pan.jpg` → warning; `notes/steak-sear/media/pan.jpg` clean. |
| VA-24 | Reference folders have **no** `media/` convention: arbitrary `[source-files]` sit directly inside `references/<src>/` with no naming constraint and are never flagged. | README §1.2 L62-63 vs L66-68; Part 3 L637-638 | info | `references/grill-basics/grill-basics.pdf` and `.../scan.png` produce zero findings. |
| VA-25 | A standalone `references/<name>.md` (not folder-hosted) → `REFERENCE_NOT_FOLDER_HOSTED` at **warning** severity. The tree shows references only in folder form while notes explicitly document both, but a URL-only reference has nothing to put in a folder — so flag, do not block. See Q7. | README §1.2 L60-63 vs L64-69; §1.2 L76-83 | warning | `references/grill-basics.md` → one warning; `references/grill-basics/grill-basics.md` clean. |
| VA-26 | `description` must be a single-line, non-empty, whitespace-trimmed string. | README §1.4 L134-135 | error | A multi-line description → `MULTILINE_DESCRIPTION`; the topic index never emits an empty description cell. |
| VA-27 | `tags.md` is reserved at every topic root — there is no per-topic tag registry. A `<topic root>/tags.md` → `RESERVED_TOPIC_TAGS_FILE`. (Note: it is *not* covered by arch I3's deny globs — record the gap for the Layer 2 spec.) | README §1.5 L180-184; §1.3 L100; arch I3 L105-112 | error | `Cooking/tags.md` yields the finding, is never regenerated, and is never deleted. |
| VA-28 | Date ordering: `updated >= created`; if present, `last_reviewed >= created`. | README §1.4 L127-128; §1.7 L346-350 | error | `created: 2024-10-15` with `updated: 2024-10-14` → `DATE_ORDER`. |
| VA-29 | `review_note` and `status.conflict-review` are coupled in both directions, at **warning** severity (a legitimate two-step edit must not burn one of Layer 2's three attempts). Also warn when `last_reviewed` coexists with `status.conflict-review` is **not** flagged — `last_reviewed` legitimately persists across a re-flag (see Q). | README §1.7 L298-304, L328-332; arch §8 L371 | warning | `status.conflict-review` without `review_note` → `MISSING_REVIEW_NOTE`; `review_note` with `status.approved` → `ORPHANED_REVIEW_NOTE`; `review_note: ""` → `EMPTY_REVIEW_NOTE`. |
| VA-30 | Forbidden conflict residue: any frontmatter key expressing conflict type, confidence, resolution text, loser marking, or conflict history → `FORBIDDEN_CONFLICT_FIELD`. `last_reviewed` is the only permitted trace of a resolved conflict. | README §1.7 L330-332, L364-371 | error | `conflict_type: contradiction`, `confidence: 0.8`, `resolution: "..."` each → one finding. |
| VA-31 | A derived-reserved `source_type` (`index`, `catalog`, `tag-registry`) on an authored file → `RESERVED_SOURCE_TYPE`. | README §1.5 L198; FM-6 | error | An authored note declaring `source_type: tag-registry` → finding. |
| VA-32 | Unknown frontmatter keys → `UNKNOWN_FIELD` warning (value preserved per FM-10). Catches typos like `descripton:` that would otherwise surface as a confusing `MISSING_REQUIRED_FIELD`. | Not specified; extension folders make strict rejection wrong | warning | `descripton: "x"` yields both `MISSING_REQUIRED_FIELD(description)` and `UNKNOWN_FIELD(descripton)`. |
| VA-33 | A `related_topics` entry already carrying a namespace prefix → `RELATED_TOPICS_PREFIXED` warning; the value is normalized (FM-15) rather than rejected, so the registry stays correct either way. | README §1.4 L137-139 (canonical form is unprefixed) | warning | `related_topics: [ topic.bbq.equipment ]` → one warning, and the rendered mapping is unchanged. |
| VA-34 | A `related_topics` target resolving to no existing topic root → `DANGLING_RELATED_TOPIC` warning. Never an error: forward references to not-yet-created topics are legitimate during bootstrapping, and the README's own example (`weather`) would otherwise be invalid. | README §1.9 L427; Part 3 L666-669; §1.4 L129 | warning | `related_topics: [ atlantis ]` in a KB with no Atlantis topic → one warning, and the mapping still renders. |
| VA-35 | Divergence between a file's stem and `slugify(title)` → `FILENAME_TITLE_DIVERGENCE` warning only. The only naming rule both documents state is folder-name == main-file stem (VA-16); hard-failing on titles would force renames, which without version control are destructive. | README §1.2 L76-86; arch §7 L322; D6 | warning | `notes/wg.md` with `title: "Grill Performance in Windy Conditions"` → one warning, never an error. |
| VA-36 | A topic root reached other than through `sub-topics/` (a `topic.md` under `notes/`, `references/`, or directly inside an extension folder) → `MISPLACED_TOPIC_ROOT` warning. It is still discovered (PA-5) so it is never invisible. | README §1.2 L73; Part 3 L647-649 | warning | `Cooking/notes/Grilling/topic.md` → one warning, and the topic still appears in the catalog. |
| VA-37 | A topic root whose slug path would require more than 4 tag levels → `TOPIC_PATH_EXCEEDS_TAG_DEPTH` warning at validation time (do not break existing trees) and a hard refusal at scaffold time (SC-9). | README §1.5 L176 + L167; §1.8 rule 5 | warning | A 4-level-deep sub-topic yields the warning; every file inside it also yields `TOPIC_TAG_LOCATION_MISMATCH` because no legal tag can express its location. |
| VA-38 | An unexpected loose file directly at a topic root (not `topic.md`/`index.md`/`expert.md`) → `UNEXPECTED_TOPIC_ROOT_FILE` warning. Unknown *directories* are extension folders (PA-7) and are never flagged. | README §1.2 L72; §1.9 L471-472 | warning | `Cooking/scratch.md` → one warning; `Cooking/recipes/` → zero findings. |
| VA-39 | Unparseable YAML frontmatter → `FRONTMATTER_PARSE_ERROR` error finding with the YAML error text and line, never an exception. | FM-13; arch §7 L348-350 | error | A file with `title: [unclosed` yields one finding and does not abort `validate_tree`. |
| VA-40 | Layer 1 performs **no** tag-registry-membership check. A syntactically valid tag used by no other file is accepted. | README §1.5 L186-191; §1.9 L442-444 | error | Negative test: a novel `topic.cooking.sous-vide` produces zero findings. |
| VA-41 | `<topic root>/topic.md` is an **authored** file carrying all seven required fields (the root catalog reads its frontmatter). It is not exempt. | README §1.3 L92, L101; §1.4 L114 | error | A `topic.md` missing `description` yields `MISSING_REQUIRED_FIELD` and the root catalog renders a degraded entry (GE-24). |

### 1.5 `generators/` (GE)

| ID | Rule | Source | Sev | Test assertion |
|----|------|--------|-----|----------------|
| GE-1 | Exactly three generators exist — topic `index.md`, root `index.md`, root `tags.md` — under `pkb/core/generators/`, and they are the only code in the system that produces derived-file content. | arch §3 L124-128; README §1.9 L419-425 | error | AST test: no module outside `pkb.core.generators` writes a path for which `is_generated()` is True. |
| GE-2 | Derived files are written **wholesale**: truncate-and-replace, never read-modify-write. Prior content — hand edits, agent edits, garbage — is never read, merged, or preserved. | arch I3 L100-118; README §1.3 L110; §1.8 rule 3 | error | Write garbage into `Cooking/index.md`, regenerate, assert bytes equal the pristine golden file. |
| GE-3 | Generators never consume derived files as input. Derived files are excluded from the frontmatter walk, tag scanning, `related_topics` aggregation, the link graph, and orphan analysis. Tags rendered *inside* `tags.md` are output, never evidence of usage. | README §1.5 L186-191; arch §10 L407-408 | error | Delete every derived file and regenerate: output is byte-identical to regenerating over a tree that still contained them. |
| GE-4 | Generation is byte-deterministic. Output must not depend on filesystem iteration order, `PYTHONHASHSEED`, locale/collation, timezone, wall-clock time, the absolute KB root path, or the host line-ending convention. Every ordering has an explicit sort key. | README §1.9 L419-421; arch §9 L386 | error | Four runs — reversed `os.scandir`, `PYTHONHASHSEED` 0 and 1, `TZ=UTC` and `TZ=Pacific/Auckland`, two different KB roots — yield one identical byte string. |
| GE-5 | Regeneration is idempotent and rebuild-equivalent: a second consecutive run changes zero files, and a full rebuild equals an incremental flush over the same tree. | arch §7 L349-350; §10 L406-408; §8 L375 | error | `regenerate_all()` twice → second call reports zero files written. |
| GE-6 | No derived file contains a generation timestamp, run id, host name, generator version, or file count. Any of these would break byte-idempotence and produce empty-diff churn on every flush. | README §1.4 L114-115; §1.5 L196-199; arch §7 L349-350 | error | Regenerate at two mocked clock values (2024-01-01, 2030-06-06) with an unchanged tree → identical bytes. |
| GE-7 | Derived byte format: UTF-8 without BOM, LF line endings, exactly one trailing newline, no trailing whitespace on any line, no tabs, 4 spaces per nested-list indent level. | README §1.5 L205-211; arch §9 L386 | error | Lint over generated output: no `\r`, no line matching `\s+$`, ends with exactly one `\n`, every indent is a multiple of 4. |
| GE-8 | Derived files are written atomically (temp file in the same directory + `os.replace`) and the write is **skipped** when rendered bytes equal on-disk bytes, so a no-op flush leaves mtimes untouched. | arch §8 L363-365; §10 L403-408 | error | Unchanged tree → `write_derived` returns False and `st_mtime_ns` is unchanged. |
| GE-9 | Render and write are separated: each generator exposes a pure `render_*(model) -> str` performing no I/O, plus a thin collect-and-write wrapper. Golden tests target the pure functions. | arch §9 L386; I1 | warning | `render_root_tags(model)` returns the golden string with a patched `open` that raises. |
| GE-10 | **Root index content**: exactly one entry per topic root, at every depth, sub-topics nested one level under their parent, siblings sorted case-insensitively then by codepoint. Each entry's data comes only from that topic's `topic.md` frontmatter (`title`, `description`). No per-file listings, no counts. | README §1.3 L101; §1.9 L424-425; §2.2 L497-501; arch §4 L158-160 | error | Adding a note to `Cooking/notes/` leaves root `index.md` byte-identical; editing `Cooking/topic.md` `description` changes exactly one line. |
| GE-11 | Root index carries **no** tag data and no cross-topic mappings — cross-topic coordination is served by root `tags.md`. | README §2.2 L499-503; §1.5 L182-184 | error | Root `index.md` contains no `↔` and no rendered tag-tree node. |
| GE-12 | The root catalog stays bounded: one rendered line per topic. It is a context artifact read by the Librarian, not the `AgentRegistry`'s data source. | README §1.8 rule 2 and Part 4; §1.9 L424-425; arch §4 L162-165 | error | A 50-topic × 20-file KB yields `line_count == topics + fixed chrome` and < 8 KB. |
| GE-13 | Root index marks a topic that owns an `expert.md` with ` *(custom expert)*`. | README §2.2 L498-500 ("Expert overrides are visible in the folder tree") | info | Adding `Cooking/expert.md` flips exactly one line to carry the marker. |
| GE-14 | **Topic index content**: walk the topic root's subtree, extract each content file's frontmatter (`title`, `description`, `tags`) plus its path relative to the topic root. Bodies are never read. | README §1.9 L419-421; §1.4 L134-135; §1.3 L93 | error | Every non-excluded `.md` under the topic appears exactly once, with its `description` verbatim after whitespace normalization. |
| GE-15 | Topic index exclusions: derived files, the `sub-topics/` subtree contents, `skills/`, `expert.md`, `topic.md`'s duplicate listing outside the Breadth section, and all non-markdown assets. Excluded assets are still visited by orphan/link analysis. | README §1.2 L56-73; §1.8 rule 6; arch §12 L437-439 | error | A topic containing `skills/voice/SKILL.md`, `expert.md`, `notes/x/media/a.png`, and `sub-topics/Grilling/notes/y.md` renders none of them as items; the png still participates in orphan detection. |
| GE-16 | A topic index does **not** recurse into `sub-topics/`. Each immediate sub-topic appears exactly once, as a link to its own `index.md` plus its `topic.md` description. | README §1.8 rule 5; §1.8 rule 2 and Part 4; §1.2 L73 | error | With 30 notes under `sub-topics/Grilling/notes/`, the parent index gains exactly one line; a 31st note leaves it byte-identical. |
| GE-17 | Every topic index embeds that topic's tag subtree — the branch of the **global** tag tree rooted at the topic's own tag (so a tag used by a file filed elsewhere still shows), with implied ancestors materialized — rendered by the same renderer as the root registry. The subtree covers `topic.<this-topic>.*` only, not `type.*`/`status.*`/`domain.*`. | README §1.3 L93; §1.5 L184; §1.9 L419; arch §7 L338 | error | The topic's `## Tag subtree` block equals the corresponding `## Namespace: topic.cooking` block of root `tags.md` modulo the heading line. |
| GE-18 | Every topic index embeds the topic-local cross-topic mappings: pairs involving this topic, oriented with the local topic's tag on the left. | README §1.3 L93; §1.9 L419, L461-462; arch §7 L339 | error | Every `↔` line in `Cooking/index.md` has a left side starting `topic.cooking`, and the same unordered pair appears exactly once in root `tags.md`. |
| GE-19 | **Cross-topic mapping derivation**: for each non-derived file, the cartesian product of (its `topic.*` frontmatter tags) × (its normalized `related_topics` targets), deduplicated, with identity pairs dropped. `related_topics` is the *only* permitted source — never inferred from shared `domain.*` tags, folder proximity, body links, or prose. | README §1.4 L137-139; §1.5 L186-189; §1.9 L422-423, L461-462; §1.8 rule 4 | error | Two files sharing `domain.legal.compliance` with no `related_topics` produce zero mappings; a file tagged `[topic.cooking.grilling, topic.cooking.heat-management]` with `related_topics: [bbq.equipment]` produces exactly two pairs. |
| GE-20 | **Mapping dedup and orientation**: an edge is keyed by the unordered pair. If only one direction was declared, render in the declared orientation (which reproduces README §1.5 L234). If both directions were declared, render with the lexicographically smaller tag on the left. Lines sorted by `(left, right)`. In topic indexes, orientation is always local-topic-left. | README §1.5 L232-236; determinism per arch §7 L349-350 | error | A single Cooking→bbq declaration renders `` `topic.cooking.grilling` ↔ `topic.bbq.equipment` ``; adding the reciprocal BBQ→cooking declaration collapses to one line, bbq-first. |
| GE-21 | **Root `tags.md` literals**: frontmatter is exactly `title: "PKB Tag Registry"` and `source_type: tag-registry`; the H1 is exactly `# PKB Tag Registry`. Contents are exactly three kinds — namespace sections, per-topic subtrees, cross-topic mappings — with no file listings, no inverted tag→file index, no counts. | README §1.5 L195-236 | error | The first five lines of generated `tags.md` match byte-for-byte; adding 100 files that use only registered tags leaves it byte-identical. |
| GE-22 | **`tags.md` section order** is fixed: one `## Namespace: topic.<root-topic-slug>` per **top-level** topic root (sorted case-insensitively), then `## Namespace: type`, `## Namespace: status`, `## Namespace: domain`, then `## Cross-topic mappings (aggregated from \`related_topics\`)` last. Sub-topics get no H2; they nest inside their root topic's tree. The rendered example order wins over the §1.5 namespace-table order. | README §1.5 L203-236 | error | A two-topic KB renders `## Namespace: topic.bbq` before `## Namespace: topic.cooking`, then type, status, domain, mappings. |
| GE-23 | **Tag tree rendering**: `- ` marker, the **full dotted tag** in backticks (not just the leaf), 4 spaces of indentation per level below the section root, the section root annotated ` – root topic`, extension leaves annotated ` *(topic-specific extension)*`, static definitions per TG-12, everything else bare. Nodes are the tags actually used by non-derived files plus their implied ancestors; siblings sorted case-insensitively by full tag string. | README §1.5 L205-211, L177, L186-191 | error | A single file tagged only `topic.cooking.grilling.gas` renders the full four-level chain; removing the tag removes all four nodes. |
| GE-24 | Extension-folder annotation is derived: a `topic.*` leaf is marked iff a directory whose name slugifies to that leaf segment exists directly under that topic root and is not a `STRUCTURAL_DIRS` member. | README §1.5 L211; §1.2 L72 | warning | With `Cooking/recipes/` present and a file tagged `topic.cooking.recipes`, the node carries the marker; deleting the folder (keeping the tag) removes the marker but keeps the node. |
| GE-25 | Generators are **total** over a degraded tree. A content file with a missing/empty/unparseable `description` still gets an index entry rendered with the literal `*(no description)*` plus an error diagnostic. A topic root whose `topic.md` is missing or unparseable renders a catalog line with the folder name as fallback title plus `*(missing topic metadata)*` and a diagnostic. A directory with no `topic.md` is not a topic and renders nothing. A file is **never silently dropped** — a dropped file is invisible to every depth agent. | README §1.4 L134-135; §1.3 L101; §2.2 L497-501; arch §7 L348-350 | error | Each degraded case yields exactly one line of output plus exactly one diagnostic, and the flush completes. |
| GE-26 | Text injected into rendered markdown (titles, descriptions, review notes) passes through one `inline(text)` helper: collapse newlines and whitespace runs to a single space, strip, replace the reserved `·` with `-`, escape `[` and `]` in link text. Values are never truncated. | README §1.4 L134-135; arch §9 L386 | warning | A description containing a newline, `[x]`, and `·` renders as one well-formed bullet and round-trips through a markdown parser without structural change. |
| GE-27 | Item bullets sort by relative POSIX path (case-insensitive, then codepoint) within their section — not by title — so a title edit changes one line in place and a rename moves exactly one line. The per-bullet tag list is the file's full `tags` sorted lexicographically, each backticked and space-separated. | README §1.9 L419-421; §1.5 L178; arch §9 L386 | error | Renaming a note moves exactly one line in the diff; editing only its title changes exactly one line; rendered tag order is independent of frontmatter order. |
| GE-28 | Derived files contain **no** conflict history: no conflict registry, no resolution log, no loser marker, no past-conflict record. Rendering a file's *current* `status.conflict-review` tag is derived-from-current-state and is required by Part 4, not a violation. | README §1.7 L364-371; Part 4 L685-687, L694-695 | error | After a full flush over a KB with conflict-tagged files, the derived-file set is exactly `{root index.md, root tags.md, one index.md per topic root}`; resolving a conflict and reflushing leaves no mention of the prior conflict. |
| GE-29 | All three generators produce valid output for an **empty** KB: root `index.md` renders its heading plus `_No topics yet._`; root `tags.md` renders its heading plus the static `type` and `status` sections (topic/domain/mapping sections omitted); no topic indexes are written. | README Part 3 L653-671; §1.5 L186-189 | error | `regenerate_all(tmp_path)` on an empty directory writes exactly two files matching `golden/empty_root_index.md` and `golden/empty_tags.md`, exit 0. |
| GE-30 | `regenerate_all(kb_root, *, today) -> FlushReport` is the single public entry point. It assumes it is the sole writer for its duration, acquires no locks itself, and is safe to call repeatedly and after partial/failed writes. | arch §8 L363-365; §7 L349-350; D2 | warning | No threading/asyncio lock and no `fcntl` call inside `regenerate_all`; two sequential calls are a documented no-op the second time. |
| GE-31 | Golden coverage required, per artifact: a populated multi-topic KB with a sub-topic and an extension folder; an empty KB; a KB with a `status.conflict-review` file; a KB with a broken link and an orphan. Goldens assert full-file string equality and fail on any whitespace, ordering, or glyph drift. A `--update-golden` flag regenerates them. | arch §9 L385-386; §11 L425-426 | error | Each golden test is a full-string comparison; drifting one space fails it. |
| GE-32 | Property tests back the tag rules feeding the generators: for any legal tag set, the rendered tree has complete ancestor chains, depth ≤ 4, total and stable sibling order, and parses back to the ancestor closure of the input set; rendering is invariant to input order. | arch §9 L386; README §1.5 L176-177 | warning | Hypothesis: `parse_tag_tree(render_tag_tree(tags)) == ancestor_closure(tags)` for all inputs. |

### 1.6 `scaffold.py` (SC)

| ID | Rule | Source | Sev | Test assertion |
|----|------|--------|-----|----------------|
| SC-1 | `scaffold_topic(kb_root, topic_path, *, title, description)` creates exactly six paths: `topic.md`, `references/`, `references/summary.md`, `notes/`, `notes/summary.md` — and (per SC-2) `topic.md` — plus nothing else. `index.md` arrives via regeneration (SC-7), not from the scaffolder. | README §1.2 L55-74; §1.9 L466-468 | error | `scaffold_topic(kb, "Cooking")` creates exactly that path set; deleting `notes/summary.md` afterwards makes `validate_tree` emit `MISSING_REQUIRED_FILE`. |
| SC-2 | The scaffolder **does** write a placeholder `topic.md` (`status.draft`, `source_type: topic`, placeholder description, empty or one-line body). Without it the topic is invisible to `AgentRegistry` (arch §4 L162) and to the root catalog (README §1.3 L101), so the expert that is supposed to draft it cannot be addressed. Step 3 of README §1.9 then becomes an edit rather than a creation — the same propose-and-approve loop either way. See C12. | README §1.9 L466-470 vs arch §4 L162-163; README §1.3 L101 | error | After `scaffold_topic`, `find_topic_roots` includes the new topic and the root catalog lists it. |
| SC-3 | Every file the scaffolder writes passes `validate_content` with zero **error**-severity findings: all seven required fields, location-consistent `topic`/`source_type`/`type.*`/`topic.*`, exactly one `status.draft` tag. | README §1.9 L468; arch §7 L317-324 | error | For every file produced by `scaffold_topic`, `validate_content` returns zero errors; `validate_tree(kb)` afterwards returns zero errors. |
| SC-4 | The scaffolder creates **no** optional members: no `expert.md`, no `skills/`, no `sub-topics/`, no extension folder. Those are added later with human approval. Their absence is never a finding. | README §1.2 L70-73; §1.9 L471-472 | error | After `scaffold_topic`, none of those exist and `validate_topic` reports zero findings. |
| SC-5 | The scaffolder is depth-agnostic: one implementation parameterized by topic-root path produces an identical file set for a top-level topic and for `Cooking/sub-topics/Grilling`. | README §1.8 rule 5; Part 3 L647-649 | error | The two file sets are equal modulo the path prefix. |
| SC-6 | `scaffold_subtopic(parent_topic_path, name)` writes to `<parent>/sub-topics/<Name>/`, creating `sub-topics/` if absent. | README §1.2 L73; Part 3 L647-649 | error | `scaffold_subtopic(cooking, "Grilling")` creates `Cooking/sub-topics/Grilling/topic.md`. |
| SC-7 | Topic creation is followed by regeneration in the same operation, so the new topic's `index.md` exists from creation and it appears in the root catalog immediately. `scaffold_topic(..., regenerate: bool = True)`. | README Part 3 L670-671; §1.9 L428; arch §4 L164-165 | error | `scaffold_topic(kb, "Cooking")` alone leaves `Cooking/index.md` present and a Cooking line in root `index.md`, with no separate generator call by the test. |
| SC-8 | The scaffolder implements no approval gate and has no HITL dependency — approval happens in Layer 2/3 before it is called. | README §1.9 L428, L466 | info | `pkb.core.scaffold` imports no interrupt machinery and is callable directly in a `tmp_path` test. |
| SC-9 | The scaffolder **refuses** (raises `TopicDepthExceeded`) when the new topic's slug path would need more than 4 `topic.*` tag levels: every file inside such a topic would be unable to carry a location-consistent tag. Existing over-deep trees are only warned about (VA-37). | README §1.5 L176 + L167; §1.8 rule 5 | error | Scaffolding a sub-topic under `Cooking/sub-topics/Grilling/sub-topics/Charcoal` raises with a message naming the depth limit. |
| SC-10 | The scaffolder never overwrites an existing file. Re-scaffolding an existing topic creates only the missing members and reports what it skipped. It never deletes or moves anything. | README §1.7 L278; arch I3; D6 (no undo) | error | Scaffolding twice leaves the first run's `topic.md` bytes unchanged and reports the skipped paths. |
| SC-11 | Topic names are validated before creation: reject a name that slugifies to the empty string, collides with a sibling's slug, or equals a reserved name or `STRUCTURAL_DIRS` member. | PA-8, PA-19; determinism of PA-9 | error | `scaffold_topic(kb, "notes")` and `scaffold_topic(kb, "!!!")` both raise. |
| SC-12 | The scaffolder seeds **no** tags beyond the placeholders' own required tags, writes no tag subtree, and touches `tags.md` only through regeneration. The topic's initial tag subtree is proposed by the Topic Expert (Layer 2) and reaches the registry only when files actually use those tags. | README §1.9 L466-472; Part 3 L668-671 | error | After `scaffold_topic(kb,'Cooking')` + flush, `tags.md` contains a `topic.cooking` section only because the placeholders carry `topic.cooking` — no invented child nodes. |

### 1.7 `maintenance.py` (MA)

| ID | Rule | Source | Sev | Test assertion |
|----|------|--------|-----|----------------|
| MA-1 | `flush(kb_root, touched_paths, *, today) -> FlushReport` performs the six documented duties: (1) regenerate affected topic `index.md`; (2) regenerate root `tags.md`; (3) regenerate root `index.md`; (4) update `updated` timestamps; (5) flag broken links and orphans; (6) build conflict-scan requests. | arch §7 L335-344; README §1.9 L416-430 | error | A spy test records the six sub-operations; a golden test over a fixture KB reproduces the tree byte-for-byte. |
| MA-2 | **Internal ordering corrects arch §7**: timestamps are bumped **before** any derived file is rendered. Belt-and-braces, derived files also render no dates (GE-6), so the ordering bug is doubly neutralized. See C7. | arch §7 L338-344 (documented order) vs L349-350 (idempotence) | error | Edit a note, run one flush, assert the topic index reflects the post-bump state and that no ISO date appears in any derived file. |
| MA-3 | `updated` is bumped **only** for paths in the explicit `touched_paths` set, using an injected clock (`today: date`), and is a no-op when the value already equals today. A flush over an untouched tree rewrites zero content files. Scan-and-stamp is forbidden — it would dirty the whole tree and break idempotence. | arch §7 L341, L345-347, L349-350; README §1.9 L426 | error | `flush(touched=set())` over a populated KB leaves every content file byte-identical; `flush(touched={p})` rewrites only `p`'s `updated` line; two flushes the same day are idempotent. |
| MA-4 | `created` is immutable. No maintenance operation may change it. | README §1.4 L127 vs §1.7 L317/L346 vs Part 5 L736/L761 (created constant across three states while updated advances) | error | `created` is byte-identical before and after any flush. |
| MA-5 | Across all authored files, the **only** frontmatter field Layer 1 may write is `updated`. `title`, `description`, `topic`, `tags`, `created`, `related_topics`, `source_type`, `review_note`, `last_reviewed`, and the body must survive a flush untouched. | arch §7 L338-344; I3 L100-103; README §1.9 L412-430 | error | Property test over a randomly generated valid KB: `flush()` changes at most the `updated` line of content files plus the full content of derived files. |
| MA-6 | Layer 1 never adds or removes a `status.*` tag or `review_note`. Conflict tagging is Layer 2's act; clearing is the human's decision applied by an agent. | README §1.7 L298-304, L328-332; §2.1 L485-486; §1.9 L440-441; arch §7 L338-344 | error | AST/grep test: the strings `status.conflict-review` and `review_note` never appear as write targets in `pkb.core`; a flush over a conflicted file leaves its `tags` byte-identical. |
| MA-7 | **Broken-link scope**: parse markdown inline links `[t](target)`, reference-style link definitions, and image embeds `![t](target)` from every non-derived `.md`. Strip `#fragment` before resolving. Resolve relative targets against the containing file's directory. Skip any target with a URL scheme (`http:`, `https:`, `mailto:`, …) — Layer 1 makes no network calls. A link to `[item]/` resolves to `[item]/[item].md`. Report `BROKEN_LINK` for a nonexistent target and `LINK_ESCAPES_KB_ROOT` for a target resolving outside `kb_root`. Wiki-links `[[x]]` and heading anchors are deferred (link kind is recorded in the finding code so adding them later is non-breaking). | README §1.9 L427; arch §7 L342; I1 L92-95 | warning | `../references/missing.md` → one finding with source file, target, and line; `https://example.invalid` → none; the suite makes zero socket calls. |
| MA-8 | **Orphan definition** (the term is used once and never defined; the intuitive "not listed in an index" reading is vacuous because the index is generated from the walk). Four distinct codes: `ORPHAN_ITEM_FOLDER` — a folder-hosted item directory with no `[item]/[item].md`; `ORPHAN_ASSET` — a file under `media/` or inside a reference folder that the sibling main `.md` never references; `ORPHAN_FILE` — an authored `.md` inside a topic that is neither a structurally required file nor located in `notes/`, `references/`, or an extension folder; `ORPHAN_OUTSIDE_TOPIC` — an authored `.md` outside any topic root that is not a root reserved file. Derived files are excluded from the link-source graph (they link to everything by construction). | README §1.9 L427; §1.2 L76-86; §1.8 rule 6 | warning | `notes/trip/media/a.png` referenced by `notes/trip/trip.md` is clean; unreferenced it is `ORPHAN_ASSET`; `notes/trip/` lacking `trip.md` is `ORPHAN_ITEM_FOLDER`; a note is never flagged merely because only the generated index links it. |
| MA-9 | Flagging is **non-blocking and non-mutating**: it never aborts the flush, never writes to a content file, never adds a tag or frontmatter key, and never deletes or moves anything. | README §1.9 L427; §1.7 L278, L364-371; arch I3 | error | A KB with a broken link produces a finding, the flush completes, and no content file's bytes changed. |
| MA-10 | Findings land in two places: the returned `FlushReport` (always) and a `## Maintenance flags` section of the owning topic's `index.md` (topic-scoped findings only; the section is omitted when empty and self-clears on the next flush). Root-scoped findings are report-only in v1. | arch I3 (derived files are the only Layer-1-owned surface); README §1.7 L366 (no separate registry) | warning | After a flush over a tree with a broken link, the flag appears in the owning topic's `index.md` and in the report; fixing the link removes the section entirely. |
| MA-11 | Conflict-scan requests are **data only**. `flush` returns `list[ScanRequest]`; Layer 1 never opens a database, never writes machine state into the KB tree, and never performs semantic comparison. Persisting to the SQLite queue table is Layer 2/3's job. See C21. | README §1.9 L429-433; arch §7 L352-357; I1 | error | `flush` over a KB with a new note returns one `ScanRequest` and constructs no model client and no DB connection. |
| MA-12 | `ScanRequest` fields: `topic_id` (agent id), `topic_path`, `changed_paths: list[str]`, `origin: "maintenance" \| "on-demand"`, `requested_at`. Requests are **coalesced per topic per flush** (the scan is a whole-topic comparison, so N per-file requests would run the same expensive scan N times). Triggers are creates *and* modifies under `notes/`, `references/`, and extension folders; derived files are excluded from the changed set. | README §1.7 L290-295; arch §7 L344 | error | Five changed notes in one topic → exactly one request carrying five paths; a request can be constructed for a topic with an empty changed set and `origin="on-demand"`. |
| MA-13 | `FlushReport` fields: `written: list[Path]`, `unchanged: list[Path]`, `findings: list[Finding]`, `scan_requests: list[ScanRequest]`. | arch §7; §8 L375 | error | Dataclass field set matches; `written + unchanged` covers every derived path. |
| MA-14 | The flush must be safe to run after a failed or partial agent run, and safe to run twice. Layer 1 makes no assumption that the tree is valid. | arch §7 L348-350; §8 L375 | error | Flushing a tree containing one unparseable file and one half-written note completes, regenerates all derived files, and reports the defects. |
| MA-15 | Layer 1 acquires **no** lock. `flush` documents a sole-writer contract; the global KB write lock is taken by Layer 2's `after_agent`. See Q16. | arch §8 L363-365 | warning | No lock primitive inside `pkb.core.maintenance`; the docstring states the contract. |

---

## 2. Contradictions found

| # | Contradiction | Recommended resolution | Why |
|---|---------------|------------------------|-----|
| **C1** | **Topic discovery depth.** arch §4 L162 says `AgentRegistry` scans `*/topic.md` (depth 1). Four lines earlier, §4 L158 makes `topic/cooking/grilling` a first-class agent id, and README §1.8 rule 5 makes sub-topics full topic roots. | **Recursive discovery** (PA-5). The `*/topic.md` glob is shorthand for "the catalog scan", not a depth constraint. | A depth-1 scan makes every sub-topic unaddressable, contradicting the addressing paragraph in the same section. Worth correcting in the arch doc. |
| **C2** | **Skill file layout.** README §1.3 L95, §2.4 L570-572, and Part 3 L646 say `skills/[skill].md`. arch D7 L35 and §12 L437-441 say `skills/<skill-name>/SKILL.md`. | **arch wins** (PA-14). §12 explicitly labels this a required README amendment. Emit `LEGACY_SKILL_LAYOUT` for the flat form. | The arch document is later, approved, and self-declares the supersession. |
| **C3** | **Skill frontmatter.** README §1.4 L114 says every authored markdown file carries PKB frontmatter. deepagents' `SKILL.md` uses `name`/`description`/`allowed-tools` — an incompatible schema. | Classify `skills/**` as a **third file class** exempt from PKB frontmatter and from index/tag generation, validated only for `name` + `description` (VA-6). **Record this as README amendment #3** alongside arch §12's two. | Forcing PKB fields onto `SKILL.md` breaks deepagents' own parsing and forfeits the override resolution D7 was chosen for. Skills are agent instructions, not indexable knowledge. |
| **C4** | **"description is required on every file."** README §1.4 L134 says *every*; §1.5 L196-199's own `tags.md` example has none; arch §7 L326-328 exempts derived files from the required-field check. | Read it as **"every authored file"** (VA-5). | The arch doc is later and explicitly resolves it; the README's own example violates the literal reading. Flagged because the literal words will otherwise get transcribed into a validator that rejects the generators' own output. |
| **C5** | **`related_topics` notation.** README §1.4 L129 shows bare names `[ bbq, weather ]`; §1.4 L137-139 and §1.7 L319 show dotted-without-namespace `bbq.equipment`; §1.5 L234 renders fully qualified `topic.bbq.equipment`. | Accept bare and dotted forms on input, **normalize by prefixing `topic.`** (FM-15), render qualified. Warn on an already-prefixed input (VA-33) so the corpus converges on the canonical form. | All three appear in the spec; one normalization function keeps validator and generator agreeing, and warning rather than rejecting avoids burning a retry attempt on a cosmetic issue. |
| **C6** | **`source_type` enum is incomplete.** README §1.4 L130 lists four values; §1.5 L198 uses a fifth (`tag-registry`); nothing covers `topic.md`, `index.md`, `expert.md`, or `SKILL.md`. | Extend to two disjoint sets (FM-6): authored `{note, reference, solution, summary, topic}`, derived-reserved `{index, catalog, tag-registry}`. `expert.md` and `SKILL.md` sit outside the frontmatter regime entirely (C3). | The enum is already demonstrably open-ended in the spec. Writing the full list down once is a prerequisite for VA-11/VA-13/VA-31. |
| **C7** | **Flush ordering.** arch §7 L338-344 regenerates indexes (steps 1-3) *before* bumping `updated` (step 4). If any derived file rendered a date, every index would be one turn stale. | **Reorder internally** (MA-2) *and* omit dates from derived output (GE-6). | Reordering is invisible to every consumer and eliminates a class of stale-derived-file bugs; omitting dates additionally removes per-note index churn. Raise as a one-line correction when Layer 2 is specced. |
| **C8** | **`domain.*` rendering.** README §1.5 L177 says a nested tag implies its parent and L188 says the generator renders "the full hierarchy"; L228-230 renders three 3-segment `domain.*` tags **flat**, with no `domain.legal` parent bullet. | **Render `domain.*` as a nested tree**, same renderer as `topic.*`. Note the deviation in a code comment and in the golden fixture. `type.*` and `status.*` stay flat because they *are* flat static definition lists. | §1.5 L182-183 calls the registry "the canonical relational tree"; one renderer is less code and matches the stated rule. This is the sharpest example-vs-rule conflict in the spec — see **Q1**, it defines a golden file. |
| **C9** | **`sub-topics/` literal vs placeholder.** README §1.2 L73 and Part 3 L647-649 render it as a real tree node; arch §4 L158's agent ids and §1.5's tags both omit it. | **Literal directory** (PA-4), **elided** in tag derivation and agent ids (PA-6, PA-9, PA-10). | Both tree diagrams bracket every placeholder and leave `sub-topics/` unbracketed beside `notes/`/`references/`/`media/`/`skills/`. It is also the only signal distinguishing a nested topic from an extension folder without probing. Elision is required by arch §4 L158 and preserves the 4-level tag budget. |
| **C10** | **Solution notes.** README §1.3 L98 and §1.5 L217 describe a solution as "a note tagged `type.solution`"; §1.4 L130's enum lists `note` and `solution` as separate `source_type` values. | **Exactly one `type.*` tag**: `source_type: solution` ↔ `type.solution` (VA-9, VA-11). A solution still lives under `notes/`. "All notes including solutions" is a Layer 2 filter over `{type.note, type.solution}`. | Keeps the `source_type`↔`type.*` consistency check that arch §7 L323-325 requires as a clean bijection, and keeps the cardinality rule simple. |
| **C11** | **Reference form asymmetry.** README §1.2 L60-63 shows `references/` containing only `summary.md` and `[source-name]/` folders; L64-69 explicitly documents both standalone and folder forms for notes. | Flag standalone references at **warning** severity (VA-25), not error. | The asymmetry looks deliberate (a reference usually carries source files), but a URL-only reference has nothing to put in a folder, and the L76-83 rule is phrased as a condition *on* folder-hosted items rather than a mandate to use folders. See **Q7**. |
| **C12** | **Who writes `topic.md`.** README §1.9 L466-470 has Layer 1 scaffold (step 1) and the expert draft `topic.md` (step 3). arch §4 L162 discovers topics by scanning `topic.md`, and README §1.3 L101 builds the root catalog from its frontmatter. | **Scaffolder writes a `status.draft` placeholder `topic.md`** (SC-2); step 3 becomes an edit. | Otherwise the freshly scaffolded topic is invisible to the registry and the catalog, so the expert supposed to draft it cannot be addressed. Same pattern as the `summary.md` placeholders README already sanctions. |
| **C13** | **Cross-topic mapping arity.** The README's note carries two `topic.*` tags and one `related_topics` entry, yet §1.5 L234 shows only one mapping line. | **Cartesian product** (GE-19). The §1.5 block is explicitly labelled "excerpt showing the Cooking subtree" and describes a different hypothetical state. | No "primary tag" field exists in the schema, and two same-depth tags make a most-specific rule undecidable. The product is a superset that never loses a relationship. See **Q6** — pin it in a golden file, because the choice is invisible until a two-topic-tag file exists. |
| **C14** | **I3 deny-glob coverage.** arch I3 protects `/kb/**/index.md`, `/kb/index.md`, `/kb/tags.md`. `/kb/**/tags.md` is *not* protected, so an agent can create `Cooking/tags.md` that no generator maintains. Conversely `/kb/**/index.md` protects paths generators never write (`notes/x/index.md`). | Split the predicates (PA-11 vs PA-12). Reserve `tags.md` at every topic root as a validation error (VA-27), and **record the I3 gap for the Layer 2 spec**. | The deny list and the generated set are genuinely different sets; conflating them either under-protects or mislabels stale files as derived. |
| **C15** | **`tags.md` section order.** The §1.5 namespace *table* (L165-171) lists topic, status, type, domain. The rendered *example* (L203-236) emits topic, type, status, domain. | **The rendered example wins** (GE-22). | The example is the golden shape; the table is documentation ordering. |
| **C16** | **Sibling ordering in the example.** `topic.cooking`'s children read grilling, heat-management, baking, recipes — neither alphabetical nor derivable. | **Sort case-insensitively by full tag string** (GE-23). Tell the human the example's sibling order will not be reproduced. | Idempotence and golden files require a total, platform-independent order; the example's order is illustrative prose. |
| **C17** | **"Purely derived" vs static definitions.** README §1.5 L189 says the registry is purely derived; L188 says `type.*`/`status.*` definitions are static generator text. | Both hold: "purely derived" binds `topic.*` and `domain.*` **content**; `type.*`/`status.*` **definition blocks are always rendered**, even in an empty KB (GE-29, TG-12). | §1.5 L182-183 calls the registry an ontology for AI ingest — an ontology that vanishes when unused cannot teach an agent how to file the first note. |
| **C18** | **Conflict-scan queue location.** arch §7 L355 puts scan requests in "a queue table in the same SQLite file" as the checkpointer — a daemon-owned file — while I1 forbids `pkb.core` importing `pkb.server`. | **Core returns `list[ScanRequest]` data; Layer 2 persists it** (MA-11). `ScanRequest` is *defined* in `pkb.core` so both layers share the type. | Preserves I1 cleanly and keeps core's suite pure `tmp_path` with no database. A core-owned SQLite module would drag connection lifecycle and migrations into "plain Python over a directory tree". |
| **C19** | **Mapping orientation.** GE-20's canonical "smaller tag left" would render the README's own example backwards (`topic.bbq.equipment ↔ topic.cooking.grilling`). | Orient by the **declared direction** when only one side declares; smaller-left only when both declare (GE-20). | This reproduces README §1.5 L234 exactly while staying a deterministic function of the tree. Consequence to assert in a test: adding a reciprocal declaration legitimately flips the line. |
| **C20** | **Lens disagreement on scan trigger.** README §1.7 L290-291 says scans are triggered "whenever new notes or references are added"; arch §7 L344 says "covering changed files". | Trigger on **create *and* modify** under `notes/`, `references/`, and extension folders (MA-12). | An edited note can newly contradict a reference; the two statements reconcile at "changed". |
| **C21** | **Lens disagreement on `topic` field semantics for sub-topic files** (nearest topic root vs the top-level root vs dotted path). | **Display name of the nearest owning topic root** (VA-12), with the machine-checkable location link carried by the `topic.*` tag (VA-15). | README's only example, `topic: "Cooking"`, is a display name, and L138 reserves dotted notation for `related_topics`. Comparing `slugify(topic)` against the last segment of the owning slug path is unambiguous at any depth. See **Q4** — confirm before locking the schema. |

---

## 3. Open questions for the human

Ranked by how much the answer changes the implementation. Each has a **recommended default already encoded in the rule table above**, so implementation is not blocked while these are pending.

| # | Question | Options | Recommended default | Blast radius if changed later |
|---|----------|---------|---------------------|------------------------------|
| **Q1** | **How is the `domain.*` namespace rendered in `tags.md`** — nested tree (per the implied-parent rule) or flat fully-qualified list (per the worked example)? (C8) | (a) nested tree, one renderer for all namespaces; (b) flat, honouring L228-230 literally; (c) flat but sorted to group siblings. | **(a) nested tree.** §1.5 L182-183 calls the registry a relational tree; L177 and L188 both state the rule the example violates; one renderer is less code and gives Layer 2 a uniform structure to slice. | Rewrites the root `tags.md` golden and the shared tree renderer. **Confirm before writing fixtures.** |
| **Q2** | **What exact frontmatter do the two generated `index.md` files carry?** Only root `tags.md` is pinned (title + source_type). | (a) title + source_type only; (b) title + description + source_type (+ `topic` on topic indexes); (c) no frontmatter; (d) add a generated-at timestamp. | **(b)**, with `source_type: catalog` for the root and `source_type: index` for topic indexes — see §4. Reject (d) outright: it breaks byte-idempotence and every golden test. | Bakes into all three goldens and the `source_type` enum. **One-line decision, high downstream cost.** |
| **Q3** | **Approve the proposed topic `index.md` layout** — section set, section order, and the per-bullet ` · tags: ` affordance (§4 below). Nothing in either document pins the layout. | (a) as proposed; (b) drop the per-bullet tag list; (c) drop the `Needs review` section; (d) drop the `Maintenance flags` section (→ Q10). | **(a) as proposed.** `Needs review` is required by Part 4 L694-695; per-bullet tags are what let an implementation agent select `type.solution`/`status.conflict-review` files without opening every one (Part 4 L688-690). | Defines the largest golden fixture. |
| **Q4** | **For a file inside a sub-topic, what does the `topic` frontmatter field hold?** (C21) | (a) display name of the nearest owning topic root (`"Grilling"`); (b) the top-level root (`"Cooking"`); (c) dotted slug path; (d) full folder path. | **(a).** The `topic.*` tag already carries the full path; the field is the human-readable owner. | Changes VA-12 and every fixture file's frontmatter. |
| **Q5** | **What `source_type` and `type.*` tag does `topic.md` carry?** No example exists. | (a) `source_type: summary` + `type.summary`; (b) new `source_type: topic` + new `type.topic`; (c) exempt like derived files. | **`source_type: topic` + `type.summary`.** `topic` is needed for the location-consistency table (VA-13) and is already implied by the enum's demonstrated open-endedness; `type.summary` avoids amending the closed four-value `type.*` vocabulary (TG-6) for one file. Reject (c): the root catalog reads its frontmatter, so it must be validated. | Changes VA-11's bijection to a documented special case and every `topic.md` fixture. |
| **Q6** | **Cross-topic mapping arity** when a file has several `topic.*` tags and one `related_topics` entry. (C13) | (a) cartesian product, deduped; (b) first-listed topic tag only; (c) the tag matching the file's location. | **(a).** No "primary tag" field exists; ties at the same depth make (b) undecidable; (c) is contradicted by the example, which renders `topic.cooking.grilling` while the file's `topic` field is `"Cooking"`. | Changes `tags.md` and every topic index's mapping section. |
| **Q7** | **Must every reference be folder-hosted?** (C11) | (a) folder-hosted always, error on standalone; (b) allow both like notes, no finding; (c) allow both, warn on standalone. | **(c) warn** (VA-25). Keeps the documented asymmetry visible without blocking a URL-only reference. | Flips one finding's severity; trivial to change. |
| **Q8** | **What exactly is an "orphaned file"?** The term appears once, undefined. (MA-8) | (a) any file nothing links to; (b) structural orphans only (missing main file, unreferenced asset, misfiled markdown, markdown outside any topic); (c) any file absent from a generated index. | **(b), four distinct codes.** Reject (c) as vacuous — the index is generated from the walk. Reject (a) as noisy — notes are legitimately reachable only via tags and the index (README §1.8 rule 4), so plain unlinked notes are normal. | Adds/removes finding codes; distinct codes make later tuning non-breaking. |
| **Q9** | **Does the root catalog list sub-topics, or only top-level topics?** | (a) every topic root at every depth, nested; (b) top-level only; (c) top-level plus sub-topics owning an `expert.md`. | **(a).** README §1.9 L424 says "a catalog of every topic", and arch §4 L158-160 makes `topic/cooking/grilling` an independently addressable agent — the Librarian cannot route to an agent it cannot see. Revisit only if a real KB pushes the catalog past a few KB. | Changes the root index golden and its size bound (GE-12). |
| **Q10** | **Where do broken-link and orphan flags land?** | (a) returned diagnostics only; (b) diagnostics + a `## Maintenance flags` section in the owning topic's `index.md`; (c) a separate report file in the KB; (d) written into the offending file's frontmatter. | **(b)** (MA-10). Reject (d) outright — mutating content files is Layer 2's conflict-tagging mechanism and requires judgment. Reject (c) — README §1.7 L366 establishes a strong "no separate registry" instinct, and the index is regenerated anyway so the section is free and self-clearing. | Adds a section to the topic index golden and couples it to the link checker. |
| **Q11** | **Enforce exactly-one `status.*` and exactly-one `type.*`?** Neither is stated as a rule; every example shows exactly that shape. | (a) error on zero or multiple; (b) warning; (c) no cardinality rule. | **(a) error** (VA-9). The whole conflict lifecycle is a state machine over one status value, and Part 4 L694-695 requires reliable `status.conflict-review` detection; a file with both `approved` and `conflict-review` is ambiguous to every consumer. | Could block writes that the docs never explicitly forbid; downgradable to warning with one constant. |
| **Q12** | **Does the scaffolder write `topic.md`?** (C12) | (a) placeholder with `status.draft` frontmatter and a stub body; (b) omit it per README step ordering; (c) omit it and make the registry discover topics by directory shape. | **(a)** (SC-2). Whichever is chosen, the root-index generator needs defined behaviour for a topic root lacking `topic.md` — GE-25 already specifies "emit a diagnostic and render degraded", never crash. | Changes SC-1's file set and the topic-creation flow. |
| **Q13** | **Unknown frontmatter keys** — reject, warn, or accept silently? | (a) strict error; (b) preserve + warn; (c) preserve silently. | **(b)** (FM-10, VA-32). Strict rejection contradicts the human-approved extension mechanism (`recipes/` wanting `servings`); silent acceptance loses typo detection (`descripton:`). | One finding's severity. |
| **Q14** | **Slugify specifics**: non-ASCII handling and length cap. | (a) NFKD + strip accents + drop non-`[a-z0-9-]` (proposed, PA-8); (b) NFC + lowercase, keep non-ASCII characters, percent-encode only in link targets; (c) require topic folder names to already be valid tag segments. | **(a)**, cap 80. It guarantees every derived tag matches the tag-segment regex (TG-4), which (b) does not. Reject (c): it forfeits human-readable folder names like `Heat Management/`. | Changes every derived tag and agent id for non-ASCII topics. Property-test the round trip either way. |
| **Q15** | **Does the 4-level tag cap constrain sub-topic nesting depth?** | (a) yes — refuse to create a topic deeper than 3 folder levels below the namespace; (b) no — deep folders simply carry shorter/looser tags; (c) warn only. | **Refuse at scaffold time (SC-9), warn at validation time (VA-37).** Every file inside an over-deep topic would be unable to carry a location-consistent `topic.*` tag, so creating one produces a permanently invalid subtree — but existing trees must not be broken. | Adds a hard failure to one create path. |
| **Q16** | **Who owns the global KB write lock?** | (a) Layer 2 middleware only, per arch §8; (b) `pkb.core` owns a filesystem lock every write path acquires; (c) core exposes an injectable context manager, Layer 2 acquires it. | **(a)** for v1 (MA-15), with (c) as the upgrade if a maintenance CLI lands. The flush is not the only writer — the scaffolder writes too — but in v1 both are invoked from Layer 2. | Adds a lock primitive to core; additive. |
| **Q17** | **Do index entries carry `updated`/`created` dates?** | (a) no dates in derived files; (b) `updated` per item; (c) a bounded "Recently changed" section. | **(a) for v1** (GE-6). Dates add churn (editing one note rewrites an index line), force the C7 ordering fix to actually matter, and duplicate what the agent gets by opening the file. If recency proves necessary, (c) confines the instability to one block. | Changes the topic index golden and re-opens C7. |
| **Q18** | **Prefixed `related_topics` values** — reject or normalize? | (a) error; (b) normalize + warn; (c) accept silently. | **(b)** (VA-33, FM-15). An LLM writing `topic.bbq.equipment` is the obvious failure mode; hard-failing costs a retry against Layer 2's 3-attempt bound. | One finding's severity. |
| **Q19** | **Confirm README amendment #3** (skills exempt from PKB frontmatter, index, and tag generation). (C3) | (a) adopt as amendment #3; (b) require PKB frontmatter on `SKILL.md`; (c) require the union of both schemas. | **(a)**. (b) breaks deepagents' parsing; indexing skills would pollute the routing catalog with agent configuration. | Changes VA-6 and the index walker's exclusion set. |
| **Q20** | **Reserve `tags.md` at topic roots?** (C14) | (a) allow it as an ordinary item; (b) reserve the name at every topic root. | **(b) error** (VA-27), plus a note in the Layer 2 spec that arch I3's deny list should gain `/kb/**/tags.md`. | One finding; the I3 note matters more than the rule. |
| **Q21** | **Duplicate-content detection across topics** (README §1.8 rule 4: a solution lives in exactly one topic, no copies). | (a) build a `DUPLICATE_ACROSS_TOPICS` info finding on identical `title` + `description`; (b) defer to Layer 2 semantic scanning; (c) skip entirely. | **(b) — do not build in v1.** Rule 4 is enforced by *not copying*, and the only mechanical expression (exact title+description match) is a weak proxy for a semantic property Layer 2 already scans for. Layer 1 must never move or delete. | Purely additive if wanted later. |

---

## 4. Proposed rendered shape for the three derived files

These become the golden fixtures. Everything the README pins (root `tags.md` frontmatter, H1, section headings, glyphs, indentation, static definitions) is reproduced exactly; everything else is a proposal subject to Q1–Q3.

### 4.1 Fixture KB used by all three examples

```
KB/
├── index.md                       ← generated
├── tags.md                        ← generated
├── skills/voice/SKILL.md
├── BBQ/
│   ├── topic.md                   title "BBQ", description "Barbecue equipment, fuel, and technique"
│   ├── index.md                   ← generated
│   ├── notes/summary.md
│   └── references/summary.md
└── Cooking/
    ├── topic.md                   title "Cooking", description "Home cooking: technique, equipment, and recipes"
    ├── index.md                   ← generated
    ├── expert.md
    ├── notes/
    │   ├── summary.md             "Distilled rules from cooking experience"
    │   ├── grill-performance-in-windy-conditions.md
    │   │     tags: topic.cooking.grilling, topic.cooking.heat-management, type.note, status.approved
    │   │     related_topics: [ bbq.equipment ]
    │   ├── preheat-the-grill.md   status.conflict-review + review_note
    │   └── old-idea/
    │       ├── old-idea.md
    │       └── media/photo.jpg    ← unreferenced → ORPHAN_ASSET
    ├── references/
    │   ├── summary.md
    │   └── grill-basics/
    │       ├── grill-basics.md
    │       └── grill-basics.pdf
    ├── recipes/                   ← extension folder
    │   └── ribeye-on-gas.md
    └── sub-topics/Grilling/
        ├── topic.md               title "Grilling", description "Charcoal and gas grilling"
        ├── index.md               ← generated
        ├── notes/summary.md
        └── references/summary.md
```

### 4.2 Root `index.md` — the Librarian's routing view

Minimal generated frontmatter: **`title`, `description`, `source_type: catalog`** (Q2).

````markdown
---
title: "PKB Topic Catalog"
description: "Every PKB topic with its description — the Librarian's routing view"
source_type: catalog
---

# PKB Topic Catalog

<!-- Generated by pkb.core. Do not edit: this file is overwritten on every flush. -->

## Topics

- [BBQ](BBQ/topic.md) `topic/bbq` — Barbecue equipment, fuel, and technique
- [Cooking](Cooking/topic.md) `topic/cooking` — Home cooking: technique, equipment, and recipes *(custom expert)*
    - [Grilling](Cooking/sub-topics/Grilling/topic.md) `topic/cooking/grilling` — Charcoal and gas grilling
````

**Empty KB** (`golden/empty_root_index.md`):

````markdown
---
title: "PKB Topic Catalog"
description: "Every PKB topic with its description — the Librarian's routing view"
source_type: catalog
---

# PKB Topic Catalog

<!-- Generated by pkb.core. Do not edit: this file is overwritten on every flush. -->

## Topics

_No topics yet._
````

**Degraded entry** (topic root whose `topic.md` is missing or unparseable, GE-25):

```markdown
- [Physics](Physics/topic.md) `topic/physics` — *(missing topic metadata)*
```

**Pinned constants**: link text = `topic.md`'s `title`; link target relative to KB root, POSIX, percent-encoded; agent id backticked; ` — ` (EM DASH U+2014) before the description; 4-space nesting per depth; ` *(custom expert)*` iff that topic root holds `expert.md`; siblings sorted case-insensitively then by codepoint.

### 4.3 Topic `index.md` — the depth index

Minimal generated frontmatter: **`title`, `description`, `topic`, `source_type: index`** (Q2). Sections in fixed order, each **omitted entirely when empty**: Breadth · Needs review · Sub-topics · Notes · References · one section per extension folder (alphabetical) · Tag subtree · Cross-topic mappings · Maintenance flags.

````markdown
---
title: "Cooking — Index"
description: "Canonical index of the Cooking topic"
topic: "Cooking"
source_type: index
---

# Cooking — Index

<!-- Generated by pkb.core. Do not edit: this file is overwritten on every flush. -->

## Breadth

- [Cooking](topic.md) — Home cooking: technique, equipment, and recipes
- [Notes summary](notes/summary.md) — Distilled rules from cooking experience
- [References summary](references/summary.md) — Overview of ingested cooking sources

## Needs review

- [Preheat the grill](notes/preheat-the-grill.md) — Reference 'Grill Basics' says preheat for 10 min. Note says 15 min.

## Sub-topics

- [Grilling](sub-topics/Grilling/index.md) — Charcoal and gas grilling

## Notes

- [Grill Performance in Windy Conditions](notes/grill-performance-in-windy-conditions.md) — How wind affects grill temperature and how to compensate for it · tags: `status.approved` `topic.cooking.grilling` `topic.cooking.heat-management` `type.note`
- [An old idea](notes/old-idea/old-idea.md) — An idea captured before the grill notes · tags: `status.draft` `topic.cooking` `type.note`
- [Preheat the grill](notes/preheat-the-grill.md) — How long to preheat the grill before cooking · tags: `status.conflict-review` `topic.cooking.grilling` `topic.cooking.heat-management` `type.note`

## References

- [Grill Basics](references/grill-basics/grill-basics.md) — Beginner guide to charcoal grilling · tags: `status.approved` `topic.cooking.grilling` `type.reference`

## Recipes

- [Ribeye on gas](recipes/ribeye-on-gas.md) — Reverse-sear ribeye on a three-burner gas grill · tags: `status.approved` `topic.cooking.recipes` `type.note`

## Tag subtree

- `topic.cooking` – root topic
    - `topic.cooking.grilling`
    - `topic.cooking.heat-management`
    - `topic.cooking.recipes` *(topic-specific extension)*

## Cross-topic mappings

- `topic.cooking.grilling` ↔ `topic.bbq.equipment`
- `topic.cooking.heat-management` ↔ `topic.bbq.equipment`

## Maintenance flags

- orphan-asset: `notes/old-idea/media/photo.jpg` (not referenced by `notes/old-idea/old-idea.md`)
````

**Pinned constants**: link target relative to the **topic root**; ` — ` (EM DASH) before the description; ` · tags: ` (MIDDLE DOT U+00B7) before the sorted, backticked, space-separated tag list; `Needs review` shows each `status.conflict-review` file with its `review_note` as the gloss; `Tag subtree` uses the same renderer and glyphs as `tags.md` (` – ` EN DASH U+2013, 4-space indents, full dotted tags in backticks); `Cross-topic mappings` uses ` ↔ ` U+2194 with the local topic always on the left; item bullets sorted by relative POSIX path.

**Minimal topic** (no sub-topics, no extension folders, no conflicts, no flags) renders only Breadth / Notes / References / Tag subtree — a separate golden.

### 4.4 Root `tags.md` — the tag registry

Minimal generated frontmatter: **`title: "PKB Tag Registry"`, `source_type: tag-registry`** — pinned verbatim by README §1.5 L196-199.

````markdown
---
title: "PKB Tag Registry"
source_type: tag-registry
---

# PKB Tag Registry

## Namespace: topic.bbq

- `topic.bbq` – root topic
    - `topic.bbq.equipment`

## Namespace: topic.cooking

- `topic.cooking` – root topic
    - `topic.cooking.grilling`
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

- `domain.legal`
    - `domain.legal.compliance`

## Cross-topic mappings (aggregated from `related_topics`)

- `topic.cooking.grilling` ↔ `topic.bbq.equipment`
- `topic.cooking.heat-management` ↔ `topic.bbq.equipment`
````

**Empty KB** (`golden/empty_tags.md`):

````markdown
---
title: "PKB Tag Registry"
source_type: tag-registry
---

# PKB Tag Registry

## Namespace: type

- `type.note` – human-written note
- `type.reference` – static source
- `type.solution` – reusable solution (a note tagged as a solution)
- `type.summary` – breadth overview

## Namespace: status

- `status.draft` – proposed, awaiting human approval
- `status.approved`
- `status.conflict-review`
````

**Pinned constants**: frontmatter is exactly two keys in that order; H1 exactly `# PKB Tag Registry`; one `## Namespace: topic.<slug>` per **top-level** topic root, sorted case-insensitively, sub-topics nested inside; section order topic\* → type → status → domain → mappings; ` – root topic` (EN DASH) on each topic section's root node; ` *(topic-specific extension)*` after the backticked tag with no dash; static `type`/`status` blocks rendered verbatim regardless of KB contents; `domain.*` rendered as a nested tree (**Q1**); mapping heading including its inline code span, exactly `## Cross-topic mappings (aggregated from \`related_topics\`)`; mapping lines ` ↔ ` U+2194, sorted by `(left, right)`.

---

## 5. Explicitly out of Layer 1

Do **not** build any of the following in `pkb.core`. Each is listed with where it belongs, so an implementer who finds the seam ambiguous has an answer.

**Judgment work (Layer 2 — `pkb.agents`, common skills)**
- Semantic conflict detection, classification, and confidence scoring. Layer 1 only *builds* `ScanRequest` values (MA-11/MA-12); the scan itself is a Topic Expert skill. *(README §1.9 L429-433, §1.7 L293-295)*
- Writing or clearing `status.conflict-review`, `review_note`, or `last_reviewed`. Layer 1 carries these fields through parse/serialize and validates their coupling — nothing more. *(MA-6; README §1.7 L298-332, §2.1 L485-486)*
- Drafting `topic.md` prose, `notes/summary.md`, `references/summary.md` beyond the scaffolder's placeholders. *(README §1.9 L437-439)*
- Tag governance: proposing new tags, approval state, allowlists. Layer 1 has no `add_tag`/`approve_tag` and no persisted approved-tag list. *(TG-9/VA-40; README §1.5 L189-191)*
- Ingestion classification (reference vs note vs solution), sub-topic proposals, `voice.md`/`discovery.md`/`research.md` behaviour. *(README §1.9 L437-447, §2.4)*

**Enforcement plumbing (Layer 2 — middleware)**
- Intercepting `write_file`/`edit_file`, returning an error `ToolMessage` without invoking the handler, and the 3-attempts-per-file-per-run bound. Layer 1 supplies findings; it does not know about tool calls. *(arch §7 L315-333, §8 L371)*
- Recording touched paths in middleware state and clearing them after the flush. Layer 1's `flush` takes the set as an argument. *(arch §7 L345-347; MA-3)*
- Running the flush on both success and failure paths. Layer 1 only guarantees the flush is safe to run. *(arch §7 L348-350; MA-14)*
- The deny `FilesystemPermission` list. Layer 1 exports `is_derived_name` as its single source of truth (PA-11); Layer 2 constructs the permission. *(arch I3 L104-112)*
- Translating between agent-visible `/kb/…` paths and on-disk paths under `kb_root`. *(arch I3 L114, §4 L168-173; CX-3)*
- Acquiring the global KB write lock around the flush. *(arch §8 L363-365; MA-15, Q16)*
- Human-approval gating for topic creation, extension folders, and skill overloads. The scaffolder has no approval parameter. *(SC-8; README §1.9 L428, L466-472)*
- `AgentRegistry`, lazy graph construction, invalidation on topic creation, expert *instantiation*. Layer 1 provides only the pure resolver `resolve_expert` and the id↔path bijection. *(arch §4 L158-165; PA-10, PA-13)*
- Context packs (Research Packs, Implementation Packs). Layer 1 exposes `build_tag_tree`, `files_with_tag`, and the indexes that make packs assemblable; it does not assemble them. *(README Part 4 L681-690; TG-10, TG-11)*

**Transport and persistence (Layer 3 — `pkb.server`, daemon)**
- Persisting scan requests to the SQLite queue table and the background dequeue worker. *(arch §7 L354-357; MA-11)*
- Surfacing findings, pending approvals, or conflict escalation in the TUI, Telegram, or MCP. Layer 1's findings are a return value with no rendering opinion beyond the topic index's flags section. *(arch §6; MA-10)*
- Thread metadata, checkpointing, SSE, the approval diff modal.

**Deferred within Layer 1 (do not build in v1; the finding codes leave room)**
- Wiki-style `[[target]]` links and in-document heading anchors in the link checker. *(MA-7)*
- `DUPLICATE_ACROSS_TOPICS` detection. *(Q21)*
- `updated`/`created` dates rendered into index entries, and any "Recently changed" section. *(Q17, GE-6)*
- Root-scope findings rendered into root `index.md` — report-only in v1. *(MA-10)*
- A core-owned filesystem lock context manager. *(MA-15, Q16)*
- `domain.*` tags embedded in topic indexes — the tag subtree is `topic.*` only. *(GE-17)*

**Never, at any layer, per the spec**
- A conflict registry, resolution log, loser marker, confidence score, or any persistent record that a conflict occurred. `last_reviewed` is the only permitted trace. *(README §1.7 L364-371; VA-30, GE-28)*
- Overwriting, moving, or deleting human content. Layer 1 flags; it never repairs. *(README §1.7 L278; MA-9, SC-10)*
- Version control, undo, backups, or shelling out to git in the first draft. *(arch D6 L33, §10 L403-408; CX-4)*
- Network I/O of any kind, including validating external URLs. *(arch I1 L92-95; MA-7)*

---

## 6. Appendix — proposed public API surface

Provided so the rule IDs above map to concrete call sites. Names are a proposal; the *shapes* are constrained by the rules cited.

```python
# pkb/core/frontmatter.py
def parse(text: str) -> ParseResult            # FM-1, FM-13 — never raises
def serialize(meta: Metadata, body: str) -> str # FM-7, FM-8
def set_field(text: str, key: str, value) -> str    # FM-11
def remove_field(text: str, key: str) -> str        # FM-11
def normalize_related_topic(value: str) -> str      # FM-15
REQUIRED_FIELDS: frozenset[str]                     # FM-2
AUTHORED_SOURCE_TYPES / DERIVED_SOURCE_TYPES: frozenset[str]   # FM-6

# pkb/core/paths.py
def slugify(name: str) -> str                                   # PA-8
def is_topic_root(path: Path) -> bool                           # PA-3
def find_topic_roots(kb_root: Path) -> list[Path]               # PA-5
def owning_topic_root(kb_root: Path, path: Path) -> Path | None # PA-15
def topic_tag_for(kb_root: Path, topic_path: Path) -> str       # PA-9
def agent_id_for(kb_root: Path, topic_path: Path) -> str        # PA-10
def topic_path_for_agent_id(kb_root: Path, agent_id: str) -> Path
def is_derived_name(kb_root: Path, path: Path) -> bool          # PA-11
def is_generated(kb_root: Path, path: Path) -> bool             # PA-12
def resolve_expert(kb_root: Path, topic_path: Path) -> Path | None   # PA-13
def resolve_skills(kb_root: Path, topic_path: Path) -> dict[str, Path]  # PA-14
def link_target(from_dir: Path, to_path: Path) -> str           # PA-18
STRUCTURAL_DIRS / RESERVED_NAMES / IGNORED_NAMES: frozenset[str] # PA-6, PA-19, PA-16

# pkb/core/tags.py
class Tag:  parse / namespace / segments / depth / parent / ancestors   # TG-1
def validate_tag(raw: str) -> list[Finding]                      # TG-2..TG-4, TG-6, TG-7
def build_tag_tree(kb_root: Path) -> TagTree                     # TG-10
def files_with_tag(kb_root: Path, tag: str) -> list[Path]        # TG-11
TYPE_DEFINITIONS / STATUS_DEFINITIONS: Mapping[str, str | None]  # TG-12

# pkb/core/validation.py
def validate_content(kb_root: Path, rel_path: str, text: str) -> list[Finding]  # VA-1
def validate_tree(kb_root: Path) -> list[Finding]                               # VA-1

# pkb/core/generators/
def render_topic_index(model) -> str ; def generate_topic_index(kb_root, topic_path) -> bool
def render_root_index(model)  -> str ; def generate_root_index(kb_root) -> bool
def render_root_tags(model)   -> str ; def generate_root_tags(kb_root) -> bool
def regenerate_all(kb_root: Path, *, today: date) -> FlushReport                # GE-30
def write_derived(path: Path, text: str) -> bool                                # GE-8

# pkb/core/scaffold.py
def scaffold_topic(kb_root, topic_path, *, title, description, regenerate=True) -> ScaffoldResult
def scaffold_subtopic(kb_root, parent_topic_path, name, *, title, description) -> ScaffoldResult

# pkb/core/maintenance.py
def flush(kb_root: Path, touched_paths: set[Path], *, today: date) -> FlushReport  # MA-1
def bump_updated(kb_root, paths: set[Path], today: date) -> list[Path]             # MA-3
def find_broken_links(kb_root: Path) -> list[Finding]                              # MA-7
def find_orphans(kb_root: Path) -> list[Finding]                                   # MA-8
def build_scan_requests(kb_root, changed: set[Path], *, origin) -> list[ScanRequest]  # MA-12

# shared types
@dataclass(frozen=True) class Finding:      code, severity, path, message, field, value, line, rule_id
@dataclass(frozen=True) class ScanRequest:  topic_id, topic_path, changed_paths, origin, requested_at
@dataclass             class FlushReport:   written, unchanged, findings, scan_requests
```

---

## 7. As built

Written after implementation and an adversarial audit (44 claims raised, 26 confirmed by an
independent verifier and fixed, 18 refuted). Everything here is a deviation from, or an addition to,
the sections above — the rules themselves are unchanged unless a row says so.

### 7.1 Modules beyond the architecture doc's §3 listing

| Module | Why it exists |
|--------|---------------|
| `models.py` | The shared data model — `Metadata`, `ParsedDocument`, `FileRecord`, `TopicRecord`, `KbSnapshot`, `ScanRequest`, `FlushReport`, `ScaffoldResult`. Decision C's single walk needs a single set of types. |
| `errors.py` | `Finding`, `Severity`, and the exception hierarchy (decision B). |
| `scan.py` | The one tree walk. Validation, generation and maintenance read its `KbSnapshot`; none walks the tree itself. |
| `analysis.py` | Broken-link and orphan detection, extracted from `maintenance.py` so the *generators* can compute the `## Maintenance flags` section themselves. Without that, `regenerate_all` and `flush` rendered different bytes for the same tree and whichever ran last won (GE-5). |
| `diagnostics.py` | The three findings both the walk and the rule engine can see — `MISPLACED_TOPIC_ROOT`, `UNEXPECTED_ROOT_ENTRY`, `FRONTMATTER_PARSE_ERROR`. CX-6 has Layer 2 show `message` to the model verbatim, so one defect must not be worded two ways depending on the entry point. |
| `generators/base.py`, `generators/derive.py` | The atomic skip-if-identical write, the `inline()` text helper, and the shared derivations (tag subtree, cross-topic pairs) the three generators share. |

### 7.2 Rule clarifications the implementation forced

| Rule | As built |
|------|----------|
| PA-5 / VA-36 | `media/` and `skills/` are closed to topic **discovery** on both walks; `notes/` and `references/` are the two VA-36 re-opens. A `topic.md` under `media/` or `skills/` is not a topic root — VA-6 keeps skills out of tag generation and GE-15 excludes them from the index, so treating one as a topic would mint a tag and an agent id for a directory nothing may render into. `scan.RECORD_ONLY_DIRS` and `paths._MISPLACED_DESCENT` state the same policy from either side and must move together. `owning_topic_root` and `is_generated` honour it too, or a `SKILL.md` beside a smuggled `topic.md` would collect the seven required-field errors VA-6 exists to prevent. |
| FM-11 | `set_field` / `remove_field` are a line-scoped textual editor over the frontmatter region, not a ruamel load-mutate-dump: round-tripping through ruamel rewrites `[ a, b ]` as `[a, b]` and reflows sequence indentation, which FM-11 exists to prevent. They also refuse any block Layer 1 cannot read, so a fence that is not a YAML mapping is never edited. |
| FM-8 / FM-9 | The README §1.4 block round-trips byte-identically except its inline comment, which FM-9 explicitly does not require the serializer to reproduce. `set_field` *does* preserve comments, which is the path a human's file actually takes. |
| GE-30 | `regenerate_all(kb_root)` takes no `today`: GE-6 forbids dates in derived output, so the clock has no consumer there. `flush` still takes `today` (MA-3). |
| TG-10 | `build_tag_tree` and `files_with_tag` take a `KbSnapshot`, not a `kb_root` (decision C). |

### 7.3 Finding codes added

`STALE_DERIVED_FILE` (PA-12) — an `index.md` that is derived by name but generated by nobody;
never written, never deleted, now never invisible. `DERIVED_NAME_CASE_COLLISION` (PA-17) — an
authored file holds the derived name under a different case; on a case-insensitive filesystem
writing would destroy it, so the write is refused and the human is told to rename.
`UNADDRESSABLE_TOPIC_ROOT` (PA-8) — a topic folder whose every segment slugifies away; kept under a
fallback address rather than dropped. `UNREADABLE_FILE` (MA-14), `DERIVED_WRITE_FAILED` (GE-8) and
`UPDATED_WRITE_FAILED` (MA-3) — I/O failures reported so the flush can finish everything else.

### 7.4 Carried forward to the next specs

- **Architecture I3** protects `/kb/**/index.md`, `/kb/index.md`, `/kb/tags.md`. It should also
  protect `/kb/**/tags.md`: a `tags.md` at a topic root is maintained by no generator (C14, VA-27).
  Layer 2 should build the deny list from `pkb.core.is_derived_name` rather than restating globs.
- **Architecture §4** describes the registry scanning `*/topic.md`. That shorthand reads as depth-1;
  discovery is recursive, and sub-topics are addressable agents (C1).
- **README amendment #3** — `skills/**` is a third file class, exempt from PKB frontmatter and from
  index and tag generation, validated only for `name` and `description` (C3, VA-6). This joins the
  two amendments the architecture doc §12 already lists.
- **Q1–Q21 defaults** are implemented as recommended (except decision A in §0). The ones worth a
  human's eye, because they shape what the human sees in the KB rather than how the code is built:
  Q1 (`domain.*` renders as a nested tree), Q2 (frontmatter on the generated index files), Q3 (the
  topic index layout in §4.3), Q9 (the root catalog lists sub-topics), Q11 (exactly one `type.*` and
  one `status.*` tag per file is an error, not a warning).
