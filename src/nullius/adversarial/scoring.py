"""Scoring the adversarial layer.

Recall and precision on planted defects, for the detectors and for the
Skeptic, reported separately so the Skeptic's contribution is visible rather
than folded into the code baseline's.

Precision matters as much as recall here, and for a reason specific to this
role. A Skeptic that objects to everything achieves perfect recall and is
useless: every claim gets blocked, so blocking carries no information. The
harness therefore runs *clean* experiments as well as defective ones, and
counts an objection raised against a clean design as a false positive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["DefectTrial", "SkepticScore", "score_trials"]


@dataclass(frozen=True, slots=True)
class DefectTrial:
    """One experiment, with or without a planted defect, and what was raised."""

    item_id: str
    planted: str | None
    """The defect kind, or ``None`` for a clean control."""

    expected_objection: str | None
    raised: tuple[str, ...]
    """Objection types actually raised, by whichever party is being scored."""

    @property
    def is_control(self) -> bool:
        return self.planted is None

    @property
    def caught(self) -> bool:
        return self.expected_objection is not None and self.expected_objection in self.raised

    @property
    def false_alarm(self) -> bool:
        """A critical objection against a design with nothing wrong with it."""
        return self.is_control and bool(self.raised)


@dataclass(frozen=True, slots=True)
class SkepticScore:
    """Recall, precision, and the counts behind them."""

    party: str
    planted: int
    caught: int
    controls: int
    false_alarms: int
    per_defect: dict[str, bool] = field(default_factory=dict)

    @property
    def recall(self) -> float:
        """Fraction of planted defects found. Undefined with nothing planted."""
        return self.caught / self.planted if self.planted else float("nan")

    @property
    def precision(self) -> float:
        """Fraction of flagged designs that really were defective."""
        flagged = self.caught + self.false_alarms
        return self.caught / flagged if flagged else float("nan")

    @property
    def specificity(self) -> float:
        """Fraction of clean designs correctly left alone."""
        if not self.controls:
            return float("nan")
        return (self.controls - self.false_alarms) / self.controls

    def as_dict(self) -> dict[str, Any]:
        return {
            "party": self.party,
            "planted": self.planted,
            "caught": self.caught,
            "controls": self.controls,
            "false_alarms": self.false_alarms,
            "recall": self.recall,
            "precision": self.precision,
            "specificity": self.specificity,
            "per_defect": dict(self.per_defect),
        }

    def __str__(self) -> str:
        return (
            f"{self.party}: recall {self.recall:.2f} ({self.caught}/{self.planted}), "
            f"precision {self.precision:.2f}, "
            f"specificity {self.specificity:.2f} "
            f"({self.controls - self.false_alarms}/{self.controls} clean designs left alone)"
        )


def score_trials(party: str, trials: list[DefectTrial]) -> SkepticScore:
    """Turn a set of trials into recall, precision and specificity."""
    planted = [t for t in trials if not t.is_control]
    controls = [t for t in trials if t.is_control]

    return SkepticScore(
        party=party,
        planted=len(planted),
        caught=sum(1 for t in planted if t.caught),
        controls=len(controls),
        false_alarms=sum(1 for t in controls if t.false_alarm),
        per_defect={t.planted: t.caught for t in planted if t.planted},
    )
