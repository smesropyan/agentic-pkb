"""``create_app`` — the daemon's ASGI application (AP-1 … AP-20).

**A factory, never a module-level ``app``.** ``StreamableHTTPSessionManager.run()`` raises
``RuntimeError: … can only be called once per instance``, and every test that exercises the lifespan
enters it again — verified at the ASGI level, where a module-level app returns
``lifespan.startup.failed`` on its second cycle while two factory-built apps coexist happily.

**``pkb.server`` imports no ``pkb.agents`` module and no harness module, directly or transitively**
(AP-2, decision B). The service arrives as ``open_service``: an async-context-manager factory typed
against the Protocol. That is what makes the import-linter contract a real check rather than a green
light — an ``allow_indirect_imports`` on this package would keep the contract passing while deleting
exactly the check that caught ``pkb.server.app -> pkb.agents.runtime -> langgraph``.

The lifespan order is a rule, not a habit (AP-3):

1. open the service — which opens ``PkbRuntime``, which runs ``regenerate_all`` (RT-7);
2. Layer 3's SQLite connection — **after**, because the WAL pragma is set in the saver's ``setup()``
   and a connection opened earlier talks to a rollback-journal file (AP-4). ``open_service`` does
   both, in that order, and asserts it;
3. reconcile ``pending_interrupt_id`` against the checkpointer (AP-5);
4. enter ``mcp_server.session_manager.run()`` — the lifespan ``streamable_http_app`` would have
   provided and that mounting throws away (MC-3);
5. start the scan worker, then the Telegram task if configured.

Shutdown reverses it, and every in-flight run is cancelled with its subscribers told (AP-12).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from pkb.contracts import PkbAgentError
from pkb.server.errors import PROBLEM_CONTENT_TYPE, problem_body, status_and_code
from pkb.server.health import HealthState
from pkb.server.mcp import build_mcp_server, mount_mcp
from pkb.server.routes import build_router

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pkb.contracts import ScanQueue
    from pkb.service import PkbService

__all__ = ["ServerConfig", "create_app"]

_log = logging.getLogger(__name__)

ServiceFactory = Callable[[], Any]
"""An async context manager yielding a :class:`~pkb.service.PkbService`.

Typed loosely on purpose: naming ``pkb.service.runtime.open_service`` here would make this module
import the composition root, and through it the harness (I2). The daemon passes it in.
"""


@dataclass
class ServerConfig:
    """Deployment settings for the transport. Never read from the knowledge base."""

    version: str = "0.1.0"
    kb_root: str = ""
    scan_interval_seconds: float = 300.0
    """How often the worker drains the conflict-scan queue (AP-14)."""

    scan_queue: ScanQueue | None = None
    """Layer 3 holds the **harness-free Protocol** and drives the timer; the graph run is Layer 2's
    (C12, C-4). ``None`` leaves the worker disabled and ``/health`` says so."""

    telegram_task: Callable[[PkbService], Awaitable[None]] | None = None
    """The daemon-hosted bot (D9, AP-17) — step 5. Step 3 builds only the supervised slot."""

    health: HealthState = field(default_factory=HealthState)
    mcp_host: str = "127.0.0.1"


def create_app(open_service: ServiceFactory, *, config: ServerConfig | None = None) -> FastAPI:
    """Build the app. Each call is independent — its own service, its own session manager."""
    settings = config or ServerConfig()
    health = settings.health
    health.version = settings.version
    health.kb_root = settings.kb_root
    shutdown = anyio.Event()

    def service_of_request(request: Request) -> Any:
        service = getattr(request.app.state, "service", None)
        if service is None:  # pragma: no cover - only reachable before startup completes
            raise RuntimeError("the service is not open; the lifespan has not run")
        return service

    def service_now() -> Any:
        return app.state.service

    def snapshot_now() -> Any:
        return app.state.snapshot()

    mcp_server = build_mcp_server(service_now, snapshot_now)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with open_service() as service:
            app.state.service = service
            health.runtime_open = True
            repaired = await service.reconcile()
            if repaired:
                _log.info("reconciled %d thread rows against the checkpointer", repaired)
            async with mcp_server.session_manager.run():
                health.mcp_mounted = True
                workers = _start_workers(service, settings, health)
                try:
                    yield
                finally:
                    # AP-12: tell every subscriber before the sockets go, then stop the runs.
                    shutdown.set()
                    await asyncio.sleep(0)
                    for task in workers:
                        task.cancel()
                    for task in workers:
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await task
                    with contextlib.suppress(Exception):
                        await service.runs.aclose()
                    health.runtime_open = False
                    health.mcp_mounted = False

    app = FastAPI(title="pkb", version=settings.version, lifespan=lifespan)
    app.state.shutdown = shutdown
    app.state.health = health
    app.state.snapshot = lambda: None

    @app.get("/health")
    async def health_endpoint(request: Request) -> dict[str, Any]:
        """200 while serving, always (AP-18). Degradation lives in the body."""
        service = getattr(request.app.state, "service", None)
        counts = (0, 0)
        pending = 0
        agents = 0
        active = 0
        subscribers = 0
        if service is not None:
            agents = len(service.list_agents())
            active = service.runs.active
            subscribers = service.runs.subscribers
            counts = await service.thread_counts()
            pending = await service.proposal_count()
        return health.payload(
            agent_count=agents,
            active_runs=active,
            subscribers=subscribers,
            threads=counts,
            proposals_pending=pending,
        )

    app.include_router(build_router(service_of_request, lambda request: request.app.state.shutdown))
    mount_mcp(app, mcp_server, host=settings.mcp_host)

    @app.exception_handler(PkbAgentError)
    async def agent_error(_: Request, exc: PkbAgentError) -> JSONResponse:
        """The one place a typed error becomes a status code (RO-20)."""
        return _problem(exc)

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError) -> JSONResponse:
        return _problem(exc)

    return app


def _problem(exc: BaseException) -> JSONResponse:
    status, _code = status_and_code(exc)
    return JSONResponse(problem_body(exc), status_code=status, media_type=PROBLEM_CONTENT_TYPE)


def _start_workers(
    service: PkbService, settings: ServerConfig, health: HealthState
) -> list[asyncio.Task[None]]:
    tasks: list[asyncio.Task[None]] = []
    if settings.scan_queue is not None:
        health.scan_worker.state = "starting"
        tasks.append(
            asyncio.create_task(_scan_worker(service, settings, health), name="pkb-scan-worker")
        )
    if settings.telegram_task is not None:
        health.telegram.state = "starting"
        tasks.append(
            asyncio.create_task(
                _supervise(lambda: settings.telegram_task(service), health.telegram),  # type: ignore[misc]
                name="pkb-telegram",
            )
        )
    return tasks


async def _scan_worker(service: PkbService, settings: ServerConfig, health: HealthState) -> None:
    """Drain the conflict-scan queue on a timer (AP-14, C12).

    Layer 3 owns the **timer and the dequeue loop**; the graph run is Layer 2's, reached through
    ``service.run_scan``. **One request at a time**: a scan must not consume a fan-out slot a human
    is waiting on, and the deployment allows three concurrent cloud models in total.
    """
    queue = settings.scan_queue
    assert queue is not None
    health.scan_worker.running()
    while True:
        try:
            requests = await queue.take(1)
            for request in requests:
                await service.run_scan(request)
                await queue.done(request.topic_id)
                health.scan_worker.last_run_at = _stamp()
            health.scan_worker.pending = max(0, health.scan_worker.pending - len(requests))
        except asyncio.CancelledError:
            health.scan_worker.state = "stopped"
            raise
        except Exception as exc:
            _log.warning("conflict scan failed", exc_info=True)
            health.scan_worker.failed(exc)
            health.scan_worker.running()
        await asyncio.sleep(settings.scan_interval_seconds)


async def _supervise(start: Callable[[], Awaitable[None]], state: Any) -> None:
    """Restart a background task forever, and never let it take the daemon with it (AP-17).

    An unhandled exception restarts the task, is logged, and shows up in ``/health``; it never
    terminates the daemon or an in-flight run. The supervision loop is Layer 3's alone — RT-50 says
    Layer 2 contains no supervision, no ``/health`` and no origin-channel tracking.
    """
    backoff = 1.0
    while True:
        try:
            state.running()
            await start()
            state.state = "stopped"
            return
        except asyncio.CancelledError:
            state.state = "stopped"
            raise
        except Exception as exc:
            _log.warning("%s crashed; restarting", state.name, exc_info=True)
            state.failed(exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


def _stamp() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
