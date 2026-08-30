"""Nullius — an artificial research institution with ground truth.

*nullius in verba*: no assertion counts without evidence, including the
machine's own.

The package is organised by plane, mirroring ``docs/02-architecture.md``:

``nullius.ledger``
    The append-only, hash-chained event spine. All state is a fold over it.
``nullius.models``
    Typed entities; the single definition shared by the ORM and the agent
    protocol.
``nullius.runtime``
    Role contracts, task queue, worker loop. Agents never talk to each other;
    they read a role-scoped view and emit a validated artifact.
``nullius.llm``
    Provider abstraction plus the content-addressed response cache that makes
    a research program replayable byte-for-byte.
``nullius.registry``
    Preregistration: canonical serialisation and the hash written before any
    executor is dispatched.
``nullius.design`` / ``nullius.build`` / ``nullius.execute``
    Experiment specification, its compiler, and the sandbox that runs it.
``nullius.custody``
    The Holdout Custodian — sole holder of test splits, enforcer of query
    budgets.
``nullius.analysis``
    Statistics. Nothing in this package is ever produced by a language model.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.0.1"
