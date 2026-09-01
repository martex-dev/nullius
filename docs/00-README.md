# Design Documents — Autonomous Research Civilization

Status: **architecture & feasibility analysis only. No implementation.**
Date: 2026-08-30

> These documents are the original design and are deliberately left as written. Several
> of their predictions have since been tested and refuted, and where that happened the
> document carries a note pointing at the result. The generated record of what was
> actually found is **[FINDINGS.md](../FINDINGS.md)**.

Read in order:

| Doc | Covers | Spec items answered |
|---|---|---|
| [01-critique.md](01-critique.md) | Spec analysis, ambiguities, hard parts, MVP cuts, failure modes, LLM trust boundary, prior art | 1–4, 15, 16, 23 |
| [02-architecture.md](02-architecture.md) | System architecture, agent protocol, state machines, experiment lifecycle, knowledge representation, economy, sandbox, tech comparison | 5, 7, 8, 9, 10, 11, 12, 17 |
| [03-data-model.md](03-data-model.md) | Schema (DDL sketch), provenance invariants | 6 |
| [04-evaluation.md](04-evaluation.md) | Evaluation methodology, ground-truth benchmark, baselines | 13, 26/27 |
| [05-security.md](05-security.md) | Threat model, sandbox hardening, security risks | 14, 28 |
| [06-roadmap.md](06-roadmap.md) | MVP definition, phased roadmap, complexity estimates, delegation, names, first experiment | 18–22, 24, 25 |

## The five decisions that matter most

Everything else is negotiable. These are not.

1. **Ground truth, or the project is unfalsifiable.** Evaluate the institution on research questions whose answers are known to the evaluator and unknown to the agents — primarily datasets generated from a structural causal model with *planted* effects, leaks, and nulls. Without this, "research quality" is scored by an LLM judging an LLM, and the whole project collapses into circularity. This is the single highest-leverage design choice in the spec, and the spec does not contain it.
2. **Enforce scientific norms with system invariants, never with prompts.** Preregistration is a content hash written before execution and checked by a DB constraint. Test-set access is mediated by a custodian service with a query budget. An agent cannot HARK, seed-shop, or peek because the architecture makes it impossible — not because its system prompt asks it not to.
3. **Agents do not converse.** Every agent action is `typed state view → typed artifact → append-only event`. A blackboard with a ledger, not a chat room. This buys replay, provenance, audit and cost control at once.
4. **Numbers never pass through an LLM.** All statistics are computed by library code in the sandbox; reports are template-rendered from the database with LLM prose confined to non-numeric slots. This deletes an entire category of fabrication.
5. **Every agent forecasts before every experiment, and forecasts are scored.** The Forecast Ledger yields calibration metrics, expected-information-gain estimates for the budget allocator, and a non-circular self-improvement signal — for free.

## The MVP in one sentence

A single-lab, CPU-only, offline pipeline that takes one tabular-ML research question through hypothesis → preregistration → templated experiment → sandboxed execution → statistical analysis → adversarial challenge → independent replication → review → a claim in a provenance database, scored against planted ground truth, with a null-effect arm to prove it can say "no effect."
