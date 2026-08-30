"""Turning a mapped entity into the row the ledger records.

One function, used by every write path, because the ledger's promise is that
folding the events reconstructs the tables *exactly*. Two ways that promise
breaks quietly, both fixed here:

**Scale.** ``Decimal("0") == Decimal("0.00")`` is True, so a value assigned at
a different scale than the one loaded looks unchanged to SQLAlchemy's change
detection. :class:`~nullius.db.tables.Money` quantises on the way into the
database; this quantises the same values on the way into the event, so the two
representations agree instead of drifting apart by a couple of zeros.

**Duplication.** The queue previously carried its own copy of this logic. Two
implementations of "what does a row look like in an event" is one too many —
they diverge, and the reconciliation failure that results points at a row
rather than at the code that wrote it.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from nullius.db.tables import Base, Money
from nullius.util.canonical import canonical_json

__all__ = ["entity_row"]


def entity_row(entity: Base) -> tuple[str, str, dict[str, Any]]:
    """Return ``(table name, primary key, JSON-ready column values)``.

    Values are normalised to match what the database will hold, then round
    tripped through canonical JSON so the payload stores and hashes the same
    way everywhere.
    """
    mapper = sa.inspect(type(entity))
    table = mapper.local_table
    if not isinstance(table, sa.Table):
        raise TypeError(
            f"{type(entity).__name__} is not mapped to a single table and cannot "
            "be recorded faithfully in the ledger"
        )

    row: dict[str, Any] = {}
    for column in mapper.columns:
        value = getattr(entity, column.key)
        if isinstance(column.type, Money) and isinstance(value, Decimal):
            value = value.quantize(Money.SCALE)
        row[column.name] = value

    pk = "|".join(str(row[column.name]) for column in table.primary_key.columns)
    return table.name, pk, dict(json.loads(canonical_json(row)))
