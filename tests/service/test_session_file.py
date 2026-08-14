"""``SessionFileWriter`` — one file, whole life, written by harness code (S-11, S-24 … S-31, S-16).

Every test builds a real knowledge base under ``tmp_path`` (two scaffolded topics, ``Cooking`` and
``BBQ``, so ``add_expert_tag`` has a second real topic to add) and a synthetic
:class:`~pkb.service.sessions.Session` row by hand — this module never touches ``SessionStore``, the
way ``tests/core/test_scaffold.py`` never touches a running agent. Rule ids are cited per test, per
``CLAUDE.md``'s "rule ids are the contract."
"""

from __future__ import annotations

import dataclasses
import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from pkb.core.errors import Severity
from pkb.core.models import FileClass, FileRole
from pkb.core.paths import classify
from pkb.core.scaffold import scaffold_topic
from pkb.core.validation import validate_content
from pkb.service.session_file import (
    LEARNING_AGENT_ID,
    SessionFileExistsError,
    SessionFileInvalidError,
    SessionFileNoOwnFileError,
    SessionFileSealedError,
    SessionFileWriter,
)
from pkb.service.sessions import Session

COOKING_AGENT = "topic/cooking"
BBQ_AGENT = "topic/bbq"
LIBRARIAN_AGENT = "librarian"

TODAY = date(2026, 8, 14)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 14, hour, minute, tzinfo=UTC)


def kb(tmp_path: Path) -> Path:
    """A real KB with two scaffolded topics — ``Cooking`` and ``BBQ`` — nothing else."""
    root = tmp_path / "KB"
    root.mkdir()
    scaffold_topic(
        root, "Cooking", title="Cooking", description="Home cooking", today=TODAY, regenerate=False
    )
    scaffold_topic(
        root, "BBQ", title="BBQ", description="Barbecue equipment", today=TODAY, regenerate=False
    )
    return root


def make_session(
    *,
    session_id: str = "session-1",
    agent_id: str = COOKING_AGENT,
    objective: str | None = "a rub that doesn't burn above 250",
    name: str = "grilling-temperatures",
    operator: str = "sergiy",
    state: str = "open",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    closed_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> Session:
    created = created_at or at(9)
    return Session(
        session_id=session_id,
        agent_id=agent_id,
        objective=objective,
        name=name,
        operator=operator,
        state=state,  # type: ignore[arg-type]
        created_at=created,
        updated_at=updated_at or created,
        closed_at=closed_at,
        ended_at=ended_at,
    )


def read(kb_root: Path, rel_path: str) -> str:
    return (kb_root / rel_path).read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# § create — S-11, S-26, S-27, S-30, S-31
# --------------------------------------------------------------------------------------


def test_create_classifies_session_and_validates_clean_s26_s27_s30_s31(tmp_path: Path) -> None:
    root = kb(tmp_path)
    session = make_session()

    path = SessionFileWriter(root).create(session)

    assert path == "sessions/grilling-temperatures.md"
    role, file_class = classify(root, root / path)
    assert (role, file_class) == (FileRole.SESSION, FileClass.AUTHORED)
    findings = validate_content(root, path, read(root, path))
    assert [f for f in findings if f.severity is Severity.ERROR] == []


def test_create_emits_the_exact_frontmatter_and_body(tmp_path: Path) -> None:
    """Pins the writer's exact output byte-for-byte — the frontmatter shape the plan specifies."""
    root = kb(tmp_path)
    session = make_session()

    path = SessionFileWriter(root).create(session)

    assert read(root, path) == (
        "---\n"
        'title: "sessions/grilling-temperatures.md"\n'
        'description: "a rub that doesn\'t burn above 250"\n'
        'topic: "(session)"\n'
        "tags:\n"
        "  - topic.cooking\n"
        "  - type.summary\n"
        "created: 2026-08-14\n"
        "updated: 2026-08-14\n"
        "source_type: summary\n"
        "---\n"
        "\n"
        "a rub that doesn't burn above 250\n"
        "\n"
        "## Experts\n"
        "\n"
        "- topic/cooking\n"
        "\n"
        "## Record\n"
        "\n"
        "## Synthesis\n"
        "\n"
        "## Distillation\n"
    )


def test_create_states_when_no_objective_was_given_s5_analogue(tmp_path: Path) -> None:
    root = kb(tmp_path)
    session = make_session(objective=None, name="standing-chat")

    path = SessionFileWriter(root).create(session)
    text = read(root, path)

    assert "The operator stated no objective." in text
    assert 'description: "A session with no stated objective."' in text
    findings = validate_content(root, path, text)
    assert [f for f in findings if f.severity is Severity.ERROR] == []


def test_create_writes_four_headings_in_order_s31(tmp_path: Path) -> None:
    root = kb(tmp_path)
    session = make_session()

    path = SessionFileWriter(root).create(session)
    text = read(root, path)

    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == ["## Experts", "## Record", "## Synthesis", "## Distillation"]


def test_create_refuses_when_the_file_already_exists_s27(tmp_path: Path) -> None:
    root = kb(tmp_path)
    session = make_session()
    writer = SessionFileWriter(root)
    writer.create(session)
    original = read(root, session.file_path)

    with pytest.raises(SessionFileExistsError):
        writer.create(session)

    assert read(root, session.file_path) == original


def test_create_leaves_no_file_when_the_write_fails_mid_create_s27(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's own reproduction: the old ``_create_exclusive`` opened the destination path
    directly (``path.open("x")``), so a write interrupted after the file existed but before its
    content landed left a permanent, corrupt file at the session's own path — unrecoverable, since
    nothing deletes files, and indistinguishable from a real collision to a later ``create()``.
    ``os.fdopen`` is patched to hand back a handle whose ``write`` fails, simulating exactly that:
    the temp file exists on disk (``tempfile.mkstemp`` already made it) when the fault lands."""
    root = kb(tmp_path)
    session = make_session()

    class _BoomHandle:
        def __init__(self, fd: int) -> None:
            self._fd = fd

        def __enter__(self) -> _BoomHandle:
            return self

        def __exit__(self, *exc: object) -> bool:
            os.close(self._fd)
            return False

        def write(self, _text: str) -> int:
            raise OSError("simulated disk failure mid-write")

    def fake_fdopen(fd: int, *args: object, **kwargs: object) -> _BoomHandle:
        return _BoomHandle(fd)

    monkeypatch.setattr(os, "fdopen", fake_fdopen)

    with pytest.raises(OSError):
        SessionFileWriter(root).create(session)

    assert not (root / session.file_path).exists()
    sessions_dir = root / "sessions"
    leftover = list(sessions_dir.iterdir()) if sessions_dir.exists() else []
    assert leftover == [], f"stray temp file(s) left behind: {leftover}"


def test_create_raises_for_the_learning_agent_s26(tmp_path: Path) -> None:
    root = kb(tmp_path)
    session = make_session(agent_id=LEARNING_AGENT_ID, name="analysis-session")

    with pytest.raises(SessionFileNoOwnFileError):
        SessionFileWriter(root).create(session)

    assert not (root / session.file_path).exists()


def test_create_on_a_librarian_session_with_no_expert_creates_cleanly_p5(
    tmp_path: Path,
) -> None:
    """P5: the operator scoped T-19/VA-9's ``topic.*`` floor away from ``FileRole.SESSION`` — a
    Librarian session, before any Topic Expert has joined it, has zero participating experts and
    so zero ``topic.*`` tags, and that is a valid file, not a refusal. ``type.summary`` still
    lands (the ``type.*`` floor is untouched)."""
    root = kb(tmp_path)
    session = make_session(agent_id=LIBRARIAN_AGENT, name="cross-topic-question")

    path = SessionFileWriter(root).create(session)

    assert (root / path).exists()
    text = read(root, path)
    frontmatter_block = text.split("---", 2)[1]
    assert "  - type.summary" in frontmatter_block
    assert "  - topic." not in frontmatter_block
    findings = validate_content(root, path, text)
    assert [f for f in findings if f.severity is Severity.ERROR] == []


# --------------------------------------------------------------------------------------
# § append_record — S-28
# --------------------------------------------------------------------------------------


def test_append_record_preserves_every_existing_byte_before_the_append_point_s28(
    tmp_path: Path,
) -> None:
    root = kb(tmp_path)
    session = make_session()
    writer = SessionFileWriter(root)
    path = writer.create(session)
    before = read(root, path)

    writer.append_record(session, "### Turn 1\n\nAsked about grill temperatures.")

    after = read(root, path)
    prefix, _, suffix = before.partition("## Synthesis")
    assert after.startswith(prefix)
    assert after.endswith("## Synthesis" + suffix)
    assert "### Turn 1\n\nAsked about grill temperatures." in after


def test_append_record_lands_entries_in_order_between_record_and_synthesis(tmp_path: Path) -> None:
    root = kb(tmp_path)
    session = make_session()
    writer = SessionFileWriter(root)
    path = writer.create(session)

    writer.append_record(session, "first entry")
    writer.append_record(session, "second entry")

    text = read(root, path)
    record_start = text.index("## Record")
    synthesis_start = text.index("## Synthesis")
    record_section = text[record_start:synthesis_start]
    assert record_section.index("first entry") < record_section.index("second entry")
    assert text.count("first entry") == 1
    assert text.count("second entry") == 1
    findings = validate_content(root, path, text)
    assert [f for f in findings if f.severity is Severity.ERROR] == []


# --------------------------------------------------------------------------------------
# § add_expert_tag — S-30
# --------------------------------------------------------------------------------------


def test_add_expert_tag_adds_the_tag_and_validates_clean_s30(tmp_path: Path) -> None:
    root = kb(tmp_path)
    session = make_session()
    writer = SessionFileWriter(root)
    path = writer.create(session)

    writer.add_expert_tag(session, "topic.bbq")

    text = read(root, path)
    assert "topic.bbq" in text
    findings = validate_content(root, path, text)
    assert [f for f in findings if f.severity is Severity.ERROR] == []


def test_add_expert_tag_is_idempotent_for_an_already_present_tag(tmp_path: Path) -> None:
    root = kb(tmp_path)
    session = make_session()
    writer = SessionFileWriter(root)
    path = writer.create(session)
    writer.add_expert_tag(session, "topic.bbq")
    once = read(root, path)

    writer.add_expert_tag(session, "topic.bbq")

    assert read(root, path) == once
    assert once.count("topic.bbq") == 1


def test_add_expert_tag_adds_the_first_tag_to_a_zero_topic_tag_session_p5(tmp_path: Path) -> None:
    """The transition P5 makes possible: a Librarian session's file starts with zero ``topic.*``
    tags (validating clean, per the ruling) and gains its first one the moment an expert joins."""
    root = kb(tmp_path)
    session = make_session(agent_id=LIBRARIAN_AGENT, name="cross-topic-question")
    writer = SessionFileWriter(root)
    path = writer.create(session)
    before = read(root, path)
    assert "  - topic." not in before.split("---", 2)[1]

    writer.add_expert_tag(session, "topic.cooking")

    after = read(root, path)
    assert "  - topic.cooking" in after.split("---", 2)[1]
    findings = validate_content(root, path, after)
    assert [f for f in findings if f.severity is Severity.ERROR] == []


def test_add_expert_tag_refuses_a_malformed_tag_and_leaves_the_file_untouched(
    tmp_path: Path,
) -> None:
    root = kb(tmp_path)
    session = make_session()
    writer = SessionFileWriter(root)
    path = writer.create(session)
    before = read(root, path)

    with pytest.raises(SessionFileInvalidError):
        writer.add_expert_tag(session, "Not A Valid Tag")

    assert read(root, path) == before


# --------------------------------------------------------------------------------------
# § the command markers — S-24 (P3), S-29
# --------------------------------------------------------------------------------------


def test_mark_closed_appends_a_closed_marker_naming_the_date_s29(tmp_path: Path) -> None:
    root = kb(tmp_path)
    session = make_session()
    writer = SessionFileWriter(root)
    path = writer.create(session)
    closed = dataclasses.replace(session, state="closed", closed_at=at(10), updated_at=at(10))

    writer.mark_closed(closed)

    text = read(root, path)
    assert text.rstrip().endswith("## Closed\n\nClosed on 2026-08-14.")
    findings = validate_content(root, path, text)
    assert [f for f in findings if f.severity is Severity.ERROR] == []


def test_mark_ended_appends_the_literal_ended_heading_s24_p3(tmp_path: Path) -> None:
    root = kb(tmp_path)
    session = make_session()
    writer = SessionFileWriter(root)
    path = writer.create(session)
    closed = dataclasses.replace(session, state="closed", closed_at=at(10), updated_at=at(10))
    writer.mark_closed(closed)
    ended = dataclasses.replace(closed, state="ended", ended_at=at(11), updated_at=at(11))

    writer.mark_ended(ended)

    text = read(root, path)
    assert "## Ended\n\nEnded on 2026-08-14." in text
    assert text.index("## Closed") < text.index("## Ended")
    findings = validate_content(root, path, text)
    assert [f for f in findings if f.severity is Severity.ERROR] == []


def test_writes_after_mark_ended_all_raise_and_leave_the_file_untouched_s24(
    tmp_path: Path,
) -> None:
    root = kb(tmp_path)
    session = make_session()
    writer = SessionFileWriter(root)
    path = writer.create(session)
    closed = dataclasses.replace(session, state="closed", closed_at=at(10), updated_at=at(10))
    writer.mark_closed(closed)
    ended = dataclasses.replace(closed, state="ended", ended_at=at(11), updated_at=at(11))
    writer.mark_ended(ended)
    sealed = read(root, path)

    with pytest.raises(SessionFileSealedError):
        writer.append_record(ended, "too late")
    with pytest.raises(SessionFileSealedError):
        writer.add_expert_tag(ended, "topic.bbq")
    with pytest.raises(SessionFileSealedError):
        writer.mark_closed(ended)
    with pytest.raises(SessionFileSealedError):
        writer.write_synthesis(ended, "too late")
    with pytest.raises(SessionFileSealedError):
        writer.rename(ended, path)

    assert read(root, path) == sealed


# --------------------------------------------------------------------------------------
# § rename — S-16, S-29
# --------------------------------------------------------------------------------------


def test_rename_moves_the_file_and_retitles_preserving_content_s16(tmp_path: Path) -> None:
    """ "Loses nothing" (S-16) pinned as full-string equality, not a handful of substrings — a
    mutation dropping half the body must fail this test. The expected bytes are computed
    independently from the pre-rename content: the same text with only the ``title`` line rewritten
    and the rename marker appended, nothing else touched, nothing else missing."""
    root = kb(tmp_path)
    session = make_session()
    writer = SessionFileWriter(root)
    old_path = writer.create(session)
    writer.append_record(session, "an early turn")
    before = read(root, old_path)

    renamed = dataclasses.replace(session, name="a-much-better-name", updated_at=at(10))
    new_path = writer.rename(renamed, old_path)

    assert new_path == "sessions/a-much-better-name.md"
    assert not (root / old_path).exists()
    after = read(root, new_path)

    retitled = before.replace(
        'title: "sessions/grilling-temperatures.md"', 'title: "sessions/a-much-better-name.md"'
    )
    assert retitled != before  # the replace above actually matched something
    expected = (
        retitled.rstrip("\n") + f"\n\n## Renamed\n\nRenamed from `{old_path}` on 2026-08-14.\n"
    )
    assert after == expected
    findings = validate_content(root, new_path, after)
    assert [f for f in findings if f.severity is Severity.ERROR] == []


def test_rename_refuses_once_the_session_is_sealed_s16_s24(tmp_path: Path) -> None:
    root = kb(tmp_path)
    session = make_session()
    writer = SessionFileWriter(root)
    old_path = writer.create(session)
    closed = dataclasses.replace(session, state="closed", closed_at=at(10), updated_at=at(10))
    writer.mark_closed(closed)
    ended = dataclasses.replace(closed, state="ended", ended_at=at(11), updated_at=at(11))
    writer.mark_ended(ended)

    renamed = dataclasses.replace(ended, name="too-late")
    with pytest.raises(SessionFileSealedError):
        writer.rename(renamed, old_path)

    assert (root / old_path).exists()
    assert not (root / "sessions/too-late.md").exists()


# --------------------------------------------------------------------------------------
# § write_synthesis — S-31 (the one non-append write)
# --------------------------------------------------------------------------------------


def test_write_synthesis_replaces_only_the_synthesis_section_s31(tmp_path: Path) -> None:
    root = kb(tmp_path)
    session = make_session()
    writer = SessionFileWriter(root)
    path = writer.create(session)
    writer.append_record(session, "a turn in the record")

    writer.write_synthesis(session, "The rub holds up to 250.")

    text = read(root, path)
    assert "a turn in the record" in text
    assert "The rub holds up to 250." in text
    synthesis_start = text.index("## Synthesis")
    distillation_start = text.index("## Distillation")
    assert "The rub holds up to 250." in text[synthesis_start:distillation_start]
    findings = validate_content(root, path, text)
    assert [f for f in findings if f.severity is Severity.ERROR] == []


def test_write_synthesis_called_again_fully_replaces_the_prior_text(tmp_path: Path) -> None:
    root = kb(tmp_path)
    session = make_session()
    writer = SessionFileWriter(root)
    path = writer.create(session)
    writer.write_synthesis(session, "First draft of the synthesis.")

    writer.write_synthesis(session, "Final synthesis text.")

    text = read(root, path)
    assert "First draft of the synthesis." not in text
    assert "Final synthesis text." in text


def test_write_synthesis_on_a_session_that_searched_nothing_leaves_the_section_empty(
    tmp_path: Path,
) -> None:
    """§2.7: "A session that produced nothing still has a file... it leaves no synthesis" — the
    section exists (S-31) but stays empty until write_synthesis is ever called."""
    root = kb(tmp_path)
    session = make_session()
    path = SessionFileWriter(root).create(session)

    text = read(root, path)
    assert "## Synthesis\n\n## Distillation" in text
