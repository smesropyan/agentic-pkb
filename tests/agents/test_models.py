"""`FallbackChatModel` — one test per promise, named so the promise is greppable.

No API key, no network, no `ollama pull`. The exception *classes* are the real ones
(`ollama.ResponseError`, `httpx.ConnectError`) because the whole safety argument rests on what the
installed client actually raises; only the transport is faked. `ollama` is not imported by
`pkb.agents` — the predicate reads `status_code` structurally so the failover survives a deployment
that overrides the model to another provider — and importing it *here* is what proves the structural
read matches the concrete class.

Two of these tests are worth more than the rest and are the reason the file exists:

* `test_bind_tools_returns_a_fallback_carrying_the_tools` and its deep-agent sibling. deepagents
  calls `bind_tools` on every model call, so a wrapper whose `bind_tools` returns a bound *primary*
  silently stops being a fallback at the exact moment the fallback is needed. Nothing else catches
  that — the model still works, right up until the day the quota dies.
* `test_a_non_retryable_failure_propagates_untouched`. Falling back on a content or request error
  produces two wrong answers instead of one clear failure, so "does not fall back" is as much a
  requirement as "does".
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import ollama
import pytest
from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from pkb.agents.models import (
    FallbackChatModel,
    ModelNotInstalledError,
    is_retryable_model_error,
    model_id_of,
    with_fallback,
)
from tests.agents.conftest import raises, says, scripted

PRIMARY = "ollama:deepseek-v4-flash:cloud"
FALLBACK = "ollama:gemma4:31b"
LOGGER = "pkb.agents.models"


# --------------------------------------------------------------------------------------
# Doubles — the transport is faked, the exception classes are not
# --------------------------------------------------------------------------------------


def quota() -> ollama.ResponseError:
    """What the Ollama Pro plan returns past three concurrent cloud models (verified live)."""
    return ollama.ResponseError("rate limit exceeded", 429)


def unreachable() -> ollama.ResponseError:
    """What a cloud model that could not be reached returns (verified against a stub server)."""
    return ollama.ResponseError("upstream unavailable", 502)


def not_pulled(model: str = "gemma4:31b") -> ollama.ResponseError:
    """What the local daemon returns for a model that is not installed (verified live)."""
    return ollama.ResponseError(f"model '{model}' not found", 404)


class ToolEcho(BaseChatModel):
    """Answers with the names of the tools it was bound with.

    A fallback that merely *answers* proves nothing about `bind_tools`: it would answer just as
    happily with no tools at all, and an agent whose model has lost its tool suite fails later and
    somewhere else. Echoing the binding is what turns "the fallback answered" into "the fallback
    answered with the tools bound".
    """

    bound: tuple[str, ...] = ()

    @property
    def _llm_type(self) -> str:
        return "tool-echo"

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        text = " ".join(self.bound) if self.bound else "<unbound>"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def bind_tools(self, tools: Any, **kwargs: Any) -> ToolEcho:
        return ToolEcho(bound=tuple(_tool_name(one) for one in tools))


class Counter:
    """Counts how many times a factory was called. `int` would be rebound, not mutated."""

    def __init__(self, model: BaseChatModel) -> None:
        self.model = model
        self.builds = 0

    def __call__(self) -> BaseChatModel:
        self.builds += 1
        return self.model


def _tool_name(one: Any) -> str:
    if isinstance(one, BaseTool):
        return one.name
    return str(getattr(one, "name", one))


@tool
def alpha(text: str) -> str:
    """First tool."""
    return text


@tool
def beta(text: str) -> str:
    """Second tool."""
    return text


def fallback_model(
    primary: BaseChatModel,
    fallback: BaseChatModel,
    *,
    fallback_id: str = FALLBACK,
) -> FallbackChatModel:
    return FallbackChatModel(
        primary_factory=lambda: primary,
        fallback_factory=lambda: fallback,
        primary_id=PRIMARY,
        fallback_id=fallback_id,
    )


# --------------------------------------------------------------------------------------
# The predicate — which failures earn a second attempt
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (quota(), True),
        (unreachable(), True),
        (ollama.ResponseError("gateway timeout", 504), True),
        (ollama.ResponseError("server error", 500), True),
        (ollama.ResponseError("request timeout", 408), True),
        (httpx.ConnectError("connection refused"), True),
        (httpx.ReadTimeout("read timed out"), True),
        (httpx.ConnectTimeout("connect timed out"), True),
        (httpx.RemoteProtocolError("server disconnected"), True),
        (ConnectionError("Failed to connect to Ollama."), True),
        (TimeoutError("timed out"), True),
        # Below here: the second model would fail in exactly the same way.
        (not_pulled(), False),
        (ollama.ResponseError("invalid options", 400), False),
        (ollama.ResponseError("unauthorized", 401), False),
        (ollama.ResponseError("forbidden", 403), False),
        (ollama.ResponseError("mid-stream error"), False),
        (httpx.LocalProtocolError("we built a bad request"), False),
        (ValueError("malformed tool call"), False),
        (TypeError("bad argument"), False),
    ],
)
def test_the_retryable_predicate_names_availability_and_nothing_else(
    exc: BaseException, expected: bool
) -> None:
    """Quota, concurrency and availability fall back; requests and content do not.

    `ollama.ResponseError("mid-stream error")` carries the sentinel `status_code == -1` — the error
    arrived inside an already-started stream and its cause is unknown. Unknown is not retryable.
    """
    assert is_retryable_model_error(exc) is expected


def test_the_status_is_read_structurally_not_by_provider() -> None:
    """A provider this project does not import still gets its failover.

    `pkb.agents.models` never imports `ollama`; the deployment model is overridable (RG-21), and an
    Anthropic `RateLimitError` or an OpenAI `APIStatusError` carries `status_code` the same way.
    """

    class ForeignRateLimitError(Exception):
        status_code = 429

    class ForeignBadRequestError(Exception):
        status_code = 400

    class ForeignWrappedError(Exception):
        response = httpx.Response(503, request=httpx.Request("POST", "http://x"))

    assert is_retryable_model_error(ForeignRateLimitError())
    assert not is_retryable_model_error(ForeignBadRequestError())
    assert is_retryable_model_error(ForeignWrappedError())


# --------------------------------------------------------------------------------------
# Failing over, and refusing to
# --------------------------------------------------------------------------------------


def test_a_retryable_failure_is_answered_by_the_fallback(caplog: pytest.LogCaptureFixture) -> None:
    model = fallback_model(scripted(raises(quota())), scripted(says("filed by the fallback")))

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        answer = model.invoke("file this")

    assert answer.content == "filed by the fallback"
    (record,) = [r for r in caplog.records if r.name == LOGGER]
    assert record.levelno == logging.WARNING
    # Both models and the reason, so the human can act without reading the code.
    assert PRIMARY in record.message
    assert FALLBACK in record.message
    assert "rate limit exceeded" in record.message


@pytest.mark.asyncio
async def test_a_retryable_failure_is_answered_by_the_fallback_async(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The runtime is async-only (RT-3), so the async path is the one production actually takes."""
    model = fallback_model(scripted(raises(unreachable())), scripted(says("filed by the fallback")))

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        answer = await model.ainvoke("file this")

    assert answer.content == "filed by the fallback"
    assert any("upstream unavailable" in r.message for r in caplog.records if r.name == LOGGER)


def test_a_non_retryable_failure_propagates_untouched() -> None:
    """The fallback is never reached, and the original exception is what the caller sees.

    A content-level error answered by a second model hands the human a different judgement without
    telling them; a malformed request answered by a second model fails twice. Both are worse than
    the one clear failure this preserves.
    """
    fallback = Counter(scripted(says("should never run")))
    model = FallbackChatModel(
        primary_factory=lambda: scripted(raises(ValueError("malformed tool call"))),
        fallback_factory=fallback,
        primary_id=PRIMARY,
        fallback_id=FALLBACK,
    )

    with pytest.raises(ValueError, match="malformed tool call"):
        model.invoke("file this")
    assert fallback.builds == 0


@pytest.mark.asyncio
async def test_a_non_retryable_failure_propagates_untouched_async() -> None:
    fallback = Counter(scripted(says("should never run")))
    model = FallbackChatModel(
        primary_factory=lambda: scripted(raises(ollama.ResponseError("invalid options", 400))),
        fallback_factory=fallback,
        primary_id=PRIMARY,
        fallback_id=FALLBACK,
    )

    with pytest.raises(ollama.ResponseError, match="invalid options"):
        await model.ainvoke("file this")
    assert fallback.builds == 0


def test_a_healthy_primary_never_touches_the_fallback() -> None:
    fallback = Counter(scripted(says("unused")))
    model = FallbackChatModel(
        primary_factory=lambda: scripted(says("filed"), says("filed again")),
        fallback_factory=fallback,
        primary_id=PRIMARY,
        fallback_id=FALLBACK,
    )

    assert model.invoke("one").content == "filed"
    assert model.invoke("two").content == "filed again"
    assert fallback.builds == 0


# --------------------------------------------------------------------------------------
# `bind_tools` — the promise that silently breaks
# --------------------------------------------------------------------------------------


def test_bind_tools_returns_a_fallback_carrying_the_tools() -> None:
    """Bind, then fail the primary: the fallback answers *with the tools bound*.

    Returning `self.primary.bind_tools(...)` would pass a naive "the fallback answers" test right up
    until tools were involved — which is always, in a deep agent. The tools are stored and applied
    to whichever model answers, so the echo below is the proof they arrived.
    """
    model = fallback_model(scripted(raises(quota())), ToolEcho())

    bound = model.bind_tools([alpha, beta])

    assert isinstance(bound, FallbackChatModel)
    assert bound.invoke("go").content == "alpha beta"


@pytest.mark.asyncio
async def test_bind_tools_survives_on_the_async_path() -> None:
    model = fallback_model(scripted(raises(quota())), ToolEcho())
    bound = model.bind_tools([alpha])
    assert (await bound.ainvoke("go")).content == "alpha"


def test_a_healthy_primary_also_receives_the_bound_tools() -> None:
    """The stored binding is applied to *whichever* model answers, not only to the fallback."""
    model = fallback_model(ToolEcho(), scripted(says("unused")))
    assert model.bind_tools([alpha, beta]).invoke("go").content == "alpha beta"


def test_rebinding_shares_the_failover_state() -> None:
    """deepagents rebinds on every model call, so the copies must share what must not be rebuilt."""
    model = fallback_model(scripted(says("ok")), scripted(says("unused")))
    once = model.bind_tools([alpha])
    twice = once.bind_tools([beta])

    assert twice.failover is model.failover
    assert twice.bound_tools is not None
    assert [_tool_name(one) for one in twice.bound_tools] == ["beta"]


def test_the_failover_survives_a_real_deep_agent() -> None:
    """End to end through `create_deep_agent`, which binds its whole tool suite unconditionally.

    This is the test that would have caught a `bind_tools` that returns a bound primary: everything
    above it can pass while a real agent's failover has been quietly deleted by the harness.
    """
    model = fallback_model(scripted(raises(quota())), scripted(says("filed by the fallback")))
    agent = create_deep_agent(
        model=model, system_prompt="file it", tools=[], checkpointer=InMemorySaver()
    )

    result = agent.invoke(
        {"messages": [HumanMessage("file this")]}, {"configurable": {"thread_id": "T-failover"}}
    )

    assert result["messages"][-1].content == "filed by the fallback"


# --------------------------------------------------------------------------------------
# Laziness, and the local model that is not installed
# --------------------------------------------------------------------------------------


def test_neither_model_is_built_until_it_is_needed() -> None:
    """`gemma4:31b` is a ~20GB download that is not pulled here; wiring it must cost nothing.

    The primary is deferred for the same reason at a smaller scale: compiling a graph must not
    construct a provider client or read credentials (RG-3, RG-4).
    """
    primary = Counter(scripted(raises(quota()), says("recovered")))
    fallback = Counter(scripted(says("from the fallback")))
    model = FallbackChatModel(
        primary_factory=primary,
        fallback_factory=fallback,
        primary_id=PRIMARY,
        fallback_id=FALLBACK,
    )

    assert (primary.builds, fallback.builds) == (0, 0)
    model.bind_tools([alpha])
    assert (primary.builds, fallback.builds) == (0, 0), "binding tools is not using a model"

    assert model.invoke("one").content == "from the fallback"
    assert (primary.builds, fallback.builds) == (1, 1)

    assert model.invoke("two").content == "recovered"
    assert (primary.builds, fallback.builds) == (1, 1), "each model is built at most once"


def test_with_fallback_resolves_nothing_at_construction() -> None:
    """An unresolvable spec is accepted here and refused at first use — that is the laziness."""
    model = with_fallback("no-such-provider:nope", "also-no-such-provider:nope")

    assert model.primary_id == "no-such-provider:nope"
    assert model.fallback_id == "also-no-such-provider:nope"
    with pytest.raises(ValueError, match="Unable to infer model provider"):
        _ = model.primary


def test_a_missing_fallback_model_names_the_pull_command() -> None:
    """The worst moment to meet a bare 404 is the moment the cloud quota died. Say the fix."""
    model = fallback_model(scripted(raises(quota())), scripted(raises(not_pulled())))

    with pytest.raises(ModelNotInstalledError) as caught:
        model.invoke("file this")

    message = str(caught.value)
    assert "fallback" in message
    assert FALLBACK in message
    assert "ollama pull gemma4:31b" in message
    # The reason the fallback was reached for at all, so the quota failure is not lost behind it.
    assert "rate limit exceeded" in message
    assert isinstance(caught.value.__cause__, ollama.ResponseError)


@pytest.mark.asyncio
async def test_a_missing_fallback_model_names_the_pull_command_async() -> None:
    model = fallback_model(scripted(raises(quota())), scripted(raises(not_pulled())))

    with pytest.raises(ModelNotInstalledError, match="ollama pull gemma4:31b"):
        await model.ainvoke("file this")


def test_a_missing_primary_model_names_the_pull_command_too() -> None:
    """A 404 is not retryable, so this never reaches the fallback — but it is just as cryptic."""
    fallback = Counter(scripted(says("unused")))
    model = FallbackChatModel(
        primary_factory=lambda: scripted(raises(not_pulled("deepseek-v4-flash"))),
        fallback_factory=fallback,
        primary_id="ollama:deepseek-v4-flash",
        fallback_id=FALLBACK,
    )

    with pytest.raises(ModelNotInstalledError, match="ollama pull deepseek-v4-flash"):
        model.invoke("file this")
    assert fallback.builds == 0


def test_a_non_ollama_model_gets_no_pull_advice() -> None:
    """`ollama pull` is advice that does not apply to another provider, so it is not offered."""
    model = FallbackChatModel(
        primary_factory=lambda: scripted(raises(quota())),
        fallback_factory=lambda: scripted(raises(not_pulled())),
        primary_id=PRIMARY,
        fallback_id="anthropic:claude-opus-5",
    )

    with pytest.raises(ollama.ResponseError, match="not found"):
        model.invoke("file this")


def test_any_other_fallback_failure_reaches_the_caller_intact() -> None:
    """Only the missing-model case is translated; everything else is handed over as it arrived.

    The primary's failure is not lost either — it is already on the `__context__` chain, which is
    what makes re-raising bare correct rather than merely convenient.
    """
    model = fallback_model(scripted(raises(quota())), scripted(raises(unreachable())))

    with pytest.raises(ollama.ResponseError, match="upstream unavailable") as caught:
        model.invoke("file this")

    assert caught.value.__cause__ is None, "the fallback's own error was not rewrapped"
    assert "rate limit exceeded" in str(caught.value.__context__)


# --------------------------------------------------------------------------------------
# Saying it out loud — once
# --------------------------------------------------------------------------------------


def test_the_warning_is_one_line_per_outage_not_one_per_model_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A turn makes many model calls. One warning each would be noise the human learns to skip."""
    model = fallback_model(scripted(raises(quota())), scripted(says("a"), says("b"), says("c")))

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        for _ in range(3):
            model.bind_tools([alpha]).invoke("go")

    assert len([r for r in caplog.records if r.name == LOGGER]) == 1


def test_a_recovered_primary_re_arms_the_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A second outage is news again — the latch clears the moment the primary answers."""
    primary = scripted(raises(quota()), says("recovered"), raises(quota()))
    model = fallback_model(primary, scripted(says("fallback one"), says("fallback two")))

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert model.invoke("one").content == "fallback one"
        assert model.invoke("two").content == "recovered"
        assert model.invoke("three").content == "fallback two"

    assert len([r for r in caplog.records if r.name == LOGGER]) == 2


# --------------------------------------------------------------------------------------
# Transparency — a failover changes who answers, not what the agent thinks it is running
# --------------------------------------------------------------------------------------


def test_the_wrapper_identifies_itself_as_the_primary() -> None:
    """deepagents finds a harness profile through `get_model_identifier` + `get_model_provider`,
    both of which read the instance it is given — so both must answer for the primary."""
    model = with_fallback(PRIMARY, FALLBACK)

    assert model.model == "deepseek-v4-flash:cloud"
    assert model._llm_type == "chat-ollama"
    assert model._get_ls_params()["ls_provider"] == "ollama"
    assert model._get_ls_params()["ls_model_name"] == "deepseek-v4-flash:cloud"
    assert model._identifying_params == model.primary._identifying_params


def test_model_id_of_reads_specs_and_instances() -> None:
    assert model_id_of(PRIMARY) == PRIMARY
    assert model_id_of(scripted(says("x"))) == "scripted"


class _NamedModel(BaseChatModel):
    model_name: str = Field(default="named-thing")

    @property
    def _llm_type(self) -> str:
        return "named"

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        raise NotImplementedError


def test_model_id_of_prefers_model_name() -> None:
    """The same order deepagents uses (`model_name`, then `model`), so the two cannot disagree."""
    assert model_id_of(_NamedModel()) == "named-thing"
