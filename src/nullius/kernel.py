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

**The seam at step 5.** Everything up to and including the locked forecasts is
cheap — a handful of model calls and no sandbox. Everything after it is
expensive. That is also exactly where the research economy needs to intervene,
because expected information gain is computed *from* the forecasts and so
cannot be known any earlier. :meth:`ResearchKernel.propose` stops there and
:meth:`ResearchKernel.execute` continues from there, which lets
:class:`~nullius.economy.programme.ResearchProgramme` propose many questions,
decide between them, and pay for only the ones it funded.

:meth:`ResearchKernel.run_item` is the two composed, unconditionally. A single
question with no competitors to be weighed against needs no allocator, and
inserting one would be a decision procedure with one option.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from nullius.adversarial.detectors import Finding, run_detectors
from nullius.analysis.confidence import ConfidenceInputs, ConfidenceReport, compute_confidence
from nullius.analysis.stats import PairedResult, paired_analysis, seed_variance
from nullius.analysis.verdict import VerdictReport, derive_verdict
from nullius.bank.items import BankItem
from nullius.build.compiler import compile_spec
from nullius.custody.custodian import HoldoutCustodian
from nullius.db.enums import (
    AssertionKind,
    EvidenceKind,
    HypothesisState,
    Polarity,
    ReplicationOutcome,
    Role,
)
from nullius.design.linter import LintReport, lint
from nullius.design.spec import ArmSpec, DatasetSpec, EstimatorSpec, ExperimentSpec, TransformSpec
from nullius.execute.runner import ExperimentRunner, SeedOutcome
from nullius.execute.sandbox import SandboxBackend
from nullius.forecast import score_forecasts
from nullius.knowledge.memory import recall
from nullius.llm.providers import LlmProvider
from nullius.repository import Repository
from nullius.roles.contracts import contracts_for
from nullius.roles.schemas import AnalysisNote, DesignProposal, ForecastStatement, HypothesisDraft
from nullius.runtime.contracts import TaskStatus
from nullius.runtime.worker import Worker
from nullius.store.cas import ContentStore
from nullius.util.ids import seed_for

__all__ = ["FULL_INSTITUTION", "KernelOutcome", "Mechanisms", "Proposal", "ResearchKernel"]


@dataclass(frozen=True, slots=True)
class Mechanisms:
    """Which parts of the institution are switched on for this pass.

    The benchmark of ``docs/04-evaluation.md`` needs to run the same pipeline
    with pieces removed, and the only way an ablation means anything is if the
    arms differ in the named mechanism and in nothing else. So the switches
    live here, on the one pipeline, rather than in a second implementation
    that would differ in ways nobody enumerated.

    The defaults are what M6 shipped: registered, custodied, unchallenged.
    Existing callers therefore keep the behaviour they had.
    """

    custody: bool = True
    """Evaluate on a split nothing else has seen.

    Switched off, the verdict is computed from the development split the
    experiment was free to look at while it was being built. That is not a
    subtle difference and it is not expected to be a small one.
    """

    preregistered: bool = True
    """Whether the design was locked before execution.

    Off, the registration is still written — the ledger has no mode in which
    it forgets — but the claim is not permitted to claim the credit for it,
    and :func:`~nullius.analysis.confidence.compute_confidence` caps
    accordingly.
    """

    adversary: bool = False
    """Run the detectors, and let a critical finding block promotion."""

    replication: bool = False
    """Re-register and re-run the design independently before claiming."""

    memory: bool = False
    """Show the Theorist what this programme already believes."""


FULL_INSTITUTION = Mechanisms(
    custody=True, preregistered=True, adversary=True, replication=True, memory=True
)

_DEFAULT = Mechanisms()

FORECASTING_ROLES = (Role.THEORIST, Role.DESIGNER, Role.ANALYST)


def _dev_values(outcomes: list[SeedOutcome], arm: str, metric: str) -> list[float]:
    """One value per seed from the development split, in seed order.

    The same shape :meth:`~nullius.custody.custodian.CustodyResult.arm_values`
    returns, so the analysis downstream cannot tell which split it was handed —
    which is the point. Only the arm's switch decides, and it decides in one
    place.
    """
    return [
        outcome.metrics[arm][metric]
        for outcome in sorted(outcomes, key=lambda o: o.seed)
        if arm in outcome.metrics and metric in outcome.metrics[arm]
    ]


def _aggregate(per_arm: dict[str, list[float]], metric: str) -> dict[str, dict[str, float]]:
    """Seed-wise values reduced to the mean and spread the detectors read.

    ``metric__sd`` is the run-to-run spread the ``seed_instability`` detector
    looks for; without it that detector silently never fires, which would be a
    detector that passes by not looking.
    """
    aggregated: dict[str, dict[str, float]] = {}
    for arm, values in per_arm.items():
        if not values:
            continue
        mean = sum(values) / len(values)
        if len(values) > 1:
            variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        else:
            variance = 0.0
        aggregated[arm] = {metric: mean, f"{metric}__sd": math.sqrt(variance)}
    return aggregated


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
    findings: tuple[Finding, ...] = ()
    """What the detectors raised. Empty when no adversary was switched on."""
    replications: int = 0
    """Independent reproductions that agreed. Not attempts — agreements."""
    halted: str | None = None
    """Why the lifecycle stopped early, if it did. A halt is a result."""

    @property
    def completed(self) -> bool:
        return self.halted is None and self.claim_id is not None


@dataclass(frozen=True, slots=True)
class Proposal:
    """A registered, forecast question that has not yet cost anything to run.

    The unit the research economy allocates over. Everything here is already
    irreversible — the registration is hashed and locked, the forecasts cannot
    be revised — so a proposal that is never funded still leaves a complete
    record of what was going to be tried and what the roles expected of it.
    That is the point: an institution that discards its unfunded proposals
    cannot tell you what it chose not to learn.
    """

    item: BankItem
    program_id: uuid.UUID
    usd: Decimal = Decimal(0)
    hypothesis_id: uuid.UUID | None = None
    registration_id: uuid.UUID | None = None
    spec: ExperimentSpec | None = None
    lint_report: LintReport | None = None
    draft: HypothesisDraft | None = None
    halted: str | None = None

    @property
    def fundable(self) -> bool:
        """Whether there is anything here an allocator could choose to pay for."""
        return self.halted is None and self.registration_id is not None and self.spec is not None

    def outcome(self, *, halted: str | None = None) -> KernelOutcome:
        """This proposal as a terminal result, for one that is never executed."""
        return KernelOutcome(
            item_id=self.item.item_id,
            program_id=self.program_id,
            hypothesis_id=self.hypothesis_id,
            registration_id=self.registration_id,
            spec=self.spec,
            lint_report=self.lint_report,
            usd=self.usd,
            halted=halted or self.halted or "not funded",
        )


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
        mechanisms: Mechanisms = _DEFAULT,
    ) -> KernelOutcome:
        """Take one question from proposal to claim.

        The two halves composed, with no allocation step between them. One
        question weighed against nothing is not a choice, and an allocator
        invoked here would be a decision procedure with a single option.
        """
        proposal = self.propose(
            item, program_id=program_id, allowance=allowance, mechanisms=mechanisms
        )
        if not proposal.fundable:
            return proposal.outcome()
        return self.execute(proposal, allowance=allowance, mechanisms=mechanisms)

    # ------------------------------------------------------------- propose

    def propose(
        self,
        item: BankItem,
        *,
        program_id: uuid.UUID,
        allowance: Decimal = Decimal("0.50"),
        mechanisms: Mechanisms = _DEFAULT,
    ) -> Proposal:
        """Steps 1 to 5: hypothesis, design, lint, registration, locked forecasts.

        Stops before anything is executed. Everything done here is cheap and
        everything after it is not, and the forecasts this ends with are what
        the allocator needs in order to decide whether the expensive part is
        worth paying for.
        """
        spent = Decimal(0)

        # 1 - Theorist -------------------------------------------------------
        # With memory on, the Theorist sees what the lab already came to
        # believe. Deliberately its *claims*, not the bank's truth: memory that
        # could only ever be right would be an oracle wearing a hat. Lab scope
        # rather than programme scope, because one programme is one question and
        # memory that could not cross a question would not be memory.
        view = dict(item.agent_view())
        if mechanisms.memory:
            view["established_claims"] = [
                recollection.as_dict()
                for recollection in recall(self._repo.session, program_id=program_id, scope="lab")
            ]
        draft, cost = self._ask(
            Role.THEORIST,
            "v1",
            program_id,
            view,
            subject=("research_questions", uuid.uuid4()),
            allowance=allowance,
        )
        spent += cost
        if not isinstance(draft, HypothesisDraft):
            return Proposal(item, program_id, usd=spent, halted="theorist failed")

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

        # 2 - Designer -------------------------------------------------------
        design, cost = self._ask(
            Role.DESIGNER,
            "v1",
            program_id,
            {"hypothesis": draft.model_dump(mode="json"), "seed_policy_minimum": 5},
            subject=("hypotheses", hypothesis.hypothesis_id),
            allowance=allowance,
        )
        spent += cost
        if not isinstance(design, DesignProposal):
            return Proposal(
                item,
                program_id,
                usd=spent,
                hypothesis_id=hypothesis.hypothesis_id,
                draft=draft,
                halted="designer failed",
            )

        spec = self._build_spec(item, draft, design)

        # 3 - Lint. Errors block registration, and that is the point ---------
        report = lint(spec)
        if not report.ok:
            self._repo.as_role(Role.DIRECTOR).advance_hypothesis(
                hypothesis.hypothesis_id, HypothesisState.SHELVED
            )
            refusal = "; ".join(f.rule for f in report.errors)
            return Proposal(
                item,
                program_id,
                usd=spent,
                hypothesis_id=hypothesis.hypothesis_id,
                spec=spec,
                lint_report=report,
                draft=draft,
                halted=f"design refused: {refusal}",
            )

        # 4 - Register. Irreversible from here --------------------------------
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

        # 5 - Forecasts, before anything runs ---------------------------------
        spent += self._elicit_forecasts(
            program_id, registration.registration_id, draft, design, allowance
        )

        return Proposal(
            item=item,
            program_id=program_id,
            usd=spent,
            hypothesis_id=hypothesis.hypothesis_id,
            registration_id=registration.registration_id,
            spec=spec,
            lint_report=report,
            draft=draft,
        )

    # ------------------------------------------------------------- execute

    def execute(
        self,
        proposal: Proposal,
        *,
        allowance: Decimal = Decimal("0.50"),
        mechanisms: Mechanisms = _DEFAULT,
    ) -> KernelOutcome:
        """Steps 6 to 11: run every seed, take custody, analyse, claim, score.

        Everything expensive lives here, which is why an allocator gets to
        stand in front of it.
        """
        if not proposal.fundable:
            raise ValueError(f"{proposal.item.item_id}: nothing here to execute")

        # Guaranteed by ``fundable``; restated so the type checker agrees.
        spec = proposal.spec
        registration_id = proposal.registration_id
        hypothesis_id = proposal.hypothesis_id
        assert spec is not None
        assert registration_id is not None
        assert hypothesis_id is not None

        item = proposal.item
        draft = proposal.draft
        program_id = proposal.program_id
        spent = proposal.usd

        # 6 - Execute ---------------------------------------------------------
        runner = ExperimentRunner(self._repo, self._backend, self._store, self._workroot)
        outcomes = runner.run(
            spec,
            registration_id=registration_id,
            bundle_id=self._bundle_id(spec),
            dataset_id=self._dataset_id(item),
            program_id=program_id,
        )
        self._repo.as_role(Role.DIRECTOR).advance_hypothesis(
            hypothesis_id, HypothesisState.EXECUTED
        )

        completed = [o for o in outcomes if o.ok]
        if not completed:
            return KernelOutcome(
                item.item_id,
                program_id,
                hypothesis_id,
                registration_id,
                spec,
                proposal.lint_report,
                outcomes,
                usd=spent,
                halted="every seed failed to execute",
            )

        # 7 - Custody: one look at the evaluation split, covering every seed ---
        holdout_queries = 0
        if mechanisms.custody:
            custodian = HoldoutCustodian(self._repo.as_role(Role.CUSTODIAN), self._store)
            custody = custodian.evaluate(
                registration_id=registration_id,
                runs=[(o.run_id, compile_spec(spec, seed=o.seed)) for o in completed],
                program_id=program_id,
            )
            baseline = custody.arm_values(spec.baseline_arm, spec.primary_metric)
            treatment = custody.arm_values(spec.treatment_arm, spec.primary_metric)
            holdout_queries = custodian.queries_consumed(registration_id)
        else:
            # No Custodian means the verdict is read off the development split —
            # the same numbers the design was free to inspect while it was being
            # written. Nothing here is cheating; this is simply what an
            # institution without an evaluation firewall is able to see, and the
            # benchmark exists to price the difference.
            baseline = _dev_values(completed, spec.baseline_arm, spec.primary_metric)
            treatment = _dev_values(completed, spec.treatment_arm, spec.primary_metric)

        # 8 - Statistics and verdict, computed by code -------------------------
        analysis = paired_analysis(baseline, treatment)
        verdict = derive_verdict(analysis, mde=spec.mde)
        variance = seed_variance(baseline)
        self._repo.as_role(Role.DIRECTOR).advance_hypothesis(
            hypothesis_id, HypothesisState.ANALYZED
        )

        # 8b - The adversary, if this arm has one ------------------------------
        findings: list[Finding] = []
        if mechanisms.adversary:
            findings = run_detectors(
                spec,
                _aggregate(
                    {spec.baseline_arm: baseline, spec.treatment_arm: treatment},
                    spec.primary_metric,
                ),
            )
            skeptic = self._repo.as_role(Role.SKEPTIC)
            for finding in findings:
                skeptic.raise_objection(
                    target_type="registrations",
                    target_id=registration_id,
                    objection_type=finding.objection_type,
                    severity=finding.severity,
                    statement=finding.statement,
                    discriminating_test=finding.discriminating_test,
                    program_id=program_id,
                )

        # 8c - Independent replication, if this arm requires one ----------------
        replications = 0
        if mechanisms.replication:
            replications = self._replicate(
                spec,
                item=item,
                hypothesis_id=hypothesis_id,
                original_registration_id=registration_id,
                program_id=program_id,
                original_verdict=verdict,
            )

        # 9 - The Analyst interprets, in words ---------------------------------
        note, cost = self._ask(
            Role.ANALYST,
            "v1",
            program_id,
            {
                "hypothesis": draft.model_dump(mode="json") if draft else {},
                "computed_statistics": analysis.as_dict(),
                "verdict": verdict.verdict.value,
                "verdict_reason": verdict.reason,
                "seed_variance": variance.as_dict(),
            },
            subject=("registrations", registration_id),
            allowance=allowance,
        )
        spent += cost

        # 10 - The claim, and its computed confidence --------------------------
        claim_id, confidence = self._record_claim(
            program_id=program_id,
            hypothesis_id=hypothesis_id,
            spec=spec,
            analysis=analysis,
            verdict=verdict,
            variance_sd=variance.sd,
            holdout_queries=holdout_queries,
            outcomes=completed,
            note=note if isinstance(note, AnalysisNote) else None,
            registration_id=registration_id,
            preregistered=mechanisms.preregistered,
            replications=replications,
        )

        # 11 - Score the forecasts against what happened -----------------------
        score_forecasts(
            self._repo,
            registration_id=registration_id,
            realised_effect=analysis.difference,
            mde=spec.mde,
            program_id=program_id,
        )

        return KernelOutcome(
            item_id=item.item_id,
            program_id=program_id,
            hypothesis_id=hypothesis_id,
            registration_id=registration_id,
            spec=spec,
            lint_report=proposal.lint_report,
            outcomes=outcomes,
            analysis=analysis,
            verdict=verdict,
            confidence=confidence,
            claim_id=claim_id,
            note=note if isinstance(note, AnalysisNote) else None,
            usd=spent,
            findings=tuple(findings),
            replications=replications,
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
            seed_root=seed_for(item.item_id),
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
        registration_id: uuid.UUID | None = None,
        preregistered: bool = True,
        replications: int = 0,
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

        # An objection raised by the detectors targets the registration, because
        # it is a complaint about the experiment and exists before any claim
        # does. Both are counted: a critical objection bars promotion wherever
        # it was filed.
        blocking = len(self._repo.open_critical_objections(claim.claim_id))
        if registration_id is not None:
            blocking += len(self._repo.open_critical_objections(registration_id))

        width = abs(analysis.ci_high - analysis.ci_low)
        confidence = compute_confidence(
            ConfidenceInputs(
                independent_replications=replications,
                effect_to_interval_ratio=(abs(analysis.difference) / width) if width else 0.0,
                seed_variance_ratio=(
                    abs(analysis.difference) / variance_sd if variance_sd else 0.0
                ),
                open_critical_objections=blocking,
                preregistered=preregistered,
                holdout_queries_consumed=holdout_queries,
                provenance_complete=self._provenance_resolves(outcomes),
                n_seeds=analysis.n_seeds,
            )
        )

        # Computing a confidence and not storing it leaves the ledger asserting
        # `speculative` about everything, which is what M11's report found by
        # re-deriving instead of displaying. Promotion goes through the write
        # path rather than assigning the column, so the ledger's own rules --
        # evidence must exist, no open critical objection, an independent
        # reproduction before the top level -- get their say. The rubric and the
        # write path were built to agree: `compute_confidence` caps at
        # `contested` exactly when an objection is open, and reaches
        # `well_supported` only with a replication, which are the two conditions
        # `promote_claim` enforces. If they ever disagree, this raises rather
        # than silently keeping the lower value.
        self._repo.as_role(Role.DIRECTOR).promote_claim(
            claim.claim_id, confidence.confidence, program_id=program_id
        )
        return claim.claim_id, confidence

    def _replicate(
        self,
        spec: ExperimentSpec,
        *,
        item: BankItem,
        hypothesis_id: uuid.UUID,
        original_registration_id: uuid.UUID,
        program_id: uuid.UUID,
        original_verdict: VerdictReport,
    ) -> int:
        """Re-register the design and run it again, independently.

        Three things make this a replication rather than a rerun.

        **Fresh seeds.** The replication registers the same design with a
        different ``seed_root``, so it draws different data. Re-executing the
        identical seeds would test that the code is deterministic, which is
        already known and is not what replication means.

        **A separate registration.** The ledger refuses a replication whose
        original and replication registrations are the same row, so the second
        run is preregistered in its own right and gets its own custody budget.
        The Designer writes it, because registering a design is the Designer's
        act and widening that authority to the Replicator would trade a real
        separation for a cosmetic one. The Replicator's independence is not in
        who filed the paperwork; it is in executing the runs and being unable
        to read the original's results while doing so.

        **The Replicator's own hands.** The runs are executed as
        :attr:`~nullius.db.enums.Role.REPLICATOR`, and that role's read
        methods are filtered to its own runs. It cannot see the original's
        results while producing its own, so agreement is not conformity.

        Returns the number of replications that *agreed* — zero or one. A
        failed replication is recorded just as loudly and returns zero, which
        leaves the claim short of the top confidence level. That is the
        mechanism working, not the mechanism failing.
        """
        replication_spec = spec.model_copy(
            update={"seed_root": seed_for(f"replication/{item.item_id}/{original_registration_id}")}
        )
        registration = self._repo.as_role(Role.DESIGNER).register(
            hypothesis_id=hypothesis_id,
            spec=replication_spec.model_dump(mode="json"),
            analysis_plan={
                "test": "paired_bootstrap",
                "alpha": 0.05,
                "correction": "holm",
                "replication_of": str(original_registration_id),
            },
            seed_root=replication_spec.seed_root,
            n_seeds=replication_spec.n_seeds,
            holdout_query_budget=3,
            program_id=program_id,
        )

        replicator = self._repo.as_role(Role.REPLICATOR)
        runner = ExperimentRunner(replicator, self._backend, self._store, self._workroot)
        outcomes = runner.run(
            replication_spec,
            registration_id=registration.registration_id,
            bundle_id=self._bundle_id(replication_spec),
            dataset_id=self._dataset_id(item),
            program_id=program_id,
        )
        completed = [o for o in outcomes if o.ok]
        if not completed:
            self._repo.as_role(Role.REPLICATOR).record_replication(
                original_registration_id=original_registration_id,
                replication_registration_id=registration.registration_id,
                outcome=ReplicationOutcome.INCONCLUSIVE,
                concordance={"reason": "every seed of the replication failed to execute"},
                program_id=program_id,
            )
            return 0

        custody = HoldoutCustodian(self._repo.as_role(Role.CUSTODIAN), self._store).evaluate(
            registration_id=registration.registration_id,
            runs=[(o.run_id, compile_spec(replication_spec, seed=o.seed)) for o in completed],
            program_id=program_id,
        )
        analysis = paired_analysis(
            custody.arm_values(replication_spec.baseline_arm, replication_spec.primary_metric),
            custody.arm_values(replication_spec.treatment_arm, replication_spec.primary_metric),
        )
        verdict = derive_verdict(analysis, mde=replication_spec.mde)

        agreed = verdict.verdict is original_verdict.verdict
        outcome = ReplicationOutcome.REPLICATED if agreed else ReplicationOutcome.FAILED_REPLICATION
        self._repo.as_role(Role.REPLICATOR).record_replication(
            original_registration_id=original_registration_id,
            replication_registration_id=registration.registration_id,
            outcome=outcome,
            concordance={
                "original_verdict": original_verdict.verdict.value,
                "replication_verdict": verdict.verdict.value,
                "replication_effect": round(analysis.difference, 6),
                "seed_root": replication_spec.seed_root,
            },
            program_id=program_id,
        )
        return 1 if agreed else 0

    def _provenance_resolves(self, outcomes: list[SeedOutcome]) -> bool:
        """Whether every artifact on the evidence chain is actually in the store.

        This used to be the literal ``True``. The confidence rubric's whole
        design is that each of its inputs is a fact an agent cannot declare, and
        one of them was being declared -- by the kernel, on the system's own
        behalf, every time. M11's report caught it by re-deriving from the
        ledger, and what it found underneath was that the Custodian had been
        naming holdout artifacts it never wrote, so the cap this input exists to
        apply had never once fired.
        """
        hashes = {
            row.artifact_hash
            for outcome in outcomes
            for row in self._repo.results_for_run(outcome.run_id)
            if row.artifact_hash
        }
        return all(self._store.exists(digest) for digest in hashes)

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
