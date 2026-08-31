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
bank_app = typer.Typer(
    help="The question bank and its measured ground truth.", no_args_is_help=True
)
app.add_typer(db_app, name="db")
app.add_typer(ledger_app, name="ledger")
app.add_typer(store_app, name="store")
cost_app = typer.Typer(help="Estimate what a research programme will cost.", no_args_is_help=True)
app.add_typer(bank_app, name="bank")
app.add_typer(cost_app, name="cost")

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


LockOption = Annotated[Path, typer.Option("--lock", "-l", help="Path to the truth lock file.")]


@bank_app.command("stats")
def bank_stats(lock: LockOption = Path("bank/truth.lock.json")) -> None:
    """Show what the bank contains, and how far each truth sits from a boundary."""
    from collections import Counter

    from nullius.bank import BANK_V1, read_lock
    from nullius.bank.truth import MIN_BOUNDARY_MARGIN, boundary_margin

    truths = read_lock(lock)
    table = Table(box=None, padding=(0, 2, 0, 0))
    for column in ("item", "effect", "std err", "verdict", "margin"):
        table.add_column(column, justify="right" if column != "verdict" else "left")

    for item in BANK_V1:
        truth = truths[item.item_id]
        margin = boundary_margin(truth)
        colour = "red" if margin < MIN_BOUNDARY_MARGIN else "dim"
        table.add_row(
            item.item_id,
            f"{truth.effect:+.4f}",
            f"{truth.standard_error:.5f}",
            truth.verdict.value,
            f"[{colour}]{margin:.1f} SE[/{colour}]",
        )

    counts = Counter(t.verdict.value for t in truths.values())
    total = len(truths)
    console.print()
    console.print(table)
    console.print()
    for verdict, count in sorted(counts.items()):
        console.print(f"  [dim]{verdict:14}[/dim] {count:2}  ({count / total:.0%})")
    console.print()
    console.print(
        f"  null fraction [bold]{counts['no_effect'] / total:.0%}[/bold] "
        "[dim](docs/04 requires at least 45%)[/dim]"
    )
    console.print()


@bank_app.command("verify")
def bank_verify(lock: LockOption = Path("bank/truth.lock.json")) -> None:
    """Recompute every truth from the generating process and compare to the lock.

    Exits non-zero on drift, so a change to the data generating process cannot
    quietly change what the institution is scored against.
    """
    from nullius.bank import validate_bank, verify
    from nullius.bank.lock import read_lock
    from nullius.bank.truth import ambiguous

    structure = validate_bank()
    if not structure.ok:
        console.print(f"[red]{structure}[/red]")
        raise typer.Exit(code=1)

    unclear = ambiguous(list(read_lock(lock).values()))
    if unclear:
        console.print(
            f"[red]bank unfit: {unclear} sit within 3 standard errors of a verdict "
            "boundary, so the oracle cannot decide them either[/red]"
        )
        raise typer.Exit(code=1)

    console.print("[dim]recomputing every truth from the generating process…[/dim]")
    result = verify(lock)
    if result.ok:
        console.print(f"[green]{result}[/green]")
        return
    console.print(f"[red]{result}[/red]")
    raise typer.Exit(code=1)


@cost_app.command("estimate")
def cost_estimate(
    cycles: Annotated[int, typer.Option("--cycles", "-c", help="Research cycles to price.")] = 1,
    retry_multiplier: Annotated[
        float, typer.Option("--retries", help="Allowance for schema repairs and follow-ups.")
    ] = 1.3,
) -> None:
    """Price a research cycle from the prompts the roles will actually send.

    Input sizes are measured from the real contracts. Only the reply length is
    estimated, and it is shown as a range because adaptive thinking - billed as
    output - dominates it.
    """
    from nullius.costing import PROMPT_CACHE_MINIMUM_TOKENS, estimate_programme
    from nullius.roles.contracts import CONTRACTS

    estimate = estimate_programme(CONTRACTS, cycles=cycles, retry_multiplier=retry_multiplier)

    table = Table(box=None, padding=(0, 2, 0, 0))
    for column, justify in (
        ("role", "left"),
        ("model", "left"),
        ("input tok", "right"),
        ("output tok", "right"),
        ("usd low", "right"),
        ("usd high", "right"),
    ):
        table.add_column(column, justify=justify)  # type: ignore[arg-type]

    for call in estimate.calls:
        table.add_row(
            call.role,
            call.model,
            f"{call.input_tokens:,}",
            f"{call.output_low:,}-{call.output_high:,}",
            f"${call.usd_low:.5f}",
            f"${call.usd_high:.5f}",
        )

    console.print()
    console.print(table)
    console.print()
    console.print(
        f"  per cycle   [bold]${estimate.per_cycle_low:.3f} - ${estimate.per_cycle_high:.3f}[/bold]"
        f"  [dim](x{retry_multiplier} for repairs and follow-ups)[/dim]"
    )
    console.print(
        f"  {cycles} cycle(s)  [bold]${estimate.total_low:.2f} - ${estimate.total_high:.2f}[/bold]"
    )

    if not any(call.prompt_caches for call in estimate.calls):
        console.print()
        console.print(
            f"  [yellow]![/yellow] no role's system prompt reaches the "
            f"{PROMPT_CACHE_MINIMUM_TOKENS}-token minimum for prompt caching, so the "
            "cached-input discount is not applied. The response cache still makes an "
            "exact repeat free."
        )
    console.print()


if __name__ == "__main__":  # pragma: no cover
    app()
