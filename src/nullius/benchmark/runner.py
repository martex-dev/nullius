"""Running the ladder: every arm, every bank item, one verdict each.

:mod:`~nullius.benchmark.protocol` fixed the analysis plan and was committed
before this file existed. What remains is mechanical, and deliberately so —
the runner has no choices left to make, because every choice that could have
been made after seeing a number was made in the protocol commit.

**Where the switches are.** An arm is a set of booleans
(:class:`~nullius.benchmark.arms.Arm`), and every one of them is consumed in
exactly one place: :func:`mechanisms_for` turns it into a
:class:`~nullius.kernel.Mechanisms`, which the kernel reads. Two institutional
arms therefore run the *same* code with different flags. There is no second
pipeline for B3 to drift away in.

**Three code paths, not eight.** B0 answers without looking, so it has no
pipeline at all. B1 and B2 ask a model directly and have no ledger, no
registration and no execution — that is what "the naive baseline everyone
actually ships" means, and giving them the institution's machinery would make
them a different arm. Everything from B3 up is the research kernel.

**Cost is measured for every arm, including the ones that do almost nothing.**
An arm cannot look good by not being billed. B0's zero is a real zero and is
reported as one.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from nullius.bank.items import BANK_V1, BankItem
from nullius.bank.lock import DEFAULT_LOCK_PATH as TRUTH_LOCK_PATH
from nullius.bank.lock import read_lock
from nullius.bank.truth import Truth, boundary_margin
from nullius.benchmark.arms import LADDER, Arm, ArmKind
from nullius.db.enums import ClaimConfidence, Role, Verdict
from nullius.db.tables import CostEntry
from nullius.kernel import KernelOutcome, Mechanisms, ResearchKernel
from nullius.llm.pricing import price_of
from nullius.repository import Repository
from nullius.util.canonical import canonical_json, sha256_of

__all__ = [
    "CONSTANT_VERDICT",
    "DIRECT_CONFIDENCE",
    "DIRECT_MOCK_VERDICT",
    "ArmOutcome",
    "ArmRun",
    "mechanisms_for",
    "run_arm",
    "run_ladder",
]

CONSTANT_VERDICT = Verdict.NO_EFFECT
"""What B0 answers, every time, without looking.

``no_effect`` rather than ``supported`` because it is the harder floor to
beat: half the bank is null by construction, so this arm scores every null
item correctly and costs nothing. An expensive institution that cannot clear
it has not earned its budget.
"""

DIRECT_CONFIDENCE = ClaimConfidence.SPECULATIVE
"""The confidence a direct answer is credited with.

Assigned rather than computed, because there is nothing to compute it from:
B1 and B2 produce no registration, no seeds, no interval and no holdout. The
confidence rubric of ``docs/03-data-model.md`` caps an unreplicated,
unregistered claim with no evidence at ``speculative``, and that is exactly
what a single-shot answer is. Crediting it higher would hand the arm a
calibration score it did not earn; crediting it lower would rig the
comparison.
"""


def mechanisms_for(arm: Arm) -> Mechanisms:
    """The arm's booleans, as the kernel's switches. The only translation."""
    return Mechanisms(
        custody=arm.custodian,
        preregistered=arm.preregistered,
        adversary=arm.adversary,
        replication=arm.replication,
        memory=arm.memory,
    )


@dataclass(frozen=True, slots=True)
class ArmOutcome:
    """One arm's answer to one bank item, and what it cost to get there.

    ``correct`` is decided against ``bank/truth.lock.json``, which was
    committed before any arm ran and is re-verified in CI. No arm is scored
    against anybody's expectation of what it should have said.
    """

    arm_id: str
    item_id: str
    verdict: Verdict
    truth_verdict: Verdict
    true_effect: float
    realised_effect: float
    boundary_margin: float
    confidence: ClaimConfidence
    usd: Decimal
    n_seeds: int
    replications: int
    findings: int
    halted: str | None = None

    @property
    def correct(self) -> bool:
        """Whether the arm said what is true.

        A halted lifecycle is incorrect, never absent. Dropping it would pay an
        arm for failing to answer the questions it found hard, which the
        protocol's exclusion rules refuse in as many words.
        """
        return self.verdict is self.truth_verdict

    @property
    def abstained(self) -> bool:
        """Whether the arm declined to answer rather than answering wrongly.

        ``UNDERPOWERED`` is a statement about the design, not about the world.
        It is still counted as incorrect by :attr:`correct` — the protocol's
        first exclusion rule refuses to drop an unanswered item, because
        dropping it would pay an arm for refusing the questions it found
        hardest — but it is reported separately, so that a system which knows
        what it cannot measure is distinguishable from one that guesses.
        """
        return self.verdict is Verdict.UNDERPOWERED

    @property
    def is_null_item(self) -> bool:
        return self.truth_verdict is Verdict.NO_EFFECT

    @property
    def claimed_an_effect(self) -> bool:
        """Whether this arm asserted a real effect, right or wrong.

        The denominator of the false discovery rate. ``inconclusive`` is not a
        discovery, and neither is ``no_effect``.
        """
        return self.verdict in (Verdict.SUPPORTED, Verdict.REFUTED, Verdict.CONDITIONAL)

    @property
    def false_discovery(self) -> bool:
        """An effect asserted where the truth is that there is none."""
        return self.claimed_an_effect and self.is_null_item

    def as_dict(self) -> dict[str, Any]:
        return {
            "arm_id": self.arm_id,
            "item_id": self.item_id,
            "verdict": self.verdict.value,
            "truth_verdict": self.truth_verdict.value,
            "true_effect": round(self.true_effect, 6),
            "realised_effect": round(self.realised_effect, 6),
            "boundary_margin": round(self.boundary_margin, 6),
            "confidence": self.confidence.value,
            "usd": str(self.usd),
            "n_seeds": self.n_seeds,
            "replications": self.replications,
            "findings": self.findings,
            "correct": self.correct,
            "abstained": self.abstained,
            "halted": self.halted,
        }


@dataclass(frozen=True, slots=True)
class ArmRun:
    """Everything one arm produced over the whole bank."""

    arm: Arm
    outcomes: tuple[ArmOutcome, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm.as_dict(),
            "outcomes": [outcome.as_dict() for outcome in self.outcomes],
        }


# ------------------------------------------------------------------- costing

AS_IF_MODEL = "claude-sonnet-5"
"""The model whose rates price a mock-driven run's real token counts.

The same substitution :mod:`nullius.economy.outcomes` discloses, for the same
reason: :class:`~nullius.llm.providers.MockProvider` is free, and a
cost-per-correct-claim whose numerator is identically zero ranks nothing. The
token counts are real. The dollars are conditional on a model that did not run,
and the results file says so.
"""


def _usd_for_program(repo: Repository, program_id: uuid.UUID) -> Decimal:
    """What this programme cost: tokens priced as-if, plus real compute.

    Summed in Python over :class:`~decimal.Decimal` rather than in SQL. SQLite
    stores this project's money as text and ``sum()`` over a text column coerces
    through binary float, which is how a ledger and its own total come to
    disagree in the last decimal place.
    """
    price = price_of(AS_IF_MODEL)
    rows = repo.session.execute(
        sa.select(
            CostEntry.llm_input_tokens,
            CostEntry.llm_output_tokens,
            CostEntry.usd,
            CostEntry.task_id,
        ).where(CostEntry.program_id == program_id)
    ).all()

    tokens = Decimal(0)
    compute = Decimal(0)
    for inputs, outputs, usd, task_id in rows:
        if task_id is not None:
            tokens += (
                Decimal(int(inputs or 0)) * price.input_usd_per_mtok
                + Decimal(int(outputs or 0)) * price.output_usd_per_mtok
            ) / Decimal(1_000_000)
        else:
            # Sandbox seconds and bytes. Genuinely burned, not substituted.
            compute += Decimal(usd or 0)
    return tokens + compute


# ------------------------------------------------------- the three code paths


def _constant(item: BankItem, truth: Truth) -> ArmOutcome:
    """B0. Answers without looking, costs nothing, and is hard to beat."""
    return ArmOutcome(
        arm_id="B0",
        item_id=item.item_id,
        verdict=CONSTANT_VERDICT,
        truth_verdict=truth.verdict,
        true_effect=truth.effect,
        realised_effect=0.0,
        boundary_margin=boundary_margin(truth),
        confidence=ClaimConfidence.SPECULATIVE,
        usd=Decimal("0.00000000"),
        n_seeds=0,
        replications=0,
        findings=0,
    )


DIRECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "effect", "reasoning"],
    "properties": {
        "verdict": {"type": "string", "enum": [v.value for v in Verdict]},
        "effect": {"type": "number"},
        "reasoning": {"type": "string"},
    },
}

DIRECT_SYSTEM = (
    "You are a machine learning researcher. You are shown a question about a "
    "tabular dataset and must answer it directly, from knowledge and reasoning "
    "alone. You cannot run an experiment. Give your verdict, your estimate of "
    "the effect size on the primary metric, and your reasoning."
)

REVISION_SYSTEM = (
    "You are the same researcher, reviewing your own previous answer. Criticise "
    "it, then give your revised verdict and effect estimate. If your first "
    "answer was right, say so and repeat it."
)


def _direct(
    arm: Arm,
    item: BankItem,
    truth: Truth,
    *,
    repo: Repository,
    provider: Any,
    program_id: uuid.UUID,
    model: Any,
) -> ArmOutcome:
    """B1 and B2. One agent, asked directly, with no institution behind it.

    ``arm.iterations`` is the only difference between the two, and it is
    consumed here and nowhere else: B2 gets to see its own answer and revise
    it. Whether that helps is the question B2 exists to ask, and under a mock
    provider the answer describes the mock — which is why both arms carry
    ``model_dependent`` and the report refuses to read mechanism into them.
    """
    from nullius.llm.types import LlmRequest, Message

    verdict = Verdict.INCONCLUSIVE
    effect = 0.0
    previous: str | None = None

    for iteration in range(max(1, arm.iterations)):
        content = f"Question: {item.question}\nDataset: {item.item_id}"
        if previous is not None:
            content += f"\n\nYour previous answer:\n{previous}"
        request = LlmRequest(
            model=model,
            system=DIRECT_SYSTEM if iteration == 0 else REVISION_SYSTEM,
            messages=(Message(role="user", content=content),),
            output_schema=DIRECT_SCHEMA,
        )
        response = provider.complete(request)

        # No registration, no runs, no custody — but the call is still billed.
        # An arm cannot look cheap by not being counted.
        repo.as_role(Role.SYSTEM).record_llm_call(
            cache_key=request.cache_key(),
            provider=provider.name,
            model=response.model,
            params={"arm": arm.arm_id, "iteration": iteration},
            prompt_hash=request.prompt_hash,
            response_hash=response.response_hash,
            cache_hit=response.cache_hit,
            program_id=program_id,
        )
        repo.as_role(Role.SYSTEM).record_cost(
            program_id=program_id,
            usd=Decimal(0),
            price_table_version="mock",
            task_id=uuid.uuid4(),
            llm_input_tokens=response.usage.total_input,
            llm_output_tokens=response.usage.output_tokens,
        )

        payload = response.structured or {}
        try:
            verdict = Verdict(str(payload.get("verdict", Verdict.INCONCLUSIVE.value)))
        except ValueError:
            verdict = Verdict.INCONCLUSIVE
        effect = float(payload.get("effect", 0.0) or 0.0)
        previous = str(payload.get("reasoning", ""))

    return ArmOutcome(
        arm_id=arm.arm_id,
        item_id=item.item_id,
        verdict=verdict,
        truth_verdict=truth.verdict,
        true_effect=truth.effect,
        realised_effect=effect,
        boundary_margin=boundary_margin(truth),
        confidence=DIRECT_CONFIDENCE,
        usd=_usd_for_program(repo, program_id),
        n_seeds=0,
        replications=0,
        findings=0,
    )


def _institutional(
    arm: Arm,
    item: BankItem,
    truth: Truth,
    *,
    kernel: ResearchKernel,
    repo: Repository,
    program_id: uuid.UUID,
    allowance: Decimal,
) -> ArmOutcome:
    """B3 through B7. The research kernel, with this arm's switches set."""
    result: KernelOutcome = kernel.run_item(
        item,
        program_id=program_id,
        allowance=allowance,
        mechanisms=mechanisms_for(arm),
    )
    return ArmOutcome(
        arm_id=arm.arm_id,
        item_id=item.item_id,
        verdict=result.verdict.verdict if result.verdict is not None else Verdict.INCONCLUSIVE,
        truth_verdict=truth.verdict,
        true_effect=truth.effect,
        realised_effect=result.analysis.difference if result.analysis is not None else 0.0,
        boundary_margin=boundary_margin(truth),
        confidence=(
            result.confidence.confidence
            if result.confidence is not None
            else ClaimConfidence.SPECULATIVE
        ),
        usd=_usd_for_program(repo, program_id),
        n_seeds=result.analysis.n_seeds if result.analysis is not None else 0,
        replications=result.replications,
        findings=len(result.findings),
        halted=result.halted,
    )


# ------------------------------------------------------- the mock's own answer

DIRECT_MOCK_VERDICT = Verdict.SUPPORTED
"""What the mock provider answers when an arm asks it directly.

**This is a stipulation, not a measurement, and it decides B1 and B2 entirely.**

There is no such thing as "what the model would say" under
:class:`~nullius.llm.providers.MockProvider`; there is only what this file
tells it to say. The choice made here is the documented failure mode of an
unstructured agent asked whether an intervention helps — it agrees. That makes
B1 the mirror of B0: one arm always says there is an effect, the other always
says there is none, and between them they bracket the bank without either
having looked at any data.

The alternative choices were both worse. A mock that answered correctly would
make B1 unbeatable and the whole ladder pointless; a mock that answered
randomly would put a pseudo-random number generator into the primary metric
and call it a baseline.

So B1 and B2's numbers are a property of this constant. They are reported
because the protocol's exclusion rules forbid withholding any arm's result,
and they are labelled ``model_dependent`` so that no comparison of mechanism
rests on them. Replacing the mock with a real provider replaces these two rows
and nothing else.
"""


def _mock_model() -> Any:
    from nullius.llm.types import ModelRef

    return ModelRef(provider="mock", model="mock-1", effort=None, thinking=None)


def _responder_for(arm: Arm, base: Any) -> Any:
    """Extend the canned role responder with an answer for the direct arms.

    B2 receives the identical answer it gave the first time. That is not an
    oversight: a mock whose second pass was better than its first would be this
    file deciding that iteration helps, which is precisely the question B2 was
    added to the ladder to ask. Under a mock the honest answer is that B2 and
    B1 are indistinguishable, and the report says that rather than showing a
    manufactured improvement.
    """

    def respond(request: Any) -> Any:
        if request.system.startswith(("You are a machine learning researcher", "You are the same")):
            return {
                "verdict": DIRECT_MOCK_VERDICT.value,
                "effect": 0.05,
                "reasoning": (
                    f"Arm {arm.arm_id} answered without running an experiment. "
                    "This response is a stipulated stand-in for a real model."
                ),
            }
        return base(request)

    return respond


# --------------------------------------------------------------- the ladder


def run_arm(
    arm: Arm,
    *,
    database: Path,
    workroot: Path,
    items: Sequence[BankItem] = BANK_V1,
    truth_lock: Path = TRUTH_LOCK_PATH,
    budget_usd: Decimal = Decimal("100.00"),
    allowance: Decimal = Decimal("0.50"),
) -> ArmRun:
    """Carry every bank item through one arm, in bank order.

    **One lab, one programme per item.** Cost is a programme-scoped quantity,
    so a shared programme would leave no way to say what a single item cost —
    and cost per correct claim is half of what this benchmark measures. Memory
    still carries, because it is recalled at lab scope; see
    :func:`~nullius.knowledge.memory.recall`.

    That split was forced by the run, not designed in advance. Putting twenty
    items in one programme tripped M8's novelty check on the second item: the
    bank is twenty variants of one question across different datasets, the
    novelty fingerprint covers metric, direction, effect size and statement
    tokens but *not* the dataset, and so "the same question about different
    data" is indistinguishable from "the same question again". Both halves of
    that are recorded in ``BUILD_PLAN.md`` as findings rather than quietly
    worked around.

    **Bank order, fixed.** Memory makes the arm order-dependent: what the
    institution knows when it reaches item *n* depends on items 1..n-1. The
    order is therefore part of the run and is held constant across arms, so
    that B6 and B7 differ in whether memory is consulted and not in what it
    would have contained.
    """
    from nullius.db.base import create_engine, create_schema, session_factory
    from nullius.economy.outcomes import canned_responder
    from nullius.execute.sandbox import SubprocessSandbox
    from nullius.llm.providers import MockProvider
    from nullius.roles.contracts import contracts_for
    from nullius.store.cas import ContentStore

    truths = read_lock(truth_lock)
    workroot.mkdir(parents=True, exist_ok=True)
    database.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database)
    create_schema(engine)

    provider = MockProvider(_responder_for(arm, canned_responder()))
    outcomes: list[ArmOutcome] = []

    with session_factory(engine)() as session:
        repo = Repository(session, Role.SYSTEM)
        lab = repo.create_lab("Nullius", f"Benchmark arm {arm.arm_id}.")
        policy = repo.create_policy(
            f"benchmark-{arm.arm_id.lower()}", {"min_seeds": 5}, f"Ladder arm {arm}."
        )
        kernel = ResearchKernel(
            repo,
            provider,
            SubprocessSandbox(),
            ContentStore(workroot / "objects"),
            workroot / "runs",
            mock=True,
        )
        # Touch the contract registry so a mis-specified arm fails here rather
        # than twenty items into a run.
        contracts_for(mock=True)

        for item in items:
            truth = truths[item.item_id]
            if arm.kind is ArmKind.CONSTANT:
                outcomes.append(_constant(item, truth))
                continue

            rq = repo.create_research_question(
                item.question, domain="tabular-ml", bank_item_id=item.item_id
            )
            program_id = repo.create_program(
                rq_id=rq.rq_id,
                lab_id=lab.lab_id,
                policy_id=policy.policy_id,
                budget_usd=budget_usd,
                config_hash="0" * 64,
                capability_digest="1" * 64,
            ).program_id

            if arm.kind is ArmKind.DIRECT:
                outcome = _direct(
                    arm,
                    item,
                    truth,
                    repo=repo,
                    provider=provider,
                    program_id=program_id,
                    model=_mock_model(),
                )
            else:
                outcome = _institutional(
                    arm,
                    item,
                    truth,
                    kernel=kernel,
                    repo=repo,
                    program_id=program_id,
                    allowance=allowance,
                )

            outcomes.append(outcome)
            repo.commit()

    return ArmRun(arm=arm, outcomes=tuple(outcomes))


def _checkpoint_path(root: Path, arm: Arm) -> Path:
    return root / f"{arm.arm_id.lower()}.outcomes.json"


def _write_checkpoint(root: Path, run: ArmRun, items_hash: str) -> None:
    payload = {"items_hash": items_hash, "run": run.as_dict()}
    _checkpoint_path(root, run.arm).write_text(canonical_json(payload) + "\n", encoding="utf-8")


def _read_checkpoint(root: Path, arm: Arm, items_hash: str) -> ArmRun | None:
    """A completed arm from an earlier attempt, if it was run on this bank."""
    path = _checkpoint_path(root, arm)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("items_hash") != items_hash:
        return None
    return ArmRun(
        arm=arm,
        outcomes=tuple(
            ArmOutcome(
                arm_id=row["arm_id"],
                item_id=row["item_id"],
                verdict=Verdict(row["verdict"]),
                truth_verdict=Verdict(row["truth_verdict"]),
                true_effect=row["true_effect"],
                realised_effect=row["realised_effect"],
                boundary_margin=row["boundary_margin"],
                confidence=ClaimConfidence(row["confidence"]),
                usd=Decimal(row["usd"]),
                n_seeds=row["n_seeds"],
                replications=row["replications"],
                findings=row["findings"],
                halted=row["halted"],
            )
            for row in payload["run"]["outcomes"]
        ),
    )


def run_ladder(
    *,
    root: Path,
    arms: Sequence[Arm] = LADDER,
    items: Sequence[BankItem] = BANK_V1,
    truth_lock: Path = TRUTH_LOCK_PATH,
    resume: bool = True,
) -> list[ArmRun]:
    """Every arm, each in its own database, over the same bank.

    Separate databases because arms must not see each other's ledgers: a
    shared store would let an arm's novelty check trip on another arm's
    hypotheses, and the arms would stop being independent.

    **Each arm is checkpointed the moment it finishes.** The first full v2
    ladder ran all eight arms over two hours and then died writing the results
    file, on a metric that was legitimately undefined, and every completed arm
    went with it. Science that has already been done should not be lost to a
    fault in the reporting of it. ``resume`` reuses any checkpoint written for
    the same bank; a checkpoint from a different bank is ignored rather than
    trusted, since it describes questions this run is not asking.
    """
    root.mkdir(parents=True, exist_ok=True)
    items_hash = sha256_of([item.as_dict() for item in items])

    runs: list[ArmRun] = []
    for arm in arms:
        cached = _read_checkpoint(root, arm, items_hash) if resume else None
        if cached is not None:
            runs.append(cached)
            continue
        run = run_arm(
            arm,
            database=root / f"{arm.arm_id.lower()}.sqlite",
            workroot=root / arm.arm_id.lower(),
            items=items,
            truth_lock=truth_lock,
        )
        _write_checkpoint(root, run, items_hash)
        runs.append(run)
    return runs
