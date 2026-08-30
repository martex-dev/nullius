"""The content-addressed artifact store.

Every fitted model, prediction array, log and manifest an experiment produces
is filed under the SHA-256 of its own bytes, at ``objects/<ab>/<digest>``.

Three properties follow, and all three are load-bearing:

- **Provenance is a foreign key.** A result row referencing a digest is a
  reference to exact bytes, not to a filename that may have been overwritten.
- **Tampering is detectable.** :meth:`ContentStore.get` rehashes on read, so a
  modified artifact fails loudly instead of silently changing a conclusion.
  ``docs/03-data-model.md`` makes an unresolvable artifact hash a bar to claim
  promotion; this is where that check bottoms out.
- **Identical artifacts are stored once.** Replications of the same run
  produce byte-identical outputs, which is exactly the case reproducibility
  work generates most.

Writes are atomic (temp file, then rename) and idempotent: re-putting existing
content is a no-op rather than a rewrite, so stored bytes are immutable in
practice as well as by convention.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

from nullius.errors import IntegrityError
from nullius.util.canonical import canonical_bytes, sha256_hex

__all__ = ["ContentStore", "Digest"]

Digest = str
"""Lowercase hex SHA-256. Aliased for readability at call sites."""

_FANOUT: Final = 2
_DIGEST_LENGTH: Final = 64
_READ_CHUNK: Final = 1 << 20


def _validate(digest: Digest) -> Digest:
    if len(digest) != _DIGEST_LENGTH or not all(c in "0123456789abcdef" for c in digest):
        raise ValueError(f"not a lowercase hex sha-256 digest: {digest!r}")
    return digest


class ContentStore:
    """An immutable, content-addressed blob store on the local filesystem."""

    __slots__ = ("root",)

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ paths

    def path_for(self, digest: Digest) -> Path:
        """Where ``digest`` lives, whether or not it is present."""
        _validate(digest)
        return self.root / digest[:_FANOUT] / digest

    def exists(self, digest: Digest) -> bool:
        return self.path_for(digest).is_file()

    # ------------------------------------------------------------------ write

    def put_bytes(self, data: bytes) -> Digest:
        """Store ``data`` and return its digest. Idempotent."""
        digest = sha256_hex(data)
        target = self.path_for(digest)
        if target.is_file():
            return digest

        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, data)
        return digest

    def put_json(self, value: Any) -> Digest:
        """Store a value's canonical JSON form.

        Uses the same canonicalisation as every other hash in the system, so a
        manifest stored here and a manifest hashed into an event agree.
        """
        return self.put_bytes(canonical_bytes(value))

    def put_file(self, source: Path | str) -> Digest:
        """Store the contents of a file, streaming rather than loading it."""
        source = Path(source)
        digest = self._hash_file(source)
        target = self.path_for(digest)
        if target.is_file():
            return digest

        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=target.parent, delete=False, suffix=".partial"
        ) as handle:
            temp = Path(handle.name)
        try:
            shutil.copyfile(source, temp)
            temp.replace(target)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        return digest

    @staticmethod
    def _atomic_write(target: Path, data: bytes) -> None:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, delete=False, suffix=".partial"
        ) as handle:
            temp = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temp.replace(target)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    @staticmethod
    def _hash_file(source: Path) -> Digest:
        import hashlib

        digest = hashlib.sha256()
        with source.open("rb") as handle:
            while chunk := handle.read(_READ_CHUNK):
                digest.update(chunk)
        return digest.hexdigest()

    # ------------------------------------------------------------------- read

    def get(self, digest: Digest) -> bytes:
        """Return the stored bytes, verifying them against ``digest``.

        Raises :class:`IntegrityError` if the content has been modified. This
        is the check that makes "every evidence row's artifact hash resolves"
        a real precondition for claim promotion rather than a hopeful one.
        """
        path = self.path_for(digest)
        if not path.is_file():
            raise IntegrityError(f"artifact {digest} is not in the store at {path}")

        data = path.read_bytes()
        actual = sha256_hex(data)
        if actual != digest:
            raise IntegrityError(
                f"artifact {digest} has been modified: stored bytes hash to {actual}"
            )
        return data

    # ------------------------------------------------------------------ audit

    def __iter__(self) -> Iterator[Digest]:
        """Yield every digest currently filed, in no particular order."""
        for shard in sorted(self.root.iterdir()):
            if not shard.is_dir() or len(shard.name) != _FANOUT:
                continue
            for entry in sorted(shard.iterdir()):
                if entry.is_file() and not entry.name.endswith(".partial"):
                    yield entry.name

    def verify_all(self) -> list[Digest]:
        """Rehash every artifact. Returns the digests that no longer match."""
        corrupted: list[Digest] = []
        for digest in self:
            try:
                _validate(digest)
                self.get(digest)
            except (IntegrityError, ValueError):
                corrupted.append(digest)
        return corrupted

    def __len__(self) -> int:
        return sum(1 for _ in self)
