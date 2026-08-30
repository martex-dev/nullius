"""The process that actually runs an experiment.

Launched by :class:`~nullius.execute.sandbox.SubprocessSandbox` as
``python -m nullius.execute.child <plan.json> <workdir>``. It installs the
guard first, then executes the plan, then writes results into ``workdir/out``.

**The holdout is not here, and cannot be reached from here.** For synthetic
data a filesystem split would be theatre — this process holds the generator
and the seed, so it could simply redraw whatever a "held out" file contained.
Instead the deployment sample this process sees is drawn from one seed, and
the Custodian later draws its own evaluation sample from the same generating
process using a seed that never appears in the plan. The holdout is therefore
a *fresh sample*, not a partition, which is strictly stronger: there is
nothing to leak because the data does not exist yet.

Nothing is pickled. The Custodian re-fits from the plan when it needs a model,
because the plan is deterministic — which avoids both an arbitrary-code
deserialisation surface and a stale-artifact problem in one decision.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

__all__ = ["main", "run_plan"]


def run_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Execute one compiled plan and return its results."""
    import numpy as np

    from nullius.build import ops

    started = time.perf_counter()
    dataset = ops.generate(plan["dataset"]["generator"], **plan["dataset"]["params"])

    # The generated deployment sample is the development set in full. The
    # Custodian's evaluation sample is drawn separately and is not derivable
    # from anything in this plan.
    x_train, y_train = dataset.x_train, dataset.y_train
    x_dev, y_dev = dataset.x_deploy, dataset.y_deploy

    arms: dict[str, Any] = {}
    for arm in plan["arms"]:
        arm_started = time.perf_counter()

        mask = np.ones(x_train.shape[1], dtype=bool)
        for step in arm["transforms"]:
            # Transforms are fitted on the training features and the unlabelled
            # deployment covariates only. Labels are never in scope here.
            selection = ops.transform(
                step["op"], x_train[:, mask], x_dev[:, mask], **step["params"]
            )
            active = np.flatnonzero(mask)
            mask[active[~selection.keep]] = False

        model = ops.estimator(arm["estimator"]["op"], **arm["estimator"]["params"])
        model.fit(x_train[:, mask], y_train)

        results: dict[str, dict[str, float]] = {}
        for split_name, features, labels in (
            ("train", x_train, y_train),
            ("dev", x_dev, y_dev),
        ):
            predictions = model.predict(features[:, mask])
            scores = None
            if hasattr(model, "predict_proba"):
                proba = model.predict_proba(features[:, mask])
                if proba.shape[1] == 2:
                    scores = proba[:, 1]
            results[split_name] = {
                name: ops.metric(name, labels, predictions, scores) for name in plan["metrics"]
            }

        arms[arm["name"]] = {
            "metrics": results,
            "n_features_used": int(mask.sum()),
            "features_used": [dataset.feature_names[i] for i in np.flatnonzero(mask)],
            "fit_seconds": round(time.perf_counter() - arm_started, 6),
        }

    return {
        "plan_version": plan["plan_version"],
        "seed": plan["seed"],
        "arms": arms,
        "n_train": int(x_train.shape[0]),
        "n_dev": int(x_dev.shape[0]),
        "n_features": int(x_train.shape[1]),
        "wall_seconds": round(time.perf_counter() - started, 6),
    }


def main(argv: list[str] | None = None) -> int:
    """Entry point. Installs the guard before anything else happens."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: python -m nullius.execute.child <plan.json> <workdir>", file=sys.stderr)
        return 2

    plan_path, workdir = Path(args[0]), Path(args[1])
    out = workdir / "out"
    out.mkdir(parents=True, exist_ok=True)

    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    # Installed after reading the plan and creating the output directory, so
    # that the guard's own setup is not what trips it, and before any operator
    # code runs.
    from nullius.execute.guard import GuardViolation, install_guard, violations

    install_guard(workdir)

    try:
        results = run_plan(plan)
    except GuardViolation as exc:
        (out / "error.json").write_text(
            json.dumps(
                {"kind": "guard_violation", "message": str(exc), "violations": violations()},
                indent=2,
            ),
            encoding="utf-8",
        )
        return 3
    except Exception as exc:
        (out / "error.json").write_text(
            json.dumps({"kind": type(exc).__name__, "message": str(exc)}, indent=2),
            encoding="utf-8",
        )
        return 1

    results["guard_violations"] = violations()
    (out / "results.json").write_text(
        json.dumps(results, indent=2, sort_keys=True), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    # Single-threaded BLAS: multi-threaded reductions sum in nondeterministic
    # order, which makes bitwise-identical reruns impossible.
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(var, "1")
    raise SystemExit(main())
