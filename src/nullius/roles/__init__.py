"""Role contracts, their input views, and their output schemas."""

from __future__ import annotations

from nullius.roles.contracts import CONTRACTS, MOCK_MODEL, contracts_for
from nullius.roles.schemas import (
    AnalysisNote,
    DesignProposal,
    ForecastStatement,
    HypothesisDraft,
)

__all__ = [
    "CONTRACTS",
    "MOCK_MODEL",
    "AnalysisNote",
    "DesignProposal",
    "ForecastStatement",
    "HypothesisDraft",
    "contracts_for",
]
