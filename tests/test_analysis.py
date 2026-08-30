"""M5 acceptance: every statistic checked against a value computed elsewhere.

"Checked against an independent reference" means one of three things here, and
each test says which:

* a value computed by hand from the published definition of the procedure;
* a value from ``scipy``, which is a separate implementation maintained by
  people who are not us;
* an analytic identity the number must satisfy regardless of implementation.

A test that only asserts the code agrees with itself would pass just as
happily on a wrong implementation.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from nullius.analysis.confidence import ConfidenceInputs, compute_confidence
from nullius.analysis.multiple import benjamini_hochberg, correct, holm
from nullius.analysis.stats import BCA_MINIMUM_SAMPLES, paired_analysis, seed_variance
from nullius.analysis.verdict import derive_verdict
from nullius.db.enums import ClaimConfidence, Verdict

# ---------------------------------------------------------------------------
# Paired analysis
# ---------------------------------------------------------------------------


def test_paired_difference_matches_hand_computation() -> None:
    baseline = [0.80, 0.82, 0.79, 0.81, 0.83]
    treatment = [0.84, 0.85, 0.83, 0.86, 0.87]
    # Differences: 0.04, 0.03, 0.04, 0.05, 0.04 -> mean 0.04
    result = paired_analysis(baseline, treatment)

    assert result.difference == pytest.approx(0.04, abs=1e-12)
    assert result.baseline_mean == pytest.approx(0.81, abs=1e-12)
    assert result.treatment_mean == pytest.approx(0.85, abs=1e-12)
    assert result.n_seeds == 5


def test_p_value_matches_scipy_paired_t_test() -> None:
    """Independent reference: scipy's own implementation."""
    rng = np.random.default_rng(11)
    baseline = rng.normal(0.8, 0.02, 12)
    treatment = baseline + rng.normal(0.03, 0.01, 12)

    result = paired_analysis(baseline, treatment)
    expected = stats.ttest_rel(treatment, baseline).pvalue
    assert result.p_value == pytest.approx(float(expected), rel=1e-12)


def test_effect_size_matches_the_definition_of_cohens_dz() -> None:
    """dz = mean(difference) / sd(difference), by hand from the definition."""
    baseline = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    treatment = [2.0, 4.0, 4.0, 6.0, 6.0, 8.0]
    differences = np.array(treatment) - np.array(baseline)
    expected = differences.mean() / differences.std(ddof=1)

    result = paired_analysis(baseline, treatment)
    assert result.effect_size == pytest.approx(float(expected), rel=1e-12)


def test_standard_error_matches_the_definition() -> None:
    values = [0.1, 0.3, 0.2, 0.4, 0.25, 0.35, 0.15]
    baseline = [0.0] * len(values)
    expected = float(np.std(values, ddof=1) / math.sqrt(len(values)))

    assert paired_analysis(baseline, values).standard_error == pytest.approx(expected, rel=1e-12)


def test_the_interval_brackets_the_estimate() -> None:
    """An analytic identity: a confidence interval contains its point estimate."""
    rng = np.random.default_rng(3)
    baseline = rng.normal(0.7, 0.05, 15)
    treatment = baseline + rng.normal(0.02, 0.01, 15)

    result = paired_analysis(baseline, treatment)
    assert result.ci_low <= result.difference <= result.ci_high


def test_the_analysis_is_reproducible() -> None:
    """Two readers of the same data must not see different intervals."""
    rng = np.random.default_rng(5)
    baseline = rng.normal(0.7, 0.05, 12)
    treatment = baseline + 0.02

    first = paired_analysis(baseline, treatment, seed=42)
    second = paired_analysis(baseline, treatment, seed=42)
    assert (first.ci_low, first.ci_high) == (second.ci_low, second.ci_high)


def test_bca_is_refused_on_small_samples() -> None:
    """The acceleration term is jackknife-estimated and unstable below ~10."""
    rng = np.random.default_rng(7)
    # Differences must actually vary; a constant offset has zero spread and
    # takes the no-interval path instead.
    baseline = rng.normal(0.7, 0.05, 5)
    small = paired_analysis(baseline, baseline + rng.normal(0.02, 0.005, 5))
    assert small.method == "percentile"

    big = rng.normal(0.7, 0.05, BCA_MINIMUM_SAMPLES)
    large = paired_analysis(big, big + rng.normal(0.02, 0.005, BCA_MINIMUM_SAMPLES))
    assert large.method == "bca"


def test_an_identical_difference_every_time_yields_no_interval() -> None:
    """Zero spread means bootstrapping would resample a constant.

    Usually a sign the seeds were not doing anything, so it is reported as
    ``none`` rather than dressed up as a very tight interval.
    """
    baseline = [0.7, 0.8, 0.9, 0.75, 0.85]
    result = paired_analysis(baseline, [value + 0.02 for value in baseline])
    assert result.method == "none"
    assert result.ci_low == result.ci_high == pytest.approx(0.02)
    assert result.standard_error == 0.0


def test_a_single_seed_yields_no_interval() -> None:
    """A zero-width interval would be a lie with a decimal point on it."""
    result = paired_analysis([0.8], [0.9])
    assert result.method == "none"
    assert result.ci_low == -math.inf
    assert result.ci_high == math.inf
    assert math.isnan(result.p_value)


def test_mismatched_arms_are_refused() -> None:
    with pytest.raises(ValueError, match="one value per arm per seed"):
        paired_analysis([0.1, 0.2], [0.1])


def test_exceeds_asks_about_the_whole_interval() -> None:
    """A claim of "at least x" must rule out everything smaller."""
    rng = np.random.default_rng(9)
    baseline = rng.normal(0.7, 0.001, 20)
    treatment = baseline + 0.05

    result = paired_analysis(baseline, treatment)
    assert result.exceeds(0.02)
    assert not result.exceeds(0.10)


def test_seed_variance_matches_numpy() -> None:
    values = [0.81, 0.79, 0.84, 0.80, 0.83]
    variance = seed_variance(values)
    assert variance.mean == pytest.approx(float(np.mean(values)), rel=1e-12)
    assert variance.sd == pytest.approx(float(np.std(values, ddof=1)), rel=1e-12)
    assert variance.minimum == 0.79
    assert variance.maximum == 0.84


# ---------------------------------------------------------------------------
# Multiple comparisons
# ---------------------------------------------------------------------------


def test_holm_matches_hand_computation() -> None:
    """Worked by hand from the step-down definition.

    p = [0.01, 0.04, 0.03], m = 3. Sorted: 0.01, 0.03, 0.04.
      rank 0: 3 x 0.01 = 0.03
      rank 1: 2 x 0.03 = 0.06
      rank 2: 1 x 0.04 = 0.04 -> raised to 0.06 by monotonicity
    """
    correction = holm([0.01, 0.04, 0.03], alpha=0.05)
    assert correction.adjusted[0] == pytest.approx(0.03)
    assert correction.adjusted[2] == pytest.approx(0.06)
    assert correction.adjusted[1] == pytest.approx(0.06)
    assert correction.rejected == (True, False, False)


def test_holm_is_monotone() -> None:
    """A larger raw p-value can never receive a smaller adjusted one."""
    raw = [0.001, 0.008, 0.02, 0.04, 0.2]
    correction = holm(raw)
    order = np.argsort(raw)
    adjusted = np.array(correction.adjusted)[order]
    assert np.all(np.diff(adjusted) >= -1e-12)


def test_holm_is_more_conservative_than_no_correction() -> None:
    raw = [0.01, 0.02, 0.03, 0.04]
    assert holm(raw).n_rejected <= correct(raw, "none").n_rejected


def test_benjamini_hochberg_matches_scipy() -> None:
    """Independent reference: scipy's false_discovery_control."""
    raw = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205]
    expected = stats.false_discovery_control(np.asarray(raw), method="bh")
    assert benjamini_hochberg(raw).adjusted == pytest.approx(tuple(expected), rel=1e-12)


def test_bh_rejects_at_least_as_much_as_holm() -> None:
    """FDR control is the looser criterion; that is the trade it makes."""
    raw = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06]
    assert benjamini_hochberg(raw).n_rejected >= holm(raw).n_rejected


def test_correcting_nothing_is_not_an_error() -> None:
    assert holm([]).adjusted == ()
    assert benjamini_hochberg([]).n_rejected == 0


def test_an_unknown_correction_is_refused() -> None:
    """The analysis plan names the procedure; a typo must not silently skip it."""
    with pytest.raises(ValueError, match="unknown correction"):
        correct([0.01], "bonferronni")


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def _result(low: float, high: float, n: int = 10):
    from nullius.analysis.stats import PairedResult

    centre = (low + high) / 2
    return PairedResult(
        n_seeds=n,
        baseline_mean=0.8,
        treatment_mean=0.8 + centre,
        difference=centre,
        ci_low=low,
        ci_high=high,
        standard_error=abs(high - low) / 4,
        p_value=0.01,
        effect_size=1.0,
        method="bca",
        alpha=0.05,
        resamples=10_000,
    )


@pytest.mark.parametrize(
    ("low", "high", "expected"),
    [
        (0.03, 0.06, Verdict.SUPPORTED),
        (-0.06, -0.03, Verdict.REFUTED),
        (-0.005, 0.005, Verdict.NO_EFFECT),
        (0.011, 0.018, Verdict.INCONCLUSIVE),  # real, smaller than claimed
        (-0.30, 0.30, Verdict.INCONCLUSIVE),  # too wide to say anything
    ],
)
def test_verdicts_are_derived_from_the_interval(low: float, high: float, expected: Verdict) -> None:
    assert derive_verdict(_result(low, high), mde=0.02).verdict is expected


def test_a_wide_interval_is_reported_as_a_fact_about_the_design() -> None:
    """The difference between "no effect" and "we could not tell"."""
    wide = derive_verdict(_result(-0.30, 0.30), mde=0.02)
    narrow = derive_verdict(_result(-0.005, 0.005), mde=0.02)

    assert wide.underpowered
    assert not narrow.underpowered
    assert "about the design" in wide.reason


def test_an_estimate_past_the_threshold_is_not_enough_on_its_own() -> None:
    """Point estimate +0.05, but the interval also admits +0.001."""
    marginal = derive_verdict(_result(0.001, 0.099), mde=0.02)
    assert marginal.verdict is Verdict.INCONCLUSIVE


def test_a_single_seed_can_never_produce_a_verdict() -> None:
    result = paired_analysis([0.8], [0.9])
    assert derive_verdict(result, mde=0.02).verdict is Verdict.INCONCLUSIVE


# ---------------------------------------------------------------------------
# The confidence rubric
# ---------------------------------------------------------------------------


STRONG = ConfidenceInputs(
    independent_replications=1,
    effect_to_interval_ratio=3.0,
    seed_variance_ratio=2.0,
    n_seeds=10,
    holdout_queries_consumed=1,
)


def test_strong_evidence_reaches_well_supported() -> None:
    assert compute_confidence(STRONG).confidence is ClaimConfidence.WELL_SUPPORTED


def test_an_open_critical_objection_caps_at_contested() -> None:
    """However clean the numbers, an unanswered critical objection dominates."""
    from dataclasses import replace

    report = compute_confidence(replace(STRONG, open_critical_objections=1))
    assert report.confidence is ClaimConfidence.CONTESTED
    assert any("critical objection" in reason for reason in report.capped_by)


def test_an_exploratory_design_can_never_be_well_supported() -> None:
    from dataclasses import replace

    report = compute_confidence(replace(STRONG, preregistered=False))
    assert report.confidence is ClaimConfidence.SUGGESTIVE
    assert any("not registered before" in reason for reason in report.capped_by)


def test_never_replicated_caps_below_well_supported() -> None:
    from dataclasses import replace

    report = compute_confidence(replace(STRONG, independent_replications=0))
    assert report.confidence is ClaimConfidence.SUPPORTED
    assert any("never independently reproduced" in reason for reason in report.capped_by)


def test_overused_holdout_erodes_confidence() -> None:
    from dataclasses import replace

    report = compute_confidence(replace(STRONG, holdout_queries_consumed=9))
    assert report.confidence is ClaimConfidence.SUPPORTED
    assert any("fitted by selection" in reason for reason in report.capped_by)


def test_broken_provenance_drops_to_speculative() -> None:
    from dataclasses import replace

    report = compute_confidence(replace(STRONG, provenance_complete=False))
    assert report.confidence is ClaimConfidence.SPECULATIVE


def test_no_evidence_means_speculative() -> None:
    assert compute_confidence(ConfidenceInputs()).confidence is ClaimConfidence.SPECULATIVE


def test_confidence_has_no_input_an_agent_could_simply_assert() -> None:
    """Every field is a checkable fact about the ledger, not an opinion."""
    fields = set(ConfidenceInputs().as_dict())
    assert "confidence" not in fields
    assert not any("belief" in f or "certainty" in f or "judgement" in f for f in fields)
