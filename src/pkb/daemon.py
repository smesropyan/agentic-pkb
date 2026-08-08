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

It is also the **only** place the Telegram deployment configuration is read (TG-17, TG-24, Q25):
the token from the environment, the mapping and the allow-list from a file beside the SQLite
database. Neither telegram module may read either — they cannot even import ``os`` without failing
the built SV-22 scan — and neither may read the knowledge base, because deployment configuration
living in KB content would let an agent's own write change which agent a chat talks to.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from pkb.server.app import ServerConfig, create_app
from pkb.server.health import HealthState, SubsystemState
from pkb.server.telegram import TelegramConfig
from pkb.server.telegram_api import shield_credentialed_http_logs

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "TELEGRAM_CONFIG_SUFFIX",
    "TELEGRAM_TOKEN_ENV",
    "build_app",
    "load_telegram_config",
    "main",
    "telegram_config_path",
]

_log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

TELEGRAM_TOKEN_ENV: Final = "PKB_TELEGRAM_TOKEN"
"""Where the bot token comes from (TG-24, Q25(b)).

A CLI flag would put the credential in the process table and the shell history; the knowledge base
is forbidden outright (I3). This name is read **here and nowhere else** — a grep for it across
``src/`` finds exactly this module.
"""

TELEGRAM_CONFIG_SUFFIX: Final = ".telegram.json"
"""``<db>.telegram.json`` — the mapping and the allow-list live beside the database (Q25(c)).

Beside the *database* rather than inside the knowledge base so the configuration travels with the
deployment rather than with the content, and so no agent write can reach it (I3, TG-17).
"""


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

    telegram_task = None
    if telegram is not None and telegram.enabled:
        # TG-14: the slot Layer 3 built stays empty unless something fills it, and until this
        # commit nothing did — `_telegram_task` existed and was never called, so the bot could not
        # start through the daemon by any path while every direct-drive test passed.
        _describe_telegram(health.telegram, telegram)
        # TG-16. `HttpBotApi` installs this itself, but here it lands *before* the supervised task
        # ever builds one — and the first thing that task does is a credentialed request. Wiring
        # time is the only moment guaranteed to precede every client in the process.
        shield_credentialed_http_logs()
        telegram_task = _telegram_task(telegram, state, health.telegram)
        _log.info("telegram enabled for %d chat(s)", len(telegram.chats))

    app = create_app(
        opener,
        config=ServerConfig(kb_root=str(kb_root), health=health, telegram_task=telegram_task),
    )
    # The snapshot the MCP pack tools read. A callable rather than a value: the tree changes under a
    # running daemon and a pack built from a stale snapshot is a pack of files that may not exist.
    app.state.snapshot = lambda: scan(kb_root)
    return app


def _describe_telegram(block: SubsystemState, config: TelegramConfig) -> None:
    """Publish the *shape* of the mapping on ``/health`` at wiring time (TG-11, TG-3).

    Both fields are configuration, so they are known before the task starts and stay correct while
    it is crash-looping or cancelled — which is exactly when a human reads ``/health``. Computing
    them inside the bot would make the answer disappear at the only moment anybody wants it.

    ``agents`` is the **distinct** set the mapping names, never a count: two chats may address one
    expert (TG-25), so a length comparison would report a phantom unmapped agent.
    """
    block.chats = len(config.chats)
    block.agents = frozenset(config.chats.values())


def _telegram_task(
    config: TelegramConfig, state: dict[str, Any], health: Any
) -> Callable[[Any], Awaitable[None]]:
    """The supervised bot (D9, AP-17, TG-14).

    Built here rather than in ``pkb.server`` because it needs the service *and* the store, and the
    composition root is the one place that has both. The task itself owns everything it spawns —
    ``_supervise`` awaits it and has no handle on a detached child, so a task that leaked one would
    get a second poller on every restart, which Telegram answers with ``409 Conflict``.

    The closure carries the ``HealthState``'s telegram block as well as the config, because
    **The ``async with`` below IS the ``finally`` TG-10 requires**, and it is here rather than in
    the adapter because this is where the client is constructed: it closes both connection pools on
    the exception path and on the ``CancelledError`` path alike, and re-raises the cancellation
    untouched. Driven both ways — fifty forced restarts leave exactly one open client, and a
    cancellation mid-long-poll closes the client within one loop turn. Unlike an SSE route this
    teardown **may** await: the ASGI cancel-scope rule that forbids it does not apply to a bare
    ``asyncio.Task``, measured.

    **Layer 3's SQLite connection is deliberately not closed here.** It is the checkpointer's own
    shared handle, taken from ``state["connection"]``, and the lifespan owns it; closing it on a
    bot restart would take every in-flight run and the whole HTTP API down with the bot — the exact
    coupling D9's supervised slot exists to avoid. The store wraps it and owns nothing.

    ``ServerConfig.telegram_task`` is a one-argument callable taking only the service and
    ``ServerConfig`` does not change (C-30). Without it nothing ever writes ``last_poll_ok_at``, and
    ``state == "running"`` is then read as "Telegram is reachable" when it only means the task is
    alive — ``_supervise`` stamps ``running`` *before* awaiting the first line of the body, so a
    daemon with a wrong token looks perfectly healthy for its whole first poll (TG-12).
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
                health=health,
            )
            await adapter.run()

    return start


def telegram_config_path(db_path: Path) -> Path:
    """``<db>.telegram.json`` — beside the database, never under ``kb_root`` (Q25(c), I3)."""
    return db_path.with_name(db_path.name + TELEGRAM_CONFIG_SUFFIX)


def load_telegram_config(
    db_path: Path, *, path: Path | None = None, token: str | None = None
) -> TelegramConfig | None:
    """Read the deployment's Telegram configuration, or ``None`` to leave the bot off (TG-17, Q25).

    ``{"chats": {"<chat_id>": "<agent_id>"}, "owners": [<user_id>, …]}``. JSON object keys are
    strings by definition, so the chat ids are coerced to ``int`` here — the mapping the adapter
    holds is keyed by the type Telegram sends (TG-1).

    Three failure modes, deliberately not the same:

    * **no file, no token** — the bot is simply not deployed. Quiet, and the daemon serves as it
      always did; D9's bot is optional and a missing one must never cost the human their HTTP API.
    * **a file that exists and is broken** — a startup error naming the path. Ignoring it silently
      leaves the human believing the bot is on and waiting for replies that no code path can send.
    * **a token whose file names no owners** — the deployment is *inert*: ``owner_user_ids`` is the
      system's only authentication boundary and empty means refuse everyone (decision X). That is
      the right default for a misconfiguration and an invisible one, so it is logged at warning.
    """
    explicit = path is not None
    source = path or telegram_config_path(db_path)
    if not source.is_file():
        if explicit:
            # A path the human typed and got wrong is not "not deployed"; it is a typo, and
            # answering it with silence is how a bot stays off for a week unnoticed.
            raise ValueError(f"--telegram-config names no file: {source}")
        return None

    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{source} is not readable Telegram configuration: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{source} must hold a JSON object, not {type(document).__name__}")

    chats = _telegram_chats(document.get("chats", {}), source)
    owners = _telegram_owners(document.get("owners", []), source)

    if not token:
        _log.info(
            "telegram is configured in %s but %s is unset; the bot stays off",
            source,
            TELEGRAM_TOKEN_ENV,
        )
        return None
    if chats and not owners:
        _log.warning(
            "telegram has a token and %d chat(s) but no owners in %s: every message and every "
            "button press will be refused until an owner user id is added",
            len(chats),
            source,
        )
    return TelegramConfig(token=token, chats=chats, owner_user_ids=owners)


def _telegram_chats(raw: Any, source: Path) -> dict[int, str]:
    """``{"<chat_id>": "<agent_id>"}`` → ``{chat_id: agent_id}`` (TG-1, TG-17).

    A non-numeric key or a non-string agent id is a startup error rather than a dropped entry: a
    silently dropped chat is answered as *unmapped* (TG-2), which reads to the human as the bot
    ignoring them for no reason.

    **A group, supergroup or channel id is refused here** (TG-19). Telegram gives those negative
    ids and private chats positive ones, so the check is one comparison and it is the only place it
    can be made *before* the deployment starts. A group is many senders with no identity check in
    front of a knowledge base with no undo, and Telegram's group privacy mode silently drops most
    messages — so a mapped group *half* works, which is worse than refusing: the human cannot tell
    a dropped note from a filed one. Refusing at load time rather than at delivery is the
    difference between a startup error naming the line and a chat that quietly does nothing.
    """
    if not isinstance(raw, dict):
        raise ValueError(f'{source}: "chats" must be an object of chat_id → agent_id')
    chats: dict[int, str] = {}
    for key, value in raw.items():
        try:
            chat_id = int(key)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{source}: chat id {key!r} is not an integer") from exc
        if not isinstance(value, str) or not value:
            raise ValueError(f"{source}: chat {chat_id} names no agent id")
        if chat_id <= 0:
            raise ValueError(
                f"{source}: chat {chat_id} is a group, supergroup or channel, and only private "
                f"chats are eligible (TG-19). A group has many senders and no identity check, and "
                f"Telegram's privacy mode drops most of its messages, so a mapped group would half "
                f"work — remove the entry or map the private chat with the expert instead"
            )
        chats[chat_id] = value
    return chats


def _telegram_owners(raw: Any, source: Path) -> frozenset[int]:
    """``[<user_id>, …]`` → the allow-list every sender is checked against (TG-20, decision X).

    Quoted ids are accepted because Telegram user ids are long enough that a human writing the file
    by hand reasonably quotes them; a ``true`` or a ``12.5`` is refused, since guessing what those
    meant would widen the one boundary the system has.
    """
    if not isinstance(raw, list):
        raise ValueError(f'{source}: "owners" must be a list of Telegram user ids')
    owners: set[int] = set()
    for entry in raw:
        if isinstance(entry, bool) or not isinstance(entry, int | str):
            raise ValueError(f"{source}: owner {entry!r} is not a Telegram user id")
        try:
            owners.add(int(entry))
        except ValueError as exc:
            raise ValueError(f"{source}: owner {entry!r} is not a Telegram user id") from exc
    return frozenset(owners)


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
    parser.add_argument(
        "--telegram-config",
        type=Path,
        default=None,
        help=f"chat mapping and owner allow-list (default: <db>{TELEGRAM_CONFIG_SUFFIX})",
    )
    args = parser.parse_args(argv)

    db_path = args.db or args.kb_root.parent / "pkb.sqlite"
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # Configured before the app is built, and logging is up first so the "no owners" warning and
    # the "no token" notice are not swallowed by an unconfigured root logger (TG-17, Q25).
    try:
        telegram = load_telegram_config(
            db_path, path=args.telegram_config, token=os.environ.get(TELEGRAM_TOKEN_ENV)
        )
    except ValueError as exc:
        # A clean, path-naming exit rather than a traceback — and never a daemon that serves with a
        # bot the human believes is running.
        parser.error(str(exc))
    uvicorn.run(build_app(args.kb_root, db_path, telegram=telegram), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
