"""M3 acceptance: a spec compiles, runs sandboxed, and reruns identically.

Marked ``isolation`` where the test proves a boundary holds, so those run as a
separate CI job — a sandbox failure should never be mistaken for an ordinary
test failure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nullius.build.compiler import compile_all, compile_spec
from nullius.build.ops import Dataset, generate
from nullius.db.enums import Split
from nullius.db.tables import RunResult
from nullius.design.spec import ArmSpec, DatasetSpec, EstimatorSpec, ExperimentSpec, TransformSpec
from nullius.execute.manifest import environment_hash, environment_manifest
from nullius.execute.runner import ExperimentRunner, summarise
from nullius.execute.sandbox import SandboxLimits, SubprocessSandbox
from nullius.ledger.rebuild import reconciliation
from nullius.repository import Repository
from nullius.store.cas import ContentStore
from tests.conftest import Scaffold, make_hypothesis

LOGREG = EstimatorSpec(op="logistic_regression")

SPEC = ExperimentSpec(
    title="Divergence pruning under spurious covariate shift",
    dataset=DatasetSpec(
        generator="covariate_shift", params={"shift": "spurious", "n_samples": 600}
    ),
    arms=(
        ArmSpec(name="full", estimator=LOGREG),
        ArmSpec(
            name="prune",
            transforms=(TransformSpec(op="divergence_prune", params={"k": 3}),),
            estimator=LOGREG,
        ),
        ArmSpec(
            name="random",
            transforms=(TransformSpec(op="random_prune", params={"k": 3}),),
            estimator=LOGREG,
        ),
    ),
    baseline_arm="full",
    treatment_arm="prune",
    primary_metric="macro_f1",
    secondary_metrics=("accuracy",),
    direction="increase",
    mde=0.02,
    prior_sd=0.012,
    n_seeds=3,
    seed_root=48192,
    tuning_budget=4,
    compute_budget_seconds=120.0,
)


# ---------------------------------------------------------------------------
# The compiler
# ---------------------------------------------------------------------------


def test_a_plan_is_plain_json_with_no_code_in_it() -> None:
    """The plan crosses a process boundary; anything executable would be a hole."""
    plan = compile_spec(SPEC, seed=SPEC.seeds()[0])
    round_tripped = json.loads(json.dumps(plan))
    assert round_tripped == plan
    assert set(plan) >= {"arms", "dataset", "metrics", "seed", "primary_metric"}


def test_a_seed_outside_the_registration_cannot_be_compiled() -> None:
    """Seeds are fixed at registration so that reporting all of them is checkable."""
    with pytest.raises(ValueError, match="not among the seeds"):
        compile_spec(SPEC, seed=999_999)


def test_one_plan_per_declared_seed() -> None:
    plans = compile_all(SPEC)
    assert [p["seed"] for p in plans] == list(SPEC.seeds())


# ---------------------------------------------------------------------------
# Acceptance 1 — compiles, runs sandboxed, emits hashed artifacts and telemetry
# ---------------------------------------------------------------------------


@pytest.fixture
def runner(repo: Repository, tmp_path: Path) -> ExperimentRunner:
    return ExperimentRunner(
        repo, SubprocessSandbox(), ContentStore(tmp_path / "objects"), tmp_path / "runs"
    )


def _registered(repo: Repository, scaffold: Scaffold) -> tuple[object, object, object]:
    hypothesis_id = make_hypothesis(repo, scaffold)
    dataset = repo.record_dataset(
        name="covariate_shift", version="1", content_hash="a" * 64, licence="synthetic"
    )
    bundle = repo.record_code_bundle(
        content_hash="b" * 64, validator_report={"compiler": "ops-registry"}, passed=True
    )
    registration = repo.register(
        hypothesis_id=hypothesis_id,
        spec=SPEC.model_dump(mode="json"),
        analysis_plan={"test": "paired_bootstrap", "alpha": 0.05},
        seed_root=SPEC.seed_root,
        n_seeds=SPEC.n_seeds,
        holdout_query_budget=3,
    )
    return registration, bundle, dataset


@pytest.mark.slow
def test_an_experiment_runs_end_to_end(
    repo: Repository, scaffold: Scaffold, runner: ExperimentRunner, tmp_path: Path
) -> None:
    registration, bundle, dataset = _registered(repo, scaffold)

    outcomes = runner.run(
        SPEC,
        registration_id=registration.registration_id,  # type: ignore[attr-defined]
        bundle_id=bundle.bundle_id,  # type: ignore[attr-defined]
        dataset_id=dataset.dataset_id,  # type: ignore[attr-defined]
        program_id=scaffold.program_id,
    )
    repo.commit()

    assert len(outcomes) == SPEC.n_seeds, "every declared seed must be recorded"
    assert all(o.ok for o in outcomes), [o.result.stderr for o in outcomes if not o.ok]

    for outcome in outcomes:
        assert set(outcome.metrics) == {"full", "prune", "random"}
        assert 0.0 <= outcome.metrics["full"]["macro_f1"] <= 1.0
        # Artifacts are hashed into the content store, not just written to disk.
        assert "results.json" in outcome.artifacts
        assert "environment.json" in outcome.artifacts
        assert len(outcome.artifacts["results.json"]) == 64
        assert outcome.result.telemetry()["wall_seconds"] > 0

    # The ledger holds it all, and still reconciles.
    assert repo.ledger.verify().ok
    assert reconciliation(repo.session).ok


@pytest.mark.slow
def test_no_holdout_metric_is_produced_by_a_run(
    repo: Repository, scaffold: Scaffold, runner: ExperimentRunner
) -> None:
    """The experiment never sees the evaluation sample, so it cannot report on it."""
    import sqlalchemy as sa

    registration, bundle, dataset = _registered(repo, scaffold)
    runner.run(
        SPEC,
        registration_id=registration.registration_id,  # type: ignore[attr-defined]
        bundle_id=bundle.bundle_id,  # type: ignore[attr-defined]
        dataset_id=dataset.dataset_id,  # type: ignore[attr-defined]
        program_id=scaffold.program_id,
    )
    repo.commit()

    splits = set(repo.session.scalars(sa.select(RunResult.split)))
    assert splits == {Split.DEV}


@pytest.mark.slow
def test_metrics_are_recorded_per_arm(
    repo: Repository, scaffold: Scaffold, runner: ExperimentRunner
) -> None:
    """A bare metric name would silently collide between arms."""
    import sqlalchemy as sa

    registration, bundle, dataset = _registered(repo, scaffold)
    outcomes = runner.run(
        SPEC,
        registration_id=registration.registration_id,  # type: ignore[attr-defined]
        bundle_id=bundle.bundle_id,  # type: ignore[attr-defined]
        dataset_id=dataset.dataset_id,  # type: ignore[attr-defined]
        program_id=scaffold.program_id,
    )
    repo.commit()

    names = set(repo.session.scalars(sa.select(RunResult.metric)))
    assert "prune.macro_f1" in names
    assert "full.macro_f1" in names
    assert "random.macro_f1" in names

    values = summarise(outcomes, "prune", "macro_f1")
    assert len(values) == SPEC.n_seeds


# ---------------------------------------------------------------------------
# Acceptance 3 — determinism
# ---------------------------------------------------------------------------


def test_the_same_seed_yields_the_same_data() -> None:
    import numpy as np

    first = generate("covariate_shift", seed=7, n_samples=200)
    second = generate("covariate_shift", seed=7, n_samples=200)
    assert isinstance(first, Dataset)
    assert np.array_equal(first.x_train, second.x_train)
    assert np.array_equal(first.y_deploy, second.y_deploy)

    different = generate("covariate_shift", seed=8, n_samples=200)
    assert not np.array_equal(first.x_train, different.x_train)


def test_the_environment_hash_is_stable_and_sensitive() -> None:
    plan = compile_spec(SPEC, seed=SPEC.seeds()[0])
    a = environment_hash(plan=plan, isolation_tier="subprocess")
    assert a == environment_hash(plan=plan, isolation_tier="subprocess")
    assert a != environment_hash(plan=plan, isolation_tier="docker")

    other = compile_spec(SPEC, seed=SPEC.seeds()[1])
    assert a != environment_hash(plan=other, isolation_tier="subprocess")


def test_the_manifest_holds_only_what_could_change_a_result() -> None:
    """Wall-clock or hostname would make every hash unique, which is no hash."""
    manifest = environment_manifest(
        plan=compile_spec(SPEC, seed=SPEC.seeds()[0]), isolation_tier="subprocess"
    )
    assert set(manifest["packages"]) == {"numpy", "scipy", "scikit-learn", "pandas"}
    assert manifest["blas_threads"] == 1
    text = json.dumps(manifest)
    for volatile in ("timestamp", "hostname", "cwd", "pid"):
        assert volatile not in text


@pytest.mark.slow
def test_two_runs_of_one_seed_produce_identical_metrics(tmp_path: Path) -> None:
    """Bitwise reruns are what makes replication meaningful rather than approximate."""
    sandbox = SubprocessSandbox()
    plan = compile_spec(SPEC, seed=SPEC.seeds()[0])
    limits = SandboxLimits(wall_seconds=120.0)

    first = sandbox.run(plan, tmp_path / "a", limits)
    second = sandbox.run(plan, tmp_path / "b", limits)

    assert first.ok and second.ok, (first.stderr, second.stderr)
    assert first.results() is not None

    def metrics(result: object) -> dict[str, object]:
        payload = result.results()  # type: ignore[attr-defined]
        return {name: arm["metrics"] for name, arm in payload["arms"].items()}

    assert metrics(first) == metrics(second)


# ---------------------------------------------------------------------------
# What the generator actually does
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_generator_behaviour_is_pinned() -> None:
    """Pins the measured direction of the effect for each shift family.

    Not an aspiration — a record. Three of the four families behave as RQ-001
    expects; the ``causal`` family does not, because the spurious features in
    this generator substitute for the causal ones almost perfectly, so
    dropping the causal features costs nothing.

    This test exists so that fixing the data generating process in M4 is a
    conscious act with a visible diff, rather than a quiet parameter tweak
    that makes the ground truth agree with whatever was hoped for.
    """
    import statistics

    from nullius.build import ops

    def effect(shift: str) -> float:
        differences = []
        for seed in range(8):
            data = ops.generate(
                "covariate_shift", seed=seed, n_samples=1200, shift=shift, shift_strength=2.0
            )
            scores = {}
            for arm, op in (("full", "passthrough"), ("prune", "divergence_prune")):
                selection = ops.transform(op, data.x_train, data.x_deploy, k=3, seed=seed)
                model = ops.estimator("logistic_regression", seed=seed)
                model.fit(data.x_train[:, selection.keep], data.y_train)
                scores[arm] = ops.metric(
                    "macro_f1", data.y_deploy, model.predict(data.x_deploy[:, selection.keep])
                )
            differences.append(scores["prune"] - scores["full"])
        return statistics.mean(differences)

    assert effect("spurious") > 0.30, "pruning should clearly help when only spurious shift"
    assert abs(effect("noise")) < 0.05, "shifting irrelevant features should change nothing"
    assert abs(effect("none")) < 0.05, "no shift should mean no effect"

    # The known gap. RQ-001 requires this to be negative; it is not, and the
    # reason is documented on the generator. Assert the current truth so the
    # M4 fix has to change this line.
    assert effect("causal") > 0, (
        "KNOWN GAP: pruning still helps under causal shift because the spurious "
        "features substitute for the causal ones. See the covariate_shift docstring."
    )
