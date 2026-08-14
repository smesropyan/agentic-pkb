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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from pkb.contracts import (
    ActionView,
    ApprovalPendingError,
    Decision,
    InvalidDecisionError,
    MessageComplete,
    MessageView,
    PendingProposal,
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
from pkb.server.routes import route_paths
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

PKB_PATHS = frozenset(
    {
        # arch §6's eight
        "/agents",
        "/agents/{agent_id:path}/threads",
        "/threads",
        "/threads/{thread_id:path}",
        "/threads/{thread_id:path}/runs",
        "/threads/{thread_id:path}/interrupt",
        "/health",
        # the five declared additions (RO-17, RO-19, RO-18, RO-19, RO-19)
        "/threads/{thread_id:path}/events",
        "/runs/{run_id}",
        "/proposals",
        "/proposals/{proposal_id}",
        # and the MCP mount (MC-2)
        "/mcp",
    }
)
"""Every path this project agreed to serve. ``PATCH``, ``GET`` and ``DELETE`` share the bare thread
path, which is why this is a **set** of thirteen against a list of sixteen route objects."""

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


def proposal(proposal_id: str) -> PendingProposal:
    return PendingProposal(
        proposal_id=proposal_id,
        agent_id=COOKING,
        thread_id=THREAD,
        action=ActionView(
            tool="write_file",
            args={"file_path": "kb/topics/Cooking/notes/steak.md"},
            description="+ Sear it hot.",
            allowed_decisions=("approve", "reject"),
            reason="breadth-approval",
        ),
        created_at=datetime(2026, 8, 8, 9, 0, tzinfo=UTC),
    )


# --------------------------------------------------------------------------------------
# § the surface itself
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_the_served_paths_are_exactly_the_pinned_set_ro1(service: StubService) -> None:
    """A route nobody gave a rule id is a surface a client will start depending on.

    RO-1 fixes the HTTP surface at arch §6's eight routes, five declared additions and ``/mcp``.
    The list has to be read through :func:`route_paths`, not ``[r.path for r in app.routes]``:
    FastAPI keeps an included router as **one** ``_IncludedRouter`` entry — which has no ``.path``
    at all — so ``app.routes`` holds seven entries for an app that serves seventeen paths, and every
    route the pkb router owns is invisible to the obvious expression. A rule that pins the surface
    has to see the surface.

    Superseded (Task 5 rebuilds this): ``PKB_PATHS`` pins the thread-era surface verbatim —
    ``/threads``, ``/threads/{thread_id:path}/interrupt``, ``/proposals`` and
    ``/proposals/{proposal_id}`` all die with Task 5/6, replaced by ``/sessions`` and its
    sub-routes. Task 5 owns re-pinning the surface for the new set.
    """
    app = create_app(opener_for(service))

    served = set(route_paths(app))

    assert served == PKB_PATHS | FASTAPI_DOCS
    # And the reason the rule names thirteen while the router holds more than thirteen objects:
    # three verbs share the bare thread path.
    assert route_paths(app).count("/threads/{thread_id:path}") == 3


@pytest.mark.superseded
def test_an_agent_id_keeps_its_slashes_ro2(service: StubService, client: TestClient) -> None:
    """``topic/cooking/grilling`` is one opaque id, not three path segments.

    An agent id *is* its position in the topic tree (RG-9), so every nested expert has ``/`` in its
    id. If the route captured only the last segment — or split, re-encoded or slugified the string
    — a grilling thread would silently be created against ``grilling`` or against ``topic/cooking``:
    the wrong expert, with the wrong prompt and the wrong write permissions, and nothing in the
    response would say so.

    Superseded (Task 5 rebuilds this): the route is ``POST /agents/{id}/threads``, deleted with the
    rest of the thread-CRUD surface; Task 5 needs an analogous slash-preserving assertion against
    ``POST /agents/{id}/sessions``.
    """
    response = client.post(f"/agents/{GRILLING}/threads", json={})

    assert response.status_code == 201
    assert ("create_thread", (GRILLING, "http")) in service.calls
    assert response.json()["thread"]["agent_id"] == GRILLING


@pytest.mark.superseded
def test_a_percent_encoded_agent_id_resolves_to_the_same_agent_ro2(
    service: StubService, client: TestClient
) -> None:
    """``%2F`` may never resolve to a *different* agent than the plain slash does.

    Starlette decodes ``%2F`` back to ``/`` before matching and proxies normalize it on the way in,
    so percent-encoding is not an escape hatch that isolates the id — it is the same id spelled
    twice. RO-2 allows either "same agent" or 404; what it forbids is the third outcome, where one
    spelling reaches ``topic/cooking`` and the other reaches something else.

    Superseded (Task 5 rebuilds this): same route, ``POST /agents/{id}/threads`` — see the sibling
    test above.
    """
    response = client.post("/agents/topic%2Fcooking/threads", json={})

    assert response.status_code in (201, 404)
    # The assertion with teeth is the id the service was asked about: the decoded one, or the
    # undecoded string that then 404s. Never a third agent.
    assert service.calls[-1] in (
        ("create_thread", (COOKING, "http")),
        ("create_thread", ("topic%2Fcooking", "http")),
    )


@pytest.mark.superseded
def test_the_events_suffix_is_not_swallowed_by_the_greedy_route_ro3(
    service: StubService, client: TestClient
) -> None:
    """``GET /threads/{tid}/events`` must reach ``attach``, not ``get_thread``.

    A thread id is not URL-simple, so every ``{thread_id}`` is a ``:path`` converter — and a
    ``:path`` converter matches slashes, including the ones in a literal suffix. Registered in the
    wrong order, ``GET /threads/{tid:path}`` answers ``/threads/x/events`` with a thread whose id is
    ``x/events``, the events route becomes dead code, and a reconnecting client silently gets a JSON
    document where it expected a stream (RO-17).

    Superseded (Task 5 rebuilds this): ``/threads*`` is gone. The hazard itself does not survive
    the rename either — a session id is a bare UUID (no ``/``), so ``/sessions/{session_id}`` is an
    ordinary path parameter and the ``:path``-converter swallow this test defended against cannot
    recur (see ``pkb.server.routes``'s module docstring). ``tests/server/test_session_routes.py``
    covers the events route reaching ``attach_session`` directly instead.
    """
    response = client.get(f"/threads/{THREAD}/events")

    assert response.status_code == 204  # nothing is running: the events route's own answer
    assert ("attach", (THREAD,)) in service.calls
    assert not [call for call in service.calls if call[0] == "get_thread"]


@pytest.mark.superseded
def test_the_runs_and_interrupt_suffixes_are_not_swallowed_either_ro3(
    service: StubService, client: TestClient
) -> None:
    """The same ordering hazard, on the two routes that start work.

    Here the failure would be worse than a wrong document: ``POST /threads/x/runs`` matching the
    greedy route would 405 rather than start a turn, and the thread id handed to the service must be
    the bare ``x`` with the suffix stripped by *routing*, not carried into the checkpointer as part
    of the id — a run keyed on ``x/runs`` writes its checkpoints to a thread no client can ever
    fetch again.

    Superseded (Task 5/6 rebuild this): mixed subject — the ``/runs`` half is a route-ordering
    hazard that survives and needs an analogous assertion against ``/sessions/{id}/runs``; the
    ``/interrupt`` half dies outright with Task 6, nothing to rebuild it against. Marked whole
    because one body asserts both.
    """
    client.post(f"/threads/{THREAD}/runs", json={"message": "how long for brisket?"})
    client.post(
        f"/threads/{THREAD}/interrupt",
        json={"interrupt_id": "i-1", "decisions": [{"type": "approve"}]},
    )

    assert ("start_run", (THREAD, "how long for brisket?")) in service.calls
    assert ("resume", (THREAD, "i-1")) in service.calls


@pytest.mark.superseded
def test_a_derived_thread_id_survives_the_url_ro3(service: StubService, client: TestClient) -> None:
    """``<uuid>::topic/cooking`` has to come back out of the URL exactly as it went in.

    A derived thread is where an expert's approval parks during a fan-out (LB-16), so it is
    precisely the id a human returning from a phone has to be able to fetch. It contains ``::`` and
    at least one ``/``; a route that split on ``/`` or a client-side "clean up the id" step would
    make the one thread the design is proudest of unreachable.

    Superseded (Task 3/7 rebuild this): the parent/derived-thread fan-out this id shape encodes is
    retired outright — a session belongs to one agent directly, and channels attach instead.
    """
    response = client.get(f"/threads/{DERIVED}")

    assert response.status_code == 200
    thread = response.json()["thread"]
    assert thread["thread_id"] == DERIVED
    # ST-6: computed from the id, so a client tells routed work from a direct conversation without
    # string-sniffing.
    assert (thread["kind"], thread["parent_thread_id"]) == ("routed", THREAD)


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


@pytest.mark.superseded
def test_create_thread_is_201_with_a_location_header_ro5(
    service: StubService, client: TestClient
) -> None:
    """201 and a ``Location`` a client can follow without re-deriving the URL itself.

    The thread id is server-minted, so the only alternative to a ``Location`` header is every client
    string-building ``/threads/{id}`` from the body — which is how a URL scheme becomes public API
    by accident. The body's ``title`` and ``origin_channel`` are optional; ``origin_channel``
    defaults to ``http`` rather than being rejected, because provenance is a label, never a
    permission (RO-22).

    Superseded (Task 5 rebuilds this): thread creation and ``origin_channel`` both die; a session
    is created with an objective and no channel field (channels attach separately, Task 7). The
    201-plus-``Location`` principle needs an analogous test against ``POST /agents/{id}/sessions``.
    """
    response = client.post(
        f"/agents/{COOKING}/threads", json={"title": "Brisket", "origin_channel": "tui"}
    )

    assert response.status_code == 201
    thread = response.json()["thread"]
    assert response.headers["location"] == f"/threads/{thread['thread_id']}"
    assert (thread["title"], thread["origin_channel"]) == ("Brisket", "tui")
    # and the id in the Location header is fetchable, which is the only thing a header promises
    assert client.get(response.headers["location"]).status_code == 200


@pytest.mark.superseded
def test_an_unknown_agent_is_404_unknown_agent_ro5(client: TestClient) -> None:
    """A typo'd topic must not mint a thread against an expert that does not exist.

    The registry is the authority on which agents exist (RG-13). Creating the row first and
    discovering the agent later would leave a conversation nobody can ever run, and a 400 would tell
    a client the *request* was malformed when the request was fine and the id was not.

    Superseded (Task 5 rebuilds this): the route is thread-creation; the unknown-agent-before-row
    principle needs an analogous test against ``POST /agents/{id}/sessions``.
    """
    response = client.post("/agents/topic/atlantis/threads", json={})

    assert response.status_code == 404
    assert response.json()["code"] == "unknown_agent"


@pytest.mark.superseded
def test_an_unknown_thread_is_404_not_an_empty_200_ro10(client: TestClient) -> None:
    """An id nobody created is a 404, decided by the threads table.

    The checkpointer cannot decide this: an unknown thread id yields *empty graph state*, not an
    error, so a ``get_thread`` that trusted it would answer 200 with an empty conversation for every
    typo — indistinguishable, at the client, from a thread whose history was lost.

    Superseded (Task 5 rebuilds this): ``GET /threads/{id}`` dies with the rest of the thread-CRUD
    surface; the not-a-row-is-404 principle needs an analogous test against ``GET /sessions/{id}``.
    """
    response = client.get("/threads/nobody-made-this")

    assert response.status_code == 404
    assert response.json()["code"] == "unknown_thread"


@pytest.mark.superseded
def test_every_message_created_at_is_null_ro10(service: StubService, client: TestClient) -> None:
    """The field is always present and always null — so no client can be tempted to sort on it.

    LangChain messages carry no timestamp, so Layer 2 has nothing truthful to put here. Omitting the
    key would make clients ``KeyError``; inventing a time — ``now()`` at read, say — would produce a
    history that reorders itself every time it is fetched. Nullable and always null is the honest
    encoding: per-thread times come from the table.

    Superseded (Task 5/6 rebuild this): the route is ``GET /threads/{id}``, and the body it asserts
    on also carries ``pending_interrupt`` (Task 6 removes it) and ``children`` (the derived-thread
    fan-out Task 3/7 remove). The null-``created_at`` principle needs an analogous test against
    whatever ``GET /sessions/{id}`` returns for its record.
    """
    service.messages = [
        MessageView(role="human", text="how long for brisket?", created_at=None),
        MessageView(role="ai", text="about twelve hours", created_at=None),
    ]

    body = client.get(f"/threads/{THREAD}").json()

    assert [(m["role"], m["text"]) for m in body["messages"]] == [
        ("human", "how long for brisket?"),
        ("ai", "about twelve hours"),
    ]
    assert all("created_at" in m and m["created_at"] is None for m in body["messages"])
    assert body["pending_interrupt"] is None and body["children"] == []


@pytest.mark.superseded
def test_patch_sets_a_title_ro19(service: StubService, client: TestClient) -> None:
    """A human's title is permanent, and an empty one is a 400 rather than an erasure.

    Titles are otherwise model-written once, after the first reply (TT-1); ``PATCH`` is how a human
    overrides that, and SV-27 makes the override stick. Accepting ``""`` or whitespace would let a
    mis-sent request blank a title with no undo (D6), and the human would have no way to tell their
    title from one the model never wrote.

    Superseded (Task 5 rebuilds this): ``PATCH /threads/{id}`` dies; the successor is
    ``POST /sessions/{id}/name``, a differently-verbed route that also renames the file and
    retitles every attached channel — not a like-for-like rename of this test.
    """
    response = client.patch(f"/threads/{THREAD}", json={"title": "  Brisket timing  "})

    assert response.status_code == 200
    assert response.json()["thread"]["title"] == "Brisket timing"
    assert ("set_title", (THREAD, "Brisket timing")) in service.calls

    for empty in ("", "   "):
        refused = client.patch(f"/threads/{THREAD}", json={"title": empty})
        assert refused.status_code == 400
    assert service.rows[THREAD].title == "Brisket timing"


@pytest.mark.superseded
def test_delete_thread_is_204_ro16(service: StubService, client: TestClient) -> None:
    """204 with no body, and the id passed through untouched so the cascade finds its children.

    Deleting erases checkpoints and every derived expert thread (SV-24) and there is no undo (D6),
    so the id the service is handed has to be the id the human asked for, character for character —
    a normalized or truncated one would cascade over a *different* subtree.

    Superseded (Task 5 rebuilds this): not a rename — ``DELETE /sessions/{id}`` does not exist at
    all, by design ("nothing deletes a session"). There is no successor for this test.
    """
    response = client.delete(f"/threads/{DERIVED}")

    assert response.status_code == 204
    assert response.content == b""
    assert ("delete_thread", (DERIVED,)) in service.calls


@pytest.mark.superseded
def test_proposals_are_listed_and_dismissed_ro19(service: StubService, client: TestClient) -> None:
    """Without retrieval the propose-only path records into a void; without dismiss the queue grows.

    MCP's writes cannot be approved by their caller, so they are recorded as proposals (RT-42)
    instead of hanging on an interrupt. That is only a design if a human can *see* them and clear
    them: a list-only surface makes an ignored proposal permanent, and a queue that only grows stops
    being read at all.
    """
    service.proposals = [proposal("p-1"), proposal("p-2")]

    listed = client.get("/proposals").json()["proposals"]
    assert [p["proposal_id"] for p in listed] == ["p-1", "p-2"]
    assert listed[0]["action"]["allowed_decisions"] == ["approve", "reject"]
    assert listed[0]["thread_id"] == THREAD  # the conversation that produced it (MC-13)

    dismissed = client.delete("/proposals/p-1")

    assert dismissed.status_code == 204
    assert [p["proposal_id"] for p in client.get("/proposals").json()["proposals"]] == ["p-2"]


# --------------------------------------------------------------------------------------
# § starting and resuming a run
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_an_empty_message_is_400_ro11(service: StubService, client: TestClient) -> None:
    """A blank turn must not reach a model, and must not open a stream.

    The body of ``POST /runs`` is ``{"message": str}`` and nothing else. An empty or whitespace-only
    message costs a full turn — eight to twelve model calls — to produce nothing, and because the refusal has
    to be a status code rather than a frame (RO-13), a client can retry it; a 200 whose only content
    is an apologetic ``run.error`` cannot be told from a real failure.

    Superseded (Task 5 rebuilds this): the route is ``POST /threads/{id}/runs``, deleted with the
    rest of the thread-CRUD surface. The empty-message-is-400 principle is re-homed, same test
    name, against ``POST /sessions/{id}/runs`` in ``tests/server/test_session_routes.py``.
    """
    for body in ({"message": ""}, {"message": "   "}, {}):
        response = client.post(f"/threads/{THREAD}/runs", json=body)
        assert response.status_code == 400, body
        assert response.json()["code"] == "invalid_decision"

    assert not [call for call in service.calls if call[0] == "start_run"]


@pytest.mark.superseded
def test_approval_mode_is_not_settable_over_http_ro11(
    service: StubService, client: TestClient
) -> None:
    """``propose_only`` over a human channel is a broken agent, not a mode — so the wire refuses it.

    ``propose_only`` makes Layer 2 auto-reject every gate (RT-42). That is right for MCP, where the
    caller is a program that cannot answer an approval; over HTTP it produces a run that silently
    refuses its own writes and files nothing, and the human sees a turn that "worked" and a knowledge
    base that did not change. Ignoring the field would be worse than rejecting it: the client would
    believe the mode took effect.

    Superseded (Task 6 rebuilds this): ``approval_mode`` has no meaning once gates are gone — the
    operator's instruction is the approval, so there is no gate for a mode to auto-reject.
    """
    response = client.post(
        f"/threads/{THREAD}/runs", json={"message": "file this", "approval_mode": "propose_only"}
    )

    assert response.status_code == 400
    assert "approval_mode" in response.json()["detail"]
    assert service.modes == []  # never reached the service, so no mode was chosen at all


@pytest.mark.superseded
def test_an_interrupt_without_an_interrupt_id_is_400_ro12(
    service: StubService, client: TestClient
) -> None:
    """Answering "the pending approval" without naming it is a lost update with no undo.

    Two channels looking at one approval is the design (D3), not an edge case: the TUI raised it
    this morning, the phone answers it at lunch. If the wire let a client post decisions without
    saying *which* interrupt they answer, a second client's stale screen would approve whatever is
    pending **now** — a different write, already gated for a different reason, applied silently and
    irreversibly (D6). Requiring the id turns that lost update into a clean 409 ``stale_interrupt``.
    """
    response = client.post(
        f"/threads/{THREAD}/interrupt", json={"decisions": [{"type": "approve"}]}
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_decision"
    assert "interrupt_id" in response.json()["detail"]
    assert not [call for call in service.calls if call[0] == "resume"]


@pytest.mark.superseded
def test_an_interrupt_posted_to_the_parent_is_never_redirected_ro14() -> None:
    """An approval is resolved on the thread that owns it — and Layer 3 does not "help".

    An expert's gate raised inside a fan-out parks on ``<librarian-thread>::<agent-id>`` (LB-16), so
    that is where the decisions belong. A transport that noticed the parent had no pending approval
    and forwarded the decisions to the derived thread would be guessing *which* delegate the human
    meant — and with two experts gated in one fan-out it would guess wrong half the time, approving
    one expert's write with the answer given for another's. The Librarian's thread is never left
    interrupted by a delegate, so a post to it is a genuine 409 ``stale_interrupt``.
    """
    service = seed(RaisingService(events=SCRIPT))
    service.error = StaleInterruptError("interrupt 'i-1' is not pending on thread '3f0c9a1e'")

    with client_for(service) as client:
        response = client.post(
            f"/threads/{THREAD}/interrupt",
            json={"interrupt_id": "i-1", "decisions": [{"type": "approve"}]},
        )

    assert response.status_code == 409
    assert response.json()["code"] == "stale_interrupt"
    # The id went to the service exactly as posted: no redirect to the derived thread.
    assert [call for call in service.calls if call[0] == "resume"] == [("resume", (THREAD, "i-1"))]


@pytest.mark.superseded
def test_validation_happens_before_the_stream_opens_ro13(
    service: StubService, client: TestClient
) -> None:
    """A refusal is a status code, not a frame — and nothing is resumed on the way to finding out.

    Once the response has committed a 200 and a ``text/event-stream`` content type, every later
    failure can only be a ``run.error`` frame, which no HTTP client retries and no proxy logs as an
    error. RO-13 therefore orders the work: read the interrupt, validate the decisions, *then*
    become a stream. The second assertion is the one with teeth — a route that validated after
    calling ``resume`` would still return 400 here while having already advanced the graph.

    Superseded (Task 6 rebuilds this): entirely about the ``/interrupt`` route, deleted outright.
    The validate-before-stream principle for ``/runs`` is a separate, surviving assertion —
    ``test_an_empty_message_is_400_ro11`` above.
    """
    for body in (
        {"decisions": [{"type": "approve"}]},  # no interrupt_id
        {"interrupt_id": "i-1"},  # no decisions
        {"interrupt_id": "i-1", "decisions": "approve"},  # decisions not a list
        {"interrupt_id": "i-1", "decisions": ["approve"]},  # a decision that is not an object
    ):
        response = client.post(f"/threads/{THREAD}/interrupt", json=body)
        assert response.status_code == 400, body
        assert response.headers["content-type"].startswith("application/problem+json"), body
        assert "text/event-stream" not in response.headers["content-type"], body

    assert not [call for call in service.calls if call[0] == "resume"]


@pytest.mark.superseded
def test_a_forged_decision_type_is_refused_ro15(service: StubService, client: TestClient) -> None:
    """A client may narrow the decisions it offers; it may never invent one.

    ``allowed_decisions`` is server-side truth (RT-32) and a UI is free to show less of it — Telegram
    drops ``edit`` because editing a document on a phone is impractical. A hand-crafted request going
    the other way is the interesting case: an unknown ``type`` reaching the resume path would be
    matched positionally against a real ``ActionView`` and answered by whatever the harness does with
    an unrecognized decision, which is a gate outcome nobody chose.
    """
    for kind in ("nuke", "APPROVE", None, 7):
        response = client.post(
            f"/threads/{THREAD}/interrupt",
            json={"interrupt_id": "i-1", "decisions": [{"type": kind}]},
        )
        assert response.status_code == 400, kind
        assert response.json()["code"] == "invalid_decision", kind

    assert not [call for call in service.calls if call[0] == "resume"]


@pytest.mark.superseded
def test_a_widened_decision_is_refused_by_the_service_ro15() -> None:
    """The re-validation behind the route reaches the wire as 400, not as a 500 or a stream.

    Whether ``edit`` was *allowed* for a given action is a question only the pending approval can
    answer, so it is checked in ``validate_decisions`` rather than at the route (RO-13). This pins
    the half the transport owns: that the refusal arrives as ``invalid_decision`` with the
    validator's own words, which is what tells a client its cached ``allowed_decisions`` is stale.
    """
    service = seed(RaisingService(events=SCRIPT))
    service.error = InvalidDecisionError("decision 0 is 'edit'; this action allows approve, reject")

    with client_for(service) as client:
        response = client.post(
            f"/threads/{THREAD}/interrupt",
            json={"interrupt_id": "i-1", "decisions": [{"type": "edit", "edited_args": {}}]},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_decision"
    assert response.json()["detail"] == "decision 0 is 'edit'; this action allows approve, reject"


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


@pytest.mark.superseded
def test_an_unmapped_typed_error_is_a_500_that_leaks_nothing_ro20() -> None:
    """A new typed error with no row is loud, and its message never reaches the wire.

    Defaulting an unknown error to anything in the 2xx range would deliver a failure dressed as a
    success — unrecoverable at the client, because there is nothing left to branch on. 500 is the
    construction that makes an unmapped error somebody's problem today. And its *detail* is a fixed
    string: an unexpected exception's message routinely carries a module path, an absolute file path
    or a fragment of a query, and this wire is also read by a Telegram bot.

    Superseded (Task 5 rebuilds this): the route is ``POST /threads/{id}/runs``, deleted. Re-homed,
    same test name, against ``POST /sessions/{id}/runs`` in ``tests/server/test_session_routes.py``.
    """
    service = seed(RaisingService(events=SCRIPT))
    service.error = UnmappedAgentError(
        "pkb.service.runtime exploded reading /Users/someone/kb/topics/Cooking/notes/steak.md"
    )

    with client_for(service) as client:
        response = client.post(f"/threads/{THREAD}/runs", json={"message": "hi"})

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "internal"
    assert body["detail"] == "an unexpected error occurred; see the daemon log"
    for leak in ("pkb.service.runtime", "/Users/someone", "steak.md", "UnmappedAgentError"):
        assert leak not in response.text


@pytest.mark.superseded
def test_an_error_body_is_problem_json_carrying_the_message_verbatim_ro21() -> None:
    """RFC 9457, a stable machine ``code``, and Layer 2's own words — not Layer 3's paraphrase.

    Layer 2's messages already name the thread and say what to do; re-wording them here would be a
    second answer to "what went wrong", which is exactly the discipline MW-13 applies to Layer 1's
    findings one layer down. The content type matters for the same reason the code does: a client —
    or a proxy, or a CLI — can parse ``application/problem+json`` without knowing this project.

    Superseded (Task 5 rebuilds this): the route is ``POST /threads/{id}/runs``, deleted. Re-homed,
    same test name, against ``POST /sessions/{id}/runs`` in ``tests/server/test_session_routes.py``.
    """
    message = "a run is already active on thread '3f0c9a1e'; cancel it or wait for it to finish"
    service = seed(RaisingService(events=SCRIPT))
    service.error = ThreadBusyError(message)

    with client_for(service) as client:
        response = client.post(f"/threads/{THREAD}/runs", json={"message": "hi"})

    assert response.headers["content-type"].split(";")[0] == "application/problem+json"
    assert response.json() == {
        "type": "about:blank",
        "title": "Thread busy",
        "status": 409,
        "code": "thread_busy",
        "detail": message,
        "retryable": True,
    }


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


@pytest.mark.superseded
def test_origin_channel_never_decides_anything_ro22() -> None:
    """D3's promise — a thread started in the TUI is finishable from Telegram — is one ``if`` away
    from being deleted, in exactly the case the design is proudest of.

    A human approves at lunch, on a phone, something the TUI asked about that morning. Any check of
    the form ``if thread.origin_channel != channel: refuse`` makes that a 403, and it would look
    entirely reasonable in review — which is why the rule is enforced structurally instead of by
    taste. ``origin_channel`` is provenance for display, notification targeting and diagnostics, and
    nothing else.

    A ``Compare`` anywhere is included, not just the test of an ``if``: ``allowed = origin ==
    "tui"`` on one line and ``if allowed`` on the next is the same check wearing a hat. The one
    boolean-context use that survives is ``fields.get("origin_channel") or "http"`` in
    ``create_thread`` — a default for a *label* being written, which decides no permission.

    Superseded (Task 7 rebuilds this): ``origin_channel`` disappears from the data model outright —
    a session carries no channel field, channels attach through a separate registry. Task 7 needs an
    analogous structural assertion that channel identity never decides anything about a session.
    """
    offenders: list[str] = []
    mentions = 0
    for package in SCANNED_PACKAGES:
        for path in sorted(package.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            mentions += source.count("origin_channel")
            tree = ast.parse(source, filename=str(path))
            offenders += [
                f"{path}:{node.lineno}"
                for node in _decision_points(tree)
                if _mentions_origin_channel(node)
            ]

    assert offenders == []
    # Non-vacuity: the column is real and these packages do carry it, so the walk above is looking
    # at live code rather than passing because a rename made the identifier disappear.
    assert mentions > 0


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

    body = state.payload(
        agent_count=1, active_runs=0, subscribers=0, threads=(0, 0), proposals_pending=0
    )

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
    body = state.payload(
        agent_count=3, active_runs=0, subscribers=0, threads=(0, 0), proposals_pending=0
    )

    assert token not in str(body)
    assert "[redacted]" in str(body["telegram"]["last_send_error"])
    assert "sendMessage failed" in str(body["telegram"]["last_send_error"]), (
        "the diagnosis survives"
    )
