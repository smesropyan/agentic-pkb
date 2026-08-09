# PKB Telegram adapter (Layer 5) — Requirements and Rules

**Date**: 2026-08-08, extended 2026-08-09 (§9)
**Status**: **§1–§8 are built** (see §8 "As built"); **§9 "Topics: a channel per expert" is designed,
not built.** Every package and built-code assumption in §1–§7 was **executed** in this repo's venv on
2026-08-08, before a line was written; see §2. Layers 1–5 are built: 2014 tests, no key, no network,
five import contracts green.
**Scope**: `pkb.server.telegram` (+ its transport sibling and its Layer 3 store) — build-order
step 5 of the [architecture design](2026-08-06-pkb-architecture-design.md) §6 "Telegram
(daemon-hosted)", written against the service seam `src/pkb/service/__init__.py`, the shared helper
[`pkb.clients.approval`](2026-08-08-pkb-tui-clients-layer4-rules.md) and the supervised slot Layer 3
already built (`ServerConfig.telegram_task`, `_supervise`, `AP-17`, `AP-18`).

### What is verified and what is not

| Marking | Means |
|---------|-------|
| **verified** | Executed on 2026-08-08 against this repo's `uv.lock` — `httpx 0.28.1`, `httpx2 2.9.1`, `fastapi`, `sse-starlette`, `mcp 2.0.0`, `import-linter`, `pytest-asyncio 1.4`, Python 3.12 — with the **real** `pkb.server.app._supervise`, the **real** `HealthState`, the **real** `pkb.clients.approval`, the real `GATE_DECISIONS` table and the repo's own `tests/service/test_seam.py`. Not read: run. Every row of §2 carries it. |
| **built (Layers 1–4)** | Already implemented and covered by the passing suite; Layer 5 binds against it, never around it. |
| **designed** | Specified here, not implemented. Every `TG-5 … TG-71` rule carried this until §8; every `TG-72 …` rule in **§9** carries it now. |

> **§9 "Topics: a channel per expert" (2026-08-09) is designed, not built.** §1–§8 describe the
> shipped adapter, in which a *chat* maps to an agent — and one bot gives one human one private
> chat, so one human reached one agent. §9 makes the addressing unit the **channel**
> `(chat_id, topic_id)` using the private-chat topics of Bot API 9.3/9.4, amends the §1 rules it
> touches in place (each marked **Amended 2026-08-09**), and records the four approved defect
> amendments. **`pkb.tui` and `pkb.clients` are untouched by it.** Read §9.1 before §9.3: one
> Telegram bug (a deleted topic is *silent*) is the whole shape of the design.

Three lenses mined this layer and two probe suites executed the packages. Where a lens and the
grounding disagree, **the grounding wins** — that rule has caught a real defect in every pass, and
here it overturned the task brief's own premise (P-22) and killed the most attractive package
layout (P-24).

**No Telegram library is installed, and none is added.** The Bot API is JSON over HTTPS; `httpx` is
already a declared dependency. See decision R and Q18.

---

## Read this first

Six findings are load-bearing enough to state up front. Each one would have failed **silently** —
the code compiles, the tests pass, `/health` says `ok`, and the guarantee is fiction.

| | What the specs (or the brief) assume | What the built code and the packages actually do |
|---|---|---|
| **P-22** | The task brief: *"MC-7's built test asserts `pkb.server.telegram` pulls no HTTP client."* | **It does not, and it never has.** `tests/server/test_mcp.py::test_the_adapter_pulls_no_http_client_and_no_harness_mc7` (line 653) has three assertions and all three are over `MCP_SOURCE` alone; the string "telegram" does not appear in that file. Executed: a `pkb/server/telegram.py` that imports `httpx`, `pkb.clients.approval`, `pkb.server.sse` and `pkb.service` and POSTs to `api.telegram.org` leaves **`Contracts: 5 kept, 0 broken`** over 123 files. So nothing mechanical resists the adapter today — only the *rule prose*, which as written is unsatisfiable, because there is no non-HTTP transport to the Bot API short of MTProto. C-27 amends it. |
| **P-23** | Arch §8: *"Telegram bot task crashes → supervised restart. The daemon and in-flight runs are unaffected."* | **`_supervise` restarts the callable and cancels nothing the previous invocation started.** Executed against the real `_supervise`: a task that spawns one child and raises produced, after 3.6 s, `generations: 3 \| live children: ['poller-1','poller-2','poller-3']`. Against the real API that is three concurrent `getUpdates` on one token; Telegram permits one, the losers get `409 Conflict: terminated by other getUpdates request`, and updates are split across three consumers, two of them crashing. `/health` reports `running` throughout. The fix belongs in the task (TG-7), not in `_supervise`. |
| **P-24** | "Put the httpx implementation in a subpackage so the no-HTTP-import assertion survives." | **A `telegram/` package deletes the adapter from every Layer 3 seam scan.** `tests/service/test_seam.py::_sources` uses `directory.glob("*.py")` — **non-recursive** — and its own docstring names `pkb/server/telegram.py` as the exact reason discovery matters. Executed both ways: as `pkb/server/telegram.py`, a module importing `os`/`tempfile` and calling `mkdir()`/`write_bytes()` **fails** SV-22 with four offenders and `uuid.uuid4()` **fails** SV-10 with `telegram.py:_callback_token`; move the identical code to `pkb/server/telegram/api.py` and **both tests pass**. The isolation intended to make MC-7 structural would instead switch off SV-1, SV-10, SV-18, SV-22 and SV-25 for the newest transport. **Two sibling modules, never a package** (TG-5). |
| **P-25** | AP-18: `/health` is a 200 that reports degradation in the body, and the daemon has no auth (AP-20). | **The bot token leaks into `/health` and into the log on the most likely first failure.** Executed: `httpx.Response(401).raise_for_status()` produces `HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.telegram.org/bot123456789:AAF-FAKE-TOKEN…/getUpdates'`; `SubsystemState.failed` stores `f"{type(exc).__name__}: {exc}"`; `HealthState.payload()` publishes it verbatim — `TOKEN IN /health: True`. Separately executed: httpx logs `HTTP Request: POST https://api.telegram.org/bot<TOKEN>/getUpdates "HTTP/1.1 200 OK"` at **INFO**, and `pkb.daemon.main` calls `logging.basicConfig(level=logging.INFO)`. A long poll writes that line every ~30 s forever. |
| **P-26** | CL-22: `truncate` is the shared cut for a length-limited channel, and Telegram's limit is 4096. | **`truncate` counts characters; Telegram counts UTF-16 code units, and the common case is silently over.** Executed: `truncate("🔥"*3000 + "\nplain tail\n" + "x"*500, 4096)` returns **3517 characters = 6517 UTF-16 units** and reports `was_truncated=False` — it did not cut at all, because 3517 ≤ 4096. Telegram rejects that send with `400 message is too long`, so the human sees **nothing**, not a short diff. A knowledge base about food, travel or code carries astral-plane characters routinely. The budget is the adapter's arithmetic (decision V, TG-44). |
| **P-27** | The in-process adapter sees what an SSE client sees, minus the framing. | **Three fields the TUI is handed do not exist in process.** Executed: `RunEnd` fields are `['run_id','final_text']` — **no `status`**; `RunError` fields are `['run_id','message','retryable']` — **no `code`**. SS-9's four statuses and SS-15's machine code are authored by `SseEncoder`, and `status_for` distinguishes a cancellation by **string-matching** `_CANCELLED_MESSAGE = "the run was cancelled"`, a private name in `pkb/server/sse.py`. `pkb.contracts` exports `RUN_STATUSES` and `CANCELLED_CODE` but **not** that message. An in-process consumer must therefore re-derive terminal status — which is exactly the second implementation SS-9 exists to prevent (decision W, TG-50). |

The corrected approach for each is in §2 and carried into the rule table.

### Decisions applied on top of the mined recommendations

| # | Decision | Why |
|---|----------|-----|
| **Q** | **MC-7 is amended to ban *loopback*, not HTTP, and the adapter is two sibling modules: `pkb/server/telegram.py` (all logic, no HTTP import) and `pkb/server/telegram_api.py` (the only httpx module).** Never a subpackage. | P-22 + P-24. MC-7's own docstring states the real intent — *"a bot that curls its own daemon is a second process to supervise, a second failure mode and a second copy of the error table"* — and egress to `api.telegram.org` is not that. The split keeps MC-7's literal no-HTTP-import assertion true of the module that holds every rule in §1, and it is what makes the whole approval surface drivable against a fake. The *sibling* part is not taste: a package is invisible to five built scans (P-24, executed both ways). |
| **R** | **Raw `httpx` behind a narrow `BotApi` Protocol. No Telegram library is added.** PTB's two correctness details are lifted into rules instead (TG-69: read budget > poll timeout, and a second connection pool so a 25 s long poll never occupies the socket an approval must go out on). | The adapter needs six methods. `python-telegram-bot` would add exactly one package (its deps are all installed) and gets those two things right — but the 95% of it that is `telegram.ext` (Application, Updater, JobQueue, signal handlers) is precisely the framework D9's supervised-task shape forbids using, so we would take a dependency in order to ban most of it by rule. `httpx` is already declared; `httpx2` is **not** (it rides in transitively via `mcp`), and `pkb.tui.client` already depends on it undeclared — do not repeat that here. Q18 records PTB as the reversible alternative, and no rule in §1 changes if it wins. |
| **S** | **The bot's durable state is a Layer 3 table module (`pkb/service/telegram.py`, tables prefixed `pkb_telegram_`) reached through a narrow `TelegramStore` Protocol.** Not `PkbService` (it is one transport's bookkeeping, useless to MCP and the TUI), not adapter memory, not a JSON file. | Executed: `_supervise` restarts the callable with **nothing carried across** — every dict, client and subscription of the previous invocation is gone. An in-memory chat→thread binding means one 502 silently starts the human's next message in a brand-new conversation while the old one still holds the pending approval. ST-7 already reserves the `pkb_` prefix for Layer 3, `db_path` defaults to `<kb>/../pkb.sqlite` (outside the tree, I3 intact), and `daemon.py:68` already reaches `service.proposals_store` — the precedent exists. The Protocol is what lets the whole suite run against an in-memory fake store. |
| **T** | **One durable ledger keyed on `update_id`, written *before* dispatch; the poll offset is `MAX(update_id)+1` from it.** At-most-once **agent execution**; a crash in the gap between the row and `start_run` is a **named** loss the bot reports to the chat on restart. | `getUpdates` is at-least-once: an update is confirmed only by the *next* poll's offset, and unconfirmed updates are redelivered for 24 hours. Combined with `_supervise`'s restart-on-anything, acknowledging after processing re-runs a turn that already wrote to a tree with **no undo** (D6), and acknowledging before processing loses a message with no trace anywhere. The ledger collapses both into one failure the human can act on: re-send. It also reconciles the two lenses that disagreed — a button pressed while the daemon was **down** was never consumed, so it is not in the ledger, so Telegram's 24-hour redelivery still lands it, and TG-58's durable re-read resolves it. |
| **U** | **The surface a decision is made against is never truncated.** The whole `description` is in the chat *before* the buttons are — one message if it fits, otherwise the full text as an in-memory `sendDocument` immediately preceding a short button message. `truncate` is used only for the preview, and gains a `marker=` parameter. | Measured on real `describe_write` output: a 120-bullet note approval is **9,218 characters** and a delete of the same file is **7,868** (a delete embeds the whole current file; a new file embeds the whole proposal). `truncate(…, 4096)` shows the human bullets 0–59 and hides 60–119 under an irreversible approve button. TU-39 already ruled this exact trade for the TUI — *"truncating it is worse than not showing it"* — and Telegram's limit is a reason to change the **container**, not the answer. The `marker=` parameter exists because `TRUNCATION_MARKER` hard-codes *"open the TUI for the whole diff"*, which under this decision prints directly above the whole diff. |
| **V** | **UTF-16 arithmetic stays in the adapter.** `pkb.clients.approval.truncate` stays character-based and channel-agnostic; the adapter searches for the largest code-point budget whose UTF-16 length fits, and re-measures the result. | P-26. CL-2 requires that module pure and channel-agnostic and CL-9's whole shape is "a channel narrows, the shared code does not learn about channels". Telegram's unit is UTF-16 code units; the TUI's is terminal cells; a third channel would bring a third. Teaching `truncate` about units reopens `pkb.clients`, its import-contract rationale and the CL-* rules for a five-line loop. |
| **W** | **`CANCELLED_MESSAGE` moves into `pkb.contracts` next to `CANCELLED_CODE`, and one shared pure `terminal_status(event, *, interrupted) -> RunStatus` is added there, which `SseEncoder.status_for` also calls.** A one-constant seam amendment, in the same commit as step 5. | P-27. Without it the bot either imports a private `pkb.server.sse._CANCELLED_MESSAGE` or keeps a second copy of the string that decides "cancelled" versus "the provider failed" — the drift decision P was written to stop, one channel later. Four things already have to agree on that table; making it five without a shared name is how a cancelled turn starts rendering as an error on a phone and as a cancellation in the TUI. |
| **X** | **Private chats only, plus an explicit `owner_user_ids` allow-list checked on every `message.from.id` and every `callback_query.from.id`. This is stated in the spec as the system's only authentication boundary.** | D9 says the bot has *"no auth boundary"* and arch §10 defers multi-user on the explicit ground that *"the daemon binds to localhost"*. Both stop being true the moment step 5 ships: a bot's username is discoverable and a bot token is a public inbound path into a process with no authentication that can write to a knowledge base with no undo. TG-1's mapping was written about **addressing** — which expert am I talking to — not about **who may say yes** to an irreversible write. The check costs one comparison and cannot be retrofitted after a token leaks. |

---

## 0. Conventions

Same severity convention as Layers 1–4. **Rule ids are stable**; a test that changes must cite the
rule that changed. Every test assertion says **no key** or **live**.

**"live" means something new here.** In Layers 1–4 it meant a live *model*. Layer 5 needs no model
at all; what a handful of facts need is a real *bot token* against `api.telegram.org`. Those are
marked **live** and carry `@pytest.mark.live`, deselected by default exactly as the other layers do
(§6.5). The default suite collects **zero** of them and opens no socket.

### 0.1 Hard constraints inherited

- **I2** — `pkb.server` (and therefore both telegram modules) never imports `deepagents`,
  `langgraph`, `langchain`, `langchain_core` or `pkb.agents`, directly or transitively. Everything
  reaches the system through `PkbService` and `pkb.contracts`. Verified: an httpx-carrying adapter
  keeps all five contracts (P-22).
- **I3 / SV-22** — Layer 3 and above write **nothing** under `kb_root`, by any path. The built SV-22
  scan fails outright on a module that imports `os`, `shutil` or `tempfile` or calls
  `write_text`/`write_bytes`/`mkdir`/`unlink`/`rename`/`symlink_to`/`open(...,'w')` (executed, P-24).
  That is decisive for attachments (TG-35) and for where the token comes from (TG-24).
- **SV-10** — only `threads.py:mint_thread_id` and `threads.py:mint_run_id` may call `uuid*` anywhere
  under `pkb/service/` and `pkb/server/`. Executed: a `telegram.py` containing `uuid.uuid4()` fails
  that built test. Callback handles are `secrets.token_hex`, not uuids (TG-57, TG-70).
- **Everything mechanical already exists below.** The adapter cites a rule; it never contains a
  second implementation of validation, diff rendering, decision validation, id derivation, event
  normalization or terminal-status mapping. The diff a human approves is rendered server-side into
  `ActionView.description` (RT-34, RT-35) and the adapter cannot re-read the tree anyway (I3).

### 0.2 Package layout (extends arch §3, L3 §0.2, L4 §0.2)

```
pkb/
├── contracts.py                 # THE SEAM — gains CANCELLED_MESSAGE and terminal_status()
│                                #   (decision W, TG-50)
├── clients/
│   └── approval.py              # gains truncate(..., marker=…) (decision U, TG-56)
├── service/
│   ├── __init__.py              # gains the TelegramStore Protocol (decision S)
│   └── telegram.py              # pkb_telegram_{updates,chats,prompts} on Layer 3's connection
└── server/
    ├── telegram.py              # THE ADAPTER. All logic. NO http import, NO os/tempfile, NO uuid.
    │                            #   A SIBLING MODULE, NEVER A PACKAGE (TG-5, P-24)
    └── telegram_api.py          # the ONLY httpx module: six Bot API calls, redaction, retry_after
```

`pkb.daemon` gains the wiring: it reads the token and the mapping, opens the store, and hands
`ServerConfig.telegram_task` a closure over all four (TG-14). **`ServerConfig` does not change.**

---

## 1. Rule table

### 1.0 Already ruled — restated so this document is self-contained

*Ruled 2026-08-07, recorded in the [Layer 3 rules](2026-08-07-pkb-service-server-layer3-rules.md)
"Rulings of 2026-08-07". These are not re-litigated here; TG-5 onwards build on them.*

| ID | Rule (ruled 2026-08-07) | Sev |
|----|-------------------------|-----|
| TG-1 | The daemon holds a **`chat_id` → `agent_id` mapping**; an inbound message is addressed to the agent its chat maps to. **`/connect` is gone**, and with it the "which expert am I talking to?" ambiguity that made a mis-sent note land in the wrong topic. **Amended 2026-08-09** (§9, decision Y): the unit is the **channel** `(chat_id, topic_id)`, not the chat — a *topic* maps to exactly one agent, and `topic_id == 0` is General. The guarantee is unchanged in force; what is removed is the ceiling of one agent per human, which one bot's one private chat imposed and which made this rule's addressing correct and useless past the first expert. | error |
| TG-2 | A message from an **unmapped** chat is answered with instructions for mapping it, never routed to a default agent. Silently defaulting is how material lands in the wrong topic. **Amended 2026-08-09**: "unmapped **channel**" (TG-74). An unbound topic inside a mapped chat is a case that did not exist before §9, and answering it from the chat's General agent would be exactly the silent default this rule forbids. | error |
| TG-3 | Creating a topic does **not** create a Telegram channel. The mapping is human-configured; the daemon reports agents that have no chat so the human can add one. **Amended 2026-08-09** (decisions AA, AB): "agents that have no **channel**", and "human-configured" now means *the human types `/channels <agent-id>`* rather than *the human hand-edits an id*. The substance is untouched — the daemon still never decides what is reachable from the phone (TG-76) — but a topic id is minted by Telegram, invisible in every client and unenumerable afterwards (F-5), so hand-editing it is not a workflow that exists. | warning |
| TG-4 | `origin_channel` on a thread records that it came from Telegram, so a conversation started on the phone is recognisable in the TUI (D3's cross-channel resume). **Restated** — see C-29: `OriginChannel` is a closed four-value literal and cannot hold a chat id. | info |

---

### 1.1 The supervised task — lifecycle, supervision and `/health` — TG

*Everything here is a consequence of one executed fact: `_supervise` restarts `start()` forever,
carries no state across, and cancels nothing the previous invocation spawned.*

| ID | Rule | Source | Sev | Test assertion (live?) |
|----|------|--------|-----|------------------------|
| TG-5 | **`pkb.server.telegram` and `pkb.server.telegram_api` are two sibling modules under `pkb/server/`, never a package.** If a package ever becomes unavoidable, `tests/service/test_seam.py::_sources` must change `glob("*.py")` to `rglob("*.py")` **in the same commit**, with the planted-module counter-tests still failing as designed. | P-24 (executed); `test_seam.py::_sources` docstring, which names `pkb/server/telegram.py` as the reason discovery matters | error | Executed both ways: as a module, a planted `os`/`uuid`/`mkdir` violation **fails** SV-10 and SV-22; as `telegram/api.py` the identical code **passes both**. The step-5 suite re-plants both and asserts the failure. **no key** |
| TG-6 | **The task never returns while the daemon is serving.** Its loop is `while True`; the only exits are `CancelledError` at shutdown and an exception. There is no reachable top-level `return` or `break`. | `_supervise` (executed); `SubsystemState.healthy`; AP-17, AP-18 | error | Executed: a task that returns cleanly leaves `state == "stopped"`, `healthy == False`, `last_error == None` — `/health` reports `degraded` **permanently, with no reason and no restart**, and `_supervise` itself returns so nothing revives it. AST check: no top-level `return`/`break` in the loop body. **no key** |
| TG-7 | **Every coroutine the task starts lives in one structured group** (`anyio.create_task_group` / `asyncio.TaskGroup`), so an exception cancels and awaits every child before `_supervise` calls `start()` again. Bare `asyncio.create_task` at task scope is forbidden. | P-23 (executed); arch §8's failure table | error | Executed against the real `_supervise`: a task spawning one child and raising left **three live pollers after three generations**. The regression test drives three generations and asserts exactly one child alive at any moment; grep finds no `asyncio.create_task` in either module. **no key** |
| TG-8 | **Transient transport failures never reach `_supervise`.** `408`, `429` (honouring `parameters.retry_after`), `5xx`, connection errors and read timeouts are handled inside the poll loop with their own bounded backoff. Only programming errors and unrecoverable configuration errors escape. | AP-17; `SubsystemState.restarts`; CLAUDE.md's failover precedent ("only quota, concurrency and availability fail over") | error | A fake returning 429 `retry_after: 3`, then 500, then 200: `state.restarts == 0`, the loop slept ≥3 s, the update was processed exactly once. **no key** — *Why*: `restarts` is the number arch §8 asks to be visible; if a dropped packet increments it the human learns to ignore it. And `_supervise`'s `backoff` is initialised **outside** its `while`, so six blips leave the bot at a permanent 60 s restart delay for the life of the process. |
| TG-9 | **A `409 Conflict` from `getUpdates` is not retryable and not fatal to the task.** It means a second consumer of the same token exists — a second daemon, an orphan poller (TG-7), or a webhook. The adapter records the reason and both causes in `last_error`, logs at error level, **stops polling**, and re-probes on a slow fixed interval. It never backs off into a hot restart loop and never returns. | Telegram's single-poller constraint; AP-17, AP-18; TG-6 | error | A fake returning 409: polling stops within one cycle, `health.telegram.last_error` names both causes, `restarts` stays 0, the task is still alive, and a later 200 resumes it. **no key** |
| TG-10 | The task closes its HTTP client and its store handle in a `finally` that runs on **both** the exception path and the `CancelledError` path, and it re-raises `CancelledError` untouched. Unlike an SSE route, this `finally` **may await**. | `_supervise`; AP-12; ST-1; P-30 (executed) | error | Fifty forced restarts leave exactly one open client; cancelling the task re-raises within one loop turn with both resources closed. Executed: an `await` inside a bare `asyncio.Task`'s `finally` **survives** cancellation (unlike the ASGI cancel scope `routes.py:288` documents) — so a shutdown farewell message is possible and must not be dropped for a rule that does not apply here. **no key** |
| TG-11 | **The `telegram` block of `/health` gains four fields**: `chats: int`, `unmapped_agents: tuple[str, ...]`, `last_poll_ok_at`, `last_send_error`. `unmapped_agents` is computed **in the `/health` endpoint** as `{d.agent_id for d in service.list_agents()} - set(mapping.values())` — one set difference over the cached catalog, on a code path that already calls `list_agents()` (`app.py:148`). An amendment to AP-18/AP-19. **Amended 2026-08-09** (§9): `health.telegram.agents` — the set the endpoint subtracts — now carries the union of the mapping's values **and** the channel directory's agents, seeded from `store.channel_agents()` by the **daemon** at composition time and updated by the adapter on every create; and the block gains `topics_enabled`, `channels` and `retired_channels`. The `/health` endpoint itself does not change. Seeding in the composition root rather than in the bot is what preserves this rule's stated property — the answer survives a crash-looping bot, which is when `/health` is read. | TG-3; AP-18, AP-19; `HealthState.payload` (read) | error | After `create_topic`, `GET /health` lists the new agent in `telegram.unmapped_agents`, and still makes zero filesystem and zero model calls. With the telegram task cancelled the field is **still** correct. **no key** — *Why*: computing it in the bot means the answer disappears exactly when the bot is crash-looping, which is when the human is reading `/health`. |
| TG-12 | **`state == "running"` means the task is alive, not that Telegram is reachable.** Connectivity is `last_poll_ok_at`, written by the adapter after each successful poll. Nothing infers reachability from `state`. | `app.py:238-241` (executed); AP-17 | warning | Executed: `_supervise` calls `state.running()` **before** awaiting `start()`, so on the first line inside the task body `health.telegram.state` is already `"running"` and `health.status` is already `"ok"`. A human debugging a wrong token otherwise sees `status: ok, restarts: 0` for the whole first 30 s poll and learns nothing. **no key** |
| TG-13 | The bot **never** makes `/health` non-200 and **never** sets `status: degraded` for a send failure. `degraded` keeps its narrow meaning — an enabled subsystem is not running. A send failure is reported in `last_send_error` with `status` unchanged. | AP-18; D9; `HealthState.status` | error | A persistent send failure: `/health` is 200 with `status == "ok"` and `telegram.last_send_error` populated. A crash loop: still 200, `status == "degraded"`. **no key** — *Why*: a 503 invites the restart D9 forbids, killing in-flight runs and pending approvals that are perfectly healthy; and `degraded` widened to "something is a bit wrong" fires constantly and gets muted. |
| TG-14 | **The task is constructed in `pkb.daemon`** as a closure over the token, the mapping, the allow-list, the `HealthState` and the store, and passed to `ServerConfig.telegram_task` as the one-argument callable it already is. **`ServerConfig` does not change** and `pkb.server.app` is not touched. | `ServerConfig.telegram_task` (built); `daemon.build_app`'s `record_proposal`/`record_flush` precedent; arch §3 decision B | error | `build_app(..., telegram=TelegramConfig(...))` yields an app whose `/health` reports `telegram.enabled` true and reaches `running`; with no config, `state == "disabled"` and `status == "ok"`. Today `daemon.py` never sets `telegram_task` at all — wiring it is part of step 5. **no key** |
| TG-15 | **No raw `httpx` exception reaches `_supervise`.** `telegram_api.py` catches transport errors and raises a typed error whose message contains **no bot token**, and never calls `raise_for_status()` on a response whose request URL carries the token without redacting first. | P-25 (executed); `SubsystemState.failed` → `HealthState.payload` → the unauthenticated `/health` body | error | Drive the adapter against a fake returning 401: `health.telegram.last_error` names the failure and **does not contain the token**. **no key** — *Why*: a 401 is the *most likely* first failure of a new deployment, so the leak fires exactly when the human is pasting `/health` output to ask for help. |
| TG-16 | The adapter **filters or silences `logging.getLogger("httpx")`** and redacts the token from anything it logs itself. The token never appears in a `repr`, a log record, `/health`, or a chat message. | P-25 (executed); `daemon.main`'s `basicConfig(level=INFO)` | error | Run the adapter against a fake with `caplog` at INFO: **no record contains the token**. Executed today: httpx emits `HTTP Request: POST https://api.telegram.org/bot<TOKEN>/getUpdates "HTTP/1.1 200 OK"` at INFO, once per long poll, forever. **no key** |

---

### 1.2 Addressing, configuration and authorization — TG

| ID | Rule | Source | Sev | Test assertion (live?) |
|----|------|--------|-----|------------------------|
| TG-17 | The `chat_id → agent_id` mapping is **deployment configuration**, held on the daemon's telegram config (a `Mapping[int, str]`), loaded by the composition root and passed to the adapter as an argument. It is never in the knowledge base, never in `threads`, and **never mutated at runtime by the bot**. **Amended 2026-08-09** (decision AB): what the bot may never write is the **human's decision** about which agents are reachable and what they are called. The channel directory (`pkb_telegram_channels`, TG-77) is not that decision — it is the *address* Telegram minted in reply to a `/channels` command an owner typed, and a topic id cannot be configuration because it is invisible in every client and unenumerable afterwards (F-5). This rule's own stated reason survives word for word: nothing changes without the human. One carve-out is named rather than hidden — TG-82's bounded recreation writes a new topic id with no new command, because it re-addresses an existing decision instead of making a new one. | TG-1, TG-3; I3, SV-22; `ServerConfig` docstring: *"Never read from the knowledge base"* | error | grep finds no write path to the mapping in `pkb/server/telegram.py`; the module reads no file and no env var. **no key** — *Why*: a mapping in the KB is a Layer 3 write under `kb_root` **and** a binding the agents can edit; a mapping the bot writes is one that changes without the human, which is the mis-file TG-1 exists to prevent. |
| TG-18 | The mapping is **validated against `service.list_agents()` at startup**, and an entry naming an agent that does not exist is **reported, not fatal**: the daemon starts, `/health` marks the entry invalid, and that chat is answered exactly like an unmapped one (TG-2) rather than routed anywhere. | TG-2, TG-3; RG-13/`UnknownAgentError`; arch §8 | error | A mapping naming `topic/nope` starts the daemon; `/health` lists it invalid; a message in that chat runs no agent and gets the TG-2 reply. **no key** — *Why*: a topic can be renamed under a running config. Fatal-on-startup means a rename takes the daemon down; silent fallthrough is the mis-file TG-1 was ruled to stop. |
| TG-19 | **Only private chats are eligible.** A message whose `chat.type != "private"` runs nothing, creates no thread and gets at most one refusal; a group or channel id is **refused in the mapping at startup**. | arch §10 ("multi-user unnecessary; the daemon binds to localhost"); D9; decision X | error | An update with `chat.type == "supergroup"` produces zero `create_thread`/`start_run` calls; a mapping containing a group id is rejected at startup. **no key** — *Why*: a group is many senders with no identity check against a KB with no auth and no undo. Telegram's group privacy mode also silently drops most messages, so a mapped group half-works, which is worse than refusing. |
| TG-20 | **Approval and ingestion are authorized per *user*, not per chat**: `owner_user_ids` is checked on every `message.from.id` and every `callback_query.from.id`, independently of the mapping. A mapped chat with a non-allowed sender is ignored silently; a non-allowed callback gets a refusal alert and issues **zero** `resume` calls. | decision X; AP-20; TG-1, TG-2; D6 | error | Three updates — mapped+allowed, mapped+other, unmapped+other: one `start_run`, zero replies for the second and third. A callback from a non-allowed user: refusal alert, zero `resume`, keyboard intact. **no key** |
| TG-21 | The reply to an **unmapped** chat contains **exactly two facts**: the chat id, and where the human adds it. **No** agent ids, no topic titles, no thread ids, no counts, no `kb_root`. `service.list_agents()` is not called on that path. | TG-2; arch §10; decision X | error | An update from an unknown chat: the outbound text contains the chat id and **no string from `list_agents()`**; a spy asserts `list_agents` was not called. **no key** — *Why*: the bot's username is discoverable, so any stranger can produce an unmapped chat, and the **topic titles are the sensitive part** of a private knowledge base. The chat id is worthless to a stranger and is the one datum the owner cannot get any other way. **Amended 2026-08-09**: the two-facts limit applies to an **unmapped chat**, whose sender may be anyone who found the bot's username. In an unbound **topic** of a mapped chat the allow-list has already admitted the sender (TG-20 runs first, as built), so that reply may list the agent ids that have no channel (TG-74) — withholding them there makes `/channels <agent-id>` unusable, because the phone has no other way to learn an agent id. |
| TG-22 | The unmapped path **runs nothing and stores none of the human's content** — no `create_thread`, no `start_run`, no thread row, no message text anywhere, no retry-later buffer — and the reply says so in one line ("I have not kept this message"). **Amended 2026-08-08:** an **offset-only** ledger row (`update_id`, `chat_id`, `kind` — never the text) is *required*, not forbidden. | TG-2; I3; D6; TG-29, TG-30 | error | Spy service: zero calls on the unmapped path; the reply text asserts the message was not stored; the ledger row for that update carries no message text. **no key** — *Why*: the two failure modes are symmetrical and both silent — half-stored text reappears in an unexpected topic later, and silently dropped text leaves the human believing it was filed. The amendment resolves a real collision with TG-29 (claim **before** dispatch) and TG-30 (offset = `MAX(update_id)+1`): without the row the offset stalls and Telegram redelivers the same stranger's update for 24 hours, so "stores nothing" as written would produce an infinite refusal loop against the owner's own send budget. What the rule is actually about is the human's *content*, and none of it is stored. |
| TG-23 | The unmapped-chat reply is **rate limited to one per chat per window**; a repeat within the window gets silence. **Amended 2026-08-09**: one per **channel** per window, and `_WARNED_CAP` bounds channels rather than chats. An unbound topic and an unmapped chat are different explanations, and sharing a window means the first unbound topic silences the explanation the second one needs. | TG-2; Telegram's per-chat rate limit | warning | Ten updates from one unknown chat produce **one** outbound message; ten from each of two unbound topics produce **two**. **no key** |
| TG-24 | **The bot token arrives as a constructor/closure argument from `pkb.daemon`.** It is never read from the environment inside either telegram module, and never appears in `repr`, `str`, `/health` or a log record. **Amended 2026-08-08:** the same holds for the **allow-list**, which is now the environment variable `PKB_TELEGRAM_OWNERS` beside `PKB_TELEGRAM_TOKEN` (Q25) and reaches the adapter the same way, as `owner_user_ids` on the config. Reading either name is `pkb.daemon`'s job alone; a grep for either across `src/` finds exactly that module. | TG-15, TG-16; SV-22 (executed: `os` is a built offender) | error | grep: no `environ`, no `getenv` in `pkb/server/telegram*.py`; the adapter's `__repr__` omits the token. **no key** — *Why*: the module **cannot** import `os` without failing a built test, so the composition root is the only place the secret can be read — which is also the right place, since `pkb.daemon` already owns every other deployment decision. |
| TG-25 | **Two chats may map to the same `agent_id`**, and this is stated rather than rejected. The dangerous direction — one chat addressing two agents — is impossible by the mapping's type. **Amended 2026-08-09**: two **channels** may address one agent *across chats*, deliberately and visibly, exactly as before — but **never two channels in one chat** (TG-77). Within one chat that is the human's Cooking history split in half, invisibly, which the cross-chat case never was. | TG-1; Q26 | info | A mapping with two chats on `topic/cooking` starts; each holds its own independent thread. `unmapped_agents` is computed from `set(mapping.values())`, never from a length comparison. **no key** |

---

### 1.3 Threads, the chat binding and durable state — TG

| ID | Rule | Source | Sev | Test assertion (live?) |
|----|------|--------|-----|------------------------|
| TG-26 | **One *current* thread per chat.** **Amended 2026-08-09** (decision Y): per **channel** — the binding is keyed `(chat_id, topic_id)`, and the §8.3 agent-mismatch rotation is per channel too. One thread per *chat* under topics would put every expert in that chat into one conversation, which is the mis-file TG-1 exists to prevent and worse than the original, because the human sees distinct topics and reasonably assumes distinct threads. The chat holds a `chat_id → thread_id` binding; the first message with no binding calls `create_thread(agent_id, origin_channel="telegram")` and then `start_run`, and every later message calls `start_run` on the bound thread. | TG-1, TG-4; SV-6, SV-10; MC-11's twin shape; Q24 | error | Two messages in one mapped chat produce **one** `create_thread` and two `start_run` calls on the same thread id. **no key** |
| TG-27 | **Rotation is explicit only (`/new`).** Never on a timer, never on message count, never after a refusal, never after a restart. **Amended 2026-08-09**: `/new` rotates the **channel** it was typed in and no other (TG-86). A `/new` in General that also rotated Cooking would be an invisible rotation of a conversation the human was not looking at — the failure class this rule names. | TG-1's motivation (a mis-sent note with no undo); D6; TU-10/TU-11's twin (the human picks the thread) | error | Fifty messages over a simulated month use one thread; `/new`, and only `/new`, produces a second `create_thread`. **no key** — *Why*: every automatic rotation is invisible, and an invisible rotation is the same failure class TG-1 was ruled to fix. The accepted cost — an unbounded checkpoint on a long-lived chat thread — is measurable, visible and mitigable later, which a silent split is not. |
| TG-28 | **The bot's durable state lives in `pkb_telegram_*` tables on the checkpointer's SQLite file**, reached through the `TelegramStore` Protocol, on a connection with `isolation_level=None`, in **single short autocommit statements** — never a transaction held across an `await`. It opens nothing and writes nothing under `kb_root`. **Amended 2026-08-09**: the tables gain `topic_id INTEGER NOT NULL DEFAULT 0` and a fourth table, `pkb_telegram_channels` (TG-77). The migration is **additive only** — `ADD COLUMN` guarded by `PRAGMA table_info`, plus `CREATE UNIQUE INDEX IF NOT EXISTS` — **never a table rebuild and never a primary-key change**, because a rebuild on the checkpointer's own connection at startup is precisely the long transaction ST-3 measured as fatal. `NOT NULL DEFAULT 0` rather than a nullable column: SQLite treats NULLs as distinct in a unique index, so a nullable topic would let two General rows coexist for one chat. Existing rows become General bindings, which is exactly right — the conversation the human was already having continues in General. | decision S; ST-1 … ST-4, ST-7, ST-8; I3, SV-22 | error | AST check: no `await` inside a transaction in `pkb/service/telegram.py`; a concurrency test hammering the store during a live run yields zero `database is locked`; every file under a fixture `kb_root` is byte-identical (mtimes unchanged) across a full bot session including an approval. **no key** — *Why*: ST-3 was **measured** — a handler holding `BEGIN IMMEDIATE` across an `await` killed a concurrent checkpointer run after the victim's 5 s timeout, surfacing as a failed agent run with a written file and no flush. The bot writes on the inbound path, which is exactly when a run is streaming. |
| TG-29 | **At-most-once agent execution.** The `pkb_telegram_updates` row is written **before** `service.start_run` is called; the poll offset is `MAX(update_id) + 1` from that table. A crash in the gap leaves a row saying so, and the bot tells that chat on restart ("I lost your message sent at 14:02 — please send it again"). **Amended 2026-08-09**: the ledger records `topic_id` beside `chat_id`, so the notice reaches the **channel** that lost the message. §8.3 already had to fix this once in the chat dimension — the notice was broadcast to every mapped chat and reached, for the ordinary deployment, none — and reaching the right chat but the wrong topic is the same defect one level down. | decision T; AP-17; ST-3, ST-7, ST-9; D6 | error | Deliver update 100, kill the task after the row and before `start_run`, restart: the next `getUpdates` uses `offset=101`, `start_run` was called **zero** times for it, and the chat receives the lost-message notice. Mirror: kill after `start_run` returns — exactly one `start_run` across the restart. **no key** — *Why*: a repeated turn is a repeated write to a tree with no undo, and the second run's content differs from the first, so the human ends up with two versions of a note they wrote once. A named loss is survivable; a duplicated KB write is not. |
| TG-30 | **A cold start with no recorded offset drains and discards the backlog**: one `getUpdates(offset=-1)` to learn the last id, acknowledge it, and one notice per mapped chat that anything sent while the bot was down was not filed. No turn runs for a backlogged update. | Telegram's 24-hour retention; D6; TG-2's reasoning | error | A fake with 40 pending updates and an empty ledger: **zero** `start_run`, the offset lands past the last update, one notice per mapped chat. **no key** — *Why*: the first start after enabling the bot — or any start where the SQLite file was moved or reset — otherwise replays up to a day of chat traffic as agent turns. The human's model is "I turned it on"; the system's would be "I have 40 messages to file". |
| TG-31 | **On restart the bot re-syncs; it never replays.** For each chat with an unfinished recorded update: (a) `get_thread(...).pending is not None` → re-post the approval keyboard; (b) else `service.attach(thread_id)` is not `None` → attach and render **only the terminal outcome**, discarding replayed frames; (c) else → post the last assistant message from `ThreadDetail.messages`, marked delivered late. **Amended 2026-08-09**: all three branches post into the **channel** the unfinished update came from, read from the ledger. A restart that re-posts Cooking's approval keyboard into General is TG-80's failure without the deletion — an approve button for an irreversible write under the wrong expert's name. | AP-9, RO-17; TU-33's twin; `RunHub` behaviour (executed) | error | Three scripted restarts, one per branch: (a) exactly one keyboard carrying the **live** `interrupt_id`; (b) exactly one message after reattachment and zero duplicates of pre-crash text; (c) `attach` returned `None` and the reply came from `ThreadDetail.messages`. **no key** — *Why*: `attach` replays from `seq 0`, so rendering the replay double-posts content already in the chat; and once the hub closes `attach` returns `None`, so a late restart cannot reach the reply through the supervisor at all. |
| TG-32 | **The bot never calls `service.cancel()`** as part of stopping, restarting or losing interest in a run. Closing a subscription detaches. `cancel` is reachable only from an explicit `/cancel`. | AP-7; D2; TU-36's twin | error | Kill the bot task mid-run: `supervisor.active == 1`, the run reaches `RunEnd`, the reply is retrievable via `get_thread`. grep: `service.cancel` appears in exactly one place, under `/cancel`. **no key** — *Why*: D2's promise is that a turn outlives the client that started it; an ingestion turn killed because the bot hit a 502 is that promise broken. |
| TG-33 | `create_thread` is called with `origin_channel="telegram"` **once**, and `origin_channel` is **never read in a conditional** — not to permit a run, not to permit a resume, not to filter `/threads`, not to pick a chat (the chat is selected by `agent_id`, TG-1). | TG-4; ST-13, RO-22; TU-17's twin | error | grep: `origin_channel` appears only as a keyword argument to `create_thread`, never in an `if`, a comprehension filter or a dict lookup. A `tui`-origin thread resumes through the Telegram path and stays `tui`. **no key** — *Why*: D3's promise is that a TUI-started thread is finishable from Telegram; one `if origin_channel == "telegram"` deletes it invisibly, in exactly the case the design is proudest of. |

---

### 1.4 The inbound surface — updates, refusals and commands — TG

| ID | Rule | Source | Sev | Test assertion (live?) |
|----|------|--------|-----|------------------------|
| TG-34 | `getUpdates` passes `allowed_updates=["message", "edited_message", "callback_query"]` **explicitly**. `channel_post`, `my_chat_member` and everything else are not subscribed to. **Amended 2026-08-08** from the two-name form: TG-35 requires an `edited_message` to be acknowledged exactly once, and an unsubscribed kind is never delivered, so the two-name subscription made TG-35 unreachable in production while leaving it drivable through `_dispatch` in a test — a rule that could only ever pass. The cost is stated and paid: `edited_message` is a *public* inbound kind, so TG-19, TG-20 and TG-23's guards must cover it, and they do — every kind goes through one admission check before `_dispatch` branches. **Amended 2026-08-09**: **unchanged for topics, and that is the rule** (TG-91). Topic lifecycle arrives as *service messages* inside `message`, so no new kind is subscribed — and **no update kind exists for a deleted topic** (F-5), which is exactly why TG-80 has to read the send response instead. Subscribing to something here would imply the deletion case is covered; it is not covered and cannot be. | Telegram Bot API; D6; TG-2 | warning | The literal `allowed_updates` value is asserted in the request the fake receives. **no key** — *Why*: naming the two kinds makes the bot's whole inbound surface auditable in one line, and every unsubscribed event still costs an offset slot and a ledger row. |
| TG-35 | An `edited_message` is **never re-run and never applied**. It is acknowledged once ("I already acted on the original — send the correction as a new message") and routed to no agent. | D6; RT-39; arch §8 | error | An `edited_message` for a processed update produces one reply and **zero** `start_run` calls. **no key** — *Why*: the turn on the original text has already run and may already have written. Re-running files near-identical material twice with no way to remove the first; ignoring it silently leaves the human believing the correction landed. |
| TG-36 | A **photo, document, voice, video or sticker** message is refused in one line and the human's **caption is quoted back**. The adapter downloads nothing and writes nothing. | SV-22 (executed); `pkb.sources.stage` writes into `<kb>/.inbox/`, i.e. **under `kb_root`**; MC-17 (`pkb_ingest` takes text only); C-36 | error | A photo update with a caption: zero service calls, one reply containing the caption text, **no file created anywhere**. **no key** — *Why*: there is no sanctioned path — the built SV-22 scan fails outright on a Layer 3 module that writes a file, and `PkbService` has no ingest-by-path method. Silently ignoring the attachment loses the caption, which is the part the human actually wrote. |
| TG-37 | **`ApprovalPendingError` from `start_run` does not rotate, retry or drop.** The adapter re-posts the pending approval's keyboard (built from `get_thread(thread_id).pending`), **quotes the human's message back verbatim**, and says it was not sent. The update is still recorded so it is not replayed. | RT-39; `approval_pending` is deliberately absent from `RETRYABLE_CODES`; SV-16; CL-8; D6 | error | A stub raising `ApprovalPendingError`: no `create_thread`, no second `start_run`, no scheduled retry, and the outbound message contains the original text plus the approval buttons carrying the **current** `interrupt_id`. **no key** — *Why*: RT-39 exists because sending to an interrupted thread silently discards the interrupt. On a phone the original keyboard has scrolled away, so saying "there is a pending approval" without showing it makes the state unresolvable from the channel the human is in. |
| TG-38 | **`ThreadBusyError` is reported as correct behaviour** — "still finishing your last message; send that again in a moment" — with the message quoted back, **no automatic retry** and no rotation. The update is recorded. | RT-45; AP-13 (measured: the slot is held +3 s to +5 s after a disconnect); TU-35's twin; `thread_busy` **is** in `RETRYABLE_CODES` | error | Two messages while the first run is live: exactly one `start_run`, the second gets the busy wording (not the word "error"), the ledger row exists, no retry is scheduled. **no key** — *Why*: on a phone this is the **normal** case — people send three lines as three messages. Presented as an error it reads as a broken daemon; retried automatically it is a second POST against a thread that may already have written. |
| TG-39 | **The command surface is `/new`, `/threads`, `/agents`, `/pending`, `/cancel`. There is no `/connect`.** `/agents` and `/threads` answer only an allow-listed sender in a mapped chat (TG-20, TG-21). **Amended 2026-08-09** (TG-86, TG-87): six commands, gaining **`/channels`**, and every one of them acts on the **channel** it was typed in — including `/cancel`, which must reach that channel's run and no other, and the turn lock, which becomes per channel (TG-93). **There is still no `/talk`** and no in-band agent selector: it would restore the hidden "current agent" `/connect` was deleted for, while a topic title is visible above the keyboard on every send and a mode is not (decision AF). | TG-1; L3 §7 "Step 5"; SV-19; TU-36's twin | warning | `/cancel` during a live run calls `service.cancel` with the run id of that chat's most recent run and the chat is told it was **cancelled**, not that it failed; `/connect` is not a command. **no key** — *Why*: a turn is ~16 s on the cloud model and **284 s** on the local fallback; without `/cancel` a human who realises they sent the wrong thing cannot stop a turn that is about to write to a tree with no undo. |
| TG-40 | `/threads` lists that chat's agent's threads in **server order, verbatim** (pending-approval rows first, then `updated_at DESC`). ~~and rebinding the chat to one of those ids is the supported cross-channel resume (D3)~~ — **struck, Amended 2026-08-09 (approved defect 4)**: nothing rebinds a chat to a listed thread, no command does, and none is added by §9. `/threads` is a **read-only listing** and its own text now says so. What is true is stated in its place: a Telegram-started thread is visible and finishable in the **TUI** (D3, `origin_channel="telegram"`), and a TUI-started thread's **approval** reaches the phone through `/pending` (Q19(a)). The listing is per **channel** (TG-86). The bot applies no client-side sort, and **never invents or derives a thread id** — if it ever must construct one it imports `expert_thread_id` from `pkb.contracts`. | RO-6, RO-7, SV-6, SV-10, RT-36; TU-10, TU-26's twins | error | `/threads` order equals `list_threads(agent_id)` order; grep finds no `"::"` literal, no id construction and **no rebind path from a listed id** in either module; the listing's own text does not promise one. **no key** — *Why*: that ordering is the design's answer to arch §8's headline scenario; re-sorting buries the thread the human came back to answer. A client-derived id resolves to the wrong agent and, under D6, shares a checkpoint with the wrong conversation — silently. The struck clause has been false since the rule was written, and it is the kind of false that costs a day: a reader implementing D3's story looks for the rebind, finds none, and cannot tell whether it was dropped or never built. Building it was considered and deferred with its reason — a thread id is 36+ characters typed on a phone (P-28 measured why ids and phones do not mix), and a durable rebind silently moves a channel into another conversation, which is TG-59's hazard one command earlier. |

---

### 1.5 The outbound surface — rendering, limits and rate — TG

| ID | Rule | Source | Sev | Test assertion (live?) |
|----|------|--------|-----|------------------------|
| TG-41 | The adapter consumes **`MessageComplete`** and never `MessageDelta`. No message is edited per token. | L3 §7 Step 5 ("never a `message.delta` consumer"); arch §5; TU-24's twin | error | A stub streaming 500 deltas then one complete produces **zero** `edit_message_text` calls and one `send_message`. **no key** — *Why*: Layer 2's token channel and fact channel are independent, so every assistant message arrives twice; Layer 3 is forbidden from fixing it (SS-13). Token-rate edits are also hundreds of calls per second against a documented one-per-second-per-chat budget. |
| TG-42 | **`RunEnd.final_text` is never rendered.** `RunEnd` is consumed for its terminal status only. | `agents/runtime.py::_deliver` (read: `yield MessageComplete(text=reply)` immediately followed by `yield RunEnd(final_text=reply)` with the identical string); TU-25's twin | error | A stubbed turn emitting deltas, one complete and a `run.end` sends **exactly one** message containing the reply once. **no key** |
| TG-43 | `ToolStart`/`ToolEnd` produce **no message**, and `SubagentStart`/`SubagentEnd` are coalesced into at most **one** line per run. | Telegram FAQ, verbatim: *"In a single chat, avoid sending more than one message per second… eventually you'll begin receiving 429 errors"*; TU-29's twin (deliberately divergent) | error | A fan-out to three experts with six tool calls sends at most one roster line and no tool lines. **no key** — *Why*: this is a deliberate divergence from TU-29, and the reason is not taste: a 4-expert fan-out's brackets alone are 8 messages in one turn, and measured, 7 unpaced messages went out in 8 ms against a ~1/s per-chat budget. The 429s that follow carry a `retry_after` that stalls the **approval** messages too. |
| TG-44 | **Every Telegram length budget is measured in UTF-16 code units** (`len(s.encode("utf-16-le")) // 2`), and the value handed to `truncate()` is a code-point budget conservative enough to satisfy it, with the result re-measured and re-cut. The limit constants live in one place with their unit named. | P-26 (executed); decision V; CL-22 | error | An all-emoji description round-trips through the adapter's budget and the sent text is ≤ 4096 UTF-16 units; a regression fixture pins the code-point/UTF-16 divergence (3517 chars = 6517 units, `was_truncated=False`). **no key** |
| TG-45 | **Splitting is on length, never on meaning.** Cuts fall on line/paragraph boundaries only, in order, with no summarizing, no reflowing, no reordering, no dropping and no per-part commentary beyond a mechanical `(2/4)` counter. The adapter never reads the text to decide where to cut, and never makes a model call. | LB-18; L3 §7 Step 5 ("never on meaning"); L3 §7 "Never, at any layer" | error | Golden test: a 12,000-character reply split into K parts reassembles **byte-identically**; every boundary falls on a newline; a fixture whose lines exceed the limit still cuts on a boundary. grep finds no summarizer, no `textwrap.fill` and no re-sort in the split path. **no key** — *Why*: LB-18 exists because a model asked to compose a reply claimed "the Cooking expert checked the knowledge base" when no expert ran. A transport that cut on meaning would be the same lie one layer down. A length cut can be wrong; it cannot be a lie. |
| TG-46 | **Server-derived text is sent with no `parse_mode`, or with every byte escaped for the mode in use.** A `description` that is a real diff (`pkb.clients.approval.is_diff`) may go inside a fenced ```` ```diff ```` block, where only `` ` `` and `\` need escaping. KB content is never sent as bare MarkdownV2. | TU-41's twin (every widget showing server text is `markup=False`); Telegram's MarkdownV2 escaping rules; TU-40 | error | A description containing `@@ -1,3 +1,4 @@`, `1.5`, `(parens)` and `_underscores_` round-trips through the send path unmodified; a fake implementing the real parser's rule 400s on the unescaped form and accepts the fenced one. **no key** — *Why*: MarkdownV2 requires escaping ``_*[]()~`>#+-=\|{}.!`` and a unified diff is *made of* those characters. The result is a `400` — the approval is never delivered at all, the same class of failure as the `MarkupError` that killed the TUI. |
| TG-47 | Link previews are disabled on every outbound message (`link_preview_options.is_disabled = true`). | arch §10 (the daemon is personal and local); I3's spirit | info | Every `sendMessage` payload carries the disabled option. **no key** — *Why*: a note containing a URL otherwise makes Telegram's servers fetch it, turning private knowledge-base content into an outbound request to a third party the human never invoked. |
| TG-48 | **429 responses are honoured**: the adapter waits `parameters.retry_after` and re-sends. Outbound messages are queued per chat and never dropped. This is a **transport** retry of an idempotent send and is explicitly *not* the run retry TU-32 forbids — say so in the docstring. | Telegram rate limits; `ResponseParameters.retry_after`; D6; contrast TU-32, SS-5 | warning | A fake returning 429 `retry_after: 2` then 200: the message is delivered **exactly once**. A grep-level assertion that no code path re-issues `start_run` or `resume` on any transport error. **no key** — *Why*: a dropped reply after an approved write means the human never learns what was written, and there is no undo. A dropped keyboard means a parked interrupt nobody is told about. Conflating this with TU-32 is the mistake this rule exists to pre-empt. |
| TG-49 | **The event pump never blocks on a Bot API call.** Frames are drained into a small bounded outbox consumed by a separate task in the same group (TG-7); under pressure the adapter drops **progress** frames only and never a `MessageComplete`, an `InterruptEvent` or a terminal frame. | `service/runs.py::SUBSCRIBER_QUEUE_SIZE = 256` (read); AP-8; TU-23's twin | error | A pilot emitting 300 frames while every Bot API call sleeps 1 s: the subscription is never dropped, every interrupt and complete is delivered, only progress frames are missing. **no key** — *Why*: `RunHub` drops a subscriber whose queue exceeds 256, and the drop path closes the stream **without a terminal frame** — so the loss is indistinguishable from an unknown outcome. One 429 with `retry_after: 30` inside the pump overflows 256 frames during a fan-out at model pace, and the approval the human is waiting for is exactly what gets lost. |
| TG-50 | **Four terminal states, no fall-through**, derived **in the adapter** because the in-process events carry neither: `completed`, `interrupted` (rendered as *awaiting you*, with the approval surfaced, never as done), `cancelled` (a `RunError` whose message is `CANCELLED_MESSAGE`, **not** an error), `error`. The derivation is the shared `pkb.contracts.terminal_status(event, interrupted=…)` (decision W), which `SseEncoder.status_for` also calls. | P-27 (executed); SS-9/`RUN_STATUSES`, AP-11, SS-15; TU-31's twin | error | Four stubbed runs produce four distinct messages; the cancelled one carries no error wording; `pkb.server.sse.status_for` and the adapter produce the same answer for the same event (identity test on the helper). grep: `"the run was cancelled"` appears in **no** telegram module. **no key** — *Why*: `RunEnd` has no `status` and `RunError` has no `code` in process (executed), so a client matching three states falls through to "done" on every provider failure. On a phone, "done" over an interrupted run means a pending, undoable write is never answered. |
| TG-51 | A stream that closes **without** a terminal frame is *outcome unknown*, re-synced with exactly **one** `get_thread` — never rendered as success and never as failure, and never re-started. | SS-7, SS-15; AP-7, AP-12; TU-33's twin | error | Kill the subscription mid-run: the chat receives an "outcome unknown" message, exactly one `get_thread` is issued, and no run is re-started. **no key** — *Why*: AP-12's farewell branch sits inside `async for event in subscription.events`, so a generator suspended on `__anext__` during a 16 s cloud call — or a 284 s local one — never sees `shutdown.is_set()` and gets no goodbye. This is the **primary** shutdown path. |
| TG-52 | Every `RunSubscription` is consumed inside `try/finally` whose `finally` calls `close` **synchronously**: `close = subscription.close; if callable(close): close()`. It is never awaited. | `service/runs.py` (read); the identical idiom at `routes.py:321` and `mcp.py:453`; C-31 | error | Killing the consumer without the `finally` leaves `supervisor.subscribers == 1`; with it, 0. A companion test pins that `await subscription.close()` raises `TypeError` (the docstring defect). **no key** |
| TG-53 | **A fan-out approval is not on the chat's own thread.** Recovery walks `get_thread(t).children` (or `list_threads(agent_id)`, already ordered pending-first) and checks each child's `pending`; it never concludes "no approval" from the parent's `pending is None`. **Amended 2026-08-09 (approved defect 3)**: a fan-out approval **names the expert and the derived thread id** in the message that carries its keyboard (TG-85), and it is posted to the **originating channel** — Q20 re-ruled and re-affirmed with per-expert channels available (TG-89). Measured on the shipped `_post_action`, the button message carried `tool · reason` and named neither, against Q20's own wording; under topics that becomes an approve button in the *Librarian's* channel for a write into Cooking, indistinguishable from the Librarian's own. | LB-16, CL-8; RO-6, RO-9; executed against the real `RuntimeService` | error | Executed: after one Librarian turn that routes to `topic/cooking` and gates, `get_thread(PARENT).pending is None` while `get_thread(PARENT).children == ['<parent>::topic/cooking']` and that child's `pending == 'int-7'`. The restart test asserts the keyboard is re-posted for a fan-out gate. **no key** — *Why*: the in-flight path is safe because the adapter keeps the `ApprovalRequest` it received; **recovery is not**, and the failure is silent — the human's buttons go dead after a restart and nothing logs it. |

---

### 1.6 Approvals and the inline keyboard — TG

*Arch §6: the approval surface "is the piece that matters most". Every rule here is about a write
with no undo (D6), decided on a phone.*

| ID | Rule | Source | Sev | Test assertion (live?) |
|----|------|--------|-----|------------------------|
| TG-54 | The keyboard is built by **`offered(action, drop=("edit", "respond"))` applied to the received `action.allowed_decisions`**, and the same verb table is read back to build the `Answer` — never a hardcoded approve/reject pair, never the `DecisionType` literal, and never a verb→type mapping written out separately from the one the keyboard draws from. **Amended 2026-08-08** to drop `respond` as well as `edit`: `validate_decisions` **requires** a message on a `respond` (*"it becomes the tool's result"*) and TG-65 forbids this channel demanding prose from a phone, so a `Respond` button could only ever submit something the daemon refuses — the same dead approval as a button Telegram 400s on, which is what this rule exists to prevent. Agrees with arch §6's "narrows to approve/reject"; no shipped `GATE_DECISIONS` row offers `respond`, and Q21(b) is deferred. An action left with nothing offerable becomes TG-55's hand-off. | arch §6 ("`edit` is impractical on a phone"); CL-9, CL-12, RO-15, RT-32; C-34 | error | Table test over all **twelve** `GateReason` rows: the offered tuple equals `tuple(d for d in GATE_DECISIONS[reason] if d != "edit")`; grep finds no literal `["approve","reject"]`. **no key** — *Why*: executed, dropping `edit` yields `('approve','reject')` for eleven reasons and leaves `delete`'s `('approve','reject')` untouched — so today the correct and the wrong implementation agree, which is exactly what lets the wrong one pass every test until a future gate allows `('edit','reject')` and the bot draws a button that 400s. `offered()` also preserves the server's ordering, which decides which button a hurried human presses first. |
| TG-55 | An action whose `allowed_decisions` is **empty as received** is rendered as a hand-off message with **no inline keyboard at all** — naming the agent, the topic and the thread id, and saying the approval is still parked and answerable from the TUI. Never an empty keyboard, never a silent skip. | CL-11; `agents/approval.py::_allowed_decisions` (returns `()` for a malformed `ReviewConfig`) | error | `ActionView(allowed_decisions=())` produces one message with `reply_markup=None` naming the thread id, and the adapter issues **zero** `resume` calls. **no key** — *Why*: `validate_decisions` then rejects every decision type, so an approval nobody can answer parks the thread forever and RT-39 refuses the next message on it — the chat is bricked with no visible cause. On Telegram a message with no buttons *at least* reads as a hand-off; an empty keyboard reads as a delivery failure. |
| TG-56 | **The deciding surface is never truncated.** The complete `description` is present in the chat **before** the buttons are: one message if it fits (TG-44), otherwise the whole text uploaded as an in-memory `sendDocument` immediately preceding a short button message naming the file, the reason and the tool. `truncate()` is used **only** for the preview inside the button message, and only when the full text is already in the chat — with an adapter-supplied `marker=` (decision U). **Amended 2026-08-09 (approved defect 3)**: the button message **names the agent**, always, and the thread id whenever the approval is parked on a derived thread or handed off (TG-85). A description is not self-identifying — it is a diff — and the one message where being wrong about *which expert* is an irreversible write to the wrong topic is this one. | decision U; TU-39; CL-22; RT-34; D6 | error | A 9,218-character description produces a `sendDocument` carrying the **byte-identical** description plus a button message whose preview ends in the adapter's marker; the concatenation of everything sent contains the description verbatim. No fixture ever attaches a keyboard to a message whose description was cut without the full text present. **no key** — *Why*: measured, a delete embeds the whole current file (7,868 chars) and a new-file write embeds the whole proposal (9,218). `truncate(…, 4096)` shows bullets 0–59 of 120 under an irreversible approve button. |
| TG-57 | **`callback_data` is `v1\|<handle>\|<index>\|<verb>` and carries nothing else** — no `thread_id`, no `agent_id`, no `chat_id`, no `interrupt_id`. `<handle>` is an opaque `secrets.token_hex(4)` key into the durable `pkb_telegram_prompts` row. The adapter validates the 64-byte budget itself and never recovers state by parsing `callback_query.message.text`. **Amended 2026-08-09**: **unchanged, and no topic id enters `callback_data`.** The handle already indexes the prompt row, and the **row** gains `topic_id` (§9.6.1). A chat id was already refused at 64 bytes (P-28); a chat id plus a topic id is further over, and the durable row exists for exactly this. | Telegram's 1–64 **byte** `callback_data`; TU-18's twin (rendering is never a wire protocol); TG-70 | error | Measured: `v1\|7f3a2b1c\|0\|a` is **15 bytes**; `a\|<32-hex interrupt id>\|0` is 36; adding a derived thread id (`<uuid4>::topic/cooking/grilling` = **60 chars**) makes it **97 bytes** — over the limit, and a fan-out approval is the common case. Property test: every emitted `callback_data` is ≤ 64 bytes for indices up to 99; a fixture pins the thread-id variant as rejected; grep finds no read of the callback's message text. **no key** — *Why*: neither PTB nor aiogram enforces the limit at construction; it 400s at the server, i.e. at the moment a human is waiting for an approval. |
| TG-58 | **A button press always re-reads the live approval before resolving.** Order: handle → `pkb_telegram_prompts` row → `get_thread(thread_id)` → `resolve(detail.pending, answers)`. Any in-memory map is a **cache**, never the authority. If neither the row nor a `list_threads` scan for `pending_interrupt_id` finds it, the human is told the approval could not be located and pointed at the TUI — the adapter never guesses a thread. | RT-38; TU-13's twin ("badge from the column, decide from the detail"); RO-9, SV-16; CL-8 | error | Resolve an approval through a **fresh** adapter instance that never saw the message: the durable row supplies the thread, `get_thread` supplies the request, the resume succeeds. With the row deleted and the column nulled, the adapter sends the hand-off and issues **zero** `resume` calls. **no key** — *Why*: this is the whole answer to "what happens to a button pressed after the daemon restarted". Telegram redelivers unconfirmed updates for **24 hours**, so a press made while the daemon was down arrives into an adapter with no memory of the message. Making the durable path the *only* path means the restart case is exercised by every test. |
| TG-59 | Decisions are posted to **`request.thread_id`** (what `resolve()` returns), never to the chat's bound thread, and answering an approval on a derived thread **does not rebind the chat**. | CL-8; LB-14, LB-16; TU-49's twin | error | A fan-out approval on `<parent>::topic/cooking` resolves against that id while the chat stays bound to `<parent>`. **no key** — *Why*: posting a delegate's decisions to the Librarian's thread is a 409 on a perfectly valid approval — the failure hardest to debug from a client. Rebinding on top would silently move the chat into the expert's conversation. |
| TG-60 | A multi-action approval is **one message per action**, each carrying that action's own full description (TG-56) and its own keyboard whose `callback_data` holds that action's index. Answers accumulate keyed by the prompt handle; when and only when all N are answered, `resolve()` is called **once** and `resume()` **once**. A partial set submits nothing, and the accumulator is validated against the freshly-read request's action count before submission. | RT-41; CL-5, CL-6; TU-43's twin; Q22 | error | A two-action approval posts two messages; pressing one button issues **no** `resume`; pressing the second issues exactly one whose decisions are in `request.actions` order. A press whose index exceeds the re-read request's count is refused with a hand-off, never padded. **no key** — *Why*: two constraints force the same shape — 4096 units cannot hold N descriptions, and CL-6 forbids padding a missing answer. It also gives TU-47's "later" for free: the human stops pressing, nothing is sent, the interrupt stays parked and answerable from the TUI. |
| TG-61 | **`answerCallbackQuery` is called first, unconditionally**, before `service.resume` and before any other slow work, on every path including the stale and refused ones. The outcome is reported afterwards by editing the message. | Telegram Bot API, verbatim: *"Telegram clients will display a progress bar until you call answerCallbackQuery… it is therefore necessary to react by calling answerCallbackQuery even if no notification is needed"*; CLAUDE.md's measured 16 s / **284 s** turns | error | A fake records call order: `answer_callback_query` precedes `resume` for every press. A `resume` that blocks 300 s still produced its answer within the first tick. **no key** — *Why*: a resume starts a turn of 8–12 model calls. Answer it afterwards and the button spins, the query expires, and the human — who has no other feedback — presses again, producing a second press against an interrupt the first already resolved. |
| TG-62 | On **`StaleInterruptError`** the adapter answers the callback with `show_alert=True` saying another channel already answered it, removes the keyboard, issues **exactly one** `get_thread` to re-read state, and **never retries**. | RT-40, RO-12, RO-14; TU-49's twin; D3, D6 | error | A resume against an already-answered interrupt: one alert (not a toast), one `get_thread`, zero further `resume` calls, keyboard removed. **no key** — *Why*: two channels on one approval is the design, not an edge case. An automatic retry either spins or applies answers the human gave to a **different** write. `show_alert` rather than a toast because a toast on a phone is missed, and the state the human then believes they are in is wrong. |
| TG-63 | **Every terminal outcome — approved, rejected, stale, error, unanswerable — removes the inline keyboard from all N messages** (`editMessageReplyMarkup` with no markup) and states the outcome in a **new** message. **Amended 2026-08-08** from "edits the text": overwriting the description destroys the chat's only surviving record of what was approved, on a system with no undo (D6), at the exact moment it starts to matter — a week later, when the human scrolls back to find out what they said yes to. Buttons and prose are separate concerns and Telegram gives them separate methods. "All N messages" includes the confirm step's own message (TG-64), whose id is recorded when it is sent. **Amended 2026-08-09**: clearing a keyboard needs **no topic** (F-6, TG-90) — `editMessageReplyMarkup` addresses a message by `chat_id` + `message_id`, in a topic exactly as in General, and the send family is the only family that takes `message_thread_id`. Stated on the rule because the natural assumption is the opposite, and acting on it puts an unknown parameter on the one call that disarms an irreversible button, inside a `finally`. | TU-47's twin; D6 | error | After a resume, every message of that approval is edited to `reply_markup=None`; a replayed press on a removed keyboard is answered with the stale alert and issues no `resume`. **no key** — *Why*: a TUI modal closes; a Telegram message lives in the chat forever with its buttons live. Without removal the human scrolls back a week later, presses approve on a write that already happened, and either gets a stale alert (lucky) or answers whatever interrupt is pending *now* (not lucky). |
| TG-64 | **Approve and reject are never in the same keyboard row**, and the three destructive reasons (`delete`, `topic-creation`, `conflict-resolution`) require a second tap: the first press replaces the keyboard with an explicit confirm/cancel pair naming the consequence. Those three carry the **"there is no undo"** line. | TU-50 (which explicitly notes it "documents the surface step 5 must offer through a different affordance"); TU-45; D6 | error | Golden keyboard table per `GateReason`: approve and reject in different rows for all twelve; the three destructive reasons emit a confirm step whose `callback_data` is still ≤ 64 bytes and render the no-undo line. **no key** — *Why*: on a phone, two buttons in one row *are* neighbouring keys, and a thumb on a moving train is a worse input device than a keyboard. `describe_write` already embeds the no-undo warning for `delete` only; the other two get it from the adapter, exactly as TU-45 requires of the TUI. |
| TG-65 | **A rejection is sent with no message**; the adapter never demands prose from a phone. | CL-13 (verified: `Decision(type="reject")` validates clean); `agents/approval.py::_harness_decision`; Q14 (RULED); Q21 | warning | A one-tap reject produces `Decision(type="reject", message=None)` and a body with a null `message`; the resume is accepted. **no key** — *Why*: `pkb.clients.approval` deliberately holds no policy requiring a reason precisely so both channels answer identically, and the harness substitutes its own "do not retry unless the user asks" text. A bot that required one would refuse a resume the daemon accepts — a client-only refusal, invisible server-side. |
| TG-66 | When `description` carries the **RT-35 validation label** (a prefix match on its exact text, never a re-run of `validate_content`), the button message leads with that label above everything else and says in one line that approving will still fail validation. `approve` stays offered. | TU-46's twin; RT-35; `agents/gates.py::_validation_label`; MW-14 | warning | An approval whose description carries the label renders it as the **first** line of the button message; a clean one renders no such line; the adapter never calls `validate_content`. **no key** — *Why*: legally `approve`/`reject` remain, but the human's intent is *fix it*, and `edit` is the affordance Telegram just removed. Approving a labelled draft burns one of MW-14's three attempts on content the human endorsed — and in a 4096-unit channel the label sits at the bottom of a 9,000-character description where nobody sees it. |

---

### 1.7 The transport port and the seam — TG

| ID | Rule | Source | Sev | Test assertion (live?) |
|----|------|--------|-----|------------------------|
| TG-67 | The adapter is written against a narrow **`BotApi` Protocol** — `get_updates`, `send_message`, `edit_message_text`, `edit_message_reply_markup`, `answer_callback_query`, `send_document`, `send_chat_action` — implemented over `httpx` in `pkb/server/telegram_api.py`. **`pkb/server/telegram.py` imports no HTTP client** and is fully drivable in process against a fake. | decisions Q and R; D9; arch §9 (free, fast, keyless, networkless); SV-4/SV-30's precedent | error | AST scan: `_roots(TELEGRAM_SOURCE) & HTTP_CLIENT_ROOTS == set()` for the logic module; the entire adapter suite runs against a fake with `socket` refused by `sys.meta_path`. **no key** |
| TG-68 | **MC-7 is amended before the adapter is written** (C-27): what is forbidden is an HTTP client used to reach *the daemon's own API*. Assert positively — `pkb/server/telegram*.py` import no `pkb.tui.client`, no `pkb.clients.sse`, no `pkb.agents`, no harness root; the only base-URL literal in either module is the Bot API's; and neither `127.0.0.1`, `localhost`, the daemon's port, nor a `/threads`, `/runs`, `/agents`, `/health` or `/mcp` path literal appears anywhere. `HTTP_CLIENT_ROOTS` is **not** widened and the mcp assertion is unchanged. | MC-7's own docstring ("a bot that curls its own daemon is a second process to supervise"); `tests/server/test_mcp.py:653`; P-22 | error | The existing MC-7 test gains a `TELEGRAM_SOURCE` case with `HARNESS_ROOTS`, the `pkb.agents` check, the loopback assertion, and `HTTP_CLIENT_ROOTS` applied to the **logic** module only. `uv run lint-imports` stays at **5 kept, 0 broken** (executed with a planted adapter). **no key** |
| TG-69 | **The HTTP read timeout strictly exceeds the long-poll timeout**, and the two constants sit next to each other with a comment saying why. The client keeps **two connection pools**, routing `getUpdates` to one and everything else to the other. The poll is cancellable at an `await` point and shutdown does not wait on the in-flight request. | Layer 4 P-11 (measured: httpx2's 5.0 s default read timeout "kills every real turn"); `pkb.tui.client.SSE_TIMEOUT` precedent; PTB's `Bot._do_post` endpoint routing (read) | error | A fake holding `getUpdates` open for 30 s completes one idle cycle with **zero** restarts under the shipped settings; a unit assertion that `READ_TIMEOUT > LONG_POLL_SECONDS`; a 25 s poll in flight does not delay an approval send; cancelling the task returns within one tick. **no key** — *Why*: a read timeout ≤ the poll timeout makes **every idle poll** raise, and `_supervise` turns that into a crash loop whose `last_error` reads "the network is down" when the configuration is wrong. This layer is paying the same bill Layer 4 already paid. |
| TG-70 | **Neither telegram module calls anything named `uuid*`.** No callback handle, correlation id or idempotency key is a minted uuid; handles are `secrets.token_hex`. | SV-10; `tests/service/test_seam.py::test_create_thread_takes_no_id_parameter_sv10` (executed) | error | Executed: a `pkb/server/telegram.py` containing `uuid.uuid4().hex[:16]` fails that built test with `At index 0 diff: 'telegram.py:_callback_token' != 'threads.py:mint_run_id'`. `uv run pytest tests/service/test_seam.py -k sv10` stays green after step 5. **no key** |
| TG-71 | **Neither telegram module writes a file**, by any path — no `os`, `shutil` or `tempfile` import, no `write_text`/`write_bytes`/`mkdir`/`unlink`/`rename`/`symlink_to`, no `open(..., 'w'\|'a'\|'x'\|'+')`. The TG-56 overflow document is uploaded from an **in-memory buffer** and never staged as a temp file. | I3, SV-22; `test_seam.py::_write_offenders` (executed) | error | Executed: a telegram module importing `os`/`tempfile` and calling `mkdir()`/`write_bytes()` fails SV-22 with four offenders. `uv run pytest tests/service/test_seam.py -k sv22` stays green; the document fixture asserts a `bytes` payload; the `kb_root` tree is byte-identical before and after a full approval cycle. **no key** — *Why*: `sendDocument` is the one place a Telegram adapter is tempted to write `/tmp/diff.md`. An in-memory buffer removes the temptation **and** the cleanup, and keeps I3 a structural fact about the package rather than a discipline about a temp directory. |

---

## 2. Where the docs are wrong about the packages and the built code

Rows **P-22 … P-33** were **executed** on 2026-08-08 in this repo's venv, against the real
`_supervise`, the real `HealthState`, the real `pkb.clients.approval`, the real `GATE_DECISIONS`,
the real `pkb.contracts` dataclasses and the repo's own `tests/service/test_seam.py`. Layers 3–4's
P-1 … P-21 are unchanged and still hold. Probe sources are under `/tmp/l5/`.

| # | Doc (or brief) says | Installed packages / built code actually do | Corrected approach |
|---|----------------------|---------------------------------------------|--------------------|
| **P-22** | The brief: MC-7's built test asserts `pkb.server.telegram` pulls no HTTP client. MC-7's own prose: "an import assertion that `pkb.server.mcp` **and `pkb.server.telegram`** pull no HTTP client." | **The test names only `MCP_SOURCE`** (three assertions, `tests/server/test_mcp.py:653-666`); "telegram" does not appear in the file. And a planted adapter importing `httpx`, `pkb.clients.approval`, `pkb.server.sse` and `pkb.service` gave `Analyzed 123 files, 627 dependencies. Contracts: 5 kept, 0 broken`. | Decision Q / TG-68: amend MC-7 to ban **loopback**, keep the literal no-HTTP-import assertion on the logic module, add a positive base-URL assertion, and extend the built test to cover the adapter so the second half of the rule stops being aspirational. Do **not** widen `HTTP_CLIENT_ROOTS` — that deletes the check on `mcp.py` too. |
| **P-23** | Arch §8: a bot crash is a supervised restart and "the daemon and in-flight runs are unaffected". | **`_supervise` cancels nothing the crashed invocation started.** Executed: `generations started = 3 \| live poller tasks = ['poller-1','poller-2','poller-3']` after 3.6 s, with `state.state == "restarting"`, `restarts == 3` and `/health` reading `running` for most of it. Also read: `backoff` is initialised outside the `while` and never resets, and a cancellation during the backoff `sleep` leaves `state == "restarting"` rather than `"stopped"`. | TG-7: one structured task group inside the task. **Do not change `_supervise`** — its simplicity is a virtue and only the task knows what it started. Record a note under AP-17 that `_supervise` supervises exactly one coroutine and cancels nothing; optionally reset `backoff` after a run that outlasted it (two lines, no behavioural risk). |
| **P-24** | "Split the httpx implementation into a subpackage so the assertion survives." | **A package is invisible to every Layer 3 seam scan.** `_sources` is `directory.glob("*.py")`, non-recursive. Executed: identical `os`/`tempfile`/`uuid`/`mkdir` violations **fail** SV-10 and SV-22 as `pkb/server/telegram.py` (`assert ['telegram.py: imports os', …] == []`, and `'telegram.py:_callback_token'`), and **pass both** as `pkb/server/telegram/api.py`. | TG-5: two sibling modules. If a package ever becomes necessary, `glob` → `rglob` in the same commit — noting that change newly covers `pkb/core/generators/`, `pkb/agents/middleware/` and `pkb/agents/tools/` and needs its own planted-module tests. |
| **P-25** | AP-18/AP-20: `/health` is a 200 with degradation in the body, on a daemon with no auth. | **The token leaks twice.** Executed: `HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.telegram.org/bot123456789:AAF-FAKE-TOKEN…/getUpdates'` → `SubsystemState.failed` → `payload()["telegram"]["last_error"]`, and `TOKEN IN /health: True`. Separately: httpx's INFO logger emits the full URL on every request, and `daemon.main` sets `basicConfig(level=INFO)`. | TG-15, TG-16, TG-24: catch and redact at the transport boundary, filter the `httpx` logger, and take the token as an argument (which SV-22 forces anyway, since the module cannot import `os`). |
| **P-26** | CL-22: `truncate` is the shared cut for a length-limited channel; Telegram's 4096 limit is its motivating case. | **Character-based, and the common case is silently over the wire limit.** Executed: `truncate("🔥"*3000 + …, 4096)` → `chars: 3517  utf16 units: 6517  cut: False`. Telegram counts UTF-16 code units and rejects at 4096. | Decision V / TG-44: the budget search lives in the adapter; `truncate` stays pure and channel-agnostic. And decision U / TG-56: for the surface a decision is made against, change the **container**, not the text. |
| **P-27** | An in-process caller sees what an SSE client sees, minus the framing. | **Three wire fields are authored by `SseEncoder` and exist on no dataclass.** Executed: `RunEnd ['run_id','final_text']`, `RunError ['run_id','message','retryable']`, and `'CANCELLED_MESSAGE' in dir(pkb.contracts) → False`. `status_for` distinguishes a cancellation by matching the private `_CANCELLED_MESSAGE = "the run was cancelled"`, and `RunSupervisor.codes` is empty on the cancel path because `_drive` records a code only in its `except Exception` branch. `interrupted` is per-stream encoder state. | Decision W / TG-50: hoist `CANCELLED_MESSAGE` into the seam and add a shared pure `terminal_status(event, *, interrupted)` that `SseEncoder` also calls. Note the asymmetry plainly: TU-* clients are **handed** `status` and `code` by the decoder; this one is not. |
| **P-28** | Nothing in the docs sizes `callback_data`. | **Nothing meaningful fits in 64 bytes.** Measured with the real seam: a derived thread id is `<uuid4>::topic/cooking/grilling` = **60 characters**; `a\|<thread>\|<32-hex interrupt id>\|0` = **97 bytes**; `a\|<interrupt id>\|0` = 36; `v1\|<8 hex>\|0\|a` = **15**. `Interrupt.id` is an `xxh3_128_hexdigest` (32 hex chars). Neither PTB 22.8 nor aiogram 3.30 enforces the limit at construction — both build a 65-byte button and 400 at the server. | TG-57: an opaque handle into a **durable** prompts row, and the adapter validates the budget itself. The lookup is mandatory, not an optimisation. |
| **P-29** | The bot can poll and reply from memory. | **`getUpdates` is at-least-once and confirmation is a side effect of the *next* poll.** Docs: *"An update is considered confirmed as soon as getUpdates is called with an offset higher than its update_id"*, and unconfirmed updates *"will not be kept longer than 24 hours"*. Measured: a loop received updates 1 and 2, handled both, stopped — and `fake.pending()` still listed `[1, 2]`; a restarted loop received update 1 again. | Decision T / TG-29, TG-30: a durable ledger written before dispatch, offset = `MAX+1`, cold start drains the backlog. The same 24-hour redelivery is what makes TG-58's durable prompt row load-bearing rather than defensive. |
| **P-30** | RO-/SS-'s "the `finally` must be synchronous" reads like a house rule. | **It is a property of the ASGI cancel scope, and does not apply here.** `routes.py:288` documents it for a response's anyio scope (level-triggered, so the first `await` in a `finally` re-raises). Executed on the shape `app.py:190-197` actually uses — a bare `asyncio.Task` cancelled mid-await with `await asyncio.sleep(0)` in its `finally` — the result was `['await-in-finally: SURVIVED', 'finally completed']`. | TG-10: the task's teardown **may** await, so an AP-12-style shutdown farewell to the chat is possible. `subscription.close()` is still called synchronously — because `close` **is** synchronous (C-31), not because of a cancel-scope rule. |
| **P-31** | Nothing in the docs sizes an approval message. | **The approval does not fit.** Measured on real `describe_write` output: a 120-bullet note approval is **9,218** characters, a delete of the same file **7,868** (a delete embeds the whole current file; a new file embeds the whole proposal), an edit **2,056**. And `is_diff()` false-positives on new-file content containing a literal `@@` — it is a bare substring search, not a line-anchored hunk check. | TG-56 (document, not truncation) and TG-46 (plain text is the safe rendering; if a fenced `diff` block is selected from `is_diff()`, tighten the predicate at its source rather than duplicating it). |
| **P-32** | Arch §6: "the Telegram adapter narrows `allowed_decisions` to approve/reject." | **True of today's table by coincidence.** Executed over all twelve `GateReason` rows: dropping `edit` yields `('approve','reject')` for eleven and leaves `delete` untouched — no shipped gate becomes empty. Which is exactly what makes the hardcoded implementation pass every test. | TG-54: derive with `offered(action, drop=("edit",))` and assert against the table, not against the literal pair. Worth a one-line correction to arch §6 so the sentence stops reading as a specification (C-34). |
| **P-33** | — (not in any doc) | **`RunHub` objects are never freed.** `RunSupervisor.forget()` and `finished()` exist with the docstring "the daemon's periodic sweep", and grep over `src/` finds **no caller**. `_drive`'s `finally` pops `_tasks` and `_by_thread` but leaves `_hubs[run_id]` holding up to `REPLAY_BUFFER_SIZE = 512` events, forever. | A Layer 3 defect, not step 5's — but flagged here because the bot's whole value proposition is a daemon that stays up for weeks (C-37). Add the sweep to `app.py`'s worker set with a grace period after the terminal frame, citing AP-9. |

---

## 3. Contradictions between the architecture, the Layer 3 rules and the built code

| # | Contradiction | Resolution | Why |
|---|---------------|------------|-----|
| **C-27** | **MC-7 as written is unsatisfiable for a Telegram adapter, and its built test does not cover one.** The rule bans an HTTP client in `pkb.server.mcp` *"and the Telegram adapter"*; the Bot API is HTTPS-only at `api.telegram.org`, with no non-HTTP transport short of MTProto. The built assertion runs `_roots(MCP_SOURCE)` alone. `pyproject.toml`'s fifth-contract comment repeats the claim as settled fact. | **Amend MC-7 to say what it means: no HTTP client is used to reach the daemon's own API.** Keep the literal no-import assertion on the adapter's **logic** module (decision Q makes that structural), add a positive base-URL assertion, and extend the built test to the new module (TG-68). Do not widen `HTTP_CLIENT_ROOTS`. | The rule's own docstring already states the real intent — *"a bot that curls its own daemon is a second process to supervise, a second failure mode and a second copy of the error table"*. Egress to a third-party API is not that. Left as written, the first honest implementer either breaks a shipped rule or reaches for a webhook, which needs a public TLS endpoint on a daemon with no auth (AP-20). |
| **C-28** | **`pkb.server.mcp` already violates MC-7's own "never `pkb.server.sse`" clause.** `mcp.py:58` does `from pkb.server.sse import thread_for_event`, used at line 556; it also imports `pkb.packs`, `starlette`, `pydantic` and `mcp`, so "imports only `pkb.contracts` and the Protocol" is prose, not fact. No test asserts that clause. | **Either drop the clause and record the precedent, or move `thread_for_event` down into `pkb.contracts`** next to `expert_thread_id` and `validate_decisions`. **Prefer the move.** | The same argument that put `expert_thread_id` in the seam applies word for word: both sides must answer identically and a second implementation of an id convention fails silently. The adapter needs `thread_for_event` for the same reason MCP does — and executed, only `InterruptEvent` knows its own thread; everything else is derived, and the derivation is **catalog-gated** so an expert's internal `general-purpose` delegation (RT-44) stays on the expert's thread rather than getting an invented one. |
| **C-29** | **TG-4 says `origin_channel` "records the chat it came from"; the type cannot hold a chat.** `OriginChannel` is `Literal["tui","telegram","mcp","http"]`, defined once (ST-13) and asserted by `tests/service/test_threads.py:894`. ST-5 fixes the `threads` columns at seven with an explicit note that nothing else may be added. | **Restate TG-4**: a Telegram-created thread is stamped `origin_channel="telegram"`; **which chat** it came from is the adapter's own binding (TG-26, TG-28), not a column. Do not add a `chat_id` column. | Nothing outside the adapter consumes a chat id; ST-13 already declined a "last seen channel" column for the same reason; and one more channel-specific column on `Thread` is the first step toward the `if origin_channel == …` RO-22 forbids (TG-33). |
| **C-30** | **TG-3 is not satisfiable through the slot Layer 3 built.** `HealthState.payload()`'s telegram object has exactly six fields, none of which can carry a list; `ServerConfig.telegram_task` is typed `Callable[[PkbService], Awaitable[None]]` and receives neither the health state nor the mapping; and `pkb/daemon.py` never sets `telegram_task` at all. | **TG-11 + TG-14**: add the four fields to the telegram block, compute `unmapped_agents` **in the `/health` endpoint** (which already calls `list_agents()`), and build the task in `pkb.daemon` as a closure capturing the token, mapping, allow-list, health state and store. **`ServerConfig`'s signature does not change.** | The callable shape is fine — the composition root already builds both `HealthState` and `ServerConfig`, and `record_proposal`/`record_flush` are the precedent. Without the fields, TG-3 gets silently skipped, and TG-3 is the only mechanism that tells a human a topic they just created is unreachable from their phone. Widening `PkbService` instead would break the stub-ability the whole Layer 3 suite rests on. |
| **C-31** | **`RunSubscription.close` is documented as "an awaitable that unsubscribes" and is a plain function.** Executed: `type(close) → function`, `inspect.isawaitable → False`, and its real docstring is *"Detach. Never cancels the run"*. Both shipped consumers hedge with `if callable(close): close()`, and `routes.py:322` carries the comment *"Synchronous, and it must stay that way — see the docstring"* pointing at a docstring saying the opposite. | **Fix the docstring** to "a **synchronous** callable that unsubscribes; it is never awaited", and type it `Callable[[], None] \| None`. The adapter uses the same idiom regardless (TG-52). | This is the **third** consumer of that seam. Left as is, step 5's author writes `await subscription.close()` from the Protocol and the resulting `TypeError` fires inside a `finally` during teardown, where it will be reported as a shutdown bug. |
| **C-32** | **Arch §8 promises a clean supervised restart; `_supervise` does not tear down what the crashed task started**, never resets `backoff`, and leaves `state == "restarting"` if cancellation lands during the backoff sleep. | **Do not change `_supervise`.** Put the fix in the task (TG-7) — the only thing that knows what it started — and record a note under AP-17 that `_supervise` supervises exactly one coroutine and cancels nothing. The backoff reset is an optional two-line Layer 3 amendment. | Executed, three generations left three live pollers, which against the real API is three concurrent `getUpdates` on one token: 409s for the losers and updates silently split across three consumers, two of them crashing, with `/health` saying `running`. Arch §8's promise is only true if the restart is clean, and today it is not. |
| **C-33** | **D9 says the bot has "no auth boundary"; arch §10 defers multi-user because "the daemon binds to localhost". Enabling this adapter falsifies both.** A bot is reachable by anyone who finds its username; nothing in TG-1…TG-4, arch §6 or the Layer 3 rules names an owner, an allow-list, or what a stranger gets beyond "instructions". | **Decision X, made explicit in this spec**: private chats only (TG-19), an `owner_user_ids` allow-list on every `message.from.id` and `callback_query.from.id` (TG-20), and a TG-2 reply that leaks nothing but the chat id (TG-21). Amend arch §10's "Multi-user" bullet: *"the daemon binds to localhost" stops being sufficient the moment D9's bot is enabled.* | The chat mapping gives de-facto authorization for *writes* — an unmapped chat runs nothing — but it was written about **addressing**, not about who may say yes to an irreversible write. The allow-list is one comparison, must exist from the first commit, and cannot be retrofitted after a token leaks: retrofitting leaves a window in which anyone who finds the bot can answer approvals. |
| **C-34** | **Arch §6 states a fixed pair where the shipped rules require a derivation**: "the Telegram adapter narrows `allowed_decisions` to approve/reject" versus CL-9/CL-12/TU-42's "affordances are built from `action.allowed_decisions`, never from a hardcoded bar". | **Read arch §6 as describing today's table, not as the contract** (TG-54), and correct the sentence so it stops reading as a specification. | Executed over all twelve gate rows the two agree exactly — which is what makes the wrong implementation pass every test today and draw an impossible button the day a gate ships `('edit','reject')`. |
| **C-35** | **TU-39 and CL-22 collide precisely on this surface.** TU-39: `description` is rendered *"verbatim… never truncated… truncating it is worse than not showing it, because the human approves an irreversible write from a fragment."* CL-22: truncation is *"for a length-limited channel"* and names Telegram's 4096 limit as the motivating case. Measured, the collision is the common case (9,218 / 7,868 characters), not a corner. | **Resolve in TU-39's favour and scope CL-22 explicitly**: truncation is for **list rows, previews and captions**, never for the surface a decision is made against (decision U, TG-56). Amend CL-22's docstring to say so, and give `truncate` a `marker=` parameter. | The `marker=` half matters more than it looks: `TRUNCATION_MARKER` hard-codes *"open the TUI for the whole diff"*, which under TG-56 prints directly above the whole diff. The alternative is a second truncation implementation in the adapter — exactly the per-channel drift CL-22 exists to prevent. |
| **C-36** | **There is no sanctioned path for a file arriving over Telegram, and two built rules close every obvious one.** `pkb.sources.stage` writes into `<kb>/.inbox/` — under `kb_root`, which I3/SV-22 forbid Layer 3 by any path — and the SV-22 scan fails outright on a Layer 3 module that writes a file. `PkbService` has no ingest-by-path method (`pkb_ingest` is text-only, MC-17). Yet the large-source ingestion spec and the README treat inbound sources as first-class, and a photo of a recipe is the most obvious thing a human sends a knowledge-base bot from a phone. | **v1 refuses attachments with the caption quoted back** (TG-36). If file ingestion is wanted, it needs a **Layer 2** entry point reached through `PkbService` (`stage_inbound(...) -> str`) whose write is the runtime's, not the transport's — the same shape `run_scan` already has. **Do not solve it by relaxing SV-22.** | Silently ignoring the attachment loses the caption, which is the part the human actually wrote — and leaves them believing a recipe was filed when no folder, stub or trace exists. Relaxing SV-22 would trade one channel's convenience for the invariant that keeps every other transport out of the tree. |
| **C-37** | **Pre-existing, not step 5's, but aggravated by it: `RunHub` objects are never freed.** `forget()` and `finished()` have no callers anywhere in `src/`; `_hubs[run_id]` retains up to 512 events per run, forever. | **A Layer 3 amendment**, not a step-5 feature: add the sweep to `app.py`'s worker set (or the existing scan tick) with a grace period after the terminal frame, citing AP-9. | It matters more once Telegram lands than it did before: the bot's whole value is a daemon that stays up for weeks, and that is the deployment where an unbounded per-run buffer accumulates. |

---

## 4. Open questions for the human

Ranked by blast radius. **Every one has a default already encoded in §1**, so implementation is not
blocked.

> **RULED 2026-08-08.** Q18, Q19, Q23 and Q24 were put to the human and **all four recommended
> defaults were confirmed**: raw `httpx` behind the `BotApi` Protocol with no Telegram library; a
> pull-only `/pending` command rather than a poller; the full description as an uploaded document
> immediately before the buttons; and one current thread per chat, rotated explicitly by `/new`.
> Q20, Q21, Q22, Q25 and Q26 stand at their recommended defaults and were not escalated — each is
> one keyboard, one table or one config line, and each is reversible. **Decision X (the owner
> allow-list) was reported to the human as an applied decision rather than asked**, on the ground
> that there is no defensible alternative: a bot token is a public inbound path into a process that
> writes to a knowledge base with no undo.

| # | Question | Options | Recommended default | Blast radius if changed later |
|---|----------|---------|---------------------|-------------------------------|
| **Q18** | **Raw `httpx` behind a `BotApi` Protocol, or `python-telegram-bot`?** | (a) raw httpx, ~260 lines, six methods; (b) `python-telegram-bot>=22.8` using `telegram.Bot` **only** — no `telegram.ext`; (c) aiogram. | **(a)** (decision R). (c) is disqualified by measurement: aiogram adds **ten** packages including `aiohttp`, a second HTTP stack the repo's own import-linter already names as a forbidden network client for `pkb.core`. (b) is genuinely defensible — measured, PTB adds **exactly one** package (every dep is already installed), `telegram.Bot` works as a bare client inside a supervised task with no `Application`/`Updater`/`JobQueue`, it routes `getUpdates` to a separate connection pool, it extends the read budget by the poll timeout automatically, and it ships a typed error taxonomy (`RetryAfter`, `Conflict`, `TimedOut`, `InvalidToken`). But 95% of PTB is `telegram.ext`, which D9's shape forbids using — taking a dependency to ban most of it is a rule waiting to be broken. TG-69 lifts the two details PTB gets right into requirements, so **no rule in §1 changes if (b) wins**. | One module (`telegram_api.py`) and one line of `pyproject.toml`. Genuinely reversible: the `BotApi` Protocol is the whole point of decision Q. Both drive the same fake — measured, PTB accepts `base_url="http://127.0.0.1:<port>/bot"` and drove a fake Bot API unmodified. |
| **Q19** | **How does an approval raised in *another* channel reach the phone?** Cross-channel event fan-out is explicitly deferred ("D3 means shared state, not shared streams"), so the bot never sees a TUI-started run's `interrupt`. | (a) pull only — a `/pending` command; (b) a poll of `list_threads()` (already pending-first) on an interval that pushes one message per newly-pending approval; (c) build the deferred cross-channel stream fan-out. | **(a) in v1 (TG-39), with (b) as an opt-in setting.** (c) is a pub/sub layer the daemon deliberately does not have. (b) uses only built calls — `thread_counts()` is two indexed counts (AP-19) and `list_threads()` is ordered pending-first (RO-6) — but needs a **durable already-announced set**, or every supervised restart re-announces every pending approval as a fresh notification. State the cost of (a) plainly: D9's headline benefit — approve from a phone something the TUI asked about hours earlier — requires the human to think to ask. | (b) adds one loop inside the bot task and one more durable table. Skipping the announced-set turns a restart loop into phone spam repeating the same approval, which is worse than not notifying at all. |
| **Q20** | **RE-RULED 2026-08-09 — (a) re-affirmed, with a new reason; see TG-89.** Per-expert channels now exist, so the question is genuinely open for the first time; the answer does not change, because under decision AA most agents have **no** channel, and routing a fan-out approval to the expert's would make it *undeliverable* in the ordinary case — arch §8's headline failure with an approve button. What *does* change is that the approval must now name the expert and the derived thread (TG-85), which the build never did (approved defect 3). **Where is a *fan-out* approval posted?** TG-1 maps chats for *inbound* messages, but LB-16 parks the gate on the **expert's** derived thread, which belongs to a different agent from the chat the human typed in. | (a) the **originating chat**, naming the expert and the derived thread; (b) the **expert's own chat**; (c) both, one primary and one pointer. | **(a).** The human is looking at the chat they just typed into; an approval that surfaces in a different chat — or in **no** chat, since TG-3 makes unmapped agents normal — is arch §8's headline failure reproduced on a phone. The TUI's answer to the same question is TU-12's *unfiltered* cross-agent "needs you" view, not per-agent routing, so (a) is the twin rather than the divergence. `resolve()` routes to `request.thread_id` regardless (CL-8, TG-59), so correctness never depends on which chat it appeared in. (c) creates two live keyboards for one interrupt — the duplicate-modal problem CL-20 exists to prevent. | Determines whether the mapping needs an inverse and whether an approval can be **undeliverable** (under (b) it can; under (a) it cannot), and whether the prompt cache is per-chat or global. |
| **Q21** | **Can a rejection carry a reason on a phone?** | (a) bare reject only, `message=None`; (b) a "Reject with a reason" button sending a `ForceReply`, attaching the human's next message; (c) any reply to an open approval message becomes the reason for the next reject. | **(a) for v1** (TG-65). Arch §6 narrows to approve/reject, and CL-13 is explicit that a required reason would be "a rule only one channel has". A bare reject is fully functional — the harness substitutes its own "do not retry unless the user asks" text. (b) is the right *later* addition and needs no new rules beyond a reply-state map. (c) is a trap: a human replying to an approval message is at least as likely to be asking a question, and misreading that as a rejection files nothing while telling the agent the human said no — indistinguishable afterwards from a considered refusal. | Small and reversible: (b) later changes the keyboard and adds one state map, touching no shared rule. Worth ruling explicitly anyway, because the absence of a reason is the most visible behavioural difference between the two human channels and will read as a bug. |
| **Q22** | **What does a multi-action approval look like on a phone?** One Telegram press delivers one `callback_query`, but `resolve()` is **total** — it raises unless every action index has an `Answer`. | (a) accumulate — one message per action, `resolve`+`resume` fire only when all N are in; (b) all-or-nothing — "Approve all N" / "Reject all N" plus "Open in the TUI"; (c) one keyboard of 2N buttons. | **(a)** (TG-60). (c) is unreadable past N=2 and gives each action no diff of its own. (b) is honest and tempting — it is how `edit` was already handled — but "Approve" over a collapsed 3-write batch is D6's no-undo at its worst, and it would need its own rule forcing the message to state how many actions the single button covers. (a) is the faithful twin of TU-43 and gives TU-47's "later" for free. Whichever wins, it is a TG rule with a stated reason for differing from TU-43. | The keyboard shape, the accumulator table and one `callback_data` field. Getting it wrong the (c) way produces a 400 at the server; getting it wrong the naive way — applying one pressed verb to all N actions — is worse and passes every happy-path test. |
| **Q23** | **A description that does not fit one message: document, split, truncate, or refuse?** | (a) the full description uploaded as an in-memory document, then a short button message; (b) K messages split on line boundaries, buttons on the last; (c) truncate with a marker and attach the buttons; (d) refuse — no keyboard, "too large to decide on a phone", hand off to the TUI. | **(a), with (b) as the fallback when the upload fails, and (c) only for the preview line** (TG-56). Measured, this is the common case, not a corner. (c) alone shows bullets 0–59 of 120 under an irreversible approve button. (d) is the honest fallback but the wrong default: reaching an approval while away from the terminal is the single reason this channel exists, and "large writes are terminal-only" silently makes the most consequential writes — **deletes** — the ones the phone refuses. (b) produces a three-to-eight-message spew per approval and burns the per-chat budget the approval itself needs. | Decides `truncate`'s role in this channel, whether `TRUNCATION_MARKER` needs the `marker=` parameter, and whether `send_document` is in the `BotApi` Protocol and therefore in every fake. **Rule this before the Protocol is written.** If (d) wins, TG-56 inverts into a size-threshold rule and there is no document path at all. |
| **Q24** | **What is a chat's *current* thread, and when does a new one start?** | (a) one long-lived thread per chat forever; (b) a new thread per message; (c) a current thread rotated explicitly by `/new`; (d) (c) plus an automatic rotation after idle or N messages. | **(c)** (TG-26, TG-27). (a) is fatal against the built refusals: RT-39 refuses a turn while an approval is pending, so one abandoned approval — the exact thing arch §8 celebrates being able to leave overnight — makes the chat permanently unusable from the phone. (b) destroys continuity, fires a titling call per message (TT-1) and turns "Cooking · 4 conversations" into "Cooking · 380". (d) is (c) plus an invisible split, and invisible is the property TG-1 was ruled to eliminate. (c) also gives the D3 story for free: `/threads` plus a rebind is how you finish a TUI-started conversation on the phone. | Decides whether step 5 needs durable per-chat state at all — and therefore whether it touches `pkb.service`. Accepted cost of (c): a long-lived chat thread's checkpoint grows without bound and every turn replays it. Measurable, visible, mitigable later. |
| **Q25** | **How are the token, the mapping and the allow-list configured?** | (a) CLI flags on `python -m pkb.daemon`; (b) environment variables; (c) a small TOML/JSON file beside the SQLite database; (d) inside the knowledge base. | **Amended 2026-08-08: (b) for the token *and the allow-list*, (c) for the mapping alone.** Originally ruled (b) for the token, (c) for the mapping and the allow-list; the allow-list moved because it is the token's other half rather than a routing detail — whoever is on it can approve an irreversible write, so the two things that must be protected belong in one place, leaving one file to gitignore and a mapping file that names no credential at all. `PKB_TELEGRAM_TOKEN` and `PKB_TELEGRAM_OWNERS`, sourced from a gitignored `.env` if one is there, with a real environment variable always winning over a line in the file. A mapping file that still carries `owners` is a **startup error** naming the variable, not an ignored key: an allow-list in a file nothing reads looks exactly like one that is in force. (d) is forbidden — I3 says Layer 3 writes nothing under `kb_root`, and reading deployment config from KB content would let an agent's write change which agent a chat talks to: a privilege escalation dressed as a note. (a) puts both in the process table and shell history. The mapping is a growing hand-edited list (TG-3: "human-configured"), so a file beats flags — beside the database, so it travels with the deployment rather than with the content. | One config loader and four lines in `pkb.daemon`. Structurally small, but the token path decides whether a leaked `/health` body is embarrassing or a knowledge-base compromise, so TG-15/TG-16/TG-24 are hard requirements whichever option wins. |
| **Q26** | **Is a Librarian chat required, and do Telegram-created threads get a title?** | Librarian: (a) required; (b) optional but recommended. Titles: (c) let TT-1 run normally; (d) suppress titling for Telegram threads. | **(b) and (c).** SV-26 already anticipates the titling case exactly — *"Telegram supplies none, which is fine: titling happens after the first reply"* — and the title is what makes the phone's conversation findable in the TUI's sidebar, which is the whole of D3's value here. A Librarian chat should be recommended, not required: without one, material for an unmapped topic has nowhere to go; with a mandatory one, TG-3's report becomes noise for a human who wants one expert on their phone. | Zero code for titles (TT-2 puts the call off the critical path). The Librarian choice changes only what `/health` and the startup validation say. |

---

## 5. The exact contract

### 5.1 `pkb.server.telegram_api` — the transport port (decisions Q, R)

```python
# The ONLY module in pkb/server/ that imports an HTTP client (TG-67, TG-68).
# A SIBLING MODULE, NOT A PACKAGE — a package is invisible to five built seam scans (TG-5, P-24).

LONG_POLL_SECONDS: Final = 25.0
READ_TIMEOUT:      Final = LONG_POLL_SECONDS + 15.0   # MUST exceed it. httpx's default kills
                                                      # every idle poll and _supervise turns that
                                                      # into a crash loop (TG-69, L4 P-11).
BASE_URL:          Final = "https://api.telegram.org"  # the ONLY base URL in either module (TG-68)

class BotApi(Protocol):                                # everything below is drivable against a fake
    async def get_updates(self, *, offset: int | None, timeout: int,
                          allowed_updates: Sequence[str]) -> Sequence[Mapping[str, Any]]: ...
    async def send_message(self, chat_id: int, text: str, *,
                           reply_markup: Mapping[str, Any] | None = None) -> int: ...   # message_id
    async def edit_message_text(self, chat_id: int, message_id: int, text: str, *,
                                reply_markup: Mapping[str, Any] | None = None) -> None: ...
    async def edit_message_reply_markup(self, chat_id: int, message_id: int, *,
                                        reply_markup: Mapping[str, Any] | None) -> None: ...
    async def answer_callback_query(self, query_id: str, *, text: str = "",
                                    show_alert: bool = False) -> None: ...
    async def send_document(self, chat_id: int, filename: str, payload: bytes, *,
                            caption: str = "") -> int: ...     # in-memory ONLY (TG-71)
    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None: ...
```

Two connection pools (TG-69): `getUpdates` on one, everything else on the other, so a 25 s long poll
can never occupy the socket an approval must go out on. Every non-file call is `POST` with
`application/json` — which keeps a 4096-character `text` off the URL and the token out of proxy
access logs; `send_document` is the only `multipart/form-data`. Envelope:
`{"ok":true,"result":…}` or `{"ok":false,"error_code":N,"description":…,"parameters":{"retry_after":N}}`.

**Every error is re-raised token-redacted** (TG-15), and `logging.getLogger("httpx")` is filtered
(TG-16).

### 5.2 `pkb.service.telegram` — the durable store (decision S)

```python
# Layer 3's own tables on the checkpointer's connection. ST-7 reserves the `pkb_` prefix.
# Single short autocommit statements; NEVER a transaction across an await (TG-28, ST-3).

pkb_telegram_updates(update_id INTEGER PRIMARY KEY, chat_id, thread_id, run_id, state, created_at)
pkb_telegram_chats  (chat_id INTEGER PRIMARY KEY, thread_id, bound_at)
pkb_telegram_prompts(handle TEXT PRIMARY KEY, chat_id, message_ids, thread_id, interrupt_id,
                     answers_json, created_at)

class TelegramStore(Protocol):          # what pkb.server.telegram is handed — never PkbService
    async def next_offset(self) -> int | None                   # MAX(update_id) + 1 (TG-29)
    async def record_update(self, update_id: int, chat_id: int) -> bool   # False = already seen
    async def mark_dispatched(self, update_id: int, thread_id: str, run_id: str) -> None
    async def orphaned(self) -> Sequence[int]                   # recorded, never dispatched (TG-29)
    async def bind(self, chat_id: int, thread_id: str) -> None  # TG-26
    async def thread_for(self, chat_id: int) -> str | None
    async def stage_prompt(self, handle: str, chat_id: int, thread_id: str,
                           interrupt_id: str, message_ids: Sequence[int]) -> None   # TG-57
    async def prompt(self, handle: str) -> PromptRow | None                          # TG-58
    async def answer(self, handle: str, index: int, verb: str) -> PromptRow          # TG-60
    async def close_prompt(self, handle: str) -> None                                # TG-63
```

### 5.3 `pkb.server.telegram` — the adapter (no HTTP import, no `os`, no `uuid`)

```python
async def run_bot(service: PkbService, *, api: BotApi, store: TelegramStore,
                  mapping: Mapping[int, str], owner_user_ids: frozenset[int],
                  health: SubsystemState) -> None:
    """The supervised task. NEVER returns (TG-6). One task group (TG-7)."""
    async with anyio.create_task_group() as group:        # TG-7 — no bare create_task
        group.start_soon(_outbox, api)                    # TG-49 — the pump never blocks on a send
        await _reconcile(service, store, api)             # TG-30, TG-31 — re-sync, never replay
        while True:                                       # TG-6 — no reachable return/break
            for update in await _poll(api, store):        # TG-8, TG-9, TG-29, TG-34
                await _dispatch(update, ...)

# callback_data — 15 bytes of 64 (TG-57). NOTHING else fits: with a derived thread id it is 97.
CALLBACK = "v1|{handle}|{index}|{verb}"          # handle = secrets.token_hex(4), NOT uuid (TG-70)

# A press: ANSWER FIRST (TG-61), then re-read the LIVE approval (TG-58), then resolve (CL-8).
verb, handle, index = _parse(query["data"])
await api.answer_callback_query(query["id"])                       # TG-61, unconditionally first
row = await store.prompt(handle)                                   # durable — survives a restart
if row is None:
    await api.answer_callback_query(query["id"], text="That approval could not be located.",
                                    show_alert=True)               # TG-58 — never guess a thread
    return
detail = await service.get_thread(row.thread_id)                   # the AUTHORITY, not the cache
row = await store.answer(handle, int(index), verb)
if not row.complete:                                               # TG-60 — partial submits NOTHING
    return
resolution = resolve(detail.pending, row.answers)                   # CL-4…CL-8, one call, N decisions
try:
    subscription = await service.resume(resolution.thread_id,       # CL-8 — the REQUEST's thread
                                        list(resolution.decisions),
                                        interrupt_id=resolution.interrupt_id)
except StaleInterruptError:
    await _alert_and_close(row, "another channel already answered this")   # TG-62 — never retry
    return
await _close_keyboards(row)                                         # TG-63 — all N, always
```

### 5.4 The seam amendments (decisions U and W, same commit)

```python
# pkb/contracts.py
CANCELLED_MESSAGE: Final = "the run was cancelled"     # was private in pkb/server/sse.py (P-27)

def terminal_status(event: AgentEvent, *, interrupted: bool) -> RunStatus:
    """The ONE mapping to RUN_STATUSES. SseEncoder.status_for calls this (TG-50, decision W)."""

# pkb/clients/approval.py
def truncate(description: str, limit: int, *, marker: str = TRUNCATION_MARKER) -> tuple[str, bool]:
    """`marker` because TRUNCATION_MARKER says 'open the TUI for the whole diff', and under TG-56
    the whole diff is in the same chat, one message up (decision U, C-35)."""
```

### 5.5 `/health` — the telegram block (TG-11, TG-12, TG-13)

```jsonc
"telegram": {
  "enabled": true, "state": "running", "restarts": 0,
  "last_error": null, "last_error_at": null, "started_at": "…",
  "chats": 3,                                   // NEW — TG-11
  "unmapped_agents": ["topic/cooking/grilling"],// NEW — computed in the endpoint, TG-11, TG-3
  "last_poll_ok_at": "…",                       // NEW — `state:"running"` means ALIVE, not REACHABLE
  "last_send_error": null                       // NEW — never changes `status` (TG-13)
}
```

### 5.6 Import contracts

**Unchanged — all five stay as they are.** Verified: a planted `pkb/server/telegram.py` importing
`httpx`, `pkb.clients.approval`, `pkb.server.sse` and `pkb.service` gives
`Analyzed 123 files, 627 dependencies. Contracts: 5 kept, 0 broken.` What changes is the **MC-7
test**, extended to cover `TELEGRAM_SOURCE` with `HARNESS_ROOTS`, the `pkb.agents` check, the
loopback assertion, and `HTTP_CLIENT_ROOTS` applied to the logic module only (TG-68).
`[project.dependencies]` gains nothing under decision R; under Q18(b) it gains
`python-telegram-bot>=22.8` with the "`telegram.ext` is forbidden" constraint written beside it.

---

## 6. Test strategy

Arch §9's intent — free, fast, keyless and networkless — holds completely. Layer 5 adds no model
call and, by default, opens no socket.

### 6.1 Three tiers, one job each

| Tier | Tests | Why not the others |
|------|-------|--------------------|
| **A fake `BotApi` + `tests/server/stub.py::StubService`** (`tests/server/test_telegram.py`) | Almost everything: keyboards over all twelve gate rows, the 64-byte budget, UTF-16 truncation, the split's byte-identical reassembly, 429 back-pressure, the offset ledger, restart re-sync, `answerCallbackQuery` ordering, the unmapped/refusal paths, the allow-list. The fake records call order, so "answer precedes resume" is directly assertable. | The only fixture that can script a 429 with `retry_after`, a 409 conflict, a crash between the ledger row and `start_run`, and a 300-frame fan-out. Needs no app, no socket, no lifespan. |
| **The real `RuntimeService` over a scripted `Runtime`** (`tests/server/test_telegram_seam.py`) | The tier that binds the adapter to the real seam: a real fan-out parking on a derived thread (TG-53), real `ThreadBusyError`/`ApprovalPendingError`/`StaleInterruptError` arriving **synchronously** from `await start_run` (AP-10), real `attach` returning `None` once idle, real `subscription.close` being synchronous. | Executed: `RuntimeService(ScriptedRuntime(), await aiosqlite.connect(":memory:", isolation_level=None))` loads **no** harness and **no** HTTP module — `sys.modules` check over `deepagents, langgraph, langchain, langchain_core, httpx, httpx2` came back empty. **Gotcha**: an aiosqlite in-memory connection keeps a worker thread alive; without `await connection.close()` the process hangs at exit. |
| **A fake Bot API on uvicorn** (a handful) | Real-socket facts only: TG-69's read-budget behaviour under a genuine 25 s long poll, and that an abandoned request does not delay shutdown. | ~0.1 s of startup each, and it drags in L4 P-14b's process-global `sse_starlette` landmine if the daemon app is also spun. Reserve it. |

### 6.2 Two fixtures that are not optional

```python
# tests/server/conftest.py  (extending what step 4 already added)
@pytest.fixture
def fake_bot() -> FakeBotApi:
    """Records every call in order, scriptable failures per method (429 + retry_after, 409, 5xx,
    400 message-too-long over a real UTF-16 count, 400 BUTTON_DATA_INVALID over 64 bytes).

    The 64-byte and 4096-unit validations MUST live in the fake, not only in the adapter — neither
    PTB nor aiogram enforces them, and the real API only complains at the moment a human is waiting
    for an approval (P-28, P-26).
    """

@pytest.fixture
def store() -> TelegramStore:
    """An in-memory TelegramStore. Every restart test constructs a FRESH adapter over the SAME
    store — that is the only shape in which TG-29/TG-31/TG-58 are actually exercised, because
    `_supervise` carries nothing across (P-23)."""
```

### 6.3 The headline assertions, by area

| Area | Fixture | Headline assertions |
|------|---------|---------------------|
| **seam** (`tests/server/test_telegram_imports.py`) | AST + `lint-imports` | the logic module pulls no HTTP client, no harness root, no `pkb.agents`, no `pkb.tui.client`, no `pkb.clients.sse`; the only base URL is the Bot API's and no daemon-local host/port/path literal appears (TG-68); `lint-imports` stays 5-kept (verified); the two planted-module re-plants still fail SV-10 and SV-22 (TG-5, TG-70, TG-71, executed). |
| **supervision** (`test_telegram_supervision.py`) | real `_supervise` + fake api | three generations leave exactly one live child (TG-7, executed as a failure today); a returning task is unreachable (TG-6); 429/500/timeout leave `restarts == 0` (TG-8); 409 stops polling and names both causes (TG-9); the token appears in neither `health.telegram.last_error` nor `caplog.text` (TG-15, TG-16, executed as a leak today); fifty restarts leave one open client (TG-10). |
| **addressing** (`test_telegram_routing.py`) | fake api + stub | mapped/unmapped/non-allowed sender (TG-18…TG-22); the unmapped reply contains the chat id and **no** string from `list_agents()`, which is never called (TG-21); a supergroup runs nothing (TG-19); ten unknown updates produce one reply (TG-23). |
| **delivery** (`test_telegram_updates.py`) | fake api + store | a crash between the ledger row and `start_run` yields `offset=101`, zero `start_run`, one lost-message notice (TG-29); a crash after `start_run` yields exactly one across the restart; a cold start with 40 pending updates runs nothing (TG-30); a restart mid-run leaves `supervisor.active == 1` and never calls `cancel` (TG-32). |
| **rendering** (`test_telegram_render.py`) | pure + fake api | 500 deltas → zero edits, one message (TG-41); `final_text` never sent (TG-42); a 12,000-character reply reassembles byte-identically on newline boundaries (TG-45); an all-emoji description fits 4096 **UTF-16 units** (TG-44, the P-26 regression); a diff round-trips with no `parse_mode` or inside a fence (TG-46); every payload disables link previews (TG-47). |
| **approvals** (`test_telegram_approval.py`) | fake api + stub + real service | all twelve gate rows give `offered(drop=("edit",))` (TG-54); `allowed_decisions=()` gives a keyboard-free hand-off and zero resumes (TG-55); a 9,218-character description is uploaded whole before the buttons (TG-56); every `callback_data` ≤ 64 bytes and the thread-id variant is pinned as rejected (TG-57); a **fresh** adapter resolves a press it never sent (TG-58); a fan-out resolves against the derived id while the chat stays bound (TG-59, TG-53); two actions → one resume (TG-60); `answer_callback_query` precedes every `resume` (TG-61); a stale interrupt gives one alert, one `get_thread`, zero resumes (TG-62); every terminal outcome clears all N keyboards (TG-63); the golden keyboard table per `GateReason` (TG-64). |
| **outcomes** (`test_telegram_outcomes.py`) | scripted events | four terminal states, no fall-through, with `terminal_status` identity-tested against `SseEncoder.status_for` (TG-50); a stream killed mid-run yields "outcome unknown" and exactly one `get_thread` (TG-51); `subscribers` returns to 0 (TG-52); 300 frames with 1 s sends lose only progress (TG-49). |
| **invariants** (CI) | — | `make layers`, `make lint`, `make types`, plus the two extended built tests (`test_seam.py -k "sv10 or sv22"`, `test_mcp.py -k mc7`). |

### 6.4 One stub gap to close first

`tests/server/stub.py` has no way to raise `ApprovalPendingError` or `ThreadBusyError` on demand
per call, and TG-37/TG-38 are both about a **synchronous** refusal from `await start_run` before
anything is committed (AP-10). Add a scriptable refusal queue. Layer 4 already fixed the
`resume`-reuses-`run-1` defect (§6.4 there); TG-39's `/cancel` depends on that fix, so verify it
landed before writing the cancel test.

### 6.5 What needs a live bot token

**Nothing in §1 does.** Every rule is assertable against a fake, which is the whole reason
decision Q splits the transport out. Four *facts about Telegram itself* are worth pinning against
the real API once, and they are marked `@pytest.mark.live`, deselected by default and skipped
outright when `PKB_TELEGRAM_TEST_TOKEN` is unset:

| Fact | Why a fake cannot settle it |
|------|------------------------------|
| `getUpdates` 24-hour retention and offset confirmation semantics | The behaviour TG-29 and TG-58 are built on. Our fake implements it; only the real API proves the fake is right. |
| The MarkdownV2 parser's exact escaping rules and its 400 on an unescaped diff | TG-46's failure mode is a 400 that eats the approval. Our fake implements the documented rule; the real parser is the arbiter. |
| The real per-chat rate limit and the `retry_after` values it hands back | TG-43 and TG-48 are sized from the FAQ's "about one message per second", which is documented as approximate. |
| `409 Conflict` on a second poller, and `BUTTON_DATA_INVALID` at 65 bytes | TG-9 and TG-57. Neither client library enforces the second, so the server is the only witness. |

```
pytest              # collects ZERO live tests; opens no socket; needs no token
pytest -m live      # four tests, skipped unless PKB_TELEGRAM_TEST_TOKEN is set
```

The token used by `-m live` must be a **throwaway bot with no chat mapped to it**, and the tests
must never call `start_run`. A live test that files a note is a live test that writes to a
knowledge base with no undo.

---

## 7. Explicitly out of Layer 5

**Already Layer 1 or 2 — cite it, never reimplement it**
- The diff and the validation label inside an approval (`gates.describe_write`, RT-34, RT-35). The
  adapter displays; it never computes, and under I3 it could not read the tree to try (TG-56, TG-66).
- Which decisions an action allows. `ActionView.allowed_decisions` is server-side truth; a channel
  narrows and never widens (RT-32, RO-15, CL-9, TG-54).
- Decision validation — `pkb.contracts.validate_decisions`, reached through `pkb.clients.approval`
  by identity (CL-3).
- Thread-id derivation (`expert_thread_id`, `librarian_thread_id`, `is_scan_thread`) and the
  event→thread mapping (`thread_for_event`, catalog-gated) — TG-40, C-28.
- The merged reply, the expert roster, the per-expert statuses, the filed-paths list (LB-18, MC-10).
  Never parsed out of text (TG-45).
- The gate table, the deny list, the write lock, the flush, the 3-attempt bound, the escalation.

**Already Layer 3**
- Thread minting, listing, ordering and grouping (SV-10, RO-6, RO-7). The bot renders the order it
  is given (TG-40).
- The run supervisor, the replay buffer, `attach`'s detach-not-cancel semantics (AP-7, AP-9).
- The typed-error tables (`ERROR_CODES`, `RETRYABLE_CODES`). The bot branches on the typed exception
  and never re-maps a code or invents prose for one.
- The supervision loop itself. The fix for P-23 belongs in the **task**, not in `_supervise` (C-32).

**Already Layer 4**
- `pkb.clients.approval`. Layer 5 imports it in process — the reason CL-1 and decision I exist — and
  adds no second copy of anything in it. The only change is `truncate(marker=)` (decision U).
- `pkb.clients.sse` and the whole wire decoder. An in-process adapter receives `pkb.contracts`
  dataclasses, not frames; `decode_frame` and `decode_request` are unusable here and unneeded.

**Deferred**
- **Webhook mode.** Long polling only. A webhook needs a public HTTPS endpoint and an inbound route
  on a daemon with no auth (AP-20, arch §10), and it would make the bot a *route* rather than the
  supervised task D9's built slot expects.
- Attachment, media and file ingestion (C-36). It needs a Layer 2 entry point through `PkbService`,
  not a relaxation of SV-22.
- Applying a `PendingProposal` (L3 Q3, CL-21); creating a topic from a chat (a gated agent flow,
  LB-7); a conflict-scan trigger (no route exists — L4 Q17).
- Live fan-out of one run's events to a *second* channel. D3 means shared **state**, not shared
  **streams** — a TUI watching a thread Telegram resumes sees the outcome through `get_thread`.
- Cross-channel approval **push** (Q19(b)) — v1 is `/pending` pull.
- Rejection with a typed reason (Q21(b)); `edit` on a phone; a per-chat coalescing window for the
  three-messages-in-a-row case (TG-38 refuses instead, and that is a real UX cost, named here so it
  does not become permanent by accident).
- ~~Forum topics~~ — **no longer deferred, Amended 2026-08-09**: private-chat topics are §9, and
  they are what makes a channel per expert possible at all. Group forums remain out (TG-19).
  Inline queries, group and multi-user support, any auth beyond the channel mapping and the sender
  allow-list, and any Telegram-specific column on `threads` (ST-5, C-29) are all still deferred.

**Never, at any layer**
- **A model call from the adapter**, of any kind, for any reason — including to summarise a reply
  that does not fit (TG-45).
- **Any read or write under `kb_root`** from either telegram module (TG-71, I3, SV-22) — including
  staging an attachment or writing the overflow document to a temp file.
- **Splitting, condensing or re-wording on meaning.** Length is the only permitted reason to cut
  (TG-45, LB-18).
- **Rendering `run.end.final_text`**, or consuming `message.delta` (TG-41, TG-42).
- **Branching on `origin_channel`** to permit, refuse, route or format anything (TG-33, RO-22).
- **Widening `allowed_decisions`**, padding a partial approval with a default, submitting N
  decisions in N calls, or applying one pressed verb to all N actions (TG-54, TG-60, CL-6).
- **Resolving from the bytes the bot rendered.** Every press re-reads the live approval (TG-58).
- **Posting decisions to the chat's bound thread** rather than `request.thread_id` (TG-59, CL-8).
- **Calling `service.cancel` on a restart, a disconnect, or a lost subscription** (TG-32, AP-7, D2).
- **An automatic retry of a run** at any level. A 429 re-send of an idempotent Bot API call is not
  that, and the distinction is written into TG-48's docstring so nobody conflates them.
- **A uuid minted anywhere in `pkb/server/telegram*.py`** (TG-70, SV-10) or a `chat_id` in
  `callback_data` (TG-57).
- **Putting the adapter in a package** without changing `_sources` to `rglob` in the same commit
  (TG-5, P-24).

---

## 8. As built

**Status: built.** `pkb.server.telegram`, `pkb.server.telegram_api`, `pkb.service.telegram`, the
`/health` block and the `pkb.daemon` wiring are in the tree, and the Layer 5 suite is
`tests/server/test_telegram.py`, `test_telegram_api.py`, `test_telegram_render.py`,
`test_telegram_supervision.py`, `test_daemon_telegram.py` and `tests/service/test_telegram_store.py`.

This section records where the build diverges from §1 and §5, and what a rule-by-rule conformance
pass had to change afterwards. Everything below is a deliberate decision with its reason; nothing
here is an outstanding defect.

### 8.1 The `BotApi` surface as shipped

§5.1 names seven methods. The shipped Protocol names seven different ones:

| §5.1 | Shipped | Why |
|------|---------|-----|
| `get_updates(*, offset, timeout, allowed_updates)` | `get_updates(offset, *, timeout=POLL_TIMEOUT)` | `allowed_updates` is not a caller's choice — it is a closed constant sent on *every* poll (TG-34), and a parameter invites a caller to narrow it. Found live on a real bot stuck at `['message']`: every button press was dropped by Telegram before delivery, with no error and no pending update. |
| `send_message(...) -> int` | `send_message(...) -> Mapping` | The whole result, because TG-63 needs `message_id` *and* nothing stops a later rule needing another field. |
| `edit_message_text` | `edit_message` | Same call, shorter name. |
| `edit_message_reply_markup` | `clear_keyboard` | **A better seam, not a rename.** `editMessageReplyMarkup` can also *set* a keyboard, and the one thing TG-63 needs is removal without touching the text. A method that can only remove cannot be the one that overwrites a description the human decided against. |
| `answer_callback_query` | `answer_callback` | Shorter; same call. |
| `send_document(chat_id, filename, payload, *, caption)` | `send_document(chat_id, filename, content, caption="")` | Same call. |
| `send_chat_action` | **absent** | **Dropped, not deferred.** The typing indicator has no rule behind it and no failure it prevents, and every method on this Protocol has to be implemented by every fake. TG-46 does not need it. |
| — | `get_me` **added** | The one call that proves a token is valid without side effects. |

`tests/server/test_telegram_api.py` compares `inspect.signature` of every `HttpBotApi` method
against the Protocol's, so the fake cannot drift from the real client silently.

### 8.2 Table names

§5.2 specifies `pkb_telegram_chats`; the build ships **`pkb_telegram_bindings`**. `chats` reads as
"the mapping", which is deployment configuration the bot must never write (TG-17); this table holds
the chat→thread binding, which the bot owns. `pkb_telegram_updates` and `pkb_telegram_prompts` are
as specified. The ledger carries `thread_id` and `run_id` as §5.2 requires, filled in at
`start_run` rather than at dispatch (see 8.3).

### 8.3 What the conformance pass changed

Four adversarial auditors re-walked TG-1…TG-71 against the first build. The behavioural gaps they
found, and how each was closed:

| Rule | What was wrong | Fix |
|------|----------------|-----|
| TG-8 | A `429` whose body omitted `parameters.retry_after` parsed as `0.0`, so `min(retry_after, 30)` slept **nothing** — three attempts in 0.0001 s, logged as "waiting 0.0s". A hot loop against a rate limiter. | The 429 wait is floored at `backoff`. |
| TG-13 | `_report_orphans` and the cold-start notice ran *before* the task group and outside any suppression, so a chat that blocked the bot raised a 403 straight into `_supervise`: `restarts` climbing, `state: "restarting"`, `/health` permanently `degraded`. | Startup notices go through `_announce`, which records the failure on `/health` and swallows it. A chat that cannot be written to is one recipient, not the subsystem being down. |
| TG-16, TG-24 | `TelegramConfig` was a plain dataclass, so `repr(config)` and `repr(adapter)` printed the token whole — into any `%r`, traceback frame or pytest locals dump. | `field(repr=False)` plus an explicit masked `__repr__`, mirroring `HttpBotApi`. |
| TG-18 | The invalid-entry report was one ERROR line at startup and nothing on `/health`. | `SubsystemState.invalid_chats`, published by `payload()`; never changes `status`. |
| TG-19 | A group id in the mapping was accepted; `edited_message` bypassed the private-chat gate entirely. | Non-positive chat ids are refused by `load_telegram_config`; every update kind goes through one `_admit` check. |
| TG-20 | A non-allow-listed sender got a refusal in a mapped chat and the full unmapped explanation anywhere else — a guaranteed reply on every path. | The allow-list runs **before** the mapping check and returns silently. The unmapped reply survives for the *owner* opening a chat they have not mapped yet, who is the only sender who needs the chat id. |
| TG-22, TG-23, TG-35 | The `edited_message` branch sat above every guard, so ten edits from a stranger produced ten replies. | Same `_admit` gate. |
| TG-26 | `bind()` wrote an `agent_id` nothing read, so re-mapping a chat kept filing into the previous expert. | `binding()` returns both; a mismatch rotates onto a fresh thread and says so. |
| TG-29 | `dispatched` flipped only after the whole turn was relayed, so a task cancelled mid-stream looked identical to a crash before `start_run` — and the human was told to re-send a turn that had already written. `orphans()` returned bare ids, so the notice was broadcast to every mapped chat in the owner allow-list, which for the ordinary deployment is **no chat at all**. | Three ledger states (claimed / started / finished), `started()` recording `thread_id` and `run_id`, and `orphans()` returning `(update_id, chat_id)` so the notice reaches the chat that lost the message. |
| TG-31 | Nothing on the restart path re-synced anything: a fresh adapter over a thread with a live `pending` sent zero messages and made zero service calls, leaving the human's buttons dead. | `_recover()` runs inside the task group before the poll loop: branch (a) re-posts the keyboard (walking children, TG-53), (b) reattaches and renders only the outcome, (c) posts the last assistant message marked late. |
| TG-39 | `_poll` awaited the whole run, so **no** `getUpdates` was issued for 16 s (cloud) or 284 s (local fallback) — `/cancel` could not reach the run it was meant to stop. | Each update is dispatched into the task group, serialized per chat by a lock that commands and button presses do not take. |
| TG-48, TG-49 | The outbox dropped on `QueueFull` and logged it as "a progress message" — but TG-43 means the queue only ever carries a `MessageComplete`, the roster line or a terminal note, so the drop path could *only* discard something the rule forbids dropping. The pump swallowed a failed send silently. | The outbox is **unbounded**: nothing is dropped, and nothing blocks either — blocking would push back-pressure onto `RunHub`'s 256-slot subscriber queue, whose overflow closes the stream without a terminal frame, which is the same rule's other half. Depth is logged every 64 messages. The pump logs the chat and the loss beside `last_send_error`. |
| TG-51 | A stream that *raised* propagated into the poll loop's blanket suppression: half a reply, no re-sync, no notice — indistinguishable from success. | The `async for` is wrapped and routed into the same outcome-unknown branch. |
| TG-52 | `/cancel` attached a subscription and never closed it, leaking a subscriber per invocation. | `try/finally` with the synchronous `close` idiom (C-31). |
| TG-53 | Recovery returned at the **first** gating child, leaving a concurrent second approval with no affordance. | Every child with a `pending` is re-posted. |
| TG-54 | The keyboard drew a `Respond` button and the resolver turned every non-`"a"` verb into a **reject** — so pressing it rejected the write, `validate_decisions` refused it, and the `finally` closed the prompt and cleared every keyboard. | One `VERBS` table read in both directions; `respond` narrowed away (see the amended rule). |
| TG-56 | A failed `sendDocument` was suppressed and the keyboard attached anyway — an irreversible Approve button under a preview ending "… (full text above)" that was now a lie. `fit` also used `truncate`'s default marker and stripped a hand-copied literal. | The upload's failure means no keyboard, only the hand-off; `fit` passes `marker=` through. |
| TG-58 | "Could not be located" and "already answered" sent the same message. | Two branches, two messages. The `list_threads` fallback §1 mentions is **not** implemented and cannot be: `callback_data` carries a handle and nothing else (TG-57), so with the row gone there is no interrupt id to scan *by*, and the adapter never guesses a thread. |
| TG-60 | The accumulator was compared against `prompt["action_count"]` — the count from when the message was posted — and an out-of-range index was recorded rather than refused, so a stray press destroyed the human's genuine answer and closed the approval. | Every press is validated against the freshly-read request; a refusal leaves the prompt open and the keyboards intact. |
| TG-62 | `StaleInterruptError` from `resume` was caught nowhere; the human got a blank, non-alert callback answer and a chat line that scrolls away. | Caught, and routed to the same `show_alert` path the pre-emptive branch uses. |
| TG-63 | The confirm step's message id was never recorded, so its keyboard was never cleared. | `_ask_confirm` records it. |
| TG-64 | **Cancel submitted a reject.** `"x"` was not a key of `_CONFIRM_VERBS`, so it was recorded as the action's answer and became `Decision(type="reject")` one function along — a human backing out of a delete had rejected the write and closed the approval. | `_CANCEL` is handled explicitly: nothing recorded, prompt open, keyboards live. |
| TG-66 | Only the label's *header* was hoisted; every finding stayed at the bottom of a description TG-56 may have uploaded as a file. | The whole block, header through the last finding. |
| TG-68 | The MC-7 test named `MCP_SOURCE` alone. Proven: planting `import httpx` **and** `_DAEMON = "http://127.0.0.1:8765/threads"` in `pkb/server/telegram.py` left 1934 tests passing and `lint-imports` at 5 kept, 0 broken. | The MC-7 test is parametrised over `(MCP_SOURCE, TELEGRAM_SOURCE, TELEGRAM_API_SOURCE)`. The loopback clause is asserted as "no `http(s)://` literal except the Bot API's" rather than as the route-path list §1 names, because two of those paths are legitimately spelled elsewhere: the bot's own `/threads` **command** (TG-39) is byte-identical to the daemon's route, and `mcp.py` names `127.0.0.1` as the host it *binds*. The `://` form still catches the plant. |

### 8.4 What TG-64 still does not do

"The first press **replaces** the keyboard with a confirm/cancel pair" is implemented as a *second*
message carrying the confirm pair, with the original keyboard left live. Both message ids are
recorded and both keyboards are cleared at the terminal outcome (TG-63), and a press on the
original after the confirm prompt appeared is answered by the same live re-read every press gets —
so nothing is unsafe. Replacing it literally means swapping `reply_markup` on the original message,
which needs a `set_keyboard` call the Protocol does not have; it is a UI improvement, not a
correctness fix, and it is deferred rather than done.

### 8.5 Two clauses of TG-48 and TG-49 that are **not** built

* **TG-48's "queued per chat".** The outbox is one queue for every chat, drained by one pump. It
  never drops (8.3), so no message is lost, but a chat whose sends are being rate limited does
  delay the next chat's — the pump sleeps inside `with_retry`. Fixing it means one pump task per
  chat, which needs the task group as a spawn point from the outbound path as well as the inbound
  one. Deferred, and stated here so it is not mistaken for done: the consequence is latency, not
  loss.
* **TG-49's "the pump never blocks on a Bot API call", for the approval path only.**
  `_post_approval` is still awaited inline from `_consume`, because it needs `sendMessage`'s
  returned `message_id` synchronously in order to record it against the prompt row — without that,
  TG-63 cannot clear the keyboard of a message it never learned the id of. Making it asynchronous
  means moving `record_message` into the pump and giving the outbox a richer item type. The
  data-loss half of TG-49 is closed (nothing is dropped) and the interrupt path is the *shortest*
  thing the pump does, but the clause as written is not satisfied.

### 8.6 The allow-list moved to the environment (Q25 amended, 2026-08-08)

Q25 first put the allow-list in the file beside the mapping. It is now `PKB_TELEGRAM_OWNERS`,
beside `PKB_TELEGRAM_TOKEN`, because it is **the token's other half, not a routing detail**: whoever
is on it can approve an irreversible write, so it belongs wherever the credential is protected. The
old split asked a human to gitignore one secret and commit a file containing the other, which is how
the other one gets committed. Nothing in §5.3 changed — the adapter still receives `token` and
`owner_user_ids` as arguments from the composition root (TG-24), and still cannot import `os`.

What changed in `pkb.daemon`:

| | As built |
|---|---|
| `PKB_TELEGRAM_OWNERS` | Comma- **or space**-separated Telegram *user* ids. A non-numeric entry is a startup error naming the variable; unset or empty refuses everyone (decision X) and is logged at warning whenever a token and chats are configured without it, because an inert deployment is otherwise indistinguishable from a working one until someone sends a message. |
| `<db>.telegram.json` | Holds `{"chats": {"<chat_id>": "<agent_id>"}}` and nothing else, so it names no credential and can be committed. A file that **still carries `owners`** is a startup error naming the variable — refused rather than ignored, because a file listing three authorized users that nothing reads looks exactly like an allow-list that is in force. |
| `load_env_file(path, environ=None)` | A stdlib `KEY=value` reader, not a dependency. **A real environment variable always wins** over a line in the file, so a systemd `Environment=` or a container secret is never silently overridden by a stale `.env` — the failure mode that makes dotenv loaders hard to debug. Values are literal, minus one matched pair of surrounding quotes: no interpolation, because a parser clever enough to expand `$` will one day eat part of a token. A world- or group-readable file is a **warning**, not a refusal. |
| `--env-file` | Default `.env`, read from the working directory before the app is built and after logging is up, so the "no owners" warning and the mode warning are both visible. |
| `.gitignore` | `.env` and `.env.*`, with `!.env.example` after them so the committed template survives. `.env.example` carries obviously-fictional values and is what `docs/how-to/telegram.md` tells the human to copy. |

The `.gitignore` ordering is asserted through `git check-ignore` rather than by reading the file as
text, because the negation must follow the pattern it exempts and that is exactly what a text
assertion gets wrong.

---

## 9. Topics: a channel per expert

**Date**: 2026-08-09
**Status**: **Built** — see §9.13 for the shipped surface and where it diverges from what is written
below. The rules, decisions and open questions are kept in their original tense; the amendments made
during the build are marked in place and the divergences are collected at the end, so this section
still reads as the design it was and the build has a rule to cite. Every Bot API fact below is either
quoted from the official changelog or was **probed live against the real bot on 2026-08-09**; see
§9.1 — and F-4 is why none of §9 is verified against the live API.
**Scope**: `pkb.server.telegram`, `pkb.server.telegram_api`, `pkb.service.telegram`, the
`/health` telegram block and the `pkb.daemon` wiring. **`pkb.tui` and `pkb.clients` are not touched
by anything in this section** — the TUI already reaches every expert directly through its sidebar
and `n`, and that surface is explicitly out of scope.

### Why this section exists

Three of the four human surfaces can already address a topic expert directly: the TUI (sidebar +
`n`), HTTP (`POST /agents/{agent_id:path}/threads`) and MCP (`pkb_ask(agent_id=…)`). **Telegram was
the only gap, and the gap was structural, not an omission.** TG-1 maps a *chat* to an agent, and one
bot gives one human exactly one private chat — so one human reached exactly one agent. Every other
agent in the catalog was, from the phone, addressable only by asking the Librarian to route to it,
which is the indirection D3 exists to remove.

Bot API 9.3 and 9.4 removed the structural limit. A private chat can now hold **topics**, and a
topic can carry a `message_thread_id` on both directions of the wire. So the unit of addressing
stops being the chat and becomes the **channel**.

### 9.0 Vocabulary, fixed here and used for the rest of the section

| Term | Means |
|------|-------|
| **channel** | The pair `(chat_id, topic_id)`, where `topic_id` is a `message_thread_id` or **`0`** for the General area. **This, not `chat_id`, is the addressing unit from TG-72 onward.** |
| **General** | The part of a private chat that carries **no** `message_thread_id`. Represented as `topic_id == 0`. Telegram mints topic ids from the message-id sequence, which starts at 1, so `0` is free and is never a real topic. |
| **Threaded Mode** | The per-bot BotFather toggle that turns topics on in private chats. **Off by default.** `getMe.has_topics_enabled` is the only way a daemon can find out. |
| **channel directory** | `agent_id ↔ (chat_id, topic_id)`, durable in `pkb_telegram_channels`, written by the bot. Distinct from the **mapping** (`<db>.telegram.json`), which stays human-configured and unwritten (TG-17). |
| **home chat** | Any private chat named by the mapping. Nothing requires exactly one; a channel key is chat-qualified throughout. |

---

### 9.1 The Bot API facts this design rests on

**F-1 … F-4 are quoted from the official changelog or were executed against the real bot today.**
`live` marks a fact only the real API can settle; those become `@pytest.mark.live` tests, deselected
by default exactly as §6.5 already does.

| # | Fact | Established by | What it forces |
|---|------|----------------|----------------|
| **F-1** | **Bot API 9.3 (2025-12-31) put topics in private chats**: `message_thread_id` and `is_topic_message` on `Message`, `has_topics_enabled` on `User` (returned **only** by `getMe`), and `message_thread_id` accepted by `sendMessage`, `sendDocument`, `sendChatAction` and the rest of the send family. **Bot API 9.4 (2026-02-09)** let bots call `createForumTopic` in a private chat. | Official changelog | The whole feature. Two API calls (`getMe`, `createForumTopic`) and one parameter on the send family — nothing else changes on the wire (TG-75, TG-78, TG-90). |
| **F-2** | **A deleted topic is silent.** In a **private** chat, sending with the `message_thread_id` of a deleted topic does **not** error: the parameter is ignored and the message lands in **General**. The same case in a group errors with `message thread not found`. The **response** carries the truth — `sendMessage` returns the `Message`, and its `message_thread_id` is where the message actually went. | tdlib/telegram-bot-api#854 | **The single most important constraint in this section.** A bot cannot learn this from an error, so it must read the response back (TG-80). Any design that does not is the failure TG-1 was ruled against, reproduced with an approve button. |
| **F-3** | **Bot API 10.0 (2026-05-08) reportedly broke sending to *existing* private-chat topics** with `400 Bad Request: message thread not found`, while inbound messages still carry `message_thread_id`. Reported unresolved. | tdlib/telegram-bot-api#847 | Treated as **the same fact as F-2** — the topic is gone, recreate and re-send (TG-83). That is the correct handling whether or not the report is a regression, which is what makes it safe to rule on an unresolved bug. |
| **F-4** *(live, probed today)* | The deployment's own bot answers `getMe` with `has_topics_enabled: false` and `allows_users_to_create_topics: false`, and `createForumTopic` answers **`400 Bad Request: the chat is not a forum`**. Threaded Mode is off; the human has been asked to enable it. | Executed 2026-08-09 against the real bot | **Nothing here can be verified against the live API before the toggle is flipped**, so the entire section is built and asserted against a fake `BotApi` — which is what the whole Layer 5 suite already does, and the reason decision Q split the transport out in the first place. It also makes TG-75 non-negotiable: the daemon must work, unchanged, with the toggle off. |
| **F-5** | **There is no update for a deleted topic.** Topic lifecycle reaches a bot as *service messages* inside an ordinary `message` update (`forum_topic_created`, `forum_topic_edited`, `forum_topic_closed`, `forum_topic_reopened`) — and deletion produces none of them. | Bot API update-type list; F-2 | `allowed_updates` needs **no new kind** (TG-91) — and the absence of a deletion event is precisely why TG-80 has to read the send response. It also means a service message arrives on the same code path as a human's message and would hit TG-36's attachment refusal (TG-92). |
| **F-6** | **Only the send family takes `message_thread_id`.** `editMessageText`, `editMessageReplyMarkup` and `answerCallbackQuery` address a message by `chat_id` + `message_id` (or by query id) and take no thread parameter. | Bot API method signatures | TG-63 needs **no** topic to clear a keyboard, contrary to the obvious assumption. Passing one is at best ignored and at worst a 400 on the one call that disarms an irreversible button (TG-90). |
| **F-7** | **The rate budget is per *chat*.** Telegram's "about one message per second" is a chat-level limit; topics are subdivisions of one chat and do not multiply it. | Telegram FAQ; F-1 (topics are `Message` fields, not chats) | Ten channels do not buy ten times the send budget. TG-43's coalescing matters **more** after this section, not less, and the outbox stays one queue per chat (TG-94). |

---

### 9.2 Decisions applied

Continuing the letter series of §"Decisions applied on top of the mined recommendations" (last: **X**).

| # | Decision | Why |
|---|----------|-----|
| **Y** | **The addressing unit becomes the channel `(chat_id, topic_id)`, with `topic_id == 0` for General.** Every piece of per-chat state — the thread binding, the turn lock, the TG-23 window, the ledger's chat column, the prompt row — is re-keyed on it. | TG-1's guarantee is *"a chat maps to exactly one agent"*, and its whole purpose was to delete the "which expert am I talking to?" ambiguity that made a mis-sent note land in the wrong topic. A topic preserves that guarantee **exactly** — a topic maps to exactly one agent — while removing the one-chat-per-bot ceiling that made it useless for more than one expert. `0` rather than `None` because it is a database key, an index component and a dict key on three code paths, and a nullable key is how a General binding and a topic binding silently collide (SQLite treats NULLs as distinct in a unique index, so a nullable column would permit two General rows per chat). Telegram's topic ids are message ids and start at 1, so `0` is permanently free. |
| **Z** | **General is the Librarian — by recommendation and a startup warning, not by rule.** `config.chats[chat_id]` keeps its exact current meaning and becomes the agent of `(chat_id, 0)`. | Three arguments for the Librarian and one against, and the one against is what keeps it a warning. **For:** (i) General is the *"I do not know where this goes"* area and the Librarian is the router — the semantics coincide; (ii) it makes the migration free, because a deployment whose single chat is mapped to the Librarian today keeps behaving identically and topics are purely additive (TG-75); (iii) every other channel's agent is named by its topic title at the top of the screen, and General is the **only** channel whose title (*"General"*) names no agent — so it is the one place TG-1's ambiguity can survive, and the Librarian is the one agent for which that ambiguity is harmless, since routing is what it does. **Against, and the reason this is not a hard rule:** today's deployments map their single chat to whatever agent they wanted on their phone, frequently one expert, and a rule would break every one of them at upgrade for a stylistic gain. So a non-Librarian General is legal, warned at startup, and named in that channel's `/agents` output — visible, never silent. |
| **AA** | **The bot creates topics, and only ever in response to an explicit human command in the chat (`/channels`).** Never at startup, never when the catalog gains an agent, never inferred from traffic. | The alternative — the daemon creating one topic per catalog agent at boot — reads attractive and is wrong three ways. A knowledge base with thirty topics produces a phone chat with thirty channels, twenty-eight of which the human never opens, and the *useful* four are then buried; the bot's first act after an upgrade becomes a burst of writes into the human's own chat that no single action undoes; and a partial failure mid-burst leaves a half-populated chat whose state is not derivable from anything, because **there is no API to enumerate a chat's topics** (F-5's corollary). Creation on request keeps TG-3's ruling intact in substance — *"creating a topic does not create a Telegram channel; the mapping is human-configured"* — and changes only the mechanism by which the human expresses the decision: a command in the chat instead of a hand-edited id they cannot see. |
| **AB** | **The channel directory is durable bot-owned state (`pkb_telegram_channels`), not deployment configuration, and TG-17 is amended to say precisely what it protects.** | This is the sharpest tension in the section and it deserves a straight answer. TG-17 forbids the bot mutating the mapping, and its stated reason is *"a mapping the bot writes is one that changes without the human"*. Under decision AA nothing changes without the human: the **decision** — that `topic/cooking` is reachable from the phone — is made by an owner typing a command, and the id Telegram mints in reply is not a decision, it is an **address**. TG-17's guarantee survives word for word. The topic id also *cannot* be configuration: it is minted by Telegram, invisible in every Telegram client, and unenumerable afterwards, so a human hand-editing it is not a workflow that exists. One carve-out is stated rather than hidden: TG-82's bounded recreation writes a new topic id without a new command, because it re-addresses an existing decision rather than making a new one. |
| **AC** | **The truth about a topic is the `message_thread_id` on the send *response*, and every send that carries one compares.** A mismatch means the topic was deleted; the keyboard on the stray message is cleared **first**, then the channel is repaired at most twice, then retired. | F-2. This is the whole design and it is worth restating as the failure it prevents: the human deletes the Cooking topic; the bot keeps sending Cooking's replies and Cooking's **approval keyboards** with a dead `message_thread_id`; Telegram silently drops them into General, where they are indistinguishable from the Librarian's; and the human taps Approve on an irreversible write attributed to the wrong expert, with no undo (D6). Nothing in the error path can detect this — the send returns `ok: true`. Clearing the stray keyboard before the repair, rather than after, is the ordering that matters: a message is dangerous only while its buttons are live, `clear_keyboard` needs the `message_id` the very same response just handed back, and the repair involves a `createForumTopic` and a re-send that can each fail. |
| **AD** | **The stray message's text is left standing; only its buttons are removed.** `deleteMessage` is not added to the `BotApi` Protocol. | Same reasoning as TG-63's amendment of 2026-08-08: a chat is the only surviving record of what the human was asked, on a system with no undo, and destroying it at the moment the machinery misfires is the worst possible time. A stray message that says what it says, with dead buttons and a correction under it, tells the human what happened; a message that vanishes tells them nothing and looks like a bug in the bot. `deleteMessage` also carries its own 48-hour window and its own error path, i.e. a second failure mode bought for a cosmetic gain. |
| **AE** | **Attribution is asymmetric: an ordinary reply inside an agent's own channel carries no prefix; every approval names its agent, and every message delivered *outside* its agent's own channel is prefixed with the agent id.** | The question is real — a topic header is attribution in a way a text prefix is not — but it is only true *while you are inside the topic*. Scrollback in General, a forwarded message, a lock-screen notification preview and TG-82's retired-to-General fallback all strip the header and none of them strip a first line. So the rule follows the exposure rather than the channel: prefixing every reply in a conversation the human is already inside is noise that trains them to skip the first line, which is exactly where the approval attribution has to be legible. Approvals are named unconditionally because an approval is the one message where being wrong about *which expert* is an irreversible write to the wrong topic — and because Q20's own wording already required it (defect 3). |
| **AF** | **`/talk` is not built.** With a channel per expert, the addressee is the topic the human is typing in. | An in-band agent selector would put the bot back into modal addressing — a hidden "current agent" that a message inherits and that the human cannot see at the moment they hit send. That is `/connect`, which TG-1 deleted by name, and the reason has not changed: *the failure is invisible*. A topic title is visible above the keyboard on every send. Building both would mean two answers to "which expert am I talking to?", which is one more than the design tolerates. |

---

### 9.3 Rule table — TG-72 onward

Same conventions as §0: stable ids, `no key` / `live` on every assertion, and every rule states the
failure it prevents.

#### 9.3.1 Addressing and the channel — TG

| ID | Rule | Source | Sev | Test assertion (live?) |
|----|------|--------|-----|------------------------|
| TG-72 | **The routing key is the channel `(chat_id, topic_id)`, never `chat_id` alone**, where `topic_id` is the inbound `message.message_thread_id` or **`0`** for General. Every piece of per-chat state is re-keyed on it: the thread binding (TG-26), the turn lock (TG-39), the unmapped-reply window (TG-23), the ledger's chat column (TG-29) and the prompt row (TG-57/TG-60). No code path may key on `chat_id` alone except an outbound rate/queue concern, which is genuinely per chat (TG-94). | decision Y; TG-1 (amended); F-1 | error | A grep-level assertion that `store` methods taking a chat take a topic beside it; two messages in two topics of one chat produce **two** `create_thread` calls and two independent bindings; a message in General and a message in a topic never share a thread. **no key** — *Why*: a single missed key is a message filed into the previous topic's expert — the exact mis-file TG-1 exists to prevent, now reachable without any configuration change. |
| TG-73 | **General (`topic_id == 0`) is a mapped channel like any other**, and its agent is `config.chats[chat_id]` — unchanged in meaning, unchanged in file format. The daemon **warns** at startup when a chat's General is not the Librarian, and that channel's `/agents` output names its agent explicitly. A chat whose General names no agent is answered by TG-2 as today. | decision Z; TG-1, TG-2, TG-17; Q26 | warning | A config mapping a chat to `librarian` starts with no warning; one mapping it to `topic/cooking` starts, warns naming both, and works; `/agents` in General names the agent in both cases. **no key** — *Why*: General is the only channel whose title names no agent, so it is the only place the "which expert?" ambiguity can survive topics. A hard rule would break every existing single-expert deployment at upgrade; a warning plus a self-naming `/agents` makes it visible instead. |
| TG-74 | **A message from an unbound topic of a mapped chat gets TG-2's treatment, scoped to that topic**: the reply is posted **in that topic** and names the exact `/channels <agent-id>` command (**amended 2026-08-09**: *and the topic id* is struck — a topic id is invisible in every Telegram client, so it is noise the human cannot act on; §9.10), and — because the allow-list has already admitted the sender (TG-20) — **may list the agent ids that have no channel**. The window is per channel (TG-23 amended). The message runs nothing and its text is not stored (TG-22). | TG-2, TG-20, TG-21, TG-22, TG-23; decision AA | error | A message in an unknown `message_thread_id` of a mapped chat: zero `start_run`, one reply **carrying that `message_thread_id`**, listing unmapped agent ids; ten such messages produce one reply. **no key** — *Why*: TG-21 withholds agent ids because the bot's username is discoverable and topic titles are the sensitive part of a private knowledge base. That reasoning is about **strangers**, and the allow-list runs first (TG-20 as built), so the only sender who can reach this path is the owner — for whom the list is the one thing that makes the command usable. |
| TG-75 | **Topic mode is discovered once, at startup, from `getMe.has_topics_enabled`**, published on `/health` as `telegram.topics_enabled`, and re-probed only on a restart. **When it is false the adapter behaves exactly as the pre-topics build**: no `message_thread_id` on any send, no `createForumTopic`, no channel directory writes, and `/channels` answers with one line naming the BotFather **Threaded Mode** toggle. Every existing binding, ledger row and prompt row keeps working. | F-1, F-4 (executed: the real bot answers `false` today); Q25; AP-18 | error | With a fake returning `has_topics_enabled: false`, the **entire pre-topics suite passes unchanged** and no payload anywhere contains `message_thread_id`; with `true`, the same suite passes with every send carrying `0` for General. `/health` publishes the flag either way. **no key** — *Why*: this is the migration guarantee. The toggle is off by default, it is the human's to flip, and they may never flip it. A deployment that upgrades and finds its bot broken because a feature it did not ask for assumed a toggle is the worst possible outcome of an additive change. |

#### 9.3.2 Creation, the directory and the catalog — TG

| ID | Rule | Source | Sev | Test assertion (live?) |
|----|------|--------|-----|------------------------|
| TG-76 | **A topic is created only in response to `/channels` from an allow-listed owner** (TG-87). Never at startup, never when the catalog gains an agent, never on an inbound message, never on a restart. The adapter calls `createForumTopic` from exactly one code path. | decision AA; TG-3; TG-20 | error | Booting an adapter whose directory is empty against a catalog of thirty agents issues **zero** `createForumTopic` calls; adding an agent to the catalog mid-session issues zero; grep finds exactly one call site. **no key** — *Why*: eager creation buries the four channels the human uses under twenty-six they do not, cannot be undone in one action, and — since no API enumerates a chat's topics (F-5) — leaves a state after a partial failure that nothing can reconstruct. |
| TG-77 | **The channel directory is durable, in `pkb_telegram_channels`, and holds at most one channel per `(chat_id, agent_id)`.** A `/channels` naming an agent that already has one in this chat **creates nothing** and answers with a pointer to the existing channel. The directory is the bot's own bookkeeping and is never read from the knowledge base, never derived from KB content and never written by an agent. | decision AB; TG-17 (amended), TG-28; I3, ST-3, ST-7 | error | Two `/channels topic/cooking` in one chat produce one `createForumTopic` and one directory row; a fresh adapter over the same store routes that topic without re-creating it; the row survives fifty supervised restarts. **no key** — *Why*: two channels for one agent in one chat is two independent conversations with one expert, invisibly diverging — TG-25 permits that across chats deliberately, and permitting it accidentally within one chat is how a human's Cooking history splits in half. |
| TG-78 | **`createForumTopic` is called with the agent's catalog `title` and nothing else** — no `icon_color`, no `icon_custom_emoji_id`. **The bot never renames, edits, closes, reopens or deletes a topic**: `editForumTopic`, `closeForumTopic`, `reopenForumTopic`, `deleteForumTopic` and `unpinAllForumTopicMessages` are **not** in the `BotApi` Protocol. The binding is by topic **id**, so a human renaming a topic changes nothing. | §8.1's precedent for dropping `send_chat_action`; `AgentDescriptor.title` (GE-25); TG-63's reasoning; D6 | error | `createForumTopic` receives exactly `chat_id` and `name`; the Protocol has no method whose name contains `delete`, `close` or `edit_forum`; a renamed topic keeps routing to the same agent. **no key** — *Why*: every method on this Protocol has to be implemented by every fake, and a parameter with no rule behind it is cost with no failure prevented. Deleting or renaming is worse than useless: the topic is the human's record of what they approved, the rename may have been deliberate, and a bot that tidies the human's chat is a bot that destroys evidence on a system with no undo. |
| TG-79 | **An agent that leaves the catalog retires its channel; the Telegram topic is left standing.** That channel is answered exactly like an unmapped one (TG-18's twin), its agent id is published in `telegram.invalid_chats`' companion field, and nothing is deleted. Re-adding the agent under the same id revives the channel with its binding intact. | TG-18; TG-78; decision AD | error | Remove an agent from the catalog: that topic's next message runs nothing and gets the unmapped-style reply, `/health` reports it, `deleteForumTopic` is never called; restore the agent and the same thread resumes. **no key** — *Why*: TG-18 already ruled this shape for chats — report, never die, never route anyway — because a topic can be renamed under a running config. The only new part is that the *Telegram* topic outlives the KB one, which is correct: it holds the human's history of a topic they may be in the middle of splitting. |

#### 9.3.3 The deleted-topic hazard — TG

*Every rule here is about a message that Telegram accepts with `ok: true` and delivers to the wrong
place.*

| ID | Rule | Source | Sev | Test assertion (live?) |
|----|------|--------|-----|------------------------|
| TG-80 | **The `message_thread_id` on the send *response* is the truth; the one sent is a request.** Every send carrying a non-zero `message_thread_id` compares the value on the returned `Message` against the one it sent. A difference, or its absence, means **the topic is gone** and the message has landed in General. The comparison is unconditional — it is not sampled, not limited to approvals, and not skipped for notices. | F-2 (tdlib/telegram-bot-api#854); decision AC; D6 | error | A fake that accepts a stale `message_thread_id`, returns `ok: true` and echoes a `Message` **without** it: the adapter detects the loss on the **first** send, marks the channel dead and takes TG-81's path. A companion test pins that the fake raises **no** error, so a version of the adapter that only inspects exceptions fails it. **live**: one `@pytest.mark.live` test sends into a deleted topic on a throwaway bot and asserts `ok: true` with a relocated `message_thread_id`, because the fake's fidelity on exactly this point is the design's foundation. |
| TG-81 | **The stray message is disarmed before anything else.** On a TG-80 mismatch the adapter, in this order: (1) calls `clear_keyboard(chat_id, response.message_id)` if the send carried one, (2) posts one plain line in General saying which topic was deleted and that the message below it was meant for that expert, (3) then repairs (TG-82). The stray **text is never deleted** and `deleteMessage` is not in the Protocol. | decision AC, AD; TG-63; D6 | error | A stray approval send: `clear_keyboard` is the **next** call after the send, before any `createForumTopic`; the correction message follows; the original text is untouched; no method named `delete_message` exists. A stray *plain* message issues no `clear_keyboard`. **no key** — *Why*: a message is dangerous only while its buttons are live, and the response that revealed the problem already carries the `message_id` needed to disarm it. Repairing first means a `createForumTopic` failure leaves an approve button for an irreversible write sitting in General under the wrong expert's name — which is the exact failure this whole section is arranged around. |
| TG-82 | **Repair is bounded and durable.** A dead channel is recreated at most **twice** (`MAX_RECREATIONS = 2`), counted in the directory row so the bound survives a restart, and the pending message is re-sent into the new topic. Past the bound the channel is **retired**: the agent's traffic goes to General with the agent id as its first line (TG-85), the human is told **once**, and no further `createForumTopic` is issued for it until a `/channels` command asks. **Amended 2026-08-09 (§9.10)**: retirement is a **channel's** state and its routing seed is read back per chat, never from the chat-less `retired_agents()`; `/channels <agent-id>` **revives** it in the process as well as in the row, or the way out the notice names creates a permanently silent topic; a recreation **carries the binding over** to the new topic, because a repair is not a rotation and the thread it moves is frequently holding the very approval being re-sent; and an unattributed send resolves its agent from the directory, because the orphan report is the message most likely to find a topic deleted and it carries no agent id. | decision AC; TG-23's rate-limit reasoning; Q20's "an approval must never be undeliverable" | error | Delete the topic three times in a fixture: two recreations, then retirement with one notice and zero further creates; a restart between deletions does not reset the count; the retired channel's messages arrive in General prefixed with the agent id. **no key** — *Why*: unbounded recreation is a loop against a human deliberately deleting a topic, and each turn of that loop is a `createForumTopic` plus a notification. Refusing to repair at all is worse: the expert's approvals become undeliverable, which is precisely the outcome Q20 rejected. Two is the smallest number that survives an accidental deletion and a fat-fingered second one without becoming a fight. |
| TG-83 | **`400 message thread not found` is the same fact as a TG-80 mismatch** and takes the identical path — with one difference stated in the code: **nothing was delivered**, so there is no stray message and TG-81's steps (1) and (2) are skipped. The adapter never treats it as retryable and never counts it toward TG-8's transport backoff. | F-3 (tdlib/telegram-bot-api#847); TG-8; TG-48 | error | A fake 400ing with that description: the channel is marked dead, no `clear_keyboard` is issued, the repair runs, `restarts == 0` and `with_retry` does **not** re-send to the dead id. **no key** — *Why*: the report is unresolved, so the honest position is that both behaviours exist in the wild. "The topic is gone — recreate and re-send" is the correct handling under either, which is what makes it safe to rule on an open bug. Retrying the send instead would re-issue the same dead id up to the retry bound, three 400s per message forever. |
| TG-84 | **A channel known to be dead is never sent to with its stale id.** Between detection and repair — and permanently after retirement — the agent's messages go to General with the TG-85 prefix. A queued outbox item addressed to a channel that died while it waited is re-addressed, never dropped and never sent blind. **Amended 2026-08-09 (§9.10)**: a repair compares its channel against the directory row **before** creating anything — a row naming a different topic means the channel already moved, so re-address and create nothing. Reachable by an ordinary restart, because prompt rows (TG-57) and ledger rows (TG-29/TG-31) keep naming the dead id and `_moved` is process memory. | TG-80, TG-82; TG-49 ("never a `MessageComplete`, an `InterruptEvent` or a terminal frame") | error | Kill a topic mid-run with three frames still in the outbox: zero sends carry the dead id, all three arrive in General prefixed, and none is dropped. **no key** — *Why*: TG-80 detects one stray per send. Without this rule a fan-out with eight queued messages produces eight strays and eight corrections, and the human's chat becomes unreadable at exactly the moment something needs approving. |

#### 9.3.4 Attribution, approvals and the command surface — TG

| ID | Rule | Source | Sev | Test assertion (live?) |
|----|------|--------|-----|------------------------|
| TG-85 | **Attribution follows exposure, not the channel.** (a) An **approval** message always names its agent; a fan-out (derived-thread) approval and every hand-off also name the **thread id**. (b) Any message delivered **outside its agent's own channel** — General fallback, a retired channel (TG-82), a `/pending` keyboard with nowhere else to go (TG-88) — carries the agent id as its first line. (c) An ordinary reply inside the agent's own channel carries **no** prefix. **Amended 2026-08-09 (§9.10)**: (a) and (b) are **exclusive**. A caller that has already named its agent suppresses the generic prefix, and asks about exposure *before* the send so the attribution leads. Both fired at once on a retired channel's approval, printing the agent id as line 1 and again as line 2 — one line, moved, not repeated. | decision AE; Q20's own wording ("naming the expert and the derived thread"); LB-16, TG-53, TG-59 | error | A fan-out approval posted into General names the expert **and** `<parent>::topic/cooking`; the same approval on the chat's own thread names the expert and not the id; an ordinary reply in `topic/cooking`'s channel starts with the reply's own first character. Golden-text assertions, not substring searches. **no key** — *Why*: this is defect 3. Q20 required the naming and the build never did it, so a fan-out approval arrived as an unattributed diff with an approve button — under topics that becomes an approve button in the *Librarian's* channel for a write into Cooking. A topic header is attribution only while you are inside the topic; scrollback, forwards and notification previews all strip it and none of them strips a first line. |
| TG-86 | **The command surface becomes six: `/new`, `/threads`, `/agents`, `/pending`, `/cancel`, `/channels`.** The five existing commands act on the **channel** they were typed in — `/new` rotates that channel's thread, `/threads` lists that channel's agent's threads, `/agents` names that channel's agent and lists this chat's channels, `/cancel` cancels that channel's most recent run. **There is no `/talk`, no `/connect`, and no in-band agent selector of any kind.** | TG-39 (amended); decisions Y, AF | warning | `/cancel` in one topic does not touch a run in another; `/new` in General leaves the Cooking binding intact; `/talk` is not a command and is not mentioned in the unknown-command reply. **no key** — *Why*: a `/cancel` that reaches the wrong turn is worse than no `/cancel` — it stops a turn the human wanted and leaves the one they were trying to stop writing into a tree with no undo. `/talk` would restore the hidden "current agent" that `/connect` was deleted for; the topic title is visible above the keyboard on every send and a mode is not. |
| TG-87 | **`/channels` is the whole creation surface**, and it is exactly three forms: **`/channels`** lists this chat's channels and the agents that have none; **`/channels <agent-id>`** ensures that agent has a channel here — binding the **current** topic if it is unbound, otherwise creating a new one; **`/channels all`** creates one for every agent with none. Every form **states which of the two things happened** in its reply. With Threaded Mode off (TG-75) all three answer with the BotFather instruction and create nothing. | decision AA; TG-3, TG-76, TG-77; F-4 | error | `/channels` in an unbound topic binds *that* topic and says so; the same command in General creates a new one and says so; `/channels all` over a catalog of five with two bound issues three creates; every reply distinguishes bind from create; with topics off, zero creates and one instruction. **no key** — *Why*: the bind-here form is the only recovery path for a lost SQLite file, because **no API enumerates a chat's topics** (F-5) — without it, a restored deployment can only abandon its existing topics and make new ones beside them. One command with two behaviours is a real ambiguity, and it is paid for by the reply naming which one occurred, after the fact, in the chat. |
| TG-88 | **`/pending` stays cross-agent, and its keyboards go to their own agents' channels.** The summary list is posted to the channel the command was typed in; each approval's keyboard is posted to **that approval's agent's channel** when one exists, and to the typing channel with the TG-85 prefix when it does not. | Q19 (RULED, pull-only); TG-39; TU-12's cross-agent "needs you" view; decision AE | error | `/pending` in General with approvals parked on two experts: one summary in General, one keyboard in each expert's channel; with one expert unchannelled, its keyboard lands in General prefixed. **no key** — *Why*: this looks like it contradicts TG-89 and does not, and the difference is worth stating. A fan-out approval has an **originating channel** — the human just typed there and is looking at it. A `/pending` approval has none: it may have been raised in the TUI hours earlier, and its agent's own channel is the only place with a claim on it and the place the human will look for that expert tomorrow. |
| TG-89 | **Q20 is re-ruled with per-expert channels available, and re-affirmed: a fan-out approval is posted to the *originating* channel**, not the expert's, naming the expert and the derived thread (TG-85). | Q20 (re-ruled 2026-08-09); LB-16, CL-8, TG-53, TG-59; TG-3 | error | A Librarian turn in General that gates on `<parent>::topic/cooking` posts the keyboard **in General**, naming the expert and the derived id, while `resolve()` still routes to `request.thread_id`; the Cooking channel receives nothing. **no key** — *Why*: Q20 chose the originating chat because per-expert channels did not exist. They do now, and the answer does not change, for a **new** reason: under decision AA most agents have **no** channel, so routing a fan-out approval to the expert would make it undeliverable in the ordinary case — arch §8's headline failure with an approve button. Two more reasons survive: an approval that appears in a channel the human is not looking at is a notification about a decision they cannot place, and splitting one submission's outcome across N channels means the human learns the result of one paste in three places. Option (c) — a pointer in the expert's channel — is still rejected: CL-20's duplicate affordance, and a pointer with no buttons is a notification that cannot be acted on. |

#### 9.3.5 Mechanics that must not be assumed — TG

| ID | Rule | Source | Sev | Test assertion (live?) |
|----|------|--------|-----|------------------------|
| TG-90 | **Only the send family carries `message_thread_id`.** `edit_message`, `clear_keyboard` and `answer_callback` address a message by `chat_id` + `message_id` (or query id) and are **never** given a topic. TG-63 therefore needs no topic to clear a keyboard, and the prompt row's `topic_id` exists for **re-sends**, not for edits. | F-6; TG-63; §8.1's shipped Protocol | error | `inspect.signature` of every `HttpBotApi` edit/answer method has no thread parameter, and the fake 400s if one is passed; a keyboard is cleared successfully for a message posted in a topic **and** for one posted in General, through the same call shape. **no key** — *Why*: the obvious assumption is that a topic-scoped message needs a topic-scoped edit, and acting on it puts an unknown parameter on the one call that disarms an irreversible button. TG-63's whole job fails silently if that call 400s inside the terminal-outcome path. |
| TG-91 | **`allowed_updates` is unchanged** — `["message", "edited_message", "callback_query"]`. Topic lifecycle arrives as service messages inside `message`, and **no update exists for a deleted topic**. | F-5; TG-34 | warning | The literal value asserted in the request the fake receives is byte-identical to the pre-topics one. **no key** — *Why*: the reflex on a new Telegram feature is to subscribe to a new update kind, and here that would be subscribing to something that does not exist while implying the deletion case is covered. It is not covered and cannot be — TG-80 exists precisely because deletion is unobservable. |
| TG-92 | **A service message runs nothing and is never answered with TG-36's attachment refusal.** Any `message` carrying `forum_topic_created`, `forum_topic_edited`, `forum_topic_closed`, `forum_topic_reopened` (or carrying neither text nor a human-supplied attachment) is admitted, ledgered and dropped in silence — with **one** exception: a `forum_topic_created` from an allow-listed owner in a mapped chat gets TG-74's binding offer, posted in the new topic. | F-5; TG-36, TG-22, TG-29 | error | A `forum_topic_created` update produces zero `start_run`, zero attachment refusals and exactly one binding offer inside the new topic; a `forum_topic_closed` produces zero messages; both are ledgered so the offset advances. **no key** — *Why*: as built, a message with no `text` falls into TG-36 and is answered *"I cannot take attachments; here is your caption"*. So the human's own act of creating a topic — the first thing they will do after enabling Threaded Mode — would be answered with a refusal about a photo they did not send. The exception is the other half: a topic the human made by hand is useless until something tells them how to bind it. |
| TG-93 | **The turn lock is per channel.** `_chat_lock` becomes `_channel_lock`, keyed on `(chat_id, topic_id)`. A turn in one topic never blocks a turn in another; commands and button presses still take no lock. | TG-39 (as built: "serialized per chat by a lock"); CLAUDE.md's measured 16 s / 284 s turns | error | A turn held open in Cooking's channel does not delay a turn started in Woodworking's; both `start_run` calls are in flight together; two messages **in one channel** still serialize. **no key** — *Why*: the lock exists because three lines typed as three messages in one conversation must not become three `ThreadBusyError` refusals. Nothing about that argument reaches across topics — and left per chat, a single 284-second local-fallback turn in one topic makes every other expert on the phone unresponsive for five minutes, with no visible cause. |
| TG-94 | **The rate budget is per chat, not per topic**, so the outbox stays **one queue per chat** and TG-43's coalescing is unchanged. N channels do not buy N times the send budget, and this is stated so nobody "parallelises" the pump per topic. | F-7; TG-43, TG-48; §8.5's stated deferral | warning | A burst across three channels of one chat is paced against one budget; the outbox is keyed by chat and the pump count does not scale with channels. **no key** — *Why*: §8.5 already records that one pump per chat costs latency across chats. Topics multiply the *appearance* of independence without multiplying the budget, so the tempting fix — a pump per topic — buys nothing and earns 429s whose `retry_after` stalls the approval messages too. |
| TG-95 | **The owner allow-list is unchanged and is not per topic.** `owner_user_ids` is checked on every `message.from.id` and every `callback_query.from.id` exactly as TG-20 specifies; a topic changes what a message is *addressed to*, never who may say yes. | TG-20, decision X; C-33 | info | A non-allow-listed sender in a bound topic is ignored silently, identically to today; a press from one gets the refusal alert and zero `resume`. **no key** — *Why*: stated rather than assumed, because "a channel per expert" invites the idea of per-channel permissions. There is exactly one authorization boundary in this system and adding a second, weaker one beside it is how the first stops being checked. |

---

### 9.4 Amendments to Layer 5 rules, made in place

Each of these is marked **Amended 2026-08-09** in §1 with its original wording preserved. The reason
is here; the amendment text is on the rule.

| ID | What changes | Why |
|----|--------------|-----|
| **TG-1** | *"A `chat_id → agent_id` mapping"* becomes *"a **channel** → `agent_id` mapping"*, where a channel is `(chat_id, topic_id)`. The guarantee is unchanged in force: exactly one agent per channel. | Decision Y. The rule's purpose — no ambiguity about which expert is being addressed — is preserved exactly; only the ceiling of one agent per human is removed. |
| **TG-2** | *"an unmapped chat"* becomes *"an unmapped **channel**"*. | TG-74. An unbound topic inside a mapped chat is a new case that did not exist, and defaulting it to the chat's General agent would be the silent mis-file TG-2 forbids. |
| **TG-3** | *"the daemon reports agents that have no chat"* becomes *"…that have no **channel**"*, and *"the mapping is human-configured"* gains: the human's decision is now expressed as a `/channels` command instead of a hand-edited id, because a topic id is minted by Telegram, invisible in every client, and unenumerable afterwards. | Decisions AA and AB. The substance — the daemon does not decide what is reachable from the phone — is untouched. |
| **TG-11** | `unmapped_agents` subtracts `health.telegram.agents`, which now carries the union of the configured mapping's values **and** the channel directory's agents; the daemon seeds it from the store at startup and the adapter updates it on every create. Two fields are added to the telegram block: `topics_enabled` (TG-75) and `channels` (the directory's size). | The `/health` **endpoint** does not change at all — it already computes `{catalog} - health.telegram.agents` (`app.py:161`). Seeding from the store in the composition root rather than in the bot is what keeps TG-11's stated property true: the answer survives a crash-looping bot, which is when `/health` is read. |
| **TG-17** | Gains: what the bot may never write is the **human's decision** about which agents are reachable and what they are called. The channel directory is not that decision — it is the address Telegram minted in reply to a command the human typed (TG-77). One carve-out is named: TG-82's bounded recreation. | Decision AB. TG-17's own stated reason is *"a mapping the bot writes is one that changes without the human"*, and under decision AA nothing changes without the human. Left unamended, the rule reads as forbidding the only mechanism by which a topic id can exist. |
| **TG-21** | Gains: the two-facts limit applies to an **unmapped chat**, where the sender may be anyone who found the bot. In an unbound **topic** of a mapped chat the allow-list has already admitted the sender (TG-20 runs first, as built), so the reply may list agent ids (TG-74). | The rule's stated reason is that topic titles are the sensitive part of a private knowledge base and the bot's username is discoverable. Neither applies once the sender is a known owner — and withholding the ids there makes `/channels <agent-id>` unusable, since the human has no other way to learn them from the phone. |
| **TG-23** | The one-reply-per-window is per **channel**, and the `_WARNED_CAP` bound is per channel. | An unbound topic and an unmapped chat are different explanations. Rate limiting them together means the first unbound topic silences the explanation for the second. |
| **TG-25** | Restated: two **channels** may address one agent — across chats, deliberately (unchanged) — but **not two channels in one chat** (TG-77). | Two conversations with one expert in one chat is the human's Cooking history split in half, invisibly. Across chats it was already a deliberate, visible arrangement. |
| **TG-26 / TG-27** | *"One current thread per chat"* becomes *"per channel"*; `/new` rotates the channel it is typed in. The agent-mismatch rotation (§8.3) is per channel too. | Decision Y. One thread per chat under topics would mean every expert in a chat sharing one conversation — the mis-file TG-1 exists to prevent, and worse, because the human would see distinct topics and assume distinct threads. |
| **TG-28** | The `pkb_telegram_*` tables gain `topic_id INTEGER NOT NULL DEFAULT 0`. Migration is **additive only**: `ADD COLUMN` guarded by `PRAGMA table_info`, plus `CREATE UNIQUE INDEX IF NOT EXISTS` — never a table rebuild, never a PK change. Existing rows become General bindings, which is exactly right: the conversation the human was having continues in General. | ST-3 was measured — a transaction held across an `await` killed a concurrent checkpointer run. A PK change on SQLite is a rebuild of the whole table on the checkpointer's own connection at startup, which is the one operation that discipline forbids. `NOT NULL DEFAULT 0` is legal as an `ADD COLUMN` on a populated table and needs no rewrite; a nullable topic column would let two General rows coexist, because SQLite treats NULLs as distinct in a unique index. |
| **TG-29** | The ledger's `chat_id` is joined by `topic_id`, so the *"I lost your message"* notice reaches the channel that lost it. | §8.3 already had to fix this once in the chat dimension — the notice was broadcast to every mapped chat and reached, for the ordinary deployment, no chat at all. Reaching the right chat but the wrong topic is the same defect one level down. |
| **TG-31** | The three re-sync branches post into the **channel** the unfinished update came from. | Same reason. A restart that re-posts Cooking's approval keyboard into General is TG-80's failure without the deletion. |
| **TG-34** | Gains a note: no new update kind is subscribed for topics, and none exists for deletion (TG-91, F-5). | The rule is auditable-in-one-line by design; a reader who knows 9.3 shipped needs to see that the line did not change and why. |
| **TG-39** | The command surface becomes six with `/channels`; every command acts on its channel; `/talk` is explicitly not built. **And the stale rebind claim is struck** — see TG-40. | Decisions AA and AF. |
| **TG-40** | **Defect 4.** The clause *"and rebinding the chat to one of those ids is the supported cross-channel resume (D3)"* is **struck**: nothing rebinds a chat to a listed thread, no command does, and none is added here. `/threads` is a **read-only listing**, and its own text says so. What is true is stated in its place: a Telegram-started thread is visible and finishable in the **TUI** (D3, `origin_channel="telegram"`), and a TUI-started thread's **approval** reaches the phone through `/pending` (Q19(a)). The ordering, the no-derivation and the `expert_thread_id` clauses are unchanged. | The claim has been false since the rule was written, and it is the kind of false that costs a day: a reader implementing D3's story from this rule looks for the rebind path, finds none, and cannot tell whether it was dropped or never existed. Building it instead was considered and deferred with its reason: a thread id is 36+ characters typed on a phone (P-28 measured why ids and phones do not mix), and a durable rebind silently moves a channel into another conversation — TG-59's exact hazard, one command earlier. |
| **TG-53 / TG-56** | **Defect 3.** Both gain the naming requirement now stated positively in **TG-85**: every approval names its agent, and a fan-out approval also names the derived thread. | Q20's own wording already required *"the originating chat, naming the expert and the derived thread"* and the build named neither — measured on the shipped `_post_action`, the button message carries `tool · reason` and nothing else. Under topics that becomes an approve button in the Librarian's channel for a write into Cooking, which is indistinguishable from the Librarian's own. |
| **TG-57** | Unchanged, and the reason is recorded: the handle indexes the prompt row, and the **row** gains `topic_id`. Nothing about the channel enters `callback_data`. | The budget has not moved. A chat id was already refused at 64 bytes (P-28); a chat id plus a topic id is further over, and the durable row already exists for exactly this. |
| **TG-63** | Gains: clearing a keyboard needs **no** topic (F-6, TG-90). The message is addressed by `chat_id` + `message_id`, in a topic exactly as in General. | The natural assumption is the opposite, and acting on it puts an unknown parameter on the one call that disarms an irreversible button. Stated on the rule so nobody has to rediscover it from a 400 in a `finally`. |

---

### 9.5 Amendments outside Layer 5

Two of the four approved defects live in other layers. **Their rule text must be amended in its own
spec in the same commit** — this section records the exact change and the executed evidence so the
build has a rule to cite, not so the amendment can live here.

| Rule | Spec | Amendment | Why |
|------|------|-----------|-----|
| **SV-12** | [Layer 3 rules](2026-08-07-pkb-service-server-layer3-rules.md) | **Defect 1.** Shape-first resolution of a derived thread id **requires the parent to exist as a registered row**. `<parent>::<agent-id>` remains openable, runnable, resumable and self-registering **only when `<parent>` is a thread that exists**; when it is not, the id is `UnknownThreadError` — a 404 over HTTP — and **no row is created and no run is started**. The original ruling and its test are otherwise untouched. | Executed: `POST /threads/<fresh-uuid4>::topic/cooking/runs` returns **200** with a full event stream, runs a real expert turn, and registers a permanent `kind:"routed"` row whose `parent_thread_id` names a thread that **404s**. SV-12's stated purpose is that *"the row is an index for discovery, never the authority on existence — the checkpoint is"*, and that argument holds for a thread the fan-out really created and whose row was lost. A derived id whose parent never existed has no checkpoint and never had one: nothing was lost, so there is nothing to recover, and self-registering it manufactures an orphan that `/threads` lists forever and that no cascade will ever delete. The real case is unaffected — after a fan-out the parent row always exists, because `create_thread` made it. **Acceptance**: SV-12's original test (delete the derived row, resume, it reappears) still passes; the new one asserts 404, zero rows and zero `start_run` for a fabricated parent. |
| **PR-3 / PR-9** | [Layer 2 rules](2026-08-06-pkb-agents-layer2-rules.md) | **Defect 2.** The expert prompt must state that material reaches it **two ways** — routed by the Librarian, or handed to it directly by the human in a conversation with this expert — and the fan-out clauses must be **scoped to the routed case**. Concretely, in `src/pkb/agents/prompts/expert_template.md`: *"Ingest what is routed to you"* becomes *"Ingest what reaches you — routed by the Librarian, or handed to you directly"*; *"The same item often reaches several topics at once"* is qualified with *"when the Librarian routed it"*; and *"anything the Librarian routed here"* gains the direct case. One prompt, both cases named — **not** two prompts and **not** a conditional on the thread's shape. | Three of four surfaces already open a direct thread on an expert, so the prompt's provenance claim is false on every one of them. The consequence is behavioural, not cosmetic: told that *"the same item often reaches several topics at once"*, a model handed a source directly deliberately **under-extracts** — takes only its own facets on the belief that a second expert holds the rest — and PR-9's decline clause then makes filing nothing a *correct* outcome for material the human handed it on purpose. The human's source is silently half-filed by a prompt describing a fan-out that never happened. Parameterising the prompt per thread shape is rejected for the reason RO-22 and TG-33 already give: a conditional on the thread's provenance is the `if origin_channel == …` class of mistake, and it would make an expert answer differently depending on how the human reached it. |

---

### 9.6 The contract

#### 9.6.1 `pkb.service.telegram` — the store, additively

```python
# ADDITIVE ONLY (TG-28 amended). `ADD COLUMN` guarded by PRAGMA table_info; a unique index; never a
# table rebuild and never a PK change — ST-3 was measured, and a rebuild on the checkpointer's own
# connection at startup is exactly the long transaction that discipline forbids.
#
# topic_id INTEGER NOT NULL DEFAULT 0   — 0 is General. Telegram's topic ids are message ids and
#                                         start at 1, so 0 is permanently free. NOT NULL because
#                                         SQLite treats NULLs as DISTINCT in a unique index, which
#                                         would let two General rows coexist for one chat.

pkb_telegram_bindings (chat_id, topic_id, thread_id, agent_id, bound_at)   # PK-equivalent index on
                                                                          # (chat_id, topic_id)
pkb_telegram_updates  (update_id PK, chat_id, topic_id, thread_id, run_id, state, created_at)
pkb_telegram_prompts  (handle PK, chat_id, topic_id, message_ids, thread_id, interrupt_id,
                       answers_json, created_at)
pkb_telegram_channels (chat_id, topic_id, agent_id, created_at, recreations)   # NEW — TG-77
                                                                              # unique (chat_id, agent_id)
                                                                              # unique (chat_id, topic_id)

class TelegramStore(Protocol):    # every chat-keyed method gains topic_id; new methods below
    async def channels(self, chat_id: int) -> Mapping[int, str]: ...          # topic_id → agent_id
    async def channel_agents(self) -> frozenset[str]: ...                     # seeds /health, TG-11
    async def open_channel(self, chat_id: int, topic_id: int, agent_id: str) -> None: ...   # TG-77
    async def rebind_channel(self, chat_id: int, agent_id: str, topic_id: int) -> int: ...
    """TG-82. Returns the new recreation count; the caller retires past MAX_RECREATIONS."""
```

#### 9.6.2 `pkb.server.telegram_api` — two calls and one parameter

```python
MAX_RECREATIONS: Final = 2      # TG-82 — durable, in the directory row
GENERAL: Final = 0              # TG-72 — not None; a key, an index component and a dict key

class BotApi(Protocol):         # additions only; §8.1's shipped seven are unchanged in shape
    async def get_me(self) -> Mapping[str, Any]: ...        # ALREADY SHIPPED — now read for
                                                            #   has_topics_enabled (TG-75)
    async def create_forum_topic(self, chat_id: int, name: str) -> Mapping[str, Any]: ...  # TG-78
    #   name and nothing else. No icon_color, no icon_custom_emoji_id — every parameter here has to
    #   be implemented by every fake, and neither has a rule behind it (§8.1's send_chat_action
    #   precedent).
    #
    # NOT ADDED, and deliberately: edit_forum_topic, close_forum_topic, reopen_forum_topic,
    # delete_forum_topic, unpin_all_forum_topic_messages, delete_message (TG-78, decision AD).

    # send_message / send_document gain `topic_id: int = GENERAL` and RETURN THE WHOLE Mapping,
    # which they already do (§8.1). TG-80 reads `message_thread_id` off that response.
    #
    # edit_message / clear_keyboard / answer_callback take NO topic (F-6, TG-90).
```

#### 9.6.3 `/health` — the telegram block

```jsonc
"telegram": {
  "enabled": true, "state": "running", "restarts": 0,
  "last_error": null, "last_send_error": null, "last_poll_ok_at": "…",
  "chats": 1,
  "invalid_chats": [],
  "topics_enabled": true,                 // NEW — getMe.has_topics_enabled (TG-75). `false` means
                                          //   the deployment runs exactly as it did before §9.
  "channels": 4,                          // NEW — directory size (TG-77)
  "retired_channels": ["topic/grilling"], // NEW — deleted past MAX_RECREATIONS (TG-82)
  "unmapped_agents": ["topic/woodworking"]// AMENDED — minus the mapping's values AND the directory's
}
```

`unmapped_agents` is still computed **in the endpoint** as `{catalog} - health.telegram.agents`
(`app.py:161`, unchanged). What changes is who fills `health.telegram.agents`: the daemon seeds it
from `store.channel_agents()` at composition time, and the adapter adds to it on every create. That
ordering is the whole of TG-11's stated property — the answer stays correct while the bot is
crash-looping, which is when `/health` gets read.

---

### 9.7 Test strategy delta

The three tiers of §6.1 are unchanged and the default suite still collects **zero** live tests and
opens no socket. What §9 adds:

| Fixture | What it must gain |
|---------|-------------------|
| `fake_bot` | **A topic model.** Created topics get ids from a monotonic counter starting at 1; `delete_topic(id)` marks one dead **without** making any call fail; a send to a dead id returns `ok: true` with the `message_thread_id` **stripped** from the echoed `Message` (F-2) — and a switch that makes it 400 with `message thread not found` instead (F-3), so TG-80 and TG-83 are both driven from one fixture. `has_topics_enabled` is settable and defaults to **`false`**, so every pre-topics test keeps running against the pre-topics behaviour by default (TG-75). |
| `store` | The additive migration is a **test**, not an assumption: build a store at the pre-§9 schema, populate a binding, a ledger row and a prompt, run `setup()`, and assert the rows survive with `topic_id == 0` and that no rebuild occurred. |
| new file `tests/server/test_telegram_topics.py` | Addressing (TG-72, TG-73, TG-74), creation (TG-76, TG-77, TG-78, TG-87), the hazard (TG-80…TG-84), attribution (TG-85), commands (TG-86, TG-88), mechanics (TG-90…TG-95). |
| `tests/server/test_telegram_migration.py` | **The whole pre-topics suite, re-run with `has_topics_enabled: false`, asserting no payload anywhere carries `message_thread_id`.** This is TG-75 and it is the single most valuable test in the section: the human may never flip the toggle. |

Two facts get `@pytest.mark.live` under §6.5's existing shape, deselected by default and skipped
without `PKB_TELEGRAM_TEST_TOKEN`:

| Fact | Why a fake cannot settle it |
|------|------------------------------|
| **A send into a deleted private-chat topic returns `ok: true` and lands in General** (F-2) | The foundation of TG-80. Our fake implements it; only the real API proves the fake is right, and if it is wrong the whole section's detection mechanism is wrong with it. |
| **`createForumTopic` succeeds in a private chat with Threaded Mode on** (F-1, F-4) | Today the real bot answers `400 the chat is not a forum` (executed). The first thing to re-run after the human flips the toggle. |

---

### 9.8 Explicitly out of §9

- **`pkb.tui` and `pkb.clients`.** Not touched, not read, not amended. The TUI already reaches every
  expert directly and that surface is out of scope by instruction.
- **Renaming, closing, reopening or deleting a topic from the bot** (TG-78), and `deleteMessage`
  anywhere (decision AD).
- **Topic icons, custom emoji, pinning, and `unpinAllForumTopicMessages`.** No rule needs them and
  every Protocol method is a method every fake must implement.
- **Per-topic authorization.** One allow-list, one boundary (TG-95).
- **Group forums.** TG-19 is unchanged and unweakened: private chats only. A topic is *inside* a
  private chat, which is why this feature is compatible with TG-19 rather than an exception to it.
- **`/talk` and any in-band agent selector** (decision AF).
- **Rebinding a channel to a listed thread id** — TG-40's struck claim. Deferred with its reason,
  not quietly dropped.
- **Enumerating a chat's existing topics.** No API does it (F-5); TG-87's bind-here form is the
  recovery path.
- **Cross-channel approval push** (Q19(b)) and live fan-out of one run to a second channel. Both
  still deferred, and topics do not change either argument.

---

### 9.9 Open questions — Q27 onward

Ranked by blast radius. **Every one has a default already encoded in §9.3**, so implementation is not
blocked on any of them.

| # | Question | Options | Recommended default | Blast radius if changed later |
|---|----------|---------|---------------------|-------------------------------|
| **Q27** | **Should `/channels all` exist?** It creates one topic per catalog agent in one command. | (a) yes; (b) list-and-name only, one agent per command. | **(a)** (TG-87). It is the "set it up once" path, and without it a human with a twelve-topic knowledge base types twelve commands with hand-copied agent ids on a phone. It is still decision AA's shape — one explicit human act — and the act is undoable one topic at a time. The risk is a human with forty topics who types it once and gets forty channels; the mitigation is that the reply says how many it will create **before** creating them only if we add a confirm step, which we have not, because TG-64's confirm affordance is for irreversible *writes* and a topic is deletable. | One command branch. If it goes, `/channels <agent-id>` is unchanged and TG-87 loses one clause. |
| **Q28** | **Is `MAX_RECREATIONS = 2` the right bound, and should retirement ever expire?** | (a) 2, permanent until a `/channels` command; (b) 2, expiring after N days; (c) 1; (d) unbounded with a rate limit. | **(a)** (TG-82). Two survives an accidental deletion plus a fat-fingered second one without becoming a fight with a human who is deliberately deleting the topic. (d) is the shape that turns a deletion into a notification loop. (b) adds a timer to a supervised task that carries no state across restarts (P-23) and would need its own durable column. **The uncertainty is honest**: nobody has yet deleted a topic in anger on this deployment, so the number is reasoned, not measured. | One constant and one column. Fully reversible; the durable count means changing the bound changes behaviour for existing dead channels immediately. |
| **Q29** | **When General is not the Librarian, should the bot say so in that channel once at startup, or only in `/agents`?** | (a) `/agents` only (quiet); (b) one notice per daemon start; (c) a notice on the first message after a start. | **(a)** (TG-73). A per-start notice on a daemon whose value proposition is staying up for weeks is either invisible or, under a restart loop, spam — and TG-13's whole lesson was that a notice fired by a restart is a notice the human learns to ignore. But (a) means a human who never types `/agents` never learns that their General talks to Cooking, and that is the one ambiguity topics leave standing. | One message. Genuinely a judgement call about noise, which is why it is here rather than ruled silently. |
| **Q30** | **Should `/channels <agent-id>` in an unbound topic bind that topic, or always create a new one?** | (a) bind here, create otherwise, and say which happened (TG-87); (b) always create, with a separate `/bind` for the other case. | **(a)**, and it is the least comfortable ruling in §9: one command with two behaviours is exactly the kind of context-dependence TG-1 was written against. It is chosen because the bind-here form is the **only** recovery path for a lost SQLite file — no API enumerates a chat's topics (F-5) — and a second command is a second thing to remember on a surface that already has six. The mitigation is the reply naming which happened, which makes the ambiguity visible after the fact rather than never. | One command branch and one line of help text. If (b) wins, `/bind` is additive and TG-87 splits in two. |
| **Q31** | **Should a `/pending` keyboard for an agent with no channel offer to create one?** | (a) no — post it in the typing channel with the TG-85 prefix (TG-88); (b) create the channel and post it there; (c) post a line suggesting `/channels <agent-id>`. | **(a)**, with **(c)** as a cheap improvement if the prefix reads badly in practice. (b) violates decision AA — creating a topic as a side effect of a *listing* command is the daemon deciding what is reachable from the phone, which is the one thing TG-3 has ruled against since the beginning. | One line of text. Nothing structural. |

### 9.10 As built — the five defects the suite found, and what changed in the rules

§9 was written before the code. Four agents built the four file sets in parallel and three wrote the
suites against them; between them the suites left **seven strict `xfail`s**, every one a real defect
rather than a misread rule. All seven are fixed and the markers are gone, so each is now an ordinary
regression test. They are recorded here because two of them are cases §9 did not state, and a rule
that only exists in a test is a rule the next change deletes by accident.

**None of these is a new rule.** Each is a rule that was written and then not implemented, or
implemented in a way that satisfied its wording and broke its purpose.

| # | Rule | The defect | What the rule now says |
|---|------|-----------|------------------------|
| 1 | **TG-25 / TG-82** | Retirement leaked across chats. `_load_directory` seeded the in-process retirement set from `store.retired_agents()`, which answers with bare agent ids and no chat, and paired every id in it with every mapped chat. An expert whose topic was deleted three times on the phone had its live, untouched channel on the laptop silently abandoned: replies moved to that chat's General with a prefix, nothing was deleted there, and no command the human can type says why. | The routing seed is read back **per chat**, through `channel(chat_id, agent_id)` — the only reader that knows both halves of the key. `retired_agents()` stays chat-less and stays right for `/health`: TG-11 asks *which experts* are in that state, not where. |
| 2 | **TG-82 / TG-84** | Reviving a retired channel made a row say it worked without making it work. `open_channel` clears `retired` and `recreations`, but `_route_out` consults the in-process set first, so `/channels <agent-id>` — the recovery TG-82's own notice instructs the human to perform — created a topic that received nothing for the life of the daemon. | Revival is one operation, `_revive`, called from both creation paths: it drops the in-process retirement **and** re-points the re-addressing chain at the new topic, so a frame still queued for the dead id follows the agent rather than arriving in General under a prefix (TG-84 — re-addressed, never dropped). |
| 3 | **TG-84** | A repair never read the directory's own `topic_id`, so a stale reference to an already-repaired channel created a **second** topic and abandoned the live one. Reachable by an ordinary restart: every durable row that names a topic — a prompt row (TG-57), a ledger row (TG-29/TG-31) — keeps naming the dead id after a repair, and `_moved` is process memory. The expert's history then splits across two live topics with the bot writing to the one the human is not in, and one of TG-82's two recreations is spent on a channel that never needed repairing. | `_repair` compares the channel it was handed against the directory row **before** creating anything. A row naming a different topic means the channel has already moved: re-address, create nothing. The row was already being read on this path, for `recreations`. |
| 4 | **TG-82, decision S** | A repair moved the channel and left the conversation behind. `rebind_channel` rewrites the directory row; nothing moved the **binding**, which is keyed on the now-dead `(chat_id, topic_id)`. The human's next message in the recreated topic opened a new thread while the old one still held the approval the bot had just re-sent them — decision S's amnesiac bot, produced by the repair itself, and the stranded thread is reachable from no channel on the phone (`/threads` is a read-only listing, TG-40 amended; `/cancel` reads the new topic's binding). | A recreation **carries the binding over**. Bind the new topic first, unbind the dead one second: a crash between the two leaves the thread reachable from both, which the next inbound message resolves, where the other order leaves it reachable from neither and there is no undo (D6). A repair is not a rotation — only `/new` is (TG-27). |
| 5 | **TG-82 / TG-29** | An unattributed send that strayed pinned its channel to General permanently, performing **zero** of TG-82's two permitted recreations and blocking the attributed sends that could have performed them. The trigger is the ordinary one: the orphan report and every other startup notice are sent with no agent id, into the channel that lost the message (TG-29), before anything else the bot does — so a topic deleted during an outage was discovered by the one message that could not repair it. | The agent is resolved from the directory when the caller does not know it, which is the same read the routing path already makes. The correction line then names the expert instead of "an expert" too. |
| 6 | **TG-85** | An approval that fell back to General was attributed **twice** — the agent id as line 1 and again as line 2 — because `_post_action` puts the attribution in the body and the send path independently prepends one on noticing the message had left its agent's channel. TG-85 and the code's own comment both say *"one line, moved, not repeated"*. Cosmetic, on the one message in the layer that carries an irreversible button and whose first lines are the only attribution a notification preview shows. | The two mechanisms are made exclusive. A caller that has already named its agent says so (`attributed`), and the generic prefix is suppressed; the caller decides *where* the line goes, because only it can put the attribution somewhere other than the very top. TG-85(b)'s "outside its agent's channel" is asked **before** the send, so the attribution leads for a retired channel exactly as it does for TG-88's fallback. |

Two smaller corrections, both in the same act:

* **`_retire` writes the re-addressing before the told-once guard.** A second dead topic for an
  already-retired agent still has to stop being sent to; the early return skipped that, so the next
  frame addressed to it strayed into General again — one more stray and one more disarm, forever.
* **TG-28's owned-name set includes the index.** ST-7's rule is about the *names* Layer 3 takes in a
  file it shares with the checkpointer, and `sqlite_master` holds an index as a row like any other.
  An unprefixed index name collides exactly as destructively as an unprefixed table name and is
  easier to add without noticing.

**Two spec sentences are looser than the build and are tightened here rather than left to be
rediscovered:**

* **TG-82's order of operations.** §9.6.1 says `rebind_channel` *"returns the new recreation count;
  the caller retires past `MAX_RECREATIONS`"*, which reads as create → rebind → retire — a flow that
  has already issued the third `createForumTopic` by the time it retires, contradicting TG-82's own
  *"no further `createForumTopic` is issued for it"*. **The authoritative check is the one before the
  create**: read `channel()["recreations"]`, and only create while it is below the bound. The
  returned count is the same fact read back, for the caller deciding whether this repair was the
  last one.
* **TG-90's acceptance text** says the fake "400s if a topic is passed" to an edit or answer call.
  The real client cannot: the parameter does not exist, so passing one is a `TypeError` at the call
  site, which is strictly better. **The fake must not accept it either** — a fake that simulates a
  400 accepts a shape the real client rejects, which is the exact drift TG-67's signature comparison
  exists to prevent.

**Two divergences from §9 that are the code being right**, recorded so nobody "fixes" them back:

* **TG-74's reply does not name the topic id.** A topic id is invisible in every Telegram client, so
  printing it is noise the human cannot act on; what makes the reply usable is the agent-id listing,
  which is there. The clause is struck from TG-74.
* **The shipped pre-topics suites pass `GENERAL` explicitly at every call site** rather than relying
  on a default, because there is no default (TG-72). Every one of those tests is about a chat with
  Threaded Mode off, which has exactly one channel, and saying so at the call site is the whole
  point of the rule: a call that omits the topic files a message under whichever binding happens to
  be General's, and that mis-file is invisible in a diff.

### 9.11 Audit — two defects the suite could not see, because every fake ran the adapter alone

An adversarial pass re-verified §9's guarantees by execution rather than by reading the tests, and
found the section's central mechanism working correctly under **one** send and failing under two.
Both defects are the same shape: `_moved` is the record that a channel is dead, and it is written
two `await`s after it is read.

Neither was visible to the shipped suite for a structural reason worth stating, because it will
recur. Every fake `BotApi` in this layer completes `sendMessage` with no `await` that yields, so two
overlapping sends can never actually overlap and the adapter is only ever exercised running alone.
That is not the deployment: `_pump_outbox` is its own child of the task group (TG-7) and a run emits
its reply and its `InterruptEvent` from another, so a send is in flight while a second one starts on
the same channel as a matter of course. A one-line `sleep` in the fake is what lets a test see it,
and the hazard file now has one.

**Defect A — the repair was a check-then-act, so one deletion created two topics (TG-82, TG-84).**
`_channel_died` read `channel in self._moved` and `_repair` wrote it after `store.channel(...)` and
`createForumTopic(...)`. Two frames that discovered the same deletion therefore both read `False`
and both created a topic. Measured, from one deletion:

* the directory named the **second** topic and `recreations` was already **2**, so the human's next
  deletion — their second, on a topic they had deleted once — retired the channel outright and sent
  that expert to General permanently. TG-82's bound was spent by the bot, not by the human.
* the **first** new topic stood on the phone carrying the expert's title, holding the reply that had
  been re-sent into it, present in the directory under nobody. Every message typed there is answered
  with TG-74's *"this topic is not connected to an expert"*: a channel that exists, is named after
  the expert, and can never be talked to.
* `_carry_binding` carried the conversation into the first and then found nothing left to carry into
  the one the directory named, so the human's next message in the topic the bot was actually using
  opened a **new** thread while the old one still held the approval they had just been shown. That
  is decision S's amnesiac bot, produced by the repair itself, and there is no undo (D6).
* two corrections were posted, against TG-84's own *"one correction, not eight"* — which held only
  for frames arriving after the first had **finished** repairing.

**Fixed** by serializing `_channel_died`'s repair section per channel (`_repairs`, a lock beside
`_locks`) and moving the `known` check **inside** it. The lock is deliberately not the TG-93 turn
lock: a repair runs inside a send, and a send happens on the outbox pump and on `_recover` as well
as inside a turn, so borrowing the turn lock would both miss those callers and deadlock the ones it
caught. **The disarm stays outside the lock** — it is per message, needs nothing but the response in
hand, and waiting for another frame's `createForumTopic` before killing a live Approve button is the
"repair first" ordering TG-81 forbids. TG-82 and TG-84 are amended in place.

**Defect B — `_plain` numbered every part `(position, 1)`, which is the empty string (TG-45).**
`_plain` bypasses `_send`, and with it the one place the part counter is applied correctly, so a
correction long enough to split would arrive as two unnumbered messages — with the UTF-16 units
`split_message` had already reserved for a label that never appeared. Latent rather than observed:
both texts it sends are short constants today. Fixed rather than argued away, because the only thing
keeping it latent is the length of a sentence somebody may lengthen, and the message in question is
the one explaining why an approval's buttons just died.

**Verified sound by execution in the same pass**, and recorded so the coverage is known rather than
assumed: the pre-topics migration (a database written by the shipped schema keeps its binding, its
ledger, its approval and its offset, and a rotation is not resurrected by the next restart);
at-most-once dispatch across a redelivery; the owner allow-list running ahead of the mapping on
messages, with silence for a stranger's message and an **alert** for a stranger's button press;
`allowed_updates` on every poll including the cold-start drain; the UTF-16 budget with the part
counter and the TG-85 prefix both included, over emoji, unsplittable lines and 100 kB inputs;
`answer_callback` before `resume` on every path; keyboards cleared with `editMessageReplyMarkup`
only — `edit_message` is reachable from no call site, so no description is ever overwritten; the
token absent from every `repr`, log record and `/health` field including the three §9 added; one
live poller across three supervised restarts; and `git diff` empty under `src/pkb/tui/` and
`src/pkb/clients/`, which the human's instruction required.

---

### 9.12 Audit — the outbound path under two sends at once, and after the process that repaired it

A second adversarial pass, run against the built adapter rather than against the rules, over the one
question §9 exists to answer: *can an approve button for an irreversible write end up in General
under an expert it does not belong to?* Four defects, all fixed, each with a regression test in
`tests/server/test_telegram_topic_hazards_audit.py` whose name ends in the rule it implements. None
is a new rule; each is a rule that was written and then not implemented on one path.

| # | Rule | The defect | What the rule now says |
|---|------|-----------|------------------------|
| 1 | **TG-81 over TG-84** | **`_channel_died`'s dedup branch skipped the disarm.** It returned early when the channel was already in `_moved` — correct for TG-84's *"one correction, not eight"*, and catastrophic for the keyboard, because a **stray is a message and a repair is a channel**. Executed: two overlapping sends into one dead topic leave a **live Approve button in General under Cooking's name**, the exact failure §9 is arranged around, reached through the one branch written to keep corrections from multiplying. The interleaving is the ordinary one, not an exotic one: the outbox pump is its own task (TG-49) and a run emits its reply from it while `_consume` posts the approval from another, so a keyboard's send is routinely in flight when the pump's next frame discovers the same topic is gone. Note that `_route_out` makes this branch **unreachable except** concurrently — it always returns the end of the `_moved` chain — so the guard's stated job was already done by the caller and the only case it ever fired on was the one it broke. | The disarm runs **before** the guard and unconditionally: TG-81 step (1) is per message. The correction runs for an **armed** stray even on an already-known-dead channel — a message whose buttons were just killed, sitting in General with nothing under it saying why, is a human pressing a dead Approve and learning nothing — and is suppressed for a plain one, which is the fan-out case TG-84 named. Only the repair stays once per channel. |
| 2 | **TG-64 / TG-82 / TG-85** | **The confirm step was sent with no `agent_id`.** `_ask_confirm` is the message that carries *"Yes, do it"* over a delete, and it was the one part of an approval invisible to both mechanisms §9 built for a channel that is no longer its agent's: `_route_out`'s retirement check and `_prefixed`'s exposure line. On a retired channel — or a topic-less deployment — the human received a bare *"There is no undo for this. Confirm?"* with a live button in General, naming no expert and no write, beside the Librarian's messages. | `_ask_confirm` takes the **live** request's `agent_id` (`detail.pending.agent_id`, never the row's) and passes it to `_send`, so the confirm step is routed and attributed exactly like the button message above it. TG-85(c) is unchanged: inside its own channel it still carries no prefix. |
| 3 | **TG-31 / TG-85 / TG-82** | **The re-sync path attributed nothing, ever.** `_resync` read `getattr(detail, "agent_id", "")` — and `ThreadDetail` has no such field, it carries the `Thread`. So the expression was a constant `None` for every thread that has ever existed, and every frame TG-31 re-synced after a restart went out unattributed: unrouted by TG-82's retirement, unprefixed by TG-85(b) in General, and unable to repair the channel it died in (TG-84) — on the one code path whose whole premise is that the topic may have been deleted while the daemon was down. | One helper, `_agent_of(detail)`, reading `detail.thread.agent_id`, used by branch (b) and by the late reply of branch (c). |
| 4 | **TG-84** | **A repair could not survive the process that made it for an unattributed send.** `_moved` is process memory and a prompt row keeps naming the topic it was posted in (TG-57), so after a bounce a press's outcome is addressed to a topic that was repaired before the restart. TG-84's amended re-addressing — *"a row naming a different topic means the channel already moved, so re-address and create nothing"* — reads the directory **by agent**, so a send that names no expert cannot use it: `_channel_died` fell back to `_directory(chat)[topic]`, which no longer contains a repaired topic id, and `_repair(None)` then pinned the channel to **General for the life of the daemon** — that channel's every later message with it. The line it lost is the record that an irreversible write just happened. | `_resolve` names the agent on the outcome line (`request.agent_id`), so the record of a decision follows its expert to the live topic. **This is a caller-side fix and it is not complete**: `_note_stale`, `_CANNOT_ANSWER`, `_PRESS_FAILED` and `_announce`'s orphan report still send unattributed, and after a restart they still land in General rather than in the repaired topic. That is survivable — General is where the human can find them and the correction says so — and the general form needs a durable dead-topic → agent record, which is Q32. |

**Q32 — should a repaired topic id stay resolvable?** Today the directory row's `topic_id` is
overwritten by `rebind_channel`, so nothing anywhere can answer *"which agent used to be at topic
71?"*, and the only reason TG-84's re-addressing works after a restart is that the caller happens to
know the agent. Options: (a) leave it, and name the agent at the remaining call sites one at a time
as they matter; (b) keep a `dead_topics(chat_id, topic_id) → agent_id` row per recreation, which
makes the re-addressing total and costs one table; (c) put `agent_id` on the prompt and ledger rows,
which is narrower than (b) and covers the two durable rows that actually name a topic. **Default
encoded: (a)**, on the grounds that the fixed call site is the only one carrying an irreversible
act's record and the rest are status lines. (c) is the cheapest complete answer if a second one bites.

**Verified sound by construction in the same pass**, recorded so the coverage is known: detection is
on the **first** send after a deletion, per part and per document, and comes from the response rather
than from an exception — `_api_send` and `_send_document` are the only two call sites that can put a
`message_thread_id` on the wire and both compare `landed_topic_id` against what they asked for;
`clear_keyboard`, `edit_message` and `answer_callback` take no topic on the Protocol, on the client
and on the fake, so TG-63's disarm cannot 400 (TG-90); a split reply that half-lands leaves its stray
part standing **with a correction under it** and is re-sent **whole** into the repaired topic, so no
fragment is ever the only copy; the recreate-and-re-send loop is bounded by construction at
`_SEND_ATTEMPTS = MAX_RECREATIONS + 2` and terminates on a General send, which is never checked, with
the bound read from the durable row *before* the create; a `callback_query` resolves its thread from
the durable row's `thread_id` and its channel from the row's `topic_id`, never from where the human
was standing, so a press on a message Telegram relocated into General still answers the right
interrupt; and a run whose topic dies mid-flight ends where the human can find it — the binding is
carried to the new topic (`_carry_binding`), the queued frames are re-addressed rather than dropped,
and past the bound the whole channel lands in General with the agent id on the first line.

---

### 9.13 As built — the shipped surface, and where it diverges from §9

**Status: built.** The code is `pkb.server.telegram`, `pkb.server.telegram_api`,
`pkb.service.telegram`, the `/health` telegram block and the `pkb.daemon` wiring; the topics suite is
`tests/server/test_telegram_topics.py`, `test_telegram_topic_hazards.py`,
`test_telegram_topic_hazards_audit.py` and `tests/service/test_telegram_topic_store.py`, beside the
six pre-topics files §8 names, which all still pass. **`git diff` and `git status` are empty under
`src/pkb/tui/` and `src/pkb/clients/`**, which the instruction that opened §9 required.

§9.10 records the defects the suite found, §9.11 and §9.12 the two adversarial passes. This section
records the **surface**: where the shipped shape differs from §9.6 and §9.7, what was examined and
deliberately left alone, and what §9 asked for that does not exist. Everything here is a decision
with a reason. Nothing here is an outstanding defect.

#### 9.13.1 The store: the bindings could not be migrated in place

§9.6.1 says **additive only** — `ADD COLUMN` guarded by `PRAGMA table_info`, plus a unique index,
never a table rebuild — and gives the measured reason (ST-3: a transaction held across an `await`
killed a concurrent checkpointer run, and a rebuild at startup is exactly that). The ledger and the
prompts are migrated exactly that way. **The bindings could not be**, and this is the sharpest
divergence in the section:

| | As built |
|---|---|
| The obstacle | The shipped table declares `chat_id INTEGER PRIMARY KEY`. In SQLite that is a **rowid alias**: it permits exactly one row per chat, forever, and no column added beside it changes that. An upgraded deployment would share one binding row across every topic in the chat — the human sees a topic per expert and gets one rotating conversation behind them, which is TG-1's mis-file with a screen that actively denies it. |
| What ships | A **new** table, `pkb_telegram_channel_bindings`, keyed `(chat_id, topic_id)`. The shipped rows are carried over into it as **General** bindings by one `INSERT … SELECT` of at most one row per chat — so the conversation the human was having continues in General, which is what TG-28's amendment promised. |
| The old table | `pkb_telegram_bindings` is **left standing with every row intact** and never written again. It is the only surviving record of what the deployment looked like before the upgrade and this system has no undo (D6). Two records of one fact can disagree, so exactly one of them is live. |
| Exactly once | A `migrated_at` stamp on the legacy row, not `INSERT OR IGNORE` alone. Without it a human who upgrades, types `/new` to rotate their General thread and then restarts has the **pre-upgrade** thread resurrected under them — `unbind` deleted the row the insert would have conflicted with, so the ignore sees nothing to ignore. A crash between the insert and the stamp re-runs both, which is harmless: nothing has polled yet. |
| Why this is still ST-3-safe | Three short autocommitted statements at startup, none of them a rebuild of a table anything else is reading. The forbidden operation was a `PRAGMA`-driven table rewrite on the checkpointer's own connection; creating a new empty table and copying at most one row per chat into it is not that. |
| Verified | Executed against a real SQLite file written by the schema at the pre-topics commit: binding, ledger (claimed-never-started and started-never-finished), prompt and offset all survive; `setup()` is idempotent; and a `/new` rotation followed by a restart leaves the binding **absent** rather than resurrected. |

Two smaller store divergences, both additions §9.6.1 did not name:

* **`channel(chat_id, agent_id)` and `retired_agents()`** are on the Protocol beside the four §9.6.1
  lists. `channel` is what makes TG-82's bound *durable* and TG-84's "compare before you create"
  possible — both read the row before the create, which is what makes "no further
  `createForumTopic` is issued" true rather than approximately true. `retired_agents()` is chat-less
  on purpose and feeds `/health` only: TG-11 asks *which experts* are retired, not where (§9.10
  defect 1 is what happens when the routing path reads it instead).
* **`pkb_telegram_channels` carries a `retired` column** §9.6.1 did not list. Retirement has to
  survive a restart for the same reason the recreation count does: a flag a bounce clears is a
  notice the human gets again every time the daemon restarts, which is TG-13's exact lesson.
* **`topic_id` is declared last** on the two migrated tables. `ADD COLUMN` appends, so an upgraded
  file gets it last; declaring it elsewhere in `_SCHEMA` would give a fresh file a different column
  order from an upgraded one and make `SELECT *` mean two things depending on install date.

#### 9.13.2 The transport and the adapter

| §9.6.2 / §9.3 | As built | Why |
|---|---|---|
| "TG-80 reads `message_thread_id` off that response" | A named function, `landed_topic_id(message) -> int`, exported from `pkb.server.telegram_api` | It maps **absence to `GENERAL`**, which is the whole subtlety: the naive `response.get("message_thread_id") != sent` is right for a stray by accident and wrong for every General send. Written once, next to the Protocol that documents the hazard, rather than inline at two call sites. |
| `create_forum_topic(chat_id, name)` | Unchanged, plus `TOPIC_NAME_LIMIT = 128`, applied in the client as `name[:TOPIC_NAME_LIMIT]` | Telegram's own cap on a topic name. A catalog title longer than it would 400 the one call the human explicitly asked for, and a truncated title on a topic is legible while a refusal is not. Truncation happens in the client rather than in the adapter, for the same reason `_address` lives there: it is a fact about the wire. |
| — | `_SEND_ATTEMPTS = MAX_RECREATIONS + 2` | The re-addressing loop's bound, derived rather than chosen, so it cannot drift from the recreation bound. §9.13.4 records the one place that derivation is no longer load-bearing. |
| — | `_repairs`, a per-channel repair lock, and `_agent_of(detail)` | §9.11 and §9.12. Named here because they are surface, not just fixes. |
| TG-87's bind-here form | `_bootstrap` admits exactly **`/channels` and `/agents`** in an unbound topic, ahead of routing | Routing's answer for an unbound topic *is* TG-74's offer, so the two commands that make a topic stop being unbound have to be reachable before it. Everything else falls through to the offer: a `/new` in a topic that addresses nobody has nothing to rotate. TG-20 has already run, so this admits no one new (TG-95). |

#### 9.13.3 The tests

§9.7 names two new files. Four ship, and TG-75's migration guarantee is not where §9.7 put it:

* **`tests/server/test_telegram_migration.py` does not exist.** Its job — "the whole pre-topics
  suite, re-run with `has_topics_enabled: false`, asserting no payload anywhere carries a
  `message_thread_id`" — is done instead by the fake defaulting `has_topics_enabled` to **`false`**,
  so `tests/server/test_telegram.py` *is* the pre-topics suite running against the pre-topics
  behaviour, plus a TG-75 section in `test_telegram_topics.py` that asserts the payload property
  directly. A second file that re-imported and re-ran the first would have two fixtures to keep in
  step and would fail in a way that named neither.
* **The pre-topics suites pass `GENERAL` explicitly at every call site**, because there is no default
  (TG-72) — recorded in §9.10 and repeated here because it is the largest single diff in the build.
* **The store's topic tests live in `tests/service/test_telegram_topic_store.py`**, beside
  `test_telegram_store.py` rather than inside it, because the migration is driven from a file written
  by the *previous* schema and that fixture has nothing to do with the rest.
* **No `@pytest.mark.live` test exists**, here or in §6.5. The default suite collects zero and opens
  no socket, which is what both sections promised; what neither delivered is the live half. For §9
  that is forced rather than chosen: F-4 — Threaded Mode is off on this deployment's bot, so
  `createForumTopic` answers `400 the chat is not a forum` and the two facts §9.7 wanted pinned
  cannot be pinned yet. **They are the first thing to write after the toggle is flipped**, and until
  then the fake's fidelity on F-2 is an assumption the whole section rests on.

#### 9.13.4 Two more defects, found in the final pass

| # | Rule | The defect | What ships |
|---|------|-----------|------------|
| 1 | **TG-56 / TG-55** | `_send_document` returned `None` whether the description landed or not, so `_post_action` believed it had and attached an irreversible Approve button to a 1,200-character preview ending *"… (full text above)"* when there was no full text above. TG-56 was implemented against the upload **raising**; topics added a second way to lose it — the document chases a dying channel through `_SEND_ATTEMPTS` re-addressings and can run out of them without ever raising. Not reachable at today's constants, and the argument for that is arithmetic in another module (`_SEND_ATTEMPTS == MAX_RECREATIONS + 2`, terminating on a General send that is never checked). | `_send_document` returns `bool`; a `False` takes TG-55's hand-off exactly as a `TelegramError` does, and says so in the log. The test asserts the **seam**, not the arithmetic, because the arithmetic is what a later change to either constant breaks silently. |
| 2 | **TG-92 / TG-36** | `_is_empty` decided "human or Telegram?" from a **list** of media keys, so an attachment kind that is not on it (`paid_media`, `invoice`, `checklist`, and whatever Telegram adds next) was classified as a service message and answered with **silence** — TG-36's *"I have not downloaded or stored the attachment"* skipped, and the human's caption lost with it. Silence is the one answer that reads as a bot that is down rather than as a bot that does not take files. | A non-empty `caption` means a human typed it, checked before the list. Telegram's own bookkeeping carries no caption, and `_SERVICE_KEYS` is matched ahead of this function either way, so nothing of Telegram's is admitted by it. The list stops being load-bearing without anyone having to maintain it. |

#### 9.13.5 Examined and deliberately not changed

* **`_send_document` does not go through `with_retry`.** Every other send does (TG-8). A 429 or a
  transport blip on the upload therefore degrades straight to TG-55's hand-off instead of retrying.
  That is worse availability and it is the safe direction: the failure of an upload is an approval
  the human is told to answer in the TUI, while a retry of a large multipart upload whose response
  was lost posts the whole description twice under one keyboard. Recorded rather than changed
  because no rule asks for it and the degradation is the one TG-56 already chose.
* **`_on_callback` performs no `chat.type == "private"` check**; only the message path does
  (`_sender_ok`, TG-19). Identical at the pre-topics commit, so it is not a topics regression, and it
  is unreachable rather than merely unexploited: a `callback_query` can only come from a message the
  bot **sent**, the bot only sends into chats named by the mapping, and `load_telegram_config`
  refuses a non-positive chat id at startup. TG-20's allow-list gates the press regardless (TG-95).
  Adding the check would mean refusing a press whose `message` Telegram marked inaccessible, which is
  a live approval nobody can answer from the phone — a worse failure than the one it prevents.
* **`/channels all` matches the literal `all` before the catalog lookup**, so an agent whose id was
  `all` would be unaddressable. Agent ids are `librarian` or `topic/<slug>` (GE-25), so the string
  cannot occur (RG-11: every id is minted by `pkb.core.agent_id_for`, and the `topic/` prefix is
  what makes even a folder named *Librarian* unambiguous); a topic called *All* is `topic/all`.
  Stated so the next reader does not have to re-derive it.
* **`health.telegram.agents` and `retired_channels` are unions that never shrink** inside one
  process, and `_revive` re-points only the **last** dead channel of a retired agent. Both were
  flagged by the build and by both audits; neither is settled by a rule. The consequence is a
  `/health` that over-reports until the next restart, on fields whose stated job (TG-11) is to say
  what is *reachable* — and over-reporting reachability is the direction that does not hide a topic
  from the human.

#### 9.13.6 What §9 asked for and is not built

* The two `@pytest.mark.live` facts of §9.7 — blocked on the BotFather toggle (9.13.3).
* Everything in §9.8, unchanged: no renaming, closing, reopening or deleting a topic; no
  `deleteMessage`; no topic icons; no per-topic authorization; no `/talk`; no rebind of a channel to
  a listed thread id (TG-40's struck claim); no enumeration of a chat's existing topics, because no
  API does it; and no cross-channel approval push.
* **Q32 is open** (§9.12): `_note_stale`, `_CANNOT_ANSWER`, `_PRESS_FAILED` and `_announce`'s orphan
  report still send unattributed, so after a restart they land in General rather than in a topic
  repaired before the bounce. Default (a) is encoded — General is findable and the correction says
  why — and (c), `agent_id` on the prompt and ledger rows, is the cheapest complete answer if a
  second one bites.
