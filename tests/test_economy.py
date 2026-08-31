"""M9 acceptance: the research economy, and whether allocation is worth anything.

The headline test does not assert that greedy-EIG wins. The milestone's
criterion is that it *measurably* beats random **or is shown not to**, so a
test that demanded a win would be a test that could only pass by the project
getting a particular answer — which is the failure this whole repository is
built to avoid. What is asserted is that the comparison is well formed, that
its interval is honest, and that the result is reported either way.
"""

from __future__ import annotations

import math
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from nullius.db.enums import Role, Verdict
from nullius.economy.cost_model import MINIMUM_OBSERVATIONS, CostModel, CostObservation
from nullius.economy.director import Allocator, candidates_for_program
from nullius.economy.eig import (
    CHANCE_BRIER,
    Gaussian,
    RoleForecast,
    calibration_weights,
    disagreement,
    expected_information_gain,
    measurement_sd,
    posterior,
)
from nullius.economy.harness import (
    RecordedForecasts,
    SyntheticForecasts,
    candidates_from_outcomes,
    compare_policies,
    default_policies,
    score_policy,
    sweep_informativeness,
)
from nullius.economy.outcomes import ItemOutcome, outcomes_are_current, read_outcomes
from nullius.economy.policy import (
    POLICIES,
    Candidate,
    CandidateKind,
    CheapestFirst,
    GreedyEig,
    GroupHistory,
    RandomAllocation,
    Reserves,
    RoundRobin,
    ThompsonSampling,
    policy_named,
)
from nullius.errors import AuthorityError
from nullius.llm.pricing import usd_for_compute
from nullius.repository import Repository
from nullius.runtime.budget import BudgetEnvelope, BudgetLedger, BudgetLevel
from nullius.runtime.contracts import TaskStatus
from nullius.runtime.queue import TaskQueue
from tests.conftest import Scaffold, make_hypothesis

OUTCOMES_LOCK = Path("bank/outcomes.lock.json")

PRIOR = Gaussian(0.0, 0.02)


def _forecast(mean: float, sd: float, role: Role = Role.THEORIST) -> RoleForecast:
    return RoleForecast(
        role=role,
        predictive_mean=mean,
        predictive_sd=sd,
        p_effect_exceeds_mde=0.5,
        p_execution_success=0.95,
    )


def _candidate(
    label: str,
    *,
    eig: float = 1.0,
    cost: str = "0.01",
    p_success: float = 1.0,
    kind: CandidateKind = CandidateKind.EXPLORATION,
    group: str = "",
) -> Candidate:
    return Candidate(
        subject_id=uuid.uuid5(uuid.NAMESPACE_OID, label),
        label=label,
        eig=eig,
        p_success=p_success,
        expected_cost_usd=Decimal(cost),
        kind=kind,
        group=group,
    )


# ---------------------------------------------------------------------------
# Expected information gain
# ---------------------------------------------------------------------------


def test_the_conjugate_update_is_the_textbook_one() -> None:
    """Known answer: equal prior and noise halve the variance and average the means."""
    updated = posterior(Gaussian(0.0, 1.0), observation=2.0, noise_sd=1.0)

    assert updated.mean == pytest.approx(1.0)
    assert updated.variance == pytest.approx(0.5)


def test_the_vectorised_gain_agrees_with_the_scalar_definition() -> None:
    """The fast path is pinned to the slow one it replaced.

    Recomputed here from :func:`posterior` alone, one draw at a time, so an
    optimisation that quietly changed the quantity would fail rather than pass
    faster.
    """
    import numpy as np

    forecasts = [_forecast(0.03, 0.01)]
    report = expected_information_gain(forecasts, prior=PRIOR, noise_sd=0.005, draws=512, seed=7)

    rng = np.random.default_rng(7)
    rng.choice(1, size=512, p=np.asarray([1.0]))
    observations = rng.normal(np.asarray([0.03])[np.zeros(512, dtype=int)], 0.01)
    by_hand = []
    for y in observations:
        post = posterior(PRIOR, float(y), 0.005)
        by_hand.append(
            math.log(PRIOR.sd / post.sd)
            + (post.variance + (post.mean - PRIOR.mean) ** 2) / (2 * PRIOR.variance)
            - 0.5
        )

    assert report.nats == pytest.approx(float(np.mean(by_hand)), rel=1e-9)


def test_roles_that_disagree_make_an_experiment_worth_more() -> None:
    """The property the architecture claims: wide disagreement, high EIG."""
    agreed = [_forecast(0.0, 0.01, Role.THEORIST), _forecast(0.0, 0.01, Role.DESIGNER)]
    disputed = [_forecast(-0.05, 0.01, Role.THEORIST), _forecast(0.05, 0.01, Role.DESIGNER)]

    quiet = expected_information_gain(agreed, prior=PRIOR, noise_sd=0.004, seed=1)
    loud = expected_information_gain(disputed, prior=PRIOR, noise_sd=0.004, seed=1)

    assert disagreement(disputed) > disagreement(agreed) == 0.0
    assert loud.nats > quiet.nats


def test_identical_forecasts_carry_no_information_about_which_item_to_fund() -> None:
    """The mock's actual situation, stated as a test.

    Every item scoring the same EIG is not a bug in the allocator; it is what a
    forecaster that says one thing about everything produces. The comparison
    reports it rather than working around it.
    """
    same = [_forecast(0.05, 0.04)]
    first = expected_information_gain(same, prior=PRIOR, noise_sd=0.006, seed=3)
    second = expected_information_gain(same, prior=PRIOR, noise_sd=0.006, seed=3)

    assert first.nats == second.nats
    assert first.disagreement == 0.0


def test_a_sharper_experiment_teaches_more_than_a_blunt_one() -> None:
    sharp = expected_information_gain([_forecast(0.04, 0.02)], prior=PRIOR, noise_sd=0.001, seed=5)
    blunt = expected_information_gain([_forecast(0.04, 0.02)], prior=PRIOR, noise_sd=0.500, seed=5)

    assert sharp.nats > blunt.nats
    assert blunt.nats < 0.05, "a measurement drowned in noise should teach almost nothing"


def test_more_seeds_measure_more_precisely() -> None:
    assert measurement_sd(0.01, 20) < measurement_sd(0.01, 5)
    with pytest.raises(ValueError):
        measurement_sd(0.0, 5)


def test_a_role_no_better_than_chance_is_given_no_weight() -> None:
    """And the fallback is uniform rather than a division by zero."""
    forecasts = [_forecast(0.0, 0.01, Role.THEORIST), _forecast(0.1, 0.01, Role.DESIGNER)]

    weights = calibration_weights(forecasts, {Role.THEORIST: 0.0, Role.DESIGNER: CHANCE_BRIER})
    assert weights.tolist() == pytest.approx([1.0, 0.0])

    hopeless = calibration_weights(
        forecasts, {Role.THEORIST: CHANCE_BRIER, Role.DESIGNER: CHANCE_BRIER}
    )
    assert hopeless.tolist() == pytest.approx([0.5, 0.5])


def test_a_forecast_with_no_spread_is_refused() -> None:
    with pytest.raises(ValueError):
        Gaussian(0.0, 0.0)


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", sorted(POLICIES))
def test_every_policy_ranks_every_candidate_exactly_once(version: str) -> None:
    """A ranking that dropped a candidate would shelve it without a reason."""
    candidates = [_candidate(f"C{i}", eig=float(i), cost=f"0.0{i + 1}") for i in range(6)]

    ordered = policy_named(version).rank(candidates)

    assert sorted(c.label for c in ordered) == sorted(c.label for c in candidates)


def test_greedy_funds_in_descending_nats_per_dollar() -> None:
    cheap_and_dull = _candidate("dull", eig=0.1, cost="0.01")
    dear_and_rich = _candidate("rich", eig=10.0, cost="0.02")

    order = GreedyEig().rank([cheap_and_dull, dear_and_rich])

    assert [c.label for c in order] == ["rich", "dull"]


def test_the_cost_control_ignores_information_entirely() -> None:
    """The capacity-matched arm of the allocation comparison."""
    order = CheapestFirst().rank(
        [_candidate("dear", eig=100.0, cost="0.05"), _candidate("cheap", eig=0.0, cost="0.01")]
    )

    assert [c.label for c in order] == ["cheap", "dear"]


def test_round_robin_will_not_let_one_line_of_research_take_everything() -> None:
    """Two strong candidates from one group cannot both precede another group's."""
    candidates = [
        _candidate("a1", eig=10.0, group="A"),
        _candidate("a2", eig=9.0, group="A"),
        _candidate("b1", eig=1.0, group="B"),
    ]

    greedy = [c.family for c in GreedyEig().rank(candidates)]
    fair = [c.family for c in RoundRobin().rank(candidates)]

    assert greedy == ["A", "A", "B"], "greedy would spend both slots on one line"
    assert fair[:2] == ["A", "B"]


def test_random_allocation_is_reproducible_and_seed_dependent() -> None:
    candidates = [_candidate(f"C{i}") for i in range(8)]

    first = [c.label for c in RandomAllocation(seed=1).rank(candidates)]
    again = [c.label for c in RandomAllocation(seed=1).rank(candidates)]
    other = [c.label for c in RandomAllocation(seed=2).rank(candidates)]

    assert first == again
    assert first != other


def test_thompson_gives_a_barren_line_of_research_less_of_the_budget() -> None:
    """History moves the ranking; without it the two are ordered by score alone."""
    candidates = [
        _candidate("proven", eig=1.0, group="P"),
        _candidate("barren", eig=1.0, group="B"),
    ]
    history = {
        "P": GroupHistory(informative=20, uninformative=0),
        "B": GroupHistory(informative=0, uninformative=20),
    }

    informed = [c.family for c in ThompsonSampling(seed=4, history=history).rank(candidates)]

    assert informed[0] == "P"


def test_an_unspent_replication_reserve_does_not_pay_for_exploration() -> None:
    """F14, enforced rather than intended.

    The exploration candidates here would consume the whole budget if the
    reserve leaked, so this fails loudly if the fence is ever removed.
    """
    reserves = Reserves(replication=0.5, null_confirmation=0.0)
    hungry = [_candidate(f"E{i}", cost="0.30") for i in range(4)]

    unfenced = GreedyEig().allocate(hungry, budget_usd=Decimal("1.00"), reserves=Reserves(0.0, 0.0))
    assert len(unfenced.funded) == 3, "without a fence, exploration takes the whole budget"

    fenced = GreedyEig().allocate(hungry, budget_usd=Decimal("1.00"), reserves=reserves)
    assert len(fenced.funded) == 1
    assert fenced.spent_by_kind[CandidateKind.REPLICATION] == Decimal(0)
    assert fenced.committed_usd <= Decimal("0.50")


def test_reserves_divide_the_whole_budget_and_nothing_more() -> None:
    pockets = Reserves(replication=0.2, null_confirmation=0.15).split(Decimal("10.00"))

    assert sum(pockets.values()) == Decimal("10.00")
    assert pockets[CandidateKind.REPLICATION] == Decimal("2.000")


def test_reserves_that_leave_nothing_to_explore_with_are_refused() -> None:
    with pytest.raises(ValueError):
        Reserves(replication=0.7, null_confirmation=0.3)


def test_the_shelved_are_recorded_with_why() -> None:
    allocation = GreedyEig().allocate(
        [_candidate("affordable", cost="0.01"), _candidate("dear", cost="9.99")],
        budget_usd=Decimal("0.05"),
        reserves=Reserves(0.0, 0.0),
    )
    inputs = allocation.as_inputs()

    assert [c.label for c in allocation.funded] == ["affordable"]
    assert inputs["shelved"][0]["label"] == "dear"
    assert "reserve has" in inputs["shelved"][0]["reason"]


def test_a_free_candidate_is_refused_rather_than_winning_everything() -> None:
    with pytest.raises(ValueError):
        _candidate("free", cost="0")


# ---------------------------------------------------------------------------
# Cost model
# ---------------------------------------------------------------------------


def test_the_cost_model_says_when_it_is_guessing() -> None:
    model = CostModel.fit([], fallback_usd=Decimal("0.05"))

    assert not model.fitted
    assert model.predict(n_seeds=5, n_arms=3) == Decimal("0.05")
    assert "unfitted" in str(model)


def test_the_cost_model_recovers_a_known_linear_relationship() -> None:
    """Costs built as ``0.01 + 0.002·seeds + 0.003·arms`` are read back."""
    observations = [
        CostObservation(
            registration_id=uuid.uuid4(),
            n_seeds=seeds,
            n_arms=arms,
            usd=Decimal(str(0.01 + 0.002 * seeds + 0.003 * arms)),
        )
        for seeds, arms in ((3, 2), (5, 2), (5, 3), (10, 3), (10, 4), (20, 4))
    ]

    model = CostModel.fit(observations, fallback_usd=Decimal("1.00"))

    assert model.n_observations >= MINIMUM_OBSERVATIONS
    assert model.fitted
    assert model.per_seed == pytest.approx(0.002, abs=1e-6)
    assert model.per_arm == pytest.approx(0.003, abs=1e-6)
    assert float(model.predict(n_seeds=7, n_arms=3)) == pytest.approx(0.033, abs=1e-5)


def test_the_cost_model_never_predicts_a_free_experiment() -> None:
    """Extrapolation below the fitted range is floored, not allowed negative."""
    observations = [
        CostObservation(uuid.uuid4(), n_seeds=seeds, n_arms=2, usd=Decimal(str(seeds * 0.01 - 0.5)))
        for seeds in (60, 70, 80, 90, 100)
    ]

    model = CostModel.fit(observations, fallback_usd=Decimal("0.05"))

    assert model.predict(n_seeds=1, n_arms=1) > 0


# ---------------------------------------------------------------------------
# Hierarchical budgets
# ---------------------------------------------------------------------------


def _spend(repo: Repository, scaffold: Scaffold, task_id: uuid.UUID, usd: str) -> None:
    repo.record_cost(
        program_id=scaffold.program_id,
        task_id=task_id,
        usd=Decimal(usd),
        price_table_version="test",
    )


def test_a_hypothesis_cap_binds_below_a_programme_that_could_still_afford_it(
    repo: Repository, scaffold: Scaffold
) -> None:
    """The refusal must name the hypothesis, and the programme must be solvent.

    The first enqueue proves the same allowance is accepted when only the
    programme is checked, so the second refusal is caused by the hypothesis cap
    and not by a budget that was exhausted all along.
    """
    queue = TaskQueue(repo)
    hypothesis_id = make_hypothesis(repo, scaffold)
    envelope = BudgetEnvelope(hypothesis_id=hypothesis_id, hypothesis_cap_usd=Decimal("0.10"))

    permitted = queue.enqueue(
        program_id=scaffold.program_id,
        role=Role.THEORIST,
        contract_version="v1",
        subject_type="hypotheses",
        subject_id=hypothesis_id,
        allowance_usd=Decimal("0.05"),
        view={},
        envelope=envelope,
    )
    assert permitted.status == TaskStatus.PENDING.value

    _spend(repo, scaffold, permitted.task_id, "0.08")

    refused = queue.enqueue(
        program_id=scaffold.program_id,
        role=Role.THEORIST,
        contract_version="v1",
        subject_type="hypotheses",
        subject_id=hypothesis_id,
        allowance_usd=Decimal("0.05"),
        view={},
        envelope=envelope,
    )

    assert refused.status == TaskStatus.REFUSED_BUDGET.value
    assert refused.failure_reason is not None
    assert "hypothesis" in refused.failure_reason
    ledger = BudgetLedger(repo)
    assert ledger.status(scaffold.program_id).remaining_usd > Decimal("1.00")


def test_an_institutional_cap_binds_above_a_solvent_programme(
    repo: Repository, scaffold: Scaffold
) -> None:
    ledger = BudgetLedger(repo)
    hypothesis_id = make_hypothesis(repo, scaffold)
    queue = TaskQueue(repo)

    task = queue.enqueue(
        program_id=scaffold.program_id,
        role=Role.THEORIST,
        contract_version="v1",
        subject_type="hypotheses",
        subject_id=hypothesis_id,
        allowance_usd=Decimal("0.05"),
        view={},
    )
    assert task.status == TaskStatus.PENDING.value, "the programme can afford this"
    _spend(repo, scaffold, task.task_id, "0.95")

    ruling = ledger.rule(
        scaffold.program_id,
        Decimal("0.10"),
        BudgetEnvelope(lab_id=scaffold.lab_id, lab_cap_usd=Decimal("1.00")),
    )

    assert not ruling.allowed
    assert ruling.level is BudgetLevel.INSTITUTION
    assert ledger.status(scaffold.program_id).can_afford(Decimal("0.10"))


def test_a_hypothesis_is_charged_for_both_its_model_calls_and_its_compute(
    repo: Repository, scaffold: Scaffold
) -> None:
    """Two routes into the cost ledger, one total, no double counting."""
    ledger = BudgetLedger(repo)
    hypothesis_id = make_hypothesis(repo, scaffold)
    queue = TaskQueue(repo)

    assert ledger.hypothesis_spend(hypothesis_id) == Decimal(0)

    task = queue.enqueue(
        program_id=scaffold.program_id,
        role=Role.THEORIST,
        contract_version="v1",
        subject_type="hypotheses",
        subject_id=hypothesis_id,
        allowance_usd=Decimal("0.05"),
        view={},
    )
    _spend(repo, scaffold, task.task_id, "0.02")

    assert ledger.hypothesis_spend(hypothesis_id) == Decimal("0.02")


def test_compute_is_priced_and_is_not_free() -> None:
    """A mock-driven programme spends nothing on tokens; the seconds are real."""
    assert usd_for_compute(0.0) == Decimal(0)
    assert usd_for_compute(10.0) > Decimal(0)
    assert usd_for_compute(20.0) == 2 * usd_for_compute(10.0)
    with pytest.raises(ValueError):
        usd_for_compute(-1.0)


# ---------------------------------------------------------------------------
# The Director's decisions
# ---------------------------------------------------------------------------


def test_only_the_director_may_record_an_allocation_decision(
    repo: Repository, scaffold: Scaffold
) -> None:
    """And the operation is otherwise available, so the refusal means something."""
    allowed = repo.as_role(Role.DIRECTOR).record_decision(
        program_id=scaffold.program_id,
        policy_id=scaffold.policy_id,
        kind="fund",
        subject_id=uuid.uuid4(),
        inputs={"eig": 1.0},
        outcome="funded",
    )
    assert allowed.outcome == "funded"

    with pytest.raises(AuthorityError):
        repo.as_role(Role.THEORIST).record_decision(
            program_id=scaffold.program_id,
            policy_id=scaffold.policy_id,
            kind="fund",
            subject_id=uuid.uuid4(),
            inputs={"eig": 1.0},
            outcome="funded",
        )


def test_every_candidate_leaves_a_decision_row_funded_or_not(
    repo: Repository, scaffold: Scaffold
) -> None:
    """The counterfactual is recorded, not only the choice."""
    allocator = Allocator(
        repo=repo,
        policy=GreedyEig(),
        policy_id=scaffold.policy_id,
        reserves=Reserves(0.0, 0.0),
    )
    candidates = [_candidate("cheap", cost="0.01"), _candidate("dear", cost="9.99")]

    allocation = allocator.decide(
        candidates, program_id=scaffold.program_id, budget_usd=Decimal("0.05")
    )
    repo.commit()

    rows = repo.decisions_for_program(scaffold.program_id)
    assert len(rows) == 2
    assert {r.outcome for r in rows} == {"funded", "shelved"}
    assert len(allocation.funded) == 1

    shelved = next(r for r in rows if r.outcome == "shelved")
    assert "reason" in shelved.inputs
    assert shelved.inputs["allocation"]["policy_version"] == GreedyEig.version


def test_a_registration_nobody_forecast_is_not_scored(repo: Repository, scaffold: Scaffold) -> None:
    """Skipped rather than defaulted: an assumed EIG would compete with measured ones."""
    hypothesis_id = make_hypothesis(repo, scaffold)
    repo.as_role(Role.DESIGNER).register(
        hypothesis_id=hypothesis_id,
        spec={"arms": [{"name": "a"}, {"name": "b"}], "prior_sd": 0.01},
        analysis_plan={"test": "paired_bootstrap"},
        seed_root=1,
        n_seeds=5,
        holdout_query_budget=3,
        program_id=scaffold.program_id,
    )

    assert candidates_for_program(repo, scaffold.program_id) == []


def test_a_forecast_registration_becomes_a_scored_candidate(
    repo: Repository, scaffold: Scaffold
) -> None:
    hypothesis_id = make_hypothesis(repo, scaffold)
    registration = repo.as_role(Role.DESIGNER).register(
        hypothesis_id=hypothesis_id,
        spec={"arms": [{"name": "a"}, {"name": "b"}], "prior_sd": 0.01, "title": "T"},
        analysis_plan={"test": "paired_bootstrap"},
        seed_root=1,
        n_seeds=5,
        holdout_query_budget=3,
        program_id=scaffold.program_id,
    )
    for role, mean in ((Role.THEORIST, 0.04), (Role.DESIGNER, -0.01)):
        repo.as_role(role).record_forecast(
            registration_id=registration.registration_id,
            p_effect_exceeds_mde=0.5,
            predictive_mean=mean,
            predictive_sd=0.01,
            p_execution_success=0.9,
            program_id=scaffold.program_id,
        )

    candidates = candidates_for_program(repo, scaffold.program_id)

    assert len(candidates) == 1
    assert candidates[0].label == "T"
    assert candidates[0].eig > 0
    assert candidates[0].p_success == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# The harness, on invented outcomes
# ---------------------------------------------------------------------------


def _outcome(
    item_id: str,
    *,
    correct: bool,
    usd: str,
    effect: float = 0.03,
    margin: float = 5.0,
) -> ItemOutcome:
    return ItemOutcome(
        item_id=item_id,
        verdict=Verdict.SUPPORTED if correct else Verdict.NO_EFFECT,
        truth_verdict=Verdict.SUPPORTED,
        true_effect=effect,
        boundary_margin=margin,
        realised_effect=effect,
        usd=Decimal(usd),
        llm_usd=Decimal(usd),
        compute_usd=Decimal(0),
        n_seeds=5,
        n_arms=3,
        forecasts=(_forecast(0.05, 0.04),),
    )


def test_a_policy_that_buys_nothing_correct_has_a_defined_efficiency() -> None:
    """Infinite, not omitted. Dropping it would flatter the worst possible outcome."""
    outcomes = [_outcome("X1", correct=False, usd="0.01")]
    candidates, _ = candidates_from_outcomes(outcomes, RecordedForecasts())

    result = score_policy(
        GreedyEig(),
        candidates,
        {"X1": outcomes[0]},
        budget_usd=Decimal("1.00"),
        reserves=Reserves(0.0, 0.0),
    )

    assert result.correct == 0
    assert math.isinf(result.cost_per_correct_claim)
    assert result.correct_per_dollar == 0.0


def test_the_comparison_reports_every_policy_on_the_same_items() -> None:
    outcomes = [_outcome(f"X{i}", correct=i % 2 == 0, usd=f"0.0{i + 1}") for i in range(8)]

    report = compare_policies(outcomes, budget_usd=Decimal("0.10"), resamples=100, seed=0)

    assert report.n_items == 8
    assert {r.policy_version for r in report.results} == {p.version for p in default_policies()}
    for difference in report.differences:
        assert difference.ci_low <= difference.observed <= difference.ci_high
        assert difference.baseline_version == RandomAllocation.version


def test_identical_recorded_forecasts_leave_the_information_term_flat() -> None:
    """The precondition for reading the headline result correctly."""
    outcomes = [_outcome(f"X{i}", correct=True, usd="0.01") for i in range(5)]

    report = compare_policies(outcomes, budget_usd=Decimal("0.03"), resamples=50)

    assert report.eig_spread == pytest.approx(0.0, abs=1e-12)


def test_the_synthetic_source_puts_information_into_the_forecasts() -> None:
    """At the top of the ladder, items differ in EIG; at the bottom they do not."""
    outcomes = [
        _outcome("easy", correct=True, usd="0.01", effect=0.06, margin=12.0),
        _outcome("hard", correct=False, usd="0.01", effect=0.01, margin=0.4),
    ]

    flat, _ = candidates_from_outcomes(outcomes, SyntheticForecasts(informativeness=0.0))
    sharp, _ = candidates_from_outcomes(outcomes, SyntheticForecasts(informativeness=1.0))

    assert flat[0].eig == pytest.approx(flat[1].eig, abs=1e-9)
    assert sharp[0].eig != pytest.approx(sharp[1].eig, abs=1e-6)


def test_an_informativeness_outside_the_unit_interval_is_refused() -> None:
    with pytest.raises(ValueError):
        SyntheticForecasts(informativeness=1.5)


# ---------------------------------------------------------------------------
# M9 acceptance — measured over the bank
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bank_outcomes() -> list[ItemOutcome]:
    if not OUTCOMES_LOCK.exists():
        pytest.skip(f"{OUTCOMES_LOCK} not present; run `nullius economy measure`")
    return read_outcomes(OUTCOMES_LOCK)


def test_the_locked_outcomes_describe_the_bank_as_it_stands(
    bank_outcomes: list[ItemOutcome],
) -> None:
    """A measurement of a bank that has since changed scores nothing."""
    assert outcomes_are_current(OUTCOMES_LOCK)
    assert len(bank_outcomes) >= 15


def test_greedy_eig_against_random_over_the_bank(
    bank_outcomes: list[ItemOutcome],
) -> None:
    """The milestone's question, answered either way.

    What is asserted is that the comparison is well formed: a paired interval
    that brackets its own point estimate, over every policy, on the same items.
    Whether greedy-EIG wins is *reported*, not required — a test that demanded
    a particular scientific answer would be the thing this project exists to
    refuse.
    """
    report = compare_policies(bank_outcomes, budget_usd=Decimal("0.03"), resamples=400, seed=0)
    difference = report.difference_for(GreedyEig.version, "correct_per_dollar")

    assert difference.ci_low <= difference.observed <= difference.ci_high
    assert difference.resamples == 400
    assert difference.verdict in {
        "no measurable difference",
        "better than baseline",
        "worse than baseline",
    }

    print(f"\n  M9 result: {difference}")
    for result in report.results:
        print(f"    {result}")
    print(f"    EIG spread across items: {report.eig_spread:.9f} nats")


def test_the_cost_only_control_separates_mechanism_from_denominator(
    bank_outcomes: list[ItemOutcome],
) -> None:
    """Whatever greedy-EIG gains over cheapest-first is what the EIG term bought.

    With the recorded forecasts flat, the two should be indistinguishable — and
    if they are not, the difference is coming from the success probability,
    which is the only other term that varies.
    """
    report = compare_policies(bank_outcomes, budget_usd=Decimal("0.03"), resamples=400, seed=0)

    greedy = report.result_for(GreedyEig.version)
    control = report.result_for(CheapestFirst.version)

    assert report.eig_spread == pytest.approx(0.0, abs=1e-9), (
        "the recorded forecasts are identical across items, so any gap between "
        "greedy-EIG and the cost-only control cannot be attributed to information"
    )
    assert greedy.correct_per_dollar == pytest.approx(control.correct_per_dollar)


@pytest.mark.slow
def test_better_forecasts_are_worth_something_or_are_shown_not_to_be(
    bank_outcomes: list[ItemOutcome],
) -> None:
    """The dose-response curve for forecast quality.

    The top rung uses the bank's ground truth and is an upper bound on what any
    forecaster could be worth here — not a claim that a live system reaches it.
    """
    report = sweep_informativeness(bank_outcomes, budget_usd=Decimal("0.03"), resamples=200, seed=0)

    assert report.points[0].eig_spread == pytest.approx(0.0, abs=1e-9)
    assert report.points[-1].eig_spread > 0.0

    print("\n  forecast-quality sweep:")
    for point in report.points:
        print(f"    {point}")
    first = report.first_separating
    print(
        "    greedy-EIG never separates from random"
        if first is None
        else f"    first separates at lambda={first.informativeness:.2f}"
    )
