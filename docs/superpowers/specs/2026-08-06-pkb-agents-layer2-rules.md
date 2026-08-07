# PKB Agents (Layer 2) — Requirements and Rules

**Date**: 2026-08-06
**Status**: Implementing. Every open question below has a default applied; none blocks the build.
**Scope**: `pkb.agents` plus the seam module `pkb.contracts` — build-order step 2 of the
[architecture design](2026-08-06-pkb-architecture-design.md), built on the merged
[Layer 1 rules](2026-08-06-pkb-core-layer1-rules.md).

---

## Read this first

This document was produced the same way as the Layer 1 rules — five lenses over `README.md`, the
architecture spec, and Layer 1's surface — plus one thing the Layer 1 pass did not need: a **grounding
pass that executed every harness assumption against the installed packages** (deepagents 0.7.5,
langchain 1.3.14, langgraph 1.2.10). The full evidence is in
[`docs/reference/deepagents-0.7.5-harness-grounding.md`](../../reference/deepagents-0.7.5-harness-grounding.md).

That pass changed the design. §2 lists twenty places where the approved architecture describes a
harness that does not exist as described. Four of them are load-bearing enough to state up front,
because each would have failed silently — the tests would have passed and the guarantee would have
been fiction:

| | What the architecture assumes | What the harness does |
|---|---|---|
| **D-2** | Validation "sits below the agent's decision-making" and cannot be skipped (arch §7). | deepagents auto-adds a `general-purpose` subagent to **every** deep agent, and it inherits only middleware whose name collides with a built-in slot. Ours never collide, so an agent that delegates a write to it bypasses validation and the flush entirely. It *does* inherit permissions, so I3 still holds — which is exactly why the hole is quiet. |
| **D-1** | "The flush runs on both success and failure" (arch §7). | `after_agent` is a node on the normal exit edge. Any exception aborts the superstep and it never runs — verified across four failure shapes, each leaving a written file and no flush. |
| **D-11** | Breadth files are supplied via `memory=[topic.md, notes/summary.md]` (arch §4). | `MemoryMiddleware` injects a prompt instructing the model to call `edit_file` on those files "to persist new knowledge" — the two files README §1.6 says the AI never finalizes on its own. It also caches them in checkpointed state, so a long thread never sees the human's edit. |
| **D-6** | "A thread is `(agent_id, thread_id)`", and delegated work runs in its own thread (arch §4). | The checkpointer keys on `thread_id` alone; `checkpoint_ns` is not usable as a second dimension. Delegated work checkpoints under the *parent's* thread in a nested namespace. The consequences arch draws are still right; the mechanism is not, and Layer 3 would have built a table against it. |

The corrected approach for each is in §2 and carried into the rule table. Where a lens and the
executed grounding disagree, **the grounding wins**.

### Decisions applied on top of the mined recommendations

| # | Decision | Why |
|---|----------|-----|
| A | Every Q1–Q13 default in §4 is applied as recommended. | Each is one constant, one table row, or one module away from its alternative. The five worth a human's eye are called out in the summary rather than blocking the build: Q1 (shipped skills are mounted, not seeded), Q2 (`expert.md` layers under a fixed standards preamble), Q3 (an expert writes only inside its own topic subtree), Q5 (the approval gate table), Q8 (`delete` is gated, not removed). |
| B | The seam is a leaf module, `pkb/contracts.py`, with nothing below it. | I2 says transports never import the harness. `pkb.agents.__init__` imports the runtime, so any type re-exported from there drags `deepagents` into `pkb.server`. A leaf module makes the invariant structural instead of a discipline, and an import-linter `forbidden` contract now enforces it — it was unenforced before (D-20). |
| C | Layer 2 adds a third middleware the architecture does not mention: `KbBreadthMiddleware`. | It replaces `memory=` (D-11), reading `topic.md` and `notes/summary.md` fresh on each model call. Arch §4's *intent* — breadth always in context, depth on demand — is preserved exactly; only the mechanism changes. |
| D | The flush guarantee is enforced in `pkb.agents.runtime`, not only in `after_agent`. | D-1 means the middleware alone cannot deliver it. The runtime wraps every execution in `try/finally` and flushes from checkpointed touched-path state when `after_agent` did not fire, so there is exactly one flush per run on both paths. |
| E | `voice` ships an opinionated **starter profile**, and the skill states that a topic may hold its own. | **Ruled by the human, 2026-08-07**, overriding this document's original C8 recommendation of a bootstrapping procedure with no profile. Without a shipped profile the drafts still have a voice — the model's own, chosen by nobody. A wrong default is visible in the first draft and gets corrected; an absent one is invisible and does not. The per-topic overload was already mechanical (`resolve_skills`, SK-16); what was missing was the skill saying so, and saying that a topic copy *replaces* rather than merges. |

### Amendments the architecture doc needs

Recorded here rather than edited into an approved document: §7's "runs on both success and failure"
needs the runtime clause (D-1); §4's `memory=` line should name the breadth middleware (D-11); §4's
thread sentence should say delegated work runs in a nested `checkpoint_ns` under the parent's thread
(D-6/C9); §5's `astream_events(version="v3")` is a different protocol on this pin and should read
`astream(..., stream_mode=["updates","messages"], subgraphs=True)` (D-12); §8's 409 is Layer 2's own
active-run registry, not a harness feature (D-15).

---

## 0. Conventions

`README.md` says *what* the PKB is; the architecture spec says *how the system is built*; the Layer 1
rules document is the mechanical contract for the tree. **This document is the contract for the agent
layer**: every rule Layer 2 must obey, with a stable id (`RT-3`, `MW-12`, `SK-7`, …) that the
implementing test cites.

It was produced by mining `README.md`, the architecture spec, the Layer 1 rules, and the deepagents
recon through five independent lenses (topology, middleware, skills, runtime, behaviour), plus a
sixth pass that **executed every harness assumption against the installed packages**. Where a lens
and the executed grounding disagree, the grounding wins and the divergence is recorded in §2.

**Rule ids are stable.** A test that changes must cite the rule that changed.

### 0.1 Severity convention

Same as Layer 1. For *validation-shaped* rules, severity is the severity of the emitted finding. For
*structural / behavioural* rules, `error` = must hold or the layer is wrong; `warning` = should hold,
deviation needs a written reason; `info` = advisory or classification only.

### 0.2 Hard constraints inherited

- **I1** — `pkb.core` imports neither `pkb.agents` nor `deepagents`. Layer 2 may import both.
- **I2** — transports never import `deepagents`/`langgraph`/`langchain`. This forces §5's seam module.
- **I3** — agents are blocked from writing derived files **at the harness level**, never by prompt.
- **Everything mechanical already exists in Layer 1.** Layer 2 cites Layer 1's rules; it never
  restates them, and it never contains a second implementation of validation, generation,
  scaffolding, slugification, id parsing, expert resolution, or tree walking.

### 0.3 Package layout (extends arch §3)

```
pkb/
├── contracts.py                 # THE SEAM — no harness imports, nothing below it (§5)
├── core/                        # Layer 1, built
└── agents/                      # Layer 2
    ├── runtime.py               # PkbRuntime: checkpointer, store, backend, locks, run/resume/cancel
    ├── paths.py                 # KB_MOUNT, to_backend_path, to_kb_relative      (the only "/kb/" in the repo)
    ├── permissions.py           # DERIVED_DENY_GLOBS, kb_permissions(topic_path=None)
    ├── gates.py                 # requires_approval(), describe_write(), interrupt_on builders
    ├── approval.py              # normalize_interrupt(), to_resume_command()
    ├── events.py                # graph stream chunk → AgentEvent
    ├── registry.py              # AgentRegistry: catalog, lazy graphs, invalidation
    ├── expert.py                # build_expert()
    ├── librarian.py             # build_librarian()
    ├── scans.py                 # ScanQueue protocol + SqliteScanQueue + run_scan()
    ├── skills.py                # packaged_skills_root(), adopt_skill(), check_skill_dir()
    ├── tools/topics.py          # create_topic / create_subtopic (gated)
    ├── middleware/
    │   ├── state.py             # KbAgentState + reducers
    │   ├── validation.py        # KbValidationMiddleware
    │   ├── maintenance.py       # KbMaintenanceMiddleware
    │   └── breadth.py           # KbBreadthMiddleware  (replaces arch §4's `memory=` — see D-11)
    ├── prompts/                 # package data: standards.md, expert_template.md, librarian.md
    └── skills/<name>/SKILL.md   # package data: the eight shipped skills (§6)
```

`packs.py` is a deferred member of the same package — see the `PK` group and §8.

---

## 1. Rule table

### 1.1 `runtime.py` and its neighbours — RT

Covers `runtime.py`, `paths.py`, `permissions.py`, `gates.py`, `approval.py`, `events.py`, `scans.py`:
everything that is neither a graph factory nor a middleware.

#### RT-A · Lifecycle and shared singletons

| ID | Rule | Source | Sev | Test assertion (API key?) |
|----|------|--------|-----|---------------------------|
| RT-1 | `PkbRuntime` owns exactly one `AsyncSqliteSaver`, one store, one backend, one KB write lock and one active-run registry, and hands them to every compiled graph — Librarian, every expert at every depth, and every internal scan run. No graph constructs its own. | arch §4 L166-167; D3 L30 | error | Two experts + the Librarian: `g.checkpointer is rt.checkpointer` for all three. **no key** |
| RT-2 | `AsyncSqliteSaver.from_conn_string(path)` is an async context manager that **closes** the connection on exit, and `__init__` calls `asyncio.get_running_loop()`. `PkbRuntime` is therefore `async with PkbRuntime.open(kb_root, db_path) as rt:` / `aclose()` — never a module-level singleton built at import time. | verified `langgraph/checkpoint/sqlite/aio.py:115-129` | error | `async with PkbRuntime.open(...)` creates `checkpoints`/`writes`; after exit a checkpointer call raises. **no key** |
| RT-3 | Synchronous checkpointer calls from the saver's own loop raise `asyncio.InvalidStateError`. Consequence: **Layer 2 exposes no sync run API** — `ainvoke`/`astream` only — while every middleware still implements *both* hook variants (MW-2), because the non-live tests drive graphs with `invoke()` against an `InMemorySaver`. | verified `aio.py:162-175` | error | `pkb.agents` exports no sync `run`/`stream`; a sync call against the runtime's saver raises. **no key** |
| RT-4 | The checkpointer file is opened WAL (verified `journal_mode == 'wal'`), so Layer 3's `threads` table and the scan queue may live in the same file. `PkbRuntime` exposes `db_path`, **never** the saver's `aiosqlite` connection; other tables use their own connection. | arch §5 L228-229, §7 L354-355 | warning | A second sqlite3 connection creates `threads` and reads `checkpoints` during a live run. **no key** |
| RT-5 | One store, created by the runtime, passed to every graph. Nothing in v1 reads it (there is no `StoreBackend`), so it is a forward-compatibility placeholder: use `AsyncSqliteStore` over the same file rather than `InMemoryStore`, so a restart is not silently lossy. | arch §4 L166 | info | `rt.store` is not None and is the identical object on Librarian and every expert. **no key** |
| RT-6 | One `CompositeBackend(default=StateBackend(), routes={"/skills/": FilesystemBackend(packaged_skills_root(), virtual_mode=True), "/kb/": FilesystemBackend(kb_root, virtual_mode=True)})`, shared by every graph. `StateBackend` gives each thread its own scratch filesystem; `/kb/` is the single on-disk tree shared by all agents; `/skills/` is the shipped-skill mount (SK-3). | arch §4 L168-173; recon §3; grounding §4/§6 | error | Two threads each write `/scratch.md` and read back only their own; both read the same `/kb/Cooking/topic.md`; `read('/skills/voice/SKILL.md')` returns packaged text. **no key** |
| RT-7 | The runtime runs **one `pkb.core.regenerate_all(kb_root)` at startup**, before serving. `after_agent` does not run when a run raises (D-1) and does not run while a thread sits at an unresolved interrupt (D-14), so derived files can be stale across a restart. Regeneration is idempotent and byte-deterministic (GE-4/GE-5), so on a clean tree this writes zero files. | arch §10 L406-408; grounding §3; verified interrupt case | warning | `PkbRuntime.open()` over a tree with stale derived files rewrites them; over a clean tree writes zero. **no key** |

#### RT-B · Paths and permissions

| ID | Rule | Source | Sev | Test assertion (API key?) |
|----|------|--------|-----|---------------------------|
| RT-8 | `/kb/` is spelled in exactly one module. Layer 1 forbids the literal (CX-3) and speaks kb-root-relative POSIX strings; `pkb/agents/paths.py` owns `KB_MOUNT`, `to_backend_path(rel) -> "/kb/<rel>"`, and `to_kb_relative(raw) -> str \| None`. Every middleware, permission builder, gate and tool uses that pair. | layer1 CX-3, §5 L548; arch §4 L166-173 | error | `grep -rn '"/kb' src/pkb` matches only `pkb/agents/paths.py`; round-trip property over 100 generated paths. **no key** |
| RT-9 | `to_kb_relative` **normalizes first**, via `deepagents.backends.utils.validate_path`. The `file_path` a middleware sees is the raw model string; deepagents normalizes only inside the tool body. `kb/Cooking/notes/b.md` (no leading slash) reaches the KB on disk while a naive `raw.startswith("/kb/")` test says False. | grounding §1 (live-verified bypass) | error | `to_kb_relative` maps `kb/x.md`, `/kb//x.md`, `/kb/./x.md` and `/kb/x.md` all to `x.md`; `/scratch/x.md` → `None`. **no key** |
| RT-10 | When `validate_path` raises (`..`, `~`, a drive prefix → `ValueError`/`NotImplementedError`), Layer 2 neither raises nor swallows: it forwards to `handler(request)` so deepagents produces its own `Error: {e}` ToolMessage. Layer 2 never re-words a harness error. | verified `backends/utils.py:648-710` | error | `write_file("/kb/../etc/passwd")` yields exactly one error ToolMessage, deepagents' text, and `validate_content` is never called. **no key** |
| RT-11 | The I3 deny list is **derived from `pkb.core.is_derived_name`, not restated**. Two globs suffice and are proven equivalent over a corpus: `["/kb/**/index.md", "/kb/**/tags.md"]`. wcmatch `GLOBSTAR` matches zero directories, so `/kb/**/index.md` already covers `/kb/index.md` — arch I3's three-glob list is one glob too many and one glob short. | arch I3; layer1 §7.4, PA-11; recon §4; grounding §5 | error | For every path in a fixture-KB walk, `is_derived_name(kb, p)` ⟹ `_check_fs_permission(rules,"write",to_backend_path(p)) == "deny"`. **no key** |
| RT-12 | The second glob is **deliberately wider than `is_derived_name`**: it also denies `/kb/<topic>/tags.md`, which Layer 1 excludes from the derived set (C14, PA-11) but rejects after the fact (VA-27). Live-verified that without it a `Cooking/tags.md` is written and lands on disk, maintained by no generator. The equivalence test in RT-11 is therefore "deny ⊇ derived", plus one asserted extra rule. | layer1 §7.4 L680-681, C14, VA-27; grounding §5 | error | `write_file("/kb/Cooking/tags.md")` returns `status="error"` and the file does not exist. **no key** |
| RT-13 | `operations=["write"]` only. Derived files must stay **readable**: the Librarian routes off root `index.md` and every expert reads its own topic `index.md`. Bulk read tools filter denied entries out of results rather than erroring, so a read-deny would silently hide the routing view. | arch I3; recon §4; README §2.2 L497-501 | error | `read_file("/kb/index.md")` succeeds while `write_file("/kb/index.md")` errors. **no key** |
| RT-14 | The same deny rules cover `delete`: `_DEFAULT_FS_TOOL_OPS` maps `delete → "write"`, and `_find_delete_deny_patterns` refuses a recursive delete of a directory containing denied descendants rather than executing it partially. | recon §4; verified `filesystem.py:1196-1210` | error | `delete("/kb/tags.md")` and `delete("/kb/Cooking")` both error. **no key** |
| RT-15 | A Topic Expert's write permissions are **scoped to its own topic subtree**: `[deny derived, allow write /kb/<topic>/**, deny write /kb/**]`, in that order (first-match-wins, default allow). Reads stay KB-wide — breadth-first research and cross-topic discovery need them. This makes README §1.8 rule 4 ("a solution note lives in exactly one topic — there are no copies") mechanical instead of prompt-level. | README §1.8 rule 4 L392-393; recon §4 resolution order | warning | The Cooking expert writing `/kb/Physics/notes/x.md` errors; `/kb/Cooking/notes/x.md` lands; `read_file("/kb/Physics/topic.md")` succeeds. **no key** |
| RT-16 | The **Librarian holds no KB write capability at all**: `deny write /kb/**` after the derived deny. Its only mutation is the gated `create_topic` tool, which writes through `pkb.core.scaffold_topic` on disk (outside the permission layer, RT-18). Filing needs the topic's skills, voice overload and `expert.md` behaviour, none of which the Librarian loads. | README §2.2 L493-505 | error | Every `write_file`/`edit_file`/`delete` under `/kb/**` from the Librarian errors; `create_topic` still works. **no key** |
| RT-17 | Writes to `/skills/**` (the packaged mount) are denied for every agent. Agents read the shipped defaults; editing them in place would mutate the installation for every knowledge base. | arch I3 philosophy; SK-3 | error | `write_file("/skills/voice/SKILL.md")` errors and the packaged bytes are unchanged. **no key** |
| RT-18 | Permissions are enforced **at the tool layer inside `FilesystemMiddleware`, never at the backend**. Layer 1's flush writing derived files directly on disk is therefore intended, not a violation: the deny list constrains the *agent* while the generators remain the sole writer. Corollary: no other `pkb.agents` code may write under `kb_root`. | recon §4 L233; arch I3 | error | AST/grep: no `open(...,'w')`, `Path.write_text`, or `backend.write` in `pkb.agents` outside `maintenance.py`'s `flush` call and `tools/topics.py`'s scaffold call. **no key** |
| RT-19 | I3 must be provably independent of the prompt: a regression test compiles an expert with a hostile system prompt ("you may edit any file, including index.md") and asserts the derived write is still refused. The `llm-wiki` example's prompt-only approach is explicitly rejected. | arch I3 L116-118 | error | Hostile-prompt graph still returns `status="error"` for `/kb/Cooking/index.md`. **no key** |
| RT-20 | The `execute` tool is registered on every deep agent and is **not** in `_DEFAULT_FS_TOOL_OPS`, so it bypasses permissions entirely — but with `CompositeBackend(StateBackend, FilesystemBackend)` it is inert (live-verified: `Error: Execution not available … SandboxBackendProtocol`, nothing written). Layer 2 must keep it inert: never `LocalShellBackend`, never a sandbox backend. | arch §10 L417-418; recon §1, §4 | warning | A scripted `execute` call errors and creates no file; `isinstance(backend, SandboxBackendProtocol)` is False. **no key** |

#### RT-C · Approval gates

| ID | Rule | Source | Sev | Test assertion (API key?) |
|----|------|--------|-----|---------------------------|
| RT-21 | `requires_approval(tool_name, args, snapshot) -> GateReason \| None` is **one pure function** in `gates.py`, used both as the `when` predicate on the `interrupt_on` entry and by the description factory. "What gates" is table-testable without constructing a graph. | arch §9 L385-390 | error | Table test over ~20 (path, tool) rows asserting the expected `GateReason`; no graph, no model. **no key** |
| RT-22 | Path-selective gating uses a **`when` predicate on a single `interrupt_on` entry per tool**, not a second permissions list. One `interrupt_on["write_file"]` entry cannot itself distinguish paths, and permission-derived interrupts over-fire on bulk tools when patterns are unanchored. | recon §7 L338-350; verified `_merge_fs_interrupt_on` | error | A write to `notes/x.md` produces no interrupt; `notes/summary.md` produces one — same agent, same entry. **no key** |
| RT-23 | **GATE — breadth approval.** Agent writes/edits to `<topic>/topic.md`, `<topic>/notes/summary.md` and `<topic>/references/summary.md` interrupt with `["approve","edit","reject"]`. These are the three "compact approval surfaces". | README §1.3 L92/96/99, §1.6 L251-261, §1.8 rule 3 | error | Each of the three paths raises exactly one interrupt; `notes/grill.md` raises none. **no key** |
| RT-24 | **GATE — human-content edits.** Any write that changes the **body** of an existing authored file under `notes/` or an extension folder interrupts. The AI does not change a note's factual content without approval. | README §1.6 L245-249; §1.8 rule 1 | error | A body-altering edit interrupts and the tree is unchanged; a frontmatter-only edit does not. **no key** |
| RT-25 | **GATE — new tags.** A write whose frontmatter introduces a `topic.*`/`domain.*` tag absent from `pkb.core.build_tag_tree(snapshot)` interrupts *before the file lands*. Layer 1 keeps no approved-tag list (TG-9/VA-40), so this gate is the **only** mechanical backing for README §1.5's "Do not create ad-hoc tags"; the `tag-proposal` skill shapes the dialog, the gate makes it unskippable. | README §1.5 L189-191, §1.8 rule 7, §1.9 L442-444 | error | Re-using `topic.cooking.grilling` does not gate; adding `topic.cooking.sous-vide` gates once and the description names the new tag. **no key** |
| RT-26 | **GATE — conflict resolution.** Clearing a conflict (`status.conflict-review` → `status.approved`, `review_note` removed, `last_reviewed` set) interrupts. **Adding** the conflict flag does not — README §1.7 explicitly instructs the AI to tag, it changes no content, and gating it would block every background scan on a human. This is the one deliberate exemption from RT-24. | README §1.7 L296-304 vs L324-332; §2.1 L485-486 | error | A scan run that only adds tag + `review_note` completes with zero interrupts; a resolution edit gates once. **no key** |
| RT-27 | **GATE — status.approved.** Any agent write that *introduces* `status.approved` on a file class the human curates (notes, the three breadth files, extension-folder content) interrupts. Reference depth files are exempt (SK-13 / Q4). | README §1.5 L168/L222, §1.3 footnote 1, §2.1 L485-486 | error | A new note tagged `status.approved` gates; the same tagged `status.draft` lands unattended. **no key** |
| RT-28 | **GATE — extension folders.** Creating the *first* file under a directory directly beneath a topic root that is not in `pkb.core.STRUCTURAL_DIRS` (minting `recipes/`) interrupts. Writing into an existing extension folder does not. | README §1.2 L72, §1.9 L471-472, §2.3 L532; layer1 PA-7 | error | The first `Cooking/recipes/x.md` gates; the second does not. **no key** |
| RT-29 | **GATE — expert.md and skill overloads.** Agent writes to `<topic>/expert.md` and `<topic>/skills/**` interrupt: README classifies both as human-created, AI-assisted. | README §1.3 L94-95, §2.4 L564-572 | warning | Each gates once. **no key** |
| RT-30 | **GATE — delete.** Any `delete` under `/kb/**` interrupts with `["approve","reject"]`. There is no version control and no undo (D6), and moving a note is a write plus a delete. | arch D6, §10 L403-408; layer1 MA-9, SC-10 | error | `delete("/kb/Cooking/notes/x.md")` gates; approving removes it, rejecting leaves it. **no key** |
| RT-31 | **NO gate** on plain note ingestion (`notes/<t>.md`), on reference depth files (`references/<src>/<src>.md`), or on any read. Capture must be frictionless (goal 3), the note carries the human's own words arriving through dialog, and reference depth files are AI-generated on ingestion. | README §1.1 L44-46, §1.3 L97-98, §2.3 L537-547 | error | Filing a note and a reference completes with zero interrupts. **no key** |
| RT-32 | `respond` is **never** an allowed decision on a KB write gate: it yields `status="success"` with the tool skipped, telling the model the write succeeded when nothing was written. Write gates allow exactly `["approve","edit","reject"]`. | verified `human_in_the_loop.py:317-333` | error | `"respond" not in cfg["allowed_decisions"]` for every entry in the gate table. **no key** |
| RT-33 | No `interrupt_on` entry may be `False` (auto-approve) for a gated path, and `pkb.agents` contains no code path that constructs a `Command(resume=...)` on its own behalf — except the documented propose-only auto-reject (RT-42). The AI never resolves its own interrupt. | README §1.6 L267, §1.8 rule 3 | error | Static audit of the compiled config; grep for `Command(resume` outside `approval.py`/propose-only. **no key** |
| RT-34 | The interrupt **description is produced by a callable** that renders what the human must judge: the target path, whether the file exists, and a unified diff of current vs proposed content (full content for a new file). `ActionRequest` carries only `name`/`args`/`description`, and I2 forbids Layer 3 from reading the KB through the harness — so the diff must be rendered here. | arch §6 L259-266; verified `human_in_the_loop.py:146-190` | error | An approval on an existing `notes/summary.md` carries a `---`/`+++` diff; on a new file, the full proposal. **no key** |
| RT-35 | The description factory also runs `pkb.core.validate_content` on the proposal and labels a failing draft *"this draft currently fails validation: <findings>"*. HITL fires in `after_model`, strictly **before** `wrap_tool_call`, so a human can otherwise approve something the validator refuses a moment later — burning one of the three attempts and confusing the dialog. | grounding: after_model ordering; layer1 VA-1 | warning | With a gate on `notes/summary.md` and an invalid draft, the interrupt text contains the finding codes. **no key** |

#### RT-D · Run API, approvals, events

| ID | Rule | Source | Sev | Test assertion (API key?) |
|----|------|--------|-----|---------------------------|
| RT-36 | `thread_id` is minted, titled, listed and deleted by **Layer 3**. Layer 2 never invents a thread id for a user conversation and never persists the `(agent_id, thread_id)` association, so every run/resume/history call takes both ids explicitly. The one exception is internal scan threads (RT-45). | arch §5 L192-197, L226-236 | error | grep: no uuid minting on the user path; `run()` requires both ids. **no key** |
| RT-37 | Layer 2 owns `configurable.checkpoint_ns` entirely. The only key it sets on a user run is `thread_id` (plus optional `recursion_limit`); nested subagent runs get their namespace automatically from the ambient parent config. Passing an explicit `checkpoint_ns` breaks `aget_state` (`ValueError: Subgraph … not found`). | arch §4 L175; verified live | error | The config dict Layer 2 builds equals `{"configurable": {"thread_id": …}}`. **no key** |
| RT-38 | `pending_approval(agent_id, thread_id)` is `await graph.aget_state(cfg)` → `.interrupts`, normalized. The interrupt is durable in the checkpoint, so any client, in any process, after any delay, across a daemon restart, can resolve it. | arch §8 L373; verified incl. `.tasks[0].interrupts` | error | Interrupt on one runtime, close it, reopen over the same SQLite file, read and resolve the approval. **no key** |
| RT-39 | `run()` **refuses to start a turn while an interrupt is pending**, raising `ApprovalPendingError`. Live-verified: sending a new user message to an interrupted thread silently discards the interrupt and runs as if the tool call never existed. Layer 3 maps this to 409. | verified live; arch §5 L232-233 | error | run → interrupt → run again raises; `aget_state().interrupts` still holds the original. **no key** |
| RT-40 | `resume()` validates decisions **before touching the graph**: it reads `aget_state(cfg).interrupts`, refuses a stale id with `StaleInterruptError` (leaving the thread interrupted), and refuses a count mismatch or a decision type outside `review_configs[i]["allowed_decisions"]` with `InvalidDecisionError`. Otherwise `_process_decision` raises a bare `ValueError` inside the graph and kills the run — live-verified that an unmatched interrupt id degrades into a confusing count-mismatch error. | verified live; `human_in_the_loop.py:334-343` | error | A stale id and a `respond` against `["approve","reject"]` each raise a typed error and the graph is never invoked. **no key** |
| RT-41 | All interruptible tool calls in **one AIMessage batch into a single interrupt**, with `action_requests[i]` positionally aligned to `review_configs[i]`. `ApprovalRequest.actions` is an ordered tuple and `decisions` must be the same length. When a delegated expert interrupts, `astream(subgraphs=True)` emits `__interrupt__` **twice** — namespace `('tools:<uuid>',)` and `()` — carrying the same `Interrupt.id`; Layer 2 dedupes by id. | recon §7; verified live end-to-end | error | Two gated writes in one message → one normalized `interrupt` event with two actions; one delegated gated write → exactly one event. **no key** |
| RT-42 | `run(..., approval_mode: Literal["interactive","propose_only"])`. In `propose_only` the gate predicate still fires but Layer 2 auto-answers `reject` with a fixed message, records a `PendingProposal`, and the run completes — so an MCP caller never hangs on an approval it cannot satisfy. This is the only sanctioned Layer-2-authored decision. | arch §6 L302-308 | warning | A propose-only run over a `notes/summary.md` write emits zero interrupt events, one `PendingProposal`, and writes no file. **no key** |
| RT-43 | Layer 2 **owns event normalization**, not Layer 3: it consumes the graph stream and yields the arch §5 union as frozen dataclasses of primitives. Layer 3 encodes them and never sees a LangChain message, an `Interrupt`, or a `Command`. Use `graph.astream(input, cfg, stream_mode=["updates","messages"], subgraphs=True)` — **not** `astream_events(version="v3")`, which on langgraph 1.2.10 is a coroutine yielding JSON-RPC envelopes, a different protocol from what arch §5 assumes. `subgraphs=True` is required or a delegated expert's messages are invisible. | arch §5 L216-222; verified live | error | Every yielded event is a frozen dataclass and `dataclasses.asdict(ev)` is JSON-serializable for all nine kinds; a regression test asserts `asyncio.iscoroutine(graph.astream_events(..., version="v3"))`. **no key** |
| RT-44 | `subagent.start`/`subagent.end` are derived from the `task` tool call — `args["subagent_type"]` names the expert — and its returning ToolMessage. The stream namespace `('tools:<uuid>',)` carries no agent name, so the task args are the only reliable label source. | arch §5 L211-212, L222; verified | warning | One delegation yields one `subagent.start` and one `subagent.end`, both naming `topic/cooking`. **no key** |
| RT-45 | Two concurrent runs on the same thread do **not** error in LangGraph OSS (verified: `asyncio.gather(run('D'), run('D'))` returned two successes). Layer 2 enforces arch §8's 409 with a per-`(agent_id, thread_id)` active-run registry raising `ThreadBusyError`. Runs on *different* threads stream concurrently and are not serialized — only the flush is. | arch §8 L363-366; verified live | error | Two concurrent `run()` calls on one thread: the second raises; two on different threads both complete. **no key** |
| RT-46 | `cancel(run_id)` is Layer 2's, because LangGraph has no server-side cancel: the runtime owns `run_id -> asyncio.Task` and cancels the task driving `astream`. Because the default `durability` is `"async"`, a cancellation can lose the last checkpoint write — use `durability="sync"` on user-facing runs. | arch §5 L198; verified `Pregel.astream(durability=…)` | warning | Cancel mid-run; the thread stays resumable and pre-cancellation state is present. **no key** |
| RT-47 | Model/provider errors surface as one normalized `run.error` event with a `retryable` flag; the thread stays resumable because the checkpoint is intact. Layer 2 never swallows the exception into a normal completion and never marks the thread finished. | arch §5 L213-214, §8 L372 | error | A scripted model that raises produces exactly one `run.error` and `aget_state` returns the pre-error checkpoint. **no key** |
| RT-48 | Layer 2 exposes `delete_thread(thread_id)` over `AsyncSqliteSaver.adelete_thread`, because Layer 3 may not import langgraph (I2). Delegated sub-runs share the parent `thread_id` (D-6), so deleting a thread removes its `tools:` namespaces too. | arch §6 L251; I2; verified | error | Delegate on thread T, delete T, assert zero checkpoint rows for T in both namespaces. **no key** |
| RT-49 | Layer 2 deliberately exposes **no thread listing**: the checkpointer cannot answer "which agent owns this thread" (`alist(None)` returns bare `CheckpointTuple`s with no agent id and no title). That is exactly why arch §5's `threads` table exists, and it is Layer 3's. | arch §5 L226-236; verified | error | `pkb.agents` exposes no callable returning thread titles or origin channels. **no key** |
| RT-50 | Layer 2 raises typed errors and emits normalized events; it contains **no supervision loop**, no `/health`, no SSE disconnect handling, no origin-channel tracking, and no logging on behalf of a transport. | arch §6 L279-281, §8 L374; I2; D9 | warning | grep: no restart/supervision loop in `pkb.agents`. **no key** |

#### RT-E · The KB write lock and the scan queue

| ID | Rule | Source | Sev | Test assertion (API key?) |
|----|------|--------|-----|---------------------------|
| RT-51 | One process-wide KB write lock, held **only** for the duration of `pkb.core.flush` and `pkb.core.scaffold_topic`. Layer 1 takes no lock and documents a sole-writer contract (MA-15); the lock is Layer 2's. It is shared by every agent id and every thread, because regeneration touches root `tags.md` and the root catalog. | arch §8 L363-365; layer1 MA-15, Q16 | error | Two concurrent runs on different threads both write; an instrumented lock counter never exceeds 1 and derived files are consistent. **no key** |
| RT-52 | The lock is **never held across a model call, a tool call, or an HITL interrupt**. Arch §8 explicitly designs for approvals that sit pending for hours; holding the lock across one would block every other thread's flush for that long. | arch §8 L363-365, L373 | error | Instrumented lock + spy: acquire/release happens inside `after_agent` only, and no model await occurs while held. **no key** |
| RT-53 | The lock is **reentrancy-safe by construction** (an `asyncio.Lock` plus a per-`asyncio.Task` depth counter). A delegated expert's flush and the Librarian's flush are sequential rather than nested — the subgraph completes inside the `task` tool before the parent's exit chain runs — but `create_topic` also takes the lock from inside a tool call, and the cost of removing the ambiguity is fifteen lines. Two parallel delegated experts then serialize rather than interleave. | arch §8 L363-365; layer1 SC-7, GE-30 | error | A Librarian turn delegating to two experts in parallel completes without deadlock, and the derived files are byte-identical to one full regeneration. **no key** |
| RT-54 | `flush` returns `list[ScanRequest]` as pure data (Layer 1 opens no database, I1/C18). Layer 2 persists it: `ScanQueue` is a **Protocol** in `pkb.contracts` with a default `SqliteScanQueue` in `scans.py` writing to `PkbRuntime.db_path` on its own connection. `KbMaintenanceMiddleware` takes a `ScanQueue`, so unit tests pass an in-memory list and touch no DB. | layer1 MA-11/MA-12/C18; arch §7 L354-357, §9 L388 | error | After a flush the injected queue holds one request; `pkb.core` still contains no DB code. **no key** |
| RT-55 | Enqueue happens **inside the same `after_agent` call and the same critical section as the flush**. A crash between the file writes and the enqueue loses the scan permanently: the next flush only sees that turn's touched paths, so an unqueued conflict scan is never re-derived. | arch §7 L354-357; layer1 MA-12 | warning | Structural/spy test: the queue write is inside the lock scope. **no key** |
| RT-56 | Requests are already coalesced per topic per flush (MA-12). Layer 2 coalesces again **across** flushes: upsert keyed on `(topic_id, status='pending')` with a merged `changed_paths` set and the latest `requested_at`, so a burst of turns does not queue N identical whole-topic scans. | layer1 MA-12 (same argument across flushes) | warning | Three consecutive flushes touching one topic leave exactly one pending row whose `changed_paths` is the union. **no key** |
| RT-57 | On-demand scans use `pkb.core.build_scan_requests(snapshot, changed, origin="on-demand", requested_at=today)`. Layer 2 never constructs a `ScanRequest` by hand. `ScanRequest.topic_id` is already an agent id (PA-10) and resolves through `AgentRegistry.get` directly — no re-parsing of `topic_path`. | layer1 MA-12, PA-10 | error | An on-demand request for an untouched topic is well-formed with empty `changed_paths`; grep confirms no hand-built `ScanRequest(`. **no key** |
| RT-58 | `run_scan(request) -> ScanResult` runs the topic's expert on **its own reserved thread** (`scan:<agent_id>:<uuid4>`), with the conflict-scan prompt, and returns the id so Layer 3 can exclude scan runs from the human's thread list. The scan's context never enters a human conversation. | arch §7 L355-357, §4 L175-181 | warning | A scan run's thread id matches `^scan:`; the human's thread history is unchanged. **no key** |
| RT-59 | The scan queue schema carries **no** finding text, conflict type, confidence, resolution, or loser marker. The system keeps no record that a conflict occurred, in the tree *or* in Layer 2's SQLite; `last_reviewed` is the only permitted trace. | README §1.7 L364-371; layer1 VA-30, GE-28, §5 L568 | error | Schema inspection: no such column; after tag → resolve → reflush, no artifact mentions the prior conflict. **no key** |

---

### 1.2 `registry.py` — RG

| ID | Rule | Source | Sev | Test assertion (API key?) |
|----|------|--------|-----|---------------------------|
| RG-1 | Exactly **two kinds of agent** exist: one Librarian (the root PKB agent) and one Topic Expert per topic root. There is no maintainer agent, no per-channel agent, no separate ingestion agent — deterministic maintenance is Layer 1 code, judgment is a Topic Expert skill. `AgentRegistry` may call only `build_librarian` and `build_expert`. | README §1.9 L406-408, §2.6; arch §3 L71-74 | error | Over a fixture with N topic roots, `list_agents()` returns N+1 descriptors and the id set is `{"librarian"} ∪ {agent_id_for(t)}`. **no key** |
| RG-2 | The catalog is built from **one `pkb.core.scan.scan(kb_root)` call**, projecting `KbSnapshot.topics` — which already carries `path`, `name`, `agent_id`, `tag`, `parent`, `children`, `has_expert`, `extension_folders`, `meta`. `pkb.agents` contains **no** `os.walk`, `rglob`, or `glob` over the KB, and re-derives none of those fields. Discovery is recursive, correcting arch §4 L162's `*/topic.md` shorthand (layer1 C1). | arch §4 L162; layer1 C1, §7.4 L683-684, decision C | error | `Cooking/` and `Cooking/sub-topics/Grilling/` both appear, parent first; grep asserts no tree walk in `pkb.agents`; a monkeypatched `scan` is called exactly once per catalog build. **no key** |
| RG-3 | The catalog scan **compiles nothing**: constructing or refreshing an `AgentRegistry` reads no `expert.md`, no `SKILL.md`, and calls no `create_deep_agent`. Those are first-use costs. | arch §4 L162-164 | error | Patch `open` to raise for any path ending `expert.md`/`SKILL.md`; catalog construction still succeeds. **no key** |
| RG-4 | Graphs are constructed **lazily on first use and cached**, keyed by agent id. Fifty topics must not mean fifty graphs at boot. | arch §4 L162-164 | error | 50-topic fixture: constructing the registry calls the factory zero times; `get("topic/cooking")` once; a second `get` zero more. **no key** |
| RG-5 | Lazy construction is safe under concurrency: two simultaneous first-uses of one id compile exactly one graph and both callers receive it (an `asyncio.Lock` with double-checked caching). Runs stream concurrently in the daemon, so an unguarded check-then-build races. | arch §8 L362-365 | error | `asyncio.gather` of 10 concurrent `get("topic/cooking")`: factory call count == 1, all 10 results identical. **no key** |
| RG-6 | Each topic's **single** compiled graph serves both access paths — direct connection and Librarian delegation. `get(agent_id)` and the runnable inside `subagents()` resolve to the same cache entry, so an expert's `expert.md`/skills/model configuration can never diverge between the two paths. | arch D8; §4 L149-157 | error | After invalidation and rebuild, the Librarian's `topic/cooking` runnable and `get("topic/cooking")` resolve to the same object. **no key** |
| RG-7 | Experts are registered with the Librarian as **`CompiledSubAgent(name, description, runnable)`** — a TypedDict with exactly those three keys — never as a declarative dict `SubAgent`. Dict-subagents are compiled fresh per invocation and cannot hold the multi-turn approval dialog README §1.6 requires. | arch D8; recon §5 | error | Every expert entry passed as `subagents=` has keys exactly `{name, description, runnable}` and no `system_prompt`. **no key** |
| RG-8 | Laziness and `CompiledSubAgent` are reconciled with a **lazy `Runnable` proxy** per topic that calls `registry.get(agent_id)` on first `invoke`/`ainvoke`. deepagents only ever calls `.invoke`/`.ainvoke` on a compiled subagent's runnable, so a proxy suffices; without it, the first Librarian turn compiles all N experts. | arch §4 L162-164 vs L153-155; `subagents.py:567,595` | error | Compiling the Librarian over a 50-topic fixture builds zero expert graphs; one delegated `task` builds exactly one. **no key** |
| RG-9 | `CompiledSubAgent.name` **is** the agent id, verbatim. The generated root `index.md` renders each topic's agent id in backticks, so the strings the Librarian reads and the `subagent_type` values `task()` accepts must be identical. deepagents applies no naming constraint (`subagent_type` is a plain `str` matched against a dict), so slashes are safe. | layer1 §4.2 L360-362; `subagents.py:459-462` | error | Every backticked id parsed out of the generated root `index.md` is a key of the assembled subagent dict. **no key** |
| RG-10 | `CompiledSubAgent.description` is the topic's `topic.md` description — the same string the root catalog renders. deepagents interpolates subagent descriptions into the `task` tool description, so routing selection and the Librarian's routing view must be driven by one string, not two. | README §2.2 L497-501; `subagents.py:468` | error | For each topic, the subagent description is a substring of that topic's root-index line. **no key** |
| RG-11 | Agent ids are produced and resolved **exclusively** by `pkb.core.agent_id_for` / `topic_path_for_agent_id`, and `LIBRARIAN_AGENT_ID` is imported, never hardcoded. `pkb.agents` contains no second implementation of slugification, `sub-topics` elision, or id parsing. The `topic/` prefix makes a collision impossible even for a top-level folder named "Librarian" (id `topic/librarian`). | layer1 CX-8, PA-10; arch §4 L158-160 | error | A topic named "Librarian" yields both `librarian` and `topic/librarian` as distinct entries; grep finds no `slugify`/`sub-topics`/`"topic/"` literal in `pkb.agents`. **no key** |
| RG-12 | Sub-topics are **first-class, independently addressable agents** at every depth: `topic/cooking/grilling` is a valid id for direct connection, appears in the catalog, and is registered with the Librarian. A Librarian that can read a topic in the catalog but cannot address it is a routing bug. Ids contain `/` and are opaque: the registry accepts and returns them verbatim and never splits or re-encodes them (transport encoding is Layer 3's). | arch §4 L158-160; layer1 C1, Q9 | error | Every backticked id in the root index resolves via `get()`; only the raw slashed form is accepted. **no key** |
| RG-13 | `topic_path_for_agent_id` raises `NotATopicRootError` for an unknown id. The registry catches it and raises a typed `UnknownAgentError` carrying the id, so Layer 3 returns 404 rather than 500. | `paths.py:517-532`; arch §6 L244-252 | error | `get("topic/atlantis")` raises `UnknownAgentError` naming the id; `NotATopicRootError` does not escape. **no key** |
| RG-14 | An `AgentDescriptor` carries `agent_id`, `title`, `description`, `has_custom_expert`, `model_id` — exactly what `GET /agents` needs, and **no field typed by a harness class**. A topic whose `topic.md` is missing or unparseable still gets a descriptor, with the folder name as title (Layer 1's "a topic is never silently dropped" applies to routing too). `get_graph` returns a `CompiledStateGraph` and is Layer-2-internal: it appears on no type Layer 3 imports. | arch §6 L245; layer1 GE-13, GE-25 | error | A corrupt `topic.md` still yields a descriptor with `title == folder name`; no `AgentDescriptor` field is a harness type. **no key** |
| RG-15 | `list_agents()` returns `librarian` first, then topics in **snapshot order** — depth-first pre-order, parent before child, the same order the root `index.md` renders. The TUI pins the Librarian above that tree. | layer1 PA-5, GE-10; arch §6 L256-257 | warning | `list_agents()[1:]` ids equal, in order, the ids parsed top-to-bottom out of the generated root index. **no key** |
| RG-16 | `invalidate(agent_id: str \| None = None)` re-runs the catalog scan (never an mtime staleness check), **evicts ids no longer in the catalog** (a renamed or removed topic must never hand out a stale graph), and **always drops the Librarian's cached graph** — its subagent list and the `task` tool description are a snapshot taken at compile time, so a topic created mid-session is otherwise unroutable. | arch §4 L164-165; `subagents.py:457-462` | error | Scaffold a topic → invalidate → the new id is in `list_agents()` and the next Librarian graph can `task()` to it; rename a folder → invalidate → `get(old_id)` raises `UnknownAgentError`. **no key** |
| RG-17 | Beyond topic creation, three tree changes make a cached graph wrong: adding/removing an `expert.md` (changes the prompt for that topic *and its descendants*), adding/removing a `skills/<name>/SKILL.md`, and editing a `topic.md` description (baked into the Librarian's `CompiledSubAgent`). `KbMaintenanceMiddleware` calls `invalidate()` when its touched-path set matches any of them. Arch §4 names only topic creation. | arch §4 L164-165; README §2.2/§2.3/§2.4 | warning | Writing `Cooking/expert.md` + invalidating changes both the Cooking and the Grilling prompt source; editing `Cooking/topic.md`'s description changes the Librarian's subagent description. **no key** |
| RG-18 | Even after invalidation, an **existing thread will not see a changed skill set**: deepagents loads skills once per session and caches them in checkpointed state (`before_agent` returns early when `skills_metadata` is present). `invalidate` documents this; a fresh skill set requires clearing that state key or a new thread. | recon §6, divergence #6; verified live | warning | Add a skill after turn 1: the same thread does not see it, a new thread does. **no key** |
| RG-19 | The registry is **read-only over the KB tree**: it never scaffolds, flushes, writes a derived file, or mutates frontmatter. Graph construction reads `expert.md` and skill metadata and nothing else. | arch I3; layer1 §5 L550-551 | error | Snapshot every file's bytes and mtime, build the registry and compile every graph, assert nothing changed. **no key** |
| RG-20 | The registry API is **thread-free and delegation-free**: no method takes or returns a `thread_id` or `run_id`, and none implies delegated work is resumable (no `threads_for_delegation`, no task-call → thread mapping). Its public surface is exactly `list_agents`, `get`, `subagents`, `invalidate`. | arch §4 L175-181, §5 L226-236 | error | `inspect.signature` over every public method: no `thread_id`/`run_id` parameter; the public method set equals those four. **no key** |
| RG-21 | The **model is a registry concern**: `AgentRegistry(..., default_model="anthropic:claude-sonnet-5", models: Mapping[str,str] \| None)`. No transport, route, or channel selects a model, and the model is never read from KB content (a `model:` key in `topic.md` would be an `UNKNOWN_FIELD` warning, VA-32). `model=` is always passed explicitly — `model=None` is deprecated and silently falls back to `claude-sonnet-4-6`, shadowing the configured default. | arch §4 L183-185; recon §1; layer1 VA-32 | error | With `models={"topic/cooking": "anthropic:claude-opus-5"}` the captured kwargs differ per agent; no call site passes `model=None`; grep finds no model literal in `pkb/server` or `pkb/tui`. **no key** |
| RG-22 | `create_deep_agent` is called from **exactly two places**: `expert.py` and `librarian.py`, both called only from `registry.py`. | arch §3 L130 | error | grep/AST assertion. **no key** |

---

### 1.3 `expert.py` — EX

| ID | Rule | Source | Sev | Test assertion (API key?) |
|----|------|--------|-----|---------------------------|
| EX-1 | `build_expert(kb_root, topic_path, runtime, *, model, registry) -> CompiledStateGraph`. **Every topic root gets its own compiled graph** — one per topic, never a shared graph parameterized at call time. | arch D8; §4 L149-152 | error | Two topics yield two distinct graph objects with different configuration. **no key** |
| EX-2 | `pkb.core.resolve_expert(kb_root, topic_path)` selects the **prompt source** — the nearest ancestor topic root, itself first, holding a case-exact `expert.md`, or `None` meaning the default template. It does **not** decide whether a topic has an agent. A sub-topic without `expert.md` still gets its own graph, own id, own breadth files and own skills chain; it merely runs the ancestor's persona over its own scope. This is the reading of README §1.8 rule 5 that arch §4 and Layer 1's per-topic `*(custom expert)*` marker both require. Layer 2 re-walks no ancestors. | layer1 PA-13; README §2.3 L509-514, §1.8 rule 5; arch §4 L149-160 | error | With `Cooking/expert.md` only, the Grilling graph's prompt derives from it while its breadth files are Grilling's; adding `Grilling/expert.md` (plus invalidation) flips the prompt. **no key** |
| EX-3 | `expert.md` sits outside the PKB frontmatter regime (Layer 1 validates only its placement, VA-20). Layer 2 parses it with `pkb.core.frontmatter.parse` and uses only the **body** as prompt text, tolerating a file with no frontmatter block at all — so a human's YAML never leaks into the system prompt. | layer1 C6, VA-20, FM-1 | error | An `expert.md` with a `---` block yields a prompt containing only the body; one without yields the whole file. **no key** |
| EX-4 | The system prompt is **layered, not replaced**: code always prepends a fixed, non-overridable PKB-standards preamble (`prompts/standards.md`), and `expert.md` — or the default template — supplies the domain layer beneath it. README §2.4's "an overload extends the common procedure but never weakens the general standards" and §2.3's two-capability-layer description both require this; full replacement would silently drop the escalation, tag-proposal and conflict rules, which are prompt-level even though the file rules are mechanical. | README §2.3 L516-523, §2.4 L568-573, §1.9 L448-453 | error | A hostile `expert.md` still produces a prompt containing the standards preamble verbatim. **no key** |
| EX-5 | An `expert.md` override changes persona and domain guidance **only**. Permissions, both KB middleware, the breadth middleware, the gate table and the flush are attached by the factory in code, after and independently of prompt selection. | README §2.3 L516-520, §2.4 L570-573; arch I3 | error | Parameterized over (no expert.md, hostile expert.md): identical permission lists, identical middleware name sets, identical `interrupt_on`; an invalid write, a derived write and a self-approving write all still fail. **no key** |
| EX-6 | **Do not pass `create_deep_agent(memory=…)`.** deepagents' `MemoryMiddleware` injects a system prompt that explicitly instructs the agent to persist learnings by calling `edit_file` on the memory files — directly contradicting README §1.6 ("it never finalizes human-approved content on its own") for `topic.md` and `notes/summary.md` — and it caches contents in checkpointed state (`if "memory_contents" in state: return None`), so a long-lived thread never sees a human-approved edit. See D-11. | verified `middleware/memory.py` `MEMORY_SYSTEM_PROMPT` and `before_agent`; README §1.6 L267, §1.8 rule 3 | error | grep: `memory=` absent from `pkb.agents`; the compiled stack contains no `MemoryMiddleware`. **no key** |
| EX-7 | Instead, `KbBreadthMiddleware.wrap_model_call` / `awrap_model_call` reads the topic's **own** `topic.md` and `notes/summary.md` fresh each model call (cached on `(path, st_mtime_ns)`) and appends them to `request.system_message` via `request.override(...)`. `index.md` is deliberately not loaded: it is the depth artifact the expert reads on demand. `notes/summary.md` is the highest-priority input for decisions. A topic missing either file still builds. | arch §4 L151 (intent); README §1.3 L99, §1.8 rule 2; verified `ModelRequest.system_message` + `.override` | error | Editing `topic.md` between turn 1 and turn 2 of one thread changes the system message the scripted model receives; a nested sub-topic loads its own files, not its parent's. **no key** |
| EX-8 | Skill sources are the ordered list `["/skills/", "/kb/skills/"] + ["/kb/<each ancestor topic>/skills/" outermost-first] + ["/kb/<topic>/skills/"]`, **filtered to directories that exist**. deepagents merges last-wins by skill name, giving precedence own topic > ancestor topic > KB root > packaged default — the same precedence `pkb.core.resolve_skills` computes, which is the assertion oracle, never a second implementation. A nonexistent source makes `_list_skills_with_errors` return a source error that becomes prompt noise on every topic without a `skills/` folder — the common case, since the scaffolder creates none (SC-4). | layer1 PA-14; recon §6; grounding §6 | error | For a KB with root `voice` and a `Cooking/skills/voice` overload, the loaded mapping equals `resolve_skills(kb, cooking)` unioned with unshadowed packaged skills; a freshly scaffolded topic yields exactly `["/skills/", "/kb/skills/"]` and no source-error text in the prompt. **no key** |
| EX-9 | The prompt kwarg is `system_prompt` (there is no `instructions` in 0.7.5), `backend` must be an initialized instance, and `model` is always explicit (RG-21). | recon §1, divergences #1-#2 | error | Monkeypatched `create_deep_agent` captures a non-None `model` and a `system_prompt` key. **no key** |
| EX-10 | `CompiledSubAgent` inherits **nothing** from its parent: not `permissions`, not `interrupt_on`, not `state_schema`, not middleware, not skills, not the backend or checkpointer. Every expert graph is therefore constructed with its full configuration — or delegated work runs unguarded. Omitting one on a sub-topic silently opens the derived-write path for the delegated path only. | recon §5 L253; arch I3 | error | Parameterized over every agent id: identical deny list and middleware set; a delegated `write_file` to `/kb/Cooking/index.md` returns an error ToolMessage. **no key** |
| EX-11 | The expert graph declares `subagents=[SubAgent(name="general-purpose", …, middleware=[KbValidationMiddleware, KbMaintenanceMiddleware])]` — or is otherwise suppressed — because deepagents **auto-adds a `general-purpose` subagent to every deep agent, even one that declares no subagents**, and that subagent inherits only middleware whose `.name` collides with a default GP slot. Ours never collides, so today it can write unvalidated, never-flushed content into `/kb/`. It *does* inherit `permissions` and `interrupt_on`, so I3 holds while arch §7's "unskippable" validation does not. See D-2. | verified `graph.py:745-812`, `_gp_inheritable`; grounding §7 | error | `task(subagent_type="general-purpose")` writing an invalid note is refused; today this test fails — it is the acceptance test for the fix. **no key** |
| EX-12 | The expert owns a scope-limited **`create_subtopic`** tool: it may create sub-topics only under its own topic root, and it calls `pkb.core.scaffold_subtopic` behind the same approval gate as `create_topic` (SC-8: the scaffolder has no gate of its own), then `registry.invalidate()`. Depth is pre-checked so `TopicDepthExceededError` (SC-9) is reported as a refusal, not a crash. | README §1.9 L447, L466-472; layer1 SC-6, SC-8, SC-9 | warning | The Cooking expert refuses a parent outside Cooking's subtree; a depth-4 proposal is refused naming the limit; on approve, `scaffold_subtopic` is called exactly once. **no key** |
| EX-13 | Web-fetch or other retrieval tools are **additive and carry no filesystem write capability**; the KB write path stays the permission-guarded built-ins. | README §2.3 L536-542; arch §10 L417-418 | warning | The fetch tool's schema exposes no path argument; with it registered, the deny permissions and both middleware are still present. **no key** |
| EX-14 | The middleware list order is `middleware=[KbBreadthMiddleware, KbValidationMiddleware, KbMaintenanceMiddleware]`. `wrap_tool_call` composes first-in-list = outermost, and `after_agent` hooks run **reverse-registration order** — the last-registered middleware's `after_agent` is the graph's `exit_node`. So maintenance flushes first on the way out, validation is outermost on the tool path. If a future middleware also defines `after_agent`, its position becomes load-bearing. | verified `factory.py:626-670`, `1614-1618`, `1754-1775` | error | With two `after_agent` middleware, execution order matches the documented reverse order. **no key** |
| EX-15 | Custom middleware must not be **named** after any core stack member (`FilesystemMiddleware`, `SubAgentMiddleware`, `SkillsMiddleware`, `SummarizationMiddleware`, `PatchToolCallsMiddleware`, `MemoryMiddleware`, `HumanInTheLoopMiddleware`): `_apply_custom_middleware` merges by `.name` and a collision **replaces the core member in place** instead of appending. | recon §1 gotcha; verified `graph.py:201-235` | error | The compiled stack still contains a `FilesystemMiddleware`, and each custom name appears exactly once. **no key** |
| EX-16 | `create_deep_agent` returns `.with_config({"recursion_limit": 9_999, …})`. Layer 2 does not rely on the default recursion limit as a runaway guard; the 3-attempt bound (MW-14) and the run registry are the guards. | recon §1 L53 | info | Documented; asserted once so a version bump that changes it is visible. **no key** |

---

### 1.4 `librarian.py` — LB

| ID | Rule | Source | Sev | Test assertion (API key?) |
|----|------|--------|-----|---------------------------|
| LB-1 | The Librarian is itself a **compiled deep agent** with its own graph, id (`pkb.core.LIBRARIAN_AGENT_ID`) and threads — not a dispatcher function. Direct connection to `librarian` is the default entry point for every channel. | README §2.2, §2.5; arch §4 L158, §6 | error | `registry.get("librarian")` returns a compiled graph and `list_agents()[0].agent_id == "librarian"`. **no key** |
| LB-2 | Its four responsibilities are fixed and appear as named sections of `prompts/librarian.md`: route each inbound item to the right expert; route multi-topic items to **all** relevant experts and **merge** their responses into one answer; propose a new topic to the human when an item fits none; consult the cross-topic mappings to pull in secondary topics. It holds no deep topic knowledge and states so. | README §2.2 L488-505 | error | The four clauses and the go-wide clause are present. Behaviour is **live**. |
| LB-3 | The Librarian prompt is **KB-independent**: no topic names, no descriptions, no per-topic instructions. The routing view is the hook-generated root `index.md`; nothing is maintained by hand. | README §2.2 L497-501; arch §4 L162-165 | error | The rendered prompt is byte-identical across two different fixture KBs. **no key** |
| LB-4 | Routing inputs are the two generated root files. Root `index.md` is bounded to one line per topic and < 8 KB (GE-12), so it is loaded into context each turn by `KbBreadthMiddleware`; root `tags.md` is unbounded and is named in the prompt as a **read-on-demand** artifact. Cross-topic coordination draws only on `tags.md`'s `related_topics`-derived mappings — never on shared `domain.*` tags, folder proximity, body links, or prose. | README §2.2 L497-503; layer1 GE-12, GE-19, GE-11 | error | The breadth block contains `/kb/index.md` content and no topic-scoped file; the prompt names `/kb/tags.md`. **no key** |
| LB-5 | The Librarian carries **no topic-scoped breadth files and no topic skill overloads**: `skills=["/skills/", "/kb/skills/"]` only. It goes wide; experts go deep. Loading a topic's breadth files into the Librarian would defeat README §1.1 goal 2. | README §2.2 L505, §1.1 goal 2, §2.4 L551-552 | error | No path under a topic root appears in the Librarian's breadth block or skill sources. **no key** |
| LB-6 | An **empty KB must produce a working Librarian with `subagents=[]`** — apart from the `general-purpose` handling. Bootstrapping starts with zero topics and every inbound item is a topic gap; the root `index.md` already exists (rendering `_No topics yet._`, GE-29), so the routing view is present from the first boot. | README Part 3 L666-669; layer1 GE-29 | error | Over a `tmp_path` holding only the two generated root files, `list_agents() == [librarian]` and `get("librarian")` compiles. **no key** |
| LB-7 | The Librarian owns the gated **`create_topic`** tool: propose → interrupt (`["approve","edit","reject"]`; `edit` is what lets the human rename the topic or rewrite the description) → `pkb.core.scaffold_topic` under the KB write lock → `registry.invalidate()`. Topic creation is an agent-invoked tool, not a transport endpoint — "all interactions are agent-mediated". Sub-topic creation belongs to the expert (EX-12). | README §1.9 L464-472, §2.1, §2.2 L500-502; layer1 SC-8 | error | A fake model calling `create_topic` raises an interrupt and creates nothing; `Command(resume={"decisions":[{"type":"approve"}]})` creates the six scaffold paths and the new id appears in `list_agents()` with no restart. **no key** |
| LB-8 | Multi-topic routing means several `task()` calls in one turn. Every expert graph must therefore be safe to invoke concurrently within one Librarian run: no per-graph mutable state, one shared backend, one shared checkpointer, and no per-run attributes on middleware instances (one middleware instance serves every run of a compiled graph). | README §2.2 L495-497; verified instance sharing | error | A fake model emitting two parallel `task` calls to two topics completes and both experts' writes land. **no key** |
| LB-9 | A thread is `(agent_id, thread_id)` **as a Layer 3 bookkeeping fact**, not a checkpointer fact (D-6). A conversation held directly with an expert and work the Librarian delegates to that expert are different conversations: delegated work runs in an isolated context and returns a summary, so "continue what the Librarian was doing" resumes the Librarian's thread. Layer 2 must **not** register a thread row for a delegated run, or `list_threads(agent_id="topic/cooking")` fills with phantom entries the user cannot resume. | arch §4 L175-181, §5 L226-236 | error | After a delegated turn, the expert's thread listing is empty and the Librarian's thread holds the whole exchange. **no key** |
| LB-10 | HITL interrupts raised inside a delegated expert **propagate to the Librarian's thread** and are resolvable there with the same `Command(resume=…)` (verified end-to-end). Approval resolution is therefore routed **by thread, never by agent**. | verified live; arch §8 L373 | error | A delegated gated write yields one normalized interrupt on the Librarian's thread; approving it creates the file. **no key** |
| LB-11 | Delegation produces **two `after_agent` flushes** (the expert's, then the Librarian's). Regeneration is idempotent so this is safe, but the touched-path state key must be `PrivateStateAttr` (MW-9) so the parent's flush is a genuine no-op rather than a repeat, and the write lock must be reentrancy-safe (RT-53). | verified both fire; `subagents.py:252-256`, `:484` | warning | One delegated write produces two `after_agent` invocations; with the private annotation the parent's touched set is empty and its flush writes zero files. **no key** |

---

### 1.5 `middleware/` — MW

Two KB middleware as arch §7 specifies, plus the third one EX-6/EX-7 forces.

#### MW-A · Shape and state

| ID | Rule | Source | Sev | Test assertion (API key?) |
|----|------|--------|-----|---------------------------|
| MW-1 | `KbValidationMiddleware` and `KbMaintenanceMiddleware` are separate `langchain.agents.middleware.AgentMiddleware` subclasses, split because validation and regeneration want opposite timing. Neither may collide by `.name` with a core stack member (EX-15). | arch §7 L313-357 | error | Both custom names appear exactly once in the compiled stack and `FilesystemMiddleware` survives. **no key** |
| MW-2 | Both implement **both** the sync and async variant of every hook they use (`wrap_tool_call`/`awrap_tool_call`, `after_agent`/`aafter_agent`, `before_agent`/`abefore_agent`, `after_model`/`aafter_model`, `wrap_model_call`/`awrap_model_call`), sharing one private implementation. The base sync variant raises `NotImplementedError` when only the async one is defined, and `after_agent` is wired as `RunnableCallable(sync, async)`. The daemon is async; the non-live tests are sync. | verified `types.py`; `factory.py:1577-1589` | error | The same fixture agent through `.invoke()` and `await .ainvoke()`: both reject an invalid write and both flush. **no key** |
| MW-3 | Blocking Layer 1 calls inside async hooks run via `asyncio.to_thread` so the event loop is never blocked by a tree walk. | arch §8; grounding §10 | warning | The async flush path calls `asyncio.to_thread`. **no key** |
| MW-4 | Middleware instances hold **read-only configuration only** (`kb_root`, queue, clock). One instance serves every run of a compiled graph, so per-run state lives in the middleware `state_schema`, never on `self`. | verified instance sharing across runs | error | The middleware has no mutable instance attribute written during a run. **no key** |
| MW-5 | Shared state lives in `middleware/state.py` as a TypedDict extending `AgentState`, merged into the resolved graph schema by langchain. Both keys are `NotRequired[Annotated[..., reducer, PrivateStateAttr]]`: `kb_touched: list[str]` and `kb_attempts: dict[str, int]`. `PrivateStateAttr` keeps them out of the public input/output schema **and** out of subagent state plumbing (deepagents passes the parent's whole state down except `messages`/`todos`/`structured_response` and private keys, and merges the subagent's back on return). | verified `factory.py:1154`, `types.py:343`, `subagents.py:252-256/484` | error | The counter accumulates across two tool calls in one run; `get_state(cfg).values` does not expose the keys; a delegated run's flush report lists only the subagent's paths. **no key** |
| MW-6 | Both reducers treat `None` on the right as a **reset**; `operator.add` is insufficient because a list-append reducer cannot clear a key, and state is checkpointed, so turn 2 would inherit turn 1's touched set. The touched-path reducer additionally de-duplicates while preserving first-seen order. | arch §7 L345-347; observed live | error | After `after_agent`, the touched key is `[]`; writing the same path twice in one run yields one entry; the second turn's flush sees only the second turn's paths. **no key** |

#### MW-B · `KbValidationMiddleware`

| ID | Rule | Source | Sev | Test assertion (API key?) |
|----|------|--------|-----|---------------------------|
| MW-7 | `wrap_tool_call` intercepts exactly `write_file` and `edit_file`, read from `request.tool_call["name"]`, with args at the deepagents 0.7.5 names: `write_file` → `file_path`, `content`; `edit_file` → `file_path`, `old_string`, `new_string`, `replace_all` (default `False`). Every other tool is forwarded unchanged and records no touched path. Direct assignment to `request` is deprecated — use `request.override(...)`. | arch §7 L317; verified `WriteFileSchema`/`EditFileSchema` | error | A scripted `read_file`/`grep`/`ls` passes through untouched; the middleware reads exactly those arg names. **no key** |
| MW-8 | Path handling is RT-9/RT-10: normalize with `validate_path` first, then a path is KB-scoped iff it is `/kb` or begins `/kb/`; the remainder is the KB-relative POSIX string Layer 1 wants. Anything else belongs to the `StateBackend` default route (agent scratch, thread-scoped) and is passed through with **zero** validation calls and no touched-path record. | arch §4 L166-173; verified `_route_for_path` | error | `/scratch/notes.md` passes through with zero `validate_content` calls; `/kb/Cooking/notes/a.md` is validated as `Cooking/notes/a.md`. **no key** |
| MW-9 | For `write_file` the proposed content is `args["content"]` verbatim and the middleware calls `pkb.core.validate_content(kb_root, rel, content)`. It **re-derives, duplicates and pre-filters nothing**: required fields, tag syntax and depth, naming, location consistency and the derived/skill file classes are entirely Layer 1's, through exactly one call site. | arch §7 L317-325; layer1 VA-1, VA-5, VA-6 | error | AST/grep: `pkb.agents` contains no frontmatter parsing, no tag regex, no `REQUIRED_FIELDS`, no `index.md`/`tags.md`/`SKILL.md` literal in the validation path, and exactly one `validate_content` call site. **no key** |
| MW-10 | For `edit_file` the middleware validates the **resulting file**, not the fragment: read current bytes, apply `deepagents.backends.utils.perform_string_replacement(content, old_string, new_string, replace_all)` — the exact function `FilesystemBackend.edit` uses, so the simulation cannot diverge — and validate the result. If that function returns a `str`, that is deepagents' own error (zero occurrences, non-unique match): forward to `handler` and let the tool report it. | arch §7 L317; verified `filesystem.py:521-580` | error | An edit that would introduce a 5-level tag is blocked and the file is byte-unchanged; an edit whose `old_string` is absent produces deepagents' error, not a PKB finding. **no key** |
| MW-11 | The middleware **skips `is_derived_name` paths entirely**, deferring to I3. Arch §7's "in practice the validator never sees them" is wrong about ordering: deepagents enforces permissions *inside* the tool body, after every `wrap_tool_call` middleware has run, so the validator does see the call. | arch §7 L326-329; verified `filesystem.py:2012`; grounding | error | A write to `/kb/Cooking/index.md` produces exactly ONE ToolMessage — the permission denial — and zero validation findings. **no key** |
| MW-12 | Blocking is decided by `pkb.core.has_errors(findings)` — **error severity only**. Warning- and info-severity findings never block. This is what keeps VA-25, VA-29, VA-33 and VA-35 from burning attempts, which is the stated reason those severities were chosen. Warnings are appended to the *success* ToolMessage as a short advisory block (capped at a few lines) so the corpus converges on canonical form without costing a retry. | layer1 VA-25/VA-29/VA-33/VA-35 rationale; `has_errors` | error | A warning-only note lands with `status="success"` and the advisory text present; one error finding refuses. **no key** |
| MW-13 | On block the middleware returns `ToolMessage(content=…, name=tool_call["name"], tool_call_id=tool_call["id"], status="error")` **without invoking `handler`**. The write never reaches the backend and the agent self-corrects in-loop. The text is `pkb.core.render_findings(errors_only(findings))` **verbatim** — Layer 2 may prepend only its own attempt counter and next-step line, and never re-words, truncates or re-orders a Layer 1 message (`Finding.render()` already emits `path: L<line> [CODE/RULE_ID] (field) message — hint`, and `sort_findings` already orders errors first). | arch §7 L330-333; layer1 CX-6; verified live | error | The spy `handler` was not called, the status is `error`, the target does not exist, and the content contains `render_findings(...)` as an exact substring with every `code` and `rule_id`. This is arch §9's headline `pkb.agents` assertion. **no key** |
| MW-14 | Retries are bounded to **3 attempts per file per run**, keyed by normalized KB-relative path — not by tool name and not by `tool_call_id`, so a `write_file` failure and an `edit_file` failure on the same file share one counter. The counter is reset in `before_agent`, which the factory wires as a once-per-run entry node; "run" therefore means one graph invocation, not one model turn and not the thread's lifetime. `before_agent` does **not** re-run on interrupt resume, so paths touched before an approval survive the pause. | arch §8 L371; layer1 VA-2; verified `factory.py:1699-1712` | error | `write_file(bad)` then `edit_file(bad)` on the same path reaches attempt 2; two sequential `invoke()`s on one thread each start at 0. **no key** |
| MW-15 | On the 4th blocked attempt for one file the middleware **escalates to the human** rather than looping: `after_model`, decorated `@hook_config(can_jump_to=["end"])`, returns `{"jump_to": "end", "messages": [AIMessage(<escalation>)]}`. `_resolve_jump("end")` resolves to `exit_node`, and `exit_node` **is** the `after_agent` chain — so the maintenance flush still runs. The message names the offending path, the blocking `code`/`rule_id`s and what the human must decide; the run ends normally so the thread stays resumable on any channel. | arch §8 L371-373; verified `factory.py:1614-1618`, `1804-1816` | error | Four invalid writes to one path end with an escalation AIMessage, `after_agent` still fired, `get_state(cfg).next` is empty, and a follow-up `invoke()` succeeds. **no key** |
| MW-16 | Escalation must **not** be `Command(goto=END)` from `wrap_tool_call`. `END` is a different node from `exit_node`; jumping there bypasses the `after_agent` chain and skips the flush, leaving stale derived files — exactly the state arch §7 L348-350 says must never occur. | arch §7 L348-350; verified | error | grep: no `Command(goto=END)` in `pkb.agents`; the 4-attempt test asserts the flush ran. **no key** |
| MW-17 | A path is recorded as touched **only** when the handler returned and the result is a `ToolMessage` with `status == "success"` (or a `Command` whose embedded ToolMessage is success). Blocked calls, permission denials, backend errors and `validate_path` errors all record nothing — a denied `/kb/index.md` write must not get its `updated` bumped. | arch §7 L345-347; layer1 MA-3; verified | error | A run whose only write was rejected, and a run whose only write was denied, both leave the touched set empty. **no key** |
| MW-18 | A successful KB write is recorded by returning `Command(update={"messages": [<the handler's ToolMessage>], "kb_touched": [rel_path]})`. The `messages` entry is **mandatory**: `ToolNode._validate_tool_command` raises `ValueError` unless the update contains a `ToolMessage` whose `tool_call_id` matches. A bare `ToolMessage` cannot carry a state update. | verified `tool_node.py:1503-1580`; live | error | After a successful write, `get_state().values["kb_touched"] == ["Cooking/notes/a.md"]` and the ToolMessage appears in `messages` exactly once. **no key** |
| MW-19 | A successful `delete` under `/kb/` is also recorded as touched (it needs no validation, but a removed note left listed in `index.md` and `tags.md` is exactly the stale-derived-file state §7 says is worse than the write itself). A deletion does **not** queue a conflict scan — MA-12's triggers are creates and modifies. | arch §7 L335-350; layer1 MA-12 | warning | Deleting a note regenerates the topic index without it and enqueues no `ScanRequest`. **no key** |

#### MW-C · `KbMaintenanceMiddleware`

| ID | Rule | Source | Sev | Test assertion (API key?) |
|----|------|--------|-----|---------------------------|
| MW-20 | `after_agent`/`aafter_agent` consumes `kb_touched`, calls `pkb.core.flush(kb_root, touched, today=<injected clock>)` **exactly once**, and returns `{"kb_touched": None}` to clear it. Per-write regeneration is forbidden: it would rewrite `tags.md` several times per turn. `today` is injected (`clock: Callable[[], date]`), never read from the wall clock inside the middleware, so a same-day double flush is provably idempotent and a date-boundary test is possible. | arch §7 L335-347; layer1 MA-3 | error | Three writes in one turn → one `flush` call with all three paths; a spy asserts `regenerate_all` is never called from `wrap_tool_call`; two flushes at the same injected date rewrite nothing the second time. **no key** |
| MW-21 | The paths passed to `flush` are **KB-relative POSIX strings** (or absolute on-disk paths under `kb_root`). Passing the agent-visible `/kb/...` form is silently useless: `flush` normalizes and ignores anything outside the tree, so no timestamp is bumped and no scan is queued, with no error raised. | verified `maintenance.py:87-110` | error | One note write yields `FlushReport.stamped == ["Cooking/notes/a.md"]` and one `ScanRequest`; a regression test asserts the `/kb/`-prefixed form yields zero stamped paths. **no key** |
| MW-22 | `flush` is called **once per run even when the touched set is empty**. Regeneration is skip-if-identical at the byte level (GE-8), so a no-op flush writes nothing and leaves mtimes untouched, and calling it unconditionally is what keeps derived files correct after a hand edit made between turns. Cost is one tree walk per run. | layer1 GE-5, GE-8, MA-14 | warning | A zero-write run still calls `flush`; `FlushReport.written == []` and every derived file's `st_mtime_ns` is unchanged. **no key** |
| MW-23 | The flush and the enqueue happen **inside the KB write lock** (RT-51…RT-55); the lock scope is exactly that critical section. | arch §8 L363-365 | error | Instrumented lock: acquired and released inside `after_agent`, never held across an await of a model or tool call. **no key** |
| MW-24 | `FlushReport(written, unchanged, stamped, findings, scan_requests)` is **never discarded**: `findings` go to an injected sink (the event stream / daemon log) and `scan_requests` to the queue. Swallowing the report makes broken-link and orphan flags invisible outside the topic index. | layer1 MA-10, MA-13; arch §7 L354-357 | error | A flush over a KB with a broken link delivers the finding to the injected sink. **no key** |
| MW-25 | `flush` never raises for content defects (CX-5, MA-14, FM-13), but Layer 2 still wraps the call so an I/O failure inside it cannot mask the agent's own result or take down the run. A failed flush is logged and reported, never re-raised into the agent's message stream. | layer1 MA-14, §7.3 (`DERIVED_WRITE_FAILED`, …) | warning | Make the KB root read-only mid-run: the run still returns its answer and the report carries `DERIVED_WRITE_FAILED`. **no key** |
| MW-26 | **`after_agent` alone does not satisfy arch §7's "the flush runs on both success and failure."** It is a graph node on the normal exit edge; an exception anywhere aborts the superstep and it never executes (live-verified across four failure shapes). `pkb.agents.runtime` therefore wraps **every** graph execution in `try/finally` and flushes there. This is why the runtime owns the only sanctioned way to execute a graph: there will be HTTP runs, Telegram runs, the scan worker and MCP calls, and putting the guard anywhere else makes a fifth caller that forgets structurally possible. | arch §7 L348-350, §8 L375; grounding §3 | error | A scripted model that raises after one successful write still ends with derived files matching the tree. **no key** |
| MW-27 | On the failure path the touched set is recovered **from the checkpoint** — `graph.aget_state(cfg).values["kb_touched"]` — not from an in-memory side channel, because the tools node committed its state update before the model node raised (live-verified: the key held the written path with `next == ('model',)`). Afterwards the runtime clears it with `update_state(cfg, {"kb_touched": None})`, or a later resume re-flushes the same paths and re-bumps `updated` across a date boundary. | arch §8 L375; grounding §3; layer1 MA-3 | error | After a forced mid-run exception the recovered set equals the paths written; resuming the next simulated day writes the `updated` line once, not twice. **no key** |
| MW-28 | The failure-path flush and `after_agent` must **not both run** for one run: guard on whether `kb_touched` is still populated (a run-scoped sentinel plus the state check), so the outer handler is a no-op after a normal completion. A double flush is harmless to the tree but would double-enqueue conflict scans. | arch §7 L349-350; layer1 MA-11/MA-12 | error | A successful run enqueues exactly one `ScanRequest` per touched topic, not two. **no key** |
| MW-29 | `after_agent` also does not run on the **interrupted** turn — it runs once when the resumed run completes. So the flush correctly lands after the human decides, but a thread abandoned at an interrupt leaves the tree unflushed until the approval is resolved; RT-7's startup regeneration is what closes that. | verified live | warning | Interrupt → zero flushes; resume → exactly one. **no key** |
| MW-30 | After the flush, if the touched set matched `expert.md`, `skills/**` or a `topic.md`, the middleware calls `registry.invalidate()` (RG-17). It performs **no other registry mutation** and never compiles a graph. | arch §4 L164-165 | warning | Writing `Cooking/expert.md` triggers exactly one `invalidate` call. **no key** |
| MW-31 | Scan findings are written by the expert through the **normal tool path** — `write_file`/`edit_file` adding `status.conflict-review` plus a `review_note` — so they pass `KbValidationMiddleware` (VA-29's coupling, VA-30's no-residue rule) and trigger the ordinary flush. Layer 1 never writes those fields (MA-6) and Layer 2 must not write them out-of-band either. | README §1.7 L298-304; layer1 MA-6, VA-29, VA-30, §5 L538 | error | A scripted scan leaves the tag on disk, produces a `Needs review` entry after the flush, and a residue field like `conflict_type:` is refused with `FORBIDDEN_CONFLICT_FIELD`. **no key** |

---

### 1.6 `skills/` and `skills.py` — SK

| ID | Rule | Source | Sev | Test assertion (API key?) |
|----|------|--------|-----|---------------------------|
| SK-1 | The implementation ships exactly **eight** starter skills, one directory each: `summarization`, `conflict-detection`, `tag-proposal`, `ingestion-classification`, `sub-topic-proposal`, `voice`, `discovery`, `research`. README §2.4's "others as needed (e.g. interviewing)" is deferred. | README Part 3 L657-661, §2.4 L549-566, §1.9 L435-448 | error | `set(DEFAULT_SKILL_NAMES)` equals those eight and matches the packaged directory listing. **no key** |
| SK-2 | Every shipped directory name is a valid deepagents skill name (1-64 chars, lowercase alnum with single hyphens) and the frontmatter `name` **equals the parent directory name exactly**. deepagents silently drops a skill that violates this — it simply disappears from the prompt. | recon §6 L276 | error | For each packaged `SKILL.md`: `meta["name"] == dir.name` and matches `[a-z0-9]+(-[a-z0-9]+)*`. **no key** |
| SK-3 | Shipped skills live as **package data** at `pkb/agents/skills/<name>/SKILL.md`, resolved through `importlib.resources` (works from a wheel and an editable checkout alike), and are exposed to agents as a **second read-only `CompositeBackend` route** `/skills/` (RT-6, RT-17), passed as the first entry of `skills=`. They are never written into the KB tree as a side effect of starting the daemon — README Part 3 step 4 says "Nothing is seeded by hand", and the mount gets override resolution for free from D7 while keeping shipped-skill upgrades automatic. Live-verified working in the grounding pass. | README Part 3 L657, L670-671; arch D7; grounding §6 | error | `packaged_skills_root()` contains all eight; a fresh `tmp_path` KB has no `skills/` directory after building the Librarian and an expert; `backend.read("/skills/voice/SKILL.md")` returns packaged text. **no key** |
| SK-4 | `adopt_skill(kb_root, name, *, topic_path=None) -> AdoptResult` is the **only** way a shipped skill reaches the tree: it copies one directory to `<kb>/skills/<name>/` (or `<topic>/skills/<name>/`), **refuses to overwrite** an existing copy (SC-10's instinct, D6's no-undo), and reports what it did. This is the same act as creating a topic-level overload, so one mechanism covers both — and it is exactly README Part 3 step 1's "the human rewrites them, with AI assistance" loop. | README Part 3 L657-661, §2.4 L568-573; layer1 SC-10 | error | Adopting twice leaves the first copy's bytes unchanged and reports it skipped; the copy validates clean under `validate_content`. **no key** |
| SK-5 | Adoption is a **permanent fork** and must say so at the moment it happens **and inside the copied file** (the CLI output is long gone by the time the human opens it): the copy shadows the packaged default forever, so later shipped improvements never reach it. Deleting the copy restores the default — the only revert, and a deliberate human act. Override is whole-record replacement, not field merge, so adoption copies the whole directory. | recon §6 L295; README Part 3 L660-661; arch D6 | error | The adopted file's first body line is the fork notice; `AdoptResult.message` names the shadowing consequence and the delete-to-revert path; truncating an adopted `summarization` leaks no packaged text. **no key** |
| SK-6 | Shipped `SKILL.md` files carry **deepagents frontmatter only** — `name`, `description` required, `license`/`compatibility`/`allowed-tools` optional — and never the seven PKB fields. `skills/**` is Layer 1's third file class (VA-6, README amendment #3); PKB frontmatter would break deepagents' parsing and forfeit the override resolution D7 was chosen for. `allowed-tools` is **omitted in v1**: the recon parses it into `SkillMetadata` but does not establish its enforcement semantics on this pin, and a wrong restriction is a silent mid-task capability gap. | layer1 VA-6, C3; arch §12; recon §6 | error | `validate_content(kb, "skills/<n>/SKILL.md", text) == []` and the key set ⊆ `{name, description, license, compatibility}`. **no key** |
| SK-7 | Each `description` states **when to invoke the skill** ("Use when …") and stays under 1024 chars. Progressive disclosure puts only name + description + path in the system prompt, so the description is the entire routing surface and the sole reason the model ever opens the body. | recon §6 L276, L297 | error | `len(description) <= 1024` for all eight; each contains a trigger clause. **no key** |
| SK-8 | The procedure lives in the SKILL.md **body**, never inlined into the expert or Librarian system prompt. Duplicating it defeats the progressive disclosure D7 buys and doubles the surface the human must rewrite. | arch D7; recon §6 L297 | warning | No packaged body paragraph (normalized) appears as a substring of any rendered system prompt. **no key** |
| SK-9 | Shipped bodies address KB files **by role relative to the topic root** ("the topic's `notes/summary.md`") and never hardcode a topic name, a KB root, or the `/kb/` mount prefix. The topic-specific paths come from the expert's prompt, which alone knows its topic. | README §2.4 L549-573; layer1 CX-3 | warning | `grep -L '/kb/' pkb/agents/skills/*/SKILL.md` matches all eight; no body names a concrete topic folder. **no key** |
| SK-10 | No shipped skill relies on prompt text to protect derived files or to enforce a rule Layer 1 enforces mechanically. A skill may mention in one line that `index.md` and root `tags.md` are machine-generated so the agent does not waste a tool call — the enforcement is RT-11's deny list. | arch I3 L116-118; layer1 §7.4 | error | A fake model writing `/kb/Cooking/index.md` is refused regardless of which skills are loaded; no packaged body instructs a write to `tags.md`/`index.md`. **no key** |
| SK-11 | Every shipped body **ends by naming the human approval gate it terminates at**, and no shipped skill ends in a state where the agent has finalized human-curated content on its own. | README §1.6 L264-267, §1.8 rule 3, §1.3 footnote 1 | error | Each of the eight has an explicit approval step section. **no key** |
| SK-12 | Every shipped body carries a shared closing footer stating that it is a **starter draft**, that the human is expected to rewrite it with AI assistance, and how to adopt an editable copy. Plain prose, no code, nothing a non-programmer cannot edit. | README Part 3 L657-661, §2.4 L564-566 | warning | Each body ends with the shared `_DRAFT_FOOTER` constant. **no key** |
| SK-13 | A shipped skill defines the **floor, never a ceiling**: it states the general standard so an overload can extend it, and contains nothing that would let an override weaken a Part 1 standard. Layer 1 validates output regardless of which skill version produced it. | README §1.9 L448-453, §2.4 L568-573 | error | A deliberately permissive topic overload of `ingestion-classification` still gets its invalid write refused. **no key** |
| SK-14 | Restated Layer 1 content in skill bodies is limited to what an agent **cannot recover from in one attempt**: the four tag namespaces and the depth cap, the seven required fields, and the folder-hosted `[item]/[item].md` convention. Single-field mistakes self-correct cheaply because the middleware carries Layer 1's `hint` verbatim (CX-6); a structurally wrong file layout burns all three attempts. Cite Layer 1 rule ids in the body so a human editing it knows which lines are load-bearing. | arch §8 L371; layer1 CX-6 | warning | Each restated constraint matches the corresponding Layer 1 constant in a table-driven check. **no key** |
| SK-15 | The registry surfaces two skill-health diagnostics Layer 1 cannot: `SKILL_NAME_MISMATCH` (frontmatter `name` ≠ directory name — deepagents drops it without explanation and VA-6 checks presence only) and `INERT_SKILL_OVERLOAD` (an overload in a directory that is not a topic root, so no expert's source chain reaches it). | recon §6 L276; layer1 VA-6, PA-14 | warning | A KB skill declaring `name: tone` at `skills/voice/` yields one `SKILL_NAME_MISMATCH` and does not appear in the agent's skill list. **no key** |
| SK-16 | Layer 2's source ordering must **agree with `pkb.core.resolve_skills`**: for every topic, the winning skill set the harness loads equals `resolve_skills(kb_root, topic_path)` unioned with the packaged defaults not shadowed by a KB copy. Two independent implementations of one precedence rule are pinned by a property test. `voice` is the overload this matters most for (§6.6): a topic voice **replaces** the root profile rather than merging with it, because the resolver returns one path per skill name. | layer1 PA-14; recon §6 | error | Property test over generated KB trees. **no key** |
| SK-17 | Adopted and shipped skills are **excluded from every knowledge-base artifact**: they appear in no `index.md`, contribute no tags to `tags.md`, and a `topic.md` smuggled under `skills/` does not create a topic root. Layer 2 must not defeat that by writing skills anywhere but `skills/`. | layer1 VA-6, GE-15, §7.2 L662 | error | After adopting all eight into a fixture KB, `regenerate_all` output is byte-identical to before adoption. **no key** |
| SK-18 | Skill **content quality is not testable without a model.** The shipped-skill suite tests mechanics only: frontmatter schema, name/dir agreement, description cap, VA-6 cleanliness, discovery through the backend, precedence ordering, adoption semantics, invalidation. Any assertion about what a skill makes a model do lives behind the `live` marker. | arch §9 L384-392 | error | The unmarked suite passes with `ANTHROPIC_API_KEY` unset and sockets blocked; the live suite is deselected by default. **no key** (that is the assertion) |

---

### 1.7 `prompts/` — PR

| ID | Rule | Source | Sev | Test assertion (API key?) |
|----|------|--------|-----|---------------------------|
| PR-1 | Three package-data prompt files: `standards.md` (the non-overridable PKB preamble, EX-4), `expert_template.md` (the one default Topic Expert template for the whole PKB), `librarian.md`. There is exactly one expert template, shipped with the implementation and **not stored in the KB tree**. | README §2.3 L509-514; arch §3 L131 | error | `importlib.resources` locates all three; the rendered default prompt for "Cooking" names the topic's display name and its `topic.md`/`index.md` paths. **no key** |
| PR-2 | The expert template states **both capability layers** (PKB general standards + unique topic knowledge) and the five §2.3 responsibilities: answer using breadth or depth files as the request requires; ingest routed information; carry out the judgment side of maintenance; manage topic-specific extensions with approval; escalate for summary approval, new tags and conflict resolution. | README §2.3 L507-534 | error | A marker clause exists for each of the five and both layers; the rendered prompt for two topics differs only in the topic substitutions. **no key** |
| PR-3 | The expert prompt distinguishes **inbound information** (ingestion path: classify, draft, file, gates) from **inbound questions** (retrieval path: breadth files for broad questions, depth files for specific ones — and it writes nothing). | README §2.2 L495-497, §2.3 L527-531, §1.8 rule 2 | error | A scripted question-shaped turn produces zero writes and zero `ScanRequest`s. **no key** |
| PR-4 | **No prompt and no shipped skill restates a rule Layer 1 enforces mechanically** — required-field lists as validation rules, tag depth as a check, naming conventions, index/`tags.md` regeneration, `updated` stamping, "do not edit `index.md`". Arch §7's whole premise is that an unskippable check beats an instruction; prose duplication creates a second, driftable source of truth. (SK-14's small restated set inside the ingestion skill is the deliberate exception, and is a *template*, not a rule restatement.) | arch §7 L311-333, I3; README §1.9 L406-430 | error | A lint test over every rendered prompt asserting the absence of a banned-phrase list ("4 levels", "required fields", "regenerate index.md", "update the timestamp", "do not edit index.md"). **no key** |
| PR-5 | What the prompts **do** carry is what no mechanical check can see: descriptions must be meaningful; tags are proposed before use; drafts are written in the human's voice; human content wins over static knowledge; the escalation duties; and that the human decides while the agent applies the change on their behalf. | README §1.9 L432-453, §2.4 L549-563, §1.7 L272-278, §2.1 L478-486 | error | The prompt contains the six judgment clauses. First-attempt validation pass rate is **live**. |
| PR-6 | The Librarian prompt carries the four §2.2 responsibilities and the go-wide clause, and contains no topic names, no domain knowledge and no per-topic instructions (LB-2, LB-3). | README §2.2 L488-505 | error | Assertion as in LB-2/LB-3. **no key** |
| PR-7 | No prompt asks the agent not to edit derived files (RT-11/RT-19), not to delete human content (RT-30), or not to self-approve (RT-27/RT-33). Every one of those is a harness mechanism. | arch I3 | error | Banned-phrase lint as PR-4. **no key** |
| PR-8 | The conflict-scan prompt used by `run_scan` names the `conflict-detection` skill, the topic under scan, and the three comparison axes, and states that the run must not answer a user question. | README §1.7 L288-296; arch §7 L355-357 | warning | The rendered scan prompt references the skill by name and the three axes. **no key** |

---

### 1.8 `packs.py` — PK *(deferred within Layer 2 — build after the six modules land)*

Context packs are a first-class Topic Expert responsibility (README Part 4) and back two of arch §6's
MCP tools, but they are not in the step-2 build list. They belong in `pkb.agents` — a pack is a
deterministic selection over Layer 1's derived surface, so making it prose loses reproducibility, and
putting it in Layer 3 duplicates `pkb.core` selection logic in the transport.

| ID | Rule | Source | Sev | Test assertion (API key?) |
|----|------|--------|-----|---------------------------|
| PK-1 | A **Research Pack** is breadth-first: `topic.md`, the relevant subtrees of root `tags.md`, the `summary.md` files of the relevant topics, plus any notes tagged `status.conflict-review` touching the research area. It contains **no `index.md`** unless the caller explicitly asks. | README Part 4 L681-687 | error | Golden test over the layer1 §4.1 fixture: the ordered path list matches, no `index.md`; `include_index=True` adds them. **no key** |
| PK-2 | An **Implementation Pack** is depth-first: the full `index.md` of the selected topic, the detailed `references/<src>/<src>.md` files, and the relevant solution notes — with **`notes/summary.md` always first**, because human rules have the highest priority for decisions. | README Part 4 L688-690, §1.3 L99 | error | `pack.entries[0].path == "notes/summary.md"`. **no key** |
| PK-3 | Pack membership is computed from Layer 1's derived surface — `files_with_tag`, `build_tag_tree`, the generated indexes — never from a second tree walk or a Layer 2 cache of the tree. | layer1 TG-10, TG-11, decision C | error | grep: no `os.walk`/`rglob`/`glob` over `kb_root` in `pkb.agents`; patching `scan` changes the pack. **no key** |
| PK-4 | Pack assembly is **read-only**: it writes nothing, tags nothing, bumps no timestamp, produces no `ScanRequest`. | README Part 4; layer1 MA-3 | error | Hash the tree before and after building both pack kinds: byte-identical, empty flush report. **no key** |
| PK-5 | A pack whose scope contains a `status.conflict-review` file carries an **escalation**, and an external (MCP) caller touching one receives the escalation shape instead of an answer. Any agent encountering such a file affecting its task pauses and escalates before proceeding. | README Part 4 L692-695; arch §6 L300-303 | error | `pack.escalation` is populated with the file and its `review_note`, via `files_with_tag(snapshot, "status.conflict-review")`. **no key** |
| PK-6 | For a query-shaped request the Librarian selects which topics contribute and the experts assemble their parts, which the Librarian merges. `pkb_implementation_pack(topic)` reaches exactly one expert. | README Part 4 L677-681; arch §6 L297-299 | warning | Wiring assertion. Topic-selection quality is **live**. |

---

## 2. Where the architecture doc is wrong about the harness

Every row below was **executed** against the pinned `deepagents 0.7.5` / `langchain 1.3.14` /
`langgraph 1.2.10` in this repo's `.venv`, not read. This section is why the grounding pass ran.

| # | Doc says | Installed API actually does | Corrected approach |
|---|----------|-----------------------------|--------------------|
| **D-1** | arch §7 L348-350: *"The flush runs on both success and failure."* | **False as written.** `after_agent` is a graph node on the normal exit edge (`factory.py:1589`); any exception aborts the superstep and it never executes. Verified across four failure shapes — model raises after a write, middleware raises in `wrap_tool_call`, async `ainvoke` raises — the file was on disk and `after_agent` fired in none of them. | `after_agent` flushes on the happy path; `pkb.agents.runtime` wraps **every** graph execution in `try/finally` and, if `after_agent` did not fire, reads `aget_state(cfg).values["kb_touched"]` (checkpointed and present on the error path, `next=('model',)`) and calls the same `flush`, then clears the key. `before_agent` resets at entry. Result: exactly one flush per run on both paths. (MW-26, MW-27, MW-28) |
| **D-2** | arch §7: validation is *"unskippable because it sits below the agent's decision-making."* | **The auto-added `general-purpose` subagent skips custom middleware entirely.** `graph.py:776`: `_gp_inheritable = [m for m in middleware if m.name in _gp_original_name_to_index]` — only middleware colliding with a default GP slot survives. Ours never collide. Verified: a parent whose guard blocks every `write_file` saw only `['task']` and `/kb/x.md` was written. It **does** inherit `permissions` and `interrupt_on`, so I3 holds and the gates hold — but validation and the flush do not. **Additional finding beyond the grounding pass: the GP subagent is auto-added to _every_ deep agent, including one that declares no `subagents` at all** — so this hole is on every expert graph, not only the Librarian. | Supply an explicit `SubAgent(name="general-purpose", …, middleware=[KbValidationMiddleware, KbMaintenanceMiddleware])` in `subagents=` on **every** graph — an explicit spec with that name suppresses the auto-add and `_apply_custom_middleware` then applies ours (verified: `Guard saw: ['task','write_file']`, `ON DISK: []`). A declarative `SubAgent` inherits parent `permissions` when it sets none (`graph.py:664`), so I3 still holds. Rejected: `register_harness_profile(key, HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))` — the registry is keyed by **model id** and process-global, and registrations merge additively onto built-in profiles. (EX-11) |
| **D-3** | arch §7 L317: intercepts writes *"routing to the KB"*. | The `file_path` in `request.tool_call["args"]` is the **raw model string**; deepagents normalizes only inside the tool body via `backends/utils.validate_path`. Verified: `write_file(file_path="kb/Cooking/notes/b.md")` reached `wrap_tool_call` verbatim, `raw.startswith("/kb/")` was `False`, and the file landed in the KB. | Call `validate_path` in the middleware **before** any `/kb/` test — `to_kb_relative` (RT-9). Permissions are unaffected (they run after normalization), so I3 is airtight where a naive prefix test is not. |
| **D-4** | arch §7 L317: `wrap_tool_call` intercepts `edit_file`. | `edit_file` carries **no content** — args are `file_path, old_string, new_string, replace_all`. `validate_content` needs the post-edit text. | Read current bytes and apply `deepagents.backends.utils.perform_string_replacement(...)` — the same function `FilesystemBackend.edit` uses, so the simulation cannot diverge — then validate the result; a `str` return is deepagents' own error and is forwarded. (MW-10) |
| **D-5** | arch I3's three globs `["/kb/**/index.md", "/kb/index.md", "/kb/tags.md"]`. | wcmatch `GLOBSTAR` matches **zero** directories, so `/kb/**/index.md` already covers `/kb/index.md` — one glob is redundant. Separately, `/kb/<topic>/tags.md` is **unprotected**: a scripted `write_file("/kb/Cooking/tags.md")` was allowed and landed on disk. | `DERIVED_DENY_GLOBS = ["/kb/**/index.md", "/kb/**/tags.md"]`, built from and equivalence-tested against `pkb.core.is_derived_name`, with the second glob documented as deliberately wider (it closes the gap Layer 1 §7.4/C14/VA-27 recorded). (RT-11, RT-12) |
| **D-6** | arch §4 L175: *"A thread is `(agent_id, thread_id)`"*, and §4 L177-181: delegated work gets a **different thread**. | The checkpointer keys on `thread_id` **alone** — the Librarian graph reading `thread_id="t-cook"` saw the Cooking expert's four messages verbatim. `checkpoint_ns` is **not** available as a second dimension: passing `checkpoint_ns="agent:a1"` makes `get_state` raise `ValueError: Subgraph … not found`. Delegated runs are invoked with only `{"configurable": {"ls_agent_type": "subagent"}}` and inherit the ambient parent config, so they checkpoint under the **Librarian's** `thread_id` in a nested `checkpoint_ns` of the form `tools:<uuid>` (verified: rows `('T-lib2','')` and `('T-lib2','tools:840894cf-…')`). | Thread ids must be **globally unique** (UUIDs), and the daemon's `threads` table is the only agent_id ↔ thread_id mapping. arch §4's *consequences* still hold — a direct expert conversation is a different conversation, and "continue what the Librarian was doing" resumes the Librarian's thread — but the mechanism sentence needs a one-line amendment, because Layer 3 will build a table against it and look for rows that never exist. Also: `adelete_thread(tid)` removes the nested namespaces too. (LB-9, RT-37, RT-48) |
| **D-7** | arch §8: a *"single global KB write lock"* around the flush. | Delegation produces **two** `after_agent` flushes (verified: both graphs fired). They are sequential — the subgraph completes inside the `task` tool before the parent's exit chain runs — but the expert's touched-path state **leaks up** into the parent, because `task` returns `Command(update={**subagent_state, …})` for every key outside `_EXCLUDED_STATE_KEYS` and `private_state_keys`. | Mark the touched-path key `PrivateStateAttr` so it is stripped in both directions (verified `subagents.py:252-256`, `:484`), making the parent's flush a genuine no-op. Make the lock reentrancy-safe by construction (an `asyncio.Lock` plus a per-`asyncio.Task` depth counter) — `create_topic` also takes it from inside a tool call, and fifteen lines removes the ambiguity. Hold it **only** around `flush`/`scaffold`, never across a model call or an interrupt. (MW-5, RT-51..RT-53, LB-11) |
| **D-8** | arch §9: *"middleware against a fake chat model."* | **Every stock langchain-core fake lacks `bind_tools`** — `GenericFakeChatModel`, `FakeMessagesListChatModel`, `FakeListChatModel`, `ParrotFakeChatModel` all inherit `BaseChatModel.bind_tools`, which raises `NotImplementedError`, and `create_agent` always calls it. The stock fakes cannot drive a deep agent at all. | Ship `ScriptedChatModel` as a fixture in `tests/agents/conftest.py` (§7). |
| **D-9** | — (not in the arch doc) | **Skills load once per thread and are checkpointed.** `SkillsMiddleware.before_agent` returns early when `skills_metadata` is in state. Verified: added a `research` skill, re-ran the same `thread_id`, still saw the old three. | Registry invalidation cannot fix this — the staleness lives in the checkpoint. Document it (RG-18); a fresh skill set needs a new thread or the state key cleared. |
| **D-10** | README Part 3: *"Default skills ship with the implementation."* | Works cleanly as a **second `FilesystemBackend` route** (`/skills/` → the package directory) placed ahead of `/kb/skills/` in the `skills=` list; verified that override resolution is last-wins by skill name across four sources with whole-record replacement. | SK-3 + `adopt_skill` (SK-4). No seeding into the human's tree. |
| **D-11** | arch §4 L149-151: `memory=[topic.md, notes/summary.md]`. | **Do not use it.** `MemoryMiddleware`'s injected `MEMORY_SYSTEM_PROMPT` reads, verbatim: *"As you learn from your interactions with the user, you can save new knowledge by calling the `edit_file` tool … To persist new knowledge, call `edit_file` to update memory promptly."* Those two files are exactly the human-approval surfaces README §1.6 says the AI never finalizes on its own. It also caches contents in checkpointed state (`if "memory_contents" in state: return None`), so a long-lived thread never sees a human-approved edit. | `KbBreadthMiddleware.wrap_model_call` reads the topic's own `topic.md` and `notes/summary.md` fresh each model call and appends them to `request.system_message` via `request.override(...)` (verified: `ModelRequest` has `system_message` and `.override`). Arch §4's *intent* — breadth always in context, `index.md` on demand — is preserved; the kwarg is not. This is the third middleware arch §7 does not mention. (EX-6, EX-7) |
| **D-12** | arch §5 L219-220 references `astream_events(version="v3")`. | On langgraph 1.2.10 v3 is a **different protocol**: a coroutine that must be awaited before iteration, yielding JSON-RPC-style `{"type":"event","method":…,"params":…}` envelopes with no `event` key — not the `on_chat_model_stream` shape arch §5 assumes. `version="v2"` gives the familiar names but is noisier and slower. | `graph.astream(input, cfg, stream_mode=["updates","messages"], subgraphs=True)`. `subgraphs=True` is **required** to see a delegated expert's messages (namespace `('tools:<uuid>',)`); without it arch §5's `subagent.*` events and delegated token streaming are impossible. (RT-43) |
| **D-13** | arch §7 L326-329: derived files *"are unwritable by agents (I3), so in practice the validator never sees them."* | Permissions are enforced **inside the tool body**, after every `wrap_tool_call` middleware. The validator does see the call; the denial arrives afterwards. | The validator early-returns on `is_derived_name` paths and defers to I3, so a derived write produces exactly one ToolMessage — the permission denial — and zero validation findings. (MW-11) |
| **D-14** | arch §7/§8 imply the flush covers the abandoned-approval case. | `after_agent` does not run on the **interrupted** turn either; it runs once on resume. A thread abandoned at an interrupt leaves the tree unflushed for as long as the human takes. | Combined with D-1, run one `pkb.core.regenerate_all` at daemon startup. Idempotent and byte-deterministic, so a clean tree costs one walk. (RT-7, MW-29) |
| **D-15** | arch §8: *"Two concurrent runs on the same thread return 409."* | LangGraph OSS has **no multitask strategy** (that is a Platform feature). Verified: `asyncio.gather(run('D'), run('D'))` returned two successes with interleaved writes. | Layer 2 keeps a per-`(agent_id, thread_id)` active-run registry and raises `ThreadBusyError`; Layer 3 maps it to 409. (RT-45) |
| **D-16** | — (not in the arch doc) | **Sending a new user message to a thread with a pending interrupt silently discards the interrupt** and runs the turn as if the tool call never existed. Verified: the gated write was never performed and the interrupt vanished. | `run()` refuses with `ApprovalPendingError` when `aget_state(cfg).interrupts` is non-empty. (RT-39) |
| **D-17** | arch §6 describes the approval modal as if approval precedes the check. | HITL interrupts fire in `after_model`, and `HumanInTheLoopMiddleware` is appended **last** so its `after_model` runs **first** in the reverse-ordered chain. `KbValidationMiddleware` lives in `wrap_tool_call`, which necessarily runs **after** the human has approved. A human can therefore approve content the validator then refuses, consuming one of the three attempts. | Run `pkb.core.validate_content` inside the gate's description factory and label a failing draft in the approval text, so the human can reject or edit instead of round-tripping. (RT-35) |
| **D-18** | arch §4 L162: the registry *"scans `*/topic.md`"*. | Depth-1 shorthand. Discovery is recursive and sub-topics are addressable agents — already recorded in Layer 1 §7.4/C1. | `pkb.core.scan.scan(kb_root).topics`, one call, no tree walk in Layer 2. (RG-2) |
| **D-19** | arch §5's `PkbService` Protocol includes `list_threads`, `create_thread`, thread titles, `origin_channel`, `pending_interrupt_id`. | The checkpointer cannot answer any of them — `alist(None)` returns bare `CheckpointTuple`s. | Those are Layer 3 composing Layer 2's surface with its own `threads` table; Layer 2 must not grow a listing API. (RT-49, §5) |
| **D-20** | — (housekeeping) | Invariant **I2 is not currently enforced**: the existing importlinter `layers` contract permits a higher layer to import a lower one, so `pkb.server` importing `pkb.agents` (and transitively `deepagents`) passes today. | Add a `forbidden` contract when `pkb.agents` lands: `pkb.server`, `pkb.tui`, `pkb.clients` must not import `deepagents`, `langgraph`, `langchain`, `langchain_core`. Add `pkb.contracts` to the layers list below `pkb.agents`. (§5) |

---

## 3. Contradictions between README and the architecture spec

| # | Contradiction | Recommended resolution | Why |
|---|---------------|------------------------|-----|
| **C1** | **Does a sub-topic have its own agent?** README §1.8 rule 5 / §2.3 L513-514: *"a sub-topic without its own `expert.md` is served by its parent topic's expert."* arch §4 L149-152: *"Every topic gets its own compiled graph"* with `memory=[topic.md, notes/summary.md]` — and a sub-topic has its own. | **Every topic root is its own agent** (EX-2). `resolve_expert` selects only the *prompt source*; a sub-topic without `expert.md` runs the ancestor's persona over its own scope, breadth files, skills chain and threads. README rule 5 is read as being about *which expertise* serves the sub-topic, not about how many graphs exist. | It is the only reading consistent with arch §4's per-topic breadth list, with Layer 1's per-topic `*(custom expert)*` marker (GE-13), and with Q9's rationale that the Librarian cannot route to an agent it cannot see. The alternative makes the root catalog list ids that are not addressable. |
| **C2** | **`expert.md`: replacement or layer?** README §2.3 L511-512 reads literally as full replacement (*"if `expert.md` exists, use it; otherwise instantiate the PKB template"*); §2.3 L516-523 describes the expert as two layers and §2.4 L568-573 says an overload *"never weakens the general standards."* | **Layered** (EX-4): code always prepends `prompts/standards.md`; `expert.md` supplies the domain layer. | Full replacement silently drops the *prompt-level* standards — approval-gate etiquette, tag proposal, conflict escalation, voice. The *mechanical* standards survive either way (I3 + middleware + gates), which is exactly why the prompt-level ones must be protected in code rather than trusted to the file. |
| **C3** | **`memory=` vs "never finalizes."** arch §4 prescribes `memory=[topic.md, notes/summary.md]`; README §1.6 L267 / §1.8 rule 3 forbid the AI finalizing those files. | **Drop `memory=`; use `KbBreadthMiddleware`** (D-11, EX-6/EX-7). | The harness's memory prompt actively instructs `edit_file` on those exact files. The arch doc's intent is right and is preserved; the kwarg is the wrong mechanism on this pin. |
| **C4** | **Flush on failure.** arch §7 asserts it; the harness cannot deliver it from `after_agent`. | **`after_agent` + a `try/finally` in `pkb.agents.runtime`** (D-1, MW-26). | The guarantee is load-bearing (stale derived files are worse than the partial write), and the runtime is the only place where "every execution path" is structurally true. |
| **C5** | **I3 glob set.** arch I3's three globs vs Layer 1's PA-11/C14 split of *deny set* from *generated set*. | **Two globs derived from `is_derived_name`, plus the deliberate `/kb/**/tags.md` widening** (D-5, RT-11/RT-12). | Layer 1 deliberately keeps `is_derived_name` and `is_generated` different sets so `VA-27`'s `RESERVED_TOPIC_TAGS_FILE` stays reachable and stale `notes/x/index.md` stays flaggable. Widening the *deny list* rather than the *predicate* preserves both. |
| **C6** | **Reference depth files: draft or approved?** README §1.3 L97 makes them purely AI-generated with human curation *at the summary level*; §1.5 L222 defines `status.draft` as "awaiting human approval" and §1.3 footnote 1 makes AI drafts human-approved. | **Reference depth files land `status.approved` with no per-file gate; everything else the agent authors lands `status.draft`** (RT-27, RT-31, SK-13). | §1.3's collaboration rule is explicit that the human curates references at the summary level — and that summary *is* gated (RT-23). The alternative creates a permanent backlog of never-to-be-approved files after a twenty-PDF ingest and makes `status.draft` useless as a query. Flagged as Q4 because it is the most visible choice in the TUI. |
| **C7** | **Shipped skills: seeded or mounted?** README Part 3 step 1 says default skills ship with the implementation and the human rewrites them; step 4 says *"Nothing is seeded by hand."* | **Mounted at `/skills/` + explicit per-skill `adopt_skill`** (SK-3, SK-4, SK-5). | Seeding is wrong on upgrades: without version control (D6) a seeded copy the human never touched is indistinguishable from one they rewrote, so shipped improvements either overwrite the human's work or never arrive — and a deliberately deleted skill silently reappears. Mounting keeps upgrades automatic; `adopt_skill` gives the human a starting text and makes the fork a decision rather than an accident. |
| **C8** | **Does `voice` ship, and with what in it?** README Part 3 step 1's shipped list omits it; step 2 says `voice.md` is drafted from the human's writing; §2.4 says every draft is written in this voice — so something must exist from turn one. | **Ship an opinionated starter profile**, plus the rule for narrowing it per topic (SK-1, SK-16, §6.6). **Ruled by the human, 2026-08-07**, overriding this document's original recommendation. | The original reasoning — that a fabricated default would "shape every draft invisibly" — had it backwards. Without a profile the drafts still have a voice; it is just the model's own, chosen by nobody and attributable to nothing. A wrong default is visible in the first draft and gets corrected; an absent one is invisible and does not. Shipping a profile also makes the file's *shape* self-evident to the human who rewrites it, which a questionnaire does not. |
| **C10** | **Which skill drafts `topic.md`?** §1.9 Layer 2 scopes summarization to the two `summary.md` files; §1.9 topic creation step 3 has the expert draft `topic.md`; §2.4 lists `topic.md` among the drafts written in the human's voice. | **Extend `summarization` to all three breadth files**, with a `topic.md` subsection (§6.1). | All three are the same artifact kind — a compact breadth approval surface with the same propose/review/approve loop. A fourth skill duplicates the procedure; a prompt-resident version escapes the human's ability to rewrite it. Note the `topic.md` specialization is load-bearing: its `description` feeds the root catalog. |
| **C11** | **Tag proposal: prompt or mechanism?** README §1.5 L189-191 states an absolute (*"Do not create ad-hoc tags"*); Layer 1 deliberately accepts unseen tags and keeps no allowlist (TG-9/VA-40); §1.9 makes it a *skill*. | **Both** — a mechanical gate (RT-25) plus the `tag-proposal` skill shaping the dialog. | Arch §7's stated premise is that an unskippable check beats an instruction, and this is the one governance rule the README states absolutely with **no** mechanical backing anywhere in Layer 1. The gate must fire on genuinely new tags only, or every first file in a new topic interrupts three times. |
| **C12** | **Who owns the conflict-scan worker?** arch §7 L354-357 puts the dequeue loop *"in the daemon"* (Layer 3), but the thing it runs is a deepagents graph, which I2 forbids Layer 3 from touching. | `pkb.agents.run_scan(request)` owns the run; **Layer 3 owns only the timer/dequeue loop**, calling through `PkbService` (RT-58, §5). | Anything else either breaks I2 outright or puts a background-task lifecycle into a layer whose suite is supposed to run against a fake chat model with no scheduler. |
| **C13** | **`AgentInfo` ownership.** arch §5 names `AgentInfo` in `pkb.service` (Layer 3) but it is produced by `pkb.agents.registry` — which would make `pkb.agents` import `pkb.service` while `pkb.service` imports `pkb.agents`. | The registry owns `AgentDescriptor`, which lives in the **leaf seam module `pkb/contracts.py`** (RG-14, §5). | Keeps the import direction one-way and makes the no-harness-imports property a structural fact rather than a discipline. |

---

## 4. Open questions for the human

Ranked by blast radius. **Every one has a default already encoded in §1**, so implementation is not blocked.

| # | Question | Options | Recommended default | Blast radius if changed later |
|---|----------|---------|---------------------|-------------------------------|
| **Q1** | **Where do the shipped skills physically live** — seeded into `<kb>/skills/` on first run, mounted from the package and layered under the KB's own, or both? (C7) | (a) seed-if-absent; (b) mount `/skills/` read-only ahead of `/kb/skills/`; (c) (b) plus explicit per-skill `adopt_skill`. | **(c)** (SK-3/SK-4/SK-5). Keeps automatic upgrades for untouched skills, makes a human rewrite exactly the same act as a topic overload, and gives the human a starting text. The fork notice must appear **at adopt time and inside the copied file** — the CLI output is long gone by the time they open it. | Changes bootstrapping, the backend route list, the skill-source order, and the upgrade story. Highest-leverage decision in this document. **Confirm before writing the skills.** |
| **Q2** | **Does `expert.md` replace the whole prompt, or only the domain layer beneath a fixed PKB-standards preamble?** (C2) | (a) full replacement; (b) layered preamble + domain layer; (c) `expert.md` appended to the full default template. | **(b)** (EX-4). Mechanical enforcement holds either way, but the prompt-level dialog rules — approval etiquette, tag proposal, conflict escalation, voice — would be silently lost under (a). | Fixes the shape of `prompts/standards.md` and `prompts/expert_template.md`. **Confirm before writing the prompts.** |
| **Q3** | **Is an expert's *write* access confined to its own topic subtree, or is there one KB-wide filesystem view?** | (a) KB-wide read + write (as arch §4 reads today); (b) KB-wide read, write confined to `/kb/<topic>/**`; (c) confine both. | **(b)** (RT-15), with the Librarian read-only (RT-16). It makes README §1.8 rule 4 mechanical rather than prompt-level and stops a mis-routed ingestion writing into a neighbour's tree. Ordering trap: permissions are first-match-wins with default allow, so the derived deny and the `/kb/**` write deny must sit **around** the topic-scoped allow in the right order. Reject (c) — breadth-first research needs KB-wide reads. | Changes `permissions.py` and one test matrix. Reversible in an afternoon; the *behavioural* consequence (an expert must hand a mis-routed item back rather than filing it) is the part worth a human's eye. |
| **Q4** | **What `status.*` does an AI-authored reference depth file land with?** (C6) | (a) everything the agent authors lands `status.draft`; (b) `status.draft` for human-curated classes, `status.approved` for reference depth files; (c) the skill decides per case. | **(b)** (RT-27, RT-31). Follows §1.3's collaboration rule exactly and avoids a permanent backlog. Reject (c): status is a single-value state machine (VA-9/Q11) and per-case judgment makes `status.draft` meaningless as a query. | Decides what a fresh ingestion of twenty PDFs looks like in the human's TUI. One constant plus one gate row. |
| **Q5** | **Approve the gate table** (RT-23 … RT-31): which exact paths and content changes require a human decision? | (a) as specified; (b) content-based only (interrupt on any body change to an existing authored file); (c) interrupt on every `/kb/**` write. | **(a) as specified.** It maps one-to-one onto README §1.3's Built-By column; the one place path-based is insufficient (an unattended factual change to an existing note) is covered by RT-24, whose complement is the conflict-flag exemption RT-26. Reject (c): it would gate reference depth files and the conflict flag, both of which the README lets the AI write. | Defines the daily feel of the system more than any other table here. One module, but the human notices every row. |
| **Q6** | **Where does per-agent model configuration live?** | (a) a process config object (env + optional TOML) injected into `AgentRegistry`: `default_model` + `{agent_id: model}`; (b) a `model:` key in `topic.md` frontmatter; (c) a non-indexed config file inside the KB tree; (d) hardcoded default only. | **(a)** (RG-21). The KB holds knowledge, not runtime configuration — a `model:` key would be an `UNKNOWN_FIELD` warning (VA-32) and would put deployment config under the human-approval workflow. Note the arch default `anthropic:claude-sonnet-5` differs from deepagents' own fallback (`claude-sonnet-4-6`), one more reason never to pass `model=None`. | One constructor signature. |
| **Q7** | **How is the `general-purpose` hole closed?** (D-2) | (a) explicit `SubAgent(name="general-purpose", middleware=[…])` per graph; (b) process-global `HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False))`; (c) leave it — I3 and the gates still reach it. | **(a)** (EX-11). Per-graph, verified, and touches no global registry keyed by model id. Reject (c): the PKB has no use for a generic file-shuffling subagent, and it makes §7's validation contract silently optional on one code path. If you would rather it not exist at all, (b) is one line — but it is process-global and merges onto built-in profiles. | Two lines in each factory plus one acceptance test (which fails today). |
| **Q8** | **`delete`: excluded, or gated?** | (a) gate every `/kb/**` delete with `["approve","reject"]`; (b) remove the tool via `HarnessProfile(excluded_tools=["delete"])`; (c) rely on the prompt. | **(a)** (RT-30). Layer 1 never deletes and there is no undo, but moving a note into a new sub-topic is a legitimate, README-sanctioned act (§1.9 topic creation) and is a write plus a delete. A gate keeps it possible and visible; (b) is process-global (same objection as Q7-b); (c) is the prompt-instead-of-mechanism pattern I3 exists to reject. Present the full file list up front so the human sees the whole plan before the first move. | One gate row. |
| **Q9** | **Does a conflict-scan run appear as a thread the human can see?** | (a) hidden behind a reserved `scan:` id, findings surface only as `status.conflict-review` files and TUI pending items; (b) visible as ordinary threads on the expert; (c) visible but collapsed into a "maintenance" list. | **(a)** (RT-58). README §1.7 is explicit that the note content is the true state of knowledge and that no conflict registry exists; a scan thread is machine bookkeeping. Returning the reserved id keeps (c) available later without changing the run path. | One id prefix; Layer 3 filtering. |
| **Q10** | **Do packs land in Layer 2 now, later, or in Layer 3?** (C12-adjacent) | (a) `pkb/agents/packs.py`, built after the six step-2 modules; (b) with step 2; (c) in Layer 3 next to the MCP tools. | **(a)** (`PK` group, §8). A pack is deterministic selection over Layer 1's derived surface — making it prose loses reproducibility and makes "notes/summary.md always first" untestable; (c) duplicates `pkb.core` selection logic in the transport. | Adds one module to arch §3's listing. |
| **Q11** | **`durability` for user-facing runs?** | (a) `"sync"` everywhere; (b) `"sync"` only for runs carrying an approval gate; (c) the langgraph default `"async"`. | **(a) for v1.** Personal KB, human-latency turns, so the throughput cost is invisible — and it removes the entire class of "the daemon died and the pending approval vanished" bugs arch §8 L373 promises will not happen. Revisit only if a scan backlog makes it measurable. | One kwarg. |
| **Q12** | **What is the shared `store` for?** | (a) `AsyncSqliteStore` over the daemon's SQLite file; (b) `InMemoryStore`; (c) omit `store=` until a consumer exists. | **(a)** (RT-5). Nothing in v1 reads it — there is no `StoreBackend` and no long-term-memory middleware — but it is already installed, shares the daemon's file and lifecycle, and costs one line. It makes arch §4's sentence true instead of aspirational. | One line. |
| **Q13** | **Confidence threshold for tagging a detected conflict?** | (a) none — tag everything the expert classifies, report confidence in the dialog only; (b) a threshold constant below which the finding is mentioned but not tagged; (c) tag everything and rank the `Needs review` section by confidence. | **(a) for v1** (§6.2). The score has no persistent home (VA-30 rejects the frontmatter key, RT-59 rejects a store), so a threshold would be an invisible tuning knob the human cannot audit, and an untagged finding vanishes when the scan thread ends. Revisit if scans prove noisy — (b) is one constant away. | One constant. |

---

## 5. The Layer 2 → Layer 3 seam

`PkbService` (arch §5) must be implementable **without importing `deepagents`, `langgraph`, or
`langchain`** (I2). Because `pkb/agents/__init__.py` imports the runtime, `from pkb.agents.types
import …` would still pull the harness in. The seam therefore lives in a **leaf module with nothing
below it**: `pkb/contracts.py`, imported by `pkb.agents`, `pkb.service`, `pkb.server`, `pkb.tui` and
`pkb.clients` alike. The importlinter layers contract gains `pkb.contracts` below `pkb.agents`, and a
new forbidden contract enforces I2 (D-20).

**Test**: `python -c "import pkb.contracts, sys; assert not {'deepagents','langgraph','langchain'} & set(sys.modules)"`.

### 5.1 `pkb/contracts.py` — the types that cross

```python
# ---- identity & catalog -------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class AgentDescriptor:
    agent_id: str                 # "librarian" | "topic/cooking/grilling"  (opaque, may contain "/")
    title: str                    # topic.md title, folder name when degraded (GE-25)
    description: str              # topic.md description; the same string routing uses (RG-10)
    has_custom_expert: bool       # the *(custom expert)* marker (GE-13)
    model_id: str                 # resolved, never selected by a transport (RG-21)

# ---- streaming events (arch §5) ------------------------------------------------------
@dataclass(frozen=True, slots=True)
class MessageDelta:    run_id: str; agent_id: str; text: str
@dataclass(frozen=True, slots=True)
class MessageComplete: run_id: str; agent_id: str; text: str
@dataclass(frozen=True, slots=True)
class ToolStart:       run_id: str; agent_id: str; tool: str; summary: str
@dataclass(frozen=True, slots=True)
class ToolEnd:         run_id: str; agent_id: str; tool: str; summary: str; error: bool
@dataclass(frozen=True, slots=True)
class SubagentStart:   run_id: str; agent_id: str            # agent_id = the *delegate* (RT-44)
@dataclass(frozen=True, slots=True)
class SubagentEnd:     run_id: str; agent_id: str; status: str
@dataclass(frozen=True, slots=True)
class InterruptEvent:  run_id: str; request: "ApprovalRequest"   # deduped by interrupt id (RT-41)
@dataclass(frozen=True, slots=True)
class RunEnd:          run_id: str; final_text: str
@dataclass(frozen=True, slots=True)
class RunError:        run_id: str; message: str; retryable: bool

AgentEvent = (MessageDelta | MessageComplete | ToolStart | ToolEnd | SubagentStart
              | SubagentEnd | InterruptEvent | RunEnd | RunError)

# ---- approvals ----------------------------------------------------------------------
DecisionType = Literal["approve", "edit", "reject", "respond"]

@dataclass(frozen=True, slots=True)
class ActionView:
    tool: str                     # "write_file" | "edit_file" | "delete" | "create_topic" | ...
    args: Mapping[str, str]       # primitives only — never a LangChain object
    description: str              # rendered by gates.describe_write: path + diff (RT-34, RT-35)
    allowed_decisions: tuple[DecisionType, ...]   # server-side truth; clients may narrow their UI only
    reason: str                   # GateReason slug: "breadth-approval" | "new-tag" | ...

@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    interrupt_id: str
    agent_id: str
    thread_id: str
    actions: tuple[ActionView, ...]          # positionally aligned with the decisions list (RT-41)

@dataclass(frozen=True, slots=True)
class Decision:
    type: DecisionType
    message: str | None = None                       # reject / respond
    edited_args: Mapping[str, str] | None = None      # edit
    edited_tool: str | None = None                    # edit

def validate_decisions(request: ApprovalRequest,
                       decisions: Sequence[Decision]) -> None: ...   # RT-40; shared with pkb.clients.approval

@dataclass(frozen=True, slots=True)
class PendingProposal:            # propose_only mode (RT-42)
    proposal_id: str; agent_id: str; thread_id: str; action: ActionView; created_at: datetime

# ---- runs & scans --------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class MessageView: role: str; text: str; created_at: datetime | None

@dataclass(frozen=True, slots=True)
class RunHandle:   run_id: str; agent_id: str; thread_id: str

@dataclass(frozen=True, slots=True)
class ScanResult:  topic_id: str; thread_id: str; tagged_paths: tuple[str, ...]; summary: str

class ScanQueue(Protocol):        # RT-54 — the middleware depends on this, not on sqlite
    async def put(self, requests: Sequence[ScanRequest]) -> None: ...
    async def take(self, limit: int = 1) -> Sequence[ScanRequest]: ...
    async def done(self, topic_id: str) -> None: ...

# ---- typed errors Layer 3 maps to status codes --------------------------------------
class PkbAgentError(Exception): ...
class UnknownAgentError(PkbAgentError): ...      # -> 404   (RG-13)
class ThreadBusyError(PkbAgentError): ...        # -> 409   (RT-45)
class ApprovalPendingError(PkbAgentError): ...   # -> 409   (RT-39)
class StaleInterruptError(PkbAgentError): ...    # -> 409   (RT-40)
class InvalidDecisionError(PkbAgentError): ...   # -> 400   (RT-40)
```

`ScanRequest`, `Finding`, `Severity` and `FlushReport` are re-exported from `pkb.core` — they are
already harness-free and Layer 1 defines them so both layers share the type (C18).

### 5.2 What `pkb.agents` exposes

```python
class PkbRuntime:
    @classmethod
    def open(cls, kb_root: Path, db_path: Path, *, config: RuntimeConfig) -> AsyncContextManager[Self]: ...
    async def aclose(self) -> None: ...

    # catalog
    def list_agents(self) -> list[AgentDescriptor]: ...

    # runs — async only (RT-3); both ids always explicit (RT-36)
    def run(self, agent_id: str, thread_id: str, message: str, *,
            approval_mode: Literal["interactive","propose_only"] = "interactive",
            ) -> AsyncIterator[AgentEvent]: ...
    def resume(self, agent_id: str, thread_id: str,
               decisions: Sequence[Decision]) -> AsyncIterator[AgentEvent]: ...
    async def cancel(self, run_id: str) -> None: ...

    # approvals & history
    async def pending_approval(self, agent_id: str, thread_id: str) -> ApprovalRequest | None: ...
    async def history(self, agent_id: str, thread_id: str) -> list[MessageView]: ...

    # threads (checkpointer only — no titles, no listing: RT-49)
    async def delete_thread(self, thread_id: str) -> None: ...

    # maintenance
    async def request_scan(self, request: ScanRequest) -> ScanResult: ...
    async def regenerate(self) -> FlushReport: ...        # RT-7, also the on-demand "rebuild" path
```

`PkbService` (build-order step 3) is exactly this surface plus a `threads` SQL table:
`create_thread`, `list_threads`, `get_thread`, titles, `origin_channel` and `pending_interrupt_id` are
Layer 3's, because the checkpointer cannot answer them (D-19). A stub `PkbService` implementing arch
§5's Protocol must be writable over this surface plus one table with **zero** deepagents imports —
that is the acceptance test for the seam.

---

## 6. The shipped skills

Eight directories of package data at `pkb/agents/skills/<name>/SKILL.md`, mounted read-only at
`/skills/` and adopted into the KB per-skill on request (Q1 → SK-3/SK-4/SK-5). Names are singular
throughout and closest to README's own nouns; they become directory names in the human's KB and are
effectively permanent once adopted, since renaming a skill after adoption breaks the override link
silently.

Every body ends with the shared draft footer (SK-12) and names its approval gate (SK-11). Bodies are
plain prose, address files by role relative to the topic root (SK-9), and cite Layer 1 rule ids only
where SK-14 permits restatement.

### 6.1 `summarization`

```yaml
---
name: summarization
description: Use when drafting or revising a topic's breadth files — topic.md, notes/summary.md, or references/summary.md — or when new material should change one of them. Produces a proposal for human review, never a finished file.
---
```

**Outline.** What breadth files are for and why length growth is a defect (they manage the *human's*
context window; distill and replace, never append; soft length target). Three targets, one procedure:
read the current file, read what changed, propose a revision. A mandatory **connections** step —
suggest links to references, to related topics, and to existing notes, rather than restating note
bodies. A `topic.md` subsection (C10): its `description` feeds the root catalog, so it must be a
single meaningful line, not a restatement of the title. `notes/summary.md` holds distilled rules and
notable solutions and is the highest priority for decisions. Terminates at the breadth-approval gate
(RT-23) — the AI never writes the approved version unprompted.

### 6.2 `conflict-detection`

```yaml
---
name: conflict-detection
description: Use when running a scheduled or on-demand conflict scan for a topic, or when new material appears to contradict existing knowledge. Compares notes, summaries, and references, tags findings for human review, and never resolves a conflict itself.
---
```

**Outline.** First constraint, stated verbatim: **human content wins over static knowledge, always** —
never resolve in the reference's favour, never rewrite a note to match a reference, never suppress a
finding because a reference looks authoritative. Three comparison axes: `notes/summary.md` vs
`references/summary.md`; individual notes vs references; notes vs notes — semantic analysis informed
by domain knowledge (recognizing two statements both true under different conditions), not string
matching. Classification into `contradiction` / `nuance` / `outdated` with a confidence, **presented
in the dialog only** — never written to a file (Layer 1 rejects `conflict_type`, `confidence`,
`resolution` outright). On a detected conflict, exactly three acts on the human content file: add
`status.conflict-review`, add a short `review_note`, leave the body byte-unchanged; the reference is
neither tagged nor edited. Two-note branch: tag **both**, present both, never pick a winner. Resolution
(only on the human's instruction): flip the tag back to `status.approved`, remove `review_note`, set
`last_reviewed` — nothing else changes and no record that a conflict occurred survives. Terminates at
the conflict-resolution gate (RT-26); tagging itself is deliberately ungated.

### 6.3 `tag-proposal`

```yaml
---
name: tag-proposal
description: Use before filing content that would introduce a hierarchical tag not already in use anywhere in the knowledge base. Proposes the tag to the human with its rationale and place in the tree, and waits for approval.
---
```

**Outline.** Propose *before* filing — the file does not land until the tag is approved. The four
mechanical constraints up front so a proposal is valid on the first attempt rather than learned
through validation errors against the 3-attempt bound (SK-14): four namespaces; depth ≤ 4 segments
**inclusive of the namespace**; lowercase kebab-case segments; a nested tag implies its parent. Only
`topic.*` and `domain.*` are proposable — `type.*` (four values) and `status.*` (three) are closed
vocabularies the skill must never extend. How to place a proposed tag in the existing tree and how to
justify it against the nearest existing tag. Never edit `tags.md` or any `index.md`: the registry is
purely derived and picks a tag up mechanically once a file uses it — there is no register step.
Terminates at the new-tag gate (RT-25), which fires mechanically whether or not this skill ran.

### 6.4 `ingestion-classification`

```yaml
---
name: ingestion-classification
description: Use when filing inbound content into a topic — a document, an article, a message from the human, or project feedback. Decides reference vs note vs solution, drafts the file with complete metadata, and files it where the classification implies.
---
```

**Outline.** A decision tree: **reference** → `references/<source>/<source>.md`; **note** (observation
or opinion) → `notes/<title>.md`, or `notes/<title>/<title>.md` when it carries media; **solution** →
a note tagged `type.solution`, under `notes/` or a human-approved extension folder. One classification
fixes three things at once — `source_type`, the single `type.*` tag, and the location — so getting it
right once avoids three findings. The canonical frontmatter template (the one place the seven fields
are restated, SK-14/PR-4), with an emphasis on `description`: single-line, non-empty, and what
deterministic index generation extracts, so a vacuous one degrades both indexes. Media: binaries under
`<item>/media/`, and the item's `.md` carries a textual description of every embedded medium so agents
read text instead of parsing binaries. `related_topics` in the unprefixed dotted form, filled whenever
a cross-topic relationship exists — it is the *only* source Layer 1 aggregates into the registry's
cross-topic mappings; shared `domain.*` tags, folder proximity and body links produce nothing. A
solution note lives in **exactly one topic** — the most relevant — with no copies; cross-topic reach
is tags, `related_topics`, and Librarian routing. When filing from the human's own input: restructure,
clarify, tag — **do not add, remove, or alter factual content**, and show the final text before
writing. Agent-authored content lands `status.draft`; reference depth files land `status.approved`
without a per-file dialog (Q4). Terminates at the show-before-write step and, for anything gated, at
the relevant gate.

### 6.5 `sub-topic-proposal`

```yaml
---
name: sub-topic-proposal
description: Use when a topic has grown large enough that its breadth files can no longer summarize it honestly. Proposes a split with the evidence and a per-file assignment, and creates nothing until the human approves.
---
```

**Outline.** Evidence to gather before proposing (note count, distinct tag clusters, whether
`notes/summary.md` has become a list rather than a distillation). The proposed sub-topic name, its
description, and the **file assignment**, presented in full up front. The depth budget: never propose
nesting that would exceed four `topic.*` levels — every file inside such a topic would be unable to
carry a location-consistent tag, and the scaffolder refuses it outright. Moving existing notes is a
**separate act, approved per file**, and the skill says plainly that there is **no undo**: a move is a
write plus a delete, and the KB has no version control. Terminates at the topic-creation gate; the
scaffolder is called only after approval.

### 6.6 `voice`

```yaml
---
name: voice
description: Use when drafting any prose the human will read as their own — curated notes, summaries, topic.md. Holds the voice profile every draft is written in, and the rule for narrowing it per topic when one subject wants a different register from another.
---
```

**Outline.** Scoped to **prose bodies only** — it does not govern frontmatter values (`description`
stays single-line and factual), rendered derived files, or tool output.

The shipped version **is a voice profile**, not a procedure for building one (C8, revised — see the
ruling below). It carries an opinionated starter profile: lead with the claim, short sentences and
ordinary words, concrete over abstract, first person for experience and plain statement for fact,
hedge only where the uncertainty is real, no throat-clearing and no summary paragraph. Enough for an
agent to write from on day one, stated as applicable rules rather than adjectives.

**Per-topic voice (SK-16).** Voice is overloadable per topic like any skill, and the skill body says
so explicitly, because this is the overload a human is most likely to want: cooking notes are
personal and narrative, trading notes are adversarial and want dates, sizes and the reasoning at the
time. Resolution is `pkb.core.resolve_skills`: nearest copy wins, a topic without one falls back to
the root, a sub-topic may narrow its parent again. The body must state that a topic copy **replaces**
the root profile for that topic rather than merging with it — the resolver returns one path per skill
name, so a two-line topic voice silently drops every general rule with no error anywhere — and must
tell the agent to propose a topic voice from repeated evidence in the edits, never from a single note.

**Keeping it honest.** The most informative sample is a draft that comes back edited; a rule edited
out every time it is applied is wrong, not under-applied. Changes to the profile are proposed with
the evidence, never inferred silently in the background.

**Hard boundary.** Applying voice **never alters the factual content of a human-written note**; style
edits to human content are proposed, not applied.

### 6.7 `discovery`

```yaml
---
name: discovery
description: Use when running an idea-discovery or brainstorming session against knowledge-base content. Reads breadth-first, generates ideas with the human, and files nothing as a side effect.
---
```

**Outline.** Read order, breadth-first: `notes/summary.md` first (human rules have the highest
priority), then `topic.md` and `references/summary.md`, then depth files only on demand. How to run
the session: surface tensions and gaps rather than restating what is already written; connect across
topics using the cross-topic mappings. **Nothing produced in a discovery session is filed as a side
effect** — anything worth keeping re-enters through `ingestion-classification` and its gates. The body
ends with an explicit hand-off step, not a write instruction.

### 6.8 `research`

```yaml
---
name: research
description: Use when exploring a question breadth-first across the knowledge base to generate options for the human to choose between. Reads topic.md, tag subtrees, and summaries — not index.md — and escalates rather than proceeding on contested knowledge.
---
```

**Outline.** Sources: `topic.md`, the relevant subtrees of root `tags.md`, and the `summary.md` files.
**Does not read `index.md` files unless explicitly asked** — that is depth-first material. Includes
notes tagged `status.conflict-review` that touch the research area, and **pauses to escalate to the
human** when such a note affects the task, rather than proceeding on contested knowledge. Output
contract: an enumerated option set with trade-offs and an explicit request for direction — the skill
generates options, the human selects. Terminates at that selection request.

---

## 7. Test strategy

Arch §9's row for `pkb.agents` — *"middleware against a fake chat model; requires API key: no"* — is
achievable end to end, and was proven so in the grounding pass against the real `pkb.core` on both
`invoke()` and `ainvoke()`. The default suite must pass with `ANTHROPIC_API_KEY` unset and sockets
blocked; that is itself the assertion (SK-18).

### 7.1 The fake-model recipe

Every stock langchain-core fake lacks `bind_tools` and therefore cannot drive a deep agent (D-8).
Ship this in `tests/agents/conftest.py`:

```python
class ScriptedChatModel(BaseChatModel):
    script: list[Any]                      # AIMessage, or a zero-arg callable that may raise
    idx: int = 0
    calls: list[list[BaseMessage]] = []    # captures the exact prompt each turn

    def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        self.calls.append(list(messages))
        item = self.script[min(self.idx, len(self.script) - 1)]
        self.idx += 1
        msg = item() if callable(item) else item
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, *, tool_choice=None, **kw):   # required — create_agent always calls it
        return self

def call(name, args, id_):
    return {"name": name, "args": args, "id": id_, "type": "tool_call"}
```

Three rules the harness forces on every test that uses it:

1. **Unique tool-call ids, and always terminate the script.** A repeated id, or a script that sticks
   on a tool call, hits a langchain 1.3.14 routing bug: `_make_model_to_tools_edge` case 6 returns
   `"model"`, but `"model"` is only in `model_to_tools_destinations` when a `response_format` or an
   `after_model` middleware exists → `KeyError: 'model'`.
2. **Exercise both the sync and async hook** (MW-2). A sync-only `wrap_tool_call` under `ainvoke()`
   raises `NotImplementedError: Asynchronous implementation of awrap_tool_call is not available`.
3. **One model instance is shared with subagents**, so the script is consumed in global call order.
   Delegation tests script the parent and the delegate as one sequence.

`self.calls` is also how prompt-content assertions work — it is how the grounding pass verified that
skill bodies do *not* reach the system prompt while name+description+path do.

### 7.2 What is testable without a key

Everything in §1 except the rows marked **live**. Concretely:

| Area | Fixture | Headline assertions |
|------|---------|---------------------|
| **permissions** (`test_permissions.py`) | fixture KB + `ScriptedChatModel` | `is_derived_name` ⟹ deny for every walked path (RT-11); `/kb/Cooking/tags.md` denied (RT-12); reads succeed (RT-13); `delete` denied on derived and on a containing directory (RT-14); topic scoping (RT-15); Librarian read-only (RT-16); **hostile prompt still denied** (RT-19). |
| **validation middleware** (`test_validation_middleware.py`) | `tmp_path` KB, spy handler | handler not called + `status="error"` + target absent + `render_findings` substring (MW-13); warning-only write lands with advisory (MW-12); `kb/x.md` normalization (RT-9); edit simulated via `perform_string_replacement` (MW-10); derived path → exactly one ToolMessage, zero findings (MW-11); attempts 1-3 then escalation with the flush still firing (MW-14, MW-15). |
| **maintenance middleware** (`test_maintenance_middleware.py`) | `tmp_path` KB, in-memory `ScanQueue` | one `flush` per turn with all touched paths (MW-20); KB-relative paths are load-bearing (MW-21); empty-touched flush is a no-op with unchanged mtimes (MW-22); the report reaches the sink (MW-24); **model raises mid-run → derived files still regenerated** (MW-26/MW-27); success ⟹ exactly one `ScanRequest` per topic (MW-28); read-only KB → run still answers, report carries `DERIVED_WRITE_FAILED` (MW-25). |
| **registry** (`test_registry.py`) | 50-topic fixture | N+1 descriptors (RG-1); recursive discovery + no tree walk (RG-2); zero graphs at boot, one on first `get` (RG-4); 10 concurrent `get` → one build (RG-5); identity across both access paths (RG-6); `UnknownAgentError` (RG-13); eviction on rename and Librarian drop on invalidate (RG-16); nothing on disk changed (RG-19); signature audit (RG-20, RG-21). |
| **expert / librarian** (`test_expert.py`, `test_librarian.py`) | 3-level fixture with and without `expert.md` | prompt source flips with `resolve_expert` (EX-2); standards preamble survives a hostile `expert.md` (EX-4, EX-5); no `MemoryMiddleware`, breadth refreshes between turns of one thread (EX-6, EX-7); skill sources equal `resolve_skills` (EX-8, SK-16); **delegated `general-purpose` write is refused** (EX-11 — fails today); `subagents=` entries have exactly three keys (RG-7); zero expert graphs at Librarian compile (RG-8); backticked root-index ids are subagent keys (RG-9); empty-KB Librarian compiles (LB-6); `create_topic` interrupt → approve → six paths + new id (LB-7); two parallel `task` calls both land (LB-8); delegated interrupt resolves on the Librarian's thread (LB-10). |
| **gates** (`test_gates.py`) | table-driven, no graph | ~20 (tool, path, args) rows → expected `GateReason` (RT-21); `respond` never allowed on a write gate (RT-32); no entry is `False` (RT-33); diff rendered for an existing file, full content for a new one (RT-34). |
| **approvals & runs** (`test_runtime.py`) | `InMemorySaver` and a `tmp_path` `AsyncSqliteSaver` | all four decision shapes produce their documented effect; count mismatch and disallowed type raise typed errors before the graph (RT-40); stale id leaves the thread interrupted (RT-40); new message during a pending interrupt refuses (RT-39); two runs on one thread → `ThreadBusyError` (RT-45); one `run.error` and a resumable thread (RT-47); cross-process resume over the same SQLite file (RT-38). |
| **events** (`test_events.py`) | scripted delegation | every event is a frozen dataclass and `asdict` is JSON-serializable (RT-43); `astream_events(version="v3")` is still a coroutine (regression pin); one `subagent.start`/`end` naming the delegate (RT-44); one interrupt event despite two `__interrupt__` emissions (RT-41). |
| **shipped skills** (`test_shipped_skills.py`) | package data | the eight names; `name == dir.name` and the charset (SK-2); description ≤ 1024 with a trigger clause (SK-7); `validate_content` clean (SK-6); no `/kb/` in any body (SK-9); each names its approval gate (SK-11); the draft footer (SK-12); adoption is refuse-to-overwrite with a fork notice (SK-4, SK-5); after adopting all eight, `regenerate_all` output is byte-identical (SK-17). |
| **seam & invariants** (`test_contracts.py`, CI) | — | importing `pkb.contracts` loads no harness module; `lint-imports` fails on a deliberate `import deepagents` in a stub `pkb/server/app.py` (D-20); a stub `PkbService` over `PkbRuntime` + one table compiles with zero deepagents imports. |

### 7.3 What needs a live model (`-m live`, opt-in, deselected by default)

Only judgment, and only where a fake model cannot say anything:

- The Librarian actually fans out to ≥ 2 experts for a two-topic question and merges into **one**
  final message (LB-2, RT-4-equivalent).
- Whether it consults the `tags.md` mappings rather than guessing relatedness.
- Ingestion classification of genuinely ambiguous input; whether a `description` is meaningful rather
  than a restatement of the title (`description != title`, length ≥ ~30 chars).
- First-attempt validation pass rate for a fresh note — the number that tells you whether SK-14's
  restated set is the right size.
- Conflict detection quality: that all three axes are attempted, and that a `nuance` is not misread as
  a `contradiction`.
- One smoke test confirming nothing in deepagents introspects a `CompiledSubAgent` runnable (`.name`,
  `.get_input_schema`) before first invocation — the one assumption RG-8's lazy proxy rests on.
- Voice fidelity of a draft.

Everything else is mechanics, and mechanics run free.

---

## 8. Explicitly out of Layer 2

Do **not** build any of the following in `pkb.agents`. Each is listed with where it belongs.

**Already Layer 1 — cite it, never reimplement it**
- Frontmatter parsing, serialization and targeted field edits. `pkb.core.frontmatter`.
- Content validation of any kind: required fields, tag syntax/depth/vocabulary, naming, location
  consistency, media placement, reserved names, conflict-residue rejection. One `validate_content`
  call site (MW-9).
- Index, tag-registry and root-catalog generation; `updated` stamping; broken-link and orphan
  detection; the `## Maintenance flags` section. `pkb.core.flush` / `regenerate_all`.
- Topic and sub-topic scaffolding, including its depth refusal and never-overwrite behaviour.
- Slugification, agent-id ↔ path bijection, `sub-topics` elision, expert resolution, skill resolution,
  derived/generated predicates, link targets. `pkb.core.paths` (CX-8: no second implementation).
- The one tree walk. `pkb.core.scan.scan` — no `os.walk`, `rglob` or `glob` in `pkb.agents` (RG-2).
- `ScanRequest` construction. `pkb.core.build_scan_requests` (RT-57).

**Layer 3 — `pkb.service`, `pkb.server`, `pkb.tui`, `pkb.clients`**
- The `threads` table: thread creation, titles, listing, `origin_channel`, `pending_interrupt_id`
  (D-19, RT-49).
- The scan-queue **dequeue worker** and its timer. Layer 2 owns the queue table, the enqueue path and
  `run_scan`; the loop is Layer 3's (C12).
- HTTP routes, SSE encoding, the MCP mount and its four tools, Telegram, the approval diff **modal**
  (Layer 2 renders the diff *text*, RT-34; Layer 3 renders the UI).
- Mapping typed errors to status codes; `/health`; Telegram bot supervision; SSE disconnect handling;
  narrowing `allowed_decisions` for a phone keyboard (Layer 2 always publishes the full server-side
  set and validates against it, RT-32).
- Percent-encoding or path-segment escaping of agent ids — a transport concern; the registry takes and
  returns them verbatim (RG-12).

**Deferred within Layer 2 — build after the six step-2 modules**
- `packs.py` (the `PK` group, Q10).
- A CLI `eject`/`adopt` front end for `adopt_skill` — the function is step 2, the command is not.
- Clearing `skills_metadata` on a live thread to refresh skills mid-conversation (RG-18 documents the
  limitation; do not build the mechanism in v1).
- A ninth shipped skill ("interviewing the human to draw out experience"), README §2.4's "others as
  needed" (SK-1).
- Per-skill `allowed-tools` declarations (SK-6): verify enforcement semantics against the pin first.

**Never, at any layer, per the spec**
- A conflict registry, resolution log, loser marker, stored confidence, or any persistent record that
  a conflict occurred — in the tree **or** in Layer 2's SQLite. `last_reviewed` is the only trace
  (RT-59).
- Overwriting, moving, or deleting human content without an approved human decision (RT-24, RT-30).
- Any agent write to a derived file, by any path, including "just this once to fix it" (RT-11, RT-18).
- The AI resolving its own approval interrupt, or an "assume approved" fallback (RT-33); the single
  exception is the documented propose-only auto-reject, which writes nothing (RT-42).
- Shell access: no `LocalShellBackend`, no `SandboxBackendProtocol`, no code path that makes `execute`
  live (RT-20).
- Network I/O from `pkb.core`, and version control / undo / backups anywhere in the first draft (D6).
