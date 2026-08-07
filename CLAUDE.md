# agentic-pkb

The **Personal Knowledge Base** — a structured markdown tree that only agents write to, with the
mechanical structure enforced in code and the meaning curated in dialog with the human.

## Where the design lives

| Document | What it fixes |
|----------|---------------|
| `README.md` | *What* the PKB is: tree structure, frontmatter, tags, conflict handling, agent roles. The human's design spec. |
| `docs/superpowers/specs/2026-08-06-pkb-architecture-design.md` | *How the system is built*: three layers, their seams, invariants I1–I3, build order. Approved. |
| `docs/superpowers/specs/2026-08-06-pkb-core-layer1-rules.md` | Every Layer 1 rule with a stable id (`FM-1`, `PA-3`, `VA-12`, `GE-4`, `MA-3`, `SC-9`). The contract `pkb.core` is tested against. |
| `docs/reference/deepagents-0.7.5-api-recon.md` | Verified signatures of the harness Layer 2 will use. |

## Build order (architecture §11)

1. `pkb.core` — schema, validator, generators, scaffolder. **Built.** See §7 "As built" in the
   Layer 1 rules for what diverged, and `src/pkb/core/__init__.py` for the surface Layer 2 imports.
2. `pkb.agents` — runtime, registry, expert factory, Librarian, middleware, default skills.
   **Built.** See §2 of the Layer 2 rules for where the harness diverges from the architecture doc,
   and `docs/reference/deepagents-0.7.5-harness-grounding.md` for the executed evidence.
3. `pkb.service` + `pkb.server` — the protocol, HTTP routes, SSE, MCP mount. **← next**
4. `pkb.tui` + `pkb.clients.approval`.
5. `pkb.server.telegram`.

## Models

| | Model | Why |
|---|---|---|
| **Default** | `ollama:deepseek-v4-flash:cloud` | 5/5 on a five-task live evaluation of this workload, ~16s per filing turn, cheap. |
| **Fallback** | `ollama:gemma4:31b` | The **local** tag — no `-cloud` suffix. Also 5/5, Low usage weight, and running it locally is never metered, so the knowledge base keeps working when the cloud quota is exhausted or the endpoint is down. |

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
