# ADR-0005 — Content-addressed LLM cache as the reproducibility mechanism

- **Status:** accepted
- **Date:** 2026-08-30
- **Relates to:** `docs/02-architecture.md` §1.1, `docs/04-evaluation.md` §5

## Context

Language models are nondeterministic, priced per token, and slow. All three are in direct tension with a project whose thesis is reproducibility:

- A research program that cannot be re-run identically has no provenance worth the name.
- The B0–B7 baseline ladder needs many arms over many bank items; run naively, cost scales with (arms × items × roles × rounds).
- CI cannot depend on a paid API.

## Decision

Every model call passes through a cache keyed on `sha256(provider ‖ model ‖ params ‖ prompt ‖ tool_schemas)`. Hits are recorded as hits, priced at zero, and logged with the same `llm_call` row as misses.

Three providers implement one interface:

- `AnthropicProvider` — live; writes into the cache.
- `ReplayProvider` — cache only; a miss is a hard error, never a silent live call.
- `MockProvider` — deterministic canned responses for unit tests.

Cache entries are committed to the repository as run fixtures for the recorded demo, so `nullius demo --replay` reproduces a full program from a clean clone with no API key and no cost.

## Consequences

**Good.** Three properties from one mechanism: byte-identical replay, near-zero marginal cost for ablations that vary structure but not prompts, and CI that exercises the real pipeline offline. This is what makes the §5 ablation sweep (seeds, replication requirement, allocation policy, holdout budget, model diversity) affordable at all.

**Bad.** A cache hit is only *sound* if the key covers everything that affects the response. Provider-side model updates under a stable alias would silently break the assumption, so the model id must be pinned to a dated version and stored in provenance, never an alias.

**Bad.** Committed fixtures are bulky. Kept under `fixtures/`, compressed, pruned per release, and excluded from the package.

**Watch for.** Cache-driven results are not independent samples. Any measurement of *variance across model runs* must bypass the cache explicitly, with a flag that is recorded in the run's provenance so nobody later mistakes replayed determinism for empirical stability.
