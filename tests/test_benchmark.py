"""M10: the ladder, and the protocol that must predate its results."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nullius.benchmark.arms import LADDER, Arm, ArmKind, arm_named, mechanism_arms
from nullius.benchmark.protocol import (
    CONFIDENCE_AS_PROBABILITY,
    build_protocol,
    read_protocol,
    verify_protocol,
    write_protocol,
)
from nullius.db.enums import CONFIDENCE_ORDER

REGISTERED = Path("benchmark/protocol.lock.json")


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


def test_the_ladder_is_the_one_the_evaluation_doc_specifies() -> None:
    assert [arm.arm_id for arm in LADDER] == [f"B{i}" for i in range(8)]


def test_each_rung_adds_exactly_one_mechanism_to_the_one_below() -> None:
    """An ablation that changed two things at once would measure neither.

    Checked over the institutional arms, where the ladder's claim to isolate a
    mechanism actually applies. B0 to B3 change the whole pipeline shape and
    are compared on that basis rather than as single-switch ablations.
    """
    switches = ("preregistered", "custodian", "adversary", "replication", "reviewer", "memory")

    def flags(arm: Arm) -> dict[str, bool]:
        return {name: bool(getattr(arm, name)) for name in switches}

    b4, b5 = arm_named("B4"), arm_named("B5")
    changed = [k for k in switches if flags(b4)[k] != flags(b5)[k]]
    assert changed == ["adversary"], f"B4 to B5 changed {changed}"

    b6, b7 = arm_named("B6"), arm_named("B7")
    changed = [k for k in switches if flags(b6)[k] != flags(b7)[k]]
    assert changed == ["memory"], f"B6 to B7 changed {changed}"


def test_the_full_arm_has_every_mechanism_on() -> None:
    full = arm_named("B6")
    assert full.preregistered
    assert full.custodian
    assert full.adversary
    assert full.replication
    assert full.reviewer
    assert full.memory


def test_the_floor_arm_looks_at_nothing() -> None:
    floor = arm_named("B0")
    assert floor.kind is ArmKind.CONSTANT
    assert not any(
        (floor.preregistered, floor.custodian, floor.adversary, floor.replication, floor.memory)
    )


def test_the_model_dependent_arms_are_named_and_excluded_from_mechanism_claims() -> None:
    """A mock-driven run may not speak for the arms a mock determines."""
    dependent = {arm.arm_id for arm in LADDER if arm.model_dependent}
    assert dependent == {"B1", "B2"}
    assert dependent.isdisjoint({arm.arm_id for arm in mechanism_arms()})


def test_an_unknown_arm_raises_rather_than_returning_a_default() -> None:
    with pytest.raises(KeyError):
        arm_named("B99")


# ---------------------------------------------------------------------------
# The protocol
# ---------------------------------------------------------------------------


def test_a_protocol_is_registered_and_its_hash_covers_its_content() -> None:
    protocol = build_protocol(registered_at="2026-08-31")

    assert protocol.protocol_hash == build_protocol(registered_at="2026-08-31").protocol_hash
    assert len(protocol.protocol_hash) == 64


def test_changing_any_registered_choice_changes_the_hash() -> None:
    """Including the ones it would be most convenient to change quietly."""
    base = build_protocol(registered_at="2026-08-31")

    for field, value in (
        ("primary_metric", "null_accuracy"),
        ("prediction", "something else entirely"),
    ):
        import dataclasses

        altered = dataclasses.replace(base, **{field: value})
        assert altered.protocol_hash != base.protocol_hash, field


def test_the_registered_protocol_is_committed_and_verifies() -> None:
    """The preregistration exists in the repository, not only in a docstring."""
    assert REGISTERED.exists(), "run `nullius benchmark preregister`"

    result = verify_protocol(REGISTERED)
    assert result.ok, str(result)
    assert result.bank_unchanged
    assert result.ladder_unchanged


def test_the_protocol_pins_the_bank_it_will_be_scored_against(tmp_path: Path) -> None:
    """A protocol satisfied by redefining "correct" would be worth nothing."""
    protocol = read_protocol(REGISTERED)

    assert protocol.bank["n_items"] == 20
    assert len(protocol.bank["items_hash"]) == 64
    assert len(protocol.bank["truth_lock_hash"]) == 64


def test_a_second_protocol_may_not_overwrite_the_first(tmp_path: Path) -> None:
    """The refusal is the mechanism.

    A preregistration that can be replaced once the numbers are in is a
    postregistration. The first write is proven to succeed so that the second
    one's refusal is about the content and not about the path.
    """
    import dataclasses

    path = tmp_path / "protocol.lock.json"
    first = build_protocol(registered_at="2026-08-31")
    assert write_protocol(first, path).exists()

    write_protocol(first, path)  # idempotent: the same protocol is not a conflict

    second = dataclasses.replace(first, prediction="B6 wins, obviously")
    with pytest.raises(ValueError, match="refusing to replace"):
        write_protocol(second, path)


def test_a_tampered_protocol_fails_verification(tmp_path: Path) -> None:
    """Editing the content without editing the hash is caught."""
    path = tmp_path / "protocol.lock.json"
    write_protocol(build_protocol(registered_at="2026-08-31"), path)
    assert verify_protocol(path).ok, "the untampered file must verify first"

    body = json.loads(path.read_text(encoding="utf-8"))
    body["protocol"]["prediction"] = "whatever the results turn out to say"
    path.write_text(json.dumps(body), encoding="utf-8")

    result = verify_protocol(path)
    assert not result.ok
    assert "stored hash" in str(result)


def test_every_confidence_level_maps_to_a_probability() -> None:
    """Calibration needs a number for every level the rubric can produce."""
    assert set(CONFIDENCE_AS_PROBABILITY) == {level.value for level in CONFIDENCE_ORDER}
    assert all(0.0 < p < 1.0 for p in CONFIDENCE_AS_PROBABILITY.values())


def test_the_confidence_mapping_is_monotone_in_the_rubric_order() -> None:
    """A stronger level that scored as less certain would invert every Brier score."""
    ordered = [CONFIDENCE_AS_PROBABILITY[level.value] for level in CONFIDENCE_ORDER]
    assert ordered == sorted(ordered)


def test_the_protocol_refuses_to_drop_an_unanswered_item() -> None:
    """Stated as an exclusion rule, because it is the tempting exclusion.

    Dropping halted items would let an arm improve its accuracy by failing on
    the questions it finds hard.
    """
    protocol = read_protocol(REGISTERED)
    assert any(
        "halts before a verdict counts as incorrect" in rule for rule in protocol.exclusion_rules
    )
