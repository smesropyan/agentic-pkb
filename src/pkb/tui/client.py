"""The daemon, over HTTP — the only module in Layer 4 that knows a socket exists (DC-9 … DC-17).

Everything the TUI does goes through here, and everything here goes through the route table. The
client holds no policy: it does not decide what a frame means, it does not retry, and it never
invents a request the route table does not have.

**Compile-keeper note (Task 5 of the sessions plan).** The daemon's routes are session-shaped now
(``DESIGN.md`` §2), and this module is repointed at ``/sessions`` — but ``pkb.tui`` itself is not
redesigned here; that is Phase 5's. So the methods below keep their thread-era **names**
(``threads``, ``thread``, ``create_thread``, ``rename``) and reshape a ``/sessions`` payload into
the thread-shaped dict :mod:`pkb.tui.state` and :mod:`pkb.tui.app` already expect
(:func:`_session_as_thread`), rather than renaming every call site across two modules for a shape
Phase 5 will redraw anyway. Three call sites have **no successor** and are gone outright:
``delete_thread`` ("nothing deletes a session," `DESIGN.md` §2.7), ``proposals``/
``dismiss_proposal`` (the parked-proposal surface retires with the gate table), and ``resolve``
(the interrupt-resume route is deleted; the operator's instruction is the approval, §2.10). A
session's own history has no read-back route yet (Task 8 wires the running record), so
:meth:`thread` returns an **empty** message list rather than inventing one.

Four things are measured rather than chosen, and none of them changed with the rename:

* **Two timeout budgets, not one** (DC-9, DC-10). httpx2's default read timeout is 5 s and that
  kills every real turn: the gap between ``run.started`` and the first token is a whole model call —
  ~16 s on the cloud default, 284 s on the local fallback. Measured against a live socket, an 8 s
  gap raised ``ReadTimeout after 5.00s`` having received only ``run.started``, while the daemon
  carried on and filed the note (AP-7) — so the human sees a network error over a run that worked.
  The streaming budget is set above ``PING_SECONDS`` instead of disabled, because the server's
  ``: ping`` comment frames **do** reset httpx2's read timer even though the decoder never surfaces
  them: three missed heartbeats is a real hang, and ``read=None`` would wait for one forever.
* **204 is checked before an EventSource is constructed** (DC-11). ``GET /sessions/{id}/events``
  answers 204 with no content type when nothing is running — the *normal* case, since most sessions a
  human opens are not mid-turn — and handing that to a decoder raises an SSE protocol error on every
  ordinary session open.
* **An error body is read explicitly** (DC-12). A streaming response holds nothing until it is
  read, so a 409 arriving before the stream opens has an empty body unless the client asks for it.
* **A stream that ends without a terminal frame means *outcome unknown*** (DC-14, SS-7). Both
  endings are real and they look different: a clean close ends the iteration with **no exception at
  all**, and an aborted socket raises. Neither is a result. The client re-syncs with
  ``GET /sessions/{id}`` and renders neither success nor failure — and never re-POSTs the run, which
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
        """`GET /sessions?state=open`, reshaped to the thread dict :mod:`pkb.tui` still expects.

        Only **open** sessions — the closest analogue of "a thread you can still turn to"; a closed
        or ended one is done, per the state machine (S-20, S-22), and Phase 5 owns however a
        history view eventually renders those. Never re-sorted here (mirrors RO-6's discipline),
        though a session list carries no pending-approval badge to sort by any more (S-38: nothing
        parks).
        """
        params: dict[str, str] = {"state": "open"}
        if agent_id:
            params["agent_id"] = agent_id
        sessions = (await self._json("GET", "/sessions", params=params))["sessions"]
        return [_session_as_thread(session) for session in sessions]

    async def thread(self, thread_id: str) -> dict[str, Any]:
        """`GET /sessions/{id}` — reshaped, and the re-sync after an unknown outcome.

        ``messages`` is always empty: a session's running record has no read-back route yet (Task 8
        wires the write side only), so history before this process attached is not visible. Live
        turns from here on still render — ``open_thread`` always follows this with an attach.
        """
        payload = await self._json("GET", f"/sessions/{thread_id}")
        return {
            "thread": _session_as_thread(payload["session"]),
            "messages": [],
            "pending_interrupt": None,
            "children": [],
        }

    async def health(self) -> dict[str, Any]:
        return await self._json("GET", "/health")

    # -- writes ----------------------------------------------------------------------

    async def create_thread(self, agent_id: str, *, title: str | None = None) -> dict[str, Any]:
        """`POST /agents/{id}/sessions` — ``title`` becomes the objective (S-5): harness code names
        the session from it immediately, rather than the thread era's async titling call."""
        payload = await self._json(
            "POST", f"/agents/{agent_id}/sessions", json={"objective": title}
        )
        return _session_as_thread(payload["session"])

    async def rename(self, thread_id: str, title: str) -> dict[str, Any]:
        """`POST /sessions/{id}/name` (S-16)."""
        payload = await self._json("POST", f"/sessions/{thread_id}/name", json={"name": title})
        return _session_as_thread(payload["session"])

    async def cancel(self, run_id: str) -> None:
        """`DELETE /runs/{id}`. The id comes from the most recent ``run.started`` — a resume mints a
        new one, and an unknown id is a deliberate 204, so the wrong id cancels nothing and says so
        to nobody (C-21)."""
        await self._json("DELETE", f"/runs/{run_id}")

    # -- streams ---------------------------------------------------------------------

    def run(self, thread_id: str, message: str) -> AsyncIterator[Frame]:
        """`POST /sessions/{id}/runs` — a turn, streamed."""
        return self._stream("POST", f"/sessions/{thread_id}/runs", json={"message": message})

    def attach(self, thread_id: str) -> AsyncIterator[Frame]:
        """`GET /sessions/{id}/events` — a **live tail**, not the conversation (decision L, DC-18).

        The hub keeps a bounded suffix, so a long run's replay may start mid-turn. The conversation
        comes from ``GET /sessions/{id}``; this fills in what is happening now.
        """
        return self._stream("GET", f"/sessions/{thread_id}/events")

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


def _session_as_thread(session: Mapping[str, Any]) -> dict[str, Any]:
    """A ``/sessions`` payload, reshaped into the thread dict :mod:`pkb.tui` still expects.

    Interim only (module docstring) — Phase 5 redesigns the TUI directly against sessions and this
    function goes away with the rename it stands in for. ``title`` comes from ``name`` (a session's
    own display name, S-4), never ``objective``, because a title is what a sidebar row shows and a
    name is exactly that. ``kind``/``parent_thread_id`` have no session analogue — a session that
    crosses topics re-opens fresh rather than forking (S-12) — and are always ``"user"``/``None``.
    ``pending_interrupt_id`` is always ``None``: nothing parks (S-38). ``origin_channel`` is
    reported as ``"tui"`` unconditionally; it is provenance for display only, never a permission
    (RO-22), and a session carries no channel field yet (Task 7 attaches channels separately).
    """
    return {
        "thread_id": session["session_id"],
        "agent_id": session["agent_id"],
        "title": session["name"],
        "kind": "user",
        "parent_thread_id": None,
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "origin_channel": "tui",
        "pending_interrupt_id": None,
    }
