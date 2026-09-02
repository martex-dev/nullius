"""Surviving the network for the length of a ladder.

A full ladder makes thousands of calls over several hours. Across that many,
at least one rate limit, overload or dropped connection is a certainty rather
than a risk — and until this module existed a single one of them raised
straight out of :meth:`~nullius.runtime.worker.Worker.execute` and killed the
run, after every call up to that point had already been paid for.

**A decorator, not a change to the worker.** The worker's loop handles schema
failures, which are a different thing: a malformed response is evidence about
the prompt and is repaired by asking again with the error attached. A 429 is
evidence about nothing except traffic. Keeping them apart means the repair
budget is not consumed by weather, and it means retry composes with the cache
— wrap the live provider, then wrap that in
:class:`~nullius.llm.providers.CachingProvider`, and a retried call is written
to the cache exactly once.

**What is not retried.** A 400, a 401, an unparseable request: these fail the
same way however often they are sent, and retrying them turns a clear error
into a slow one. So does a refusal, which is a real answer from the model and
is handled as an outcome rather than an error.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from nullius.llm.types import LlmRequest, LlmResponse

__all__ = [
    "DEFAULT_BACKOFF",
    "TRANSIENT_STATUS",
    "Backoff",
    "RetryingProvider",
    "is_transient",
]

#: HTTP statuses worth trying again. 429 is a rate limit, 529 is Anthropic's
#: "overloaded", and the 5xx range is the server saying the failure is its own.
#: 408 and 409 are included because a request timeout and a conflict both
#: resolve on their own; 4xx is otherwise deliberately absent, since a 400 or a
#: 401 fails identically however many times it is sent.
TRANSIENT_STATUS: frozenset[int] = frozenset({408, 409, 429, 500, 502, 503, 504, 529})

#: Exception type names treated as transient without inspecting a status code.
#: Matched by name so that this module does not import the Anthropic SDK: the
#: package is an optional extra, and a retry policy that cannot be loaded
#: without it would be absent exactly where the mock is being used to stand in
#: for a network that is not there.
TRANSIENT_NAMES: frozenset[str] = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "OverloadedError",
        "RateLimitError",
        "ServiceUnavailableError",
    }
)


def is_transient(error: BaseException) -> bool:
    """Whether this failure is worth trying again.

    Three ways to qualify, in order of how much they can be trusted: a status
    code in :data:`TRANSIENT_STATUS`, membership of the builtin timeout and
    connection families, or a type name in :data:`TRANSIENT_NAMES`.

    The default is *no*. An unrecognised error is raised rather than retried,
    because turning an unknown failure into six identical unknown failures
    separated by sleeps helps nobody and hides the original.
    """
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status in TRANSIENT_STATUS
    if isinstance(error, TimeoutError | ConnectionError):
        return True
    return type(error).__name__ in TRANSIENT_NAMES


@dataclass(frozen=True, slots=True)
class Backoff:
    """Exponential backoff with full jitter.

    Full jitter — a uniform draw from ``[0, delay]`` rather than the delay
    itself — because a ladder runs its arms in sequence against one endpoint,
    and a fleet of clients that all back off by exactly the same doubling
    sequence re-collides on every attempt. The randomness is what spreads them.
    """

    attempts: int = 6
    base_seconds: float = 1.0
    max_seconds: float = 60.0

    def delays(self, rng: random.Random | None = None) -> Iterator[float]:
        """The wait before each retry, shortest first."""
        source = rng or random
        for attempt in range(self.attempts - 1):
            ceiling = min(self.max_seconds, self.base_seconds * (2.0**attempt))
            yield source.uniform(0.0, ceiling)

    @property
    def worst_case_seconds(self) -> float:
        """The longest a single call can stall, for a caller sizing a run."""
        return sum(
            min(self.max_seconds, self.base_seconds * (2.0**attempt))
            for attempt in range(self.attempts - 1)
        )


DEFAULT_BACKOFF = Backoff()


@dataclass(slots=True)
class RetryingProvider:
    """Wraps a provider so transient network failures do not end a run."""

    inner: object
    backoff: Backoff = DEFAULT_BACKOFF
    sleep: Callable[[float], None] = time.sleep
    rng: random.Random | None = None
    name: str = field(init=False)

    #: Every retry that happened, as ``(attempt, error)``. Kept so a run can
    #: report how much weather it survived instead of only whether it finished.
    retries: list[tuple[int, str]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.name = getattr(self.inner, "name", "unknown")

    def complete(self, request: LlmRequest) -> LlmResponse:
        complete = self.inner.complete  # type: ignore[attr-defined]
        delays = list(self.backoff.delays(self.rng))
        last: BaseException | None = None

        for attempt, delay in enumerate([*delays, None]):
            try:
                return complete(request)  # type: ignore[no-any-return]
            except BaseException as error:
                if not is_transient(error) or delay is None:
                    raise
                last = error
                self.retries.append((attempt + 1, f"{type(error).__name__}: {error}"))
                self.sleep(delay)

        # Unreachable: the final iteration has ``delay is None`` and re-raises.
        raise AssertionError(last)
