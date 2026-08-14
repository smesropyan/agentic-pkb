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
argument. Everything the checkpointer cannot answer (D-19) is answered by the `sessions` table.

Session-shaped (Task 6, DESIGN.md §2): a session is created directly on its agent and addressed by
its own id thereafter — no thread CRUD, and nothing deletes one (§2.7). No `resume` and no
`pending_approval` either: no graph composes a gate any longer, so nothing ever parks (S-38, S-39).
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pkb.contracts import (
    AgentDescriptor,
    AgentEvent,
    MessageView,
    UnknownAgentError,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    agent_id   TEXT NOT NULL,
    objective  TEXT,
    name       TEXT NOT NULL,
    operator   TEXT NOT NULL,
    state      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    closed_at  TEXT,
    ended_at   TEXT
)
"""


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    agent_id: str
    objective: str | None
    name: str
    operator: str
    state: str
    created_at: str
    closed_at: str | None
    ended_at: str | None


class Runtime(Protocol):
    """Exactly what Layer 3 needs from `PkbRuntime`. No harness type appears in it."""

    def list_agents(self) -> list[AgentDescriptor]: ...

    def run(self, agent_id: str, thread_id: str, message: str) -> AsyncIterator[AgentEvent]: ...

    async def cancel(self, run_id: str) -> None: ...

    async def history(self, agent_id: str, thread_id: str) -> list[MessageView]: ...


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

    # -- sessions: Layer 3's table, because the checkpointer cannot answer any of this ---

    def create_session(
        self, agent_id: str, *, objective: str | None = None, operator: str = "operator"
    ) -> Session:
        known = {descriptor.agent_id for descriptor in self._runtime.list_agents()}
        if agent_id not in known:
            raise UnknownAgentError(agent_id)
        session = Session(
            session_id=str(uuid.uuid4()),
            agent_id=agent_id,
            objective=objective,
            name=objective or "session",
            operator=operator,
            state="open",
            created_at=datetime.now(UTC).isoformat(),
            closed_at=None,
            ended_at=None,
        )
        self._db.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.session_id,
                session.agent_id,
                session.objective,
                session.name,
                session.operator,
                session.state,
                session.created_at,
                session.closed_at,
                session.ended_at,
            ),
        )
        return session

    def list_sessions(
        self, agent_id: str | None = None, *, state: str | None = None
    ) -> list[Session]:
        clauses, params = [], []
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._db.execute(f"SELECT * FROM sessions{where} ORDER BY created_at", params)
        return [Session(*row) for row in rows]

    def get_session(self, session_id: str) -> Session:
        row = self._db.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise UnknownAgentError(session_id)
        return Session(*row)

    def close_session(self, session_id: str) -> Session:
        """S-20: state -> closed, entering the learning queue (S-25/P4: `state='closed'` IS it)."""
        session = self.get_session(session_id)
        stamp = datetime.now(UTC).isoformat()
        self._db.execute(
            "UPDATE sessions SET state = 'closed', closed_at = ? WHERE session_id = ?",
            (stamp, session_id),
        )
        return self.get_session(session_id)

    def end_session(self, session_id: str) -> Session:
        """S-22/S-24: seals the session — legal only from `closed`."""
        session = self.get_session(session_id)
        if session.state != "closed":
            raise UnknownAgentError(f"{session_id} is {session.state!r}, not closed")
        stamp = datetime.now(UTC).isoformat()
        self._db.execute(
            "UPDATE sessions SET state = 'ended', ended_at = ? WHERE session_id = ?",
            (stamp, session_id),
        )
        return self.get_session(session_id)

    # -- runs ---------------------------------------------------------------------------

    async def stream_run(self, session_id: str, message: str) -> AsyncIterator[AgentEvent]:
        session = self.get_session(session_id)
        async for event in self._runtime.run(session.agent_id, session_id, message):
            yield event

    async def cancel(self, run_id: str) -> None:
        await self._runtime.cancel(run_id)

    async def history(self, session_id: str) -> list[MessageView]:
        session = self.get_session(session_id)
        return await self._runtime.history(session.agent_id, session_id)
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

    async def cancel(self, run_id):
        return None

    async def history(self, agent_id, thread_id):
        return []


async def main():
    service = module.PkbService(FakeRuntime(), Path({db!r}))
    session = service.create_session("librarian", objective="plan the week", operator="tester")
    assert [row.session_id for row in service.list_sessions("librarian")] == [session.session_id]
    assert service.get_session(session.session_id).state == "open"

    events = [event async for event in service.stream_run(session.session_id, "hello")]
    assert isinstance(events[-1], RunEnd)
    assert events[-1].final_text == "echo: hello"

    assert service.list_sessions(state="closed") == []
    closed = service.close_session(session.session_id)
    assert closed.state == "closed"
    assert [row.session_id for row in service.list_sessions(state="closed")] == [session.session_id]

    ended = service.end_session(session.session_id)
    assert ended.state == "ended"
    assert service.list_sessions(state="closed") == []
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


def test_a_stub_service_compiles_and_runs_with_the_harness_banned_i2(tmp_path: Path) -> None:
    """The acceptance test for the seam: a `PkbService` over the runtime plus one SQL table.

    Run in a subprocess whose `sys.meta_path` refuses `deepagents`, `langgraph`, `langchain` and
    `langchain_core` outright. A single missing type in `pkb.contracts` shows up here as an
    ImportError rather than as an architectural argument two layers later.

    Rewritten for Task 6 (assigned by Task 2's review — mirrors `tests/service/test_seam.py`'s sv4
    sibling): ``BANNED_DRIVER`` is now session-shaped — ``create_session``, ``list_sessions``,
    ``get_session``, ``close_session``/``end_session`` — with no thread CRUD, no ``resume``, no
    ``pending_approval`` and no delete, per the sessions model (DESIGN.md §2: a session belongs to
    one agent directly, no gate ever parks one, and nothing deletes a session).
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


@pytest.mark.asyncio
async def test_the_same_stub_drives_a_real_runtime_i2(kb: Path, tmp_path: Path) -> None:
    """Compiling against the seam is not enough; the surface has to be the one that exists.

    Rewritten for Task 6 (assigned by Task 2's review — same reasons as this file's harness-banned
    sibling above): session-shaped, against the real ``PkbRuntime`` — no ``channel=``, no
    ``pending_approval``, no per-agent thread listing and no delete (there is no session
    equivalent — "nothing deletes a session").
    """
    module = load_stub(tmp_path)
    model = scripted(says("filed under Cooking"))

    async with opened(kb, model) as rt:
        service = module.PkbService(rt, tmp_path / "layer3.sqlite")
        try:
            ids = {descriptor.agent_id for descriptor in service.list_agents()}
            assert {"librarian", COOKING} <= ids

            session = service.create_session(COOKING, objective="Steak", operator="tester")
            events = [
                event async for event in service.stream_run(session.session_id, "how do I sear")
            ]

            assert isinstance(events[-1], RunEnd)
            assert events[-1].final_text == "filed under Cooking"
            assert any(isinstance(event, MessageComplete) for event in events)

            replayed = await service.history(session.session_id)
            assert [view.role for view in replayed] == ["human", "assistant"]

            # The question the checkpointer cannot answer, answered by Layer 3's own table (D-19).
            assert [row.session_id for row in service.list_sessions(COOKING)] == [
                session.session_id
            ]
            assert service.list_sessions("librarian") == []

            assert service.list_sessions(state="closed") == []
            closed = service.close_session(session.session_id)
            assert closed.state == "closed"
            assert [row.session_id for row in service.list_sessions(state="closed")] == [
                session.session_id
            ]

            ended = service.end_session(session.session_id)
            assert ended.state == "ended"
            assert service.list_sessions(state="closed") == []
        finally:
            service.close()


def test_the_runtime_is_the_only_name_pkb_agents_exports_5_2() -> None:
    """Anything re-exported from `pkb.agents` drags the harness into every importer (decision B)."""
    import pkb.agents as package

    assert package.__all__ == ["PkbRuntime", "RuntimeConfig"]


def test_the_service_protocols_methods_all_exist_on_the_runtime_5_2(tmp_path: Path) -> None:
    """A rename on either side is a broken seam; here it is a failing assertion instead.

    Rewritten for Task 6: ``required`` is derived from the stub's own ``Runtime`` Protocol, reshaped
    session-shaped alongside ``STUB_SOURCE`` — ``list_agents``, ``run``, ``cancel``, ``history``, with
    no ``resume`` and no ``pending_approval``, the interrupt-resume surface Task 6 removes from
    ``PkbRuntime`` entirely. Every name the stub still declares is checked against the real thing, so
    a rename on either side is still caught here.
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
            session = service.create_session(COOKING)
            events = [event async for event in service.stream_run(session.session_id, "hi")]
            service.close()
            return events

    for event in asyncio.run(go()):
        assert dataclasses.is_dataclass(event)
        json.dumps(dataclasses.asdict(event))
