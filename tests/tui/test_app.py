"""The shell and the approval modal, under a real pilot (TU-4 … TU-22, TU-37 … TU-50).

Three fixtures, chosen per rule rather than by habit:

* **No fixture at all** for the pure functions — ``diff_text``, ``validation_header``, the binding
  tables, and the AST scans over the two packages. They are faster and sharper than a pilot, and a
  golden over ``diff_text`` is the only place TU-40 can be pinned exactly.
* **A pilot over an unopened client** for the modal. The modal never talks to the daemon, so a
  socket would only add a way for the test to be flaky; and a client that is not open makes
  "dismissing sends nothing" (TU-47) provable by construction — there is nothing to send it with.
* **A pilot over the real daemon on a real socket** for the shell. ``httpx2.ASGITransport``
  buffers (P-14a), and the point of these tests is that the client's decoder meets the server's
  real encoder over a real chunked response.

``sse_starlette``'s ``AppStatus.should_exit`` is process-global and sticky (P-14b): after one
uvicorn server stops, the next one in the same process truncates a healthy run with a bogus
``run.error``. The autouse fixture below resets it, and
``test_two_servers_in_one_process_both_stream_p14b`` proves the reset works — without that proof a
suite built on it fails from the second streaming test on, with a failure that reads as a daemon bug.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import shutil
import socket
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sse_starlette.sse
import uvicorn
from rich.syntax import Syntax
from sse_starlette.sse import AppStatus
from textual.content import Content
from textual.markup import MarkupError
from textual.widgets import Button, Input, Label, ListView, Static, Tree

from pkb.agents.gates import GateReason, describe_write
from pkb.clients.approval import Resolution
from pkb.contracts import (
    ActionView,
    ApprovalRequest,
    InterruptEvent,
    MessageDelta,
    MessageView,
    RunEnd,
    SubagentStart,
    expert_thread_id,
    is_scan_thread,
)
from pkb.core import Metadata
from pkb.core.frontmatter import serialize
from pkb.core.scaffold import scaffold_topic
from pkb.core.scan import scan
from pkb.server.app import ServerConfig, create_app
from pkb.server.health import HealthState
from pkb.service import Thread, ThreadDetail
from pkb.tui.app import UNTITLED, PkbApp, proposal_line, thread_label
from pkb.tui.client import PkbClient
from pkb.tui.modal import VALIDATION_LABEL, ApprovalModal, diff_text, validation_header
from tests.server.stub import COOKING, GRILLING, LIBRARIAN, StubService, opener_for

TODAY = date(2026, 8, 8)
NOW = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)

THREAD = "3f0c9a1e"
DERIVED = expert_thread_id(THREAD, COOKING)
"""``3f0c9a1e::topic/cooking`` — where a gate raised inside a fan-out actually parks (LB-16)."""

CLIENT_PACKAGES = ("pkb/tui", "pkb/clients")
"""Both packages, because I2's ban and TU-4's are statements about the *layer*, not about a module.
A helper that read the tree would be just as wrong living next to the decoder as next to the app."""

SIZE = (120, 40)
"""Every ``run_test`` pins a size: at 80x24 a sidebar plus a modal pushes the buttons off-screen
and ``pilot.click`` raises ``OutOfBounds``, and a reflow on a version bump would read as a defect."""


# --------------------------------------------------------------------------------------
# Fixtures — the sticky global, the daemon, the app
# --------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_sse_shutdown_global() -> Iterator[None]:
    """``sse_starlette.sse.AppStatus.should_exit`` is process-global and sticky (P-14b).

    Measured: server #1 stops, and server #2 — a fresh app in the same process — delivers
    ``['run.started', 'run.error']`` over a run that is perfectly healthy. Without this reset the
    streaming tests fail from the second one onward and the failure looks like a daemon defect
    rather than a test-harness one.

    Clearing the flag alone is not enough on sse-starlette 3.4.8. ``_shutdown_watcher`` polls the
    uvicorn server it captured at start every 0.5 s and re-arms the flag once that server stops, so
    a stopped server keeps arming the global *after* the reset; and ``_get_shutdown_state`` is keyed
    per **thread**, not per event loop, so ``watcher_started`` and the registered events outlive the
    loop they belong to. Turning the automatic drain off for this module means nothing but this file
    can ever set the flag, and quiescing the thread-local state means the file does not depend on
    what an earlier test happened to leak.
    """
    AppStatus.enable_automatic_graceful_drain = False
    AppStatus.should_exit = False
    try:
        yield
    finally:
        AppStatus.should_exit = False
        AppStatus.enable_automatic_graceful_drain = True


async def quiesce_sse_shutdown() -> None:
    """Cancel the shutdown watcher and clear the thread-local state it feeds (P-14b)."""
    AppStatus.should_exit = False
    for task in asyncio.all_tasks():
        if getattr(task.get_coro(), "__qualname__", "") == "_shutdown_watcher":
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    state = sse_starlette.sse._get_shutdown_state()
    state.events.clear()
    state.watcher_started = False
    AppStatus.should_exit = False


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    return port


@contextlib.asynccontextmanager
async def daemon(service: StubService, *, health: HealthState | None = None) -> AsyncIterator[str]:
    """The **real** ``create_app`` over a **real** socket, for the length of one test."""
    config = ServerConfig(kb_root="/kb", health=health or HealthState())
    app = create_app(opener_for(service), config=config)
    await quiesce_sse_shutdown()
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(task, 5)
        await quiesce_sse_shutdown()


@contextlib.asynccontextmanager
async def shell(
    service: StubService,
    *,
    health: HealthState | None = None,
    client_factory: Callable[[str], PkbClient] = PkbClient,
) -> AsyncIterator[tuple[PkbApp, Any]]:
    """A mounted :class:`PkbApp` talking to a live daemon, with its catalog already loaded."""
    async with daemon(service, health=health) as base_url:
        client = client_factory(base_url)
        async with client.opened():
            app = PkbApp(client)
            async with app.run_test(size=SIZE) as pilot:
                await wait_until(pilot, lambda: bool(app.agents))
                yield app, pilot


@contextlib.asynccontextmanager
async def offline_app() -> AsyncIterator[tuple[PkbApp, Any]]:
    """A mounted app whose client was never opened — it cannot issue a request if it wants to.

    That is the point for the modal tests: "dismissing sends nothing" is not a promise a spy has to
    police when there is no transport underneath at all.
    """
    app = PkbApp(PkbClient(base_url="http://127.0.0.1:1"))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        yield app, pilot


async def open_modal(app: PkbApp, pilot: Any) -> ApprovalModal:
    """The approval modal, once it is actually the active screen.

    ``app.query_one`` searches the *default* screen, so every assertion about the modal has to go
    through the screen the modal pushed — otherwise a test asserting on an empty result set would
    pass for a modal that never opened.
    """
    await wait_until(pilot, lambda: isinstance(app.screen, ApprovalModal))
    screen = app.screen
    assert isinstance(screen, ApprovalModal)
    return screen


async def wait_until(pilot: Any, predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """A bounded poll on ``pilot.pause()``.

    ``pause()`` drains the message queue; it cannot know an SSE frame is still in flight. The usual
    substitute — a fixed sleep — buys a flake back plus half a second per test.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await pilot.pause()
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the condition never became true within the timeout")


# --------------------------------------------------------------------------------------
# Fixtures — knowledge-base fixtures and real gate descriptions
# --------------------------------------------------------------------------------------

BULLETS = "\n# Steak\n\n- Pull at 130F\n- Rest 10 minutes\n"
"""Markdown bullets: the most common content in this knowledge base, and the exact shape a diff
lexer paints in the **deletion** colour (P-13)."""


def document(body: str = BULLETS, *, title: str = "Steak notes") -> str:
    """A well-formed PKB document, serialized by Layer 1 so the fixture cannot drift from FM-*."""
    meta = Metadata(
        title=title,
        description="Distilled rules for cooking steak at home.",
        topic="Cooking",
        tags=("topic.cooking", "type.note"),
        created=TODAY,
        updated=TODAY,
        source_type="note",
    )
    return serialize(meta, body)


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    """A real knowledge base, built by Layer 1's own scaffolder."""
    root = tmp_path / "KnowledgeBase"
    root.mkdir()
    scaffold_topic(
        root,
        "Cooking",
        title="Cooking",
        description="Home cooking: technique, equipment, and recipes",
        today=TODAY,
    )
    return root


def new_file_description(kb_root: Path) -> str:
    """The real ``describe_write`` output for a write to a file that does not exist yet."""
    return describe_write(
        GateReason.BREADTH_APPROVAL,
        "write_file",
        "Cooking/notes/steak.md",
        {"content": document()},
        scan(kb_root),
    )


def existing_file_description(kb_root: Path) -> str:
    """The real ``describe_write`` output for a write over an existing file — a unified diff."""
    target = kb_root / "Cooking/notes/steak.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document(), encoding="utf-8")
    edited = "\n# Steak\n\n- Pull at 125F\n- Rest 10 minutes\n- Salt the night before\n"
    return describe_write(
        GateReason.BREADTH_APPROVAL,
        "write_file",
        "Cooking/notes/steak.md",
        {"content": document(edited)},
        scan(kb_root),
    )


# --------------------------------------------------------------------------------------
# Fixtures — approvals, threads, services
# --------------------------------------------------------------------------------------


def action(
    *,
    tool: str = "write_file",
    args: dict[str, str] | None = None,
    description: str = "Approval required: breadth-approval\nTool: write_file\n",
    allowed: tuple[str, ...] = ("approve", "edit", "reject"),
    reason: str = "breadth-approval",
) -> ActionView:
    return ActionView(
        tool=tool,
        args=args if args is not None else {"file_path": "Cooking/notes/steak.md", "content": "x"},
        description=description,
        allowed_decisions=allowed,  # type: ignore[arg-type]
        reason=reason,
    )


def approval(
    *actions: ActionView, thread_id: str = DERIVED, interrupt_id: str = "i-1"
) -> ApprovalRequest:
    """One approval, parked on the thread that raised it — the expert's, in a fan-out."""
    return ApprovalRequest(
        interrupt_id=interrupt_id,
        agent_id=COOKING,
        thread_id=thread_id,
        actions=actions or (action(),),
    )


def thread(
    thread_id: str,
    *,
    agent_id: str = LIBRARIAN,
    title: str | None = "Steak",
    minutes_ago: int = 0,
    pending: str | None = None,
    origin: str = "tui",
) -> Thread:
    stamp = NOW - timedelta(minutes=minutes_ago)
    return Thread(
        thread_id=thread_id,
        agent_id=agent_id,
        created_at=stamp,
        updated_at=stamp,
        origin_channel=origin,  # type: ignore[arg-type]
        title=title,
        pending_interrupt_id=pending,
    )


class Service(StubService):
    """The shipped stub plus the two things a Layer 4 test has to script.

    ``children`` is what TU-18's offers are rebuilt from after a reload, and a scripted ``resume``
    is what lets TU-49 assert *which thread* the decisions were posted to without the resumed run
    replaying the same interrupt straight back into the modal.
    """

    def __init__(self, *, children: Sequence[dict[str, Any]] = (), **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.children = list(children)
        self.resume_events: list[Any] = []

    async def get_thread(self, thread_id: str) -> ThreadDetail:
        detail = await super().get_thread(thread_id)
        if not self.children:
            return detail
        kids = tuple(
            Thread(
                thread_id=str(child["thread_id"]),
                agent_id=str(child["agent_id"]),
                created_at=NOW,
                updated_at=NOW,
                origin_channel="tui",
                title=child.get("title"),
            )
            for child in self.children
        )
        return ThreadDetail(
            thread=detail.thread,
            messages=detail.messages,
            pending=detail.pending,
            children=kids,
        )

    async def resume(
        self, thread_id: str, decisions: Sequence[Any], *, interrupt_id: str | None = None
    ) -> Any:
        self.calls.append(("resume", (thread_id, interrupt_id)))
        self.events = list(self.resume_events)
        return await StubService.start_run(self, thread_id, "")


# --------------------------------------------------------------------------------------
# Helpers — reading the screen, and reading the source
# --------------------------------------------------------------------------------------


def rows(app: PkbApp) -> list[str]:
    """Every thread row, as the human reads it."""
    listing = app.query_one("#threads", ListView)
    return [str(item.query_one(Label).content) for item in listing.children]


def picker(app: PkbApp) -> list[tuple[str, Any]]:
    """``(label, data)`` for every node of the agent tree, in the order it renders."""
    out: list[tuple[str, Any]] = []

    def walk(node: Any) -> None:
        for child in node.children:
            out.append((str(child.label), child.data))
            walk(child)

    walk(app.query_one("#agents", Tree).root)
    return out


def transcript(app: PkbApp) -> list[str]:
    return [str(widget.content) for widget in app.query("#transcript Static")]


def sources(*packages: str) -> list[Path]:
    root = Path(__file__).resolve().parents[2] / "src"
    return sorted(path for package in packages for path in (root / package).glob("*.py"))


def identifiers(path: Path) -> set[str]:
    """Every name a module's *code* uses — docstrings and comments excluded by construction.

    Grep cannot tell "this module explains why it never touches the filesystem" from "this module
    touches the filesystem", and both TU-4 and TU-5 are asserted by absence. Every mention in these
    packages today is in prose, which is exactly the case a text grep gets wrong.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.name.rpartition(".")[2])
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            found.add(node.name)
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(node.arg)
    return found


def called(path: Path) -> set[str]:
    """Every function a module *calls*, by name.

    Narrower than :func:`identifiers` on purpose: ``Branch.open`` is a field, and a rule about
    opening files must not be tripped by a dataclass attribute that happens to share the word.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            found.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            found.add(node.func.attr)
    return found


def literals(path: Path) -> list[str]:
    """Every string literal in a module's code — again, docstrings excluded.

    A module docstring or a function docstring is an ``Expr`` whose value is a ``Constant``, so
    dropping those is what separates "the module warns against ``::``" from "the module splits on
    ``::``".
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def bindings_of(screen: type) -> list[tuple[str, str]]:
    """``(key, action)`` for a screen's declared bindings, whatever form they are declared in."""
    out: list[tuple[str, str]] = []
    for binding in screen.BINDINGS:  # type: ignore[attr-defined]
        if isinstance(binding, tuple):
            out.append((binding[0], binding[1]))
        else:
            out.append((binding.key, binding.action))
    return out


_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")


def neighbours(key: str) -> set[str]:
    """The keys a finger can hit by mistake instead of ``key`` on a QWERTY keyboard."""
    near: set[str] = set()
    for index, row in enumerate(_ROWS):
        if key not in row:
            continue
        position = row.index(key)
        for other_index in (index - 1, index, index + 1):
            if not 0 <= other_index < len(_ROWS):
                continue
            other = _ROWS[other_index]
            for offset in (-1, 0, 1):
                if 0 <= position + offset < len(other):
                    near.add(other[position + offset])
    return near - {key}


def digest(root: Path) -> dict[str, tuple[str, int]]:
    """Every file under ``root`` by content hash and mtime — I3's "nothing moved" made checkable."""
    return {
        str(path.relative_to(root)): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# --------------------------------------------------------------------------------------
# § the two packages touch nothing and know no model (TU-4, TU-5)
# --------------------------------------------------------------------------------------

_FILESYSTEM_NAMES = frozenset(
    {
        "open",
        "read_text",
        "write_text",
        "read_bytes",
        "write_bytes",
        "glob",
        "rglob",
        "walk",
        "iterdir",
        "mkdir",
        "unlink",
        "rmtree",
        "listdir",
        "copyfile",
        "NamedTemporaryFile",
    }
)


def test_no_client_module_opens_reads_or_writes_anything_tu4() -> None:
    """The TUI is a separate process that may not even be on the same host (arch §10).

    A filesystem read of ``kb_root`` from here is not redundant, it is **wrong**: it would render an
    approval from bytes the daemon never saw, and a write would put Layer 4 inside I3's guarantee
    that nothing above Layer 1 touches the tree. Both are the kind of shortcut that looks harmless
    in a "save this diff" feature and silently makes the client host-bound.
    """
    for path in sources(*CLIENT_PACKAGES):
        used = called(path) & _FILESYSTEM_NAMES
        assert used == set(), f"{path.name} reaches the filesystem via {sorted(used)}"
        assert "Path" not in identifiers(path), f"{path.name} names a filesystem path type"


@pytest.mark.superseded
def test_a_full_approval_session_leaves_the_knowledge_base_byte_identical_tu4(kb: Path) -> None:
    """The strongest form of TU-4: hash the tree around a session that decides a real write.

    An assertion about identifiers can only see the shapes it was told to look for. This one sees
    any of them — a cache file, a scratch export, a log next to the notes, a rewritten mtime — and
    it exercises the one screen most likely to be tempted into "let me just read the file to show a
    better diff" (TU-39).

    Superseded (Phase 5 rebuilds this): the "session" driven here is a full decide-through-the-modal
    cycle over :class:`ApprovalModal`, which dies with the interrupt/resume surface — the operator's
    instruction is the approval, so nothing parks and there is no modal to open. The weaker,
    identifier-only form of TU-4 survives as
    `test_no_client_module_opens_reads_or_writes_anything_tu4`; the strong byte-identity form needs
    a session-write-shaped successor once Phase 5 rebuilds client polish.
    """
    description = existing_file_description(kb)
    before = digest(kb)
    assert before, "the fixture must actually contain files for this test to mean anything"

    async def session() -> None:
        async with offline_app() as (app, pilot):
            answers: list[Any] = []
            app.push_screen(
                ApprovalModal(approval(action(description=description))), answers.append
            )
            await pilot.pause()
            await pilot.press("a")
            await wait_until(pilot, lambda: bool(answers))
            assert isinstance(answers[0], Resolution)

    asyncio.run(session())
    assert digest(kb) == before


def test_neither_package_calls_a_model_or_names_one_tu5() -> None:
    """A client that summarizes, re-titles or picks an agent has taken a decision nobody can audit.

    The model is a **registry** concern (RG-21): no transport, route or channel picks one. The
    failure this forbids is not a wasted token — it is a second, invisible author of knowledge-base
    content sitting in the process furthest from every guardrail Layers 1-3 install.
    """
    banned_names = {"init_chat_model", "ChatOllama", "ChatAnthropic", "ChatOpenAI", "ainvoke"}
    banned_fragments = ("ollama:", "gemma", "deepseek", "gpt-", "claude-", "model_id")
    for path in sources(*CLIENT_PACKAGES):
        assert identifiers(path) & banned_names == set(), path.name
        for text in literals(path):
            for fragment in banned_fragments:
                assert fragment not in text, f"{path.name} names a model: {text!r}"


# --------------------------------------------------------------------------------------
# § the agent picker (TU-8, TU-9)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_picker_renders_the_catalog_in_server_order_tu8() -> None:
    """Snapshot order is the tree's order, which is the order the human's knowledge base is in.

    Re-sorting alphabetically makes the sidebar disagree with the root catalog ``index.md`` and with
    every other channel — Cooking would come before Librarian, and the agent every question starts
    with would sit in the middle of the list. The catalog carries ``model_id``; a picker that showed
    it would invite the human to choose one, which deletes RG-21.
    """
    async with shell(Service()) as (app, _):
        assert [label for label, _ in picker(app)] == ["Librarian", "Cooking", "Grilling"]
        assert [data for _, data in picker(app)] == [LIBRARIAN, COOKING, GRILLING]

        # Server order is not alphabetical order: this fixture can tell the two apart.
        assert [label for label, _ in picker(app)] != sorted(label for label, _ in picker(app))

        # The catalog really does carry a model id, and none of it reaches the screen.
        assert all("model_id" in descriptor for descriptor in app.agents)
        assert any("ollama" in str(descriptor["model_id"]) for descriptor in app.agents)
        assert all("ollama" not in label for label, _ in picker(app))


@pytest.mark.asyncio
async def test_an_agent_id_reaches_the_wire_byte_identically_tu9() -> None:
    """Nesting may be *derived* from the ``/`` in an id; the request carries the id whole.

    Reassembling ``topic/cooking/grilling`` from the labels of three tree nodes is the silent
    failure RO-2 forbids: it resolves to a *different* agent, whose threads then share a checkpoint
    with the wrong conversation. Nothing about the screen would look wrong.
    """
    service = Service()
    async with shell(service) as (app, pilot):
        tree = app.query_one("#agents", Tree)
        app.set_focus(tree)
        await pilot.press("down", "down", "down", "enter")
        await wait_until(pilot, lambda: app.selected_agent == GRILLING)

    listed = [args for name, args in service.calls if name == "list_sessions"]
    assert (GRILLING, "open") in listed, listed
    assert all("%2F" not in str(args) for args in listed)


def test_no_agent_id_is_percent_encoded_or_reassembled_tu9() -> None:
    """The two ways a client mangles an opaque id, asserted by absence.

    ``%2F`` is not an alternative to a raw slash — Starlette decodes it back and proxies normalize
    it — and splitting an id into segments is how a request ends up naming a *different* agent. The
    scan is over the syntax tree, because both packages discuss slashes in prose and neither uses
    them.
    """
    for path in sources(*CLIENT_PACKAGES):
        names = called(path)
        assert "quote" not in names, path.name
        assert "quote_plus" not in names, path.name
        assert "urlencode" not in names, path.name
        assert "split" not in names, path.name
        for text in literals(path):
            assert "%2F" not in text, path.name


# --------------------------------------------------------------------------------------
# § the thread list (TU-10, TU-11, TU-15)
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_thread_list_keeps_the_servers_order_tu10() -> None:
    """Pending-first is the design's answer to the scenario the architecture is built around.

    The fixture is deliberately hostile to every client-side sort anyone would reach for: the
    pending row is the **oldest** by ``updated_at`` and its title sorts last. A TUI that re-sorted by
    recency or by title would bury the exact row the human came back to answer — and it would look
    entirely reasonable doing it, which is why this is asserted rather than eyeballed.

    Superseded (Phase 5 rebuilds this): "pending-first" is sorted on `pending_interrupt_id`, and
    sessions have no gates, no parked proposals and no pending queue anywhere — nothing is ever
    parked, so there is no signal left to sort first on. A successor ordering (if any) needs a
    session-shaped "needs attention" signal that does not exist yet.
    """
    service = Service()
    service.rows = {
        "t-old": thread("t-old", title="Zucchini", minutes_ago=600, pending="i-1"),
        "t-new": thread("t-new", title="Almonds", minutes_ago=1),
    }
    async with shell(service) as (app, _):
        assert rows(app) == ["● Zucchini", "  Almonds"]


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_routed_row_is_marked_from_kind_not_from_a_string_scan_tu11() -> None:
    """A conversation *with* Cooking and the work the Librarian *routed* to Cooking are different.

    They sit in the same list under per-expert grouping, and "continue what the Librarian was doing"
    resumes a different thread from "continue with the Cooking expert". ``kind`` exists (ST-6) so
    telling them apart is a field lookup rather than a client sniffing an id for ``::`` — a client
    that sniffs is one server-side id change away from mislabelling every row.

    Superseded (Phase 5 rebuilds this): "routed" is the derived-thread `<parent>::<agent>` addressing
    the Librarian's fan-out used to park a sub-agent's work under the parent's thread — retired
    entirely, no parent/derived split in the session model. There is no `kind == "routed"` left to
    distinguish.
    """
    service = Service()
    service.rows = {
        DERIVED: thread(DERIVED, agent_id=COOKING, title="Steak, routed"),
        "t-user": thread("t-user", agent_id=COOKING, title="Steak, mine"),
    }
    async with shell(service) as (app, _):
        assert rows(app) == ["  Steak, routed (routed)", "  Steak, mine"]
        assert app.threads[0]["kind"] == "routed"
        assert app.threads[0]["parent_thread_id"] == THREAD
        assert app.threads[1]["kind"] == "user"


@pytest.mark.superseded
def test_routedness_is_never_detected_by_searching_for_a_double_colon_tu11() -> None:
    """The literal that must not appear, asserted over the syntax tree.

    ``pkb.tui`` explains the rule in prose, so a text grep hits its own documentation. What matters
    is that no *code* in the package contains ``::``: derivation belongs to
    ``contracts.expert_thread_id`` and detection to ``Thread.kind``.

    Superseded (Phase 5 rebuilds this): guards against sniffing the derived-thread `<t>::<agent>` id
    scheme, which is retired along with `expert_thread_id` and fan-out addressing — there is no more
    derived id for code to be tempted into parsing.
    """
    for path in sources("pkb/tui"):
        for text in literals(path):
            assert "::" not in text, f"{path.name} spells a derived thread id: {text!r}"


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_an_untitled_thread_and_an_empty_title_render_differently_tu15() -> None:
    """``None`` means "not titled yet"; ``""`` means a human deliberately blanked it.

    Titles land asynchronously after the first reply, so a large share of rows in a fresh knowledge
    base are ``None`` — collapsing the two states makes a brand-new thread indistinguishable from a
    blanked one, and a first-line fallback is the "I grilled a ribeye last weeke…" sidebar the
    titling ruling rejected outright.

    Superseded (Phase 5 rebuilds this): a session has no "untitled" state to distinguish from a
    blanked one — S-5 names it from the objective *synchronously*, at creation, with a deterministic
    slug when none was given, never asynchronously and never ``None`` (``pkb.tui.client``'s
    ``_session_as_thread``, which reports ``title`` from a session's own ``name`` for exactly this
    reason). The distinction this test asserts does not exist in the session model to render.
    """
    service = Service()
    service.rows = {
        "t-null": thread("t-null", title=None),
        "t-empty": thread("t-empty", title=""),
    }
    async with shell(service) as (app, _):
        rendered = rows(app)
        assert rendered[0] != rendered[1]
        assert rendered[0] == f"  {UNTITLED}"
        assert rendered[1] == "  "

    # And the row builder never falls back to the first message, for either state.
    assert "hi" not in thread_label({"title": None})
    assert "hi" not in thread_label({"title": ""})


# --------------------------------------------------------------------------------------
# § startup and navigation (TU-6, TU-7, TU-12, TU-13, TU-14, TU-17, TU-18, TU-19)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_degraded_daemon_banners_at_200_and_still_runs_tu6() -> None:
    """``/health`` reports degradation in the **body**, with a 200 (AP-18).

    A client that only checks the status code never learns that a subsystem has restarted forty
    times — healthy by any single sample, broken by any human's judgement. And the banner is a
    status line, never a gate: refusing to start runs against a degraded daemon would take a bot
    outage and turn it into a knowledge base nobody can write to.
    """
    health = HealthState()
    health.telegram.state = "restarting"
    health.telegram.restarts = 40
    service = Service(events=[RunEnd(run_id="run-1", final_text="filed")])
    service.rows = {THREAD: thread(THREAD)}

    async with shell(service, health=health) as (app, pilot):
        assert "degraded" in app.last_error
        assert "telegram" in app.last_error

        await app.open_thread(THREAD)
        app.query_one("#compose", Input).value = "where does this go?"
        app.set_focus(app.query_one("#compose", Input))
        await pilot.press("enter")
        await wait_until(
            pilot, lambda: any(name == "start_session_run" for name, _ in service.calls)
        )


@pytest.mark.asyncio
async def test_a_closed_port_renders_the_start_command_and_stops_tu7() -> None:
    """The TUI is the thing a human launches first, so a closed port is a *screen*, not a traceback.

    Everything needed to recover is already known to the client: the address it tried and the one
    command that starts a daemon. A stack trace on the port it just chose is the worst possible
    first impression, and a spinner is worse still because it never resolves.
    """
    client = PkbClient(base_url=f"http://127.0.0.1:{free_port()}")
    async with client.opened():
        app = PkbApp(client)
        async with app.run_test(size=SIZE) as pilot:
            await wait_until(pilot, lambda: "no daemon" in app.last_error)
            assert client.base_url in app.last_error
            assert "python -m pkb.daemon" in app.last_error
            assert app.agents == [] and app.threads == []
            assert picker(app) == []
            await pilot.pause()
            assert app.is_running


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_needs_you_view_is_one_keystroke_and_unfiltered_tu12() -> None:
    """When an expert gates *inside* a fan-out, the human does not know which expert was reached.

    The merged reply that named it scrolled away hours ago. Requiring them to pick the right expert
    before they can see the approval is the failure RO-8 was written to close, so the unfiltered
    list is one binding away from wherever they are — and it must not carry the agent filter that
    was in force when they pressed it.

    Superseded (Phase 5 rebuilds this): the whole scenario is a gate firing *inside* a fan-out and
    parking on the expert's derived thread — no gates, no interrupts, no derived threads in the
    session model, so there is nothing pending to jump to.
    """
    service = Service()
    service.rows = {
        DERIVED: thread(DERIVED, agent_id=COOKING, title="Gated", pending="i-1"),
        "t-user": thread("t-user", agent_id=LIBRARIAN, title="Mine"),
    }
    async with shell(service) as (app, pilot):
        app.selected_agent = COOKING
        app.set_focus(app.query_one("#threads", ListView))
        service.calls.clear()
        await pilot.press("p")
        await wait_until(pilot, lambda: any(name == "list_threads" for name, _ in service.calls))

        assert ("list_threads", (None,)) in service.calls, service.calls
        assert app.selected_agent is None
        assert rows(app)[0] == "● Gated (routed)"


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_an_unbadged_row_whose_detail_is_pending_still_raises_the_modal_tu13() -> None:
    """Badge from the column, decide from the detail — and the false negative is the dangerous one.

    A stale ``pending_interrupt_id`` of ``None`` hides an approval from every channel and is never
    discovered, because nobody opens a thread they cannot see is waiting. Opening is the repair
    path, so a client that short-circuited the read on an unbadged row would make the one state that
    cannot heal itself permanent.

    Superseded (Phase 5 rebuilds this): `pending_interrupt_id` and the modal it raises both die with
    the interrupt/resume surface — a session is never "pending" on a human, so there is no stale flag
    to guard against.
    """
    service = Service()
    service.rows = {THREAD: thread(THREAD, pending=None)}
    service.pending = approval(thread_id=THREAD)

    async with shell(service) as (app, pilot):
        await app.open_thread(THREAD)
        screen = await open_modal(app, pilot)
        assert screen.request.interrupt_id == "i-1"


@pytest.mark.asyncio
async def test_opening_an_idle_thread_reads_the_detail_then_attaches_tu14() -> None:
    """There is no field that says "a run is in flight", so there is no heuristic to write.

    ``pending_interrupt_id`` means *parked*, not *running*. Attaching always is what makes the
    abandoned-turn case work — a run started from Telegram at lunch is still streaming when the TUI
    opens the thread — and RO-17 makes the call free when idle: 204, and it starts nothing.
    """
    service = Service()
    service.rows = {THREAD: thread(THREAD)}
    async with shell(service) as (app, pilot):
        service.calls.clear()
        await app.open_thread(THREAD)
        await wait_until(pilot, lambda: any(name == "attach_session" for name, _ in service.calls))

    assert [name for name, _ in service.calls] == ["get_session", "attach_session"]
    assert ("get_session", (THREAD,)) in service.calls
    assert not any(name == "start_session_run" for name, _ in service.calls)


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_new_thread_is_stamped_tui_and_a_telegram_thread_still_runs_tu17() -> None:
    """D3's promise is that a thread started in one channel is finishable from another.

    One ``if origin_channel == …`` deletes that guarantee in exactly the case the design is proudest
    of, and deletes it invisibly: the thread simply does not offer the action, with nothing on
    screen to say why.

    Superseded (Phase 5 rebuilds this): `origin_channel` as a stamped identity field on a thread is
    retired — the whole channel-is-identity model dies, replaced by channels attaching to a session
    rather than one channel owning it. The underlying promise (any channel can continue a
    conversation) survives and needs a session-shaped assertion once channels attach.
    """
    service = Service(events=[RunEnd(run_id="run-1", final_text="filed")])
    service.rows = {"t-tg": thread("t-tg", title="From my phone", origin="telegram")}

    async with shell(service) as (app, pilot):
        app.selected_agent = COOKING
        await app.action_new_thread()
        minted = [row for row in service.rows.values() if row.agent_id == COOKING]
        assert minted and minted[0].origin_channel == "tui"

        await app.open_thread("t-tg")
        app.query_one("#compose", Input).value = "carry on"
        app.set_focus(app.query_one("#compose", Input))
        await pilot.press("enter")
        await wait_until(pilot, lambda: any(name == "start_run" for name, _ in service.calls))
        assert ("start_run", ("t-tg", "carry on")) in service.calls


@pytest.mark.superseded
def test_origin_channel_is_written_but_never_branched_on_tu17() -> None:
    """The prohibition, over the syntax tree: the name appears as a value, never as a condition.

    Superseded (Phase 5 rebuilds this): `origin_channel` itself is retired along with the
    channel-is-identity model, so there is no such field left to guard against branching on.
    """
    for path in sources("pkb/tui"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                parts = [node.left, *node.comparators]
                for part in parts:
                    assert not (
                        isinstance(part, ast.Constant) and part.value == "origin_channel"
                    ), f"{path.name} branches on origin_channel"


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_conversation_and_the_expert_offers_come_from_the_detail_payload_tu19() -> None:
    """Replay is authoritative on open, and ``children`` is where an expert's branch survives.

    A live fan-out shows every expert's deltas; a reopened thread shows the Librarian's own messages
    plus the merged reply, because the experts' work lives in their own checkpoints. A UI whose live
    view is richer than its replayed view teaches the human that reopening loses information — when
    the detail is one click away, in a row the offer links to (TU-18).

    Superseded (Phase 5 rebuilds this): "expert offers" resolve to a `children` row addressed by the
    derived-thread id `expert_thread_id` mints for a fan-out — the whole parent/derived split is
    retired, so there is no derived thread left for an offer to point at.
    """
    service = Service(children=[{"thread_id": DERIVED, "agent_id": COOKING, "title": "Cooking"}])
    service.rows = {THREAD: thread(THREAD)}
    service.messages = [
        MessageView(role="human", text="where does this go?", created_at=None),
        MessageView(role="assistant", text="Filed under Cooking.", created_at=None),
    ]

    async with shell(service) as (app, pilot):
        await app.open_thread(THREAD)
        await wait_until(pilot, lambda: len(transcript(app)) >= 3)

        assert transcript(app)[:2] == [
            "you  where does this go?",
            "librarian  Filed under Cooking.",
        ]
        offers = app.view.offers if app.view else []
        assert [(entry.agent_id, entry.thread_id) for entry in offers] == [(COOKING, DERIVED)]


@pytest.mark.superseded
def test_an_expert_offer_is_never_recovered_from_the_reply_text_tu18() -> None:
    """Parsing the merged reply would make ``merge_reply``'s rendering a wire protocol.

    The offer's target is an envelope field (live) or a ``children`` row (after a reload). Text is
    never one of the two: once the reply scrolls away the live frames are gone, and a client reading
    prose for an agent id would silently offer a thread that does not exist.

    Superseded (Phase 5 rebuilds this): the "expert offer" feature it guards is built entirely on
    `SubagentStart` and the derived-thread `children` list from a fan-out — both retired with the
    parent/derived split, so there is no offer left to protect from a text-scraped reconstruction.
    """
    for path in sources("pkb/tui"):
        assert "final_text" not in identifiers(path), path.name
        names = called(path)
        assert "findall" not in names and "search" not in names, path.name


@pytest.mark.superseded
def test_a_scan_thread_is_filtered_with_the_seams_own_helper_tu20() -> None:
    """Maintenance is not a conversation, and the client asks the seam rather than spelling it out.

    A ``scan:`` row that reached the sidebar would offer a thread every list is meant to exclude,
    and a client with its own ``startswith("scan:")`` is a second answer to a question ``contracts``
    already answers — the drift that makes one channel show a row another hides.

    Superseded (Phase 5 rebuilds this): mixed — the `is_scan_thread` half uses `DERIVED`, a
    retired derived-thread id, as its non-scan example; the second half asserts `PkbClient.proposals`
    / `dismiss_proposal` exist and nothing named `apply*` does, which is the parked-proposal surface
    (`ProposalStore`, `/proposals`) retired outright. Marked whole rather than split, since both
    halves depend on retired design. A successor for "maintenance is not a conversation" needs
    whatever background-scan surface, if any, Phase 5 gives sessions.
    """
    from pkb.tui import app as app_module

    assert app_module.is_scan_thread is is_scan_thread
    assert is_scan_thread("scan:2026-08-08")
    assert not is_scan_thread(DERIVED)

    # And v1 lists and dismisses a proposal — applying one is a Layer 2 entry point that no route
    # exposes, so a client method for it could only be a button that always fails.
    assert hasattr(PkbClient, "proposals") and hasattr(PkbClient, "dismiss_proposal")
    assert not any(name.startswith("apply") for name in dir(PkbClient))


# --------------------------------------------------------------------------------------
# § the pump, the modal and the worker rules (TU-22, TU-23, TU-37, TU-38, TU-49)
# --------------------------------------------------------------------------------------

BURST = 200
"""Just under ``SUBSCRIBER_QUEUE_SIZE`` (256), because the stub publishes the whole script with no
awaits at all — past the queue the *hub* drops the subscriber and the test would be measuring the
fixture rather than the client. Two hundred frames is still far more than any human answers a modal
in, and a pump that stopped consuming leaves every one of them unapplied."""


def burst_script(interrupt_at: int = 2, total: int = BURST) -> list[Any]:
    """A run that raises an approval early and then keeps talking for a long time."""
    events: list[Any] = [SubagentStart(run_id="run-1", agent_id=COOKING)]
    events += [
        MessageDelta(run_id="run-1", agent_id=LIBRARIAN, text=".") for _ in range(interrupt_at)
    ]
    events.append(InterruptEvent(run_id="run-1", request=approval()))
    events += [MessageDelta(run_id="run-1", agent_id=LIBRARIAN, text="x") for _ in range(total)]
    events.append(RunEnd(run_id="run-1", final_text="filed"))
    return events


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_an_interrupt_opens_the_modal_while_the_pump_keeps_consuming_tu22_tu23() -> None:
    """The two worker rules, in the one flow where breaking either is unrecoverable.

    ``push_screen_wait`` raises ``NoActiveWorker`` outside a worker — at exactly the moment a human
    is being asked to approve a write. And the worker that waits must not be the one reading the
    socket: a human takes minutes over a diff while a fan-out runs at model pace, and a subscriber
    whose queue passes 256 is dropped. Both failures look like the daemon losing a run.

    So: the modal is open, and every one of the frames that arrived *after* the interrupt has
    already been folded into the view.

    Superseded (Phase 5 rebuilds this): the whole scenario is built on `InterruptEvent` raising
    `ApprovalModal` mid-stream and a scripted `resume` answering it — the interrupt/resume surface
    dies outright. "One pump keeps consuming while a worker waits on something else" is a real
    principle that survives, but needs a non-approval trigger; the burst-without-an-interrupt half
    is covered separately by `test_switching_threads_leaves_exactly_one_pump_running_tu38` and
    `test_the_pump_is_a_textual_worker_and_never_a_bare_task_tu22`.
    """
    service = Service(events=burst_script())
    service.rows = {THREAD: thread(THREAD)}
    service.resume_events = [RunEnd(run_id="run-2", final_text="filed")]

    async with shell(service) as (app, pilot):
        await app.open_thread(THREAD)
        app.query_one("#compose", Input).value = "where does this go?"
        app.set_focus(app.query_one("#compose", Input))
        await pilot.press("enter")

        await wait_until(pilot, lambda: isinstance(app.screen, ApprovalModal))
        assert app.view is not None
        await wait_until(pilot, lambda: app.view is not None and not app.view.running)

        # The modal is STILL open, and the whole burst that followed the interrupt was consumed.
        assert isinstance(app.screen, ApprovalModal)
        # The encoder saw the interrupt, so the terminal frame says *parked on you* rather than
        # done — which is the state the pump has to reach while the human is still reading.
        assert app.view.terminal == "interrupted"
        spoken = "".join(entry.text for entry in app.view.entries if entry.kind == "message")
        assert spoken.count("x") == BURST, spoken.count("x")

        # TU-49: the decisions go to the request's OWN thread — the expert's, not the streamed one.
        await pilot.press("a")
        await wait_until(pilot, lambda: any(name == "resume" for name, _ in service.calls))
        assert ("resume", (DERIVED, "i-1")) in service.calls, service.calls
        assert not any(
            args and args[0] == THREAD for name, args in service.calls if name == "resume"
        )


@pytest.mark.asyncio
async def test_switching_threads_leaves_exactly_one_pump_running_tu38() -> None:
    """One pump at a time, and the previous response closed deterministically.

    Two live pumps means two open httpx2 responses and two writers into one transcript — and because
    the cleanup rides on the generator's ``finally``, a detached task would leak the socket rather
    than close it. ``exclusive=True`` in one group is what makes "switch thread" mean "stop reading
    the old one", which is also what stops a stale run from painting into the pane the human is now
    looking at.
    """
    from textual.worker import WorkerState

    service = Service(events=[RunEnd(run_id="run-1", final_text="filed")], admission_delay=0.3)
    service.rows = {THREAD: thread(THREAD), "t-two": thread("t-two", title="Second")}

    async with shell(service) as (app, pilot):
        await app.open_thread(THREAD)
        first = app.pump(THREAD, message="one")
        await pilot.pause()
        assert first.state == WorkerState.RUNNING, "the fixture must have a pump to displace"

        second = app.pump("t-two", message="two")
        await pilot.pause()

        assert first.state == WorkerState.CANCELLED
        assert second.state == WorkerState.RUNNING
        alive = [worker for worker in app.workers if worker.group == "stream"]
        assert alive == [second], alive


def test_the_pump_is_a_textual_worker_and_never_a_bare_task_tu22() -> None:
    """A bare ``asyncio.create_task`` compiles, runs, and breaks only inside the approval modal.

    That is what makes this a structural rule rather than a style note: everything works until a
    human is asked about an irreversible write, and then ``push_screen_wait`` raises where nothing
    can recover. The package documents the rule in prose, so the check is over the syntax tree.
    """
    for path in sources("pkb/tui"):
        names = called(path)
        assert "create_task" not in names, f"{path.name} runs the pump outside a worker"
        assert "ensure_future" not in names, path.name


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_stream_that_raises_leaves_the_app_alive_with_its_transcript_tu37() -> None:
    """An unhandled exception in a default worker **kills the app**.

    In production that is a dropped daemon connection taking down the whole TUI instead of showing
    "reconnecting". Under ``run_test`` it is worse to debug: the exception is re-raised at the
    context manager's ``__aexit__``, after every assertion has passed, so the traceback points at
    the ``async with`` line rather than at the stream. This test fails at that exit if the pump ever
    loses ``exit_on_error=False``.

    Superseded (Task 8 rebuilds this): the crash-resilience principle (``exit_on_error=False``
    survives a raising stream) is permanent, but its proof here leans on ``service.messages`` — a
    session's running record has no read-back route yet, so ``client.thread()`` always returns an
    empty history (``pkb.tui.client``'s module docstring) and the seeded first message never
    appears in the transcript to survive the crash. A successor needs a live turn to seed that first
    line instead of pre-loaded history.
    """

    class Exploding(PkbClient):
        def run(self, thread_id: str, message: str) -> AsyncIterator[Any]:
            async def stream() -> AsyncIterator[Any]:
                raise RuntimeError("the socket went away mid-frame")
                yield  # pragma: no cover - unreachable, but this must be a generator

            return stream()

    service = Service()
    service.rows = {THREAD: thread(THREAD)}
    service.messages = [MessageView(role="human", text="where does this go?", created_at=None)]

    async with shell(service, client_factory=lambda url: Exploding(base_url=url)) as (app, pilot):
        await app.open_thread(THREAD)
        await wait_until(pilot, lambda: transcript(app) == ["you  where does this go?"])

        app.query_one("#compose", Input).value = "and this?"
        app.set_focus(app.query_one("#compose", Input))
        await pilot.press("enter")
        await pilot.pause()
        await asyncio.sleep(0.05)
        await pilot.pause()

        assert app.is_running
        assert transcript(app)[0] == "you  where does this go?"
        # Still responsive afterwards: the failure cost one stream, not the session.
        await pilot.press("p")
        await wait_until(pilot, lambda: app.selected_agent is None)


# --------------------------------------------------------------------------------------
# § the approval modal — what the human reads (TU-39, TU-40, TU-41, TU-45, TU-46)
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_a_new_file_description_carries_no_colour_and_a_diff_colours_only_its_hunks_tu40(
    kb: Path,
) -> None:
    """The golden, over both real ``describe_write`` shapes.

    Superseded (Phase 5 rebuilds this): `diff_text` is `pkb.tui.modal`'s only caller of
    `describe_write`/`GateReason`, and it exists solely to render `ApprovalModal`'s body — which
    dies with the interrupt/resume surface (no gates, so nothing is ever described for approval).
    The colour-only-inside-a-real-hunk hazard this golden protects against is real and would need a
    successor renderer if Phase 5 gives sessions any diff display at all.

    Measured with the real lexer below: ``rich``'s ``diff`` grammar paints ``- Pull at 130F`` in a
    *new-file* proposal the **same** colour it paints a genuine deletion inside a real hunk. Markdown
    bullets are the most common content in this knowledge base, so a modal built on a diff lexer
    would routinely tell the human that the lines being added are being removed — on a write with no
    undo. ``@@`` is the observable proof the server actually produced a diff, and it is the only
    thing that may switch colour on.
    """
    new_file = new_file_description(kb)
    assert "(new file)" in new_file and "@@" not in new_file
    assert "\n- Pull at 130F" in new_file, "the fixture must contain markdown bullets"

    existing = existing_file_description(kb)
    assert "@@" in existing
    assert "\n-- Pull at 130F" in existing, "the same bullet, this time genuinely being removed"

    # The hazard, demonstrated rather than asserted from memory: the lexer gives the ADDED bullet in
    # the new-file proposal the identical style it gives a REMOVED line in the real diff.
    added_bullet = _style_at(new_file, "- Pull at 130F")
    real_removal = _style_at(existing, "-- Pull at 130F")
    plain_line = _style_at(new_file, "Tool: write_file")
    assert added_bullet.color == real_removal.color
    assert added_bullet.color != plain_line.color

    # What the modal actually renders: no colour at all on the proposal…
    plain = diff_text(new_file)
    assert [span for span in plain.spans if span.style in {"red", "green"}] == []
    assert plain.spans == []
    assert plain.plain == new_file

    # …and colour only at or after the first hunk header on the diff.
    coloured = diff_text(existing)
    hunk = coloured.plain.index("@@")
    painted = [span for span in coloured.spans if span.style in {"red", "green"}]
    assert painted, "a real diff must colour its hunk"
    assert all(span.start >= hunk for span in painted)
    assert {span.style for span in painted} == {"red", "green"}


def _style_at(description: str, needle: str) -> Any:
    """The style ``rich``'s diff grammar would give the line containing ``needle``."""
    lexed = Syntax(description, "diff").highlight(description)
    offset = description.index(needle)
    spans = [span for span in lexed.spans if span.start <= offset < span.end]
    assert spans, needle
    return spans[-1].style


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_bracketed_kb_path_renders_instead_of_killing_the_app_tu41(kb: Path) -> None:
    """The single most likely production crash in this layer, and it is one keyword away.

    ``Static`` and ``Label`` default to ``markup=True``, and Textual's parser raises ``MarkupError``
    on a closing tag with no opener — which a KB-relative POSIX path in square brackets is. The
    exception kills the app, not the widget, and every field this modal shows carries paths and free
    model text.

    Superseded (Phase 5 rebuilds this): the crash guarded against is specific to `ApprovalModal`
    rendering a KB path in square brackets, and the modal dies with the interrupt/resume surface.
    The `Content.from_markup` hazard itself is real and generic — whatever screen next renders a raw
    KB-relative path needs an equivalent guard.
    """
    hazard = "[/kb/Cooking/notes]"
    with pytest.raises(MarkupError):
        Content.from_markup(f"See {hazard} for it")

    description = f"Approval required: breadth-approval\nTool: write_file\nSee {hazard} for it\n"
    request = approval(action(description=description, reason=f"breadth {hazard}"))

    async with offline_app() as (app, pilot):
        app.push_screen(ApprovalModal(request))
        screen = await open_modal(app, pilot)
        assert app.is_running
        body = str(screen.query_one("#description-0", Static).content)
        assert hazard in body
        assert any(hazard in str(label.content) for label in screen.query(Label))


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_modal_body_equals_the_description_with_the_kb_deleted_tu39(kb: Path) -> None:
    """The bytes the human approves are the bytes the server rendered, and nothing else.

    ``description`` already holds the server-rendered diff, produced once so this modal and
    Telegram's keyboard decide about the same thing. Truncating or reflowing it is worse than not
    showing it — the human would approve an irreversible write from a fragment — and recomputing it
    here is impossible anyway: the tree is deleted before the modal is built, exactly as it would be
    on a TUI running on another host.

    Superseded (Phase 5 rebuilds this): `ApprovalModal` dies with the interrupt/resume surface, so
    there is no approval body left to render byte-identically. The "render exactly what the server
    sent, never recompute" principle is real and would need a successor wherever a session's record
    shows a write.
    """
    proposal = new_file_description(kb)
    diff = existing_file_description(kb)
    shutil.rmtree(kb)
    assert not kb.exists()

    async with offline_app() as (app, pilot):
        app.push_screen(ApprovalModal(approval(action(description=proposal))))
        screen = await open_modal(app, pilot)
        assert str(screen.query_one("#description-0", Static).content) == proposal
        app.pop_screen()
        await pilot.pause()

        app.push_screen(ApprovalModal(approval(action(description=diff))))
        screen = await open_modal(app, pilot)
        rendered = str(screen.query_one("#description-0", Static).content)

    # The diff branch re-joins its lines, so it is line-for-line identical plus one trailing
    # newline — pinned exactly, so any *other* transformation fails this assertion.
    assert rendered.splitlines() == diff.splitlines()
    assert rendered == diff + "\n"


@pytest.mark.superseded
def test_the_modal_computes_no_diff_of_its_own_tu39() -> None:
    """A second diff renderer is a second answer to "what am I approving".

    The displayed diff is context-limited (``n=3``), so bytes reconstructed from it would differ
    subtly from what the human read — the one thing an approval must never do — and under I2 this
    package could not read the tree correctly to build a better one.

    Superseded (Phase 5 rebuilds this): "what am I approving" presumes an approval to compute a diff
    for; `ApprovalModal` dies with the interrupt/resume surface and takes this prohibition's subject
    with it.
    """
    for path in sources("pkb/tui"):
        names = identifiers(path)
        assert "difflib" not in names, path.name
        assert "unified_diff" not in names, path.name
        assert "SequenceMatcher" not in names, path.name


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_destructive_reason_carries_the_no_undo_warning_and_its_slug_tu45() -> None:
    """The reason is the most consequential framing of the action, and it is already ordered.

    Re-wording the slug makes it unsearchable and unstable across channels; with no version control
    in the first draft, a delete approved by mistake is simply gone, and the modal is the last place
    that can say so out loud.

    Superseded (Phase 5 rebuilds this): built entirely around `ApprovalModal` and `GateReason`,
    both of which die with the interrupt/resume surface — the operator's instruction is the
    approval, so there is no modal left to warn from. The underlying worry (an irreversible write
    with no confirmation of what it does) is real and needs a successor wherever sessions surface a
    destructive write.
    """
    async with offline_app() as (app, pilot):
        for reason in GateReason:
            screen = ApprovalModal(
                approval(action(reason=reason.value, allowed=("approve", "reject")))
            )
            app.push_screen(screen)
            await open_modal(app, pilot)
            labels = [str(label.content) for label in screen.query(Label)]
            assert any(text.endswith(f"·  {reason.value}") for text in labels), reason.value
            destructive = reason.value in {"delete", "topic-creation", "conflict-resolution"}
            warned = any(text == "There is no undo for this." for text in labels)
            assert warned is destructive, reason.value
            app.pop_screen()
            await pilot.pause()


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_validation_label_is_lifted_above_the_diff_tu46(kb: Path) -> None:
    """The label exists so the human can reject or edit *instead of* approving a doomed draft.

    HITL fires before the validator runs, so approving an invalid draft spends one of three attempts
    (MW-14) on content the human endorsed. Buried under a two-hundred-line diff it is a label nobody
    reads, and the mechanism buys nothing. Detection is a prefix match on the server's own text —
    never a re-run of ``validate_content``, which this package cannot do.

    Superseded (Phase 5 rebuilds this): HITL — the interrupt/resume surface — is what this label
    warns *before*, and it is retired outright along with `ApprovalModal`. The operator's instruction
    is the approval, so there is no "spend one of three attempts" scenario left to guard against.
    """
    invalid = describe_write(
        GateReason.BREADTH_APPROVAL,
        "write_file",
        "Cooking/notes/summary.md",
        {"content": "\n# Notes summary\n\nNo frontmatter at all.\n"},
        scan(kb),
    )
    assert VALIDATION_LABEL in invalid, "the modal's constant must track gates._validation_label"
    assert validation_header(invalid).startswith(VALIDATION_LABEL)
    assert validation_header(new_file_description(kb)) == ""

    async with offline_app() as (app, pilot):
        app.push_screen(ApprovalModal(approval(action(description=invalid))))
        screen = await open_modal(app, pilot)
        texts = [str(widget.content) for widget in screen.query("#approval Static")]
        header = next(index for index, text in enumerate(texts) if VALIDATION_LABEL in text)
        body = next(index for index, text in enumerate(texts) if text.count("\n") > 3)
        assert header < body


# --------------------------------------------------------------------------------------
# § the approval modal — what the human can do (TU-42, TU-43, TU-44, TU-47, TU-48, TU-50)
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_controls_are_built_from_allowed_decisions_and_never_widened_tu42() -> None:
    """A delete gates as ``('approve','reject')`` — there is no ``edit`` and no ``respond``.

    Arch §6's "approve / edit / reject" is the common case, not the contract. A modal that drew the
    four literal decision types would offer ``edit`` on a delete, where the server answers 400 — a
    failure the human caused by pressing a button the TUI drew. Where ``edit`` is missing the modal
    says why, rather than leaving a hole.

    Superseded (Phase 5 rebuilds this): "gates as" presumes a gate; approve/edit/reject decisions and
    `ApprovalModal` both die with the interrupt/resume surface, so there is no `allowed_decisions` set
    left to build controls from.
    """
    async with offline_app() as (app, pilot):
        screen = ApprovalModal(approval(action(tool="delete", allowed=("approve", "reject"))))
        app.push_screen(screen)
        await open_modal(app, pilot)

        controls = [str(button.id) for button in screen.query(Button)]
        assert controls == ["decide-approve", "decide-reject", "decide-later"]
        assert "decide-edit" not in controls
        assert "decide-respond" not in controls
        assert any(
            "cannot be usefully edited" in str(label.content) for label in screen.query(Label)
        )

        # A planted decision is refused locally, by the seam's own validator, before any transport.
        screen.action_decide("edit")
        await pilot.pause()
        assert isinstance(app.screen, ApprovalModal), "a refused decision must not dismiss"


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_a_two_action_approval_submits_one_resolution_in_order_tu43() -> None:
    """RT-41 batches every interruptible call of one message into a single interrupt.

    Two writes in one approval is therefore the normal case, and answering them one at a time makes
    the second stale against the interrupt the first already resolved — a 409 on an answer the human
    definitely gave, with no way to tell afterwards which half was applied.

    Superseded (Phase 5 rebuilds this): the batched interrupt this test resolves is the interrupt/
    resume surface itself — writes land immediately now, one at a time, with no batch to answer as
    one `Resolution`.
    """
    first = action(args={"file_path": "Cooking/notes/a.md", "content": "a"})
    second = action(args={"file_path": "Cooking/notes/b.md", "content": "b"})
    answers: list[Any] = []

    async with offline_app() as (app, pilot):
        app.push_screen(ApprovalModal(approval(first, second)), answers.append)
        await pilot.pause()
        await pilot.press("a")
        await wait_until(pilot, lambda: bool(answers))

    resolution = answers[0]
    assert isinstance(resolution, Resolution)
    assert len(resolution.decisions) == 2
    assert [decision.type for decision in resolution.decisions] == ["approve", "approve"]
    assert resolution.interrupt_id == "i-1"
    assert len(resolution.body()["decisions"]) == 2


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_editor_opens_one_field_per_arg_including_a_tool_with_no_path_tu44() -> None:
    """One key→string editor covers all five gated tools; a document editor covers exactly one.

    ``edit_file`` has ``old_string``/``new_string`` and no document at all, and ``create_topic`` has
    no ``file_path`` — only ``name``/``title``/``description``, which is the whole point of that
    gate. And the fields are seeded from ``args``, never from the rendered description: the diff is
    context-limited, so content reconstructed from it would differ from what the human read.

    Superseded (Phase 5 rebuilds this): "one field per arg" is the ``edit`` decision's editor inside
    `ApprovalModal`, and `edit` dies with the interrupt/resume surface — the operator writes directly
    rather than editing a parked proposal.
    """
    args = {"name": "Braising", "title": "Braising", "description": "Low and slow, in liquid."}
    description = (
        "Approval required: topic-creation\nTool: create_topic\nSomething else entirely.\n"
    )
    request = approval(
        action(tool="create_topic", args=args, description=description, reason="topic-creation")
    )

    async with offline_app() as (app, pilot):
        app.push_screen(ApprovalModal(request))
        screen = await open_modal(app, pilot)
        fields = list(screen.query(".arg"))
        assert [str(field.id) for field in fields] == [
            "arg-0-name",
            "arg-0-title",
            "arg-0-description",
        ]
        assert [field.value for field in fields] == list(args.values())
        assert all(field.value != description for field in fields)
        assert "file_path" not in args


@pytest.mark.superseded
def test_the_modal_never_reconstructs_content_from_the_description_tu44() -> None:
    """The prohibition itself: no parser of the server's rendered text lives in this package.

    Superseded (Phase 5 rebuilds this): the prohibition is scoped to `ApprovalModal`'s edit path,
    which dies with the interrupt/resume surface — there is no modal-side reconstruction of content
    left to forbid.
    """
    for path in sources("pkb/tui"):
        names = identifiers(path)
        assert "proposed_content" not in names, path.name
        assert "apply_patch" not in names, path.name


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_escape_dismisses_without_deciding_and_sends_nothing_tu47() -> None:
    """An escape that quietly submitted a reject would file nothing and say the human refused.

    Afterwards that is indistinguishable from a considered refusal, and the agent will not retry.
    Durable parking is the design's promise: the interrupt stays in the checkpoint, answerable from
    any channel, and the modal has to be able to get out of the way without spending it.

    Superseded (Phase 5 rebuilds this): "durable parking" is exactly the interrupt/resume surface
    that is retired — no gates, no parked proposals, no pending queue anywhere, so there is nothing
    left in a checkpoint for an escape to leave untouched.
    """
    answers: list[Any] = []
    async with offline_app() as (app, pilot):
        app.push_screen(ApprovalModal(approval()), answers.append)
        await pilot.pause()
        await pilot.press("escape")
        await wait_until(pilot, lambda: bool(answers))
        assert not isinstance(app.screen, ApprovalModal)

    assert answers == [None]
    assert not any(isinstance(answer, Resolution) for answer in answers)


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_an_approval_with_no_live_run_opens_from_the_detail_payload_tu48() -> None:
    """The scenario the whole daemon architecture exists for.

    The human comes back hours later, from a different channel, to an approval raised this morning.
    There is no stream to attach to and no local state — one ``GET /threads/{id}`` has to be enough,
    and answering it starts a fresh stream on the request's own thread.

    Superseded (Phase 5 rebuilds this): "an approval raised this morning" cannot happen once nothing
    parks — the operator's instruction is the approval, so there is no cold-open detail payload to
    resume from and no derived thread (`DERIVED`) for the resume to target.
    """
    service = Service()
    service.rows = {THREAD: thread(THREAD, pending="i-1")}
    service.pending = approval()
    service.resume_events = [RunEnd(run_id="run-2", final_text="filed")]

    async with shell(service) as (app, pilot):
        await app.open_thread(THREAD)
        await wait_until(pilot, lambda: isinstance(app.screen, ApprovalModal))
        assert not any(name == "start_run" for name, _ in service.calls)

        await pilot.press("a")
        await wait_until(pilot, lambda: any(name == "resume" for name, _ in service.calls))
        assert ("resume", (DERIVED, "i-1")) in service.calls


@pytest.mark.superseded
def test_the_binding_tables_are_golden_and_approve_is_not_next_to_reject_tu50() -> None:
    """A golden list makes a rebind a reviewed diff rather than a surprise.

    There is no undo, so approve and reject must not be keys a finger can confuse, and the modal
    must not be dismissible by accident. The table also documents the surface step 5 has to offer
    through a different affordance — a Telegram keyboard has no ``escape``.

    Superseded (Phase 5 rebuilds this): mixed — `bindings_of(ApprovalModal)`'s whole golden list and
    the approve/reject adjacency check die with the modal itself, and `PkbApp`'s own table pins
    ``"P" -> "proposals"``, which dies with the parked-proposal surface. Marked whole rather than
    split, since the file-level golden nature of the assertion resists a clean per-binding split.
    `PkbApp`'s remaining bindings (``p``/``n``/``R``/``c``/``q``) need a fresh golden once Phase 5
    settles what the session-era keymap actually is.
    """
    assert bindings_of(PkbApp) == [
        ("p", "pending"),
        ("n", "new_thread"),
        ("R", "rename"),
        ("P", "proposals"),
        ("c", "cancel"),
        ("q", "quit"),
    ]
    assert bindings_of(ApprovalModal) == [
        ("a", "decide('approve')"),
        ("e", "decide('edit')"),
        ("r", "decide('reject')"),
        ("escape", "later"),
    ]

    keys = dict(bindings_of(ApprovalModal))
    assert "r" not in neighbours("a"), "approve and reject must not be adjacent keys"
    assert "a" not in neighbours("r")
    assert keys["escape"] == "later", "escape must never resolve the approval"
    assert len(set(keys)) == len(keys)


# --------------------------------------------------------------------------------------
# § the fixture the streaming tests stand on (P-14b)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_servers_in_one_process_both_stream_p14b() -> None:
    """Proof that the autouse reset works — without it every later streaming test is a lie.

    Measured: server #1 stops, and server #2 in the same process delivers ``run.started`` followed
    immediately by ``run.error`` over a run that is perfectly healthy, because
    ``AppStatus.should_exit`` stayed ``True``. A suite that spins a uvicorn per test would then fail
    from the second test onward, and the failure reads as a daemon bug rather than a fixture one.
    """
    for _ in range(2):
        service = Service(
            events=[
                MessageDelta(run_id="run-1", agent_id=LIBRARIAN, text="filed"),
                RunEnd(run_id="run-1", final_text="filed"),
            ]
        )
        service.rows = {THREAD: thread(THREAD)}  # a run now requires a real session to target (S-9)
        async with daemon(service) as base_url:
            client = PkbClient(base_url=base_url)
            async with client.opened():
                frames = [frame async for frame in client.run(THREAD, "hi")]
        assert [frame.type for frame in frames][-1] == "run.end"
        assert frames[-1].status == "completed"


# --------------------------------------------------------------------------------------
# The affordances the first pass left unbuilt, 2026-08-08
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_a_routed_thread_offers_no_rename_tu16() -> None:
    """A dead control teaches the human to distrust every other one.

    `PATCH` on a derived thread is refused server-side (SV-28): its generated title states where it
    came from, and the human never named it. Offering the rename anyway means the first time they
    try it, the UI does nothing and gives no reason.

    Superseded (Phase 5 rebuilds this): "derived thread" is the `<parent>::<agent>` fan-out
    addressing retired outright — there is no more thread whose title states where it was routed
    from, and `/name` on a session is a real, always-offered command rather than a refused `PATCH`.
    """
    assert "R" in {binding[0] for binding in PkbApp.BINDINGS}


@pytest.mark.superseded
def test_a_scan_proposal_is_labelled_and_is_not_a_link_tu20() -> None:
    """`scan:` threads are filtered out of every list by rule (RT-58), so a link is a dead end.

    A background maintenance write is not a conversation the human can open, and on a knowledge base
    that has been running a while the majority of the proposals list may be exactly that.

    Superseded (Phase 5 rebuilds this): `proposal_line` renders a row of the parked-proposal list —
    `ProposalStore` and `/proposals` are retired outright, so there is no proposals list left for a
    `scan:` row to be labelled inside.
    """
    ordinary = proposal_line(
        {
            "thread_id": "3f0c9a12-8e64-4a1f-9b77-2c5d0a11e4d3",
            "action": {
                "reason": "breadth-approval",
                "args": {"file_path": "Cooking/notes/summary.md"},
            },
        }
    )
    maintenance = proposal_line(
        {
            "thread_id": "scan:topic/cooking:0f8e",
            "action": {"reason": "conflict-resolution", "args": {}},
        }
    )

    assert "background maintenance" not in ordinary
    assert "background maintenance" in maintenance
    assert "Cooking/notes/summary.md" in ordinary


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_proposals_pane_says_what_it_actually_covers_tu21() -> None:
    """It is **not** "what agents wanted to write", and the difference is not pedantry.

    The gate table is the same for every channel (C-7, MC-18) and it leaves plain note writes and
    first-write reference files **ungated** — so an MCP or scan-originated note lands with no human,
    no proposal, and no entry in this list. A human reading "what agents wanted to write" would
    conclude they had seen everything an agent did, which is the opposite of true. A false belief
    about coverage is worse than no view at all.

    Superseded (Phase 5 rebuilds this): the gate table and the proposals pane it describes are both
    retired with the interrupt/resume surface — every write lands immediately, so there is no
    "what needed your approval" pane left to be honest about its coverage of.
    """
    stub = StubService()
    client = PkbClient(base_url="http://127.0.0.1:1")
    app = PkbApp(client)
    app.client = _DirectClient(stub)  # type: ignore[assignment]

    async with app.run_test() as pilot:
        await app.action_proposals()
        await pilot.pause()
        copy = " ".join(str(widget.render()) for widget in app.query("#transcript Static"))

    assert "needed your approval" in copy
    assert "not available yet" in copy
    assert "thread list" in copy, "it points at where the rest of the record lives"
    assert "wanted to write" not in copy


class _DirectClient:
    """The one call the proposals pane makes, without a socket.

    `base_url` is here because the app names it in its no-daemon message (TU-7), which the pane
    never reaches — but a stand-in that omits it fails for a reason unrelated to the rule.
    """

    base_url = "http://127.0.0.1:8765"

    def __init__(self, service: StubService) -> None:
        self._service = service

    async def proposals(self) -> list[dict[str, object]]:
        return [
            {
                "proposal_id": p.proposal_id,
                "thread_id": p.thread_id,
                "action": {"reason": p.action.reason, "args": dict(p.action.args)},
            }
            for p in await self._service.list_proposals()
        ]
