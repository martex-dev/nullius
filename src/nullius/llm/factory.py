"""Choosing a provider, and assembling the layers around it.

One place that turns a name into a working provider, because the order of the
wrappers matters and getting it wrong is expensive rather than obviously wrong.

The live stack is ``Caching(Retrying(Anthropic))``, innermost first:

* **Retry** sits closest to the network, so a 429 is absorbed before anything
  above it sees a failure.
* **Cache** sits outside retry, so a call that succeeded on its fourth attempt
  is written to the cache once and every later run of the same request is free.
  Reversed, the cache would sit under the retry and a transient failure would
  re-enter the cache lookup on each attempt — harmless but pointless, and it
  would record nothing when the call finally succeeded.

That ordering is what makes the first live run the only one that costs money,
which is the whole argument of ADR-0005.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from nullius.environment import detect_live_provider
from nullius.errors import NulliusError
from nullius.llm.cache import ResponseCache
from nullius.llm.providers import CachingProvider, MockProvider, ReplayProvider
from nullius.llm.retry import DEFAULT_BACKOFF, Backoff, RetryingProvider

if TYPE_CHECKING:
    from nullius.llm.providers import LlmProvider

__all__ = ["PROVIDER_NAMES", "build_provider", "require_live_credentials"]

#: What ``--provider`` accepts.
PROVIDER_NAMES: tuple[str, ...] = ("mock", "anthropic", "replay")

#: The model a live run uses when the caller does not name one. Pinned rather
#: than an alias: a provider silently updating what sits behind an alias would
#: make cached responses unreproducible while the cache key stayed identical.
DEFAULT_LIVE_MODEL = "claude-sonnet-5"


def require_live_credentials(provider: str) -> None:
    """Refuse a live run that has no credentials, before anything is spent.

    ``detect_live_provider`` previously only printed a row in ``nullius
    doctor``, where a reader could note the absence and start a run anyway. A
    ladder that discovers it has no key on its first call has already built a
    database, compiled specs and burned sandbox time; one that discovers it
    here has not.
    """
    if provider != "anthropic":
        return
    if detect_live_provider() is None:
        raise NulliusError(
            "no live credentials found: set ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) "
            "before running with --provider anthropic. Nothing has been spent. "
            "Run 'nullius doctor' to see what was looked for."
        )


def build_provider(
    name: str = "mock",
    *,
    cache_dir: Path | None = None,
    responder: Any = None,
    backoff: Backoff = DEFAULT_BACKOFF,
    client: Any = None,
) -> LlmProvider:
    """Assemble the named provider with the layers a real run needs.

    ``cache_dir`` enables the response cache. It is required for ``replay`` and
    optional but strongly wanted for ``anthropic``, where it is the difference
    between paying once and paying every time.
    """
    if name not in PROVIDER_NAMES:
        raise NulliusError(f"unknown provider {name!r}; choose one of {list(PROVIDER_NAMES)}")

    if name == "mock":
        if responder is None:
            raise NulliusError("the mock provider needs a responder")
        return MockProvider(responder)

    if name == "replay":
        if cache_dir is None:
            raise NulliusError("replay needs a cache directory to replay from")
        return ReplayProvider(ResponseCache(cache_dir))

    require_live_credentials(name)
    from nullius.llm.anthropic_provider import AnthropicProvider

    live: Any = RetryingProvider(AnthropicProvider(client=client), backoff=backoff)
    if cache_dir is None:
        return live  # type: ignore[no-any-return]
    return CachingProvider(live, ResponseCache(cache_dir))
