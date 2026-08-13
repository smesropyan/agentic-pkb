# Phase 1: The Tree Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the mechanical tree layer of `pkb.core` against `DESIGN.md` §1, so a scaffolded
topic, its files and the derived surfaces (each topic's `index.md`, the root `tags.md` registry)
match the new design exactly.

**Architecture:** Re-implementation in place. `pkb.core` stays plain Python — no LLM, no network, no
subprocess, no database, no git — and stays the only writer of derived files. The salvage bin
(frontmatter parsing, tag machinery, walkers, scaffolder, generator framework) is modified and
re-tested; the dead machinery (root `index.md`, the `status.*` namespace, `review_note` and
`last_reviewed`, extension folders, the scan-trigger hooks) is deleted with its tests. New `T-*`
rules are minted first and every docstring and test cites them.

**Tech Stack:** Python 3.11+ via `uv`, pytest on `tmp_path`, no new dependencies.

## Global Constraints

- `DESIGN.md` §1 is the contract; where it is silent this plan proposes and flags the proposal.
- `make check` is green after every task. The suite currently passes 2215 with 6 deselected; the
  counts move as superseded tests are deselected and new ones land — the gate, not the count, is
  the constraint. `addopts` becomes `-m 'not live and not superseded'` in Task 2 and each later
  task's new tests run un-marked.
- Derived output is byte-idempotent, carries no timestamps and no counts.
- Findings, not exceptions, for content defects. KB-relative POSIX strings in every `Finding`.
- Nothing moves or deletes operator content. There is no undo.
- Commit after every task with the message the task names, plus the standard trailer:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_013RAwz2RWewccxyv7ZqeRz3`.

## File Structure

| File | Fate |
|---|---|
| `docs/superpowers/specs/2026-08-13-tree-T-rules.md` | Created: the `T-*` rules (Task 1) |
| `pyproject.toml` | Modified: addopts deselects `superseded` (Task 2) |
| `src/pkb/core/frontmatter.py` | Modified: fields shrink (Task 3) |
| `src/pkb/core/tags.py` | Modified: `status` namespace removed (Task 4) |
| `src/pkb/core/validation.py` | Modified: rules removed and added (Tasks 4, 5, 8) |
| `src/pkb/core/models.py` | Modified: `FileRole` loses extension/root-index members, gains `SESSION` and `CAPTURED_SOURCE` (Task 5) |
| `src/pkb/core/paths.py` | Modified: classifier and root layout (Task 5) |
| `src/pkb/core/scan.py` | Modified: root layout, captured sources, `AUTHORSHIP.md` (Task 5) |
| `src/pkb/core/generators/root_index.py` | Deleted; `(custom expert)` logic moves to the registry (Task 6) |
| `src/pkb/core/generators/tags_registry.py` | Modified: summaries, markers, skills catalog (Task 6) |
| `src/pkb/core/generators/topic_index.py` | Modified: skills catalog, approach entries (Task 7) |
| `src/pkb/core/generators/derive.py` | Modified: extension-marker machinery removed (Task 6) |
| `src/pkb/core/scaffold.py` | Modified: no `status.draft`, `## Skills` in `topic.md` (Task 8) |
| `src/pkb/core/maintenance.py` | Modified: scan-trigger machinery removed (Task 9) |
| `tests/core/test_tree_rules.py` | Created: the new `T-*` conformance suite, one test cites one rule (Tasks 3–9) |
| `tests/core/golden/tags.md`, `empty_tags.md` | Regenerated for the new registry (Task 6) |
| `tests/core/test_design_example.py` | Created: the proves-itself test (Task 10) |

## Two proposals this plan makes where the design is silent

**P1 — the approach-entry shape (Task 7).** DESIGN §1.1/§1.9 say each topic `index.md` carries one
entry per approach a breadth file lists, a lift not a judgment, but fix no on-disk shape for the
listing. Proposal: a breadth file may carry a `## Approaches` section whose items are
`- <name>: <kb-path>#<heading>`; the generator copies exactly those lines. **Checkpoint: present
this to the operator with Task 7's diff; one line reverses it.**

**P2 — a root `index.md` on disk is reported, not deleted (Task 5).** The design says the root
holds no `index.md`; an existing one (every current KB has one) is operator-visible content the
mechanical layer must not delete (no-undo). Proposal: `UNEXPECTED_ROOT_ENTRY` finding names it and
the generator simply stops writing it. **Checkpoint with Task 5's diff.**

---

### Task 1: Mint the `T-*` rules

**Files:**
- Create: `docs/superpowers/specs/2026-08-13-tree-T-rules.md`

**Interfaces:**
- Produces: rule ids `T-1`…`T-n` that every later task's docstrings and tests cite.

- [ ] **Step 1:** Read `DESIGN.md` §1 (all ten subsections) and write the rules file: a table of
  `T-*` rows — id, rule text (quoting §1 where exact), severity, and the check that will assert it.
  Cover at minimum: the topic structure and structural dirs (no extension folders, `sub-topics/`
  recursion); the three file classes with `AUTHORSHIP.md` in class 3 and captured sources exempt
  whatever their extension; the seven required fields and `related_topics` as the only optional
  field; the three namespaces with `type.*` the one closed set (`note`, `solution`, `reference`,
  `summary`) and the four tag rules (≥1 `topic.*`, exactly one `type.*`, depth ≤ 4, nested implies
  parent); the registry contents (tag tree, one-line summary per topic-backed node lifted from
  `topic.md`'s `description`, `*(custom expert)*` marker, cross-topic mappings from
  `related_topics`, skills catalog, `domain.*` bare, `type.*` static definitions); no root
  `index.md`; each topic `index.md`'s contents (file entries with descriptions, tag subtree, own
  skills catalog, approach entries per P1); byte-idempotent derived output; per-write validation
  and per-run regeneration; the `## Skills` section in `topic.md`; root `sessions/*.md` accepted as
  knowledge files with `topic: "(session)"` and no location-agreement check (the full session rules
  are Phase 2's `S-*`).
- [ ] **Step 2:** Self-check: walk §1.1–§1.10 and confirm every subsection maps to ≥1 rule; list the
  mapping at the bottom of the file.
- [ ] **Step 3:** Commit: `spec: mint the T-rules for the tree`.

### Task 2: Deselect the superseded core tests

**Files:**
- Modify: `pyproject.toml` (addopts)
- Modify: test files found by the greps below (marker lines only)

- [ ] **Step 1:** Change addopts to `-q --strict-markers -m 'not live and not superseded'`.
- [ ] **Step 2:** Mark `@pytest.mark.superseded` (module-level `pytestmark` where a whole file dies)
  every test matched by these greps — the old design's assertions:
  `grep -rln 'status\.draft\|status\.approved\|status\.conflict-review\|review_note\|last_reviewed' tests/`
  `grep -rln 'EXTENSION\|extension_folder\|topic-specific\|recipes/' tests/core tests/agents`
  `grep -rln 'root_index\|ROOT_INDEX\|render_root_index' tests/`
  `grep -rln 'scan_queue\|_SCAN_TRIGGER_ROLES\|ScanRequest\|on_demand_request' tests/`
  Mark at the narrowest level that isolates the assertion (function over module where a file mixes
  live and dead). Do not delete anything yet: deletion is Task 11, after replacements exist.
- [ ] **Step 3:** `make check` — green; record the new passed/deselected counts in the commit body.
- [ ] **Step 4:** Commit: `test: deselect the superseded core assertions`.

### Task 3: Frontmatter — the fields shrink

**Files:**
- Modify: `src/pkb/core/frontmatter.py`, `src/pkb/core/models.py` (the two retired model fields)
- Test: `tests/core/test_tree_rules.py` (created here)

**Interfaces:**
- Produces: `OPTIONAL_FIELDS == frozenset({"related_topics"})`; `CANONICAL_ORDER` without
  `review_note`/`last_reviewed`; `Frontmatter` model without those attributes. Consumed by Tasks
  4–8.

- [ ] **Step 1: Write the failing tests**

```python
# tests/core/test_tree_rules.py
"""Conformance tests for the T-rules (docs/superpowers/specs/2026-08-13-tree-T-rules.md)."""
from pkb.core import frontmatter

def test_related_topics_is_the_only_optional_field_t():  # cite the T-id from Task 1
    assert frontmatter.OPTIONAL_FIELDS == frozenset({"related_topics"})

def test_retired_fields_are_unknown_not_known_t():
    for retired in ("review_note", "last_reviewed", "provenance", "status"):
        assert retired not in frontmatter.KNOWN_FIELDS

def test_a_retired_field_round_trips_as_unknown_t(tmp_path):
    text = (
        "---\ntitle: \"T\"\ndescription: \"D\"\ntopic: \"Cooking\"\n"
        "tags:\n  - topic.cooking\n  - type.note\ncreated: 2024-01-01\nupdated: 2024-01-02\n"
        "source_type: note\nreview_note: \"old\"\n---\nbody\n"
    )
    doc = frontmatter.parse(text)
    assert "review_note" in doc.meta.unknown_fields
```

- [ ] **Step 2:** `uv run pytest tests/core/test_tree_rules.py -v` — FAIL (fields still known).
- [ ] **Step 3:** Remove the two fields from `OPTIONAL_FIELDS`, `CANONICAL_ORDER`, `_QUOTED_FIELDS`
  (`review_note` only), the field constructors around `frontmatter.py:320`, and the two attributes
  on the model in `models.py`. Chase every `mypy` error the removal surfaces — `validation.py`'s
  VA-28/VA-29 references break here and are stubbed out with the rule removals completed in Task 4.
- [ ] **Step 4:** `uv run pytest tests/core/test_tree_rules.py -v` — PASS. `make check` — green.
- [ ] **Step 5:** Commit: `feat: shrink frontmatter to seven required fields plus related_topics`.

### Task 4: Tags — three namespaces, one closed set

**Files:**
- Modify: `src/pkb/core/tags.py`, `src/pkb/core/validation.py`
- Test: `tests/core/test_tree_rules.py`

**Interfaces:**
- Produces: `Namespace` = `{TOPIC, TYPE, DOMAIN}`; no `STATUS_DEFINITIONS`; validation requiring
  ≥1 `topic.*` and exactly one `type.*` and no `status.*` anywhere. Consumed by Tasks 5–8.

- [ ] **Step 1: Write the failing tests**

```python
from pkb.core import tags

def test_three_namespaces_and_type_is_the_closed_set_t():
    assert {n.value for n in tags.Namespace} == {"topic", "type", "domain"}
    assert not hasattr(tags, "STATUS_DEFINITIONS")

def test_a_status_tag_is_an_unknown_namespace_finding_t(tmp_path):
    # build a one-note KB via the scaffolder, add "status.approved" to its tags,
    # run pkb.core.scan + validate, and assert a finding with code UNKNOWN_TAG_NAMESPACE
    ...  # concrete body written against the existing scan/validate call shape in test_scan.py
```

- [ ] **Step 2:** Run — FAIL. **Step 3:** Delete `Namespace.STATUS`, the status vocabulary block
  (`tags.py` ~130–137), `STATUS_DEFINITIONS`, and the status branch of the registry renderer's
  definition sections; in `validation.py` delete VA-29 whole and the exactly-one-`status.*` clause
  of the tag-count rule, keeping ≥1 `topic.*` and exactly-one `type.*` under their new `T-*` ids.
- [ ] **Step 4:** Run tests, then `make check` — the golden registry fixtures now fail; regenerate
  them ONLY if Task 6 is not reordered before this lands — otherwise mark the two golden tests
  `superseded` here and Task 6 replaces them. **Step 5:** Commit:
  `feat: three tag namespaces, type the one closed set`.

### Task 5: Classifier — the new tree shape

**Files:**
- Modify: `src/pkb/core/models.py` (FileRole), `src/pkb/core/paths.py`, `src/pkb/core/scan.py`,
  `src/pkb/core/validation.py` (location tables)
- Test: `tests/core/test_tree_rules.py`

**Interfaces:**
- Produces: `FileRole` without `ROOT_INDEX`, `EXTENSION_SUMMARY`, `EXTENSION_ITEM`; with `SESSION`
  and `CAPTURED_SOURCE`. Root layout = `tags.md`, `skills/`, `sessions/`, topic dirs; root
  `index.md` yields `UNEXPECTED_ROOT_ENTRY` (P2). A non-`SKILL.md` file inside a skill folder other
  than `AUTHORSHIP.md` warns `LEGACY_SKILL_LAYOUT` as today; `AUTHORSHIP.md` is class 3, never
  parsed. Every file under `references/<src>/` except `<src>.md` classifies `CAPTURED_SOURCE`
  whatever its extension and is never opened for YAML. `sessions/*.md` at the root classifies
  `SESSION`, parsed as a knowledge file, with the topic-location agreement checks skipped.

- [ ] **Step 1:** Failing tests: one per produced behaviour above, built on `tmp_path` trees the way
  `tests/core/test_scan.py` builds them (six tests, each citing its `T-*` id; the captured-source
  test writes `references/book/book.md` plus `references/book/extract.md` and asserts the second is
  `CAPTURED_SOURCE` with `doc is None`).
- [ ] **Step 2:** Run — FAIL. **Step 3:** Implement: delete the three `FileRole` members and every
  table row naming them (`_SOURCE_TYPES_BY_ROLE`, `_TYPE_TAGS_BY_ROLE` derive; VA-16/VA-38's
  extension clauses; `RECORD_ONLY_DIRS` extension entries); add the two new members and their
  classification in `paths.classify`; extend the root-layout check in `scan.py` (~line 319).
- [ ] **Step 4:** Run, then `make check`. **Step 5:** Commit:
  `feat: classify the new tree shape — sessions in, extensions and root index out`.

### Task 6: The registry is the one derived root file

**Files:**
- Modify: `src/pkb/core/generators/tags_registry.py`, `src/pkb/core/generators/derive.py`,
  `src/pkb/core/generators/__init__.py`
- Delete: `src/pkb/core/generators/root_index.py`
- Test: regenerate `tests/core/golden/tags.md`, `tests/core/golden/empty_tags.md`; new tests in
  `tests/core/test_tree_rules.py`

**Interfaces:**
- Consumes: Task 4's namespaces, Task 5's roles.
- Produces: `render_root_tags(snapshot: KbSnapshot, *, shipped_skills: Sequence[SkillEntry]) -> str`
  where `SkillEntry = tuple[str, str]` (name, description), rendering per topic-backed `topic.*`
  node ` – <description from that topic's topic.md>` and `CUSTOM_EXPERT_MARKER` (constant moves
  here from `root_index.py`), a `## Skills` section from `shipped_skills` plus root `skills/`
  entries, cross-topic mappings unchanged, `domain.*` nodes bare, `type.*` static definitions
  unchanged. `generate_root_tags` gains the same keyword. `generators/__init__.py` stops writing
  the root index and passes `shipped_skills=()` by default so Layer 1 keeps zero knowledge of the
  package-data mount (the daemon supplies the real list — Phase 5 wires it).

- [ ] **Step 1:** Failing tests: build the DESIGN §1.6 example tree on `tmp_path` (Cooking with the
  example description and an `expert.md`, the baking sub-topic, one note carrying
  `related_topics`), call `render_root_tags`, and assert the example's marked lines appear exactly:
  the described node, the `*(custom expert)*` node, a bare `domain.*` node, and the `## Skills`
  section head. Plus a byte-idempotence test (render twice, identical) and a lifted-not-authored
  test (change the `topic.md` description, re-render, the node line follows it).
- [ ] **Step 2:** Run — FAIL. **Step 3:** Implement; delete `root_index.py` and the
  `EXTENSION_MARKER` machinery in `derive.py`; regenerate both goldens with the repo's existing
  regeneration path and eyeball the diff against DESIGN §1.6 before accepting.
- [ ] **Step 4:** Run, `make check`. **Step 5:** Commit:
  `feat: the registry carries the catalog — summaries, markers, skills; no root index`.

### Task 7: The topic index — skills catalog and approach entries

**Files:**
- Modify: `src/pkb/core/generators/topic_index.py`
- Test: `tests/core/test_tree_rules.py`

**Interfaces:**
- Consumes: Task 5's roles, Task 6's registry conventions (same renderer for the tag subtree).
- Produces: each topic `index.md` gains `## Skills` (that topic's own `skills/*/SKILL.md` name +
  description, no parent's, no shipped) and `## Approaches` (P1: verbatim copy of `- <name>:
  <kb-path>#<heading>` items from the topic's breadth files, source file noted per entry).

- [ ] **Step 1:** Failing tests: a topic with one skill folder and a `topic.md` carrying a
  `## Approaches` list renders both sections; a topic with neither renders neither heading; a
  sub-topic repeats no parent skill. **Step 2:** Run — FAIL. **Step 3:** Implement.
- [ ] **Step 4:** Run, `make check`. **Step 5:** Commit:
  `feat: topic index carries its own skills and the approach entries` — and present P1 to the
  operator in the task report.

### Task 8: Scaffolder — the new topic shape

**Files:**
- Modify: `src/pkb/core/scaffold.py`
- Test: `tests/core/test_tree_rules.py`

- [ ] **Step 1:** Failing tests: a scaffolded topic's `topic.md` contains a `## Skills` section;
  no scaffolded file carries any `status.*` tag; the scaffolded tree validates with zero errors
  under the new rules. **Step 2:** Run — FAIL. **Step 3:** Implement (drop the `status.draft`
  stamping at `scaffold.py` placeholder templates, add the section). **Step 4:** Run, `make check`.
- [ ] **Step 5:** Commit: `feat: scaffold the new topic shape`.

### Task 9: Maintenance — regeneration only

**Files:**
- Modify: `src/pkb/core/maintenance.py`
- Test: `tests/core/test_tree_rules.py`

- [ ] **Step 1:** Failing test: `maintenance` exposes no scan-trigger surface
  (`not hasattr(maintenance, "_SCAN_TRIGGER_ROLES")` plus the public API check that per-run
  regeneration covers exactly the topic indexes and the registry). **Step 2:** Run — FAIL.
- [ ] **Step 3:** Delete the scan-trigger roles, the enqueue hook and their docstrings; keep
  per-write validation and per-run regeneration. **Step 4:** Run, `make check`. **Step 5:** Commit:
  `feat: maintenance regenerates and validates, nothing else`.

### Task 10: The proves-itself test

**Files:**
- Create: `tests/core/test_design_example.py`

- [ ] **Step 1:** One end-to-end test, written to pass: scaffold a KB on `tmp_path`, create the
  DESIGN §1.6 example topics, file a note and a reference through the frontmatter serializer, run
  scan + validate (zero errors), regenerate everything twice (byte-identical both times), and
  assert the registry excerpt lines from DESIGN §1.6 appear verbatim. This is the phase's
  proves-itself scenario from the roadmap, kept as a living test.
- [ ] **Step 2:** `make check`. **Step 3:** Commit: `test: the tree proves itself against DESIGN §1.6`.

### Task 11: Delete the superseded core tests and dead exports

**Files:**
- Delete: every test Task 2 marked that lives under `tests/core/` (their subjects are rebuilt);
  agents/server/packs tests keep their markers for Phases 3–5.
- Modify: `src/pkb/core/__init__.py` exports; `src/pkb/contracts.py` if it names deleted symbols.

- [ ] **Step 1:** `grep -rln 'pytestmark.*superseded\|mark\.superseded' tests/core/` — delete those
  tests (whole files where wholly dead). **Step 2:** Remove dead exports; run
  `grep -rn 'root_index\|STATUS_DEFINITIONS\|EXTENSION_MARKER' src/` — must be empty.
- [ ] **Step 3:** `make check` — green; record final counts. **Step 4:** Commit:
  `chore: remove the superseded tree tests and dead exports`.

---

## Self-Review (run after Task 11)

1. **Spec coverage:** walk `DESIGN.md` §1.1–§1.10 and the T-rules table; every rule has a citing
   test in `test_tree_rules.py` or `test_design_example.py`. List gaps as new tasks.
2. **Placeholder scan:** the one intentionally elided test body (Task 4 Step 1's second test) must
   be concrete in the written test file; no `...` survives into committed tests.
3. **Type consistency:** `SkillEntry`, `FileRole.SESSION`, `FileRole.CAPTURED_SOURCE` and the
   `render_root_tags` keyword match across Tasks 5–7 and the tests.
4. **The two proposals:** P1 and P2 were surfaced to the operator with their tasks' reports.

## Verification

- `make check` green at every commit; final counts recorded.
- `tests/core/test_design_example.py` is the end-to-end proof and stays in the suite.
- Idempotence: the double-regeneration assertion in Task 10.
- Layer discipline: import-linter still reports 5 kept contracts (pkb.core imports nothing above it).
