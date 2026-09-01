"""Scoring the ladder against the plan that was hashed before it ran.

Every number in this file was specified in :mod:`~nullius.benchmark.protocol`
and committed in an earlier change. The bootstrap resample count, the alpha,
the multiplicity correction, the baseline arm, and the map from a computed
confidence level to a probability are all read from the registered protocol
rather than chosen here. That is the only reason the results mean anything:
none of these knobs could be turned after the numbers were visible, because
turning one would change a hash that is in the git history.

**The pairing.** Arms are compared item by item. Every arm answers all twenty
bank questions, so the difference between two arms on item *n* is a paired
observation, and the bootstrap resamples *items* rather than answers. The
population being generalised to is "questions like these", which is the only
population twenty synthetic items can speak for and is stated as such.

**What a confidence level means numerically.** The institution computes an
ordinal — ``contested`` through ``well_supported`` — and Brier and calibration
need a number. The protocol fixes that translation, deliberately unflatteringly:
the top level is 0.90, so a confident error is punished hard.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

from nullius.analysis.multiple import Correction, correct
from nullius.benchmark.arms import arm_named
from nullius.benchmark.protocol import PROTOCOL_VERSIONS, Protocol, read_protocol
from nullius.benchmark.runner import AS_IF_MODEL, ArmOutcome, ArmRun
from nullius.db.enums import ClaimConfidence, Verdict
from nullius.util.canonical import canonical_json

__all__ = [
    "DEFAULT_RESULTS_PATH",
    "ECE_BINS",
    "PREDICTION_CONTRASTS",
    "ArmMetrics",
    "Comparison",
    "LadderReport",
    "compare_to_baseline",
    "read_results",
    "score_arm",
    "score_ladder",
    "write_results",
]


def _round(value: float, places: int = 6) -> float | None:
    """Round for serialisation, and render an undefined metric as null.

    ``canonical_json`` refuses NaN, and is right to: "a metric that is NaN or
    infinite is a defect, not a result". But some metrics are genuinely
    *undefined* rather than defective — B0 asserts no effects, so under v2's
    registered calibration scope it has no Brier score, and an arm that is
    never correct has no cost per correct claim. JSON ``null`` says "there is
    no such number here". NaN says "here is a number, and it is not a number",
    which is how an undefined metric ends up being averaged into a table.
    """
    if math.isnan(value) or math.isinf(value):
        return None
    return round(value, places)


ECE_BINS = 5
"""Bins for expected calibration error.

Five because the confidence rubric has five levels, so each bin holds one
level and the binning adds no choice of its own. A finer grid over twenty
items would report the bin edges rather than the calibration.
"""


def _brier(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float:
    """Mean squared error of the stated probability against what happened."""
    if not probabilities:
        return float("nan")
    paired = zip(probabilities, outcomes, strict=True)
    return float(np.mean([(p - (1.0 if hit else 0.0)) ** 2 for p, hit in paired]))


def _ece(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float:
    """Gap between stated confidence and realised accuracy, weighted by bin size.

    An arm that says 0.90 and is right 90% of the time scores zero however
    often it is wrong, which is the point: this measures honesty about
    uncertainty, not accuracy. Accuracy is already the primary metric.
    """
    if not probabilities:
        return float("nan")
    values = np.asarray(probabilities, dtype=np.float64)
    hits = np.asarray([1.0 if o else 0.0 for o in outcomes], dtype=np.float64)
    edges = np.linspace(0.0, 1.0, ECE_BINS + 1)
    total = 0.0
    for low, high in pairwise(edges):
        # Half-open bins, with the top bin closed so p == 1.0 is not dropped.
        inside = (values >= low) & ((values < high) | (high >= 1.0) & (values <= high))
        if not inside.any():
            continue
        weight = float(inside.sum()) / len(values)
        total += weight * abs(float(values[inside].mean()) - float(hits[inside].mean()))
    return total


@dataclass(frozen=True, slots=True)
class ArmMetrics:
    """One arm, scored on every metric the protocol named.

    All seven are reported for every arm, always. The protocol's third
    exclusion rule says there is no condition under which a result is
    withheld, and the simplest way to keep that promise is to have no code
    path that can drop one.
    """

    arm_id: str
    label: str
    model_dependent: bool
    n_items: int
    n_correct: int
    n_halted: int
    verdict_accuracy: float
    coverage: float
    """Fraction of items the arm was willing to answer at all."""

    assertion_accuracy: float
    """Accuracy over the items it did answer.

    Reported beside ``verdict_accuracy`` rather than instead of it. An arm can
    raise this to 1.0 by answering only what it is sure of, and ``coverage``
    is what stops that reading as a good result. Neither number means much
    alone, which is the point of printing both.
    """

    n_abstained: int
    null_accuracy: float
    brier: float
    expected_calibration_error: float
    false_discovery_rate: float
    usd_total: Decimal
    usd_per_correct_claim: float
    effect_size_error: float

    n_replicates: int = 1
    """Passes over the bank these numbers average.

    Only custodied arms are replicated; the rest return identical results
    however often they run, which the v3-against-v4 comparison measured rather
    than assumed.
    """

    n_scored: int = 0
    """Items the calibration metrics were computed over.

    Under v2's registered ``asserted_effects`` scope this is smaller than
    ``n_items``, and for B0 it is zero: an arm that answers ``no_effect`` about
    everything never asserts an effect, so it has no calibration to score. That
    is a real property of the arm, not a gap in the measurement.
    """

    def as_dict(self) -> dict[str, Any]:
        return {
            "arm_id": self.arm_id,
            "label": self.label,
            "model_dependent": self.model_dependent,
            "n_items": self.n_items,
            "n_replicates": self.n_replicates,
            "n_scored": self.n_scored,
            "n_correct": self.n_correct,
            "n_halted": self.n_halted,
            "verdict_accuracy": _round(self.verdict_accuracy),
            "coverage": _round(self.coverage),
            "assertion_accuracy": _round(self.assertion_accuracy),
            "n_abstained": self.n_abstained,
            "null_accuracy": _round(self.null_accuracy),
            "brier": _round(self.brier),
            "expected_calibration_error": _round(self.expected_calibration_error),
            "false_discovery_rate": _round(self.false_discovery_rate),
            "usd_total": str(self.usd_total),
            "usd_per_correct_claim": _round(self.usd_per_correct_claim, 8),
            "effect_size_error": _round(self.effect_size_error),
        }

    def __str__(self) -> str:
        flag = " [model-dependent]" if self.model_dependent else ""
        return (
            f"{self.arm_id} accuracy {self.verdict_accuracy:.2f} "
            f"(answered {self.coverage:.0%}, right {self.assertion_accuracy:.2f}) "
            f"null {self.null_accuracy:.2f} brier {self.brier:.3f} "
            f"fdr {self.false_discovery_rate:.2f}{flag}"
        )


def _accuracy(outcomes: Sequence[ArmOutcome]) -> float:
    return sum(o.correct for o in outcomes) / len(outcomes) if outcomes else float("nan")


def score_arm(run: ArmRun, protocol: Protocol) -> ArmMetrics:
    """Score one arm on the seven registered metrics."""
    outcomes = run.outcomes
    nulls = [o for o in outcomes if o.is_null_item]
    discoveries = [o for o in outcomes if o.claimed_an_effect]

    # Which items the calibration metrics are computed over. v1 said "all of
    # them", and running it showed why that was wrong: the confidence rubric
    # measures evidence *for an effect*, so a correct `no_effect` answer
    # necessarily carries weak evidence and scored as gross underconfidence.
    # v2 registers `asserted_effects` - the subpopulation where the rubric's
    # quantity and the scored outcome are the same quantity. Read from the
    # protocol rather than chosen here, and v1 keeps the behaviour its results
    # were produced under.
    scope = str(protocol.statistics.get("calibration_scope", "all_items"))
    scored = discoveries if scope == "asserted_effects" else list(outcomes)

    probabilities = [protocol.confidence_as_probability[o.confidence.value] for o in scored]
    correct = [o.correct for o in scored]
    all_correct = [o.correct for o in outcomes]

    usd_total = sum((o.usd for o in outcomes), Decimal(0))
    n_correct = sum(all_correct)

    answered = [o for o in outcomes if not o.abstained]
    return ArmMetrics(
        n_replicates=run.n_replicates,
        n_scored=len(scored),
        coverage=(len(answered) / len(outcomes)) if outcomes else float("nan"),
        assertion_accuracy=_accuracy(answered),
        n_abstained=len(outcomes) - len(answered),
        arm_id=run.arm.arm_id,
        label=run.arm.label,
        model_dependent=run.arm.model_dependent,
        n_items=len(run.by_item()),
        n_correct=n_correct,
        n_halted=sum(1 for o in outcomes if o.halted is not None),
        verdict_accuracy=_accuracy(outcomes),
        null_accuracy=_accuracy(nulls),
        brier=_brier(probabilities, correct),
        expected_calibration_error=_ece(probabilities, correct),
        # No discoveries means no false ones. Reported as zero rather than as
        # nan, because "claimed nothing" genuinely has a false discovery rate
        # of zero — and the accuracy column is where that abstinence is paid for.
        false_discovery_rate=(
            sum(o.false_discovery for o in discoveries) / len(discoveries) if discoveries else 0.0
        ),
        usd_total=usd_total,
        # An arm with no correct claims has an undefined cost per correct
        # claim, not an infinite one, and is reported as undefined.
        usd_per_correct_claim=(float(usd_total) / n_correct if n_correct else float("nan")),
        effect_size_error=(
            float(np.mean([abs(o.realised_effect - o.true_effect) for o in outcomes]))
            if outcomes
            else float("nan")
        ),
    )


@dataclass(frozen=True, slots=True)
class Comparison:
    """One arm against the baseline, paired over items."""

    arm_id: str
    baseline_arm_id: str
    metric: str
    difference: float
    ci_low: float
    ci_high: float
    p_value: float
    resamples: int
    model_dependent: bool
    """True if either side of this comparison is dominated by the model."""

    @property
    def separates(self) -> bool:
        """Whether the interval excludes no difference at all."""
        return self.ci_low > 0.0 or self.ci_high < 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "arm_id": self.arm_id,
            "baseline_arm_id": self.baseline_arm_id,
            "metric": self.metric,
            "difference": round(self.difference, 6),
            "ci_low": round(self.ci_low, 6),
            "ci_high": round(self.ci_high, 6),
            "p_value": round(self.p_value, 6),
            "resamples": self.resamples,
            "separates": self.separates,
            "model_dependent": self.model_dependent,
        }

    def __str__(self) -> str:
        return (
            f"{self.arm_id} - {self.baseline_arm_id}: {self.difference:+.3f} "
            f"[{self.ci_low:+.3f}, {self.ci_high:+.3f}] p={self.p_value:.4f}"
        )


def _paired_bootstrap(
    treatment: Sequence[bool] | Sequence[float],
    baseline: Sequence[bool] | Sequence[float],
    *,
    resamples: int,
    alpha: float,
    seed: int,
) -> tuple[float, float, float, float]:
    """Difference in accuracy, its percentile interval, and a two-sided p.

    Percentile rather than BCa. ``paired_analysis`` uses BCa above ten samples
    and the bank has twenty items, so BCa would be available — but the
    acceleration term is estimated by jackknife over a 0/1 vector, where
    leaving one item out moves the statistic in steps of 1/20 and the estimate
    is dominated by that granularity rather than by the skew it is meant to
    correct. The protocol therefore registered ``percentile bootstrap over
    items``, and this is that.
    """
    # Floats rather than booleans, because with replicates an item carries the
    # *rate* at which an arm got it right rather than a single yes or no.
    a = np.asarray([float(x) for x in treatment], dtype=np.float64)
    b = np.asarray([float(x) for x in baseline], dtype=np.float64)
    if a.size != b.size:
        raise ValueError("a paired comparison needs both arms to answer the same items")
    if a.size == 0:
        return float("nan"), float("nan"), float("nan"), float("nan")

    observed = float(a.mean() - b.mean())
    rng = np.random.default_rng(seed)
    # One index draw per resample, applied to *both* arms: that is what makes
    # the comparison paired. Resampling each arm independently would compare
    # two different bootstrap worlds.
    picks = rng.integers(0, a.size, size=(resamples, a.size))
    differences = a[picks].mean(axis=1) - b[picks].mean(axis=1)

    low = float(np.percentile(differences, 100.0 * alpha / 2.0))
    high = float(np.percentile(differences, 100.0 * (1.0 - alpha / 2.0)))

    # Two-sided bootstrap p: how much of the resampled distribution sits on the
    # far side of no difference, doubled. Floored at one resample rather than
    # reported as exactly zero, since 2000 resamples cannot evidence p < 1/2000.
    below = float(np.mean(differences <= 0.0))
    above = float(np.mean(differences >= 0.0))
    p_value = min(1.0, 2.0 * max(min(below, above), 1.0 / resamples))
    return observed, low, high, p_value


def compare_to_baseline(
    runs: Sequence[ArmRun],
    protocol: Protocol,
    *,
    seed: int = 0,
) -> tuple[list[Comparison], Correction]:
    """Every arm against the registered baseline, corrected for multiplicity.

    The baseline and the correction method are read from the protocol, not
    chosen here. Seven comparisons against one baseline on one metric is a
    family, and reporting seven uncorrected intervals would mean expecting one
    spurious result and presenting it as a finding.
    """
    baseline_id = str(protocol.statistics["baseline_arm"])
    resamples = int(protocol.statistics["resamples"])
    alpha = float(protocol.statistics["alpha"])
    method = str(protocol.statistics["multiplicity"]).replace("-", "_")

    by_id = {run.arm.arm_id: run for run in runs}
    if baseline_id not in by_id:
        raise ValueError(
            f"the protocol names {baseline_id} as the baseline, and this run "
            f"does not include it; a comparison against a missing arm is not a "
            f"comparison"
        )
    base_run = by_id[baseline_id]
    ordered = base_run.by_item()

    comparisons: list[Comparison] = []
    for index, run in enumerate(runs):
        if run.arm.arm_id == baseline_id:
            continue
        theirs = run.by_item()
        shared = [item_id for item_id in ordered if item_id in theirs]
        difference, low, high, p_value = _paired_bootstrap(
            [_rate(theirs[i], QUANTITIES["verdict_accuracy"]) for i in shared],
            [_rate(ordered[i], QUANTITIES["verdict_accuracy"]) for i in shared],
            resamples=resamples,
            alpha=alpha,
            # Per-arm seeds, derived from position so the whole report is
            # reproducible from one integer.
            seed=seed + index,
        )
        comparisons.append(
            Comparison(
                arm_id=run.arm.arm_id,
                baseline_arm_id=baseline_id,
                metric=protocol.primary_metric,
                difference=difference,
                ci_low=low,
                ci_high=high,
                p_value=p_value,
                resamples=resamples,
                model_dependent=run.arm.model_dependent or base_run.arm.model_dependent,
            )
        )

    correction = correct([c.p_value for c in comparisons], method=method, alpha=alpha)
    return comparisons, correction


PREDICTION_CONTRASTS = (("B4", "B3"), ("B6", "B4"), ("B6", "B7"))
"""The contrasts the registered prediction is actually about.

Added after the first full ladder, and worth being precise about why, because
"we added a statistic once we saw the numbers" is normally the confession at
the centre of a bad result.

These are not new hypotheses. The protocol registered a prediction whose two
terms are exactly ``B4 - B3`` and ``B6 - B4``, and registered an adjudication
that compares them. What the protocol did *not* register was any requirement
that either term be distinguishable from zero — so the rule returns "upheld"
for a one-item difference in each direction, on a twenty-item bank whose
metric cannot resolve below one item. The first run duly returned "upheld" on
exactly that margin.

Reporting the point estimates without their intervals would let a coin flip
read as a confirmed prediction. So the intervals are computed and reported.
The verdict itself is left exactly as the registered rule produces it, wrong
or right; the interval sits beside it. ``B6 - B7`` is included because it is
the memory ablation, which the ladder exists to measure and which the
baseline family never compares.
"""


@dataclass(frozen=True, slots=True)
class LadderReport:
    """The whole benchmark: every arm scored, compared, and adjudicated."""

    protocol_hash: str
    primary_metric: str
    prediction: str
    metrics: tuple[ArmMetrics, ...]
    comparisons: tuple[Comparison, ...]
    correction: Correction
    prediction_upheld: bool | None
    prediction_reason: str
    prediction_contrasts: tuple[Comparison, ...] = ()
    """Intervals for the contrasts the prediction is made of. See
    :data:`PREDICTION_CONTRASTS`."""

    def metrics_for(self, arm_id: str) -> ArmMetrics:
        for row in self.metrics:
            if row.arm_id == arm_id:
                return row
        raise KeyError(f"no arm {arm_id!r} in this report")

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol_hash": self.protocol_hash,
            "primary_metric": self.primary_metric,
            "prediction": self.prediction,
            "prediction_upheld": self.prediction_upheld,
            "prediction_reason": self.prediction_reason,
            "metrics": [m.as_dict() for m in self.metrics],
            "comparisons": [c.as_dict() for c in self.comparisons],
            "prediction_contrasts": [c.as_dict() for c in self.prediction_contrasts],
            "correction": self.correction.as_dict(),
        }


def _adjudicate(
    metrics: Sequence[ArmMetrics],
    contrasts: Sequence[Comparison] = (),
    rule: str = "point_estimates",
    named: dict[str, str] | None = None,
) -> tuple[bool | None, str]:
    """Was the registered prediction right?

    The prediction, fixed before any of this ran: *B4 captures most of the gain
    over B3* — adding preregistration and the Custodian to a role-decomposed
    pipeline buys more accuracy than the Skeptic, replication, review and
    memory buy on top of it.

    Made arithmetic so that it cannot be argued about afterwards. Let
    ``mechanism = B4 - B3`` and ``agents = B6 - B4``. The prediction holds when
    ``mechanism > agents``. Both are reported whichever way it falls, and a
    negative ``mechanism`` — custody making the institution look *worse* on
    accuracy, which is a live possibility since B3 is scored on the split it
    tuned on — refutes the prediction rather than being explained.
    """
    if rule == "named_contrast":
        # The protocol names the arms, the quantity and the direction, and the
        # verdict is computed from those. v3 registered a prediction about
        # coverage and inherited a rule that tested accuracy, so it reported
        # "refuted" after measuring something the prediction never mentioned --
        # right by accident. A rule stored apart from its prediction can drift
        # from it silently; a rule derived from it cannot.
        if named is None:
            return None, "not adjudicable: this protocol names no adjudicated contrast"
        found = next(
            (
                c
                for c in contrasts
                if (c.arm_id, c.baseline_arm_id, c.metric)
                == (named["treatment"], named["baseline"], named["quantity"])
            ),
            None,
        )
        if found is None:
            return None, (
                f"not adjudicable: this run produced no "
                f"{named['treatment']}-{named['baseline']} contrast on {named['quantity']}"
            )
        upheld = found.ci_low > 0.0 if named["direction"] == "greater" else found.ci_high < 0.0
        return upheld, (
            f"{named['quantity']} ({named['treatment']}-{named['baseline']}) = "
            f"{found.difference:+.4f}, 95% CI [{found.ci_low:+.4f}, {found.ci_high:+.4f}]; "
            f"interval {'excludes' if upheld else 'does not exclude'} zero; "
            f"prediction {'upheld' if upheld else 'refuted'}"
        )

    if rule == "interval_excludes_zero":
        found = next((c for c in contrasts if (c.arm_id, c.baseline_arm_id) == ("B4", "B3")), None)
        if found is None:
            return None, "not adjudicable: this run produced no B4-B3 contrast"
        upheld = found.ci_low > 0.0
        return upheld, (
            f"mechanism (B4-B3) = {found.difference:+.4f}, "
            f"95% CI [{found.ci_low:+.4f}, {found.ci_high:+.4f}]; "
            f"interval {'excludes' if upheld else 'does not exclude'} zero; "
            f"prediction {'upheld' if upheld else 'refuted'}"
        )

    by_id = {m.arm_id: m for m in metrics}
    missing = [arm for arm in ("B3", "B4", "B6") if arm not in by_id]
    if missing:
        return None, f"not adjudicable: this run omitted {', '.join(missing)}"

    mechanism = by_id["B4"].verdict_accuracy - by_id["B3"].verdict_accuracy
    agents = by_id["B6"].verdict_accuracy - by_id["B4"].verdict_accuracy
    upheld = mechanism > agents
    return upheld, (
        f"mechanism (B4-B3) = {mechanism:+.4f}; "
        f"everything else (B6-B4) = {agents:+.4f}; "
        f"prediction {'upheld' if upheld else 'refuted'}"
    )


#: How a named quantity is read off one item's outcome. The adjudicated
#: quantity is registered by name in the protocol, so the mapping from that
#: name to a per-item boolean has to live somewhere both sides agree on.
QUANTITIES: dict[str, Callable[[ArmOutcome], bool]] = {
    "verdict_accuracy": lambda o: o.correct,
    "coverage": lambda o: not o.abstained,
}


def _rate(outcomes: Sequence[ArmOutcome], read: Callable[[ArmOutcome], bool]) -> float:
    """How often an arm got this item right, across its replicates."""
    return sum(1.0 for o in outcomes if read(o)) / len(outcomes)


def _contrast(
    runs: Sequence[ArmRun],
    treatment_id: str,
    baseline_id: str,
    protocol: Protocol,
    *,
    seed: int,
    quantity: str = "verdict_accuracy",
) -> Comparison | None:
    """One paired arm-against-arm interval, or None if either arm is missing."""
    by_id = {run.arm.arm_id: run for run in runs}
    if treatment_id not in by_id or baseline_id not in by_id:
        return None
    treatment, baseline = by_id[treatment_id], by_id[baseline_id]
    theirs = treatment.by_item()
    ours = baseline.by_item()
    shared = [i for i in ours if i in theirs]
    read = QUANTITIES[quantity]
    # Averaged within an item before the arms are compared, so the bootstrap
    # keeps resampling *items* — the population the bank can speak for — while
    # extra replicates reduce custody noise instead of inflating the sample.
    difference, low, high, p_value = _paired_bootstrap(
        [_rate(theirs[i], read) for i in shared],
        [_rate(ours[i], read) for i in shared],
        resamples=int(protocol.statistics["resamples"]),
        alpha=float(protocol.statistics["alpha"]),
        seed=seed,
    )
    return Comparison(
        arm_id=treatment_id,
        baseline_arm_id=baseline_id,
        metric=quantity,
        difference=difference,
        ci_low=low,
        ci_high=high,
        p_value=p_value,
        resamples=int(protocol.statistics["resamples"]),
        model_dependent=treatment.arm.model_dependent or baseline.arm.model_dependent,
    )


def score_ladder(
    runs: Sequence[ArmRun],
    protocol: Protocol,
    *,
    seed: int = 0,
) -> LadderReport:
    """Score every arm and settle the registered prediction.

    Refuses a run that does not cover the arms the protocol registered. The
    first v4 ladder ran eight arms under a nine-arm protocol -- a wiring slip
    meant the arm list never reached the runner -- and produced a complete
    looking results file, seven of seven baseline comparisons, and no sign
    that the arm the protocol exists to test had never executed. Only the
    adjudication noticed, and only because it happened to name that arm.
    A results file is a claim about a protocol, and this is what makes the
    claim checkable.
    """
    registered = {str(arm["arm_id"]) for arm in protocol.arms}
    present = {run.arm.arm_id for run in runs}
    if missing := registered - present:
        raise ValueError(
            f"protocol {protocol.protocol_hash[:16]} registers "
            f"{len(registered)} arms and this run has {len(present)}; "
            f"missing {sorted(missing)}. Scoring it would report a partial "
            "ladder as though it were the registered one."
        )
    if unexpected := present - registered:
        raise ValueError(
            f"this run contains {sorted(unexpected)}, which protocol "
            f"{protocol.protocol_hash[:16]} does not register"
        )

    metrics = tuple(score_arm(run, protocol) for run in runs)
    comparisons, correction = compare_to_baseline(runs, protocol, seed=seed)
    wanted: list[tuple[str, ...]] = [tuple(pair) for pair in PREDICTION_CONTRASTS]
    named = protocol.statistics.get("adjudicated")
    if named:
        # Always compute the contrast the protocol adjudicates on, whatever it
        # is, so the verdict can never be reported without the interval it
        # rests on.
        wanted.append((str(named["treatment"]), str(named["baseline"]), str(named["quantity"])))
    contrasts = tuple(
        found
        for offset, entry in enumerate(wanted)
        if (
            found := _contrast(
                runs,
                entry[0],
                entry[1],
                protocol,
                seed=seed + 100 + offset,
                quantity=entry[2] if len(entry) > 2 else "verdict_accuracy",
            )
        )
        is not None
    )
    adjudicated = protocol.statistics.get("adjudicated")
    upheld, reason = _adjudicate(
        metrics,
        contrasts,
        str(protocol.statistics.get("adjudication", "point_estimates")),
        named={k: str(v) for k, v in adjudicated.items()} if adjudicated else None,
    )
    return LadderReport(
        protocol_hash=protocol.protocol_hash,
        primary_metric=protocol.primary_metric,
        prediction=protocol.prediction,
        metrics=metrics,
        comparisons=tuple(comparisons),
        correction=correction,
        prediction_upheld=upheld,
        prediction_reason=reason,
        prediction_contrasts=contrasts,
    )


DEFAULT_RESULTS_PATH = Path("benchmark/results.lock.json")


def write_results(
    report: LadderReport,
    runs: Sequence[ArmRun],
    path: Path = DEFAULT_RESULTS_PATH,
    *,
    provider: str,
) -> Path:
    """Write the results, stamped with the protocol they were scored against.

    Unlike :func:`~nullius.benchmark.protocol.write_protocol`, this *does*
    overwrite. A protocol may be registered once; results may be re-measured as
    often as anyone likes, and refusing to overwrite them would only encourage
    keeping the run that came out best. The protocol hash travels with them, so
    a results file scored against an edited plan is detectable rather than
    merely discouraged.

    ``provider`` is recorded because it decides how much of this is a
    measurement. Under ``mock`` the model-dependent arms describe the mock, and
    a reader should not have to infer that from the absence of an API key.
    """
    payload = {
        "version": 1,
        "provider": provider,
        "as_if_model": AS_IF_MODEL,
        "report": report.as_dict(),
        "per_item": [run.as_dict() for run in runs],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    return path


def read_results(path: Path = DEFAULT_RESULTS_PATH) -> tuple[LadderReport, list[ArmRun]]:
    """Reconstruct the per-item outcomes, and re-score them from scratch.

    The report is *recomputed* rather than read back, so the stored summary is
    checkable against the stored outcomes rather than taken on trust. It also
    means the scoring can be re-derived, argued with, and corrected without
    re-running thirty-five minutes of science — which is the difference between
    a results file and a screenshot.
    """
    body = json.loads(path.read_text(encoding="utf-8"))
    runs = [
        ArmRun(
            arm=arm_named(entry["arm"]["arm_id"]),
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
                for row in entry["outcomes"]
            ),
        )
        for entry in body["per_item"]
    ]
    # Which registered protocol these results were scored under, found by
    # hash rather than assumed. More than one protocol is registered now, and
    # re-scoring v2's results under v1's plan would be exactly the substitution
    # preregistration exists to prevent — quietly, and with a plausible number
    # at the end of it.
    stored_hash = str(body["report"]["protocol_hash"])
    protocol = None
    for settings in PROTOCOL_VERSIONS.values():
        candidate_path = Path(settings["path"])
        if not candidate_path.exists():
            continue
        candidate = read_protocol(candidate_path)
        if candidate.protocol_hash == stored_hash:
            protocol = candidate
            break
    if protocol is None:
        known = ", ".join(
            f"{v}={read_protocol(Path(c['path'])).protocol_hash[:16]}"
            for v, c in PROTOCOL_VERSIONS.items()
            if Path(c["path"]).exists()
        )
        raise ValueError(
            f"these results were scored against protocol {stored_hash[:16]}, which is "
            f"not among the registered ones ({known}). Re-scoring them under a "
            "different plan would be exactly the substitution preregistration exists "
            "to prevent."
        )
    return score_ladder(runs, protocol), runs
