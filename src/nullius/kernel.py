"""The research kernel: one question, carried to a claim.

The loop M6 exists to demonstrate. It is deliberately linear and rule-driven —
the Director here is a policy function, not a model, because an institution
whose *orchestration* is also a language model has two sources of variance and
no way to tell them apart.

The ordering is the argument:

1. The Theorist proposes, seeing only the question.
2. The Designer designs, seeing only the hypothesis and the operator registry.
3. The design is linted. Errors block registration.
4. **The registration is locked** — hashed, timestamped, irreversible.
5. Forecasts are elicited and locked. After this point no prediction counts.
6. Every declared seed executes in the sandbox.
7. The Custodian evaluates on a sample nothing else has seen.
8. Statistics and the verdict are computed by code.
9. The Analyst interprets, in words, with the numbers already settled.
10. A claim is written with its evidence, and its confidence is computed.
11. Forecasts are scored against what happened.

Steps 3, 4, 6, 7, 8, 10 and 11 involve no model at all. That is most of the
work, and it is the part that decides what the institution believes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from nullius.analysis.confidence import ConfidenceInputs, ConfidenceReport, compute_confidence
from nullius.analysis.stats import PairedResult, paired_analysis, seed_variance
from nullius.analysis.verdict import VerdictReport, derive_verdict
from nullius.bank.items import BankItem
from nullius.build.compiler import compile_spec
from nullius.custody.custodian import HoldoutCustodian
from nullius.db.enums import AssertionKind, EvidenceKind, HypothesisState, Polarity, Role
from nullius.design.linter import LintReport, lint
from nullius.design.spec import ArmSpec, DatasetSpec, EstimatorSpec, ExperimentSpec, TransformSpec
from nullius.execute.runner import ExperimentRunner, SeedOutcome
from nullius.execute.sandbox import SandboxBackend
from nullius.forecast import score_forecasts
from nullius.llm.providers import LlmProvider
from nullius.repository import Repository
from nullius.roles.contracts import contracts_for
from nullius.roles.schemas import AnalysisNote, DesignProposal, ForecastStatement, HypothesisDraft
from nullius.runtime.contracts import TaskStatus
from nullius.runtime.worker import Worker
from nullius.store.cas import ContentStore

__all__ = ["KernelOutcome", "ResearchKernel"]

FORECASTING_ROLES = (Role.THEORIST, Role.DESIGNER, Role.ANALYST)


@dataclass(frozen=True, slots=True)
class KernelOutcome:
    """Everything one pass through the lifecycle produced."""

    item_id: str
    program_id: uuid.UUID
    hypothesis_id: uuid.UUID | None = None
    registration_id: uuid.UUID | None = None
    spec: ExperimentSpec | None = None
    lint_report: LintReport | None = None
    outcomes: list[SeedOutcome] = field(default_factory=list)
    analysis: PairedResult | None = None
    verdict: VerdictReport | None = None
    confidence: ConfidenceReport | None = None
    claim_id: uuid.UUID | None = None
    note: AnalysisNote | None = None
    usd: Decimal = Decimal(0)
    halted: str | None = None
    """Why the lifecycle stopped early, if it did. A halt is a result."""

    @property
    def completed(self) -> bool:
        return self.halted is None and self.claim_id is not None


class ResearchKernel:
    """Carries one bank item through the research lifecycle."""

    __slots__ = ("_backend", "_provider", "_repo", "_store", "_worker", "_workroot")

    def __init__(
        self,
        repo: Repository,
        provider: LlmProvider,
        backend: SandboxBackend,
        store: ContentStore,
        workroot: Path,
        *,
        mock: bool = False,
    ) -> None:
        self._repo = repo
        self._provider = provider
        self._backend = backend
        self._store = store
        self._workroot = Path(workroot)
        self._worker = Worker(repo, provider, contracts_for(mock=mock))

    # ------------------------------------------------------------------ run

    def run_item(
        self,
        item: BankItem,
        *,
        program_id: uuid.UUID,
        allowance: Decimal = Decimal("0.50"),
    ) -> KernelOutcome:
        """Take one question from proposal to claim."""
        spent = Decimal(0)

        # 1 — Theorist -------------------------------------------------------
        draft, cost = self._ask(
            Role.THEORIST,
            "v1",
            program_id,
            item.agent_view(),
            subject=("research_questions", uuid.uuid4()),
            allowance=allowance,
        )
        spent += cost
        if not isinstance(draft, HypothesisDraft):
            return KernelOutcome(item.item_id, program_id, usd=spent, halted="theorist failed")

        hypothesis = self._repo.as_role(Role.THEORIST).create_hypothesis(
            program_id=program_id,
            statement=draft.statement,
            mechanism=draft.mechanism,
            primary_metric=draft.primary_metric,
            direction=draft.direction,
            mde=draft.mde,
            falsification_condition=draft.falsification_condition,
            assumptions={"stated": draft.assumptions},
        )

        # 2 — Designer -------------------------------------------------------
        proposal, cost = self._ask(
            Role.DESIGNER,
            "v1",
            program_id,
            {"hypothesis": draft.model_dump(mode="json"), "seed_policy_minimum": 5},
            subject=("hypotheses", hypothesis.hypothesis_id),
            allowance=allowance,
        )
        spent += cost
        if not isinstance(proposal, DesignProposal):
            return KernelOutcome(
                item.item_id,
                program_id,
                hypothesis.hypothesis_id,
                usd=spent,
                halted="designer failed",
            )

        spec = self._build_spec(item, draft, proposal)

        # 3 — Lint. Errors block registration, and that is the point ---------
        report = lint(spec)
        if not report.ok:
            self._repo.as_role(Role.DIRECTOR).advance_hypothesis(
                hypothesis.hypothesis_id, HypothesisState.SHELVED
            )
            return KernelOutcome(
                item.item_id,
                program_id,
                hypothesis.hypothesis_id,
                spec=spec,
                lint_report=report,
                usd=spent,
                halted=f"design refused: {'; '.join(f.rule for f in report.errors)}",
            )

        # 4 — Register. Irreversible from here -------------------------------
        registration = self._repo.as_role(Role.DESIGNER).register(
            hypothesis_id=hypothesis.hypothesis_id,
            spec=spec.model_dump(mode="json"),
            analysis_plan={
                "test": "paired_bootstrap",
                "alpha": 0.05,
                "correction": "holm",
                "lint": report.as_dict(),
            },
            seed_root=spec.seed_root,
            n_seeds=spec.n_seeds,
            holdout_query_budget=3,
            program_id=program_id,
        )
        self._repo.as_role(Role.DIRECTOR).advance_hypothesis(
            hypothesis.hypothesis_id, HypothesisState.REGISTERED
        )

        # 5 — Forecasts, before anything runs --------------------------------
        spent += self._elicit_forecasts(
            program_id, registration.registration_id, draft, proposal, allowance
        )

        # 6 — Execute --------------------------------------------------------
        runner = ExperimentRunner(self._repo, self._backend, self._store, self._workroot)
        outcomes = runner.run(
            spec,
            registration_id=registration.registration_id,
            bundle_id=self._bundle_id(spec),
            dataset_id=self._dataset_id(item),
            program_id=program_id,
        )
        self._repo.as_role(Role.DIRECTOR).advance_hypothesis(
            hypothesis.hypothesis_id, HypothesisState.EXECUTED
        )

        completed = [o for o in outcomes if o.ok]
        if not completed:
            return KernelOutcome(
                item.item_id,
                program_id,
                hypothesis.hypothesis_id,
                registration.registration_id,
                spec,
                report,
                outcomes,
                usd=spent,
                halted="every seed failed to execute",
            )

        # 7 — Custody: one look at the evaluation split, covering every seed ---
        custodian = HoldoutCustodian(self._repo.as_role(Role.CUSTODIAN))
        custody = custodian.evaluate(
            registration_id=registration.registration_id,
            runs=[(o.run_id, compile_spec(spec, seed=o.seed)) for o in completed],
            program_id=program_id,
        )

        # 8 — Statistics and verdict, computed by code ------------------------
        baseline = custody.arm_values(spec.baseline_arm, spec.primary_metric)
        treatment = custody.arm_values(spec.treatment_arm, spec.primary_metric)
        analysis = paired_analysis(baseline, treatment)
        verdict = derive_verdict(analysis, mde=spec.mde)
        variance = seed_variance(baseline)
        self._repo.as_role(Role.DIRECTOR).advance_hypothesis(
            hypothesis.hypothesis_id, HypothesisState.ANALYZED
        )

        # 9 — The Analyst interprets, in words --------------------------------
        note, cost = self._ask(
            Role.ANALYST,
            "v1",
            program_id,
            {
                "hypothesis": draft.model_dump(mode="json"),
                "computed_statistics": analysis.as_dict(),
                "verdict": verdict.verdict.value,
                "verdict_reason": verdict.reason,
                "seed_variance": variance.as_dict(),
            },
            subject=("registrations", registration.registration_id),
            allowance=allowance,
        )
        spent += cost

        # 10 — The claim, and its computed confidence --------------------------
        claim_id, confidence = self._record_claim(
            program_id=program_id,
            hypothesis_id=hypothesis.hypothesis_id,
            spec=spec,
            analysis=analysis,
            verdict=verdict,
            variance_sd=variance.sd,
            holdout_queries=custodian.queries_consumed(registration.registration_id),
            outcomes=completed,
            note=note if isinstance(note, AnalysisNote) else None,
        )

        # 11 — Score the forecasts against what happened ----------------------
        score_forecasts(
            self._repo,
            registration_id=registration.registration_id,
            realised_effect=analysis.difference,
            mde=spec.mde,
            program_id=program_id,
        )

        return KernelOutcome(
            item_id=item.item_id,
            program_id=program_id,
            hypothesis_id=hypothesis.hypothesis_id,
            registration_id=registration.registration_id,
            spec=spec,
            lint_report=report,
            outcomes=outcomes,
            analysis=analysis,
            verdict=verdict,
            confidence=confidence,
            claim_id=claim_id,
            note=note if isinstance(note, AnalysisNote) else None,
            usd=spent,
        )

    # -------------------------------------------------------------- helpers

    def _ask(
        self,
        role: Role,
        version: str,
        program_id: uuid.UUID,
        view: dict[str, Any],
        *,
        subject: tuple[str, uuid.UUID],
        allowance: Decimal,
    ) -> tuple[Any, Decimal]:
        """Enqueue one task, run it, and return its payload and cost."""
        subject_type, subject_id = subject
        task = self._worker.queue.enqueue(
            program_id=program_id,
            role=role,
            contract_version=version,
            subject_type=subject_type,
            subject_id=subject_id,
            allowance_usd=allowance,
            view=view,
        )
        if task.status != TaskStatus.PENDING.value:
            return None, Decimal(0)

        result = self._worker.run_once(role)
        if result is None:
            return None, Decimal(0)
        return (result.payload if result.ok else None), result.usd

    def _elicit_forecasts(
        self,
        program_id: uuid.UUID,
        registration_id: uuid.UUID,
        draft: HypothesisDraft,
        proposal: DesignProposal,
        allowance: Decimal,
    ) -> Decimal:
        """Ask every forecasting role, and lock what they say."""
        view = {
            "hypothesis": draft.model_dump(mode="json"),
            "design": proposal.model_dump(mode="json"),
        }
        spent = Decimal(0)
        for role in FORECASTING_ROLES:
            statement, cost = self._ask(
                role,
                "forecast-v1",
                program_id,
                view,
                subject=("registrations", registration_id),
                allowance=allowance,
            )
            spent += cost
            if isinstance(statement, ForecastStatement):
                self._repo.as_role(role).record_forecast(
                    registration_id=registration_id,
                    p_effect_exceeds_mde=statement.p_effect_exceeds_mde,
                    predictive_mean=statement.predictive_mean,
                    predictive_sd=statement.predictive_sd,
                    p_execution_success=statement.p_execution_success,
                    program_id=program_id,
                )
        return spent

    def _build_spec(
        self, item: BankItem, draft: HypothesisDraft, proposal: DesignProposal
    ) -> ExperimentSpec:
        """Assemble the specification the Designer described.

        The Designer chooses; the compiler assembles. Notably the capacity
        control is included only if the Designer asked for it — so the linter
        gets a real chance to refuse a design that omitted it.
        """
        estimator = EstimatorSpec(op=proposal.estimator)
        arms = [
            ArmSpec(name="full", estimator=estimator),
            ArmSpec(
                name="treatment",
                transforms=(
                    TransformSpec(
                        op=proposal.treatment_transform, params={"k": proposal.treatment_k}
                    ),
                ),
                estimator=estimator,
            ),
        ]
        if proposal.include_capacity_control:
            arms.append(
                ArmSpec(
                    name="capacity_control",
                    transforms=(
                        TransformSpec(op="random_prune", params={"k": proposal.treatment_k}),
                    ),
                    estimator=estimator,
                )
            )

        return ExperimentSpec(
            title=f"{item.item_id}: {draft.statement[:120]}",
            dataset=DatasetSpec(generator="covariate_shift", params=dict(item.generator_params)),
            arms=tuple(arms),
            baseline_arm="full",
            treatment_arm="treatment",
            primary_metric=draft.primary_metric,
            direction=draft.direction,
            mde=draft.mde,
            prior_sd=draft.prior_sd,
            n_seeds=proposal.n_seeds,
            seed_root=abs(hash(item.item_id)) % 100_000,
            tuning_budget=proposal.tuning_budget,
            compute_budget_seconds=300.0,
        )

    def _record_claim(
        self,
        *,
        program_id: uuid.UUID,
        hypothesis_id: uuid.UUID,
        spec: ExperimentSpec,
        analysis: PairedResult,
        verdict: VerdictReport,
        variance_sd: float,
        holdout_queries: int,
        outcomes: list[SeedOutcome],
        note: AnalysisNote | None,
    ) -> tuple[uuid.UUID, ConfidenceReport]:
        """Write the claim, link its evidence, and compute its confidence."""
        analyst = self._repo.as_role(Role.ANALYST)
        claim = analyst.create_claim(
            program_id=program_id,
            statement=(
                note.interpretation if note else f"Verdict {verdict.verdict.value} for {spec.title}"
            ),
            kind=AssertionKind.INFERRED_CLAIM,
            hypothesis_id=hypothesis_id,
        )

        results = self._repo.results_for_run(outcomes[0].run_id)
        polarity = Polarity.SUPPORTS if analysis.difference >= 0 else Polarity.CONTRADICTS
        for row in results:
            analyst.add_evidence(
                claim_id=claim.claim_id,
                kind=EvidenceKind.EXPERIMENTAL,
                polarity=polarity,
                result_id=row.result_id,
                strength=analysis.as_dict(),
                program_id=program_id,
            )

        width = abs(analysis.ci_high - analysis.ci_low)
        confidence = compute_confidence(
            ConfidenceInputs(
                independent_replications=0,  # M7 introduces the Replicator
                effect_to_interval_ratio=(abs(analysis.difference) / width) if width else 0.0,
                seed_variance_ratio=(
                    abs(analysis.difference) / variance_sd if variance_sd else 0.0
                ),
                open_critical_objections=len(self._repo.open_critical_objections(claim.claim_id)),
                preregistered=True,
                holdout_queries_consumed=holdout_queries,
                provenance_complete=True,
                n_seeds=analysis.n_seeds,
            )
        )
        return claim.claim_id, confidence

    def _bundle_id(self, spec: ExperimentSpec) -> uuid.UUID:
        bundle = self._repo.as_role(Role.SYSTEM).record_code_bundle(
            content_hash=__import__("nullius.util.canonical", fromlist=["sha256_of"]).sha256_of(
                spec.model_dump(mode="json")
            ),
            validator_report={"compiler": "ops-registry", "codegen": False},
            passed=True,
        )
        return bundle.bundle_id

    def _dataset_id(self, item: BankItem) -> uuid.UUID:
        from nullius.util.canonical import sha256_of

        dataset = self._repo.as_role(Role.SYSTEM).record_dataset(
            name=f"bank/{item.item_id}",
            version="1",
            content_hash=sha256_of(item.generator_params),
            licence="synthetic",
        )
        return dataset.dataset_id
