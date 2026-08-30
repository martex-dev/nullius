"""Multiple-comparison control, applied at the level of the research programme.

The correction that matters is not the one inside a single experiment — it is
the one across every hypothesis a programme tested. An institution that runs
forty experiments and reports the three that cleared p < 0.05 has found
nothing, and correcting only within each experiment would not notice
(`docs/01-critique.md` F16).

That is why these take a *family* of registrations. The family is read from
the registration ledger, not assembled by whoever is writing the report, so it
cannot quietly shrink to the tests that worked.

Two procedures, because they answer different questions:

``holm``
    Controls the probability of *any* false claim. The right choice when a
    single wrong institutional claim is costly — which is the default posture
    here.
``benjamini_hochberg``
    Controls the expected *proportion* of false claims among those made. The
    right choice for a screening pass whose output will be replicated anyway.

Which one a programme uses is part of its preregistered analysis plan, so the
choice is made before the p-values exist.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import stats

__all__ = ["Correction", "benjamini_hochberg", "correct", "holm"]


@dataclass(frozen=True, slots=True)
class Correction:
    """Adjusted p-values and which hypotheses survive."""

    method: str
    alpha: float
    raw: tuple[float, ...]
    adjusted: tuple[float, ...]
    rejected: tuple[bool, ...]

    @property
    def n_rejected(self) -> int:
        return sum(self.rejected)

    def as_dict(self) -> dict[str, object]:
        return {
            "method": self.method,
            "alpha": self.alpha,
            "raw": list(self.raw),
            "adjusted": list(self.adjusted),
            "rejected": list(self.rejected),
        }


def holm(p_values: Sequence[float], alpha: float = 0.05) -> Correction:
    """Holm–Bonferroni step-down. Controls the family-wise error rate.

    Sort ascending, compare the *i*-th smallest against ``alpha / (m - i)``,
    and stop at the first failure — everything after it is retained too.
    Adjusted values are made monotone so that a larger raw p-value can never
    end up with a smaller adjusted one.
    """
    raw = np.asarray(p_values, dtype=np.float64)
    m = raw.size
    if m == 0:
        return Correction("holm", alpha, (), (), ())

    order = np.argsort(raw, kind="stable")
    adjusted_sorted = np.empty(m, dtype=np.float64)

    running = 0.0
    for rank, index in enumerate(order):
        candidate = (m - rank) * raw[index]
        running = max(running, candidate)  # enforce monotonicity
        adjusted_sorted[rank] = min(running, 1.0)

    adjusted = np.empty(m, dtype=np.float64)
    adjusted[order] = adjusted_sorted

    return Correction(
        method="holm",
        alpha=alpha,
        raw=tuple(float(value) for value in raw),
        adjusted=tuple(float(value) for value in adjusted),
        rejected=tuple(bool(value <= alpha) for value in adjusted),
    )


def benjamini_hochberg(p_values: Sequence[float], alpha: float = 0.05) -> Correction:
    """Benjamini–Hochberg step-up. Controls the false discovery rate.

    Delegates the adjustment to :func:`scipy.stats.false_discovery_control`
    rather than reimplementing it — an independently maintained
    implementation is worth more here than a few lines of our own.
    """
    raw = np.asarray(p_values, dtype=np.float64)
    if raw.size == 0:
        return Correction("benjamini_hochberg", alpha, (), (), ())

    adjusted = stats.false_discovery_control(raw, method="bh")
    return Correction(
        method="benjamini_hochberg",
        alpha=alpha,
        raw=tuple(float(value) for value in raw),
        adjusted=tuple(float(value) for value in adjusted),
        rejected=tuple(bool(value <= alpha) for value in adjusted),
    )


def correct(p_values: Sequence[float], method: str, alpha: float = 0.05) -> Correction:
    """Apply the preregistered correction by name."""
    match method:
        case "holm":
            return holm(p_values, alpha)
        case "benjamini_hochberg" | "bh" | "fdr":
            return benjamini_hochberg(p_values, alpha)
        case "none":
            raw = tuple(float(p) for p in p_values)
            return Correction("none", alpha, raw, raw, tuple(p <= alpha for p in raw))
        case _:
            raise ValueError(
                f"unknown correction {method!r}; the analysis plan must name one of "
                "'holm', 'benjamini_hochberg', or 'none'"
            )
