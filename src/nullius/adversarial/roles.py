"""The Skeptic and the Reviewer.

Both are given information the roles they check do not have, and denied
information those roles do have. That asymmetry is the whole design.

The **Skeptic** sees the registered design and the raw per-arm numbers. It
does *not* see the Analyst's interpretation — `docs/02-architecture.md` §2.3:
it attacks artifacts, not narrative. A Skeptic reading a confident summary
tends to argue with the summary.

The **Reviewer** sees a structured evidence bundle: the verdict, the computed
confidence, the objections and their status. It does not see prose either. It
is a state-transition gate, not a prose critic, because prose review is
unscoreable.

Both run on a different model family from the Theorist and Designer. That is
`docs/01-critique.md` F8 — an adversary sharing a base model with the work it
checks inherits the same blind spots — and it is a research variable, not a
cost decision, even though the cheaper model happens to be the diverse one
here.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from nullius.build import ops
from nullius.db.enums import Role
from nullius.llm.types import ModelRef
from nullius.runtime.contracts import (
    AgentTask,
    RoleContract,
    ValidationFailure,
    register_validator,
    register_view,
)

__all__ = [
    "REVIEWER_MODEL",
    "SKEPTIC_MODEL",
    "ObjectionStatement",
    "ReviewStatement",
    "SkepticReport",
    "adversarial_contracts",
]

Sentence = Annotated[str, Field(min_length=20, max_length=600)]

#: A different family from the Theorist and Designer, deliberately.
SKEPTIC_MODEL = ModelRef(
    provider="anthropic", model="claude-opus-5", max_tokens=3072, effort="high"
)
REVIEWER_MODEL = ModelRef(
    provider="anthropic", model="claude-haiku-4-5", max_tokens=1536, effort="low"
)


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ObjectionStatement(_Strict):
    """One typed objection, with the experiment that would settle it."""

    objection_type: Literal[
        "leakage",
        "contamination",
        "weak_baseline",
        "confound",
        "multiple_testing",
        "seed_instability",
        "metric_invalid",
        "underpowered",
        "implementation_bug",
        "alternative_explanation",
        "generalisation_overreach",
        "artifact_of_benchmark",
    ]
    severity: Literal["minor", "major", "critical"]
    statement: Sentence
    discriminating_test: Sentence
    """What experiment would settle this. Required, and checked to describe
    an action rather than a wish."""


class SkepticReport(_Strict):
    """Everything the Skeptic wants to say. Possibly nothing.

    An empty list is a legitimate and useful answer. A Skeptic that always
    finds something is as uninformative as one that never does, and the
    scoring in :mod:`nullius.adversarial.scoring` measures precision as well
    as recall precisely so that manufacturing objections is costly.
    """

    objections: list[ObjectionStatement] = Field(default_factory=list, max_length=5)
    assessment: Sentence


class ReviewStatement(_Strict):
    """The Reviewer's decision, and the scores behind it."""

    decision: Literal["accept", "minor_revision", "major_revision", "reject"]
    novelty: int = Field(ge=1, le=5)
    methodological_quality: int = Field(ge=1, le=5)
    statistical_quality: int = Field(ge=1, le=5)
    reproducibility: int = Field(ge=1, le=5)
    rationale: Sentence


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@register_view("skeptic.evidence")
def _skeptic_evidence(repo: Any, task: AgentTask) -> dict[str, Any]:
    """The design and the raw numbers. No interpretation.

    Everything here is a fact about what was run and what came out. The
    Analyst's reading of it is deliberately absent.
    """
    return {
        "design": task.view.get("design", {}),
        "per_arm_results": task.view.get("per_arm_results", {}),
        "computed_statistics": task.view.get("computed_statistics", {}),
        "available_transforms": list(ops.registered_transforms()),
        "available_estimators": list(ops.registered_estimators()),
        "detector_findings": task.view.get("detector_findings", []),
    }


@register_view("reviewer.bundle")
def _reviewer_bundle(repo: Any, task: AgentTask) -> dict[str, Any]:
    """A structured evidence bundle. No prose to be persuaded by."""
    return {
        "verdict": task.view.get("verdict", ""),
        "verdict_reason": task.view.get("verdict_reason", ""),
        "computed_statistics": task.view.get("computed_statistics", {}),
        "confidence": task.view.get("confidence", ""),
        "confidence_caps": task.view.get("confidence_caps", []),
        "objections": task.view.get("objections", []),
        "replication": task.view.get("replication", "none"),
        "lint_findings": task.view.get("lint_findings", []),
    }


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

_ACTION_WORDS = (
    "run",
    "rerun",
    "add",
    "remove",
    "compare",
    "refit",
    "repeat",
    "increase",
    "hold",
    "swap",
    "measure",
    "test",
    "check",
    "drop",
    "vary",
    "randomis",
    "randomiz",
)


@register_validator("skeptic.tests_are_actionable")
def tests_are_actionable(payload: BaseModel, view: dict[str, Any]) -> None:
    """Every objection must name an experiment, not a worry.

    `docs/01-critique.md` F6. Criticism no experiment could resolve cannot
    block a claim, however well written, so it is rejected here rather than
    argued with later.
    """
    fields = payload.model_dump()
    for index, objection in enumerate(fields.get("objections", [])):
        test = str(objection.get("discriminating_test", "")).lower()
        if not any(word in test for word in _ACTION_WORDS):
            raise ValidationFailure(
                f"objection {index} ({objection.get('objection_type')}) names no "
                "experiment that could settle it. State what to run, not what to "
                "worry about."
            )


@register_validator("reviewer.decision_matches_scores")
def decision_matches_scores(payload: BaseModel, view: dict[str, Any]) -> None:
    """A decision has to follow from the scores it reports.

    Not a judgement about whether the Reviewer is right — only that it is
    self-consistent. Accepting work it scored as poor, or rejecting work it
    scored highly, means one of the two is decoration.
    """
    fields = payload.model_dump()
    scores = [
        fields["novelty"],
        fields["methodological_quality"],
        fields["statistical_quality"],
        fields["reproducibility"],
    ]
    decision = fields["decision"]
    worst, average = min(scores), sum(scores) / len(scores)

    if decision == "accept" and (worst <= 2 or average < 3.0):
        raise ValidationFailure(
            f"accepted work scored {scores}; a decision must follow from the scores it reports"
        )
    if decision == "reject" and average >= 4.0:
        raise ValidationFailure(f"rejected work scored {scores}, which does not follow")


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------

SKEPTIC_PROMPT = """\
You are the Skeptic of a research institution. Your job is not to help this \
result stand up. It is to find the reason it might be wrong.

You are looking at a design and the numbers it produced. You have not been \
shown anyone's interpretation, and you should not ask for one.

For each objection, name the experiment that would settle it. An objection no \
experiment could resolve cannot block anything and will be rejected.

Raising nothing is a legitimate answer when the work is sound. You are scored \
on precision as well as recall, so an objection you do not believe costs you."""

REVIEWER_PROMPT = """\
You are the Reviewer of a research institution, deciding whether a claim may \
enter the institutional record.

You have a structured bundle: the verdict, the computed confidence and what \
capped it, the standing objections, and whether the work was independently \
reproduced. There is no prose to weigh, by design.

Score what you see and let the decision follow from the scores. You may reject \
work that everyone else was happy with; that is what the role is for."""


def adversarial_contracts(*, mock: bool = False) -> dict[tuple[Role, str], RoleContract]:
    """Contracts for the Skeptic and Reviewer."""
    from nullius.roles.contracts import MOCK_MODEL

    def model(real: ModelRef) -> ModelRef:
        return MOCK_MODEL if mock else real

    return {
        (Role.SKEPTIC, "v1"): RoleContract(
            role=Role.SKEPTIC,
            version="v1",
            model=model(SKEPTIC_MODEL),
            system_prompt=SKEPTIC_PROMPT,
            input_view="skeptic.evidence",
            output_schema=SkepticReport,
            validators=("skeptic.tests_are_actionable",),
            max_calls_per_task=2,
        ),
        (Role.REVIEWER, "v1"): RoleContract(
            role=Role.REVIEWER,
            version="v1",
            model=model(REVIEWER_MODEL),
            system_prompt=REVIEWER_PROMPT,
            input_view="reviewer.bundle",
            output_schema=ReviewStatement,
            validators=("reviewer.decision_matches_scores",),
            max_calls_per_task=2,
        ),
    }
