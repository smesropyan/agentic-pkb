"""YAML frontmatter: parsing, canonical serialization, and surgical field edits (FM-1 … FM-15).

Three write paths exist for a reason:

* :func:`parse` is **total** (FM-13). A hand-edited tree must never stop a flush, so a malformed
  block becomes a structured failure, and a well-formed block with a badly typed value becomes a
  :class:`~pkb.core.models.FieldProblem` — never an exception, never a guess (FM-4, FM-5).
* :func:`serialize` renders the **canonical** block: fixed key order (FM-7) and fixed style (FM-8).
  The scaffolder and the generators use it, because they own every byte of what they write.
* :func:`set_field` / :func:`remove_field` are **surgical** (FM-11). They rewrite the target key's
  lines and nothing else, so a human's key order, quoting, flow/block choice, comments, and the
  whole markdown body survive byte-for-byte. Which lines a key owns comes from the parser, never
  from a guess at the text — and a block Layer 1 cannot read, or cannot slice into whole lines, is
  left entirely alone (:func:`_editable`). There is no version control (arch D6), so a gratuitous
  rewrite is unrecoverable noise and a wrong one is unrecoverable loss.

``source_type`` vocabulary follows decision A of the rules document, not FM-6's literal five-value
list: the authored enum stays at README §1.4's four values, and ``topic.md`` carries
``source_type: summary``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from io import StringIO
from typing import Any, Final

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from pkb.core.models import FieldProblem, Metadata, ParsedDocument

__all__ = [
    "AUTHORED_SOURCE_TYPES",
    "CANONICAL_ORDER",
    "DELIMITER",
    "DERIVED_SOURCE_TYPES",
    "KNOWN_FIELDS",
    "OPTIONAL_FIELDS",
    "REQUIRED_FIELDS",
    "SOURCE_TYPES",
    "normalize_related_topic",
    "parse",
    "remove_field",
    "serialize",
    "set_field",
    "unknown_values",
]

# --------------------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------------------

DELIMITER: Final = "---"
"""The frontmatter fence — the whole line, and the file's first line (FM-1)."""

REQUIRED_FIELDS: Final[frozenset[str]] = frozenset(
    {"title", "description", "topic", "tags", "created", "updated", "source_type"}
)
"""Exactly seven, on every authored file (FM-2)."""

OPTIONAL_FIELDS: Final[frozenset[str]] = frozenset({"related_topics"})
"""The only recognized optional field; it defaults to ``[]`` (T-12)."""

KNOWN_FIELDS: Final[frozenset[str]] = REQUIRED_FIELDS | OPTIONAL_FIELDS
"""Everything else is preserved and reported as unknown (FM-10)."""

CANONICAL_ORDER: Final[tuple[str, ...]] = (
    "title",
    "description",
    "topic",
    "tags",
    "created",
    "updated",
    "related_topics",
    "source_type",
)
"""Serialization key order; unknown keys follow in first-seen order (FM-7, T-12)."""

AUTHORED_SOURCE_TYPES: Final[frozenset[str]] = frozenset(
    {"note", "reference", "solution", "summary"}
)
"""Legal on a human- or agent-authored file (FM-6 as amended by decision A)."""

DERIVED_SOURCE_TYPES: Final[frozenset[str]] = frozenset({"index", "catalog", "tag-registry"})
"""Reserved for generated files; never legal on an authored one (FM-6, VA-31)."""

SOURCE_TYPES: Final[frozenset[str]] = AUTHORED_SOURCE_TYPES | DERIVED_SOURCE_TYPES

_TAG_NAMESPACES: Final[frozenset[str]] = frozenset({"topic", "status", "type", "domain"})
"""Closed namespace set (TG-2); ``normalize_related_topic`` keys off it (FM-15)."""

_QUOTED_FIELDS: Final[frozenset[str]] = frozenset({"title", "description", "topic"})
"""Always double-quoted on write (FM-8)."""

_LIST_FIELDS: Final[frozenset[str]] = frozenset({"tags", "related_topics"})
_FLOW_FIELDS: Final[frozenset[str]] = frozenset({"related_topics"})
"""Rendered as ``[ a, b ]`` rather than a block sequence (FM-8)."""

_DATE_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}")
"""FM-5: no time, no timezone, zero-padded. ``2024-1-5`` and datetimes are ``DATE_FORMAT``."""

_SEQ_INDENT: Final = "  "
"""Canonical block-sequence indent, matching README §1.4's ``  - topic.cooking.grilling``."""

# A plain (unquoted) scalar we are willing to emit: no indicators, no spaces, no chance of the
# loader reading it back as a number, bool, or null.
_PLAIN_SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+-]*")
_NUMERIC = re.compile(r"[-+]?(\d[\d_]*(\.[\d_]*)?|\.[\d_]+)([eE][-+]?\d+)?")
_YAML_WORDS: Final[frozenset[str]] = frozenset(
    {"y", "n", "yes", "no", "true", "false", "on", "off", "null", "none", "~"}
)
_SHORT_ESCAPES: Final[Mapping[str, str]] = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


# --------------------------------------------------------------------------------------
# Splitting the fence off the body
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Split:
    """Where the frontmatter lives inside a document (FM-1)."""

    yaml_text: str
    body: str
    start: int
    """Offset of the first YAML byte — just past the opening fence's newline."""

    end: int
    """Offset of the closing fence line, or of end-of-text when unterminated."""

    terminated: bool


def _strip_eol(line: str) -> str:
    return line.rstrip("\r")


def _split_document(text: str) -> _Split | None:
    """Locate the block, or ``None`` when the file does not open with a fence (FM-1).

    Splitting is done on ``\\n`` only — never ``str.splitlines``, which also breaks on ``\\v``,
    ``\\f`` and ``U+2028`` and would corrupt a body containing them.
    """
    lines = text.split("\n")
    if _strip_eol(lines[0]) != DELIMITER:
        return None
    start = len(lines[0]) + 1
    cursor = start
    for line in lines[1:]:
        if _strip_eol(line) == DELIMITER:
            return _Split(text[start:cursor], text[cursor + len(line) + 1 :], start, cursor, True)
        cursor += len(line) + 1
    return _Split(text[start:], "", start, len(text), False)


def _to_lines(chunk: str) -> list[str]:
    """Newline-terminated lines without their terminators; ``""`` yields no lines."""
    if not chunk:
        return []
    lines = chunk.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def _from_lines(lines: Sequence[str]) -> str:
    return "".join(f"{line}\n" for line in lines)


# --------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------


def _reader() -> YAML:
    """Round-trip loader: tolerates inline comments (FM-9) and reports precise error marks."""
    yaml = YAML()
    yaml.width = 1 << 20
    return yaml


def _yaml_error(exc: YAMLError) -> tuple[str, int | None]:
    """One-line message plus the 1-based line *within the file* (FM-13, VA-39)."""
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    problem = getattr(exc, "problem", None)
    message = " ".join(str(problem if problem else exc).split())
    # ``mark.line`` is 0-based within the YAML text, which itself starts on file line 2.
    line = mark.line + 2 if mark is not None else None
    return message, line


@dataclass(frozen=True, slots=True)
class _Load:
    """A loaded block: the mapping of fields it holds, or why it is not one (FM-13)."""

    mapping: Mapping[Any, Any] | None
    error: str | None = None
    error_line: int | None = None


_EMPTY_BLOCK: Final[Mapping[Any, Any]] = {}
"""An empty block is well-formed but carries nothing; VA-3 turns that into a finding."""


def _load_block(yaml_text: str) -> _Load:
    """Read one frontmatter block. Never raises (FM-13).

    The single definition of "frontmatter Layer 1 can read", shared by :func:`parse` and the
    surgical writers below. They must agree exactly: a writer that edits a block the parser
    rejected is repairing a file nobody could read, which §5 forbids at any layer.
    """
    try:
        loaded = _reader().load(yaml_text)
    except YAMLError as exc:
        message, line = _yaml_error(exc)
        return _Load(None, message, line)
    except Exception as exc:
        # Not every loader failure is a YAMLError: an impossible timestamp such as `2024-13-45`
        # reaches ``datetime.date`` and surfaces as a bare ValueError. FM-13 makes totality the
        # contract, so every construction failure becomes a parse failure.
        detail = " ".join(str(exc).split())
        return _Load(None, f"invalid frontmatter value: {detail}")
    if loaded is None:
        return _Load(_EMPTY_BLOCK)
    if not isinstance(loaded, Mapping):
        return _Load(None, f"frontmatter must be a mapping of fields, got {_typename(loaded)}", 2)
    return _Load(loaded)


def parse(text: str) -> ParsedDocument:
    """Parse a markdown document into frontmatter and body. Never raises (FM-1, FM-9, FM-13).

    A file that does not open with ``---`` has no frontmatter: ``meta`` is ``None`` and the whole
    file is the body. An unterminated or unparseable block yields ``error``/``error_line`` with
    ``meta`` still ``None``, so one bad file cannot abort a tree walk.
    """
    split = _split_document(text)
    if split is None:
        return ParsedDocument(body=text)
    if not split.terminated:
        return ParsedDocument(
            body="",
            error="unterminated frontmatter block: no closing '---'",
            error_line=1,
        )
    load = _load_block(split.yaml_text)
    if load.mapping is None:
        return ParsedDocument(body=split.body, error=load.error, error_line=load.error_line)

    raw: dict[str, Any] = {str(key): value for key, value in load.mapping.items()}
    return ParsedDocument(body=split.body, raw=raw, meta=_build_metadata(load.mapping))


def _typename(value: object) -> str:
    if value is None:
        return "null"
    return type(value).__name__


def _build_metadata(loaded: Mapping[Any, Any]) -> Metadata:
    """Coerce a loaded mapping into the typed view (FM-4, FM-5, FM-10).

    Coercion is lenient and total: a badly typed value leaves its field ``None``/empty and records
    a :class:`FieldProblem`. Vocabulary membership (``source_type``, tag namespaces) is *not*
    checked here — that is validation's job, and it needs the offending value to survive parsing.
    """
    present: list[str] = []
    unknown: list[str] = []
    problems: list[FieldProblem] = []
    values: dict[str, Any] = {}

    for key, value in loaded.items():
        name = str(key)
        present.append(name)
        if name in KNOWN_FIELDS:
            values[name] = value
        else:
            unknown.append(name)

    def text_field(name: str) -> str | None:
        return _coerce_text(name, values[name], problems) if name in values else None

    def date_field(name: str) -> date | None:
        return _coerce_date(name, values[name], problems) if name in values else None

    return Metadata(
        title=text_field("title"),
        description=text_field("description"),
        topic=text_field("topic"),
        tags=(
            _coerce_str_list("tags", values["tags"], problems, require_non_empty=True)
            if "tags" in values
            else ()
        ),
        created=date_field("created"),
        updated=date_field("updated"),
        related_topics=(
            _coerce_str_list(
                "related_topics", values["related_topics"], problems, require_non_empty=False
            )
            if "related_topics" in values
            else ()
        ),
        source_type=text_field("source_type"),
        unknown_fields=tuple(unknown),
        bad_fields=tuple(problems),
        present_keys=tuple(present),
    )


def _coerce_text(name: str, value: Any, problems: list[FieldProblem]) -> str | None:
    """Non-empty string, or a ``FIELD_TYPE``/``EMPTY_FIELD`` problem (FM-4)."""
    if not isinstance(value, str):
        problems.append(
            FieldProblem(name, "FIELD_TYPE", f"expected a string, got {_typename(value)}")
        )
        return None
    if not value.strip():
        problems.append(FieldProblem(name, "EMPTY_FIELD", "value is empty"))
        return None
    return str(value)


def _coerce_date(name: str, value: Any, problems: list[FieldProblem]) -> date | None:
    """Calendar date only — no time, no timezone (FM-5)."""
    if isinstance(value, datetime):
        problems.append(
            FieldProblem(
                name, "DATE_FORMAT", "expected a calendar date YYYY-MM-DD, got a date-time"
            )
        )
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str) and _DATE_TEXT.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            problems.append(FieldProblem(name, "DATE_FORMAT", f"not a real date: {value!r}"))
            return None
    problems.append(
        FieldProblem(name, "DATE_FORMAT", f"expected a date YYYY-MM-DD, got {_short(value)}")
    )
    return None


def _coerce_str_list(
    name: str, value: Any, problems: list[FieldProblem], *, require_non_empty: bool
) -> tuple[str, ...]:
    """List of strings; a scalar or a mixed list is a ``FIELD_TYPE`` problem (FM-4)."""
    if not isinstance(value, list):
        problems.append(
            FieldProblem(name, "FIELD_TYPE", f"expected a list of strings, got {_typename(value)}")
        )
        return ()
    for index, item in enumerate(value):
        if not isinstance(item, str):
            problems.append(
                FieldProblem(
                    name,
                    "FIELD_TYPE",
                    f"item {index} is not a string: {_short(item)}",
                )
            )
            return ()
    if require_non_empty and not value:
        problems.append(FieldProblem(name, "EMPTY_FIELD", "list is empty"))
        return ()
    return tuple(str(item) for item in value)


def _short(value: object) -> str:
    rendered = " ".join(str(value).split())
    return rendered if len(rendered) <= 40 else f"{rendered[:37]}..."


def unknown_values(doc: ParsedDocument) -> dict[str, Any]:
    """The unknown keys' loaded values, ready to hand back to :func:`serialize` (FM-10).

    ``Metadata`` records unknown key *names* only, so preserving them across a canonical rewrite
    needs the raw mapping too.
    """
    if doc.meta is None or doc.raw is None:
        return {}
    return {key: doc.raw[key] for key in doc.meta.unknown_fields if key in doc.raw}


# --------------------------------------------------------------------------------------
# Canonical serialization
# --------------------------------------------------------------------------------------


def _escape(char: str) -> str:
    """Escape one character for a double-quoted scalar, or return it unchanged.

    C0/C1 controls, DEL, and the three characters YAML additionally treats as line breaks
    (U+0085, U+2028, U+2029) must be escaped or the value would not survive a reload.
    """
    if char in _SHORT_ESCAPES:
        return _SHORT_ESCAPES[char]
    code = ord(char)
    if code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F:
        return f"\\x{code:02x}"
    if char in ("\u2028", "\u2029", "\ufeff"):
        return f"\\u{code:04x}"
    return char


def _quote(value: str) -> str:
    """A YAML double-quoted scalar (FM-8)."""
    return '"' + "".join(_escape(char) for char in value) + '"'


def _plain_or_quoted(value: str) -> str:
    """Bare when it round-trips as itself, quoted otherwise."""
    if (
        _PLAIN_SAFE.fullmatch(value)
        and value.lower() not in _YAML_WORDS
        and not _NUMERIC.fullmatch(value)
    ):
        return value
    return _quote(value)


def _scalar(key: str, value: object) -> str:
    """One value rendered in canonical style (FM-5, FM-8)."""
    if isinstance(value, datetime):
        return _quote(value.isoformat())
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if value is None:
        return ""
    text = str(value)
    return _quote(text) if key in _QUOTED_FIELDS else _plain_or_quoted(text)


def _render_pair(key: str, value: object, *, flow: bool, indent: str = _SEQ_INDENT) -> list[str]:
    """Render ``key: value`` as the lines it occupies (FM-8)."""
    if isinstance(value, list | tuple):
        items = list(value)
        if flow:
            inner = ", ".join(_plain_or_quoted(str(item)) for item in items)
            return [f"{key}: [ {inner} ]" if items else f"{key}: []"]
        if not items:
            return [f"{key}: []"]
        return [f"{key}:", *[f"{indent}- {_plain_or_quoted(str(item))}" for item in items]]
    rendered = _scalar(key, value)
    return [f"{key}: {rendered}" if rendered else f"{key}:"]


def _render_unknown(key: str, value: object) -> list[str]:
    """Unknown keys keep their value verbatim; shape is arbitrary, so let YAML emit it (FM-10)."""
    if isinstance(value, str | bool | int | float | date) or value is None:
        return _render_pair(key, value, flow=False)
    yaml = YAML()
    yaml.width = 1 << 20
    yaml.indent(mapping=2, sequence=4, offset=2)
    buffer = StringIO()
    yaml.dump({key: value}, buffer)
    return _to_lines(buffer.getvalue())


def serialize(meta: Metadata, body: str, *, extra: Mapping[str, Any] | None = None) -> str:
    """Render metadata and body into a canonical document (FM-7, FM-8).

    Key order is :data:`CANONICAL_ORDER` followed by ``extra`` (the unknown keys, FM-10). A field
    is emitted when it holds a value, or when it was literally present in the source block — so an
    explicit ``related_topics: []`` survives a round trip while an absent one stays absent.

    This is the *canonical* writer, used where the caller owns every byte. To touch one field of a
    human's file, use :func:`set_field`, which preserves their formatting (FM-11).
    """
    lines: list[str] = [DELIMITER]
    for key in CANONICAL_ORDER:
        value = getattr(meta, key)
        if key in _LIST_FIELDS:
            if not value and not meta.has(key):
                continue
            lines.extend(_render_pair(key, value, flow=key in _FLOW_FIELDS))
            continue
        if value is None:
            continue
        lines.extend(_render_pair(key, value, flow=False))

    for key, value in (extra or {}).items():
        if key in KNOWN_FIELDS:
            continue
        lines.extend(_render_unknown(key, value))

    lines.append(DELIMITER)
    return _from_lines(lines) + body


# --------------------------------------------------------------------------------------
# Surgical edits
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Block:
    """One top-level key and the line range it owns, ``[start, stop)``."""

    key: str
    start: int
    stop: int


def _blocks(lines: Sequence[str], mapping: Mapping[Any, Any]) -> list[_Block] | None:
    """Split the frontmatter into per-key line ranges, or refuse (FM-11).

    The boundaries come from the parser that already read the block: ruamel records the line and
    the column of every top-level key. Deriving them from the text instead means guessing where a
    key starts, and a guess misplaces the cut for every value that spans lines — a flow collection
    or a double-quoted scalar continuing at column zero is indistinguishable from the next key, so
    the slice either strands half a value or, worse, silently truncates one and mints a phantom key
    in its place.

    A key owns its own line plus every continuation line under it. Blank lines and column-zero
    comments before the next key belong to *that* key, by the usual YAML reading — so removing a
    field never eats the comment introducing its neighbour.

    ``None`` means no line belongs to exactly one key, so no edit can touch "the target key's lines
    and nothing else": keys sharing a line (a top-level flow mapping), an indented or explicit key,
    a key merged in from an anchor (it owns no line at all), or content ahead of the first key.
    Rewriting any of those by line would drop a byte the human wrote, and arch D6 leaves no undo.
    """
    # ``lc.data`` maps each key to [key line, key column, value line, value column], 0-based within
    # the block. A plain mapping (the empty block) has no ``lc`` and no keys.
    marks: Mapping[Any, Sequence[int]] = getattr(getattr(mapping, "lc", None), "data", None) or {}
    starts: list[tuple[int, str]] = []
    for key in mapping:
        mark = marks.get(key)
        if mark is None:
            return None
        line, column = mark[0], mark[1]
        if column != 0 or not 0 <= line < len(lines):
            return None
        if starts and line <= starts[-1][0]:
            return None
        # ``str(key)`` matches what :func:`parse` puts in ``raw``: a non-string key is never a field
        # name Layer 1 writes, so it only ever has to bound its neighbours.
        starts.append((line, str(key)))

    preamble = lines[: starts[0][0]] if starts else lines
    if any(line.strip() and not line.lstrip().startswith("#") for line in preamble):
        return None

    out: list[_Block] = []
    for position, (start, key) in enumerate(starts):
        stop = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        while stop > start + 1 and (not lines[stop - 1].strip() or lines[stop - 1].startswith("#")):
            stop -= 1
        out.append(_Block(key, start, stop))
    return out


@dataclass(frozen=True, slots=True)
class _Editable:
    """A block Layer 1 both reads and can rewrite one whole line at a time (FM-11)."""

    split: _Split
    lines: list[str]
    blocks: list[_Block]


def _editable(text: str) -> _Editable | None:
    """The block a surgical write may touch, or ``None`` — meaning leave the file alone.

    Four refusals, one reason: without version control (arch D6) a rewrite Layer 1 cannot justify
    byte-for-byte is unrecoverable, and §5 forbids overwriting human content at any layer. There is
    no block (FM-1); the block is unterminated; the block is one :func:`parse` reports on rather
    than reads — a half-written note, a body opening with a thematic break, a sequence; or the
    block reads but does not slice into lines (see :func:`_blocks`).

    Editing an unreadable block is not a harmless guess: with no key line to find, the write lands
    *above* the human's text, and once a key is found its guessed range runs to the end of the
    block, so the following write replaces that text with the rendered pair. Refusing keeps the
    file byte-identical; the walk still reports it (``FRONTMATTER_PARSE_ERROR``, VA-39), so nothing
    is hidden — Layer 1 flags, it never repairs (MA-9).
    """
    split = _split_document(text)
    if split is None or not split.terminated:
        return None
    mapping = _load_block(split.yaml_text).mapping
    if mapping is None:
        return None
    lines = _to_lines(split.yaml_text)
    blocks = _blocks(lines, mapping)
    if blocks is None:
        return None
    return _Editable(split, lines, blocks)


def _comment_start(line: str, offset: int) -> int:
    """Index of the ``#`` that opens an inline comment, or ``-1`` (FM-9)."""
    in_single = in_double = False
    index = offset
    while index < len(line):
        char = line[index]
        if in_double:
            if char == "\\":
                index += 2
                continue
            if char == '"':
                in_double = False
        elif in_single:
            if char == "'":
                in_single = False
        elif char == '"':
            in_double = True
        elif char == "'":
            in_single = True
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return index
        index += 1
    return -1


def _inline_comment(line: str) -> str:
    """The trailing ``  # …`` of a key line, whitespace included, or ``""``."""
    colon = line.find(":")
    if colon < 0:
        return ""
    start = _comment_start(line, colon + 1)
    if start < 0:
        return ""
    return line[len(line[:start].rstrip()) :]


def _detect_seq_indent(lines: Sequence[str]) -> str:
    """The block-sequence indent this file already uses, so an edit does not reflow it (FM-11).

    Falls back to the canonical two spaces only when the block holds no sequence to copy from.
    """
    for line in lines:
        stripped = line.lstrip(" ")
        if stripped.startswith("- ") or stripped.rstrip("\r") == "-":
            return line[: len(line) - len(stripped)]
    return _SEQ_INDENT


def _detect_eol(lines: Sequence[str]) -> str:
    """``"\\r"`` when the block uses CRLF, so a rewritten line keeps the file's line endings."""
    return "\r" if any(line.endswith("\r") for line in lines) else ""


def _insert_position(key: str, blocks: Sequence[_Block], total: int) -> int:
    """Where a brand-new key goes: canonical order among the keys already present (FM-7)."""
    if key not in CANONICAL_ORDER:
        return total
    rank = CANONICAL_ORDER.index(key)
    position = 0
    for block in blocks:
        if block.key in CANONICAL_ORDER and CANONICAL_ORDER.index(block.key) < rank:
            position = block.stop
    return position


def set_field(text: str, key: str, value: object) -> str:
    """Write one frontmatter field, leaving every other byte alone (FM-11).

    Only the target key's lines change: its inline comment, the surrounding keys' order, quoting
    and flow/block style, and the entire markdown body are preserved exactly. A missing key is
    inserted in canonical position (FM-7). A document whose block Layer 1 cannot read, or cannot
    slice into whole lines, is returned unchanged (:func:`_editable`) — the maintenance flush must
    stay total over a hand-edited tree (FM-13), and inventing or guessing at a block would be a
    rewrite no one asked for.
    """
    editable = _editable(text)
    if editable is None:
        return text

    lines = editable.lines
    existing = next((block for block in editable.blocks if block.key == key), None)
    eol = _detect_eol(lines)

    if existing is not None:
        original = [line.rstrip("\r") for line in lines[existing.start : existing.stop]]
        flow = len(original) == 1 and "[" in original[0]
        rendered = _render_pair(key, value, flow=flow, indent=_detect_seq_indent(original))
        rendered[0] += _inline_comment(original[0])
        updated = [*lines[: existing.start], *_with_eol(rendered, eol), *lines[existing.stop :]]
    else:
        rendered = _render_pair(
            key, value, flow=key in _FLOW_FIELDS, indent=_detect_seq_indent(lines)
        )
        at = _insert_position(key, editable.blocks, len(lines))
        updated = [*lines[:at], *_with_eol(rendered, eol), *lines[at:]]

    return text[: editable.split.start] + _from_lines(updated) + text[editable.split.end :]


def _with_eol(rendered: Sequence[str], eol: str) -> list[str]:
    return [line + eol for line in rendered] if eol else list(rendered)


def remove_field(text: str, key: str) -> str:
    """Delete a frontmatter key outright — never blank it (FM-11).

    Every line the key owns goes, including a multi-line value's continuations, and nothing else
    does. A key that is not present, or a document whose block Layer 1 cannot read or cannot slice
    into whole lines (:func:`_editable`), is returned unchanged.
    """
    editable = _editable(text)
    if editable is None:
        return text

    lines = editable.lines
    block = next((candidate for candidate in editable.blocks if candidate.key == key), None)
    if block is None:
        return text

    updated = [*lines[: block.start], *lines[block.stop :]]
    return text[: editable.split.start] + _from_lines(updated) + text[editable.split.end :]


# --------------------------------------------------------------------------------------
# related_topics
# --------------------------------------------------------------------------------------


def normalize_related_topic(value: str) -> str:
    """Qualify a ``related_topics`` entry with its namespace (FM-15).

    The spec writes this value three ways — ``bbq``, ``bbq.equipment``, ``topic.bbq.equipment`` —
    so one normalizer keeps the validator and the registry renderer agreeing (contradiction C5).
    Idempotent: an already-namespaced value passes through untouched.
    """
    stripped = value.strip()
    if not stripped:
        return ""
    if stripped.split(".", 1)[0] in _TAG_NAMESPACES:
        return stripped
    return f"topic.{stripped}"
