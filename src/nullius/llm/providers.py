"""Providers, and the caching wrapper that makes programs replayable.

Four implementations of one interface:

``MockProvider``
    Deterministic canned responses. Unit tests.
``ReplayProvider``
    Cache only. A miss is a hard error, never a silent live call — otherwise a
    "replay" could quietly spend money and produce a different program.
``AnthropicProvider``
    The live path (in :mod:`nullius.llm.anthropic_provider`).
``CachingProvider``
    Wraps any of the above. Reads through, writes back, and marks hits.

The interface is deliberately thin (ADR-0003). Its one real job is to make
model diversity across adversarial roles possible, since a Skeptic sharing a
base model with the Theorist inherits its blind spots (`docs/01-critique.md`
F8).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from nullius.errors import NulliusError
from nullius.llm.cache import ResponseCache
from nullius.llm.types import LlmRequest, LlmResponse, Usage

__all__ = [
    "CachingProvider",
    "LlmProvider",
    "MockProvider",
    "ReplayCacheMiss",
    "ReplayProvider",
]


class ReplayCacheMiss(NulliusError):
    """A replay needed a response that was never recorded.

    Deliberately fatal. Falling through to a live call would make a replay
    silently non-reproducible and unexpectedly billable.
    """


@runtime_checkable
class LlmProvider(Protocol):
    """Anything that can answer an :class:`LlmRequest`."""

    name: str

    def complete(self, request: LlmRequest) -> LlmResponse:
        """Answer ``request``."""
        ...


class MockProvider:
    """Deterministic canned responses, for tests and offline development.

    Responses are chosen by a caller-supplied function of the request, so a
    test can model a role that answers differently depending on what it was
    asked — including answering *badly*, which is what the repair path and the
    validator failures need to be exercised.
    """

    name = "mock"

    __slots__ = ("_calls", "_responder")

    def __init__(
        self,
        responder: Callable[[LlmRequest], LlmResponse | dict[str, Any] | str],
    ) -> None:
        self._responder = responder
        self._calls: list[LlmRequest] = []

    @property
    def calls(self) -> list[LlmRequest]:
        """Every request seen, in order. Lets a test assert what a role saw."""
        return list(self._calls)

    def complete(self, request: LlmRequest) -> LlmResponse:
        self._calls.append(request)
        answer = self._responder(request)

        if isinstance(answer, LlmResponse):
            return answer
        if isinstance(answer, dict):
            import json

            text = json.dumps(answer, sort_keys=True)
            return LlmResponse(
                text=text,
                model=request.model.model,
                stop_reason="end_turn",
                usage=Usage(input_tokens=len(request.system) // 4, output_tokens=len(text) // 4),
                structured=answer,
            )
        return LlmResponse(
            text=answer,
            model=request.model.model,
            stop_reason="end_turn",
            usage=Usage(input_tokens=len(request.system) // 4, output_tokens=len(answer) // 4),
        )


class ReplayProvider:
    """Serves only what was recorded. A miss is an error."""

    name = "replay"

    __slots__ = ("_cache",)

    def __init__(self, cache: ResponseCache) -> None:
        self._cache = cache

    def complete(self, request: LlmRequest) -> LlmResponse:
        recorded = self._cache.get(request.cache_key())
        if recorded is None:
            raise ReplayCacheMiss(
                f"no recorded response for {request.cache_key()[:16]}… "
                f"(model {request.model.model}). A replay must not fall through to a "
                "live call: that would make the run neither reproducible nor free."
            )
        return recorded


class CachingProvider:
    """Read-through cache around another provider."""

    __slots__ = ("_cache", "_inner", "name")

    def __init__(self, inner: LlmProvider, cache: ResponseCache) -> None:
        self._inner = inner
        self._cache = cache
        self.name = inner.name

    def complete(self, request: LlmRequest) -> LlmResponse:
        key = request.cache_key()
        recorded = self._cache.get(key)
        if recorded is not None:
            return recorded

        response = self._inner.complete(request)
        self._cache.put(key, request.as_dict(), response)
        return response
