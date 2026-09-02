"""Assembling the station out of the same record the paper is assembled from.

:func:`nullius.paper.model.assemble` reads every registered protocol and every
committed result and refuses when one fails to verify or re-score. The station
reuses it rather than re-reading the same files, so the two documents cannot
disagree about what was registered or what was measured — they are two
renderings of one record, which is the arrangement ``FINDINGS.md`` and
``paper/index.html`` already have.

**Two modes, and the page says which.** *Aggregate* reads only committed
artifacts and therefore works from a clean clone and in CI. *Ledger* is aggregate
plus one arm's SQLite ledger, which carries the per-agent detail the lock files
do not: individual events, objections and their discriminating tests, holdout
queries, the query audit, tokens per role. A room whose only source is the
ledger says so when there is no ledger, and shows nothing.

**Every figure carries where it came from.** :class:`Figure` has no constructor
that omits ``source``, so a number cannot reach the page without naming the file
or table it was read out of. That is the mechanism behind the claim that nothing
on this page was typed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from nullius.bank.lock import DEFAULT_LOCK_PATH, V2_LOCK_PATH
from nullius.benchmark.arms import Arm, ArmKind
from nullius.benchmark.metrics import ArmMetrics, LadderReport
from nullius.benchmark.protocol import PROTOCOL_VERSIONS
from nullius.benchmark.runner import ArmOutcome, ArmRun
from nullius.db.enums import Verdict
from nullius.paper.model import BankProfile, Chapter, Paper
from nullius.paper.model import assemble as assemble_paper
from nullius.station.ledger import LedgerView, open_ledger
from nullius.station.map import (
    ROOMS,
    TERMINAL_DOORS,
    Backing,
    Room,
    dead_switches,
    unrepresented_roles,
    unrepresented_states,
)

__all__ = [
    "Figure",
    "Occupancy",
    "Station",
    "Token",
    "assemble",
    "engaged_rooms",
    "payload",
    "route_for",
]

Mode = Literal["aggregate", "ledger"]

TRUTH_LOCKS = {"v1": DEFAULT_LOCK_PATH, "v2": V2_LOCK_PATH}
"""Where each bank's locked truths live, so the Oracle can cite its own file."""


@dataclass(frozen=True, slots=True)
class Figure:
    """One number on the page, and the artifact it was read out of.

    ``source`` is not optional. A figure with nowhere to point is a figure
    somebody typed, and this project's whole argument is that it does not have
    any of those.
    """

    label: str
    value: str
    source: str
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "value": self.value, "source": self.source, "note": self.note}


@dataclass(frozen=True, slots=True)
class Occupancy:
    """What is behind one room, for the record being displayed."""

    room: Room
    backing: Backing
    engaged: bool
    """Whether the arm on display actually passes through this room."""

    figures: tuple[Figure, ...] = ()
    notes: tuple[str, ...] = ()
    doors: tuple[tuple[str, int], ...] = ()
    """Terminal states this room owns, and how many hypotheses reached each."""

    detail: dict[str, Any] = field(default_factory=dict)
    """Room-specific tables. Only the Registry fills this in for now."""

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.room.as_dict(),
            "backing": self.backing.value,
            "engaged": self.engaged,
            "figures": [f.as_dict() for f in self.figures],
            "notes": list(self.notes),
            "doors": [list(door) for door in self.doors],
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class Token:
    """One bank item's passage through the station, under the arm on display.

    ``route`` is the rooms the arm's switches actually engage, and ``verdict`` is
    what it answered. Neither is a timeline: the ledger records three state
    transitions and the results file records none, so the station animates a
    route it can name rather than a schedule it would have to invent.
    """

    item_id: str
    route: tuple[str, ...]
    verdict: str
    truth: str
    correct: bool
    abstained: bool
    n_seeds: int
    replicate: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "route": list(self.route),
            "verdict": self.verdict,
            "truth": self.truth,
            "correct": self.correct,
            "abstained": self.abstained,
            "n_seeds": self.n_seeds,
            "replicate": self.replicate,
        }


def route_for(arm: Arm) -> tuple[str, ...]:
    """The rooms this arm's switches engage, in corridor order.

    Derived from the arm rather than drawn, so an arm that switches a mechanism
    off is shown bypassing the room rather than visiting it dimly. The two
    direct arms and the constant arm return an empty route, which is not a gap
    in the drawing: B1 and B2 ask a model and answer, and B0 answers without
    asking. Walking no rooms at all is what "unstructured agent" means, and it
    is the comparison the whole ladder is built on.
    """
    if arm.kind in (ArmKind.CONSTANT, ArmKind.DIRECT):
        return ()
    route = ["drafting", "registry", "workshop", "execution", "analysis"]
    if arm.adversary:
        route.append("challenge")
    if arm.replication:
        route.append("blind")
    if arm.reviewer and "reviewer" not in dead_switches():
        route.append("review")
    route.append("record")
    return tuple(route)


def engaged_rooms(arm: Arm) -> frozenset[str]:
    """Every room this arm touches, corridor and off-corridor alike.

    The Vault and the Archive are not on the walk — the Vault has no corridor
    into it by design, and the Archive is consulted rather than passed through —
    so they are added here from the switches that reach them. The Treasury is
    engaged by every arm that spends anything, which is every arm but the one
    that answers without looking.
    """
    rooms = set(route_for(arm))
    if arm.custodian:
        rooms.add("vault")
    if arm.memory:
        rooms.add("archive")
    if arm.kind is not ArmKind.CONSTANT:
        rooms.add("treasury")
    return frozenset(rooms)


def _fmt(value: float, places: int = 3) -> str:
    if value != value or value in (float("inf"), float("-inf")):
        return "—"
    return f"{value:.{places}f}"


@dataclass(frozen=True, slots=True)
class Station:
    """Everything the page draws, assembled from checkable sources."""

    paper: Paper
    mode: Mode
    chapter: Chapter
    """The protocol on display."""

    arm: Arm
    """The arm whose passage through the station the map shows."""

    metrics: ArmMetrics | None
    occupancy: tuple[Occupancy, ...]
    tokens: tuple[Token, ...]
    bank: BankProfile
    ledger: LedgerView | None = None
    dead: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def provider(self) -> str:
        return self.paper.provider

    @property
    def live_provider(self) -> bool:
        return self.provider not in ("mock", "unknown", "")

    @property
    def arm_route(self) -> tuple[str, ...]:
        """The rooms the arm on display walks, in corridor order."""
        return route_for(self.arm)

    @property
    def arms(self) -> tuple[Arm, ...]:
        return tuple(run.arm for run in self.chapter.runs)

    def occupancy_of(self, room_id: str) -> Occupancy:
        for occupied in self.occupancy:
            if occupied.room.room_id == room_id:
                return occupied
        raise KeyError(f"no room {room_id!r} on this station")

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "provider": self.provider,
            "live_provider": self.live_provider,
            "protocol": self.chapter.version,
            "protocol_hash": self.chapter.protocol.protocol_hash,
            "arm": self.arm.as_dict(),
            "rooms": [o.as_dict() for o in self.occupancy],
            "tokens": [t.as_dict() for t in self.tokens],
            "dead_switches": list(self.dead),
            "ledger": self.ledger.as_dict() if self.ledger else None,
        }


# ------------------------------------------------------------------- assembling


def _focus(paper: Paper, protocol: str | None) -> Chapter:
    if protocol is not None:
        for chapter in paper.chapters:
            if chapter.version == str(protocol):
                return chapter
        known = [c.version for c in paper.chapters]
        raise ValueError(f"no protocol v{protocol}; registered protocols are {known}")
    latest = paper.latest
    if latest is None:
        raise ValueError("no protocol has produced results, so there is nothing to display")
    return latest


def _focus_arm(chapter: Chapter, arm_id: str | None) -> ArmRun:
    runs = chapter.runs
    if not runs:
        raise ValueError(f"protocol v{chapter.version} has no arm runs")
    if arm_id is not None:
        for run in runs:
            if run.arm.arm_id == arm_id:
                return run
        raise ValueError(f"no arm {arm_id!r}; this protocol ran {[r.arm.arm_id for r in runs]}")
    # The last arm of the ladder is the most institutional one, which is the
    # arm that engages the most rooms and therefore shows the most station.
    return runs[-1]


def _verdict_counts(outcomes: tuple[ArmOutcome, ...]) -> dict[str, int]:
    counts: dict[str, int] = {verdict.value: 0 for verdict in Verdict}
    for outcome in outcomes:
        counts[outcome.verdict.value] += 1
    return counts


def _door_counts(ledger: LedgerView | None) -> dict[str, int]:
    """How many hypotheses were recorded reaching each terminal state.

    Read from the state-change events rather than from the ``hypotheses`` row,
    because the row holds only where a hypothesis stopped. In every ledger this
    project has produced these are all zero, which is a fact about the code and
    not about the drawing — see the Review room and ``BUILD_PLAN.md`` M22.
    """
    written = dict(ledger.transitions) if ledger else {}
    return {door.value: int(written.get(door.value, 0)) for door in TERMINAL_DOORS}


def _analysis_detail(chapter: Chapter, arm: Arm) -> dict[str, Any]:
    """The intervals, with the same annotation the paper puts on them.

    :func:`~nullius.paper.render.contrast_note` is shared rather than
    reimplemented, so the station and the paper cannot describe one interval in
    two different ways. What it says about a contrast whose arms differ only in a
    switch acting through the model is that the interval is not a measurement —
    under a provider that ignores its input the mechanism is delivered and then
    discarded, and what is left is the difference between two custody draws.
    """
    from nullius.paper.render import contrast_note

    report = chapter.report
    if report is None:
        return {}

    def row(comparison: Any) -> dict[str, Any]:
        return {
            "arm_id": comparison.arm_id,
            "baseline_arm_id": comparison.baseline_arm_id,
            "metric": comparison.metric,
            "difference": f"{comparison.difference:+.3f}",
            "ci_low": f"{comparison.ci_low:+.3f}",
            "ci_high": f"{comparison.ci_high:+.3f}",
            "p_value": f"{comparison.p_value:.4f}",
            # The shared note is written for Markdown, where emphasis is asterisks
            # and the leading dash joins it onto a bullet. Both are markup for the
            # other rendering, not content, so they come off here rather than the
            # station keeping a second wording of its own.
            "note": contrast_note(comparison).lstrip(" —").replace("*", "").strip(),
            "interpretable": comparison.interpretable,
            "model_dependent": comparison.model_dependent,
            "involves_arm": arm.arm_id in (comparison.arm_id, comparison.baseline_arm_id),
        }

    return {
        "prediction_contrasts": [row(c) for c in report.prediction_contrasts],
        "comparisons": [row(c) for c in report.comparisons],
        "baseline_arm_id": report.comparisons[0].baseline_arm_id if report.comparisons else "",
        "primary_metric": report.primary_metric,
        "correction": report.correction.method,
        "prediction_reason": report.prediction_reason,
    }


def _registry_detail(chapter: Chapter, paper: Paper, ledger: LedgerView | None) -> dict[str, Any]:
    """The Registry's dashboard: the same discipline, at two scales.

    The protocol is registered before the ladder runs and its hash is in the git
    history; each hypothesis's spec is registered before its run and a trigger
    refuses the run otherwise. One is a claim about this repository, the other a
    claim about one ledger, and both are checkable rather than asserted.
    """
    protocols = [
        {
            "version": c.version,
            "hash": c.protocol.protocol_hash,
            "registered_at": c.protocol.registered_at,
            "arms": len(c.protocol.arms),
            # ``n_items`` rather than ``items``: Jinja resolves an attribute before a
            # key, so a dict key called ``items`` renders as ``dict.items``.
            "n_items": int(c.protocol.bank.get("n_items", 0)),
            "bank_hash": str(c.protocol.bank.get("items_hash", ""))[:16],
            "truth_hash": str(c.protocol.bank.get("truth_lock_hash", ""))[:16],
            "prediction": c.protocol.prediction,
            "verdict": c.verdict,
            "primary_metric": c.protocol.primary_metric,
            "focus": c.version == chapter.version,
        }
        for c in paper.chapters
    ]
    detail: dict[str, Any] = {
        "protocols": protocols,
        "statistics": {str(k): str(v) for k, v in chapter.protocol.statistics.items()},
        "exclusion_rules": list(chapter.protocol.exclusion_rules),
        "confidence_as_probability": {
            str(k): float(v) for k, v in chapter.protocol.confidence_as_probability.items()
        },
    }
    if ledger is not None:
        detail["registrations"] = [
            {
                "registration_id": str(row["registration_id"])[:12],
                "kind": str(row["kind"]),
                "spec_hash": str(row["spec_hash"])[:16],
                "seed_root": int(row["seed_root"]),
                "n_seeds": int(row["n_seeds"]),
                "holdout_query_budget": int(row["holdout_query_budget"]),
                "registered_at": str(row["registered_at"]),
                "locked": bool(row["locked"]),
            }
            for row in ledger.registrations
        ]
        detail["total_registrations"] = ledger.count("registrations")
        detail["runs_after_their_registration"] = ledger.registrations_before_their_runs
        detail["runs_checked"] = ledger.registrations_with_a_run
        detail["first_registration"] = ledger.first_registration
        detail["first_run"] = ledger.first_run
    return detail


def _figures_for(  # one branch per room, which is the clearest shape it has
    room: Room,
    *,
    chapter: Chapter,
    run: ArmRun,
    metrics: ArmMetrics | None,
    bank: BankProfile,
    ledger: LedgerView | None,
) -> tuple[tuple[Figure, ...], tuple[str, ...]]:
    """The figures one room can show, and the notes that qualify them.

    Every branch reads from the committed results, the committed protocol, the
    locked truths or the ledger, and names which in each figure's source.
    """
    results = f"benchmark/results.v{chapter.version}.lock.json"
    protocol = f"benchmark/protocol.v{chapter.version}.lock.json"
    arm = run.arm
    outcomes = run.outcomes
    figures: list[Figure] = []
    notes: list[str] = []

    if room.room_id == "drafting":
        figures.append(
            Figure(
                "bank items carried",
                str(len({o.item_id for o in outcomes})),
                results,
                "one research question, one hypothesis, one programme each",
            )
        )
        if ledger is not None:
            figures.append(Figure("hypotheses written", str(ledger.count("hypotheses")), "ledger"))
            figures.append(
                Figure(
                    "hypothesis.created events",
                    str(dict(ledger.events_by_type).get("hypothesis.created", 0)),
                    "ledger:events",
                )
            )

    elif room.room_id == "screening":
        notes.append(
            "The benchmark funds every bank item, so nothing is screened out here. "
            "Screening decisions and shelvings appear when a funding round allocates a "
            "laboratory budget across competing programmes."
        )
        if ledger is not None:
            figures.append(Figure("decisions recorded", str(ledger.count("decisions")), "ledger"))
            figures.append(Figure("programmes opened", str(ledger.count("programs")), "ledger"))

    elif room.room_id == "registry":
        figures.append(Figure("registered protocols", str(len(PROTOCOL_VERSIONS)), "benchmark/"))
        figures.append(Figure("protocol hash", chapter.protocol.protocol_hash[:16], protocol))
        figures.append(
            Figure(
                "design locked before running",
                "yes" if arm.preregistered else "no",
                protocol,
                ""
                if arm.preregistered
                else "the registration is still written; "
                "this arm is not permitted to claim credit for it",
            )
        )
        if ledger is not None:
            figures.append(Figure("registrations", str(ledger.count("registrations")), "ledger"))
            figures.append(
                Figure(
                    "runs that began after their registration",
                    f"{ledger.registrations_before_their_runs} of "
                    f"{ledger.registrations_with_a_run}",
                    "ledger:runs join registrations",
                    "checked from the stored timestamps, not from the constraint's existence",
                )
            )

    elif room.room_id == "workshop":
        notes.append(
            "Builder-as-compiler: the registered spec is compiled by the project's own "
            "unit-tested harness, so the bundles here were built by library code and the "
            "ledger records the system as their actor. Code generation is gated on a "
            "container runtime and has not run."
        )
        if ledger is not None:
            figures.append(Figure("code bundles", str(ledger.count("code_bundles")), "ledger"))

    elif room.room_id == "execution":
        seeds = [o.n_seeds for o in outcomes]
        figures.append(Figure("seed-runs bought", str(sum(seeds)), results))
        figures.append(
            Figure("seeds per item", _fmt(sum(seeds) / len(seeds) if seeds else 0.0, 2), results)
        )
        figures.append(Figure("most seeds on one item", str(max(seeds, default=0)), results))
        figures.append(
            Figure(
                "escalation",
                "adaptive" if arm.adaptive_seeds else "fixed",
                protocol,
                "sized from an upper bound on the noise"
                if arm.conservative_escalation
                else "sized from the paired standard deviation"
                if arm.adaptive_seeds
                else "",
            )
        )
        if ledger is not None:
            figures.append(Figure("runs executed", str(ledger.count("runs")), "ledger"))
            figures.append(Figure("run results", str(ledger.count("run_results")), "ledger"))

    elif room.room_id == "analysis":
        if metrics is not None:
            figures.append(Figure("verdict accuracy", _fmt(metrics.verdict_accuracy, 3), results))
            figures.append(Figure("coverage", _fmt(metrics.coverage, 3), results))
            figures.append(
                Figure("accuracy where it answered", _fmt(metrics.assertion_accuracy, 3), results)
            )
            figures.append(Figure("null accuracy", _fmt(metrics.null_accuracy, 3), results))
            figures.append(Figure("brier", _fmt(metrics.brier, 3), results))
            figures.append(
                Figure("calibration error", _fmt(metrics.expected_calibration_error, 3), results)
            )
            figures.append(
                Figure("false discovery rate", _fmt(metrics.false_discovery_rate, 3), results)
            )
            figures.append(Figure("effect size error", _fmt(metrics.effect_size_error, 4), results))
        notes.append(
            "Every one of these is computed by library code from the run results. No "
            "statistic in this project passes through a language model."
        )

    elif room.room_id == "challenge":
        raised = sum(o.findings for o in outcomes)
        figures.append(
            Figure(
                "objections raised",
                str(raised),
                results,
                "" if arm.adversary else "this arm runs no detectors",
            )
        )
        if ledger is not None:
            figures.append(
                Figure("objections in the ledger", str(ledger.count("objections")), "ledger")
            )
            figures.append(
                Figure("resolutions", str(ledger.count("objection_resolutions")), "ledger")
            )

    elif room.room_id == "blind":
        figures.append(
            Figure(
                "independent reproductions",
                str(sum(o.replications for o in outcomes)),
                results,
                "" if arm.replication else "this arm does not replicate",
            )
        )
        figures.append(
            Figure(
                "passes over the bank",
                str(run.n_replicates),
                results,
                "custody draws averaged before the arms are compared",
            )
        )
        if ledger is not None:
            figures.append(
                Figure("replications recorded", str(ledger.count("replications")), "ledger")
            )

    elif room.room_id == "record":
        counts = _verdict_counts(outcomes)
        for verdict in Verdict:
            figures.append(Figure(verdict.value, str(counts[verdict.value]), results))
        notes.append(
            "These are verdicts about the world, scored against planted ground truth. "
            "They are not the terminal states of the research state machine, which are "
            "the doors on this room and are counted separately."
        )

    elif room.room_id == "vault":
        figures.append(
            Figure(
                "evaluated on a split it never saw",
                "yes" if arm.custodian else "no",
                protocol,
                "" if arm.custodian else "this arm reads the development split it tuned on",
            )
        )
        custodied = [a for a in chapter.protocol.arms if a.get("custodian")]
        figures.append(
            Figure(
                "arms with a Custodian",
                f"{len(custodied)} of {len(chapter.protocol.arms)}",
                protocol,
            )
        )
        if ledger is not None:
            figures.append(
                Figure("holdout queries granted", str(ledger.holdout.get("granted", 0)), "ledger")
            )
            figures.append(
                Figure("holdout queries refused", str(ledger.holdout.get("refused", 0)), "ledger")
            )
            for key, value in sorted(ledger.seal.items()):
                figures.append(
                    Figure(
                        f"{key} rows",
                        str(value),
                        "ledger:run_results",
                        "a CHECK constraint refuses this row from any other actor",
                    )
                )
            harness_holdout = sum(
                n
                for split, who, n in ledger.results_by_split
                if split == "holdout" and who != "custodian"
            )
            figures.append(
                Figure(
                    "holdout rows computed by anyone else",
                    str(harness_holdout),
                    "ledger:run_results",
                )
            )

    elif room.room_id == "treasury":
        if metrics is not None:
            figures.append(Figure("total", f"${metrics.usd_total}", results))
            figures.append(
                Figure("per correct claim", f"${metrics.usd_per_correct_claim:.5f}", results)
            )
        figures.append(
            Figure(
                "spent on this item at most",
                f"${max((o.usd for o in outcomes), default=Decimal(0)):.5f}",
                results,
            )
        )
        notes.append(
            "Token counts are real. The dollars are those counts priced as if a named "
            "model had produced them, because the provider that produced them is free "
            "and a cost per correct claim whose numerator is zero ranks nothing."
        )
        if ledger is not None:
            for role, entries, input_tokens, output_tokens, cpu in ledger.cost_by_role:
                figures.append(
                    Figure(
                        role,
                        f"{input_tokens:,} in / {output_tokens:,} out"
                        if input_tokens or output_tokens
                        else f"{cpu:.0f} cpu-seconds",
                        "ledger:cost_entries",
                        f"{entries} entries",
                    )
                )

    elif room.room_id == "archive":
        figures.append(
            Figure(
                "memory carried across items",
                "yes" if arm.memory else "no",
                protocol,
                "delivered and discarded under a provider whose output does not depend on its input"
                if arm.memory
                else "",
            )
        )
        if ledger is not None:
            figures.append(Figure("claims", str(ledger.count("claims")), "ledger"))
            figures.append(Figure("evidence rows", str(ledger.count("evidence")), "ledger"))
            figures.append(
                Figure("follow-ups generated", str(ledger.count("follow_ups")), "ledger")
            )
            figures.append(Figure("sources", str(ledger.count("sources")), "ledger"))

    elif room.room_id == "oracle":
        truth = TRUTH_LOCKS[bank.name].as_posix()
        figures.append(Figure("items", str(bank.n_items), truth))
        figures.append(Figure("true effect exactly zero", str(bank.n_null), truth))
        figures.append(Figure("null fraction", _fmt(bank.null_fraction, 2), truth))
        figures.append(
            Figure("within one experiment SE of a boundary", str(bank.within_one_se), truth)
        )
        figures.append(
            Figure("within two experiment SE of a boundary", str(bank.within_two_se), truth)
        )
        figures.append(Figure("what one item is worth", _fmt(bank.resolution, 4), truth))
        notes.append(
            "You can read this room. The institution cannot: ground truth lives where no "
            "role-scoped view can join it, and an isolation test proves no such view "
            "exposes it. Only the scorer holds a key, which is why these numbers are on "
            "this page at all."
        )

    return tuple(figures), tuple(notes)


def _backing(
    room: Room,
    figures: tuple[Figure, ...],
    engaged: bool,
    ledger: LedgerView | None,
) -> Backing:
    """Whether this room has anything behind it, decided by looking."""
    if room.locked:
        return Backing.UNBUILT
    if not figures:
        return Backing.EMPTY
    del engaged, ledger
    return Backing.LIVE


def assemble(
    *,
    strict: bool = True,
    ledger: Path | None = None,
    protocol: str | None = None,
    arm_id: str | None = None,
) -> Station:
    """Read the record, pick a protocol and an arm, and lay out the station.

    ``strict`` is passed through to the paper's assembler, which refuses when a
    protocol fails to verify or a results file fails to re-score. A station whose
    inputs no longer check out is worse than no station, because it looks like
    evidence and it is prettier than the paper.
    """
    paper = assemble_paper(strict=strict)
    chapter = _focus(paper, protocol)
    run = _focus_arm(chapter, arm_id)
    report: LadderReport | None = chapter.report
    metrics = report.metrics_for(run.arm.arm_id) if report else None
    view = open_ledger(ledger) if ledger is not None else None
    n_items = int(chapter.protocol.bank.get("n_items", 0))
    bank = next((b for b in paper.banks if b.n_items == n_items), paper.banks[-1])

    route = route_for(run.arm)
    engaged = engaged_rooms(run.arm)
    doors = _door_counts(view)
    occupancy: list[Occupancy] = []
    for room in ROOMS:
        if room.locked:
            figures: tuple[Figure, ...] = ()
            notes: tuple[str, ...] = (room.unbuilt_because,)
        else:
            figures, notes = _figures_for(
                room, chapter=chapter, run=run, metrics=metrics, bank=bank, ledger=view
            )
        detail: dict[str, Any] = {}
        if room.room_id == "registry":
            detail = _registry_detail(chapter, paper, view)
        elif room.room_id == "analysis":
            detail = _analysis_detail(chapter, run.arm)
        occupancy.append(
            Occupancy(
                room=room,
                backing=_backing(room, figures, room.room_id in engaged, view),
                engaged=room.room_id in engaged,
                figures=figures,
                notes=notes,
                doors=tuple((s.value, doors[s.value]) for s in room.states if s.value in doors),
                detail=detail,
            )
        )

    tokens = tuple(
        Token(
            item_id=outcome.item_id,
            route=route,
            verdict=outcome.verdict.value,
            truth=outcome.truth_verdict.value,
            correct=outcome.correct,
            abstained=outcome.abstained,
            n_seeds=outcome.n_seeds,
            replicate=outcome.replicate,
        )
        for outcome in run.outcomes
    )

    problems = list(paper.problems)
    for role in sorted(unrepresented_roles()):
        problems.append(f"the role {role.value!r} has no room")
    for state in sorted(unrepresented_states()):
        problems.append(f"the state {state.value!r} has no room")

    return Station(
        paper=paper,
        mode="ledger" if view is not None else "aggregate",
        chapter=chapter,
        arm=run.arm,
        metrics=metrics,
        occupancy=tuple(occupancy),
        tokens=tokens,
        bank=bank,
        ledger=view,
        dead=tuple(sorted(dead_switches())),
        problems=tuple(problems),
    )


def payload(station: Station) -> str:
    """The station as JSON, for a reader to check a figure against.

    Serialised once, inline, so the page stays a single file that works offline.
    ``<``, ``>`` and ``&`` become their unicode escapes: the JSON is embedded in
    a ``<script>`` element, and a closing tag appearing inside a string would end
    that element early. Escaping the three characters is what makes it safe to
    emit unescaped, which it must be in order to parse.
    """
    body = json.dumps(station.as_dict(), separators=(",", ":"), sort_keys=True)
    escapes = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026"}
    for character, escape in escapes.items():
        body = body.replace(character, escape)
    return body
