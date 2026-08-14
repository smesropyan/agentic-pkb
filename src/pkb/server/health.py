"""``GET /health`` — 200 while the process is serving, always (AP-18, AP-19).

Degradation is reported in the **body**, never in the status code. D9's whole point is that a
crashed Telegram bot must not take the daemon down; a 503 invites exactly the restart D9 forbids and
would kill in-flight runs that are perfectly healthy. A supervisor that restarts on a non-200 would
therefore turn one flapping subsystem into lost work.

**Cheap and side-effect-free** (AP-19): no tree walk, no ``regenerate``, no graph compile, no
checkpointer read, no model call. ``agent_count`` comes from the cached catalog and the thread count
is one indexed ``COUNT(*)``. A health endpoint that walks the tree times out exactly when the system
is under load — which is when something is asking.

No ``proposals`` key (Task 6): ``pkb.service.proposals`` and the ``propose_only`` auto-rejection it
recorded are both gone, so there is nothing left to count.
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
    """Whether the supervised **task** is alive — never whether the subsystem can reach the
    network (TG-12). ``_supervise`` calls :meth:`running` before it awaits the task body, so a bot
    whose token was revoked reports ``running`` for the whole of its next poll while every request
    is answered ``401`` — and it is stamped ``running`` again at the top of every restart, so a
    sample taken at the wrong moment says ``running`` no matter how wrong the token is.
    :attr:`last_poll_ok_at` is the only thing that says otherwise, so nothing may infer
    reachability from this field."""

    restarts: int = 0
    last_error: str | None = None
    last_error_at: str | None = None
    started_at: str | None = None
    pending: int = 0
    last_run_at: str | None = None

    chats: int = 0
    """How many chats the deployment mapping names (TG-11). Set by the composition root from the
    config it loads; the bot never mutates the mapping (TG-17), so this never changes at runtime.

    Still **chats**, not channels, and the distinction is the point since topics: one chat holds one
    General plus every topic the human asked for, so this counts the human-configured file's lines
    and :attr:`channels` counts what the bot was asked to create beside them."""

    agents: frozenset[str] = frozenset()
    """Every agent id reachable from Telegram — the mapping's values **union the channel directory's**
    (TG-11, amended 2026-08-09 by TG-72/TG-77).

    A set rather than a count, because two channels may legitimately address one agent: the answer to
    "which topics are unreachable from my phone" is a set difference against the catalog, and a
    length comparison would call a healthy two-chat deployment complete while a topic sits
    unreachable. It is deliberately **not** in :meth:`payload` — ``/health`` publishes the
    difference (``unmapped_agents``), which is the part a human can act on.

    Since topics, the mapping is only half the answer: ``config.chats[chat_id]`` names the agent of
    each chat's General (TG-73) and every *other* channel is a row in ``pkb_telegram_channels``,
    which the human created with ``/channels`` and the daemon knows only by reading the store. So the
    composition root seeds this from ``store.channel_agents()`` at wiring time and the adapter adds
    to it on every create. Seeding in the daemon rather than in the bot is the whole of TG-11's
    stated property: the answer stays correct while the bot is crash-looping, which is exactly when
    somebody reads ``/health``."""

    last_poll_ok_at: str | None = None
    """When ``getUpdates`` last returned (TG-12). Written by the adapter, never by the supervisor.
    This — not :attr:`state` — is the field that reports connectivity."""

    last_send_error: str | None = None
    """The last outbound send failure, redacted (TG-13). Reported and nothing more: it never makes
    ``/health`` non-200 and never turns :attr:`~HealthState.status` ``degraded``."""

    invalid_chats: tuple[int, ...] = ()
    """Mapped chats naming an agent the catalog does not have (TG-18). Written by the adapter's
    startup validation, published by :meth:`payload`.

    Reported rather than fatal, because a topic can be renamed under a running config and exiting
    would take every other chat, every parked approval and the TUI down with it. But *reported* has
    to mean somewhere a human looks: without this field the only trace was one ERROR line at
    startup, which is exactly the thing that has scrolled away by the time somebody reads
    ``/health`` — a deployment with a typo served a telegram block indistinguishable from a healthy
    one, and the bad entry subtracted nothing from ``unmapped_agents`` either. Like
    :attr:`last_send_error` it never changes :attr:`~HealthState.status`: the subsystem is running,
    one line of configuration is wrong."""

    topics_enabled: bool = False
    """Whether this bot may use topics in a private chat — ``getMe.has_topics_enabled`` (TG-75).

    **``False`` is the correct default and the correct answer for most deployments**: Threaded Mode
    is a per-bot BotFather toggle that is off until a human flips it, and with it off the adapter
    behaves byte-identically to the pre-topics build — no ``message_thread_id`` on any send, no
    ``createForumTopic``, no directory writes. Probed once at startup and re-probed only on a
    restart, so it is a fact about the deployment rather than a per-send question.

    Published because it is the first thing to look at when ``/channels`` answers "turn on Threaded
    Mode" and the human is certain they already did: the toggle and the daemon's belief about it are
    two different things, and only one of them is visible in BotFather."""

    channels: int = 0
    """How many channels the directory holds — ``pkb_telegram_channels`` (TG-77).

    A count, not a set, because the *identities* already reach ``/health`` through :attr:`agents`
    and the difference (``unmapped_agents``) is what a human acts on. What the count adds is the
    other direction: a directory of zero with topics enabled says nobody has run ``/channels`` yet,
    which is a different problem from a directory of twelve where one agent is missing."""

    retired_channels: tuple[str, ...] = ()
    """Agent ids whose channel no longer routes, and why it is reported rather than repaired.

    Two things retire a channel and both are reported here, because to a human they are one
    question — *"why is Grilling not answering in its own topic?"*:

    * the human deleted the topic and it was recreated ``MAX_RECREATIONS`` times already (TG-82), so
      the agent's traffic falls back to General with an agent-id first line (TG-85);
    * the agent left the catalog, so its channel is retired and the Telegram topic is left standing
      (TG-79) — nothing is deleted, and re-adding the agent revives the binding.

    Reported and nothing more, exactly like :attr:`invalid_chats` and for the same reason (TG-13):
    the subsystem is running and delivering, one channel is taking a longer road. Without the field
    the only trace of either case is a single line in the chat that scrolls away, and the human's
    conclusion is that the bot lost their expert."""

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

    def poll_ok(self) -> None:
        """Stamp a successful poll (TG-12).

        The supervisor knows only that the task has not raised; the adapter is the only thing that
        knows Telegram answered. Without this stamp a human debugging a wrong token reads
        ``state: running, restarts: 0, status: ok`` and learns nothing at all.
        """
        self.last_poll_ok_at = _now()

    def send_failed(self, exc: BaseException) -> None:
        """Record an outbound send failure — and **only** that (TG-13).

        Deliberately touches neither ``state`` nor ``restarts``: ``degraded`` keeps its narrow
        meaning of "an enabled subsystem is not running". A 503 or a widened ``degraded`` on a
        failed ``sendMessage`` invites the supervisor restart D9 forbids, killing in-flight runs that
        are perfectly healthy — and a signal that fires for every dropped message is one somebody
        mutes.

        The message goes through :func:`redact` for the same reason :meth:`failed` does: it is
        arbitrary text from an arbitrary library, and this field is served unauthenticated with the
        bot token sitting in the request URL's path.
        """
        self.last_send_error = redact(f"{type(exc).__name__}: {exc}")

    def payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "restarts": self.restarts,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
            "started_at": self.started_at,
            "chats": self.chats,
            "last_poll_ok_at": self.last_poll_ok_at,
            "last_send_error": self.last_send_error,
            "invalid_chats": self.invalid_chats,
            "topics_enabled": self.topics_enabled,
            "channels": self.channels,
            "retired_channels": self.retired_channels,
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
        mcp_sessions: int = 0,
        unmapped_agents: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """``unmapped_agents`` is computed by the caller (TG-11, C-30).

        The ``/health`` endpoint already holds the cached agent catalog, so the set difference
        against :attr:`SubsystemState.agents` happens there rather than in the bot. Computing it in
        the bot would make the answer disappear exactly when the bot is crash-looping, which is the
        moment a human is reading ``/health``.

        **What it means since topics** (TG-3 amended, TG-72): agents with no **channel** — no
        mapped chat's General *and* no row in the channel directory — rather than agents with no
        chat. The endpoint's arithmetic is unchanged; what changed is :attr:`SubsystemState.agents`,
        which the daemon now seeds from the store. So an empty list means every topic in the
        catalog is reachable from the phone, and a listed agent is one the human would have to run
        ``/channels <agent-id>`` for — which is the action the field exists to prompt.
        """
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
            "scan_worker": {
                "state": self.scan_worker.state,
                "pending": self.scan_worker.pending,
                "last_run_at": self.scan_worker.last_run_at,
                "last_error": self.scan_worker.last_error,
            },
            "flush": {"last_report_at": self.flush_last_at, "findings": self.flush_findings},
            "telegram": {
                "enabled": self.telegram.enabled,
                **self.telegram.payload(),
                "unmapped_agents": unmapped_agents,
            },
            "mcp": {"mounted": self.mcp_mounted, "sessions": mcp_sessions},
        }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
