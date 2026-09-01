"""Assembling a paper out of what was registered and what was measured.

The same rule as :mod:`nullius.report`, applied to a different artifact: this
generates the document from the committed protocols and results rather than
from prose anybody wrote afterwards. No number here is typed. Every one is read
from a results file whose stored summary re-scores from its own per-item rows,
and every claim about what was predicted is read from a protocol whose hash is
in the git history.

**Why generate a paper at all.** The failure this guards against is specific
and extremely common: a project runs several protocols, one of them produces
the flattering result, and the write-up quietly becomes about that one. Here
every registered protocol appears, in order, with its prediction and its
outcome — including the four that were refuted and the two results that were
later retracted. The document cannot select, because it is not written; it is
enumerated.

**What it will not do.** It will not render if a protocol fails to verify or a
results file fails to re-score against the protocol it names. A paper whose
inputs no longer check out is worse than no paper, because it looks like
evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nullius.bank.items import BANK_V1, BANK_V2
from nullius.bank.lock import DEFAULT_LOCK_PATH, V2_LOCK_PATH, read_lock
from nullius.bank.truth import Truth
from nullius.benchmark.metrics import LadderReport, read_results
from nullius.benchmark.protocol import PROTOCOL_VERSIONS, Protocol, read_protocol, verify_protocol
from nullius.benchmark.runner import ArmRun

__all__ = [
    "BankProfile",
    "Chapter",
    "Paper",
    "assemble",
    "bank_profile",
    "results_path",
]


def results_path(version: str) -> Path:
    """Where a protocol's results live, derived rather than listed.

    This was a dict, and it went stale the moment a sixth protocol was
    registered — the paper raised a KeyError on a version it had never been told
    about. The same shape of bug had already put an unverified protocol past CI
    and run an eight-arm ladder under a nine-arm plan. Anything keyed by
    protocol version should be computed from the registry, not maintained
    beside it.
    """
    return (
        Path("benchmark/results.lock.json")
        if version == "1"
        else Path(f"benchmark/results.v{version}.lock.json")
    )


#: The paired-difference standard error the experiment actually achieves, taken
#: from the ledgers rather than assumed. Used only to express item difficulty in
#: units the institution can feel; it is not an input to any verdict.
MEASURED_EXPERIMENT_SE = 0.00348


@dataclass(frozen=True, slots=True)
class BankProfile:
    """How hard a bank is, in the units the experiment resolves."""

    name: str
    n_items: int
    n_null: int
    within_one_se: int
    within_two_se: int
    resolution: float
    """The smallest difference the primary metric can express: one item."""

    @property
    def null_fraction(self) -> float:
        return self.n_null / self.n_items if self.n_items else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "n_items": self.n_items,
            "n_null": self.n_null,
            "null_fraction": round(self.null_fraction, 4),
            "within_one_se": self.within_one_se,
            "within_two_se": self.within_two_se,
            "resolution": round(self.resolution, 4),
        }


def _distance_in_se(truth: Truth) -> float:
    edges = (truth.mde, -truth.mde, 0.5 * truth.mde, -0.5 * truth.mde)
    return min(abs(truth.effect - edge) for edge in edges) / MEASURED_EXPERIMENT_SE


def bank_profile(name: str, items: Any, lock_path: Path) -> BankProfile:
    """Difficulty measured from the locked truths, never asserted."""
    truths = list(read_lock(lock_path).values())
    distances = [_distance_in_se(t) for t in truths]
    return BankProfile(
        name=name,
        n_items=len(items),
        n_null=sum(1 for t in truths if t.is_null),
        within_one_se=sum(1 for d in distances if d <= 1.0),
        within_two_se=sum(1 for d in distances if d <= 2.0),
        resolution=1.0 / len(items) if items else 0.0,
    )


@dataclass(frozen=True, slots=True)
class Chapter:
    """One registered protocol, and whatever became of it.

    ``report`` is absent when a protocol was registered but never run, which is
    a state the document reports rather than hides — a plan with no result is
    part of the record too.
    """

    version: str
    protocol: Protocol
    report: LadderReport | None = None
    runs: tuple[ArmRun, ...] = ()

    @property
    def was_run(self) -> bool:
        return self.report is not None

    @property
    def verdict(self) -> str:
        if self.report is None:
            return "registered, not yet run"
        if self.report.prediction_upheld is None:
            return "not adjudicable"
        return "upheld" if self.report.prediction_upheld else "refuted"

    @property
    def n_arms(self) -> int:
        return len(self.protocol.arms)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "protocol_hash": self.protocol.protocol_hash,
            "registered_at": self.protocol.registered_at,
            "prediction": self.protocol.prediction,
            "verdict": self.verdict,
            "reason": self.report.prediction_reason if self.report else "",
        }


@dataclass(frozen=True, slots=True)
class Paper:
    """Everything the document says, assembled from checkable sources."""

    claim: str
    chapters: tuple[Chapter, ...]
    banks: tuple[BankProfile, ...]
    provider: str
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def run_chapters(self) -> tuple[Chapter, ...]:
        return tuple(c for c in self.chapters if c.was_run)

    @property
    def latest(self) -> Chapter | None:
        """The most recent chapter that actually produced results."""
        run = self.run_chapters
        return run[-1] if run else None

    @property
    def predictions_upheld(self) -> int:
        return sum(1 for c in self.run_chapters if c.report and c.report.prediction_upheld)

    @property
    def predictions_refuted(self) -> int:
        return sum(1 for c in self.run_chapters if c.report and c.report.prediction_upheld is False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "provider": self.provider,
            "chapters": [c.as_dict() for c in self.chapters],
            "banks": [b.as_dict() for b in self.banks],
            "predictions_upheld": self.predictions_upheld,
            "predictions_refuted": self.predictions_refuted,
            "problems": list(self.problems),
        }


def assemble(*, strict: bool = True) -> Paper:
    """Read every registered protocol and every committed result.

    ``strict`` refuses to assemble when a protocol fails to verify or a results
    file fails to re-score. Turning it off is for inspecting a broken state, not
    for publishing one, and the problems are carried on the paper either way so
    that a document built from damaged inputs says so on its own face.
    """
    problems: list[str] = []
    chapters: list[Chapter] = []

    for version in sorted(PROTOCOL_VERSIONS, key=int):
        settings = PROTOCOL_VERSIONS[version]
        path = Path(settings["path"])
        if not path.exists():
            problems.append(f"protocol v{version} is registered in code but not committed")
            continue

        verification = verify_protocol(path)
        if not verification.ok:
            problems.append(f"protocol v{version} does not verify: {verification}")
            if strict:
                continue

        protocol = read_protocol(path)
        results = results_path(version)
        report: LadderReport | None = None
        runs: tuple[ArmRun, ...] = ()
        if results.exists():
            try:
                report, run_list = read_results(results)
                runs = tuple(run_list)
            except (ValueError, KeyError) as exc:
                problems.append(f"{results.name} does not re-score: {exc}")
                if strict:
                    raise
        chapters.append(Chapter(version=version, protocol=protocol, report=report, runs=runs))

    if strict and problems:
        raise ValueError(
            "refusing to assemble a paper from inputs that do not check out: " + "; ".join(problems)
        )

    claim = chapters[0].protocol.claim if chapters else ""
    return Paper(
        claim=claim,
        chapters=tuple(chapters),
        banks=(
            bank_profile("v1", BANK_V1, DEFAULT_LOCK_PATH),
            bank_profile("v2", BANK_V2, V2_LOCK_PATH),
        ),
        provider=_provider(),
        problems=tuple(problems),
    )


def _provider() -> str:
    """Which provider produced the results, read from the newest results file.

    Named on the paper's face. Every number in this document was produced under
    a mock provider unless this says otherwise, and a reader should not have to
    dig for that.
    """
    for version in sorted(PROTOCOL_VERSIONS, key=int, reverse=True):
        path = results_path(version)
        if not path.exists():
            continue
        body = json.loads(path.read_text(encoding="utf-8"))
        return str(body.get("provider", "unknown"))
    return "unknown"
