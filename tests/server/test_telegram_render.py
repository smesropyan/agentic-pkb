"""The adapter's pure arithmetic — TG-44, TG-45, TG-54, TG-57, TG-64.

Nothing here touches a socket, a store, a service or the adapter object. These five rules are the
ones a reviewer cannot check by reading, because every one of them is a *measurement* the code makes
on the human's behalf and each is wrong in a way that looks right:

* **The unit is not the character.** ``truncate`` counts code points and Telegram counts UTF-16 code
  units, so the shipped shared cut passes a message the wire refuses — and a refused send is the
  human seeing *nothing*, not a short diff (P-26, TG-44).
* **A cut is allowed to be wrong; it is never allowed to be a lie.** LB-18 exists because a model
  claimed an expert had checked the knowledge base when none ran. A transport that summarised,
  reflowed or reordered to make a reply fit would be the same lie one layer down (TG-45).
* **64 bytes is the whole budget for a button.** Not enough for anything meaningful: a derived
  thread id alone is 60 characters, and neither Telegram client library enforces the limit at
  construction — it 400s at the server, i.e. at the moment a human is waiting on an approval
  (TG-57).
* **The keyboard is derived, never authored.** Dropping ``edit`` from today's twelve gate rows
  yields ``approve``/``reject`` every time, so the correct implementation and a hardcoded pair agree
  on every shipped input — which is exactly what lets the wrong one pass until a gate ships
  ``('edit', 'reject')`` and the bot draws a button the server rejects (TG-54, C-34).
* **A row is a thumb.** On a phone two buttons side by side are neighbouring keys, and there is no
  undo under any of them (TG-64, D6).
"""

from __future__ import annotations

import ast
import secrets
import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest

import pkb.server.telegram as telegram_module
from pkb.agents.gates import GATE_DECISIONS, GateReason
from pkb.clients.approval import truncate
from pkb.contracts import ActionView, DecisionType
from pkb.server.telegram import (
    NO_UNDO_REASONS,
    callback_data,
    fit,
    keyboard_for,
    parse_callback,
    split_message,
    utf16_len,
)
from pkb.server.telegram_api import CALLBACK_DATA_LIMIT, MESSAGE_LIMIT

SOURCE = Path(telegram_module.__file__)

FIRE = "\N{FIRE}"
"""U+1F525 — one Python character, **two** UTF-16 code units. The whole of P-26 in one symbol."""

P26 = FIRE * 3000 + "\nplain tail\n" + "x" * 500
"""The executed P-26 fixture: comfortably under 4096 *characters*, far over 4096 *units*.

A knowledge base about food, travel or code carries astral-plane characters routinely — this is a
plausible reply, not a pathological one.
"""

ASCII_LINES = "".join(f"{index:03d} " + "word " * 15 + "\n" for index in range(150))
"""Exactly 12,000 characters in 150 lines of 80 — TG-45's golden."""

CONTINUES = "\n… (continues)"
"""``fit``'s default marker. Reproduced because it is what the human *sees* where the text stopped;
a test that imported it could not notice the marker disappearing."""

EMITTED_VERBS = ("a", "r", "s", "ca", "cr", "x")
"""Every verb the adapter puts on a button: approve, reject, respond, the two confirmations of a
no-undo action, and cancel. The wire values, not the constants, because the wire is what 400s."""


def action(
    allowed: Sequence[DecisionType],
    *,
    reason: str = "breadth-approval",
    description: str = "--- a/topics/Cooking/notes/steak.md\n@@\n-old\n+new\n",
) -> ActionView:
    """One action carrying exactly the decisions a server said were legal."""
    return ActionView(
        tool="write_file",
        args={"path": "topics/Cooking/notes/steak.md"},
        description=description,
        allowed_decisions=tuple(allowed),
        reason=reason,
    )


def buttons(keyboard: Sequence[Sequence[dict[str, str]]]) -> list[dict[str, str]]:
    return [button for row in keyboard for button in row]


def names_in(function: str) -> set[str]:
    """Every identifier the *code* of one function uses — docstrings and comments excluded.

    Grep over source text cannot tell "this function documents that it never reflows" from "this
    function reflows", and TG-45 is asserted by absence.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Name):
                    found.add(inner.id)
                elif isinstance(inner, ast.Attribute):
                    found.add(inner.attr)
    assert found, f"{function} is not in {SOURCE.name} any more; this test must follow it"
    return found


def string_sequence_literals() -> list[set[str]]:
    """Every list/tuple/set literal of plain strings in the module."""
    literals: list[set[str]] = []
    for node in ast.walk(ast.parse(SOURCE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.List | ast.Tuple | ast.Set) and node.elts:
            values = [
                e.value
                for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
            if len(values) == len(node.elts):
                literals.append(set(values))
    return literals


def imported_modules() -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(SOURCE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return modules


# --------------------------------------------------------------------------------------
# § the unit is the UTF-16 code unit (TG-44)
# --------------------------------------------------------------------------------------


def test_utf16_len_counts_code_units_not_characters_tg44() -> None:
    """The measurement Telegram actually applies, on the three classes of character.

    ASCII and BMP text make the two counts agree, which is why a character-based budget survives
    every hand test someone types in English. An astral character costs two units, and that is the
    entire gap between "this fits" and ``400 message is too long``.
    """
    assert utf16_len("plain") == len("plain") == 5
    assert utf16_len("café ☕") == len("café ☕") == 6  # BMP: one unit each, counts agree
    assert utf16_len(FIRE) == 2
    assert len(FIRE) == 1
    assert utf16_len("") == 0


def test_a_character_budget_passes_a_message_telegram_refuses_tg44() -> None:
    """The P-26 regression, pinned: the shared cut does not cut, and the send is over the wire limit.

    ``truncate(P26, 4096)`` reports ``was_truncated=False`` — 3,512 characters is under 4,096, so
    there is nothing to do — while the same string is 6,512 UTF-16 units, which Telegram rejects
    outright. The human is not shown a truncated reply; they are shown nothing at all, and the only
    trace is a 400 in a log they do not read. This test is what stops anyone "simplifying" the
    adapter back onto the character-based budget.

    The spec quotes 3,517/6,517 for this fixture; the string it prints is 3,512/6,512, five
    characters of tail wording apart. Measured here rather than copied, because the number that
    matters is the **gap** — one extra unit for every one of the 3,000 emoji.
    """
    cut, was_truncated = truncate(P26, MESSAGE_LIMIT)
    assert was_truncated is False
    assert cut == P26
    assert len(P26) == 3512 <= MESSAGE_LIMIT
    assert utf16_len(P26) == 6512 > MESSAGE_LIMIT
    assert utf16_len(P26) - len(P26) == 3000  # exactly one extra unit per emoji

    fitted, cut_by_fit = fit(P26)
    assert cut_by_fit is True
    assert utf16_len(fitted) <= MESSAGE_LIMIT


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("all-emoji", FIRE * 5000),
        ("all-ascii", "x" * 12000),
        ("ascii-lines", ASCII_LINES),
        ("mixed", P26),
        ("mixed-lines", (FIRE * 200 + "\n" + "prose " * 40 + "\n") * 20),
        ("exactly-at-the-limit", "y" * MESSAGE_LIMIT),
        ("one-unit-over", FIRE * (MESSAGE_LIMIT // 2) + "z"),
    ],
)
def test_fit_bounds_every_content_shape_in_utf16_units_tg44(label: str, text: str) -> None:
    """Whatever the character-to-unit ratio of the content, what comes out is sendable.

    The ratio is not knowable in advance — it depends on the text — so the only safe method is to
    cut, re-measure and cut again, and the only honest assertion is on the *output*: every one of
    these seven shapes, emoji-only through ASCII-only, comes back inside 4,096 units. A budget
    computed once from a fixed ratio passes the ASCII rows and silently fails the first row.
    """
    fitted, was_cut = fit(text)
    assert utf16_len(fitted) <= MESSAGE_LIMIT, label
    assert was_cut is (utf16_len(text) > MESSAGE_LIMIT), label
    if not was_cut:
        assert fitted == text, label


@pytest.mark.parametrize("text", [FIRE * 5000, "x" * 12000, P26, ASCII_LINES])
def test_fit_only_ever_stops_early_it_never_rewrites_tg44(text: str) -> None:
    """What survives the cut is a **prefix** of what the model wrote, plus a visible marker.

    A budget search that re-encoded, normalised or re-wrapped to save units would produce something
    the human then approves or acts on that the agent never said. Cutting is allowed to lose the
    end; it is not allowed to change the beginning. The marker is asserted because a silent stop is
    a reply the human believes is complete.
    """
    fitted, was_cut = fit(text)
    assert was_cut is True
    assert fitted.endswith(CONTINUES)
    body = fitted.removesuffix(CONTINUES)
    assert text.startswith(body)
    assert body, "a cut that keeps nothing tells the human less than the marker alone"


def test_fit_takes_an_adapter_supplied_marker_tg44() -> None:
    """The preview under a keyboard says "full text above", not "open the TUI" (TG-56, decision U).

    ``truncate``'s own marker points at another client, which is exactly wrong when the whole text
    is three messages up in the same chat. The marker is the adapter's to choose, and the shared
    one must not leak through.
    """
    preview, was_cut = fit(P26, 1200, marker="\n… (full text above)")
    assert was_cut is True
    assert preview.endswith("\n… (full text above)")
    assert "open the TUI" not in preview
    assert utf16_len(preview) <= 1200


# --------------------------------------------------------------------------------------
# § splitting is on length, never on meaning (TG-45)
# --------------------------------------------------------------------------------------


def test_a_12000_character_reply_reassembles_byte_identically_tg45() -> None:
    """The golden: K parts, every one sendable, and the concatenation is the original byte for byte.

    LB-18 exists because a model asked to compose a reply claimed "the Cooking expert checked the
    knowledge base" when no expert ran. A transport that dropped, reordered or condensed a part to
    make a long reply fit would be the same lie one layer down — and worse, invisible, because the
    human has no copy to compare against. A length cut can be wrong; it cannot be a lie.
    """
    assert len(ASCII_LINES) == 12000
    parts = split_message(ASCII_LINES)

    assert len(parts) > 1, "12,000 characters must not fit in one 4,096-unit message"
    assert "".join(parts) == ASCII_LINES
    assert all(utf16_len(part) <= MESSAGE_LIMIT for part in parts)
    assert all(part for part in parts), "an empty part is a send Telegram rejects for nothing"


def test_every_boundary_falls_on_a_newline_tg45() -> None:
    """Cuts land between lines, so no line is ever split across two messages.

    Mid-line is how a unified diff turns a removal into what reads as an addition: ``-old`` cut
    after the minus is ``-`` on one screen and ``old`` on the next. The human approves the second
    screen. Every part but the last therefore ends at a line ending, and every part but the first
    starts at the beginning of one.
    """
    parts = split_message(ASCII_LINES)
    for part in parts[:-1]:
        assert part.endswith("\n")
    for part in parts[1:]:
        assert not part.startswith(" ")
        assert part.splitlines()[0] in ASCII_LINES.splitlines()


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("ascii", "x" * 12000),
        ("emoji", FIRE * 6000),
        ("no-trailing-newline", "line one\n" + "z" * 9000),
        ("long-line-then-short-lines", "q" * 9000 + "\nshort\nlines\n"),
    ],
)
def test_a_single_line_over_the_limit_is_still_cut_tg45(label: str, text: str) -> None:
    """A line longer than a whole message still has to go, and the split must terminate.

    There is no newline to cut on, so the rule degrades to a hard cut — the one case where a
    boundary is arbitrary. What must not degrade is termination and totality: a shrink that stops
    making progress hangs the outbox task with the human's reply inside it, and a shrink that loses
    the remainder drops the end of an answer with no marker saying so.
    """
    parts = split_message(text)
    assert "".join(parts) == text, label
    assert all(utf16_len(part) <= MESSAGE_LIMIT for part in parts), label
    assert all(part for part in parts), label
    assert len(parts) <= 16, label  # a non-shrinking cut would produce thousands, or never return


def test_text_that_fits_is_one_part_unchanged_tg45() -> None:
    """The common case: one message, no counter, nothing appended.

    Every reply from a phone-sized turn fits. If the fitting path also rewrote — normalising line
    endings, stripping a trailing newline — every ordinary message would differ from what the agent
    produced, which is the defect TG-45 forbids at its least visible.
    """
    for text in ("filed under Cooking", "", "one\ntwo\n", FIRE * 2048):
        assert split_message(text) == [text]


def test_the_split_path_holds_no_summariser_reflow_or_re_sort_tg45() -> None:
    """Asserted by absence: the split reads lengths, and never reads the text.

    ``textwrap.fill`` would silently reflow a diff into prose; a ``sorted`` would put the parts in
    an order the agent did not write; a model call would put a paraphrase under an approve button.
    None of them is a syntax error and none of them fails a round-trip test that only checks the
    part count — so the guard is that the names are simply not there.
    """
    used = names_in("split_message")
    forbidden = {
        "textwrap",
        "fill",
        "shorten",
        "wrap",
        "sorted",
        "sort",
        "reverse",
        "reversed",
        "summary",
        "summarize",
        "summarise",
        "service",
        "invoke",
        "ainvoke",
        "model",
        "sub",
        "replace",
        "strip",
    }
    assert not used & forbidden, sorted(used & forbidden)
    assert not imported_modules() & {"textwrap", "re", "difflib", "langchain"}


# --------------------------------------------------------------------------------------
# § callback_data is 64 bytes, and that is the whole state a button may carry (TG-57)
# --------------------------------------------------------------------------------------


def test_every_emitted_callback_data_fits_the_64_byte_budget_tg57() -> None:
    """Property, over real handles and every index and verb the adapter emits.

    Neither PTB nor aiogram enforces the limit at construction: both happily build a 65-byte button
    and the failure surfaces as ``400 BUTTON_DATA_INVALID`` from Telegram — at the moment a human is
    waiting on an approval, on the send that carries the buttons, so nothing arrives at all. A
    fan-out approval is the common case and indices grow with it, which is why the sweep runs to 99
    and the budget is measured in **bytes**, not characters.
    """
    for _ in range(200):
        handle = secrets.token_hex(4)
        for index in (0, 1, 9, 10, 42, 99):
            for verb in EMITTED_VERBS:
                data = callback_data(handle, index, verb)
                assert len(data.encode()) <= CALLBACK_DATA_LIMIT
                assert parse_callback(data) == (handle, index, verb)


def test_the_state_that_does_not_fit_is_pinned_tg57() -> None:
    """The arithmetic that forced an opaque handle over a self-describing payload.

    Measured with the real seam: a derived thread id is ``<uuid4>::topic/cooking/grilling`` = 60
    characters, and a payload carrying it beside a 32-hex interrupt id is 97 bytes — half again over
    the limit, for the *common* case. ``v1|7f3a2b1c|0|a`` is 15. Nothing meaningful fits, so the
    button carries a key and the durable prompts row carries the state; the lookup is mandatory, not
    an optimisation, and this is the number that says so.
    """
    thread_id = f"{uuid.uuid4()}::topic/cooking/grilling"
    assert len(thread_id) == 60
    interrupt_id = secrets.token_hex(16)  # xxh3_128 hexdigest — 32 characters
    assert len(f"a|{thread_id}|{interrupt_id}|0".encode()) == 97 > CALLBACK_DATA_LIMIT

    assert callback_data("7f3a2b1c", 0, "a") == "v1|7f3a2b1c|0|a"
    assert len(b"v1|7f3a2b1c|0|a") == 15


def test_callback_data_refuses_to_emit_an_oversized_payload_tg57() -> None:
    """The adapter validates the budget itself rather than trusting the caller or the library.

    If a future handle, verb or index scheme grows past 64 bytes, this raises here — in the code
    that built it — instead of 400ing at Telegram with a human watching a spinner. A silent pass is
    an approval that can never be answered from the phone.
    """
    with pytest.raises(ValueError, match="64"):
        callback_data(secrets.token_hex(32), 0, "a")


def test_parse_callback_round_trips_what_callback_data_emits_tg57() -> None:
    """The button is the only thing that comes back, so the pair must be exact inverses.

    An index that round-trips as a string, or a handle the parser trims, resolves the wrong action
    of a fan-out — approving a write the human rejected, with no undo.
    """
    for index in (0, 7, 99):
        handle = secrets.token_hex(4)
        assert parse_callback(callback_data(handle, index, "cr")) == (handle, index, "cr")


@pytest.mark.parametrize(
    ("label", "data"),
    [
        ("empty", ""),
        ("too-few-fields", "v1|7f3a2b1c|0"),
        ("too-many-fields", "v1|7f3a2b1c|0|a|extra"),
        ("wrong-version", "v2|7f3a2b1c|0|a"),
        ("no-version", "7f3a2b1c|0|a"),
        ("index-not-a-number", "v1|7f3a2b1c|first|a"),
        ("empty-verb", "v1|7f3a2b1c|0|"),
        ("someone-elses-scheme", '{"handle": "7f3a2b1c"}'),
        ("bare-word", "approve"),
    ],
)
def test_parse_callback_rejects_rather_than_guesses_tg57(label: str, data: str) -> None:
    """Anything that is not this version's shape is ``None``, and ``None`` means do nothing.

    A button lives in a chat forever, so a payload from a previous version of the adapter can arrive
    at any time. Guessing at it — defaulting the index to 0, or the verb to approve — applies a
    decision to an action the human never saw. Refusing costs one unanswered press; guessing writes
    to the knowledge base.
    """
    assert parse_callback(data) is None, label


# --------------------------------------------------------------------------------------
# § the keyboard is derived from allowed_decisions (TG-54)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("reason", list(GateReason), ids=[r.value for r in GateReason])
def test_the_keyboard_is_the_servers_decisions_minus_edit_tg54(reason: GateReason) -> None:
    """All twelve shipped gate rows, against the table itself rather than against a literal pair.

    This is worth writing precisely *because* it looks redundant: dropping ``edit`` yields
    ``('approve', 'reject')`` for eleven reasons and leaves ``delete``'s pair untouched, so today a
    hardcoded approve/reject bar agrees with the derivation on every input that exists. That
    agreement is what would let the wrong implementation pass every test right up to the day a gate
    ships ``('edit', 'reject')`` and the bot draws an Approve button the server answers with a 400.
    Order is asserted too: it is the server's, and the first button is the one a hurried thumb hits.
    """
    expected = tuple(d for d in GATE_DECISIONS[reason] if d != "edit")
    keyboard = keyboard_for(action(GATE_DECISIONS[reason], reason=reason.value), "7f3a2b1c", 0)

    assert keyboard is not None
    labels = {"approve": "Approve", "reject": "Reject", "respond": "Respond"}
    assert [button["text"] for button in buttons(keyboard)] == [labels[d] for d in expected]
    assert all(parse_callback(button["callback_data"]) is not None for button in buttons(keyboard))


@pytest.mark.parametrize(
    ("label", "allowed", "expected"),
    [
        ("edit-and-reject", ("edit", "reject"), ["Reject"]),
        ("respond-only", ("respond",), ["Respond"]),
        ("reject-first", ("reject", "approve"), ["Reject", "Approve"]),
        ("everything", ("approve", "edit", "reject", "respond"), ["Approve", "Reject", "Respond"]),
    ],
)
def test_a_gate_todays_table_does_not_ship_is_still_rendered_tg54(
    label: str, allowed: tuple[DecisionType, ...], expected: list[str]
) -> None:
    """The inputs that tell a derivation apart from a hardcoded pair.

    ``('edit', 'reject')`` is the case that matters: the correct keyboard offers Reject alone, and a
    hardcoded bar offers Approve — a button that, pressed, sends a decision ``validate_decisions``
    rejects, so the human is told their approval failed for reasons that have nothing to do with the
    knowledge base. ``('reject', 'approve')`` pins that the channel narrows without re-ordering.
    """
    keyboard = keyboard_for(action(allowed), "7f3a2b1c", 3)
    assert keyboard is not None
    assert [button["text"] for button in buttons(keyboard)] == expected, label
    assert all(parse_callback(b["callback_data"])[1] == 3 for b in buttons(keyboard))  # type: ignore[index]


def test_the_module_holds_no_hardcoded_approve_reject_pair_tg54() -> None:
    """Asserted by absence, because the correct and the wrong answer agree on every shipped input.

    Arch §6 says "the Telegram adapter narrows allowed_decisions to approve/reject", which reads as
    a specification and is only a description of today's table (C-34). A ``["approve", "reject"]``
    anywhere in this module is that sentence implemented — passing every test in this file's table
    until the table changes under it.
    """
    assert {"approve", "reject"} not in string_sequence_literals()
    assert "offered" in names_in("keyboard_for")


# --------------------------------------------------------------------------------------
# § one row is one thumb (TG-64)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("reason", list(GateReason), ids=[r.value for r in GateReason])
def test_approve_and_reject_are_never_in_one_row_tg64(reason: GateReason) -> None:
    """Every gate row: the two opposite answers are never neighbouring keys.

    A phone keyboard row is thumb-width. Approve and Reject side by side, read on a moving train,
    is one mis-tap away from an irreversible write to a knowledge base with no version control and
    no undo (D6) — and the mis-tap is indistinguishable from the intent, because the only record is
    the decision itself.
    """
    keyboard = keyboard_for(action(GATE_DECISIONS[reason], reason=reason.value), "7f3a2b1c", 0)
    assert keyboard is not None
    for row in keyboard:
        texts = {button["text"] for button in row}
        assert not {"Approve", "Reject"} <= texts, row


@pytest.mark.parametrize(
    ("label", "allowed"),
    [("nothing-allowed", ()), ("edit-only", ("edit",))],
)
def test_nothing_offerable_gives_no_keyboard_not_an_empty_one_tg64(
    label: str, allowed: tuple[DecisionType, ...]
) -> None:
    """``None``, so the message goes out as a hand-off — never a keyboard with no buttons.

    An empty ``inline_keyboard`` renders as a message whose buttons failed to load, which reads as
    a delivery fault and invites the human to wait for a retry that will never come. A message with
    no buttons at least reads as "answer this somewhere else", which is true: the interrupt is still
    parked and the TUI can resolve it (TG-55).
    """
    assert keyboard_for(action(allowed), "7f3a2b1c", 0) is None, label


def test_the_no_undo_reasons_are_real_gate_reasons_tg64() -> None:
    """The three destructive reasons, spelled the way the server spells them.

    They are matched against ``ActionView.reason``, which is a slug on the wire — so a typo or a
    renamed enum member does not fail anywhere. It silently removes the second tap and the "there is
    no undo" line from a delete, and the first thing anybody learns about it is a file that is gone.
    """
    shipped = {reason.value for reason in GateReason}
    destructive = {
        GateReason.DELETE.value,
        GateReason.TOPIC_CREATION.value,
        GateReason.CONFLICT_RESOLUTION.value,
    }
    assert set(NO_UNDO_REASONS) - shipped == set()
    assert set(NO_UNDO_REASONS) == destructive


def test_the_confirm_step_still_fits_the_button_budget_tg64() -> None:
    """The second tap is a button too, and its verbs are longer than the first tap's.

    The confirmation replaces the keyboard with ``confirm-approve``/``confirm-reject``/``cancel``
    against the same handle and index. If the extra byte pushed the payload over 64, the destructive
    reasons — the only ones that need confirming — would be the only ones that could not be
    answered from the phone.
    """
    for verb in ("ca", "cr", "x"):
        data = callback_data(secrets.token_hex(4), 99, verb)
        assert len(data.encode()) <= CALLBACK_DATA_LIMIT
        assert parse_callback(data) == (data.split("|")[1], 99, verb)


# --------------------------------------------------------------------------------------
# § the part counter is part of the payload (TG-45 against TG-44) — a source bug
# --------------------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="BUG: TelegramAdapter._send prepends '(i/n)\\n' to a part split_message already sized "
    "to the full 4096-unit limit, so the send is 4102 units and Telegram refuses it — the "
    "human sees nothing. The counter has to be inside the split's budget.",
    strict=True,
)
def test_the_mechanical_counter_fits_inside_the_wire_limit_tg45() -> None:
    """TG-45 allows a ``(2/4)`` counter; TG-44 says the budget must cover what is actually sent.

    ``_send`` splits, then prefixes the counter to each part — but ``split_message`` fills each part
    to 4,096 units, so the counter lands *outside* the budget it was measured against. A reply whose
    lines happen to pack flush against the limit produces a first message of 4,102 units, which is
    the P-26 failure again one line further along: not a truncated reply, no reply.
    """
    text = ("x" * 4095 + "\n") * 3
    parts = split_message(text)
    assert len(parts) > 1
    for position, part in enumerate(parts):
        label = f"({position + 1}/{len(parts)})\n"
        assert utf16_len(label + part) <= MESSAGE_LIMIT, f"part {position + 1} is over the limit"
