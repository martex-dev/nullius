"""Primitives that the provenance guarantees rest on.

Nothing in this package knows about research. It provides the three things
every hash in Nullius depends on: a canonical byte representation for
structured values, an injectable clock, and an injectable identifier source.

The latter two are injectable for one reason: a research program must be
replayable. Wall-clock time and random UUIDs are the two places
nondeterminism would otherwise leak into the ledger.
"""

from __future__ import annotations

from nullius.util.canonical import canonical_json, sha256_hex, sha256_of
from nullius.util.clock import Clock, FrozenClock, SystemClock
from nullius.util.ids import DeterministicIds, IdGenerator, RandomIds

__all__ = [
    "Clock",
    "DeterministicIds",
    "FrozenClock",
    "IdGenerator",
    "RandomIds",
    "SystemClock",
    "canonical_json",
    "sha256_hex",
    "sha256_of",
]
