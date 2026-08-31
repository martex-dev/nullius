"""The runner and the scoring, tested where they could quietly lie.

The tests that matter here are not the ones that check a mean is a mean. They
are the ones that check a switch is connected: an ablation whose arms differ in
a boolean the pipeline never reads would produce eight columns of the same
experiment and look exactly like a result.
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from nullius.benchmark.arms import LADDER, arm_named
from nullius.benchmark.metrics import (
    _adjudicate,
    _paired_bootstrap,
    compare_to_baseline,
    score_arm,
)
from nullius.benchmark.protocol import read_protocol
from nullius.benchmark.runner import ArmOutcome, ArmRun, mechanisms_for
from nullius.db.enums import ClaimConfidence, Verdict

PROTOCOL = read_protocol()


def outcome(
    arm_id: str = "BX",
    item_id: str = "B01",
    *,
    verdict: Verdict = Verdict.SUPPORTED,
    truth: Verdict = Verdict.SUPPORTED,
    confidence: ClaimConfidence = ClaimConfidence.SUPPORTED,
    usd: str = "0.01",
    halted: str | None = None,
) -> ArmOutcome:
    return ArmOutcome(
        arm_id=arm_id,
        item_id=item_id,
        verdict=verdict,
        truth_verdict=truth,
        true_effect=0.05,
        realised_effect=0.05,
        boundary_margin=0.03,
        confidence=confidence,
        usd=Decimal(usd),
        n_seeds=5,
        replications=0,
        findings=0,
        halted=halted,
    )


# --------------------------------------------------------------- the switches


def test_every_arm_boolean_reaches_the_pipeline() -> None:
    """An arm field that no switch consumes is an ablation that ablates nothing."""
    for arm in LADDER:
        mechanisms = mechanisms_for(arm)
        assert mechanisms.custody is arm.custodian
        assert mechanisms.preregistered is arm.preregistered
        assert mechanisms.adversary is arm.adversary
        assert mechanisms.replication is arm.replication
        assert mechanisms.memory is arm.memory


def test_the_memory_ablation_differs_in_memory_and_nothing_else() -> None:
    """B6 against B7 is the whole claim about memory. It must be a clean pair."""
    six = mechanisms_for(arm_named("B6"))
    seven = mechanisms_for(arm_named("B7"))
    assert six.memory is True
    assert seven.memory is False
    for field in ("custody", "preregistered", "adversary", "replication"):
        assert getattr(six, field) == getattr(seven, field), field


# ------------------------------------------------------ what an outcome means


def test_a_halted_item_counts_as_wrong_rather_than_missing() -> None:
    """The protocol's first exclusion rule, enforced rather than described.

    Dropping an unanswered item would pay an arm for refusing the questions it
    found hardest.
    """
    halted = outcome(
        verdict=Verdict.INCONCLUSIVE, truth=Verdict.SUPPORTED, halted="every seed failed"
    )
    assert halted.correct is False

    run = ArmRun(arm=arm_named("B4"), outcomes=(halted, outcome()))
    metrics = score_arm(run, PROTOCOL)
    assert metrics.n_items == 2
    assert metrics.n_halted == 1
    assert metrics.verdict_accuracy == 0.5


def test_abstaining_is_not_a_discovery() -> None:
    """Only an asserted effect can be a false one."""
    assert outcome(verdict=Verdict.INCONCLUSIVE).claimed_an_effect is False
    assert outcome(verdict=Verdict.NO_EFFECT).claimed_an_effect is False
    assert outcome(verdict=Verdict.SUPPORTED).claimed_an_effect is True
    assert outcome(verdict=Verdict.SUPPORTED, truth=Verdict.NO_EFFECT).false_discovery is True
    assert outcome(verdict=Verdict.NO_EFFECT, truth=Verdict.NO_EFFECT).false_discovery is False


def test_an_arm_that_is_never_right_has_an_undefined_cost_per_claim() -> None:
    """Undefined, not infinite, and not zero.

    Zero would rank a useless arm first on the cost metric.
    """
    run = ArmRun(
        arm=arm_named("B4"),
        outcomes=(outcome(verdict=Verdict.NO_EFFECT, truth=Verdict.SUPPORTED),),
    )
    metrics = score_arm(run, PROTOCOL)
    assert metrics.n_correct == 0
    assert math.isnan(metrics.usd_per_correct_claim)


def test_the_confidence_mapping_comes_from_the_protocol_not_the_scorer() -> None:
    """A Brier score is a function of the translation; the translation is hashed."""
    confident_and_wrong = ArmRun(
        arm=arm_named("B4"),
        outcomes=(
            outcome(
                verdict=Verdict.SUPPORTED,
                truth=Verdict.NO_EFFECT,
                confidence=ClaimConfidence.WELL_SUPPORTED,
            ),
        ),
    )
    expected = PROTOCOL.confidence_as_probability[ClaimConfidence.WELL_SUPPORTED.value]
    assert score_arm(confident_and_wrong, PROTOCOL).brier == pytest.approx(expected**2)


# ------------------------------------------------------------- the statistics


def test_the_bootstrap_is_paired() -> None:
    """Two arms that always agree have exactly zero difference, in every resample.

    Resampling the arms independently would put spread on a difference that is
    identically zero, and the interval would stop meaning anything.
    """
    answers = [True, False, True, True, False]
    difference, low, high, _ = _paired_bootstrap(
        answers, answers, resamples=200, alpha=0.05, seed=0
    )
    assert difference == 0.0
    assert low == 0.0
    assert high == 0.0


def test_the_bootstrap_p_value_is_floored_at_one_resample() -> None:
    """1000 resamples cannot evidence p < 1/1000, and must not report that it can."""
    _, _, _, p_value = _paired_bootstrap(
        [True] * 20, [False] * 20, resamples=1000, alpha=0.05, seed=0
    )
    assert p_value == pytest.approx(2.0 / 1000)
    assert p_value > 0.0


def test_a_comparison_against_a_missing_baseline_raises() -> None:
    """Rather than silently comparing against whichever arm happened to be first."""
    with pytest.raises(ValueError, match="baseline"):
        compare_to_baseline([ArmRun(arm=arm_named("B4"), outcomes=(outcome(),))], PROTOCOL)


def test_the_correction_is_the_one_the_protocol_registered() -> None:
    runs = [
        ArmRun(
            arm=arm,
            outcomes=tuple(outcome(arm_id=arm.arm_id, item_id=f"B{i:02d}") for i in range(5)),
        )
        for arm in LADDER
    ]
    _, correction = compare_to_baseline(runs, PROTOCOL)
    assert correction.method == str(PROTOCOL.statistics["multiplicity"]).replace("-", "_")
    assert correction.alpha == PROTOCOL.statistics["alpha"]


# ------------------------------------------------------------- the prediction


def _ladder_at(b3: float, b4: float, b6: float) -> list[ArmRun]:
    runs = []
    for arm_id, accuracy in (("B3", b3), ("B4", b4), ("B6", b6)):
        n_right = round(accuracy * 10)
        outcomes = tuple(
            outcome(
                arm_id=arm_id,
                item_id=f"B{i:02d}",
                verdict=Verdict.SUPPORTED if i < n_right else Verdict.NO_EFFECT,
            )
            for i in range(10)
        )
        runs.append(ArmRun(arm=arm_named(arm_id), outcomes=outcomes))
    return runs


def test_the_registered_prediction_is_settled_by_arithmetic() -> None:
    """It must be capable of coming out false, and it must say so plainly."""
    upheld, reason = _adjudicate([score_arm(r, PROTOCOL) for r in _ladder_at(0.4, 0.8, 0.9)])
    assert upheld is True
    assert "upheld" in reason

    upheld, reason = _adjudicate([score_arm(r, PROTOCOL) for r in _ladder_at(0.4, 0.5, 0.9)])
    assert upheld is False
    assert "refuted" in reason


def test_an_incomplete_ladder_is_not_adjudicated_at_all() -> None:
    """Rather than defaulting to upheld, which is the flattering default."""
    upheld, reason = _adjudicate([score_arm(ArmRun(arm_named("B4"), (outcome(),)), PROTOCOL)])
    assert upheld is None
    assert "not adjudicable" in reason
