# Phase 2: Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the session layer against `DESIGN.md` §2, so a session is a durable named thing
with one file for its whole life, reached through channels that attach to it, closed by `/close`
into the learning queue and sealed by `/end` — driven through the API alone.

**Architecture:** Re-implementation in place. The salvage bin is the service's durable store shape
(`threads.py`'s SQLite discipline, migrations table, the run supervisor in `runs.py`, the hub
fan-out) and the daemon's run-ownership rule (a dropped connection detaches, never cancels). What
dies: the channel-is-identity model, the parked-proposal machinery (`proposals.py`, its routes),
and every gate — the operator's instruction is the approval, so nothing interrupts a write to park
it. Agent prompts, skills and the Learning agent are Phases 3–4; Telegram and TUI get minimal
truthful repointing here and their polish in Phase 5. The session file is written by harness code
in the service layer through `pkb.core`'s serializer — no model ever holds a tool that writes
`sessions/**`.

**Tech Stack:** unchanged (Python 3.11+, SQLite, FastAPI/SSE, deepagents runtime as it stands).

## Global Constraints

- `DESIGN.md` §2 is the contract; where silent, the plan proposes and flags. `make check` green
  after every task (baseline 2119 passed / 62 deselected; counts move with markers and new tests).
- The session file is a knowledge file under Phase 1's T-rules: seven required fields,
  `topic: "(session)"`, `type.summary`, one `topic.*` tag per participating expert,
  `FileRole.SESSION`, location-agreement checks skipped. Nothing here re-litigates Layer 1.
- Nothing moves or deletes operator content; the one file-move that exists is `/name`'s rename,
  which is the session's own file, performed by harness code, with nothing lost.
- Commit per task with the task's message plus the standard trailer
  (`Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` /
  `Claude-Session: https://claude.ai/code/session_013RAwz2RWewccxyv7ZqeRz3`). Never push mid-phase.

## Proposals where the design is silent (flag with the task, do not block)

**P3 — the on-disk seal.** `/end` seals the file and a sealed file is never reopened. Proposal: the
seal is an appended `## Ended` marker line written by harness code (the design says marks are
appended entries, the body is append-only, and frontmatter carries no state field), plus the store's
`state='ended'`; the writer module refuses every write to a sealed file by checking the store, not
the file. **P4 — the queue is a view, not a table.** "Every closed session enters the learning
queue... no cap and no expiry" is implemented as the set of sessions with `state='closed'` ordered
by `closed_at` — the queue IS the closed-not-ended sessions, no second structure to drift.

## File Structure

| File | Fate |
|---|---|
| `docs/superpowers/specs/2026-08-14-sessions-S-rules.md` | Created: the `S-*` rules (Task 1) |
| `src/pkb/service/sessions.py` | Created from `threads.py`'s shape: state machine, operator, objective (Task 3) |
| `src/pkb/service/threads.py` | Deleted at Task 10 (its store shape lives on in `sessions.py`) |
| `src/pkb/service/session_file.py` | Created: the harness-side file writer (Task 4) |
| `src/pkb/service/proposals.py` | Deleted (Task 6) |
| `src/pkb/service/runtime.py`, `runs.py` | Modified: session wiring, no interrupt path (Tasks 3–6) |
| `src/pkb/server/routes.py` | Modified: `/sessions` routes, commands, no `/proposals`, no `/interrupt` (Task 5) |
| `src/pkb/server/telegram.py`, `src/pkb/service/telegram.py` | Modified: bindings repoint channel→session, minimal (Task 7) |
| `src/pkb/agents/...` (gate wiring only) | Modified: interrupts off, truthful compile-keepers (Task 6) |
| `tests/service/test_sessions.py`, `tests/service/test_session_file.py`, `tests/server/test_session_routes.py` | Created |
| `tests/service/test_design_session_life.py` | Created: the proves-itself test (Task 9) |

---

### Task 1: Mint the `S-*` rules

**Files:** Create `docs/superpowers/specs/2026-08-14-sessions-S-rules.md`.

- [ ] **Step 1:** Read `DESIGN.md` §2 whole (all ten subsections) and mint `S-*` rows in the
  Phase 1 rules-file format (id, rule quoting §2 where exact, severity, the check that asserts it).
  Cover at minimum: the session as the durable named thing, its name held by itself not a channel;
  the operator defined per-session (whoever establishes it and sets its goal, human or agent
  alike); opening on the Librarian, a Topic Expert or the Learning agent; one way in (the API) with
  the TUI and Telegram as clients; channels attach, several may hold one session, one conversation
  whatever the count, turns strictly ordered; the seven commands with `/name`/`/close`/`/end`
  acting on the session itself; `/name` renames the file and retitles every attached channel,
  refused after the seal; `/close` = nothing more to craft, brings channels away, enters the queue,
  judges nothing; `/end` said by the operator, seals the file, analysis included; the one file per
  session and its six-stage life with the section inventory (objective and experts, running record,
  synthesis holding instruction sets, distillation); the record lands unapproved because it says
  what happened, the synthesis waits on the operator word for word; no gates, no parked proposals,
  no pending queue anywhere; every write reaches the tree through a session; P3 and P4 as proposal
  rows marked for the operator. Boundary rows (assert-nothing markers): the analysis/distillation
  content is Phase 4's `L-*`; Telegram surface polish is Phase 5.
- [ ] **Step 2:** Self-check mapping §2.1–§2.10 → rule ids at the file's foot; no subsection empty.
- [ ] **Step 3:** `make check` (must stay green). Commit: `spec: mint the S-rules for sessions`.

### Task 2: Deselect the superseded session-model tests

**Files:** test files only, plus nothing else.

- [ ] **Step 1:** Mark `@pytest.mark.superseded` (narrowest level) tests whose ASSERTIONS depend on
  retired session design, found from these starting greps plus judgment:
  `grep -rln 'pkb_proposals\|/proposals\|pending_interrupt\|interrupt' tests/service tests/server tests/tui tests/agents`
  `grep -rln '"/new"\|origin_channel' tests/` (channel-identity era)
  Judgment rule as Phase 1 Task 2: fixture-uses-it is not enough; assertion-depends-on-it marks.
  The TUI approval modal tests and client approval-helper tests assert parked approvals — mark
  with a Phase-5 comment. Gate/interrupt assertions in tests/agents mark with a Phase-3 comment.
- [ ] **Step 2:** `make check` green; record counts in the commit body.
  Commit: `test: deselect the superseded session-model assertions`.

### Task 3: The session store

**Files:** Create `src/pkb/service/sessions.py` (from `threads.py`'s shape); modify
`src/pkb/service/runtime.py`, `src/pkb/service/__init__.py`. Tests: `tests/service/test_sessions.py`.

**Interfaces:**
- Produces: `SessionStore` with `create(agent_id, objective, operator, *, name=None) -> Session`;
  `get/list`; `rename(session_id, name, *, now)`; `close(session_id, *, now)` (idempotent error on
  re-close); `end(session_id, *, now)` (only from `closed`); `queue() -> list[Session]` (closed,
  by `closed_at`, P4); `Session` dataclass with `session_id, agent_id, objective, name, operator,
  state ('open'|'closed'|'ended'), created_at, updated_at, closed_at, ended_at, file_path`.
  The store enforces the state machine; illegal transitions raise the service's error type.
- Consumes: `threads.py`'s SQLite/migration discipline (copy the shape, new table `sessions`).

- [ ] **Step 1:** Failing tests: creation records operator and objective; the state machine
  (open→closed→ended, re-close errors, end-from-open errors); `queue()` returns exactly the closed
  set ordered by `closed_at`; rename updates `name`+`updated_at`; unnamed sessions get a
  deterministic name from the objective (slug, the way thread ids are minted today — read
  `threads.py` and reuse its slug/id discipline).
- [ ] **Step 2:** FAIL → implement → PASS → `make check`.
- [ ] **Step 3:** Commit: `feat: the session store — durable, named, one state machine`.

### Task 4: The session file writer

**Files:** Create `src/pkb/service/session_file.py`. Tests: `tests/service/test_session_file.py`.

**Interfaces:**
- Produces: `SessionFileWriter(kb_root)` with `create(session) -> str` (KB-relative path
  `sessions/<name>.md`: frontmatter per the Global Constraints, body opening with the objective and
  an `## Experts` line); `append_record(session, entry_md)` (append-only, under `## Record`);
  `add_expert_tag(session, topic_tag)` (frontmatter gains the tag when a new expert joins);
  `mark_closed(session)` / `mark_ended(session)` (appended marker entries per P3; `mark_ended`
  makes the writer refuse all later writes for that session); `rename(session, old_path)` (moves
  the file, rewrites `title`, loses nothing); `write_synthesis(session, md)` (replaces only the
  `## Synthesis` section — the one non-append write, operator-approved by contract, Phase 4 wires
  its caller). Every write validates through `pkb.core` (scan+validate the file) and REFUSES on
  error findings rather than landing an invalid file.
- Consumes: `pkb.core.frontmatter.serialize`, Phase 1's SESSION classification.

- [ ] **Step 1:** Failing tests: created file classifies `FileRole.SESSION` and validates clean;
  record appends preserve every existing byte before the append point; rename moves and retitles
  with content preserved; writes after `mark_ended` raise; a write that would produce an error
  finding is refused and the file is untouched (write to a temp+swap or validate-before-write).
- [ ] **Step 2:** FAIL → implement → PASS → `make check`.
- [ ] **Step 3:** Commit: `feat: the session file — one file, whole life, written by harness code`.

### Task 5: Routes and commands

**Files:** Modify `src/pkb/server/routes.py`, `src/pkb/service/runtime.py`. Tests:
`tests/server/test_session_routes.py`.

**Interfaces:**
- Produces: `POST /agents/{id}/sessions` (creates store row + file; body: objective, optional name,
  operator from the caller's declared identity field, default "operator");
  `GET /sessions` (`?state=` filter; `state=closed` is the queue), `GET /sessions/{id}`,
  `POST /sessions/{id}/name` (store rename + file rename + channel retitle fan-out stub for Task 7),
  `POST /sessions/{id}/close`, `POST /sessions/{id}/end`, `POST /sessions/{id}/runs` and
  `GET /sessions/{id}/events` (the old thread run/SSE machinery re-homed). The old `/threads*`
  routes DELETED, `/threads/{id}/interrupt` DELETED, `/proposals*` DELETED (Task 6 removes their
  backing). `DELETE /sessions/{id}` does NOT exist — nothing deletes a session.
- Consumes: Tasks 3–4.

- [ ] **Step 1:** Failing route tests via the existing FastAPI test-client patterns in
  tests/server; every route above, plus: a run on a closed session is refused; `/end` on an open
  session is refused; the SSE events stream carries the run events for whichever channel asks.
- [ ] **Step 2:** FAIL → implement → PASS → `make check` (TUI/clients compile-keepers: point
  `pkb/clients` + `pkb/tui` at `/sessions` minimally — same JSON shapes where possible; mark any
  client test that asserts retired routes superseded with a Phase-5 comment).
- [ ] **Step 3:** Commit: `feat: session routes — the API is the one way in`.

### Task 6: The gates die

**Files:** Delete `src/pkb/service/proposals.py`; modify `src/pkb/service/runtime.py`,
`src/pkb/server/routes.py` (already stripped in Task 5 — verify), the agents-side gate wiring
(find it: `grep -rn 'interrupt_on\|human_in_the_loop\|HumanInTheLoop' src/pkb/agents/` — the
runtime/registry composes it), `src/pkb/server/mcp.py` and `src/pkb/service/telegram.py` where
they surface approvals.

- [ ] **Step 1:** Failing tests: a note-write tool call inside a run LANDS during the turn with no
  interrupt raised (use the existing fake-model run harness from tests/agents — find the pattern
  that drives a scripted tool call); the runtime exposes no interrupt-resume surface; importing
  `pkb.service.proposals` fails.
- [ ] **Step 2:** Implement: interrupts off at the composition point (delete the gate config, not
  the deepagents library shim), delete proposals.py + store init + exports, delete approval
  surfacing in mcp.py/telegram.py with truthful minimal replacements (a Telegram approval prompt
  becomes nothing — sends that referenced it are Phase 5's polish; keep the module compiling and
  its live non-approval tests green). Mark newly-dying tests superseded (Phase-3/Phase-5 comments
  per subject). `make check`.
- [ ] **Step 3:** Commit: `feat: the operator's instruction is the approval — gates and proposals removed`.

### Task 7: Channels attach to sessions

**Files:** Modify `src/pkb/service/telegram.py` (bindings channel→session), `src/pkb/server/telegram.py`
(minimal command/flow repoint), `src/pkb/service/sessions.py` (attached-channels registry:
`attach(session_id, channel_ref)`, `detach`, `channels(session_id)`), `src/pkb/server/routes.py`
(retitle fan-out on rename becomes real). Tests: extend `tests/service/test_sessions.py` +
`tests/server/test_session_routes.py`.

- [ ] **Step 1:** Failing tests: two channels attach to one session and both appear in
  `channels()`; rename retitles every attached channel (assert via the store + a recorded telegram
  send in the existing fake-transport pattern from tests/server/test_telegram*); `/close` detaches
  every channel; a Telegram binding row maps a chat/topic to a SESSION id.
- [ ] **Step 2:** FAIL → implement minimally (deep Telegram UX — pickers, topic creation flows —
  stays as-is where it can, marked superseded where its assertions die; Phase 5 rebuilds polish).
- [ ] **Step 3:** `make check`. Commit: `feat: channels attach to sessions, several at once`.

### Task 8: The turn writes the record

**Files:** Modify `src/pkb/service/runs.py` / `runtime.py`: after each completed run, harness code
appends a record entry to the session file (turn summary: who asked, what the agent replied,
compressed — the entry is the run's final text plus the operator's message, verbatim, no model
re-summarization) and touches `add_expert_tag` when the run's agent maps to a topic. Tests: extend
`tests/service/test_session_file.py` with the run-completion hook via the fake-run pattern.

- [ ] **Step 1:** Failing test: a completed run leaves the exchange in the file under `## Record`;
  two runs append in order; a run on the Librarian adds no topic tag, a run on a Topic Expert adds
  its `topic.*` tag once.
- [ ] **Step 2:** FAIL → implement → PASS → `make check`.
- [ ] **Step 3:** Commit: `feat: the running record lands as the work happens`.

### Task 9: The proves-itself test

**Files:** Create `tests/service/test_design_session_life.py` (written to pass).

- [ ] **Step 1:** One end-to-end test through the API test client and a scripted fake model:
  create a session on a Topic Expert with an objective → the file exists, classifies SESSION,
  validates clean → `/name` renames (file moved, title rewritten, attached channel retitled) →
  one worked turn → the record holds it and the expert's tag is in frontmatter → `/close` → the
  session is in `GET /sessions?state=closed` (the queue) and channels are detached → `/end` →
  sealed marker present, a further run and a further write both refused → the file's section order
  matches DESIGN §2.7's life. Then tick this plan's Task 1–9 checkboxes.
- [ ] **Step 2:** `make check`. Commit: `test: a session lives its whole life through the API`.

### Task 10: Delete the superseded session tests and dead modules

**Files:** Delete `src/pkb/service/threads.py` (verify zero imports remain), superseded-marked
tests under tests/service and tests/server whose subjects this phase rebuilt (tests/tui and
tests/agents markers STAY for Phases 3–5); dead exports in `src/pkb/service/__init__.py`,
`src/pkb/contracts.py`.

- [ ] **Step 1:** Delete; clean stranded imports/fixtures; verification greps:
  `grep -rn 'threads\.py\|ThreadStore\|pkb_proposals\|pending_interrupt' src/` → only Phase-3/5
  survivors, each documented with file:line in the commit body.
- [ ] **Step 2:** `make check`; tick Task 10's checkboxes; run the plan's Self-Review (coverage of
  every S-rule → citing test; placeholder scan; type consistency) and record it in the commit body.
- [ ] **Step 3:** Commit: `chore: remove the superseded session machinery`.

---

## Verification

- `make check` green at every commit; the proves-itself test is the living end-to-end proof.
- The S-rule → test mapping recorded at Task 10 (the Self-Review, executed — Phase 1's final
  review caught this step being skipped; do not repeat that).
- Final: the widened dead-machinery grep, and a manual smoke: run the daemon locally against a
  scratch KB, create/name/close/end a session via curl, open the file.
