"""The Layer 2 → Layer 3 seam, as an acceptance test — SV-1, SV-2, SV-4, SV-30, AP-2.

Layer 2 proved that ``pkb.contracts`` and a *stub* service compile and run with the harness banned
from the interpreter (`tests/agents/test_contracts.py`). Step 3's acceptance test is that **the same
is true of the real service and every server module** (§6.1, SV-30, AP-2), so the technique is
promoted rather than repeated: a subprocess whose ``sys.meta_path`` refuses ``deepagents``,
``langgraph``, ``langchain`` and ``langchain_core`` imports every Layer 3 module, and then
constructs the real :class:`~pkb.service.runtime.RuntimeService` over a fake runtime and drives it.

Why a subprocess rather than a linter: ``lint-imports`` reads the import graph, and an import graph
cannot tell you whether a class *needs* a harness object to do its job. Only executing it can, and
only executing it with the harness genuinely absent. The harness import in ``open_service`` lives
inside the function for exactly this reason — the class must be constructible without it — and
nothing but this file would notice if it moved back to module scope.

What the seam buys is in §6.1: because ``PkbService``'s every type is expressible without the
harness, every route, every SSE frame, every MCP tool and every TUI screen tests against a stub —
no runtime, no checkpointer, no model, no SQLite. The rules pinned below are the conditions that
keep that true, and each one of them is a way the seam has been lost in some other codebase: a
harness type in one signature (SV-1), a second module reaching for the runtime (SV-2), a config dict
assembled up here (SV-18), a convenience write (SV-22), a model client of one's own (SV-25).

Every AST scan here is run twice — over the real tree, where it must find nothing, and over a
planted module doing the forbidden thing, where it must find it. A rule that forbids something
nobody has written yet passes trivially forever otherwise.
"""

from __future__ import annotations

import ast
import asyncio
import builtins
import importlib
import inspect
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import aiosqlite
import pytest

from pkb.contracts import (
    AgentDescriptor,
    AgentEvent,
    ApprovalMode,
    ApprovalRequest,
    Decision,
    MessageView,
    RunEnd,
    ScanRequest,
    ScanResult,
)
from pkb.core.models import FlushReport
from pkb.service import PkbService
from pkb.service import runtime as service_runtime
from pkb.service.runtime import Runtime, RuntimeService
from pkb.service.threads import mint_thread_id

HARNESS = ("deepagents", "langgraph", "langchain", "langchain_core")
"""The four roots I2 names. Banning the roots bans every submodule (see ``_BAN``)."""

HARNESS_TYPES = frozenset(
    {"AgentGraph", "Interrupt", "Command", "RunnableConfig", "BaseChatModel", "StateSnapshot"}
)
"""The types SV-1 names, plus the two that would sneak in first if a signature widened."""

PKB = Path(str(importlib.import_module("pkb").__file__)).resolve().parent
SERVICE_DIR = PKB / "service"
SERVER_DIR = PKB / "server"


# --------------------------------------------------------------------------------------
# Walking the two packages
# --------------------------------------------------------------------------------------


def _sources(*directories: Path) -> dict[Path, ast.Module]:
    """Every module of the given packages, parsed. Discovered, never listed.

    Discovery matters: a rule that holds over a hand-written list of files stops holding the day
    somebody adds ``pkb/server/telegram.py``, and that is precisely the day it is most needed.
    """
    found = {
        path: ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for directory in directories
        for path in sorted(directory.glob("*.py"))
    }
    assert found, f"no modules found under {directories} — the walk is broken, not the code"
    return found


def _planted(source: str) -> dict[Path, ast.Module]:
    """One synthetic module in the shape ``_sources`` returns.

    Every scan below is run twice: over the real tree, where it must find nothing, and over a
    planted module that does the forbidden thing, where it must find it. A checker that cannot fail
    is not a check, and a green scan proves nothing on its own — least of all a scan whose whole
    job is to notice something nobody has written yet.
    """
    return {Path("planted.py"): ast.parse(source)}


def _module_name(path: Path) -> str:
    relative = path.relative_to(PKB.parent).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(tree: ast.Module) -> list[tuple[str, tuple[str, ...]]]:
    """``(module, imported names)`` for every import at any depth, function-level included.

    Function-level is the whole point: ``open_service`` imports ``pkb.agents`` inside its body, and
    a check that only looked at module scope would miss both that import and any harness import
    hidden the same way.
    """
    found: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, ()) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.module, tuple(alias.name for alias in node.names)))
    return found


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """Every bare string statement: module, class and function docstrings, and attribute docs.

    These are prose. A rule about what the *code* does must not be defeated by a docstring that
    explains why the code does not do it — half the modules here name ``checkpoint_ns`` precisely to
    say they never set one.
    """
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }


def _dotted(node: ast.expr) -> str:
    """``a.b.c`` for an attribute chain, ``a`` for a name, ``""`` for anything else."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return ""


def _called_name(func: ast.expr) -> str:
    """The name being called, however it was reached.

    ``func.attr`` rather than the last segment of :func:`_dotted`, because the receiver is often not
    a name at all: ``(kb_root / "Cooking/notes/x.md").write_text(...)`` has a ``BinOp`` receiver, and
    a check that only understood dotted paths would wave through the single most likely way a stray
    write actually gets written.
    """
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _calls(tree: ast.Module) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


# --------------------------------------------------------------------------------------
# The harness-banned subprocess
# --------------------------------------------------------------------------------------

_BAN = '''
import importlib.abc
import sys

BANNED = __BANNED__


class _Ban(importlib.abc.MetaPathFinder):
    """Refuse these packages outright — a machine on which the harness is simply not installed."""

    def find_spec(self, fullname, path=None, target=None):
        for banned in BANNED:
            if fullname == banned or fullname.startswith(banned + "."):
                raise ImportError("banned in this process: " + fullname)
        return None


sys.meta_path.insert(0, _Ban())


def leaked():
    return sorted(
        module
        for module in sys.modules
        for banned in BANNED
        if module == banned or module.startswith(banned + ".")
    )
'''


def _run_banned(script: str, banned: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _BAN.replace("__BANNED__", repr(banned)) + script],
        capture_output=True,
        text=True,
        check=False,
    )


def _import_every(directories: tuple[Path, ...], extra: tuple[str, ...]) -> str:
    names = [_module_name(path) for path in _sources(*directories)] + list(extra)
    return "".join(f"import {name}\n" for name in sorted(names))


# The real service, constructed and driven with the harness absent (SV-4, SV-30). The fake runtime
# is built from `pkb.contracts` alone: if `RuntimeService` needed one harness object to function —
# a `RunnableConfig` to pass down, a `Command` to build a resume — this is where that shows up, as
# a NameError in a subprocess rather than as an architectural argument two layers later.
_DRIVE_THE_REAL_SERVICE = '''
import asyncio
from pathlib import Path

import aiosqlite

from pkb.contracts import (
    AgentDescriptor,
    MessageComplete,
    MessageView,
    RunEnd,
    UnknownAgentError,
)
from pkb.service.runtime import Runtime, RuntimeService

COOKING = "topic/cooking"

CATALOG = [
    AgentDescriptor(
        agent_id="librarian",
        title="Librarian",
        description="Routes each item to the right Topic Expert.",
        has_custom_expert=False,
        model_id="ollama:deepseek-v4-flash:cloud",
    ),
    AgentDescriptor(
        agent_id=COOKING,
        title="Cooking",
        description="Food, heat and time.",
        has_custom_expert=True,
        model_id="ollama:deepseek-v4-flash:cloud",
    ),
]


class FakeRuntime:
    """Satisfies `pkb.service.runtime.Runtime` structurally, and knows nothing of any graph."""

    db_path = Path("never-opened.sqlite")

    def list_agents(self):
        return CATALOG

    def run(self, agent_id, thread_id, message, *, approval_mode="interactive", run_id=None):
        async def stream():
            yield MessageComplete(run_id="R1", agent_id=agent_id, text="echo: " + message)
            yield RunEnd(run_id="R1", final_text="echo: " + message)

        return stream()

    def resume(self, agent_id, thread_id, decisions, *, interrupt_id=None, run_id=None):
        async def stream():
            yield RunEnd(run_id="R2", final_text="resumed")

        return stream()

    async def cancel(self, run_id):
        return None

    async def pending_approval(self, agent_id, thread_id):
        return None

    async def history(self, agent_id, thread_id):
        return [MessageView(role="human", text="how do I sear", created_at=None)]

    async def delete_thread(self, thread_id):
        return None

    async def request_scan(self, request):
        raise NotImplementedError

    async def regenerate(self):
        raise NotImplementedError


# Structural, not nominal: every member of the Protocol answered by an object that never heard of
# `PkbRuntime`. This is SV-4 stated as an assertion rather than as a type annotation.
MEMBERS = [name for name in vars(Runtime) if not name.startswith("_")]
MEMBERS += list(Runtime.__annotations__)
assert len(MEMBERS) == 10, MEMBERS
for member in MEMBERS:
    assert hasattr(FakeRuntime, member), member


async def main():
    connection = await aiosqlite.connect(":memory:", isolation_level=None)
    service = RuntimeService(FakeRuntime(), connection)
    await service.setup()

    thread = await service.create_thread(COOKING, origin_channel="telegram")
    assert thread.agent_id == COOKING
    assert thread.title is None, thread.title
    assert thread.origin_channel == "telegram"

    try:
        await service.create_thread("topic/atlantis")
    except UnknownAgentError:
        pass
    else:
        raise AssertionError("an unknown agent must be refused before the row is inserted")
    assert len(list(await service.list_threads())) == 1

    listed = await service.list_threads(COOKING)
    assert [row.thread_id for row in listed] == [thread.thread_id]
    assert list(await service.list_threads("librarian")) == []

    detail = await service.get_thread(thread.thread_id)
    assert detail.thread.thread_id == thread.thread_id
    assert detail.descriptor is not None and detail.descriptor.title == "Cooking"
    assert detail.pending is None
    assert [message.role for message in detail.messages] == ["human"]

    subscription = await service.start_run(thread.thread_id, "hello")
    events = [event async for event in subscription.events]
    assert isinstance(events[-1], RunEnd), events
    assert events[-1].final_text == "echo: hello"

    await asyncio.sleep(0.05)  # let the off-critical-path titling task finish (TT-2)

    await service.delete_thread(thread.thread_id)
    assert list(await service.list_threads()) == []
    await connection.close()


asyncio.run(main())
assert leaked() == [], leaked()
print("OK")
'''


# --------------------------------------------------------------------------------------
# SV-30 / SV-4 — the acceptance test
# --------------------------------------------------------------------------------------


def test_every_layer3_module_imports_with_the_harness_banned_sv30() -> None:
    """The one-line version of I2 from §6.1, run for real instead of asserted in prose.

    ``import pkb.server; assert not {"deepagents", …} & set(sys.modules)`` is the whole guarantee:
    a transport that pulls in the harness pulls in a model client, a checkpointer and a graph
    compiler, and every future adapter — an ACP one, a second daemon, a packaged TUI — inherits
    that weight and that coupling. A ban rather than a `sys.modules` check afterwards, because an
    import that merely *happened* to be lazy today would pass the check and fail the day it moved.
    """
    result = _run_banned(
        _import_every((SERVICE_DIR, SERVER_DIR), ("pkb.contracts", "pkb.packs", "pkb.daemon"))
        + "assert leaked() == [], leaked()\nprint('OK')\n",
        HARNESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("OK")


@pytest.mark.superseded
def test_the_real_service_runs_against_a_fake_runtime_with_the_harness_banned_sv4() -> None:
    """Importing is half of it; the class has to *work*, which is why this drives it (SV-4, SV-30).

    Superseded (Task 3/5 rebuild this): the embedded ``_DRIVE_THE_REAL_SERVICE`` script is entirely
    thread-CRUD-shaped — ``create_thread(..., origin_channel=...)``, ``list_threads``, ``get_thread``,
    ``delete_thread`` — entangled with the ``start_run``/events check that survives. The SV-4/SV-30
    architectural principle (the seam works end to end with the harness banned) is permanent and
    needs a session-shaped driver script; nothing in this plan currently owns rewriting it, so
    whoever touches the seam next should notice this gap rather than leave it silently uncovered.

    ``RuntimeService`` is constructor-injected with a structural ``Runtime``, so the fake below —
    built from ``pkb.contracts`` and nothing else — is a complete substitute. If a single method
    reached for a harness object to do its job, this run would fail where no linter and no type
    checker looks: at execution, with the harness genuinely absent.

    It also pins the seam's *shape*. Every argument crossing it and every value coming back is a
    ``pkb.contracts`` type or a Layer 3 dataclass of primitives, which is what makes the stub in
    ``tests/server/stub.py`` — and therefore the entire free, fast, keyless server suite — possible.
    """
    result = _run_banned(_DRIVE_THE_REAL_SERVICE, HARNESS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("OK")


def test_the_server_never_reaches_pkb_agents_even_transitively_ap2() -> None:
    """``pkb.server`` gets the strict contract: no ``pkb.agents``, by any path (AP-2, decision B).

    The service arrives as ``open_service`` — a factory typed against the Protocol — precisely so
    that no server module ever has a reason to name the composition root. Banning ``pkb.agents``
    itself, not just the harness, is what catches the chain the grounding pass found:
    ``pkb.server.app -> pkb.agents.runtime -> langgraph``. That chain imports no harness module in
    ``pkb.server``'s own source, so a source-level grep would call it clean.
    """
    result = _run_banned(
        _import_every((SERVER_DIR,), ()) + "assert leaked() == [], leaked()\nprint('OK')\n",
        (*HARNESS, "pkb.agents"),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("OK")


# --------------------------------------------------------------------------------------
# SV-1 — the Protocol is expressible without the harness
# --------------------------------------------------------------------------------------

_ALLOWED_ANNOTATION_MODULES = frozenset(
    {"pkb.contracts", "pkb.service", "pkb.core", "collections.abc", "typing", "datetime"}
)
"""Where a ``PkbService`` annotation may come from: the seam, Layer 3's own dataclasses, Layer 1,
and the standard library's container and time types."""


def _unresolvable_annotations(source: str, protocol: str = "PkbService") -> set[str]:
    """Names in the Protocol's signatures that do not resolve to something SV-1 permits.

    "Resolves" is meant literally: a name imported from ``pkb.contracts`` is looked up *in*
    ``pkb.contracts``, so a signature naming a type that has quietly moved out of the seam fails
    here rather than at the first import from a transport.
    """
    tree = ast.parse(source)
    imported = {
        alias: module
        for module, names in _imports(tree)
        for alias in names
        if not alias.startswith("_")
    }
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)} | {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    body = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == protocol
    )

    annotations: list[ast.expr] = []
    for node in ast.walk(body):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            arguments = node.args
            every = [
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
                arguments.vararg,
                arguments.kwarg,
            ]
            annotations.extend(arg.annotation for arg in every if arg and arg.annotation)
            if node.returns is not None:
                annotations.append(node.returns)

    unresolved: set[str] = set()
    for annotation in annotations:
        for node in ast.walk(annotation):
            if isinstance(node, ast.Constant):
                continue  # `None` in `X | None`, and literal strings such as `"interactive"`
            if not isinstance(node, ast.Name | ast.Attribute):
                continue
            name = _dotted(node)
            root = name.split(".")[0]
            if root in defined or hasattr(builtins, root):
                continue
            module = imported.get(root)
            resolves = (
                module is not None
                and module in _ALLOWED_ANNOTATION_MODULES
                and hasattr(importlib.import_module(module), root)
            )
            if not resolves:
                unresolved.add(name)
    return unresolved


def test_the_protocol_names_no_harness_type_in_any_signature_sv1() -> None:
    """A stub is writable **exactly when** the Protocol is expressible without the harness.

    That equivalence is the whole architecture of the test suite: ``tests/server/stub.py`` fakes
    every method, so routes, SSE frames, MCP tools and TUI screens are tested without a runtime, a
    checkpointer, a model or SQLite — and can therefore assert things a live system never could
    deterministically, like a busy thread 409ing in milliseconds while the first event is five
    seconds away. One ``Interrupt`` or ``RunnableConfig`` in one signature and the stub becomes
    unwritable, so the entire suite quietly moves behind an API key.

    The negative control at the bottom is not decoration: a checker that cannot fail is not a check.
    """
    source = (SERVICE_DIR / "__init__.py").read_text(encoding="utf-8")
    assert _unresolvable_annotations(source) == set()
    named = {node.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Name)}
    assert not HARNESS_TYPES & named

    planted = """
from collections.abc import Sequence
from langgraph.types import Command

class PkbService:
    async def resume(self, thread_id: str, decisions: Sequence[Decision]) -> Command: ...
"""
    # Both ways a harness type gets in: imported from the harness, and simply not imported at all.
    assert _unresolvable_annotations(planted) == {"Command", "Decision"}


# --------------------------------------------------------------------------------------
# SV-2 / SV-4 — one module reaches the harness, and only by two names
# --------------------------------------------------------------------------------------


def _harness_touching(
    sources: dict[Path, ast.Module],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """``({file: names imported from pkb.agents}, [files naming a harness module])``."""
    through_agents: dict[str, tuple[str, ...]] = {}
    named_directly: dict[str, tuple[str, ...]] = {}
    for path, tree in sources.items():
        for module, names in _imports(tree):
            if module.split(".")[0] in HARNESS:
                named_directly[path.name] = (module,)
            elif module == "pkb.agents" or module.startswith("pkb.agents."):
                through_agents[path.name] = names
    return through_agents, named_directly


def test_one_service_module_touches_the_harness_and_only_by_two_names_sv2() -> None:
    """Naming exactly one module keeps I2 structural rather than exempting a whole package.

    Something must call ``PkbRuntime.open``, so ``pkb.service`` cannot get the strict contract
    ``pkb.server`` gets — its lint-imports contract is direct-only. The thing that stops that
    exemption from spreading is this: the harness is reachable from ``pkb/service/runtime.py`` and
    nowhere else, through the two names ``pkb.agents`` exports and no harness module by name. A
    later ``pkb/service/anything.py`` cannot inherit the exemption silently, which is the failure
    ``allow_indirect_imports`` on a whole package invites.
    """
    through_agents, named_directly = _harness_touching(_sources(SERVICE_DIR))
    assert named_directly == {}, named_directly
    assert set(through_agents) == {"runtime.py"}, through_agents
    assert set(through_agents["runtime.py"]) <= {"PkbRuntime", "RuntimeConfig"}, through_agents

    # Both violations the rule is drawn against: a second module reaching the harness, and a module
    # reaching past `pkb.agents`' two exported names into the harness itself.
    planted_touch, planted_named = _harness_touching(
        _planted(
            "from pkb.agents.expert import build_expert\nfrom langgraph.types import Command\n"
        )
    )
    assert planted_touch == {"planted.py": ("build_expert",)}
    assert not set(planted_touch["planted.py"]) <= {"PkbRuntime", "RuntimeConfig"}
    assert planted_named == {"planted.py": ("langgraph.types",)}


def _protocol_members(protocol: type) -> set[str]:
    """Every attribute a Protocol requires — its methods and its annotated attributes."""
    annotated: dict[str, object] = getattr(protocol, "__annotations__", {})
    return {name for name in vars(protocol) if not name.startswith("_")} | set(annotated)


@pytest.mark.superseded
def test_the_service_depends_on_a_structural_runtime_not_a_concrete_one_sv4() -> None:
    """The injected dependency is a Protocol written out here, never ``PkbRuntime`` imported.

    Written out rather than imported is the property SV-30's subprocess rests on: annotate the
    parameter with the real class and the module has a module-scope harness import, and the class
    can no longer be constructed on a machine without the harness. Structural also means Layer 2 can
    add a method without Layer 3 seeing it, and Layer 3 states exactly the nine calls it makes.

    Superseded (Task 6 rebuilds this): the pinned member set includes ``resume`` and
    ``pending_approval`` — the interrupt-resume surface Task 6 removes from the ``Runtime`` protocol
    entirely. The structural-Protocol principle survives; the exact nine-member set does not.
    """
    parameter = inspect.signature(RuntimeService.__init__).parameters["runtime"]
    assert parameter.annotation is Runtime or parameter.annotation == "Runtime"
    assert getattr(Runtime, "_is_protocol", False), "Runtime must be a Protocol, not a base class"
    assert Runtime.__module__ == "pkb.service.runtime"

    # Every call Layer 3 makes on the runtime, stated in one place — and the set the fake in the
    # banned subprocess has to answer.
    assert _protocol_members(Runtime) == {
        "db_path",
        "list_agents",
        "run",
        "resume",
        "cancel",
        "pending_approval",
        "history",
        "delete_thread",
        "request_scan",
        "regenerate",
    }


# --------------------------------------------------------------------------------------
# SV-6 / SV-10 — what the signatures may and may not take
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_runs_are_addressed_by_thread_never_by_agent_sv6() -> None:
    """Cross-channel resume is a one-field handoff, and only because of this.

    Telegram continues what the TUI started with nothing but the id from ``list_threads``: no agent,
    no channel state, no second lookup the phone cannot do. Put ``agent_id`` in these signatures and
    every client has to carry a pair, keep it consistent, and get it right — and a client that
    guesses wrong runs the Librarian's graph on an expert's checkpoint, which D-6 measured reading
    the other conversation's messages verbatim with no error anywhere.

    Superseded (Task 3/5/6 rebuild this): the second loop's method list mixes ``start_run`` (whose
    addressed-by-thread-not-by-agent principle survives against ``session_id``) with ``resume``
    (dies with the gate), ``create_thread``/``get_thread`` (thread CRUD, dies) and ``delete_thread``
    (no successor — "nothing deletes a session"). Marked whole; the surviving principle needs an
    analogous assertion once sessions land.
    """
    for owner in (PkbService, RuntimeService):
        for method in ("start_run", "resume"):
            parameters = inspect.signature(getattr(owner, method)).parameters
            assert "agent_id" not in parameters, f"{owner.__name__}.{method}"
            assert list(parameters)[:2] == ["self", "thread_id"], f"{owner.__name__}.{method}"

    # The implementation is the Protocol, argument for argument: a client written against one and
    # served by the other is what the seam promises.
    for method in ("start_run", "resume", "create_thread", "get_thread", "delete_thread"):
        assert inspect.signature(getattr(RuntimeService, method)) == inspect.signature(
            getattr(PkbService, method)
        ), method


def _minting_sites(sources: dict[Path, ast.Module]) -> list[str]:
    """``file:function`` for every place a uuid is generated."""
    sites: list[str] = []
    for path, tree in sources.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if any(
                _called_name(call.func).startswith("uuid")
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
            ):
                sites.append(f"{path.name}:{node.name}")
    return sites


@pytest.mark.superseded
def test_create_thread_takes_no_id_parameter_sv10() -> None:
    """**Layer 3 mints every user thread id, and only Layer 3.**

    Layer 2 explicitly refuses to invent one (RT-36), so if the transport does not mint it nobody
    does — and the tempting alternatives are all wrong in the same way. A Telegram chat id, an MCP
    argument or a slug of the title is *stable*, which means two conversations reuse it, and the
    checkpointer keys on ``thread_id`` alone: they silently merge into one checkpoint with no error
    anywhere (SV-11, D-6). ``mint_thread_id`` taking no arguments is that stated mechanically —
    there is nothing a caller could pass in for an id to be derived from.

    Superseded (Task 3 rebuilds this): ``create_thread``/``mint_thread_id`` are replaced by
    ``SessionStore.create``/a session-id minter; the minting-sites assertion also pins
    ``threads.py:mint_thread_id`` and ``threads.py:mint_run_id`` by file, both moving. The
    no-caller-supplied-id principle survives and Task 3 owns re-asserting it.
    """
    for owner in (PkbService, RuntimeService):
        parameters = set(inspect.signature(owner.create_thread).parameters)
        assert not parameters & {"thread_id", "id", "chat_id", "thread"}, owner.__name__

    assert inspect.signature(mint_thread_id).parameters == {}
    minted = {mint_thread_id() for _ in range(64)}
    assert len(minted) == 64
    assert all(uuid.UUID(value).version == 4 for value in minted)

    # And nowhere else in Layer 3 makes one: an id born in a route or an MCP tool is an id nobody
    # asserted the namespace invariants for (SV-9). Two minters are allowed and both live in
    # `threads.py` — `mint_run_id` is RO-11's, minted before a run starts so `run.started` can carry
    # it and the supervisor can key on it; it is not a thread id and never reaches the table.
    assert sorted(_minting_sites(_sources(SERVICE_DIR, SERVER_DIR))) == [
        "threads.py:mint_run_id",
        "threads.py:mint_thread_id",
    ]
    assert _minting_sites(
        _planted("async def create(chat_id):\n    return await store.create(str(uuid.uuid4()))\n")
    ) == ["planted.py:create"]


# --------------------------------------------------------------------------------------
# SV-18 / SV-22 / SV-25 — the three things Layer 3 must never do
# --------------------------------------------------------------------------------------


_CONFIG_KEYS = frozenset({"configurable", "checkpoint_ns", "recursion_limit"})


def _config_offenders(sources: dict[Path, ast.Module]) -> list[str]:
    """Every place a harness config key is used as a key, a keyword or a name — prose excluded."""
    offenders: list[str] = []
    for path, tree in sources.items():
        prose = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) not in prose and node.value in _CONFIG_KEYS:
                    offenders.append(f"{path.name}: literal {node.value!r}")
            elif isinstance(node, ast.keyword) and node.arg in _CONFIG_KEYS:
                offenders.append(f"{path.name}: keyword {node.arg}=")
            elif isinstance(node, ast.Name | ast.Attribute):
                name = node.attr if isinstance(node, ast.Attribute) else node.id
                if name in _CONFIG_KEYS:
                    offenders.append(f"{path.name}: {name}")
    return offenders


def test_layer3_hands_the_runtime_nothing_but_a_thread_id_sv18() -> None:
    """No ``configurable`` dict, no ``checkpoint_ns``, no ``recursion_limit`` — anywhere.

    Each is a different failure. ``checkpoint_ns`` makes ``aget_state`` raise outright, and it is
    the exact trap D-6 records: it *looks* like a second dimension for keying a thread and is not
    one. ``recursion_limit`` is already set by ``create_deep_agent``'s ``.with_config``, so setting
    it here silently overrides Layer 2's own choice from a transport. And a ``configurable`` dict
    assembled up here is a second implementation of ``runtime.thread_config``, which is the class
    of duplication the layering exists to prevent — the two drift, and the one that loses is
    whichever the human is not looking at.

    Docstrings are excluded on purpose: several of these modules name ``checkpoint_ns`` precisely to
    explain why they never set one.
    """
    assert _config_offenders(_sources(SERVICE_DIR, SERVER_DIR)) == []
    assert set(
        _config_offenders(
            _planted(
                '"""A docstring that says checkpoint_ns, which must not count."""\n'
                'config = {"configurable": {"thread_id": t, "checkpoint_ns": ""}}\n'
                "graph.ainvoke(state, recursion_limit=50)\n"
            )
        )
    ) == {
        "planted.py: literal 'configurable'",
        "planted.py: literal 'checkpoint_ns'",
        "planted.py: keyword recursion_limit=",
    }


# `touch` is deliberately absent: `ThreadStore.touch` bumps a SQL column, and a name-based check
# cannot tell the two apart. Everything that *creates* a file is here, which is the path a stray
# write would actually take.
_FILE_WRITERS = frozenset(
    {"write_text", "write_bytes", "writelines", "mkdir", "unlink", "rename", "symlink_to"}
)
_LAYER1_WRITERS = frozenset({"flush", "regenerate_all", "scaffold_topic", "scaffold_subtopic"})


def _write_offenders(sources: dict[Path, ast.Module]) -> list[str]:
    """Every call that could put bytes on disk, and every import that exists to do so."""
    offenders: list[str] = []
    for path, tree in sources.items():
        for module, names in _imports(tree):
            if module.split(".")[0] in {"shutil", "os", "tempfile"}:
                offenders.append(f"{path.name}: imports {module}")
            if module.startswith("pkb.core") and set(names) & _LAYER1_WRITERS:
                offenders.append(f"{path.name}: imports {sorted(set(names) & _LAYER1_WRITERS)}")
        for call in _calls(tree):
            name = _called_name(call.func)
            if name in _FILE_WRITERS | _LAYER1_WRITERS:
                offenders.append(f"{path.name}: {name}()")
            elif name == "open":
                modes = [
                    argument.value
                    for argument in [*call.args[1:], *(keyword.value for keyword in call.keywords)]
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                ]
                if any(set(mode) & set("wxa+") for mode in modes):
                    offenders.append(f"{path.name}: open(..., {modes!r})")
    return offenders


def test_no_layer3_module_writes_under_the_kb_root_sv22() -> None:
    """I3 one layer up: **only Layer 1 writes, and only where an agent's tool path led it.**

    A write from a route bypasses everything that makes a write safe — the schema validator, the
    deny list, the gate table, the write lock and the flush that keeps ``index.md`` and ``tags.md``
    true. And there is no undo (D6): a transport that renamed a file would be a bug whose blast
    radius is the human's own notes. The rule is drawn at "no file-creating call at all" rather than
    at "no call under ``kb_root``" because the second needs a runtime check nobody can perform on a
    path assembled at runtime — the first is checkable here, today, on every module at once.

    ``runtime.regenerate()`` is the one sanctioned Layer 1 call and it is the runtime's, so the
    forbidden names are Layer 1's *own* entry points, which Layer 3 must never call directly.
    """
    assert _write_offenders(_sources(SERVICE_DIR, SERVER_DIR)) == []
    assert set(
        _write_offenders(
            _planted(
                "from pkb.core import flush\n"
                "def save(kb_root, note):\n"
                "    (kb_root / 'Cooking/notes/x.md').write_text(note)\n"
                "    with open(kb_root / 'tags.md', 'w') as handle:\n"
                "        handle.writelines([note])\n"
                "    flush(kb_root)\n"
            )
        )
    ) == {
        "planted.py: imports ['flush']",
        "planted.py: write_text()",
        "planted.py: open(..., ['w'])",
        "planted.py: writelines()",
        "planted.py: flush()",
    }


_MODEL_CLIENTS = frozenset(
    {
        "init_chat_model",
        "ChatOllama",
        "ChatAnthropic",
        "ChatOpenAI",
        "FallbackChatModel",
        "BaseChatModel",
        "resolve_model",
    }
)


def _model_offenders(sources: dict[Path, ast.Module]) -> list[str]:
    """Every import or construction of a chat model."""
    offenders: list[str] = []
    for path, tree in sources.items():
        for module, names in _imports(tree):
            if module.startswith("pkb.agents.models") or set(names) & _MODEL_CLIENTS:
                offenders.append(f"{path.name}: imports {module}")
        for call in _calls(tree):
            if _called_name(call.func) in _MODEL_CLIENTS:
                offenders.append(f"{path.name}: {_called_name(call.func)}()")
    return offenders


def test_layer3_constructs_no_model_client_sv25() -> None:
    """The one model call Layer 3 makes is the title, and it goes through the runtime.

    Not "it happens to today": if ``pkb.server`` or ``pkb.service`` built a client of its own, the
    model would become a *transport* concern — chosen per route, per channel, per MCP tool — and
    RG-21 puts that choice in the registry for a reason. It would also put a second, unmonitored
    path around ``FallbackChatModel``, so a quota outage would fail over on the agent path and hard-
    fail here, with none of the warning the human needs to learn their quota ran out.

    The harness-banned subprocess above already proves it from the other direction: every one of
    these clients needs ``langchain``, and every Layer 3 module imports without it.
    """
    assert _model_offenders(_sources(SERVICE_DIR, SERVER_DIR)) == []
    assert set(
        _model_offenders(
            _planted(
                "from langchain.chat_models import init_chat_model\n"
                "def summarize(text):\n"
                "    return init_chat_model('ollama:gemma4:31b').invoke(text)\n"
            )
        )
    ) == {"planted.py: imports langchain.chat_models", "planted.py: init_chat_model()"}


# --------------------------------------------------------------------------------------
# A defect found while driving the real service through the seam — RO-11
# --------------------------------------------------------------------------------------


class _SlowRuntime:
    """A runtime whose first event arrives after the admission deadline — the normal case.

    Not a pathological fake: AP-10's own note measures an admitted run's first event at **2.06 s**
    against a 250 ms deadline, so every run against a real model takes this path.
    """

    db_path = Path("never-opened.sqlite")

    def list_agents(self) -> Sequence[AgentDescriptor]:
        return _CATALOG

    def run(
        self,
        agent_id: str,
        thread_id: str,
        message: str,
        *,
        approval_mode: ApprovalMode = "interactive",
        run_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        async def stream() -> AsyncIterator[AgentEvent]:
            await asyncio.sleep(0.05)
            yield RunEnd(run_id=run_id or f"runtime-{agent_id}", final_text=message)

        return stream()

    def resume(
        self,
        agent_id: str,
        thread_id: str,
        decisions: Sequence[Decision],
        *,
        interrupt_id: str | None = None,
        run_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        raise NotImplementedError

    async def cancel(self, run_id: str) -> None: ...

    async def pending_approval(self, agent_id: str, thread_id: str) -> ApprovalRequest | None:
        return None

    async def history(self, agent_id: str, thread_id: str) -> Sequence[MessageView]:
        return []

    async def delete_thread(self, thread_id: str) -> None: ...

    async def request_scan(self, request: ScanRequest) -> ScanResult:
        raise NotImplementedError

    async def regenerate(self) -> FlushReport:
        raise NotImplementedError


_CATALOG: tuple[AgentDescriptor, ...] = (
    AgentDescriptor(
        agent_id="topic/cooking",
        title="Cooking",
        description="Food, heat and time.",
        has_custom_expert=False,
        model_id="ollama:deepseek-v4-flash:cloud",
    ),
    AgentDescriptor(
        agent_id="topic/running",
        title="Running",
        description="Legs, mostly.",
        has_custom_expert=False,
        model_id="ollama:deepseek-v4-flash:cloud",
    ),
)


@pytest.mark.asyncio
async def test_a_run_carries_its_id_before_its_first_event_ro11(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RO-11: ``run_id`` is server-minted and known **before** the first token, so cancel is never
    a race — and SS-8 puts it in frame 0 of every stream.

    It is also the key the supervisor files the run under. With ``run_id=""`` for every run whose
    first event outruns the admission deadline, three things break at once and none of them raises:
    the second such run's hub **replaces** the first's in ``RunSupervisor._hubs``, so ``attach``
    (RO-17) hands a reconnecting client somebody else's stream; the first run's ``finally`` pops
    ``_tasks[""]`` — the other run's task — so ``/health``'s ``active_runs`` and ``aclose``'s
    shutdown sweep both lose it; and ``cancel(run_id)`` from any client cancels whichever run is
    currently squatting on ``""``. The remedy the Protocol already anticipates is the ``run_id``
    parameter on both ``start_run`` and ``runtime.run``: mint it up front and hand it down.
    """
    # The deadline, not the model, scaled down: 10 ms of waiting stands in for 250 ms, so the fake's
    # 50 ms first event models the 2.06 s one without the suite paying for it.
    monkeypatch.setattr(service_runtime, "ADMISSION_DEADLINE", 0.01)

    connection = await aiosqlite.connect(":memory:", isolation_level=None)
    service = RuntimeService(_SlowRuntime(), connection)
    try:
        await service.setup()
        cooking = await service.create_thread("topic/cooking")
        running = await service.create_thread("topic/running")

        first = await service.start_run(cooking.thread_id, "how do I sear")
        second = await service.start_run(running.thread_id, "how do I taper")
        handles = (first.handle, second.handle)

        for subscription in (first, second):
            assert [event async for event in subscription.events]
        await asyncio.sleep(0.05)  # let the titling tasks finish before the connection goes
    finally:
        await connection.close()

    assert all(handle.run_id for handle in handles), handles
    assert handles[0].run_id != handles[1].run_id, handles
