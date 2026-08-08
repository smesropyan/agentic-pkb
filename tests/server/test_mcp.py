"""The MCP surface (MC-1 … MC-23) — driven in-process by the official SDK.

Every test here speaks real JSON-RPC to a real ``StreamableHTTPSessionManager`` over
``httpx2.ASGITransport``: no socket, no uvicorn, no model. That matters more than convenience.
Four of the rules in this file exist *because* a plausible-looking mount answers HTTP happily and
still fails the handshake — ``app.mount`` 307s (MC-2), a mounted sub-app's lifespan is thrown away
so ``session_manager.run()`` never runs (MC-3), and the SDK's DNS-rebinding lockdown 421s a Host
header without a port (MC-4). None of those are visible to a test that pokes the router; all three
are one ``initialize`` away.

Two mechanics are worth knowing before reading:

* **``ASGITransport`` does not run the lifespan.** :func:`lifespan_running` drives it by hand —
  which is what makes MC-3 assertable in both directions, with and without the session manager.
* **The SDK renamed its model fields in 2.0**: ``is_error``, ``structured_content``,
  ``resource_templates``, ``server_info`` in Python; ``isError``, ``structuredContent`` on the wire.
  The tests read the Python names, so an upgrade that renames them back fails here loudly.

Where a rule's own test assertion says "grep", this file parses the module with :mod:`ast` instead.
A literal grep for ``"interactive"`` (MC-8) matches the module docstring that *explains* why the word
never appears in code, and a literal grep for ``langgraph`` (MC-9) matches ``app.py``'s docstring
about the import-linter contract. Reading the executed strings and the real import statements asks
the question the rule is actually asking, and cannot be silenced by rewording a comment.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, Protocol

import httpx2
import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp_types import CallToolResult

import pkb.server
import pkb.server.app as app_module
import pkb.server.mcp as mcp_module
from pkb.contracts import (
    LIBRARIAN_AGENT_ID,
    ActionView,
    AgentEvent,
    ApprovalPendingError,
    InvalidDecisionError,
    MessageComplete,
    PendingProposal,
    RunEnd,
    RunError,
    RunHandle,
    StaleInterruptError,
    SubagentEnd,
    SubagentStart,
    ThreadBusyError,
    UnknownAgentError,
    UnknownThreadError,
    expert_thread_id,
)
from pkb.core.models import KbSnapshot
from pkb.core.scan import scan
from pkb.server.app import ServerConfig, create_app
from pkb.server.mcp import TOOL_NAMES, build_mcp_server
from pkb.service import RunSubscription
from tests.server.stub import AGENTS, COOKING, GRILLING, LIBRARIAN, NOW, StubService, opener_for

# --------------------------------------------------------------------------------------
# The fixture knowledge base — two topics whose agent ids are the stub catalog's own
# --------------------------------------------------------------------------------------

CONFLICT_NOTE = "Cooking/notes/preheat-the-grill.md"
REVIEW_NOTE = "Reference Grill Basics says preheat for 10 min. Note says 15 min."


def _doc(title: str, topic: str, tags: Sequence[str], *, review_note: str | None = None) -> str:
    """A well-formed PKB document. Layer 1's scanner parses this, so the frontmatter is real."""
    lines = [
        "---",
        f'title: "{title}"',
        f'description: "About {title.lower()}"',
        f'topic: "{topic}"',
        "tags:",
        *(f"  - {tag}" for tag in tags),
        "created: 2024-09-01",
        "updated: 2024-09-01",
        "source_type: note",
    ]
    if review_note is not None:
        lines.append(f'review_note: "{review_note}"')
    lines += ["---", "", f"# {title}", "", "Fifteen minutes on high with the lid closed.", ""]
    return "\n".join(lines)


CLEAN_FILES: Mapping[str, str] = {
    "Cooking/topic.md": _doc("Cooking", "Cooking", ["topic.cooking", "type.summary"]),
    "Cooking/notes/summary.md": _doc("Notes summary", "Cooking", ["topic.cooking", "type.summary"]),
    "Cooking/references/summary.md": _doc(
        "References summary", "Cooking", ["topic.cooking", "type.summary"]
    ),
    "Cooking/sub-topics/Grilling/topic.md": _doc(
        "Grilling", "Grilling", ["topic.cooking.grilling", "type.summary"]
    ),
    "Cooking/sub-topics/Grilling/notes/summary.md": _doc(
        "Notes summary", "Grilling", ["topic.cooking.grilling", "type.summary"]
    ),
}
"""``Cooking`` → ``topic/cooking`` and ``Cooking/sub-topics/Grilling`` → ``topic/cooking/grilling``,
which are exactly the ids :data:`tests.server.stub.AGENTS` publishes. The two halves have to agree:
the adapter intersects the run's participating agents with the snapshot's topics."""

REVIEWED_FILES: Mapping[str, str] = {
    **CLEAN_FILES,
    CONFLICT_NOTE: _doc(
        "Preheat the grill",
        "Cooking",
        ["topic.cooking", "type.note", "status.conflict-review"],
        review_note=REVIEW_NOTE,
    ),
}
"""The same tree with one note under human review — the trigger MC-20 computes deterministically."""

RESOLVED_FILES: Mapping[str, str] = {
    **CLEAN_FILES,
    CONFLICT_NOTE: _doc("Preheat the grill", "Cooking", ["topic.cooking", "type.note"]),
}
"""The same note, same path, tag cleared. MC-20's escalation must self-clear against this."""


def _snapshot(root: Path, files: Mapping[str, str]) -> KbSnapshot:
    root.mkdir(parents=True, exist_ok=True)
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    return scan(root)


@pytest.fixture(scope="session")
def empty_kb(tmp_path_factory: pytest.TempPathFactory) -> KbSnapshot:
    """A snapshot of an empty tree.

    Enough for the mount and discovery tests, which run no tool and therefore never look at the
    knowledge base — giving them a populated one would only slow every test down.
    """
    return scan(tmp_path_factory.mktemp("EmptyKB"))


@pytest.fixture
def clean_kb(tmp_path: Path) -> KbSnapshot:
    """Two topics, nothing under review."""
    return _snapshot(tmp_path / "clean", CLEAN_FILES)


@pytest.fixture
def reviewed_kb(tmp_path: Path) -> KbSnapshot:
    """One Cooking note carrying ``status.conflict-review`` and its ``review_note``."""
    return _snapshot(tmp_path / "reviewed", REVIEWED_FILES)


@pytest.fixture
def resolved_kb(tmp_path: Path) -> KbSnapshot:
    """The same note with the tag cleared — the human resolved it."""
    return _snapshot(tmp_path / "resolved", RESOLVED_FILES)


# --------------------------------------------------------------------------------------
# Scripted services — three knobs the shared stub deliberately does not expose
# --------------------------------------------------------------------------------------

FANOUT: tuple[AgentEvent, ...] = (
    SubagentStart(run_id="run-1", agent_id=COOKING),
    SubagentStart(run_id="run-1", agent_id=GRILLING),
    MessageComplete(run_id="run-1", agent_id=COOKING, text="Fifteen minutes, lid closed."),
    MessageComplete(run_id="run-1", agent_id=GRILLING, text="Ten, on charcoal."),
    SubagentEnd(run_id="run-1", agent_id=COOKING, status="answered"),
    SubagentEnd(run_id="run-1", agent_id=GRILLING, status="declined"),
    RunEnd(run_id="run-1", final_text="Cooking says fifteen; Grilling says ten."),
)
"""A two-expert Librarian turn: roster, per-expert text, per-expert status, merged reply."""


class ScriptedService(StubService):
    """:class:`StubService` plus the three things these rules need and the shared stub omits.

    ``handle_agent`` is the agent the :class:`~pkb.contracts.RunHandle` names. The shared stub always
    says ``librarian``, and a *direct* ask to an expert really does run under that expert's id —
    which is the case MC-19's menu heuristic has to survive.

    ``hang`` is a run whose stream never produces a terminal event. It is the only way to reach
    MC-15's deadline, and it is an :class:`asyncio.Event` that is never set rather than a sleep, so
    the test's wall clock is the deadline and nothing else.

    ``gated`` records a proposal against the run's own thread the way a propose-only run that hit
    the gate table does — MC-13, MC-18 and MC-21 all need one to exist with a real thread id.
    """

    def __init__(
        self,
        *,
        handle_agent: str = LIBRARIAN,
        hang: bool = False,
        gated: tuple[str, str] | None = None,
        raise_on_run: BaseException | None = None,
        strict_threads: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.handle_agent = handle_agent
        self.hang = hang
        self.gated = gated
        self.raise_on_run = raise_on_run
        self.strict_threads = strict_threads
        self.never = asyncio.Event()

    async def start_run(
        self,
        thread_id: str,
        message: str,
        *,
        approval_mode: str = "interactive",
        run_id: str | None = None,
    ) -> RunSubscription:
        self.calls.append(("start_run", (thread_id, message)))
        self.modes.append(approval_mode)
        if self.raise_on_run is not None:
            raise self.raise_on_run
        if self.strict_threads and thread_id not in self.rows:
            raise UnknownThreadError(f"no thread {thread_id!r}")
        if self.busy:
            raise ThreadBusyError(f"a run is already active on thread {thread_id!r}")
        if self.gated is not None:
            path, reason = self.gated
            self.proposals.append(
                PendingProposal(
                    proposal_id="proposal-1",
                    agent_id=self.handle_agent,
                    thread_id=thread_id,
                    action=ActionView(
                        tool="write_file",
                        args={"file_path": path},
                        description=f"--- a/{path}\n+++ b/{path}",
                        allowed_decisions=("approve", "edit", "reject"),
                        reason=reason,
                    ),
                    created_at=NOW,
                )
            )

        async def starter() -> tuple[RunHandle, AsyncIterator[AgentEvent]]:
            handle = RunHandle(run_id=self.run_id, agent_id=self.handle_agent, thread_id=thread_id)

            async def stream() -> AsyncIterator[AgentEvent]:
                if self.hang:
                    await self.never.wait()
                for event in self.events:
                    yield event

            return handle, stream()

        return await self.runs.start(thread_id, starter)


# --------------------------------------------------------------------------------------
# Driving the daemon in-process
# --------------------------------------------------------------------------------------

BASE_URL = "http://127.0.0.1:8000"
"""The one base URL the SDK's DNS-rebinding lockdown admits: localhost **with a port** (MC-4)."""

PORTLESS_URL = "http://127.0.0.1"
TESTSERVER_URL = "http://testserver"

INITIALIZE: Mapping[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pkb-tests", "version": "0"},
    },
}
JSONRPC_HEADERS: Mapping[str, str] = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}


def build_app(
    service: StubService, *, snapshot: KbSnapshot, deadline: float | None = None
) -> FastAPI:
    """The daemon app over a stub service and a real Layer 1 snapshot.

    ``deadline`` reaches :func:`~pkb.server.mcp.build_mcp_server` by patching the name ``create_app``
    resolves — the tool deadline is a build-time argument with a five-minute default, and MC-15 is
    untestable inside a test suite that may not sleep.
    """
    if deadline is None:
        app = create_app(opener_for(service), config=ServerConfig())
    else:

        def with_deadline(service_of: Any, snapshot_of: Any, **_: Any) -> Any:
            return build_mcp_server(service_of, snapshot_of, deadline=deadline)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(app_module, "build_mcp_server", with_deadline)
            app = create_app(opener_for(service), config=ServerConfig())
    app.state.snapshot = lambda: snapshot
    return app


@contextlib.asynccontextmanager
async def lifespan_running(app: FastAPI) -> AsyncIterator[None]:
    """Run the ASGI lifespan by hand, because ``ASGITransport`` does not (MC-3, AP-3).

    This is not a convenience: ``streamable_http_app()`` returns a Starlette app whose *whole*
    lifespan is ``session_manager.run()``, the daemon adopts that step itself, and a transport that
    skips lifespans would leave every test passing against a server that never started.
    """
    inbox: asyncio.Queue[dict[str, str]] = asyncio.Queue()
    outbox: list[dict[str, str]] = []

    async def send(message: dict[str, str]) -> None:
        outbox.append(message)

    task = asyncio.create_task(
        app({"type": "lifespan", "asgi": {"version": "3.0"}}, inbox.get, send)  # type: ignore[arg-type]
    )
    await inbox.put({"type": "lifespan.startup"})
    for _ in range(400):
        if outbox:
            break
        await asyncio.sleep(0.005)
    assert outbox and outbox[-1]["type"] == "lifespan.startup.complete", outbox
    try:
        yield
    finally:
        await inbox.put({"type": "lifespan.shutdown"})
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(task, 2.0)
        task.cancel()


@contextlib.asynccontextmanager
async def mcp_session(app: FastAPI, base_url: str = BASE_URL) -> AsyncIterator[ClientSession]:
    """An initialized SDK session against the mounted ``/mcp`` route."""
    transport = httpx2.ASGITransport(app=app)
    async with (
        httpx2.AsyncClient(transport=transport, base_url=base_url) as http,
        streamable_http_client(f"{base_url}/mcp", http_client=http) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


class Connect(Protocol):
    """The fixture's shape: hand it a service, ``async with`` it for an initialized MCP session.

    A context manager rather than a plain ``await`` on purpose. The SDK client is built on anyio
    task groups, and a cancel scope must be exited by the task that entered it — parking these on an
    ``AsyncExitStack`` owned by the fixture unwinds them in pytest's finalizer task instead of the
    test's, and every test dies in ``anyio`` with a message about cancel scopes rather than about
    MCP.
    """

    def __call__(
        self,
        service: StubService,
        *,
        snapshot: KbSnapshot | None = None,
        deadline: float | None = None,
    ) -> AbstractAsyncContextManager[ClientSession]: ...


@pytest.fixture
def connect(clean_kb: KbSnapshot) -> Connect:
    """Build the app, drive its lifespan, initialize a session — and tear all three down in order."""

    @contextlib.asynccontextmanager
    async def _connect(
        service: StubService,
        *,
        snapshot: KbSnapshot | None = None,
        deadline: float | None = None,
    ) -> AsyncIterator[ClientSession]:
        app = build_app(service, snapshot=snapshot or clean_kb, deadline=deadline)
        async with lifespan_running(app), mcp_session(app) as session:
            yield session

    return _connect


async def call(session: ClientSession, tool: str, **arguments: Any) -> CallToolResult:
    """One tool call, narrowed to the wire type the ``is_error`` contract lives on."""
    result = await session.call_tool(tool, arguments)
    assert isinstance(result, CallToolResult), result
    return result


def outcome(result: CallToolResult) -> dict[str, Any]:
    """The discriminated outcome inside a tool result — the union MC-14 and MC-20 share."""
    assert result.structured_content is not None, result.content
    body = result.structured_content["outcome"]
    assert isinstance(body, dict)
    return body


# --------------------------------------------------------------------------------------
# Reading the source the way the rules mean, not the way grep would
# --------------------------------------------------------------------------------------

MCP_SOURCE = Path(mcp_module.__file__)
SERVER_SOURCES = sorted(Path(pkb.server.__file__).parent.glob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def code_strings(path: Path) -> list[str]:
    """Every string literal the module **executes** — docstrings and comments excluded.

    Comments never reach the AST; docstrings are the only string a module evaluates purely to
    document itself. What is left is every string that can become behaviour.
    """
    tree = _parse(path)
    documentation: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                documentation.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in documentation
    ]


def imported_modules(path: Path) -> set[str]:
    """Every module this file names in an ``import`` — including inside a function body."""
    names: set[str] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def attribute_chains(path: Path) -> set[str]:
    """Two-deep attribute accesses, as ``owner.attr`` — enough to spot ``x.final_text.split``."""
    return {
        f"{node.value.attr}.{node.attr}"
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
    }


HARNESS_ROOTS = frozenset(
    {"langchain", "langchain_core", "langgraph", "deepagents", "langchain_ollama"}
)
MODEL_CLIENT_ROOTS = frozenset({"openai", "anthropic", "ollama", "litellm", "boto3"})
HTTP_CLIENT_ROOTS = frozenset({"httpx", "httpx2", "requests", "aiohttp", "urllib3", "urllib"})


def _roots(path: Path) -> set[str]:
    return {name.split(".")[0] for name in imported_modules(path)}


# --------------------------------------------------------------------------------------
# § The mount (MC-1 … MC-4)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_to_mcp_is_two_hundred_with_no_redirect_and_a_session_id_mc2(
    empty_kb: KbSnapshot,
) -> None:
    """``/mcp`` is the URL arch §6 publishes, and it must answer *at that URL*.

    ``app.mount("/mcp", …)`` looks right and is not: the sub-app's router 307s ``/mcp`` → ``/mcp/``.
    The SDK client follows the redirect and hides it; a stricter client — which is exactly what an
    external agent runs — does not, and the knowledge base is simply unreachable from it. The mount
    is therefore a bare ``Route``, and the two things that prove it are an empty redirect history
    and a session id issued on the first POST rather than on the one after the bounce.
    """
    app = build_app(StubService(), snapshot=empty_kb)
    async with lifespan_running(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url=BASE_URL) as http:
            response = await http.post("/mcp", json=INITIALIZE, headers=dict(JSONRPC_HEADERS))

    assert response.status_code == 200
    assert list(response.history) == [], "a 307 to /mcp/ means the mount is app.mount, not a Route"
    assert response.headers.get("mcp-session-id"), "initialize must issue a session id"


@pytest.mark.asyncio
async def test_initialize_fails_when_the_lifespan_never_ran_mc3(empty_kb: KbSnapshot) -> None:
    """The lifespan **is** the server. Without it the route exists and serves nothing.

    ``streamable_http_app()`` builds a Starlette app whose lifespan is ``session_manager.run()``,
    and mounting throws that lifespan away. The failure is not a 404 or a 500 the daemon would log
    as a routing mistake — it is ``RuntimeError: Task group is not initialized`` from deep inside
    the session manager, on every request, forever. This test is the negative half of MC-3: it is
    what makes the positive half mean something rather than merely pass.
    """
    app = build_app(StubService(), snapshot=empty_kb)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url=BASE_URL) as http:
        with pytest.raises(RuntimeError, match="Task group is not initialized"):
            await http.post("/mcp", json=INITIALIZE, headers=dict(JSONRPC_HEADERS))


@pytest.mark.asyncio
async def test_the_handshake_completes_when_the_lifespan_drives_the_manager_mc3(
    empty_kb: KbSnapshot,
) -> None:
    """With the daemon's own lifespan entering ``session_manager.run()``, the handshake completes."""
    app = build_app(StubService(), snapshot=empty_kb)
    async with lifespan_running(app), mcp_session(app) as session:
        assert session.server_info is not None
        assert session.server_info.name == "pkb"


@pytest.mark.asyncio
async def test_the_sdk_two_point_zero_field_names_are_snake_case_mc1(empty_kb: KbSnapshot) -> None:
    """MC-1 pins the SDK at 2.0.0, whose Python fields are snake_case while the wire stays camel.

    Every assertion in this file reads ``is_error``, ``structured_content``, ``resource_templates``
    and ``server_info``. If an upgrade renames them back the failure should be one obvious test
    rather than thirty confusing ones, and a `getattr` fallback in the adapter would silently paper
    over a protocol change.
    """
    app = build_app(StubService(), snapshot=empty_kb)
    async with lifespan_running(app), mcp_session(app) as session:
        listing = await session.list_tools()
        tool = next(t for t in listing.tools if t.name == "pkb_ask")

    assert isinstance(tool.input_schema, dict)
    assert "question" in tool.input_schema["properties"]


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        pytest.param(BASE_URL, 200, id="localhost-with-a-port"),
        pytest.param(TESTSERVER_URL, 421, id="testserver"),
        pytest.param(PORTLESS_URL, 421, id="localhost-without-a-port"),
    ],
)
@pytest.mark.asyncio
async def test_dns_rebinding_lockdown_stays_on_and_the_client_is_pinned_mc4(
    empty_kb: KbSnapshot, base_url: str, expected: int
) -> None:
    """The daemon keeps ``allowed_hosts`` on; the *test client* is what gets pinned.

    ``streamable_http_app(host="127.0.0.1")`` auto-enables ``allowed_hosts=["127.0.0.1:*", …]``, and
    the daemon binds localhost anyway, so the DNS-rebinding protection costs nothing and is the
    difference between a local knowledge base and one any web page can POST to. The consequence is
    a test-only trap worth keeping visible: the allow-list patterns all carry ``:*``, so a **portless**
    ``http://127.0.0.1`` is rejected exactly as ``http://testserver`` is. A future test that drops
    the ``:8000`` gets a 421 that looks like a routing bug and is not one.
    """
    app = build_app(StubService(), snapshot=empty_kb)
    async with lifespan_running(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url=base_url) as http:
            response = await http.post("/mcp", json=INITIALIZE, headers=dict(JSONRPC_HEADERS))

    assert response.status_code == expected


# --------------------------------------------------------------------------------------
# § Discovery (MC-5 … MC-7)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exactly_four_tools_and_no_fifth_mc5(empty_kb: KbSnapshot) -> None:
    """Four tools, and the absences are the design.

    No ``pkb_approve``: an external agent cannot satisfy a human gate, and a tool that let it would
    make "human content wins" a setting rather than an invariant. No write tool either — every write
    goes through the agent layer, where the gate table and the validators live. A fifth tool is how
    that boundary erodes, one convenience at a time, so the assertion is equality and not
    containment.
    """
    app = build_app(StubService(), snapshot=empty_kb)
    async with lifespan_running(app), mcp_session(app) as session:
        listing = await session.list_tools()

    assert [tool.name for tool in listing.tools] == list(TOOL_NAMES)
    assert TOOL_NAMES == ("pkb_ask", "pkb_ingest", "pkb_research_pack", "pkb_implementation_pack")


@pytest.mark.asyncio
async def test_two_static_resources_and_one_template_mc6(empty_kb: KbSnapshot) -> None:
    """A template is invisible to a client that only calls ``list_resources()``.

    The MCP protocol keeps concrete resources and URI templates in two different listings, and
    ``pkb://proposals/{proposal_id}`` lives only in the second. That split is the gap this rule
    exists to close: RG-9 forbids fuzzy-matching an agent id, so a caller that cannot *enumerate*
    can only guess, and a caller that never learns a proposal's URI shape cannot follow README
    Part 4's feedback loop at all. Both listings are asserted here precisely because checking one
    would leave the other free to disappear.
    """
    app = build_app(StubService(), snapshot=empty_kb)
    async with lifespan_running(app), mcp_session(app) as session:
        resources = await session.list_resources()
        templates = await session.list_resource_templates()

    assert [str(r.uri) for r in resources.resources] == ["pkb://agents", "pkb://proposals"]
    assert [t.uri_template for t in templates.resource_templates] == [
        "pkb://proposals/{proposal_id}"
    ]


@pytest.mark.asyncio
async def test_the_agents_resource_publishes_the_ids_the_tools_accept_mc6(
    connect: Connect,
) -> None:
    """Discovery is only useful if it yields the *same* ids the tools take verbatim.

    An external agent reads ``pkb://agents``, picks an id and passes it straight to ``pkb_ask``. If
    the resource rendered display names, folder paths or tags instead, every call would fail on an
    id the caller had just been handed — and MC-16 forbids the adapter from guessing what was meant.
    """
    service = ScriptedService(events=list(FANOUT))
    async with connect(service) as session:
        result = await session.read_resource("pkb://agents")
        payload = json.loads(result.contents[0].text)  # type: ignore[union-attr]

        assert [a["agent_id"] for a in payload["agents"]] == [d.agent_id for d in AGENTS]
        answered = await call(session, "pkb_ask", question="how long?", agent_id=COOKING)
        assert answered.is_error is False


def test_the_adapter_pulls_no_http_client_and_no_harness_mc7() -> None:
    """MCP is a transport, not a second client of the daemon.

    D9 is explicit that an adapter reaches the service by calling it, never by making an HTTP round
    trip back into the same process — a bot that curls its own daemon is a second process to
    supervise, a second failure mode and a second copy of the error table. And I2 keeps every
    transport free of the harness, so the import-linter contract stays a real check rather than an
    ``allow_indirect_imports`` rubber stamp.
    """
    roots = _roots(MCP_SOURCE)

    assert not roots & HTTP_CLIENT_ROOTS
    assert not roots & HARNESS_ROOTS
    assert not any(name.startswith("pkb.agents") for name in imported_modules(MCP_SOURCE))


# --------------------------------------------------------------------------------------
# § The channel's mode (MC-8)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_run_is_propose_only_mc8(connect: Connect) -> None:
    """The approval mode belongs to the channel, and behind ``/mcp`` there is no human.

    Interactive mode parks a run on an ``interrupt`` and waits for a decision. There is nobody on
    this call path to make one, so an interactive MCP write that gated would hang until the deadline
    and then be cancelled — a lost turn that looks like a timeout. The contract is stronger than
    "usually propose_only": MCP must see **zero** interrupt events, which is only true if no path
    can pass the other mode. No tool exposes it as an argument, either.
    """
    service = ScriptedService(events=list(FANOUT))
    async with connect(service) as session:
        await call(session, "pkb_ask", question="how long?")
        assert service.modes == ["propose_only"]

        await call(session, "pkb_ingest", content="Preheat for twelve minutes.")
        assert service.modes == ["propose_only", "propose_only"]

        listing = await session.list_tools()
        for tool in listing.tools:
            assert "approval_mode" not in tool.input_schema.get("properties", {})


def test_the_word_interactive_is_in_no_executable_string_mc8() -> None:
    """The grep MC-8 asks for, aimed at code rather than at prose.

    A literal ``grep interactive src/pkb/server/mcp.py`` matches the module docstring that explains
    why the word never appears in code, so the rule's own check would fail on a correct module. What
    the rule means is that no *executed* string in the adapter is the other approval mode — because
    the moment one is, a gated MCP write parks on an interrupt nobody will ever answer.
    """
    strings = code_strings(MCP_SOURCE)

    assert not [text for text in strings if "interactive" in text.lower()]
    assert "propose_only" in strings


# --------------------------------------------------------------------------------------
# § The reply (MC-9, MC-10)
# --------------------------------------------------------------------------------------

VERBATIM = (
    "## Cooking\n\n- Fifteen minutes, lid closed — measured on a three-burner gas grill.\n"
    "\n## Grilling\n\n> declined: no charcoal experience on file.\n"
    "\n\u00a0a non-breaking space, and a trailing plain one \n"
)
"""Headings, an em dash, a blockquote, a non-breaking space and a trailing space — everything a
well-meaning "let me just tidy this up" would eat."""


@pytest.mark.asyncio
async def test_the_answer_is_run_end_final_text_byte_for_byte_mc9(connect: Connect) -> None:
    """The transport reports the outcome; it does not have opinions about it.

    ``RunEnd.final_text`` is the attributed merge LB-18 pins with a golden test — one section per
    expert, verbatim, with declines shown as declines. A transport that shortened it, re-wrapped it
    or summarized it would be telling the caller something no expert said, which is the same lie
    LB-18 exists to prevent, one layer up. Byte-for-byte is the only assertion that catches a
    "harmless" ``.strip()``.
    """
    service = ScriptedService(events=[RunEnd(run_id="run-1", final_text=VERBATIM)])
    async with connect(service) as session:
        body = outcome(await call(session, "pkb_ask", question="how long?"))

        assert body["answer"] == VERBATIM


def test_layer_three_constructs_no_model_client_mc9() -> None:
    """``pkb/server`` holds no model client at all, so it *cannot* re-word an answer.

    SV-25 allows Layer 3 exactly one model call — the thread title — and routes even that through
    the runtime. Nothing else: not the merge, not a pack, not agent selection. Asserted as an import
    fact rather than a behaviour because the failure it guards against is a future edit that reaches
    for a client "just to tidy the reply", and by the time that is observable in an answer it is
    already shipping.
    """
    for source in SERVER_SOURCES:
        roots = _roots(source)
        assert not roots & MODEL_CLIENT_ROOTS, source
        assert not roots & HARNESS_ROOTS, source


@pytest.mark.asyncio
async def test_experts_are_assembled_from_the_event_stream_mc10(connect: Connect) -> None:
    """The roster, the statuses, the per-expert text and the thread ids all come from events.

    Parsing ``RunEnd.final_text`` would work — and would quietly make ``merge_reply``'s rendering
    format a wire protocol, so LB-18's golden test would stop pinning a human-readable answer and
    start pinning a parser's input. The events already carry everything: ``SubagentStart`` the
    roster, ``SubagentEnd`` the status, each expert's own ``MessageComplete`` its text, and SS-10's
    derivation the thread id — which is what makes "continue with the Grilling expert" a real link
    rather than a suggestion.
    """
    service = ScriptedService(events=list(FANOUT))
    async with connect(service) as session:
        body = outcome(await call(session, "pkb_ask", question="how long?"))
        thread_id = body["thread_id"]

        assert [e["agent_id"] for e in body["experts"]] == [COOKING, GRILLING]
        assert [e["thread_id"] for e in body["experts"]] == [
            expert_thread_id(thread_id, COOKING),
            expert_thread_id(thread_id, GRILLING),
        ]
        assert [e["status"] for e in body["experts"]] == ["answered", "declined"]
        assert [e["text"] for e in body["experts"]] == [
            "Fifteen minutes, lid closed.",
            "Ten, on charcoal.",
        ]


@pytest.mark.asyncio
async def test_no_subagent_events_means_no_experts_however_the_reply_reads_mc10(
    connect: Connect,
) -> None:
    """The complement, and the one an accidental parser fails.

    Here the merged reply is *shaped* exactly like a two-expert fan-out — ``merge_reply``'s own
    headings, one titled section per expert — but no ``SubagentStart`` was ever emitted. Anything
    reading the prose would happily report two experts that never ran, complete with derived thread
    ids pointing at conversations that do not exist and that a caller would then try to continue.
    Reading the stream reports none.
    """
    service = ScriptedService(
        events=[
            RunEnd(run_id="run-1", final_text="## Cooking\n\nFifteen.\n\n## Grilling\n\nTen.\n")
        ]
    )
    async with connect(service) as session:
        body = outcome(await call(session, "pkb_ask", question="how long?"))

        assert body["status"] == "answered"
        assert body["experts"] == []


def test_nothing_in_layer_three_parses_the_merged_reply_mc10() -> None:
    """No regex engine in a module that *sees a reply*, and no method ever called on ``final_text``.

    Two cheap facts that together make the parse impossible rather than merely absent: the modules
    that handle a run's output import no ``re``, and ``final_text`` is only ever read and passed on
    — never split, never scanned for headings. A future edit that adds either has to delete this
    test first, which is the conversation the rule wants to force.

    **Scoped to the reply path rather than to the package.** It was every file under ``pkb/server``,
    which is a proxy: `re` in a module that never touches a run cannot parse a reply. The proxy then
    forbade an unrelated correct thing — the credential redaction on ``/health``, which exists
    because that endpoint is unauthenticated and published a bot token verbatim — and a rule that
    blocks a security fix for a reason unrelated to its own is a rule that gets deleted wholesale
    rather than narrowed. Widen it again the moment another module starts handling a reply.
    """
    reply_path = [
        source
        for source in SERVER_SOURCES
        if source.name in {"mcp.py", "sse.py", "routes.py", "telegram.py"}
    ]
    assert reply_path, "the reply-path module set went stale"
    for source in reply_path:
        assert "re" not in _roots(source), source
    assert not [chain for chain in attribute_chains(MCP_SOURCE) if chain.startswith("final_text.")]


# --------------------------------------------------------------------------------------
# § Threads (MC-11 … MC-13)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_omitted_thread_id_creates_a_real_mcp_thread_and_returns_it_mc11(
    connect: Connect,
) -> None:
    """Every MCP call runs on a real, durable thread — never on a scratch context.

    D3's promise is that a conversation is addressable from any channel, so the id has to come back
    or the caller's next question starts from nothing. And SV-10 says callers never mint ids: an id
    minted by an external agent is one the daemon cannot guarantee is unique, unused, or not already
    someone else's conversation.
    """
    service = ScriptedService(events=list(FANOUT))
    async with connect(service) as session:
        body = outcome(await call(session, "pkb_ask", question="how long?"))

        assert body["thread_id"] in service.rows
        assert service.rows[body["thread_id"]].origin_channel == "mcp"


@pytest.mark.asyncio
async def test_the_returned_thread_id_continues_the_same_conversation_mc11(
    connect: Connect,
) -> None:
    """A caller that passes the id back gets history; the adapter must not quietly start over.

    The failure this prevents is subtle and expensive: a second ``create_thread`` would answer the
    follow-up with no memory of the first turn, and the caller — which *did* pass the id — would
    read a confident answer to a question it thought had context.
    """
    service = ScriptedService(events=list(FANOUT))
    async with connect(service) as session:
        first = outcome(await call(session, "pkb_ask", question="how long?"))
        second = outcome(
            await call(
                session, "pkb_ask", question="and on charcoal?", thread_id=first["thread_id"]
            )
        )

        assert second["thread_id"] == first["thread_id"]
        assert [c for c in service.calls if c[0] == "create_thread"] == [
            ("create_thread", (LIBRARIAN,))
        ]
        assert [c[1][0] for c in service.calls if c[0] == "start_run"] == [
            first["thread_id"],
            first["thread_id"],
        ]


@pytest.mark.asyncio
async def test_an_unknown_thread_id_errors_rather_than_being_created_mc11(
    connect: Connect,
) -> None:
    """A supplied id must already exist. Creating it on demand would forge a conversation.

    "Continue thread X" and "start a thread and call it X" are different requests, and only one of
    them is something an external caller may ask for (SV-10). Silently upgrading the first into the
    second also destroys the diagnostic: the caller sees an empty history and blames the knowledge
    base rather than its own stale id.
    """
    service = ScriptedService(events=list(FANOUT), strict_threads=True)
    async with connect(service) as session:
        body = outcome(
            await call(session, "pkb_ask", question="how long?", thread_id="no-such-thread")
        )

        assert body["code"] == "unknown_thread"
        assert not [c for c in service.calls if c[0] == "create_thread"]
        assert service.rows == {}


@pytest.mark.parametrize(
    ("thread_id", "accepted"),
    [
        pytest.param(f"t{'::'}{COOKING}", False, id="derived-expert-thread"),
        pytest.param(f"scan:{COOKING}:abc", False, id="maintenance-scan-thread"),
        pytest.param("2f0d4b6e-2f4b-4a1e-9c1a-6f7a1d2b3c4d", True, id="a-minted-uuid"),
    ],
)
@pytest.mark.asyncio
async def test_derived_and_maintenance_thread_ids_are_refused_mc12(
    connect: Connect, thread_id: str, accepted: bool
) -> None:
    """Two id shapes are functions of something the daemon already holds, and are not addressable.

    ``<parent>::<agent-id>`` is the thread the Librarian derives when it routes; ``scan:<agent>:<uuid>``
    is machine bookkeeping for a conflict scan. Accepting either from outside would let an external
    agent write into a human's conversation with an expert, or into a maintenance run, neither of
    which it owns — and both of which the human would later read as their own history. The check is
    on *shape*, before any service call, so nothing exists to clean up afterwards.
    """
    service = ScriptedService(events=list(FANOUT))
    async with connect(service) as session:
        result = await call(session, "pkb_ask", question="how long?", thread_id=thread_id)
        body = outcome(result)

        if accepted:
            assert result.is_error is False
            assert body["status"] == "answered"
        else:
            assert result.is_error is True
            assert body["code"] == "invalid_argument"
            assert not service.calls, "a refused shape must not reach the service at all"


@pytest.mark.asyncio
async def test_an_mcp_thread_is_listed_and_labelled_not_hidden_mc13(connect: Connect) -> None:
    """A proposal the human must review is meaningless without the conversation that produced it.

    Hiding robot threads is how a knowledge base fills with writes nobody can trace: the human sees
    "approve this summary rewrite?" with no way to ask *why*. So MCP threads are listed like any
    other and carry ``origin_channel="mcp"`` — a label for provenance, never an authorization check
    (RO-22).
    """
    service = ScriptedService(
        events=list(FANOUT), gated=("Cooking/notes/summary.md", "breadth-approval")
    )
    async with connect(service) as session:
        body = outcome(await call(session, "pkb_ingest", content="Preheat for twelve minutes."))
        threads = await service.list_threads()

        assert [t.thread_id for t in threads] == [body["thread_id"]]
        assert [t.origin_channel for t in threads] == ["mcp"]
        assert [p.thread_id for p in service.proposals] == [body["thread_id"]]


# --------------------------------------------------------------------------------------
# § Errors (MC-14) and bounds (MC-15, MC-23)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        pytest.param(UnknownAgentError("no agent 'topic/x'"), "unknown_agent", id="unknown_agent"),
        pytest.param(UnknownThreadError("no thread 't'"), "unknown_thread", id="unknown_thread"),
        pytest.param(ThreadBusyError("a run is active"), "thread_busy", id="thread_busy"),
        pytest.param(ApprovalPendingError("parked"), "approval_pending", id="approval_pending"),
        pytest.param(StaleInterruptError("stale"), "stale_interrupt", id="stale_interrupt"),
        pytest.param(InvalidDecisionError("bad"), "invalid_decision", id="invalid_decision"),
    ],
)
@pytest.mark.asyncio
async def test_typed_errors_are_returned_with_a_machine_code_mc14(
    connect: Connect, error: BaseException, code: str
) -> None:
    """The caller is a program, so the failure has to be branchable — and reachable.

    Two halves. **Returned, not raised**: every exception path in this SDK produces
    ``structured_content: null`` and a message prefixed with the tool's own name, so a raise cannot
    carry a code at all — the caller would be left regex-matching English. **The same table as
    HTTP**: three of these conditions share 409 and a client's correct reaction to each differs
    (retry later, render the approval, refetch the interrupt), so two mapping tables would drift and
    a client would spin on ``approval_pending`` forever waiting for a human it never told anyone
    about.
    """
    service = ScriptedService(raise_on_run=error)
    async with connect(service) as session:
        result = await call(session, "pkb_ask", question="how long?")

        assert result.is_error is True
        assert outcome(result)["code"] == code
        assert outcome(result)["message"] == str(error)


@pytest.mark.asyncio
async def test_a_failed_run_is_flagged_like_every_other_coded_failure_mc14(
    connect: Connect,
) -> None:
    """``Failed`` has one meaning, so it must have one wire shape.

    ``_failure`` and ``_failure_from`` both return ``CallToolResult(is_error=True)`` around a
    ``Failed`` outcome; the ``RunError`` path returns the identical payload inside a *successful*
    result. MC-20's whole argument is that an escalation is ``is_error == false`` **because** a
    well-behaved agent retries errors — which presumes errors are flagged. Here a retryable model
    failure arrives unflagged, so the agent that would have retried does not, and the human's
    question is dropped on the floor with a status field nobody read.
    """
    service = ScriptedService(
        events=[RunError(run_id="run-1", message="the model timed out", retryable=True)]
    )
    async with connect(service) as session:
        result = await call(session, "pkb_ask", question="how long?")

        assert outcome(result)["code"] == "run_failed"
        assert outcome(result)["retryable"] is True
        assert result.is_error is True


@pytest.mark.asyncio
async def test_a_run_that_never_ends_times_out_and_is_cancelled_mc15(connect: Connect) -> None:
    """An MCP call is bounded and it cleans up after itself.

    Unbounded is not an option: an external agent issues calls faster than a human and a hung graph
    would hold a thread, a cloud-model slot and a subscriber forever. ``cancel(run_id)`` rather than
    "stop reading" is the point — a Librarian fan-out drives several graphs under one run id, and
    only cancelling the family stops the experts still writing.
    """
    service = ScriptedService(hang=True)
    async with connect(service, deadline=0.1) as session:
        body = outcome(await call(session, "pkb_ask", question="how long?"))

        assert body["status"] == "timeout"
        assert service.cancelled == ["run-1"]
        assert body["thread_id"] in service.rows


@pytest.mark.asyncio
async def test_a_timeout_is_flagged_like_every_other_coded_failure_mc15(connect: Connect) -> None:
    """A ``retryable`` failure the caller cannot see is a failure the caller will not retry."""
    service = ScriptedService(hang=True)
    async with connect(service, deadline=0.1) as session:
        result = await call(session, "pkb_ask", question="how long?")

        assert outcome(result)["retryable"] is True
        assert result.is_error is True


@pytest.mark.asyncio
async def test_mcp_gets_no_exemption_from_the_active_run_registry_mc23(connect: Connect) -> None:
    """A robot caller is still one caller, and the busy check is not negotiable for it.

    RT-39 refuses a second run on a thread that already has one, because two graphs writing the same
    conversation interleave checkpoints and corrupt history. An external agent hits that far more
    often than a human does — it does not wait for the previous answer — so the transport most
    tempted to add an exemption is exactly the one that must not have one. The refusal comes back
    as a code the caller can act on, and nothing was created on the way.
    """
    service = ScriptedService(events=list(FANOUT))
    thread = await service.create_thread(LIBRARIAN, origin_channel="mcp")
    service.busy = True
    async with connect(service) as session:
        result = await call(session, "pkb_ask", question="how long?", thread_id=thread.thread_id)

        assert result.is_error is True
        assert outcome(result)["code"] == "thread_busy"
        assert list(service.rows) == [thread.thread_id]


# --------------------------------------------------------------------------------------
# § Ids and hints (MC-16, MC-17)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("topic", "accepted"),
    [
        pytest.param(GRILLING, True, id="the-agent-id"),
        pytest.param("Cooking/sub-topics/Grilling", False, id="a-folder-path"),
        pytest.param("topic.cooking.grilling", False, id="a-topic-tag"),
        pytest.param("Grilling", False, id="a-display-name"),
    ],
)
@pytest.mark.asyncio
async def test_an_id_is_taken_verbatim_and_never_fuzzy_matched_mc16(
    connect: Connect, topic: str, accepted: bool
) -> None:
    """``pkb_implementation_pack(topic=…)`` takes an **agent id**, and only an agent id.

    Three near-misses are refused here and each is a thing a caller genuinely reaches for: the
    folder path, the topic tag, the display name. None is resolved by similarity, because a pack
    silently built for the wrong topic is indistinguishable from a correct one at the consumer — an
    implementation agent reads it top-down and trusts it. The refusal names the id it was given and
    lists the ones that exist, so the caller can fix it in one step instead of guessing back.
    """
    service = ScriptedService()
    async with connect(service) as session:
        result = await call(session, "pkb_implementation_pack", topic=topic)
        body = outcome(result)

        if accepted:
            assert result.is_error is False
            assert body["scope"] == [GRILLING]
        else:
            assert result.is_error is True
            assert body["code"] == "unknown_topic"
            assert topic in body["message"], "the refusal must name the id it was handed"
            assert GRILLING in body["message"], "and the ids that do exist"


@pytest.mark.asyncio
async def test_ingest_always_enters_at_the_librarian_with_hints_as_context_mc17(
    connect: Connect,
) -> None:
    """A hint is context, never a shortcut past classification.

    Information fans out exactly as questions do (D11): several experts may file their own
    extraction of the same material and any may decline. A ``topic_hint`` that *selected* an expert
    would hand an external agent the one decision this system refuses to guess at any layer, and a
    ``source_type`` written straight into frontmatter would let a caller stamp provenance the
    Librarian never verified. So both are appended as labelled advisory text on the item, and the
    content itself crosses unmodified.
    """
    service = ScriptedService(events=list(FANOUT))
    async with connect(service) as session:
        content = "Ribeye, reverse-seared, twelve minutes at 120C.\n\n- second line, kept verbatim"
        await call(
            session,
            "pkb_ingest",
            content=content,
            source_type="retrospective",
            topic_hint="Cooking",
        )

        (thread_id, message) = next(c[1] for c in service.calls if c[0] == "start_run")
        assert service.rows[thread_id].agent_id == LIBRARIAN_AGENT_ID
        assert message.startswith(content), "the item crosses unmodified"
        trailer = message[len(content) :]
        assert "advisory" in trailer
        assert "- source type: retrospective" in trailer
        assert "- topic hint: Cooking" in trailer


# --------------------------------------------------------------------------------------
# § Filed versus proposed, and the menu (MC-18, MC-19)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_result_names_what_became_a_proposal_mc18(connect: Connect) -> None:
    """Propose-only is not read-only, and the difference has to be visible in the result.

    The gate table is the same for every channel: a plain note lands unattended, a breadth summary
    becomes a proposal. Conflating the two is the specific failure this rule prevents — an external
    agent believes a summary update landed, reports success upstream, and the human's distilled
    rules never changed. The proposal is reported with the reason the gate fired and the path, which
    is what makes it actionable rather than a count.
    """
    service = ScriptedService(
        events=list(FANOUT), gated=("Cooking/notes/summary.md", "breadth-approval")
    )
    async with connect(service) as session:
        body = outcome(await call(session, "pkb_ingest", content="Preheat for twelve minutes."))

        assert body["status"] == "answered"
        assert body["proposals"] == [
            {
                "proposal_id": "proposal-1",
                "tool": "write_file",
                "path": "Cooking/notes/summary.md",
                "reason": "breadth-approval",
            }
        ]


@pytest.mark.asyncio
async def test_an_expert_menu_is_an_ordinary_success_the_caller_may_answer_mc19(
    connect: Connect,
) -> None:
    """When classification does not land, that is an answer — not an error and not an interrupt.

    An error would be retried, and the retry would fail identically because nothing changed. An
    interrupt would block, and there is no human behind ``/mcp`` to unblock it. So the menu comes
    back as a successful result the caller may reply to as the next message on the same thread, with
    the candidate ids in a structured field — and the adapter picks none of them, because guessing
    a topic is the thing this system refuses to do at every layer.
    """
    service = ScriptedService(
        events=[
            RunEnd(
                run_id="run-1",
                final_text=f"I could file this under {GRILLING}. Which do you mean?",
            )
        ]
    )
    async with connect(service) as session:
        result = await call(session, "pkb_ask", question="how long?")
        body = outcome(result)

        assert result.is_error is False
        assert body["status"] == "menu"
        assert GRILLING in body["candidates"]
        assert body["thread_id"] in service.rows, "answerable as the next turn on the same thread"


@pytest.mark.asyncio
async def test_a_menu_offers_only_the_ids_the_reply_actually_named_mc19(connect: Connect) -> None:
    """A candidate list is a promise that each entry was offered. Ancestors were not."""
    service = ScriptedService(
        events=[RunEnd(run_id="run-1", final_text=f"Did you mean {GRILLING}?")]
    )
    async with connect(service) as session:
        body = outcome(await call(session, "pkb_ask", question="how long?"))

        assert body["candidates"] == [GRILLING]


@pytest.mark.asyncio
async def test_a_direct_experts_answer_is_never_a_menu_mc19(connect: Connect) -> None:
    """Cross-references are what a good expert answer contains; they are not an unresolved routing."""
    service = ScriptedService(
        handle_agent=COOKING,
        events=[
            RunEnd(
                run_id="run-1",
                final_text=f"Fifteen minutes. For charcoal specifics, ask {GRILLING}.",
            )
        ],
    )
    async with connect(service) as session:
        body = outcome(await call(session, "pkb_ask", question="how long?", agent_id=COOKING))

        assert body["status"] == "answered"


# --------------------------------------------------------------------------------------
# § Escalation (MC-20, MC-21)
# --------------------------------------------------------------------------------------

ESCALATING_CALLS = [
    pytest.param("pkb_ask", {"question": "how long do I preheat?"}, id="pkb_ask"),
    pytest.param("pkb_ingest", {"content": "Preheat for twelve minutes."}, id="pkb_ingest"),
    pytest.param(
        "pkb_research_pack", {"query": "preheat", "topics": [COOKING]}, id="pkb_research_pack"
    ),
    pytest.param("pkb_implementation_pack", {"topic": COOKING}, id="pkb_implementation_pack"),
]


@pytest.mark.parametrize(("tool", "arguments"), ESCALATING_CALLS)
@pytest.mark.asyncio
async def test_an_escalation_is_a_success_with_a_discriminator_mc20(
    connect: Connect, reviewed_kb: KbSnapshot, tool: str, arguments: dict[str, Any]
) -> None:
    """Contested material stops all four tools, and stopping is not an error.

    Not an error, because a well-behaved agent retries errors and a retried escalation is an
    escalation ignored — the caller would keep asking until the deadline and then act on whichever
    answer it got. Not prose either, because the caller is a program that has to *stop*: the
    discriminator and the ``review_note`` are what tell it which file the human is still deciding
    about. The trigger is computed from the tag on disk intersected with the participating topics,
    never from what the model said it read, so it cannot be talked out of firing.
    """
    service = ScriptedService(
        events=[
            SubagentStart(run_id="run-1", agent_id=COOKING),
            RunEnd(run_id="run-1", final_text=""),
        ]
    )
    async with connect(service, snapshot=reviewed_kb) as session:
        result = await call(session, tool, **arguments)
        body = outcome(result)

        assert result.is_error is False
        assert body["status"] == "escalation"
        assert [e["path"] for e in body["escalation"]] == [CONFLICT_NOTE]
        assert body["escalation"][0]["review_note"] == REVIEW_NOTE
        assert body["escalation"][0]["agent_id"] == COOKING


@pytest.mark.asyncio
async def test_the_escalation_self_clears_when_the_human_resolves_the_tag_mc20(
    connect: Connect, resolved_kb: KbSnapshot
) -> None:
    """The same file, same path, tag cleared — and the knowledge base goes back to answering.

    Self-clearing is what keeps the escalation cheap enough to be honest. If lifting it needed a
    second action nobody would ever take one, and the tag would become a permanent "this topic is
    broken" sign that callers learn to route around.
    """
    service = ScriptedService(
        events=[
            SubagentStart(run_id="run-1", agent_id=COOKING),
            RunEnd(run_id="run-1", final_text="Fifteen minutes."),
        ]
    )
    async with connect(service, snapshot=resolved_kb) as session:
        body = outcome(await call(session, "pkb_ask", question="how long?"))

        assert body["status"] == "answered"
        assert body["answer"] == "Fifteen minutes."


@pytest.mark.asyncio
async def test_a_sibling_topic_is_unaffected_by_the_conflict_mc20(
    connect: Connect, reviewed_kb: KbSnapshot
) -> None:
    """Scope is the participating topics' subtrees, not the whole knowledge base.

    One contested note in Cooking must not silence Grilling. An escalation that fired knowledge-base
    wide would make a single unresolved disagreement stop every tool for every topic — which is how
    a safety mechanism gets switched off rather than fixed.
    """
    service = ScriptedService(
        events=[
            SubagentStart(run_id="run-1", agent_id=GRILLING),
            RunEnd(run_id="run-1", final_text="Ten, on charcoal."),
        ]
    )
    async with connect(service, snapshot=reviewed_kb) as session:
        body = outcome(await call(session, "pkb_ask", question="how long?"))

        assert body["status"] == "answered"
        pack = outcome(await call(session, "pkb_implementation_pack", topic=GRILLING))
        assert pack["status"] == "ok"


@pytest.mark.asyncio
async def test_mcp_never_resolves_a_conflict_itself_mc21(
    connect: Connect, reviewed_kb: KbSnapshot
) -> None:
    """Clearing the review flag is gated, so from here it can only ever become a proposal.

    The asymmetry is deliberate and has to survive into the transport: *adding*
    ``status.conflict-review`` is ungated so a background scan is never blocked on a human, while
    *clearing* it is gated so no agent can declare a disagreement settled. On a propose-only call
    that means a recorded proposal, nothing changed on disk, and the tag still firing — which the
    next call proves by escalating again.
    """
    service = ScriptedService(
        events=[
            SubagentStart(run_id="run-1", agent_id=COOKING),
            RunEnd(run_id="run-1", final_text=""),
        ],
        gated=(CONFLICT_NOTE, "conflict-resolution"),
    )
    async with connect(service, snapshot=reviewed_kb) as session:
        await call(session, "pkb_ask", question="is the preheat conflict settled?")
        listed = await session.read_resource("pkb://proposals")
        payload = json.loads(listed.contents[0].text)  # type: ignore[union-attr]

        assert [p["reason"] for p in payload["proposals"]] == ["conflict-resolution"]
        assert [p["args"]["file_path"] for p in payload["proposals"]] == [CONFLICT_NOTE]

        again = outcome(await call(session, "pkb_ask", question="and now?"))
        assert again["status"] == "escalation", "nothing was resolved, so the tag still fires"


# --------------------------------------------------------------------------------------
# § Shape (MC-22)
# --------------------------------------------------------------------------------------


def assert_primitives(value: object, where: str = "$") -> None:
    """Every leaf is a JSON primitive — no harness object survived into the result."""
    if isinstance(value, dict):
        for key, item in value.items():
            assert isinstance(key, str), where
            assert_primitives(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_primitives(item, f"{where}[{index}]")
    else:
        assert value is None or isinstance(value, str | int | float | bool), f"{where}: {value!r}"


@pytest.mark.asyncio
async def test_every_result_is_json_primitives_and_carries_no_harness_object_mc22(
    connect: Connect, reviewed_kb: KbSnapshot
) -> None:
    """All four tools, all four outcome shapes, over a JSON boundary that admits nothing else.

    I2's whole promise is that a LangChain message, an ``Interrupt``, a ``Command`` or a
    ``CompiledStateGraph`` never reaches a transport. That holds today because ``AgentEvent`` and
    ``pkb.contracts`` are already primitives — but packs, proposals and escalations are new types
    added at this layer, and a stray dataclass or ``Path`` in one of them serializes as ``repr`` at
    best and raises mid-response at worst, after the 200 has been committed.
    """
    service = ScriptedService(
        events=list(FANOUT), gated=("Cooking/notes/summary.md", "breadth-approval")
    )
    escalating = ScriptedService(
        events=[
            SubagentStart(run_id="run-1", agent_id=COOKING),
            RunEnd(run_id="run-1", final_text=""),
        ]
    )
    async with (
        connect(service) as session,
        connect(escalating, snapshot=reviewed_kb) as escalating_session,
    ):
        results = [
            await call(session, "pkb_ask", question="how long?"),
            await call(session, "pkb_ingest", content="Preheat for twelve minutes."),
            await call(session, "pkb_research_pack", query="preheat", topics=[COOKING]),
            await call(session, "pkb_implementation_pack", topic=COOKING),
            await call(escalating_session, "pkb_ask", question="how long?"),
            await call(session, "pkb_ask", question="how long?", thread_id=f"t{'::'}{COOKING}"),
        ]

        assert [outcome(r)["status"] for r in results] == [
            "answered",
            "answered",
            "ok",
            "ok",
            "escalation",
            "error",
        ]
        for result in results:
            body = result.structured_content
            assert_primitives(body)
            assert json.loads(json.dumps(body)) == body
