"""Exception hierarchy.

The distinction that matters here is between an *invariant violation* and an
ordinary error. An invariant violation means the system was asked to enter a
state that `docs/03-data-model.md` says must be unreachable — a result without
a prior registration, an agent-authored holdout number, a modified locked
registration. These are never retried, never swallowed, and never downgraded
to a warning. They indicate either a bug in Nullius or an attempt to launder
invalid science into the ledger, and both deserve a crash.
"""

from __future__ import annotations


class NulliusError(Exception):
    """Base class for everything this package raises deliberately."""


class InvariantViolation(NulliusError):
    """A scientific invariant would have been broken.

    Raised by the repository layer, and mirrored by database triggers so that
    the rule also holds against raw SQL. If you are tempted to catch this,
    fix the caller instead.
    """


class AuthorityError(NulliusError):
    """A role attempted an operation outside its authority.

    Institutional roles are separated by *capability*, not by instruction
    (`docs/01-critique.md` A5). This is the enforcement point.
    """


class IntegrityError(NulliusError):
    """Stored bytes do not match the digest they are filed under.

    Either the content-addressed store or the event hash chain has been
    modified out of band.
    """


class BudgetExceeded(NulliusError):
    """A task's cost allowance would be exceeded.

    Not a failure: `docs/02-architecture.md` §7 makes budget exhaustion a
    legitimate terminal research state. The caller records it as an event.
    """
