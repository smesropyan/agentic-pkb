"""SQLite concurrency and the open order — AP-4, ST-2, ST-3, ST-4, ST-8.

The one Layer 3 test file that legitimately touches the harness. Everything asserted here is a
property of a **real** file that a real ``PkbRuntime`` opened: the journal mode the checkpointer's
``setup()`` leaves behind, what happens to Layer 3's own writes while a graph is streaming into the
same file, and what survives a restart. A stub cannot answer any of those questions — SQLite's
locking is the thing under test, and a fake connection would only assert the fake.

No key, no network, no model call that leaves the process: the runtime runs on a
``ScriptedChatModel`` and the only I/O is ``tmp_path`` and its SQLite file.

**Repointed from ``ThreadStore`` to ``SessionStore`` at Task 10** (``pkb.service.threads`` is deleted
whole). Every property under test here is a property of the shared connection discipline
(``open_connection``, ``BUSY_TIMEOUT_MS`` — moved into ``pkb.service.runtime``, the one remaining
opener) rather than of either table's own columns, so the vehicle changes and the assertions do not:
``SessionStore.create`` replaces ``ThreadStore.register`` as "the write that lands a row," and it
mints its own id per call (``mint_session_id``) rather than taking a caller-supplied one — which
removes a hazard the old tests guarded against (``register``'s ``INSERT OR IGNORE`` could silently
swallow an id collision) rather than reintroducing an equivalent one, since a genuine collision here
raises. The ST-14 section (``ProposalStore`` outlives the process that recorded it) is not carried
over: both of its tests were already marked superseded citing Task 6, which deleted
``pkb.service.proposals`` outright, and Task 10 deletes what Task 6 already retired rather than
adapting it to a table that no longer exists either.
"""

from __future__ import annotations

import ast
import asyncio
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from pkb.agents import PkbRuntime, RuntimeConfig
from pkb.contracts import RunEnd
from pkb.core.scaffold import scaffold_topic
from pkb.service.runtime import BUSY_TIMEOUT_MS, open_connection
from pkb.service.sessions import MIGRATIONS_TABLE, SessionStore
from tests.agents.conftest import TODAY, ScriptedChatModel, says, scripted

COOKING = "topic/cooking"

SERVICE_PACKAGE = Path(__file__).resolve().parents[2] / "src" / "pkb" / "service"

INSERT_ROW = """
INSERT INTO sessions
    (session_id, agent_id, objective, name, operator, state, created_at, updated_at, closed_at,
     ended_at)
VALUES (?, ?, NULL, ?, 'test', 'open', '2026-08-08T00:00:00Z', '2026-08-08T00:00:00Z', NULL, NULL)
"""
"""A row shaped like :data:`~pkb.service.sessions.SessionStore.create`'s own insert, but driven
directly for the tests below that need to control *when* a statement lands rather than what
:class:`SessionStore` computes for them — ``name`` is passed explicitly (it is ``UNIQUE NOT NULL``)
rather than slugged, since these tests already mint distinct, readable ids of their own."""


# --------------------------------------------------------------------------------------
# Harness — a real knowledge base, a real runtime, a scripted model
# --------------------------------------------------------------------------------------


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    """A one-topic knowledge base, scaffolded through Layer 1 so it cannot drift from the real one."""
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


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """The runtime's SQLite file. It does not exist yet — several tests care about that."""
    return tmp_path / "pkb.sqlite"


def config(model: ScriptedChatModel | None = None) -> RuntimeConfig:
    """Production configuration with the model swapped for a scripted one and no failover.

    ``fallback_model=None`` because :func:`pkb.agents.models.with_fallback` would otherwise build a
    second, real model client for a suite that must not touch a provider (SK-18).
    """
    return RuntimeConfig(default_model=model or scripted(says("ok")), fallback_model=None)


@asynccontextmanager
async def opened(kb: Path, db: Path, model: ScriptedChatModel | None = None) -> AsyncIterator[Any]:
    async with PkbRuntime.open(kb, db, config=config(model)) as runtime:
        yield runtime


@asynccontextmanager
async def store(runtime: Any) -> AsyncIterator[tuple[aiosqlite.Connection, SessionStore]]:
    """Layer 3's own connection over a runtime that is already open, with the table created."""
    connection = await open_connection(runtime.db_path)
    try:
        sessions = SessionStore(connection)
        await sessions.setup()
        yield connection, sessions
    finally:
        await connection.close()


async def journal_mode(connection: aiosqlite.Connection) -> str:
    cursor = await connection.execute("PRAGMA journal_mode")
    row = await cursor.fetchone()
    assert row is not None
    return str(row[0]).lower()


async def scalar(connection: aiosqlite.Connection, sql: str, *args: Any) -> Any:
    cursor = await connection.execute(sql, args)
    row = await cursor.fetchone()
    assert row is not None
    return row[0]


async def session_ids(connection: aiosqlite.Connection) -> set[str]:
    cursor = await connection.execute("SELECT session_id FROM sessions")
    return {str(row[0]) for row in await cursor.fetchall()}


def modules() -> Iterator[tuple[Path, ast.Module, str]]:
    """Every module of ``pkb.service``, parsed. The AST rules below read these."""
    for path in sorted(SERVICE_PACKAGE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        yield path, ast.parse(source), source


def executed_sql(tree: ast.Module, source: str) -> Iterator[str]:
    """Every SQL string that reaches ``execute``/``executescript``/``executemany``, constants resolved.

    Deliberately the *call arguments* rather than every literal in the file: ``sessions.py``'s own
    module docstring explains at length what ``BEGIN IMMEDIATE`` would do to a concurrent run, so a
    grep flags the explanation and still misses an f-string that issued one.
    """
    constants: dict[str, str] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            targets: list[ast.expr] = list(statement.targets)
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
        else:
            continue
        if statement.value is None:
            continue
        text = ast.get_source_segment(source, statement.value) or ""
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = text
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"execute", "executescript", "executemany"}:
            continue
        segment = ast.get_source_segment(source, node.args[0]) or ""
        yield constants.get(segment.strip(), segment)


# --------------------------------------------------------------------------------------
# AP-4 · the open order
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_connection_opened_before_the_runtime_gets_a_rollback_journal_ap4(
    kb: Path, db: Path
) -> None:
    """The measurement the whole rule rests on, restated as a test.

    ``AsyncSqliteSaver.from_conn_string`` is a bare ``aiosqlite.connect`` and ``PRAGMA
    journal_mode=WAL`` is set in ``setup()``, which only ``PkbRuntime.open`` calls. So the *same two
    lines of code* in the wrong order give Layer 3 a rollback-journal file, where a reader blocks a
    writer — and nothing about it looks wrong until the daemon is under load and a ``/sessions`` read
    stalls a run's flush. Ordering is invisible in a diff; this is what makes it visible.
    """
    early = await aiosqlite.connect(str(db))
    try:
        assert await journal_mode(early) == "delete"
    finally:
        await early.close()

    async with opened(kb, db) as runtime:
        late = await aiosqlite.connect(str(runtime.db_path))
        try:
            assert await journal_mode(late) == "wal"
        finally:
            await late.close()


@pytest.mark.asyncio
async def test_open_connection_refuses_a_file_that_is_not_in_wal_and_names_the_rule_ap4(
    kb: Path, db: Path
) -> None:
    """A wrong open order must fail at startup, loudly, rather than run slowly forever.

    The failure AP-4 guards is silent: everything works in development, where one request runs at a
    time, and degrades only when a read and a write overlap. So ``open_connection`` asserts the
    journal mode instead of trusting the caller, and the message has to carry the rule id and the
    remedy — the person who sees it is looking at a stack trace, not at this spec.
    """
    with pytest.raises(RuntimeError) as caught:
        await open_connection(db)
    message = str(caught.value)
    assert "AP-4" in message
    assert "delete" in message
    assert "PkbRuntime.open" in message

    async with opened(kb, db) as runtime, store(runtime) as (connection, _):
        assert await journal_mode(connection) == "wal"


@pytest.mark.asyncio
async def test_the_busy_timeout_is_sqlites_default_and_hammering_never_locks_st4(
    kb: Path, db: Path
) -> None:
    """Four writers, no waiting, no lost write — because nobody lowered the timeout.

    5000 ms is SQLite's default and Layer 3 restates it rather than tuning it: at 1 ms the same
    hammering measured ``ok=528 locked=72``. Every one of those 72 is a session row that never landed
    — a conversation missing from the list, or a queue entry no channel can see. The retry people
    reach for instead is SQLite's own, and it is already on.
    """
    async with opened(kb, db) as runtime, store(runtime) as (first, sessions):
        assert await scalar(first, "PRAGMA busy_timeout") == BUSY_TIMEOUT_MS

        others = [await open_connection(runtime.db_path) for _ in range(3)]
        stores = [sessions, *(SessionStore(other) for other in others)]
        try:
            results = await asyncio.gather(
                *(
                    writer.create(COOKING, f"objective w{index}-{n}", "test")
                    for index, writer in enumerate(stores)
                    for n in range(50)
                ),
                return_exceptions=True,
            )
        finally:
            for other in others:
                await other.close()

        assert [result for result in results if isinstance(result, BaseException)] == []
        assert await scalar(first, "SELECT COUNT(*) FROM sessions") == 200


# --------------------------------------------------------------------------------------
# ST-2 · one aiosqlite connection, no synchronisation of our own
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_hundred_concurrent_upserts_survive_a_live_run_st2(kb: Path, db: Path) -> None:
    """The daemon writes its own rows *while* a graph streams into the same file. It must not care.

    Layer 3's rows are written from event callbacks and from ordinary session creation — so its
    writes are concurrent with the checkpointer's by construction, on the same file, from the same
    loop. ``aiosqlite`` puts one worker thread behind a ``SimpleQueue`` per connection, which is why
    unsynchronised coroutines are safe here without a lock of our own; ``sqlite3`` on
    ``asyncio.to_thread`` would need ``check_same_thread=False`` *plus* that lock, which is
    re-implementing aiosqlite. Zero failures and 300 rows is the property: a swallowed ``database is
    locked`` here is a session that vanishes from every channel's list.

    Each concurrent write is its own ``SessionStore.create`` call, which mints its own id and raises
    on a genuine failure rather than ``ThreadStore.register``'s old ``INSERT OR IGNORE`` — so a
    row-count check is already the stronger assertion the old set-membership check existed to
    approximate for an idempotent write.
    """
    async with (
        opened(kb, db, scripted(says("filed"))) as runtime,
        store(runtime) as (conn, sessions),
    ):
        streaming = asyncio.Event()
        hammered = asyncio.Event()

        async def live_run() -> list[Any]:
            events: list[Any] = []
            async for event in runtime.run(COOKING, "T-live", "say something"):
                events.append(event)
                streaming.set()
                if not hammered.is_set():
                    # Hold the stream open across the hammering. The run is mid-flight — its
                    # checkpoint writes are not finished — while Layer 3 writes to the same file.
                    await hammered.wait()
            return events

        async def hammer() -> list[Any]:
            await asyncio.wait_for(streaming.wait(), timeout=5)
            results = await asyncio.gather(
                *(sessions.create(COOKING, f"objective {n:03d}", "test") for n in range(300)),
                return_exceptions=True,
            )
            hammered.set()
            return results

        run_events, results = await asyncio.gather(live_run(), hammer())

        assert [result for result in results if isinstance(result, BaseException)] == []
        assert await scalar(conn, "SELECT COUNT(*) FROM sessions") == 300
        assert isinstance(run_events[-1], RunEnd)
        # Both writers really were in the same file: the checkpointer's rows for this thread are
        # readable from Layer 3's own connection. ("T-live" is the checkpointer's own thread id,
        # LangGraph's key — unrelated to and untouched by Layer 3's own `sessions` table.)
        checkpoints = await scalar(
            conn, "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", "T-live"
        )
        assert checkpoints > 0


def test_layer_3_serialises_nothing_of_its_own_st2() -> None:
    """No lock, no semaphore, no ``to_thread``, no ``sqlite3`` — the connection already does it.

    ST-2's choice of ``aiosqlite`` is only worth anything if the code trusts it. A lock added later
    "to be safe" would serialise every Layer 3 read behind every Layer 3 write for the lifetime of
    the daemon, and being redundant it would never be measured or removed. Structural check, because
    the cost of the mistake is invisible in every test that is not a load test.
    """
    offenders: list[str] = []
    for path, tree, _source in modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"Lock", "Semaphore", "to_thread"}:
                offenders.append(f"{path.name}: {node.attr}")
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = [alias.name for alias in node.names]
                module = getattr(node, "module", None) or ""
                if "sqlite3" in names or module == "sqlite3":
                    offenders.append(f"{path.name}: sqlite3")
    assert offenders == []


@pytest.mark.asyncio
async def test_a_write_is_durable_the_moment_its_statement_returns_st2(kb: Path, db: Path) -> None:
    """Autocommit, asserted from outside: a second connection sees the row with no ``commit()``.

    aiosqlite's default ``isolation_level`` is ``''`` — deferred — so a bare ``INSERT`` would open an
    implicit transaction that stays open until somebody commits. ``open_connection`` passes
    ``isolation_level=None`` instead, which is what makes ST-3's "short autocommit statements" true
    of every statement rather than of the ones whose author remembered. The observable form of that
    is this: durability does not depend on a later call that a raising handler might never make.
    """
    async with opened(kb, db) as runtime, store(runtime) as (writer, _):
        await writer.execute(INSERT_ROW, ("T-uncommitted", COOKING, "t-uncommitted"))
        # Deliberately no `writer.commit()` anywhere in this test.
        reader = await open_connection(runtime.db_path)
        try:
            assert "T-uncommitted" in await session_ids(reader)
        finally:
            await reader.close()


@pytest.mark.asyncio
async def test_a_failing_coroutine_cannot_undo_another_coroutines_write_st2_st9(
    kb: Path, db: Path
) -> None:
    """Two handlers share one connection; one fails. The other's row must be unaffected.

    On a *shared* connection under deferred isolation, transactions are a property of the connection
    rather than of the coroutine — so one handler's ``rollback()`` in an ``except`` block discards
    every other coroutine's pending statement, and one handler's ``commit()`` commits them. Measured
    the other way round: six coroutines, one raising before its own commit, six rows persisted where
    five were expected. ST-9 needs each session write to be independently idempotent and safe to
    repeat after a crash, and neither is true if the outcome depends on what an unrelated coroutine
    did between the statement and the commit. The first half of this test shows the damage; the
    second shows the real connection is immune to it.
    """
    inserted = asyncio.Event()
    failed = asyncio.Event()

    async def interleaved(connection: aiosqlite.Connection) -> None:
        async def survivor() -> None:
            await connection.execute(INSERT_ROW, ("T-survivor", COOKING, "t-survivor"))
            inserted.set()
            await failed.wait()
            await connection.commit()

        async def casualty() -> None:
            await inserted.wait()
            await connection.execute(INSERT_ROW, ("T-casualty", COOKING, "t-casualty"))
            await connection.rollback()  # an ordinary `except:` branch
            failed.set()

        await asyncio.gather(survivor(), casualty())

    async with opened(kb, db) as runtime, store(runtime) as (real, _):
        deferred = await aiosqlite.connect(str(runtime.db_path))
        try:
            await interleaved(deferred)
        finally:
            await deferred.close()
        # The casualty's rollback took the survivor's completed write with it.
        assert "T-survivor" not in await session_ids(real)

        inserted.clear()
        failed.clear()
        await interleaved(real)
        assert "T-survivor" in await session_ids(real)


# --------------------------------------------------------------------------------------
# ST-3 · never hold a write transaction across an await
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_transaction_held_across_an_await_locks_out_a_concurrent_writer_st3(
    kb: Path, db: Path
) -> None:
    """The failure ST-3 forbids, reproduced deliberately — because the real thing is unrecognisable.

    WAL has exactly one writer. A Layer 3 handler that opened a transaction and then awaited
    anything — a model call, an event, a client — was measured killing a concurrent checkpointer run
    with ``database is locked after 16.09s``. What the human sees is not a lock message: it is a run
    that failed sixteen seconds in, with a file written and no flush, and nothing in the traceback
    pointing at the HTTP handler that was holding the transaction. Reproduced here on a short
    ``busy_timeout`` so it costs milliseconds instead of seconds, and the same writer succeeds the
    moment the transaction is short again.
    """
    async with opened(kb, db) as runtime, store(runtime) as (holder, _):
        other = await open_connection(runtime.db_path)
        await other.execute("PRAGMA busy_timeout=50")
        opened_txn = asyncio.Event()
        may_commit = asyncio.Event()

        async def holds_across_an_await() -> None:
            await holder.execute("BEGIN IMMEDIATE")  # exactly what ST-3 forbids
            opened_txn.set()
            await may_commit.wait()
            await holder.execute("COMMIT")

        async def concurrent_writer() -> None:
            await opened_txn.wait()
            with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                await other.execute(INSERT_ROW, ("T-blocked", COOKING, "t-blocked"))
            may_commit.set()

        try:
            await asyncio.gather(holds_across_an_await(), concurrent_writer())
            # With no transaction held, the identical statement lands immediately.
            await other.execute(INSERT_ROW, ("T-blocked", COOKING, "t-blocked"))
            assert "T-blocked" in await session_ids(holder)
        finally:
            await other.close()


def test_no_layer_3_statement_opens_a_transaction_st3() -> None:
    """Nothing in ``pkb.service`` may issue ``BEGIN``. Every write is one short autocommit statement.

    This is the structural half of the rule, and it is the half that holds. The runtime demonstration
    above proves what a held transaction does; only a check over the source proves that no future
    handler — a batched delete, a "transactional" session rename — quietly reintroduces one. Note the
    asymmetry with Layer 2's scan queue, which *does* use ``BEGIN IMMEDIATE``: it runs on a
    synchronous connection in a worker thread with no await between the ``BEGIN`` and the ``COMMIT``,
    which is the only shape where it is safe.
    """
    statements = [
        (path.name, statement)
        for path, tree, source in modules()
        for statement in executed_sql(tree, source)
    ]
    assert statements, "the scanner found no SQL at all — it stopped scanning what it claims to"
    offenders = [
        f"{name}: {statement.strip()[:60]}"
        for name, statement in statements
        if "BEGIN" in statement.upper()
    ]
    assert offenders == []


# --------------------------------------------------------------------------------------
# ST-8 · the schema version lives in Layer 3's own table
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_writer_of_this_file_claims_user_version_st8(kb: Path, db: Path) -> None:
    """``PRAGMA user_version`` is one counter per *file*, and four independent writers share it.

    The checkpointer, the langgraph store, Layer 2's scan queue and Layer 3 all write this file. Any
    one of them claiming ``user_version`` for its own schema would silently overwrite the others'
    idea of their version — and the symptom arrives later, as a migration that runs twice or not at
    all. Layer 3 keeps its version in ``pkb_session_migrations`` (mirroring the store's
    ``store_migrations`` precedent and ``threads.py``'s own ``pkb_service_migrations``, before Task
    10 deleted that module), and this asserts the pragma is still untouched after a full open, a
    service setup, a live run and a repeat setup.
    """
    async with (
        opened(kb, db, scripted(says("done"))) as runtime,
        store(runtime) as (conn, sessions),
    ):
        assert await scalar(conn, "PRAGMA user_version") == 0

        [event async for event in runtime.run(COOKING, "T-version", "hello")]
        await sessions.setup()  # a second build over an existing file: additive, never a rewrite

        assert await scalar(conn, "PRAGMA user_version") == 0
        cursor = await conn.execute(f"SELECT version FROM {MIGRATIONS_TABLE}")
        assert [row[0] for row in await cursor.fetchall()] == [1]
