"""Running a registered experiment, seed by seed.

Ties the pieces together: compile a plan for each declared seed, execute it in
the sandbox, hash whatever it produced into the artifact store, and record a
run plus its metrics through the repository — which means through the ledger.

Two rules the loop enforces rather than assumes:

**Every declared seed runs, and every one is recorded.** The seeds are fixed at
registration, so a run that reports a subset is detectable. Seed-shopping is
not prevented by asking nicely; it is prevented by the set being known in
advance and every member having a row.

**A crash in one seed does not take the others with it.** A failed seed is
recorded as a scientific failure — evidence about the design — and the rest
continue.

**Every run is charged.** Compute is the only cost a mock-driven programme
actually incurs, so a runner that recorded metrics but not seconds would leave
the research economy measuring a numerator over an empty denominator. The
charge is written from the sandbox's own telemetry, in the same transaction as
the run it belongs to.

No holdout metric is produced here. The child never sees the evaluation
sample, and :meth:`~nullius.repository.Repository.record_result` would refuse
a holdout metric from anyone but the Custodian in any case.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from nullius.build.compiler import compile_spec
from nullius.db.enums import Role, RunStatus, Split
from nullius.design.spec import ExperimentSpec
from nullius.errors import InvariantViolation
from nullius.execute.manifest import environment_hash, environment_manifest
from nullius.execute.sandbox import SandboxBackend, SandboxLimits, SandboxResult
from nullius.llm.pricing import usd_for_compute
from nullius.repository import Repository
from nullius.runtime.budget import BudgetLedger
from nullius.store.cas import ContentStore

__all__ = ["ExperimentRunner", "SeedOutcome"]


@dataclass(frozen=True, slots=True)
class SeedOutcome:
    """What one seed produced."""

    seed: int
    run_id: uuid.UUID
    status: RunStatus
    metrics: dict[str, dict[str, float]]
    """``{arm: {metric: value}}`` on the development split."""
    artifacts: dict[str, str]
    environment_hash: str
    result: SandboxResult

    @property
    def ok(self) -> bool:
        return self.status is RunStatus.COMPLETED


class ExperimentRunner:
    """Executes every seed of a registered experiment."""

    __slots__ = ("_backend", "_budget", "_repo", "_store", "_workroot")

    def __init__(
        self,
        repo: Repository,
        backend: SandboxBackend,
        store: ContentStore,
        workroot: Path,
    ) -> None:
        self._repo = repo
        self._backend = backend
        self._store = store
        self._workroot = Path(workroot)
        # Accounting is a control-plane action recorded on behalf of whichever
        # role's work incurred it, which is what WRITE_AUTHORITY says about
        # ``record_cost``. Naming that here keeps a Replicator-scoped runner
        # able to be charged without being able to charge itself anything else.
        self._budget = BudgetLedger(repo.as_role(Role.SYSTEM))

    def run(
        self,
        spec: ExperimentSpec,
        *,
        registration_id: uuid.UUID,
        bundle_id: uuid.UUID,
        dataset_id: uuid.UUID,
        program_id: uuid.UUID | None = None,
        git_commit: str = "0" * 40,
        seeds: Sequence[int] | None = None,
    ) -> list[SeedOutcome]:
        """Run the declared seeds and record each one.

        ``seeds`` selects a subset, and every member must be one the
        registration already declared. That check is the whole safety of
        adaptive seeding: escalation may decide *how many* of the preregistered
        seeds to spend, never *which*, and never one that was not named before
        anything ran.
        """
        limits = SandboxLimits(wall_seconds=spec.compute_budget_seconds)
        outcomes: list[SeedOutcome] = []

        declared = spec.seeds()
        chosen = tuple(declared) if seeds is None else tuple(seeds)
        undeclared = set(chosen) - set(declared)
        if undeclared:
            raise InvariantViolation(
                f"seeds {sorted(undeclared)} are not in this registration's declared "
                f"set; running an undeclared seed is running an unregistered experiment"
            )

        for seed in chosen:
            plan = compile_spec(spec, seed=seed)
            env_hash = environment_hash(plan=plan, isolation_tier=self._backend.tier.value)

            run = self._repo.start_run(
                registration_id=registration_id,
                bundle_id=bundle_id,
                dataset_id=dataset_id,
                seed=seed,
                environment_hash=env_hash,
                image_digest=self._backend.tier.value,
                isolation_tier=self._backend.tier.value,
                git_commit=git_commit,
                program_id=program_id,
            )

            workdir = self._workroot / str(run.run_id)
            result = self._backend.run(plan, workdir, limits)

            artifacts = self._store_outputs(result)
            manifest_hash = self._store.put_json(
                environment_manifest(plan=plan, isolation_tier=self._backend.tier.value)
            )
            artifacts["environment.json"] = manifest_hash

            status = RunStatus(result.status)
            telemetry = {
                **result.telemetry(),
                "artifacts": artifacts,
                "guard_violations": (result.results() or {}).get("guard_violations", []),
                "error": result.error(),
            }
            self._repo.finish_run(
                run.run_id, status=status, telemetry=telemetry, program_id=program_id
            )

            metrics = self._record_metrics(
                run_id=run.run_id,
                result=result,
                artifact_hash=artifacts.get("results.json", manifest_hash),
                program_id=program_id,
            )

            if program_id is not None:
                self._charge(run.run_id, result, artifacts, program_id)

            outcomes.append(
                SeedOutcome(
                    seed=seed,
                    run_id=run.run_id,
                    status=status,
                    metrics=metrics,
                    artifacts=artifacts,
                    environment_hash=env_hash,
                    result=result,
                )
            )

        return outcomes

    # ------------------------------------------------------------- helpers

    def _charge(
        self,
        run_id: uuid.UUID,
        result: SandboxResult,
        artifacts: dict[str, str],
        program_id: uuid.UUID,
    ) -> None:
        """Bill the programme for the seconds and bytes this seed used.

        A failed seed is charged too. It consumed the machine, and a ledger
        that only records successful work would make an expensive dead end
        look free — which is precisely the mistake the research economy exists
        to stop anyone making.
        """
        storage_mb = sum(
            path.stat().st_size for path in result.outputs.values() if path.exists()
        ) / (1024 * 1024)
        self._budget.record_compute_cost(
            program_id=program_id,
            run_id=run_id,
            cpu_seconds=result.wall_seconds,
            storage_mb=storage_mb,
            usd=usd_for_compute(result.wall_seconds, storage_mb),
        )

    def _store_outputs(self, result: SandboxResult) -> dict[str, str]:
        """Hash every harvested file into the content-addressed store."""
        return {name: self._store.put_file(path) for name, path in result.outputs.items()}

    def _record_metrics(
        self,
        *,
        run_id: uuid.UUID,
        result: SandboxResult,
        artifact_hash: str,
        program_id: uuid.UUID | None,
    ) -> dict[str, dict[str, float]]:
        """Record development-split metrics, one row per arm and metric.

        Metric names are qualified by arm, because a run compares several arms
        and a bare metric name would silently collide between them.
        """
        payload = result.results()
        if payload is None:
            return {}

        recorded: dict[str, dict[str, float]] = {}
        for arm_name, arm in payload.get("arms", {}).items():
            dev = arm.get("metrics", {}).get("dev", {})
            recorded[arm_name] = dict(dev)
            for metric_name, value in dev.items():
                self._repo.record_result(
                    run_id=run_id,
                    split=Split.DEV,
                    metric=f"{arm_name}.{metric_name}",
                    value=float(value),
                    artifact_hash=artifact_hash,
                    program_id=program_id,
                )
        return recorded


def summarise(outcomes: list[SeedOutcome], arm: str, metric: str) -> list[float]:
    """The per-seed values for one arm and metric, in seed order.

    Every declared seed contributes or the list is short, which is exactly the
    signal the analysis needs to refuse an experiment that lost seeds.
    """
    values: list[float] = []
    for outcome in outcomes:
        if outcome.ok and arm in outcome.metrics and metric in outcome.metrics[arm]:
            values.append(outcome.metrics[arm][metric])
    return values
