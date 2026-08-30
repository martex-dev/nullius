"""The experiment specification.

This is the object that gets hashed and preregistered, so it has to be
*complete*: everything that determines what will be run and how it will be
judged must be here, before anything executes. If a decision can be made later
it is a decision that can be made after seeing results, which is the failure
this project exists to prevent.

It is also entirely declarative. No code, no callables, no free text that gets
interpreted — every operation is a key into a closed registry
(:mod:`nullius.build.ops`). That is ADR-0004 in type form: the Designer emits
this, and a human-written compiler turns it into a run.

The deliberately awkward fields are the load-bearing ones:

``primary_metric``
    Exactly one, named up front. A design with two primary metrics has two
    chances to succeed.
``mde`` and ``prior_sd``
    The effect worth detecting, and the variability expected. Committing to
    both before running is what makes the power analysis honest — and what
    lets the analysis say "underpowered" rather than "not significant".
``arms`` / ``baseline_arm``
    Which comparison is the claim. Everything else is secondary by
    construction.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ArmSpec",
    "DatasetSpec",
    "EstimatorSpec",
    "ExperimentSpec",
    "SplitSpec",
    "TransformSpec",
]

Name = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")]


class _Frozen(BaseModel):
    """Immutable, and closed to unknown fields.

    ``extra="forbid"`` matters more than it looks: a typo'd field in a spec
    would otherwise be silently ignored, and the registration would hash a
    design nobody intended.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class TransformSpec(_Frozen):
    """One preprocessing step, by registry key."""

    op: Name
    params: dict[str, Any] = Field(default_factory=dict)


class EstimatorSpec(_Frozen):
    """The model an arm fits, by registry key."""

    op: Name
    params: dict[str, Any] = Field(default_factory=dict)


class ArmSpec(_Frozen):
    """One condition being compared."""

    name: Name
    transforms: tuple[TransformSpec, ...] = ()
    estimator: EstimatorSpec


class DatasetSpec(_Frozen):
    """Where the data comes from.

    A generator plus parameters rather than a file path, so an experiment is
    reproducible from the specification alone. ``group_column`` is declared
    here rather than inferred, because a grouped structure that the splitter
    does not know about is a leak.
    """

    generator: Name
    params: dict[str, Any] = Field(default_factory=dict)
    group_column: str | None = None
    content_hash: str | None = None
    """Set once the data exists; part of the run's provenance, not the design."""


class SplitSpec(_Frozen):
    """How the data is divided.

    The holdout fraction is declared but never handed to the experiment: the
    Custodian holds that split, and the runner only ever sees train and dev.
    """

    kind: Literal["stratified", "grouped"] = "stratified"
    dev_fraction: float = Field(default=0.2, gt=0, lt=1)
    holdout_fraction: float = Field(default=0.2, gt=0, lt=1)
    group_column: str | None = None

    @model_validator(mode="after")
    def _grouped_needs_a_group(self) -> SplitSpec:
        if self.kind == "grouped" and not self.group_column:
            raise ValueError("a grouped split must name the column it groups on")
        return self


class ExperimentSpec(_Frozen):
    """A complete, executable, preregisterable experiment."""

    title: str = Field(min_length=8, max_length=200)
    dataset: DatasetSpec
    arms: tuple[ArmSpec, ...] = Field(min_length=2)
    baseline_arm: Name
    treatment_arm: Name

    primary_metric: Name
    secondary_metrics: tuple[Name, ...] = ()
    direction: Literal["increase", "decrease", "no_change"]
    mde: float = Field(ge=0, description="Smallest effect worth detecting.")
    prior_sd: float = Field(gt=0, description="Expected per-seed standard deviation.")

    split: SplitSpec = Field(default_factory=SplitSpec)
    n_seeds: int = Field(ge=1, le=200)
    seed_root: int = Field(ge=0)
    tuning_budget: int = Field(default=0, ge=0)
    """Identical for every arm. An under-tuned baseline is not a baseline."""

    compute_budget_seconds: float = Field(default=300.0, gt=0)

    @model_validator(mode="after")
    def _arms_are_coherent(self) -> ExperimentSpec:
        names = [arm.name for arm in self.arms]
        if len(names) != len(set(names)):
            raise ValueError("arm names must be unique")
        for role, name in (
            ("baseline_arm", self.baseline_arm),
            ("treatment_arm", self.treatment_arm),
        ):
            if name not in names:
                raise ValueError(f"{role} {name!r} is not among the arms {names}")
        if self.baseline_arm == self.treatment_arm:
            raise ValueError("the baseline and the treatment must be different arms")
        return self

    @model_validator(mode="after")
    def _primary_metric_is_singular(self) -> ExperimentSpec:
        if self.primary_metric in self.secondary_metrics:
            raise ValueError(
                f"{self.primary_metric!r} is both the primary metric and a secondary one; "
                "a design with two primary metrics has two chances to succeed"
            )
        return self

    @model_validator(mode="after")
    def _splits_leave_room_to_train(self) -> ExperimentSpec:
        if self.split.dev_fraction + self.split.holdout_fraction >= 0.9:
            raise ValueError("dev and holdout fractions leave under 10% for training")
        return self

    def seeds(self) -> tuple[int, ...]:
        """The seeds this experiment will use, derived from ``seed_root``.

        Deterministic and part of the registration, so the set of seeds is
        fixed before any of them runs — which is what makes reporting all of
        them checkable rather than trust-based.

        Drawn below :data:`~nullius.util.ids.EXPERIMENT_SEED_CEILING`, so an
        experiment can never land on a seed the bank's oracle used.
        """
        import numpy as np

        from nullius.util.ids import EXPERIMENT_SEED_CEILING

        generator = np.random.default_rng(self.seed_root)
        return tuple(
            int(s) for s in generator.integers(0, EXPERIMENT_SEED_CEILING, size=self.n_seeds)
        )

    def arm(self, name: str) -> ArmSpec:
        for arm in self.arms:
            if arm.name == name:
                return arm
        raise KeyError(f"no arm named {name!r}")

    def all_metrics(self) -> tuple[str, ...]:
        return (self.primary_metric, *self.secondary_metrics)
