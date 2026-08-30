# 02 — System Architecture

## 1. Architectural stance

Three commitments determine everything below.

**Blackboard, not conversation.** Agents never message each other. Each agent invocation is a pure-ish function: read a *typed, role-filtered view* of institutional state → emit a *schema-validated artifact* → the runtime commits it as an event. There is no dialogue, no turn-taking, no "agents discussing". This eliminates the dominant failure mode of multi-agent LLM systems (unbounded conversational drift and cost) and gives replay, audit and provenance as by-products.

**Event-sourced state.** The database is an append-only `event` ledger plus derived read-model tables maintained by projections. "Why does the institution believe X" is answerable by construction: fold the events. Nothing is updated in place; nothing is deleted.

**Deterministic replay.** Every LLM call goes through a cache keyed by `sha256(provider, model, params, prompt, tool_schemas)`. A replay of a program hits cache and reproduces exactly. This is simultaneously the cost-control mechanism, the reproducibility mechanism, and the enabler for cheap counterfactual ablations of institutional structure.

### 1.1 Revisions to the spec's architecture (§3)

| Spec element | Change | Why |
|---|---|---|
| Agents as boxes with arrows between them | Agents as workers pulling typed tasks from a queue; all coupling through the blackboard | Arrows between agents become conversations; conversations become cost and drift |
| Literature Researcher in the main loop | Moved off the critical path; runs against a vendored corpus, output is *advisory* and gated by a provenance verifier | Largest hallucination surface, lowest MVP value |
| Sandbox Executor as a step | Split into **Builder → Validator → Executor → Custodian** | Validation must be a separate gate; the Custodian must be the only holder of the test split |
| "Peer Review" as a single stage | Reviewer is a *state-transition gate* with typed inputs, not a prose critic | Prose review is unscoreable |
| Knowledge Graph at the end | Knowledge graph is *the substrate*, written to at every step | A graph populated at the end is a summary, not a memory |
| — | **New: Registry** (preregistration ledger) | Makes non-HARKing an invariant |
| — | **New: Forecast Ledger** | Calibration, EIG, and a self-improvement signal |
| — | **New: Defect Injector** (eval harness only) | Calibrates the adversarial layer |
| — | **New: Cost Ledger** | Real economy, single source of truth |

### 1.2 Component diagram

```text
                     ┌──────────────────────────────────────────────┐
                     │        CONTROL PLANE (deterministic)         │
                     │                                              │
 research question → │  Director-Policy  ──► Task Queue ──► Runtime  │
                     │        ▲                              │      │
                     │        │                              ▼      │
                     │   Cost Ledger                   Agent Workers │
                     │   Forecast Ledger                (LLM calls)  │
                     │   Registry (prereg)                   │       │
                     └───────────────────┬───────────────────┼───────┘
                                         │                   │
                     ┌───────────────────▼───────────────────▼───────┐
                     │      BLACKBOARD  (Postgres, event-sourced)    │
                     │  events │ hypotheses │ registrations │ claims │
                     │  evidence │ objections │ reviews │ artifacts  │
                     └───────────────────┬───────────────────────────┘
                                         │
        ┌────────────────────────────────┼────────────────────────────────┐
        ▼                                ▼                                ▼
┌───────────────┐              ┌───────────────────┐            ┌──────────────────┐
│  EXECUTION    │              │  HOLDOUT          │            │  READ MODELS     │
│  PLANE        │              │  CUSTODIAN        │            │                  │
│               │              │                   │            │  genealogy view  │
│ Validator     │              │ owns test splits  │            │ static HTML rep. │
│ Container     │─artifact────►│ runs prereg eval  │            │ CLI / dashboard  │
│ runner        │              │ query budget      │            │                  │
│ no network    │◄──metrics────│ returns metrics   │            └──────────────────┘
│ ro rootfs     │              └───────────────────┘
└───────────────┘
        ▲
        │ mounts (read-only, hashed)
┌───────────────┐
│ DATA PLANE    │  vendored datasets · SCM generators · literature corpus
└───────────────┘
```

Agent roles from spec §4–13 all live in "Agent Workers". They are distinguished by **role contract**: system prompt + model + tool ACL + input view + output schema + scoring rule. Not by name.

## 2. Agent interfaces and communication protocol

### 2.1 The contract

```python
class RoleContract(BaseModel):
    role: Role  # DIRECTOR | THEORIST | LITERATURE | SKEPTIC | ...
    version: str  # contracts are versioned; results record which was used
    model: ModelRef  # provider + model id + params
    input_view: ViewSpec  # named, parameterised SQL view — the ONLY state it sees
    tools: frozenset[ToolId]  # allowlist; runtime rejects anything else
    output_schema: type[BaseModel]  # structured output, validated before commit
    validators: list[ValidatorId]  # code checks run on the output before commit
    max_tokens_per_call: int
    max_calls_per_task: int
    scoring: ScoringRule | None  # how this role's track record is measured
```

### 2.2 Invocation

```python
class AgentTask(BaseModel):
    task_id: UUID
    role: Role
    program_id: UUID
    subject_ref: EntityRef  # hypothesis / experiment / claim under consideration
    view_snapshot_id: UUID  # immutable snapshot of the input view
    budget: CostAllowance
    deadline: datetime


class AgentResult(BaseModel):
    task_id: UUID
    contract_version: str
    payload: BaseModel  # instance of contract.output_schema
    cost: CostRecord  # tokens in/out, cached?, wall time, USD
    llm_calls: list[LlmCallRef]  # each references a cache key — full audit trail
    status: OK | REFUSED | INVALID | BUDGET_EXCEEDED | TIMEOUT
```

Rules the runtime enforces, not the prompt:

1. An agent sees **only** `view_snapshot_id`. Views are the information-asymmetry mechanism (§A5, F9).
2. Output failing `output_schema` or any validator is rejected. One structured repair attempt, then the task fails and the failure is recorded as an event — it is not retried into success.
3. Every result is committed as an immutable event with its cost and its LLM cache keys.
4. Agents cannot write to the blackboard directly. Only the runtime writes, and only after validation.
5. Agents cannot call other agents. To involve another role, an agent *emits a request artifact*; the Director policy decides whether it becomes a task.

### 2.3 Role summary

| Role | Sees | Tools | Emits | Scored on |
|---|---|---|---|---|
| **Director** | program state, budget, forecast summary, open objections | none (pure reasoning) + policy calc | `AllocationDecision` | Program-level: correct claims per unit cost |
| **Theorist** | RQ, knowledge graph subgraph, prior results (aggregated, not raw) | novelty search | `HypothesisDraft[]` | Falsifiability pass rate; downstream confirmation rate; novelty |
| **Literature** | RQ, vendored corpus | corpus search (offline) | `SourcedClaim[]` | Provenance-verification pass rate |
| **Designer** | hypothesis + registration template + design linter feedback | power calc, design linter | `ExperimentSpec` | Linter pass rate; replication rate of designs it produced |
| **Builder** | `ExperimentSpec` only (**not** the hypothesis rationale) | codegen, unit-test runner | `ExperimentBundle` | Validator pass rate; runtime failure rate |
| **Analyst** | raw result artifacts + registration | stats library (executed, not narrated) | `AnalysisReport` | Agreement with an independent re-analysis; calibration |
| **Skeptic** | code, raw artifacts, registration, **not** the Analyst's narrative | leak detector, seed-variance rerun, shuffle test, ablation request | `Objection[]` | **Recall/precision on injected defects** |
| **Replicator** | registration only (RLS-enforced) | full build+run | `ReplicationOutcome` | Independence audit; agreement with truth on planted questions |
| **Reviewer** | structured evidence bundle | none | `ReviewDecision` | Accept/reject accuracy vs. planted ground truth |

Note two deliberate asymmetries: the **Builder is blind to the hypothesis's motivation** (it implements a spec, so it cannot unconsciously bias toward the desired outcome), and the **Skeptic never reads the Analyst's prose** (so it attacks the artifacts, not the narrative).

## 3. Research state machine

Hypothesis lifecycle. Transitions are performed only by the named actor, and each is an event.

```text
                  ┌──────────┐
                  │  DRAFT   │  Theorist
                  └────┬─────┘
       reject ◄────────┤ novelty + falsifiability validators
                       ▼
                  ┌──────────┐
                  │ SCREENED │
                  └────┬─────┘
                       │ Director: funded? (EIG / cost)
        ┌──────────────┼──────────────┐
        ▼              ▼              │
   ┌─────────┐   ┌──────────┐         │
   │ SHELVED │   │ REGISTERED│ ◄──────┘  Registry writes prereg hash
   └─────────┘   └────┬─────┘            (IRREVERSIBLE; forecasts elicited here)
                      ▼
                 ┌──────────┐
                 │  BUILT   │  Builder → Validator gate
                 └────┬─────┘
                      ▼
                 ┌──────────┐   infra failure ──► retry (≤N, logged)
                 │ EXECUTED │   scientific failure ──► OBSERVED_FAILURE
                 └────┬─────┘
                      ▼
                 ┌──────────┐
                 │ ANALYZED │  Analyst (stats computed by code)
                 └────┬─────┘
                      ▼
                 ┌──────────┐
                 │CHALLENGED│  Skeptic; critical objections gate forward
                 └────┬─────┘
                      ▼
                 ┌──────────┐
                 │REPLICATED│  Replicator (independent) — required for institutional
                 └────┬─────┘
                      ▼
                 ┌──────────┐
                 │ REVIEWED │  Reviewer decision
                 └────┬─────┘
        ┌─────────────┼─────────────┬──────────────┐
        ▼             ▼             ▼              ▼
 ┌────────────┐ ┌──────────┐ ┌────────────┐ ┌──────────┐
 │INSTITUTIONAL│ │ REFUTED  │ │INCONCLUSIVE│ │ REVISED  │──► new DRAFT
 │   CLAIM     │ │          │ │            │ │ (child)  │
 └────────────┘ └──────────┘ └────────────┘ └──────────┘
```

Hard invariants:

- `REGISTERED` is irreversible for that registration. Changing anything creates a child registration typed `exploratory`.
- No transition to `INSTITUTIONAL` with an open `critical` objection, without ≥1 `REPLICATED` outcome from an independent role, or with any evidence row whose artifact hash is missing.
- `REFUTED` and `INCONCLUSIVE` are terminal *successes* of the process and are reported with equal prominence. The report generator fails loudly if any registration lacks a terminal state.
- Every terminal transition emits a `follow_up_opportunity` record; the Theorist's next cycle reads these. This is the generational loop of §13/§15.

## 4. Experiment lifecycle

```text
1. DESIGN        Designer emits ExperimentSpec (typed).
2. LINT          Design linter (code): baseline capacity matched? confounders
                 enumerated? grouped splits? power ≥ target for the stated MDE?
                 seeds ≥ policy minimum? primary metric single and pre-declared?
3. REGISTER      Canonical JSON → sha256 → registry row (append-only, timestamped).
                 Forecasts elicited from every role BEFORE execution and locked.
4. BUILD         MVP: spec compiled by human-written harness.
                 Stage 2+: Builder generates code from a restricted op registry.
5. VALIDATE      Static: import allowlist, no network calls, no file writes outside
                 workdir, no reads of holdout paths, AST checks for eval/exec.
                 Dynamic: unit tests, smoke run on a tiny synthetic fixture,
                 resource estimate within budget.
6. EXECUTE       Container: no network, read-only rootfs, tmpfs workdir, non-root,
                 CPU/mem/pids/time caps, dataset mounted read-only by hash.
                 Emits: fitted artifacts, predictions on dev split, logs, metrics
                 on TRAIN/DEV ONLY, resource telemetry, env manifest.
7. CUSTODY       Custodian loads the artifact, runs the PREREGISTERED evaluator
                 against the held-out split, returns metrics, decrements the
                 hypothesis's holdout query budget. Agents never touch this split.
8. ANALYZE       Analysis harness (code) computes seed variance, paired bootstrap
                 CIs, effect size, program-level multiple-comparison correction.
                 Analyst writes interpretation ONLY, referencing computed numbers.
9. CHALLENGE     Skeptic runs detectors + may request ≤K discriminating tests
                 (each becomes its own registered experiment).
10. REPLICATE    Replicator receives ONLY the registration; rebuilds and reruns
                 on a fresh SCM resample / fresh split.
11. REVIEW       Reviewer reads the structured evidence bundle; decision gates
                 the state transition.
12. INGEST       Claim + evidence links written; genealogy updated;
                 follow-up opportunities emitted; costs finalised.
```

Every step writes an event. Steps 5, 6, 7, 8 contain no LLM calls at all — a useful sanity check on the design: **the load-bearing parts of the science are code.**

## 5. Knowledge representation

Relational, in Postgres. The "graph" is a set of typed edge tables plus recursive CTEs; no graph database until a query provably needs one.

Node types: `ResearchQuestion`, `Program`, `Hypothesis`, `Registration`, `Experiment`, `Run`, `Result`, `Observation`, `Interpretation`, `Claim`, `Evidence`, `Objection`, `Replication`, `Review`, `Source`, `Dataset`, `Artifact`, `Policy`, `Forecast`, `Decision`, `Failure`.

The **epistemic type discipline** demanded by §23 is enforced as a Postgres enum on every assertion row, and cross-type promotion is illegal:

```text
OBSERVED_FACT     ← only written by the execution/custody plane from artifacts
SOURCED_CLAIM     ← only with a resolvable source_id + verbatim passage
INFERRED_CLAIM    ← only with ≥1 parent evidence row
HYPOTHESIS        ← agent-generated, never evidence for anything
SPECULATION       ← agent-generated, excluded from all reports and metrics
```

A row of kind `INFERRED_CLAIM` with zero parents fails a check constraint. A `Claim` cannot reference a `SPECULATION` as evidence. These are database constraints, not conventions.

Edges mirror the spec (§14) plus what the invariants need:

```text
HYPOTHESIS  --derived_from-->        HYPOTHESIS      (genealogy; recursive CTE)
HYPOTHESIS  --registered_as-->      REGISTRATION
REGISTRATION--executed_as-->        RUN
RUN         --produced-->           RESULT
RESULT      --supports/contradicts->CLAIM            (signed, with strength)
CLAIM       --supported_by-->       EVIDENCE
CLAIM       --challenged_by-->      OBJECTION
OBJECTION   --resolved_by-->        REGISTRATION     (the discriminating test)
REGISTRATION--replicated_by-->      REPLICATION
CLAIM       --reviewed_by-->        REVIEW
DECISION    --allocated_to-->       HYPOTHESIS
FORECAST    --about-->              REGISTRATION
POLICY      --governed-->           DECISION
```

**Confidence is computed, never asserted.** A pure function over evidence rows:

```text
confidence(claim) = f( n_independent_replications,
                       effect_size / CI_width,
                       seed_variance_ratio,
                       open_critical_objections,       # any → cap at "contested"
                       preregistered?,                 # exploratory → cap at "suggestive"
                       holdout_queries_consumed,       # more queries → discount
                       provenance_completeness )
→ {contested, speculative, suggestive, supported, well_supported}
```

Disagreement (§22) is represented, not resolved: each role's `Position` row persists with its rationale. `well_supported` with a standing Skeptic dissent is a legal, and interesting, state.

## 6. Research genealogy

Materialised from `hypothesis.parent_id` + `derivation_kind ∈ {specialisation, generalisation, refutation_response, merge, ablation, follow_up_from_failure}` via a recursive CTE. Rendered as a tree in the CLI and as an interactive graph in the Stage 6 dashboard. Each node carries its terminal status and confidence, so a glance shows which branches were productive — the Lakatosian progressive/degenerating distinction, made visible and, eventually, actionable by the Director's allocation policy.

## 7. Resource economy

One ledger, real units.

```python
class CostRecord(BaseModel):
    llm_input_tokens: int
    llm_output_tokens: int
    llm_cached_tokens: int
    cpu_seconds: float
    peak_memory_mb: int
    storage_mb: float
    usd_equivalent: Decimal  # priced from a versioned price table
```

Budgets are hierarchical and hard: `institution → program → hypothesis → task`. The runtime refuses to dispatch a task whose allowance exceeds the remaining parent budget. Exhaustion is an event, and a legitimate research outcome (`ABANDONED_BUDGET`), not an error.

### Allocation

The Director *proposes*; a policy function *decides*, from scored inputs:

```text
score(h) = EIG(h) × P_success(h) × strategic_weight(h) / expected_cost(h)
```

- `EIG(h)` — expected entropy reduction of the claim's posterior, computed from the **elicited forecast distribution** for the registration. Concretely: each role gives a predictive distribution over the primary metric's effect; EIG is the expected KL between prior and post-observation posterior, estimated by Monte Carlo over that predictive distribution. Cheap, principled, and it makes disagreement between roles automatically valuable (wide disagreement → high EIG).
- `P_success(h)` — from the roles' forecast of successful execution, calibrated by their historical Brier scores.
- `expected_cost(h)` — regression on historical cost of similar specs.
- Policy also enforces reserves: a fixed fraction of budget for replication and a fixed fraction for confirmatory null testing (mitigating F14).

**The allocation policy is a swappable, versioned object.** `RandomAllocation`, `RoundRobin`, `GreedyEIG`, `ThompsonSampling` are all implementations of one interface — which makes "does intelligent allocation help?" a measurable question rather than an assumption.

### Forecast Ledger

Before execution, every participating role emits a locked forecast (point + interval + P(effect exceeds the preregistered threshold)). After results, forecasts are scored with a proper scoring rule (Brier for the binary, CRPS for the continuous). This yields, for free: per-role calibration curves, the EIG inputs above, an agent-weighting signal, and one of the most interesting evaluation results the project can produce — *is the Skeptic actually better calibrated than the Theorist?*

## 8. Sandbox architecture

See `05-security.md` for the threat model. Structurally:

- **Two-tier isolation.** Tier 1 (MVP): Docker/Podman container, `--network=none`, read-only rootfs, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, non-root uid, seccomp default profile, tmpfs workdir with size cap, `--pids-limit`, `--cpus`, `--memory`, hard wall-clock kill. Tier 2 (if the project ever runs untrusted third-party code or exposes a service): gVisor or a microVM (Firecracker/Kata).
- **No network, ever.** Not "restricted" — absent. Dependencies come from a pre-built image with a pinned lockfile; datasets are mounted read-only by hash. This single decision removes exfiltration, dependency-confusion, and most prompt-injection consequences at once.
- **Holdout isolation.** The test split is not on any filesystem the experiment container can see. It exists only in the Custodian's mount namespace.
- **Artifact contract.** The container's only output channel is `/workdir/out`, from which the runner harvests a manifest, hashes every file, enforces a size cap, and rejects anything not declared in the spec.
- **Environment manifest.** Image digest, lockfile hash, CPU model, kernel, library versions, dataset hashes, code hash, seeds — hashed together into `environment_hash` stored with every run.

**Windows note.** This machine has no Docker and no git repo. Recommended setup before Stage 1: WSL2 + Docker Desktop (or Podman in WSL2), develop inside the WSL2 filesystem, and `git init` immediately — provenance depends on commit hashes existing from the first experiment.

## 9. Technology comparison

| Concern | Options | Recommendation | Reasoning |
|---|---|---|---|
| Language | Python / TS / Rust core | **Python 3.12** | The experiments are ML; a second language buys nothing here |
| State store | Postgres / SQLite / Postgres+Neo4j | **Postgres 16** (SQLite for local unit tests) | Needs transactions, RLS (replicator isolation), `jsonb`, recursive CTEs, `SKIP LOCKED` queueing. Neo4j adds an operational component for queries recursive CTEs already answer |
| Queue | Celery / RQ+Redis / **Postgres `SKIP LOCKED`** / Temporal | **Postgres `SKIP LOCKED`** for MVP; revisit Temporal at Stage 5 | One less service; the ledger and the queue share a transaction, which matters for exactly-once state transitions. Temporal is genuinely attractive for long-running durable workflows later |
| Agent framework | LangGraph / AutoGen / CrewAI / **none** | **None.** ~500 lines of runtime | These frameworks solve conversation orchestration and prompt plumbing. This project's hard problems are state, provenance and isolation, which they do not address and whose abstractions they obscure. Adopting one would also make the §27 ablation harder to control |
| LLM access | Provider SDKs behind a thin `LLMProvider` | **Anthropic SDK + a 100-line provider interface**, with a caching decorator | Model diversity across adversarial roles is a design requirement, so the abstraction is load-bearing — but keep it thin |
| Structured output | JSON mode / tool-use / Instructor / **Pydantic + native structured output** | **Pydantic v2 + native structured outputs**, retry-on-validation-failure once | Schema *is* the protocol |
| Sandbox | subprocess+rlimits / **Docker** / gVisor / Firecracker | **Docker/Podman** now, gVisor optional later | rlimits on generated code is not isolation; microVMs are overkill until there is a network boundary |
| ML libs | sklearn / PyTorch / both | **sklearn + numpy/scipy first**, PyTorch when a question needs it | The first domain (tabular, distribution shift) is entirely sklearn-shaped, runs on CPU, and finishes in seconds — which is what makes many-seed, many-replication designs affordable |
| Stats | scipy + statsmodels + custom bootstrap | **scipy/statsmodels**, plus a small vetted `paired_bootstrap` module | Never an LLM |
| Dashboard | Next.js / Streamlit / FastAPI+HTMX / **static HTML** | **Static HTML report generator** (MVP) → **FastAPI + HTMX** (Stage 6) → Next.js only if genuinely needed | The dashboard is a read model over an event log; a JS SPA is a large cost for a read model. HTMX over server-rendered Jinja covers the live views |
| Artifact storage | filesystem CAS / S3/MinIO | **Content-addressed filesystem** (`objects/<sha256[:2]>/<sha256>`), S3 interface later | CAS gives dedup and integrity for free |
| Config | YAML / TOML / **Pydantic Settings + versioned Policy rows** | Policy in the DB, infra config in TOML | Policies must be versioned and queryable, not files |
| Migrations | **Alembic** | Alembic | Schema will churn |

Two anti-recommendations worth stating explicitly: **do not adopt a multi-agent framework**, and **do not add a vector database in the MVP**. Embeddings for novelty dedup fit fine in `pgvector` on the existing Postgres.
