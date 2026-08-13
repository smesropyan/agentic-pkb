# agentic-pkb

The Personal Knowledge Base (PKB) is an AI-assisted expert system that fuses theoretical, practical and procedural
knowledge for the topics the operator works in. `README.md` is the specification: the goal, the three pillars,
sessions and the self-learning loop. `DESIGN.md` is the technical design that serves it, in ten sections: the tree,
sessions, the agents, the skills, the workflows, conflict handling, the self-learning loop, and how work is handed
out.

## The ruling (2026-08-13)

The operator ruled: re-implement, not reconcile. `src/` and `tests/` implement the design that `README.md` and
`DESIGN.md` superseded on this date. They are reference material and a parts bin, salvaged module by module only as
a phase rebuilds it, never trusted as a base to patch. The archived specs under
`docs/superpowers/specs/superseded/` carry the measurements and the defect history that priced the new design; no
rule id in them binds the new build. Behaviour questions are answered from `DESIGN.md`, never from the old code.

## The roadmap

`docs/superpowers/plans/2026-08-13-reimplementation-roadmap.md` is the build plan. Phase 0 grounds the repo, Phase 1
builds the tree, Phase 2 builds sessions, Phase 3 builds the agents and their skills, Phase 4 builds the workflows
and the self-learning loop, and Phase 5 hands work out and rewires the transports. Each phase gets its own plan,
written at phase start with the superpowers writing-plans skill against the design and the then-current tree, and
executed subagent-driven. `docs/how-to/` still documents the superseded build; it is rewritten from a clean clone
after Phase 5.

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

## Conventions that survive

- Rule ids are the contract. A docstring or test that implements a rule cites its id. New ids are minted per design
  section, per the roadmap: `T-*` for §1 The Tree, `S-*` §2 Sessions, `A-*` §3 The Agents, `K-*` §4 The Skills,
  `W-*` §5 The Workflows, `C-*` §6 Conflict Handling, `L-*` §7 The Self-Learning Loop, `H-*` §8 Handing Work Out.
- Findings, not exceptions, for content defects. Exceptions are for unusable inputs only.
- KB-relative POSIX strings for every path in a finding, a model field, or rendered output.
- Derived files are byte-idempotent and carry no timestamps or counts. Only the mechanical layer writes them.
- There is no undo. Nothing moves or deletes operator content.
- `.env` stays gitignored at mode 600. A real environment variable always wins over a line in it.
- The repo is public. No credentials in any committed file, ever.

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
