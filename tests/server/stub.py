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
from pkb.service import RunSubscription, Thread, ThreadDetail
from pkb.service.runs import RunSupervisor

__all__ = ["AGENTS", "COOKING", "LIBRARIAN", "StubService", "opener_for"]

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
