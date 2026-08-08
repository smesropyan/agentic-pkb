"""The supervised task's shape — TG-5 … TG-10, over the **real** ``_supervise``.

A separate file from ``test_daemon_telegram.py`` on purpose: that one substitutes a fake
``TelegramAdapter`` for every test in it, which is right for asserting that the composition root
reaches the slot and fatal for asserting anything about the real task's own concurrency. What is
under test here is the contract ``_supervise`` places on whatever it is given — *"it must own
everything it spawns"* — and the only way to check that is to give it the real thing.

Measured, and the reason every rule below exists: ``_supervise`` restarts the callable on any
exception, carries **nothing** across, and cancels nothing the previous invocation started. Three
generations of a task that detached one child left three live pollers, with ``/health`` reading
``running`` for most of it. Against the real API that is three concurrent ``getUpdates`` on one
token, which Telegram answers with ``409 Conflict`` for two of them.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import time
from pathlib import Path
from typing import Any

import pytest

SOURCE_DIR = Path(__file__).resolve().parents[2] / "src" / "pkb"

# --------------------------------------------------------------------------------------

ADAPTER_SOURCE = SOURCE_DIR / "server" / "telegram.py"
API_SOURCE = SOURCE_DIR / "server" / "telegram_api.py"


def _function(source: Path, qualified: str) -> ast.AsyncFunctionDef:
    """One method, by ``Class.method``, from the real source."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    owner, _, name = qualified.partition(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == owner:
            for inner in node.body:
                if isinstance(inner, ast.AsyncFunctionDef) and inner.name == name:
                    return inner
    raise AssertionError(f"{qualified} is not in {source.name}; this test must follow it")


def test_the_adapter_is_two_sibling_modules_and_never_a_package_tg5() -> None:
    """A ``telegram/`` package is invisible to five built seam scans, all of them non-recursive.

    ``tests/service/test_seam.py::_sources`` globs ``*.py`` one level deep, and its own docstring
    names ``pkb/server/telegram.py`` as the reason discovery matters. Executed both ways: as a
    module, a planted ``os``/``uuid``/``mkdir`` violation **fails** SV-10 and SV-22; move the
    identical code to ``pkb/server/telegram/core.py`` behind a re-exporting ``__init__`` and both
    pass — SV-1, SV-10, SV-18, SV-22 and SV-25 all go blind on the newest transport at once, with
    the only symptom a bare ``FileNotFoundError`` from an unrelated test.

    If a package ever becomes unavoidable, ``_sources`` must change ``glob`` to ``rglob`` **in the
    same commit**, with the planted-module counter-tests still failing as designed — and that
    change newly covers ``pkb/core/generators/``, ``pkb/agents/middleware/`` and
    ``pkb/agents/tools/``, which need planted tests of their own.
    """
    assert ADAPTER_SOURCE.is_file() and API_SOURCE.is_file()
    assert not (SOURCE_DIR / "server" / "telegram").exists(), (
        "a package here switches off SV-1, SV-10, SV-18, SV-22 and SV-25 for this module"
    )


@pytest.mark.parametrize("method", ["TelegramAdapter._poll", "TelegramAdapter._pump_outbox"])
def test_the_task_has_no_reachable_exit_tg6(method: str) -> None:
    """A supervised task that returns cleanly is worse than one that crashes.

    Executed: ``_supervise`` sets ``state == "stopped"``, ``healthy == False`` and
    ``last_error == None``, and then **returns itself** — so ``/health`` reads ``degraded``
    permanently, with no reason given and nothing left to revive it. A crash at least restarts and
    names a cause. So the loop body carries no ``return`` and no ``break``: the only ways out are
    ``CancelledError`` at shutdown and an exception.
    """
    body = _function(ADAPTER_SOURCE, method).body
    loops = [node for node in body if isinstance(node, ast.While)]
    assert loops, f"{method} is no longer a `while` loop; TG-6 is about that loop"

    for loop in loops:
        assert isinstance(loop.test, ast.Constant) and loop.test.value is True
        for node in ast.walk(loop):
            assert not isinstance(node, ast.Break), f"{method} can break out of its loop"
            assert not isinstance(node, ast.Return), f"{method} can return from its loop"


@pytest.mark.parametrize("source", [ADAPTER_SOURCE, API_SOURCE], ids=["telegram", "telegram_api"])
def test_neither_telegram_module_detaches_a_task_tg7(source: Path) -> None:
    """``asyncio.create_task`` at task scope is the P-23 defect, spelled out.

    ``_supervise`` restarts the callable and cancels **nothing** the previous invocation started:
    measured, a task that detached one child left three live pollers after three generations, and
    ``/health`` read ``running`` for most of it. Against the real API that is three concurrent
    ``getUpdates`` on one token — Telegram permits one and answers the rest with ``409 Conflict``,
    so updates split across consumers that then crash and multiply. The group is what makes a
    restart a clean slate, and a bare ``create_task`` is what takes that away.
    """
    calls = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    assert "asyncio.create_task" not in calls
    assert "asyncio.ensure_future" not in calls


@pytest.mark.asyncio
async def test_three_generations_never_leave_two_pollers_alive_tg7() -> None:
    """The P-23 regression, over the **real** ``_supervise`` and the real adapter.

    A crash on every third poll, three generations, and at no tick may two children of the same
    name be alive: that is the difference between "the bot restarted" and "the bot is now three
    bots sharing one token". Driven through the real supervisor because the failure is a property
    of *its* contract — it awaits one coroutine and has no handle on anything that coroutine
    detaches.
    """
    from pkb.server.app import _supervise
    from pkb.server.health import SubsystemState
    from pkb.server.telegram import TelegramAdapter, TelegramConfig

    state = SubsystemState(name="telegram")
    polls = 0
    peak = 0

    class Api:
        async def get_updates(self, offset: Any, *, timeout: int = 25) -> Any:
            nonlocal polls, peak
            polls += 1
            live = [
                task
                for task in asyncio.all_tasks()
                if task.get_name().startswith("pkb-telegram-poll")
            ]
            peak = max(peak, len(live))
            await asyncio.sleep(0)
            if polls % 3 == 0:
                raise RuntimeError("the poller died")
            return []

        async def send_message(self, *args: Any, **kwargs: Any) -> Any:
            return {"message_id": 1}

    class Store:
        async def setup(self) -> None: ...
        async def next_offset(self) -> int | None:
            return 1

        async def orphans(self) -> list[tuple[int, int | None]]:
            return []

        async def unfinished(self) -> list[tuple[int, int | None, str]]:
            return []

    class Catalog:
        def list_agents(self) -> list[Any]:
            return []

    bot = TelegramAdapter(
        service=Catalog(),  # type: ignore[arg-type]
        store=Store(),  # type: ignore[arg-type]
        api=Api(),  # type: ignore[arg-type]
        config=TelegramConfig(token="000:fake", chats={}, owner_user_ids=frozenset()),
    )

    supervised = asyncio.create_task(_supervise(bot.run, state))
    # Wall-clock, not loop ticks: `_supervise` sleeps a doubling backoff between generations
    # (1 s, then 2 s), so a tick-counting drive returns before the first restart has happened.
    deadline = time.monotonic() + 6.0
    while state.restarts < 2 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    supervised.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await supervised

    assert state.restarts >= 2, "the crash must reach the supervisor, twice"
    assert peak <= 1, "a previous generation's poller was still alive when the next one started"
    assert [t for t in asyncio.all_tasks() if t.get_name().startswith("pkb-telegram-")] == []


@pytest.mark.asyncio
async def test_a_transient_failure_never_increments_restarts_tg8() -> None:
    """``restarts`` is the number arch §8 asks a human to trust, over the real ``SubsystemState``.

    A 429, a 500 and a read timeout are the weather, not the subsystem being down. If a dropped
    packet incremented this counter the human would learn to ignore it — and ``_supervise``
    initialises its backoff **outside** its ``while``, so six blips would also leave the bot at a
    permanent 60 s restart delay for the life of the process.
    """
    from pkb.server.app import _supervise
    from pkb.server.health import SubsystemState
    from pkb.server.telegram import TelegramAdapter, TelegramConfig
    from pkb.server.telegram_api import TRANSPORT_CODE, TelegramError

    state = SubsystemState(name="telegram")
    # One failure per poll cycle, each followed by a success: `with_retry` allows three attempts,
    # so three failures inside one cycle would exhaust it and reach the supervisor by design.
    scripted: list[TelegramError | None] = [
        TelegramError("getUpdates", 429, "Too Many Requests", retry_after=0.01),
        None,
        TelegramError("getUpdates", 500, "Internal Server Error"),
        None,
        TelegramError("getUpdates", TRANSPORT_CODE, "transport failure: ReadTimeout"),
        None,
    ]

    class Api:
        async def get_updates(self, offset: Any, *, timeout: int = 25) -> Any:
            await asyncio.sleep(0)
            if scripted:
                error = scripted.pop(0)
                if error is not None:
                    raise error
            return []

        async def send_message(self, *args: Any, **kwargs: Any) -> Any:
            return {"message_id": 1}

    class Store:
        async def setup(self) -> None: ...
        async def next_offset(self) -> int | None:
            return 1

        async def orphans(self) -> list[tuple[int, int | None]]:
            return []

        async def unfinished(self) -> list[tuple[int, int | None, str]]:
            return []

    class Catalog:
        def list_agents(self) -> list[Any]:
            return []

    bot = TelegramAdapter(
        service=Catalog(),  # type: ignore[arg-type]
        store=Store(),  # type: ignore[arg-type]
        api=Api(),  # type: ignore[arg-type]
        config=TelegramConfig(token="000:fake", chats={}, owner_user_ids=frozenset()),
    )

    supervised = asyncio.create_task(_supervise(bot.run, state))
    deadline = time.monotonic() + 10.0
    while scripted and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    supervised.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await supervised

    assert scripted == [], "the three transient failures were never delivered"
    assert state.restarts == 0, "a blip must not be a restart (TG-8)"
    assert state.last_error is None


@pytest.mark.asyncio
async def test_fifty_forced_restarts_leave_exactly_one_open_client_tg10() -> None:
    """The ``async with`` in ``_telegram_task`` **is** TG-10's ``finally``, on both paths.

    ``_supervise`` restarts on anything and carries nothing across, so a client left open by a
    crashed generation is leaked for the life of the process — fifty crashes is fifty sockets and
    fifty connection pools in a daemon whose value proposition is staying up for weeks. Driven
    against a client that counts, over the real supervisor, and then cancelled to prove the
    cancellation path closes it too and re-raises untouched.

    Layer 3's SQLite connection is deliberately **not** closed by that teardown: it is the
    checkpointer's own shared handle and the lifespan owns it. Closing it on a bot restart would
    take every in-flight run and the whole HTTP API down with the bot.
    """

    opened = 0
    closed = 0

    class CountingClient:
        async def __aenter__(self) -> CountingClient:
            nonlocal opened
            opened += 1
            return self

        async def __aexit__(self, *_: object) -> None:
            nonlocal closed
            closed += 1

    async def start() -> None:
        async with CountingClient():
            raise RuntimeError("the generation crashed")

    # The generations are driven directly rather than through `_supervise`: its backoff doubles to
    # a minute, so fifty supervised restarts is an hour of sleeping. What is under test is the
    # teardown the supervisor calls fifty times, and TG-7's test already drives the supervisor
    # itself.
    for _ in range(50):
        with contextlib.suppress(RuntimeError):
            await start()

    assert opened == 50
    assert opened - closed == 0, f"{opened - closed} clients were left open across the restarts"


@pytest.mark.asyncio
async def test_cancelling_the_task_closes_the_client_and_re_raises_tg10() -> None:
    """Shutdown: the cancellation reaches the ``finally``, the client closes, nothing is swallowed.

    Unlike an SSE route this teardown **may** await — the ASGI cancel-scope rule that forbids it is
    a property of the response's anyio scope, and this is a bare ``asyncio.Task``. Measured: an
    ``await`` inside such a ``finally`` survives cancellation, which is what makes an AP-12-style
    farewell to the chat possible at all.
    """
    from pkb.server.app import _supervise
    from pkb.server.health import SubsystemState

    closed: list[int] = []
    entered = asyncio.Event()

    class CountingClient:
        async def __aenter__(self) -> CountingClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            await asyncio.sleep(0)  # the teardown is allowed to await here (P-30)
            closed.append(1)

    async def start() -> None:
        async with CountingClient():
            entered.set()
            await asyncio.Event().wait()  # a long poll in flight

    state = SubsystemState(name="telegram")
    supervised = asyncio.create_task(_supervise(start, state))
    await entered.wait()
    supervised.cancel()

    with pytest.raises(asyncio.CancelledError):
        await supervised

    assert closed == [1]
    assert state.state == "stopped"
