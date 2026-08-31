"""Code detectors: the baseline the Skeptic has to beat.

Each detector reads the same evidence the Skeptic gets — the registered
specification and the raw per-arm results — and raises the same typed
objection. They are the control arm of the adversarial layer: if the Skeptic's
recall is no better than these, the Skeptic is an expensive way to restate
them.

Every objection names a **discriminating test**: the experiment that would
settle it. That is not decoration. `docs/01-critique.md` F6 is objection
theater — fluent criticism no experiment could resolve — and requiring a test
is how it is refused at the schema level rather than argued about.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from nullius.build import ops
from nullius.db.enums import ObjectionSeverity, ObjectionType
from nullius.design.power import power_for
from nullius.design.spec import ExperimentSpec

__all__ = ["DETECTORS", "Finding", "run_detectors"]

CEILING = 0.995
"""A metric this close to its maximum is usually a fault, not a result."""

COLLAPSE = 0.002
"""Arms differing by less than this have not been distinguished at all."""


@dataclass(frozen=True, slots=True)
class Finding:
    """One code-raised objection."""

    detector: str
    objection_type: ObjectionType
    severity: ObjectionSeverity
    statement: str
    discriminating_test: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "type": self.objection_type.value,
            "severity": self.severity.value,
            "statement": self.statement,
            "discriminating_test": self.discriminating_test,
        }


Detector = Callable[[ExperimentSpec, dict[str, dict[str, float]]], Iterator[Finding]]
DETECTORS: dict[str, Detector] = {}


def detector(name: str) -> Callable[[Detector], Detector]:
    def register(fn: Detector) -> Detector:
        DETECTORS[name] = fn
        return fn

    return register


@detector("ceiling_effect")
def _ceiling_effect(
    spec: ExperimentSpec, results: dict[str, dict[str, float]]
) -> Iterator[Finding]:
    """Every arm at the ceiling, and no difference between them.

    The signature of a feature that dominates everything else — often one
    derived from the label. The comparison did not fail to find an effect; it
    was never in a position to find one.
    """
    values = [
        arm.get(spec.primary_metric) for arm in results.values() if spec.primary_metric in arm
    ]
    if len(values) < 2 or any(v is None for v in values):
        return

    scores = [float(v) for v in values if v is not None]
    if min(scores) >= CEILING and (max(scores) - min(scores)) < COLLAPSE:
        yield Finding(
            detector="ceiling_effect",
            objection_type=ObjectionType.LEAKAGE,
            severity=ObjectionSeverity.CRITICAL,
            statement=(
                "Every arm reaches the metric ceiling and the arms are "
                "indistinguishable. Some feature is predicting the label almost "
                "perfectly regardless of the intervention, so this design cannot "
                "measure the intervention. The null here is manufactured."
            ),
            discriminating_test={
                "action": "refit_with_each_feature_removed",
                "expect": "one feature accounts for nearly all performance",
            },
        )


@detector("no_actual_shift")
def _no_actual_shift(
    spec: ExperimentSpec, results: dict[str, dict[str, float]]
) -> Iterator[Finding]:
    """A claim about distribution change, on data that does not change.

    An absent parameter means the generator's default, not zero. Reading the
    defaults from the generator keeps this from firing on every design that
    simply did not restate them.
    """
    defaults = ops.generator_defaults(spec.dataset.generator)
    settings = {**defaults, **spec.dataset.params}
    unchanged = settings.get("shift") in (None, "none") or not settings.get("shift_strength")

    if unchanged and spec.direction != "no_change":
        yield Finding(
            detector="no_actual_shift",
            objection_type=ObjectionType.ARTIFACT_OF_BENCHMARK,
            severity=ObjectionSeverity.CRITICAL,
            statement=(
                "The design claims an effect under distribution change, but the "
                "two environments are drawn from the same process. Whatever this "
                "measures, it is not robustness to a shift that did not happen."
            ),
            discriminating_test={
                "action": "rerun_with_a_declared_shift",
                "expect": "the effect appears only when the environments differ",
            },
        )


@detector("capacity_matched")
def _capacity_matched(
    spec: ExperimentSpec, results: dict[str, dict[str, float]]
) -> Iterator[Finding]:
    """Pruning without a count-matched random control."""
    treatment = spec.arm(spec.treatment_arm)
    dropped = next(
        (
            int(step.params.get("k", 0))
            for step in treatment.transforms
            if step.op in {"divergence_prune", "random_prune"}
        ),
        None,
    )
    if dropped is None:
        return

    matched = any(
        any(s.op == "random_prune" and int(s.params.get("k", 0)) == dropped for s in arm.transforms)
        for arm in spec.arms
        if arm.name != treatment.name
    )
    if not matched:
        yield Finding(
            detector="capacity_matched",
            objection_type=ObjectionType.CONFOUND,
            severity=ObjectionSeverity.CRITICAL,
            statement=(
                f"The treatment drops {dropped} features and no arm drops "
                f"{dropped} at random. Any difference confounds which features "
                "were removed with how many."
            ),
            discriminating_test={
                "action": "add_random_prune_arm",
                "matched_on": "n_features",
                "expect": "the effect survives against the matched control",
            },
        )


@detector("weak_baseline")
def _weak_baseline(spec: ExperimentSpec, results: dict[str, dict[str, float]]) -> Iterator[Finding]:
    """A baseline nobody would deploy makes any treatment look good."""
    if spec.arm(spec.baseline_arm).estimator.op == "majority_class":
        yield Finding(
            detector="weak_baseline",
            objection_type=ObjectionType.WEAK_BASELINE,
            severity=ObjectionSeverity.CRITICAL,
            statement=(
                "The baseline predicts the majority class. That is a floor to "
                "clear, not a comparison to claim against."
            ),
            discriminating_test={
                "action": "rerun_against_the_same_estimator",
                "expect": "the effect survives a competent baseline",
            },
        )


@detector("underpowered")
def _underpowered(spec: ExperimentSpec, results: dict[str, dict[str, float]]) -> Iterator[Finding]:
    """A design that cannot detect what it claims produces uninterpretable nulls."""
    if spec.direction == "no_change":
        return
    power = power_for(effect=spec.mde, sd=spec.prior_sd, n=spec.n_seeds)
    if power < 0.8:
        yield Finding(
            detector="underpowered",
            objection_type=ObjectionType.UNDERPOWERED,
            severity=ObjectionSeverity.MAJOR,
            statement=(
                "The design cannot reliably detect the effect it claims at the "
                "number of seeds it runs. A null from this experiment would mean "
                "the experiment was too small, not that the effect is absent."
            ),
            discriminating_test={
                "action": "increase_seeds_to_target_power",
                "expect": "the interval narrows enough to separate the outcomes",
            },
        )


@detector("seed_instability")
def _seed_instability(
    spec: ExperimentSpec, results: dict[str, dict[str, float]]
) -> Iterator[Finding]:
    """An effect smaller than the run-to-run spread of a single arm."""
    spreads = [
        arm.get(f"{spec.primary_metric}__sd")
        for arm in results.values()
        if f"{spec.primary_metric}__sd" in arm
    ]
    if not spreads:
        return
    worst = max(float(s) for s in spreads if s is not None)
    if worst > spec.mde:
        yield Finding(
            detector="seed_instability",
            objection_type=ObjectionType.SEED_INSTABILITY,
            severity=ObjectionSeverity.MAJOR,
            statement=(
                "An arm varies more between seeds than the effect being claimed. "
                "Any single-seed comparison here would be noise."
            ),
            discriminating_test={
                "action": "increase_seeds",
                "expect": "the arm means stabilise below the claimed effect",
            },
        )


def run_detectors(
    spec: ExperimentSpec,
    results: dict[str, dict[str, float]],
    *,
    disabled: frozenset[str] = frozenset(),
) -> list[Finding]:
    """Run every detector not in ``disabled``.

    ``disabled`` exists for one purpose: scoring the Skeptic with the detector
    that would have found a planted defect switched off, so the Skeptic is not
    graded on whether it agrees with a check that already ran.
    """
    findings: list[Finding] = []
    for name, check in DETECTORS.items():
        if name in disabled:
            continue
        findings.extend(check(spec, results))
    return findings
