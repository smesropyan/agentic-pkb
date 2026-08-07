"""The once-per-turn maintenance flush — ``KbMaintenanceMiddleware`` (MW-20 … MW-30).

Layer 1 owns every mechanic here: :func:`pkb.core.flush` bumps ``updated``, regenerates the derived
files and returns the conflict-scan requests as pure data (MA-1 … MA-13). This middleware is only
the *timing and plumbing* around that one call — when it runs, what paths it is given, who takes the
lock, and where the report goes. There is no second implementation of anything Layer 1 does.

Four properties are load-bearing and each of them exists because of a verified harness behaviour
rather than a design preference:

**The flush runs once per turn, in ``after_agent``, never per write (MW-20).**
Regeneration is whole-tree: doing it after each ``write_file`` would rewrite the root ``tags.md``
several times in one turn and make the "one flush" cost linear in the model's chattiness. The
``today`` used for the ``updated`` stamp is injected (``clock``) rather than read from the wall
clock, which is what makes a same-day double flush provably idempotent and a date-boundary test
possible at all.

**``after_agent`` alone does NOT deliver arch §7's "the flush runs on both success and failure"
(D-1, MW-26).**
It is an ordinary graph node on the normal exit edge (``factory.py:1589``); an exception anywhere in
the run aborts the pregel superstep and the node is never reached — executed against the pin across
four failure shapes, with the written file on disk and no flush in any of them. The tools node has
however already committed its state update, so the touched paths survive **in the checkpoint**. The
division of labour is therefore:

* this middleware covers the happy path, and clears the key when it is done;
* :mod:`pkb.agents.runtime` wraps every graph execution in ``try/finally`` and, on the way out,
  reads ``state.KB_TOUCHED`` back out of ``graph.aget_state(cfg).values`` and calls
  :meth:`KbMaintenanceMiddleware.aflush_pending` (MW-27).

That makes ``KB_TOUCHED`` a **published contract**, not a private detail: it is read from outside
the graph, by another module, after the run has already failed. Renaming it or making it non-private
to the checkpoint breaks the failure path silently — the tree simply goes stale.

**The two paths must not both flush (MW-28).**
``after_agent`` returns ``{KB_TOUCHED: None}``, so a normally-completed run leaves an empty set
behind and :meth:`aflush_pending` — which refuses to flush an empty set — is a no-op. That empty
set *is* the run-scoped sentinel. A double flush would be harmless to the tree (regeneration is
skip-if-identical, GE-8) but would enqueue every conflict scan twice.

**``before_agent`` resets the set at run entry (MW-6).**
State is checkpointed. Without the reset, turn 2 of a thread would re-flush turn 1's paths and
re-stamp ``updated`` on files it never touched. The reset deliberately does *not* re-run on an
interrupt resume (``factory.py:1699-1712``), so paths written before an approval survive the pause
and are flushed once when the human decides (MW-29).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Protocol, cast

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from pkb.agents.middleware.state import KB_TOUCHED, KbAgentState, merge_touched_paths
from pkb.agents.paths import to_kb_relative
from pkb.contracts import ScanQueue
from pkb.core import Finding, FlushReport, ScanRequest, Severity, flush
from pkb.core.models import FileRole
from pkb.core.paths import classify

__all__ = [
    "MUTATING_TOOLS",
    "NULL_WRITE_LOCK",
    "FlushSink",
    "KbMaintenanceMiddleware",
    "KbWriteLock",
    "SupportsInvalidate",
]


MUTATING_TOOLS: Final[frozenset[str]] = frozenset({"write_file", "edit_file", "delete"})
"""The deepagents filesystem tools whose success changes the tree (MW-18, MW-19).

``delete`` is the reason this middleware records touched paths at all: MW-7 confines
``KbValidationMiddleware`` to ``write_file``/``edit_file``, and a note removed from disk but left
listed in ``index.md`` and ``tags.md`` is exactly the stale-derived-file state the flush exists to
prevent (MW-19). The two write tools are recorded here as well so that the maintenance guarantee
does not depend on a sibling middleware being present or on where it sits in the stack; the
touched-path reducer de-duplicates (MW-6), so a path recorded by both is one entry.
"""

_REGISTRY_ROLES: Final[frozenset[FileRole]] = frozenset(
    {FileRole.EXPERT, FileRole.SKILL, FileRole.TOPIC_OVERVIEW}
)
"""File roles whose change invalidates a compiled graph (MW-30).

Read through :func:`pkb.core.paths.classify` rather than matched against ``"expert.md"`` /
``"skills/"`` / ``"topic.md"`` literals: Layer 1 owns the location table (VA-13), and a second copy
of it here would drift the first time a role moves.
"""


FlushSink = Callable[[FlushReport], None]
"""Where a :class:`~pkb.core.models.FlushReport` goes once the flush is done (MW-24).

The report is never discarded: ``findings`` carry broken links, orphans and write failures that are
invisible anywhere else outside the topic index, and Layer 3 is the layer with a log and an event
stream. Injected rather than imported so this module keeps no transport dependency (I2).
"""


class SupportsInvalidate(Protocol):
    """The slice of ``AgentRegistry`` this middleware is allowed to touch (MW-30, RG-17)."""

    def invalidate(self) -> None: ...


class KbWriteLock(Protocol):
    """The process-wide knowledge-base write lock, owned by the runtime (RT-51 … RT-53).

    Both context-manager protocols are required because MW-2 requires both hook variants: the daemon
    runs ``aafter_agent`` and the non-live suite runs ``after_agent`` on the same compiled graph, and
    an ``asyncio.Lock`` cannot be acquired from the synchronous one.

    The lock is held for exactly the flush-and-enqueue critical section and never across a model
    call, a tool call or an approval interrupt (RT-52) — approvals are designed to sit pending for
    hours, and holding a global lock across one would stall every other thread's flush for that long.
    """

    def __enter__(self) -> object: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    async def __aenter__(self) -> object: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class _NullWriteLock:
    """The default lock: no lock at all.

    A single-process, single-runtime test does not need one, and a middleware that silently created
    its own would give the *illusion* of RT-51's one process-wide lock while every compiled graph
    held a different object. The runtime injects the real one.
    """

    def __enter__(self) -> object:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def __aenter__(self) -> object:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


NULL_WRITE_LOCK: Final[KbWriteLock] = _NullWriteLock()
"""The shared do-nothing lock used when the runtime injects none."""


class KbMaintenanceMiddleware(AgentMiddleware[KbAgentState]):
    """Call :func:`pkb.core.flush` exactly once per turn, with the paths that turn touched.

    Implements MW-20 … MW-30. Read the module docstring first — the failure-path split between this
    middleware and :mod:`pkb.agents.runtime` (MW-26/MW-27) is the part that looks redundant and is
    not.

    Every constructor argument is **read-only configuration** (MW-4). One instance serves every run
    of a compiled graph — verified: the same ``id()`` across two agents — so nothing per-run may
    live on ``self``. The per-run state lives in :class:`~pkb.agents.middleware.state.KbAgentState`.
    """

    state_schema = KbAgentState
    tools: Sequence[BaseTool] = ()

    def __init__(
        self,
        kb_root: Path,
        *,
        queue: ScanQueue | None = None,
        sink: FlushSink | None = None,
        clock: Callable[[], date] = date.today,
        lock: KbWriteLock | None = None,
        registry: SupportsInvalidate | None = None,
    ) -> None:
        """Wire the middleware to one knowledge base and its collaborators.

        Args:
            kb_root: The knowledge base on disk. Layer 1 speaks paths relative to it (MW-21).
            queue: Where :attr:`~pkb.core.models.FlushReport.scan_requests` are persisted (RT-54).
                A :class:`~pkb.contracts.ScanQueue` Protocol, never a database handle — that is what
                lets a test pass a list and what keeps ``pkb.core`` free of SQL (C18).
            sink: Where the whole report goes (MW-24). ``None`` drops it, which is a defect in the
                daemon and a convenience in a unit test.
            clock: Injected ``today`` (MW-20). Never ``date.today()`` read inside the hook: two
                flushes on one simulated day must be provably identical, and a date-boundary
                regression must be writable without freezing the machine clock.
            lock: The process-wide KB write lock (RT-51). Defaults to :data:`NULL_WRITE_LOCK`.
            registry: Invalidated when the turn changed an ``expert.md``, a skill or a ``topic.md``
                (MW-30). This middleware performs no other registry mutation and compiles nothing.
        """
        super().__init__()
        self.kb_root = kb_root
        self.queue = queue
        self.sink = sink
        self.clock = clock
        self.lock = lock if lock is not None else NULL_WRITE_LOCK
        self.registry = registry

    # ----------------------------------------------------------------------------------
    # Recording what the turn touched
    # ----------------------------------------------------------------------------------

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Record a successful knowledge-base mutation on the way out (MW-17, MW-18, MW-19)."""
        target = self._target(request)
        result = handler(request)
        return result if target is None else _with_touched(result, target)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Async twin of :meth:`wrap_tool_call` (MW-2).

        A sync-only hook raises ``NotImplementedError`` under ``ainvoke()``, and the daemon is
        async-only (RT-3) — so the async variant is the one that actually ships, and the sync one
        exists because the non-live suite drives graphs with ``invoke()``.
        """
        target = self._target(request)
        result = await handler(request)
        return result if target is None else _with_touched(result, target)

    def _target(self, request: ToolCallRequest) -> str | None:
        """The KB-relative path this tool call would change, or ``None`` if it changes none.

        ``None`` covers every non-mutating tool, every path under another mount (agent scratch on
        the ``StateBackend``, the read-only ``/skills/`` route), and every path the harness itself
        refuses. Normalization is :func:`pkb.agents.paths.to_kb_relative`, which calls the harness's
        own ``validate_path`` first — a naive ``startswith("/kb/")`` test answers "not mine" for the
        ``kb/Cooking/notes/b.md`` spelling a model can and does emit, while the tool writes it into
        the tree anyway (D-3, RT-9).
        """
        if request.tool_call.get("name") not in MUTATING_TOOLS:
            return None
        args = request.tool_call.get("args") or {}
        return to_kb_relative(args.get("file_path"))

    # ----------------------------------------------------------------------------------
    # The flush
    # ----------------------------------------------------------------------------------

    def before_agent(self, state: KbAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        """Clear the touched set at run entry (MW-6, MW-14).

        Wired by the factory as a once-per-run entry node, and deliberately **not** re-run on an
        interrupt resume, so a turn that paused for an approval still flushes the paths it wrote
        before the pause (MW-29).
        """
        return {KB_TOUCHED: None}

    async def abefore_agent(
        self, state: KbAgentState, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        """Async twin of :meth:`before_agent` (MW-2)."""
        return {KB_TOUCHED: None}

    def after_agent(self, state: KbAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        """Flush the turn and clear the touched set (MW-20, MW-22, MW-28)."""
        self.flush_turn(_touched(state))
        return {KB_TOUCHED: None}

    async def aafter_agent(
        self, state: KbAgentState, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        """Async twin of :meth:`after_agent` (MW-2)."""
        await self.aflush_turn(_touched(state))
        return {KB_TOUCHED: None}

    def flush_turn(self, touched: Sequence[str]) -> FlushReport:
        """One flush, unconditionally, even for an empty touched set (MW-20, MW-22, MW-23).

        The empty case is not a wasted walk: regeneration is skip-if-identical at the byte level
        (GE-8), so a no-op flush writes nothing and leaves every ``st_mtime_ns`` alone — and running
        it anyway is what puts the derived files back in order after a human hand-edited the tree
        between two turns. The cost is one tree walk per run.

        The flush and the enqueue share one critical section (MW-23, RT-55): a crash between the
        file writes and the enqueue loses the scan permanently, because the next flush only ever
        sees *its own* turn's touched paths.
        """
        with self.lock:
            report = self._flush(touched)
            _drive(self._enqueue(report.scan_requests))
        self._publish(report, touched)
        return report

    async def aflush_turn(self, touched: Sequence[str]) -> FlushReport:
        """Async twin of :meth:`flush_turn` (MW-2, MW-3).

        The Layer 1 call is a whole-tree walk that reads every markdown file, so it goes through
        ``asyncio.to_thread``: blocking the event loop here would stall every other thread's
        streaming for the duration of the walk (MW-3).
        """
        async with self.lock:
            report = await asyncio.to_thread(self._flush, touched)
            await self._enqueue(report.scan_requests)
        await asyncio.to_thread(self._publish, report, touched)
        return report

    def flush_pending(self, touched: Sequence[str]) -> FlushReport | None:
        """The failure-path entry point: flush what a dead run left behind (MW-26 … MW-28).

        Called by :mod:`pkb.agents.runtime` from a ``try/finally`` around every graph execution,
        with the touched set recovered from the checkpoint::

            values = graph.get_state(config).values
            report = middleware.flush_pending(values.get(KB_TOUCHED, ()))
            if report is not None:
                ...  # clear the key so a later resume does not re-flush the same paths (MW-27)

        Returns ``None`` — flushing nothing and enqueuing nothing — when *touched* is empty. That is
        MW-28's guard, and the emptiness is not a heuristic: :meth:`after_agent` clears the key as
        its last act, so "still populated" means exactly "``after_agent`` did not run". Making it a
        guard rather than a second unconditional flush is what keeps a successful run at one
        ``ScanRequest`` per touched topic instead of two.
        """
        if not touched:
            return None
        return self.flush_turn(touched)

    async def aflush_pending(self, touched: Sequence[str]) -> FlushReport | None:
        """Async twin of :meth:`flush_pending` — the variant the daemon uses (MW-26, RT-3)."""
        if not touched:
            return None
        return await self.aflush_turn(touched)

    # ----------------------------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------------------------

    def _flush(self, touched: Sequence[str]) -> FlushReport:
        """:func:`pkb.core.flush`, wrapped so it cannot take the run down (MW-25).

        Layer 1 never raises for a content defect — a half-written note, unparseable frontmatter, an
        unwritable derived file all become findings (MA-14, GE-8) — so this ``except`` is not the
        read-only-knowledge-base case, which Layer 1 already reports as ``DERIVED_WRITE_FAILED``.
        It is the case nobody predicted: whatever it is, the human's answer has already been
        produced and must still be delivered. The failure is reported, never re-raised into the
        agent's message stream.
        """
        try:
            return flush(self.kb_root, touched, today=self.clock())
        except Exception as exc:  # deliberately total — see the docstring
            return FlushReport(
                findings=[
                    Finding(
                        code="DERIVED_WRITE_FAILED",
                        severity=Severity.ERROR,
                        message=f"the maintenance flush did not complete: {exc}",
                        rule_id="MW-25",
                        hint=(
                            "the derived files may be stale; the next flush or a daemon restart "
                            "regenerates them"
                        ),
                    )
                ]
            )

    async def _enqueue(self, requests: Sequence[ScanRequest]) -> None:
        """Hand this turn's conflict-scan requests to the queue (RT-54, RT-55, MW-28).

        Layer 1 built them (``build_scan_requests``, already coalesced one-per-topic) and opened no
        database to do it; persisting them is Layer 2's, through the
        :class:`~pkb.contracts.ScanQueue` Protocol so a unit test can pass a list.

        An empty batch is not handed over: a queue implementation should not have to special-case
        the commonest turn in the system.
        """
        if self.queue is not None and requests:
            await self.queue.put(list(requests))

    def _publish(self, report: FlushReport, touched: Sequence[str]) -> None:
        """Deliver the report and refresh the registry — both **outside** the lock (RT-52, MW-24).

        The sink is a transport's log or event stream and the registry check stats a few paths;
        neither writes the tree, so neither belongs in the critical section that every other
        thread's flush is waiting on.
        """
        if self.sink is not None:
            self.sink(report)
        if self.registry is not None and self._changes_agent_configuration(touched):
            self.registry.invalidate()

    def _changes_agent_configuration(self, touched: Sequence[str]) -> bool:
        """Whether this turn rewrote something a compiled graph was built from (MW-30).

        ``expert.md`` is an expert's prompt source, ``skills/**`` its skill set and ``topic.md`` its
        title and description — the three inputs the registry caches. Everything else leaves every
        compiled graph correct.
        """
        for relative in touched:
            try:
                role, _ = classify(self.kb_root, self.kb_root / relative)
            except ValueError:
                continue
            if role in _REGISTRY_ROLES:
                return True
        return False


# --------------------------------------------------------------------------------------
# Free functions
# --------------------------------------------------------------------------------------


def _touched(state: KbAgentState) -> list[str]:
    """This run's touched paths, read out of the agent state.

    The cast is because ``KB_TOUCHED`` is a module constant rather than a string literal, and a
    ``TypedDict`` may only be subscripted with a literal. Spelling ``"kb_touched"`` here instead
    would put the published contract in two places, which is precisely what the constant prevents.
    """
    return list(cast("Mapping[str, Any]", state).get(KB_TOUCHED) or ())


def _with_touched(result: ToolMessage | Command[Any], relative: str) -> ToolMessage | Command[Any]:
    """Attach *relative* to a tool result's state update, if the tool actually succeeded (MW-17).

    Only a success is recorded. A blocked validation, a permission denial, a backend error and a
    ``validate_path`` refusal all come back as an error ``ToolMessage``, and recording one would bump
    ``updated`` on a file that was never written — which is how a denied ``index.md`` write ends up
    looking like a change to every reader of the tree.

    The ``messages`` entry in the returned :class:`~langgraph.types.Command` is **mandatory**:
    ``ToolNode._validate_tool_command`` raises ``ValueError`` unless the update carries a
    ``ToolMessage`` whose ``tool_call_id`` matches the call (MW-18). A bare ``ToolMessage`` cannot
    carry a state update at all, which is why the success path has to re-wrap a perfectly good
    message.
    """
    message = _tool_message(result)
    if message is None or message.status == "error":
        return result
    if isinstance(result, Command):
        return replace(result, update=_merged_update(result.update, relative))
    return Command(update={"messages": [message], KB_TOUCHED: [relative]})


def _tool_message(result: ToolMessage | Command[Any]) -> ToolMessage | None:
    """The ``ToolMessage`` a handler produced, whether bare or already wrapped in a ``Command``.

    Every deepagents filesystem tool returns a bare ``ToolMessage`` (verified against the pin,
    including ``StateBackend`` writes, which mutate state through ``CONFIG_KEY_SEND`` instead). The
    ``Command`` branch is for the case where a sibling middleware sits closer to the tool and has
    already wrapped the result — the recommended stack order puts this middleware innermost, so it
    normally does not happen, but nothing in the harness guarantees an ordering.
    """
    if isinstance(result, ToolMessage):
        return result
    for key, value in _update_items(result.update):
        if key != "messages":
            continue
        candidates = value if isinstance(value, list | tuple) else [value]
        for item in candidates:
            if isinstance(item, ToolMessage):
                return item
    return None


def _merged_update(update: Any, relative: str) -> Any:
    """Add *relative* to an existing ``Command`` update without disturbing anything else.

    ``Command.update`` may be a mapping or a sequence of ``(key, value)`` pairs; both are merged in
    their own shape. The mapping case reuses the state reducer so an update that already names some
    touched paths cannot end up with a duplicate — the merge rule lives in exactly one place
    (MW-6).
    """
    if isinstance(update, Mapping):
        existing = update.get(KB_TOUCHED)
        merged = merge_touched_paths(existing if isinstance(existing, list) else None, [relative])
        return {**update, KB_TOUCHED: merged}
    if isinstance(update, list):
        return [*update, (KB_TOUCHED, [relative])]
    return update


def _update_items(update: Any) -> list[tuple[str, Any]]:
    """A ``Command`` update as ``(key, value)`` pairs, for either supported shape."""
    if isinstance(update, Mapping):
        return list(update.items())
    if isinstance(update, list):
        return [pair for pair in update if isinstance(pair, tuple) and len(pair) == 2]
    return []


def _drive(coro: Coroutine[Any, Any, None]) -> None:
    """Run *coro* to completion from a synchronous hook.

    The :class:`~pkb.contracts.ScanQueue` Protocol is async because the daemon is async (RT-3), but
    MW-2 requires the synchronous ``after_agent`` to do the same work — the non-live suite drives
    graphs with ``invoke()``. When the calling thread has no running loop (the ordinary case for a
    synchronous hook) this is one ``asyncio.run``; when it does have one, the caller reached a
    synchronous graph from inside async code and ``asyncio.run`` would raise, so the coroutine is
    driven on a private loop in a worker thread instead of deadlocking.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(asyncio.run, coro).result()
