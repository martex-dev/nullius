"""Engine and session construction.

Two things happen here that are easy to get wrong and expensive to discover
late:

**Foreign keys are switched on.** SQLite disables them per connection by
default. A provenance graph whose foreign keys are advisory is not a
provenance graph, so the pragma is set on every connect and the schema is
refused if it did not take.

**Invariant triggers are installed with the schema.** ``create_schema`` cannot
produce a database without them; see :mod:`nullius.db.triggers`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from nullius.db.tables import Base

__all__ = [
    "DEFAULT_DATABASE_FILENAME",
    "create_engine",
    "create_schema",
    "session_factory",
]

DEFAULT_DATABASE_FILENAME = "nullius.sqlite"


def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        # Triggers on a table must fire for statements issued by other
        # triggers; without this, nested enforcement would silently not run.
        cursor.execute("PRAGMA recursive_triggers=ON")
    finally:
        cursor.close()


def create_engine(url: str | Path, *, echo: bool = False) -> sa.Engine:
    """Build an engine for ``url``.

    A bare filesystem path is accepted and interpreted as SQLite, because that
    is how the CLI is used in practice.
    """
    if isinstance(url, Path) or "://" not in str(url):
        url = f"sqlite+pysqlite:///{Path(url).as_posix()}"

    engine = sa.create_engine(str(url), echo=echo, future=True)
    if engine.dialect.name == "sqlite":
        sa.event.listens_for(engine, "connect")(_configure_sqlite)
    return engine


def create_schema(engine: sa.Engine) -> None:
    """Create every table and install every invariant.

    Verifies afterwards that foreign keys are actually enforced, rather than
    trusting that the pragma was applied.
    """
    Base.metadata.create_all(engine)

    if engine.dialect.name == "sqlite":
        with engine.connect() as connection:
            enabled = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
            if not enabled:
                raise RuntimeError(
                    "SQLite foreign keys are not enforced on this connection; "
                    "provenance links would be advisory"
                )


def session_factory(engine: sa.Engine) -> sessionmaker[Session]:
    """Session maker with autoflush off.

    Autoflush would emit partial writes at arbitrary points, which makes the
    ordering that the preregistration trigger checks hard to reason about.
    The repository flushes explicitly.
    """
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
