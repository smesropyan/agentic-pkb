"""Shared fixtures for the `pkb.agents` suite — above all, a chat model that can drive a deep agent.

The whole non-live suite rests on `ScriptedChatModel`. Every fake shipped by langchain-core
(`GenericFakeChatModel`, `FakeMessagesListChatModel`, `FakeListChatModel`, `ParrotFakeChatModel`)
inherits `BaseChatModel.bind_tools`, which raises `NotImplementedError` — and `create_agent` always
calls it, so none of them can drive a deep agent at all (D-8). This one can, and it records the
exact prompt it saw each turn, which is how prompt-content rules are asserted.

Three constraints the harness forces on any test using it, each learned the hard way:

1. **Unique tool-call ids, and the script must terminate.** A repeated id, or a script that sticks
   on a tool call, hits a langchain 1.3.14 routing bug (`KeyError: 'model'`).
2. **Both hook variants.** A middleware implementing only the sync hook raises under `ainvoke()`;
   tests drive both.
3. **One model instance is shared with subagents**, so a delegation test scripts the parent and the
   delegate as a single sequence in global call order.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from pkb.core.scaffold import scaffold_subtopic, scaffold_topic

TODAY = datetime.date(2026, 8, 6)


class ScriptedChatModel(BaseChatModel):
    """A chat model that replays a script and records what it was asked.

    `script` holds `AIMessage`s, or zero-argument callables — a callable may raise, which is how the
    failure-path tests make a run blow up after a write has already landed (MW-26).
    """

    script: list[Any] = Field(default_factory=list)
    idx: int = 0
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    """Every turn's prompt, in order — how prompt-content rules are asserted."""

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(list(messages))
        item = self.script[min(self.idx, len(self.script) - 1)]
        self.idx += 1
        message = item() if callable(item) else item
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> BaseChatModel:
        """Required: `create_agent` calls this unconditionally, and every stock fake raises here."""
        return self

    @property
    def system_prompts(self) -> list[str]:
        """The system prompt text of each turn, for prompt-content assertions."""
        return [str(turn[0].content) for turn in self.calls if turn and turn[0].type == "system"]


def scripted(*script: Any) -> ScriptedChatModel:
    """A fresh model with its own recording buffers — never share one across tests."""
    return ScriptedChatModel(script=list(script), idx=0, calls=[])


def call(name: str, args: dict[str, Any], id_: str) -> dict[str, Any]:
    """One tool call. `id_` must be unique across the whole script (see the module docstring)."""
    return {"name": name, "args": args, "id": id_, "type": "tool_call"}


def says(text: str) -> AIMessage:
    """A plain assistant turn, which also terminates a script."""
    return AIMessage(content=text)


def calls(*tool_calls: dict[str, Any], text: str = "") -> AIMessage:
    """An assistant turn that requests tools."""
    return AIMessage(content=text, tool_calls=list(tool_calls))


def raises(exc: Exception) -> Callable[[], AIMessage]:
    """A script entry that blows the run up — for the flush-on-failure rules (MW-26, MW-27)."""

    def _raise() -> AIMessage:
        raise exc

    return _raise


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    """A valid two-topic knowledge base with one sub-topic, built through Layer 1 itself.

    Built by `scaffold_topic` rather than by hand so the fixture cannot drift from what the
    scaffolder actually writes — if Layer 1's placeholders change, these tests see it.
    """
    root = tmp_path / "KnowledgeBase"
    root.mkdir()
    scaffold_topic(
        root,
        "Cooking",
        title="Cooking",
        description="Home cooking: technique, equipment, and recipes",
        today=TODAY,
    )
    scaffold_topic(
        root,
        "BBQ",
        title="BBQ",
        description="Barbecue equipment, fuel, and technique",
        today=TODAY,
    )
    scaffold_subtopic(
        root,
        "Cooking",
        "Grilling",
        title="Grilling",
        description="Charcoal and gas grilling",
        today=TODAY,
    )
    return root


@pytest.fixture
def empty_kb(tmp_path: Path) -> Path:
    """An empty KB — the bootstrapping case, where every inbound item is a topic gap (LB-6)."""
    root = tmp_path / "EmptyKnowledgeBase"
    root.mkdir()
    return root
