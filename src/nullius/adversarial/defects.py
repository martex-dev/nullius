"""Planted defects: the calibration harness for the adversarial layer.

`docs/01-critique.md` §1.2 — a Skeptic given the instruction "find why this
might be wrong" and run on the same base model as the work it checks produces
fluent, unfalsifiable criticism that costs tokens and blocks nothing. The only
way to know whether an adversary is real is to hand it work with known faults
and count what it finds.

So: inject a defect whose identity we record, run the full adversarial layer,
and measure recall and precision. This is mutation testing pointed at
epistemics.

**What this harness can and cannot measure.** Anything specified precisely
enough to *inject* is specified precisely enough to *detect*, so every defect
here also has a code detector. That is not a flaw in the defects; it is a
limit on the method, and pretending otherwise would be the exact
self-congratulation this project exists to avoid.

So the Skeptic is scored on these defects **with the corresponding detectors
switched off**. That answers "can it find a real fault on its own evidence?"
It does not answer "would it find a fault nobody anticipated", and no
injection harness can, because an anticipated defect is what an injection is.
The honest reading of a good Skeptic score is therefore: it is not merely
restating the detectors. Treat it as a floor, not a proof.

**Injection never touches the question bank.** A defective experiment is
scored against the same ground truth as a clean one; the defect changes the
experiment, not the answer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from nullius.design.spec import ExperimentSpec

__all__ = ["DEFECTS", "Defect", "DefectKind", "inject", "registered_defects"]


class DefectKind(StrEnum):
    """The faults we know how to plant."""

    MISSING_CAPACITY_CONTROL = "missing_capacity_control"
    """Prunes features with no arm dropping as many at random."""

    WEAK_BASELINE = "weak_baseline"
    """Compares against something no one would deploy."""

    UNDERPOWERED = "underpowered"
    """Claims an effect the design cannot detect."""

    NO_ACTUAL_SHIFT = "no_actual_shift"
    """Claims an effect under distribution change, on data that does not change."""

    LEAKED_FEATURE = "leaked_feature"
    """A feature that is very nearly the label. Training performance goes
    perfect and the comparison becomes meaningless."""

    SINGLE_ENVIRONMENT_EVALUATION = "single_environment_evaluation"
    """Evaluates only where it trained, so nothing about transfer is shown."""


@dataclass(frozen=True, slots=True)
class Defect:
    """One plantable fault, and who ought to catch it."""

    kind: DefectKind
    description: str
    apply: Callable[[ExperimentSpec], ExperimentSpec]
    detector: str
    """The code check that finds this without any model.

    Disabled when scoring the Skeptic, so the Skeptic is not simply being
    graded on whether it agrees with a detector that already ran.
    """

    caught_by_linter: bool
    """True when the design linter refuses it before anything executes.

    These never reach the Skeptic in a real programme, so they are reported
    separately rather than folded into its recall.
    """

    expected_objection: str
    """The objection type a correct challenge should raise."""


def _mutate(spec: ExperimentSpec, **changes: Any) -> ExperimentSpec:
    """Apply changes and re-validate.

    An injected defect must still be a *valid* specification. If it failed
    schema validation the lifecycle would refuse it for the wrong reason, and
    the trial would measure Pydantic rather than the adversarial layer.
    """
    return ExperimentSpec.model_validate({**spec.model_dump(), **changes})


def _with_dataset(spec: ExperimentSpec, **params: Any) -> ExperimentSpec:
    dataset = spec.dataset.model_dump()
    dataset["params"] = {**dataset["params"], **params}
    return _mutate(spec, dataset=dataset)


def _drop_capacity_control(spec: ExperimentSpec) -> ExperimentSpec:
    arms = [
        arm.model_dump()
        for arm in spec.arms
        if not any(step.op == "random_prune" for step in arm.transforms)
    ]
    return _mutate(spec, arms=arms)


def _weaken_baseline(spec: ExperimentSpec) -> ExperimentSpec:
    arms = []
    for arm in spec.arms:
        dumped = arm.model_dump()
        if arm.name == spec.baseline_arm:
            dumped["estimator"] = {"op": "majority_class", "params": {}}
        arms.append(dumped)
    return _mutate(spec, arms=arms)


def _underpower(spec: ExperimentSpec) -> ExperimentSpec:
    return _mutate(spec, n_seeds=3, mde=0.001, prior_sd=0.08)


def _remove_the_shift(spec: ExperimentSpec) -> ExperimentSpec:
    """Same design, same claim, but the environments are identical.

    Invisible to the linter, which sees a well-formed comparison. Visible to a
    detector that reads the dataset parameters, and visible to anyone who asks
    what the experiment is actually contrasting.
    """
    return _with_dataset(spec, shift="none", shift_strength=0.0)


def _leak_the_label(spec: ExperimentSpec) -> ExperimentSpec:
    """Put a dominant label-derived feature among the noise columns.

    Nothing about the design looks wrong, and the tell is not an inflated
    training score — a clean run already reaches the training ceiling. The tell
    is that *every arm* reaches the ceiling on the evaluation split and the
    difference between them collapses. Measured on a spurious-shift item, a
    genuine effect of about +0.60 becomes +0.00: the defect manufactures a
    false null.
    """
    return _with_dataset(spec, leak_strength=8.0)


def _evaluate_where_it_trained(spec: ExperimentSpec) -> ExperimentSpec:
    """Claim a transfer effect while both environments are the training one."""
    damaged = _with_dataset(spec, shift="none", shift_strength=0.0)
    return _mutate(damaged, title=f"{spec.title} (deployment performance)"[:200])


DEFECTS: dict[DefectKind, Defect] = {
    DefectKind.MISSING_CAPACITY_CONTROL: Defect(
        kind=DefectKind.MISSING_CAPACITY_CONTROL,
        detector="capacity_matched",
        caught_by_linter=True,
        description="pruning arm with no count-matched random control",
        apply=_drop_capacity_control,
        expected_objection="confound",
    ),
    DefectKind.WEAK_BASELINE: Defect(
        kind=DefectKind.WEAK_BASELINE,
        detector="weak_baseline",
        caught_by_linter=True,
        description="baseline predicts the majority class",
        apply=_weaken_baseline,
        expected_objection="weak_baseline",
    ),
    DefectKind.UNDERPOWERED: Defect(
        kind=DefectKind.UNDERPOWERED,
        detector="underpowered",
        caught_by_linter=True,
        description="claims an effect far below what the design can detect",
        apply=_underpower,
        expected_objection="underpowered",
    ),
    DefectKind.NO_ACTUAL_SHIFT: Defect(
        kind=DefectKind.NO_ACTUAL_SHIFT,
        detector="no_actual_shift",
        caught_by_linter=False,
        description="claims an effect under distribution change, on unchanged data",
        apply=_remove_the_shift,
        expected_objection="artifact_of_benchmark",
    ),
    DefectKind.LEAKED_FEATURE: Defect(
        kind=DefectKind.LEAKED_FEATURE,
        detector="ceiling_effect",
        caught_by_linter=False,
        description="a feature that is almost the label",
        apply=_leak_the_label,
        expected_objection="leakage",
    ),
    DefectKind.SINGLE_ENVIRONMENT_EVALUATION: Defect(
        kind=DefectKind.SINGLE_ENVIRONMENT_EVALUATION,
        detector="no_actual_shift",
        caught_by_linter=False,
        description="claims transfer while evaluating only where it trained",
        apply=_evaluate_where_it_trained,
        expected_objection="generalisation_overreach",
    ),
}


def inject(spec: ExperimentSpec, kind: DefectKind) -> tuple[ExperimentSpec, Defect]:
    """Plant one defect. Returns the damaged specification and its record."""
    defect = DEFECTS[kind]
    return defect.apply(spec), defect


def registered_defects(*, reaching_the_skeptic: bool = False) -> tuple[Defect, ...]:
    """Every plantable defect, optionally only those the Skeptic ever sees.

    Defects the linter refuses stop the lifecycle before any challenge
    happens, so including them in the Skeptic's recall would credit it for
    work the linter did.
    """
    defects = tuple(DEFECTS.values())
    if reaching_the_skeptic:
        return tuple(d for d in defects if not d.caught_by_linter)
    return defects
