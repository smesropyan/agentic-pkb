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
from collections.abc import Sequence
from pathlib import Path

import pytest

import pkb.server.telegram as telegram_module
from pkb.clients.approval import truncate
from pkb.contracts import ActionView, DecisionType
from pkb.server.telegram import split_message, utf16_len
from pkb.server.telegram_api import MESSAGE_LIMIT

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

EMITTED_VERBS = ("a", "r", "ca", "cr", "x")
"""Every verb the adapter puts on a button: approve, reject, the two confirmations of a no-undo
action, and cancel. The wire values, not the constants, because the wire is what 400s.

``respond`` is absent because the channel narrows it away (TG-54, TG-65): ``validate_decisions``
requires a message on it and this channel may not demand prose from a phone."""


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


def string_constants() -> set[str]:
    """Every string literal the module's *code* holds — docstrings excluded.

    Docstrings quote constants on purpose (``fit`` explains why ``TRUNCATION_MARKER``'s default is
    wrong for its caller); a duplicate in the code is the drift being asserted against.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }


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


# --------------------------------------------------------------------------------------
# § the keyboard is derived from allowed_decisions (TG-54)
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# § one row is one thumb (TG-64)
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# § the part counter is part of the payload (TG-45 against TG-44)
# --------------------------------------------------------------------------------------


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


# --------------------------------------------------------------------------------------
# § both directions read one verb table (TG-54)
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# § the bot renders ids, it never makes them (TG-40)
# --------------------------------------------------------------------------------------
