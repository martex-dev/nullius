# Nullius — Build Plan

> *Nullius in verba* — take nobody's word for it.

This is the executable plan derived from [`docs/`](docs/). The design documents say *what* to build and *why*; this says *in what order*, *with what acceptance test*, and *what changes because of the machine we're actually on*.

**Status:** M0–M35 complete; v5 and v6 landed, v7 registered and not yet run. **V6's result does not adjudicate what v6 registered** — its treatment arm did not implement the mechanism it names; see M23. The live path is wired but unspent (mock-driven throughout; the first live run awaits an API key). M12's code-generation half is blocked on both a key and Docker. Nothing below is claimed as done until its acceptance criteria are green in CI.

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

### M11 · Observability ✅ (static report; the dashboard stays a Stage 6 item)
Static HTML report generator, then the FastAPI + HTMX dashboard: overview, hypothesis explorer, run monitor, genealogy graph, agent timeline, claim view.

**Acceptance** — a person answers "why does the system believe C-014?" in three clicks. **Met in one.** The index lists every claim; one click opens its dossier, which carries what raised the confidence, what capped it, the ledger facts underneath, the question it answers, the design locked before it ran, the seeds, the numbers the verdict came from, every objection with its discriminating test, and how the forecasts that were locked beforehand actually scored.

**Static files, not a server** — the shape `docs/01-critique.md` §24 argued for. A dashboard is a read model over an event log; serving it needs a process running at the moment someone asks, while a directory of HTML can be opened from a clone, attached to an issue, committed beside the results it describes, and diffed against the last one. For a project whose whole argument is that its record survives inspection, the artifact that travels beats the one that must be hosted. `nullius report build` exits **non-zero** on a broken chain, a ledger that does not reconcile, or a claim carrying a confidence the ledger no longer supports.

**The report re-derives rather than displays.** Confidence is not read out of the `claims` row and printed; it is recomputed from the same ledger facts `compute_confidence` consumes and compared against the stored value. Disagreements are listed first on the front page. That is only possible because M5 built every input to be a checkable fact rather than an opinion — and the first thing it did when pointed at a real ledger was find three places where that had quietly stopped being true.

**Three bugs, found by re-deriving instead of displaying.** All pinned by tests.

1. **The computed confidence never reached the ledger.** `create_claim` writes `speculative`; the kernel computed the real level, returned it in `KernelOutcome`, and never wrote it back. Every `claims` row in every ledger this project has ever produced said `speculative` — including claims that had been independently replicated and were reported as `well_supported`. It stayed invisible because the benchmark read the in-memory value, which is exactly why something had to read from the *store* instead. The kernel now promotes through `promote_claim`, so the ledger's own rules — evidence exists, no open critical objection, an independent reproduction before the top level — get their say rather than a column being assigned.
2. **The Custodian named artifacts it never stored.** Every holdout `RunResult` carried a content address that resolved to no object; the `dev` results written by the harness all resolved. So the evaluation numbers — the ones the verdict is actually computed from — had no artifact behind them. An address for an artifact that was never written is worse than no address, because it reads as provenance. The Custodian now writes the measurement payload and verifies the store addresses it identically.
3. **`provenance_complete` was asserted, not checked.** The kernel passed the literal `True`. The confidence rubric's entire design is that no input can be declared, and one of them was being declared by the system on its own behalf every time — which meant this cap had never once fired in the project's life. Now computed from the store.

**M10's results are unaffected.** The benchmark read `KernelOutcome.confidence`, the correctly computed in-memory value, so `benchmark/results.lock.json` stands as measured. What changed is that the ledger now agrees with it.

**Two new CI jobs.** `protocol` re-scores the committed results from their own per-item rows, so a results file whose headline numbers cannot be recomputed from the outcomes it ships with cannot be committed. `report` carries three bank items through the full institution, renders them, and fails if the generator's own integrity check trips — the report has to survive its own check on output nobody curated, and the site is uploaded as an artifact.

---

### M12 · Beyond the MVP
Code generation (restricted op registry → constrained → free-form, measured against the compiler baseline, Docker required here), vendored literature corpus with a provenance verifier, versioned self-improving policies, template-rendered papers, and finally multiple labs.

**Blocked, and stated rather than quietly skipped.** Code generation needs a live model *and* Docker, and this machine has neither (`docs/adr/0002`, and the M0 environment table). It stays unbuilt until both exist. Nothing else in this bucket was started, because building more mechanisms on an instrument that cannot measure mechanisms is the mistake M10 was warning about.

---

### M12a · A bank that can measure ✅
M10's ladder separated no two institutional arms. Before adding anything, fix the instrument — which is entirely offline work.

**Acceptance** — a second registered bank on which the primary metric can resolve a difference smaller than one item, with ground truth that is not in doubt; and a second protocol repairing the three flaws running the first one exposed. Neither replaces the first.

**The diagnosis in M10 was wrong, and measuring it properly says so.** M10 concluded "the bank is too easy". It is not: thirteen of v1's twenty items already sat within two experiment standard errors of a verdict boundary, and the single item B4 got wrong (B15) was the third hardest in the bank. The actual limits were different, and both are arithmetic rather than judgement — twenty items make the primary metric move in steps of 0.05, so **no difference smaller than one item can be seen at all**, and only six items sat inside one standard error, which is the band where two arms can plausibly disagree. That correction is recorded here because the wrong version was committed in M10's own write-up.

**Bank v2: sixty items, thirty of them inside one experiment standard error.**

| | v1 | v2 |
|---|---|---|
| items | 20 | **60** |
| metric resolution | 0.050 | **0.017** |
| within 1 measured experiment SE | 3 | **20** |
| within 2 measured experiment SEs | 6 | **37** |
| true nulls | 45% | **45%** |
| minimum oracle margin | ≥3 SE | **3.4 SE** |

*Corrected after the fact.* The bank was designed against an assumed experiment standard error of 0.005; the ledger says the real one is **0.00348** (median over B6's 360 claims). Every item is therefore *harder* in relative terms than intended, and the counts above are the measured ones rather than the design-time estimates.

The headroom that makes a hard bank a *fair* one is the gap between the two measurements. The oracle sees 40 seeds of 20,000 samples and resolves an effect to about 0.0008; an experiment gets 5 seeds of 2,000 and resolves it to about 0.005. Every v2 item is at least three *oracle* standard errors from its boundary — its ground truth is not in doubt — while half of them sit inside one *experiment* standard error of it. Unambiguous to the oracle, a coin flip for the institution.

Every parameter was found by measuring a 311-point sweep of the generator and selecting on the result, never by picking a number that looked right. The causal branch turns out to be non-monotone below `shift_strength≈0.5`, so selection is restricted to the monotone branch above it. Truth derivation for all sixty items takes 69 seconds.

**Protocol v2 repairs the three flaws, and repairs none of them in place.** v1 stays on disk, still verifying, still wrong in the three ways it was wrong — editing a hashed preregistration to fix its own findings is the exact substitution the file exists to prevent.

1. **Baseline arm B1 → B0.** B1 is `model_dependent`, so under a mock every comparison in v1's registered family inherited the flag and the whole table was uninterpretable for mechanism. B0 answers without looking and is not a model.
2. **Adjudication on an interval, not two point estimates.** v1's rule returned "upheld" for a one-item difference. v2 requires the B4−B3 interval to exclude zero, and fails the prediction if it does not, whatever the point estimate says.
3. **Calibration scored only where the rubric's quantity is the scored quantity.** The confidence rubric measures evidence *for an effect*, so a correct `no_effect` answer necessarily carries weak evidence and scored as gross underconfidence — ECE ≈ 0.42 was an artefact of the mapping, not a property of the institution. v2 registers `asserted_effects`.

**A fourth flaw, found while fixing the other three.** Adding those two keys to the builder changed what `build_protocol(version="1")` produced, while every existing check stayed green: bank unchanged, arms unchanged, stored hash still matching its own content. A registered protocol the code can no longer reproduce has been edited in effect. `ProtocolVerification` now carries `rebuilds_identically`, v1's payload gained no keys, and v1 rebuilds to `1d4c76d2…` exactly as M10 registered it.

`nullius benchmark run --bank 2` runs the ladder on v2. `--bank 1` still reproduces M10.

---

### M12b · The v2 ladder ✅ — and the registered prediction is refuted
`benchmark/results.v2.lock.json`, protocol `254be687…`, 60 items, mock provider.

| arm | | acc | null | brier | ece | fdr | $/correct |
|---|---|---|---|---|---|---|---|
| B0 | oracle-null | 0.45 | 1.00 | — | — | 0.00 | 0.00000 |
| B1 * | single-shot | 0.18 | 0.00 | 0.197 | 0.217 | 0.45 | 0.00293 |
| B2 * | + loop | 0.18 | 0.00 | 0.197 | 0.217 | 0.45 | 0.00830 |
| B3 | multi-role | 0.60 | 0.33 | 0.203 | 0.450 | 0.00 | 0.01081 |
| B4 | + prereg + custodian | 0.67 | 0.37 | 0.164 | 0.394 | 0.00 | 0.00973 |
| B5 | + Skeptic | 0.62 | 0.41 | 0.151 | 0.309 | 0.00 | 0.01052 |
| B6 | full institution | **0.72** | 0.48 | **0.110** | **0.281** | 0.00 | **0.00919** |
| B7 | full − memory | 0.70 | 0.48 | 0.101 | 0.265 | 0.00 | 0.00940 |

**The prediction is refuted, by the rule registered before the run.** B4 − B3 = +0.067, 95% CI **[−0.033, +0.167]** — the interval spans zero, so the prediction fails regardless of the point estimate. v1's weaker rule would have called the same data "upheld". The project was wrong in public about its own registered prediction, which is the outcome preregistration exists to make possible.

**Only the full institution beats the do-nothing baseline.** After Benjamini–Hochberg across the family of seven, four comparisons survive: B1−B0 and B2−B0 (both *negative*, and model-dependent), and **B6−B0 = +0.267 [+0.050, +0.450]** and **B7−B0 = +0.250 [+0.050, +0.467]**. B4−B0 = +0.217 with a lower bound of exactly 0.000 and does **not** survive. So the cheap-mechanism arm is not what separates — the expensive one is. That is the opposite of what was registered.

**Calibration improves monotonically down the ladder, and this is the clean signal.** Brier 0.203 → 0.164 → 0.151 → 0.110 → 0.101 and ECE 0.450 → 0.394 → 0.309 → 0.281 → 0.265 across B3→B7. Every added mechanism improves it, without exception. v1 could not see this at all, because v1's calibration metric was measuring the confidence mapping rather than the institution — the flaw protocol v2 was registered to fix.

**Never once a false discovery.** FDR is 0.00 for every institutional arm. Of B3's 24 wrong answers all 24 are `inconclusive`, and of B6's 17, all 17.

> **Corrected in M13.** This section originally said *"every error the institution makes is an abstention"*. That was wrong, and the error was mine rather than the system's. Splitting those `inconclusive` answers by the branch of `derive_verdict` that produced them shows most are not abstentions at all: **16 of B3's 24 and 11 of B6's 17 are substantive findings** — the interval ruled out the claimed effect but not a smaller one, so the institution asserted a real sub-MDE effect where the truth was a null. Only 8 and 6 respectively were genuine "the interval is too wide to say anything". A wrong finding and a declined question are different failures, and I reported the first as the second.

**A flaw in the primary metric, now visible because the bank is hard.** `verdict_accuracy` scores a calibrated "I cannot tell" exactly as harshly as a confident error — the distinction an institution exists to make, and the headline metric was blind to it. Null accuracy falling from 0.89 (v1) to 0.33–0.48 (v2) is largely this effect rather than a decline in judgement.

> **And worse than that, also found in M13.** Because `inconclusive` is a *real truth value* in this bank, the conflation did not only penalise abstention — it **rewarded** it. An arm whose interval was too wide to say anything was scored *correct* whenever the truth happened to be `inconclusive`. Every arm above is inflated by it, unevenly: B3 by 7 items, B4 by 9, B5 by 8, B6 by 4, B7 by 9, out of sixty. Deflated, the accuracies are B3 0.483, B4 0.517, B5 0.483, B6 0.650, B7 0.550 — and the B6/B7 gap widens from +0.017 to +0.100. **The ordering this section rests on is not safe.** M13 splits the verdict and re-runs the ladder; the numbers above stand as what protocol v2 measured, not as what is true.

**And the abstentions are correct, which is a power finding about the design.** The measured experiment standard error is 0.00348 at the policy's `min_seeds: 5`. The hardest null sits 0.0030 from the null-band edge. Separating them at three standard errors needs about **61 seeds**. The institution is being asked questions its own declared design cannot answer, and it says so instead of guessing — so the right target for M13 is the seed policy, not the agents.

**Memory still contributes nothing measurable.** B6 − B7 = +0.017, CI [−0.050, +0.083]. Consistent with v1, on a bank three times the size.

---

### M13 · Abstention is not an answer ✅
The metric flaw M12b exposed, fixed at the representation rather than with a new scoring rule.

**Acceptance** — an abstention can never be scored as a correct answer; coverage and accuracy-when-answering are reported beside the headline; the ladder re-runs under a protocol that registers all three.

**One enum value was doing two jobs.** `derive_verdict` returns `inconclusive` both for *"the interval rules out the claimed effect but not a smaller one — something is there, less than was claimed"* and for *"the interval is too wide to separate anything; this is a statement about the design, not the world"*. Its own reason strings say exactly that. The benchmark scored the enum, so the distinction never left the function.

**The cost was not what I first thought.** M12b reported that the institution's errors were all abstentions. They were not: most were substantive findings that were wrong. And because `inconclusive` is a real truth value here, the conflation ran in the flattering direction — an arm that could say nothing was credited with a correct answer whenever the truth happened to be `inconclusive`. That inflated every v2 arm, unevenly, and by enough to move the ordering.

**The fix is representational.** `Verdict.UNDERPOWERED` is a separate member, and it is never a truth: the oracle measures at forty seeds of twenty thousand samples and is never short of power, so `classify` cannot produce it and an abstention cannot be scored correct by accident. This was preferred over a scoring rule with an abstention weight, because that weight would have been a free parameter chosen after seeing which value flattered the institution.

**`VerdictReport.underpowered` already existed** and already computed this — by searching its own reason string for "too wide". It is an identity check now.

**Protocol v3** registers the vocabulary and adds `coverage` and `assertion_accuracy` beside the headline. Neither is primary: an arm reaches assertion accuracy 1.0 by answering only what it is sure of, and coverage is what stops that reading as success. `verdict_accuracy` still counts an abstention as incorrect — v1's first exclusion rule refuses to pay an arm for declining the questions it found hardest, and separating abstention from error is not forgiving it.

Per-version metric tuples were needed to do this without vandalism: adding two entries to the shared `METRICS` constant would have changed the hash of two protocols that are supposed to be immutable. v1 and v2 both still verify and still rebuild identically.

---

### M13b · The v3 ladder ✅ — and M12b's headline does not survive
`benchmark/results.v3.lock.json`, protocol `9eb8e1e1…`, 60 items, mock provider.

| arm | | acc | coverage | when answered | null | brier | ece | fdr |
|---|---|---|---|---|---|---|---|---|
| B0 | oracle-null | 0.45 | 1.00 | 0.45 | 1.00 | — | — | 0.00 |
| B1 * | single-shot | 0.18 | 1.00 | 0.18 | 0.00 | 0.197 | 0.217 | 0.45 |
| B2 * | + loop | 0.18 | 1.00 | 0.18 | 0.00 | 0.197 | 0.217 | 0.45 |
| B3 | multi-role | 0.48 | 0.75 | 0.64 | 0.33 | 0.203 | 0.450 | 0.00 |
| B4 | + prereg + custodian | 0.52 | 0.77 | 0.67 | 0.41 | 0.142 | 0.297 | 0.00 |
| B5 | + Skeptic | 0.55 | 0.75 | 0.73 | 0.44 | 0.132 | 0.350 | 0.00 |
| B6 | full institution | 0.57 | 0.78 | 0.72 | 0.41 | **0.117** | 0.294 | 0.00 |
| B7 | full − memory | 0.57 | 0.73 | **0.77** | 0.48 | 0.118 | **0.292** | 0.00 |

**Every institutional arm lost accuracy, and the non-abstaining arms lost none.** B3 −0.117, B4 −0.150, B5 −0.067, B6 −0.150, B7 −0.133; B0, B1 and B2 unchanged to three decimals. That is the control: those three never abstain, so the vocabulary change cannot touch them. The drop is the correction, not run-to-run variation.

**M12b's headline is retracted.** It said *only the full institution beats the do-nothing baseline*, on B6−B0 = +0.267 [+0.050, +0.450]. Scored correctly, **B6 − B0 = +0.117, CI [−0.083, +0.317]** — it spans zero. After Benjamini–Hochberg only two of seven contrasts survive, and both are the *negative* model-dependent ones. **At this power, nothing in the institution beats answering `no_effect` about everything.**

**The one contrast that separates decisively is coverage, in the direction that costs the institution.** B6 − B0 = **−0.217, CI [−0.317, −0.117]**. The institution answers about a fifth less of the bank than the constant does. It is right more often when it does answer (B6 0.72 against B0 0.45), but that gap does not separate either: **B6 − B0 when answered = +0.213, CI [−0.043, +0.468]** over the 47 items both were willing to call.

So the institution trades coverage for accuracy, and at sixty items neither side of that trade is resolvable. Only the trade itself is visible.

**Calibration remains the one robust signal, across all three protocols.** Brier 0.203 → 0.142 → 0.132 → 0.117 → 0.118 down B3→B7. It survived the bank getting harder and the metric getting fixed, which is more than the accuracy ordering did.

**The registered prediction is refuted, and the way it was adjudicated is a flaw I introduced.** The prediction had two clauses. The first — *separating abstention from finding lowers every arm's verdict accuracy* — is **true**. The second — *the institutional arms separate on coverage, B6 answering more of the bank than B3, interval excluding zero* — is **false**: B6 − B3 coverage = +0.033, CI [−0.050, +0.117].

But the run reported "refuted" without testing either clause. Protocol v3 inherited v2's `adjudication: interval_excludes_zero`, which computes B4 − B3 on **accuracy**, while v3's prediction text is about **coverage**. The verdict is right and was reached by measuring something the prediction does not mention. **A registered prediction and a registered adjudication rule that measure different quantities is a fifth protocol flaw, and this one is purely mine** — I wrote new prediction text and left the rule key at its inherited value. A v4 must derive the adjudicated quantity from the prediction rather than storing them independently.

The prediction did at least say in advance how to read its own failure: *"If coverage does not separate, the institution's advantage is in what it says and not in how much it is able to say."* On these numbers that is the reading — the advantage is in calibration and in declining to answer, not in answering more.

---

### M14 · Adaptive seeding ✅ — the first registered prediction this project has got right
`benchmark/results.v4.lock.json`, protocol `b46bdef3…`, 9 arms, 60 items, mock provider.

| arm | | acc | coverage | when answered | null | brier | ece | $/correct |
|---|---|---|---|---|---|---|---|---|
| B0 | oracle-null | 0.45 | 1.00 | 0.45 | 1.00 | — | — | 0.00000 |
| B3 | multi-role | 0.48 | 0.75 | 0.64 | 0.33 | 0.203 | 0.450 | 0.01341 |
| B4 | + prereg + custodian | 0.62 | 0.80 | 0.77 | 0.52 | 0.155 | 0.314 | 0.01051 |
| B5 | + Skeptic | 0.62 | 0.83 | 0.74 | 0.48 | 0.150 | 0.311 | 0.01051 |
| B6 | full institution | 0.55 | 0.75 | 0.73 | 0.44 | 0.101 | 0.265 | 0.01196 |
| B7 | full − memory | 0.55 | 0.75 | 0.73 | 0.48 | 0.118 | 0.226 | 0.01196 |
| **B8** | **+ adaptive seeding** | **0.73** | **0.93** | **0.79** | **0.56** | **0.088** | 0.248 | **0.00944** |

**The prediction is upheld.** Coverage, B8 − B6 = **+0.183, 95% CI [+0.083, +0.300]**, p = 0.001 — the interval excludes zero, which is what v4 registered as the test. B8 abstains on **4 of 60** items where B6 abstains on **15**. This is the first registered prediction the project has got right, and it was adjudicated on the quantity the prediction actually named, which v3 could not do.

**B8 is the only arm that beats the do-nothing baseline.** B8 − B0 = +0.283, CI [+0.083, +0.483], p = 0.009, and it survives Benjamini–Hochberg. Three of eight contrasts survive: B1−B0 and B2−B0 (negative, model-dependent) and this one.

**And it is the cheapest institutional arm per correct claim** — $0.00944 against B6's $0.01196 — despite running several times the seed-runs. Total spend is only 5% higher ($0.415 against $0.395), because token cost is per role-call and does not scale with seeds; compute is nearly free next to as-if-priced tokens. Buying resolution is cheap in exactly the currency this benchmark measures.

**A stability finding that qualifies everything above.** Arms B0–B7 ran twice — once under v3, once again under v4 — which is an unplanned replication of the whole ladder. B0, B1, B2 and B3 came back *identical to three decimals*; B4 moved **+0.100**, B5 +0.067, B6 and B7 −0.017.

The split is not arbitrary. B3 has no Custodian, so it reads the development split, which is deterministic given seeds fixed by item id. Every arm that varies is a *custodied* arm, and the Custodian's evaluation seed is derived from the registration id — a fresh UUID per run. That is deliberate (`docs/03`: a custody seed derived from the design would let anyone re-register a spec and shop a fixed evaluation set), and the price is that **custodied arms move by up to 0.100 between runs, six times the metric's 0.017 resolution.**

So: **B4 − B3 is not a stable finding.** It was +0.033 spanning zero in v3 and +0.133 excluding zero in v4, on the same arms and the same bank. The swing is the custody draw. B8 − B6 at +0.183 is larger than any single-arm swing observed, and B8 − B0 at +0.283 is larger still, which is why those are reported as findings and B4 − B3 is not.

**The next milestone is replication of the ladder itself.** One draw per arm is not enough at this resolution, and the project has now measured how much it is not enough by. Every arm should run several times and the report should carry the distribution rather than a single number.

---

### M16 · The paper ✅
`nullius paper build` renders `paper/index.html` from the committed protocols and results. A roadmap item (`docs/06`, template-rendered papers) and the natural place for the record to end up.

**Acceptance** — the document cannot report a flattering subset.

It does not select because it does not choose. Every registered protocol appears in registration order with its prediction and its outcome: **two upheld, two refuted, one registered and not yet run** — and the unrun one is labelled as such rather than omitted, because a plan with no result is part of the record too. Two results that later protocols retracted are still in the document, under the protocols that produced them.

**Nothing numeric is typed.** Every figure is read from a results file whose stored summary re-scores from its own per-item rows; every prediction is read from a protocol whose hash is in the git history; bank difficulty is computed from the locked truths. `assemble(strict=True)` refuses to build when a protocol fails to verify or a results file fails to re-score — a paper whose inputs no longer check out is worse than no paper, because it looks like evidence.

**Two prose sections, declared as data.** The six flaws and five limitations are the only hand-written content, held in `render.py` as constants so they can be counted and checked in one place. Each flaw names the milestone whose commit records it, and a test enforces that.

The flaw list is the section a written-up-afterwards paper would not have, because in that genre the flaws are fixed before anything is published. Here they are the record: five of them were found by *executing* a preregistered plan rather than by reviewing one.

A ninth CI job builds it on every push and uploads it as an artifact.

---

### M17 · The escalation was sizing itself from a bad estimate ◐ instrument done
Found while measuring something else, which is how most of this project's findings have arrived.

M14's escalation reads a standard deviation off the paired differences of the five mandatory seeds and sizes the extra seeds from it. **Five points make a much worse estimate than it looks.** Simulated at the measured paired SD of 0.00348:

| | SD estimate | seeds it asks for |
|---|---|---|
| 5th percentile | 0.00147 | **4** |
| median | 0.00320 | 7 |
| truth | 0.00348 | 8 |
| 95th percentile | 0.00533 | 15 |

It lands **under half the true value 8.9% of the time**. When it does, the escalation buys four seeds where eight are needed, the item stays underpowered, and it abstains — a failure on exactly the questions the mechanism was added to answer.

**The fix is an upper confidence limit, and the asymmetry is the point.** B9 sizes from the 80% chi-square upper bound rather than the point estimate, so uncertainty about the noise buys *more* data instead of less. Over-buying costs compute, which this project has measured at 5% of total spend for several times the seed-runs; under-buying costs an answer. It is not free: at five observations the bound is 1.56× the estimate, and seeds scale with the square, so the median escalation roughly doubles. **Protocol v6 registers that cost as part of the prediction** — if cost per correct claim rises without coverage improving, the bound is only expensive.

**A near-miss worth recording.** The measurement that started this was of sample size, not seeds, and a five-seed estimate said quadrupling samples cut the SD fivefold. At thirty seeds the same measurement said 0.73×, and the ordering was not even monotone. The first number was noise. I nearly built a milestone on it, and the thing that stopped it was re-measuring with more seeds — which is the same lesson the finding itself is about.

**A third instance of the same bug shape.** The paper's results-path table was a dict keyed by protocol version, and it raised a `KeyError` the moment v6 was registered. That is the third time something keyed by protocol version was maintained beside the registry instead of computed from it — after the CI job that listed protocols by hand, and the ladder that ran eight arms under a nine-arm plan. It is derived now.

All seven protocols verify and rebuild identically. **v6 has since been run, and refuted — against an arm that never sized anything conservatively.** The upper bound described here was built correctly and measured correctly; what was missing was the wire from the arm to it. M23 has the count of what that cost.

---

### M18 · The front door states the findings, and cannot drift from them ✅
The README said *"That may turn out to be false. The benchmark is designed to be able to say so."* It had said so, four times, and the front door did not mention it.

**`FINDINGS.md` is generated**, by the same assembled record the HTML paper renders — a second rendering, not a second account, so the two cannot disagree. CI regenerates it on every push and fails on `git diff --exit-code`. The check was verified in both directions: stable across regeneration, and it catches a one-line hand edit.

That closes the one place this project was asking to be taken on trust. A repository whose thesis is *take nobody's word for it* cannot ask a reader to take its README's word for it.

**The design documents are annotated, not rewritten.** `docs/04-evaluation.md` staked out the headline prediction in advance — *B4 will capture most of the gain over B3* — and a reader had no way to learn it was refuted. It now carries a note saying so, with the intervals, and pointing at the generated record. The document itself is unchanged, on the same principle that keeps a superseded protocol on disk: the design is a historical record, and editing it to match the result would destroy the thing that makes the result meaningful.

`docs/00-README.md` carries the same warning at the top of the set.

---

### M15b · The v5 ladder ✅ — replication resolves the contrast that flipped
`benchmark/results.v5.lock.json`, protocol `6bfaa136…`, 9 arms, 60 items, three passes per custodied arm.

| arm | n | acc | coverage | answered | null | brier | ece | $/correct |
|---|---|---|---|---|---|---|---|---|
| B0 | 1 | 0.45 | 1.00 | 0.45 | 1.00 | — | — | 0.00000 |
| B3 | 1 | 0.48 | 0.75 | 0.64 | 0.33 | 0.203 | 0.450 | 0.01343 |
| B4 | 3 | 0.52 | 0.74 | 0.70 | 0.41 | 0.136 | 0.333 | 0.01242 |
| B5 | 3 | 0.55 | 0.78 | 0.71 | 0.42 | 0.150 | 0.329 | 0.01179 |
| B6 | 3 | 0.53 | 0.74 | 0.71 | 0.42 | 0.111 | 0.285 | 0.01249 |
| B7 | 3 | 0.57 | 0.76 | 0.76 | 0.51 | 0.121 | 0.299 | 0.01151 |
| **B8** | 3 | **0.72** | **0.87** | **0.83** | **0.64** | **0.090** | **0.250** | **0.00971** |

**Both clauses of the prediction hold, and the second is the point.** Coverage B8 − B6 = **+0.122, CI [+0.044, +0.211]**, p = 0.002 — upheld. And B4 − B3 = **+0.039, CI [−0.056, +0.133]** — still spanning zero, exactly as registered.

That settles the contrast that flipped. v3's single draw said +0.033 spanning zero; v4's said **+0.133 excluding** it. Averaged over three custody draws it reads +0.039 and does not separate. **The v4 reading was the noise**, and the protocol that predicted so in advance was right to be cautious. This is the clearest thing replication has bought: a contrast that a single draw would have published.

**B8 remains the only arm that beats the do-nothing baseline** — +0.267, CI [+0.094, +0.439], p = 0.003, surviving Benjamini–Hochberg — now on three passes rather than one, and still the cheapest institutional arm per correct claim.

**Memory has now failed to show a contribution across four protocols.** B6 − B7 = −0.044, CI [−0.106, +0.011]; the point estimate is on the wrong side of zero.

---

### M19 · The reproducibility claim, verified rather than asserted ✅
The README claims results trace to hashed artifacts and the repo rebuilds from a clean clone. **Until M15 that was false for every custodied arm** — identifiers were random UUIDs, so the Custodian's evaluation seed differed on every run and no custodied result could be reproduced at all. It held only for arms that never query the Custodian, which is exactly why running the ladder twice left B0–B3 identical and moved everything above.

Measured now: running a custodied arm twice gives **bit-identical verdicts and realised effects to nine decimals**. Every scientific field matches. **Only `usd` differs, by about 0.2%** — compute is billed from wall-clock seconds actually consumed, which no seeding makes deterministic. Reporting it as reproducible would be the comfortable lie; dropping compute from the cost would make the economy measure half of what a run spends. A tenth CI job runs the comparison on a clean machine.

**Two display and reconstruction bugs, both found by reading the output rather than trusting it.** The results table had lost the `n` header while still emitting the value, so every column after `arm` was shifted by one and the whole table misread — my own first summary of v5 was wrong because of it. And `read_results` rebuilt outcomes field by field without `replicate`, so a three-pass arm reported itself as one-pass, which is the single number telling a reader how much replication is behind the figures. That is the fourth time a reconstruction path drifted from its schema, so the fix is a round-trip test comparing whole dicts rather than another named field.

---

### M20 · A null result was reported for a mechanism that could not have acted ✅
Memory has failed to show a contribution across five protocols. That is not a finding about memory.

**Memory can only act by changing what a model writes.** It adds recalled claims to the Theorist's view; nothing else. The mock's response is byte-identical with and without them — verified directly. So B6 − B7, whose arms differ in that one switch, measured **the difference between two custody draws** and was reported as memory's contribution in v1 through v5.

B1 and B2 were labelled `model_dependent` from the start for exactly this reason. Memory was not, and four registered protocols carried a null result for a switch that was delivered and discarded.

**The fix is general, not a special case for memory.** `MODEL_MEDIATED` names the switches that act only through a model — `memory` and `iterations` — and `Arm.differs_only_by_model` detects any contrast separated by nothing else. Contrasts so identified are labelled **not interpretable** rather than printed as intervals. The rule independently re-derives B2 − B1, which was already flagged, and leaves B4 − B3, B8 − B6, B6 − B4 and B9 − B8 interpretable, so it is narrow enough not to excuse every null.

Re-scored across all five committed results it flags **exactly one contrast per protocol, the same one**. No adjudicated prediction was ever made on B6 − B7, so no headline moves. This is a reporting correction, not a new analysis plan, and it needs no protocol v7.

**A false alarm worth recording.** The first diagnosis was that `recall()` returned nothing because each bank item runs in its own programme. It returned nothing *when I called it* — with the default `scope="program"`. The kernel calls it with `scope="lab"`, which returns ten claims. I had started editing `recall` to "fix" something that worked. Checking what the caller actually passes is what stopped it.

**Three malformed lists that had been shipping since M18.** Jinja's `trim_blocks` eats the newline after a block tag, so every Markdown bullet whose line ended with an inline `{% endif %}` silently joined the next — the baseline comparisons, the new contrasts, and the limitations all rendered as single run-on lines. **The CI drift check could not see it**: that check verifies the committed file matches the generator, and both were wrong in the same way. A consistency check is not a correctness check. Two structural tests now assert the generated Markdown is well formed — one caught two of the three lists on its first run, and the table test encodes the shifted-column bug from M19.

---

### M21 · The live path ✅ — wired, survivable, and cheap to repeat
Everything below was reachable only by a mock until now. No API spend yet; the machinery is what changed.

**A · Wired.** `AnthropicProvider` and `ProviderRefusal` are exported. `nullius.llm.factory` is the one place that turns a name into a working provider, and `--provider / --cache / --model / --max-usd` reach the runner and the kernel.

Two things were quietly false before. `runner.py` built `ModelRef(provider="mock", model="mock-1")` as a **literal**, so a live run would have recorded every B1 and B2 call as answered by a mock — and since the pricing table is keyed on model name, priced them at zero. And `cli.py` wrote `provider="mock"` into the results file as a literal, so a live run would have produced a results file claiming to be a mock run. For a project whose entire argument is that its record survives inspection, that is fatal rather than untidy. Both now carry what was actually used.

`detect_live_provider()` used only to print a row in `doctor`. It gates now: `--provider anthropic` with no key exits 1 **before a database, a workroot or a results file exists**. Verified — nothing is created.

**B · Survivable.** `worker.py` called `provider.complete()` bare. The only exceptions handled anywhere in that loop were `ValidationError` and `ValidationFailure` — schema problems. Over a multi-hour ladder, one 429, 529 or dropped connection is a certainty, and it would have raised straight out and ended the run *after everything up to that point was paid for*.

`RetryingProvider` wraps the network with exponential backoff and full jitter. A **decorator, not a change to the worker**, because a malformed response is evidence about the prompt and a rate limit is evidence about traffic — mixing them would spend the repair budget on weather. Retried statuses are 408/409/429/5xx/529; 400 and 401 are not, since they fail identically however often they are sent. An unrecognised error is raised rather than retried.

`ProviderRefusal` existed and nothing consumed it. A refusal is now a recorded failed task with its reason, in the ledger — retrying it would pay for the same answer, raising it would end a ladder over one role's prompt.

`SpendGuard` is the `--max-usd` kill switch. The budget machinery caps a *programme*, and the benchmark gives every item its own — so a $100 programme cap permitted sixty of them per arm and **nothing was watching the total**, which is the number that matters overnight. Checked at item boundaries; finer would interrupt a half-executed experiment, coarser is a whole arm too late.

Checkpointing was built for a crash while *writing results*. A mid-arm API failure is a different shape, and is now tested as one: a finished arm is on disk, the arm in flight is not, and resuming does not re-run what was already paid for.

**C · Cheap to repeat.** The live stack is `Caching(Retrying(Anthropic))`, and the order is the point — cache **outside** retry, so a call that succeeded on its fourth attempt is written once and every later run of that request is free. Reversed, nothing would be recorded. Tested.

**The cost estimate covered three of five roles.** `contracts_for` holds Theorist, Designer and Analyst; Skeptic and Reviewer live in `adversarial_contracts` and are a disjoint set, so the pre-flight number omitted them — and the Skeptic is by far the most expensive, reading the whole evidence bundle on Opus at **$0.036–$0.131 a call**. A cycle now prices at $0.089–$0.303 rather than a fraction of it. A cost estimate that silently prices part of the work is worse than none, because it is the number someone buys credit from.

A live smoke test runs one bank item end to end with a key and **skips rather than fails** without one.

**A wiring bug worth recording.** The first v4 ladder ran eight arms under a nine-arm protocol: a `ruff format` pass had collapsed the `run_ladder(...)` call onto one line before an edit meant to add `arms=` to it, so the replacement matched nothing and the runner silently used its eight-arm default. It produced a complete-looking results file — seven of seven baseline comparisons, no halted items — and nothing objected except the adjudication, which happened to name the missing arm by id. `score_ladder` now refuses any run whose arms do not match the protocol's, in either direction. The eight completed arms were reused from their checkpoints, so the correction cost one arm's compute rather than nine.

---

### M22 · The Station ✅ — the architecture, drawn from the enums that define it
A gamified floor plan of the institution: fourteen rooms, one per department, laid out from
`db/enums.py` and filled in from the committed record. `nullius station build` renders it to a
single self-contained HTML file, and refuses on the same terms the paper does.

**The map is not a picture of the architecture; it is the architecture, laid out.** A room
declares which `Role`s work in it and which `HypothesisState`s it owns. `unrepresented_roles()`
and `unrepresented_states()` are computed and a test fails on either being non-empty, so adding
a role or a state to the enum breaks the build until a room claims it. The corridor's order is
the order the states are declared in, and the exits are `TERMINAL_STATES` — both read off the
enum rather than listed beside it. **This is the fourth thing in this project keyed by a
project-level enum**, after the CI job that listed protocols by hand, the ladder that ran eight
arms under a nine-arm plan, and the paper's results-path table that raised on v6. It is the
first one derived.

**Two modes, and the page says which.** *Aggregate* reads only committed artifacts — protocols,
results, locked truths — so it builds from a clean clone and in CI. *Ledger* adds one arm's
SQLite ledger for the per-agent detail the lock files do not carry: registrations with their
timestamps, objections with their discriminating tests, holdout queries, the query audit, and
tokens per role. A room whose only source is the ledger renders empty without one and says so.

**Every figure names the artifact it came from.** `Figure` has no constructor that omits
`source`, so a number cannot reach the page without pointing at the file or table it was read
out of. The hand-written prose — four principles, four limitations, and each room's charter and
invariant — is declared as data, and a test asserts that none of it contains a digit, milestone
tags and arm ids excepted. That is the paper's discipline applied to a medium that is more
persuasive and less obviously checkable.

**No animation without a backing row.** A token's route is computed from its arm's switches and
its colour from the recorded verdict against planted truth; the pacing is display, and the page
says so on its own face. Nothing is drawn conferring, because the architecture denies that
agents converse and a drawing that showed them doing it would make the interface lie about the
system. The page states how many of the recorded passes are in motion rather than letting a
subset read as the whole record.

**The Registry is the one room built to depth**, because its invariant is fractal: the protocol
is hashed and committed before the ladder that tests it exists, and one level down each design
is hashed and locked before its own run. Both are shown as checks rather than claims — the git
history for the first, and a count of runs that began after the registration authorising them
for the second. On a v5 institutional ledger that is 600 of 600. The other ten dashboards are
deliberately not built; design mistakes get replicated ten times if you build them all first.

**The Vault is the room you cannot see into from inside.** It reports 1,800 holdout rows
computed by the Custodian and **zero computed by anyone else** — the `CHECK` constraint holding
on the record in front of it, which is a different statement from the constraint existing. B3,
the arm with no Custodian, leaves it untouched, which is the switch being connected rather than
merely recorded.

#### What building it revealed was wrong

**1. A registered switch that reaches no code at all.** `Arm.reviewer` is declared on every arm,
hashed into every registered protocol from v1, and set on B6, B7, B8 and B9. `mechanisms_for`
maps seven fields into the kernel and `reviewer` is not one of them; nothing else in `src/`
reads it. Flipping it leaves the kernel's switches identical, so an arm that sets it does not
differ from one that does not. The `reviews` table is empty in every ledger this project has
produced, and `adversarial_contracts` — which holds the Reviewer's contract, input view and
validator — is consumed only by the cost estimator.

This is M20's shape, one layer lower. M20 named the switches that can act *only through a
model*; there was no rule for a switch that acts through *nothing*. `unread_switches()` now finds
them by flipping one field at a time on a probe arm and asking whether the resulting
`Mechanisms` changes, and `HANDLED_OUTSIDE_THE_KERNEL` declares the two that legitimately act
elsewhere — `iterations`, read by the direct-agent path, and `model_dependent`, which is a label
and not a switch. Everything left over is reported as dead on the page. A test proves the probe
can tell a live switch from a dead one before it is believed about `reviewer`, and another
proves the excuse dict cannot grow into a place to hide one.

**No result moves.** B5 → B6 is documented as adding replication, review and memory; review
contributes nothing, and memory is model-mediated and already labelled uninterpretable. So that
step measures replication, plus custody noise. No adjudicated prediction was ever made on it.
This is a reporting correction, not a new analysis plan — and wiring the Reviewer would change
what every institutional arm does, which is a protocol v7 and not a bug fix.

**2. Nine of fifteen states are written by nothing, and no hypothesis has ever reached a
terminal one.** `advance_hypothesis` is called with `DRAFT`, `SHELVED`, `REGISTERED`, `EXECUTED`,
`ANALYZED` and `ABANDONED_BUDGET` anywhere in `src/`. `SCREENED`, `BUILT`, `CHALLENGED`,
`REPLICATED`, `REVIEWED`, `INSTITUTIONAL`, `REFUTED`, `INCONCLUSIVE` and `REVISED` are written by
no code path at all. Every hypothesis in every ledger this project has produced sits at
`analyzed`.

The work is *done* — there are `bundle.built`, `objection.raised`, `replication.recorded` and
`claim.promoted` events in the same ledgers — it is simply not recorded *as state*. So the
station draws every terminal exit at the same width and every one of them reads zero, which is
the honest picture and a sharper one than the drawing I set out to make.
`docs/02-architecture.md` §3 states as a hard invariant that *the report generator fails loudly
if any registration lacks a terminal state*. Nothing implements that, and if it did, every run
in the project's history would trip it. Recorded as
[ADR-0008](docs/adr/0008-station-draws-the-record-not-the-design.md).

**3. Two enums share two words, and reading one off the other would be the drawing's easiest
lie.** `Verdict` and `HypothesisState` both contain `refuted` and `inconclusive`. The first is an
answer about the world, scored against planted truth; the second is where a hypothesis stopped.
The record room keeps them in separate blocks with the distinction stated, and the test that
guards it asserts the overlap still exists rather than asserting it away.

**Acceptance**
- `nullius station build` produces a self-contained page from committed artifacts alone, with no
  ledger present — no CDN, no stylesheet, no remote image, every `href` pointing inside the file.
- `assemble(strict=True)` refuses a protocol that does not verify and builds when they all do;
  both directions tested, and a page built with `strict=False` says so on its face.
- Every `Figure` carries a source, and no hand-written string on the page contains a digit.
- Every `Role` and every `HypothesisState` is represented, each state by exactly one room.
- `model_dependent` arms are labelled, and a contrast separated only by a model-mediated switch
  is shown as *not interpretable* rather than as an interval — using the paper's own
  `contrast_note`, so the two documents cannot describe one interval in two different ways.
- The provider is read from the results file and displayed; tested with a mock and with a live
  value.
- Reading a ledger cannot write to it, proven by trying rather than by the URI looking right.
- An eleventh CI job builds the station on every push and uploads it as an artifact.

---

### M23 · The switch was declared, hashed, translated — and read by nothing ✅
The v6 ladder finished: twenty-two passes, ten arms, `benchmark/results.v6.lock.json`.

```
  PREDICTION REFUTED  coverage (B9-B8) = +0.0333, 95% CI [-0.0111, +0.0889]
```

**That refutation is not about conservative sizing.** B9 is B8 with one boolean,
`conservative_escalation`, which sizes the escalation from an 80% chi-square upper bound on the
noise instead of the point estimate. The field is declared on the arm, hashed into protocol v6,
carried by `mechanisms_for` into the kernel's `Mechanisms`, read at `kernel.py`, and handed to
`_replicate(conservative=…)`. **`_replicate` never used it, and neither call to `_escalate`
passed it at all.** So the flag crossed every boundary and stopped one line short of the
calculation.

The signature is unmistakable once looked for:

| | B8 | B9 |
|---|---|---|
| seed counts, 180 outcomes | 5×60, 6×6, 7×3, 8×3, 9×3, 11×6, 14×6, 18×6, 19×6, 21×6, 23×3, 24×72 | *identical* |
| total seeds bought | 2,703 | 2,703 |
| outcomes where the two differ | — | **0 of 180** |

Not similar. Identical, item by item and replicate by replicate. After threading the flag
through both call sites, the same two items move from 12 seeds to 21.

**Three checks, and the one that was missing.** Each catches what the one above it cannot.

1. `unread_switches()` (M22) flips a field and asks whether `Mechanisms` changes. It found
   `reviewer`. It cannot find this one — `conservative_escalation` *is* a field on `Mechanisms`,
   so flipping it does change the object.
2. **A parameter accepted and never read.** `ARG` is now selected in ruff, and it names the bug
   in one line: `kernel.py:922 ARG002 Unused method argument: conservative`. This is the check
   that would have cost a second rather than a six-hour ladder. Five modules are exempted —
   `@detector`, `@register_view`, `@transform_op`, SQLAlchemy's `TypeDecorator` and its event
   listeners, where the signature *is* the interface — and `kernel.py` deliberately is not.
   `tests/test_switches.py` asserts the same rule over the kernel by AST, so no edit to
   `pyproject.toml` can switch it off where it cost something. Enabling the rule also found a
   genuinely dead parameter in `ExperimentRunner._charge`, which took `artifacts` and billed
   from `result.outputs`; it is deleted rather than exempted.
3. **Running both arms and comparing what they bought.** Only an execution can prove a switch is
   connected — an argument can be read, passed on, and still reach nothing, which is exactly what
   happened here. Marked slow, two items, and it fails on the pre-fix kernel.

**What v6 is evidence of.** Not conservative sizing, which it never ran. B9 minus B8 is an
*unplanned negative control*: two arms that were operationally identical, differing only in
registration id and therefore in the Custodian's evaluation draw. Its interval is a measured
noise floor for this ladder at three replicates — coverage +0.033 [−0.011, +0.089], and verdict
accuracy −0.044 [−0.117, +0.028] computed afterwards and labelled as such. **The interval
covered zero on a contrast whose true difference is zero**, which is the first calibration check
this project's bootstrap has had. One draw cannot establish coverage, and it is not offered as
if it could; it is one honest observation where there were none.

It also sets the scale for the neighbours. v6's other three contrasts — B4−B3 = +0.039,
B6−B4 = +0.006, B6−B7 = −0.044 — are all the size of a difference now known to be zero, and all
three already span zero. The bootstrap was saying so; the control is the first thing to
corroborate it from outside.

**v6 stands on the record.** Its lock file is committed, its verdict is reported as refuted, and
the paper says in the same breath that the refutation adjudicates nothing. Deleting a registered
protocol's result because the run embarrassed the code is the exact move this project exists to
make impossible. **Protocol v7 re-registers v6's prediction** — same ladder, same adjudicated
contrast, hash `0dac6ca41fdb` — against a switch that is connected. It has not been run.

**The shape, for the third time.** M20: a switch that acts only through a model, reported as
mechanism. M22: a switch that reaches no code at all. M23: a switch that reaches the kernel and
is dropped inside it. Each was found by asking a *different* question about the same claim —
that an arm's declared mechanisms are the mechanisms it runs — and each time the previous
milestone's check was structurally unable to see the new case.

**And a fourth hand-maintained list.** The CI job that re-scores every committed results file
named five paths in a tuple. There were six on disk, so `results.v6.lock.json` — the run this
whole milestone is about — would have gone through CI unchecked. It is a glob now, and it fails
loudly if the glob matches nothing rather than passing vacuously.

**And M18's bug, one shape along.** Every chapter heading in `FINDINGS.md` after the first has
been rendering glued to the previous paragraph — `…rests on them.### Protocol v2` — since the
document existed. A `{%- else %}` swallowed the newline that ended the footnote. M18's structural
test asserts that a heading has a blank line before it, and could not see this because the line
does not start with a hash; the drift check could not see it because the file and the generator
were wrong together. It is fixed, and the guard is now *no hash anywhere but the start of a
line*, which fails on five lines of the previously committed file.

**Acceptance**
- `conservative` is passed at both `_escalate` call sites, and B9 buys strictly more seeds than
  B8 on items that escalate below the ceiling.
- `ruff check` selects `ARG` and passes; the kernel is not exempted, and an AST test asserts the
  same rule over it independently of the config.
- The AST guard fails on `git show HEAD~1:src/nullius/kernel.py` with exactly one finding,
  `_replicate(conservative)`.
- Protocol v7 verifies, rebuilds identically, and appears in the paper as registered and not yet
  run.
- The paper and `FINDINGS.md` carry the flaw and the limitation, and CI's drift check passes.
- CI re-scores every `benchmark/results*.lock.json` found by glob — six of them, not the five
  the tuple named — and exits non-zero if it finds none.
- No `#` appears anywhere but the start of a line in `FINDINGS.md`, which the file committed at
  `8460649` fails on five lines.

---

### M24 · The Station, actually drawn ✅ — a place, not a wireframe
M22 built the record correctly and drew it at about five per cent of the size it needed.
Fifteen rooms fitted in 880 by 520; a room was twenty-six units across, a person in it was
three pixels, and a fixture was a hairline. Every concept was already there and none of it was
legible. This milestone is presentation only: `render.py` and the templates. `model.py`,
`map.py` and `ledger.py` are untouched, and every test written against them still passes.

**Scale was the whole problem.** `SCALE` turns one plan unit into twelve drawing units, so the
map is authored once in `map.py` at the size that is right for reasoning about adjacency and
drawn at the size that has somewhere to put a console. A room is now 312 by 288 and the station
is 2872 by 1678, in a pan-and-zoom viewport: drag to pan, scroll to zoom about the cursor, click
a room to focus it and open its dashboard. It fits to screen on load, so the whole facility
reads at a glance before anybody dives in.

**Rooms are places.** Walls nine units thick with a lit inner edge and a doorway cut where the
corridor meets them — computed from consecutive rooms in the walk, so a room that moves in the
plan takes its doorways with it. A plated floor tinted toward the room's function colour, under
a soft lamp in that colour that is most of what makes it feel like somewhere. Five to ten drawn
fixtures each, appropriate to the work: racks and a bench in the Workshop, a long table of
specimen runs on the Execution floor, sealed cabinets and one terminal in the Vault, shelving in
the Archive. Drop shadows under the walls and the furniture, one light direction throughout.

**Agents are people at posts.** Forty-eight units tall with a distinct silhouette per `Role` —
the Skeptic hooded with a glass, the Custodian suited with a visor slot, the Replicator blindfold
banded and trailing a faint second outline, the Builder in a hard hat. `SYSTEM` is drawn as a
servitor rather than a person, because it is the deterministic control plane and giving it a face
would be the drawing making a claim the architecture does not. Each bobs on its own period,
seeded from its room's id. A room with no actor is still drawn with nobody in it.

**The exits are doors.** Cut into whichever outer wall faces away from the middle of the station
— chosen by probing for a wall with no room behind it and no corridor through it, so moving a
room moves its exits — with a lit counter plate outside each. Equal width by construction, and a
test says so.

#### What building it revealed was wrong

**1. The captions collided because each was placed against a coordinate that happened to be
free.** The Vault's "no corridor crosses this line" landed on the Treasury's "0 · abandoned
budget" and the two rendered as one unreadable string; "NO ACTOR STATIONED" hung outside the
record's box. Both were fixable in one line each, and fixing them would have left the next pair
to be found by eye.

Every string on the map now goes through `_label`, which measures it, truncates it to its
container and records the box it occupies — and the box is a promise rather than an estimate
because the same width is emitted as the element's `textLength`, so what the layout reserves is
what the browser draws, on any platform and in whichever fallback font resolves.
`overlapping_labels` returns the intersecting pairs and a test requires the list to be empty at
1x, 2.5x and 7x. Boxes are in world units and the camera is a uniform transform, so that is the
whole zoom range.

**2. A float one unit in the last place truncated a label to fit a box built around it.**
`BUILDER` rendered as `BUILD…` on a chip sized for `BUILDER`. The chip measured the string, added
padding to get its width, then handed the label back `width − padding` as its container — which
in binary is not the number it started from. The reproduction is exact: `7 × 13 × 0.6` round
tripped through `+18, −18` comes back smaller than itself. `_truncate` now carries a tolerance
and the chip passes the measured width rather than recomputing it, which is the more important
half of the fix.

**3. A dict key named after a dict method rendered as the method, for the second time.**
`token.values` printed `<built-in method values of dict object at 0x…>` into an SVG animation.
The same shape put `<built-in method items>` into the Registry's protocol table in M22 — Jinja
resolves an attribute before a key, the page still builds, and nothing complains. Renaming it
twice is not a fix, so a test now walks every structure handed to the template and fails on any
key that shadows a `dict` attribute.

**4. The zeroes on the exits read as a broken renderer.** Four doors, four counts of nothing.
They are correct — M22's finding was that no code path writes a terminal state, so nothing has
ever left by one — but an honest zero still has to look deliberate. The doors are drawn unlit,
and the room says in as many words that the count is a fact about the code rather than a failure
of the drawing.

**Acceptance**
- Every test from M22 and M23 still passes; the data layer was not touched.
- A room's interior holds at least five drawn fixtures, all of them inside its floor, and every
  stationed role renders a figure of at least sixteen units with its own silhouette. Tested.
- Pan, zoom and click-to-focus work, and the station fits to screen on load.
- No two text elements on the map overlap, at any zoom the viewport reaches. Tested against the
  measured boxes rather than by eye.
- Every label fits the container it was given, and the exits' counter plates never sit over a
  room's floor. Tested.
- Still one self-contained file — no CDN, no external font, no remote image — and it still
  refuses to build on input that does not verify.
- `prefers-reduced-motion` stops every animation and the pause control starts pressed when it is
  set.

---

### M25 · A map, not a plan ✅ — callouts, a roster, and people who walk
M24 drew the station at a size worth looking at and then framed it as a diagram: a map in one
column, a permanent dashboard in the other, room labels printed on the floors, and staff who
stood still. This milestone makes it a map you play with. Presentation only again — `model.py`,
`map.py` and `ledger.py` are untouched and every test written against them still passes.

**The label came off the floor.** Each room is named by a callout card that floats beside it,
with a numbered badge, the room's name, chips for the roles stationed there and the states it
owns, a status lamp and one word for what it is doing: `WORKING`, `IDLE`, `NO DATA`, `LOCKED` or
`SEALED`. Every one of those words is read off the assembled record — a locked room is one whose
feature is unbuilt, a sealed one has no corridor into it by design, an idle one is a room the arm
on display does not engage. Moving the label out gave the interior back its top third, which is
now a third row of furniture.

**Cards are placed by search, not by hand.** For each room the layout sweeps every position
around it — four sides, three distances, nine slides along each wall — discards any that leaves
the viewBox, sorts what is left by distance from the room, and takes the nearest that hits
nothing already on the map. The result is that each card sits directly above or beside the room
it names, and no two of them touch. Screening's card stacks above its own exit plate because that
is the only clear space its room has.

**The map is the page.** Full width, up to 88% of the viewport height, with a head-up display
over it: the station's identity top-left, zoom and pause top-right, the legend bottom-left, and a
department roster down the right — fourteen rows with a status pip each, which is also how you
reach a room without hunting for it on the map. Clicking a room, its card or its roster row opens
the dashboard as a sheet over the map rather than in a column beside it, so the dossier gets the
width it deserves and the map keeps all of its own.

**People walk.** Every stationed role paces a patrol on the clear floor in front of its
workstations, with its own stretch of the room, its own lane and its own phase. They stay in
their rooms: the token is what moves through the station, and a role walking the corridor would
say something about the architecture that is not true.

#### What building it revealed was wrong

**1. A search whose failure mode is "use the answer it started with" is not a search.** The first
card placer had six fixed slots and fell back to the first one when all six were rejected. It
silently put Screening's card over its own exit plate, and then — once the sweep was widened —
put the Oracle's card over the Vault. Both are the same bug: the fallback was a guess dressed as
a result. It now takes the slot with the least overlap, which is a defensible answer, and a test
asserts no card sits over any room, so a future crowding shows up as a failure rather than as a
drawing.

**2. The right edge of the plan had no margin, and the sealed column had nowhere to put its
cards.** The world's margins were symmetric because that is the obvious way to write them. The
two edges are asked for different things: the record's counter plates hang off the left, and the
Vault, the Oracle, the Archive and the Treasury are stacked against the right with sixty units
between them and no room on any side. Asymmetric margins, and the column's cards sit outside it
where they belong.

**3. Two actors in one room walked the same lane and drew as one shape.** Screening's Director
and Literature, and the Registry's Designer and the system servitor, were given the same stretch
of floor with different phases — which keeps them apart most of the time and merges them the rest
of it. Each now gets its own slice of the width, so they cannot occupy the same place whatever
the phase. A test asserts the patrols are disjoint.

**4. A card wider than the gap between two rooms cannot sit above its own room.** The room pitch
is 348 units; the first cards were up to 520. Each pushed the next along the row until Screening's
label was hanging over Registry, three rooms from the thing it named, with a leader line making it
traceable rather than readable. Cards are capped at the pitch, and the index moved into a badge so
the name has the width instead.

**Acceptance**
- Every test from M22, M23 and M24 still passes; the data layer was not touched.
- Every room is named by exactly one card, the card carries the room's own name, and no card sits
  over any room or leaves the viewBox. Tested.
- Every card's status word is derived from the record, and matches what the record says about
  that room. Tested against all five states.
- Every stationed role walks a patrol that stays inside its own room's floor and never shares
  floor with another actor. Tested.
- The dossier holds a panel for every room and opens over the map; the roster lists every
  department. Tested.
- No two labels on the map overlap, at any zoom. Still tested, now with the cards in it.
- Still one self-contained file, still refuses to build on input that does not verify, and
  `prefers-reduced-motion` still stops every animation including the walking.

---

### M26 · The map answers, and the dossier is a console ✅
Two milestones of drawing, and clicking a room did nothing at all. Fixed, and then the dossier
was made worth opening: eleven actors redrawn as their own builds, and a dashboard with pages,
an arm switch and a filterable record of every item. Presentation only again — `model.py`,
`map.py` and `ledger.py` are untouched.

**The map answers now.** Click a room, its card or its roster row and its dossier opens over the
map; Escape or the scrim closes it.

**The actors are builds, not silhouettes.** The Director in a long coat with a peaked cap and a
tablet; Literature with round spectacles carrying a stack of books on a satchel strap; the
Builder in a hard hat with a lamp, a tool belt and a wrench; the Designer in an apron with a
drafting triangle, a T-square and a pencil behind the ear; the Analyst in a headset reading a
floating panel of bars; the Skeptic hooded with a glass; the Custodian sealed into a suit whose
helmet has a visor slot instead of a face, with a keyring; the Replicator blindfold-banded and
dragging a faint copy of itself; the Reviewer in a mantle with a stamp; the Theorist trailing a
scarf with a slate. `SYSTEM` is still a servitor rather than a person, for the same reason as
before. A test draws each on its own and requires no two to come out the same.

**The dossier is a console.** Six pages where there was one — *overview*, *figures*, *arm
scores*, *items*, *provenance*, and *protocols* or *contrasts* where the room has them — with an
arm switch across the top. The switch is the interesting part: it changes which recorded arm
every panel is describing, **and the map with it**. The route the tokens walk, which rooms are
lit, every card's status word and lamp, and the roster all follow, because a dossier that
disagreed with the drawing behind it would be worse than either alone.

The *items* page is the arm's own record — one row per bank item per pass, with what it answered,
what is true, whether that scored, seeds bought, dollars spent, realised against true effect and
the margin to the nearest verdict boundary. Filter by outcome, search by item, sort by any
column. The *provenance* page lists every figure in the room beside the artifact it was read out
of, which is the claim this project makes about itself, made checkable in the place a reader is
standing.

Every arm is assembled by re-entering `assemble()` rather than by reconstructing its figures, so
the switch shows what the record says and cannot drift from it. A test asserts that each arm's
figures equal the ones that arm produces when assembled on its own.

#### What building it revealed was wrong

**1. An invisible sheet of glass over the whole map.** The tokens are drawn after the rooms and
carry a bloom filter, and a filter's region is much larger than the thing it blurs — the group's
bounding box is the entire route, inflated by the filter margins. With hit testing left on, that
one group sat over every room on the map and ate every click. The page rendered perfectly and
simply did not respond, which is the worst shape a bug can have.

Every layer drawn above the rooms that is not itself a target now declines hit testing, and a
test reads the markup and fails if one of them stops. The failure was invisible by construction,
so it needed a check that does not depend on anybody noticing.

**2. Capturing the pointer on `pointerdown` cancels the click.** The second half of the same bug,
and it would have survived the first fix. Pan was implemented by capturing the pointer as soon as
a button went down; the browser then retargets the subsequent `click` to the capturing element,
so a click on a room arrived at the viewport instead. Capture is now taken only once the pointer
has actually moved, and the click is handled by delegation from the viewport, which is immune to
retargeting either way.

**3. A drag threshold that never reset.** `moved` accumulated across a drag and was only zeroed
on the next `pointerdown`, so the guard that suppresses a click at the end of a pan was carrying
state into the next interaction. It is measured from the start of each press now.

**Acceptance**
- Every test from M22 through M25 still passes; the data layer was not touched.
- No layer drawn above the rooms accepts hit testing. Tested, and the test was proven to fail
  with the fix removed before it was believed.
- Every id the page's script looks up exists in the document it ships in. Tested — a generated
  page whose script and markup disagree fails silently in a browser and nowhere else.
- The page carries every arm of the protocol on display, and each arm's figures are the ones
  that arm produces when assembled alone. Tested.
- The items table is the recorded outcomes, one row per item per pass, unsummarised. Tested.
- No two roles are drawn the same way. Tested by rendering each build on its own.
- Still one self-contained file, still refuses to build on input that does not verify.

---

### M27 · Fittings, props, and a hallway that is a place ✅
Presentation only again — `model.py`, `map.py` and `ledger.py` untouched.

**The corridor was a stroke through the room centres.** Wide, dark, and a line on a diagram
rather than somewhere you could walk. The runs of floor that are genuinely *outside* a room are
now computed from the gap between two consecutive shells and drawn as sections: walls down both
sides, deck plating, a dashed guide down the middle and a light overhead. They line up with the
doorways because they are measured from the same gap the doorways were cut for. Inside a room the
walk continues as a band of darker plating across its floor, so the route reads as one run rather
than as rooms with a line between them. Each threshold gets hazard chevrons.

**Rooms went from seven objects to twelve.** A fittings band against the top wall — vents, conduit
runs with a pilot light, an illuminated sign over the door — a third row of furniture, and loose
props tucked into the margins the patrol does not use: chairs, barrels, a cart, a coil of cables,
a floor hatch. Eleven new fixture kinds. Each room also carries its number as a large faint floor
stencil, which is the same index its callout card shows.

**The actors have arms.** Ten builds gained shoulders, sleeves in the room's colour, and hands —
which is what was missing when a figure was holding a tablet or a wrench with nothing to hold it
with.

#### What building it revealed was wrong

**1. Two bands were computed from different origins and landed on each other.** The overhead
light strips were positioned from the top of the floor and the new wall fittings from the content
origin, and at the room's height those two happen to coincide — the lights were drawn inside the
vents. Neither was wrong on its own. The lights now run across the room where a ceiling light
would be, rather than along the wall where the fittings are.

**2. A patch script that asserts its way down a file leaves the file half-written.** The
substitution for the floor stencil did not match, the assertion fired, and because the write is
at the end nothing at all was applied — but the earlier substitutions had already succeeded
against the in-memory copy, so the run *looked* partial and was in fact a no-op. Worth recording
because the failure reads as the opposite of what it is: an aborted all-or-nothing patch is
safer than a partial one, and it is easy to spend a while looking for changes that were never
written.

**Acceptance**
- Every test from M22 through M26 still passes.
- A room holds at least five drawn fixtures, all inside its floor — now twelve to fourteen.
  Tested.
- No two labels overlap, including the new floor stencils, at any zoom. Tested.
- Still one self-contained file; still refuses to build on input that does not verify.

---

### M28 · Materials, and a room built out of its own work ✅
Presentation only for the third time — `model.py`, `map.py` and `ledger.py` untouched.

**Every object in the station was made of the same two greys.** `--fix` and `--fix-2`, plus the
room's own accent for anything that lit up. That is why a workbench, a filing cabinet and a
reactor were the same rectangle at different proportions, and why a room read as a plan of a room
rather than as a place. Things are now made of something: steel, enamel, wood, brass, glass,
rubber, painted metal, each with a lit side and a shadowed one, and a handful of fittings light up
in a colour the room does not get to choose — a pass bin is green wherever it stands and a coolant
line is cold everywhere.

**And the kit was generic.** Nine of the fourteen rooms held a rack, a cabinet and a shelf, which
says the nine departments do the same work — the one thing the map exists to deny. Each room now
owns equipment nobody else has: a drafting table and a corkboard strung with red thread in
Drafting; a scanner arch and three sorting bins in Screening; a seal press and a wall of numbered
drawers in Registry; pegboards, parts bins and coolant valves in the Workshop; a core in a cage on
the Execution floor; plot walls and an oscilloscope in Analysis; targets and a training dummy in
Challenge; a case of blanks and a key safe in the Blind room; a reading lamp and a podium in
Review; a press and tape reels in the Record; two vault doors and bullion on pallets in the Vault;
a teller counter and a balance in the Treasury; card catalogues in the Archive; an orb in a gimbal
and two dishes in the Oracle. Thirty-two new kinds of object; twelve to fifteen in every room.

**The corridor got wider and got equipment.** Hazard-striped edges, grating across the run, rivets
along the wall panels, direction arrows down the middle, a pipe run with a junction box, pools of
light under the lamps, and the run's own number stencilled on the deck. Each of those is placed
from the hall's index, so the run between Drafting and Screening is not the run between Screening
and Registry drawn a second time.

**The actors got volume.** Boots with soles, a far leg in shadow, a torso with a lit shoulder edge
and a shaded side, sleeves ending in gloved hands, a face with a brow, an ear and a shadowed
cheek — and a chest insignia whose shape is the role's own.

#### What building it revealed was wrong

**1. `.glowpane` animates opacity, so the opacity you wrote is not the opacity you get.** The
class exists to make a screen breathe between 0.7 and 1. Put it on a twelve-percent halo over the
bullion and the halo is composited at eighty-five percent: a solid lens of gold sitting on top of
the bars. The animation wins because it is the animated value, not the presentation attribute.
Anything that wants to be faint has to not be told to hum. Soft edges now come from a radial
gradient, which is also what the three hard-edged elliptical smudges in every room turned out to
need.

**2. The arms went on before the coat, so the hands were inside it.** Boots, legs and arms were
drawn first and the torso over the top, which is right at the shoulder and wrong at the wrist:
the hand sits a hair inside the coat's own silhouette and disappears, leaving every figure with
two stubs. It was invisible while the figures were flat and obvious the moment they had gloves to
lose. Arms now go on after the body and before whatever the figure is carrying.

**Acceptance**
- Every test from M22 through M27 still passes.
- Every kind a kit names has a branch in the fixture macro and a material. Tested — a kind
  without one falls through to a grey box that looks like furniture from a distance, which is
  exactly how the station got here.
- No two rooms are furnished the same way; each owns at least one thing nobody else has. Tested.
- Every room is drawn in at least three materials and lit by at least two colours. Tested.
- Each run of corridor knows which run it is. Tested.
- Still one self-contained file; still refuses to build on input that does not verify.

---

### M29 · A map with nothing written on it ✅
Presentation, plus twelve room names. `model.py` and `ledger.py` untouched; `map.py` changed only
where it holds the string a room is called.

**The map was a diagram wearing a drawing.** Fourteen callout cards, a roster, a header, a legend,
counter plates and every caption on the station were on screen at once, and under all of it was
the thing worth looking at. At rest the map now carries no writing at all — rooms, fittings,
people, and the number painted on each floor, which is something painted on the room rather than
writing about it. Hovering a room is the question and a peek is the answer: which department, who
is stationed there, what is behind the number, what it is doing on the arm on display. Clicking
opens the dossier as before. `labels` puts the whole annotated layer back, and the fit follows —
bare, the view frames the building; annotated, it frames the world the callouts live in.

**And everything in it was flat.** Objects were filled and stroked and that was all, which is why
the station read as printed rather than built. Every fixture now goes through one lighting pass:
a bevel taken off the shape's own alpha, a specular hit from up and to the left, and the shadow it
casts on the floor — so all fifty kinds are lit without any of them knowing about it. The walls
are drawn as walls: an outer face, a cap, an inner face and bolts, with the floor sunk inside them
and the shade they throw falling across it. Floors gained a painted work zone, wear, bolts and
drains; the ground the station stands on gained a depth gradient and a hundred and fifty stars.

**Twelve rooms are named for places rather than for activities.** Analysis is what you do; the
Analysis Room is where you stand. Design Room, Screening Room, Registry Room, Development
Workshop, Experiment Floor, Analysis Room, Challenge Chamber, Blind Testing Room, Review Room,
Records Room, Resource Room, Archive Room. The Vault and the Oracle keep their names, because
those two are not rooms you work in.

#### What building it revealed was wrong

**1. A specular pass that is correct is still a specular pass that is wrong.** The first relief
filter had a wide blur and a low exponent, which spreads the highlight across the whole of a
shape instead of hugging its edge. Every material went milky: the workbench stopped being wood
and the parts bins went pastel. The fix is not less light, it is a tighter light — a small blur
and a high specular exponent — and the material colours had to come down half a step anyway,
because a surface that is now lit does not also need to be bright.

**2. A label that does not fit is not allowed to lie about the name.** `DEVELOPMENT WORKSHOP` is
wider than the widest card the room pitch allows, and the label system's answer to that was to
truncate, so the card beside the Development Workshop said `DEVELOPMENT WORKSH…`. Every other
string on the map may be clipped; the name of the room the card points at may not. Names are now
set smaller until they fit, and the test that caught it is the one that asks each card to carry
its room's name in full.

**Acceptance**
- Every test from M22 through M28 still passes.
- The callouts, the captions, the counter plates and the roster are hidden at rest and shown by
  `labels`; the floor stencil is not. Tested against the stylesheet.
- Hovering a room names it, from the same record the dossier reads. Tested.
- The bare map is framed to the building, and every room is inside that frame. Tested.
- Every room but the Vault and the Oracle is named for a place, and the page carries every name
  in full. Tested.
- Still one self-contained file; still refuses to build on input that does not verify.

---

### M30 · Things that stand on the floor ✅
Presentation only. `model.py`, `map.py` and `ledger.py` untouched.

**Every object in the station was an axis-aligned rectangle lying flat.** Three milestones went
into the kit — thematic equipment in M28, materials and lighting in M29 — and the map still read
as coloured squares inside a bigger square, because none of that addressed the thing that was
actually wrong. Nothing in the drawing distinguished a cabinet from a mark painted on the deck.
Detail added to a flat rectangle makes a busier flat rectangle.

**Every fixture now has a height, and is drawn as a solid.** It arrives with `f.z`, which is how
much of its box is the face you look at rather than the top you look down on, and one shared
`solid()` draws it: a cast shadow, a front face in its own shadow darkening towards the floor, a
top face in the light, a bright seam where the two meet and a dark contour all the way round.
Cylinders — barrels, the reactor core — go through `drum()` and get a lid and a curved body.
Detail then goes on the two faces, which is what makes an object legible rather than merely
present: a rack has a vented lid and blade units with lights in its face; the sorting bins are
tubs with coloured lids; the parts wall is bins tilted towards you; the file walls are drawers
with brass pulls and one left open; the press has a wheel and a sheet coming off it; a stool is a
seat on a post with legs under it.

The height is a fraction of the box the row already allotted, so standing up costs the drawing no
floor space and nothing can stand into the row behind it.

#### What building it revealed was wrong

**1. A specular filter and hand-drawn faces are the same job done twice.** M29's relief pass
existed to fake shading on flat shapes. Applied over faces that are already lit it did what it
always does to a lit surface — added light — and every material went milky again, exactly as it
had at the start of M29. The specular came off; the filter now does only the part that is still
needed, which is putting the object on the floor.

**2. The figures became the flat thing.** They were fine while the furniture was flat too. The
moment the furniture had volume, the people were the only cut-outs left in the room, and because
a figure is drawn from many small parts it has no single silhouette to stroke. The contour is
taken from whatever silhouette each one happens to have, by dilating its own alpha — which is
also the only way to outline a shape that changes as it walks.

**Acceptance**
- Every test from M22 through M29 still passes.
- Every fixture has a height, and the height is the one the table says. Tested.
- A solid is drawn inside the box its row allotted. Tested — a thing that stands up must not
  stand into the row behind it.
- The lighting language goes through the shared helpers, and the specular pass it replaced is
  gone from the page. Tested.
- Still one self-contained file; still refuses to build on input that does not verify.

---

### M31 · The cutaway, and the building between the rooms ✅
Presentation only. `model.py`, `map.py` and `ledger.py` untouched.

**The camera is on its side.** Every room was a floor you looked down at, with its furniture laid
out in four bands of depth. A room is now a chamber you look into: a ceiling with fittings hung
from it, a back wall with a service run across it, and a floor slab that everything stands on.
There is one axis left and it is up, so how tall a thing is has become the whole of what says what
it is — a wall of filing drawers is three quarters of the chamber, a stool is a quarter of it —
and the rows are divided in proportion to how wide each thing deserves to be rather than evenly,
because a row of identical columns reads as a chart rather than as a room somebody laid out.
Everything either stands on the ground line or is fixed above it. There is no third case.

**And the black between the rooms is the building now.** Fourteen lit boxes floating in nothing
says the departments are all there is and that between them is nothing, which is false about any
institution and specifically false about this one. Between the two rows of the pipeline there is a
service deck: a truss, a pipe run, pressure vessels and valve wheels. Down the side of the sealed
wing there is a trunk with flanges and a ladder. Between the sealed rooms there are short service
runs. Under it all is the plant hall — columns and cross-bracing, three tanks with their access
ladders, heat exchangers, a gantry rail with a trolley on it, and the cable trays that feed the
place. Every region is derived from where the rooms actually are, so moving a room moves the
building with it rather than leaving a gantry hanging over a floor.

None of the plant records anything, and none of it is a department. So it carries no number
anywhere, nothing on it can be clicked, and there is a test that says so.

#### What building it revealed was wrong

**1. A kit says what is in a room, not where.** The furniture tables named a wall row, a back row,
a middle row and a front row, which was a description of the old point of view baked into the
data. Turning the camera made three of those four meaningless — and `plotwall` sat in Design's
*middle* row, so with the wall rule applied only to the back row it ended up standing on the
floor, which is a board leaning against a wall and a different claim about the room. Anything that
belongs on a wall is now taken out of whichever row named it, wherever the kit happened to put it.

**2. The test that pinned the cap was pinning the point of view.** M30's `z` was the height of a
thing's front face and its complement was the top you looked down on. In elevation the same number
means the opposite thing: almost all of an object is its face, and the cap is the sliver you still
catch from a little above. The test asserting the ratio had to be rewritten, and it was right that
it failed — it was the only thing in the suite that knew which way the camera pointed.

**Acceptance**
- Every test from M22 through M30 still passes, one of them rewritten for the new camera.
- Everything in a room stands on its ground line or hangs above it, and stays inside its chamber.
  Tested.
- A room is drawn at three or more different heights, and every height is the one the table says.
  Tested.
- The people stand on the same floor as the furniture. Tested — there are no lanes any more.
- The plant fills the space between the rooms, never overlaps one, states no number and cannot be
  clicked. Tested.
- Still one self-contained file; still refuses to build on input that does not verify.

---

### M32 · An actor with somewhere to be ✅
Presentation only. `model.py`, `map.py` and `ledger.py` untouched.

**The people were a gif.** Each one traced a few pixels of path back and forth, forever, at the
same speed, whatever was happening. That is decoration, and decoration on a page whose whole claim
is that nothing on it is decorative. An actor now walks between the **stations of its own room** —
the room's own fixtures, chosen from its own stretch of floor — stops at each one, leans into the
work, and turns to face the way it is going. Where a stretch of floor has nothing to work at, the
actor paces it rather than miming at a spot where there is nothing: two stations a hand's width
apart is a figure shuffling on the spot, which reads as a broken animation rather than as somebody
busy, so below a minimum spread the route falls back to walking.

**And the animation now means something.** An arm that does not engage a room has nobody working
in it, so switching arms changes who is at work — the actors in the rooms that arm leaves out
stand at their posts. That is the same fact the route, the opacity and the dossier already report,
said a fourth way, and it is the difference between a map that moves and a map that is showing
you a system.

**Everybody says who they are.** A nameplate rides above each figure with the role's own name on
it, outside the group that mirrors, so it stays the right way round when its owner turns.

**The two thin places are filled.** Review and The Oracle had kits written for a layout with four
rows of depth and supplied too few things to stand on a floor; both gained furniture. The plant
hall had three tanks and two exchangers clustered to the left, so its right-hand third read as the
building running out — a row of nine switchgear and skid units now runs the length of its deck.

#### What building it revealed was wrong

**1. A negative delay and a frozen clock look exactly like a broken animation.** Sampling the
actor's transform in the browser returned the same number sixteen times over eleven seconds. The
keyframes were right, `getAnimations()` said `running`, and the value never moved — because the
pane was not compositing, so the document timeline was not advancing at all. Driving
`animation.currentTime` by hand proved the motion in one step. Worth recording: when an animation
looks stuck, check the clock before the keyframes.

**2. `tag` was already taken.** The nameplate went in as `class="tag"`, which is also the class on
the dossier's backing chip — so the count of nameplates on the page was one more than the number
of people in the building. The test that caught it was counting a class name rather than a thing,
which is exactly why it caught it.

**Acceptance**
- Every test from M22 through M31 still passes; three that read the old motion path were rewritten
  against what an actor does now.
- Every actor's stations are inside its own room, in order, and far enough apart that nobody
  shuffles on the spot. Tested.
- Two actors in a room never cross. Tested.
- At least two thirds of actors are stationed at a real fixture rather than pacing, and no actor is
  half at a station. Tested.
- An actor in a room this arm does not engage stands at its first station, with the animation off.
  Tested against both the stylesheet and the script.
- Every actor carries its own name. Tested.
- Still one self-contained file; still refuses to build on input that does not verify.

---

### M33 · The department, in plain words ✅
Presentation, plus one new module of prose. `model.py`, `map.py` and `ledger.py` untouched.

**Every dossier opened on a charter.** The charters are exact and they are written for somebody
who already knows what a preregistration is:

> Detectors and the Skeptic raise typed objections, each carrying the experiment that would tell
> it apart from the claim it disputes.

That is no use at all to a reader who has just clicked on a room. Every department now opens on a
**brief**: what the room is for in one sentence, what happens in it as a numbered list, who works
there with a job title an outsider would recognise, what it is doing on the arm on display, what
it has recorded, and what has not happened yet. The exact wording is one click away under *show
the exact rule*, because plain language is a summary and a summary loses things.

**And every department got the tab for the thing it, and no other, does.** What gets in (Design),
what happens to the losers (Screening), the lock (Registry), the bundle (Workshop), the sandbox
(Experiment Floor), who writes the numbers (Analysis), objections (Challenge), blindness (Blind
Testing), why it is dark (Review), the four doors (Records), custody (the Vault), the money
(Resource), what is remembered (Archive), ground truth (the Oracle). Fourteen departments, fourteen
tabs, none of them a house style applied fourteen times.

**The prose is the page's one exception, and it is fenced.** `station/brief.py` is hand-written
and says so at the top. The rule that keeps the exception safe is that **it may not contain a
number** — not one, anywhere, checked by a function in the module and by the test that already
guards every other piece of hand-written prose on the page. Every quantity on a brief is filled in
by the page from the record. The file can be wrong about what the institution is *for*, which is
a thing a person wrote down and can be argued with; it cannot be wrong about what the institution
*did*.

#### What building it revealed was wrong

**1. The station's own tests had become the slowest thing in the suite, by an order of
magnitude.** Adding six tests took `tests/test_station.py` from seventy seconds to sixteen
minutes, which is not a thing six tests can do. `--durations` put thirty-one seconds against
*setup* on twenty-one different tests: each one asked for a `tmp_path` and wrote a 1.6MB page into
it, and the cost was in the fixture rather than in anything being tested. The page is
deterministic — there is a test at the top of the file that asserts exactly that — so it is now
built once and cached. **Eighty-three tests in thirty-four seconds**, which is faster than the
suite was before this milestone with six fewer tests in it. Worth recording because the symptom
pointed at the wrong place entirely: the new tests looked like the cause and were merely the straw.

**2. `open()` set the tab twice.** The dossier kept landing on the old first tab however the brief
was wired, because a line further down `open()` reset `tab = 'overview'` unconditionally — a line
written when there was only ever one first tab. Two statements setting the same variable four
lines apart, the second one silently winning.

**Acceptance**
- Every test from M22 through M32 still passes.
- Every room has a brief and every role has a plain-language description. Tested.
- No piece of that prose states a figure. Tested twice — by the module's own check and by the
  suite's existing one-place guard, which the brief now joins.
- Every department has a tab no other department has, present in the strip and in the panel, with
  at least two sections. Tested.
- A dossier opens on the brief, and the exact rule is one click away in every one of them. Tested.
- The brief's numbers come from the arm's own record rather than from the prose. Tested.
- Still one self-contained file; still refuses to build on input that does not verify.

---

### M34 · The room you arrive in, and fifteen sheets that are not the same sheet ✅
Presentation, plus one room on the plan. `model.py` and `ledger.py` untouched; `map.py` gained a
room and the coordinates of the others moved around it.

**The station had no front door.** Opening it gave you fourteen departments and a pipeline to
guess your way along, and the first thing anybody wants — *what is this, who is in it, what has it
done* — was not anywhere. There is now a **Control Room**: the first room, the largest thing on the
plan by seven times, sitting across the middle with corridors up to the four departments above it,
down to the four below, east to the pipeline's turn, and on to the spine that serves the sealed
wing. Its brief is the only one whose subject is the project rather than a department. It counts
the institution at a glance — departments, kinds of actor, arms run, recorded passes, rooms that do
not exist yet — carries the arm's whole scored result, and ends in a grid of fourteen buttons that
take you into any of the others.

**No new role was invented for it.** The rule this project runs on is that departments, roles and
states come out of `db/enums.py` and are derived rather than declared, and a room needs a reason to
have somebody in it. The Control Room is staffed by the control plane, because the control plane is
the thing that wrote every event the room reports. It owns no state of the research machine, it is
on no route, and nothing passes through it — it is a view, and the map says so by leaving it off
the corridor.

**And every dossier was the same dossier.** One stylesheet applied fourteen times meant reading the
title to know where you were. A department's sheet is now set in its own **face**, its own **hue**
and one of five **frames**: a reading room lays its sections out in a single generous column, a
board sets them abreast under coloured rules, a bay boxes each one like something bolted to a wall,
a ledger runs a left column of headings against a right column of entries, and a console puts them
on a dark bank of readouts. The Registry is monospaced because it deals in hashes; Records is
slab-serif because it is a ledger; the Design Room is a serif reading room; the Workshop and the
Challenge Chamber are condensed and boxed. Every face is a stack of things already on the machine,
because the page still has to work with the network unplugged.

**Each department also gained a second tab of its own**, and the hub four. Thirty-two tabs across
fifteen rooms, written out of the same data the sections are so that adding one cannot leave it out
of the strip.

#### What building it revealed was wrong

**1. A room that big has nowhere to hang its label.** The callout placer searches four sides, three
distances and nine slides, filters to what is inside the world, and takes the least-bad slot if
nothing is free. The Control Room is a hundred and twenty units wide across the middle of the plan:
above it is a row of departments, below it is another, and the margin to its left was too narrow to
hold a card. So it took the least-bad slot, which was on top of another room — and the test written
in M25 for exactly that failure caught it immediately. The margin is wider now. Worth recording
because the placer did not fail; it did what it was told, and what it was told had stopped being
possible.

**2. Giving the hub a good kit took the Analysis Room's identity.** Every department is tested to
own at least one kind of object nobody else has. The Control Room wanted screens and plot walls and
consoles — and the moment it had a screen, Analysis had nothing of its own, because a screen was
the only thing that had been Analysis's alone. The hub got a signature instead: a wall-spanning
status board, which is a better thing for it to have anyway. A test that only checks uniqueness
tells you something has been taken; it does not tell you what to do about it, and the right answer
was not to give the hub less.

**Acceptance**
- Every test from M22 through M33 still passes.
- The hub is first, is the largest room, owns no state, and is on no route. Tested.
- It has a corridor to every room that can be walked to and none into the two that cannot. Tested.
- At least five faces and four frames are in use, and no face carries more than four departments.
  Tested against the stylesheet.
- Every department has at least two tabs of its own, all of them in the strip and in the panel.
  Tested.
- The hub's figures are counted across the map and the record, and it points at the other fourteen.
  Tested.
- Still one self-contained file; still refuses to build on input that does not verify.

---

### M35 · The facility does something, and says what it is doing ✅
Presentation only. `model.py`, `map.py` and `ledger.py` untouched.

Worked from a list of outside suggestions, kept the ones that were true and refused the one that
was not.

**Every room now says what it is doing, on its own wall.** The map has always known — it was on
the callout card, which M29 took off along with all the other writing, and a facility whose only
sign of trouble is a card you have to switch on is a facility that always looks like it is going
well. Fourteen plates, each with a lamp coloured by what is behind the room and a word from the
same call the dossier uses: `WORKING`, `NO DATA`, `IDLE`, `LOCKED`, `SEALED`. Switching arms moves
every one of them. The Review Room reads `LOCKED` in red from across the floor, which is the
honest state of this project and is now visible without opening anything.

**The Control Room shows the walk it reports on.** It described a pipeline and drew none. There is
now a strip of ten numbered blocks on its wall, in the order a hypothesis meets them, each lit by
what that stage is doing on the arm on display and joined by the line the hypothesis follows.

**And the building moves.** Not more decoration — the same building, running. A car goes up and
down each shaft, packets travel every corridor, and the screens change what they are showing.
Everything new stops for the pause button and for a reader whose machine has asked for less motion,
which is now tested rather than remembered.

**Actors wear their room's number.** `05 BUILDER` rather than `BUILDER`, so an actor and its
department are one fact seen twice instead of two things to hold in your head.

#### What was refused, and why

A mission panel — *MISSION 042, discover robust predictors of X, 78% complete, 12 active, 3 review,
2 failed*. It would look superb and every number in it would be invented. This station shows a
finished, recorded run; there is no mission in progress, no percentage of anything, and no queue.
The rule the whole page is built on is that no number reaches it except by being read from a
verified artifact, and a progress bar is a number. What the idea was really asking for — *the
building should have a visible current purpose* — is answered by the hub's own brief, which says
which arm is on display and what it actually scored.

#### What building it revealed was wrong

**1. A patch script that asserts its way down two files leaves the second one untouched.** The
last substitution in the run targeted `station.html` and the text it wanted was in `agents.html`,
so it raised — and because the write is at the end, everything the script had already done to
`station.html` in memory went with it, while `parts.html`, written earlier in the same run, had
landed. Half the milestone appeared to apply. The same failure was recorded in M27 and it caught
me again, in the same shape, for the same reason: the write is at the end and the assertion is not.

**2. The pipeline strip was drawn behind the furniture it describes.** Placed at a fraction of the
chamber that looked like the middle of the wall, it landed on the tops of the consoles standing on
the floor. In elevation there is exactly one free band — between the boards fixed to the wall and
the tops of the things standing under them — and the test now asserts the strip is in it rather
than trusting the fraction.

**Acceptance**
- Every test from M22 through M34 still passes.
- Every room carries a plate inside its own chamber whose word comes from the same call the
  dossier uses, and the set of words on the map is not uniformly cheerful. Tested.
- The hub's strip is the walk, in order, numbered as the rooms are, and sits clear of both the
  wall boards and the furniture. Tested.
- Every actor wears its room's number. Tested.
- The lift, the packets and the screens all stop for the pause button and for reduced motion.
  Tested against the stylesheet.
- Still one self-contained file; still refuses to build on input that does not verify.

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
