"""The oracle: measuring what is actually true.

Runs the same comparison the institution will run, but at a scale the
institution is never allowed: far more samples, far more seeds, and — the part
that matters — **a disjoint seed stream**. An experiment cannot stumble onto
the oracle's exact sample, so agreeing with the truth requires estimating it,
not reproducing it.

This is the one place in the project where a number is computed and then
treated as authoritative. It earns that by being reproducible from the
generating process alone: ``nullius bank verify`` recomputes every value and
fails if any has drifted.
"""

from __future__ import annotations

import statistics
from typing import Any

from nullius.bank.truth import Truth, classify
from nullius.build import ops
from nullius.util.ids import EXPERIMENT_SEED_CEILING

__all__ = ["ORACLE_SEED_OFFSET", "measure_effect"]

ORACLE_SEED_OFFSET = EXPERIMENT_SEED_CEILING
"""Seeds live in a range no experiment reaches.

Disjoint by construction: :meth:`nullius.design.spec.ExperimentSpec.seeds`
draws strictly below this value, so no experiment can land on an oracle seed
and reproduce the ground truth sample outright. Agreeing with the truth
therefore requires estimating it.
"""

DEFAULT_SAMPLES = 20_000
DEFAULT_SEEDS = 40


def measure_effect(
    *,
    item_id: str,
    generator_params: dict[str, Any],
    mde: float,
    baseline_transform: str = "passthrough",
    treatment_transform: str = "divergence_prune",
    transform_params: dict[str, Any] | None = None,
    estimator: str = "logistic_regression",
    metric: str = "macro_f1",
    n_samples: int = DEFAULT_SAMPLES,
    n_seeds: int = DEFAULT_SEEDS,
    planted_defects: tuple[str, ...] = (),
) -> Truth:
    """Estimate the true effect of the treatment for one bank item.

    Returns the mean paired difference across seeds and its standard error.
    The standard error is reported rather than hidden: a "truth" whose own
    uncertainty overlaps the decision boundary is not fit to score anything,
    and :func:`nullius.bank.items.validate_bank` refuses such items.
    """
    params = dict(transform_params or {"k": 3})
    differences: list[float] = []
    causal_features: tuple[str, ...] = ()

    for index in range(n_seeds):
        seed = ORACLE_SEED_OFFSET + index
        data = ops.generate("covariate_shift", seed=seed, n_samples=n_samples, **generator_params)
        causal_features = data.causal_features

        scores: dict[str, float] = {}
        for arm, transform_name in (
            ("baseline", baseline_transform),
            ("treatment", treatment_transform),
        ):
            selection = ops.transform(
                transform_name, data.x_train, data.x_deploy, seed=seed, **params
            )
            model = ops.estimator(estimator, seed=seed)
            model.fit(data.x_train[:, selection.keep], data.y_train)
            scores[arm] = ops.metric(
                metric, data.y_deploy, model.predict(data.x_deploy[:, selection.keep])
            )
        differences.append(scores["treatment"] - scores["baseline"])

    effect = statistics.mean(differences)
    spread = statistics.stdev(differences) if len(differences) > 1 else 0.0
    standard_error = spread / (len(differences) ** 0.5)

    return Truth(
        item_id=item_id,
        effect=effect,
        standard_error=standard_error,
        verdict=classify(effect, mde),
        mde=mde,
        oracle_samples=n_samples,
        oracle_seeds=n_seeds,
        causal_features=causal_features,
        planted_defects=planted_defects,
    )
