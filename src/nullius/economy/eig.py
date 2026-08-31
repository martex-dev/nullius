"""Expected information gain, derived from what the roles actually predicted.

The allocator needs a number for "how much would running this teach us?", and
there are two ways to get one. The bad way is to ask a model. The way taken
here is to compute it from the Forecast Ledger, which already exists because
every role must state a predictive distribution before execution and is scored
on it afterwards. A role cannot inflate its experiment's priority without
committing to a prediction it will be graded against.

The model is the textbook one, with one deliberate substitution.

Prior over the effect ``θ``: ``N(μ₀, τ²)``, where ``τ`` is the hypothesis's own
declared ``prior_sd``. Observation: ``y | θ ~ N(θ, σ²)``, where ``σ`` is how
precisely this design can measure the effect — a function of seed variance and
seed count, not of anyone's opinion. Both are Gaussian, so the posterior after
seeing ``y`` is Gaussian and the information gained is the KL divergence from
prior to posterior, in closed form.

The substitution: the expectation over ``y`` is taken under the **elicited
predictive mixture** — every role's stated distribution, weighted — rather
than under the prior predictive. Those coincide only when the roles collectively
agree with the prior. When they do not, the mixture is wide, observations far
from ``μ₀`` are likely, and the expected gain rises. This is what
``docs/02-architecture.md`` §7 means by *wide disagreement → high EIG*: an
experiment the institution's own members cannot agree about is worth running,
and the arithmetic says so without anyone having to assert it.

**What this number is not.** EIG measures expected surprise, not expected
correctness. An experiment on a question nobody can call is highly informative
and also the one most likely to yield an answer the institution gets wrong.
Ranking by EIG and scoring by cost-per-correct-claim are therefore different
objectives, and :mod:`nullius.economy.harness` measures whether they pull in
the same direction rather than assuming it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from nullius.db.enums import Role

__all__ = [
    "CHANCE_BRIER",
    "DEFAULT_DRAWS",
    "EigReport",
    "Gaussian",
    "RoleForecast",
    "calibration_weights",
    "disagreement",
    "expected_information_gain",
    "measurement_sd",
    "posterior",
]

DEFAULT_DRAWS = 4096
"""Monte-Carlo draws for the expectation over the predictive mixture.

Large enough that the estimate is stable to about three decimal places in
nats, small enough that scoring twenty candidates is instant. The draws are
seeded, so an allocation decision is reproducible from its recorded inputs.
"""

CHANCE_BRIER = 0.25
"""The Brier score of a forecaster who always says one half.

The reference point for skill: a role that cannot beat it contributes nothing,
and :func:`calibration_weights` gives it a weight of zero.
"""


@dataclass(frozen=True, slots=True)
class Gaussian:
    """A normal distribution. Mean and spread, nothing else."""

    mean: float
    sd: float

    def __post_init__(self) -> None:
        if self.sd <= 0:
            raise ValueError("a distribution needs positive spread")

    @property
    def variance(self) -> float:
        return self.sd * self.sd


@dataclass(frozen=True, slots=True)
class RoleForecast:
    """One role's locked prediction, as the allocator consumes it.

    A projection of :class:`~nullius.db.tables.Forecast` rather than the row
    itself, so the allocator can be exercised on hypothetical forecasts —
    which is exactly what the informativeness sweep in
    :mod:`nullius.economy.harness` needs — without inventing database rows to
    hold predictions nobody made.
    """

    role: Role
    predictive_mean: float
    predictive_sd: float
    p_effect_exceeds_mde: float
    p_execution_success: float

    @property
    def distribution(self) -> Gaussian:
        return Gaussian(self.predictive_mean, self.predictive_sd)


def measurement_sd(seed_sd: float, n_seeds: int) -> float:
    """How precisely a design with ``n_seeds`` seeds can measure a paired effect.

    The paired difference of two arms measured on the same seeds has standard
    error ``seed_sd·√2/√n`` when the arms are uncorrelated across seeds. They
    are in fact positively correlated — that is why the design is paired at
    all — so this over-states the noise, which makes the resulting EIG
    conservative. Preferring to under-claim how much an experiment will teach
    is the right direction for a thing that decides what to fund.
    """
    if n_seeds < 1:
        raise ValueError("a design measures nothing with no seeds")
    if seed_sd <= 0:
        raise ValueError("a design whose metric never moves measures nothing")
    return float(seed_sd * math.sqrt(2.0 / n_seeds))


def posterior(prior: Gaussian, observation: float, noise_sd: float) -> Gaussian:
    """The conjugate Gaussian update after seeing one measurement."""
    if noise_sd <= 0:
        raise ValueError("a measurement needs positive noise")
    precision = 1.0 / prior.variance + 1.0 / (noise_sd * noise_sd)
    variance = 1.0 / precision
    mean = variance * (prior.mean / prior.variance + observation / (noise_sd * noise_sd))
    return Gaussian(mean, math.sqrt(variance))


def _kl(posterior_belief: Gaussian, prior: Gaussian) -> float:
    """KL(posterior ‖ prior) for two Gaussians, in nats."""
    return float(
        math.log(prior.sd / posterior_belief.sd)
        + (posterior_belief.variance + (posterior_belief.mean - prior.mean) ** 2)
        / (2.0 * prior.variance)
        - 0.5
    )


def disagreement(forecasts: Sequence[RoleForecast]) -> float:
    """Spread of the roles' central predictions, in the metric's own units.

    Zero when every role predicts the same thing — which is exactly what
    happens under a mock provider, and the reason the mock cannot demonstrate
    that EIG-guided allocation is worth anything.
    """
    if len(forecasts) < 2:
        return 0.0
    means = np.asarray([f.predictive_mean for f in forecasts], dtype=np.float64)
    return float(means.std(ddof=1))


def calibration_weights(
    forecasts: Sequence[RoleForecast],
    mean_brier: Mapping[Role, float] | None = None,
) -> np.ndarray:
    """How much each role's prediction counts, from its history of being right.

    The Brier skill score against a coin flip: ``1 - brier/0.25``, floored at
    zero. A role with no scoring history yet counts fully — the alternative is
    to silence every new role permanently, since a role that is never listened
    to never generates the record that would earn it a hearing.

    Falls back to uniform weights when no role has demonstrated any skill,
    rather than dividing by zero or, worse, quietly returning an empty
    allocation.
    """
    if not forecasts:
        raise ValueError("no forecasts to weight")
    history = dict(mean_brier or {})
    skill = np.asarray(
        [max(0.0, 1.0 - history.get(f.role, 0.0) / CHANCE_BRIER) for f in forecasts],
        dtype=np.float64,
    )
    total = skill.sum()
    if total <= 0:
        return np.full(len(forecasts), 1.0 / len(forecasts))
    return skill / total


@dataclass(frozen=True, slots=True)
class EigReport:
    """The expected information gain, with everything that produced it.

    Kept whole rather than reduced to a float because it is written into the
    ``inputs`` column of a :class:`~nullius.db.tables.Decision`, where the
    point is that a funding decision can be re-derived and argued with later.
    """

    nats: float
    prior_sd: float
    measurement_sd: float
    mixture_sd: float
    disagreement: float
    n_forecasts: int
    draws: int
    seed: int

    @property
    def bits(self) -> float:
        return self.nats / math.log(2.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "nats": round(self.nats, 6),
            "bits": round(self.bits, 6),
            "prior_sd": round(self.prior_sd, 6),
            "measurement_sd": round(self.measurement_sd, 6),
            "mixture_sd": round(self.mixture_sd, 6),
            "disagreement": round(self.disagreement, 6),
            "n_forecasts": self.n_forecasts,
            "draws": self.draws,
            "seed": self.seed,
        }

    def __str__(self) -> str:
        return (
            f"EIG {self.nats:.4f} nats ({self.bits:.4f} bits) from {self.n_forecasts} "
            f"forecast(s), disagreement {self.disagreement:.4f}"
        )


def expected_information_gain(
    forecasts: Sequence[RoleForecast],
    *,
    prior: Gaussian,
    noise_sd: float,
    mean_brier: Mapping[Role, float] | None = None,
    draws: int = DEFAULT_DRAWS,
    seed: int = 0,
) -> EigReport:
    """Expected KL from prior to posterior, over the elicited predictive mixture.

    Monte Carlo rather than closed form, deliberately. The closed form exists
    only when the expectation is taken under the prior predictive; taking it
    under the roles' mixture is the whole point, and a sampled estimate keeps
    the substitution visible instead of hiding it inside an algebraic identity
    that no longer holds.
    """
    if not forecasts:
        raise ValueError("expected information gain needs at least one forecast")
    if noise_sd <= 0:
        raise ValueError("a measurement needs positive noise")

    weights = calibration_weights(forecasts, mean_brier)
    means = np.asarray([f.predictive_mean for f in forecasts], dtype=np.float64)
    sds = np.asarray([f.predictive_sd for f in forecasts], dtype=np.float64)

    rng = np.random.default_rng(seed)
    component = rng.choice(len(forecasts), size=draws, p=weights)
    observations = rng.normal(means[component], sds[component])

    # The conjugate update and the divergence, vectorised over the draws. The
    # scalar :func:`posterior` and :func:`_kl` remain the definition and are
    # pinned against this by test, because a fast path nobody checks against
    # the slow one is a second implementation pretending to be an optimisation.
    posterior_variance = 1.0 / (1.0 / prior.variance + 1.0 / (noise_sd * noise_sd))
    posterior_means = posterior_variance * (
        prior.mean / prior.variance + observations / (noise_sd * noise_sd)
    )
    gains = (
        0.5 * math.log(prior.variance / posterior_variance)
        + (posterior_variance + (posterior_means - prior.mean) ** 2) / (2.0 * prior.variance)
        - 0.5
    )

    # Spread of the mixture itself: the law of total variance, so that a set of
    # confident-but-disagreeing roles reads as wide rather than as narrow.
    mixture_mean = float(np.dot(weights, means))
    mixture_variance = float(np.dot(weights, sds**2) + np.dot(weights, (means - mixture_mean) ** 2))

    return EigReport(
        nats=float(np.mean(gains)),
        prior_sd=prior.sd,
        measurement_sd=noise_sd,
        mixture_sd=math.sqrt(mixture_variance),
        disagreement=disagreement(forecasts),
        n_forecasts=len(forecasts),
        draws=draws,
        seed=seed,
    )
