"""The Textual client (TU-1 … TU-50).

The shape is arch §6's: a sidebar holding the agent picker and the thread list for the selected
agent, a main pane holding the conversation, and the approval modal on ``interrupt``.

Four structural rules, each of which fails identically in production and is therefore not a coding
preference:

* **The SSE pump is a Textual worker, never a bare ``asyncio.create_task``** (TU-22).
  ``push_screen_wait`` raises ``NoActiveWorker`` outside one — at exactly the moment a human is
  being asked to approve a write, which is the least recoverable place in the application and the
  hardest to reach by accident while clicking around.
* **The pump never awaits a human decision** (TU-23). Approvals go onto an in-app queue drained by a
  *separate* worker that owns the modal. A consumer that stops consuming is a consumer the hub drops
  after 256 frames, while a human takes minutes over a diff — and the drop closes the stream with no
  terminal frame, so the loss would look like an unknown outcome rather than a bug.
* **``exit_on_error=False``** (TU-37). An unhandled exception in a default worker kills the app; a
  dropped daemon connection would take the whole TUI down instead of showing "reconnecting".
* **``exclusive=True, group="stream"``** (TU-38). Switching threads cancels the previous pump, whose
  generator ``finally`` closes the previous httpx2 response deterministically — which is why the
  consumer is written as ``async with`` inside the worker rather than as a detached task.

And the rule that shapes the whole screen: **a thread list is rendered in the order the server
returned it**, pending-approval rows first. That ordering is the design's answer to the scenario the
architecture is built around — somebody coming back hours later, from another channel, to an
approval they left pending — and a client that re-sorts by title or recency buries the exact row
they came back for, invisibly, because the list still looks reasonable.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Final

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static, Tree

from pkb.clients.sse import Frame
from pkb.contracts import (
    ERROR_CODES,
    ApprovalRequest,
    StaleInterruptError,
    ThreadBusyError,
    is_scan_thread,
)
from pkb.tui.client import PkbClient, PkbHttpError, StreamEndedError
from pkb.tui.modal import ApprovalModal
from pkb.tui.state import Entry, RunView, offers_from_children, replay

__all__ = ["PkbApp", "main"]

LIBRARIAN: Final = "librarian"

THREAD_BUSY: Final = ERROR_CODES[ThreadBusyError]
STALE_INTERRUPT: Final = ERROR_CODES[StaleInterruptError]
"""Read from the seam's table, never spelled here (DC-15, decision P).

The table lives in ``pkb.contracts`` precisely so the daemon, the MCP adapter, this client and step
5's Telegram adapter cannot each keep a copy. A literal here is a copy: renaming the code in the
seam would leave these comparisons silently false, and the branch they guard is the one that tells a
human "the previous turn is still finishing" instead of showing them an error.
"""

UNTITLED: Final = "Untitled thread"
"""What a ``title is None`` row shows (TU-15).

``None`` and ``""`` are different states and render differently. Titles land asynchronously after
the first reply, so a large share of rows in a fresh knowledge base are untitled — and a first-line
fallback is exactly the "I grilled a ribeye last weeke…" sidebar the titling ruling rejected.
"""

BUSY_HINT: Final = "the previous turn is still finishing (a few seconds) — reattaching rather than starting a new one"
"""What a ``409 thread_busy`` says (TU-35).

The slot is held until the in-flight model call winds down, measured at three to five seconds, so a
human who closes and reopens the TUI hits this every time. Presented as an error it reads as a
broken daemon, and the correct action — attach — is the one an error toast hides.
"""


class PkbApp(App[None]):
    """The client. Holds no knowledge-base state and never reads the tree (TU-4)."""

    CSS = """
    Screen { layout: horizontal; }
    #sidebar { width: 34; border-right: solid $panel; }
    #agents { height: 50%; }
    #threads { height: 1fr; }
    #main { width: 1fr; }
    #transcript { height: 1fr; }
    .warning { color: $warning; }
    .hint { color: $text-muted; }
    .pending { text-style: bold; }
    #status { height: auto; color: $text-muted; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        # TU-12: the "needs you" view, one keystroke from every screen. When an expert gates inside
        # a fan-out the human does not know which expert was reached — the merged reply that named
        # it scrolled away hours ago — so requiring them to guess before they can find the approval
        # is the failure this binding exists to close.
        ("p", "pending", "Needs you"),
        ("n", "new_thread", "New thread"),
        ("R", "rename", "Rename"),
        ("P", "proposals", "Proposals"),
        ("c", "cancel", "Cancel run"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, client: PkbClient) -> None:
        super().__init__()
        self.client = client
        self.agents: list[dict[str, Any]] = []
        self.threads: list[dict[str, Any]] = []
        self.selected_agent: str | None = None
        self.thread_id: str | None = None
        self.view: RunView | None = None
        self.approvals: asyncio.Queue[ApprovalRequest] = asyncio.Queue()
        self.last_error: str = ""

    # -- layout ------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="sidebar"):
            yield Tree("Agents", id="agents")
            yield ListView(id="threads")
        with Vertical(id="main"):
            yield VerticalScroll(id="transcript")
            yield Static("", id="status", markup=False)
            yield Input(placeholder="Ask, or hand something over…", id="compose")
        yield Footer()

    async def on_mount(self) -> None:
        self.approval_worker()
        await self.refresh_catalog()

    # -- startup -----------------------------------------------------------------------

    async def refresh_catalog(self) -> None:
        """`/health`, then `/agents`, then `/threads` (TU-6).

        ``/health`` reports degradation **in the body with a 200** (AP-18), so a client that only
        checks the status code never learns that a subsystem has been restarting for an hour. It is
        a status line and never a gate: it does not block input and does not decide whether a run
        may start.
        """
        try:
            health = await self.client.health()
            self.agents = await self.client.agents()
            self.threads = await self.client.threads()
        except Exception as exc:
            self._say(
                f"no daemon at {self.client.base_url} — start it with "
                f"`python -m pkb.daemon <kb-root>`  ({type(exc).__name__})"
            )
            return
        if health.get("status") == "degraded":
            self._say(f"daemon degraded: {_degraded(health)}")
        self._fill_agents()
        self._fill_threads()

    def _fill_agents(self) -> None:
        """The catalog **verbatim and in server order** — Librarian first (TU-8).

        Never re-sorted alphabetically, never filtered, and ``model_id`` is never displayed: the
        model is a registry concern and a transport that lets a human pick one deletes RG-21.
        """
        tree = self.query_one("#agents", Tree)
        tree.clear()
        nodes: dict[str, Any] = {}
        for descriptor in self.agents:
            agent_id = str(descriptor["agent_id"])
            title = str(descriptor.get("title") or agent_id)
            parent_id = agent_id.rpartition("/")[0]
            parent = nodes.get(parent_id, tree.root)
            # TU-9: nesting may be *derived* from the '/' in an id, but the id itself is carried
            # verbatim as one string. Reassembling it from node labels is the silent failure RO-2
            # forbids — a request that resolves to a different agent, sharing a checkpoint with the
            # wrong conversation.
            node = parent.add(title, data=agent_id, expand=True)
            nodes[agent_id] = node
        tree.root.expand()

    def _fill_threads(self) -> None:
        """The list, in the server's order (TU-10, TU-11, TU-15). No client-side sort exists."""
        listing = self.query_one("#threads", ListView)
        listing.clear()
        for thread in self.threads:
            if is_scan_thread(str(thread["thread_id"])):
                continue  # maintenance, never a conversation (RT-58) — belt and braces; the server filters
            listing.append(ListItem(Label(thread_label(thread), markup=False)))

    # -- navigation --------------------------------------------------------------------

    @on(Tree.NodeSelected)
    async def _agent_selected(self, event: Tree.NodeSelected[Any]) -> None:
        agent_id = event.node.data
        if not isinstance(agent_id, str):
            return
        self.selected_agent = agent_id
        self.threads = await self.client.threads(agent_id)
        self._fill_threads()

    @on(ListView.Selected)
    async def _thread_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None or index >= len(self.threads):
            return
        await self.open_thread(str(self.threads[index]["thread_id"]))

    async def action_pending(self) -> None:
        """The unfiltered "needs you" view — every pending approval, pending-first (TU-12)."""
        self.selected_agent = None
        self.threads = await self.client.threads()
        self._fill_threads()
        self._say("everything waiting on you, across every expert")

    async def open_thread(self, thread_id: str) -> None:
        """`GET /threads/{id}`, then **always** attach (TU-13, TU-14).

        The detail's ``pending_interrupt`` is the authority, not the list's badge: the column can be
        stale in both directions and the false negative hides an approval from every channel. Opening
        is the repair path — the server rewrites the column on that read — so the client never
        short-circuits it.
        """
        self.thread_id = thread_id
        try:
            detail = await self.client.thread(thread_id)
        except PkbHttpError as exc:
            self._say(f"{exc.code}: {exc.detail}")
            return
        thread = dict(detail["thread"])
        agent_id = str(thread["agent_id"])
        self.view = RunView(thread_id=thread_id, agent_id=agent_id)
        self.view.entries = replay(detail, agent_id)
        self.view.entries.extend(offers_from_children(list(detail.get("children") or [])))
        self.view.terminal = "completed"
        self._render()

        pending = detail.get("pending_interrupt")
        if isinstance(pending, dict):
            # TU-48: reachable with no live run at all. This is the scenario the daemon exists for —
            # the human comes back hours later, from another channel, and one GET has to be enough.
            from pkb.clients.sse import decode_request

            self.approvals.put_nowait(decode_request(pending))
        self.pump(thread_id, attach=True)

    # -- streaming ---------------------------------------------------------------------

    @on(Input.Submitted, "#compose")
    async def _submit(self, event: Input.Submitted) -> None:
        message = event.value.strip()
        if not message or self.thread_id is None:
            return
        event.input.value = ""
        if self.view is not None:
            self.view.entries.append(Entry(kind="human", agent_id="", text=message))
            self._render()
        self.pump(self.thread_id, message=message)

    @work(exclusive=True, group="stream", exit_on_error=False)
    async def pump(
        self, thread_id: str, *, message: str | None = None, attach: bool = False
    ) -> None:
        """Consume one stream. **Never awaits a decision** (TU-22, TU-23, TU-37, TU-38)."""
        view = self.view or RunView(thread_id=thread_id, agent_id=self.selected_agent or LIBRARIAN)
        self.view = view
        if message is not None:
            view.terminal = "running"
        stream = (
            self.client.attach(thread_id) if attach else self.client.run(thread_id, message or "")
        )
        try:
            async for frame in stream:
                request = view.apply(frame)
                if request is not None:
                    # Queued, not awaited: the modal is another worker's problem.
                    self.approvals.put_nowait(request)
                self._render()
        except PkbHttpError as exc:
            if exc.code == THREAD_BUSY:
                self._say(BUSY_HINT)  # TU-35: correct behaviour, said as such
                return
            self._say(f"{exc.code}: {exc.detail}")
        except StreamEndedError:
            # TU-33: the primary shutdown path. Never assume completion or failure — re-read.
            view.ended("unknown")
            self._say("the connection ended before the run did — re-reading the thread")
            await self._resync(thread_id)
        finally:
            self._render()

    @work(group="approvals", exit_on_error=False)
    async def approval_worker(self) -> None:
        """Owns the modal, so the pump never stops consuming (TU-23).

        A separate worker for exactly one reason: ``push_screen_wait`` blocks its own worker until a
        human decides, and a human takes minutes over a diff while a fan-out runs at model pace.
        """
        while True:
            request = await self.approvals.get()
            resolution = await self.push_screen_wait(ApprovalModal(request))
            if resolution is None:
                continue  # TU-47: "later" sends nothing; the interrupt stays parked
            try:
                # TU-49: to the request's OWN thread. In a fan-out the gate parks on the expert's
                # derived thread, and posting to the Librarian's is a 409 for a valid approval.
                async for frame in self.client.resolve(resolution.thread_id, resolution.body()):
                    self._absorb(frame)
            except PkbHttpError as exc:
                if exc.code == STALE_INTERRUPT:
                    # Another channel answered it. Do not retry — retrying either spins or applies
                    # answers the human gave to a different write.
                    self._say("another channel answered that approval")
                    await self._resync(resolution.thread_id)
                else:
                    self._say(f"{exc.code}: {exc.detail}")
            except StreamEndedError:
                await self._resync(resolution.thread_id)

    def _absorb(self, frame: Frame) -> None:
        if self.view is not None:
            request = self.view.apply(frame)
            if request is not None:
                self.approvals.put_nowait(request)
            self._render()

    async def _resync(self, thread_id: str) -> None:
        """SS-7's re-sync: the server is the authority on an outcome the stream did not report."""
        try:
            self.threads = await self.client.threads(self.selected_agent)
            self._fill_threads()
        except Exception:
            return

    async def action_cancel(self) -> None:
        """`DELETE /runs/{id}` from the most recent ``run.started`` (TU-36).

        Cancelling and closing a view are different intents and are different affordances: closing
        detaches and the run continues, which is D2's promise. A wrong run id is a silent no-op — the
        route answers 204 for an unknown one — which is why the target comes from the *most recent*
        ``run.started`` rather than a cached one.
        """
        if self.view is None or not self.view.run_id:
            return
        await self.client.cancel(self.view.run_id)

    async def action_rename(self) -> None:
        """Offered on a ``kind == "user"`` thread only (TU-16).

        A derived thread's title states where it came from and the server refuses a ``PATCH`` on
        one. Offering a rename the server refuses is a dead control, and a dead control teaches the
        human to distrust every other one.
        """
        thread = self._current_row()
        if thread is None:
            return
        if thread.get("kind") == "routed":
            self._say("a routed thread's name says where it came from, and is not editable")
            return
        composer = self.query_one("#compose", Input)
        composer.value = f"/rename {thread.get('title') or ''}"
        composer.focus()

    async def action_proposals(self) -> None:
        """Writes that needed a human and could not get one (TU-20, TU-21).

        **Not** "what agents wanted to write". The gate table is the same for every channel, and it
        leaves plain note writes and first-write reference files ungated — so an MCP or scan-
        originated note lands with no human, no proposal and no entry here. A false belief about
        coverage is worse than no view at all, so the copy says what this list actually is and
        points at the thread list for the rest.

        Dismiss only: applying a proposal needs a Layer 2 entry point that does not exist, and a
        greyed-out "apply" would say "not now" when the truth is "there is nothing to resume".
        """
        try:
            proposals = await self.client.proposals()
        except PkbHttpError as exc:
            self._say(f"{exc.code}: {exc.detail}")
            return
        pane = self.query_one("#transcript", VerticalScroll)
        pane.remove_children()
        pane.mount(
            Static(
                "Writes that needed your approval and could not get it.\n"
                "Applying one is not available yet — dismiss to clear it. Everything an agent filed "
                "without needing you is in the thread list.",
                markup=False,
                classes="hint",
            )
        )
        for proposal in proposals:
            pane.mount(Static(proposal_line(proposal), markup=False))
        if not proposals:
            pane.mount(Static("nothing is waiting on you here", markup=False, classes="hint"))
        self._say(f"{len(proposals)} proposal(s)")

    def _current_row(self) -> dict[str, Any] | None:
        listing = self.query_one("#threads", ListView)
        index = listing.index
        if index is None or index >= len(self.threads):
            return None
        return self.threads[index]

    async def action_new_thread(self) -> None:
        if self.selected_agent is None:
            self._say("pick an agent first")
            return
        thread = await self.client.create_thread(self.selected_agent)
        self.threads = await self.client.threads(self.selected_agent)
        self._fill_threads()
        await self.open_thread(str(thread["thread_id"]))

    # -- rendering ---------------------------------------------------------------------

    def _render(self) -> None:
        if self.view is None:
            return
        pane = self.query_one("#transcript", VerticalScroll)
        pane.remove_children()
        for entry in self.view.entries:
            pane.mount(Static(_line(entry), markup=False))
        note = self.view.waiting_note or _terminal_note(self.view)
        if note:
            self._say(note)

    def _say(self, text: str) -> None:
        self.last_error = text
        self.query_one("#status", Static).update(text)


def thread_label(thread: dict[str, Any]) -> str:
    """One row. Badged when pending, marked when routed, placeholder when untitled."""
    title = thread.get("title")
    name = UNTITLED if title is None else str(title)
    badge = "● " if thread.get("pending_interrupt_id") else "  "
    kind = " (routed)" if thread.get("kind") == "routed" else ""
    return f"{badge}{name}{kind}"


def proposal_line(proposal: dict[str, Any]) -> str:
    """One proposal row. A ``scan:`` thread is labelled and is **not** a link (TU-20).

    Scan threads are filtered out of every list by rule (RT-58), so a navigation affordance on one
    is a dead end — and a background maintenance write is not a conversation the human can open.
    """
    action = dict(proposal.get("action") or {})
    path = str(action.get("args", {}).get("file_path", "")) or "(no path)"
    origin = (
        " · background maintenance" if is_scan_thread(str(proposal.get("thread_id", ""))) else ""
    )
    return f"  {action.get('reason', '?')}  {path}{origin}"


def _line(entry: Entry) -> str:
    if entry.kind == "human":
        return f"you  {entry.text}"
    if entry.kind == "offer":
        return f"  → continue with {entry.agent_id}"
    if entry.kind == "tool":
        return f"  {'✗' if entry.error else '·'} {entry.text}"
    if entry.kind == "note":
        return f"  ! {entry.text}"
    return f"{entry.agent_id or 'assistant'}  {entry.text}"


def _terminal_note(view: RunView) -> str:
    """TU-31: four states and no fall-through; TU-32: retry is a **button**, never automatic."""
    if view.terminal == "interrupted":
        return "waiting on your decision"
    if view.terminal == "cancelled":
        return "cancelled"
    if view.terminal == "error":
        return "the run failed — press enter to try again" if view.retryable else "the run failed"
    if view.terminal == "unknown":
        return "the connection ended before the run did — outcome unknown"
    return ""


def _degraded(health: dict[str, Any]) -> str:
    parts = []
    for name in ("scan_worker", "telegram"):
        state = dict(health.get(name) or {})
        if state.get("state") not in (None, "running", "disabled"):
            parts.append(f"{name}={state.get('state')}")
    return ", ".join(parts) or "see /health"


def main(argv: list[str] | None = None) -> int:
    """``python -m pkb.tui [--daemon URL]``."""
    import argparse

    parser = argparse.ArgumentParser(prog="pkb-tui", description="The PKB terminal client.")
    parser.add_argument("--daemon", default="http://127.0.0.1:8765")
    args = parser.parse_args(argv)

    client = PkbClient(base_url=args.daemon)

    async def run() -> None:
        async with client.opened():
            await PkbApp(client).run_async()

    asyncio.run(run())
    return 0
