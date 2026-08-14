"""The HTTP surface — sessions, the seven commands' backing routes, and no more (RO-1 … RO-22, S-*).

``DESIGN.md`` §2: "there is one way in, the API" (S-13). A session is a durable named thing that
outlives any one channel, and every session-affecting operation — create, list, name, close, end, a
turn, its live events — is a route below and nothing else. ``/threads*``, ``/threads/{id}/interrupt``
and ``/proposals*`` are **gone**: their service-side backing survives in ``pkb.service.runtime``
until Task 6 deletes the gates it exists for (the plan's own "leave the methods, delete the
routes"), but nothing here serves them any longer. There is deliberately no
``DELETE /sessions/{id}`` — "nothing deletes a session" (§2.7) — and no interrupt-resume route: the
operator's instruction is the approval (architecture note on §2.10), so nothing here parks a write
for later.

Three things about the shape of this module are still load-bearing:

* **A session id is a bare UUID** (:func:`~pkb.service.sessions.mint_session_id`) — no ``::`` and no
  ``/``, unlike the derived thread ids this surface used to carry. Every ``/sessions/{session_id}``
  segment is therefore an ordinary path parameter, and the old registration-order hazard (RO-3, a
  ``:path`` converter swallowing a literal suffix) does not apply to it. An **agent** id still
  contains ``/`` and is still opaque (RO-2), so ``POST /agents/{agent_id:path}/sessions`` keeps the
  ``:path`` converter that route always needed.
* **The typed-error map is a single exception handler** (RO-20), never an ``HTTPException`` built by
  hand in a route. Several conditions share 409 and a client's reaction to each differs, so the body
  carries a stable machine ``code`` and clients branch on that (RO-21).
* **Nothing branches on ``origin_channel``** (RO-22). D3's whole point is that a conversation started
  in the TUI is finishable from Telegram; one authorization check silently deletes the guarantee in
  exactly the case the design is proudest of. It is provenance for display, notification targeting
  and diagnostics only — Task 7 wires channel attachment, and this still holds once it lands.

Streaming is :class:`sse_starlette.EventSourceResponse` rather than ``StreamingResponse`` (SS-1) —
it listens for ``http.disconnect`` itself, and it brings ping keep-alives, ``cache-control:
no-store`` and a shutdown grace period. And a response **subscribes**; it never drives the run
(AP-6). Closing it detaches. This machinery is salvaged whole from the thread era — re-homed to key
on a session id, never rewritten.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Body, Request, Response
from fastapi.responses import JSONResponse
from sse_starlette import EventSourceResponse, ServerSentEvent

from pkb.contracts import InvalidDecisionError
from pkb.server.errors import PROBLEM_CONTENT_TYPE, problem_body, status_and_code
from pkb.server.sse import SseEncoder
from pkb.service.session_file import LEARNING_AGENT_ID

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pkb.service import PkbService, RunSubscription, Session, Thread, ThreadDetail

__all__ = [
    "SSE_HEADERS",
    "build_router",
    "route_paths",
    "session_payload",
    "thread_payload",
]

SSE_HEADERS: Mapping[str, str] = {
    "Cache-Control": "no-store",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
"""Headers on every streaming route (SS-2).

``X-Accel-Buffering: no`` is for a reverse proxy that would otherwise buffer the whole response —
the classic way a working stream becomes a five-minute pause followed by everything at once. Response
compression must be **disabled** for these routes for the same reason; the app factory excludes them.
"""

PING_SECONDS = 15
"""Keep-alive interval (SS-6).

A **fixed-interval heartbeat over the whole connection**, not an idleness timer: ``sse-starlette``'s
ping loop is ``while active: sleep(interval); send(ping)`` and outgoing data never resets it. So a
busy stream carries pings too, and nothing downstream may read ``: ping`` as "the run is idle".

Idle gaps are normal rather than anomalous here, which is why the heartbeat exists at all: a filing
turn is ~16 s per model call on the default model, and a fan-out branch waiting on the concurrency
semaphore emits nothing until it starts. A **comment** frame rather than ``event: ping`` so it can
never be mistaken for a domain event by a client dispatching on ``type``.

Never ``0``: that is a busy loop, not an off switch — measured at 5680 ping frames in 0.2 s.
"""

SHUTDOWN_GRACE_SECONDS = 5
"""How long the daemon lets streams say goodbye (AP-12).

Comfortably over the watcher's poll plus encode time, and it must stay under uvicorn's own
``--timeout-graceful-shutdown`` or the process dies mid-farewell.
"""


def route_paths(app: Any) -> list[str]:
    """Every path the app actually serves, flattened — RO-1's pinned set.

    FastAPI keeps an included router as a single ``_IncludedRouter`` entry rather than splicing its
    routes into ``app.routes``, so the obvious ``[r.path for r in app.routes]`` reports two routes
    for an app that serves fourteen. A rule that pins the surface has to see the surface.
    """
    paths: list[str] = []
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", None)
        if path is not None:
            paths.append(path)
        # An included router is one entry holding its own routes under `original_router`.
        nested = getattr(route, "original_router", None)
        if nested is not None:
            paths.extend(route_paths(nested))
    return paths


def build_router(
    service_of: Callable[[Request], PkbService],
    shutdown_of: Callable[[Request], Any] | None = None,
) -> APIRouter:
    """Every route, over a service resolved per request.

    ``service_of`` rather than a captured service so the router is built once by the app factory and
    the service can come from application state — which is what lets two ``create_app()`` calls in
    one test process each have their own (AP-1).

    ``shutdown_of`` supplies the daemon's shutdown event to every stream (AP-12). It is a parameter
    rather than a global because the app is a factory: two apps in one process must not share one.
    """
    router = APIRouter()

    def shutdown_for(request: Request) -> Any:
        return shutdown_of(request) if shutdown_of is not None else None

    # -- catalog -------------------------------------------------------------------

    @router.get("/agents")
    async def list_agents(request: Request) -> dict[str, Any]:
        """The catalog, verbatim (RO-4). No field added, nothing reordered, no model chosen."""
        service = service_of(request)
        return {"agents": [asdict(descriptor) for descriptor in service.list_agents()]}

    # -- sessions --------------------------------------------------------------------

    @router.post("/agents/{agent_id:path}/sessions", status_code=201)
    async def create_session(
        request: Request, agent_id: str, body: Annotated[dict[str, Any] | None, Body()] = None
    ) -> Response:
        """201 with the full session and a ``Location`` header (S-1, S-9).

        ``{agent_id:path}`` because **an agent id contains ``/`` and is opaque** (RO-2): the captured
        string goes to the service verbatim, exactly as ``POST /agents/{id}/threads`` always took it
        — RO-2 is unchanged by the session rename. The body is ``{"objective": str|null,
        "name": str|null, "operator": str|null}``; ``operator`` is the caller's declared identity and
        defaults to ``"operator"`` when absent, per S-8: "a human or an agent alike," and this route
        has no way to tell the two apart, so it does not try.
        """
        service = service_of(request)
        fields = body or {}
        session = await service.create_session(
            agent_id,
            objective=fields.get("objective"),
            operator=fields.get("operator") or "operator",
            name=fields.get("name"),
        )
        return JSONResponse(
            {"session": session_payload(session)},
            status_code=201,
            headers={"Location": f"/sessions/{session.session_id}"},
        )

    @router.get("/sessions")
    async def list_sessions(
        request: Request, agent_id: str | None = None, state: str | None = None
    ) -> dict[str, Any]:
        """``?state=`` filters; ``state=closed`` **is** the learning queue, ``closed_at``-ordered
        rather than creation-ordered (S-23, S-25/P4)."""
        service = service_of(request)
        sessions = await service.list_sessions(agent_id, state=state)  # type: ignore[arg-type]
        return {"sessions": [session_payload(session) for session in sessions]}

    @router.post("/sessions/{session_id}/name")
    async def rename_session(
        request: Request, session_id: str, body: Annotated[dict[str, Any], Body()]
    ) -> dict[str, Any]:
        """``/name`` (S-16): store rename, file move and retitle, channel fan-out stubbed for Task 7."""
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise InvalidDecisionError("'name' is required and must be a non-empty string")
        service = service_of(request)
        session = await service.rename_session(session_id, name.strip())
        return {"session": session_payload(session)}

    @router.post("/sessions/{session_id}/close")
    async def close_session(request: Request, session_id: str) -> dict[str, Any]:
        """``/close`` (S-17, S-20, S-21): no body — the operator has nothing to craft, it judges
        nothing (S-20)."""
        service = service_of(request)
        return {"session": session_payload(await service.close_session(session_id))}

    @router.post("/sessions/{session_id}/end")
    async def end_session(request: Request, session_id: str) -> dict[str, Any]:
        """``/end`` (S-22): legal only from ``closed`` — refused on an open session (S-24/P3)."""
        service = service_of(request)
        return {"session": session_payload(await service.end_session(session_id))}

    @router.post("/sessions/{session_id}/runs")
    async def start_session_run(
        request: Request, session_id: str, body: Annotated[dict[str, Any], Body()]
    ) -> Any:
        """Begin a turn and stream it — the thread era's ``POST /runs`` machinery, re-homed.

        The body is ``{"message": str}`` and **nothing else** — ``approval_mode`` was never exposed
        over HTTP (RO-11) and there is even less reason now: no gate exists for it to auto-reject.
        A run on a session that is not ``open`` is refused (S-20, S-24/P3), by
        ``RuntimeService.start_session_run`` before anything streams.
        """
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            raise InvalidDecisionError("'message' is required and must be a non-empty string")
        service = service_of(request)
        subscription = await service.start_session_run(session_id, message)
        return _stream(subscription, service, shutdown=shutdown_for(request))

    @router.get("/sessions/{session_id}/events")
    async def attach_session_events(request: Request, session_id: str) -> Any:
        """Attach to the run in flight, replaying from ``seq 0``; 204 when idle (RO-17).

        How a reconnecting channel rejoins without starting a second run. No side effects: this
        starts nothing, and several channels may hold one session at once (S-6) — each attaches
        here independently.
        """
        service = service_of(request)
        subscription = await service.attach_session(session_id)
        if subscription is None:
            return Response(status_code=204)
        return _stream(subscription, service, started=False, shutdown=shutdown_for(request))

    @router.get("/sessions/{session_id}")
    async def get_session(request: Request, session_id: str) -> dict[str, Any]:
        """One session's row (S-4). ``UnknownSessionError`` → 404 for an id nobody minted."""
        service = service_of(request)
        return {"session": session_payload(await service.get_session(session_id))}

    # -- runs -------------------------------------------------------------------------

    @router.delete("/runs/{run_id}", status_code=204)
    async def cancel_run(request: Request, run_id: str) -> Response:
        """204, and **204 for an unknown id too** (RO-18): cancelling nothing is not an error."""
        service = service_of(request)
        await service.cancel(run_id)
        return Response(status_code=204)

    return router


# --------------------------------------------------------------------------------------
# Streaming
# --------------------------------------------------------------------------------------


def _stream(
    subscription: RunSubscription,
    service: PkbService,
    *,
    started: bool = True,
    shutdown: Any | None = None,
) -> EventSourceResponse:
    """Turn a subscription into an SSE response that **detaches** when it closes (AP-6, AP-7).

    Three measured constraints shape this function, and each one is a bug if ignored:

    * **The ``finally`` must be synchronous.** The enclosing anyio cancel scope is *level-triggered*,
      so the first ``await`` inside a ``finally`` raises ``CancelledError`` again and the rest of the
      block never runs — and ``asyncio.shield`` does **not** rescue it (measured for both response
      classes). Unsubscribing is pure in-memory bookkeeping; anything that needs an await on teardown
      belongs to the daemon-owned run task, never here.
    * **It detaches; it never cancels.** The daemon owns the run (AP-7). A dropped connection is a
      client going away, not a human changing their mind — cancellation has its own route.
    * **The generator watches the shutdown event itself.** ``shutdown_grace_period`` alone delivers
      nothing: without a generator that notices and yields a terminal frame, a shutdown simply
      cancels it and the client is left on a stream that ended silently (AP-12).

    No ``sep`` override: the library's CRLF is the SSE standard, and pinning LF here would make the
    wire bytes differ from every other SSE producer for no gain.
    """
    catalog = [descriptor.agent_id for descriptor in service.list_agents()]
    codes = getattr(getattr(service, "runs", None), "codes", None)
    encoder = SseEncoder(subscription.handle, catalog, codes)

    async def frames() -> AsyncIterator[ServerSentEvent]:
        try:
            if started:
                yield encoder.started()
            async for event in subscription.events:
                if shutdown is not None and shutdown.is_set():
                    yield encoder.cancelled()  # AP-12: say goodbye before the grace period ends
                    return
                yield encoder.event(event)
        finally:
            # Synchronous, and it must stay that way — see the docstring.
            close = subscription.close
            if callable(close):
                close()

    return EventSourceResponse(
        frames(),
        headers=dict(SSE_HEADERS),
        ping=PING_SECONDS,
        shutdown_event=shutdown,
        shutdown_grace_period=SHUTDOWN_GRACE_SECONDS,
    )


# --------------------------------------------------------------------------------------
# Payloads — the wire shapes of §5.1
# --------------------------------------------------------------------------------------


def thread_payload(thread: Thread) -> dict[str, Any]:
    """``Thread`` on the wire, with ``kind`` and ``parent_thread_id`` **computed** (ST-5, ST-6)."""
    return {
        "thread_id": thread.thread_id,
        "agent_id": thread.agent_id,
        "title": thread.title,
        "kind": thread.kind,
        "parent_thread_id": thread.parent_thread_id,
        "created_at": thread.created_at.isoformat().replace("+00:00", "Z"),
        "updated_at": thread.updated_at.isoformat().replace("+00:00", "Z"),
        "origin_channel": thread.origin_channel,
        "pending_interrupt_id": thread.pending_interrupt_id,
    }


def detail_payload(detail: ThreadDetail) -> dict[str, Any]:
    """``GET /threads/{id}`` (RO-10).

    ``created_at`` on a message is **always null** — LangChain messages carry no timestamp — so it
    is nullable on the wire and no client may sort on it. Per-thread times come from the table.
    """
    return {
        "thread": thread_payload(detail.thread),
        "messages": [
            {
                "role": message.role,
                "text": message.text,
                "created_at": message.created_at.isoformat() if message.created_at else None,
            }
            for message in detail.messages
        ],
        "pending_interrupt": _approval_payload(detail.pending) if detail.pending else None,
        "children": [thread_payload(child) for child in detail.children],
    }


def _approval_payload(request: Any) -> dict[str, Any]:
    """``ApprovalRequest`` verbatim — Layer 3 renders none of it (SS-11).

    ``description`` already holds the server-rendered unified diff and any validation finding
    (RT-34, RT-35) and ``allowed_decisions`` is server-side truth. Layer 3 must not recompute,
    filter or re-render any of it: a second diff renderer is a second answer to "what am I
    approving", and a client that re-reads the tree to build one cannot exist under I2 anyway.
    """
    return {
        "interrupt_id": request.interrupt_id,
        "agent_id": request.agent_id,
        "thread_id": request.thread_id,
        "actions": [
            {
                "tool": action.tool,
                "args": dict(action.args),
                "description": action.description,
                "allowed_decisions": list(action.allowed_decisions),
                "reason": action.reason,
            }
            for action in request.actions
        ],
    }


def session_payload(session: Session) -> dict[str, Any]:
    """``Session`` on the wire (S-1 … S-7).

    ``file_path`` is ``null`` for a session opened on the Learning agent (S-19, S-26): it never had
    a file, and reporting the computed ``sessions/<name>.md`` string there would claim a path that
    is not on disk. Every other session's ``file_path`` is always populated — one file, whole life
    (S-26, S-27) — so a client never has to ask a second endpoint whether it exists.
    """
    return {
        "session_id": session.session_id,
        "agent_id": session.agent_id,
        "objective": session.objective,
        "name": session.name,
        "operator": session.operator,
        "state": session.state,
        "created_at": _iso(session.created_at),
        "updated_at": _iso(session.updated_at),
        "closed_at": _iso(session.closed_at) if session.closed_at else None,
        "ended_at": _iso(session.ended_at) if session.ended_at else None,
        "file_path": session.file_path if session.agent_id != LEARNING_AGENT_ID else None,
    }


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def error_response(exc: BaseException, **extra: Any) -> JSONResponse:
    """The one place a typed error becomes a response (RO-20, RO-21)."""
    status, _ = status_and_code(exc)
    return JSONResponse(
        problem_body(exc, **extra), status_code=status, media_type=PROBLEM_CONTENT_TYPE
    )
