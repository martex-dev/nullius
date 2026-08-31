"""The read model: what the ledger supports, assembled for a reader.

A report is the place a project lies to itself. It is written last, read by
people who will not check it, and every incentive points towards a page that
looks more settled than the evidence behind it. Three rules keep this one
honest.

**It re-derives rather than displays.** A claim's confidence is not read out of
the ``claims`` row and printed. It is recomputed here from the same ledger
facts :func:`~nullius.analysis.confidence.compute_confidence` consumes —
replication count, interval width, seed variance, open objections,
preregistration, holdout queries, provenance, seed count — and then compared
against the stored value. When they disagree, the dossier says so and the page
shows both. That is only possible because none of those inputs is an opinion,
which was the point of designing them that way in M5.

**It reports what is missing.** A claim whose evidence does not resolve in the
artifact store is not quietly rendered without its provenance section; it is
rendered with the gap named. :attr:`ClaimDossier.problems` is the list of
things a reader should not have to notice for themselves.

**It computes nothing new.** Every number here comes from a row or from a
function the rest of the system already uses. The report has no statistics of
its own, because a figure that exists only in the report is a figure nobody
tested.
"""

from __future__ import annotations

import statistics
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from nullius.analysis.confidence import ConfidenceInputs, ConfidenceReport, compute_confidence
from nullius.db.enums import (
    ClaimConfidence,
    ObjectionSeverity,
    ObjectionStatus,
    ReplicationOutcome,
    Role,
    Split,
)
from nullius.db.tables import (
    Claim,
    CostEntry,
    Evidence,
    Forecast,
    ForecastScore,
    HoldoutQuery,
    Hypothesis,
    Lab,
    Objection,
    ObjectionResolution,
    Program,
    Registration,
    Replication,
    ResearchQuestion,
    Run,
    RunResult,
)
from nullius.ledger.rebuild import Reconciliation, reconciliation
from nullius.store.cas import ContentStore

__all__ = [
    "ClaimDossier",
    "ClaimSummary",
    "Overview",
    "ProgramSummary",
    "build_dossier",
    "build_overview",
    "claim_ids",
]


# --------------------------------------------------------------------- claims


@dataclass(frozen=True, slots=True)
class ClaimSummary:
    """One line in an index. Enough to decide whether to open it."""

    claim_id: uuid.UUID
    statement: str
    confidence: ClaimConfidence
    hypothesis_id: uuid.UUID | None
    program_id: uuid.UUID
    n_evidence: int
    open_critical: int
    confidence_disputed: bool
    """The stored level differs from what the ledger now supports."""


@dataclass(frozen=True, slots=True)
class ClaimDossier:
    """Everything behind one claim, and everything wrong with it.

    Assembled so that a reader can answer *why does the system believe this?*
    without leaving the page: the hypothesis it answers, the design that was
    locked before it ran, the seeds that executed, the numbers the verdict was
    computed from, who objected, what replicated, and how the forecasts that
    were locked beforehand actually scored.
    """

    claim: Claim
    hypothesis: Hypothesis | None
    registration: Registration | None
    runs: tuple[Run, ...]
    evidence: tuple[Evidence, ...]
    objections: tuple[tuple[Objection, ObjectionStatus], ...]
    forecasts: tuple[tuple[Forecast, ForecastScore | None], ...]
    replications: tuple[Replication, ...]
    analysis: dict[str, Any]
    """The paired result the verdict came from, as stored on the evidence."""

    recomputed: ConfidenceReport
    stored: ClaimConfidence
    holdout_queries: int
    unresolved_artifacts: tuple[str, ...]
    usd: Decimal
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def confidence_disputed(self) -> bool:
        """Whether the ledger still supports the level the claim carries."""
        return self.recomputed.confidence is not self.stored

    @property
    def ancestry(self) -> tuple[Hypothesis, ...]:
        return ()


def claim_ids(session: Session) -> list[uuid.UUID]:
    """Every claim, newest first."""
    return list(session.scalars(sa.select(Claim.claim_id).order_by(Claim.computed_at.desc())))


def _open_critical(session: Session, *target_ids: uuid.UUID | None) -> list[Objection]:
    """Unresolved critical objections against any of these targets.

    Mirrors :meth:`~nullius.repository.Repository.open_critical_objections`,
    including its definition of *open* as "no resolution row exists", but takes
    several targets because an objection may be filed against the registration
    rather than the claim — which is where the detectors file theirs.
    """
    targets = [t for t in target_ids if t is not None]
    if not targets:
        return []
    resolved = sa.select(ObjectionResolution.objection_id).where(
        ObjectionResolution.status != ObjectionStatus.OPEN
    )
    return list(
        session.scalars(
            sa.select(Objection).where(
                Objection.target_id.in_(targets),
                Objection.severity == ObjectionSeverity.CRITICAL,
                Objection.objection_id.not_in(resolved),
            )
        )
    )


def _objection_status(session: Session, objection: Objection) -> ObjectionStatus:
    latest = session.scalars(
        sa.select(ObjectionResolution)
        .where(ObjectionResolution.objection_id == objection.objection_id)
        .order_by(ObjectionResolution.resolved_at.desc())
    ).first()
    return latest.status if latest is not None else ObjectionStatus.OPEN


def _baseline_sd(session: Session, registration_id: uuid.UUID, spec: dict[str, Any]) -> float:
    """Run-to-run spread of the baseline arm on the evaluation split.

    Recomputed from the recorded per-seed results rather than stored, because
    the confidence rubric's ``seed_variance_ratio`` is meant to be a fact about
    the runs. If it were carried alongside the claim, a claim could be written
    with a flattering one.
    """
    arm = str(spec.get("baseline_arm", ""))
    metric = str(spec.get("primary_metric", ""))
    if not arm or not metric:
        return 0.0
    values = list(
        session.scalars(
            sa.select(RunResult.value)
            .join(Run, Run.run_id == RunResult.run_id)
            .where(
                Run.registration_id == registration_id,
                RunResult.split == Split.HOLDOUT,
                RunResult.metric == f"{arm}.{metric}",
            )
        )
    )
    if len(values) < 2:
        return 0.0
    return float(statistics.stdev(float(v) for v in values))


def build_dossier(
    session: Session, claim_id: uuid.UUID, *, store: ContentStore | None = None
) -> ClaimDossier:
    """Assemble one claim's case, and re-derive its confidence from the ledger."""
    claim = session.get(Claim, claim_id)
    if claim is None:
        raise KeyError(f"no such claim: {claim_id}")

    problems: list[str] = []
    hypothesis = (
        session.get(Hypothesis, claim.hypothesis_id) if claim.hypothesis_id is not None else None
    )
    if hypothesis is None:
        problems.append("this claim names no hypothesis, so it answers no stated question")

    registration: Registration | None = None
    if hypothesis is not None:
        registration = session.scalars(
            sa.select(Registration)
            .where(Registration.hypothesis_id == hypothesis.hypothesis_id)
            .order_by(Registration.registered_at.asc())
        ).first()
    if registration is None:
        problems.append("no registration: nothing was locked before this ran")

    evidence = tuple(session.scalars(sa.select(Evidence).where(Evidence.claim_id == claim_id)))
    if not evidence:
        problems.append("no evidence rows: this claim rests on nothing recorded")

    analysis: dict[str, Any] = {}
    for row in evidence:
        if isinstance(row.strength, dict) and "difference" in row.strength:
            analysis = dict(row.strength)
            break

    runs: tuple[Run, ...] = ()
    holdout_queries = 0
    if registration is not None:
        runs = tuple(
            session.scalars(
                sa.select(Run)
                .where(Run.registration_id == registration.registration_id)
                .order_by(Run.seed.asc())
            )
        )
        holdout_queries = int(
            session.scalar(
                sa.select(sa.func.count())
                .select_from(HoldoutQuery)
                .where(HoldoutQuery.registration_id == registration.registration_id)
            )
            or 0
        )

    # Provenance: every artifact hash on the evidence chain must resolve.
    unresolved: list[str] = []
    if store is not None and runs:
        hashes = set(
            session.scalars(
                sa.select(RunResult.artifact_hash).where(
                    RunResult.run_id.in_([r.run_id for r in runs]),
                    RunResult.artifact_hash.is_not(None),
                )
            )
        )
        unresolved = sorted(h for h in hashes if h and not store.exists(h))
        if unresolved:
            problems.append(
                f"{len(unresolved)} artifact hash(es) on the evidence chain do not "
                "resolve in the store"
            )

    objections = tuple(
        (row, _objection_status(session, row))
        for row in session.scalars(
            sa.select(Objection).where(
                Objection.target_id.in_(
                    [
                        i
                        for i in (
                            claim_id,
                            registration.registration_id if registration else None,
                            hypothesis.hypothesis_id if hypothesis else None,
                        )
                        if i is not None
                    ]
                )
            )
        )
    )

    forecasts: tuple[tuple[Forecast, ForecastScore | None], ...] = ()
    replications: tuple[Replication, ...] = ()
    if registration is not None:
        scores = {
            row.forecast_id: row
            for row in session.scalars(
                sa.select(ForecastScore).where(
                    ForecastScore.registration_id == registration.registration_id
                )
            )
        }
        forecasts = tuple(
            (row, scores.get(row.forecast_id))
            for row in session.scalars(
                sa.select(Forecast).where(Forecast.registration_id == registration.registration_id)
            )
        )
        replications = tuple(
            session.scalars(
                sa.select(Replication).where(
                    Replication.original_registration_id == registration.registration_id
                )
            )
        )

    agreed = sum(
        1
        for r in replications
        if r.outcome is ReplicationOutcome.REPLICATED and r.executed_by is Role.REPLICATOR
    )

    width = abs(float(analysis.get("ci_high", 0.0)) - float(analysis.get("ci_low", 0.0)))
    difference = abs(float(analysis.get("difference", 0.0)))
    sd = (
        _baseline_sd(session, registration.registration_id, registration.spec)
        if registration is not None and isinstance(registration.spec, dict)
        else 0.0
    )

    recomputed = compute_confidence(
        ConfidenceInputs(
            independent_replications=agreed,
            effect_to_interval_ratio=(difference / width) if width else 0.0,
            seed_variance_ratio=(difference / sd) if sd else 0.0,
            open_critical_objections=len(
                _open_critical(
                    session,
                    claim_id,
                    registration.registration_id if registration else None,
                )
            ),
            preregistered=bool(registration is not None and registration.locked),
            holdout_queries_consumed=holdout_queries,
            provenance_complete=not unresolved,
            n_seeds=int(analysis.get("n_seeds", len(runs))),
        )
    )
    if recomputed.confidence is not claim.confidence:
        problems.append(
            f"the ledger supports {recomputed.confidence.value!r}, and this claim "
            f"carries {claim.confidence.value!r}"
        )

    usd = _program_usd(session, claim.program_id)

    return ClaimDossier(
        claim=claim,
        hypothesis=hypothesis,
        registration=registration,
        runs=runs,
        evidence=evidence,
        objections=objections,
        forecasts=forecasts,
        replications=replications,
        analysis=analysis,
        recomputed=recomputed,
        stored=claim.confidence,
        holdout_queries=holdout_queries,
        unresolved_artifacts=tuple(unresolved),
        usd=usd,
        problems=tuple(problems),
    )


def _program_usd(session: Session, program_id: uuid.UUID) -> Decimal:
    """Summed in Python over Decimal.

    SQLite stores this project's money as text, and ``sum()`` over a text
    column coerces through binary float — which is how a ledger and its own
    total come to disagree in the last decimal place.
    """
    rows = session.scalars(sa.select(CostEntry.usd).where(CostEntry.program_id == program_id))
    return sum((Decimal(value or 0) for value in rows), Decimal(0))


# ------------------------------------------------------------------- overview


@dataclass(frozen=True, slots=True)
class ProgramSummary:
    """One research question, and what became of it."""

    program_id: uuid.UUID
    lab_name: str
    question: str
    n_hypotheses: int
    n_claims: int
    usd: Decimal


@dataclass(frozen=True, slots=True)
class Overview:
    """The front page: what exists, what it cost, and whether it holds together."""

    labs: tuple[Lab, ...]
    programs: tuple[ProgramSummary, ...]
    claims: tuple[ClaimSummary, ...]
    n_registrations: int
    n_runs: int
    n_events: int
    usd: Decimal
    chain_ok: bool
    chain_detail: str
    reconciliation: Reconciliation

    @property
    def disputed_claims(self) -> tuple[ClaimSummary, ...]:
        """Claims whose stored confidence the ledger no longer supports.

        Surfaced on the front page rather than buried, because a report that
        knows a claim is overstated and does not lead with it is a worse
        artifact than one that never checked.
        """
        return tuple(c for c in self.claims if c.confidence_disputed)

    @property
    def integrity_ok(self) -> bool:
        return self.chain_ok and self.reconciliation.ok


def build_overview(
    session: Session, *, store: ContentStore | None = None, ledger: Any | None = None
) -> Overview:
    """Assemble the front page, including whether the ledger still verifies."""
    from nullius.db.tables import Event

    labs = tuple(session.scalars(sa.select(Lab).order_by(Lab.created_at.asc())))

    programs: list[ProgramSummary] = []
    for program, lab, question in session.execute(
        sa.select(Program, Lab, ResearchQuestion.text)
        .join(Lab, Lab.lab_id == Program.lab_id)
        .join(ResearchQuestion, ResearchQuestion.rq_id == Program.rq_id)
    ).all():
        programs.append(
            ProgramSummary(
                program_id=program.program_id,
                lab_name=lab.name,
                question=str(question or ""),
                n_hypotheses=_count(session, Hypothesis, Hypothesis.program_id, program.program_id),
                n_claims=_count(session, Claim, Claim.program_id, program.program_id),
                usd=_program_usd(session, program.program_id),
            )
        )

    claims: list[ClaimSummary] = []
    for claim in session.scalars(sa.select(Claim).order_by(Claim.computed_at.desc())):
        dossier = build_dossier(session, claim.claim_id, store=store)
        claims.append(
            ClaimSummary(
                claim_id=claim.claim_id,
                statement=claim.statement,
                confidence=claim.confidence,
                hypothesis_id=claim.hypothesis_id,
                program_id=claim.program_id,
                n_evidence=len(dossier.evidence),
                open_critical=len(
                    _open_critical(
                        session,
                        claim.claim_id,
                        dossier.registration.registration_id if dossier.registration else None,
                    )
                ),
                confidence_disputed=dossier.confidence_disputed,
            )
        )

    chain_ok, chain_detail = True, "not checked"
    if ledger is not None:
        verification = ledger.verify()
        chain_ok, chain_detail = bool(verification.ok), str(verification)

    return Overview(
        labs=labs,
        programs=tuple(programs),
        claims=tuple(claims),
        n_registrations=int(
            session.scalar(sa.select(sa.func.count()).select_from(Registration)) or 0
        ),
        n_runs=int(session.scalar(sa.select(sa.func.count()).select_from(Run)) or 0),
        n_events=int(session.scalar(sa.select(sa.func.count()).select_from(Event)) or 0),
        usd=sum(
            (p.usd for p in programs),
            Decimal(0),
        ),
        chain_ok=chain_ok,
        chain_detail=chain_detail,
        reconciliation=reconciliation(session),
    )


def _count(session: Session, table: Any, column: Any, value: Any) -> int:
    return int(
        session.scalar(sa.select(sa.func.count()).select_from(table).where(column == value)) or 0
    )


def summarise(values: Sequence[float]) -> str:
    """A compact ``mean ± sd`` for a template. Formatting, not statistics."""
    if not values:
        return "—"
    if len(values) == 1:
        return f"{values[0]:.4f}"
    return f"{statistics.fmean(values):.4f} ± {statistics.stdev(values):.4f}"
