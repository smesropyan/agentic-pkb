"""Layer 2 — the agent layer.

Hosts the Librarian and the Topic Experts on the deepagents harness. Imports :mod:`pkb.core` and
the harness; nothing below it may import this package (invariants I1, I2).

The public surface is deliberately **one class** (§5.2). :class:`~pkb.agents.runtime.PkbRuntime`
owns the process's singletons and is the only sanctioned way to execute a graph, so everything a
transport needs — the catalog, runs, approvals, history, scans, regeneration — hangs off it.

Everything a transport *binds against* lives in :mod:`pkb.contracts`, not here, and that separation
is structural rather than stylistic: importing this package imports the runtime, which imports
deepagents, langgraph and langchain. A type re-exported from here would drag the whole harness into
``pkb.server`` through a single ``from pkb.agents import …`` and make invariant I2 fiction (decision
B, D-20). So ``AgentEvent``, ``ApprovalRequest``, ``Decision``, ``AgentDescriptor``, the typed errors
and the rest are imported from ``pkb.contracts``, which is a leaf module with nothing below it.
"""

from pkb.agents.runtime import PkbRuntime, RuntimeConfig

__all__ = ["PkbRuntime", "RuntimeConfig"]
