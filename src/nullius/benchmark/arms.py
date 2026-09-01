"""The baseline ladder: eight arms, differing only in institutional structure.

``docs/04-evaluation.md`` §3 requires every arm to run on identical bank items,
identical compute, identical model, identical seeds and identical data access,
so that the only thing varying is the structure being tested. This module is
where the arms are declared, and it declares them as *switches over one
pipeline* rather than as eight programs. Eight programs would differ in a
thousand incidental ways, and the comparison would measure those.

**The awkward arm is B3, and the awkwardness is informative.** "Multi-role, no
adversary" also means no preregistration and no Custodian — but this
repository makes preregistration structural: a trigger refuses a run without a
prior locked registration, and a `CHECK` constraint refuses a holdout metric
from anyone but the Custodian. The invariants that make the institution
trustworthy make its own ablation unrepresentable through the ledger.

So B3 does not go through the ledger. It compiles and runs the same experiment
and computes its verdict from the **development split** — the data it was
allowed to look at while tuning. That is not a workaround; it is exactly what
"no custodian" means, and it should produce a measurably optimistic result. If
it does not, the Custodian is not earning its place.

**What a mock provider can and cannot measure here.** B0 and B3–B7 differ in
*mechanism*: which split is evaluated, whether a design is locked before it
runs, whether detectors may block a claim, whether an independent replication
is required. All of that is deterministic code and is measured honestly with
no model at all. B1 and B2 differ from B3 in *agent behaviour* — one model
call versus several — and under a canned provider they measure the can. Their
numbers are reported and labelled, never quietly averaged in with the rest.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = ["LADDER", "LADDER_V4", "Arm", "ArmKind", "arm_named", "mechanism_arms"]


class ArmKind(StrEnum):
    """How an arm reaches a verdict, which decides what code path it takes."""

    CONSTANT = "constant"
    """Answers the same thing every time, without looking. B0."""

    DIRECT = "direct"
    """One agent, its own code path, no ledger and no custody. B1 and B2."""

    UNCUSTODIED = "uncustodied"
    """The full experiment, judged on the split it was allowed to tune on. B3."""

    INSTITUTIONAL = "institutional"
    """Through the ledger: locked registration, Custodian, computed verdict. B4+."""


@dataclass(frozen=True, slots=True)
class Arm:
    """One rung of the ladder.

    Every field below the identity block is a mechanism that can be switched
    off, and each one is switched off in exactly one place in the runner. An
    arm is therefore a claim about which mechanisms are present, and nothing
    else differs.
    """

    arm_id: str
    label: str
    isolates: str
    kind: ArmKind

    preregistered: bool = False
    """A design hash locked before any run exists."""

    custodian: bool = False
    """Verdict computed on a split the experiment never saw."""

    adversary: bool = False
    """Detectors and the Skeptic may raise objections that block promotion."""

    replication: bool = False
    """An independent rerun is required before the top confidence level."""

    reviewer: bool = False
    memory: bool = False
    """Institutional memory carries across bank items rather than being wiped."""

    iterations: int = 1
    """How many times the agent may revise before committing. B2's only change."""

    adaptive_seeds: bool = False
    """Spend seeds where they would change the answer, to a declared ceiling."""

    model_dependent: bool = False
    """Whether this arm's behaviour is dominated by the language model.

    Set for B1 and B2. Under a mock provider their results describe the mock,
    and the report says so rather than presenting eight comparable numbers.
    """

    def as_dict(self) -> dict[str, Any]:
        return {
            "arm_id": self.arm_id,
            "label": self.label,
            "isolates": self.isolates,
            "kind": self.kind.value,
            "preregistered": self.preregistered,
            "custodian": self.custodian,
            "adversary": self.adversary,
            "replication": self.replication,
            "reviewer": self.reviewer,
            "memory": self.memory,
            "iterations": self.iterations,
            "adaptive_seeds": self.adaptive_seeds,
            "model_dependent": self.model_dependent,
        }

    def __str__(self) -> str:
        return f"{self.arm_id} {self.label}"


LADDER: tuple[Arm, ...] = (
    Arm(
        arm_id="B0",
        label="Oracle-null",
        isolates="the floor, and any imbalance in the bank",
        kind=ArmKind.CONSTANT,
    ),
    Arm(
        arm_id="B1",
        label="Single-shot",
        isolates="the naive baseline everyone actually ships",
        kind=ArmKind.DIRECT,
        model_dependent=True,
    ),
    Arm(
        arm_id="B2",
        label="Single-agent + loop",
        isolates="whether iteration alone helps",
        kind=ArmKind.DIRECT,
        iterations=3,
        model_dependent=True,
    ),
    Arm(
        arm_id="B3",
        label="Multi-role, no adversary",
        isolates="whether role decomposition alone helps",
        kind=ArmKind.UNCUSTODIED,
    ),
    Arm(
        arm_id="B4",
        label="B3 + preregistration + custodian",
        isolates="how much comes from mechanism rather than from agents",
        kind=ArmKind.INSTITUTIONAL,
        preregistered=True,
        custodian=True,
    ),
    Arm(
        arm_id="B5",
        label="B4 + Skeptic",
        isolates="the value of adversarial challenge",
        kind=ArmKind.INSTITUTIONAL,
        preregistered=True,
        custodian=True,
        adversary=True,
    ),
    Arm(
        arm_id="B6",
        label="Full institution",
        isolates="replication, review and memory on top of challenge",
        kind=ArmKind.INSTITUTIONAL,
        preregistered=True,
        custodian=True,
        adversary=True,
        replication=True,
        reviewer=True,
        memory=True,
    ),
    Arm(
        arm_id="B7",
        label="Full - memory",
        isolates="institutional memory's own contribution",
        kind=ArmKind.INSTITUTIONAL,
        preregistered=True,
        custodian=True,
        adversary=True,
        replication=True,
        reviewer=True,
        memory=False,
    ),
)
"""The ladder of ``docs/04-evaluation.md`` §3, in order.

Frozen at eight arms, because protocols v1 to v3 hash it. Extending the ladder
is extending a registration, so v4 uses :data:`LADDER_V4` and the earlier
protocols keep verifying against the arms they were registered with.

B7 differs from B6 in exactly one field. That is the whole point of expressing
arms as switches: an ablation that differs in one boolean cannot accidentally
differ in anything else.
"""


LADDER_V4: tuple[Arm, ...] = (
    *LADDER,
    Arm(
        arm_id="B8",
        label="Full + adaptive seeding",
        isolates="whether spending seeds where they matter beats spending them evenly",
        kind=ArmKind.INSTITUTIONAL,
        preregistered=True,
        custodian=True,
        adversary=True,
        replication=True,
        reviewer=True,
        memory=True,
        adaptive_seeds=True,
    ),
)
"""The ladder with the M14 arm, registered under protocol v4.

B8 differs from B6 in one boolean, which is the same discipline every other
rung follows. It exists because v3 measured a quarter of every institutional
arm's answers as abstentions at a seed count the linter had passed as
adequately powered — the design was powered to *detect* the claimed effect and
not to *exclude* a smaller one, and those are different questions.
"""


def arm_named(arm_id: str, ladder: tuple[Arm, ...] = LADDER_V4) -> Arm:
    """Look an arm up by id, raising rather than guessing."""
    for arm in ladder:
        if arm.arm_id == arm_id:
            return arm
    raise KeyError(f"no such arm {arm_id!r}; the ladder is {[a.arm_id for a in LADDER]}")


def mechanism_arms() -> tuple[Arm, ...]:
    """The arms whose differences are code rather than model behaviour.

    These are the ones a mock-driven run can speak to. Kept as a function
    rather than a constant so that adding a model-dependent arm cannot quietly
    slip into the set that gets reported as measured.
    """
    return tuple(arm for arm in LADDER if not arm.model_dependent)
