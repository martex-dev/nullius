"""The Holdout Custodian.

The only actor permitted to produce a number about the evaluation split, and
the only one that can, because it is the only one that has the split.

For synthetic data that guarantee is not a file permission. As M3 established,
a filesystem holdout would be theatre — the experiment holds the generator and
the seed, so it could redraw whatever the "held out" file contained. Instead
the Custodian draws its *own* sample from a seed derived here and never placed
in a plan, in a numeric range disjoint from both experiment seeds and the
bank's oracle seeds. The evaluation data does not exist until the Custodian
makes it, so there is nothing to leak.

Nothing is deserialised either. The Custodian re-fits the model from the plan,
which is deterministic, rather than loading a pickled estimator — avoiding an
arbitrary-code surface and a stale-artifact problem in one decision.

Every look costs a query, and a *look* is one evaluation of one
registration across all of its seeds — not one seed. The failure being
prevented (`docs/01-critique.md` F2) is selection on test-set noise, and
selection happens at the level of a design decision: seeing all five seeds of
one design is one decision's worth of information. Charging per seed would
have made a well-replicated design look more suspicious than a thin one,
which is backwards.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from nullius.build import ops
from nullius.db.enums import ComputedBy, Role, Split
from nullius.db.tables import HoldoutQuery, Registration, Run
from nullius.errors import NulliusError
from nullius.repository import Repository
from nullius.util.canonical import sha256_of

__all__ = ["CUSTODY_SEED_FLOOR", "BudgetExhausted", "CustodyResult", "HoldoutCustodian"]

CUSTODY_SEED_FLOOR = 2_000_000
"""Custody seeds start here.

Disjoint from experiment seeds (strictly below 1,000,000) and from the bank's
oracle seeds (which begin at 1,000,000). Three actors, three ranges, no
overlap by construction.
"""

CUSTODY_DOMAIN = "nullius/custody/v1"
"""Domain separator, so a custody seed cannot collide with any other hash use."""


class BudgetExhausted(NulliusError):
    """The registration has used every holdout query it was granted.

    Not a bug. A hypothesis that needs a fourth look at the test split has
    told us something about itself.
    """


@dataclass(frozen=True, slots=True)
class CustodyResult:
    """Metrics from the evaluation split, and what they cost.

    One result covers every seed of one registration, because that is what one
    query buys.
    """

    registration_id: uuid.UUID
    per_seed: dict[int, dict[str, dict[str, float]]]
    """``{seed: {arm: {metric: value}}}`` on the evaluation samples."""
    queries_consumed: int
    remaining_budget: int

    def arm_values(self, arm: str, metric: str) -> list[float]:
        """One value per seed, in seed order — what the paired analysis needs."""
        return [
            self.per_seed[seed][arm][metric]
            for seed in sorted(self.per_seed)
            if arm in self.per_seed[seed]
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "registration_id": str(self.registration_id),
            "seeds": sorted(self.per_seed),
            "per_seed": {str(k): v for k, v in self.per_seed.items()},
            "queries_consumed": self.queries_consumed,
            "remaining_budget": self.remaining_budget,
        }


def custody_seed(registration_id: uuid.UUID, seed: int) -> int:
    """Derive the evaluation seed for one run.

    Deterministic — so a claim can be re-evaluated on exactly the sample it
    was judged against — but derived from the registration identity rather
    than from anything that appears in a plan.
    """
    digest = sha256_of({"domain": CUSTODY_DOMAIN, "registration": registration_id, "seed": seed})
    return CUSTODY_SEED_FLOOR + int(digest[:12], 16) % (2**31 - CUSTODY_SEED_FLOOR)


class HoldoutCustodian:
    """Holds the evaluation split and rations access to it."""

    __slots__ = ("_repo",)

    def __init__(self, repo: Repository) -> None:
        if repo.role is not Role.CUSTODIAN:
            raise NulliusError(
                f"the Custodian must act as {Role.CUSTODIAN.value!r}, not "
                f"{repo.role.value!r}. Constructing it as another role would let that "
                "role's identity onto holdout metrics."
            )
        self._repo = repo

    # ----------------------------------------------------------- accounting

    def queries_consumed(self, registration_id: uuid.UUID) -> int:
        total = self._repo.session.scalar(
            sa.select(sa.func.count())
            .select_from(HoldoutQuery)
            .where(HoldoutQuery.registration_id == registration_id, HoldoutQuery.granted.is_(True))
        )
        return int(total or 0)

    def remaining_budget(self, registration_id: uuid.UUID) -> int:
        registration = self._registration(registration_id)
        return registration.holdout_query_budget - self.queries_consumed(registration_id)

    def _registration(self, registration_id: uuid.UUID) -> Registration:
        registration = self._repo.session.get(Registration, registration_id)
        if registration is None:
            raise NulliusError(f"no registration {registration_id}")
        if not registration.locked:
            raise NulliusError(
                f"registration {registration_id} is not locked; the evaluation split is "
                "not opened for a design that can still change"
            )
        return registration

    # ------------------------------------------------------------ evaluate

    def evaluate(
        self,
        *,
        registration_id: uuid.UUID,
        runs: list[tuple[uuid.UUID, dict[str, Any]]],
        program_id: uuid.UUID | None = None,
    ) -> CustodyResult:
        """Evaluate every seed of one registration, and charge a single query.

        ``runs`` pairs each recorded run with the plan it executed. All of them
        are evaluated together because they are one design's worth of evidence,
        and the budget counts design decisions rather than seeds.

        Refuses once the registration's budget is spent, recording the denied
        attempt so it stays visible even though it produced nothing.
        """
        registration = self._registration(registration_id)
        if not runs:
            raise NulliusError("nothing to evaluate: no runs were supplied")
        for run_id, plan in runs:
            self._check_plan_belongs_to_run(registration_id, run_id, plan)

        remaining = registration.holdout_query_budget - self.queries_consumed(registration_id)
        if remaining <= 0:
            self._record_query(
                registration_id,
                granted=False,
                remaining=0,
                artifact_hash=sha256_of([plan for _, plan in runs]),
                program_id=program_id,
            )
            raise BudgetExhausted(
                f"registration {registration_id} has used all "
                f"{registration.holdout_query_budget} of its holdout queries. Further "
                "looks at the evaluation split would fit it by selection; register a "
                "new confirmatory experiment instead."
            )

        per_seed: dict[int, dict[str, dict[str, float]]] = {}
        for _, plan in runs:
            seed = int(plan["seed"])
            per_seed[seed] = self._measure(plan, custody_seed(registration_id, seed))

        # Seed keys are stringified for hashing: canonical JSON refuses non-string
        # mapping keys, because their ordering is not stable across types.
        artifact_hash = sha256_of(
            {
                "registration": registration_id,
                "per_seed": {str(seed): metrics for seed, metrics in per_seed.items()},
            }
        )
        self._record_query(
            registration_id,
            granted=True,
            remaining=remaining - 1,
            artifact_hash=artifact_hash,
            program_id=program_id,
        )

        for run_id, plan in runs:
            seed = int(plan["seed"])
            for arm_name, arm_metrics in per_seed[seed].items():
                for metric_name, value in arm_metrics.items():
                    self._repo.record_result(
                        run_id=run_id,
                        split=Split.HOLDOUT,
                        metric=f"{arm_name}.{metric_name}",
                        value=float(value),
                        artifact_hash=artifact_hash,
                        computed_by=ComputedBy.CUSTODIAN,
                        program_id=program_id,
                    )

        return CustodyResult(
            registration_id=registration_id,
            per_seed=per_seed,
            queries_consumed=self.queries_consumed(registration_id),
            remaining_budget=remaining - 1,
        )

    def _check_plan_belongs_to_run(
        self, registration_id: uuid.UUID, run_id: uuid.UUID, plan: dict[str, Any]
    ) -> None:
        """Refuse to attach one experiment's evaluation to another's run.

        The evaluation sample is derived from the plan's seed, so pairing a
        plan with a run that executed a different seed would record metrics
        describing data that run never saw. The database would eventually
        notice the duplicate metric name; noticing here says why.
        """
        run = self._repo.session.get(Run, run_id)
        if run is None:
            raise NulliusError(f"no run {run_id}")
        if run.registration_id != registration_id:
            raise NulliusError(
                f"run {run_id} belongs to registration {run.registration_id}, not "
                f"{registration_id}; an evaluation must attach to its own run"
            )
        if run.seed != int(plan["seed"]):
            raise NulliusError(
                f"the plan is for seed {plan['seed']} but run {run_id} executed seed "
                f"{run.seed}. The evaluation sample is derived from the seed, so these "
                "metrics would describe data this run never saw."
            )

    # ------------------------------------------------------------- private

    def _measure(self, plan: dict[str, Any], evaluation_seed: int) -> dict[str, dict[str, float]]:
        """Re-fit each arm from the plan and score it on a fresh sample.

        Training data comes from the plan's own seed — the model must be the
        one the experiment built. Evaluation data comes from the custody seed,
        which the experiment has never seen.
        """
        import numpy as np

        training = ops.generate(plan["dataset"]["generator"], **plan["dataset"]["params"])
        evaluation = ops.generate(
            plan["dataset"]["generator"],
            **{**plan["dataset"]["params"], "seed": evaluation_seed},
        )

        results: dict[str, dict[str, float]] = {}
        for arm in plan["arms"]:
            mask = np.ones(training.x_train.shape[1], dtype=bool)
            for step in arm["transforms"]:
                selection = ops.transform(
                    step["op"],
                    training.x_train[:, mask],
                    training.x_deploy[:, mask],
                    **step["params"],
                )
                active = np.flatnonzero(mask)
                mask[active[~selection.keep]] = False

            model = ops.estimator(arm["estimator"]["op"], **arm["estimator"]["params"])
            model.fit(training.x_train[:, mask], training.y_train)

            predictions = model.predict(evaluation.x_deploy[:, mask])
            scores = None
            if hasattr(model, "predict_proba"):
                proba = model.predict_proba(evaluation.x_deploy[:, mask])
                if proba.shape[1] == 2:
                    scores = proba[:, 1]

            results[arm["name"]] = {
                name: ops.metric(name, evaluation.y_deploy, predictions, scores)
                for name in plan["metrics"]
            }
        return results

    def _record_query(
        self,
        registration_id: uuid.UUID,
        *,
        granted: bool,
        remaining: int,
        artifact_hash: str,
        program_id: uuid.UUID | None = None,
    ) -> None:
        # Through the repository, so the look is an event. Writing the row
        # directly would leave the ledger unable to reconstruct how many times
        # the evaluation split was consulted.
        self._repo.record_holdout_query(
            registration_id=registration_id,
            artifact_hash=artifact_hash,
            granted=granted,
            remaining_budget=remaining,
            program_id=program_id,
        )
