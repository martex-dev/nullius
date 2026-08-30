# ADR-0004 — The MVP Builder is a compiler, not a code generator

- **Status:** accepted
- **Date:** 2026-08-30
- **Supersedes for the MVP:** the original specification §9 ("Experiment Builder")

## Context

The specification's Experiment Builder generates source code, configuration, tests and an execution script from an experiment design. It is the most impressive-sounding component in the system and the most dangerous one, because its failures are *silent*: preprocessing fitted before the split, a metric computed on the wrong axis, a CV split that ignores group structure, a baseline that isn't capacity-matched. Each produces a plausible number and an invalid experiment.

Worse, those failures are confounded with everything else during the period when the institution itself is being debugged. If a claim comes out wrong, we would not know whether the Skeptic failed, the Analyst failed, or the generated code silently computed the wrong thing.

## Decision

For M0–M11, no language model writes executable code.

The Designer emits a typed `ExperimentSpec`. A human-written, unit-tested compiler turns that spec into a run plan against a fixed operator library (scikit-learn estimators, splitters, shift generators, metrics). The spec is the preregistered object; the compiler is deterministic; the operator library is tested against known-answer fixtures.

Code generation arrives at M12 in three graded steps — a restricted operator registry the model may extend, then constrained generation inside a template, then free-form behind the validator gate — and each step is measured against the compiler as a baseline on the same bank items.

## Consequences

**Good.** End-to-end working science in weeks rather than months. Near-zero silent-invalidity risk while the institution is being calibrated. When generation does arrive, its contribution is *measurable* rather than assumed, because a validated baseline already exists. The design linter has a closed, checkable space to reason about.

**Bad.** Expressiveness is bounded by the spec language. A hypothesis the DSL cannot express cannot be tested, and the Theorist must be constrained to the expressible — which is a real limitation on the research the MVP can do, and is reported as one.

**Unexpectedly good.** Being forced to design a real experiment DSL is better engineering than hoping a model improvises a correct one on each of several hundred runs.
