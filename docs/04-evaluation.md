# 04 — Evaluation Methodology

The project's own scientific claim is:

> **Institutional structure — preregistration, adversarial challenge, independent replication, and evidence-typed memory — improves the accuracy and calibration of autonomous empirical research relative to an unstructured agent, at a measurable cost in compute and tokens.**

That is a falsifiable claim with a plausible null. The evaluation exists to test it, not to demonstrate it.

## 1. The question bank (ground truth)

Everything rests on a bank of research questions whose true answers we know and the agents cannot.

### 1.1 Synthetic tier — structural causal models

A generator produces tabular datasets from an explicit SCM. Because we author the DGP, we know exactly:

- the true effect of each intervention on the population metric, including **exact zeros**;
- which features are causal, which are spurious (correlated only through a confounder or selection), and which are noise;
- the true robustness ordering of candidate methods under a specified shift;
- where any defect was planted.

Each bank item is:

```yaml
item_id: SCM-014
research_question: "Does <treatment family> improve <metric> under <shift> relative to <baseline>?"
dgp: {scm: ..., shift: ..., n: ..., noise: ...}
truth:
  effect: 0.000            # true population effect; ~50% of the bank is exactly 0
  sign: null
  correct_verdict: no_effect      # supported | refuted | no_effect | conditional
  moderators: [...]               # for 'conditional' items: the true interaction
  planted_defects: []             # or e.g. [label_leak_via_feature_7]
sampling: {seeds: [...], fresh_resample_available: true}
```

**Design rules for the bank:**

- **≥45% null items.** A system that always finds an effect must score badly. This is the single most important composition rule.
- **~20% conditional items** where the effect exists only under a moderator. Tests whether the institution finds the qualification rather than the headline.
- **~15% items with planted defects**, so that a naive pipeline reaches a *confidently wrong* answer and only defect detection saves it. This is where the Skeptic earns its cost.
- **~10% items where the obvious method wins for the wrong reason** (e.g. feature reduction helps through capacity control, not through shift-awareness). Tests confound detection.
- **Held-out split of the bank** never used during development, run once per release.

### 1.2 Semi-synthetic tier

Real vendored datasets with *injected* known perturbations (a planted leak column, a known label-noise rate, a synthetic subgroup shift). Truth is known for the injected component only.

### 1.3 Real tier — external validity

Fully real questions with answers established by an exhaustive offline sweep we ran ourselves and locked before the system saw the data. Expensive; use a handful, and treat them as a sanity check on the synthetic tiers rather than a primary metric.

## 2. Primary metrics

All computed automatically against the bank.

| Metric | Definition | Why it matters |
|---|---|---|
| **Verdict accuracy** | Fraction of items where the final institutional verdict matches `correct_verdict` | The headline |
| **Null accuracy** | Verdict accuracy restricted to true-zero items | Catches the yes-machine failure |
| **Calibration (Brier / ECE)** | Over the system's final confidence level mapped to a probability | A system that is wrong but *knows* it is uncertain is far more useful than one that is confidently wrong |
| **False discovery rate** | Fraction of `institutional` claims that are false | The claim that matters for downstream use |
| **Defect recall / precision** | Skeptic detection of planted defects | Whether adversarial review is real |
| **Replication concordance** | Agreement between original and independent replication, and its agreement with truth | Distinguishes self-consistency from correctness |
| **Cost per correct claim** | USD-equivalent / correct institutional claim | The efficiency axis; the whole point of the economy |
| **Effect-size error** | \|estimated − true\| on non-null items | Beyond binary correctness |
| **Provenance completeness** | Fraction of claims whose evidence chain fully resolves to artifacts | Whether the memory is real |
| **Diversity** | Distinct hypothesis clusters explored per program (embedding clusters), and coverage of the true moderator space | Detects degenerate loops |
| **Failure recovery** | Fraction of scientific failures producing a valid, later-confirmed follow-up hypothesis | Whether failure is genuinely first-class |
| **Forecast calibration by role** | Brier/CRPS per role | Interesting in its own right; also the self-improvement signal |

Deliberately **not** a metric: paper quality, prose quality, reviewer-agent scores, or anything an LLM judges. Those may be reported as descriptive colour; they never enter the evaluation.

## 3. Baseline ladder (spec §27, refined)

Every arm runs on the identical bank items, identical compute cap, identical model, identical seeds, and identical data access. Only institutional structure varies.

| Arm | Composition | Isolates |
|---|---|---|
| **B0 Oracle-null** | Always answers "no effect" | Floor; exposes bank imbalance |
| **B1 Single-shot** | One LLM: read question → write code → run → conclude | The naive baseline everyone actually ships |
| **B2 Single-agent + loop** | B1 with iteration until self-satisfied | Does iteration alone help? |
| **B3 Multi-role, no adversary** | Theorist + Designer + Builder + Analyst | Does role decomposition alone help? |
| **B4 B3 + preregistration + custodian** | Adds the invariants, no Skeptic | **Key arm**: how much comes from mechanism rather than from agents? |
| **B5 B4 + Skeptic** | Adds adversarial challenge | Value of adversarial review |
| **B6 Full** | B5 + independent replication + Reviewer + persistent memory across items | The full institution |
| **B7 Full − memory** | B6 with memory wiped between items | Isolates institutional memory's contribution |

The interesting prediction, worth stating in advance so the project can be wrong about it: **B4 will capture most of the gain over B3.** If true, the paper's finding is "cheap mechanisms beat expensive agents", which is a more useful result than "more agents are better" and is exactly the kind of thing this testbed exists to establish. Register that prediction before running.

Statistics: paired over bank items, bootstrap CIs over items and seeds, FDR control across arms, minimum bank size determined by a power analysis for the smallest effect worth caring about (target: detect a 10-point accuracy difference, which at ~60 items and paired comparison is feasible).

## 4. Evaluating agents individually

- **Skeptic**: precision/recall on injected defects; and *yield* — fraction of its objections whose discriminating test actually changed the verdict. An objection that never changes anything is theater regardless of how it reads.
- **Reviewer**: accept/reject decisions scored against truth; measure whether it rejects true claims (over-conservatism) as well as accepting false ones.
- **Theorist**: fraction of hypotheses that pass validators; downstream confirmation rate; institutional novelty; and coverage of the true moderator space.
- **Designer**: linter pass rate; replication rate of the designs it produced; power adequacy vs. realised variance.
- **Director**: regret against a retrospective oracle allocation over the same item set. Computable exactly, because after the fact we know which experiments were informative.

## 5. Ablations to run inside the full system

Enabled cheaply by event sourcing + LLM cache replay:

- min seeds ∈ {1, 3, 5, 10} — is the spec's §19 example ("5 seeds for small effects") actually right?
- replication required ∈ {none, 1, 2}
- allocation policy ∈ {random, round-robin, greedy-EIG, Thompson}
- holdout query budget ∈ {1, 3, 10, ∞} — quantifies adaptive overfitting directly
- model diversity ∈ {all one model, adversarial roles on a different model}
- Skeptic present but ignored (measures whether *raising* objections helps even when unheeded, i.e. is the value in the criticism or in the blocking?)

## 6. Reporting discipline

The evaluation is subject to the same rules the system is: the benchmark protocol is preregistered in the repository with a hash before results are collected; every arm's outcome is reported including the ones that make the project look bad; and the held-out bank split is used exactly once per release. If the institution does not beat B1, that is the finding, and it gets published as such.
