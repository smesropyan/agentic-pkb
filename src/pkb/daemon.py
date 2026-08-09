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
**both secrets from the environment** — ``PKB_TELEGRAM_TOKEN`` and ``PKB_TELEGRAM_OWNERS``, sourced
from a gitignored ``.env`` if one is there — and the chat mapping from a file beside the SQLite
database. The allow-list sits with the token rather than with the mapping because it is the token's
other half: whoever is on it can approve an irreversible write, so the two things that must be
protected are in one place and the file that remains names no credential at all.

Neither telegram module may read either — they cannot even import ``os`` without failing the built
SV-22 scan — and neither may read the knowledge base, because deployment configuration living in KB
content would let an agent's own write change which agent a chat talks to.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
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
    "DEFAULT_ENV_FILE",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "TELEGRAM_CONFIG_SUFFIX",
    "TELEGRAM_OWNERS_ENV",
    "TELEGRAM_TOKEN_ENV",
    "build_app",
    "load_env_file",
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

TELEGRAM_OWNERS_ENV: Final = "PKB_TELEGRAM_OWNERS"
"""Who may say yes, as a comma- or space-separated list of Telegram user ids (TG-20, decision X).

**In the environment beside the token rather than in the JSON file**, because the allow-list is the
credential's other half. A token is only as dangerous as the set of people who may answer an
approval with it, and a deployment that leaks one and not the other is a deployment where the leak
is survivable. Keeping them in one place also means one file to protect and one to gitignore, rather
than a secret in the environment and an authorization list in a file that reads as harmless.

Empty or unset means the bot refuses everyone. That is the correct default for a misconfiguration —
the alternative is a bot that accepts a stranger because a variable was misspelled — and it is
invisible, so it is logged at warning whenever a token and chats are configured without it.
"""

DEFAULT_ENV_FILE: Final = ".env"
"""Read from the working directory at startup, if it is there, and gitignored.

Not a dependency: the parser is a dozen lines of stdlib below. A **real environment variable always
wins** over a line in this file, so a systemd unit, a container's secret mount or a one-off
``PKB_TELEGRAM_TOKEN=… python -m pkb.daemon`` is never silently overridden by a stale file someone
forgot was there — which is the failure mode that makes dotenv loaders hard to debug.
"""

TELEGRAM_CONFIG_SUFFIX: Final = ".telegram.json"
"""``<db>.telegram.json`` — the **chat mapping**, and nothing else, beside the database (Q25(c)).

Beside the *database* rather than inside the knowledge base so the configuration travels with the
deployment rather than with the content, and so no agent write can reach it (I3, TG-17).

It holds no credential — the token and the allow-list are both environment variables — so this file
is safe to commit, and the fact that it is safe is worth stating: the moment one secret lives in a
file that looks harmless, someone commits it.
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


def load_env_file(path: Path, environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Fold ``KEY=value`` lines into the environment and return the names taken. Missing file: none.

    A dozen lines rather than a dependency, and three of its rules are load-bearing:

    * **An existing variable is never overwritten.** The environment is the deployment's own voice —
      a systemd ``Environment=``, a container secret, a one-off ``PKB_TELEGRAM_TOKEN=… python -m
      pkb.daemon`` — and a file that silently wins over it produces a bot running on last month's
      token with no way to see why. The returned list names only what the file actually supplied.
    * **Values are taken literally**, minus one matched pair of surrounding quotes. No interpolation,
      no escapes, no ``export`` semantics: a bot token contains ``:``, ``_`` and ``-``, and a parser
      clever enough to expand ``$`` is a parser that will one day eat part of a credential.
    * **A world- or group-readable file is a warning.** This file holds the token *and* the
      allow-list, which together are full write access to a knowledge base with no undo.
    """
    target = environ if environ is not None else os.environ
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise ValueError(f"{path} is not readable: {exc}") from exc

    _warn_if_widely_readable(path)
    taken: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not name:
            raise ValueError(f"{path}:{number} is not a KEY=value line: {raw.strip()!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name in target:
            continue  # the environment wins — see the docstring
        target[name] = value
        taken.append(name)
    return taken


def _warn_if_widely_readable(path: Path) -> None:
    """Say so once if the secrets file is readable by anyone but its owner.

    Not an error: refusing to start over a permission bit would strand a human whose deployment is
    on a single-user machine where it does not matter. But the token plus the allow-list is the
    whole authentication story of a process that writes to a tree with no undo, so it is worth one
    line naming the fix.
    """
    try:
        mode = path.stat().st_mode
    except OSError:  # pragma: no cover - it was readable a moment ago
        return
    if mode & 0o077:
        _log.warning(
            "%s is readable beyond its owner (mode %o); it holds the bot token and the owner "
            "allow-list, so `chmod 600 %s`",
            path,
            mode & 0o777,
            path,
        )


def load_telegram_config(
    db_path: Path,
    *,
    path: Path | None = None,
    token: str | None = None,
    owners: str | None = None,
) -> TelegramConfig | None:
    """Read the deployment's Telegram configuration, or ``None`` to leave the bot off (TG-17, Q25).

    **The two secrets come from the environment and the mapping comes from the file.** ``token`` is
    ``PKB_TELEGRAM_TOKEN`` and ``owners`` is ``PKB_TELEGRAM_OWNERS``; the file holds only
    ``{"chats": {"<chat_id>": "<agent_id>"}}``, which names no credential and can be committed. JSON
    object keys are strings by definition, so the chat ids are coerced to ``int`` here — the mapping
    the adapter holds is keyed by the type Telegram sends (TG-1).

    Q25 originally put the allow-list in the file beside the mapping. It moved because the allow-list
    is the token's other half rather than a routing detail: whoever is on it can approve an
    irreversible write, so it belongs wherever the credential is protected, and splitting the two
    across a gitignored environment and a checked-in file is how one of them gets committed.

    Four failure modes, deliberately not the same:

    * **no file, no token** — the bot is simply not deployed. Quiet, and the daemon serves as it
      always did; D9's bot is optional and a missing one must never cost the human their HTTP API.
    * **a file that exists and is broken** — a startup error naming the path. Ignoring it silently
      leaves the human believing the bot is on and waiting for replies that no code path can send.
    * **a file that still carries ``owners``** — also a startup error, naming the environment
      variable it moved to. This is the one case where silence would be dangerous rather than merely
      confusing: a file listing three authorized users, ignored, reads as an allow-list that is set.
    * **a token with chats and no owners** — the deployment is *inert*: ``owner_user_ids`` is the
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

    if "owners" in document:
        raise ValueError(
            f"{source}: the owner allow-list moved out of this file and into ${TELEGRAM_OWNERS_ENV} "
            f'— set it beside the token and delete the "owners" key. It is refused rather than '
            f"ignored because an allow-list sitting in a file that nothing reads looks exactly like "
            f"an allow-list that is in force"
        )
    chats = _telegram_chats(document.get("chats", {}), source)
    allowed = _telegram_owners(owners)

    if not token:
        _log.info(
            "telegram is configured in %s but %s is unset; the bot stays off",
            source,
            TELEGRAM_TOKEN_ENV,
        )
        return None
    if chats and not allowed:
        _log.warning(
            "telegram has a token and %d chat(s) but %s names no owners: every message and every "
            "button press will be refused until an owner user id is added",
            len(chats),
            TELEGRAM_OWNERS_ENV,
        )
    return TelegramConfig(token=token, chats=chats, owner_user_ids=allowed)


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


def _telegram_owners(raw: str | None) -> frozenset[int]:
    """``PKB_TELEGRAM_OWNERS`` → the allow-list every sender is checked against (TG-20, decision X).

    Commas, spaces or both. Unset and empty both mean *nobody*, which refuses everyone.

    **Anything that is not an integer is a startup error, never a skipped entry.** This is the one
    boundary the system has: dropping an unparseable id would either lock the real owner out — a
    puzzling silence, since the bot then refuses their own messages — or, in a longer list, quietly
    shrink the set while leaving the deployment looking configured. A typo should stop the daemon
    with the offending text in the message.
    """
    if raw is None:
        return frozenset()
    owners: set[int] = set()
    for entry in raw.replace(",", " ").split():
        try:
            owners.add(int(entry))
        except ValueError as exc:
            raise ValueError(
                f"${TELEGRAM_OWNERS_ENV}: {entry!r} is not a Telegram user id. Expected numeric "
                f"ids separated by commas or spaces"
            ) from exc
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
        help=f"chat mapping (default: <db>{TELEGRAM_CONFIG_SUFFIX})",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(DEFAULT_ENV_FILE),
        help=(
            f"KEY=value file read into the environment if present, for {TELEGRAM_TOKEN_ENV} and "
            f"{TELEGRAM_OWNERS_ENV} (default: {DEFAULT_ENV_FILE}); a real environment variable wins"
        ),
    )
    args = parser.parse_args(argv)

    db_path = args.db or args.kb_root.parent / "pkb.sqlite"
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # Configured before the app is built, and logging is up first so the "no owners" warning and
    # the "no token" notice are not swallowed by an unconfigured root logger (TG-17, Q25).
    try:
        taken = load_env_file(args.env_file)
        if taken:
            # The NAMES only. The whole point of this file is that the values do not get logged.
            _log.info("read %s from %s", ", ".join(sorted(taken)), args.env_file)
        telegram = load_telegram_config(
            db_path,
            path=args.telegram_config,
            token=os.environ.get(TELEGRAM_TOKEN_ENV),
            owners=os.environ.get(TELEGRAM_OWNERS_ENV),
        )
    except ValueError as exc:
        # A clean, path-naming exit rather than a traceback — and never a daemon that serves with a
        # bot the human believes is running.
        parser.error(str(exc))
    uvicorn.run(build_app(args.kb_root, db_path, telegram=telegram), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
