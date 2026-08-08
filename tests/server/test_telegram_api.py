"""The transport, over a real socket (TG-34, TG-48, TG-67 … TG-71, and TG-15/TG-16's leaks).

Everything here drives the **real** :class:`~pkb.server.telegram_api.HttpBotApi` against a **fake
Bot API** — a FastAPI app on 127.0.0.1 that speaks the real envelope
(``{"ok":true,"result":…}`` / ``{"ok":false,"error_code":N,"description":…,"parameters":{…}}``) and
enforces the two limits that were confirmed against ``api.telegram.org``. No token, no
``api.telegram.org``, no key: the whole file is keyless and networkless in the sense that matters —
nothing leaves loopback.

It is over a socket rather than in process because every rule in this file is about something an
ASGI shortcut erases. ``httpx.ASGITransport`` has no read timeout, no connection pool and no
concurrency between a held request and a second one, and the three findings this layer is paying
for — a poll severed by an under-sized read budget, an approval queued behind a 25 s long poll, and
a subscription silently narrowed server-side — are *exactly* those three things.

The fake is deliberately strict where the real API is strict. It counts a message in **UTF-16 code
units** and ``callback_data`` in **bytes**, because those two units are the whole content of TG-44
and TG-57, and a fake that counted characters would let the constants drift to a value the real
server rejects at the moment a human is waiting for an approval.

Three tests are ``xfail(strict=True)``. They are not aspiration: each is a rule in §1 that the
built transport does not yet keep, demonstrated rather than described.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import inspect
import json
import logging
import socket
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from pkb.server import telegram_api as transport
from pkb.server.telegram_api import (
    ALLOWED_UPDATES,
    CALLBACK_DATA_LIMIT,
    MESSAGE_LIMIT,
    POLL_TIMEOUT,
    READ_BUDGET,
    BotApi,
    HttpBotApi,
    TelegramError,
    with_retry,
)

TOKEN = "8123456789:AAF-not-a-real-bot-token-0123456789abcdef"
"""Shaped like a real one — ``<bot id>:<secret>`` — because half the rules here are about the fact
that it lives in the **URL path** of every single call."""

CHAT = 4242

SOURCE = Path(transport.__file__)

BANNED_LITERALS = ("127.0.0.1", "localhost", "/threads", "/runs", "/agents", "/health", "/mcp")
"""TG-68: the daemon's own API. A transport that could name it could become a second client of the
process it is already inside."""


def utf16_len(text: str) -> int:
    """What Telegram counts. ``"🔥"`` is one character and **two** units (P-26)."""
    return len(text.encode("utf-16-le")) // 2


# --------------------------------------------------------------------------------------
# The fake Bot API — the real shapes, the real limits, and a gate to hold a request open
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Call:
    """One request the fake received, as the wire delivered it."""

    method: str
    token: str
    verb: str
    content_type: str
    body: dict[str, Any]
    files: dict[str, tuple[str, bytes]] = field(default_factory=dict)


def ok(result: Any) -> tuple[int, Any]:
    return 200, {"ok": True, "result": result}


def refusal(code: int, description: str, *, retry_after: float | None = None) -> tuple[int, Any]:
    payload: dict[str, Any] = {"ok": False, "error_code": code, "description": description}
    if retry_after is not None:
        payload["parameters"] = {"retry_after": retry_after}
    return code, payload


def raw(status: int, body: str) -> tuple[int, str]:
    """A response that is not JSON at all — what a proxy or a gateway returns under load."""
    return status, body


class FakeBotApi:
    """``api.telegram.org``, minus the network and the token check.

    Three things it does that a dict of canned responses cannot: it **records** what arrived (so
    ``allowed_updates`` can be asserted on the third poll, not just the first), it can **hold** a
    request open (so a long poll has a duration), and it **enforces** the two documented limits.
    """

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.delivered: list[str] = []
        self.scripted: dict[str, list[tuple[int, Any]]] = {}
        self.gates: dict[str, asyncio.Event] = {}
        self.arrived: dict[str, asyncio.Event] = {}
        self.app = FastAPI()
        self.app.post("/bot{token}/{method}")(self._handle)

    # -- the test's side ---------------------------------------------------------------

    def script(self, method: str, *responses: tuple[int, Any]) -> None:
        """Queue responses for ``method``; anything past the queue gets the healthy default."""
        self.scripted.setdefault(method, []).extend(responses)

    def hold(self, method: str) -> asyncio.Event:
        """Hold every ``method`` request open until the returned event is set."""
        gate = asyncio.Event()
        self.gates[method] = gate
        self.arrived[method] = asyncio.Event()
        return gate

    async def wait_for(self, method: str) -> None:
        await asyncio.wait_for(self.arrived[method].wait(), 5)

    def calls_to(self, method: str) -> list[Call]:
        return [call for call in self.calls if call.method == method]

    # -- the server's side -------------------------------------------------------------

    async def _handle(self, token: str, method: str, request: Request) -> Response:
        self.calls.append(await self._record(token, method, request))
        if method in self.arrived:
            self.arrived[method].set()
        gate = self.gates.get(method)
        if gate is not None:
            await gate.wait()
        queued = self.scripted.get(method) or []
        status, payload = queued.pop(0) if queued else self._answer(method)
        if isinstance(payload, str):
            return Response(payload, status_code=status, media_type="text/html")
        return JSONResponse(payload, status_code=status)

    async def _record(self, token: str, method: str, request: Request) -> Call:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            body = {key: value for key, value in form.multi_items() if isinstance(value, str)}
            files = {
                key: (value.filename or "", await value.read())
                for key, value in form.multi_items()
                if isinstance(value, UploadFile)
            }
            return Call(method, token, request.method, content_type, body, files)
        body = json.loads(await request.body() or b"{}")
        return Call(method, token, request.method, content_type, body)

    def _answer(self, method: str) -> tuple[int, Any]:
        call = self.calls[-1]
        if method == "sendMessage":
            over = self._over_limit(call)
            if over is not None:
                return over
            self.delivered.append(str(call.body["text"]))
        if method == "getUpdates":
            return ok([])
        if method == "getMe":
            return ok({"id": 8123456789, "is_bot": True, "username": "pkb_test_bot"})
        if method in ("sendMessage", "sendDocument"):
            return ok({"message_id": len(self.calls)})
        return ok(True)

    @staticmethod
    def _over_limit(call: Call) -> tuple[int, Any] | None:
        """The two refusals confirmed live: 4097 units and 65 bytes of ``callback_data``."""
        if utf16_len(str(call.body.get("text", ""))) > MESSAGE_LIMIT:
            return refusal(400, "Bad Request: message is too long")
        rows = (call.body.get("reply_markup") or {}).get("inline_keyboard") or []
        for row in rows:
            for button in row:
                if len(str(button.get("callback_data", "")).encode()) > CALLBACK_DATA_LIMIT:
                    return refusal(400, "Bad Request: BUTTON_DATA_INVALID")
        return None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.asynccontextmanager
async def serving(fake: FakeBotApi) -> AsyncIterator[HttpBotApi]:
    """One uvicorn on loopback, with an opened :class:`HttpBotApi` pointed at it.

    Every gate is released on the way out: uvicorn's graceful shutdown waits for in-flight
    requests, and a test that leaves a poll held would hang the teardown rather than the test.
    """
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(fake.app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        async with HttpBotApi(token=TOKEN, base_url=f"http://127.0.0.1:{port}") as api:
            yield api
    finally:
        for gate in list(fake.gates.values()):
            gate.set()
        server.should_exit = True
        await asyncio.wait_for(task, 5)


# --------------------------------------------------------------------------------------
# § The subscription (TG-34)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowed_updates_is_sent_on_every_poll_not_just_the_first_tg34() -> None:
    """``allowed_updates`` **persists server-side**, so the last poll to omit it wins forever.

    This is the find of the layer. A real bot was left subscribed to ``["message"]`` by an earlier
    caller, and from then on Telegram dropped every ``callback_query`` *before delivery*: no error,
    no log line, no pending update, no redelivery after 24 hours. The poll loop looked perfectly
    healthy and approvals were simply unanswerable from the phone — the one thing this whole layer
    exists to make possible.

    Sending it once at startup is therefore not enough, and neither is sending it "when it
    changes": the setting can be narrowed by anything else holding the token. It goes on the third
    poll for the same reason it goes on the first.
    """
    fake = FakeBotApi()
    async with serving(fake) as api:
        assert await api.get_updates(None) == []

        fake.script("getUpdates", ok([{"update_id": 7, "callback_query": {"id": "q-1"}}]))
        second = await api.get_updates(1)
        await api.get_updates(8)

    assert [update["update_id"] for update in second] == [7]

    polls = fake.calls_to("getUpdates")
    assert len(polls) == 3
    for poll in polls:
        assert poll.body["allowed_updates"] == ["message", "edited_message", "callback_query"]
        assert poll.body["timeout"] == POLL_TIMEOUT
    assert [poll.body.get("offset") for poll in polls] == [None, 1, 8]


def test_the_subscription_names_every_kind_the_bot_must_receive_tg34() -> None:
    """The bot's whole inbound surface, auditable in one line — and it must be a superset of what
    the adapter dispatches on.

    ``callback_query`` carries every approval answer; ``message`` carries every turn. The third
    name is load-bearing too: TG-35 requires an ``edited_message`` to be acknowledged exactly once
    ("send the correction as a new message"), and an update kind that is not subscribed to is never
    delivered — so a bot narrowed to the two names TG-34's prose lists could not keep TG-35 at all.
    Anything *not* named here still costs an offset slot and a ledger row, which is why the tuple is
    a closed list rather than ``None`` (Telegram's "everything except chat_member").
    """
    assert ALLOWED_UPDATES == ("message", "edited_message", "callback_query")
    assert "channel_post" not in ALLOWED_UPDATES
    assert "my_chat_member" not in ALLOWED_UPDATES


# --------------------------------------------------------------------------------------
# § The read budget and the two pools (TG-69)
# --------------------------------------------------------------------------------------


def test_the_read_budget_strictly_exceeds_the_poll_timeout_tg69() -> None:
    """A read budget at or below the poll timeout makes **every idle poll** raise.

    Telegram holds ``getUpdates`` open for the full ``timeout`` when nothing is pending — that is
    what long polling *is* — so a client that gives up first turns the healthy steady state into a
    ``ReadTimeout``. ``_supervise`` restarts on anything, so the daemon then spins, and
    ``/health`` reports "the network is down" when the truth is a mis-sized constant. Layer 4 paid
    this exact bill once already (P-11: httpx's 5 s default "kills every real turn").

    The margin is asserted, not just the inequality: equal-plus-a-second would still race the round
    trip on a slow link, and the failure mode is a crash loop rather than a slow reply.
    """
    assert READ_BUDGET > POLL_TIMEOUT
    assert READ_BUDGET - POLL_TIMEOUT >= 10


@pytest.mark.asyncio
async def test_the_poll_client_is_the_one_holding_the_read_budget_tg69() -> None:
    """A correct constant that never reaches a client is decoration.

    ``READ_BUDGET`` only prevents anything if it is what the *polling* client waits on; and the
    sending client must **not** inherit it, because a 40 s wait on a stuck ``sendMessage`` is 40 s
    the approval keyboard is not on the human's phone.
    """
    fake = FakeBotApi()
    async with serving(fake) as api:
        assert api._poll is not None and api._send is not None
        assert api._poll.timeout.read == READ_BUDGET
        assert api._send.timeout.read is not None
        assert api._send.timeout.read < POLL_TIMEOUT


@pytest.mark.asyncio
async def test_an_undersized_read_budget_severs_a_healthy_poll_tg69() -> None:
    """The defect the budget prevents, demonstrated against a poll that is doing nothing wrong.

    Measured against the real API: a client with ``read=2.0`` against ``getUpdates(timeout=8)``
    raised ``ReadTimeout`` at 2.2 s — the server was still holding the connection open exactly as
    documented. Here the fake plays the same part: it holds the request, the starved client dies,
    and the shipped client is still waiting on the very same request and returns normally once the
    server answers. The distinction is invisible from the error alone, which is why it shipped.
    """
    fake = FakeBotApi()
    gate = fake.hold("getUpdates")
    async with serving(fake) as api:
        poll = asyncio.create_task(api.get_updates(None))
        await fake.wait_for("getUpdates")

        starved = httpx.AsyncClient(timeout=httpx.Timeout(5.0, read=0.05))
        async with starved:
            with pytest.raises(httpx.ReadTimeout):
                await starved.post(
                    f"{api.base_url}/bot{TOKEN}/getUpdates",
                    json={"timeout": POLL_TIMEOUT, "allowed_updates": list(ALLOWED_UPDATES)},
                )

        assert not poll.done()
        gate.set()
        assert await asyncio.wait_for(poll, 5) == []


@pytest.mark.asyncio
async def test_a_send_does_not_queue_behind_an_in_flight_long_poll_tg69() -> None:
    """A 25 s long poll must never occupy the socket an approval goes out on.

    The pinned fact is the two pools — one client for ``getUpdates``, one for everything else —
    because that is what stops a single saturated or keep-alive-bound connection from serialising
    the two. The observable consequence is asserted alongside it: with a poll held open, a
    ``sendMessage`` still completes, and the poll is still pending afterwards, so the send did not
    simply win a race for one connection. Without the split, a human holding their phone waits up
    to a full poll timeout for the keyboard on a write they are being asked to approve.
    """
    fake = FakeBotApi()
    gate = fake.hold("getUpdates")
    async with serving(fake) as api:
        assert api._poll is not api._send

        poll = asyncio.create_task(api.get_updates(None))
        await fake.wait_for("getUpdates")

        sent = await asyncio.wait_for(api.send_message(CHAT, "approve this write?"), 2)

        assert sent["message_id"]
        assert fake.delivered == ["approve this write?"]
        assert not poll.done()
        gate.set()
        await asyncio.wait_for(poll, 5)


@pytest.mark.asyncio
async def test_cancelling_an_in_flight_poll_returns_at_once_tg69() -> None:
    """Shutdown must not wait out the poll it interrupted.

    The poll spends almost all of its life parked on one ``await``, and the daemon's stop path
    cancels the task and then closes the clients. If the cancellation only took effect when the
    request completed, every restart — and every Ctrl-C — would stall for up to the read budget,
    which is long enough to look like a hang and get the process killed instead.
    """
    fake = FakeBotApi()
    gate = fake.hold("getUpdates")
    async with serving(fake) as api:
        poll = asyncio.create_task(api.get_updates(None))
        await fake.wait_for("getUpdates")

        started = time.perf_counter()
        poll.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(poll, 1)

        assert time.perf_counter() - started < 0.2
        gate.set()


# --------------------------------------------------------------------------------------
# § Rate limits (TG-48)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_429_is_waited_out_and_the_message_is_delivered_exactly_once_tg48() -> None:
    """Telegram states the wait; ignoring it is how a bot earns a longer one.

    This is a **transport** retry of an idempotent send, and explicitly not the run retry TU-32
    forbids: nothing here re-issues ``start_run`` or ``resume``, it re-sends the same bytes to the
    same chat. Dropping it instead is not a cosmetic loss — a dropped reply after an approved write
    means the human never learns what was written, and there is no undo; a dropped keyboard means a
    parked interrupt nobody is ever told about.

    "Exactly once" is asserted on the fake's *delivery* list rather than its request count: the
    refused attempt must not also reach the chat, which is what a naive re-send on a 429 that was
    actually applied would produce.
    """
    fake = FakeBotApi()
    fake.script("sendMessage", refusal(429, "Too Many Requests: retry after 1", retry_after=0.2))
    async with serving(fake) as api:
        started = time.perf_counter()
        sent = await with_retry(lambda: api.send_message(CHAT, "filed under Cooking"))
        elapsed = time.perf_counter() - started

    assert sent["message_id"]
    assert fake.delivered == ["filed under Cooking"]
    assert len(fake.calls_to("sendMessage")) == 2
    assert 0.2 <= elapsed < 0.7


@pytest.mark.asyncio
async def test_a_persistent_429_gives_up_rather_than_hammering_tg48() -> None:
    """A bounded number of attempts, and the wait comes from the payload rather than a constant.

    ``retry_after: 0`` here, so three attempts complete in well under a tenth of a second — which
    is only true if the sleep is the *stated* value. An unbounded retry against a rate limiter is
    how a token gets a longer ban, and the supervisor's restart is the right escalation: it is
    visible in ``/health``, whereas a loop inside one call is invisible everywhere.
    """
    fake = FakeBotApi()
    fake.script(
        "sendMessage", *[refusal(429, "Too Many Requests", retry_after=0) for _ in range(5)]
    )
    async with serving(fake) as api:
        started = time.perf_counter()
        with pytest.raises(TelegramError) as caught:
            await with_retry(lambda: api.send_message(CHAT, "filed under Cooking"))
        elapsed = time.perf_counter() - started

    assert caught.value.code == 429
    assert len(fake.calls_to("sendMessage")) == 3
    assert fake.delivered == []
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_anything_that_is_not_a_rate_limit_propagates_untouched_tg48() -> None:
    """A 400 or a 401 will fail identically three times over.

    Retrying a refusal that is about the *request* buys nothing and hides the cause: the supervisor
    can restart and ``/health`` can name a bad token, but only if the error reaches them promptly
    and unchanged. Exactly one request goes out, and the description arrives verbatim so the human
    sees Telegram's own words rather than "send failed".
    """
    fake = FakeBotApi()
    fake.script("sendMessage", refusal(400, "Bad Request: chat not found"))
    async with serving(fake) as api:
        started = time.perf_counter()
        with pytest.raises(TelegramError) as caught:
            await with_retry(lambda: api.send_message(CHAT, "filed under Cooking"))
        elapsed = time.perf_counter() - started

    assert (caught.value.code, caught.value.description) == (400, "Bad Request: chat not found")
    assert len(fake.calls_to("sendMessage")) == 1
    assert elapsed < 0.5


# --------------------------------------------------------------------------------------
# § Refusals become one typed error, and it never carries the token (TG-15)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_refusal_carries_the_method_code_and_stated_wait_tg15() -> None:
    """Everything a caller needs to decide what to do, and nothing it has to parse prose for.

    ``with_retry`` branches on ``code == 429`` and sleeps ``retry_after``; ``/health`` prints the
    description; the log names the method. Had any of those been left to string-matching the
    description, a wording change on Telegram's side would silently turn a rate limit into a fatal
    error — and the description is the one field here that is documented to be human-facing prose.
    """
    fake = FakeBotApi()
    fake.script("sendMessage", refusal(429, "Too Many Requests: retry after 7", retry_after=7))
    async with serving(fake) as api:
        with pytest.raises(TelegramError) as caught:
            await api.send_message(CHAT, "filed under Cooking")

    error = caught.value
    assert (error.method, error.code, error.retry_after) == ("sendMessage", 429, 7.0)
    assert error.description == "Too Many Requests: retry after 7"
    assert "sendMessage" in str(error) and "429" in str(error)


@pytest.mark.asyncio
async def test_a_401_names_the_failure_without_the_token_tg15() -> None:
    """A wrong token is the *most likely* first failure of a new deployment.

    That is precisely when the human copies the error into a chat window to ask for help, and the
    token sits in the URL path of every call — so ``raise_for_status()``'s message ("Client error
    '401 Unauthorized' for url 'https://api.telegram.org/bot<TOKEN>/getUpdates'") is a credential
    leak on the most-travelled path in the layer. ``/health`` publishes ``last_error`` verbatim and
    has no authentication (AP-20), so the error string is effectively public.
    """
    fake = FakeBotApi()
    fake.script("getUpdates", refusal(401, "Unauthorized"))
    async with serving(fake) as api:
        with pytest.raises(TelegramError) as caught:
            await api.get_updates(None)

    error = caught.value
    assert (error.method, error.code) == ("getUpdates", 401)
    assert TOKEN not in str(error)
    assert TOKEN not in repr(error)
    assert TOKEN.split(":")[1] not in str(error)


@pytest.mark.asyncio
async def test_a_non_json_response_becomes_the_same_typed_error_tg15() -> None:
    """A gateway under load answers with HTML, and ``response.json()`` raises inside the parser.

    That exception is neither a ``TelegramError`` nor anything the caller catches, so it escapes
    the poll loop as a ``JSONDecodeError`` — the supervisor restarts and ``/health`` reports a
    parse error for what is really "Telegram is having a bad afternoon". The status code is
    preserved so the human sees the 502 that actually happened.
    """
    fake = FakeBotApi()
    fake.script("getUpdates", raw(502, "<html><body>502 Bad Gateway</body></html>"))
    async with serving(fake) as api:
        with pytest.raises(TelegramError) as caught:
            await api.get_updates(None)

    assert caught.value.code == 502
    assert caught.value.method == "getUpdates"


@pytest.mark.asyncio
async def test_an_ok_false_body_on_a_200_is_still_a_refusal_tg15() -> None:
    """The envelope, not the status line, is the Bot API's answer.

    Telegram has been observed returning ``ok: false`` with a 200, and a transport that trusted
    ``response.status_code`` would hand the adapter ``result: None`` as a success — after which the
    adapter records a message id it never got and edits a message that does not exist.
    """
    fake = FakeBotApi()
    fake.script("sendMessage", (200, {"ok": False, "error_code": 403, "description": "blocked"}))
    async with serving(fake) as api:
        with pytest.raises(TelegramError) as caught:
            await api.send_message(CHAT, "filed under Cooking")

    assert (caught.value.code, caught.value.description) == (403, "blocked")
    assert caught.value.retry_after == 0.0


@pytest.mark.xfail(
    reason="TG-15: telegram_api catches no transport error; raw httpx errors reach _supervise",
    strict=True,
)
@pytest.mark.asyncio
async def test_a_transport_failure_becomes_a_typed_error_too_tg15() -> None:
    """TG-15 is "no raw ``httpx`` exception reaches ``_supervise``", and connection errors are the
    common case — a laptop that slept, a DNS blip, Telegram closing a keep-alive connection.

    ``pkb/server/telegram.py`` cannot catch them: it imports no HTTP client, on purpose (TG-67), so
    it can only suppress ``TelegramError``. What escapes instead is an ``httpx.ConnectError``,
    which kills the task group, restarts the bot, and drops the adapter's ``_pump_outbox``
    suppression on the floor — the outbox is where a *progress* message failing is supposed to be
    harmless. Converting it here is the only place the conversion can happen.
    """
    async with HttpBotApi(token=TOKEN, base_url=f"http://127.0.0.1:{_free_port()}") as api:
        with pytest.raises(TelegramError):
            await api.get_me()


# --------------------------------------------------------------------------------------
# § The token never appears anywhere it can be read (TG-16)
# --------------------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="TG-16: HttpBotApi is a dataclass with a plain `token` field", strict=True
)
def test_the_token_is_not_in_the_clients_repr_tg16() -> None:
    """TG-16: the token never appears in a ``repr``.

    A dataclass ``repr`` is not a curiosity — it is what an f-string, a ``_log.debug("%r", api)``,
    a ``TaskGroup`` traceback and pytest's own assertion-locals dump all print. The client is held
    for the whole life of the daemon by the supervised task, so it is in the frame of every
    traceback the bot ever produces.
    """
    assert TOKEN not in repr(HttpBotApi(token=TOKEN))


@pytest.mark.xfail(
    reason="TG-16: nothing filters logging.getLogger('httpx'); it logs the token URL at INFO",
    strict=True,
)
@pytest.mark.asyncio
async def test_no_log_record_carries_the_token_tg16(caplog: pytest.LogCaptureFixture) -> None:
    """``httpx`` logs the full request URL at INFO, and the token is a path segment of it.

    ``pkb.daemon.main`` calls ``basicConfig(level=logging.INFO)``, so on a live deployment this
    writes ``HTTP Request: POST https://api.telegram.org/bot<TOKEN>/getUpdates "HTTP/1.1 200 OK"``
    to the daemon log roughly every 30 seconds, forever — a credential in a file the human will
    happily paste into a bug report. One poll is enough to show it.
    """
    caplog.set_level(logging.INFO)
    fake = FakeBotApi()
    async with serving(fake) as api:
        await api.get_updates(None)

    leaked = [record.name for record in caplog.records if TOKEN in record.getMessage()]
    assert leaked == []


@pytest.mark.asyncio
async def test_every_non_file_call_is_a_json_post_tg16() -> None:
    """A ``GET`` would put knowledge-base content in the URL — and URLs are what get logged.

    Every proxy, gateway and access log along the path records the request line. A 4096-character
    reply or an approval description in the query string is that content copied into
    infrastructure the human never chose, and it would exceed common URL length limits besides.
    ``sendDocument`` is the one exception, and it is multipart rather than a URL either way.
    """
    fake = FakeBotApi()
    async with serving(fake) as api:
        await api.get_me()
        await api.get_updates(None)
        await api.send_message(CHAT, "filed under Cooking")
        await api.answer_callback("q-1", "Approved")
        await api.edit_message(CHAT, 12, "Approved — written to topics/Cooking/notes/steak.md")

    assert [call.method for call in fake.calls] == [
        "getMe",
        "getUpdates",
        "sendMessage",
        "answerCallbackQuery",
        "editMessageText",
    ]
    for call in fake.calls:
        assert call.verb == "POST"
        assert call.content_type.startswith("application/json")


# --------------------------------------------------------------------------------------
# § The limits are the ones the real server enforces (TG-44, TG-57)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_message_limit_is_the_boundary_the_api_enforces_tg44() -> None:
    """4096 is a **hard refusal**, not advice, and the failure lands on the human's side.

    Confirmed live: 4096 units is accepted, 4097 gets ``400 message is too long``. A splitter sized
    one unit too generously therefore does not send a slightly-too-long message — it sends
    *nothing*, and the human is left with silence where a reply or an approval was supposed to be.
    """
    assert MESSAGE_LIMIT == 4096
    fake = FakeBotApi()
    async with serving(fake) as api:
        assert await api.send_message(CHAT, "x" * MESSAGE_LIMIT)

        with pytest.raises(TelegramError) as caught:
            await api.send_message(CHAT, "x" * (MESSAGE_LIMIT + 1))

    assert caught.value.code == 400
    assert "too long" in caught.value.description
    assert fake.delivered == ["x" * MESSAGE_LIMIT]


@pytest.mark.asyncio
async def test_the_message_limit_counts_utf16_units_not_characters_tg44() -> None:
    """The limit's **unit** is where this silently goes wrong (P-26).

    2049 fire emoji are 2049 characters — comfortably under 4096 by any ``len()`` — and 4098 UTF-16
    code units, which Telegram rejects. A knowledge base about food, travel or code carries
    astral-plane characters routinely, so this is the common case rather than an exotic one, and
    the character-counting budget reports ``was_truncated=False`` while producing it.
    """
    text = "🔥" * (MESSAGE_LIMIT // 2 + 1)
    assert len(text) < MESSAGE_LIMIT
    assert utf16_len(text) > MESSAGE_LIMIT

    fake = FakeBotApi()
    async with serving(fake) as api:
        with pytest.raises(TelegramError) as caught:
            await api.send_message(CHAT, text)

    assert "too long" in caught.value.description
    assert fake.delivered == []


@pytest.mark.asyncio
async def test_the_callback_budget_is_sixty_four_bytes_tg57() -> None:
    """64 **bytes**, and the server checks it at the moment a human is waiting for an approval.

    Confirmed live: 64 bytes is accepted, 65 gets ``400 BUTTON_DATA_INVALID``. Neither PTB nor
    aiogram enforces it at construction, so an over-long ``callback_data`` is not caught when the
    keyboard is built — it is caught when the keyboard fails to arrive, which is why the adapter
    keeps the mapping and puts a short opaque handle on the wire. Bytes rather than characters is
    the second half: 22 emoji are 22 characters and 88 bytes.
    """
    assert CALLBACK_DATA_LIMIT == 64
    fake = FakeBotApi()
    fits = [[{"text": "approve", "callback_data": "d" * CALLBACK_DATA_LIMIT}]]
    over = [[{"text": "approve", "callback_data": "d" * (CALLBACK_DATA_LIMIT + 1)}]]
    emoji = [[{"text": "approve", "callback_data": "🔥" * 22}]]

    async with serving(fake) as api:
        assert await api.send_message(CHAT, "approve?", keyboard=fits)

        for keyboard in (over, emoji):
            with pytest.raises(TelegramError) as caught:
                await api.send_message(CHAT, "approve?", keyboard=keyboard)
            assert "BUTTON_DATA_INVALID" in caught.value.description

    assert len(("🔥" * 22).encode()) > CALLBACK_DATA_LIMIT
    assert fake.calls_to("sendMessage")[0].body["reply_markup"] == {"inline_keyboard": fits}


# --------------------------------------------------------------------------------------
# § The uploaded document never touches disk (TG-71)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_document_uploads_from_memory_and_writes_nothing_tg71() -> None:
    """The overflow document is a *description of a proposed write*, and I3 says only Layer 1
    writes files.

    Staging it as a temp file would put knowledge-base content — often the full text of a file that
    has not been approved yet — on disk under a name nothing tracks, outside the tree, surviving a
    crash. The write paths are made to explode for the duration, so "in memory" is asserted rather
    than assumed, and the bytes are checked to arrive verbatim: a document truncated on the way to
    the phone is a decision made against a partial diff.
    """
    payload = ("--- a/topics/Cooking/notes/steak.md\n+++ b/…\n" * 100).encode()

    def explode(*_: object, **__: object) -> Any:
        raise AssertionError("the transport wrote to disk")

    fake = FakeBotApi()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Path, "write_bytes", explode)
        patch.setattr(Path, "write_text", explode)
        patch.setattr(tempfile, "NamedTemporaryFile", explode)
        patch.setattr(tempfile, "mkstemp", explode)
        async with serving(fake) as api:
            sent = await api.send_document(CHAT, "approval.diff", payload, caption="c" * 2000)

    call = fake.calls_to("sendDocument")[-1]
    assert sent["message_id"]
    assert call.content_type.startswith("multipart/form-data")
    assert call.files["document"] == ("approval.diff", payload)
    assert call.body["chat_id"] == str(CHAT)
    assert len(call.body["caption"]) == 1024


# --------------------------------------------------------------------------------------
# § The seam itself (TG-67, TG-68, TG-70)
# --------------------------------------------------------------------------------------


def test_the_http_client_implements_the_protocol_exactly_tg67() -> None:
    """The Protocol is what makes the other 60 rules testable without a socket — but only while its
    one implementation actually matches it.

    Every adapter test in this layer drives a fake typed as :class:`BotApi`. If the real client's
    signature drifted — a renamed keyword, a moved default, an extra required argument — the whole
    adapter suite would keep passing against a shape that no longer exists, and the first thing to
    find out would be the live bot. Comparing signatures pins the fake and the real client to one
    contract.
    """
    methods = {name for name in vars(BotApi) if not name.startswith("_")}

    assert methods == {
        "get_me",
        "get_updates",
        "send_message",
        "send_document",
        "answer_callback",
        "edit_message",
    }
    for name in sorted(methods):
        assert inspect.signature(getattr(HttpBotApi, name)) == inspect.signature(
            getattr(BotApi, name)
        ), name


def _literals(source: Path) -> list[str]:
    """Every string constant in the module that is not a docstring — code, not prose."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    prose = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in prose
    ]


def test_the_only_base_url_in_the_transport_is_the_bot_apis_tg68() -> None:
    """MC-7 as amended: what is forbidden is an HTTP client used to reach the **daemon's own API**.

    Egress to ``api.telegram.org`` is the whole job; a request to ``127.0.0.1/threads`` would make
    the bot a second client of the process it already lives inside — a second failure mode, a
    second copy of the error table, and a deadlock waiting to happen when the daemon's own event
    loop is the thing that is busy. Asserting the base URL positively is the check that survives
    someone adding a second one, which a "no loopback literal" grep alone would not catch.
    """
    assert HttpBotApi(token=TOKEN).base_url == "https://api.telegram.org"

    literals = _literals(SOURCE)
    assert [text for text in literals if "://" in text] == ["https://api.telegram.org"]
    for banned in BANNED_LITERALS:
        assert not [text for text in literals if banned in text], banned


def test_the_transport_mints_no_uuid_tg70() -> None:
    """Ids in this system are minted in exactly one place, and a transport is not it.

    SV-10 exists because a second minting site is a second id format, and the callback handles this
    layer puts on the wire have a hard 64-**byte** budget a uuid would eat half of. The rule is
    stated as "nothing named ``uuid*``" precisely so it can be checked mechanically here rather
    than argued about at review time.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names |= {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    names |= {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert not [name for name in names if name.startswith("uuid")]
