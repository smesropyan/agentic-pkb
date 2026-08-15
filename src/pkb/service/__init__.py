"""The service seam — what every transport is written against (SV-1 … SV-30).

:class:`PkbService` is a **Protocol** whose every parameter and return type is a ``pkb.contracts``
type, a dataclass of primitives defined here, or a builtin. Nothing in a signature names
``AgentGraph``, ``Interrupt``, ``Command`` or ``RunnableConfig``. That is not tidiness: it is what
makes the architecture's stub possible. A stub is writable exactly when the Protocol is expressible
without the harness, and a stub is what lets the whole server suite assert things a live system
could never assert deterministically — that a fan-out interleaves, that a busy thread 409s in
milliseconds while the first event is five seconds away.

**No interrupt-resume surface** (S-38, S-39; Task 6 of
``docs/superpowers/plans/2026-08-14-phase2-sessions.md``). There is no ``resume`` on this Protocol
and no ``pending_approval`` anywhere below it: no graph in :mod:`pkb.agents` composes ``interrupt_on``
any longer, so nothing a run does ever parks on a human decision, and there is nothing left to
answer. "The operator's instruction is the approval" — a write lands during the turn it was
instructed in, full stop.

**No thread-keyed run surface either** (Task 10 of the same plan). ``create_thread``, ``list_threads``,
``get_thread``, ``set_title``, ``delete_thread``, the thread-keyed ``start_run`` and the thread-keyed
``attach`` are gone: sessions are the one durable, named thing a run is addressed by now (DESIGN.md
§2), and their service-side backing (``pkb.service.threads.ThreadStore``) is deleted whole.
:class:`Thread` and :class:`ThreadDetail` themselves **stay** — not because anything in
:class:`PkbService` still returns one, but because ``tests/tui`` (Phase 5's, per
``CLAUDE.md``'s ruling and the plan's own "tests/tui... marks stay wholesale") imports and
constructs them directly, and a stub still returns them for exactly those fixtures. Deleting the
dataclasses would force touching every one of those call sites for a shape Phase 5 redraws anyway —
the same trade-off ``tests/server/stub.py``'s own compatibility shim already documents.

**The service adds no behaviour to a run** (SV-5). It does not retry, does not reorder events, does
not synthesize an :data:`~pkb.contracts.AgentEvent` the runtime did not emit, and does not swallow
one. Everything mechanical already exists below — event normalization, the write lock, the flush.
Layer 3 cites those rules; it never contains a second implementation of one.

The package layout, and why it is a package rather than a module (decision C):

* ``__init__`` — this file: the Protocol and the Layer-3 dataclasses, harness-free.
* ``sessions.py`` — the ``sessions`` table: one durable, named state machine per `S-1 … S-39`
  (``docs/superpowers/specs/2026-08-14-sessions-S-rules.md``).
* ``session_file.py`` — the one write surface for ``sessions/**`` (S-11), harness-free.
* ``runs.py`` — the run supervisor and the per-run hub: **the daemon owns runs, the request does
  not** (decision A).
* ``runtime.py`` — the one module permitted to import ``pkb.agents``; also where the SQLite
  connection discipline (``open_connection``, ``BUSY_TIMEOUT_MS``) and ``mint_run_id`` live now
  that ``sessions.py`` is Layer 3's only table (Task 10).

``threads.py`` — the ``threads`` table ``sessions.py`` superseded (``DESIGN.md`` §2) — is **deleted**
(Task 10), along with the thread-keyed run surface built on it. ``proposals.py`` — ``pkb_proposals``,
a durable home for a write an external agent proposed and could not approve — is **deleted** (Task
6). It served ``propose_only`` mode's auto-rejection, and there is nothing left to auto-reject once
no tool call ever interrupts.

Something has to call ``PkbRuntime.open``. Naming exactly one module keeps I2 structural instead of
exempting a whole package, so a later addition to this package cannot inherit the exemption silently
— the same trick ``pkb/contracts.py`` used at the Layer 2 seam.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from pkb.contracts import (
    AgentDescriptor,
    AgentEvent,
    ApprovalRequest,
    MessageView,
    OriginChannel,
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
    """One conversation, as every channel saw it before the sessions rebuild.

    Seven stored columns (ST-5) and two **computed** fields. ``kind`` and ``parent_thread_id`` are
    pure functions of ``thread_id`` (LB-14), so storing them would be a second answer to a question
    the id already answers — the class of duplication RT-36 rejects. They are fields rather than a
    client-side derivation because under RO-7's per-expert grouping a routed thread sits in the same
    list as the human's own conversations, and telling them apart must not require string-sniffing
    in a UI (ST-6).

    **Kept past Task 10** (`CLAUDE.md`, "the ruling") though :class:`PkbService` names it nowhere any
    more: ``tests/tui`` — Phase 5's, and the plan's own "tests/tui... marks stay wholesale" — imports
    and constructs this class directly, and ``tests/server/stub.py``'s compatibility shim returns one
    for exactly those fixtures. Nothing in production code (``pkb.service``, ``pkb.server``,
    ``pkb.tui``) constructs one any longer; ``pkb.service.threads.ThreadStore``, the one class that
    read and wrote the ``threads`` table this dataclass mirrored, is deleted whole.
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
    """Always ``None`` (S-38, S-39; Task 6).

    Before the sessions rebuild this was an index reconciled against the checkpointer at startup
    (AP-5) and repaired on read (RO-9) — never the authority, because the checkpoint decided whether
    an approval was pending. No graph in :mod:`pkb.agents` composes ``interrupt_on`` any longer, so
    nothing ever parks and nothing ever sets this column; it stays on the dataclass, always ``None``,
    for the same reason the rest of :class:`Thread` does (see that class's own docstring).
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
    """Everything needed to render one conversation (SV-14).

    One call has to be enough, because that is all a client re-attaching from a second channel has:
    a phone at lunch and a TUI that morning must see the same state (arch §8, D3).

    ``pending`` is always ``None`` (S-38, S-39; Task 6): there is no interrupt-resume surface left to
    read it from, live or otherwise. The field stays on the dataclass rather than being dropped
    outright because every consumer already reads it as "nothing to answer" when it is ``None``, and
    an always-``None`` field says that truthfully; it stays alongside the rest of ``Thread`` past
    Task 10 for the same reason (see that class's own docstring). ``children`` are the threads this
    turn routed to, carried for **provenance** — their primary home is their own expert's list (RO-7).
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

    # -- sessions --------------------------------------------------------------------
    # No thread-keyed surface any more (Task 10): `create_thread`, `list_threads`, `get_thread`,
    # `set_title` and `delete_thread` are gone along with `pkb.service.threads.ThreadStore`, their
    # one implementation. Sessions are the one durable, named thing a run is addressed by (DESIGN.md
    # §2); `Thread`/`ThreadDetail` themselves stay defined below only for `tests/tui`'s sake — see
    # their own docstrings.
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

    async def start_session_run(self, session_id: str, message: str) -> RunSubscription:
        """Begin a turn on a session (re-homed from ``start_run``'s thread-keyed machinery).

        Refused on any session that is not ``open`` — a closed session "takes no more turns"
        (S-20) and a sealed one never reopens (S-24/P3).

        Carries no ``approval_mode`` (Task 6). The parameter distinguished ``interactive`` from
        ``propose_only`` so MCP's writes could auto-reject a gate no robot could answer; no graph
        composes a gate any longer; the distinction has nothing left to select between, and MCP now
        calls this exactly as every other caller does.
        """
        ...

    async def attach_session(self, session_id: str) -> RunSubscription | None:
        """Subscribe to whatever is already running on this session, or ``None`` when idle."""
        ...

    # -- channels ----------------------------------------------------------------------
    # S-4, S-6, S-7, S-13 … S-17 (Task 7). ``channel_ref`` is an opaque string a transport mints —
    # ``pkb.server.telegram.channel_ref`` for Telegram, ``"tui:<client-id>"`` for the TUI — and this
    # Protocol never parses one. Reached in-process by a transport that talks to the service directly
    # (D9 — Telegram), never over HTTP: there is no ``/sessions/{id}/channels`` route, because nothing
    # outside the daemon process needs to attach one on the API's own behalf.

    async def attach_channel(self, session_id: str, channel_ref: str) -> None:
        """Attach a channel to a session (S-6, S-14). Idempotent; a channel already holding a
        different session is moved (S-7: "a channel holds one session at a time")."""
        ...

    async def detach_channel(self, session_id: str, channel_ref: str) -> None:
        """Detach a channel from a session (S-17). Never an error — an unattached ref, or one
        attached to a different session, is a no-op."""
        ...

    async def session_channels(self, session_id: str) -> list[str]:
        """Every channel currently attached to a session (S-6)."""
        ...

    # -- runs ----------------------------------------------------------------------
    # No thread-keyed `start_run`/`attach` either (Task 10): `start_session_run`/`attach_session`
    # above are the one way to begin or rejoin a turn now. The refusals both raise — `ThreadBusyError`
    # (still that name; `pkb.agents.runtime` raises it on a busy checkpoint regardless of what keys
    # it, thread or session), `UnknownAgentError` — are still raised **before** the caller commits a
    # response (AP-10); the alternative is a 200 that later has to carry a 409, and headers cannot
    # wait a whole model call.

    async def cancel(self, run_id: str) -> None:
        """Cancel a run and everything it fanned out to. An unknown id is a no-op (SV-19, RT-46)."""
        ...

    # -- maintenance -----------------------------------------------------------------
    # No proposals surface (Task 6): `pkb.service.proposals` and its `pkb_proposals` table are
    # deleted along with the gates they served. `propose_only` auto-rejected a gate a robot could not
    # answer and recorded the rejection for a human to review later; no gate ever fires now, so there
    # is nothing to auto-reject and nothing to review.

    async def run_scan(self, request: ScanRequest) -> ScanResult:
        """Run one conflict scan. The dequeue timer is Layer 3's; the graph run is Layer 2's (C12)."""
        ...

    async def thread_counts(self) -> tuple[int, int]:
        """``(total, pending_approvals)`` for ``/health`` (AP-19).

        ``pending_approvals`` is always ``0`` (Task 6): no graph composes ``interrupt_on`` any
        longer, so nothing is ever pending. ``total`` counts sessions since Task 10 — the ``threads``
        table this method once counted is deleted along with ``pkb.service.threads`` — kept as a pair
        rather than narrowed to ``total`` alone because ``/health``'s wire shape is unchanged.
        """
        ...

    async def regenerate(self) -> None:
        """Rewrite every derived file (RT-7) — the one sanctioned Layer 1 call, and it is Layer 2's."""
        ...
