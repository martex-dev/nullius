"""M19: the reproducibility claim, verified rather than asserted.

The README says results trace back to hashed artifacts and the repository
rebuilds from a clean clone. Until M15 that claim was **false for every
custodied arm**: identifiers were random UUIDs, so a registration id — and
through it the Custodian's evaluation seed — differed on every run, and no
custodied result could be reproduced at all. It was true only for the arms that
never query the Custodian, which is why running the ladder twice left B0 to B3
identical and moved everything above them.

These tests pin what is now true, and just as importantly what is not: the
science reproduces exactly, and the cost does not, because part of the cost is
measured in seconds that were actually burned.

They are marked slow because each one carries bank items through the real
compiler, the real sandbox and the real Custodian. Nothing here is mocked
except the prose.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from nullius.bank.items import BANK_V2
from nullius.bank.lock import V2_LOCK_PATH
from nullius.benchmark.arms import arm_named
from nullius.benchmark.runner import ArmOutcome, ArmRun, run_arm

#: Fields whose value is a fact about the world or the ledger, and which must
#: therefore be identical between two runs of the same arm.
SCIENTIFIC_FIELDS = (
    "arm_id",
    "item_id",
    "verdict",
    "truth_verdict",
    "true_effect",
    "realised_effect",
    "boundary_margin",
    "confidence",
    "n_seeds",
    "replications",
    "findings",
    "correct",
    "abstained",
    "replicate",
    "halted",
)

#: How far the two runs' costs may differ. Compute is billed from wall-clock
#: seconds, so this is scheduler noise and cannot be driven to zero. Observed at
#: about 0.2%; the bound is loose enough not to be flaky and tight enough that a
#: real change in what the arm executed would break it.
COST_TOLERANCE = Decimal("0.02")


def _twice(arm_id: str, item_ids: tuple[str, ...], root: Path) -> tuple[ArmRun, ArmRun]:
    items = [i for i in BANK_V2 if i.item_id in item_ids]
    return tuple(  # type: ignore[return-value]
        run_arm(
            arm_named(arm_id),
            database=root / f"{attempt}.sqlite",
            workroot=root / attempt,
            items=items,
            truth_lock=V2_LOCK_PATH,
        )
        for attempt in ("first", "second")
    )


def _compare(first: ArmRun, second: ArmRun) -> None:
    assert len(first.outcomes) == len(second.outcomes)
    for a, b in zip(first.outcomes, second.outcomes, strict=True):
        for field in SCIENTIFIC_FIELDS:
            assert getattr(a, field) == getattr(b, field), f"{a.item_id}.{field}"
        assert _relative(a, b) < COST_TOLERANCE, f"{a.item_id}: cost moved more than noise"


def _relative(a: ArmOutcome, b: ArmOutcome) -> Decimal:
    if not a.usd:
        return Decimal(0)
    return abs(a.usd - b.usd) / a.usd


@pytest.mark.slow
def test_a_custodied_arm_reproduces_exactly(tmp_path: Path) -> None:
    """The case that was broken until M15.

    B4 is the first rung that queries the Custodian, whose evaluation seed is
    derived from the registration id. With random identifiers that seed changed
    on every run; with a stream seeded by arm and replicate it does not, and the
    verdict and the realised effect come back bit-identical.
    """
    _compare(*_twice("B4", ("C12", "C28", "C40"), tmp_path))


@pytest.mark.slow
def test_an_uncustodied_arm_reproduces_exactly(tmp_path: Path) -> None:
    """The control. B3 reads the development split, which is fixed by seeds
    derived from the item id, so it was already reproducible before M15 and must
    remain so."""
    _compare(*_twice("B3", ("C12", "C28", "C40"), tmp_path))


@pytest.mark.slow
def test_an_adaptive_arm_reproduces_including_how_much_it_bought(tmp_path: Path) -> None:
    """Escalation decides from the development split, so the decision itself has
    to be reproducible — not just the verdict it leads to. An arm that bought a
    different number of seeds on the second run would be reproducible in its
    answer and not in its reasoning."""
    first, second = _twice("B8", ("C28", "C30"), tmp_path)
    _compare(first, second)
    assert [o.n_seeds for o in first.outcomes] == [o.n_seeds for o in second.outcomes]


@pytest.mark.slow
def test_cost_is_the_one_thing_that_need_not_reproduce(tmp_path: Path) -> None:
    """And it is honest that it need not.

    Token counts are reproducible and priced at a fixed table. Compute is billed
    from wall-clock seconds actually consumed, which no amount of seeding makes
    deterministic. Reporting it as reproducible would be the more comfortable
    lie; the alternative — dropping compute from the cost — would make the
    economy measure only half of what a run spends.

    The assertion is a bound, not an inequality. Two runs *may* land on the same
    cost, and a test that demanded they differ would be asserting that noise
    exists — true almost always, and flaky exactly when it is not.
    """
    first, second = _twice("B4", ("C12",), tmp_path)
    a, b = first.outcomes[0], second.outcomes[0]

    assert a.verdict is b.verdict
    assert a.realised_effect == b.realised_effect
    assert _relative(a, b) < COST_TOLERANCE
