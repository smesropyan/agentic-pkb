"""The transport, over a real socket (DC-9 … DC-15, DC-17 … DC-21).

Almost nothing here is in-process. ``httpx2.ASGITransport`` and Starlette's ``TestClient`` both
buffer a stream into one body (P-4, P-14a), and every rule in this file is about *when* bytes arrive
or *whether* they arrive at all: a read budget that has to survive a whole model call, a 204 that
must be seen before a decoder is built, a 409 whose body is empty until it is asked for, a stream
that stops mid-run. So the daemon is the real ``create_app`` over :class:`~tests.server.stub.
StubService`, served by uvicorn on 127.0.0.1, and the client is the real :class:`~pkb.tui.client.
PkbClient`.

Two fixtures earn their complexity.

``serving`` quiesces **sse-starlette's process-global shutdown state** around every server.
``AppStatus.should_exit`` is a class attribute, and 3.4.8 adds a per-thread watcher task that
introspects ``signal.getsignal(SIGTERM)`` to find the uvicorn ``Server`` and polls its
``should_exit`` every 0.5 s. Measured here: server #1 stops, its watcher survives the stop, and 0.5 s
later it flips the global flag — so a *healthy* run on server #2 is truncated with
``run.error: the daemon is shutting down``, a failure that reads exactly like a daemon bug. Setting
the flag back to ``False`` is not enough on its own, because the stale watcher sets it again after
the reset; the watcher task is cancelled too, and automatic graceful drain is turned off so nothing
can re-arm it.

``ScriptedRuns`` gives a run a **shape in time**: a gap before the first event, and a gate the run
parks on until the test releases it. The stub's own iterator is instantaneous, and an instantaneous
run cannot express "the model has been thinking for eight seconds" (DC-9) or "attach while this is
still going" (DC-17). The gate is used instead of a sleep wherever the property is ordering rather
than duration, so those tests have no timing slack to lose.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import dataclasses
import json
import socket
import subprocess
import sys
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx2
import pytest
import uvicorn
from sse_starlette import sse as sse_starlette
from sse_starlette.sse import AppStatus

from pkb import clients as clients_package
from pkb import tui as tui_package
from pkb.contracts import (
    ERROR_CODES,
    RUN_STARTED_EVENT,
    AgentEvent,
    ApprovalPendingError,
    MessageComplete,
    MessageDelta,
    RunEnd,
    RunHandle,
    StaleInterruptError,
    ThreadBusyError,
    UnknownThreadError,
    expert_thread_id,
)
from pkb.server.app import ServerConfig, create_app
from pkb.server.routes import PING_SECONDS
from pkb.service import RunSubscription
from pkb.tui import client as client_module
from pkb.tui.client import (
    JSON_TIMEOUT,
    SSE_TIMEOUT,
    PkbClient,
    PkbHttpError,
    StreamEndedError,
)
from tests.server.stub import COOKING, GRILLING, LIBRARIAN, StubService, opener_for

RUN = "run-1"

CODE_LITERALS = ("thread_busy", "approval_pending", "stale_interrupt", "run_error")
"""The machine codes DC-15 moved into ``pkb.contracts`` so step 4 and step 5 cannot each copy them."""

LAYER4_SOURCES = sorted(
    [
        *Path(tui_package.__file__).parent.glob("*.py"),
        *Path(clients_package.__file__).parent.glob("*.py"),
    ]
)


# --------------------------------------------------------------------------------------
# Fixtures — a real daemon on a real socket, and a run with a shape in time
# --------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _quiet_sse_shutdown_global() -> Iterator[None]:
    """sse-starlette's shutdown flag is process-global; leaving it armed breaks the next test.

    ``AppStatus.should_exit`` is a class attribute and ``enable_automatic_graceful_drain`` is what
    lets a *stopped* server's watcher set it. Turning the drain off for the duration of this module
    means the only thing that can ever set the flag is this file, and nothing here does.
    """
    AppStatus.enable_automatic_graceful_drain = False
    AppStatus.should_exit = False
    try:
        yield
    finally:
        AppStatus.should_exit = False
        AppStatus.enable_automatic_graceful_drain = True


async def _quiesce_sse_shutdown() -> None:
    """Cancel the shutdown watcher and clear the flag it feeds — see the module docstring."""
    AppStatus.should_exit = False
    for task in asyncio.all_tasks():
        if getattr(task.get_coro(), "__qualname__", "") == "_shutdown_watcher":
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    state = sse_starlette._get_shutdown_state()
    state.events.clear()
    state.watcher_started = False
    AppStatus.should_exit = False


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.asynccontextmanager
async def serving(app: Any, *, lifespan: str = "on") -> AsyncIterator[str]:
    """One uvicorn in a task, on a free loopback port, yielding its base URL."""
    await _quiesce_sse_shutdown()
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan=lifespan)
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        await _quiesce_sse_shutdown()


@contextlib.asynccontextmanager
async def daemon(service: StubService) -> AsyncIterator[PkbClient]:
    """The real ``create_app`` over a stub, with an opened :class:`PkbClient` pointed at it."""
    app = create_app(opener_for(service), config=ServerConfig(kb_root="/kb"))
    async with serving(app) as base_url:
        client = PkbClient(base_url=base_url)
        async with client.opened():
            yield client


class ScriptedRuns(StubService):
    """A stub whose run has a shape in time: a gap before it speaks, and a gate it parks on.

    ``first_gap`` models the only silence a real turn always contains — ``run.started`` is written
    before the model is called, so the gap to the first token is a whole model call. ``hold_after``
    parks the run after *n* events until :attr:`release` is set, which is how a test observes a run
    *in flight* without racing a sleep against it.
    """

    def __init__(
        self,
        script: Sequence[AgentEvent] = (),
        *,
        first_gap: float = 0.0,
        hold_after: int | None = None,
        agent_id: str = LIBRARIAN,
        raises: BaseException | None = None,
    ) -> None:
        super().__init__()
        self.script = list(script)
        self.first_gap = first_gap
        self.hold_after = hold_after
        self.agent_id = agent_id
        self.raises = raises
        self.release = asyncio.Event()

    async def start_run(self, thread_id: str, message: str, **kwargs: Any) -> RunSubscription:
        self.calls.append(("start_run", (thread_id, message)))
        if self.raises is not None:
            raise self.raises

        async def starter() -> tuple[RunHandle, AsyncIterator[AgentEvent]]:
            handle = RunHandle(run_id=self.run_id, agent_id=self.agent_id, thread_id=thread_id)

            async def stream() -> AsyncIterator[AgentEvent]:
                if self.first_gap:
                    await asyncio.sleep(self.first_gap)
                for index, event in enumerate(self.script):
                    yield event
                    if self.hold_after == index + 1:
                        await self.release.wait()

            return handle, stream()

        return await self.runs.start(thread_id, starter)

    async def start_session_run(self, session_id: str, message: str, **kwargs: Any) -> Any:
        """The session-keyed mirror :class:`~pkb.tui.client.PkbClient` actually drives now (Task 5
        repoints the TUI at ``/sessions``): identical script/gap/hold/raise behavior, re-homed."""
        self.calls.append(("start_session_run", (session_id, message)))
        if self.raises is not None:
            raise self.raises

        async def starter() -> tuple[RunHandle, AsyncIterator[AgentEvent]]:
            handle = RunHandle(run_id=self.run_id, agent_id=self.agent_id, thread_id=session_id)

            async def stream() -> AsyncIterator[AgentEvent]:
                if self.first_gap:
                    await asyncio.sleep(self.first_gap)
                for index, event in enumerate(self.script):
                    yield event
                    if self.hold_after == index + 1:
                        await self.release.wait()

            return handle, stream()

        return await self.runs.start(session_id, starter)


Responder = Callable[[dict[str, Any], Any], Any]


class Recorder:
    """A raw ASGI app that records the exact request line it was sent and answers what it is told.

    The real daemon cannot be made to end a stream without a terminal frame — the supervisor
    synthesizes one whatever happens (AP-11) — so DC-14's two endings need a server that misbehaves
    on purpose. Recording the request bytes is what turns "the client did not retry" into an
    assertion rather than a hope.
    """

    def __init__(self, respond: Responder) -> None:
        self.respond = respond
        self.seen: list[tuple[str, str, bytes]] = []

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":  # pragma: no cover - uvicorn sends a lifespan scope only if on
            return
        body = b""
        more = True
        while more:
            message = await receive()
            body += message.get("body", b"")
            more = bool(message.get("more_body", False))
        raw_path = scope.get("raw_path") or scope["path"].encode()
        self.seen.append((scope["method"], raw_path.decode(), body))
        await self.respond(scope, send)


ONE_DELTA = (
    b"event: message.delta\r\n"
    b'data: {"type":"message.delta","seq":0,"run_id":"run-1","thread_id":"t-1",'
    b'"agent_id":"librarian","text":"filing"}\r\n\r\n'
)


async def _start_stream(send: Any, *, extra_headers: Sequence[tuple[bytes, bytes]] = ()) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/event-stream"), *extra_headers],
        }
    )


async def truncated_stream(scope: dict[str, Any], send: Any) -> None:
    """One frame, then a clean chunked close — the ending that raises **nothing at all**."""
    await _start_stream(send)
    await send({"type": "http.response.body", "body": ONE_DELTA, "more_body": True})
    await send({"type": "http.response.body", "body": b"", "more_body": False})


async def aborted_stream(scope: dict[str, Any], send: Any) -> None:
    """One frame, then a socket that dies owing bytes — the ending that raises."""
    await _start_stream(send, extra_headers=[(b"content-length", b"400")])
    await send({"type": "http.response.body", "body": ONE_DELTA, "more_body": True})
    raise RuntimeError("the daemon fell over mid-stream")


async def thread_created(scope: dict[str, Any], send: Any) -> None:
    """A 201 whose thread id the client had no part in choosing."""
    body = json.dumps(
        {
            "thread": {
                "thread_id": "server-minted-8f2c",
                "agent_id": LIBRARIAN,
                "title": None,
                "kind": "user",
                "parent_thread_id": None,
                "created_at": "2026-08-08T09:00:00Z",
                "updated_at": "2026-08-08T09:00:00Z",
                "origin_channel": "tui",
                "pending_interrupt_id": None,
            }
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 201,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def string_literals(path: Path) -> set[str]:
    """Every string a module's *code* contains, prose excluded.

    "Prose" is any bare string statement — a module, class or function docstring, and this
    codebase's attribute docstrings too. Excluded deliberately: a module that explains "three
    conditions share 409 — thread_busy, approval_pending, stale_interrupt" is documenting the seam's
    table, not copying it. A comparison against one of those strings is copying it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    prose = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node not in prose
    }


def calls_named(path: Path, name: str) -> list[ast.Call]:
    """Every ``<something>.<name>(...)`` call in a module."""
    return [
        node
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
    ]


async def collect(stream: AsyncIterator[Any]) -> list[Any]:
    return [frame async for frame in stream]


# --------------------------------------------------------------------------------------
# § two timeout budgets, not one (DC-9, DC-10)
# --------------------------------------------------------------------------------------


def test_the_streaming_read_budget_outlives_three_heartbeats_dc9() -> None:
    """httpx2's 5 s default kills every real turn, and the daemon files the note anyway.

    ``run.started`` is written before the model is called, so the first gap on any stream is a whole
    model call: ~16 s on the cloud default and 284 s on the local fallback (CLAUDE.md). At the
    library default the client raises ``ReadTimeout after 5.00s`` over a run that is about to
    succeed — the human sees a network error and the write lands regardless (AP-7). The budget is
    finite rather than ``None`` because the server's ``: ping`` comment frames reset httpx2's read
    timer, so anything above ``PING_SECONDS`` tolerates an arbitrarily slow model while still
    noticing a socket that has genuinely died.
    """
    assert SSE_TIMEOUT.read is None or SSE_TIMEOUT.read > PING_SECONDS
    assert SSE_TIMEOUT.read == 45.0 == 3 * PING_SECONDS
    assert httpx2.AsyncClient().timeout.read == 5.0 < PING_SECONDS


def test_the_json_budget_is_short_and_separate_dc10() -> None:
    """One constant for both classes forces a choice between two broken behaviours.

    Every non-streaming route is one indexed SQL read and never a model call (AP-19), so a minute of
    patience there is a UI that hangs on a daemon that is simply not listening. Share the streaming
    budget and that is what you get; share the short one and every stream dies mid-model-call. The
    structural half matters as much as the numbers: the streaming budget must be named at each
    ``stream(`` call site, because ``AsyncClient``'s own timeout is the JSON one.
    """
    assert JSON_TIMEOUT.read == 10.0
    assert SSE_TIMEOUT.read is None or SSE_TIMEOUT.read > (JSON_TIMEOUT.read or 0.0)

    source = Path(client_module.__file__)
    streams = calls_named(source, "stream")
    assert streams, "the transport no longer streams through `.stream(`; this test must follow it"
    for call in streams:
        timeout = next((kw for kw in call.keywords if kw.arg == "timeout"), None)
        assert timeout is not None, "a streaming call with no explicit timeout inherits 5 s"
        assert isinstance(timeout.value, ast.Name) and timeout.value.id == "SSE_TIMEOUT"
    for call in calls_named(source, "request"):
        assert not any(kw.arg == "timeout" for kw in call.keywords), (
            "a JSON call took the SSE budget"
        )


@pytest.mark.asyncio
async def test_a_first_event_a_model_call_away_still_arrives_dc9() -> None:
    """The budget is proved by the run that would have died without it, not by the constant.

    The gap here is 0.6 s standing in for the real 16 s to 284 s, and the contrast is a client whose read
    budget is smaller than the gap: it receives ``run.started`` and then raises, exactly as httpx2's
    5 s default does against a live model. The daemon is the same one in both halves, and it
    delivers the whole run in both — the difference is entirely in what the client was willing to
    wait for.
    """
    service = ScriptedRuns(
        [MessageDelta(RUN, LIBRARIAN, "filing"), RunEnd(RUN, "filed")], first_gap=0.6
    )
    app = create_app(opener_for(service), config=ServerConfig(kb_root="/kb"))
    async with serving(app) as base_url:
        client = PkbClient(base_url=base_url)
        async with client.opened():
            thread_id = (await client.create_thread(LIBRARIAN))["thread_id"]
            frames = await collect(client.run(thread_id, "where does this go?"))
            assert [frame.type for frame in frames] == [
                RUN_STARTED_EVENT,
                "message.delta",
                "run.end",
            ]

        impatient = httpx2.Timeout(10.0, read=0.25)
        async with httpx2.AsyncClient(base_url=base_url, timeout=impatient) as raw:
            received: list[str] = []
            with pytest.raises(httpx2.ReadTimeout):
                async with raw.stream(
                    "POST", f"/sessions/{thread_id}/runs", json={"message": "again"}
                ) as response:
                    async for sse in httpx2.EventSource(response):
                        received.append(str(sse.event))
            assert received == [RUN_STARTED_EVENT], "it died in the gap before the first token"


# --------------------------------------------------------------------------------------
# § the idle 204, seen before a decoder is built (DC-11)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opening_an_idle_thread_attaches_to_nothing_and_raises_nothing_dc11() -> None:
    """This is the *normal* path: most threads a human opens have no run in flight.

    ``GET /threads/{id}/events`` answers 204 when the thread is idle (RO-17), and 204 carries no
    content type at all. A client that treats the attach as "a stream, possibly empty" gets an empty
    iteration; one that hands the response to a decoder gets a protocol error on every ordinary
    thread open, which is most of them.
    """
    service = ScriptedRuns()
    async with daemon(service) as client:
        thread_id = (await client.create_thread(LIBRARIAN))["thread_id"]

        assert await collect(client.attach(thread_id)) == []
        assert ("attach_session", (thread_id,)) in service.calls


@pytest.mark.asyncio
async def test_the_naive_event_source_raises_an_sse_error_on_that_204_dc11() -> None:
    """The failure DC-11 exists to prevent, executed rather than described.

    ``EventSource`` checks the content type in ``__aiter__``, not in ``__init__`` — so the naive
    "attach, then iterate" form gets its ``SSEError`` on the *first frame it waits for*, complaining
    about a content type of ``''``. A protocol error, reported over a thread that is simply not
    running, at the moment the human opens it. Only a branch on ``status_code`` taken before the
    iteration starts avoids it.
    """
    service = ScriptedRuns()
    async with daemon(service) as client:
        thread_id = (await client.create_thread(LIBRARIAN))["thread_id"]

        async with client.http.stream("GET", f"/sessions/{thread_id}/events") as response:
            assert response.status_code == 204
            assert response.headers.get("content-type") is None
            with pytest.raises(httpx2.SSEError, match="text/event-stream"):
                async for _ in httpx2.EventSource(response):
                    pass  # pragma: no cover - the first iteration is where it dies


# --------------------------------------------------------------------------------------
# § a refusal that arrives before the stream (DC-12, DC-13, DC-15)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_409_before_the_stream_opens_carries_its_machine_code_dc12() -> None:
    """A refusal reaches the caller as a typed error, not as a stream that yielded nothing.

    ``POST /threads/{id}/runs`` refuses synchronously (AP-10) precisely so headers do not have to
    wait a whole model call, which means the 409 arrives on the same call that was going to be a
    stream. Losing it — or reporting it as "no frames" — turns "a run is already active" into a turn
    that silently did nothing.
    """
    service = ScriptedRuns([RunEnd(RUN, "filed")])
    async with daemon(service) as client:
        thread_id = (await client.create_thread(LIBRARIAN))["thread_id"]
        service.raises = ThreadBusyError(f"a run is already active on thread {thread_id!r}")

        with pytest.raises(PkbHttpError) as caught:
            await collect(client.run(thread_id, "where does this go?"))

        assert caught.value.status == 409
        assert caught.value.code == "thread_busy" == ERROR_CODES[ThreadBusyError]
        assert caught.value.detail == f"a run is already active on thread {thread_id!r}"
        assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_a_streaming_response_holds_no_body_until_it_is_asked_dc12() -> None:
    """Why ``await response.aread()`` is not a stylistic choice.

    On a streaming response ``.json()`` raises ``ResponseNotRead`` — so the naive form does not get
    a *wrong* code, it gets an exception where the diagnosis should have been, and whatever the
    client does next it will not be branching on ``thread_busy``. The daemon really did send the
    problem+json body; nobody read it.
    """
    service = ScriptedRuns(
        [RunEnd(RUN, "filed")], raises=ThreadBusyError("a run is already active")
    )
    async with daemon(service) as client:
        thread_id = "t-1"
        async with client.http.stream(
            "POST", f"/sessions/{thread_id}/runs", json={"message": "hi"}
        ) as response:
            assert response.status_code == 409
            assert response.headers["content-type"].startswith("application/problem+json")
            with pytest.raises(httpx2.ResponseNotRead):
                response.json()

            await response.aread()
            assert response.json()["code"] == "thread_busy"


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_three_conditions_share_409_and_only_the_code_tells_them_apart_dc12() -> None:
    """Wait, render the approval, refetch — three reactions behind one status code.

    A client that branched on ``409`` alone would pick one of the three and be wrong two thirds of
    the time; one that branched on ``detail`` would break the first time a message was reworded. The
    codes come from ``pkb.contracts``' own table, so the daemon, the MCP adapter and this client
    cannot disagree about what a refusal is called.

    Superseded (Phase 5 rebuilds this): mixed — two of the three codes are the interrupt/resume
    surface retired outright (``ApprovalPendingError``: a thread parked on an approval;
    ``StaleInterruptError``: an interrupt id that no longer matches what is pending), and sessions
    never park, so neither condition exists to distinguish. ``ThreadBusyError`` and the trailing
    404-on-unknown-thread check are ordinary run/lookup concerns that survive; marked whole because
    the loop and the ``set(seen) == {...}`` assertion cannot be split without touching the test body.
    A successor needs a session-shaped 409 table with at most one retired arm removed.
    """
    service = ScriptedRuns([RunEnd(RUN, "filed")])
    async with daemon(service) as client:
        thread_id = (await client.create_thread(LIBRARIAN))["thread_id"]
        seen: dict[str, tuple[int, str]] = {}

        for exc in (
            ThreadBusyError("a run is already active"),
            ApprovalPendingError("thread is parked on an approval"),
            StaleInterruptError("no approval is pending"),
        ):
            service.raises = exc
            with pytest.raises(PkbHttpError) as caught:
                await collect(client.run(thread_id, "hi"))
            seen[caught.value.code] = (caught.value.status, caught.value.detail)

        assert set(seen) == {"thread_busy", "approval_pending", "stale_interrupt"}
        assert {status for status, _ in seen.values()} == {409}
        assert seen["stale_interrupt"][1] == "no approval is pending"

        service.raises = None
        with pytest.raises(PkbHttpError) as missing:
            await client.thread("no-such-thread")
        assert (missing.value.status, missing.value.code) == (
            404,
            ERROR_CODES[UnknownThreadError],
        )


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_only_thread_busy_is_retryable_and_the_body_says_so_dc13() -> None:
    """Retryability is a fact the daemon states, never one the client infers from a status.

    All three 409s would look retryable to a client reasoning from the number. Retrying an
    ``approval_pending`` spins forever against a thread waiting on the very human doing the
    retrying, which is a UI that appears to hang for a reason the human is holding in their hand.

    Superseded (Phase 5 rebuilds this): the same mixed loop as the sibling DC-12 test — two of the
    three codes (``approval_pending``, ``stale_interrupt``) are the retired interrupt/resume surface
    and never fire once nothing parks. ``thread_busy``'s retryable-true fact survives; marked whole
    because the loop cannot be split without touching the test body.
    """
    service = ScriptedRuns([RunEnd(RUN, "filed")])
    async with daemon(service) as client:
        thread_id = (await client.create_thread(LIBRARIAN))["thread_id"]
        retryable: dict[str, bool] = {}

        for exc in (
            ThreadBusyError("a run is already active"),
            ApprovalPendingError("thread is parked on an approval"),
            StaleInterruptError("no approval is pending"),
        ):
            service.raises = exc
            with pytest.raises(PkbHttpError) as caught:
                await collect(client.run(thread_id, "hi"))
            retryable[caught.value.code] = caught.value.retryable

        assert retryable == {
            "thread_busy": True,
            "approval_pending": False,
            "stale_interrupt": False,
        }


def test_no_layer_4_module_spells_a_machine_code_itself_dc15() -> None:
    """Four things have to agree on these strings, and two of them may not import ``pkb.server``.

    That is why the table lives in ``pkb.contracts``: a copy in step 4 would be copied again in step
    5, and the drift shows up as a client that stops recognising a refusal after somebody renames a
    code — silently, because an unrecognised code falls through to whatever the default branch does.
    Docstrings are exempt; a comparison is not.
    """
    offenders = {
        path.name: sorted(string_literals(path) & set(CODE_LITERALS)) for path in LAYER4_SOURCES
    }
    assert LAYER4_SOURCES, "the source glob is wrong"
    assert not {name: found for name, found in offenders.items() if found}


def test_the_transport_never_branches_on_the_prose_dc12() -> None:
    """``detail`` and ``title`` are for a human; a branch on either is a branch on a translation.

    RO-21 makes ``detail`` the exception's own message verbatim, which means it is Layer 2's
    sentence and free to change. The transport carries it and reads nothing from it — every
    comparison in the module is against a number or against ``None``, and there is nowhere for a
    branch on a sentence to hide.
    """
    comparisons = [
        ast.unparse(node)
        for node in ast.walk(ast.parse(Path(client_module.__file__).read_text(encoding="utf-8")))
        if isinstance(node, ast.Compare)
        and any(
            isinstance(operand, ast.Constant) and isinstance(operand.value, str)
            for operand in node.comparators
        )
    ]
    assert comparisons == []

    error = PkbHttpError(409, {"code": "thread_busy", "detail": "busy", "retryable": True})
    assert (error.code, error.detail, error.retryable) == ("thread_busy", "busy", True)

    unmapped = PkbHttpError(500, {})
    assert (unmapped.code, unmapped.detail, unmapped.retryable) == ("internal", "", False)


# --------------------------------------------------------------------------------------
# § a stream that ends without an ending (DC-14, SS-7, C-16)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clean_close_without_a_terminal_frame_is_outcome_unknown_dc14() -> None:
    """A chunked stream that simply stops raises **nothing at all** — silence is not success.

    Without this the client's ``async for`` ends normally and every caller reads that as "the run
    finished". The run may have completed, may still be going, and in a shutdown very probably did
    neither; the only honest answer is to go and re-read the thread. The client must also not
    re-POST: filing the same material twice has no undo (D6).
    """
    recorder = Recorder(truncated_stream)
    async with serving(recorder, lifespan="off") as base_url:
        client = PkbClient(base_url=base_url)
        async with client.opened():
            with pytest.raises(StreamEndedError) as caught:
                await collect(client.run("t-1", "where does this go?"))

    assert caught.value.cause is None, "a clean EOF has no exception to blame"
    assert "outcome unknown" in str(caught.value)
    assert [(method, path) for method, path, _ in recorder.seen] == [("POST", "/sessions/t-1/runs")]


@pytest.mark.asyncio
async def test_an_aborted_socket_collapses_to_the_same_unknown_outcome_dc14() -> None:
    """The other ending looks completely different at the API and must mean the same thing.

    A socket that dies owing bytes raises ``httpx2.RemoteProtocolError``. If the two endings stayed
    distinct at the transport, the TUI and — in step 5 — Telegram would each invent their own
    reconciliation of "connection error" versus "stream ended", and the two would disagree about a
    run that wrote to the knowledge base.
    """
    recorder = Recorder(aborted_stream)
    async with serving(recorder, lifespan="off") as base_url:
        client = PkbClient(base_url=base_url)
        async with client.opened():
            with pytest.raises(StreamEndedError) as caught:
                await collect(client.attach("t-1"))

    assert isinstance(caught.value.cause, httpx2.RemoteProtocolError)
    assert [(method, path) for method, path, _ in recorder.seen] == [
        ("GET", "/sessions/t-1/events")
    ]


@pytest.mark.asyncio
async def test_the_frames_before_the_silence_are_still_delivered_dc14() -> None:
    """Unknown is about the *outcome*, not about the tokens — what arrived is still what arrived.

    A transport that swallowed the partial transcript on its way to raising would make the re-sync
    look like a run that produced nothing, and the human would watch their answer disappear.
    """
    recorder = Recorder(truncated_stream)
    async with serving(recorder, lifespan="off") as base_url:
        client = PkbClient(base_url=base_url)
        async with client.opened():
            received = []
            with pytest.raises(StreamEndedError):
                async for frame in client.run("t-1", "hi"):
                    received.append(frame)

    assert [frame.type for frame in received] == ["message.delta"]
    assert isinstance(received[0].event, MessageDelta)
    assert received[0].event.text == "filing"


@pytest.mark.asyncio
async def test_the_client_stops_reading_at_the_first_terminal_frame_dc14() -> None:
    """One ending per stream, and it is the first one — whatever the wire does afterwards.

    Layer 2 can legitimately emit a straggling delegate frame after a Librarian merge, and the
    supervisor publishes it (SS-7 promises one *terminal* frame, not that nothing follows it). A
    client that kept reading would render text after the run had ended and, worse, could see a
    second terminal frame turn a completed turn into a visible failure (C-16). The raw half of this
    test proves the daemon really did send the straggler: the client's silence is a decision.
    """
    script = [
        MessageDelta(RUN, LIBRARIAN, "fil"),
        RunEnd(RUN, "filed"),
        MessageComplete(RUN, LIBRARIAN, "late"),
    ]
    service = ScriptedRuns(script)
    app = create_app(opener_for(service), config=ServerConfig(kb_root="/kb"))
    async with serving(app) as base_url:
        client = PkbClient(base_url=base_url)
        async with client.opened():
            thread_id = (await client.create_thread(LIBRARIAN))["thread_id"]
            frames = await collect(client.run(thread_id, "hi"))
            assert [frame.type for frame in frames] == [
                RUN_STARTED_EVENT,
                "message.delta",
                "run.end",
            ]
            assert frames[-1].terminal and frames[-1].status == "completed"

            async with client.http.stream(
                "POST", f"/sessions/{thread_id}/runs", json={"message": "hi"}, timeout=SSE_TIMEOUT
            ) as response:
                on_the_wire = [str(sse.event) async for sse in httpx2.EventSource(response)]
    assert on_the_wire[-1] == "message.complete", "the daemon does send a frame after run.end"


# --------------------------------------------------------------------------------------
# § an attach is a live tail, not a transcript (DC-17, DC-18)
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_an_attach_stream_carries_no_run_started_dc17() -> None:
    """``routes.attach`` passes ``started=False``, so frame 0 of an attach is whatever is happening.

    Everything a client would have read off ``run.started`` — the run id it needs to cancel, the
    thread the frames belong to — has to come from the envelope of any frame instead. A client that
    waited for ``run.started`` before enabling cancel would leave the button dead for exactly the
    run a human wants to stop.

    Superseded (Phase 5 rebuilds this): the scenario is a Librarian thread whose fan-out produces a
    frame from ``COOKING``, and the assertion pins ``replayed.thread_id == expert_thread_id(...)`` —
    the derived-thread `<parent>::<agent>` addressing retired wholesale with the parent/derived
    split. The "no run.started on attach" principle likely survives; it needs a session-shaped
    scenario, not this fan-out one, to prove it again.
    """
    delta = MessageDelta(RUN, COOKING, "sear it")
    service = ScriptedRuns([delta, RunEnd(RUN, "filed")], hold_after=1)
    async with daemon(service) as client:
        thread_id = (await client.create_thread(LIBRARIAN))["thread_id"]

        live = client.run(thread_id, "how do I cook a steak?")
        started = await anext(live)
        assert started.type == RUN_STARTED_EVENT and started.handle is not None
        assert (await anext(live)).type == "message.delta"

        tail = client.attach(thread_id)
        replayed = await anext(tail)
        assert replayed.type != RUN_STARTED_EVENT
        assert replayed.run_id == started.handle.run_id
        assert replayed.thread_id == expert_thread_id(thread_id, COOKING)

        await tail.aclose()
        service.release.set()
        await collect(live)


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_runs_own_agent_comes_from_the_thread_not_the_attached_frame_dc17() -> None:
    """On a fan-out the first attached frame belongs to the *delegate*, not to the run.

    ``SseEncoder`` stamps each frame with the agent that emitted it and derives that agent's own
    thread (SS-10), so a client that titled the pane from the first frame it attached to would label
    a Librarian turn "Cooking" and file the human's next message against the wrong conversation.

    Superseded (Phase 5 rebuilds this): the whole scenario is a Librarian fan-out to a delegate on
    its own SS-10-derived thread — retired entirely, no parent/derived split in the session model, so
    there is no delegate frame on a different agent's derived thread to mislabel.
    """
    service = ScriptedRuns(
        [MessageDelta(RUN, COOKING, "sear it"), RunEnd(RUN, "filed")], hold_after=1
    )
    async with daemon(service) as client:
        thread_id = (await client.create_thread(LIBRARIAN))["thread_id"]
        live = client.run(thread_id, "how do I cook a steak?")
        await anext(live)
        await anext(live)

        tail = client.attach(thread_id)
        replayed = await anext(tail)
        detail = await client.thread(thread_id)

        assert replayed.agent_id == COOKING
        assert detail["thread"]["agent_id"] == LIBRARIAN != replayed.agent_id
        assert detail["thread"]["thread_id"] == thread_id

        await tail.aclose()
        service.release.set()
        await collect(live)


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_finished_run_has_no_tail_while_the_thread_keeps_its_history_dc18() -> None:
    """The attach stream is a live tail; the conversation is ``GET /threads/{id}``.

    The hub keeps a bounded suffix and the fresh per-response encoder renumbers ``seq`` contiguously
    over anything it dropped, so a hole in a replay is undetectable on the wire. Reconstructing a
    transcript from an attach is therefore a transcript that is quietly missing its middle; the
    authoritative history is fetched on every thread open and this is why.

    Superseded (Task 8 rebuilds this): the *principle* — an attach is a live tail, not the record —
    still holds, but ``GET /sessions/{id}`` has no read-back for a session's running record yet
    (Task 8 wires the write side only, appending each turn's exchange after it completes;
    ``pkb.tui.client``'s module docstring). ``PkbClient.thread`` returns an empty ``messages`` list
    truthfully rather than inventing history it cannot fetch, so this assertion cannot pass until a
    read route exists to satisfy it.
    """
    service = ScriptedRuns([MessageDelta(RUN, LIBRARIAN, "filing"), RunEnd(RUN, "filed")])
    async with daemon(service) as client:
        thread_id = (await client.create_thread(LIBRARIAN))["thread_id"]
        await collect(client.run(thread_id, "where does this go?"))

        assert await collect(client.attach(thread_id)) == []
        detail = await client.thread(thread_id)
        assert [message["text"] for message in detail["messages"]] == ["hi"]


# --------------------------------------------------------------------------------------
# § ids are opaque, and the client mints none (DC-19, DC-20)
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_every_route_the_tui_uses_keeps_its_ids_byte_for_byte_dc19() -> None:
    """Agent ids contain ``/``; derived thread ids contain ``::`` **and** ``/``.

    Nothing may split, slugify, percent-encode or trim one — Starlette decodes ``%2F`` straight back
    and proxies normalize it, so the only thing that works is interpolating the server's string. The
    trailing slash is the asymmetric trap: ``/runs``, ``/interrupt`` and ``/events`` ``rstrip("/")``
    the captured id while ``GET``/``PATCH``/``DELETE /threads/{id}`` do not, so one stray slash 404s
    on some verbs and quietly works on others. What the route handler received is the assertion,
    because that is the string that will be used to look up a checkpoint.

    Superseded (Phase 5 rebuilds this): the whole subject is byte-preservation of the derived-thread
    `<parent>::<agent>` id — `expert_thread_id` and the parent/derived split it mints for both retire
    with sessions. The underlying "an opaque id survives every route unmangled" principle likely
    survives for a plain session id; it needs its own test once `/sessions` routes land.
    """
    service = ScriptedRuns([RunEnd(RUN, "filed")])
    async with daemon(service) as client:
        catalog = [descriptor["agent_id"] for descriptor in await client.agents()]
        assert GRILLING in catalog and "/" in GRILLING

        parent = await client.create_thread(GRILLING)
        assert ("create_thread", (GRILLING, "tui")) in service.calls

        derived = expert_thread_id(parent["thread_id"], COOKING)
        assert "::" in derived and "/" in derived
        service.rows[derived] = dataclasses.replace(
            service.rows[parent["thread_id"]], thread_id=derived, agent_id=COOKING
        )

        detail = await client.thread(derived)
        assert detail["thread"]["thread_id"] == derived
        assert detail["thread"]["parent_thread_id"] == parent["thread_id"]

        await collect(client.run(derived, "hi"))
        await collect(client.attach(derived))
        await client.rename(derived, "Steak")
        await client.threads(GRILLING)
        await client.delete_thread(derived)

    received = dict(service.calls)
    for name in ("get_thread", "start_run", "attach", "set_title", "delete_thread"):
        assert received[name][0] == derived, f"{name} was handed a mangled id"
        assert not received[name][0].endswith("/")
    assert received["list_threads"] == (GRILLING,)


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_client_never_mints_a_thread_id_dc20() -> None:
    """``POST /agents/{id}/threads`` is the only way a thread comes into existence (SV-10).

    A client that generated an id would create a conversation the daemon has no row for: every
    subsequent call against it 404s, and the human's first message vanishes. The request carries the
    title and the origin channel and nothing else — the id in the reply is the server's.

    Superseded (Phase 5 rebuilds this): mixed — the "the client mints no id" principle (SV-10) very
    likely survives for session creation, but the body-shape assertion
    ``json.loads(body) == {"title": None, "origin_channel": "tui"}`` pins ``origin_channel`` as a
    field of the creation request, which is the channel-is-identity model sessions retire (channels
    attach afterward instead). Marked whole because the equality check cannot be narrowed without
    touching the test body; a successor needs a session-creation body shape with no ``origin_channel``.
    """
    recorder = Recorder(thread_created)
    async with serving(recorder, lifespan="off") as base_url:
        client = PkbClient(base_url=base_url)
        async with client.opened():
            thread = await client.create_thread(LIBRARIAN)

    method, path, body = recorder.seen[0]
    assert (method, path) == ("POST", f"/agents/{LIBRARIAN}/threads")
    assert json.loads(body) == {"title": None, "origin_channel": "tui"}
    assert thread["thread_id"] == "server-minted-8f2c"

    literals = string_literals(Path(client_module.__file__))
    assert not any("uuid" in literal for literal in literals)
    assert "uuid" not in Path(client_module.__file__).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_cancelling_an_unknown_run_is_a_silent_204_ro18() -> None:
    """Cancelling nothing is not an error — which is why the *wrong* target fails silently.

    ``DELETE /runs/{id}`` answers 204 whether or not the id names anything, so a client that cancels
    the run id it saw before a resume gets a cheerful no-op and a run that keeps going. That is
    decision M's whole point: the id must come from the most recent ``run.started``. The transport
    cannot detect the mistake, so it must at least not invent a failure that is not there.
    """
    service = ScriptedRuns([RunEnd(RUN, "filed")])
    async with daemon(service) as client:
        assert await client.cancel("run-that-never-existed") is None
        assert service.cancelled == ["run-that-never-existed"]


# --------------------------------------------------------------------------------------
# § every route in §5.2, against the real daemon
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_every_route_the_transport_declares_answers_on_the_real_daemon_dc19() -> None:
    """Thirteen call sites, no more — and each one reaches a route that exists.

    There is no shared table binding the client's URLs to the router's (unlike the nine event
    names), so a renamed path or a moved verb reaches the client as a 404 in whichever screen
    happens to open first. Exercising all of them against the real ``create_app`` is the only thing
    that keeps the two halves honest.

    Superseded (Phase 5 rebuilds this): mixed — four of the thirteen call sites are retired outright:
    ``client.proposals()`` (``/proposals``, deleted with ``proposals.py``), the ``origin_channel``
    assertion on thread creation (channel-is-identity, retired), ``client.resolve(...)`` / the
    recorded ``"resume"`` call (``/threads/{id}/interrupt``, retired with the gates), and
    ``client.delete_thread(...)`` — "nothing deletes a session" (arch), no ``DELETE /sessions/{id}``
    at all. The remaining call sites — agents, health, create/list/rename/get a thread, run, cancel,
    attach — are ordinary CRUD/streaming concerns that survive under ``/sessions``. Marked whole
    because one straight-line script exercises all thirteen; a successor needs the same shape rebuilt
    against the session routes with the four retired calls dropped.
    """
    service = ScriptedRuns([MessageDelta(RUN, LIBRARIAN, "filing"), RunEnd(RUN, "filed")])
    async with daemon(service) as client:
        assert [row["agent_id"] for row in await client.agents()] == [LIBRARIAN, COOKING, GRILLING]
        assert (await client.health())["status"] == "ok"
        assert await client.proposals() == []

        thread = await client.create_thread(LIBRARIAN)
        thread_id = thread["thread_id"]
        assert thread["origin_channel"] == "tui" and thread["title"] is None

        assert [row["thread_id"] for row in await client.threads()] == [thread_id]
        assert [row["thread_id"] for row in await client.threads(LIBRARIAN)] == [thread_id]
        assert (await client.rename(thread_id, "Steak"))["title"] == "Steak"
        assert (await client.thread(thread_id))["thread"]["title"] == "Steak"

        run = await collect(client.run(thread_id, "where does this go?"))
        assert run[-1].status == "completed"
        await client.cancel(run[0].handle.run_id if run[0].handle else RUN)

        resumed = await collect(
            client.resolve(thread_id, {"interrupt_id": "i-1", "decisions": [{"type": "approve"}]})
        )
        assert [frame.type for frame in resumed][-1] == "run.end"
        assert ("resume", (thread_id, "i-1")) in service.calls

        assert await collect(client.attach(thread_id)) == []
        await client.delete_thread(thread_id)
        assert await client.threads() == []


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_interrupt_route_is_addressed_by_the_requests_own_thread_dc19() -> None:
    """A fan-out's approval parks on the **expert's** derived thread, not the one being watched.

    The human is reading the Librarian's stream, so a client that posted the decisions to the thread
    it is streaming would resume a checkpoint with nothing pending — a 409 for a perfectly valid
    approval, on the one frame a human actually acts on.

    Superseded (Phase 5 rebuilds this): both halves of the scenario retire together — the
    ``/threads/{id}/interrupt`` route and ``client.resolve`` die with the gates, and the "expert's
    derived thread" it resolves against is the parent/derived split retired with sessions. Nothing
    parks any more, so there is no approval left to be addressed to the wrong thread.
    """
    service = ScriptedRuns([RunEnd(RUN, "filed")])
    async with daemon(service) as client:
        watched = (await client.create_thread(LIBRARIAN))["thread_id"]
        parked = expert_thread_id(watched, COOKING)
        service.rows[parked] = dataclasses.replace(
            service.rows[watched], thread_id=parked, agent_id=COOKING
        )

        await collect(
            client.resolve(parked, {"interrupt_id": "i-7", "decisions": [{"type": "approve"}]})
        )

    assert ("resume", (parked, "i-7")) in service.calls
    assert ("resume", (watched, "i-7")) not in service.calls


# --------------------------------------------------------------------------------------
# § purity — the transport knows a socket, and nothing below the seam (DC-21, TU-3)
# --------------------------------------------------------------------------------------


PURITY_SCRIPT = """
import importlib, sys

importlib.import_module("pkb.tui.client")
loaded = set(sys.modules)
banned = {name for name in loaded
          if name.split(".")[0] in {"deepagents", "langgraph", "langchain", "langchain_core",
                                    "fastapi", "starlette", "sse_starlette", "uvicorn"}}
banned |= {name for name in loaded if name.startswith(("pkb.server", "pkb.agents"))}

importlib.import_module("pkb.service")
leaked = {name for name in sys.modules
          if name.split(".")[0] in {"deepagents", "langgraph", "langchain", "aiosqlite",
                                    "fastapi", "httpx"}}

import json
print(json.dumps({"banned": sorted(banned), "httpx2": "httpx2" in loaded,
                  "leaked": sorted(leaked)}))
"""


def test_the_transport_imports_a_socket_library_and_nothing_below_the_seam_dc21() -> None:
    """A subprocess, because ``sys.modules`` in this process is already full of everything.

    ``pkb.tui`` importing ``pkb.server`` is broken under any correct layering — the TUI is a client
    of a daemon that may be on another machine — and the failure is invisible: the import works, the
    tests pass, and the layering contract is fiction until somebody reads the graph. Importing the
    harness would be worse still: it would drag langgraph into a terminal UI's startup time and make
    the model a client-side concern (RG-21).

    ``pkb.service`` is checked in the same breath because ``pkb.tui`` imports it for its dataclasses
    (DC-21). One future convenience re-export in ``pkb/service/__init__.py`` would reach
    ``pkb.service.runtime``, and the TUI's dependency graph would grow the whole harness without a
    line of ``pkb.tui`` changing.
    """
    result = subprocess.run(
        [sys.executable, "-c", PURITY_SCRIPT],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    report = json.loads(result.stdout.strip().splitlines()[-1])

    assert report["banned"] == [], "the transport reached below the seam"
    assert report["httpx2"] is True, "the transport is the one module that owns the socket"
    assert report["leaked"] == [], "importing the service seam pulled in the harness"
