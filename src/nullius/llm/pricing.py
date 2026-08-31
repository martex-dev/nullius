"""Model prices, versioned.

The research economy is denominated in real money (`docs/01-critique.md` A8):
a fake credit system teaches nothing about whether a research strategy is
worth its cost. So every model call is priced from this table, and the table's
version is stored on every cost row — a price change must not silently rewrite
what past research appeared to cost.

Prices are US dollars per million tokens, as published for the first-party
Anthropic API. Bedrock, Vertex and Foundry are partner-operated with separate
rates; add them as distinct provider entries rather than assuming parity.

Compute is priced here too, and for a reason that only becomes visible at M9.
A programme run against :class:`~nullius.llm.providers.MockProvider` costs
exactly nothing in tokens, so an economy denominated in tokens alone reports
every research strategy as infinitely efficient. The experiments are real
whatever produced their design, so the seconds they burn are real, and
cost-per-correct-claim is only a ratio if the denominator can be non-zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nullius.llm.types import Usage

__all__ = [
    "COMPUTE_USD_PER_CPU_SECOND",
    "PRICE_TABLE_VERSION",
    "STORAGE_USD_PER_MB_MONTH",
    "ModelPrice",
    "price_of",
    "usd_for",
    "usd_for_compute",
]

PRICE_TABLE_VERSION = "2026-08-31"
"""Bump whenever a rate changes. Stored on every cost entry.

Bumped from ``2026-06-24`` when compute rates were added: a table that
prices more things than it used to is a different table, and a cost row
written under the old version must stay comparable to itself rather than to
whatever the current table would have said.
"""

COMPUTE_USD_PER_CPU_SECOND = Decimal("0.0000111")
"""About $0.04 per vCPU-hour — a general-purpose cloud instance, per second.

Wall-clock seconds are charged as CPU seconds because the sandbox pins one
process to one core (:class:`~nullius.execute.sandbox.SubprocessSandbox`).
When a backend arrives that runs several cores per experiment, this stops
being true and the runner must report real CPU time instead.
"""

STORAGE_USD_PER_MB_MONTH = Decimal("0.000023")
"""About $0.023 per GB-month. Artifacts are kept, so storage is a real, if small, term."""

_CACHE_READ_MULTIPLIER = Decimal("0.1")
"""Cached input is billed at roughly a tenth of the input rate."""

_CACHE_WRITE_MULTIPLIER = Decimal("1.25")
"""Writing to the cache costs slightly more than an ordinary input token."""

_PER_MILLION = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Input and output rates, in USD per million tokens."""

    input_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal


PRICES: dict[str, ModelPrice] = {
    "claude-fable-5": ModelPrice(Decimal("10.00"), Decimal("50.00")),
    "claude-opus-5": ModelPrice(Decimal("5.00"), Decimal("25.00")),
    "claude-opus-4-8": ModelPrice(Decimal("5.00"), Decimal("25.00")),
    "claude-sonnet-5": ModelPrice(Decimal("2.00"), Decimal("10.00")),
    "claude-haiku-4-5": ModelPrice(Decimal("1.00"), Decimal("5.00")),
    # Test doubles cost nothing, but must still be priced rather than special
    # cased, so that a mock-driven program produces a real (zero) cost row.
    "mock-1": ModelPrice(Decimal(0), Decimal(0)),
}


def price_of(model: str) -> ModelPrice:
    """Rates for ``model``.

    Unknown models raise rather than defaulting to zero. A research program
    that silently reports $0.00 because a model was missing from the table
    would misrepresent its own cost, which is the one number the economy
    exists to measure.
    """
    try:
        return PRICES[model]
    except KeyError:
        raise KeyError(
            f"no price for model {model!r} in price table {PRICE_TABLE_VERSION}. "
            "Add it rather than letting an unpriced call report as free."
        ) from None


def usd_for(model: str, usage: Usage, *, cache_hit: bool = False) -> Decimal:
    """Cost of one call, exactly.

    A cache hit costs nothing: no request was made. That is the whole
    economic argument for replay (ADR-0005).
    """
    if cache_hit:
        return Decimal(0)

    price = price_of(model)
    ordinary_input = Decimal(usage.input_tokens) * price.input_usd_per_mtok
    cached_input = (
        Decimal(usage.cache_read_input_tokens) * price.input_usd_per_mtok * _CACHE_READ_MULTIPLIER
    )
    written_cache = (
        Decimal(usage.cache_creation_input_tokens)
        * price.input_usd_per_mtok
        * _CACHE_WRITE_MULTIPLIER
    )
    output = Decimal(usage.output_tokens) * price.output_usd_per_mtok

    return (ordinary_input + cached_input + written_cache + output) / _PER_MILLION


def usd_for_compute(cpu_seconds: float, storage_mb: float = 0.0) -> Decimal:
    """Cost of one experiment run: the seconds it burned and the bytes it kept.

    Storage is billed for one month of retention. That is a choice rather than
    a measurement — nothing here knows how long an artifact will be kept — and
    it is stated so that a reader can see the assumption instead of inferring
    it from a number that looks derived.
    """
    if cpu_seconds < 0 or storage_mb < 0:
        raise ValueError("compute cannot be negative")
    return (
        Decimal(str(cpu_seconds)) * COMPUTE_USD_PER_CPU_SECOND
        + Decimal(str(storage_mb)) * STORAGE_USD_PER_MB_MONTH
    )
