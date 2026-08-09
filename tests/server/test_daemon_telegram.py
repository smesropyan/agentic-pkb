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
  and `TelegramAdapter` (so nothing opens a socket to `api.telegram.org`), and
  `SqliteTelegramStore` (so the TG-11 seed of the channel directory needs no database). The fakes
  are substituted on the modules `_telegram_task` imports *inside* its body, which is where the
  daemon actually resolves them.

Nothing here writes under a `kb_root` (I3, TG-71) and nothing here needs a bot token that is real.

**Both secrets now come from the environment** (Q25, amended). `PKB_TELEGRAM_TOKEN` always did;
`PKB_TELEGRAM_OWNERS` moved out of the JSON file, because the allow-list is the token's other half
— whoever is on it can approve a write to a tree with no undo (decision X). The file that remains
holds only `{"chats": …}` and names no credential. Every test below that used to put `"owners"` in
that file now sets the variable instead, and the old shape has its own test: it is a *startup
error*, not an ignored key.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from starlette.testclient import TestClient

from pkb.daemon import (
    TELEGRAM_CONFIG_SUFFIX,
    TELEGRAM_OWNERS_ENV,
    TELEGRAM_TOKEN_ENV,
    build_app,
    load_env_file,
    load_telegram_config,
    main,
    telegram_config_path,
)
from pkb.server.telegram import TelegramAdapter as RealTelegramAdapter
from pkb.server.telegram import TelegramConfig
from tests.server.stub import COOKING, GRILLING, LIBRARIAN, StubService

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
SECOND_OWNER = 987654321
"""Obviously fictional, like every id and token here — this repository is public."""

STRANGER = 505050
"""Nobody's user id. Used to prove the allow-list is consulted rather than assumed."""

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


DIRECTORY: set[str] = set()
"""The channel directory's agents, as :class:`FakeStore` reports them (TG-11, TG-77).

Module level and cleared by ``wiring`` for the same reason ``STARTED`` is: the daemon builds the
store inside the supervised closure, so a test can only reach it through what the closure imports.
"""


@dataclass
class FakeStore:
    """``SqliteTelegramStore``'s two startup methods, and nothing else (TG-11).

    The daemon now reads the channel directory *itself*, before the adapter runs, because
    ``unmapped_agents`` has to stay right while the bot is crash-looping — which is exactly when
    somebody reads ``/health``. So the sentinel connection is no longer enough on its own: something
    has to answer ``setup()`` and ``channel_agents()`` without a real SQLite file behind it.
    """

    connection: Any
    setups: int = 0

    async def setup(self) -> None:
        self.setups += 1

    async def channel_agents(self) -> frozenset[str]:
        return frozenset(DIRECTORY)


async def _forever() -> None:

    await asyncio.Event().wait()


@pytest.fixture(autouse=True)
def wiring(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[FakeAdapter]]:
    """Substitute the things the closure imports, and clear the record between tests."""
    STARTED.clear()
    DIRECTORY.clear()
    monkeypatch.setattr("pkb.server.telegram.TelegramAdapter", FakeAdapter)
    monkeypatch.setattr("pkb.server.telegram_api.HttpBotApi", FakeBotApi)
    monkeypatch.setattr("pkb.service.telegram.SqliteTelegramStore", FakeStore)
    yield STARTED
    STARTED.clear()
    DIRECTORY.clear()


@pytest.fixture(autouse=True)
def isolated_telegram_env() -> Iterator[None]:
    """Neither secret survives a test, and neither is inherited from the developer's shell (Q25).

    ``main`` folds an env file into the **real** ``os.environ``, and ``monkeypatch.delenv`` records
    no undo for a name that was not set — so a test whose env file supplies
    ``PKB_TELEGRAM_OWNERS`` would leave it set for every test after it, and the suite would pass or
    fail on ordering. Clearing on the way in matters just as much: a machine that really runs this
    bot exports both variables, and without this the token on that laptop decides whether the bot
    in a test is enabled.

    Autouse, so it is set up first and therefore torn down **after** ``monkeypatch`` undoes its own
    ``setenv`` — the saved values are the ones the process actually started with.
    """
    saved = {name: os.environ.get(name) for name in (TELEGRAM_TOKEN_ENV, TELEGRAM_OWNERS_ENV)}
    for name in saved:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


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
    # Layer 3's SQLite connection, which `_telegram_task` refuses to start without. Neither the
    # fake store nor the fake adapter touches it, so a sentinel is enough to prove the closure
    # looked for it.
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


def daemon_argv(tmp_path: Path, *extra: str, env_file: Path | None = None) -> list[str]:
    """Argv for ``main``, never able to read the checkout's own ``.env`` (Q25).

    ``--env-file`` defaults to ``.env`` relative to the working directory, which under pytest is
    the repository root — where a developer running this bot keeps a real token. Passing a path
    explicitly on every call, defaulting to one that does not exist, is what makes these tests say
    the same thing on every machine.
    """
    return [
        str(tmp_path / "kb"),
        "--db",
        str(tmp_path / "pkb.sqlite"),
        "--env-file",
        str(env_file or tmp_path / "absent.env"),
        *extra,
    ]


def write_env(tmp_path: Path, text: str, *, name: str = ".env", mode: int = 0o600) -> Path:
    """An env file, owner-readable by default so only the test that wants the warning gets it."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


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


def test_health_counts_an_agent_the_channel_directory_reaches_as_mapped_tg11(
    tmp_path: Path, opened: StubService
) -> None:
    """Half the answer is now a table the human filled with ``/channels``, not the config file.

    Since topics, ``chats`` names only each chat's General (TG-73); every other channel is a row in
    ``pkb_telegram_channels``, because a topic id is minted by Telegram, invisible in every client
    and unenumerable afterwards — the directory is the only record that ``topic/cooking/grilling``
    is reachable at all. Without the seed, an agent the human gave a topic to yesterday is listed as
    unreachable today, which reads as the bot having lost it and invites a second ``/channels``.

    Seeded **here**, in the composition root, rather than in the adapter: that is what keeps TG-11's
    stated property true, since the answer has to survive a bot that is crash-looping on a revoked
    token.
    """
    DIRECTORY.add(GRILLING)
    app = app_for(tmp_path, enabled_config(chats={CHAT: LIBRARIAN}))

    with TestClient(app, base_url=BASE_URL) as client:
        running_adapter()  # the seed happens in the closure, before the adapter is constructed
        payload = client.get("/health").json()

    unmapped = set(payload["telegram"]["unmapped_agents"])
    assert GRILLING not in unmapped
    assert LIBRARIAN not in unmapped
    assert COOKING in unmapped


def test_a_general_area_that_is_not_the_librarian_is_warned_about_at_startup_tg73(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """General is the one channel whose title names no agent, so it is the one that must be said.

    Every topic carries its expert's title above the keyboard; General carries the word *"General"*.
    So it is the single place TG-1's "which expert am I talking to?" ambiguity survives topics, and
    a human whose General quietly answers as Cooking finds out by having a note filed somewhere they
    did not choose — into a tree with no undo (D6).

    A warning and not a refusal (decision Z): every deployment that exists today maps its one chat
    to whatever agent it wanted on the phone, frequently one expert, and refusing to start would
    break all of them at upgrade for a stylistic gain. The Librarian chat is asserted silent because
    a warning that fires for the correct configuration is one nobody reads.
    """
    with caplog.at_level(logging.WARNING, logger="pkb.daemon"):
        app_for(tmp_path, enabled_config(chats={CHAT: COOKING, OTHER_CHAT: LIBRARIAN}))

    warnings = [
        record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING
    ]
    assert sum(COOKING in message for message in warnings) == 1
    assert not any(str(OTHER_CHAT) in message for message in warnings)


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

    The owner ids arrive from ``PKB_TELEGRAM_OWNERS`` rather than from the file now (Q25, amended),
    and they need the same coercion for the same reason on the ``message.from.id`` side.
    """
    write_config(tmp_path, {"chats": {str(CHAT): COOKING}})

    config = load_telegram_config(
        tmp_path / "pkb.sqlite", token=TOKEN, owners=f"{OWNER},{SECOND_OWNER}"
    )

    assert config is not None
    assert config.chats == {CHAT: COOKING}
    assert config.owner_user_ids == frozenset({OWNER, SECOND_OWNER})
    assert config.enabled is True


def test_an_explicit_config_path_overrides_the_default_tg17(tmp_path: Path) -> None:
    """``--telegram-config`` exists because a deployment may keep its database somewhere dull."""
    path = write_config(tmp_path, {"chats": {"5": COOKING}}, name="bot.json")

    config = load_telegram_config(
        tmp_path / "pkb.sqlite", path=path, token=TOKEN, owners=str(OWNER)
    )

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
    write_config(tmp_path, {"chats": {str(CHAT): COOKING}})
    caplog.set_level(logging.INFO)

    assert load_telegram_config(tmp_path / "pkb.sqlite", token=None, owners=str(OWNER)) is None
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
        ["chats", "owners"],
    ],
    ids=[
        "chats-not-an-object",
        "chat-id-not-a-number",
        "agent-id-not-a-string",
        "agent-id-empty",
        "chat-id-is-a-supergroup",
        "document-not-an-object",
    ],
)
def test_every_unusable_config_shape_names_the_path_too_q25(tmp_path: Path, document: Any) -> None:
    """Unusable *input* is an exception; only content defects are findings (house rule).

    Each of these silently drops something if it is tolerated, and each dropped thing is invisible:
    a dropped chat is answered as unmapped, which looks exactly like the bot ignoring the human.

    The three malformed-``owners`` cases this list used to carry are gone: the key no longer belongs
    in this file at all, so its *shape* is no longer the question. Any file carrying it is refused
    outright by the migration test below, and a malformed allow-list is now a
    ``PKB_TELEGRAM_OWNERS`` error instead.
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
    """TG-24: the composition root reads the secrets; nothing under ``pkb/server/`` may.

    Neither telegram module can even import ``os`` without failing the built SV-22 scan, so the
    grep is belt-and-braces — but ``os.environ`` is reachable through other imports, and the rule
    is about *where the secret enters the process*, which is a place, not an import.

    ``PKB_TELEGRAM_OWNERS`` is checked here too (Q25, amended). A second environment variable is a
    second chance to add a second reader, and the obvious wrong fix for "the adapter needs the
    allow-list" is a ``getenv`` in the adapter — which would make the deployment's authentication
    boundary configurable from two places that can disagree.
    """
    monkeypatch.setenv(TELEGRAM_TOKEN_ENV, TOKEN)
    monkeypatch.setenv(TELEGRAM_OWNERS_ENV, str(OWNER))
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: None)
    write_config(tmp_path, {"chats": {str(CHAT): COOKING}})

    assert main(daemon_argv(tmp_path)) == 0

    for source in TELEGRAM_SOURCES:
        text = source.read_text(encoding="utf-8")
        assert "environ" not in text, f"{source.name} reads the environment"
        assert "getenv" not in text, f"{source.name} reads the environment"
        assert TELEGRAM_TOKEN_ENV not in text, f"{source.name} names the token variable"
        assert TELEGRAM_OWNERS_ENV not in text, f"{source.name} names the owners variable"


def test_main_builds_the_app_with_the_mapping_it_loaded_tg17(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``python -m pkb.daemon`` is the only path a real deployment takes, so it is asserted too.

    ``build_app`` gaining the wiring is worth nothing if ``main`` never builds a ``TelegramConfig``:
    the bot would remain unstartable outside a test. This drives argv → environment → file → app.
    """
    built: list[Any] = []
    monkeypatch.setenv(TELEGRAM_TOKEN_ENV, TOKEN)
    monkeypatch.setenv(TELEGRAM_OWNERS_ENV, str(OWNER))
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: built.append(app))
    write_config(tmp_path, {"chats": {str(CHAT): COOKING, "7": LIBRARIAN}})

    assert main(daemon_argv(tmp_path)) == 0

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
        main(daemon_argv(tmp_path))

    assert caught.value.code == 2
    assert started == [], "a daemon started with a bot the human believes is configured"
    assert path.exists()


def test_a_daemon_without_telegram_never_touches_the_config_file_q25(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No token, no file: ``main`` builds an app with the bot disabled and serves anyway."""
    built: list[Any] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: built.append(app))

    assert main(daemon_argv(tmp_path)) == 0

    assert built[0].state.health.telegram.state == "disabled"


# --------------------------------------------------------------------------------------
# § The allow-list is an environment variable (TG-20, decision X, Q25 amended)
# --------------------------------------------------------------------------------------


async def admits(config: TelegramConfig, *, sender: int, chat_id: int = CHAT) -> str | None:
    """Put one private message through the **real** adapter's admission check (TG-19, TG-20).

    ``RealTelegramAdapter`` is bound at import time, before the ``wiring`` fixture replaces the
    module attribute, so this is the shipped check rather than a restatement of it in the test.
    Nothing here opens a socket: the check reads the config and nothing else, which is why the three
    collaborators can be ``None``.

    Who is admitted and where a message goes were one method (``_admit``) and are now two —
    ``_sender_ok`` and ``_route`` (TG-72, TG-95): a channel changed what a message is addressed
    *to*, and deliberately not who may say yes. This helper drives the first, and answers with the
    chat's General agent when the sender passes, so the assertions below read as they always did.
    """
    bot = RealTelegramAdapter(
        service=cast(Any, None), store=cast(Any, None), api=cast(Any, None), config=config
    )
    message = {"chat": {"id": chat_id, "type": "private"}, "from": {"id": sender}}
    return config.chats.get(chat_id) if bot._sender_ok(message) else None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (f"{OWNER}", {OWNER}),
        (f"{OWNER},{SECOND_OWNER}", {OWNER, SECOND_OWNER}),
        (f"{OWNER} {SECOND_OWNER}", {OWNER, SECOND_OWNER}),
        (f"{OWNER}, {SECOND_OWNER}", {OWNER, SECOND_OWNER}),
        (f"  {OWNER} ,,  {SECOND_OWNER}  ", {OWNER, SECOND_OWNER}),
        (f"{OWNER},{OWNER}", {OWNER}),
    ],
    ids=["one", "commas", "spaces", "both", "ragged", "repeated"],
)
def test_the_owner_allow_list_accepts_commas_spaces_or_both_tg20(
    tmp_path: Path, raw: str, expected: set[int]
) -> None:
    """One separator would be a rule the human has to remember, and getting it wrong is silent.

    ``PKB_TELEGRAM_OWNERS`` is typed by hand into a ``.env`` beside a token, and the natural forms
    are ``a,b``, ``a b`` and ``a, b``. A parser that split on only one of them would turn the other
    two into a single unparseable entry — and before this variable existed that meant an allow-list
    of nobody, which refuses the very human who wrote it (decision X) with no error anywhere.
    Ragged whitespace and a repeated id are the same case: harmless, so they must not be fatal.
    """
    write_config(tmp_path, {"chats": {str(CHAT): COOKING}})

    config = load_telegram_config(tmp_path / "pkb.sqlite", token=TOKEN, owners=raw)

    assert config is not None
    assert config.owner_user_ids == frozenset(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw", [None, "", "   ", " , , "], ids=["unset", "empty", "blank", "commas"]
)
async def test_an_unset_owner_list_refuses_every_sender_rather_than_admitting_one_x(
    tmp_path: Path, raw: str | None
) -> None:
    """Decision X: empty means *nobody*, and the proof is a refusal, not an empty set.

    An empty ``frozenset`` is only the safe default if the code downstream reads it as "refuse".
    The dangerous shapes are all one edit away and all pass a set-equality assertion: an ``if
    owner_user_ids:`` guard that skips the check when the list is empty, or a truthiness fallback
    that treats "unconfigured" as "unrestricted". Either one turns a misspelled variable into a
    knowledge base with no undo that answers to anyone who finds the bot's username.

    The control matters as much as the refusal: the same chat, the same sender, admitted the moment
    the variable names them. Without it this test would also pass against an adapter that refuses
    everybody always.
    """
    write_config(tmp_path, {"chats": {str(CHAT): COOKING}})

    inert = load_telegram_config(tmp_path / "pkb.sqlite", token=TOKEN, owners=raw)
    configured = load_telegram_config(tmp_path / "pkb.sqlite", token=TOKEN, owners=str(OWNER))

    assert inert is not None and configured is not None
    assert inert.owner_user_ids == frozenset()
    assert await admits(inert, sender=OWNER) is None
    assert await admits(inert, sender=STRANGER) is None
    assert await admits(configured, sender=OWNER) == COOKING


@pytest.mark.parametrize(
    ("raw", "offender"),
    [
        ("sergiy", "sergiy"),
        (f"{OWNER},@sergiy", "@sergiy"),
        (f"{OWNER}, {SECOND_OWNER}, 12.5", "12.5"),
        (f"{OWNER} 0x1f", "0x1f"),
        ("\ufeff99001", "\\ufeff99001"),
    ],
    ids=["a-username", "an-at-handle", "a-float", "hex", "a-byte-order-mark"],
)
def test_a_non_numeric_owner_is_a_startup_error_naming_the_text_tg20(
    tmp_path: Path, raw: str, offender: str
) -> None:
    """A dropped id is worse than a refused deployment, in both directions (TG-20, decision X).

    Skip the bad entry in a one-name list and the daemon starts with an allow-list of nobody: the
    human's own messages are then ignored *silently*, because TG-20's refusal is silence, and there
    is nothing to read anywhere. Skip it in a three-name list and the set quietly shrinks while
    ``/health`` still says ``running`` and the ``.env`` still visibly names three people — the
    deployment looks configured and two of them have simply stopped being able to answer.

    The offending text is in the message because the whole class of cause is a typo, a username
    where an id belongs, or a stray character the shell put there; "not a Telegram user id" without
    it sends the human to compare five numbers by eye. It is quoted with ``!r``, which is what the
    byte-order-mark case is here for: the entry an editor pasted looks *identical* to a correct one
    on screen, and only the escaped form (``\\ufeff99001``) tells the human what is actually wrong.
    """
    write_config(tmp_path, {"chats": {str(CHAT): COOKING}})

    with pytest.raises(ValueError) as caught:
        load_telegram_config(tmp_path / "pkb.sqlite", token=TOKEN, owners=raw)

    assert offender in str(caught.value)
    assert TELEGRAM_OWNERS_ENV in str(caught.value)


def test_a_config_file_that_still_lists_owners_is_refused_and_names_the_variable_q25(
    tmp_path: Path,
) -> None:
    """The migration trap, and the reason it is an error rather than a tolerated leftover.

    Q25 originally put the allow-list in this file. Every deployment that predates the move has one
    there, and the file survives the upgrade untouched — so the failure mode is a human reading
    ``{"owners": [a, b, c]}`` in their own configuration and concluding that a, b and c are
    authorized, while the code that reads it is gone and the real allow-list is empty. That is the
    exact inversion of decision X: an allow-list nothing reads looks identical to one in force.

    Refused even when ``PKB_TELEGRAM_OWNERS`` is set and *agrees*, which is the subtle case: the
    two lists can drift apart later, and a file the loader tolerates today is one somebody edits
    tomorrow expecting it to take effect.
    """
    path = write_config(tmp_path, {"chats": {str(CHAT): COOKING}, "owners": [OWNER]})

    with pytest.raises(ValueError) as caught:
        load_telegram_config(tmp_path / "pkb.sqlite", token=TOKEN, owners=str(OWNER))

    message = str(caught.value)
    assert TELEGRAM_OWNERS_ENV in message
    assert str(path) in message
    assert "owners" in message


def test_main_refuses_to_start_on_a_config_that_still_lists_owners_q25(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The migration error has to reach the human as an exit, or it is not a migration error.

    A ``ValueError`` raised inside a loader that ``main`` swallowed would leave the daemon serving
    with an empty allow-list and a configuration file that says otherwise — which is precisely the
    state the refusal exists to make impossible. Nothing may have been started.
    """
    started: list[Any] = []
    monkeypatch.setenv(TELEGRAM_TOKEN_ENV, TOKEN)
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: started.append(app))
    write_config(tmp_path, {"chats": {str(CHAT): COOKING}, "owners": [OWNER]})

    with pytest.raises(SystemExit) as caught:
        main(daemon_argv(tmp_path))

    assert caught.value.code == 2
    assert started == [], "a daemon started with an allow-list its config file contradicts"


# --------------------------------------------------------------------------------------
# § The env file (Q25 amended, DEFAULT_ENV_FILE)
# --------------------------------------------------------------------------------------


def test_a_real_environment_variable_always_beats_the_env_file_q25(tmp_path: Path) -> None:
    """The one rule that decides whether this loader is debuggable (Q25, ``load_env_file``).

    The environment is the deployment's own voice — a systemd ``Environment=``, a container secret,
    a one-off ``PKB_TELEGRAM_TOKEN=… python -m pkb.daemon``. A file that silently won over it would
    produce a bot running on last month's token while ``env | grep PKB`` shows the new one, and no
    amount of staring at the shell would explain it.

    The return value is the same rule stated as data: it names only what the file *supplied*, so
    the startup log line cannot claim credit for a variable it did not set.
    """
    environ = {TELEGRAM_TOKEN_ENV: TOKEN}
    path = write_env(tmp_path, f"{TELEGRAM_TOKEN_ENV}=stale-token\n{TELEGRAM_OWNERS_ENV}={OWNER}\n")

    taken = load_env_file(path, environ)

    assert environ[TELEGRAM_TOKEN_ENV] == TOKEN
    assert environ[TELEGRAM_OWNERS_ENV] == str(OWNER)
    assert taken == [TELEGRAM_OWNERS_ENV]


def test_the_env_file_tolerates_comments_blanks_export_and_quoting_q25(tmp_path: Path) -> None:
    """Everything a human copies out of a shell or a README, taken as written.

    ``.env.example`` is a commented template the human edits in place, and the natural edits are to
    leave the comments, keep the blank lines, paste an ``export`` line straight from a shell, and
    quote a value that has punctuation in it. Rejecting any of those would make the file's own
    documentation unparseable — and the failure would land at daemon startup, where the human is
    least equipped to see that a ``#`` was the problem.
    """
    path = write_env(
        tmp_path,
        "# both halves of the deployment's security live here\n"
        "\n"
        f"export {TELEGRAM_TOKEN_ENV} = '{TOKEN}'\n"
        "   \n"
        f'  {TELEGRAM_OWNERS_ENV}="{OWNER}, {SECOND_OWNER}"   \n'
        "# trailing comment\n",
    )
    environ: dict[str, str] = {}

    taken = load_env_file(path, environ)

    assert environ[TELEGRAM_TOKEN_ENV] == TOKEN
    assert environ[TELEGRAM_OWNERS_ENV] == f"{OWNER}, {SECOND_OWNER}"
    assert taken == [TELEGRAM_TOKEN_ENV, TELEGRAM_OWNERS_ENV]


def test_env_file_values_are_literal_and_nothing_is_interpolated_q25(tmp_path: Path) -> None:
    """A parser clever enough to expand ``$`` is one that will eat part of a credential.

    A bot token is ``<digits>:<base64-ish>`` — colons, underscores, hyphens — and BotFather's
    alphabet is not fixed by anything this code controls. Splitting on the *first* ``=`` and taking
    the rest verbatim is what makes that safe. Interpolation is the specific danger: a ``$`` inside
    a secret would be replaced by an empty string, producing a token that is *almost* right, and
    the only symptom is a 401 the human reads as "the token was revoked".
    """
    literal = "123456789:AA-fake$HOME-token_with-punctuation=and=equals"
    path = write_env(tmp_path, f"{TELEGRAM_TOKEN_ENV}={literal}\n")
    environ: dict[str, str] = {}

    load_env_file(path, environ)

    assert environ[TELEGRAM_TOKEN_ENV] == literal


def test_a_missing_env_file_is_nothing_at_all_rather_than_an_error_q25(tmp_path: Path) -> None:
    """``--env-file`` has a default, so the common deployment never has this file at all.

    Systemd units, containers and a plain ``export`` in a shell are all first-class ways to run
    this daemon, and none of them writes a ``.env``. Raising on the default path would make the
    file mandatory by accident and break every one of them.
    """
    environ: dict[str, str] = {}

    assert load_env_file(tmp_path / "nothing-here.env", environ) == []
    assert environ == {}


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("PKB_TELEGRAM_TOKEN=fine\nPKB_TELEGRAM_OWNERS 99001\n", 2),
        ("PKB_TELEGRAM_TOKEN=fine\n\n\n=orphaned-value\n", 4),
        ("just-a-name\n", 1),
    ],
    ids=["a-space-instead-of-equals", "no-name", "first-line"],
)
def test_a_malformed_env_line_is_an_error_naming_the_line_number_q25(
    tmp_path: Path, text: str, number: int
) -> None:
    """Skipping the line is how a token goes missing without anybody being told (Q25).

    A file whose bad line is skipped loads *partially*: the daemon starts, ``/health`` says the bot
    is disabled or the allow-list is empty, and the human is looking at a file that plainly
    contains the value. The line number is what turns that into a five-second fix, and it is the
    only locator available — the file has no other structure, and the content must not be echoed
    because the malformed line may be a mangled secret.
    """
    path = write_env(tmp_path, text)

    with pytest.raises(ValueError) as caught:
        load_env_file(path, {})

    assert f"{path}:{number}" in str(caught.value)


def test_a_world_readable_env_file_warns_but_still_loads_q25(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A permission bit is worth a sentence, never a refusal to start (``_warn_if_widely_readable``).

    This file holds the token *and* the allow-list, which together are unattended write access to a
    knowledge base with no undo — so mode 644 on a shared machine is a real finding and the human
    should be told, with the fix in the line. But refusing to boot over it would strand somebody on
    a single-user box where it does not matter, and the pressure that creates is to move the secret
    somewhere the check cannot see it, which is worse than the permission bit.
    """
    path = write_env(tmp_path, f"{TELEGRAM_OWNERS_ENV}={OWNER}\n", mode=0o644)
    caplog.set_level(logging.WARNING)
    environ: dict[str, str] = {}

    assert load_env_file(path, environ) == [TELEGRAM_OWNERS_ENV]

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "a world-readable secrets file passed without a word"
    assert str(path) in " ".join(warnings)
    assert "chmod" in " ".join(warnings)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(0o600, False), (0o640, True), (0o604, True)],
)
def test_the_permission_warning_fires_only_when_somebody_else_can_read_it_q25(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, mode: int, expected: bool
) -> None:
    """The previous test proves the warning appears; this proves it discriminates.

    Without the 0o600 case a `_warn_if_widely_readable` that warned unconditionally would pass every
    other test in this file, and a warning printed at every start of a correctly-permissioned
    deployment is one the human learns to scroll past — which costs nothing until the day the file
    really is world-readable. The group and other bits are checked separately because
    `chmod 640` on a shared box hands the token to everyone in the group, and a check written as
    `mode & 0o007` would call that fine.
    """
    path = write_env(tmp_path, f"{TELEGRAM_OWNERS_ENV}={OWNER}\n", mode=mode)
    caplog.set_level(logging.WARNING)
    environ: dict[str, str] = {}

    assert load_env_file(path, environ) == [TELEGRAM_OWNERS_ENV]

    warned = any("chmod" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert warned is expected, f"mode {mode:o} should {'warn' if expected else 'be silent'}"


# --------------------------------------------------------------------------------------
# § `main` reads both secrets out of `--env-file` (Q25 amended, TG-16)
# --------------------------------------------------------------------------------------


def test_main_takes_both_secrets_from_the_env_file_and_enables_the_bot_q25(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, opened: StubService
) -> None:
    """argv → env file → environment → config → app → running bot, in one pass (Q25, TG-14).

    This is the path a real deployment takes and the only one that proves the pieces meet: a
    ``load_env_file`` that works in isolation is worth nothing if ``main`` reads the file after it
    has already built the ``TelegramConfig``, or passes the wrong variable to ``owners``. Both
    mistakes leave every unit test above green and the bot refusing its owner.

    The allow-list is asserted on the config the adapter was actually handed rather than on
    ``/health``, because ``/health`` deliberately publishes the mapping's shape and **not** the
    owners (TG-11, TG-21) — the endpoint is unauthenticated.
    """
    built: list[Any] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: built.append(app))
    env_file = write_env(
        tmp_path,
        f"{TELEGRAM_TOKEN_ENV}={TOKEN}\n{TELEGRAM_OWNERS_ENV}={OWNER}, {SECOND_OWNER}\n",
    )
    write_config(tmp_path, {"chats": {str(CHAT): COOKING}})

    assert main(daemon_argv(tmp_path, env_file=env_file)) == 0

    with TestClient(built[0], base_url=BASE_URL) as client:
        adapter = running_adapter()
        payload = client.get("/health").json()

    assert payload["telegram"]["enabled"] is True
    assert payload["telegram"]["state"] == "running"
    assert adapter.config.token == TOKEN
    assert adapter.config.owner_user_ids == frozenset({OWNER, SECOND_OWNER})


def test_main_logs_the_names_it_read_and_never_the_values_q25(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The whole point of the file is that its contents do not end up in the log (TG-16, Q25).

    ``main`` sets ``basicConfig(level=INFO)``, so this line goes wherever the daemon's output goes
    — a journal, a scrollback, a bug report. Naming the variables is genuinely useful: "which of
    these two did the file actually supply, and which came from my shell" is the first question
    when the bot will not start. Printing the values would put a live bot token in the same place,
    and TG-16 spent a whole filter keeping it out of the httpx logs.

    The allow-list is checked too. A user id is not a credential, but it identifies a real person
    in a file the human is about to paste into a bug report.
    """
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: None)
    env_file = write_env(
        tmp_path, f"{TELEGRAM_TOKEN_ENV}={TOKEN}\n{TELEGRAM_OWNERS_ENV}={SECOND_OWNER}\n"
    )
    write_config(tmp_path, {"chats": {str(CHAT): COOKING}})
    caplog.set_level(logging.INFO)

    assert main(daemon_argv(tmp_path, env_file=env_file)) == 0

    assert TELEGRAM_TOKEN_ENV in caplog.text
    assert TELEGRAM_OWNERS_ENV in caplog.text
    assert TOKEN not in caplog.text
    assert SECRET not in caplog.text
    assert str(SECOND_OWNER) not in caplog.text


def test_main_starts_on_a_world_readable_env_file_after_saying_so_q25(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Mode 644 warns; it does not cost the human their daemon (``_warn_if_widely_readable``).

    Asserted through ``main`` rather than through the loader alone because the decision that
    matters is the one the process makes: a warning that turned out to abort startup would take
    the HTTP API down with the bot, and D9's bot may never do that.
    """
    built: list[Any] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: built.append(app))
    env_file = write_env(
        tmp_path,
        f"{TELEGRAM_TOKEN_ENV}={TOKEN}\n{TELEGRAM_OWNERS_ENV}={OWNER}\n",
        mode=0o644,
    )
    write_config(tmp_path, {"chats": {str(CHAT): COOKING}})
    caplog.set_level(logging.WARNING)

    assert main(daemon_argv(tmp_path, env_file=env_file)) == 0

    assert built[0].state.health.telegram.chats == 1
    assert [r for r in caplog.records if r.levelno == logging.WARNING], caplog.text


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


# --------------------------------------------------------------------------------------
# § The repository's own half of the bargain: the default path, the ignore rules, the template
# --------------------------------------------------------------------------------------


def test_the_env_file_flag_defaults_to_dot_env_in_the_working_directory_q25(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, opened: StubService
) -> None:
    """``cp .env.example .env`` && ``python -m pkb.daemon <kb>`` has to be the whole setup (Q25).

    Every other test in this file passes ``--env-file`` explicitly, on purpose — the default
    resolves against the working directory, which under pytest is the repository root. That
    discipline leaves the default itself completely unpinned: renaming it, or resolving it against
    ``kb_root`` or the database's directory instead of the cwd, breaks the documented setup and
    fails nothing. The symptom would be the quietest one this subsystem has — no token, so no bot,
    and one INFO line about a file the human is looking straight at.

    ``DEFAULT_ENV_FILE`` is not asserted as a string here: the claim is about where ``main``
    actually reads, which is the only part a human can get wrong.
    """
    built: list[Any] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: built.append(app))
    monkeypatch.chdir(tmp_path)
    write_env(tmp_path, f"{TELEGRAM_TOKEN_ENV}={TOKEN}\n{TELEGRAM_OWNERS_ENV}={OWNER}\n")
    write_config(tmp_path, {"chats": {str(CHAT): COOKING}})

    assert main([str(tmp_path / "kb"), "--db", str(tmp_path / "pkb.sqlite")]) == 0

    with TestClient(built[0], base_url=BASE_URL):
        adapter = running_adapter()

    assert adapter.config.token == TOKEN
    assert adapter.config.owner_user_ids == frozenset({OWNER})


@pytest.mark.parametrize(
    ("name", "ignored"),
    [(".env", True), (".env.production", True), (".env.example", False)],
    ids=["the-secrets-file", "a-variant", "the-template"],
)
def test_git_ignores_the_secrets_file_and_not_the_template_q25(name: str, ignored: bool) -> None:
    """The third half of this change, and the only one no Python can enforce (Q25, decision X).

    Moving both secrets into ``.env`` is a *worse* deployment than leaving them in the JSON file if
    ``.env`` is committable: the token and the allow-list are now in one file, and the repository is
    public. The exception is where it gets subtle — ``!.env.example`` has to come after ``.env.*``
    or the template is ignored too, and an ignored template is one nobody ever receives, which
    sends the next person straight back to inventing their own variable names.

    Asserted through ``git check-ignore`` rather than by reading ``.gitignore`` as text, because the
    ordering of negation patterns is exactly the part a text assertion would get wrong in the same
    direction the file did.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / ".git").exists() or shutil.which("git") is None:
        pytest.skip("not a git checkout")

    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", name], cwd=repo, capture_output=True
    )

    assert (result.returncode == 0) is ignored, f"git check-ignore {name} -> {result.returncode}"


def test_the_committed_template_supplies_exactly_the_two_variables_the_loader_reads_q25() -> None:
    """``.env.example`` is documentation that can rot, so it is parsed by the shipped parser (Q25).

    It is the one file in this change a human copies rather than reads, so a template that names a
    variable the loader does not read — a rename, a typo, a third variable somebody added and never
    wired — produces a ``.env`` that looks complete and a bot that stays off with a single INFO
    line. Running :func:`load_env_file` over it proves the same code path that reads the real file
    accepts this one, and that what it supplies is exactly the two names the daemon asks the
    environment for.
    """
    template = Path(__file__).resolve().parents[2] / ".env.example"
    environ: dict[str, str] = {}

    assert template.is_file(), "the template the guide tells people to copy is missing"
    assert load_env_file(template, environ) == [TELEGRAM_TOKEN_ENV, TELEGRAM_OWNERS_ENV]
    assert set(environ) == {TELEGRAM_TOKEN_ENV, TELEGRAM_OWNERS_ENV}


def test_the_committed_template_holds_placeholders_that_say_so_q25() -> None:
    """``!.env.example`` is a hole in the ignore rule, and this is what stops a token going through.

    The template sits one keystroke from the real file, in an editor, in a public repository, with
    ``.gitignore`` explicitly exempting it — so the accident is not hypothetical: fill it in, forget
    it is the template rather than the copy, commit. A live token is unrecoverable once pushed, and
    the redaction TG-16 spends a log filter on protects the daemon's own output, not this file.

    A committed value therefore has to *announce* that it is not real. That is checkable where "is
    this a real token" is not: BotFather's alphabet is not fixed by anything this code controls, so
    a shape test would pass a real token and fail a good placeholder.
    """
    template = Path(__file__).resolve().parents[2] / ".env.example"
    environ: dict[str, str] = {}
    load_env_file(template, environ)

    marked = ("replace", "example", "fake", "your")
    token = environ[TELEGRAM_TOKEN_ENV].lower()
    assert any(word in token for word in marked), f"{token!r} does not announce itself as a sample"
    assert environ[TELEGRAM_OWNERS_ENV] == str(SECOND_OWNER), "use the file's own fictional id"
