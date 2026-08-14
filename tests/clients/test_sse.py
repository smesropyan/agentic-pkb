"""The wire, read back — :mod:`pkb.clients.sse` against the encoder it inverts (DC-1 … DC-17).

Every frame in this file is produced by the **real** :class:`~pkb.server.sse.SseEncoder`, and most
of them by the real ``_approval_payload``/``detail_payload`` builders besides. A decoder test whose
fixtures are hand-written strings asserts that the decoder agrees with the test author: the two
halves drift on the first rename and nothing fails until a human opens a thread. The only thing
worth pinning here is that the decoder is the encoder's inverse, so the encoder is the fixture.

The three properties this file exists to hold are each a defect that was found rather than a taste:

* the name table is **inverted from the seam**, so a tenth event kind is an ImportError at startup
  and no wire name is spelled anywhere in :mod:`pkb.clients` or :mod:`pkb.tui` (DC-1, SS-3);
* envelope stripping is **field-driven**, because ``status`` is a dataclass field on
  ``subagent.end`` and an envelope field on ``run.end`` — the one shape a key-driven decoder
  destroys, and it only shows up on a fan-out run (DC-2, decision K);
* a decoded frame is an **envelope plus an event**, because ``thread_id``, ``seq``, ``status`` and
  ``code`` belong to no dataclass, and a decoder returning a bare event renders a thread parked on a
  human decision as "done" (DC-3, DC-5, decision J).
"""

from __future__ import annotations

import ast
import dataclasses
import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args

import pytest
from sse_starlette import ServerSentEvent

from pkb import contracts as contracts_module
from pkb.clients.sse import TERMINAL, Frame, decode_frame, decode_request
from pkb.contracts import (
    CANCELLED_CODE,
    EVENT_NAMES,
    RUN_ERROR_CODE,
    RUN_STARTED_EVENT,
    RUN_STATUSES,
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
from pkb.server.routes import detail_payload
from pkb.server.sse import ENVELOPE_KEYS, SseEncoder
from pkb.service import Thread, ThreadDetail
from tests.server.stub import AGENTS, COOKING, GRILLING, LIBRARIAN

RUN = "run-1"
THREAD = "t-1"
HANDLE = RunHandle(run_id=RUN, agent_id=LIBRARIAN, thread_id=THREAD)
CATALOG: tuple[str, ...] = tuple(descriptor.agent_id for descriptor in AGENTS)

DIFF = "--- a/topics/Cooking/notes/steak.md\n+++ b/topics/Cooking/notes/steak.md\n-old\n+new\n"

SRC = Path(contracts_module.__file__).parent
CLIENT_SOURCES = sorted(
    [*SRC.joinpath("clients").rglob("*.py"), *SRC.joinpath("tui").rglob("*.py")]
)
WIRE_NAMES = frozenset({*EVENT_NAMES.values(), RUN_STARTED_EVENT})


# --------------------------------------------------------------------------------------
# Fixtures — one of every kind, encoded by the encoder the decoder must invert
# --------------------------------------------------------------------------------------


def approval(
    agent_id: str = COOKING,
    *,
    interrupt_id: str = "i-1",
    allowed: tuple[str, ...] = ("approve", "edit", "reject"),
) -> ApprovalRequest:
    """One approval, parked on the thread that raised it — a fan-out's derived thread (LB-16)."""
    return ApprovalRequest(
        interrupt_id=interrupt_id,
        agent_id=agent_id,
        thread_id=expert_thread_id(THREAD, agent_id),
        actions=(
            ActionView(
                tool="write_file",
                args={"file_path": "topics/Cooking/notes/steak.md", "content": "sear hot\n"},
                description=DIFF,
                allowed_decisions=allowed,  # type: ignore[arg-type]
                reason="breadth-approval",
            ),
            ActionView(
                tool="delete",
                args={"file_path": "topics/Cooking/notes/old.md"},
                description="Delete topics/Cooking/notes/old.md",
                allowed_decisions=("approve", "reject"),
                reason="delete",
            ),
        ),
    )


SAMPLES: tuple[AgentEvent, ...] = (
    MessageDelta(run_id=RUN, agent_id=LIBRARIAN, text="one\ntwo"),
    MessageComplete(run_id=RUN, agent_id=LIBRARIAN, text="filed under Cooking"),
    ToolStart(run_id=RUN, agent_id=COOKING, tool="write_file", summary="steak.md"),
    ToolEnd(run_id=RUN, agent_id=COOKING, tool="write_file", summary="steak.md", error=False),
    SubagentStart(run_id=RUN, agent_id=COOKING),
    SubagentEnd(run_id=RUN, agent_id=COOKING, status="failed"),
    InterruptEvent(run_id=RUN, request=approval()),
    RunEnd(run_id=RUN, final_text="filed"),
    RunError(run_id=RUN, message="provider timeout", retryable=True),
)
"""One instance of each of the nine kinds, in the union's own order."""


def encoder(**kwargs: Any) -> SseEncoder:
    return SseEncoder(HANDLE, CATALOG, **kwargs)


def decode(frame: ServerSentEvent) -> Frame:
    """What a client does with one frame off the wire: its ``event:`` name and its ``data:`` line."""
    decoded = decode_frame(str(frame.event), str(frame.data))
    assert decoded is not None, f"the decoder skipped {frame.event!r}"
    return decoded


def payload(frame: ServerSentEvent) -> dict[str, Any]:
    body = json.loads(str(frame.data))
    assert isinstance(body, dict)
    return body


def code_strings(source: str) -> set[str]:
    """Every string literal a module's **code** uses — docstrings and comments excluded.

    Comments never reach the AST; a docstring (module, class, function or the attribute docstrings
    this codebase leans on) is exactly a bare ``Expr`` holding a string constant, so dropping those
    leaves the strings the module actually computes with. Grep cannot tell "this module explains why
    it never spells a wire name" from "this module spells a wire name", and both DC-1 and DC-8 are
    asserted by absence.
    """
    tree = ast.parse(source)
    documentation = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in documentation
    }


def _is_ping(text: str) -> bool:
    """Whether a literal is the comment frame itself rather than a word that contains ``ping``."""
    return text.strip() in {"ping", ":ping"} or ": ping" in text


def thread_detail(request: ApprovalRequest) -> dict[str, Any]:
    """``GET /threads/{id}`` for a thread parked on ``request``, through the real route builder."""
    now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    thread = Thread(
        thread_id=request.thread_id,
        agent_id=request.agent_id,
        created_at=now,
        updated_at=now,
        origin_channel="tui",
        pending_interrupt_id=request.interrupt_id,
    )
    return detail_payload(ThreadDetail(thread=thread, pending=request))


# --------------------------------------------------------------------------------------
# § the round trip — the decoder is the encoder's inverse (DC-1, SS-3, SS-4)
# --------------------------------------------------------------------------------------


def test_every_event_kind_round_trips_to_an_equal_event_dc1() -> None:
    """Nine kinds out through the real encoder, nine equal kinds back — no exceptions, no drift.

    This is the whole contract between Layer 3 and Layer 4, and it is one nobody can check by eye:
    an SSE stream has no schema negotiation and no error channel, so a field the decoder drops or
    mis-names is simply believed, forever, by whichever screen renders it. Equality (not "the type
    is right") is the assertion, because the failures that actually happen are a lost ``retryable``
    flag, a coerced ``error: false``, and a nested ``ApprovalRequest`` rebuilt with a different
    thread id — each of which type-checks perfectly and changes what a human is shown.
    """
    enc = encoder()
    frames = [enc.event(event) for event in SAMPLES]
    decoded = [decode(frame) for frame in frames]

    assert [frame.event for frame in decoded] == list(SAMPLES)
    assert {type(frame.event) for frame in decoded} == set(get_args(AgentEvent)), (
        "the round trip must cover the whole union, or a kind can rot untested"
    )


@pytest.mark.superseded
def test_the_nested_approval_survives_the_round_trip_dc1() -> None:
    """The one frame a human acts on carries a whole object graph, and all of it must survive.

    ``interrupt`` is the only event with a nested dataclass, and every field of it decides what the
    human sees or where the answer goes: ``description`` is the server-rendered diff (RT-34) they
    read before saying yes, ``args`` is what the tool will be invoked with, ``allowed_decisions`` is
    which buttons exist, and ``thread_id`` is where the resume must be posted. A shallow decode that
    left ``request`` as a raw ``dict`` would still satisfy a type check on ``Frame`` and would fail
    only in the modal, on an irreversible write (D6).

    Superseded (Phase 5 rebuilds this): its vehicle is ``InterruptEvent``/``ApprovalRequest``, both
    retired with the interrupt/resume surface — no gate ever raises, so there is no nested object
    graph left for a frame to carry.
    """
    request = approval()
    decoded = decode(encoder().event(InterruptEvent(run_id=RUN, request=request)))

    assert isinstance(decoded.event, InterruptEvent)
    assert decoded.event.request == request
    assert decoded.event.request.actions[0].description == DIFF
    assert decoded.event.request.actions[0].args == request.actions[0].args
    assert decoded.event.request.thread_id == expert_thread_id(THREAD, COOKING)


def test_the_decoder_consumes_every_key_on_the_wire_ss4() -> None:
    """Nothing the encoder writes is left unread — the envelope is flat for exactly this reason.

    SS-4 flattens the envelope over the event's own fields so a frame maps onto a dataclass with no
    second table on the client side. That only works while every key is accounted for by one side or
    the other: a key that is neither an envelope key, nor a field of the target class, nor a
    terminal's ``status``/``code`` is a value Layer 3 is sending and Layer 4 is silently dropping.
    The shared keys must also *agree* — ``run_id`` and ``agent_id`` appear in both halves carrying
    the same value, and a third meaning arriving under one of those names is the failure to catch.
    """
    enc = encoder()
    for event, frame in [(event, enc.event(event)) for event in SAMPLES]:
        body = payload(frame)
        fields = {field.name for field in dataclasses.fields(event)}
        terminal = str(frame.event) in TERMINAL
        accounted = set(ENVELOPE_KEYS) | fields | ({"status", "code"} if terminal else set())
        assert set(body) <= accounted, f"{frame.event}: unread keys {set(body) - accounted}"

        for shared in ("run_id", "agent_id"):
            if shared in fields:
                assert body[shared] == getattr(event, shared)


def test_no_wire_name_is_spelled_in_either_client_package_dc1() -> None:
    """The table is inverted from the seam once; the client never writes a name down.

    ``EVENT_NAMES`` asserts its own totality at import, so a tenth :data:`AgentEvent` kind is an
    ImportError at startup. A client that keeps its own copy — or just one ``if name ==
    "message.delta"`` — converts that loud failure into an event class that quietly stops arriving,
    which from the outside is indistinguishable from a model that stopped emitting it. The scan is
    over code strings only: both packages *discuss* the names in their docstrings, and they should.
    """
    control = code_strings('name = "run.end"\n"""A docstring naming run.end and message.delta."""')
    assert control & WIRE_NAMES == {"run.end"}, "the scanner itself is broken"

    for path in CLIENT_SOURCES:
        spelled = code_strings(path.read_text(encoding="utf-8")) & WIRE_NAMES
        assert spelled == set(), f"{path.name} spells the wire name(s) {sorted(spelled)}"


def test_the_decoder_imports_neither_the_server_nor_a_transport_dc1() -> None:
    """The tempting shortcut — ``from pkb.server.sse import ENVELOPE_KEYS`` — is in-repo and correct.

    It is also what makes the TUI un-runnable without the daemon package installed and untestable
    against a wire it did not itself produce, and it drags ``sse_starlette`` and ``fastapi`` into a
    process whose whole job is to read text off a socket. A fresh interpreter is the only honest
    check: inside the test session every banned module is already imported by some other test.
    """
    proc = subprocess.run(
        [sys.executable, "-c", "import sys, pkb.clients.sse; print(sorted(sys.modules))"],
        capture_output=True,
        text=True,
        check=True,
    )
    loaded = set(ast.literal_eval(proc.stdout))
    banned = {
        "deepagents",
        "langgraph",
        "langchain",
        "langchain_core",
        "httpx",
        "httpx2",
        "textual",
        "rich",
        "fastapi",
        "starlette",
        "sse_starlette",
    }
    assert {module.split(".")[0] for module in loaded} & banned == set()
    assert {module for module in loaded if module.startswith(("pkb.agents", "pkb.server"))} == set()


# --------------------------------------------------------------------------------------
# § field-driven stripping — the shape a key-driven decoder destroys (DC-2, decision K)
# --------------------------------------------------------------------------------------


def test_a_key_driven_decoder_destroys_subagent_end_dc2() -> None:
    """``status`` means two unrelated things, and subtracting a key set loses the wrong one.

    On ``subagent.end`` it is a **dataclass field** — the delegate's outcome (LB-15). On ``run.end``
    and ``run.error`` it is an **envelope field** Layer 3 computes (SS-9). A decoder that builds its
    constructor arguments by subtracting the envelope key list — the form an implementer writes
    first, and the form SS-4's own wording invites — cannot serve both, and the frame it dies on is
    the delegate's. That is a crash on fan-out runs only: the case a TUI is hardest to exercise by
    hand, and the case the Librarian takes on every filing turn that touches a topic.
    """
    frame = encoder().event(SubagentEnd(run_id=RUN, agent_id=COOKING, status="failed"))
    body = payload(frame)
    key_driven = set(ENVELOPE_KEYS) | {"status", "code"}

    with pytest.raises(TypeError, match="SubagentEnd"):
        SubagentEnd(**{k: v for k, v in body.items() if k not in key_driven})

    decoded = decode(frame)
    assert decoded.event == SubagentEnd(run_id=RUN, agent_id=COOKING, status="failed")


@pytest.mark.superseded
def test_field_driven_stripping_keeps_the_terminal_status_off_the_dataclass_dc2() -> None:
    """The mirror image: ``run.end`` carries a ``status`` no dataclass has a slot for.

    ``RunEnd`` is deliberately unmodified — parked-or-done is Layer 3's computation, not Layer 2's,
    because ``astream`` returns normally when a graph interrupts. Keeping only keys that are fields
    of the target class is what lets one decoder handle both meanings without a per-event special
    case, and it is why a tenth event kind needs no client change at all.

    Superseded (Phase 5 rebuilds this): the whole "parked-or-done" rationale is the interrupt/resume
    surface — no gate means no run is ever left waiting, so ``status`` collapses to "completed" and
    there is no ``"interrupted"`` value left for this to prove flows through. The generic "envelope
    fields never land on the dataclass" shape is covered on the ``subagent.end`` arm by
    `test_a_key_driven_decoder_destroys_subagent_end_dc2`, which needs no interrupt to make its
    point.
    """
    enc = encoder()
    enc.event(InterruptEvent(run_id=RUN, request=approval()))
    decoded = decode(enc.event(RunEnd(run_id=RUN, final_text="filed")))

    assert decoded.event == RunEnd(run_id=RUN, final_text="filed")
    assert decoded.status == "interrupted"
    assert not hasattr(decoded.event, "status")


# --------------------------------------------------------------------------------------
# § dispatch is on the name, never the shape (DC-3)
# --------------------------------------------------------------------------------------


def test_message_delta_and_complete_are_told_apart_by_name_only_dc3() -> None:
    """Two kinds with byte-identical payloads that the TUI must treat as opposites.

    ``MessageDelta`` and ``MessageComplete`` have the same three fields, so no amount of looking at
    the payload can tell them apart — and TU-24 says one is appended to the streaming buffer while
    the other *replaces* it. A decoder that sniffed shape would pick one of them always: either the
    reply is rendered twice (delta buffer plus the final message) or it never finalizes and the
    spinner runs forever after the run has ended.
    """
    assert {f.name for f in dataclasses.fields(MessageDelta)} == {
        f.name for f in dataclasses.fields(MessageComplete)
    }

    enc = encoder()
    delta = enc.event(MessageDelta(run_id=RUN, agent_id=LIBRARIAN, text="half a sen"))
    complete = enc.event(MessageComplete(run_id=RUN, agent_id=LIBRARIAN, text="half a sen"))
    assert payload(delta).keys() == payload(complete).keys()

    assert type(decode(delta).event) is MessageDelta
    assert type(decode(complete).event) is MessageComplete
    # The same bytes under the other name decode to the other type — name-only dispatch, proved.
    crossed = decode_frame(str(complete.event), str(delta.data))
    assert crossed is not None and type(crossed.event) is MessageComplete


# --------------------------------------------------------------------------------------
# § three cases, not two: the table, run.started, and everything else (DC-4)
# --------------------------------------------------------------------------------------


def test_run_started_decodes_to_the_handle_it_carries_dc4() -> None:
    """Frame 0 of every POST-opened stream is the one frame that is not an ``AgentEvent``.

    ``RUN_STARTED_EVENT`` is deliberately absent from ``EVENT_NAMES`` — it is a transport frame, not
    an event kind — so a decoder built by inverting the table alone raises on the *first* frame of
    every run it will ever see. The handle it carries is not decoration either: the run id is the
    only cancel target (TU-36) and it arrives before the first token precisely so cancelling is
    never a race with a run that has not yet emitted anything.
    """
    assert RUN_STARTED_EVENT not in set(EVENT_NAMES.values())

    decoded = decode(encoder().started())
    assert decoded.handle == HANDLE
    assert decoded.event is None
    assert decoded.seq == 0
    assert decoded.type == RUN_STARTED_EVENT


def test_an_unknown_event_name_is_skipped_and_the_stream_survives_dc4(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A name this client does not know means the daemon is newer than the client, not that it broke.

    The daemon and the TUI are separate processes with separate lifetimes (arch §10): a daemon
    running a newer build is the normal state after an upgrade, not an anomaly. Raising on one
    unrecognised frame abandons a turn that is otherwise arriving correctly — and the turn keeps
    running server-side (AP-7), so the human sees a crash over work that succeeded.
    """
    enc = encoder()
    frames = [enc.event(event) for event in SAMPLES[:2]]

    with caplog.at_level(logging.DEBUG, logger="pkb.clients.sse"):
        assert decode_frame("pkb.future", json.dumps({"seq": 99, "whatever": True})) is None
    assert any("pkb.future" in record.getMessage() for record in caplog.records)

    assert [decode(frame).event for frame in frames] == list(SAMPLES[:2])


# --------------------------------------------------------------------------------------
# § a frame is the envelope plus the event (DC-5, decision J)
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_the_envelope_reaches_the_client_beside_the_event_dc5() -> None:
    """``thread_id``, ``seq``, ``status`` and ``code`` belong to no dataclass, and all four matter.

    A decoder that returned a bare ``AgentEvent`` throws away exactly the facts a client acts on:
    which conversation a fan-out frame belongs to, whether the run parked on a human decision or
    finished, and whether it stopped because somebody cancelled it. Layer 3's own suite found and
    fixed that bug server-side; returning a bare event re-introduces it one layer up, where it
    renders a thread waiting on a human as "done" and nobody ever goes back to it.

    Superseded (Phase 5 rebuilds this): mixed — the first half exercises ``"interrupted"`` via a
    prior ``InterruptEvent``, and the second half derives a fan-out frame's ``thread_id`` from
    ``expert_thread_id``, the derived-thread scheme retired along with the parent/derived split.
    ``seq`` and ``run_id`` surviving beside the event is real and needs a session-shaped successor.
    """
    enc = encoder()
    enc.event(InterruptEvent(run_id=RUN, request=approval()))
    end = decode(enc.event(RunEnd(run_id=RUN, final_text="filed")))

    assert (end.run_id, end.thread_id, end.type) == (RUN, THREAD, "run.end")
    assert end.status == "interrupted"
    assert isinstance(end.seq, int)

    fanned = decode(encoder().event(ToolStart(RUN, COOKING, "write_file", "steak.md")))
    assert fanned.thread_id == expert_thread_id(THREAD, COOKING) != THREAD
    assert fanned.agent_id == COOKING


def test_status_and_code_are_read_only_on_terminal_frames_dc5() -> None:
    """``Frame.status`` must never pick up a delegate's outcome and call it the run's ending.

    ``subagent.end`` carries ``status="failed"`` as its own field. Read generically — "if the
    payload has a status, that is the run's status" — one failed branch of a fan-out marks the whole
    turn failed while the Librarian is still merging the other experts' replies, and the human is
    told the filing died when it is about to succeed.
    """
    branch = decode(encoder().event(SubagentEnd(run_id=RUN, agent_id=COOKING, status="failed")))
    assert isinstance(branch.event, SubagentEnd)
    assert branch.event.status == "failed"
    assert branch.status is None
    assert branch.code is None

    for event in SAMPLES:
        decoded = decode(encoder().event(event))
        assert (decoded.status is not None) == decoded.terminal


def test_only_run_end_and_run_error_are_terminal_dc14() -> None:
    """The client stops reading at the first terminal frame, so "terminal" must be exactly two names.

    Too wide and the reader hangs up mid-turn on a ``tool.end``, leaving the rest of the reply
    unrendered while the run keeps going. Too narrow and a stream that has genuinely ended is read
    as "outcome unknown", which under DC-14 sends the client back to ``GET /threads/{id}`` on every
    successful run. This is also the property that immunises a client against a supervisor that
    emits something after its terminal frame.
    """
    assert set(TERMINAL) == {EVENT_NAMES[RunEnd], EVENT_NAMES[RunError]}

    for event in SAMPLES:
        decoded = decode(encoder().event(event))
        assert decoded.terminal is isinstance(event, RunEnd | RunError)
    assert decode(encoder().started()).terminal is False


# --------------------------------------------------------------------------------------
# § four statuses and an open code set (DC-6, DC-7)
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_all_four_run_statuses_reach_the_client_dc6() -> None:
    """Four, not three — and two of them ride on ``run.error``, which §5.3's wording hides.

    A cancelled run never emits ``run.end`` at all (Layer 2 re-raises ``CancelledError``), and a
    provider failure is a ``run.error`` with ``status: "error"``. A client with an exhaustive
    three-way match either raises or falls through to "done" on **every** provider failure — the
    most common failure a human will actually see, and the one where showing "done" over a turn that
    filed nothing is worst.

    Superseded (Phase 5 rebuilds this): pins ``RUN_STATUSES`` at four values, exercising
    ``"interrupted"`` via a parked ``InterruptEvent`` — both retired with the gate. Three statuses
    (``completed``, ``cancelled``, ``error``) survive and need a successor golden without the fourth.
    """
    completed = encoder()
    parked = encoder()
    parked.event(InterruptEvent(run_id=RUN, request=approval()))
    failed = encoder()

    seen = {
        decode(completed.event(RunEnd(run_id=RUN, final_text="filed"))).status,
        decode(parked.event(RunEnd(run_id=RUN, final_text="filed"))).status,
        decode(failed.event(RunError(run_id=RUN, message="boom", retryable=True))).status,
        decode(encoder().cancelled()).status,
    }
    assert seen == set(RUN_STATUSES)


def test_an_unknown_error_code_arrives_with_its_message_intact_dc7() -> None:
    """``run.error.code`` is an open set, so the client's job is to show what it was told.

    Only ``cancelled`` and ``run_error`` exist today, and a mid-stream typed error does not even
    carry its own: the supervisor publishes ``RunError(message=str(exc))`` and discards the
    exception's type. A client that renders only codes it recognises shows a human an empty box for
    every failure the daemon learns to name after this build shipped.
    """
    fabricated = SseEncoder(HANDLE, CATALOG, codes={RUN: "future_thing"})
    decoded = decode(
        fabricated.event(RunError(run_id=RUN, message="tokens: 0\nboom", retryable=False))
    )

    assert decoded.code == "future_thing"
    assert isinstance(decoded.event, RunError)
    assert decoded.event.message == "tokens: 0\nboom"
    assert decoded.event.retryable is False

    assert decode(encoder().cancelled()).code == CANCELLED_CODE
    plain = decode(encoder().event(RunError(run_id=RUN, message="boom", retryable=True)))
    assert plain.code == RUN_ERROR_CODE


# --------------------------------------------------------------------------------------
# § pings are invisible (DC-8)
# --------------------------------------------------------------------------------------


def test_ping_comments_are_never_a_client_side_signal_dc8() -> None:
    """A ``: ping`` comment frame is dropped by the SSE decoder before it is ever an event.

    Liveness on this wire comes from the read timeout (DC-9) and progress from a local clock
    (TU-25). Anything built on seeing pings gets nothing at all, because httpx2 returns ``None`` for
    a comment line — and a client that drops to raw lines to see them reports "idle" in the middle
    of a busy fan-out, which is precisely backwards. The decoder is only ever handed a name and a
    data line, so a comment cannot reach it; what the scan pins is that nobody went looking.
    """
    control = code_strings('comment = ": ping"\nheartbeat = "ping"')
    assert {text for text in control if _is_ping(text)} == {": ping", "ping"}, "scanner is broken"

    for path in CLIENT_SOURCES:
        pings = sorted(
            text for text in code_strings(path.read_text(encoding="utf-8")) if _is_ping(text)
        )
        assert pings == [], f"{path.name} treats a ping as a value: {pings}"


# --------------------------------------------------------------------------------------
# § seq is a per-response cursor, and an attach has no run.started (DC-16, DC-17)
# --------------------------------------------------------------------------------------


def test_seq_orders_one_response_and_means_nothing_across_two_dc16() -> None:
    """``(run_id, seq)`` is not an event identity, because the encoder is built per response.

    ``routes._stream`` constructs a fresh ``SseEncoder`` for every response, so the same event gets
    a different number on an attach than it had on the original stream, and a resume re-numbers from
    zero under the same thread. A transcript keyed on ``(run_id, seq)`` therefore overwrites the
    first half of a resumed turn with the second — silently, because the numbers look contiguous.
    """
    event = MessageDelta(run_id=RUN, agent_id=LIBRARIAN, text="one")

    first = encoder()
    first.started()
    original = decode(first.event(event))

    attached = encoder()
    replayed = decode(attached.event(event))

    assert original.event == replayed.event
    assert original.seq != replayed.seq
    assert (original.seq, replayed.seq) == (1, 0)


@pytest.mark.superseded
def test_an_attach_stream_carries_no_handle_and_a_delegate_agent_dc17() -> None:
    """``routes.attach`` passes ``started=False``, so frame 0 of an attach is whatever came next.

    Two things follow and both are silent failures. The run id — the only cancel target — has to
    come from an envelope, because there is no ``run.started`` to read it from. And the first
    frame's ``agent_id`` on a fan-out is the *delegate*: a client that titles the pane from it
    labels a Librarian turn "Cooking", and the run's own agent has to come from
    ``GET /threads/{id}`` instead.

    Superseded (Phase 5 rebuilds this): the "delegate agent" half is a fan-out frame's ``thread_id``
    derived via ``expert_thread_id`` — the parent/derived split, retired outright. "No handle on an
    attach" is a real, surviving property of `run.started` and needs a session-shaped successor.
    """
    enc = encoder()
    frames = [
        enc.event(SubagentStart(run_id=RUN, agent_id=GRILLING)),
        enc.event(ToolStart(run_id=RUN, agent_id=GRILLING, tool="write_file", summary="rib.md")),
        enc.event(RunEnd(run_id=RUN, final_text="filed")),
    ]
    decoded = [decode(frame) for frame in frames]

    assert all(frame.handle is None for frame in decoded)
    assert {frame.run_id for frame in decoded} == {RUN}
    assert decoded[0].agent_id == GRILLING != HANDLE.agent_id
    assert decoded[0].thread_id == expert_thread_id(THREAD, GRILLING)
    assert decoded[-1].thread_id == THREAD


# --------------------------------------------------------------------------------------
# § one parser for both routes an approval reaches a client by (CL-19)
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_the_interrupt_frame_and_the_thread_detail_parse_identically_cl19() -> None:
    """One approval, two routes into a client, and they must not be able to disagree.

    The live ``interrupt`` frame and ``GET /threads/{id}.pending_interrupt`` carry the same nested
    shape, and a human meets it both ways by design: raised in the TUI at breakfast, opened from a
    phone at lunch (arch §8, D3). Two parsers are two answers to "what am I approving", and the
    second one is always the one nobody tests — so the modal opened from history would render
    different args, or route the resume to a different thread, from the one opened live.

    Superseded (Phase 5 rebuilds this): the whole "§ one parser for both routes an approval reaches a
    client by (CL-19)" section — ``decode_request`` exists only to parse ``ApprovalRequest``/
    ``pending_interrupt``, and nothing is ever pending once the operator's instruction is the
    approval. `pkb.clients.approval`'s own tests (a separate file, not in scope here) cover the
    decision-helper half of this same retirement.
    """
    request = approval()
    frame = decode(encoder().event(InterruptEvent(run_id=RUN, request=request)))
    assert isinstance(frame.event, InterruptEvent)

    detail = thread_detail(request)
    from_detail = decode_request(detail["pending_interrupt"])

    assert from_detail == frame.event.request == request
    assert from_detail.actions[0] == request.actions[0]
    assert [action.allowed_decisions for action in from_detail.actions] == [
        ("approve", "edit", "reject"),
        ("approve", "reject"),
    ]
    assert (from_detail.thread_id, from_detail.interrupt_id) == (request.thread_id, "i-1")


@pytest.mark.superseded
def test_the_parser_keeps_arguments_as_opaque_strings_cl19() -> None:
    """``args`` crosses JSON as strings and comes back as strings — no helpful re-typing.

    The far side coerces (`replace_all` arrives as the string ``'False'`` and pydantic's lax mode
    accepts it), and a client cannot see the tool's schema anyway. A parser that JSON-decoded a
    stringified list, or turned ``'False'`` into ``False``, would be guessing — and the guess is
    sent straight back as an edit's complete arg map (CL-14), into a tool call the human already
    approved.
    """
    request = decode_request(
        {
            "interrupt_id": "i-9",
            "agent_id": COOKING,
            "thread_id": expert_thread_id(THREAD, COOKING),
            "actions": [
                {
                    "tool": "edit_file",
                    "args": {"replace_all": "False", "tags": '["cooking","steak"]'},
                    "description": DIFF,
                    "allowed_decisions": ["approve", "reject"],
                    "reason": "breadth-approval",
                }
            ],
        }
    )
    args = request.actions[0].args
    assert all(isinstance(value, str) for value in args.values())
    assert args["replace_all"] == "False"
    assert args["tags"] == '["cooking","steak"]'
    assert request.actions[0].allowed_decisions == ("approve", "reject")


@pytest.mark.superseded
def test_a_decision_outside_the_literal_is_dropped_cl19() -> None:
    """``allowed_decisions`` is typed ``tuple[DecisionType, ...]`` and the wire cannot be trusted.

    The value arrives as a JSON list of arbitrary strings from a daemon that may be a newer build.
    Layer 2 drops what it does not recognise on the way out (``_allowed_decisions``); the one parser
    on the way in must do the same, because everything downstream treats the tuple as the set of
    things a human may click. An unrecognised member becomes a control that the server refuses with
    ``400 invalid_decision`` — a dead button on an approval, which is the one screen where a control
    that does nothing teaches the human to distrust the UI.
    """
    request = decode_request(
        {
            "interrupt_id": "i-9",
            "agent_id": COOKING,
            "thread_id": THREAD,
            "actions": [
                {
                    "tool": "write_file",
                    "args": {},
                    "description": "",
                    "allowed_decisions": ["approve", "bless"],
                    "reason": "breadth-approval",
                }
            ],
        }
    )
    assert request.actions[0].allowed_decisions == ("approve",)
