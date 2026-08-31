# Nullius — Build Plan

> *Nullius in verba* — take nobody's word for it.

This is the executable plan derived from [`docs/`](docs/). The design documents say *what* to build and *why*; this says *in what order*, *with what acceptance test*, and *what changes because of the machine we're actually on*.

**Status:** M0–M10 complete (M6–M10 mock-driven; the first live run awaits an API key). M11 next. Nothing below is claimed as done until its acceptance criteria are green in CI.

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

| policy | funded | correct | $/correct | nats/$ |
|---|---|---|---|---|
| `random/v1` | 4 | 4 | $0.0065 | 780.3 |
| `round-robin/v1` | 4 | 4 | $0.0065 | 780.2 |
| `cheapest-first/v1` | 4 | 4 | $0.0065 | 780.4 |
| `greedy-eig/v1` | 4 | 4 | $0.0065 | 780.4 |
| `thompson/v1` | 4 | 4 | $0.0065 | 780.3 |

Greedy-EIG against random on correct claims per dollar: **+2.54, 95% CI [−38.59, +38.62] — no measurable difference.** The spread of expected information gain across items is **0.000000000 nats**, and greedy-EIG's figures are identical to the cost-only control's to every digit shown.

**Why the information term could not have helped.** Under `MockProvider` every role emits the same forecast for every item, so EIG is constant across the bank and `greedy-eig/v1` *is* `cheapest-first/v1`. Reporting "EIG does not help" from that setting alone would be a claim about the forecasts dressed up as a claim about the policy — which is what `nullius economy sweep` exists to separate. Dialling forecast informativeness from nothing to oracle-grade, greedy-EIG **never separates from random at any forecast quality**, and never beats the cost-only control. Its point estimate against the control rises with forecast quality but the interval spans zero throughout.

**An earlier version of this section reported the opposite, and was wrong.** It claimed greedy-EIG beat random at +12.51, CI [+0.01, +38.96]. That interval's lower bound was one hundredth of a unit above zero, and it did not survive re-measurement once the seed bug below was fixed. A knife-edge separation reported as a win is exactly the failure this project is built to catch, and it caught it here on its own machinery rather than in review.

**The honest limitation.** Every bank item costs within 0.1% of every other, so all five policies fund exactly four items and there is almost nothing for an allocator to differentiate on. The bootstrap intervals are correspondingly enormous — roughly ±38 on a quantity whose point estimates are single digits. This design can detect a large allocation effect and would miss a moderate one. Cost heterogeneity across the bank is what M10 needs in order to make this comparison sharp; the honest statement today is *no detectable effect at this power*, not *no effect*.

**A reproducibility bug the measurement found in the kernel.** Seed roots were derived from `abs(hash(item.item_id))`. Python randomises string hashing per process, so a *preregistered* seed root differed on every run — changing `spec_hash`, changing which seeds were drawn, and flipping the verdict of any item near a decision boundary. M3's acceptance claim, that two runs with the same seed give identical metrics, held within a process and failed across them, which is the case "reproducible from a clean clone" actually depends on. Now derived from SHA-256 via `seed_for()`, pinned by a test that compares subprocesses started with different `PYTHONHASHSEED` values, plus a control proving the old derivation really was unstable.

**What re-running the measurement does and does not reproduce.** Experiment seeds are now fixed by the preregistration. The *evaluation* sample is not: the Custodian derives it from the registration id, so a fresh measurement draws a fresh holdout. That is deliberate — a custody seed derived from the design would let anyone re-register the same spec and shop a fixed evaluation set. Two consecutive full measurements were compared: all twenty verdicts agreed, while every realised effect differed. `bank/outcomes.lock.json` records one honest draw, not a canonical one.

**The economy now governs the institution, rather than sitting beside it.** The first cut of M9 shipped an allocator that nothing in the research lifecycle called. `ResearchKernel` is now split at the seam the economy needs — `propose()` stops at a locked registration and locked forecasts, which is where expected information gain first becomes computable and just before anything expensive happens, and `execute()` continues from there. `FundingRound` puts several questions up, allocates the laboratory's budget across them, and runs only the winners; the rest keep their registrations and forecasts, reach `ABANDONED_BUDGET`, and leave a decision row naming what beat them. `nullius economy round` drives it.

That work found the container was wrong. Allocation across bank items had been written inside a single programme, and M8's institutional-novelty guard correctly refused every proposal after the first — a `Program` is *one research question*, which is what the guard enforces. So a round now opens one programme per question and allocates one tier up, at the laboratory, which is the `institution → program` boundary the budget hierarchy already had and nothing had yet used.

**Three silent bugs surfaced on the way**, all of which reported as science rather than as faults, and all now pinned by tests:
- `SubprocessSandbox` passed a relative workdir to a child launched *inside* that workdir, so the path resolved twice and every seed returned `scientific_failure`. The first two bank measurements reported 0/20 correct because of it. An execution fault wearing the costume of a research result is the one disguise this project cannot allow.
- The write guard compared a resolved allowed-root against an unresolved target, so a workdir reached through a Windows 8.3 short name denied every write an experiment made to its own output directory.
- `.gitignore` used trailing `#` comments, which git does not support — `objects/`, `runs/` and the rest matched nothing and were never ignored.

---

### M10 · Benchmark harness ✅ (mock-driven; underpowered, and says so)
Arms B0–B7 from `docs/04-evaluation.md`, matched on model, compute, seeds and data access. Preregistered protocol committed with a hash *before* results are collected.

**Acceptance** — the full ladder runs; verdict accuracy, null accuracy, calibration, FDR, and cost-per-correct-claim reported per arm with bootstrap CIs.

**The protocol went in first, in its own commit.** `benchmark/protocol.lock.json` (`1d4c76d2…`) fixes the arms, the metrics, the bootstrap resample count, alpha, the multiplicity correction, the baseline arm, the map from computed confidence to probability, and the exclusion rules — and it was committed before a single line of the runner existed. The git history is therefore the evidence that the plan predates the results, rather than a docstring claiming it does. `nullius benchmark run` refuses to start if `verify_protocol` fails, so a run against a bank that moved after registration cannot be reported as preregistered.

**One pipeline, not eight.** The arms are switches (`Mechanisms`) that `ResearchKernel` reads — custody, preregistration, adversary, replication, memory. Two institutional arms run the *same* code with different flags. A second implementation for B3 to drift away in is the failure mode this avoids, and the ablation's validity rests entirely on it. Two tests exist purely to prove the switches are connected rather than merely recorded: the same design, same seeds, run with and without the Custodian, must produce *different* measured effects — because the custodied pass is reading a sample the other never saw — and it must consume no holdout budget when custody is off.

**Three code paths, because the ladder declares three.** B0 answers `no_effect` without looking and costs nothing. B1 and B2 ask a model directly with no ledger, registration or execution. B3 upward is the kernel. Giving B1 the institution's machinery would have made it a different arm.

**What the mock decides, stated rather than buried.** Under `MockProvider` there is no such thing as "what the model would say" — only what the runner tells it to say. `DIRECT_MOCK_VERDICT` is `supported`: the documented failure mode of an unstructured agent asked whether an intervention helps. That makes B1 the mirror of B0, and it means B1 and B2's numbers are a property of that constant, not a measurement. B2 receives the identical answer on its second pass, because a mock that improved on revision would be the runner deciding that iteration helps — which is the question B2 was added to ask. Both arms carry `model_dependent`, and no mechanism claim rests on them.

**A defect this milestone found in M8's memory.** `recall()` was programme-scoped. A `Program` is *one* research question, so memory could never cross from one bank item to the next — which would have made B6 and B7 identical by construction and produced an ablation capable only of reporting no difference. Memory is now recalled at **lab** scope, which is the `institution → program` boundary the budget hierarchy already had. Programme scope remains the default, because it is the right answer for a single programme reasoning about itself.

**A second, still open.** The novelty fingerprint covers metric, direction, effect size and statement tokens, but not the *dataset*. The bank is twenty variants of one question across twenty data-generating processes, so "the same question about different data" is indistinguishable from "the same question again". M9 hit the same boundary and resolved it the same way — one programme per question — and that is what the runner does. The underlying gap in `fingerprint()` is recorded here rather than patched, because widening it is a change to what novelty *means* and belongs with a test that says so.

**Why every arm gets its own database.** The ledger refuses to register the same design twice: re-registering would hide that an experiment was run twice. That is correct, and it is also why two arms of an ablation cannot share a store. Two arms are not two runs of one experiment; they are one experiment run by two different institutions.

**Who registers a replication.** The Replicator may not `register` — that authority is the Designer's, and widening it would have traded a real separation for a cosmetic one. A replication is preregistered by the Designer with a *fresh* seed root and executed by the Replicator, whose reads are filtered to its own runs. Re-executing the identical seeds would test that the code is deterministic, which is already known and is not what replication means.

**The ladder ran. `benchmark/results.lock.json`, protocol `1d4c76d2…`, mock provider.**

| arm | | acc | null | brier | ece | fdr | $/correct |
|---|---|---|---|---|---|---|---|
| B0 | oracle-null | 0.45 | 1.00 | 0.250 | 0.050 | 0.00 | 0.00000 |
| B1 * | single-shot | 0.20 | 0.00 | 0.200 | 0.200 | 0.45 | 0.00269 |
| B2 * | + loop | 0.20 | 0.00 | 0.200 | 0.200 | 0.45 | 0.00761 |
| B3 | multi-role | 0.90 | 0.89 | 0.283 | 0.417 | 0.00 | 0.00722 |
| B4 | + prereg + custodian | **0.95** | 0.89 | 0.242 | 0.420 | 0.00 | 0.00683 |
| B5 | + Skeptic | 0.95 | 0.89 | 0.302 | 0.467 | 0.00 | 0.00683 |
| B6 | full institution | 0.90 | 0.89 | 0.316 | 0.415 | 0.00 | 0.00732 |
| B7 | full − memory | 0.95 | 1.00 | 0.311 | 0.472 | 0.00 | 0.00693 |

`*` model-dependent; this run used a mock provider, so B1 and B2 describe the mock.

**The one result that survives its own interval.** B4 − B0 = **+0.50, CI [+0.25, +0.75], p = 0.002**. The institution decisively beats a free constant that answers `no_effect` without looking. Nothing else on the ladder separates.

**The registered prediction is upheld, and the margin is one item.** Mechanism (B4 − B3) = +0.05, CI **[+0.000, +0.150]**, p = 0.73. Everything else (B6 − B4) = −0.05, CI **[−0.150, +0.000]**, p = 0.70. Both span zero. The primary metric moves in steps of 1/20 = 0.05, so the verdict rests on a single item in each direction. **Reported as upheld because that is what the registered rule returns, and reported as uninformative because that is what the arithmetic says.** The rule was committed before the numbers and has not been touched since; what changed is that its intervals are now printed beside it.

**Three flaws in the preregistered protocol, found only by running it.** None is patched, because the protocol is hashed and editing it is the one move preregistration exists to prevent. Each belongs to a v2 registered as a change.

1. **The adjudication rule is too weak.** `mechanism > agents` compares two point estimates and requires neither to separate from zero. On twenty items it can return "upheld" for noise, and on this run it did. A v2 should require the mechanism contrast to exclude zero.
2. **The registered baseline is model-dependent.** The protocol names B1 as the baseline arm, and B1 is `model_dependent`. So *every* comparison in the registered family inherits the flag, and under a mock provider the entire baseline table is uninterpretable for mechanism — which is why the honest contrast above is against B0, not B1. Fixing this means a different baseline, which means a different protocol.
3. **The confidence-to-probability map grades the wrong thing.** `CONFIDENCE_AS_PROBABILITY` translates a rubric that measures *evidence for an effect* into a probability read as *the answer is correct*. B4 was right on 19 of 20 items while stating 0.40–0.75, because a correct `no_effect` answer has weak evidence for an effect by construction. The institutional arms therefore score ECE ≈ 0.42 — **systematic underconfidence, and an artefact of the mapping rather than a property of the institution.** The Brier and ECE columns above measure the translation as much as the calibration.

**Mechanisms that contributed nothing measurable.** B5 − B4 = exactly 0.000: the Skeptic's detectors raised no finding that changed any verdict. B6 − B7 = −0.05, CI [−0.150, +0.000]: memory did not help and if anything cost an item. Neither is evidence of absence at this power — twenty items cannot resolve below one item — but neither may be reported as a benefit.

**Where the accuracy actually comes from.** B3, with role decomposition alone and no preregistration and no Custodian, already reaches 0.90. Everything the institution adds above that moves between zero and one item. On this bank, at this power, **the decomposition is doing the work and the institutional machinery is not yet earning its cost** — B4 is cheaper per correct claim than B3 ($0.00683 vs $0.00722), but only because it got one more item right.

**The honest summary.** The ladder demonstrates that the harness works, that the arms differ in the mechanism named and nothing else, and that the whole thing beats a do-nothing floor. It does **not** demonstrate that structure beats agents, because the bank is too small and the provider is a mock. Sharpening it needs a harder bank with items near the verdict boundary, more items, and a live model — which is what M10's results make the case for, rather than something the milestone can assert.

---

### M11 · Observability ⬅ next
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
