"""Ground truth, and how a true effect becomes a correct verdict.

The bank's answers are *measured*, never authored. Writing down what a
configuration ought to do and calling it ground truth would make the benchmark
a test of whether the institution agrees with me, which is the circularity the
whole project exists to avoid (`docs/01-critique.md` §1.1). So
:mod:`nullius.bank.oracle` runs the comparison at a scale no experiment is
allowed, and this module turns the number it produces into a verdict.

Verdicts are defined **relative to the hypothesis's declared effect size**,
not to statistical significance. A hypothesis says "at least ``mde``"; the
honest answers to that are:

``supported``
    The effect is at least as large as claimed.
``refuted``
    The effect is at least that large *in the opposite direction*. Pruning
    does not merely fail to help — it hurts.
``no_effect``
    Nothing is there. Half the bank is this, so a system that always finds
    something scores badly.
``inconclusive``
    An effect exists but is smaller than claimed. This is the category that
    makes the bank a calibration test rather than a detection test: getting it
    right means saying "yes, but not by as much as you said", which is the
    answer a system optimising for impressive findings never gives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nullius.db.enums import Verdict

__all__ = ["MIN_BOUNDARY_MARGIN", "Truth", "ambiguous", "boundary_margin", "classify"]

NULL_BAND = 0.5
"""Below half the declared effect, we call it nothing.

Not arbitrary: it has to be far enough from ``mde`` that a correct
"no effect" is distinguishable from a correct "smaller than claimed", and
close enough to zero that calling it nothing is defensible.
"""


def classify(effect: float, mde: float) -> Verdict:
    """The correct answer to "is the effect at least ``mde``?"."""
    if effect >= mde:
        return Verdict.SUPPORTED
    if effect <= -mde:
        return Verdict.REFUTED
    if abs(effect) < NULL_BAND * mde:
        return Verdict.NO_EFFECT
    return Verdict.INCONCLUSIVE


@dataclass(frozen=True, slots=True)
class Truth:
    """What is actually true about one bank item.

    ``effect`` is the expected difference in the primary metric between the
    treatment and baseline arms, estimated by the oracle at large sample over
    many seeds. It is not a population parameter in the textbook sense — it is
    the expectation of a specific comparison under a specific data generating
    process, which is exactly what the institution is being asked about.
    """

    item_id: str
    effect: float
    standard_error: float
    verdict: Verdict
    mde: float
    oracle_samples: int
    oracle_seeds: int
    causal_features: tuple[str, ...] = ()
    planted_defects: tuple[str, ...] = ()

    @property
    def sign(self) -> int:
        return 0 if self.verdict is Verdict.NO_EFFECT else (1 if self.effect > 0 else -1)

    @property
    def is_null(self) -> bool:
        return self.verdict is Verdict.NO_EFFECT

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "effect": round(self.effect, 6),
            "standard_error": round(self.standard_error, 6),
            "verdict": self.verdict.value,
            "mde": self.mde,
            "oracle_samples": self.oracle_samples,
            "oracle_seeds": self.oracle_seeds,
            "causal_features": list(self.causal_features),
            "planted_defects": list(self.planted_defects),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Truth:
        return cls(
            item_id=payload["item_id"],
            effect=payload["effect"],
            standard_error=payload["standard_error"],
            verdict=Verdict(payload["verdict"]),
            mde=payload["mde"],
            oracle_samples=payload["oracle_samples"],
            oracle_seeds=payload["oracle_seeds"],
            causal_features=tuple(payload.get("causal_features", ())),
            planted_defects=tuple(payload.get("planted_defects", ())),
        )

    def __str__(self) -> str:
        return (
            f"{self.item_id}: effect {self.effect:+.4f} ± {self.standard_error:.4f} "
            f"→ {self.verdict.value} (mde {self.mde:g})"
        )


MIN_BOUNDARY_MARGIN = 3.0
"""How far a measured truth must sit from any verdict boundary, in its own
standard errors.

A truth whose uncertainty straddles the line between ``supported`` and
``inconclusive`` cannot score anything: the institution would be marked wrong
for an answer the oracle itself cannot confidently give. Items that fail this
are fixed by moving the configuration, never by moving the boundary.
"""


def boundary_margin(truth: Truth) -> float:
    """Distance from the nearest verdict boundary, in standard errors."""
    if truth.standard_error <= 0:
        return float("inf")
    boundaries = (truth.mde, -truth.mde, NULL_BAND * truth.mde, -NULL_BAND * truth.mde)
    return min(abs(truth.effect - b) for b in boundaries) / truth.standard_error


def ambiguous(
    truths: list[Truth] | tuple[Truth, ...], min_margin: float = MIN_BOUNDARY_MARGIN
) -> list[str]:
    """Items whose own measurement cannot decide their verdict."""
    return [t.item_id for t in truths if boundary_margin(t) < min_margin]
