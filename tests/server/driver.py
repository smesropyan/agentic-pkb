"""A raw ASGI driver — the only in-process way to test streaming (P-4, §6.2).

``TestClient`` cannot: ``_TestClientTransport.handle_request`` does
``raw_kwargs["stream"] = httpx.ByteStream(raw_kwargs["stream"].read())``, so 50 frames sent over
0.5 s arrive as **one chunk**, and breaking out of the iteration leaves the server generator running
to completion — no disconnect ever reaches the app. ``httpx2.ASGITransport`` buffers identically.
Both are still the right tool for frame *content*: split the one buffered body on ``\\r\\n\\r\\n``.

What only this driver can assert:

* frames arrive **over time** (timestamps, not just contents);
* a client hangup runs the generator's ``finally`` — which is how AP-6/AP-7's detach behaviour is
  pinned without spinning uvicorn: a hangup leaves the run task alive to its terminal event and the
  generator merely unsubscribes.

``spec_version`` defaults to ``"2.3"``, which is what uvicorn 0.52.1 declares on h11, httptools and
zttp. ``"2.4"`` exists for SS-1's divergence test, and ``send_raises_after_disconnect`` models
uvicorn's real 2.4 contract, where ``send`` raises ``OSError`` on a dead socket.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Captured", "drive"]


@dataclass
class Captured:
    """What one driven request produced."""

    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    chunks: list[tuple[float, bytes]] = field(default_factory=list)
    error: BaseException | None = None

    @property
    def body(self) -> bytes:
        return b"".join(chunk for _, chunk in self.chunks)

    @property
    def frames(self) -> list[str]:
        """SSE frames, split on the real CRLF blank line."""
        return [f for f in self.body.decode().split("\r\n\r\n") if f.strip()]

    def events(self) -> list[tuple[str, str]]:
        """``(event, data)`` per frame, skipping comment frames such as the ping keep-alive."""
        out: list[tuple[str, str]] = []
        for frame in self.frames:
            lines = frame.splitlines()
            name = next((x.split(": ", 1)[1] for x in lines if x.startswith("event: ")), "")
            data = next((x.split(": ", 1)[1] for x in lines if x.startswith("data: ")), "")
            if name or data:
                out.append((name, data))
        return out

    @property
    def spans(self) -> list[float]:
        """Arrival time of each chunk, so a test can assert incrementality rather than assume it."""
        return [at for at, _ in self.chunks]


async def drive(
    app: Any,
    path: str = "/",
    *,
    method: str = "GET",
    body: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
    spec_version: str = "2.3",
    disconnect_after: int | None = None,
    send_raises_after_disconnect: bool = False,
    timeout: float = 3.0,
) -> Captured:
    """Run one request against an ASGI app and return what came back, with timings."""
    captured = Captured()
    started = time.monotonic()
    disconnected = asyncio.Event()
    sent_request = False

    async def receive() -> dict[str, Any]:
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": body, "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            captured.status = message["status"]
            captured.headers = {
                k.decode().lower(): v.decode() for k, v in message.get("headers", [])
            }
        elif message["type"] == "http.response.body":
            if disconnected.is_set() and send_raises_after_disconnect:
                raise OSError("client disconnected")
            chunk = message.get("body", b"")
            if chunk:
                captured.chunks.append((round(time.monotonic() - started, 3), chunk))
                if disconnect_after is not None and len(captured.chunks) >= disconnect_after:
                    disconnected.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers or [(b"host", b"127.0.0.1:8000")],
        "client": ("127.0.0.1", 51234),
        "server": ("127.0.0.1", 8000),
        "state": {},
    }

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(task, timeout)
    except TimeoutError:
        task.cancel()
        captured.error = TimeoutError(f"the app did not finish within {timeout}s")
    except BaseException as exc:
        captured.error = exc
    return captured
