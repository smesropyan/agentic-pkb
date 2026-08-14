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
@pytest.mark.superseded
async def test_rebinding_can_move_a_chat_to_another_agents_thread_tg40(db_path: Path) -> None:
    """Rebinding to an id from ``/threads`` is the supported cross-channel resume (D3).

    A thread started in the TUI is meant to be finishable from a phone, so a bind must accept a
    thread the chat has never seen — including a derived, fan-out id — and simply become the
    current one. A store that only ever appended for the agent it first saw would make arch §8's
    headline scenario unreachable from Telegram.

    Superseded (Task 7 rebuilds this): the scenario is built on ``FANOUT_THREAD``'s
    ``<t>::<agent>`` derived-thread notation and its whole point is arch §8's cross-channel resume
    of an approval parked on a Librarian fan-out — both retired with the interrupt surface and the
    parent/derived id split. A channel rebinding to a different session it did not create is a
    property Task 7's ``attach``/``detach`` still needs, but the successor test wants a session id,
    not a derived thread shape, to prove it with.
    """
    async with opened(db_path) as store:
        await store.bind(CHAT, GENERAL, THREAD, LIBRARIAN)
        await store.bind(CHAT, GENERAL, FANOUT_THREAD, COOKING)

        assert await store.bound_session(CHAT, GENERAL) == FANOUT_THREAD


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


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_an_unknown_handle_resolves_to_nothing_tg58(db_path: Path) -> None:
    """A handle the store does not know must produce a hand-off, never a guessed thread.

    ``callback_data`` carries only ``v1|<handle>|<index>|<verb>``, so the handle is the *whole* of
    what a press knows. If an unknown one returned anything but ``None`` the adapter would resume an
    interrupt it inferred — applying a decision the human made about one write to whichever approval
    happens to be pending now, with no undo.

    Superseded (Task 6 rebuilds this): ``prompt``/the whole handle-resolves-to-an-approval surface
    is deleted with the interrupt/resume machinery — every write lands immediately, so no press ever
    needs to be resolved against a parked decision.
    """
    async with opened(db_path) as store:
        assert await store.prompt("deadbeef") is None


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_a_prompt_is_readable_by_an_adapter_that_never_saw_the_message_tg58(
    db_path: Path,
) -> None:
    """This is the whole answer to "what happens to a button pressed after the daemon restarted".

    Telegram redelivers an unconfirmed callback for 24 hours, so the press arrives into an adapter
    with no memory of the message at all. Everything the resume path needs — which thread to read,
    which interrupt to expect, how many answers make a complete set — has to come back off the disk,
    or the human's tap is answered with "I cannot find that approval" on an approval that is still
    perfectly live.

    Superseded (Task 6 rebuilds this): there is no resume path and nothing is ever pending, so a
    restarted adapter has no approval row to recover.
    """
    async with opened(db_path) as store:
        await open_two_action_prompt(store)

    async with opened(db_path) as restarted:
        row = await restarted.prompt(HANDLE)

        assert row is not None
        assert row["chat_id"] == CHAT
        assert row["thread_id"] == FANOUT_THREAD
        assert row["interrupt_id"] == "int-7"
        assert row["action_count"] == 2
        assert row["answers"] == {}
        assert row["message_ids"] == []
        assert row["resolved"] is False


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_every_message_of_an_approval_is_remembered_tg63(db_path: Path) -> None:
    """All N keyboards must die together, so all N message ids have to be recoverable.

    A TUI modal closes; a Telegram message lives in the chat forever with its buttons live. If one
    message of a multi-action approval is forgotten, the human scrolls back a week later, presses
    approve on a write that already happened, and either gets a stale alert (lucky) or answers
    whatever interrupt is pending *now* (not lucky).

    Superseded (Task 6 rebuilds this): there is no keyboard to clear because there is no approval to
    render — the operator's instruction is the approval, so nothing sits in the chat waiting to be
    pressed.
    """
    async with opened(db_path) as store:
        await open_two_action_prompt(store)
        await store.record_message(HANDLE, 11)
        await store.record_message(HANDLE, 12)

    async with opened(db_path) as restarted:
        await restarted.record_message(HANDLE, 13)
        row = await restarted.prompt(HANDLE)

        assert row is not None
        assert row["message_ids"] == [11, 12, 13]


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_answers_accumulate_across_separate_calls_and_across_a_restart_tg60(
    db_path: Path,
) -> None:
    """CL-6 forbids padding a missing answer, so a lost tap makes the approval unanswerable here.

    The two actions are two separate messages and therefore two separate updates, minutes apart if
    the human puts the phone down. Nothing may be submitted until all of them are in — so the
    accumulator is the state that decides whether ``resolve`` is called at all, and if it is dropped
    by a restart the earlier taps are gone and the only way left to finish is the TUI.

    Superseded (Task 6 rebuilds this): there is no multi-action decision to accumulate — every write
    lands on the operator's instruction alone.
    """
    async with opened(db_path) as store:
        await open_two_action_prompt(store)

        assert await store.record_answer(HANDLE, 0, "approve") == {0: "approve"}

    async with opened(db_path) as restarted:
        assert await restarted.record_answer(HANDLE, 1, "reject") == {0: "approve", 1: "reject"}

        row = await restarted.prompt(HANDLE)
        assert row is not None
        assert row["answers"] == {0: "approve", 1: "reject"}


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_the_returned_answers_are_keyed_by_action_index_not_by_a_string_tg60(
    db_path: Path,
) -> None:
    """The answers are ordered against ``request.actions`` by index, so the key must be an integer.

    JSON has no integer keys, so the round-trip through the row is exactly where a ``0`` quietly
    becomes ``"0"``. The set then never matches an ``action_count`` check by index, ``decisions``
    are assembled in the wrong order — or in the worst case sorted as strings, putting action 10
    before action 2 — and the human's approve lands on a different write than the one they read.

    Superseded (Task 6 rebuilds this): ``request.actions``/``Decision`` and the whole indexed-answer
    shape belong to the interrupt surface that is deleted outright.
    """
    async with opened(db_path) as store:
        await store.open_prompt(HANDLE, CHAT, GENERAL, FANOUT_THREAD, "int-7", 12)
        await store.record_answer(HANDLE, 2, "approve")
        answers = await store.record_answer(HANDLE, 10, "reject")

        assert answers == {2: "approve", 10: "reject"}
        assert sorted(answers) == [2, 10]


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_answering_the_same_action_twice_replaces_rather_than_appends_tg64(
    db_path: Path,
) -> None:
    """A destructive reason takes two taps, and the second tap is another answer for one index.

    ``delete``, ``topic-creation`` and ``conflict-resolution`` replace the keyboard with an explicit
    confirm/cancel pair, so index 0 is answered twice by design. If the second answer were appended
    the set would hold N+1 entries for N actions and never equal the freshly-read request's count —
    the approval would sit parked forever with the human staring at a confirmed button.

    Superseded (Task 6 rebuilds this): a destructive write is no longer a two-tap confirm/cancel
    parked on a gate — it is the operator's instruction, executed.
    """
    async with opened(db_path) as store:
        await store.open_prompt(HANDLE, CHAT, GENERAL, THREAD, "int-9", 1)
        await store.record_answer(HANDLE, 0, "approve")
        answers = await store.record_answer(HANDLE, 0, "reject")

        assert answers == {0: "reject"}
        assert len(answers) == 1


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_a_resolved_prompt_keeps_its_answers_and_says_it_is_resolved_tg63(
    db_path: Path,
) -> None:
    """A replayed press must be recognisable as *already answered*, not as unknown.

    The two look identical to an adapter that deletes the row on resolution, and they call for
    opposite replies: TG-62's alert saying another channel already answered it, versus TG-58's
    hand-off saying the approval could not be located. The redelivery window is 24 hours and the
    message keeps its buttons until they are removed, so a second press is the expected case.

    Superseded (Task 6 rebuilds this): there is no resolved/unresolved state because there is no
    parked decision to resolve.
    """
    async with opened(db_path) as store:
        await open_two_action_prompt(store)
        await store.record_answer(HANDLE, 0, "approve")
        await store.record_answer(HANDLE, 1, "approve")
        await store.resolve_prompt(HANDLE)

    async with opened(db_path) as restarted:
        row = await restarted.prompt(HANDLE)

        assert row is not None
        assert row["resolved"] is True
        assert row["answers"] == {0: "approve", 1: "approve"}


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_two_approvals_in_one_chat_never_share_an_accumulator_tg60(db_path: Path) -> None:
    """A chat can hold more than one parked approval, and the handle is what keeps them apart.

    A fan-out gates each expert separately, so two keyboards sit in the chat at once. If the
    accumulator were keyed by chat or thread instead of by the opaque handle, a tap on the Grilling
    approval would count towards the Cooking one and ``resolve`` would fire early — submitting a
    decision the human never made about a file they never saw.

    Superseded (Task 6 rebuilds this): there is no fan-out gate and nothing is ever parked, so no
    chat ever holds two approvals to keep apart.
    """
    async with opened(db_path) as store:
        await store.open_prompt(HANDLE, CHAT, GENERAL, FANOUT_THREAD, "int-7", 2)
        await store.open_prompt("b2c3d4e5", CHAT, GENERAL, f"{THREAD}::topic/grilling", "int-8", 1)

        await store.record_answer(HANDLE, 0, "approve")
        other = await store.record_answer("b2c3d4e5", 0, "reject")

        first = await store.prompt(HANDLE)
        assert first is not None
        assert first["answers"] == {0: "approve"}
        assert other == {0: "reject"}


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_two_taps_arriving_together_do_not_lose_an_answer_tg60(db_path: Path) -> None:
    """The accumulator decides whether ``resolve`` is ever called, so a dropped tap parks forever.

    ``record_answer`` is a ``prompt()`` read, an ``await``, then an ``UPDATE`` of the whole JSON
    blob. Two coroutines both read ``{}`` before either writes, so the second write erases the
    first — and because CL-6 forbids padding a missing answer, the set never reaches
    ``action_count``, ``resolve`` is never called and the interrupt stays parked with both of the
    human's taps already spent. The failure is silent from the phone: the buttons appeared to work.

    The merge therefore happens inside the ``UPDATE`` itself, so what is written is what was read.
    The persisted row is only half the claim: ``_on_callback`` decides on the map ``record_answer``
    *returns* (``if len(answers) < prompt["action_count"]: return``), so one of the two calls has to
    come back holding both answers or ``resolve`` is still never reached.

    Superseded (Task 6 rebuilds this): there is no accumulator and no ``resolve`` to race towards —
    the concurrent-write discipline this proves is real, but it needs a successor over whatever
    Task 6 leaves in its place, not over the deleted answer-merge.
    """
    async with opened(db_path) as store:
        await open_two_action_prompt(store)

        returned = await asyncio.gather(
            store.record_answer(HANDLE, 0, "approve"),
            store.record_answer(HANDLE, 1, "reject"),
        )

        row = await store.prompt(HANDLE)
        assert row is not None
        assert row["answers"] == {0: "approve", 1: "reject"}
        assert max(len(answers) for answers in returned) == row["action_count"]


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_two_messages_recorded_together_do_not_lose_a_keyboard_tg60(db_path: Path) -> None:
    """``record_message`` had ``record_answer``'s shape, and it loses a keyboard rather than a tap.

    The N messages of a multi-action approval are posted one per action, and every one of their ids
    has to reach the row or TG-63 cannot clear all N. A read-modify-write across an ``await`` drops
    whichever id was written second, and the message it belonged to keeps its buttons forever: the
    human scrolls back, presses approve on a write that already happened, and either gets a stale
    alert or answers whatever interrupt is pending now. Nothing about that is visible when it
    happens, so the assertion is on the ids that survive a concurrent pair, not on a serial one.

    Superseded (Task 6 rebuilds this): there is no keyboard, so no message id ever needs to be
    remembered for one.
    """
    async with opened(db_path) as store:
        await open_two_action_prompt(store)

        await asyncio.gather(
            store.record_message(HANDLE, 11),
            store.record_message(HANDLE, 12),
        )

        row = await store.prompt(HANDLE)
        assert row is not None
        assert sorted(row["message_ids"]) == [11, 12]


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_recording_against_an_unknown_handle_conjures_no_prompt_tg58(db_path: Path) -> None:
    """A press whose row is gone must stay unanswerable — never become an approval of its own.

    Superseded (Task 6 rebuilds this): there is no prompt row and no interrupt for a stale handle to
    conjure.

    ``callback_data`` is attacker-visible in the sense that it survives forever in the chat and is
    replayed by Telegram for a day. If a stale handle created a row on first use, the adapter would
    later read that invented row, find a thread id in it and resume an interrupt on the strength of
    a message nobody currently pending ever sent.
    """
    async with opened(db_path) as store:
        assert await store.record_answer("nosuchhandle", 0, "approve") == {}
        await store.record_message("nosuchhandle", 99)

        assert await store.prompt("nosuchhandle") is None


# --------------------------------------------------------------------------------------
# ST-7 — the bot owns the `pkb_telegram_*` tables and nothing else
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_setup_creates_exactly_the_pkb_telegram_tables_st7(db_path: Path) -> None:
    """The bot shares one file with the checkpointer, the harness store and Layer 3's own table.

    ST-7 reserves the ``pkb_`` prefix for Layer 3 precisely so this file can be shared, and the cost
    of taking an unprefixed name is not a failed test but a corrupted graph: ``threads``,
    ``checkpoints`` and ``writes`` are the run's memory, and a colliding ``CREATE`` or an ``ALTER``
    against one of them takes out every conversation in the knowledge base at once.

    Superseded (Task 6 rebuilds this): the exact owned-table set asserted here names
    ``PROMPTS_TABLE``, which is deleted with the approval-prompt surface — the reserved-prefix
    principle survives and needs the successor set once Task 6/7 settle what ``setup()`` creates.
    """
    await seeded_foreign_tables(db_path)
    before = catalog(db_path)

    async with opened(db_path) as store:
        await store.setup()

    after = catalog(db_path)

    assert set(after) - set(before) == OWNED_TABLES
    assert all(name.startswith("pkb_telegram_") for name in set(after) - set(before))


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


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_setup_over_an_existing_file_keeps_every_row_the_bot_already_wrote_st7(
    db_path: Path,
) -> None:
    """``setup()`` runs on every start, including the restart that happens seconds after a crash.

    A ``CREATE TABLE`` without ``IF NOT EXISTS`` raises — and inside the task that is another
    ``_supervise`` restart, i.e. a crash loop that never gets as far as polling. A ``DROP`` first
    would be worse and quieter: the ledger empties, so every update Telegram still holds is claimed
    again and every parked approval becomes a handle nobody can resolve.

    Superseded (Task 6 rebuilds this): the final assertion reads back a surviving prompt row, which
    has no successor once approvals are gone; the ledger/binding halves of "setup never loses a row"
    survive and need a rebuild without it.
    """
    async with opened(db_path) as store:
        await store.claim(42, CHAT, GENERAL, "message")
        await store.bind(CHAT, GENERAL, THREAD, LIBRARIAN)
        await open_two_action_prompt(store)

    async with opened(db_path) as restarted:
        await restarted.setup()

        assert await restarted.next_offset() == 43
        assert await restarted.bound_session(CHAT, GENERAL) == THREAD
        assert await restarted.prompt(HANDLE) is not None


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


@pytest.mark.asyncio
@pytest.mark.superseded
async def test_a_whole_approval_cycle_leaves_the_knowledge_base_byte_identical_tg28(
    tmp_path: Path,
) -> None:
    """I3, measured rather than argued: nothing under ``kb_root`` moves, including its mtimes.

    The structural half is stronger than this test — neither telegram module imports ``os``,
    ``shutil`` or ``tempfile``, and the built SV-22 scan fails outright on one that does — but the
    structural half is about *imports*, and this is about the tree. The database deliberately lives
    **outside** ``kb_root`` (``<kb>/../pkb.sqlite``), which is the whole reason a Layer 3 table can
    hold a transport's bookkeeping at all.

    Superseded (Task 6 rebuilds this): the walk this drives is named for and built around a full
    approval cycle (``open_prompt`` … ``resolve_prompt``), which no longer exists. I3 itself is
    permanent and needs a successor walk over whatever a full Telegram turn becomes.
    """
    kb_root = tmp_path / "kb"
    (kb_root / "topics" / "Cooking").mkdir(parents=True)
    (kb_root / "topics" / "Cooking" / "steak.md").write_text("existing\n", encoding="utf-8")
    (kb_root / "tags.md").write_text("# Tags\n", encoding="utf-8")
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(kb_root.rglob("*"))
        if path.is_file()
    }

    async with opened(tmp_path / "pkb.sqlite") as store:
        await store.claim(1, CHAT, GENERAL, "message")
        await store.bind(CHAT, GENERAL, THREAD, COOKING)
        await store.started(1, THREAD, "run-1")
        await store.open_prompt(HANDLE, CHAT, GENERAL, THREAD, "i-1", 2)
        await store.record_message(HANDLE, 1001)
        await store.record_answer(HANDLE, 0, "a")
        await store.record_answer(HANDLE, 1, "r")
        await store.resolve_prompt(HANDLE)
        await store.dispatched(1)

    after = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(kb_root.rglob("*"))
        if path.is_file()
    }
    assert after == before
    assert (tmp_path / "pkb.sqlite").is_file(), "the database is beside the tree, never inside it"
