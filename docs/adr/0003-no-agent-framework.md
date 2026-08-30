# ADR-0003 — No agent framework

- **Status:** accepted
- **Date:** 2026-08-30
- **Relates to:** `docs/02-architecture.md` §9

## Context

LangGraph, AutoGen, CrewAI and CAMEL are the obvious things to reach for. All of them are competent at what they are for: orchestrating conversations between model-backed actors, plumbing prompts, and managing turn-taking.

None of that is this project's problem. Nullius's hard parts are an append-only provenance ledger, preregistration invariants, holdout custody, sandbox isolation, and a benchmark with ground truth. A framework helps with none of these, and its abstractions actively obscure two things we need to be able to state precisely: exactly what state an agent saw, and exactly what it cost.

There is also a benchmark-integrity argument. `docs/04-evaluation.md` compares arms B0–B7 that differ *only* in institutional structure. A framework's built-in retries, memory, and message passing would leak into some arms and not others, confounding the comparison this project exists to make.

## Decision

Write the runtime. It is roughly 1,500 lines: role contracts, a task queue, a worker loop, structured-output validation, a cost ledger, and a caching LLM provider abstraction.

Depend directly on the provider SDK (`anthropic`) behind a thin `LLMProvider` interface — kept thin deliberately, because model diversity across adversarial roles is a design requirement, not a hypothetical.

## Consequences

**Good.** Every byte an agent saw is a named SQL view we can inspect and test. Every call is cached and priced. The baseline ladder is genuinely controlled. No dependency churn from a fast-moving framework.

**Bad.** We write and maintain the runtime, including retries, timeouts and backoff. Features that arrive free in a framework — streaming UIs, ready-made tool loops — we build if we want them.

**Accepted.** This is a research instrument. Knowing exactly what it did matters more than getting there quickly.
