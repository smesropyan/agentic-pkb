"""Turning one approval into one resume — the same way in every channel (CL-1 … CL-22).

Arch §6: both human channels must turn an ``interrupt`` into a :class:`~pkb.contracts.Decision`
consistently — *"same action parsing, same validation of which decisions are allowed… that logic
lives once"*. Only the rendering differs: a diff modal in the TUI, inline keyboard buttons in
Telegram. This is that module, and it holds **no UI and no transport**.

The rules it exists to make mechanical:

* **``allowed_decisions`` is server-side truth.** A client may **narrow** what it offers — Telegram
  drops ``edit``, because editing a document on a phone is impractical — and may never widen it
  (RT-32, RO-15). :func:`offered` returns a *subsequence* of the server's own list, so narrowing
  cannot accidentally reorder or invent one, and the server re-validates on the way in regardless.
* **Decisions are positionally aligned with actions** (RT-41). One approval can carry several
  actions; answer index 1 with index 0's decision and the human approves a write they never saw.
  :func:`resolve` is index-keyed and **total** — a partial answer raises rather than being padded.
* **The interrupt id travels with the decisions, always** (RO-12). Two channels looking at one
  approval is the design, not an edge case; without the id a second client's stale answers apply to
  whatever is pending *now*, silently, with no undo. :class:`Resolution` makes them inseparable.
* **The thread the decisions go to is the one inside the request**, never the one being streamed
  (LB-16, CL-8). In a fan-out the gate parks on the expert's derived thread; posting to the
  Librarian's is a 409 for a perfectly valid approval, and it is the failure hardest to debug from a
  client.

What it deliberately does **not** hold is policy. Requiring a typed reason for a rejection would be
a rule only one channel has — Telegram cannot reasonably demand prose from a phone — and the whole
point of this module is that both answer identically. A reason is *invited* by the UI and optional
here (Q14, RULED).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from pkb.contracts import (
    ActionView,
    ApprovalRequest,
    Decision,
    DecisionType,
    InvalidDecisionError,
    validate_decisions,
)

__all__ = [
    "TRUNCATION_MARKER",
    "Answer",
    "Resolution",
    "edited_args",
    "is_diff",
    "offered",
    "resolve",
    "truncate",
    "validate_decisions",
]

TRUNCATION_MARKER: Final = "\n… (truncated — open the TUI for the whole diff)"
"""What a channel with a length limit appends. Visible, because a silently clipped diff is a diff
the human approved without seeing (D6: there is no undo)."""

_DIFF_MARKER: Final = "@@"
"""The observable sign that the server actually produced a unified diff.

``describe_write`` emits five shapes and only one is a diff — a *new* file gets ``Proposed
content:`` and raw markdown. Colouring that with a diff lexer paints every ``- `` bullet as a
**deletion**, telling the human that lines being added are being removed, on a write with no undo.
So a renderer colourises only when this marker is present (decision N).
"""


@dataclass(frozen=True, slots=True)
class Answer:
    """One human answer to one action, before it becomes a :class:`~pkb.contracts.Decision`."""

    type: DecisionType
    message: str | None = None
    """Free text. Optional for ``reject`` — invited by the UI, never required here (Q14, C-13) —
    and **required** for ``respond``, which the harness reads unconditionally."""

    changes: Mapping[str, str] = field(default_factory=dict)
    """For ``edit`` only: the fields the human changed. Merged over the action's own args."""

    allow_retarget: bool = False
    """Explicit permission to change ``file_path``. Editing *content* is routine; editing the
    **destination** silently redirects a write the human is looking at, so it takes a second act."""


@dataclass(frozen=True, slots=True)
class Resolution:
    """A validated answer to one approval — the id and the decisions, inseparable (CL-7)."""

    interrupt_id: str
    thread_id: str
    decisions: tuple[Decision, ...]

    def body(self) -> dict[str, Any]:
        """The ``POST /threads/{id}/interrupt`` body, verbatim (RO-12)."""
        return {
            "interrupt_id": self.interrupt_id,
            "decisions": [
                {
                    "type": decision.type,
                    "message": decision.message,
                    "edited_args": dict(decision.edited_args) if decision.edited_args else None,
                    "edited_tool": decision.edited_tool,
                }
                for decision in self.decisions
            ],
        }


def offered(action: ActionView, *, drop: Collection[DecisionType] = ()) -> tuple[DecisionType, ...]:
    """What a channel may put in front of the human — a **subsequence** of the server's list (CL-9).

    Narrowing is legitimate and widening is not: the server re-validates on the way in, so a
    hand-crafted request carrying ``edit`` against an action that forbids it is a 400 regardless of
    channel. Returning a subsequence rather than a set keeps the server's own ordering, which is
    what decides which button is first — and the first button is the one a hurried human presses.
    """
    return tuple(kind for kind in action.allowed_decisions if kind not in drop)


def resolve(request: ApprovalRequest, answers: Mapping[int, Answer]) -> Resolution:
    """Every action answered, positionally, validated before it leaves the client (CL-4 … CL-6).

    **Total by construction**: an answer for every action or an ``InvalidDecisionError`` here. The
    alternative — padding the gaps with a default — is how a human approves a second write they
    never looked at, because one approval can carry several actions and the UI showed them one.

    Validation runs through the seam's own :func:`~pkb.contracts.validate_decisions`, the same
    function the service and the runtime call, so a client never refuses something the daemon would
    accept and never sends something it would reject.
    """
    missing = [index for index in range(len(request.actions)) if index not in answers]
    if missing:
        raise InvalidDecisionError(
            f"every action needs an answer: {len(request.actions)} action(s), "
            f"missing {sorted(missing)}"
        )
    extra = sorted(index for index in answers if not 0 <= index < len(request.actions))
    if extra:
        raise InvalidDecisionError(f"no such action index: {extra}")

    decisions = tuple(
        _decision(request.actions[index], answers[index]) for index in range(len(request.actions))
    )
    validate_decisions(request, decisions, interrupt_id=request.interrupt_id)
    return Resolution(
        interrupt_id=request.interrupt_id,
        # CL-8: always the request's own thread. In a fan-out the gate parks on the expert's derived
        # thread, and posting a delegate's decisions to the Librarian's thread is a 409.
        thread_id=request.thread_id,
        decisions=decisions,
    )


def edited_args(
    action: ActionView, changes: Mapping[str, str], *, allow_retarget: bool = False
) -> dict[str, str]:
    """The **complete** argument map the harness will act on, not a patch (CL-14).

    ``edited_args`` replaces the tool call's arguments wholesale, so sending only what changed drops
    every field the human left alone — including the content of the file being written.

    Changing ``file_path`` needs ``allow_retarget``. Editing the *content* of a proposed write is
    what the modal is for; editing its **destination** silently redirects a write the human is
    looking at into a file they are not, which is a different act and takes a second one.
    """
    merged = dict(action.args)
    for key, value in changes.items():
        if (
            key == "file_path"
            and str(value) != str(merged.get("file_path", ""))
            and not allow_retarget
        ):
            raise InvalidDecisionError(
                "changing 'file_path' redirects the write to a file the human is not looking at; "
                "pass allow_retarget=True to mean it"
            )
        merged[key] = value
    return merged


def truncate(description: str, limit: int) -> tuple[str, bool]:
    """``(text, was_truncated)`` — cut on a **line boundary**, with a visible marker (CL-22).

    Shared because every channel with a length limit needs it and each would otherwise cut
    differently: Telegram's message limit is the obvious case, but a narrow terminal pane is the
    same problem. Cutting mid-line in a unified diff can turn a removal into what reads as an
    addition, and a silent cut is a diff the human approved without seeing all of.
    """
    if limit <= 0 or len(description) <= limit:
        return description, False
    budget = max(0, limit - len(TRUNCATION_MARKER))
    cut = description[:budget]
    boundary = cut.rfind("\n")
    if boundary > 0:
        cut = cut[:boundary]
    return cut + TRUNCATION_MARKER, True


def is_diff(description: str) -> bool:
    """Whether the server actually rendered a unified diff — see :data:`_DIFF_MARKER` (decision N)."""
    return _DIFF_MARKER in description


def _decision(action: ActionView, answer: Answer) -> Decision:
    if answer.type not in action.allowed_decisions:
        raise InvalidDecisionError(
            f"{action.tool!r} allows {list(action.allowed_decisions)}, not {answer.type!r}"
        )
    if answer.type != "edit":
        return Decision(type=answer.type, message=answer.message)
    return Decision(
        type="edit",
        message=answer.message,
        edited_args=edited_args(action, answer.changes, allow_retarget=answer.allow_retarget),
        # CL-15: an edit changes a call's *arguments*, never which tool runs. Retargeting the tool
        # is a different action entirely and the human approved this one.
        edited_tool=None,
    )
