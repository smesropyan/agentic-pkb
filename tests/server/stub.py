"""A stub :class:`~pkb.service.PkbService` — what makes the whole server suite free and fast (§6.1).

Because the Protocol's every type is expressible without the harness (SV-1), every route, every SSE
frame and every MCP tool tests against this: no runtime, no checkpointer, no model, no SQLite. That
is what lets the suite assert things a live system could never assert deterministically — that a
fan-out interleaves, that an expert's gate parks on the derived thread, that a busy thread 409s in
milliseconds while the first event is five seconds away.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from pkb.contracts import (
    AgentDescriptor,
    AgentEvent,
    ApprovalRequest,
    Decision,
    MessageView,
    PendingProposal,
    RunHandle,
    ThreadBusyError,
    UnknownAgentError,
    UnknownThreadError,
)
from pkb.core.paths import slugify
from pkb.service import RunSubscription, Thread, ThreadDetail
from pkb.service.runs import RunSupervisor
from pkb.service.session_file import LEARNING_AGENT_ID, SessionFileNoOwnFileError
from pkb.service.sessions import (
    IllegalSessionTransitionError,
    Session,
    SessionList,
    SessionNameTakenError,
    SessionState,
    UnknownSessionError,
)

__all__ = ["AGENTS", "COOKING", "LEARNING", "LIBRARIAN", "StubService", "opener_for"]

LEARNING = LEARNING_AGENT_ID
"""The Learning agent's placeholder id (S-9, S-19, S-26) — not in ``AGENTS``, same as production
until Phase 4 mints a real registry entry; the catalog check special-cases it by this literal."""

_DEFAULT_SESSION_STEM = "session"

LIBRARIAN = "librarian"
COOKING = "topic/cooking"
GRILLING = "topic/cooking/grilling"

AGENTS: tuple[AgentDescriptor, ...] = (
    AgentDescriptor(
        agent_id=LIBRARIAN,
        title="Librarian",
        description="Routes questions to the right experts.",
        has_custom_expert=False,
        model_id="ollama:deepseek-v4-flash:cloud",
    ),
    AgentDescriptor(
        agent_id=COOKING,
        title="Cooking",
        description="Food, heat and time.",
        has_custom_expert=True,
        model_id="ollama:deepseek-v4-flash:cloud",
    ),
    AgentDescriptor(
        agent_id=GRILLING,
        title="Grilling",
        description="Fire, specifically.",
        has_custom_expert=False,
        model_id="ollama:deepseek-v4-flash:cloud",
    ),
)

NOW = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)


class StubService:
    """Everything the Protocol promises, scripted.

    ``events`` is the script one run yields; ``admission_delay`` models a run whose first event is a
    whole model call away, which is what AP-10's race exists for.
    """

    def __init__(
        self,
        *,
        events: Sequence[AgentEvent] = (),
        admission_delay: float = 0.0,
        run_id: str = "run-1",
    ) -> None:
        self.rows: dict[str, Thread] = {}
        self.proposals: list[PendingProposal] = []
        self.sessions: dict[str, Session] = {}
        self._session_names: set[str] = set()
        self._session_clock = 0
        """A tick per state transition (create/close/end), so ``closed_at``/``ended_at`` order is
        actually distinguishable from insertion order in a test — real time never advances inside a
        stub call, and `NOW` alone would tie every close in one test at the same instant."""
        self.runs = RunSupervisor()
        self.events = list(events)
        self.admission_delay = admission_delay
        self.run_id = run_id
        self.busy = False
        self.pending: ApprovalRequest | None = None
        self.messages: list[MessageView] = [MessageView(role="human", text="hi", created_at=None)]
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.modes: list[str] = []
        self.cancelled: list[str] = []
        self.reconciled = 0

    # -- catalog ---------------------------------------------------------------------

    def list_agents(self) -> Sequence[AgentDescriptor]:
        self.calls.append(("list_agents", ()))
        return AGENTS

    # -- threads ---------------------------------------------------------------------

    async def create_thread(
        self, agent_id: str, *, title: str | None = None, origin_channel: str = "http"
    ) -> Thread:
        # `origin_channel` is recorded (TG-4): it was thrown away here, so nothing in the suite
        # could see which channel stamped a thread, and deleting the keyword from the Telegram
        # adapter broke no test at all — silently costing D3's cross-channel-resume story, where a
        # conversation started on a phone has to be recognisable in the TUI.
        self.calls.append(("create_thread", (agent_id, origin_channel)))
        if agent_id not in {a.agent_id for a in AGENTS}:
            raise UnknownAgentError(f"no agent answers to the id {agent_id!r}")
        thread = Thread(
            thread_id=str(uuid.uuid4()),
            agent_id=agent_id,
            created_at=NOW,
            updated_at=NOW,
            origin_channel=origin_channel,  # type: ignore[arg-type]
            title=title,
        )
        self.rows[thread.thread_id] = thread
        return thread

    async def list_threads(self, agent_id: str | None = None) -> Sequence[Thread]:
        self.calls.append(("list_threads", (agent_id,)))
        return [t for t in self.rows.values() if agent_id in (None, t.agent_id)]

    async def get_thread(self, thread_id: str) -> ThreadDetail:
        self.calls.append(("get_thread", (thread_id,)))
        thread = self.rows.get(thread_id)
        if thread is None:
            raise UnknownThreadError(f"no thread {thread_id!r}")
        return ThreadDetail(
            thread=thread, messages=tuple(self.messages), pending=self.pending, children=()
        )

    async def set_title(self, thread_id: str, title: str) -> Thread:
        self.calls.append(("set_title", (thread_id, title)))
        thread = self.rows[thread_id]
        self.rows[thread_id] = dataclasses.replace(thread, title=title)
        return self.rows[thread_id]

    async def delete_thread(self, thread_id: str) -> None:
        self.calls.append(("delete_thread", (thread_id,)))
        self.rows.pop(thread_id, None)

    # -- sessions (S-1 … S-39) ---------------------------------------------------------
    # A pure in-memory fake of `SessionStore`'s state machine — no SQLite, matching this
    # module's own rule (module docstring). It raises the identical typed errors the real
    # store and `RuntimeService` raise, which is what lets the route-level error-mapping
    # tests exercise the real `pkb.server.errors` table without a database.

    async def create_session(
        self,
        agent_id: str,
        *,
        objective: str | None = None,
        operator: str = "operator",
        name: str | None = None,
    ) -> Session:
        self.calls.append(("create_session", (agent_id, objective, operator, name)))
        if agent_id not in {a.agent_id for a in AGENTS} and agent_id != LEARNING:
            raise UnknownAgentError(f"no agent answers to the id {agent_id!r}")
        base = slugify(name) if name else (slugify(objective) if objective else "")
        unique = self._unique_session_name(base or _DEFAULT_SESSION_STEM)
        session = Session(
            session_id=str(uuid.uuid4()),
            agent_id=agent_id,
            objective=objective,
            name=unique,
            operator=operator,
            state="open",
            created_at=NOW,
            updated_at=NOW,
        )
        self.sessions[session.session_id] = session
        return session

    async def get_session(self, session_id: str) -> Session:
        self.calls.append(("get_session", (session_id,)))
        session = self.sessions.get(session_id) or self._session_from_row(session_id)
        if session is None:
            raise UnknownSessionError(f"no session {session_id!r}")
        return session

    async def list_sessions(
        self, agent_id: str | None = None, *, state: SessionState | None = None
    ) -> SessionList:
        self.calls.append(("list_sessions", (agent_id, state)))
        merged = dict(self.sessions)
        for thread_id in self.rows:
            merged.setdefault(thread_id, self._session_from_row(thread_id))  # type: ignore[arg-type]
        rows = [s for s in merged.values() if agent_id in (None, s.agent_id)]
        if state is not None:
            rows = [s for s in rows if s.state == state]
        if state == "closed":
            rows.sort(key=lambda s: s.closed_at or NOW)
        else:
            rows.sort(key=lambda s: s.created_at)
        return rows

    def _session_from_row(self, session_id: str) -> Session | None:
        """Compatibility shim for the many pre-existing (mostly TUI) fixtures that still build a
        scripted conversation by assigning straight into ``self.rows`` — a ``Thread``, never a
        ``Session`` — rather than calling ``create_session``. Rather than touch every one of those
        call sites for a shape Phase 5 redraws anyway, a ``Thread`` row doubles as an open session
        with the same id, agent and timestamps. Real session tests build through ``create_session``
        and never populate ``self.rows`` at all, so the two paths do not mix."""
        thread = self.rows.get(session_id)
        if thread is None:
            return None
        return Session(
            session_id=thread.thread_id,
            agent_id=thread.agent_id,
            objective=None,
            name=thread.title or thread.thread_id,
            operator="operator",
            state="open",
            created_at=thread.created_at,
            updated_at=thread.updated_at,
        )

    async def rename_session(self, session_id: str, name: str) -> Session:
        self.calls.append(("rename_session", (session_id, name)))
        session = await self.get_session(session_id)
        if session.agent_id == LEARNING:
            raise SessionFileNoOwnFileError(
                f"session {session_id!r} opened on the Learning agent has no file of its own; "
                f"there is nothing to rename"
            )
        if session.state == "ended":
            raise IllegalSessionTransitionError(
                f"session {session_id!r} is sealed (state='ended'); a sealed session is never "
                f"renamed"
            )
        slugged = slugify(name) or _DEFAULT_SESSION_STEM
        if slugged != session.name:
            if slugged in self._session_names:
                raise SessionNameTakenError(
                    f"a session named {slugged!r} already exists; refused rather than disambiguated"
                )
            self._session_names.discard(session.name)
            self._session_names.add(slugged)
        updated = dataclasses.replace(session, name=slugged, updated_at=NOW)
        self.sessions[session_id] = updated
        return updated

    async def close_session(self, session_id: str) -> Session:
        self.calls.append(("close_session", (session_id,)))
        session = await self.get_session(session_id)
        if session.state != "open":
            raise IllegalSessionTransitionError(
                f"session {session_id!r} is {session.state!r}; only an open session can be closed"
            )
        stamp = self._tick()
        updated = dataclasses.replace(session, state="closed", closed_at=stamp, updated_at=stamp)
        self.sessions[session_id] = updated
        return updated

    async def end_session(self, session_id: str) -> Session:
        self.calls.append(("end_session", (session_id,)))
        session = await self.get_session(session_id)
        if session.state != "closed":
            raise IllegalSessionTransitionError(
                f"session {session_id!r} is {session.state!r}; only a closed session can be ended"
            )
        stamp = self._tick()
        updated = dataclasses.replace(session, state="ended", ended_at=stamp, updated_at=stamp)
        self.sessions[session_id] = updated
        return updated

    def _tick(self) -> datetime:
        """The next distinguishable instant — see ``_session_clock``'s own docstring."""
        self._session_clock += 1
        return NOW.replace(microsecond=self._session_clock)

    def _unique_session_name(self, base: str) -> str:
        candidate = base
        attempt = 1
        while candidate in self._session_names:
            attempt += 1
            candidate = f"{base}-{attempt}"
        self._session_names.add(candidate)
        return candidate

    # -- runs ------------------------------------------------------------------------

    async def start_run(
        self,
        thread_id: str,
        message: str,
        *,
        approval_mode: str = "interactive",
        run_id: str | None = None,
    ) -> RunSubscription:
        self.calls.append(("start_run", (thread_id, message)))
        self.modes.append(approval_mode)
        if self.busy:
            raise ThreadBusyError(f"a run is already active on thread {thread_id!r}")

        async def starter() -> tuple[RunHandle, AsyncIterator[AgentEvent]]:
            if self.admission_delay:
                await asyncio.sleep(self.admission_delay)
            handle = RunHandle(run_id=self.run_id, agent_id=LIBRARIAN, thread_id=thread_id)

            async def stream() -> AsyncIterator[AgentEvent]:
                for event in self.events:
                    yield event

            return handle, stream()

        return await self.runs.start(thread_id, starter)

    async def resume(
        self, thread_id: str, decisions: Sequence[Decision], *, interrupt_id: str | None = None
    ) -> RunSubscription:
        self.calls.append(("resume", (thread_id, interrupt_id)))
        return await self.start_run(thread_id, "")

    async def attach(self, thread_id: str) -> RunSubscription | None:
        self.calls.append(("attach", (thread_id,)))
        return self.runs.attach(thread_id)

    # -- session runs (re-homed from thread-keyed start_run/attach, Task 5) ------------

    async def start_session_run(
        self, session_id: str, message: str, *, approval_mode: str = "interactive"
    ) -> RunSubscription:
        self.calls.append(("start_session_run", (session_id, message)))
        self.modes.append(approval_mode)
        session = await self.get_session(session_id)
        if session.state != "open":
            raise IllegalSessionTransitionError(
                f"session {session_id!r} is {session.state!r}; a run is refused on any session "
                f"that is not open"
            )
        if self.busy:
            raise ThreadBusyError(f"a run is already active on session {session_id!r}")

        async def starter() -> tuple[RunHandle, AsyncIterator[AgentEvent]]:
            if self.admission_delay:
                await asyncio.sleep(self.admission_delay)
            handle = RunHandle(run_id=self.run_id, agent_id=session.agent_id, thread_id=session_id)

            async def stream() -> AsyncIterator[AgentEvent]:
                for event in self.events:
                    yield event

            return handle, stream()

        return await self.runs.start(session_id, starter)

    async def attach_session(self, session_id: str) -> RunSubscription | None:
        self.calls.append(("attach_session", (session_id,)))
        return self.runs.attach(session_id)

    async def cancel(self, run_id: str) -> None:
        self.calls.append(("cancel", (run_id,)))
        self.cancelled.append(run_id)
        await self.runs.cancel(run_id)

    # -- proposals and maintenance ------------------------------------------------------

    async def list_proposals(self, *, status: str = "pending") -> Sequence[PendingProposal]:
        self.calls.append(("list_proposals", (status,)))
        return list(self.proposals)

    async def get_proposal(self, proposal_id: str) -> PendingProposal:
        for proposal in self.proposals:
            if proposal.proposal_id == proposal_id:
                return proposal
        raise UnknownThreadError(f"no proposal {proposal_id!r}")

    async def dismiss_proposal(self, proposal_id: str) -> None:
        self.proposals = [p for p in self.proposals if p.proposal_id != proposal_id]

    async def run_scan(self, request: Any) -> Any:
        self.calls.append(("run_scan", (request,)))
        return None

    async def regenerate(self) -> None:
        self.calls.append(("regenerate", ()))

    async def reconcile(self) -> int:
        self.reconciled += 1
        return 0

    async def thread_counts(self) -> tuple[int, int]:
        pending = sum(1 for t in self.rows.values() if t.pending_interrupt_id)
        return (len(self.rows), pending)

    async def proposal_count(self) -> int:
        return len(self.proposals)


def opener_for(service: StubService) -> Callable[[], Any]:
    """The ``open_service`` factory ``create_app`` takes, over a stub."""
    import contextlib

    @contextlib.asynccontextmanager
    async def opener() -> AsyncIterator[StubService]:
        yield service

    return opener
