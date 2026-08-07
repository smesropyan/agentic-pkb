"""The custom agent state the KB middleware carry across one run (MW-5, MW-6, D-7).

Two keys, both owned by :mod:`pkb.agents.middleware`:

``kb_touched``
    Every KB-relative path a tool wrote successfully this run. ``KbMaintenanceMiddleware`` drains
    it in ``after_agent`` and hands it to :func:`pkb.core.flush` (MW-20); the runtime drains the
    same key out of the checkpoint when the run died before ``after_agent`` could fire (MW-27).

``kb_attempts``
    Blocked write attempts per KB-relative path, which is what bounds the self-correction loop to
    three tries per file per run (MW-14).

Three properties of the declaration below are load-bearing and each looks like something a reader
would "tidy up":

1. **The reducer must be the LAST metadata element of the ``Annotated``.** langgraph's
   ``_is_field_binop`` looks at ``__metadata__[-1]`` only and requires it to be callable
   (``langgraph/graph/state.py:1890``). ``PrivateStateAttr`` is a dataclass *instance* and is not
   callable, so writing ``Annotated[list[str], reducer, PrivateStateAttr]`` — the order the Layer 2
   rules document sketches — silently produces a ``LastValue`` channel: the reducer never runs, the
   second tool call in a turn overwrites the first, and nothing errors. Verified against the pinned
   langgraph 1.2.10. Both markers are order-independent for their *other* consumers
   (``deepagents.middleware._state._has_marker`` and langchain's ``_resolve_schema`` scan the whole
   metadata tuple), so last-position-for-the-reducer is free.

2. **``PrivateStateAttr`` is mandatory, and must be the singleton, not an equal
   ``OmitFromSchema(input=True, output=True)``.** ``_has_marker`` compares with ``is``. The marker
   is what makes ``deepagents``' ``task`` tool strip these keys in *both* directions
   (``subagents.py:538`` on the way in, ``subagents.py:484`` on the way back). Without it a
   delegated expert's touched paths are merged into the Librarian's state by
   ``Command(update=...)``, and the parent's ``after_agent`` flushes a second time over paths it
   never wrote — re-stamping ``updated`` on another agent's files (D-7, LB-11, MW-5).

3. **Every name used in the annotations is imported at runtime, never under ``TYPE_CHECKING``.**
   ``private_state_field_names`` resolves the schema with ``get_type_hints`` and, on ``NameError``,
   logs a warning and *skips the schema* — which silently forfeits property 2.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, NotRequired

from langchain.agents.middleware.types import AgentState, PrivateStateAttr

__all__ = [
    "KB_ATTEMPTS",
    "KB_TOUCHED",
    "KbAgentState",
    "merge_attempt_counts",
    "merge_touched_paths",
]

KB_TOUCHED = "kb_touched"
"""State key holding this run's touched KB-relative paths. Never spell the string elsewhere."""

KB_ATTEMPTS = "kb_attempts"
"""State key holding this run's blocked-attempt counts, keyed by KB-relative path (MW-14)."""


def merge_touched_paths(left: Sequence[str] | None, right: Sequence[str] | str | None) -> list[str]:
    """Merge two touched-path updates, de-duplicating and preserving first-seen order (MW-6).

    ``None`` on the right is a **reset**, not a no-op: state is checkpointed, so a plain
    append-only reducer could never clear the key and turn 2 of a thread would re-flush turn 1's
    paths — re-stamping ``updated`` on files that turn did not touch (MW-6, MW-20). ``after_agent``
    returns ``{"kb_touched": None}`` and lands here.

    De-duplication is the reducer's job rather than the caller's because two tool calls in one
    ``AIMessage`` do not see each other's updates: each writes the path it wrote, and both updates
    reach this function separately.

    A bare ``str`` on the right is accepted as a one-element update. A list is the contract, but a
    string is iterable, so without this guard a caller's mistake would splat a path into
    single-character entries and flush nothing — a silent failure rather than a loud one.
    """
    if right is None:
        return []
    incoming = [right] if isinstance(right, str) else list(right)
    merged = list(left) if left else []
    seen = set(merged)
    for path in incoming:
        if path not in seen:
            seen.add(path)
            merged.append(path)
    return merged


def merge_attempt_counts(
    left: Mapping[str, int] | None, right: Mapping[str, int] | None
) -> dict[str, int]:
    """Merge two attempt-counter updates by **adding** per path (MW-6, MW-14).

    The update is a *delta*, not an absolute count: a middleware that blocks a write on
    ``Cooking/notes/a.md`` writes ``{"kb_touched": ..., "kb_attempts": {"Cooking/notes/a.md": 1}}``
    and reads the running total back from ``state[KB_ATTEMPTS]``. Adding rather than replacing is
    what makes two blocked sibling tool calls on one path in a single ``AIMessage`` count as two
    attempts: neither sees the other's update (``request.state`` is the pre-step snapshot), so
    both would write ``1`` and a last-write-wins reducer would lose one of the three tries the
    agent is allowed (MW-14).

    ``None`` on the right resets, for the same checkpointing reason as
    :func:`merge_touched_paths`: ``before_agent`` clears the counter at run entry so "three
    attempts per file" means per graph invocation, not per thread lifetime.
    """
    if right is None:
        return {}
    merged = dict(left) if left else {}
    for path, delta in right.items():
        merged[path] = merged.get(path, 0) + delta
    return merged


class KbAgentState(AgentState):
    """``AgentState`` plus the two keys the KB middleware share (MW-5).

    Declared on every KB middleware as ``state_schema``; langchain merges the schemas of all
    middleware into the resolved graph schema (``factory.py:1154``), so declaring it more than once
    is harmless and declaring it nowhere loses both keys.

    Read the module docstring before touching either annotation — the metadata order and the
    ``PrivateStateAttr`` marker are both load-bearing and both fail silently.
    """

    kb_touched: NotRequired[Annotated[list[str], PrivateStateAttr, merge_touched_paths]]
    """KB-relative POSIX paths written successfully this run (MW-17, MW-18)."""

    kb_attempts: NotRequired[Annotated[dict[str, int], PrivateStateAttr, merge_attempt_counts]]
    """Blocked write attempts per KB-relative path this run (MW-14)."""
