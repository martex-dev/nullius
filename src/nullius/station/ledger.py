"""Reading a ladder's ledger, for the detail the lock files do not carry.

The committed results files hold what every arm answered about every bank item.
They do not hold who did what, when, at what cost, or which objection carried
which discriminating test. That lives in the per-arm SQLite ledgers a ladder
leaves behind, which are gitignored because they are large and because they are
outputs — so the station has to work without one, and say when it is without one.

**Read-only, and by SELECT.** The repository layer is the only write path in
this project and nothing here writes. The connection is opened through a
read-only URI so that a bug cannot make this a second write path by accident,
and the queries are literal SELECTs against table names rather than ORM models
because the ledgers on disk were written by five different milestones and a
model mismatch would make an old ladder unreadable instead of merely partial.
Every query degrades to ``None`` on a table this ledger does not have.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["LedgerView", "open_ledger"]


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    """Open a ledger read-only, so that reading it cannot alter it."""
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        yield connection


def _tables(connection: sqlite3.Connection) -> frozenset[str]:
    rows = connection.execute("select name from sqlite_master where type='table'")
    return frozenset(str(row["name"]) for row in rows)


def _rows(
    connection: sqlite3.Connection,
    present: frozenset[str],
    table: str | tuple[str, ...],
    sql: str,
    parameters: Sequence[object] = (),
) -> tuple[dict[str, Any], ...]:
    """Run a query, or return nothing if this ledger lacks a table it touches.

    Every table the query joins is named, so an older ladder missing one of them
    yields an empty room rather than an exception. A station that cannot open a
    v2 ledger would be a station that can only describe the present.
    """
    needed = (table,) if isinstance(table, str) else table
    if not set(needed) <= present:
        return ()
    return tuple(dict(row) for row in connection.execute(sql, tuple(parameters)))


@dataclass(frozen=True, slots=True)
class LedgerView:
    """One arm's ledger, distilled to what the station draws.

    Everything here is a count or a row read out of the file named by
    :attr:`path`. Nothing is inferred, and where a table is empty the emptiness
    is carried through as an empty tuple rather than being filled in.
    """

    path: Path
    arm_id: str
    counts: dict[str, int]
    """Rows per table, so a room can say ``empty`` and mean it."""

    events_by_type: tuple[tuple[str, int], ...]
    events_by_role: tuple[tuple[str, int], ...]
    states_seen: tuple[tuple[str, int], ...]
    """Distinct ``hypotheses.state`` values and how many rows hold each."""

    transitions: tuple[tuple[str, int], ...]
    """States actually written by a ``hypothesis.state_changed`` event."""

    registrations: tuple[dict[str, Any], ...]
    objections: tuple[dict[str, Any], ...]
    holdout: dict[str, Any] = field(default_factory=dict)
    audit: tuple[tuple[str, str, str, int], ...] = ()
    """The query audit, grouped: role, operation, entity, count."""

    results_by_split: tuple[tuple[str, str, int], ...] = ()
    """``run_results`` grouped by split and by who computed it."""

    seal: dict[str, int] = field(default_factory=dict)
    """The custody boundary, counted: holdout rows by computed_by."""

    cost_by_role: tuple[tuple[str, int, int, int, float], ...] = ()
    """Per role: cost entries, input tokens, output tokens, CPU seconds.

    Entries with no task are the execution floor's compute, which belongs to no
    agent and is billed from wall-clock seconds actually consumed. The token
    counts are real; the dollars in the results file are those counts priced as
    if a named model had produced them, because the mock is free.
    """

    llm_calls: tuple[tuple[str, str, int, int], ...] = ()
    """provider, model, cache_hit, count."""

    first_registration: str = ""
    first_run: str = ""
    """Timestamps bounding the Registry's invariant, read rather than asserted."""

    registrations_before_their_runs: int = 0
    registrations_with_a_run: int = 0

    def count(self, table: str) -> int:
        return self.counts.get(table, 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path.as_posix(),
            "arm_id": self.arm_id,
            "counts": dict(self.counts),
            "events_by_type": [list(row) for row in self.events_by_type],
            "events_by_role": [list(row) for row in self.events_by_role],
            "states_seen": [list(row) for row in self.states_seen],
            "transitions": [list(row) for row in self.transitions],
            "registrations": [dict(row) for row in self.registrations],
            "objections": [dict(row) for row in self.objections],
            "holdout": dict(self.holdout),
            "audit": [list(row) for row in self.audit],
            "results_by_split": [list(row) for row in self.results_by_split],
            "seal": dict(self.seal),
            "cost_by_role": [list(row) for row in self.cost_by_role],
            "llm_calls": [list(row) for row in self.llm_calls],
            "first_registration": self.first_registration,
            "first_run": self.first_run,
            "registrations_before_their_runs": self.registrations_before_their_runs,
            "registrations_with_a_run": self.registrations_with_a_run,
        }


_TABLES = (
    "artifacts",
    "claims",
    "code_bundles",
    "cost_entries",
    "datasets",
    "decisions",
    "events",
    "evidence",
    "follow_ups",
    "forecast_scores",
    "forecasts",
    "holdout_queries",
    "hypotheses",
    "labs",
    "llm_calls",
    "objection_resolutions",
    "objections",
    "policies",
    "positions",
    "programs",
    "query_audit",
    "registrations",
    "replications",
    "research_questions",
    "reviews",
    "run_results",
    "runs",
    "sources",
    "tasks",
)

#: How many registrations the Registry panel lists. The invariant is checked
#: over every one of them; the table shows a sample, and says how many it is of.
REGISTRY_SAMPLE = 12


def open_ledger(path: Path) -> LedgerView:
    """Read one arm's ledger. Raises if the file is not there."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no ledger at {path}")

    with _connect(path) as connection:
        present = _tables(connection)
        counts = {
            table: int(connection.execute(f'select count(*) from "{table}"').fetchone()[0])
            for table in _TABLES
            if table in present
        }

        def rows(
            table: str | tuple[str, ...],
            sql: str,
            parameters: Sequence[object] = (),
        ) -> tuple[dict[str, Any], ...]:
            return _rows(connection, present, table, sql, parameters)

        events_by_type = tuple(
            (str(r["event_type"]), int(r["n"]))
            for r in rows(
                "events",
                "select event_type, count(*) as n from events group by 1 order by 2 desc",
            )
        )
        events_by_role = tuple(
            (str(r["actor_role"]), int(r["n"]))
            for r in rows(
                "events",
                "select actor_role, count(*) as n from events group by 1 order by 2 desc",
            )
        )
        states_seen = tuple(
            (str(r["state"]), int(r["n"]))
            for r in rows("hypotheses", "select state, count(*) as n from hypotheses group by 1")
        )
        transitions = _transitions(rows)
        registrations = rows(
            "registrations",
            "select registration_id, hypothesis_id, kind, spec_hash, seed_root, n_seeds, "
            "holdout_query_budget, registered_at, locked from registrations "
            f"order by registered_at limit {REGISTRY_SAMPLE}",
        )
        objections = rows(
            "objections",
            "select type, severity, status, statement, discriminating_test, raised_by_role, "
            "was_injected_defect, created_at from objections order by created_at",
        )
        holdout_rows = rows(
            "holdout_queries",
            "select granted, count(*) as n, min(remaining_budget) as low from holdout_queries "
            "group by granted",
        )
        holdout = {
            "granted": sum(int(r["n"]) for r in holdout_rows if r["granted"]),
            "refused": sum(int(r["n"]) for r in holdout_rows if not r["granted"]),
            "lowest_remaining_budget": min(
                (int(r["low"]) for r in holdout_rows if r["low"] is not None), default=0
            ),
        }
        audit = tuple(
            (str(r["role"]), str(r["operation"]), str(r["entity"]), int(r["n"]))
            for r in rows(
                "query_audit",
                "select role, operation, entity, count(*) as n from query_audit "
                "group by 1,2,3 order by 4 desc",
            )
        )
        results_by_split = tuple(
            (str(r["split"]), str(r["computed_by"]), int(r["n"]))
            for r in rows(
                "run_results",
                "select split, computed_by, count(*) as n from run_results "
                "group by 1,2 order by 1,2",
            )
        )
        seal = {f"{s}/{w}": n for s, w, n in results_by_split if s == "holdout"}
        cost_by_role = tuple(
            (
                str(r["role"] or "execution (no task)"),
                int(r["n"] or 0),
                int(r["input_tokens"] or 0),
                int(r["output_tokens"] or 0),
                float(r["cpu_seconds"] or 0.0),
            )
            for r in rows(
                ("cost_entries", "tasks"),
                "select t.role as role, count(*) as n, sum(c.llm_input_tokens) as input_tokens, "
                "sum(c.llm_output_tokens) as output_tokens, sum(c.cpu_seconds) as cpu_seconds "
                "from cost_entries as c left join tasks as t on t.task_id = c.task_id "
                "group by 1 order by 2 desc",
            )
        )
        llm_calls = tuple(
            (str(r["provider"]), str(r["model"]), int(r["cache_hit"]), int(r["n"]))
            for r in rows(
                "llm_calls",
                "select provider, model, cache_hit, count(*) as n from llm_calls "
                "group by 1,2,3 order by 4 desc",
            )
        )
        first_registration, first_run = _boundaries(rows)
        before, with_run = _seal_check(rows)

    return LedgerView(
        path=path,
        arm_id=path.stem,
        counts=counts,
        events_by_type=events_by_type,
        events_by_role=events_by_role,
        states_seen=states_seen,
        transitions=transitions,
        registrations=registrations,
        objections=objections,
        holdout=holdout,
        audit=audit,
        results_by_split=results_by_split,
        seal=seal,
        cost_by_role=cost_by_role,
        llm_calls=llm_calls,
        first_registration=first_registration,
        first_run=first_run,
        registrations_before_their_runs=before,
        registrations_with_a_run=with_run,
    )


def _transitions(rows: Any) -> tuple[tuple[str, int], ...]:
    """Which states a ``hypothesis.state_changed`` event actually wrote.

    Read out of the event payload rather than off the ``hypotheses`` row,
    because the row holds only where a hypothesis stopped and the events hold
    everywhere it went. The difference between this and
    :data:`~nullius.station.map.PIPELINE_STATES` is the station's own finding.
    """
    counted: dict[str, int] = {}
    for row in rows(
        "events",
        "select payload from events where event_type = 'hypothesis.state_changed'",
    ):
        try:
            payload = json.loads(str(row["payload"]))
        except (TypeError, ValueError):
            continue
        state = payload.get("row", {}).get("state")
        if isinstance(state, str):
            counted[state] = counted.get(state, 0) + 1
    return tuple(sorted(counted.items(), key=lambda item: -item[1]))


def _boundaries(rows: Any) -> tuple[str, str]:
    registered = rows("registrations", "select min(registered_at) as t from registrations")
    started = rows("runs", "select min(started_at) as t from runs")
    first_registration = str(registered[0]["t"]) if registered and registered[0]["t"] else ""
    first_run = str(started[0]["t"]) if started and started[0]["t"] else ""
    return first_registration, first_run


def _seal_check(rows: Any) -> tuple[int, int]:
    """How many runs began after the registration that authorised them.

    The trigger already refuses a run with no prior locked registration. This
    counts the same thing from the timestamps, so the page shows the invariant
    holding on this record rather than asserting that a constraint exists.
    """
    counted = rows(
        ("runs", "registrations"),
        "select count(*) as n, sum(case when r.registered_at <= runs.started_at then 1 else 0 end) "
        "as ordered from runs join registrations as r "
        "on r.registration_id = runs.registration_id",
    )
    if not counted:
        return 0, 0
    total = int(counted[0]["n"] or 0)
    ordered = int(counted[0]["ordered"] or 0)
    return ordered, total
