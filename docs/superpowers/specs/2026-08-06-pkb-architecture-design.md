# PKB System Architecture

**Date**: 2026-08-06
**Status**: Approved
**Scope**: System-wide architecture — the seams between layers. Each layer gets its own spec and
implementation plan afterward.

---

## 1. Purpose

`README.md` specifies *what* the Personal Knowledge Base is. This document specifies *how it is
built*: the process model, the layer boundaries, and the contracts that cross them.

It exists because the access requirements (Textual TUI, direct connection to any agent, Telegram,
programmatic agent access) constrain the agent layer. Settling those seams once prevents designing
the agent layer twice.

This document does **not** detail the internals of any layer. It fixes the interfaces so the three
layers can be specified and built independently.

---

## 2. Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Harness is [`langchain-ai/deepagents`](https://github.com/langchain-ai/deepagents) | Native support for subagents, skills with progressive disclosure and override resolution, pluggable filesystem backends, declarative filesystem permissions, and HITL interrupts — most of Part 2 of the README maps to existing features. |
| D2 | Daemon + thin clients, not embedded library | Required by D3. Also gives Telegram uptime independent of the terminal, and one writer to the KB. |
| D3 | Threads are shared across channels | Start a conversation in the TUI, continue it from Telegram. Requires a durable shared checkpointer and thread discovery in every client. |
| D4 | Custom daemon on the MIT LangGraph core — **not** LangGraph Platform | `langgraph-api` (the runtime behind `langgraph dev`) is Elastic License 2.0, requires `LANGGRAPH_CLOUD_LICENSE_KEY`, Postgres, and Redis. `langgraph`, `langgraph-checkpoint`, and `langgraph-checkpoint-sqlite` are MIT and provide everything needed: compiled graphs, durable threads, streaming, and `Command(resume=...)`. |
| D5 | HTTP + SSE for human channels; MCP for programmatic agents | Two different audiences. ACP is a client↔agent protocol (editors, streaming, permission prompts); MCP is an agent↔capability protocol. External Project Manager agents want the PKB as a capability. |
| D6 | KB tree is a git repository; the daemon commits | Free undo, an audit trail separating what agents wrote from what the human approved, and a backup path. |
| D7 | Skills use `skills/<name>/SKILL.md`, amending README §2.4 | deepagents' native format. Buys progressive disclosure and name-collision override resolution with no custom code. |
| D8 | Each topic is its own compiled graph, registered with the Librarian as a `CompiledSubAgent` | Dict-subagents are one-shot; README §1.6 requires multi-turn approval dialog. One artifact serves both direct connection and Librarian routing. |

### Rejected alternatives

- **Embedded library per channel** — cannot satisfy D3, and three processes writing one tree breaks
  Layer 1's determinism guarantee.
- **LangGraph Platform as the daemon** — see D4. A license key plus two database services is the
  wrong trade for a personal knowledge base.
- **ACP as the human-facing protocol** — ACP is stdio, one session per spawned process. Bending it
  into a shared daemon with cross-channel resume fights its design, and Telegram over ACP is
  awkward. Viable later as an *additional* adapter (see §10).
- **MCP for everything, including the TUI** — MCP has no stateful conversation thread, no assistant
  token streaming, and maps interrupt/approve/edit/reject badly onto tool calls.

---

## 3. Layers and seams

```
┌──────────────────────────────────────────────────────────────┐
│  CLIENTS   Textual TUI  │  Telegram bot  │  external agents   │
└──────────────────────────────────────────────────────────────┘
              │ REST + SSE                    │ MCP (streamable HTTP)
┌──────────────────────────────────────────────────────────────┐
│  LAYER 3 — TRANSPORT            pkb.server                   │
│  FastAPI: /agents /threads /runs /interrupt  + mounted MCP   │
└──────────────────────────────────────────────────────────────┘
              │  PkbService  (in-process Python interface)
┌──────────────────────────────────────────────────────────────┐
│  LAYER 2 — AGENTS               pkb.agents                   │
│  AgentRegistry · Librarian graph · one expert graph / topic  │
│  deepagents: skills, subagents, HITL, filesystem backend     │
└──────────────────────────────────────────────────────────────┘
              │  validators + generators (direct calls)
┌──────────────────────────────────────────────────────────────┐
│  LAYER 1 — KB CORE              pkb.core                     │
│  frontmatter schema · validator · index/tags generators ·    │
│  topic scaffolder · git commit policy                        │
└──────────────────────────────────────────────────────────────┘
                              │
                    KnowledgeBase/  (git repo on disk)
```

Three invariants define the seams. Each is mechanically checkable and each should be enforced by a
lint rule or test.

**I1 — `pkb.core` imports nothing from `pkb.agents` or `pkb.server`.**
Layer 1 is plain Python over a directory tree. Its whole test suite runs against `tmp_path` with no
API key and no network. This is what makes README §1.9's "enforce structure mechanically" real: if
Layer 1 needed an agent to run, it could be skipped.

**I2 — Transports never import `deepagents`.**
Layer 3 may touch only `PkbService`. This keeps the TUI, Telegram, and MCP adapters thin and makes a
fourth adapter additive rather than invasive.

**I3 — Only Layer 1 writes derived files.**
Every `index.md` and the root `tags.md` are produced by generators. Agents are blocked at the harness
level:

```python
permissions=[
    FilesystemPermission(
        operations=["write"],
        paths=["/kb/**/index.md", "/kb/index.md", "/kb/tags.md"],
        mode="deny",
    ),
]
```

(Paths are backend-relative; the KB mounts at `/kb/` — see §4.)

Not by prompt instruction. The `llm-wiki` example in the deepagents repo asks its agent not to edit
derived files; we enforce it.

### Package layout

```
pkb/
├── core/            # Layer 1 — no LLM, no agent imports
│   ├── frontmatter.py      # schema + parse/serialize
│   ├── validation.py       # required fields, tag syntax/depth, naming, location consistency
│   ├── generators/         # topic index.md, root index.md, root tags.md
│   ├── scaffold.py         # new topic / sub-topic structure
│   └── repo.py             # git commit policy
├── agents/          # Layer 2
│   ├── registry.py         # AgentRegistry: catalog, lazy graph construction, invalidation
│   ├── librarian.py        # root routing agent
│   ├── expert.py           # Topic Expert factory (template + expert.md override)
│   ├── middleware/         # KbValidationMiddleware, KbMaintenanceMiddleware
│   └── runtime.py          # shared checkpointer, store, backend
├── service.py       # PkbService protocol + implementation
├── server/          # Layer 3
│   ├── app.py              # FastAPI
│   ├── routes.py
│   └── mcp.py              # mounted MCP server
├── tui/             # Textual client
└── channels/
    └── telegram.py
```

---

## 4. Agent topology and addressing

Every topic gets its **own compiled graph** — `create_deep_agent(...)` with its own system prompt,
skills chain, and `memory=[topic.md, notes/summary.md]`. That single artifact serves both access
paths:

- **Direct connection** — addressable on its own, with its own threads and checkpoints.
- **Librarian routing** — registered with the Librarian as
  `CompiledSubAgent(name=..., description=..., runnable=expert_graph)`, so deepagents' `task()` tool
  routes to it.

**Addressing.** Agent IDs mirror the tree: `librarian`, `topic/cooking`, `topic/cooking/grilling`.
Sub-topics resolve to the nearest ancestor holding an `expert.md`, falling back to the topic root —
README §1.8 rule 5, implemented as path resolution rather than a special case.

**Registry.** `AgentRegistry` scans `*/topic.md` at startup to build the catalog, but constructs
graphs **lazily on first use** and caches them. Fifty topics must not mean fifty graphs at boot.
Topic creation invalidates the registry; the Librarian's subagent list is rebuilt on next access.

**Shared runtime.** One `AsyncSqliteSaver` checkpointer, one store, and one backend:

```python
CompositeBackend(
    default=StateBackend(),                                    # agent scratch, thread-scoped
    routes={"/kb/": FilesystemBackend(root_dir=KB, virtual_mode=True)},
)
```

A thread is `(agent_id, thread_id)`. Cross-channel resume is the same `thread_id` reached from a
different client.

**Consequence.** A conversation held *directly* with the Cooking expert and work the Librarian
*delegates* to it are different threads. Delegated work runs in the subagent's isolated context and
returns a summary. "Continue what the Librarian was doing" resumes the Librarian's thread, not the
expert's. This is correct behaviour, and the TUI must not imply otherwise.

**Model.** Configurable per agent; default `anthropic:claude-sonnet-5`. The model is a registry
concern, not a transport concern.

---

## 5. `PkbService`

```python
class PkbService(Protocol):
    async def list_agents(self) -> list[AgentInfo]: ...
    async def list_threads(self, agent_id: str | None = None) -> list[Thread]: ...
    async def create_thread(self, agent_id: str) -> Thread: ...
    async def get_thread(self, thread_id: str) -> ThreadDetail: ...
    def stream_run(self, thread_id: str, message: str) -> AsyncIterator[Event]: ...
    def resume(self, thread_id: str, decisions: list[Decision]) -> AsyncIterator[Event]: ...
    async def cancel(self, run_id: str) -> None: ...
```

### The `Event` union

| Event | Payload | Consumed by |
|-------|---------|-------------|
| `message.delta` | token text | TUI |
| `message.complete` | full message | TUI, Telegram, MCP |
| `tool.start` | tool name, argument summary | TUI |
| `tool.end` | result summary, error flag | TUI |
| `subagent.start` | subagent name | TUI |
| `subagent.end` | subagent name, status | TUI |
| `interrupt` | action requests, allowed decisions, optional diff | TUI, Telegram |
| `run.end` | run id, final message | all |
| `run.error` | error message, retryable flag | all |

Events are **normalized**, not proxied from LangGraph. The clients need different slices: the TUI
wants token deltas, Telegram cannot use them (editing a message per token hits rate limits, so it
renders on `message.complete`), and MCP wants only the final result. Proxying raw
`astream_events(version="v3")` payloads would require every adapter to understand deepagents
internals, making invariant I2 fiction.

`subagent.start`/`subagent.end` let the TUI show *"→ routing to Cooking expert"*, making README
§2.2's routing behaviour visible instead of a silent pause.

### Thread metadata

The LangGraph checkpointer persists graph state per thread but does not index threads by agent or
carry a title. The daemon keeps its own table in the same SQLite file:

```
threads(thread_id, agent_id, title, created_at, updated_at, origin_channel,
        pending_interrupt_id)   -- nullable; set while an approval awaits a decision
```

Without it, "list my threads with the Cooking expert" has no answer and cross-channel resume has no
discovery surface.

---

## 6. Transport adapters

### HTTP API

```
GET    /agents                        catalog: id, title, description
GET    /threads?agent_id=             resume discovery, includes pending_interrupt
POST   /agents/{agent_id}/threads     new thread
GET    /threads/{id}                  replay history
POST   /threads/{id}/runs             → SSE stream of Event
POST   /threads/{id}/interrupt        {decisions: [...]} → SSE resumes
DELETE /threads/{id}
GET    /health
```

### Textual TUI

Sidebar holds the agent picker (Librarian pinned above the topic tree) and the thread list for the
selected agent; the main pane is the conversation.

The **approval modal** on `interrupt` is the piece that matters most. What the human approves in a
knowledge base is usually *content* — a drafted `notes/summary.md`, a proposed tag, a conflict
resolution — so the modal renders a **diff** of the proposed write, with approve / edit / reject.
README §1.6 calls the breadth files "a compact approval surface"; this is where that becomes a UI.

Transport is `httpx` plus an SSE client. The TUI imports `pkb.service` types only, never
`pkb.agents`.

### Telegram

One bot. Default target is the Librarian; `/agents` lists, `/connect cooking` switches — direct
expert access without a bot per topic. Approvals arrive as inline keyboard buttons.

`edit` is impractical on a phone, so the Telegram adapter narrows `allowed_decisions` to
approve/reject and directs the human to the TUI for anything needing an edit.

### MCP

Mounted at `/mcp` on the same FastAPI app.

| Tool | Purpose |
|------|---------|
| `pkb_ask(agent_id, question)` | Query the Librarian or a named expert |
| `pkb_ingest(content, source_type, topic_hint)` | Submit information for filing |
| `pkb_research_pack(query)` | Breadth-first context pack (README Part 4) |
| `pkb_implementation_pack(topic)` | Depth-first context pack (README Part 4) |

Two behaviours are requirements from the README, not enhancements:

- **Conflict escalation.** Part 4 requires any agent encountering a `status.conflict-review` file to
  pause and escalate. An MCP tool touching one returns an escalation result, not an answer.
- **The MCP write path is propose-only.** An external agent cannot satisfy a human approval gate.
  Rather than interrupting and hanging, writes needing approval are recorded as **pending
  proposals** and the tool returns "proposed, awaiting human review". The human sees them in the
  TUI. This keeps "human content wins" true when the caller is a robot.

---

## 7. Layer 1 enforcement, git, and the scan queue

Two middleware, split because validation and regeneration want opposite timing.

### `KbValidationMiddleware.wrap_tool_call`

Intercepts every `write_file` / `edit_file` routing to the KB. Parses frontmatter and checks:

- required fields present (`title`, `description`, `topic`, `tags`, `created`, `updated`,
  `source_type`)
- tag syntax and depth ≤ 4 levels
- file naming conventions, including the folder-hosted `[item]/[item].md` rule
- consistency between declared `topic` / `source_type` / `topic.*` / `type.*` tags and actual
  location

Derived files (`index.md` at any level, root `tags.md`) carry only minimal generated frontmatter per
README §1.4 and are exempt from the required-field check. They are also unwritable by agents (I3),
so in practice the validator never sees them.

On failure it returns an error `ToolMessage` **without invoking the handler**. The write never lands;
the agent sees exactly what was wrong and self-corrects in-loop. This is unskippable because it sits
below the agent's decision-making rather than in its prompt.

### `KbMaintenanceMiddleware.after_agent`

Flushes once per turn:

1. Regenerate affected topic `index.md` files, including tag subtrees and cross-topic mappings.
2. Regenerate root `tags.md` from tags actually used, aggregating `related_topics`.
3. Regenerate the root catalog `index.md`.
4. Update `updated` timestamps.
5. Flag broken links and orphaned files.
6. Enqueue conflict scans covering changed files.
7. Commit.

`wrap_tool_call` records touched paths in middleware state; `after_agent` consumes and clears them.
Per-write regeneration would rewrite `tags.md` several times in one turn for no benefit.

### Git commit policy

The commit happens in `after_agent` *after* regeneration, so every commit is a consistent tree with
content and derived files together. One commit per run that changed files:

```
pkb(cooking): add note "Grill performance in windy conditions"

Agent: topic/cooking
Thread: 01J8X...
```

Human approvals happen *inside* the run via interrupt→resume, so an approved change and its commit
are one unit. On run failure the daemon still regenerates and commits rather than rolling back — a
tree with stale derived files is worse than a recorded bad state, and `git revert` is a better undo
than anything hand-rolled.

### Conflict scan queue

README §1.9 has Layer 1 *schedule* scans and Layer 2 *execute* them. `after_agent` writes scan
requests to a queue table in the same SQLite file. A background task in the daemon dequeues them and
runs the relevant topic expert on its own thread with a conflict-scan prompt. Findings tag files
`status.conflict-review` with a `review_note`, surfacing as pending items in the TUI.

---

## 8. Concurrency and failure

**Concurrency.** Runs stream concurrently, but the `after_agent` flush takes a single global KB write
lock, because regeneration touches root `tags.md` and the root catalog. Two concurrent runs on the
same thread return `409`.

**Failure modes.**

| Failure | Behaviour |
|---------|-----------|
| Frontmatter validation fails | Error `ToolMessage`, agent retries. Bounded to 3 attempts per file per run, then escalates to the human. |
| Model or provider error | `run.error` event; thread remains resumable. |
| Client disconnects mid-approval | Interrupt persists in the checkpoint, appears as pending on `list_threads`, and **any** client can resolve it later. |
| Git commit fails (e.g. external edit conflict) | Run completes, commit logged as failed, flagged in `/health`. The tree is never corrupted. |

The abandoned-approval case is a direct benefit of the daemon model: approve from a phone something
the TUI asked about hours earlier. The thread list should be designed around it.

---

## 9. Testing strategy

| Layer | Harness | Requires API key |
|-------|---------|------------------|
| `pkb.core` | pytest + `tmp_path`; golden files for generators; property tests for tag rules | no |
| `pkb.agents` | middleware against a fake chat model; assert invalid writes never reach the backend | no |
| `pkb.server` | FastAPI `TestClient` against a stub `PkbService` | no |
| `pkb.tui` | Textual `run_test()` pilot against the same stub | no |
| end-to-end | live smoke tests behind an opt-in marker | yes |

Because `PkbService` is a Protocol, everything above Layer 2 tests against a stub. Nearly the entire
suite runs free and fast; live tests are a thin top layer.

Invariants I1 and I2 are enforced by an import-linter rule in CI, not by convention.

---

## 10. Out of scope

Deferred deliberately, not overlooked:

- **ACP adapter.** Would expose the PKB to Zed and other ACP editors. Additive once `PkbService`
  exists — a fourth adapter, no core changes.
- **Multi-user.** The PKB is personal. `StoreBackend` namespacing and auth are unnecessary; the
  daemon binds to localhost.
- **Remote deployment topology.** The daemon is host-agnostic. Where it runs, and how the TUI reaches
  it when it is not local, is a deployment question for when Telegram lands.
- **Sandboxing.** `FilesystemBackend(virtual_mode=True)` confines agents to the KB root. No shell
  access is granted, so `LocalShellBackend` and sandbox backends are not used.

---

## 11. Build order

Bottom-up, one spec and plan per layer:

1. **`pkb.core`** — schema, validator, generators, scaffolder, git policy. No LLM, no agents, fully
   TDD-able. Every guarantee above rests on it.
2. **`pkb.agents`** — runtime, registry, expert factory, Librarian, the two middleware, default
   skills.
3. **`pkb.service` + `pkb.server`** — the protocol, its implementation, HTTP routes, SSE, MCP mount.
4. **`pkb.tui`** — Textual client, including the approval diff modal.
5. **`pkb.channels.telegram`** — bot adapter.

---

## 12. README amendments required

1. **§2.4 and §1.3** — skills become `skills/<skill-name>/SKILL.md` rather than
   `skills/<skill>.md`, adopting the deepagents Agent Skills format. Applies to both the PKB root
   `skills/` folder and topic-level overload folders. Override resolution (same skill name, later
   source wins) is then provided by the harness.
2. **Part 3** — the KB tree diagram updates to reflect the same change.
