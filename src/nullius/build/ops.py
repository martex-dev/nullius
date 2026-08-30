"""The operator registry.

The closed set of things an experiment may do. Nothing outside this module is
reachable from a specification, which is what ADR-0004 buys: the space of
expressible experiments is small, human-written and unit-tested, rather than
whatever a model improvised this time.

Three families:

**Generators** produce data. For M3 these are simple parameterised shifts; M4
replaces them with structural causal models carrying known ground truth. The
interface is the same either way — a spec names a generator and its
parameters, and the data is reproducible from the specification alone.

**Transforms** and **estimators** are thin wrappers over scikit-learn, pinned
to deterministic settings.

**Metrics** are computed here and nowhere else. No metric is ever produced by
a language model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import numpy as np
from numpy.typing import NDArray
from sklearn.base import BaseEstimator
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, roc_auc_score

__all__ = [
    "Dataset",
    "estimator",
    "generate",
    "metric",
    "registered_estimators",
    "registered_generators",
    "registered_metrics",
    "registered_transforms",
    "transform",
]

Array = NDArray[np.float64]
Labels = NDArray[np.int_]


@dataclass(frozen=True, slots=True)
class Dataset:
    """Features and labels for two environments.

    ``train`` is what an experiment fits on. ``deployment`` is the shifted
    environment. The holdout partition of ``deployment`` never leaves the
    Custodian, so the runner only ever receives what it is allowed to see.
    """

    x_train: Array
    y_train: Labels
    x_deploy: Array
    y_deploy: Labels
    feature_names: tuple[str, ...]
    causal_features: tuple[str, ...] = ()
    """Known by construction for synthetic data. Not exposed to any agent view."""


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

_GENERATORS: dict[str, Callable[..., Dataset]] = {}
_TRANSFORMS: dict[str, Callable[..., Any]] = {}
_ESTIMATORS: dict[str, Callable[..., BaseEstimator]] = {}
_METRICS: dict[str, Callable[[Labels, Labels, Array | None], float]] = {}


def _register(registry: dict[str, Any], name: str) -> Callable[[Any], Any]:
    def decorate(fn: Any) -> Any:
        if name in registry:
            raise ValueError(f"{name!r} is already registered")
        registry[name] = fn
        return fn

    return decorate


def generator(name: str) -> Callable[[Any], Any]:
    return _register(_GENERATORS, name)


def transform_op(name: str) -> Callable[[Any], Any]:
    return _register(_TRANSFORMS, name)


def estimator_op(name: str) -> Callable[[Any], Any]:
    return _register(_ESTIMATORS, name)


def metric_op(name: str) -> Callable[[Any], Any]:
    return _register(_METRICS, name)


@generator("covariate_shift")
def _covariate_shift(
    *,
    seed: int,
    n_samples: int = 2000,
    n_causal: int = 4,
    n_spurious: int = 3,
    n_noise: int = 5,
    shift: str = "spurious",
    shift_strength: float = 2.0,
    spurious_strength: float = 1.6,
    label_noise: float = 0.5,
    n_shifted: int = 3,
) -> Dataset:
    """Tabular data under *label-preserving covariate shift*.

    Three families of feature, known by construction:

    ``causal``
        Drawn first; the label is generated from them. ``P(y | X_causal)`` is
        invariant across environments, so a model that learns it transfers.
    ``spurious``
        Generated *from* the label, so their relationship to it is a property
        of the environment rather than of the world. Under a spurious shift
        that relationship reverses.
    ``noise``
        Independent of the label in every environment.

    Two properties make this a usable ground truth, and both were wrong in the
    first version:

    **Shift is applied before the label is drawn.** "Label-preserving
    covariate shift" means ``P(X)`` changes and ``P(y | X)`` does not.
    Shifting the causal features *after* generating the label is concept
    drift, and it destroys the very relationship the experiment is supposed to
    find — which is why pruning appeared to *help* under causal shift.

    **``spurious_strength`` sits in a window.** The moderator only exists in a
    band: spurious features must be strong enough that reversing them is
    harmful (so pruning helps under a spurious shift) and weak enough that
    they cannot substitute for the causal features (so pruning hurts under a
    causal shift). Measured, at ``shift_strength=2``:

    ==================  ==============  ============  ===========
    ``spurious_strength``  spurious shift  causal shift  moderator
    ==================  ==============  ============  ===========
    0.8                 −0.058          −0.347        absent
    1.2                 +0.061          −0.459        present
    1.6                 +0.076          −0.354        present
    2.0                 +0.022          +0.000        absent
    ==================  ==============  ============  ===========

    The default of 1.6 is the centre of the measured window. Effect sizes for
    the question bank are then set by ``shift_strength``, not by moving out of
    this band.
    """
    if shift not in {"causal", "spurious", "noise", "none"}:
        raise ValueError(f"unknown shift family {shift!r}")

    rng = np.random.default_rng(seed)
    weights = np.linspace(1.0, 0.4, n_causal)

    def draw(n: int, environment: str) -> tuple[Array, Labels]:
        deploy = environment == "deploy"
        causal = rng.normal(0.0, 1.0, size=(n, n_causal))
        if deploy and shift == "causal":
            causal[:, :n_shifted] += shift_strength

        # The label comes from the causal features as they actually are, so
        # P(y | X_causal) holds in both environments.
        y = ((causal @ weights) + rng.normal(0.0, label_noise, size=n) > 0).astype(np.int_)

        spurious = spurious_strength * (y[:, None] * 2.0 - 1.0) + rng.normal(
            0.0, 1.0, size=(n, n_spurious)
        )
        if deploy and shift == "spurious":
            # Reversal is what makes them harmful; the offset is what makes the
            # change visible to a marginal-divergence criterion. Without it the
            # marginal mean is unchanged and no pruning rule could find them.
            spurious[:, :n_shifted] = -spurious[:, :n_shifted] + shift_strength

        noise = rng.normal(0.0, 1.0, size=(n, n_noise))
        if deploy and shift == "noise":
            noise[:, :n_shifted] += shift_strength

        return np.hstack([causal, spurious, noise]).astype(np.float64), y

    x_train, y_train = draw(n_samples, "train")
    x_deploy, y_deploy = draw(n_samples, "deploy")

    names = (
        *(f"causal_{i}" for i in range(n_causal)),
        *(f"spurious_{i}" for i in range(n_spurious)),
        *(f"noise_{i}" for i in range(n_noise)),
    )
    return Dataset(
        x_train=x_train,
        y_train=y_train,
        x_deploy=x_deploy,
        y_deploy=y_deploy,
        feature_names=names,
        causal_features=tuple(f"causal_{i}" for i in range(n_causal)),
    )


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FeatureSelection:
    """A column mask, fitted on training data and applied unchanged after."""

    keep: NDArray[np.bool_]

    def apply(self, x: Array) -> Array:
        return x[:, self.keep]


@transform_op("passthrough")
def _passthrough(x_train: Array, x_deploy: Array, **_: Any) -> FeatureSelection:
    return FeatureSelection(keep=np.ones(x_train.shape[1], dtype=bool))


@transform_op("divergence_prune")
def _divergence_prune(x_train: Array, x_deploy: Array, *, k: int = 3, **_: Any) -> FeatureSelection:
    """Drop the ``k`` features whose train/deployment marginals diverge most.

    Uses the unlabelled deployment covariates only. That is legitimate
    transductive adaptation, and it is declared here rather than assumed: the
    same computation on *labelled* deployment data would be leakage.
    """
    divergence = np.abs(x_train.mean(axis=0) - x_deploy.mean(axis=0)) / (x_train.std(axis=0) + 1e-9)
    keep = np.ones(x_train.shape[1], dtype=bool)
    keep[np.argsort(divergence)[-k:]] = False
    return FeatureSelection(keep=keep)


@transform_op("random_prune")
def _random_prune(
    x_train: Array, x_deploy: Array, *, k: int = 3, seed: int = 0, **_: Any
) -> FeatureSelection:
    """Drop ``k`` features at random.

    The capacity-matched control. Without it, any effect of pruning is
    confounded with simply having fewer features — the planted defect that
    RQ-001 uses to test whether the Skeptic is real.
    """
    rng = np.random.default_rng(seed)
    keep = np.ones(x_train.shape[1], dtype=bool)
    keep[rng.choice(x_train.shape[1], size=k, replace=False)] = False
    return FeatureSelection(keep=keep)


# ---------------------------------------------------------------------------
# Estimators
# ---------------------------------------------------------------------------


@estimator_op("logistic_regression")
def _logistic(*, seed: int = 0, c: float = 1.0, max_iter: int = 500) -> BaseEstimator:
    return LogisticRegression(C=c, max_iter=max_iter, random_state=seed)


@estimator_op("gradient_boosting")
def _gbm(*, seed: int = 0, n_estimators: int = 60, max_depth: int = 3) -> BaseEstimator:
    return GradientBoostingClassifier(
        n_estimators=n_estimators, max_depth=max_depth, random_state=seed
    )


@estimator_op("random_forest")
def _forest(
    *, seed: int = 0, n_estimators: int = 100, max_depth: int | None = None
) -> BaseEstimator:
    return RandomForestClassifier(
        n_estimators=n_estimators, max_depth=max_depth, random_state=seed, n_jobs=1
    )


@estimator_op("majority_class")
def _dummy(*, seed: int = 0) -> BaseEstimator:
    """The floor. An arm that cannot beat this has not shown anything."""
    return DummyClassifier(strategy="most_frequent", random_state=seed)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@metric_op("accuracy")
def _accuracy(y_true: Labels, y_pred: Labels, _scores: Array | None = None) -> float:
    return float(accuracy_score(y_true, y_pred))


@metric_op("macro_f1")
def _macro_f1(y_true: Labels, y_pred: Labels, _scores: Array | None = None) -> float:
    return float(f1_score(y_true, y_pred, average="macro", zero_division=0))


@metric_op("balanced_accuracy")
def _balanced(y_true: Labels, y_pred: Labels, _scores: Array | None = None) -> float:
    return float(balanced_accuracy_score(y_true, y_pred))


@metric_op("roc_auc")
def _roc_auc(y_true: Labels, y_pred: Labels, scores: Array | None = None) -> float:
    if scores is None or len(np.unique(y_true)) < 2:
        return float("nan")
    return float(roc_auc_score(y_true, scores))


@metric_op("expected_calibration_error")
def _ece(y_true: Labels, y_pred: Labels, scores: Array | None = None, bins: int = 10) -> float:
    """How far predicted confidence is from observed frequency."""
    if scores is None:
        return float("nan")
    edges = np.linspace(0.0, 1.0, bins + 1)
    error = 0.0
    for low, high in pairwise(edges):
        mask = (scores > low) & (scores <= high)
        if not mask.any():
            continue
        error += mask.mean() * abs(float(scores[mask].mean()) - float(y_true[mask].mean()))
    return float(error)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def generate(name: str, **params: Any) -> Dataset:
    dataset = _lookup(_GENERATORS, name, "generator")(**params)
    assert isinstance(dataset, Dataset)
    return dataset


def transform(name: str, x_train: Array, x_deploy: Array, **params: Any) -> FeatureSelection:
    result = _lookup(_TRANSFORMS, name, "transform")(x_train, x_deploy, **params)
    return result  # type: ignore[no-any-return]


def estimator(name: str, **params: Any) -> BaseEstimator:
    return _lookup(_ESTIMATORS, name, "estimator")(**params)


def metric(name: str, y_true: Labels, y_pred: Labels, scores: Array | None = None) -> float:
    return float(_lookup(_METRICS, name, "metric")(y_true, y_pred, scores))


def _lookup(registry: dict[str, Any], name: str, kind: str) -> Any:
    try:
        return registry[name]
    except KeyError:
        raise KeyError(
            f"no {kind} named {name!r}; the operator registry is closed, and "
            f"available {kind}s are {sorted(registry)}"
        ) from None


def registered_generators() -> tuple[str, ...]:
    return tuple(sorted(_GENERATORS))


def registered_transforms() -> tuple[str, ...]:
    return tuple(sorted(_TRANSFORMS))


def registered_estimators() -> tuple[str, ...]:
    return tuple(sorted(_ESTIMATORS))


def registered_metrics() -> tuple[str, ...]:
    return tuple(sorted(_METRICS))
