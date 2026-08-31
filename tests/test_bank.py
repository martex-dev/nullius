"""M4 acceptance: the bank's truth is measured, locked, and unreachable."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from nullius.bank import BANK_V1, MDE, classify, compute_truths, read_lock, validate_bank
from nullius.bank.items import _FORBIDDEN_IN_QUESTIONS, BANK_V2
from nullius.bank.lock import DEFAULT_LOCK_PATH, V2_LOCK_PATH, verify, write_lock
from nullius.bank.oracle import ORACLE_SEED_OFFSET, measure_effect
from nullius.bank.truth import MIN_BOUNDARY_MARGIN, ambiguous, boundary_margin
from nullius.db.enums import Verdict
from nullius.util.canonical import sha256_of


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


# ---------------------------------------------------------------------------
# M12 — bank v2 exists because v1 could not resolve the arms
# ---------------------------------------------------------------------------


def test_v1_is_frozen_and_still_hashes_to_what_the_protocol_registered() -> None:
    """v1 is load-bearing: protocol v1 and M10's results are bound to its hash.

    Adding a second bank must not disturb it. A bank version is part of a
    preregistration, so a new bank is a new registration rather than an edit.
    """
    from nullius.benchmark.protocol import read_protocol

    registered = read_protocol()
    assert registered.bank["n_items"] == len(BANK_V1) == 20
    assert registered.bank["items_hash"] == sha256_of([i.as_dict() for i in BANK_V1])


def test_v2_is_larger_and_its_ids_do_not_collide_with_v1() -> None:
    assert len(BANK_V2) == 60
    assert not ({i.item_id for i in BANK_V1} & {i.item_id for i in BANK_V2})
    assert not validate_bank(BANK_V2).problems


def test_v2_holds_the_null_fraction_the_evaluation_doc_specifies() -> None:
    truths = read_lock(V2_LOCK_PATH)
    nulls = sum(1 for t in truths.values() if t.is_null)
    assert nulls / len(truths) == pytest.approx(0.45, abs=0.02)


def test_every_v2_item_has_an_unambiguous_truth() -> None:
    """Hard for the experiment, never in doubt for the oracle.

    That gap is the whole design: the oracle sees 40 seeds of 20,000 samples,
    an experiment gets 5 of 2,000. An item may sit inside one experiment
    standard error of a boundary while remaining several oracle standard
    errors clear of it. Without this the bank would be unfair rather than hard.
    """
    truths = list(read_lock(V2_LOCK_PATH).values())
    assert ambiguous(truths) == []
    assert min(boundary_margin(t) for t in truths) >= MIN_BOUNDARY_MARGIN


def test_v2_puts_far_more_items_where_the_arms_could_actually_differ() -> None:
    """The measured reason v2 exists.

    v1's ladder separated nothing. The diagnosis at the time — that the bank
    was too easy — was wrong: thirteen of v1's twenty items already sat within
    two experiment standard errors of a boundary. What was actually wrong is
    that twenty items move the primary metric in steps of 0.05, and only six
    sat inside one standard error, which is the band where two arms can
    plausibly disagree.
    """
    experiment_se = 0.005

    def within_one_se(truths: dict[str, object]) -> int:
        return sum(
            1
            for t in truths.values()
            if min(abs(t.effect - b) for b in (t.mde, -t.mde, 0.5 * t.mde, -0.5 * t.mde))
            / experiment_se
            <= 1.0
        )

    v1_hard = within_one_se(read_lock())
    v2_hard = within_one_se(read_lock(V2_LOCK_PATH))

    assert v1_hard <= 6
    assert v2_hard >= 25
    # And the resolution of the primary metric improves with the item count.
    assert 1 / len(BANK_V2) < 1 / len(BANK_V1)


def test_the_two_truth_locks_describe_their_own_banks_and_not_each_other() -> None:
    """`write_lock` used to hard-code v1's hash, which would have silently
    stamped it onto v2's truths."""
    v1_payload = json.loads(Path("bank/truth.lock.json").read_text(encoding="utf-8"))
    v2_payload = json.loads(V2_LOCK_PATH.read_text(encoding="utf-8"))

    assert v1_payload["items_hash"] == sha256_of([i.as_dict() for i in BANK_V1])
    assert v2_payload["items_hash"] == sha256_of([i.as_dict() for i in BANK_V2])
    assert v1_payload["items_hash"] != v2_payload["items_hash"]
