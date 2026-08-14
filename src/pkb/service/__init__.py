"""The service seam — what every transport is written against (SV-1 … SV-30).

:class:`PkbService` is a **Protocol** whose every parameter and return type is a ``pkb.contracts``
type, a dataclass of primitives defined here, or a builtin. Nothing in a signature names
``AgentGraph``, ``Interrupt``, ``Command`` or ``RunnableConfig``. That is not tidiness: it is what
makes the architecture's stub possible. A stub is writable exactly when the Protocol is expressible
without the harness, and a stub is what lets the whole server suite assert things a live system
could never assert deterministically — that a fan-out interleaves, that an expert's gate parks on
the derived thread, that a busy thread 409s in milliseconds while the first event is five seconds
away.

**Runs are addressed by thread, never by agent** (SV-6). ``start_run(thread_id, …)`` and
``resume(thread_id, …)`` take no ``agent_id``; the service resolves it, from the id's own shape or
from the ``threads`` row. That is what makes cross-channel resume a one-field handoff: Telegram
needs only the id from ``list_threads`` to continue what the TUI started (D3).

**The service adds no behaviour to a run** (SV-5). It does not retry, does not reorder events, does
not synthesize an :data:`~pkb.contracts.AgentEvent` the runtime did not emit, and does not swallow
one. Everything mechanical already exists below — event normalization, the diff inside an approval,
which decisions an action allows, the gate table, the write lock, the flush. Layer 3 cites those
rules; it never contains a second implementation of one.

The package layout, and why it is a package rather than a module (decision C):

* ``__init__`` — this file: the Protocol and the Layer-3 dataclasses, harness-free.
* ``threads.py`` — the ``threads`` table on Layer 3's own ``aiosqlite`` connection. Superseded by
  ``sessions.py`` (``DESIGN.md`` §2); kept until Task 6/10 stop needing its methods and its table.
* ``sessions.py`` — the ``sessions`` table: one durable, named state machine per `S-1 … S-39`
  (``docs/superpowers/specs/2026-08-14-sessions-S-rules.md``).
* ``session_file.py`` — the one write surface for ``sessions/**`` (S-11), harness-free.
* ``proposals.py`` — ``pkb_proposals``, so a propose-only write survives a restart.
* ``runs.py`` — the run supervisor and the per-run hub: **the daemon owns runs, the request does
  not** (decision A).
* ``runtime.py`` — the one module permitted to import ``pkb.agents``.

Something has to call ``PkbRuntime.open``. Naming exactly one module keeps I2 structural instead of
exempting a whole package, so a later ``pkb/service/proposals.py`` cannot inherit the exemption
silently — the same trick ``pkb/contracts.py`` used at the Layer 2 seam.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from pkb.contracts import (
    AgentDescriptor,
    AgentEvent,
    ApprovalMode,
    ApprovalRequest,
    Decision,
    MessageView,
    OriginChannel,
    PendingProposal,
    RunHandle,
    ScanRequest,
    ScanResult,
    ThreadKind,
    librarian_thread_id,
)
from pkb.service.sessions import Session, SessionList, SessionState

__all__ = [
    "PkbService",
    "RunSubscription",
    "Session",
    "SessionList",
    "SessionState",
    "Thread",
    "ThreadDetail",
]


@dataclass(frozen=True, slots=True)
class Thread:
    """One conversation, as every channel sees it.

    Seven stored columns (ST-5) and two **computed** fields. ``kind`` and ``parent_thread_id`` are
    pure functions of ``thread_id`` (LB-14), so storing them would be a second answer to a question
    the id already answers — the class of duplication RT-36 rejects. They are fields rather than a
    client-side derivation because under RO-7's per-expert grouping a routed thread sits in the same
    list as the human's own conversations, and telling them apart must not require string-sniffing
    in a UI (ST-6).
    """

    thread_id: str
    agent_id: str
    created_at: datetime
    updated_at: datetime
    origin_channel: OriginChannel
    title: str | None = None
    """``None`` until the titling call lands (TT-1).

    Null and empty are **not** the same signal: a client cannot tell "not titled yet" from "titled
    empty" if the two are collapsed, and the first wants a placeholder while the second wants
    nothing (ST-5, SV-26).
    """

    pending_interrupt_id: str | None = None
    """An **index**, never the authority (decision E).

    The checkpoint decides whether an approval is pending; this column exists so the thread *list*
    can badge one without an ``aget_state`` and a lazy graph compile per row. It is reconciled
    against the checkpointer at startup (AP-5) and repaired on read (RO-9), and it never refuses a
    run — a stale column would block a legitimate turn with no way for the human to clear it (SV-16).
    """

    @property
    def parent_thread_id(self) -> str | None:
        """The Librarian thread that routed to this one, or ``None`` for a thread a human started."""
        return librarian_thread_id(self.thread_id)

    @property
    def kind(self) -> ThreadKind:
        return "routed" if self.parent_thread_id is not None else "user"


@dataclass(frozen=True, slots=True)
class ThreadDetail:
    """Everything needed to render one conversation and its pending approval (SV-14).

    One call has to be enough, because that is all a client re-attaching from a second channel has:
    somebody answering on a phone at lunch an approval the TUI raised that morning has no local
    state at all (arch §8, D3).

    ``pending`` is read **live** from the runtime, never from the row's column, and a disagreement
    repairs the column (RO-9). ``children`` are the threads this turn routed to, carried for
    **provenance** — their primary home is their own expert's list (RO-7).
    """

    thread: Thread
    messages: tuple[MessageView, ...] = ()
    pending: ApprovalRequest | None = None
    children: tuple[Thread, ...] = ()
    descriptor: AgentDescriptor | None = None


@dataclass
class RunSubscription:
    """A live view of one run: its handle, and the events it is producing.

    Returned rather than an ``AsyncIterator`` alone because the caller needs the ``run_id`` **before**
    the first frame — to write ``run.started`` (SS-8), and so that cancelling is never a race with
    the run that has not yet emitted anything.

    ``events`` may be consumed by several subscribers at once, each with its own bounded queue: the
    run is owned by the daemon, and a response merely subscribes to it (decision A). Closing a
    subscription **detaches**; it never cancels the run (AP-7). That is D2's promise — a turn
    outlives the terminal that started it — and an ingestion turn killed because a phone crossed a
    tunnel is that promise broken.
    """

    handle: RunHandle
    events: AsyncIterator[AgentEvent]
    close: object = field(default=None, repr=False)
    """An awaitable that unsubscribes. Typed loosely here so the Protocol stays harness-free and
    dependency-free; ``pkb.service.runs`` supplies the real one."""


@runtime_checkable
class PkbService(Protocol):
    """What a transport may ask of the system, and nothing more.

    Every method here is either SQL over Layer 3's own table or a forward to one Layer 2 call. The
    mapping is fixed (SV-5) so that a spy runtime can assert one call per method with the ids it was
    given, and an event-identity test can assert the list the service yields is the list the runtime
    yielded.
    """

    # -- catalog -------------------------------------------------------------------

    def list_agents(self) -> Sequence[AgentDescriptor]:
        """The catalog, verbatim from the registry: Librarian first, topics in snapshot order.

        Synchronous, compiles no graph and walks no tree (RG-3, RG-4) — creating fifty threads must
        build zero graphs (SV-8).
        """
        ...

    # -- threads -------------------------------------------------------------------

    async def create_thread(
        self,
        agent_id: str,
        *,
        title: str | None = None,
        origin_channel: OriginChannel = "http",
    ) -> Thread:
        """Mint a thread for an agent, validating the agent **before** inserting the row.

        Takes no id parameter: **Layer 3 mints every user thread id, and only Layer 3** (SV-10) — a
        bare ``uuid4``, never client-supplied, never derived from a title, chat id or MCP argument.
        Layer 2 explicitly refuses to invent one (RT-36), so if the transport does not mint it
        nobody does. A client-supplied ``title`` wins permanently (TT-4).
        """
        ...

    async def list_threads(self, agent_id: str | None = None) -> Sequence[Thread]:
        """Threads, grouped per expert, most-in-need-of-attention first (RO-6, RO-7).

        ``agent_id`` filters by **exact match**, never prefix: ``topic/cooking`` must not return
        ``topic/cooking/grilling``'s threads. A routed thread is listed under the expert that *ran*
        it, because "what have I been doing with Cooking" is the question a human actually asks.
        ``scan:`` threads never appear anywhere (RT-58) and are the only exclusion.

        Ordered ``pending_interrupt_id IS NOT NULL DESC, updated_at DESC``: a list sorted by
        creation date buries the very thread the human came back to answer (arch §8).
        """
        ...

    async def get_thread(self, thread_id: str) -> ThreadDetail:
        """One conversation, its history, and its **live** pending approval (SV-14)."""
        ...

    async def set_title(self, thread_id: str, title: str) -> Thread:
        """Rename a thread. A human-set title wins permanently and is never overwritten (SV-27)."""
        ...

    async def delete_thread(self, thread_id: str) -> None:
        """Erase a conversation and everything the Librarian routed out of it (SV-24, RT-48).

        The checkpoint cascade first, then the row cascade, in that order — otherwise the table
        keeps rows pointing at erased checkpoints and the list offers conversations that open empty.
        Deleting a *derived* thread reaches neither sideways to siblings nor upwards to the parent,
        matching the runtime's own asymmetry.
        """
        ...

    # -- sessions --------------------------------------------------------------------
    # DESIGN.md §2; `docs/superpowers/specs/2026-08-14-sessions-S-rules.md` (S-1 … S-39). The API
    # is the one way in (S-13): every session-affecting operation below is what a route calls, and
    # a route calls nothing else to reach a session.

    async def create_session(
        self,
        agent_id: str,
        *,
        objective: str | None = None,
        operator: str = "operator",
        name: str | None = None,
    ) -> Session:
        """Validate the agent, then create the store row and the file, in that order (S-9).

        Refuses an ``agent_id`` outside {the Librarian, a topic expert the registry knows, the
        Learning agent} before either is touched — mirrors ``create_thread``'s own ordering. A
        session opened on the Learning agent gets a store row and no file of its own (S-19, S-26).
        """
        ...

    async def get_session(self, session_id: str) -> Session:
        """One session's row. ``UnknownSessionError`` for an id nobody minted."""
        ...

    async def list_sessions(
        self, agent_id: str | None = None, *, state: SessionState | None = None
    ) -> SessionList:
        """Every session, optionally filtered. ``state='closed'`` **is** the learning queue (S-25/P4),
        ordered by ``closed_at`` rather than by creation order."""
        ...

    async def rename_session(self, session_id: str, name: str) -> Session:
        """``/name`` (S-16): store rename, then the file's own move and retitle.

        ``SessionNameTakenError`` on a collision, refused rather than disambiguated (S-16);
        ``IllegalSessionTransitionError`` once ``/end`` has sealed the file; a distinct "no file to
        rename" refusal for a Learning-agent session, which opens no file of its own (S-19).
        """
        ...

    async def close_session(self, session_id: str) -> Session:
        """``/close`` (S-17, S-20, S-21): state → ``closed``, every attached channel let go (Task 7
        wires the fan-out), the file's own marker appended."""
        ...

    async def end_session(self, session_id: str) -> Session:
        """``/end`` (S-22): legal only from ``closed``; seals the file (S-24/P3)."""
        ...

    async def start_session_run(
        self,
        session_id: str,
        message: str,
        *,
        approval_mode: ApprovalMode = "interactive",
    ) -> RunSubscription:
        """Begin a turn on a session (re-homed from ``start_run``'s thread-keyed machinery).

        Refused on any session that is not ``open`` — a closed session "takes no more turns"
        (S-20) and a sealed one never reopens (S-24/P3).

        ``approval_mode`` is not exposed over HTTP (RO-11 unchanged); it exists so MCP can request
        ``propose_only`` in-process, the same reason ``start_run`` carries it.
        """
        ...

    async def attach_session(self, session_id: str) -> RunSubscription | None:
        """Subscribe to whatever is already running on this session, or ``None`` when idle."""
        ...

    # -- runs ----------------------------------------------------------------------

    async def start_run(
        self,
        thread_id: str,
        message: str,
        *,
        approval_mode: ApprovalMode = "interactive",
        run_id: str | None = None,
    ) -> RunSubscription:
        """Begin a turn and return a live subscription to it.

        The refusals — ``ThreadBusyError``, ``ApprovalPendingError``, ``UnknownAgentError`` — are
        raised **here**, before the caller commits a response (AP-10). The alternative is a 200 that
        later has to carry a 409, and headers cannot wait a whole model call.

        ``approval_mode`` exists on the Protocol because otherwise the MCP adapter cannot get
        propose-only behaviour without importing the harness (SV-17) — and an MCP write that
        interrupts hangs forever on a decision no robot can make. It is **not** exposed over HTTP
        (RO-11): over a human channel, propose-only is a run that silently refuses its own approvals
        and files nothing, which is a broken agent rather than a mode.
        """
        ...

    async def resume(
        self,
        thread_id: str,
        decisions: Sequence[Decision],
        *,
        interrupt_id: str | None = None,
    ) -> RunSubscription:
        """Answer a pending approval and continue **the same run**.

        Validates with ``pkb.contracts.validate_decisions`` *itself*, before touching the runtime
        (SV-15). The service validating and the runtime validating again is deliberate: the shared
        validator lives in the seam precisely so that every caller answers "which decisions are
        allowed" identically.
        """
        ...

    async def attach(self, thread_id: str) -> RunSubscription | None:
        """Subscribe to whatever is already running on this thread, or ``None`` when idle (RO-17).

        No side effects and no second run: this is how a reconnecting client rejoins, replaying the
        hub from ``seq 0`` so it starts at the beginning of the run in flight rather than
        mid-sentence (AP-9).
        """
        ...

    async def cancel(self, run_id: str) -> None:
        """Cancel a run and everything it fanned out to. An unknown id is a no-op (SV-19, RT-46)."""
        ...

    # -- proposals and maintenance ---------------------------------------------------

    async def list_proposals(self, *, status: str = "pending") -> Sequence[PendingProposal]:
        """Writes an external agent proposed and cannot approve (RT-42, decision F)."""
        ...

    async def get_proposal(self, proposal_id: str) -> PendingProposal: ...

    async def dismiss_proposal(self, proposal_id: str) -> None:
        """Take one off the human's queue. v1 cannot *apply* one — that is a Layer 2 entry point."""
        ...

    async def run_scan(self, request: ScanRequest) -> ScanResult:
        """Run one conflict scan. The dequeue timer is Layer 3's; the graph run is Layer 2's (C12)."""
        ...

    async def thread_counts(self) -> tuple[int, int]:
        """``(total, pending_approvals)`` for ``/health`` — two indexed counts, no walk (AP-19)."""
        ...

    async def proposal_count(self) -> int:
        """How many proposals await the human. One indexed count (AP-19)."""
        ...

    async def reconcile(self) -> int:
        """Rewrite every row's pending-approval index from the checkpoint at startup (AP-5)."""
        ...

    async def regenerate(self) -> None:
        """Rewrite every derived file (RT-7) — the one sanctioned Layer 1 call, and it is Layer 2's."""
        ...
