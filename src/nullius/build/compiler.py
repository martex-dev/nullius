"""Compiling a specification into an executable plan.

Deterministic, total, and human-written — ADR-0004. The compiler is the reason
no language model writes executable code in this milestone: the space of
experiments is whatever the specification language can express, and every path
through it is the same tested code.

The plan is plain JSON. It crosses a process boundary into the sandbox, so it
must carry no callables, no pickles and no imports — only registry keys and
parameters. That constraint is doing security work as well as clarity work.
"""

from __future__ import annotations

from typing import Any

from nullius.design.spec import ExperimentSpec

__all__ = ["RunPlan", "compile_spec"]

RunPlan = dict[str, Any]
"""A JSON-serialisable instruction set for one seed of one experiment."""


def compile_spec(spec: ExperimentSpec, *, seed: int) -> RunPlan:
    """Turn ``spec`` into the plan for one seed.

    One plan per seed rather than one plan for all of them: seeds are the unit
    of replication, they are executed independently, and a crash in one must
    not take the others with it.
    """
    if seed not in spec.seeds():
        raise ValueError(
            f"seed {seed} is not among the seeds this registration declared; "
            "seeds are fixed at registration so that reporting all of them is checkable"
        )

    return {
        "plan_version": 1,
        "title": spec.title,
        "seed": seed,
        "dataset": {
            "generator": spec.dataset.generator,
            "params": {**spec.dataset.params, "seed": seed},
        },
        "split": {
            "kind": spec.split.kind,
            "dev_fraction": spec.split.dev_fraction,
            # The holdout fraction is carried so the child can *exclude* it.
            # The child never receives holdout labels; the Custodian holds them.
            "holdout_fraction": spec.split.holdout_fraction,
            "group_column": spec.split.group_column,
        },
        "arms": [
            {
                "name": arm.name,
                "transforms": [
                    {"op": step.op, "params": {**step.params, "seed": seed}}
                    for step in arm.transforms
                ],
                "estimator": {
                    "op": arm.estimator.op,
                    "params": {**arm.estimator.params, "seed": seed},
                },
            }
            for arm in spec.arms
        ],
        "metrics": list(spec.all_metrics()),
        "primary_metric": spec.primary_metric,
        "baseline_arm": spec.baseline_arm,
        "treatment_arm": spec.treatment_arm,
    }


def compile_all(spec: ExperimentSpec) -> list[RunPlan]:
    """One plan per declared seed, in the order the registration fixed."""
    return [compile_spec(spec, seed=seed) for seed in spec.seeds()]
