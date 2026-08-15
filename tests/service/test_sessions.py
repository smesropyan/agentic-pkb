"""``SessionStore`` — the one state machine, durable and named (S-1 … S-39).

Everything here runs on a real ``aiosqlite`` connection over ``tmp_path``, exactly as
``tests/service/test_threads.py`` did for ``ThreadStore``: no harness, no model, no network, and
nothing wired through ``RuntimeService`` — Task 3 builds the store alone (this module's own
docstring records why ``runtime.py``/``__init__.py`` stay untouched). Rule ids are cited per method
under test, per ``CLAUDE.md``'s "rule ids are the contract."
"""

from __future__ import annotations

import dataclasses
import inspect
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from pkb.service.sessions import (
    MIGRATIONS_TABLE,
    TABLE,
    IllegalSessionTransitionError,
    Session,
    SessionNameTakenError,
    SessionStore,
    UnknownSessionError,
    mint_session_id,
)

LIBRARIAN = "librarian"
COOKING = "topic/cooking"


def at(hour: int, minute: int = 0) -> datetime:
    """A fixed instant. ``_now`` renders to whole seconds, so ordering needs explicit stamps."""
    return datetime(2026, 8, 14, hour, minute, tzinfo=UTC)


@asynccontextmanager
async def session_store(db_path: Path) -> AsyncIterator[SessionStore]:
    """Layer 3's own connection, in autocommit, over a real file (mirrors ST-2, ST-3)."""
    connection = await aiosqlite.connect(str(db_path), isolation_level=None)
    try:
        store = SessionStore(connection)
        await store.setup()
        yield store
    finally:
        await connection.close()


def table_columns(db_path: Path, table: str) -> list[tuple[str, str, int, int]]:
    """``(name, declared type, NOT NULL, primary key)`` for one table, straight from SQLite."""
    with sqlite3.connect(db_path) as raw:
        return [
            (str(r[1]), str(r[2]), int(r[3]), int(r[5]))
            for r in raw.execute(f"PRAGMA table_info({table})")
        ]


# --------------------------------------------------------------------------------------
# § creation — S-1, S-5
# --------------------------------------------------------------------------------------
# S-2 is not this store's to test (fix round 2, finding 2): its capture/lesson distinction lives
# entirely in SessionFileWriter, which this module's own docstring says it never touches. S-2's
# dedicated tests are in tests/service/test_session_file.py instead.


@pytest.mark.asyncio
async def test_create_records_the_operator_and_the_objective_s1(tmp_path: Path) -> None:
    """S-1: a session is durable, held on one agent for one objective — fixed at creation."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(
            COOKING, "a rub that doesn't burn above 250", "sergiy", now=at(9)
        )

        assert session.agent_id == COOKING
        assert session.objective == "a rub that doesn't burn above 250"
        assert session.operator == "sergiy"
        assert session.state == "open"
        assert session.created_at == session.updated_at == at(9)
        assert session.closed_at is None
        assert session.ended_at is None

        row = await store.get(session.session_id)
        assert row == session


@pytest.mark.asyncio
async def test_agent_id_and_objective_never_change_after_creation_s1(tmp_path: Path) -> None:
    """S-1: "no store method mutates either afterward (``rename`` changes ``name``/``title`` only)"."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "the original objective", "sergiy", now=at(9))

        renamed = await store.rename(session.session_id, "a much better name", now=at(10))
        closed = await store.close(session.session_id, now=at(11))
        ended = await store.end(session.session_id, now=at(12))

        for row in (renamed, closed, ended):
            assert row.agent_id == COOKING
            assert row.objective == "the original objective"


@pytest.mark.asyncio
async def test_an_objective_less_session_is_still_recorded_s1(tmp_path: Path) -> None:
    """§2.2: "A session usually has an objective and some have none... a standing conversation"."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, None, "sergiy", name="standing chat", now=at(9))

        assert session.objective is None
        assert session.name == "standing-chat"


@pytest.mark.asyncio
async def test_an_unnamed_session_gets_a_deterministic_slug_from_the_objective_s5(
    tmp_path: Path,
) -> None:
    """S-5: harness code derives the name from the objective, the same way every time."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "Trading · Trend Signal", "sergiy", now=at(9))

        assert session.name == "trading-trend-signal"
        assert session.file_path == "sessions/trading-trend-signal.md"


@pytest.mark.asyncio
async def test_two_sessions_on_the_same_objective_disambiguate_by_number_s5(
    tmp_path: Path,
) -> None:
    """S-16/S-27: the name is the path, and nothing overwrites a file, sealed or open."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        first = await store.create(COOKING, "grilling temperatures", "sergiy", now=at(9))
        second = await store.create(COOKING, "grilling temperatures", "sergiy", now=at(9, 5))

        assert first.name == "grilling-temperatures"
        assert second.name == "grilling-temperatures-2"
        assert first.file_path != second.file_path


@pytest.mark.asyncio
async def test_a_name_with_no_mappable_characters_falls_back_to_the_default_stem_s5(
    tmp_path: Path,
) -> None:
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "???", "sergiy", now=at(9))

        assert session.name == "session"


@pytest.mark.asyncio
async def test_a_caller_supplied_name_is_slugged_too(tmp_path: Path) -> None:
    """A human-typed name still has to be a valid path segment (S-16's path-is-the-name)."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(
            COOKING, "an objective", "sergiy", name="Trading · Trend Signal", now=at(9)
        )

        assert session.name == "trading-trend-signal"


@pytest.mark.asyncio
async def test_minted_session_ids_are_unique() -> None:
    minted = [mint_session_id() for _ in range(200)]
    assert len(set(minted)) == 200


# --------------------------------------------------------------------------------------
# § no session kind, and no fork or copy — S-3, S-12
# --------------------------------------------------------------------------------------
# Fix round 2, finding 2: S-3 and S-12 (both error severity) were cited by no test anywhere in the
# tree. Both are structural-absence checks in the S-33 pattern
# (tests/service/test_session_file.py): a rule this file enforces by *not building a mechanism*
# is pinned by asserting the mechanism stays unbuilt, so a later addition has to move this line on
# purpose rather than drift the property in silently.


def test_session_carries_no_kind_or_type_field_s3() -> None:
    """ "The PKB holds one shape for all of them... a session that searches nothing is an ordinary
    session" (S-3, quoted). ``Session`` exposes no field that could distinguish a "search session"
    from any other — every session is the identical row shape regardless of what it does."""
    field_names = {field.name for field in dataclasses.fields(Session)}
    assert "kind" not in field_names
    assert "type" not in field_names


def test_session_store_names_no_fork_copy_or_merge_method_s12() -> None:
    """ "Nothing copies one session's file into another" (S-12, quoted) — the store half of the
    same guard ``test_session_file_writer_names_no_fork_copy_or_merge_method_s12`` pins for the
    writer (``tests/service/test_session_file.py``): no method on ``SessionStore`` forks, copies,
    or merges one session's row into another."""
    public_methods = [
        name
        for name, _ in inspect.getmembers(SessionStore, inspect.isfunction)
        if not name.startswith("_")
    ]
    for banned in ("fork", "copy", "merge", "clone"):
        offenders = [name for name in public_methods if banned in name]
        assert offenders == [], offenders


# --------------------------------------------------------------------------------------
# § the state machine — S-20, S-22, S-24 (P3)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_state_machine_walks_open_closed_ended_in_order(tmp_path: Path) -> None:
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "an objective", "sergiy", now=at(9))
        assert session.state == "open"

        closed = await store.close(session.session_id, now=at(10))
        assert closed.state == "closed"
        assert closed.closed_at == at(10)
        assert closed.updated_at == at(10)
        assert closed.ended_at is None

        ended = await store.end(session.session_id, now=at(11))
        assert ended.state == "ended"
        assert ended.ended_at == at(11)
        assert ended.updated_at == at(11)
        # closed_at is untouched by end() — it still records when /close happened.
        assert ended.closed_at == at(10)


@pytest.mark.asyncio
async def test_re_closing_an_already_closed_session_is_a_named_error_s20(tmp_path: Path) -> None:
    """S-20: "idempotent error on re-close" — the same refusal, every time, not a silent no-op."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "an objective", "sergiy", now=at(9))
        await store.close(session.session_id, now=at(10))

        with pytest.raises(IllegalSessionTransitionError):
            await store.close(session.session_id, now=at(11))
        with pytest.raises(IllegalSessionTransitionError):
            await store.close(session.session_id, now=at(12))

        # The failed re-close attempts changed nothing.
        row = await store.get(session.session_id)
        assert row is not None
        assert (row.state, row.closed_at, row.updated_at) == ("closed", at(10), at(10))


@pytest.mark.asyncio
async def test_closing_an_ended_session_is_also_refused_s20(tmp_path: Path) -> None:
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "an objective", "sergiy", now=at(9))
        await store.close(session.session_id, now=at(10))
        await store.end(session.session_id, now=at(11))

        with pytest.raises(IllegalSessionTransitionError):
            await store.close(session.session_id, now=at(12))


@pytest.mark.asyncio
async def test_ending_an_open_session_is_refused_s22(tmp_path: Path) -> None:
    """S-22: "``/end``... only succeeds from ``state='closed'``" — never straight from ``open``."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "an objective", "sergiy", now=at(9))

        with pytest.raises(IllegalSessionTransitionError):
            await store.end(session.session_id, now=at(10))

        row = await store.get(session.session_id)
        assert row is not None and row.state == "open"


@pytest.mark.asyncio
async def test_ending_an_already_ended_session_is_refused_s24(tmp_path: Path) -> None:
    """S-24/P3: "a sealed file is never reopened" — end() cannot run twice."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "an objective", "sergiy", now=at(9))
        await store.close(session.session_id, now=at(10))
        await store.end(session.session_id, now=at(11))

        with pytest.raises(IllegalSessionTransitionError):
            await store.end(session.session_id, now=at(12))


@pytest.mark.asyncio
async def test_state_transitions_on_an_unknown_session_raise_unknown_session(
    tmp_path: Path,
) -> None:
    async with session_store(tmp_path / "pkb.sqlite") as store:
        assert await store.get("no-such-session") is None
        with pytest.raises(UnknownSessionError):
            await store.close("no-such-session", now=at(9))
        with pytest.raises(UnknownSessionError):
            await store.end("no-such-session", now=at(9))
        with pytest.raises(UnknownSessionError):
            await store.rename("no-such-session", "a new name", now=at(9))


# --------------------------------------------------------------------------------------
# § rename — S-16
# --------------------------------------------------------------------------------------
# S-19 is not this store's to test either (fix round 2, finding 2, mirrors S-2's note above): this
# module's own docstring records that the Learning agent has no registry entry a bare SessionStore
# can check, so "no file to rename" is refused one layer up, in RuntimeService.rename_session
# (S-19, S-26). Its dedicated test — over a real POST /sessions/{id}/name, asserting status, code
# and detail all carry the reason — is
# tests/server/test_session_routes.py::test_a_rename_on_a_learning_agent_session_is_409_and_says_why_s19
# (fix round 2, finding 1's own new test).


@pytest.mark.asyncio
async def test_rename_updates_name_and_updated_at_only(tmp_path: Path) -> None:
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "an objective", "sergiy", now=at(9))

        renamed = await store.rename(session.session_id, "a much better name", now=at(10))

        assert renamed.name == "a-much-better-name"
        assert renamed.updated_at == at(10)
        assert renamed.created_at == at(9)
        assert renamed.session_id == session.session_id
        assert renamed.state == "open"


@pytest.mark.asyncio
async def test_rename_is_legal_from_open_and_from_closed_s16(tmp_path: Path) -> None:
    """§2.6: "``/name`` renames the session at any point before ``/end`` seals the file"."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "an objective", "sergiy", now=at(9))
        await store.close(session.session_id, now=at(10))

        renamed = await store.rename(session.session_id, "renamed after close", now=at(11))

        assert renamed.name == "renamed-after-close"
        assert renamed.state == "closed"


@pytest.mark.asyncio
async def test_rename_is_refused_once_the_session_is_sealed_s16(tmp_path: Path) -> None:
    """S-16: "it refuses the rename once ``/end`` has sealed this file"."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "an objective", "sergiy", now=at(9))
        await store.close(session.session_id, now=at(10))
        await store.end(session.session_id, now=at(11))

        with pytest.raises(IllegalSessionTransitionError):
            await store.rename(session.session_id, "too late now", now=at(12))

        row = await store.get(session.session_id)
        assert row is not None and row.name == session.name


@pytest.mark.asyncio
async def test_renaming_to_a_name_already_taken_is_refused_s16(tmp_path: Path) -> None:
    """S-16: "harness code refuses a name any session file already holds" — DESIGN.md's other
    "refuses" in the same sentence as the seal (S-16, S-24). Unlike ``create()``'s auto-derived or
    freshly-minted name, which still disambiguates by number (module docstring), the operator
    explicitly typed this name in a rename, so the call is refused rather than silently redirected
    to a different path."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        holder = await store.create(COOKING, "grilling temperatures", "sergiy", now=at(9))
        other = await store.create(COOKING, "something else entirely", "sergiy", now=at(9, 5))

        with pytest.raises(SessionNameTakenError):
            await store.rename(other.session_id, holder.name, now=at(10))

        # The failed rename changed nothing: neither row moved.
        holder_row = await store.get(holder.session_id)
        other_row = await store.get(other.session_id)
        assert holder_row is not None and holder_row.name == holder.name
        assert other_row is not None and other_row.name == other.name


@pytest.mark.asyncio
async def test_renaming_a_session_to_its_own_current_name_is_a_no_op_collision(
    tmp_path: Path,
) -> None:
    """The collision check must exclude the session's own row, or renaming to the same slug twice
    would raise ``SessionNameTakenError`` against itself instead of leaving it alone."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "an objective", "sergiy", now=at(9))

        renamed = await store.rename(session.session_id, session.name, now=at(10))

        assert renamed.name == session.name


# --------------------------------------------------------------------------------------
# § the queue — S-23, S-25 (P4)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_queue_is_exactly_the_closed_set_s25(tmp_path: Path) -> None:
    """P4: "the queue IS the set of sessions with ``state='closed'``... no second structure"."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        still_open = await store.create(COOKING, "still open", "sergiy", now=at(9))
        closed = await store.create(COOKING, "closed", "sergiy", now=at(9, 5))
        sealed = await store.create(COOKING, "sealed", "sergiy", now=at(9, 10))

        await store.close(closed.session_id, now=at(10))
        await store.close(sealed.session_id, now=at(10, 30))
        await store.end(sealed.session_id, now=at(11))

        queued = await store.queue()

        assert [row.session_id for row in queued] == [closed.session_id]
        assert still_open.session_id not in [row.session_id for row in queued]
        assert sealed.session_id not in [row.session_id for row in queued]


@pytest.mark.asyncio
async def test_the_queue_orders_by_closed_at_not_by_creation_or_agent_s25(
    tmp_path: Path,
) -> None:
    async with session_store(tmp_path / "pkb.sqlite") as store:
        # Created in one order, closed in the reverse — the queue must follow closed_at.
        first_created = await store.create(COOKING, "first created", "sergiy", now=at(8))
        second_created = await store.create(LIBRARIAN, "second created", "sergiy", now=at(8, 30))

        await store.close(second_created.session_id, now=at(9))
        await store.close(first_created.session_id, now=at(10))

        queued = await store.queue()

        assert [row.session_id for row in queued] == [
            second_created.session_id,
            first_created.session_id,
        ]


@pytest.mark.asyncio
async def test_a_session_that_produced_nothing_still_enters_the_queue_s20(
    tmp_path: Path,
) -> None:
    """S-20: "``/close``... every time and whatever the session produced" — no filing bar here."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "nothing came of this", "sergiy", now=at(9))

        await store.close(session.session_id, now=at(10))

        assert [row.session_id for row in await store.queue()] == [session.session_id]


@pytest.mark.asyncio
async def test_ending_a_session_removes_it_from_the_queue_by_the_state_write_alone_s25(
    tmp_path: Path,
) -> None:
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "an objective", "sergiy", now=at(9))
        await store.close(session.session_id, now=at(10))
        assert [row.session_id for row in await store.queue()] == [session.session_id]

        await store.end(session.session_id, now=at(11))

        assert await store.queue() == []


# --------------------------------------------------------------------------------------
# § get / list
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_none_for_an_unknown_session(tmp_path: Path) -> None:
    async with session_store(tmp_path / "pkb.sqlite") as store:
        assert await store.get("no-such-session") is None


@pytest.mark.asyncio
async def test_list_filters_by_agent_exactly_and_by_state(tmp_path: Path) -> None:
    async with session_store(tmp_path / "pkb.sqlite") as store:
        cooking_open = await store.create(COOKING, "cooking, open", "sergiy", now=at(9))
        cooking_closed = await store.create(COOKING, "cooking, closed", "sergiy", now=at(9, 5))
        librarian_open = await store.create(LIBRARIAN, "librarian, open", "sergiy", now=at(9, 10))
        await store.close(cooking_closed.session_id, now=at(10))

        assert {row.session_id for row in await store.list(COOKING)} == {
            cooking_open.session_id,
            cooking_closed.session_id,
        }
        assert [row.session_id for row in await store.list(LIBRARIAN)] == [
            librarian_open.session_id
        ]
        assert {row.session_id for row in await store.list(state="open")} == {
            cooking_open.session_id,
            librarian_open.session_id,
        }
        assert [row.session_id for row in await store.list(COOKING, state="closed")] == [
            cooking_closed.session_id
        ]
        assert len(await store.list()) == 3


# --------------------------------------------------------------------------------------
# § attached channels — S-4, S-6, S-7, S-17 (Task 7)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_channels_attach_to_one_session_and_both_appear_in_channels_s6(
    tmp_path: Path,
) -> None:
    """S-6: "several channels may hold one session at once... every attached channel sees the
    same thread"."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "a rub that doesn't burn", "sergiy", now=at(9))

        await store.attach(session.session_id, "telegram:1:0")
        await store.attach(session.session_id, "tui:client-a")

        assert set(await store.channels(session.session_id)) == {"telegram:1:0", "tui:client-a"}


@pytest.mark.asyncio
async def test_attach_is_idempotent(tmp_path: Path) -> None:
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "an objective", "sergiy", now=at(9))

        await store.attach(session.session_id, "telegram:1:0")
        await store.attach(session.session_id, "telegram:1:0")

        assert await store.channels(session.session_id) == ["telegram:1:0"]


@pytest.mark.asyncio
async def test_a_channel_holds_one_session_at_a_time_s7(tmp_path: Path) -> None:
    """S-7: "A channel holds one session at a time" — attaching moves it, per ``/threads``."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        first = await store.create(COOKING, "first", "sergiy", now=at(9))
        second = await store.create(LIBRARIAN, "second", "sergiy", now=at(9, 5))

        await store.attach(first.session_id, "telegram:1:0")
        await store.attach(second.session_id, "telegram:1:0")

        assert await store.channels(first.session_id) == []
        assert await store.channels(second.session_id) == ["telegram:1:0"]


@pytest.mark.asyncio
async def test_detach_removes_one_channel_and_leaves_the_other(tmp_path: Path) -> None:
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "an objective", "sergiy", now=at(9))
        await store.attach(session.session_id, "telegram:1:0")
        await store.attach(session.session_id, "tui:client-a")

        await store.detach(session.session_id, "telegram:1:0")

        assert await store.channels(session.session_id) == ["tui:client-a"]


@pytest.mark.asyncio
async def test_detach_of_an_unknown_ref_is_not_an_error(tmp_path: Path) -> None:
    """Mirrors the store's own error discipline: an unmapped detach is a no-op, not a raise
    (``CLAUDE.md``, "Findings, not exceptions, for content defects")."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "an objective", "sergiy", now=at(9))

        await store.detach(session.session_id, "telegram:99:0")  # never attached — no raise
        await store.detach("no-such-session", "telegram:1:0")  # unknown session — no raise

        assert await store.channels(session.session_id) == []


@pytest.mark.asyncio
async def test_channels_of_a_session_with_none_attached_is_empty(tmp_path: Path) -> None:
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session = await store.create(COOKING, "an objective", "sergiy", now=at(9))

        assert await store.channels(session.session_id) == []


# --------------------------------------------------------------------------------------
# § the schema
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_table_has_exactly_the_stored_columns(tmp_path: Path) -> None:
    """``file_path`` is absent on purpose — it is a pure function of ``name`` (see the module and
    class docstrings), so a stored copy would be a second answer that could disagree with it."""
    db = tmp_path / "pkb.sqlite"
    async with session_store(db):
        pass

    assert table_columns(db, TABLE) == [
        ("session_id", "TEXT", 0, 1),
        ("agent_id", "TEXT", 1, 0),
        ("objective", "TEXT", 0, 0),
        ("name", "TEXT", 1, 0),
        ("operator", "TEXT", 1, 0),
        ("state", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
        ("closed_at", "TEXT", 0, 0),
        ("ended_at", "TEXT", 0, 0),
    ]
    assert {field.name for field in dataclasses.fields(Session)} == {
        "session_id",
        "agent_id",
        "objective",
        "name",
        "operator",
        "state",
        "created_at",
        "updated_at",
        "closed_at",
        "ended_at",
    }


@pytest.mark.asyncio
async def test_setup_is_idempotent_and_records_its_own_schema_version(tmp_path: Path) -> None:
    db = tmp_path / "pkb.sqlite"
    async with session_store(db) as store:
        await store.setup()  # a second call over an existing file: additive, never a rewrite

        with sqlite3.connect(db) as raw:
            versions = [row[0] for row in raw.execute(f"SELECT version FROM {MIGRATIONS_TABLE}")]
        assert versions == [1]


@pytest.mark.asyncio
async def test_a_duplicate_session_id_is_refused_by_the_table(tmp_path: Path) -> None:
    """The PRIMARY KEY backstops the store's own disambiguation loop (defense in depth)."""
    async with session_store(tmp_path / "pkb.sqlite") as store:
        session_id = mint_session_id()
        await store._connection.execute(
            "INSERT INTO sessions "
            "(session_id, agent_id, objective, name, operator, state, created_at, updated_at, "
            "closed_at, ended_at) VALUES (?,?,?,?,?,?,?,?,NULL,NULL)",
            (
                session_id,
                COOKING,
                "first",
                "first-name",
                "sergiy",
                "open",
                "2026-08-14T09:00:00Z",
                "2026-08-14T09:00:00Z",
            ),
        )
        await store._connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            await store._connection.execute(
                "INSERT INTO sessions "
                "(session_id, agent_id, objective, name, operator, state, created_at, updated_at, "
                "closed_at, ended_at) VALUES (?,?,?,?,?,?,?,?,NULL,NULL)",
                (
                    session_id,
                    COOKING,
                    "second",
                    "second-name",
                    "sergiy",
                    "open",
                    "2026-08-14T09:05:00Z",
                    "2026-08-14T09:05:00Z",
                ),
            )
