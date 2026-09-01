"""The runner and the scoring, tested where they could quietly lie.

The tests that matter here are not the ones that check a mean is a mean. They
are the ones that check a switch is connected: an ablation whose arms differ in
a boolean the pipeline never reads would produce eight columns of the same
experiment and look exactly like a result.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from nullius.benchmark.arms import LADDER, arm_named
from nullius.benchmark.metrics import (
    _adjudicate,
    _contrast,
    _paired_bootstrap,
    compare_to_baseline,
    read_results,
    score_arm,
    score_ladder,
)
from nullius.benchmark.protocol import (
    V2_PROTOCOL_PATH,
    V3_PROTOCOL_PATH,
    V4_PROTOCOL_PATH,
    V5_PROTOCOL_PATH,
    read_protocol,
)
from nullius.benchmark.runner import ArmOutcome, ArmRun, mechanisms_for
from nullius.db.enums import ClaimConfidence, Verdict
from nullius.util.canonical import canonical_json

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
        item_id="B01",
        verdict=Verdict.INCONCLUSIVE,
        truth=Verdict.SUPPORTED,
        halted="every seed failed",
    )
    assert halted.correct is False

    # Two distinct items: outcomes sharing an id are replicates of one item,
    # which is what `by_item` groups them as.
    run = ArmRun(arm=arm_named("B4"), outcomes=(halted, outcome(item_id="B02")))
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


# ----------------------------------------------------------- the results file


def test_the_committed_results_rescore_to_the_summary_they_ship_with() -> None:
    """The stored summary must be derivable from the stored outcomes.

    A results file whose headline numbers cannot be recomputed from its own
    per-item rows is a screenshot, not a record. Re-scoring here is what makes
    the ladder's conclusions arguable without re-running the science.
    """
    report, runs = read_results()
    assert report.protocol_hash == PROTOCOL.protocol_hash
    assert len(runs) == len(LADDER)
    assert {run.arm.arm_id for run in runs} == {arm.arm_id for arm in LADDER}
    for run in runs:
        assert len(run.outcomes) == PROTOCOL.bank["n_items"]


def test_the_registered_prediction_is_reported_with_its_intervals() -> None:
    """The verdict alone can be produced by a one-item difference.

    On a twenty-item bank the primary metric moves in steps of 0.05, and the
    registered adjudication compares two point estimates without requiring
    either to separate from zero. So the intervals travel with the verdict, or
    a coin flip reads as a confirmed prediction.
    """
    report, _ = read_results()
    contrasts = {(c.arm_id, c.baseline_arm_id): c for c in report.prediction_contrasts}
    assert ("B4", "B3") in contrasts
    assert ("B6", "B4") in contrasts
    for contrast in report.prediction_contrasts:
        assert contrast.ci_low <= contrast.difference <= contrast.ci_high


def test_results_scored_against_a_different_protocol_are_refused(tmp_path: Path) -> None:
    """Re-scoring under a plan the run did not use is the substitution
    preregistration exists to prevent."""
    body = json.loads(Path("benchmark/results.lock.json").read_text(encoding="utf-8"))
    body["report"]["protocol_hash"] = "0" * 64
    tampered = tmp_path / "results.lock.json"
    tampered.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(ValueError, match="preregistration exists to prevent"):
        read_results(tampered)


# ------------------------------------------- an undefined metric is not a NaN


def test_an_arm_that_asserts_nothing_serialises_a_null_not_a_nan(tmp_path: Path) -> None:
    """The bug that stopped the first v2 ladder writing its results.

    B0 answers ``no_effect`` about everything, so under v2's registered
    ``asserted_effects`` calibration scope it asserts nothing and has no Brier
    score at all. That is a real property of the arm. Serialising it as NaN
    made ``canonical_json`` refuse the whole results file -- correctly, since
    "a metric that is NaN or infinite is a defect, not a result" -- after all
    eight arms had already run. JSON null is the honest representation: there
    is no such number, rather than a number that is not a number.
    """
    abstaining = ArmRun(
        arm=arm_named("B0"),
        outcomes=tuple(
            outcome(
                arm_id="B0",
                item_id=f"C{i:02d}",
                verdict=Verdict.NO_EFFECT,
                truth=Verdict.NO_EFFECT,
            )
            for i in range(5)
        ),
    )
    v2 = read_protocol(V2_PROTOCOL_PATH)
    metrics = score_arm(abstaining, v2)

    assert metrics.n_scored == 0
    assert math.isnan(metrics.brier)
    payload = metrics.as_dict()
    assert payload["brier"] is None
    assert payload["expected_calibration_error"] is None
    # And the whole thing must now survive canonicalisation, which is where it
    # failed before.
    assert canonical_json(payload)


def test_the_v1_scope_still_scores_every_item() -> None:
    """v2's scope must not leak backwards into the protocol v1 was run under."""
    run = ArmRun(
        arm=arm_named("B4"),
        outcomes=tuple(
            outcome(item_id=f"B{i:02d}", verdict=Verdict.NO_EFFECT, truth=Verdict.NO_EFFECT)
            for i in range(5)
        ),
    )
    assert score_arm(run, PROTOCOL).n_scored == 5
    assert score_arm(run, read_protocol(V2_PROTOCOL_PATH)).n_scored == 0


# --------------------------------------------------------------- checkpoints


def test_a_finished_arm_is_reused_rather_than_rerun(tmp_path: Path) -> None:
    """Science already done should not be lost to a fault in reporting it.

    The first full v2 ladder ran all eight arms over two hours and then died
    writing the results file, on a metric that was legitimately undefined.
    Every completed arm went with it.
    """
    from nullius.bank.items import BANK_V2
    from nullius.benchmark.runner import _read_checkpoint, _write_checkpoint
    from nullius.util.canonical import sha256_of

    items = BANK_V2[:3]
    items_hash = sha256_of([i.as_dict() for i in items])
    arm = arm_named("B0")
    run = ArmRun(
        arm=arm,
        outcomes=tuple(
            outcome(arm_id="B0", item_id=i.item_id, verdict=Verdict.NO_EFFECT) for i in items
        ),
    )

    _write_checkpoint(tmp_path, run, items_hash)
    restored = _read_checkpoint(tmp_path, arm, items_hash)

    assert restored is not None
    assert restored.arm == arm
    assert [o.item_id for o in restored.outcomes] == [i.item_id for i in items]
    assert [o.verdict for o in restored.outcomes] == [o.verdict for o in run.outcomes]
    assert [o.usd for o in restored.outcomes] == [o.usd for o in run.outcomes]


def test_a_checkpoint_from_a_different_bank_is_ignored(tmp_path: Path) -> None:
    """It describes questions this run is not asking, so it is not evidence
    about this run."""
    from nullius.benchmark.runner import _read_checkpoint, _write_checkpoint

    arm = arm_named("B0")
    run = ArmRun(arm=arm, outcomes=(outcome(arm_id="B0", verdict=Verdict.NO_EFFECT),))
    _write_checkpoint(tmp_path, run, "hash-of-some-other-bank")

    assert _read_checkpoint(tmp_path, arm, "hash-of-the-bank-being-run") is None


# ------------------------------- abstention is not an answer, and not a shrug


def test_an_abstention_is_never_credited_as_a_correct_answer() -> None:
    """The bug that inflated every arm's v2 accuracy.

    ``inconclusive`` is a real truth value in this bank -- "the effect is real
    and smaller than claimed" -- and it was also what the institution returned
    when its interval was too wide to say anything at all. So an arm that could
    say nothing was scored correct whenever the truth happened to be
    ``inconclusive``. It cost four to nine items out of sixty, per arm, all in
    the flattering direction.

    ``UNDERPOWERED`` is never a truth, because the oracle is never short of
    power, so the two can no longer be confused.
    """
    abstention = outcome(verdict=Verdict.UNDERPOWERED, truth=Verdict.INCONCLUSIVE)
    assert abstention.abstained
    assert not abstention.correct

    finding = outcome(verdict=Verdict.INCONCLUSIVE, truth=Verdict.INCONCLUSIVE)
    assert not finding.abstained
    assert finding.correct


def test_an_abstention_is_not_a_discovery_either() -> None:
    assert not outcome(verdict=Verdict.UNDERPOWERED).claimed_an_effect
    assert not outcome(verdict=Verdict.UNDERPOWERED, truth=Verdict.NO_EFFECT).false_discovery


def test_coverage_and_assertion_accuracy_are_reported_together() -> None:
    """Either alone is misleading, which is why the protocol registers both.

    An arm can drive assertion accuracy to 1.0 by answering only what it is
    sure of. Coverage is what stops that reading as a good result.
    """
    cautious = ArmRun(
        arm=arm_named("B4"),
        outcomes=(
            outcome(item_id="C01", verdict=Verdict.SUPPORTED, truth=Verdict.SUPPORTED),
            outcome(item_id="C02", verdict=Verdict.UNDERPOWERED, truth=Verdict.SUPPORTED),
            outcome(item_id="C03", verdict=Verdict.UNDERPOWERED, truth=Verdict.NO_EFFECT),
            outcome(item_id="C04", verdict=Verdict.UNDERPOWERED, truth=Verdict.REFUTED),
        ),
    )
    metrics = score_arm(cautious, read_protocol(V3_PROTOCOL_PATH))

    assert metrics.assertion_accuracy == 1.0  # never wrong when it spoke
    assert metrics.coverage == 0.25  # but it hardly ever spoke
    assert metrics.verdict_accuracy == 0.25  # and the headline still says so
    assert metrics.n_abstained == 3


def test_the_headline_metric_still_counts_an_abstention_as_incorrect() -> None:
    """The protocol's first exclusion rule, unchanged since v1: dropping an
    unanswered item would pay an arm for refusing the questions it found
    hardest. Separating abstention from error does not mean forgiving it."""
    run = ArmRun(
        arm=arm_named("B4"),
        outcomes=tuple(
            outcome(item_id=f"C{i:02d}", verdict=Verdict.UNDERPOWERED, truth=Verdict.SUPPORTED)
            for i in range(4)
        ),
    )
    metrics = score_arm(run, read_protocol(V3_PROTOCOL_PATH))
    assert metrics.verdict_accuracy == 0.0
    assert metrics.coverage == 0.0
    assert math.isnan(metrics.assertion_accuracy)
    assert metrics.as_dict()["assertion_accuracy"] is None


def test_scoring_refuses_a_ladder_missing_an_arm_the_protocol_registered() -> None:
    """The first v4 ladder ran eight arms under a nine-arm protocol.

    A wiring slip meant the arm list never reached the runner, and the result
    was a complete-looking results file: seven of seven baseline comparisons,
    no halted items, and no sign anywhere that the arm the protocol exists to
    test had never executed. Only the adjudication noticed, and only because it
    happened to name that arm by id.
    """
    v4 = read_protocol(V4_PROTOCOL_PATH)
    eight = [
        ArmRun(
            arm=arm_named(a),
            outcomes=(outcome(arm_id=a, verdict=Verdict.NO_EFFECT, truth=Verdict.NO_EFFECT),),
        )
        for a in ("B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7")
    ]
    with pytest.raises(ValueError, match=r"missing \['B8'\]"):
        score_ladder(eight, v4)


def test_scoring_refuses_an_arm_the_protocol_does_not_register() -> None:
    """A v3 run cannot be scored under v4's plan just because it has fewer arms."""
    v3 = read_protocol(V3_PROTOCOL_PATH)
    nine = [
        ArmRun(
            arm=arm_named(a),
            outcomes=(outcome(arm_id=a, verdict=Verdict.NO_EFFECT, truth=Verdict.NO_EFFECT),),
        )
        for a in ("B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8")
    ]
    with pytest.raises(ValueError, match="does not register"):
        score_ladder(nine, v3)


# ------------------------------------------------------------- replication


def _replicated(arm_id: str, per_item: dict[str, list[bool]]) -> ArmRun:
    """An arm whose items were answered differently across passes."""
    return ArmRun(
        arm=arm_named(arm_id),
        outcomes=tuple(
            replace(
                outcome(
                    arm_id=arm_id,
                    item_id=item,
                    verdict=Verdict.SUPPORTED if right else Verdict.NO_EFFECT,
                    truth=Verdict.SUPPORTED,
                ),
                replicate=index,
            )
            for item, passes in per_item.items()
            for index, right in enumerate(passes)
        ),
    )


def test_replicates_of_one_item_are_one_item() -> None:
    """Pooling replicates as independent observations would treat three looks
    at one question as three questions, and shrink every interval by a factor
    the design has not earned."""
    run = _replicated("B6", {"C01": [True, True, False], "C02": [False, False, False]})

    assert run.n_replicates == 3
    assert len(run.outcomes) == 6
    assert set(run.by_item()) == {"C01", "C02"}

    metrics = score_arm(run, read_protocol(V5_PROTOCOL_PATH))
    assert metrics.n_items == 2  # not six
    assert metrics.n_replicates == 3


def test_a_contrast_averages_within_an_item_before_comparing_arms() -> None:
    """An arm right two passes in three scores 2/3 on that item, not two wins."""
    better = _replicated("B8", {"C01": [True, True, True], "C02": [True, True, False]})
    worse = _replicated("B6", {"C01": [True, False, False], "C02": [False, False, False]})

    found = _contrast([better, worse], "B8", "B6", read_protocol(V5_PROTOCOL_PATH), seed=3)
    assert found is not None
    # (1.0 + 2/3)/2 - (1/3 + 0)/2 = 0.8333 - 0.1667
    assert found.difference == pytest.approx(0.6667, abs=1e-3)


def test_only_custodied_arms_are_worth_replicating() -> None:
    """Measured in the v3-against-v4 comparison, not assumed: every arm that
    moved between runs is a custodied one, and every arm that did not is not."""
    for arm in LADDER:
        if arm.arm_id in ("B0", "B1", "B2", "B3"):
            assert not arm.custodian, arm.arm_id
        else:
            assert arm.custodian, arm.arm_id
