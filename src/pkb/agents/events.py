"""Graph stream chunks → :class:`~pkb.contracts.AgentEvent` (RT-43, RT-41, RT-44).

Layer 2 owns event normalization, not Layer 3. Everything yielded here is a frozen dataclass of
primitives from :mod:`pkb.contracts`; a transport encodes it and never sees a LangChain message, an
``Interrupt`` or a ``Command`` (I2).

**Why not ``astream_events(version="v3")``**, which the architecture doc names: on langgraph 1.2.10
that is a *different protocol*. Calling it returns a **coroutine** that must be awaited before it
can be iterated, and it yields JSON-RPC-style ``{"type": "event", "method": …, "params": …}``
envelopes with no ``event`` key — not the ``on_chat_model_stream`` shape arch §5 assumes (D-12).
``version="v2"`` gives the familiar names but is noisier and slower. So the sanctioned driver is::

    graph.astream(payload, config, stream_mode=["updates", "messages"], subgraphs=True)

**``subgraphs=True`` is load-bearing**, not a nicety. Without it a delegated expert runs entirely
invisibly: its messages, its tool calls and its interrupt all live under the namespace
``('tools:<uuid>',)`` and never reach the stream. Arch §5's ``subagent.*`` events and delegated token
streaming are impossible without it. Do not "simplify" it away.

The chunk shape that combination produces is ``(namespace, mode, payload)``:

* ``mode == "messages"`` → ``(message, metadata)``. This is the *token* channel and the source of
  :class:`~pkb.contracts.MessageDelta`. A streaming provider emits ``AIMessageChunk`` fragments here.
* ``mode == "updates"`` → ``{node_name: state_update}``. This is the *fact* channel and the source of
  everything else, because a completed node's messages are whole: partial tool-call fragments on the
  token channel cannot be turned into a reliable :class:`~pkb.contracts.ToolStart`.

Two behaviours exist to work around harness facts and will look wrong to a reader who has not seen
them fire:

* A delegated interrupt is emitted **twice** — once under ``('tools:<uuid>',)`` and once under
  ``()`` — with the same ``Interrupt.id``. It is one approval, so it is deduped by id (RT-41).
* A subagent event must name the **delegate**, not the parent (RT-44). The namespace string carries
  no agent name, so the delegate's id comes from the ``task`` tool call's ``subagent_type``
  argument, which is exactly the registered ``CompiledSubAgent.name`` (RG-9).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Final

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.types import Interrupt

from pkb.agents.approval import ReasonResolver, normalize_interrupt
from pkb.contracts import (
    AgentEvent,
    InterruptEvent,
    MessageComplete,
    MessageDelta,
    RunEnd,
    RunError,
    SubagentEnd,
    SubagentStart,
    ToolEnd,
    ToolStart,
)

__all__ = [
    "STREAM_MODES",
    "SUBGRAPHS",
    "EventNormalizer",
    "is_retryable",
    "stream_events",
]

STREAM_MODES: Final[tuple[str, ...]] = ("updates", "messages")
"""The two stream modes Layer 2 consumes. See the module docstring for why not ``astream_events``."""

SUBGRAPHS: Final = True
"""Named so the requirement is greppable: without it, delegated work is invisible (RT-43)."""

_TASK_TOOL: Final = "task"
"""deepagents' delegation tool. Its ``subagent_type`` arg is the delegate's agent id (RT-44)."""

_INTERRUPT_KEY: Final = "__interrupt__"

_SUMMARY_LIMIT: Final = 240
_ARG_VALUE_LIMIT: Final = 80
_PATH_ARG_KEYS: Final = ("file_path", "path", "topic_path", "pattern")
_BULK_ARG_KEYS: Final = frozenset({"content", "old_string", "new_string"})

_RETRYABLE_MARKERS: Final = (
    "ratelimit",
    "rate_limit",
    "rate limit",
    "overloaded",
    "timeout",
    "timed out",
    "connection",
    "unavailable",
    "internal server",
    "temporarily",
    "429",
    "500",
    "502",
    "503",
    "529",
)


def is_retryable(exc: BaseException) -> bool:
    """Whether a run failure looks transient, for :attr:`RunError.retryable` (RT-47).

    Deliberately a string heuristic over the exception's type name and message rather than an
    ``isinstance`` table: Layer 2 must not import a provider SDK to classify its errors, and the
    flag is advice to a client's retry button, not a control-flow decision.
    """
    if isinstance(exc, TimeoutError | ConnectionError):
        return True
    haystack = f"{type(exc).__name__} {exc}".lower()
    return any(marker in haystack for marker in _RETRYABLE_MARKERS)


class EventNormalizer:
    """Stateful translator from raw graph stream chunks to :class:`AgentEvent`s (RT-43).

    Stateful because three of the nine event kinds cannot be decided from one chunk: interrupt
    deduplication spans namespaces (RT-41), a subagent's identity is learned from the parent's
    ``task`` call and reused for every chunk in its namespace (RT-44), and :class:`RunEnd`'s final
    text is the last root-namespace assistant message of the whole run.

    One instance per run: **feed** every chunk, then :meth:`drain`, then :meth:`run_end` or
    :meth:`run_error`. :func:`stream_events` does all three; a caller driving ``astream`` itself must
    not skip the drain, or the events held back by RT-44's attribution rule are lost.
    """

    def __init__(
        self,
        *,
        run_id: str,
        agent_id: str,
        thread_id: str,
        reason_for: ReasonResolver | None = None,
    ) -> None:
        """Args mirror :func:`stream_events`; ``agent_id``/``thread_id`` identify the *run*'s agent.

        An approval raised inside a delegated expert is resolved on the parent's thread with the
        parent's id (LB-10), so those two values are stamped on every :class:`InterruptEvent`
        regardless of which namespace raised it.
        """
        self.run_id = run_id
        self.agent_id = agent_id
        self.thread_id = thread_id
        self._reason_for = reason_for
        self._namespace_agents: dict[tuple[str, ...], str] = {}
        self._delegates: dict[str, str] = {}
        self._open_delegates: dict[str, str] = {}
        self._held: dict[tuple[str, ...], list[BaseMessage]] = {}
        self._seen_interrupts: set[str] = set()
        self._seen_messages: set[tuple[str, str]] = set()
        self._final_text = ""

    @property
    def final_text(self) -> str:
        """The last assistant text produced in the **root** namespace — the run's answer.

        A delegated expert's closing message is its report to the parent, not the human's answer, so
        subgraph namespaces never set this.
        """
        return self._final_text

    def feed(self, chunk: object) -> list[AgentEvent]:
        """Normalize one ``(namespace, mode, payload)`` chunk. Unrecognized chunks yield nothing."""
        split = _split_chunk(chunk)
        if split is None:
            return []
        namespace, mode, payload = split
        if mode == "messages":
            return self._feed_messages(namespace, payload)
        if mode == "updates":
            return self._feed_updates(namespace, payload)
        return []

    def drain(self) -> list[AgentEvent]:
        """Release anything still held for an unidentified subgraph, before the terminal event.

        A namespace is normally identified within a chunk or two of appearing (see
        :meth:`_feed_updates`). A delegate that ends without ever reaching its model — it errored
        inside the tool node, say — never gets identified, so its held messages are emitted here
        attributed to the run's own agent rather than dropped.
        """
        events: list[AgentEvent] = []
        for namespace in list(self._held):
            events.extend(self._release(namespace))
        return events

    def run_end(self) -> RunEnd:
        """The terminal event of a run that completed."""
        return RunEnd(run_id=self.run_id, final_text=self._final_text)

    def run_error(self, exc: BaseException) -> RunError:
        """The terminal event of a run that raised (RT-47).

        The thread stays resumable: the checkpoint written before the failure is intact, and Layer 2
        never marks the thread finished.
        """
        return RunError(
            run_id=self.run_id,
            message=str(exc) or type(exc).__name__,
            retryable=is_retryable(exc),
        )

    # -- channels ----------------------------------------------------------------------

    def _feed_messages(self, namespace: tuple[str, ...], payload: object) -> list[AgentEvent]:
        """The token channel: assistant text fragments, plus the identity metadata everything needs.

        Only this channel carries per-chunk metadata, so it is where both attribution facts are
        learned: which agent owns a subgraph namespace, and which namespace a parent's ``task`` call
        ran in. ``updates`` chunks carry neither.
        """
        if not isinstance(payload, tuple) or len(payload) != 2:
            return []
        message, metadata = payload
        self._learn_agent(namespace, metadata)
        events = self._release(namespace)
        if isinstance(message, ToolMessage) and message.name == _TASK_TOOL:
            self._adopt_delegate(message.tool_call_id, metadata)
        if isinstance(message, AIMessage) and message.text:
            events.append(
                MessageDelta(
                    run_id=self.run_id, agent_id=self._agent_for(namespace), text=message.text
                )
            )
        return events

    def _feed_updates(self, namespace: tuple[str, ...], payload: object) -> list[AgentEvent]:
        """The fact channel: completed messages, tool results and interrupts.

        Messages from a subgraph whose agent is not yet known are **held**, not attributed to the
        parent. A resumed delegated approval opens exactly this way: the first chunk in the
        delegate's namespace is ``HumanInTheLoopMiddleware.after_model`` re-emitting the approved
        ``AIMessage``, and no ``task`` call is replayed on a resume, so nothing has named the
        delegate yet. Emitting it immediately would label the expert's write as the Librarian's,
        which is precisely the confusion RT-44 exists to prevent. The hold lasts until the
        namespace's next ``messages`` chunk — one chunk later in practice.
        """
        if not isinstance(payload, Mapping):
            return []
        interrupts: list[AgentEvent] = []
        messages: list[BaseMessage] = []
        for node, update in payload.items():
            if node == _INTERRUPT_KEY:
                interrupts.extend(self._interrupt_events(update))
                continue
            if isinstance(update, Mapping):
                messages.extend(_messages_of(update))
        if messages and namespace and namespace not in self._namespace_agents:
            self._held.setdefault(namespace, []).extend(messages)
            return interrupts
        scoped: list[AgentEvent] = []
        for message in messages:
            scoped.extend(self._message_events(namespace, message))
        return interrupts + scoped

    def _release(self, namespace: tuple[str, ...]) -> list[AgentEvent]:
        """Turn a namespace's held messages into events, now that it can be attributed."""
        held = self._held.pop(namespace, None)
        if not held:
            return []
        events: list[AgentEvent] = []
        for message in held:
            events.extend(self._message_events(namespace, message))
        return events

    # -- per-message -------------------------------------------------------------------

    def _message_events(self, namespace: tuple[str, ...], message: BaseMessage) -> list[AgentEvent]:
        if not self._first_sighting(message):
            return []
        if isinstance(message, AIMessage):
            return self._ai_events(namespace, message)
        if isinstance(message, ToolMessage):
            return self._tool_result_events(namespace, message)
        return []

    def _ai_events(self, namespace: tuple[str, ...], message: AIMessage) -> list[AgentEvent]:
        agent_id = self._agent_for(namespace)
        events: list[AgentEvent] = []
        text = message.text
        if text:
            events.append(MessageComplete(run_id=self.run_id, agent_id=agent_id, text=text))
            if not namespace:
                self._final_text = text
        for tool_call in message.tool_calls:
            name = str(tool_call.get("name") or "")
            args = tool_call.get("args") or {}
            if name == _TASK_TOOL:
                events.append(self._open_delegation(tool_call.get("id"), args))
            else:
                events.append(
                    ToolStart(
                        run_id=self.run_id,
                        agent_id=agent_id,
                        tool=name,
                        summary=_summarize_args(args),
                    )
                )
        return events

    def _tool_result_events(
        self, namespace: tuple[str, ...], message: ToolMessage
    ) -> list[AgentEvent]:
        failed = message.status == "error"
        delegate = self._delegates.get(message.tool_call_id)
        if delegate is not None or message.name == _TASK_TOOL:
            # The `task` ToolMessage is the delegation's end, not an ordinary tool result: the TUI
            # shows "← Cooking expert returned", and a ToolEnd(tool="task") beside it would be the
            # same fact twice (RT-44).
            self._open_delegates.pop(message.tool_call_id, None)
            return [
                SubagentEnd(
                    run_id=self.run_id,
                    agent_id=delegate or self._agent_for(namespace),
                    status="error" if failed else "success",
                )
            ]
        return [
            ToolEnd(
                run_id=self.run_id,
                agent_id=self._agent_for(namespace),
                tool=str(message.name or ""),
                summary=_truncate(message.text, _SUMMARY_LIMIT),
                error=failed,
            )
        ]

    def _open_delegation(self, tool_call_id: object, args: Mapping[str, Any]) -> SubagentStart:
        """Record and announce a delegation, named by ``subagent_type`` (RT-44).

        The registry registers each expert as ``CompiledSubAgent(name=<agent id>)`` and the model
        must spell that same string back to reach it (RG-9), so this value *is* the delegate's agent
        id — which is what lets the TUI show "→ routing to Cooking expert" instead of a silent pause.
        """
        delegate = str(args.get("subagent_type") or "")
        if isinstance(tool_call_id, str):
            self._delegates[tool_call_id] = delegate
            self._open_delegates[tool_call_id] = delegate
        return SubagentStart(run_id=self.run_id, agent_id=delegate)

    # -- interrupts --------------------------------------------------------------------

    def _interrupt_events(self, update: object) -> list[AgentEvent]:
        """One :class:`InterruptEvent` per *distinct* interrupt id (RT-41).

        A delegated approval arrives twice, from the subgraph namespace and again from the root, and
        both carry the same id. Deduping by id is the whole reason this normalizer is stateful; drop
        it and the human is asked to decide one write twice.
        """
        events: list[AgentEvent] = []
        for item in _as_list(update):
            if not isinstance(item, Interrupt) or item.id in self._seen_interrupts:
                continue
            self._seen_interrupts.add(item.id)
            events.append(
                InterruptEvent(
                    run_id=self.run_id,
                    request=normalize_interrupt(
                        item,
                        agent_id=self.agent_id,
                        thread_id=self.thread_id,
                        reason_for=self._reason_for,
                    ),
                )
            )
        return events

    # -- identity ----------------------------------------------------------------------

    def _learn_agent(self, namespace: tuple[str, ...], metadata: object) -> None:
        """Bind a subgraph namespace to its agent id from ``lc_agent_name``.

        deepagents stamps every ``CompiledSubAgent`` runnable with ``metadata.lc_agent_name = name``
        (`subagents.py:437-441`), so the token channel labels a delegate's namespace precisely. The
        ``updates`` channel carries no metadata at all, which is why the binding is cached here and
        reused for both channels.
        """
        if not namespace or not isinstance(metadata, Mapping):
            return
        name = metadata.get("lc_agent_name")
        if isinstance(name, str) and name:
            self._namespace_agents[namespace] = name

    def _adopt_delegate(self, tool_call_id: str, metadata: object) -> None:
        """Recover a delegation's name when this run did not see the ``task`` call (RT-44).

        On a resumed run the ``task`` call was made in the *previous* run, so ``subagent_type`` is
        nowhere in this stream — yet the delegation's returning ToolMessage still arrives here and
        must produce a correctly named :class:`SubagentEnd`. Its metadata names the child
        checkpoint namespace (``tools:<uuid>``, plus a ``|``-joined suffix for deeper nesting), which
        the delegate's own chunks have already bound to an agent id.
        """
        if not tool_call_id or tool_call_id in self._delegates or not isinstance(metadata, Mapping):
            return
        raw = metadata.get("langgraph_checkpoint_ns")
        if not isinstance(raw, str) or not raw:
            return
        learned = self._namespace_agents.get((raw.split("|", 1)[0],))
        if learned:
            self._delegates[tool_call_id] = learned

    def _agent_for(self, namespace: tuple[str, ...]) -> str:
        if not namespace:
            return self.agent_id
        learned = self._namespace_agents.get(namespace)
        if learned:
            return learned
        if len(self._open_delegates) == 1:
            return next(iter(self._open_delegates.values()))
        return self.agent_id

    def _first_sighting(self, message: BaseMessage) -> bool:
        """Suppress a repeat of a message already turned into events in this run.

        ``HumanInTheLoopMiddleware.after_model`` re-emits the whole ``AIMessage`` when it rewrites
        the approved tool calls, so a resumed turn can otherwise announce the same tool twice.
        """
        if not message.id:
            return True
        key = (type(message).__name__, message.id)
        if key in self._seen_messages:
            return False
        self._seen_messages.add(key)
        return True


async def stream_events(
    graph: Any,
    payload: Any,
    config: Any,
    *,
    run_id: str,
    agent_id: str,
    thread_id: str,
    reason_for: ReasonResolver | None = None,
) -> AsyncIterator[AgentEvent]:
    """Drive one graph execution and yield normalized events (RT-43).

    The stream is always ``stream_mode=["updates", "messages"], subgraphs=True`` — see the module
    docstring for why both parts are load-bearing.

    A failure yields exactly one :class:`RunError` and then stops: the exception is **not** re-raised,
    because RT-47 says a provider error surfaces as a normalized event rather than as a second thing
    every transport must catch. It is also not swallowed into a normal completion — no
    :class:`RunEnd` is emitted, so a caller can tell the two apart by the terminal event. The
    runtime's flush guard (MW-26) is a ``try/finally`` around this loop and runs on either ending;
    it recovers the touched paths from the checkpoint, not from the exception.

    Cancellation propagates untouched: :meth:`~asyncio.Task.cancel` is how ``cancel(run_id)`` is
    implemented (RT-46), so turning it into a ``RunError`` would make a cancelled run look broken.
    """
    normalizer = EventNormalizer(
        run_id=run_id, agent_id=agent_id, thread_id=thread_id, reason_for=reason_for
    )
    try:
        async for chunk in graph.astream(
            payload, config, stream_mode=list(STREAM_MODES), subgraphs=SUBGRAPHS
        ):
            for event in normalizer.feed(chunk):
                yield event
    except (asyncio.CancelledError, GeneratorExit):
        raise
    except Exception as exc:  # every provider failure becomes exactly one run.error (RT-47)
        for event in normalizer.drain():
            yield event
        yield normalizer.run_error(exc)
        return
    for event in normalizer.drain():
        yield event
    yield normalizer.run_end()


# --------------------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------------------


def _split_chunk(chunk: object) -> tuple[tuple[str, ...], str, object] | None:
    """Unpack ``(namespace, mode, payload)``, the shape multi-mode + ``subgraphs=True`` produces."""
    if not isinstance(chunk, tuple) or len(chunk) != 3:
        return None
    namespace, mode, payload = chunk
    if not isinstance(namespace, tuple) or not isinstance(mode, str):
        return None
    return tuple(str(part) for part in namespace), mode, payload


def _messages_of(update: Mapping[str, Any]) -> list[BaseMessage]:
    """The ``messages`` of one node's state update, tolerating a bare message."""
    return [item for item in _as_list(update.get("messages")) if isinstance(item, BaseMessage)]


def _as_list(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return list(value)
    return [value]


def _summarize_args(args: Mapping[str, Any]) -> str:
    """Render tool arguments for a human-facing one-liner — never the raw arguments.

    A path argument alone is the whole story for the filesystem tools, and the bulk arguments
    (``content``, ``old_string``, ``new_string``) are deliberately dropped: they are document-sized,
    and the place a human reads proposed content is the approval description (RT-34), not an event.
    """
    for key in _PATH_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            return _truncate(value, _SUMMARY_LIMIT)
    parts = [
        f"{key}={_truncate(_scalar(value), _ARG_VALUE_LIMIT)}"
        for key, value in args.items()
        if key not in _BULK_ARG_KEYS
    ]
    return _truncate(", ".join(parts), _SUMMARY_LIMIT)


def _scalar(value: object) -> str:
    return value if isinstance(value, str) else str(value)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"
