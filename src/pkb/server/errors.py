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

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Final

from pkb.contracts import (
    ApprovalPendingError,
    InvalidDecisionError,
    PkbAgentError,
    StaleInterruptError,
    ThreadBusyError,
    UnknownAgentError,
    UnknownThreadError,
)
from pkb.packs import UnknownTopicError

__all__ = [
    "ERROR_CODES",
    "INTERNAL_CODE",
    "PROBLEM_CONTENT_TYPE",
    "problem_body",
    "status_and_code",
]

PROBLEM_CONTENT_TYPE: Final = "application/problem+json"
"""RFC 9457. A shape a client can parse without knowing this project."""

INTERNAL_CODE: Final = "internal"

ERROR_CODES: Final[Mapping[type[BaseException], tuple[int, str]]] = MappingProxyType(
    {
        UnknownAgentError: (404, "unknown_agent"),
        UnknownThreadError: (404, "unknown_thread"),
        UnknownTopicError: (404, "unknown_topic"),
        ThreadBusyError: (409, "thread_busy"),
        ApprovalPendingError: (409, "approval_pending"),
        StaleInterruptError: (409, "stale_interrupt"),
        InvalidDecisionError: (400, "invalid_decision"),
        ValueError: (400, "validation_error"),
    }
)
"""The one table, shared by the HTTP handler and the MCP adapter so the two cannot drift (MC-14).

Order matters on lookup, not here: :func:`status_and_code` walks the exception's MRO, so a subclass
of a mapped error inherits its mapping and ``ValueError``'s row never shadows ``InvalidDecisionError``.
"""

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
        INTERNAL_CODE: "Internal error",
    }
)

_RETRYABLE: Final = frozenset({"thread_busy"})
"""Codes where retrying *the same call later* can succeed.

``approval_pending`` is deliberately absent: retrying does not help, because the thread stays parked
until a human decides. MC-14 states both as non-retryable-on-this-thread so a program does not spin.
"""


def status_and_code(exc: BaseException) -> tuple[int, str]:
    """``(status, code)`` for one exception — MRO order, so subclasses inherit their mapping.

    Anything unmapped is ``(500, "internal")``, including an unmapped ``PkbAgentError`` subclass.
    That is the point: a new typed error is a 500 until somebody gives it a row, rather than
    silently becoming whatever the last ``except`` happened to do.
    """
    for klass in type(exc).__mro__:
        row = ERROR_CODES.get(klass)
        if row is not None:
            return row
    return (500, INTERNAL_CODE)


def problem_body(exc: BaseException, **extra: Any) -> dict[str, Any]:
    """An RFC 9457 body. ``detail`` is the exception's own message, verbatim (RO-21).

    An **internal** error is the one exception: its detail is a fixed string, because the message of
    an unexpected exception routinely carries a module path, a file path or a fragment of a query,
    and none of that belongs on a wire a Telegram bot also reads.
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
    body.update({key: value for key, value in extra.items() if value is not None})
    return body


def is_agent_error(exc: BaseException) -> bool:
    """Whether this is one of the seam's typed errors — used to decide 500 vs mapped."""
    return isinstance(exc, PkbAgentError)
