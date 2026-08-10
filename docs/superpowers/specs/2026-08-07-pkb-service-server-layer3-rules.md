# PKB Service + Server (Layer 3) — Requirements and Rules

**Date**: 2026-08-07
**Status**: **Built (2026-08-08).** 1529 tests, ruff, mypy-strict and four import contracts green.
See §8 "As built" for what the grounding re-run corrected and what the test suite found.
**Scope**: `pkb.service`, `pkb.server` (`app`, `routes`, `sse`, `mcp`, `telegram` wiring), `pkb.packs`,
and the Layer 3 tables — build-order step 3 of the
[architecture design](2026-08-06-pkb-architecture-design.md), built on the merged
[Layer 1 rules](2026-08-06-pkb-core-layer1-rules.md) and
[Layer 2 rules](2026-08-06-pkb-agents-layer2-rules.md) (1155 tests, no API key, no network).

### What is verified and what is not

Same convention as Layer 2, and it matters more here than anywhere: four of the five package
assumptions this document rests on were **executed**, and one of them found a bug that would have
shipped silently.

| Marking | Means |
|---------|-------|
| **verified** | Executed in the grounding pass against a real resolution of `fastapi 0.141.1` / `starlette 1.4.1` / `uvicorn 0.52.1` / `sse-starlette 3.4.8` / `mcp 2.0.0` / `httpx2 2.9.1` on Python 3.12.13, with this repo's `pyproject.toml` + `uv.lock`. Not read — run. Every row of §2 carries it. |
| **built (Layer 2)** | Already implemented and covered by the passing suite; Layer 3 binds against it. |
| **designed** | Specified here, not yet implemented. Every `SV-*`, `AP-*`, `RO-*`, `SS-*`, `MC-*`, `ST-*`, `PK-*` rule carries this unless its Source column says otherwise. |

A rule can be verified about the packages and still only designed as behaviour: `AP-4`'s connection
ordering rests on a verified fact (the WAL pragma is set in `setup()`, not by `from_conn_string`,
P-5) and an unbuilt mechanism.

---

## Read this first

Three lenses mined Layer 3 (service/threads, HTTP+SSE+concurrency, MCP+packs); a fourth **executed**
every package assumption. Where a lens and the executed grounding disagree, **the grounding wins**.

Four findings are load-bearing enough to state up front, because each would have failed silently —
the tests would have passed and the guarantee would have been fiction:

| | What the architecture assumes | What the packages actually do |
|---|---|---|
| **P-2** | Arch §7's corrected flush guarantee: the runtime's `try/finally` delivers *"exactly one flush per run on both paths"*. | **False across an SSE hangup.** `asyncio.Task.cancel()` is edge-triggered; an **anyio cancel scope** — which both `StreamingResponse` and `EventSourceResponse` use to abort on `http.disconnect` — is level-triggered, so *every* await inside the runtime's `finally` raises again. Measured against the **real `PkbRuntime`**: note written, `flush reports with stamps: []`, `index.md lists the note: False`. Driving the same run through a plain `asyncio.Task` pump: `stamped: [['Cooking/notes/reverse-sear.md']]`.  **⚠ NOT REPRODUCED — see the correction note in §2.** |
| **P-4** | Arch §9: *"`pkb.server` — FastAPI `TestClient` against a stub `PkbService`"*. | `TestClient` **cannot test streaming**: `_TestClientTransport.handle_request` does `raw_kwargs["stream"] = httpx.ByteStream(raw_kwargs["stream"].read())`. Measured: 50 frames arrived as one chunk, and breaking after chunk 1 left the server generator running to completion. `httpx2.ASGITransport` buffers too. |
| **P-3** | Arch §6: *"Mounted at `/mcp` on the same FastAPI app."* | `app.mount("/mcp", …)` **307-redirects** `/mcp` → `/mcp/`, **throws away the sub-app's lifespan** so nothing serves, `session_manager.run()` may be called **once per instance** (forcing an app factory), and DNS-rebinding lockdown **421s the test client** by default. |
| **P-1** | — (not in the arch doc) | **`httpx` has gone 2.x under a new distribution name, `httpx2`.** `mcp 2.0.0` requires it; `starlette.testclient` imports it. Both `httpx` and `httpx2` install side by side and do not collide. `pkb.agents.models`' `httpx.ConnectError` predicate keeps working untouched; Layer 3 binds `httpx2`. **`httpx-sse` is unnecessary** — `httpx2` ships `EventSource`/`ServerSentEvent` natively. |

The corrected approach for each is in §2 and carried into the rule table.

### Decisions applied on top of the mined recommendations

| # | Decision | Why |
|---|----------|-----|
| **A** | **The daemon owns runs; the HTTP request does not.** `PkbService.start_run()` drives the run in a plain `asyncio.Task` publishing into a per-run hub; an SSE response merely subscribes. A dropped connection detaches, it does not cancel. | Forced by durability. D2's whole promise is that a turn outlives the terminal that started it — an ingestion turn killed because a phone crossed a tunnel is that promise broken — and D3's cross-channel resume assumes the run belongs to the daemon, not to a socket. (It was originally argued twice over, the second argument being P-2's lost flush; that measurement **did not reproduce** — see the correction in §2 — and this decision does not need it.) It also makes `GET /threads/{id}/events` (reattach) a subscription rather than a redesign. |
| **B** | **`pkb.server` never imports `pkb.agents`, not even transitively.** `create_app(open_service, …)` takes a service factory; the composition root that opens `PkbRuntime` is `pkb.service.runtime`, wired by `pkb/daemon.py`. | This is the configuration the grounding **verified passes** (`source_modules = ["pkb.contracts","pkb.service","pkb.server"]` → 3 kept, 0 broken) *and* verified catches a planted transitive violation (`pkb.server.app -> pkb.agents.runtime -> langgraph`). `allow_indirect_imports = true` on `pkb.server` would keep the contract green while deleting exactly the check that caught it. The MCP session manager already forces an app factory (P-3c), so this costs one parameter. |
| **C** | **`pkb.service` is a package, not a module**, with the Protocol and the tables harness-free and one named module (`pkb/service/runtime.py`) permitted to import `pkb.agents`. | Something must call `PkbRuntime.open`. Naming exactly one module keeps I2 structural instead of exempting a whole package, so a later `pkb/service/proposals.py` cannot inherit the exemption silently. Same trick `pkb/contracts.py` used at the Layer 2 seam. |
| **D** | **Layer 3 stores nothing it can derive.** No `parent_thread_id` column, no `kind` column, no `agent_id` on a derived thread's row beyond what the id already says. `Thread` exposes both as **computed** fields so no client string-sniffs. | RT-36's own words: *"deriving is not inventing — there is nothing to look up and nothing to keep in sync."* A cached parentage column is a second answer to a question `librarian_thread_id()` already answers exactly. |
| **E** | **The checkpoint is the authority on a pending approval; `pending_interrupt_id` is an index.** It is reconciled against the checkpointer at startup, repaired on read, and never used to refuse a run. | RT-38 makes the checkpoint durable across a restart; the column can be stale in **both** directions. A false negative hides a pending approval from every channel — the one failure arch §8 promises cannot happen — and is never discovered, because nobody resumes a thread they cannot see. |
| **F** | **Propose-only proposals get a durable home in Layer 3 (`pkb_proposals`), listed and dismissable — but v1 cannot *apply* one.** | `PkbRuntime` keeps them in a list that dies with the process and offers only an optional `proposal_sink` (verified, `runtime.py:1385-1397`). "The human sees them in the TUI" is prose without persistence. *Applying* one needs a new Layer 2 entry point and an RT-18 amendment — that is Layer 2 work, and it is Q3 below rather than step 3. |
| **G** | **Context packs move below the seam: `pkb/packs.py`, a leaf beside `pkb/contracts.py`.** Topic *selection* by classification stays in Layer 2. | Amends Layer 2's Q10, on three things that changed since: the pack types must cross the seam anyway (I2), PK-3/PK-4 make assembly a pure function of `pkb.core`'s derived surface, and PK-1/PK-2 are golden-file tests that belong in the free-and-fast profile rather than behind a checkpointer and a chat model. |

---

## 0. Conventions

Same severity convention as Layers 1 and 2. **Rule ids are stable**; a test that changes must cite
the rule that changed. Every test assertion says **no key** or **live**.

### 0.1 Hard constraints inherited

- **I2** — transports never import `deepagents`/`langgraph`/`langchain`/`langchain_core`. This is the
  constraint the whole layout exists to make structural.
- **I3** — only Layer 1 writes derived files. Layer 3 writes **nothing** under `kb_root`, by any path.
- **Everything mechanical already exists below.** Layer 3 cites Layer 1 and Layer 2 rules; it never
  contains a second implementation of validation, generation, id parsing, thread-id derivation,
  event normalization, diff rendering, or decision validation.

### 0.2 Package layout (extends arch §3)

```
pkb/
├── contracts.py                 # THE SEAM — leaf, no harness imports (built)
│                                #   gains: UnknownThreadError, EVENT_NAMES, the thread-id helpers,
│                                #   Pack/PackEntry/Escalation, OriginChannel
├── packs.py                     # NEW leaf: deterministic pack assembly over pkb.core (decision G)
├── core/                        # Layer 1 (built)
├── agents/                      # Layer 2 (built)
├── service/
│   ├── __init__.py              # PkbService Protocol, Thread, ThreadDetail — harness-free
│   ├── threads.py               # the `threads` table (aiosqlite, own connection)
│   ├── proposals.py             # the `pkb_proposals` table
│   ├── runs.py                  # the run supervisor + per-run hub (decision A)
│   └── runtime.py               # RuntimeService — the ONE module that imports pkb.agents
├── server/
│   ├── app.py                   # create_app(open_service, config) — lifespan, workers, mounts
│   ├── routes.py
│   ├── sse.py                   # AgentEvent -> bytes, one table, one direction
│   ├── errors.py                # typed error -> (status, code), one place
│   ├── health.py
│   ├── mcp.py                   # the four tools + two resources
│   └── telegram.py              # daemon-hosted bot task (D9) — step 5
├── clients/approval.py          # step 4
├── tui/                         # step 4
└── daemon.py                    # composition root: opens the runtime, calls create_app
```

---

## 1. Rule table

### 1.1 `pkb.service` — the Protocol and its implementation — SV

| ID | Rule | Source | Sev | Test assertion (live model?) |
|----|------|--------|-----|------------------------------|
| SV-1 | `PkbService` is a **Protocol** in `pkb/service/__init__.py` whose every parameter and return type is a `pkb.contracts` type, a Layer-3-owned dataclass of primitives (`Thread`, `ThreadDetail`, `RunSubscription`), or a builtin. No signature names `AgentGraph`, `Interrupt`, `Command`, `RunnableConfig` or any harness type. This is what makes arch §9's stub possible: a stub is writable because the Protocol is expressible without the harness. | arch §5 L271-280, §9 L520-524; layer2 §5.2 L711-715; I2 | error | AST over the Protocol: every annotation resolves to `pkb.contracts`, `pkb.service`, `pkb.core` or builtins. **no key** |
| SV-2 | The runtime-backed implementation lives in `pkb/service/runtime.py` and it is the **only** Layer-3 module that imports `pkb.agents`. It reaches the harness through the two names `pkb.agents` exports — `PkbRuntime`, `RuntimeConfig` — and names no harness module directly. | decision C; layer2 decision B; `pkb.agents.__all__` (built) | error | AST: the only harness-adjacent import in `pkb/service/` is `from pkb.agents import PkbRuntime, RuntimeConfig`, in exactly one file. **no key** |
| SV-3 | The runtime is constructed **once**, by `pkb.service.runtime.open_service(kb_root, db_path, config)`, an async context manager held for the daemon's lifetime. `AsyncSqliteSaver` closes its connection on context exit and pins itself to its creating loop (RT-2), so a module-level singleton cannot work. | RT-2, RT-7; arch §3 L155-159 | error | Opening and closing the service twice over one SQLite file both succeed; `PkbRuntime` appears in exactly one module. **no key** |
| SV-4 | The service is **constructor-injected** with a structural `Runtime` Protocol, never with a concrete `PkbRuntime`. That is the property that lets the *real* service class run in a harness-banned subprocess against a fake runtime (SV-30). | arch §9 L513-533; `tests/agents/test_contracts.py` (built) | error | The real `PkbService` implementation imports and runs with `deepagents`/`langgraph`/`langchain` banned from `sys.meta_path`. **no key** |
| SV-5 | The method → runtime mapping is exactly: `list_agents→list_agents` (sync), `create_thread/list_threads/get_thread/set_title→SQL only`, `start_run→run(agent_id, thread_id, message, approval_mode=…, run_id=…)`, `resume→resume(agent_id, thread_id, decisions, interrupt_id=…)`, `cancel→cancel(run_id)`, `delete_thread→delete_thread` + the row cascade, `history/pending_approval→history/pending_approval(agent_id, thread_id)`, `run_scan→request_scan`, `regenerate→regenerate`. The service adds **no** behaviour to a run: it does not retry, does not reorder events, does not synthesize an `AgentEvent` the runtime did not emit, and does not swallow one. | arch §5 L271-280; layer2 §5.2 L676-709; `runtime.py:682-928` | error | Spy runtime: one call per service method with the ids it was given; an event-identity test asserts the list the service yields is the list the runtime yielded. **no key** |
| SV-6 | Runs are addressed **by thread, never by agent**: `start_run(thread_id, …)` and `resume(thread_id, …)` take no `agent_id`; the service resolves it (SV-9). This is what makes cross-channel resume a one-field handoff — Telegram needs only the id from `list_threads` to continue what the TUI started — and it is the same routing-by-thread rule Layer 2 applies to approvals. | arch §5 L277-278, D3; LB-10, LB-16 | error | `inspect.signature` on both has no `agent_id`; a Librarian thread and an expert thread run through the identical call shape. **no key** |
| SV-7 | **The `(agent_id, thread_id)` association exists in exactly one place: the `threads` row.** Every Layer 2 call needs both ids and the checkpointer cannot answer which agent owns a thread (D-6, D-19, RT-49), so the service supplies `agent_id` from the row or from the id's shape — and that is why every HTTP route can take a thread id alone. | D-6, D-19, RT-36, RT-49 | error | With a stub runtime, `stream_run` on a Cooking thread passes `agent_id="topic/cooking"`. **no key** |
| SV-8 | `create_thread(agent_id, *, title="", origin_channel=…)` validates the agent against the catalog and raises `UnknownAgentError` **before** inserting a row, and it must **not** compile a graph — `list_agents()` reads the cached catalog and compiles nothing (RG-3/RG-4). Creating fifty threads builds zero graphs. | arch §5 L275; RG-3, RG-4, RG-13 | error | `create_thread("topic/atlantis")` raises `UnknownAgentError`; a registry spy counts zero graph builds over N calls. **no key** |
| SV-9 | Thread ids live in **three provably disjoint namespaces** and the service resolves an agent by shape first, table second: `<uuid4>` → `threads.agent_id`; `<parent>::<agent-id>` → everything after the first `::` (LB-14); `scan:<agent-id>:<uuid4>` → machine bookkeeping, refused for user operations (RT-58). A minted id must contain no `::` and must not start with `scan:` — `uuid4` satisfies both, and the service **asserts** it rather than assuming it. | arch §5 L333-338; RT-36, RT-58, LB-14; `routing.py:472-503`, `scans.py:70` | error | Property test: the three shapes never overlap; `agent_for_thread("<u>::topic/cooking/grilling") == "topic/cooking/grilling"` even though the id contains `/`. **no key** |
| SV-10 | **Layer 3 mints every user thread id, and only Layer 3.** A bare `uuid4`, never client-supplied, never derived from a title, agent id, chat id or MCP argument. `create_thread` takes no id parameter. Layer 2 explicitly refuses to invent one (RT-36), so if the transport does not mint it nobody does. | arch §5 L226-236; RT-36 | error | `inspect.signature(create_thread)` has no id parameter; grep finds no `thread_id=` built from a chat id or slug. **no key** |
| SV-11 | `thread_id` must be **globally unique across agents**, not per agent: the checkpointer keys on `thread_id` alone and `checkpoint_ns` is unusable as a second dimension (D-6, verified — the Librarian graph on the Cooking expert's thread id read its four messages verbatim). Two agents sharing an id silently merge two conversations into one checkpoint, with no error anywhere. The PRIMARY KEY enforces it for minted ids; SV-9's disjoint namespaces enforce it for derived ones. | D-6; arch §4 L245-249; RT-37 | error | Duplicate insert → `IntegrityError`; a regression test asserts a Librarian thread and an expert thread never collide. **no key** |
| SV-12 | An **unregistered** thread with a recognizable shape is still openable, runnable and resumable: `get_thread`/`start_run`/`resume` on `<parent>::<agent-id>` work from the id alone with no row. The row is an index for **discovery**, never the authority on **existence** — the checkpoint is. Such a call registers the row as a side effect. **Amended 2026-08-09 (Layer 5 §9.5, defect 1): only when `<parent>` exists as a registered row.** Shape-first resolution requires the parent; when it does not exist the derived id is `UnknownThreadError` → 404, with **zero rows created and zero runs started**. Original wording preserved above and unchanged for the real case, because after a fan-out `create_thread` made the parent row before the derivation existed. | arch §5 L333-338; RT-38; LB-14; L5 §9.5 | error | Delete the derived row, then `resume` on the derived id: the approval still resolves and the row reappears (**unchanged, still passes**). And: `POST /threads/<fresh-uuid4>::topic/cooking/runs` → 404, zero `threads` rows, zero `start_run`. **no key** — *Why*: executed before the amendment, that call returned **200** with a full event stream, ran a real expert turn against a checkpoint nothing had ever written, and registered a permanent `kind:"routed"` row whose `parent_thread_id` 404s. SV-12's stated reason is that a *lost* row must not hide a real conversation; a derived id whose parent never existed has no checkpoint and never had one, so nothing was lost — self-registering it manufactures an orphan `/threads` lists forever and no cascade ever deletes. |
| SV-13 | The service **refuses user operations on a `scan:` thread** — `get_thread`, `start_run`, `resume`, `delete_thread` all raise `UnknownThreadError` → 404 — and never creates a row for one. A conflict scan is machine bookkeeping whose context must never enter a human conversation. | RT-58; Q9; `ScanResult.thread_id` | error | After a scan, `list_threads()` is unchanged and `get_thread(result.thread_id)` is 404. **no key** |
| SV-14 | `get_thread(thread_id) -> ThreadDetail` carries the row plus: the thread's `AgentDescriptor`, `messages` from `runtime.history`, `pending` read **live** from `runtime.pending_approval` (never from the column), and `children` — the threads this turn routed to, carried for **provenance**, since their primary home is their own expert's list (RO-7). One call must be enough to render a conversation and its approval, because that is all a client re-attaching from a second channel has. | arch §5 L276; RT-38; arch §8 L502 | error | Interrupt a run, restart the service over the same SQLite file, `get_thread` returns the live `ApprovalRequest` with rendered `ActionView`s. **no key** |
| SV-15 | `resume` calls `pkb.contracts.validate_decisions(pending, decisions, interrupt_id=…)` **itself**, before touching the runtime. Arch §5's two-argument sketch cannot express staleness and RT-40 requires refusing it. The service validating and the runtime validating again is deliberate: the shared validator in the seam exists precisely so every caller answers "which decisions are allowed" identically. | arch §5 L278, §6 L385-389; RT-40; `contracts.validate_decisions` (built) | error | A stale `interrupt_id` raises `StaleInterruptError` with the graph never invoked; a `respond` against `["approve","reject"]` raises `InvalidDecisionError`. **no key** |
| SV-16 | The service must **not** pre-empt Layer 2's refusals from its own column: it never returns 409 by reading `pending_interrupt_id` and never refuses a second run by consulting the table. `ApprovalPendingError` (RT-39/D-16) and `ThreadBusyError` (RT-45) are the live registry and checkpoint state; a stale column would refuse a legitimate turn with no way for the human to clear it. | RT-39, RT-45; D-15, D-16; decision E | error | Hand-set `pending_interrupt_id` on an uninterrupted thread: `start_run` still runs. Interrupt for real: the runtime raises and the route returns 409. **no key** |
| SV-17 | `start_run` takes `approval_mode: ApprovalMode = "interactive"` and forwards it. Arch §5's Protocol omits it, but without it the MCP adapter cannot get propose-only behaviour without importing the harness — and an MCP write that interrupts hangs forever on a decision no robot can make. | arch §6 L405-409; RT-42; `contracts.ApprovalMode` (built) | error | A propose-only run emits zero `InterruptEvent`s, writes no file, and records one `PendingProposal`. **no key** |
| SV-18 | The config the service hands the runtime is only ever `thread_id`. It never constructs a `RunnableConfig`, never sets `checkpoint_ns` (it makes `aget_state` raise outright), never sets `recursion_limit` (already set by `create_deep_agent`'s `.with_config`). `runtime.thread_config` is Layer 2's and stays there. | RT-37; EX-16; `runtime.py:930-938` | error | grep: no `configurable` dict and no `checkpoint_ns` literal in `pkb/service/` or `pkb/server/`. **no key** |
| SV-19 | `cancel(run_id)` is opaque and un-scoped: the service forwards the id and does not look up which thread it belongs to. A Librarian turn drives several graphs under one run id and the runtime cancels the whole family (`<run_id>` and `<run_id>::<agent_id>`); narrowing cancellation to one thread would leave expert runs alive after the human cancelled the question that started them. Cancelling an unknown id is a no-op, not an error. | arch §5 L279; RT-46; `runtime.py:810-827` | warning | Cancelling a Librarian turn mid-fan-out stops every expert run; an unknown id is a no-op. **no key** |
| SV-20 | The service exposes `list_proposals()`, `get_proposal(id)`, `dismiss_proposal(id)` and `run_scan(request)` — four methods arch §5's Protocol does not have. Each is forced by a Layer 2 rule (RT-42 twice, C12) and an extra that lives only on the concrete class is an extra the stub cannot fake, which pushes the MCP adapter and the scan worker into the live suite. | arch §6 L405-409, §7 L466-470; RT-42, C12 | error | Protocol conformance against the stub; `GET /proposals` returns recorded rows. **no key** |
| SV-21 | The service must **not** construct a `ScanRequest` by hand and must not import `pkb.agents.scans`: on-demand scans go through `pkb.core.build_scan_requests` and then `runtime.request_scan`. | RT-54, RT-57, RT-58; C12 | error | grep: no hand-built `ScanRequest(` in `pkb/service/` or `pkb/server/`; no `pkb.agents` import in the worker. **no key** |
| SV-22 | **No Layer 3 module writes to the knowledge base.** No `open(...,'w')`, no `Path.write_text` under `kb_root`, no `pkb.core.flush`/`scaffold` call of its own. Every mutation goes through the agent tool path, where validation, the deny list and the gate table sit. The one sanctioned Layer 1 call is `runtime.regenerate()` (RT-7), and it is the runtime's. | I3; RT-18 (the same argument one layer up); README §2.1 | error | AST/grep over `src/pkb/service` and `src/pkb/server`: no write call resolving under the KB root. **no key** |
| SV-23 | The service never deletes or rewrites a `threads` row as a side effect of a **run** failing. A run that errored leaves a resumable thread (RT-47), so its row survives with `updated_at` bumped; only `delete_thread` removes a row. Losing it would make a resumable conversation invisible to every channel — the checkpoint would still exist and nothing could find it. | RT-47; arch §8 L500-501; D6 | error | A scripted model that raises leaves exactly one row, `pending_interrupt_id IS NULL`, and `get_thread` still replays the pre-error history. **no key** |
| SV-24 | `delete_thread` calls `runtime.delete_thread(thread_id)` **and then** deletes the parent row together with every `thread_id LIKE '<parent>::%'` row, in that order. Layer 2 already cascades the derived children's checkpoints (RT-48, Q15, `runtime.py:864-891`); if the SQL cascade did not mirror it, the table would keep rows pointing at erased checkpoints. Deleting a **derived** thread must not reach sideways to siblings or upwards to the parent — matching the runtime's own asymmetry. | arch §6 L352; RT-48; Q15 | error | Fan out to two experts, delete the Librarian thread: three rows gone, three checkpoint sets gone. Delete only a derived thread: parent and sibling remain. **no key** |
| SV-25 | **The titling call is the one model call Layer 3 makes, and it is off the critical path.** A thread is named by a model after its first assistant reply (TT-1…TT-4, Q4 RULED): the reply is delivered first, the titling call runs once, its failure is logged and leaves the title null, and a human-set title is never overwritten. Nothing else in Layer 3 calls a model — not the merge (MC-9), not a pack (PK-8), not agent selection (MC-16, MC-19) — and Layer 3 constructs no model client of its own: the titling call goes through the runtime like every other model call, so `pkb/server` stays model-free. | Q4 (RULED 2026-08-07); TT-1…TT-4; Q6 | error | A `ScriptedChatModel` with an exhausted script is never called by `create_thread`, and never on the run's critical path — the titling call happens after `RunEnd` is delivered. **no key** |
| SV-26 | Until the titling call lands, a thread's title is **null**, and clients render a placeholder — not a truncated first line. `create_thread` accepts a client-supplied title (which then wins permanently, TT-4) and Telegram supplies none, which is fine: titling happens after the first reply, so there is nothing for D9 to ask for up front. Null and empty are **not** the same signal — "not titled yet" must be distinguishable from "titled empty" (ST-5). | Q4 (RULED); TT-1, TT-2, TT-4; D9 | warning | A thread created and never run has `title is None`; a titling failure leaves it `None` rather than a first-line fallback. **no key** |
| SV-27 | A model-written title is written **once** and never overwritten; `set_title(thread_id, title)` from any channel wins permanently. Watching a title change under them makes the thread list unusable as the place to find the approval they left pending yesterday. | arch §8 L508-510; D6 (no undo) | warning | Turn 2 does not change the title; `set_title` then turn 3 keeps the human's string. **no key** |
| SV-28 | A **derived** thread's title is generated at registration and is not human-editable in v1: `'<expert title> — via "<parent title>"'`. The human never named that conversation and never will — it exists because the Librarian routed to it — and leaving it blank makes the thread tree unreadable. | LB-9, LB-14, Q14; arch §4 L251-260 | info | After a fan-out the child row's title contains the expert's `AgentDescriptor.title` and the parent's; `set_title` on a derived thread is refused. **no key** |
| SV-29 | **"Continue with the Cooking expert" is an ordinary `start_run` on the derived thread id** — `runtime.run` → `_stream`, the same call a direct conversation makes. The service must not special-case it, must not copy history into a new thread, and must not route the follow-up back through the Librarian. That is what makes step 4's offer a link rather than a sentence. | arch §4 L251-260; LB-14, LB-15; `runtime.py:977-1009` | error | Fan out, then `start_run` on `<t>::topic/cooking`: the expert's history includes the routed exchange and the new turn appends to it. **no key** |
| SV-30 | The Layer 2 seam acceptance test is **promoted, not replaced**: the real `PkbService` implementation must run in a subprocess whose `sys.meta_path` refuses `deepagents`, `langgraph`, `langchain` and `langchain_core`, driven against an injected fake runtime — the shape of `tests/agents/test_contracts.py`. | arch §9 L513-533; layer2 §5.2 L711-715 | error | The real class imports and runs with the harness banned, and the same class drives a real `PkbRuntime` in-process. **no key** |

---

### 1.2 `pkb.server.app` — lifecycle, run supervision, workers — AP

| ID | Rule | Source | Sev | Test assertion (live model?) |
|----|------|--------|-----|------------------------------|
| AP-1 | **`create_app(open_service, *, config) -> FastAPI` is an app factory, not a module-level `app`.** `StreamableHTTPSessionManager.run()` raises `RuntimeError: … can only be called once per instance` (**verified**), and every test that exercises the lifespan enters it again. | P-3c (verified); decision B | error | Two `create_app()` calls each enter and exit their lifespan cleanly; a module-level `app` is absent. **no key** |
| AP-2 | `pkb.server` imports **no** `pkb.agents` module and no harness module, directly or transitively. The service arrives as `open_service`, an async-context-manager factory typed against the Protocol. | I2; D-20; decision B; grounding §"Bonus" (verified) | error | `lint-imports` with `source_modules = ["pkb.contracts","pkb.server","pkb.tui","pkb.clients"]` and **no** `allow_indirect_imports` passes on the real tree and fails on a planted `pkb.server.app -> pkb.agents.runtime`. **no key** |
| AP-3 | The lifespan does, in order: open the service (which opens `PkbRuntime`, which runs `regenerate_all`, RT-7) → **open Layer 3's SQLite connection** → reconcile `pending_interrupt_id` → enter `mcp_server.session_manager.run()` → start the scan worker → start the Telegram task if configured. Shutdown reverses it. | RT-7; P-3b (verified); decision E | error | A second startup over the same SQLite file succeeds; `/health` reports every subsystem started. **no key** |
| AP-4 | **Layer 3 must open its SQLite connection after `PkbRuntime.open()`.** `AsyncSqliteSaver.from_conn_string` is a bare `aiosqlite.connect`; `PRAGMA journal_mode=WAL` lives in `setup()`. Measured timeline: `right after from_conn_string(): delete` → `after await saver.setup(): wal`. Opening earlier talks to a rollback-journal file, where a reader blocks a writer. | P-5 (verified); RT-4 | error | A connection opened before the runtime sees `journal_mode == 'delete'`; opened after, `'wal'`. **no key** |
| AP-5 | The daemon reconciles **every `threads` row against the checkpointer at startup** — one `pending_approval` per row, rewriting the column — before serving. This is the `pending_interrupt_id` analogue of RT-7's `regenerate_all`, and it is what makes the column survive a restart honestly: it can be stale in both directions. Bounded by the thread count, which for a personal KB is small. | RT-7, RT-38; arch §8 L502; D-14; decision E | error | Interrupt a run, hand-corrupt the column, reopen: the column matches the checkpoint and `list_threads` badges the right thread. **no key** |
| AP-6 | **A run is never driven from an SSE response handler.** `pkb.service.runs` drives `runtime.run(...)` in a plain `asyncio.Task` that publishes into an `asyncio.Queue`-backed hub; the SSE generator subscribes to the hub and, in its `finally`, **unsubscribes** — it never cancels the run task (AP-7). The reason is durability, not a package quirk: a run driven from the response handler lives and dies with the socket, so D2/D3's promise that a turn outlives the client that started it becomes conditional on a stable connection, and cancellation silently becomes something the network does rather than something the human does (RO-18). *(This rule was originally justified by P-2's lost-flush measurement; that measurement did not reproduce — §2 — and is no longer cited. Severity stays **error** because the guarantee at stake is arch §8's headline one.)* | D2, D3; decision A; arch §8 L502, L509-510; RT-46 | **error** | Raw ASGI driver, hard disconnect mid-run: the run task reaches its terminal event, the note is on disk and the topic `index.md` lists it; grep asserts no response handler iterates `runtime.run`. **no key** |
| AP-7 | **A dropped connection detaches; it does not cancel the run.** The daemon exists so a turn outlives the terminal that started it (D2), and an ingestion turn killed because a phone crossed a tunnel is that promise broken. The run continues to its own ending — completion, error, or an approval that parks durably in the checkpoint. Cancellation is a deliberate act with its own route. | D2; arch §8 L502, L509-510; RT-46; decision A | error | Abort the client mid-stream, then assert the run reached its terminal event and the thread's state advanced. **no key** |
| AP-8 | The hub is **per run**, subscribers are many, and the response subscribes **before** the run task starts so no early frame is lost. Each subscriber gets its own bounded queue; a subscriber that overflows is **disconnected**, not waited for. Layer 2's own queue is bounded at 64 so a slow consumer throttles the model stream — right for one consumer, wrong once one stalled browser can stall the run. | `runtime.EVENT_BUFFER_SIZE`; decision A | warning | Two subscribers both receive every frame; a subscriber that never reads is dropped while the run completes normally for the other. **no key** |
| AP-9 | The hub keeps a **bounded per-run replay buffer** keyed by `seq`, so a reattach (`GET /threads/{id}/events`) starts from `seq 0` of the run in flight rather than mid-sentence. The buffer is dropped one grace period after the terminal frame. | SS-5; decision A | warning | Attach a second client mid-run: it sees every frame from `seq 0` and the same terminal frame. **no key** |
| AP-10 | `start_run` performs its refusals **synchronously, before the response commits**: it awaits the run task's admission (the first `__anext__` of `runtime.run`, which is where `ThreadBusyError`/`ApprovalPendingError`/`UnknownAgentError` are raised) and re-raises them to the caller. The alternative is a 200 that later has to carry a 409, and headers cannot wait a whole model call. | RT-39, RT-45, RG-13; arch §5 L282-299 | error | With a stub whose first event is delayed 5 s, a busy thread returns 409 within milliseconds and no `text/event-stream` header is sent. **no key** |
| AP-11 | **A cancelled run produces no terminal event from Layer 2** — `stream_events` re-raises `CancelledError` without emitting `run.error` (`events.py:464`) and the queue simply closes. The supervisor therefore synthesizes the terminal frame: `run.error` with `code: "cancelled"`, `retryable: true`. Without it every attached client hangs on a stream that ended silently. This is a **transport frame**, not a fabricated `AgentEvent`, and it is the only frame Layer 3 authors. | `events.py:464-473` (built); SS-7 | error | Cancel mid-run: the subscriber's last frame is `run.error` `code=cancelled` and the response closes. **no key** |
| AP-12 | Daemon shutdown cancels every in-flight run (`PkbRuntime.aclose` already does) and every subscriber gets AP-11's terminal frame before its response closes. `sse-starlette`'s `shutdown_event` + `shutdown_grace_period` is the mechanism, so generators exit cooperatively instead of eating a `CancelledError`. Anything not flushed is covered by the next boot's `regenerate_all`, so shutdown never blocks on a model call. | `runtime.aclose` (built); RT-7; D-14; grounding §2 (verified) | warning | Shutdown mid-run: subscribers receive `run.error code=cancelled`, the lifespan exits within a bounded time, the next startup rewrites derived files. **no key** |
| AP-13 | After a disconnect the `(agent_id, thread_id)` active-run slot stays held until the in-flight model call winds down — **measured at +3 s to +5 s**, after which a second run was admitted. This is correct behaviour, and Layer 3 must expect a legitimate `409 thread_busy` on an immediate reconnect and say so in the UI rather than treating it as an error. | grounding §2 (verified); RT-45 | warning | Disconnect then immediately re-POST: a 409 whose body carries `code=thread_busy` and a retry hint. **no key** |
| AP-14 | The **conflict-scan worker is Layer 3's and the graph run is Layer 2's** (C12). The daemon holds the harness-free `ScanQueue` Protocol from `pkb.contracts`, dequeues on a timer, and calls `service.run_scan(request)`. A scan must not consume a fan-out slot a human is waiting on, so the worker takes at most one request at a time. | arch §7 L466-470; C12; RT-54, RT-57, RT-58 | warning | The worker drains an injected in-memory queue and calls `run_scan` once per request; `/health.scan_worker.pending` falls to 0. **no key** |
| AP-15 | The daemon passes `RuntimeConfig.proposal_sink` a sink that **persists** each `PendingProposal` into `pkb_proposals`. `PkbRuntime` keeps them in memory only (`runtime.py:893-900`), so without this a proposal is a write the human never got to review after one restart. | RT-42; `runtime.py:471,1385-1397` (built); decision F | error | A propose-only run records a proposal that survives a service restart and appears in `list_proposals()`. **no key** |
| AP-16 | The daemon passes `RuntimeConfig.flush_sink` a sink that logs every `FlushReport` and surfaces its `findings` count in `/health`. `None` drops broken links, orphans and `DERIVED_WRITE_FAILED` on the floor — a convenience in a unit test and a defect in a daemon. The sink is **not run-correlated**, so it is diagnostics, not per-run provenance (see Q6). | MW-24; `RuntimeConfig.flush_sink` (built) | warning | A flush over a KB with a broken link reaches the injected sink and increments the health counter. **no key** |
| AP-17 | The **Telegram bot is a supervised background task inside the daemon calling `PkbService` directly** — no HTTP round trip, no second process (D9). An unhandled exception restarts the task, is logged, and is surfaced in `/health`; it never terminates the daemon or an in-flight run. The supervision loop is Layer 3's alone: RT-50 says Layer 2 contains no supervision, no `/health` and no origin-channel tracking. | D9; arch §6 L369-383, §8 L503; RT-50 | warning | A bot task that raises is restarted, logged, and reflected in `/health`, while a concurrent run completes normally. **no key** |
| AP-18 | **`GET /health` returns 200 while the process is serving, always.** A flapping Telegram task must not make the daemon look dead to a supervisor — D9's whole point is that the bot crashing never terminates the daemon — so degradation is reported in the body (`status: "ok" \| "degraded"`), never in the status code. A 503 would invite exactly the restart D9 forbids and would kill in-flight runs and pending approvals that are perfectly healthy. | arch §8 L503; D9 | error | With the bot in a restart loop, `/health` is 200 with `status == "degraded"` and the daemon still serves runs. **no key** |
| AP-19 | `/health` is cheap and side-effect-free: **no** KB tree walk, no `regenerate`, no graph compile, no checkpointer read, no model call. `agent_count` comes from the cached catalog; `scan_worker.pending` from one indexed `COUNT(*)`. A health endpoint that walks the tree times out exactly when the system is under load. | RG-3/RG-4; RT-51 | warning | Spies assert zero calls to `scan`, `regenerate`, `registry.get` and `aget_state` across 100 requests. **no key** |
| AP-20 | The daemon binds **localhost**, has no auth and no multi-user namespacing, and no route carries a version prefix. Arch §10 defers deployment topology deliberately. Every route sits behind one `APIRouter` so a prefix is a one-line change later. | arch §10 | info | The bind address is `127.0.0.1` by default; the route table has no `/v1`. **no key** |

---

### 1.3 `pkb.server.routes` — RO

| ID | Rule | Source | Sev | Test assertion (live model?) |
|----|------|--------|-----|------------------------------|
| RO-1 | The HTTP surface is arch §6's **eight** routes plus **five declared additions** and the `/mcp` route. Any route beyond those thirteen is an addition that must be given a rule id. Additions: `GET /threads/{id}/events`, `PATCH /threads/{id}`, `DELETE /runs/{run_id}`, `GET /proposals`, `DELETE /proposals/{id}`. | arch §6 L345-354 | error | `[r.path for r in app.routes]` equals the pinned set plus the declared additions; a new route without a rule id fails the test. **no key** |
| RO-2 | **An `agent_id` contains `/` and is opaque**: the route declares `{agent_id:path}` and passes the captured string to the service verbatim. `%2F` is not a workable alternative (Starlette decodes it back before matching, and proxies normalize it). Nothing in Layer 3 splits, re-encodes, slugifies or fuzzy-matches an id. | RG-9, RG-11, RG-12; `AgentDescriptor.agent_id` docstring | error | `POST /agents/topic/cooking/grilling/threads` resolves `topic/cooking/grilling`; `%2F` resolves to the same id or 404s, never to a different one. **no key** |
| RO-3 | **A `thread_id` is not URL-simple either**: derived threads contain `::` *and* `/`, scan threads contain `:`. Every `{id}` in a thread route is `{thread_id:path}`, and routes with a literal suffix (`/runs`, `/interrupt`, `/events`) are registered **before** the bare greedy ones so `GET /threads/{tid:path}` cannot swallow `/threads/x/events`. | LB-14, `EXPERT_THREAD_SEPARATOR`; RT-58, `SCAN_THREAD_PREFIX`; arch §5 L336-338 | error | `GET /threads/<uuid>::topic/cooking` returns that thread; `GET /threads/<uuid>/events` hits the events route. **no key** |
| RO-4 | `GET /agents` returns `AgentDescriptor` **verbatim** from `list_agents()`: all five fields, Librarian first, topics in snapshot order, no field added, nothing reordered, no `model_id` chosen or overridden. It compiles no graph and walks no tree. | arch §5 L273, §6 L346; C13, RG-14, RG-15, RG-21, RG-3 | error | `service.list_agents() == runtime.list_agents()` object-for-object; a spy asserts no graph factory call; grep finds no model literal in `pkb/server` or `pkb/service`. **no key** |
| RO-5 | `POST /agents/{agent_id:path}/threads` → **201** with the full `Thread` and a `Location` header. Body (undefined by arch §6): `{"title": str\|null, "origin_channel": OriginChannel\|null}`, both optional. Unknown agent → 404 `unknown_agent`. | arch §6 L348; arch §5 L326-327; RG-13 | error | 201 + `Location: /threads/<id>`; the row round-trips through `GET /threads/{id}`; `topic/atlantis` → 404 `unknown_agent`. **no key** |
| RO-6 | `GET /threads?agent_id=` filters by **exact match**, never prefix — `topic/cooking` must not return `topic/cooking/grilling`'s threads — and sorts `pending_interrupt_id IS NOT NULL DESC, updated_at DESC`. Arch §8 says the thread list "should be designed around" the abandoned-approval case; a list sorted by creation date buries the very thread the human came back to answer. | arch §6 L347, §8 L502, L508-510 | error | A parent/child topic fixture: filtering returns only exact matches; the oldest interrupted thread sorts first. **no key** |
| RO-7 | **`list_threads` is grouped per expert.** A derived `<thread>::<agent-id>` appears in `list_threads(agent_id)` for **the expert that ran it**, alongside the conversations the human held with that expert directly; navigation is by subject, because "what have I been doing with Cooking" is the question a human actually asks. `scan:` threads are excluded everywhere (RT-58), and they are the only exclusion. The Librarian parentage is not stored and not lost: it stays derivable (`librarian_thread_id()`, decision D) and `ThreadDetail.children` remains available as **provenance**, no longer as the derived thread's only home. `Thread.kind`/`parent_thread_id` (ST-6) are what let a client tell a routed thread from a direct one inside one list. | Q2 (RULED 2026-08-07); LB-9, LB-14; RT-58; decision D | error | After a two-expert fan-out, `list_threads("topic/cooking")` contains that expert's derived thread, `list_threads("librarian")` contains the parent, `get_thread(parent).children` still has two, and `list_threads()` contains no `scan:` thread. **no key** |
| RO-8 | A derived thread carrying a **pending approval** is badged and labelled with its parent turn, and sorts first (RO-6's ordering) in its expert's group and in an unfiltered listing. Under RO-7's grouping it is already visible; what this rule buys is that the human coming back from a phone finds it **without knowing which expert the fan-out reached** — an expert parked at an approval during a fan-out (LB-16/LB-17's `awaiting-approval`) is precisely what they came back to resolve, and the merged reply that named it is long gone. | arch §8 L502, L508-510; LB-16, LB-17; Q2 | error | A fan-out where one expert gates: the gated derived thread is badged, labelled with the parent, and sorts ahead of every non-pending row in an unfiltered `list_threads()`. **no key** |
| RO-9 | On the **list**, `pending_interrupt_id` is served from the column (an index — one `aget_state` and one lazy graph compile per row is not a list operation). On **`GET /threads/{id}`** the pending approval is read authoritatively from `runtime.pending_approval`, and a disagreement **repairs** the column. | arch §6 L347; RT-38; RG-4; decision E | warning | A stub reporting a pending approval for a thread whose column is null: `GET /threads/{id}` reports it and the column is repaired; `GET /threads` compiles no graph. **no key** |
| RO-10 | `GET /threads/{id}` → `{thread, messages, pending_interrupt, children}`. `MessageView.created_at` is **always null** from Layer 2 (LangChain messages carry no timestamp), so it is nullable on the wire and no client may sort on it — per-thread times come from the table. An unknown thread is decided by the `threads` table plus SV-9's shapes, not by the checkpointer (an unknown id yields empty graph state, not an error) → 404 `unknown_thread`. | arch §6 L349; `runtime.history` docstring (built); SV-9 | error | A never-created thread id returns 404 rather than an empty 200; every `created_at` in the payload is null. **no key** |
| RO-11 | `POST /threads/{id}/runs` body is `{"message": str}` and **nothing else**. The route does **not** expose `approval_mode`: `propose_only` means Layer 2 auto-rejects every gate (RT-42), which over a human channel is a run that silently refuses its own approvals and files nothing — a broken agent, not a mode. It is set in-process by MCP, where the reason for it actually holds. `run_id` is server-minted and returned in `run.started` before the first token, so cancel is never a race. | arch §6 L350; RT-42, RT-46; SS-8 | error | A body carrying `approval_mode` is 400; an empty `message` is 400; every frame's `run_id` equals the one in frame 0. **no key** |
| RO-12 | `POST /threads/{id}/interrupt` body is `{"interrupt_id": str, "decisions": [Decision]}`. **`interrupt_id` is required on the wire** even though `validate_decisions` takes it optionally: two channels looking at one approval is the design, not an edge case, and without the id a second client's stale decisions apply to whatever is pending now — silently, with no undo. Requiring it turns a lost update into a clean 409 `stale_interrupt`. **A deliberate deviation from arch §6's `{decisions: […]}` sketch.** | arch §6 L351; RT-40, RT-41; D3, D6 | error | A body with no `interrupt_id` is 400; a stale one is 409 `stale_interrupt` and the thread is still interrupted afterwards. **no key** |
| RO-13 | The interrupt route validates **before it opens a stream**: `pending_approval` then `pkb.contracts.validate_decisions(...)`, the one shared harness-free validator. Only then does the response become SSE. This is what makes 400/409 deterministic rather than something smuggled into an already-committed 200. | `validate_decisions` docstring (built); RT-40; arch §6 L385-390 | error | Wrong-count and disallowed-type bodies both return JSON errors with no `text/event-stream` header and the stub's `resume` never called. **no key** |
| RO-14 | An approval is resolved **on the thread that owns it**. An expert's gate raised inside a fan-out parks on `<librarian-thread>::<agent-id>`, so the client POSTs there. The server must not "helpfully" redirect an interrupt posted to the Librarian's thread — the Librarian's thread is never left interrupted by a delegate, and such a request is a genuine 409 `stale_interrupt`. | LB-10, LB-16; `runtime.resume` docstring (built) | error | Posting a delegate's decisions to the parent thread returns 409 and the derived thread is still interrupted; posting to the derived thread resolves it. **no key** |
| RO-15 | A client may **narrow** `allowed_decisions` for its UI and never widen it: Telegram drops `edit`. The server-side set is the truth and is re-validated on the way back in (RO-13), so a hand-crafted request carrying `edit` against an action that forbids it is a 400 regardless of channel. | arch §6 L378-380; RT-32; `ActionView.allowed_decisions` (built) | error | A forged `edit` decision against a two-decision action raises `InvalidDecisionError` → 400. **no key** |
| RO-16 | `DELETE /threads/{id}` → **204**, with the SV-24 cascade. While a run is active on that thread it returns **409 `thread_busy`**: deleting erases checkpoints and derived expert threads, there is no undo (D6), and a run in flight may be mid-write. Making the human cancel first is one extra call and it is the call that says they meant it. | arch §6 L352; RT-48, Q15; D6 | error | Delete a Librarian thread that fanned out to two experts: three rows gone, `runtime.delete_thread` called once with the parent id. Delete during a run: 409. **no key** |
| RO-17 | **Proposed addition** `GET /threads/{id}/events` → SSE: attach to whatever is running on that thread, with no side effects, replaying the hub from `seq 0` (AP-9), and **204** when nothing is running. This is how a reconnecting TUI or a second channel rejoins without starting a second run — which `POST /runs` would refuse with 409 anyway. | arch §6 (not pinned); D3; RT-45; decision A | warning | Start a run, attach a second client mid-stream, assert it sees every frame and the same terminal frame. **no key** |
| RO-18 | **Proposed addition** `DELETE /runs/{run_id}` → 204, calling `cancel(run_id)`. Without it `PkbService.cancel` has no caller and RT-46 is dead code over HTTP. An unknown run id is 204, not 404. | arch §5 L279; RT-46; SV-19 | warning | Cancel a fan-out: all expert tasks cancelled, `GET /threads/{id}` still returns history, unknown id is 204. **no key** |
| RO-19 | **Proposed additions** `PATCH /threads/{id}` `{"title": str}` → 200 (SV-27), `GET /proposals` → `{"proposals": […]}`, `DELETE /proposals/{id}` → 204. Without a retrieval call the propose-only path records into a void; without dismiss the human's queue only ever grows. | arch §6 L406-410; RT-42; decision F; SV-27 | warning | `GET /proposals` returns recorded rows; `DELETE` removes one; `PATCH` sets a permanent title. **no key** |
| RO-20 | **The typed-error → status map lives in exactly one FastAPI exception handler**: `UnknownAgentError`→404, `UnknownThreadError`→404, `ThreadBusyError`→409, `ApprovalPendingError`→409, `StaleInterruptError`→409, `InvalidDecisionError`→400. A route never builds an `HTTPException` for one of these by hand, and a `PkbAgentError` subclass with no mapping is a **500 by construction** so a new one cannot silently become a 200. | `contracts.py:393-423` (built); RG-13, RT-39, RT-40, RT-45 | error | Table test over the six errors through a stub → the six codes; an unmapped `PkbAgentError` subclass yields 500 with `code="internal"` and no module path in the body. **no key** |
| RO-21 | Because three distinct conditions share 409 and the client's reaction to each differs (retry later / render the approval / refetch the interrupt), **every error body carries a stable machine `code`** and clients branch on it, never on prose. Bodies are `application/problem+json` (RFC 9457) and `detail` is the exception's own message **verbatim** — Layer 2's messages already name the thread and what to do, and Layer 3 never re-words them, the same discipline MW-13 applies to Layer 1 findings. | arch §6; MW-13's precedent | error | `ThreadBusyError`'s message appears verbatim in `detail`; content-type is `application/problem+json`. **no key** |
| RO-22 | **Nothing may branch on `origin_channel` to decide whether a run, a resume or an approval is permitted.** D3's whole point is that a thread started in the TUI is finishable from Telegram; one authorization check against this column silently deletes that guarantee, in exactly the case the design is proudest of. It is provenance for display, notification targeting and diagnostics only. | D3; arch §8 L502, L508-510 | error | A `tui` thread is resumable through the Telegram code path; grep asserts `origin_channel` never appears in a conditional outside rendering and notification code. **no key** |

---

### 1.4 `pkb.server.sse` — SS

| ID | Rule | Source | Sev | Test assertion (live model?) |
|----|------|--------|-----|------------------------------|
| SS-1 | **Use `sse_starlette.EventSourceResponse`, not `StreamingResponse`** — and not for formatting convenience. `StreamingResponse` only aborts on disconnect below ASGI `spec_version 2.4`; driven at `2.4` it **ignored `http.disconnect` entirely** and kept generating to a 3 s timeout (verified). uvicorn 0.52.1 declares `2.3` today, so plain streaming works **by accident of a version number**. `EventSourceResponse` listens for the disconnect itself and is correct at both. It also brings ping keep-alives, `cache-control: no-store`, and `shutdown_event`/`shutdown_grace_period`. | grounding §2 — the `spec_version 2.4` measurement (reported; it was gathered in the same pass as P-2's flush claim, which did **not** reproduce, so re-run it before relying on it) | error | A raw ASGI driver at `spec_version 2.4` disconnects: the `EventSourceResponse` generator's `finally` runs; the `StreamingResponse` one does not. **no key** |
| SS-2 | Headers: `Content-Type: text/event-stream; charset=utf-8`, `Cache-Control: no-store`, `Connection: keep-alive`, `X-Accel-Buffering: no`, and **response compression is disabled for these routes** — a buffering gzip middleware is the classic way a working stream becomes a five-minute pause followed by everything at once. | grounding §2 (verified header set) | error | Header assertions on all three streaming routes; the compression middleware excludes them. **no key** |
| SS-3 | The wire event name is arch §5's name **verbatim** in the `event:` field: `message.delta`, `message.complete`, `tool.start`, `tool.end`, `subagent.start`, `subagent.end`, `interrupt`, `run.end`, `run.error` — plus two transport frames, `run.started` (SS-8) and the keep-alive comment (SS-6). The mapping from the nine `AgentEvent` dataclasses to those names lives in **one** table, next to the union it names, imported by both the encoder and the TUI decoder, and it is **total**: an unmapped member raises at import, never drops on the floor. | arch §5 L285-295; `contracts.AgentEvent` (built); RT-43 | error | `typing.get_args(AgentEvent)` is covered exhaustively; a deliberately removed row fails the test; a round-trip encodes and decodes all nine kinds. **no key** |
| SS-4 | `data:` is **one line** of compact JSON (`json.dumps(..., separators=(",",":"))`, which escapes newlines, so multi-line `data:` framing never arises). The object is `dataclasses.asdict(event)` — RT-43 guarantees that is JSON-serializable for all nine kinds — merged with a flat envelope: `type`, `seq`, `run_id`, `thread_id`, `agent_id`. Flat keeps a frame matchable to its dataclass in `contracts.py` with no mapping table. | RT-43; `contracts.py` module docstring (built) | error | No frame contains `\n` inside `data:`; every payload has the envelope keys plus exactly the dataclass fields; a test asserts **no envelope key collides** with any field name across the union. **no key** |
| SS-5 | **Amended 2026-08-08 (step 4):** `id:` is a **per-response** ordering cursor, not a per-run one — `SseEncoder` is constructed per response, so two responses over one run both number from 0 and `(run_id, seq)` is *not* an event identity. A client uses it for ordering within one stream and nothing else; a transcript keyed on it silently overwrites the first half of a resumed run. Originally: `id:` is a per-run monotonic `seq` starting at 0, incremented for every frame including `run.started` and the terminal one. A within-run cursor, not a global one — which is what makes AP-9's replay buffer additive rather than a redesign. **No `retry:` field is ever written**, and the streams are deliberately not `EventSource`-compatible: both are responses to a POST, which the browser API cannot issue. Reconnection is an explicit client act against `GET /threads/{id}/events` (RO-17), never a transport-level auto-retry that would silently re-POST a run. | arch §6 (framing undefined); RO-17 | warning | `seq` over a run is 0..n with no gaps and matches `id:`; grep: no `retry:` in the encoder. **no key** |
| SS-6 | A keep-alive **comment** frame (`: ping`) is written after every 15 s of idleness, using `sse-starlette`'s own `ping`. Idle gaps are normal, not anomalous: a filing turn measured ~16 s per model call on the default model (Q6), and a fan-out branch waiting on the concurrency semaphore emits nothing until it starts. A comment rather than `event: ping` so it can never be mistaken for a domain event by a client dispatching on `type`. | Q6 (measured); LB-15; grounding §2 (verified frame: `: ping - …`) | warning | A stub emitting nothing for 40 s produces ≥2 comment frames and no domain frame; the TUI decoder skips them. **no key** |
| SS-7 | A stream carries **exactly one** terminal frame — `run.end` or `run.error` — and the server closes immediately after writing it. A connection closing without one means *outcome unknown*: the client re-syncs with `GET /threads/{id}` rather than assuming either. Layer 2 already guarantees one terminal event per run, including for a Librarian turn where step 1's `run.end` is swallowed and each expert's terminal events are folded into the merge. | `events.stream_events` (built); `runtime._librarian_turn`; RT-47 | error | Over stubbed direct, fan-out and error runs, each stream has exactly one terminal `run.*` frame and it is last. **no key** |
| SS-8 | **Amended 2026-08-08 (step 4):** every stream **opened by `POST /runs` or `POST /interrupt`** — `GET /threads/{id}/events` passes `started=False`, so a client that asserts frame 0 is `run.started` cannot reconnect at all. On an attach the client takes `run_id`/`thread_id` from any frame's envelope and the run's agent from `GET /threads/{id}`, because on a fan-out a frame's `agent_id` is the *delegate*. Originally: the **first** frame of every stream is `run.started`, whose payload is exactly `contracts.RunHandle` (`run_id`, `agent_id`, `thread_id`), written before any event is relayed. The client has the run id (for cancel) and the agent id (for the header) before the first token — and it gives `RunHandle`, otherwise unused by Layer 2's surface, its purpose. | `contracts.RunHandle` (built, unused); arch §6 | warning | Frame 0 of every stream is `run.started` with `seq==0` and the three fields. **no key** |
| SS-9 | **Amended 2026-08-08 (step 4): four values, not three.** `completed` and `interrupted` ride on `run.end`; `cancelled` and `error` ride on `run.error`, because a cancelled run never emits `run.end` at all. The four are named in `pkb.contracts.RUN_STATUSES` so a client can match exhaustively — a three-way match falls through to "done" on every provider failure. **A run that emitted `interrupt` and then `run.end` is parked, not complete.** The harness's `astream` returns normally when a graph interrupts, so `run_end()` is emitted either way (`events.py:473`). The `run.end` envelope therefore carries `status: "completed" \| "interrupted" \| "cancelled"`, computed by Layer 3 from what it saw on the stream. Getting this wrong shows up as a TUI that says "done" over a thread waiting on a human. The `RunEnd` **dataclass is unmodified** — this is an envelope field. | `events.py:464-473` (built); RT-38; arch §8 L502 | error | A stubbed gated run yields `run.end` `status=="interrupted"` with `pending_interrupt_id` set; an ungated one yields `"completed"`. **no key** |
| SS-10 | **Every frame carries `thread_id` in the envelope, and for a fan-out event it is the expert's derived thread.** Only `InterruptEvent` carries a thread id today; the rest carry `agent_id` alone, so without this every client re-implements LB-14's derivation in its own language. The derivation is total and gated on the catalog: `run.thread_id` when `event.agent_id == run.agent_id`; `expert_thread_id(run.thread_id, event.agent_id)` when `event.agent_id` resolves in the catalog; `run.thread_id` otherwise — which is what keeps an expert's own `general-purpose` delegation (RT-44), whose agent id is not a catalog id, on the expert's own thread. | `contracts.py` (only `ApprovalRequest` carries `thread_id`); LB-14, LB-15, LB-16; RT-44 | error | A stubbed fan-out to two experts: every non-Librarian frame's `thread_id` equals `<t>::<agent_id>` and matches the `interrupt` frame's nested id; a `general-purpose` frame carries the parent thread. **no key** |
| SS-11 | The `interrupt` frame nests the whole `ApprovalRequest`: `{interrupt_id, agent_id, thread_id, actions:[{tool, args, description, allowed_decisions, reason}]}`. `description` already holds the server-rendered unified diff and any validation finding (RT-34, RT-35); `allowed_decisions` is server-side truth. Layer 3 must not recompute, filter or re-render any of it, and **no client re-reads the KB to build a diff** — I2 forbids reaching the tree through the harness, and a second diff renderer is a second answer to "what am I approving". | RT-34, RT-35, RT-41; arch §6 L358-368; `ActionView` (built) | error | Render an approval from a JSON round-trip with the KB directory deleted; grep asserts no filesystem read in the approval renderers. **no key** |
| SS-12 | **Fan-out events interleave.** Up to `fanout_limit` (default 3) experts run concurrently on one run id, so deltas from two agents alternate. A client groups by `agent_id`, treats `subagent.start`/`end` as **brackets over a concurrent branch, not nesting**, and assumes no ordering between two agents' frames. Within one `(run_id, agent_id)` Layer 2's order is preserved exactly and Layer 3 never reorders, coalesces or batches. | LB-15; `routing.FanOut.stream`; RT-44 | error | A stubbed 3-expert fan-out: per-agent order preserved, at least one interleaving, one start/end pair per agent. **no key** |
| SS-13 | Layer 3 does **not** deduplicate, filter or enrich events. Interrupt deduplication (one `Interrupt.id` under two namespaces) and message dedup already happen in Layer 2's normalizer (RT-41, RT-43); a second pass in the transport can only diverge. The transport's whole job on the event path is envelope + encode. | RT-41, RT-43; `events.EventNormalizer` (built) | warning | A stub emitting two events with the same interrupt id produces two frames; grep finds no dedupe set in `pkb/server`. **no key** |
| SS-14 | The service refuses to widen or reinterpret what Layer 2 emits: it never converts a `RunError` into an HTTP status after the stream opened (SS-15), never synthesizes a `MessageComplete` from deltas, never suppresses `SubagentStart`/`SubagentEnd`. MCP consumes only the final result by **filtering at the adapter**, not by asking the runtime for less. | arch §5 L296-299; RT-43 | warning | The MCP tool drops deltas while `stream_run` still yielded them; an event-identity test on the service. **no key** |
| SS-15 | **The status code is chosen before the first byte.** After that, every failure is a `run.error` frame. A typed error that escapes AP-10's admission check (a genuine race) arrives as the stream's **terminal `run.error` carrying the same machine `code`**, then closes. Status codes and terminal frames are two encodings of one mapping, never two different answers. Verified end to end: a mid-stream failure kept the 200 and arrived as `event='run.error' data={'message': 'provider timeout', 'retryable': True}`. | grounding §2 (verified); RT-47; RO-20 | error | A busy thread returns 409 with no stream opened; a stub raising on first anext yields a 200 stream whose only frame is `run.error` `code="thread_busy"`. **no key** |
| SS-16 | `POST /threads/{id}/runs` and `POST /threads/{id}/interrupt` both return a stream: **a resume continues the same run.** The service must not model an approval as a fire-and-forget POST plus a separate subscription — the decisions and the continuation are one call, which is what lets Telegram deliver the outcome of an approval in the handler that received the button press. | arch §6 L350-352; RT-40 | warning | Posting decisions receives the resumed run's events on the same response. **no key** |

---

### 1.5 `pkb.server.mcp` — MC

| ID | Rule | Source | Sev | Test assertion (live model?) |
|----|------|--------|-----|------------------------------|
| MC-1 | The MCP server uses the **official `mcp` SDK at 2.0.0**. Do **not** add `fastmcp` — FastMCP is now in-tree as `mcp.server.MCPServer` with `@server.tool()`, `@server.resource()`, `streamable_http_app()` and `session_manager`. Note the 2.0 renames: model fields are snake_case (`server_info`, `input_schema`, `structured_content`) while the wire stays camelCase, and `streamablehttp_client` → **`streamable_http_client`**. | P-3 (verified, real JSON-RPC exchange) | error | `list_tools()` over the mounted app returns the four names through the official SDK client. **no key** |
| MC-2 | **Mount it as a bare `Route`, not `app.mount`.** `app.mount("/mcp", …)` makes `/mcp` a **307** to `/mcp/`; arch §6's URL is `/mcp` exactly and a stricter client will not follow. Use `mcp_server.streamable_http_app(streamable_http_path="/mcp", host=…)` for its side effect (it constructs and stashes the session manager; `session_manager` raises `RuntimeError` before it is called), then append `Route("/mcp", StreamableHTTPASGIApp(mcp_server.session_manager), methods=["GET","POST","DELETE"])`. | P-3a (verified) | error | `POST /mcp` returns 200 with no redirect; `mcp-session-id` is issued on initialize. **no key** |
| MC-3 | **The daemon lifespan must drive `session_manager.run()`.** `streamable_http_app()` returns a `Starlette` whose lifespan *is* `session_manager.run()`; mounting throws it away and nothing serves. | P-3b (verified) | error | Without the lifespan, `initialize` fails; with it, the SDK client completes a handshake. **no key** |
| MC-4 | **Keep DNS-rebinding lockdown on in the daemon and pin the test client instead.** `streamable_http_app(host=…)` defaults to `127.0.0.1`, auto-enabling `allowed_hosts=["127.0.0.1:*","localhost:*","[::1]:*"]`; measured, `base=http://testserver` → **421 `Invalid Host header`**, `base=http://127.0.0.1:8000` → 200. The daemon binds localhost (arch §10), so the protection is free. | P-3d (verified) | error | The test client uses `base_url="http://127.0.0.1"` and gets 200; a `testserver` host gets 421. **no key** |
| MC-5 | Exactly **four tools**: `pkb_ask`, `pkb_ingest`, `pkb_research_pack`, `pkb_implementation_pack`. No fifth in v1 — in particular no `pkb_approve` (an external agent cannot satisfy a human gate) and no write tool bypassing the agent layer. | arch §6; D5 | error | `list_tools()` returns exactly those four names. **no key** |
| MC-6 | Discovery and proposal status are **MCP resources**, not extra tools: `pkb://agents`, `pkb://proposals`, `pkb://proposals/{id}`. This keeps arch §6's four-tool table literally true while closing two real gaps: RG-9 forbids fuzzy-matching an id, so an external agent that cannot enumerate ids can only guess; and README Part 4's feedback loop is one-directional unless the Project Manager can learn its proposal landed. | arch §6; RG-9; README Part 4 | warning | `list_resources()` returns the catalog; reading it yields the same ids `GET /agents` returns; `list_tools()` still returns four. **no key** |
| MC-7 | `pkb.server.mcp` imports only `pkb.contracts` and the `PkbService` Protocol — never `pkb.agents`, never a harness module, never an HTTP client, never `pkb.server.sse`. The Telegram adapter is subject to the same rule (D9: no HTTP round trip, no second process). | I2; D-20; D9 | error | `lint-imports`; plus an import assertion that `pkb.server.mcp` and `pkb.server.telegram` pull no HTTP client. **no key** |
| MC-8 | **Every MCP run passes `approval_mode="propose_only"`.** The mode is a property of the channel, not a tool argument: no tool exposes it and no MCP path passes `"interactive"`. Interactive mode requires a human on the call path and there is none behind `/mcp`. The contract is that MCP therefore sees **zero** `interrupt` events — it must never block and never poll for an approval. | arch §6 L405-409; RT-42; SV-17 | error | grep: `"interactive"` appears nowhere in `pkb/server/mcp.py`; a stub records the mode of every run and asserts `propose_only`. **no key** |
| MC-9 | The adapter consumes the same normalized stream as every other transport and emits only the terminal outcome: it drops `message.delta`, `tool.*` and `subagent.*` **at the adapter**, and returns `RunEnd.final_text` **verbatim**. It never re-words, shortens or summarizes it, and never makes a model call of its own. A transport that summarizes the merged Librarian reply is the same lie LB-18 exists to prevent, one layer up. | arch §5 L285-299; LB-18; layer2 §8 | error | The tool result equals `RunEnd.final_text` byte-for-byte; grep asserts `pkb/server/` contains no model client. **no key** |
| MC-10 | **`experts` is assembled from the event stream, never by parsing the merged reply.** `SubagentStart`/`SubagentEnd` give the roster and the status; each expert's own `MessageComplete` under its agent id gives its text; SS-10's derivation gives the thread id. Parsing `RunEnd.final_text` would make `merge_reply`'s rendering format a wire protocol, and LB-18's golden test would then be pinning one. | LB-15, LB-18; `routing.py:811-879`; SS-10 | error | A stubbed Librarian run yields `experts[i].thread_id == expert_thread_id(t, agent_id)`; grep asserts no parsing of merge headings anywhere in Layer 3. **no key** |
| MC-11 | Every MCP call runs on a **real, durable thread**. `thread_id` is optional: absent → `create_thread(agent_id, origin_channel="mcp")` and the id is returned so the caller can continue; supplied → it must already exist. MCP callers never mint ids (SV-10), and a caller that ignores the returned id simply gets ephemeral behaviour. | arch D3, D-6; RT-36; SV-10 | error | Two calls with the returned id share history; an unknown id errors rather than silently creating one; the row's `origin_channel == "mcp"`. **no key** |
| MC-12 | A client-supplied `thread_id` matching a **derived** shape is refused: anything containing `::` or starting `scan:`. Those two shapes are functions of something the daemon already has; accepting one from outside lets an external agent write into a conversation, or a maintenance run, it does not own. | arch §5 L333-338; RT-36, RT-58, LB-14; SV-9, SV-13 | error | Table test over `"t::topic/cooking"`, `"scan:topic/cooking:abc"` and a uuid: the first two rejected `invalid_argument`, the third accepted. **no key** |
| MC-13 | **MCP-created threads are listed, not hidden**, labelled `origin_channel="mcp"`. A `PendingProposal` the human must review is meaningless without the conversation that produced it, and hiding robot threads is how a knowledge base fills with writes nobody can trace. | arch §6 L391-409; RT-42; RO-7 | warning | A `pkb_ingest` call leaves one listed thread and one proposal whose `thread_id` matches it. **no key** |
| MC-14 | Layer 2's typed errors become **structured** results with the same machine `code` table `pkb.server.errors` owns — `unknown_agent`, `thread_busy`, `approval_pending`, `stale_interrupt`, `invalid_decision`, `unknown_thread` — never free prose. The caller is a program: `thread_busy` and `approval_pending` are stated non-retryable-on-this-thread so a client does not spin, and `RunError.retryable` is passed through as its own field. **One table shared with the HTTP mapping** so the two cannot drift. | `contracts.py:393-423`; RO-20; RT-39, RT-45, RT-47 | error | Parameterized over the six errors from a stub: each yields `isError` with the expected `code`. **no key** |
| MC-15 | An MCP call is **bounded and cancellable**: the adapter mints nothing, takes the `RunHandle` from `start_run`, enforces a configured deadline, and on timeout or client disconnect calls `cancel(run_id)` and returns a `timeout` result. A Librarian fan-out drives several graphs under one run id and `cancel` already covers the family. | arch §5 L279; RT-46; SV-19; AP-10 | error | A stub whose run never terminates: the tool returns `timeout` within the deadline and `cancel` was called with the handle's run id. **no key** |
| MC-16 | `pkb_ask` accepts any catalog id **verbatim**, including sub-topic ids containing `/`. The transport never splits, re-encodes or fuzzy-matches an id, and an unresolvable id is an error **naming the id**, never a nearest-match guess. Same for `pkb_implementation_pack(topic)`, whose argument is an **agent id** (`topic/cooking/grilling`) — not a folder path, not a topic tag, not a display name. | RG-9, RG-11, RG-12, RG-13; arch §4 | error | `topic="Cooking/sub-topics/Grilling"` and `topic="topic.cooking.grilling"` both error; `topic="topic/cooking/grilling"` succeeds. **no key** |
| MC-17 | `pkb_ingest` always enters at the **Librarian** — it carries no `agent_id`. Fan-out applies to information exactly as to questions (D11): several experts may file their own extraction and any may decline. `topic_hint` and `source_type` are **advisory context appended to the item**, never a bypass: they do not select an expert, do not skip classification, and are never written into frontmatter by Layer 3. | arch §6; D11, decision G, LB-15; README §2.3 | error | A stub records the message: it ends with `content` unmodified and carries the hint as labelled context; `agent_id` is always `librarian`. Whether classification honours the hint is **live**. |
| MC-18 | **Propose-only is not read-only.** The gate table is the boundary: plain note ingestion (`notes/<t>.md`) and reference depth files are **ungated** and land unattended, while breadth summaries, new tags, `status.approved`, extension folders, `expert.md`/skill overloads, conflict resolution and deletes gate and become proposals. Every tool result must **distinguish filed from proposed**; conflating them makes an external agent believe a summary update landed when it did not. | RT-23 … RT-31, RT-42; README §1.1 goal 2 | error | A propose-only run writing one note and gating one `notes/summary.md`: the result names one proposal, the note exists on disk, the summary does not. **no key** |
| MC-19 | **When classification does not land, the expert menu is an ordinary successful result** — not an error and not an interrupt. The caller may answer it as the next message on the same thread. The candidate ids are surfaced in a structured field so a program can choose, and the adapter **must never choose for it**. | LB-19; arch §4; layer2 §8 ("Guessing a topic … never, at any layer") | error | A stub whose `final_text` is a menu yields `isError == false`, candidates parsed into a field, and no auto-selection. **no key** |
| MC-20 | **Conflict escalation is a successful result with an explicit discriminator, never an MCP error.** Any of the four tools whose scope touches a `status.conflict-review` file returns `{status: "escalation", files: [{path, review_note, agent_id}], …}` with `isError == false`. Not an error, because a well-behaved agent retries errors and a retried escalation is an escalation ignored; not prose, because the caller is a program that has to **stop**. The trigger is computed **deterministically** from `files_with_tag(snapshot, "status.conflict-review")` intersected with the participating topics' subtrees — not from what the model said it read, and not a model judgement. It self-clears when the human resolves the tag. | README Part 4; arch §6 L404-405; PK-5; RT-59; `pkb.core.files_with_tag` | error | Over a fixture holding one tagged note: each tool returns `isError == false`, `status == "escalation"` and the `review_note` verbatim; clearing the tag stops it; a sibling topic is unaffected. **no key** |
| MC-21 | **MCP never resolves a conflict.** Clearing `status.conflict-review` → `status.approved` is gated (RT-26), so on a propose-only call it becomes a `PendingProposal` with `reason == "conflict-resolution"` and nothing changes on disk. **Adding** the flag stays ungated so a background scan is never blocked on a human — that asymmetry is deliberate and must survive into the transport. | RT-26; README §1.7, Part 4 | error | A propose-only run whose only action clears a conflict produces one proposal with that reason and the file still carries the tag. **no key** |
| MC-22 | Every MCP result is JSON-serializable from frozen dataclasses of primitives and carries **no harness object** — no LangChain message, no `Interrupt`, no `Command`, no `CompiledStateGraph`. Falls out of `AgentEvent` and `pkb.contracts` already being that shape; the new pack and proposal types must keep it. | RT-43; `contracts.py` module docstring; I2 | error | `json.dumps(dataclasses.asdict(x))` succeeds for every result type of all four tools, including packs and escalations. **no key** |
| MC-23 | MCP-originated runs get **no exemptions**: the active-run registry (`thread_busy`), the pending-approval refusal, the global KB write lock and the fan-out cap all apply. Additionally the daemon **bounds concurrent MCP-originated runs**, because an external agent issues calls faster than a human and the deployment allows three concurrent cloud models. | RT-39, RT-45, RT-51, LB-15; Q6 | warning | Two concurrent `pkb_ask` on one thread: the second is `thread_busy`; N concurrent on distinct threads never exceed the configured cap. **no key** |

---

### 1.6 Layer 3's tables — ST

| ID | Rule | Source | Sev | Test assertion (live model?) |
|----|------|--------|-----|------------------------------|
| ST-1 | Layer 3's tables live in the checkpointer's SQLite file (RT-4, verified `journal_mode == 'wal'`) on **Layer 3's own `aiosqlite` connection**, never the saver's — the saver pins itself to its creating loop and handing its connection out breaks that. `PkbRuntime` exposes `db_path` for exactly this. | RT-4; P-5 (verified) | error | A `threads` write during a live run against the same file succeeds; the store never touches `runtime.checkpointer`. **no key** |
| ST-2 | **Use `aiosqlite`, not `sqlite3` on `asyncio.to_thread`.** Measured: **300 concurrent upserts on one shared aiosqlite connection during a live run — elapsed 1.24 s, rows=300, failures=0**, because `aiosqlite 0.22.1` serializes access internally. Plain `sqlite3` also works but needs `check_same_thread=False` **plus your own `asyncio.Lock`**, which is re-implementing aiosqlite. It is already a transitive dependency via `langgraph-checkpoint-sqlite`, so it costs nothing. | P-5 (verified) | error | 300 unsynchronised coroutine upserts during a live run: zero failures, no lock of our own. **no key** |
| ST-3 | **Never hold a write transaction open across an `await`.** WAL has exactly one writer; measured, a Layer 3 handler doing `BEGIN IMMEDIATE` → `await` → `COMMIT` killed the concurrent checkpointer run with `OperationalError: database is locked after 16.09s`, which surfaces to the user as a failed run with a written file and no flush. Short autocommit statements, always. This is the whole answer to "what breaks under concurrent access". | P-5 (verified) | **error** | A handler that awaits inside a transaction is caught by a lint/AST check; a concurrency test asserts a live run is never locked out. **no key** |
| ST-4 | Keep the **default 5000 ms `busy_timeout`** and wrap Layer 3 writes in a small retry. Measured at 1 ms: `ok=573 locked=2`. Do not lower it. | P-5 (verified) | warning | A hammering test at the default timeout has zero `database is locked` failures. **no key** |
| ST-5 | `threads` columns are exactly arch §5's seven — `thread_id` TEXT PRIMARY KEY, `agent_id` NOT NULL, `title` **NULL** (null until the titling call lands, TT-1 — an empty string is a different state and a client cannot tell "not titled yet" from "titled empty" if the two are collapsed), `created_at` NOT NULL (ISO-8601 UTC), `updated_at` NOT NULL, `origin_channel` NOT NULL, `pending_interrupt_id` NULL — **and nothing else**. In particular there is no `parent_thread_id` and no `kind`: both are pure functions of the id (LB-14), and storing what is derivable is the class of duplication RT-36 rejects. | arch §5 L325-327; RT-36; LB-14; TT-1; decision D | warning | Schema introspection asserts the exact column set and that `title` is nullable; a fresh row round-trips `title is None`; child lookup uses the derivation. **no key** |
| ST-6 | `Thread` (the dataclass, not the row) exposes `parent_thread_id: str \| None` and `kind: "user" \| "routed"` as **computed** fields, so a client distinguishes a conversation held directly with an expert from the work the Librarian routed to it **without string-sniffing in the UI**. Under RO-7's per-expert grouping the two sit in the same list, so the distinction has to be a field. | arch §4 L256-260; LB-9, LB-14; Q2; decision D | warning | `Thread.parent_thread_id` is None for a minted thread and equals the parent for a derived one. **no key** |
| ST-7 | The table name `threads` is free and must **stay** free: the file already holds `checkpoints` and `writes` (the checkpointer), `store`, `store_vectors`, `store_migrations`, `vector_migrations` (`AsyncSqliteStore`), and `scan_queue` (Layer 2). Layer 3 owns `threads` and anything it prefixes `pkb_`; it never writes to, migrates, or `DROP`s a table it does not own. | RT-4; verified langgraph schemas; `scans.py::_TABLE` (built) | error | After startup, `sqlite_master` holds all seven foreign tables untouched plus Layer 3's; grep asserts no `DROP`/`ALTER` against a non-owned name. **no key** |
| ST-8 | Migrations use Layer 3's **own** version table `pkb_service_migrations(version INTEGER PRIMARY KEY)` — mirroring the store's `store_migrations` precedent — applied additively as `ALTER TABLE … ADD COLUMN` with defaults. **`PRAGMA user_version` is verified unused** by the checkpointer, the store and the scan queue today, and that is exactly why it must not be claimed: it is one counter per *file*, shared by four writers, and taking a global for a per-table concern is the mistake `checkpoint_ns` was. | verified (no `user_version` anywhere in the installed langgraph tree); `store/sqlite/base.py:1073-1090`; D-6 | warning | Open a v1 file with a v2 build: the column is added, rows survive, `PRAGMA user_version` is still 0. **no key** |
| ST-9 | Every `threads` write is **idempotent and safe to repeat after a crash**: `INSERT OR IGNORE` for registration, targeted `UPDATE` for `title`/`updated_at`/`pending_interrupt_id`. There is **no transaction spanning a run** — the row is written from event callbacks while the stream is live, and a client disconnecting mid-run must not roll back the row that records the pending approval. | arch §8 L502; RT-38; ST-3 | warning | Kill the stream consumer mid-run: the row still carries the interrupt id and `get_thread` resolves it. **no key** |
| ST-10 | `updated_at` is bumped on every `InterruptEvent`, `RunEnd` and `RunError` for that thread. `pending_interrupt_id` is set to `request.interrupt_id` on every `InterruptEvent` — **on the row of `request.thread_id`, which may be a derived thread, not the thread the client is streaming** (LB-16) — and cleared on the first terminal event for a run on that thread that is not followed by a new interrupt. | arch §5 L326; LB-16, RT-41 | error | A gated write inside a routed expert sets the column on `<t>::topic/cooking` and the Librarian's row stays NULL. **no key** |
| ST-11 | An `InterruptEvent` naming a thread with **no row** causes the service to create one (agent from SV-9's shape rule) rather than dropping it. A pending approval no channel can list is the one failure arch §8 promises cannot happen, and it is reachable: the fan-out gives an expert a derived thread whose row registration is a separate step that can be missed. | arch §8 L502; LB-14, LB-16 | error | Delete the derived row mid-run, then let the expert interrupt: the row reappears and `list_threads` shows the badge. **no key** |
| ST-12 | Derived rows are registered **as the fan-out happens**, on a `SubagentStart` seen during a run on a Librarian thread whose delegate resolves in the catalog — not lazily. The catalog check is what distinguishes a routed expert from an expert's own `general-purpose` delegation (RT-44), which runs in a nested namespace under the *same* thread and must get **no** row. | LB-15, LB-14, RT-44; `contracts.SubagentStart` | error | A Librarian fan-out registers one row per expert; an expert's `task` to `general-purpose` registers none. **no key** |
| ST-13 | `origin_channel` is drawn from a **closed set defined once in `pkb.contracts`**: `Literal["tui","telegram","mcp","http"]`. It records where the conversation **started**, is set once at creation, is never updated when another channel continues it, and a derived thread inherits its parent's value. v1 keeps no "last seen channel" column because nothing consumes it. Every adapter stamps the same word or the column is ethnography rather than data. | arch §5 L325-327; D3, D9 | warning | The type is a `Literal`; each adapter's create path asserts its stamped value; resuming a `tui` thread from Telegram leaves it `tui`. **no key** |
| ST-14 | `pkb_proposals(proposal_id TEXT PRIMARY KEY, agent_id, thread_id, tool, args_json, description, allowed_decisions_json, reason, created_at, status, resolved_at)` is written by the `proposal_sink` (AP-15) and read by `list_proposals`. Same argument as `threads`: Layer 2 must not grow a table for it (RT-49) and the checkpointer cannot answer the question. **Nothing in v1 flips `status` to applied** — see Q3. | RT-42, RT-49; `RuntimeConfig.proposal_sink` (built); decision F | error | Record a proposal, close the runtime, reopen over the same file: `list_proposals()` still returns it. **no key** |

---

### 1.7 `pkb.packs` — PK

*Amends Layer 2's Q10: assembly moves below the seam (decision G). Ids continue Layer 2's `PK` series.*

| ID | Rule | Source | Sev | Test assertion (live model?) |
|----|------|--------|-----|------------------------------|
| PK-7 | Pack **assembly** lives in `pkb/packs.py`, a leaf importing only `pkb.core` and `pkb.contracts`; the pack **types** (`Pack`, `PackEntry`, `PackOmission`, `Escalation`) live in `pkb.contracts` because they must cross the seam (I2). Topic **selection by classification** — the one model call — stays in Layer 2. Leaving assembly in `pkb.agents` means the only way to test a golden pack is to stand up a runtime with a checkpointer and a chat model. | Q10 (amended); I2; PK-3; arch §9; decision G | warning | `python -c "import pkb.packs, sys; assert not {'deepagents','langgraph','langchain'} & set(sys.modules)"`; a golden pack test constructs no runtime. **no key** |
| PK-8 | **Pack assembly runs no model.** `pkb_implementation_pack` makes zero graph runs; `pkb_research_pack` makes at most one — the classification that selects topics — and **none** when the caller supplies `topics` explicitly. Making assembly a model call loses reproducibility and makes "`notes/summary.md` always first" unverifiable. | PK-3, PK-4, PK-6; arch §9 | error | Both packs built against a runtime whose chat model is a `ScriptedChatModel` with an **empty** script: the implementation pack succeeds and the model is never called; the research pack with explicit `topics` likewise. **no key** |
| PK-9 | `pkb_research_pack(query, topics=None, include_index=False, budget_bytes=None)`: when `topics` is given, classification is skipped entirely. Ordering is fixed and golden-tested: the tag-subtree block first, then per topic in snapshot order — `topic.md`, then **`notes/summary.md` before `references/summary.md`** (human-approved experience precedes static knowledge, §1.7's general rule), then conflict-review notes. **No `index.md`** unless `include_index=True`. | README Part 4; PK-1; README §1.7 | error | Golden test over the fixture KB: the ordered path list matches byte-for-byte; `include_index=True` adds exactly the topic indexes. **no key** (topic *selection quality* is **live**) |
| PK-10 | `pkb_implementation_pack(topic, include_subtopics=False, budget_bytes=None)`: **`notes/summary.md` first, always**, then the selected topic's full `index.md`, then `references/<src>/<src>.md` depth files, then `type.solution` notes. The ordering is the rule, not an implementation detail — human rules have the highest priority and an implementation agent reads top-down. `include_subtopics` defaults **False**: README says "the full `index.md` of the **selected** topic", and a four-sub-topic tree would otherwise return an order of magnitude more than was asked for, which is the context-window problem README §1.8 rule 2 and Part 4 exist to bound. | README Part 4; §1.3 L101; PK-2; §1.8 rule 2 | error | `pack.entries[0].path.endswith("notes/summary.md")` for every fixture topic, including one whose file is an empty placeholder. **no key** |
| PK-11 | A pack carries a **size budget** and truncates deterministically **at an entry boundary, never mid-file**, reporting exactly which entries were omitted and why. Packs are the one MCP result that can be arbitrarily large — a topic with forty ingested references produces an implementation pack no context window holds — and a silently clipped pack is worse than a short one because the consumer cannot tell. | README §1.8 rule 2, Part 4; layer1 GE-12's precedent | warning | With a budget of N bytes over an oversized fixture: entries are a prefix of the unbudgeted ordering, every entry is whole, and `omitted` names the rest with a reason. **no key** |
| PK-12 | Pack membership is computed from Layer 1's derived surface — `pkb.core.scan`, `files_with_tag`, `build_tag_tree`, the generated indexes — **never** from a second tree walk and never from a cached copy of the tree held by a transport. Assembly is **read-only**: it writes nothing, tags nothing, bumps no timestamp, produces no `ScanRequest`. | PK-3, PK-4; RG-2; layer1 MA-3 | error | grep: no `os.walk`/`rglob`/`glob` over `kb_root` outside `pkb.core`; hash every file before and after building both pack kinds — byte-identical, mtimes unchanged, injected scan queue empty. **no key** |

---

## 2. Where the architecture is wrong about the packages

Rows **P-1 … P-5** were **executed** against a real resolution of the Layer 3 dependency set on this
repo's `pyproject.toml` + `uv.lock`. `uv add --no-sync fastapi uvicorn sse-starlette mcp` resolved
**101 packages in 371 ms with zero existing pins moved** (`pydantic` stays 2.13.4 — there is no
langchain/pydantic conflict), and the full existing suite passed unchanged: **1155 passed in 66.55 s**.

| # | Doc says | Installed packages actually do | Corrected approach |
|---|----------|-------------------------------|--------------------|
| **P-1** | — (not in the arch doc) | **`httpx` is now 2.x under a new distribution name, `httpx2`.** `mcp 2.0.0` requires `httpx2>=2.5.0` and `starlette.testclient` does `import httpx2 as httpx` with a deprecation warning for plain `httpx`. Both distributions install side by side and do **not** collide — `httpx` ships module `httpx`, `httpx2` ships module `httpx2` (verified via `importlib.metadata`). | Two HTTP clients in one venv is **intentional**, not an accident to be cleaned up. `pkb.agents.models`' `httpx.ConnectError` failover predicate (D-21) keeps working untouched; Layer 3's tests and clients bind `httpx2`. **Do not add `httpx-sse`**: `httpx2` ships SSE natively (`httpx2.EventSource`, `ServerSentEvent`, `SSEError`, derived from httpx-sse, MIT) and decoding works straight off `client.stream(...)`. (SS-1, and the TUI's transport in step 4.) |
| **P-2** | arch §7's correction and Layer 2 decision D: the runtime's `try/finally` delivers *"exactly one flush per run on both paths"*. | **False across an SSE client hangup.** `asyncio.Task.cancel()` is edge-triggered; an anyio cancel scope is level-triggered, so every await inside a `finally` raises again. Generic proof: `/naive -> ['finally-entered','flush-raised:CancelledError']`, `/detached -> ['finally-entered','FLUSHED']`. Then against the **real `PkbRuntime`**, real scripted model, real KB, real uvicorn, real socket close: `StreamingResponse` and `EventSourceResponse` both gave `flush reports with stamps: []` / `index.md lists the note: False`; a `StreamingResponse` **over an `asyncio.Task` pump** gave `stamped: [['Cooking/notes/reverse-sear.md']]` / `True`. Invisible to Layer 2's own tests: `rt.cancel(run_id)` and a plain `Task.cancel()` both flush correctly — only the anyio path loses it. `_drive`'s exit chain (`runtime.py:1312-1319`) has no shield, and `_flush_pending` swallows `except Exception`, which `CancelledError` is not. | **Never hand `rt.run(...)` straight to an SSE response.** Drive it in a plain `asyncio.Task` pushing into an `asyncio.Queue`; the SSE generator reads the queue and, in its `finally`, issues exactly one `task.cancel()` (AP-6). Combined with decision A (detach, don't cancel) the common path never cancels at all. Arch §8's "Client disconnects mid-approval" row and README Part 4 both depend on this. RT-7's startup `regenerate_all` limits the blast radius to "stale until the next restart" — but that is not the guarantee the docs claim.  **⚠ NOT REPRODUCED — see the correction note in §2.** |
| **P-3** | arch §6: *"Mounted at `/mcp` on the same FastAPI app."* | Four separate problems. **(a)** `app.mount("/mcp", …)` makes the sub-app's router redirect `/mcp` → `/mcp/` with a **307**; the SDK client follows it, a stricter one may not, and arch §6's URL is `/mcp` exactly. **(b)** `Mount` **does not run the sub-app's lifespan**, and `streamable_http_app()` returns `Starlette(..., lifespan=lambda app: session_manager.run())` — mounting throws that away and nothing serves. **(c)** `session_manager.run()` raises `RuntimeError: … can only be called once per instance`. **(d)** `streamable_http_app(host=…)` defaults to `127.0.0.1`, auto-enabling `allowed_hosts`; measured `base=http://testserver → 421 'Invalid Host header'`. | (a) Append a bare `Route("/mcp", StreamableHTTPASGIApp(mcp_server.session_manager), methods=["GET","POST","DELETE"])` to the parent, calling `streamable_http_app(streamable_http_path="/mcp", host=…)` for its side effect — verified: exact URL, no redirect, no private attributes. (b) The daemon lifespan does `async with mcp_server.session_manager.run(): yield`. (c) **App factory**, not a module-level `app` — a module-level one cannot be entered twice, which every lifespan test does. (d) Keep lockdown on (the daemon binds localhost) and pin the test client to `base_url="http://127.0.0.1"`. (MC-2, MC-3, MC-4, AP-1.) |
| **P-4** | arch §9: *"`pkb.server` — FastAPI `TestClient` against a stub `PkbService`"*. | **`TestClient` cannot test SSE.** `_TestClientTransport.handle_request` does `raw_kwargs["stream"] = httpx.ByteStream(raw_kwargs["stream"].read())`. Measured: `client.stream("GET", "/runs/plain?n=50&delay=0.01")` returned **all 50 frames in one chunk**, and breaking after chunk 1 left the server generator running to completion — no disconnect, no incrementality. `httpx2.ASGITransport` buffers identically (four frames at 0.1 s arrived as one chunk at t=0.41), and so does httpx 0.28's. | **Three tools, one job each** (§6): `TestClient` for non-streaming routes *and* SSE frame **content** (split the concatenated body on `\r\n\r\n`); `httpx2.ASGITransport` + `streamable_http_client(url, http_client=…)` for the MCP mount end-to-end with no socket; and a ~40-line **raw ASGI driver** (`spec_version "2.3"`, `create_task(app(scope, receive, send))`, `{"type":"http.disconnect"}` from `receive`) — the only in-process way to assert frames arrive over time and that the generator's `finally` runs on disconnect. Proven as a real pytest file under this repo's own `pytest-asyncio 1.4`: **4 passed**, no `anyio` plugin, no network, no key. |
| **P-5** | RT-4: *"The checkpointer file is opened WAL … so Layer 3's `threads` table may live in the same file."* | **True, but conditional on ordering, and RT-4 does not say so.** `from_conn_string` is a bare `aiosqlite.connect`; the WAL pragma lives in `setup()`. Measured: `right after from_conn_string(): delete` → `a Layer-3 connection sees: delete` → `after await saver.setup(): wal`. Under WAL, sharing works cleanly (checkpoint rows grew 2→8 while 30 `threads` commits landed with no errors). What breaks is holding a write transaction across an await: `OperationalError: database is locked after 16.09s`. `aiosqlite` beats `sqlite3`-on-a-thread: 300 concurrent upserts on one shared connection during a live run, elapsed 1.24 s, zero failures, no lock of our own. | **Open Layer 3's connection after `PkbRuntime.open()`** (AP-4). Use `aiosqlite` on Layer 3's **own** connection, never the saver's (ST-1, ST-2). **Never hold a write transaction across an await** (ST-3) — that is the whole answer to "what breaks under concurrent access". Keep the default 5000 ms `busy_timeout` and retry (ST-4). |
| **P-6** | arch §5's `PkbService` Protocol: seven methods, `stream_run(thread_id, message)`, `resume(thread_id, decisions)`, `AgentInfo`. | Five things it cannot express and one wrong name. It has no `approval_mode` (so MCP cannot get propose-only without importing the harness), no `interrupt_id` (so RT-40's staleness refusal is unreachable), no proposal surface, no scan entry point, no `agent_id` on the Layer 2 calls that require it, and `AgentInfo` would make `pkb.agents` import a transport (C13). | Widen the Protocol and record the divergence (SV-1, SV-5, SV-15, SV-17, SV-20). The Protocol's whole purpose is that everything above Layer 2 tests against a stub; an extra that lives only on the concrete class is an extra the stub cannot fake, which pushes the MCP adapter and the scan worker into the live suite. `AgentInfo` is retired in favour of `pkb.contracts.AgentDescriptor` (already built). |
| **P-7** | arch §5's `Event` table implies `run.end` means the run finished. | `events.stream_events` yields `run_end()` after the astream loop **whether or not the graph interrupted** (`events.py:473`), because the harness's `astream` returns normally on an interrupt. And a **cancelled** run yields no terminal event at all — `except (asyncio.CancelledError, GeneratorExit): raise` (`events.py:464`). | `run.end` carries a Layer-3-computed `status` envelope field (SS-9), and the supervisor synthesizes `run.error code=cancelled` (AP-11). Both are transport frames; the `RunEnd`/`RunError` dataclasses are unmodified. |
| **P-8** | D-20 / arch §9: I2 is enforced by an import-linter rule. | The current `forbidden` contract lists **only** `pkb.contracts`, and the `layers` contract permits a higher layer to import a lower one — so `pkb.server` importing `pkb.agents` (and transitively `deepagents`) passes today. Verified: extending `source_modules` to `["pkb.contracts","pkb.service","pkb.server"]` gives *Analyzed 84 files, 417 dependencies. Contracts: 3 kept, 0 broken* and **catches a planted transitive violation**: `pkb.server is not allowed to import langgraph: pkb.server.app -> pkb.agents.runtime (l.10) / pkb.agents.runtime -> langgraph`. The existing `layers` contract needs no edit. | Two contracts (§5.4): a **strict** one (no `allow_indirect_imports`) over `pkb.contracts`, `pkb.server`, `pkb.tui`, `pkb.clients` — which is why decision B keeps `pkb.server` free of `pkb.agents` entirely — and a **direct-only** one (`allow_indirect_imports = true`, verified supported at `importlinter/contracts/forbidden.py:72`) over `pkb.service`, the composition root. `pkb.clients` must also be added to the `layers` contract, where it is currently absent. |
| **P-9** | arch §3's package layout has `service.py` as a module and `app.py` as *"FastAPI; owns the daemon lifecycle"*. | Neither survives contact with P-3c (one-shot session manager forces a factory) or decision B (something must construct `PkbRuntime`, and it cannot be reachable from `pkb.server`). | `pkb/service/` becomes a package with one named harness-touching module (decision C); `create_app(open_service, *, config)` still owns the lifecycle — it just receives the factory rather than importing it (AP-1, AP-2). |

---


> ### ⚠ Correction to P-2 — it did not reproduce
>
> **Checked independently on 2026-08-07 and could not be reproduced**, in four setups of increasing
> fidelity: a bare `asyncio.Task` cancel; an `anyio.move_on_after` scope; the same with the cancel
> arriving while suspended *inside* the `async for`; and finally **a live `uvicorn` server serving an
> `EventSourceResponse` that iterates `PkbRuntime.run`, with a real client hanging up mid-stream**.
> In every arm the note was on disk *and* the topic index listed it — the flush completed.
>
> Treat the claim as **unverified**. No rule may cite it as evidence.
>
> **Decision A still stands, on a better argument.** It was justified partly by P-2; it does not need
> it. D2 and D3 say a turn outlives the client that started it — that is the entire point of a daemon
> with shared threads. An ingestion turn that dies because a phone crossed a tunnel breaks that
> promise whether or not the flush survives, and §8 already promises that any client can resolve a
> pending approval later. Decision A delivers that; the flush question is incidental.
>
> **P-4 and P-1 were independently confirmed** — `TestClient` served 50 of 50 frames before the client
> saw the first, and `starlette.testclient` emits a deprecation warning pointing at `httpx2`.
> **P-3 has not been independently checked**; treat it as reported rather than verified until someone
> mounts an MCP server and calls a tool through it.
>
> The lesson worth keeping: one of four "executed" findings did not survive an independent attempt.
> A grounding pass beats recollection and is still not authority — re-run the ones a decision rests on.


## 3. Contradictions between README, the architecture and the Layer 2 rules

| # | Contradiction | Resolution | Why |
|---|---------------|------------|-----|
| **C-1** | **Where does the thread-id derivation live?** `expert_thread_id`, `librarian_thread_id`, `EXPERT_THREAD_SEPARATOR` are in `pkb.agents.routing`; `SCAN_THREAD_PREFIX` is in `pkb.agents.scans` — both harness-importing modules `pkb.tui` and `pkb.clients` may never import. Yet arch §5 says *"Layer 3 must recognize both"* shapes, and SS-10/ST-12 require Layer 3 to **produce** one. | **Move all four into `pkb.contracts` and re-export from `pkb.agents.routing`/`scans`, identity-tested** (`pkb.agents.routing.expert_thread_id is pkb.contracts.expert_thread_id`). | Exactly the precedent `validate_decisions` set: the one copy sits in the seam because both sides must answer identically and the transports cannot reach `pkb.agents`. A second implementation of an id convention is the mistake CX-8/RG-11 exist to prevent, and it is the kind that fails silently — a thread resolving to the wrong agent shares a checkpoint (D-6). |
| **C-2** | **Is `pkb.service` bound by I2?** The prompt says the contract *"must be extended to `pkb.server`/`pkb.service`"*, but something must construct `PkbRuntime`; D-20 names only `pkb.server`, `pkb.tui`, `pkb.clients`. | **`pkb.service` is the composition root and gets its own direct-only contract** (P-8); `pkb.server` gets the strict one and never imports `pkb.agents` at all (decision B, C). | A strict contract over `pkb.service` is unimplementable — some module must call `PkbRuntime.open` and a transitive chain then exists wherever it lives. A blanket `allow_indirect_imports` over `pkb.server` would keep the contract green while deleting the check the grounding proved works. The real proof of I2 stays the subprocess ban (SV-30); the linter is the cheap continuous half. |
| **C-3** | **`AgentInfo` vs `AgentDescriptor`.** Arch §5 names `AgentInfo` in `pkb.service`; the registry produces it, which would make `pkb.agents` import a transport. | **`AgentInfo` is retired**; `GET /agents` returns `pkb.contracts.AgentDescriptor` verbatim (RO-4). | Already resolved at Layer 2 (C13, RG-14) and already built. Recorded here so a reader of arch §5 does not reintroduce it. |
| **C-4** | **Who owns the conflict-scan worker?** Arch §7 puts the dequeue loop *"in the daemon"*; the thing it runs is a deepagents graph, which I2 forbids Layer 3 from touching. | **Layer 3 owns the timer and the dequeue loop over the harness-free `ScanQueue` Protocol; `service.run_scan` forwards to `runtime.request_scan`** (AP-14, SV-21). | Layer 2's C12, unchanged. Anything else either breaks I2 outright or puts a background-task lifecycle into a layer whose suite runs against a fake chat model with no scheduler. |
| **C-5** | **Do routed expert threads appear in the thread list?** Arch §5 says *"a routed thread belongs in the human's thread list as a child of the Librarian's"*; RT-58 says a scan thread belongs out of it. | **Grouped per expert: a derived thread appears under the expert that ran it, with a pending approval badged and sorted first** (RO-7, RO-8, Q2 RULED 2026-08-07). Arch §5's "child of the Librarian's" is superseded — the parentage stays derivable and `children` becomes provenance. | Navigation is by subject: "what have I been doing with Cooking" is the question a human asks, and hiding the routed work under the turn that spawned it buries it. Hiding it entirely would make "continue with the Cooking expert" a dead end once the merged reply scrolls away. The badge is what keeps "approve from a phone something the TUI asked about hours earlier" true when the approval was raised **inside** a fan-out (LB-16). |
| **C-6** | **Does deleting a thread cascade?** RT-48 specifies the checkpoint cascade to `<parent>::*`; arch §6 says only `DELETE /threads/{id}`. | **The SQL cascade mirrors the checkpoint cascade exactly, in that order** (SV-24), and deleting a derived thread reaches neither sideways nor upwards. | Q15(a). If the SQL cascade did not mirror it, the table would keep rows pointing at erased checkpoints and the list would offer conversations that open empty. |
| **C-7** | **Does the propose-only path widen the gate set for external callers?** README Part 4 says *"Knowledge Base commits require explicit human approval"* in the one section about project agents; the approved gate table (Q5, RT-31) leaves plain notes and reference depth files ungated — so today an MCP call files content with no human involved and no undo. | **Same gate table for every channel** (MC-18), with the compensating control — MCP threads listed and labelled, proposals visible — **built in step 3 rather than deferred** (MC-13, ST-14). | README §2.1 is explicit that the standards hold *"no matter which channel a request arrives from"*, the Project Manager is a local trusted component, and gating every write turns a retrospective's ten notes into ten clicks — the friction goal 2 exists to remove. But it is the human's call: see Q4. |
| **C-8** | **Does `pkb.contracts` grow `UnknownThreadError`?** Layer 3 owns the thread table so it owns the 404, but `pkb.clients` and `pkb.tui` must catch it without importing `pkb.service` — and the existing stub's `raise UnknownAgentError(thread_id)` reaches the wire as "no such agent" when the agent is fine. | **Add `UnknownThreadError(PkbAgentError)` to `pkb.contracts.__all__`** (RO-20). | Mirrors why `validate_decisions` lives in the seam: `pkb.contracts` is where a type both sides must name lives, and the typed-error block is explicitly *"the errors Layer 3 maps to status codes"*. |
| **C-9** | **Where do context packs live?** Layer 2's Q10 answered `pkb/agents/packs.py` when the consumer was a Topic Expert; MCP is now the consumer and the pack types must cross the seam. | **Assembly moves to `pkb/packs.py`, a leaf; types to `pkb.contracts`; selection-by-classification stays in `pkb.agents`** (PK-7, decision G). | Three things changed since Q10: the types are going into `pkb.contracts` under any option (I2); PK-3/PK-4 make assembly a pure function of `pkb.core`'s derived surface; and PK-1/PK-2 are golden-file tests that belong in the free-and-fast profile. Q10's rejection of building packs *in the transport* stands unchanged — that would duplicate `pkb.core` selection logic a fourth adapter would duplicate again. |
| **C-10** | **`SubagentStart`/`SubagentEnd` carry no `thread_id`**, so the only structured way Layer 3 learns a derived thread exists is to re-derive it — and it must first distinguish a routed expert from an expert's own `general-purpose` delegation, which shares the parent's thread. | **Derive in Layer 3, gated on "the delegate resolves in the catalog"** (SS-10, ST-12). Adding `thread_id` to the event dataclasses is the cleaner fix and is Q5. | The catalog gate is total and deterministic: `general-purpose` is not a catalog id, so it never gets a derived thread. Parsing the merged reply — which *does* name each thread — is disqualified outright: it makes `merge_reply`'s rendering format load-bearing and LB-18's golden test would be pinning a wire protocol. |
| **C-11** | **Arch §9's testing row for `pkb.server` is `TestClient`**; P-4 proves it cannot test the thing that matters most. | **Three tools, and uvicorn-in-a-task only for the handful of tests that must prove real-socket behaviour** (§6). | The arch doc's *intent* — the whole server suite runs free, fast, keyless and networkless — is preserved exactly and is achievable; only the single-tool claim is wrong. |

---

## 4. Open questions for the human

Ranked by blast radius. **Every one has a default already encoded in §1**, so implementation is not blocked.

| # | Question | Options | Recommended default | Blast radius if changed later |
|---|----------|---------|---------------------|-------------------------------|
| **Q1** | **Does a dropped SSE connection cancel the run or detach from it?** | (a) disconnect cancels — iterate Layer 2's generator from the response handler; (b) **detach** — a daemon-owned run task publishing into a per-run hub, with `GET /threads/{id}/events` to reattach; (c) (b) plus a replay buffer so a reconnect resumes from `seq` with no gap. | **(b), with (c)'s buffer scoped to the run in flight** (decision A, AP-6 … AP-9, RO-17). (a) is disqualified because it makes D2's headline promise conditional on a stable socket — a phone in a tunnel would kill an ingestion turn mid-write — while quietly turning `cancel(run_id)` into something the network does rather than something the human does. (Its second argument used to be P-2's lost flush; that did not reproduce — §2 — and the first argument settles it alone.) | One hub module (~150 lines) plus the discipline that a slow subscriber is dropped rather than allowed to stall the run (AP-8). Changing it later changes every route that streams. **This is the largest structural decision in step 3.** |
| **Q2** | **Do routed expert threads appear in the human's thread list, and where?** (Layer 2's Q14 asks the same question and is settled by this ruling.) | (a) children of the Librarian thread, excluded from the flat and per-expert lists; (b) flat, alongside the human's own expert conversations; (c) hidden until a reply offers one. | **RULED 2026-08-07: grouped per expert** (RO-7, RO-8). The list is organised by agent — a derived `<thread>::<agent-id>` appears under the expert that ran it, not under the Librarian thread that spawned it, because navigation is by subject. (a) was the pre-ruling default and is retired; (c) makes "continue with the Cooking expert" a dead end the moment the reply scrolls away. The "fragments with no visible parent" objection to a flat list is answered rather than dodged: `Thread.kind`/`parent_thread_id` (ST-6) mark a routed thread and name its parent, and a pending approval is badged and sorted first (RO-8) so arch §8's headline case survives. | One `WHERE` clause and one children query. Cheap to change, but it is the difference between the offer being a link and being a sentence. |
| **Q3** | **What does approving a `PendingProposal` actually do?** RT-42 already auto-answered the gate with `reject`, the run completed, and there is no interrupt left to resume. | (a) a new Layer 2 `apply_proposal(id)` replaying the stored bytes through `validate_content` → write → `flush` → scan-enqueue under the write lock (needs an RT-18 amendment); (b) re-prompt the agent ("the human approved your write; perform it now"); (c) change RT-42 so propose-only **parks** the interrupt and approval is an ordinary `resume`; (d) v1 records and lists only — no apply. | **(d) for step 3, (a) as the design for step 4/5** (decision F, ST-14, RO-19). Persisting, listing and dismissing is what step 3 owes; *applying* is a Layer 2 entry point and belongs in a Layer 2 amendment, not smuggled into a transport. Reject (b) outright: the human approved *specific content*, and handing the same prompt back to a model produces something else — the exact failure LB-18 was built around. (c) is genuinely cheaper and reuses everything, but it contradicts a built and tested rule ("a propose-only run emits zero interrupt events"), leaves `pkb_ask` with no answer for a parked turn, and an abandoned proposal parks the thread forever under RT-39. | (d)→(a) is additive: one Layer 2 function, one `status` transition, one route verb. **But README Part 4's two most valuable feedback rows — Summary Update and Conflict Tag Update — stay unreachable from a project until it lands.** Worth deciding now because it is what the Project Manager integration is written against. |
| **Q4** | **How does a thread come by its title?** Nothing in the README, the arch spec or the Layer 2 rules says. | (a) client-supplied only; (b) model-written (a titling call); (c) client-supplied optional, with a deterministic derivation from the first human message when absent, written once. | **RULED 2026-08-07: (b) model-written** (TT-1…TT-4, SV-25…SV-28). (c)'s deterministic derivation is retired — it produces a sidebar full of "I grilled a ribeye last weeke…". The cost objections that argued against (b) are answered by the shape the ruling takes: TT-2 puts the call off the turn's critical path so it never competes for a cloud slot the human is waiting on (Q6), TT-3 spends it once per thread, and TT-4 keeps a human-set name permanent. (a) alone fails Telegram, where a message arrives before anyone could name anything (D9). Derived threads keep a generated, non-editable title (SV-28). | One function and one column write. The human notices it on every row of the sidebar, which is why it is here rather than buried.  **RULED 2026-08-07: (b) model-written.** A titling call names the thread; see TT-1…TT-4. |
| **Q5** | **Should `pkb.contracts` events carry `thread_id`?** Only `InterruptEvent` does today, so Layer 3 re-derives it for every fan-out frame. | (a) add `thread_id: str` to all nine event dataclasses; (b) add it to `SubagentStart`/`SubagentEnd` only; (c) leave Layer 2 alone and derive in Layer 3, gated on the catalog. | **(c) for step 3** (SS-10, C-10) — it is total, deterministic and needs no Layer 2 change mid-step. **(a) is the better long-term shape**: two lines in `pkb.contracts` plus the normalizer, it makes the event self-describing, and it removes Layer 3's need to know that `general-purpose` is not a catalog id — a piece of harness trivia I2 exists to keep out of transports. | (c)→(a) touches nine frozen dataclasses and Layer 2's event tests. Do it as a deliberate Layer 2 amendment, not as a side effect of step 3. |
| **Q6** | **Should a run report which files it filed?** `ToolStart`/`ToolEnd` carry a *rendered* summary by design (RT-34's sibling rule), and `FlushReport` reaches the daemon through `flush_sink` but is **not run-correlated**. So `pkb_ingest` cannot honestly tell an external agent which paths landed. | (a) omit `filed` from v1 MCP results and say so; (b) correlate `FlushReport` to a run by threading a run id through the flush sink (a Layer 2 change); (c) parse tool summaries. | **(a) for v1**, with the flush sink feeding `/health` and the log (AP-16). (c) is disqualified — a rendered summary is not a path, and parsing it is a second, driftable copy of something Layer 2 chose not to publish. (b) is the honest fix and is one field on `FlushReport` plus one on the sink signature. | One field in the MCP result schema. Worth the human's eye because "did my note land?" is the first question a Project Manager integration asks. |
| **Q7** | **Does anything make `/health` non-200, and what counts as `degraded`?** | (a) always 200 while serving; `degraded` when the Telegram task is not running while enabled, the scan worker is stopped with an error, or the runtime is closed; (b) 503 when degraded so a supervisor restarts the daemon; (c) split `/health` (liveness, always 200) and `/health/ready` (503 when degraded). | **(a)**, with (c) available later (AP-18). D9 is explicit that a crashed bot must not take the daemon down; a 503 invites exactly the restart D9 forbids and would kill in-flight runs and pending approvals that are perfectly healthy. | One field. Worth confirming because `degraded` is the word a future monitoring rule fires on. |
| **Q8** | **Does an implementation pack include sub-topics?** | (a) selected topic root only; (b) root plus all descendants; (c) an explicit flag. | **(c) defaulting to (a)** (PK-10). README says "the full `index.md` of the **selected** topic", and a `topic/cooking` request over a four-sub-topic tree would otherwise return an order of magnitude more than was asked for — the context-window problem README §1.8 rule 2 and Part 4 exist to bound. | One argument, and it interacts cleanly with the size budget (PK-11). |

---


### Rulings of 2026-08-07 (thread titles, thread grouping, Telegram shape)

**Titles are model-written (Q4).** Not client-supplied and not derived from the first line, both of
which produce a sidebar full of "I grilled a ribeye last weeke…".

| ID | Rule | Sev | Test assertion |
|----|------|-----|----------------|
| TT-1 | A thread is titled by a **model call** after its first assistant reply, not before: there is nothing to title until the exchange has content. Until then the thread has a null title and clients show a placeholder. | error | A thread created and never run has `title is None`; after one completed turn it has a non-empty title. **live** for quality, **no key** for the mechanism (scripted model). |
| TT-2 | Titling runs **off the turn's critical path** and its failure is never the turn's failure. The reply is delivered, the title arrives after. A titling error is logged and leaves the title null. | error | With a model that raises only on the titling call, the run still yields `RunEnd` and the thread still exists. **no key** |
| TT-3 | A thread is titled **once**. Later turns do not re-title it, so a name the human has learned to recognise does not move under them. Re-titling happens only on explicit request. | error | Two turns on one thread leave the title from the first unchanged. **no key** |
| TT-4 | A human-set title always wins and is never overwritten by a later titling call. | error | Renaming, then running another turn, leaves the human's title. **no key** |

**The thread list is grouped per expert (Q2).** `list_threads` is organised by `agent_id`; a derived
`<thread>::<agent-id>` appears under **the expert that ran it**, not under the Librarian thread that
spawned it. Navigation is by subject: "what have I been doing with Cooking" is the question a human
actually asks, and a flat list buries it. The parent is still derivable (`librarian_thread_id()`,
decision D) for a client that wants to show provenance.

**Telegram is a channel per expert (amends arch §6 and D9).** The architecture describes one bot with
`/connect cooking` switching targets. The ruling is instead: **one chat per agent** — a Cooking
channel talks to the Cooking expert, a Librarian channel routes. Consequences for step 5, recorded
here so Layer 3 does not design against the old shape:

| ID | Rule | Sev | Test assertion |
|----|------|-----|----------------|
| TG-1 | The daemon holds a **`chat_id` → `agent_id` mapping**; an inbound message is addressed to the agent its chat maps to. `/connect` is gone, and with it the "which expert am I talking to?" ambiguity that made a mis-sent note land in the wrong topic. | error | A message in a chat mapped to `topic/cooking` runs that expert; the same text in the Librarian chat routes. **no key** |
| TG-2 | A message from an **unmapped** chat is answered with instructions for mapping it, never routed to a default agent. Silently defaulting is how material lands in the wrong topic. | error | An unknown `chat_id` produces the mapping instructions and runs no agent. **no key** |
| TG-3 | Creating a topic does **not** create a Telegram channel. The mapping is human-configured; the daemon reports agents that have no chat so the human can add one. | warning | After `create_topic`, `/health` lists the new agent as unmapped. **no key** |
| TG-4 | `origin_channel` on a thread records the chat it came from, so a conversation started on the phone is recognisable in the TUI (D3's cross-channel resume). | info | A thread created from Telegram carries its chat's origin. **no key** |


## 5. The wire contract

This is what a TUI and a Telegram bot are written against. It is exact, not sketched.

### 5.1 Shared JSON shapes

```jsonc
// Thread — `kind` and `parent_thread_id` are COMPUTED from thread_id, never stored (ST-5, ST-6)
{
  "thread_id":            "3f0c9a12-8e64-4a1f-9b77-2c5d0a11e4d3",
  "agent_id":             "topic/cooking/grilling",
  "title":                "Searing a ribeye",     // null until the titling call lands (TT-1)
  "kind":                 "user",              // "user" | "routed"
  "parent_thread_id":     null,                // non-null only for "routed"
  "created_at":           "2026-08-07T09:12:44Z",
  "updated_at":           "2026-08-07T09:14:02Z",
  "origin_channel":       "tui",               // "tui" | "telegram" | "mcp" | "http"
  "pending_interrupt_id": null                 // an INDEX, not the authority (decision E)
}

// AgentDescriptor — verbatim from list_agents(), five fields, no more (RO-4)
{ "agent_id": "topic/cooking", "title": "Cooking", "description": "…",
  "has_custom_expert": true, "model_id": "ollama:deepseek-v4-flash:cloud" }

// MessageView — created_at is ALWAYS null from Layer 2 (RO-10)
{ "role": "human", "text": "…", "created_at": null }

// ApprovalRequest — actions are POSITIONALLY aligned with the decisions sent back (RT-41)
{ "interrupt_id": "int-7c1…", "agent_id": "topic/cooking",
  "thread_id": "3f0c…::topic/cooking",
  "actions": [ { "tool": "write_file",
                 "args": { "file_path": "/kb/Cooking/notes/summary.md", "content": "…" },
                 "description": "Cooking/notes/summary.md (exists)\n--- current\n+++ proposed\n@@ …",
                 "allowed_decisions": ["approve","edit","reject"],
                 "reason": "breadth-approval" } ] }

// Decision
{ "type": "approve", "message": null, "edited_args": null, "edited_tool": null }

// Error — application/problem+json, RFC 9457 (RO-21)
{ "type": "about:blank", "title": "Thread busy", "status": 409,
  "code": "thread_busy",
  "detail": "a run is already active on thread '3f0c…'; cancel it or wait for it to finish",
  "thread_id": "3f0c…" }
```

**Error codes** (one table, shared by HTTP and MCP — RO-20, MC-14):
`unknown_agent`→404 · `unknown_thread`→404 · `thread_busy`→409 · `approval_pending`→409 ·
`stale_interrupt`→409 · `invalid_decision`→400 · `validation_error`→400 · `internal`→500.

### 5.2 The routes

| # | Route | Request | Response |
|---|-------|---------|----------|
| 1 | `GET /agents` | — | `200 {"agents": [AgentDescriptor, …]}` — Librarian first, snapshot order |
| 2 | `GET /threads?agent_id=<exact>` | — | `200 {"threads": [Thread, …]}` — grouped per expert: a routed thread is listed under the expert that ran it (RO-7); `scan:` threads never; `pending DESC, updated_at DESC` |
| 3 | `POST /agents/{agent_id:path}/threads` | `{"title": str\|null, "origin_channel": OriginChannel\|null}` | `201 {"thread": Thread}` + `Location: /threads/<id>` · `404 unknown_agent` |
| 4 | `GET /threads/{thread_id:path}` | — | `200 {"thread": Thread, "messages": [MessageView], "pending_interrupt": ApprovalRequest\|null, "children": [Thread]}` — `children` is provenance (RO-7) · `404 unknown_thread` |
| 5 | `POST /threads/{thread_id:path}/runs` | `{"message": str}` | `200 text/event-stream` · `400 validation_error` · `404` · `409 thread_busy\|approval_pending` |
| 6 | `POST /threads/{thread_id:path}/interrupt` | `{"interrupt_id": str, "decisions": [Decision, …]}` | `200 text/event-stream` (the same run continues) · `400 invalid_decision` · `409 stale_interrupt` |
| 7 | `DELETE /threads/{thread_id:path}` | — | `204` (cascades derived rows + checkpoints) · `409 thread_busy` |
| 8 | `GET /health` | — | `200` always while serving (§5.5) |
| +1 | `GET /threads/{thread_id:path}/events` | — | `200 text/event-stream` replaying the run in flight from `seq 0` · `204` when idle |
| +2 | `PATCH /threads/{thread_id:path}` | `{"title": str}` | `200 {"thread": Thread}` · `409` on a derived thread |
| +3 | `DELETE /runs/{run_id}` | — | `204` (no-op for an unknown id) |
| +4 | `GET /proposals?status=pending` | — | `200 {"proposals": [PendingProposal, …]}` |
| +5 | `DELETE /proposals/{proposal_id}` | — | `204` |
| — | `GET\|POST\|DELETE /mcp` | JSON-RPC | streamable HTTP, bare `Route`, no redirect (MC-2) |

**Route registration order is load-bearing** (RO-3): `/threads/{tid:path}/runs`,
`/threads/{tid:path}/interrupt`, `/threads/{tid:path}/events` must all be registered **before**
`/threads/{tid:path}`, or the greedy converter swallows them.

### 5.3 SSE framing

```
id: 0
event: run.started
data: {"type":"run.started","seq":0,"run_id":"run-9f2","thread_id":"3f0c…","agent_id":"librarian"}

id: 1
event: subagent.start
data: {"type":"subagent.start","seq":1,"run_id":"run-9f2","thread_id":"3f0c…::topic/cooking","agent_id":"topic/cooking"}

id: 2
event: message.delta
data: {"type":"message.delta","seq":2,"run_id":"run-9f2","thread_id":"3f0c…::topic/cooking","agent_id":"topic/cooking","text":"Pull it at"}

id: 7
event: interrupt
data: {"type":"interrupt","seq":7,"run_id":"run-9f2","thread_id":"3f0c…::topic/cooking","agent_id":"topic/cooking","request":{…ApprovalRequest…}}

: ping - 2026-08-07T19:55:44Z

id: 9
event: run.end
data: {"type":"run.end","seq":9,"run_id":"run-9f2","thread_id":"3f0c…","agent_id":"librarian","final_text":"…merged, attributed…","status":"interrupted"}
```

- **Envelope**: `type`, `seq`, `run_id`, `thread_id`, `agent_id`, flat-merged with
  `dataclasses.asdict(event)`. A test asserts no envelope key collides with any dataclass field
  across the union (SS-4).
- **Names**: arch §5's nine verbatim, plus `run.started` (transport) and `: ping` (comment). One
  total table (SS-3).
- **`thread_id` on a fan-out frame is the expert's derived thread** (SS-10).
- **`run.end.status`** ∈ `completed | interrupted | cancelled`, computed by Layer 3 (SS-9).
- **`run.error`** carries `message`, `retryable` and a `code` — including the synthesized
  `code: "cancelled", retryable: true` (AP-11).
- **Exactly one terminal frame**, and the response closes after it (SS-7). No `retry:` (SS-5).
- **Fan-out frames interleave**; group by `agent_id`, treat `subagent.*` as brackets, not nesting (SS-12).

### 5.4 The import-linter contracts (P-8)

> **Corrected 2026-08-08 (step 4, P-10).** The `layers` list below is **wrong** as written: `|` is
> import-linter's *independent* delimiter, so `(pkb.server) | (pkb.tui) | (pkb.clients)` forbids
> `pkb.tui -> pkb.clients` — the one import `pkb.clients` exists for (arch §6: the approval helper
> "is imported by the TUI and the Telegram adapter"). Verified BROKEN on both that edge and
> `pkb.server -> pkb.clients`. `pkb.clients` gets its **own layer** directly below the transports;
> the corrected list is in the Layer 4 rules §5.4 and runs 4 kept, 0 broken with both imports
> present. It also buys something this one did not: `pkb.clients` below `pkb.agents` makes "the
> approval helper never touches the harness" a *layers* fact as well as a forbidden-contract one.

```toml
# strict — indirect imports ARE checked; this is the contract that caught
# `pkb.server.app -> pkb.agents.runtime -> langgraph` in the grounding pass
[[tool.importlinter.contracts]]
name = "I2 — transports never import the harness, directly or transitively"
type = "forbidden"
source_modules    = ["pkb.contracts", "pkb.server", "pkb.tui", "pkb.clients"]
forbidden_modules = ["deepagents", "langgraph", "langchain", "langchain_core"]

# direct-only — pkb.service is the composition root; it may reach the harness ONLY
# through pkb.agents' two exported names, never by naming a harness module itself
[[tool.importlinter.contracts]]
name = "I2 — the composition root names no harness module"
type = "forbidden"
source_modules        = ["pkb.service"]
forbidden_modules     = ["deepagents", "langgraph", "langchain", "langchain_core"]
allow_indirect_imports = true

# layers — add pkb.clients and pkb.packs, which are absent today
layers = ["(pkb.server) | (pkb.tui) | (pkb.clients)", "(pkb.service)", "(pkb.agents)",
          "(pkb.packs)", "pkb.contracts", "pkb.core"]
```

### 5.5 `/health`

```jsonc
{ "status": "ok",                  // "ok" | "degraded" — never a non-200 (AP-18)
  "version": "0.1.0", "uptime_s": 8241, "kb_root": "/…/KnowledgeBase",
  "agent_count": 7,                // cached catalog; no tree walk (AP-19)
  "active_runs": 2, "subscribers": 3,
  "runtime":     { "open": true, "db_path": "…", "durability": "sync", "fanout_limit": 3 },
  "threads":     { "total": 41, "pending_approvals": 2 },
  "proposals":   { "pending": 5 },
  "scan_worker": { "state": "running", "pending": 0, "last_run_at": "…", "last_error": null },
  "flush":       { "last_report_at": "…", "findings": 0 },
  "telegram":    { "enabled": true, "state": "running", "restarts": 0,
                   "last_error": null, "last_error_at": null, "started_at": "…" },
  "mcp":         { "mounted": true, "sessions": 1 } }
```
`telegram.state` ∈ `disabled | starting | running | restarting | stopped`. `restarts` and
`last_error` are the supervision state arch §8 asks to be visible — a bot that has restarted 40
times is healthy by any single-sample check and broken by any human's judgement.

### 5.6 MCP tool schemas

```jsonc
// pkb_ask — query the Librarian or a named expert
input:  { "question": "string (required)",
          "agent_id": "string, default 'librarian'",   // catalog id verbatim, may contain '/'
          "thread_id": "string | null" }               // no '::', no 'scan:' (MC-12)
output: { "status": "answered" | "menu" | "escalation" | "timeout" | "error",
          "answer": "string — RunEnd.final_text VERBATIM (MC-9)",
          "thread_id": "…", "agent_id": "…", "run_id": "…",
          "experts": [ { "agent_id": "topic/cooking", "title": "Cooking",
                         "thread_id": "<t>::topic/cooking",
                         "status": "answered|failed|awaiting-approval|busy",
                         "text": "that expert's own final message, verbatim" } ],
          "candidates": ["topic/cooking", "topic/health"],   // status == "menu" only
          "escalation": [ { "path": "Cooking/notes/preheat.md",
                            "review_note": "…", "agent_id": "topic/cooking" } ],
          "proposals":  [ { "proposal_id": "…", "tool": "write_file",
                            "path": "Cooking/notes/summary.md", "reason": "breadth-approval" } ],
          "code": "thread_busy", "retryable": false }        // status == "error" only

// pkb_ingest — always enters at the Librarian; hints are advisory context (MC-17)
input:  { "content": "string (required)", "source_type": "string | null",
          "topic_hint": "string | null", "thread_id": "string | null" }
output: same envelope as pkb_ask, minus "candidates"; "proposals" distinguishes
        proposed from filed (MC-18). "filed" is omitted in v1 — see Q6.

// pkb_research_pack — breadth-first (PK-9)
input:  { "query": "string (required)", "topics": ["agent_id"] | null,
          "include_index": false, "budget_bytes": 0 }
// pkb_implementation_pack — depth-first, notes/summary.md ALWAYS first (PK-10)
input:  { "topic": "agent_id (required)", "include_subtopics": false, "budget_bytes": 0 }

// both packs return:
output: { "status": "ok" | "escalation",
          "kind": "research" | "implementation",
          "scope": ["topic/cooking"],
          "entries":  [ { "path": "Cooking/notes/summary.md", "role": "notes-summary",
                          "bytes": 1842, "text": "…" } ],
          "omitted":  [ { "path": "…", "reason": "budget" } ],   // whole entries only (PK-11)
          "truncated": false,
          "escalation": [ { "path": "…", "review_note": "…", "agent_id": "…" } ] }

// resources (MC-6)
pkb://agents            -> {"agents": [AgentDescriptor, …]}
pkb://proposals         -> {"proposals": [PendingProposal, …]}
pkb://proposals/{id}    -> PendingProposal
```

An escalation is `isError == false` with `status: "escalation"` (MC-20). A well-behaved agent
retries errors, and a retried escalation is an escalation ignored.

---

## 6. Test strategy

Arch §9's *intent* — the whole server suite runs free, fast, keyless and networkless — is achievable
end to end and was proven so in the grounding pass (a real pytest file, **4 passed** under this
repo's own `pytest-asyncio 1.4`, no `anyio` plugin, no network, no key). Only the single-tool claim
is wrong (P-4).

### 6.1 What a stub `PkbService` buys

Because `PkbService` is a Protocol whose every type is expressible without the harness (SV-1),
**every route, every SSE frame, every MCP tool and every TUI screen tests against a stub** — no
runtime, no checkpointer, no model, no SQLite. That is what lets the server suite assert things a
live system could never assert deterministically: that a fan-out interleaves, that an expert's gate
parks on the derived thread, that a busy thread 409s within milliseconds while the first event is
five seconds away, that a stale interrupt id leaves the thread interrupted.

Layer 2 already proved the seam compiles with the harness banned. **Step 3's acceptance test is that
the same is true of the real service and every server module** (SV-30, AP-2):

```python
import pkb.server, sys
assert not {"deepagents", "langgraph", "langchain", "langchain_core"} & set(sys.modules)
```

### 6.2 Three tools, one job each

| Tool | Tests | Why not the others |
|------|-------|--------------------|
| **`TestClient`** (pin `base_url="http://127.0.0.1"`, MC-4) | All non-streaming routes; the error-code table; **and SSE frame *content*** — the body arrives concatenated, so split on `\r\n\r\n` and assert event names and payloads. | Cannot test incrementality or disconnect: it reads the whole stream into a `ByteStream` before returning (P-4). |
| **`httpx2.ASGITransport`** | The **MCP mount end-to-end, in-process**: `streamable_http_client(url, http_client=…)` accepts an ASGI-backed client, so the official SDK drives the mounted server with no socket. Verified working. | Buffers exactly like `TestClient`, so it proves nothing about streaming. |
| **A ~40-line raw ASGI driver** | The **only** in-process way to assert (a) frames arrive over time and (b) the generator's `finally` runs on disconnect. Build the scope with `spec_version "2.3"` (uvicorn's value), `create_task(app(scope, receive, send))`, read `http.response.body` off a queue, return `{"type": "http.disconnect"}` from `receive`. | This is how AP-6/AP-7's detach behaviour is pinned without spinning uvicorn — that a hangup leaves the run task alive to its terminal event and the generator's `finally` merely unsubscribes. It is the highest-value test in the layer. (It is *not* pinning P-2's flush regression; that measurement did not reproduce — §2.) |

**uvicorn-in-a-task** is reserved for the handful of tests that must prove real-socket behaviour:
the ping frame's wire form, a mid-stream `run.error` keeping its 200, and the `spec_version 2.4`
divergence in SS-1.

### 6.3 The headline assertions, by area

| Area | Fixture | Headline assertions |
|------|---------|---------------------|
| **seam** (`tests/service/test_seam.py`) | harness-banned subprocess + fake runtime | the **real** `PkbService` imports and runs with `deepagents`/`langgraph`/`langchain` banned (SV-30); AST over the Protocol (SV-1); one harness-touching module (SV-2). |
| **threads & ids** (`tests/service/test_threads.py`) | `tmp_path` SQLite | three disjoint namespaces (SV-9); duplicate id → `IntegrityError` (SV-11); unregistered derived thread still resumable and self-registering (SV-12); `scan:` refused (SV-13); the delete cascade both directions (SV-24); exact column set (ST-5). |
| **sqlite concurrency** (`tests/service/test_store.py`) | real `PkbRuntime` over `tmp_path` | connection opened before the runtime sees `journal_mode == 'delete'`, after sees `'wal'` (AP-4); 300 concurrent aiosqlite upserts during a live run, zero failures (ST-2); a transaction held across an await is caught (ST-3). |
| **run supervision** (`tests/server/test_runs.py`) | **raw ASGI driver** + real runtime | **a hard disconnect mid-run leaves the run task alive to its terminal event, the note on disk AND listed in `index.md`** (AP-6 — the D2/D3 durability guarantee); disconnect does not cancel (AP-7); two subscribers, one dropped, run unaffected (AP-8); cancel yields the synthesized terminal frame (AP-11). |
| **routes** (`tests/server/test_routes.py`) | `TestClient` + stub service | the pinned route table (RO-1); `{agent_id:path}` with `/` (RO-2); greedy-converter ordering (RO-3); the six error codes plus an unmapped subclass → 500 (RO-20); `interrupt_id` required (RO-12); validation before the stream opens (RO-13); a delegate's decisions posted to the parent → 409 (RO-14). |
| **sse** (`tests/server/test_sse.py`) | pure-function unit tests + raw driver | the name table is total (SS-3); no `\n` inside `data:`, no envelope/field collision (SS-4); `seq` 0..n (SS-5); exactly one terminal frame over direct, fan-out and error runs (SS-7); `run.end.status` (SS-9); derived `thread_id` per frame including the `general-purpose` case (SS-10); no dedupe set in `pkb/server` (SS-13). |
| **mcp** (`tests/server/test_mcp.py`) | `httpx2.ASGITransport` + official SDK client | exactly four tools and the two resources (MC-5, MC-6); `/mcp` returns 200 with no 307 (MC-2); the lifespan drives the session manager (MC-3); `testserver` → 421 and `127.0.0.1` → 200 (MC-4); `propose_only` on every path (MC-8); `final_text` byte-for-byte (MC-9); `experts` from events, never from parsed prose (MC-10); escalation is `isError == false` (MC-20). |
| **packs** (`tests/test_packs.py`) | fixture KB, **no runtime** | `import pkb.packs` loads no harness module (PK-7); both packs build against an **empty** `ScriptedChatModel` script (PK-8); golden ordered path lists (PK-9, PK-10); budget truncation at entry boundaries (PK-11); tree byte-identical before and after (PK-12). |
| **invariants** (CI) | — | `lint-imports` passes on the real tree and **fails on a planted `import langgraph` in `pkb/server/routes.py`** and on a planted `pkb.server.app -> pkb.agents.runtime` (AP-2, §5.4). |

### 6.4 What genuinely needs a live model (`-m live`, deselected by default)

Only judgment, and only where a fake model cannot say anything. **Nothing in §1's rule table needs
one** — every rule in §1 is asserted with **no key**. The live marker covers, and only covers:

- Whether a model-written thread title is a **good** title (TT-1). The mechanism — after the first
  reply, off the critical path, once, never over a human-set title — is asserted with a scripted
  model and no key.

- Whether classification is **right** — whether a two-topic item names both topics and a one-topic
  item resists naming three (unchanged from Layer 2's live list).
- Whether `pkb_ingest`'s `topic_hint` actually influences classification, or is ignored (MC-17).
- Whether `pkb_research_pack`'s topic **selection** picks the topics a human would (PK-9); the
  assembly and ordering are golden-tested without a model.
- Whether two experts handed one source through MCP produce genuinely different extractions
  (decision G, PR-9).
- End-to-end cross-channel resume against a real model: interrupt through the TUI's service calls,
  close and reopen the service, resolve through the Telegram adapter's calls, assert the file lands.
  **The stub version of this test needs no model and is the one that must always pass.**

---

## 7. Explicitly out of Layer 3

Do **not** build any of the following in `pkb.service` or `pkb.server`. Each is listed with where it belongs.

**Already Layer 2 — cite it, never reimplement it**
- Event normalization, deduplication and the nine-kind union. `pkb.agents.events` (RT-41, RT-43).
- The diff and validation text inside an approval. `gates.describe_write` renders it server-side,
  once (RT-34, RT-35); Layer 3 renders the **UI**, never a second diff.
- Which decisions an action allows. `ActionView.allowed_decisions` is server-side truth; a client
  narrows its UI and never widens it (RT-32, RO-15).
- The gate table, the deny list, the write lock, the flush, the 3-attempt bound, the escalation.
- The `(agent_id, thread_id)` *config*: `runtime.thread_config` is the only config anyone builds (RT-37).
- Thread-id **derivation semantics**: `expert_thread_id`/`librarian_thread_id` move to the seam
  (C-1), but the rule that a routed thread is `<parent>::<agent-id>` is LB-14's, not Layer 3's.
- The conflict-scan **run**. Layer 3 owns the timer and the dequeue loop only (C12, AP-14).
- Applying an approved proposal. That is a new Layer 2 entry point and an RT-18 amendment (Q3).
- Anything that decides *whether* an expert runs. Routing is a harness workflow (LB-15, decision F).

**Already Layer 1**
- Every tree walk, index, tag registry, `updated` stamp, scaffold, slugification, id bijection,
  derived/generated predicate, `ScanRequest` construction, and `files_with_tag`. `pkb.packs` calls
  them; it does not reimplement them (PK-12).

**Step 4 — `pkb.tui` + `pkb.clients.approval`**
- The approval **modal** and its diff rendering; the sidebar's agent picker and thread tree; the
  interrupt→`Decision` client helper (which imports `validate_decisions` from the seam, never a copy);
  the SSE **decoder** (which imports the same name table the encoder does, SS-3).

**Step 5 — `pkb.server.telegram`**
- The bot's command surface (`/agents`, `/threads`) — **no `/connect`**: a chat is bound to its agent
  by the `chat_id` → `agent_id` mapping (TG-1) and an unmapped chat is answered with instructions
  rather than routed to a default (TG-2). Also inline keyboard rendering, message
  splitting on Telegram's length limit — **never on meaning** (LB-18), and never a `message.delta`
  consumer (editing per token hits the rate limit). Step 3 builds only the supervised task slot,
  its `/health` reporting (AP-17, AP-18) and the `origin_channel="telegram"` stamp.

**Deferred**
- Auth, a `/v1` prefix, multi-user namespacing, remote topology (arch §10, AP-20).
- Fan-out of one run's events to *other* channels. D3 means shared **state**, not shared **streams**;
  a TUI watching a thread Telegram resumes sees the outcome through `get_thread`, not live SSE.
  Conflating the two invites a pub/sub layer the daemon does not have.
- `Last-Event-ID` reconnection with gapless replay across a *new* HTTP request. `seq` on every frame
  (SS-5) keeps it additive; `GET /threads/{id}` re-sync closes the gap on a personal KB.
- An ACP adapter (arch §10) — a fourth adapter, additive once `PkbService` exists.

**Never, at any layer**
- **A model call on a run's critical path, and any model call other than the titling one.** Layer 3's
  single model call is the title (TT-1…TT-4, SV-25), which runs after the reply is delivered and whose
  failure is never the turn's failure. Nothing else: not to summarize a merged reply (MC-9), not to
  assemble a pack (PK-8), not to choose an agent when an id does not resolve (MC-16, MC-19). Layer 3
  builds no model client of its own — the titling call goes through the runtime — so `grep` for a
  model client in `pkb/server` and `pkb/service` must still find nothing.
- **A write under `kb_root` from Layer 3, by any path** (SV-22, I3).
- **Parsing the merged Librarian reply** to recover thread ids, filed paths or expert names. That
  makes a rendering format load-bearing and turns LB-18's golden test into a wire protocol (MC-10, SS-14).
- **Branching on `origin_channel` to permit or refuse anything** (RO-22).
- **Refusing a run from `pending_interrupt_id`** or any other Layer 3 cache of Layer 2 state (SV-16).
- **Iterating `runtime.run(...)` from an ASGI response handler** (AP-6). It ties the run's lifetime to
  the socket, which is D2/D3's promise deleted, and the tests that would catch it are the ones nobody
  writes.

---

## 8. As built (2026-08-08)

Built as `pkb/packs.py`, `pkb/service/` (five modules), `pkb/server/` (six), `pkb/daemon.py`, and the
seam additions in `pkb/contracts.py`. **1529 tests** pass — 226 of them new, across
`tests/service/{test_seam,test_threads,test_store}.py`,
`tests/server/{test_runs,test_routes,test_sse,test_mcp}.py` and `tests/test_packs.py` — with ruff,
mypy-strict and **four** import contracts green.

### What the grounding re-run corrected

Seven probes executed every package assumption against the installed versions. **No claim in §2
failed to reproduce**, and SS-1 in particular is now independently verified rather than inherited
from the pass that produced the discredited P-2 — so its "re-run before relying on it" hedge is
dropped. But several rules were wrong in ways that were bugs rather than wording:

| Rule | What the re-run found |
|---|---|
| **AP-10** | "Await the first `__anext__`" is right for a *refusal* (0.01 ms) and wrong for an *admitted* run, whose first event is a whole model call away — 2.06 s measured. Awaiting unconditionally holds the response headers for that long, which is the thing AP-10 exists to prevent. It is now a **race** with a 250 ms deadline: refused runs raise, admitted ones commit the headers and put the pending future at the head of the stream. |
| **SS-1, AP-6** | New hard constraint: **the SSE generator's `finally` must be synchronous.** The enclosing anyio cancel scope is level-triggered, so the first `await` inside it raises `CancelledError` again and the rest never runs — and `asyncio.shield` does **not** rescue it. Anything needing an await on teardown belongs to the run task. |
| **SS-1** | The 2.4 hazard is worse than stated. Under uvicorn's real contract (`send` raises `OSError` on a dead socket) `StreamingResponse` does not merely keep generating — starlette raises `ClientDisconnect` and the suspended generator is **never finalized**. |
| **AP-9** | The replay buffer must hold `ServerSentEvent` objects, never the dicts handed to the response: `ensure_bytes` **mutates** a yielded dict, adding a `sep` key, and an unknown key raises. |
| **SS-6** | The ping is a **fixed-interval heartbeat over the whole connection**, not an idleness timer — outgoing data never resets it. Nothing may read `: ping` as "the run is idle". `ping=0` is a busy loop, not an off switch. |
| **SS-5, §5.3** | Real wire bytes are **CRLF**, and field order is fixed as comment, id, event, data, retry regardless of construction order. The §5.3 sample's LF framing and its `: ping - <ISO>` line are both wrong. |
| **ST-2, ST-9** | Layer 3's connection must be opened `isolation_level=None`. With aiosqlite's default deferred isolation, one coroutine's `commit()` commits every other coroutine's pending statement on a shared connection — six rows persisted where five were expected. |
| **ST-3** | The lock-out takes exactly the **victim's** `busy_timeout` (5.4 s at the default), not 16 s. The rule is stronger that way: the victim is the *saver*, whose timeout Layer 3 does not own. |
| **ST-7** | Five foreign tables, not seven — `store_vectors` and `vector_migrations` are not created without an embedding index. Two index names (`store_prefix_idx`, `idx_store_expires_at`) are also reserved. |
| **MC-1** | `mcp.server.fastmcp` is **gone**. `from mcp.server import MCPServer`, `from mcp.server.streamable_http_manager import StreamableHTTPASGIApp`, `from mcp.client.streamable_http import streamable_http_client`, types from `mcp_types`. |
| **MC-2** | `app.mount` fails a **third** way the rule does not name, and it is the one an implementer hits first: `streamable_http_path` defaults to `/mcp` inside the sub-app, so the endpoint lands at `/mcp/mcp`. |
| **MC-4** | A **portless** Host header 421s exactly like `testserver`. Four places in this document say to pin `base_url="http://127.0.0.1"`; every one of them would produce a suite where every MCP test 421s. It must carry a port. |
| **MC-14** | A coded error cannot be **raised**: every exception path yields `structured_content: null` and prefixes the message with the tool's name. It must be a **returned** `CallToolResult` with `is_error=True`. `CallToolResult` also may not appear in a return annotation's union — `InvalidSignature`. |
| **MC-6** | A templated resource appears **only** in `list_resource_templates()`, never in `list_resources()`. It is two static resources plus one template, not "two resources". |
| **PK-9** | Unimplementable as written against the `Pack` type: a rendered tag subtree has no path. It ships as a synthesized entry at `path="tags.md"`, `role="tag-subtree"`. |
| **PK-12** | Entry *text* cannot come from the snapshot — `ParsedDocument` keeps only the frontmatter-stripped body. A targeted `abs_path` read of a path the snapshot already named is not a second tree walk, and the rule has to say so. |

### What the test suite found

The eight test files were written in parallel, each over a disjoint rule set, each required to
demonstrate any defect it found as a **strict xfail** rather than work around it. They found
**thirteen**, all since fixed — and because each test was written against the broken code, each one
demonstrably fails without its fix.

The worst was mine, introduced by AP-10's own correction: **a run admitted but not yet speaking was
handed `run_id=""`** — the normal path, since the race times out by design. That empty string is the
key the supervisor files hubs and tasks under, so every slow-starting run shared one key: the second
run's hub replaced the first's, the first's teardown deleted the second's thread entry, `attach`
handed a reconnecting client another thread's stream, and `cancel` was unaddressable because the
client had only ever received `""`. The run id is now **minted before the run starts** and handed
down (RO-11, SS-8).

The rest, by what they were:

* **Rows that were never written.** `resume` did not register an unregistered derived thread, so
  answering an approval on a thread whose row was missing left that conversation invisible to every
  list (SV-12). A derived thread did not inherit its parent's `origin_channel`, so every routed row
  read `http` whatever channel the human was on (ST-13).
* **Streams that never ended.** A dropped subscriber's end-of-stream sentinel was sent under
  `suppress(QueueFull)` — and the only way to be dropped is that queue being full, so the sentinel
  was always discarded: the run was protected and the reader was left awaiting a queue nothing would
  feed again (AP-8). The fix reserves a slot for the sentinel, so what the dropped reader keeps is a
  clean prefix followed by an ending rather than a stream with a hole in it.
* **A mechanism wired to nothing.** No caller passed the shutdown event to the SSE generator, so
  `EventSourceResponse(shutdown_event=None)`, the farewell branch was unreachable and
  `SseEncoder.cancelled()` was dead code — AP-12's grace period buys nothing without a generator
  that notices (AP-12).
* **A terminal frame a client had to string-match.** A cancelled run's `run.error` carried neither
  AP-11's `code: "cancelled"` nor SS-9's status, because status was computed only for `RunEnd` —
  making SS-9's third value unreachable on the wire, since a cancelled run never emits `run.end` at
  all (AP-11, SS-9, SS-15).
* **One payload, two wire shapes.** A failed run and a timeout were returned through the *success*
  envelope while every other coded failure went through `is_error` — so the only caller who could
  act on `retryable` was exactly the one branching on `is_error` (MC-14, MC-15).
* **A menu that invented options.** Candidate ids were matched by substring, so a reply naming
  `topic/cooking/grilling` also offered `topic/cooking` — an option the Librarian never wrote, which
  a caller picks, filing the material one level up from where it belongs. And the heuristic fired on
  a **direct expert ask**, which can never be a menu because only the Librarian classifies (MC-19).
* **A delete with no undo.** `DELETE /threads/{id}` did not refuse while a run was live on that
  thread, though it erases checkpoints and every derived expert thread (RO-16).
* **A golden test that could not be golden.** `research_pack` ordered topics by the caller's
  argument rather than snapshot order, so two callers naming the same topics differently got
  byte-different packs for identical content (PK-9).

### Deviations from the spec, recorded

* **`RG-20`'s public surface gains `chat_model_for`.** RG-21 makes the model-with-failover a
  registry property; the ingestion loop was a third consumer and reached for `init_chat_model`
  itself. Amended in the Layer 2 rules.
* **`SV-2`'s harness import is deferred into `open_service`.** SV-4/SV-30 require the *real*
  `RuntimeService` to import and run with the harness banned from `sys.meta_path`; a module-level
  `from pkb.agents import PkbRuntime` makes that impossible. The module still names only the two
  exported names and no harness module, so the import contract is unchanged.
* **Thread titles are deterministic in v1, with TT-1…TT-4's *mechanism* shipped.** The title is
  written after the first reply, off the critical path, once per thread, never over a human-set
  one — that is all asserted. The model call itself needs a Layer 2 entry point that answers one
  prompt without appending to the conversation being titled; the runtime has none, and adding one is
  a Layer 2 amendment rather than a transport concern. Swapping it in changes one function body.
* **`pkb_research_pack` requires explicit `topics` in v1.** PK-8 allows it at most one model call —
  the classification that selects topics — and that call lives in Layer 2 and is not wired to this
  tool. Naming the topics is the honest interface until it is.
* **`pkb.tui` and `pkb.clients` are absent from the import contract's source list**, because
  import-linter errors on a module that does not exist. They join it in step 4.

---

## 9. Amendments from step 4's spec (2026-08-08)

Specifying Layer 4 against this document found **four defects in the built Layer 3 code** and five
rows here that are false about it. The defects are fixed and carry regression tests; the rows above
are annotated in place. Recorded together because the pattern is the point: a client written against
a promise the server does not keep fails in the client, where it is hardest to debug.

| | The defect | Why it mattered |
|---|---|---|
| **SS-7** | `RunSupervisor._drive` did `terminal_seen = isinstance(event, RunEnd \| RunError)` — an assignment where it had to be `\|=`. Any event after `run.end` reset the flag and the `finally` published a **spurious second terminal frame**. | A straggling delegate frame after a Librarian merge is entirely plausible, and a client that kept reading would turn a completed turn into a visible failure. One character. |
| **SS-15** | `_drive` published `RunError(message=str(exc))` and **discarded the exception's type**, so every failure reached the wire as an untyped `run_error` — while SS-15's own test assertion names `code="thread_busy"`. | RO-21 tells clients to branch on the code and never on prose. With no code there was nothing to branch on, on the one path a human sees most. The supervisor now records `code_for(exc)` and the encoder puts it on the wire. |
| **AP-9** | `RunHub._replay` kept the **first** 512 frames and `subscribe()` dropped the overflow under `suppress(QueueFull)`. | The wrong half: a client attaching to a run in flight wants what just happened. And because the per-response encoder renumbers `seq` contiguously, the resulting hole was **undetectable on the wire** — an attach rendered a conversation with a silent bite out of the middle. Now a `deque(maxlen=…)` suffix, with the subscriber queue sized to fit it. |
| **RO-16 / ST-13 / SV-12 / RO-11** | Fixed earlier the same day, from the step-3 suite. See §8. | |

Two more are **recorded, not fixed**, because the honest fix is a client rule rather than a server
change — both are in the Layer 4 rules:

* **AP-12's farewell frame is not delivered while a generator is suspended on `__anext__`.** The
  shutdown check sits inside `async for event in subscription.events`, so a run waiting on a 16 s
  cloud call — exactly when a shutdown lands — never reaches it. SS-7's *outcome unknown* path is
  therefore the client's **primary** shutdown path rather than a rare one, and a client must not
  weaken its handling on the strength of AP-12.
* **"A resume continues the same run" is false of the run id.** `RuntimeService.resume` mints a new
  one, which is what keeps the supervisor's bookkeeping honest after the first run's task completed
  at the interrupt. The prose is amended to "continues the same **turn**, on a new run id"; a client
  takes its cancel target from the most recent `run.started`. Because `DELETE /runs/{unknown}` is a
  deliberate 204, a client holding the pre-interrupt id would cancel nothing and report success.
