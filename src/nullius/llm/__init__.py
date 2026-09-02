"""Model access: typed requests, a content-addressed cache, and providers.

:mod:`~nullius.llm.factory` is the way to build one. It assembles the layers a
live run needs in the order that makes the first run the only one that costs
money, and refuses a live run with no credentials before anything is spent.
"""

from __future__ import annotations

from nullius.llm.anthropic_provider import AnthropicProvider, ProviderRefusal
from nullius.llm.cache import ResponseCache
from nullius.llm.factory import PROVIDER_NAMES, build_provider, require_live_credentials
from nullius.llm.pricing import PRICE_TABLE_VERSION, usd_for, usd_for_compute
from nullius.llm.providers import (
    CachingProvider,
    LlmProvider,
    MockProvider,
    ReplayCacheMiss,
    ReplayProvider,
)
from nullius.llm.retry import Backoff, RetryingProvider, is_transient
from nullius.llm.types import Effort, LlmRequest, LlmResponse, Message, ModelRef, Usage

__all__ = [
    "PRICE_TABLE_VERSION",
    "PROVIDER_NAMES",
    "AnthropicProvider",
    "Backoff",
    "CachingProvider",
    "Effort",
    "LlmProvider",
    "LlmRequest",
    "LlmResponse",
    "Message",
    "MockProvider",
    "ModelRef",
    "ProviderRefusal",
    "ReplayCacheMiss",
    "ReplayProvider",
    "ResponseCache",
    "RetryingProvider",
    "Usage",
    "build_provider",
    "is_transient",
    "require_live_credentials",
    "usd_for",
    "usd_for_compute",
]
