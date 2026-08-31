"""What an experiment is expected to cost, learned from what they have cost.

The allocator divides by this number, so getting it wrong biases every
decision. Two honest sources exist and both are used:

**History**, when there is any. A least-squares fit of cost against the two
design parameters that actually drive it — the number of seeds and the number
of arms, whose product is the number of times the estimator is trained. Not a
sophisticated model; a linear one, on two features chosen because the
mechanism says they are the drivers, rather than a large model fitted to
whatever columns were lying around.

**The measured prompt estimate**, when there is not. :mod:`nullius.costing`
builds the real requests each role will send and prices them. That is a
genuine prior, not a guess, and it is what the model returns until enough runs
exist for a fit to mean anything.

The distinction is exposed rather than smoothed over. :attr:`CostModel.fitted`
says which source a prediction came from, and it is recorded in the decision's
inputs — because "the allocator preferred the cheap experiments" means
something different when the cheapness was measured than when it was assumed.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import numpy as np
import sqlalchemy as sa

from nullius.db.tables import CostEntry, Hypothesis, Registration, Run
from nullius.repository import Repository

__all__ = ["MINIMUM_OBSERVATIONS", "CostModel", "CostObservation", "observations_for_program"]

MINIMUM_OBSERVATIONS = 5
"""Fewer than this and a three-parameter fit is interpolating noise.

Three coefficients from four points is not an estimate, it is a solved system
with a residual of zero and no predictive content whatsoever.
"""

_FLOOR = Decimal("0.000001")
"""A prediction of zero or less would let a candidate divide by nothing."""


@dataclass(frozen=True, slots=True)
class CostObservation:
    """One completed registration and what it actually cost."""

    registration_id: uuid.UUID
    n_seeds: int
    n_arms: int
    usd: Decimal

    @property
    def trainings(self) -> int:
        """Seeds times arms: how many times the estimator was fitted."""
        return self.n_seeds * self.n_arms


@dataclass(frozen=True, slots=True)
class CostModel:
    """Predicts the cost of a design that has not been run.

    Immutable and fitted by classmethod rather than mutated in place, so a
    decision's recorded inputs describe a model that still exists in the form
    that produced them.
    """

    fallback_usd: Decimal
    intercept: float = 0.0
    per_seed: float = 0.0
    per_arm: float = 0.0
    n_observations: int = 0
    residual_sd: float = 0.0

    @property
    def fitted(self) -> bool:
        """Whether this prediction comes from history or from the prior."""
        return self.n_observations >= MINIMUM_OBSERVATIONS

    @classmethod
    def fit(cls, observations: Sequence[CostObservation], *, fallback_usd: Decimal) -> CostModel:
        """Least squares on ``[1, n_seeds, n_arms]``, or the prior if there is too little."""
        if len(observations) < MINIMUM_OBSERVATIONS:
            return cls(fallback_usd=fallback_usd, n_observations=len(observations))

        design = np.asarray(
            [[1.0, float(o.n_seeds), float(o.n_arms)] for o in observations], dtype=np.float64
        )
        target = np.asarray([float(o.usd) for o in observations], dtype=np.float64)
        coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
        residuals = target - design @ coefficients

        return cls(
            fallback_usd=fallback_usd,
            intercept=float(coefficients[0]),
            per_seed=float(coefficients[1]),
            per_arm=float(coefficients[2]),
            n_observations=len(observations),
            residual_sd=float(residuals.std(ddof=1)) if len(observations) > 3 else 0.0,
        )

    def predict(self, *, n_seeds: int, n_arms: int) -> Decimal:
        """Expected cost of one registration of this shape.

        A fitted model can extrapolate to a negative cost for a design smaller
        than anything it has seen. That is floored rather than corrected: the
        floor is visible in the prediction, whereas a silently clipped
        coefficient would not be.
        """
        if n_seeds < 1 or n_arms < 1:
            raise ValueError("a design has at least one seed and one arm")
        if not self.fitted:
            return max(self.fallback_usd, _FLOOR)
        predicted = self.intercept + self.per_seed * n_seeds + self.per_arm * n_arms
        return max(Decimal(str(round(predicted, 8))), _FLOOR)

    def as_dict(self) -> dict[str, Any]:
        return {
            "fitted": self.fitted,
            "n_observations": self.n_observations,
            "fallback_usd": str(self.fallback_usd),
            "intercept": round(self.intercept, 8),
            "per_seed": round(self.per_seed, 8),
            "per_arm": round(self.per_arm, 8),
            "residual_sd": round(self.residual_sd, 8),
        }

    def __str__(self) -> str:
        if not self.fitted:
            return (
                f"cost model: unfitted ({self.n_observations} of {MINIMUM_OBSERVATIONS} "
                f"observations), falling back to ${self.fallback_usd:.6f}"
            )
        return (
            f"cost model: ${self.intercept:.6f} + ${self.per_seed:.6f}/seed + "
            f"${self.per_arm:.6f}/arm from {self.n_observations} runs "
            f"(residual sd ${self.residual_sd:.6f})"
        )


def observations_for_program(repo: Repository, program_id: uuid.UUID) -> list[CostObservation]:
    """Every registration in a programme, with what its runs cost.

    Costs are attributed through ``cost → run → registration``, which is the
    only join that is true by construction: a cost row carries the run that
    incurred it, and a run carries the registration it was declared under.
    Attributing through tasks instead would miss compute entirely, and compute
    is most of what a mock-driven programme spends.

    A run has no programme of its own; it belongs to the programme its
    hypothesis does. Reaching the programme therefore costs two more joins, and
    they are written out rather than shortened by a denormalised column, so
    there stays exactly one place where a run's programme is decided.
    """
    rows = repo.session.execute(
        sa.select(
            Registration.registration_id,
            Registration.n_seeds,
            Registration.spec,
        )
        .join(Run, Run.registration_id == Registration.registration_id)
        .join(Hypothesis, Hypothesis.hypothesis_id == Registration.hypothesis_id)
        .where(Hypothesis.program_id == program_id)
        .group_by(Registration.registration_id)
    ).all()

    observations: list[CostObservation] = []
    for registration_id, n_seeds, spec in rows:
        # Summed in Python for the reason BudgetLedger.status gives: SQLite's
        # sum() over the Money column coerces through binary floating point.
        amounts = repo.session.scalars(
            sa.select(CostEntry.usd)
            .join(Run, Run.run_id == CostEntry.run_id)
            .where(Run.registration_id == registration_id)
        )
        observations.append(
            CostObservation(
                registration_id=registration_id,
                n_seeds=int(n_seeds),
                n_arms=max(1, len((spec or {}).get("arms", ()))),
                usd=sum(amounts, Decimal(0)),
            )
        )
    return observations
