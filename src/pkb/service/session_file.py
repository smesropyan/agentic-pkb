"""The session file: one file, whole life, written by harness code (``DESIGN.md`` §2.7).

``DESIGN.md`` §2 replaces the channel-is-identity thread model with a session — "durable, held on
one agent for one objective, for as long as that work lasts" (§2.1) — and fixes that it "keeps one
file... for its whole life" (§2.7): the objective and the experts, the running record, the
synthesis, and the distillation, in that order, present even when a section holds nothing (S-31).
No model ever holds a tool that writes ``sessions/**`` — this module is the one write surface a
Librarian session and a Topic-Expert session both use to reach it (S-11), the way
``pkb.core.scaffold`` is the one write surface for a topic's placeholder files. Every write goes
through :func:`pkb.core.validation.validate_content` and refuses on an error finding rather than
landing an invalid file: either the write is computed and validated purely in memory before a byte
touches disk (``create``, ``append_record``, ``add_expert_tag``, the marker methods,
``write_synthesis``), or the target path never receives the new bytes at all
(``rename``'s validate-before-move). The file on disk is therefore always either the old valid
state or the new one — never a half-written one.

**The state machine lives in** :mod:`pkb.service.sessions`; **this module never reads it, only the**
:class:`~pkb.service.sessions.Session` **row a caller hands it.** P3/S-24 rules the seal: "the
writer module refuses every write to a sealed file by checking the store, never by parsing the
file." Concretely, every mutating method but :meth:`SessionFileWriter.mark_ended` itself refuses
when ``session.state == "ended"`` (:func:`_refuse_if_sealed`); ``mark_ended`` performs no such
check because it is the sealing act, called exactly once with the freshly-``ended`` row
:meth:`~pkb.service.sessions.SessionStore.end` returns — and the store's own state machine (S-22)
already refuses a second ``end()`` call, so a second ``mark_ended`` call has no legal row to be
built from. The seal's on-disk shape is P3's own: a literal ``## Ended`` heading, appended after
everything else (S-24).

**Body structure, fixed by** :meth:`create` **and never renumbered:** the objective (a paragraph,
no heading of its own — a session with none states so, per §2.2's "the file's first section records
that the operator stated no objective") followed by ``## Experts`` (a bullet naming the founding
agent), then the three fixed sections ``## Record``, ``## Synthesis`` and ``## Distillation`` — four
``##`` headings in total (S-31). :meth:`append_record` inserts new bytes at the end of ``## Record``
content, immediately before ``## Synthesis`` (the ordinary turn-by-turn narrative, S-28); the three
command markers (``mark_closed``, ``rename``, ``mark_ended``) instead append a *new*, standalone
``##`` heading at the true end of whatever the file currently holds (S-29) — never inside ``##
Record`` — because the fixed four-section skeleton is a structural contract :meth:`write_synthesis`
depends on (it locates ``## Synthesis`` by searching for the literal heading text, then replaces
everything up to the next ``## Distillation``), and a marker landing between two of the four
sections would break that search. :meth:`write_synthesis` is the one write here that replaces rather
than appends — "the one non-append write" the plan names it, structural-only (S-31, S-32/S-34's
boundary: this module holds no opinion on whether the synthesis's claims are honest, only on the
section's shape).

**Section search is a raw string match over the whole body, and that is a sharp edge — fixed twice,
flagged once more.** :func:`_insert_before_heading` and :func:`_replace_section` both locate a
section by ``str.find``-ing the literal heading text, with no notion of "inside a turn's own
content" versus "a real section boundary." A payload this module did not compose and does not
control — a turn's operator message or model reply — containing a bare line that happens to read
``## Synthesis`` or ``## Distillation`` would, unquoted, silently corrupt every later append and
replace: the write still succeeds and validates clean, because the result is syntactically valid
markdown with every required field intact, and nothing about the file's *shape* is wrong, only which
words landed in which section. Fix round 1, finding 1 closes this for every source of body content
this module itself ever receives, two layers deep: (1) :func:`append_record`'s only caller —
``pkb.service.runtime``'s ``_turn_entry`` — blockquotes both the message and the reply line-by-line
before ever calling this module, so a turn's payload can never contain a bare ``## `` line (see that
function's own docstring for the reasoning and why quoting is still "verbatim"); (2)
:func:`_replace_section`'s *start* marker is now newline-anchored (``f"\n{heading}\n"``, matching
its own pre-existing ``end_marker`` and :func:`_insert_before_heading`), because blockquoting alone
left a gap here — a quoted echo like ``"> ## Synthesis\n"`` still contains the *unanchored* substring
``"## Synthesis\n"`` two characters in, which the old start marker matched just as readily as a real
heading. **Not** fixed: :meth:`write_synthesis`'s own ``md`` argument is operator-approved content
(S-30, S-32/S-34) nobody blockquotes, and a heading-shaped line inside *it* would corrupt the next
call's own search over the section it just wrote — but nothing calls :meth:`write_synthesis` before
Phase 4 wires the analysis session that drafts it, so hardening its own content now would be
guessing at a call site and an approval flow neither of which exists yet. Flagged here for whoever
wires that caller, rather than fixed speculatively.

**Topic tags scope to participating experts (P5).** :meth:`create` derives the founding agent's
``topic.*`` tag from ``session.agent_id`` via :func:`pkb.core.paths.topic_path_for_agent_id` /
:func:`~pkb.core.paths.topic_tag_for`, and writes none for the Librarian
(:data:`pkb.core.paths.LIBRARIAN_AGENT_ID`, which owns no topic) — zero participating experts is a
valid file, per the operator's P5 ruling scoping T-19/VA-9's ``topic.*`` floor away from
``FileRole.SESSION`` (``docs/superpowers/plans/2026-08-14-phase2-sessions.md``, "Three rulings";
``docs/superpowers/specs/2026-08-13-tree-T-rules.md``, T-19's own amendment). ``session.agent_id ==
"learning"`` is refused outright instead (S-26: an analysis session opens no file of its own) — the
module's own placeholder for the Learning agent's id, since Phase 4 has not yet registered one
anywhere on disk (mirrors ``pkb.service.sessions``'s own documented deferral for the same reason).
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from pkb.contracts import PkbAgentError
from pkb.core.errors import Finding, NotATopicRootError, errors_only, has_errors, render_findings
from pkb.core.frontmatter import parse as parse_frontmatter
from pkb.core.frontmatter import serialize, unknown_values
from pkb.core.models import Metadata
from pkb.core.paths import LIBRARIAN_AGENT_ID, topic_path_for_agent_id, topic_tag_for
from pkb.core.scaffold import PLACEHOLDER_SOURCE_TYPE, PLACEHOLDER_TYPE_TAG
from pkb.core.validation import validate_content
from pkb.service.sessions import Session

__all__ = [
    "LEARNING_AGENT_ID",
    "SESSION_TOPIC_FIELD",
    "SessionFileError",
    "SessionFileExistsError",
    "SessionFileInvalidError",
    "SessionFileMissingError",
    "SessionFileNoOwnFileError",
    "SessionFileSealedError",
    "SessionFileStructureError",
    "SessionFileWriter",
    "topic_tag_for_agent",
]

LEARNING_AGENT_ID: Final = "learning"
"""This module's own placeholder for the Learning agent's id (S-26).

Phase 4 mints the real registry entry; until it exists there is nothing on disk to check against
(mirrors ``pkb.service.sessions``'s module docstring, which defers the analogous S-9/S-19 checks for
the identical reason). A literal string comparison here is deliberately narrow: it names exactly the
one id this module refuses to open a file for, and nothing more.
"""

SESSION_TOPIC_FIELD: Final = "(session)"
"""The literal ``topic`` frontmatter value every session file carries (the plan's own spelling).

A session file owns no topic root (``pkb.core.paths.classify``'s own comment: it "owns no topic...
so the location-agreement checks... have nothing to compare against and are skipped by
construction"), so ``topic`` cannot be a real topic's display name the way it is on a note or a
reference. This fixed marker fills the required field (VA-4) without asserting a location no
session file has.
"""

_NO_OBJECTIVE_TEXT: Final = "The operator stated no objective."
_NO_OBJECTIVE_DESCRIPTION: Final = "A session with no stated objective."

_HEADING_EXPERTS: Final = "## Experts"
_HEADING_RECORD: Final = "## Record"
_HEADING_SYNTHESIS: Final = "## Synthesis"
_HEADING_DISTILLATION: Final = "## Distillation"
_HEADING_CLOSED: Final = "## Closed"
_HEADING_RENAMED: Final = "## Renamed"
_HEADING_ENDED: Final = "## Ended"
"""P3's own literal shape: "the seal is an appended ``## Ended`` marker entry"."""

_WHITESPACE_RUN: Final = re.compile(r"\s+")


# --------------------------------------------------------------------------------------
# Errors (project convention: content defects are findings, unusable inputs are exceptions)
# --------------------------------------------------------------------------------------


class SessionFileError(PkbAgentError):
    """Base for every refusal this module raises."""


class SessionFileExistsError(SessionFileError):
    """``create`` or ``rename`` refused: the target path is already a file (S-27).

    "Harness code creates the file when the session opens, under a name no session file already
    holds... nothing overwrites a file, sealed or open" (§2.7, quoted).
    """


class SessionFileSealedError(SessionFileError):
    """A write refused because ``session.state == 'ended'`` (S-24/P3).

    Checked at the store row the caller passed in, never by parsing the file — P3's own words: "the
    writer module refuses every write to a sealed file by checking the store, never by parsing the
    file."
    """


class SessionFileInvalidError(SessionFileError):
    """The bytes this write would produce fail ``pkb.core`` validation; the write is refused.

    Carries the blocking findings, rendered the way ``pkb.core.errors.render_findings`` renders them
    for a human or an agent with no other context — the same text Layer 2's own refusal path already
    uses (``src/pkb/agents/middleware/validation.py``).
    """

    def __init__(self, findings: Sequence[Finding]) -> None:
        self.findings: tuple[Finding, ...] = tuple(findings)
        super().__init__(render_findings(errors_only(self.findings)))


class SessionFileNoOwnFileError(SessionFileError):
    """``create`` refused: an analysis session opens no file of its own (S-19, S-26).

    Its writes land in the closed session's file it analyses instead — there is nothing here for
    :meth:`SessionFileWriter.create` to create.
    """


class SessionFileMissingError(SessionFileError):
    """A mutating call named a session whose file is not on disk — a caller bug, not a content
    defect: design leaves no path by which a session's file can go missing once created (§2.7,
    "nothing deletes it")."""


class SessionFileStructureError(SessionFileError):
    """One of the four fixed headings :meth:`SessionFileWriter.create` writes is missing.

    Defensive only. Every session file reaches the tree through this module alone (S-39: "every
    write reaches the tree through a session and the PKB has no other door"), so this should never
    fire outside a bug in this module itself.
    """


# --------------------------------------------------------------------------------------
# Body construction and section surgery
# --------------------------------------------------------------------------------------


def _one_line(value: str) -> str:
    return _WHITESPACE_RUN.sub(" ", value).strip()


def _objective_text(objective: str | None) -> str:
    if objective is None:
        return _NO_OBJECTIVE_TEXT
    stripped = objective.strip()
    return stripped if stripped else _NO_OBJECTIVE_TEXT


def _objective_description(objective: str | None) -> str:
    if objective is None:
        return _NO_OBJECTIVE_DESCRIPTION
    collapsed = _one_line(objective)
    return collapsed if collapsed else _NO_OBJECTIVE_DESCRIPTION


def _initial_body(*, objective: str | None, agent_id: str) -> str:
    """The four-section skeleton (S-31), filled in only where ``create`` has content to put."""
    return (
        f"\n{_objective_text(objective)}\n"
        "\n"
        f"{_HEADING_EXPERTS}\n"
        "\n"
        f"- {agent_id}\n"
        "\n"
        f"{_HEADING_RECORD}\n"
        "\n"
        f"{_HEADING_SYNTHESIS}\n"
        "\n"
        f"{_HEADING_DISTILLATION}\n"
    )


def _insert_before_heading(body: str, heading: str, entry_md: str) -> str:
    """Insert ``entry_md`` immediately before ``heading``, preserving every earlier byte (S-28).

    Used only for ``## Record``'s own append point (immediately before ``## Synthesis``) — the
    ordinary turn-by-turn narrative. Marker entries use :func:`_append_at_end` instead (module
    docstring).
    """
    marker = f"\n{heading}\n"
    index = body.find(marker)
    if index == -1:
        raise SessionFileStructureError(f"expected {heading!r} in the session body; not found")
    return body[:index] + f"\n{entry_md.strip()}\n" + body[index:]


def _append_at_end(body: str, entry_md: str) -> str:
    """Append ``entry_md`` as new bytes past whatever currently ends the body (S-29).

    Never rewrites a byte before the append point — the body stays append-only regardless of what
    a prior marker or turn already put there.
    """
    return body.rstrip("\n") + "\n\n" + entry_md.strip() + "\n"


def _marker_entry(heading: str, detail: str) -> str:
    return f"{heading}\n\n{detail}"


def _replace_section(body: str, heading: str, next_heading: str, new_content: str) -> str:
    """Replace only the content between ``heading`` and ``next_heading`` (S-31's one exception).

    Used by :meth:`SessionFileWriter.write_synthesis` alone. Locates both headings by their fixed
    literal text — the same four-section skeleton :meth:`SessionFileWriter.create` always writes —
    and replaces the span between them wholesale, leaving every other byte, before ``heading`` and
    from ``next_heading`` onward, untouched.

    Both markers are anchored on the newline that must precede a real heading
    (``f"\\n{heading}\\n"``), matching :func:`_insert_before_heading`'s own discipline and this
    function's own pre-existing ``end_marker`` — fix round 1, finding 1: the *start* marker used to
    search for the bare ``f"{heading}\\n"``, so a blockquoted turn entry's own quoted echo of a fake
    heading (``"> ## Synthesis\\n"``) still contained that unanchored substring two characters in,
    and this call found the fake before the genuine one even once the entry layer stopped emitting
    a bare ``## `` line. Anchoring both markers identically is what makes "verbatim, blockquoted"
    payloads actually inert here too, not only at :func:`_insert_before_heading`'s own call site.
    """
    start_marker = f"\n{heading}\n"
    start = body.find(start_marker)
    if start == -1:
        raise SessionFileStructureError(f"expected {heading!r} in the session body; not found")
    content_start = start + len(start_marker)
    end_marker = f"\n{next_heading}\n"
    end = body.find(end_marker, content_start)
    if end == -1:
        raise SessionFileStructureError(
            f"expected {next_heading!r} after {heading!r}; the session body is not the fixed shape"
        )
    stripped = new_content.strip()
    replacement = f"\n{stripped}\n\n" if stripped else "\n"
    return body[:content_start] + replacement + body[end + 1 :]


def topic_tag_for_agent(kb_root: Path, agent_id: str) -> str | None:
    """An agent's ``topic.*`` tag, or ``None`` when it owns no topic.

    ``None`` for the Librarian (:data:`~pkb.core.paths.LIBRARIAN_AGENT_ID`) and for any id the tree
    cannot resolve — a valid, zero-topic-tag file per P5 (module docstring). Public — not just
    :meth:`SessionFileWriter.create`'s own helper — because :mod:`pkb.service.runtime` needs the
    identical resolution for S-30's other half (Task 8): "one ``topic.*`` tag per expert that took
    part" is written once at ``create`` for the founding expert and again, idempotently, by
    :meth:`SessionFileWriter.add_expert_tag` whenever a completed run's own agent maps to a topic.
    A second, private copy of the Librarian-is-``None``/``NotATopicRootError``-is-``None`` logic in
    ``runtime.py`` would be the same rule expressed twice, with nothing to keep the two in step the
    day a third case (a topic renamed out from under a live session, say) is added to just one of
    them.
    """
    if agent_id == LIBRARIAN_AGENT_ID:
        return None
    try:
        topic_path = topic_path_for_agent_id(kb_root, agent_id)
        return topic_tag_for(kb_root, topic_path)
    except NotATopicRootError:
        return None


# --------------------------------------------------------------------------------------
# Disk I/O
# --------------------------------------------------------------------------------------


def _read(path: Path) -> str:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return handle.read()
    except OSError as exc:
        raise SessionFileMissingError(f"{path} could not be read: {exc}") from exc


def _create_exclusive(path: Path, text: str) -> None:
    """Exclusive create, total under an interrupted write (S-27).

    ``path.open("x")`` alone creates the destination inode before a single byte of ``text`` is
    written, so a write interrupted partway (disk full, a killed process, an injected fault) once
    left a permanent, corrupt file at the session's own path — unrecoverable, because nothing
    deletes a file (§2.7), and indistinguishable from a real collision to the next ``create()``
    call. Writing the full text to a same-directory temp file first and only then linking it into
    place keeps the invariant total: a failure at any point before the link leaves ``path`` exactly
    as it was (absent, if this is the first attempt); the link itself is one atomic syscall that
    either succeeds — with the complete bytes already committed to disk under the temp name — or
    raises ``FileExistsError`` untouched, never midway.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        try:
            path.hardlink_to(tmp_path)
        except FileExistsError as exc:
            raise SessionFileExistsError(f"{path} already exists (S-27)") from exc
    finally:
        with contextlib.suppress(OSError):
            tmp_path.unlink()


def _atomic_write(path: Path, text: str) -> None:
    """Write via a same-directory temp file plus ``os.replace`` — never a partial file on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        tmp_path.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


# --------------------------------------------------------------------------------------
# The writer
# --------------------------------------------------------------------------------------


class SessionFileWriter:
    """The one write surface a Librarian session and a Topic-Expert session both use (S-11).

    Every mutating method validates the bytes it would write, in memory, before any of them touch
    disk, and raises :class:`SessionFileInvalidError` rather than land an invalid file. Every method
    but :meth:`create` and :meth:`mark_ended` additionally refuses when ``session.state == 'ended'``
    (S-24/P3) — see the module docstring for why those two are exempt.
    """

    def __init__(self, kb_root: Path) -> None:
        self._kb_root = kb_root

    # -- creation ------------------------------------------------------------------------

    def create(self, session: Session) -> str:
        """Write the file's first bytes: the four-section skeleton, valid from the first read.

        Returns the KB-relative path (``session.file_path``). Raises
        :class:`SessionFileNoOwnFileError` for the Learning agent (S-26),
        :class:`SessionFileExistsError` when the path is already a file (S-27), and
        :class:`SessionFileInvalidError` when the bytes this session would produce do not
        otherwise validate. A session opened on the Librarian, with no expert consulted yet,
        carries no ``topic.*`` tag and validates clean regardless (P5) — see the module docstring.
        """
        if session.agent_id == LEARNING_AGENT_ID:
            raise SessionFileNoOwnFileError(
                f"session {session.session_id!r} opened on the Learning agent has no file of its "
                f"own (S-19, S-26); its writes land in the session it analyses"
            )
        rel_path = session.file_path
        full = self._kb_root / rel_path
        if full.exists():
            raise SessionFileExistsError(f"{rel_path} already exists (S-27)")

        topic_tag = topic_tag_for_agent(self._kb_root, session.agent_id)
        tags = (topic_tag, PLACEHOLDER_TYPE_TAG) if topic_tag else (PLACEHOLDER_TYPE_TAG,)
        today = session.created_at.date()
        meta = Metadata(
            title=rel_path,
            description=_objective_description(session.objective),
            topic=SESSION_TOPIC_FIELD,
            tags=tags,
            created=today,
            updated=today,
            source_type=PLACEHOLDER_SOURCE_TYPE,
        )
        body = _initial_body(objective=session.objective, agent_id=session.agent_id)
        text = serialize(meta, body)
        self._validate_or_raise(rel_path, text)
        _create_exclusive(full, text)
        return rel_path

    # -- the running record ---------------------------------------------------------------

    def append_record(self, session: Session, entry_md: str) -> None:
        """Append ``entry_md`` at the end of ``## Record``, before every existing byte of it (S-28).

        Refused once the session is sealed (S-24/P3).
        """
        _refuse_if_sealed(session)
        full = self._kb_root / session.file_path
        loaded = _load(_read(full))
        new_body = _insert_before_heading(loaded.body, _HEADING_SYNTHESIS, entry_md)
        new_text = serialize(loaded.meta, new_body, extra=loaded.extra)
        self._validate_or_raise(session.file_path, new_text)
        _atomic_write(full, new_text)

    def add_expert_tag(self, session: Session, topic_tag: str) -> None:
        """Frontmatter gains ``topic_tag`` when a new expert joins (S-30).

        A no-op — no read-modify-write, no validation call — when the tag is already present
        (idempotent: a second expert consult that resolves to the same topic must not grow the tag
        list or rewrite the file for nothing).
        """
        _refuse_if_sealed(session)
        full = self._kb_root / session.file_path
        loaded = _load(_read(full))
        if topic_tag in loaded.meta.tags:
            return
        new_meta = dataclasses.replace(loaded.meta, tags=(*loaded.meta.tags, topic_tag))
        new_text = serialize(new_meta, loaded.body, extra=loaded.extra)
        self._validate_or_raise(session.file_path, new_text)
        _atomic_write(full, new_text)

    # -- the three command markers (S-29) --------------------------------------------------

    def mark_closed(self, session: Session) -> None:
        """Append the ``/close`` marker (S-29). Refused once the session is sealed."""
        _refuse_if_sealed(session)
        if session.closed_at is None:
            raise ValueError(f"session {session.session_id!r} carries no closed_at (caller bug)")
        entry = _marker_entry(_HEADING_CLOSED, f"Closed on {session.closed_at.date().isoformat()}.")
        self._append_marker(session, entry)

    def mark_ended(self, session: Session) -> None:
        """Append the ``## Ended`` seal marker (S-24/P3, S-29).

        No ``_refuse_if_sealed`` guard here on purpose (module docstring): this call is the sealing
        act itself, made with the freshly-``ended`` row :meth:`~pkb.service.sessions.SessionStore.end`
        returns. After it returns, every other method on this session refuses.
        """
        if session.ended_at is None:
            raise ValueError(f"session {session.session_id!r} carries no ended_at (caller bug)")
        entry = _marker_entry(_HEADING_ENDED, f"Ended on {session.ended_at.date().isoformat()}.")
        self._append_marker(session, entry, skip_seal_check=True)

    def _append_marker(
        self, session: Session, entry_md: str, *, skip_seal_check: bool = False
    ) -> None:
        if not skip_seal_check:
            _refuse_if_sealed(session)
        full = self._kb_root / session.file_path
        loaded = _load(_read(full))
        new_body = _append_at_end(loaded.body, entry_md)
        new_text = serialize(loaded.meta, new_body, extra=loaded.extra)
        self._validate_or_raise(session.file_path, new_text)
        _atomic_write(full, new_text)

    # -- rename ----------------------------------------------------------------------------

    def rename(self, session: Session, old_path: str) -> str:
        """Move the file to ``session.file_path``, rewrite ``title``, append the rename marker.

        ``session`` is the row *after* :meth:`~pkb.service.sessions.SessionStore.rename` already
        committed the new name — ``old_path`` is the path the file held before, which nothing else
        remembers (S-16, S-29). Loses nothing: every existing byte survives, only ``title`` and the
        trailing marker change. Refused once the session is sealed (S-16: "it refuses the rename
        once ``/end`` has sealed this file").
        """
        _refuse_if_sealed(session)
        new_path = session.file_path
        old_full = self._kb_root / old_path
        new_full = self._kb_root / new_path
        loaded = _load(_read(old_full))
        new_meta = dataclasses.replace(loaded.meta, title=new_path)
        stamp = (session.updated_at or session.created_at).date().isoformat()
        entry = _marker_entry(_HEADING_RENAMED, f"Renamed from `{old_path}` on {stamp}.")
        new_body = _append_at_end(loaded.body, entry)
        new_text = serialize(new_meta, new_body, extra=loaded.extra)
        self._validate_or_raise(new_path, new_text)

        if new_full == old_full:
            _atomic_write(new_full, new_text)
        else:
            if new_full.exists():
                raise SessionFileExistsError(f"{new_path} already exists (S-27, S-16)")
            _atomic_write(new_full, new_text)
            old_full.unlink()
        return new_path

    # -- synthesis: the one non-append write ------------------------------------------------

    def write_synthesis(self, session: Session, md: str) -> None:
        """Replace the ``## Synthesis`` section's content with ``md`` — nothing else changes.

        The one write here that is not an append (S-31): the caller has already secured the
        operator's word-for-word approval (S-30, S-32/S-34 — this method holds no opinion on the
        content itself, only on the section's shape). Refused once the session is sealed.
        """
        _refuse_if_sealed(session)
        full = self._kb_root / session.file_path
        loaded = _load(_read(full))
        new_body = _replace_section(loaded.body, _HEADING_SYNTHESIS, _HEADING_DISTILLATION, md)
        new_text = serialize(loaded.meta, new_body, extra=loaded.extra)
        self._validate_or_raise(session.file_path, new_text)
        _atomic_write(full, new_text)

    # -- internals ---------------------------------------------------------------------------

    def _validate_or_raise(self, rel_path: str, text: str) -> None:
        findings = validate_content(self._kb_root, rel_path, text)
        if has_errors(findings):
            raise SessionFileInvalidError(findings)


def _refuse_if_sealed(session: Session) -> None:
    if session.state == "ended":
        raise SessionFileSealedError(
            f"session {session.session_id!r} is sealed (state='ended'); no further write is "
            f"accepted (S-24/P3)"
        )


@dataclasses.dataclass(frozen=True, slots=True)
class _Loaded:
    """One session file's document, read once and reused by every field a rewrite needs."""

    meta: Metadata
    body: str
    extra: dict[str, object]


def _load(text: str) -> _Loaded:
    """Parse ``text`` once — the document's metadata, body and preserved unknown fields.

    Bytes this module itself always wrote (a session file has no other writer, S-39), so a single
    parse is trusted rather than re-checked.
    """
    doc = parse_frontmatter(text)
    return _Loaded(meta=doc.meta or Metadata(), body=doc.body, extra=unknown_values(doc))
