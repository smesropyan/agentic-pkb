"""The wire, frame by frame (SS-3 … SS-13).

Almost every test here is a pure function over :mod:`pkb.server.sse`, because framing is where a
transport bug is cheapest to catch and most expensive to ship: an SSE stream has no schema
negotiation, no version handshake and no error channel of its own. A frame that names an event
wrongly, drops a field, or reuses a sequence number is simply believed by every client, in every
language, forever.

The three tests that are *not* pure run a scripted stream through :class:`~pkb.service.runs.
RunSupervisor` by way of ``tests.server.stub.StubService``, because "exactly one terminal frame"
(SS-7) and "fan-out frames interleave" (SS-12) are properties of the composed pipe — supervisor into
encoder — and a test that hands the encoder a list it wrote itself would be asserting its own
fixture.
"""

from __future__ import annotations

import ast
import dataclasses
import itertools
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sse_starlette import ServerSentEvent

from pkb import contracts as contracts_module
from pkb.contracts import (
    EVENT_NAMES,
    RUN_STARTED_EVENT,
    ActionView,
    AgentEvent,
    ApprovalRequest,
    InterruptEvent,
    MessageComplete,
    MessageDelta,
    RunEnd,
    RunError,
    RunHandle,
    SubagentEnd,
    SubagentStart,
    ToolEnd,
    ToolStart,
    expert_thread_id,
)
from pkb.server.sse import ENVELOPE_KEYS, SseEncoder, event_name
from tests.server.stub import AGENTS, COOKING, GRILLING, LIBRARIAN, StubService

RUN = "run-1"
THREAD = "t-1"
HANDLE = RunHandle(run_id=RUN, agent_id=LIBRARIAN, thread_id=THREAD)
CATALOG: tuple[str, ...] = tuple(descriptor.agent_id for descriptor in AGENTS)

DIFF = "--- a/topics/Cooking/notes/steak.md\n+++ b/topics/Cooking/notes/steak.md\n-old\n+new\n"

SERVER_SOURCES = sorted(Path(contracts_module.__file__).parent.joinpath("server").glob("*.py"))


# --------------------------------------------------------------------------------------
# Fixtures — one of every event kind, and the two ways to get frames
# --------------------------------------------------------------------------------------


def approval(
    agent_id: str = COOKING, *, thread_id: str | None = None, interrupt_id: str = "i-1"
) -> ApprovalRequest:
    """One approval, parked on the thread that raised it (SS-11's shape, LB-16's thread)."""
    return ApprovalRequest(
        interrupt_id=interrupt_id,
        agent_id=agent_id,
        thread_id=expert_thread_id(THREAD, agent_id) if thread_id is None else thread_id,
        actions=(
            ActionView(
                tool="write_file",
                args={"path": "topics/Cooking/notes/steak.md"},
                description=DIFF,
                allowed_decisions=("approve", "reject"),
                reason="breadth-approval",
            ),
        ),
    )


SAMPLES: tuple[AgentEvent, ...] = (
    MessageDelta(run_id=RUN, agent_id=LIBRARIAN, text="one\ntwo"),
    MessageComplete(run_id=RUN, agent_id=LIBRARIAN, text="filed under Cooking"),
    ToolStart(run_id=RUN, agent_id=COOKING, tool="write_file", summary="steak.md"),
    ToolEnd(run_id=RUN, agent_id=COOKING, tool="write_file", summary="steak.md", error=False),
    SubagentStart(run_id=RUN, agent_id=COOKING),
    SubagentEnd(run_id=RUN, agent_id=COOKING, status="ok"),
    InterruptEvent(run_id=RUN, request=approval()),
    RunEnd(run_id=RUN, final_text="filed"),
    RunError(run_id=RUN, message="provider timeout", retryable=True),
)
"""One instance of each of the nine kinds, in the union's own order."""


def encoder() -> SseEncoder:
    return SseEncoder(HANDLE, CATALOG)


def payload(frame: ServerSentEvent) -> dict[str, Any]:
    """The frame's ``data:`` line, parsed — what a client actually receives."""
    body = json.loads(str(frame.data))
    assert isinstance(body, dict)
    return body


def encode_all(events: Sequence[AgentEvent]) -> list[ServerSentEvent]:
    """``run.started`` plus one frame per event, through a single encoder."""
    enc = encoder()
    return [enc.started(), *(enc.event(event) for event in events)]


async def stream_frames(events: Sequence[AgentEvent]) -> list[ServerSentEvent]:
    """The composed pipe: a scripted run through the supervisor, out through the encoder.

    Everything ``_stream`` in ``routes.py`` does, minus HTTP — so a terminal frame the supervisor
    synthesises (AP-11) is counted the same way a client would count it.
    """
    service = StubService(events=list(events))
    subscription = await service.start_run(THREAD, "where does this go?")
    enc = SseEncoder(subscription.handle, CATALOG)
    frames = [enc.started()]
    async for event in subscription.events:
        frames.append(enc.event(event))
    return frames


def names(frames: Sequence[ServerSentEvent]) -> list[str]:
    return [str(frame.event) for frame in frames]


def identifiers(path: Path) -> set[str]:
    """Every name a module's *code* uses — docstrings and comments excluded by construction.

    Grep over source text cannot tell "this module explains why it does not deduplicate" from "this
    module deduplicates", and both SS-5 and SS-13 are asserted by absence.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            found.add(node.name)
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(node.arg)
    return found


# --------------------------------------------------------------------------------------
# § the name table is total (SS-3)
# --------------------------------------------------------------------------------------


def test_a_removed_row_fails_at_import_ss3() -> None:
    """Totality is enforced at import, not by review, and this is the enforcement.

    ``pkb.contracts`` asserts the table covers the union while it is being imported, so a tenth
    event kind added without a name is an ImportError at daemon startup rather than a frame that
    vanishes on the floor months later. Re-executing the module with one row deleted is the only way
    to prove that guard still bites: delete the assertion and every other test in this file still
    passes.
    """
    source = Path(contracts_module.__file__).read_text(encoding="utf-8")
    doctored = source.replace('        RunError: "run.error",\n', "", 1)
    assert doctored != source, "the RunError row moved; this test must follow it"

    name = "pkb._contracts_with_a_missing_row"
    module = ModuleType(name)
    module.__file__ = contracts_module.__file__
    sys.modules[name] = module  # `dataclass` resolves annotations through sys.modules
    try:
        with pytest.raises(AssertionError, match="RunError"):
            exec(compile(doctored, "<doctored contracts>", "exec"), module.__dict__)
    finally:
        del sys.modules[name]


def test_every_kind_round_trips_through_its_wire_name_ss3() -> None:
    """Encode nine kinds, read the names back off the wire, land on the same nine classes.

    The table is shared by the encoder and by the TUI's decoder precisely so this round trip holds;
    a decoder with its own copy would drift on the first rename and mis-dispatch an event kind
    rather than fail.
    """
    decode = {name: kind for kind, name in EVENT_NAMES.items()}
    assert {type(event) for event in SAMPLES} == set(EVENT_NAMES)

    for event in SAMPLES:
        frame = encoder().event(event)
        wire = str(frame.event)
        assert wire == EVENT_NAMES[type(event)]
        assert decode[wire] is type(event)
        assert payload(frame)["type"] == wire


def test_an_unnamed_event_raises_rather_than_defaulting_ss3() -> None:
    """No fallback name, no ``event: unknown``, no silent drop.

    A default would turn "the union grew and nobody updated the table" into a frame clients cannot
    dispatch on — the exact failure the import-time assertion exists to make loud.
    """

    @dataclasses.dataclass(frozen=True, slots=True)
    class Invented:
        run_id: str

    with pytest.raises(KeyError):
        event_name(Invented(run_id=RUN))  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# § the payload: one line, flat envelope, no collisions (SS-4)
# --------------------------------------------------------------------------------------


def test_data_is_one_line_even_when_the_payload_has_newlines_ss4() -> None:
    """Multi-line ``data:`` framing never arises, because JSON escapes the newlines first.

    An approval's ``description`` carries a rendered unified diff (RT-34) and a message delta can
    contain anything the model wrote. Emitted raw, either would be split across several ``data:``
    lines, and a client that reassembles them wrongly — or a proxy that reframes them — silently
    changes the content of the diff a human is about to approve.
    """
    for frame in encode_all(SAMPLES):
        wire = frame.encode().decode()
        data_lines = [line for line in wire.split("\r\n") if line.startswith("data: ")]
        assert len(data_lines) == 1
        assert "\n" not in data_lines[0] and "\r" not in data_lines[0]

    gate = encoder().event(InterruptEvent(run_id=RUN, request=approval()))
    assert "\\n" in str(gate.data), "the newlines are escaped, not stripped"
    assert payload(gate)["request"]["actions"][0]["description"] == DIFF


def test_the_payload_is_the_envelope_plus_exactly_the_dataclass_fields_ss4() -> None:
    """Flat, and nothing extra — so a frame is matchable to its dataclass with no mapping table.

    A client reads ``type`` and then reads the fields of the class of that name in
    ``pkb.contracts``. An unexpected key means the two have diverged; a missing one means a client
    written against the seam crashes on a field the wire never sent.
    """
    for event in SAMPLES:
        body = payload(encoder().event(event))
        expected = set(ENVELOPE_KEYS) | {f.name for f in dataclasses.fields(event)}
        if isinstance(event, RunEnd):
            expected |= {"status"}  # SS-9, an envelope field on an unmodified dataclass
        if isinstance(event, RunError):
            # AP-11/SS-15: a terminal error carries the same machine `code` the status table would
            # have used, plus SS-9's status — so a client tells a cancellation from a provider
            # failure by branching, never by matching the sentence in `message`.
            expected |= {"status", "code"}
        assert set(body) == expected, f"{type(event).__name__} payload keys"


def test_no_envelope_key_collides_with_a_field_across_the_union_ss4() -> None:
    """Two keys appear in both the envelope and the dataclasses, and both must *mean* the same.

    ``run_id`` and ``agent_id`` are deliberately shared: the envelope's copy is the authoritative
    one and the dataclass's copy says the same thing. What this pins is that no *third* meaning ever
    arrives under one of those names — an ``agent_id`` that is the envelope's on one kind and the
    dataclass's on another is a bug nobody can see, because both are plausible strings.

    ``status`` gets the same treatment from the other side: SS-9 adds it to ``run.end``, and
    ``SubagentEnd`` already has a field by that name meaning something else entirely. They must
    never land on one frame.
    """
    fields_by_kind = {kind: {f.name for f in dataclasses.fields(kind)} for kind in EVENT_NAMES}
    shared = set(ENVELOPE_KEYS) & set().union(*fields_by_kind.values())
    assert shared == {"run_id", "agent_id"}

    for event in SAMPLES:
        body = payload(encoder().event(event))
        own = fields_by_kind[type(event)]
        if "run_id" in own:
            assert body["run_id"] == event.run_id == HANDLE.run_id
        if "agent_id" in own:
            assert body["agent_id"] == event.agent_id  # type: ignore[union-attr]

    assert "status" not in fields_by_kind[RunEnd], "SS-9's status is an envelope field"
    subagent = payload(encoder().event(SubagentEnd(run_id=RUN, agent_id=COOKING, status="error")))
    assert subagent["status"] == "error", "the subagent's own status, unshadowed"
    assert payload(encoder().event(RunEnd(run_id=RUN, final_text="")))["status"] == "completed"


def test_every_frame_carries_the_whole_envelope_ss4() -> None:
    """Five keys, on every frame, including the two Layer 3 authors itself.

    A client that has to reconstruct ``run_id`` or ``thread_id`` from context has to keep state
    across frames, and state kept across a stream is state that is wrong after a reattach.
    """
    for frame in encode_all(SAMPLES):
        body = payload(frame)
        assert set(ENVELOPE_KEYS) <= set(body)
        assert body["run_id"] == RUN
        assert isinstance(body["seq"], int)
        assert body["type"] and body["thread_id"] and body["agent_id"]


# --------------------------------------------------------------------------------------
# § seq, and the absent retry (SS-5)
# --------------------------------------------------------------------------------------


def test_seq_is_zero_to_n_with_no_gaps_and_matches_the_id_field_ss5() -> None:
    """The ``id:`` field and the envelope's ``seq`` are one number, counted from 0.

    A reattaching client replays from ``seq 0`` of the run in flight (AP-9), so a gap is not a
    cosmetic defect — it is a client that waits forever for a frame that was never numbered, or
    replays a buffer it cannot align.
    """
    frames = encode_all(SAMPLES)
    assert [int(str(frame.id)) for frame in frames] == list(range(len(frames)))
    assert [payload(frame)["seq"] for frame in frames] == list(range(len(frames)))
    assert payload(frames[0])["seq"] == 0


def test_seq_is_a_within_run_cursor_not_a_global_one_ss5() -> None:
    """Two runs both start at 0, and neither can see the other's count.

    A global cursor would make ``seq`` meaningless as a replay offset the moment a second thread ran
    concurrently, which on a daemon serving a TUI and Telegram at once is the normal case.
    """
    first = encode_all(SAMPLES)
    second = encode_all(SAMPLES[:3])
    assert payload(second[0])["seq"] == 0
    assert [payload(f)["seq"] for f in second] == [0, 1, 2, 3]
    assert payload(first[-1])["seq"] == len(SAMPLES)


def test_no_retry_field_is_ever_written_ss5() -> None:
    """These streams answer a POST, which ``EventSource`` cannot issue — so no auto-reconnect.

    A ``retry:`` field invites a transport-level reconnection, and the only thing a browser could
    reconnect to here is the POST that *started the run*. That would silently start a second run
    against a knowledge base. Reconnection is an explicit client act against
    ``GET /threads/{id}/events`` (RO-17), and nothing on the wire may suggest otherwise.
    """
    for frame in encode_all(SAMPLES):
        assert frame.retry is None
        assert b"retry:" not in frame.encode()

    for path in SERVER_SOURCES:
        assert "retry" not in identifiers(path), f"{path.name} names a retry field"


def test_the_wire_bytes_are_crlf_in_the_fixed_field_order_ss5() -> None:
    """id, then event, then data, separated by CRLF, terminated by a blank line.

    The library owns framing and the encoder never formats a string itself; this asserts what a
    socket receives, because "it round-trips through our own parser" is exactly the test that would
    still pass if we shipped LF and confused a strict proxy.
    """
    frame = encoder().started()
    wire = frame.encode()
    body = json.dumps(payload(frame), separators=(",", ":"), ensure_ascii=False)
    assert wire == f"id: 0\r\nevent: {RUN_STARTED_EVENT}\r\ndata: {body}\r\n\r\n".encode()
    assert b"\n" not in wire.replace(b"\r\n", b"")


# --------------------------------------------------------------------------------------
# § exactly one terminal frame (SS-7) and frame 0 (SS-8)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_direct_run_carries_one_terminal_frame_and_it_is_last_ss7() -> None:
    """A stream that closes without a terminal frame means *outcome unknown*, not success.

    A client that assumed otherwise would tell a human their note was filed on the strength of a
    dropped connection. One terminal frame, last, is what makes "the connection ended" and "the run
    ended" distinguishable at all.
    """
    frames = await stream_frames(
        [
            MessageDelta(run_id=RUN, agent_id=LIBRARIAN, text="filing"),
            MessageComplete(run_id=RUN, agent_id=LIBRARIAN, text="filed"),
            RunEnd(run_id=RUN, final_text="filed"),
        ]
    )
    terminals = [name for name in names(frames) if name in {"run.end", "run.error"}]
    assert terminals == ["run.end"]
    assert names(frames)[-1] == "run.end"


@pytest.mark.asyncio
async def test_a_fanout_carries_one_terminal_frame_and_it_is_last_ss7() -> None:
    """Two experts finish, and neither ending is a terminal frame for the run.

    Layer 2 swallows each expert's own ``run.end`` and folds them into the merge (RT-47), so a
    Librarian turn that fanned out to three experts must still produce exactly one. A client that
    counted ``run.end`` frames would otherwise close the stream after the first expert answered and
    lose the merged reply.
    """
    frames = await stream_frames(
        [
            SubagentStart(run_id=RUN, agent_id=COOKING),
            SubagentStart(run_id=RUN, agent_id=GRILLING),
            SubagentEnd(run_id=RUN, agent_id=COOKING, status="ok"),
            SubagentEnd(run_id=RUN, agent_id=GRILLING, status="ok"),
            MessageComplete(run_id=RUN, agent_id=LIBRARIAN, text="both filed"),
            RunEnd(run_id=RUN, final_text="both filed"),
        ]
    )
    assert [n for n in names(frames) if n.startswith("run.")] == [RUN_STARTED_EVENT, "run.end"]
    assert names(frames)[-1] == "run.end"


@pytest.mark.asyncio
async def test_an_error_run_carries_one_terminal_frame_and_it_is_last_ss7() -> None:
    """A failure is still exactly one ending, and it is the last thing on the stream.

    Errors are the case where a second terminal frame is most tempting — the run failed, so
    something also wants to say it ended. Two endings is a client that renders a failure and then
    overwrites it with a success.
    """
    frames = await stream_frames(
        [
            MessageDelta(run_id=RUN, agent_id=LIBRARIAN, text="thinking"),
            RunError(run_id=RUN, message="provider timeout", retryable=True),
        ]
    )
    assert [n for n in names(frames) if n in {"run.end", "run.error"}] == ["run.error"]
    assert names(frames)[-1] == "run.error"
    assert payload(frames[-1])["retryable"] is True


@pytest.mark.asyncio
async def test_a_stream_that_ends_without_an_ending_still_gets_one_ss7() -> None:
    """Layer 2 going quiet is not an outcome; the supervisor owes the client a terminal frame.

    This is the failure mode a cancelled run produces naturally, and the reason it is asserted here
    rather than only in the supervisor's own tests: SS-7 is a promise about the *stream*, and the
    stream is the encoder's output.
    """
    frames = await stream_frames([MessageDelta(run_id=RUN, agent_id=LIBRARIAN, text="…")])
    assert names(frames)[-1] == "run.error"
    assert [n for n in names(frames) if n in {"run.end", "run.error"}] == ["run.error"]


def test_frame_zero_is_run_started_with_exactly_the_run_handle_ss8() -> None:
    """The client holds the run id — hence the cancel — before the first token exists.

    ``run_id`` is server-minted, so without this frame a client that wants to cancel has to wait for
    an event to learn what to cancel, and a run that hangs before its first token is precisely the
    run a human wants to stop. The agent id is here for the same reason: the header renders before
    the answer starts.
    """
    frames = encode_all(SAMPLES)
    body = payload(frames[0])
    assert body["type"] == RUN_STARTED_EVENT
    assert body["seq"] == 0 and str(frames[0].id) == "0"
    assert set(body) - {"type", "seq"} == {f.name for f in dataclasses.fields(RunHandle)}
    assert body["run_id"] == RUN
    assert body["agent_id"] == LIBRARIAN
    assert body["thread_id"] == THREAD


# --------------------------------------------------------------------------------------
# § parked is not complete (SS-9)
# --------------------------------------------------------------------------------------


def test_the_run_end_dataclass_is_unmodified_ss9() -> None:
    """``status`` is an envelope field. The seam's dataclass stays what Layer 2 emits.

    Adding it to :class:`~pkb.contracts.RunEnd` would push a transport concern into the type Layer 2
    constructs, and Layer 2 has no way to fill it in — it does not know a graph interrupted.
    """
    assert {f.name for f in dataclasses.fields(RunEnd)} == {"run_id", "final_text"}


def test_a_cancelled_run_says_cancelled_on_its_terminal_frame_ss9_ap11() -> None:
    """SS-9's third status has no frame that can carry it, and AP-11's code never reaches the wire.

    A cancelled run emits no terminal event from Layer 2, so the supervisor synthesises
    ``RunError(message="the run was cancelled", retryable=True)`` — the exact value
    ``SseEncoder.status_for`` recognises. But ``event()`` only asks for a status when the event is a
    ``RunEnd``, and a cancelled run never produces one. The client is left distinguishing "the human
    cancelled" from "the provider timed out" by comparing an English sentence.
    """
    enc = encoder()
    enc.started()
    frame = enc.event(RunError(run_id=RUN, message="the run was cancelled", retryable=True))
    body = payload(frame)
    assert body["code"] == "cancelled"
    assert body["status"] == "cancelled"


# --------------------------------------------------------------------------------------
# § the derived thread (SS-10)
# --------------------------------------------------------------------------------------


def test_an_event_from_a_catalog_agent_gets_the_derived_thread_ss10() -> None:
    """A routed expert really does run on its own addressable thread (LB-14, D-6).

    Only ``InterruptEvent`` carries a thread id today, so without this every client — the TUI, the
    Telegram bot, an MCP consumer — would re-implement the ``<t>::<agent_id>`` derivation in its own
    language, and the first one to get it wrong would resume the wrong checkpoint.

    Un-marked at Task 10 (fix round 1): Task 2's sweep (``d6cc0ac``) caught this in the same
    ``@pytest.mark.superseded`` pass as its two ``InterruptEvent``-specific siblings below, but its
    own three events — ``SubagentStart``, ``MessageDelta``, ``ToolEnd`` — carry no gate and no
    interrupt; ``expert_thread_id`` fan-out labelling is live, general-purpose SSE wire protocol
    (SS-3's exhaustiveness keeps every kind of :data:`~pkb.contracts.AgentEvent` covered), untouched
    by Task 6's gate death and not waiting on any later phase either.
    """
    for event in (
        SubagentStart(run_id=RUN, agent_id=COOKING),
        MessageDelta(run_id=RUN, agent_id=COOKING, text="sear it"),
        ToolEnd(run_id=RUN, agent_id=COOKING, tool="write_file", summary="ok", error=False),
    ):
        body = payload(encoder().event(event))
        assert body["thread_id"] == expert_thread_id(THREAD, COOKING) == f"{THREAD}::{COOKING}"
        assert body["agent_id"] == COOKING


def test_an_experts_own_general_purpose_delegate_keeps_the_parent_thread_ss10() -> None:
    """``general-purpose`` is not a catalog id, and it must not be given a thread of its own.

    An expert delegating internally (RT-44) runs in a nested namespace under the *same* thread. A
    derivation that keyed on "is this the run's agent?" alone would mint ``<t>::general-purpose`` —
    a thread id that resolves to no agent, cannot be opened, and shares a checkpoint with nothing.
    The catalog gate is what makes the difference, so this test is the reason it exists.

    Un-marked at Task 10 (fix round 1) — see the sibling above for why: a ``MessageDelta`` from an
    expert's own internal delegate carries no gate, and the catalog-membership check this asserts is
    live regardless of the interrupt/gate system's fate.
    """
    body = payload(encoder().event(MessageDelta(run_id=RUN, agent_id="general-purpose", text="x")))
    assert body["thread_id"] == THREAD
    assert "general-purpose" not in CATALOG


# --------------------------------------------------------------------------------------
# § interleaving, preserved (SS-12)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fanout_frames_interleave_with_per_agent_order_preserved_ss12() -> None:
    """Three experts run concurrently on one run id, and Layer 3 reorders nothing.

    A client groups by ``agent_id`` and must assume no ordering *between* two agents — but within
    one ``(run_id, agent_id)`` Layer 2's order is exact. A transport that buffered per agent to make
    the stream look tidy would destroy the only property a client can actually depend on, and turn a
    live fan-out into three answers arriving at the end.
    """
    tokens = {COOKING: ("c1", "c2"), GRILLING: ("g1", "g2")}
    script: list[AgentEvent] = [
        SubagentStart(run_id=RUN, agent_id=COOKING),
        SubagentStart(run_id=RUN, agent_id=GRILLING),
        MessageDelta(run_id=RUN, agent_id=COOKING, text="c1"),
        MessageDelta(run_id=RUN, agent_id=GRILLING, text="g1"),
        MessageDelta(run_id=RUN, agent_id=COOKING, text="c2"),
        MessageDelta(run_id=RUN, agent_id=GRILLING, text="g2"),
        SubagentEnd(run_id=RUN, agent_id=GRILLING, status="ok"),
        SubagentEnd(run_id=RUN, agent_id=COOKING, status="ok"),
        RunEnd(run_id=RUN, final_text="merged"),
    ]
    frames = await stream_frames(script)
    bodies = [payload(frame) for frame in frames[1:]]  # drop run.started

    assert [b["agent_id"] for b in bodies] == [
        getattr(event, "agent_id", LIBRARIAN) for event in script
    ]
    for expert, said in tokens.items():
        own = [b for b in bodies if b["agent_id"] == expert]
        assert [b["type"] for b in own] == [
            "subagent.start",
            "message.delta",
            "message.delta",
            "subagent.end",
        ]
        assert [b["text"] for b in own if b["type"] == "message.delta"] == list(said)

    deltas = [b["agent_id"] for b in bodies if b["type"] == "message.delta"]
    assert any(a != b for a, b in itertools.pairwise(deltas)), "no interleaving at all"


@pytest.mark.asyncio
async def test_subagent_frames_bracket_a_branch_rather_than_nesting_ss12() -> None:
    """One start and one end per agent, and the pairs may overlap.

    A client that treated ``subagent.start``/``end`` as a stack would corrupt its own tree the first
    time two experts overlapped — which is the default, since ``fanout_limit`` is 3.
    """
    frames = await stream_frames(
        [
            SubagentStart(run_id=RUN, agent_id=COOKING),
            SubagentStart(run_id=RUN, agent_id=GRILLING),
            SubagentEnd(run_id=RUN, agent_id=COOKING, status="ok"),
            SubagentEnd(run_id=RUN, agent_id=GRILLING, status="error"),
            RunEnd(run_id=RUN, final_text="merged"),
        ]
    )
    bodies = [payload(frame) for frame in frames]
    for expert in (COOKING, GRILLING):
        assert [b["type"] for b in bodies if b["agent_id"] == expert] == [
            "subagent.start",
            "subagent.end",
        ]

    def at(kind: str, agent: str) -> int:
        return next(i for i, b in enumerate(bodies) if b["type"] == kind and b["agent_id"] == agent)

    # Grilling opens before Cooking closes: the brackets overlap, so they are not a stack.
    assert at("subagent.start", GRILLING) < at("subagent.end", COOKING)
    assert at("subagent.start", COOKING) < at("subagent.end", COOKING)
    assert bodies[at("subagent.end", GRILLING)]["status"] == "error", "the branch's own outcome"


# --------------------------------------------------------------------------------------
# § no second opinion (SS-13)
# --------------------------------------------------------------------------------------


def test_identical_events_are_not_coalesced_ss13() -> None:
    """Two identical deltas are two tokens, not one repeated.

    A model legitimately emits the same token twice in a row. Coalescing on equality would silently
    edit the assistant's text — the one thing a transport must never do.
    """
    enc = encoder()
    frames = [enc.event(MessageDelta(run_id=RUN, agent_id=LIBRARIAN, text="ha")) for _ in range(3)]
    assert [payload(f)["seq"] for f in frames] == [0, 1, 2]
    assert {payload(f)["text"] for f in frames} == {"ha"}


def test_no_module_in_the_server_keeps_a_dedupe_set_ss13() -> None:
    """Structural, because the behavioural test can only ever cover the cases someone thought of.

    ``pkb.server``'s whole job on the event path is envelope and encode. A ``seen`` set anywhere in
    it is a second deduplication pass by definition, wherever it was added and whatever it was for.
    """
    assert SERVER_SOURCES, "no server modules found — the glob is wrong"
    for path in SERVER_SOURCES:
        offenders = {name for name in identifiers(path) if "dedup" in name or "seen" in name}
        assert not offenders, f"{path.name} keeps {sorted(offenders)}"


# --------------------------------------------------------------------------------------
# Defects the Layer 4 spec found in Layer 3, 2026-08-08
# --------------------------------------------------------------------------------------


def test_a_terminal_error_carries_the_typed_code_the_supervisor_recorded_ss15() -> None:
    """SS-15 promises a mid-stream typed error keeps its machine `code`. It did not.

    `RunSupervisor._drive` published `RunError(message=str(exc))` and discarded the exception's
    type, so every failure reached the wire as an untyped `run_error` — and a client cannot tell
    "the thread is busy, wait and retry" from "this run died" without reading the sentence in
    `message`, which is exactly what RO-21 says clients must never do.
    """
    handle = RunHandle(run_id="run-1", agent_id="topic/cooking", thread_id="T1")
    encoder = SseEncoder(handle, (), {"run-1": "thread_busy"})

    frame = encoder.event(
        RunError(run_id="run-1", message="a run is already active", retryable=True)
    )

    body = json.loads(str(frame.data))
    assert body["code"] == "thread_busy"
    assert body["status"] == "error"


def test_a_cancelled_run_is_not_a_failure_ap11() -> None:
    """A cancellation gets its own code, so a client offers "try again" for one and not the other."""
    handle = RunHandle(run_id="run-2", agent_id="topic/cooking", thread_id="T1")

    frame = SseEncoder(handle).event(
        RunError(run_id="run-2", message="the run was cancelled", retryable=True)
    )

    body = json.loads(str(frame.data))
    assert (body["code"], body["status"]) == ("cancelled", "cancelled")
