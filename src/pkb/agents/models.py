"""The chat model the registry hands to a factory — primary, fallback, and what earns a failover.

RG-21 makes the model a *registry* concern: nothing in the tree, no transport and no channel
selects one. This module is the mechanism that concern needs and nothing more — it knows how to run
one model with a second one behind it, and it knows which failures deserve that second attempt. It
does **not** know which models those are; that is :mod:`pkb.agents.registry`'s policy
(:data:`~pkb.agents.registry.DEFAULT_MODEL`, :data:`~pkb.agents.registry.DEFAULT_FALLBACK_MODEL`).

**Why a chat model and not ``Runnable.with_fallbacks``.** ``create_deep_agent`` binds its tool suite
unconditionally — ``langchain/agents/factory.py:1390`` calls ``request.model.bind_tools(...)`` on
*every* model call — and ``RunnableWithFallbacks`` does not implement ``bind_tools`` (checked on the
pin: ``hasattr(RunnableWithFallbacks, "bind_tools")`` is ``False``). Wrapping a model in
``with_fallbacks`` therefore cannot survive contact with the agent factory at all.

**Why the binding is stored rather than applied** (see :meth:`FallbackChatModel.bind_tools`). The
obvious implementation binds the primary and returns the bound runnable, and it is silently wrong:
the fallback stops being a fallback at exactly the moment deepagents binds its tools, which is every
run. So the wrapper keeps the tools and applies them to *whichever* model ends up answering, and
``bind_tools`` returns another wrapper of the same kind.

**What earns a failover** — :func:`is_retryable_model_error`, and the predicate is the whole safety
argument. Falling back on the wrong error class is worse than not falling back at all: a malformed
request or a model name that does not exist fails identically on the second model, so retrying it
turns one clear failure into two, and a content-level error answered by a different model hands the
human a different judgement without telling them. Only quota, concurrency and availability failures
fall back. Every exception shape below was **executed** against the pinned ``langchain-ollama 1.1.0``
/ ``ollama`` client, not guessed:

* ``429`` (quota or the Pro plan's three-concurrent-model limit), ``502`` (a cloud model was
  unreachable) and every other ``5xx`` arrive as ``ollama.ResponseError`` carrying ``.status_code``
  — on both the sync and the async path. Verified against a stub server returning each status.
* A dead endpoint arrives as a **raw ``httpx.ConnectError``**, *not* as the builtin
  ``ConnectionError`` the ollama client's ``_request_raw`` promises: langchain-ollama always takes
  the streaming path (``Client._request``'s ``inner()``), which catches ``httpx.HTTPStatusError``
  and nothing else. Verified by pointing a ``ChatOllama`` at ``127.0.0.1:1``. Both shapes are in the
  predicate, because the non-streaming path is still reachable from other calls.
* A model that is not pulled arrives as ``ollama.ResponseError`` with ``.status_code == 404`` and the
  message ``model 'gemma4:31b' not found``. Verified against the live local daemon. That is **not**
  retryable — and when it is the *fallback* that is missing, it is translated into
  :class:`ModelNotInstalledError`, which names the ``ollama pull`` command. A cryptic 404 at the
  moment the cloud quota dies is the worst possible time for a confusing error.

The status is read by duck typing rather than by ``isinstance(exc, ollama.ResponseError)`` so that a
deployment overriding the model to another provider keeps its failover: an Anthropic
``RateLimitError`` and an OpenAI ``APIStatusError`` both carry ``status_code`` too.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final

import httpx
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field

if TYPE_CHECKING:  # pragma: no cover - typing only
    from langchain_core.callbacks import (
        AsyncCallbackManagerForLLMRun,
        CallbackManagerForLLMRun,
    )
    from langchain_core.language_models.base import LangSmithParams, LanguageModelInput
    from langchain_core.language_models.model_profile import ModelProfile
    from langchain_core.messages import AIMessage, BaseMessage
    from langchain_core.runnables import Runnable
    from langchain_core.tools import BaseTool

__all__ = [
    "OLLAMA_PREFIX",
    "FallbackChatModel",
    "ModelNotInstalledError",
    "is_retryable_model_error",
    "model_id_of",
    "with_fallback",
]

logger = logging.getLogger(__name__)

OLLAMA_PREFIX: Final = "ollama:"
"""What an ``init_chat_model`` spec starts with when it names an Ollama model.

``init_chat_model`` splits on the *first* colon only, so ``ollama:deepseek-v4-flash:cloud`` resolves
to a ``ChatOllama`` whose ``model`` is ``deepseek-v4-flash:cloud`` — the tag survives (verified). The
prefix is therefore also the only thing that distinguishes "this model is pullable with
``ollama pull``" from any other provider's spec, which is what :func:`_pull_command` needs.
"""

_RETRYABLE_STATUS: Final = frozenset({408, 429})
"""HTTP statuses below 500 that mean *try again elsewhere*, never *your request was wrong*.

``429`` is the one that matters in production: the Ollama Pro plan queues past three concurrent
cloud models and then rejects. ``408`` is a server-side request timeout, which is the same class of
problem. Everything else under 500 — ``400``, ``401``, ``403``, ``404``, ``422`` — is a statement
about the *request* and will be repeated verbatim by the second model.
"""

_MODEL_NOT_FOUND: Final = 404
"""Ollama's answer for a model that is not pulled on this machine (verified live, see the module
docstring). Not retryable, but worth translating: see :class:`ModelNotInstalledError`."""

_RETRYABLE_TRANSPORT: Final = (
    ConnectionError,
    TimeoutError,
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.ProxyError,
    httpx.RemoteProtocolError,
)
"""Exception types that mean the endpoint could not be reached or did not finish answering.

``httpx.RemoteProtocolError`` (the server hung up mid-response) is in and its sibling
``httpx.LocalProtocolError`` is deliberately out: the first is the endpoint failing, the second is
*us* building an invalid request, and the second model would build exactly the same one. The two
builtins are here because ``ollama.Client._request_raw`` translates ``httpx.ConnectError`` into a
plain ``ConnectionError`` on its non-streaming path.
"""


class ModelNotInstalledError(RuntimeError):
    """The model named by a spec is not pulled on this machine.

    Exists so the *worst* moment to meet a bare ``404`` — the cloud quota has just run out and the
    local safety net turns out to be missing — produces a sentence with the fix in it instead. The
    message names the model, the ``ollama pull`` command, and what the primary had failed with.
    """


def is_retryable_model_error(exc: BaseException) -> bool:
    """Whether *exc* is a failure the *other* model might not have.

    ``True`` for quota, concurrency and availability — ``429``, ``408``, every ``5xx``, and any
    connection or timeout failure. ``False`` for everything else, which is the important half: a
    malformed request, an unknown model name, a bad API key or a content-level parse failure will
    fail identically on the second model, and falling back on one of those produces two wrong
    answers where there should have been one clear failure.

    An unrecognized exception is **not** retryable. That includes an ``ollama.ResponseError`` raised
    from a mid-stream ``{"error": ...}`` line, which carries the sentinel ``status_code == -1``: the
    generation had already begun, so its cause is unknown and re-running it on another model is a
    guess. See the module docstring for the executed evidence behind every shape named here.
    """
    if isinstance(exc, _RETRYABLE_TRANSPORT):
        return True
    status = _status_of(exc)
    if status is None:
        return False
    return status in _RETRYABLE_STATUS or 500 <= status < 600


def model_id_of(model: str | BaseChatModel) -> str:
    """A display id for *model* — what :attr:`~pkb.contracts.AgentDescriptor.model_id` carries.

    A spec string is its own id. An already-built model is identified the same way deepagents
    identifies one (``deepagents._models.get_model_identifier``): ``model_name``, then ``model``.
    ``_llm_type`` is the last resort so a test's scripted model still produces a readable row
    instead of a repr.
    """
    if isinstance(model, str):
        return model
    for attribute in ("model_name", "model"):
        value = getattr(model, attribute, None)
        if isinstance(value, str) and value:
            return value
    return model._llm_type


def with_fallback(
    primary: str | BaseChatModel,
    fallback: str | BaseChatModel,
) -> FallbackChatModel:
    """Run *primary*, and *fallback* when :func:`is_retryable_model_error` says the failure earns it.

    **Neither model is resolved here.** The fallback must not be, and that is requirement 3: the
    local model is a ~20GB download that may not be pulled, and an unavailable safety net has to
    cost nothing while the cloud model is healthy — which is also why its absence surfaces as
    :class:`ModelNotInstalledError` at the moment of need rather than at boot.

    The primary is deferred for a quieter but equally real reason: RG-3 and RG-4 make *compiling*
    a graph pay for nothing it does not use, and resolving a spec means constructing a provider
    client — which for several providers reads credentials and raises without them. Deferring keeps
    ``AgentRegistry._compile`` free of provider SDKs, keeps a registry whose factories are injected
    (every test that drives graphs with a scripted model) able to carry a placeholder spec it never
    resolves, and costs nothing: ``create_deep_agent`` inspects the model as it builds, and that is
    the first thing that resolves it.

    Resolution is plain :func:`~langchain.chat_models.init_chat_model` rather than deepagents'
    ``resolve_model``, which additionally applies a registered ``ProviderProfile``. On the pin the
    only registered provider profile is ``openai``, so for every model this project runs the two are
    identical; a deployment that switches to an OpenAI spec *and* wants that profile should pass a
    pre-built model instead.
    """
    return FallbackChatModel(
        primary_factory=lambda: _resolve(primary),
        fallback_factory=lambda: _resolve(fallback),
        primary_id=model_id_of(primary),
        fallback_id=model_id_of(fallback),
    )


class _FailoverState:
    """The mutable half of a :class:`FallbackChatModel`, shared by every ``bind_tools`` copy of it.

    Sharing is not an optimization; every part of it breaks without it.

    *The built models* must be shared or "each is built at most once" is void — a wrapper is copied
    on every model call, so a per-copy cache is no cache at all, and the fallback would be
    reconstructed on each failover.

    *The warned-once latch* must be shared for the same reason: langchain re-binds the tool suite on
    **every** model call (``factory.py:_get_bound_model``), so a latch living on the wrapper itself
    would be re-armed once per call and the warning would become per-call noise. Held here, one
    outage produces one warning; the latch clears the moment the primary answers again, so a later
    outage is reported afresh. That is at most one line per run, which is what a human needs to
    learn that their quota died — and never a line per model call, which they would learn to ignore.
    """

    __slots__ = ("_fallback", "_lock", "_primary", "_warned")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._primary: BaseChatModel | None = None
        self._fallback: BaseChatModel | None = None
        self._warned = False

    def primary(self, build: Callable[[], BaseChatModel]) -> BaseChatModel:
        """The primary, built on first need and kept. A failed build is not cached."""
        with self._lock:
            if self._primary is None:
                self._primary = build()
            return self._primary

    def fallback(self, build: Callable[[], BaseChatModel]) -> BaseChatModel:
        """The fallback, built on first need and kept. A failed build is not cached."""
        with self._lock:
            if self._fallback is None:
                self._fallback = build()
            return self._fallback

    def announce(self, *, primary_id: str, fallback_id: str, exc: BaseException) -> None:
        """Say it out loud, once per outage. A silent failover is the failure mode this prevents."""
        with self._lock:
            if self._warned:
                return
            self._warned = True
        logger.warning(
            "model failover: the primary model %r is unavailable (%s: %s), so this run is being "
            "answered by the fallback model %r. The knowledge base keeps working, but the judgement "
            "in these turns is the fallback's, not the primary's.",
            primary_id,
            type(exc).__name__,
            exc,
            fallback_id,
        )

    def recovered(self) -> None:
        """Re-arm the warning: the primary answered, so the next outage is a new one."""
        with self._lock:
            self._warned = False


class FallbackChatModel(BaseChatModel):
    """A chat model that answers from :attr:`primary`, or from the fallback when it cannot.

    Transparent to everything that inspects a model: :attr:`model`, :meth:`_get_ls_params`,
    :attr:`_identifying_params` and ``_llm_type`` all describe the **primary**, so deepagents'
    harness-profile lookup (``harness_profiles._harness_profile_for_model`` →
    ``get_model_identifier`` + ``get_model_provider``) resolves exactly what it would have resolved
    for a bare primary. A failover changes which model answers; it must not change which harness the
    agent runs under.

    ``profile`` is the one exception and it is deliberate. It is a *field*, filled by a validator at
    construction, so delegating it would resolve the primary at construction and cost the laziness
    :func:`with_fallback` explains. It stays ``None`` — which is exactly what ``ChatOllama`` itself
    reports for both of this project's models, so nothing changes for them. What a non-Ollama
    override gives up is bounded and documented: deepagents' summarization middleware falls back
    from fraction-of-context budgets to fixed token counts, and its multimodal scrub treats every
    content type as supported.

    Both :meth:`_generate` and :meth:`_agenerate` are implemented: the runtime is async-only (RT-3)
    but the non-live suite drives graphs with ``invoke()`` (MW-2), so a wrapper that implemented one
    of them would fail in exactly one of those two worlds. Neither ``_stream`` nor ``_astream`` is
    implemented, and nothing is lost by that: ``langchain/agents/factory.py`` executes the model with
    ``model_.invoke(messages)`` / ``await model_.ainvoke(messages)``, never ``.stream``, so no chat
    model in this harness streams tokens whether it is wrapped or not.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    primary_factory: Callable[[], BaseChatModel]
    """Builds the model that answers when it can. Called at most once, on first use."""

    fallback_factory: Callable[[], BaseChatModel]
    """Builds the fallback. Called at most once, on the first failover — never at compile time."""

    primary_id: str
    """Display id of the primary, for the warning and for :class:`ModelNotInstalledError`."""

    fallback_id: str
    """Display id of the fallback. When it starts with ``ollama:`` it also yields the pull command."""

    failover: _FailoverState = Field(default_factory=_FailoverState)
    """Shared with every ``bind_tools`` copy — see :class:`_FailoverState` for why that matters."""

    retryable: Callable[[BaseException], bool] = is_retryable_model_error
    """The predicate. Injectable so a test can drive the two branches without inventing HTTP."""

    bound_tools: tuple[Any, ...] | None = None
    """Tools handed to :meth:`bind_tools`, kept rather than applied. ``None`` means never bound."""

    bound_kwargs: Mapping[str, Any] = Field(default_factory=dict)
    """The rest of what :meth:`bind_tools` was called with — ``tool_choice`` and model settings."""

    # -- the two models ------------------------------------------------------------------

    @property
    def primary(self) -> BaseChatModel:
        """The primary, built once on first use and shared with every ``bind_tools`` copy."""
        return self.failover.primary(self.primary_factory)

    @property
    def fallback(self) -> BaseChatModel:
        """The fallback, built once on first *need*. Touching this attribute builds it."""
        return self.failover.fallback(self.fallback_factory)

    # -- identity: everything here describes the primary ---------------------------------

    @property
    def _llm_type(self) -> str:
        return self.primary._llm_type

    @property
    def model(self) -> str:
        """The primary's provider-native identifier.

        deepagents reads ``model_name`` then ``model`` off the instance it is given
        (``_models.get_model_identifier``) to find a harness profile. This wrapper has no
        ``model_name``, so this is the attribute that answers, and it answers with the primary's own
        identifier — the same string a bare primary would have produced.
        """
        return model_id_of(self.primary)

    @property
    def _identifying_params(self) -> dict[str, Any]:
        """The primary's. This is the LLM-cache key, so a failover must not silently change it —
        the fallback's answer is still an answer to the same configured model."""
        return dict(self.primary._identifying_params)

    def _get_ls_params(self, stop: list[str] | None = None, **kwargs: Any) -> LangSmithParams:
        """Delegated so ``ls_provider`` is the primary's, not ``fallbackchatmodel``.

        The base implementation derives the provider from the *class name*, and deepagents uses that
        provider to look up a harness profile — so a wrapper that did not delegate would silently
        put every agent on a different harness than the model it actually runs.
        """
        return self.primary._get_ls_params(stop=stop, **kwargs)

    def _resolve_model_profile(self) -> ModelProfile | None:
        """Deliberately ``None`` — see the class docstring.

        The base class calls this from a ``mode="after"`` validator, i.e. while the wrapper is being
        *constructed*, and construction happens once per graph compile and again on every
        ``bind_tools``. Answering it honestly would mean resolving the primary at each of those
        moments, which is exactly the cost :func:`with_fallback` defers. Overriding it to ``None``
        (rather than leaving the base implementation, which also returns ``None``) is what makes
        that a decision on the record instead of an accident.
        """
        return None

    # -- tools ---------------------------------------------------------------------------

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        **kwargs: Any,
    ) -> FallbackChatModel:
        """Return another fallback model carrying *tools* — never a bound primary.

        This is the method the whole class turns on. ``bind_tools`` on an ordinary chat model
        returns a ``RunnableBinding``, which is not a chat model and has no fallback behaviour of
        its own; deepagents calls it on every model call, so binding the primary here and returning
        the result would delete the failover at precisely the moment it starts to matter. Instead
        the tools are *stored* and applied by :meth:`_bound` to whichever model answers, and the
        returned object is another :class:`FallbackChatModel` — so binding twice, or binding a
        binding, still leaves a fallback in place.

        The copy shares :attr:`failover` by reference, which is what keeps the lazily built fallback
        and the warned-once latch alive across the rebinding that happens on every call.

        ``tool_choice`` is deliberately not named as a parameter: it stays inside ``**kwargs`` and is
        forwarded verbatim, so this wrapper never injects a ``tool_choice=None`` the caller did not
        pass. A second call replaces the previous binding rather than merging with it, matching what
        rebinding an unwrapped model does.
        """
        return FallbackChatModel(
            primary_factory=self.primary_factory,
            fallback_factory=self.fallback_factory,
            primary_id=self.primary_id,
            fallback_id=self.fallback_id,
            failover=self.failover,
            retryable=self.retryable,
            bound_tools=tuple(tools),
            bound_kwargs=dict(kwargs),
        )

    def _bound(self, model: BaseChatModel) -> Runnable[LanguageModelInput, AIMessage]:
        """*model* with this wrapper's tool binding applied, if it has one."""
        if self.bound_tools is None:
            return model
        return model.bind_tools(list(self.bound_tools), **self.bound_kwargs)

    # -- generation ----------------------------------------------------------------------

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Answer from the primary, or from the fallback when the failure earns one."""
        try:
            message = self._bound(self.primary).invoke(messages, stop=stop, **kwargs)
        except Exception as primary_error:
            secondary = self._begin_failover(primary_error)
            try:
                message = self._bound(secondary).invoke(messages, stop=stop, **kwargs)
            except Exception as fallback_error:
                missing = self._missing_fallback(primary_error, fallback_error)
                if missing is None:
                    raise
                raise missing from fallback_error
            return _as_result(message)
        self.failover.recovered()
        return _as_result(message)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """The async half — the one production takes (RT-3). Deliberately a mirror of
        :meth:`_generate` rather than a delegation: the decision lives in the two helpers both call,
        and the only thing duplicated here is the ``await``, which cannot be shared."""
        try:
            message = await self._bound(self.primary).ainvoke(messages, stop=stop, **kwargs)
        except Exception as primary_error:
            secondary = self._begin_failover(primary_error)
            try:
                message = await self._bound(secondary).ainvoke(messages, stop=stop, **kwargs)
            except Exception as fallback_error:
                missing = self._missing_fallback(primary_error, fallback_error)
                if missing is None:
                    raise
                raise missing from fallback_error
            return _as_result(message)
        self.failover.recovered()
        return _as_result(message)

    # -- the failover decision -----------------------------------------------------------

    def _begin_failover(self, primary_error: BaseException) -> BaseChatModel:
        """The fallback model, or a raise. Called only from an ``except`` block.

        A non-retryable failure leaves here as itself — with one exception. If the *primary* is an
        Ollama model that is not pulled, the raw ``404`` is replaced by the same actionable message
        the fallback gets, because a typo'd or unpulled primary produces exactly as confusing an
        error as a missing fallback does.

        ``raise primary_error`` rather than a bare ``raise``: a bare one would re-raise whatever
        exception happens to be active in the *caller's* frame, which is the same object today and
        would stop being obviously so the first time this is called from anywhere else. CPython does
        not chain an exception to itself, so re-raising the object costs no ``__context__`` loop.
        """
        if not self.retryable(primary_error):
            missing = _not_installed("primary", self.primary_id, primary_error)
            if missing is not None:
                raise missing from primary_error
            raise primary_error
        secondary = self.fallback
        self.failover.announce(
            primary_id=self.primary_id, fallback_id=self.fallback_id, exc=primary_error
        )
        return secondary

    def _missing_fallback(
        self, primary_error: BaseException, fallback_error: BaseException
    ) -> ModelNotInstalledError | None:
        """The actionable error to raise when the safety net turned out not to be installed.

        ``None`` for every other way the fallback can fail, and the caller then re-raises bare —
        so the fallback's own error reaches the human untouched, with the primary's failure already
        on its ``__context__`` chain because it was raised while the primary's was being handled.
        """
        return _not_installed("fallback", self.fallback_id, fallback_error, because=primary_error)


# --------------------------------------------------------------------------------------
# Module-level helpers
# --------------------------------------------------------------------------------------


def _resolve(model: str | BaseChatModel) -> BaseChatModel:
    """A spec string becomes a model; a model is already one."""
    if isinstance(model, BaseChatModel):
        return model
    return init_chat_model(model)


def _as_result(message: AIMessage) -> ChatResult:
    """One message as the ``ChatResult`` ``_generate`` must return.

    ``llm_output`` is left empty on purpose: token usage and response metadata travel on the message
    itself, so nothing observable is dropped by not reconstructing the inner call's envelope.
    """
    return ChatResult(generations=[ChatGeneration(message=message)])


def _status_of(exc: BaseException) -> int | None:
    """The HTTP status behind *exc*, by duck typing rather than by provider.

    ``ollama.ResponseError`` carries ``status_code`` directly (and ``-1`` when it never saw one,
    which this rejects); ``httpx.HTTPStatusError`` carries it on ``.response``; the Anthropic and
    OpenAI SDK errors use the same ``status_code`` attribute. Reading it structurally is what lets
    :func:`is_retryable_model_error` keep working when a deployment overrides the model to another
    provider — the failover is a registry feature, not an Ollama feature.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status > 0:
        return status
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) and status > 0 else None


def _pull_command(model_id: str) -> str | None:
    """``ollama pull <model>`` for an Ollama spec, ``None`` for any other provider."""
    if not model_id.startswith(OLLAMA_PREFIX):
        return None
    return f"ollama pull {model_id.removeprefix(OLLAMA_PREFIX)}"


def _not_installed(
    role: str, model_id: str, exc: BaseException, *, because: BaseException | None = None
) -> ModelNotInstalledError | None:
    """Translate a ``404`` from an Ollama model into a sentence with the fix in it.

    *role* is ``"primary"`` or ``"fallback"`` and is named in the message, because those two are
    read at very different moments: a missing primary is a configuration mistake found at once,
    while a missing fallback is found only when the cloud quota has already died — and the human
    needs to be told which of the two they are looking at.

    ``None`` when this is not that case — a different status, or a model from another provider,
    where ``ollama pull`` would be advice that does not apply.
    """
    command = _pull_command(model_id)
    if command is None or _status_of(exc) != _MODEL_NOT_FOUND:
        return None
    detail = (
        f" It was reached for because the primary model failed with: {because}" if because else ""
    )
    return ModelNotInstalledError(
        f"the {role} model {model_id!r} is not installed on this machine — "
        f"run `{command}` to pull it.{detail}"
    )
