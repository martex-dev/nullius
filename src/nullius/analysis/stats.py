"""Paired analysis over seeds.

Every experiment in Nullius compares two arms on the *same* seeds, so the unit
of analysis is the per-seed difference and the whole apparatus is paired. That
is a large gain in precision and it is also the only honest framing: the arms
saw identical data, so treating them as independent samples would overstate
the uncertainty and understate the effect.

Nothing here is ever produced by a language model. The Analyst role writes
prose *about* these numbers; it does not compute them, and
:mod:`nullius.repository` will not accept a metric it did not get from code.

Two deliberate refusals:

**A single seed yields no interval.** With one observation there is no
variance to estimate, and returning a zero-width interval would be a lie with
a decimal point on it.

**Bias-corrected bootstrap is not used on tiny samples.** BCa's acceleration
term is estimated by jackknife, which is unstable below roughly ten
observations. Below that threshold the interval is computed by the percentile
method and *says so* in :attr:`PairedResult.method`, so a report can never
present a fragile interval as a robust one.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from scipy import stats

__all__ = [
    "BCA_MINIMUM_SAMPLES",
    "PairedResult",
    "paired_analysis",
    "seed_variance",
]

BCA_MINIMUM_SAMPLES = 10
"""Below this many seeds, the acceleration term is not worth trusting."""

DEFAULT_RESAMPLES = 10_000
DEFAULT_ALPHA = 0.05


@dataclass(frozen=True, slots=True)
class PairedResult:
    """The paired comparison of two arms across seeds."""

    n_seeds: int
    baseline_mean: float
    treatment_mean: float
    difference: float
    ci_low: float
    ci_high: float
    standard_error: float
    p_value: float
    effect_size: float
    """Cohen's *dz*: the mean difference in units of its own standard deviation."""
    method: Literal["bca", "percentile", "none"]
    alpha: float
    resamples: int

    @property
    def interval_excludes_zero(self) -> bool:
        return self.ci_low > 0 or self.ci_high < 0

    def exceeds(self, threshold: float) -> bool:
        """Whether the whole interval clears ``threshold`` in its own direction.

        Stricter than "the estimate exceeds the threshold", and it is the
        right question: a hypothesis claims an effect of at least some size,
        so the evidence has to rule out anything smaller.
        """
        if threshold >= 0:
            return self.ci_low > threshold
        return self.ci_high < threshold

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_seeds": self.n_seeds,
            "baseline_mean": self.baseline_mean,
            "treatment_mean": self.treatment_mean,
            "difference": self.difference,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "standard_error": self.standard_error,
            "p_value": self.p_value,
            "effect_size": self.effect_size,
            "method": self.method,
            "alpha": self.alpha,
            "resamples": self.resamples,
        }

    def __str__(self) -> str:
        return (
            f"{self.difference:+.4f} "
            f"[{self.ci_low:+.4f}, {self.ci_high:+.4f}] "
            f"({self.method}, n={self.n_seeds}, p={self.p_value:.4g})"
        )


def paired_analysis(
    baseline: Sequence[float],
    treatment: Sequence[float],
    *,
    alpha: float = DEFAULT_ALPHA,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = 0,
) -> PairedResult:
    """Compare two arms measured on the same seeds.

    ``seed`` fixes the resampling so an analysis is reproducible: rerunning it
    must not move the interval, or two readers of the same data would see
    different conclusions.
    """
    first = np.asarray(baseline, dtype=np.float64)
    second = np.asarray(treatment, dtype=np.float64)
    if first.shape != second.shape:
        raise ValueError(
            f"paired analysis needs one value per arm per seed; got {first.size} "
            f"baseline and {second.size} treatment values"
        )
    if first.size == 0:
        raise ValueError("no seeds to analyse")

    differences = second - first
    n = int(differences.size)
    difference = float(differences.mean())

    if n == 1:
        # One observation carries no information about its own spread.
        return PairedResult(
            n_seeds=1,
            baseline_mean=float(first[0]),
            treatment_mean=float(second[0]),
            difference=difference,
            ci_low=float("-inf"),
            ci_high=float("inf"),
            standard_error=float("nan"),
            p_value=float("nan"),
            effect_size=float("nan"),
            method="none",
            alpha=alpha,
            resamples=0,
        )

    spread = float(differences.std(ddof=1))
    standard_error = spread / math.sqrt(n)

    if spread == 0.0:
        # Identical every time. A bootstrap would resample a constant.
        p_value = 0.0 if difference != 0.0 else 1.0
        effect = math.inf if difference != 0.0 else 0.0
        return PairedResult(
            n_seeds=n,
            baseline_mean=float(first.mean()),
            treatment_mean=float(second.mean()),
            difference=difference,
            ci_low=difference,
            ci_high=difference,
            standard_error=0.0,
            p_value=p_value,
            effect_size=effect,
            method="none",
            alpha=alpha,
            resamples=0,
        )

    method: Literal["bca", "percentile"] = "bca" if n >= BCA_MINIMUM_SAMPLES else "percentile"
    interval = stats.bootstrap(
        (differences,),
        np.mean,
        confidence_level=1 - alpha,
        n_resamples=resamples,
        method=method,
        random_state=np.random.default_rng(seed),
    ).confidence_interval

    return PairedResult(
        n_seeds=n,
        baseline_mean=float(first.mean()),
        treatment_mean=float(second.mean()),
        difference=difference,
        ci_low=float(interval.low),
        ci_high=float(interval.high),
        standard_error=standard_error,
        p_value=float(stats.ttest_rel(second, first).pvalue),
        effect_size=difference / spread,
        method=method,
        alpha=alpha,
        resamples=resamples,
    )


@dataclass(frozen=True, slots=True)
class SeedVariance:
    """How much a single arm moves from seed to seed.

    Reported alongside every effect, because an effect smaller than the
    run-to-run noise of either arm is not an effect anyone should act on — and
    because a *zero* here usually means the seeds were not doing anything.
    """

    mean: float
    sd: float
    minimum: float
    maximum: float
    n: int

    @property
    def coefficient_of_variation(self) -> float:
        return self.sd / abs(self.mean) if self.mean else float("inf")

    def as_dict(self) -> dict[str, float | int]:
        return {
            "mean": self.mean,
            "sd": self.sd,
            "min": self.minimum,
            "max": self.maximum,
            "n": self.n,
        }


def seed_variance(values: Sequence[float]) -> SeedVariance:
    """Spread of one arm's metric across seeds."""
    array = np.asarray(values, dtype=np.float64)
    if array.size == 0:
        raise ValueError("no values")
    return SeedVariance(
        mean=float(array.mean()),
        sd=float(array.std(ddof=1)) if array.size > 1 else 0.0,
        minimum=float(array.min()),
        maximum=float(array.max()),
        n=int(array.size),
    )
