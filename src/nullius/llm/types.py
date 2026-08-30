"""Request and response types for model calls.

Everything here is frozen and canonically hashable, because the cache key is
the reproducibility mechanism (ADR-0005). A field that affects the response
but is missing from :meth:`LlmRequest.cache_key` would let a replay return a
response that the current request would never have produced — which is worse
than no cache at all, since it would be silent.

Note what is *absent*: there is no ``temperature``, ``top_p`` or ``top_k``.
Current Claude models reject them, and determinism here comes from the cache,
not from sampling parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from nullius.util.canonical import sha256_of

__all__ = [
    "Effort",
    "LlmRequest",
    "LlmResponse",
    "Message",
    "ModelRef",
    "Usage",
]

Effort = Literal["low", "medium", "high", "xhigh", "max"]
ThinkingMode = Literal["adaptive", "disabled"]


@dataclass(frozen=True, slots=True)
class ModelRef:
    """A specific model and the knobs that change its output.

    ``model`` must be a pinned identifier, never a moving alias: a provider
    silently updating the model behind an alias would make cached responses
    unreproducible while the cache key stayed the same.
    """

    provider: str
    model: str
    max_tokens: int = 8192
    effort: Effort | None = "high"
    thinking: ThinkingMode | None = "adaptive"

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "max_tokens": self.max_tokens,
            "effort": self.effort,
            "thinking": self.thinking,
        }


@dataclass(frozen=True, slots=True)
class Message:
    """One conversational turn. Nullius sends one user turn per call."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class LlmRequest:
    """Everything that determines a response."""

    model: ModelRef
    system: str
    messages: tuple[Message, ...]
    output_schema: dict[str, Any] | None = None
    """JSON schema the response must conform to, via ``output_config.format``."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model.as_dict(),
            "system": self.system,
            "messages": [{"role": m.role, "content": m.content} for m in self.messages],
            "output_schema": self.output_schema,
        }

    def cache_key(self) -> str:
        """The content address of this request.

        Covers the provider, the pinned model, every generation parameter, the
        system prompt, the messages and the output schema. Two requests with
        the same key are the same question.
        """
        return sha256_of(self.as_dict())

    @property
    def prompt_hash(self) -> str:
        """Hash of the prompt alone, for audit without storing the text."""
        return sha256_of({"system": self.system, "messages": self.as_dict()["messages"]})


@dataclass(frozen=True, slots=True)
class Usage:
    """Token accounting, in the provider's own categories.

    Cached reads and cache writes are priced differently from ordinary input
    tokens, so they are counted separately rather than folded together.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total_input(self) -> int:
        return self.input_tokens + self.cache_read_input_tokens + self.cache_creation_input_tokens

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
        }


@dataclass(frozen=True, slots=True)
class LlmResponse:
    """A model response, plus what it cost and where it came from."""

    text: str
    model: str
    stop_reason: str
    usage: Usage = field(default_factory=Usage)
    structured: dict[str, Any] | None = None
    cache_hit: bool = False
    """True when served from the response cache. A cache hit costs nothing and
    is *not* an independent sample — see ADR-0005."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "model": self.model,
            "stop_reason": self.stop_reason,
            "usage": self.usage.as_dict(),
            "structured": self.structured,
        }

    @property
    def response_hash(self) -> str:
        return sha256_of(self.as_dict())

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, cache_hit: bool = False) -> LlmResponse:
        usage = payload.get("usage") or {}
        return cls(
            text=payload["text"],
            model=payload["model"],
            stop_reason=payload["stop_reason"],
            usage=Usage(**usage),
            structured=payload.get("structured"),
            cache_hit=cache_hit,
        )
