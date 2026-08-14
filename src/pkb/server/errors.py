"""Typed error → (status, code), in exactly one place (RO-20, RO-21, MC-14).

Three distinct conditions share **409** and a client's reaction to each differs — retry later,
render the approval, refetch the interrupt — so prose cannot carry the distinction. Every error body
carries a stable machine ``code`` and clients branch on that, never on the message.

Two rules with teeth:

* **A route never builds an ``HTTPException`` for one of these by hand.** One handler, one table. Two
  places that map the same exception are two places that drift.
* **An unmapped ``PkbAgentError`` subclass is a 500 by construction**, never a 200. A new typed error
  added to the seam without a row here should be loud, and the failure mode a default-to-200 map
  produces — an error reaching a client dressed as a success — is unrecoverable at the client.

``detail`` is the exception's own message **verbatim**. Layer 2's messages already name the thread
and say what to do; re-wording them here would be a second answer to "what went wrong", which is the
discipline MW-13 applies to Layer 1's findings, one layer up.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Final

from pkb.contracts import (
    ERROR_CODES,
    INTERNAL_CODE,
    RETRYABLE_CODES,
    PkbAgentError,
    code_for,
)
from pkb.packs import UnknownTopicError
from pkb.service.session_file import SessionFileInvalidError
from pkb.service.sessions import (
    IllegalSessionTransitionError,
    SessionNameTakenError,
    UnknownSessionError,
)

__all__ = [
    "ERROR_CODES",
    "ERROR_STATUS",
    "ILLEGAL_SESSION_TRANSITION_CODE",
    "INTERNAL_CODE",
    "INVALID_SESSION_FILE_CODE",
    "PROBLEM_CONTENT_TYPE",
    "SESSION_NAME_TAKEN_CODE",
    "UNKNOWN_SESSION_CODE",
    "problem_body",
    "status_and_code",
]

PROBLEM_CONTENT_TYPE: Final = "application/problem+json"
"""RFC 9457. A shape a client can parse without knowing this project."""

UNKNOWN_SESSION_CODE: Final = "unknown_session"
SESSION_NAME_TAKEN_CODE: Final = "session_name_taken"
ILLEGAL_SESSION_TRANSITION_CODE: Final = "illegal_session_transition"
INVALID_SESSION_FILE_CODE: Final = "invalid_session_file"
"""The session layer's own wire codes (S-9, S-16, S-20, S-22, S-24/P3).

Mapped here rather than in :data:`pkb.contracts.ERROR_CODES`, the same way
:data:`UnknownTopicError_CODE` already is: :mod:`pkb.service.sessions` and
:mod:`pkb.service.session_file` sit *above* ``pkb.contracts`` in the layer order (I1/I2 —
``pkb.service`` depends on ``pkb.contracts``, never the reverse), so their typed errors cannot be
named from inside the seam's own table without inverting that dependency. Every unmapped
:class:`~pkb.contracts.PkbAgentError` subclass is already a 500 by construction (:func:`code_for`'s
own fallback), so these four rows are what keeps that from applying to the session errors instead
of their real status.
"""

ERROR_STATUS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "unknown_agent": 404,
        "unknown_thread": 404,
        "unknown_topic": 404,
        "thread_busy": 409,
        "approval_pending": 409,
        "stale_interrupt": 409,
        "invalid_decision": 400,
        "validation_error": 400,
        UNKNOWN_SESSION_CODE: 404,
        SESSION_NAME_TAKEN_CODE: 409,
        ILLEGAL_SESSION_TRANSITION_CODE: 409,
        INVALID_SESSION_FILE_CODE: 400,
        INTERNAL_CODE: 500,
    }
)
"""Machine code → HTTP status. The **transport's** half of the table.

The code half lives in :data:`pkb.contracts.ERROR_CODES`, because four things have to agree on it
and two of them — the TUI and the Telegram adapter — may not import this module (I2, decision P). A
status code is a transport concern and a Telegram bot has no use for one, so it stays here.
"""

UnknownTopicError_CODE: Final = "unknown_topic"
"""``pkb.packs``' own refusal, mapped here because it is below the seam and knows nothing of wires."""

_TITLES: Final = MappingProxyType(
    {
        "unknown_agent": "Unknown agent",
        "unknown_thread": "Unknown thread",
        "unknown_topic": "Unknown topic",
        "thread_busy": "Thread busy",
        "approval_pending": "Approval pending",
        "stale_interrupt": "Stale interrupt",
        "invalid_decision": "Invalid decision",
        "validation_error": "Invalid request",
        UNKNOWN_SESSION_CODE: "Unknown session",
        SESSION_NAME_TAKEN_CODE: "Session name taken",
        ILLEGAL_SESSION_TRANSITION_CODE: "Illegal session transition",
        INVALID_SESSION_FILE_CODE: "Invalid session file",
        INTERNAL_CODE: "Internal error",
    }
)

_RETRYABLE = RETRYABLE_CODES
"""Re-exported from the seam so the HTTP body and a client agree (decision P)."""

_BELOW_SEAM_CODES: Final[tuple[tuple[type[BaseException], str], ...]] = (
    (UnknownTopicError, UnknownTopicError_CODE),
    (UnknownSessionError, UNKNOWN_SESSION_CODE),
    (SessionNameTakenError, SESSION_NAME_TAKEN_CODE),
    (IllegalSessionTransitionError, ILLEGAL_SESSION_TRANSITION_CODE),
    (SessionFileInvalidError, INVALID_SESSION_FILE_CODE),
)
"""Typed errors from below the seam, checked by ``isinstance`` before :func:`code_for`'s own table.

Each of these subclasses :class:`~pkb.contracts.PkbAgentError` directly (siblings, not a chain), so
order among them is never load-bearing — only that every one of them is checked *before* falling
through to the seam's class-identity table, which knows nothing of a module it sits below.
"""


def status_and_code(exc: BaseException) -> tuple[int, str]:
    """``(status, code)`` for one exception.

    The code comes from the seam's table (so every channel answers identically) and the status from
    this module's. Anything unmapped is ``(500, "internal")``, including an unmapped
    ``PkbAgentError`` subclass — a new typed error is a 500 until somebody gives it a row, rather
    than silently becoming whatever the last ``except`` happened to do.
    """
    code = next(
        (mapped for klass, mapped in _BELOW_SEAM_CODES if isinstance(exc, klass)), code_for(exc)
    )
    return (ERROR_STATUS.get(code, 500), code)


def problem_body(exc: BaseException, **extra: Any) -> dict[str, Any]:
    """An RFC 9457 body. ``detail`` is the exception's own message, verbatim (RO-21).

    An **internal** error is the one exception: its detail is a fixed string, because the message of
    an unexpected exception routinely carries a module path, a file path or a fragment of a query,
    and none of that belongs on a wire a Telegram bot also reads.

    A ``findings`` sequence, when the exception carries one (``SessionFileInvalidError.findings``
    today), rides along as a structured extra — duck-typed rather than an ``isinstance`` check, so a
    future finding-carrying error inherits this for free. ``detail`` already carries the same
    findings *rendered* (``SessionFileInvalidError.__init__`` builds its own message with
    ``render_findings``), so this is the machine-readable twin of text a human already gets, never a
    second, divergent rendering.
    """
    status, code = status_and_code(exc)
    detail = (
        "an unexpected error occurred; see the daemon log" if code == INTERNAL_CODE else str(exc)
    )
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": _TITLES.get(code, "Error"),
        "status": status,
        "code": code,
        "detail": detail,
    }
    if code in _RETRYABLE:
        body["retryable"] = True
    findings = getattr(exc, "findings", None)
    if findings:
        body["findings"] = [dataclasses.asdict(finding) for finding in findings]
    body.update({key: value for key, value in extra.items() if value is not None})
    return body


def is_agent_error(exc: BaseException) -> bool:
    """Whether this is one of the seam's typed errors — used to decide 500 vs mapped."""
    return isinstance(exc, PkbAgentError)
