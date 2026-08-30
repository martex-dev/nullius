"""The task queue.

Tasks live in the database, in the same transaction as the ledger. That is
worth one service's worth of inconvenience: a task transitioning to
``succeeded`` and the events describing what it produced must commit together
or not at all, and a separate broker cannot promise that.

Dispatch is where budgets bite. :meth:`TaskQueue.enqueue` refuses a task whose
allowance the programme cannot afford, records a ``task.refused_budget`` event,
and returns the refused task rather than raising — budget exhaustion is a
research outcome, not a crash.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from nullius.db.enums import Role
from nullius.db.rows import entity_row
from nullius.db.tables import Task
from nullius.repository import Repository
from nullius.runtime.budget import BudgetLedger
from nullius.runtime.contracts import AgentTask, TaskStatus

__all__ = ["TaskQueue"]


class TaskQueue:
    """Durable, database-backed queue of agent tasks."""

    __slots__ = ("_budget", "_repo")

    def __init__(self, repo: Repository) -> None:
        self._repo = repo
        self._budget = BudgetLedger(repo)

    # ------------------------------------------------------------- dispatch

    def enqueue(
        self,
        *,
        program_id: uuid.UUID,
        role: Role,
        contract_version: str,
        subject_type: str,
        subject_id: uuid.UUID,
        allowance_usd: Decimal,
        view: dict[str, Any],
    ) -> Task:
        """Queue a task, or refuse it for want of budget.

        The affordability check happens before the task is dispatched, so a
        programme cannot overspend by discovering the limit halfway through a
        model call.
        """
        status = self._budget.status(program_id)
        now = self._repo.clock.now()

        affordable = status.can_afford(allowance_usd)
        task = Task(
            task_id=self._repo.ids.new(),
            program_id=program_id,
            role=role,
            contract_version=contract_version,
            subject_type=subject_type,
            subject_id=subject_id,
            status=(TaskStatus.PENDING if affordable else TaskStatus.REFUSED_BUDGET).value,
            allowance_usd=allowance_usd,
            view=view,
            result=None,
            failure_reason=(
                None
                if affordable
                else (
                    f"allowance ${allowance_usd:.4f} exceeds remaining "
                    f"${status.remaining_usd:.4f} of ${status.budget_usd:.2f}"
                )
            ),
            calls=0,
            spent_usd=Decimal(0),
            created_at=now,
            claimed_at=None,
            finished_at=None,
        )
        self._repo.session.add(task)
        self._repo.session.flush()

        self._repo.ledger.append(
            event_type="task.enqueued" if affordable else "task.refused_budget",
            subject_type="tasks",
            subject_id=task.task_id,
            actor_role=Role.SYSTEM,
            program_id=program_id,
            payload={
                "entity": "tasks",
                "pk": str(task.task_id),
                "row": entity_row(task)[2],
                "budget": {
                    "remaining_usd": str(status.remaining_usd),
                    "allowance_usd": str(allowance_usd),
                },
            },
        )
        return task

    # ---------------------------------------------------------------- claim

    def claim(self, role: Role | None = None) -> AgentTask | None:
        """Take the oldest pending task, or ``None`` if there is nothing to do.

        Uses ``SKIP LOCKED`` where the backend has it, so several workers can
        share a queue without claiming the same task twice.
        """
        query = (
            sa.select(Task)
            .where(Task.status == TaskStatus.PENDING.value)
            .order_by(Task.created_at.asc(), Task.task_id.asc())
            .limit(1)
        )
        if role is not None:
            query = query.where(Task.role == role)
        if self._repo.session.get_bind().dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)

        task = self._repo.session.scalars(query).one_or_none()
        if task is None:
            return None

        task.status = TaskStatus.RUNNING.value
        task.claimed_at = self._repo.clock.now()
        self._repo.session.flush()
        return to_agent_task(task)

    # --------------------------------------------------------------- finish

    def complete(
        self,
        task_id: uuid.UUID,
        *,
        status: TaskStatus,
        result: dict[str, Any] | None,
        spent_usd: Decimal,
        calls: int,
        failure_reason: str | None = None,
    ) -> Task:
        """Record a task's outcome and append the event describing it."""
        task = self._repo.session.get(Task, task_id)
        if task is None:
            raise KeyError(f"no such task: {task_id}")

        task.status = status.value
        task.result = result
        task.spent_usd = spent_usd
        task.calls = calls
        task.failure_reason = failure_reason
        task.finished_at = self._repo.clock.now()
        self._repo.session.flush()

        self._repo.ledger.append(
            event_type=f"task.{status.value}",
            subject_type="tasks",
            subject_id=task.task_id,
            actor_role=task.role,
            program_id=task.program_id,
            actor_task_id=task.task_id,
            payload={"entity": "tasks", "pk": str(task.task_id), "row": entity_row(task)[2]},
        )
        return task

    # ----------------------------------------------------------------- read

    def pending_count(self, program_id: uuid.UUID | None = None) -> int:
        query = (
            sa.select(sa.func.count())
            .select_from(Task)
            .where(Task.status == TaskStatus.PENDING.value)
        )
        if program_id is not None:
            query = query.where(Task.program_id == program_id)
        return int(self._repo.session.scalar(query) or 0)


def to_agent_task(task: Task) -> AgentTask:
    """Convert a stored row into the value an agent runtime consumes."""
    return AgentTask(
        task_id=task.task_id,
        program_id=task.program_id,
        role=task.role,
        contract_version=task.contract_version,
        subject_type=task.subject_type,
        subject_id=task.subject_id,
        allowance_usd=Decimal(task.allowance_usd),
        view=dict(task.view or {}),
    )
