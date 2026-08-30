"""Command line entry point.

Subcommands are added milestone by milestone; see ``BUILD_PLAN.md``. Anything
not yet implemented is absent rather than stubbed, so ``--help`` is always an
honest statement of what works.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from nullius import __version__
from nullius.environment import Capabilities, detect

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


if __name__ == "__main__":  # pragma: no cover
    app()
