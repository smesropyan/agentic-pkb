# deepagents 0.7.5 — API recon

**Date**: 2026-08-06
**Why this exists**: the architecture spec (D1) commits Layer 2 to `deepagents`. This is a verbatim
reading of the *installed* package, checking each assumption the architecture doc makes. Written
before Layer 2 is specced so the spec can quote real signatures. Re-verify against the pinned
version when Layer 2 starts.

---

Recon complete. Everything below is quoted from the actually-installed `deepagents==0.7.5` in `/private/tmp/claude-501/-Users-smesropyan-projects-agentic-pkb/c3287252-66c3-42c0-ada1-74df4e8125ff/scratchpad/recon/.venv/lib/python3.12/site-packages/`.

# Installed set

`deepagents 0.7.5` · `langchain 1.3.14` · `langchain-core 1.5.3` · `langgraph 1.2.10` · `langgraph-prebuilt 1.1.0` · `langgraph-checkpoint 4.1.1` · `langchain-anthropic 1.5.4` · `langchain-google-genai 4.3.2` · `wcmatch 11.0` · Python 3.12.13.

---

## 1. `create_deep_agent` — CONFIRMED (with parameter-name corrections)

`deepagents/graph.py:268`:

```python
def create_deep_agent(
    model: str | BaseChatModel | None = None,
    tools: Sequence[BaseTool | Callable | dict[str, Any]] | None = None,
    *,
    system_prompt: str | SystemMessage | None = None,
    middleware: Sequence[AgentMiddleware[StateT_co, ContextT]] = (),
    subagents: Sequence[SubAgent | CompiledSubAgent | AsyncSubAgent] | None = None,
    skills: list[str] | None = None,
    memory: list[str] | None = None,
    permissions: list[FilesystemPermission] | None = None,
    backend: BackendProtocol | None = None,
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    response_format: ResponseFormat[ResponseT] | type[ResponseT] | dict[str, Any] | None = None,
    state_schema: type[DeepAgentState] | None = None,
    context_schema: type[ContextT] | None = None,
    checkpointer: Checkpointer | None = None,
    store: BaseStore | None = None,
    debug: bool = False,
    name: str | None = None,
    cache: BaseCache | None = None,
) -> CompiledStateGraph[AgentState[ResponseT], ContextT, InputAgentState, OutputAgentState[ResponseT]]:
```

Notes that matter for architecture:

- The prompt kwarg is `system_prompt`, **not** `instructions` (that name does not exist in 0.7.5).
- `skills` and `memory` are `list[str]` of **backend paths**, not objects. `memory` = list of `AGENTS.md` paths.
- `backend` must be an **instance**; factories were removed: `"backend must be an initialized backend instance. Backend factories were removed in deepagents 0.7; pass StateBackend(), ..."` (`filesystem.py:1661`).
- `model=None` is deprecated (`since 0.5.3`, removal `1.0.0`) and defaults to `ChatAnthropic(model_name="claude-sonnet-4-6")`.
- Returns `.with_config({"recursion_limit": 9_999, ...})`.
- No `tool_configs`/`post_model_hook`/`builtin_tools` params. Built-in tool suite: `ls, read_file, write_file, edit_file, delete, glob, grep, execute, task`. `tools=` is purely additive; you cannot remove a built-in via `create_deep_agent` — only via a registered `HarnessProfile(excluded_tools=...)`.

Middleware assembly order (from the docstring at `graph.py:361-396`, matching the code at 817-893): `SkillsMiddleware` (if `skills`) → `FilesystemMiddleware` → `SubAgentMiddleware` → `SummarizationMiddleware` → `PatchToolCallsMiddleware` → `AsyncSubAgentMiddleware` → **your `middleware=`** → profile extras → prompt caching → `MemoryMiddleware` → `HumanInTheLoopMiddleware`.

Important gotcha: `_apply_custom_middleware` merges **by `.name`**. If your middleware's `.name` collides with a stack member it **replaces it in place** rather than being appended (`graph.py:201-235`).

---

## 2. Middleware base class — CONFIRMED

Comes from **`langchain.agents.middleware.types`**, not deepagents. deepagents re-exports nothing of it; its own middleware subclass it:

```python
class AgentMiddleware(Generic[StateT, ContextT, ResponseT]):
    state_schema: type[StateT] = cast("type[StateT]", _DefaultAgentState)
    tools: Sequence[BaseTool]
    transformers: Sequence[TransformerFactory] = ()

    @property
    def name(self) -> str: ...

    def before_agent(self, state: StateT, runtime: Runtime[ContextT]) -> dict[str, Any] | None
    async def abefore_agent(...)
    def before_model(self, state: StateT, runtime: Runtime[ContextT]) -> dict[str, Any] | None
    async def abefore_model(...)
    def after_model(self, state: StateT, runtime: Runtime[ContextT]) -> dict[str, Any] | None
    async def aafter_model(...)
    def after_agent(self, state: StateT, runtime: Runtime[ContextT]) -> dict[str, Any] | None
    async def aafter_agent(...)

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT] | AIMessage | ExtendedModelResponse[ResponseT]
    async def awrap_model_call(...)  # handler returns Awaitable[ModelResponse]

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]
    async def awrap_tool_call(...)  # handler returns Awaitable[ToolMessage | Command]
```

All hook names in your list exist. Sync and async are **separate methods** — the base sync `wrap_*_call` raises `NotImplementedError` with a message telling you to implement the other variant, so an async-only middleware breaks under `invoke()`/`stream()`. Implement both if the daemon mixes sync and async entry points.

`ToolCallRequest` is from **`langgraph.prebuilt.tool_node`** (also re-exported as `langchain.tools.tool_node.ToolCallRequest`, which is what deepagents imports in `_fs_interrupt.py`):

```python
@dataclass
class ToolCallRequest:
    tool_call: ToolCall
    tool: BaseTool | None
    state: Any
    runtime: ToolRuntime

    def override(self, **overrides) -> ToolCallRequest   # tool_call, state
```
Direct attribute assignment is deprecated; use `.override()`.

**Returning an error ToolMessage without invoking the handler — CONFIRMED.** Just construct and return it; the handler is never called and the tool never runs. This is exactly what deepagents itself does (`filesystem.py:2012`):

```python
if _check_fs_permission(self._permissions, "write", validated_path) == "deny":
    return ToolMessage(
        content=f"Error: permission denied for write on {validated_path}",
        name="write_file",
        tool_call_id=runtime.tool_call_id,
        status="error",
    )
```

Verified end-to-end with a live agent run (`.../scratchpad/recon/smoke2.py`) — a `Guard(AgentMiddleware)` returning `ToolMessage(..., status="error")` from `wrap_tool_call` produced:
```
ToolMessage | 'Error: blocked by Guard' | status: error
FILES: {}
```
i.e. the write was fully suppressed.

One caveat: `FilesystemMiddleware.wrap_tool_call` (`filesystem.py:3445`) is already occupied — it exists for large-result eviction and always calls `handler(request)`. Your guard must be a **separate** middleware (any different `.name`), or it will replace filesystem eviction.

---

## 3. Backends — CONFIRMED

`deepagents/backends/__init__.py` exports: `BackendProtocol, CompositeBackend, ContextHubBackend, FilesystemBackend, LangSmithSandbox, LocalShellBackend, NamespaceFactory, StateBackend, StoreBackend, DEFAULT_EXECUTE_TIMEOUT`.

```python
class CompositeBackend(BackendProtocol):
    def __init__(
        self,
        default: BackendProtocol | StateBackend,
        routes: dict[str, BackendProtocol],
        *,
        artifacts_root: str = "/",
    ) -> None
```
Routes match longest-prefix-first; the prefix is **stripped** before forwarding to the routed backend. `ls("/")` aggregates default entries plus one synthetic `FileInfo(path=route_prefix, is_dir=True)` per route.

```python
class FilesystemBackend(BackendProtocol):
    def __init__(
        self,
        root_dir: str | Path | None = None,
        virtual_mode: bool = True,
        max_file_size_mb: int = 10,
    ) -> None
```
`root_dir` and `virtual_mode=True` both exist as named. `virtual_mode=True` anchors all paths to `root_dir`, blocks `..`/`~`, and verifies the resolved path stays under root. Docstring is explicit that it "does not provide sandboxing or process isolation".

```python
class StateBackend(BackendProtocol):
    def __init__(self) -> None
```
No args. Requires a live LangGraph execution context (`CONFIG_KEY_READ`/`CONFIG_KEY_SEND`); calling it outside a graph raises `RuntimeError`. Pre-populate via `agent.invoke({"messages": [...], "files": {...}})`.

**Read/write API surface** (`backends/protocol.py:378-751`), every method has an `a`-prefixed async twin that defaults to `asyncio.to_thread`:

```python
ls(path: str) -> LsResult
read(file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult
grep(pattern: str, path: str|None = None, glob: str|None = None, *, max_count: int|None = None) -> GrepResult
glob(pattern: str, path: str|None = None) -> GlobResult
write(file_path: str, content: str) -> WriteResult
edit(file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult
delete(file_path: str) -> DeleteResult          # optional; raises NotImplementedError by default; recursive
upload_files(files: list[tuple[str, bytes]]) -> list[FileUploadResponse]
download_files(paths: list[str]) -> list[FileDownloadResponse]
```
Result types are dataclasses (`ReadResult, WriteResult, EditResult, DeleteResult, LsResult, GrepResult, GlobResult`) each carrying an `error` field rather than raising. `grep` is **literal substring, not regex** — explicitly documented (`protocol.py:484`).

`execute` lives on `SandboxBackendProtocol(BackendProtocol)`. Plain `FilesystemBackend` does **not** have it — the only local sandbox is `class LocalShellBackend(FilesystemBackend, SandboxBackendProtocol)` (`local_shell.py:26`), which runs unsandboxed host shell.

Live-verified: `CompositeBackend(default=StateBackend(), routes={"/kb/": FilesystemBackend(root_dir=..., virtual_mode=True)})` constructs and routes.

---

## 4. `FilesystemPermission` — CONFIRMED

Import path: `from deepagents import FilesystemPermission`, canonical `deepagents.middleware.filesystem.FilesystemPermission` (`filesystem.py:383`). `deepagents.middleware.permissions` is a backward-compat re-export shim only.

```python
@dataclass
class FilesystemPermission:
    operations: list[FilesystemOperation]     # Literal["read", "write"]
    paths: list[str]
    mode: Literal["allow", "deny", "interrupt"] = "allow"
```

Operation mapping (`filesystem.py:123`):
```python
_DEFAULT_FS_TOOL_OPS = {
    "ls": "read", "read_file": "read", "glob": "read", "grep": "read",
    "write_file": "write", "edit_file": "write", "delete": "write",
}
```
`execute` is **not** in this table — shell commands bypass permissions entirely.

Resolution (`filesystem.py:420`) — first matching rule wins, default allow:
```python
def _check_fs_permission(rules, operation, path) -> Literal["allow","deny","interrupt"]:
    for rule in rules:
        if operation not in rule.operations:
            continue
        if any(wcglob.globmatch(path, pattern, flags=_FS_WCMATCH_FLAGS) for pattern in rule.paths):
            return rule.mode
    return "allow"
```

**Glob syntax**: `wcmatch.glob.globmatch` with `_FS_WCMATCH_FLAGS = wcglob.BRACE | wcglob.GLOBSTAR` (`filesystem.py:114`). Live-verified: `*`, `?`, `[0-9]`, `**` (globstar, matches zero-or-more dirs), and `{a,b}` brace expansion all work. `/kb/**/*.md` matches both `/kb/a/b.md` **and** `/kb/b.md`. No `EXTGLOB`, no `NEGATE`, no case-insensitivity flag.

`__post_init__` validation (live-verified):
- path not starting with `/` → `ValueError: Permission path must start with '/'`
- `..` in parts → `ValueError`
- `~` in parts → **`NotImplementedError`** (not `ValueError`)

Semantics caveats worth putting in the doc:
- A rule path of `"/kb/"` does **not** cover `/kb/a.md` — patterns are matched against the full path, so write `/kb/**`.
- Permissions apply **at the tool layer inside `FilesystemMiddleware`**, not at the backend. Direct backend calls (e.g. your own code, or a subagent with a custom `FilesystemMiddleware`) bypass them entirely. Docstring: *"`FilesystemMiddleware` applies these permissions at the tool level for its built-in filesystem tools, not at the backend level."*
- Bulk tools (`ls`/`glob`/`grep`) do not deny outright — they **filter denied entries out of results** (`_filter_paths_by_permission`, `_filter_grep_matches_by_permission`).
- Subagents inherit parent `permissions` unless they set their own, which replaces the parent's list wholesale.
- `mode="interrupt"` requires `HumanInTheLoopMiddleware` — auto-installed by `create_deep_agent` when any interrupt rule is present.

---

## 5. `CompiledSubAgent` — CONFIRMED

`deepagents/middleware/subagents.py:167`, exported from `deepagents`. It is a `TypedDict` with exactly three fields (live-verified `__annotations__`):

```python
class CompiledSubAgent(TypedDict):
    name: str
    description: str
    runnable: Runnable
```

Detection in `create_deep_agent` is by key presence: `if "graph_id" in spec` → `AsyncSubAgent`; `elif "runnable" in spec` → `CompiledSubAgent`; else declarative `SubAgent`.

`CompiledSubAgent` does **not** inherit `interrupt_on`, `permissions`, `state_schema`, or any middleware from the parent — you own the compiled runnable entirely. Its state schema must include `messages`. Result extraction: `structured_response` if non-`None` (JSON-serialized), else the last non-empty `AIMessage` text.

For comparison, `SubAgent` (`subagents.py:36`) fields: `name, description, system_prompt` (required); `tools, model, middleware, interrupt_on, skills, permissions, response_format` (optional).

---

## 6. Skills — CONFIRMED

Layout (`skills.py:21-44`), one directory per skill, `SKILL.md` required, **discovered only one level deep** under each source (`_list_skills_with_errors` does a single non-recursive `backend.ls(source_path)` and looks for `<dir>/SKILL.md`):

```
/skills/user/web-research/
├── SKILL.md          # Required: YAML frontmatter + markdown instructions
└── helper.py         # Optional: supporting files
```
```markdown
---
name: web-research
description: Structured approach to conducting thorough web research
license: MIT
---
```

`SkillMetadata` fields: `path, name, description, license, compatibility, metadata, allowed_tools`. The frontmatter key is `allowed-tools` (hyphen) and accepts a space/comma-separated string or a YAML list. Constraints: name 1-64 chars, lowercase alnum + single hyphens, **must equal the parent directory name**; description ≤ 1024; compatibility ≤ 500; file ≤ 10 MB.

Passing them in — two paths:
```python
create_deep_agent(..., skills=["/skills/user/", "/skills/project/"])   # list[str] only
SkillsMiddleware(backend=..., sources=[...], system_prompt=SKILLS_SYSTEM_PROMPT)
```
`SkillsMiddleware.sources` accepts `SkillSource = str | tuple[str, str]` (path or `(path, label)`), but `create_deep_agent(skills=...)` is typed `list[str]` — to use labels you must construct `SkillsMiddleware` yourself and pass it via `middleware=` (name-collision replacement makes this work).

**Override resolution — CONFIRMED, last-wins by skill name**, in `before_agent` (`skills.py:946-963`):
```python
if "skills_metadata" in state:
    return None                    # loaded once per session, then checkpointed
all_skills: dict[str, SkillMetadata] = {}
for source_path in self.sources:
    source_skills, source_error = _list_skills_with_errors(backend, source_path)
    for skill in source_skills:
        all_skills[skill["name"]] = skill
```
Live-verified with a base/user layout: `base/note-taking` was fully replaced by `user/note-taking` (description, path, `allowed_tools`, `metadata` all from the user copy) while `user/research` was added. Note it is a whole-record replacement, not a field-level merge.

Delivery is progressive disclosure: only name+description+path go into the system prompt; the model must `read_file` the `SKILL.md` (the prompt tells it to pass `limit=1000`).

Subagents get skills only if their `SubAgent` spec sets `skills`; the auto-added `general-purpose` subagent inherits the top-level `skills` (`graph.py:761`).

---

## 7. HITL — CONFIRMED

Two ways to configure, both funneling into `HumanInTheLoopMiddleware(interrupt_on=...)`, merged by `_merge_fs_interrupt_on` with **user `interrupt_on` winning per tool name** over permission-derived entries:

```python
class InterruptOnConfig(TypedDict):
    allowed_decisions: list[DecisionType]        # Literal["approve","edit","reject","respond"]
    description: NotRequired[str | _DescriptionFactory]
    args_schema: NotRequired[dict[str, Any]]
    when: NotRequired[Callable[[ToolCallRequest], bool]]
```
`interrupt_on={"edit_file": True}` expands to all four decisions; `False` auto-approves.

The interrupt is raised in **`after_model`**, not in the tool wrapper (`human_in_the_loop.py:435`): `decisions = interrupt(hitl_request)["decisions"]` using `from langgraph.types import interrupt`. All interruptible tool calls in one `AIMessage` are batched into a single interrupt, and the resume payload must supply **exactly as many decisions as interrupted calls, in order** — otherwise `ValueError: Number of human decisions (N) does not match number of hanging tool calls (M)`.

Interrupt payload shape (`HITLRequest`), live-captured from a real run with `permissions=[FilesystemPermission(operations=["write"], paths=["/secrets/**"], mode="interrupt")]`:
```json
{
  "action_requests": [{"name": "write_file",
                       "args": {"file_path": "/secrets/a.md", "content": "hi"},
                       "description": "Tool execution requires approval\n\nTool: write_file\nArgs: {...}"}],
  "review_configs": [{"action_name": "write_file",
                      "allowed_decisions": ["approve","edit","reject","respond"]}]
}
```

Resume shape (`HITLResponse`) — `Command(resume={"decisions": [...]})` where each decision is one of:
```python
{"type": "approve"}
{"type": "edit",    "edited_action": {"name": str, "args": dict}}   # id preserved
{"type": "reject",  "message": str}    # optional; -> ToolMessage status="error"
{"type": "respond", "message": str}    # tool skipped; -> ToolMessage status="success"
```
Live-verified: `agent.invoke(Command(resume={"decisions": [{"type": "reject", "message": "nope"}]}), config=cfg)` produced `ToolMessage | 'nope' | status: error` and `FILES: {}`. Requires a `checkpointer` and a `thread_id`.

Permission-derived interrupts get `when` predicates that make the interrupt path-aware (`_fs_interrupt.py:156-183`), with a hard-coded scope table:
```python
_FS_TOOL_PATH_ARGS = {
    "ls": ("read", "path", "bulk", None),
    "read_file": ("read", "file_path", "exact", None),
    "write_file": ("write", "file_path", "exact", None),
    "edit_file": ("write", "file_path", "exact", None),
    "delete": ("write", "file_path", "bulk", None),
    "glob": ("read", "path", "bulk", "pattern"),
    "grep": ("read", "path", "bulk", None),
}
```
Bulk tools fire whenever the search subtree could overlap the rule's anchor; a pathless `grep(path=None)` fires unconditionally. Unanchored patterns like `/**/secrets` collapse to `/` and over-fire on every bulk call — anchor your interrupt patterns (`/secrets/**`).

---

## 8. Version pins — CONFIRMED (with one divergence)

`deepagents-0.7.5.dist-info/METADATA`:
```
Requires-Python: <4.0,>=3.11
Requires-Dist: langchain<2.0.0,>=1.3.14
Requires-Dist: langchain-core<2.0.0,>=1.5.0
Requires-Dist: langchain-anthropic<2.0.0,>=1.5.4
Requires-Dist: langchain-google-genai<5.0.0,>=4.3.1
Requires-Dist: langsmith>=0.10.9
Requires-Dist: packaging>=23.2
Requires-Dist: wcmatch>=11.0
Requires-Dist: langchain-aws<2.0.0,>=1.6.2; extra == "aws"
Requires-Dist: langchain-quickjs>=0.3.3; extra == "quickjs"
Requires-Dist: av<19.0.0,>=18.0.0; extra == "video"
Requires-Dist: pillow<13.0.0,>=12.3.0; extra == "video"
```

**DIVERGES**: deepagents does **not** pin langgraph directly. langgraph arrives transitively: `langchain 1.3.14` → `langgraph<1.3.0,>=1.2.5` (resolved 1.2.10), which pulls `langgraph-checkpoint<5.0.0,>=4.1.0`, `langgraph-prebuilt<1.2.0,>=1.1.0`, `langgraph-sdk<0.5.0,>=0.4.2`.

Also note `langchain-anthropic` and `langchain-google-genai` are **hard dependencies**, not extras — both get installed unconditionally. `langchain-openai` is only an extra of `langchain`, so add it explicitly if needed. Python floor is **3.11**, and Anthropic prompt-caching middleware is appended unconditionally (no-ops for non-Anthropic models).

---

## Divergence summary for the architecture doc

1. `instructions=` does not exist — it is `system_prompt=`.
2. `backend=` takes an instance, never a factory/callable (explicit 0.7 breaking change with a runtime error message).
3. Custom middleware merges **by `.name`** — a name collision silently replaces a core middleware instead of stacking. Don't name a guard `FilesystemMiddleware`.
4. `permissions` are enforced at the tool layer only; direct backend calls and the `execute` tool bypass them completely.
5. `create_deep_agent(skills=...)` is `list[str]`; `(path, label)` tuples require constructing `SkillsMiddleware` yourself.
6. Skill discovery is one level deep per source, and skills load **once per session** (skipped if `skills_metadata` is already in checkpointed state) — a long-lived daemon thread will not pick up newly added skills without clearing that state key.
7. HITL interrupts fire in `after_model` and are **batched per AI message**; the resume list must be positionally aligned.
8. langgraph is transitive, not directly pinned.

Scratch venv and the four live smoke scripts (`smoke.py`, `smoke2.py` middleware short-circuit, `smoke3.py` HITL interrupt/resume, `smoke4.py` skills override, `smoke5.py` glob/validation) are at `/private/tmp/claude-501/-Users-smesropyan-projects-agentic-pkb/c3287252-66c3-42c0-ada1-74df4e8125ff/scratchpad/recon/`.