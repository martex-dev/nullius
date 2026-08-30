"""The confidence rubric.

`docs/02-architecture.md` §5: confidence is *computed* from evidence rows,
never asserted by an agent. This is that function.

The design principle is that every input is something an agent cannot simply
declare. Replication count, interval width, seed variance, open objections,
preregistration status, holdout queries consumed — each is a fact about the
ledger. There is no "how confident are you?" field anywhere, and adding one
would undo the whole arrangement, because a system optimising for impressive
findings would fill it in optimistically every time.

Three of the inputs are *caps* rather than contributions, which is the part
worth reading twice. Evidence can raise confidence gradually; a single
disqualifying fact lowers it immediately. An exploratory registration cannot
produce a well-supported claim no matter how clean the numbers are, because
the design was chosen after seeing data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nullius.db.enums import CONFIDENCE_ORDER, ClaimConfidence

__all__ = ["ConfidenceInputs", "ConfidenceReport", "compute_confidence"]

#: Holdout looks beyond this many start eroding confidence: each additional
#: query is another chance for the test set to be fitted by selection.
FREE_HOLDOUT_QUERIES = 3


@dataclass(frozen=True, slots=True)
class ConfidenceInputs:
    """Facts about the ledger. Every one is checkable; none is an opinion."""

    independent_replications: int = 0
    """Replications by the Replicator role, not reruns by the original author."""

    effect_to_interval_ratio: float = 0.0
    """|effect| divided by the width of its confidence interval."""

    seed_variance_ratio: float = 0.0
    """Effect divided by the run-to-run standard deviation of the baseline arm."""

    open_critical_objections: int = 0
    preregistered: bool = True
    holdout_queries_consumed: int = 0
    provenance_complete: bool = True
    """Every artifact hash in the evidence chain resolves in the store."""

    n_seeds: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "independent_replications": self.independent_replications,
            "effect_to_interval_ratio": self.effect_to_interval_ratio,
            "seed_variance_ratio": self.seed_variance_ratio,
            "open_critical_objections": self.open_critical_objections,
            "preregistered": self.preregistered,
            "holdout_queries_consumed": self.holdout_queries_consumed,
            "provenance_complete": self.provenance_complete,
            "n_seeds": self.n_seeds,
        }


@dataclass(frozen=True, slots=True)
class ConfidenceReport:
    """The computed level, with every reason that moved it."""

    confidence: ClaimConfidence
    inputs: ConfidenceInputs
    raised_by: tuple[str, ...] = field(default_factory=tuple)
    capped_by: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "confidence": self.confidence.value,
            "raised_by": list(self.raised_by),
            "capped_by": list(self.capped_by),
            "inputs": self.inputs.as_dict(),
        }

    def __str__(self) -> str:
        text = f"{self.confidence.value}"
        if self.capped_by:
            text += f" (capped: {'; '.join(self.capped_by)})"
        return text


def _index(level: ClaimConfidence) -> int:
    return CONFIDENCE_ORDER.index(level)


def compute_confidence(inputs: ConfidenceInputs) -> ConfidenceReport:
    """Derive a claim's confidence from what the ledger holds."""
    raised: list[str] = []
    capped: list[str] = []

    # --- Earned, step by step ------------------------------------------------
    level = ClaimConfidence.SPECULATIVE

    if inputs.n_seeds >= 3 and inputs.effect_to_interval_ratio >= 1.0:
        level = ClaimConfidence.SUGGESTIVE
        raised.append("interval narrower than the effect it measures")

    if (
        inputs.n_seeds >= 5
        and inputs.effect_to_interval_ratio >= 2.0
        and inputs.seed_variance_ratio >= 1.0
    ):
        level = ClaimConfidence.SUPPORTED
        raised.append("effect exceeds run-to-run variation across at least five seeds")

    if level is ClaimConfidence.SUPPORTED and inputs.independent_replications >= 1:
        level = ClaimConfidence.WELL_SUPPORTED
        raised.append(f"reproduced independently {inputs.independent_replications} time(s)")

    # --- Caps, applied afterwards -------------------------------------------
    # A ceiling, not a penalty: no amount of other evidence lifts a claim past
    # a disqualifying fact about how it was produced.
    ceilings: list[tuple[ClaimConfidence, str]] = []

    if inputs.open_critical_objections > 0:
        ceilings.append(
            (
                ClaimConfidence.CONTESTED,
                f"{inputs.open_critical_objections} unresolved critical objection(s)",
            )
        )
    if not inputs.preregistered:
        ceilings.append(
            (
                ClaimConfidence.SUGGESTIVE,
                "exploratory: the design was not registered before the data was seen",
            )
        )
    if not inputs.provenance_complete:
        ceilings.append(
            (ClaimConfidence.SPECULATIVE, "evidence chain does not fully resolve to artifacts")
        )
    if inputs.holdout_queries_consumed > FREE_HOLDOUT_QUERIES:
        ceilings.append(
            (
                ClaimConfidence.SUPPORTED,
                f"{inputs.holdout_queries_consumed} holdout queries: the test split has "
                "been looked at enough times to be fitted by selection",
            )
        )
    if inputs.independent_replications == 0:
        ceilings.append((ClaimConfidence.SUPPORTED, "never independently reproduced"))

    for ceiling, reason in ceilings:
        if _index(ceiling) < _index(level):
            level = ceiling
            capped.append(reason)
        elif _index(ceiling) == _index(level):
            capped.append(reason)

    return ConfidenceReport(
        confidence=level,
        inputs=inputs,
        raised_by=tuple(raised),
        capped_by=tuple(capped),
    )
