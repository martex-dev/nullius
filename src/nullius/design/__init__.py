"""Experiment specification, design linting, and power analysis."""

from __future__ import annotations

from nullius.design.linter import Finding, LintReport, Severity, lint
from nullius.design.power import minimum_detectable_effect, power_for, required_seeds
from nullius.design.spec import (
    ArmSpec,
    DatasetSpec,
    EstimatorSpec,
    ExperimentSpec,
    SplitSpec,
    TransformSpec,
)

__all__ = [
    "ArmSpec",
    "DatasetSpec",
    "EstimatorSpec",
    "ExperimentSpec",
    "Finding",
    "LintReport",
    "Severity",
    "SplitSpec",
    "TransformSpec",
    "lint",
    "minimum_detectable_effect",
    "power_for",
    "required_seeds",
]
