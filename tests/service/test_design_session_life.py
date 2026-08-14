"""The proves-itself test (Task 9 of ``docs/superpowers/plans/2026-08-14-phase2-sessions.md``).

Written to pass, not TDD-red-first: this is Phase 2's living end-to-end proof that the session
machinery Tasks 3-8 built matches ``DESIGN.md`` §2.7's own words for "one file per session, for its
whole life" — driven through the real API test client and a scripted fake model, never through a
stub that never touches the store, the file or the wire.

One session lives through all seven stages the design names, in order: created on a Topic Expert
with an objective, a channel attached to it, ``/name`` renaming it (file moved, title rewritten,
the attached channel retitled over a real ``TelegramChannelNotifier`` and a fake-transport Bot API —
the same "fake-transport-visible surface" ``tests/server/test_telegram_topics.py`` exercises for
every other Telegram send), one worked turn landing in the record, ``/close`` entering the learning
queue and detaching every channel, ``/end`` sealing the file, and a final read of the file's own
section order against §2.7's own sentence: "The sections run in the order the life does: the
objective and the experts, the running record, the synthesis, and the distillation" (S-31).

Every session-affecting call goes through ``client.post``/``client.get`` over a real ``RuntimeService``
— real ``:memory:`` SQLite, a real ``tmp_path`` knowledge base with one scaffolded topic — except
channel attachment, which ``DESIGN.md`` §2 and Task 7's own route set never gave an HTTP verb (S-15's
seven commands are ``/channels``, ``/name``, ``/close``, ``/end`` and three more that never include
"attach"; attaching *is* one of the ways a channel finds a session, S-14, and Task 7 built it as a
service method for that reason). That one step calls ``RuntimeService.attach_channel`` directly, on
the same service instance the test client's app was opened with, the way
``test_close_detaches_every_attached_channel_s17`` in ``tests/server/test_session_routes.py`` already
does over a stub — proven safe here over a *real* ``aiosqlite`` connection too, because
``aiosqlite.Connection._execute`` binds each call's future to whichever loop is running when it is
awaited (``future = asyncio.get_event_loop().create_future()``), never to the loop that opened the
connection, so a synchronous ``TestClient`` call from the app's own background loop and a bare
``asyncio.run(...)`` call from the test's main thread never collide.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from starlette.testclient import TestClient

from pkb.contracts import AgentDescriptor, AgentEvent, MessageComplete, RunEnd
from pkb.core.errors import has_errors
from pkb.core.frontmatter import parse as parse_frontmatter
from pkb.core.models import FileClass, FileRole
from pkb.core.paths import LIBRARIAN_AGENT_ID, classify
from pkb.core.scaffold import scaffold_topic
from pkb.core.validation import validate_content
from pkb.server.app import create_app
from pkb.server.telegram import Channel, TelegramChannelNotifier, channel_ref
from pkb.service.runtime import RuntimeService
from pkb.service.session_file import SessionFileSealedError, SessionFileWriter
from pkb.service.sessions import Session

BASE_URL = "http://127.0.0.1:8000"
TODAY = date(2026, 8, 14)

COOKING_AGENT = "topic/cooking"
"""The Topic Expert the session opens on — Task 9's brief names one explicitly."""

REPLY = "pull the 12lb brisket at 203F internal, then rest it an hour before slicing."
"""The scripted model's one worked reply — a fixed string this test can grep the file for."""

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
]


class ScriptedModel:
    """The "scripted fake model" Task 9 asks for — one ``MessageComplete`` + ``RunEnd`` pair, always
    :data:`REPLY`, mirroring ``ScriptedRuntime`` in ``tests/service/test_session_record.py`` (Task
    8's own real-service harness) rather than inventing a second discipline for the same shape."""

    db_path = Path("never-opened.sqlite")

    def list_agents(self) -> Any:
        return CATALOG

    def run(self, agent_id: str, thread_id: str, message: str, **_: Any) -> Any:
        async def stream() -> AsyncIterator[AgentEvent]:
            yield MessageComplete(run_id="r1", agent_id=agent_id, text=REPLY)
            yield RunEnd(run_id="r1", final_text=REPLY)

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


class FakeTelegramApi:
    """The fake-transport surface (mirrors ``FakeBotApi`` in
    ``tests/server/test_telegram_topics.py``): every :class:`~pkb.server.telegram_api.BotApi` call
    implemented, per that Protocol's own rule ("every method here has to be implemented by every
    fake in the suite"), but only :meth:`edit_forum_topic` — the one call
    ``TelegramChannelNotifier.retitle`` ever makes — is exercised or recorded; the other eight exist
    solely so this class satisfies the Protocol structurally under strict mypy.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str]] = []

    async def get_me(self) -> Mapping[str, Any]:
        raise NotImplementedError

    async def get_updates(
        self, offset: int | None, *, timeout: int = 0
    ) -> Sequence[Mapping[str, Any]]:
        raise NotImplementedError

    async def create_forum_topic(self, chat_id: int, name: str) -> Mapping[str, Any]:
        raise NotImplementedError

    async def edit_forum_topic(self, chat_id: int, topic_id: int, name: str) -> None:
        self.calls.append((chat_id, topic_id, name))

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: Sequence[Sequence[Mapping[str, str]]] | None = None,
        topic_id: int = 0,
    ) -> Mapping[str, Any]:
        raise NotImplementedError

    async def send_document(
        self,
        chat_id: int,
        filename: str,
        content: bytes,
        caption: str = "",
        *,
        topic_id: int = 0,
    ) -> Mapping[str, Any]:
        raise NotImplementedError

    async def answer_callback(
        self, callback_id: str, text: str = "", *, show_alert: bool = False
    ) -> None:
        raise NotImplementedError

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        raise NotImplementedError

    async def clear_keyboard(self, chat_id: int, message_id: int) -> None:
        raise NotImplementedError


def _kb(tmp_path: Path) -> Path:
    """A real KB with one scaffolded topic — ``Cooking`` — enough for a Topic-Expert session."""
    root = tmp_path / "KB"
    root.mkdir()
    scaffold_topic(
        root, "Cooking", title="Cooking", description="Home cooking", today=TODAY, regenerate=False
    )
    return root


@contextlib.contextmanager
def _real_client(
    kb_root: Path, telegram_api: FakeTelegramApi
) -> Iterator[tuple[TestClient, RuntimeService]]:
    """A real ``RuntimeService`` over a real SQLite connection, opened once for the app's lifespan
    and captured through ``holder`` so the test can also reach ``attach_channel`` directly (module
    docstring) — mirrors ``real_client``/``_real_service`` in ``tests/server/test_session_routes.py``,
    with the notifier wired to a real ``TelegramChannelNotifier`` before the app ever serves a
    request, which that module's own fixture leaves at the default ``None``.
    """
    holder: list[RuntimeService] = []

    @contextlib.asynccontextmanager
    async def _service() -> AsyncIterator[RuntimeService]:
        connection = await aiosqlite.connect(":memory:", isolation_level=None)
        try:
            service = RuntimeService(ScriptedModel(), connection, kb_root=kb_root)
            await service.setup()
            service.notifier = TelegramChannelNotifier(telegram_api)
            holder.append(service)
            yield service
        finally:
            await connection.close()

    def opener() -> Any:
        return _service()

    with TestClient(create_app(opener), base_url=BASE_URL) as client:
        yield client, holder[0]


def test_a_session_lives_its_whole_life_through_the_api(tmp_path: Path) -> None:
    """``DESIGN.md`` §2.7: "A session keeps one file... for its whole life," through every stage
    §2.6 and §2.7 name, driven end to end through the real routes.
    """
    kb_root = _kb(tmp_path)
    telegram_api = FakeTelegramApi()

    with _real_client(kb_root, telegram_api) as (client, service):
        # --------------------------------------------------------------------------------
        # 1. Create on a Topic Expert with an objective (S-1, S-9, S-27) — the file exists,
        #    classifies FileRole.SESSION, and validates clean.
        # --------------------------------------------------------------------------------
        created = client.post(
            f"/agents/{COOKING_AGENT}/sessions",
            json={"objective": "a rub that doesn't burn above 250"},
        )
        assert created.status_code == 201
        session = created.json()["session"]
        session_id = session["session_id"]
        assert session["agent_id"] == COOKING_AGENT
        assert session["state"] == "open"
        rel_path = session["file_path"]
        assert rel_path == f"sessions/{session['name']}.md"

        original = kb_root / rel_path
        assert original.exists()
        role, file_class = classify(kb_root, original)
        assert (role, file_class) == (FileRole.SESSION, FileClass.AUTHORED)
        findings = validate_content(kb_root, rel_path, original.read_text(encoding="utf-8"))
        assert not has_errors(findings), findings

        # --------------------------------------------------------------------------------
        # 2. Attach a channel (S-6, S-14) — the service-level call, since attaching is not one of
        #    the seven commands (S-15) and carries no HTTP verb of its own (module docstring).
        # --------------------------------------------------------------------------------
        channel = channel_ref(Channel(chat_id=770001, topic_id=71))
        asyncio.run(service.attach_channel(session_id, channel))
        assert asyncio.run(service.session_channels(session_id)) == [channel]

        # --------------------------------------------------------------------------------
        # 3. /name renames (S-16) — file moved, title rewritten, the attached channel retitled
        #    over the real TelegramChannelNotifier and its fake-transport Bot API.
        # --------------------------------------------------------------------------------
        renamed_response = client.post(f"/sessions/{session_id}/name", json={"name": "Sear Timing"})
        assert renamed_response.status_code == 200
        renamed = renamed_response.json()["session"]
        assert renamed["name"] == "sear-timing"
        assert renamed["file_path"] == "sessions/sear-timing.md"

        moved = kb_root / renamed["file_path"]
        assert not original.exists()
        assert moved.exists()
        doc = parse_frontmatter(moved.read_text(encoding="utf-8"))
        assert doc.meta is not None
        assert doc.meta.title == renamed["file_path"]
        assert telegram_api.calls == [(770001, 71, "sear-timing")]

        # --------------------------------------------------------------------------------
        # 4. One worked turn (S-28, S-30) — the record holds the exchange, and the expert's
        #    topic.* tag is in frontmatter.
        # --------------------------------------------------------------------------------
        message = "how long for a 12lb brisket?"
        run_response = client.post(f"/sessions/{session_id}/runs", json={"message": message})
        assert run_response.status_code == 200

        after_turn = moved.read_text(encoding="utf-8")
        record_start = after_turn.index("## Record")
        synthesis_start = after_turn.index("## Synthesis")
        record_section = after_turn[record_start:synthesis_start]
        assert message in record_section
        assert REPLY in record_section
        assert "topic.cooking" in after_turn.split("---", 2)[1]
        turn_findings = validate_content(kb_root, renamed["file_path"], after_turn)
        assert not has_errors(turn_findings), turn_findings

        # --------------------------------------------------------------------------------
        # 5. /close (S-17, S-20, S-25/P4) — the session enters the learning queue and every
        #    attached channel is detached.
        # --------------------------------------------------------------------------------
        closed_response = client.post(f"/sessions/{session_id}/close")
        assert closed_response.status_code == 200
        assert closed_response.json()["session"]["state"] == "closed"

        queue = client.get("/sessions", params={"state": "closed"}).json()["sessions"]
        assert session_id in [row["session_id"] for row in queue]
        assert asyncio.run(service.session_channels(session_id)) == []

        # --------------------------------------------------------------------------------
        # 6. /end (S-22, S-24/P3) — the sealed marker lands, and a further run AND a further
        #    write are both refused.
        # --------------------------------------------------------------------------------
        ended_response = client.post(f"/sessions/{session_id}/end")
        assert ended_response.status_code == 200
        assert ended_response.json()["session"]["state"] == "ended"

        blocked_run = client.post(
            f"/sessions/{session_id}/runs", json={"message": "one more thing"}
        )
        assert blocked_run.status_code == 409

        sealed_session: Session = asyncio.run(service.get_session(session_id))
        assert sealed_session.state == "ended"
        with pytest.raises(SessionFileSealedError):
            SessionFileWriter(kb_root).append_record(sealed_session, "### Turn\n\nrefused")

        # --------------------------------------------------------------------------------
        # 7. Section order matches DESIGN §2.7's own life: "The sections run in the order the
        #    life does: the objective and the experts, the running record, the synthesis, and
        #    the distillation" (S-31) — with the turn's own exchange sitting inside ## Record,
        #    between it and ## Synthesis. The three command markers (Renamed, Closed, Ended)
        #    land after the four fixed sections, one per command, in the order this test issued
        #    them (S-29): rename, then close, then end.
        # --------------------------------------------------------------------------------
        final_text = moved.read_text(encoding="utf-8")
        headings = re.findall(r"(?m)^## \w+$", final_text)
        assert headings == [
            "## Experts",
            "## Record",
            "## Synthesis",
            "## Distillation",
            "## Renamed",
            "## Closed",
            "## Ended",
        ]
        final_findings = validate_content(kb_root, renamed["file_path"], final_text)
        assert not has_errors(final_findings), final_findings
