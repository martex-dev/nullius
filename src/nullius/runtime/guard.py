"""The global spend guard.

Hierarchical budgets stop a *programme* overspending. They do not stop a
misconfigured loop from opening a hundred programmes, and they have no notion
of a day. This does.

Three independent ceilings, each of which halts the institution on its own:

``daily_usd``
    Wall-clock spend since midnight UTC. The one that protects real money
    when something is wrong in a way nobody anticipated.
``project_usd``
    Everything ever spent by this installation.
``calls_per_hour``
    A rate limit, which catches a runaway loop long before either dollar
    ceiling would.

Exhaustion is not an exception here either. It returns a refusal the caller
records, in keeping with `docs/02-architecture.md` §7 — the institution
stopping because it ran out of money is a fact about the research, and the
ledger should say so.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from nullius.db.tables import CostEntry, LlmCall
from nullius.util.clock import Clock, SystemClock

__all__ = ["SpendGuard", "SpendLimits", "SpendVerdict"]


@dataclass(frozen=True, slots=True)
class SpendLimits:
    """Ceilings for the whole installation.

    The defaults are deliberately small. A limit that has never stopped
    anything has not been tested, and the cost of discovering that is a
    surprise on a bill.
    """

    daily_usd: Decimal = Decimal("5.00")
    project_usd: Decimal = Decimal("100.00")
    calls_per_hour: int = 200


@dataclass(frozen=True, slots=True)
class SpendVerdict:
    """Whether work may proceed, and what stopped it if not."""

    allowed: bool
    reason: str | None = None
    daily_usd: Decimal = Decimal(0)
    project_usd: Decimal = Decimal(0)
    calls_last_hour: int = 0

    def __str__(self) -> str:
        if self.allowed:
            return (
                f"within limits: ${self.daily_usd:.4f} today, "
                f"${self.project_usd:.4f} total, {self.calls_last_hour} calls this hour"
            )
        return f"HALTED: {self.reason}"


class SpendGuard:
    """Checks the installation-wide ceilings before work is dispatched."""

    __slots__ = ("_clock", "_limits", "_session")

    def __init__(
        self,
        session: Session,
        limits: SpendLimits | None = None,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._session = session
        self._limits = limits or SpendLimits()
        self._clock = clock or SystemClock()

    def check(self, prospective_usd: Decimal = Decimal(0)) -> SpendVerdict:
        """Whether ``prospective_usd`` more spending is permitted."""
        now = self._clock.now()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

        daily = self._spend_since(midnight)
        total = self._spend_since(dt.datetime.min.replace(tzinfo=dt.UTC))
        calls = self._calls_since(now - dt.timedelta(hours=1))

        if daily + prospective_usd > self._limits.daily_usd:
            return SpendVerdict(
                False,
                f"daily ceiling reached: ${daily:.4f} spent today of ${self._limits.daily_usd:.2f}",
                daily,
                total,
                calls,
            )
        if total + prospective_usd > self._limits.project_usd:
            return SpendVerdict(
                False,
                f"project ceiling reached: ${total:.4f} of ${self._limits.project_usd:.2f}",
                daily,
                total,
                calls,
            )
        if calls >= self._limits.calls_per_hour:
            return SpendVerdict(
                False,
                f"rate ceiling reached: {calls} model calls in the last hour, "
                f"limit {self._limits.calls_per_hour}",
                daily,
                total,
                calls,
            )
        return SpendVerdict(True, None, daily, total, calls)

    def _spend_since(self, since: dt.datetime) -> Decimal:
        # Summed in Python: SQLite would coerce the text column through binary
        # floating point, which is what Money exists to avoid.
        amounts = self._session.scalars(
            sa.select(CostEntry.usd).where(CostEntry.created_at >= since)
        )
        return sum(amounts, Decimal(0))

    def _calls_since(self, since: dt.datetime) -> int:
        total = self._session.scalar(
            sa.select(sa.func.count())
            .select_from(LlmCall)
            .where(LlmCall.created_at >= since, LlmCall.cache_hit.is_(False))
        )
        return int(total or 0)
