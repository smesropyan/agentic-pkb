"""The Bot API, over httpx — the **only** module in `pkb.server` that opens a socket (TG-67, TG-68).

A sibling module rather than a subpackage, and that is not taste: five built seam scans use a
**non-recursive** ``glob("*.py")``, so identical code inside a ``telegram/`` package is invisible to
SV-1, SV-10, SV-18, SV-22 and SV-25 — the isolation would switch off the checks for the newest
transport. Executed both ways to confirm it.

Everything here is behind :class:`BotApi` so the adapter, which holds every rule, can be driven
against a fake with no socket and no token. Six methods is the whole surface.

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
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

import httpx

__all__ = [
    "ALLOWED_UPDATES",
    "CALLBACK_DATA_LIMIT",
    "MESSAGE_LIMIT",
    "POLL_TIMEOUT",
    "BotApi",
    "HttpBotApi",
    "TelegramError",
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
    """The six calls the adapter makes. A Protocol so every rule above it tests against a fake."""

    async def get_me(self) -> Mapping[str, Any]: ...

    async def get_updates(self, offset: int | None) -> Sequence[Mapping[str, Any]]: ...

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

    async def answer_callback(self, callback_id: str, text: str = "") -> None: ...

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None: ...


@dataclass
class HttpBotApi:
    """:class:`BotApi` over ``api.telegram.org``.

    Two clients, deliberately: see the module docstring. ``token`` is never logged and never
    interpolated into anything this module raises.
    """

    token: str
    base_url: str = "https://api.telegram.org"
    _poll: httpx.AsyncClient | None = field(default=None, repr=False)
    _send: httpx.AsyncClient | None = field(default=None, repr=False)

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

    async def get_updates(self, offset: int | None) -> Sequence[Mapping[str, Any]]:
        body: dict[str, Any] = {
            "timeout": POLL_TIMEOUT,
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
        body: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if keyboard:
            body["reply_markup"] = {"inline_keyboard": [list(row) for row in keyboard]}
        return dict(await self._call("sendMessage", body))

    async def send_document(
        self, chat_id: int, filename: str, content: bytes, caption: str = ""
    ) -> Mapping[str, Any]:
        """The whole description, in memory, never written to disk (I3 and plain hygiene)."""
        client = self._client(poll=False)
        response = await client.post(
            f"{self._url}/sendDocument",
            data={"chat_id": str(chat_id), "caption": caption[:1024]},
            files={"document": (filename, content, "text/plain")},
        )
        return dict(self._unwrap("sendDocument", response))

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        """Stops the client's spinner. Telegram expires an unanswered query, so this always runs."""
        await self._call(
            "answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]}
        )

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        """Replaces a resolved approval's message, **removing its keyboard** so it cannot be
        pressed twice — the phone's answer to CL-20's duplicate-decision problem."""
        await self._call(
            "editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text}
        )

    # -- plumbing ---------------------------------------------------------------------

    def _client(self, *, poll: bool) -> httpx.AsyncClient:
        client = self._poll if poll else self._send
        if client is None:  # pragma: no cover - a programming error, not a runtime state
            raise RuntimeError("the bot api is not open; use `async with HttpBotApi(...)`")
        return client

    async def _call(self, method: str, body: Mapping[str, Any], *, poll: bool = False) -> Any:
        client = self._client(poll=poll)
        response = await client.post(f"{self._url}/{method}", json=dict(body))
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


async def with_retry(call: Any, *, attempts: int = 3) -> Any:
    """Honour a ``429``'s ``retry_after`` and give up rather than hammering.

    Telegram states the wait; ignoring it is how a bot earns a longer one. Anything that is not a
    rate limit propagates immediately — the supervisor's job is to restart, not this function's to
    paper over a 401.
    """
    for attempt in range(attempts):
        try:
            return await call()
        except TelegramError as exc:
            if exc.code != 429 or attempt == attempts - 1:
                raise
            _log.warning("rate limited on %s; waiting %.1fs", exc.method, exc.retry_after)
            await asyncio.sleep(min(exc.retry_after, 30.0))
    raise RuntimeError("unreachable")  # pragma: no cover
