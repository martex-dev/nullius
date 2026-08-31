"""Observability: a static report over the event ledger.

The MVP shape `docs/01-critique.md` §24 argued for — a generated directory of
HTML rather than a served dashboard, because a read model that can be committed
beside the results it describes is worth more to an institution arguing that
its record survives inspection than one that has to be running to be read.

:mod:`~nullius.report.model` assembles what the ledger supports and re-derives
each claim's confidence from it rather than displaying the stored value.
:mod:`~nullius.report.render` turns that into pages.
"""

from __future__ import annotations

from nullius.report.model import (
    ClaimDossier,
    ClaimSummary,
    Overview,
    ProgramSummary,
    build_dossier,
    build_overview,
    claim_ids,
)
from nullius.report.render import Site, environment, write_site

__all__ = [
    "ClaimDossier",
    "ClaimSummary",
    "Overview",
    "ProgramSummary",
    "Site",
    "build_dossier",
    "build_overview",
    "claim_ids",
    "environment",
    "write_site",
]
