"""The schema.

A direct implementation of ``docs/03-data-model.md``, with one guiding rule:
**if a scientific norm can be a constraint, it is a constraint here rather
than a convention documented elsewhere.**

The constraints doing the most work, and what each makes impossible:

``run_results.ck_holdout_custodian_only``
    An agent-authored number about the test split cannot enter the database.
``registrations.spec_hash`` (unique, immutable once locked)
    A design cannot be silently edited after results are seen; a changed
    design is a new row, degraded to ``exploratory``.
``hypotheses`` NOT NULLs on ``falsification_condition`` / ``primary_metric`` /
``mde``
    "Attention probably improves performance" cannot be stored.
``evidence.ck_evidence_has_a_referent``
    Every evidence row points at a result, a source, or a parent claim.
``claims.ck_claim_is_not_speculation``
    A speculation cannot become a claim.

Triggers in :mod:`nullius.db.triggers` cover the rules that need to compare
rows (ordering, existence) rather than columns.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from nullius.db.enums import (
    AssertionKind,
    ClaimConfidence,
    ComputedBy,
    DerivationKind,
    EvidenceKind,
    HypothesisState,
    ObjectionSeverity,
    ObjectionStatus,
    ObjectionType,
    Polarity,
    RegistrationKind,
    ReplicationOutcome,
    ReviewDecision,
    Role,
    RunStatus,
    Split,
    Stance,
)


class UtcDateTime(sa.TypeDecorator[dt.datetime]):
    """Timezone-aware UTC instants, identically on both backends.

    SQLite's ``DATETIME`` discards ``tzinfo``, which would hand back naive
    datetimes that :func:`nullius.util.canonical.canonical_json` rightly
    refuses to hash. Rejecting naive values on the way *in* and restoring UTC
    on the way *out* keeps the ordering guarantee that the preregistration
    invariant depends on.
    """

    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect: object) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "naive datetime rejected: ledger ordering requires an absolute instant"
            )
        return value.astimezone(dt.UTC)

    def process_result_value(
        self, value: dt.datetime | None, dialect: object
    ) -> dt.datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


class Money(sa.TypeDecorator[Decimal]):
    """Exact decimal amounts, stored as text at a fixed scale.

    Costs are money, and binary floating point is the wrong representation for
    money; SQLite has no native decimal, so the exact digits are stored as
    text.

    The fixed scale is not cosmetic. ``Decimal("0") == Decimal("0.00")`` is
    True, so without normalisation SQLAlchemy's change detection sees no
    difference between a loaded value and an assigned one at a different
    scale, skips the UPDATE, and leaves the row holding the old digits while
    the in-memory object holds the new ones — at which point the ledger
    records a number the database does not have. Quantising on the way in
    makes representation and equality agree.
    """

    impl = sa.String(32)
    cache_ok = True

    #: 1e-8 dollars resolves per-token costs without floating point.
    SCALE = Decimal("0.00000001")

    def process_bind_param(self, value: Decimal | None, dialect: object) -> str | None:
        return None if value is None else str(Decimal(value).quantize(self.SCALE))

    def process_result_value(self, value: str | None, dialect: object) -> Decimal | None:
        return None if value is None else Decimal(value)


class Base(DeclarativeBase):
    """Declarative base with the type map both backends share."""

    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy API
        uuid.UUID: sa.Uuid(),
        dt.datetime: UtcDateTime(),
        dict[str, Any]: sa.JSON(),
        list[Any]: sa.JSON(),
        Decimal: Money(),
    }


def _enum(python_type: type, name: str) -> sa.Enum:
    """A portable enum column: VARCHAR plus a CHECK constraint on both backends.

    ``native_enum=False`` keeps SQLite and Postgres identical, and avoids
    Postgres enum types that need a migration to extend.

    ``values_callable`` is not optional. SQLAlchemy stores an enum's *name* by
    default, so ``Role.SYSTEM`` would be written as ``'SYSTEM'`` while every
    trigger, CHECK constraint and hashed payload in this project speaks in
    values (``'system'``). Storing names would leave the invariant SQL quietly
    matching nothing.
    """
    return sa.Enum(
        python_type,
        name=name,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda enum_class: [member.value for member in enum_class],
    )


#: SQLite only autoincrements a column declared exactly ``INTEGER PRIMARY KEY``;
#: a ``BIGINT`` primary key is left NULL. The variant keeps 64-bit width on
#: Postgres while remaining autoincrementable on SQLite.
_AUTO_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(sa.Uuid(), primary_key=True)


def _digest(nullable: bool = False) -> Mapped[str]:
    """A lowercase hex SHA-256, stored as text so it is greppable in dumps."""
    return mapped_column(sa.String(64), nullable=nullable)


# ===========================================================================
# The ledger spine
# ===========================================================================


class Event(Base):
    """Append-only, hash-chained record of everything that happened.

    All state is recoverable by folding this table (see
    :mod:`nullius.ledger.rebuild`). ``chain_hash`` binds each row to its
    predecessor, so an out-of-band edit to history is detectable by
    ``nullius ledger verify`` rather than merely discouraged.
    """

    __tablename__ = "events"

    seq: Mapped[int] = mapped_column(_AUTO_PK, primary_key=True, autoincrement=True)
    event_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), unique=True)
    occurred_at: Mapped[dt.datetime]
    program_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), index=True)
    actor_role: Mapped[Role] = mapped_column(_enum(Role, "role_t"))
    actor_task_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    event_type: Mapped[str] = mapped_column(sa.String(64), index=True)
    subject_type: Mapped[str] = mapped_column(sa.String(64))
    subject_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid())
    payload: Mapped[dict[str, Any]]
    payload_hash: Mapped[str] = _digest()
    prev_hash: Mapped[str | None] = mapped_column(sa.String(64))
    chain_hash: Mapped[str] = _digest()
    policy_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())

    __table_args__ = (
        sa.Index("ix_events_subject", "subject_type", "subject_id"),
        sa.Index("ix_events_program_seq", "program_id", "seq"),
    )


# ===========================================================================
# Institution
# ===========================================================================


class Lab(Base):
    """An artificial laboratory. Exactly one row until M12."""

    __tablename__ = "labs"

    lab_id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(sa.String(128), unique=True)
    charter: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[dt.datetime]


class Policy(Base):
    """A versioned institutional policy.

    The only surface the institution is permitted to modify about itself
    (`docs/01-critique.md` A9). Never prompts at large, never core code.
    """

    __tablename__ = "policies"

    policy_id: Mapped[uuid.UUID] = _uuid_pk()
    version: Mapped[str] = mapped_column(sa.String(32), unique=True)
    parent_version: Mapped[str | None] = mapped_column(sa.String(32))
    params: Mapped[dict[str, Any]]
    rationale: Mapped[str] = mapped_column(sa.Text)
    ab_test_registration_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("registrations.registration_id")
    )
    active: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    created_at: Mapped[dt.datetime]


class ResearchQuestion(Base):
    __tablename__ = "research_questions"

    rq_id: Mapped[uuid.UUID] = _uuid_pk()
    text: Mapped[str] = mapped_column(sa.Text)
    domain: Mapped[str] = mapped_column(sa.String(64))
    origin: Mapped[str] = mapped_column(sa.String(16))
    parent_claim_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    bank_item_id: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    """Links to a question-bank item when this RQ is being used for evaluation.

    The bank's *ground truth* is deliberately not reachable from here; see
    ``docs/04-evaluation.md`` and the isolation tests.
    """
    created_at: Mapped[dt.datetime]

    __table_args__ = (sa.CheckConstraint("origin IN ('human','derived')", name="ck_rq_origin"),)


class Program(Base):
    """A research programme: one question, one budget, one policy."""

    __tablename__ = "programs"

    program_id: Mapped[uuid.UUID] = _uuid_pk()
    rq_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("research_questions.rq_id"))
    lab_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("labs.lab_id"))
    policy_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("policies.policy_id"))
    budget_usd: Mapped[Decimal]
    status: Mapped[str] = mapped_column(sa.String(32))
    config_hash: Mapped[str] = _digest()
    """Hash of the full institutional configuration, so a program can be replayed."""
    capability_digest: Mapped[str] = _digest()
    """Which enforcement tiers were active (ADR-0001, ADR-0002)."""
    created_at: Mapped[dt.datetime]


# ===========================================================================
# Hypotheses and preregistration
# ===========================================================================


class Hypothesis(Base):
    """A falsifiable statement with a named metric and a stated effect size.

    The NOT NULL columns are the filter: a vague hypothesis cannot be stored,
    so it cannot be funded, built or claimed.
    """

    __tablename__ = "hypotheses"

    hypothesis_id: Mapped[uuid.UUID] = _uuid_pk()
    program_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("programs.program_id"), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("hypotheses.hypothesis_id"), index=True
    )
    derivation: Mapped[DerivationKind] = mapped_column(_enum(DerivationKind, "derivation_kind"))
    statement: Mapped[str] = mapped_column(sa.Text)
    mechanism: Mapped[str] = mapped_column(sa.Text)
    primary_metric: Mapped[str] = mapped_column(sa.String(64))
    direction: Mapped[str] = mapped_column(sa.String(16))
    mde: Mapped[float] = mapped_column(sa.Float)
    """Minimum detectable / claimed effect. Required: it is what falsifies."""
    falsification_condition: Mapped[str] = mapped_column(sa.Text)
    assumptions: Mapped[dict[str, Any]]
    state: Mapped[HypothesisState] = mapped_column(_enum(HypothesisState, "hypothesis_state"))
    novelty_embedding: Mapped[list[Any] | None] = mapped_column(sa.JSON, nullable=True)
    created_by_task: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    created_at: Mapped[dt.datetime]

    __table_args__ = (
        sa.CheckConstraint(
            "parent_id IS NOT NULL OR derivation = 'root'",
            name="ck_hypothesis_lineage",
        ),
        sa.CheckConstraint(
            "direction IN ('increase','decrease','no_change')",
            name="ck_hypothesis_direction",
        ),
        sa.CheckConstraint("mde >= 0", name="ck_hypothesis_mde_nonneg"),
    )


class Registration(Base):
    """A preregistration. The central invariant of the whole system.

    ``spec_hash`` is written before any executor is dispatched and is unique.
    Once ``locked``, the specification and analysis plan are immutable — a
    trigger enforces this — so a design cannot be revised after seeing
    results without creating a new, visibly ``exploratory`` row.
    """

    __tablename__ = "registrations"

    registration_id: Mapped[uuid.UUID] = _uuid_pk()
    hypothesis_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("hypotheses.hypothesis_id"), index=True
    )
    kind: Mapped[RegistrationKind] = mapped_column(_enum(RegistrationKind, "registration_kind"))
    parent_registration_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("registrations.registration_id")
    )
    spec: Mapped[dict[str, Any]]
    spec_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    analysis_plan: Mapped[dict[str, Any]]
    seed_root: Mapped[int] = mapped_column(sa.BigInteger)
    n_seeds: Mapped[int] = mapped_column(sa.Integer)
    holdout_query_budget: Mapped[int] = mapped_column(sa.Integer)
    registered_at: Mapped[dt.datetime]
    locked: Mapped[bool] = mapped_column(sa.Boolean, default=True)

    __table_args__ = (
        sa.CheckConstraint("n_seeds >= 1", name="ck_registration_seeds"),
        sa.CheckConstraint("holdout_query_budget >= 1", name="ck_registration_holdout_budget"),
        sa.CheckConstraint(
            "kind = 'confirmatory' OR parent_registration_id IS NOT NULL",
            name="ck_registration_derived_has_parent",
        ),
    )


class Forecast(Base):
    """A role's locked prediction, elicited *before* execution.

    Scored afterwards with a proper scoring rule. This one table yields
    calibration per role, the expected-information-gain inputs for the
    allocator, and a self-improvement signal that does not depend on any
    agent grading another (`docs/02-architecture.md` §7).
    """

    __tablename__ = "forecasts"

    forecast_id: Mapped[uuid.UUID] = _uuid_pk()
    registration_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("registrations.registration_id"), index=True
    )
    role: Mapped[Role] = mapped_column(_enum(Role, "role_t"))
    p_effect_exceeds_mde: Mapped[float] = mapped_column(sa.Float)
    predictive_mean: Mapped[float] = mapped_column(sa.Float)
    predictive_sd: Mapped[float] = mapped_column(sa.Float)
    p_execution_success: Mapped[float] = mapped_column(sa.Float)
    created_at: Mapped[dt.datetime]
    # Written once, after resolution.
    brier_score: Mapped[float | None] = mapped_column(sa.Float)
    crps: Mapped[float | None] = mapped_column(sa.Float)

    __table_args__ = (
        sa.UniqueConstraint("registration_id", "role", name="uq_forecast_one_per_role"),
        sa.CheckConstraint(
            "p_effect_exceeds_mde BETWEEN 0 AND 1 AND p_execution_success BETWEEN 0 AND 1",
            name="ck_forecast_probabilities",
        ),
        sa.CheckConstraint("predictive_sd > 0", name="ck_forecast_sd_positive"),
    )


# ===========================================================================
# Data, code, execution
# ===========================================================================


class Dataset(Base):
    """A dataset, identified by the hash of its content.

    ``generator_spec`` holds the structural causal model for synthetic items.
    Ground truth is *not* stored here — it lives in the bank, outside every
    role-scoped view (``docs/04-evaluation.md``).
    """

    __tablename__ = "datasets"

    dataset_id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(sa.String(128))
    version: Mapped[str] = mapped_column(sa.String(32))
    content_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    generator_spec: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    licence: Mapped[str] = mapped_column(sa.String(64))
    created_at: Mapped[dt.datetime]

    __table_args__ = (sa.UniqueConstraint("name", "version", name="uq_dataset_name_version"),)


class CodeBundle(Base):
    """Executable code plus the validator's verdict on it."""

    __tablename__ = "code_bundles"

    bundle_id: Mapped[uuid.UUID] = _uuid_pk()
    content_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    built_by_task: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    validator_report: Mapped[dict[str, Any]]
    passed: Mapped[bool] = mapped_column(sa.Boolean)
    created_at: Mapped[dt.datetime]


class Run(Base):
    """One execution of one seed of one registration.

    A trigger refuses insertion unless a *locked* registration already exists
    and was registered no later than the run's start. That is the mechanism
    behind "results cannot exist without a prior registration".
    """

    __tablename__ = "runs"

    run_id: Mapped[uuid.UUID] = _uuid_pk()
    registration_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("registrations.registration_id"), index=True
    )
    bundle_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("code_bundles.bundle_id"))
    dataset_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("datasets.dataset_id"))
    seed: Mapped[int] = mapped_column(sa.BigInteger)
    executed_by: Mapped[Role] = mapped_column(_enum(Role, "role_t"))
    environment_hash: Mapped[str] = _digest()
    image_digest: Mapped[str] = mapped_column(sa.String(128))
    isolation_tier: Mapped[str] = mapped_column(sa.String(16))
    """Recorded per run so a claim's isolation tier stays auditable (ADR-0002)."""
    git_commit: Mapped[str] = mapped_column(sa.String(40))
    started_at: Mapped[dt.datetime]
    finished_at: Mapped[dt.datetime | None]
    status: Mapped[RunStatus] = mapped_column(_enum(RunStatus, "run_status"))
    retry_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    telemetry: Mapped[dict[str, Any]]

    __table_args__ = (
        sa.UniqueConstraint(
            "registration_id",
            "seed",
            "executed_by",
            "retry_count",
            name="uq_run_identity",
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_run_retry_nonneg"),
    )


class RunResult(Base):
    """One metric from one run. Append-only.

    ``ck_holdout_custodian_only`` is small and does a great deal of work: no
    path exists by which an agent-authored number about the test set enters
    the database.
    """

    __tablename__ = "run_results"

    result_id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("runs.run_id"), index=True)
    split: Mapped[Split] = mapped_column(_enum(Split, "split_t"))
    metric: Mapped[str] = mapped_column(sa.String(64))
    value: Mapped[float] = mapped_column(sa.Float)
    computed_by: Mapped[ComputedBy] = mapped_column(_enum(ComputedBy, "computed_by_t"))
    artifact_hash: Mapped[str] = _digest()

    __table_args__ = (
        sa.CheckConstraint(
            "split <> 'holdout' OR computed_by = 'custodian'",
            name="ck_holdout_custodian_only",
        ),
        sa.UniqueConstraint("run_id", "split", "metric", name="uq_result_identity"),
    )


class HoldoutQuery(Base):
    """Accounting for adaptive overfitting.

    Every look at the test split is a row. Confidence is later discounted by
    how many were consumed, which is the only honest way to price hundreds of
    automated experiments against one holdout.
    """

    __tablename__ = "holdout_queries"

    query_id: Mapped[uuid.UUID] = _uuid_pk()
    registration_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("registrations.registration_id"), index=True
    )
    requested_at: Mapped[dt.datetime]
    artifact_hash: Mapped[str] = _digest()
    granted: Mapped[bool] = mapped_column(sa.Boolean)
    remaining_budget: Mapped[int] = mapped_column(sa.Integer)


class Replication(Base):
    """An independent reproduction attempt and its outcome."""

    __tablename__ = "replications"

    replication_id: Mapped[uuid.UUID] = _uuid_pk()
    original_registration_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("registrations.registration_id"), index=True
    )
    replication_registration_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("registrations.registration_id")
    )
    outcome: Mapped[ReplicationOutcome] = mapped_column(
        _enum(ReplicationOutcome, "replication_outcome")
    )
    concordance: Mapped[dict[str, Any]]
    executed_by: Mapped[Role] = mapped_column(_enum(Role, "role_t"))
    created_at: Mapped[dt.datetime]


# ===========================================================================
# Claims, evidence, criticism
# ===========================================================================


class Source(Base):
    """An external source, with the retrieved passage stored verbatim.

    ``verified`` is set only by the resolver, never by an agent: an
    unresolvable citation must not be able to support a claim.
    """

    __tablename__ = "sources"

    source_id: Mapped[uuid.UUID] = _uuid_pk()
    identifier: Mapped[str] = mapped_column(sa.String(256), index=True)
    retrieved_at: Mapped[dt.datetime]
    verbatim_passage: Mapped[str] = mapped_column(sa.Text)
    passage_hash: Mapped[str] = _digest()
    verified: Mapped[bool] = mapped_column(sa.Boolean, default=False)


class Claim(Base):
    """An assertion the institution holds, with a computed confidence."""

    __tablename__ = "claims"

    claim_id: Mapped[uuid.UUID] = _uuid_pk()
    program_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("programs.program_id"), index=True)
    hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("hypotheses.hypothesis_id")
    )
    statement: Mapped[str] = mapped_column(sa.Text)
    kind: Mapped[AssertionKind] = mapped_column(_enum(AssertionKind, "assertion_kind"))
    confidence: Mapped[ClaimConfidence] = mapped_column(_enum(ClaimConfidence, "claim_confidence"))
    computed_at: Mapped[dt.datetime]

    __table_args__ = (
        sa.CheckConstraint("kind <> 'speculation'", name="ck_claim_is_not_speculation"),
    )


class Evidence(Base):
    """A link from a claim to what supports or contradicts it."""

    __tablename__ = "evidence"

    evidence_id: Mapped[uuid.UUID] = _uuid_pk()
    claim_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("claims.claim_id"), index=True)
    kind: Mapped[EvidenceKind] = mapped_column(_enum(EvidenceKind, "evidence_kind"))
    polarity: Mapped[Polarity] = mapped_column(_enum(Polarity, "polarity_t"))
    result_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("run_results.result_id"))
    source_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("sources.source_id"))
    parent_claim_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("claims.claim_id"))
    strength: Mapped[dict[str, Any]]
    created_at: Mapped[dt.datetime]

    __table_args__ = (
        sa.CheckConstraint(
            "(kind = 'experimental' AND result_id IS NOT NULL)"
            " OR (kind = 'sourced' AND source_id IS NOT NULL)"
            " OR (kind = 'derived' AND parent_claim_id IS NOT NULL)",
            name="ck_evidence_has_a_referent",
        ),
    )


class Objection(Base):
    """A typed criticism that must name a test capable of settling it.

    ``discriminating_test`` is NOT NULL by design: an objection with no
    resolving experiment is rhetoric, and rhetoric cannot block a claim.
    ``was_injected_defect`` is populated only by the evaluation harness, and
    is how Skeptic recall becomes measurable.
    """

    __tablename__ = "objections"

    objection_id: Mapped[uuid.UUID] = _uuid_pk()
    target_type: Mapped[str] = mapped_column(sa.String(32))
    target_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), index=True)
    type: Mapped[ObjectionType] = mapped_column(_enum(ObjectionType, "objection_type"))
    severity: Mapped[ObjectionSeverity] = mapped_column(_enum(ObjectionSeverity, "objection_sev"))
    statement: Mapped[str] = mapped_column(sa.Text)
    discriminating_test: Mapped[dict[str, Any]]
    raised_by_task: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    raised_by_role: Mapped[Role] = mapped_column(_enum(Role, "role_t"))
    status: Mapped[ObjectionStatus] = mapped_column(_enum(ObjectionStatus, "objection_status"))
    resolved_by_registration_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("registrations.registration_id")
    )
    was_injected_defect: Mapped[bool | None] = mapped_column(sa.Boolean)
    created_at: Mapped[dt.datetime]


class Review(Base):
    __tablename__ = "reviews"

    review_id: Mapped[uuid.UUID] = _uuid_pk()
    claim_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("claims.claim_id"), index=True)
    decision: Mapped[ReviewDecision] = mapped_column(_enum(ReviewDecision, "review_decision"))
    scores: Mapped[dict[str, Any]]
    rationale: Mapped[str] = mapped_column(sa.Text)
    reviewed_by_task: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    created_at: Mapped[dt.datetime]


class Position(Base):
    """A role's stance on a claim. Disagreement is stored, not resolved."""

    __tablename__ = "positions"

    position_id: Mapped[uuid.UUID] = _uuid_pk()
    claim_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("claims.claim_id"), index=True)
    role: Mapped[Role] = mapped_column(_enum(Role, "role_t"))
    stance: Mapped[Stance] = mapped_column(_enum(Stance, "stance_t"))
    rationale: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[dt.datetime]

    __table_args__ = (sa.UniqueConstraint("claim_id", "role", name="uq_position_one_per_role"),)


# ===========================================================================
# Decisions, cost, audit
# ===========================================================================


class Decision(Base):
    """An allocation or governance decision, with its inputs preserved.

    ``inputs`` holds the numbers the policy actually saw, so a decision can be
    re-derived and second-guessed later. ``dissent`` records an override of a
    standing objection rather than erasing it.
    """

    __tablename__ = "decisions"

    decision_id: Mapped[uuid.UUID] = _uuid_pk()
    program_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("programs.program_id"), index=True)
    policy_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("policies.policy_id"))
    kind: Mapped[str] = mapped_column(sa.String(32))
    subject_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid())
    inputs: Mapped[dict[str, Any]]
    outcome: Mapped[str] = mapped_column(sa.String(32))
    dissent: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    created_at: Mapped[dt.datetime]


class Task(Base):
    """One unit of work dispatched to one role.

    ``view`` stores the materialised input the agent was shown, rather than a
    reference to be recomputed later. An audit asking "what did the Skeptic
    actually see?" must be answerable after the institution's state has moved
    on, and a recomputed view answers a different question.
    """

    __tablename__ = "tasks"

    task_id: Mapped[uuid.UUID] = _uuid_pk()
    program_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("programs.program_id"), index=True)
    role: Mapped[Role] = mapped_column(_enum(Role, "role_t"))
    contract_version: Mapped[str] = mapped_column(sa.String(32))
    subject_type: Mapped[str] = mapped_column(sa.String(64))
    subject_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid())
    status: Mapped[str] = mapped_column(sa.String(20), index=True)
    allowance_usd: Mapped[Decimal]
    view: Mapped[dict[str, Any]]
    result: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    failure_reason: Mapped[str | None] = mapped_column(sa.Text)
    calls: Mapped[int] = mapped_column(sa.Integer, default=0)
    spent_usd: Mapped[Decimal]
    created_at: Mapped[dt.datetime]
    claimed_at: Mapped[dt.datetime | None]
    finished_at: Mapped[dt.datetime | None]

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('pending','running','succeeded','failed','refused_budget')",
            name="ck_task_status",
        ),
    )


class CostEntry(Base):
    """Real cost, in one ledger. A fake economy teaches nothing."""

    __tablename__ = "cost_entries"

    cost_id: Mapped[int] = mapped_column(_AUTO_PK, primary_key=True, autoincrement=True)
    program_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    llm_input_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    llm_output_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    llm_cached_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    cpu_seconds: Mapped[float] = mapped_column(sa.Float, default=0.0)
    storage_mb: Mapped[float] = mapped_column(sa.Float, default=0.0)
    usd: Mapped[Decimal]
    price_table_version: Mapped[str] = mapped_column(sa.String(32))
    created_at: Mapped[dt.datetime]


class LlmCall(Base):
    """One model call. ``cache_key`` is what makes a program replayable."""

    __tablename__ = "llm_calls"

    call_id: Mapped[uuid.UUID] = _uuid_pk()
    task_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), index=True)
    cache_key: Mapped[str] = mapped_column(sa.String(64), index=True)
    provider: Mapped[str] = mapped_column(sa.String(32))
    model: Mapped[str] = mapped_column(sa.String(64))
    params: Mapped[dict[str, Any]]
    prompt_hash: Mapped[str] = _digest()
    response_hash: Mapped[str] = _digest()
    cache_hit: Mapped[bool] = mapped_column(sa.Boolean)
    created_at: Mapped[dt.datetime]


class Artifact(Base):
    """Index of what is in the content-addressed store, and where it came from."""

    __tablename__ = "artifacts"

    artifact_hash: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(sa.String(32))
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger)
    produced_by_run: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), index=True)
    created_at: Mapped[dt.datetime]


class QueryAudit(Base):
    """Which role read what.

    Where row-level security is unavailable (ADR-0001), this is how Replicator
    blindness is *proven* rather than asserted: the isolation test asserts
    that the replicator role never read a row belonging to the original run.
    """

    __tablename__ = "query_audit"

    audit_id: Mapped[int] = mapped_column(_AUTO_PK, primary_key=True, autoincrement=True)
    occurred_at: Mapped[dt.datetime]
    role: Mapped[Role] = mapped_column(_enum(Role, "role_t"), index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    operation: Mapped[str] = mapped_column(sa.String(64))
    entity: Mapped[str] = mapped_column(sa.String(64))
    entity_ids: Mapped[list[Any]]


APPEND_ONLY_TABLES: tuple[str, ...] = (
    "events",
    "run_results",
    "forecasts",
    "objections",
    "cost_entries",
    "holdout_queries",
    "llm_calls",
    "query_audit",
)
"""Tables no application role may UPDATE or DELETE.

Enforced by triggers in :mod:`nullius.db.triggers` so the rule also holds
against raw SQL, not merely against our own repository layer.
"""
