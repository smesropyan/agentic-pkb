"""Run supervision — the daemon owns runs, the socket does not (AP-6 … AP-12, RO-17, RO-18).

Everything here exists to defend one promise: **a turn outlives the terminal that started it** (D2).
Drive ``runtime.run(...)`` from an ASGI response handler and that promise silently becomes
conditional on a stable connection — an ingestion turn dies because a phone crossed a tunnel, and
cancellation quietly becomes something the network does rather than something the human does. The
tests that matter here are therefore about *time and teardown*, not about payload shapes.

Two tools make that testable in-process:

* :func:`tests.server.driver.drive` — the raw ASGI driver. ``TestClient`` buffers the whole body and
  never delivers a disconnect, so it can neither see frames arrive over time nor run a response
  generator's ``finally``. Those two facts are the entire subject of this file.
* :class:`tests.server.stub.StubService`, subclassed here as :class:`PacedService` so a script
  arrives over a measurable stretch of time instead of all at once. A run that finishes inside one
  scheduler slot cannot be disconnected from, cancelled mid-flight, or attached to.

Where a rule is about the supervisor rather than the transport, the test drives
:class:`~pkb.service.runs.RunSupervisor` directly: the hub is harness-free and the fan-out rules
(AP-8, AP-9) are about queues, not about HTTP.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

import pytest
from fastapi import FastAPI

from pkb.contracts import AgentEvent, MessageDelta, RunEnd, RunError, RunHandle, ThreadBusyError
from pkb.server.app import create_app
from pkb.service import RunSubscription
from pkb.service.runs import SUBSCRIBER_QUEUE_SIZE, RunSupervisor
from tests.server.driver import Captured, drive
from tests.server.stub import LIBRARIAN, StubService, opener_for

THREAD = "t-1"
RUN_ID = "run-1"
RUNS_PATH = f"/threads/{THREAD}/runs"
EVENTS_PATH = f"/threads/{THREAD}/events"
JSON_HEADERS = [(b"host", b"127.0.0.1:8000"), (b"content-type", b"application/json")]
MESSAGE = b'{"message": "file this"}'


# --------------------------------------------------------------------------------------
# Fixtures local to this file — a paced script, an app under its lifespan, and small waits
# --------------------------------------------------------------------------------------


def script(count: int, *, run_id: str = RUN_ID) -> list[AgentEvent]:
    """``count`` deltas and a real terminal event.

    The terminal ``RunEnd`` matters: the supervisor's ``finally`` synthesizes a ``run.error`` for a
    stream that ends without one, so a script that just stops would make every "the run completed
    normally" assertion here indistinguishable from "the run died quietly".
    """
    deltas: list[AgentEvent] = [
        MessageDelta(run_id=run_id, agent_id=LIBRARIAN, text=f"chunk {i}") for i in range(count)
    ]
    return [*deltas, RunEnd(run_id=run_id, final_text="filed")]


class PacedService(StubService):
    """A :class:`StubService` whose script arrives over time, and that remembers how it ended.

    ``gap`` is the pause between events, which is what makes "mid-run" a real instant rather than a
    hopeful one. ``script_exhausted`` and ``stream_cancelled`` record the two endings that AP-6 and
    AP-11 have to tell apart: a run that reached its own terminal event, and a run that was killed.
    Neither is visible from the client side once the client has hung up — which is exactly the case
    under test.
    """

    def __init__(
        self,
        *,
        events: Sequence[AgentEvent] = (),
        gap: float = 0.02,
        admission_delay: float = 0.0,
        admission_error: BaseException | None = None,
        run_id: str = RUN_ID,
    ) -> None:
        super().__init__(events=events, admission_delay=admission_delay, run_id=run_id)
        self.gap = gap
        self.admission_error = admission_error
        self.script_exhausted = asyncio.Event()
        self.stream_cancelled = False

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
            if self.admission_error is not None:
                raise self.admission_error
            handle = RunHandle(run_id=self.run_id, agent_id=LIBRARIAN, thread_id=thread_id)

            async def stream() -> AsyncIterator[AgentEvent]:
                try:
                    for event in self.events:
                        await asyncio.sleep(self.gap)
                        yield event
                    self.script_exhausted.set()
                except asyncio.CancelledError:
                    self.stream_cancelled = True
                    raise

            return handle, stream()

        return await self.runs.start(thread_id, starter)


def starter_for(
    events: Sequence[AgentEvent],
    *,
    gap: float = 0.0,
    thread_id: str = THREAD,
    run_id: str = RUN_ID,
) -> Callable[[], Awaitable[tuple[RunHandle, AsyncIterator[AgentEvent]]]]:
    """A :data:`~pkb.service.runs.Starter` over a scripted stream, for the supervisor-level tests."""

    async def starter() -> tuple[RunHandle, AsyncIterator[AgentEvent]]:
        handle = RunHandle(run_id=run_id, agent_id=LIBRARIAN, thread_id=thread_id)

        async def stream() -> AsyncIterator[AgentEvent]:
            for event in events:
                await asyncio.sleep(gap)
                yield event

        return handle, stream()

    return starter


@contextlib.asynccontextmanager
async def serving(service: StubService) -> AsyncIterator[FastAPI]:
    """An app with its lifespan entered — the routes cannot resolve a service without it."""
    app = create_app(opener_for(service))
    async with app.router.lifespan_context(app):
        yield app


async def collect(events: AsyncIterator[AgentEvent]) -> list[AgentEvent]:
    return [event async for event in events]


async def drain(events: AsyncIterator[AgentEvent], *, quiet_for: float = 0.15) -> list[AgentEvent]:
    """Everything a subscriber can still be given, without assuming its stream ever ends.

    Used only where the stream's *ending* is the thing under suspicion; everywhere else
    :func:`collect` is the honest reader, because a stream that never closes is a bug.
    """
    seen: list[AgentEvent] = []
    try:
        while True:
            seen.append(await asyncio.wait_for(events.__anext__(), quiet_for))
    except (StopAsyncIteration, TimeoutError):
        return seen


async def until(condition: Callable[[], bool], *, timeout: float = 1.0, why: str = "") -> None:
    """Poll a cheap predicate. Every wait in this file is bounded and short by construction."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"the condition never held within {timeout}s: {why}")


async def observe(service: StubService, thread_id: str = THREAD) -> asyncio.Task[list[AgentEvent]]:
    """An independent subscriber on the run in flight, collecting until the hub closes.

    The only way to answer "did the run keep going after the client vanished?" from outside the run
    task, and the honest one: it reads the same hub the abandoned response was reading.
    """
    await until(lambda: service.runs.hub_for_thread(thread_id) is not None, why="the run started")
    subscription = service.runs.attach(thread_id)
    assert subscription is not None
    return asyncio.create_task(collect(subscription.events))


def watch(supervisor: RunSupervisor, thread_id: str = THREAD) -> asyncio.Task[list[AgentEvent]]:
    """The same independent subscriber, one layer down, where the run is known to be in flight."""
    subscription = supervisor.attach(thread_id)
    assert subscription is not None
    return asyncio.create_task(collect(subscription.events))


def data_of(captured: Captured, index: int) -> dict[str, object]:
    name, payload = captured.events()[index]
    body: dict[str, object] = json.loads(payload)
    body["type"] = name
    return body


# --------------------------------------------------------------------------------------
# AP-6 / AP-7 — a hangup detaches; it never cancels
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec_version", "send_raises"),
    [("2.3", False), ("2.4", False), ("2.4", True)],
    ids=["asgi-2.3", "asgi-2.4", "asgi-2.4-send-raises"],
)
@pytest.mark.asyncio
async def test_a_hangup_detaches_and_the_run_keeps_going_ap6_ap7(
    spec_version: str, send_raises: bool
) -> None:
    """The headline guarantee: a dropped socket is a reader leaving, not a human changing their mind.

    Layer 3 exists so a turn outlives the terminal that started it (D2). If the response handler
    drove the run, this test's disconnect would kill an ingestion turn mid-write — the note never
    lands, the topic ``index.md`` never lists it, and the human's only evidence is a phone that lost
    signal in a tunnel. So three things must hold at once after a hard hangup: the response
    generator's ``finally`` ran and merely *unsubscribed*, the run task is still alive, and it goes
    on to its own terminal event with its stream never cancelled.

    Run at both ASGI spec versions, and with ``send`` raising on a dead socket the way uvicorn's real
    2.4 contract does: the teardown path differs between them, and detaching must not be an accident
    of which one the server happens to speak.
    """
    service = PacedService(events=script(20), gap=0.02)
    async with serving(service) as app:
        driving = asyncio.create_task(
            drive(
                app,
                RUNS_PATH,
                method="POST",
                body=MESSAGE,
                headers=JSON_HEADERS,
                spec_version=spec_version,
                disconnect_after=2,
                send_raises_after_disconnect=send_raises,
            )
        )
        watcher = await observe(service)
        captured = await driving

        # The client is gone. The run is not.
        assert service.runs.active == 1, "a hangup cancelled the run task (AP-7)"
        await until(
            lambda: service.runs.subscribers == 1,
            why="the abandoned response unsubscribed, leaving only the observer (AP-6)",
        )

        seen = await asyncio.wait_for(watcher, 3)

    assert captured.status == 200
    assert service.script_exhausted.is_set(), "the run did not reach the end of its own script"
    assert service.stream_cancelled is False, "the hangup cancelled the run's stream (AP-7)"
    assert seen[-1] == RunEnd(run_id=RUN_ID, final_text="filed")
    assert seen == script(20), "the run's own events were lost once its first reader left"


@pytest.mark.asyncio
async def test_closing_a_subscription_leaves_the_run_task_alive_ap7() -> None:
    """``RunSubscription.close`` unsubscribes and nothing else — no cancel hidden behind it.

    This is the same rule as the HTTP test one layer down, and it is worth pinning separately
    because ``close`` is the seam every future transport will call: the TUI, the Telegram bot, the
    MCP adapter. The day one of them makes ``close`` mean "stop the run", cross-channel resume (D3)
    dies quietly — a second channel detaching would kill the turn the first one is watching.
    """
    supervisor = RunSupervisor()
    subscription = await supervisor.start(THREAD, starter_for(script(10), gap=0.01))
    watcher = watch(supervisor)

    close = subscription.close
    assert callable(close)
    close()

    assert supervisor.active == 1, "detaching cancelled the run"
    seen = await asyncio.wait_for(watcher, 2)
    assert seen[-1] == RunEnd(run_id=RUN_ID, final_text="filed")
    assert supervisor.active == 0


@pytest.mark.asyncio
async def test_frames_arrive_over_time_rather_than_in_one_chunk_ap6() -> None:
    """Incrementality is the property the whole streaming design is for, so assert it, not assume it.

    A buffering client — ``TestClient``, ``httpx.ASGITransport``, a reverse proxy without
    ``X-Accel-Buffering: no`` — turns a working stream into a long silence followed by everything at
    once, and every test that only reads the finished body passes identically either way. The raw
    driver timestamps each chunk, so "the human sees the turn as it happens" becomes checkable.
    """
    service = PacedService(events=script(6), gap=0.03)
    async with serving(service) as app:
        captured = await drive(
            app, RUNS_PATH, method="POST", body=MESSAGE, headers=JSON_HEADERS, timeout=3.0
        )

    assert captured.status == 200
    assert captured.headers["content-type"].startswith("text/event-stream")
    assert len(captured.chunks) >= 6, "the body arrived as one buffered blob"
    assert captured.spans == sorted(captured.spans)
    assert captured.spans[-1] - captured.spans[0] > 0.05, "every frame landed in the same instant"


# --------------------------------------------------------------------------------------
# AP-8 — a stalled subscriber is dropped, never waited for
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stalled_subscriber_is_dropped_and_the_run_finishes_ap8() -> None:
    """One browser that stopped reading must not be able to stall a knowledge-base write.

    Layer 2's own event queue is bounded at 64 *on purpose*: back-pressure there throttles the model,
    which is right when the queue feeds the run. It is exactly wrong once the queue feeds a client —
    a laptop that went to sleep mid-stream would hold the write lock and park the turn indefinitely.
    So each subscriber owns a bounded queue, and the one that overflows loses its place while
    everybody else, and the run itself, carries on untouched.
    """
    events = script(SUBSCRIBER_QUEUE_SIZE + 40)
    supervisor = RunSupervisor()
    live = await supervisor.start(THREAD, starter_for(events, gap=0))
    stalled = supervisor.attach(THREAD)
    assert stalled is not None
    reading = asyncio.create_task(collect(live.events))

    seen = await asyncio.wait_for(reading, 5)

    assert seen == events, "the attentive subscriber lost frames because another one stalled"
    assert supervisor.active == 0, "the run did not finish"

    abandoned = await drain(stalled.events)
    assert len(abandoned) < len(events), "the stalled subscriber was waited for, not dropped"
    assert len(abandoned) <= SUBSCRIBER_QUEUE_SIZE
    assert abandoned == events[: len(abandoned)], "the dropped stream lost its ordering too"


@pytest.mark.asyncio
async def test_a_dropped_subscribers_stream_actually_ends_ap8() -> None:
    """Being dropped has to be visible to the subscriber that is dropped.

    Dropping a reader from the hub's list stops it costing the *run* anything, which is half the
    rule. The other half is that the reader learns it was dropped: its stream ends, its response
    completes, and it is free to reattach from ``seq 0`` (AP-9, RO-17). Left hanging instead, it is
    in precisely the state AP-11 exists to prevent — a stream that ended silently — and the daemon
    holds a generator and a socket for a run that finished long ago.
    """
    events = script(SUBSCRIBER_QUEUE_SIZE + 40)
    supervisor = RunSupervisor()
    live = await supervisor.start(THREAD, starter_for(events, gap=0))
    stalled = supervisor.attach(THREAD)
    assert stalled is not None
    assert await asyncio.wait_for(collect(live.events), 5) == events

    await asyncio.wait_for(collect(stalled.events), 0.5)


# --------------------------------------------------------------------------------------
# AP-9 / RO-17 — reattaching starts at seq 0, not mid-sentence
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attaching_mid_run_replays_from_seq_zero_ap9() -> None:
    """A reconnecting client must rejoin a *conversation*, not a sentence already in progress.

    Without the replay buffer, a TUI that reconnected after a dropped Wi-Fi frame would show the
    tail of a run with no beginning — no ``tool.start`` for the write it is about to be asked to
    approve, no earlier assistant text — and the human would be deciding on a fragment. The buffer
    is what makes RO-17 a subscription rather than a redesign: the hub already holds the run's
    frames, so a late reader is just an early reader that started late.
    """
    events = script(8)
    supervisor = RunSupervisor()
    live = await supervisor.start(THREAD, starter_for(events, gap=0.02))

    early = [await anext(live.events) for _ in range(3)]
    assert early == events[:3]

    late = supervisor.attach(THREAD)
    assert late is not None
    replayed = await asyncio.wait_for(collect(late.events), 3)

    assert replayed == events, "the late subscriber joined mid-sentence"
    assert replayed[0] == events[0], "replay did not start at seq 0"


@pytest.mark.asyncio
async def test_attach_over_http_replays_the_run_in_flight_ro17() -> None:
    """``GET /threads/{id}/events`` is how a second channel rejoins without starting a second run.

    ``POST /runs`` would refuse with 409 (the thread is busy), so without this route a client that
    lost its connection has no way back to a turn it can still see running in ``list_threads`` — and
    D3's cross-channel resume, the case the design is proudest of, would only work between turns.
    The attach stream writes no ``run.started``: it did not start anything, and claiming otherwise
    would let a client believe it owns a run it merely joined.
    """
    service = PacedService(events=script(6), gap=0.03)
    async with serving(service) as app:
        await service.start_run(THREAD, "file this")
        captured = await drive(app, EVENTS_PATH, headers=JSON_HEADERS, timeout=3.0)

    assert captured.status == 200
    assert captured.headers["content-type"].startswith("text/event-stream")
    names = [name for name, _ in captured.events()]
    assert "run.started" not in names, "attaching claimed to have started the run"
    assert names[0] == "message.delta"
    assert names[-1] == "run.end"
    assert data_of(captured, 0)["seq"] == 0, "the attached stream did not number from 0 (SS-5)"
    assert data_of(captured, 0)["text"] == "chunk 0", "the client joined mid-sentence (AP-9)"


@pytest.mark.asyncio
async def test_attach_is_204_when_the_thread_is_idle_ro17() -> None:
    """Nothing running is not an error, and it must not be a stream either.

    A 200 with an empty ``text/event-stream`` would leave a reconnecting client hanging on a socket
    that will never produce a frame, indistinguishable from a slow model. 204 tells it, in one
    round trip and with no side effects, to fall back to ``GET /threads/{id}`` for history.
    """
    service = PacedService(events=script(4))
    async with serving(service) as app:
        captured = await drive(app, EVENTS_PATH, headers=JSON_HEADERS, timeout=2.0)

    assert captured.status == 204
    assert captured.body == b""
    assert "text/event-stream" not in captured.headers.get("content-type", "")
    assert ("attach", (THREAD,)) in service.calls
    assert service.runs.active == 0, "attaching started a run"


# --------------------------------------------------------------------------------------
# AP-10 — admission is synchronous, and it does not wait for the model
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_busy_thread_refuses_before_the_first_event_ap10() -> None:
    """Headers cannot wait a whole model call, and a refusal must not arrive inside a 200.

    The race this rule settles: the first event of a run is a model call away — five seconds is
    ordinary, and 284 on the local fallback — while ``thread_busy`` is knowable immediately. If
    admission were folded into the stream, a client that double-tapped send would get a committed
    ``text/event-stream`` and learn about the conflict as a frame, having already drawn a turn that
    never existed. The stub here delays its first event by five seconds; the refusal must not.
    """
    service = PacedService(events=script(4), admission_delay=5.0)
    service.busy = True
    async with serving(service) as app:
        started = time.monotonic()
        captured = await drive(
            app, RUNS_PATH, method="POST", body=MESSAGE, headers=JSON_HEADERS, timeout=2.0
        )
        elapsed = time.monotonic() - started

    assert captured.status == 409
    assert elapsed < 1.0, f"the refusal waited on the run's first event ({elapsed:.2f}s)"
    body = json.loads(captured.body)
    assert body["code"] == "thread_busy"
    assert "text/event-stream" not in captured.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_an_admission_failure_is_a_status_code_not_a_frame_ap10() -> None:
    """The refusals Layer 2 raises on the *first* ``__anext__`` still have to beat the response.

    ``ThreadBusyError``, ``ApprovalPendingError`` and ``UnknownAgentError`` are raised where the
    graph is first driven, not where the request is parsed — so the supervisor awaits exactly that
    much of the run on the caller's stack and lets it propagate. Get this wrong and every one of
    them becomes a 200 that later has to carry a 409, which no HTTP client can act on: the status is
    already on the wire.
    """
    service = PacedService(
        events=script(4),
        admission_delay=0.01,
        admission_error=ThreadBusyError("a run is already active on thread 't-1'"),
    )
    async with serving(service) as app:
        captured = await drive(
            app, RUNS_PATH, method="POST", body=MESSAGE, headers=JSON_HEADERS, timeout=2.0
        )

    assert captured.status == 409, "an admission failure committed a 200 stream"
    assert json.loads(captured.body)["code"] == "thread_busy"
    assert captured.headers["content-type"] == "application/problem+json"
    assert service.runs.active == 0, "a refused run left a task behind"
    assert service.runs.subscribers == 0, "a refused run left a subscription behind"


# --------------------------------------------------------------------------------------
# AP-11 — the one frame Layer 3 authors
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cancelled_run_still_gets_a_terminal_frame_ap11() -> None:
    """Layer 2 emits *nothing* for a cancelled run, so the supervisor must say the run is over.

    ``stream_events`` re-raises ``CancelledError`` without emitting ``run.error`` and its queue
    simply closes, which from a subscriber's side is indistinguishable from a model thinking hard.
    Without the synthesized frame every attached client hangs forever on a stream that ended
    silently — the TUI spinner never stops, the Telegram chat never gets a reply, and the human's
    own cancel is the thing that looks like a hang.
    """
    supervisor = RunSupervisor()
    live = await supervisor.start(THREAD, starter_for(script(40), gap=0.02))
    reading = asyncio.create_task(collect(live.events))
    await until(lambda: supervisor.active == 1, why="the run task is driving")
    await asyncio.sleep(0.05)

    await supervisor.cancel(RUN_ID)
    seen = await asyncio.wait_for(reading, 2)

    assert seen[-1] == RunError(run_id=RUN_ID, message="the run was cancelled", retryable=True)
    assert sum(1 for event in seen if isinstance(event, RunError | RunEnd)) == 1
    assert supervisor.active == 0


@pytest.mark.asyncio
async def test_cancel_mid_run_closes_the_stream_with_run_error_ap11() -> None:
    """The same guarantee where the client actually stands: the response ends, and says why.

    ``retryable: true`` is the load-bearing half — a cancelled run is the one terminal error where
    resending the same message is the right thing to do, and a client that cannot tell it from a
    validation failure will either refuse to retry or retry the ones it must not.
    """
    service = PacedService(events=script(40), gap=0.02)
    async with serving(service) as app:
        driving = asyncio.create_task(
            drive(app, RUNS_PATH, method="POST", body=MESSAGE, headers=JSON_HEADERS, timeout=3.0)
        )
        await until(lambda: service.runs.active == 1, why="the run task is driving")
        await asyncio.sleep(0.05)
        await service.cancel(RUN_ID)
        captured = await asyncio.wait_for(driving, 3)

    assert captured.error is None, "the stream never closed after the cancel"
    assert captured.status == 200, "the status was chosen before the first byte (SS-15)"
    names = [name for name, _ in captured.events()]
    assert names[0] == "run.started"
    assert names[-1] == "run.error", f"the stream ended on {names[-1]!r}"
    assert "run.end" not in names
    last = data_of(captured, -1)
    assert last["message"] == "the run was cancelled"
    assert last["retryable"] is True
    assert last["run_id"] == RUN_ID


@pytest.mark.asyncio
async def test_the_terminal_frame_names_the_cancellation_code_ap11() -> None:
    """A client branches on ``code``, never on prose (RO-21), and cancellation is no exception.

    Every other failure Layer 3 reports carries a stable machine code, precisely so a bot, a TUI and
    an MCP adapter can react differently without any of them string-matching a message that is free
    to be reworded. A terminal ``run.error`` with only a sentence in it forces exactly that match —
    and the sentence it would have to match, ``"the run was cancelled"``, is one the encoder itself
    already compares against to compute ``run.end.status``.
    """
    service = PacedService(events=script(40), gap=0.02)
    async with serving(service) as app:
        driving = asyncio.create_task(
            drive(app, RUNS_PATH, method="POST", body=MESSAGE, headers=JSON_HEADERS, timeout=3.0)
        )
        await until(lambda: service.runs.active == 1, why="the run task is driving")
        await asyncio.sleep(0.05)
        await service.cancel(RUN_ID)
        captured = await asyncio.wait_for(driving, 3)

    assert data_of(captured, -1)["code"] == "cancelled"


# --------------------------------------------------------------------------------------
# AP-12 — shutdown tells every subscriber before the sockets go
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_ends_every_stream_and_does_not_block_ap12() -> None:
    """A daemon restart must not leave a client watching a socket that quietly stopped.

    Two halves, and both are failure modes seen in the wild. A shutdown that cancels the response
    generator without a farewell leaves every attached TUI showing a half-written turn forever; a
    shutdown that waits for the run to finish blocks the whole process on a model call, which on the
    local fallback is 284 seconds and on a stuck endpoint is never. So the lifespan cancels the runs,
    every subscriber gets a terminal frame, and nothing flushed late is lost — the next boot's
    ``regenerate_all`` rewrites the derived files anyway (RT-7).
    """
    service = PacedService(events=script(60), gap=0.02)
    app = create_app(opener_for(service))
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    driving = asyncio.create_task(
        drive(app, RUNS_PATH, method="POST", body=MESSAGE, headers=JSON_HEADERS, timeout=3.0)
    )
    await until(lambda: service.runs.active == 1, why="the run task is driving")
    await asyncio.sleep(0.05)

    started = time.monotonic()
    await asyncio.wait_for(lifespan.__aexit__(None, None, None), 2)
    elapsed = time.monotonic() - started
    captured = await asyncio.wait_for(driving, 3)

    assert elapsed < 1.0, f"shutdown blocked on the run instead of cancelling it ({elapsed:.2f}s)"
    assert service.runs.active == 0, "a run survived the lifespan that owned it"
    names = [name for name, _ in captured.events()]
    assert names[-1] == "run.error", f"the stream ended on {names[-1]!r} at shutdown"
    assert data_of(captured, -1)["retryable"] is True


@pytest.mark.asyncio
async def test_the_shutdown_event_reaches_the_stream_ap12() -> None:
    """``shutdown_grace_period`` on its own delivers nothing; a generator has to notice.

    ``sse-starlette`` sets the event and then waits — it does not write a frame. The cooperative
    exit AP-12 asks for only exists if the response generator watches the event and yields its own
    goodbye, which is why ``_stream`` takes a ``shutdown`` parameter at all. Unwired, the whole
    mechanism collapses to "cancel the generator", which is the pre-3.3.0 behaviour the grace period
    was added to replace, and the farewell frame is dead code.
    """
    service = PacedService(events=script(60), gap=0.02)
    async with serving(service) as app:
        driving = asyncio.create_task(
            drive(app, RUNS_PATH, method="POST", body=MESSAGE, headers=JSON_HEADERS, timeout=0.6)
        )
        await until(lambda: service.runs.active == 1, why="the run task is driving")
        await asyncio.sleep(0.05)
        app.state.shutdown.set()
        captured = await asyncio.wait_for(driving, 2)

        assert captured.error is None, "the stream ignored the shutdown event and ran on"
        assert data_of(captured, -1)["code"] == "cancelled"


# --------------------------------------------------------------------------------------
# RO-18 — cancelling is a deliberate act, with its own route
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_a_run_cancels_it_and_returns_204_ro18() -> None:
    """Without this route ``PkbService.cancel`` has no caller and RT-46 is dead code over HTTP.

    It is the other half of AP-7: once a dropped connection stops cancelling anything, the human
    needs an explicit way to stop a turn — and it has to be un-scoped, because a Librarian fan-out
    drives several expert graphs under one run id and cancelling only the thread the client is
    watching would leave the experts writing.
    """
    service = PacedService(events=script(40), gap=0.02)
    async with serving(service) as app:
        driving = asyncio.create_task(
            drive(app, RUNS_PATH, method="POST", body=MESSAGE, headers=JSON_HEADERS, timeout=3.0)
        )
        await until(lambda: service.runs.active == 1, why="the run task is driving")

        captured = await drive(app, f"/runs/{RUN_ID}", method="DELETE", timeout=2.0)
        stream = await asyncio.wait_for(driving, 3)

    assert captured.status == 204
    assert captured.body == b""
    assert service.cancelled == [RUN_ID]
    assert service.runs.active == 0, "the run outlived its own cancellation"
    assert [name for name, _ in stream.events()][-1] == "run.error"


@pytest.mark.asyncio
async def test_cancelling_an_unknown_run_is_204_ro18() -> None:
    """Cancelling nothing is not an error — 404 here would make every honest client look broken.

    A run id goes stale the instant the run ends, and the client holding it cannot know when that
    was: it may have hung up (AP-7), the daemon may have restarted, or the turn may simply have
    finished between the human's decision and the request. Cancel is idempotent by nature, so the
    only answer that does not force clients to special-case a normal race is "it is not running".
    """
    service = PacedService(events=script(4))
    async with serving(service) as app:
        captured = await drive(app, "/runs/run-that-never-was", method="DELETE", timeout=2.0)

    assert captured.status == 204
    assert captured.body == b""
    assert service.cancelled == ["run-that-never-was"], "the route did not reach the service"
