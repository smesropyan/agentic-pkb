"""The session surface — the API is the one way in (S-1 … S-39, RO-1 … RO-22).

``DESIGN.md`` §2 replaces the channel-is-identity thread model with a session: a durable named thing
reached through the seven commands' backing routes. Every test here drives a real ``create_app``
over :class:`~tests.server.stub.StubService` — free, fast, no runtime, no SQLite — the same
discipline ``tests/server/test_routes.py`` established for the thread era (module docstring there).
What is under test is the *transport*: which paths exist, how a body becomes a session, what a
route refuses before anything irreversible happens, and how a typed session error becomes a status
code.

A handful of tests are marked ``# real service`` and drive a genuine :class:`RuntimeService` over a
:class:`FakeRuntime`, a real ``:memory:`` SQLite connection and a real ``tmp_path`` KB root instead
of the stub — the only way to prove ``RuntimeService.create_session`` actually composes
``SessionStore`` and ``SessionFileWriter`` (the store row and the file, wired together for the first
time by this task) rather than merely forwarding to a fake that never touches either.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from starlette.testclient import TestClient

from pkb.contracts import (
    AgentEvent,
    InvalidDecisionError,
    MessageComplete,
    PkbAgentError,
    RunEnd,
    RunHandle,
    ThreadBusyError,
    UnknownAgentError,
)
from pkb.core.errors import has_errors
from pkb.core.validation import validate_content
from pkb.server.app import create_app
from pkb.server.errors import (
    ILLEGAL_SESSION_TRANSITION_CODE,
    SESSION_NAME_TAKEN_CODE,
    UNKNOWN_SESSION_CODE,
)
from pkb.server.routes import route_paths
from pkb.service.runtime import RuntimeService
from pkb.service.sessions import IllegalSessionTransitionError, SessionNameTakenError
from tests.server.stub import (
    AGENTS,
    COOKING,
    GRILLING,
    LEARNING,
    LIBRARIAN,
    StubService,
    opener_for,
)
from tests.server.test_routes import FASTAPI_DOCS

BASE_URL = "http://127.0.0.1:8000"

SCRIPT = (
    MessageComplete(run_id="run-1", agent_id=LIBRARIAN, text="pull it at 52C"),
    RunEnd(run_id="run-1", final_text="pull it at 52C"),
)

SESSION_PATHS = frozenset(
    {
        "/agents",
        "/agents/{agent_id:path}/sessions",
        "/sessions",
        "/sessions/{session_id}",
        "/sessions/{session_id}/name",
        "/sessions/{session_id}/close",
        "/sessions/{session_id}/end",
        "/sessions/{session_id}/runs",
        "/sessions/{session_id}/events",
        "/runs/{run_id}",
        "/health",
        "/mcp",
    }
)
"""Every path this project agreed to serve, re-pinned for the session era (RO-1). ``/threads*``,
``/threads/{id}/interrupt`` and ``/proposals*`` are gone; ``DELETE /sessions/{id}`` does not exist
— "nothing deletes a session" (§2.7) — so there is no successor to the old ``DELETE /threads/{id}``
row."""


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


@contextlib.contextmanager
def client_for(service: StubService) -> Iterator[TestClient]:
    with TestClient(create_app(opener_for(service)), base_url=BASE_URL) as client:
        yield client


@pytest.fixture
def service() -> StubService:
    return StubService(events=SCRIPT)


@pytest.fixture
def client(service: StubService) -> Iterator[TestClient]:
    with client_for(service) as started:
        yield started


class RaisingService(StubService):
    """A stub whose session run entry points refuse, so the error map can be driven from a route."""

    error: BaseException | None = None

    async def start_session_run(self, session_id: str, message: str) -> Any:
        if self.error is not None:
            raise self.error
        return await super().start_session_run(session_id, message)


# --------------------------------------------------------------------------------------
# § the pinned surface (RO-1)
# --------------------------------------------------------------------------------------


def test_the_served_paths_are_exactly_the_session_pinned_set_ro1(service: StubService) -> None:
    """Task 5's own re-pin: the old thread/interrupt/proposal rows are gone, ``/sessions*`` and its
    sub-routes stand in their place, ``/agents``, ``/runs/{id}`` and ``/health`` survive untouched."""
    app = create_app(opener_for(service))

    served = set(route_paths(app))

    assert served == SESSION_PATHS | FASTAPI_DOCS


# --------------------------------------------------------------------------------------
# § creating a session
# --------------------------------------------------------------------------------------


def test_create_session_is_201_with_a_location_header_s1(
    service: StubService, client: TestClient
) -> None:
    response = client.post(
        f"/agents/{COOKING}/sessions", json={"objective": "a rub that doesn't burn"}
    )

    assert response.status_code == 201
    session = response.json()["session"]
    assert response.headers["location"] == f"/sessions/{session['session_id']}"
    assert session["agent_id"] == COOKING
    assert session["objective"] == "a rub that doesn't burn"
    assert session["state"] == "open"
    assert session["operator"] == "operator"  # S-8: the declared identity default
    assert session["file_path"] == f"sessions/{session['name']}.md"
    # the id in the Location header is fetchable, which is the only thing a header promises
    assert client.get(response.headers["location"]).status_code == 200


def test_the_operator_comes_from_the_caller_declared_identity_field_s8(
    service: StubService, client: TestClient
) -> None:
    response = client.post(f"/agents/{COOKING}/sessions", json={"operator": "sergiy"})

    assert response.json()["session"]["operator"] == "sergiy"
    assert ("create_session", (COOKING, None, "sergiy", None)) in service.calls


def test_an_agent_id_keeps_its_slashes_ro2(service: StubService, client: TestClient) -> None:
    """``topic/cooking/grilling`` is one opaque id, not three path segments — RO-2 unchanged by the
    session rename."""
    response = client.post(f"/agents/{GRILLING}/sessions", json={})

    assert response.status_code == 201
    assert response.json()["session"]["agent_id"] == GRILLING
    assert ("create_session", (GRILLING, None, "operator", None)) in service.calls


def test_an_unknown_agent_is_404_unknown_agent_before_any_row_lands_s9(
    service: StubService, client: TestClient
) -> None:
    """S-9: an unknown target is a named error, not a silently-accepted row."""
    response = client.post("/agents/topic/atlantis/sessions", json={"objective": "anything"})

    assert response.status_code == 404
    assert response.json()["code"] == "unknown_agent"
    assert service.sessions == {}


def test_the_learning_agent_is_a_valid_creation_target_s9() -> None:
    """S-9: "establish the session to the Learning agent for the analysis after /close" — a target
    the catalog itself does not list (Phase 4 mints its registry row), refused nowhere in this
    layer."""
    service = StubService()
    with client_for(service) as client:
        response = client.post(f"/agents/{LEARNING}/sessions", json={"objective": "review it"})

    assert response.status_code == 201
    assert response.json()["session"]["agent_id"] == LEARNING


# --------------------------------------------------------------------------------------
# § listing and reading
# --------------------------------------------------------------------------------------


def test_get_session_roundtrips_what_create_returned(
    service: StubService, client: TestClient
) -> None:
    created = client.post(f"/agents/{COOKING}/sessions", json={"objective": "sear a steak"}).json()[
        "session"
    ]

    fetched = client.get(f"/sessions/{created['session_id']}").json()["session"]

    assert fetched == created


def test_an_unknown_session_is_404_unknown_session(client: TestClient) -> None:
    response = client.get("/sessions/nobody-made-this")

    assert response.status_code == 404
    assert response.json()["code"] == UNKNOWN_SESSION_CODE


def test_list_sessions_filters_by_state_and_agent(service: StubService, client: TestClient) -> None:
    cooking = client.post(f"/agents/{COOKING}/sessions", json={}).json()["session"]
    client.post(f"/agents/{GRILLING}/sessions", json={})
    client.post(f"/sessions/{cooking['session_id']}/close")

    open_only = client.get("/sessions", params={"state": "open"}).json()["sessions"]
    assert [s["agent_id"] for s in open_only] == [GRILLING]

    cooking_only = client.get("/sessions", params={"agent_id": COOKING}).json()["sessions"]
    assert [s["session_id"] for s in cooking_only] == [cooking["session_id"]]


def test_state_closed_is_the_learning_queue_ordered_by_closed_at_s25(
    service: StubService, client: TestClient
) -> None:
    """P4/S-25: the queue IS the closed set, ``closed_at``-ordered — not creation order."""
    first = client.post(f"/agents/{COOKING}/sessions", json={"name": "first"}).json()["session"]
    second = client.post(f"/agents/{GRILLING}/sessions", json={"name": "second"}).json()["session"]
    # Close in the *opposite* order they were created, so a creation-ordered list would disagree.
    client.post(f"/sessions/{second['session_id']}/close")
    client.post(f"/sessions/{first['session_id']}/close")

    queue = client.get("/sessions", params={"state": "closed"}).json()["sessions"]

    assert [s["session_id"] for s in queue] == [second["session_id"], first["session_id"]]


# --------------------------------------------------------------------------------------
# § /name (S-16)
# --------------------------------------------------------------------------------------


def test_name_renames_the_session(service: StubService, client: TestClient) -> None:
    session = client.post(f"/agents/{COOKING}/sessions", json={}).json()["session"]

    response = client.post(f"/sessions/{session['session_id']}/name", json={"name": "Sear Timing"})

    assert response.status_code == 200
    renamed = response.json()["session"]
    assert renamed["name"] == "sear-timing"
    assert renamed["file_path"] == "sessions/sear-timing.md"


def test_an_empty_name_is_400(service: StubService, client: TestClient) -> None:
    session = client.post(f"/agents/{COOKING}/sessions", json={}).json()["session"]

    for body in ({"name": ""}, {"name": "   "}, {}):
        response = client.post(f"/sessions/{session['session_id']}/name", json=body)
        assert response.status_code == 400, body
        assert response.json()["code"] == "invalid_decision"


def test_a_rename_collision_is_409_session_name_taken_s16(
    service: StubService, client: TestClient
) -> None:
    client.post(f"/agents/{COOKING}/sessions", json={"name": "brisket"})
    other = client.post(f"/agents/{GRILLING}/sessions", json={}).json()["session"]

    response = client.post(f"/sessions/{other['session_id']}/name", json={"name": "brisket"})

    assert response.status_code == 409
    assert response.json()["code"] == SESSION_NAME_TAKEN_CODE


def test_a_rename_after_the_seal_is_409_s16() -> None:
    """ "it refuses the rename once ``/end`` has sealed this file" (S-16)."""
    service = StubService()
    with client_for(service) as client:
        session = client.post(f"/agents/{COOKING}/sessions", json={}).json()["session"]
        client.post(f"/sessions/{session['session_id']}/close")
        client.post(f"/sessions/{session['session_id']}/end")

        response = client.post(f"/sessions/{session['session_id']}/name", json={"name": "new"})

    assert response.status_code == 409
    assert response.json()["code"] == ILLEGAL_SESSION_TRANSITION_CODE


# --------------------------------------------------------------------------------------
# § /close and /end (S-17, S-20 … S-25/P4)
# --------------------------------------------------------------------------------------


def test_close_marks_the_session_closed(service: StubService, client: TestClient) -> None:
    session = client.post(f"/agents/{COOKING}/sessions", json={}).json()["session"]

    response = client.post(f"/sessions/{session['session_id']}/close")

    assert response.status_code == 200
    closed = response.json()["session"]
    assert closed["state"] == "closed"
    assert closed["closed_at"] is not None


def test_re_close_is_409_illegal_session_transition_s20(
    service: StubService, client: TestClient
) -> None:
    session = client.post(f"/agents/{COOKING}/sessions", json={}).json()["session"]
    client.post(f"/sessions/{session['session_id']}/close")

    response = client.post(f"/sessions/{session['session_id']}/close")

    assert response.status_code == 409
    assert response.json()["code"] == ILLEGAL_SESSION_TRANSITION_CODE


def test_end_on_an_open_session_is_refused_s22(service: StubService, client: TestClient) -> None:
    session = client.post(f"/agents/{COOKING}/sessions", json={}).json()["session"]

    response = client.post(f"/sessions/{session['session_id']}/end")

    assert response.status_code == 409
    assert response.json()["code"] == ILLEGAL_SESSION_TRANSITION_CODE


def test_end_after_close_seals_the_session_s22(service: StubService, client: TestClient) -> None:
    session = client.post(f"/agents/{COOKING}/sessions", json={}).json()["session"]
    client.post(f"/sessions/{session['session_id']}/close")

    response = client.post(f"/sessions/{session['session_id']}/end")

    assert response.status_code == 200
    ended = response.json()["session"]
    assert ended["state"] == "ended"
    assert ended["ended_at"] is not None


def test_unknown_session_on_close_and_end_is_404(client: TestClient) -> None:
    for route in ("close", "end"):
        response = client.post(f"/sessions/nobody-made-this/{route}")
        assert response.status_code == 404, route
        assert response.json()["code"] == UNKNOWN_SESSION_CODE, route


# --------------------------------------------------------------------------------------
# § a turn on a session — the re-homed run/SSE machinery
# --------------------------------------------------------------------------------------


def test_a_run_streams_over_the_new_route(service: StubService, client: TestClient) -> None:
    """SS-2: all three headers, on ``/runs`` specifically — ``test_attach_streams_the_sse_headers_ss2``
    below pins the same three on ``/events``, so a regression dropping one from either route's own
    call to ``_stream()``/``SSE_HEADERS`` is caught rather than only the shared machinery's."""
    session = client.post(f"/agents/{COOKING}/sessions", json={}).json()["session"]

    response = client.post(
        f"/sessions/{session['session_id']}/runs", json={"message": "how long for brisket?"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert ("start_session_run", (session["session_id"], "how long for brisket?")) in service.calls


def test_an_empty_message_is_400_ro11(service: StubService, client: TestClient) -> None:
    session = client.post(f"/agents/{COOKING}/sessions", json={}).json()["session"]

    for body in ({"message": ""}, {"message": "   "}, {}):
        response = client.post(f"/sessions/{session['session_id']}/runs", json=body)
        assert response.status_code == 400, body
        assert response.json()["code"] == "invalid_decision"

    assert not [call for call in service.calls if call[0] == "start_session_run"]


def test_a_run_on_a_closed_session_is_refused_s20() -> None:
    service = StubService()
    with client_for(service) as client:
        session = client.post(f"/agents/{COOKING}/sessions", json={}).json()["session"]
        client.post(f"/sessions/{session['session_id']}/close")

        response = client.post(
            f"/sessions/{session['session_id']}/runs", json={"message": "one more thing"}
        )

    assert response.status_code == 409
    assert response.json()["code"] == ILLEGAL_SESSION_TRANSITION_CODE


def test_a_run_on_a_sealed_session_is_refused() -> None:
    service = StubService()
    with client_for(service) as client:
        session = client.post(f"/agents/{COOKING}/sessions", json={}).json()["session"]
        client.post(f"/sessions/{session['session_id']}/close")
        client.post(f"/sessions/{session['session_id']}/end")

        response = client.post(
            f"/sessions/{session['session_id']}/runs", json={"message": "one more thing"}
        )

    assert response.status_code == 409
    assert response.json()["code"] == ILLEGAL_SESSION_TRANSITION_CODE


def test_the_events_route_is_204_when_idle_and_streams_a_live_run(
    service: StubService, client: TestClient
) -> None:
    session = client.post(f"/agents/{COOKING}/sessions", json={}).json()["session"]

    idle = client.get(f"/sessions/{session['session_id']}/events")
    assert idle.status_code == 204
    assert ("attach_session", (session["session_id"],)) in service.calls


class AttachedService(StubService):
    """A stub with a session run already in flight, so ``GET /sessions/{id}/events`` streams."""

    async def attach_session(self, session_id: str) -> Any:
        self.calls.append(("attach_session", (session_id,)))

        async def stream() -> AsyncIterator[AgentEvent]:
            for event in self.events:
                yield event

        handle = RunHandle(run_id=self.run_id, agent_id=LIBRARIAN, thread_id=session_id)
        from pkb.service import RunSubscription

        return RunSubscription(handle=handle, events=stream(), close=None)


def test_attach_streams_the_sse_headers_ss2() -> None:
    service = AttachedService(events=SCRIPT)
    with client_for(service) as client:
        session = client.post(f"/agents/{COOKING}/sessions", json={}).json()["session"]
        response = client.get(f"/sessions/{session['session_id']}/events")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"


# --------------------------------------------------------------------------------------
# § errors (RO-20, RO-21)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (UnknownAgentError("no agent answers to 'topic/atlantis'"), 404, "unknown_agent"),
        (ThreadBusyError("a run is already active on session 's-1'"), 409, "thread_busy"),
        (InvalidDecisionError("expected 2 decisions, got 1"), 400, "invalid_decision"),
        (
            IllegalSessionTransitionError("session 's-1' is 'closed'; refused"),
            409,
            ILLEGAL_SESSION_TRANSITION_CODE,
        ),
        (
            SessionNameTakenError("a session named 'x' already exists"),
            409,
            SESSION_NAME_TAKEN_CODE,
        ),
    ],
    ids=["unknown_agent", "thread_busy", "invalid", "illegal_transition", "name_taken"],
)
def test_each_typed_error_maps_to_one_status_and_code_ro20(
    error: PkbAgentError, status: int, code: str
) -> None:
    service = RaisingService(events=SCRIPT)
    service.error = error
    with client_for(service) as client:
        session_response = client.post(f"/agents/{COOKING}/sessions", json={})
        session = session_response.json()["session"]
        response = client.post(f"/sessions/{session['session_id']}/runs", json={"message": "hi"})

    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.json()["status"] == status


class UnmappedAgentError(PkbAgentError):
    """A typed error added to the seam that nobody gave a row in ``ERROR_CODES``."""


def test_an_unmapped_typed_error_is_a_500_that_leaks_nothing_ro20() -> None:
    service = RaisingService(events=SCRIPT)
    service.error = UnmappedAgentError(
        "pkb.service.runtime exploded reading /Users/someone/kb/sessions/x.md"
    )
    with client_for(service) as client:
        session = client.post(f"/agents/{COOKING}/sessions", json={}).json()["session"]
        response = client.post(f"/sessions/{session['session_id']}/runs", json={"message": "hi"})

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "internal"
    assert body["detail"] == "an unexpected error occurred; see the daemon log"
    for leak in ("pkb.service.runtime", "/Users/someone", "UnmappedAgentError"):
        assert leak not in response.text


def test_an_error_body_is_problem_json_carrying_the_message_verbatim_ro21() -> None:
    message = "session 's-1' is 'ended'; a sealed session is never renamed"
    service = RaisingService(events=SCRIPT)
    service.error = IllegalSessionTransitionError(message)
    with client_for(service) as client:
        session = client.post(f"/agents/{COOKING}/sessions", json={}).json()["session"]
        response = client.post(f"/sessions/{session['session_id']}/runs", json={"message": "hi"})

    assert response.headers["content-type"].split(";")[0] == "application/problem+json"
    assert response.json() == {
        "type": "about:blank",
        "title": "Illegal session transition",
        "status": 409,
        "code": ILLEGAL_SESSION_TRANSITION_CODE,
        "detail": message,
    }


# --------------------------------------------------------------------------------------
# § real service — proving RuntimeService actually composes the store and the file
# --------------------------------------------------------------------------------------


class FakeRuntime:
    """Satisfies ``pkb.service.runtime.Runtime`` structurally — no harness (mirrors test_seam.py)."""

    db_path = Path("never-opened.sqlite")

    def list_agents(self) -> Any:
        return AGENTS

    def run(self, agent_id: str, thread_id: str, message: str, **_: Any) -> Any:
        async def stream() -> AsyncIterator[Any]:
            yield MessageComplete(run_id="r1", agent_id=agent_id, text=f"echo: {message}")
            yield RunEnd(run_id="r1", final_text=f"echo: {message}")

        return stream()

    def resume(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - unused here
        raise NotImplementedError

    async def cancel(self, run_id: str) -> None:
        return None

    async def pending_approval(self, agent_id: str, thread_id: str) -> None:
        return None

    async def history(self, agent_id: str, thread_id: str) -> Any:
        return []

    async def delete_thread(self, thread_id: str) -> None:
        return None

    async def request_scan(self, request: Any) -> Any:
        raise NotImplementedError

    async def regenerate(self) -> None:
        raise NotImplementedError


@contextlib.asynccontextmanager
async def _real_service(kb_root: Path) -> AsyncIterator[RuntimeService]:
    connection = await aiosqlite.connect(":memory:", isolation_level=None)
    try:
        service = RuntimeService(FakeRuntime(), connection, kb_root=kb_root)
        await service.setup()
        yield service
    finally:
        await connection.close()


@contextlib.contextmanager
def real_client(kb_root: Path) -> Iterator[TestClient]:
    def opener() -> Any:
        return _real_service(kb_root)

    with TestClient(create_app(opener), base_url=BASE_URL) as client:
        yield client


def test_a_librarian_session_creates_its_file_cleanly_with_zero_experts_p5(tmp_path: Path) -> None:
    """P5: the Librarian owns no topic, so a fresh session has no expert's ``topic.*`` tag to write
    — Phase 1's own floor is scoped away for ``FileRole.SESSION`` alone, and this is a valid file."""
    with real_client(tmp_path) as client:
        response = client.post(f"/agents/{LIBRARIAN}/sessions", json={"objective": "plan the week"})

    assert response.status_code == 201
    session = response.json()["session"]
    on_disk = tmp_path / session["file_path"]
    assert on_disk.exists()

    findings = validate_content(tmp_path, session["file_path"], on_disk.read_text(encoding="utf-8"))
    assert not has_errors(findings), findings


def test_an_unknown_agent_never_reaches_the_store_or_the_disk_s9(tmp_path: Path) -> None:
    """S-9: the catalog check runs *before* ``SessionStore.create`` — nothing lands anywhere."""
    with real_client(tmp_path) as client:
        response = client.post("/agents/topic/atlantis/sessions", json={"objective": "x"})

    assert response.status_code == 404
    assert response.json()["code"] == "unknown_agent"
    sessions_dir = tmp_path / "sessions"
    assert not sessions_dir.exists() or list(sessions_dir.glob("*.md")) == []
