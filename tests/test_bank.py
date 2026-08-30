"""M4 acceptance: the bank's truth is measured, locked, and unreachable."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from nullius.bank import BANK_V1, MDE, classify, compute_truths, read_lock, validate_bank
from nullius.bank.items import _FORBIDDEN_IN_QUESTIONS
from nullius.bank.lock import DEFAULT_LOCK_PATH, verify, write_lock
from nullius.bank.oracle import ORACLE_SEED_OFFSET, measure_effect
from nullius.bank.truth import MIN_BOUNDARY_MARGIN, ambiguous, boundary_margin
from nullius.db.enums import Verdict


@pytest.fixture(scope="module")
def locked() -> dict[str, object]:
    return read_lock(DEFAULT_LOCK_PATH)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Composition: the bank must be able to punish a system that always says yes
# ---------------------------------------------------------------------------


def test_the_bank_is_structurally_valid() -> None:
    report = validate_bank()
    assert report.ok, str(report)


def test_at_least_45_percent_of_items_are_true_nulls(locked: dict[str, object]) -> None:
    """docs/04's composition rule. A yes-machine has to score badly."""
    counts = Counter(t.verdict.value for t in locked.values())  # type: ignore[attr-defined]
    assert counts["no_effect"] / len(locked) >= 0.45


def test_the_bank_spans_the_declared_effect_in_both_directions(
    locked: dict[str, object],
) -> None:
    """The decision of 2026-08-30: mixed effect sizes, not one regime."""
    counts = Counter(t.verdict.value for t in locked.values())  # type: ignore[attr-defined]
    assert counts[Verdict.SUPPORTED.value] >= 3
    assert counts[Verdict.REFUTED.value] >= 3
    assert counts[Verdict.INCONCLUSIVE.value] >= 2, (
        "the 'real but smaller than claimed' band is what makes this a "
        "calibration test rather than a detection test"
    )


def test_no_truth_sits_on_a_verdict_boundary(locked: dict[str, object]) -> None:
    """A truth the oracle cannot decide cannot score an institution."""
    truths = list(locked.values())
    unclear = ambiguous(truths)  # type: ignore[arg-type]
    assert not unclear, f"ambiguous items: {unclear}"
    assert all(boundary_margin(t) >= MIN_BOUNDARY_MARGIN for t in truths)  # type: ignore[arg-type]


def test_every_item_has_a_locked_truth(locked: dict[str, object]) -> None:
    assert {item.item_id for item in BANK_V1} == set(locked)


# ---------------------------------------------------------------------------
# Isolation: an agent cannot read the answer off the question
# ---------------------------------------------------------------------------


@pytest.mark.isolation
def test_a_question_never_names_the_generating_process() -> None:
    """An agent told which family of features moved need not run an experiment."""
    for item in BANK_V1:
        lowered = item.question.lower()
        for word in _FORBIDDEN_IN_QUESTIONS:
            assert word not in lowered, f"{item.item_id} leaks {word!r}"


@pytest.mark.isolation
def test_the_agent_view_withholds_everything_that_would_give_it_away() -> None:
    for item in BANK_V1:
        view = item.agent_view()
        assert set(view) == {"item_id", "question", "primary_metric", "claimed_effect"}
        serialised = json.dumps(view)
        for param, value in item.generator_params.items():
            assert param not in serialised, f"{item.item_id} exposes {param}"
            # Only non-numeric values are checked: a bare number like "0.0" is
            # a substring of the claimed effect "0.02" and would fail for no
            # reason. The giveaway is the shift family name, not the magnitude.
            if isinstance(value, str):
                assert value not in serialised, f"{item.item_id} exposes {param}={value}"
        for defect in item.planted_defects:
            assert defect not in serialised
        assert item.notes not in serialised


@pytest.mark.isolation
def test_the_oracle_uses_seeds_no_experiment_reaches() -> None:
    """Agreeing with the truth must require estimating it, not reproducing it."""
    from nullius.design.spec import ExperimentSpec
    from tests.test_execution import SPEC

    assert isinstance(SPEC, ExperimentSpec)
    assert all(seed < ORACLE_SEED_OFFSET for seed in SPEC.seeds())


@pytest.mark.isolation
def test_bank_items_cannot_fix_the_sample_size() -> None:
    """The oracle must be free to measure at a scale no experiment is allowed."""
    for item in BANK_V1:
        assert "n_samples" not in item.generator_params
        assert "seed" not in item.generator_params


# ---------------------------------------------------------------------------
# Verdict classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("effect", "expected"),
    [
        (0.05, Verdict.SUPPORTED),
        (0.02, Verdict.SUPPORTED),
        (0.015, Verdict.INCONCLUSIVE),
        (0.010, Verdict.INCONCLUSIVE),
        (0.009, Verdict.NO_EFFECT),
        (0.0, Verdict.NO_EFFECT),
        (-0.009, Verdict.NO_EFFECT),
        (-0.015, Verdict.INCONCLUSIVE),
        (-0.02, Verdict.REFUTED),
        (-0.5, Verdict.REFUTED),
    ],
)
def test_verdicts_are_relative_to_the_claimed_effect(effect: float, expected: Verdict) -> None:
    assert classify(effect, MDE) is expected


def test_an_effect_smaller_than_claimed_is_not_the_same_as_no_effect() -> None:
    """The distinction the whole calibration band rests on."""
    assert classify(0.015, MDE) is not classify(0.001, MDE)


# ---------------------------------------------------------------------------
# The lock
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_the_lock_reproduces_from_the_generating_process() -> None:
    """Ground truth is re-derivable, not merely recorded."""
    result = verify(DEFAULT_LOCK_PATH)
    assert result.ok, str(result)
    assert result.checked == len(BANK_V1)


def test_the_lock_notices_a_changed_data_generating_process(tmp_path: Path) -> None:
    """The check that makes silent ground-truth drift impossible."""
    truths = compute_truths(BANK_V1[:1], n_samples=800, n_seeds=3)
    lock = tmp_path / "truth.lock.json"
    write_lock(truths, lock)

    payload = json.loads(lock.read_text(encoding="utf-8"))
    payload["truths"][0]["effect"] = 0.0  # pretend the answer was always zero
    payload["truths"][0]["verdict"] = Verdict.NO_EFFECT.value
    lock.write_text(json.dumps(payload), encoding="utf-8")

    result = verify(lock, n_samples=800, n_seeds=3)
    assert not result.ok
    assert "B01" in result.drifted


def test_the_lock_notices_when_the_items_themselves_change(tmp_path: Path) -> None:
    truths = compute_truths(BANK_V1[:1], n_samples=800, n_seeds=3)
    lock = tmp_path / "truth.lock.json"
    write_lock(truths, lock)

    payload = json.loads(lock.read_text(encoding="utf-8"))
    payload["items_hash"] = "0" * 64
    lock.write_text(json.dumps(payload), encoding="utf-8")

    result = verify(lock, n_samples=800, n_seeds=3)
    assert not result.ok
    assert result.items_changed


# ---------------------------------------------------------------------------
# The oracle
# ---------------------------------------------------------------------------


def test_the_oracle_is_deterministic() -> None:
    params = {"shift": "noise", "shift_strength": 2.0}
    first = measure_effect(
        item_id="probe", generator_params=params, mde=MDE, n_samples=800, n_seeds=4
    )
    second = measure_effect(
        item_id="probe", generator_params=params, mde=MDE, n_samples=800, n_seeds=4
    )
    assert first.effect == second.effect
    assert first.verdict is second.verdict


def test_the_oracle_reports_its_own_uncertainty() -> None:
    """A truth without an error bar cannot be checked for ambiguity."""
    truth = measure_effect(
        item_id="probe",
        generator_params={"shift": "spurious", "shift_strength": 2.0},
        mde=MDE,
        n_samples=800,
        n_seeds=6,
    )
    assert truth.standard_error > 0
    assert truth.oracle_seeds == 6
    assert truth.oracle_samples == 800


def test_the_oracle_knows_which_features_are_causal() -> None:
    """Known by construction, and kept out of every agent view."""
    truth = measure_effect(
        item_id="probe",
        generator_params={"shift": "noise", "shift_strength": 1.0},
        mde=MDE,
        n_samples=400,
        n_seeds=2,
    )
    assert truth.causal_features == ("causal_0", "causal_1", "causal_2", "causal_3")
