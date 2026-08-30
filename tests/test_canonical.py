"""Canonical serialisation: the bytes every provenance guarantee rests on."""

from __future__ import annotations

import datetime as dt
import math
import uuid
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from nullius.db.enums import Role
from nullius.util.canonical import (
    CanonicalisationError,
    canonical_json,
    sha256_hex,
    sha256_of,
)


def test_key_order_does_not_change_the_hash() -> None:
    """Two agents describing one design in different field orders registered one design."""
    a = {"metric": "macro_f1", "seeds": 5, "arms": ["full", "prune"]}
    b = {"arms": ["full", "prune"], "seeds": 5, "metric": "macro_f1"}
    assert sha256_of(a) == sha256_of(b)


def test_list_order_does_change_the_hash() -> None:
    """Order is meaningful in a sequence even though it is not in a mapping."""
    assert sha256_of(["full", "prune"]) != sha256_of(["prune", "full"])


def test_integers_and_floats_do_not_collide() -> None:
    """A seed of 5 is not a seed of 5.0."""
    assert canonical_json({"seed": 5}) != canonical_json({"seed": 5.0})


def test_booleans_are_not_integers() -> None:
    assert canonical_json({"x": True}) == '{"x":true}'
    assert canonical_json({"x": 1}) == '{"x":1}'


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_metrics_are_refused(value: float) -> None:
    """A NaN metric is a defect, and must fail where it is recorded."""
    with pytest.raises(CanonicalisationError, match="not a result"):
        canonical_json({"macro_f1": value})


def test_naive_datetimes_are_refused() -> None:
    """Preregistration ordering needs an absolute instant."""
    with pytest.raises(CanonicalisationError, match="absolute instant"):
        canonical_json({"registered_at": dt.datetime(2026, 1, 1, 12, 0, 0)})


def test_equivalent_instants_in_different_zones_hash_identically() -> None:
    utc = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)
    plus_two = dt.datetime(2026, 1, 1, 14, 0, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    assert sha256_of({"t": utc}) == sha256_of({"t": plus_two})


def test_decimals_keep_their_exact_value() -> None:
    """0.1 + 0.2 is not 0.3, and a cost ledger must not pretend otherwise."""
    assert canonical_json({"usd": Decimal("0.30")}) == '{"usd":"0.30"}'
    assert canonical_json({"usd": Decimal("0.30")}) != canonical_json({"usd": 0.3})


def test_uuids_enums_and_bytes_have_canonical_forms() -> None:
    identifier = uuid.UUID("6f1d4a52-8c3b-5e77-9a10-2b8e4d6c9f31")
    encoded = canonical_json(
        {"id": identifier, "role": Role.SKEPTIC, "digest": b"\xde\xad\xbe\xef"}
    )
    assert '"role":"skeptic"' in encoded
    assert '"digest":"deadbeef"' in encoded
    assert str(identifier) in encoded


def test_unknown_types_are_refused_rather_than_stringified() -> None:
    """Silent ``str()`` would make two different objects hash alike."""

    class Opaque:
        pass

    with pytest.raises(CanonicalisationError, match="no canonical representation"):
        canonical_json({"x": Opaque()})


def test_non_string_keys_are_refused() -> None:
    with pytest.raises(CanonicalisationError, match="not a string"):
        canonical_json({1: "a"})


def test_sha256_hex_matches_the_reference_value() -> None:
    """Pinned against the published SHA-256 of the empty string."""
    assert sha256_hex(b"") == ("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")


@given(
    st.dictionaries(
        st.text(min_size=1, max_size=8),
        st.one_of(
            st.integers(),
            st.text(max_size=16),
            st.booleans(),
            st.none(),
            st.floats(allow_nan=False, allow_infinity=False),
        ),
        max_size=6,
    )
)
def test_canonicalisation_is_deterministic(value: dict[str, Any]) -> None:
    assert canonical_json(value) == canonical_json(value)
    assert sha256_of(value) == sha256_of(dict(reversed(list(value.items()))))
