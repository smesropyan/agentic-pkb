"""Run supervision — **the daemon owns runs; the HTTP request does not** (AP-6 … AP-13, decision A).

This is the largest structural decision in Layer 3, and it is forced by durability rather than by a
package quirk. D2's whole promise is that a turn outlives the terminal that started it, and D3's
cross-channel resume assumes the run belongs to the daemon rather than to a socket. Drive
``runtime.run(...)`` straight from an ASGI response handler and both become conditional on a stable
connection: an ingestion turn dies because a phone crossed a tunnel, and cancellation silently
becomes something the network does rather than something the human does.

So a run is a plain :class:`asyncio.Task` publishing into a per-run :class:`RunHub`. A response
*subscribes*. Closing a subscription **detaches**; it never cancels (AP-7). The run continues to its
own ending — completion, error, or an approval that parks durably in the checkpoint — and
``GET /threads/{id}/events`` is then a subscription rather than a redesign (RO-17).

Four properties that are each a rule:

* **Admission is synchronous** (AP-10). ``ThreadBusyError``, ``ApprovalPendingError`` and
  ``UnknownAgentError`` are raised on the first ``__anext__`` of the runtime's generator, so
  :func:`RunSupervisor.start` awaits exactly that much before returning. The alternative is a 200
  that later has to carry a 409, and headers cannot wait a whole model call.
* **A slow subscriber is dropped, not waited for** (AP-8). Layer 2's own queue is bounded at 64 so a
  slow consumer throttles the model stream — right for one consumer, wrong the moment one stalled
  browser can stall a run that is writing to the knowledge base.
* **A bounded replay buffer per run** (AP-9), so a client attaching mid-run starts at ``seq 0``
  rather than mid-sentence.
* **The supervisor synthesizes the one frame Layer 3 authors** (AP-11). A cancelled run emits *no*
  terminal event from Layer 2 — ``stream_events`` re-raises ``CancelledError`` without emitting
  ``run.error`` — so without this every attached client hangs on a stream that ended silently.

Harness-free: this module knows nothing about graphs. It takes an async generator of
:data:`~pkb.contracts.AgentEvent` and a :class:`~pkb.contracts.RunHandle`, which is what lets the
whole of it be tested against a stub with no runtime, no checkpointer and no model.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Final

from pkb.contracts import AgentEvent, RunEnd, RunError, RunHandle
from pkb.service import RunSubscription

__all__ = [
    "CANCELLED_CODE",
    "RunHub",
    "RunSupervisor",
]

_log = logging.getLogger(__name__)

CANCELLED_CODE: Final = "cancelled"
"""The ``code`` on the terminal frame the supervisor synthesizes for a cancelled run (AP-11)."""

SUBSCRIBER_QUEUE_SIZE: Final = 256
"""Per-subscriber buffer before that subscriber is dropped.

Generous — a run is minutes of work and a client should have to be genuinely stuck to lose its
place — but finite, because the alternative is one stalled reader holding a knowledge-base write
open. Deliberately larger than Layer 2's own 64: that bound exists to throttle the *model*, which is
right when the queue feeds the run and wrong when it feeds a browser.
"""

REPLAY_BUFFER_SIZE: Final = 512
"""How many frames a reattaching client can replay from (AP-9).

Bounded because a 300-page ingestion emits far more than any client needs to catch up on, and an
unbounded buffer would make the daemon's memory a function of the longest run it ever served.
"""


@dataclass
class _Subscriber:
    queue: asyncio.Queue[AgentEvent | None]
    dropped: bool = False


class RunHub:
    """One run's fan-out: many subscribers, each with its own bounded queue (AP-8, AP-9).

    Not an ``asyncio.Condition`` or a shared queue. Each subscriber owning a queue is what makes
    "drop the slow one" expressible at all: with a shared queue the only choices are block everyone
    or lose frames for everyone.
    """

    def __init__(self, handle: RunHandle) -> None:
        self.handle = handle
        self._subscribers: list[_Subscriber] = []
        self._replay: list[AgentEvent] = []
        self._closed = False
        self._terminal: AgentEvent | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: AgentEvent) -> None:
        """Hand one event to every subscriber, dropping any that has stopped reading.

        Never blocks and never awaits: it is called from the run task, and a publish that could wait
        would put a browser on the critical path of a knowledge-base write.
        """
        if len(self._replay) < REPLAY_BUFFER_SIZE:
            self._replay.append(event)
        if isinstance(event, RunEnd | RunError):
            self._terminal = event
        for subscriber in list(self._subscribers):
            if subscriber.dropped:
                continue
            if subscriber.queue.qsize() >= SUBSCRIBER_QUEUE_SIZE:
                # Its own fault and its own problem: the run continues, and this reader gets a
                # *closed* stream rather than the power to stall everyone else (AP-8). Closed
                # matters as much as dropped — a reader left awaiting a queue nothing will feed
                # again is a response that never completes and a socket held for a finished run.
                subscriber.dropped = True
                self._detach(subscriber)
                _log.warning(
                    "dropping a subscriber of run %s: it stopped reading and its queue is full",
                    self.handle.run_id,
                )
                continue
            subscriber.queue.put_nowait(event)

    def close(self) -> None:
        """End every subscription. Called once, after the terminal frame has been published."""
        self._closed = True
        for subscriber in list(self._subscribers):
            self._detach(subscriber)

    def subscribe(self) -> tuple[AsyncIterator[AgentEvent], Callable[[], None]]:
        """A new reader, primed with the replay buffer (AP-9).

        The buffer is copied into the queue **before** the subscriber is registered, so a frame
        published between the two cannot slip in ahead of the replay and arrive out of order.
        """
        # One slot of headroom beyond the logical capacity, reserved for the end-of-stream
        # sentinel. `publish` treats the *logical* size as full, so the sentinel always fits — see
        # `_detach`, where the alternative was evicting a frame and handing the dropped reader a
        # stream with a hole in it instead of a clean prefix.
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE + 1)
        subscriber = _Subscriber(queue=queue)
        for event in self._replay:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)
        if self._closed:
            queue.put_nowait(None)
        else:
            self._subscribers.append(subscriber)

        async def events() -> AsyncIterator[AgentEvent]:
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event

        def unsubscribe() -> None:
            """Detach. **Never cancels the run** — that is AP-7, and it is the whole design."""
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

        return events(), unsubscribe

    def _detach(self, subscriber: _Subscriber) -> None:
        """Remove a subscriber and **end its stream**.

        The sentinel used to be sent under ``suppress(QueueFull)`` — but the only way a subscriber
        gets dropped is that same queue being full, so on the drop path the sentinel was always
        discarded. The run was protected (half of AP-8) and the reader was left awaiting a queue
        nothing would ever feed again: over HTTP, a response that never completes and a socket held
        open for a run that finished long ago. Ending the stream is what "disconnected" has to mean.

        The reserved slot is why this cannot fail: what the dropped reader keeps is a clean prefix
        of the run followed by an ending, rather than a stream with a hole punched in it to make
        room for its own terminator.
        """
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)
        with contextlib.suppress(asyncio.QueueFull):
            subscriber.queue.put_nowait(None)


EventStream = AsyncIterator[AgentEvent]
Starter = Callable[[], Awaitable[tuple[RunHandle, EventStream]]]
"""How the supervisor begins a run: awaited once, returning the handle and the live stream.

A callable rather than the runtime itself, so this module stays harness-free and the whole of run
supervision is drivable by a stub (arch §9).
"""


class RunSupervisor:
    """Every run in flight, and the hubs feeding their subscribers.

    One instance per daemon. Cancellation, shutdown and ``/health``'s ``active_runs`` all read from
    here, which is why it is a real object rather than a module-level dict: the daemon's lifespan
    owns it and closing it is a step in shutdown.
    """

    def __init__(self) -> None:
        self._hubs: dict[str, RunHub] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._by_thread: dict[str, str] = {}

    @property
    def active(self) -> int:
        return len(self._tasks)

    @property
    def subscribers(self) -> int:
        return sum(hub.subscriber_count for hub in self._hubs.values())

    def hub_for_thread(self, thread_id: str) -> RunHub | None:
        """The hub of whatever is running on this thread, or ``None`` when it is idle (RO-17)."""
        run_id = self._by_thread.get(thread_id)
        return self._hubs.get(run_id) if run_id else None

    async def start(self, thread_id: str, starter: Starter) -> RunSubscription:
        """Admit a run, then drive it in a task that outlives every subscriber (AP-6, AP-10).

        ``starter`` is awaited **here**, on the caller's stack, precisely so its refusals reach the
        caller as exceptions rather than as a frame on a stream that has already committed a 200.
        Everything after that first await belongs to the daemon.

        The subscription is created **before** the task starts (AP-8), so the caller cannot miss an
        early frame — including one emitted before its first `await` yields control.
        """
        handle, stream = await starter()
        hub = RunHub(handle)
        self._hubs[handle.run_id] = hub
        self._by_thread[thread_id] = handle.run_id
        events, unsubscribe = hub.subscribe()

        task = asyncio.create_task(
            self._drive(hub, stream, thread_id), name=f"pkb-run-{handle.run_id}"
        )
        self._tasks[handle.run_id] = task
        return RunSubscription(handle=handle, events=events, close=unsubscribe)

    def attach(self, thread_id: str) -> RunSubscription | None:
        """Subscribe to a run already in flight, replaying it from ``seq 0`` (RO-17, AP-9)."""
        hub = self.hub_for_thread(thread_id)
        if hub is None or hub.closed:
            return None
        events, unsubscribe = hub.subscribe()
        return RunSubscription(handle=hub.handle, events=events, close=unsubscribe)

    async def cancel(self, run_id: str) -> None:
        """Cancel a run. An unknown id is a **no-op**, not an error (SV-19, RO-18).

        Un-scoped on purpose: a Librarian turn drives several graphs under one run id and the
        runtime cancels the whole family. Narrowing cancellation to one thread would leave expert
        runs alive after the human cancelled the question that started them.
        """
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def aclose(self) -> None:
        """Cancel every run in flight and let each hub deliver its terminal frame (AP-12)."""
        for run_id in list(self._tasks):
            await self.cancel(run_id)

    async def _drive(self, hub: RunHub, stream: EventStream, thread_id: str) -> None:
        """Relay a run's events into its hub, and guarantee exactly one terminal frame (SS-7).

        The ``finally`` closes the hub whatever happened, so a subscriber can never be left waiting
        on a stream that ended silently — which is the failure mode a cancelled run produces
        naturally, because Layer 2 re-raises ``CancelledError`` without emitting anything.
        """
        terminal_seen = False
        try:
            async for event in stream:
                terminal_seen = isinstance(event, RunEnd | RunError)
                hub.publish(event)
        except asyncio.CancelledError:
            # AP-11: the frame Layer 3 authors, and the only one. A transport frame, not a
            # fabricated AgentEvent — the run really did end, and this says how.
            hub.publish(
                RunError(
                    run_id=hub.handle.run_id,
                    message="the run was cancelled",
                    retryable=True,
                )
            )
            terminal_seen = True
            raise
        except Exception as exc:  # a starter that fails after admission still owes a terminal frame
            _log.exception("run %s failed", hub.handle.run_id)
            hub.publish(RunError(run_id=hub.handle.run_id, message=str(exc), retryable=False))
            terminal_seen = True
        finally:
            if not terminal_seen:
                hub.publish(
                    RunError(
                        run_id=hub.handle.run_id,
                        message="the run ended without a terminal event",
                        retryable=False,
                    )
                )
            hub.close()
            self._tasks.pop(hub.handle.run_id, None)
            if self._by_thread.get(thread_id) == hub.handle.run_id:
                del self._by_thread[thread_id]

    def forget(self, run_id: str) -> None:
        """Drop a finished run's hub — the grace period after the terminal frame (AP-9)."""
        self._hubs.pop(run_id, None)

    def finished(self) -> Sequence[str]:
        """Run ids whose hubs are closed, for the daemon's periodic sweep."""
        return [run_id for run_id, hub in self._hubs.items() if hub.closed]
