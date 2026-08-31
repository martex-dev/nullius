"""The cost ledger and hierarchical budgets.

Budgets are hard and denominated in real money. A task whose allowance exceeds
what its programme has left is refused *at dispatch* — before a model is
called, not after the money is spent — and the refusal is recorded as an event
rather than raised as an error.

That last part is a deliberate design choice from `docs/02-architecture.md`
§7: running out of budget is a legitimate terminal state for a line of
research, not a crash. An institution that cannot afford to answer a question
has learned something about the question's cost, and the ledger should say so.

**The hierarchy.** ``institution → program → hypothesis → task``. The programme
level is authoritative and always checked, because its budget is a column on a
row. The levels above and below it are caps a caller supplies in a
:class:`BudgetEnvelope`, checked against spend derived from the cost ledger by
join. Where the derivation is possible the check happens; the alternative — a
denormalised ``hypothesis_id`` on every cost row — would create a second place
for the truth to live and a way for the two to disagree.

A refusal names the level that bound it. "Refused: over budget" tells an
operator nothing they can act on; "refused: the hypothesis has $0.03 of its
$0.50 left, though the programme has $8 left" tells them which knob to turn.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

import sqlalchemy as sa

from nullius.db.tables import CostEntry, Program, Registration, Run, Task
from nullius.llm.pricing import PRICE_TABLE_VERSION
from nullius.llm.types import Usage
from nullius.repository import Repository

__all__ = [
    "BudgetEnvelope",
    "BudgetLedger",
    "BudgetLevel",
    "BudgetStatus",
    "DispatchRuling",
]


class BudgetLevel(StrEnum):
    """The four tiers of `docs/02-architecture.md` §7, outermost first."""

    INSTITUTION = "institution"
    PROGRAM = "program"
    HYPOTHESIS = "hypothesis"
    TASK = "task"


@dataclass(frozen=True, slots=True)
class BudgetEnvelope:
    """The caps around one dispatch, beyond the programme's own.

    Every field is optional and an absent cap is not enforced — which is
    honest rather than lax. A hierarchy that invented a default institutional
    ceiling would be enforcing a number nobody chose, and the first time it
    bound, the refusal would be attributed to a policy that does not exist.
    """

    lab_id: uuid.UUID | None = None
    lab_cap_usd: Decimal | None = None
    hypothesis_id: uuid.UUID | None = None
    hypothesis_cap_usd: Decimal | None = None

    def as_dict(self) -> dict[str, str]:
        return {
            key: str(value)
            for key, value in (
                ("lab_id", self.lab_id),
                ("lab_cap_usd", self.lab_cap_usd),
                ("hypothesis_id", self.hypothesis_id),
                ("hypothesis_cap_usd", self.hypothesis_cap_usd),
            )
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class DispatchRuling:
    """Whether a task may be dispatched, and which level decided."""

    allowed: bool
    level: BudgetLevel | None = None
    """The binding level when refused; ``None`` when allowed."""

    reason: str | None = None

    def __str__(self) -> str:
        return "allowed" if self.allowed else f"refused at {self.level}: {self.reason}"


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    """What a programme has, has spent, and has left."""

    program_id: uuid.UUID
    budget_usd: Decimal
    spent_usd: Decimal

    @property
    def remaining_usd(self) -> Decimal:
        return self.budget_usd - self.spent_usd

    @property
    def exhausted(self) -> bool:
        return self.remaining_usd <= 0

    def can_afford(self, amount: Decimal) -> bool:
        return amount <= self.remaining_usd

    def __str__(self) -> str:
        return (
            f"${self.spent_usd:.4f} spent of ${self.budget_usd:.2f} "
            f"(${self.remaining_usd:.4f} remaining)"
        )


class BudgetLedger:
    """Reads and writes the cost side of a programme."""

    __slots__ = ("_repo", "_session")

    def __init__(self, repo: Repository) -> None:
        self._repo = repo
        self._session = repo.session

    def status(self, program_id: uuid.UUID) -> BudgetStatus:
        """Current standing for one programme."""
        program = self._session.get(Program, program_id)
        if program is None:
            raise KeyError(f"no such programme: {program_id}")

        # Summed in Python, deliberately: SQLite's sum() over the text column
        # Money uses would coerce through binary floating point, which is the
        # representation Money exists to avoid.
        amounts = self._session.scalars(
            sa.select(CostEntry.usd).where(CostEntry.program_id == program_id)
        )
        return BudgetStatus(
            program_id=program_id,
            budget_usd=Decimal(program.budget_usd),
            spent_usd=sum(amounts, Decimal(0)),
        )

    def institution_status(self, lab_id: uuid.UUID, cap_usd: Decimal) -> BudgetStatus:
        """What a whole laboratory has spent, across every programme it owns.

        The cap is supplied rather than stored: there is no institutional
        budget column, and inventing one here would put a number in the
        database that no policy chose.
        """
        amounts = self._session.scalars(
            sa.select(CostEntry.usd)
            .join(Program, Program.program_id == CostEntry.program_id)
            .where(Program.lab_id == lab_id)
        )
        return BudgetStatus(
            program_id=lab_id, budget_usd=cap_usd, spent_usd=sum(amounts, Decimal(0))
        )

    def hypothesis_spend(self, hypothesis_id: uuid.UUID) -> Decimal:
        """Everything charged to one hypothesis, by both routes it can be charged.

        Model calls arrive through ``cost → task``, where the task's subject is
        either the hypothesis itself or a registration of it. Compute arrives
        through ``cost → run → registration``. Both are unioned on cost id, so
        an entry reachable by both routes is counted once.
        """
        registrations = sa.select(Registration.registration_id).where(
            Registration.hypothesis_id == hypothesis_id
        )
        by_task = (
            sa.select(CostEntry.cost_id, CostEntry.usd)
            .join(Task, Task.task_id == CostEntry.task_id)
            .where(
                sa.or_(
                    Task.subject_id == hypothesis_id,
                    Task.subject_id.in_(registrations),
                )
            )
        )
        by_run = (
            sa.select(CostEntry.cost_id, CostEntry.usd)
            .join(Run, Run.run_id == CostEntry.run_id)
            .where(Run.registration_id.in_(registrations))
        )
        rows = self._session.execute(by_task.union(by_run)).all()
        return sum((Decimal(usd) for _cost_id, usd in rows), Decimal(0))

    def rule(
        self,
        program_id: uuid.UUID,
        allowance_usd: Decimal,
        envelope: BudgetEnvelope | None = None,
    ) -> DispatchRuling:
        """Check an allowance against every level that has a cap.

        Outermost first, so the refusal names the widest constraint that binds
        — the one an operator most needs to know about, and the one that would
        otherwise be masked by a narrower cap failing first.
        """
        envelope = envelope or BudgetEnvelope()

        if envelope.lab_id is not None and envelope.lab_cap_usd is not None:
            institution = self.institution_status(envelope.lab_id, envelope.lab_cap_usd)
            if not institution.can_afford(allowance_usd):
                return DispatchRuling(
                    allowed=False,
                    level=BudgetLevel.INSTITUTION,
                    reason=(
                        f"allowance ${allowance_usd:.4f} exceeds the institution's remaining "
                        f"${institution.remaining_usd:.4f} of ${institution.budget_usd:.2f}"
                    ),
                )

        program = self.status(program_id)
        if not program.can_afford(allowance_usd):
            return DispatchRuling(
                allowed=False,
                level=BudgetLevel.PROGRAM,
                reason=(
                    f"allowance ${allowance_usd:.4f} exceeds remaining "
                    f"${program.remaining_usd:.4f} of ${program.budget_usd:.2f}"
                ),
            )

        if envelope.hypothesis_id is not None and envelope.hypothesis_cap_usd is not None:
            spent = self.hypothesis_spend(envelope.hypothesis_id)
            remaining = envelope.hypothesis_cap_usd - spent
            if allowance_usd > remaining:
                return DispatchRuling(
                    allowed=False,
                    level=BudgetLevel.HYPOTHESIS,
                    reason=(
                        f"allowance ${allowance_usd:.4f} exceeds the hypothesis's remaining "
                        f"${remaining:.4f} of ${envelope.hypothesis_cap_usd:.2f}, though the "
                        f"programme has ${program.remaining_usd:.4f} left"
                    ),
                )

        return DispatchRuling(allowed=True)

    def record_llm_cost(
        self,
        *,
        program_id: uuid.UUID,
        task_id: uuid.UUID | None,
        usage: Usage,
        usd: Decimal,
        cache_hit: bool,
    ) -> CostEntry:
        """Record what a model call cost.

        Cache hits are recorded too, at zero, rather than omitted: the count of
        free calls is exactly what makes the replay argument checkable.
        """
        return self._repo.record_cost(
            program_id=program_id,
            task_id=task_id,
            usd=usd,
            price_table_version=PRICE_TABLE_VERSION,
            llm_input_tokens=usage.input_tokens,
            llm_output_tokens=usage.output_tokens,
            llm_cached_tokens=usage.cache_read_input_tokens if cache_hit else 0,
        )

    def record_compute_cost(
        self,
        *,
        program_id: uuid.UUID,
        run_id: uuid.UUID,
        cpu_seconds: float,
        storage_mb: float,
        usd: Decimal,
    ) -> CostEntry:
        """Record what an experiment run cost."""
        return self._repo.record_cost(
            program_id=program_id,
            run_id=run_id,
            usd=usd,
            price_table_version=PRICE_TABLE_VERSION,
            cpu_seconds=cpu_seconds,
            storage_mb=storage_mb,
        )
