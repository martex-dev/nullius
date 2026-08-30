"""The live Anthropic provider.

Kept deliberately small. Everything interesting — caching, cost accounting,
validation, retries with a repair turn — lives in layers above, so this file
does one thing: translate an :class:`~nullius.llm.types.LlmRequest` into a
Messages API call and translate the answer back.

Three API details this depends on, all of which changed recently enough to be
worth stating:

- **Structured output** goes in ``output_config.format``. The older
  ``output_format`` parameter is deprecated.
- **Thinking** is ``{"type": "adaptive"}``. ``budget_tokens`` is rejected with
  a 400 on current models.
- **No sampling parameters.** ``temperature`` / ``top_p`` / ``top_k`` are
  rejected on current models, which is why :class:`ModelRef` has none.

The SDK is an optional dependency, so the import is deferred: the package must
remain installable and fully testable with no provider SDK present.
"""

from __future__ import annotations

import json
from typing import Any

from nullius.errors import NulliusError
from nullius.llm.types import LlmRequest, LlmResponse, Usage

__all__ = ["AnthropicProvider", "ProviderRefusal"]


class ProviderRefusal(NulliusError):
    """The model declined the request.

    A first-class outcome rather than an exception to swallow: a role whose
    task is consistently refused is a fact about the institution's prompts,
    and it belongs in the ledger.
    """


class AnthropicProvider:
    """Calls the Anthropic Messages API."""

    name = "anthropic"

    __slots__ = ("_client",)

    def __init__(self, client: Any = None) -> None:
        if client is not None:
            self._client = client
            return
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised by absence
            raise NulliusError(
                "the anthropic SDK is not installed; install the 'anthropic' extra "
                "or use MockProvider / ReplayProvider"
            ) from exc
        # Zero-arg construction resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN
        # or an `ant auth login` profile, in that order.
        self._client = anthropic.Anthropic()

    def complete(self, request: LlmRequest) -> LlmResponse:
        payload: dict[str, Any] = {
            "model": request.model.model,
            "max_tokens": request.model.max_tokens,
            "system": request.system,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        }

        output_config: dict[str, Any] = {}
        if request.model.effort is not None:
            output_config["effort"] = request.model.effort
        if request.output_schema is not None:
            output_config["format"] = {
                "type": "json_schema",
                "schema": request.output_schema,
            }
        if output_config:
            payload["output_config"] = output_config

        if request.model.thinking is not None:
            payload["thinking"] = {"type": request.model.thinking}

        message = self._client.messages.create(**payload)

        if getattr(message, "stop_reason", None) == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None)
            raise ProviderRefusal(
                f"model {request.model.model} declined the request"
                + (f" (category {category})" if category else "")
            )

        text = "".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        )

        structured: dict[str, Any] | None = None
        if request.output_schema is not None and text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                structured = None
            else:
                structured = parsed if isinstance(parsed, dict) else None

        usage = getattr(message, "usage", None)
        return LlmResponse(
            text=text,
            model=getattr(message, "model", request.model.model),
            stop_reason=getattr(message, "stop_reason", "end_turn") or "end_turn",
            usage=Usage(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            ),
            structured=structured,
        )
