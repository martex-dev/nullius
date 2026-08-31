"""Invariants: blindness, and what it takes to promote a claim."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from nullius.db.enums import (
    AssertionKind,
    ClaimConfidence,
    ComputedBy,
    EvidenceKind,
    ObjectionSeverity,
    ObjectionStatus,
    ObjectionType,
    Polarity,
    ReplicationOutcome,
    ReviewDecision,
    Role,
    RunStatus,
    Split,
)
from nullius.db.tables import QueryAudit, RunResult
from nullius.errors import AuthorityError, InvariantViolation
from nullius.ledger.rebuild import reconciliation
from nullius.repository import Repository
from nullius.runtime.guard import SpendGuard, SpendLimits
from tests.conftest import Scaffold, make_hypothesis
from tests.test_execution import SPEC

pytestmark = pytest.mark.invariant


@pytest.fixture
def prepared(repo: Repository, scaffold: Scaffold) -> dict[str, uuid.UUID]:
    """A registration, an original run with results, and a claim on it."""
    hypothesis_id = make_hypothesis(repo, scaffold)
    dataset = repo.record_dataset(name="d", version="1", content_hash="a" * 64, licence="synthetic")
    bundle = repo.record_code_bundle(content_hash="b" * 64, validator_report={}, passed=True)
    registration = repo.register(
        hypothesis_id=hypothesis_id,
        spec=SPEC.model_dump(mode="json"),
        analysis_plan={"test": "paired_bootstrap"},
        seed_root=SPEC.seed_root,
        n_seeds=SPEC.n_seeds,
        holdout_query_budget=3,
        program_id=scaffold.program_id,
    )
    run = repo.start_run(
        registration_id=registration.registration_id,
        bundle_id=bundle.bundle_id,
        dataset_id=dataset.dataset_id,
        seed=SPEC.seeds()[0],
        environment_hash="c" * 64,
        image_digest="none",
        isolation_tier="subprocess",
        git_commit="0" * 40,
        program_id=scaffold.program_id,
    )
    repo.finish_run(
        run.run_id, status=RunStatus.COMPLETED, telemetry={}, program_id=scaffold.program_id
    )
    result = repo.record_result(
        run_id=run.run_id,
        split=Split.DEV,
        metric="prune.macro_f1",
        value=0.89,
        artifact_hash="d" * 64,
        program_id=scaffold.program_id,
    )

    analyst = repo.as_role(Role.ANALYST)
    claim = analyst.create_claim(
        program_id=scaffold.program_id,
        statement="Pruning improves deployment performance under this shift.",
        kind=AssertionKind.INFERRED_CLAIM,
        hypothesis_id=hypothesis_id,
    )
    analyst.add_evidence(
        claim_id=claim.claim_id,
        kind=EvidenceKind.EXPERIMENTAL,
        polarity=Polarity.SUPPORTS,
        result_id=result.result_id,
        strength={"difference": 0.59},
        program_id=scaffold.program_id,
    )
    repo.commit()

    return {
        "hypothesis_id": hypothesis_id,
        "registration_id": registration.registration_id,
        "run_id": run.run_id,
        "claim_id": claim.claim_id,
    }


# ---------------------------------------------------------------------------
# Replicator blindness
# ---------------------------------------------------------------------------


@pytest.mark.isolation
def test_the_replicator_cannot_read_the_original_runs_results(
    repo: Repository, prepared: dict[str, uuid.UUID]
) -> None:
    """Enforced by capability, not by asking it not to look."""
    replicator = repo.as_role(Role.REPLICATOR)

    assert repo.results_for_run(prepared["run_id"]), "the control plane can see them"
    assert replicator.results_for_run(prepared["run_id"]) == []
    assert replicator.runs_for_registration(prepared["registration_id"]) == []


@pytest.mark.isolation
def test_replicator_blindness_is_provable_from_the_audit_log(
    repo: Repository, prepared: dict[str, uuid.UUID]
) -> None:
    """The acceptance criterion: proven by evidence, not by inspection.

    Where PostgreSQL row-level security is unavailable, this trail is what
    substitutes for it (ADR-0001) — so the claim of blindness has to be
    checkable after the fact, from what the role actually read.
    """
    replicator = repo.as_role(Role.REPLICATOR)
    replicator.results_for_run(prepared["run_id"])
    replicator.runs_for_registration(prepared["registration_id"])
    replicator.get_registration(prepared["registration_id"])
    repo.commit()

    original_result_ids = {str(row.result_id) for row in repo.session.scalars(sa.select(RunResult))}
    assert original_result_ids, "there is something it could have read"

    trail = list(
        repo.session.scalars(sa.select(QueryAudit).where(QueryAudit.role == Role.REPLICATOR))
    )
    assert trail, "the replicator did read something, so the trail is not empty by accident"

    everything_read = {identifier for entry in trail for identifier in (entry.entity_ids or [])}
    assert not (everything_read & original_result_ids), (
        f"the replicator read rows from the original run: "
        f"{sorted(everything_read & original_result_ids)}"
    )


@pytest.mark.isolation
def test_the_replicator_may_read_the_registration(
    repo: Repository, prepared: dict[str, uuid.UUID]
) -> None:
    """Blindness is to results, not to the design it is asked to reproduce."""
    registration = repo.as_role(Role.REPLICATOR).get_registration(prepared["registration_id"])
    assert registration is not None
    assert registration.spec


def test_a_registration_cannot_replicate_itself(
    repo: Repository, prepared: dict[str, uuid.UUID]
) -> None:
    with pytest.raises(InvariantViolation, match="cannot replicate itself"):
        repo.as_role(Role.REPLICATOR).record_replication(
            original_registration_id=prepared["registration_id"],
            replication_registration_id=prepared["registration_id"],
            outcome=ReplicationOutcome.REPLICATED,
            concordance={},
        )


# ---------------------------------------------------------------------------
# A critical objection blocks promotion until its test is run
# ---------------------------------------------------------------------------


def test_a_critical_objection_blocks_promotion(
    repo: Repository, scaffold: Scaffold, prepared: dict[str, uuid.UUID]
) -> None:
    """The acceptance criterion, first half."""
    repo.as_role(Role.SKEPTIC).raise_objection(
        target_type="claims",
        target_id=prepared["claim_id"],
        objection_type=ObjectionType.CONFOUND,
        severity=ObjectionSeverity.CRITICAL,
        statement="Feature count is not matched between the arms.",
        discriminating_test={"action": "add_random_prune_arm", "matched_on": "n_features"},
        program_id=scaffold.program_id,
    )

    with pytest.raises(InvariantViolation, match="unresolved critical objection"):
        repo.as_role(Role.DIRECTOR).promote_claim(
            prepared["claim_id"], ClaimConfidence.SUPPORTED, program_id=scaffold.program_id
        )


def test_a_critical_objection_cannot_be_closed_by_assertion(
    repo: Repository, scaffold: Scaffold, prepared: dict[str, uuid.UUID]
) -> None:
    """Settled by running the discriminating test, not by declaring it settled."""
    objection = repo.as_role(Role.SKEPTIC).raise_objection(
        target_type="claims",
        target_id=prepared["claim_id"],
        objection_type=ObjectionType.CONFOUND,
        severity=ObjectionSeverity.CRITICAL,
        statement="Feature count is not matched between the arms.",
        discriminating_test={"action": "add_random_prune_arm"},
        program_id=scaffold.program_id,
    )

    with pytest.raises(InvariantViolation, match="not by declaring it settled"):
        repo.as_role(Role.DIRECTOR).resolve_objection(
            objection.objection_id, status=ObjectionStatus.RESOLVED_REJECTED
        )


def test_running_the_discriminating_test_unblocks_the_claim(
    repo: Repository, scaffold: Scaffold, prepared: dict[str, uuid.UUID]
) -> None:
    """The acceptance criterion, second half."""
    objection = repo.as_role(Role.SKEPTIC).raise_objection(
        target_type="claims",
        target_id=prepared["claim_id"],
        objection_type=ObjectionType.CONFOUND,
        severity=ObjectionSeverity.CRITICAL,
        statement="Feature count is not matched between the arms.",
        discriminating_test={"action": "add_random_prune_arm"},
        program_id=scaffold.program_id,
    )

    # The discriminating test is itself a registered experiment.
    settling = repo.as_role(Role.DESIGNER).register(
        hypothesis_id=prepared["hypothesis_id"],
        spec={**SPEC.model_dump(mode="json"), "title": "Capacity-matched control"},
        analysis_plan={"test": "paired_bootstrap", "resolves": str(objection.objection_id)},
        seed_root=SPEC.seed_root + 1,
        n_seeds=SPEC.n_seeds,
        holdout_query_budget=1,
        program_id=scaffold.program_id,
    )
    resolution = repo.as_role(Role.DIRECTOR).resolve_objection(
        objection.objection_id,
        status=ObjectionStatus.RESOLVED_REJECTED,
        resolved_by_registration_id=settling.registration_id,
        note="The matched control reproduced the effect.",
        program_id=scaffold.program_id,
    )
    assert resolution.resolved_by_registration_id == settling.registration_id

    claim = repo.as_role(Role.DIRECTOR).promote_claim(
        prepared["claim_id"], ClaimConfidence.SUPPORTED, program_id=scaffold.program_id
    )
    assert claim.confidence is ClaimConfidence.SUPPORTED
    repo.commit()
    assert reconciliation(repo.session).ok


def test_well_supported_requires_an_independent_replication(
    repo: Repository, scaffold: Scaffold, prepared: dict[str, uuid.UUID]
) -> None:
    director = repo.as_role(Role.DIRECTOR)

    with pytest.raises(InvariantViolation, match="independent reproduction"):
        director.promote_claim(
            prepared["claim_id"], ClaimConfidence.WELL_SUPPORTED, program_id=scaffold.program_id
        )

    replication_registration = repo.as_role(Role.DESIGNER).register(
        hypothesis_id=prepared["hypothesis_id"],
        spec={**SPEC.model_dump(mode="json"), "title": "Independent reproduction"},
        analysis_plan={"test": "paired_bootstrap"},
        seed_root=SPEC.seed_root + 7,
        n_seeds=SPEC.n_seeds,
        holdout_query_budget=1,
        program_id=scaffold.program_id,
    )
    repo.as_role(Role.REPLICATOR).record_replication(
        original_registration_id=prepared["registration_id"],
        replication_registration_id=replication_registration.registration_id,
        outcome=ReplicationOutcome.REPLICATED,
        concordance={"sign": "same", "within_interval": True},
        program_id=scaffold.program_id,
    )

    claim = director.promote_claim(
        prepared["claim_id"], ClaimConfidence.WELL_SUPPORTED, program_id=scaffold.program_id
    )
    assert claim.confidence is ClaimConfidence.WELL_SUPPORTED


def test_a_failed_replication_does_not_count(
    repo: Repository, scaffold: Scaffold, prepared: dict[str, uuid.UUID]
) -> None:
    replication_registration = repo.as_role(Role.DESIGNER).register(
        hypothesis_id=prepared["hypothesis_id"],
        spec={**SPEC.model_dump(mode="json"), "title": "Failed reproduction"},
        analysis_plan={"test": "paired_bootstrap"},
        seed_root=SPEC.seed_root + 9,
        n_seeds=SPEC.n_seeds,
        holdout_query_budget=1,
        program_id=scaffold.program_id,
    )
    repo.as_role(Role.REPLICATOR).record_replication(
        original_registration_id=prepared["registration_id"],
        replication_registration_id=replication_registration.registration_id,
        outcome=ReplicationOutcome.FAILED_REPLICATION,
        concordance={"sign": "opposite"},
        program_id=scaffold.program_id,
    )

    with pytest.raises(InvariantViolation, match="independent reproduction"):
        repo.as_role(Role.DIRECTOR).promote_claim(
            prepared["claim_id"], ClaimConfidence.WELL_SUPPORTED, program_id=scaffold.program_id
        )


def test_only_the_reviewer_records_a_review(
    repo: Repository, scaffold: Scaffold, prepared: dict[str, uuid.UUID]
) -> None:
    with pytest.raises(AuthorityError, match="may not record_review"):
        repo.as_role(Role.THEORIST).record_review(
            claim_id=prepared["claim_id"],
            decision=ReviewDecision.ACCEPT,
            scores={"novelty": 4},
            rationale="Looks good to me.",
        )

    review = repo.as_role(Role.REVIEWER).record_review(
        claim_id=prepared["claim_id"],
        decision=ReviewDecision.MAJOR_REVISION,
        scores={"novelty": 3, "statistical_quality": 2},
        rationale="Underpowered for the effect claimed.",
        program_id=scaffold.program_id,
    )
    assert review.decision is ReviewDecision.MAJOR_REVISION


# ---------------------------------------------------------------------------
# The global spend guard
# ---------------------------------------------------------------------------


def test_the_guard_permits_spending_within_its_ceilings(repo: Repository) -> None:
    guard = SpendGuard(repo.session, clock=repo.clock)
    verdict = guard.check(Decimal("0.05"))
    assert verdict.allowed, str(verdict)


def test_the_daily_ceiling_halts_the_institution(repo: Repository, scaffold: Scaffold) -> None:
    """A limit that has never stopped anything has not been tested."""
    guard = SpendGuard(repo.session, SpendLimits(daily_usd=Decimal("1.00")), clock=repo.clock)
    repo.record_cost(
        program_id=scaffold.program_id,
        usd=Decimal("0.99"),
        price_table_version="test",
    )

    assert guard.check(Decimal("0.005")).allowed
    verdict = guard.check(Decimal("0.50"))
    assert not verdict.allowed
    assert "daily ceiling" in (verdict.reason or "")


def test_the_project_ceiling_halts_the_institution(repo: Repository, scaffold: Scaffold) -> None:
    guard = SpendGuard(
        repo.session,
        SpendLimits(daily_usd=Decimal("1000"), project_usd=Decimal("1.00")),
        clock=repo.clock,
    )
    repo.record_cost(
        program_id=scaffold.program_id, usd=Decimal("1.50"), price_table_version="test"
    )

    verdict = guard.check()
    assert not verdict.allowed
    assert "project ceiling" in (verdict.reason or "")


def test_the_rate_ceiling_catches_a_runaway_loop(repo: Repository, scaffold: Scaffold) -> None:
    """Catches a loop long before either dollar ceiling would."""
    guard = SpendGuard(repo.session, SpendLimits(calls_per_hour=3), clock=repo.clock)
    system = repo.as_role(Role.SYSTEM)
    for index in range(3):
        system.record_llm_call(
            cache_key=f"{index:064d}",
            provider="mock",
            model="mock-1",
            params={},
            prompt_hash="0" * 64,
            response_hash="1" * 64,
            cache_hit=False,
            program_id=scaffold.program_id,
        )

    verdict = guard.check()
    assert not verdict.allowed
    assert "rate ceiling" in (verdict.reason or "")


def test_cache_hits_do_not_count_against_the_rate_ceiling(
    repo: Repository, scaffold: Scaffold
) -> None:
    """A replay makes no request, so it cannot be a runaway loop."""
    guard = SpendGuard(repo.session, SpendLimits(calls_per_hour=2), clock=repo.clock)
    system = repo.as_role(Role.SYSTEM)
    for index in range(5):
        system.record_llm_call(
            cache_key=f"{index:064d}",
            provider="replay",
            model="mock-1",
            params={},
            prompt_hash="0" * 64,
            response_hash="1" * 64,
            cache_hit=True,
            program_id=scaffold.program_id,
        )

    assert guard.check().allowed


def test_holdout_metrics_still_require_the_custodian(
    repo: Repository, prepared: dict[str, uuid.UUID]
) -> None:
    """The M5 rule, re-checked now that more roles exist."""
    for role in (Role.SKEPTIC, Role.REVIEWER, Role.REPLICATOR, Role.DIRECTOR):
        with pytest.raises((InvariantViolation, AuthorityError)):
            repo.as_role(role).record_result(
                run_id=prepared["run_id"],
                split=Split.HOLDOUT,
                metric="prune.macro_f1",
                value=0.99,
                artifact_hash="e" * 64,
                computed_by=ComputedBy.CUSTODIAN,
            )


def test_the_original_objection_is_never_edited(
    repo: Repository, scaffold: Scaffold, prepared: dict[str, uuid.UUID]
) -> None:
    """Resolution is a separate row, so what was said stays as it was said."""
    from nullius.db.tables import Objection

    objection = repo.as_role(Role.SKEPTIC).raise_objection(
        target_type="claims",
        target_id=prepared["claim_id"],
        objection_type=ObjectionType.CONFOUND,
        severity=ObjectionSeverity.MAJOR,
        statement="Feature count is not matched between the arms.",
        discriminating_test={"action": "add_random_prune_arm"},
        program_id=scaffold.program_id,
    )
    original_statement = objection.statement

    repo.as_role(Role.DIRECTOR).resolve_objection(
        objection.objection_id,
        status=ObjectionStatus.RESOLVED_REJECTED,
        program_id=scaffold.program_id,
    )
    repo.commit()

    stored = repo.session.get(Objection, objection.objection_id)
    assert stored is not None
    assert stored.statement == original_statement
    assert stored.status is ObjectionStatus.OPEN, (
        "the objection row records what was raised; whether it still stands is a separate fact"
    )
    assert reconciliation(repo.session).ok


def test_an_objection_cannot_be_resolved_twice(
    repo: Repository, scaffold: Scaffold, prepared: dict[str, uuid.UUID]
) -> None:
    objection = repo.as_role(Role.SKEPTIC).raise_objection(
        target_type="claims",
        target_id=prepared["claim_id"],
        objection_type=ObjectionType.SEED_INSTABILITY,
        severity=ObjectionSeverity.MINOR,
        statement="The arms vary between seeds more than is comfortable.",
        discriminating_test={"action": "increase_seeds"},
        program_id=scaffold.program_id,
    )
    director = repo.as_role(Role.DIRECTOR)
    director.resolve_objection(
        objection.objection_id,
        status=ObjectionStatus.RESOLVED_UPHELD,
        program_id=scaffold.program_id,
    )

    with pytest.raises(InvariantViolation, match="already resolved_upheld"):
        director.resolve_objection(
            objection.objection_id,
            status=ObjectionStatus.RESOLVED_REJECTED,
            program_id=scaffold.program_id,
        )
