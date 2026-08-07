"""Structured problem reporting for Layer 1.

Every mechanical check in ``pkb.core`` reports through :class:`Finding` rather than raising, so one
pass over a file yields the complete correction list an agent needs (rule CX-5). Exceptions are
reserved for programming errors and for operations that cannot proceed at all.

Rule ids (``FM-1``, ``VA-9``, ...) refer to
``docs/superpowers/specs/2026-08-06-pkb-core-layer1-rules.md``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class PkbError(Exception):
    """Base class for unrecoverable Layer 1 failures."""


class KbNotFoundError(PkbError):
    """The knowledge base root does not exist or is not a directory."""


class NotATopicRootError(PkbError):
    """A topic-scoped operation was pointed at a directory holding no ``topic.md`` (PA-2, PA-3)."""


class ScaffoldError(PkbError):
    """A topic could not be scaffolded."""


class TopicDepthExceededError(ScaffoldError):
    """The requested topic would need more than four ``topic.*`` tag levels (SC-9)."""


class Severity(StrEnum):
    """How a caller should react to a :class:`Finding`.

    ``ERROR`` blocks the write that produced it; ``WARNING`` and ``INFO`` are reported but never
    block — Layer 2 turns errors into a rejected tool call and surfaces the rest.
    """

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class Finding:
    """One rule violation.

    ``message`` is consumed verbatim by Layer 2's error ``ToolMessage``, so it must say what is
    wrong without assuming the reader has this codebase in context (CX-6).
    """

    code: str
    """Stable machine code, UPPER_SNAKE, e.g. ``MISSING_REQUIRED_FIELD``."""

    severity: Severity
    message: str
    rule_id: str
    """Rule id from the Layer 1 rules document, e.g. ``VA-4``."""

    path: str | None = None
    """KB-relative POSIX path of the offending file, if the finding is file-scoped."""

    field: str | None = None
    """Frontmatter field the finding concerns, if any."""

    value: str | None = None
    """The offending value, rendered short."""

    line: int | None = None
    """1-based line number within the file, when known."""

    hint: str | None = None
    """The concrete fix, when one can be named."""

    def render(self) -> str:
        """One line, readable by a human or an agent with no other context."""
        where = f"{self.path}: " if self.path else ""
        line = f"L{self.line} " if self.line is not None else ""
        field = f" ({self.field})" if self.field else ""
        hint = f" — {self.hint}" if self.hint else ""
        return f"{where}{line}[{self.code}/{self.rule_id}]{field} {self.message}{hint}"


_SEVERITY_ORDER = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


def errors_only(findings: Iterable[Finding]) -> list[Finding]:
    """The blocking subset."""
    return [f for f in findings if f.severity is Severity.ERROR]


def has_errors(findings: Iterable[Finding]) -> bool:
    """True when at least one finding blocks the write."""
    return any(f.severity is Severity.ERROR for f in findings)


def sort_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Deterministic order: errors first, then by path, line, and code."""
    return sorted(
        findings,
        key=lambda f: (
            _SEVERITY_ORDER[f.severity],
            f.path or "",
            f.line if f.line is not None else -1,
            f.code,
            f.field or "",
        ),
    )


def render_findings(findings: Iterable[Finding]) -> str:
    """Multi-line report — this is what an agent sees when a write is rejected."""
    return "\n".join(f.render() for f in sort_findings(findings))
