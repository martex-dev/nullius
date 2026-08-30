"""Rebuilding state by folding the event log.

The claim "all state is a fold over the ledger" is easy to write in a design
document and easy to quietly stop being true. So it is a test, not a promise:
:func:`reconciliation` reconstructs every projected table from events alone and
diffs it against what the tables actually contain.

If a repository method ever writes a row without recording the corresponding
event, the reconciliation fails and names the table and primary key. That is
the whole point — the ledger cannot silently fall behind the database.

Event payload convention, which the repository upholds::

    {"entity": "<table name>", "pk": "<primary key as text>", "row": {...}}

An event whose payload lacks ``entity`` is a narrative event (a decision, a
budget refusal) and contributes nothing to the fold.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from nullius.db.tables import Base, Event
from nullius.util.canonical import canonical_json

__all__ = ["Reconciliation", "fold_events", "reconciliation", "snapshot_tables"]

#: Tables that are *not* projections of the log.
#:
#: ``events`` is the log itself; ``query_audit`` records reads rather than state
#: changes, and recording a read as a state change would make the audit trail
#: part of what it audits.
NOT_PROJECTED: frozenset[str] = frozenset({"events", "query_audit"})

State = dict[str, dict[str, dict[str, Any]]]
"""``{table: {primary key: row}}``."""


def projected_tables() -> list[str]:
    """Every table expected to be reconstructible from the log."""
    return sorted(name for name in Base.metadata.tables if name not in NOT_PROJECTED)


def _normalise_row(row: dict[str, Any]) -> dict[str, Any]:
    """Reduce a row to its canonical comparable form.

    Round-tripping through canonical JSON removes the differences that are
    representational rather than real — a ``UUID`` versus its string form, a
    ``Decimal`` versus its digits, an aware datetime versus its UTC ISO text.
    """
    import json

    return dict(json.loads(canonical_json(row)))


def snapshot_tables(session: Session) -> State:
    """Read the current contents of every projected table."""
    state: State = {}
    for name in projected_tables():
        table = Base.metadata.tables[name]
        primary_key = list(table.primary_key.columns)
        rows: dict[str, dict[str, Any]] = {}
        for record in session.execute(sa.select(table)).mappings():
            row = _normalise_row(dict(record))
            key = "|".join(str(row[column.name]) for column in primary_key)
            rows[key] = row
        state[name] = rows
    return state


def fold_events(events: Iterable[Event]) -> State:
    """Reconstruct state from events alone."""
    state: State = {name: {} for name in projected_tables()}
    for event in events:
        payload = event.payload or {}
        entity = payload.get("entity")
        if not isinstance(entity, str) or entity in NOT_PROJECTED:
            continue
        if entity not in state:
            state[entity] = {}
        row = payload.get("row")
        key = payload.get("pk")
        if not isinstance(row, dict) or not isinstance(key, str):
            continue
        # Later events supersede earlier ones for the same key, which is how
        # a state transition on a mutable row (a hypothesis advancing) folds.
        state[entity][key] = _normalise_row(row)
    return state


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """Difference between the folded log and the live tables."""

    ok: bool
    tables_checked: int
    rows_checked: int
    missing_from_log: list[str] = field(default_factory=list)
    missing_from_tables: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        if self.ok:
            return (
                f"ledger reconciles: {self.rows_checked} rows across "
                f"{self.tables_checked} tables rebuilt from events"
            )
        parts: list[str] = []
        if self.missing_from_log:
            parts.append(f"{len(self.missing_from_log)} row(s) written without an event")
        if self.missing_from_tables:
            parts.append(f"{len(self.missing_from_tables)} event(s) with no surviving row")
        if self.mismatched:
            parts.append(f"{len(self.mismatched)} row(s) differ from the log")
        return "ledger does NOT reconcile: " + "; ".join(parts)


def reconciliation(session: Session) -> Reconciliation:
    """Fold the log and diff it against the tables."""
    events = session.scalars(sa.select(Event).order_by(Event.seq.asc()))
    folded = fold_events(events)
    live = snapshot_tables(session)

    missing_from_log: list[str] = []
    missing_from_tables: list[str] = []
    mismatched: list[str] = []
    rows_checked = 0

    for table in projected_tables():
        live_rows = live.get(table, {})
        folded_rows = folded.get(table, {})
        for key, row in live_rows.items():
            rows_checked += 1
            if key not in folded_rows:
                missing_from_log.append(f"{table}:{key}")
            elif folded_rows[key] != row:
                mismatched.append(f"{table}:{key}")
        for key in folded_rows:
            if key not in live_rows:
                missing_from_tables.append(f"{table}:{key}")

    return Reconciliation(
        ok=not (missing_from_log or missing_from_tables or mismatched),
        tables_checked=len(projected_tables()),
        rows_checked=rows_checked,
        missing_from_log=sorted(missing_from_log),
        missing_from_tables=sorted(missing_from_tables),
        mismatched=sorted(mismatched),
    )
