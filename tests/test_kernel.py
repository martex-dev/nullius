"""M6 acceptance: one question carried to a claim, with the ordering enforced."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from nullius.bank.items import BANK_V1, BankItem
from nullius.db.tables import Claim, Evidence, Forecast, ForecastScore, Registration, Run, RunResult
from nullius.execute.sandbox import SubprocessSandbox
from nullius.kernel import ResearchKernel
from nullius.ledger.rebuild import reconciliation
from nullius.llm.providers import MockProvider
from nullius.llm.types import LlmRequest
from nullius.repository import Repository
from nullius.store.cas import ContentStore
from tests.conftest import Scaffold

ITEM = next(item for item in BANK_V1 if item.item_id == "B01")

GOOD_HYPOTHESIS = {
    "statement": (
        "Dropping the features whose distributions differ most between environments "
        "will raise deployment macro-F1 relative to using every feature."
    ),
    "mechanism": (
        "Features whose relationship to the label is unstable across environments "
        "mislead a model that learned to rely on them during training."
    ),
    "primary_metric": "macro_f1",
    "direction": "increase",
    "mde": 0.02,
    "prior_sd": 0.006,
    "falsification_condition": (
        "If the interval for the paired difference does not exceed the claimed effect, "
        "the hypothesis is wrong."
    ),
    "assumptions": ["the deployment covariates are observable before labelling"],
}

GOOD_DESIGN = {
    "treatment_transform": "divergence_prune",
    "treatment_k": 3,
    "estimator": "logistic_regression",
    "include_capacity_control": True,
    "n_seeds": 5,
    "tuning_budget": 4,
    "rationale": (
        "Comparing against an arm that drops as many features at random separates "
        "the choice of features from the reduction in count."
    ),
}

GOOD_FORECAST = {
    "p_effect_exceeds_mde": 0.65,
    "predictive_mean": 0.05,
    "predictive_sd": 0.04,
    "p_execution_success": 0.95,
    "reasoning": (
        "The mechanism is plausible and the design is adequately powered for the "
        "effect it claims to detect."
    ),
}

GOOD_NOTE = {
    "interpretation": (
        "Dropping the most divergent features improved deployment performance by more "
        "than the amount claimed, consistent with those features having misled the model."
    ),
    "limitations": [
        "Only one family of distribution change was examined, on synthetic data.",
    ],
    "mechanism_supported": True,
    "alternative_explanation": (
        "The improvement could come from reducing the number of features rather than "
        "from which features were removed."
    ),
}


def _responder(overrides: dict[str, Any] | None = None):
    """A mock that answers according to which role is asking."""
    table = {
        "Theorist": GOOD_HYPOTHESIS,
        "Experiment Designer": GOOD_DESIGN,
        "Analyst": GOOD_NOTE,
        "predict an": GOOD_FORECAST,
    }
    table.update(overrides or {})

    def respond(request: LlmRequest) -> dict[str, Any]:
        for marker, payload in table.items():
            if marker in request.system:
                return payload
        raise AssertionError(f"no mock response for system prompt: {request.system[:60]}")

    return respond


@pytest.fixture
def kernel(repo: Repository, tmp_path: Path) -> ResearchKernel:
    return ResearchKernel(
        repo,
        MockProvider(_responder()),
        SubprocessSandbox(),
        ContentStore(tmp_path / "objects"),
        tmp_path / "runs",
        mock=True,
    )


# ---------------------------------------------------------------------------
# Acceptance 1 — one item, end to end
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_a_question_becomes_a_claim(
    repo: Repository, scaffold: Scaffold, kernel: ResearchKernel
) -> None:
    outcome = kernel.run_item(ITEM, program_id=scaffold.program_id)
    repo.commit()

    assert outcome.halted is None, outcome.halted
    assert outcome.completed
    assert outcome.hypothesis_id is not None
    assert outcome.registration_id is not None
    assert outcome.claim_id is not None
    assert outcome.verdict is not None
    assert outcome.confidence is not None
    assert outcome.note is not None

    assert repo.ledger.verify().ok
    assert reconciliation(repo.session).ok


# ---------------------------------------------------------------------------
# Acceptance 2 — the registration predates every run
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.invariant
def test_the_registration_hash_predates_every_run(
    repo: Repository, scaffold: Scaffold, kernel: ResearchKernel
) -> None:
    """The anti-HARKing ordering, checked on a real lifecycle rather than a fixture."""
    outcome = kernel.run_item(ITEM, program_id=scaffold.program_id)
    repo.commit()

    registration = repo.session.get(Registration, outcome.registration_id)
    assert registration is not None
    assert registration.locked

    runs = list(
        repo.session.scalars(sa.select(Run).where(Run.registration_id == outcome.registration_id))
    )
    assert runs
    for run in runs:
        assert registration.registered_at <= run.started_at


@pytest.mark.slow
@pytest.mark.invariant
def test_every_forecast_is_locked_before_the_first_run(
    repo: Repository, scaffold: Scaffold, kernel: ResearchKernel
) -> None:
    """A prediction made after results is not a prediction."""
    outcome = kernel.run_item(ITEM, program_id=scaffold.program_id)
    repo.commit()

    forecasts = list(
        repo.session.scalars(
            sa.select(Forecast).where(Forecast.registration_id == outcome.registration_id)
        )
    )
    assert len(forecasts) == 3, "the Theorist, Designer and Analyst each forecast"

    first_run = repo.session.scalars(
        sa.select(Run)
        .where(Run.registration_id == outcome.registration_id)
        .order_by(Run.started_at.asc())
        .limit(1)
    ).one()
    for forecast in forecasts:
        assert forecast.created_at <= first_run.started_at


@pytest.mark.slow
def test_forecasts_are_scored_against_what_happened(
    repo: Repository, scaffold: Scaffold, kernel: ResearchKernel
) -> None:
    outcome = kernel.run_item(ITEM, program_id=scaffold.program_id)
    repo.commit()

    scores = list(
        repo.session.scalars(
            sa.select(ForecastScore).where(ForecastScore.registration_id == outcome.registration_id)
        )
    )
    assert len(scores) == 3
    for score in scores:
        assert 0.0 <= score.brier_score <= 1.0
        assert score.crps >= 0.0
        assert outcome.analysis is not None
        assert score.realised_effect == pytest.approx(outcome.analysis.difference)


# ---------------------------------------------------------------------------
# Acceptance 3 — every number traces to a result row
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_every_claim_number_traces_to_a_result_row(
    repo: Repository, scaffold: Scaffold, kernel: ResearchKernel
) -> None:
    """Provenance, checked rather than assumed."""
    outcome = kernel.run_item(ITEM, program_id=scaffold.program_id)
    repo.commit()

    evidence = list(
        repo.session.scalars(sa.select(Evidence).where(Evidence.claim_id == outcome.claim_id))
    )
    assert evidence, "a claim with no evidence is not a claim"

    for row in evidence:
        assert row.result_id is not None
        result = repo.session.get(RunResult, row.result_id)
        assert result is not None
        assert len(result.artifact_hash) == 64


@pytest.mark.slow
def test_the_analyst_never_states_a_number(
    repo: Repository, scaffold: Scaffold, kernel: ResearchKernel
) -> None:
    """The claim's prose comes from the Analyst, and carries no figures."""
    outcome = kernel.run_item(ITEM, program_id=scaffold.program_id)
    repo.commit()

    claim = repo.session.get(Claim, outcome.claim_id)
    assert claim is not None
    assert not any(character.isdigit() for character in claim.statement)

    note = outcome.note
    assert note is not None
    text = json.dumps(note.model_dump())
    assert not any(character.isdigit() for character in text)


@pytest.mark.slow
def test_holdout_metrics_exist_and_come_only_from_the_custodian(
    repo: Repository, scaffold: Scaffold, kernel: ResearchKernel
) -> None:
    from nullius.db.enums import ComputedBy, Split

    kernel.run_item(ITEM, program_id=scaffold.program_id)
    repo.commit()

    holdout = list(
        repo.session.scalars(sa.select(RunResult).where(RunResult.split == Split.HOLDOUT))
    )
    assert holdout
    assert all(row.computed_by is ComputedBy.CUSTODIAN for row in holdout)


# ---------------------------------------------------------------------------
# The lifecycle refuses bad work rather than proceeding
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_a_design_without_a_capacity_control_is_refused_before_registration(
    repo: Repository, scaffold: Scaffold, tmp_path: Path
) -> None:
    """The planted defect. The linter must stop it before anything is locked."""
    careless = dict(GOOD_DESIGN, include_capacity_control=False)
    kernel = ResearchKernel(
        repo,
        MockProvider(_responder({"Experiment Designer": careless})),
        SubprocessSandbox(),
        ContentStore(tmp_path / "objects"),
        tmp_path / "runs",
        mock=True,
    )

    outcome = kernel.run_item(ITEM, program_id=scaffold.program_id)
    repo.commit()

    assert outcome.halted is not None
    assert "capacity_matched" in outcome.halted
    assert outcome.registration_id is None, "nothing may be registered after a refused design"
    assert not list(repo.session.scalars(sa.select(Registration)))
    assert reconciliation(repo.session).ok


@pytest.mark.slow
def test_an_underpowered_design_is_refused_before_it_runs(
    repo: Repository, scaffold: Scaffold, tmp_path: Path
) -> None:
    """A null that means "we did not look hard enough" never gets produced."""
    thin = dict(GOOD_DESIGN, n_seeds=3)
    tiny_effect = dict(GOOD_HYPOTHESIS, mde=0.001, prior_sd=0.05)
    kernel = ResearchKernel(
        repo,
        MockProvider(_responder({"Experiment Designer": thin, "Theorist": tiny_effect})),
        SubprocessSandbox(),
        ContentStore(tmp_path / "objects"),
        tmp_path / "runs",
        mock=True,
    )

    outcome = kernel.run_item(ITEM, program_id=scaffold.program_id)
    repo.commit()

    assert outcome.halted is not None
    assert "underpowered" in outcome.halted
    assert not list(repo.session.scalars(sa.select(Run)))


@pytest.mark.slow
def test_an_analyst_that_states_a_number_fails_its_task(
    repo: Repository, scaffold: Scaffold, tmp_path: Path
) -> None:
    """Rejected by a validator, not tidied up afterwards."""
    chatty = dict(GOOD_NOTE, interpretation="The effect was 5 macro-F1 points, which is large.")
    kernel = ResearchKernel(
        repo,
        MockProvider(_responder({"Analyst": chatty})),
        SubprocessSandbox(),
        ContentStore(tmp_path / "objects"),
        tmp_path / "runs",
        mock=True,
    )

    outcome = kernel.run_item(ITEM, program_id=scaffold.program_id)
    repo.commit()

    # The lifecycle still completes — the numbers were never the Analyst's to
    # give — but the note is absent and the claim falls back to the verdict.
    assert outcome.note is None
    assert outcome.claim_id is not None
    claim = repo.session.get(Claim, outcome.claim_id)
    assert claim is not None
    assert "Verdict" in claim.statement


@pytest.mark.slow
def test_a_budget_too_small_halts_the_lifecycle(
    repo: Repository, scaffold: Scaffold, tmp_path: Path
) -> None:
    """Refused at dispatch, and the halt is the outcome rather than a crash."""
    kernel = ResearchKernel(
        repo,
        MockProvider(_responder()),
        SubprocessSandbox(),
        ContentStore(tmp_path / "objects"),
        tmp_path / "runs",
        mock=True,
    )

    outcome = kernel.run_item(ITEM, program_id=scaffold.program_id, allowance=Decimal("1000.00"))
    repo.commit()

    assert outcome.halted == "theorist failed"
    assert outcome.hypothesis_id is None
    events = [e.event_type for e in repo.ledger.events()]
    assert "task.refused_budget" in events


def test_the_bank_item_view_is_all_the_theorist_receives() -> None:
    """No generator parameters reach the role that proposes the hypothesis."""
    view = ITEM.agent_view()
    assert set(view) == {"item_id", "question", "primary_metric", "claimed_effect"}
    assert "shift" not in json.dumps(view)


def test_a_bank_item_is_addressable_by_id() -> None:
    assert isinstance(ITEM, BankItem)
    assert ITEM.item_id == "B01"
