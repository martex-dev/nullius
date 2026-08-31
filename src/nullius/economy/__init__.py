"""The research economy: what to fund, from what it is expected to teach.

Four pieces, deliberately separable. :mod:`~nullius.economy.eig` turns the
Forecast Ledger into an expected information gain. :mod:`~nullius.economy.
cost_model` turns run history into an expected cost. :mod:`~nullius.economy.
policy` ranks and funds from those two numbers and knows nothing about the
database. :mod:`~nullius.economy.director` connects them to institutional state
and writes down what was decided.

:mod:`~nullius.economy.harness` then asks whether any of it helps, over the
question bank, against a random allocator — and reports the answer either way.
"""

from __future__ import annotations

from nullius.economy.cost_model import CostModel, CostObservation, observations_for_program
from nullius.economy.director import Allocator, candidates_for_program
from nullius.economy.eig import (
    EigReport,
    Gaussian,
    RoleForecast,
    disagreement,
    expected_information_gain,
    measurement_sd,
)
from nullius.economy.policy import (
    POLICIES,
    Allocation,
    AllocationPolicy,
    Candidate,
    CandidateKind,
    CheapestFirst,
    GreedyEig,
    RandomAllocation,
    Reserves,
    RoundRobin,
    ThompsonSampling,
    policy_named,
)

__all__ = [
    "POLICIES",
    "Allocation",
    "AllocationPolicy",
    "Allocator",
    "Candidate",
    "CandidateKind",
    "CheapestFirst",
    "CostModel",
    "CostObservation",
    "EigReport",
    "Gaussian",
    "GreedyEig",
    "RandomAllocation",
    "Reserves",
    "RoleForecast",
    "RoundRobin",
    "ThompsonSampling",
    "candidates_for_program",
    "disagreement",
    "expected_information_gain",
    "measurement_sd",
    "observations_for_program",
    "policy_named",
]
