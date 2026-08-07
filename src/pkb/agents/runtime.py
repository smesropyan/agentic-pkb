"""``PkbRuntime`` — the process's shared singletons and the only sanctioned way to run a graph.

This module owns the four things the harness does not give us, each verified against the pinned
``deepagents 0.7.5`` / ``langchain 1.3.14`` / ``langgraph 1.2.10`` rather than assumed. Every one of
them is here — and not in a middleware, a transport or a helper — because this is the only place
where "on every execution path" is *structurally* true. There will be HTTP runs, Telegram runs, the
scan worker and MCP calls; putting any of these guards anywhere else makes a fifth caller that
forgets it structurally possible (MW-26).

**1. The flush runs on failure too (D-1, RT-7, MW-26, MW-27).**
``KbMaintenanceMiddleware.after_agent`` is an ordinary graph node on the normal exit edge, so an
exception anywhere in a run aborts the pregel superstep and it never executes — executed across four
failure shapes, each leaving a written file and no flush. The tools node has however already
committed its state update, so the paths survive **in the checkpoint**. Every execution here is
therefore wrapped in ``try/finally``: on the way out the runtime reads
:data:`~pkb.agents.middleware.state.KB_TOUCHED` back out of ``aget_state(config).values``, flushes
what is left, and clears the key. ``after_agent`` clears the key as its last act, so a normally
completed run leaves an empty set and the outer handler is a no-op — exactly one flush per run on
both paths (MW-28). :meth:`PkbRuntime.open` additionally runs one
:func:`pkb.core.regenerate_all` before serving, for the runs that died before this code existed and
for threads abandoned at an approval (D-14, RT-7).

**2. Two concurrent runs on one thread are refused (D-15, RT-45).**
LangGraph OSS has no multitask strategy — that is a Platform feature. ``asyncio.gather(run('D'),
run('D'))`` returns two successes with interleaved writes. The 409 arch §8 promises is this module's
own per-``(agent_id, thread_id)`` registry, raising :class:`~pkb.contracts.ThreadBusyError`.

**3. A new message during a pending approval is refused (D-16, RT-39).**
Sending one to the harness **silently discards the interrupt** and runs the turn as if the gated
tool call never happened — the write is simply never performed and the approval vanishes. So
:meth:`PkbRuntime.run` reads ``aget_state(config).interrupts`` first and raises
:class:`~pkb.contracts.ApprovalPendingError` rather than forwarding.

**4. The knowledge-base write lock (D-7, RT-51 … RT-53).**
One per process, reentrancy-safe by construction, held **only** around ``flush`` and
``scaffold_topic`` — never across a model call, a tool call or an interrupt. Approvals are designed
to sit pending for hours and a lock held across one would freeze every other thread's flush for that
long.

**Lifecycle (RT-2, RT-3).** ``AsyncSqliteSaver.from_conn_string`` is an async context manager that
*closes* the connection on exit, and its ``__init__`` calls :func:`asyncio.get_running_loop`. So the
runtime is ``async with PkbRuntime.open(kb_root, db_path) as rt:`` and never a module-level singleton
built at import time. Synchronous checkpointer calls from the saver's own loop raise
``asyncio.InvalidStateError``, which is why the run API here is async-only — while every middleware
still implements both hook variants (MW-2), because the non-live suite drives graphs with
``invoke()`` against an ``InMemorySaver``.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from types import TracebackType
from typing import TYPE_CHECKING, Any, Final, Literal, Self
from uuid import uuid4

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import BackendProtocol
from deepagents.backends.state import StateBackend
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.base import BaseStore
from langgraph.store.sqlite.aio import AsyncSqliteStore
from langgraph.types import Checkpointer

from pkb.agents.approval import (
    DEFAULT_REASON,
    normalize_interrupts,
    propose_only_command,
    to_resume_command,
    validate_decisions,
)
from pkb.agents.events import stream_events
from pkb.agents.gates import requires_approval
from pkb.agents.middleware.maintenance import (
    FlushSink,
    KbMaintenanceMiddleware,
    KbWriteLock,
)
from pkb.agents.middleware.state import KB_TOUCHED
from pkb.agents.paths import KB_MOUNT, SKILLS_MOUNT, to_kb_relative
from pkb.agents.registry import DEFAULT_MODEL, AgentGraph, AgentRegistry
from pkb.agents.scans import SqliteScanQueue, run_scan
from pkb.agents.skills import packaged_skills_root
from pkb.agents.tools.topics import TopicToolEnv, topic_tools
from pkb.contracts import (
    AgentDescriptor,
    AgentEvent,
    ApprovalMode,
    ApprovalPendingError,
    ApprovalRequest,
    Decision,
    FlushReport,
    InterruptEvent,
    MessageView,
    PendingProposal,
    RunEnd,
    RunError,
    ScanQueue,
    ScanRequest,
    ScanResult,
    ThreadBusyError,
)
from pkb.core import regenerate_all
from pkb.core.scan import scan

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from langchain_core.messages import BaseMessage
    from langchain_core.runnables import RunnableConfig
    from langchain_core.tools import BaseTool

    from pkb.core.models import KbSnapshot

__all__ = [
    "DEFAULT_DURABILITY",
    "EVENT_BUFFER_SIZE",
    "Durability",
    "PkbRuntime",
    "ProposalSink",
    "ReentrantWriteLock",
    "RegistryFactory",
    "RuntimeConfig",
]

Durability = Literal["sync", "async", "exit"]
"""langgraph's checkpoint-write policy for one execution."""

DEFAULT_DURABILITY: Durability = "sync"
"""How every user-facing run checkpoints (Q11, RT-46).

The langgraph default is ``"async"``, under which a cancellation can lose the last checkpoint write.
This is a personal knowledge base with human-latency turns, so the throughput cost of writing
synchronously is invisible — and it removes the whole class of "the daemon died and the pending
approval vanished" bugs arch §8 promises will not happen.
"""

EVENT_BUFFER_SIZE: Final = 64
"""Bound on the buffer between the task driving ``astream`` and the caller consuming events.

Bounded rather than unlimited so a slow consumer applies backpressure to the run instead of letting
a long generation accumulate in memory; large enough that no ordinary turn ever blocks on it.
"""

ProposalSink = Callable[[PendingProposal], None]
"""Where a propose-only auto-rejection is recorded (RT-42). Injected, never a table here."""

RegistryFactory = Callable[["PkbRuntime"], AgentRegistry]
"""Builds the agent registry for a runtime. The seam a test uses to inject a scripted model.

The registry needs the runtime (RT-1's singletons) and the runtime needs the registry (it resolves
agent ids to graphs), so one of the two has to be built second. The runtime constructs itself first
and then calls this with ``self``.
"""


# --------------------------------------------------------------------------------------
# The knowledge-base write lock (RT-51 … RT-53, D-7)
# --------------------------------------------------------------------------------------


class ReentrantWriteLock:
    """One process-wide knowledge-base write lock, safe to re-enter and usable from both worlds.

    Satisfies :class:`~pkb.agents.middleware.maintenance.KbWriteLock`: both the synchronous and the
    asynchronous context-manager protocol, because MW-2 makes the *same* critical section run from
    ``after_agent`` (the non-live suite drives ``invoke()``) and from ``aafter_agent`` (the daemon).

    **Why a ``threading.Lock`` underneath and not an ``asyncio.Lock`` (D-7).** An ``asyncio.Lock``
    cannot be acquired from a synchronous hook at all, and it provides no exclusion against a hook
    running on a worker thread — which is where ``asyncio.to_thread`` puts Layer 1's tree walk. The
    plain lock is acquired off-loop through :func:`asyncio.to_thread` on the async path, so the event
    loop is never blocked while waiting, and released directly (a ``threading.Lock``, unlike an
    ``RLock``, may be released by a different thread than acquired it).

    **Why reentrancy at all.** ``create_topic`` takes this lock from *inside* a tool call while an
    outer flush may also want it (RT-53). Delegation does not actually nest the two — a delegated
    expert's subgraph completes inside the ``task`` tool before the parent's exit chain runs — but
    the ambiguity costs fifteen lines to remove and a deadlock in a daemon costs an afternoon.
    Ownership is keyed on ``(thread, task)``: two tasks on one event loop are different owners even
    though they share a thread, which is what keeps two parallel delegated experts serialized rather
    than interleaved.

    The lock is held for the flush-and-enqueue critical section and for a scaffold, and for nothing
    else (RT-52). :attr:`depth` and :attr:`acquisitions` exist so a test can prove that.
    """

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._owner: tuple[int, int] | None = None
        self.depth = 0
        """Current re-entry depth. ``0`` when the lock is free; never above 1 for distinct owners."""

        self.acquisitions = 0
        """How many times the lock was taken from *free*. Reentrant re-takes do not count."""

    @staticmethod
    def _owner_key() -> tuple[int, int]:
        """Identity of the current holder: the OS thread plus, inside a loop, the task.

        Two coroutines on one event loop share a thread but must not share the lock — a task that
        awaits inside the critical section would otherwise let a sibling walk straight in.
        """
        try:
            task = asyncio.current_task()
        except RuntimeError:  # no running loop: a purely synchronous caller
            task = None
        return (threading.get_ident(), id(task) if task is not None else 0)

    def _take(self) -> bool:
        """Claim reentrancy if we already hold it. ``True`` means no acquire is needed."""
        if self._owner == self._owner_key():
            self.depth += 1
            return True
        return False

    def _claim(self) -> None:
        self._owner = self._owner_key()
        self.depth = 1
        self.acquisitions += 1

    def _release(self) -> None:
        self.depth -= 1
        if self.depth == 0:
            self._owner = None
            self._mutex.release()

    def __enter__(self) -> Self:
        """Acquire synchronously. Blocks the calling thread — see :meth:`__aenter__` for the daemon.

        A synchronous acquire from the event-loop thread would block the loop, so this path is for
        the sync hooks MW-2 requires and for callers with no loop at all (RT-3 keeps the production
        run API async-only).
        """
        if not self._take():
            self._mutex.acquire()
            self._claim()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._release()

    async def __aenter__(self) -> Self:
        """Acquire without blocking the event loop.

        The wait happens on a worker thread, so every other run keeps streaming while one holds the
        lock — RT-51's "runs on different threads stream concurrently; only the flush is serialized".
        """
        if not self._take():
            await asyncio.to_thread(self._mutex.acquire)
            self._claim()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._release()


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Deployment configuration. Never read from the knowledge base (RG-21, Q6).

    The tree holds knowledge, not runtime configuration: a ``model:`` key in ``topic.md`` would be
    an ``UNKNOWN_FIELD`` warning (VA-32) and would drag the deployment's model choice through the
    human-approval workflow.
    """

    default_model: str = DEFAULT_MODEL
    """The model every agent runs on unless :attr:`models` names another (RG-21)."""

    models: Mapping[str, str] = field(default_factory=dict)
    """Per-agent-id overrides, e.g. ``{"topic/cooking": "anthropic:claude-opus-5"}``."""

    durability: Durability = DEFAULT_DURABILITY
    """Checkpoint durability for every run. See :data:`DEFAULT_DURABILITY`."""

    clock: Callable[[], date] = date.today
    """Injected ``today`` for the ``updated`` stamp (MW-20), so a date boundary is testable."""

    flush_sink: FlushSink | None = None
    """Where every :class:`~pkb.core.models.FlushReport` goes (MW-24) — the daemon's log or event
    stream. ``None`` drops broken links, orphans and ``DERIVED_WRITE_FAILED`` on the floor, which is
    a defect in a daemon and a convenience in a unit test."""

    proposal_sink: ProposalSink | None = None
    """Where a propose-only auto-rejection is recorded (RT-42). The runtime also keeps them in
    memory; a daemon that wants them to survive a restart passes a sink that persists them."""


# --------------------------------------------------------------------------------------
# The runtime
# --------------------------------------------------------------------------------------


class PkbRuntime:
    """The shared singletons and the sanctioned way to execute a graph (RT-1 … RT-7, RT-36 … RT-58).

    Satisfies :class:`pkb.agents.expert.GraphRuntime` structurally, which is how every compiled
    graph — the Librarian, every expert at every depth, and every internal scan run — receives *the
    same* checkpointer, store, backend and write lock (RT-1). Nothing constructs its own.

    Use :meth:`open`; see the module docstring for why a module-level singleton cannot work (RT-2).
    """

    def __init__(
        self,
        kb_root: Path,
        db_path: Path,
        *,
        config: RuntimeConfig | None = None,
        registry_factory: RegistryFactory | None = None,
    ) -> None:
        """Build the runtime's synchronous half. Prefer :meth:`open`.

        Everything that needs a running event loop — the checkpointer, the store — is created by
        :meth:`open`, because ``AsyncSqliteSaver.__init__`` calls :func:`asyncio.get_running_loop`
        and pins itself to that loop (RT-2). Until then :attr:`checkpointer` and :attr:`store` are
        ``None`` and no graph should be compiled.

        Args:
            kb_root: The knowledge base on disk.
            db_path: The runtime's SQLite file. It holds the checkpointer's ``checkpoints`` and
                ``writes`` tables and Layer 2's scan queue; Layer 3's ``threads`` table lives here
                too, on its own connection (RT-4). The file is opened WAL, which is what makes that
                safe.
            config: Deployment configuration.
            registry_factory: Builds the :class:`~pkb.agents.registry.AgentRegistry`. The default
                wires the real factories, the configured models and the gated topic tools; a test
                replaces it to drive graphs with a scripted chat model (D-8).
        """
        self.kb_root = kb_root
        self.db_path = db_path
        """The SQLite file. Never the saver's ``aiosqlite`` connection — other tables open their
        own, which is what keeps Layer 3 out of the checkpointer's loop affinity (RT-4)."""

        self.config = config or RuntimeConfig()
        self.backend: BackendProtocol = _build_backend(kb_root)
        self.checkpointer: Checkpointer = None
        self.store: BaseStore | None = None
        self.write_lock: KbWriteLock = ReentrantWriteLock()
        self.queue = SqliteScanQueue(db_path)
        """The concrete queue. :attr:`scan_queue` is the same object seen through the Protocol the
        middleware depends on; this name is what :meth:`open` calls ``setup`` on."""

        self.scan_queue: ScanQueue | None = self.queue
        self.clock = self.config.clock
        self.flush_sink: FlushSink | None = self._publish_flush

        self._stack: AsyncExitStack | None = None
        self._snapshot: KbSnapshot | None = None
        self._snapshot_lock = threading.Lock()
        self._active: dict[tuple[str, str], str] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._proposals: list[PendingProposal] = []
        self._maintenance = KbMaintenanceMiddleware(
            kb_root,
            queue=self.scan_queue,
            sink=self.flush_sink,
            clock=self.clock,
            lock=self.write_lock,
            registry=self,
        )
        """The failure-path flush (MW-26). A *second* instance of the middleware the graphs carry,
        which is sound precisely because middleware hold read-only configuration and no per-run
        state (MW-4) — reaching into a compiled graph to find its instance would be the fragile
        alternative, and the runtime would still need one for a graph it never compiled."""

        self._registry = (registry_factory or _default_registry)(self)

    # ----------------------------------------------------------------------------------
    # Lifecycle (RT-2, RT-5, RT-7)
    # ----------------------------------------------------------------------------------

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        kb_root: Path,
        db_path: Path,
        *,
        config: RuntimeConfig | None = None,
        registry_factory: RegistryFactory | None = None,
    ) -> AsyncIterator[Self]:
        """Open the runtime for the process's lifetime (RT-2, RT-7).

        ``AsyncSqliteSaver.from_conn_string`` closes its connection when its context exits, so the
        saver has to be held open for as long as any graph might use it — that is what this context
        manager is. One store is opened over the same file (RT-5, Q12): nothing in v1 reads it, but
        an ``AsyncSqliteStore`` shares the daemon's file and lifecycle, so making the placeholder
        real costs one line and stops a later restart being silently lossy.

        One :func:`pkb.core.regenerate_all` runs before the first ``yield`` (RT-7). Derived files can
        be stale across a restart in two ways the middleware cannot cover: a run that died before
        ``after_agent`` (D-1) and a thread abandoned at an unresolved approval (D-14). Regeneration
        is idempotent and byte-deterministic (GE-4/GE-5), so on a clean tree this writes zero files
        and costs one walk.
        """
        runtime = cls(kb_root, db_path, config=config, registry_factory=registry_factory)
        stack = AsyncExitStack()
        runtime._stack = stack
        try:
            saver = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(str(db_path)))
            await saver.setup()
            runtime.checkpointer = saver
            store = await stack.enter_async_context(AsyncSqliteStore.from_conn_string(str(db_path)))
            await store.setup()
            runtime.store = store
            await runtime.queue.setup()
            await runtime.regenerate()
            yield runtime
        finally:
            await runtime.aclose()

    async def aclose(self) -> None:
        """Close the checkpointer, the store and every in-flight run. Idempotent.

        After this the saver's connection is gone and any checkpointer call raises, which is the
        observable half of RT-2: the runtime is a scoped resource, not a global.
        """
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._tasks.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        self._active.clear()
        stack, self._stack = self._stack, None
        if stack is not None:
            await stack.aclose()
        self.checkpointer = None
        self.store = None

    # ----------------------------------------------------------------------------------
    # The shared snapshot (RT-1, RT-21)
    # ----------------------------------------------------------------------------------

    def snapshot(self) -> KbSnapshot:
        """The current tree, scanned once and cached until something changes (RG-2).

        The gate predicate calls this once per gated tool call in ``after_model`` and the
        description factory calls it again, so a bare ``lambda: scan(kb_root)`` would walk the whole
        tree several times per turn. Invalidation is deliberately event-driven rather than
        mtime-based: mtime granularity is one second on the network and sync filesystems a personal
        knowledge base lives on, and a missed invalidation is silent.
        """
        with self._snapshot_lock:
            if self._snapshot is None:
                self._snapshot = scan(self.kb_root)
            return self._snapshot

    def invalidate(self, agent_id: str | None = None) -> None:
        """Drop the cached tree and the compiled graphs the tree makes wrong (RG-16, MW-30).

        Satisfies :class:`~pkb.agents.middleware.maintenance.SupportsInvalidate`, so the maintenance
        middleware and the topic-creation tools can be handed the runtime rather than the registry:
        a rewritten ``expert.md``, skill or ``topic.md`` invalidates *both* caches through one call,
        and a caller cannot refresh one and forget the other.
        """
        with self._snapshot_lock:
            self._snapshot = None
        self._registry.invalidate(agent_id)

    # ----------------------------------------------------------------------------------
    # Catalog (RG-1, RG-15)
    # ----------------------------------------------------------------------------------

    def list_agents(self) -> list[AgentDescriptor]:
        """Every addressable agent: the Librarian first, then topics in snapshot order (RG-15)."""
        return self._registry.list_agents()

    # ----------------------------------------------------------------------------------
    # Runs (RT-36 … RT-47)
    # ----------------------------------------------------------------------------------

    async def run(
        self,
        agent_id: str,
        thread_id: str,
        message: str,
        *,
        approval_mode: ApprovalMode = "interactive",
        run_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Start a turn and stream its normalized events (RT-36, RT-39, RT-42, RT-45).

        Both ids are always explicit: Layer 2 never invents a thread id for a user conversation and
        never persists the ``(agent_id, thread_id)`` association — the checkpointer keys on
        ``thread_id`` alone (D-6) and the mapping is Layer 3's ``threads`` table (RT-36, RT-49).

        Args:
            agent_id: ``librarian`` or a topic id, verbatim and opaque (RG-12).
            thread_id: Minted, titled, listed and deleted by Layer 3. Must be globally unique:
                delegated work checkpoints under *this* thread in a nested namespace (D-6).
            message: The human's turn.
            approval_mode: ``interactive`` streams the approval to a human. ``propose_only`` is the
                MCP write path (RT-42): the gate still fires, but an external agent cannot satisfy
                it, so Layer 2 auto-answers ``reject`` with a fixed message, records a
                :class:`~pkb.contracts.PendingProposal`, and the run completes instead of hanging
                on a decision nobody will make. That auto-rejection is the only decision Layer 2
                ever authors on its own behalf (RT-33).
            run_id: Optional, so a caller can :meth:`cancel` a run it has not yet seen an event
                from. Minted when absent — a *run* id, never a thread id.

        Yields:
            :class:`~pkb.contracts.AgentEvent` values: frozen dataclasses of primitives, never a
            LangChain message, an ``Interrupt`` or a ``Command`` (RT-43, I2).

        Raises:
            UnknownAgentError: No such agent (RG-13) — Layer 3 returns 404.
            ThreadBusyError: A run is already active on this thread (RT-45) — 409.
            ApprovalPendingError: The thread is waiting on a human decision (RT-39) — 409. Refused
                rather than forwarded, because the harness would silently discard the interrupt and
                run the turn as if the gated call had never happened (D-16).
        """
        async for event in self._stream(
            agent_id,
            thread_id,
            _payload_factory(message),
            run_id=run_id or _new_run_id(),
            approval_mode=approval_mode,
            refuse_when_pending=True,
        ):
            yield event

    async def resume(
        self,
        agent_id: str,
        thread_id: str,
        decisions: Sequence[Decision],
        *,
        interrupt_id: str | None = None,
        run_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Answer the thread's pending approval and stream the rest of the run (RT-40, LB-10).

        The decisions are validated **before the graph is touched**. Without that,
        ``_process_decision`` raises a bare ``ValueError`` from inside ``after_model``, which aborts
        the superstep, skips the flush (D-1) and hands the human a stack trace instead of a 400 —
        and an unmatched interrupt id degrades into a confusing message about hanging tool calls
        that never names the id.

        Approvals are routed **by thread, never by agent** (LB-10): an interrupt raised inside a
        delegated expert propagates to the parent's thread and is resolved there with the parent's
        id, so ``agent_id`` is the id of the run that was interrupted.

        Raises:
            StaleInterruptError: Nothing is pending, or ``interrupt_id`` names a different interrupt.
                The thread is left interrupted and the original approval is still resolvable.
            InvalidDecisionError: Wrong number of decisions, or a type the action does not allow.
        """
        pending = await self.pending_approval(agent_id, thread_id)
        command = _resume_payload(pending, decisions, interrupt_id=interrupt_id)
        async for event in self._stream(
            agent_id,
            thread_id,
            lambda: command,
            run_id=run_id or _new_run_id(),
            approval_mode="interactive",
            refuse_when_pending=False,
        ):
            yield event

    async def cancel(self, run_id: str) -> None:
        """Stop a run in flight (RT-46).

        LangGraph has no server-side cancel, so this is Layer 2's: the runtime owns
        ``run_id -> asyncio.Task`` and cancels the task driving ``astream``. The thread stays
        resumable — every run checkpoints with :data:`DEFAULT_DURABILITY`, so the state written
        before the cancellation is on disk — and the cancelled task still flushes on its way out
        (MW-26). Unknown or already-finished run ids are a no-op, so a client racing the end of a
        run does not get an error for winning.
        """
        task = self._tasks.get(run_id)
        if task is not None:
            task.cancel()

    # ----------------------------------------------------------------------------------
    # Approvals, history, threads (RT-38, RT-48, RT-49)
    # ----------------------------------------------------------------------------------

    async def pending_approval(self, agent_id: str, thread_id: str) -> ApprovalRequest | None:
        """The approval this thread is waiting on, normalized — or ``None`` (RT-38, RT-41).

        Read from the checkpoint, so any client, in any process, after any delay, across a daemon
        restart, can resolve it. The same interrupt appears on both ``.interrupts`` and
        ``.tasks[0].interrupts`` and a delegated one is emitted under two namespaces with one id, so
        the normalization deduplicates by id: one approval, one request.
        """
        graph = self._registry.get(agent_id)
        state = await graph.aget_state(self.thread_config(thread_id))
        requests = normalize_interrupts(
            state.interrupts,
            agent_id=agent_id,
            thread_id=thread_id,
            reason_for=self.reason_for,
        )
        return requests[0] if requests else None

    async def history(self, agent_id: str, thread_id: str) -> list[MessageView]:
        """Replay a thread's conversation as primitives (RT-43).

        The system prompt is excluded: it is configuration the factory attaches in code, not part of
        the conversation, and it changes between turns as the breadth middleware refreshes it.
        ``created_at`` is ``None`` because LangChain messages carry no timestamp — Layer 3's
        ``threads`` table is where run times live (D-19).
        """
        graph = self._registry.get(agent_id)
        state = await graph.aget_state(self.thread_config(thread_id))
        messages = state.values.get("messages") or []
        return [view for view in (_message_view(message) for message in messages) if view]

    async def delete_thread(self, thread_id: str) -> None:
        """Erase a thread's checkpoints and writes (RT-48).

        Exposed here because Layer 3 may not import langgraph (I2). Delegated sub-runs share the
        parent's ``thread_id`` in a nested ``checkpoint_ns`` (D-6), so this removes their rows too.
        """
        if not isinstance(self.checkpointer, BaseCheckpointSaver):
            msg = "the runtime is closed; open it with PkbRuntime.open(...)"
            raise RuntimeError(msg)
        await self.checkpointer.adelete_thread(thread_id)

    def pending_proposals(self) -> list[PendingProposal]:
        """Propose-only actions awaiting a human, in the order they were recorded (RT-42).

        In-memory: this is what keeps "human content wins" true when the caller is a robot, and the
        durable copy is whatever :attr:`RuntimeConfig.proposal_sink` does with it. Layer 2 grows no
        table for them, the same way it grows no thread listing (RT-49).
        """
        return list(self._proposals)

    # ----------------------------------------------------------------------------------
    # Maintenance (RT-7, RT-57, RT-58)
    # ----------------------------------------------------------------------------------

    async def regenerate(self) -> FlushReport:
        """Rewrite every derived file (RT-7, GE-30).

        Startup regeneration and the on-demand "rebuild" path are the same call. Under the write
        lock, on a worker thread: it is a whole-tree walk and it writes, so it is exactly the
        critical section RT-51 exists for.
        """
        async with self.write_lock:
            report = await asyncio.to_thread(regenerate_all, self.kb_root)
        self._publish_flush(report)
        return report

    async def request_scan(self, request: ScanRequest) -> ScanResult:
        """Run one conflict scan on its own reserved thread (RT-58, C12).

        The dequeue loop and its timer are Layer 3's; the graph run is here, because I2 forbids a
        transport from touching deepagents at all.
        """
        return await run_scan(self, request)

    # ----------------------------------------------------------------------------------
    # Wiring the pieces the factories and the gates need
    # ----------------------------------------------------------------------------------

    def thread_config(self, thread_id: str) -> RunnableConfig:
        """The only config Layer 2 ever builds (RT-37).

        ``checkpoint_ns`` is owned entirely by the harness: nested subagent runs get their namespace
        from the ambient parent config, and passing an explicit one breaks ``aget_state`` outright
        (``ValueError: Subgraph … not found``). ``recursion_limit`` is already set by
        ``create_deep_agent``'s own ``.with_config`` (EX-16), so adding one here would only shadow it.
        """
        return {"configurable": {"thread_id": thread_id}}

    def reason_for(self, tool: str, args: Mapping[str, Any]) -> str:
        """The gate slug an :class:`~pkb.contracts.ActionView` carries (RT-34).

        ``ActionRequest`` has nowhere to put a reason — it carries only ``name``/``args``/
        ``description`` — so the gate's own answer cannot survive the harness round trip and is
        recomputed here from the same pure function the ``when`` predicate used.
        """
        reason = requires_approval(
            tool, to_kb_relative(args.get("file_path")), args, self.snapshot()
        )
        return reason.value if reason is not None else DEFAULT_REASON

    def tools_for(self, agent_id: str) -> Sequence[BaseTool]:
        """The extra tools one agent carries: ``create_topic`` or ``create_subtopic`` (LB-7, EX-12).

        Passed to the registry as its ``tool_factory``. The expert's tool is genuinely per-topic —
        it may create sub-topics only under its own root — so one shared instance cannot serve every
        agent, and the agent id is the only thing that says which topic is being built.
        """
        env = TopicToolEnv(
            kb_root=self.kb_root,
            snapshot=self.snapshot,
            lock=self.write_lock,
            registry=self,
            clock=self.clock,
        )
        return topic_tools(env, agent_id)

    # ----------------------------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------------------------

    async def _stream(
        self,
        agent_id: str,
        thread_id: str,
        payload: Callable[[], Any],
        *,
        run_id: str,
        approval_mode: ApprovalMode,
        refuse_when_pending: bool,
    ) -> AsyncIterator[AgentEvent]:
        """Register the run, drive it in a task, and forward its events.

        The run is driven by a *task* rather than inline in this generator for two reasons, both
        load-bearing. It is what :meth:`cancel` cancels (RT-46). And it is what makes MW-26's flush
        guard unconditional: a consumer that stops iterating half way through — an SSE client that
        disconnects, a ``break`` in a caller's loop — leaves an abandoned async generator whose
        ``finally`` runs at some unspecified later point, while a task's ``finally`` always runs.

        The busy check and the registration happen before the first ``await`` in this coroutine, so
        two runs started concurrently on one thread cannot both see a free slot (RT-45).
        """
        graph = self._registry.get(agent_id)
        key = (agent_id, thread_id)
        if key in self._active:
            raise ThreadBusyError(
                f"a run is already active on thread {thread_id!r} for agent {agent_id!r}"
            )
        self._active[key] = run_id
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue(maxsize=EVENT_BUFFER_SIZE)
        task: asyncio.Task[None] | None = None
        try:
            if refuse_when_pending:
                await self._refuse_when_pending(graph, thread_id)
            task = asyncio.create_task(
                self._drive(
                    graph,
                    queue,
                    payload(),
                    agent_id=agent_id,
                    thread_id=thread_id,
                    run_id=run_id,
                    approval_mode=approval_mode,
                )
            )
            self._tasks[run_id] = task
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
            # A cancelled run is not a failed one (RT-46): the task's own ``finally`` has already
            # flushed and closed the stream, so re-raising here would make ``cancel(run_id)`` look
            # like an error to the caller — and, worse, cancel the *consumer* along with it.
            with contextlib.suppress(asyncio.CancelledError):
                await task
        finally:
            self._active.pop(key, None)
            self._tasks.pop(run_id, None)
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _drive(
        self,
        graph: AgentGraph,
        queue: asyncio.Queue[AgentEvent | None],
        payload: Any,
        *,
        agent_id: str,
        thread_id: str,
        run_id: str,
        approval_mode: ApprovalMode,
    ) -> None:
        """Execute the graph and push normalized events into *queue* (RT-42, RT-43, MW-26)."""
        config = self.thread_config(thread_id)
        driver = _DurableGraph(graph, self.config.durability)
        try:
            while True:
                captured: ApprovalRequest | None = None
                failed = False
                async for event in stream_events(
                    driver,
                    payload,
                    config,
                    run_id=run_id,
                    agent_id=agent_id,
                    thread_id=thread_id,
                    reason_for=self.reason_for,
                ):
                    if approval_mode == "propose_only" and isinstance(event, InterruptEvent):
                        # The gate fired and there is no human on this call path. Record what was
                        # proposed and answer it ourselves — the run then completes rather than
                        # parking forever on a decision the caller cannot make (RT-42).
                        captured = event.request
                        self._record_proposals(event.request)
                        continue
                    failed = failed or isinstance(event, RunError)
                    if captured is not None and isinstance(event, RunEnd):
                        # Not the end: we are about to resume this run with the auto-rejection, and
                        # a caller must see exactly one terminal event for the whole call.
                        continue
                    await queue.put(event)
                if captured is None or failed:
                    return
                payload = propose_only_command(captured)
        finally:
            await self._flush_pending(graph, config)
            await queue.put(None)

    async def _refuse_when_pending(self, graph: AgentGraph, thread_id: str) -> None:
        """RT-39. See the module docstring for what the harness does instead (D-16)."""
        state = await graph.aget_state(self.thread_config(thread_id))
        if state.interrupts:
            raise ApprovalPendingError(
                f"thread {thread_id!r} is waiting on a human decision; resolve or reject it before "
                "sending another message"
            )

    async def _flush_pending(self, graph: AgentGraph, config: RunnableConfig) -> None:
        """The other half of arch §7's "the flush runs on both success and failure" (MW-26, MW-27).

        The touched set is recovered **from the checkpoint**, not from an in-memory side channel:
        the tools node committed its state update before the model node raised (verified — the key
        held the written path with ``next == ('model',)``), and the run that failed may not even be
        in this process's memory any more.

        Clearing the key afterwards is what stops a later execution re-flushing the same paths and
        re-stamping ``updated`` across a date boundary (MW-27). ``aupdate_state`` from outside the
        graph is recorded in the grounding as tripping a langchain routing bug (``KeyError:
        'model'``); it does not reproduce on our stacks, because that bug needs a graph with *no*
        ``after_model`` middleware and both ``KbValidationMiddleware`` and
        ``HumanInTheLoopMiddleware`` define one. It is still guarded: a failed clear costs one
        redundant flush, while a raise here would replace the run's real outcome with a bookkeeping
        error.

        **A run parked at an approval is left alone (MW-29).** An interrupted turn also ends without
        ``after_agent``, so it reaches this method with a populated touched set and looks exactly
        like a crash — but it is not one, and treating it as one is destructive: ``aupdate_state``
        writes a fresh checkpoint and the pending ``__interrupt__`` write does not survive it, so
        the human's approval simply disappears (executed against the pin, not reasoned). Flushing
        without clearing is no better: the resumed turn's own ``after_agent`` would then see the
        pre-interrupt paths a second time and re-stamp them on the day the human happened to answer.
        So the interrupted turn flushes nothing and the resume flushes once, which is what MW-29
        specifies; the tree being briefly unflushed while a human thinks is the exact gap RT-7's
        startup regeneration exists to close (D-14).
        """
        try:
            state = await graph.aget_state(config)
            if state.interrupts:
                return
            touched = list(state.values.get(KB_TOUCHED) or ())
        except Exception:
            return
        report = await self._maintenance.aflush_pending(touched)
        if report is None:
            return
        with contextlib.suppress(Exception):
            await graph.aupdate_state(config, {KB_TOUCHED: None})

    def _record_proposals(self, request: ApprovalRequest) -> None:
        """Turn one auto-rejected approval into :class:`~pkb.contracts.PendingProposal` rows."""
        for action in request.actions:
            proposal = PendingProposal(
                proposal_id=_new_run_id(),
                agent_id=request.agent_id,
                thread_id=request.thread_id,
                action=action,
                created_at=datetime.now(UTC),
            )
            self._proposals.append(proposal)
            if self.config.proposal_sink is not None:
                self.config.proposal_sink(proposal)

    def _publish_flush(self, report: FlushReport) -> None:
        """Deliver a report and drop the cached tree (MW-24).

        Every flush is a point at which the tree may have changed, so this is the one place the
        snapshot cache is invalidated — including the failure-path flush and the startup
        regeneration, neither of which goes through a middleware hook.
        """
        with self._snapshot_lock:
            self._snapshot = None
        if self.config.flush_sink is not None:
            self.config.flush_sink(report)


# --------------------------------------------------------------------------------------
# Module-level helpers
# --------------------------------------------------------------------------------------


class _DurableGraph:
    """A graph that streams with :data:`DEFAULT_DURABILITY` (RT-46, Q11).

    :func:`pkb.agents.events.stream_events` owns the stream modes and ``subgraphs=True`` — the two
    parts of D-12 that make delegated work visible at all — and deliberately takes the graph as an
    argument rather than growing a durability parameter of its own. Wrapping it here keeps the
    checkpoint policy a *runtime* decision, which is where it belongs: a scan run and a user run
    could reasonably differ, and neither should be a keyword threaded through the normalizer.
    """

    __slots__ = ("_durability", "_graph")

    def __init__(self, graph: AgentGraph, durability: Durability) -> None:
        self._graph = graph
        self._durability = durability

    def astream(self, payload: Any, config: Any, **kwargs: Any) -> AsyncIterator[Any]:
        return self._graph.astream(payload, config, durability=self._durability, **kwargs)


def _build_backend(kb_root: Path) -> BackendProtocol:
    """The one :class:`CompositeBackend` every graph shares (RT-6, RT-20).

    ``StateBackend`` is the default route, so each thread gets its own scratch filesystem and two
    threads writing ``/scratch.md`` never see each other's. ``/kb/`` is the single on-disk tree every
    agent shares — that is what makes README §1.8 rule 4 ("a solution note lives in exactly one
    topic") expressible at all. ``/skills/`` is the packaged-skill mount (SK-3), read-only for every
    agent (RT-17).

    No sandbox backend, ever (RT-20). deepagents registers an ``execute`` tool on every deep agent
    and it is *not* in ``_DEFAULT_FS_TOOL_OPS``, so it bypasses the permission layer entirely — with
    these backends it is inert (``Error: Execution not available … SandboxBackendProtocol``, nothing
    written), and keeping it inert is a Layer 2 obligation rather than a happy accident.
    """
    return CompositeBackend(
        default=StateBackend(),
        routes={
            KB_MOUNT: FilesystemBackend(root_dir=str(kb_root), virtual_mode=True),
            SKILLS_MOUNT: FilesystemBackend(
                root_dir=str(packaged_skills_root()), virtual_mode=True
            ),
        },
    )


def _default_registry(runtime: PkbRuntime) -> AgentRegistry:
    """The production registry: real factories, configured models, gated topic tools."""
    return AgentRegistry(
        runtime.kb_root,
        runtime,
        default_model=runtime.config.default_model,
        models=runtime.config.models,
        tool_factory=runtime.tools_for,
    )


def _payload_factory(message: str) -> Callable[[], Any]:
    """Defer building the LangChain payload until the run is actually admitted.

    Keeps the import of :mod:`langchain_core.messages` out of the module's hot path and, more
    usefully, keeps :meth:`PkbRuntime._stream` free of any knowledge of what a payload *is* — the
    same code path carries a new turn and a ``Command(resume=...)``.
    """

    def build() -> Any:
        return {"messages": [HumanMessage(message)]}

    return build


def _resume_payload(
    pending: ApprovalRequest | None,
    decisions: Sequence[Decision],
    *,
    interrupt_id: str | None,
) -> Any:
    """Validate and build the resume command, outside the graph (RT-40, RT-33).

    :func:`~pkb.agents.approval.to_resume_command` validates again; the explicit call here is what
    lets the staleness check see the ``interrupt_id`` the client believes it is answering, which the
    two-argument form cannot express.
    """
    request = validate_decisions(pending, decisions, interrupt_id=interrupt_id)
    return to_resume_command(request, decisions)


def _new_run_id() -> str:
    """A fresh run id. Never a thread id — those are Layer 3's to mint (RT-36)."""
    return str(uuid4())


_ROLES: Final[Mapping[str, str]] = {"human": "human", "ai": "assistant", "tool": "tool"}
"""LangChain message type → the role a transport renders. ``system`` is deliberately absent."""


def _message_view(message: BaseMessage) -> MessageView | None:
    """One replayed message, or ``None`` for one a human never needs to see."""
    role = _ROLES.get(message.type)
    if role is None:
        return None
    text = message.text
    if not text:
        return None
    return MessageView(role=role, text=text, created_at=None)
