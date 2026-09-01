"""M10: the ladder, and the protocol that must predate its results."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nullius.benchmark.arms import LADDER, Arm, ArmKind, arm_named, mechanism_arms
from nullius.benchmark.protocol import (
    CONFIDENCE_AS_PROBABILITY,
    V2_PROTOCOL_PATH,
    V3_PROTOCOL_PATH,
    V4_PROTOCOL_PATH,
    V5_PROTOCOL_PATH,
    V6_PROTOCOL_PATH,
    ProtocolVerification,
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


# ---------------------------------------------------------------------------
# M12 — a second protocol, registered as a change rather than an edit
# ---------------------------------------------------------------------------


def test_registering_v2_leaves_v1_verifying_exactly_as_it_did() -> None:
    """The point of the whole exercise.

    v1 was found to have three flaws by running it. None was patched, because
    editing a hashed preregistration to fix its own findings is precisely the
    substitution the file exists to prevent. v1 stays on disk, still
    verifying, still wrong in the three ways it was wrong.
    """
    assert verify_protocol().ok
    assert read_protocol().version == "1"
    assert read_protocol().statistics["baseline_arm"] == "B1"


def test_v2_verifies_against_its_own_bank() -> None:
    verification = verify_protocol(V2_PROTOCOL_PATH)
    assert verification.ok, str(verification)
    assert read_protocol(V2_PROTOCOL_PATH).version == "2"


def test_the_two_protocols_are_different_registrations() -> None:
    v1, v2 = read_protocol(), read_protocol(V2_PROTOCOL_PATH)
    assert v1.protocol_hash != v2.protocol_hash
    assert v1.bank["items_hash"] != v2.bank["items_hash"]
    assert v1.bank["n_items"] == 20
    assert v2.bank["n_items"] == 60


def test_v2_fixes_each_flaw_that_running_v1_exposed() -> None:
    """Every one of the three, and each is checkable rather than asserted."""
    v2 = read_protocol(V2_PROTOCOL_PATH)

    # 1. The baseline is no longer an arm whose behaviour the model dominates.
    assert v2.statistics["baseline_arm"] == "B0"
    assert not arm_named(str(v2.statistics["baseline_arm"])).model_dependent

    # 2. The prediction is settled on an interval, not two point estimates.
    assert v2.statistics["adjudication"] == "interval_excludes_zero"
    assert "excludes zero" in v2.prediction

    # 3. Calibration is scored where the rubric's quantity is the scored one.
    assert v2.statistics["calibration_scope"] == "asserted_effects"


def test_verifying_a_protocol_rebuilds_it_under_its_own_declared_version() -> None:
    """Otherwise verifying v1 would silently check it against v2's bank, and a
    protocol that fails because a *later* bank exists is not a check on
    anything."""
    v1 = read_protocol()
    rebuilt = build_protocol(registered_at=v1.registered_at, version=v1.version)
    assert rebuilt.protocol_hash == v1.protocol_hash

    other = build_protocol(registered_at=v1.registered_at, version="2")
    assert other.protocol_hash != v1.protocol_hash


def test_an_unknown_protocol_version_raises_rather_than_defaulting() -> None:
    with pytest.raises(KeyError):
        build_protocol(version="99")


def test_verification_catches_a_builder_that_has_drifted_from_the_file() -> None:
    """The check that was missing, and the bug that motivated it.

    Adding two keys to the builder's payload changed what v1 would rebuild to
    while every existing check stayed green: the bank was unchanged, the arms
    were unchanged, and the stored hash still matched its own content. A
    registered protocol the code can no longer reproduce has been edited in
    effect, however innocent the diff looks.
    """
    stored = read_protocol()
    verification = verify_protocol()
    assert verification.ok
    assert verification.rebuilds_identically

    drifted = ProtocolVerification(
        ok=False,
        protocol_hash=stored.protocol_hash,
        stored_hash=stored.protocol_hash,
        bank_unchanged=True,
        ladder_unchanged=True,
        rebuilds_identically=False,
    )
    assert not drifted.ok
    assert "no longer rebuilds" in str(drifted)


def test_v1_carries_none_of_the_keys_v2_registered() -> None:
    """v2's choices are absent from v1 rather than back-filled into it.

    Back-filling would change the hash of a protocol that is supposed to be
    immutable, so readers of v1 fall back to the behaviour its results were
    actually produced under.
    """
    v1 = read_protocol()
    assert "calibration_scope" not in v1.statistics
    assert "adjudication" not in v1.statistics


# ---------------------------------------------------------------------------
# M14 — a ninth arm, and a protocol that adjudicates what it predicted
# ---------------------------------------------------------------------------


def test_extending_the_ladder_leaves_the_earlier_protocols_verifying() -> None:
    """Adding `adaptive_seeds` to Arm.as_dict changed the arms payload of three
    protocols that are supposed to be immutable, and `rebuilds_identically`
    caught it. A protocol records the arms as they were described when it was
    registered; a later field is a later registration."""
    from pathlib import Path

    from nullius.benchmark.protocol import PROTOCOL_VERSIONS

    for version, settings in sorted(PROTOCOL_VERSIONS.items()):
        result = verify_protocol(Path(settings["path"]))
        assert result.ok, f"v{version}: {result}"
        assert result.rebuilds_identically, f"v{version}"


def test_only_v4_registered_the_adaptive_field() -> None:
    earlier = (V2_PROTOCOL_PATH, V3_PROTOCOL_PATH)
    for older in (read_protocol(), *(read_protocol(p) for p in earlier)):
        assert len(older.arms) == 8
        assert all("adaptive_seeds" not in arm for arm in older.arms)

    v4 = read_protocol(V4_PROTOCOL_PATH)
    assert len(v4.arms) == 9
    assert all("adaptive_seeds" in arm for arm in v4.arms)


def test_the_adaptive_arm_differs_from_the_full_institution_in_one_boolean() -> None:
    six = arm_named("B6")
    eight = arm_named("B8")
    assert eight.adaptive_seeds and not six.adaptive_seeds
    for field in ("preregistered", "custodian", "adversary", "replication", "reviewer", "memory"):
        assert getattr(six, field) == getattr(eight, field), field


def test_v4_names_the_quantity_it_adjudicates() -> None:
    """v3 registered a prediction about coverage and inherited a rule that
    tested accuracy, so it reported a verdict after measuring something the
    prediction never mentioned. Storing the contrast as data is the fix."""
    v4 = read_protocol(V4_PROTOCOL_PATH)
    named = v4.statistics["adjudicated"]

    assert v4.statistics["adjudication"] == "named_contrast"
    assert named["quantity"] == "coverage"
    assert (named["treatment"], named["baseline"]) == ("B8", "B6")
    # And the prediction text is about the same quantity the rule will test.
    assert "coverage" in v4.prediction.lower()
    assert named["treatment"] in v4.prediction and named["baseline"] in v4.prediction


def test_v4_records_the_escalation_ceiling_it_ran_under() -> None:
    """The ceiling is part of what was preregistered, not a runtime choice."""
    from nullius.kernel import ADAPTIVE_SEED_CEILING

    v4 = read_protocol(V4_PROTOCOL_PATH)
    assert v4.statistics["adaptive_seed_ceiling"] == ADAPTIVE_SEED_CEILING


def test_v6_registers_the_conservative_arm_without_disturbing_v4_or_v5() -> None:
    """Adding `conservative_escalation` to Arm.as_dict changes the arms payload
    of every protocol that did not register it. The arm-field projection is what
    keeps v4 and v5 rebuilding to the hashes they were registered with."""
    from pathlib import Path

    from nullius.benchmark.protocol import PROTOCOL_VERSIONS

    for version, settings in sorted(PROTOCOL_VERSIONS.items(), key=lambda kv: int(kv[0])):
        result = verify_protocol(Path(settings["path"]))
        assert result.ok, f"v{version}: {result}"

    for older in (read_protocol(V4_PROTOCOL_PATH), read_protocol(V5_PROTOCOL_PATH)):
        assert len(older.arms) == 9
        assert all("conservative_escalation" not in arm for arm in older.arms)

    v6 = read_protocol(V6_PROTOCOL_PATH)
    assert len(v6.arms) == 10
    assert all("conservative_escalation" in arm for arm in v6.arms)


def test_b9_differs_from_b8_in_one_boolean() -> None:
    eight, nine = arm_named("B8"), arm_named("B9")
    assert nine.conservative_escalation and not eight.conservative_escalation
    for field in ("preregistered", "custodian", "adversary", "replication", "memory"):
        assert getattr(eight, field) == getattr(nine, field), field
    assert eight.adaptive_seeds and nine.adaptive_seeds


def test_v6_adjudicates_the_quantity_its_prediction_names() -> None:
    v6 = read_protocol(V6_PROTOCOL_PATH)
    named = v6.statistics["adjudicated"]
    assert (named["treatment"], named["baseline"]) == ("B9", "B8")
    assert named["quantity"] == "coverage"
    assert "coverage" in v6.prediction.lower()
    assert v6.statistics["escalation_confidence"] == 0.80
