"""The design linter.

Runs before a specification may be registered. Everything here is a check a
careful reviewer would make and an enthusiastic one would skip, expressed as
code so it happens every time.

The rule that matters most is :func:`_capacity_matched`. RQ-001's whole point
is that dropping features changes two things at once — which features are
present, *and* how many — so an arm that prunes must be compared against an
arm that prunes the same number at random. Omitting that control is the
planted defect the Skeptic is measured against; the linter catching it here
means the Skeptic is being tested on something harder than the obvious case.

Findings are severity-typed. ``ERROR`` blocks registration. ``WARNING`` is
recorded on the registration and printed in the report, so a known weakness
travels with the claim instead of being forgotten.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum

from nullius.build import ops
from nullius.design.power import minimum_detectable_effect, power_for
from nullius.design.spec import ArmSpec, ExperimentSpec

__all__ = ["Finding", "LintReport", "Severity", "lint"]

TARGET_POWER = 0.8
"""Conventional. Stated here so a change to it is a visible commit."""

_PRUNING_OPS = frozenset({"divergence_prune", "random_prune"})


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Finding:
    rule: str
    severity: Severity
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.value}] {self.rule}: {self.message}"


@dataclass(frozen=True, slots=True)
class LintReport:
    findings: tuple[Finding, ...]

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.WARNING)

    @property
    def ok(self) -> bool:
        """True when nothing blocks registration."""
        return not self.errors

    def as_dict(self) -> dict[str, list[dict[str, str]]]:
        """Stored on the registration, so weaknesses travel with the claim."""
        return {
            "findings": [
                {"rule": f.rule, "severity": f.severity.value, "message": f.message}
                for f in self.findings
            ]
        }

    def __str__(self) -> str:
        if not self.findings:
            return "design lint: clean"
        return "design lint:\n" + "\n".join(f"  {f}" for f in self.findings)


Rule = Callable[[ExperimentSpec], Iterator[Finding]]
_RULES: list[Rule] = []


def rule(fn: Rule) -> Rule:
    _RULES.append(fn)
    return fn


def _pruned_count(arm: ArmSpec) -> int | None:
    """How many features an arm drops, or ``None`` if it drops none."""
    for step in arm.transforms:
        if step.op in _PRUNING_OPS:
            return int(step.params.get("k", 0))
    return None


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@rule
def _ops_are_registered(spec: ExperimentSpec) -> Iterator[Finding]:
    """Every operation must resolve. The registry is closed by design."""
    if spec.dataset.generator not in ops.registered_generators():
        yield Finding(
            "ops_registered",
            Severity.ERROR,
            f"unknown generator {spec.dataset.generator!r}; "
            f"available: {list(ops.registered_generators())}",
        )
    for arm in spec.arms:
        if arm.estimator.op not in ops.registered_estimators():
            yield Finding(
                "ops_registered",
                Severity.ERROR,
                f"arm {arm.name!r} names unknown estimator {arm.estimator.op!r}",
            )
        for step in arm.transforms:
            if step.op not in ops.registered_transforms():
                yield Finding(
                    "ops_registered",
                    Severity.ERROR,
                    f"arm {arm.name!r} names unknown transform {step.op!r}",
                )
    for name in spec.all_metrics():
        if name not in ops.registered_metrics():
            yield Finding("ops_registered", Severity.ERROR, f"unknown metric {name!r}")


@rule
def _capacity_matched(spec: ExperimentSpec) -> Iterator[Finding]:
    """A pruning arm needs a control that prunes as much at random.

    Otherwise the comparison confounds *which* features were removed with
    *how many* — and the effect could be capacity control rather than
    anything about distribution shift.
    """
    treatment = spec.arm(spec.treatment_arm)
    dropped = _pruned_count(treatment)
    if dropped is None:
        return

    matched = [
        arm
        for arm in spec.arms
        if arm.name != treatment.name
        and any(
            s.op == "random_prune" and int(s.params.get("k", 0)) == dropped for s in arm.transforms
        )
    ]
    if not matched:
        yield Finding(
            "capacity_matched",
            Severity.ERROR,
            f"arm {treatment.name!r} drops {dropped} features but no arm drops "
            f"{dropped} at random. Any effect is confounded with feature count; "
            "add a random_prune control with the same k.",
        )


@rule
def _baseline_is_not_a_straw_man(spec: ExperimentSpec) -> Iterator[Finding]:
    """A trivial baseline makes any treatment look good."""
    baseline = spec.arm(spec.baseline_arm)
    if baseline.estimator.op == "majority_class":
        yield Finding(
            "weak_baseline",
            Severity.ERROR,
            "the baseline arm predicts the majority class; that is a floor to "
            "clear, not a comparison to claim against",
        )
    treatment = spec.arm(spec.treatment_arm)
    if baseline.estimator.op != treatment.estimator.op:
        yield Finding(
            "weak_baseline",
            Severity.WARNING,
            f"baseline uses {baseline.estimator.op!r} and treatment uses "
            f"{treatment.estimator.op!r}; the comparison mixes the intervention "
            "with a change of model family",
        )


@rule
def _tuning_budget_is_equal(spec: ExperimentSpec) -> Iterator[Finding]:
    """Stated once for all arms, so it cannot differ between them."""
    if spec.tuning_budget == 0:
        yield Finding(
            "tuning_budget",
            Severity.WARNING,
            "no tuning budget; both arms run at their default hyperparameters, "
            "so the result speaks only to those defaults",
        )


@rule
def _grouped_data_needs_a_grouped_split(spec: ExperimentSpec) -> Iterator[Finding]:
    """Group structure the splitter does not know about is a leak."""
    if spec.dataset.group_column and spec.split.kind != "grouped":
        yield Finding(
            "grouped_split",
            Severity.ERROR,
            f"the dataset declares group column {spec.dataset.group_column!r} but "
            f"the split is {spec.split.kind!r}; rows from one group would land on "
            "both sides of the split",
        )
    if spec.split.kind == "grouped" and spec.split.group_column != spec.dataset.group_column:
        yield Finding(
            "grouped_split",
            Severity.ERROR,
            "the split groups on a different column from the one the dataset declares",
        )


@rule
def _enough_seeds(spec: ExperimentSpec) -> Iterator[Finding]:
    """One seed cannot show variance, and variance is most of the story."""
    if spec.n_seeds < 3:
        yield Finding(
            "seed_minimum",
            Severity.ERROR,
            f"{spec.n_seeds} seed(s) cannot distinguish an effect from run-to-run "
            "variation; at least 3 are needed, and 5 or more for effects near the MDE",
        )
    elif spec.n_seeds < 5:
        yield Finding(
            "seed_minimum",
            Severity.WARNING,
            f"{spec.n_seeds} seeds is thin for an effect as small as {spec.mde}",
        )


@rule
def _adequately_powered(spec: ExperimentSpec) -> Iterator[Finding]:
    """Can this design detect the effect it says it cares about?

    Answerable before running only because the spec commits to ``prior_sd``.
    That commitment is the point: a design that turns out underpowered was
    underpowered by declaration, not by hindsight.
    """
    if spec.direction == "no_change":
        return  # An equivalence design is powered differently; M5 handles it.

    power = power_for(effect=spec.mde, sd=spec.prior_sd, n=spec.n_seeds)
    if power >= TARGET_POWER:
        return

    detectable = minimum_detectable_effect(sd=spec.prior_sd, n=spec.n_seeds)
    yield Finding(
        "underpowered",
        Severity.ERROR,
        f"power is {power:.2f} for an effect of {spec.mde:g} at {spec.n_seeds} seeds "
        f"(sd {spec.prior_sd:g}); this design can only detect {detectable:.4g}. "
        "Raise n_seeds, or state an MDE the design can actually find.",
    )


@rule
def _budget_is_plausible(spec: ExperimentSpec) -> Iterator[Finding]:
    runs = spec.n_seeds * len(spec.arms)
    if spec.compute_budget_seconds / runs < 0.5:
        yield Finding(
            "compute_budget",
            Severity.WARNING,
            f"{spec.compute_budget_seconds:g}s for {runs} fits leaves under half a "
            "second each; the run will probably be killed mid-experiment",
        )


def lint(spec: ExperimentSpec) -> LintReport:
    """Run every rule against ``spec``."""
    findings: list[Finding] = []
    for check in _RULES:
        findings.extend(check(spec))
    return LintReport(findings=tuple(findings))
