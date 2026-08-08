"""The composition root — the one place the whole system is assembled (arch §3, decision B).

Everything below is deliberately unable to do this. ``pkb.server`` may not import ``pkb.agents``
(AP-2), and ``pkb.service.runtime`` may import it but knows nothing about HTTP. So the wiring lives
here, in a module both may name and neither depends on:

    open_service  ->  pkb.service.runtime.open_service   (opens PkbRuntime, then Layer 3's SQLite)
    create_app    ->  pkb.server.app.create_app          (routes, SSE, MCP, workers, /health)

It is also where the two sinks are attached, because ``RuntimeConfig`` is frozen and the runtime is
built exactly once (SV-3). Neither sink is optional in a daemon:

* the **proposal sink** persists every ``PendingProposal`` into ``pkb_proposals`` (AP-15). Without
  it a propose-only write is one restart from gone, and the caller was told it was proposed.
* the **flush sink** surfaces each ``FlushReport``'s findings in ``/health`` and the log (AP-16).
  ``None`` drops broken links, orphans and ``DERIVED_WRITE_FAILED`` on the floor — a convenience in
  a unit test and a defect in a daemon.

The daemon binds **localhost**, has no auth and no multi-user namespacing, and no route carries a
version prefix (AP-20). Arch §10 defers deployment topology deliberately; every route sits behind
one router so a prefix is a one-line change later.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pkb.server.app import ServerConfig, create_app
from pkb.server.health import HealthState
from pkb.server.telegram import TelegramConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI

__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "build_app", "main"]

_log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def build_app(
    kb_root: Path,
    db_path: Path,
    *,
    config: Any | None = None,
    telegram: TelegramConfig | None = None,
) -> FastAPI:
    """Wire the runtime, the service and the app together.

    Imports of ``pkb.agents`` happen **inside** this function rather than at module scope. That is
    not style: ``pkb.server``'s import contract is checked transitively, and a module-level import
    here would make every ``import pkb.daemon`` in a test pull the harness into a process that is
    supposed to prove it can run without one.
    """
    from pkb.agents import RuntimeConfig
    from pkb.core.scan import scan
    from pkb.service.runtime import open_service

    health = HealthState(kb_root=str(kb_root), db_path=str(db_path))
    state: dict[str, Any] = {}

    def record_proposal(proposal: Any) -> None:
        """AP-15. Synchronous, because ``RuntimeConfig``'s sink is — so it schedules the write."""
        service = state.get("service")
        if service is None:  # pragma: no cover - a proposal before startup cannot happen
            return
        import asyncio

        asyncio.get_running_loop().create_task(service.proposals_store.record(proposal))

    def record_flush(report: Any) -> None:
        """AP-16. Findings reach ``/health`` and the log rather than the floor."""
        findings = len(getattr(report, "findings", ()) or ())
        health.record_flush(findings)
        if findings:
            _log.warning("flush reported %d finding(s)", findings)

    runtime_config = config or RuntimeConfig(
        proposal_sink=record_proposal,
        flush_sink=record_flush,
    )

    @asynccontextmanager
    async def opener() -> AsyncIterator[Any]:
        async with open_service(kb_root, db_path, config=runtime_config) as service:
            state["service"] = service
            state["connection"] = getattr(service, "connection", None)
            health.db_path = str(db_path)
            health.durability = str(getattr(runtime_config, "durability", ""))
            health.fanout_limit = int(getattr(runtime_config, "fanout_limit", 0))
            yield service

    app = create_app(opener, config=ServerConfig(kb_root=str(kb_root), health=health))
    # The snapshot the MCP pack tools read. A callable rather than a value: the tree changes under a
    # running daemon and a pack built from a stale snapshot is a pack of files that may not exist.
    app.state.snapshot = lambda: scan(kb_root)
    return app


def _telegram_task(
    config: TelegramConfig, state: dict[str, Any]
) -> Callable[[Any], Awaitable[None]]:
    """The supervised bot (D9, AP-17).

    Built here rather than in ``pkb.server`` because it needs the service *and* the store, and the
    composition root is the one place that has both. The task itself owns everything it spawns —
    ``_supervise`` awaits it and has no handle on a detached child, so a task that leaked one would
    get a second poller on every restart, which Telegram answers with ``409 Conflict``.
    """

    async def start(service: Any) -> None:
        from pkb.server.telegram import TelegramAdapter
        from pkb.server.telegram_api import HttpBotApi
        from pkb.service.telegram import SqliteTelegramStore

        connection = state.get("connection")
        if connection is None:  # pragma: no cover - the lifespan opens it before workers start
            raise RuntimeError("Layer 3's SQLite connection is not open")
        async with HttpBotApi(token=config.token) as api:
            adapter = TelegramAdapter(
                service=service,
                store=SqliteTelegramStore(connection),
                api=api,
                config=config,
            )
            await adapter.run()

    return start


def main(argv: list[str] | None = None) -> int:
    """Run the daemon. ``python -m pkb.daemon <kb-root> [--db …] [--port …]``."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(prog="pkb-daemon", description="Run the PKB daemon.")
    parser.add_argument("kb_root", type=Path, help="the knowledge base directory")
    parser.add_argument(
        "--db", type=Path, default=None, help="SQLite file (default: <kb>/../pkb.sqlite)"
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    db_path = args.db or args.kb_root.parent / "pkb.sqlite"
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    uvicorn.run(build_app(args.kb_root, db_path), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
