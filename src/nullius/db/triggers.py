"""Invariants that need to compare rows.

A ``CHECK`` constraint can only see one row, so the rules below — which are
about *ordering* and *existence* — are triggers. They are installed on both
SQLite and Postgres, in each dialect's own syntax, for one reason: the rules
must hold against raw SQL, not merely against :mod:`nullius.repository`. An
invariant that only our own code enforces is a convention.

The four rules:

``append_only``
    ``UPDATE`` and ``DELETE`` are refused on the ledger tables. History is not
    editable, so a claim's evidence cannot be quietly revised.

``run_requires_prior_registration``
    A run cannot exist unless a *locked* registration for it was recorded no
    later than the run started. This is the anti-HARKing mechanism: results
    are structurally unable to precede the design they test.

``registration_immutable_once_locked``
    The specification, its hash, the analysis plan, the seed root and the kind
    cannot change after locking. A revised design is a new row, degraded to
    ``exploratory``.

``forecast_before_execution``
    A forecast cannot be recorded once any run exists for that registration.
    Predictions made after seeing results are not predictions.
"""

from __future__ import annotations

from collections.abc import Iterator

import sqlalchemy as sa

from nullius.db.tables import APPEND_ONLY_TABLES, Base

__all__ = [
    "expected_trigger_names",
    "install_invariants",
    "installed_trigger_names",
    "invariant_ddl",
    "verify_invariants",
]

_PREFIX = "invariant"


# --------------------------------------------------------------------------
# SQLite
# --------------------------------------------------------------------------


def _sqlite_ddl() -> Iterator[str]:
    for table in APPEND_ONLY_TABLES:
        for verb in ("UPDATE", "DELETE"):
            yield f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table}_no_{verb.lower()}
            BEFORE {verb} ON {table}
            BEGIN
                SELECT RAISE(ABORT,
                    '{_PREFIX}: {table} is append-only ({verb} refused)');
            END;
            """

    # SQLite has no adjacent-string-literal concatenation (PostgreSQL does, per
    # the SQL standard), so each RAISE message must be a single literal.
    yield f"""
    CREATE TRIGGER IF NOT EXISTS trg_runs_require_prior_registration
    BEFORE INSERT ON runs
    BEGIN
        SELECT RAISE(ABORT, '{_PREFIX}: a run requires a locked registration recorded no later than the run started')
        WHERE NOT EXISTS (
            SELECT 1 FROM registrations r
            WHERE r.registration_id = NEW.registration_id
              AND r.locked = 1
              AND r.registered_at <= NEW.started_at
        );
    END;
    """  # noqa: E501 - the message must be one SQLite string literal

    yield f"""
    CREATE TRIGGER IF NOT EXISTS trg_registration_immutable_once_locked
    BEFORE UPDATE ON registrations
    FOR EACH ROW WHEN OLD.locked = 1
    BEGIN
        SELECT RAISE(ABORT, '{_PREFIX}: a locked registration is immutable; record a new exploratory registration instead')
        WHERE NEW.spec_hash        IS NOT OLD.spec_hash
           OR NEW.spec             IS NOT OLD.spec
           OR NEW.analysis_plan    IS NOT OLD.analysis_plan
           OR NEW.seed_root        IS NOT OLD.seed_root
           OR NEW.n_seeds          IS NOT OLD.n_seeds
           OR NEW.kind             IS NOT OLD.kind
           OR NEW.registered_at    IS NOT OLD.registered_at
           OR NEW.locked           IS NOT OLD.locked;
    END;
    """  # noqa: E501 - the message must be one SQLite string literal

    yield f"""
    CREATE TRIGGER IF NOT EXISTS trg_forecast_before_execution
    BEFORE INSERT ON forecasts
    BEGIN
        SELECT RAISE(ABORT, '{_PREFIX}: a forecast cannot be recorded once a run exists for this registration')
        WHERE EXISTS (
            SELECT 1 FROM runs r WHERE r.registration_id = NEW.registration_id
        );
    END;
    """  # noqa: E501 - the message must be one SQLite string literal


# --------------------------------------------------------------------------
# PostgreSQL
# --------------------------------------------------------------------------


def _postgres_ddl() -> Iterator[str]:
    yield f"""
    CREATE OR REPLACE FUNCTION nullius_append_only() RETURNS trigger AS $fn$
    BEGIN
        RAISE EXCEPTION '{_PREFIX}: % is append-only (% refused)',
            TG_TABLE_NAME, TG_OP;
    END;
    $fn$ LANGUAGE plpgsql;
    """

    for table in APPEND_ONLY_TABLES:
        yield f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table};"
        yield f"""
        CREATE TRIGGER trg_{table}_append_only
        BEFORE UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION nullius_append_only();
        """

    yield f"""
    CREATE OR REPLACE FUNCTION nullius_run_requires_registration()
    RETURNS trigger AS $fn$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM registrations r
            WHERE r.registration_id = NEW.registration_id
              AND r.locked IS TRUE
              AND r.registered_at <= NEW.started_at
        ) THEN
            RAISE EXCEPTION '{_PREFIX}: a run requires a locked registration '
                'recorded no later than the run started';
        END IF;
        RETURN NEW;
    END;
    $fn$ LANGUAGE plpgsql;
    """
    yield "DROP TRIGGER IF EXISTS trg_runs_require_prior_registration ON runs;"
    yield """
    CREATE TRIGGER trg_runs_require_prior_registration
    BEFORE INSERT ON runs
    FOR EACH ROW EXECUTE FUNCTION nullius_run_requires_registration();
    """

    yield f"""
    CREATE OR REPLACE FUNCTION nullius_registration_immutable()
    RETURNS trigger AS $fn$
    BEGIN
        IF OLD.locked IS TRUE AND (
               NEW.spec_hash     IS DISTINCT FROM OLD.spec_hash
            OR NEW.spec::text    IS DISTINCT FROM OLD.spec::text
            OR NEW.analysis_plan::text IS DISTINCT FROM OLD.analysis_plan::text
            OR NEW.seed_root     IS DISTINCT FROM OLD.seed_root
            OR NEW.n_seeds       IS DISTINCT FROM OLD.n_seeds
            OR NEW.kind          IS DISTINCT FROM OLD.kind
            OR NEW.registered_at IS DISTINCT FROM OLD.registered_at
            OR NEW.locked        IS DISTINCT FROM OLD.locked
        ) THEN
            RAISE EXCEPTION '{_PREFIX}: a locked registration is immutable; '
                'record a new exploratory registration instead';
        END IF;
        RETURN NEW;
    END;
    $fn$ LANGUAGE plpgsql;
    """
    yield "DROP TRIGGER IF EXISTS trg_registration_immutable ON registrations;"
    yield """
    CREATE TRIGGER trg_registration_immutable
    BEFORE UPDATE ON registrations
    FOR EACH ROW EXECUTE FUNCTION nullius_registration_immutable();
    """

    yield f"""
    CREATE OR REPLACE FUNCTION nullius_forecast_before_execution()
    RETURNS trigger AS $fn$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM runs r WHERE r.registration_id = NEW.registration_id
        ) THEN
            RAISE EXCEPTION '{_PREFIX}: a forecast cannot be recorded once a '
                'run exists for this registration';
        END IF;
        RETURN NEW;
    END;
    $fn$ LANGUAGE plpgsql;
    """
    yield "DROP TRIGGER IF EXISTS trg_forecast_before_execution ON forecasts;"
    yield """
    CREATE TRIGGER trg_forecast_before_execution
    BEFORE INSERT ON forecasts
    FOR EACH ROW EXECUTE FUNCTION nullius_forecast_before_execution();
    """


def invariant_ddl(dialect: str) -> list[str]:
    """Return the invariant DDL statements for ``dialect``."""
    match dialect:
        case "sqlite":
            return [sql.strip() for sql in _sqlite_ddl()]
        case "postgresql":
            return [sql.strip() for sql in _postgres_ddl()]
        case _:
            raise NotImplementedError(
                f"no invariant DDL for dialect {dialect!r}; refusing to create a "
                "database whose scientific invariants are unenforced"
            )


ROW_COMPARING_TRIGGERS: tuple[str, ...] = (
    "runs_require_prior_registration",
    "registration_immutable_once_locked",
    "forecast_before_execution",
)
"""The invariants that need to compare rows, named for verification."""


def expected_trigger_names(dialect: str) -> frozenset[str]:
    """Every trigger that must exist for the schema to be trustworthy."""
    match dialect:
        case "sqlite":
            append_only = {
                f"trg_{table}_no_{verb}"
                for table in APPEND_ONLY_TABLES
                for verb in ("update", "delete")
            }
            return frozenset(append_only | {f"trg_{name}" for name in ROW_COMPARING_TRIGGERS})
        case "postgresql":
            append_only = {f"trg_{table}_append_only" for table in APPEND_ONLY_TABLES}
            return frozenset(
                append_only
                | {
                    "trg_runs_require_prior_registration",
                    "trg_registration_immutable",
                    "trg_forecast_before_execution",
                }
            )
        case _:
            raise NotImplementedError(f"no invariant DDL for dialect {dialect!r}")


def installed_trigger_names(connection: sa.Connection) -> frozenset[str]:
    """Trigger names actually present in the database."""
    match connection.dialect.name:
        case "sqlite":
            query = "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        case "postgresql":
            query = "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"
        case name:
            raise NotImplementedError(f"cannot enumerate triggers on dialect {name!r}")
    return frozenset(row[0] for row in connection.exec_driver_sql(query))


def verify_invariants(connection: sa.Connection) -> None:
    """Fail unless every invariant trigger is present.

    A partially installed rule set is the worst possible state: it looks like
    an enforced schema and behaves like an unenforced one. Rather than trust
    that the DDL ran, check afterwards that it took.
    """
    missing = expected_trigger_names(connection.dialect.name) - installed_trigger_names(connection)
    if missing:
        raise RuntimeError(
            "scientific invariants are not installed: missing "
            f"{sorted(missing)}. This database must not be used to record research."
        )


def install_invariants(connection: sa.Connection) -> None:
    """Install every invariant trigger on an existing schema, then verify it took."""
    for statement in invariant_ddl(connection.dialect.name):
        connection.exec_driver_sql(statement)
    verify_invariants(connection)


@sa.event.listens_for(Base.metadata, "after_create")
def _install_after_create(
    target: sa.MetaData,
    connection: sa.Connection,
    **kwargs: object,
) -> None:
    """Attach invariants to ``metadata.create_all``.

    Deliberately automatic: a schema created without its invariants is not a
    Nullius schema, and making that an opt-in step invites forgetting it.
    """
    install_invariants(connection)
