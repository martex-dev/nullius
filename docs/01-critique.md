# 01 — Specification Analysis

## 1. Assessment of the specification

The spec is unusually good for this genre. It correctly identifies that the interesting engineering is in *research infrastructure*, not in agent prompting, and it names most of the right primitives: provenance, preregistration, replication, adversarial review, resource limits, failure-as-data.

It has one structural gap and several smaller ones.

### 1.1 The structural gap: nothing in the spec can tell you whether the system works

§26 proposes evaluating the civilization on "hypothesis quality", "scientific reliability", "research diversity". Every one of those, as written, would in practice be measured by asking an LLM to grade an LLM's output. The stated meta-question — *does institutional structure improve autonomous research quality?* — is then answered by a metric that the institution's own components produce. That is not a research result; it is a mirror.

§26 does gesture at the fix ("measure how often high-confidence claims survive independent verification"), but internal replication only measures self-consistency. A system with a systematic bug reproduces its own bug perfectly.

**The fix, which should be treated as the project's core contribution:**

Construct the research questions from **structural causal models with planted answers**. The data generating process is written by us; the agents receive only sampled data. We therefore know, by construction:

- which features are causally relevant to the label and which are spuriously correlated;
- the exact magnitude of every effect, including effects that are exactly zero;
- where a leak was planted (a feature deterministically derived from the label, a duplicate row spanning the train/test split, a group-structure violation);
- which of two methods is genuinely better on the population, not on the sample.

Then "did the institution reach the correct conclusion" and "was its stated confidence calibrated" become *objective, cheap, and automatic*. Every other proposed metric becomes measurable against this. Half the question bank must have true effect size zero, so that "the system said no effect and was right" is a scoreable outcome and a yes-machine scores poorly.

Real datasets (vendored, hashed, offline) then serve as an external-validity check on top of the synthetic bank, not as the primary evaluation surface.

This changes what the project *is*: not "an AI that does research" but **a testbed with ground truth for measuring whether institutional structure improves autonomous empirical reasoning.** That framing is defensible to an ML researcher; the other is not.

### 1.2 Second gap: no mechanism calibrates the adversary

§7 defines the Skeptic well but gives it no objective. "Find why the research might be wrong", run on the same base model that produced the research, yields correlated blind spots and, worse, *objection theater*: fluent, unfalsifiable, unscoreable criticism that costs tokens and blocks nothing.

**Fix: planted-defect injection.** In a controlled fraction of runs, inject a known defect into the experiment (label leak, train/test contamination, an unmatched-capacity baseline, a metric computed on the wrong split, a fixed seed masking variance). Skeptic recall and precision on injected defects are then measurable. An uncalibrated Skeptic's objections are downweighted by the Director. This converts the adversarial layer from vibes into a measured component; it is mutation testing applied to epistemics.

Additionally, every objection must be typed and must name a **concrete discriminating test**. An objection with no test that could resolve it is rejected by schema, not by judgement.

### 1.3 Third gap: preregistration is described as a policy, not a mechanism

§8 says experiments "should ideally be registered BEFORE execution" and that the system "should prevent agents from quietly modifying the hypothesis after seeing results." An LLM asked not to HARK will HARK. This must be a system invariant:

- The registration (hypothesis text, primary metric, direction, effect threshold, analysis plan, seed source, stopping rule) is serialised canonically and hashed. The hash is written to an append-only table with a timestamp before any executor is dispatched.
- The results table carries a foreign key to the registration hash. Analysis reads the registration from the ledger, never from an agent's message.
- Any post-hoc change creates a *new* registration marked `exploratory`, derived from the original. Exploratory findings can never become institutional claims without a fresh confirmatory registration and a fresh execution on fresh data.
- **Test-split access is mediated.** Agents never receive the test partition. They submit a fitted artifact plus the preregistered evaluator to a Holdout Custodian service, which returns metrics and decrements a per-hypothesis query budget. This is the only real defence against adaptive overfitting across hundreds of automated experiments, and it is architecture, not prompting.

## 2. Ambiguous or underspecified components

| # | Component | What's unclear | Resolution proposed |
|---|---|---|---|
| A1 | "Meaningful empirical research" (§1) | No operational definition; unmeasurable as stated | Redefine: recovers planted ground truth with calibrated confidence on a held-out question bank (see `04-evaluation.md`) |
| A2 | Agent "incentives" (§4) | Prompts are not incentives; an LLM has no utility function you can set by asking | Implement incentives as *selection pressure*: scored track records (Forecast Ledger, Skeptic defect recall) that change how much weight the Director gives an agent's output and which agent variant is used next |
| A3 | "Expected information gain: 0.71" (§4.1) | Information gain over what distribution? | Defined explicitly as expected reduction in entropy of the claim's posterior, computed from elicited agent forecasts (see `02-architecture.md` §7) |
| A4 | Literature Researcher sources (§6) | "Permitted sources" undefined; live web search is the largest hallucination *and* prompt-injection surface in the design | MVP: curated, vendored, hash-pinned offline corpus. Stage 3+: live retrieval, but a claim may only cite a source whose retrieved text is stored verbatim and whose identifier resolves — enforced by a verifier, not the agent |
| A5 | Replicator independence (§12) | "Should not receive unnecessary information" — how is that enforced when agents share a store? | Enforced by *capability*, not instruction: the Replicator runs as a separate process under a distinct DB role whose row-level security denies it the original code, results and analysis. It receives the registration document only |
| A6 | Skeptic "blocks publication" (§7) | Blocks forever? Who overrules? Deadlock risk | Objections are typed with severity; `critical` objections gate the transition to `institutional`. Resolution requires a discriminating experiment or a Director override recorded as a dissent event. Objections expire after N cycles into `unresolved_limitation`, which is printed in the report |
| A7 | "Novelty" (§13, §26) | Novelty against what corpus? Unknowable in general | Restrict to *institutional novelty*: does this hypothesis duplicate one already in our knowledge graph (embedding + structural match)? Never claim novelty against world literature |
| A8 | Research economy units (§16) | Credits vs. real cost; is the budget a game or accounting? | Make it real: one ledger in USD-equivalent, pricing LLM tokens, CPU-seconds and storage. A fake economy teaches nothing |
| A9 | Self-improvement scope (§19) | Which surface is mutable? | Only a versioned, typed `Policy` record (thresholds, seed counts, allocation weights, agent-variant selection). Never prompts-at-large, never core code. Policy changes require an A/B against the question bank |
| A10 | "Paper" (§25) | Is the paper an output or the product? | An output artifact, deliberately deprioritised. The claim ledger is the product |
| A11 | Multiple labs (§18) | Shared "scientific ecosystem" semantics undefined | Out of scope until the single lab has a scored track record. At Stage 8: labs read each other's registrations and claims and may issue replications and objections, through the same typed interfaces |
| A12 | Failure taxonomy (§21) | "Do not silently retry everything" — but some retries are correct (transient OOM) | Distinguish *infrastructure failure* (retryable up to N, logged, not scientific evidence) from *scientific failure* (never retried; becomes an Observation and may spawn a hypothesis) |
| A13 | Dataset licensing/versioning (§20, §32) | Not addressed | Every dataset is vendored, hashed, licence-recorded, and mounted read-only. Sandbox has no network, so a dataset that is not vendored does not exist |

## 3. Technically difficult components (ranked by difficulty × risk)

1. **Making the evaluation non-circular.** Requires the SCM generator, question bank, calibration scoring, and discipline about what agents can see. Nothing else matters if this is wrong.
2. **Adaptive overfitting across many automated experiments.** Hundreds of LLM-designed experiments against the same dataset family will find the test set's noise. Mitigated by the Holdout Custodian, query budgets, and fresh resamples for confirmatory runs. Easy to state; easy to violate in a hundred places.
3. **Free-form experiment code generation that is both expressive and valid.** The Builder writing arbitrary training code is the flashiest component and the largest source of *silent* invalidity — subtly wrong CV splits, preprocessing fit on the full set, metric on the wrong axis. Recommend template-first.
4. **Meaningful adversarial review.** Correlated blind spots between same-model agents. Needs defect injection, information asymmetry, tool asymmetry, and ideally model diversity across roles.
5. **Genuine replication independence.** Requires process and DB-role isolation, not a prompt.
6. **Reproducibility across LLM nondeterminism.** Solved with a content-addressed LLM response cache keyed on `(provider, model, params, prompt_hash, tool_schema_hash)`; replays hit cache and are deterministic. Also the enabler for counterfactual policy replay in the baseline comparison.
7. **Resource allocation that is more than a heuristic.** EIG from elicited forecasts is tractable; doing it well is research in itself. Ship a simple scored version and measure it against random allocation.
8. **Cost control.** A naive loop with a Skeptic and a Reviewer can burn a large token budget on a trivial question. Needs hard per-program caps, aggressive caching, small models for mechanical roles, bounded critique rounds.

## 4. Unnecessary for an MVP — cut, defer, or replace

| Spec section | Verdict | Reasoning |
|---|---|---|
| §18 Competing labs | **Cut to Stage 8** | Multiplies every unsolved problem by N |
| §19 Self-improving policies | **Cut to Stage 7** | Cannot measure improvement before the ground-truth benchmark exists |
| §24 Dashboard (Next.js, real-time) | **Replace for MVP** | A static HTML report generator over the event ledger plus a `rich` CLI gives 90% of the value for 5% of the effort. Real dashboard at Stage 6 |
| §25 Paper generation | **Defer, then constrain** | Most seductive, least informative. When built: template renderer, numbers from DB only |
| §6 Literature Researcher w/ live web | **Replace for MVP** | Vendored offline corpus. Live retrieval adds hallucination and injection risk before there is anything to protect |
| §14 Graph database | **Replace** | Postgres with recursive CTEs handles genealogy at this scale. Add a graph store only if a query proves impossible |
| §17 Multiple concurrent programs | **Defer to Stage 5** | Needed only once the economy exists |
| §16 Five separate budget types | **Simplify** | One unified cost ledger from day one; the allocation *policy* is the Stage 5 work |
| §9 Free-form code generation | **Constrain for MVP** | Typed `ExperimentSpec` compiled by a human-written, unit-tested harness. Codegen enters as a restricted op registry at Stage 2 |
| §11 Full statistical suite | **Prioritise a subset** | Seed variance, paired bootstrap CIs, effect sizes, multiple-comparison control. Add the rest when a question demands it |
| §20 Environment hashing | **Keep — it is cheap** | Container digest + lockfile hash + dataset hash + code commit, from commit #1. Retrofitting provenance is miserable |

## 5. Failure modes — how this system will fool itself

Assume every one of these *will* occur.

| # | Failure mode | Mechanism | Mitigation |
|---|---|---|---|
| F1 | **HARKing** | Hypothesis quietly rewritten after seeing results | Hashed preregistration; FK from result to registration; post-hoc changes forced into `exploratory` lineage |
| F2 | **Adaptive test-set overfitting** | Hundreds of experiments select on test noise | Holdout Custodian; per-hypothesis query budget; fresh resample for confirmatory runs |
| F3 | **Seed shopping** | Report the seed that worked | Seeds derived from a preregistered seeded RNG; all seeds' results are required rows; analysis over the full set or the experiment is invalid |
| F4 | **Publication bias / null suppression** | Failed experiments quietly abandoned | Registration ledger append-only; every registration must reach a terminal state; report generator enumerates unreported registrations |
| F5 | **Metric / spec gaming** | Generated code computes the metric on training data, or leaks labels into features | Metrics computed only by the Custodian using the preregistered evaluator; automated leak detectors on every artifact |
| F6 | **Objection theater** | Fluent unfalsifiable criticism | Objections require a typed discriminating test; Skeptic scored on planted-defect recall; unresolvable objections downweighted |
| F7 | **Reviewer capture** | Reviewer rewards well-written prose | Reviewer sees structured evidence, not prose; its accept/reject decisions are scored against planted ground truth |
| F8 | **Correlated blind spots** | All agents are the same model | Model diversity across adversarial roles; information and tool asymmetry; defect injection measures residual correlation |
| F9 | **Replication contamination** | Replicator sees original code or expected result | Separate process + DB role with RLS; independence audited by checking which rows that role read |
| F10 | **Retry laundering** | Crash → retry → crash → retry → a run that "worked" | Infrastructure vs scientific failure typing; retry count attached to every result; retries on a scientific failure invalidate it |
| F11 | **Citation fabrication** | Plausible references with no referent | Vendored corpus IDs only in MVP; later, resolver verification with stored verbatim passage |
| F12 | **Confidence inflation** | Everything becomes "strong evidence" | Confidence *computed* from a rubric over evidence rows (replication count, effect size, CI width, objection status), never written by an agent |
| F13 | **Degenerate exploration** | The institution re-asks the same hypothesis in new words | Institutional-novelty dedup at hypothesis intake; diversity metric in evaluation |
| F14 | **Budget starvation of nulls** | Nulls are cheap to abandon, expensive to confirm | Director policy reserves a fixed share of budget for confirmatory null testing; null-arm questions make this scoreable |
| F15 | **Prompt injection via data or literature** | Retrieved text or a dataset field contains instructions | All external text is data, never instruction; rendered into delimited, labelled blocks; sandbox has no network; tool ACLs per role |
| F16 | **Silent statistical invalidity** | Dozens of hypotheses, nominal p-values | Family-wise/FDR control at the *program* level, computed centrally from the registration ledger, not per-experiment |
| F17 | **Provenance rot** | Claims accumulate faster than evidence links | DB constraints: no claim without ≥1 evidence row; no `institutional` status without ≥1 successful independent replication and zero open `critical` objections |
| F18 | **Cost blowout** | Critique loops recurse | Hard per-program token cap; bounded critique rounds; caching; small models for mechanical roles |
| F19 | **Anthropomorphic drift** | Agent prose about "believing" and "deciding" gets read as the system's actual state | The DB is the state. Prose is decoration and is excluded from every metric |

## 6. Where LLMs should and should not be trusted

The operative rule: **an LLM may propose, name, explain and prioritise. It may not measure, compute, or attest.**

| Task | Trust level | Enforcement |
|---|---|---|
| Generating candidate hypotheses | **Propose** — high value | Schema validation; falsifiability checks; novelty dedup |
| Writing motivation / mechanism prose | **Trusted (non-load-bearing)** | Never citable as evidence |
| Proposing experiment design | **Propose, then verify** | Design linter runs as code: baseline matched? confounds enumerated? power adequate? splits grouped? |
| Writing experiment code | **Propose, then sandbox + test** | Static checks, import allowlist, unit tests, leak detectors, resource caps, no network |
| Computing any statistic | **Never** | scipy/numpy inside the sandbox. LLM-emitted numbers may not enter the DB |
| Judging significance | **Never** | Computed by the analysis harness from raw artifacts |
| Interpreting a computed result in words | **Propose** | Stored as a distinct typed row (`interpretation`), never as `observation` |
| Asserting an external fact | **Never without provenance** | Must resolve to a stored source passage; unresolvable claims rejected at write time |
| Estimating priors / forecasts | **Trusted as elicitation** | Value comes from *scoring* them, not believing them |
| Judging novelty vs. world literature | **Never** | Institutional novelty only |
| Setting a claim's confidence | **Never** | Rubric computed from evidence rows |
| Deciding resource allocation | **Advisory** | Policy code decides from scored inputs; the agent supplies estimates |
| Writing the report's numbers | **Never** | Template-rendered from DB; LLM fills designated prose slots and the renderer rejects numerals in those slots |
| Summarising an objection | **Propose** | Typed, with a required discriminating test |

## 7. Prior art and differentiation

The space is crowded as of 2026. Overlapping work, grouped by what it actually does:

- **End-to-end "AI scientist" pipelines** (Sakana's AI Scientist line; Agent-Laboratory-style systems; several 2025 startup entrants producing autonomously written workshop papers). These optimise the *paper*: idea → code → experiment → LaTeX → simulated review. Their weakness is exactly what §35 of the spec warns against — the output is judged by how the prose reads, often by LLM reviewers.
- **Hypothesis-generation systems** (Google's AI co-scientist and similar multi-agent generate/rank/evolve architectures, largely biomedical). Strong on idea generation and tournament ranking; validation is external and human, so the loop is not autonomously closed.
- **Agentic ML benchmarks** (MLE-Bench, MLAgentBench, RE-Bench/METR-style time-horizon evals, DiscoveryBench and related data-driven-discovery suites). These supply ground truth — the right instinct — but they evaluate *a single agent solving a task*, not an institution accumulating knowledge across a research program.
- **Autonomous laboratory systems** (Coscientist, ChemCrow, self-driving labs). Genuinely closed-loop with physical validation; orthogonal domain, not concerned with institutional epistemics.
- **Literature/evidence agents** (PaperQA-style RAG with provenance, systematic-review automation). Good provenance discipline; no experimentation.
- **Multi-agent frameworks** (AutoGen, CrewAI, LangGraph, CAMEL). Infrastructure for conversation, not for scientific state. Worth learning from; wrong to build on here.

> **Evidence-discipline note.** The paragraph above is a from-memory orientation, not a citation set. Before any of it appears in a report or README, each system must be verified against a retrieved primary source and stored with provenance — the same rule this system imposes on its own agents. Assume some details are stale or wrong until checked.

**How this project differentiates.** Not by being another end-to-end paper writer, but by making the *institution itself* the object of measurement:

1. **Planted ground truth.** Questions generated from known SCMs with known effect sizes, known nulls, known planted defects. Correctness and calibration are measured, not judged.
2. **Norms as invariants.** Preregistration hashes, a Holdout Custodian with query budgets, DB-level provenance constraints. Other systems ask agents to be rigorous; this one makes rigour the only reachable state.
3. **A calibrated adversary.** Defect injection turns the Skeptic into a measured detector with recall and precision instead of a rhetorical device.
4. **A Forecast Ledger.** Every agent forecasts every experiment before it runs; proper scoring gives calibration, EIG estimates, and a non-circular improvement signal.
5. **Counterfactual replay.** Event-sourced state plus a content-addressed LLM cache means a research program can be replayed under a different institutional configuration at near-zero marginal cost — which is what makes the §27 ablation ("does structure help?") affordable and properly controlled.

Honest positioning: **a measurement instrument for autonomous research processes**, which happens to be a working autonomous research process.
