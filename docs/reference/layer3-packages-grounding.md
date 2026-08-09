# Layer 3 packages — grounding

**Date**: 2026-08-07
**Why this exists**: the Layer 3 rules rest on how FastAPI, SSE, MCP and SQLite actually behave on
this machine, not on how the architecture describes them. Every claim below was executed. Four
findings changed the design, one of them a hole in a Layer 2 guarantee that only appears when the
consumer is an HTTP response.

Re-run this pass when any of those pins move.

---

# GROUNDING PASS — Layer 3 packages, executed

Repo untouched (`git status` clean). Everything below ran in `/private/tmp/claude-501/-Users-smesropyan-projects-agentic-pkb/c3287252-66c3-42c0-ada1-74df4e8125ff/scratchpad/l3/recon/` against Python 3.12.13 / uv 0.11.23.

**Headline: four of the five answers diverge from what the specs assume, and one of them silently breaks arch §7's flush guarantee.**

---

## 1. Web framework — **CONFIRMED**, with one structural surprise

`uv add --no-sync fastapi uvicorn sse-starlette mcp httpx-sse` on a copy of this repo's `pyproject.toml` + `uv.lock`: **101 packages resolved in 371ms. Zero existing pins moved. `pydantic` stays 2.13.4.** There is no langchain/pydantic conflict.

```
fastapi 0.141.1   starlette 1.4.1   uvicorn 0.52.1   sse-starlette 3.4.8   mcp 2.0.0
```
New transitives: `mcp-types 2.0.0`, **`httpx2 2.9.1`**, `httpcore2 2.9.1`, `jsonschema 4.26.0`, `referencing`, `rpds-py`, `opentelemetry-api 1.44.0`, `pyjwt 2.13.0`, `python-multipart`, `annotated-doc`, `attrs`, `truststore`.

I installed the whole set into a copy of the repo and ran the suite: **1155 passed in 66.55s.** Adding Layer 3's deps breaks nothing in Layers 1–2.

**The surprise: `httpx` has gone 2.x under a new distribution name, `httpx2`.** `mcp 2.0.0` requires `httpx2>=2.5.0`, and `starlette.testclient` now does:

```python
import httpx2 as httpx
...
"Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead."
```

Both are installed and they do **not** collide — `httpx` dist ships module `httpx`, `httpx2` dist ships module `httpx2` (verified via `importlib.metadata.distribution(...).files`). So `pkb.agents.models`' `httpx.ConnectError` predicate keeps working untouched, while Layer 3's tests and clients bind `httpx2`. Two HTTP clients in one venv is intentional here, not an accident — worth a line in the rules doc so nobody "cleans it up".

`fastapi 0.141.1` requires `starlette>=0.46.0` and resolves to `starlette 1.4.1` — Starlette is 1.x now, and that matters for §2.

---

## 2. SSE — **sse-starlette is the right answer, but not for the reason you'd assume.** And there is a **DIVERGENCE that costs a flush.**

### Plain `StreamingResponse` is *currently* enough — by accident of a version number

`starlette/responses.py:265` (1.4.1):

```python
spec_version = tuple(map(int, scope.get("asgi", {}).get("spec_version", "2.0").split(".")))
if spec_version >= (2, 4):
    try:
        await self.stream_response(send)
    except OSError:
        raise ClientDisconnect()
else:
    async with create_collapsing_task_group() as task_group:
        task_group.start_soon(wrap, partial(self.stream_response, send))
        await wrap(partial(self.listen_for_disconnect, receive))
```

`uvicorn 0.52.1` declares **`spec_version: "2.3"`** (`h11_impl.py:207`, `httptools_impl.py:228`), so `StreamingResponse` takes the `listen_for_disconnect` branch and does abort. Driven directly at `spec_version="2.4"` it **ignores `http.disconnect` entirely** and kept generating to my 3s timeout:

```
=== client disconnects after 2 frames, asgi spec_version=2.0 ===
  -- StreamingResponse      trace: [...,'plain:cancelled','plain:finally'] (len=5)
  -- EventSourceResponse    trace: [...,'lib:cancelled','lib:finally'] (len=5)
=== client disconnects after 2 frames, asgi spec_version=2.4 ===
  -- StreamingResponse      app task raised: TimeoutError
                            trace: ['plain:yield:56',...,'plain:cancelled'] (len=62)
  -- EventSourceResponse    trace: [...,'lib:cancelled','lib:finally'] (len=5)
```

`EventSourceResponse` listens for the disconnect itself and is correct at both spec versions. **That is the durable reason to take `sse-starlette` 3.4.8** — not formatting convenience. Plus it gives you `ping` keep-alives, `cache-control: no-store`, and a `shutdown_event` + `shutdown_grace_period` so generators can exit cooperatively on daemon shutdown instead of eating a `CancelledError`.

### What the client receives (real uvicorn, POST → SSE)

```
   200 text/event-stream; charset=utf-8 | cache-control: no-store | connection: keep-alive
   raw: b'event: message.delta\r\ndata: {"run_id": "r1", "text": "tok0"}\r\n\r\n'
   raw: b'event: run.end\r\ndata: {"run_id": "r1", "final_text": "done"}\r\n\r\n'
```
Mid-stream failure keeps the 200 — a `run.error` **must** be a frame, never a status:
```
   status: 200
   event='message.delta' ... event='run.error' data={'message': 'provider timeout', 'retryable': True}
```
Keep-alives are comment frames the TUI must skip: `b': ping - 2026-08-07 19:55:44.031172+00:00\r\n\r\n'`.

**`httpx-sse` is unnecessary.** `httpx2` ships SSE natively (`httpx2._sse`, derived from httpx-sse, MIT): `httpx2.EventSource`, `ServerSentEvent`, `SSEError`. Decoding works straight off a `client.stream(...)` response. Don't add the dependency.

### DIVERGENCE — the SSE disconnect **destroys the runtime's `try/finally` flush**

Arch §7's correction and Layer 2 decision D say the runtime's `try/finally` delivers "exactly one flush per run on both paths". **It does not survive an SSE client hangup.**

Cause: `asyncio.Task.cancel()` is edge-triggered — one `CancelledError` is delivered and awaits in a `finally` then run normally. An **anyio cancel scope** (what both `StreamingResponse` and `EventSourceResponse` use to abort on `http.disconnect`) is level-triggered: *every* await inside the `finally` raises again. Generic proof (`probe/t_finally.py`, real uvicorn, hard socket close):

```
  /naive       -> ['naive:finally-entered', 'naive:flush-raised:CancelledError']
  /shielded    -> ['shielded:finally-entered', 'shielded:flush-raised:CancelledError', 'shielded:FLUSHED']
  /detached    -> ['detached:finally-entered', 'detached:FLUSHED']
  /sse_naive   -> ['sse_naive:finally-entered', 'sse_naive:flush-raised:CancelledError']
```

Then the decisive run — the **real `PkbRuntime`**, real scripted model, real KB, real uvicorn, real socket close (`repo/probe_sse_runtime.py`):

```
[A. StreamingResponse(rt.run(...))]
  note written before hangup : True
  flush reports with stamps  : []
  index.md lists the note    : False        <-- derived files stale
[B. EventSourceResponse(rt.run(...))]
  note written before hangup : True
  flush reports with stamps  : []
  index.md lists the note    : False        <-- same
[C. StreamingResponse over an asyncio.Task pump]
  note written before hangup : True
  flush reports with stamps  : [['Cooking/notes/reverse-sear.md']]
  index.md lists the note    : True         <-- flush landed
```

This is invisible to Layer 2's own tests, and I checked why: `rt.cancel(run_id)` and a plain `asyncio.Task.cancel()` **both flush correctly** (I ran them against the real runtime: `stamped: [['Cooking/notes/reverse-sear.md']]`). Only the anyio path loses it. `_drive`'s exit chain (`runtime.py:1312-1319`) is `await self._flush_pending(...)` / `await queue.put(None)` with no shield, and `_flush_pending` swallows `except Exception` — which `CancelledError` is not, since it's a `BaseException` on 3.12.

**Corrected approach for Layer 3:** never hand `rt.run(...)` straight to an SSE response. Drive it in a plain `asyncio.Task` that pushes into an `asyncio.Queue`; the SSE generator reads the queue and, in its `finally`, issues exactly **one** `task.cancel()`. That converts the transport's level-triggered cancel into the edge-triggered one the runtime is already correct under. Proven above (case C). Arch §8's "Client disconnects mid-approval" row and README Part 4 both depend on this. RT-7's startup `regenerate_all` limits the blast radius to "stale until the next restart", but that is not the guarantee the docs claim.

Related, and *not* a bug: after a disconnect the `(agent_id, thread_id)` active-run slot stays held until the in-flight model call actually winds down (measured: released between +3s and +5s, then a second run was admitted). Correct behaviour — but Layer 3 must expect a legitimate 409 on an immediate reconnect and say so in the TUI rather than treating it as an error.

---

## 3. MCP — **DIVERGES from arch §6 in four ways**, all fixable

**Package: the official `mcp` SDK, at version 2.0.0.** Do not add `fastmcp` — FastMCP is now in-tree as `mcp.server.MCPServer` (`mcp/server/mcpserver.py`), with `@server.tool()`, `@server.resource()`, `@server.prompt()`, `streamable_http_app()`, `session_manager`.

Working exchange over the mount (raw JSON-RPC, real uvicorn — `probe/t_mcp.py`):

```
POST /mcp2 initialize -> 200
  mcp-session-id: def8882fed92461295b852b01b3f8022
  content-type: text/event-stream
  body: event: message
data: {"jsonrpc":"2.0","id":1,"result":{"capabilities":{...},"protocolVersion":"2025-06-18","serverInfo":{"name":"pkb","version":"0.1.0"}}}
POST notifications/initialized -> 202
POST tools/list -> 200
data: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"description":"Query the Librarian or a named expert.","inputSchema":{"properties":{"agent_id":{...},"question":{...}},"required":["agent_id","question"],"type":"object"},"name":"pkb_ask","outputSchema":{...}}]}}
POST tools/call -> 200
data: {"jsonrpc":"2.0","id":3,"result":{"content":[{"text":"[topic/cooking] answered: ribeye?","type":"text"}],"isError":false,"structuredContent":{"result":"[topic/cooking] answered: ribeye?"}}}
```

And through the official SDK client:
```
initialize: name='pkb' version='0.1.0'  protocol 2025-11-25
tools: [('pkb_ask', ['agent_id', 'question'])]
call_tool -> [TextContent(type='text', text='[topic/cooking] answered: ribeye?')] | structured: {'result': ...}
```

### The four divergences

**(a) `app.mount("/mcp", ...)` produces a 307 on `/mcp`.** The sub-app's router redirects to `/mcp/`. The SDK client follows it; a stricter client may not, and arch §6's URL is `/mcp` exactly. Mount the ASGI handler as a bare `Route` on the parent instead:

```python
mcp_server.streamable_http_app(streamable_http_path="/mcp", host="0.0.0.0")  # for its side effect
app.router.routes.append(
    Route("/mcp", endpoint=StreamableHTTPASGIApp(mcp_server.session_manager),
          methods=["GET", "POST", "DELETE"])
)
```
Verified: exact URL, no redirect, no private attributes (`streamable_http_app()` is what constructs and stashes the session manager; `session_manager` raises `RuntimeError` before it is called).

**(b) `Mount` does not run the sub-app's lifespan.** `streamable_http_app()` returns `Starlette(..., lifespan=lambda app: session_manager.run())` — mounting throws that away and nothing serves. The daemon lifespan must drive it:
```python
@contextlib.asynccontextmanager
async def lifespan(app): 
    async with mcp_server.session_manager.run():
        yield
```

**(c) `session_manager.run()` may be called exactly once per instance** — `RuntimeError: StreamableHTTPSessionManager .run() can only be called once per instance.` This forces an **app factory**; a module-level `app = FastAPI()` cannot be entered twice, which every test that uses the lifespan will do.

**(d) DNS-rebinding lockdown is on by default and rejects the test client.** `streamable_http_app(host=...)` defaults to `"127.0.0.1"`, which auto-enables `allowed_hosts=["127.0.0.1:*","localhost:*","[::1]:*"]`:
```
  host='127.0.0.1'  base=http://testserver      -> 421 'Invalid Host header'
  host='127.0.0.1'  base=http://127.0.0.1:8000  -> 200
  host='0.0.0.0'    base=http://testserver      -> 200
```
Keep it on in the daemon (the daemon binds localhost, arch §10) and pin the test client to `base_url="http://127.0.0.1"`.

**Transport matches arch §6** — streamable HTTP, mountable, POST/GET/DELETE on one path. Responses default to `text/event-stream`; pass `json_response=True` for plain JSON. Note the SDK's own 2.0 renames: model fields are snake_case (`server_info`, `input_schema`, `structured_content`) while the wire stays camelCase, and `streamablehttp_client` → **`streamable_http_client`**.

---

## 4. Testing — **DIVERGES from arch §9.** `TestClient` cannot test SSE.

Arch §9 says "`pkb.server` — FastAPI `TestClient` against a stub `PkbService`". That works for routes. It **cannot** test streaming or disconnect, because `starlette.testclient._TestClientTransport.handle_request` does:

```python
raw_kwargs["stream"] = httpx.ByteStream(raw_kwargs["stream"].read())
```

Measured: `client.stream("GET", "/runs/plain?n=50&delay=0.01")` returned **all 50 frames in one chunk**, and breaking after chunk 1 left the server generator running to `plain:done`. No disconnect, no incrementality.

And `httpx2.ASGITransport` buffers too — same shape (`body_parts` accumulated, response built only after `await self.app(...)` returns). So does httpx 0.28's. Four frames at 0.1s delay arrived as one chunk at `t=0.41`.

**The recipe — three tools, one job each.** Written as a real pytest file and run green under this repo's own `pytest-asyncio 1.4` (`repo/tests/server/test_recipe.py`, 4 passed; no `anyio` plugin needed, no network, no key):

1. **`TestClient`** — non-streaming routes, and SSE *frame content* (you get the concatenated body; split on `\r\n\r\n` and assert the event names/payloads). Pin `base_url="http://127.0.0.1"`.
2. **`httpx2.ASGITransport`** — the MCP mount end-to-end, in-process. `streamable_http_client(url, http_client=...)` accepts your ASGI-backed client, so the official SDK drives the mounted server with no socket:
   ```python
   async with app.router.lifespan_context(app):
       async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                     base_url="http://pkb.test") as http_client:
           async with streamable_http_client("http://pkb.test/mcp", http_client=http_client) as streams:
               async with ClientSession(streams[0], streams[1]) as session:
                   await session.initialize(); await session.call_tool(...)
   ```
3. **A ~40-line raw ASGI driver** — build the scope with `spec_version "2.3"` (uvicorn's value), `create_task(app(scope, receive, send))`, read `http.response.body` messages off a queue, and return `{"type": "http.disconnect"}` from `receive` to hang up. This is the **only** in-process way to assert (a) frames arrive over time and (b) the generator's `finally` runs on disconnect. It is also how you regression-test the §2 flush finding without spinning uvicorn.

Use uvicorn-in-a-task only for the handful of tests that must prove real-socket behaviour.

---

## 5. SQLite alongside the checkpointer — **RT-4 CONFIRMED, but conditional on ordering**

### WAL is set by `setup()`, and `setup()` is lazy

`AsyncSqliteSaver.from_conn_string` is just `aiosqlite.connect(...)`. The `PRAGMA journal_mode=WAL;` lives in `setup()`. Measured timeline:

```
== A. journal_mode timeline ==
  right after from_conn_string() : delete
  a Layer-3 connection sees      : delete
  after await saver.setup()      : wal
  after the first run            : wal
== F. reopen the same file ==
  journal_mode before setup on a reopened WAL file: wal
```

**Actionable constraint RT-4 doesn't state: Layer 3 must open its `threads`/scan-queue connection *after* `PkbRuntime.open()`.** Open it earlier and you are talking to a rollback-journal file, where a reader blocks a writer. (An `aiosqlite` connection opened afterwards reports `wal` correctly — verified.)

### Under WAL, sharing the file works

```
== B. WAL: reader + second writer during a run ==
  checkpoint rows observed growing: 2 -> 8 (30 samples)
  `threads` commits during the run: 30; errors: none
== D. Layer 3 hammering writes with busy_timeout=1ms ==
  ok=573 locked=2 first=OperationalError: database is locked
```
Reading `checkpoints` mid-run is clean. Writes are clean at the default 5000ms `busy_timeout` (both connections report it). At 1ms you get occasional `database is locked` — so keep the default and wrap Layer 3 writes in a small retry; do not lower it.

### What breaks

```
== C. Layer 3 holds BEGIN IMMEDIATE across an await ==
  checkpointer run -> OperationalError: database is locked after 16.09s
```
**One rule, and it is the whole answer to "what breaks under concurrent access": never hold a write transaction open across an await.** WAL has exactly one writer; a Layer 3 handler that does `BEGIN IMMEDIATE` → `await something` → `COMMIT` kills every in-flight run with a `database is locked` after the busy timeout, which surfaces to the user as a failed run with a written file and no flush. Short autocommit statements, always.

### aiosqlite vs sqlite3-on-a-thread — **use `aiosqlite`**

```
aiosqlite 0.22.1 — 300 concurrent upserts on ONE shared conn during a live run
  elapsed 1.24s, rows=300, failures=0
  L3 aiosqlite journal_mode: wal ; reading `checkpoints`: [('X',''),('X',''),('X','')]
```
It serializes access internally (its `Connection` is no longer a `threading.Thread` subclass in 0.22), so 300 unsynchronised coroutines on one connection are safe with no lock of your own. Plain `sqlite3` on `asyncio.to_thread` also works (200 concurrent upserts during a run, clean) but needs `check_same_thread=False` **plus your own `asyncio.Lock`** to avoid interleaving on one connection — that's re-implementing aiosqlite. It is already a transitive dependency via `langgraph-checkpoint-sqlite`, so it costs nothing. Keep RT-4's rule that it is Layer 3's **own** connection, never the saver's.

---

## Bonus: invariant I2 is mechanically safe with these packages

- The entire Layer 3 dependency set (`fastapi`, `starlette`, `sse_starlette`, `uvicorn`, `mcp`, `mcp.server.*`, `mcp.client.streamable_http`, `httpx2`, `anyio`, `aiosqlite`) imports **zero** harness modules: `harness modules loaded: NONE`.
- The import-linter extension works. On a copy of the repo with stub `pkb/service.py` + `pkb/server/app.py` importing fastapi/mcp/sse-starlette, changing one line:
  ```toml
  source_modules = ["pkb.contracts", "pkb.service", "pkb.server"]
  ```
  → `Analyzed 84 files, 417 dependencies. Contracts: 3 kept, 0 broken.` And it **catches** a planted violation, including transitively:
  ```
  pkb.server is not allowed to import langgraph:
  -   pkb.server.app -> pkb.agents.runtime (l.10)
      pkb.agents.runtime -> langgraph (l.78, l.79, l.80, l.81, l.82)
  ```
  The existing `layers` contract needs no edit — `(pkb.server) | (pkb.tui)` and `(pkb.service)` are already optional.

---

## Artifacts

| Path | What it proves |
|---|---|
| `/private/tmp/claude-501/-Users-smesropyan-projects-agentic-pkb/c3287252-66c3-42c0-ada1-74df4e8125ff/scratchpad/l3/recon/probe/` | throwaway venv with the resolved set; `pyproject.toml`/`uv.lock` hold the resolution |
| `…/probe/t_testclient.py` | TestClient buffers; early break is not a disconnect |
| `…/probe/t_asgi.py`, `…/probe/t_asgi_raw.py` | ASGITransport buffers; raw driver streams and disconnects; `spec_version` 2.0 vs 2.4 |
| `…/probe/t_uvicorn.py`, `…/probe/t_sse_client.py` | real-socket SSE, ping frames, mid-stream `run.error`, hangup |
| `…/probe/t_mcp.py`, `…/probe/t_mcp_inproc.py` | mount styles, 307, raw JSON-RPC, SDK client, ASGI-transport client, one-shot session manager |
| `…/probe/t_finally.py` | anyio level-triggered cancel kills `await` in `finally`; shield/detach fix |
| `…/probe/t_sqlite2.py`, `…/probe/t_sqlite3.py` | WAL timeline, contention, aiosqlite vs sqlite3 |
| `…/recon/repo/probe_sse_runtime.py` | **the flush loss and its fix**, against the real `PkbRuntime` |
| `…/recon/repo/tests/server/test_recipe.py` | the Q4 recipe, 4 tests green under this repo's pytest-asyncio |