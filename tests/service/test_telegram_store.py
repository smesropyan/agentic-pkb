"""The bot's durable state — bindings, the update ledger, the approval prompts (decisions S and T).

Everything here runs against a **real** ``aiosqlite`` connection over ``tmp_path``, opened
``isolation_level=None`` exactly as the daemon opens it (ST-2). A fake connection would only assert
the fake, and the three questions this module exists to answer are all questions about what is still
true after the process that wrote it is gone:

* ``_supervise`` restarts the adapter with **nothing carried across** — every dict, client and
  subscription of the previous invocation is dropped. So each test that matters here closes the
  store and opens a new one over the same file, because "survives a restart" is the entire claim
  decision S makes.
* ``getUpdates`` is at-least-once and an unconfirmed update is redelivered for **24 hours**. The
  ledger is what turns that into at-most-once *agent execution* (decision T, TG-29): a second run of
  the same update is a second write into a tree with **no undo** (D6).
* CL-6 forbids padding a missing answer, so a multi-action approval either accumulates every tap
  durably or can never be finished from the phone at all (TG-60).

No key, no network, no model: the only I/O is ``tmp_path`` and its SQLite file.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
import pytest

from pkb.service.telegram import (
    BINDINGS_TABLE,
    CHANNELS_TABLE,
    GENERAL,
    LEDGER_TABLE,
    SqliteTelegramStore,
)

CHAT = 4242
OTHER_CHAT = -1001
THREAD = "6f1b0d4a-0f6e-4f3a-9d7c-1b2c3d4e5f60"
FANOUT_THREAD = f"{THREAD}::topic/cooking"
LIBRARIAN = "librarian"
COOKING = "topic/cooking"
HANDLE = "7f3a2b1c"

# The tables the checkpointer, the store and Layers 2 and 3 already own on this file (ST-7). None of
# them is the bot's, and the bot must be invisible to every one of them.
FOREIGN_SCHEMA = """
CREATE TABLE checkpoints (thread_id TEXT NOT NULL, checkpoint BLOB);
CREATE TABLE writes (thread_id TEXT NOT NULL, task_id TEXT NOT NULL);
CREATE TABLE store (prefix TEXT NOT NULL, key TEXT NOT NULL, value TEXT);
CREATE TABLE store_migrations (v INTEGER PRIMARY KEY);
CREATE TABLE scan_queue (path TEXT PRIMARY KEY, queued_at TEXT NOT NULL);
CREATE TABLE threads (thread_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL);
CREATE INDEX store_prefix_idx ON store (prefix);
"""

OWNED_TABLES = {
    BINDINGS_TABLE,
    LEDGER_TABLE,
    CHANNELS_TABLE,
    f"{CHANNELS_TABLE}_topic_idx",
}
"""Everything ``setup()`` adds to a shared file, **including the index** (ST-7, TG-77).

The index is here because ``catalog()`` reads ``sqlite_master``, where an index is a row
like any other — and because ST-7's rule is about the *names* this layer takes in a file
it shares with the checkpointer. An unprefixed index name collides exactly as destructively
as an unprefixed table name and is easier to add without noticing. The directory itself
(``CHANNELS_TABLE``) arrived with §9; the legacy bindings table is deliberately absent,
because a fresh install is never handed it (TG-28 amended); ``PROMPTS_TABLE`` is deliberately
absent too now (Task 6, DESIGN.md §2.10) — the test below that reads this set is superseded and
still names the old table in its own docstring, pending Task 6/7's successor.
"""


# --------------------------------------------------------------------------------------
# Harness — one file, opened and reopened the way the daemon does
# --------------------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """The daemon's SQLite file, which lives *outside* ``kb_root`` (decision S, I3)."""
    return tmp_path / "pkb.sqlite"


@asynccontextmanager
async def connected(db_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """A connection with the two settings the daemon uses: autocommit and WAL (ST-2, AP-4)."""
    connection = await aiosqlite.connect(str(db_path), isolation_level=None)
    try:
        await connection.execute("PRAGMA journal_mode=WAL")
        yield connection
    finally:
        await connection.close()


@asynccontextmanager
async def opened(db_path: Path) -> AsyncIterator[SqliteTelegramStore]:
    """A store over its own connection.

    Every use of this in a second ``async with`` over the same path is a **restart**: a new
    connection, a new object, no shared memory of any kind — and ``setup()`` runs again, because the
    daemon calls it on every start and an existing file must not be re-created out from under its
    rows.
    """
    async with connected(db_path) as connection:
        store = SqliteTelegramStore(connection)
        await store.setup()
        yield store


def catalog(db_path: Path) -> dict[str, str]:
    """``name → CREATE statement`` for everything in ``sqlite_master`` SQLite did not add itself."""
    with sqlite3.connect(db_path) as raw:
        rows = raw.execute("SELECT name, sql FROM sqlite_master")
        return {str(r[0]): str(r[1]) for r in rows if not str(r[0]).startswith("sqlite_")}


def rows_of(db_path: Path, table: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(db_path) as raw:
        return [tuple(row) for row in raw.execute(f"SELECT * FROM {table}")]


async def seeded_foreign_tables(db_path: Path) -> None:
    """Put the five foreign tables and their rows on the file before the bot ever opens it."""
    async with connected(db_path) as connection:
        await connection.executescript(FOREIGN_SCHEMA)
        await connection.execute("INSERT INTO threads VALUES (?, ?)", (THREAD, LIBRARIAN))
        await connection.execute("INSERT INTO checkpoints VALUES (?, ?)", (THREAD, b"\x01\x02"))
        await connection.execute("INSERT INTO scan_queue VALUES (?, ?)", ("a.md", "2026-08-08"))


# --------------------------------------------------------------------------------------
# The ledger — at-most-once agent execution (decision T, TG-29)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_redelivered_update_is_claimed_only_once_tg29(db_path: Path) -> None:
    """Telegram resends an unconfirmed update for 24 hours; a bot that re-ran each one re-files.

    The turn behind an update writes to a knowledge base with **no undo** (D6), and the second run's
    text is not the first's — so a human who sent one note ends up with two versions of it and no way
    to remove either. ``claim`` returning ``True`` is the only thing standing between a redelivery
    and a duplicate write, so the second answer for the same ``update_id`` must be ``False``
    whatever else happened in between.
    """
    async with opened(db_path) as store:
        assert await store.claim(100, CHAT, GENERAL, "message") is True
        assert await store.claim(100, CHAT, GENERAL, "message") is False
        await store.dispatched(100)
        assert await store.claim(100, CHAT, GENERAL, "message") is False


@pytest.mark.asyncio
async def test_a_redelivery_after_a_restart_is_still_refused_tg29(db_path: Path) -> None:
    """The redelivery window outlives the process, so the answer has to come off the disk.

    ``_supervise`` restarts the adapter with nothing carried across, and the update Telegram is
    still holding is precisely the one the crash interrupted. An in-memory "seen" set answers
    ``True`` on the first poll after every restart — i.e. it re-runs the turn exactly in the case
    where the turn most likely already wrote.
    """
    async with opened(db_path) as store:
        assert await store.claim(100, CHAT, GENERAL, "message") is True

    async with opened(db_path) as restarted:
        assert await restarted.claim(100, CHAT, GENERAL, "message") is False


@pytest.mark.asyncio
async def test_a_fresh_database_has_no_offset_tg29(db_path: Path) -> None:
    """``None`` means "whatever Telegram still has" — which is correct, and is not ``0``.

    An update nobody ever claimed was never processed, so a first start must not assert a position
    it cannot know. Returning ``0`` (or ``-1``) here would be the adapter inventing an acknowledgement
    on behalf of a ledger that has never seen anything, and TG-30's cold-start drain — which is what
    keeps a day of backlog from being replayed as agent turns — is selected on exactly this ``None``.
    """
    async with opened(db_path) as store:
        assert await store.next_offset() is None


@pytest.mark.asyncio
async def test_the_offset_is_the_highest_claim_plus_one_whatever_order_they_arrived_tg29(
    db_path: Path,
) -> None:
    """``MAX(update_id) + 1`` acknowledges every id at or below it — so it must never lag.

    An offset one short of the maximum re-delivers an update that was already dispatched, which is
    the duplicate write D6 has no answer for; an offset past the maximum silently drops the human's
    next message. Deriving it from the rows rather than from arrival order is what makes it right
    when updates are handled out of order.
    """
    async with opened(db_path) as store:
        await store.claim(104, CHAT, GENERAL, "message")
        await store.claim(100, CHAT, GENERAL, "callback_query")
        await store.claim(102, OTHER_CHAT, GENERAL, "message")

        assert await store.next_offset() == 105


@pytest.mark.asyncio
async def test_the_offset_does_not_wait_for_dispatch_tg29(db_path: Path) -> None:
    """The row is written **before** the run, and the offset follows the row, not the outcome.

    This is the whole trade decision T makes: acknowledging after processing re-runs a turn that may
    already have written, so the ledger acknowledges first and reports the gap instead. If the offset
    counted only dispatched updates, a crash mid-turn would hand the same update back on the next
    poll — reintroducing exactly the duplicate the ledger was added to prevent.
    """
    async with opened(db_path) as store:
        await store.claim(7, CHAT, GENERAL, "message")

        assert await store.next_offset() == 8
        assert await store.orphans() == [(7, CHAT, GENERAL)]


@pytest.mark.asyncio
async def test_a_reopened_store_resumes_at_the_offset_the_ledger_implies_tg29(
    db_path: Path,
) -> None:
    """A restart must poll from where the file says, not from zero and not from Telegram's backlog.

    Resuming lower replays updates already dispatched (duplicate writes into a tree with no undo);
    resuming from ``None`` after a crash makes TG-30 discard the backlog, throwing away messages the
    human sent while the daemon was down. Both failures are invisible in the moment, so the resume
    point is asserted across an actual close/reopen rather than on a live object.
    """
    async with opened(db_path) as store:
        await store.claim(500, CHAT, GENERAL, "message")
        await store.claim(501, CHAT, GENERAL, "callback_query")
        await store.dispatched(500)

    async with opened(db_path) as restarted:
        assert await restarted.next_offset() == 502


@pytest.mark.asyncio
async def test_the_offset_is_derived_from_the_ledger_not_remembered_beside_it_tg29(
    db_path: Path,
) -> None:
    """Two records of the same fact can disagree; this one is reconstructible, so it is recomputed.

    A cached "last offset" that is written separately from the row is one crash away from pointing
    past a claimed update (lost message) or behind a dispatched one (duplicate run) — and nothing
    reports the disagreement. Asserted by having a *second* store claim the update: an object that
    kept its own counter would answer from stale memory, a derived one reads the ledger.
    """
    async with opened(db_path) as store, opened(db_path) as concurrent:
        assert await store.next_offset() is None
        await concurrent.claim(900, CHAT, GENERAL, "message")

        assert await store.next_offset() == 901


@pytest.mark.asyncio
async def test_an_update_claimed_but_never_dispatched_is_named_in_arrival_order_tg29(
    db_path: Path,
) -> None:
    """The crash between the row and ``start_run`` is a **named** loss, reported oldest-first.

    Retrying an orphan would re-run a turn that may already have written; saying nothing leaves the
    human believing their message was filed and waiting for a reply that is never coming. "I lost
    your message — send it again" is the only outcome they can act on, and it is only actionable if
    it names them in the order they were sent, so the human can tell which message it was.
    """
    async with opened(db_path) as store:
        for update_id in (12, 5, 9):
            await store.claim(update_id, CHAT, GENERAL, "message")
        await store.dispatched(9)

        assert await store.orphans() == [(5, CHAT, GENERAL), (12, CHAT, GENERAL)]

    async with opened(db_path) as restarted:
        assert await restarted.orphans() == [(5, CHAT, GENERAL), (12, CHAT, GENERAL)]
        await restarted.dispatched(5)
        await restarted.dispatched(12)
        assert await restarted.orphans() == []


@pytest.mark.asyncio
async def test_a_clean_session_leaves_nothing_to_apologise_for_tg29(db_path: Path) -> None:
    """The notice must be rare, or it is noise the human learns to ignore.

    An ``orphans()`` that reported every handled update would put a "I lost your message" line at the
    top of the chat after every single restart, and the one restart that really did drop a message
    would be indistinguishable from the fifty that did not.
    """
    async with opened(db_path) as store:
        for update_id in (1, 2, 3):
            await store.claim(update_id, CHAT, GENERAL, "message")
            await store.dispatched(update_id)

    async with opened(db_path) as restarted:
        assert await restarted.orphans() == []


@pytest.mark.asyncio
async def test_twenty_concurrent_claims_of_one_update_yield_exactly_one_true_tg29(
    db_path: Path,
) -> None:
    """Only one caller may be told to run the turn, however many ask at once.

    The read-then-write shape of this check (``SELECT``, decide, ``INSERT``) is correct in a single
    thread and wrong in the daemon, where an ``await`` between the two hands control to another
    coroutine — and both callers then start a run for the same message. The exclusion has to be the
    primary key's, which is why the answer is the statement's own ``rowcount`` and not a lookup.
    """
    async with opened(db_path) as store:
        results = await asyncio.gather(
            *(store.claim(77, CHAT, GENERAL, "message") for _ in range(20))
        )

        assert results.count(True) == 1
        assert results.count(False) == 19
        assert await store.next_offset() == 78


@pytest.mark.asyncio
async def test_two_stores_on_one_file_still_agree_on_who_claimed_it_tg29(db_path: Path) -> None:
    """The daemon holds one connection today; the guarantee must not depend on that.

    A second connection is exactly what an operator's ``sqlite3`` session, a second daemon started
    by mistake, or a future worker looks like. If the at-most-once property lived in the object
    rather than in the file, each of them would run the same turn once more — and the only evidence
    would be duplicated notes in the tree.
    """
    async with opened(db_path) as first, opened(db_path) as second:
        outcomes = await asyncio.gather(
            first.claim(31, CHAT, GENERAL, "message"),
            second.claim(31, CHAT, GENERAL, "message"),
            first.claim(31, CHAT, GENERAL, "message"),
            second.claim(31, CHAT, GENERAL, "message"),
        )

        assert outcomes.count(True) == 1


@pytest.mark.asyncio
async def test_a_hundred_interleaved_statements_never_lock_the_file_st3(db_path: Path) -> None:
    """The bot writes on the inbound path — i.e. while a run is streaming into the same file.

    ST-3 was measured: a handler holding ``BEGIN IMMEDIATE`` across an ``await`` killed the
    concurrent checkpointer run after the victim's own 5 s ``busy_timeout``, surfacing as a failed
    agent run with a written file and no flush. Short autocommit statements are what make the same
    load boring, so the load is applied here across two connections and asserted to raise nothing.
    """
    async with opened(db_path) as first, opened(db_path) as second:
        work = []
        for n in range(50):
            work.append(first.claim(n, CHAT, GENERAL, "message"))
            work.append(second.bind(CHAT + n, GENERAL, f"{THREAD}-{n}", LIBRARIAN))
        await asyncio.gather(*work)

        assert await first.next_offset() == 50
        assert await second.bound_session(CHAT + 3, GENERAL) == f"{THREAD}-3"


# --------------------------------------------------------------------------------------
# The chat → thread binding (decision S, TG-26, TG-27)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unknown_chat_has_no_bound_session_tg26(db_path: Path) -> None:
    """The first message in a chat must be distinguishable from every later one.

    ``None`` is what selects ``create_thread``; anything else — a default id, an empty string —
    sends the first message of a conversation into a thread that does not exist, and the human's
    opening line disappears into a 404 they never see.
    """
    async with opened(db_path) as store:
        assert await store.bound_session(CHAT, GENERAL) is None


@pytest.mark.asyncio
async def test_a_binding_survives_a_restart_s(db_path: Path) -> None:
    """The amnesiac bot: one 502 restarts the task and the next message starts a *new* conversation.

    That failure has no error anywhere — the human keeps typing into what looks like the same chat
    while the thread holding their pending approval is orphaned behind them. Nothing in the task
    survives ``_supervise``, so the binding has to be on the disk, and this is the assertion that
    says so.
    """
    async with opened(db_path) as store:
        await store.bind(CHAT, GENERAL, THREAD, LIBRARIAN)

    async with opened(db_path) as restarted:
        assert await restarted.bound_session(CHAT, GENERAL) == THREAD


@pytest.mark.asyncio
async def test_rebinding_replaces_the_thread_rather_than_stacking_one_tg27(db_path: Path) -> None:
    """``/new`` is the only rotation there is, and it must leave exactly one current thread.

    If a rebind stacked a second row the chat would have two "current" threads, and which one the
    next message reached would depend on row order — a silent split, which is the same failure class
    TG-1 was ruled to fix. Proven by unbinding **once**: an older row underneath would surface.
    """
    async with opened(db_path) as store:
        await store.bind(CHAT, GENERAL, THREAD, LIBRARIAN)
        await store.bind(CHAT, GENERAL, "second-thread", LIBRARIAN)

        assert await store.bound_session(CHAT, GENERAL) == "second-thread"

        await store.unbind(CHAT, GENERAL)
        assert await store.bound_session(CHAT, GENERAL) is None


@pytest.mark.asyncio
async def test_rebinding_can_move_a_chat_to_a_different_agents_session_task7(
    db_path: Path,
) -> None:
    """Task 7 successor of ``test_rebinding_can_move_a_chat_to_another_agents_thread_tg40``: the
    same property — a channel accepts a rebind to a session it did not create, and simply becomes
    the current one — proven with a session id rather than a retired derived-thread shape."""
    first_session = "1e6a9f2c-11e1-4a7a-9b1e-2a6b7c8d9e0f"
    second_session = "2f7b0a3d-22f2-5b8b-ac2f-3b7c8d9e0f10"
    async with opened(db_path) as store:
        await store.bind(CHAT, GENERAL, first_session, LIBRARIAN)
        await store.bind(CHAT, GENERAL, second_session, COOKING)

        assert await store.bound_session(CHAT, GENERAL) == second_session
        assert await store.binding(CHAT, GENERAL) == (second_session, COOKING)


@pytest.mark.asyncio
async def test_unbinding_one_chat_leaves_every_other_chat_alone_tg27(db_path: Path) -> None:
    """One channel per expert means several chats share this table; ``/new`` is scoped to one.

    A ``/new`` in the Cooking chat that also reset the Librarian chat would rotate a conversation
    the human never touched — an invisible rotation, and there is no undo for the note that then
    lands in the wrong thread.
    """
    async with opened(db_path) as store:
        await store.bind(CHAT, GENERAL, THREAD, LIBRARIAN)
        await store.bind(OTHER_CHAT, GENERAL, FANOUT_THREAD, COOKING)

        await store.unbind(CHAT, GENERAL)

        assert await store.bound_session(CHAT, GENERAL) is None
        assert await store.bound_session(OTHER_CHAT, GENERAL) == FANOUT_THREAD


@pytest.mark.asyncio
async def test_unbinding_a_chat_that_was_never_bound_is_not_an_error_tg27(db_path: Path) -> None:
    """``/new`` in a fresh chat is a normal thing for a human to type, not a crash.

    Every exception inside the task is a ``_supervise`` restart, so a store that raised here would
    turn a harmless command into a bot that drops its poll, loses its in-flight state and comes back
    with a backoff — for the most ordinary keystroke in the command surface.
    """
    async with opened(db_path) as store:
        await store.unbind(CHAT, GENERAL)

        assert await store.bound_session(CHAT, GENERAL) is None


# --------------------------------------------------------------------------------------
# § the session rename (Task 7) — a binding row maps a chat/topic to a SESSION id
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_bound_session_id_round_trips_through_a_fresh_store(db_path: Path) -> None:
    """A binding row maps a chat/topic to a SESSION id — the row Task 7's brief names directly."""
    session_id = "3a8c1d4e-33f3-6c9c-bd3a-4c8d9e0f1122"
    async with opened(db_path) as store:
        await store.bind(CHAT, GENERAL, session_id, COOKING)

        assert await store.bound_session(CHAT, GENERAL) == session_id
        assert await store.binding(CHAT, GENERAL) == (session_id, COOKING)


@pytest.mark.asyncio
async def test_the_bindings_table_migrates_a_pre_session_thread_id_column(db_path: Path) -> None:
    """A file written before Task 7 has ``thread_id`` where the row now reads ``session_id`` — the
    rename is a metadata-only ``ALTER TABLE … RENAME COLUMN`` (see ``_migrate``'s own docstring),
    so a row minted under the old name is readable, unchanged, under the new one."""
    async with connected(db_path) as connection:
        await connection.execute(
            f"CREATE TABLE {BINDINGS_TABLE} ("
            f"chat_id INTEGER NOT NULL, topic_id INTEGER NOT NULL, thread_id TEXT NOT NULL, "
            f"agent_id TEXT NOT NULL, bound_at TEXT NOT NULL, PRIMARY KEY (chat_id, topic_id))"
        )
        await connection.execute(
            "INSERT INTO pkb_telegram_channel_bindings VALUES (?,?,?,?,?)",
            (CHAT, GENERAL, THREAD, LIBRARIAN, "2026-08-08T09:00:00Z"),
        )
        await connection.commit()

    async with opened(db_path) as store:
        assert await store.bound_session(CHAT, GENERAL) == THREAD
        assert await store.binding(CHAT, GENERAL) == (THREAD, LIBRARIAN)

    with sqlite3.connect(db_path) as raw:
        columns = {row[1] for row in raw.execute(f"PRAGMA table_info({BINDINGS_TABLE})")}
    assert "session_id" in columns
    assert "thread_id" not in columns


# --------------------------------------------------------------------------------------
# Approval prompts — the durable row a button press is resolved through (TG-57, TG-58, TG-60)
# --------------------------------------------------------------------------------------


async def open_two_action_prompt(store: SqliteTelegramStore) -> None:
    """One approval the human is being shown, with two actions and therefore two messages."""
    await store.open_prompt(HANDLE, CHAT, GENERAL, FANOUT_THREAD, "int-7", 2)


# --------------------------------------------------------------------------------------
# ST-7 — the bot owns the `pkb_telegram_*` tables and nothing else
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_full_bot_session_leaves_the_foreign_tables_byte_identical_st7(
    db_path: Path,
) -> None:
    """Every write the bot makes must be invisible to the five tables it does not own.

    The dangerous version of this failure is not a ``DROP`` — that fails loudly. It is a stray
    ``UPDATE threads SET …`` from the transport's own bookkeeping, which silently rewrites a row
    Layer 3 owns. So a whole session — bind, claim, dispatch, unbind — runs between the two reads,
    and both the schemas and the rows have to come back unchanged.

    Once claimed a full session also opened an approval prompt, recorded its messages and answers,
    and resolved it — that machinery is deleted with the rest of the interrupt/resume surface
    (Task 6, DESIGN.md §2.10), so the session this test builds is the bind/claim/dispatch/unbind
    shape that survives it; the isolation claim itself is unaffected by what is inside the session.
    """
    await seeded_foreign_tables(db_path)
    before = catalog(db_path)
    thread_rows = rows_of(db_path, "threads")
    checkpoint_rows = rows_of(db_path, "checkpoints")
    queue_rows = rows_of(db_path, "scan_queue")

    async with opened(db_path) as store:
        await store.bind(CHAT, GENERAL, THREAD, LIBRARIAN)
        await store.claim(1, CHAT, GENERAL, "message")
        await store.dispatched(1)
        await store.claim(2, CHAT, GENERAL, "callback_query")
        await store.unbind(CHAT, GENERAL)

    after = catalog(db_path)

    assert {name: sql for name, sql in after.items() if name in before} == before
    assert rows_of(db_path, "threads") == thread_rows
    assert rows_of(db_path, "checkpoints") == checkpoint_rows
    assert rows_of(db_path, "scan_queue") == queue_rows


# --------------------------------------------------------------------------------------
# § the connection this store must be handed (TG-28, ST-1, ST-3)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_transactional_connection_is_refused_at_setup_tg28(db_path: Path) -> None:
    """Autocommit is a **precondition**, and it was neither documented nor checked.

    Every statement in this module is a single short write, which is only true because Layer 3
    opens its connection ``isolation_level=None``. Handed a default aiosqlite connection,
    ``_merged``'s ``execute → fetchall → commit`` holds an implicit write transaction across two
    awaits — and ST-3 *measured* what that costs: a handler holding one across an ``await`` killed
    a concurrent checkpointer run after the victim's 5 s timeout, surfacing as a failed agent run
    with a written file and no flush. The bot writes on the inbound path, which is precisely when a
    run is streaming, so the failure would land on somebody else's turn and name this module
    nowhere.
    """
    connection = await aiosqlite.connect(db_path)  # the default: deferred transactions
    try:
        with pytest.raises(ValueError) as caught:
            await SqliteTelegramStore(connection).setup()
    finally:
        await connection.close()

    assert "isolation_level=None" in str(caught.value)


def test_no_statement_opens_a_transaction_it_has_to_hold_tg28() -> None:
    """``BEGIN`` appears nowhere, so there is no transaction for an ``await`` to sit inside.

    Asserted over the source rather than by behaviour because the failure is a *timing* one: with
    autocommit the same code is correct, and the day somebody adds ``BEGIN IMMEDIATE`` to make two
    statements atomic, every test here still passes and a concurrent checkpointer write starts
    failing five seconds later in another layer.
    """
    import ast
    from pathlib import Path as _Path

    import pkb.service.telegram as store_module

    source = _Path(store_module.__file__).read_text(encoding="utf-8")
    statements = [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    executed = [text.upper() for text in statements if "SELECT" in text or "UPDATE" in text]
    assert executed, "the walk found no SQL at all; this check is broken, not the code"
    for text in statements:
        assert "BEGIN " not in text.upper(), text


@pytest.mark.asyncio
async def test_the_store_is_hammered_while_a_run_streams_and_never_locks_tg28(
    db_path: Path,
) -> None:
    """The bot writes on the inbound path, which is exactly when the checkpointer is writing.

    Two connections on one file, one of them looping the checkpointer's own tables while the store
    accumulates ledger rows and directory rows (the approval prompts this used to hammer with are
    deleted with the rest of the interrupt/resume surface — Task 6, DESIGN.md §2.10 — but the
    concurrency claim is about writes racing the checkpointer, not about what they are): with a
    transaction held across an ``await`` this is where ``database is locked`` appears, five seconds
    later, on the *other* connection. Zero is the only acceptable number because the victim of the
    failure is an agent run, not the bot.
    """
    async with opened(db_path) as store:
        other = await aiosqlite.connect(db_path, isolation_level=None)
        try:
            await other.executescript(FOREIGN_SCHEMA)
            errors: list[str] = []

            async def checkpointing() -> None:
                for index in range(200):
                    try:
                        await other.execute(
                            "INSERT INTO checkpoints (thread_id, checkpoint) VALUES (?,?)",
                            (f"t-{index}", b"blob"),
                        )
                    except sqlite3.OperationalError as exc:  # pragma: no cover - the failure case
                        errors.append(str(exc))
                    await asyncio.sleep(0)

            async def botting() -> None:
                for index in range(200):
                    try:
                        await store.claim(index, CHAT, GENERAL, "message")
                        await store.started(index, THREAD, f"run-{index}")
                        await store.dispatched(index)
                        await store.open_channel(CHAT, index + 1, f"topic/agent-{index}")
                    except sqlite3.OperationalError as exc:  # pragma: no cover - the failure case
                        errors.append(str(exc))
                    await asyncio.sleep(0)

            await asyncio.gather(checkpointing(), botting())
        finally:
            await other.close()

    assert errors == []
