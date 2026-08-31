"""The truth lock file.

The bank's measured answers, written once and thereafter *checked* rather than
trusted. ``nullius bank verify`` recomputes every value from the generating
process and fails if any has drifted — so a change to the data generating
process cannot silently change what the institution is being scored against.

The lock is committed. That is the point: the ground truth for every result
this project ever reports is in the git history, with the commit that produced
it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from nullius.bank.items import BANK_V1, BankItem
from nullius.bank.oracle import DEFAULT_SAMPLES, DEFAULT_SEEDS, measure_effect
from nullius.bank.truth import Truth
from nullius.util.canonical import canonical_json, sha256_of

__all__ = [
    "DEFAULT_LOCK_PATH",
    "V2_LOCK_PATH",
    "LockVerification",
    "compute_truths",
    "read_lock",
    "verify",
    "write_lock",
]

DEFAULT_LOCK_PATH = Path("bank/truth.lock.json")
V2_LOCK_PATH = Path("bank/truth.v2.lock.json")
"""Bank v2's truths, in their own file beside v1's.

Two banks, two locks, and no merging. ``benchmark/protocol.lock.json`` hashes
v1's items *and* v1's truth lock, so a lock that grew to cover both banks would
change the hash of the one M10's results were registered against. A bank
version is part of a preregistration; adding to it means a new file, not a
bigger one.
"""

#: How far a recomputed effect may drift before verification fails.
#: Generous relative to the oracle's own standard error, tight relative to the
#: null band, so ordinary floating-point variation passes and a changed data
#: generating process does not.
DRIFT_TOLERANCE = 1e-6


def compute_truths(
    items: Iterable[BankItem] = BANK_V1,
    *,
    n_samples: int = DEFAULT_SAMPLES,
    n_seeds: int = DEFAULT_SEEDS,
) -> list[Truth]:
    """Measure every item. Slow by design — this is the expensive, careful pass."""
    return [
        measure_effect(
            item_id=item.item_id,
            generator_params=item.generator_params,
            mde=item.mde,
            planted_defects=item.planted_defects,
            n_samples=n_samples,
            n_seeds=n_seeds,
        )
        for item in items
    ]


def write_lock(
    truths: Iterable[Truth],
    path: Path = DEFAULT_LOCK_PATH,
    *,
    items: Sequence[BankItem] = BANK_V1,
) -> Path:
    """Write the lock file, with a hash of the items it describes.

    ``items`` must be the bank the truths were measured from. It used to be
    hard-coded to v1, which was correct while one bank existed and would have
    silently stamped v1's hash onto v2's truths the moment a second one did.
    """
    truth_list = list(truths)
    payload = {
        "version": 1,
        "items_hash": sha256_of([item.as_dict() for item in items]),
        "truths": [truth.as_dict() for truth in truth_list],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    return path


def read_lock(path: Path = DEFAULT_LOCK_PATH) -> dict[str, Truth]:
    """Load the locked truths, keyed by item id."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {entry["item_id"]: Truth.from_dict(entry) for entry in payload["truths"]}


@dataclass(frozen=True, slots=True)
class LockVerification:
    ok: bool
    checked: int
    drifted: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    items_changed: bool = False

    def __str__(self) -> str:
        if self.ok:
            return f"bank truth verified: {self.checked} items recomputed and unchanged"
        parts = []
        if self.items_changed:
            parts.append("the bank items themselves changed since the lock was written")
        if self.missing:
            parts.append(f"{len(self.missing)} item(s) missing from the lock: {list(self.missing)}")
        if self.drifted:
            parts.append(f"{len(self.drifted)} item(s) drifted: {list(self.drifted)}")
        return "bank truth NOT verified: " + "; ".join(parts)


def verify(
    path: Path = DEFAULT_LOCK_PATH,
    *,
    n_samples: int | None = None,
    n_seeds: int | None = None,
    items: Sequence[BankItem] = BANK_V1,
) -> LockVerification:
    """Recompute every truth and compare it to the lock.

    Oracle settings come from the lock unless overridden, so verification
    reproduces the conditions the truth was measured under rather than
    whatever the caller happens to pass.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    locked = {entry["item_id"]: Truth.from_dict(entry) for entry in payload["truths"]}
    items_changed = payload.get("items_hash") != sha256_of([i.as_dict() for i in items])

    if locked:
        first = next(iter(locked.values()))
        n_samples = first.oracle_samples if n_samples is None else n_samples
        n_seeds = first.oracle_seeds if n_seeds is None else n_seeds

    drifted: list[str] = []
    missing: list[str] = []
    for truth in compute_truths(
        items, n_samples=n_samples or DEFAULT_SAMPLES, n_seeds=n_seeds or DEFAULT_SEEDS
    ):
        previous = locked.get(truth.item_id)
        if previous is None:
            missing.append(truth.item_id)
        elif (
            abs(previous.effect - truth.effect) > DRIFT_TOLERANCE
            or previous.verdict is not truth.verdict
        ):
            drifted.append(truth.item_id)

    return LockVerification(
        ok=not (drifted or missing or items_changed),
        checked=len(locked),
        drifted=tuple(drifted),
        missing=tuple(missing),
        items_changed=items_changed,
    )
