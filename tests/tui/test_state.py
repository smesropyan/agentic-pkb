"""The run view as pure state — TU-18, TU-19, TU-24 … TU-33, TU-36.

``RunView`` holds no Textual object on purpose, so every rule about *what a turn looks like* is
assertable without a terminal. That is what this file exercises, and it exercises it over the real
wire: every frame is built by the **server's** :class:`~pkb.server.sse.SseEncoder` and read back by
the **client's** :func:`~pkb.clients.sse.decode_frame`, so the state is fed exactly the bytes the
daemon emits. A hand-written frame dict would let this file agree with itself while disagreeing with
the daemon — and the four rules below are all corrections to something the daemon really does.

The corrections, and the misrendering each one prevents:

* Layer 2's token channel and fact channel are independent, so **every assistant message arrives
  twice** — once as deltas, once complete — and SS-13 forbids Layer 3 from fixing it. A client that
  appends both renders every reply twice (TU-24), and ``run.end.final_text`` carries the same string
  a third time (TU-25).
* ``subagent.*`` are **brackets over concurrent branches**, and they need not balance: an expert that
  raises an approval never emits ``subagent.end`` at all. A spinner that waits for one spins forever
  on precisely the branch the human has to look at (TU-27).
* There are **four** terminal states and a fifth non-state. A cancellation is not a failure (TU-31),
  a stream that simply stops is not a completion (TU-33), and a resume mints a **new run id**, so a
  cached cancel target cancels nothing and reports success, because ``DELETE /runs/{unknown}`` is a
  deliberate 204 (TU-36).
* Nothing here is ever recovered from prose: the "continue with the expert" offer comes from the
  envelope's ``thread_id`` and from ``children``, never from parsing the merged reply (TU-18).
"""

from __future__ import annotations

import ast
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from sse_starlette import ServerSentEvent

import pkb.clients.sse as clients_sse
import pkb.tui.state as tui_state
from pkb.clients.sse import Frame, decode_frame
from pkb.contracts import (
    CANCELLED_CODE,
    RUN_ERROR_CODE,
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
from pkb.server.sse import SseEncoder
from pkb.tui.state import RunView, offers_from_children, replay

LIBRARIAN = "librarian"
COOKING = "topic/cooking"
GRILLING = "topic/cooking/grilling"
BAKING = "topic/baking"
GENERAL = "general-purpose"
"""An expert's *internal* delegation (RT-44) — deliberately **not** a catalog id."""

CATALOG: tuple[str, ...] = (LIBRARIAN, COOKING, GRILLING, BAKING)

THREAD = "t-1"
RUN = "run-1"
HANDLE = RunHandle(run_id=RUN, agent_id=LIBRARIAN, thread_id=THREAD)

CANCELLED_MESSAGE = "the run was cancelled"
"""The exact sentence ``RunSupervisor`` synthesises for a cancelled run (AP-11).

Reproduced rather than imported because it is a *wire* fact: ``SseEncoder.status_for`` keys the
``cancelled`` status off this message, and a test that patched the encoder's own constant would
assert nothing about the frame a real cancel produces.
"""

PACKAGE_SOURCES: tuple[Path, ...] = tuple(
    sorted(Path(tui_state.__file__).parent.glob("*.py"))
    + sorted(Path(clients_sse.__file__).parent.glob("*.py"))
)


# --------------------------------------------------------------------------------------
# Fixtures — the real encoder in, the real decoder out, nothing hand-written in between
# --------------------------------------------------------------------------------------


def approval(
    agent_id: str = COOKING, *, interrupt_id: str = "i-1", thread_id: str | None = None
) -> ApprovalRequest:
    """One approval, parked on the thread that raised it (LB-16)."""
    return ApprovalRequest(
        interrupt_id=interrupt_id,
        agent_id=agent_id,
        thread_id=expert_thread_id(THREAD, agent_id) if thread_id is None else thread_id,
        actions=(
            ActionView(
                tool="write_file",
                args={"file_path": "topics/Cooking/notes/steak.md"},
                description="Proposed content:\n- Pull at 130F\n",
                allowed_decisions=("approve", "edit", "reject"),
                reason="breadth-approval",
            ),
        ),
    )


def decode(frame: ServerSentEvent) -> Frame:
    """The client's view of one encoded frame."""
    decoded = decode_frame(str(frame.event), str(frame.data))
    assert decoded is not None, f"the decoder dropped {frame.event!r}"
    return decoded


def wire(
    events: Sequence[AgentEvent], *, handle: RunHandle = HANDLE, started: bool = False
) -> list[Frame]:
    """Encode with the server, decode with the client — one response's worth of frames.

    One :class:`SseEncoder` per call, because the daemon builds one per response: that is what makes
    ``seq`` restart at zero on a resume, and it is the shape TU-36 has to be fed.
    """
    encoder = SseEncoder(handle, CATALOG)
    frames = [decode(encoder.started())] if started else []
    frames.extend(decode(encoder.event(event)) for event in events)
    return frames


def fed(
    events: Sequence[AgentEvent],
    *,
    handle: RunHandle = HANDLE,
    started: bool = False,
    view: RunView | None = None,
) -> RunView:
    """A :class:`RunView` with one response folded into it."""
    run_view = view or RunView(thread_id=handle.thread_id, agent_id=handle.agent_id)
    for frame in wire(events, handle=handle, started=started):
        run_view.apply(frame)
    return run_view


def texts(view: RunView, kind: str = "message") -> list[str]:
    return [entry.text for entry in view.entries if entry.kind == kind]


def identifiers(path: Path) -> set[str]:
    """Every name a module's *code* uses — docstrings and comments excluded by construction.

    Grep over source text cannot tell "this module explains why it never renders ``final_text``"
    from "this module renders ``final_text``", and both TU-25 and TU-18 are asserted by absence.
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
# § one message, not two (TU-24)
# --------------------------------------------------------------------------------------


def test_a_complete_replaces_the_deltas_it_repeats_tu24() -> None:
    """The same reply arrives on two channels and must land on the screen once.

    Layer 2 streams tokens on one channel and finished messages on another; its dedup is by message
    id *within* the fact channel, so it cannot see that the deltas said the same thing. SS-13
    forbids Layer 3 from collapsing them, which leaves exactly one place where it can happen. Append
    instead of replace and the human reads every answer the knowledge base gives them twice.
    """
    reply = "filed under Cooking/notes/steak.md"
    view = fed(
        [
            MessageDelta(run_id=RUN, agent_id=LIBRARIAN, text="filed under "),
            MessageDelta(run_id=RUN, agent_id=LIBRARIAN, text="Cooking/notes/steak.md"),
            MessageComplete(run_id=RUN, agent_id=LIBRARIAN, text=reply),
        ]
    )

    assert texts(view) == [reply]
    assert view.messages_for(LIBRARIAN) == view.entries


def test_a_complete_that_differs_from_its_deltas_wins_tu24() -> None:
    """The complete is the fact channel; the deltas are a draft of it.

    The two channels are independent, so they can genuinely disagree — a message rewritten after its
    tokens streamed, or a delta lost. Keeping the concatenation would show the human the draft and
    file the fact, and nothing on the screen would say which one the knowledge base acted on.
    """
    view = fed(
        [
            MessageDelta(run_id=RUN, agent_id=LIBRARIAN, text="filing under Baki"),
            MessageDelta(run_id=RUN, agent_id=LIBRARIAN, text="ng"),
            MessageComplete(run_id=RUN, agent_id=LIBRARIAN, text="filed under Cooking"),
        ]
    )

    assert texts(view) == ["filed under Cooking"]


def test_each_agent_of_a_fan_out_keeps_its_own_buffer_tu24() -> None:
    """Two experts stream concurrently on one run id; only ``agent_id`` tells them apart.

    A buffer keyed on the run alone would interleave two experts' tokens into one sentence and then
    let whichever completed last replace the pair — losing one expert's answer entirely, in the
    fan-out that is the whole reason the Librarian exists.
    """
    view = fed(
        [
            MessageDelta(run_id=RUN, agent_id=COOKING, text="sear "),
            MessageDelta(run_id=RUN, agent_id=GRILLING, text="two zones"),
            MessageDelta(run_id=RUN, agent_id=COOKING, text="first"),
            MessageComplete(run_id=RUN, agent_id=COOKING, text="sear first"),
            MessageComplete(run_id=RUN, agent_id=GRILLING, text="two zones"),
        ]
    )

    assert texts(view) == ["sear first", "two zones"]
    assert [entry.text for entry in view.messages_for(COOKING)] == ["sear first"]
    assert [entry.text for entry in view.messages_for(GRILLING)] == ["two zones"]


def test_a_resumed_run_does_not_overwrite_the_first_halfs_message_tu24() -> None:
    """A resume mints a new run id, and the buffer key has to notice.

    Interrupt then resume is one *turn* over two runs, from the same agent, on the same thread. Key
    the buffer on the agent alone and the resumed run's message replaces the pre-approval half of
    the conversation — the half that says what the human was asked to approve.
    """
    resumed = RunHandle(run_id="run-2", agent_id=LIBRARIAN, thread_id=THREAD)
    view = fed([MessageComplete(run_id=RUN, agent_id=LIBRARIAN, text="may I write steak.md?")])
    fed(
        [MessageComplete(run_id="run-2", agent_id=LIBRARIAN, text="written")],
        handle=resumed,
        started=True,
        view=view,
    )

    assert texts(view) == ["may I write steak.md?", "written"]


# --------------------------------------------------------------------------------------
# § final_text is not a message (TU-25)
# --------------------------------------------------------------------------------------


def test_run_end_final_text_is_never_a_third_copy_of_the_reply_tu25() -> None:
    """``run.end`` is consumed for its status; its text is already on the screen.

    The runtime yields ``MessageComplete(text=reply)`` and then ``RunEnd(final_text=reply)`` with the
    *identical* string. Rendering both puts a third copy of the answer directly under the second —
    with TU-24's deltas that is the same paragraph three times for one turn.
    """
    reply = "filed under Cooking"
    view = fed(
        [
            MessageDelta(run_id=RUN, agent_id=LIBRARIAN, text=reply),
            MessageComplete(run_id=RUN, agent_id=LIBRARIAN, text=reply),
            RunEnd(run_id=RUN, final_text=reply),
        ]
    )

    assert texts(view) == [reply]
    assert view.terminal == "completed"


def test_a_final_text_nobody_else_sent_still_reaches_no_entry_tu25() -> None:
    """Proof the single copy above is the *complete*, not the ``run.end``.

    If the view were rendering ``final_text`` and dropping the complete, the previous test would
    still pass — the two strings are identical by construction. Making them differ is the only way
    to see which one the transcript kept.
    """
    view = fed(
        [
            MessageComplete(run_id=RUN, agent_id=LIBRARIAN, text="filed under Cooking"),
            RunEnd(run_id=RUN, final_text="A SENTENCE ONLY RUN_END CARRIES"),
        ]
    )

    assert texts(view) == ["filed under Cooking"]
    assert all("ONLY RUN_END" not in entry.text for entry in view.entries)


def test_no_module_in_the_client_packages_reads_final_text_tu25() -> None:
    """Asserted by absence, over the AST, because the docstrings discuss it at length.

    ``final_text`` reaching a widget is the one-line change that reintroduces the duplicate, and it
    reads as an obvious improvement ("the run told us the answer, show it"). Nothing in either
    package should touch the attribute at all — the transcript is built from ``message.complete``.
    """
    for path in PACKAGE_SOURCES:
        assert "final_text" not in identifiers(path), f"{path.name} reads run.end.final_text"


# --------------------------------------------------------------------------------------
# § the pane is the envelope's, never a re-derivation (TU-26)
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_a_branch_belongs_to_the_thread_its_envelope_names_tu26() -> None:
    """The server derives the expert's thread against the catalog; the client only reads it.

    ``expert_thread_id`` is gated on the catalog server-side (LB-14, SS-10). A client re-deriving
    from ``agent_id`` would agree here and disagree in the case below — which is why the assertion
    is that the branch carries the *envelope's* value, not that it carries a correct-looking one.

    Superseded (Phase 5 rebuilds this): the assertion pins ``branches[COOKING].thread_id`` to
    ``expert_thread_id`` — the derived-thread `<parent>::<agent>` addressing retired with the
    parent/derived split. There is no derived thread for a branch to carry.
    """
    view = fed([SubagentStart(run_id=RUN, agent_id=COOKING)])

    assert view.branches[COOKING].thread_id == expert_thread_id(THREAD, COOKING)
    assert view.branches[COOKING].thread_id != THREAD


@pytest.mark.superseded
def test_a_general_purpose_delegation_stays_on_the_parent_thread_tu26() -> None:
    """An expert's *internal* delegation is not an expert, and must not get a pane of its own.

    ``general-purpose`` (RT-44) is not a catalog id, so the server's derivation leaves the frame on
    the parent thread. A client that built a thread id from ``agent_id`` would invent one, split a
    single conversation across two panes, and offer "continue with the general-purpose expert" —
    a link to a thread that does not exist.

    Superseded (Phase 5 rebuilds this): the assertion pins ``branches[COOKING].thread_id`` to
    ``expert_thread_id`` alongside the parent-thread case — the derived-thread scheme both arms
    compare against is retired with the parent/derived split.
    """
    view = fed(
        [
            SubagentStart(run_id=RUN, agent_id=COOKING),
            SubagentStart(run_id=RUN, agent_id=GENERAL),
        ]
    )

    assert view.branches[GENERAL].thread_id == THREAD
    assert view.branches[COOKING].thread_id == expert_thread_id(THREAD, COOKING)
    assert [offer.agent_id for offer in view.offers] == [COOKING]


# --------------------------------------------------------------------------------------
# § brackets, not nesting, and they need not balance (TU-27)
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_the_terminal_frame_closes_a_branch_that_never_ended_tu27() -> None:
    """The branch that raised the approval is the one that never sends ``subagent.end``.

    Three experts run concurrently under one run id; the one that gates stops mid-work and its
    bracket is never closed. A UI that clears a spinner only on ``subagent.end`` therefore spins
    forever on **precisely the branch the human has to look at**, next to two that finished cleanly
    — which reads as "still working", so nobody opens it.

    Superseded (Phase 5 rebuilds this): mixed — the scenario gates ``COOKING`` with an
    ``InterruptEvent`` and asserts ``view.pending`` (the pending-approval state slice) resolves to
    its ``expert_thread_id`` — both the interrupt surface and the derived-thread addressing it parks
    on are retired outright; nothing gates, so nothing leaves a branch open this way. The general
    "an un-ended branch still closes on the terminal frame" principle for ``GRILLING``/``BAKING``
    likely survives; marked whole because one script drives all three branches and cannot be split
    without touching the test body.
    """
    view = fed(
        [
            SubagentStart(run_id=RUN, agent_id=COOKING),
            SubagentStart(run_id=RUN, agent_id=GRILLING),
            SubagentStart(run_id=RUN, agent_id=BAKING),
            SubagentEnd(run_id=RUN, agent_id=GRILLING, status="answered"),
            SubagentEnd(run_id=RUN, agent_id=BAKING, status="failed"),
            InterruptEvent(run_id=RUN, request=approval(COOKING)),
            RunEnd(run_id=RUN, final_text="two of three answered"),
        ]
    )

    assert view.terminal == "interrupted"
    assert not [branch.agent_id for branch in view.branches.values() if branch.spinning]
    assert view.branches[GRILLING].status == "answered"
    assert view.branches[BAKING].status == "failed"
    assert view.branches[COOKING].status == "interrupted"
    assert not view.branches[COOKING].open
    assert view.pending is not None
    assert view.pending.thread_id == expert_thread_id(THREAD, COOKING)


def test_two_branches_interleave_and_each_keeps_its_own_order_tu27() -> None:
    """Within one ``(run_id, agent_id)`` order is exact; between two agents there is no order.

    Up to ``fanout_limit`` experts run concurrently on one run, so the frames arrive shuffled. A
    widget that nested the second ``subagent.start`` inside the first would claim Grilling was
    called *by* Cooking — a causal lie about a routing decision the human may want to correct.
    """
    view = fed(
        [
            SubagentStart(run_id=RUN, agent_id=COOKING),
            MessageDelta(run_id=RUN, agent_id=COOKING, text="sear "),
            SubagentStart(run_id=RUN, agent_id=GRILLING),
            MessageDelta(run_id=RUN, agent_id=GRILLING, text="two "),
            MessageDelta(run_id=RUN, agent_id=COOKING, text="hard, "),
            MessageDelta(run_id=RUN, agent_id=GRILLING, text="zones"),
            MessageDelta(run_id=RUN, agent_id=COOKING, text="rest ten"),
        ]
    )

    assert [entry.text for entry in view.messages_for(COOKING)] == ["sear hard, rest ten"]
    assert [entry.text for entry in view.messages_for(GRILLING)] == ["two zones"]
    assert set(view.branches) == {COOKING, GRILLING}


def test_an_end_whose_start_was_never_seen_still_records_a_branch_tu27() -> None:
    """An attach joins a fan-out mid-flight, so the opening bracket may simply be missing.

    ``RunHub`` keeps only the first 512 frames of a replay and drops the overflow, so a client that
    attached late can receive ``subagent.end`` for a branch it never saw start. Ignoring it would
    hide the one field that says whether that expert answered or failed.
    """
    view = fed([SubagentEnd(run_id=RUN, agent_id=GRILLING, status="failed")])

    assert view.branches[GRILLING].status == "failed"
    assert not view.branches[GRILLING].spinning


# --------------------------------------------------------------------------------------
# § four terminal states, no fall-through (TU-31, TU-32)
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_the_four_terminal_states_are_reached_and_are_distinct_tu31() -> None:
    """``completed | interrupted | cancelled | error``, each from the frame that really carries it.

    ``completed`` and ``interrupted`` ride on ``run.end``; ``cancelled`` and ``error`` ride on
    ``run.error``, because a cancelled run never emits ``run.end`` at all. A three-way match — which
    is what SS-9 as written invites — either raises or falls through to "done" on every provider
    failure, i.e. on the most common failure a human will actually see.

    Superseded (Phase 5 rebuilds this): mixed — the ``"interrupted"`` arm is built from an
    ``InterruptEvent``, the retired gate surface, and a session never reaches it because nothing
    parks. ``completed``, ``cancelled`` and ``error`` survive as three of the four states; marked
    whole because the ``endings`` dict and the ``set(reached) == set(RUN_STATUSES)`` assertion cannot
    be split without touching the test body. A successor needs a three-state table once
    ``RUN_STATUSES`` itself drops ``interrupted``.
    """
    endings = {
        "completed": [RunEnd(run_id=RUN, final_text="filed")],
        "interrupted": [
            InterruptEvent(run_id=RUN, request=approval()),
            RunEnd(run_id=RUN, final_text=""),
        ],
        "cancelled": [RunError(run_id=RUN, message=CANCELLED_MESSAGE, retryable=True)],
        "error": [RunError(run_id=RUN, message="provider timeout", retryable=True)],
    }
    reached = {status: fed(events).terminal for status, events in endings.items()}

    assert reached == {status: status for status in endings}
    assert set(reached) == set(RUN_STATUSES)


def test_a_cancellation_records_no_error_entry_tu31() -> None:
    """A cancellation is not a failure, and the transcript must not say it was.

    The human pressed cancel; there is nothing to report and nothing to retry. An error banner over
    their own deliberate act reads as "your cancel broke something", and it is indistinguishable
    from the provider dying at the same moment — which is the state that *does* need a retry.
    """
    view = fed([RunError(run_id=RUN, message=CANCELLED_MESSAGE, retryable=True)])

    assert view.terminal == "cancelled"
    assert view.code == CANCELLED_CODE
    assert not [entry for entry in view.entries if entry.error]
    assert texts(view, "note") == []


def test_a_real_failure_shows_the_servers_message_verbatim_tu31() -> None:
    """``run.error.code`` is an open set, so the sentence is the part a client can always show.

    A mid-stream typed error is published as ``RunError(message=str(exc))`` with its type discarded,
    so the code is ``run_error`` no matter what actually broke. Re-wording or suppressing ``message``
    leaves the human with a blank box on the one path where the detail is all there is.
    """
    view = fed([RunError(run_id=RUN, message="ollama: 429 quota exhausted", retryable=True)])

    assert view.terminal == "error"
    assert view.code == RUN_ERROR_CODE
    assert texts(view, "note") == ["ollama: 429 quota exhausted"]
    assert [entry.error for entry in view.entries] == [True]


def test_retryable_is_carried_through_so_a_retry_can_be_a_button_tu32() -> None:
    """The server says whether trying again could work; the client must not guess.

    A retry is a second POST against a thread whose first run may already have written to the
    knowledge base, and there is no undo — so it is a button the *human* presses, and it may only be
    offered when the server said the failure was transient. Inferring it from the status code (or
    always offering it) makes the human the one who duplicates an ingestion turn.
    """
    transient = fed([RunError(run_id=RUN, message="provider timeout", retryable=True)])
    permanent = fed([RunError(run_id=RUN, message="the model is not installed", retryable=False)])

    assert (transient.terminal, transient.retryable) == ("error", True)
    assert (permanent.terminal, permanent.retryable) == ("error", False)


# --------------------------------------------------------------------------------------
# § a stream that just stops (TU-33)
# --------------------------------------------------------------------------------------


def test_a_stream_that_stops_records_unknown_and_closes_its_branches_tu33() -> None:
    """No terminal frame means the outcome is *unknown* — not done, and not failed.

    This is the primary shutdown path, not a rare one: the daemon's farewell branch only runs
    between events, so a run suspended on a 16 s cloud call — or a 284 s local one — never gets one.
    Recording "completed" leaves a pending approval invisible; recording "error" invites a retry of
    a turn that may still be writing to the tree.
    """
    view = fed([SubagentStart(run_id=RUN, agent_id=COOKING)])
    assert view.running

    view.ended()

    assert view.terminal == "unknown"
    assert view.terminal not in RUN_STATUSES
    assert not view.running
    assert not view.branches[COOKING].spinning
    assert view.branches[COOKING].status == "unknown"
    assert not [entry for entry in view.entries if entry.error]


def test_ended_never_overwrites_a_terminal_state_it_already_saw_tu33() -> None:
    """The socket always closes after the terminal frame; that close is not new information.

    Every completed run ends this way, so an unconditional overwrite would turn *every* successful
    turn into "outcome unknown" and send the client back to ``GET /threads/{id}`` for a fact it was
    already told.
    """
    view = fed([RunEnd(run_id=RUN, final_text="filed")])

    view.ended()

    assert view.terminal == "completed"


# --------------------------------------------------------------------------------------
# § the cancel target (TU-36)
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_the_cancel_target_moves_to_the_resumed_run_tu36() -> None:
    """A resume mints a **new** run id, and cancelling the old one fails silently.

    ``RuntimeService.resume`` continues the *turn* on a fresh run id, and ``DELETE /runs/{unknown}``
    is a deliberate 204 — so a client caching the pre-interrupt id gets a cancel button that does
    nothing and reports success, over a run that keeps writing. Re-keying on every ``run.started``
    is the only thing standing between the human and that.

    Superseded (Phase 5 rebuilds this): the scenario is built on an ``InterruptEvent`` and
    ``RuntimeService.resume`` — both retired with the gates, so there is no post-approval resume to
    mint a fresh run id from. The re-key-on-``run.started`` principle plausibly survives for an
    ordinary second turn on the same session; it needs a non-interrupt scenario to prove it again.
    """
    view = fed(
        [
            InterruptEvent(run_id=RUN, request=approval()),
            RunEnd(run_id=RUN, final_text=""),
        ],
        started=True,
    )
    assert view.run_id == RUN

    resumed = RunHandle(run_id="run-2", agent_id=LIBRARIAN, thread_id=THREAD)
    fed(
        [MessageComplete(run_id="run-2", agent_id=LIBRARIAN, text="written")],
        view=view,
        handle=resumed,
        started=True,
    )

    assert view.run_id == "run-2"


def test_the_cancel_target_exists_before_the_first_token_tu36() -> None:
    """``run.started`` is frame 0 so the button is live before anything is generated.

    A run that hangs before emitting a token is exactly the run a human wants to stop, and it is the
    one with no event to read a run id from. Taking the id from the handle rather than from a later
    frame's envelope is what makes cancel available for it.
    """
    view = fed([], started=True)

    assert view.run_id == RUN
    assert view.entries == []


# --------------------------------------------------------------------------------------
# § waiting, never hung (TU-30)
# --------------------------------------------------------------------------------------


def test_a_long_gap_names_the_agent_and_never_says_hung_tu30() -> None:
    """Silence is normal here, and saying "hung" over it is a misdiagnosis with a retry attached.

    A filing turn is 8-12 model calls at ~16 s each, a fan-out branch blocked on the concurrency
    semaphore emits nothing at all until it starts, and a silent failover to the local model makes
    284 s a *correct* turn. What the human needs is who is being waited on and for how long — the
    two facts that turn "is it broken?" into "Cooking has had four minutes".
    """
    view = fed([SubagentStart(run_id=RUN, agent_id=COOKING)])
    view.last_frame_at = time.monotonic() - 284.0

    note = view.waiting_note

    assert COOKING in note
    assert "284" in note
    assert "hung" not in note.lower()
    assert view.terminal == "running"
    assert not [entry for entry in view.entries if entry.error]


def test_a_gap_with_no_branch_open_names_the_run_s_own_agent_tu30() -> None:
    """Before any delegation there is still someone to name: the agent the human is talking to.

    An empty "waiting on  — 284s" is worse than no indicator, because it reads as a UI fault rather
    than as a slow turn.
    """
    view = RunView(thread_id=THREAD, agent_id=LIBRARIAN)
    view.last_frame_at = time.monotonic() - 60.0

    assert LIBRARIAN in view.waiting_note


def test_the_waiting_note_is_silent_when_there_is_nothing_to_wait_for_tu30() -> None:
    """It appears on a genuine gap and never on a run that already ended.

    A fresh frame means progress, so the indicator has to disappear again; and a finished run that
    still claims to be waiting on Cooking is a spinner nobody can clear.
    """
    live = fed([MessageDelta(run_id=RUN, agent_id=LIBRARIAN, text="thinking")])
    assert live.waiting_note == ""

    done = fed([RunEnd(run_id=RUN, final_text="filed")])
    done.last_frame_at = time.monotonic() - 284.0
    assert done.waiting_note == ""


# --------------------------------------------------------------------------------------
# § "continue with the expert" comes from the envelope, never from prose (TU-18)
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_an_offer_targets_the_thread_the_envelope_named_tu18() -> None:
    """The link is an ordinary POST to the derived thread, and its id arrives on the wire.

    Recovering it from the merged reply would make ``merge_reply``'s *rendering* a wire protocol —
    a heading change in Layer 2 would silently break every "continue with Cooking" link, and the
    breakage is a link to a thread id that does not exist rather than an error anyone sees.

    Superseded (Phase 5 rebuilds this): "continue with the expert" resolves to an
    ``expert_thread_id`` derived thread — the whole parent/derived split is retired, so there is no
    derived thread left for an offer to target.
    """
    view = fed(
        [
            SubagentStart(run_id=RUN, agent_id=COOKING),
            SubagentStart(run_id=RUN, agent_id=GRILLING),
            MessageComplete(run_id=RUN, agent_id=LIBRARIAN, text="Cooking and Grilling both said"),
            RunEnd(run_id=RUN, final_text="Cooking and Grilling both said"),
        ]
    )

    assert [(offer.agent_id, offer.thread_id) for offer in view.offers] == [
        (COOKING, expert_thread_id(THREAD, COOKING)),
        (GRILLING, expert_thread_id(THREAD, GRILLING)),
    ]
    assert all(offer.text == "" for offer in view.offers)


@pytest.mark.superseded
def test_children_reproduce_the_offers_after_a_reload_tu18() -> None:
    """Live frames are gone once the reply scrolls away; ``children`` is where parentage survives.

    This is the second of the offer's two sources and the reason text is never one: reopening a
    Librarian thread hours later replays only the merged reply, so without ``children`` the human's
    route back into the expert's own conversation is a paragraph they have to read and retype.

    Superseded (Phase 5 rebuilds this): built entirely on the ``children`` list's ``thread_id``
    (``expert_thread_id``) and ``parent_thread_id`` fields — the parent/derived thread split
    retired wholesale, so there is no derived child row for ``offers_from_children`` to read.
    """
    live = fed(
        [
            SubagentStart(run_id=RUN, agent_id=COOKING),
            SubagentStart(run_id=RUN, agent_id=GRILLING),
        ]
    )
    children = [
        {
            "thread_id": expert_thread_id(THREAD, COOKING),
            "agent_id": COOKING,
            "title": "steak, routed",
            "kind": "routed",
            "parent_thread_id": THREAD,
        },
        {
            "thread_id": expert_thread_id(THREAD, GRILLING),
            "agent_id": GRILLING,
            "title": None,
            "kind": "routed",
            "parent_thread_id": THREAD,
        },
    ]

    reloaded = offers_from_children(children)

    assert [(offer.agent_id, offer.thread_id) for offer in reloaded] == [
        (offer.agent_id, offer.thread_id) for offer in live.offers
    ]
    assert [offer.kind for offer in reloaded] == ["offer", "offer"]
    assert [offer.title for offer in reloaded] == ["steak, routed", ""]


def test_nothing_in_the_client_packages_parses_an_agent_id_out_of_prose_tu18() -> None:
    """Asserted by absence: the merged reply is never a source of ids.

    ``final_text`` and the merged ``message.complete`` are the two places an agent name appears as
    text. Reading either would put LB-18's golden rendering under a client's contract, and it would
    fail by *offering the wrong expert* — a thread id that resolves, to the wrong conversation.
    """
    for path in PACKAGE_SOURCES:
        names = identifiers(path)
        assert "final_text" not in names, f"{path.name} reads run.end.final_text"
        assert "merge_reply" not in names, f"{path.name} knows about the merge rendering"


# --------------------------------------------------------------------------------------
# § the conversation is the detail payload (TU-19)
# --------------------------------------------------------------------------------------


def test_replay_builds_the_conversation_from_the_detail_payload_tu19() -> None:
    """``GET /threads/{id}`` is authoritative on open; the stream is additive.

    An attach is a live tail with an unreliable prefix — the hub keeps the *first* 512 frames and
    the fresh encoder renumbers ``seq``, so the hole in the middle is undetectable on the wire.
    Reconstructing history from frames therefore shows a turn with a silent gap; the detail payload
    is the only thing that cannot.
    """
    detail: dict[str, Any] = {
        "thread": {"thread_id": THREAD, "agent_id": LIBRARIAN, "title": "steak"},
        "messages": [
            {"role": "human", "text": "I grilled a ribeye", "created_at": None},
            {"role": "assistant", "text": "filed under Cooking", "created_at": None},
            {"role": "human", "text": "and the rest?", "created_at": None},
            {"role": "assistant", "text": "Cooking and Grilling both said", "created_at": None},
        ],
        "pending_interrupt": None,
        "children": [],
    }

    entries = replay(detail, LIBRARIAN)

    assert [(entry.kind, entry.agent_id, entry.text) for entry in entries] == [
        ("human", "", "I grilled a ribeye"),
        ("message", LIBRARIAN, "filed under Cooking"),
        ("human", "", "and the rest?"),
        ("message", LIBRARIAN, "Cooking and Grilling both said"),
    ]


def test_the_replayed_reply_appears_exactly_once_tu19() -> None:
    """The replayed view must not be *poorer* or *richer* than the live one for the same turn.

    Live, the reply arrives as deltas, as a complete and as ``final_text`` and renders once (TU-24,
    TU-25). Replayed, it is one message in the payload and must render once too — otherwise
    reopening a thread visibly changes what the conversation says, and the human learns to distrust
    whichever view they are not looking at.
    """
    reply = "filed under Cooking"
    live = fed(
        [
            MessageDelta(run_id=RUN, agent_id=LIBRARIAN, text=reply),
            MessageComplete(run_id=RUN, agent_id=LIBRARIAN, text=reply),
            RunEnd(run_id=RUN, final_text=reply),
        ]
    )
    replayed = replay(
        {"messages": [{"role": "assistant", "text": reply, "created_at": None}]}, LIBRARIAN
    )

    assert texts(live) == [reply]
    assert [entry.text for entry in replayed] == [reply]


def test_a_thread_that_never_ran_replays_to_nothing_tu19() -> None:
    """A freshly minted thread has no ``messages`` at all, and opening it must not raise.

    ``POST /agents/{id}/threads`` mints a thread before any run, and TU-14 opens every thread with a
    ``GET`` — so the empty payload is the *first* thing this function ever sees in a fresh knowledge
    base, not an edge case.
    """
    assert replay({"thread": {"thread_id": THREAD}, "children": []}, LIBRARIAN) == []
    assert replay({"messages": []}, LIBRARIAN) == []


def test_a_tool_message_in_the_history_is_kept_tu19() -> None:
    """The server already decided what a human needs to see; the client does not filter again.

    ``_message_view`` drops system messages and empty ones and keeps ``tool`` — so a tool result in
    the payload is there deliberately. Silently discarding it makes the replayed turn shorter than
    the one the human watched, which is the exact impression TU-19 exists to prevent.
    """
    entries = replay(
        {
            "messages": [
                {"role": "tool", "text": "wrote topics/Cooking/notes/steak.md", "created_at": None}
            ]
        },
        LIBRARIAN,
    )

    assert [entry.text for entry in entries] == ["wrote topics/Cooking/notes/steak.md"]


# --------------------------------------------------------------------------------------
# § tool lines are the server's, verbatim (TU-29, alongside the run view's own rules)
# --------------------------------------------------------------------------------------


def test_a_tool_line_is_the_servers_summary_and_an_error_is_marked_tu29() -> None:
    """The summary is rendered server-side once so every channel says the same thing.

    Re-rendering it client-side is a second answer to "what is the agent doing", and the first step
    toward a client that reads the knowledge base to find out — which I2 forbids it from doing
    correctly anyway. ``ToolEnd.error`` is the one bit the client adds styling from.
    """
    view = fed(
        [
            ToolStart(run_id=RUN, agent_id=COOKING, tool="write_file", summary="steak.md"),
            ToolEnd(
                run_id=RUN,
                agent_id=COOKING,
                tool="write_file",
                summary="steak.md — refused",
                error=True,
            ),
        ]
    )

    assert texts(view, "tool") == ["steak.md", "steak.md — refused"]
    assert [entry.error for entry in view.entries] == [False, True]
