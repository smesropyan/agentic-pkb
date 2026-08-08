"""One approval, one resume, the same way in every channel (CL-1 … CL-22).

Nothing here is about a UI. :mod:`pkb.clients.approval` exists because the TUI and the Telegram bot
sit on opposite sides of a transport boundary and must nonetheless turn one ``interrupt`` into one
identical resume — same parsing, same validation of which decisions are allowed, same routing of the
answer back to the thread that raised it. So every test below is a pure function over the helper,
and what they pin is the *guarantee two channels share*, not the drawing either one does.

The stakes are set by D6: a knowledge base has no version control and no undo. Every rule in this
file is one way a client could make a human approve something they did not read — a padded decision
for an action the modal never showed, a decision list applied to whatever interrupt happens to be
pending now, an ``edit`` that drops the content of the file being written, a diff cut mid-line so a
removal reads as an addition. None of them are recoverable after the fact, which is why they are
refused here, in front of the transport, rather than diagnosed afterwards.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import subprocess
import sys
from collections.abc import Mapping
from itertools import combinations
from pathlib import Path
from typing import Any, get_args

import pytest

from pkb import contracts as contracts_module
from pkb.agents.gates import GATE_DECISIONS, GateReason
from pkb.clients import approval as helper
from pkb.clients.approval import (
    TRUNCATION_MARKER,
    Answer,
    Resolution,
    edited_args,
    is_diff,
    offered,
    resolve,
    truncate,
)
from pkb.contracts import (
    ActionView,
    ApprovalRequest,
    Decision,
    DecisionType,
    InvalidDecisionError,
    expert_thread_id,
    validate_decisions,
)

PARENT_THREAD = "t-1"
"""The Librarian's thread — the one a client has open and is streaming."""

INTERRUPT = "i-42"
COOKING = "topic/cooking"

DECISION_TYPES: tuple[DecisionType, ...] = get_args(DecisionType)

WRITE_ARGS: Mapping[str, str] = {
    "file_path": "topics/Cooking/notes/steak.md",
    "content": "# Steak\n\n- Rest for 8 minutes.\n",
    "replace_all": "False",
}
"""Three args, one of them a whole document, one of them a stringified bool (CL-17)."""

DIFF = (
    "--- a/topics/Cooking/notes/steak.md\n"
    "+++ b/topics/Cooking/notes/steak.md\n"
    "@@ -1,4 +1,4 @@\n"
    " Sear the steak.\n"
    "-Rest for 2 minutes.\n"
    "+Rest for 8 minutes.\n"
    " Slice against the grain.\n"
)

PROPOSED_CONTENT = "Proposed content:\n\n# Steak\n\n- Rest for 8 minutes.\n- Slice it.\n"
"""What ``describe_write`` emits for a *new* file: raw markdown, no hunk header, ``- `` bullets."""


# --------------------------------------------------------------------------------------
# Fixtures — the two shipped gate shapes, and the two request shapes a client sees
# --------------------------------------------------------------------------------------


def write_action(
    *, args: Mapping[str, str] = WRITE_ARGS, description: str = DIFF, reason: str = "new-tag"
) -> ActionView:
    """A write gate: ``approve``/``edit``/``reject``, exactly as ``GATE_DECISIONS`` ships it."""
    return ActionView(
        tool="write_file",
        args=dict(args),
        description=description,
        allowed_decisions=("approve", "edit", "reject"),
        reason=reason,
    )


def delete_action() -> ActionView:
    """A delete gate: two decisions, because a delete cannot be edited into a different delete."""
    return ActionView(
        tool="delete",
        args={"file_path": "topics/Cooking/notes/old.md"},
        description="Delete topics/Cooking/notes/old.md",
        allowed_decisions=("approve", "reject"),
        reason="delete",
    )


def request(
    *actions: ActionView, thread_id: str = PARENT_THREAD, interrupt_id: str = INTERRUPT
) -> ApprovalRequest:
    return ApprovalRequest(
        interrupt_id=interrupt_id,
        agent_id=COOKING,
        thread_id=thread_id,
        actions=actions or (write_action(),),
    )


def helper_source() -> Path:
    return Path(contracts_module.__file__).parent / "clients" / "approval.py"


# --------------------------------------------------------------------------------------
# CL-1, CL-2, CL-3 — one validator, no transport, no clock
# --------------------------------------------------------------------------------------


def test_validate_decisions_is_the_seam_function_itself_cl3() -> None:
    """Two implementations of "may this action be answered this way" is two answers to it.

    The daemon validates with ``pkb.contracts.validate_decisions`` on the way in. If the client had
    its own copy, the two would drift on the first rule change and the client would either refuse a
    resume the daemon accepts — a client-only refusal, invisible from the server side and impossible
    to explain to the human — or send one the daemon 400s after the human already clicked approve.
    Identity, not equivalence, is the only assertion that survives a future edit to either side.
    """
    assert helper.validate_decisions is validate_decisions


def test_the_helper_writes_out_no_decision_type_list_of_its_own_cl3() -> None:
    """A hardcoded ``["approve", "edit", "reject"]`` anywhere in the client is a second gate table.

    ``allowed_decisions`` is server-side truth and the shipped table already has two shapes: a
    delete allows two decisions, every write allows three. A client that lists the types itself
    draws an ``edit`` button on a delete, where it permanently 400s — the human clicks a control the
    client invented and reads the server's refusal as a bug in the daemon.
    """
    tree = ast.parse(helper_source().read_text(encoding="utf-8"))
    literals = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.List | ast.Tuple | ast.Set)
        if {
            element.value
            for element in node.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }
        & set(DECISION_TYPES)
    ]
    assert literals == [], f"decision types listed in the client: {literals}"


def test_importing_the_helper_loads_no_transport_and_no_ui_cl1() -> None:
    """Its two consumers sit on opposite sides of a transport boundary; it may belong to neither.

    The TUI reaches the daemon over HTTP and the bot runs in-process against ``pkb.service`` (D9).
    An ``httpx2`` or ``textual`` import here makes the module unusable by half its users — and a
    ``pkb.agents`` import drags langgraph into a process that is meant to be a client, which is the
    import-linter violation that I2 exists to prevent. Checked in a fresh interpreter because this
    test module itself imports the harness, so ``sys.modules`` in-process proves nothing.
    """
    script = (
        "import sys; import pkb.clients.approval;"
        "forbidden = {'httpx', 'httpx2', 'textual', 'sse_starlette', 'fastapi', 'langgraph',"
        " 'pkb.service', 'pkb.server', 'pkb.agents', 'pkb.tui'};"
        "leaked = sorted(forbidden & set(sys.modules));"
        "print(leaked);"
        "sys.exit(1 if leaked else 0)"
    )
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, f"leaked into a client process: {done.stdout.strip()}"


def test_every_public_callable_is_a_plain_function_of_its_arguments_cl2() -> None:
    """A coroutine here would make the bot's in-process call path different from the TUI's.

    The helper is shared precisely so both channels run the *same* code; an ``async def`` splits it
    into a version one caller can use and a version the other must wrap, and a wrapper is where the
    two implementations start again.
    """
    for name in helper.__all__:
        member = getattr(helper, name)
        if callable(member) and not isinstance(member, type):
            assert not inspect.iscoroutinefunction(member), name


# --------------------------------------------------------------------------------------
# CL-9, CL-11, CL-12 — narrowing is a subsequence, never a set operation
# --------------------------------------------------------------------------------------


def test_narrowing_is_a_subsequence_of_the_servers_own_order_cl9() -> None:
    """Order decides which control is first, and the first control is the one a hurried human hits.

    A client may drop what it cannot render — Telegram drops ``edit`` because editing a document on
    a phone is impractical — and may never add one: the server re-validates the un-narrowed set on
    the way in, so an invented decision is a 400 the client itself caused. Implemented as a set
    operation this would silently reorder, and "approve" arriving where the modal used to draw
    "reject" is a mis-click with no undo behind it.
    """
    for allowed in (("approve", "edit", "reject"), ("approve", "reject")):
        action = dataclasses.replace(write_action(), allowed_decisions=allowed)
        for size in range(len(DECISION_TYPES) + 1):
            for drop in combinations(DECISION_TYPES, size):
                result = offered(action, drop=drop)
                assert set(result) <= set(allowed), (allowed, drop, result)
                positions = [allowed.index(kind) for kind in result]
                assert positions == sorted(positions), (allowed, drop, result)
                assert len(result) == len([k for k in allowed if k not in drop])


def test_dropping_edit_still_leaves_approve_the_first_button_cl9() -> None:
    """Telegram's actual narrowing, spelled out: two buttons, in the server's order, no reordering.

    This is the one narrowing that ships, so it is the one that must not become
    ``sorted(set(allowed) - {"edit"})`` — which would put ``approve`` before ``reject`` today by
    luck of the alphabet and swap them the day a decision type called ``abort`` is added.
    """
    assert offered(write_action(), drop=("edit",)) == ("approve", "reject")
    assert offered(write_action()) == ("approve", "edit", "reject")


def test_the_offered_set_is_the_shipped_gate_table_row_for_row_cl12() -> None:
    """Arch §6's "approve / edit / reject" is the common case, not the contract.

    Built to that sentence a modal offers ``edit`` on a delete, where it 400s every time — the
    shipped table has two shapes, and ``respond`` appears in neither. Reading the affordances off
    ``action.allowed_decisions`` is what keeps a client correct when the table grows a third.
    """
    for reason, expected in GATE_DECISIONS.items():
        action = dataclasses.replace(write_action(), allowed_decisions=expected, reason=str(reason))
        assert offered(action) == expected, reason
    assert GATE_DECISIONS[GateReason.DELETE] == ("approve", "reject")
    assert "respond" not in set().union(*GATE_DECISIONS.values())


def test_an_action_nobody_can_answer_offers_nothing_and_builds_nothing_cl11() -> None:
    """An approval no channel can answer parks the thread forever and RT-39 then bricks the rest.

    ``_allowed_decisions`` returns ``()`` for a malformed ``ReviewConfig``, and a client that
    guessed a default there would answer a gate the server never authorised. The honest outcome is
    an empty affordance list and a typed refusal — which is what a hand-off is rendered from.
    """
    unanswerable = dataclasses.replace(write_action(), allowed_decisions=())
    assert offered(unanswerable) == ()
    assert offered(write_action(), drop=DECISION_TYPES) == ()

    for kind in DECISION_TYPES:
        with pytest.raises(InvalidDecisionError):
            resolve(request(unanswerable), {0: Answer(type=kind)})


# --------------------------------------------------------------------------------------
# CL-5, CL-6 — positional, index-keyed, total
# --------------------------------------------------------------------------------------


def test_decisions_come_back_in_the_actions_own_order_cl5() -> None:
    """One approval carries several actions and the answers are matched to them by position alone.

    RT-41 batches every interruptible tool call of one ``AIMessage`` into a single interrupt, so a
    delete beside a write is the normal case, not an edge one. Answer index 1 with index 0's
    decision and the human deletes a file while believing they approved a note. The answers here are
    supplied in reverse insertion order to prove the builder reads the integer key rather than the
    order the UI happened to collect them in.
    """
    pending = request(delete_action(), write_action())
    answers = {1: Answer(type="approve"), 0: Answer(type="reject", message="keep it")}

    resolution = resolve(pending, answers)

    assert isinstance(resolution.decisions, tuple)
    assert [decision.type for decision in resolution.decisions] == ["reject", "approve"]
    assert resolution.decisions[0].message == "keep it"


def test_validate_decisions_alone_cannot_see_a_swap_cl5() -> None:
    """Why the positional discipline has to live in the builder: the validator cannot check it.

    Both shipped sets contain ``approve`` and ``reject``, so a delete answered with the write's
    decision and vice versa passes every check the seam makes and reaches the graph as a valid
    resume. Nothing downstream will ever flag it. This test exists so that a future "the server
    validates it anyway" refactor of :func:`resolve` fails against a recorded fact.
    """
    pending = request(delete_action(), write_action())
    swapped = (Decision(type="approve"), Decision(type="reject"))

    assert validate_decisions(pending, swapped, interrupt_id=INTERRUPT) is pending


def test_a_partially_answered_approval_is_refused_not_padded_cl6() -> None:
    """Padding a missing answer is how a human approves a second write they never looked at.

    Neither default is defensible: ``approve`` is the AI resolving its own gate wearing a client's
    clothes, which RT-33 forbids outright, and ``reject`` is quieter and just as wrong — the write
    silently never happens and nobody is recorded as having decided. The refusal is typed, because
    every caller catches ``InvalidDecisionError`` and nothing catches a ``KeyError``.
    """
    pending = request(delete_action(), write_action())

    with pytest.raises(InvalidDecisionError, match="1"):
        resolve(pending, {0: Answer(type="approve")})

    with pytest.raises(InvalidDecisionError):
        resolve(pending, {})


def test_an_index_no_action_has_is_refused_cl5() -> None:
    """An answer keyed to an action that is not in this request is answering a different approval.

    It happens when a modal is rebuilt while a second interrupt arrives: the widget still holds the
    old action list. Accepting the in-range subset and dropping the stray would send a decision list
    that validates cleanly and means something else entirely.
    """
    pending = request(write_action())

    with pytest.raises(InvalidDecisionError, match="no such action index"):
        resolve(pending, {0: Answer(type="approve"), 5: Answer(type="reject")})


# --------------------------------------------------------------------------------------
# CL-7, CL-8 — the id and the thread travel with the decisions
# --------------------------------------------------------------------------------------


def test_the_interrupt_id_is_inseparable_from_the_decisions_cl7() -> None:
    """Two channels looking at one approval is the design (D3), not an edge case.

    Without the id, a second client's stale answers apply to whatever is pending *now* — silently,
    with no undo. The id must therefore come from the request the decisions were built against, and
    there must be no parameter through which a caller can pair some other id with them by hand: the
    409 that ``interrupt_id`` exists to produce is the only thing standing between two channels and
    a lost update.
    """
    pending = request(write_action())
    resolution = resolve(pending, {0: Answer(type="approve")})

    assert resolution.interrupt_id == INTERRUPT
    assert set(inspect.signature(resolve).parameters) == {"request", "answers"}
    assert [field.name for field in dataclasses.fields(Resolution)] == [
        "interrupt_id",
        "thread_id",
        "decisions",
    ]
    assert all(field.default is dataclasses.MISSING for field in dataclasses.fields(Resolution)), (
        "a defaultable id is an id a caller can forget to set"
    )


def test_the_body_is_the_post_payload_verbatim_cl7() -> None:
    """The resume body is built once, here, so both channels put the same JSON on the wire.

    ``POST /threads/{id}/interrupt`` reads ``interrupt_id`` and a positional ``decisions`` list;
    a channel assembling its own dict is a second wire format that drifts, and the first symptom is
    a 400 nobody can attribute to a client. It must also survive ``json.dumps`` — the helper's own
    values reach the socket untouched.
    """
    pending = request(delete_action(), write_action())
    body = resolve(pending, {0: Answer(type="reject"), 1: Answer(type="approve")}).body()

    assert set(body) == {"interrupt_id", "decisions"}
    assert body["interrupt_id"] == INTERRUPT
    assert body["decisions"] == [
        {"type": "reject", "message": None, "edited_args": None, "edited_tool": None},
        {"type": "approve", "message": None, "edited_args": None, "edited_tool": None},
    ]
    assert json.loads(json.dumps(body)) == body


def test_a_fan_out_approval_resolves_against_its_own_thread_cl8() -> None:
    """The gate parks on the expert's derived thread; the client is streaming the Librarian's.

    LB-16: inside a fan-out the interrupt is raised on ``<parent>::<agent-id>``, and the run handle
    the client opened names the parent. Posting a delegate's decisions to the parent is a genuine
    ``409 stale_interrupt`` for a perfectly valid approval — the server must not "helpfully"
    redirect it — so a client routing by the streamed id can never resolve a fan-out approval at
    all, and the human sees an approval that refuses every answer with no visible cause.
    """
    derived = expert_thread_id(PARENT_THREAD, COOKING)
    assert derived != PARENT_THREAD

    pending = request(write_action(), thread_id=derived)
    resolution = resolve(pending, {0: Answer(type="approve")})

    assert resolution.thread_id == pending.thread_id == derived
    assert "thread_id" not in set(inspect.signature(resolve).parameters)


# --------------------------------------------------------------------------------------
# CL-14, CL-15, CL-16 — what an edit may change, and what it may not
# --------------------------------------------------------------------------------------


def test_an_edit_carries_every_argument_not_only_the_changed_one_cl14() -> None:
    """``edited_args`` replaces the call's arguments wholesale — it is not a patch.

    ``_harness_decision`` does ``dict(decision.edited_args)`` and stops there, so a modal sending
    only ``{"content": …}`` invokes ``write_file`` with no ``file_path``: the tool errors inside the
    graph *after* the human already said yes, and burns one of the three attempts MW-14 allows on a
    client bug. The untouched values must also survive verbatim, strings and all (CL-17) — a client
    that helpfully turns ``'False'`` into a bool is guessing at a schema it cannot see.
    """
    action = write_action()
    resolution = resolve(
        request(action),
        {0: Answer(type="edit", changes={"content": "# Steak\n\n- Rest for 10 minutes.\n"})},
    )
    decision = resolution.decisions[0]

    assert decision.edited_args is not None
    assert set(decision.edited_args) == set(action.args)
    assert decision.edited_args["file_path"] == WRITE_ARGS["file_path"]
    assert decision.edited_args["replace_all"] == "False"
    assert decision.edited_args["content"] == "# Steak\n\n- Rest for 10 minutes.\n"


def test_building_edited_args_leaves_the_action_untouched_cl14() -> None:
    """A cancelled edit must leave the modal showing what the server actually sent.

    ``ActionView`` is frozen but its ``args`` mapping is not, and mutating it in place would make
    the second render of one approval disagree with the first — the human re-reads a diff that no
    longer matches the arguments the daemon holds.
    """
    action = write_action()
    merged = edited_args(action, {"content": "different"})

    assert merged is not action.args
    assert action.args["content"] == WRITE_ARGS["content"]
    assert merged["content"] == "different"


def test_retargeting_a_write_takes_a_second_deliberate_act_cl16() -> None:
    """Editing content is what the modal is for; editing the destination is a different act.

    An edited action is executed without re-running the gate predicate — ``_process_decision``
    returns a ``ToolCall`` and never calls ``_should_interrupt`` — so a changed ``file_path``
    redirects a write the human is looking at into a file they are not, and the approval recorded in
    the checkpoint describes a different file from the one the tree received. With the flag the new
    destination is surfaced in the returned map, so the caller can re-display it before sending.
    """
    action = write_action()

    with pytest.raises(InvalidDecisionError, match="allow_retarget"):
        edited_args(action, {"file_path": "topics/Cooking/notes/elsewhere.md"})
    with pytest.raises(InvalidDecisionError, match="allow_retarget"):
        resolve(
            request(action),
            {0: Answer(type="edit", changes={"file_path": "topics/Cooking/notes/elsewhere.md"})},
        )

    resolution = resolve(
        request(action),
        {
            0: Answer(
                type="edit",
                changes={"file_path": "topics/Cooking/notes/elsewhere.md"},
                allow_retarget=True,
            )
        },
    )
    assert resolution.decisions[0].edited_args == {
        **WRITE_ARGS,
        "file_path": "topics/Cooking/notes/elsewhere.md",
    }


def test_resending_an_unchanged_file_path_is_not_a_retarget_cl16() -> None:
    """A key→string editor seeds every field, so ``file_path`` comes back on every ordinary edit.

    If the flag were required whenever the key is *present* rather than whenever the value
    *differs*, the routine case — fix a typo in the content, submit the whole form — would refuse
    itself, and a client author would reach for ``allow_retarget=True`` unconditionally, which
    disarms the check for the case it exists for.
    """
    action = write_action()
    merged = edited_args(action, {"file_path": WRITE_ARGS["file_path"], "content": "fixed"})

    assert merged["file_path"] == WRITE_ARGS["file_path"]
    assert merged["content"] == "fixed"


def test_an_edit_never_substitutes_the_tool_cl15() -> None:
    """An edit changes a call's arguments, never which tool runs.

    ``_harness_decision`` uses ``decision.edited_tool or action.tool`` and nothing re-checks the
    substitution, while a delete gates as ``('approve', 'reject')`` with no ``edit`` at all — so
    tool substitution is the one route by which an ``edit``-allowed approval performs a delete. The
    human read a diff of a write; the tree loses a file, permanently. The helper closes it by never
    producing the field, and :class:`Answer` gives no way to ask for it.
    """
    resolution = resolve(request(write_action()), {0: Answer(type="edit", changes={})})

    assert resolution.decisions[0].edited_tool is None
    assert resolution.body()["decisions"][0]["edited_tool"] is None
    assert "edited_tool" not in {field.name for field in dataclasses.fields(Answer)}


def test_validate_decisions_alone_would_let_a_tool_substitution_through_cl15() -> None:
    """The recorded fact behind the rule above: the seam does not check ``edited_tool`` (P-18).

    A hand-built ``edit`` naming ``delete`` against a ``write_file`` gate validates cleanly, which is
    exactly why "never set it" has to be a property of the shared builder rather than something a
    client trusts the daemon to catch.
    """
    pending = request(write_action())
    substituted = (Decision(type="edit", edited_tool="delete", edited_args=dict(WRITE_ARGS)),)

    assert validate_decisions(pending, substituted, interrupt_id=INTERRUPT) is pending


# --------------------------------------------------------------------------------------
# CL-4, CL-13 — refused locally, before any transport, with exactly the seam's rules
# --------------------------------------------------------------------------------------


def test_a_decision_the_action_forbids_is_refused_before_any_request_cl4() -> None:
    """Whatever control the UI drew, the server's list is the truth and the client re-checks it.

    Narrowing is a UI affordance, never an access control: a widened modal — an ``edit`` button on a
    delete gate — produces a 400 the human caused by clicking a button the client invented. Catching
    it locally turns that into a refusal the client can explain, with no round trip and nothing
    pending changed.
    """
    pending = request(delete_action())

    with pytest.raises(InvalidDecisionError, match="delete"):
        resolve(pending, {0: Answer(type="edit", changes={"file_path": "x.md"})})


def test_decisions_are_materialised_before_they_are_validated_cl4() -> None:
    """``validate_decisions`` takes ``len(decisions)``: a generator crashes it with a ``TypeError``.

    Every caller in this stack is written to catch ``InvalidDecisionError``/``StaleInterruptError``,
    so an untyped ``TypeError`` escapes as a stack trace where a 400 belonged. The builder therefore
    hands the validator a concrete ``tuple``, and this test records the crash it is avoiding.
    """
    pending = request(write_action())
    assert isinstance(resolve(pending, {0: Answer(type="approve")}).decisions, tuple)

    with pytest.raises(TypeError):
        validate_decisions(pending, (d for d in [Decision(type="approve")]))  # type: ignore[arg-type]


def test_reject_needs_no_reason_but_respond_does_cl13() -> None:
    """The helper enforces exactly what the validator enforces — no more, and no less.

    A required rejection reason would be a rule only one channel could have: Telegram cannot demand
    typed prose from a phone, and ``pkb.agents.approval`` deliberately substitutes the harness's own
    text when there is none. Requiring it here would refuse a resume the daemon accepts, invisibly
    from the server side. ``respond`` is the opposite: the harness reads ``decision["message"]``
    unconditionally and would ``KeyError`` inside the graph, so it is refused before it is sent.
    """
    silent_reject = resolve(request(write_action()), {0: Answer(type="reject")})
    assert silent_reject.decisions[0].message is None

    respondable = dataclasses.replace(
        write_action(), allowed_decisions=("approve", "respond", "reject")
    )
    assert resolve(request(respondable), {0: Answer(type="respond", message="not here")})

    with pytest.raises(InvalidDecisionError, match="respond"):
        resolve(request(respondable), {0: Answer(type="respond")})


# --------------------------------------------------------------------------------------
# CL-22 and decision N — showing the human less than everything, visibly
# --------------------------------------------------------------------------------------


def test_truncation_cuts_on_a_line_boundary_cl22() -> None:
    """Cutting mid-line in a unified diff can turn a removal into what reads as an addition.

    Telegram's 4096-character limit meets a ``description`` that can hold a whole document, and the
    cut lands wherever the byte count says. Half of ``-Rest for 2 minutes.`` is still a diff line to
    the human reading it — and the marker has to be visible, because a silently clipped diff is a
    diff the human approved without seeing (D6: there is no undo).
    """
    limit = DIFF.index("-Rest for 2 minutes.") + 6 + len(TRUNCATION_MARKER)
    text, was_truncated = truncate(DIFF, limit)

    assert was_truncated
    assert text.endswith(TRUNCATION_MARKER)
    body = text[: -len(TRUNCATION_MARKER)]
    assert DIFF.startswith(body)
    assert body == DIFF[: DIFF.index("\n-Rest for 2 minutes.")]
    assert set(body.split("\n")) <= set(DIFF.split("\n")), "a partial line reached the human"
    assert "-Rest" not in body


@pytest.mark.parametrize("limit", [80, 120, len(DIFF) - 1])
def test_a_truncated_description_fits_the_channels_limit_cl22(limit: int) -> None:
    """The point of the cut is the channel's hard limit, so the marker has to fit inside it too.

    Appending the marker after cutting to the limit would push the message back over it, and
    Telegram rejects the whole send: the human then sees nothing at all rather than a short diff,
    for an approval that is blocking a run.
    """
    text, was_truncated = truncate(DIFF, limit)

    assert was_truncated
    assert len(text) <= limit


def test_an_untruncated_description_is_returned_unchanged_cl22() -> None:
    """The flag is what a channel renders "you are not seeing all of this" from.

    It must be false when nothing was cut, or every approval carries a warning and the warning stops
    meaning anything; and the original text must come back byte-identical, because the same string
    is what the TUI shows in full beside Telegram's short form (SS-11: the server renders once).
    """
    short = "Delete topics/Cooking/notes/old.md"

    assert truncate(short, 4096) == (short, False)
    assert truncate(DIFF, len(DIFF)) == (DIFF, False)
    assert truncate(DIFF, 0) == (DIFF, False)


def test_only_a_hunk_header_makes_a_description_a_diff_decision_n() -> None:
    """``describe_write`` emits five shapes and only one of them is a diff.

    A *new* file gets ``Proposed content:`` and raw markdown. Colourising that with a diff lexer
    paints every ``- `` bullet as a **deletion**, telling the human that lines being added are being
    removed — on a write with no undo. The hunk header is the only observable sign the server
    actually produced a diff.
    """
    assert is_diff(DIFF)
    assert not is_diff(PROPOSED_CONTENT)
    assert not is_diff("--- a/notes.md\n+++ b/notes.md\n")
    assert not is_diff("Delete topics/Cooking/notes/old.md")


def test_a_truncated_diff_is_still_recognisable_as_a_diff_decision_n() -> None:
    """Truncation and colourisation compose: the cut must not change what the renderer thinks it has.

    A cut that removed the hunk header would flip the renderer to plain text mid-approval, so the
    same diff reads one way in the TUI and another in a length-limited channel — for the same
    ``description``, rendered once by the server.
    """
    text, was_truncated = truncate(DIFF, len(DIFF) - 1)

    assert was_truncated
    assert is_diff(text)


def test_a_line_longer_than_the_budget_is_still_marked_cl22() -> None:
    """When there is no line boundary to cut on, the cut is still declared rather than hidden.

    A single ``description`` line can be longer than the whole budget — one arg of a write can hold
    a document with no newlines in it. There is nothing to cut on then, so the character cut is
    unavoidable; what is not negotiable is the marker, because the failure this rule prevents is a
    human approving a diff they were shown only part of without knowing it.
    """
    text, was_truncated = truncate("x" * 500, 200)

    assert was_truncated
    assert text.endswith(TRUNCATION_MARKER)
    assert len(text) <= 200


def test_a_channel_supplies_its_own_truncation_marker_cl22() -> None:
    """The default marker sends the human to the TUI, which is a lie in a channel that has the text.

    Under decision U / TG-56 a description too long for one Telegram message is uploaded whole as a
    document and *then* previewed under the buttons — so ``"open the TUI for the whole diff"`` would
    print directly above the whole diff it is telling them to open a terminal to read. The parameter
    is what keeps that wording per-channel while the **cut** stays one implementation: the
    alternative is a second truncation inside the adapter, which is the per-channel drift CL-22
    exists to prevent, and it would drift on the line boundary, not just the wording.
    """
    marker = "\n… (full text in the document above)"
    limit = DIFF.index("-Rest for 2 minutes.") + 6 + len(marker)

    text, was_truncated = truncate(DIFF, limit, marker=marker)

    assert was_truncated
    assert text.endswith(marker)
    assert TRUNCATION_MARKER not in text
    assert len(text) <= limit, "the marker has to be budgeted for, whoever supplied it"

    body = text[: -len(marker)]
    assert body == DIFF[: DIFF.index("\n-Rest for 2 minutes.")], "still a line-boundary cut"
    assert set(body.split("\n")) <= set(DIFF.split("\n")), "a partial line reached the human"


def test_the_default_marker_is_unchanged_for_every_existing_caller_cl22() -> None:
    """``marker`` is keyword-only with the shipped default, so no caller shifts by being recompiled.

    The TUI's list rows and the service's own previews were written against
    ``TRUNCATION_MARKER``; C-35 adds a knob for Telegram, and a knob that changes what everyone else
    already renders is a channel divergence introduced by the fix meant to prevent one.
    """
    explicit, explicit_cut = truncate(DIFF, 120, marker=TRUNCATION_MARKER)

    assert (explicit, explicit_cut) == truncate(DIFF, 120)
    assert explicit.endswith(TRUNCATION_MARKER)

    assert truncate(DIFF, 0, marker="!") == (DIFF, False), "no cut means no marker of any kind"
    assert truncate("short", 4096, marker="!") == ("short", False)


def test_a_resolution_is_a_frozen_value_object_cl7() -> None:
    """Two channels may hold the same approval; neither may mutate the other's answer in place.

    The id, the thread and the decisions are one value precisely so they cannot drift apart between
    being built and being sent — a resolution whose ``thread_id`` can be reassigned after validation
    is the CL-8 bug with an extra step.
    """
    resolution = resolve(request(write_action()), {0: Answer(type="approve")})

    with pytest.raises(dataclasses.FrozenInstanceError):
        resolution.thread_id = "somewhere-else"  # type: ignore[misc]

    payload: dict[str, Any] = resolution.body()
    assert payload["interrupt_id"] == INTERRUPT
