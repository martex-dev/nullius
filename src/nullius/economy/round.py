"""A funding round: several questions, one purse, and a policy that chooses.

The rest of the economy is a library about allocation. This is the part that
makes the institution live under it. Several research questions are proposed,
a policy decides which of them the laboratory's budget will pay to run, the
decision is written to the ledger with the numbers it was made from, and only
the funded ones are executed.

**Why a round spans programmes rather than living inside one.** A
:class:`~nullius.db.tables.Program` is one research question, one budget, one
policy — that is what the data model says, and the institutional-novelty check
from M8 enforces it: two hypotheses within a programme that make the same
claim about the same metric are a duplicate, and the second is refused. Bank
items are *different questions*, so gathering them into one programme would
have the novelty guard rejecting nineteen of twenty perfectly good proposals.
Each question therefore gets its own programme, and the money is allocated one
tier up, at the laboratory — which is exactly the ``institution → program``
boundary in the budget hierarchy of ``docs/02-architecture.md`` §7.

The order matters and is the whole design.

1. **Propose everything.** Each question is carried to a locked registration
   and locked forecasts. That is cheap — model calls, no sandbox — and it is
   the only way to obtain the expected information gain, which is computed
   *from* the forecasts and so cannot be known before eliciting them.
2. **Allocate once, with everything on the table.** A policy ranks every
   proposal in the round and fills each budget pocket. Funding is comparative:
   this was funded *instead of* those, and the
   :class:`~nullius.db.tables.Decision` rows say so.
3. **Execute only what was funded.** The expensive half runs for the winners.
   The rest reach ``ABANDONED_BUDGET``, which ``docs/02-architecture.md`` §7
   names a legitimate terminal state rather than a failure.

**Unfunded work is not discarded.** A shelved proposal keeps its programme,
its registration, its forecasts, and a decision row explaining what beat it.
An institution that threw those away could not say what it chose not to learn,
and that counterfactual is exactly what a critic of an allocation policy needs.

**A known bias, stated rather than fixed.** The forecasts attached to an
unfunded proposal are never scored, because nothing happened to score them
against. So the calibration record grows only where the money went, and a
policy that persistently starves a line of research also never learns whether
the roles were any good at predicting it. That is inherent to spending money
on some things and not others; correcting it would mean running the
experiments anyway, which is the thing being avoided.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from nullius.bank.items import BankItem
from nullius.db.enums import HypothesisState, Role
from nullius.economy.cost_model import CostModel, observations_for_program
from nullius.economy.director import Allocator, candidates_for_program
from nullius.economy.policy import Allocation, AllocationPolicy, Candidate, GreedyEig, Reserves
from nullius.kernel import KernelOutcome, Proposal, ResearchKernel
from nullius.repository import Repository
from nullius.runtime.budget import BudgetEnvelope, BudgetLedger

__all__ = ["PER_TRAINING_FALLBACK_USD", "FundingRound", "RoundResult"]

PER_TRAINING_FALLBACK_USD = Decimal("0.0005")
"""Assumed cost per estimator fitting, before any experiment has run.

Scaled by the size of the design rather than flat, so the first allocation of
a round is not wrong about the expensive proposals in the same direction as
about the cheap ones. Superseded by the fitted cost model the moment this
laboratory has run anything.
"""

_UNCAPPED_PROGRAMME = Decimal("1000000")
"""A programme ceiling high enough never to be the binding constraint.

The round's real limit is the laboratory's. Splitting the round budget into
per-programme shares would make the programme tier bind first, and every
refusal would then name the wrong level.
"""


@dataclass(frozen=True, slots=True)
class RoundResult:
    """What one funding round did, and what it declined to do."""

    lab_id: uuid.UUID
    allocation: Allocation | None
    executed: tuple[KernelOutcome, ...] = ()
    unfunded: tuple[KernelOutcome, ...] = ()
    proposal_usd: Decimal = Decimal(0)
    """Spent proposing, including on the questions that were never run.

    Reported separately because it is the price of being able to choose. A
    round that funds one experiment out of ten still paid to propose ten, and
    an efficiency figure that ignored that would flatter every selective
    allocator.
    """

    total_usd: Decimal = Decimal(0)
    """Everything the round spent, taken from the cost ledger.

    Read from :class:`~nullius.runtime.budget.BudgetLedger` rather than summed
    from the parts, because the parts overlap: an executed outcome's ``usd``
    already contains the cost of proposing it, so adding it to
    ``proposal_usd`` would charge the proposing half twice.
    """

    halted: tuple[str, ...] = ()

    @property
    def proposed(self) -> int:
        return len(self.executed) + len(self.unfunded)

    @property
    def claims(self) -> int:
        return sum(1 for outcome in self.executed if outcome.completed)

    @property
    def execution_usd(self) -> Decimal:
        """The marginal cost of running what was funded, above proposing it."""
        return max(self.total_usd - self.proposal_usd, Decimal(0))

    @property
    def usd_per_claim(self) -> Decimal | None:
        """Total spend per claim reached, or ``None`` when none was.

        Total, not marginal: the money spent proposing the questions that lost
        is part of what the surviving claims cost. A round may not bill the
        experiments it declined to run to nobody.
        """
        return (self.total_usd / self.claims) if self.claims else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "lab_id": str(self.lab_id),
            "proposed": self.proposed,
            "funded": len(self.executed),
            "claims": self.claims,
            "proposal_usd": str(self.proposal_usd),
            "execution_usd": str(self.execution_usd),
            "total_usd": str(self.total_usd),
            "usd_per_claim": str(self.usd_per_claim) if self.usd_per_claim else None,
            "allocation": self.allocation.as_inputs() if self.allocation else None,
            "halted": list(self.halted),
        }

    def __str__(self) -> str:
        per_claim = f"${self.usd_per_claim:.6f}/claim" if self.usd_per_claim else "no claim"
        return (
            f"{len(self.executed)} of {self.proposed} funded, {self.claims} claims, "
            f"${self.total_usd:.6f} (${self.proposal_usd:.6f} proposing) -> {per_claim}"
        )


@dataclass(slots=True)
class FundingRound:
    """Proposes several questions, funds some of them, and runs only those.

    Holds no state between calls beyond its collaborators. A round's memory is
    the ledger, which is where an institution's memory belongs.
    """

    kernel: ResearchKernel
    repo: Repository
    lab_id: uuid.UUID
    policy_id: uuid.UUID
    policy: AllocationPolicy = field(default_factory=GreedyEig)
    reserves: Reserves = field(default_factory=lambda: Reserves(0.0, 0.0))
    """No reserve by default.

    Every candidate a bank round produces is exploration, so a replication
    reserve would fence off budget that nothing on the table is eligible to
    spend. A reserve that cannot be drawn on is not prudence, it is a smaller
    budget wearing a policy's clothes. Set it when there is replication or
    null-confirmation work to pay for.
    """

    hypothesis_cap_usd: Decimal | None = None
    """Per-hypothesis ceiling, checked at dispatch by the budget hierarchy."""

    def run(
        self,
        items: Sequence[BankItem],
        *,
        budget_usd: Decimal,
        allowance: Decimal = Decimal("0.50"),
    ) -> RoundResult:
        """Propose every question, fund what the policy chooses, run only those."""
        if not items:
            raise ValueError("a funding round needs at least one question")

        opened = [
            (
                program_id,
                self.kernel.propose(item, program_id=program_id, allowance=allowance),
            )
            for item, program_id in ((item, self._open_programme(item)) for item in items)
        ]
        proposal_usd = sum((p.usd for _pid, p in opened), Decimal(0))

        fundable = [(pid, p) for pid, p in opened if p.fundable]
        halted = tuple(
            f"{p.item.item_id}: {p.halted}" for _pid, p in opened if p.halted is not None
        )
        if not fundable:
            return self._result(
                allocation=None,
                executed=[],
                unfunded=[p.outcome() for _pid, p in opened],
                proposal_usd=proposal_usd,
                halted=halted,
            )

        allocation = self._allocate(fundable, budget_usd=budget_usd)
        funded_ids = {c.subject_id for c in allocation.funded}

        executed: list[KernelOutcome] = []
        unfunded: list[KernelOutcome] = [p.outcome() for _pid, p in opened if not p.fundable]

        for _program_id, proposal in fundable:
            if proposal.registration_id in funded_ids:
                executed.append(self._execute(proposal, allowance=allowance))
            else:
                unfunded.append(self._shelve(proposal))

        return self._result(
            allocation=allocation,
            executed=executed,
            unfunded=unfunded,
            proposal_usd=proposal_usd,
            halted=halted,
        )

    # -------------------------------------------------------------- internals

    def _open_programme(self, item: BankItem) -> uuid.UUID:
        """One research question, one programme."""
        director = self.repo.as_role(Role.DIRECTOR)
        question = director.create_research_question(
            item.question, domain="tabular-ml", bank_item_id=item.item_id
        )
        programme = director.create_program(
            rq_id=question.rq_id,
            lab_id=self.lab_id,
            policy_id=self.policy_id,
            budget_usd=_UNCAPPED_PROGRAMME,
            config_hash="0" * 64,
            capability_digest="1" * 64,
        )
        return programme.program_id

    def _allocate(
        self,
        fundable: Sequence[tuple[uuid.UUID, Proposal]],
        *,
        budget_usd: Decimal,
    ) -> Allocation:
        """Score every registration in the round, and let the policy choose.

        Candidates are rebuilt from the database rather than carried over from
        the proposals, deliberately. The allocator must see exactly what the
        ledger holds — if a forecast failed to record, the experiment it
        belongs to should not be fundable, and reading through the proposal
        objects would hide that.
        """
        candidates: list[Candidate] = []
        owner: dict[uuid.UUID, uuid.UUID] = {}

        for program_id, proposal in fundable:
            if proposal.registration_id is None:
                continue
            model = CostModel.fit(
                observations_for_program(self.repo, program_id),
                fallback_usd=self._fallback_cost(proposal),
            )
            for candidate in candidates_for_program(
                self.repo,
                program_id,
                registration_ids=[proposal.registration_id],
                cost_model=model,
            ):
                candidates.append(candidate)
                owner[candidate.subject_id] = program_id

        allocation = self.policy.allocate(candidates, budget_usd=budget_usd, reserves=self.reserves)

        # A decision belongs to the programme whose work it concerns, so the
        # round's single allocation is recorded programme by programme. Every
        # row still carries the whole allocation in its inputs, which is what
        # keeps the comparison between them recoverable afterwards.
        Allocator(
            repo=self.repo,
            policy=self.policy,
            policy_id=self.policy_id,
            reserves=self.reserves,
        ).record(allocation, owner=owner)
        return allocation

    def _fallback_cost(self, proposal: Proposal) -> Decimal:
        """What to assume this experiment costs, before any have run."""
        spec = proposal.spec
        trainings = (spec.n_seeds * len(spec.arms)) if spec is not None else 1
        return PER_TRAINING_FALLBACK_USD * Decimal(max(trainings, 1))

    def _execute(self, proposal: Proposal, *, allowance: Decimal) -> KernelOutcome:
        """Run a funded proposal under its hypothesis-level cap."""
        if self.hypothesis_cap_usd is not None and proposal.hypothesis_id is not None:
            envelope = BudgetEnvelope(
                hypothesis_id=proposal.hypothesis_id,
                hypothesis_cap_usd=self.hypothesis_cap_usd,
            )
            ruling = BudgetLedger(self.repo).rule(proposal.program_id, allowance, envelope)
            if not ruling.allowed:
                return self._shelve(proposal, reason=str(ruling))
        return self.kernel.execute(proposal, allowance=allowance)

    def _shelve(self, proposal: Proposal, reason: str | None = None) -> KernelOutcome:
        """Record a proposal the budget did not reach.

        ``ABANDONED_BUDGET`` rather than ``SHELVED``: the design was sound
        enough to register and the forecasts were taken, so what stopped it was
        money, and the state should say which. A hypothesis abandoned for want
        of budget is a fact about the laboratory's finances, not a judgement on
        the question.
        """
        if proposal.hypothesis_id is not None:
            self.repo.as_role(Role.DIRECTOR).advance_hypothesis(
                proposal.hypothesis_id, HypothesisState.ABANDONED_BUDGET
            )
        return proposal.outcome(halted=reason or "not funded: the budget went elsewhere")

    def _result(
        self,
        *,
        allocation: Allocation | None,
        executed: Sequence[KernelOutcome],
        unfunded: Sequence[KernelOutcome],
        proposal_usd: Decimal,
        halted: tuple[str, ...],
    ) -> RoundResult:
        # The cap is irrelevant here; only the spend is being read.
        spent = BudgetLedger(self.repo).institution_status(self.lab_id, Decimal(0)).spent_usd
        return RoundResult(
            lab_id=self.lab_id,
            allocation=allocation,
            executed=tuple(executed),
            unfunded=tuple(unfunded),
            proposal_usd=proposal_usd,
            total_usd=spent,
            halted=halted,
        )
