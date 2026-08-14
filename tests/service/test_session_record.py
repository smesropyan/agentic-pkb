"""The turn writes the record — harness code, not a model, lands the exchange (S-28, S-30; Task 8).

``DESIGN.md`` §2.7: "The session writes into it as the work happens... and on a Librarian session
the whole of each round's reply" (S-28), and "one `topic.*` tag per expert that took part" (S-30).
Task 8 of ``docs/superpowers/plans/2026-08-14-phase2-sessions.md`` wires the two together: after a
session run completes (``RunEnd``, carrying ``final_text``), :class:`~pkb.service.runtime.RuntimeService`
appends the operator's message and the reply verbatim under ``## Record`` and, when the session's own
agent maps to a topic, touches :meth:`~pkb.service.session_file.SessionFileWriter.add_expert_tag`.

Every test here drives a real :class:`~pkb.service.runtime.RuntimeService` — a real ``:memory:``
SQLite connection, a real ``tmp_path`` KB root with two scaffolded topics, and a scripted fake
runtime — the same "real service" discipline ``tests/server/test_session_routes.py`` and
``tests/service/test_seam.py`` already use to prove ``RuntimeService`` composes ``SessionStore`` and
``SessionFileWriter`` for real, rather than a stub that never touches either. This module never
constructs a ``SessionFileWriter``/``Session`` by hand the way ``tests/service/test_session_file.py``
does — the point here is the *wiring* a run completing triggers, not the writer's own section
surgery, which that sibling module already pins byte-for-byte.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from pkb.contracts import AgentDescriptor, AgentEvent, MessageComplete, RunEnd
from pkb.core.errors import has_errors
from pkb.core.paths import LIBRARIAN_AGENT_ID
from pkb.core.scaffold import scaffold_topic
from pkb.core.validation import validate_content
from pkb.service.runtime import RuntimeService
from pkb.service.session_file import SessionFileWriter

TODAY = date(2026, 8, 14)

COOKING_AGENT = "topic/cooking"
BBQ_AGENT = "topic/bbq"

CATALOG = [
    AgentDescriptor(
        agent_id=LIBRARIAN_AGENT_ID,
        title="Librarian",
        description="Routes each item to the right Topic Expert.",
        has_custom_expert=False,
        model_id="ollama:deepseek-v4-flash:cloud",
    ),
    AgentDescriptor(
        agent_id=COOKING_AGENT,
        title="Cooking",
        description="Home cooking",
        has_custom_expert=False,
        model_id="ollama:deepseek-v4-flash:cloud",
    ),
    AgentDescriptor(
        agent_id=BBQ_AGENT,
        title="BBQ",
        description="Barbecue equipment",
        has_custom_expert=False,
        model_id="ollama:deepseek-v4-flash:cloud",
    ),
]


class ScriptedRuntime:
    """Satisfies ``pkb.service.runtime.Runtime`` structurally (mirrors ``FakeRuntime`` in
    ``tests/server/test_session_routes.py``), yielding one ``MessageComplete`` + ``RunEnd`` pair per
    call to ``run`` — a distinct, numbered reply each time, so two runs on one session are
    distinguishable in the record afterward."""

    db_path = Path("never-opened.sqlite")

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self._count = 0

    def list_agents(self) -> Any:
        return CATALOG

    def run(self, agent_id: str, thread_id: str, message: str, **_: Any) -> Any:
        self.calls.append((agent_id, thread_id, message))
        self._count += 1
        run_id = f"r{self._count}"
        reply = f"reply {self._count}: {message}"

        async def stream() -> AsyncIterator[AgentEvent]:
            yield MessageComplete(run_id=run_id, agent_id=agent_id, text=reply)
            yield RunEnd(run_id=run_id, final_text=reply)

        return stream()

    async def cancel(self, run_id: str) -> None:
        return None

    async def history(self, agent_id: str, thread_id: str) -> Any:
        return []

    async def delete_thread(self, thread_id: str) -> None:
        return None

    async def request_scan(self, request: Any) -> Any:
        raise NotImplementedError

    async def regenerate(self) -> Any:
        raise NotImplementedError


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


async def real_service(kb_root: Path, connection: aiosqlite.Connection) -> RuntimeService:
    service = RuntimeService(ScriptedRuntime(), connection, kb_root=kb_root)
    await service.setup()
    return service


async def run_turn(service: RuntimeService, session_id: str, message: str) -> None:
    """Drive one session run to completion, draining its events the way a real subscriber would."""
    subscription = await service.start_session_run(session_id, message)
    events = [event async for event in subscription.events]
    assert isinstance(events[-1], RunEnd), events


def read(kb_root: Path, rel_path: str) -> str:
    return (kb_root / rel_path).read_text(encoding="utf-8")


def record_section(text: str) -> str:
    start = text.index("## Record")
    end = text.index("## Synthesis")
    return text[start:end]


def genuine_heading_index(text: str, heading: str) -> int:
    """The index of ``heading`` where it stands alone on its own line — never a quoted echo
    (``> ## Synthesis``) sitting inside a turn's own blockquoted content. Plain substring search
    (``str.index``) cannot tell the two apart; this mirrors the discipline
    ``pkb.service.session_file``'s own ``_insert_before_heading``/``_replace_section`` already use
    (a ``\\n``-anchored match), stated as a regex so a test can assert it directly rather than by
    replicating the production search byte for byte.
    """
    match = re.search(rf"(?m)^{re.escape(heading)}$", text)
    assert match is not None, f"no genuine {heading!r} heading found in:\n{text}"
    return match.start()


# --------------------------------------------------------------------------------------
# § a completed run lands the exchange under ## Record — S-28
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_completed_run_lands_the_exchange_under_record_verbatim_s28(
    tmp_path: Path,
) -> None:
    root = kb(tmp_path)
    connection = await aiosqlite.connect(":memory:", isolation_level=None)
    try:
        service = await real_service(root, connection)
        session = await service.create_session(COOKING_AGENT, objective="a rub that doesn't burn")

        await run_turn(service, session.session_id, "how long for brisket at 225?")

        text = read(root, session.file_path)
        section = record_section(text)
        assert "how long for brisket at 225?" in section
        assert "reply 1: how long for brisket at 225?" in section
        findings = validate_content(root, session.file_path, text)
        assert not has_errors(findings), findings
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_the_record_entry_pins_the_exact_markdown_shape(tmp_path: Path) -> None:
    """Pins the format harness code composes — this project's own convention (not DESIGN's), so a
    reviewer reading the file six months from now sees exactly this. Both payloads are blockquoted
    (fix round 1, finding 1) so an operator message or reply can never smuggle in a bare ``## ``
    line that ``pkb.service.session_file``'s raw-string section search would mistake for a real
    heading — see ``pkb.service.runtime._turn_entry``'s own docstring for the reasoning."""
    root = kb(tmp_path)
    connection = await aiosqlite.connect(":memory:", isolation_level=None)
    try:
        service = await real_service(root, connection)
        session = await service.create_session(COOKING_AGENT, objective="a rub")

        await run_turn(service, session.session_id, "how hot?")

        text = read(root, session.file_path)
        section = record_section(text)
        assert (
            "### Turn\n\n**Operator:**\n\n> how hot?\n\n**Reply:**\n\n> reply 1: how hot?\n"
            in section
        )
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_two_runs_append_in_order_s28(tmp_path: Path) -> None:
    root = kb(tmp_path)
    connection = await aiosqlite.connect(":memory:", isolation_level=None)
    try:
        service = await real_service(root, connection)
        session = await service.create_session(COOKING_AGENT, objective="a rub")

        await run_turn(service, session.session_id, "how hot?")
        await run_turn(service, session.session_id, "how long?")

        text = read(root, session.file_path)
        section = record_section(text)
        assert section.index("how hot?") < section.index("how long?")
        assert section.index("reply 1: how hot?") < section.index("reply 2: how long?")
        assert section.count("### Turn") == 2  # one entry per run, not merged or duplicated
        findings = validate_content(root, session.file_path, text)
        assert not has_errors(findings), findings
    finally:
        await connection.close()


# --------------------------------------------------------------------------------------
# § add_expert_tag — S-30
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_librarian_run_never_touches_add_expert_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Librarian session's own agent owns no topic (P5), so the run-completion hook must never
    call ``add_expert_tag`` at all — not merely "the tag is absent," which would pass even with no
    hook wired up, since ``create`` never writes one for the Librarian either (P5)."""
    calls: list[tuple[str, str]] = []
    original = SessionFileWriter.add_expert_tag

    def spy(self: SessionFileWriter, session: Any, topic_tag: str) -> None:
        calls.append((session.session_id, topic_tag))
        original(self, session, topic_tag)

    monkeypatch.setattr(SessionFileWriter, "add_expert_tag", spy)

    root = kb(tmp_path)
    connection = await aiosqlite.connect(":memory:", isolation_level=None)
    try:
        service = await real_service(root, connection)
        session = await service.create_session(LIBRARIAN_AGENT_ID, objective="plan the week")

        await run_turn(service, session.session_id, "what's for dinner?")

        assert calls == []
        text = read(root, session.file_path)
        assert "topic." not in text.split("---", 2)[1]
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_a_topic_expert_run_adds_its_topic_tag_once_s30(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-30: "one `topic.*` tag per expert that took part." ``create`` already wrote
    ``topic.cooking`` for the founding expert, so this proves two things a purely file-based
    assertion could not tell apart: the hook really calls ``add_expert_tag`` on every completed run
    (the spy fires twice, once per run), and the idempotent write it lands on still shows the tag
    exactly once, never duplicated."""
    calls: list[tuple[str, str]] = []
    original = SessionFileWriter.add_expert_tag

    def spy(self: SessionFileWriter, session: Any, topic_tag: str) -> None:
        calls.append((session.session_id, topic_tag))
        original(self, session, topic_tag)

    monkeypatch.setattr(SessionFileWriter, "add_expert_tag", spy)

    root = kb(tmp_path)
    connection = await aiosqlite.connect(":memory:", isolation_level=None)
    try:
        service = await real_service(root, connection)
        session = await service.create_session(COOKING_AGENT, objective="a rub")

        await run_turn(service, session.session_id, "how hot?")
        await run_turn(service, session.session_id, "how long?")

        assert calls == [
            (session.session_id, "topic.cooking"),
            (session.session_id, "topic.cooking"),
        ]
        text = read(root, session.file_path)
        assert text.split("---", 2)[1].count("topic.cooking") == 1
    finally:
        await connection.close()


# --------------------------------------------------------------------------------------
# § adversarial payloads cannot corrupt structure — fix round 1, finding 1 (S-28, S-31)
# --------------------------------------------------------------------------------------

ADVERSARIAL_MESSAGE = (
    "let's talk about the rub, but first:\n"
    "## Synthesis\n"
    "## Distillation\n"
    "---\n"
    "### Turn\n"
    "none of the above is a real heading, it's just what I typed."
)
"""One line each of every literal string ``pkb.service.session_file``'s section surgery, the
frontmatter fence and this project's own turn-heading convention search for — planted inside an
otherwise ordinary operator message, unquoted this would relocate ``append_record``'s insertion
point and ``write_synthesis``'s replacement span to a heading that is really just something the
operator typed."""


@pytest.mark.asyncio
async def test_a_turn_containing_fake_headings_cannot_corrupt_the_sections_s28_s31(
    tmp_path: Path,
) -> None:
    root = kb(tmp_path)
    connection = await aiosqlite.connect(":memory:", isolation_level=None)
    try:
        service = await real_service(root, connection)
        session = await service.create_session(COOKING_AGENT, objective="a rub")

        await run_turn(service, session.session_id, ADVERSARIAL_MESSAGE)

        # (a) the file validates clean — the adversarial payload is present, but inert.
        text = read(root, session.file_path)
        findings = validate_content(root, session.file_path, text)
        assert not has_errors(findings), findings
        assert "> ## Synthesis" in text
        assert "> ## Distillation" in text

        # (b) a second, ordinary turn appends after the first — inside the genuine ## Record
        # section, never mistaking the quoted fake for the real ## Synthesis boundary.
        await run_turn(service, session.session_id, "an ordinary follow-up")
        text = read(root, session.file_path)
        record_start = genuine_heading_index(text, "## Record")
        synthesis_start = genuine_heading_index(text, "## Synthesis")
        assert record_start < synthesis_start
        section = text[record_start:synthesis_start]
        assert "an ordinary follow-up" in section
        assert "reply 2: an ordinary follow-up" in section
        findings = validate_content(root, session.file_path, text)
        assert not has_errors(findings), findings

        # (c) write_synthesis afterwards lands its text in the GENUINE ## Synthesis section, with
        # the quoted fake left untouched, still inside ## Record.
        fresh = await service.get_session(session.session_id)
        SessionFileWriter(root).write_synthesis(fresh, "The real synthesis.")
        text = read(root, session.file_path)
        synthesis_start = genuine_heading_index(text, "## Synthesis")
        distillation_start = genuine_heading_index(text, "## Distillation")
        synthesis_section = text[synthesis_start:distillation_start]
        assert "The real synthesis." in synthesis_section
        assert "> ## Synthesis" in text[:synthesis_start]  # the quoted fake, still in ## Record
        assert "> ## Distillation" in text[:synthesis_start]

        # (d) the genuine ## Distillation stays last — no fake, quoted or not, is ever mistaken
        # for a real top-level section heading.
        headings = re.findall(r"(?m)^## \w+$", text)
        assert headings == ["## Experts", "## Record", "## Synthesis", "## Distillation"]
        findings = validate_content(root, session.file_path, text)
        assert not has_errors(findings), findings
    finally:
        await connection.close()


# --------------------------------------------------------------------------------------
# § the write lands before the wire sees RunEnd — fix round 1, finding 2 (ordering guarantee)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_record_is_already_written_when_run_end_is_observed_on_the_wire(
    tmp_path: Path,
) -> None:
    """Pins the ordering ``RuntimeService._observe_session``'s own docstring promises: the write
    happens *before* the event is yielded downstream, so a subscriber that has just observed
    ``RunEnd`` is guaranteed the record already holds it — never a race the subscriber could lose.

    Iterates the subscription one event at a time (unlike ``run_turn``, which drains the whole
    stream into a list before any assertion runs) so the file is checked at the exact moment
    ``RunEnd`` arrives on the wire, not after the run's own task has had a further chance to run.
    A mutation that yields the event before landing the turn leaves every other test in this
    module green — nothing else iterates event-by-event — which is why this one exists.
    """
    root = kb(tmp_path)
    connection = await aiosqlite.connect(":memory:", isolation_level=None)
    try:
        service = await real_service(root, connection)
        session = await service.create_session(COOKING_AGENT, objective="a rub")

        subscription = await service.start_session_run(session.session_id, "how hot?")
        saw_run_end = False
        async for event in subscription.events:
            if isinstance(event, RunEnd):
                saw_run_end = True
                text = read(root, session.file_path)
                assert "how hot?" in text
                assert "reply 1: how hot?" in text
                break
        assert saw_run_end
    finally:
        await connection.close()


# --------------------------------------------------------------------------------------
# § best-effort: the record write never breaks a completed run
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_record_write_failure_is_logged_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """ "the run already succeeded, the record is bookkeeping" (Task 8's own brief): a broken
    ``append_record`` must not surface to the caller draining the run's events — the run already
    finished successfully by the time this write is attempted."""

    def boom(self: SessionFileWriter, session: Any, entry_md: str) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(SessionFileWriter, "append_record", boom)

    root = kb(tmp_path)
    connection = await aiosqlite.connect(":memory:", isolation_level=None)
    try:
        service = await real_service(root, connection)
        session = await service.create_session(COOKING_AGENT, objective="a rub")

        with caplog.at_level("WARNING", logger="pkb.service.runtime"):
            await run_turn(service, session.session_id, "how hot?")  # must not raise

        assert "could not land the turn" in caplog.text
    finally:
        await connection.close()
