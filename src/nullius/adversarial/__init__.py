"""The adversarial layer: planted defects, code detectors, Skeptic, Reviewer."""

from __future__ import annotations

from nullius.adversarial.defects import (
    DEFECTS,
    Defect,
    DefectKind,
    inject,
    registered_defects,
)
from nullius.adversarial.detectors import DETECTORS, Finding, run_detectors
from nullius.adversarial.roles import (
    ObjectionStatement,
    ReviewStatement,
    SkepticReport,
    adversarial_contracts,
)
from nullius.adversarial.scoring import DefectTrial, SkepticScore, score_trials

__all__ = [
    "DEFECTS",
    "DETECTORS",
    "Defect",
    "DefectKind",
    "DefectTrial",
    "Finding",
    "ObjectionStatement",
    "ReviewStatement",
    "SkepticReport",
    "SkepticScore",
    "adversarial_contracts",
    "inject",
    "registered_defects",
    "run_detectors",
    "score_trials",
]
