# 06 — MVP, Roadmap, Complexity, First Experiment

## 1. The MVP

**Definition.** A single-lab, CPU-only, fully offline pipeline that carries one tabular-ML research question from hypothesis to a provenance-backed claim, scored against planted ground truth, including a null arm.

Included:

- Postgres schema with the append-only ledger and the promotion invariants (`03-data-model.md`).
- Agent runtime: role contracts, typed views, structured output, validators, cost accounting, LLM cache.
- Roles: Theorist, Designer, **Builder-as-compiler**, Analyst, Skeptic, Replicator, Reviewer, and a rule-based Director.
- Registry (preregistration hashes) and Forecast Ledger.
- SCM data generator + a 15-item question bank (half null).
- Docker sandbox with the full hardening set; Holdout Custodian with query budgets.
- Analysis harness: seed variance, paired bootstrap CIs, effect sizes, Holm/BH correction.
- Static HTML report generator + `rich` CLI.
- Benchmark harness running arms B1 and B6.

Explicitly excluded from the MVP: free-form code generation, live literature retrieval, the web dashboard, paper generation, the research economy's optimiser (a fixed rule stands in), self-improving policies, multiple programs, multiple labs.

**The critical MVP decision: Builder-as-compiler, not Builder-as-codegen.**

The Designer emits a typed `ExperimentSpec`. A human-written, unit-tested harness compiles that spec into a run. No LLM writes executable code in the MVP.

This is the highest-leverage scoping choice available, and it is contrarian relative to the spec's §9. It buys:

- end-to-end working science in weeks rather than months;
- near-zero silent-invalidity risk while the *institution* is being debugged;
- a clean control condition — when codegen arrives at Stage 2, its contribution is measurable against a working baseline instead of confounded with everything else.

The cost is expressiveness: the space of expressible experiments is whatever the spec language covers. For the first domain that is a genuine constraint but not a crippling one, and it forces the useful discipline of designing a real experiment DSL rather than hoping an LLM improvises one correctly every time.

**MVP acceptance criteria** (each objectively checkable):

1. Every result row's registration hash predates its run's `started_at`.
2. On the 15-item bank, verdict accuracy > B1 baseline, with null accuracy ≥ 0.8.
3. At least one item is correctly terminated as `refuted` and at least one as `no_effect`.
4. Every `institutional` claim's evidence chain fully resolves to hashed artifacts.
5. An independent replication runs from the registration alone and its RLS role can be shown, by audit log, never to have read the original results.
6. A full program replays from the event log + LLM cache and reproduces identical claims.
7. Total cost per program is measured and reported in USD.

## 2. Phased roadmap

| Stage | Deliverable | Gate to proceed |
|---|---|---|
| **0. Foundations** (1 wk) | WSL2 + Docker, `git init`, Postgres via compose, Alembic, CI, price table, CAS | Container runs a hello-world experiment with all hardening flags and no network |
| **1. Research kernel** (3 wk) | Schema, ledger, runtime, role contracts, Registry, cost ledger, Theorist + Designer + compiler + Analyst | One hypothesis goes hypothesis → prereg → run → analysed claim with full provenance |
| **2. Ground truth** (2 wk) | SCM generator, 15-item bank, Holdout Custodian, verdict/calibration scoring, B1 baseline | Bank runs end to end; B1 vs kernel numbers exist |
| **3. Adversarial layer** (3 wk) | Skeptic + detector suite, typed objections with discriminating tests, Replicator with RLS isolation, Reviewer, defect injector | Skeptic recall on injected defects measured and > 0.5 |
| **4. Institutional memory** (2 wk) | Genealogy CTEs, follow-up generation from terminal states, institutional novelty dedup, cross-item memory | A program's second-generation hypotheses demonstrably derive from first-generation results |
| **5. Research economy** (2 wk) | Forecast-derived EIG, allocation policy interface + 4 implementations, hierarchical budgets, multi-program allocation | Greedy-EIG measurably beats random allocation on cost-per-correct-claim, or is shown not to |
| **6. Observability** (2 wk) | FastAPI + HTMX dashboard: overview, hypothesis explorer, run monitor, genealogy graph, agent timeline, claim view | A person can answer "why does the system believe C-014?" in three clicks |
| **7. Codegen** (3 wk) | Restricted op registry → constrained codegen → free-form with validator gate; measured against the compiler baseline | Codegen arm's validity rate within tolerance of the compiler arm |
| **8. Literature** (2 wk) | Vendored corpus, provenance verifier, source resolution; live retrieval behind a human gate | Zero unverified sources reach any claim |
| **9. Self-improvement** (2 wk) | Versioned policies, A/B against the held-out bank, promotion requires a passed A/B | A policy change is adopted through the full evidentiary process, logged as a decision |
| **10. Paper generation** (1 wk) | Template renderer; numerals prohibited in LLM slots | A generated report's every number traces to a `run_result` row |
| **11. Multi-lab** (4 wk+) | Lab policies, cross-lab replication and objection, shared claim space | Only after the single lab has a stable measured track record |

Roughly 5–6 months of focused single-engineer work to Stage 6; Stages 0–3 (~9 weeks) already produce something scientifically defensible and worth writing up.

## 3. Engineering complexity estimates

Scale: 1 = a day, 5 = a month-plus of genuinely hard work. Assumes an experienced engineer using Claude Code.

| Subsystem | Complexity | Risk | Notes |
|---|---|---|---|
| DB schema + migrations | 2 | Low | Volume is small; the constraints are the work |
| Event ledger + projections | 2 | Low | Well-trodden pattern |
| Agent runtime (contracts, views, validation) | 3 | Medium | ~1500 lines; the value is in the discipline it enforces |
| LLM provider + cache | 2 | Low | High payoff per line |
| Preregistration Registry | 2 | Low | Small code, large consequence |
| **SCM generator + question bank** | **4** | **High** | Getting the DGPs to be non-trivial, realistic, and correctly labelled with truth is genuinely hard research design work. Underestimate this at your peril |
| ExperimentSpec DSL + compiler | 3 | Medium | Expressiveness/safety trade-off |
| Design linter | 3 | Medium | Power analysis, confound enumeration, split validation |
| Docker sandbox + runner | 3 | Medium | Fiddly, especially on Windows/WSL2 |
| **Holdout Custodian** | 3 | High | Simple code, but every leak path must be closed; a single mistake invalidates everything downstream |
| Analysis harness (stats) | 3 | Medium | Correctness matters more than breadth |
| **Leak/defect detector suite** | **4** | **High** | Open-ended; will never be complete; the benchmark measures the gap |
| Defect injector | 2 | Low | High value per unit effort |
| Skeptic role + objection typing | 3 | High | Prompt/contract design is empirical; expect iteration |
| Replicator isolation (RLS, roles) | 3 | Medium | Postgres RLS + an audit test proving blindness |
| Reviewer role | 2 | Medium | Mostly schema |
| Forecast Ledger + scoring | 2 | Low | Small, elegant, disproportionately useful |
| EIG + allocation policies | 4 | Medium | Easy to do badly, interesting to do well |
| Genealogy + novelty dedup | 2 | Low | Recursive CTEs + pgvector |
| Report generator (static) | 2 | Low | Jinja over the read models |
| Dashboard (FastAPI + HTMX) | 3 | Low | Effort, not difficulty |
| Benchmark harness + baseline arms | 3 | Medium | Must be genuinely matched across arms |
| Codegen path | 4 | High | Deferred for good reason |
| Literature + provenance verifier | 3 | High | Hallucination surface |
| Self-improving policies | 4 | High | Easy to make unfalsifiable |
| Multi-lab | 4 | Medium | Mostly a repeat of solved problems |

## 4. Delegation: Claude Code vs. human judgement

**Delegate freely** (mechanical, verifiable, high volume):

- Schema DDL, Alembic migrations, Pydantic models from the spec in `03-data-model.md`.
- The agent runtime plumbing, provider abstraction, cache, cost accounting.
- Docker/compose files and the container runner.
- The statistics harness against known-answer fixtures.
- Static analysis and leak detectors (each one is a well-specified predicate with tests).
- Jinja report templates, dashboard views, CLI.
- Test suites, especially property tests over the invariants ("no run without a prior registration" is a lovely property test).
- Refactors, migrations, documentation upkeep.

**Human decides, Claude implements:**

- The DGP design and the bank composition. This *is* the science; delegating it means an LLM authoring the ground truth its own evaluation depends on. Author the SCMs yourself; have Claude implement and test them.
- Role contracts: what each role sees, what it may not see, and how it is scored.
- The promotion rule and confidence rubric.
- Which invariants are constraints versus conventions.
- Benchmark protocol and preregistration of the project's own claims.
- Anything touching the Custodian or the sandbox policy.
- Interpreting results and deciding what the project claims.

**Never delegate:**

- Deciding whether a result is real.
- Loosening a security or isolation control to make something work.
- Writing the ground truth used to score the system.

A useful heuristic: Claude Code should build the instrument; a human decides what the instrument measures and whether to believe the reading.

## 5. Repository structure

Refined from spec §30 — flatter, with the boundaries that matter made explicit.

```text
<project>/
├── pyproject.toml            uv/poetry, locked
├── docker/
│   ├── experiment.Dockerfile   pinned deps, no network at runtime
│   └── compose.yml             postgres + orchestrator
├── docs/                       these design documents
├── src/arc/                    (package name follows the chosen project name)
│   ├── ledger/                 events, projections, hash chain
│   ├── models/                 pydantic entities == DB schema
│   ├── runtime/                task queue, workers, role contracts, views
│   ├── llm/                    provider abstraction + cache + cost
│   ├── roles/                  one module per role: prompt, schema, validators, scoring
│   ├── registry/               preregistration, hashing, canonical JSON
│   ├── forecast/               elicitation, scoring, EIG
│   ├── design/                 ExperimentSpec DSL, linter, power analysis
│   ├── build/                  spec compiler (MVP) | codegen (Stage 7)
│   ├── validate/               static analysis, leak detectors, defect injector
│   ├── execute/                container runner, telemetry, artifact harvest
│   ├── custody/                Holdout Custodian (separate process, own mounts)
│   ├── analysis/               statistics harness
│   ├── knowledge/              claims, evidence, genealogy, novelty
│   ├── economy/                budgets, allocation policies
│   ├── policy/                 versioned policy records
│   └── report/                 templates, static site
├── bank/                       SCM definitions + question bank (ground truth; gitignored from agent views)
├── data/                       vendored datasets, hashed, read-only
├── objects/                    content-addressed artifact store
├── benchmarks/                 arms B0–B7, protocol, preregistered analysis
├── tests/
│   ├── unit/
│   ├── invariants/             property tests over the scientific invariants
│   └── isolation/              proves the sandbox and the Replicator blindness
└── scripts/
```

Two structural points: `custody/` is a separate process with its own filesystem view, and `bank/` holds ground truth that no agent view may ever join against.

## 6. Project names

| Name | Rationale |
|---|---|
| **Nullius** *(recommended)* | From the Royal Society's *nullius in verba* — "take nobody's word for it." It names the project's actual thesis: no assertion counts without evidence, including the machine's own. Package `nullius`, CLI `nullius run` |
| **Lakatos** | After research programmes and the progressive/degenerating distinction — precisely what the genealogy view measures. Slightly insider-ish |
| **Cavendish** | Understated lab name; evokes careful measurement. Neutral, no thesis |
| **Refute** | Blunt and accurate: the system's job is to try to knock its own findings down. Good CLI verb, weak as a noun |
| **Provenance Lab / PROVLAB** | Descriptive, dull, immediately legible to an engineer. The safe choice |

Check name collisions on PyPI, GitHub and trademarks before committing.

## 7. The first reproducible experiment (spec §32)

### RQ-001

> Under label-preserving covariate shift in tabular classification, does divergence-based feature pruning — dropping features whose train/deployment marginal distributions diverge most — improve out-of-distribution macro-F1 relative to training on all features; and does the effect depend on whether the shifted features are causally relevant to the label?

Chosen because it is genuinely non-obvious, has a **known correct answer that is conditional rather than binary**, contains a natural confound the Skeptic should catch, includes a clean null arm, and runs in seconds on a CPU — which makes 10 seeds × 5 configurations × 3 arms × 2 model families affordable many times over.

### Data (SCM, ours by construction)

```text
Causal:   X_c1..X_c4      Y = σ(β·g(X_c)) ;  P(Y|X_c) invariant across environments
Spurious: X_s1..X_s3      generated FROM Y ; strength changes between environments
Noise:    X_n1..X_n5      independent of Y in both environments

Environments: train ~ E0, deployment ~ E1
Configurations (the moderator axis):
  C1  only spurious features shift          → true effect: pruning HELPS  (+)
  C2  only causal features shift            → true effect: pruning HURTS  (−)
  C3  both shift, spurious more             → small positive
  C4  both shift, causal more               → small negative
  C5  only noise features shift             → TRUE EFFECT = 0   (null arm)
```

Ground truth per configuration is computed offline from the DGP by exhaustive evaluation on a large population sample, and stored where no agent view can reach it.

### Arms

```text
A_full     train on all features
A_prune    drop top-k features by train/deployment marginal divergence
           (unlabeled deployment covariates only — declared and enforced)
A_random   drop k features at random          ← capacity-matched control
```

`A_random` is deliberately **omitted from the first registration**, and its absence is a *planted design defect*. Whether the Skeptic demands a capacity-matched control before the claim is promoted is the first real test of the adversarial layer. If it does not, that is a finding about the Skeptic, recorded as such.

### Preregistered protocol

```text
Primary metric        macro-F1 on the deployment split (Custodian-held)
Primary comparison    A_prune vs A_full at C1
MDE                   2.0 points macro-F1
Direction             increase
Seeds                 10, derived from seed_root; ALL reported
Models                logistic regression + gradient boosting (generalisation check)
Tuning                identical budget per arm on a dev split; no holdout tuning
Statistics            paired bootstrap over seeds (10k resamples), BCa CIs;
                      Holm correction across the 5 configurations
Holdout query budget  3 per registration
Stopping rule         fixed design; no interim looks
Falsification         H refuted if the 95% CI for the C1 paired difference
                      excludes +2.0 points, or includes 0 with an upper bound < +2.0
Null check            C5 must show a CI containing 0 and excluding ±2.0;
                      a "significant" C5 result invalidates the whole run
Secondary             in-distribution macro-F1, ECE, features retained, fit time
```

The C5 null arm doubles as an **internal validity check on the pipeline itself**: a positive result where the truth is exactly zero means something is broken, and the correct response is to halt the program rather than to explain the finding.

### What the run should produce

1. Theorist emits H-001 plus at least one child hypothesis about the causal/spurious moderator.
2. Forecasts locked from every role before execution; scored afterwards.
3. Registration hash written and timestamped before any container starts.
4. 300 runs (3 arms × 5 configs × 10 seeds × 2 models), each with its own environment hash.
5. Analyst produces computed statistics plus an interpretation that does not overstate C3/C4.
6. Skeptic raises the capacity confound (the planted defect), typed `confound`, severity `critical`, with `A_random` as its discriminating test; a follow-up registration runs it.
7. Replicator, given only the registration, rebuilds and reruns on a fresh SCM resample.
8. Reviewer promotes a **conditional** claim, not a general one.
9. Final institutional claim, expected to be something like: *"Divergence-based pruning improves OOD macro-F1 only when the shifted features are non-causal (C1: +X ± Y, replicated); it degrades performance when causal features shift (C2: −A ± B); no effect when only noise features shift (C5)."*
10. Follow-up hypotheses emitted from the terminal states — e.g. that a causal-relevance-weighted divergence criterion dominates raw divergence — feeding the next generation.

### Demo success criteria (about the institution, not the ML)

The demo succeeds if: the verdict matches planted truth on all five configurations including the null; the Skeptic catches the planted capacity confound before promotion; the replication concords; every number in the report traces to a `run_result` row; the whole program replays deterministically from a clean clone; and the total cost is reported in dollars.

The demo **fails** — informatively — if it produces a confident general claim ("pruning improves robustness") without the moderator. That failure would be worth reporting as loudly as a success, and the architecture is built so that it cannot be quietly avoided.

## 8. What the full civilization looks like (spec §20 of the task list)

Once Stages 0–11 are complete: several artificial laboratories with different constitutions — high-novelty risk-takers, conservative replicators, theory-heavy, experiment-heavy — running concurrent research programs against a shared, evidence-typed claim space. Labs read each other's preregistrations, issue independent replications, file typed objections, and compete for a finite budget allocated on measured track record: verdict accuracy, calibration, replication success, cost per correct claim. Research policies evolve through versioned A/B tests against a held-out question bank, with every policy change logged as a decision with evidence. Failed programs and refuted claims persist in the genealogy as first-class objects, and the institution's own history becomes a dataset it can study.

And the whole thing remains judged, as §35 insists, not by the fluency of what it writes, but by whether its high-confidence claims survive independent verification against ground truth it never saw.
