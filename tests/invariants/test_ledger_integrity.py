"""Invariants: the ledger is append-only, tamper-evident, and complete.

"All state is a fold over the event log" is the kind of claim that is true on
the day it is written and quietly false six months later. So it is a test:
:func:`~nullius.ledger.rebuild.reconciliation` rebuilds every table from events
and names any row that was written without one.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from nullius.db.enums import (
    AssertionKind,
    ComputedBy,
    EvidenceKind,
    ObjectionSeverity,
    ObjectionType,
    Polarity,
    Role,
    RunStatus,
    Split,
)
from nullius.db.tables import APPEND_ONLY_TABLES
from nullius.errors import InvariantViolation
from nullius.ledger.rebuild import reconciliation
from nullius.repository import Repository
from tests.conftest import Scaffold, make_hypothesis

pytestmark = pytest.mark.invariant


# ---------------------------------------------------------------------------
# Append-only
# ---------------------------------------------------------------------------


@pytest.fixture
def populated(repo: Repository, scaffold: Scaffold) -> Repository:
    """One row in every append-only table.

    Necessary, not incidental: a ``BEFORE UPDATE ... FOR EACH ROW`` trigger
    never fires on an empty table, so testing append-only enforcement against
    empty tables would pass while proving nothing.
    """
    from nullius.db.tables import CostEntry, HoldoutQuery, LlmCall

    hypothesis_id = make_hypothesis(repo, scaffold)
    dataset = repo.record_dataset(name="d", version="1", content_hash="a" * 64, licence="CC0")
    bundle = repo.record_code_bundle(content_hash="b" * 64, validator_report={}, passed=True)
    registration = repo.register(
        hypothesis_id=hypothesis_id,
        spec={"arms": ["full"]},
        analysis_plan={"test": "paired_bootstrap"},
        seed_root=1,
        n_seeds=1,
        holdout_query_budget=1,
    )
    repo.record_forecast(
        registration_id=registration.registration_id,
        p_effect_exceeds_mde=0.5,
        predictive_mean=0.0,
        predictive_sd=0.01,
        p_execution_success=1.0,
    )
    run = repo.start_run(
        registration_id=registration.registration_id,
        bundle_id=bundle.bundle_id,
        dataset_id=dataset.dataset_id,
        seed=1,
        environment_hash="c" * 64,
        image_digest="none",
        isolation_tier="subprocess",
        git_commit="0" * 40,
    )
    repo.record_result(
        run_id=run.run_id, split=Split.DEV, metric="macro_f1", value=0.9, artifact_hash="d" * 64
    )
    repo.as_role(Role.SKEPTIC).raise_objection(
        target_type="runs",
        target_id=run.run_id,
        objection_type=ObjectionType.SEED_INSTABILITY,
        severity=ObjectionSeverity.MAJOR,
        statement="One seed cannot show variance.",
        discriminating_test={"n_seeds": 5},
    )
    repo.get_hypothesis(hypothesis_id)  # populates query_audit

    # These three have no repository method until M2/M5; inserted directly
    # because the subject under test is the database rule, not the API.
    now = repo.clock.now()
    repo.session.add_all(
        [
            CostEntry(
                program_id=scaffold.program_id,
                usd=Decimal("0.0100"),
                price_table_version="2026-08",
                created_at=now,
            ),
            HoldoutQuery(
                query_id=repo.ids.new(),
                registration_id=registration.registration_id,
                requested_at=now,
                artifact_hash="e" * 64,
                granted=True,
                remaining_budget=0,
            ),
            LlmCall(
                call_id=repo.ids.new(),
                task_id=None,
                cache_key="f" * 64,
                provider="mock",
                model="mock-1",
                params={},
                prompt_hash="0" * 64,
                response_hash="1" * 64,
                cache_hit=False,
                created_at=now,
            ),
        ]
    )
    repo.commit()
    return repo


@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
def test_append_only_tables_refuse_update_and_delete(populated: Repository, table: str) -> None:
    """History is not editable, so evidence cannot be quietly revised."""
    rows = populated.session.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
    assert rows > 0, (
        f"{table} is empty, so a row-level trigger would never fire and this "
        "test would pass without exercising anything"
    )

    column = _any_column(populated, table)
    for statement in (f"UPDATE {table} SET {column} = {column}", f"DELETE FROM {table}"):
        with pytest.raises(sa.exc.DatabaseError, match="append-only"):
            populated.session.execute(sa.text(statement))
        populated.session.rollback()


def _any_column(repo: Repository, table: str) -> str:
    inspector = sa.inspect(repo.session.get_bind())
    return inspector.get_columns(table)[0]["name"]


# ---------------------------------------------------------------------------
# Tamper evidence
# ---------------------------------------------------------------------------


def test_intact_ledger_verifies(repo: Repository, scaffold: Scaffold) -> None:
    make_hypothesis(repo, scaffold)
    repo.commit()

    result = repo.ledger.verify()
    assert result.ok, str(result)
    assert result.events_checked >= 5


def test_altering_a_payload_breaks_the_chain(repo: Repository, scaffold: Scaffold) -> None:
    """Rewriting history is possible with database access — but not silently."""
    make_hypothesis(repo, scaffold, statement="original claim")
    repo.commit()

    # Reach past the append-only trigger the way a determined editor would.
    engine = repo.session.get_bind()
    with engine.connect() as raw:  # type: ignore[union-attr]
        raw.exec_driver_sql("DROP TRIGGER trg_events_no_update")
        raw.exec_driver_sql(
            "UPDATE events SET payload = json_set(payload, '$.entity', 'tampered')"
            " WHERE event_type = 'hypothesis.created'"
        )
        raw.commit()

    repo.session.expire_all()
    result = repo.ledger.verify()
    assert not result.ok
    assert result.first_bad_seq is not None
    assert "payload does not match" in (result.reason or "")


def test_deleting_a_trailing_event_is_detected(repo: Repository, scaffold: Scaffold) -> None:
    """A pure hash chain cannot see a truncation; contiguity checking can."""
    make_hypothesis(repo, scaffold)
    repo.commit()

    engine = repo.session.get_bind()
    with engine.connect() as raw:  # type: ignore[union-attr]
        raw.exec_driver_sql("DROP TRIGGER trg_events_no_delete")
        raw.exec_driver_sql("DELETE FROM events WHERE seq = 2")
        raw.commit()

    repo.session.expire_all()
    result = repo.ledger.verify()
    assert not result.ok
    assert "sequence gap" in (result.reason or "")


def test_reattributing_an_event_to_another_role_breaks_the_chain(
    repo: Repository, scaffold: Scaffold
) -> None:
    """The chain covers the header, not just the payload."""
    make_hypothesis(repo, scaffold)
    repo.commit()

    engine = repo.session.get_bind()
    with engine.connect() as raw:  # type: ignore[union-attr]
        raw.exec_driver_sql("DROP TRIGGER trg_events_no_update")
        raw.exec_driver_sql(
            "UPDATE events SET actor_role = 'skeptic' WHERE event_type = 'hypothesis.created'"
        )
        raw.commit()

    repo.session.expire_all()
    result = repo.ledger.verify()
    assert not result.ok
    assert "chain hash" in (result.reason or "")


# ---------------------------------------------------------------------------
# Completeness: state is a fold over the log
# ---------------------------------------------------------------------------


def test_a_full_research_cycle_reconciles(repo: Repository, scaffold: Scaffold) -> None:
    """Every row in every table must be reconstructible from events alone."""
    hypothesis_id = make_hypothesis(repo, scaffold)
    dataset = repo.record_dataset(name="scm-c1", version="1", content_hash="a" * 64, licence="CC0")
    bundle = repo.record_code_bundle(
        content_hash="b" * 64, validator_report={"checks": ["ast", "leakage"]}, passed=True
    )
    registration = repo.register(
        hypothesis_id=hypothesis_id,
        spec={"arms": ["full", "prune"]},
        analysis_plan={"test": "paired_bootstrap"},
        seed_root=48192,
        n_seeds=2,
        holdout_query_budget=3,
    )
    for role in (Role.THEORIST, Role.SKEPTIC):
        repo.as_role(role).record_forecast(
            registration_id=registration.registration_id,
            p_effect_exceeds_mde=0.5,
            predictive_mean=0.02,
            predictive_sd=0.01,
            p_execution_success=0.9,
        )

    run = repo.start_run(
        registration_id=registration.registration_id,
        bundle_id=bundle.bundle_id,
        dataset_id=dataset.dataset_id,
        seed=48192,
        environment_hash="c" * 64,
        image_digest="none",
        isolation_tier="subprocess",
        git_commit="0" * 40,
    )
    repo.finish_run(run.run_id, status=RunStatus.COMPLETED, telemetry={"cpu_seconds": 2.5})
    result = repo.record_result(
        run_id=run.run_id,
        split=Split.DEV,
        metric="macro_f1",
        value=0.887,
        artifact_hash="d" * 64,
    )
    repo.as_role(Role.CUSTODIAN).record_result(
        run_id=run.run_id,
        split=Split.HOLDOUT,
        metric="macro_f1",
        value=0.871,
        artifact_hash="e" * 64,
        computed_by=ComputedBy.CUSTODIAN,
    )

    analyst = repo.as_role(Role.ANALYST)
    claim = analyst.create_claim(
        program_id=scaffold.program_id,
        statement="Pruning improves OOD macro-F1 when shifted features are non-causal.",
        kind=AssertionKind.INFERRED_CLAIM,
        hypothesis_id=hypothesis_id,
    )
    analyst.add_evidence(
        claim_id=claim.claim_id,
        kind=EvidenceKind.EXPERIMENTAL,
        polarity=Polarity.SUPPORTS,
        result_id=result.result_id,
        strength={"effect": 0.031, "ci": [0.008, 0.055], "n_seeds": 2},
    )
    repo.as_role(Role.SKEPTIC).raise_objection(
        target_type="claims",
        target_id=claim.claim_id,
        objection_type=ObjectionType.CONFOUND,
        severity=ObjectionSeverity.CRITICAL,
        statement="Feature count differs between arms; capacity is not matched.",
        discriminating_test={"arm": "random_prune", "matched_on": "n_features"},
    )
    repo.commit()

    report = reconciliation(repo.session)
    assert report.ok, str(report)
    assert report.rows_checked >= 12

    chain = repo.ledger.verify()
    assert chain.ok, str(chain)


def test_a_row_written_without_an_event_is_caught(repo: Repository, scaffold: Scaffold) -> None:
    """The reconciliation must actually be able to fail."""
    repo.commit()
    repo.session.execute(
        sa.text(
            "INSERT INTO datasets (dataset_id, name, version, content_hash, licence,"
            " created_at) VALUES (:id, 'smuggled', '1', :h, 'CC0', :t)"
        ),
        {
            "id": str(uuid.uuid4()),
            "h": "f" * 64,
            "t": repo.clock.now().isoformat(),
        },
    )
    repo.session.commit()

    report = reconciliation(repo.session)
    assert not report.ok
    assert any(row.startswith("datasets:") for row in report.missing_from_log)


# ---------------------------------------------------------------------------
# Evidence discipline
# ---------------------------------------------------------------------------


def test_speculation_cannot_become_a_claim(repo: Repository, scaffold: Scaffold) -> None:
    analyst = repo.as_role(Role.ANALYST)
    with pytest.raises(InvariantViolation, match="speculation cannot be a claim"):
        analyst.create_claim(
            program_id=scaffold.program_id,
            statement="Attention probably helps.",
            kind=AssertionKind.SPECULATION,
        )


def test_evidence_must_point_at_something(repo: Repository, scaffold: Scaffold) -> None:
    analyst = repo.as_role(Role.ANALYST)
    claim = analyst.create_claim(
        program_id=scaffold.program_id,
        statement="A claim.",
        kind=AssertionKind.INFERRED_CLAIM,
    )
    with pytest.raises(InvariantViolation, match="evidence that points at nothing"):
        analyst.add_evidence(
            claim_id=claim.claim_id,
            kind=EvidenceKind.EXPERIMENTAL,
            polarity=Polarity.SUPPORTS,
            strength={"effect": 0.9},
        )


def test_objection_without_a_discriminating_test_is_refused(
    repo: Repository, scaffold: Scaffold
) -> None:
    """Criticism no experiment could settle cannot block a claim."""
    skeptic = repo.as_role(Role.SKEPTIC)
    with pytest.raises(InvariantViolation, match="rhetoric, not review"):
        skeptic.raise_objection(
            target_type="claims",
            target_id=uuid.uuid4(),
            objection_type=ObjectionType.ALTERNATIVE_EXPLANATION,
            severity=ObjectionSeverity.CRITICAL,
            statement="Something else might explain this.",
            discriminating_test={},
        )


def test_hypothesis_without_a_falsification_condition_is_refused(
    repo: Repository, scaffold: Scaffold
) -> None:
    theorist = repo.as_role(Role.THEORIST)
    with pytest.raises(InvariantViolation, match="not a hypothesis"):
        theorist.create_hypothesis(
            program_id=scaffold.program_id,
            statement="Attention probably improves performance.",
            mechanism="Unclear.",
            primary_metric="accuracy",
            direction="increase",
            mde=0.0,
            falsification_condition="   ",
        )


def test_budget_is_stored_exactly(repo: Repository, scaffold: Scaffold) -> None:
    """Costs are money; binary floating point is the wrong representation."""
    from nullius.db.tables import Program

    program = repo.session.get(Program, scaffold.program_id)
    assert program is not None
    assert program.budget_usd == Decimal("25.00")
    assert isinstance(program.budget_usd, Decimal)
