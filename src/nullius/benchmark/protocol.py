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

from nullius.bank.items import BANK_V1
from nullius.bank.lock import DEFAULT_LOCK_PATH as TRUTH_LOCK_PATH
from nullius.benchmark.arms import LADDER
from nullius.db.enums import CONFIDENCE_ORDER, ClaimConfidence
from nullius.util.canonical import canonical_json, sha256_of

__all__ = [
    "CONFIDENCE_AS_PROBABILITY",
    "DEFAULT_PROTOCOL_PATH",
    "PROTOCOL_VERSION",
    "Protocol",
    "ProtocolVerification",
    "build_protocol",
    "read_protocol",
    "verify_protocol",
    "write_protocol",
]

DEFAULT_PROTOCOL_PATH = Path("benchmark/protocol.lock.json")

PROTOCOL_VERSION = "1"

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


def build_protocol(
    *,
    registered_at: str | None = None,
    truth_lock: Path = TRUTH_LOCK_PATH,
) -> Protocol:
    """Assemble the protocol from the bank and the ladder as they stand.

    ``registered_at`` is a date, not a timestamp: the protocol is a document,
    and a second-resolution clock in a hashed artifact only makes the hash
    unreproducible without making the record more honest.
    """
    truth = json.loads(truth_lock.read_text(encoding="utf-8"))
    return Protocol(
        version=PROTOCOL_VERSION,
        registered_at=registered_at or dt.datetime.now(dt.UTC).date().isoformat(),
        claim=THE_CLAIM,
        prediction=REGISTERED_PREDICTION,
        primary_metric=PRIMARY_METRIC,
        metrics=METRICS,
        arms=tuple(arm.as_dict() for arm in LADDER),
        confidence_as_probability=dict(CONFIDENCE_AS_PROBABILITY),
        statistics={
            "pairing": "paired over bank items",
            "interval": "percentile bootstrap over items",
            "resamples": RESAMPLES,
            "alpha": ALPHA,
            "multiplicity": FDR_METHOD,
            "baseline_arm": "B1",
            "confidence_order": [level.value for level in CONFIDENCE_ORDER],
        },
        bank={
            "n_items": len(BANK_V1),
            "items_hash": sha256_of([item.as_dict() for item in BANK_V1]),
            "truth_lock_hash": sha256_of(truth),
        },
        exclusion_rules=EXCLUSION_RULES,
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

    def __str__(self) -> str:
        if self.ok:
            return f"protocol verified: {self.protocol_hash[:16]}, bank and ladder unchanged"
        parts: list[str] = []
        if self.protocol_hash != self.stored_hash:
            parts.append("the stored hash does not match the stored content")
        if not self.bank_unchanged:
            parts.append("the question bank or its ground truth has changed since registration")
        if not self.ladder_unchanged:
            parts.append("the arm definitions have changed since registration")
        return "protocol NOT verified: " + "; ".join(parts)


def verify_protocol(
    path: Path = DEFAULT_PROTOCOL_PATH, *, truth_lock: Path = TRUTH_LOCK_PATH
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

    current = build_protocol(registered_at=protocol.registered_at, truth_lock=truth_lock)
    bank_unchanged = protocol.bank == current.bank
    ladder_unchanged = protocol.arms == current.arms

    return ProtocolVerification(
        ok=(protocol.protocol_hash == stored_hash and bank_unchanged and ladder_unchanged),
        protocol_hash=protocol.protocol_hash,
        stored_hash=stored_hash,
        bank_unchanged=bank_unchanged,
        ladder_unchanged=ladder_unchanged,
    )
