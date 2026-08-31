"""Allocation policies: who gets funded, and why.

The Director proposes; a policy decides. Everything the decision rests on is a
number computed elsewhere — expected information gain from the Forecast
Ledger, expected cost from the cost model, success probability from the
forecasts themselves — so a policy is a small, testable ranking function over
scored candidates rather than a place where judgement happens invisibly.

``score(h) = EIG(h) × P_success(h) × strategic_weight(h) / expected_cost(h)``

Four policies implement the same interface, which is the point: *does
intelligent allocation help?* is only a question if the alternatives are
interchangeable at the call site. :mod:`nullius.economy.harness` swaps them and
measures.

A fifth is here that the plan does not ask for. :class:`CheapestFirst` ignores
every input except cost. It exists for the same reason the design linter
insists on a capacity-matched control arm: without it, "greedy-EIG beat random"
cannot be told apart from "dividing by cost beat random", and the second is
true of any policy with a denominator. A result that cannot distinguish its own
mechanism from a triviality is not a result.

**Reserves.** A fixed fraction of the budget is fenced off for replication and
another for confirmatory null testing (``docs/02-architecture.md`` §7, F14).
Unspent reserve does not flow back to exploration. That is not an oversight: an
institution that can quietly reallocate its replication budget to novel work
whenever novel work looks more attractive does not have a replication budget,
it has an intention.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar

import numpy as np

__all__ = [
    "POLICIES",
    "Allocation",
    "AllocationPolicy",
    "Candidate",
    "CandidateKind",
    "CheapestFirst",
    "GreedyEig",
    "RandomAllocation",
    "Reserves",
    "RoundRobin",
    "ThompsonSampling",
    "policy_named",
]

_MINIMUM_COST = Decimal("0.000001")
"""A candidate that claims to be free would divide by zero and win everything."""


class CandidateKind(StrEnum):
    """Which reserve a piece of work is paid from.

    The kinds are budget categories, not experiment types. A replication and a
    novel experiment may compile to identical specs; what differs is which
    pocket the money comes out of, and therefore what the institution is
    structurally obliged to keep spending on.
    """

    EXPLORATION = "exploration"
    REPLICATION = "replication"
    NULL_CONFIRMATION = "null_confirmation"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One thing that could be funded, with the numbers behind it."""

    subject_id: uuid.UUID
    label: str
    """Stable, human-meaningful name — a bank item id, or a hypothesis's first words."""

    eig: float
    p_success: float
    expected_cost_usd: Decimal
    kind: CandidateKind = CandidateKind.EXPLORATION
    strategic_weight: float = 1.0
    group: str = ""
    """What line of research this belongs to.

    Round-robin rotates across groups and Thompson sampling learns per group,
    so a group that is left empty defaults to the candidate's own label —
    every candidate its own line of research, which is the right behaviour
    when nothing better is known.
    """

    def __post_init__(self) -> None:
        if self.expected_cost_usd <= 0:
            raise ValueError(f"{self.label}: expected cost must be positive")
        if not 0.0 <= self.p_success <= 1.0:
            raise ValueError(f"{self.label}: p_success is a probability")
        if self.eig < 0:
            raise ValueError(f"{self.label}: information gain cannot be negative")

    @property
    def family(self) -> str:
        return self.group or self.label

    @property
    def score(self) -> float:
        """``EIG × P_success × weight / cost`` — expected nats per dollar."""
        cost = max(self.expected_cost_usd, _MINIMUM_COST)
        return self.eig * self.p_success * self.strategic_weight / float(cost)

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject_id": str(self.subject_id),
            "label": self.label,
            "kind": self.kind.value,
            "eig": round(self.eig, 6),
            "p_success": round(self.p_success, 6),
            "strategic_weight": round(self.strategic_weight, 6),
            "expected_cost_usd": str(self.expected_cost_usd),
            "score": round(self.score, 6),
            "group": self.family,
        }


@dataclass(frozen=True, slots=True)
class Reserves:
    """Fractions of the budget fenced off from exploration.

    The defaults are a judgement, and a stated one: a fifth to replication and
    a seventh to confirming nulls. They are parameters of a versioned
    :class:`~nullius.db.tables.Policy` precisely so that the choice is
    auditable and can itself be tested rather than defended.
    """

    replication: float = 0.20
    null_confirmation: float = 0.15

    def __post_init__(self) -> None:
        if self.replication < 0 or self.null_confirmation < 0:
            raise ValueError("a reserve cannot be negative")
        if self.replication + self.null_confirmation >= 1.0:
            raise ValueError("reserves would leave nothing to explore with")

    @property
    def exploration(self) -> float:
        return 1.0 - self.replication - self.null_confirmation

    def fraction_for(self, kind: CandidateKind) -> float:
        return {
            CandidateKind.EXPLORATION: self.exploration,
            CandidateKind.REPLICATION: self.replication,
            CandidateKind.NULL_CONFIRMATION: self.null_confirmation,
        }[kind]

    def split(self, budget_usd: Decimal) -> dict[CandidateKind, Decimal]:
        """Divide a budget into its pockets. Every dollar lands in exactly one."""
        return {
            kind: (budget_usd * Decimal(str(self.fraction_for(kind)))) for kind in CandidateKind
        }

    def as_dict(self) -> dict[str, float]:
        return {
            "exploration": self.exploration,
            "replication": self.replication,
            "null_confirmation": self.null_confirmation,
        }


@dataclass(frozen=True, slots=True)
class Allocation:
    """What a policy decided, and everything needed to second-guess it."""

    policy_version: str
    policy_params: dict[str, Any]
    budget_usd: Decimal
    reserves: Reserves
    funded: tuple[Candidate, ...] = ()
    shelved: tuple[tuple[Candidate, str], ...] = ()
    spent_by_kind: dict[CandidateKind, Decimal] = field(default_factory=dict)

    @property
    def committed_usd(self) -> Decimal:
        return sum(self.spent_by_kind.values(), Decimal(0))

    @property
    def expected_nats(self) -> float:
        return sum(c.eig for c in self.funded)

    def as_inputs(self) -> dict[str, Any]:
        """The payload for a :class:`~nullius.db.tables.Decision` row.

        Shelved candidates are included with their reason. A record of what was
        funded says what the institution did; a record of what was refused, and
        why, is what makes the policy itself falsifiable later.
        """
        return {
            "policy_version": self.policy_version,
            "policy_params": self.policy_params,
            "budget_usd": str(self.budget_usd),
            "reserves": self.reserves.as_dict(),
            "committed_usd": str(self.committed_usd),
            "spent_by_kind": {k.value: str(v) for k, v in sorted(self.spent_by_kind.items())},
            "funded": [c.as_dict() for c in self.funded],
            "shelved": [{**c.as_dict(), "reason": reason} for c, reason in self.shelved],
        }

    def __str__(self) -> str:
        return (
            f"{self.policy_version}: funded {len(self.funded)} of "
            f"{len(self.funded) + len(self.shelved)} for ${self.committed_usd:.4f} "
            f"of ${self.budget_usd:.2f}"
        )


class AllocationPolicy(ABC):
    """A versioned, swappable decision rule.

    Subclasses supply an ordering. Splitting the budget by reserve, walking the
    order, and recording why each refusal happened is done once, here, so that
    two policies differ in exactly one respect — which is the only way the
    comparison between them measures what it claims to.
    """

    version: ClassVar[str]

    def params(self) -> dict[str, Any]:
        """Whatever configures this policy, for the audit record."""
        return {}

    @abstractmethod
    def rank(self, candidates: Sequence[Candidate]) -> list[Candidate]:
        """Order candidates best-first. Must be a permutation of its input."""

    def allocate(
        self,
        candidates: Iterable[Candidate],
        *,
        budget_usd: Decimal,
        reserves: Reserves | None = None,
    ) -> Allocation:
        """Fund down the ranking until each pocket runs out."""
        reserves = reserves or Reserves()
        pockets = reserves.split(budget_usd)
        remaining = dict(pockets)
        spent = dict.fromkeys(CandidateKind, Decimal(0))

        funded: list[Candidate] = []
        shelved: list[tuple[Candidate, str]] = []

        for candidate in self.rank(list(candidates)):
            pocket = remaining[candidate.kind]
            if candidate.expected_cost_usd <= pocket:
                remaining[candidate.kind] = pocket - candidate.expected_cost_usd
                spent[candidate.kind] += candidate.expected_cost_usd
                funded.append(candidate)
            else:
                shelved.append(
                    (
                        candidate,
                        f"{candidate.kind.value} reserve has ${pocket:.4f} left, "
                        f"below the ${candidate.expected_cost_usd:.4f} expected",
                    )
                )

        return Allocation(
            policy_version=self.version,
            policy_params=self.params(),
            budget_usd=budget_usd,
            reserves=reserves,
            funded=tuple(funded),
            shelved=tuple(shelved),
            spent_by_kind=spent,
        )


def _stable(candidates: Sequence[Candidate]) -> list[Candidate]:
    """A deterministic starting order, so no policy inherits input order as signal."""
    return sorted(candidates, key=lambda c: (c.label, str(c.subject_id)))


class RandomAllocation(AllocationPolicy):
    """Funds in a shuffled order. The null hypothesis of the research economy.

    Seeded, so a comparison against it is reproducible — an unseeded control
    would make every reported difference partly a fact about one draw.
    """

    version = "random/v1"

    __slots__ = ("_seed",)

    def __init__(self, seed: int = 0) -> None:
        self._seed = seed

    def params(self) -> dict[str, Any]:
        return {"seed": self._seed}

    def rank(self, candidates: Sequence[Candidate]) -> list[Candidate]:
        ordered = _stable(candidates)
        rng = np.random.default_rng(self._seed)
        return [ordered[i] for i in rng.permutation(len(ordered))]


class RoundRobin(AllocationPolicy):
    """Takes one candidate from each line of research in turn.

    Not a strawman. Round-robin is what fairness looks like when nobody trusts
    the scores, and it has a real property greedy policies lack: no single line
    of research can consume the budget by scoring well repeatedly. Within a
    group it still prefers the better-scoring candidate, so it is a constraint
    on greed rather than a rejection of it.
    """

    version = "round-robin/v1"

    def rank(self, candidates: Sequence[Candidate]) -> list[Candidate]:
        groups: dict[str, list[Candidate]] = {}
        for candidate in _stable(candidates):
            groups.setdefault(candidate.family, []).append(candidate)
        for members in groups.values():
            members.sort(key=lambda c: (-c.score, c.label))

        ordered: list[Candidate] = []
        while any(groups.values()):
            for family in sorted(groups):
                if groups[family]:
                    ordered.append(groups[family].pop(0))
        return ordered


class CheapestFirst(AllocationPolicy):
    """Ignores every input but cost. The capacity control for allocation.

    Any policy that divides by expected cost will look good on a
    cost-denominated metric. This one isolates that effect: whatever
    :class:`GreedyEig` beats *this* by is attributable to the information terms
    rather than to the denominator they share.
    """

    version = "cheapest-first/v1"

    def rank(self, candidates: Sequence[Candidate]) -> list[Candidate]:
        return sorted(_stable(candidates), key=lambda c: (c.expected_cost_usd, c.label))


class GreedyEig(AllocationPolicy):
    """Funds strictly in descending expected nats per dollar.

    Greedy is optimal for this problem only when candidates are independent and
    divisible, and they are neither: an experiment is all-or-nothing, and two
    experiments on the same question are not worth twice one of them. It is
    used anyway, because the alternative is an optimiser whose failures would
    be harder to attribute than its successes.
    """

    version = "greedy-eig/v1"

    def rank(self, candidates: Sequence[Candidate]) -> list[Candidate]:
        return sorted(_stable(candidates), key=lambda c: (-c.score, c.label))


@dataclass(frozen=True, slots=True)
class GroupHistory:
    """How a line of research has paid off so far.

    ``informative`` counts funded experiments that reached a verdict the
    institution could act on; ``uninformative`` counts those that did not.
    Both are needed: a Beta posterior built from successes alone cannot become
    less confident.
    """

    informative: int = 0
    uninformative: int = 0

    @property
    def alpha(self) -> float:
        return 1.0 + self.informative

    @property
    def beta(self) -> float:
        return 1.0 + self.uninformative


class ThompsonSampling(AllocationPolicy):
    """Samples each line of research's payoff rate, then ranks by the draw.

    The exploration term the greedy policy lacks. A group with no history has a
    uniform Beta posterior, so it is sometimes drawn high and gets funded on a
    run where greedy would never have reached it — which is how a promising
    line that started badly is given another chance.

    Beta-Bernoulli over "was this line of research informative?" rather than
    over the effect itself, deliberately: the effect is what the experiment is
    for, and a policy that formed beliefs about it before running would be
    doing science in the allocator.
    """

    version = "thompson/v1"

    __slots__ = ("_history", "_seed")

    def __init__(self, seed: int = 0, history: Mapping[str, GroupHistory] | None = None) -> None:
        self._seed = seed
        self._history = dict(history or {})

    def params(self) -> dict[str, Any]:
        return {
            "seed": self._seed,
            "history": {
                group: {"informative": h.informative, "uninformative": h.uninformative}
                for group, h in sorted(self._history.items())
            },
        }

    def rank(self, candidates: Sequence[Candidate]) -> list[Candidate]:
        ordered = _stable(candidates)
        rng = np.random.default_rng(self._seed)
        draws = {
            family: float(rng.beta(h.alpha, h.beta))
            for family, h in sorted(
                {c.family: self._history.get(c.family, GroupHistory()) for c in ordered}.items()
            )
        }
        return sorted(
            ordered,
            key=lambda c: (-(draws[c.family] * c.score), c.label),
        )


POLICIES: dict[str, type[AllocationPolicy]] = {
    RandomAllocation.version: RandomAllocation,
    RoundRobin.version: RoundRobin,
    CheapestFirst.version: CheapestFirst,
    GreedyEig.version: GreedyEig,
    ThompsonSampling.version: ThompsonSampling,
}
"""Every policy, by version string.

Versions rather than names because a policy is a
:class:`~nullius.db.tables.Policy` row on every decision it makes, and
``greedy-eig`` changing behaviour without changing its version would silently
rewrite the meaning of past records.
"""


def policy_named(version: str, **kwargs: Any) -> AllocationPolicy:
    """Construct a policy by version string, for the CLI and the harness."""
    try:
        cls = POLICIES[version]
    except KeyError:
        raise KeyError(f"no allocation policy {version!r}; known: {sorted(POLICIES)}") from None
    return cls(**kwargs)
