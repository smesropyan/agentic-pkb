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

**3a. The Librarian's turn is a workflow, not a graph run (LB-12 … LB-19).**
Routing used to be a tool the Librarian's model could decline to call, and measured against a real
model it declined. So :meth:`PkbRuntime.run` on the Librarian drives four steps — classify (the one
model call), fan out to every applicable expert, merge their answers by attribution, offer the
threads they ran on — of which only the first is a graph run. The other three are ordinary Python
here and in :mod:`pkb.agents.routing`, which is what makes the fan-out unskippable. Every expert in
the fan-out goes through the *same* :meth:`PkbRuntime._stream` a direct conversation uses, on its own
derived thread, so it inherits the active-run registry, the pending-approval refusal, the write lock
and the flush guard without a second implementation of any of them.

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
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Final, Literal, Self
from uuid import uuid4
from weakref import WeakKeyDictionary

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import BackendProtocol
from deepagents.backends.state import StateBackend
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.base import BaseStore
from langgraph.store.sqlite.aio import AsyncSqliteStore
from langgraph.types import Checkpointer

from pkb.agents.approval import (
    DEFAULT_REASON,
    propose_only_command,
)
from pkb.agents.events import stream_events
from pkb.agents.expert import expert_prompt
from pkb.agents.gates import requires_approval
from pkb.agents.ingestion import (
    Asker,
    IngestionReport,
    SourceFile,
    ingest,
    ingest_tools,
    model_asker,
    reference_file_path,
    resolve_slug,
)
from pkb.agents.middleware.maintenance import (
    FlushSink,
    KbMaintenanceMiddleware,
    KbWriteLock,
)
from pkb.agents.middleware.state import KB_TOUCHED
from pkb.agents.paths import KB_MOUNT, SKILLS_MOUNT, to_kb_relative
from pkb.agents.registry import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_MODEL,
    AgentGraph,
    AgentRegistry,
)
from pkb.agents.routing import (
    RETRY_INSTRUCTION as ROUTE_RETRY,
)
from pkb.agents.routing import (
    TOPIC_GAP_INSTRUCTION,
    FanOut,
    expert_thread_id,
    librarian_thread_id,
    merge_reply,
    read_decision,
    resolve_targets,
    routing_menu,
)
from pkb.agents.scans import SqliteScanQueue, run_scan
from pkb.agents.skills import packaged_skills_root
from pkb.agents.tools.topics import TopicToolEnv, topic_tools
from pkb.contracts import (
    AgentDescriptor,
    AgentEvent,
    ApprovalMode,
    ApprovalPendingError,
    ApprovalRequest,
    FlushReport,
    InterruptEvent,
    MessageComplete,
    MessageView,
    PendingProposal,
    RunEnd,
    RunError,
    ScanQueue,
    ScanRequest,
    ScanResult,
    ThreadBusyError,
    UnknownAgentError,
)
from pkb.core import regenerate_all
from pkb.core.models import TopicRecord
from pkb.core.paths import LIBRARIAN_AGENT_ID
from pkb.core.scan import scan
from pkb.sources import (
    SourceNotFoundError,
    StagedSource,
    canonical_origin,
    check_fetchable,
    is_url,
    slug_for,
    stage,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import BaseMessage
    from langchain_core.runnables import RunnableConfig
    from langchain_core.tools import BaseTool

    from pkb.core.models import KbSnapshot

__all__ = [
    "DEFAULT_DURABILITY",
    "DEFAULT_FANOUT_LIMIT",
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

DEFAULT_FANOUT_LIMIT: Final = 3
"""How many experts a Librarian turn may run at once (LB-15).

Three, because the deployment is an Ollama Pro plan whose stated allowance is **three concurrent
cloud models** (Q6). A five-topic question that fired five concurrent runs would have two of them
answered with a ``429``, and the merged reply would report a knowledge-base failure that is really a
plan limit. The cap is configuration rather than a constant in the fan-out because the plan is the
thing that varies: a deployment running everything locally can raise it, and one metered harder can
drop it to 1 without any code understanding why.
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

_ACQUIRE_POLL_SECONDS: Final = 0.02
"""How long one off-loop attempt at the write mutex may hold an executor worker (RT-51).

Short enough that a waiter always hands the worker back before the current holder's own
``asyncio.to_thread(flush, …)`` can be starved of one, long enough that a contended acquire costs a
handful of executor round trips rather than a spin. The lock is only ever held across a tree walk
(RT-52), so waits are short by construction.
"""


class ReentrantWriteLock:
    """One process-wide knowledge-base write lock, safe to re-enter and usable from both worlds.

    Satisfies :class:`~pkb.agents.middleware.maintenance.KbWriteLock`: both the synchronous and the
    asynchronous context-manager protocol, because MW-2 makes the *same* critical section run from
    ``after_agent`` (the non-live suite drives ``invoke()``) and from ``aafter_agent`` (the daemon).

    **Why a ``threading.Lock`` underneath (D-7).** An ``asyncio.Lock`` alone cannot be acquired from
    a synchronous hook at all, and it provides no exclusion against a hook running on a worker
    thread — which is where :func:`asyncio.to_thread` puts Layer 1's tree walk, and where langchain
    puts a synchronous tool call reached from ``ainvoke``. So the mutual exclusion that actually
    holds across both worlds is a plain mutex, released directly (a ``threading.Lock``, unlike an
    ``RLock``, may be released by a different thread than acquired it).

    **Why an ``asyncio.Lock`` on top of it, one per event loop (RT-51, RT-53).** Waiting for the
    mutex from a coroutine means parking a worker of the loop's *default* executor for the whole
    wait — and the task that currently holds the lock needs a worker from that same pool for
    ``asyncio.to_thread(flush, …)``. With N waiters and a pool of ``min(32, cpu_count + 4)``, N+1
    concurrent flushes park every worker on ``mutex.acquire`` and the holder's flush can never be
    scheduled: a permanent, process-wide deadlock, and nothing recovers without a restart. The
    ``asyncio.Lock`` is the queue async waiters actually wait on — it costs no thread — so at most
    one task *per loop* is ever off-loop waiting for the mutex. That is also the construction RT-53
    prescribes ("an ``asyncio.Lock`` plus a per-``asyncio.Task`` depth counter"); the mutex beneath
    it is what extends the exclusion to the synchronous world.

    **Why the off-loop acquire is bounded and shielded.** Bounded (:data:`_ACQUIRE_POLL_SECONDS`)
    so the one remaining worker is handed back between attempts and a waiter can never starve the
    holder even on a one-worker executor. Shielded because cancelling the awaiting coroutine —
    :meth:`PkbRuntime.cancel`, RT-46, or a consumer that stops iterating — does **not** stop the
    worker thread: it goes on to take the mutex, ``_copy_future_state`` drops the result on the
    floor because the destination future is already cancelled, and the mutex is then held by nobody
    with ``depth == 0`` and ``_owner is None``. Every later flush, scaffold and ``regenerate()``
    blocks forever. The shield keeps the inner future alive so its done-callback can hand a mutex
    won after the cancellation straight back.

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
        self._gate: asyncio.Lock | None = None
        """The per-loop gate this owner is holding, when it acquired through :meth:`__aenter__`."""

        self._gates: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
            WeakKeyDictionary()
        )
        """One gate per event loop. Keyed weakly so a closed loop's gate does not outlive it, and
        per-loop because an ``asyncio.Lock`` binds to the first loop that awaits it."""

        self._gates_lock = threading.Lock()
        """Guards :attr:`_gates` — two event loops on two threads may reach it at once."""

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

    def _claim(self, gate: asyncio.Lock | None = None) -> None:
        """Record this owner. *gate* is the per-loop gate an async acquire is holding for it."""
        self._owner = self._owner_key()
        self._gate = gate
        self.depth = 1
        self.acquisitions += 1

    def _release(self) -> None:
        """Give the lock back at depth 0 — the mutex first, then the gate.

        That order matters: the next task out of the gate finds the mutex already free and takes it
        on the non-blocking fast path, without a poll cycle.
        """
        self.depth -= 1
        if self.depth == 0:
            self._owner = None
            gate, self._gate = self._gate, None
            self._mutex.release()
            if gate is not None:
                gate.release()

    def _gate_for_loop(self) -> asyncio.Lock:
        """The gate async waiters on *this* loop queue on (see the class docstring)."""
        loop = asyncio.get_running_loop()
        with self._gates_lock:
            gate = self._gates.get(loop)
            if gate is None:
                gate = asyncio.Lock()
                self._gates[loop] = gate
            return gate

    async def _acquire_off_loop(self) -> None:
        """Take the mutex from a worker thread without blocking the loop, cancel-safe (RT-51).

        Each attempt is bounded so the worker goes back to the pool between tries — the holder needs
        one for its own ``to_thread`` — and shielded so a cancellation cannot orphan an acquire that
        is about to succeed. The done-callback is attached to the *shielded* future on purpose:
        attaching it to the outer one would never fire with a result, because
        ``asyncio.futures._copy_future_state`` refuses to deliver into a cancelled destination,
        which is the leak itself.
        """
        while True:
            attempt = asyncio.ensure_future(
                asyncio.to_thread(self._mutex.acquire, True, _ACQUIRE_POLL_SECONDS)
            )
            try:
                acquired = await asyncio.shield(attempt)
            except asyncio.CancelledError:
                attempt.add_done_callback(self._hand_back)
                raise
            if acquired:
                return

    def _hand_back(self, attempt: asyncio.Future[bool]) -> None:
        """Release a mutex won by an acquire whose waiter is already gone (RT-51)."""
        if attempt.cancelled() or attempt.exception() is not None:
            return
        if attempt.result():
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
        """Acquire without blocking the event loop and without starving the current holder.

        Three steps, each of them load-bearing (see the class docstring for why):

        1. queue on this loop's ``asyncio.Lock``, which costs no thread, so N waiting tasks cannot
           exhaust the executor the holder's own ``to_thread`` needs;
        2. try the mutex non-blockingly on the loop — the uncontended case, and the common one;
        3. only then go off-loop, in bounded, shielded attempts.

        Every other run keeps streaming throughout — RT-51's "runs on different threads stream
        concurrently; only the flush is serialized".
        """
        if self._take():
            return self
        gate = self._gate_for_loop()
        await gate.acquire()
        try:
            if not self._mutex.acquire(blocking=False):
                await self._acquire_off_loop()
        except BaseException:
            # Includes the cancellation raised out of `_acquire_off_loop`: leaving the gate held
            # would wedge every other task on this loop just as thoroughly as leaking the mutex.
            gate.release()
            raise
        self._claim(gate)
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

    default_model: str | BaseChatModel = DEFAULT_MODEL
    """The model every agent runs on unless :attr:`models` names another (RG-21).

    A deployment configures a spec string; a :class:`~langchain_core.language_models.BaseChatModel`
    is accepted so a test can drive the whole runtime — including the graph-free ingestion loop,
    which reaches the model directly rather than through a compiled graph — with one scripted model
    and no key.
    """

    models: Mapping[str, str | BaseChatModel] = field(default_factory=dict)
    """Per-agent-id overrides, e.g. ``{"topic/cooking": "ollama:qwen4:32b-thinking"}``."""

    fallback_model: str | None = DEFAULT_FALLBACK_MODEL
    """The model that answers when the chosen one hits quota, concurrency or an unreachable
    endpoint (RG-21, :func:`pkb.agents.models.with_fallback`).

    A deployment setting like every other field here, and for the same reason: the tree holds
    knowledge, not the answer to "what do we run when the cloud plan says no". ``None`` disables the
    failover entirely, which is what a deployment with no local model should set — better a clean
    ``429`` than a fallback that cannot be built.
    """

    source_roots: tuple[Path, ...] = ()
    """Directories a source may be ingested from. Empty means **the human's home directory**.

    ``ingest_source`` is the one tool that reads outside the knowledge base, and ``origin`` is a
    string the *model* chooses. Every other read an expert can make is confined by the backend —
    ``FilesystemBackend(root_dir=kb_root, virtual_mode=True)`` refuses traversal outright — so
    without a bound here a single tool call reads any file the daemon's user can read, puts its
    text in front of the model, and copies it byte-for-byte into the tree. Reproduced with
    ``~/.ssh/id_rsa``: staged, ingested, and copied into a topic as an ordinary reference.

    The default is the home directory rather than the empty set because refusing everything would
    make the feature unusable on day one for the person it is built for, and because the realistic
    threat is not a user who cannot reach their own files — it is a prompt-injected model reaching
    for ``/etc``, a mounted volume, or another user's home. A deployment that wants it tighter names
    the directories it wants; a deployment that wants a sealed system names one it controls.
    """

    allow_url_sources: bool = True
    """Whether a source may be fetched over HTTP at all.

    The URL branch is the same read surface over the network: nothing restricted the host, so
    ``http://169.254.169.254/…`` and ``http://localhost:…`` were both reachable from inside a model
    turn. Link-local, loopback and private addresses are refused whatever this is set to; this
    switch turns the whole branch off for a deployment that ingests only local files.
    """

    durability: Durability = DEFAULT_DURABILITY
    """Checkpoint durability for every run. See :data:`DEFAULT_DURABILITY`."""

    fanout_limit: int = DEFAULT_FANOUT_LIMIT
    """How many experts one Librarian turn may run concurrently (LB-15). See the default."""

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
        self._reading_locks: dict[tuple[str, str], asyncio.Lock] = {}
        """One ingestion at a time per ``(agent, source)`` — see :meth:`_reading_lock`."""

        self._staging_lock = asyncio.Lock()
        """Serializes :func:`pkb.sources.stage` (LS-8). Deliberately *not* the knowledge-base write
        lock: staging writes only inside ``<kb>/.inbox/``, which no flush and no generator touches,
        and it can spend thirty seconds on a download — holding the process-wide lock across that
        would stall every other thread's flush for the duration (RT-52's argument)."""
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
            snapshots=self,
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

        **Freshness contract (RT-25, RT-28).** The view a caller gets reflects every change made
        through this process up to the last completed tool call. The cache is dropped by, and only
        by, these events:

        * a successful knowledge-base mutation through the tool layer — ``KbMaintenanceMiddleware``
          calls :meth:`invalidate_snapshot` from its innermost ``wrap_tool_call`` (EX-14), so the
          next ``after_model`` gate evaluation sees the file the previous call just wrote;
        * a ``create_topic``/``create_subtopic`` scaffold, through :meth:`invalidate` (RG-16);
        * any flush, including the failure-path flush and the startup regeneration
          (:meth:`_publish_flush`).

        What it does **not** cover is a change made behind the tool layer's back — a human editing
        the tree by hand while a turn runs. Anything that must be right about the *disk* rather than
        about the last write reads the filesystem directly (:func:`pkb.core.paths.owning_topic_root`
        is how the gates do it) rather than trusting this.
        """
        with self._snapshot_lock:
            if self._snapshot is None:
                self._snapshot = scan(self.kb_root)
            return self._snapshot

    def invalidate_snapshot(self) -> None:
        """Drop the cached tree, and only that (RT-25, RT-28).

        Satisfies :class:`~pkb.agents.middleware.maintenance.SupportsSnapshotInvalidate`. The
        compiled graphs stay: a note landing under ``Cooking/recipes/`` changes what the gates must
        see on the very next tool call, and changes nothing about any agent's prompt, skills or
        catalog entry — so calling the whole of :meth:`invalidate` here would recompile the
        Librarian and every expert once per written file (RG-16), which is a far bigger hammer than
        the problem.
        """
        with self._snapshot_lock:
            self._snapshot = None

    def invalidate(self, agent_id: str | None = None) -> None:
        """Drop the cached tree and the compiled graphs the tree makes wrong (RG-16, MW-30).

        Satisfies :class:`~pkb.agents.middleware.maintenance.SupportsInvalidate`, so the maintenance
        middleware and the topic-creation tools can be handed the runtime rather than the registry:
        a rewritten ``expert.md``, skill or ``topic.md`` invalidates *both* caches through one call,
        and a caller cannot refresh one and forget the other.
        """
        self.invalidate_snapshot()
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
        rid = run_id or _new_run_id()
        if agent_id == LIBRARIAN_AGENT_ID:
            # Four steps, not one graph run (LB-12). See `_librarian_turn`.
            async for event in self._librarian_turn(
                thread_id,
                _payload_factory(message),
                message=message,
                run_id=rid,
                approval_mode=approval_mode,
                refuse_when_pending=True,
            ):
                yield event
            return
        async for event in self._stream(
            agent_id,
            thread_id,
            _payload_factory(message),
            run_id=rid,
            approval_mode=approval_mode,
            refuse_when_pending=True,
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

        A Librarian turn drives several graphs under one run id — the classification, then one run
        per expert in the fan-out — so the bookkeeping key is ``<run_id>`` or
        ``<run_id>::<agent_id>`` and cancelling the turn cancels the whole family. Anything narrower
        would leave expert runs alive after the human cancelled the question that started them.
        """
        for key, task in list(self._tasks.items()):
            if key == run_id or key.startswith(f"{run_id}{_TASK_KEY_SEPARATOR}"):
                task.cancel()

    # ----------------------------------------------------------------------------------
    # History, threads (RT-43, RT-48, RT-49)
    # ----------------------------------------------------------------------------------

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
        """Erase a thread's checkpoints and writes, and those of the experts it routed to (RT-48).

        Exposed here because Layer 3 may not import langgraph (I2).

        **The derived threads are the part that is easy to get wrong.** Before the routing workflow,
        delegated work checkpointed under the parent's own ``thread_id`` in a nested
        ``checkpoint_ns`` (D-6), so one ``adelete_thread`` took it all. Now every expert in a
        fan-out runs on ``<thread_id>::<agent_id>`` (LB-14) — addressable, which was the point, and
        therefore *not* removed by deleting the parent. A "delete this conversation" that left the
        expert's copy of the material behind would be the worst kind of lie in a system with no
        version control and no undo (D6), so the derived ids are enumerated from the catalog and
        deleted too. Deleting a thread that never existed is a no-op, which is what makes
        enumerating the whole catalog cheaper than remembering who ran.
        """
        if not isinstance(self.checkpointer, BaseCheckpointSaver):
            msg = "the runtime is closed; open it with PkbRuntime.open(...)"
            raise RuntimeError(msg)
        await self.checkpointer.adelete_thread(thread_id)
        if librarian_thread_id(thread_id) is not None:
            # Already a derived thread: deleting an expert's conversation must not reach sideways
            # into its siblings or upwards into the Librarian's.
            return
        for descriptor in self._registry.list_agents():
            if descriptor.agent_id != LIBRARIAN_AGENT_ID:
                await self.checkpointer.adelete_thread(
                    expert_thread_id(thread_id, descriptor.agent_id)
                )

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
    # Large-source ingestion (LS-1 … LS-12)
    # ----------------------------------------------------------------------------------

    async def ingest(
        self,
        agent_id: str,
        origin: str,
        *,
        confirm: bool = False,
        refresh: bool = False,
        maintain: bool = True,
        asker: Asker | None = None,
    ) -> IngestionReport:
        """Read one source into one topic, section by section (LS-1 … LS-12).

        The workflow, not a graph run — the same shape as the Librarian's routing turn, and for the
        same reason. A model that decides when a book has been read enough will decide it has, and
        say so convincingly; a loop in code cannot. :func:`pkb.agents.ingestion.ingest` is where that
        loop lives; this method supplies it with the four things only the runtime has: the staged
        source, the expert's own model and prompt, the cached tree the gates read, and the flush.

        **A source already here produces an offer, never a silent re-read or a silent skip
        (LS-11).** Re-reading a book is expensive and skipping it is surprising, so when the topic
        already holds a *complete* reading of this origin the method reads nothing and returns a
        report saying so. An **incomplete** reading is a different case and is resumed without
        asking: nobody chose to stop at chapter 14, and continuing loses nothing.

        Args:
            agent_id: The topic expert doing the reading. The Librarian cannot ingest — it holds no
                write capability (RT-16) and no lens to read through (LB-5).
            origin: A filesystem path or a URL. Staged into ``<kb>/.inbox/`` (LS-8), extracted if it
                is binary, and **both are kept** (LS-7).
            confirm: The human said yes to a re-reading. Only meaningful when a complete reading is
                already on disk.
            refresh: Extract the source again rather than reusing the cached extraction (LS-9) —
                for after an extractor upgrade.
            maintain: Flush this run's writes here. The tool path passes ``False`` and hands the
                paths to ``kb_touched`` instead, so the graph's own single flush stamps them
                (MW-20); a direct caller has no graph and needs this.
            asker: Overrides how the expert is reached. The default puts one section in front of
                the agent's configured model with the expert's own system prompt.

        Raises:
            UnknownAgentError: ``agent_id`` names no topic (RG-13).
            pkb.sources.SourceError: The source could not be staged or extracted — a scanned PDF, an
                encrypted one, a URL that would not load. Loud at the start rather than a confident
                summary of nothing (LS-7).
        """
        async with self._reading_lock(agent_id, origin):
            return await self._ingest(
                agent_id, origin, confirm=confirm, refresh=refresh, maintain=maintain, asker=asker
            )

    async def _ingest(
        self,
        agent_id: str,
        origin: str,
        *,
        confirm: bool,
        refresh: bool,
        maintain: bool,
        asker: Asker | None,
    ) -> IngestionReport:
        """One reading, with the ``(agent, source)`` slot already held. See :meth:`ingest`."""
        topic = self._topic_for(agent_id)
        staged, offered = await self._stage(topic, origin, confirm=confirm, refresh=refresh)
        if staged is None:
            return IngestionReport(
                agent_id=agent_id,
                topic_path=topic.path,
                origin=origin,
                slug=offered,
                path=reference_file_path(topic.path, offered),
                offered_reingest=True,
            )
        report = await ingest(
            self.kb_root,
            topic,
            staged,
            ask=asker or self._asker(agent_id, topic),
            snapshot=self.snapshot,
            today=self.clock(),
            agent_id=agent_id,
            lock=self.write_lock,
        )
        if maintain and report.touched:
            await self._maintenance.aflush_pending(list(report.touched))
        return report

    def _reading_lock(self, agent_id: str, origin: str) -> asyncio.Lock:
        """One reading of one source into one topic at a time (RT-60, RT-61).

        Narrow on purpose. The knowledge-base write lock cannot serve here: the loop spends most of
        its life awaiting a model, and RT-52 forbids holding that lock across a model call — an
        approval on another thread would sit behind a book. But two readings of the *same* source
        into the *same* topic genuinely race, because each one reads the file, appends to it and
        writes it back, and the write lock only makes the last step atomic. The loser's whole pass
        would be silently overwritten, which for a 300-page book is an expensive kind of nothing.

        Keyed on ``(agent, origin)``, so two topics ingesting one book still run concurrently — that
        is decision G's whole point (LS-4) — and one topic ingesting two books does too.
        """
        return self._reading_locks.setdefault((agent_id, origin), asyncio.Lock())

    def _topic_for(self, agent_id: str) -> TopicRecord:
        """The topic an agent id addresses, or a typed 404 (RG-11, RG-13)."""
        topic = self._topic_or_none(agent_id)
        if topic is None:
            raise UnknownAgentError(f"no topic answers to the id {agent_id!r}")
        return topic

    def _topic_or_none(self, agent_id: str) -> TopicRecord | None:
        """The topic record behind an agent id, read off the snapshot Layer 1 produced (RG-2).

        ``None`` for the Librarian — which is not a topic and holds no write capability at all
        (RT-16, LB-5) — and for an id the current snapshot does not know, which is how
        :meth:`tools_for` stays silent about a stale catalog entry rather than failing the graph
        build the registry is in the middle of.
        """
        if agent_id == LIBRARIAN_AGENT_ID:
            return None
        for record in self.snapshot().topics.values():
            if record.agent_id == agent_id:
                return record
        return None

    async def _stage(
        self, topic: TopicRecord, origin: str, *, confirm: bool, refresh: bool
    ) -> tuple[StagedSource | None, str]:
        """Stage the source, or answer ``(None, slug)`` when the human should be asked (LS-11).

        The "already here?" question is answered from **the topic**, before anything is fetched: the
        reading record lives in ``<topic>/references/<slug>/<slug>.md``, and the folder is found by
        the origin recorded in its own provenance block, so the answer costs one directory listing
        and no network.

        It used to be answered from ``<kb>/.inbox/<slug>/source.json`` instead, on the reasoning
        that a source never staged cannot have been ingested. That is false, and the spec says so
        two rules earlier: LS-9 makes ``.inbox`` a disposable cache — clearing it is the first thing
        anyone does to reclaim the disk an 18.8 MB PDF took — while the reading state lives in the
        tree. So ``rm -rf .inbox`` turned a finished book into an unread one and a housekeeping
        command into an hour of unattended model calls, with a spurious second pass recorded in the
        provenance and nobody asked.

        Staging itself runs off the event loop and under the staging lock rather than the
        knowledge-base write lock: it writes only inside ``<kb>/.inbox/``, which no flush and no
        generator touches, and holding the KB lock across a thirty-second download would freeze
        every other thread's flush for that long (RT-52's argument, applied to I/O rather than to an
        approval).
        """
        recorded = canonical_origin(origin)
        self._check_origin_allowed(recorded)
        slug = await asyncio.to_thread(
            resolve_slug, self.kb_root, topic.path, recorded, slug_for(recorded)
        )
        if not refresh and not confirm and self._is_read(topic, slug):
            return None, slug
        async with self._staging_lock:
            staged = await asyncio.to_thread(stage, self.kb_root, origin, refresh=refresh)
        return staged, staged.slug

    def _check_origin_allowed(self, origin: str) -> None:
        """The read surface ``ingest_source`` opens, bounded — see :attr:`RuntimeConfig.source_roots`.

        Raises :class:`pkb.sources.SourceNotFoundError` rather than a new type, so the tool's
        existing handler relays a refusal the model can act on instead of aborting the superstep.
        The message names the setting, because the person who hits this legitimately — a book on an
        external drive — needs to know what to change.
        """
        if is_url(origin):
            if not self.config.allow_url_sources:
                raise SourceNotFoundError(
                    "this deployment does not ingest sources over the network "
                    "(RuntimeConfig.allow_url_sources is off)"
                )
            check_fetchable(origin)
            return

        roots = self.config.source_roots or (Path.home(),)
        target = Path(origin)
        if any(target == root or root in target.parents for root in roots):
            return
        allowed = ", ".join(str(root) for root in roots)
        raise SourceNotFoundError(
            f"{origin} is outside the directories this knowledge base ingests from ({allowed}). "
            f"Ask the human to move the source there, or to add its directory to "
            f"RuntimeConfig.source_roots."
        )

    def _is_read(self, topic: TopicRecord, slug: str) -> bool:
        """Does this topic already hold a *complete* reading of this source (LS-5, LS-11)?

        Complete is the operative word, and the reading record on disk is the only place it is
        recorded — the file is the resume state, and a second store of progress is a second thing
        that can be wrong. An interrupted pass therefore resumes silently while a finished one asks.
        """
        target = self.kb_root / reference_file_path(topic.path, slug)
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        records = SourceFile.load(text).passes()
        return bool(records) and records[-1].complete

    def _asker(self, agent_id: str, topic: TopicRecord) -> Asker:
        """How the expert answers one section: its own model, its own system prompt.

        The prompt is :func:`pkb.agents.expert.expert_prompt` — the non-overridable standards
        preamble with the topic's ``expert.md`` or the shipped template beneath it (EX-4) — so the
        lens is the topic's own even though no graph runs. The model comes from the **registry**
        (RG-21), which is what carries the configured failover: reading ``config.default_model`` and
        calling ``init_chat_model`` on it directly is how this became the only path in the system
        that could not survive an exhausted quota, on the operation that spends the most of it.
        """
        chosen = self._registry.chat_model_for(agent_id)
        model = init_chat_model(chosen) if isinstance(chosen, str) else chosen
        return model_asker(model, expert_prompt(self.kb_root, topic))

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
        """The extra tools one agent carries (LB-7, EX-12, LS-11).

        Passed to the registry as its ``tool_factory``. Every one of them is genuinely per-agent —
        ``create_subtopic`` may only build inside its own root, and ``ingest_source`` reads into its
        own topic — so one shared instance cannot serve every agent, and the agent id is the only
        thing that says which topic is being written into.
        """
        env = TopicToolEnv(
            kb_root=self.kb_root,
            snapshot=self.snapshot,
            lock=self.write_lock,
            registry=self,
            clock=self.clock,
        )
        return [
            *topic_tools(env, agent_id),
            *ingest_tools(self, agent_id, is_expert=self._topic_or_none(agent_id) is not None),
        ]

    # ----------------------------------------------------------------------------------
    # The Librarian's routing workflow (LB-12 … LB-19)
    # ----------------------------------------------------------------------------------

    @property
    def fanout_limit(self) -> int:
        """How many experts one turn may run at once — :class:`~pkb.agents.routing.FanOutHost`."""
        return self.config.fanout_limit

    def expert_stream(
        self,
        agent_id: str,
        thread_id: str,
        message: str,
        *,
        run_id: str,
        approval_mode: ApprovalMode,
    ) -> AsyncIterator[AgentEvent]:
        """One expert's run inside a fan-out — :class:`~pkb.agents.routing.FanOutHost` (LB-15).

        It is deliberately :meth:`_stream`, the same call a direct conversation with that expert
        makes, differing only in the thread it runs on. That is what preserves "you are changing who
        decides to call the expert, not what an expert is": the expert's own prompt, skills chain,
        breadth middleware, validation and maintenance middleware and topic-scoped permissions all
        come from its compiled graph, and every runtime guarantee — the active-run registry (RT-45),
        the pending-approval refusal (RT-39), the write lock (RT-51) and the flush on both paths
        (MW-26) — comes from this method. A fan-out that reached into the graph directly would have
        needed its own copy of all eight, and the copy is where they rot.

        ``task_key`` scopes the run's cancellation bookkeeping to ``<run_id>::<agent_id>`` so several
        experts sharing one turn's run id do not evict each other from :attr:`_tasks` (see
        :meth:`cancel`). Events still carry the turn's ``run_id``: a client is watching one turn.
        """
        return self._stream(
            agent_id,
            thread_id,
            _payload_factory(message),
            run_id=run_id,
            approval_mode=approval_mode,
            refuse_when_pending=True,
            task_key=f"{run_id}{_TASK_KEY_SEPARATOR}{agent_id}",
        )

    async def _librarian_turn(
        self,
        thread_id: str,
        payload: Callable[[], Any],
        *,
        message: str | None,
        run_id: str,
        approval_mode: ApprovalMode,
        refuse_when_pending: bool,
    ) -> AsyncIterator[AgentEvent]:
        """Classify, fan out, merge, offer — one Librarian turn (LB-12 … LB-19).

        The slot for ``(librarian, thread_id)`` is held across all four steps rather than for the
        classification alone (RT-45). A second turn arriving while the fan-out is still running would
        otherwise be admitted, classify against a thread whose reply has not been written yet, and
        route the same item twice.

        Step 1 is the only graph run on this thread, and three of its endings are not routing:

        * it **raised** — one ``run.error`` was already emitted (RT-47); nothing is fanned out,
          because a classification that failed named no topics;
        * it **parked on a gate** — the model proposed ``create_topic`` for a topic gap and the
          human's decision is pending on this thread (LB-7). The turn ends there and RT-39 refuses
          the next message until it is answered, which is correct: the item is waiting on a topic;
        * it **answered in prose** even after :class:`~pkb.agents.routing.RouteMiddleware` forced its
          one retry — the human gets the menu (LB-19), never a guess.
        """
        key = (LIBRARIAN_AGENT_ID, thread_id)
        if key in self._active:
            raise ThreadBusyError(
                f"a run is already active on thread {thread_id!r} for the librarian"
            )
        self._active[key] = run_id
        try:
            graph = self._registry.get(LIBRARIAN_AGENT_ID)
            config = self.thread_config(thread_id)
            ended: RunEnd | None = None
            failed = False
            async for event in self._stream(
                LIBRARIAN_AGENT_ID,
                thread_id,
                payload,
                run_id=run_id,
                approval_mode=approval_mode,
                refuse_when_pending=refuse_when_pending,
                claim=False,
            ):
                if isinstance(event, RunEnd):
                    ended = event
                    continue
                failed = failed or isinstance(event, RunError)
                yield event
            if failed or ended is None:
                return
            state = await graph.aget_state(config)
            if state.interrupts:
                yield ended
                return
            async for event in self._route(
                graph,
                config,
                thread_id,
                message=message,
                classification=ended,
                run_id=run_id,
                approval_mode=approval_mode,
            ):
                yield event
        finally:
            self._release_slot(key, run_id)

    async def _route(
        self,
        graph: AgentGraph,
        config: RunnableConfig,
        thread_id: str,
        *,
        message: str | None,
        classification: RunEnd,
        run_id: str,
        approval_mode: ApprovalMode,
    ) -> AsyncIterator[AgentEvent]:
        """Steps 2 to 4, in code, over whatever step 1 decided (LB-15 … LB-19)."""
        state = await graph.aget_state(config)
        catalog = [
            descriptor
            for descriptor in self._registry.list_agents()
            if descriptor.agent_id != LIBRARIAN_AGENT_ID
        ]
        decision = read_decision(state.values)
        targets, unknown = resolve_targets(decision, catalog)

        if not targets:
            if not catalog:
                # A topic gap with nothing to choose from — the bootstrapping case (LB-6). A menu of
                # no experts is not a choice, so the turn goes back to the Librarian for the one
                # thing it can do about a gap: propose a topic, gated, for the human (LB-7).
                async for event in self._topic_gap(thread_id, run_id, approval_mode):
                    yield event
                return
            reply = routing_menu(catalog, prose=classification.final_text)
            async for event in self._deliver(graph, config, reply, run_id=run_id):
                yield event
            return

        fan = FanOut(
            self,
            targets,
            _forwarded_message(message, state.values),
            thread_id=thread_id,
            run_id=run_id,
            approval_mode=approval_mode,
            reason=decision.reason if decision is not None else "",
        )
        async for event in fan.stream():
            yield event
        async for event in self._deliver(
            graph, config, merge_reply(fan.outcomes, unknown=unknown), run_id=run_id
        ):
            yield event

    async def _topic_gap(
        self, thread_id: str, run_id: str, approval_mode: ApprovalMode
    ) -> AsyncIterator[AgentEvent]:
        """Hand an unroutable item back to the Librarian to propose a topic (LB-6, LB-7).

        A second turn on the same thread rather than a branch inside the first, because what happens
        next is a *conversation*: the model proposes, the ``create_topic`` gate fires, the human
        approves, edits the name or declines, and the thread carries all of it. Its events —
        including that interrupt and its own terminal event — are forwarded unchanged, so this path
        looks to a client exactly like the topic-creation flow it is.
        """
        async for event in self._stream(
            LIBRARIAN_AGENT_ID,
            thread_id,
            _payload_factory(TOPIC_GAP_INSTRUCTION),
            run_id=run_id,
            approval_mode=approval_mode,
            refuse_when_pending=False,
            claim=False,
        ):
            yield event

    async def _deliver(
        self, graph: AgentGraph, config: RunnableConfig, reply: str, *, run_id: str
    ) -> AsyncIterator[AgentEvent]:
        """Record the turn's answer on the Librarian's thread and end the turn with it (LB-18).

        The reply is appended to the thread's messages because it *is* the Librarian's turn: without
        it ``history`` would show a routing tool call and nothing else, and the next turn's
        classification would have no idea what was already said. It is written with
        ``aupdate_state`` rather than by a graph node because no model produced it — that is the
        guarantee, not an implementation detail.

        A failed append is swallowed. The human has the answer either way, and replacing a delivered
        reply with a bookkeeping error would be the worse of the two failures.
        """
        with contextlib.suppress(Exception):
            await graph.aupdate_state(config, {"messages": [AIMessage(content=reply)]})
        yield MessageComplete(run_id=run_id, agent_id=LIBRARIAN_AGENT_ID, text=reply)
        yield RunEnd(run_id=run_id, final_text=reply)

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
        claim: bool = True,
        task_key: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Register the run, drive it in a task, and forward its events.

        The run is driven by a *task* rather than inline in this generator for two reasons, both
        load-bearing. It is what :meth:`cancel` cancels (RT-46). And it is what makes MW-26's flush
        guard unconditional: a consumer that stops iterating half way through — an SSE client that
        disconnects, a ``break`` in a caller's loop — leaves an abandoned async generator whose
        ``finally`` runs at some unspecified later point, while a task's ``finally`` always runs.

        The active-run slot is released by that same task, for that same reason (RT-45): a client
        that stops reading at the terminal event — the natural way to consume this stream — and
        keeps the generator referenced (an SSE handler frame, a stored iterator) would otherwise
        hold the slot for as long as it lives, and the next turn on that ``(agent_id, thread_id)``
        would be refused with a 409 against a run that finished. The release here is the second
        half, for a stream abandoned *mid-run*; both go through :meth:`_release_slot`, which is
        identity-guarded because they can run out of order.

        The busy check and the registration happen before the first ``await`` in this coroutine, so
        two runs started concurrently on one thread cannot both see a free slot (RT-45).

        ``claim=False`` is for the one caller that already holds the slot: a Librarian turn takes
        ``(librarian, thread)`` for its whole four-step workflow and drives the classification
        through here, so re-taking it would refuse the turn against itself and releasing it at the
        end of step 1 would let a second turn in while the fan-out is still running.
        """
        graph = self._registry.get(agent_id)
        key = (agent_id, thread_id)
        if claim:
            if key in self._active:
                raise ThreadBusyError(
                    f"a run is already active on thread {thread_id!r} for agent {agent_id!r}"
                )
            self._active[key] = run_id
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue(maxsize=EVENT_BUFFER_SIZE)
        task: asyncio.Task[None] | None = None
        bookkeeping = task_key or run_id
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
                    release_slot=claim,
                )
            )
            self._tasks[bookkeeping] = task
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
            self._tasks.pop(bookkeeping, None)
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if claim:
                self._release_slot(key, run_id)

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
        release_slot: bool = True,
    ) -> None:
        """Execute the graph and push normalized events into *queue* (RT-42, RT-43, RT-45, MW-26).

        The exit chain is three nested ``finally`` blocks and the nesting is the whole point. The
        flush must run whatever happened (MW-26); the terminal sentinel must be queued even if the
        flush raises, or the consumer waits on :meth:`asyncio.Queue.get` forever and ``run()``
        never returns (a broken :attr:`RuntimeConfig.flush_sink` or a locked scan database is enough
        to do it); and the active-run slot must be freed even if *that* is cancelled — a bounded
        queue plus a consumer that walked away can park ``put`` indefinitely, and a slot that
        outlives its run is a permanently 409 thread (RT-45).
        """
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
            try:
                try:
                    await self._flush_pending(graph, config)
                finally:
                    await queue.put(None)
            finally:
                if release_slot:
                    self._release_slot((agent_id, thread_id), run_id)

    def _release_slot(self, key: tuple[str, str], run_id: str) -> None:
        """Free the active-run slot, but only if *run_id* still owns it (RT-45).

        The guard is not defensive coding. This is called from two places that genuinely run out of
        order — the drive task's ``finally`` when the run ends, and an abandoned generator's
        finalization some unspecified number of loop ticks later — so an unguarded ``pop`` would let
        a finished run evict the slot of the *next* run on that thread, and a third concurrent run
        would then be admitted. That is an RT-45 breach in the opposite direction from the one this
        method exists to close.
        """
        if self._active.get(key) == run_id:
            del self._active[key]

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

        A flush rewrites the derived files, so the cached tree is stale by definition afterwards —
        including on the failure-path flush and the startup regeneration, neither of which goes
        through a middleware hook. It is *not* the only invalidation point: see
        :meth:`snapshot` for the full freshness contract.
        """
        self.invalidate_snapshot()
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
        fallback_model=runtime.config.fallback_model,
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


def _new_run_id() -> str:
    """A fresh run id. Never a thread id — those are Layer 3's to mint (RT-36)."""
    return str(uuid4())


_TASK_KEY_SEPARATOR: Final = "::"
"""Separates a turn's run id from the agent whose sub-run a cancellation task belongs to.

Only :attr:`PkbRuntime._tasks` and :meth:`PkbRuntime.cancel` know this string; it is not an id a
caller ever sees, and it is *not* the thread derivation (that one is
:func:`pkb.agents.routing.expert_thread_id`, which happens to spell its join the same way for the
same reason — a thread id minted by Layer 3 cannot contain it by accident).
"""


def _forwarded_message(message: str | None, values: Mapping[str, Any]) -> str:
    """What the experts are actually sent: the human's item, verbatim (LB-15).

    Never a paraphrase and never the Librarian's summary of it — an expert that ingests a summary
    files a summary, and the whole point of routing to several experts is that each one reads the
    *source* through its own lens.

    On a resumed turn there is no message argument, so it is recovered from the thread. The last
    ``HumanMessage`` is the right one except in one case that must be excluded:
    :data:`~pkb.agents.routing.RETRY_INSTRUCTION` is delivered as a ``HumanMessage`` so the human can
    see why the turn cost two model calls (LB-13), and forwarding *that* to four experts would route
    the harness's own nagging instead of the item.
    """
    if message is not None:
        return message
    for entry in reversed(list(values.get("messages") or ())):
        text = getattr(entry, "text", "")
        if getattr(entry, "type", "") == "human" and text and text != ROUTE_RETRY:
            return str(text)
    return ""


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
