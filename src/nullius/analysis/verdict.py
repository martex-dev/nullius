"""Deriving a verdict from evidence.

The institution's answer to "is the effect at least ``mde``?" is computed
here, from an interval, by code. No agent writes it. That is not distrust of
the Analyst so much as a structural point: a verdict written in prose can be
nudged, and one derived from an interval cannot be nudged without changing the
interval.

The four answers mirror the bank's own vocabulary
(:mod:`nullius.bank.truth`), which is what makes scoring possible at all:

``supported``
    The interval clears the claimed effect. Not merely "the estimate is
    bigger" — the whole interval is.
``refuted``
    The interval clears it in the opposite direction.
``no_effect``
    The interval is tight enough to rule out an effect of the claimed size,
    and sits within the null band.
``inconclusive``
    Everything else. Two very different situations share this label, and the
    :attr:`Verdict` alone does not distinguish them, so
    :class:`VerdictReport` carries ``reason``: an effect that is real but
    smaller than claimed, versus an interval too wide to say anything. The
    second is a statement about the *design*, and the report says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nullius.analysis.stats import PairedResult
from nullius.bank.truth import NULL_BAND
from nullius.db.enums import Verdict

__all__ = ["VerdictReport", "derive_verdict"]


@dataclass(frozen=True, slots=True)
class VerdictReport:
    """The verdict, and why."""

    verdict: Verdict
    reason: str
    mde: float
    result: PairedResult

    @property
    def underpowered(self) -> bool:
        """True when the interval is too wide to distinguish the outcomes.

        A null that means "we could not tell" is not a null, and reporting it
        as one is the failure mode a bank half full of true zeros exists to
        catch.

        This used to be decided by looking for "too wide" in the reason string,
        because the verdict vocabulary had no way to say it. That worked here
        and nowhere else: the benchmark scored the enum, so the distinction
        never left this class, and an arm that could say nothing was scored
        against a bank where "inconclusive" is a real answer. It is an
        identity check now.
        """
        return self.verdict is Verdict.UNDERPOWERED

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "mde": self.mde,
            "underpowered": self.underpowered,
            "analysis": self.result.as_dict(),
        }

    def __str__(self) -> str:
        return f"{self.verdict.value}: {self.reason}"


def derive_verdict(result: PairedResult, mde: float) -> VerdictReport:
    """Compute the institution's answer from the interval."""
    if mde <= 0:
        raise ValueError("a hypothesis must claim a positive effect size")

    low, high = result.ci_low, result.ci_high
    null_edge = NULL_BAND * mde

    if result.method == "none" and result.n_seeds == 1:
        return VerdictReport(
            Verdict.UNDERPOWERED,
            "one seed gives no interval, so the result is too wide to distinguish "
            "any outcome from any other",
            mde,
            result,
        )

    if low > mde:
        return VerdictReport(
            Verdict.SUPPORTED,
            f"the whole interval [{low:+.4f}, {high:+.4f}] exceeds the claimed {mde:g}",
            mde,
            result,
        )
    if high < -mde:
        return VerdictReport(
            Verdict.REFUTED,
            f"the whole interval [{low:+.4f}, {high:+.4f}] lies past {-mde:g}: the "
            "effect is real and points the other way",
            mde,
            result,
        )
    if -null_edge < low and high < null_edge:
        return VerdictReport(
            Verdict.NO_EFFECT,
            f"the interval [{low:+.4f}, {high:+.4f}] sits inside the null band "
            f"(±{null_edge:g}), ruling out an effect of the claimed size",
            mde,
            result,
        )
    if -mde < low and high < mde:
        return VerdictReport(
            Verdict.INCONCLUSIVE,
            f"the interval [{low:+.4f}, {high:+.4f}] excludes the claimed {mde:g} but "
            "not a smaller effect: something is there, less than was claimed",
            mde,
            result,
        )
    return VerdictReport(
        Verdict.UNDERPOWERED,
        f"the interval [{low:+.4f}, {high:+.4f}] is too wide to separate the claimed "
        f"effect from no effect; this is a statement about the design, not the world",
        mde,
        result,
    )
