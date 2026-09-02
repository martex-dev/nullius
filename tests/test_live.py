"""Surviving a live run: retries, refusals, spend caps, and paying once.

A ladder makes thousands of calls over several hours against a paid endpoint.
The failure modes that matter there are not the ones a mock exercises — a mock
never rate-limits, never overloads, never drops a connection, and never costs
anything when a run has to be started again.

These tests are about the parts that only matter when money and a network are
involved, and they run without either.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from nullius.errors import BudgetExceeded, NulliusError
from nullius.llm.anthropic_provider import ProviderRefusal
from nullius.llm.factory import PROVIDER_NAMES, build_provider, require_live_credentials
from nullius.llm.retry import Backoff, RetryingProvider, is_transient
from nullius.llm.types import LlmRequest, LlmResponse, Message, ModelRef, Usage

MODEL = ModelRef(provider="mock", model="mock-1", effort=None, thinking=None)
REQUEST = LlmRequest(model=MODEL, system="s", messages=(Message(role="user", content="c"),))


def _response(text: str = "{}") -> LlmResponse:
    return LlmResponse(
        text=text, model="mock-1", stop_reason="end_turn", usage=Usage(), structured={}
    )


class _Status(Exception):
    """Stands in for an SDK error carrying an HTTP status."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


@dataclass
class _Flaky:
    """Fails a given number of times, then succeeds."""

    failures: int
    status: int = 429
    name: str = "flaky"
    calls: int = 0

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        if self.calls <= self.failures:
            raise _Status(self.status)
        return _response()


# ------------------------------------------------------------------- retries


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 504, 529])
def test_the_statuses_worth_retrying(status: int) -> None:
    """429 is a rate limit and 529 is Anthropic's overloaded. Over a ladder of
    thousands of calls, meeting one is a certainty rather than a risk."""
    assert is_transient(_Status(status))


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_a_permanent_failure_is_not_retried(status: int) -> None:
    """These fail identically however often they are sent, so retrying turns a
    clear error into a slow one."""
    assert not is_transient(_Status(status))


def test_timeouts_and_dropped_connections_are_transient() -> None:
    assert is_transient(TimeoutError("read timed out"))
    assert is_transient(ConnectionError("connection reset"))


def test_an_unrecognised_error_is_raised_rather_than_retried() -> None:
    """Six identical unknown failures separated by sleeps help nobody and hide
    the original."""
    assert not is_transient(ValueError("something else entirely"))


def test_a_run_survives_a_rate_limit() -> None:
    inner = _Flaky(failures=3)
    slept: list[float] = []
    provider = RetryingProvider(
        inner, backoff=Backoff(attempts=6, base_seconds=0.0), sleep=slept.append
    )

    assert provider.complete(REQUEST).model == "mock-1"
    assert inner.calls == 4
    assert len(provider.retries) == 3
    assert len(slept) == 3


def test_retries_are_bounded_and_the_last_failure_is_raised() -> None:
    """A ladder that retries for ever is a ladder that has hung."""
    inner = _Flaky(failures=99)
    provider = RetryingProvider(
        inner, backoff=Backoff(attempts=4, base_seconds=0.0), sleep=lambda _: None
    )

    with pytest.raises(_Status):
        provider.complete(REQUEST)
    assert inner.calls == 4


def test_a_permanent_error_fails_on_the_first_attempt() -> None:
    inner = _Flaky(failures=99, status=400)
    provider = RetryingProvider(
        inner, backoff=Backoff(attempts=6, base_seconds=0.0), sleep=lambda _: None
    )

    with pytest.raises(_Status):
        provider.complete(REQUEST)
    assert inner.calls == 1


def test_backoff_grows_and_is_jittered() -> None:
    """Full jitter, because a ladder runs its arms in sequence against one
    endpoint and identical doubling sequences re-collide on every attempt."""
    import random

    backoff = Backoff(attempts=6, base_seconds=1.0, max_seconds=60.0)
    delays = list(backoff.delays(random.Random(0)))

    assert len(delays) == 5
    assert all(0.0 <= d <= min(60.0, 2.0**index) for index, d in enumerate(delays))
    assert backoff.worst_case_seconds == pytest.approx(31.0)


# ------------------------------------------------------------------ refusals


def test_a_refusal_is_recorded_rather_than_raised(repo, scaffold) -> None:  # type: ignore[no-untyped-def]
    """A refusal is an answer, not an outage. Retrying it pays for the same
    answer; raising it ends a ladder over one role's prompt."""
    from nullius.db.enums import Role
    from nullius.db.tables import Task
    from nullius.roles.contracts import contracts_for
    from nullius.runtime.contracts import TaskStatus
    from nullius.runtime.worker import Worker

    class _Refusing:
        name = "refusing"

        def complete(self, request: LlmRequest) -> LlmResponse:
            raise ProviderRefusal("declined to answer")

    worker = Worker(repo, _Refusing(), contracts_for(mock=True))
    task = worker.queue.enqueue(
        program_id=scaffold.program_id,
        role=Role.THEORIST,
        contract_version="v1",
        subject_type="research_questions",
        subject_id=scaffold.rq_id,
        allowance_usd=Decimal("0.50"),
        view={"question": "does pruning help?", "item_id": "B01"},
    )
    result = worker.execute(task)

    assert result.status is TaskStatus.FAILED
    assert result.payload is None

    # The refusal reaches the ledger, which is the point: a role whose task is
    # consistently declined is a fact about this institution's prompts.
    stored = repo.session.get(Task, task.task_id)
    assert stored is not None
    assert "refused" in (stored.failure_reason or "")


# --------------------------------------------------------------- spend caps


def test_the_spend_guard_stops_a_ladder_at_its_cap() -> None:
    """The budget machinery caps a programme, and the benchmark gives every
    item its own — so nothing was watching the total, which is the number that
    matters when a run is left going overnight."""
    from nullius.benchmark.runner import SpendGuard

    guard = SpendGuard(cap_usd=Decimal("1.00"))
    guard.charge(Decimal("0.40"))
    guard.check()
    assert guard.remaining == Decimal("0.60")

    guard.charge(Decimal("0.70"))
    with pytest.raises(BudgetExceeded, match="max-usd"):
        guard.check()


def test_no_cap_means_no_ceiling_rather_than_a_default_one() -> None:
    """A hierarchy that invented a ceiling nobody chose would attribute its
    first refusal to a policy that does not exist."""
    from nullius.benchmark.runner import SpendGuard

    guard = SpendGuard()
    guard.charge(Decimal("1000"))
    guard.check()
    assert guard.remaining is None


# ------------------------------------------------------- provider selection


def test_a_live_run_without_credentials_refuses_before_spending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`detect_live_provider` used only to print a row in `doctor`, where a
    reader could note the absence and start a run anyway."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(NulliusError, match="no live credentials"):
        require_live_credentials("anthropic")
    require_live_credentials("mock")  # never gated


def test_an_unknown_provider_is_refused() -> None:
    with pytest.raises(NulliusError, match="unknown provider"):
        build_provider("gpt")
    assert PROVIDER_NAMES == ("mock", "anthropic", "replay")


def test_replay_needs_somewhere_to_replay_from() -> None:
    with pytest.raises(NulliusError, match="cache directory"):
        build_provider("replay")


def test_the_cache_makes_a_repeat_free(tmp_path: Path) -> None:
    """ADR-0005's whole argument: the first live run is the only one that costs.

    The cache sits *outside* retry, so a call that succeeded on its fourth
    attempt is written once and every later run of the same request is free.
    """
    from nullius.llm.cache import ResponseCache
    from nullius.llm.providers import CachingProvider

    inner = _Flaky(failures=2)
    retrying = RetryingProvider(
        inner, backoff=Backoff(attempts=6, base_seconds=0.0), sleep=lambda _: None
    )
    cached = CachingProvider(retrying, ResponseCache(tmp_path / "cache"))

    first = cached.complete(REQUEST)
    calls_after_first = inner.calls
    second = cached.complete(REQUEST)

    assert first.model == second.model
    assert calls_after_first == 3  # two failures, then the success
    assert inner.calls == 3  # the repeat reached the network not at all


def test_a_replay_miss_refuses_to_fall_through_to_the_network(tmp_path: Path) -> None:
    """A replay that quietly went live would be neither reproducible nor free."""
    from nullius.llm.providers import ReplayCacheMiss

    provider = build_provider("replay", cache_dir=tmp_path / "cache")
    with pytest.raises(ReplayCacheMiss):
        provider.complete(REQUEST)


# ------------------------------------------------------------ the first spend


def test_the_estimate_covers_every_role_a_cycle_dispatches() -> None:
    """It covered three of five. The two it missed are the Skeptic, which reads
    the whole evidence bundle, and the Reviewer — and a cost estimate that
    silently prices part of the work is the number someone buys credit from."""
    from nullius.costing import estimate_programme, every_contract

    roles = {role.value for role, _ in every_contract()}
    assert {"theorist", "designer", "analyst", "skeptic", "reviewer"} <= roles

    estimate = estimate_programme()
    priced = {call.role for call in estimate.calls}
    assert "skeptic" in priced and "reviewer" in priced


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="no ANTHROPIC_API_KEY: the live smoke test is skipped, not failed",
)
@pytest.mark.slow
def test_one_bank_item_end_to_end_against_the_real_api(tmp_path: Path) -> None:
    """The first real spend, deliberately one item.

    Skips cleanly with no key so the suite is green on a machine that has none,
    and runs a single bank item through the whole institution when there is
    one. The cache directory is inside ``tmp_path``, so this pays once per
    invocation rather than once ever — that is the right trade for a smoke
    test, whose job is to prove the wiring works now and not to be cheap.
    """
    from nullius.bank.items import BANK_V2
    from nullius.bank.lock import V2_LOCK_PATH
    from nullius.benchmark.arms import arm_named
    from nullius.benchmark.runner import run_arm

    run = run_arm(
        arm_named("B4"),
        database=tmp_path / "live.sqlite",
        workroot=tmp_path / "work",
        items=[i for i in BANK_V2 if i.item_id == "C01"],
        truth_lock=V2_LOCK_PATH,
        provider_name="anthropic",
        cache_dir=tmp_path / "cache",
    )

    assert len(run.outcomes) == 1
    outcome = run.outcomes[0]
    assert outcome.item_id == "C01"
    # A live call costs something. A zero here would mean the pricing table did
    # not recognise the model, which is how a live run reports itself as free.
    assert outcome.usd > Decimal(0)


# ------------------------------------------------- checkpoints under failure


@pytest.mark.slow
def test_a_mid_run_api_failure_leaves_finished_arms_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checkpointing was built for a crash while *writing results* — M20 salvaged
    eight arms from one. An API failure mid-arm is a different shape: it lands
    inside the loop rather than after it, and the arm in flight has already
    spent money without producing an outcome.

    What must hold is that arms already finished are not re-run, because on a
    live provider re-running them is the entire cost of the failure.
    """
    from nullius.bank.items import BANK_V2
    from nullius.bank.lock import V2_LOCK_PATH
    from nullius.benchmark.arms import arm_named
    from nullius.benchmark.runner import run_ladder
    from nullius.economy import outcomes as outcomes_module

    items = [i for i in BANK_V2 if i.item_id in ("C28", "C30")]
    arms = (arm_named("B3"), arm_named("B4"))
    base = outcomes_module.canned_responder()
    state = {"calls": 0, "limit": 10_000}

    def failing(request: LlmRequest) -> object:
        state["calls"] += 1
        if state["calls"] > state["limit"]:
            raise _Status(500)
        return base(request)

    monkeypatch.setattr(outcomes_module, "canned_responder", lambda: failing)

    # Enough calls for B3's two items, not enough to finish B4.
    state["limit"] = 12
    root = tmp_path / "ladder"
    with pytest.raises(_Status):
        run_ladder(root=root, arms=arms, items=items, truth_lock=V2_LOCK_PATH)

    assert (root / "b3.outcomes.json").exists(), "a finished arm was not checkpointed"
    assert not (root / "b4.outcomes.json").exists(), "an unfinished arm was checkpointed"

    # Resume with the failure lifted. B3 must come back from disk, so the only
    # calls this pass makes are B4's.
    state["limit"] = 10_000
    state["calls"] = 0
    runs = run_ladder(root=root, arms=arms, items=items, truth_lock=V2_LOCK_PATH)

    assert [r.arm.arm_id for r in runs] == ["B3", "B4"]
    assert all(len(r.outcomes) == len(items) for r in runs)
    assert state["calls"] > 0, "B4 should have been executed"
    assert state["calls"] <= 12, "B3 was re-run instead of resumed from its checkpoint"
