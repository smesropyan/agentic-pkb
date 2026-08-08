"""What every human channel needs and neither owns — the shared client layer.

Two modules, and the split between them is structural rather than tidy:

* :mod:`pkb.clients.approval` turns an ``interrupt`` into a :class:`~pkb.contracts.Decision` list.
  The TUI and (in step 5) the Telegram adapter must do that **identically** — same action parsing,
  same answer to "which decisions is this action allowed" — because they are two views of one
  approval and a human may answer from either (arch §6, D3). Only the *rendering* differs: a diff
  modal in one, inline keyboard buttons in the other.
* :mod:`pkb.clients.sse` turns a wire frame back into the seam's own dataclasses. It is the exact
  inverse of ``pkb.server.sse`` and it imports the **same** name table rather than keeping a copy
  (SS-3), which is what makes a tenth event kind an ImportError at startup instead of a frame that
  silently vanishes.

**This package is transport-free and UI-free** — no ``httpx``, no ``httpx2``, no ``textual`` — and a
forbidden-import contract enforces it (decision I). The reason is step 5: the Telegram adapter runs
*inside the daemon* and calls :class:`~pkb.service.PkbService` directly, with no HTTP round trip
(D9), and MC-7's built test asserts it pulls no HTTP client. If any module here imported one, that
assertion would degrade from a structural fact into a discipline about which submodule you happen to
import. The httpx2 wrapper that actually iterates a response lives in ``pkb.tui.client``, above.

It is also below ``pkb.agents`` in the layer list, which makes "the approval helper never touches
the harness" a *layers* fact as well as a forbidden-contract one, and catches
``pkb.clients -> pkb.tui`` for free.
"""

from __future__ import annotations

__all__: list[str] = []
