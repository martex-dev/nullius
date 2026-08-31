"""The benchmark: the project measuring itself, under its own rules.

:mod:`~nullius.benchmark.arms` declares the B0–B7 ladder as switches over one
pipeline, so that two arms differ in the mechanism named and in nothing else.
:mod:`~nullius.benchmark.protocol` fixes the analysis plan and hashes it before
any result exists.

The ordering is the point. The protocol is committed in its own change, ahead
of the runner that produces numbers, so the git history itself demonstrates
that the plan predates the results rather than merely asserting it.

:mod:`~nullius.benchmark.runner` executes the ladder and
:mod:`~nullius.benchmark.metrics` scores it against the plan the protocol
fixed. Neither has a knob the protocol did not already set.
"""

from __future__ import annotations

from nullius.benchmark.arms import LADDER, Arm, ArmKind, arm_named, mechanism_arms
from nullius.benchmark.metrics import (
    ArmMetrics,
    Comparison,
    LadderReport,
    compare_to_baseline,
    score_arm,
    score_ladder,
)
from nullius.benchmark.protocol import (
    CONFIDENCE_AS_PROBABILITY,
    DEFAULT_PROTOCOL_PATH,
    Protocol,
    ProtocolVerification,
    build_protocol,
    read_protocol,
    verify_protocol,
    write_protocol,
)
from nullius.benchmark.runner import (
    ArmOutcome,
    ArmRun,
    mechanisms_for,
    run_arm,
    run_ladder,
)

__all__ = [
    "CONFIDENCE_AS_PROBABILITY",
    "DEFAULT_PROTOCOL_PATH",
    "LADDER",
    "Arm",
    "ArmKind",
    "ArmMetrics",
    "ArmOutcome",
    "ArmRun",
    "Comparison",
    "LadderReport",
    "Protocol",
    "ProtocolVerification",
    "arm_named",
    "build_protocol",
    "compare_to_baseline",
    "mechanism_arms",
    "mechanisms_for",
    "read_protocol",
    "run_arm",
    "run_ladder",
    "score_arm",
    "score_ladder",
    "verify_protocol",
    "write_protocol",
]
