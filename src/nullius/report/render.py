"""Rendering the read model to static HTML.

Static files, not a server, and that is a decision rather than a shortcut
(`docs/01-critique.md` §24, ADR-lineage: static report first, FastAPI + HTMX at
Stage 6). A dashboard is a read model over an event log. Serving it needs a
process to be running at the moment somebody asks; a directory of files can be
opened from a clone, attached to an issue, committed beside the results it
describes, and diffed against the last one. For an institution whose whole
argument is that its record survives inspection, the artifact that travels is
worth more than the one that has to be hosted.

Nothing here decides anything. The templates read a :mod:`nullius.report.model`
dossier and render it; the model re-derives from the ledger. A number that
first appears at this layer would be a number nobody tested, so there are none.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape
from sqlalchemy.orm import Session

from nullius.knowledge.genealogy import ancestors
from nullius.report.model import build_dossier, build_overview, claim_ids
from nullius.store.cas import ContentStore

__all__ = ["Site", "environment", "write_site"]

#: Levels, worst to best, mapped to the three badge styles the stylesheet has.
#: A five-level rubric rendered in five colours reads as a spectrum where the
#: rubric means a ladder with two disqualifying rungs, so contested and
#: speculative share the alarming style and the rest do not.
_CONFIDENCE_CLASS = {
    "contested": "no",
    "speculative": "no",
    "suggestive": "mid",
    "supported": "ok",
    "well_supported": "ok",
}


def _confidence_class(level: str) -> str:
    return _CONFIDENCE_CLASS.get(level, "mid")


def _num(value: object) -> str:
    """Format a value for a table cell, without changing what it is.

    Floats are shown to four decimals. Sixteen significant figures on a ratio
    estimated from five seeds is not precision, it is noise wearing precision's
    clothes, and a reader who sees it learns to distrust every other number on
    the page. Booleans and integers pass through, because rounding those would
    be changing them.
    """
    if isinstance(value, bool | int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def environment() -> Environment:
    """The Jinja environment, with undefined names treated as errors.

    ``StrictUndefined`` because a report is exactly the artifact where a silent
    blank is dangerous: a template that quietly renders an empty cell for a
    field that no longer exists produces a page which looks complete and is
    not. A missing name should break the build, loudly, while somebody is
    watching.
    """
    env = Environment(
        loader=PackageLoader("nullius.report", "templates"),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["confidence_class"] = _confidence_class
    env.filters["num"] = _num
    return env


@dataclass(frozen=True, slots=True)
class Site:
    """What was written, and what the reader should be told about it."""

    root: Path
    index: Path
    claim_pages: tuple[Path, ...]
    disputed: int
    """Claims whose stored confidence the ledger no longer supports."""

    integrity_ok: bool

    def __str__(self) -> str:
        state = "intact" if self.integrity_ok else "COMPROMISED"
        disputed = (
            f", {self.disputed} claim(s) whose confidence the ledger does not support"
            if self.disputed
            else ""
        )
        return (
            f"{len(self.claim_pages)} claim page(s) written to {self.root}; "
            f"ledger {state}{disputed}"
        )


def write_site(
    session: Session,
    out: Path,
    *,
    database: Path | str,
    store: ContentStore | None = None,
    ledger: object | None = None,
    clean: bool = True,
) -> Site:
    """Render the whole report into ``out``.

    ``clean`` removes an existing site first, because a stale page for a claim
    that no longer exists is the specific failure this format invites: files
    persist, and a reader has no way to tell a current page from a leftover.
    """
    out = Path(out)
    if clean and out.exists():
        shutil.rmtree(out)
    (out / "claims").mkdir(parents=True, exist_ok=True)

    env = environment()
    overview = build_overview(session, store=store, ledger=ledger)

    index = out / "index.html"
    index.write_text(
        env.get_template("index.html").render(overview=overview, root="", database=str(database)),
        encoding="utf-8",
    )

    pages: list[Path] = []
    template = env.get_template("claim.html")
    for claim_id in claim_ids(session):
        dossier = build_dossier(session, claim_id, store=store)
        lineage = (
            ancestors(session, dossier.hypothesis.hypothesis_id)
            if dossier.hypothesis is not None
            else []
        )
        page = out / "claims" / f"{claim_id}.html"
        page.write_text(
            template.render(d=dossier, ancestry=lineage, root="../", database=str(database)),
            encoding="utf-8",
        )
        pages.append(page)

    return Site(
        root=out,
        index=index,
        claim_pages=tuple(pages),
        disputed=len(overview.disputed_claims),
        integrity_ok=overview.integrity_ok,
    )


def claim_page_for(out: Path, claim_id: uuid.UUID) -> Path:
    """Where a given claim's page lives, for callers that need the link."""
    return Path(out) / "claims" / f"{claim_id}.html"
