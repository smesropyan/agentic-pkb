# PKB System Architecture

**Date**: 2026-08-06
**Status**: Approved. **Amended 2026-08-07** — see the amendment log below.
**Scope**: System-wide architecture — the seams between layers. Each layer gets its own spec and
implementation plan afterward.

### Amendment log

| Date | What changed | Why |
|------|--------------|-----|
| 2026-08-07 | §4, §5, §7, §8 corrected in five places where this document described a harness that does not exist as described. | Layers 1 and 2 were built, and a grounding pass **executed** every harness assumption against the pinned `deepagents 0.7.5` / `langchain 1.3.14` / `langgraph 1.2.10`. Five assumptions were wrong. Each correction below names the Layer 2 rules row (`D-1`, `D-6`, `D-11`, `D-12`, `D-15`) holding the executed evidence, so a later reader does not "fix" the text back. |
| 2026-08-07 | New decisions **D10** (Librarian routing is a harness-encoded workflow) and **D11** (one source, several experts); **D8** narrowed. §4 and §5 updated to match. | Ruled by the human after a live measurement of the Librarian: given a topic question it did **not** delegate — it read the topic folders itself and answered from raw files, with no topic skills, no `expert.md` persona and no per-topic voice, and on another run claimed *"the Cooking expert checked the knowledge base"* when no expert had run. |

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

| #   | Decision | Rationale |
|-----|----------|-----------|
| D1  | Harness is [`langchain-ai/deepagents`](https://github.com/langchain-ai/deepagents) | Native support for subagents, skills with progressive disclosure and override resolution, pluggable filesystem backends, declarative filesystem permissions, and HITL interrupts — most of Part 2 of the README maps to existing features. |
| D2  | Daemon + thin clients, not embedded library | Required by D3. Also gives Telegram uptime independent of the terminal, and one writer to the KB. |
| D3  | Threads are shared across channels | Start a conversation in the TUI, continue it from Telegram. Requires a durable shared checkpointer and thread discovery in every client. |
| D4  | Custom daemon on the MIT LangGraph core — **not** LangGraph Platform | `langgraph-api` (the runtime behind `langgraph dev`) is Elastic License 2.0, requires `LANGGRAPH_CLOUD_LICENSE_KEY`, Postgres, and Redis. `langgraph`, `langgraph-checkpoint`, and `langgraph-checkpoint-sqlite` are MIT and provide everything needed: compiled graphs, durable threads, streaming, and `Command(resume=...)`. |
| D5  | HTTP + SSE for human channels; MCP for programmatic agents | Two different audiences. ACP is a client↔agent protocol (editors, streaming, permission prompts); MCP is an agent↔capability protocol. External Project Manager agents want the PKB as a capability. |
| D6  | KB tree is **plain markdown files** — no version control in the first draft | Keeps Layer 1 to a single responsibility (structure) and removes commit-policy questions (when, what message, what on failure) from the first build. Git is additive later: it observes the tree rather than changing how anything writes to it. See §10. |
| D9  | The Telegram adapter is **hosted inside the daemon process**, calling `PkbService` directly | The bot's required lifetime *is* the daemon's lifetime. Hosting it in the TUI would tie it to a foreground terminal; hosting it standalone adds a third process and an auth boundary for no gain. It is enabled by config and supervised as a background task. |
| D7  | Skills use `skills/<name>/SKILL.md`, amending README §2.4 | deepagents' native format. Buys progressive disclosure and name-collision override resolution with no custom code. |
| D8  | Each topic is its own compiled graph. **Amended 2026-08-07**: it is no longer registered with the Librarian as a `CompiledSubAgent` — the routing workflow invokes it directly (D10). | Dict-subagents are one-shot; README §1.6 requires multi-turn approval dialog. One artifact still serves both access paths. Experts keep `task` among themselves (their own general-purpose subagent); the Librarian does not get one, because with routing in code the tool is redundant and leaving it leaves the bypass. |
| D10 | **Librarian routing is a harness-encoded workflow, not a model decision.** A turn is: (1) classify — the one model call, answered through a `route` tool; (2) fan out — code invokes every applicable expert; (3) merge — code composes the reply by attribution; (4) offer the answering experts for direct connection. Ruled by the human, 2026-08-07. | Measured: a Librarian holding a `task` tool and free to decide **did not delegate**, and once claimed an expert had contributed when none had run. The merge is the part that must not be a model call — attribution assembled from actual results cannot lie about who contributed, and a model asked to write the merge demonstrably does. When classification is uncertain the harness asks the human **which experts to engage, from a menu of candidates**; a wrong guess files knowledge in the wrong place and there is no undo (D6). |
| D11 | **One source may be ingested by several experts.** Fan-out applies to information exactly as it applies to questions; each expert extracts the facets its own topic cares about, and each may decline. Ruled by the human, 2026-08-07. | *"A management book can offer lessons on management & parenting."* Two topic-lens extractions of one source are not two copies of one file, and README §1.8 rule 4 ("no copies") is scoped to **solution notes** — that misreading was made once already and corrected. Misrouted material that nobody files is a correct outcome, not a partial failure. |

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
- **Telegram inside the TUI process** — the TUI is a foreground process that exits with the terminal;
  Telegram exists precisely to reach the PKB when you are away from the terminal. Opposite lifetimes.
  What the two channels *should* share is approval rendering, which is a client-side helper (§6), not
  a process. See D9.

---

## 3. Layers and seams

```
        ┌─────────────────┐            ┌──────────────────┐
        │   Textual TUI   │            │  external agents │
        └─────────────────┘            └──────────────────┘
              │ REST + SSE                    │ MCP (streamable HTTP)
┌──────────────────────────────────────────────────────────────┐
│  LAYER 3 — TRANSPORT            pkb.server        ┌────────┐ │
│  FastAPI: /agents /threads /runs /interrupt       │Telegram│ │
│           + mounted MCP                           │ bot    │ │
│                                                   └───┬────┘ │
└──────────────────────────────────────────────────────────────┘
              │  PkbService  (in-process Python interface)  ◄──┘
┌──────────────────────────────────────────────────────────────┐
│  LAYER 2 — AGENTS               pkb.agents                   │
│  AgentRegistry · Librarian graph · one expert graph / topic  │
│  deepagents: skills, subagents, HITL, filesystem backend     │
└──────────────────────────────────────────────────────────────┘
              │  validators + generators (direct calls)
┌──────────────────────────────────────────────────────────────┐
│  LAYER 1 — KB CORE              pkb.core                     │
│  frontmatter schema · validator · index/tags generators ·    │
│  topic scaffolder                                            │
└──────────────────────────────────────────────────────────────┘
                              │
              KnowledgeBase/  (plain markdown tree on disk)
```

The Telegram bot runs *inside* the daemon and calls `PkbService` directly rather than looping back
through HTTP (D9). It is still a Layer 3 component and still bound by invariant I2.

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
        paths=["/kb/**/index.md", "/kb/**/tags.md"],   # corrected 2026-08-07 — see below
        mode="deny",
    ),
]
```

> **Corrected 2026-08-07 (Layer 2 rules D-5).** The original list read `["/kb/**/index.md",
> "/kb/index.md", "/kb/tags.md"]` — one glob too many and one glob short. wcmatch's `GLOBSTAR`
> matches **zero** directories, so `/kb/**/index.md` already covers `/kb/index.md`; and
> `/kb/<topic>/tags.md` was unprotected, verified by a scripted write that landed on disk and would
> have been maintained by no generator. The two globs as built are derived from
> `pkb.core.is_derived_name` and equivalence-tested against it over a walked tree, rather than
> restated. `operations=["write"]` only: derived files must stay **readable**, or the routing
> catalog itself becomes invisible.

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
│   └── scaffold.py         # new topic / sub-topic structure
├── agents/          # Layer 2
│   ├── registry.py         # AgentRegistry: catalog, lazy graph construction, invalidation
│   ├── librarian.py        # root routing agent (classify step)
│   ├── routing.py          # the fan-out and the merge — code, not a model (D10)
│   ├── expert.py           # Topic Expert factory (template + expert.md override)
│   ├── middleware/         # KbValidation, KbMaintenance, KbBreadth (§4), KbRouting (D10)
│   └── runtime.py          # shared checkpointer, store, backend, locks, run/resume/cancel
├── contracts.py     # the Layer 2 ↔ Layer 3 seam: a leaf module, no harness imports (I2)
├── service.py       # PkbService protocol + implementation
├── server/          # Layer 3
│   ├── app.py              # FastAPI; owns the daemon lifecycle
│   ├── routes.py
│   ├── mcp.py              # mounted MCP server
│   └── telegram.py         # daemon-hosted bot task (D9), optional via config
├── clients/         # shared client-side helpers
│   └── approval.py         # interrupt event → decision, used by TUI and Telegram
└── tui/             # Textual client (separate process, HTTP + SSE)
```

---

## 4. Agent topology and addressing

Every topic gets its **own compiled graph** — `create_deep_agent(...)` with its own system prompt,
skills chain, and breadth files. That single artifact serves both access paths:

- **Direct connection** — addressable on its own, with its own threads and checkpoints.
- **Librarian routing** — invoked by the routing workflow below, on a thread derived from the
  Librarian's.

> **Corrected 2026-08-07 — `memory=` (Layer 2 rules D-11).** This section originally said each
> expert graph gets `memory=[topic.md, notes/summary.md]`. **Do not restore it.** deepagents'
> `MemoryMiddleware` injects a system prompt that instructs the model to call `edit_file` on the
> memory files *"to persist new knowledge"* — those two files are exactly the human-approval
> surfaces README §1.6 says the AI never finalizes on its own — and it caches their contents in
> checkpointed state, so a long-lived thread never sees the human's edit. `KbBreadthMiddleware`
> replaces it, reading both files fresh on every model call and appending them to the system
> message. The *intent* of this paragraph — breadth always in context, `index.md` on demand — is
> preserved exactly; only the mechanism changed.

### The Librarian turn (D10)

Routing is a workflow the harness runs, not a decision the Librarian may take differently. Four
steps, three of them code:

1. **Classify — the one model call.** The Librarian is given the generated root catalog and answers
   by calling a `route` tool with the applicable topic ids and a one-line reason. It is a tool call
   rather than structured output because the `format` parameter is ignored on the deployment's
   Ollama cloud endpoint (measured, 2026-08-07). This is the only step with discretion in it.
2. **Fan out — code.** The runtime invokes every applicable expert graph. Not a tool the model may
   decline to call; a step that always runs, for **information exactly as for questions** (D11).
   Concurrency is capped and the cap is configurable — the deployment allows three concurrent cloud
   models, so a five-topic item runs five experts three at a time. The cap bounds concurrency, never
   the set: dropping an expert from the fan-out would silently lose the extraction this design
   exists to produce.
3. **Merge — code, never a model.** The reply is composed by attribution: each expert's own answer
   under its own heading, with the expert's title and agent id, verbatim. An expert that failed, or
   that is waiting on an approval, gets a section saying so, and the rest of the reply is still
   delivered. An expert that had nothing to contribute says so in **its own words** — the merge does
   not classify that, because deciding an answer means "declined" requires reading the answer to
   decide what it meant, and a decline is a correct outcome rather than a status. A merge written by
   a model is exactly how *"the Cooking expert checked the knowledge base"* gets said when no expert
   ran.
4. **Offer direct connection.** The reply names the agent ids that answered, and each has a real,
   addressable thread, so a client can offer "continue with the Cooking expert" and mean it.

**When classification is uncertain, the harness asks the human — with a menu of candidate experts.**
Not a guess and not an open question. It fires when the model answers in prose instead of calling
`route` — after one forced retry — or when nothing it named resolves against the catalog. The menu
is the turn's **reply**: the candidate experts, plus whatever the model did say, quoted rather than
hidden, so the human can see it is a question about their item rather than a system error. It lands
in the Librarian's thread like any other reply, so it survives a restart and is answerable from any
channel; the human's choice arrives as the next message. A topic gap — nothing applicable and
nothing worth choosing between, which is every item in a fresh knowledge base — goes to the gated
`create_topic` flow instead (README §2.2).

**The Librarian holds no `task` tool.** With routing in code it is redundant, and leaving it leaves
the bypass that D10 exists to close. Experts keep theirs (D8).

**Addressing.** Agent IDs mirror the tree: `librarian`, `topic/cooking`, `topic/cooking/grilling`.
Sub-topics resolve to the nearest ancestor holding an `expert.md`, falling back to the topic root —
README §1.8 rule 5, implemented as path resolution rather than a special case.

**Registry.** `AgentRegistry` scans the tree at startup to build the catalog, but constructs graphs
**lazily on first use** and caches them. Fifty topics must not mean fifty graphs at boot. Topic
creation invalidates the registry.

**Shared runtime.** One `AsyncSqliteSaver` checkpointer, one store, and one backend:

```python
CompositeBackend(
    default=StateBackend(),                                    # agent scratch, thread-scoped
    routes={
        "/skills/": FilesystemBackend(root_dir=PACKAGED_SKILLS, virtual_mode=True),
        "/kb/":     FilesystemBackend(root_dir=KB, virtual_mode=True),
    },
)
```

> **Corrected 2026-08-07 — threads (Layer 2 rules D-6).** This section originally said *"a thread is
> `(agent_id, thread_id)`"*. The checkpointer keys on **`thread_id` alone**; `checkpoint_ns` is not
> usable as a second dimension (passing one explicitly makes `aget_state` raise). So thread ids must
> be globally unique, and the `(agent_id, thread_id)` association is bookkeeping in the daemon's own
> `threads` table (§5), not something the checkpointer can answer.

**Expert threads are derived, not opaque.** The work the routing workflow gives an expert runs on
`<librarian-thread>::<agent-id>` — a deterministic function of the Librarian's thread, so it needs
no table to resolve and no id to invent. That is what makes step 4 real: "continue with the Cooking
expert" opens a thread that exists, holding the exchange the human just read part of. It replaces
the nested `checkpoint_ns` the harness would otherwise use, which no client can address.

**Consequence.** A conversation held *directly* with the Cooking expert on a thread of the human's
own and the work the Librarian routed to it are still different conversations, on different threads.
"Continue what the Librarian was doing" resumes the Librarian's thread; "continue with the Cooking
expert" resumes the derived one. Both are addressable, and the TUI should say which is which.

**Model.** Configurable per agent; default `ollama:deepseek-v4-flash:cloud` with a local
`ollama:gemma4:31b` fallback, both chosen on a measured evaluation of this workload (Layer 2 rules
Q6 — this document's original `anthropic:claude-sonnet-5` was a placeholder). The model is a
registry concern, not a transport concern.

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
renders on `message.complete`), and MCP wants only the final result. Proxying raw stream payloads
would require every adapter to understand deepagents internals, making invariant I2 fiction.

> **Corrected 2026-08-07 — the stream API (Layer 2 rules D-12).** This section originally named
> `astream_events(version="v3")`. On the langgraph pin, v3 is a **different protocol**: a coroutine
> that must be awaited before iteration, yielding JSON-RPC-style `{"type","method","params"}`
> envelopes with no `event` key — not the `on_chat_model_stream` shape assumed here. Layer 2 uses
> `graph.astream(input, cfg, stream_mode=["updates","messages"], subgraphs=True)`. `subgraphs=True`
> is required, or a subgraph's messages are invisible.

`subagent.start`/`subagent.end` let the TUI show *"→ routing to Cooking expert"*, making README
§2.2's routing behaviour visible instead of a silent pause. Under D10 they are emitted by the
fan-out step itself, once per expert, naming the **delegate** — not derived from a `task` tool call,
which the Librarian no longer has. Between them the runtime forwards that expert's own events —
message deltas, tool starts, an approval it raised — relabelled with the expert's agent id and
carrying its derived thread id, so a client can show progress per expert and resolve an expert's
approval on the thread that owns it.

A run's `run.end` for a Librarian turn carries the **merged, attributed** text (§4 step 3). The
Librarian's own prose is not the answer and never reaches the client as one; the same merged text is
appended to the Librarian's thread so replayed history shows what the human actually read.

### Thread metadata

The LangGraph checkpointer persists graph state per thread but does not index threads by agent or
carry a title. The daemon keeps its own table in the same SQLite file:

```
threads(thread_id, agent_id, title, created_at, updated_at, origin_channel,
        pending_interrupt_id)   -- nullable; set while an approval awaits a decision
```

Without it, "list my threads with the Cooking expert" has no answer and cross-channel resume has no
discovery surface.

Two thread-id shapes are **derived rather than minted by a client**, and Layer 3 must recognize both:
`<librarian-thread>::<agent-id>` for work the routing workflow gave an expert (§4), and
`scan:<agent-id>:<uuid4>` for a conflict-scan run (§7). Both are ordinary threads — resumable,
holding real history, carrying their own approvals. A routed thread belongs in the human's thread
list as a child of the Librarian's; a scan thread is machine bookkeeping and belongs out of it.

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

### Telegram (daemon-hosted)

Runs as a supervised background task inside the daemon process and calls `PkbService` directly — no
HTTP round trip, no second process, no auth boundary (D9). Enabled by config; absent config, the
daemon starts without it.

One bot. Default target is the Librarian; `/agents` lists, `/connect cooking` switches — direct
expert access without a bot per topic. Approvals arrive as inline keyboard buttons.

`edit` is impractical on a phone, so the Telegram adapter narrows `allowed_decisions` to
approve/reject and directs the human to the TUI for anything needing an edit.

Because the bot shares a process with the daemon, its task is supervised: an unhandled exception
restarts the bot task and is logged, but never terminates the daemon.

### Shared approval helper

Both human channels must turn an `interrupt` event into a `Decision` consistently — same action
parsing, same validation of which decisions are allowed. That logic lives once in
`pkb.clients.approval` and is imported by the TUI and the Telegram adapter. Only the *rendering*
differs: a diff modal in the TUI, inline keyboard buttons in Telegram.

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

## 7. Layer 1 enforcement and the scan queue

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

`wrap_tool_call` records touched paths in middleware state; `after_agent` consumes and clears them.
Per-write regeneration would rewrite `tags.md` several times in one turn for no benefit.

The flush runs on both success and failure. A run that errors midway may have already written files,
and leaving the tree with stale derived files is worse than the partial write itself — regeneration
is idempotent, so running it is always safe.

> **Corrected 2026-08-07 — where that guarantee comes from (Layer 2 rules D-1).** `after_agent`
> cannot deliver it. It is a graph node on the **normal exit edge**: any exception aborts the
> superstep and it never executes — verified across four failure shapes, each leaving a written file
> and no flush. It also does not run on an *interrupted* turn; it runs once when the resumed run
> completes. So the guarantee lives in `pkb.agents.runtime`, which wraps **every** graph execution
> in `try/finally`, recovers the touched paths from the checkpoint when `after_agent` did not fire,
> and flushes there — exactly one flush per run on both paths. A daemon-startup
> `regenerate_all` closes the abandoned-approval case. This is why the runtime owns the only
> sanctioned way to execute a graph: HTTP runs, Telegram runs, the scan worker, the routing
> workflow's fan-out and MCP calls otherwise make a fifth caller that forgets structurally possible.

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

> **Corrected 2026-08-07 — where the 409 comes from (Layer 2 rules D-15).** Not from the harness.
> LangGraph OSS has **no multitask strategy** — that is a Platform feature — and verified,
> `asyncio.gather` of two runs on one thread returned two successes with interleaved writes. The 409
> is Layer 2's own per-`(agent_id, thread_id)` active-run registry raising `ThreadBusyError`, which
> Layer 3 maps. A second, related refusal is Layer 2's too: sending a new message to a thread with a
> pending interrupt silently **discards** the interrupt in the harness, so the runtime refuses that
> as well.

**Fan-out concurrency (D10).** The routing workflow runs experts up to the configured cap at a time,
each on its own derived thread, and each takes the write lock for its own flush — so their flushes
serialize while their model calls do not. Because a routed thread is an ordinary thread, the two
refusals above apply to it: an expert still sitting at an unresolved approval from an earlier turn
cannot be given new work, and the merge reports that instead of silently dropping the interrupt.
**One expert failing must not lose the others**: a failed fan-out branch becomes its own section in
the merged reply, and the rest is delivered.

**Failure modes.**

| Failure | Behaviour |
|---------|-----------|
| Frontmatter validation fails | Error `ToolMessage`, agent retries. Bounded to 3 attempts per file per run, then escalates to the human. |
| Model or provider error | `run.error` event; thread remains resumable. |
| Client disconnects mid-approval | Interrupt persists in the checkpoint, appears as pending on `list_threads`, and **any** client can resolve it later. |
| Telegram bot task crashes | Supervised restart; logged and surfaced in `/health`. The daemon and in-flight runs are unaffected (D9). |
| Run errors after partial writes | The runtime's `try/finally` flush runs (see §7's correction), so derived files match the tree. Without version control there is no rollback — see the caveat in §10. |
| One expert fails during a fan-out | Its failure is reported in its own section of the merged reply; every other expert's answer is still delivered. A fan-out is not all-or-nothing. |
| An expert declines the routed material | A correct outcome, not a failure (D11). Its section says so, and nothing is filed in that topic. |

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
suite runs free and fast; live tests are a thin top layer. The stock langchain fakes cannot drive a
deep agent — they inherit a `bind_tools` that raises, and `create_agent` always calls it — so Layer 2
ships its own `ScriptedChatModel` fixture.

Moving routing into code (D10) moves most of it out of the live suite: classification is one scripted
tool call, and the fan-out, the merge, the cap, the derived thread ids and the failure isolation are
all code with no model in them. What still needs a live model is whether the classification is
*right*.

Invariants I1 and I2 are enforced by an import-linter rule in CI, not by convention.

---

## 10. Out of scope

Deferred deliberately, not overlooked:

- **Version control of the KB (D6).** The first draft writes plain markdown. The consequence is
  real and worth stating: there is **no undo**. If an agent writes something wrong and the human
  approves it, the previous content is gone. Two things make this survivable in a first draft — the
  approval gate means nothing lands unreviewed, and Layer 1's regeneration is idempotent, so derived
  files can always be rebuilt from content. Adding git later is purely additive: it observes the tree
  after each flush and changes no write path. Until then, back up the KB directory.
- **ACP adapter.** Would expose the PKB to Zed and other ACP editors. Additive once `PkbService`
  exists — a fourth adapter, no core changes.
- **Multi-user.** The PKB is personal. `StoreBackend` namespacing and auth are unnecessary; the
  daemon binds to localhost.
- **Remote deployment topology.** The daemon is host-agnostic. Where it runs, and how the TUI reaches
  it when it is not local, is a deployment question for when Telegram lands. Note that D9 ties the
  bot to the daemon's host, and the daemon's host is where the KB lives.
- **Sandboxing.** `FilesystemBackend(virtual_mode=True)` confines agents to the KB root. No shell
  access is granted, so `LocalShellBackend` and sandbox backends are not used.

---

## 11. Build order

Bottom-up, one spec and plan per layer:

1. **`pkb.core`** — schema, validator, generators, scaffolder. No LLM, no agents, fully TDD-able.
   Every guarantee above rests on it. **Built.**
2. **`pkb.agents`** — runtime, registry, expert factory, Librarian, the middleware (three, not two —
   see §4's `memory=` correction), default skills. **Built**, with the Librarian's routing workflow
   (D10) as the one part specified but not yet implemented: see §1.4 of the Layer 2 rules for which
   `LB-*` rules are designed-but-unproven and which are verified.
3. **`pkb.service` + `pkb.server`** — the protocol, its implementation, HTTP routes, SSE, MCP mount.
   **← next.**
4. **`pkb.tui`** + **`pkb.clients.approval`** — Textual client, including the approval diff modal.
5. **`pkb.server.telegram`** — daemon-hosted bot task, reusing the approval helper from step 4.

Each built layer has its own rules document with stable rule ids that its tests cite; where the
implementation diverged from this document, the divergence is recorded there and the correction is
carried back here rather than left to a reader to notice.

---

## 12. README amendments

All of the following are **applied** to `README.md` as of 2026-08-07.

1. **§2.4 and §1.3** — skills become `skills/<skill-name>/SKILL.md` rather than
   `skills/<skill>.md`, adopting the deepagents Agent Skills format. Applies to both the PKB root
   `skills/` folder and topic-level overload folders. Override resolution (same skill name, later
   source wins) is then provided by the harness, and it is **whole-record**: a topic's copy replaces
   the root one rather than merging with it.
2. **Part 3** — the KB tree diagram updates to reflect the same change.
3. **§1.4 (new)** — `skills/**` is a **third file class**, alongside knowledge files and
   machine-generated files: exempt from PKB frontmatter and from index and tag generation, carrying
   the harness's `name`/`description` instead. Forcing the PKB fields onto a `SKILL.md` breaks
   deepagents' own parser and the skill disappears from the prompt with no error anywhere.
4. **§2.2** — routing is described as the harness-run workflow it now is (D10), including the expert
   menu when classification is uncertain.
5. **§1.8 rule 4 and §2.2/§2.3** — one source may be ingested by several experts, each through its
   own topic's lens, and an expert may decline (D11). Rule 4 gains a clause saying it is scoped to
   *solution notes*, because the generalization to source ingestion was made once already.
6. **§2.6** — the agent-hierarchy diagram shows the fan-out and the merge.
7. **Part 3 step 2** — `voice` ships an opinionated starter profile rather than being bootstrapped
   from a questionnaire (ruled by the human, 2026-08-07; Layer 2 rules C8).
