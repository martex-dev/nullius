"""The specification and the design linter."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nullius.design.linter import Severity, lint
from nullius.design.power import (
    minimum_detectable_effect,
    power_for,
    required_seeds,
    seeds_to_resolve,
)
from nullius.design.spec import ArmSpec, DatasetSpec, EstimatorSpec, ExperimentSpec, TransformSpec

LOGREG = EstimatorSpec(op="logistic_regression")


def _spec(**overrides: object) -> ExperimentSpec:
    """A clean, lint-passing design. Tests break one thing at a time."""
    base: dict[str, object] = {
        "title": "Divergence-based pruning under covariate shift",
        "dataset": DatasetSpec(generator="covariate_shift", params={"shift": "spurious"}),
        "arms": (
            ArmSpec(name="full", estimator=LOGREG),
            ArmSpec(
                name="prune",
                transforms=(TransformSpec(op="divergence_prune", params={"k": 3}),),
                estimator=LOGREG,
            ),
            ArmSpec(
                name="random",
                transforms=(TransformSpec(op="random_prune", params={"k": 3}),),
                estimator=LOGREG,
            ),
        ),
        "baseline_arm": "full",
        "treatment_arm": "prune",
        "primary_metric": "macro_f1",
        "secondary_metrics": ("accuracy",),
        "direction": "increase",
        "mde": 0.02,
        "prior_sd": 0.012,
        "n_seeds": 10,
        "seed_root": 48192,
        "tuning_budget": 8,
    }
    return ExperimentSpec(**(base | overrides))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The specification refuses incoherent designs outright
# ---------------------------------------------------------------------------


def test_a_clean_design_lints_clean() -> None:
    report = lint(_spec())
    assert report.ok, str(report)
    assert not report.errors


def test_a_metric_cannot_be_both_primary_and_secondary() -> None:
    with pytest.raises(ValidationError, match="two chances to succeed"):
        _spec(secondary_metrics=("macro_f1", "accuracy"))


def test_baseline_and_treatment_must_differ() -> None:
    with pytest.raises(ValidationError, match="must be different arms"):
        _spec(treatment_arm="full")


def test_an_unknown_arm_cannot_be_the_treatment() -> None:
    with pytest.raises(ValidationError, match="is not among the arms"):
        _spec(treatment_arm="nonexistent")


def test_unknown_fields_are_refused() -> None:
    """A typo'd field would otherwise be hashed into a design nobody intended."""
    with pytest.raises(ValidationError):
        _spec(mde_threshold=0.02)


def test_seeds_are_deterministic_and_fixed_at_registration() -> None:
    spec = _spec()
    assert spec.seeds() == spec.seeds()
    assert len(spec.seeds()) == spec.n_seeds
    assert _spec(seed_root=1).seeds() != spec.seeds()


# ---------------------------------------------------------------------------
# The linter catches what a hurried reviewer would not
# ---------------------------------------------------------------------------


def test_pruning_without_a_capacity_matched_control_is_an_error() -> None:
    """RQ-001's planted defect. This is the rule the Skeptic is measured against."""
    spec = _spec(
        arms=(
            ArmSpec(name="full", estimator=LOGREG),
            ArmSpec(
                name="prune",
                transforms=(TransformSpec(op="divergence_prune", params={"k": 3}),),
                estimator=LOGREG,
            ),
        )
    )
    report = lint(spec)
    assert not report.ok
    rules = {f.rule for f in report.errors}
    assert "capacity_matched" in rules
    assert "confounded with feature count" in str(report)


def test_a_control_pruning_a_different_number_does_not_count() -> None:
    """Matched on *count*, not merely on being random."""
    spec = _spec(
        arms=(
            ArmSpec(name="full", estimator=LOGREG),
            ArmSpec(
                name="prune",
                transforms=(TransformSpec(op="divergence_prune", params={"k": 3}),),
                estimator=LOGREG,
            ),
            ArmSpec(
                name="random",
                transforms=(TransformSpec(op="random_prune", params={"k": 1}),),
                estimator=LOGREG,
            ),
        )
    )
    assert "capacity_matched" in {f.rule for f in lint(spec).errors}


def test_a_majority_class_baseline_is_refused() -> None:
    spec = _spec(
        arms=(
            ArmSpec(name="full", estimator=EstimatorSpec(op="majority_class")),
            ArmSpec(
                name="prune",
                transforms=(TransformSpec(op="divergence_prune", params={"k": 3}),),
                estimator=LOGREG,
            ),
            ArmSpec(
                name="random",
                transforms=(TransformSpec(op="random_prune", params={"k": 3}),),
                estimator=LOGREG,
            ),
        )
    )
    assert "weak_baseline" in {f.rule for f in lint(spec).errors}


def test_one_seed_cannot_show_variance() -> None:
    assert "seed_minimum" in {f.rule for f in lint(_spec(n_seeds=1, mde=0.5)).errors}


def test_an_underpowered_design_is_refused_before_it_runs() -> None:
    """Stated in advance, so a null means "no effect" rather than "we didn't look"."""
    spec = _spec(n_seeds=3, mde=0.001, prior_sd=0.05)
    report = lint(spec)
    findings = {f.rule for f in report.errors}
    assert "underpowered" in findings
    assert "can only detect" in str(report)


def test_an_unregistered_operator_is_refused() -> None:
    spec = _spec(
        arms=(
            ArmSpec(name="full", estimator=EstimatorSpec(op="neural_oracle")),
            ArmSpec(name="prune", estimator=LOGREG),
        ),
        baseline_arm="full",
        treatment_arm="prune",
    )
    assert "ops_registered" in {f.rule for f in lint(spec).errors}


def test_grouped_data_needs_a_grouped_split() -> None:
    spec = _spec(
        dataset=DatasetSpec(
            generator="covariate_shift", params={"shift": "spurious"}, group_column="patient"
        )
    )
    assert "grouped_split" in {f.rule for f in lint(spec).errors}


def test_warnings_do_not_block_but_are_recorded() -> None:
    """A known weakness should travel with the claim, not vanish."""
    report = lint(_spec(tuning_budget=0))
    assert report.ok
    assert any(f.rule == "tuning_budget" for f in report.warnings)
    assert report.as_dict()["findings"]
    assert all(f["severity"] in {"error", "warning"} for f in report.as_dict()["findings"])


def test_mixing_model_families_warns() -> None:
    spec = _spec(
        arms=(
            ArmSpec(name="full", estimator=LOGREG),
            ArmSpec(
                name="prune",
                transforms=(TransformSpec(op="divergence_prune", params={"k": 3}),),
                estimator=EstimatorSpec(op="gradient_boosting"),
            ),
            ArmSpec(
                name="random",
                transforms=(TransformSpec(op="random_prune", params={"k": 3}),),
                estimator=LOGREG,
            ),
        )
    )
    report = lint(spec)
    assert report.ok, "mixing families is a weakness, not a blocker"
    assert any(
        f.rule == "weak_baseline" and f.severity is Severity.WARNING for f in report.findings
    )


# ---------------------------------------------------------------------------
# Power
# ---------------------------------------------------------------------------


def test_power_rises_with_sample_size_and_effect() -> None:
    assert power_for(effect=0.02, sd=0.01, n=20) > power_for(effect=0.02, sd=0.01, n=5)
    assert power_for(effect=0.05, sd=0.01, n=10) > power_for(effect=0.01, sd=0.01, n=10)


def test_a_zero_effect_has_power_equal_to_alpha() -> None:
    assert power_for(effect=0.0, sd=0.01, n=10) == pytest.approx(0.05)


def test_minimum_detectable_effect_is_the_inverse_of_power() -> None:
    mde = minimum_detectable_effect(sd=0.01, n=10)
    assert power_for(effect=mde, sd=0.01, n=10) == pytest.approx(0.8, abs=0.01)


def test_required_seeds_finds_the_smallest_adequate_n() -> None:
    n = required_seeds(effect=0.02, sd=0.01)
    assert power_for(effect=0.02, sd=0.01, n=n) >= 0.8
    assert power_for(effect=0.02, sd=0.01, n=n - 1) < 0.8


def test_an_unreachable_effect_returns_the_cap() -> None:
    assert required_seeds(effect=1e-9, sd=1.0, cap=50) == 50


# ---------------------------------------------------------------------------
# M14 — adaptive seeding, preregistered as a rule rather than a number
# ---------------------------------------------------------------------------


def test_the_whole_seed_set_is_named_before_anything_runs() -> None:
    """A ceiling declares more seeds; it never renumbers the ones already there.

    ``n_seeds`` is immutable on a Registration by database trigger, so the only
    honest way to spend more compute on a hard question is to preregister a
    rule. That works precisely because escalation chooses how far down an
    already-declared list to go, never which seeds are on it.
    """
    fixed = _spec(n_seeds=5)
    adaptive = _spec(n_seeds=5, max_seeds=24)

    assert len(fixed.seeds()) == 5
    assert len(adaptive.seeds()) == 24
    assert adaptive.seeds()[:5] == fixed.seeds()
    assert adaptive.mandatory_seeds() == fixed.seeds()


def test_a_ceiling_below_the_floor_is_refused() -> None:
    with pytest.raises(ValidationError, match="below n_seeds"):
        _spec(n_seeds=10, max_seeds=5)


def test_no_ceiling_means_no_escalation_is_permitted() -> None:
    spec = _spec(n_seeds=7)
    assert spec.max_seeds == 0
    assert spec.seed_ceiling == 7
    assert spec.seeds() == spec.mandatory_seeds()


def test_escalation_targets_exclusion_not_detection() -> None:
    """Power analysis asks whether an effect can be detected. A verdict needs
    the interval to fit inside one region, which is a stricter question and the
    one v3 measured a quarter of every arm failing."""
    sd = 0.00348
    # Comfortably inside the null band: little extra needed.
    assert seeds_to_resolve(estimate=0.002, sd=sd, mde=0.02) <= 5
    # Pressed against the edge of the null band: more.
    assert seeds_to_resolve(estimate=0.007, sd=sd, mde=0.02) > 5
    # Far past the claimed effect: trivial.
    assert seeds_to_resolve(estimate=0.05, sd=sd, mde=0.02) < 5
    # Sitting exactly on a boundary is unresolvable at any price, and says so.
    assert seeds_to_resolve(estimate=0.02, sd=sd, mde=0.02, cap=200) == 200


def test_a_design_powered_to_detect_can_still_fail_to_exclude() -> None:
    """The gap M14 exists to close, stated as an assertion.

    At five seeds the design has ample power to detect an effect of 0.02, and
    cannot place an interval inside the null band for an item sitting at 0.007.
    """
    sd = 0.00348
    assert power_for(effect=0.02, sd=sd, n=5) > 0.9
    assert seeds_to_resolve(estimate=0.007, sd=sd, mde=0.02) > 5
