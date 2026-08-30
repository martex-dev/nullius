"""The cost ledger and hierarchical budgets.

Budgets are hard and denominated in real money. A task whose allowance exceeds
what its programme has left is refused *at dispatch* — before a model is
called, not after the money is spent — and the refusal is recorded as an event
rather than raised as an error.

That last part is a deliberate design choice from `docs/02-architecture.md`
§7: running out of budget is a legitimate terminal state for a line of
research, not a crash. An institution that cannot afford to answer a question
has learned something about the question's cost, and the ledger should say so.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa

from nullius.db.tables import CostEntry, Program
from nullius.llm.pricing import PRICE_TABLE_VERSION
from nullius.llm.types import Usage
from nullius.repository import Repository

__all__ = ["BudgetLedger", "BudgetStatus"]


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
