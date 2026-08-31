"""The Director's allocation pass: state in, decisions out.

Every number the policy sees is assembled here from rows that already exist —
forecasts from the Forecast Ledger, costs from the cost ledger, seed variance
from past runs. Nothing is asked of a model, and nothing is invented. That is
what makes an allocation decision auditable: the ``inputs`` column of the
:class:`~nullius.db.tables.Decision` row holds every figure the policy
consumed, so the decision can be recomputed and disputed by anyone who thinks
it was wrong.

The split is deliberate. :mod:`nullius.economy.policy` contains the ranking
rules and knows nothing about the database; this module contains the database
and knows nothing about ranking. A policy is therefore testable on invented
candidates, and the assembly is testable against a real programme, without
either having to stand up the other.

**One decision row per candidate**, funded or shelved. A ledger that recorded
only what was funded would make the counterfactual — what the institution
declined to learn, and on what grounds — unrecoverable, and that is precisely
the record a critic needs.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from nullius.analysis.stats import seed_variance
from nullius.db.enums import Role, RunStatus
from nullius.db.tables import Forecast, ForecastScore, Hypothesis, Registration, Run, RunResult
from nullius.economy.cost_model import CostModel, observations_for_program
from nullius.economy.eig import (
    Gaussian,
    RoleForecast,
    expected_information_gain,
    measurement_sd,
)
from nullius.economy.policy import Allocation, AllocationPolicy, Candidate, CandidateKind, Reserves
from nullius.repository import Repository

__all__ = ["DEFAULT_PRIOR_SD", "DEFAULT_SEED_SD", "Allocator", "candidates_for_program"]

DEFAULT_PRIOR_SD = 0.02
"""Prior spread over the effect when a hypothesis declares none.

One claimed effect size. A prior that says "the effect is about as likely to
be nothing as to be the size claimed" is the weakest defensible starting
point, and it is stated here rather than derived so that nobody mistakes it
for a measurement.
"""

DEFAULT_SEED_SD = 0.01
"""Seed-to-seed spread assumed for a design with no execution history.

Superseded by the measured spread the moment this programme has run anything;
:func:`candidates_for_program` prefers the measurement wherever one exists.
"""


def _forecasts_for(repo: Repository, registration_id: uuid.UUID) -> list[RoleForecast]:
    rows = repo.session.scalars(
        sa.select(Forecast)
        .where(Forecast.registration_id == registration_id)
        .order_by(Forecast.role, Forecast.created_at)
    )
    return [
        RoleForecast(
            role=row.role,
            predictive_mean=row.predictive_mean,
            predictive_sd=row.predictive_sd,
            p_effect_exceeds_mde=row.p_effect_exceeds_mde,
            p_execution_success=row.p_execution_success,
        )
        for row in rows
    ]


def _mean_brier(repo: Repository) -> dict[Role, float]:
    """Each role's average Brier score across everything it has forecast.

    Institution-wide rather than per-programme: calibration is a property of a
    role, and restricting the history to the programme currently being
    allocated would throw away most of the evidence about who is worth
    listening to.
    """
    rows = repo.session.execute(
        sa.select(ForecastScore.role, sa.func.avg(ForecastScore.brier_score)).group_by(
            ForecastScore.role
        )
    ).all()
    return {Role(role): float(score) for role, score in rows if score is not None}


def _measured_seed_sd(repo: Repository, program_id: uuid.UUID) -> float | None:
    """Spread of the primary metric across completed seeds, if there is any.

    Any completed run in the programme counts. The quantity being estimated is
    how noisy this *machine on this data* is, which does not change between
    hypotheses, so restricting it to one registration would discard evidence
    for no gain.
    """
    values = list(
        repo.session.scalars(
            sa.select(RunResult.value)
            .join(Run, Run.run_id == RunResult.run_id)
            .join(Registration, Registration.registration_id == Run.registration_id)
            .join(Hypothesis, Hypothesis.hypothesis_id == Registration.hypothesis_id)
            .where(Hypothesis.program_id == program_id, Run.status == RunStatus.COMPLETED)
        )
    )
    if len(values) < 2:
        return None
    spread = seed_variance(values).sd
    return spread if spread > 0 else None


def candidates_for_program(
    repo: Repository,
    program_id: uuid.UUID,
    *,
    registration_ids: Sequence[uuid.UUID] | None = None,
    cost_model: CostModel | None = None,
    fallback_cost_usd: Decimal = Decimal("0.05"),
    seed: int = 0,
) -> list[Candidate]:
    """Score every registration awaiting a funding decision.

    Registrations without a single forecast are skipped rather than scored
    with a default. A candidate whose information gain was assumed rather than
    elicited would compete against ones that were measured, and the allocator
    would be ranking partly on which experiments happened to have been asked
    about.
    """
    model = cost_model or CostModel.fit(
        observations_for_program(repo, program_id), fallback_usd=fallback_cost_usd
    )
    brier = _mean_brier(repo)
    measured_sd = _measured_seed_sd(repo, program_id)

    query = (
        sa.select(Registration, Hypothesis)
        .join(Hypothesis, Hypothesis.hypothesis_id == Registration.hypothesis_id)
        .where(Hypothesis.program_id == program_id)
        .order_by(Registration.registered_at, Registration.registration_id)
    )
    if registration_ids is not None:
        query = query.where(Registration.registration_id.in_(list(registration_ids)))

    candidates: list[Candidate] = []
    for registration, hypothesis in repo.session.execute(query).all():
        forecasts = _forecasts_for(repo, registration.registration_id)
        if not forecasts:
            continue

        spec = registration.spec or {}
        n_arms = max(1, len(spec.get("arms", ())))
        prior_sd = float(spec.get("prior_sd") or DEFAULT_PRIOR_SD)

        report = expected_information_gain(
            forecasts,
            prior=Gaussian(0.0, prior_sd),
            noise_sd=measurement_sd(measured_sd or DEFAULT_SEED_SD, registration.n_seeds),
            mean_brier=brier,
            seed=seed,
        )
        candidates.append(
            Candidate(
                subject_id=registration.registration_id,
                label=str(spec.get("title") or hypothesis.statement)[:80],
                kind=_kind_of(registration),
                eig=report.nats,
                p_success=min(f.p_execution_success for f in forecasts),
                expected_cost_usd=model.predict(n_seeds=registration.n_seeds, n_arms=n_arms),
                group=str(hypothesis.hypothesis_id),
            )
        )
    return candidates


def _kind_of(registration: Registration) -> CandidateKind:
    """Which pocket a registration is paid from.

    A replication registration is a replication; anything else is exploration.
    Null confirmation is not inferable from the registration — it is a
    deliberate choice to spend money confirming that nothing is there, and the
    caller who made that choice sets the kind.
    """
    return (
        CandidateKind.REPLICATION
        if registration.kind.value == "replication"
        else CandidateKind.EXPLORATION
    )


@dataclass(frozen=True, slots=True)
class Allocator:
    """Runs one allocation pass and writes what it decided to the ledger."""

    repo: Repository
    policy: AllocationPolicy
    policy_id: uuid.UUID
    reserves: Reserves = field(default_factory=Reserves)

    def decide(
        self,
        candidates: Sequence[Candidate],
        *,
        program_id: uuid.UUID,
        budget_usd: Decimal,
    ) -> Allocation:
        """Allocate, then record one decision per candidate.

        The whole allocation is written into every row's ``inputs``, not just
        the candidate's own numbers. A funding decision is comparative — this
        was funded *instead of* those — and a record that omitted the
        alternatives would describe a choice that was never actually made.
        """
        allocation = self.policy.allocate(candidates, budget_usd=budget_usd, reserves=self.reserves)
        director = self.repo.as_role(Role.DIRECTOR)
        context = allocation.as_inputs()

        for candidate in allocation.funded:
            director.record_decision(
                program_id=program_id,
                policy_id=self.policy_id,
                kind="fund",
                subject_id=candidate.subject_id,
                inputs={"candidate": candidate.as_dict(), "allocation": context},
                outcome="funded",
            )
        for candidate, reason in allocation.shelved:
            director.record_decision(
                program_id=program_id,
                policy_id=self.policy_id,
                kind="shelve",
                subject_id=candidate.subject_id,
                inputs={
                    "candidate": candidate.as_dict(),
                    "allocation": context,
                    "reason": reason,
                },
                outcome="shelved",
            )
        return allocation

    def record(
        self,
        allocation: Allocation,
        *,
        owner: Mapping[uuid.UUID, uuid.UUID],
    ) -> None:
        """Write an allocation whose candidates span several programmes.

        A :class:`~nullius.db.tables.Decision` names the programme it belongs
        to, so a single round-wide allocation is recorded programme by
        programme — each row against the one whose work it concerns. ``owner``
        maps a candidate's subject to that programme.

        A candidate with no owner is skipped rather than filed somewhere
        plausible. Guessing which programme a decision belongs to would put a
        wrong foreign key in the one table whose purpose is to say who decided
        what about whom.
        """
        context = allocation.as_inputs()
        director = self.repo.as_role(Role.DIRECTOR)

        for candidate in allocation.funded:
            program_id = owner.get(candidate.subject_id)
            if program_id is None:
                continue
            director.record_decision(
                program_id=program_id,
                policy_id=self.policy_id,
                kind="fund",
                subject_id=candidate.subject_id,
                inputs={"candidate": candidate.as_dict(), "allocation": context},
                outcome="funded",
            )
        for candidate, reason in allocation.shelved:
            program_id = owner.get(candidate.subject_id)
            if program_id is None:
                continue
            director.record_decision(
                program_id=program_id,
                policy_id=self.policy_id,
                kind="shelve",
                subject_id=candidate.subject_id,
                inputs={
                    "candidate": candidate.as_dict(),
                    "allocation": context,
                    "reason": reason,
                },
                outcome="shelved",
            )

    def summary(self, program_id: uuid.UUID) -> dict[str, Any]:
        """What this programme has decided so far, by outcome."""
        rows = self.repo.decisions_for_program(program_id)
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.outcome] = counts.get(row.outcome, 0) + 1
        return {"decisions": len(rows), "by_outcome": counts}
