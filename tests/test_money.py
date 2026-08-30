"""Money: exact amounts, and the scale bug that made the ledger lie."""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from nullius.db.tables import Money, Program
from nullius.ledger.rebuild import reconciliation
from nullius.llm.pricing import PRICES, price_of, usd_for
from nullius.llm.types import Usage
from nullius.repository import Repository
from tests.conftest import Scaffold


def test_prices_match_the_published_rates() -> None:
    """Pinned. A silent rate change must break a test, not a budget."""
    assert price_of("claude-opus-5").input_usd_per_mtok == Decimal("5.00")
    assert price_of("claude-opus-5").output_usd_per_mtok == Decimal("25.00")
    assert price_of("claude-sonnet-5").input_usd_per_mtok == Decimal("2.00")
    assert price_of("claude-haiku-4-5").output_usd_per_mtok == Decimal("5.00")
    assert price_of("claude-fable-5").output_usd_per_mtok == Decimal("50.00")


def test_an_unpriced_model_raises_rather_than_reporting_free() -> None:
    """Reporting $0.00 for an unpriced call would misstate the one number
    the research economy exists to measure."""
    with pytest.raises(KeyError, match="no price for model"):
        usd_for("some-unlisted-model", Usage(input_tokens=1000))


def test_cost_is_exact() -> None:
    # 1M input at $5 + 1M output at $25
    assert usd_for("claude-opus-5", Usage(1_000_000, 1_000_000)) == Decimal(30)
    # 100k input at $5/Mtok = $0.50
    assert usd_for("claude-opus-5", Usage(input_tokens=100_000)) == Decimal("0.5")


def test_cached_input_is_cheaper_and_cache_writes_dearer() -> None:
    plain = usd_for("claude-opus-5", Usage(input_tokens=1_000_000))
    cached = usd_for("claude-opus-5", Usage(cache_read_input_tokens=1_000_000))
    written = usd_for("claude-opus-5", Usage(cache_creation_input_tokens=1_000_000))

    assert cached == plain / 10
    assert written == plain * Decimal("1.25")


def test_a_cache_hit_costs_nothing() -> None:
    """No request was made. This is the whole economic argument for replay."""
    usage = Usage(input_tokens=500_000, output_tokens=50_000)
    assert usd_for("claude-opus-5", usage, cache_hit=True) == Decimal(0)
    assert usd_for("claude-opus-5", usage, cache_hit=False) > 0


def test_every_priced_model_has_both_rates() -> None:
    for model, price in PRICES.items():
        assert price.input_usd_per_mtok >= 0, model
        assert price.output_usd_per_mtok >= 0, model


# ---------------------------------------------------------------------------
# Regression: Decimal equality ignores scale
# ---------------------------------------------------------------------------


def test_decimal_scale_does_not_desynchronise_the_ledger(
    repo: Repository, scaffold: Scaffold
) -> None:
    """``Decimal("0") == Decimal("0.00")``, so SQLAlchemy sees no change.

    Before Money quantised on bind, assigning a differently-scaled amount left
    the row holding the old digits while the object held the new ones — and
    the event recorded a number the database did not have. The reconciliation
    is the thing that catches it, so this test asserts against that.
    """
    program = repo.session.get(Program, scaffold.program_id)
    assert program is not None

    assert Decimal("0") == Decimal("0.00")  # the premise
    program.budget_usd = Decimal("25.0000")  # same value, different scale
    repo.session.flush()
    repo.commit()

    stored = repo.session.execute(sa.text("SELECT budget_usd FROM programs LIMIT 1")).scalar_one()
    assert str(stored) == "25.00000000", "money is stored at one canonical scale"

    assert reconciliation(repo.session).ok


def test_money_quantises_to_a_single_representation() -> None:
    money = Money()
    for equivalent in (Decimal("1"), Decimal("1.0"), Decimal("1.00000000")):
        assert money.process_bind_param(equivalent, None) == "1.00000000"


def test_money_keeps_sub_cent_precision() -> None:
    """Per-token costs land well below a cent; rounding to cents would erase them."""
    tiny = usd_for("claude-opus-5", Usage(input_tokens=1))
    assert tiny > 0
    assert Money().process_bind_param(tiny, None) == "0.00000500"
