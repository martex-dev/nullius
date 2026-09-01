"""The paper: the project's own results, enumerated rather than written.

Every registered protocol appears in order with its prediction and its outcome,
including the ones that were refuted and the results that were later retracted.
The document cannot select a flattering subset because it does not choose one —
it reads the committed protocols and results and prints what it finds.
"""

from __future__ import annotations

from nullius.paper.model import BankProfile, Chapter, Paper, assemble, bank_profile
from nullius.paper.render import (
    FLAWS,
    LIMITATIONS,
    Flaw,
    environment,
    render_findings,
    write_findings,
    write_paper,
)

__all__ = [
    "FLAWS",
    "LIMITATIONS",
    "BankProfile",
    "Chapter",
    "Flaw",
    "Paper",
    "assemble",
    "bank_profile",
    "environment",
    "render_findings",
    "write_findings",
    "write_paper",
]
