"""The role-scoped write path.

Every mutation of institutional state goes through here, and every mutation
does three things atomically: check that the acting role has authority, write
the domain row, and append the event that records it. There is no other way in.

Two enforcement layers, deliberately redundant:

*Authority* is checked here, in Python, against :data:`WRITE_AUTHORITY`. This
is what gives roles different *capabilities* rather than different
instructions — the Replicator cannot record an original run because the method
refuses, not because its prompt asks it not to.

*Invariants* are checked here **and** by database triggers. The duplication is
the point: the Python check produces a good error message, and the trigger
means the rule still holds when someone reaches past this module with raw SQL.
A rule enforced only by the layer that wants to break it is not enforced.

Reads are audited. Where row-level security is unavailable (ADR-0001), the
:class:`~nullius.db.tables.QueryAudit` trail is how Replicator blindness is
*proven* rather than asserted.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any, TypeVar

import sqlalchemy as sa
from sqlalchemy.orm import Session

from nullius.db.enums import (
    AssertionKind,
    ClaimConfidence,
    ComputedBy,
    DerivationKind,
    EvidenceKind,
    HypothesisState,
    ObjectionSeverity,
    ObjectionStatus,
    ObjectionType,
    Polarity,
    RegistrationKind,
    Role,
    RunStatus,
    Split,
)
from nullius.db.rows import entity_row
from nullius.db.tables import (
    Base,
    Claim,
    CodeBundle,
    CostEntry,
    Dataset,
    Evidence,
    Forecast,
    HoldoutQuery,
    Hypothesis,
    Lab,
    LlmCall,
    Objection,
    Policy,
    Program,
    QueryAudit,
    Registration,
    ResearchQuestion,
    Run,
    RunResult,
)
from nullius.errors import AuthorityError, InvariantViolation
from nullius.ledger.ledger import Ledger
from nullius.util.canonical import canonical_json, sha256_of
from nullius.util.clock import Clock, SystemClock
from nullius.util.ids import IdGenerator, RandomIds

__all__ = ["WRITE_AUTHORITY", "Repository"]

T = TypeVar("T", bound=Base)

_ANY_ROLE = frozenset(Role)

WRITE_AUTHORITY: dict[str, frozenset[Role]] = {
    # Institutional setup is the control plane's business.
    "create_lab": frozenset({Role.SYSTEM}),
    "create_policy": frozenset({Role.SYSTEM, Role.DIRECTOR}),
    "create_research_question": frozenset({Role.SYSTEM, Role.DIRECTOR}),
    "create_program": frozenset({Role.SYSTEM, Role.DIRECTOR}),
    # Science.
    "create_hypothesis": frozenset({Role.SYSTEM, Role.THEORIST}),
    "advance_hypothesis": frozenset({Role.SYSTEM, Role.DIRECTOR}),
    "register": frozenset({Role.SYSTEM, Role.DESIGNER}),
    # Every role forecasts; that is the point of the Forecast Ledger.
    "record_forecast": _ANY_ROLE,
    "record_dataset": frozenset({Role.SYSTEM}),
    "record_code_bundle": frozenset({Role.SYSTEM, Role.BUILDER}),
    # The Replicator executes its own runs and nobody else's.
    "start_run": frozenset({Role.SYSTEM, Role.REPLICATOR}),
    "finish_run": frozenset({Role.SYSTEM, Role.REPLICATOR}),
    "record_result": frozenset({Role.SYSTEM, Role.CUSTODIAN, Role.REPLICATOR}),
    "create_claim": frozenset({Role.SYSTEM, Role.ANALYST}),
    "add_evidence": frozenset({Role.SYSTEM, Role.ANALYST}),
    "raise_objection": frozenset({Role.SYSTEM, Role.SKEPTIC, Role.REVIEWER}),
    # Accounting is a control-plane action, recorded on behalf of whichever
    # role's task incurred it.
    "record_cost": frozenset({Role.SYSTEM}),
    # Only the Custodian looks at the evaluation split, so only the
    # Custodian can record having looked.
    "record_holdout_query": frozenset({Role.CUSTODIAN}),
    "record_llm_call": frozenset({Role.SYSTEM}),
}
"""Which roles may perform which operation.

Authority, not etiquette: an operation missing from a role's set raises
:class:`~nullius.errors.AuthorityError` before anything is written.
"""


def _jsonable(value: Any) -> Any:
    """Round-trip through canonical JSON so payloads store and hash identically."""
    return json.loads(canonical_json(value))


class Repository:
    """A session bound to one acting role."""

    __slots__ = ("_clock", "_ids", "_ledger", "_policy_id", "_session", "_task_id", "role")

    def __init__(
        self,
        session: Session,
        role: Role,
        *,
        clock: Clock | None = None,
        ids: IdGenerator | None = None,
        task_id: uuid.UUID | None = None,
        policy_id: uuid.UUID | None = None,
    ) -> None:
        self._session = session
        self.role = role
        self._clock = clock or SystemClock()
        self._ids = ids or RandomIds()
        self._task_id = task_id
        self._policy_id = policy_id
        self._ledger = Ledger(session, clock=self._clock, ids=self._ids)

    # ------------------------------------------------------------ plumbing

    @property
    def ledger(self) -> Ledger:
        return self._ledger

    @property
    def session(self) -> Session:
        return self._session

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def ids(self) -> IdGenerator:
        return self._ids

    def as_role(self, role: Role) -> Repository:
        """The same session, clock and identifier stream, acting as ``role``.

        How the institution switches actors. Roles differ by capability, so
        handing work to another role means constructing its repository, not
        relaxing a check.
        """
        return Repository(
            self._session,
            role,
            clock=self._clock,
            ids=self._ids,
            task_id=self._task_id,
            policy_id=self._policy_id,
        )

    def _authorise(self, operation: str) -> None:
        permitted = WRITE_AUTHORITY.get(operation)
        if permitted is None:
            raise AuthorityError(
                f"operation {operation!r} has no authority entry; every write must "
                "declare which roles may perform it"
            )
        if self.role not in permitted:
            raise AuthorityError(
                f"role {self.role.value!r} may not {operation}; "
                f"permitted roles are {sorted(r.value for r in permitted)}"
            )

    @staticmethod
    def _describe(entity: Base) -> tuple[str, str, dict[str, Any]]:
        """Return ``(table name, primary key, column values)`` for an entity.

        This is what makes the ledger a faithful projection: the event payload
        is the row itself, so folding the log reconstructs the table exactly.
        """
        return entity_row(entity)

    def _commit_entity(
        self,
        entity: T,
        *,
        event_type: str,
        program_id: uuid.UUID | None,
    ) -> T:
        """Persist ``entity`` and append the event describing it."""
        self._session.add(entity)
        self._session.flush()

        table, pk, row = self._describe(entity)
        self._ledger.append(
            event_type=event_type,
            subject_type=table,
            subject_id=self._subject_id(row, pk),
            actor_role=self.role,
            program_id=program_id,
            actor_task_id=self._task_id,
            policy_id=self._policy_id,
            payload={"entity": table, "pk": pk, "row": row},
        )
        return entity

    @staticmethod
    def _subject_id(row: dict[str, Any], pk: str) -> uuid.UUID:
        """Best-effort UUID for the event's subject.

        Most entities have a UUID primary key. The few with integer keys get a
        derived UUID so the event still points somewhere stable.
        """
        for value in row.values():
            if isinstance(value, uuid.UUID):
                return value
        return uuid.uuid5(uuid.NAMESPACE_OID, pk)

    def _audit(self, operation: str, entity: str, ids: list[str]) -> None:
        self._session.add(
            QueryAudit(
                occurred_at=self._clock.now(),
                role=self.role,
                task_id=self._task_id,
                operation=operation,
                entity=entity,
                entity_ids=ids,
            )
        )

    # ------------------------------------------------------ institution

    def create_lab(self, name: str, charter: str) -> Lab:
        self._authorise("create_lab")
        lab = Lab(lab_id=self._ids.new(), name=name, charter=charter, created_at=self._clock.now())
        return self._commit_entity(lab, event_type="lab.created", program_id=None)

    def create_policy(
        self,
        version: str,
        params: dict[str, Any],
        rationale: str,
        *,
        parent_version: str | None = None,
        active: bool = True,
    ) -> Policy:
        self._authorise("create_policy")
        policy = Policy(
            policy_id=self._ids.new(),
            version=version,
            parent_version=parent_version,
            params=params,
            rationale=rationale,
            active=active,
            created_at=self._clock.now(),
        )
        return self._commit_entity(policy, event_type="policy.created", program_id=None)

    def create_research_question(
        self,
        text: str,
        domain: str,
        *,
        origin: str = "human",
        bank_item_id: str | None = None,
    ) -> ResearchQuestion:
        self._authorise("create_research_question")
        rq = ResearchQuestion(
            rq_id=self._ids.new(),
            text=text,
            domain=domain,
            origin=origin,
            bank_item_id=bank_item_id,
            created_at=self._clock.now(),
        )
        return self._commit_entity(rq, event_type="research_question.created", program_id=None)

    def create_program(
        self,
        *,
        rq_id: uuid.UUID,
        lab_id: uuid.UUID,
        policy_id: uuid.UUID,
        budget_usd: Decimal,
        config_hash: str,
        capability_digest: str,
        status: str = "active",
    ) -> Program:
        self._authorise("create_program")
        program = Program(
            program_id=self._ids.new(),
            rq_id=rq_id,
            lab_id=lab_id,
            policy_id=policy_id,
            budget_usd=budget_usd,
            status=status,
            config_hash=config_hash,
            capability_digest=capability_digest,
            created_at=self._clock.now(),
        )
        return self._commit_entity(
            program, event_type="program.created", program_id=program.program_id
        )

    # -------------------------------------------------------- hypotheses

    def create_hypothesis(
        self,
        *,
        program_id: uuid.UUID,
        statement: str,
        mechanism: str,
        primary_metric: str,
        direction: str,
        mde: float,
        falsification_condition: str,
        assumptions: dict[str, Any] | None = None,
        parent_id: uuid.UUID | None = None,
        derivation: DerivationKind = DerivationKind.ROOT,
    ) -> Hypothesis:
        """Record a falsifiable hypothesis.

        The vagueness filter is the signature itself: there is no way to call
        this without naming a metric, a direction, an effect size and the
        condition that would refute it.
        """
        self._authorise("create_hypothesis")
        if not falsification_condition.strip():
            raise InvariantViolation(
                "a hypothesis with no falsification condition is not a hypothesis"
            )
        if parent_id is None and derivation is not DerivationKind.ROOT:
            raise InvariantViolation(
                f"derivation {derivation.value!r} requires a parent hypothesis"
            )

        hypothesis = Hypothesis(
            hypothesis_id=self._ids.new(),
            program_id=program_id,
            parent_id=parent_id,
            derivation=derivation,
            statement=statement,
            mechanism=mechanism,
            primary_metric=primary_metric,
            direction=direction,
            mde=mde,
            falsification_condition=falsification_condition,
            assumptions=assumptions or {},
            state=HypothesisState.DRAFT,
            created_by_task=self._task_id,
            created_at=self._clock.now(),
        )
        return self._commit_entity(
            hypothesis, event_type="hypothesis.created", program_id=program_id
        )

    def advance_hypothesis(
        self, hypothesis_id: uuid.UUID, new_state: HypothesisState
    ) -> Hypothesis:
        """Move a hypothesis through the research state machine."""
        self._authorise("advance_hypothesis")
        hypothesis = self._session.get(Hypothesis, hypothesis_id)
        if hypothesis is None:
            raise InvariantViolation(f"no such hypothesis: {hypothesis_id}")

        hypothesis.state = new_state
        self._session.flush()
        table, pk, row = self._describe(hypothesis)
        self._ledger.append(
            event_type="hypothesis.state_changed",
            subject_type=table,
            subject_id=hypothesis.hypothesis_id,
            actor_role=self.role,
            program_id=hypothesis.program_id,
            actor_task_id=self._task_id,
            policy_id=self._policy_id,
            payload={"entity": table, "pk": pk, "row": row},
        )
        return hypothesis

    # ------------------------------------------------------ registration

    def register(
        self,
        *,
        hypothesis_id: uuid.UUID,
        spec: dict[str, Any],
        analysis_plan: dict[str, Any],
        seed_root: int,
        n_seeds: int,
        holdout_query_budget: int,
        kind: RegistrationKind = RegistrationKind.CONFIRMATORY,
        parent_registration_id: uuid.UUID | None = None,
        program_id: uuid.UUID | None = None,
    ) -> Registration:
        """Preregister an experiment. The hash is written before anything runs.

        ``spec_hash`` is the canonical hash of the specification *and* the
        analysis plan together: changing how the data will be analysed is
        changing the experiment, and must produce a different registration.
        """
        self._authorise("register")
        if kind is not RegistrationKind.CONFIRMATORY and parent_registration_id is None:
            raise InvariantViolation(
                f"a {kind.value} registration must name the registration it derives from"
            )

        spec_hash = sha256_of({"spec": spec, "analysis_plan": analysis_plan})
        existing = self._session.scalars(
            sa.select(Registration).where(Registration.spec_hash == spec_hash)
        ).one_or_none()
        if existing is not None:
            raise InvariantViolation(
                f"this exact design is already registered as {existing.registration_id}; "
                "re-registering would hide that the same experiment was run twice"
            )

        registration = Registration(
            registration_id=self._ids.new(),
            hypothesis_id=hypothesis_id,
            kind=kind,
            parent_registration_id=parent_registration_id,
            spec=spec,
            spec_hash=spec_hash,
            analysis_plan=analysis_plan,
            seed_root=seed_root,
            n_seeds=n_seeds,
            holdout_query_budget=holdout_query_budget,
            registered_at=self._clock.now(),
            locked=True,
        )
        return self._commit_entity(
            registration, event_type="registration.locked", program_id=program_id
        )

    def record_forecast(
        self,
        *,
        registration_id: uuid.UUID,
        p_effect_exceeds_mde: float,
        predictive_mean: float,
        predictive_sd: float,
        p_execution_success: float,
        program_id: uuid.UUID | None = None,
    ) -> Forecast:
        """Lock this role's prediction before the experiment runs.

        One forecast per role per registration, recorded before execution. Both
        halves matter: a prediction made after seeing results is not a
        prediction, and a prediction that can be revised is not one either.
        """
        self._authorise("record_forecast")
        already_run = self._session.scalars(
            sa.select(Run).where(Run.registration_id == registration_id).limit(1)
        ).one_or_none()
        if already_run is not None:
            raise InvariantViolation(
                "a forecast cannot be recorded once a run exists for this "
                "registration; predictions made after seeing results are not predictions"
            )

        existing = self._session.scalars(
            sa.select(Forecast).where(
                Forecast.registration_id == registration_id,
                Forecast.role == self.role,
            )
        ).one_or_none()
        if existing is not None:
            raise InvariantViolation(
                f"{self.role.value} has already forecast registration {registration_id}; "
                "a forecast is locked when made, and a revisable forecast cannot be scored"
            )

        forecast = Forecast(
            forecast_id=self._ids.new(),
            registration_id=registration_id,
            role=self.role,
            p_effect_exceeds_mde=p_effect_exceeds_mde,
            predictive_mean=predictive_mean,
            predictive_sd=predictive_sd,
            p_execution_success=p_execution_success,
            created_at=self._clock.now(),
        )
        return self._commit_entity(forecast, event_type="forecast.locked", program_id=program_id)

    # --------------------------------------------------------- execution

    def record_dataset(
        self,
        *,
        name: str,
        version: str,
        content_hash: str,
        licence: str,
        generator_spec: dict[str, Any] | None = None,
    ) -> Dataset:
        self._authorise("record_dataset")
        # Content-addressed: identical bytes are the same dataset, so
        # re-recording is idempotent rather than an error.
        existing = self._session.scalars(
            sa.select(Dataset).where(Dataset.content_hash == content_hash)
        ).one_or_none()
        if existing is not None:
            return existing

        dataset = Dataset(
            dataset_id=self._ids.new(),
            name=name,
            version=version,
            content_hash=content_hash,
            generator_spec=generator_spec,
            licence=licence,
            created_at=self._clock.now(),
        )
        return self._commit_entity(dataset, event_type="dataset.registered", program_id=None)

    def record_code_bundle(
        self,
        *,
        content_hash: str,
        validator_report: dict[str, Any],
        passed: bool,
    ) -> CodeBundle:
        self._authorise("record_code_bundle")
        existing = self._session.scalars(
            sa.select(CodeBundle).where(CodeBundle.content_hash == content_hash)
        ).one_or_none()
        if existing is not None:
            return existing

        bundle = CodeBundle(
            bundle_id=self._ids.new(),
            content_hash=content_hash,
            built_by_task=self._task_id,
            validator_report=validator_report,
            passed=passed,
            created_at=self._clock.now(),
        )
        return self._commit_entity(bundle, event_type="bundle.built", program_id=None)

    def start_run(
        self,
        *,
        registration_id: uuid.UUID,
        bundle_id: uuid.UUID,
        dataset_id: uuid.UUID,
        seed: int,
        environment_hash: str,
        image_digest: str,
        isolation_tier: str,
        git_commit: str,
        retry_count: int = 0,
        program_id: uuid.UUID | None = None,
    ) -> Run:
        """Begin an execution.

        Refuses unless a locked registration for it already exists and was
        recorded no later than now. The database trigger enforces the same
        rule; this check exists to say *why*.
        """
        self._authorise("start_run")
        started_at = self._clock.now()

        registration = self._session.get(Registration, registration_id)
        if registration is None:
            raise InvariantViolation(
                f"no registration {registration_id}: results cannot exist without "
                "a design registered before them"
            )
        if not registration.locked:
            raise InvariantViolation(
                f"registration {registration_id} is not locked; an unlocked design "
                "can still be edited and must not be executed"
            )
        if registration.registered_at > started_at:
            raise InvariantViolation(
                f"registration {registration_id} is dated after the run start "
                f"({registration.registered_at.isoformat()} > {started_at.isoformat()})"
            )

        bundle = self._session.get(CodeBundle, bundle_id)
        if bundle is not None and not bundle.passed:
            raise InvariantViolation(
                f"code bundle {bundle_id} failed validation and must not be executed"
            )

        duplicate = self._session.scalars(
            sa.select(Run).where(
                Run.registration_id == registration_id,
                Run.seed == seed,
                Run.executed_by == self.role,
                Run.retry_count == retry_count,
            )
        ).one_or_none()
        if duplicate is not None:
            raise InvariantViolation(
                f"seed {seed} of registration {registration_id} has already been run by "
                f"{self.role.value} (attempt {retry_count}); re-running a seed under the "
                "same identity would let a second measurement replace the first. "
                "Increment retry_count for a genuine retry."
            )

        run = Run(
            run_id=self._ids.new(),
            registration_id=registration_id,
            bundle_id=bundle_id,
            dataset_id=dataset_id,
            seed=seed,
            executed_by=self.role,
            environment_hash=environment_hash,
            image_digest=image_digest,
            isolation_tier=isolation_tier,
            git_commit=git_commit,
            started_at=started_at,
            finished_at=None,
            status=RunStatus.COMPLETED,
            retry_count=retry_count,
            telemetry={},
        )
        return self._commit_entity(run, event_type="run.started", program_id=program_id)

    def finish_run(
        self,
        run_id: uuid.UUID,
        *,
        status: RunStatus,
        telemetry: dict[str, Any],
        program_id: uuid.UUID | None = None,
    ) -> Run:
        self._authorise("finish_run")
        run = self._session.get(Run, run_id)
        if run is None:
            raise InvariantViolation(f"no such run: {run_id}")

        run.status = status
        run.telemetry = telemetry
        run.finished_at = self._clock.now()
        self._session.flush()

        table, pk, row = self._describe(run)
        self._ledger.append(
            event_type="run.finished",
            subject_type=table,
            subject_id=run.run_id,
            actor_role=self.role,
            program_id=program_id,
            actor_task_id=self._task_id,
            policy_id=self._policy_id,
            payload={"entity": table, "pk": pk, "row": row},
        )
        return run

    def record_result(
        self,
        *,
        run_id: uuid.UUID,
        split: Split,
        metric: str,
        value: float,
        artifact_hash: str,
        computed_by: ComputedBy = ComputedBy.HARNESS,
        program_id: uuid.UUID | None = None,
    ) -> RunResult:
        """Record one metric.

        Holdout metrics are refused from anyone but the Custodian. The table's
        ``ck_holdout_custodian_only`` constraint says the same thing to raw
        SQL; this says it with an explanation.
        """
        self._authorise("record_result")
        if split is Split.HOLDOUT:
            if computed_by is not ComputedBy.CUSTODIAN:
                raise InvariantViolation(
                    "a holdout metric must be computed by the Custodian; no "
                    "agent-authored number about the test split may enter the ledger"
                )
            if self.role is not Role.CUSTODIAN:
                raise InvariantViolation(
                    f"role {self.role.value!r} may not record a holdout metric; "
                    "the Custodian holds the test split"
                )

        duplicate = self._session.scalars(
            sa.select(RunResult).where(
                RunResult.run_id == run_id,
                RunResult.split == split,
                RunResult.metric == metric,
            )
        ).one_or_none()
        if duplicate is not None:
            raise InvariantViolation(
                f"{metric} on the {split.value} split of run {run_id} is already recorded "
                f"as {duplicate.value}; a measurement is written once. Recording it again "
                "would silently replace an observation with another."
            )

        result = RunResult(
            result_id=self._ids.new(),
            run_id=run_id,
            split=split,
            metric=metric,
            value=value,
            computed_by=computed_by,
            artifact_hash=artifact_hash,
        )
        return self._commit_entity(result, event_type="result.recorded", program_id=program_id)

    # ---------------------------------------------------- claims & doubt

    def create_claim(
        self,
        *,
        program_id: uuid.UUID,
        statement: str,
        kind: AssertionKind,
        hypothesis_id: uuid.UUID | None = None,
    ) -> Claim:
        """Create a claim. Confidence starts at the floor and is computed later."""
        self._authorise("create_claim")
        if kind is AssertionKind.SPECULATION:
            raise InvariantViolation(
                "a speculation cannot be a claim; it is excluded from every report"
            )
        claim = Claim(
            claim_id=self._ids.new(),
            program_id=program_id,
            hypothesis_id=hypothesis_id,
            statement=statement,
            kind=kind,
            confidence=ClaimConfidence.SPECULATIVE,
            computed_at=self._clock.now(),
        )
        return self._commit_entity(claim, event_type="claim.created", program_id=program_id)

    def add_evidence(
        self,
        *,
        claim_id: uuid.UUID,
        kind: EvidenceKind,
        polarity: Polarity,
        strength: dict[str, Any],
        result_id: uuid.UUID | None = None,
        source_id: uuid.UUID | None = None,
        parent_claim_id: uuid.UUID | None = None,
        program_id: uuid.UUID | None = None,
    ) -> Evidence:
        """Link a claim to what supports or contradicts it."""
        self._authorise("add_evidence")
        referents = {
            EvidenceKind.EXPERIMENTAL: result_id,
            EvidenceKind.SOURCED: source_id,
            EvidenceKind.DERIVED: parent_claim_id,
        }
        if referents[kind] is None:
            raise InvariantViolation(
                f"{kind.value} evidence must name its referent; evidence that points "
                "at nothing is not evidence"
            )

        evidence = Evidence(
            evidence_id=self._ids.new(),
            claim_id=claim_id,
            kind=kind,
            polarity=polarity,
            result_id=result_id,
            source_id=source_id,
            parent_claim_id=parent_claim_id,
            strength=strength,
            created_at=self._clock.now(),
        )
        return self._commit_entity(evidence, event_type="evidence.linked", program_id=program_id)

    def raise_objection(
        self,
        *,
        target_type: str,
        target_id: uuid.UUID,
        objection_type: ObjectionType,
        severity: ObjectionSeverity,
        statement: str,
        discriminating_test: dict[str, Any],
        was_injected_defect: bool | None = None,
        program_id: uuid.UUID | None = None,
    ) -> Objection:
        """Raise a typed objection that names a test capable of settling it.

        An objection with no discriminating test is refused. Criticism that no
        experiment could resolve cannot block a claim, however well written.
        """
        self._authorise("raise_objection")
        if not discriminating_test:
            raise InvariantViolation(
                "an objection must name a discriminating test; an objection no "
                "experiment could settle is rhetoric, not review"
            )

        objection = Objection(
            objection_id=self._ids.new(),
            target_type=target_type,
            target_id=target_id,
            type=objection_type,
            severity=severity,
            statement=statement,
            discriminating_test=discriminating_test,
            raised_by_task=self._task_id,
            raised_by_role=self.role,
            status=ObjectionStatus.OPEN,
            was_injected_defect=was_injected_defect,
            created_at=self._clock.now(),
        )
        return self._commit_entity(objection, event_type="objection.raised", program_id=program_id)

    # --------------------------------------------------------- accounting

    def record_cost(
        self,
        *,
        program_id: uuid.UUID,
        usd: Decimal,
        price_table_version: str,
        task_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        llm_input_tokens: int = 0,
        llm_output_tokens: int = 0,
        llm_cached_tokens: int = 0,
        cpu_seconds: float = 0.0,
        storage_mb: float = 0.0,
    ) -> CostEntry:
        """Record what something cost.

        Zero-cost entries are recorded too, not skipped: the count of free
        cache hits is what makes the replay argument checkable, and a budget
        that cannot be reconstructed from the ledger is not a budget.
        """
        self._authorise("record_cost")
        entry = CostEntry(
            program_id=program_id,
            task_id=task_id,
            run_id=run_id,
            llm_input_tokens=llm_input_tokens,
            llm_output_tokens=llm_output_tokens,
            llm_cached_tokens=llm_cached_tokens,
            cpu_seconds=cpu_seconds,
            storage_mb=storage_mb,
            usd=usd,
            price_table_version=price_table_version,
            created_at=self._clock.now(),
        )
        return self._commit_entity(entry, event_type="cost.recorded", program_id=program_id)

    def record_holdout_query(
        self,
        *,
        registration_id: uuid.UUID,
        artifact_hash: str,
        granted: bool,
        remaining_budget: int,
        program_id: uuid.UUID | None = None,
    ) -> HoldoutQuery:
        """Record one look at the evaluation split — or one refused look.

        Refusals are recorded too. An attempt to exceed the budget is a fact
        about the research, and a denied query that left no trace would make
        the count of looks a lower bound rather than a count.
        """
        self._authorise("record_holdout_query")
        query = HoldoutQuery(
            query_id=self._ids.new(),
            registration_id=registration_id,
            requested_at=self._clock.now(),
            artifact_hash=artifact_hash,
            granted=granted,
            remaining_budget=remaining_budget,
        )
        return self._commit_entity(
            query,
            event_type="holdout.queried" if granted else "holdout.refused",
            program_id=program_id,
        )

    def record_llm_call(
        self,
        *,
        cache_key: str,
        provider: str,
        model: str,
        params: dict[str, Any],
        prompt_hash: str,
        response_hash: str,
        cache_hit: bool,
        task_id: uuid.UUID | None = None,
        program_id: uuid.UUID | None = None,
    ) -> LlmCall:
        """Record one model call, for audit and for replay."""
        self._authorise("record_llm_call")
        call = LlmCall(
            call_id=self._ids.new(),
            task_id=task_id,
            cache_key=cache_key,
            provider=provider,
            model=model,
            params=params,
            prompt_hash=prompt_hash,
            response_hash=response_hash,
            cache_hit=cache_hit,
            created_at=self._clock.now(),
        )
        return self._commit_entity(call, event_type="llm.called", program_id=program_id)

    # -------------------------------------------------------------- reads

    def get_hypothesis(self, hypothesis_id: uuid.UUID) -> Hypothesis | None:
        self._audit("get_hypothesis", "hypotheses", [str(hypothesis_id)])
        return self._session.get(Hypothesis, hypothesis_id)

    def get_registration(self, registration_id: uuid.UUID) -> Registration | None:
        self._audit("get_registration", "registrations", [str(registration_id)])
        return self._session.get(Registration, registration_id)

    def results_for_run(self, run_id: uuid.UUID) -> list[RunResult]:
        rows = list(self._session.scalars(sa.select(RunResult).where(RunResult.run_id == run_id)))
        self._audit("results_for_run", "run_results", [str(r.result_id) for r in rows])
        return rows

    def open_critical_objections(self, target_id: uuid.UUID) -> list[Objection]:
        """Objections that currently bar promotion to an institutional claim."""
        rows = list(
            self._session.scalars(
                sa.select(Objection).where(
                    Objection.target_id == target_id,
                    Objection.severity == ObjectionSeverity.CRITICAL,
                    Objection.status == ObjectionStatus.OPEN,
                )
            )
        )
        self._audit("open_critical_objections", "objections", [str(o.objection_id) for o in rows])
        return rows

    def audit_trail(self, role: Role | None = None) -> list[QueryAudit]:
        """What each role has read. The evidence behind a blindness claim."""
        query = sa.select(QueryAudit).order_by(QueryAudit.audit_id.asc())
        if role is not None:
            query = query.where(QueryAudit.role == role)
        return list(self._session.scalars(query))

    # ------------------------------------------------------------ commit

    def commit(self) -> None:
        """Commit the domain rows and their events together, or neither."""
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def __enter__(self) -> Repository:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()

    def __repr__(self) -> str:
        return f"Repository(role={self.role.value!r})"
