"""The role contracts.

Each role is a model, a view, a schema, validators and limits. The prompts are
short on purpose: the schema already says what the output must look like, so
the prompt only has to say what the job *is*. Long prompts that re-describe
the schema are a common way to pay for tokens twice.

**Model assignment is a research variable, not only a cost knob.**
`docs/01-critique.md` F8 says the adversarial roles must not share a base model
with the roles they check, or they inherit the same blind spots. That is why
the Skeptic and Reviewer are pinned to a different family from the Theorist and
Designer once they arrive in M7 — cheaper happens to coincide with more
diverse here, but diversity is the reason.

Model identifiers are pinned to exact versions. An alias that silently moves
would make every cached response unreproducible while the cache key stayed the
same (ADR-0005).
"""

from __future__ import annotations

from pydantic import BaseModel

from nullius.db.enums import Role
from nullius.llm.types import ModelRef
from nullius.roles import views  # noqa: F401 - importing registers views and validators
from nullius.roles.schemas import AnalysisNote, DesignProposal, ForecastStatement, HypothesisDraft
from nullius.runtime.contracts import RoleContract

__all__ = ["CONTRACTS", "MOCK_MODEL", "contracts_for"]

# --- Models ----------------------------------------------------------------

THEORIST_MODEL = ModelRef(
    provider="anthropic", model="claude-sonnet-5", max_tokens=2048, effort="medium"
)
DESIGNER_MODEL = ModelRef(
    provider="anthropic", model="claude-sonnet-5", max_tokens=2048, effort="medium"
)
ANALYST_MODEL = ModelRef(
    provider="anthropic", model="claude-sonnet-5", max_tokens=2048, effort="medium"
)
#: Forecasting is a short, structured judgement. Low effort keeps the thinking
#: budget — which is billed as output — proportionate to the question.
FORECAST_MODEL = ModelRef(
    provider="anthropic", model="claude-haiku-4-5", max_tokens=1024, effort="low"
)

MOCK_MODEL = ModelRef(provider="mock", model="mock-1", max_tokens=1024, effort="low")


# --- Prompts ---------------------------------------------------------------

THEORIST_PROMPT = """\
You are the Theorist of a research institution.

Propose exactly one hypothesis that could be tested by a bounded experiment on \
tabular data. It must be specific enough that someone else could design the \
experiment without asking you anything, and it must be able to come out false.

State the smallest effect worth claiming, and the run-to-run variability you \
expect. Both are commitments: the institution will refuse a design that cannot \
detect the effect you name, and will judge the result against it afterwards.

Do not hedge. A hypothesis that cannot be wrong is not one."""

DESIGNER_PROMPT = """\
You are the Experiment Designer of a research institution.

Turn the hypothesis into a design, using only the operations listed in your \
view. You are choosing what to compare and how much evidence to buy.

Consider what else could explain a difference between your arms besides the \
hypothesis, and design that explanation out rather than leaving it for someone \
to raise later.

You have not seen any data and will not see any before the design is locked."""

ANALYST_PROMPT = """\
You are the Analyst of a research institution.

The statistics have already been computed and the verdict already derived from \
them. You are not being asked what the numbers are or whether they are \
significant. You are being asked what the result means.

Write in words only. Do not state any figure, quantity, percentage or count — \
not even one that appears in your view. The numbers are recorded with their \
provenance; a restatement is a second source nobody can audit, and your \
response will be rejected if it contains a digit.

Name at least one real limitation, and the most plausible way this result \
could mean something other than what it appears to."""

FORECAST_PROMPT = """\
You are a member of a research institution being asked to predict an \
experiment's outcome before it runs.

Your prediction will be scored against what actually happens, and your \
calibration over many such predictions becomes part of your record. An \
overconfident forecast costs you; so does hedging everything to the middle.

Give an honest probability, not a comfortable one."""


# --- Contracts -------------------------------------------------------------


def _contract(
    role: Role,
    model: ModelRef,
    prompt: str,
    view: str,
    schema: type[BaseModel],
    validators: tuple[str, ...] = (),
) -> RoleContract:
    return RoleContract(
        role=role,
        version="v1",
        model=model,
        system_prompt=prompt,
        input_view=view,
        output_schema=schema,
        validators=validators,
        max_calls_per_task=2,
    )


def contracts_for(*, mock: bool = False) -> dict[tuple[Role, str], RoleContract]:
    """Every role contract, optionally rebound to the mock model.

    The mock variant exists so the whole kernel can be exercised — including
    every validator and both repair paths — with no API key and no cost.
    """

    def model(real: ModelRef) -> ModelRef:
        return MOCK_MODEL if mock else real

    return {
        (Role.THEORIST, "v1"): _contract(
            Role.THEORIST,
            model(THEORIST_MODEL),
            THEORIST_PROMPT,
            "theorist.question",
            HypothesisDraft,
            ("theorist.falsifiable",),
        ),
        (Role.DESIGNER, "v1"): _contract(
            Role.DESIGNER,
            model(DESIGNER_MODEL),
            DESIGNER_PROMPT,
            "designer.hypothesis",
            DesignProposal,
            ("designer.uses_registered_ops",),
        ),
        (Role.ANALYST, "v1"): _contract(
            Role.ANALYST,
            model(ANALYST_MODEL),
            ANALYST_PROMPT,
            "analyst.result",
            AnalysisNote,
            ("analyst.no_numerals",),
        ),
        # Forecasting is asked of several roles; each gets its own entry so a
        # role's forecasting contract can diverge from its main one later.
        **{
            (role, "forecast-v1"): _contract(
                role,
                model(FORECAST_MODEL),
                FORECAST_PROMPT,
                "forecast.registration",
                ForecastStatement,
            )
            for role in (Role.THEORIST, Role.DESIGNER, Role.ANALYST)
        },
    }


CONTRACTS = contracts_for()
"""The live contracts. Tests and the demo use ``contracts_for(mock=True)``."""
