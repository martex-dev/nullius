"""M7 acceptance: the adversarial layer is measured, not assumed."""

from __future__ import annotations

import pytest

from nullius.adversarial import roles as adversarial
from nullius.adversarial.defects import DefectKind, inject, registered_defects
from nullius.adversarial.detectors import DETECTORS, run_detectors
from nullius.adversarial.roles import ObjectionStatement, ReviewStatement, SkepticReport
from nullius.adversarial.scoring import DefectTrial, score_trials
from nullius.build import ops
from nullius.db.enums import ObjectionSeverity, Role
from nullius.design.linter import lint
from nullius.design.spec import ExperimentSpec
from nullius.runtime.contracts import ValidationFailure
from tests.test_execution import SPEC as _RAW_SPEC

#: A lint-clean base. Injecting into a design that already fails the linter
#: would measure the linter's opinion of the base, not of the defect.
SPEC = _RAW_SPEC.model_copy(update={"n_seeds": 10, "prior_sd": 0.012})

# ---------------------------------------------------------------------------
# Defects inject cleanly and are genuinely damaging
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(DefectKind))
def test_every_defect_produces_a_valid_specification(kind: DefectKind) -> None:
    """A defect the schema rejects would be caught for the wrong reason."""
    damaged, defect = inject(SPEC, kind)
    assert isinstance(damaged, ExperimentSpec)
    assert defect.kind is kind
    assert damaged != SPEC


@pytest.mark.parametrize(
    "kind",
    [d.kind for d in registered_defects() if d.caught_by_linter],
)
def test_defects_the_linter_owns_are_refused_before_anything_runs(kind: DefectKind) -> None:
    damaged, _ = inject(SPEC, kind)
    report = lint(damaged)
    assert not report.ok, f"{kind} should not survive the linter"


@pytest.mark.parametrize(
    "kind",
    [d.kind for d in registered_defects(reaching_the_skeptic=True)],
)
def test_defects_the_skeptic_owns_survive_the_linter(kind: DefectKind) -> None:
    """Otherwise the Skeptic would be credited for the linter's work."""
    damaged, _ = inject(SPEC, kind)
    assert lint(damaged).ok, f"{kind} is caught by the linter and never reaches the Skeptic"


@pytest.mark.slow
def test_the_leak_defect_manufactures_a_false_null() -> None:
    """The reason this defect matters: a real effect is flattened to nothing."""
    import statistics

    def effect(**params: object) -> float:
        differences = []
        for seed in range(6):
            data = ops.generate(
                "covariate_shift", seed=seed, n_samples=1000, shift="spurious", **params
            )
            scores = {}
            for arm, op in (("full", "passthrough"), ("prune", "divergence_prune")):
                selection = ops.transform(op, data.x_train, data.x_deploy, k=3, seed=seed)
                model = ops.estimator("logistic_regression", seed=seed)
                model.fit(data.x_train[:, selection.keep], data.y_train)
                scores[arm] = ops.metric(
                    "macro_f1", data.y_deploy, model.predict(data.x_deploy[:, selection.keep])
                )
            differences.append(scores["prune"] - scores["full"])
        return statistics.mean(differences)

    assert effect() > 0.3, "the clean item has a large real effect"
    assert abs(effect(leak_strength=8.0)) < 0.01, "the defect flattens it to nothing"


# ---------------------------------------------------------------------------
# Detectors: the code baseline
# ---------------------------------------------------------------------------


CLEAN_RESULTS = {
    "full": {"macro_f1": 0.30},
    "prune": {"macro_f1": 0.89},
    "random": {"macro_f1": 0.38},
}
LEAKED_RESULTS = {
    "full": {"macro_f1": 1.0},
    "prune": {"macro_f1": 1.0},
    "random": {"macro_f1": 1.0},
}


def test_the_detectors_leave_a_clean_design_alone() -> None:
    """Specificity matters as much as recall: everything-is-wrong is useless."""
    assert run_detectors(SPEC, CLEAN_RESULTS) == []


def test_the_ceiling_detector_finds_the_leak() -> None:
    findings = run_detectors(SPEC, LEAKED_RESULTS)
    assert [f.detector for f in findings] == ["ceiling_effect"]
    assert findings[0].severity is ObjectionSeverity.CRITICAL
    assert "manufactured" in findings[0].statement


@pytest.mark.parametrize("kind", [d.kind for d in registered_defects()])
def test_each_defect_is_found_by_its_own_detector(kind: DefectKind) -> None:
    damaged, defect = inject(SPEC, kind)
    results = LEAKED_RESULTS if kind is DefectKind.LEAKED_FEATURE else CLEAN_RESULTS

    detected = {f.detector for f in run_detectors(damaged, results)}
    assert defect.detector in detected, f"{kind} was not found by {defect.detector}"


def test_disabling_a_detector_actually_disables_it() -> None:
    """The mechanism that lets the Skeptic be scored without a code assist."""
    damaged, defect = inject(SPEC, DefectKind.LEAKED_FEATURE)
    assert run_detectors(damaged, LEAKED_RESULTS)
    assert run_detectors(damaged, LEAKED_RESULTS, disabled=frozenset({defect.detector})) == []


def test_every_detector_names_a_discriminating_test() -> None:
    """An objection no experiment could settle cannot block anything."""
    for kind in DefectKind:
        damaged, _ = inject(SPEC, kind)
        for finding in run_detectors(damaged, LEAKED_RESULTS):
            assert finding.discriminating_test
            assert "action" in finding.discriminating_test


def test_every_registered_defect_has_a_detector_that_exists() -> None:
    for defect in registered_defects():
        assert defect.detector in DETECTORS


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def test_recall_and_precision_are_computed_from_trials() -> None:
    trials = [
        DefectTrial("B01", "leaked_feature", "leakage", ("leakage",)),
        DefectTrial("B02", "no_actual_shift", "artifact_of_benchmark", ()),
        DefectTrial("B03", None, None, ()),
        DefectTrial("B04", None, None, ("confound",)),
    ]
    score = score_trials("skeptic", trials)

    assert score.planted == 2
    assert score.caught == 1
    assert score.recall == 0.5
    assert score.controls == 2
    assert score.false_alarms == 1
    assert score.precision == 0.5
    assert score.specificity == 0.5


def test_an_objector_that_flags_everything_scores_badly_on_precision() -> None:
    """Perfect recall with no specificity is worthless: blocking carries no signal."""
    trials = [DefectTrial(f"P{i}", "leaked_feature", "leakage", ("leakage",)) for i in range(3)]
    trials += [DefectTrial(f"C{i}", None, None, ("leakage",)) for i in range(5)]
    score = score_trials("noisy", trials)

    assert score.recall == 1.0
    assert score.precision < 0.4
    assert score.specificity == 0.0


def test_a_silent_objector_scores_zero_recall_but_perfect_specificity() -> None:
    trials = [DefectTrial(f"P{i}", "leaked_feature", "leakage", ()) for i in range(3)]
    trials += [DefectTrial(f"C{i}", None, None, ()) for i in range(3)]
    score = score_trials("silent", trials)

    assert score.recall == 0.0
    assert score.specificity == 1.0


@pytest.mark.slow
def test_the_detector_baseline_meets_the_acceptance_threshold() -> None:
    """Acceptance: recall above 0.5 on the planted defects, with no false alarms.

    This is the code baseline. The Skeptic is scored the same way with its
    detector disabled, so its number is comparable to this one.
    """
    trials: list[DefectTrial] = []
    for defect in registered_defects():
        damaged, _ = inject(SPEC, defect.kind)
        results = LEAKED_RESULTS if defect.kind is DefectKind.LEAKED_FEATURE else CLEAN_RESULTS
        raised = tuple(f.objection_type.value for f in run_detectors(damaged, results))
        trials.append(
            DefectTrial(defect.kind.value, defect.kind.value, defect.expected_objection, raised)
        )

    trials.append(
        DefectTrial(
            "clean",
            None,
            None,
            tuple(f.objection_type.value for f in run_detectors(SPEC, CLEAN_RESULTS)),
        )
    )

    score = score_trials("detectors", trials)
    assert score.recall > 0.5, str(score)
    assert score.false_alarms == 0, str(score)


# ---------------------------------------------------------------------------
# The Skeptic's and Reviewer's contracts
# ---------------------------------------------------------------------------


def test_an_objection_without_an_experiment_is_rejected() -> None:
    """Objection theater, refused at the validator rather than argued with."""
    vague = SkepticReport(
        objections=[
            ObjectionStatement(
                objection_type="confound",
                severity="critical",
                statement="Something else might be going on here that we have not considered.",
                discriminating_test="It would be concerning if this were a confound.",
            )
        ],
        assessment="The design gives me pause for reasons I cannot quite pin down.",
    )
    with pytest.raises(ValidationFailure, match="names no experiment"):
        adversarial.tests_are_actionable(vague, {})


def test_an_objection_naming_an_experiment_is_accepted() -> None:
    concrete = SkepticReport(
        objections=[
            ObjectionStatement(
                objection_type="confound",
                severity="critical",
                statement="The arms differ in feature count as well as in which features.",
                discriminating_test="Add an arm that drops the same number of features at random.",
            )
        ],
        assessment="One control is missing; otherwise the design is sound.",
    )
    adversarial.tests_are_actionable(concrete, {})


def test_raising_nothing_is_a_legitimate_answer() -> None:
    adversarial.tests_are_actionable(
        SkepticReport(objections=[], assessment="I can find nothing wrong with this design."),
        {},
    )


def test_a_review_decision_must_follow_from_its_scores() -> None:
    inconsistent = ReviewStatement(
        decision="accept",
        novelty=1,
        methodological_quality=2,
        statistical_quality=1,
        reproducibility=2,
        rationale="This is excellent work and should enter the record immediately.",
    )
    with pytest.raises(ValidationFailure, match="must follow from the scores"):
        adversarial.decision_matches_scores(inconsistent, {})


def test_a_consistent_review_is_accepted() -> None:
    adversarial.decision_matches_scores(
        ReviewStatement(
            decision="accept",
            novelty=3,
            methodological_quality=4,
            statistical_quality=4,
            reproducibility=5,
            rationale="Adequately powered, preregistered, and independently reproduced.",
        ),
        {},
    )


def test_the_adversary_runs_on_a_different_model_family() -> None:
    """docs/01-critique.md F8: shared blind spots are the failure being avoided."""
    from nullius.roles.contracts import CONTRACTS

    contracts = adversarial.adversarial_contracts()
    skeptic = contracts[(Role.SKEPTIC, "v1")].model.model
    theorist = CONTRACTS[(Role.THEORIST, "v1")].model.model

    assert skeptic != theorist, (
        "an adversary sharing a base model with the work it checks inherits the same blind spots"
    )
