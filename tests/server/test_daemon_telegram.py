"""The composition root's Telegram wiring (TG-14, TG-11, TG-16, TG-17, TG-24, Q25).

**Everything here goes through `build_app`.** That is the whole point of the file: the in-process
run that "verified" this layer drove `TelegramAdapter` directly, and a directly-driven adapter
cannot notice that `build_app` never filled `ServerConfig.telegram_task` — so the bot could not
start through the daemon by any path while every adapter test passed. A test that constructs the
thing under test by hand proves the thing works, never that anything reaches it.

What is faked and what is not:

* **real** — `build_app`, `create_app`, the lifespan, `_start_workers`, `_supervise`, `/health`,
  the config loader, the logging filter. Those are the wiring under test.
* **faked** — `open_service` (a stub service, so no runtime, no model, no SQLite), `HttpBotApi`
  and `TelegramAdapter` (so nothing opens a socket to `api.telegram.org`). The fakes are
  substituted on the modules `_telegram_task` imports *inside* its body, which is where the daemon
  actually resolves them.

Nothing here writes under a `kb_root` (I3, TG-71) and nothing here needs a bot token that is real.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from pkb.daemon import (
    TELEGRAM_CONFIG_SUFFIX,
    TELEGRAM_TOKEN_ENV,
    build_app,
    load_telegram_config,
    main,
    telegram_config_path,
)
from pkb.server.telegram import TelegramConfig
from tests.server.stub import COOKING, LIBRARIAN, StubService

BASE_URL = "http://127.0.0.1:8000"
"""A ``Host`` header with a port — the MCP mount rejects a portless one before any route runs."""

TOKEN = "123456789:AAF-not-a-real-token-000000000000000"
"""Shaped like a real one on purpose: TG-16's redaction keys off ``bot<digits>:<secret>``."""

SECRET = "AAF-not-a-real-token-000000000000000"
"""The half that must never be readable. The numeric bot id is public and stays."""

CHAT = 4242
OTHER_CHAT = 100777
"""A second **private** chat: TG-19 refuses a negative (group/channel) id at load time."""
OWNER = 99001

SOURCE_DIR = Path(__file__).resolve().parents[2] / "src" / "pkb"
TELEGRAM_SOURCES = (
    SOURCE_DIR / "server" / "telegram.py",
    SOURCE_DIR / "server" / "telegram_api.py",
)


# --------------------------------------------------------------------------------------
# § Fakes — everything that would otherwise open a socket or a database
# --------------------------------------------------------------------------------------


@dataclass
class FakeRuntimeConfig:
    """What ``build_app`` reads off the runtime config for ``/health``, and nothing else."""

    durability: str = "sync"
    fanout_limit: int = 3


@dataclass
class FakeBotApi:
    """Stands in for ``HttpBotApi``: an async context manager holding a token and no socket."""

    token: str = ""
    closed: bool = False

    async def __aenter__(self) -> FakeBotApi:
        return self

    async def __aexit__(self, *_: object) -> bool:
        self.closed = True
        return False


@dataclass
class FakeAdapter:
    """Stands in for ``TelegramAdapter``. Records what the closure handed it, then blocks forever.

    Blocking is the honest model of TG-6: the real task never returns while the daemon serves, and
    a fake that returned would leave ``state == "stopped"`` and make ``/health`` degraded for a
    reason that has nothing to do with the wiring under test.
    """

    service: Any = None
    store: Any = None
    api: Any = None
    config: Any = None
    health: Any = None

    async def run(self) -> None:
        STARTED.append(self)
        await _forever()


STARTED: list[FakeAdapter] = []
"""Every adapter the daemon actually constructed and ran, in order."""


async def _forever() -> None:

    await asyncio.Event().wait()


@pytest.fixture(autouse=True)
def wiring(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[FakeAdapter]]:
    """Substitute the three things the closure imports, and clear the record between tests."""
    STARTED.clear()
    monkeypatch.setattr("pkb.server.telegram.TelegramAdapter", FakeAdapter)
    monkeypatch.setattr("pkb.server.telegram_api.HttpBotApi", FakeBotApi)
    yield STARTED
    STARTED.clear()


@pytest.fixture(autouse=True)
def pristine_http_loggers() -> Iterator[None]:
    """Restore the ``httpx``/``httpcore`` loggers after each test.

    ``logging`` is process-global and TG-16's shield is installed by ``build_app``, so without this
    the *order* tests run in decides what a later test observes — which is how a logging assertion
    becomes flaky and then gets deleted.
    """
    loggers = [logging.getLogger("httpx"), logging.getLogger("httpcore")]
    saved = [(logger, list(logger.filters), logger.level) for logger in loggers]
    try:
        yield
    finally:
        for logger, filters, level in saved:
            logger.filters = filters
            logger.setLevel(level)


@pytest.fixture
def service() -> StubService:
    stub = StubService()
    # Layer 3's SQLite connection, which `_telegram_task` refuses to start without. The fake store
    # never touches it, so a sentinel is enough to prove the closure looked for it.
    stub.connection = object()  # type: ignore[attr-defined]
    return stub


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch, service: StubService) -> StubService:
    """``open_service`` over the stub — no runtime, no checkpointer, no model."""

    @contextlib.asynccontextmanager
    async def opener(
        kb_root: Path, db_path: Path, *, config: Any = None
    ) -> AsyncIterator[StubService]:
        yield service

    monkeypatch.setattr("pkb.service.runtime.open_service", opener)
    return service


def app_for(tmp_path: Path, telegram: TelegramConfig | None = None) -> Any:
    return build_app(
        tmp_path / "kb",
        tmp_path / "pkb.sqlite",
        config=FakeRuntimeConfig(),
        telegram=telegram,
    )


def enabled_config(**overrides: Any) -> TelegramConfig:
    settings: dict[str, Any] = {
        "token": TOKEN,
        "chats": {CHAT: COOKING},
        "owner_user_ids": frozenset({OWNER}),
    }
    settings.update(overrides)
    return TelegramConfig(**settings)


def running_adapter(timeout: float = 3.0) -> FakeAdapter:
    """Wait for the supervised task to reach the adapter, or say which half of the wiring failed."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if STARTED:
            return STARTED[0]
        time.sleep(0.01)
    raise AssertionError("the telegram task never ran — ServerConfig.telegram_task is unset")


# --------------------------------------------------------------------------------------
# § The task is actually wired into the app (TG-14)
# --------------------------------------------------------------------------------------


def test_a_configured_bot_starts_through_build_app_and_health_says_running_tg14(
    tmp_path: Path, opened: StubService
) -> None:
    """TG-14's acceptance criterion, literally: config in, ``enabled`` true, ``running`` reached.

    Until this test existed, ``build_app`` accepted a ``TelegramConfig``, defined ``_telegram_task``
    and then built a ``ServerConfig`` without it — so ``_start_workers`` never saw a task and the
    bot was unreachable from the daemon by any path. Every adapter test still passed, because they
    all constructed the adapter themselves. This asserts the *seam*, which is the only thing that
    was broken.
    """
    app = app_for(tmp_path, enabled_config())

    with TestClient(app, base_url=BASE_URL) as client:
        adapter = running_adapter()
        payload = client.get("/health").json()

    assert payload["telegram"]["enabled"] is True
    assert payload["telegram"]["state"] == "running"
    assert payload["status"] == "ok"
    assert adapter.config.token == TOKEN
    assert adapter.api.token == TOKEN


def test_no_configuration_leaves_the_block_disabled_and_the_daemon_ok_tg14(
    tmp_path: Path, opened: StubService
) -> None:
    """The other half of TG-14: a daemon with no bot is a healthy daemon, not a degraded one.

    ``SubsystemState.healthy`` is ``not enabled or running``, so the disabled state has to stay
    genuinely disabled — a subsystem wired in and immediately dead would make ``status`` degraded
    forever on every deployment that never wanted a bot.
    """
    app = app_for(tmp_path, None)

    with TestClient(app, base_url=BASE_URL) as client:
        payload = client.get("/health").json()

    assert payload["telegram"]["enabled"] is False
    assert payload["telegram"]["state"] == "disabled"
    assert payload["status"] == "ok"
    assert STARTED == []


def test_a_token_without_chats_stays_off_rather_than_polling_nowhere_tg14(
    tmp_path: Path, opened: StubService
) -> None:
    """``TelegramConfig.enabled`` is token **and** mapping, and the wiring honours both.

    A token with an empty mapping is a half-finished deployment: every chat is unmapped, so the bot
    can only ever answer TG-2 refusals while holding a live poller against Telegram. Off is the
    honest state, and ``/health`` saying ``disabled`` is what tells the human the mapping is missing.
    """
    app = app_for(
        tmp_path, TelegramConfig(token=TOKEN, chats={}, owner_user_ids=frozenset({OWNER}))
    )

    with TestClient(app, base_url=BASE_URL) as client:
        payload = client.get("/health").json()

    assert payload["telegram"]["state"] == "disabled"
    assert STARTED == []


def test_the_closure_hands_the_adapter_the_live_health_block_tg14(
    tmp_path: Path, opened: StubService
) -> None:
    """TG-14/TG-12: the closure captures the ``HealthState``, or ``last_poll_ok_at`` is never written.

    ``_supervise`` stamps ``running`` **before** awaiting the first line of the task body, so
    ``state`` says nothing about whether Telegram is reachable — a daemon with a revoked token
    reports ``running`` with ``restarts: 0`` for its whole first long poll. ``last_poll_ok_at`` is
    the field that distinguishes them, and it can only be written by an adapter that was handed the
    block. Asserting identity is not enough: this drives the write through to the served payload.
    """
    app = app_for(tmp_path, enabled_config())

    with TestClient(app, base_url=BASE_URL) as client:
        adapter = running_adapter()
        assert adapter.health is app.state.health.telegram
        assert client.get("/health").json()["telegram"]["last_poll_ok_at"] is None

        adapter.health.poll_ok()
        payload = client.get("/health").json()

    assert payload["telegram"]["last_poll_ok_at"] is not None


def test_the_mapping_shape_reaches_health_before_the_task_ever_runs_tg11(tmp_path: Path) -> None:
    """TG-11: ``chats`` and ``agents`` are configuration, so they are set at wiring time.

    No lifespan is entered here, which is the point — the numbers must stay correct while the bot
    is crash-looping, cancelled or not yet started, because that is precisely when a human reads
    ``/health``. ``agents`` is the distinct set, never a count: two chats may address one expert
    (TG-25), and a length comparison would invent an unmapped agent that does not exist.
    """
    app = app_for(
        tmp_path,
        enabled_config(chats={CHAT: COOKING, OTHER_CHAT: COOKING, 7: LIBRARIAN}),
    )

    block = app.state.health.telegram
    assert block.chats == 3
    assert block.agents == frozenset({COOKING, LIBRARIAN})


def test_health_names_the_agents_no_chat_can_reach_tg11(
    tmp_path: Path, opened: StubService
) -> None:
    """TG-3's whole mechanism, end to end: an expert with no chat is *reported*, never guessed at.

    The endpoint computes the set difference, but it can only do so against a mapping something put
    there — so an unwired ``build_app`` makes every agent look reachable while none of them are.
    Creating a topic and then wondering for a week why the bot ignores it is the failure this
    closes, and it is silent by construction: nothing else in the system ever mentions the mapping.
    """
    app = app_for(tmp_path, enabled_config(chats={CHAT: COOKING}))

    with TestClient(app, base_url=BASE_URL) as client:
        payload = client.get("/health").json()

    unmapped = set(payload["telegram"]["unmapped_agents"])
    assert COOKING not in unmapped
    assert LIBRARIAN in unmapped


# --------------------------------------------------------------------------------------
# § Where the configuration comes from (TG-17, TG-24, Q25)
# --------------------------------------------------------------------------------------


def write_config(tmp_path: Path, document: Any, *, name: str | None = None) -> Path:
    path = tmp_path / (name or f"pkb.sqlite{TELEGRAM_CONFIG_SUFFIX}")
    path.write_text(
        document if isinstance(document, str) else json.dumps(document), encoding="utf-8"
    )
    return path


def test_the_config_file_sits_beside_the_database_and_never_in_the_kb_q25(tmp_path: Path) -> None:
    """Q25(c), and Q25(d) refused: deployment configuration is not knowledge-base content.

    A mapping inside ``kb_root`` would be a file the agents themselves may write, so an expert
    could change which agent a chat talks to by filing a note — a privilege escalation dressed up
    as knowledge. It also breaks I3. Beside the database it travels with the deployment instead.
    """
    db = tmp_path / "state" / "pkb.sqlite"
    kb = tmp_path / "kb"

    path = telegram_config_path(db)

    assert path == db.parent / "pkb.sqlite.telegram.json"
    assert kb not in path.parents


def test_a_configured_deployment_loads_chats_and_owners_as_integers_tg17(tmp_path: Path) -> None:
    """JSON object keys are strings; Telegram ids are integers — the coercion happens once, here.

    Every comparison downstream is against ``message.chat.id`` and ``message.from.id``, which
    arrive as JSON numbers. A mapping left keyed by ``"4242"`` matches nothing, and the symptom is
    not an error: it is every message in a correctly configured chat answered as unmapped (TG-2).
    """
    write_config(tmp_path, {"chats": {str(CHAT): COOKING}, "owners": [OWNER, str(OWNER + 1)]})

    config = load_telegram_config(tmp_path / "pkb.sqlite", token=TOKEN)

    assert config is not None
    assert config.chats == {CHAT: COOKING}
    assert config.owner_user_ids == frozenset({OWNER, OWNER + 1})
    assert config.enabled is True


def test_an_explicit_config_path_overrides_the_default_tg17(tmp_path: Path) -> None:
    """``--telegram-config`` exists because a deployment may keep its database somewhere dull."""
    path = write_config(tmp_path, {"chats": {"5": COOKING}, "owners": [OWNER]}, name="bot.json")

    config = load_telegram_config(tmp_path / "pkb.sqlite", path=path, token=TOKEN)

    assert config is not None and config.chats == {5: COOKING}


def test_no_config_file_disables_the_bot_quietly_q25(tmp_path: Path) -> None:
    """The bot is optional; a daemon without one must still serve.

    D9's bot is an addition to the system, not a precondition for it. Raising here would mean every
    existing deployment stops booting the day this code ships, which is the one outcome a new
    optional subsystem may never cause.
    """
    assert load_telegram_config(tmp_path / "pkb.sqlite", token=TOKEN) is None


def test_no_token_disables_the_bot_quietly_and_says_so_once_tg24(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A mapping without ``PKB_TELEGRAM_TOKEN`` is a deployment that has not finished, not a crash.

    It is also the case a human hits constantly — a new shell, a lost export, a service manager
    that does not pass the environment through — so the log line names the variable rather than
    leaving them to guess why a configured bot never answers.
    """
    write_config(tmp_path, {"chats": {str(CHAT): COOKING}, "owners": [OWNER]})
    caplog.set_level(logging.INFO)

    assert load_telegram_config(tmp_path / "pkb.sqlite", token=None) is None
    assert TELEGRAM_TOKEN_ENV in caplog.text


def test_a_malformed_config_is_a_startup_error_naming_the_path_q25(tmp_path: Path) -> None:
    """A broken file is the one case that must not be quiet.

    Ignoring it leaves the human believing the bot is on: they see a daemon running, they message
    it, and nothing happens — with no error anywhere, because the failure was swallowed at startup.
    The path is in the message because "malformed configuration" without it is a treasure hunt
    across a machine with two knowledge bases.
    """
    path = write_config(tmp_path, "{not json at all")

    with pytest.raises(ValueError) as caught:
        load_telegram_config(tmp_path / "pkb.sqlite", token=TOKEN)

    assert str(path) in str(caught.value)


@pytest.mark.parametrize(
    "document",
    [
        {"chats": [COOKING]},
        {"chats": {"not-a-number": COOKING}},
        {"chats": {"4242": 17}},
        {"chats": {"4242": ""}},
        {"chats": {"-1001234567890": COOKING}},
        {"owners": "99001"},
        {"owners": [True]},
        {"owners": [12.5]},
        ["chats", "owners"],
    ],
    ids=[
        "chats-not-an-object",
        "chat-id-not-a-number",
        "agent-id-not-a-string",
        "agent-id-empty",
        "chat-id-is-a-supergroup",
        "owners-not-a-list",
        "owner-is-a-bool",
        "owner-is-a-float",
        "document-not-an-object",
    ],
)
def test_every_unusable_config_shape_names_the_path_too_q25(tmp_path: Path, document: Any) -> None:
    """Unusable *input* is an exception; only content defects are findings (house rule).

    Each of these silently drops something if it is tolerated, and each dropped thing is invisible:
    a dropped chat is answered as unmapped, and a dropped owner is a human whose approvals are all
    refused. Both look like the bot ignoring them, which is indistinguishable from a bug.
    """
    path = write_config(tmp_path, document)

    with pytest.raises(ValueError) as caught:
        load_telegram_config(tmp_path / "pkb.sqlite", token=TOKEN)

    assert str(path) in str(caught.value)


def test_an_explicitly_named_missing_file_is_an_error_not_silence_q25(tmp_path: Path) -> None:
    """A path the human typed is a statement of intent; answering a typo with silence hides it.

    The default path missing means "no bot here" — nobody asserted otherwise. ``--telegram-config
    /wrong/place.json`` means somebody did, and the same reasoning as the malformed case applies:
    a bot that is off while the human believes it is on produces no error anywhere, ever.
    """
    with pytest.raises(ValueError) as caught:
        load_telegram_config(tmp_path / "pkb.sqlite", path=tmp_path / "nope.json", token=TOKEN)

    assert "nope.json" in str(caught.value)


def test_a_token_and_chats_with_no_owners_warns_that_the_bot_is_inert_x(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Decision X: an empty allow-list refuses everyone — the safe default, and an invisible one.

    ``owner_user_ids`` is the system's only authentication boundary, so empty *must* mean refuse;
    the alternative is a knowledge base with no undo answering to anyone who finds the bot's
    username. But the human who forgot the ``owners`` line sees a daemon that starts, a ``/health``
    that says ``running``, and a bot that answers nothing. The warning is the only signal there is.
    """
    write_config(tmp_path, {"chats": {str(CHAT): COOKING}})
    caplog.set_level(logging.WARNING)

    config = load_telegram_config(tmp_path / "pkb.sqlite", token=TOKEN)

    assert config is not None and config.owner_user_ids == frozenset()
    assert config.enabled is True
    assert [r for r in caplog.records if r.levelno == logging.WARNING], caplog.text


def test_the_token_is_read_from_the_environment_only_in_the_daemon_tg24(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TG-24: the composition root reads the secret; nothing under ``pkb/server/`` may.

    Neither telegram module can even import ``os`` without failing the built SV-22 scan, so the
    grep is belt-and-braces — but ``os.environ`` is reachable through other imports, and the rule
    is about *where the secret enters the process*, which is a place, not an import.
    """
    monkeypatch.setenv(TELEGRAM_TOKEN_ENV, TOKEN)
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: None)
    write_config(tmp_path, {"chats": {str(CHAT): COOKING}, "owners": [OWNER]})

    assert main([str(tmp_path / "kb"), "--db", str(tmp_path / "pkb.sqlite")]) == 0

    for source in TELEGRAM_SOURCES:
        text = source.read_text(encoding="utf-8")
        assert "environ" not in text, f"{source.name} reads the environment"
        assert "getenv" not in text, f"{source.name} reads the environment"
        assert TELEGRAM_TOKEN_ENV not in text, f"{source.name} names the token variable"


def test_main_builds_the_app_with_the_mapping_it_loaded_tg17(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``python -m pkb.daemon`` is the only path a real deployment takes, so it is asserted too.

    ``build_app`` gaining the wiring is worth nothing if ``main`` never builds a ``TelegramConfig``:
    the bot would remain unstartable outside a test. This drives argv → environment → file → app.
    """
    built: list[Any] = []
    monkeypatch.setenv(TELEGRAM_TOKEN_ENV, TOKEN)
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: built.append(app))
    write_config(tmp_path, {"chats": {str(CHAT): COOKING, "7": LIBRARIAN}, "owners": [OWNER]})

    assert main([str(tmp_path / "kb"), "--db", str(tmp_path / "pkb.sqlite")]) == 0

    block = built[0].state.health.telegram
    assert block.chats == 2
    assert block.agents == frozenset({COOKING, LIBRARIAN})


def test_main_refuses_to_start_on_a_malformed_config_q25(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The malformed-file error reaches the human as an exit, not as a daemon with a dead bot."""
    started: list[Any] = []
    monkeypatch.setenv(TELEGRAM_TOKEN_ENV, TOKEN)
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: started.append(app))
    path = write_config(tmp_path, "}{")

    with pytest.raises(SystemExit) as caught:
        main([str(tmp_path / "kb"), "--db", str(tmp_path / "pkb.sqlite")])

    assert caught.value.code == 2
    assert started == [], "a daemon started with a bot the human believes is configured"
    assert path.exists()


def test_a_daemon_without_telegram_never_touches_the_config_file_q25(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No token, no file: ``main`` builds an app with the bot disabled and serves anyway."""
    built: list[Any] = []
    monkeypatch.delenv(TELEGRAM_TOKEN_ENV, raising=False)
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: built.append(app))

    assert main([str(tmp_path / "kb"), "--db", str(tmp_path / "pkb.sqlite")]) == 0

    assert built[0].state.health.telegram.state == "disabled"


# --------------------------------------------------------------------------------------
# § The token never reaches the log (TG-16)
# --------------------------------------------------------------------------------------


def httpx_logs(message: str) -> None:
    """Exactly what ``httpx._client`` emits, on exactly the logger it emits it on."""
    logging.getLogger("httpx").info(message)


def test_enabling_the_bot_redacts_the_token_out_of_httpx_request_logs_tg16(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """TG-16: ``httpx`` logs the full request URL at INFO and the token is a path segment of it.

    ``main`` sets ``basicConfig(level=INFO)`` and the bot long-polls every ~30 s, so without a
    filter the credential is appended to the daemon log forever — the one file a human cheerfully
    pastes into a bug report. Measured (P-25): ``HTTP Request: POST
    https://api.telegram.org/bot<TOKEN>/getUpdates "HTTP/1.1 200 OK"``.

    Redaction rather than suppression, so the request line itself survives and the human can still
    see that a poll happened.
    """
    caplog.set_level(logging.INFO)
    app_for(tmp_path, enabled_config())

    httpx_logs(f'HTTP Request: POST https://api.telegram.org/bot{TOKEN}/getUpdates "HTTP/1.1 200"')

    assert SECRET not in caplog.text
    assert TOKEN not in caplog.text
    assert "getUpdates" in caplog.text


def test_the_shield_is_narrow_enough_to_keep_other_request_lines_tg16(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A blanket ``WARNING`` on ``httpx`` would hide the MCP client's requests too.

    The daemon drives httpx for more than the bot, and those lines carry no secret and are how a
    human sees a request was made at all. Filtering on *content* keeps them; raising the level
    throws them away to solve a problem only one of them has.
    """
    caplog.set_level(logging.INFO)
    app_for(tmp_path, enabled_config())

    httpx_logs('HTTP Request: GET http://127.0.0.1:8765/health "HTTP/1.1 200 OK"')

    assert 'HTTP Request: GET http://127.0.0.1:8765/health "HTTP/1.1 200 OK"' in caplog.text


def test_httpcore_debug_tracing_cannot_print_the_token_either_tg16(tmp_path: Path) -> None:
    """``httpcore`` repeats the request target in its DEBUG traces, and a filter cannot reach it.

    It logs through *child* loggers (``httpcore.http11``), and ``Logger.handle`` consults filters
    only on the logger that created the record — a filter on the parent is never asked. So the
    level is clamped instead. It costs nothing at the daemon's INFO default and only bites a human
    who turns on DEBUG, where the alternative is printing the credential.
    """
    logging.getLogger("httpcore").setLevel(logging.NOTSET)

    app_for(tmp_path, enabled_config())

    assert logging.getLogger("httpcore").level >= logging.INFO
    assert not logging.getLogger("httpcore").isEnabledFor(logging.DEBUG)


def test_the_wiring_itself_logs_no_token_tg16(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The daemon's own startup line says how many chats, never which token (TG-16, TG-24)."""
    caplog.set_level(logging.INFO)

    app_for(tmp_path, enabled_config())

    assert TOKEN not in caplog.text
    assert SECRET not in caplog.text
