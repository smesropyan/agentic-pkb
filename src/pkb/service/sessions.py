"""The ``sessions`` table — Layer 3's durable store for the one state machine (S-1 … S-39).

``DESIGN.md`` §2 replaces the channel-is-identity thread model with a session: "durable, held on
one agent for one objective, for as long as that work lasts" (§2.1), "a file in the root
``sessions/`` folder" that "holds its name itself" (§2.2). This module is the row that durability
rests on — one file, but a file needs an index to be found, listed and transitioned, the way
``threads.py``'s row was never the authority on a checkpoint's existence but was the thing every
channel actually queried (ST-12). ``docs/superpowers/specs/2026-08-14-sessions-S-rules.md`` is the
contract; every rule this store enforces is cited by id in the method that enforces it, and every
test that exercises one cites it back (``CLAUDE.md``, "Conventions that survive").

**What this module builds, task by task (``docs/superpowers/plans/2026-08-14-phase2-sessions.md``,
Task 3):** the row shape, the state machine (S-20, S-22, S-24/P3, S-25/P4) and deterministic naming
(S-5). What it deliberately does **not** build, because nothing yet consumes it:

* **S-9's agent-id validation** ("refuses an ``agent_id`` that resolves to none of {the Librarian, a
  topic expert the registry knows, the Learning agent}"). ``ThreadStore.create`` never validated an
  agent id either — ``RuntimeService.create_thread`` does, one layer up, against the live registry
  (``src/pkb/service/runtime.py``, ``_catalog_ids()``), *before* the row is inserted. The Learning
  agent does not exist yet (Phase 4), so there is no registry entry to check against even if this
  store reached for one. The same split lands here: a future ``RuntimeService.create_session`` (Task
  5, alongside the routes that call it) validates before calling :meth:`SessionStore.create`, which
  stays agent-agnostic the way ``ThreadStore.create`` always was.
* **S-19's "no file to rename" refusal** for an analysis session opened on the Learning agent. Same
  reason: this store cannot yet tell an ordinary session's ``agent_id`` from the Learning agent's,
  because the Learning agent is not a registered id anywhere on disk today. Task 4/5 owns this once
  ``SessionFileWriter`` and the routes exist to ask the question.
* **The HTTP/wire error-code rows** (``pkb.contracts.ERROR_CODES``, ``pkb.server.errors.ERROR_STATUS``).
  The two exceptions below subclass :class:`~pkb.contracts.PkbAgentError` — "the service's error
  type", the family every ``pkb.service`` module already raises and every transport already knows how
  to translate: an *unmapped* subclass is a 500 by construction, never a 200
  (``src/pkb/server/errors.py``). Giving them their own wire code is Task 5's job, when a route first
  needs to turn one into a 404/409 a client branches on.

Both deferrals are the plan's own escape hatch: "if runtime.py wiring is better deferred to Task 5's
route re-home, defer it and say so ... rather than inventing plumbing nothing consumes yet." Neither
``src/pkb/service/runtime.py`` nor ``src/pkb/service/__init__.py`` is touched by this module for the
same reason — nothing there calls a session method yet, and the ``PkbService`` Protocol gaining
session methods is Task 5's "Routes and commands", not this one.

**A disclosed asymmetry: ``create`` disambiguates a name collision, ``rename`` refuses one.** S-16
binds *rename* in so many words — "harness code refuses a name any session file already holds" — and
DESIGN.md §2 is silent on what a fresh ``create()`` should do when the name it would mint collides
with an existing one. ``create`` keeps disambiguating by number (``-2``, ``-3``, …), the
``pkb.sources._staging_dir`` trade-off for two sources whose names slugify alike; only ``rename``
raises :class:`SessionNameTakenError`, because there the operator named a *specific* existing path and
S-16 says that call is refused, not silently redirected to a different one. See :meth:`create` and
:meth:`rename` for the rule cites in place.

**SQLite discipline, copied from ``threads.py`` (``ST-2`` … ``ST-4``, ``ST-8``, ``ST-9``):** one
``aiosqlite`` connection Layer 3 already owns, opened WAL and autocommit before this module ever
sees it; every method is one short statement, because holding a write transaction open across an
``await`` starves the checkpointer's own writer. Its own migrations table
(``pkb_session_migrations``) rather than reusing ``threads.py``'s ``pkb_service_migrations``: that
table is imported by name in a still-active test
(``tests/service/test_store.py::test_no_writer_of_this_file_claims_user_version_st8``, asserting the
single row ``[1]``) and ``threads.py`` itself dies whole in Task 10 — a new table sharing its name
would either break that assertion the moment both stores are wired into one ``setup()``, or tie this
module's schema version to a module built to be deleted. A distinct table is zero coupling either way.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only
    # `aiosqlite` names one type here (`SessionStore.__init__`'s `connection` parameter) and is
    # otherwise unused at runtime, so it stays out of `sys.modules` for anyone importing this
    # module for its dataclasses alone — `pkb/service/__init__.py` does exactly that, to expose
    # `Session`/`SessionList`/`SessionState` on the harness-free `PkbService` Protocol, and the TUI
    # imports `pkb.service` for its dataclasses without ever wanting a database driver (DC-21).
    import aiosqlite

from pkb.contracts import PkbAgentError
from pkb.core.paths import slugify

__all__ = [
    "CHANNELS_TABLE",
    "MIGRATIONS_TABLE",
    "TABLE",
    "ChannelRefs",
    "IllegalSessionTransitionError",
    "Session",
    "SessionList",
    "SessionNameTakenError",
    "SessionState",
    "SessionStore",
    "UnknownSessionError",
    "mint_session_id",
]

TABLE: Final = "sessions"
"""Layer 3 owns this name and anything prefixed ``pkb_`` — and nothing else (mirrors ST-7)."""

CHANNELS_TABLE: Final = "session_channels"
"""The attached-channels registry (S-4, S-6, S-7, S-13 … S-17; Task 7).

Unprefixed, alongside :data:`TABLE`, because it is core session-domain data rather than incidental
bookkeeping (mirrors this module's own choice for ``sessions`` over a ``pkb_``-prefixed name) — a
second table in the same module, added additively (``CREATE TABLE IF NOT EXISTS``, no
``_SCHEMA_VERSION`` bump: nothing here needs a migration guard the way an ``ALTER TABLE`` does, so
there is nothing for a version ladder to record).

``channel_ref`` is an **opaque string the transport mints** — this module never parses one. The
formats in use today: ``"telegram:<chat_id>:<topic_id>"`` (``pkb.server.telegram.channel_ref``,
matching :class:`~pkb.server.telegram.Channel`'s own two fields) and ``"tui:<client-id>"`` — Task 7's
brief names both. A session's own agnosticism about the transport is the point (S-13: "there is one
way in, the API"): this table would be identical if a third transport arrived tomorrow.

``channel_ref`` is the table's own primary key, not ``(session_id, channel_ref)`` — S-7, quoted: "A
channel holds one session at a time." :meth:`SessionStore.attach` is therefore last-write-wins on the
channel, mirroring ``pkb.service.telegram.SqliteTelegramStore.bind``'s own
``ON CONFLICT DO UPDATE``: attaching a channel already holding a different session **moves** it
(``/threads``'s own effect, S-14) rather than raising or attaching twice, and the old session's
:meth:`~SessionStore.channels` reflects the move with no separate detach call needed.
"""

MIGRATIONS_TABLE: Final = "pkb_session_migrations"
"""This module's own version table — deliberately not ``threads.py``'s ``pkb_service_migrations``.

See the module docstring: that table's row count is asserted by a still-active test and the module
that owns it is deleted whole in Task 10. Not ``PRAGMA user_version`` either, for the reason
``threads.py`` gives: it is one counter per *file*, shared by every independent writer of
``pkb.sqlite`` (the checkpointer, the langgraph store, Layer 2's scan queue, and now two Layer-3
tables), and a per-table concern has no business claiming a global.
"""

_SCHEMA: Final = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    session_id  TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    objective   TEXT,
    name        TEXT NOT NULL UNIQUE,
    operator    TEXT NOT NULL,
    state       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    closed_at   TEXT,
    ended_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_{TABLE}_agent ON {TABLE}(agent_id);
CREATE INDEX IF NOT EXISTS idx_{TABLE}_state ON {TABLE}(state);
CREATE TABLE IF NOT EXISTS {CHANNELS_TABLE} (
    channel_ref  TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    attached_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_{CHANNELS_TABLE}_session ON {CHANNELS_TABLE}(session_id);
CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} (version INTEGER PRIMARY KEY);
"""

_SCHEMA_VERSION: Final = 1

_COLUMNS: Final = (
    "session_id, agent_id, objective, name, operator, state, created_at, updated_at, "
    "closed_at, ended_at"
)

DEFAULT_SESSION_NAME_STEM: Final = "session"
"""What an objective-less, name-less session slugs to before disambiguation (S-5).

Mirrors ``pkb.sources.slug_for``'s ``"source"`` fallback for an origin with no mappable characters:
:func:`~pkb.core.paths.slugify` can return ``""``, and a session's name is also its file's path
(S-4, S-16), so an empty segment is never an acceptable result.
"""

_NAME_ATTEMPTS: Final = 100
"""Disambiguation ceiling, mirroring ``pkb.sources._staging_dir``'s own bound."""

SessionState = Literal["open", "closed", "ended"]
"""The state machine's three values (S-20, S-22, S-24/P3). Open → closed → ended, and nothing else:
no store method can move a session backward or sideways (S-1 — ``agent_id`` and ``objective`` are
fixed at creation and no transition here touches either)."""


class UnknownSessionError(PkbAgentError):
    """No such session — the session-store analogue of ``UnknownThreadError``.

    A distinct type rather than reusing ``UnknownThreadError``: a session is not a thread (DESIGN.md
    §2 replaces the thread model outright), and a 404 that says "no such thread" for a session lookup
    would be a wrong answer wearing a right-shaped exception.
    """


class IllegalSessionTransitionError(PkbAgentError):
    """A call the state machine refuses: re-close, end-from-open, or any write past the seal.

    Covers S-20 (``close`` only from ``open``), S-22 (``end`` only from ``closed``) and S-24/P3 (a
    sealed — ``state='ended'`` — session refuses every further write, checked here at the store
    rather than by parsing the file, per P3's own ruling). One type for all three: a caller branching
    on "why was this refused" reads the message, which names the session's current state and the
    transition that was attempted, the way ``ThreadBusyError``'s and ``ApprovalPendingError``'s
    messages already do.
    """


class SessionNameTakenError(PkbAgentError):
    """``rename`` refused: the target name already belongs to another session (S-16).

    A distinct type rather than a third case of :class:`IllegalSessionTransitionError`: a name
    collision is not a state-machine violation — it can happen to a session in ``open`` or
    ``closed`` state alike — and Task 5 maps the two to different HTTP statuses (404/409-shaped for
    the state machine, 409/422-shaped for a name fight over one path). S-16 quotes DESIGN.md
    verbatim: "harness code refuses a name any session file already holds, because the path is the
    name and two sessions cannot answer to one." See :meth:`SessionStore.rename`'s docstring for why
    this refuses where :meth:`SessionStore.create` still disambiguates.
    """


def mint_session_id() -> str:
    """A fresh session id — a bare ``uuid4``, mirroring ``threads.py``'s ``mint_thread_id``.

    Unlike a thread id, a session id carries no derived-id or maintenance-id namespace to stay clear
    of (S-3: a session has no sub-kind distinguishing a "search session" from any other), so there is
    nothing here for a namespace assertion to protect.
    """
    return str(uuid.uuid4())


@dataclass(frozen=True, slots=True)
class Session:
    """One session, as the store holds it — S-1's durable row: one agent, one objective, for life.

    ``file_path`` is a **computed** property rather than a stored column, mirroring
    ``pkb.service.Thread``'s ``kind``/``parent_thread_id`` (RT-36's anti-duplication rule): S-16
    quotes DESIGN.md's own words, "the path is the name", so a cached path is a second answer to a
    question ``name`` already answers exactly, and the two could only ever disagree by a bug landing
    in one write path and not the other.
    """

    session_id: str
    agent_id: str
    objective: str | None
    name: str
    operator: str
    state: SessionState
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    ended_at: datetime | None = None

    @property
    def file_path(self) -> str:
        """``sessions/<name>.md`` at the KB root (S-26, S-27) — see the class docstring."""
        return f"sessions/{self.name}.md"


SessionList = list[Session]
"""Alias for the builtin, resolved at module scope.

``SessionStore`` below defines a method named ``list`` (the task's own spelling), which shadows the
builtin ``list`` for any annotation written *inside* the class body after that method — mypy resolves
a bare ``list[Session]`` there against the class's own name, not ``builtins.list``. Fixing it up front
here, once, is simpler than routing around the shadow method by method.
"""

ChannelRefs = list[str]
"""Alias for the builtin, for the identical reason :data:`SessionList` is one: :meth:`SessionStore.
channels` (Task 7) is defined after :meth:`SessionStore.list` in the class body, so a bare
``list[str]`` written there would resolve against the method, not ``builtins.list``, too."""


class SessionStore:
    """Short autocommit statements over one ``aiosqlite`` connection Layer 3 owns (mirrors ST-3).

    Not a repository abstraction, for the same reason ``ThreadStore`` is not one: every method is one
    statement (plus, for a name, the disambiguation read-loop below it), so there is nowhere for a
    second one to hide inside an ``await``.
    """

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def setup(self) -> None:
        """Create the table if it is not there, and record the schema version.

        Additive by construction, mirroring ``ThreadStore.setup``: a later version adds columns with
        defaults rather than rewriting rows, so opening a v1 file with a v2 build keeps every row.
        """
        await self._connection.executescript(_SCHEMA)
        await self._connection.execute(
            f"INSERT OR IGNORE INTO {MIGRATIONS_TABLE}(version) VALUES (?)", (_SCHEMA_VERSION,)
        )
        await self._connection.commit()

    # -- writes ----------------------------------------------------------------------

    async def create(
        self,
        agent_id: str,
        objective: str | None,
        operator: str,
        *,
        name: str | None = None,
        now: datetime | None = None,
    ) -> Session:
        """Insert a new, open session (S-1, S-2). Every field but ``name`` lands verbatim.

        ``agent_id`` and ``objective`` are fixed here and touched by no later method (S-1 — no store
        method mutates either after creation; ``rename`` changes ``name``/``updated_at`` only). A
        caller-supplied ``name`` is slugged the same way a derived one is (S-16's path-is-the-name
        discipline applies uniformly, whether the operator typed the name or harness code derived
        it), and either way the result is disambiguated against every name already on file, because
        the name is also the path and nothing overwrites a file, sealed or open (S-27).

        ``name=None`` derives a deterministic slug from the objective (S-5) — the same discipline
        ``pkb.sources.slug_for`` uses for a staged source's directory name: slugify, fall back to a
        fixed stem when nothing mappable survives, disambiguate by number.

        **Disclosed asymmetry with** :meth:`rename`: a collision *here* — whether the name came from
        the objective or the caller typed it — disambiguates by number rather than refusing.
        :meth:`rename` refuses instead (S-16, quoted: "harness code refuses a name any session file
        already holds"), because S-16 binds the *rename* path in so many words and DESIGN.md §2 is
        silent on what a fresh ``create()`` should do about a collision it discovers on the way in —
        there is no existing session's name being taken out from under it yet, only two names racing
        to be minted, and disambiguating the newcomer is the same trade-off
        ``pkb.sources._staging_dir`` already makes for two sources whose names slugify alike.
        """
        stamp = _now(now)
        session_id = mint_session_id()
        base = slugify(name) if name else (slugify(objective) if objective else "")
        unique_name = await self._unique_name(base or DEFAULT_SESSION_NAME_STEM)
        await self._connection.execute(
            f"INSERT INTO {TABLE} ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,NULL,NULL)",
            (session_id, agent_id, objective, unique_name, operator, "open", stamp, stamp),
        )
        await self._connection.commit()
        return Session(
            session_id=session_id,
            agent_id=agent_id,
            objective=objective,
            name=unique_name,
            operator=operator,
            state="open",
            created_at=_parse(stamp),
            updated_at=_parse(stamp),
        )

    async def rename(self, session_id: str, name: str, *, now: datetime | None = None) -> Session:
        """``/name`` (S-16, S-19-adjacent): updates ``name`` and ``updated_at``, nothing else.

        Refused once the session is sealed (``state == 'ended'``): "harness code refuses the rename
        once ``/end`` has sealed this file, because a sealed file is never reopened" (S-16, quoted).
        Legal from both ``open`` and ``closed`` — DESIGN.md §2.6 fixes only the seal as the boundary:
        "``/name`` renames the session at any point before ``/end`` seals the file."

        Refused, too, when the slugged target already names another session — S-16, quoted:
        "harness code refuses a name any session file already holds, because the path is the name
        and two sessions cannot answer to one." This is DESIGN.md's other "refuses" in the same
        sentence as the seal, so it raises :class:`SessionNameTakenError` rather than silently
        disambiguating the way :meth:`create` does for an auto-derived or freshly-minted name (see
        that method's docstring for why the two differ). Renaming a session to the name it already
        holds is a no-op, not a collision with itself.
        """
        row = await self._require(session_id)
        if row.state == "ended":
            raise IllegalSessionTransitionError(
                f"session {session_id!r} is sealed (state='ended'); a sealed session is never "
                f"renamed (S-16, S-24)"
            )
        slugged = slugify(name) or DEFAULT_SESSION_NAME_STEM
        if slugged != row.name:
            cursor = await self._connection.execute(
                f"SELECT 1 FROM {TABLE} WHERE name = ? AND session_id != ?", (slugged, session_id)
            )
            if await cursor.fetchone() is not None:
                raise SessionNameTakenError(
                    f"a session named {slugged!r} already exists; refused rather than "
                    f"disambiguated (S-16)"
                )
        stamp = _now(now)
        await self._connection.execute(
            f"UPDATE {TABLE} SET name = ?, updated_at = ? WHERE session_id = ?",
            (slugged, stamp, session_id),
        )
        await self._connection.commit()
        return await self._require(session_id)

    async def close(self, session_id: str, *, now: datetime | None = None) -> Session:
        """``/close`` (S-20): legal only from ``open``; every other call is a named, repeatable error.

        Sets ``state='closed'`` and ``closed_at`` in the same statement that touches ``updated_at``.
        That state change alone is what enters the session into :meth:`queue` (S-25/P4) — this method
        performs no analysis call and blocks on nothing beyond the write, matching S-20's "that
        analysis is never synchronous with the command."
        """
        row = await self._require(session_id)
        if row.state != "open":
            raise IllegalSessionTransitionError(
                f"session {session_id!r} is {row.state!r}; only an open session can be closed "
                f"(S-20) — re-close is refused, idempotently, rather than silently accepted"
            )
        stamp = _now(now)
        await self._connection.execute(
            f"UPDATE {TABLE} SET state = 'closed', closed_at = ?, updated_at = ? "
            f"WHERE session_id = ?",
            (stamp, stamp, session_id),
        )
        await self._connection.commit()
        return await self._require(session_id)

    async def end(self, session_id: str, *, now: datetime | None = None) -> Session:
        """``/end`` (S-22): legal only from ``closed`` — never from ``open``, never twice.

        Sets ``state='ended'`` and ``ended_at``. Per P3/S-24, sealing lives here at the store: the
        writer module (Task 4) refuses every later write to this session's file by *querying this
        store's state*, never by parsing the file for a sentinel — this call is the one place
        ``state='ended'`` is ever written.
        """
        row = await self._require(session_id)
        if row.state != "closed":
            raise IllegalSessionTransitionError(
                f"session {session_id!r} is {row.state!r}; only a closed session can be ended "
                f"(S-22) — end-from-open and end-twice are both refused"
            )
        stamp = _now(now)
        await self._connection.execute(
            f"UPDATE {TABLE} SET state = 'ended', ended_at = ?, updated_at = ? WHERE session_id = ?",
            (stamp, stamp, session_id),
        )
        await self._connection.commit()
        return await self._require(session_id)

    # -- reads -----------------------------------------------------------------------

    async def get(self, session_id: str) -> Session | None:
        cursor = await self._connection.execute(
            f"SELECT {_COLUMNS} FROM {TABLE} WHERE session_id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        return _row_to_session(row) if row else None

    async def list(
        self, agent_id: str | None = None, *, state: SessionState | None = None
    ) -> SessionList:
        """Every session, optionally filtered by agent (exact match) and/or state.

        Ordered by ``created_at, session_id`` — stable and deterministic, unlike the queue's own
        ordering (:meth:`queue`, S-25/P4), which is ``closed_at``'s alone to fix.
        """
        clauses: list[str] = []
        params: list[str] = []
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await self._connection.execute(
            f"SELECT {_COLUMNS} FROM {TABLE}{where} ORDER BY created_at, session_id", params
        )
        return [_row_to_session(row) for row in await cursor.fetchall()]

    async def queue(self) -> SessionList:
        """The learning queue (S-23, S-25/P4): exactly the ``state='closed'`` set, by ``closed_at``.

        A view, not a table — P4's ruling: "the queue IS the set of sessions with ``state='closed'``
        ordered by ``closed_at``, no second structure to drift." ``/close`` enters a session by the
        state write alone (:meth:`close`); ``/end`` leaves it the same way (:meth:`end`) — this query
        has no separate enqueue or dequeue call anywhere in this module.
        """
        cursor = await self._connection.execute(
            f"SELECT {_COLUMNS} FROM {TABLE} WHERE state = 'closed' ORDER BY closed_at, session_id"
        )
        return [_row_to_session(row) for row in await cursor.fetchall()]

    # -- attached channels — S-4, S-6, S-7, S-13 … S-17 (Task 7) ------------------------

    async def attach(
        self, session_id: str, channel_ref: str, *, now: datetime | None = None
    ) -> None:
        """Attach ``channel_ref`` to ``session_id`` (S-6, S-14). Idempotent; moves on a re-attach.

        No existence check on ``session_id`` — mirrors this store's own documented split with
        ``RuntimeService`` (module docstring, "What this module builds"): ``SessionStore.create``
        never validates ``agent_id`` either, and the layer above is where a live catalog or a live
        session actually lives. A caller that attaches a channel to a session id nobody minted gets
        a channel entry :meth:`channels` will report and :meth:`get` will never corroborate — visible
        and recoverable, never silently dropped.
        """
        stamp = _now(now)
        await self._connection.execute(
            f"INSERT INTO {CHANNELS_TABLE} (channel_ref, session_id, attached_at) VALUES (?,?,?) "
            f"ON CONFLICT(channel_ref) DO UPDATE SET "
            f"session_id = excluded.session_id, attached_at = excluded.attached_at",
            (channel_ref, session_id, stamp),
        )
        await self._connection.commit()

    async def detach(self, session_id: str, channel_ref: str) -> None:
        """Detach ``channel_ref`` from ``session_id`` (S-17, S-20). Never an error.

        Scoped to *this* session on purpose: a channel a ``/threads`` move already carried to a
        different session is not this session's to detach, and a stale ``/close`` racing that move
        must not reach across and sever a conversation it no longer owns. Both "never attached" and
        "attached, but to someone else" are silent no-ops — mirrors the store's own error discipline
        (``CLAUDE.md``, "Findings, not exceptions, for content defects": this is bookkeeping, not
        content, and the caller asked for an end state that already holds).
        """
        await self._connection.execute(
            f"DELETE FROM {CHANNELS_TABLE} WHERE channel_ref = ? AND session_id = ?",
            (channel_ref, session_id),
        )
        await self._connection.commit()

    async def channels(self, session_id: str) -> ChannelRefs:
        """Every channel attached to ``session_id`` (S-6), ordered by attachment then ref, for a
        deterministic fan-out (:meth:`RuntimeService.rename_session`'s retitle loop, S-16)."""
        cursor = await self._connection.execute(
            f"SELECT channel_ref FROM {CHANNELS_TABLE} WHERE session_id = ? "
            f"ORDER BY attached_at, channel_ref",
            (session_id,),
        )
        return [str(row[0]) for row in await cursor.fetchall()]

    # -- internals ---------------------------------------------------------------------

    async def _require(self, session_id: str) -> Session:
        row = await self.get(session_id)
        if row is None:
            raise UnknownSessionError(f"no session {session_id!r}")
        return row

    async def _unique_name(self, base: str) -> str:
        """``base``, or ``base-2``, ``base-3``, … — the first not already on file (mirrors S-27).

        :meth:`create`'s own disambiguation only — every row is fresh, so there is no "own row" to
        exclude the way a rename would need. :meth:`rename` does not call this: S-16 has it refuse a
        collision instead (:class:`SessionNameTakenError`), never disambiguate one.
        """
        for attempt in range(1, _NAME_ATTEMPTS + 1):
            candidate = base if attempt == 1 else f"{base}-{attempt}"
            cursor = await self._connection.execute(
                f"SELECT 1 FROM {TABLE} WHERE name = ?", (candidate,)
            )
            if await cursor.fetchone() is None:
                return candidate
        raise RuntimeError(f"too many sessions named {base!r} in {TABLE}")


# --------------------------------------------------------------------------------------
# Row mapping
# --------------------------------------------------------------------------------------


def _row_to_session(row: Sequence[object]) -> Session:
    return Session(
        session_id=str(row[0]),
        agent_id=str(row[1]),
        objective=str(row[2]) if row[2] is not None else None,
        name=str(row[3]),
        operator=str(row[4]),
        state=str(row[5]),  # type: ignore[arg-type]
        created_at=_parse(str(row[6])),
        updated_at=_parse(str(row[7])),
        closed_at=_parse(str(row[8])) if row[8] is not None else None,
        ended_at=_parse(str(row[9])) if row[9] is not None else None,
    )


def _now(now: datetime | None) -> str:
    """ISO-8601 UTC with a trailing ``Z`` — identical to ``threads.py``'s own (§5.1)."""
    moment = now or datetime.now(UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
