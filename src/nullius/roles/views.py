"""Input views and output validators.

A view is the entire world a role sees. Registering one is the only way to
widen what a role can look at, which makes "what did the Skeptic know?" a
question you answer by reading a registry rather than by tracing call sites.

The views here are deliberately thin. The Theorist gets a question and a
metric — what a researcher gets. The Designer gets the hypothesis and the list
of operations it may combine, and nothing about the data. Neither is ever
handed the bank item's generator parameters, because an agent told which
family of features moved would not need to run an experiment at all.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from nullius.build import ops
from nullius.runtime.contracts import (
    AgentTask,
    ValidationFailure,
    register_validator,
    register_view,
)

if TYPE_CHECKING:  # pragma: no cover
    from nullius.repository import Repository

__all__ = ["no_numerals"]

_DIGIT = re.compile(r"\d")


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@register_view("theorist.question")
def _theorist_question(repo: Repository, task: AgentTask) -> dict[str, Any]:
    """The research question, the metric, and the claimed effect. Nothing else."""
    return {
        "question": task.view.get("question", ""),
        "primary_metric": task.view.get("primary_metric", "macro_f1"),
        "claimed_effect": task.view.get("claimed_effect"),
        "domain": "tabular classification under distribution change",
    }


@register_view("designer.hypothesis")
def _designer_hypothesis(repo: Repository, task: AgentTask) -> dict[str, Any]:
    """The hypothesis plus the closed set of operations available to build it.

    The Designer sees no data and no results. It is choosing a design, and a
    design chosen with results in view is not a design.
    """
    return {
        "hypothesis": task.view.get("hypothesis", {}),
        "available_transforms": list(ops.registered_transforms()),
        "available_estimators": list(ops.registered_estimators()),
        "available_metrics": list(ops.registered_metrics()),
        "seed_policy_minimum": task.view.get("seed_policy_minimum", 5),
    }


@register_view("forecast.registration")
def _forecast_registration(repo: Repository, task: AgentTask) -> dict[str, Any]:
    """What a role is told before it commits to a prediction.

    The registered design and the claim, with no results — because there are
    none yet. A forecast made after results is not a forecast, and the ledger
    refuses one anyway.
    """
    return {
        "hypothesis": task.view.get("hypothesis", {}),
        "design": task.view.get("design", {}),
        "role_being_asked": task.role.value,
    }


@register_view("analyst.result")
def _analyst_result(repo: Repository, task: AgentTask) -> dict[str, Any]:
    """The computed statistics, and the verdict code already derived from them.

    The Analyst is not being asked what the numbers are, or what they imply
    statistically — both are settled before it is called. It is being asked
    what they *mean*, which is the part a person would want in words.
    """
    return {
        "hypothesis": task.view.get("hypothesis", {}),
        "computed_statistics": task.view.get("computed_statistics", {}),
        "verdict": task.view.get("verdict", ""),
        "verdict_reason": task.view.get("verdict_reason", ""),
        "seed_variance": task.view.get("seed_variance", {}),
    }


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


@register_validator("analyst.no_numerals")
def no_numerals(payload: BaseModel, view: dict[str, Any]) -> None:
    """Reject any digit in the Analyst's prose.

    Blunt on purpose. The numbers exist already, computed by code and stored
    with their provenance; a restatement in prose is a second source that
    nobody can audit and that a later reader may quote instead of the first.
    Rejecting digits outright is a rule with no judgement in it, which is what
    makes it hold.
    """
    fields = payload.model_dump()
    offending: list[str] = []

    for name, value in fields.items():
        texts = value if isinstance(value, list) else [value]
        for text in texts:
            if isinstance(text, str) and _DIGIT.search(text):
                offending.append(name)
                break

    if offending:
        raise ValidationFailure(
            f"the interpretation contains digits in {sorted(set(offending))}. "
            "Numbers are computed and stored by the analysis harness; describe "
            "the result in words and let the figures speak for themselves."
        )


@register_validator("designer.uses_registered_ops")
def uses_registered_ops(payload: BaseModel, view: dict[str, Any]) -> None:
    """The operator registry is closed; a design naming anything else cannot build."""
    fields = payload.model_dump()

    transform = fields.get("treatment_transform")
    if transform is not None and transform not in ops.registered_transforms():
        raise ValidationFailure(f"unknown transform {transform!r}")

    estimator = fields.get("estimator")
    if estimator is not None and estimator not in ops.registered_estimators():
        raise ValidationFailure(f"unknown estimator {estimator!r}")


@register_validator("theorist.falsifiable")
def falsifiable(payload: BaseModel, view: dict[str, Any]) -> None:
    """A falsification condition that does not describe an outcome is decoration."""
    fields = payload.model_dump()
    condition = str(fields.get("falsification_condition", "")).lower()

    if not any(
        word in condition
        for word in ("if", "when", "unless", "fails", "does not", "smaller", "less", "below", "no ")
    ):
        raise ValidationFailure(
            "the falsification condition does not describe an observable outcome; "
            "state what result would show the hypothesis to be wrong"
        )

    mde = fields.get("mde")
    if isinstance(mde, (int, float)) and mde <= 0:
        raise ValidationFailure("a hypothesis must claim a positive effect size")
