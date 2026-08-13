# PKB Re-implementation Roadmap

> **For agentic workers:** this is the roadmap, not a task-level plan. Each phase below gets its own
> plan in `docs/superpowers/plans/` written with `superpowers:writing-plans` when the phase starts,
> and executed with `superpowers:subagent-driven-development` or `superpowers:executing-plans`.
> Phase 0 is task-level already and executes directly.

**Goal:** Rebuild the PKB against `README.md` (the specification) and `DESIGN.md` (the technical
design, ten sections), replacing the code and layer specs that implement the superseded design.

**Architecture:** The operator ruled re-implement, not reconcile. Existing code is reference
material and a parts bin, not a base to patch: a module is salvaged only where the design did not
move, and every salvaged module is re-tested against the new design's rules rather than trusted.
The build order follows the design's own dependency arc: the mechanical tree first, then sessions,
then the agents and their skills, then the workflows and the loop, transports last because they
changed least.

**Tech Stack:** Python 3.11+ via `uv`, deepagents harness, Ollama models (`deepseek-v4-flash:cloud`
default, local `gemma4:31b` fallback), SQLite, FastAPI/SSE, Textual, Telegram Bot API. Unchanged
from the previous build unless a phase plan argues otherwise.

## Global Constraints

- `README.md` and `DESIGN.md` are the contract. Where code and design disagree, the design wins;
  where the design is silent, the phase plan proposes and the operator rules. Do not edit either
  document from an implementation branch without an operator ruling.
- Every phase produces working, tested software behind `make check` (pytest, ruff, mypy strict,
  import-linter). A phase is not done with a red gate.
- TDD per `superpowers:test-driven-development`: the failing test precedes the implementation.
- New rule ids are minted per design section (`T-*` for §1 The Tree, `S-*` §2 Sessions, `A-*` §3,
  `K-*` §4, `W-*` §5, `C-*` §6, `L-*` §7, `H-*` §8), one rules file per phase under
  `docs/superpowers/specs/`, superseding the old layer specs.
- The old layer specs are archived, never deleted: they carry the measurements and the defect
  history that priced this design.
- Derived files are byte-idempotent and carry no timestamps or counts. Only the mechanical layer
  writes derived files. There is no undo: nothing moves or deletes operator content.
- The repo is public. No credentials in any committed file; `.env` stays gitignored and mode 600.

---

## Phase 0: Ground the repo (task-level, execute directly)

Small, mechanical, and it stops every later phase from tripping over the old design's paperwork.

### Task 0.1: Archive the old layer specs

**Files:**
- Create: `docs/superpowers/specs/superseded/` (move the seven `2026-08-0*-pkb-*.md` and
  `2026-08-07-large-source-ingestion.md` files into it)
- Create: `docs/superpowers/specs/superseded/README.md`

- [ ] **Step 1:** `git mv` the eight old spec files into `superseded/`.
- [ ] **Step 2:** Write `superseded/README.md`, five lines: these specs describe the design that
  `DESIGN.md` replaced on 2026-08-13; they are kept for their measurements and defect history; no
  rule id in them binds the new build; the citation forms `README §X.Y` and `DESIGN §X.Y` in them
  refer to the documents as they stood at commit `592d19d`.
- [ ] **Step 3:** `grep -rn 'docs/superpowers/specs/2026' src/ tests/ Makefile` — repoint any path
  that breaks (expected: none; docstrings cite ids, not paths).
- [ ] **Step 4:** `make check` — green.
- [ ] **Step 5:** Commit: `chore: archive the superseded layer specs`.

### Task 0.2: Rewrite CLAUDE.md for the re-implementation

**Files:**
- Modify: `CLAUDE.md` (full rewrite, ~60 lines replacing ~140)

- [ ] **Step 1:** Rewrite with exactly these sections: what the project is (two sentences pointing
  at `README.md` and `DESIGN.md`); the ruling (re-implement, not reconcile — existing `src/` and
  `tests/` implement the superseded design and are reference material until a phase rebuilds them);
  the roadmap pointer (this file); the Models section carried over verbatim (it is still true); the
  Conventions that survive (rule-ids-are-the-contract, findings-not-exceptions, KB-relative POSIX
  paths, derived-files-byte-idempotent, no-undo, `.env` handling); the Commands block unchanged.
- [ ] **Step 2:** Delete the old build-order table and the per-layer status paragraphs.
- [ ] **Step 3:** `make check` — green. Commit: `docs: point CLAUDE.md at the new design`.

### Task 0.3: Quarantine the old suite's design assertions

The suite is green against the old design and will go red piecemeal as phases land. Decide the
mechanism once, here, instead of per phase.

- [ ] **Step 1:** Add a pytest marker `superseded` to `pyproject.toml` markers list, with the
  comment: asserts the superseded design; removed when the owning phase rebuilds the module.
- [ ] **Step 2:** No test is marked yet. Each phase plan marks the tests its rebuild invalidates in
  its first task, so the gate stays green throughout rather than red for weeks.
- [ ] **Step 3:** Commit: `test: add the superseded marker for the phased rebuild`.

---

## Phase 1: The Tree (DESIGN §1) — the mechanical layer

**Builds:** topic scaffolding; the three-class file model; frontmatter (seven required fields, no
`status.*`, no `review_note`/`last_reviewed`, no provenance field); three tag namespaces with
`type.*` the one closed set; the registry as the one derived root file (tag tree, one-line summary
per topic node lifted from `topic.md`, `(custom expert)` markers, cross-topic mappings, skills
catalog; `domain.*` bare); each topic's `index.md` with tag subtree, skills catalog and
per-approach entries; no root `index.md`; captured sources exempt from frontmatter whatever their
extension; the skills section inside `topic.md`; `AUTHORSHIP.md` in file class 3.

**Salvage bin (re-test, then keep what fits):** `core/frontmatter.py`, `core/tags.py` machinery,
`core/paths.py` walkers, `core/scaffold.py`, `core/generators/` framework and `topic_index.py`,
`core/scan.py`. **Dies with the old design:** `generators/root_index.py`, the `status.*` closed
set, `VA-29` coupling, extension-folder machinery (`PA-7`, `VA-16`, `VA-38`, `GE-14`, `GE-24`),
`_SCAN_TRIGGER_ROLES` and the scan queue hooks in `core/maintenance.py`.

**Proves itself by:** a scaffolded topic, files filed under the new frontmatter, and a regenerated
registry matching DESIGN §1.6's worked example byte for byte.

## Phase 2: Sessions (DESIGN §2) — the session file and the service

**Builds:** one file per session at the root for its whole life (objective and experts, running
record, synthesis with instruction sets, distillation); `topic: "(session)"` and a `topic.*` tag
per expert; the seven commands including `/name` (rename moves the file and retitles every attached
channel) and `/end`; channels attach to sessions, several at once, one conversation; the operator
defined per-session; every closed session enters the queue; the root-write tool the session file
needs (`RT-15` confinement has no route today — this is the known hole, closed here).

**Salvage bin:** `service/threads.py` and the daemon's run-ownership model (a dropped connection
detaches, never cancels), the SQLite store shape, `contracts.py` patterns. **Dies:** the
channel-is-identity model, `/pending`-style parked approvals, every gate.

**Proves itself by:** a session opened, named, worked, closed, and its file sealed with `/end`,
driven through the API alone.

## Required research for Phases 3 and 4 (the operator's directive, 2026-08-13)

Before either phase plan is written, a deep web-research pass goes into the implementations, not
the readmes, of five repositories the operator named, plus the wider literature on brainstorming
and planning agents:

- `https://github.com/obra/superpowers` — draw heavily on the skill designs.
- `https://github.com/gsd-build/get-shit-done` and `https://github.com/gsd-build/gsd-2` — GSD 1
  and 2: questioning, workflows, agent prompts.
- `https://github.com/nousresearch/hermes-agent` — research and build upon the self-learning
  design; the old §2.8 guards (RS-141/RS-142) came from here and the new Learning agent should be
  grounded in what the implementation actually does.
- `https://github.com/openclaw/openclaw` — agent interactions and orchestration.

Read the agent implementations themselves: prompts, loop code, stop conditions, hand-offs. Findings
land in a research brief beside the phase plan, with strength labels, and anything adopted must
still beat the measurements this design already carries (code fan-out, attributed merge, no debate
rounds).

## Phase 3: The Agents and the Skills (DESIGN §3–§4)

**Builds:** the three agents with the Librarian holding no topic and the Learning agent holding
none either; per-session operator wiring; subtree write confinement re-derived from the new rules;
the thirteen shipped skills with the five new ones written (`brainstorming`, `planning`,
`briefing`, `web-search`, `answering-a-brief`) and `research`/`discovery` retired; skill
overloading and the by-name resolution; the skills catalog feeding Phase 1's generators;
`AUTHORSHIP.md` written and read by harness code.

**Salvage bin:** `agents/runtime.py` harness wiring, `agents/permissions.py` shape,
`agents/ingestion.py` (the bounded reader survives the redesign nearly whole), the model failover
in `agents/models.py`. **Dies:** the Librarian's `route`-only surface, the old skill texts that
describe tagging conflicts.

## Phase 4: Workflows, Conflict Handling and the Loop (DESIGN §5–§7)

**Builds:** the Librarian's turn (classify against the registry, code fan-out, brief per expert,
merge by attribution, the operator as round boundary); the deep phase drafting instruction sets and
experts checking them; brainstorming/planning as one pair at two scopes with the three bindings;
write-time conflict checking by a read-only sub-agent on all four axes; the Learning agent's
analysis cycle, the filing bar, the learning queue with no cap and no expiry; the learning channel.

**Measurements carried as constraints:** the fan-out is code and the merge is attribution (both
priced in the old repo); no debate rounds; notes never travel with search questions; search
budgets on sub-agents, none on the operator's rounds.

## Phase 5: Handing Work Out and Transports (DESIGN §8, §2.5)

**Builds:** context packs re-derived from the new tree (no conflict-tag role, synthesis-only from
session files); the tool-fetch return path (first mechanism decision — the design left it at the
spec's altitude deliberately); the API as the one way in with the TUI and Telegram as clients.

**Salvage bin (largest):** `server/` HTTP+SSE, `tui/`, `server/telegram*.py` — the topic-hazard
handling was bought with live defects and the channel-attach model from Phase 2 is the only deep
change they need.

---

## Verification, roadmap-level

- Each phase ends with `make check` green, its rules file written, and a live smoke run of the
  phase's proves-itself scenario recorded in the phase plan.
- After Phase 5: a fresh-clone bootstrap following DESIGN §9, then the spec's own worked loop —
  ingest a source, work a session, `/close`, analysis, `/end` — end to end on a phone.
- `docs/how-to/` is rewritten after Phase 5 from a clean clone, commands executed not recalled,
  as the old guides were.

## Execution

Phase plans are written one at a time, at phase start, against the design and the then-current
tree — not all up front, because each phase's plan depends on what the previous phase actually
built. Phase 0 executes directly from this document.
