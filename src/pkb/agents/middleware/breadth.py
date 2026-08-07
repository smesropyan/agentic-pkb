"""Breadth files in context, read fresh from disk on every model call (EX-6, EX-7, LB-4).

Architecture §4 supplies a topic's breadth through ``create_deep_agent``'s memory parameter, listing
``topic.md`` and ``notes/summary.md`` as the sources. On the pinned deepagents 0.7.5 that is the
wrong mechanism for this knowledge base, for two independent reasons (D-11, EX-6):

1. ``MemoryMiddleware`` injects a system prompt that instructs the model, verbatim, to *"save new
   knowledge by calling the ``edit_file`` tool"* on exactly those files — and those two files are
   the human-approval surfaces README §1.6 says the AI never finalizes on its own. The gates (RT-23)
   would refuse the write, so the damage is not a lost file; it is an agent told every turn to do
   something the system exists to prevent, spending approvals on writes it was prompted into.
2. It caches the contents in **checkpointed** state (``if "memory_contents" in state: return None``),
   so a thread that stays open across a human's edit keeps showing the model the version it loaded
   on turn one. For a knowledge base whose whole premise is that the human's text wins, a silently
   stale copy of the human's text is the worst possible failure.

This middleware keeps §4's *intent* — breadth always in context, ``index.md`` on demand — and
replaces only the mechanism: :meth:`~KbBreadthMiddleware.wrap_model_call` reads the configured files
off disk and appends them to ``request.system_message``. It reads them again for the next model
call, so a human edit lands in the very next request.

**What is loaded.** A Topic Expert loads its own ``topic.md`` and ``notes/summary.md``
(:func:`topic_breadth_sources`, EX-7). ``index.md`` is deliberately absent: it is the derived depth
directory the expert reads on demand, and it grows without bound. The Librarian loads the root
catalog ``index.md`` and nothing topic-scoped (:func:`librarian_breadth_sources`, LB-4, LB-5) —
root ``tags.md`` is unbounded (GE-19) and its prompt names it as a read-on-demand artifact instead.

**When a file is absent, the block still renders and names it as missing.** This is the normal case,
not an error: a topic mid-creation has a directory before it has a ``topic.md``, and a knowledge
base on its first boot has no root catalog until the first flush. An expert whose ``topic.md`` does
not exist yet is usually about to draft one, so *"not present"* is the single most useful thing the
block can say — more useful than silence, which the model cannot distinguish from an empty file.
The same holds for a file that is unreadable or not valid UTF-8: the block says so and the run
continues, because a breadth file the human broke must not take down the conversation in which they
would fix it.

**A freshly scaffolded placeholder is passed through verbatim.** ``pkb.core.scaffold_topic`` writes
a body that begins *"Placeholder. The Topic Expert drafts…"*, which already says what it is. This
module does no placeholder detection: any such check would be a second copy of Layer 1's scaffold
prose (§8 — cite Layer 1, never restate it) that silently stops matching the day the wording
changes, and it would buy nothing the text does not already say.

**Nothing is cached.** EX-7 sketches a cache keyed on ``(path, st_mtime_ns)``; this implementation
deliberately does not have one, and the deviation is recorded in the handoff. The saving is one
``read()`` of at most :data:`MAX_SOURCE_BYTES` next to a network round trip to a language model —
unmeasurable. The cost is a reintroduction of the exact bug D-11 exists to remove: mtime granularity
is one second on many network and sync-client filesystems (a personal knowledge base living in a
synced folder is the expected deployment), so a human edit made in the same second as the previous
read, or one that leaves the byte length unchanged, would be invisible for the rest of the thread.
"Fresh on every model call" is the guarantee; the cache was the parenthetical.

**Large files are truncated, loudly.** A breadth file is meant to stay compact — GE-12 bounds the
root catalog at 8 KB — so :data:`MAX_SOURCE_BYTES` is set well above that. Passing the cap is
therefore itself information: the block says the text was cut and names the path the model can read
in full, and a truncated ``notes/summary.md`` is a topic that has outgrown its summary
(the ``sub-topic-proposal`` skill's trigger), not a middleware failure.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, Final

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelCallResult,
    ModelRequest,
    ModelResponse,
)

from pkb.agents.middleware.state import KbAgentState
from pkb.agents.paths import to_backend_path
from pkb.core.paths import INDEX_FILE, NOTES_DIR, SUMMARY_FILE, TOPIC_FILE

__all__ = [
    "BLOCK_CLOSE",
    "BLOCK_HEADER",
    "BLOCK_OPEN",
    "MAX_SOURCE_BYTES",
    "KbBreadthMiddleware",
    "librarian_breadth_sources",
    "topic_breadth_sources",
]

MAX_SOURCE_BYTES: Final = 16_384
"""Per-file cap on the bytes injected into the system message.

Generous on purpose. GE-12 bounds the root catalog at 8 KB and a topic's breadth files are supposed
to get sharper rather than longer, so a file that reaches this cap has stopped being a breadth file.
The cap exists so that a human pasting a 2 MB transcript into ``notes/summary.md`` costs one
truncation notice per turn instead of the context window.
"""

BLOCK_OPEN: Final = "<knowledge_base_breadth>"
BLOCK_CLOSE: Final = "</knowledge_base_breadth>"

BLOCK_HEADER: Final = (
    "Read from disk for this message. This is the current text of these files, including any edit "
    "made since the last message; none of it is cached from an earlier turn."
)
"""The one sentence of prose the block carries.

It states a mechanical fact the model cannot otherwise know. It deliberately does *not* restate that
``notes/summary.md`` outranks the rest when something has to be decided — ``prompts/standards.md``
already says that, and duplicating judgment rules into generated context is how the two copies drift
(PR-4's reasoning, applied one layer over).
"""


def topic_breadth_sources(topic_path: str) -> tuple[str, ...]:
    """The two files a Topic Expert carries in context, KB-relative and in reading order (EX-7).

    *topic_path* is a :attr:`pkb.core.models.TopicRecord.path` — ``Cooking`` or
    ``Cooking/sub-topics/Grilling``. A sub-topic therefore loads **its own** breadth files, never its
    parent's: ``resolve_expert`` chooses whose *persona* runs a sub-topic (EX-2), but the scope,
    and so the breadth, is always the sub-topic's own.

    The file names come from ``pkb.core.paths`` rather than from string literals here, so the day
    Layer 1 renames one of them this list moves with it.

    Raises:
        ValueError: If *topic_path* is empty, blank or only slashes — such a path would silently
            produce the knowledge-base root's own ``topic.md`` and ``notes/summary.md``, giving an
            expert a breadth block belonging to no topic at all.
    """
    base = topic_path.strip("/")
    if not base.strip():
        msg = f"expected a knowledge-base-relative topic path, got {topic_path!r}"
        raise ValueError(msg)
    return (f"{base}/{TOPIC_FILE}", f"{base}/{NOTES_DIR}/{SUMMARY_FILE}")


def librarian_breadth_sources() -> tuple[str, ...]:
    """The Librarian's routing view: the generated root catalog, and nothing else (LB-4, LB-5).

    Root ``index.md`` is one line per topic and bounded under 8 KB (GE-12), which is what makes it
    affordable every turn. Root ``tags.md`` is unbounded, so the Librarian prompt names it as a
    read-on-demand artifact instead of loading it. Nothing topic-scoped appears here: the Librarian
    goes wide and delegates depth (LB-5).
    """
    return (INDEX_FILE,)


def _attr(value: str) -> str:
    """Escape a value for use inside a double-quoted attribute of the rendered block."""
    return value.replace("&", "&amp;").replace('"', "&quot;")


def _decode(raw: bytes, *, trim: bool) -> str | None:
    """Decode UTF-8, optionally dropping up to three trailing bytes; ``None`` if it is not UTF-8.

    *trim* is set only when the bytes were cut at the size cap, where the cut can land in the middle
    of a multi-byte character. Without it a legitimately UTF-8 file would be reported as broken
    purely because of where the cap fell. It is off for a whole file, so a genuinely undecodable one
    is reported as such rather than silently losing its last character.
    """
    for dropped in range(4 if trim else 1):
        end = len(raw) - dropped
        if end < 0:
            break
        try:
            return raw[:end].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return None


class KbBreadthMiddleware(AgentMiddleware[KbAgentState, Any, Any]):
    """Appends the configured knowledge-base files to the system message on every model call.

    Construct it through :meth:`for_topic` or :meth:`for_librarian` rather than naming the files at
    a call site; those two classmethods are the whole of EX-7's and LB-4's source lists.

    The instance holds read-only configuration and nothing else (MW-4). One instance serves every
    run of a compiled graph and two delegated experts may run concurrently inside one Librarian turn
    (LB-8), so a mutable attribute here would be shared state between unrelated conversations.
    """

    state_schema = KbAgentState
    """Declared for the family, not because this middleware reads either key.

    langchain merges every middleware's schema into the resolved graph schema (``factory.py:1154``),
    so declaring it more than once across the KB middleware is harmless — and declaring it here
    keeps a graph configured with only this middleware structurally identical to a full one.
    """

    def __init__(
        self,
        kb_root: Path,
        sources: Sequence[str],
        *,
        max_bytes: int = MAX_SOURCE_BYTES,
    ) -> None:
        """Configure the middleware.

        Args:
            kb_root: The knowledge base on disk. Files are read from here directly rather than
                through the agent's backend: the backend view is what the *model* may touch, and
                this is context assembly, which happens before the model exists in the turn.
            sources: KB-relative POSIX paths, in the order they should appear. Usually the return of
                :func:`topic_breadth_sources` or :func:`librarian_breadth_sources`.
            max_bytes: Per-file injection cap. See :data:`MAX_SOURCE_BYTES`.

        Raises:
            ValueError: If a source is not a usable knowledge-base-relative path.
        """
        self.kb_root = kb_root
        self.sources = tuple(sources)
        self.max_bytes = max_bytes
        for source in self.sources:
            # Reject an unusable source while the graph is being built. Left to the first model
            # call it would raise inside `wrap_model_call`, and an exception there aborts the
            # superstep — which (D-1) takes the maintenance flush down with it.
            to_backend_path(source)

    @classmethod
    def for_topic(
        cls, kb_root: Path, topic_path: str, *, max_bytes: int = MAX_SOURCE_BYTES
    ) -> KbBreadthMiddleware:
        """A Topic Expert's breadth: its own ``topic.md`` and ``notes/summary.md`` (EX-7)."""
        return cls(kb_root, topic_breadth_sources(topic_path), max_bytes=max_bytes)

    @classmethod
    def for_librarian(
        cls, kb_root: Path, *, max_bytes: int = MAX_SOURCE_BYTES
    ) -> KbBreadthMiddleware:
        """The Librarian's routing view: the generated root catalog only (LB-4, LB-5)."""
        return cls(kb_root, librarian_breadth_sources(), max_bytes=max_bytes)

    # ----------------------------------------------------------------------------------
    # Rendering
    # ----------------------------------------------------------------------------------

    def _section(self, relative: str) -> str:
        """Render one source file, whatever state it is in.

        Every branch returns a section rather than raising: a missing, unreadable, undecodable or
        oversized breadth file is a thing the human needs to be told about *in the conversation
        where they can fix it*, not a reason for the turn to fail.
        """
        backend_path = _attr(to_backend_path(relative))
        target = self.kb_root.joinpath(*relative.split("/"))
        try:
            with target.open("rb") as handle:
                raw = handle.read(self.max_bytes + 1)
        except FileNotFoundError:
            return f'<file path="{backend_path}" note="not present" />'
        except OSError as exc:
            return f'<file path="{backend_path}" note="unreadable ({type(exc).__name__})" />'

        truncated = len(raw) > self.max_bytes
        text = _decode(raw[: self.max_bytes], trim=True) if truncated else _decode(raw, trim=False)
        if text is None:
            return f'<file path="{backend_path}" note="not valid UTF-8" />'
        if truncated:
            # Cut back to a line boundary so the model is not handed half a sentence as if it were
            # the whole file. `rpartition` yields "" when there is no newline at all, in which case
            # the raw cut stands.
            head = text.rpartition("\n")[0] or text
            body = head.strip()
            note = f' note="truncated at {self.max_bytes} bytes — read the file for the rest"'
        else:
            body = text.strip()
            note = ""
        if not body:
            return f'<file path="{backend_path}" note="empty" />'
        return f'<file path="{backend_path}"{note}>\n{body}\n</file>'

    def _render(self) -> str | None:
        """The whole block, or ``None`` when there is nothing configured to load."""
        if not self.sources:
            return None
        sections = "\n\n".join(self._section(relative) for relative in self.sources)
        return f"{BLOCK_OPEN}\n{BLOCK_HEADER}\n\n{sections}\n{BLOCK_CLOSE}"

    def _with_block(self, request: ModelRequest[Any], block: str | None) -> ModelRequest[Any]:
        """Append *block* to the request's system message, via ``override`` (EX-7).

        ``override`` returns a new request; direct assignment to a ``ModelRequest`` attribute is
        deprecated on this pin and warns. Appending — rather than replacing — is what keeps the
        standards preamble and the domain layer intact under the breadth block (EX-4).
        """
        if block is None:
            return request
        return request.override(
            system_message=append_to_system_message(request.system_message, block)
        )

    # ----------------------------------------------------------------------------------
    # Hooks — both variants, per MW-2
    # ----------------------------------------------------------------------------------

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelCallResult[Any]:
        """Append the breadth block, then run the model call (EX-7)."""
        return handler(self._with_block(request, self._render()))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelCallResult[Any]:
        """Async variant (MW-2), with the file reads off the event loop (MW-3).

        The sync hook raises ``NotImplementedError`` under ``ainvoke()``, and the daemon is
        async-only (RT-3), so both variants are mandatory rather than symmetric-for-neatness. The
        reads go through :func:`asyncio.to_thread` because a knowledge base on a network or
        sync-client mount can block for hundreds of milliseconds on a single ``open()``, and every
        other thread's run shares this event loop.
        """
        block = await asyncio.to_thread(self._render)
        return await handler(self._with_block(request, block))
