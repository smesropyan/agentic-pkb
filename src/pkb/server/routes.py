"""The HTTP surface — thirteen routes and no more (RO-1 … RO-22).

Arch §6's eight, plus five declared additions, plus ``/mcp``. Any route beyond those is an addition
that must be given a rule id, and a test pins the set: a route that appears without a rule is a
surface nobody agreed to and a client will start depending on.

Three things about the shape of this module are load-bearing:

* **Registration order** (RO-3). A thread id is not URL-simple — a derived one contains ``::`` *and*
  ``/`` — so every ``{thread_id}`` is a ``:path`` converter, and the routes with a literal suffix
  (``/runs``, ``/interrupt``, ``/events``) are registered **before** the bare greedy one. Otherwise
  ``GET /threads/{tid:path}`` swallows ``/threads/x/events`` and the events route is dead.
* **The typed-error map is a single exception handler** (RO-20), never an ``HTTPException`` built by
  hand in a route. Three conditions share 409 and a client's reaction to each differs, so the body
  carries a stable machine ``code`` and clients branch on that (RO-21).
* **Nothing branches on ``origin_channel``** (RO-22). D3's whole point is that a thread started in
  the TUI is finishable from Telegram; one authorization check against that column silently deletes
  the guarantee in exactly the case the design is proudest of. It is provenance for display,
  notification targeting and diagnostics only.

Streaming is :class:`sse_starlette.EventSourceResponse` rather than ``StreamingResponse`` (SS-1) —
it listens for ``http.disconnect`` itself, and it brings ping keep-alives, ``cache-control:
no-store`` and a shutdown grace period. And a response **subscribes**; it never drives the run
(AP-6). Closing it detaches.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import asdict
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Body, Request, Response
from fastapi.responses import JSONResponse
from sse_starlette import EventSourceResponse, ServerSentEvent

from pkb.contracts import Decision, InvalidDecisionError
from pkb.server.errors import PROBLEM_CONTENT_TYPE, problem_body, status_and_code
from pkb.server.sse import SseEncoder

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pkb.service import PkbService, RunSubscription, Thread, ThreadDetail

__all__ = ["SSE_HEADERS", "build_router", "route_paths", "thread_payload"]

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

    # -- threads -------------------------------------------------------------------

    @router.post("/agents/{agent_id:path}/threads", status_code=201)
    async def create_thread(
        request: Request, agent_id: str, body: Annotated[dict[str, Any] | None, Body()] = None
    ) -> Response:
        """201 with the full thread and a ``Location`` header (RO-5).

        ``{agent_id:path}`` because **an agent id contains ``/`` and is opaque** (RO-2): the captured
        string goes to the service verbatim. ``%2F`` is not a workable alternative — Starlette
        decodes it back before matching and proxies normalize it — and nothing in Layer 3 splits,
        re-encodes, slugifies or fuzzy-matches an id.
        """
        service = service_of(request)
        fields = body or {}
        thread = await service.create_thread(
            agent_id,
            title=fields.get("title"),
            origin_channel=fields.get("origin_channel") or "http",
        )
        return JSONResponse(
            {"thread": thread_payload(thread)},
            status_code=201,
            headers={"Location": f"/threads/{thread.thread_id}"},
        )

    @router.get("/threads")
    async def list_threads(request: Request, agent_id: str | None = None) -> dict[str, Any]:
        """Grouped per expert, most-in-need-of-attention first (RO-6, RO-7, RO-8)."""
        service = service_of(request)
        threads = await service.list_threads(agent_id)
        return {"threads": [thread_payload(thread) for thread in threads]}

    # RO-3: the literal-suffix routes must be registered BEFORE the greedy `{thread_id:path}` ones,
    # or the converter swallows `/runs`, `/interrupt` and `/events`.

    @router.post("/threads/{thread_id:path}/runs")
    async def start_run(
        request: Request, thread_id: str, body: Annotated[dict[str, Any], Body()]
    ) -> Any:
        """Begin a turn and stream it (RO-11).

        The body is ``{"message": str}`` and **nothing else**. ``approval_mode`` is deliberately not
        exposed: propose-only means Layer 2 auto-rejects every gate, which over a human channel is a
        run that silently refuses its own approvals and files nothing — a broken agent, not a mode.
        MCP sets it in-process, where the reason for it actually holds (RT-42, SV-17).
        """
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            raise InvalidDecisionError("'message' is required and must be a non-empty string")
        if "approval_mode" in body:
            raise InvalidDecisionError(
                "'approval_mode' is not settable over HTTP: propose-only is the MCP channel's mode"
            )
        service = service_of(request)
        subscription = await service.start_run(thread_id.rstrip("/"), message)
        return _stream(subscription, service, shutdown=shutdown_for(request))

    @router.post("/threads/{thread_id:path}/interrupt")
    async def resolve_interrupt(
        request: Request, thread_id: str, body: Annotated[dict[str, Any], Body()]
    ) -> Any:
        """Answer an approval and continue **the same run** (RO-12, RO-13, SS-16).

        ``interrupt_id`` is **required on the wire** even though the validator takes it optionally.
        Two channels looking at one approval is the design rather than an edge case, and without the
        id a second client's stale decisions apply to whatever is pending *now* — silently, with no
        undo. Requiring it turns a lost update into a clean 409.

        Validation happens before the response becomes a stream (RO-13), which is what makes 400 and
        409 deterministic rather than smuggled into an already-committed 200.
        """
        interrupt_id = body.get("interrupt_id")
        if not isinstance(interrupt_id, str) or not interrupt_id:
            raise InvalidDecisionError(
                "'interrupt_id' is required: without it a second client's stale decisions would "
                "apply to whatever is pending now"
            )
        raw = body.get("decisions")
        if not isinstance(raw, list):
            raise InvalidDecisionError("'decisions' must be a list")
        decisions = [_decision(item) for item in raw]
        service = service_of(request)
        subscription = await service.resume(
            thread_id.rstrip("/"), decisions, interrupt_id=interrupt_id
        )
        return _stream(subscription, service, shutdown=shutdown_for(request))

    @router.get("/threads/{thread_id:path}/events")
    async def attach(request: Request, thread_id: str) -> Any:
        """Attach to the run in flight, replaying from ``seq 0``; 204 when idle (RO-17).

        How a reconnecting client rejoins without starting a second run — which ``POST /runs`` would
        refuse with 409 anyway. No side effects: this starts nothing.
        """
        service = service_of(request)
        subscription = await service.attach(thread_id.rstrip("/"))
        if subscription is None:
            return Response(status_code=204)
        return _stream(subscription, service, started=False, shutdown=shutdown_for(request))

    @router.patch("/threads/{thread_id:path}")
    async def rename(
        request: Request, thread_id: str, body: Annotated[dict[str, Any], Body()]
    ) -> Any:
        """A human's title, permanent (RO-19, SV-27)."""
        title = body.get("title")
        if not isinstance(title, str) or not title.strip():
            raise InvalidDecisionError("'title' is required and must be a non-empty string")
        service = service_of(request)
        return {"thread": thread_payload(await service.set_title(thread_id, title.strip()))}

    @router.get("/threads/{thread_id:path}")
    async def get_thread(request: Request, thread_id: str) -> dict[str, Any]:
        """One conversation, its history, its live approval, and its children (RO-10)."""
        service = service_of(request)
        return detail_payload(await service.get_thread(thread_id))

    @router.delete("/threads/{thread_id:path}", status_code=204)
    async def delete_thread(request: Request, thread_id: str) -> Response:
        """204, with the cascade (RO-16, SV-24)."""
        service = service_of(request)
        await service.delete_thread(thread_id)
        return Response(status_code=204)

    # -- runs, proposals -----------------------------------------------------------

    @router.delete("/runs/{run_id}", status_code=204)
    async def cancel_run(request: Request, run_id: str) -> Response:
        """204, and **204 for an unknown id too** (RO-18): cancelling nothing is not an error."""
        service = service_of(request)
        await service.cancel(run_id)
        return Response(status_code=204)

    @router.get("/proposals")
    async def list_proposals(request: Request, status: str = "pending") -> dict[str, Any]:
        service = service_of(request)
        return {
            "proposals": [_proposal_payload(p) for p in await service.list_proposals(status=status)]
        }

    @router.delete("/proposals/{proposal_id}", status_code=204)
    async def dismiss_proposal(request: Request, proposal_id: str) -> Response:
        service = service_of(request)
        await service.dismiss_proposal(proposal_id)
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


def _proposal_payload(proposal: Any) -> dict[str, Any]:
    return {
        "proposal_id": proposal.proposal_id,
        "agent_id": proposal.agent_id,
        "thread_id": proposal.thread_id,
        "created_at": proposal.created_at.isoformat().replace("+00:00", "Z"),
        "action": {
            "tool": proposal.action.tool,
            "args": dict(proposal.action.args),
            "description": proposal.action.description,
            "allowed_decisions": list(proposal.action.allowed_decisions),
            "reason": proposal.action.reason,
        },
    }


def _decision(item: Any) -> Decision:
    """One wire decision, or a 400 (RO-15).

    A client may **narrow** the decisions it offers — Telegram drops ``edit`` — and never widen
    them. The server-side set is the truth and is re-validated on the way back in, so a hand-crafted
    request carrying ``edit`` against an action that forbids it is a 400 regardless of channel; that
    check is ``validate_decisions``', in the service, and this only builds the object.
    """
    if not isinstance(item, Mapping):
        raise InvalidDecisionError("each decision must be an object")
    kind = item.get("type")
    if kind not in ("approve", "edit", "reject", "respond"):
        raise InvalidDecisionError(f"unknown decision type {kind!r}")
    return Decision(
        type=kind,
        message=item.get("message"),
        edited_args=item.get("edited_args"),
        edited_tool=item.get("edited_tool"),
    )


def error_response(exc: BaseException, **extra: Any) -> JSONResponse:
    """The one place a typed error becomes a response (RO-20, RO-21)."""
    status, _ = status_and_code(exc)
    return JSONResponse(
        problem_body(exc, **extra), status_code=status, media_type=PROBLEM_CONTENT_TYPE
    )
