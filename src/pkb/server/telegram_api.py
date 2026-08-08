"""The Bot API, over httpx — the **only** module in `pkb.server` that opens a socket (TG-67, TG-68).

A sibling module rather than a subpackage, and that is not taste: five built seam scans use a
**non-recursive** ``glob("*.py")``, so identical code inside a ``telegram/`` package is invisible to
SV-1, SV-10, SV-18, SV-22 and SV-25 — the isolation would switch off the checks for the newest
transport. Executed both ways to confirm it.

Everything here is behind :class:`BotApi` so the adapter, which holds every rule, can be driven
against a fake with no socket and no token. Seven methods is the whole surface.

Four details are measured against the real API rather than recalled, and each is a silent failure:

* **``allowed_updates`` is passed on every poll.** It **persists server-side**: whatever the last
  caller left is what the bot is subscribed to. Found live on a real bot stuck at ``['message']`` —
  every button press was dropped by Telegram before delivery, with no error, no log line and no
  pending update. Approvals were simply unanswerable from the phone.
* **The read budget must exceed the poll timeout.** Measured: a client ``read=2.0`` against
  ``getUpdates(timeout=8)`` raised ``ReadTimeout`` after 2.2 s — severing a *healthy* poll. This is
  the same defect the SSE work found one layer down, where httpx's 5 s default killed every turn.
* **A long poll must not occupy the socket an approval goes out on.** The poll holds a connection
  for its whole timeout, so sends get their own client. Otherwise a keyboard waits up to 25 s to
  reach a human who is holding their phone.
* **The limits are exact.** 4097 characters → ``400 message is too long``; 65 bytes of
  ``callback_data`` → ``400 BUTTON_DATA_INVALID``; 64 fits. All three confirmed against
  ``api.telegram.org``.

Two things leave this module, and only two: a :class:`TelegramError` (TG-15) and a result mapping.
An ``httpx`` exception never does, because the layer above imports no HTTP client on purpose
(TG-67) and therefore cannot catch one — it would reach ``_supervise`` and restart the bot for a
DNS blip. And no error this module raises can carry the URL, because the token is a **path
segment** of every URL here (TG-16, TG-24): messages are built from the method name and the
exception's *type*, never from anything that has seen a URL.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

import httpx

from pkb.server.health import redact

__all__ = [
    "ALLOWED_UPDATES",
    "CALLBACK_DATA_LIMIT",
    "LINK_PREVIEW",
    "MESSAGE_LIMIT",
    "POLL_TIMEOUT",
    "READ_BUDGET",
    "RETRY_CODES",
    "TRANSPORT_CODE",
    "BotApi",
    "HttpBotApi",
    "TelegramError",
    "shield_credentialed_http_logs",
    "with_retry",
]

_log = logging.getLogger(__name__)

MESSAGE_LIMIT: Final = 4096
"""Telegram counts **UTF-16 code units**, not characters. Confirmed: 4097 → `message is too long`."""

CALLBACK_DATA_LIMIT: Final = 64
"""Bytes. Confirmed: 65 → `BUTTON_DATA_INVALID`, 64 → OK. An interrupt id does not fit beside a
verb and an index, which is why a button carries a short token and the adapter holds the mapping."""

POLL_TIMEOUT: Final = 25
"""Seconds Telegram holds a poll open with nothing pending. Confirmed: `timeout=5` returned at 5.4s."""

READ_BUDGET: Final = POLL_TIMEOUT + 15
"""The client-side read timeout for a poll — **above** the server-side one, with room to spare."""

ALLOWED_UPDATES: Final = ("message", "edited_message", "callback_query")
"""Passed on **every** poll. See the module docstring: this setting persists on Telegram's side, and
a narrowed one silently drops button presses forever."""

LINK_PREVIEW: Final = {"is_disabled": True}
"""TG-47. Sent on every method that accepts it. Without it Telegram's servers **fetch** every URL a
note or a diff contains, to build a preview card — turning private knowledge-base content into an
outbound request to a third party the human never invoked, on a daemon whose whole premise is that
it is personal and local (arch §10)."""

TRANSPORT_CODE: Final = 0
"""The :attr:`TelegramError.code` for a failure that never reached Telegram — a refused connection,
a DNS blip, a read timeout. Not an HTTP status, because there was no HTTP response; ``0`` cannot
collide with one."""

RETRY_CODES: Final = frozenset({TRANSPORT_CODE, 408, 429, 500, 502, 503, 504})
"""Exactly what TG-8 names as transient: connection errors and read timeouts (``TRANSPORT_CODE``),
``408``, ``429`` and ``5xx``. **`409` is deliberately absent** — it means a second consumer of the
same token is polling (TG-9), and retrying makes the collision worse rather than better. Everything
else is about the *request* and would fail identically three times over."""

MAX_BACKOFF: Final = 30.0
"""Ceiling on any single wait, so a stated ``retry_after`` of an hour does not park the poll loop
past the point where the human would rather see the failure in ``/health``."""

_RETRY_AFTER: Final = "retry_after"


class TelegramError(Exception):
    """A refusal from the Bot API, carrying its own code and any ``retry_after``."""

    def __init__(self, method: str, code: int, description: str, retry_after: float = 0.0) -> None:
        self.method = method
        self.code = code
        self.description = description
        self.retry_after = retry_after
        # The method, never the URL: a bot token lives in the URL path and this message reaches
        # `/health` and the log (the redaction in `pkb.server.health` is the backstop, not the plan).
        super().__init__(f"{method} failed: {code} {description}")


class BotApi(Protocol):
    """The seven calls the adapter makes. A Protocol so every rule above it tests against a fake.

    ``edit_message`` and ``clear_keyboard`` are two methods rather than one because they map to two
    Telegram methods with different blast radii (TG-63, TG-67): one rewrites the text, the other
    removes the buttons and leaves every character of the message alone.
    """

    async def get_me(self) -> Mapping[str, Any]: ...

    async def get_updates(
        self, offset: int | None, *, timeout: int = POLL_TIMEOUT
    ) -> Sequence[Mapping[str, Any]]: ...

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: Sequence[Sequence[Mapping[str, str]]] | None = None,
    ) -> Mapping[str, Any]: ...

    async def send_document(
        self, chat_id: int, filename: str, content: bytes, caption: str = ""
    ) -> Mapping[str, Any]: ...

    async def answer_callback(
        self, callback_id: str, text: str = "", *, show_alert: bool = False
    ) -> None: ...

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None: ...

    async def clear_keyboard(self, chat_id: int, message_id: int) -> None: ...


class _RedactingFilter(logging.Filter):
    """Strip anything credential-shaped out of a record before a handler can write it (TG-16).

    A Telegram bot token is a **path segment** of every Bot API URL, and ``httpx`` logs the whole
    request line at INFO: ``HTTP Request: POST https://api.telegram.org/bot<TOKEN>/getUpdates
    "HTTP/1.1 200 OK"``. ``pkb.daemon.main`` sets ``basicConfig(level=INFO)`` and the bot long-polls
    roughly every 30 seconds forever, so without this the credential is appended to the daemon log
    for the life of the deployment — a secret sitting in the one file a human cheerfully pastes into
    a bug report (P-25, measured against a real bot).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


def shield_credentialed_http_logs() -> None:
    """Make the HTTP client's own logging safe. Idempotent; called wherever a token exists (TG-16).

    It lives **here**, beside the credential, rather than only in the composition root: this module
    is the one place a bot token is ever handed to an HTTP client, so a client built by a test, a
    script or a future caller that never goes through ``pkb.daemon`` is protected too. A shield the
    composition root installs protects only deployments assembled the one way it knows about, and
    the leak is a property of holding the token, not of how the process was started. ``pkb.daemon``
    still calls it as well, so the filter is in place before any client is constructed.

    Redacting rather than silencing, and on the ``httpx`` logger rather than the root: the daemon
    also drives httpx for the MCP client, and those request lines are useful and carry no secret.
    Raising ``httpx`` to WARNING would take them out too, which is how a human loses the one log
    line that tells them a request was made at all.

    ``httpcore`` gets the blunter treatment because the finer one cannot reach it: it logs through
    *child* loggers (``httpcore.http11``), and a filter on a parent logger is never consulted for a
    record its child created — ``Logger.handle`` filters only on the originating logger. Its trace
    records repeat the request target, token and all, so the level is clamped instead. That costs
    nothing at the daemon's INFO default and only bites a human who deliberately turns on DEBUG,
    where the alternative is printing the credential.
    """
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(existing, _RedactingFilter) for existing in httpx_logger.filters):
        httpx_logger.addFilter(_RedactingFilter())
    httpcore_logger = logging.getLogger("httpcore")
    if httpcore_logger.level == logging.NOTSET or httpcore_logger.level < logging.INFO:
        httpcore_logger.setLevel(logging.INFO)


@dataclass
class HttpBotApi:
    """:class:`BotApi` over ``api.telegram.org``.

    Two clients, deliberately: see the module docstring. ``token`` is never logged and never
    interpolated into anything this module raises.

    **The token is kept out of the ``repr`` twice over** (TG-16). ``field(repr=False)`` is the
    fallback for the day someone deletes the explicit ``__repr__`` below and the dataclass
    machinery generates one again; the ``__repr__`` itself is what actually runs. This is not
    fastidiousness: the client is held for the whole life of the supervised task, so it sits in a
    frame of every traceback the bot produces, and a ``repr`` is what an f-string, a ``%r`` log
    call and pytest's locals dump all print. The masked form names the *bot* — the numeric id in
    front of the colon, which ``getMe`` publishes anyway — because an operator running two bots
    needs to know which one this is, and a bare ``***`` would send them to the config file to
    guess.
    """

    token: str = field(repr=False)
    base_url: str = "https://api.telegram.org"
    _poll: httpx.AsyncClient | None = field(default=None, repr=False)
    _send: httpx.AsyncClient | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """TG-16: the moment a token exists in this process, the HTTP client's log is shielded.

        Construction rather than ``__aenter__`` because the guard costs nothing and the window has
        to be closed before the first request, not merely before the first *poll* — and a caller who
        builds the client without entering it still holds the credential.
        """
        shield_credentialed_http_logs()

    def __repr__(self) -> str:
        """TG-16: identifies the bot, never the secret."""
        bot_id, _, secret = self.token.partition(":")
        masked = f"{bot_id}:***" if secret else "***"
        return f"HttpBotApi(bot={masked}, base_url={self.base_url!r})"

    async def __aenter__(self) -> HttpBotApi:
        self._poll = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=READ_BUDGET))
        self._send = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        return self

    async def __aexit__(self, *_: object) -> None:
        for client in (self._poll, self._send):
            if client is not None:
                await client.aclose()
        self._poll = self._send = None

    @property
    def _url(self) -> str:
        return f"{self.base_url}/bot{self.token}"

    async def get_me(self) -> Mapping[str, Any]:
        return dict(await self._call("getMe", {}))

    async def get_updates(
        self, offset: int | None, *, timeout: int = POLL_TIMEOUT
    ) -> Sequence[Mapping[str, Any]]:
        """``timeout`` is a parameter for exactly one caller: TG-30's cold-start drain.

        The drain issues ``get_updates(-1, timeout=0)`` to learn the last update id and acknowledge
        it, and it runs *before* the bot is answering anything. At ``POLL_TIMEOUT`` it would park
        the whole startup for 25 s on a chat that is idle — which is the normal case — so the
        daemon would look hung on every restart. ``allowed_updates`` is unaffected: it is sent here
        too, because a drain is a poll and the setting persists server-side.
        """
        body: dict[str, Any] = {
            "timeout": timeout,
            # Every time. Not a default, not a one-off setup call — see the module docstring.
            "allowed_updates": list(ALLOWED_UPDATES),
        }
        if offset is not None:
            body["offset"] = offset
        result = await self._call("getUpdates", body, poll=True)
        return list(result) if isinstance(result, list) else []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: Sequence[Sequence[Mapping[str, str]]] | None = None,
    ) -> Mapping[str, Any]:
        """TG-47: ``link_preview_options`` goes on **every** send, not on the ones that look like
        they contain a URL. A reply is knowledge-base content and a diff is a proposed write; a
        preview card means Telegram fetched whatever URL was in it.

        **There is no ``parse_mode``, and there is no parameter for one** (TG-46). Every byte that
        leaves here is server-derived text — an agent's reply, or a ``describe_write`` description
        the human is about to approve. MarkdownV2 requires escaping ``_*[]()~`>#+-=|{}.!`` and a
        unified diff is *made of* those characters, so a mode set here would turn the most
        consequential message the bot sends into a ``400`` — the approval never arrives at all,
        which is the same class of failure as the markup error that killed the TUI's renderer.
        Plain text cannot fail that way, and the entities Telegram would have drawn are worth
        nothing beside an approval that reaches the phone.
        """
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "link_preview_options": dict(LINK_PREVIEW),
        }
        if keyboard:
            body["reply_markup"] = {"inline_keyboard": [list(row) for row in keyboard]}
        return dict(await self._call("sendMessage", body))

    async def send_document(
        self, chat_id: int, filename: str, content: bytes, caption: str = ""
    ) -> Mapping[str, Any]:
        """The whole description, in memory, never written to disk (I3 and plain hygiene).

        The one multipart call, and the one that does not go through :meth:`_call` — so its
        transport failures are wrapped here rather than there (TG-15). An overflow document is sent
        *before* the keyboard it belongs to, so a raw ``ConnectError`` escaping this method would
        restart the bot with a human halfway through an approval.
        """
        client = self._client(poll=False)
        try:
            response = await client.post(
                f"{self._url}/sendDocument",
                data={"chat_id": str(chat_id), "caption": caption[:1024]},
                files={"document": (filename, content, "text/plain")},
            )
        except httpx.HTTPError as exc:
            raise _transport_error("sendDocument", exc) from None
        return dict(self._unwrap("sendDocument", response))

    async def answer_callback(
        self, callback_id: str, text: str = "", *, show_alert: bool = False
    ) -> None:
        """Stops the client's spinner. Telegram expires an unanswered query, so this always runs.

        ``show_alert`` turns the toast into a modal the human has to dismiss, which is what a stale
        press deserves (TG-62): a toast beside an unchanged message reads as "nothing happened",
        and the human presses again.
        """
        body: dict[str, Any] = {"callback_query_id": callback_id, "text": text[:200]}
        if show_alert:
            body["show_alert"] = True
        await self._call("answerCallbackQuery", body)

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        """Rewrites a message's text — and **only** that (TG-63).

        Telegram leaves an existing ``reply_markup`` in place when ``editMessageText`` omits it, so
        this is not a keyboard-removal path even by accident; :meth:`clear_keyboard` is. Link
        previews are disabled here too (TG-47): an edit re-renders the message, so an edit that
        omitted the option would fetch every URL the original was careful not to.

        No ``parse_mode``, for the same reason as :meth:`send_message` (TG-46): an edit re-parses
        the whole text, so a mode here would 400 on exactly the diffs that were safe to send.
        """
        await self._call(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "link_preview_options": dict(LINK_PREVIEW),
            },
        )

    async def clear_keyboard(self, chat_id: int, message_id: int) -> None:
        """Removes the inline keyboard, leaving every character of the text alone (TG-63).

        ``editMessageReplyMarkup`` with **no** ``reply_markup`` key at all is how Telegram is told
        to remove a keyboard — an explicit ``null`` works, an absent field is the documented form,
        and either way the text is untouched.

        The alternative the build shipped was ``edit_message(chat, id, "This approval has been
        answered.")``, which removed the buttons by *overwriting the description of the write the
        human had just decided about*. On a system with no undo (D6), in a chat that is the only
        surviving record of what was approved, that erases the evidence at the exact moment it
        starts to matter — a week later, when the human scrolls back to find out what they said yes
        to. Buttons and prose are separate concerns and Telegram gives them separate methods.
        """
        await self._call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id})

    # -- plumbing ---------------------------------------------------------------------

    def _client(self, *, poll: bool) -> httpx.AsyncClient:
        client = self._poll if poll else self._send
        if client is None:  # pragma: no cover - a programming error, not a runtime state
            raise RuntimeError("the bot api is not open; use `async with HttpBotApi(...)`")
        return client

    async def _call(self, method: str, body: Mapping[str, Any], *, poll: bool = False) -> Any:
        client = self._client(poll=poll)
        try:
            response = await client.post(f"{self._url}/{method}", json=dict(body))
        except httpx.HTTPError as exc:
            raise _transport_error(method, exc) from None
        return self._unwrap(method, response)

    def _unwrap(self, method: str, response: httpx.Response) -> Any:
        try:
            payload = response.json()
        except ValueError:
            raise TelegramError(method, response.status_code, "a non-JSON response") from None
        if payload.get("ok"):
            return payload.get("result")
        parameters = payload.get("parameters") or {}
        raise TelegramError(
            method,
            int(payload.get("error_code", response.status_code)),
            str(payload.get("description", "")),
            float(parameters.get(_RETRY_AFTER, 0.0)),
        )


def _transport_error(method: str, exc: httpx.HTTPError) -> TelegramError:
    """Turn an ``httpx`` failure into the one error type this module raises (TG-15).

    The description is built from the exception's **type name** and nothing else. That is not
    terseness for its own sake: ``str(exc)`` on an httpx error is free-form and several of its
    subclasses interpolate the request URL, and the bot token is a path segment of every URL here
    (TG-16). ``last_error`` is published verbatim by ``/health``, which has no authentication
    (AP-20), so an error string that *might* carry the URL is a credential leak on the most
    travelled path in the layer. A type name — ``ConnectError``, ``ReadTimeout``, ``PoolTimeout`` —
    is the whole diagnosis anyway, and it cannot contain a URL by construction.
    """
    return TelegramError(method, TRANSPORT_CODE, f"transport failure: {type(exc).__name__}")


async def with_retry(call: Any, *, attempts: int = 3, backoff: float = 0.5) -> Any:
    """Absorb the transient failures TG-8 names, and let everything else through at once.

    TG-8's list is ``408``, ``429`` (honouring ``parameters.retry_after``), ``5xx``, connection
    errors and read timeouts — see :data:`RETRY_CODES`. They are absorbed *here* rather than by
    ``_supervise`` because a restart is a visible event: it increments ``SubsystemState.restarts``,
    which arch §8 asks to be the number a human trusts, and ``_supervise`` initialises its backoff
    outside its ``while``, so six dropped packets leave the bot at a permanent 60 s restart delay
    for the life of the process. A bot that reports "6 restarts" for six blips is a bot whose
    restart count gets ignored.

    ``429`` waits the **stated** ``retry_after``; Telegram names the number and ignoring it is how
    a bot earns a longer ban. Everything else backs off geometrically from ``backoff``, bounded by
    :data:`MAX_BACKOFF`, because there is no stated number to honour.

    Anything outside :data:`RETRY_CODES` propagates on the first attempt. A ``400`` or a ``401`` is
    about the request and would fail identically three times over, and a ``409`` means a second
    poller holds the same token (TG-9) — retrying that one does not resolve the collision, it
    *is* the collision, and the adapter has a dedicated slow re-probe for it.
    """
    for attempt in range(attempts):
        try:
            return await call()
        except TelegramError as exc:
            if exc.code not in RETRY_CODES or attempt == attempts - 1:
                raise
            if exc.code == 429:
                # Floored at `backoff` (TG-8): `parameters.retry_after` is documented as optional
                # and a 429 that omits it parses as 0.0, which turned three attempts into a hot
                # loop against a rate limiter — measured at 0.0001 s for the whole sequence, logged
                # as "waiting 0.0s". Retrying a rate limit with no wait is how a bot earns a longer
                # ban, so the absence of a stated number falls back to the ordinary backoff rather
                # than to none at all.
                delay = min(max(exc.retry_after, backoff), MAX_BACKOFF)
                _log.warning("rate limited on %s; waiting %.1fs", exc.method, delay)
            else:
                delay = min(backoff * (2**attempt), MAX_BACKOFF)
                _log.warning(
                    "transient failure on %s (%s %s); retrying in %.1fs",
                    exc.method,
                    exc.code,
                    exc.description,
                    delay,
                )
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable")  # pragma: no cover
