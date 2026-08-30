"""Canonical serialisation.

Every provenance guarantee in Nullius reduces to one question: *do these two
things hash to the same bytes?* A preregistration hash is only meaningful if
the same specification always canonicalises identically, and two different
specifications never do.

The rules, and why each exists:

- **Keys sorted, no insignificant whitespace.** Dictionary ordering must not
  change a hash; two agents describing the same design in a different field
  order registered the same design.
- **NaN and infinity rejected.** They are not JSON, they compare unequal to
  themselves, and a metric that is NaN is a bug we want raised at the moment
  of recording rather than discovered during analysis.
- **Datetimes must be timezone-aware and are normalised to UTC.** A naive
  timestamp is unorderable across hosts, and the preregistration invariant is
  fundamentally a claim about ordering.
- **No float coercion of integers.** ``5`` and ``5.0`` are different values
  and must not collide; seeds and sample counts are integers.
- **Bytes are hex.** Digests appear inside payloads constantly.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from enum import Enum
from typing import Any

__all__ = ["canonical_bytes", "canonical_json", "sha256_hex", "sha256_of"]


class CanonicalisationError(ValueError):
    """A value cannot be represented canonically, so it must not be hashed."""


def _normalise(value: Any) -> Any:
    """Reduce ``value`` to the JSON subset that canonical form admits."""
    match value:
        case None | bool():
            # bool before int: isinstance(True, int) is True and would coerce.
            return value
        case int():
            return value
        case float():
            if math.isnan(value) or math.isinf(value):
                raise CanonicalisationError(
                    f"non-finite float {value!r} cannot be canonicalised; a metric "
                    "that is NaN or infinite is a defect, not a result"
                )
            return value
        case str():
            return value
        case bytes() | bytearray():
            return bytes(value).hex()
        case Decimal():
            # str() keeps the exact decimal value; float() would not.
            return str(value)
        case uuid.UUID():
            return str(value)
        case dt.datetime():
            if value.tzinfo is None:
                raise CanonicalisationError(
                    f"naive datetime {value!r} cannot be canonicalised; "
                    "preregistration ordering requires an absolute instant"
                )
            return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
        case dt.date():
            return value.isoformat()
        case Enum():
            return _normalise(value.value)
        case Mapping():
            out: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise CanonicalisationError(
                        f"mapping key {key!r} is not a string; canonical JSON has no "
                        "stable ordering for mixed key types"
                    )
                out[key] = _normalise(item)
            return out
        case Sequence():
            return [_normalise(item) for item in value]
        case _:
            raise CanonicalisationError(
                f"{type(value).__name__} has no canonical representation; add one "
                "explicitly rather than letting str() decide"
            )


def canonical_json(value: Any) -> str:
    """Return the canonical JSON text for ``value``.

    Deterministic across processes, machines and Python versions for every
    type handled above.
    """
    return json.dumps(
        _normalise(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_bytes(value: Any) -> bytes:
    """Canonical JSON encoded as UTF-8 — the bytes that actually get hashed."""
    return canonical_json(value).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_of(value: Any) -> str:
    """Lowercase hex SHA-256 of a value's canonical form.

    This is the function behind preregistration hashes, event payload hashes
    and the LLM cache key.
    """
    return sha256_hex(canonical_bytes(value))
