"""The content-addressed store: dedup, atomicity, and corruption detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from nullius.errors import IntegrityError
from nullius.store.cas import ContentStore

pytestmark = [pytest.mark.isolation]


def test_roundtrip(store: ContentStore) -> None:
    digest = store.put_bytes(b"predictions,0.91,0.88")
    assert store.get(digest) == b"predictions,0.91,0.88"
    assert store.exists(digest)


def test_identical_content_is_stored_once(store: ContentStore) -> None:
    """Replications produce byte-identical outputs; that is the common case."""
    first = store.put_bytes(b"same")
    second = store.put_bytes(b"same")
    assert first == second
    assert len(store) == 1


def test_different_content_gets_different_digests(store: ContentStore) -> None:
    assert store.put_bytes(b"a") != store.put_bytes(b"b")
    assert len(store) == 2


def test_json_uses_the_same_canonical_form_as_every_other_hash(store: ContentStore) -> None:
    """A manifest stored here and one hashed into an event must agree."""
    from nullius.util.canonical import sha256_of

    manifest = {"metric": "macro_f1", "value": 0.887}
    assert store.put_json(manifest) == sha256_of(manifest)


def test_missing_artifact_raises_rather_than_returning_empty(store: ContentStore) -> None:
    """An unresolvable artifact hash bars claim promotion; it must be loud."""
    with pytest.raises(IntegrityError, match="not in the store"):
        store.get("0" * 64)


def test_modified_artifact_is_detected_on_read(store: ContentStore) -> None:
    """Rehashing on read is what makes provenance a check, not a hope."""
    digest = store.put_bytes(b"original result")
    store.path_for(digest).write_bytes(b"doctored result")

    with pytest.raises(IntegrityError, match="has been modified"):
        store.get(digest)


def test_verify_all_finds_every_corrupted_artifact(store: ContentStore) -> None:
    good = store.put_bytes(b"intact")
    bad = store.put_bytes(b"will be edited")
    store.path_for(bad).write_bytes(b"edited")

    corrupted = store.verify_all()
    assert corrupted == [bad]
    assert good not in corrupted


def test_partial_writes_are_never_visible(store: ContentStore) -> None:
    """A crashed write must not leave a file that reads as a valid artifact."""
    digest = store.put_bytes(b"content")
    stray = store.path_for(digest).parent / "deadbeef.partial"
    stray.write_bytes(b"half-written")

    assert list(store) == [digest]
    assert store.verify_all() == []


def test_put_file_streams_and_matches_put_bytes(store: ContentStore, tmp_path: Path) -> None:
    payload = b"x" * (2 << 20)
    source = tmp_path / "big.bin"
    source.write_bytes(payload)

    assert store.put_file(source) == store.put_bytes(payload)


@pytest.mark.parametrize("bad", ["", "z" * 64, "abc", "A" * 64])
def test_malformed_digests_are_refused(store: ContentStore, bad: str) -> None:
    with pytest.raises(ValueError, match="not a lowercase hex"):
        store.path_for(bad)
