"""What the institution actually did with each bank item, measured once and locked.

The allocation comparison needs to know, per item, what the institution
concluded and what it cost. Those are expensive to obtain — a full pass
through the research lifecycle, every declared seed executed in the sandbox —
and they do not depend on which allocation policy was in charge. So they are
measured once, written to a lock file beside ``bank/truth.lock.json``, and
thereafter read.

**This is the design decision that makes the M9 comparison mean anything.**
Holding the science fixed and varying only the allocator is what isolates
allocation as a cause. If each policy ran its own experiments, the arms would
differ in what was executed as well as in what was chosen, and a difference in
cost-per-correct-claim could not be attributed to either.

The consequence, stated plainly: this measures *selection*. A policy is scored
on which questions it chose to spend money on, given what spending money on
each of them turns out to buy. It does not measure a policy's effect on how
well an experiment is subsequently run, which is real and is not captured here.

**Two disclosed substitutions.**

Token costs are real counts priced *as if* produced by a named model, because
:class:`~nullius.llm.providers.MockProvider` is free and an economy whose
numerator is identically zero compares nothing. Compute costs are not
substituted at all — the seconds were genuinely burned.

The verdicts are not substituted in any way. Every one comes from real data,
the real compiler, the real sandbox and the real statistics; only the prose
around them came from a mock.

**What re-running this does and does not reproduce.** Experiment seeds are
fixed by the preregistration and derived from the item id by
:func:`~nullius.util.ids.seed_for`, so they are identical on every run. The
*evaluation* sample is not: :func:`~nullius.custody.custodian.custody_seed`
derives it from the registration id, and a fresh measurement writes fresh
registrations, so the Custodian draws a new holdout each time. That is
deliberate — a custody seed derived from the design would let anyone
re-register the same spec repeatedly and shop a fixed evaluation set, which is
precisely what the query budget exists to stop.

The consequence is worth stating plainly: ``realised_effect`` moves between
measurements, and an item sitting close to a verdict boundary could in
principle move with it. Two consecutive full measurements were compared when
this was written; all twenty verdicts agreed while every realised effect
differed. The lock therefore records one honest draw, not a canonical one.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from nullius.bank.items import BANK_V1, BankItem
from nullius.bank.lock import DEFAULT_LOCK_PATH as TRUTH_LOCK_PATH
from nullius.bank.lock import read_lock
from nullius.bank.truth import Truth, boundary_margin
from nullius.db.enums import Role, Verdict
from nullius.db.tables import CostEntry, Forecast, Run
from nullius.economy.eig import RoleForecast
from nullius.kernel import KernelOutcome, ResearchKernel
from nullius.llm.pricing import price_of
from nullius.repository import Repository
from nullius.util.canonical import canonical_json, sha256_of

__all__ = [
    "AS_IF_MODEL",
    "DEFAULT_OUTCOMES_PATH",
    "ItemOutcome",
    "measure_item",
    "read_outcomes",
    "write_outcomes",
]

DEFAULT_OUTCOMES_PATH = Path("bank/outcomes.lock.json")

AS_IF_MODEL = "claude-sonnet-5"
"""The model whose rates price a mock-driven programme's token counts.

Named in the lock file, so a reader can see that the dollars are conditional
on a model that never ran, and can reprice them if they disagree. Sonnet
rather than Opus because it is the tier these role contracts specify for
everything but the Theorist.
"""


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    """One bank item, carried to a verdict, with what that cost.

    ``correct`` compares against the bank's measured truth, not against
    anyone's expectation. An institution that says ``no_effect`` about an item
    whose true effect is zero has got it right, and is scored as having got it
    right, which is the whole reason half the bank is null.
    """

    item_id: str
    verdict: Verdict
    truth_verdict: Verdict
    true_effect: float
    boundary_margin: float
    realised_effect: float
    usd: Decimal
    llm_usd: Decimal
    compute_usd: Decimal
    n_seeds: int
    n_arms: int
    forecasts: tuple[RoleForecast, ...] = ()
    halted: str | None = None

    @property
    def reached_a_verdict(self) -> bool:
        return self.halted is None

    @property
    def correct(self) -> bool:
        """Did the institution reach the answer the oracle measured?"""
        return self.reached_a_verdict and self.verdict is self.truth_verdict

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "verdict": self.verdict.value,
            "truth_verdict": self.truth_verdict.value,
            "true_effect": round(self.true_effect, 6),
            "boundary_margin": round(self.boundary_margin, 6),
            "realised_effect": round(self.realised_effect, 6),
            "usd": str(self.usd),
            "llm_usd": str(self.llm_usd),
            "compute_usd": str(self.compute_usd),
            "n_seeds": self.n_seeds,
            "n_arms": self.n_arms,
            "halted": self.halted,
            "forecasts": [
                {
                    "role": f.role.value,
                    "predictive_mean": round(f.predictive_mean, 6),
                    "predictive_sd": round(f.predictive_sd, 6),
                    "p_effect_exceeds_mde": round(f.p_effect_exceeds_mde, 6),
                    "p_execution_success": round(f.p_execution_success, 6),
                }
                for f in self.forecasts
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ItemOutcome:
        return cls(
            item_id=payload["item_id"],
            verdict=Verdict(payload["verdict"]),
            truth_verdict=Verdict(payload["truth_verdict"]),
            true_effect=payload["true_effect"],
            boundary_margin=payload["boundary_margin"],
            realised_effect=payload["realised_effect"],
            usd=Decimal(payload["usd"]),
            llm_usd=Decimal(payload["llm_usd"]),
            compute_usd=Decimal(payload["compute_usd"]),
            n_seeds=payload["n_seeds"],
            n_arms=payload["n_arms"],
            halted=payload.get("halted"),
            forecasts=tuple(
                RoleForecast(
                    role=Role(f["role"]),
                    predictive_mean=f["predictive_mean"],
                    predictive_sd=f["predictive_sd"],
                    p_effect_exceeds_mde=f["p_effect_exceeds_mde"],
                    p_execution_success=f["p_execution_success"],
                )
                for f in payload.get("forecasts", ())
            ),
        )

    def __str__(self) -> str:
        mark = "correct" if self.correct else "wrong"
        return (
            f"{self.item_id}: said {self.verdict.value}, truth {self.truth_verdict.value} "
            f"({mark}) for ${self.usd:.6f}"
        )


def _as_if_llm_usd(repo: Repository, program_id: uuid.UUID) -> Decimal:
    """Price the programme's real token counts at a real model's rates.

    Reads the token *counts* from the cost ledger while ignoring its dollars.
    The counts are a measurement of what the roles actually sent and received;
    the dollars correctly record what the mock charged, which is nothing.
    """
    price = price_of(AS_IF_MODEL)
    rows = repo.session.execute(
        sa.select(
            sa.func.sum(CostEntry.llm_input_tokens),
            sa.func.sum(CostEntry.llm_output_tokens),
        ).where(CostEntry.program_id == program_id, CostEntry.task_id.is_not(None))
    ).one()
    inputs, outputs = (int(value or 0) for value in rows)
    return (
        Decimal(inputs) * price.input_usd_per_mtok + Decimal(outputs) * price.output_usd_per_mtok
    ) / Decimal(1_000_000)


def _compute_usd(repo: Repository, program_id: uuid.UUID) -> Decimal:
    amounts = repo.session.scalars(
        sa.select(CostEntry.usd).where(
            CostEntry.program_id == program_id, CostEntry.run_id.is_not(None)
        )
    )
    return sum(amounts, Decimal(0))


def _forecasts(repo: Repository, registration_id: uuid.UUID | None) -> tuple[RoleForecast, ...]:
    if registration_id is None:
        return ()
    rows = repo.session.scalars(
        sa.select(Forecast)
        .where(Forecast.registration_id == registration_id)
        .order_by(Forecast.role)
    )
    return tuple(
        RoleForecast(
            role=row.role,
            predictive_mean=row.predictive_mean,
            predictive_sd=row.predictive_sd,
            p_effect_exceeds_mde=row.p_effect_exceeds_mde,
            p_execution_success=row.p_execution_success,
        )
        for row in rows
    )


def measure_item(
    kernel: ResearchKernel,
    repo: Repository,
    item: BankItem,
    truth: Truth,
    *,
    program_id: uuid.UUID,
    allowance: Decimal = Decimal("0.50"),
) -> ItemOutcome:
    """Run one bank item all the way through, and record what it produced.

    A halted lifecycle is an outcome, not an error: it consumed budget and
    produced no claim, which is exactly the case an allocator should be
    penalised for choosing. It is recorded with its reason and counted as
    incorrect.
    """
    result: KernelOutcome = kernel.run_item(item, program_id=program_id, allowance=allowance)

    llm_usd = _as_if_llm_usd(repo, program_id)
    compute_usd = _compute_usd(repo, program_id)
    n_arms = len(result.spec.arms) if result.spec is not None else 0
    n_seeds = 0
    if result.registration_id is not None:
        n_seeds = len(
            {
                run.seed
                for run in repo.session.scalars(
                    sa.select(Run).where(Run.registration_id == result.registration_id)
                )
            }
        )

    return ItemOutcome(
        item_id=item.item_id,
        verdict=result.verdict.verdict if result.verdict is not None else Verdict.INCONCLUSIVE,
        truth_verdict=truth.verdict,
        true_effect=truth.effect,
        boundary_margin=boundary_margin(truth),
        realised_effect=result.analysis.difference if result.analysis is not None else 0.0,
        usd=llm_usd + compute_usd,
        llm_usd=llm_usd,
        compute_usd=compute_usd,
        n_seeds=max(n_seeds, 1),
        n_arms=max(n_arms, 1),
        forecasts=_forecasts(repo, result.registration_id),
        halted=result.halted,
    )


def write_outcomes(
    outcomes: Iterable[ItemOutcome],
    path: Path = DEFAULT_OUTCOMES_PATH,
    *,
    items: Sequence[BankItem] = BANK_V1,
) -> Path:
    """Write the outcomes lock, hashed against the items it describes.

    The hash is the same guard ``bank/truth.lock.json`` uses: a change to the
    bank must invalidate the measurements taken on the old bank rather than
    silently coexist with them.
    """
    payload = {
        "version": 1,
        "items_hash": sha256_of([item.as_dict() for item in items]),
        "as_if_model": AS_IF_MODEL,
        "outcomes": [outcome.as_dict() for outcome in outcomes],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    return path


def read_outcomes(path: Path = DEFAULT_OUTCOMES_PATH) -> list[ItemOutcome]:
    """Load the locked outcomes, in the order they were measured."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [ItemOutcome.from_dict(entry) for entry in payload["outcomes"]]


def outcomes_are_current(path: Path = DEFAULT_OUTCOMES_PATH) -> bool:
    """Whether the locked outcomes were measured on the bank as it stands now."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return bool(payload.get("items_hash") == sha256_of([i.as_dict() for i in BANK_V1]))


def truths_for(path: Path = TRUTH_LOCK_PATH) -> dict[str, Truth]:
    """The locked ground truth, for a caller measuring outcomes against it."""
    return read_lock(path)


# ---------------------------------------------------------------------------
# Driving the measurement
# ---------------------------------------------------------------------------

CANNED_ANSWERS: dict[str, dict[str, Any]] = {
    "Theorist": {
        "statement": (
            "Dropping the features whose distributions differ most between environments "
            "will raise deployment macro-F1 relative to using every feature."
        ),
        "mechanism": (
            "Features whose relationship to the label is unstable across environments "
            "mislead a model that learned to rely on them during training."
        ),
        "primary_metric": "macro_f1",
        "direction": "increase",
        "mde": 0.02,
        "prior_sd": 0.006,
        "falsification_condition": (
            "If the interval for the paired difference does not exceed the claimed effect, "
            "the hypothesis is wrong."
        ),
        "assumptions": ["the deployment covariates are observable before labelling"],
    },
    "Experiment Designer": {
        "treatment_transform": "divergence_prune",
        "treatment_k": 3,
        "estimator": "logistic_regression",
        "include_capacity_control": True,
        "n_seeds": 5,
        "tuning_budget": 4,
        "rationale": (
            "Comparing against an arm that drops as many features at random separates "
            "the choice of features from the reduction in count."
        ),
    },
    "predict an": {
        "p_effect_exceeds_mde": 0.65,
        "predictive_mean": 0.05,
        "predictive_sd": 0.04,
        "p_execution_success": 0.95,
        "reasoning": (
            "The mechanism is plausible and the design is adequately powered for the "
            "effect it claims to detect."
        ),
    },
    "Analyst": {
        "interpretation": (
            "The computed comparison is reported as it stands, with the direction and "
            "magnitude left to the statistics rather than restated here."
        ),
        "limitations": [
            "Only one family of distribution change was examined, on synthetic data.",
        ],
        "mechanism_supported": True,
        "alternative_explanation": (
            "The change could come from reducing the number of features rather than "
            "from which features were removed."
        ),
    },
}
"""What the mock says, keyed by a marker in each role's system prompt.

The *forecast* is identical for every bank item, and that is the finding
rather than a shortcoming: a forecaster that says the same thing about every
question carries no information about which question is worth funding, which
is exactly what :mod:`nullius.economy.harness` measures and reports.

The *hypothesis* is not identical, and cannot be. Two questions about two
different datasets that produce the same sentence are the same hypothesis, and
the institutional-novelty guard from M8 refuses the second one — correctly.
:func:`canned_responder` therefore names the item under study, which is what a
Theorist that had read the question would do anyway.
"""

_ITEM_ID = re.compile(r'"item_id":\s*"([^"]+)"')


def canned_responder() -> Any:
    """A responder for :class:`~nullius.llm.providers.MockProvider`.

    Reads the item under study out of the view it was sent, so that a
    programme proposing several questions produces several distinguishable
    hypotheses rather than one hypothesis and a stack of duplicate-rejections.
    """
    from nullius.llm.types import LlmRequest

    def respond(request: LlmRequest) -> dict[str, Any]:
        for marker, payload in CANNED_ANSWERS.items():
            if marker not in request.system:
                continue
            if marker != "Theorist":
                return payload

            # Only the Theorist sees the question, and only its answer has to
            # be distinguishable between items.
            content = request.messages[0].content if request.messages else ""
            found = _ITEM_ID.search(content)
            item_id = found.group(1) if found else "an unnamed dataset"
            return {
                **payload,
                "statement": f"On dataset {item_id}, {payload['statement'][0].lower()}"
                f"{payload['statement'][1:]}",
            }
        raise KeyError(f"no canned response for system prompt: {request.system[:60]}")

    return respond


def measure_bank(
    database: Path,
    workroot: Path,
    *,
    items: Sequence[BankItem] = BANK_V1,
    truth_lock: Path = TRUTH_LOCK_PATH,
    budget_usd: Decimal = Decimal("25.00"),
) -> list[ItemOutcome]:
    """Carry every bank item through the lifecycle, one programme each.

    A programme per item, deliberately. Cost is a programme-scoped quantity, so
    twenty items in one programme would leave no way to say what any single
    item cost — and cost per item is half of the number this whole milestone is
    about.
    """
    from nullius.db.base import create_engine, create_schema, session_factory
    from nullius.execute.sandbox import SubprocessSandbox
    from nullius.llm.providers import MockProvider
    from nullius.store.cas import ContentStore

    truths = read_lock(truth_lock)
    workroot.mkdir(parents=True, exist_ok=True)
    database.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database)
    create_schema(engine)

    outcomes: list[ItemOutcome] = []
    with session_factory(engine)() as session:
        repo = Repository(session, Role.SYSTEM)
        lab = repo.create_lab("Nullius", "Measure whether structure helps.")
        policy = repo.create_policy("m9-measurement", {"min_seeds": 5}, "Bank measurement pass.")

        kernel = ResearchKernel(
            repo,
            MockProvider(canned_responder()),
            SubprocessSandbox(),
            ContentStore(workroot / "objects"),
            workroot / "runs",
            mock=True,
        )

        for item in items:
            truth = truths[item.item_id]
            rq = repo.create_research_question(
                item.question, domain="tabular-ml", bank_item_id=item.item_id
            )
            program = repo.create_program(
                rq_id=rq.rq_id,
                lab_id=lab.lab_id,
                policy_id=policy.policy_id,
                budget_usd=budget_usd,
                config_hash="0" * 64,
                capability_digest="1" * 64,
            )
            outcomes.append(measure_item(kernel, repo, item, truth, program_id=program.program_id))
            repo.commit()

    return outcomes
