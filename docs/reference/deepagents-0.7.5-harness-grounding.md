# deepagents 0.7.5 — harness grounding for Layer 2

**Date**: 2026-08-06
**Why this exists**: the Layer 2 rules rest on how the installed harness actually behaves, not on how
the architecture doc describes it. Every claim below was executed against the pinned
`deepagents 0.7.5` / `langchain 1.3.14` / `langgraph 1.2.10` in this repo's lockfile. Twenty
divergences from the architecture doc came out of this pass; the four that would have failed silently
are summarised at the top of the Layer 2 rules.

Re-run this pass when any of those three pins moves.

---

# Layer 2 harness grounding — deepagents 0.7.5 / langchain 1.3.14 / langgraph 1.2.10, verified in this repo's `.venv`

All scripts under `/private/tmp/claude-501/-Users-smesropyan-projects-agentic-pkb/c3287252-66c3-42c0-ada1-74df4e8125ff/scratchpad/l2recon/`. Every claim below was run, not read. Shared fake model: `fakes.py`. **End-to-end proof wiring the real `pkb.core` into the real harness: `q0_integration.py`** — it passes on both `invoke()` and `ainvoke()`.

**Headline: 8 CONFIRMED, 2 DIVERGE. The two divergences (Q3 `after_agent` on failure; the `general-purpose` subagent bypassing custom middleware) both break claims the architecture doc makes in §7 and both have cheap fixes.**

---

## 1. Middleware that rejects a tool call — **CONFIRMED**

`langchain/agents/middleware/types.py:662` (async twin at `:744`):

```python
def wrap_tool_call(
    self,
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
) -> ToolMessage | Command[Any]:
```

`ToolCallRequest` (`langgraph/prebuilt/tool_node.py:133`): `tool_call: ToolCall`, `tool: BaseTool | None`, `state: Any`, `runtime: ToolRuntime`; `.override(**overrides)` (direct assignment deprecated).

**To reject: construct and return a `ToolMessage(..., status="error", tool_call_id=tc["id"])` — or a `Command(update={"messages":[that ToolMessage], ...})` — and never call `handler`.** `q1_q2_reject_and_state.py`: two invalid writes rejected, the third landed; disk held only `frontmatter ok`. `q0_integration.py` does the same with real `validate_content` / `render_findings`, and the model saw:

```
ToolMessage[error] Cooking/notes/preheat.md: [MISSING_FRONTMATTER/VA-3] The file carries no
YAML frontmatter. — Open the file with a --- block holding title, description, topic, tags, ...
```

Four grounding facts arch §7 needs and does not currently state:

- **Path normalisation happens *inside* the tool, after your middleware.** `deepagents/backends/utils.py:648 validate_path()` runs at `filesystem.py:1937`+; `wrap_tool_call` sees the raw model arg. `q4_backends_and_paths.py`: `write_file(file_path="kb/Cooking/notes/b.md")` (no leading slash) **landed on disk in the KB** while `raw.startswith("/kb/")` said `False`. The middleware must call `validate_path(raw)` itself before deciding — `/kb/./Cooking//notes/c.md` normalises too.
- **`edit_file` carries no content**: args are `file_path, old_string, new_string, replace_all` (`filesystem.py:1144-1150`). `validate_content` needs the post-edit text, so the middleware must read current bytes and apply the replacement itself.
- **`handler()` returns a `ToolMessage`, not a `Command`, for every filesystem tool** — including `StateBackend` writes, which mutate state through `CONFIG_KEY_SEND` (`q2b_handler_returns.py`). Safe to wrap in your own `Command`.
- **Permission denies come back through `handler()` as an error `ToolMessage`.** Only record a touched path when `getattr(result, "status", None) != "error"`, or a denied `index.md` write gets its `updated` bumped (I hit exactly this before adding the guard — see `_record` in `q0_integration.py`).

`FilesystemMiddleware.wrap_tool_call` (large-result eviction) is outermost; ours sits inside it and *before* the permission check, so any `.name` other than `FilesystemMiddleware` is fine.

## 2. Middleware state — **CONFIRMED**

Custom state schema. `langchain/agents/factory.py:1154`: `state_schemas = [*(m.state_schema for m in middleware), base_state]`, merged by `_resolve_schemas`. Declare `state_schema = KbState` on the middleware; write through `Command(update={...})` from `wrap_tool_call`; read via `request.state` / `state` in `after_agent`.

```python
class KbState(AgentState):
    kb_touched:  NotRequired[Annotated[list[str], merge_or_reset]]
    kb_attempts: NotRequired[Annotated[dict[str, int], merge_or_reset]]
```

Verified in `q1_q2_reject_and_state.py`: attempt counter read 1, then 2 across successive tool steps; `after_agent` saw `['/kb/a.md']`.

Three constraints:

- **`operator.add` is not enough — you need a reset sentinel.** State is checkpointed, so turn 2 on the same thread would inherit turn 1's touched set. Use a reducer where `None` on the right means "clear".
- **Reset in `before_agent`, not after the run.** `agent.update_state(cfg, {...})` from outside the graph trips a langchain 1.3.14 routing bug (`KeyError: 'model'`) when the run died at the model node — `q3c_corrected_flush.py`. `before_agent` does **not** re-run on interrupt resume (`q8_hitl.py`), so paths touched before an approval survive the pause.
- **Never keep per-run state on `self`.** `q2c_async_and_sharing.py`: one middleware instance serves every run of the compiled graph (same `id()` across two agents). Instance attributes are read-only config (`kb_root`) only.
- Sibling tool calls in one `AIMessage` do **not** see each other's updates (`request.state` is the pre-step snapshot). Retries arrive as separate model turns, so the 3-attempt bound is unaffected.

## 3. `after_agent` on the failure path — **DIVERGES. This is the most important finding.**

`after_agent` is an ordinary graph node on the normal exit edge (`factory.py:1589`, `exit_node = f"{middleware_w_after_agent[-1].name}.after_agent"`). **An exception anywhere aborts the pregel superstep and the node is never reached.**

`q3_after_agent_failure.py` / `q3b_failure_variants.py`, four failure shapes, `after_agent` ran in **none**:

| failure | events | disk |
|---|---|---|
| clean control | `before_agent, after_model, tool:write_file, after_model, **after_agent**` | `a.md` |
| model raises after a write | `before_agent, after_model, tool:write_file` | `a.md` (written, no flush) |
| middleware raises in `wrap_tool_call` | `tool:write_file` | — |
| async `ainvoke`, model raises | `atool:write_file` | `a.md` (written, no flush) |

> Architecture §7: *"The flush runs on both success and failure."* **False as written.** `KbMaintenanceMiddleware.after_agent` alone leaves the tree with stale derived files after exactly the case the doc says it is protecting against.

**Corrected approach (proven in `q3c_corrected_flush.py`):** the state *is* checkpointed on the error path (`get_state(cfg).values["kb_touched"] == ['/kb/a.md']`, `next=('model',)`, thread resumable). So:

1. `after_agent` flushes on the happy path (it has state, runs in-graph, is checkpointed).
2. The runner (`PkbService.stream_run`) wraps the run in `try/finally`; if `after_agent` did not fire (run-scoped sentinel), it reads `agent.get_state(cfg).values["kb_touched"]` and calls the same `flush()`.
3. `before_agent` clears the set at entry.

Result: exactly one flush per run, both paths — `success: [('after_agent', ['/kb/a.md','/kb/b.md'])]`, `model raises: [('runner-finally', ['/kb/a.md'])]`.

Also note: `after_agent` correctly does **not** run while a run is paused at a HITL interrupt; it runs on resume (`q8_hitl.py` §A).

## 4. Backends — **CONFIRMED**

`CompositeBackend(default=StateBackend(), routes={"/kb/": FilesystemBackend(root_dir=KB, virtual_mode=True)})` constructs and routes exactly as §4 specifies. `q4_backends_and_paths.py`: `/kb/Cooking/notes/a.md` → disk; `/scratch/d.md` → `state["files"]`. Intermediate directories are created automatically.

**Agent path → on-disk path.** `FilesystemBackend._resolve_path` (`backends/filesystem.py:203-215`) is `(root_dir / vpath.lstrip("/")).resolve()`, rejecting `..`/`~`, then `full.relative_to(self.cwd)`. The route prefix is stripped by `CompositeBackend._get_backend_and_key` before it gets there. So the middleware needs only:

```python
def kb_rel(raw: str) -> str | None:                 # -> kb-root-relative POSIX
    normalized = validate_path(raw)                  # deepagents' own normaliser
    return normalized[len("/kb/"):] if normalized.startswith("/kb/") else None
```

which is exactly what `validate_content(kb_root, rel_path, text)` wants — no on-disk path needed for the gate. Where a disk path *is* needed (reading current bytes for `edit_file`), use `(kb_root / rel)` and remember to compare against `kb_root.resolve()` for containment (macOS `/var` → `/private/var`).

## 5. `FilesystemPermission` — **CONFIRMED, and I3's glob list can shrink**

`deepagents.middleware.filesystem.FilesystemPermission`, re-exported from `deepagents`:

```python
@dataclass
class FilesystemPermission:
    operations: list[Literal["read", "write"]]
    paths: list[str]
    mode: Literal["allow", "deny", "interrupt"] = "allow"
```

`q5_permissions.py`, live agent: `write_file("/kb/index.md")`, `write_file("kb/index.md")` and `edit_file("/kb/index.md")` all returned `ToolMessage[error] 'Error: permission denied for write on /kb/index.md'` and the file on disk still read `GENERATED`. **The un-normalised bypass does not work here** — permissions are checked after `validate_path`, so I3 is airtight where the validation middleware is not.

`__post_init__`: `"kb/**"` → `ValueError` (must start with `/`); `".."` → `ValueError`; `"~"` → `NotImplementedError`.

**Two corrections to arch I3's snippet:**
- `/kb/**/index.md` **already matches `/kb/index.md`** (wcmatch `GLOBSTAR` matches zero directories — verified). The three-glob list collapses to `["/kb/**/index.md", "/kb/**/tags.md"]`.
- The second glob also closes the gap Layer 1 §7.4 flagged (`Cooking/tags.md`, VA-27) — verified `deny`.

Build the list from `pkb.core.is_derived_name`'s definition rather than restating globs, per Layer 1 §7.4.

## 6. Skills — **CONFIRMED**

`q6_skills.py`. Paths are **backend paths**, `list[str]`, one directory per skill, `SKILL.md` required, discovery is one level deep per source (`_list_skills_with_errors`).

Frontmatter schema parsed into `SkillMetadata` — live output for a topic overload:
```python
{'name': 'voice', 'description': 'COOKING: recipe voice',
 'path': '/kb/Cooking/skills/voice/SKILL.md', 'metadata': {}, 'license': 'MIT',
 'compatibility': None, 'allowed_tools': ['read_file', 'write_file']}
```
Frontmatter key is `allowed-tools` (hyphen). `name` must equal the parent directory name, 1-64 chars, lowercase alnum + single hyphens.

**Override resolution is exactly the PKB's chain**, last source wins by skill name, whole-record replacement:
```python
skills=["/shipped-skills/", "/kb/skills/", "/kb/Cooking/skills/"]
```
→ `summarization` from shipped, `discovery` from KB root, `voice` from Cooking. **Shipped default skills work as a fourth `FilesystemBackend` route** (`/shipped-skills/` → the package dir) — this is how README Part 3's "default skills ship with the implementation" is satisfied without copying files into the KB.

Progressive disclosure confirmed: only name + description + path + license/allowed-tools reach the system prompt; bodies do not (`"shipped body" in prompt == False`). deepagents even renders `**Cooking Skills**: /kb/Cooking/skills/ (higher priority)`. Auto-derived labels are ugly (`"Kb Skills"`) — use `SkillsMiddleware(sources=[(path, label)])` via `middleware=` if that matters.

**Gotcha with teeth for the daemon:** `skills_metadata` is checkpointed and `before_agent` short-circuits if present. Adding a skill mid-thread is invisible — verified: added `research`, re-ran the same `thread_id`, still `['discovery','summarization','voice']`. Registry invalidation must clear that state key or start a new thread.

## 7. Subagents — **CONFIRMED for `CompiledSubAgent`; DIVERGES on `general-purpose`**

`CompiledSubAgent.__annotations__ == {'name': str, 'description': str, 'runnable': Runnable}`, all three required. `q7_subagents.py`: the Cooking expert compiled graph registered on the Librarian, `task(description=..., subagent_type="topic-cooking")` routed to it, the write landed, and the parent got back:

```
ToolMessage  task  'Filed the steak note under Cooking/notes/steak.md.'
```

i.e. **the subagent's last `AIMessage` text**. The subagent saw only `[SystemMessage, HumanMessage('File this steak note')]` — isolated context, exactly arch §4's "Consequence" paragraph. `after_agent` fired in **both** graphs, so the flush runs twice per delegated turn: harmless (idempotent) but **the §8 global KB write lock must be reentrant or the nested flush deadlocks.**

The `task` tool description is a static string built at compile time listing every subagent name+description — so adding a topic genuinely requires rebuilding the Librarian graph, confirming arch §4's invalidation design.

> **DIVERGENCE.** `q1b_subagent_bypass.py`: the auto-added `general-purpose` subagent **does not inherit custom middleware**. `graph.py:776` — `_gp_inheritable = [m for m in (middleware or []) if m.name in _gp_original_name_to_index]` — only middleware whose `.name` collides with a default GP slot survives. A parent whose `Guard` blocks every `write_file` saw only `['task']`, and `/kb/x.md` was written. It *does* inherit `permissions` (`/kb/index.md` stayed `GENERATED`), so **I3 holds but arch §7's "unskippable" validation does not.**
>
> **Fix (verified):** supply an explicit `SubAgent(name="general-purpose", ..., middleware=[KbValidationMiddleware(...)])` in `subagents=` — an explicit spec suppresses the auto-add, and `_apply_custom_middleware(subagent_middleware, spec["middleware"])` (`graph.py:699`) applies it. Re-run: `Guard saw: ['task', 'write_file']`, `ON DISK: []`.

## 8. HITL — **CONFIRMED**

`q8_hitl.py`. `interrupt_on: dict[str, bool | InterruptOnConfig]`; `True` → all four decisions, `False` → auto-approve.

```python
class InterruptOnConfig(TypedDict):
    allowed_decisions: list[Literal["approve","edit","reject","respond"]]
    description: NotRequired[str | _DescriptionFactory]
    args_schema: NotRequired[dict[str, Any]]
    when: NotRequired[Callable[[ToolCallRequest], bool]]
```

On `stream(..., stream_mode="updates")` the event is a `__interrupt__` key whose value is a one-element tuple of `Interrupt(id=..., value=...)`:

```json
{"action_requests": [{"name": "write_file",
                      "args": {"file_path": "/kb/Cooking/notes/summary.md", "content": "DRAFT"},
                      "description": "Approve this KB write"}],
 "review_configs": [{"action_name": "write_file",
                     "allowed_decisions": ["approve", "reject"]}]}
```

Resume shapes, all live-verified:

```python
Command(resume={"decisions": [{"type": "approve"}]})                              # file written
Command(resume={"decisions": [{"type": "reject",  "message": "wrong voice"}]})    # ToolMessage[error] 'wrong voice', disk untouched
Command(resume={"decisions": [{"type": "edit", "edited_action":
                 {"name": "write_file", "args": {...}}}]})                        # file == "HUMAN EDIT"
Command(resume={"decisions": [{"type": "respond", "message": "..."}]})            # tool skipped, ToolMessage[success]
```

Decisions must be positionally aligned with the interrupted calls. Permission-derived (`mode="interrupt"`) and explicit `interrupt_on` produce the same payload; explicit wins per tool name. The `when` predicate receives a `ToolCallRequest` and narrowing `allowed_decisions` to `["approve","reject"]` is honoured verbatim — that is the Telegram adapter's mechanism (arch §6).

Cross-channel resume discovery works: `get_state(cfg).interrupts` → `[('12fda630...', ['action_requests','review_configs'])]`, `next=('HumanInTheLoopMiddleware.after_model',)`.

## 9. Checkpointer — **CONFIRMED, with a load-bearing addressing correction**

`q9_checkpointer.py`. `langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver`; the package root exports only `SqliteSaver`.

- `from_conn_string(path)` is a **classmethod `@asynccontextmanager`** — the daemon must hold it in an `AsyncExitStack` for the process lifetime. After exit: `ValueError: no active connection`.
- `__init__` calls `asyncio.get_running_loop()`; constructing outside a loop → `RuntimeError: no running event loop`. `self.loop` pins the saver.
- `setup()` is lazy (`is_setup` False → True after the first run), runs `PRAGMA journal_mode=WAL`, creates `checkpoints` and `writes` with PK `(thread_id, checkpoint_ns, checkpoint_id)`.
- **One instance shared by many compiled graphs: yes.** Three graphs, three concurrent `ainvoke`s, distinct threads → `['lib says hi', 'filed', 'physics says hi']`. All DB access is serialised by one `asyncio.Lock`, so it is safe but not parallel — fine for a personal KB.
- The daemon's own `threads` / scan-queue tables live happily in the same file via a separate `aiosqlite` connection: `tables in pkb.sqlite: ['checkpoints', 'threads', 'writes']`, WAL already on.

> **Correction to arch §4/§5.** *"A thread is `(agent_id, thread_id)`"* is **not** enforced by the checkpointer — `thread_id` alone is the key. The Librarian graph reading `thread_id="t-cook"` saw the Cooking expert's 4 messages verbatim. And `checkpoint_ns` is **not** available as the second dimension: passing `checkpoint_ns="agent:a1"` makes `get_state` raise `ValueError: Subgraph agent not found` (it is reserved for subgraph namespacing). **Thread ids must be globally unique (UUIDs) and the daemon's `threads` table is the only agent_id ↔ thread_id mapping.**

## 10. Testing without an API key — **CONFIRMED, with a divergence you must plan around**

langchain-core 1.5.3 ships `GenericFakeChatModel`, `FakeMessagesListChatModel`, `FakeListChatModel`, `ParrotFakeChatModel` in `langchain_core.language_models`. **None of them implements `bind_tools`** — `BaseChatModel.bind_tools` raises `NotImplementedError` (`chat_models.py:2338`) and `create_agent` *always* calls it (`factory.py:1367/1390/1399`). So **the stock fakes cannot drive a deep agent**; you need langchain's own test pattern. `fakes.py`:

```python
class ScriptedChatModel(BaseChatModel):
    script: list[Any]          # AIMessage, or a zero-arg callable that may raise
    idx: int = 0
    calls: list[list[BaseMessage]] = []

    def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        self.calls.append(list(messages))
        item = self.script[min(self.idx, len(self.script) - 1)]
        self.idx += 1
        msg = item() if callable(item) else item
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, *, tool_choice=None, **kw): return self   # required

def call(name, args, id_): return {"name": name, "args": args, "id": id_, "type": "tool_call"}
```

Scripting a tool call is `AIMessage(content="", tool_calls=[call("write_file", {...}, "c1")])`; scripting a provider failure is a callable that raises. `self.calls` captures the exact prompt (used above to inspect skill disclosure). One model instance is shared with subagents, so the script is consumed in global call order.

Three test-writing rules the harness forces:

- **Give every scripted tool call a unique id and always terminate the script.** A repeated id (or a script that sticks on a tool call) hits a langchain 1.3.14 routing bug: `_make_model_to_tools_edge` case 6 returns `"model"`, but `"model"` is only in `model_to_tools_destinations` when a `response_format` or an `after_model` middleware exists → `KeyError: 'model'` (`langgraph/graph/_branch.py:203`).
- **Implement both the sync and the async hook.** `q2c_async_and_sharing.py`: a sync-only `wrap_tool_call` under `ainvoke()` raises `NotImplementedError: Asynchronous implementation of awrap_tool_call is not available`. The daemon is async and the non-live tests will want sync — Layer 2's middleware must ship `wrap_tool_call`/`awrap_tool_call` and `after_agent`/`aafter_agent`, with the blocking Layer 1 calls behind `asyncio.to_thread` (pattern in `q0_integration.py`).
- **Hook ordering**, `middleware=[A, B]`: `A.wrap.in → B.wrap.in → B.wrap.out → A.wrap.out`, then `B.after_agent → A.after_agent`. So list `[KbValidationMiddleware, KbMaintenanceMiddleware]` — validation outermost on the tool path, maintenance first on the way out.

---

## Corrections the Layer 2 spec must carry

| # | Doc | Says | Actually | Fix |
|---|---|---|---|---|
| 1 | arch §7 | "The flush runs on both success and failure" | `after_agent` never runs on any failure path | `after_agent` + a `try/finally` flush in the runner reading `get_state().values["kb_touched"]`; reset in `before_agent` |
| 2 | arch §7 | validation is "unskippable" | the auto-added `general-purpose` subagent skips custom middleware entirely | supply an explicit `SubAgent(name="general-purpose", middleware=[...])` |
| 3 | arch §7 | intercepts writes "routing to the KB" | raw tool args are un-normalised; `kb/x.md` reaches the KB | call `deepagents.backends.utils.validate_path` in the middleware |
| 4 | arch §7 | `wrap_tool_call` intercepts `edit_file` | `edit_file` has no `content` arg | reconstruct post-edit text from disk before `validate_content` |
| 5 | arch I3 | 3 globs | `/kb/**/index.md` covers `/kb/index.md` | `["/kb/**/index.md", "/kb/**/tags.md"]`, derived from `pkb.core.is_derived_name` |
| 6 | arch §4/§5 | "A thread is `(agent_id, thread_id)`" | checkpointer keys on `thread_id` only; `checkpoint_ns` is reserved | globally unique thread ids + the daemon `threads` table |
| 7 | arch §8 | global KB write lock around the flush | delegated subagent runs flush nested inside the parent's flush | make the lock reentrant, or skip the flush inside `CompiledSubAgent` graphs |
| 8 | arch §9 | "middleware against a fake chat model" | stock langchain fakes lack `bind_tools` | ship `ScriptedChatModel` as a test fixture in `tests/agents/conftest.py` |
| 9 | — | — | skills load once per thread and are checkpointed | registry invalidation must clear `skills_metadata` or open a new thread |
| 10 | README Part 3 | default skills ship with the implementation | works as a second `FilesystemBackend` route (`/shipped-skills/`) ahead of `/kb/skills/` in the `skills=` list | — |