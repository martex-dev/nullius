"""Time, as an injected dependency.

The preregistration invariant is a claim about ordering: the registration hash
existed *before* the run started. That claim is only as trustworthy as the
clock behind it, and it is untestable if the clock is ``datetime.now``.

So time enters Nullius through this interface. Production uses
:class:`SystemClock`; tests and replays use :class:`FrozenClock`, which also
lets an invariant test construct the exact adversarial case — a run whose
start precedes its own registration — that must be refused.
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol, runtime_checkable

__all__ = ["Clock", "FrozenClock", "SystemClock"]


@runtime_checkable
class Clock(Protocol):
    """Source of timezone-aware UTC instants."""

    def now(self) -> dt.datetime:
        """Return the current instant, always timezone-aware and in UTC."""
        ...


class SystemClock:
    """Wall-clock time. The only clock permitted for a live research program."""

    __slots__ = ()

    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)


class FrozenClock:
    """A clock the caller advances by hand.

    Not merely a test double: replaying a recorded program needs the recorded
    instants back, not today's.
    """

    __slots__ = ("_now",)

    def __init__(self, start: dt.datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware start instant")
        self._now = start.astimezone(dt.UTC)

    def now(self) -> dt.datetime:
        return self._now

    def advance(self, seconds: float) -> dt.datetime:
        """Move the clock forward and return the new instant."""
        if seconds < 0:
            raise ValueError("time does not run backwards in the ledger")
        self._now += dt.timedelta(seconds=seconds)
        return self._now

    def set(self, instant: dt.datetime) -> dt.datetime:
        """Set the clock absolutely.

        Permitted to move backwards: an invariant test must be able to build
        the out-of-order case that the ledger has to refuse.
        """
        if instant.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware instant")
        self._now = instant.astimezone(dt.UTC)
        return self._now
