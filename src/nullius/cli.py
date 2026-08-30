"""Command line entry point.

Subcommands are added milestone by milestone; see ``BUILD_PLAN.md``. Anything
not yet implemented is absent rather than stubbed, so ``--help`` is always an
honest statement of what works.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from nullius import __version__
from nullius.db.base import (
    DEFAULT_DATABASE_FILENAME,
    create_engine,
    create_schema,
    session_factory,
)
from nullius.db.triggers import invariant_ddl
from nullius.environment import Capabilities, detect
from nullius.ledger.ledger import Ledger
from nullius.ledger.rebuild import reconciliation
from nullius.store.cas import ContentStore

app = typer.Typer(
    name="nullius",
    help="An artificial research institution. Take nobody's word for it.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command()
def version() -> None:
    """Print the installed version."""
    console.print(f"nullius {__version__}")


def _capability_table(caps: Capabilities) -> Table:
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column(style="dim")
    table.add_column()

    table.add_row("python", caps.python_version)
    table.add_row("platform", f"{caps.platform} ({caps.machine})")
    table.add_row("cpus", str(caps.cpu_count))
    table.add_row(
        "isolation tier",
        f"[bold]{caps.isolation_tier}[/bold]"
        + (f"  ({caps.docker_version})" if caps.docker_version else ""),
    )
    table.add_row("visibility tier", f"[bold]{caps.visibility_tier}[/bold]")
    table.add_row("live provider", caps.live_provider or "[dim]none[/dim]")
    table.add_row("git commit", (caps.git_commit or "unknown")[:12])
    table.add_row("capability digest", caps.digest()[:16])
    return table


@app.command()
def doctor() -> None:
    """Report what this host can enforce, and what it cannot.

    The tiers reported here are stored in the provenance of every run, so a
    claim produced under a weaker tier stays identifiable as such.
    """
    caps = detect()
    console.print()
    console.print("[bold]nullius doctor[/bold]")
    console.print()
    console.print(_capability_table(caps))

    if caps.warnings:
        console.print()
        for warning in caps.warnings:
            console.print(f"[yellow]![/yellow] {warning}")
    console.print()


db_app = typer.Typer(help="Create and inspect the institutional database.", no_args_is_help=True)
ledger_app = typer.Typer(help="Verify the integrity of the event ledger.", no_args_is_help=True)
store_app = typer.Typer(help="Verify the content-addressed artifact store.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(ledger_app, name="ledger")
app.add_typer(store_app, name="store")

DatabaseOption = Annotated[
    Path,
    typer.Option("--database", "-d", help="Path to the SQLite database, or a connection URL."),
]
StoreOption = Annotated[
    Path,
    typer.Option("--store", "-s", help="Root of the content-addressed artifact store."),
]


@db_app.command("init")
def db_init(database: DatabaseOption = Path(DEFAULT_DATABASE_FILENAME)) -> None:
    """Create the schema and install every invariant trigger.

    There is no way to create a schema without its invariants; a database
    whose scientific rules are unenforced is not a Nullius database.
    """
    engine = create_engine(database)
    create_schema(engine)

    dialect = engine.dialect.name
    console.print(
        f"[green]created[/green] {dialect} schema at [bold]{database}[/bold] "
        f"with {len(invariant_ddl(dialect))} invariant statements"
    )


@ledger_app.command("verify")
def ledger_verify(database: DatabaseOption = Path(DEFAULT_DATABASE_FILENAME)) -> None:
    """Recompute the hash chain over every event.

    Exits non-zero if the ledger has been altered, so this is usable as a gate
    in CI or a cron job rather than only as a human-read report.
    """
    engine = create_engine(database)
    with session_factory(engine)() as session:
        result = Ledger(session).verify()

    if result.ok:
        console.print(f"[green]{result}[/green]")
        return
    console.print(f"[red]{result}[/red]")
    raise typer.Exit(code=1)


@ledger_app.command("reconcile")
def ledger_reconcile(database: DatabaseOption = Path(DEFAULT_DATABASE_FILENAME)) -> None:
    """Rebuild every table from the event log and diff it against the tables.

    Proves that state really is a fold over the ledger. A row written without
    its event shows up here, named.
    """
    engine = create_engine(database)
    with session_factory(engine)() as session:
        result = reconciliation(session)

    if result.ok:
        console.print(f"[green]{result}[/green]")
        return

    console.print(f"[red]{result}[/red]")
    for label, rows in (
        ("written without an event", result.missing_from_log),
        ("in the log but not in the tables", result.missing_from_tables),
        ("differing from the log", result.mismatched),
    ):
        for row in rows[:10]:
            console.print(f"  [dim]{label}:[/dim] {row}")
    raise typer.Exit(code=1)


@store_app.command("verify")
def store_verify(store: StoreOption = Path("objects")) -> None:
    """Rehash every artifact and report any whose bytes have changed."""
    content_store = ContentStore(store)
    corrupted = content_store.verify_all()
    total = len(content_store)

    if not corrupted:
        console.print(f"[green]store intact: {total} artifacts verified[/green]")
        return

    console.print(f"[red]store CORRUPTED: {len(corrupted)} of {total} artifacts[/red]")
    for digest in corrupted[:10]:
        console.print(f"  [dim]{digest}[/dim]")
    raise typer.Exit(code=1)


if __name__ == "__main__":  # pragma: no cover
    app()
