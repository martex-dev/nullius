"""Statistics, verdicts, and computed confidence.

Nothing in this package is ever produced by a language model. The Analyst role
writes prose about these numbers; it does not compute them.
"""

from __future__ import annotations

from nullius.analysis.confidence import (
    ConfidenceInputs,
    ConfidenceReport,
    compute_confidence,
)
from nullius.analysis.multiple import Correction, benjamini_hochberg, correct, holm
from nullius.analysis.stats import PairedResult, SeedVariance, paired_analysis, seed_variance
from nullius.analysis.verdict import VerdictReport, derive_verdict

__all__ = [
    "ConfidenceInputs",
    "ConfidenceReport",
    "Correction",
    "PairedResult",
    "SeedVariance",
    "VerdictReport",
    "benjamini_hochberg",
    "compute_confidence",
    "correct",
    "derive_verdict",
    "holm",
    "paired_analysis",
    "seed_variance",
]
