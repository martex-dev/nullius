"""Controlled vocabularies.

These are enums rather than free strings because most of them are load-bearing
for an invariant. ``AssertionKind`` in particular is the mechanism behind
`docs/01-critique.md` §23's evidence discipline: an inferred claim with no
parents and a speculation promoted to evidence are both rejected by reference
to these values, not by an agent's good behaviour.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "AssertionKind",
    "ClaimConfidence",
    "ComputedBy",
    "DerivationKind",
    "EvidenceKind",
    "HypothesisState",
    "ObjectionSeverity",
    "ObjectionType",
    "Polarity",
    "RegistrationKind",
    "ReplicationOutcome",
    "ReviewDecision",
    "Role",
    "RunStatus",
    "Split",
    "Stance",
    "Verdict",
]


class Role(StrEnum):
    """Institutional actors.

    ``SYSTEM`` covers the deterministic control plane. ``CUSTODIAN`` is a
    distinct actor rather than part of ``SYSTEM`` because it is the only one
    permitted to produce holdout metrics, and separating it makes that
    authority checkable.
    """

    DIRECTOR = "director"
    THEORIST = "theorist"
    LITERATURE = "literature"
    DESIGNER = "designer"
    BUILDER = "builder"
    ANALYST = "analyst"
    SKEPTIC = "skeptic"
    REPLICATOR = "replicator"
    REVIEWER = "reviewer"
    CUSTODIAN = "custodian"
    SYSTEM = "system"


class AssertionKind(StrEnum):
    """Epistemic type of an assertion. Promotion between these is illegal."""

    OBSERVED_FACT = "observed_fact"
    """Written only by the execution or custody plane, from artifacts."""

    SOURCED_CLAIM = "sourced_claim"
    """Requires a resolvable source and a stored verbatim passage."""

    INFERRED_CLAIM = "inferred_claim"
    """Requires at least one parent evidence row."""

    HYPOTHESIS = "hypothesis"
    """Agent-generated. Never evidence for anything."""

    SPECULATION = "speculation"
    """Agent-generated. Excluded from every report and every metric."""


class HypothesisState(StrEnum):
    """The research state machine of `docs/02-architecture.md` §3."""

    DRAFT = "draft"
    SCREENED = "screened"
    SHELVED = "shelved"
    REGISTERED = "registered"
    BUILT = "built"
    EXECUTED = "executed"
    ANALYZED = "analyzed"
    CHALLENGED = "challenged"
    REPLICATED = "replicated"
    REVIEWED = "reviewed"
    # Terminal.
    INSTITUTIONAL = "institutional"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"
    REVISED = "revised"
    ABANDONED_BUDGET = "abandoned_budget"


TERMINAL_STATES = frozenset(
    {
        HypothesisState.INSTITUTIONAL,
        HypothesisState.REFUTED,
        HypothesisState.INCONCLUSIVE,
        HypothesisState.REVISED,
        HypothesisState.ABANDONED_BUDGET,
        HypothesisState.SHELVED,
    }
)
"""States from which no further transition occurs.

``REFUTED`` and ``INCONCLUSIVE`` are terminal *successes* of the process and
are reported with the same prominence as ``INSTITUTIONAL``.
"""


class DerivationKind(StrEnum):
    """How a hypothesis descends from its parent — the genealogy edge label."""

    ROOT = "root"
    SPECIALISATION = "specialisation"
    GENERALISATION = "generalisation"
    REFUTATION_RESPONSE = "refutation_response"
    MERGE = "merge"
    ABLATION = "ablation"
    FOLLOW_UP_FROM_FAILURE = "follow_up_from_failure"


class RegistrationKind(StrEnum):
    """Confirmatory registrations may become institutional claims. Others may not."""

    CONFIRMATORY = "confirmatory"
    EXPLORATORY = "exploratory"
    REPLICATION = "replication"


class RunStatus(StrEnum):
    """Outcome of an execution.

    The split between infrastructure and scientific failure is the point:
    infrastructure failures may be retried, scientific failures never are and
    become research objects in their own right (`docs/01-critique.md` A12).
    """

    COMPLETED = "completed"
    INFRA_FAILURE = "infra_failure"
    SCIENTIFIC_FAILURE = "scientific_failure"
    TIMEOUT = "timeout"
    OOM = "oom"


RETRYABLE_STATUSES = frozenset({RunStatus.INFRA_FAILURE, RunStatus.TIMEOUT, RunStatus.OOM})


class Split(StrEnum):
    TRAIN = "train"
    DEV = "dev"
    HOLDOUT = "holdout"


class ComputedBy(StrEnum):
    """Who computed a metric. Holdout metrics accept only ``CUSTODIAN``."""

    HARNESS = "harness"
    CUSTODIAN = "custodian"


class EvidenceKind(StrEnum):
    EXPERIMENTAL = "experimental"
    SOURCED = "sourced"
    DERIVED = "derived"


class Polarity(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"


class ClaimConfidence(StrEnum):
    """Computed from evidence, never asserted by an agent.

    Ordered weakest to strongest; the rubric in M5 maps evidence rows onto
    this scale.
    """

    CONTESTED = "contested"
    SPECULATIVE = "speculative"
    SUGGESTIVE = "suggestive"
    SUPPORTED = "supported"
    WELL_SUPPORTED = "well_supported"


CONFIDENCE_ORDER: tuple[ClaimConfidence, ...] = (
    ClaimConfidence.CONTESTED,
    ClaimConfidence.SPECULATIVE,
    ClaimConfidence.SUGGESTIVE,
    ClaimConfidence.SUPPORTED,
    ClaimConfidence.WELL_SUPPORTED,
)


class ObjectionSeverity(StrEnum):
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"
    """Gates promotion to an institutional claim while open."""


class ObjectionType(StrEnum):
    """The Skeptic's taxonomy.

    A closed vocabulary because objections must be scoreable against injected
    defects; free-text objections cannot be matched to a planted defect.
    """

    LEAKAGE = "leakage"
    CONTAMINATION = "contamination"
    WEAK_BASELINE = "weak_baseline"
    CONFOUND = "confound"
    MULTIPLE_TESTING = "multiple_testing"
    SEED_INSTABILITY = "seed_instability"
    METRIC_INVALID = "metric_invalid"
    UNDERPOWERED = "underpowered"
    IMPLEMENTATION_BUG = "implementation_bug"
    ALTERNATIVE_EXPLANATION = "alternative_explanation"
    GENERALISATION_OVERREACH = "generalisation_overreach"
    ARTIFACT_OF_BENCHMARK = "artifact_of_benchmark"


class ObjectionStatus(StrEnum):
    OPEN = "open"
    RESOLVED_UPHELD = "resolved_upheld"
    RESOLVED_REJECTED = "resolved_rejected"
    EXPIRED = "expired"
    """Aged out into a reported unresolved limitation rather than a blocker."""


class ReplicationOutcome(StrEnum):
    REPLICATED = "replicated"
    PARTIALLY_REPLICATED = "partially_replicated"
    FAILED_REPLICATION = "failed_replication"
    INCONCLUSIVE = "inconclusive"


class ReviewDecision(StrEnum):
    ACCEPT = "accept"
    MINOR_REVISION = "minor_revision"
    MAJOR_REVISION = "major_revision"
    REJECT = "reject"


class Stance(StrEnum):
    """A role's position on a claim. Disagreement is preserved, not resolved."""

    SUPPORTS = "supports"
    OPPOSES = "opposes"
    ABSTAINS = "abstains"
    UNCERTAIN = "uncertain"


class Verdict(StrEnum):
    """The institution's answer, scored against planted ground truth.

    ``INCONCLUSIVE`` and ``UNDERPOWERED`` are separate because they are
    different kinds of statement, and collapsing them cost the benchmark its
    meaning. The first is a *finding about the world*: the interval rules out
    the claimed effect but not a smaller one, so something is there and it is
    less than was claimed. The second is a *statement about the design*: the
    interval is too wide to separate any hypothesis from any other, and the
    institution is declining to answer.

    They were one value until M13. ``derive_verdict`` had always distinguished
    them in its reason string — one says "something is there, less than was
    claimed", the other says "this is a statement about the design, not the
    world" — while returning the same enum member for both. The consequence was
    not merely that abstention was scored as an error. It was that when the
    truth happened to *be* ``inconclusive``, an arm that could say nothing at
    all was credited with having said the right thing. Every arm's v2 accuracy
    was inflated that way, by four to nine items out of sixty.

    ``UNDERPOWERED`` is never a truth. The oracle measures at forty seeds of
    twenty thousand samples and is never short of power, so
    :func:`~nullius.bank.truth.classify` cannot return it and an abstention can
    never be scored correct by accident.
    """

    SUPPORTED = "supported"
    REFUTED = "refuted"
    NO_EFFECT = "no_effect"
    CONDITIONAL = "conditional"
    INCONCLUSIVE = "inconclusive"
    """A real effect, smaller than the one claimed. A finding, not a shrug."""

    UNDERPOWERED = "underpowered"
    """The design cannot separate the hypotheses. An abstention, not a finding."""
