# ADR-0006 — Defer Alembic migrations to the end of M5

- **Status:** accepted
- **Date:** 2026-08-30
- **Amends:** `BUILD_PLAN.md` M1 ("Alembic migrations, both backends")

## Context

M1 listed Alembic among its deliverables. On reaching it, the case is weak:

- There are no deployed instances and no data anyone needs to keep. Migrations exist to preserve existing data; there is none.
- The schema will change substantially in M2 (task queue, LLM call accounting), M3 (experiment specifications), M4 (the question bank) and M5 (custody and analysis). Migrations authored now would be rewritten several times, and reviewing a chain of migrations that never ran against real data is busywork that looks like rigour.
- `create_all` plus `verify_invariants` already guarantees the property that actually matters: a database either has all 19 invariant triggers or refuses to exist.

The counter-argument is real but not yet binding: a research ledger whose schema changes must be able to carry its history forward, because a claim that cannot be re-read is not reproducible.

## Decision

Ship M1 without Alembic. Adopt it at the end of M5, when the schema stabilises, with:

- one initial revision generated from the models as they stand then;
- `nullius db upgrade`, and `db init` stamping `head`;
- a CI job asserting that `alembic upgrade head` produces a schema with **no autogenerate diff** against the models — which is the check that actually prevents drift, and is worth more than the migrations themselves.

Until then `create_schema` is the only way to build a database, and `nullius doctor` reports the schema as unversioned.

## Consequences

**Good.** No migration churn across four milestones of heavy schema change. The drift check arrives once it can be meaningful.

**Bad.** Any database created before M5 must be rebuilt rather than migrated. Acceptable while the only databases are test fixtures and demo runs, and stated in the README rather than discovered.

**The trigger to revisit, ahead of schedule:** the first time a research program produces results that someone wants to keep across a schema change. At that point migrations stop being bookkeeping and start being provenance.
