"""The daemon, over HTTP — the only module in Layer 4 that knows a socket exists (DC-9 … DC-17).

Everything the TUI does goes through here, and everything here goes through the thirteen routes.
The client holds no policy: it does not decide what a frame means, it does not retry, and it never
invents a request the route table does not have.

Four things are measured rather than chosen:

* **Two timeout budgets, not one** (DC-9, DC-10). httpx2's default read timeout is 5 s and that
  kills every real turn: the gap between ``run.started`` and the first token is a whole model call —
  ~16 s on the cloud default, 284 s on the local fallback. Measured against a live socket, an 8 s
  gap raised ``ReadTimeout after 5.00s`` having received only ``run.started``, while the daemon
  carried on and filed the note (AP-7) — so the human sees a network error over a run that worked.
  The streaming budget is set above ``PING_SECONDS`` instead of disabled, because the server's
  ``: ping`` comment frames **do** reset httpx2's read timer even though the decoder never surfaces
  them: three missed heartbeats is a real hang, and ``read=None`` would wait for one forever.
* **204 is checked before an EventSource is constructed** (DC-11). ``GET /threads/{id}/events``
  answers 204 with no content type when the thread is idle — the *normal* case, since most threads a
  human opens are not running — and handing that to a decoder raises an SSE protocol error on every
  ordinary thread open.
* **An error body is read explicitly** (DC-12). A streaming response holds nothing until it is
  read, so a 409 arriving before the stream opens has an empty body unless the client asks for it.
* **A stream that ends without a terminal frame means *outcome unknown*** (DC-14, SS-7). Both
  endings are real and they look different: a clean close ends the iteration with **no exception at
  all**, and an aborted socket raises. Neither is a result. The client re-syncs with
  ``GET /threads/{id}`` and renders neither success nor failure — and never re-POSTs the run, which
  would file the same material twice with no undo.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final

import httpx2

from pkb.clients.sse import Frame, decode_frame

__all__ = [
    "JSON_TIMEOUT",
    "SSE_TIMEOUT",
    "PkbClient",
    "PkbHttpError",
    "StreamEndedError",
]

_log = logging.getLogger(__name__)

SSE_TIMEOUT: Final = httpx2.Timeout(10.0, read=45.0)
"""Streaming budget: three missed 15 s heartbeats.

Not ``None``. The server's ping resets this timer (measured: ``read=2.5`` survived a 6 s silence at
``ping=1``), so a finite value still tolerates a 284 s local-model turn while a genuinely dead
connection is noticed in under a minute rather than never.
"""

JSON_TIMEOUT: Final = httpx2.Timeout(10.0)
"""Everything else. Each of those routes is one indexed SQL read and never a model call (AP-19)."""


class PkbHttpError(Exception):
    """A typed refusal from the daemon, carrying the machine ``code`` a client branches on (RO-21).

    Never the prose: three conditions share 409 and the right reaction to each differs — wait and
    retry, render the approval, refetch the interrupt.
    """

    def __init__(self, status: int, body: Mapping[str, Any]) -> None:
        self.status = status
        self.code = str(body.get("code", "internal"))
        self.detail = str(body.get("detail", ""))
        self.retryable = bool(body.get("retryable", False))
        super().__init__(f"{self.code}: {self.detail}")


class StreamEndedError(Exception):
    """A stream ended without a terminal frame — the **outcome is unknown** (SS-7, DC-14).

    Deliberately not an error about the run: the run may have completed, may still be going, and in
    the shutdown case very probably did neither. The only correct response is to re-read the thread.
    """

    def __init__(self, url: str, cause: BaseException | None = None) -> None:
        self.url = url
        self.cause = cause
        super().__init__(f"the stream for {url} ended without a terminal frame; outcome unknown")


@dataclass
class PkbClient:
    """One connection to one daemon. Thin by rule: every method is one route."""

    base_url: str = "http://127.0.0.1:8765"
    _client: httpx2.AsyncClient | None = None

    @asynccontextmanager
    async def opened(self) -> AsyncIterator[PkbClient]:
        async with httpx2.AsyncClient(base_url=self.base_url, timeout=JSON_TIMEOUT) as client:
            self._client = client
            try:
                yield self
            finally:
                self._client = None

    @property
    def http(self) -> httpx2.AsyncClient:
        if self._client is None:  # pragma: no cover - a programming error, not a runtime state
            raise RuntimeError("the client is not open; use `async with client.opened()`")
        return self._client

    # -- reads -----------------------------------------------------------------------

    async def agents(self) -> list[dict[str, Any]]:
        """`GET /agents` — the catalog, verbatim. Cached by the caller and joined on ``agent_id``."""
        return list((await self._json("GET", "/agents"))["agents"])

    async def threads(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        """`GET /threads` — already ordered pending-first, and never re-sorted here (RO-6)."""
        params = {"agent_id": agent_id} if agent_id else None
        return list((await self._json("GET", "/threads", params=params))["threads"])

    async def thread(self, thread_id: str) -> dict[str, Any]:
        """`GET /threads/{id}` — **the conversation**, and the re-sync after an unknown outcome."""
        return await self._json("GET", f"/threads/{thread_id}")

    async def health(self) -> dict[str, Any]:
        return await self._json("GET", "/health")

    async def proposals(self) -> list[dict[str, Any]]:
        return list((await self._json("GET", "/proposals"))["proposals"])

    # -- writes ----------------------------------------------------------------------

    async def create_thread(self, agent_id: str, *, title: str | None = None) -> dict[str, Any]:
        body = {"title": title, "origin_channel": "tui"}
        payload = await self._json("POST", f"/agents/{agent_id}/threads", json=body)
        return dict(payload["thread"])

    async def rename(self, thread_id: str, title: str) -> dict[str, Any]:
        payload = await self._json("PATCH", f"/threads/{thread_id}", json={"title": title})
        return dict(payload["thread"])

    async def delete_thread(self, thread_id: str) -> None:
        await self._json("DELETE", f"/threads/{thread_id}")

    async def cancel(self, run_id: str) -> None:
        """`DELETE /runs/{id}`. The id comes from the most recent ``run.started`` — a resume mints a
        new one, and an unknown id is a deliberate 204, so the wrong id cancels nothing and says so
        to nobody (C-21)."""
        await self._json("DELETE", f"/runs/{run_id}")

    async def dismiss_proposal(self, proposal_id: str) -> None:
        await self._json("DELETE", f"/proposals/{proposal_id}")

    # -- streams ---------------------------------------------------------------------

    def run(self, thread_id: str, message: str) -> AsyncIterator[Frame]:
        """`POST /threads/{id}/runs` — a turn, streamed."""
        return self._stream("POST", f"/threads/{thread_id}/runs", json={"message": message})

    def resolve(self, thread_id: str, body: Mapping[str, Any]) -> AsyncIterator[Frame]:
        """`POST /threads/{id}/interrupt` — the decisions and the continuation are **one call**.

        ``thread_id`` is the one inside the request, never the one being streamed (CL-8, LB-16).
        """
        return self._stream("POST", f"/threads/{thread_id}/interrupt", json=dict(body))

    def attach(self, thread_id: str) -> AsyncIterator[Frame]:
        """`GET /threads/{id}/events` — a **live tail**, not the conversation (decision L, DC-18).

        The hub keeps a bounded suffix, so a long run's replay may start mid-turn. The conversation
        comes from ``GET /threads/{id}``; this fills in what is happening now.
        """
        return self._stream("GET", f"/threads/{thread_id}/events")

    async def _stream(
        self, method: str, path: str, *, json: Mapping[str, Any] | None = None
    ) -> AsyncIterator[Frame]:
        saw_terminal = False
        async with self.http.stream(method, path, json=json, timeout=SSE_TIMEOUT) as response:
            if response.status_code == 204:
                return  # RO-17: idle is an answer, and it arrives with no content type at all
            if response.status_code >= 400:
                await response.aread()  # a streaming response holds nothing until it is asked
                raise PkbHttpError(response.status_code, response.json())
            try:
                async for sse in httpx2.EventSource(response):
                    frame = decode_frame(sse.event, sse.data)
                    if frame is None:
                        continue
                    yield frame
                    if frame.terminal:
                        saw_terminal = True
                        return  # never read past the first terminal frame (SS-7, C-16)
            except httpx2.HTTPError as exc:
                raise StreamEndedError(path, exc) from exc
        if not saw_terminal:
            raise StreamEndedError(path)

    async def _json(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self.http.request(method, path, json=json, params=params)
        if response.status_code >= 400:
            raise PkbHttpError(response.status_code, _body(response))
        if response.status_code == 204 or not response.content:
            return {}
        decoded: Any = response.json()
        return dict(decoded)


def _body(response: httpx2.Response) -> Mapping[str, Any]:
    try:
        return dict(response.json())
    except Exception:
        return {"code": "internal", "detail": response.text[:500]}
