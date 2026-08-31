"""Invariants: the evaluation split belongs to the Custodian, and looks are counted."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from nullius.build.compiler import compile_spec
from nullius.custody.custodian import (
    CUSTODY_SEED_FLOOR,
    BudgetExhausted,
    HoldoutCustodian,
    custody_seed,
)
from nullius.db.enums import ComputedBy, Role, RunStatus, Split
from nullius.db.tables import HoldoutQuery, RunResult
from nullius.errors import AuthorityError, InvariantViolation, NulliusError
from nullius.execute.runner import ExperimentRunner
from nullius.execute.sandbox import SubprocessSandbox
from nullius.ledger.rebuild import reconciliation
from nullius.repository import Repository
from nullius.store.cas import ContentStore
from nullius.util.ids import EXPERIMENT_SEED_CEILING
from tests.conftest import Scaffold, make_hypothesis
from tests.test_execution import SPEC

pytestmark = pytest.mark.invariant


@pytest.fixture
def registered(repo: Repository, scaffold: Scaffold) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """A locked registration and one recorded run per declared seed.

    One run per seed, because that is what the runner produces and because
    each seed's evaluation sample is derived from its own seed.
    """
    hypothesis_id = make_hypothesis(repo, scaffold)
    dataset = repo.record_dataset(
        name="covariate_shift", version="1", content_hash="a" * 64, licence="synthetic"
    )
    bundle = repo.record_code_bundle(content_hash="b" * 64, validator_report={}, passed=True)
    registration = repo.register(
        hypothesis_id=hypothesis_id,
        spec=SPEC.model_dump(mode="json"),
        analysis_plan={"test": "paired_bootstrap", "correction": "holm"},
        seed_root=SPEC.seed_root,
        n_seeds=SPEC.n_seeds,
        holdout_query_budget=2,
    )
    run_ids: list[uuid.UUID] = []
    for seed in SPEC.seeds():
        run = repo.start_run(
            registration_id=registration.registration_id,
            bundle_id=bundle.bundle_id,
            dataset_id=dataset.dataset_id,
            seed=seed,
            environment_hash="c" * 64,
            image_digest="none",
            isolation_tier="subprocess",
            git_commit="0" * 40,
            program_id=scaffold.program_id,
        )
        repo.finish_run(
            run.run_id, status=RunStatus.COMPLETED, telemetry={}, program_id=scaffold.program_id
        )
        run_ids.append(run.run_id)
    return registration.registration_id, run_ids


def _custodian(repo: Repository) -> HoldoutCustodian:
    return HoldoutCustodian(repo.as_role(Role.CUSTODIAN))


# ---------------------------------------------------------------------------
# Only the Custodian, and only through the Custodian
# ---------------------------------------------------------------------------


def test_the_custodian_cannot_be_constructed_as_another_role(repo: Repository) -> None:
    """Otherwise another role's identity would end up on holdout metrics."""
    for role in (Role.SYSTEM, Role.ANALYST, Role.REPLICATOR, Role.SKEPTIC):
        with pytest.raises(NulliusError, match="must act as 'custodian'"):
            HoldoutCustodian(repo.as_role(role))


def test_no_other_role_can_record_a_holdout_metric(
    repo: Repository, scaffold: Scaffold, registered: tuple[uuid.UUID, list[uuid.UUID]]
) -> None:
    """The acceptance criterion, stated exhaustively over every role."""
    _, run_ids = registered
    run_id = run_ids[0]

    for role in Role:
        if role is Role.CUSTODIAN:
            continue
        actor = repo.as_role(role)
        with pytest.raises((InvariantViolation, AuthorityError)):
            actor.record_result(
                run_id=run_id,
                split=Split.HOLDOUT,
                metric="prune.macro_f1",
                value=0.99,
                artifact_hash="d" * 64,
                computed_by=ComputedBy.CUSTODIAN,
            )


def test_only_the_custodian_can_record_having_looked(
    repo: Repository, registered: tuple[uuid.UUID, list[uuid.UUID]]
) -> None:
    registration_id, _ = registered
    for role in (Role.SYSTEM, Role.ANALYST, Role.REPLICATOR):
        with pytest.raises(AuthorityError, match="may not record_holdout_query"):
            repo.as_role(role).record_holdout_query(
                registration_id=registration_id,
                artifact_hash="e" * 64,
                granted=True,
                remaining_budget=5,
            )


@pytest.mark.slow
def test_a_full_run_produces_no_holdout_metric_until_custody(
    repo: Repository, scaffold: Scaffold, tmp_path: Path
) -> None:
    """An experiment can execute completely without ever touching the split."""
    hypothesis_id = make_hypothesis(repo, scaffold)
    dataset = repo.record_dataset(
        name="covariate_shift", version="1", content_hash="a" * 64, licence="synthetic"
    )
    bundle = repo.record_code_bundle(content_hash="b" * 64, validator_report={}, passed=True)
    registration = repo.register(
        hypothesis_id=hypothesis_id,
        spec=SPEC.model_dump(mode="json"),
        analysis_plan={"test": "paired_bootstrap"},
        seed_root=SPEC.seed_root,
        n_seeds=SPEC.n_seeds,
        holdout_query_budget=3,
    )
    runner = ExperimentRunner(
        repo, SubprocessSandbox(), ContentStore(tmp_path / "objects"), tmp_path / "runs"
    )
    outcomes = runner.run(
        SPEC,
        registration_id=registration.registration_id,
        bundle_id=bundle.bundle_id,
        dataset_id=dataset.dataset_id,
        program_id=scaffold.program_id,
    )
    repo.commit()

    assert set(repo.session.scalars(sa.select(RunResult.split))) == {Split.DEV}

    custodian = _custodian(repo)
    result = custodian.evaluate(
        registration_id=registration.registration_id,
        runs=[(o.run_id, compile_spec(SPEC, seed=o.seed)) for o in outcomes],
        program_id=scaffold.program_id,
    )
    repo.commit()

    assert set(result.per_seed[outcomes[0].seed]) == {"full", "prune", "random"}
    assert set(repo.session.scalars(sa.select(RunResult.split))) == {Split.DEV, Split.HOLDOUT}

    holdout_rows = list(
        repo.session.scalars(sa.select(RunResult).where(RunResult.split == Split.HOLDOUT))
    )
    assert holdout_rows
    assert all(row.computed_by is ComputedBy.CUSTODIAN for row in holdout_rows)

    assert repo.ledger.verify().ok
    assert reconciliation(repo.session).ok


# ---------------------------------------------------------------------------
# The evaluation sample is not reachable from the plan
# ---------------------------------------------------------------------------


@pytest.mark.isolation
def test_the_custody_seed_is_in_a_range_nothing_else_uses() -> None:
    """Three actors, three disjoint seed ranges, by construction."""
    for seed in SPEC.seeds():
        derived = custody_seed(uuid.uuid4(), seed)
        assert derived >= CUSTODY_SEED_FLOOR
        assert derived > EXPERIMENT_SEED_CEILING
        assert seed < EXPERIMENT_SEED_CEILING


@pytest.mark.isolation
def test_the_custody_seed_appears_nowhere_in_the_plan() -> None:
    """A plan that contained it would let the experiment redraw the split."""
    import json

    registration_id = uuid.uuid4()
    for seed in SPEC.seeds():
        plan = compile_spec(SPEC, seed=seed)
        derived = custody_seed(registration_id, seed)
        assert str(derived) not in json.dumps(plan)


def test_the_custody_seed_is_deterministic_but_registration_specific() -> None:
    """Re-evaluable on the same sample; different for a different registration."""
    first, second = uuid.uuid4(), uuid.uuid4()
    assert custody_seed(first, 7) == custody_seed(first, 7)
    assert custody_seed(first, 7) != custody_seed(second, 7)
    assert custody_seed(first, 7) != custody_seed(first, 8)


# ---------------------------------------------------------------------------
# The query budget
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_exhausting_the_budget_blocks_further_access_and_is_an_event(
    repo: Repository, scaffold: Scaffold, registered: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """The acceptance criterion: exhaustion is recorded and it blocks."""
    registration_id, run_ids = registered
    custodian = _custodian(repo)
    seeds = SPEC.seeds()

    assert custodian.remaining_budget(registration_id) == 2
    for index in range(2):
        custodian.evaluate(
            registration_id=registration_id,
            runs=[(run_ids[index], compile_spec(SPEC, seed=seeds[index]))],
            program_id=scaffold.program_id,
        )
    assert custodian.remaining_budget(registration_id) == 0

    with pytest.raises(BudgetExhausted, match="used all 2 of its holdout queries"):
        custodian.evaluate(
            registration_id=registration_id,
            runs=[(run_ids[2], compile_spec(SPEC, seed=seeds[2]))],
            program_id=scaffold.program_id,
        )
    repo.commit()

    queries = list(
        repo.session.scalars(
            sa.select(HoldoutQuery).where(HoldoutQuery.registration_id == registration_id)
        )
    )
    assert len(queries) == 3, "the refused attempt is recorded too"
    assert [q.granted for q in queries] == [True, True, False]

    events = [e.event_type for e in repo.ledger.events()]
    assert events.count("holdout.queried") == 2
    assert events.count("holdout.refused") == 1
    assert reconciliation(repo.session).ok


@pytest.mark.slow
def test_a_refused_query_produces_no_metric(
    repo: Repository, scaffold: Scaffold, registered: tuple[uuid.UUID, list[uuid.UUID]]
) -> None:
    """Blocking must actually block, not merely complain afterwards."""
    registration_id, run_ids = registered
    custodian = _custodian(repo)
    seeds = SPEC.seeds()

    for index in range(2):
        custodian.evaluate(
            registration_id=registration_id,
            runs=[(run_ids[index], compile_spec(SPEC, seed=seeds[index]))],
            program_id=scaffold.program_id,
        )
    repo.commit()
    before = len(list(repo.session.scalars(sa.select(RunResult))))

    with pytest.raises(BudgetExhausted):
        custodian.evaluate(
            registration_id=registration_id,
            runs=[(run_ids[2], compile_spec(SPEC, seed=seeds[2]))],
            program_id=scaffold.program_id,
        )
    repo.commit()

    assert len(list(repo.session.scalars(sa.select(RunResult)))) == before


def test_an_unlocked_registration_does_not_open_the_split(
    repo: Repository, scaffold: Scaffold
) -> None:
    """A design that can still change is not entitled to an evaluation."""
    custodian = _custodian(repo)
    with pytest.raises(NulliusError, match="no registration"):
        custodian.evaluate(
            registration_id=uuid.uuid4(),
            runs=[(uuid.uuid4(), compile_spec(SPEC, seed=SPEC.seeds()[0]))],
        )


def test_a_plan_cannot_be_evaluated_against_another_seeds_run(
    repo: Repository, scaffold: Scaffold, registered: tuple[uuid.UUID, list[uuid.UUID]]
) -> None:
    """The evaluation sample is derived from the seed, so the pair must match.

    Otherwise metrics measured on one sample would be recorded against a run
    that never saw it.
    """
    registration_id, run_ids = registered
    custodian = _custodian(repo)

    with pytest.raises(NulliusError, match="never saw"):
        custodian.evaluate(
            registration_id=registration_id,
            runs=[(run_ids[0], compile_spec(SPEC, seed=SPEC.seeds()[1]))],
            program_id=scaffold.program_id,
        )
