"""Invariants under arbitrary operation sequences.

The other invariant tests each construct one adversarial case by hand. These
generate sequences instead, and assert that the guarantees hold after *every*
step regardless of the order operations arrive in — which is the situation an
autonomous institution actually creates, where a Director interleaves work
across hypotheses and roles.

Databases are built inside the test body rather than by fixture: Hypothesis
reuses function-scoped fixtures across examples, which would let state leak
between them and quietly weaken the property.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

import pytest
import sqlalchemy as sa
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from nullius.db.base import create_engine, create_schema, session_factory
from nullius.db.enums import ComputedBy, ObjectionSeverity, ObjectionType, Role, RunStatus, Split
from nullius.db.tables import RunResult
from nullius.errors import AuthorityError, InvariantViolation
from nullius.ledger.rebuild import reconciliation
from nullius.repository import Repository
from nullius.util.clock import FrozenClock
from nullius.util.ids import DeterministicIds

pytestmark = pytest.mark.invariant

EPOCH = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.UTC)


class Op(StrEnum):
    """The alphabet of things an institution does."""

    HYPOTHESISE = "hypothesise"
    REGISTER = "register"
    FORECAST = "forecast"
    RUN = "run"
    RESULT_DEV = "result_dev"
    RESULT_HOLDOUT = "result_holdout"
    OBJECT = "object"
    ADVANCE_TIME = "advance_time"


class _World:
    """A live institution the operations act on."""

    def __init__(self, path: Path) -> None:
        engine = create_engine(path)
        create_schema(engine)
        self.session = session_factory(engine)()
        self.clock = FrozenClock(EPOCH)
        self.ids = DeterministicIds("prop")
        self.repo = Repository(self.session, Role.SYSTEM, clock=self.clock, ids=self.ids)

        lab = self.repo.create_lab("L", "charter")
        policy = self.repo.create_policy("v1", {}, "rationale")
        rq = self.repo.create_research_question("q", "tabular-ml")
        program = self.repo.create_program(
            rq_id=rq.rq_id,
            lab_id=lab.lab_id,
            policy_id=policy.policy_id,
            budget_usd=Decimal("10.00"),
            config_hash="0" * 64,
            capability_digest="1" * 64,
        )
        self.program_id = program.program_id
        self.dataset = self.repo.record_dataset(
            name="d", version="1", content_hash="a" * 64, licence="CC0"
        )
        self.bundle = self.repo.record_code_bundle(
            content_hash="b" * 64, validator_report={}, passed=True
        )
        self.hypotheses: list[uuid.UUID] = []
        self.registrations: list[uuid.UUID] = []
        self.runs: list[uuid.UUID] = []
        self.counter = 0

    def apply(self, op: Op) -> None:
        """Perform ``op`` if its preconditions hold. Refusals are expected."""
        self.counter += 1
        repo = self.repo
        try:
            match op:
                case Op.ADVANCE_TIME:
                    self.clock.advance(60)
                case Op.HYPOTHESISE:
                    h = repo.as_role(Role.THEORIST).create_hypothesis(
                        program_id=self.program_id,
                        statement=f"H-{self.counter}",
                        mechanism="mechanism",
                        primary_metric="macro_f1",
                        direction="increase",
                        mde=0.02,
                        falsification_condition="CI excludes the MDE.",
                    )
                    self.hypotheses.append(h.hypothesis_id)
                case Op.REGISTER if self.hypotheses:
                    r = repo.as_role(Role.DESIGNER).register(
                        hypothesis_id=self.hypotheses[-1],
                        spec={"arms": ["full", "prune"], "n": self.counter},
                        analysis_plan={"test": "paired_bootstrap"},
                        seed_root=self.counter,
                        n_seeds=2,
                        holdout_query_budget=2,
                    )
                    self.registrations.append(r.registration_id)
                case Op.FORECAST if self.registrations:
                    repo.as_role(Role.SKEPTIC).record_forecast(
                        registration_id=self.registrations[-1],
                        p_effect_exceeds_mde=0.3,
                        predictive_mean=0.01,
                        predictive_sd=0.02,
                        p_execution_success=0.9,
                    )
                case Op.RUN if self.registrations:
                    run = repo.start_run(
                        registration_id=self.registrations[-1],
                        bundle_id=self.bundle.bundle_id,
                        dataset_id=self.dataset.dataset_id,
                        seed=self.counter,
                        environment_hash="c" * 64,
                        image_digest="none",
                        isolation_tier="subprocess",
                        git_commit="0" * 40,
                    )
                    repo.finish_run(run.run_id, status=RunStatus.COMPLETED, telemetry={})
                    self.runs.append(run.run_id)
                case Op.RESULT_DEV if self.runs:
                    repo.record_result(
                        run_id=self.runs[-1],
                        split=Split.DEV,
                        metric="macro_f1",
                        value=0.5,
                        artifact_hash="d" * 64,
                    )
                case Op.RESULT_HOLDOUT if self.runs:
                    repo.as_role(Role.CUSTODIAN).record_result(
                        run_id=self.runs[-1],
                        split=Split.HOLDOUT,
                        metric="macro_f1",
                        value=0.5,
                        artifact_hash="e" * 64,
                        computed_by=ComputedBy.CUSTODIAN,
                    )
                case Op.OBJECT if self.runs:
                    repo.as_role(Role.SKEPTIC).raise_objection(
                        target_type="runs",
                        target_id=self.runs[-1],
                        objection_type=ObjectionType.SEED_INSTABILITY,
                        severity=ObjectionSeverity.MAJOR,
                        statement="Too few seeds.",
                        discriminating_test={"n_seeds": 5},
                    )
                case _:
                    return
        except (InvariantViolation, AuthorityError):
            # A refusal is a correct outcome, not a test failure. What matters
            # is that the ledger is intact afterwards either way.
            self.session.rollback()
            return
        self.repo.commit()

    def close(self) -> None:
        self.session.close()


@settings(
    max_examples=75,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(operations=st.lists(st.sampled_from(list(Op)), min_size=1, max_size=14))
def test_invariants_survive_arbitrary_operation_sequences(
    tmp_path_factory: pytest.TempPathFactory, operations: list[Op]
) -> None:
    """After every step, whatever the order: the ledger holds and state folds."""
    world = _World(tmp_path_factory.mktemp("prop") / "n.sqlite")
    try:
        for op in operations:
            world.apply(op)

            chain = world.repo.ledger.verify()
            assert chain.ok, f"after {op}: {chain}"

        report = reconciliation(world.session)
        assert report.ok, f"after {operations}: {report}"

        # No holdout number reached the ledger from anyone but the Custodian,
        # whatever sequence produced it.
        offenders = world.session.scalars(
            sa.select(RunResult).where(
                RunResult.split == Split.HOLDOUT,
                RunResult.computed_by != ComputedBy.CUSTODIAN,
            )
        ).all()
        assert not offenders

        # Every run is preceded by the registration it tests.
        rows = world.session.execute(
            sa.text(
                "SELECT r.started_at, g.registered_at FROM runs r"
                " JOIN registrations g ON g.registration_id = r.registration_id"
            )
        ).all()
        for started_at, registered_at in rows:
            assert registered_at <= started_at
    finally:
        world.close()
