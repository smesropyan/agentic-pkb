"""The approval modal — "the piece that matters most" (arch §6; TU-39 … TU-50).

Every rule here is about a write with no undo (D6), so each one is stated as the failure it
prevents rather than as a preference.

* **The description is rendered verbatim** (TU-39). It already holds the server-rendered unified
  diff and any validation finding, rendered once so this modal and Telegram's keyboard decide about
  the same bytes. The TUI computes no diff, reads no file and imports no diff library — under I2 it
  could not read the tree correctly anyway, and a second diff renderer is a second answer to "what
  am I approving".
* **Colour only inside a real hunk** (TU-40). ``describe_write`` emits five shapes and only one is a
  diff: a *new* file gets ``Proposed content:`` and raw markdown. Feeding that to a diff lexer paints
  every ``- `` bullet in the deletion colour — telling the human that lines being **added** are being
  removed. Markdown bullets are the most common content in this knowledge base.
* **Every widget carrying server text is ``markup=False``** (TU-41). ``Static`` and ``Label`` default
  to markup on, and a description containing ``[/Users/me/kb]`` raises ``MarkupError`` — which kills
  the app, not the widget. Paths and free model text are exactly what these fields carry. This is
  the single most likely production crash in the layer.
* **The controls come from ``allowed_decisions``, verbatim** (TU-42). The modal may omit one it
  cannot support and may never enable one the server did not allow. A widened UI produces a 400 the
  human caused by clicking a button the TUI drew.
* **It can be dismissed without deciding** (TU-47), and dismissing sends nothing. The interrupt
  stays parked in the checkpoint and stays resolvable from any channel. An escape that silently
  submitted a reject would file nothing and tell the agent the human said no — indistinguishable,
  afterwards, from a considered refusal.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, Final

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from pkb.clients.approval import Answer, is_diff, offered, resolve
from pkb.contracts import ActionView, ApprovalRequest, DecisionType, InvalidDecisionError

__all__ = ["ApprovalModal", "diff_text", "validation_header"]

VALIDATION_LABEL: Final = "This draft currently fails validation:"
"""``gates._validation_label``'s exact text. Matched as a **prefix**, never re-validated (TU-46).

The label exists so a human can reject or edit *instead of* approving a draft the validator will
refuse a moment later — which burns one of three attempts (MW-14) on content they endorsed. Buried
under a two-hundred-line diff it is a label nobody reads, so the modal lifts it above the fold.
"""

NO_UNDO_REASONS: Final = frozenset({"delete", "topic-creation", "conflict-resolution"})
"""Gate reasons that carry an explicit "there is no undo" warning (TU-45).

With no version control in the first draft, a delete approved by mistake is gone, and the modal is
the last place that can say so.
"""

_HUNK: Final = "@@"

_ADD: Final = "green"
_REMOVE: Final = "red"
_META: Final = "bold"


def diff_text(description: str) -> Text:
    """The description as a ``rich`` renderable — coloured **only inside a real hunk** (TU-40).

    Returned as a renderable rather than markup so no widget has to parse it, which is also what
    keeps TU-41 true: a ``Text`` object is never scanned for square brackets.
    """
    body = Text(no_wrap=False)
    if not is_diff(description):
        # A `(new file)` description is raw markdown. Colouring it would paint every `- ` bullet as
        # a deletion, which is the opposite of what is happening.
        body.append(description)
        return body

    in_hunk = False
    for index, line in enumerate(description.splitlines()):
        if line.startswith(_HUNK):
            in_hunk = True
            body.append(line + "\n", style=_META)
            continue
        if not in_hunk:
            body.append(line + "\n", style=_META if index == 0 else None)
            continue
        style = _ADD if line.startswith("+") else _REMOVE if line.startswith("-") else None
        body.append(line + "\n", style=style)
    return body


def validation_header(description: str) -> str:
    """The validation label, lifted out of the description — or ``""`` (TU-46)."""
    for line in description.splitlines():
        if line.startswith(VALIDATION_LABEL):
            return line.strip()
    return ""


class ApprovalModal(ModalScreen[Any]):
    """One approval, one submission of N decisions in order (TU-43).

    Dismisses with a :class:`~pkb.clients.approval.Resolution` when the human decides, and with
    ``None`` when they choose "later" — which sends nothing at all.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        # TU-50: approve and reject are deliberately **not adjacent**. There is no undo, and a
        # mistyped neighbour key on a knowledge-base write is unrecoverable.
        ("a", "decide('approve')", "Approve"),
        ("e", "decide('edit')", "Edit"),
        ("r", "decide('reject')", "Reject"),
        ("escape", "later", "Later"),
    ]

    def __init__(self, request: ApprovalRequest) -> None:
        super().__init__()
        self.request = request
        self._edits: dict[int, dict[str, str]] = {}

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="approval"):
            yield Label(f"{len(self.request.actions)} action(s) need your decision", markup=False)
            for index, action in enumerate(self.request.actions):
                yield from self._action(index, action)
        with Horizontal(id="approval-controls"):
            for kind in self._offered():
                yield Button(kind.title(), id=f"decide-{kind}")
            yield Button("Later", id="decide-later")

    def _action(self, index: int, action: ActionView) -> ComposeResult:
        # markup=False on every one of these: they carry KB paths and free model text, and
        # `[/kb/Cooking/notes]` in a markup-enabled widget raises MarkupError and kills the app.
        yield Label(f"{action.tool}  ·  {action.reason}", markup=False)
        if action.reason in NO_UNDO_REASONS:
            yield Label("There is no undo for this.", markup=False, classes="warning")
        header = validation_header(action.description)
        if header:
            yield Label(header, markup=False, classes="warning")
        yield Static(diff_text(action.description), id=f"description-{index}")
        if "edit" in action.allowed_decisions:
            # TU-44: a key → single-string editor over `args`, seeded from `args` and never from
            # `description`. Only `write_file` carries a document; `edit_file` has old/new strings
            # and `create_topic` has no path at all. And the displayed diff is context-limited, so
            # reconstructing content from it would differ subtly from what the human read.
            for key, value in action.args.items():
                yield Input(value=str(value), id=f"arg-{index}-{key}", classes="arg")
        elif action.allowed_decisions:
            yield Label(
                "This action cannot be usefully edited into a different one.",
                markup=False,
                classes="hint",
            )

    def _offered(self) -> tuple[DecisionType, ...]:
        """The union of what every action allows — the modal offers no more (TU-42)."""
        seen: list[DecisionType] = []
        for action in self.request.actions:
            for kind in offered(action):
                if kind not in seen:
                    seen.append(kind)
        return tuple(seen)

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        name = str(event.button.id or "")
        if name == "decide-later":
            self.action_later()
            return
        self.action_decide(name.removeprefix("decide-"))

    def action_later(self) -> None:
        """Dismiss without deciding. **Sends nothing** (TU-47)."""
        self.dismiss(None)

    def action_decide(self, kind: str) -> None:
        """One submission of N decisions, in the request's own order (TU-43).

        Submitting them one at a time would make the second stale against the interrupt the first
        already resolved — and RT-41 batches every interruptible call of one message into a single
        interrupt, so two actions is the normal case rather than the exotic one.
        """
        answers = {
            index: Answer(
                type=kind,  # type: ignore[arg-type]
                changes=self._changes(index, action) if kind == "edit" else {},
            )
            for index, action in enumerate(self.request.actions)
        }
        try:
            self.dismiss(resolve(self.request, answers))
        except InvalidDecisionError as exc:
            # The shared validator refused before anything left the client — which is the point of
            # it living in the seam. Say so and stay open rather than sending a request the daemon
            # would refuse with a 400 the human caused by pressing a key the modal drew.
            self.notify(str(exc), severity="error")

    def _changes(self, index: int, action: ActionView) -> Mapping[str, str]:
        changed: dict[str, str] = {}
        for key in action.args:
            try:
                field = self.query_one(f"#arg-{index}-{key}", Input)
            except Exception:
                continue
            if field.value != str(action.args[key]):
                changed[key] = field.value
        return changed
