# Nullius — Build Plan

> *Nullius in verba* — take nobody's word for it.

This is the executable plan derived from [`docs/`](docs/). The design documents say *what* to build and *why*; this says *in what order*, *with what acceptance test*, and *what changes because of the machine we're actually on*.

**Status:** M0–M9 complete (M6–M9 mock-driven; the first live run awaits an API key). M10 next. Nothing below is claimed as done until its acceptance criteria are green in CI.

---

## 0. Environment reality

Recon on the build machine (2026-08-30):

| | |
|---|---|
| OS | Windows 11 Pro, 28 CPUs |
| Python | 3.12.10 · uv 0.12.7 |
| Node | 24.18.0 |
| Git / GitHub | git 2.55 · `gh` authenticated as `martex-dev` |
| **Docker / Podman** | **absent** |
| **WSL2** | **absent** |
| **PostgreSQL** | **absent** |
| **LLM API key** | **absent** |

Four constraints, four decisions. Each is recorded as an ADR in [`docs/adr/`](docs/adr/) and each is reversible.

### D-1 · SQLite is the default store; Postgres is opt-in
`docs/03-data-model.md` targets Postgres 16. At MVP scale (~10⁵ events, ~10⁴ result rows) SQLite is entirely sufficient, and it makes the repository's central promise — *reproducible from a clean clone* — literally true: `git clone && uv sync && nullius demo`, no services.

Schema is written once in SQLAlchemy; both backends run the same migrations. Postgres-only hardening (row-level security for Replicator blindness) becomes a backend capability rather than an assumption, and where it is unavailable the same invariant is enforced by the role-scoped repository layer plus a query audit log — which is more testable anyway. See [ADR-0001](docs/adr/0001-sqlite-default-postgres-optional.md).

### D-2 · The sandbox has three backends, and the MVP doesn't need the strong one
`SandboxBackend` is an interface with `SubprocessSandbox` (default: AST validation, import allowlist, a `sys.addaudithook` denying sockets/subprocess/out-of-workdir writes, wall-clock kill, psutil memory cap), `DockerSandbox` (the full hardening set in `docs/05-security.md`), and room for gVisor later.

The load-bearing point: **the MVP executes no LLM-written code at all.** Builder-as-compiler means experiment code is our own unit-tested harness. Container isolation is therefore not on the critical path until M12 (code generation). Until then `SubprocessSandbox` is defence against *accidental* misbehaviour, which is the real threat, and the README says so plainly rather than implying a security boundary that isn't there. See [ADR-0002](docs/adr/0002-sandbox-backends.md).

### D-3 · No API key is needed until M6
Every layer below the agents is testable with `MockProvider`. The content-addressed LLM cache doubles as a record/replay fixture store, so once a demo has been recorded it re-runs in CI for free and byte-identically. A key is required only for the first *live* agent run at the end of M6. See [ADR-0005](docs/adr/0005-llm-cache-and-replay.md).

### D-4 · Infrastructure before agents; ground truth before both
This reorders `docs/06-roadmap.md`. The agents are the *easy* part and the least certain; the ledger, the custodian and the question bank are what everything else depends on and what makes any agent result meaningful. Building the evaluation before the thing being evaluated is the whole thesis of this project applied to itself.

---

## 1. Milestones

Dependency order. Each milestone is a PR into `main` with CI green.

### M0 · Foundations ✅
Repository, tooling, CI, package skeleton, CLI entry point, licence, ADR log, docs moved in.

**Acceptance**
- `uv sync && uv run pytest` green from a clean clone on Linux + Windows CI.
- `nullius version` and `nullius --help` work.
- ruff + mypy (strict on `src/`) clean.

---

### M1 · Ledger and core model ✅
The append-only spine and every entity from `docs/03-data-model.md`.

- SQLAlchemy models: events, programs, hypotheses, registrations, runs, results, claims, evidence, objections, reviews, positions, decisions, forecasts, policies, costs, sources, datasets, artifacts.
- ~~Alembic migrations~~ — deferred to the end of M5, see [ADR-0006](docs/adr/0006-defer-alembic-to-m5.md).
- Hash-chained event ledger + `nullius ledger verify`.
- Content-addressed artifact store (`objects/<ab>/<sha256>`).
- Role-scoped repository layer — the only write path.
- Invariants as constraints/triggers: no run without a prior locked registration; holdout metrics only from the custodian; no claim without evidence; append-only enforcement.

**Acceptance**
- Property tests (Hypothesis) prove each invariant cannot be violated through the public API.
- Tamper test: mutating a historical event is detected by `ledger verify`.
- Round-trip test: state rebuilt by folding the event log equals the read models.

---

### M2 · Runtime and LLM layer ✅
- Role contracts (`RoleContract`, `AgentTask`, `AgentResult`) exactly as `docs/02-architecture.md` §2.
- Task queue + worker loop (`SKIP LOCKED` on Postgres; single-writer transaction on SQLite).
- `LLMProvider`: `AnthropicProvider`, `MockProvider`, `ReplayProvider`.
- Content-addressed response cache keyed on `(provider, model, params, prompt, tool_schemas)`.
- Cost ledger with a versioned price table; hierarchical budget enforcement at dispatch.
- Structured output via Pydantic with one repair retry, then hard fail.

**Acceptance**
- A trivial role executes end-to-end against `MockProvider`.
- Replaying a recorded run is byte-identical and costs $0.
- A task exceeding its parent budget is refused at dispatch, and the refusal is an event.

---

### M3 · Experiment DSL, compiler, sandbox ✅
- `ExperimentSpec` schema (the registered object).
- Design linter: single pre-declared primary metric, capacity-matched baselines, grouped splits, seed minimum, power for the stated MDE.
- Spec → executable plan compiler (scikit-learn, CPU).
- `SandboxBackend` + `SubprocessSandbox` + `DockerSandbox`.
- Artifact harvest, telemetry, environment manifest hashing.

**Acceptance**
- A hand-written spec compiles, runs sandboxed, emits hashed artifacts and telemetry.
- Isolation suite: attempts to open a socket, spawn a subprocess, or write outside the workdir are all denied and logged.
- Two runs with the same seed produce identical `environment_hash` and identical metrics.

---

### M4 · SCM generator and question bank ✅


- SCM DSL: causal / spurious / noise features, environments, shift configurations.
- Oracle: true population effects computed by large-sample evaluation, cached and versioned.
- Bank v1 — RQ-001's five configurations plus ten further items, **≥45 % true-zero**, ~20 % conditional, ~15 % carrying a planted defect.
- Bank isolation: ground truth lives where no agent view can join it; an isolation test proves it.

**Acceptance**
- `nullius bank verify` recomputes every truth value deterministically from the DGP.
- `nullius bank stats` reports null fraction, conditional fraction, planted-defect fraction.
- A leak test proves no role-scoped view exposes a `ground_truth` column.

---

### M5 · Custodian and analysis harness ✅
- Holdout Custodian as a separate process holding the test splits; preregistered evaluator; per-registration query budget.
- Statistics: seed variance, paired BCa bootstrap, effect sizes, Holm and Benjamini–Hochberg at program level.
- Verdict derivation and the computed confidence rubric.

**Acceptance**
- Known-answer fixtures for every statistic (values checked against independent references).
- No code path exists by which a non-custodian process produces a holdout metric — proven by test, not inspection.
- Query budget exhaustion is an event and blocks further holdout access.

---

### M6 · Research kernel — first end-to-end science ◐ mock-driven; live run pending an API key
Roles: Theorist, Designer, Analyst, and a rule-based Director. Plus the Registry and the Forecast Ledger.

**Acceptance**
- One bank item runs hypothesis → registration → forecasts → build → execute → custody → analysis → claim, with the registration hash provably predating the first run.
- Full provenance: every number in the output resolves to a `run_result` row.
- Recorded with `MockProvider`, then **the first live run** against Anthropic. Cost reported in USD.

---

### M7 · Adversarial layer ✅
Skeptic + detector suite, typed objections with mandatory discriminating tests, Replicator with enforced blindness, Reviewer, defect injector.

**Acceptance**
- Skeptic recall/precision on injected defects measured and reported; recall > 0.5 to pass.
- Replicator blindness proven by audit log: it never read a row from the original run.
- At least one claim is blocked from promotion by a critical objection, and unblocked only by a discriminating experiment.

---

### M8 · Institutional memory ✅
Genealogy CTEs, follow-up generation from terminal states, institutional-novelty dedup, cross-item memory.

**Acceptance** — second-generation hypotheses demonstrably derive from first-generation results; a duplicate hypothesis is caught at intake.

---

### M9 · Research economy ✅
Forecast-derived EIG, the `AllocationPolicy` interface with random / round-robin / greedy-EIG / Thompson implementations, hierarchical budgets, reserves for replication and null confirmation.

**Acceptance** — greedy-EIG measurably beats random on cost-per-correct-claim over the bank, **or is shown not to**. Either result ships.

**The result, and it is the second kind.** Measured over the twenty-item bank at a $0.03 budget, 400 paired bootstrap resamples over items ([ADR-0007](docs/adr/0007-allocation-measured-on-fixed-outcomes.md) for why the comparison holds the science fixed and varies only selection):

| policy | funded | correct | $/correct |
|---|---|---|---|
| `random/v1` | 4 | 3 | $0.0086 |
| `round-robin/v1` | 4 | 4 | $0.0064 |
| `cheapest-first/v1` | 4 | 4 | $0.0064 |
| `greedy-eig/v1` | 4 | 4 | $0.0064 |
| `thompson/v1` | 4 | 4 | $0.0064 |

Greedy-EIG beats random: **+12.51 correct claims per dollar, 95% CI [+0.01, +38.96]** — an interval that excludes zero, but only just.

**And the gain has nothing to do with expected information gain.** The cost-only control produces a byte-identical figure, and the spread of EIG across items is exactly 0.000000000 nats. Under `MockProvider` every role emits the same forecast for every item, so the information term is constant and greedy-EIG *is* the cost control. Without `cheapest-first/v1` on the table this would have read as a win for the mechanism. It is a win for the denominator.

`nullius economy sweep` then dials forecast informativeness from nothing to oracle-grade. As the forecasts acquire per-item signal, greedy-EIG's advantage over random **shrinks** (+12.1 → +9.2) and its difference from the cost control turns **negative** (0.00 → −2.97, interval spanning zero). It never beats the cost control at any forecast quality. That is the direction the theory predicts and the module docstring stated in advance: EIG measures expected surprise, and the items with most surprise in them are the ones near a verdict boundary — exactly where the institution gets the answer wrong. Maximising information and maximising correct claims per dollar are different objectives on this bank, and ranking by the first costs you the second.

What this does not settle: the forecasts here carry no information, so the sweep's oracle-informed rungs bound what a *perfect* forecaster could buy rather than measuring what a live one does. Re-run against a real provider when M6's live run lands.

**Three silent bugs surfaced on the way**, all of which reported as science rather than as faults, and all now pinned by tests:
- `SubprocessSandbox` passed a relative workdir to a child launched *inside* that workdir, so the path resolved twice and every seed returned `scientific_failure`. The first two bank measurements reported 0/20 correct because of it. An execution fault wearing the costume of a research result is the one disguise this project cannot allow.
- The write guard compared a resolved allowed-root against an unresolved target, so a workdir reached through a Windows 8.3 short name denied every write an experiment made to its own output directory.
- `.gitignore` used trailing `#` comments, which git does not support — `objects/`, `runs/` and the rest matched nothing and were never ignored.

---

### M10 · Benchmark harness ⬅ next
Arms B0–B7 from `docs/04-evaluation.md`, matched on model, compute, seeds and data access. Preregistered protocol committed with a hash *before* results are collected.

**Acceptance** — the full ladder runs; verdict accuracy, null accuracy, calibration, FDR, and cost-per-correct-claim reported per arm with bootstrap CIs.

---

### M11 · Observability
Static HTML report generator, then the FastAPI + HTMX dashboard: overview, hypothesis explorer, run monitor, genealogy graph, agent timeline, claim view.

**Acceptance** — a person answers "why does the system believe C-014?" in three clicks.

---

### M12 · Beyond the MVP
Code generation (restricted op registry → constrained → free-form, measured against the compiler baseline, Docker required here), vendored literature corpus with a provenance verifier, versioned self-improving policies, template-rendered papers, and finally multiple labs.

---

## 2. Working agreement

- **`main` is always green.** Every milestone lands as a PR with CI passing on Linux and Windows.
- **Invariants get property tests, not unit tests.** If a rule in `docs/03` can be stated as "no sequence of API calls can produce X", it is tested that way.
- **No milestone is "done" because the code exists.** It is done when its acceptance criteria are automated and green.
- **A test that cannot fail is worse than no test.** M1 shipped two tests that passed without exercising anything: one bound a UUID in a form the database does not store (matching zero rows, so the trigger never fired), another attempted `UPDATE` on an empty table (where a row-level trigger never fires). Both now assert their own preconditions. Any test asserting that an operation is *refused* must first prove the operation would otherwise have done something.
- **Deviations from `docs/` are ADRs**, not silent drift. The design documents are not edited to match the code; the ADR records why they diverged.
- **The project's own claims follow its own rules.** The benchmark protocol is preregistered with a hash before the ladder is run, and negative results are reported.

## 3. Open items requiring a human

1. **API key** — `ANTHROPIC_API_KEY` needed for the first live run at the end of M6. Everything before that is mock-driven.
2. **Docker** — needed only for M12 code generation, or earlier if you want the strong sandbox. Requires WSL2 + Docker Desktop and a reboot.
3. **The DGPs in M4.** The structural causal models are the ground truth this project is scored against. They are designed by a person and implemented by the machine — never the reverse.
4. **Benchmark preregistration** — a human signs off before the B0–B7 ladder runs.
