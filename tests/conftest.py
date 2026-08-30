"""Shared fixtures.

Every test gets a real database with real triggers. Nothing here mocks the
enforcement layer — an invariant test that ran against a stubbed database
would prove nothing about the database we actually ship.

Time and identifiers are frozen and seeded so that a test can construct exact
adversarial orderings (a run dated before its own registration) and so that
assertions about hashes are stable.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from nullius.db.base import create_engine, create_schema, session_factory
from nullius.db.enums import Role
from nullius.repository import Repository
from nullius.store.cas import ContentStore
from nullius.util.clock import FrozenClock
from nullius.util.ids import DeterministicIds

EPOCH = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.UTC)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(EPOCH)


@pytest.fixture
def ids() -> DeterministicIds:
    return DeterministicIds("test")


@pytest.fixture
def engine(tmp_path: Path) -> sa.Engine:
    """A real, freshly created database with all invariants installed."""
    engine = create_engine(tmp_path / "test.sqlite")
    create_schema(engine)
    return engine


@pytest.fixture
def session(engine: sa.Engine) -> Iterator[Session]:
    with session_factory(engine)() as session:
        yield session


@pytest.fixture
def repo(session: Session, clock: FrozenClock, ids: DeterministicIds) -> Repository:
    """A repository acting as the control plane."""
    return Repository(session, Role.SYSTEM, clock=clock, ids=ids)


@dataclass(frozen=True, slots=True)
class Scaffold:
    """A minimal but complete institution, ready for one hypothesis."""

    lab_id: uuid.UUID
    policy_id: uuid.UUID
    rq_id: uuid.UUID
    program_id: uuid.UUID


@pytest.fixture
def scaffold(repo: Repository) -> Scaffold:
    lab = repo.create_lab("Test Lab", "Investigate whether structure helps.")
    policy = repo.create_policy("v0.1", {"min_seeds": 5}, "Baseline policy.")
    rq = repo.create_research_question(
        "Does divergence-based feature pruning improve OOD macro-F1?",
        domain="tabular-ml",
    )
    program = repo.create_program(
        rq_id=rq.rq_id,
        lab_id=lab.lab_id,
        policy_id=policy.policy_id,
        budget_usd=Decimal("25.00"),
        config_hash="0" * 64,
        capability_digest="1" * 64,
    )
    return Scaffold(
        lab_id=lab.lab_id,
        policy_id=policy.policy_id,
        rq_id=rq.rq_id,
        program_id=program.program_id,
    )


@pytest.fixture
def store(tmp_path: Path) -> ContentStore:
    return ContentStore(tmp_path / "objects")


def sql_uuid(engine: sa.Engine, value: uuid.UUID) -> str:
    """A UUID in the exact form the database stores it.

    Raw-SQL tests must bind identifiers the way SQLAlchemy wrote them.
    SQLite's ``Uuid`` type stores 32 hex characters with no dashes, so binding
    ``str(value)`` matches zero rows — and a statement that matches nothing is
    refused by nothing, which makes an invariant test pass for the wrong
    reason. :func:`test_sql_uuid_matches_stored_representation` guards this.
    """
    return value.hex if engine.dialect.name == "sqlite" else str(value)


def make_hypothesis(repo: Repository, scaffold: Scaffold, statement: str = "H") -> uuid.UUID:
    """A well-formed hypothesis: named metric, direction, effect size, refutation."""
    hypothesis = repo.create_hypothesis(
        program_id=scaffold.program_id,
        statement=statement,
        mechanism="Shifted non-causal features carry no invariant signal.",
        primary_metric="macro_f1",
        direction="increase",
        mde=0.02,
        falsification_condition="95% CI for the paired difference excludes +0.02.",
    )
    return hypothesis.hypothesis_id
