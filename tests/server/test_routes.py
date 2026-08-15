"""The HTTP surface (RO-1 … RO-22).

Every test here drives a real ``create_app`` over :class:`~tests.server.stub.StubService`, so what
is under test is the *transport*: which paths exist, how a URL becomes an id, what a body must
carry before anything irreversible happens, and how a typed error becomes a status code. No
runtime, no model, no SQLite — the seam is expressible without the harness (SV-1) precisely so this
suite can be free.

``base_url`` carries a **port** on purpose. The MCP mount keeps DNS-rebinding protection on (MC-4),
and a portless ``Host`` header — which is what ``TestClient``'s default ``http://testserver`` sends
— is rejected with 421 before a route is ever consulted.

``TestClient`` buffers a stream into one body (P-4), so nothing here asserts *incrementality*; that
is ``tests/server/test_runs.py``'s raw driver. What it can assert, and does, is the status code and
the header set chosen **before** the first byte — which is the whole of RO-13's guarantee.
"""

from __future__ import annotations

import ast
import contextlib
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from pkb.contracts import (
    ApprovalPendingError,
    Decision,
    InvalidDecisionError,
    MessageComplete,
    PkbAgentError,
    RunEnd,
    RunHandle,
    StaleInterruptError,
    ThreadBusyError,
    UnknownAgentError,
    UnknownThreadError,
    expert_thread_id,
)
from pkb.server.app import ServerConfig, create_app
from pkb.server.health import HealthState, redact
from pkb.service import RunSubscription, Thread
from tests.server.stub import COOKING, LIBRARIAN, NOW, StubService, opener_for

BASE_URL = "http://127.0.0.1:8000"
"""A ``Host`` header with a port — see the module docstring."""

GRILLING = "topic/cooking/grilling"

THREAD = "3f0c9a1e"
"""A Librarian thread a human started in the TUI."""

DERIVED = expert_thread_id(THREAD, COOKING)
"""``3f0c9a1e::topic/cooking`` — a thread id carrying both ``::`` and ``/`` (RO-3)."""

SCRIPT = (
    MessageComplete(run_id="run-1", agent_id=LIBRARIAN, text="pull it at 52C"),
    RunEnd(run_id="run-1", final_text="pull it at 52C"),
)
"""A run short enough to finish inside a buffered ``TestClient`` response."""


# --------------------------------------------------------------------------------------
# The pinned surface (RO-1)
# --------------------------------------------------------------------------------------

FASTAPI_DOCS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})
"""FastAPI's own documentation endpoints. Named rather than filtered by prefix so that turning them
off (or a future FastAPI adding a fifth) shows up as a failing assertion rather than as silence."""


# --------------------------------------------------------------------------------------
# Fixtures — all local to this file
# --------------------------------------------------------------------------------------


def seed(service: StubService) -> StubService:
    """Give a stub the two threads every routing test needs: a parent and its derived child."""
    for thread_id, agent_id in ((THREAD, LIBRARIAN), (DERIVED, COOKING)):
        service.rows[thread_id] = Thread(
            thread_id=thread_id,
            agent_id=agent_id,
            created_at=NOW,
            updated_at=NOW,
            origin_channel="tui",
        )
    return service


@contextlib.contextmanager
def client_for(service: StubService) -> Iterator[TestClient]:
    """A client over a freshly built app — a factory per test, never a module-level one (AP-1)."""
    with TestClient(create_app(opener_for(service)), base_url=BASE_URL) as client:
        yield client


@pytest.fixture
def service() -> StubService:
    return seed(StubService(events=SCRIPT))


@pytest.fixture
def client(service: StubService) -> Iterator[TestClient]:
    with client_for(service) as started:
        yield started


class RaisingService(StubService):
    """A stub whose run entry points refuse, so the error map can be driven from a route.

    Every typed error in the seam is raised by Layer 2 somewhere behind ``start_run``/``resume``;
    which method raises is not what RO-20 is about, so one lever drives the whole table.
    """

    error: BaseException | None = None

    async def start_run(
        self,
        thread_id: str,
        message: str,
        *,
        approval_mode: str = "interactive",
        run_id: str | None = None,
    ) -> RunSubscription:
        if self.error is not None:
            raise self.error
        return await super().start_run(
            thread_id, message, approval_mode=approval_mode, run_id=run_id
        )

    async def resume(
        self, thread_id: str, decisions: Sequence[Decision], *, interrupt_id: str | None = None
    ) -> RunSubscription:
        self.calls.append(("resume", (thread_id, interrupt_id)))
        if self.error is not None:
            raise self.error
        return await StubService.start_run(self, thread_id, "")


class AttachedService(StubService):
    """A stub with a run already in flight, so ``GET /threads/{id}/events`` streams (RO-17).

    ``StubService``'s own run finishes the moment its scripted events are drained, and a finished
    run has no hub to attach to — which would make the events route answer 204 and assert nothing
    about SS-2's headers.
    """

    async def attach(self, thread_id: str) -> RunSubscription | None:
        self.calls.append(("attach", (thread_id,)))

        async def stream() -> AsyncIterator[Any]:
            for event in self.events:
                yield event

        handle = RunHandle(run_id=self.run_id, agent_id=LIBRARIAN, thread_id=thread_id)
        return RunSubscription(handle=handle, events=stream(), close=None)


# --------------------------------------------------------------------------------------
# § the surface itself
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# § the catalog and the threads
# --------------------------------------------------------------------------------------


def test_the_agent_catalog_is_served_verbatim_ro4(client: TestClient) -> None:
    """Five fields, snapshot order, Librarian first — and no model chosen anywhere on this path.

    ``GET /agents`` is the one call a client makes before it knows anything, so every temptation
    lives here: reordering "helpfully" (which would move the Librarian out of the position every
    client's default lands on), adding a field, or filling in a ``model_id`` when the registry left
    one blank. The model is a registry concern (RG-21) — no transport, route or channel picks one —
    and a route that defaulted it would silently answer for a policy it does not own.
    """
    agents = client.get("/agents").json()["agents"]

    assert [a["agent_id"] for a in agents] == [LIBRARIAN, COOKING, GRILLING]
    assert all(
        set(a) == {"agent_id", "title", "description", "has_custom_expert", "model_id"}
        for a in agents
    )
    assert agents[1] == {
        "agent_id": COOKING,
        "title": "Cooking",
        "description": "Food, heat and time.",
        "has_custom_expert": True,
        "model_id": "ollama:deepseek-v4-flash:cloud",
    }


# --------------------------------------------------------------------------------------
# § starting and resuming a run
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# § errors
# --------------------------------------------------------------------------------------


class UnmappedAgentError(PkbAgentError):
    """A typed error added to the seam that nobody gave a row in ``ERROR_CODES``."""


@pytest.mark.superseded
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (UnknownAgentError("no agent answers to 'topic/atlantis'"), 404, "unknown_agent"),
        (UnknownThreadError("no thread '3f0c9a1e'"), 404, "unknown_thread"),
        (ThreadBusyError("a run is already active on thread '3f0c9a1e'"), 409, "thread_busy"),
        (
            ApprovalPendingError("thread '3f0c9a1e' is waiting on an approval"),
            409,
            "approval_pending",
        ),
        (
            StaleInterruptError("interrupt 'i-0' is no longer the pending one"),
            409,
            "stale_interrupt",
        ),
        (InvalidDecisionError("expected 2 decisions, got 1"), 400, "invalid_decision"),
    ],
    ids=["unknown_agent", "unknown_thread", "thread_busy", "approval_pending", "stale", "invalid"],
)
def test_each_typed_error_maps_to_one_status_and_code_ro20(
    error: PkbAgentError, status: int, code: str
) -> None:
    """One table, one handler — because three of these share 409 and mean different things.

    ``thread_busy`` says retry later, ``approval_pending`` says render the approval, and
    ``stale_interrupt`` says refetch the interrupt: identical status codes, opposite client
    reactions. A route building its own ``HTTPException`` would be a second place this mapping
    lives, and the two would drift the first time an error message was reworded.

    Superseded whole (Task 5 rebuilds this): every case posts to ``/threads/{id}/runs``, deleted
    with the rest of the thread-CRUD surface, so all six fail regardless of the error type under
    test — not only the two (``approval_pending``, ``stale``) that were already marked
    individually for Task 6. The five that survive as a *principle* (``unknown_agent``,
    ``thread_busy``, ``invalid``, plus session-shaped ``illegal_session_transition`` and
    ``session_name_taken``) are re-homed against ``POST /sessions/{id}/runs`` in
    ``tests/server/test_session_routes.py``; ``unknown_thread`` has no session-era successor, since
    nothing on the session surface ever raises it.
    """
    service = seed(RaisingService(events=SCRIPT))
    service.error = error

    with client_for(service) as client:
        response = client.post(f"/threads/{THREAD}/runs", json={"message": "hi"})

    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.json()["status"] == status


# --------------------------------------------------------------------------------------
# § provenance is not permission (RO-22)
# --------------------------------------------------------------------------------------

SCANNED_PACKAGES = (Path("src/pkb/server"), Path("src/pkb/service"))


def _mentions_origin_channel(node: ast.AST) -> bool:
    return any(
        (isinstance(child, ast.Name) and child.id == "origin_channel")
        or (isinstance(child, ast.Attribute) and child.attr == "origin_channel")
        or (isinstance(child, ast.Constant) and child.value == "origin_channel")
        for child in ast.walk(node)
    )


def _decision_points(tree: ast.AST) -> Iterator[ast.AST]:
    """Every place this code *chooses* — the shapes an authorization check can hide in."""
    for node in ast.walk(tree):
        if isinstance(node, ast.If | ast.While | ast.IfExp | ast.Assert):
            yield node.test
        elif isinstance(node, ast.comprehension):
            yield from node.ifs
        elif isinstance(node, ast.Match):
            yield node.subject
        elif isinstance(node, ast.Compare):
            yield node


# --------------------------------------------------------------------------------------
# § streaming headers (SS-2)
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", f"/threads/{THREAD}/runs", {"message": "how long for brisket?"}),
        (
            "POST",
            f"/threads/{THREAD}/interrupt",
            {"interrupt_id": "i-1", "decisions": []},
        ),
        ("GET", f"/threads/{THREAD}/events", None),
    ],
    ids=["runs", "interrupt", "events"],
)
def test_every_streaming_route_carries_the_sse_headers_ss2(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    """Three headers, and each one is a real deployment failure when it is missing.

    ``Cache-Control: no-store`` keeps an intermediary from serving one client's run to the next.
    ``X-Accel-Buffering: no`` is the one that bites in production: an nginx or Cloudflare hop
    buffers the whole response by default, so a working stream becomes a five-minute pause followed
    by everything at once — and it looks like a hung agent, not a proxy. ``charset=utf-8`` on the
    content type is what makes a KB full of accented ingredient names decode.

    Superseded whole (Task 5 rebuilds this): all three cases target ``/threads*``, deleted; the
    ``interrupt`` case was already marked individually for Task 6, and now ``runs``/``events`` join
    it because the routes themselves are gone. The SSE-header principle is re-homed against
    ``POST /sessions/{id}/runs`` and ``GET /sessions/{id}/events`` in
    ``tests/server/test_session_routes.py``.
    """
    service = seed(AttachedService(events=SCRIPT))

    with client_for(service) as client:
        response = client.request(method, path, json=body)

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"


def test_no_middleware_may_compress_a_stream_ss2(service: StubService) -> None:
    """A gzip middleware over a stream is the same five-minute pause, from inside the app.

    ``X-Accel-Buffering`` only tells a *proxy* not to buffer. A compression middleware added to this
    app would buffer the response itself, before the proxy ever sees it, and SS-2 requires the
    streaming routes to be excluded from it. Today the honest way to assert that is that no such
    middleware is installed at all: if one is ever added, this fails and whoever adds it has to say
    here how the streams are excluded.
    """
    app = create_app(opener_for(service))

    installed = [middleware.cls.__name__ for middleware in app.user_middleware]

    assert not [name for name in installed if "GZip" in name or "Compress" in name], installed


# --------------------------------------------------------------------------------------
# A credential must never reach an unauthenticated surface, 2026-08-08
# --------------------------------------------------------------------------------------


def test_a_subsystem_error_never_publishes_a_credential_ap18() -> None:
    """`/health` is unauthenticated by design, and it published `last_error` verbatim.

    AP-20 defers auth on the ground that the daemon binds localhost; AP-18 makes `/health` a 200
    that reports degradation in the body. Together those mean an exception message from a background
    task is served to anything on the machine. A Telegram bot token lives in the URL *path*, so
    `raise_for_status()` on a 401 puts it in the message, `SubsystemState.failed` stored it, and
    `/health` handed it out — measured with a real token before this was fixed.

    The redaction lives where the storing happens rather than at each call site, because the defect
    is a property of publishing arbitrary exception text on a public surface: a future subsystem
    with a credentialed URL inherits the fix without knowing the rule exists.
    """
    import httpx

    token = "123456789:AAFAKEfakeFAKEfake_TOKEN_valueXYZ"
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    state = HealthState(kb_root="/kb")

    try:
        httpx.Response(401, request=httpx.Request("POST", url)).raise_for_status()
    except httpx.HTTPStatusError as exc:
        state.telegram.failed(exc)

    body = state.payload(agent_count=1, active_runs=0, subscribers=0, threads=(0, 0))

    assert token not in str(body)
    assert "[redacted]" in str(body["telegram"]["last_error"])
    assert "401 Unauthorized" in str(body["telegram"]["last_error"]), "the diagnosis survives"


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        (
            "https://api.telegram.org/bot123456789:AAF-secret-value-here/getUpdates",
            "AAF-secret-value-here",
        ),
        ("https://user:hunter2@example.com/x", "hunter2"),
        ("GET /x?api_key=sk-abcdef123456", "sk-abcdef123456"),
        ("Authorization: Bearer sk-xyz789", "sk-xyz789"),
        ("Authorization: sk-plain", "sk-plain"),
    ],
)
def test_every_credential_shape_is_redacted_ap18(raw: str, secret: str) -> None:
    """The shapes a credential actually arrives in, rather than the one that prompted the fix."""
    assert secret not in redact(raw)


def test_redaction_leaves_ordinary_text_alone_ap18() -> None:
    """A redactor that eats diagnostics is a redactor somebody turns off."""
    message = "ConnectError: [Errno 61] Connection refused to 127.0.0.1:8765"
    assert redact(message) == message


# --------------------------------------------------------------------------------------
# The telegram block of `/health` (TG-11, TG-12, TG-13), 2026-08-08
# --------------------------------------------------------------------------------------


@contextlib.contextmanager
def client_for_health(state: HealthState) -> Iterator[TestClient]:
    """A client over an app whose ``HealthState`` the test owns — the daemon's seam (C-30).

    ``ServerConfig`` takes the health state the composition root built, which is where the Telegram
    mapping's shape (``chats``, ``agents``) is stamped onto it. No telegram task is started: the
    block must be right whether or not the bot is running.
    """
    app = create_app(opener_for(seed(StubService())), config=ServerConfig(health=state))
    with TestClient(app, base_url=BASE_URL) as started:
        yield started


def mapped(mapping: dict[int, str]) -> HealthState:
    """A health state carrying what the daemon loads from its config file (TG-11, TG-17)."""
    state = HealthState(kb_root="/kb")
    state.telegram.chats = len(mapping)
    state.telegram.agents = frozenset(mapping.values())
    state.telegram.running()
    return state


def test_health_names_every_agent_no_chat_can_reach_tg11() -> None:
    """TG-3's whole mechanism: the daemon reports the agents the mapping does not name.

    Creating a topic does not create a Telegram chat — the mapping is hand-configured and the bot
    never writes it. So the human's first sign that a new topic is unreachable from their phone is
    that the bot ignores it, silently and permanently, unless `/health` says so here. The count is
    published beside it because "three chats, one unreachable topic" is the sentence a human needs.
    """
    state = mapped({100: LIBRARIAN, 200: COOKING})

    with client_for_health(state) as client:
        body = client.get("/health").json()

    assert body["telegram"]["chats"] == 2
    assert body["telegram"]["unmapped_agents"] == [GRILLING]


def test_unmapped_agents_is_a_set_difference_not_a_count_tg25() -> None:
    """Two chats may map to one agent, so arithmetic over lengths lies about coverage.

    A household with a phone and a tablet on the same topic has two chats and one agent. A check of
    ``len(mapping) >= len(agents)`` calls that deployment complete while two topics sit unreachable,
    which is precisely the silent gap TG-3 exists to close.
    """
    state = mapped({100: COOKING, 200: COOKING})

    with client_for_health(state) as client:
        body = client.get("/health").json()

    assert body["telegram"]["chats"] == 2, "two chats"
    assert body["telegram"]["unmapped_agents"] == [LIBRARIAN, GRILLING]


def test_unmapped_agents_survives_a_crash_looping_bot_tg11() -> None:
    """It is computed in the endpoint, not by the bot, so a dead bot cannot take it away.

    A human reads `/health` when something is wrong. If the mapping report came from the adapter,
    it would go blank exactly then — and the reader would be unable to tell "this topic has no
    chat" from "the bot is down", which are different problems with different fixes.
    """
    state = mapped({100: LIBRARIAN, 200: COOKING})
    state.telegram.failed(RuntimeError("boom"))

    with client_for_health(state) as client:
        response = client.get("/health")

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "degraded", "a restarting subsystem is the narrow degraded case"
    assert body["telegram"]["unmapped_agents"] == [GRILLING]


def test_running_does_not_mean_telegram_is_reachable_tg12() -> None:
    """``state`` reports the task; ``last_poll_ok_at`` reports Telegram.

    ``_supervise`` stamps ``running()`` before it awaits the task body, so a bot whose token was
    revoked polls, gets a 401 and stays ``running`` with ``restarts: 0`` forever. A human debugging
    that sees ``status: ok`` for the whole first poll window and learns nothing; only a
    ``last_poll_ok_at`` that never appears — or stops advancing — tells the truth.
    """
    state = mapped({100: COOKING})

    with client_for_health(state) as client:
        before = client.get("/health").json()["telegram"]
        state.telegram.poll_ok()
        after = client.get("/health").json()["telegram"]

    assert before["state"] == "running" and before["last_poll_ok_at"] is None
    assert after["last_poll_ok_at"] == state.telegram.last_poll_ok_at
    assert after["last_poll_ok_at"] is not None


def test_a_send_failure_never_degrades_health_tg13() -> None:
    """A failed ``sendMessage`` is reported and nothing else — 200, ``ok``, ``running``.

    ``degraded`` means one thing: an enabled subsystem is not running. Widened to "something is a
    bit wrong" it fires on every dropped message and gets muted; and a non-200 invites the
    supervisor restart D9 forbids, which would kill in-flight runs and pending approvals that are
    perfectly healthy. So ``send_failed`` touches neither ``state`` nor ``restarts``.
    """
    state = mapped({100: COOKING})

    with client_for_health(state) as client:
        state.telegram.send_failed(TimeoutError("sendMessage timed out"))
        response = client.get("/health")

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["telegram"]["state"] == "running"
    assert body["telegram"]["restarts"] == 0
    assert "sendMessage timed out" in body["telegram"]["last_send_error"]


def test_a_send_failure_never_publishes_the_bot_token_tg13() -> None:
    """The new field is arbitrary library text on an unauthenticated surface, so it is redacted.

    The bot token lives in the request URL's *path*, so any client error that names the URL carries
    it — and `/health` is served to anything on the machine (AP-20). ``last_error`` was fixed for
    this once; a second free-text field inherits the fix rather than reopening the leak.
    """
    token = "123456789:AAFAKEfakeFAKEfake_TOKEN_valueXYZ"
    state = mapped({100: COOKING})

    state.telegram.send_failed(
        ConnectionError(f"POST https://api.telegram.org/bot{token}/sendMessage failed")
    )
    body = state.payload(agent_count=3, active_runs=0, subscribers=0, threads=(0, 0))

    assert token not in str(body)
    assert "[redacted]" in str(body["telegram"]["last_send_error"])
    assert "sendMessage failed" in str(body["telegram"]["last_send_error"]), (
        "the diagnosis survives"
    )
