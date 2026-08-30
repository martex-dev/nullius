"""The question bank: research questions whose answers are knowable.

Ground truth is measured by :mod:`nullius.bank.oracle`, locked to a committed
file, and re-verified rather than trusted. Nothing in this package is ever
exposed to an agent view — see :meth:`nullius.bank.items.BankItem.agent_view`
for the only thing that is.
"""

from __future__ import annotations

from nullius.bank.items import BANK_V1, MDE, BankItem, validate_bank
from nullius.bank.lock import compute_truths, read_lock, verify, write_lock
from nullius.bank.oracle import measure_effect
from nullius.bank.truth import Truth, classify

__all__ = [
    "BANK_V1",
    "MDE",
    "BankItem",
    "Truth",
    "classify",
    "compute_truths",
    "measure_effect",
    "read_lock",
    "validate_bank",
    "verify",
    "write_lock",
]
