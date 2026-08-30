# 03 — Data Model

Postgres 16. Illustrative DDL — abbreviated, not final. The point is the **invariants**, which are expressed as constraints rather than left to agent behaviour.

## 1. Principles

1. **Append-only where truth lives.** `event`, `registration`, `run_result`, `forecast`, `objection`, `cost_entry` have no `UPDATE` or `DELETE` grants for any application role. Mutable read-model tables are derived and rebuildable.
2. **Content addressing.** Every artifact, dataset, code bundle and environment is identified by `sha256`. Provenance is then just foreign keys.
3. **Epistemic typing at the schema level.** `assertion_kind` cannot be silently promoted.
4. **Constraints over conventions.** If a scientific norm can be a `CHECK`, a trigger, or an RLS policy, it must be.

## 2. Core ledger

```sql
CREATE TYPE assertion_kind AS ENUM
  ('observed_fact','sourced_claim','inferred_claim','hypothesis','speculation');

CREATE TYPE role_t AS ENUM
  ('director','theorist','literature','designer','builder','analyst',
   'skeptic','replicator','reviewer','system');

-- Append-only spine. All state is a fold over this.
CREATE TABLE event (
  event_id      BIGSERIAL PRIMARY KEY,
  occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  program_id    UUID NOT NULL REFERENCES program(program_id),
  actor_role    role_t NOT NULL,
  actor_task_id UUID,                       -- NULL for system events
  event_type    TEXT NOT NULL,
  subject_type  TEXT NOT NULL,
  subject_id    UUID NOT NULL,
  payload       JSONB NOT NULL,
  payload_hash  BYTEA NOT NULL,             -- sha256 of canonical payload
  prev_hash     BYTEA,                      -- hash chain: tamper-evident ledger
  policy_id     UUID REFERENCES policy(policy_id)
);
CREATE INDEX ON event (program_id, event_id);
CREATE INDEX ON event (subject_type, subject_id);
-- REVOKE UPDATE, DELETE ON event FROM app_roles;
```

The `prev_hash` chain means a post-hoc edit to history is detectable. Cheap, and it makes "the ledger is the record" true rather than aspirational.

## 3. Research entities

```sql
CREATE TABLE research_question (
  rq_id UUID PRIMARY KEY,
  text TEXT NOT NULL,
  domain TEXT NOT NULL,
  origin TEXT NOT NULL,                     -- 'human' | 'derived'
  parent_claim_id UUID REFERENCES claim(claim_id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE program (
  program_id UUID PRIMARY KEY,
  rq_id UUID NOT NULL REFERENCES research_question(rq_id),
  lab_id UUID NOT NULL REFERENCES lab(lab_id),        -- 1 row until Stage 8
  policy_id UUID NOT NULL REFERENCES policy(policy_id),
  budget_usd NUMERIC(12,4) NOT NULL,
  status TEXT NOT NULL,
  config_hash BYTEA NOT NULL               -- full institutional config, for replay
);

CREATE TYPE hypothesis_state AS ENUM
  ('draft','screened','shelved','registered','built','executed','analyzed',
   'challenged','replicated','reviewed','institutional','refuted',
   'inconclusive','revised','abandoned_budget');

CREATE TYPE derivation_kind AS ENUM
  ('root','specialisation','generalisation','refutation_response',
   'merge','ablation','follow_up_from_failure');

CREATE TABLE hypothesis (
  hypothesis_id UUID PRIMARY KEY,
  program_id UUID NOT NULL REFERENCES program(program_id),
  parent_id UUID REFERENCES hypothesis(hypothesis_id),
  derivation derivation_kind NOT NULL,
  statement TEXT NOT NULL,                  -- the falsifiable sentence
  mechanism TEXT NOT NULL,                  -- expected causal story (prose, non-load-bearing)
  primary_metric TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('increase','decrease','no_change')),
  mde NUMERIC NOT NULL,                     -- minimum detectable / claimed effect
  falsification_condition TEXT NOT NULL,
  assumptions JSONB NOT NULL,
  state hypothesis_state NOT NULL,
  novelty_embedding vector(1024),           -- pgvector, institutional dedup
  created_by_task UUID,
  CHECK (parent_id IS NOT NULL OR derivation = 'root')
);
```

A hypothesis without a `falsification_condition`, an `mde`, and a single named `primary_metric` cannot be inserted. That alone eliminates the "attention probably improves performance" class of output at the storage layer.

## 4. Preregistration — the central invariant

```sql
CREATE TYPE registration_kind AS ENUM ('confirmatory','exploratory','replication');

CREATE TABLE registration (
  registration_id UUID PRIMARY KEY,
  hypothesis_id UUID NOT NULL REFERENCES hypothesis(hypothesis_id),
  kind registration_kind NOT NULL,
  parent_registration_id UUID REFERENCES registration(registration_id),
  spec JSONB NOT NULL,                     -- full ExperimentSpec, canonicalised
  spec_hash BYTEA NOT NULL UNIQUE,         -- sha256(canonical_json(spec))
  analysis_plan JSONB NOT NULL,            -- stat test, correction, stopping rule
  seed_root BIGINT NOT NULL,               -- all seeds derived deterministically
  n_seeds INT NOT NULL CHECK (n_seeds >= 1),
  holdout_query_budget INT NOT NULL CHECK (holdout_query_budget >= 1),
  registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  locked BOOLEAN NOT NULL DEFAULT TRUE,
  CHECK (kind = 'confirmatory' OR parent_registration_id IS NOT NULL)
);
-- Trigger: forbid UPDATE of spec/analysis_plan/seed_root once locked.
-- Trigger: forbid INSERT INTO run unless a matching locked registration
--          exists with registered_at < now().
```

Consequences: results cannot exist without a prior registration; a modified design is a *new* registration whose `kind` is degraded to `exploratory`; and `exploratory` registrations are excluded by the claim-promotion rule below.

## 5. Execution and provenance

```sql
CREATE TABLE dataset (
  dataset_id UUID PRIMARY KEY,
  name TEXT NOT NULL, version TEXT NOT NULL,
  content_hash BYTEA NOT NULL UNIQUE,
  generator_spec JSONB,                    -- for SCM-generated data: the DGP
  ground_truth JSONB,                      -- planted effects/leaks. RLS: agents CANNOT read
  licence TEXT NOT NULL,
  UNIQUE (name, version)
);

CREATE TABLE code_bundle (
  bundle_id UUID PRIMARY KEY,
  content_hash BYTEA NOT NULL UNIQUE,
  built_by_task UUID,
  validator_report JSONB NOT NULL,
  passed BOOLEAN NOT NULL
);

CREATE TABLE run (
  run_id UUID PRIMARY KEY,
  registration_id UUID NOT NULL REFERENCES registration(registration_id),
  bundle_id UUID NOT NULL REFERENCES code_bundle(bundle_id),
  dataset_id UUID NOT NULL REFERENCES dataset(dataset_id),
  seed BIGINT NOT NULL,
  executed_by role_t NOT NULL,             -- 'system' or 'replicator'
  environment_hash BYTEA NOT NULL,
  image_digest TEXT NOT NULL,
  git_commit TEXT NOT NULL,
  started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
  status TEXT NOT NULL,                    -- completed|infra_failure|scientific_failure|timeout|oom
  retry_count INT NOT NULL DEFAULT 0,
  telemetry JSONB NOT NULL,                -- cpu, peak mem, wall time, files written
  UNIQUE (registration_id, seed, executed_by, retry_count)
);

CREATE TABLE run_result (                  -- append-only, one row per metric
  result_id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES run(run_id),
  split TEXT NOT NULL CHECK (split IN ('train','dev','holdout')),
  metric TEXT NOT NULL,
  value DOUBLE PRECISION NOT NULL,
  computed_by TEXT NOT NULL CHECK (computed_by IN ('harness','custodian')),
  artifact_hash BYTEA NOT NULL,
  CHECK (split <> 'holdout' OR computed_by = 'custodian')  -- agents cannot produce holdout numbers
);

CREATE TABLE holdout_query (               -- adaptive-overfitting accounting
  query_id UUID PRIMARY KEY,
  registration_id UUID NOT NULL REFERENCES registration(registration_id),
  requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  artifact_hash BYTEA NOT NULL,
  granted BOOLEAN NOT NULL,
  remaining_budget INT NOT NULL
);
```

The `CHECK (split <> 'holdout' OR computed_by = 'custodian')` line is small and does a great deal of work: **no path exists by which an agent-authored number about the test set enters the database.**

## 6. Claims, evidence, objections, review

```sql
CREATE TABLE claim (
  claim_id UUID PRIMARY KEY,
  program_id UUID NOT NULL REFERENCES program(program_id),
  hypothesis_id UUID REFERENCES hypothesis(hypothesis_id),
  statement TEXT NOT NULL,
  kind assertion_kind NOT NULL,
  confidence TEXT NOT NULL,                -- COMPUTED by rubric; app has no write path
  computed_at TIMESTAMPTZ NOT NULL,
  CHECK (kind <> 'speculation')            -- speculation never becomes a claim
);

CREATE TABLE evidence (
  evidence_id UUID PRIMARY KEY,
  claim_id UUID NOT NULL REFERENCES claim(claim_id),
  kind TEXT NOT NULL CHECK (kind IN ('experimental','sourced','derived')),
  polarity TEXT NOT NULL CHECK (polarity IN ('supports','contradicts')),
  result_id UUID REFERENCES run_result(result_id),
  source_id UUID REFERENCES source(source_id),
  parent_claim_id UUID REFERENCES claim(claim_id),
  strength JSONB NOT NULL,                 -- effect size, CI, n_seeds, p, corrected p
  CHECK (
    (kind='experimental' AND result_id IS NOT NULL) OR
    (kind='sourced'      AND source_id IS NOT NULL) OR
    (kind='derived'      AND parent_claim_id IS NOT NULL)
  )
);
-- Trigger: a claim with zero evidence rows cannot leave 'speculative' confidence.

CREATE TABLE source (
  source_id UUID PRIMARY KEY,
  identifier TEXT NOT NULL,                -- DOI/arXiv/corpus id — must resolve
  retrieved_at TIMESTAMPTZ NOT NULL,
  verbatim_passage TEXT NOT NULL,          -- stored, not paraphrased
  passage_hash BYTEA NOT NULL,
  verified BOOLEAN NOT NULL DEFAULT FALSE  -- set only by the resolver, never an agent
);

CREATE TYPE objection_severity AS ENUM ('minor','major','critical');
CREATE TYPE objection_type AS ENUM
  ('leakage','contamination','weak_baseline','confound','multiple_testing',
   'seed_instability','metric_invalid','underpowered','implementation_bug',
   'alternative_explanation','generalisation_overreach','artifact_of_benchmark');

CREATE TABLE objection (
  objection_id UUID PRIMARY KEY,
  target_type TEXT NOT NULL, target_id UUID NOT NULL,
  type objection_type NOT NULL,
  severity objection_severity NOT NULL,
  statement TEXT NOT NULL,
  discriminating_test JSONB NOT NULL,      -- REQUIRED: what would settle this
  raised_by_task UUID NOT NULL,
  status TEXT NOT NULL,                    -- open|resolved_upheld|resolved_rejected|expired
  resolved_by_registration UUID REFERENCES registration(registration_id),
  was_injected_defect BOOLEAN,             -- eval harness only; NULL in production runs
  CHECK (jsonb_typeof(discriminating_test) = 'object')
);

CREATE TABLE review (
  review_id UUID PRIMARY KEY,
  claim_id UUID NOT NULL REFERENCES claim(claim_id),
  decision TEXT NOT NULL CHECK (decision IN ('accept','minor_revision','major_revision','reject')),
  scores JSONB NOT NULL,                   -- novelty, method, stats, reproducibility, evidence
  rationale TEXT NOT NULL,
  reviewed_by_task UUID NOT NULL
);
```

The promotion rule, as a function the application cannot bypass:

```sql
-- claim may reach 'well_supported' only if:
--   ≥1 replication with outcome='replicated' executed_by='replicator'
--   AND 0 objections with severity='critical' AND status='open'
--   AND its registration.kind='confirmatory'
--   AND every evidence row's artifact_hash resolves in the CAS
--   AND holdout_queries_consumed <= registration.holdout_query_budget
```

## 7. Forecasts, positions, decisions, cost

```sql
CREATE TABLE forecast (                    -- locked BEFORE execution
  forecast_id UUID PRIMARY KEY,
  registration_id UUID NOT NULL REFERENCES registration(registration_id),
  role role_t NOT NULL,
  p_effect_exceeds_mde DOUBLE PRECISION NOT NULL CHECK (p_effect_exceeds_mde BETWEEN 0 AND 1),
  predictive_mean DOUBLE PRECISION NOT NULL,
  predictive_sd DOUBLE PRECISION NOT NULL CHECK (predictive_sd > 0),
  p_execution_success DOUBLE PRECISION NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  brier_score DOUBLE PRECISION,            -- written once, after resolution
  crps DOUBLE PRECISION,
  UNIQUE (registration_id, role)
);
-- Trigger: reject INSERT if any run exists for this registration.

CREATE TABLE position (                    -- §22: disagreement is preserved
  position_id UUID PRIMARY KEY,
  claim_id UUID NOT NULL REFERENCES claim(claim_id),
  role role_t NOT NULL,
  stance TEXT NOT NULL CHECK (stance IN ('supports','opposes','abstains','uncertain')),
  rationale TEXT NOT NULL,
  UNIQUE (claim_id, role)
);

CREATE TABLE decision (
  decision_id UUID PRIMARY KEY,
  program_id UUID NOT NULL REFERENCES program(program_id),
  policy_id UUID NOT NULL REFERENCES policy(policy_id),
  kind TEXT NOT NULL,                      -- fund|shelve|replicate|terminate|override
  subject_id UUID NOT NULL,
  inputs JSONB NOT NULL,                   -- eig, p_success, cost, reserves — auditable
  outcome TEXT NOT NULL,
  dissent JSONB                            -- recorded when overriding an objection
);

CREATE TABLE policy (
  policy_id UUID PRIMARY KEY,
  version TEXT NOT NULL UNIQUE,
  parent_version TEXT,
  params JSONB NOT NULL,                   -- min_seeds, alpha, allocation_class, reserves…
  rationale TEXT NOT NULL,
  ab_test_registration UUID REFERENCES registration(registration_id),  -- Stage 7
  active BOOLEAN NOT NULL
);

CREATE TABLE cost_entry (
  cost_id BIGSERIAL PRIMARY KEY,
  program_id UUID NOT NULL, task_id UUID, run_id UUID,
  llm_input_tokens INT, llm_output_tokens INT, llm_cached_tokens INT,
  cpu_seconds NUMERIC, storage_mb NUMERIC,
  usd NUMERIC(12,6) NOT NULL,
  price_table_version TEXT NOT NULL
);

CREATE TABLE llm_call (                    -- reproducibility + audit + cache
  call_id UUID PRIMARY KEY,
  task_id UUID NOT NULL,
  cache_key BYTEA NOT NULL,                -- sha256(provider,model,params,prompt,tools)
  provider TEXT NOT NULL, model TEXT NOT NULL, params JSONB NOT NULL,
  prompt_hash BYTEA NOT NULL, response_hash BYTEA NOT NULL,
  cache_hit BOOLEAN NOT NULL
);
```

## 8. Access control (the isolation the spec asks for in §12)

```sql
CREATE ROLE agent_replicator;  -- distinct DB role; workers connect as their role
ALTER TABLE run          ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_result   ENABLE ROW LEVEL SECURITY;
ALTER TABLE code_bundle  ENABLE ROW LEVEL SECURITY;

-- The Replicator can see registrations, and nothing produced by the original run.
CREATE POLICY replicator_blind ON run_result FOR SELECT TO agent_replicator
  USING (EXISTS (SELECT 1 FROM run r WHERE r.run_id = run_result.run_id
                 AND r.executed_by = 'replicator'));

-- Nobody but the eval harness may read planted ground truth.
ALTER TABLE dataset ENABLE ROW LEVEL SECURITY;
CREATE POLICY no_ground_truth ON dataset FOR SELECT TO agent_roles
  USING (TRUE) WITH CHECK (FALSE);   -- ground_truth column excluded via a view
```

Agents read through role-specific **views**, never base tables. The view is the `input_view` in the role contract, which makes information asymmetry a schema object you can inspect and test — rather than an instruction you hope was followed.

## 9. Volume expectations

A 200-experiment program: ~10⁵ events, ~10⁴ result rows, ~10³ registrations, a few GB of artifacts. Trivially within a single Postgres instance. Do not design for scale that will not arrive; design for auditability, which will be needed on day one.
