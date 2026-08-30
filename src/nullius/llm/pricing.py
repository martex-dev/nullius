"""Model prices, versioned.

The research economy is denominated in real money (`docs/01-critique.md` A8):
a fake credit system teaches nothing about whether a research strategy is
worth its cost. So every model call is priced from this table, and the table's
version is stored on every cost row — a price change must not silently rewrite
what past research appeared to cost.

Prices are US dollars per million tokens, as published for the first-party
Anthropic API. Bedrock, Vertex and Foundry are partner-operated with separate
rates; add them as distinct provider entries rather than assuming parity.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nullius.llm.types import Usage

__all__ = ["PRICE_TABLE_VERSION", "ModelPrice", "price_of", "usd_for"]

PRICE_TABLE_VERSION = "2026-06-24"
"""Bump whenever a rate changes. Stored on every cost entry."""

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
