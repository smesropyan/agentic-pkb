"""The Layer 2 → Layer 3 seam — §5, I2, D-19, D-20, RT-43, RT-49.

This is the acceptance test for the seam, and it is deliberately the harshest thing in the suite: a
stub `PkbService` implementing arch §5's Protocol over `PkbRuntime` plus one SQL table must compile
**and run** with the harness *banned from the interpreter*. If Layer 3 needed one deepagents,
langgraph or langchain name to express itself, that run would fail at import — which is the only
honest way to prove I2 rather than assert it.

The stub is written once, as `STUB_SOURCE`, and exercised twice:

* in a subprocess with an import hook that raises for every harness module, against a fake runtime
  built from `pkb.contracts` alone — this proves the *type surface* is complete;
* in this process, against a **real** `PkbRuntime` over a real knowledge base — this proves the
  surface it binds against actually exists and behaves.

One source, two proofs. A stub that only compiled would prove nothing about the method names, and a
stub that only ran would prove nothing about the imports.

`PkbService` is Layer 2's surface plus a `threads` table because the checkpointer cannot answer
"which agent owns this thread", "what is it called" or "which channel did it arrive on" — `alist`
returns bare `CheckpointTuple`s (D-19). Every one of those questions is answered below by SQL over
Layer 3's own table, which is exactly the division RT-49 draws.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from pkb.contracts import MessageComplete, RunEnd
from tests.agents.conftest import says, scripted
from tests.agents.test_runtime import COOKING, opened

HARNESS = ("deepagents", "langgraph", "langchain", "langchain_core")

# --------------------------------------------------------------------------------------
# The stub. One source; see the module docstring for why it is a string.
# --------------------------------------------------------------------------------------

STUB_SOURCE = '''
"""A stub `PkbService` (architecture §5) over `PkbRuntime` plus one SQL table.

Written the way Layer 3 will be written: it binds against `pkb.contracts` and a structural view of
the runtime, and it imports nothing from `pkb.agents` — the runtime arrives as a constructor
argument. Everything the checkpointer cannot answer (D-19) is answered by the `threads` table.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pkb.contracts import (
    AgentDescriptor,
    AgentEvent,
    ApprovalRequest,
    Decision,
    MessageView,
    UnknownAgentError,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    thread_id      TEXT PRIMARY KEY,
    agent_id       TEXT NOT NULL,
    title          TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    origin_channel TEXT NOT NULL
)
"""


@dataclass(frozen=True, slots=True)
class Thread:
    thread_id: str
    agent_id: str
    title: str
    created_at: str
    origin_channel: str


class Runtime(Protocol):
    """Exactly what Layer 3 needs from `PkbRuntime`. No harness type appears in it."""

    def list_agents(self) -> list[AgentDescriptor]: ...

    def run(self, agent_id: str, thread_id: str, message: str) -> AsyncIterator[AgentEvent]: ...

    def resume(
        self, agent_id: str, thread_id: str, decisions: Sequence[Decision]
    ) -> AsyncIterator[AgentEvent]: ...

    async def cancel(self, run_id: str) -> None: ...

    async def pending_approval(
        self, agent_id: str, thread_id: str
    ) -> ApprovalRequest | None: ...

    async def history(self, agent_id: str, thread_id: str) -> list[MessageView]: ...

    async def delete_thread(self, thread_id: str) -> None: ...


class PkbService:
    def __init__(self, runtime: Runtime, db_path: Path) -> None:
        self._runtime = runtime
        self._db = sqlite3.connect(db_path, isolation_level=None)
        self._db.execute(SCHEMA)

    def close(self) -> None:
        self._db.close()

    # -- catalog ------------------------------------------------------------------------

    def list_agents(self) -> list[AgentDescriptor]:
        return self._runtime.list_agents()

    # -- threads: Layer 3's table, because the checkpointer cannot answer any of this ----

    def create_thread(self, agent_id: str, *, title: str = "", channel: str = "tui") -> Thread:
        known = {descriptor.agent_id for descriptor in self._runtime.list_agents()}
        if agent_id not in known:
            raise UnknownAgentError(agent_id)
        thread = Thread(
            thread_id=str(uuid.uuid4()),
            agent_id=agent_id,
            title=title or "New conversation",
            created_at=datetime.now(UTC).isoformat(),
            origin_channel=channel,
        )
        self._db.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
            (
                thread.thread_id,
                thread.agent_id,
                thread.title,
                thread.created_at,
                thread.origin_channel,
            ),
        )
        return thread

    def list_threads(self, agent_id: str | None = None) -> list[Thread]:
        if agent_id is None:
            rows = self._db.execute("SELECT * FROM threads ORDER BY created_at")
        else:
            rows = self._db.execute(
                "SELECT * FROM threads WHERE agent_id = ? ORDER BY created_at", (agent_id,)
            )
        return [Thread(*row) for row in rows]

    def get_thread(self, thread_id: str) -> Thread:
        row = self._db.execute(
            "SELECT * FROM threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            raise UnknownAgentError(thread_id)
        return Thread(*row)

    # -- runs ---------------------------------------------------------------------------

    async def stream_run(self, thread_id: str, message: str) -> AsyncIterator[AgentEvent]:
        thread = self.get_thread(thread_id)
        async for event in self._runtime.run(thread.agent_id, thread_id, message):
            yield event

    async def resume(
        self, thread_id: str, decisions: Sequence[Decision]
    ) -> AsyncIterator[AgentEvent]:
        thread = self.get_thread(thread_id)
        async for event in self._runtime.resume(thread.agent_id, thread_id, decisions):
            yield event

    async def cancel(self, run_id: str) -> None:
        await self._runtime.cancel(run_id)

    async def pending_approval(self, thread_id: str) -> ApprovalRequest | None:
        thread = self.get_thread(thread_id)
        return await self._runtime.pending_approval(thread.agent_id, thread_id)

    async def history(self, thread_id: str) -> list[MessageView]:
        thread = self.get_thread(thread_id)
        return await self._runtime.history(thread.agent_id, thread_id)

    async def delete_thread(self, thread_id: str) -> None:
        thread = self.get_thread(thread_id)
        await self._runtime.delete_thread(thread.thread_id)
        self._db.execute("DELETE FROM threads WHERE thread_id = ?", (thread.thread_id,))
'''


BANNED_DRIVER = '''
"""Import and drive the stub with every harness module banned from the interpreter."""

import asyncio
import importlib.abc
import importlib.util
import sys
from pathlib import Path

BANNED = {banned!r}


class Ban(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".")[0]
        if root in BANNED:
            raise ImportError(f"the harness is banned in this process: {{fullname}}")
        return None


sys.meta_path.insert(0, Ban())

from pkb.contracts import (  # noqa: E402
    AgentDescriptor,
    MessageComplete,
    RunEnd,
)

spec = importlib.util.spec_from_file_location("pkb_service_stub", {stub!r})
module = importlib.util.module_from_spec(spec)
# Registered before execution: `@dataclass(slots=True)` resolves annotations through
# `sys.modules[cls.__module__]`, and an unregistered module makes that lookup return None.
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class FakeRuntime:
    """A runtime built from `pkb.contracts` alone — no harness anywhere."""

    def list_agents(self):
        return [
            AgentDescriptor(
                agent_id="librarian",
                title="Librarian",
                description="Routes each item to the right Topic Expert.",
                has_custom_expert=False,
                model_id="anthropic:claude-sonnet-5",
            )
        ]

    async def run(self, agent_id, thread_id, message):
        yield MessageComplete(run_id="R1", agent_id=agent_id, text=f"echo: {{message}}")
        yield RunEnd(run_id="R1", final_text=f"echo: {{message}}")

    async def resume(self, agent_id, thread_id, decisions):
        yield RunEnd(run_id="R2", final_text="resumed")

    async def cancel(self, run_id):
        return None

    async def pending_approval(self, agent_id, thread_id):
        return None

    async def history(self, agent_id, thread_id):
        return []

    async def delete_thread(self, thread_id):
        return None


async def main():
    service = module.PkbService(FakeRuntime(), Path({db!r}))
    thread = service.create_thread("librarian", title="First", channel="telegram")
    assert [row.thread_id for row in service.list_threads("librarian")] == [thread.thread_id]
    assert service.get_thread(thread.thread_id).origin_channel == "telegram"

    events = [event async for event in service.stream_run(thread.thread_id, "hello")]
    assert isinstance(events[-1], RunEnd)
    assert events[-1].final_text == "echo: hello"

    assert await service.pending_approval(thread.thread_id) is None
    await service.delete_thread(thread.thread_id)
    assert service.list_threads() == []
    service.close()


asyncio.run(main())
leaked = sorted(name for name in sys.modules if name.split(".")[0] in BANNED)
assert leaked == [], leaked
print("OK")
'''


def write_stub(tmp_path: Path) -> Path:
    stub = tmp_path / "pkb_service_stub.py"
    stub.write_text(STUB_SOURCE, encoding="utf-8")
    return stub


def load_stub(tmp_path: Path) -> Any:
    stub = write_stub(tmp_path)
    spec = importlib.util.spec_from_file_location("pkb_service_stub", stub)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # See the banned driver: `@dataclass(slots=True)` needs the module in `sys.modules`.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------------------
# The seam itself
# --------------------------------------------------------------------------------------


def test_importing_the_seam_loads_no_harness_module_i2() -> None:
    """`pkb.contracts` is a leaf: `pkb.core` and the standard library, and nothing else, ever."""
    probe = (
        "import pkb.contracts, sys;"
        f"leaked = sorted(n for n in sys.modules if n.split('.')[0] in {HARNESS!r});"
        "assert leaked == [], leaked;"
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"


def test_the_seam_would_still_be_a_leaf_if_pkb_agents_were_imported_first_i2() -> None:
    """The invariant is about what `pkb.contracts` *imports*, not about import order."""
    source = Path(__import__("pkb.contracts", fromlist=["__file__"]).__file__).read_text("utf-8")
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert not (roots & set(HARNESS))
    assert "pkb" in roots  # pkb.core only — the re-exported Layer 1 types


def test_the_stub_service_imports_only_the_seam_and_the_standard_library_i2(tmp_path: Path) -> None:
    """If Layer 3 had to name a harness type to express itself, the seam would be incomplete."""
    tree = ast.parse(write_stub(tmp_path).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    assert not any(module.split(".")[0] in HARNESS for module in modules)
    assert {module for module in modules if module.startswith("pkb")} == {"pkb.contracts"}


@pytest.mark.superseded
def test_a_stub_service_compiles_and_runs_with_the_harness_banned_i2(tmp_path: Path) -> None:
    """The acceptance test for the seam: a `PkbService` over the runtime plus one SQL table.

    Run in a subprocess whose `sys.meta_path` refuses `deepagents`, `langgraph`, `langchain` and
    `langchain_core` outright. A single missing type in `pkb.contracts` shows up here as an
    ImportError rather than as an architectural argument two layers later.

    Superseded (Task 6 rebuilds this — assigned by Task 2's review — mirroring `tests/service/test_seam.py`'s sv4 sibling):
    ``BANNED_DRIVER`` is entirely thread-CRUD-and-gate shaped — ``create_thread(...,
    channel="telegram")``, ``list_threads``, ``get_thread(...).origin_channel``,
    ``pending_approval``, ``delete_thread`` — all retired by the sessions model (DESIGN.md §2: a
    session belongs to one agent directly, channels attach rather than stamp an ``origin_channel``,
    no gate to park on, and nothing deletes a session). The I2 principle it proves — the seam
    compiles and runs with the harness genuinely banned — is permanent; ``STUB_SOURCE`` and
    ``BANNED_DRIVER`` need a session-shaped rewrite once ``pkb.service.sessions`` exists.
    """
    stub = write_stub(tmp_path)
    driver = tmp_path / "driver.py"
    driver.write_text(
        BANNED_DRIVER.format(banned=HARNESS, stub=str(stub), db=str(tmp_path / "layer3.sqlite")),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(driver)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("OK")


@pytest.mark.superseded
@pytest.mark.asyncio
async def test_the_same_stub_drives_a_real_runtime_i2(kb: Path, tmp_path: Path) -> None:
    """Compiling against the seam is not enough; the surface has to be the one that exists.

    Superseded (Task 6 rebuilds this — assigned by Task 2's review — same reasons as this file's harness-banned sibling above):
    ``channel="telegram"``, ``pending_approval``, per-agent ``list_threads``, and ``delete_thread``
    (there is no session equivalent — "nothing deletes a session") are all thread-CRUD-and-gate
    shaped and retired by the sessions model. The catalog/run/history assertions that do not name a
    thread by CRUD operation survive in spirit; this whole test is entangled with the ones that do
    not, so marked whole.
    """
    module = load_stub(tmp_path)
    model = scripted(says("filed under Cooking"))

    async with opened(kb, model) as rt:
        service = module.PkbService(rt, tmp_path / "layer3.sqlite")
        try:
            ids = {descriptor.agent_id for descriptor in service.list_agents()}
            assert {"librarian", COOKING} <= ids

            thread = service.create_thread(COOKING, title="Steak", channel="telegram")
            events = [
                event async for event in service.stream_run(thread.thread_id, "how do I sear")
            ]

            assert isinstance(events[-1], RunEnd)
            assert events[-1].final_text == "filed under Cooking"
            assert any(isinstance(event, MessageComplete) for event in events)

            replayed = await service.history(thread.thread_id)
            assert [view.role for view in replayed] == ["human", "assistant"]
            assert await service.pending_approval(thread.thread_id) is None

            # The question the checkpointer cannot answer, answered by Layer 3's own table (D-19).
            assert [row.title for row in service.list_threads(COOKING)] == ["Steak"]
            assert service.list_threads("librarian") == []

            await service.delete_thread(thread.thread_id)
            assert service.list_threads() == []
            assert await rt.history(COOKING, thread.thread_id) == []
        finally:
            service.close()


def test_the_runtime_is_the_only_name_pkb_agents_exports_5_2() -> None:
    """Anything re-exported from `pkb.agents` drags the harness into every importer (decision B)."""
    import pkb.agents as package

    assert package.__all__ == ["PkbRuntime", "RuntimeConfig"]


@pytest.mark.superseded
def test_the_service_protocols_methods_all_exist_on_the_runtime_5_2(tmp_path: Path) -> None:
    """A rename on either side is a broken seam; here it is a failing assertion instead.

    Superseded (Task 6 rebuilds this): ``required`` is derived from the stub's own ``Runtime``
    Protocol, which names ``resume`` and ``pending_approval`` — the interrupt-resume surface Task 6
    removes from ``PkbRuntime`` entirely ("the runtime exposes no interrupt-resume surface" is one of
    Task 6's own failing tests). Once those methods are gone, ``hasattr(PkbRuntime, "resume")`` is
    false and this assertion fails by design. The seam-methods-exist-on-the-runtime principle
    survives; whoever rebuilds the stub for sessions (Task 3) inherits re-asserting it over the
    surviving member set.
    """
    from pkb.agents.runtime import PkbRuntime

    module = load_stub(tmp_path)
    required = [
        name
        for name in vars(module.Runtime)
        if not name.startswith("_") and callable(vars(module.Runtime)[name])
    ]
    assert required
    for name in required:
        assert hasattr(PkbRuntime, name), name


def test_every_event_survives_the_json_boundary_rt43(kb: Path, tmp_path: Path) -> None:
    """Layer 3 encodes these; a harness object hiding in one would only surface at runtime."""
    import dataclasses
    import json

    module = load_stub(tmp_path)
    model = scripted(says("done"))

    async def go() -> list[Any]:
        async with opened(kb, model) as rt:
            service = module.PkbService(rt, tmp_path / "layer3.sqlite")
            thread = service.create_thread(COOKING)
            events = [event async for event in service.stream_run(thread.thread_id, "hi")]
            service.close()
            return events

    for event in asyncio.run(go()):
        assert dataclasses.is_dataclass(event)
        json.dumps(dataclasses.asdict(event))
