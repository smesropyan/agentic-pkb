"""``RuntimeService`` — the composition root, and the **only** Layer 3 module that imports the
harness (SV-2, SV-3, decision C).

It reaches ``pkb.agents`` through the two names that package exports — ``PkbRuntime`` and
``RuntimeConfig`` — and names no harness module directly. Naming exactly one module keeps I2
structural rather than exempting a whole package: a later ``pkb/service/anything.py`` cannot inherit
the exemption silently, which is the same trick ``pkb/contracts.py`` used at the Layer 2 seam.

The class itself is **constructor-injected with a structural runtime**, never with a concrete
``PkbRuntime``. That is the property that lets the *real* service class run in a harness-banned
subprocess against a fake runtime (SV-4, SV-30) — the acceptance test Layer 2 already passes for the
seam, promoted to cover Layer 3. ``open_service`` is where the concrete runtime is actually built,
and it is an async context manager held for the daemon's lifetime because
``AsyncSqliteSaver`` closes its connection on context exit and pins itself to its creating loop
(RT-2): a module-level singleton cannot work.

**The service adds no behaviour to a run** (SV-5). Every method is either SQL over Layer 3's own
tables or one forward. It does not retry, reorder, synthesize or swallow. Where it *does* act — the
startup reconciliation, the titling call, registering a derived thread from a ``SubagentStart`` —
each is a rule with an id and a reason, and each is bookkeeping about a run rather than a change to
one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import aiosqlite

from pkb.contracts import (
    AgentDescriptor,
    AgentEvent,
    InterruptEvent,
    MessageView,
    OriginChannel,
    RunEnd,
    RunError,
    RunHandle,
    ScanRequest,
    ScanResult,
    SubagentStart,
    ThreadBusyError,
    UnknownAgentError,
    UnknownThreadError,
    expert_thread_id,
    is_scan_thread,
    librarian_thread_id,
)
from pkb.service import RunSubscription, Thread, ThreadDetail
from pkb.service.runs import RunSupervisor
from pkb.service.session_file import (
    LEARNING_AGENT_ID,
    SessionFileNoOwnFileError,
    SessionFileWriter,
)
from pkb.service.sessions import (
    IllegalSessionTransitionError,
    Session,
    SessionList,
    SessionState,
    SessionStore,
    UnknownSessionError,
)
from pkb.service.threads import ThreadStore, mint_run_id, mint_thread_id, open_connection

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pkb.core.models import FlushReport

__all__ = ["ADMISSION_DEADLINE", "RuntimeService", "open_service"]

ADMISSION_DEADLINE = 0.25
"""How long ``start_run`` waits to learn whether a run was refused (AP-10).

A **race**, not an await. The refusals — ``ThreadBusyError``, ``ApprovalPendingError``,
``UnknownAgentError`` — are raised on the first ``__anext__`` of the runtime's generator and cost
0.01 ms, while an *admitted* run's first event is a whole model call away: measured at 2.06 s
against a 2.0 s model. Awaiting unconditionally would hold the response headers open for the length
of that call — the thing AP-10 exists to prevent, reintroduced by the obvious implementation of it.

Two thousand times the measured refusal cost, and small enough that a client never notices it.
"""

_log = logging.getLogger(__name__)


class Runtime(Protocol):
    """The structural shape :class:`RuntimeService` depends on — never a concrete ``PkbRuntime``.

    Written out rather than imported so the dependency is *structural*: the real service class can
    then be imported and driven with ``deepagents``/``langgraph``/``langchain`` banned from
    ``sys.meta_path`` (SV-4, SV-30), which is the only proof of I2 that a linter cannot give.
    """

    db_path: Path

    def list_agents(self) -> Sequence[AgentDescriptor]: ...

    def run(
        self,
        agent_id: str,
        thread_id: str,
        message: str,
        *,
        run_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]: ...

    async def cancel(self, run_id: str) -> None: ...

    async def history(self, agent_id: str, thread_id: str) -> Sequence[MessageView]: ...

    async def delete_thread(self, thread_id: str) -> None: ...

    async def request_scan(self, request: ScanRequest) -> ScanResult: ...

    async def regenerate(self) -> FlushReport: ...


class RuntimeService:
    """:class:`~pkb.service.PkbService` over a real runtime and Layer 3's own SQLite tables."""

    def __init__(
        self,
        runtime: Runtime,
        connection: aiosqlite.Connection,
        *,
        kb_root: Path,
        supervisor: RunSupervisor | None = None,
    ) -> None:
        self._runtime = runtime
        self._connection = connection
        self._threads = ThreadStore(connection)
        self._sessions = SessionStore(connection)
        self._session_files = SessionFileWriter(kb_root)
        self._runs = supervisor or RunSupervisor()
        self._titling: set[str] = set()

    @property
    def runs(self) -> RunSupervisor:
        """The supervisor, for ``/health`` and for shutdown. Not part of the Protocol."""
        return self._runs

    @property
    def connection(self) -> aiosqlite.Connection:
        """Layer 3's own SQLite connection, for a transport that needs a table of its own.

        The Telegram adapter keeps durable per-chat state (ST-7's ``pkb_`` prefix), and the
        composition root is the only place that has both the service and the connection. Exposed
        rather than passed around because ``open_service`` owns the connection's lifetime and a
        second one would defeat AP-4's ordering assertion.
        """
        return self._connection

    async def setup(self) -> None:
        await self._threads.setup()
        await self._sessions.setup()

    # ----------------------------------------------------------------------------------
    # Catalog
    # ----------------------------------------------------------------------------------

    def list_agents(self) -> Sequence[AgentDescriptor]:
        """Verbatim from the registry — no field added, nothing reordered, no model chosen (RO-4)."""
        return self._runtime.list_agents()

    def _catalog_ids(self) -> frozenset[str]:
        return frozenset(descriptor.agent_id for descriptor in self._runtime.list_agents())

    # ----------------------------------------------------------------------------------
    # Threads
    # ----------------------------------------------------------------------------------

    async def create_thread(
        self,
        agent_id: str,
        *,
        title: str | None = None,
        origin_channel: OriginChannel = "http",
    ) -> Thread:
        """Validate the agent, then insert. In that order, and compiling nothing (SV-8)."""
        if agent_id not in self._catalog_ids():
            raise UnknownAgentError(f"no agent answers to the id {agent_id!r}")
        return await self._threads.create(
            mint_thread_id(), agent_id, title=title, origin_channel=origin_channel
        )

    async def list_threads(self, agent_id: str | None = None) -> Sequence[Thread]:
        return await self._threads.list_threads(agent_id)

    async def get_thread(self, thread_id: str) -> ThreadDetail:
        """The row and the history (SV-14). ``pending`` is always ``None`` (Task 6, S-38).

        Before the sessions rebuild ``pending`` was read live from the runtime and a disagreement
        with the row's own column was repaired on the spot (RO-9, decision E). No graph composes
        ``interrupt_on`` any longer, so nothing is ever pending, there is nothing to read live, and
        nothing to repair.
        """
        agent_id = await self._threads.resolve_agent(thread_id)
        row = await self._threads.get(thread_id)
        if row is None:
            # SV-12: a derived thread is openable from its id alone. Register it and carry on — the
            # row is an index for discovery, and the checkpoint is the authority on existence.
            await self._threads.register(
                thread_id, agent_id, origin_channel=await self._channel(thread_id)
            )
            row = await self._threads.get(thread_id)
        if row is None:  # pragma: no cover - the insert above cannot fail silently
            raise UnknownThreadError(f"no thread {thread_id!r}")

        messages = await self._runtime.history(agent_id, thread_id)
        descriptor = next((d for d in self._runtime.list_agents() if d.agent_id == agent_id), None)
        return ThreadDetail(
            thread=row,
            messages=tuple(messages),
            children=tuple(await self._threads.children(thread_id)),
            descriptor=descriptor,
        )

    async def set_title(self, thread_id: str, title: str) -> Thread:
        """A human's title, which wins permanently (SV-27, TT-4).

        Refused on a derived thread: the human never named that conversation and never will — it
        exists because the Librarian routed to it — and its generated name states its provenance
        (SV-28).
        """
        row = await self._threads.get(thread_id)
        if row is None:
            raise UnknownThreadError(f"no thread {thread_id!r}")
        if row.kind == "routed":
            raise UnknownThreadError(
                f"{thread_id!r} is a routed thread and its name states where it came from; "
                f"rename the conversation that produced it instead"
            )
        await self._threads.set_title(thread_id, title)
        return await self._threads.get(thread_id) or row

    async def delete_thread(self, thread_id: str) -> None:
        """Checkpoints first, then rows — mirroring Layer 2's own cascade (SV-24, RT-48)."""
        if is_scan_thread(thread_id):
            raise UnknownThreadError(f"{thread_id!r} is a maintenance thread")
        if self._runs.hub_for_thread(thread_id) is not None:
            # RO-16. Deleting erases checkpoints and every derived expert thread, there is no undo
            # (D6), and a run in flight may be mid-write. Making the human cancel first is one extra
            # call and it is the call that says they meant it.
            raise ThreadBusyError(
                f"a run is active on thread {thread_id!r}; cancel it before deleting the thread"
            )
        await self._runtime.delete_thread(thread_id)
        await self._threads.delete_cascade(thread_id)

    # ----------------------------------------------------------------------------------
    # Sessions (DESIGN.md §2; S-1 … S-39)
    # ----------------------------------------------------------------------------------

    def _session_catalog_ids(self) -> frozenset[str]:
        """The Librarian, every topic expert, and the Learning agent (S-9).

        The Learning agent has no registry entry yet — Phase 4 mints it — so it is added here by
        its own literal placeholder id (:data:`~pkb.service.session_file.LEARNING_AGENT_ID`, the
        same one :class:`~pkb.service.session_file.SessionFileWriter` already refuses to open a
        file for) rather than left for the catalog check to reject a legitimate target for want of
        a row nobody can add yet.
        """
        return self._catalog_ids() | {LEARNING_AGENT_ID}

    async def create_session(
        self,
        agent_id: str,
        *,
        objective: str | None = None,
        operator: str = "operator",
        name: str | None = None,
    ) -> Session:
        """S-9: validate the agent, then the store row, then the file — in that order.

        **Failure order and its observable state.** The row lands first because it is what makes
        the session *discoverable and named* (mirrors ``create_thread``'s own ordering, SV-8) and
        because the file's own path is a function of the row's disambiguated ``name`` — there is no
        name to create a file under until the store has minted one. If the file step then refuses
        (:class:`~pkb.service.session_file.SessionFileError`, most likely
        :class:`~pkb.service.session_file.SessionFileInvalidError` or an ``OSError`` wrapped by one
        of that module's typed errors), the row is **not** rolled back: :class:`SessionStore`
        exposes no delete, by design ("nothing moves or deletes operator content", `CLAUDE.md`), so
        there is nothing here that could remove it even if leaving a bare index row were the wrong
        answer. It is the right one in practice, though, because the store's own ``UNIQUE``
        constraint on ``name`` already forecloses the common cause of a collision — two sessions
        racing for the same slug — before the file step is ever reached; what can still fail there
        is an untracked file already sitting at the computed path, or a disk fault, and either way
        the operator sees the raised error immediately rather than a silently-vanished session. A
        row with no file is visible in ``GET /sessions`` and named clearly by the state of things:
        recoverable by hand, never lost.

        A session opened on the Learning agent (S-19, S-26) gets the row and **no** file — the file
        writer refuses one by design, so this method does not call it for that agent at all, rather
        than calling it and treating the refusal as an error it is not.
        """
        if agent_id not in self._session_catalog_ids():
            raise UnknownAgentError(f"no agent answers to the id {agent_id!r}")
        session = await self._sessions.create(agent_id, objective, operator, name=name)
        if agent_id != LEARNING_AGENT_ID:
            self._session_files.create(session)
        return session

    async def get_session(self, session_id: str) -> Session:
        session = await self._sessions.get(session_id)
        if session is None:
            raise UnknownSessionError(f"no session {session_id!r}")
        return session

    async def list_sessions(
        self, agent_id: str | None = None, *, state: SessionState | None = None
    ) -> SessionList:
        """``state='closed'`` **is** the learning queue (S-23, S-25/P4): ``closed_at`` order, not
        creation order. ``queue()`` itself takes no ``agent_id`` — P4 fixes it as one global view
        with no second structure — so a caller-supplied filter is applied here, in Python, over
        that same view, never by asking the store for a second, agent-scoped query.
        """
        if state == "closed":
            sessions = await self._sessions.queue()
            return [s for s in sessions if agent_id is None or s.agent_id == agent_id]
        return await self._sessions.list(agent_id, state=state)

    async def rename_session(self, session_id: str, name: str) -> Session:
        """``/name`` (S-16, S-19): store rename, then the file's own move and retitle.

        Store-then-file, the same order and the same documented trade-off as
        :meth:`create_session`: the store's ``UNIQUE`` constraint on ``name`` is what keeps a
        collision at the file step rare rather than routine, and there is no store method to undo a
        committed rename (no delete, no second name, consistent with "nothing moves or deletes
        operator content"). A failure at the file step therefore leaves the store's ``name`` already
        the new one while the bytes are still at the old path; the raised error is the operator's
        signal, and a further ``/name`` call — to the same target or another — is how it is resolved
        by hand, the same recovery :meth:`create_session` documents.

        Refused with a distinct "no file to rename" error (S-19) for a session opened on the
        Learning agent, which never had a file to begin with (S-26) — checked *before* the store is
        touched, so a Learning-agent session's row-level ``name`` cannot drift out of step with a
        file that was never there to rename.
        """
        before = await self.get_session(session_id)
        if before.agent_id == LEARNING_AGENT_ID:
            raise SessionFileNoOwnFileError(
                f"session {session_id!r} opened on the Learning agent has no file of its own "
                f"(S-19, S-26); there is nothing to rename"
            )
        old_path = before.file_path
        after = await self._sessions.rename(session_id, name)
        self._session_files.rename(after, old_path)
        return after

    async def close_session(self, session_id: str) -> Session:
        """``/close`` (S-17, S-20, S-21): store transition, then the file's own marker entry.

        Channel detachment (S-17) is Task 7's — no channel-attachment registry exists yet for this
        to fan out over. A Learning-agent session carries no file, so the marker write is skipped
        for it exactly as :meth:`create_session` skips file creation.
        """
        session = await self._sessions.close(session_id)
        if session.agent_id != LEARNING_AGENT_ID:
            self._session_files.mark_closed(session)
        return session

    async def end_session(self, session_id: str) -> Session:
        """``/end`` (S-22): legal only from ``closed``; seals the file (S-24/P3).

        The store's ``state`` is the single source of truth for sealed-ness (P3) — the file's own
        ``## Ended`` marker is a human-readable echo of it, not a second authority, which is why a
        Learning-agent session (no file) still ends cleanly with the marker step simply skipped.
        """
        session = await self._sessions.end(session_id)
        if session.agent_id != LEARNING_AGENT_ID:
            self._session_files.mark_ended(session)
        return session

    async def start_session_run(self, session_id: str, message: str) -> RunSubscription:
        """``POST /sessions/{id}/runs``: resolve, admit, and hand back a subscription.

        Re-homed from ``start_run``'s thread-keyed admission race (AP-10) rather than sharing it:
        a session carries no ``pending_interrupt_id`` column and forks into no derived row on a
        fan-out (S-12 — a session that crosses topics re-opens fresh, on the Librarian, rather than
        forking), so there is no table this needs to keep honest as the stream goes by the way
        ``_observe`` does for a thread. The events therefore relay **untouched** (SV-5) with nothing
        wrapped around them at all.

        Carries no ``approval_mode`` (Task 6). Before the gates died, MCP set ``propose_only``
        in-process (RT-42, SV-17) so its writes auto-rejected a gate no robot could answer; no graph
        composes a gate any longer (``pkb.agents.gates.build_interrupt_on`` is no longer called from
        ``build_expert``/``build_librarian``), so there is nothing left to route around and nothing
        left to pass. Removing the parameter is the honest default over keeping an inert one: RO-11's
        reasoning that it must never reach HTTP has nothing left to guard either, since the whole
        distinction it named is gone.
        """
        session = await self.get_session(session_id)
        if session.state != "open":
            raise IllegalSessionTransitionError(
                f"session {session_id!r} is {session.state!r}; a run is refused on any session "
                f"that is not open (S-20: a closed session 'takes no more turns'; S-24/P3: a "
                f"sealed one never reopens)"
            )
        minted = mint_run_id()
        stream = self._runtime.run(session.agent_id, session_id, message, run_id=minted)
        return await self._launch_session(session_id, session.agent_id, minted, stream)

    async def attach_session(self, session_id: str) -> RunSubscription | None:
        return self._runs.attach(session_id)

    async def _launch_session(
        self,
        session_id: str,
        agent_id: str,
        run_id: str,
        stream: AsyncIterator[AgentEvent],
    ) -> RunSubscription:
        """The admission race (AP-10) alone — mirrors ``_launch`` with no thread bookkeeping."""

        async def starter() -> tuple[RunHandle, AsyncIterator[AgentEvent]]:
            iterator = stream.__aiter__()
            first = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({first}, timeout=ADMISSION_DEADLINE)
            if done:
                try:
                    admitted: AgentEvent | None = first.result()
                except StopAsyncIteration:
                    admitted = None
                head = _prepend(admitted, iterator) if admitted is not None else _drain(iterator)
            else:
                head = _await_first(first, iterator)
            handle = RunHandle(run_id=run_id, agent_id=agent_id, thread_id=session_id)
            return handle, head

        return await self._runs.start(session_id, starter)

    # ----------------------------------------------------------------------------------
    # Runs
    # ----------------------------------------------------------------------------------

    async def start_run(
        self, thread_id: str, message: str, *, run_id: str | None = None
    ) -> RunSubscription:
        """Resolve the agent, admit the run, and hand back a subscription (SV-6, AP-10).

        Carries no ``approval_mode`` (Task 6): see :meth:`start_session_run`'s docstring.
        """
        agent_id = await self._threads.resolve_agent(thread_id)
        await self._threads.register(
            thread_id, agent_id, origin_channel=await self._channel(thread_id)
        )
        minted = run_id or mint_run_id()
        stream = self._runtime.run(agent_id, thread_id, message, run_id=minted)
        return await self._launch(thread_id, agent_id, minted, stream, title_after=True)

    async def attach(self, thread_id: str) -> RunSubscription | None:
        return self._runs.attach(thread_id)

    async def cancel(self, run_id: str) -> None:
        """Cancel the daemon's task **and** tell the runtime, which cancels the whole family.

        Both, because they mean different things: the task owns the relay and the hub, and the
        runtime owns the graphs — a Librarian turn drives several under one run id (SV-19, RT-46).
        """
        with contextlib.suppress(Exception):
            await self._runtime.cancel(run_id)
        await self._runs.cancel(run_id)

    async def _channel(self, thread_id: str) -> OriginChannel:
        """The channel a thread inherits (ST-13).

        A derived thread takes its parent's: the conversation started wherever the human started it,
        and the fan-out is an implementation detail of answering them. Defaulting to ``http``
        instead — which is what happens when nobody passes it — makes ``origin_channel`` ethnography
        rather than data, and it is the field a notification has to target.
        """
        parent = librarian_thread_id(thread_id)
        for candidate in (thread_id, parent):
            if candidate is None:
                continue
            row = await self._threads.get(candidate)
            if row is not None:
                return row.origin_channel
        return "http"

    async def _launch(
        self,
        thread_id: str,
        agent_id: str,
        run_id: str,
        stream: AsyncIterator[AgentEvent],
        *,
        title_after: bool = False,
    ) -> RunSubscription:
        """Admit the run, then wrap its stream in the bookkeeping the table needs.

        The wrapper is where ``updated_at``, ``pending_interrupt_id`` and derived-row registration
        happen (ST-9 … ST-12). Deliberately *around* the stream rather than after it: those writes
        record a run in progress, and a client disconnecting mid-run must not roll back the row that
        records the pending approval.
        """

        async def starter() -> tuple[RunHandle, AsyncIterator[AgentEvent]]:
            iterator = stream.__aiter__()
            first = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({first}, timeout=ADMISSION_DEADLINE)
            if done:
                # Either the run was refused — and the typed error propagates to the caller, which
                # is the whole point of doing this before a response commits — or it produced its
                # first event faster than the deadline and we already hold it.
                try:
                    admitted: AgentEvent | None = first.result()
                except StopAsyncIteration:
                    admitted = None
                head = _prepend(admitted, iterator) if admitted is not None else _drain(iterator)
            else:
                # Admitted, but the first event is a whole model call away. Awaiting it would hold
                # the response headers open for the length of that call — measured at 2.06 s against
                # a 2.0 s model, where the refusal path costs 0.01 ms. So the headers go now and the
                # pending future becomes the head of the stream.
                head = _await_first(first, iterator)
            # The run id is **minted before the run starts** and handed down, never read off the
            # first event. Reading it off the event meant a run that had not spoken yet had no id at
            # all: `run.started` carried an empty one (RO-11, SS-8), `cancel` was unaddressable, and
            # — worst — the supervisor keys its hubs and tasks on it, so every slow-starting run
            # shared one key and the second replaced the first's hub while the first's teardown
            # deleted the second's thread entry.
            handle = RunHandle(run_id=run_id, agent_id=agent_id, thread_id=thread_id)
            return handle, self._observe(head, thread_id, agent_id, title_after)

        return await self._runs.start(thread_id, starter)

    async def _observe(
        self,
        stream: AsyncIterator[AgentEvent],
        thread_id: str,
        agent_id: str,
        title_after: bool,
    ) -> AsyncIterator[AgentEvent]:
        """Relay every event untouched, and keep the table honest as it goes (SV-5, ST-10 … ST-12).

        *Untouched* is the load-bearing word: the events yielded here are the objects the runtime
        yielded, in order, with nothing added, dropped or reordered — an event-identity test asserts
        exactly that.
        """
        catalog = self._catalog_ids()
        channel = await self._channel(thread_id)
        saw_interrupt = False
        try:
            async for event in stream:
                if isinstance(event, SubagentStart) and event.agent_id in catalog:
                    # ST-12: register the derived row as the fan-out happens, not lazily. The
                    # catalog check is what keeps an expert's own `general-purpose` delegation —
                    # which runs under the *same* thread (RT-44) — from getting a row of its own.
                    derived = expert_thread_id(thread_id, event.agent_id)
                    if derived != thread_id:
                        await self._threads.register(
                            derived,
                            event.agent_id,
                            title=self._derived_title(event.agent_id),
                            origin_channel=channel,
                        )
                elif isinstance(event, InterruptEvent):
                    saw_interrupt = True
                    target = event.request.thread_id or thread_id
                    # ST-11: an interrupt naming a thread with no row creates one. A pending
                    # approval no channel can list is the one failure arch §8 promises cannot happen.
                    await self._threads.register(
                        target, event.request.agent_id or agent_id, origin_channel=channel
                    )
                    await self._threads.set_pending(target, event.request.interrupt_id)
                elif isinstance(event, RunEnd | RunError):
                    await self._threads.touch(thread_id)
                    if not saw_interrupt:
                        await self._threads.set_pending(thread_id, None)
                yield event
        finally:
            if title_after:
                # TT-2: off the critical path. The reply is delivered, the title arrives after, and
                # a titling failure is never the turn's failure.
                self._schedule_title(thread_id, agent_id)

    def _derived_title(self, agent_id: str) -> str:
        """``'<expert title> — via the Librarian'`` (SV-28).

        Generated rather than blank: the human never named this conversation, and a thread tree of
        untitled rows is unreadable. Not editable in v1 — its name states its provenance.
        """
        descriptor = next((d for d in self._runtime.list_agents() if d.agent_id == agent_id), None)
        return f"{descriptor.title if descriptor else agent_id} — via the Librarian"

    def _schedule_title(self, thread_id: str, agent_id: str) -> None:
        """Fire the one model call Layer 3 makes, once per thread, after the reply (TT-1 … TT-4).

        A task rather than an await: the run's ``finally`` must not wait on a model. Nothing else in
        Layer 3 calls a model — not the merge, not a pack, not agent selection — and Layer 3 builds
        no model client of its own; this goes through the runtime like every other model call, so
        ``pkb.server`` stays model-free (SV-25).
        """
        if thread_id in self._titling:
            return
        self._titling.add(thread_id)
        task = asyncio.create_task(self._title(thread_id, agent_id))
        task.add_done_callback(lambda _: self._titling.discard(thread_id))

    async def _title(self, thread_id: str, agent_id: str) -> None:
        row = await self._threads.get(thread_id)
        if row is None or row.title is not None or row.kind == "routed":
            return
        try:
            messages = await self._runtime.history(agent_id, thread_id)
            title = _title_from(messages)
            if title:
                await self._threads.title_once(thread_id, title)
        except Exception:  # never the turn's failure (TT-2)
            _log.warning("titling thread %s failed; it stays untitled", thread_id, exc_info=True)

    # ----------------------------------------------------------------------------------
    # Maintenance
    # ----------------------------------------------------------------------------------
    # No proposals surface and no startup reconciliation (Task 6). `list_proposals`,
    # `get_proposal`, `dismiss_proposal`, `proposal_count` and `reconcile` all existed to serve the
    # interrupt-resume surface: the first four read `pkb_proposals`, `pkb.service.proposals`'s durable
    # record of a write `propose_only` mode auto-rejected; `reconcile` repaired `pending_interrupt_id`
    # against the checkpointer at startup because that column could go stale across a restart. No
    # graph composes `interrupt_on` any longer, so nothing is ever pending and nothing is ever
    # proposed — there is nothing left for any of the five to do.

    async def thread_counts(self) -> tuple[int, int]:
        return await self._threads.counts()

    async def run_scan(self, request: ScanRequest) -> ScanResult:
        """One scan, forwarded. The dequeue timer is the daemon's; the graph run is Layer 2's."""
        return await self._runtime.request_scan(request)

    async def regenerate(self) -> None:
        await self._runtime.regenerate()


def _prepend(first: AgentEvent, rest: AsyncIterator[AgentEvent]) -> AsyncIterator[AgentEvent]:
    """Put the admission event back on the front of the stream.

    Admission consumes one event to surface the refusals before a response commits (AP-10). That
    event is a real event and belongs on the stream — dropping it would make the service's output
    differ from the runtime's, which is exactly what SV-5 forbids.
    """

    async def stream() -> AsyncIterator[AgentEvent]:
        yield first
        async for event in rest:
            yield event

    return stream()


def _await_first(
    pending: asyncio.Future[AgentEvent], rest: AsyncIterator[AgentEvent]
) -> AsyncIterator[AgentEvent]:
    """The stream for a run admitted but not yet speaking: await the in-flight first event, then go.

    The future is already running — cancelling and re-calling ``__anext__`` would start a second
    turn — so it is awaited here, on the run task, where waiting costs nothing.
    """

    async def stream() -> AsyncIterator[AgentEvent]:
        try:
            yield await pending
        except StopAsyncIteration:
            return
        async for event in rest:
            yield event

    return stream()


def _drain(rest: AsyncIterator[AgentEvent]) -> AsyncIterator[AgentEvent]:
    """A run that ended before it said anything. Nothing to prepend, and nothing to lose."""

    async def stream() -> AsyncIterator[AgentEvent]:
        async for event in rest:
            yield event

    return stream()


def _title_from(messages: Sequence[MessageView]) -> str:
    """A title from the exchange. **Deterministic in v1** — see the note below.

    TT-1 rules that titles are model-written, and SV-25 says that call goes through the runtime. The
    runtime does not expose a bare "one prompt, one answer" entry point today (its two entry points
    both run a graph on a thread, which would append to the very conversation being titled), so v1
    fills the column from the first human line and the mechanism — after the first reply, off the
    critical path, once per thread, never over a human-set title — is what this ships. Adding the
    model call is one Layer 2 method and changes nothing here but this function's body.
    """
    for message in messages:
        if message.role == "human" and message.text.strip():
            words = message.text.strip().split()
            title = " ".join(words[:8])
            return title if len(words) <= 8 else f"{title}…"
    return ""


@asynccontextmanager
async def open_service(
    kb_root: Path,
    db_path: Path,
    *,
    config: Any | None = None,
    runtime_factory: Callable[..., Any] | None = None,
) -> AsyncIterator[RuntimeService]:
    """Open the runtime, then Layer 3's connection, in that order (SV-3, AP-3, AP-4).

    **The order is the rule.** ``AsyncSqliteSaver.from_conn_string`` is a bare ``aiosqlite.connect``
    and ``PRAGMA journal_mode=WAL`` is set in ``setup()``, which ``PkbRuntime.open`` calls. A
    connection opened before that talks to a rollback-journal file, where a reader blocks a writer —
    measured as ``journal_mode == 'delete'`` before and ``'wal'`` after.
    """
    # The harness import is **inside** this function, and that is SV-4 rather than style: the real
    # `RuntimeService` class has to import and run in a subprocess with `deepagents`/`langgraph`/
    # `langchain` banned from `sys.meta_path` (SV-30), driven against a fake runtime. A module-level
    # import would make that impossible while changing nothing about what this module may name —
    # `PkbRuntime` and `RuntimeConfig`, the two names `pkb.agents` exports, and no harness module.
    if runtime_factory is None:
        from pkb.agents import PkbRuntime

        runtime_factory = PkbRuntime.open
    opener = runtime_factory
    async with opener(kb_root, db_path, config=config) as runtime:
        # Opened *after*, in autocommit, with the WAL assertion — see `open_connection`. AP-4 is one
        # line away from being a bug nobody finds until a reader blocks a writer under load.
        connection = await open_connection(runtime.db_path)
        try:
            service = RuntimeService(runtime, connection, kb_root=kb_root)
            await service.setup()
            yield service
        finally:
            await connection.close()
