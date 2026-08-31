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
economy_app = typer.Typer(
    help="Allocation policies, and whether any of them beats chance.", no_args_is_help=True
)
benchmark_app = typer.Typer(
    help="The B0-B7 ladder, and the protocol registered before it runs.",
    no_args_is_help=True,
)
app.add_typer(bank_app, name="bank")
app.add_typer(cost_app, name="cost")
app.add_typer(economy_app, name="economy")
app.add_typer(benchmark_app, name="benchmark")

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


# ---------------------------------------------------------------- economy

OutcomesOption = Annotated[
    Path,
    typer.Option("--outcomes", "-o", help="Path to the measured bank outcomes lock."),
]
BudgetOption = Annotated[
    float,
    typer.Option("--budget", "-b", help="Budget the policies are allocating, in USD."),
]


@economy_app.command("policies")
def economy_policies() -> None:
    """List the allocation policies, and what each one ranks by."""
    from nullius.economy.policy import POLICIES

    table = Table(box=None, padding=(0, 2, 0, 0))
    table.add_column("version")
    table.add_column("ranks by")

    for version, cls in sorted(POLICIES.items()):
        summary = (cls.__doc__ or "").strip().splitlines()[0]
        table.add_row(version, summary)

    console.print()
    console.print(table)
    console.print()


@economy_app.command("measure")
def economy_measure(
    outcomes: OutcomesOption = Path("bank/outcomes.lock.json"),
    workroot: Annotated[
        Path, typer.Option("--workroot", help="Scratch directory for runs and artifacts.")
    ] = Path(".nullius-measure"),
) -> None:
    """Carry every bank item through the lifecycle and lock what it produced.

    Slow and deliberate — one full research programme per item. The result is
    committed, so the allocation comparison runs from a measurement rather than
    re-running the science every time somebody changes a ranking rule.
    """
    from nullius.economy.outcomes import measure_bank, write_outcomes

    console.print("[dim]running every bank item through the lifecycle…[/dim]")
    measured = measure_bank(workroot / "measure.sqlite", workroot)
    for outcome in measured:
        colour = "green" if outcome.correct else "yellow"
        console.print(f"  [{colour}]{outcome}[/{colour}]")

    path = write_outcomes(measured, outcomes)
    correct = sum(1 for o in measured if o.correct)
    console.print()
    console.print(f"  {correct}/{len(measured)} correct, written to [bold]{path}[/bold]")
    console.print()


@economy_app.command("compare")
def economy_compare(
    outcomes: OutcomesOption = Path("bank/outcomes.lock.json"),
    budget: BudgetOption = 0.05,
    resamples: Annotated[
        int, typer.Option("--resamples", help="Bootstrap resamples over bank items.")
    ] = 2000,
) -> None:
    """Run every allocation policy over the measured bank and report the result.

    The acceptance criterion for M9 is whether greedy-EIG separates from random
    on cost per correct claim. Both answers are printed the same way.
    """
    from decimal import Decimal

    from nullius.economy.harness import compare_policies
    from nullius.economy.outcomes import outcomes_are_current, read_outcomes

    measured = read_outcomes(outcomes)
    if not outcomes_are_current(outcomes):
        console.print(
            "[yellow]![/yellow] these outcomes were measured on a different bank; "
            "re-run [bold]nullius economy measure[/bold]"
        )

    report = compare_policies(measured, budget_usd=Decimal(str(budget)), resamples=resamples)

    table = Table(box=None, padding=(0, 2, 0, 0))
    for column, justify in (
        ("policy", "left"),
        ("funded", "right"),
        ("correct", "right"),
        ("usd", "right"),
        ("$/correct", "right"),
        ("nats/$", "right"),
    ):
        table.add_column(column, justify=justify)  # type: ignore[arg-type]

    for result in report.results:
        per_correct = "—" if result.correct == 0 else f"${result.cost_per_correct_claim:.4f}"
        table.add_row(
            result.policy_version,
            str(result.funded),
            str(result.correct),
            f"${result.usd:.4f}",
            per_correct,
            f"{result.nats_per_dollar:.1f}",
        )

    console.print()
    console.print(
        f"  [dim]forecasts: {report.forecast_source}, "
        f"{report.n_items} items, budget ${report.budget_usd:.4f}[/dim]"
    )
    console.print()
    console.print(table)
    console.print()
    console.print(f"  [dim]EIG spread across items: {report.eig_spread:.6f} nats[/dim]")
    if report.eig_spread == 0:
        console.print(
            "  [yellow]![/yellow] every item scored the same expected information gain, "
            "so no policy could rank on it. Run [bold]nullius economy sweep[/bold] to see "
            "what better forecasts would be worth."
        )
    console.print()
    for difference in report.differences:
        colour = "green" if difference.separates and difference.observed > 0 else "dim"
        console.print(f"  [{colour}]{difference}[/{colour}]")
    console.print()


@economy_app.command("sweep")
def economy_sweep(
    outcomes: OutcomesOption = Path("bank/outcomes.lock.json"),
    budget: BudgetOption = 0.05,
    resamples: Annotated[
        int, typer.Option("--resamples", help="Bootstrap resamples per rung.")
    ] = 400,
) -> None:
    """Dial forecast quality from nothing to oracle-grade and watch the gap.

    Answers the question the comparison cannot: under a mock provider every
    role forecasts the same thing about every item, so a null result there is a
    fact about the forecasts rather than about the policy. The top of this
    ladder uses ground truth no role may see, and is an upper bound only.
    """
    from decimal import Decimal

    from nullius.economy.harness import sweep_informativeness
    from nullius.economy.outcomes import read_outcomes

    report = sweep_informativeness(
        read_outcomes(outcomes), budget_usd=Decimal(str(budget)), resamples=resamples
    )

    console.print()
    for point in report.points:
        colour = "green" if point.difference.separates else "dim"
        console.print(f"  [{colour}]{point}[/{colour}]")
        control = "green" if point.information_helped else "dim"
        console.print(f"      [{control}]vs cost control: {point.against_cost_control}[/{control}]")
    console.print()

    first = report.first_separating
    if first is None:
        console.print(
            "  greedy-EIG does not separate from random at any forecast quality on this bank."
        )
    else:
        console.print(
            f"  greedy-EIG first separates from random at forecast quality "
            f"[bold]lambda={first.informativeness:.2f}[/bold]."
        )

    isolated = report.first_beating_the_cost_control
    if isolated is None:
        console.print(
            "  It never beats the cost-only control, so nothing it gained can be "
            "attributed to the information term."
        )
    else:
        console.print(
            f"  It first beats the cost-only control at "
            f"[bold]lambda={isolated.informativeness:.2f}[/bold] - the point at which "
            "the information term is doing the work."
        )
    console.print()


@economy_app.command("round")
def economy_round(
    questions: Annotated[
        int, typer.Option("--questions", "-n", help="Bank items to put up for funding.")
    ] = 4,
    budget: BudgetOption = 0.02,
    policy: Annotated[
        str, typer.Option("--policy", "-p", help="Allocation policy version.")
    ] = "greedy-eig/v1",
    workroot: Annotated[
        Path, typer.Option("--workroot", help="Scratch directory for runs and artifacts.")
    ] = Path(".nullius-round"),
) -> None:
    """Put several questions up for funding and run only the ones that win.

    The economy governing the institution rather than sitting beside it: every
    question is proposed to a locked registration and locked forecasts, the
    policy allocates the laboratory's budget across them, and only the funded
    ones are executed. The rest keep their registrations and reach
    ABANDONED_BUDGET, so what was declined stays on the record.
    """
    from decimal import Decimal

    from nullius.bank.items import BANK_V1
    from nullius.db.base import create_engine, create_schema, session_factory
    from nullius.db.enums import Role
    from nullius.economy.outcomes import canned_responder
    from nullius.economy.policy import policy_named
    from nullius.economy.round import FundingRound
    from nullius.execute.sandbox import SubprocessSandbox
    from nullius.kernel import ResearchKernel
    from nullius.llm.providers import MockProvider
    from nullius.repository import Repository
    from nullius.store.cas import ContentStore

    workroot.mkdir(parents=True, exist_ok=True)
    engine = create_engine(workroot / "round.sqlite")
    create_schema(engine)

    with session_factory(engine)() as session:
        repo = Repository(session, Role.SYSTEM)
        lab = repo.create_lab("Nullius", "Measure whether structure helps.")
        stored = repo.create_policy(
            f"round-{policy.replace('/', '-')}",
            {"allocation_class": policy},
            "One funding round over the question bank.",
        )
        kernel = ResearchKernel(
            repo,
            MockProvider(canned_responder()),
            SubprocessSandbox(),
            ContentStore(workroot / "objects"),
            workroot / "runs",
            mock=True,
        )
        round_ = FundingRound(
            kernel=kernel,
            repo=repo,
            lab_id=lab.lab_id,
            policy_id=stored.policy_id,
            policy=policy_named(policy),
        )

        console.print(
            f"[dim]proposing {questions} question(s), then allocating "
            f"${budget:.4f} by {policy}...[/dim]"
        )
        result = round_.run(list(BANK_V1[:questions]), budget_usd=Decimal(str(budget)))
        repo.commit()

    table = Table(box=None, padding=(0, 2, 0, 0))
    for column in ("question", "decision", "verdict"):
        table.add_column(column)

    for outcome in result.executed:
        verdict = outcome.verdict.verdict.value if outcome.verdict else "-"
        table.add_row(outcome.item_id, "[green]funded[/green]", verdict)
    for outcome in result.unfunded:
        table.add_row(outcome.item_id, "[yellow]shelved[/yellow]", outcome.halted or "-")

    console.print()
    console.print(table)
    console.print()
    console.print(f"  {result}")
    for line in result.halted:
        console.print(f"  [yellow]![/yellow] {line}")
    console.print()


# -------------------------------------------------------------- benchmark

ProtocolOption = Annotated[
    Path, typer.Option("--protocol", help="Path to the registered protocol lock.")
]


@benchmark_app.command("ladder")
def benchmark_ladder() -> None:
    """Show the arms, and which mechanism each one adds."""
    from nullius.benchmark.arms import LADDER

    table = Table(box=None, padding=(0, 2, 0, 0))
    for column in ("arm", "composition", "prereg", "custody", "skeptic", "replic", "memory"):
        table.add_column(column)

    def mark(on: bool) -> str:
        return "[green]yes[/green]" if on else "[dim]-[/dim]"

    for arm in LADDER:
        table.add_row(
            f"{arm.arm_id}{' *' if arm.model_dependent else ''}",
            arm.label,
            mark(arm.preregistered),
            mark(arm.custodian),
            mark(arm.adversary),
            mark(arm.replication),
            mark(arm.memory),
        )

    console.print()
    console.print(table)
    console.print()
    console.print(
        "  [dim]* behaviour dominated by the language model; under a mock provider "
        "these arms describe the mock.[/dim]"
    )
    console.print()


@benchmark_app.command("preregister")
def benchmark_preregister(protocol: ProtocolOption = Path("benchmark/protocol.lock.json")) -> None:
    """Fix the analysis plan and hash it, before any result exists.

    Refuses to overwrite a different protocol. That refusal is the mechanism:
    a preregistration that can be replaced once the numbers are in is a
    postregistration.
    """
    from nullius.benchmark.protocol import build_protocol, write_protocol

    registered = build_protocol()
    try:
        path = write_protocol(registered, protocol)
    except ValueError as refusal:
        console.print(f"[red]{refusal}[/red]")
        raise typer.Exit(code=1) from None

    console.print()
    console.print(f"  registered [bold]{registered.protocol_hash}[/bold]")
    console.print(f"  written to [bold]{path}[/bold]")
    console.print()
    console.print(f"  [dim]claim:[/dim] {registered.claim}")
    console.print()
    console.print(f"  [dim]prediction:[/dim] {registered.prediction}")
    console.print()
    console.print(
        f"  [dim]bank:[/dim] {registered.bank['n_items']} items, "
        f"truth {registered.bank['truth_lock_hash'][:16]}"
    )
    console.print()


@benchmark_app.command("verify")
def benchmark_verify(protocol: ProtocolOption = Path("benchmark/protocol.lock.json")) -> None:
    """Check the protocol is intact and still describes the current bank.

    Exits non-zero otherwise, so a run against a bank that moved after
    registration cannot be reported as preregistered.
    """
    from nullius.benchmark.protocol import verify_protocol

    result = verify_protocol(protocol)
    if result.ok:
        console.print(f"[green]{result}[/green]")
        return
    console.print(f"[red]{result}[/red]")
    raise typer.Exit(code=1)


@benchmark_app.command("run")
def benchmark_run(
    workdir: Annotated[
        Path, typer.Option(help="Where each arm's database and run tree are written.")
    ] = Path(".nullius/benchmark"),
    results: Annotated[Path, typer.Option(help="Where to write the results lock.")] = Path(
        "benchmark/results.lock.json"
    ),
    protocol: ProtocolOption = Path("benchmark/protocol.lock.json"),
    seed: Annotated[
        int, typer.Option(help="Bootstrap seed. The whole report is a function of it.")
    ] = 0,
) -> None:
    """Run the full B0-B7 ladder and score it against the registered protocol.

    Refuses to run if the protocol does not verify. A result measured against a
    plan that moved after registration is not a preregistered result, and
    producing one anyway would waste the only thing this benchmark has.
    """
    from nullius.benchmark.metrics import score_ladder, write_results
    from nullius.benchmark.protocol import read_protocol, verify_protocol
    from nullius.benchmark.runner import run_ladder

    verification = verify_protocol(protocol)
    if not verification.ok:
        console.print(f"[red]{verification}[/red]")
        raise typer.Exit(code=1)

    registered = read_protocol(protocol)
    console.print()
    console.print(f"  protocol [bold]{registered.protocol_hash[:16]}[/bold] verified")
    console.print(f"  [dim]prediction:[/dim] {registered.prediction}")
    console.print()

    runs = run_ladder(root=workdir)
    report = score_ladder(runs, registered, seed=seed)
    path = write_results(report, runs, results, provider="mock")

    table = Table(box=None, padding=(0, 2, 0, 0))
    for column in ("arm", "acc", "null", "brier", "ece", "fdr", "$/correct", "halted"):
        table.add_column(column)
    for row in report.metrics:
        marker = " *" if row.model_dependent else ""
        per_correct = (
            "[dim]none[/dim]" if row.n_correct == 0 else f"{row.usd_per_correct_claim:.5f}"
        )
        table.add_row(
            f"{row.arm_id}{marker}",
            f"{row.verdict_accuracy:.2f}",
            f"{row.null_accuracy:.2f}",
            f"{row.brier:.3f}",
            f"{row.expected_calibration_error:.3f}",
            f"{row.false_discovery_rate:.2f}",
            per_correct,
            str(row.n_halted),
        )
    console.print(table)
    console.print()

    for comparison in report.comparisons:
        flag = " [dim](model-dependent)[/dim]" if comparison.model_dependent else ""
        console.print(f"  {comparison}{flag}")
    console.print()
    console.print(
        f"  [dim]{report.correction.method}, alpha {report.correction.alpha}: "
        f"{report.correction.n_rejected} of {len(report.comparisons)} survive[/dim]"
    )
    console.print()

    if report.prediction_upheld is None:
        console.print(f"  [yellow]{report.prediction_reason}[/yellow]")
    elif report.prediction_upheld:
        console.print(f"  [green]PREDICTION UPHELD[/green]  {report.prediction_reason}")
    else:
        console.print(f"  [red]PREDICTION REFUTED[/red]  {report.prediction_reason}")
    console.print()
    console.print(f"  written to [bold]{path}[/bold]")
    console.print()
    console.print(
        "  [dim]* behaviour dominated by the language model; this run used a mock "
        "provider, so these arms describe the mock and not a model.[/dim]"
    )
    console.print()


if __name__ == "__main__":  # pragma: no cover
    app()
