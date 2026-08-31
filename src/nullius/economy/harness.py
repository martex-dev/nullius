"""Does intelligent allocation help? The measurement, not the assumption.

M9's acceptance criterion is that greedy-EIG measurably beats random on
cost-per-correct-claim over the bank, **or is shown not to**. This module is
what decides which, and it is built so that the second answer is as reportable
as the first.

The comparison holds the science fixed. Every policy chooses from the same
:mod:`~nullius.economy.outcomes` — the same twenty items, each already carried
to a verdict at a measured cost — so the only thing that varies between arms
is which items the budget was spent on. Anything that differs is attributable
to selection, because nothing else differs.

**Two metrics, because EIG and correctness are not the same objective.**

``cost_per_correct_claim`` is the plan's metric and the one the institution
cares about. ``nats_per_dollar`` is what greedy-EIG is actually maximising.
They can disagree, and there is a reason to expect them to: an experiment with
high expected information gain is one whose outcome is genuinely uncertain,
and an outcome that is genuinely uncertain is one the institution is more
likely to get wrong. A policy that hunts for surprise will buy more surprise
and fewer correct claims. Reporting only the first metric would hide the
mechanism; reporting only the second would grade the policy on its own
homework.

**The informativeness sweep.** Under a mock provider every role emits the same
forecast for every item, so every item's EIG is identical and greedy-EIG
degenerates into ranking by cost and success probability alone. That is a fact
about the forecasts, not about the policy, and it would be dishonest to report
"EIG does not help" from a setting where EIG cannot vary. So the sweep
replaces the recorded forecasts with synthetic ones whose per-item information
content is dialled from nothing to oracle-grade, and reports where — if
anywhere — the EIG term begins to earn its place. The oracle-informed end is
an upper bound on what a perfect forecaster could buy, and is labelled as one;
no live system is claimed to reach it.

**Bootstrap.** Items are resampled with replacement and every policy is re-run
on the same resample, so the difference between two policies is paired. The
reported interval is over items — the population being generalised to is
"questions like these", which is the only population the bank can speak for.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import numpy as np

from nullius.db.enums import Role
from nullius.economy.eig import (
    Gaussian,
    RoleForecast,
    expected_information_gain,
    measurement_sd,
)
from nullius.economy.outcomes import ItemOutcome
from nullius.economy.policy import (
    AllocationPolicy,
    Candidate,
    CandidateKind,
    CheapestFirst,
    GreedyEig,
    RandomAllocation,
    Reserves,
    RoundRobin,
    ThompsonSampling,
)

__all__ = [
    "DEFAULT_RESAMPLES",
    "ComparisonReport",
    "ForecastSource",
    "PolicyResult",
    "RecordedForecasts",
    "SweepPoint",
    "SyntheticForecasts",
    "candidates_from_outcomes",
    "compare_policies",
    "default_policies",
    "sweep_informativeness",
]

DEFAULT_RESAMPLES = 2000
"""Bootstrap resamples. Enough for a stable 95% percentile interval on 20 items."""

FORECASTING_ROLES = (Role.THEORIST, Role.DESIGNER, Role.ANALYST)


# ---------------------------------------------------------------------------
# Where the forecasts come from
# ---------------------------------------------------------------------------


class ForecastSource(Protocol):
    """Supplies the predictive distributions the allocator scores an item by."""

    @property
    def name(self) -> str:
        """How this source is identified in a report."""

    def for_item(self, outcome: ItemOutcome) -> tuple[RoleForecast, ...]: ...


@dataclass(frozen=True, slots=True)
class RecordedForecasts:
    """The forecasts the roles actually made, as locked before execution.

    The honest default, and under a mock provider a degenerate one: identical
    across items, so every item scores the same EIG.
    """

    name: str = "recorded"

    def for_item(self, outcome: ItemOutcome) -> tuple[RoleForecast, ...]:
        return outcome.forecasts


@dataclass(frozen=True, slots=True)
class SyntheticForecasts:
    """Forecasts whose per-item information content is a dial.

    At ``informativeness = 0`` every role predicts the same thing about every
    item: no signal, which is what a mock provider produces and therefore the
    setting the recorded forecasts actually occupy. At ``1`` the roles' central
    predictions track each item's true effect and their spread tracks its
    genuine difficulty, so disagreement is large exactly where the answer is
    hard.

    **This is an upper bound, not a measurement of any real forecaster.** It
    uses the bank's ground truth, which no role may see. Its only purpose is to
    answer "how much could better forecasts be worth?", which is unanswerable
    from a setting where the forecasts carry no information at all.
    """

    informativeness: float
    baseline_mean: float = 0.0
    baseline_sd: float = 0.03
    name: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.informativeness <= 1.0:
            raise ValueError("informativeness is a fraction")
        # ASCII only: these strings are printed to a Windows console, which
        # defaults to cp1252 and raises on anything outside it.
        object.__setattr__(
            self, "name", self.name or f"synthetic(lambda={self.informativeness:.2f})"
        )

    def for_item(self, outcome: ItemOutcome) -> tuple[RoleForecast, ...]:
        """A deterministic function of the item's truth and the dial.

        No noise term at all. An earlier version jittered each mean by a
        billionth to look more like a real forecaster, and at ``lambda = 0``
        that jitter was the *only* thing separating one item's expected
        information gain from another's — so the allocator ranked on sampling
        noise and the flat case was not flat. Whatever a noise term buys in
        realism, it costs in being able to say what the zero rung means.
        """
        lam = self.informativeness

        # Difficulty: how close the truth sits to a verdict boundary, in its
        # own standard errors. Small margin means the honest answer is hard,
        # which is what a well-calibrated forecaster would be uncertain about.
        difficulty = 1.0 / (1.0 + max(outcome.boundary_margin, 0.0))

        forecasts: list[RoleForecast] = []
        for index, role in enumerate(FORECASTING_ROLES):
            centre = (1 - lam) * self.baseline_mean + lam * outcome.true_effect
            # Roles pull apart in proportion to difficulty: agreement on easy
            # items, disagreement on hard ones.
            offset = lam * difficulty * outcome.true_effect * (index - 1) * 0.5
            spread = (1 - lam) * self.baseline_sd + lam * max(
                self.baseline_sd * difficulty * 2.0, 1e-4
            )
            forecasts.append(
                RoleForecast(
                    role=role,
                    predictive_mean=float(centre + offset),
                    predictive_sd=float(max(spread, 1e-4)),
                    p_effect_exceeds_mde=float(np.clip(0.5 + lam * (centre * 10), 0.0, 1.0)),
                    p_execution_success=0.95,
                )
            )
        return tuple(forecasts)


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


def candidates_from_outcomes(
    outcomes: Sequence[ItemOutcome],
    source: ForecastSource,
    *,
    prior_sd: float = 0.02,
    seed_sd: float = 0.01,
    mean_brier: Mapping[Role, float] | None = None,
    seed: int = 0,
    suffix: str = "",
) -> tuple[list[Candidate], dict[str, float]]:
    """Score every measured outcome as a fundable candidate.

    Returns the candidates and their EIG by label, so the sweep can report how
    much the information term actually varied without recomputing it.

    ``expected_cost_usd`` is the outcome's *realised* cost. That is a stronger
    assumption than the allocator gets in practice — it amounts to a perfect
    cost model — and it is made deliberately: a noisy cost model would add
    variance that is shared by every arm and obscures the comparison the
    harness exists to make. :class:`~nullius.economy.cost_model.CostModel` is
    what a live programme uses, and it is measured separately.
    """
    import uuid as _uuid

    candidates: list[Candidate] = []
    eigs: dict[str, float] = {}

    # One seed for every item, not one per item. The Monte-Carlo draw is a
    # property of the estimator, not of the question being estimated: giving
    # each item its own stream made two items with identical forecasts score
    # differently, so sampling noise became ranking signal and a policy could
    # appear to prefer one item over an indistinguishable one. Sharing the
    # stream is also the standard variance reduction for a comparison — the
    # arms see the same draws, so their difference carries none of it.
    for outcome in outcomes:
        forecasts = source.for_item(outcome)
        label = f"{outcome.item_id}{suffix}"
        if not forecasts:
            raise ValueError(f"{outcome.item_id}: no forecasts, so nothing to score it by")

        report = expected_information_gain(
            forecasts,
            prior=Gaussian(0.0, prior_sd),
            noise_sd=measurement_sd(seed_sd, outcome.n_seeds),
            mean_brier=mean_brier,
            seed=seed,
        )
        eigs[label] = report.nats
        candidates.append(
            Candidate(
                subject_id=_uuid.uuid5(_uuid.NAMESPACE_OID, label),
                label=label,
                kind=CandidateKind.EXPLORATION,
                eig=report.nats,
                p_success=min(f.p_execution_success for f in forecasts),
                expected_cost_usd=max(outcome.usd, Decimal("0.000001")),
                group=label,
            )
        )
    return candidates, eigs


# ---------------------------------------------------------------------------
# Scoring one policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PolicyResult:
    """How one policy did on one set of items."""

    policy_version: str
    funded: int
    considered: int
    correct: int
    usd: Decimal
    nats: float

    @property
    def cost_per_correct_claim(self) -> float:
        """USD per correct claim. Infinite when nothing correct was bought.

        Infinity rather than an omission: a policy that spent the budget and
        got nothing right has a well-defined and very bad efficiency, and
        dropping it from the average would flatter exactly the failure the
        metric exists to catch.
        """
        return float(self.usd) / self.correct if self.correct else math.inf

    @property
    def correct_per_dollar(self) -> float:
        """The reciprocal, which is finite everywhere.

        Used for the bootstrap: a distribution containing infinities has no
        percentiles, so the interval is computed on this and reported on both.
        """
        return self.correct / float(self.usd) if self.usd > 0 else 0.0

    @property
    def nats_per_dollar(self) -> float:
        """What greedy-EIG is actually maximising."""
        return self.nats / float(self.usd) if self.usd > 0 else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "funded": self.funded,
            "considered": self.considered,
            "correct": self.correct,
            "usd": str(self.usd),
            "nats": round(self.nats, 6),
            "cost_per_correct_claim": self.cost_per_correct_claim,
            "correct_per_dollar": self.correct_per_dollar,
            "nats_per_dollar": self.nats_per_dollar,
        }

    def __str__(self) -> str:
        cost = (
            "no correct claim"
            if math.isinf(self.cost_per_correct_claim)
            else f"${self.cost_per_correct_claim:.4f}/correct"
        )
        return (
            f"{self.policy_version}: {self.correct}/{self.funded} correct of "
            f"{self.considered} offered, ${self.usd:.4f} -> {cost}, "
            f"{self.nats_per_dollar:.2f} nats/$"
        )


def score_policy(
    policy: AllocationPolicy,
    candidates: Sequence[Candidate],
    outcomes_by_label: Mapping[str, ItemOutcome],
    *,
    budget_usd: Decimal,
    reserves: Reserves,
) -> PolicyResult:
    """Allocate, then look up what each funded item actually turned out to be."""
    allocation = policy.allocate(candidates, budget_usd=budget_usd, reserves=reserves)
    funded = allocation.funded
    return PolicyResult(
        policy_version=policy.version,
        funded=len(funded),
        considered=len(candidates),
        correct=sum(1 for c in funded if outcomes_by_label[c.label].correct),
        usd=sum((outcomes_by_label[c.label].usd for c in funded), Decimal(0)),
        nats=sum(c.eig for c in funded),
    )


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PairedDifference:
    """One policy against the baseline, bootstrapped over items."""

    policy_version: str
    baseline_version: str
    metric: str
    observed: float
    ci_low: float
    ci_high: float
    resamples: int

    @property
    def separates(self) -> bool:
        """Whether the interval excludes zero — the acceptance test's question."""
        return self.ci_low > 0 or self.ci_high < 0

    @property
    def verdict(self) -> str:
        if not self.separates:
            return "no measurable difference"
        return "better than baseline" if self.observed > 0 else "worse than baseline"

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "baseline_version": self.baseline_version,
            "metric": self.metric,
            "observed": round(self.observed, 6),
            "ci_low": round(self.ci_low, 6),
            "ci_high": round(self.ci_high, 6),
            "resamples": self.resamples,
            "separates": self.separates,
            "verdict": self.verdict,
        }

    def __str__(self) -> str:
        return (
            f"{self.policy_version} minus {self.baseline_version} on {self.metric}: "
            f"{self.observed:+.4f} [{self.ci_low:+.4f}, {self.ci_high:+.4f}] "
            f"-> {self.verdict}"
        )


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    """Every policy on the same items, with paired intervals against random."""

    forecast_source: str
    budget_usd: Decimal
    n_items: int
    results: tuple[PolicyResult, ...]
    differences: tuple[PairedDifference, ...]
    eig_spread: float
    """Standard deviation of EIG across items.

    Zero means the information term was constant, so any policy that consumes
    it ranked on something else entirely. Reported first because it decides
    whether the rest of the table is about EIG at all.
    """

    resamples: int

    def result_for(self, version: str) -> PolicyResult:
        return next(r for r in self.results if r.policy_version == version)

    def difference_for(self, version: str, metric: str) -> PairedDifference:
        return next(
            d for d in self.differences if d.policy_version == version and d.metric == metric
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "forecast_source": self.forecast_source,
            "budget_usd": str(self.budget_usd),
            "n_items": self.n_items,
            "eig_spread": round(self.eig_spread, 9),
            "resamples": self.resamples,
            "results": [r.as_dict() for r in self.results],
            "differences": [d.as_dict() for d in self.differences],
        }


def default_policies(seed: int = 0) -> list[AllocationPolicy]:
    """The four the plan requires, plus the cost-only control."""
    return [
        RandomAllocation(seed=seed),
        RoundRobin(),
        CheapestFirst(),
        GreedyEig(),
        ThompsonSampling(seed=seed),
    ]


def compare_policies(
    outcomes: Sequence[ItemOutcome],
    *,
    budget_usd: Decimal,
    source: ForecastSource | None = None,
    policies: Sequence[AllocationPolicy] | None = None,
    baseline_version: str = RandomAllocation.version,
    reserves: Reserves | None = None,
    prior_sd: float = 0.02,
    seed_sd: float = 0.01,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = 0,
) -> ComparisonReport:
    """Run every policy over the same items and bootstrap the differences.

    ``reserves`` defaults to none for the comparison, and that is a deliberate
    departure from what a live programme uses. Every candidate here is
    exploration, so a replication reserve would fence off a third of the budget
    that no policy could spend, shrinking every arm equally and testing
    nothing. The reserve mechanism is exercised on its own.
    """
    if not outcomes:
        raise ValueError("nothing to allocate over")

    forecasts: ForecastSource = source if source is not None else RecordedForecasts()
    policies = list(policies or default_policies(seed))
    reserves = reserves or Reserves(replication=0.0, null_confirmation=0.0)

    candidates, eigs = candidates_from_outcomes(
        outcomes, forecasts, prior_sd=prior_sd, seed_sd=seed_sd, seed=seed
    )
    by_label = {f"{o.item_id}": o for o in outcomes}

    results = tuple(
        score_policy(p, candidates, by_label, budget_usd=budget_usd, reserves=reserves)
        for p in policies
    )

    differences = _bootstrap(
        outcomes,
        forecasts,
        policies,
        baseline_version=baseline_version,
        budget_usd=budget_usd,
        reserves=reserves,
        prior_sd=prior_sd,
        seed_sd=seed_sd,
        resamples=resamples,
        seed=seed,
    )

    spread = float(np.std(list(eigs.values()), ddof=1)) if len(eigs) > 1 else 0.0
    return ComparisonReport(
        forecast_source=forecasts.name,
        budget_usd=budget_usd,
        n_items=len(outcomes),
        results=results,
        differences=differences,
        eig_spread=spread,
        resamples=resamples,
    )


_METRICS = ("correct_per_dollar", "nats_per_dollar")


def _bootstrap(
    outcomes: Sequence[ItemOutcome],
    source: ForecastSource,
    policies: Sequence[AllocationPolicy],
    *,
    baseline_version: str,
    budget_usd: Decimal,
    reserves: Reserves,
    prior_sd: float,
    seed_sd: float,
    resamples: int,
    seed: int,
) -> tuple[PairedDifference, ...]:
    """Percentile intervals for each policy minus the baseline, paired by resample.

    Each item is scored exactly once. An item's expected information gain and
    cost do not depend on which resample it landed in, so recomputing them per
    draw would be two thousand times the arithmetic for identical numbers — and
    would quietly introduce per-draw seed variation into a comparison whose
    whole design is that the arms see the same items.
    """
    rng = np.random.default_rng(seed)
    n = len(outcomes)
    base, _ = candidates_from_outcomes(
        outcomes, source, prior_sd=prior_sd, seed_sd=seed_sd, seed=seed
    )
    draws: dict[tuple[str, str], list[float]] = {
        (p.version, metric): [] for p in policies for metric in _METRICS
    }

    for _draw in range(resamples):
        indices = rng.integers(0, n, size=n)
        # Labels are suffixed by position so a resample that draws the same
        # item twice offers it twice, rather than silently collapsing into one
        # candidate and shrinking the choice set.
        candidates = [
            Candidate(
                subject_id=base[i].subject_id,
                label=f"{base[i].label}#{position}",
                kind=base[i].kind,
                eig=base[i].eig,
                p_success=base[i].p_success,
                expected_cost_usd=base[i].expected_cost_usd,
                strategic_weight=base[i].strategic_weight,
                group=f"{base[i].family}#{position}",
            )
            for position, i in enumerate(indices)
        ]
        by_label = {c.label: outcomes[i] for c, i in zip(candidates, indices, strict=True)}

        for policy in policies:
            scored = score_policy(
                policy, candidates, by_label, budget_usd=budget_usd, reserves=reserves
            )
            for metric in _METRICS:
                draws[(policy.version, metric)].append(getattr(scored, metric))

    differences: list[PairedDifference] = []
    for policy in policies:
        if policy.version == baseline_version:
            continue
        for metric in _METRICS:
            delta = np.asarray(draws[(policy.version, metric)]) - np.asarray(
                draws[(baseline_version, metric)]
            )
            differences.append(
                PairedDifference(
                    policy_version=policy.version,
                    baseline_version=baseline_version,
                    metric=metric,
                    observed=float(delta.mean()),
                    ci_low=float(np.percentile(delta, 2.5)),
                    ci_high=float(np.percentile(delta, 97.5)),
                    resamples=resamples,
                )
            )
    return tuple(differences)


# ---------------------------------------------------------------------------
# The informativeness sweep
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SweepPoint:
    """One rung of the forecast-quality ladder.

    Two differences, because they answer different questions. ``difference`` is
    greedy-EIG against the random baseline, which is the milestone's stated
    comparison. ``against_cost_control`` is greedy-EIG against
    :class:`~nullius.economy.policy.CheapestFirst`, which shares every term of
    the score except the information one — so it is the only number here that
    isolates what EIG itself bought.
    """

    informativeness: float
    eig_spread: float
    difference: PairedDifference
    against_cost_control: PairedDifference

    @property
    def information_helped(self) -> bool:
        """Whether the EIG term moved anything the cost denominator did not."""
        return self.against_cost_control.separates

    def as_dict(self) -> dict[str, Any]:
        return {
            "informativeness": self.informativeness,
            "eig_spread": round(self.eig_spread, 9),
            "difference": self.difference.as_dict(),
            "against_cost_control": self.against_cost_control.as_dict(),
            "information_helped": self.information_helped,
        }

    def __str__(self) -> str:
        return (
            f"lambda={self.informativeness:.2f}  EIG spread {self.eig_spread:.4f}  "
            f"{self.difference}"
        )


@dataclass(frozen=True, slots=True)
class SweepReport:
    """How much greedy-EIG gains as the forecasts it consumes improve."""

    points: tuple[SweepPoint, ...] = ()
    metric: str = "correct_per_dollar"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def first_separating(self) -> SweepPoint | None:
        """The cheapest forecast quality at which the gain is measurable, if any."""
        for point in self.points:
            if point.difference.separates and point.difference.observed > 0:
                return point
        return None

    @property
    def first_beating_the_cost_control(self) -> SweepPoint | None:
        """The cheapest forecast quality at which *information* is what won.

        The stricter and more interesting reading of the milestone. Separating
        from random is achievable by dividing by cost; separating from the cost
        control is not.
        """
        for point in self.points:
            if point.information_helped and point.against_cost_control.observed > 0:
                return point
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "points": [p.as_dict() for p in self.points],
            "first_separating": (
                self.first_separating.informativeness if self.first_separating else None
            ),
            "first_beating_the_cost_control": (
                self.first_beating_the_cost_control.informativeness
                if self.first_beating_the_cost_control
                else None
            ),
            **self.extra,
        }


def sweep_informativeness(
    outcomes: Sequence[ItemOutcome],
    *,
    budget_usd: Decimal,
    levels: Sequence[float] = (0.0, 0.25, 0.5, 0.75, 1.0),
    metric: str = "correct_per_dollar",
    resamples: int = 400,
    seed: int = 0,
    prior_sd: float = 0.02,
    seed_sd: float = 0.01,
) -> SweepReport:
    """Dial forecast quality from nothing to oracle-grade and watch the gap.

    Fewer resamples than :func:`compare_policies` by default, because this runs
    the whole comparison once per rung and the question here is where the sign
    changes rather than the exact width of any one interval.
    """
    points: list[SweepPoint] = []
    for level in levels:
        report = compare_policies(
            outcomes,
            budget_usd=budget_usd,
            source=SyntheticForecasts(informativeness=level),
            policies=[RandomAllocation(seed=seed), CheapestFirst(), GreedyEig()],
            resamples=resamples,
            seed=seed,
            prior_sd=prior_sd,
            seed_sd=seed_sd,
        )
        # Re-run with the cost control as the baseline, so the paired interval
        # is greedy against it directly rather than a difference of two
        # differences — which would have a wider interval for no reason.
        against_control = compare_policies(
            outcomes,
            budget_usd=budget_usd,
            source=SyntheticForecasts(informativeness=level),
            policies=[CheapestFirst(), GreedyEig()],
            baseline_version=CheapestFirst.version,
            resamples=resamples,
            seed=seed,
            prior_sd=prior_sd,
            seed_sd=seed_sd,
        )
        points.append(
            SweepPoint(
                informativeness=level,
                eig_spread=report.eig_spread,
                difference=report.difference_for(GreedyEig.version, metric),
                against_cost_control=against_control.difference_for(GreedyEig.version, metric),
            )
        )
    return SweepReport(points=tuple(points), metric=metric)
