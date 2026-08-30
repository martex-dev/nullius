# ADR-0001 — SQLite is the default store; Postgres is opt-in

- **Status:** accepted
- **Date:** 2026-08-30
- **Supersedes part of:** `docs/03-data-model.md` §1 ("Postgres 16")

## Context

The data model targets Postgres 16 and leans on four Postgres features: row-level security (for Replicator blindness), triggers, `jsonb`, and `pgvector`. The build machine has no Postgres and no Docker, and installing either requires WSL2 and a reboot.

Independently: one of this project's stated goals is that a research result be reproducible from a clean clone. A required database service is a reproducibility tax paid by every future replicator.

Expected scale for an MVP program is ~10⁵ events, ~10⁴ result rows, ~10³ registrations. That is three orders of magnitude below where SQLite becomes questionable.

## Decision

SQLite is the default backend. Postgres is a supported, opt-in production backend selected by connection URL.

The schema is defined once in SQLAlchemy and migrated by Alembic against both. Backend-specific capabilities are declared explicitly:

| Capability | SQLite | Postgres |
|---|---|---|
| Append-only enforcement | trigger | trigger + revoked grants |
| Invariant `CHECK` constraints | yes | yes |
| Replicator blindness | role-scoped repository layer + query audit log | the same, **plus** row-level security |
| Novelty embeddings | numpy cosine over a small table | `pgvector` |
| Task queue | single-writer transaction | `SELECT … FOR UPDATE SKIP LOCKED` |

`nullius doctor` reports which enforcement tier is active, and the provenance record of every run stores it — so a claim produced under the weaker tier is identifiable as such forever.

## Consequences

**Good.** Zero-dependency clone-and-run. Tests are fast and hermetic. The isolation invariant gets an application-layer implementation with an audit trail, which is more directly testable than an RLS policy and is portable.

**Bad.** Replicator blindness under SQLite rests on our repository layer rather than on the database refusing. A bug there is a silent breach; under Postgres RLS it is not. Mitigation: the blindness test asserts against the audit log, and runs in CI against *both* backends.

**Neutral.** Concurrency is limited under SQLite. Experiment execution parallelism lives in the sandbox pool, not the database, so this does not bind at MVP scale.

## Revisit when

Any of: a program exceeds ~10⁶ events; concurrent labs (M12) need real write concurrency; or an audit demands that isolation be enforced by the database rather than by our code.
