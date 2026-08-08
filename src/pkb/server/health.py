"""``GET /health`` — 200 while the process is serving, always (AP-18, AP-19).

Degradation is reported in the **body**, never in the status code. D9's whole point is that a
crashed Telegram bot must not take the daemon down; a 503 invites exactly the restart D9 forbids and
would kill in-flight runs and pending approvals that are perfectly healthy. A supervisor that
restarts on a non-200 would therefore turn one flapping subsystem into lost work.

**Cheap and side-effect-free** (AP-19): no tree walk, no ``regenerate``, no graph compile, no
checkpointer read, no model call. ``agent_count`` comes from the cached catalog and the thread and
proposal counts are one indexed ``COUNT(*)`` each. A health endpoint that walks the tree times out
exactly when the system is under load — which is when something is asking.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Literal

__all__ = ["HealthState", "SubsystemState", "redact"]

_SECRETS: Final = (
    # A Telegram bot token lives in the URL *path*, so it reaches any exception message that names
    # the URL — which `raise_for_status()` does.
    re.compile(r"(?i)\b(bot)(\d{6,})(:[A-Za-z0-9_-]{20,})"),
    # Credentials in a netloc, and the usual query-string names.
    re.compile(r"(?i)(://)([^/\s:@]+):([^/\s@]+)(@)"),
    # `Authorization: Bearer <token>` — the scheme word is not the secret, the rest of it is.
    re.compile(r"(?i)\b(authorization)(:\s*)((?:bearer|basic|token)\s+)?\S+"),
    re.compile(r"(?i)\b(token|api[_-]?key|secret|password)(=|:\s*)([^\s&\"']+)"),
)

_MASK: Final = "[redacted]"


def redact(text: str) -> str:
    """Strip anything credential-shaped from a string bound for ``/health`` or the log.

    ``/health`` is **unauthenticated by design** (AP-20: the daemon binds localhost and defers auth),
    and it publishes ``last_error`` verbatim. A subsystem whose credential lives in a URL therefore
    leaks it on its first failure: measured with a real bot token, ``raise_for_status()`` on a 401
    produces ``Client error '401 Unauthorized' for url 'https://api.telegram.org/bot<TOKEN>/…'``,
    ``SubsystemState.failed`` stores that verbatim, and ``/health`` serves it to anything on the
    machine.

    Redacting here rather than at each call site is the point: the leak is a property of *storing an
    arbitrary exception message on a public surface*, so the fix belongs where the storing happens.
    A future subsystem with a credentialed URL inherits it without knowing this rule exists.
    """
    for pattern in _SECRETS:
        text = pattern.sub(lambda m: _mask(m), text)
    return text


def _mask(match: re.Match[str]) -> str:
    groups = match.groups()
    if len(groups) == 4:  # scheme://user:pass@
        return f"{groups[0]}{groups[1]}:{_MASK}{groups[3]}"
    if groups[0].lower() == "authorization":
        return f"{groups[0]}{groups[1]}{groups[2] or ''}{_MASK}"
    return f"{groups[0]}{groups[1]}{_MASK}"


Status = Literal["ok", "degraded"]


@dataclass
class SubsystemState:
    """One background task's supervision state, as ``/health`` reports it (AP-17).

    ``restarts`` and ``last_error`` are the part arch §8 asks to be visible: a bot that has restarted
    forty times is healthy by any single-sample check and broken by any human's judgement.
    """

    name: str
    state: str = "disabled"
    restarts: int = 0
    last_error: str | None = None
    last_error_at: str | None = None
    started_at: str | None = None
    pending: int = 0
    last_run_at: str | None = None

    @property
    def enabled(self) -> bool:
        return self.state != "disabled"

    @property
    def healthy(self) -> bool:
        """Anything other than ``running`` on an enabled subsystem counts as degraded."""
        return not self.enabled or self.state == "running"

    def failed(self, exc: BaseException) -> None:
        """Record a crash — with the message **redacted** before it can reach ``/health``.

        See :func:`redact`. An exception message is arbitrary text from an arbitrary library, and
        this field is served unauthenticated.
        """
        self.restarts += 1
        self.last_error = redact(f"{type(exc).__name__}: {exc}")
        self.last_error_at = _now()
        self.state = "restarting"

    def running(self) -> None:
        self.state = "running"
        self.started_at = self.started_at or _now()

    def payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "restarts": self.restarts,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
            "started_at": self.started_at,
        }


@dataclass
class HealthState:
    """Everything ``/health`` reports, mutated in place by the daemon's lifespan and workers."""

    version: str = "0.1.0"
    kb_root: str = ""
    db_path: str = ""
    durability: str = ""
    fanout_limit: int = 0
    started_at: float = field(default_factory=time.monotonic)
    runtime_open: bool = False
    mcp_mounted: bool = False
    flush_last_at: str | None = None
    flush_findings: int = 0
    scan_worker: SubsystemState = field(default_factory=lambda: SubsystemState(name="scan_worker"))
    telegram: SubsystemState = field(default_factory=lambda: SubsystemState(name="telegram"))

    @property
    def status(self) -> Status:
        """``degraded`` when an enabled subsystem is not running, or the runtime is closed."""
        healthy = self.runtime_open and self.scan_worker.healthy and self.telegram.healthy
        return "ok" if healthy else "degraded"

    def record_flush(self, findings: int) -> None:
        """What the ``flush_sink`` feeds (AP-16).

        ``None`` for a sink drops broken links, orphans and ``DERIVED_WRITE_FAILED`` on the floor —
        a convenience in a unit test and a defect in a daemon.
        """
        self.flush_last_at = _now()
        self.flush_findings = findings

    def payload(
        self,
        *,
        agent_count: int,
        active_runs: int,
        subscribers: int,
        threads: tuple[int, int],
        proposals_pending: int,
        mcp_sessions: int = 0,
    ) -> dict[str, Any]:
        total, pending = threads
        return {
            "status": self.status,
            "version": self.version,
            "uptime_s": int(time.monotonic() - self.started_at),
            "kb_root": self.kb_root,
            "agent_count": agent_count,
            "active_runs": active_runs,
            "subscribers": subscribers,
            "runtime": {
                "open": self.runtime_open,
                "db_path": self.db_path,
                "durability": self.durability,
                "fanout_limit": self.fanout_limit,
            },
            "threads": {"total": total, "pending_approvals": pending},
            "proposals": {"pending": proposals_pending},
            "scan_worker": {
                "state": self.scan_worker.state,
                "pending": self.scan_worker.pending,
                "last_run_at": self.scan_worker.last_run_at,
                "last_error": self.scan_worker.last_error,
            },
            "flush": {"last_report_at": self.flush_last_at, "findings": self.flush_findings},
            "telegram": {"enabled": self.telegram.enabled, **self.telegram.payload()},
            "mcp": {"mounted": self.mcp_mounted, "sessions": mcp_sessions},
        }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
