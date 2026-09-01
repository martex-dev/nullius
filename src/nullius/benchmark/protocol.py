"""The preregistered benchmark protocol.

The project's own claim is evaluated under the same rule it imposes on its
agents: the analysis plan is fixed, hashed and committed **before** any result
exists. Not as ceremony — as the only thing that makes the eventual numbers
worth reading. Every degree of freedom exercised after seeing results is a way
to get the answer one wanted, and there are a great many of them here: which
arms to compare, which metric to headline, how confidence maps to a
probability, how many bootstrap resamples, whether to correct for multiplicity.
Each is settled below, in advance, in a file whose hash is in the git history.

**The prediction is registered too.** ``docs/04-evaluation.md`` §3 states one
in advance and asks to be held to it: *B4 will capture most of the gain over
B3* — that is, cheap mechanism will beat expensive agents. It is recorded here
so the project can be publicly wrong about it. A prediction written down after
the fact is not a prediction.

**The bank is pinned.** The protocol hash covers the bank items and the
measured ground truth they are scored against, so a protocol cannot be
satisfied by quietly changing what "correct" means. Re-locking the bank
invalidates the protocol and ``nullius benchmark verify`` says so.

**What is not settled here** is which arms a given run can speak to. Under a
mock provider the arms differing only in model behaviour describe the mock;
the protocol names them and requires the report to label them, rather than
pretending a canned provider measures an agent.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nullius.bank.items import BANK_V1, BANK_V2
from nullius.bank.lock import DEFAULT_LOCK_PATH as TRUTH_LOCK_PATH
from nullius.bank.lock import V2_LOCK_PATH as V2_TRUTH_LOCK_PATH
from nullius.benchmark.arms import LADDER, LADDER_V4, LADDER_V6, Arm
from nullius.db.enums import CONFIDENCE_ORDER, ClaimConfidence, Verdict
from nullius.kernel import ADAPTIVE_SEED_CEILING
from nullius.util.canonical import canonical_json, sha256_of

__all__ = [
    "CONFIDENCE_AS_PROBABILITY",
    "DEFAULT_PROTOCOL_PATH",
    "LATEST_PROTOCOL_VERSION",
    "PROTOCOL_VERSION",
    "PROTOCOL_VERSIONS",
    "V2_PROTOCOL_PATH",
    "V3_PROTOCOL_PATH",
    "V4_PROTOCOL_PATH",
    "V5_PROTOCOL_PATH",
    "V6_PROTOCOL_PATH",
    "Protocol",
    "ProtocolVerification",
    "build_protocol",
    "read_protocol",
    "verify_protocol",
    "write_protocol",
]

DEFAULT_PROTOCOL_PATH = Path("benchmark/protocol.lock.json")

PROTOCOL_VERSION = "1"
PRIMARY_METRIC = "verdict_accuracy"

REGISTERED_PREDICTION = (
    "B4 captures most of the gain over B3 - that is, adding preregistration "
    "and the Custodian to a role-decomposed pipeline improves verdict accuracy "
    "more than adding the Skeptic, replication, review and memory do on top of "
    "it. If true, the finding is that cheap mechanisms beat expensive agents."
)

EXCLUSION_RULES = (
    "An item whose lifecycle halts before a verdict counts as incorrect for "
    "that arm rather than being dropped, because dropping it would reward an "
    "arm for failing to answer the questions it finds hard.",
    "An arm whose behaviour is dominated by the language model is reported "
    "with its results labelled model-dependent, and is excluded from any claim "
    "about mechanism when the run used a mock provider.",
    "Every arm's outcome is reported, including the ones that make the project "
    "look bad. There is no rule under which a result is withheld.",
)


THE_CLAIM = (
    "Institutional structure - preregistration, adversarial challenge, "
    "independent replication, and evidence-typed memory - improves the accuracy "
    "and calibration of autonomous empirical research relative to an "
    "unstructured agent, at a measurable cost in compute and tokens."
)

METRICS = (
    "verdict_accuracy",
    "null_accuracy",
    "brier",
    "expected_calibration_error",
    "false_discovery_rate",
    "usd_per_correct_claim",
    "effect_size_error",
)


ARM_FIELDS_V1 = (
    "arm_id",
    "label",
    "isolates",
    "kind",
    "preregistered",
    "custodian",
    "adversary",
    "replication",
    "reviewer",
    "memory",
    "iterations",
    "model_dependent",
)
"""The arm fields protocols v1 to v3 registered.

Pinned as a list rather than taken from ``Arm.as_dict()`` because adding a
field to that method changed the hash of three protocols that are supposed to
be immutable, the first time an arm gained one. The verification added in M12a
caught it; this is the fix. A protocol records the arms *as they were
described when it was registered*, and a later field is a later registration.
"""

ARM_FIELDS_V6 = (
    *ARM_FIELDS_V1,
    "adaptive_seeds",
    "conservative_escalation",
)
ARM_FIELDS_V4 = (*ARM_FIELDS_V1, "adaptive_seeds")


def _project(arm: Arm, fields: tuple[str, ...]) -> dict[str, Any]:
    """One arm, reduced to the fields a given protocol registered."""
    full = arm.as_dict()
    return {name: full[name] for name in fields}


LATEST_PROTOCOL_VERSION = "6"

V2_PROTOCOL_PATH = Path("benchmark/protocol.v2.lock.json")
V3_PROTOCOL_PATH = Path("benchmark/protocol.v3.lock.json")
V4_PROTOCOL_PATH = Path("benchmark/protocol.v4.lock.json")
V5_PROTOCOL_PATH = Path("benchmark/protocol.v5.lock.json")
V6_PROTOCOL_PATH = Path("benchmark/protocol.v6.lock.json")

REPLICATES = 3
"""Passes over the bank per custodied arm, fixed before the run.

The v4 ladder measured why one is not enough: re-running the same arm moved
its accuracy by up to 0.100, six times the metric's resolution, because the
Custodian draws a fresh holdout for every registration. A contrast smaller
than that cannot be read from a single draw, and the contrasts this benchmark
exists to measure are smaller than that.

Three rather than more because the expensive arm costs about fifty minutes a
pass. Registered here so that "we ran it until it looked right" is not
available afterwards.
"""

V2_PREDICTION = (
    "The mechanism contrast B4 - B3 is positive and its 95% interval excludes "
    "zero. Adding preregistration and the Custodian to a role-decomposed "
    "pipeline improves verdict accuracy by a margin this design can actually "
    "resolve. If it holds, cheap mechanism beats expensive agents; if the "
    "interval spans zero, the prediction fails regardless of the point estimate."
)

V2_EXCLUSION_RULES = (
    *EXCLUSION_RULES,
    "The primary comparison is against B0, the arm that answers without "
    "looking. B1 was v1's baseline and is model-dependent, which under a mock "
    "provider made every comparison in the registered family uninterpretable "
    "for mechanism. B1 and B2 are still reported; nothing is compared against "
    "them.",
    "Brier score and calibration error are computed only over items where the "
    "arm asserted an effect. The confidence rubric measures evidence *for an "
    "effect*, so a correct 'no effect' answer necessarily carries weak "
    "evidence and scored as gross underconfidence in v1 - an artefact of the "
    "mapping rather than a property of the institution. Restricting to "
    "assertions is the subpopulation where the rubric's quantity and the "
    "scored outcome are the same quantity.",
    "The registered prediction is adjudicated on an interval, not on a point "
    "estimate. v1's rule compared two point estimates and returned 'upheld' "
    "for a one-item difference on a twenty-item bank.",
)

#: What differs between registered protocol versions. Everything else - the
#: metrics, the resample count, alpha, the correction - is shared, because a
#: version that changed all of them would not be a revision of the same
#: benchmark.
#:
#: v2 exists because running v1 exposed three flaws in it, each recorded in
#: ``BUILD_PLAN.md`` and none of them patched in place. Editing a hashed
#: preregistration to fix its own findings is the exact substitution this file
#: exists to prevent, so v1 stays on disk, still verifying, still wrong in the
#: three ways it was wrong.
V3_METRICS = (
    *METRICS,
    "coverage",
    "assertion_accuracy",
)

V3_PREDICTION = (
    "Separating abstention from finding lowers every arm's verdict accuracy, "
    "because v2 credited an arm that could say nothing with having said the "
    "right thing whenever the truth happened to be 'inconclusive'. The "
    "institutional arms will separate on coverage: B6 answers more of the bank "
    "than B3 does, and the interval on that difference excludes zero. If "
    "coverage does not separate, the institution's advantage is in what it "
    "says and not in how much it is able to say."
)

V3_EXCLUSION_RULES = (
    *V2_EXCLUSION_RULES,
    "An abstention is reported, never dropped. 'underpowered' still counts as "
    "incorrect for verdict accuracy, because the first exclusion rule refuses "
    "to reward an arm for declining the questions it found hardest. It is also "
    "counted separately, because a system that knows what it cannot measure is "
    "not the same as one that guesses.",
    "Coverage and assertion accuracy are reported together and neither is the "
    "primary metric. An arm can drive assertion accuracy to 1.0 by answering "
    "only what it is sure of, and coverage is what stops that reading as a "
    "good result.",
)


V4_PREDICTION = (
    "Adaptive seeding raises coverage. B8 abstains on fewer bank items than B6 "
    "does, and the 95% interval on that difference excludes zero."
)

V4_ADJUDICATED = {
    "treatment": "B8",
    "baseline": "B6",
    "quantity": "coverage",
    "direction": "greater",
}
"""The quantity the prediction is about, named so the rule cannot drift from it.

v3 registered a prediction about coverage and inherited an adjudication rule
that tested accuracy, so the run reported "refuted" after measuring something
the prediction did not mention. It was right by accident. Storing the arms, the
quantity and the direction as data — and deriving the verdict from them —
is what stops a prediction and its test being edited apart.
"""

V4_EXCLUSION_RULES = (
    *V3_EXCLUSION_RULES,
    "The adjudicated quantity is named in the protocol and the verdict is "
    "computed from it. A protocol whose prediction and whose adjudication rule "
    "describe different quantities has not registered anything, however "
    "precise either one is on its own.",
    "Adaptive seeding may only spend seeds the registration already declared. "
    "The full seed set is derived from seed_root at registration and has length "
    "max_seeds; escalation chooses how far down that list to go and never which "
    "seeds are on it.",
    "The escalation decision reads the development split only. Deciding how "
    "much more data to collect by looking at the quantity the verdict will be "
    "computed from is optional stopping, and would buy significance rather "
    "than resolution.",
)

V5_PREDICTION = (
    "Replication narrows the ladder rather than reordering it. Averaging three "
    "custody draws per arm leaves B8 - B6 on coverage positive with an interval "
    "still excluding zero, and leaves B4 - B3 on verdict accuracy spanning zero "
    "- the contrast that flipped between the v3 and v4 single draws. If B4 - B3 "
    "separates under replication, the v4 reading was right and this protocol's "
    "caution was wrong."
)

V5_ADJUDICATED = {
    "treatment": "B8",
    "baseline": "B6",
    "quantity": "coverage",
    "direction": "greater",
}

V5_EXCLUSION_RULES = (
    *V4_EXCLUSION_RULES,
    "Only custodied arms are replicated. An uncustodied arm reads the "
    "development split, which is fixed by seeds derived from the item id, and "
    "returns identical results however often it runs - measured, not assumed: "
    "running the ladder twice left B0 through B3 identical to three decimals "
    "while every custodied arm moved. Replicating a deterministic arm reports "
    "a spread of zero as though it were evidence of stability.",
    "Replicates are averaged within a bank item before arms are compared, so "
    "the bootstrap continues to resample items - the population the bank can "
    "speak for. Pooling replicates as independent observations would treat "
    "three looks at one question as three questions and shrink every interval "
    "by a factor the design has not earned.",
)

V6_PREDICTION = (
    "Sizing the escalation from an upper bound on the noise rather than a point "
    "estimate raises coverage. B9 abstains on fewer bank items than B8 does, and "
    "the 95% interval on that difference excludes zero. It should also cost more "
    "per item, because a bound that errs towards more data buys more data; if "
    "cost per correct claim rises without coverage improving, the bound is only "
    "expensive."
)

V6_ADJUDICATED = {
    "treatment": "B9",
    "baseline": "B8",
    "quantity": "coverage",
    "direction": "greater",
}

V6_EXCLUSION_RULES = (
    *V5_EXCLUSION_RULES,
    "The escalation's standard deviation is estimated from the paired "
    "differences of the mandatory seeds, and at five observations that estimate "
    "lands under half the true value about 9% of the time. B9 replaces it with "
    "an 80% chi-square upper confidence limit. The asymmetry is deliberate: "
    "over-buying costs compute, which this project measured at 5% of total spend "
    "for several times the seed-runs, and under-buying costs an answer.",
)

PROTOCOL_VERSIONS: dict[str, dict[str, Any]] = {
    "6": {
        "items": BANK_V2,
        "truth_lock": V2_TRUTH_LOCK_PATH,
        "path": V6_PROTOCOL_PATH,
        "baseline_arm": "B0",
        "prediction": V6_PREDICTION,
        "exclusion_rules": V6_EXCLUSION_RULES,
        "metrics": V3_METRICS,
        "arms": LADDER_V6,
        "arm_fields": ARM_FIELDS_V6,
        "extra_statistics": {
            "calibration_scope": "asserted_effects",
            "adjudication": "named_contrast",
            "adjudicated": dict(V6_ADJUDICATED),
            "verdict_vocabulary": [v.value for v in Verdict],
            "adaptive_seed_ceiling": ADAPTIVE_SEED_CEILING,
            "replicates": REPLICATES,
            "replicated_arms": "custodied",
            "escalation_confidence": 0.80,
        },
    },
    "5": {
        "items": BANK_V2,
        "truth_lock": V2_TRUTH_LOCK_PATH,
        "path": V5_PROTOCOL_PATH,
        "baseline_arm": "B0",
        "prediction": V5_PREDICTION,
        "exclusion_rules": V5_EXCLUSION_RULES,
        "metrics": V3_METRICS,
        "arms": LADDER_V4,
        "arm_fields": ARM_FIELDS_V4,
        "extra_statistics": {
            "calibration_scope": "asserted_effects",
            "adjudication": "named_contrast",
            "adjudicated": dict(V5_ADJUDICATED),
            "verdict_vocabulary": [v.value for v in Verdict],
            "adaptive_seed_ceiling": ADAPTIVE_SEED_CEILING,
            "replicates": REPLICATES,
            "replicated_arms": "custodied",
        },
    },
    "4": {
        "items": BANK_V2,
        "truth_lock": V2_TRUTH_LOCK_PATH,
        "path": V4_PROTOCOL_PATH,
        "baseline_arm": "B0",
        "prediction": V4_PREDICTION,
        "exclusion_rules": V4_EXCLUSION_RULES,
        "metrics": V3_METRICS,
        "arms": LADDER_V4,
        "arm_fields": ARM_FIELDS_V4,
        "extra_statistics": {
            "calibration_scope": "asserted_effects",
            "adjudication": "named_contrast",
            "adjudicated": dict(V4_ADJUDICATED),
            "verdict_vocabulary": [v.value for v in Verdict],
            "adaptive_seed_ceiling": ADAPTIVE_SEED_CEILING,
        },
    },
    "1": {
        "items": BANK_V1,
        "truth_lock": TRUTH_LOCK_PATH,
        "path": DEFAULT_PROTOCOL_PATH,
        "baseline_arm": "B1",
        "prediction": REGISTERED_PREDICTION,
        "exclusion_rules": EXCLUSION_RULES,
        "metrics": METRICS,
        "arms": LADDER,
        "arm_fields": ARM_FIELDS_V1,
        "extra_statistics": {},
    },
    "3": {
        "items": BANK_V2,
        "truth_lock": V2_TRUTH_LOCK_PATH,
        "path": V3_PROTOCOL_PATH,
        "baseline_arm": "B0",
        "prediction": V3_PREDICTION,
        "exclusion_rules": V3_EXCLUSION_RULES,
        "metrics": V3_METRICS,
        "arms": LADDER,
        "arm_fields": ARM_FIELDS_V1,
        "extra_statistics": {
            "calibration_scope": "asserted_effects",
            "adjudication": "interval_excludes_zero",
            "verdict_vocabulary": [v.value for v in Verdict],
        },
    },
    "2": {
        "items": BANK_V2,
        "truth_lock": V2_TRUTH_LOCK_PATH,
        "path": V2_PROTOCOL_PATH,
        "baseline_arm": "B0",
        "prediction": V2_PREDICTION,
        "exclusion_rules": V2_EXCLUSION_RULES,
        "metrics": METRICS,
        "arms": LADDER,
        "arm_fields": ARM_FIELDS_V1,
        "extra_statistics": {
            "calibration_scope": "asserted_effects",
            "adjudication": "interval_excludes_zero",
        },
    },
}

RESAMPLES = 2000
"""Bootstrap resamples, fixed in advance.

Stated because it is a researcher degree of freedom like any other: a
percentile interval computed at whatever resample count first excluded zero
would be a result about the resampling.
"""

ALPHA = 0.05
FDR_METHOD = "benjamini-hochberg"

CONFIDENCE_AS_PROBABILITY: dict[str, float] = {
    ClaimConfidence.CONTESTED.value: 0.30,
    ClaimConfidence.SPECULATIVE.value: 0.40,
    ClaimConfidence.SUGGESTIVE.value: 0.55,
    ClaimConfidence.SUPPORTED.value: 0.75,
    ClaimConfidence.WELL_SUPPORTED.value: 0.90,
}
"""How a computed confidence level becomes a probability, for scoring.

Calibration needs a number, and the rubric produces a five-level ordinal. The
mapping between them is a modelling choice that changes every Brier score and
every calibration curve, so it is fixed here rather than chosen once the
curves are visible.

The values are deliberately unflattering to the institution: the top level is
0.90 rather than 0.95, so a confident error is punished hard, and the bottom
is 0.30 rather than 0.05, so a contested claim that turns out right earns
little credit. Both directions cost the system points. That is the correct
bias for a project measuring itself.
"""


@dataclass(frozen=True, slots=True)
class Protocol:
    """The analysis plan, fixed before results and identified by its hash."""

    version: str
    registered_at: str
    claim: str
    prediction: str
    primary_metric: str
    metrics: tuple[str, ...]
    arms: tuple[dict[str, Any], ...]
    confidence_as_probability: dict[str, float]
    statistics: dict[str, Any]
    bank: dict[str, Any]
    exclusion_rules: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        """Everything the hash covers, in canonical order."""
        return {
            "version": self.version,
            "registered_at": self.registered_at,
            "claim": self.claim,
            "prediction": self.prediction,
            "primary_metric": self.primary_metric,
            "metrics": list(self.metrics),
            "arms": [dict(arm) for arm in self.arms],
            "confidence_as_probability": dict(self.confidence_as_probability),
            "statistics": dict(self.statistics),
            "bank": dict(self.bank),
            "exclusion_rules": list(self.exclusion_rules),
        }

    @property
    def protocol_hash(self) -> str:
        return sha256_of(self.payload())

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Protocol:
        return cls(
            version=payload["version"],
            registered_at=payload["registered_at"],
            claim=payload["claim"],
            prediction=payload["prediction"],
            primary_metric=payload["primary_metric"],
            metrics=tuple(payload["metrics"]),
            arms=tuple(payload["arms"]),
            confidence_as_probability=dict(payload["confidence_as_probability"]),
            statistics=dict(payload["statistics"]),
            bank=dict(payload["bank"]),
            exclusion_rules=tuple(payload["exclusion_rules"]),
        )

    def __str__(self) -> str:
        return (
            f"protocol v{self.version} registered {self.registered_at}, "
            f"{len(self.arms)} arms, hash {self.protocol_hash[:16]}"
        )


def build_protocol(
    *,
    registered_at: str | None = None,
    truth_lock: Path | None = None,
    version: str = PROTOCOL_VERSION,
) -> Protocol:
    """Assemble the protocol from the bank and the ladder as they stand.

    ``registered_at`` is a date, not a timestamp: the protocol is a document,
    and a second-resolution clock in a hashed artifact only makes the hash
    unreproducible without making the record more honest.
    """
    settings = PROTOCOL_VERSIONS[version]
    items = settings["items"]
    truth_lock = truth_lock or settings["truth_lock"]
    truth = json.loads(Path(truth_lock).read_text(encoding="utf-8"))
    return Protocol(
        version=version,
        registered_at=registered_at or dt.datetime.now(dt.UTC).date().isoformat(),
        claim=THE_CLAIM,
        prediction=settings["prediction"],
        primary_metric=PRIMARY_METRIC,
        metrics=settings["metrics"],
        arms=tuple(_project(arm, settings["arm_fields"]) for arm in settings["arms"]),
        confidence_as_probability=dict(CONFIDENCE_AS_PROBABILITY),
        statistics={
            "pairing": "paired over bank items",
            "interval": "percentile bootstrap over items",
            "resamples": RESAMPLES,
            "alpha": ALPHA,
            "multiplicity": FDR_METHOD,
            "baseline_arm": settings["baseline_arm"],
            "confidence_order": [level.value for level in CONFIDENCE_ORDER],
            # Only versions that registered these carry them. v1 did not, and
            # adding a key to a hashed payload after the fact would change the
            # hash of a protocol that is supposed to be immutable — so readers
            # of v1 fall back to the behaviour v1's results were produced
            # under, and v2 states its choices explicitly.
            **settings["extra_statistics"],
        },
        bank={
            "n_items": len(items),
            "items_hash": sha256_of([item.as_dict() for item in items]),
            "truth_lock_hash": sha256_of(truth),
        },
        exclusion_rules=settings["exclusion_rules"],
    )


def write_protocol(protocol: Protocol, path: Path = DEFAULT_PROTOCOL_PATH) -> Path:
    """Write the protocol and its hash. Refuses to overwrite a different one.

    Overwriting is how a preregistration stops being one. Editing the file by
    hand is still possible, of course — but it leaves a diff in a committed
    file whose hash changed, which is the whole mechanism.
    """
    if path.exists():
        existing = read_protocol(path)
        if existing.protocol_hash != protocol.protocol_hash:
            raise ValueError(
                f"{path} already holds protocol {existing.protocol_hash[:16]}; "
                f"refusing to replace it with {protocol.protocol_hash[:16]}. "
                "Registering a second protocol means a new version, recorded as "
                "a change rather than written over the first."
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"protocol": protocol.payload(), "protocol_hash": protocol.protocol_hash}
    path.write_text(canonical_json(body) + "\n", encoding="utf-8")
    return path


def read_protocol(path: Path = DEFAULT_PROTOCOL_PATH) -> Protocol:
    """Load a registered protocol."""
    body = json.loads(path.read_text(encoding="utf-8"))
    return Protocol.from_dict(body["protocol"])


@dataclass(frozen=True, slots=True)
class ProtocolVerification:
    """Whether a registered protocol still describes the world it was written for."""

    ok: bool
    protocol_hash: str
    stored_hash: str
    bank_unchanged: bool
    ladder_unchanged: bool
    rebuilds_identically: bool = True
    """Whether ``build_protocol`` still produces this exact payload.

    Added after a change to the builder altered what v1 would rebuild to while
    leaving every other check green: the bank was unchanged, the arms were
    unchanged, and the stored hash still matched its own content, so nothing
    complained. A registered protocol that the code can no longer reproduce has
    been edited in effect, and this is the check that says so.
    """

    def __str__(self) -> str:
        if self.ok:
            return f"protocol verified: {self.protocol_hash[:16]}, bank and ladder unchanged"
        parts: list[str] = []
        if self.protocol_hash != self.stored_hash:
            parts.append("the stored hash does not match the stored content")
        if not self.rebuilds_identically:
            parts.append("the code no longer rebuilds this protocol to the payload it holds")
        if not self.bank_unchanged:
            parts.append("the question bank or its ground truth has changed since registration")
        if not self.ladder_unchanged:
            parts.append("the arm definitions have changed since registration")
        return "protocol NOT verified: " + "; ".join(parts)


def verify_protocol(
    path: Path = DEFAULT_PROTOCOL_PATH, *, truth_lock: Path | None = None
) -> ProtocolVerification:
    """Check that the protocol is intact and still describes the current bank.

    Three separate questions, reported separately: does the file's hash match
    its own content, is the bank the one the protocol was registered against,
    and are the arms still defined the way they were. A run whose protocol
    fails any of these is not a preregistered run.
    """
    body = json.loads(path.read_text(encoding="utf-8"))
    protocol = Protocol.from_dict(body["protocol"])
    stored_hash = str(body.get("protocol_hash", ""))

    # Rebuilt under the version the file itself declares, so that verifying v1
    # never silently checks it against v2's bank.
    current = build_protocol(
        registered_at=protocol.registered_at,
        truth_lock=truth_lock,
        version=protocol.version,
    )
    bank_unchanged = protocol.bank == current.bank
    ladder_unchanged = protocol.arms == current.arms
    rebuilds = current.protocol_hash == protocol.protocol_hash

    return ProtocolVerification(
        ok=(
            protocol.protocol_hash == stored_hash
            and bank_unchanged
            and ladder_unchanged
            and rebuilds
        ),
        protocol_hash=protocol.protocol_hash,
        stored_hash=stored_hash,
        bank_unchanged=bank_unchanged,
        ladder_unchanged=ladder_unchanged,
        rebuilds_identically=rebuilds,
    )
