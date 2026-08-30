"""M2 acceptance: a role runs end to end, replays free, and respects a budget."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field

from nullius.db.enums import Role
from nullius.db.tables import LlmCall, Task
from nullius.ledger.rebuild import reconciliation
from nullius.llm.cache import ResponseCache
from nullius.llm.providers import (
    CachingProvider,
    MockProvider,
    ReplayCacheMiss,
    ReplayProvider,
)
from nullius.llm.types import LlmRequest, ModelRef
from nullius.repository import Repository
from nullius.runtime.budget import BudgetLedger
from nullius.runtime.contracts import (
    AgentTask,
    RoleContract,
    TaskStatus,
    ValidationFailure,
    register_validator,
    register_view,
)
from nullius.runtime.worker import Worker
from tests.conftest import Scaffold

# ---------------------------------------------------------------------------
# A minimal role: propose a hypothesis
# ---------------------------------------------------------------------------


class HypothesisDraft(BaseModel):
    """What a Theorist emits. Deliberately unable to express a vague guess."""

    statement: str = Field(min_length=10)
    primary_metric: str
    direction: str
    mde: float = Field(ge=0)
    falsification_condition: str = Field(min_length=10)


@register_view("test.rq_only")
def _rq_only(repo: Repository, task: AgentTask) -> dict[str, Any]:
    """The narrowest possible view: the question, and nothing else."""
    return {"question": task.view.get("question", "")}


@register_validator("test.direction_is_declared")
def _direction_is_declared(payload: BaseModel, view: dict[str, Any]) -> None:
    assert isinstance(payload, HypothesisDraft)
    if payload.direction not in {"increase", "decrease", "no_change"}:
        raise ValidationFailure(f"direction {payload.direction!r} is not one of the three")


MODEL = ModelRef(provider="mock", model="mock-1", max_tokens=1024)

CONTRACT = RoleContract(
    role=Role.THEORIST,
    version="v1",
    model=MODEL,
    system_prompt="You are the Theorist. Emit one falsifiable hypothesis.",
    input_view="test.rq_only",
    output_schema=HypothesisDraft,
    validators=("test.direction_is_declared",),
)

GOOD = {
    "statement": "Divergence-based pruning improves OOD macro-F1 when shifts are non-causal.",
    "primary_metric": "macro_f1",
    "direction": "increase",
    "mde": 0.02,
    "falsification_condition": "The 95% CI for the paired difference excludes +0.02.",
}


def _contracts() -> dict[tuple[Role, str], RoleContract]:
    return {(Role.THEORIST, "v1"): CONTRACT}


def _enqueue(worker: Worker, scaffold: Scaffold, allowance: str = "1.00") -> Task:
    return worker.queue.enqueue(
        program_id=scaffold.program_id,
        role=Role.THEORIST,
        contract_version="v1",
        subject_type="research_questions",
        subject_id=scaffold.rq_id,
        allowance_usd=Decimal(allowance),
        view={"question": "Does pruning shifted features improve OOD macro-F1?"},
    )


# ---------------------------------------------------------------------------
# Acceptance 1 — a role executes end to end
# ---------------------------------------------------------------------------


def test_a_role_runs_end_to_end(repo: Repository, scaffold: Scaffold) -> None:
    provider = MockProvider(lambda _request: GOOD)
    worker = Worker(repo, provider, _contracts())
    _enqueue(worker, scaffold)

    result = worker.run_once()

    assert result is not None
    assert result.ok, result.failure_reason
    assert isinstance(result.payload, HypothesisDraft)
    assert result.payload.primary_metric == "macro_f1"
    repo.commit()

    task = repo.session.get(Task, result.task_id)
    assert task is not None
    assert task.status == TaskStatus.SUCCEEDED.value
    assert task.result is not None

    assert repo.ledger.verify().ok
    assert reconciliation(repo.session).ok


def test_the_agent_sees_only_its_view(repo: Repository, scaffold: Scaffold) -> None:
    """Information asymmetry is what the prompt is built from, not an instruction."""
    provider = MockProvider(lambda _request: GOOD)
    worker = Worker(repo, provider, _contracts())
    _enqueue(worker, scaffold)
    worker.run_once()

    prompt = provider.calls[0].messages[0].content
    assert "Does pruning shifted features improve OOD macro-F1?" in prompt
    # Nothing about the wider institution leaked in.
    assert str(scaffold.program_id) not in prompt
    assert "budget" not in prompt.lower()
    assert "<view>" in prompt


def test_queue_empty_returns_none(repo: Repository, scaffold: Scaffold) -> None:
    worker = Worker(repo, MockProvider(lambda _r: GOOD), _contracts())
    assert worker.run_once() is None


# ---------------------------------------------------------------------------
# Acceptance 2 — replay is byte-identical and free
# ---------------------------------------------------------------------------


def test_replay_is_identical_and_costs_nothing(
    repo: Repository, scaffold: Scaffold, tmp_path: Path
) -> None:
    cache = ResponseCache(tmp_path / "llm")
    recording = CachingProvider(MockProvider(lambda _r: GOOD), cache)
    worker = Worker(repo, recording, _contracts())
    _enqueue(worker, scaffold)

    first = worker.run_once()
    assert first is not None and first.ok
    assert first.cache_hits == 0
    assert len(cache) == 1
    repo.commit()

    # A replay provider cannot reach the network by construction.
    replaying = ReplayProvider(cache)
    replay_worker = Worker(repo, replaying, _contracts())
    _enqueue(replay_worker, scaffold)

    second = replay_worker.run_once()
    assert second is not None and second.ok
    assert second.cache_hits == 1
    assert second.usd == Decimal(0), "a cache hit made no request and must cost nothing"

    assert first.payload is not None and second.payload is not None
    assert first.payload.model_dump() == second.payload.model_dump()


def test_replay_refuses_to_fall_through_to_a_live_call(tmp_path: Path) -> None:
    """A silent fall-through would make a replay neither reproducible nor free."""
    provider = ReplayProvider(ResponseCache(tmp_path / "empty"))
    request = LlmRequest(model=MODEL, system="s", messages=())

    with pytest.raises(ReplayCacheMiss, match="must not fall through"):
        provider.complete(request)


def test_cache_key_covers_every_generation_parameter(tmp_path: Path) -> None:
    """A field that changes the response but not the key would poison replays."""
    base = LlmRequest(model=MODEL, system="s", messages=())
    variants = [
        LlmRequest(model=MODEL, system="different", messages=()),
        LlmRequest(model=ModelRef("mock", "mock-1", max_tokens=2048), system="s", messages=()),
        LlmRequest(
            model=ModelRef("mock", "mock-1", max_tokens=1024, effort="low"),
            system="s",
            messages=(),
        ),
        LlmRequest(
            model=ModelRef("mock", "mock-1", max_tokens=1024, thinking="disabled"),
            system="s",
            messages=(),
        ),
        LlmRequest(model=ModelRef("other", "mock-1", max_tokens=1024), system="s", messages=()),
        LlmRequest(model=MODEL, system="s", messages=(), output_schema={"type": "object"}),
    ]
    keys = {base.cache_key(), *(v.cache_key() for v in variants)}
    assert len(keys) == len(variants) + 1


def test_llm_calls_are_recorded_for_audit(repo: Repository, scaffold: Scaffold) -> None:
    worker = Worker(repo, MockProvider(lambda _r: GOOD), _contracts())
    _enqueue(worker, scaffold)
    result = worker.run_once()
    assert result is not None
    repo.commit()

    import sqlalchemy as sa

    calls = list(repo.session.scalars(sa.select(LlmCall)))
    assert len(calls) == 1
    assert calls[0].cache_hit is False
    assert calls[0].task_id == result.task_id
    assert len(calls[0].cache_key) == 64


# ---------------------------------------------------------------------------
# Acceptance 3 — budget refusal at dispatch, recorded as an event
# ---------------------------------------------------------------------------


def test_task_exceeding_the_budget_is_refused_at_dispatch(
    repo: Repository, scaffold: Scaffold
) -> None:
    """Refused before a model is called, not after the money is gone."""
    worker = Worker(repo, MockProvider(lambda _r: GOOD), _contracts())

    task = _enqueue(worker, scaffold, allowance="10000.00")

    assert task.status == TaskStatus.REFUSED_BUDGET.value
    assert task.failure_reason is not None
    assert "exceeds remaining" in task.failure_reason
    repo.commit()

    events = [e.event_type for e in repo.ledger.events()]
    assert "task.refused_budget" in events
    assert "task.enqueued" not in events

    # A refused task is never claimable, so no model call can happen.
    assert worker.run_once() is None


def test_a_refusal_is_a_research_outcome_not_an_exception(
    repo: Repository, scaffold: Scaffold
) -> None:
    worker = Worker(repo, MockProvider(lambda _r: GOOD), _contracts())
    task = _enqueue(worker, scaffold, allowance="10000.00")
    repo.commit()

    assert reconciliation(repo.session).ok
    assert repo.ledger.verify().ok
    stored = repo.session.get(Task, task.task_id)
    assert stored is not None and stored.status == TaskStatus.REFUSED_BUDGET.value


def test_spend_accumulates_and_reduces_the_remaining_budget(
    repo: Repository, scaffold: Scaffold
) -> None:
    from nullius.llm.types import LlmResponse, Usage

    def expensive(request: LlmRequest) -> LlmResponse:
        return LlmResponse(
            text=__import__("json").dumps(GOOD),
            model="claude-opus-5",
            stop_reason="end_turn",
            usage=Usage(input_tokens=100_000, output_tokens=10_000),
            structured=GOOD,
        )

    worker = Worker(repo, MockProvider(expensive), _contracts())
    _enqueue(worker, scaffold)
    result = worker.run_once()
    assert result is not None and result.ok

    # 100k input at $5/Mtok + 10k output at $25/Mtok = $0.50 + $0.25
    assert result.usd == Decimal("0.75")

    ledger = BudgetLedger(repo)
    status = ledger.status(scaffold.program_id)
    assert status.spent_usd == Decimal("0.75")
    assert status.remaining_usd == Decimal("24.25")


# ---------------------------------------------------------------------------
# Failure handling — recorded, never retried into success
# ---------------------------------------------------------------------------


def test_malformed_output_gets_exactly_one_repair_attempt(
    repo: Repository, scaffold: Scaffold
) -> None:
    attempts: list[int] = []

    def flaky(request: LlmRequest) -> dict[str, Any]:
        attempts.append(len(attempts))
        return GOOD if len(attempts) > 1 else {"statement": "too short"}

    worker = Worker(repo, MockProvider(flaky), _contracts())
    _enqueue(worker, scaffold)
    result = worker.run_once()

    assert result is not None and result.ok
    assert result.calls == 2, "one initial call plus one repair"


def test_persistently_malformed_output_fails_rather_than_looping(
    repo: Repository, scaffold: Scaffold
) -> None:
    worker = Worker(repo, MockProvider(lambda _r: {"statement": "no"}), _contracts())
    _enqueue(worker, scaffold)
    result = worker.run_once()

    assert result is not None
    assert result.status is TaskStatus.FAILED
    assert result.calls == CONTRACT.max_calls_per_task
    assert result.failure_reason is not None
    assert "did not satisfy the schema" in result.failure_reason
    repo.commit()
    assert reconciliation(repo.session).ok


def test_validator_rejection_is_not_retried(repo: Repository, scaffold: Scaffold) -> None:
    """The shape was right and the content wrong — a fact about the role."""
    bad_direction = {**GOOD, "direction": "sideways"}
    provider = MockProvider(lambda _r: bad_direction)
    worker = Worker(repo, provider, _contracts())
    _enqueue(worker, scaffold)
    result = worker.run_once()

    assert result is not None
    assert result.status is TaskStatus.FAILED
    assert result.calls == 1, "a validator failure is not a schema failure; do not retry"
    assert result.failure_reason is not None
    assert "validator rejected" in result.failure_reason


def test_unknown_contract_fails_the_task_without_calling_a_model(
    repo: Repository, scaffold: Scaffold
) -> None:
    provider = MockProvider(lambda _r: GOOD)
    worker = Worker(repo, provider, {})
    _enqueue(worker, scaffold)
    result = worker.run_once()

    assert result is not None
    assert result.status is TaskStatus.FAILED
    assert provider.calls == []
    assert result.failure_reason is not None
    assert "no contract registered" in result.failure_reason


# ---------------------------------------------------------------------------
# Contract wiring
# ---------------------------------------------------------------------------


def test_a_contract_cannot_name_an_unregistered_view() -> None:
    with pytest.raises(KeyError, match="no input view named"):
        RoleContract(
            role=Role.THEORIST,
            version="v0",
            model=MODEL,
            system_prompt="s",
            input_view="does.not.exist",
            output_schema=HypothesisDraft,
        )


def test_a_contract_must_allow_at_least_one_call() -> None:
    with pytest.raises(ValueError, match="at least one call"):
        RoleContract(
            role=Role.THEORIST,
            version="v0",
            model=MODEL,
            system_prompt="s",
            input_view="test.rq_only",
            output_schema=HypothesisDraft,
            max_calls_per_task=0,
        )
