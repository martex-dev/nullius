"""Invariants: preregistration cannot be circumvented.

Each test here asserts that a state `docs/03-data-model.md` calls unreachable
really is unreachable — and every one is checked twice: once through
:class:`~nullius.repository.Repository` (which should refuse with a useful
message) and once through raw SQL (which should be refused by a database
trigger). The second half is the one that matters. A rule enforced only by the
layer that wants to break it is not enforced.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
import sqlalchemy as sa

from nullius.db.enums import ComputedBy, RegistrationKind, Role, RunStatus, Split
from nullius.errors import AuthorityError, InvariantViolation
from nullius.repository import Repository
from nullius.util.clock import FrozenClock
from tests.conftest import Scaffold, make_hypothesis, sql_uuid

pytestmark = pytest.mark.invariant


def _register(repo: Repository, hypothesis_id: uuid.UUID, salt: str = "a") -> uuid.UUID:
    registration = repo.register(
        hypothesis_id=hypothesis_id,
        spec={"arms": ["full", "prune"], "salt": salt},
        analysis_plan={"test": "paired_bootstrap", "alpha": 0.05},
        seed_root=48192,
        n_seeds=5,
        holdout_query_budget=3,
    )
    return registration.registration_id


def _prerequisites(repo: Repository) -> tuple[uuid.UUID, uuid.UUID]:
    dataset = repo.record_dataset(name="scm-c1", version="1", content_hash="a" * 64, licence="CC0")
    bundle = repo.record_code_bundle(
        content_hash="b" * 64, validator_report={"checks": []}, passed=True
    )
    return bundle.bundle_id, dataset.dataset_id


# ---------------------------------------------------------------------------
# A run cannot precede its registration
# ---------------------------------------------------------------------------


def test_run_without_a_registration_is_refused(repo: Repository, scaffold: Scaffold) -> None:
    bundle_id, dataset_id = _prerequisites(repo)

    with pytest.raises(InvariantViolation, match="results cannot exist without"):
        repo.start_run(
            registration_id=uuid.uuid4(),
            bundle_id=bundle_id,
            dataset_id=dataset_id,
            seed=1,
            environment_hash="c" * 64,
            image_digest="none",
            isolation_tier="subprocess",
            git_commit="0" * 40,
        )


def test_run_dated_before_its_registration_is_refused(
    repo: Repository, scaffold: Scaffold, clock: FrozenClock
) -> None:
    """The adversarial case: design the experiment, then back-date the run."""
    hypothesis_id = make_hypothesis(repo, scaffold)
    bundle_id, dataset_id = _prerequisites(repo)
    registration_id = _register(repo, hypothesis_id)

    # Rewind so "now" precedes the registration. advance() refuses negative
    # deltas by design, so an out-of-order instant must be set explicitly.
    clock.set(clock.now() - dt.timedelta(hours=1))

    with pytest.raises(InvariantViolation, match="dated after the run start"):
        repo.start_run(
            registration_id=registration_id,
            bundle_id=bundle_id,
            dataset_id=dataset_id,
            seed=1,
            environment_hash="c" * 64,
            image_digest="none",
            isolation_tier="subprocess",
            git_commit="0" * 40,
        )


def test_database_refuses_a_back_dated_run_inserted_by_raw_sql(
    repo: Repository, scaffold: Scaffold, clock: FrozenClock
) -> None:
    """The same rule, reaching past the repository entirely."""
    hypothesis_id = make_hypothesis(repo, scaffold)
    bundle_id, dataset_id = _prerequisites(repo)
    registration_id = _register(repo, hypothesis_id)
    repo.commit()

    engine = repo.session.get_bind()
    assert isinstance(engine, sa.Engine)
    earlier = (clock.now() - dt.timedelta(days=1)).isoformat()

    with pytest.raises(sa.exc.DatabaseError, match="requires a locked registration"):
        repo.session.execute(
            sa.text(
                "INSERT INTO runs (run_id, registration_id, bundle_id, dataset_id, seed,"
                " executed_by, environment_hash, image_digest, isolation_tier, git_commit,"
                " started_at, status, retry_count, telemetry)"
                " VALUES (:run_id, :reg, :bundle, :dataset, 1, 'system', :env, 'none',"
                " 'subprocess', :commit, :started, 'completed', 0, '{}')"
            ),
            {
                "run_id": sql_uuid(engine, uuid.uuid4()),
                "reg": sql_uuid(engine, registration_id),
                "bundle": sql_uuid(engine, bundle_id),
                "dataset": sql_uuid(engine, dataset_id),
                "env": "c" * 64,
                "commit": "0" * 40,
                "started": earlier,
            },
        )


# ---------------------------------------------------------------------------
# A locked registration is immutable
# ---------------------------------------------------------------------------


def test_locked_registration_cannot_be_edited(repo: Repository, scaffold: Scaffold) -> None:
    """HARKing, attempted directly against the table."""
    hypothesis_id = make_hypothesis(repo, scaffold)
    registration_id = _register(repo, hypothesis_id)
    repo.commit()

    engine = repo.session.get_bind()
    assert isinstance(engine, sa.Engine)

    with pytest.raises(sa.exc.DatabaseError, match="locked registration is immutable"):
        repo.session.execute(
            sa.text("UPDATE registrations SET spec_hash = :h WHERE registration_id = :r"),
            {"h": "9" * 64, "r": sql_uuid(engine, registration_id)},
        )


def test_registering_the_same_design_twice_is_refused(repo: Repository, scaffold: Scaffold) -> None:
    """Re-registering would hide that the same experiment ran twice."""
    hypothesis_id = make_hypothesis(repo, scaffold)
    _register(repo, hypothesis_id, salt="same")

    with pytest.raises(InvariantViolation, match="already registered"):
        _register(repo, hypothesis_id, salt="same")


def test_analysis_plan_is_part_of_the_registration_hash(
    repo: Repository, scaffold: Scaffold
) -> None:
    """Changing how data will be analysed changes the experiment."""
    hypothesis_id = make_hypothesis(repo, scaffold)
    first = repo.register(
        hypothesis_id=hypothesis_id,
        spec={"arms": ["full", "prune"]},
        analysis_plan={"test": "paired_bootstrap", "alpha": 0.05},
        seed_root=1,
        n_seeds=5,
        holdout_query_budget=3,
    )
    second = repo.register(
        hypothesis_id=hypothesis_id,
        spec={"arms": ["full", "prune"]},
        analysis_plan={"test": "paired_bootstrap", "alpha": 0.10},
        seed_root=1,
        n_seeds=5,
        holdout_query_budget=3,
    )
    assert first.spec_hash != second.spec_hash


def test_derived_registration_must_name_its_parent(repo: Repository, scaffold: Scaffold) -> None:
    hypothesis_id = make_hypothesis(repo, scaffold)
    with pytest.raises(InvariantViolation, match="must name the registration it derives from"):
        repo.register(
            hypothesis_id=hypothesis_id,
            spec={"arms": ["full"]},
            analysis_plan={"test": "t"},
            seed_root=1,
            n_seeds=1,
            holdout_query_budget=1,
            kind=RegistrationKind.EXPLORATORY,
        )


# ---------------------------------------------------------------------------
# Forecasts precede execution
# ---------------------------------------------------------------------------


def test_forecast_after_a_run_exists_is_refused(repo: Repository, scaffold: Scaffold) -> None:
    """A prediction made after seeing results is not a prediction."""
    hypothesis_id = make_hypothesis(repo, scaffold)
    bundle_id, dataset_id = _prerequisites(repo)
    registration_id = _register(repo, hypothesis_id)

    repo.record_forecast(
        registration_id=registration_id,
        p_effect_exceeds_mde=0.4,
        predictive_mean=0.01,
        predictive_sd=0.02,
        p_execution_success=0.95,
    )
    repo.start_run(
        registration_id=registration_id,
        bundle_id=bundle_id,
        dataset_id=dataset_id,
        seed=1,
        environment_hash="c" * 64,
        image_digest="none",
        isolation_tier="subprocess",
        git_commit="0" * 40,
    )
    repo.commit()

    skeptic = repo.as_role(Role.SKEPTIC)
    with pytest.raises(InvariantViolation, match="not predictions"):
        skeptic.record_forecast(
            registration_id=registration_id,
            p_effect_exceeds_mde=0.9,
            predictive_mean=0.05,
            predictive_sd=0.01,
            p_execution_success=1.0,
        )


# ---------------------------------------------------------------------------
# The holdout belongs to the Custodian
# ---------------------------------------------------------------------------


def _completed_run(repo: Repository, scaffold: Scaffold) -> uuid.UUID:
    hypothesis_id = make_hypothesis(repo, scaffold)
    bundle_id, dataset_id = _prerequisites(repo)
    registration_id = _register(repo, hypothesis_id)
    run = repo.start_run(
        registration_id=registration_id,
        bundle_id=bundle_id,
        dataset_id=dataset_id,
        seed=1,
        environment_hash="c" * 64,
        image_digest="none",
        isolation_tier="subprocess",
        git_commit="0" * 40,
    )
    repo.finish_run(run.run_id, status=RunStatus.COMPLETED, telemetry={"cpu_seconds": 1.0})
    return run.run_id


def test_harness_cannot_produce_a_holdout_metric(repo: Repository, scaffold: Scaffold) -> None:
    run_id = _completed_run(repo, scaffold)

    with pytest.raises(InvariantViolation, match="computed by the Custodian"):
        repo.record_result(
            run_id=run_id,
            split=Split.HOLDOUT,
            metric="macro_f1",
            value=0.91,
            artifact_hash="d" * 64,
            computed_by=ComputedBy.HARNESS,
        )


def test_analyst_cannot_record_a_holdout_metric_even_claiming_custody(
    repo: Repository, scaffold: Scaffold
) -> None:
    """Asserting ``computed_by='custodian'`` does not make you the Custodian."""
    run_id = _completed_run(repo, scaffold)
    replicator = repo.as_role(Role.REPLICATOR)

    with pytest.raises(InvariantViolation, match="the Custodian holds the test split"):
        replicator.record_result(
            run_id=run_id,
            split=Split.HOLDOUT,
            metric="macro_f1",
            value=0.99,
            artifact_hash="d" * 64,
            computed_by=ComputedBy.CUSTODIAN,
        )


def test_custodian_can_record_a_holdout_metric(repo: Repository, scaffold: Scaffold) -> None:
    run_id = _completed_run(repo, scaffold)
    custodian = repo.as_role(Role.CUSTODIAN)

    result = custodian.record_result(
        run_id=run_id,
        split=Split.HOLDOUT,
        metric="macro_f1",
        value=0.887,
        artifact_hash="d" * 64,
        computed_by=ComputedBy.CUSTODIAN,
    )
    assert result.computed_by is ComputedBy.CUSTODIAN


def test_database_refuses_a_harness_authored_holdout_metric(
    repo: Repository, scaffold: Scaffold
) -> None:
    """The CHECK constraint, reached directly."""
    run_id = _completed_run(repo, scaffold)
    repo.commit()

    engine = repo.session.get_bind()
    assert isinstance(engine, sa.Engine)

    with pytest.raises(sa.exc.IntegrityError, match="ck_holdout_custodian_only"):
        repo.session.execute(
            sa.text(
                "INSERT INTO run_results (result_id, run_id, split, metric, value,"
                " computed_by, artifact_hash)"
                " VALUES (:id, :run, 'holdout', 'macro_f1', 0.99, 'harness', :art)"
            ),
            {
                "id": sql_uuid(engine, uuid.uuid4()),
                "run": sql_uuid(engine, run_id),
                "art": "d" * 64,
            },
        )


# ---------------------------------------------------------------------------
# Authority
# ---------------------------------------------------------------------------


def test_theorist_cannot_register_an_experiment(repo: Repository, scaffold: Scaffold) -> None:
    hypothesis_id = make_hypothesis(repo, scaffold)
    theorist = repo.as_role(Role.THEORIST)

    with pytest.raises(AuthorityError, match="may not register"):
        _register(theorist, hypothesis_id)


def test_skeptic_cannot_record_results(repo: Repository, scaffold: Scaffold) -> None:
    run_id = _completed_run(repo, scaffold)
    skeptic = repo.as_role(Role.SKEPTIC)

    with pytest.raises(AuthorityError, match="may not record_result"):
        skeptic.record_result(
            run_id=run_id,
            split=Split.DEV,
            metric="macro_f1",
            value=0.9,
            artifact_hash="d" * 64,
        )


#: Public repository callables that read or manage the session rather than
#: mutating institutional state. Everything else must declare its authority.
NON_MUTATING = frozenset(
    {
        "as_role",
        "audit_trail",
        "commit",
        "get_hypothesis",
        "get_registration",
        "open_critical_objections",
        "results_for_run",
        "rollback",
    }
)


def test_every_write_operation_declares_its_authority() -> None:
    """No write may exist without an explicit authority entry.

    This is what stops a future milestone from adding a mutating method that
    every role can call because nobody remembered to restrict it.
    """
    import inspect

    from nullius.repository import WRITE_AUTHORITY

    public_writes = {
        name
        for name, member in inspect.getmembers(Repository, inspect.isfunction)
        if not name.startswith("_") and name not in NON_MUTATING
    }
    assert public_writes == set(WRITE_AUTHORITY), (
        "every mutating repository method must appear in WRITE_AUTHORITY; "
        f"undeclared: {sorted(public_writes - set(WRITE_AUTHORITY))}"
    )


def test_sql_uuid_matches_stored_representation(repo: Repository, scaffold: Scaffold) -> None:
    """Guard: raw-SQL tests must bind identifiers exactly as stored.

    SQLite stores a UUID as 32 hex characters with no dashes. Binding the
    dashed form matches zero rows, and a statement that matches nothing is
    refused by nothing — so an invariant test would pass while proving
    nothing. This test fails if that representation ever changes.
    """
    make_hypothesis(repo, scaffold)
    repo.commit()

    engine = repo.session.get_bind()
    assert isinstance(engine, sa.Engine)

    stored = repo.session.execute(sa.text("SELECT program_id FROM programs LIMIT 1")).scalar_one()
    assert stored == sql_uuid(engine, scaffold.program_id)

    matched = repo.session.execute(
        sa.text("SELECT count(*) FROM programs WHERE program_id = :p"),
        {"p": sql_uuid(engine, scaffold.program_id)},
    ).scalar_one()
    assert matched == 1, "raw SQL must be able to address a row the ORM wrote"
