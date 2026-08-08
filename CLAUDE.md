# agentic-pkb

The **Personal Knowledge Base** — a structured markdown tree that only agents write to, with the
mechanical structure enforced in code and the meaning curated in dialog with the human.

## Where the design lives

| Document | What it fixes |
|----------|---------------|
| `README.md` | *What* the PKB is: tree structure, frontmatter, tags, conflict handling, agent roles. The human's design spec. |
| `docs/superpowers/specs/2026-08-06-pkb-architecture-design.md` | *How the system is built*: three layers, their seams, invariants I1–I3, build order. Approved. |
| `docs/superpowers/specs/2026-08-06-pkb-core-layer1-rules.md` | Every Layer 1 rule with a stable id (`FM-1`, `PA-3`, `VA-12`, `GE-4`, `MA-3`, `SC-9`). The contract `pkb.core` is tested against. |
| `docs/superpowers/specs/2026-08-06-pkb-agents-layer2-rules.md` | Every Layer 2 rule (`RT-*`, `RG-*`, `MW-*`, `LB-*`, `PR-*`, `SK-*`): runtime, registry, permissions, gates, middleware, the routing workflow, the shipped prompts and skills. The contract `pkb.agents` is tested against. |
| `docs/superpowers/specs/2026-08-07-pkb-service-server-layer3-rules.md` | Every Layer 3 rule (`SV-*`, `RO-*`, `SS-*`, `AP-*`, `ST-*`, `MC-*`, `PK-*`): the service protocol, HTTP routes, SSE, approvals, packs, the MCP mount and the Telegram wiring. Built. |
| `docs/superpowers/specs/2026-08-07-large-source-ingestion.md` | Sources that do not fit a turn: one file per source with the arguments as sections, `.inbox` staging, re-ingestion and reconciliation. Crosses all three layers. Designed, not built. |
| `docs/reference/deepagents-0.7.5-api-recon.md` | Verified signatures of the harness Layer 2 will use. |

## Build order (architecture §11)

1. `pkb.core` — schema, validator, generators, scaffolder. **Built.** See §7 "As built" in the
   Layer 1 rules for what diverged, and `src/pkb/core/__init__.py` for the surface Layer 2 imports.
2. `pkb.agents` — runtime, registry, expert factory, Librarian, middleware, default skills, and the
   Librarian's routing workflow (`routing.py`: classify, fan out, attributed merge). **Built and
   merged.** See §2 of the Layer 2 rules for where the harness diverges from the architecture doc,
   and `docs/reference/deepagents-0.7.5-harness-grounding.md` for the executed evidence.
3. `pkb.service` + `pkb.server` + `pkb.packs` — the protocol, the `threads`/`pkb_proposals` tables,
   the run supervisor, HTTP routes, SSE, the MCP mount and the daemon. **Built.** See §8 "As built"
   in the Layer 3 rules for what the grounding pass corrected and the thirteen defects the suite
   found.
4. `pkb.tui` + `pkb.clients.approval`. **← next**
5. `pkb.server.telegram` — hosted in the daemon, one Telegram channel per expert.

**The daemon owns runs.** A run is a plain `asyncio.Task` publishing into a per-run hub; an HTTP
response subscribes to it. A dropped connection **detaches** — it never cancels — because D2's whole
promise is that a turn outlives the terminal that started it, and an ingestion turn killed because a
phone crossed a tunnel is that promise broken. Cancellation is a deliberate act with its own route.
Run it with `python -m pkb.daemon <kb-root>`; it binds localhost and has no auth (arch §10).

Large-source ingestion has its own spec and is not built. It changes **nothing** in `pkb.core`: one
physical file per source with the arguments as sections inside it is the shape Layer 1 already
implements. What it adds is in `pkb.agents` — per-kind extraction skills and a resumable, chunked
workflow that walks a source through a windowed reader rather than a whole-file `read_file`.

## Models

| | Model | Why |
|---|---|---|
| **Default** | `ollama:deepseek-v4-flash:cloud` | 5/5 on a five-task live evaluation of this workload, ~16s per filing turn, cheap. |
| **Fallback** | `ollama:gemma4:31b` | The **local** tag — no `-cloud` suffix. Also 5/5, Low usage weight, and never metered, so the knowledge base keeps working when the cloud quota is exhausted or the endpoint is down. |

**The fallback is a degraded backup, not a drop-in.** Measured on this machine: the same filing turn
takes **284 seconds locally** against ~16 seconds on the cloud default — about 18× slower, because a
turn is 8–12 model calls and each local call is ~25s over a growing context. It works; it is not
fast. Anything with a deadline in front of it — a TUI turn, a Telegram reply, the conflict-scan
worker — needs a timeout sized for the fallback rather than for the primary, and the human should be
told the run switched, because the first symptom of a silent failover is a turn that looks hung.
The same weights on `gemma4:31b-cloud` ran the eval at ~32s per task, so this is the local hardware,
not the model.

Both are configured on `RuntimeConfig` (`default_model`, per-agent `models`, `fallback_model`) and
reach the graphs through `AgentRegistry` — the model is a **registry** concern (RG-21): no
transport, route or channel picks one, and it is never read from KB content. `fallback_model=None`
turns the failover off.

**Prerequisite: `ollama pull gemma4:31b`.** It is not pulled by default and it is a ~20GB download,
so nothing here pulls it and nothing builds it until the day it is needed. If that day arrives and
it is missing, the error is a `ModelNotInstalledError` naming this exact command.

The failover is `pkb.agents.models.FallbackChatModel`. Two things about it are load-bearing:

- **Only quota, concurrency and availability fail over** — 429, 408, 5xx, connection and timeout
  errors. A malformed request, a missing model or a content error propagates untouched, because the
  second model would fail identically and two wrong answers are worse than one clear failure.
- **Every failover is logged at warning level**, naming both models and the reason, at most once per
  outage. A silent failover means the human never learns their quota ran out and quietly gets a
  different model's judgment.

The deployment is an Ollama **Pro** plan: three concurrent cloud models, usage weighted per model,
limits resetting on 5-hour and weekly windows. Overflow queues, then rejects with 429; a 502 means a
cloud model was unreachable.

## Conventions

- **Rule ids are the contract.** A docstring or test that implements a rule cites its id. Changing
  behaviour means changing the rule in the spec first.
- **Findings, not exceptions**, for content defects. Exceptions are for unusable inputs only.
- **KB-relative POSIX strings** for every path in a `Finding`, a model field, or rendered output.
- **Layer 1 is plain Python**: no LLM, no network, no subprocess, no database, no git. Its whole test
  suite runs on `tmp_path`.
- **Only Layer 1 writes derived files** (`index.md` anywhere, root `tags.md`) — invariant I3.
- **Derived output carries no timestamps or counts.** Byte-idempotence is what keeps a flush from
  churning the tree on every turn.
- Layer 1 flags; it never repairs. Nothing moves or deletes human content — there is no undo (D6).
- **The shipped skills are mounted, not seeded.** They live in package data
  (`src/pkb/agents/skills/`) and are mounted read-only ahead of the knowledge base's own `skills/`,
  which stays empty until the human adopts one. Adoption is a permanent fork: the copy shadows the
  shipped default and later improvements stop reaching it. `skills/**` is a third file class —
  exempt from PKB frontmatter and from every index and tag artifact.
- **Inbound sources stage in `<kb>/.inbox/`.** Dot-prefixed, so Layer 1's walk already skips it —
  verified: nothing from it is recorded, validated, indexed or tagged. A *path* comes in rather than
  a paste; anything binary is extracted to text and **both are kept**, the extraction being what the
  ingestion loop reads and the original being what a topic gets a copy of. The tool stages the file
  and the agent only reads it — an expert's writes are confined to its own topic subtree (RT-15).
- **A topic gets a copy of a source only by ingesting it gainfully** — at least one insight. Zero
  insights leaves no folder, no stub and no copy: no trace at all, rather than an empty folder
  implying the source was considered and is somehow relevant.

## Commands

```
make install   # uv sync
make test      # pytest
make lint      # ruff check + format --check
make types     # mypy (strict)
make layers    # import-linter — enforces the layer invariants
make check     # all of the above
```

Always run tools through `uv run`; the system Python is 3.9 and will not work.
