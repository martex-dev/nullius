"""What each role is allowed to emit.

Schemas are the protocol. A role cannot say anything the schema cannot
express, which is where most of the discipline in this system lives: the
Theorist cannot propose a vague hypothesis because there is no field for one,
and the Analyst cannot report a number because it has no numeric field to put
it in.

That last one is worth stating plainly. `docs/01-critique.md` puts computing a
statistic in the "never" column for language models, and this is how that is
enforced rather than requested — the Analyst receives numbers that code
computed and returns prose about them. A validator then checks the prose
contains no digits at all, so a model that decides to helpfully restate the
effect size fails instead of quietly becoming the source of a figure.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AnalysisNote",
    "DesignProposal",
    "ForecastStatement",
    "HypothesisDraft",
]

Sentence = Annotated[str, Field(min_length=20, max_length=600)]


class _Strict(BaseModel):
    """Frozen, and closed to fields nobody declared."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class HypothesisDraft(_Strict):
    """One falsifiable hypothesis.

    Every field is required, and together they make a vague hypothesis
    unrepresentable: there is nowhere to put "attention probably helps".
    """

    statement: Sentence
    """What is claimed, specifically enough to design an experiment from."""

    mechanism: Sentence
    """Why it might be true. Prose, and never cited as evidence."""

    primary_metric: Literal["macro_f1", "accuracy", "balanced_accuracy", "roc_auc"]
    direction: Literal["increase", "decrease", "no_change"]

    mde: float = Field(gt=0, le=1, description="Smallest effect worth claiming.")
    prior_sd: float = Field(
        gt=0, le=1, description="Expected per-seed standard deviation of the difference."
    )

    falsification_condition: Sentence
    """What result would count as this hypothesis being wrong."""

    assumptions: list[str] = Field(default_factory=list, max_length=8)


class DesignProposal(_Strict):
    """An experiment design, in the vocabulary of the operator registry.

    Deliberately narrow. The Designer chooses *which* registered operations to
    compare and how many seeds to spend; it cannot invent an operation, because
    the fields are enumerations over what the compiler can build.
    """

    treatment_transform: Literal["divergence_prune", "random_prune", "passthrough"]
    treatment_k: int = Field(ge=1, le=8, description="How many features the treatment drops.")
    estimator: Literal[
        "logistic_regression", "gradient_boosting", "random_forest", "majority_class"
    ]
    include_capacity_control: bool
    """Whether to add an arm dropping the same number of features at random.

    The Designer may say no. The linter will then refuse the design, and the
    refusal is recorded — which is how we find out whether the Designer knows
    it needs this control, rather than assuming it does.
    """

    n_seeds: int = Field(ge=1, le=50)
    tuning_budget: int = Field(ge=0, le=32)
    rationale: Sentence


class ForecastStatement(_Strict):
    """A role's locked prediction, made before the experiment runs.

    Scored afterwards with a proper scoring rule. The value is in the scoring,
    not in believing the number.
    """

    p_effect_exceeds_mde: float = Field(ge=0.0, le=1.0)
    predictive_mean: float = Field(ge=-1.0, le=1.0)
    predictive_sd: float = Field(gt=0.0, le=1.0)
    p_execution_success: float = Field(ge=0.0, le=1.0)
    reasoning: Sentence


class AnalysisNote(_Strict):
    """The Analyst's interpretation. Prose only.

    There is no numeric field here by design, and
    :func:`nullius.roles.views.no_numerals` rejects digits in the text. The
    numbers already exist, computed by
    :mod:`nullius.analysis`; an interpretation that restates them invites a
    later reader to trust the restatement rather than the source.
    """

    interpretation: Sentence
    """What the computed result means, in words."""

    limitations: list[Sentence] = Field(min_length=1, max_length=5)
    """At least one. A result with no stated limitation has not been read."""

    mechanism_supported: bool
    """Whether the observed direction is consistent with the proposed mechanism."""

    alternative_explanation: Sentence
    """The most plausible way the result could mean something else."""
