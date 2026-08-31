"""Identifiers, as an injected dependency.

Random UUIDs are the second place nondeterminism would leak into the ledger
(the first being the clock). A replayed research program that allocates
different identifiers produces a different event chain, different hashes, and
therefore cannot be compared to the original — which would defeat the point of
ADR-0005.

:class:`RandomIds` is used for live programs. :class:`DeterministicIds` derives
a reproducible stream from a seed, so a replay reconstructs the same graph.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Protocol, runtime_checkable

__all__ = [
    "EXPERIMENT_SEED_CEILING",
    "DeterministicIds",
    "IdGenerator",
    "RandomIds",
    "seed_for",
]

EXPERIMENT_SEED_CEILING = 1_000_000
"""Experiment seeds are drawn below this; the oracle's begin at it.

Disjoint by construction rather than by luck. If an experiment could draw
an oracle seed it could reproduce the ground truth sample outright, and
agreeing with the truth would stop being evidence of having estimated it.
"""


def seed_for(label: str) -> int:
    """A stable experiment seed root derived from a label.

    Not :func:`hash`. Python randomises string hashing per process unless
    ``PYTHONHASHSEED`` is fixed, so ``hash(item_id)`` gives a different answer
    in every interpreter — which means a *preregistered* seed root, the number
    that is supposed to be nailed down before anything runs, would differ
    between runs. That silently changes ``spec_hash``, changes which seeds are
    drawn, and can flip the verdict of any experiment whose true effect sits
    near a decision boundary. A preregistration that does not survive a
    restart is not a preregistration.

    SHA-256 instead: the same digest this project already uses to make
    registrations and artifacts identical across machines, truncated into the
    range experiments are allowed to draw from.
    """
    digest = hashlib.sha256(label.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % EXPERIMENT_SEED_CEILING


#: Namespace for derived identifiers. Fixed for all time; changing it would
#: silently renumber every replayed program.
NULLIUS_NAMESPACE = uuid.UUID("6f1d4a52-8c3b-5e77-9a10-2b8e4d6c9f31")


@runtime_checkable
class IdGenerator(Protocol):
    """Source of entity identifiers."""

    def new(self) -> uuid.UUID:
        """Return a fresh identifier."""
        ...


class RandomIds:
    """Version-4 UUIDs. The default for live research programs."""

    __slots__ = ()

    def new(self) -> uuid.UUID:
        return uuid.uuid4()


class DeterministicIds:
    """A reproducible identifier stream derived from a seed.

    Two generators built with the same seed emit the same sequence, which is
    what makes a replayed program comparable to the original.
    """

    __slots__ = ("_counter", "_seed")

    def __init__(self, seed: str) -> None:
        self._seed = seed
        self._counter = 0

    def new(self) -> uuid.UUID:
        value = uuid.uuid5(NULLIUS_NAMESPACE, f"{self._seed}:{self._counter}")
        self._counter += 1
        return value

    @property
    def issued(self) -> int:
        """How many identifiers have been drawn — part of a replay's state."""
        return self._counter
