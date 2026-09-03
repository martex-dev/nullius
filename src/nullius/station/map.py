"""The station's floor plan, derived from the enums rather than drawn beside them.

A room is a claim about which institutional actors work in it and which states
of :class:`~nullius.db.enums.HypothesisState` it owns. The map is therefore not
a picture of the architecture; it is the architecture, laid out.

**Why derived.** This is the fourth thing in the project to be keyed by a
project-level enum, after the CI job that listed protocols by hand, the ladder
that ran eight arms under a nine-arm plan, and the paper's results-path table
that raised on v6. Each went stale the moment the thing it mirrored grew. So
:func:`unrepresented_roles` and :func:`unrepresented_states` are computed and a
test fails on either being non-empty: adding a role or a state to
``db/enums.py`` breaks the build until a room claims it, which is the only
mechanism that has ever stopped this class of drift here.

**Corridor order is computed too.** ``HypothesisState`` is declared in pipeline
order with the exits named by ``TERMINAL_STATES``, so the walk from the drafting
room to the record and the set of doors at the far end both fall out of the
enum. Reordering the enum reorders the station.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from nullius.benchmark.arms import Arm, ArmKind
from nullius.benchmark.runner import mechanisms_for
from nullius.db.enums import TERMINAL_STATES, HypothesisState, Role

__all__ = [
    "HANDLED_OUTSIDE_THE_KERNEL",
    "PIPELINE_STATES",
    "ROOMS",
    "TERMINAL_DOORS",
    "Backing",
    "Room",
    "Wing",
    "corridor",
    "dead_switches",
    "declared_switches",
    "room_named",
    "room_owning",
    "unread_switches",
    "unrepresented_roles",
    "unrepresented_states",
]


PIPELINE_STATES: tuple[HypothesisState, ...] = tuple(
    state for state in HypothesisState if state not in TERMINAL_STATES
)
"""The states a hypothesis passes through, in declaration order.

``SHELVED`` is declared between ``SCREENED`` and ``REGISTERED`` but is an exit,
so it is filtered out here by membership of ``TERMINAL_STATES`` rather than by
position. Partitioning on the frozenset the enum module already maintains is
what keeps this from being a second list of states to forget to update.
"""

TERMINAL_DOORS: tuple[HypothesisState, ...] = tuple(
    state for state in HypothesisState if state in TERMINAL_STATES
)
"""Every way out, in declaration order.

Drawn the same size as each other and as the way in. ``REFUTED`` and
``INCONCLUSIVE`` are terminal successes of the process, and a floor plan that
drew them as narrower doors than ``INSTITUTIONAL`` would be arguing with the
project's own design principle in the one medium where nobody reads the caption.
"""


class Wing(StrEnum):
    """Which part of the station a room belongs to.

    ``SEALED`` is not decoration. The Vault holds the evaluation split in a
    separate process and the Oracle holds ground truth the institution may never
    read; both are drawn as rooms with no corridor into them, because that is
    what they are.
    """

    PIPELINE = "pipeline"
    OFF_PIPELINE = "off_pipeline"
    SEALED = "sealed"


class Backing(StrEnum):
    """What is behind a room, decided by looking rather than by asserting.

    Only ``UNBUILT`` is declared, and only where the milestone that would build
    the feature has not run. The rest is measured at assembly time: a room with
    a source and no rows reports ``EMPTY``, and never activity it does not have.
    """

    LIVE = "live"
    """Rows were found for this room in the record being displayed."""

    EMPTY = "empty"
    """The source exists and holds nothing for this room. Said, not hidden."""

    UNBUILT = "unbuilt"
    """The feature this room would show has not been built. Declared, with why."""


@dataclass(frozen=True, slots=True)
class Room:
    """One department, its actors, and the states it owns.

    ``x``/``y``/``w``/``h`` are in the station's own plan units, a 200 by 124
    floor. They are layout and carry no claim; everything that means anything is
    above them. The pipeline runs as a serpentine down the left; the off-corridor
    and sealed rooms have their own column on the right, reached by no corridor.
    """

    room_id: str
    name: str
    roles: tuple[Role, ...]
    states: tuple[HypothesisState, ...]
    wing: Wing
    accent: str
    charter: str
    """What this room does, in one sentence."""

    invariant: str = ""
    """The rule this room's walls enforce, where it enforces one."""

    unbuilt_because: str = ""
    """Why this room is dark, where the feature behind it does not exist.

    Non-empty here is a promise that the room renders locked and shows no
    figures, however much data happens to be lying around next to it.
    """

    x: float = 0.0
    y: float = 0.0
    w: float = 26.0
    h: float = 20.0

    @property
    def locked(self) -> bool:
        return bool(self.unbuilt_because)

    def as_dict(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "name": self.name,
            "roles": [role.value for role in self.roles],
            "states": [state.value for state in self.states],
            "wing": self.wing.value,
            "accent": self.accent,
            "charter": self.charter,
            "invariant": self.invariant,
            "unbuilt_because": self.unbuilt_because,
            "locked": self.locked,
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
        }


ROOMS: tuple[Room, ...] = (
    Room(
        room_id="drafting",
        name="Design Room",
        roles=(Role.THEORIST,),
        states=(HypothesisState.DRAFT,),
        wing=Wing.PIPELINE,
        accent="violet",
        charter="The Theorist writes a falsifiable hypothesis with a mechanism, a "
        "primary metric, a direction and a minimum effect worth detecting.",
        invariant="A hypothesis with no falsification condition is refused at intake, "
        "and one too close to an existing hypothesis is refused as unnovel.",
        x=4,
        y=15,
        w=26,
        h=24,
    ),
    Room(
        room_id="screening",
        name="Screening Room",
        roles=(Role.DIRECTOR, Role.LITERATURE),
        states=(HypothesisState.SCREENED, HypothesisState.SHELVED),
        wing=Wing.PIPELINE,
        accent="amber",
        charter="The Director decides what gets funded out of what was drafted. "
        "What is not funded is shelved, with the decision recorded.",
        invariant="Shelving is a recorded decision naming what beat it, not a deletion.",
        x=33,
        y=15,
        w=26,
        h=24,
    ),
    Room(
        room_id="registry",
        name="Registry Room",
        roles=(Role.DESIGNER, Role.SYSTEM),
        states=(HypothesisState.REGISTERED,),
        wing=Wing.PIPELINE,
        accent="cyan",
        charter="The Designer emits an experiment spec, the linter checks it, and the "
        "Registry hashes it and locks it. Nothing may run until this has happened.",
        invariant="A run without a prior locked registration is refused by a database "
        "trigger, not by an agent's good behaviour. Registration is irreversible: "
        "changing anything creates a child registration typed exploratory.",
        x=62,
        y=15,
        w=26,
        h=24,
    ),
    Room(
        room_id="workshop",
        name="Development Workshop",
        roles=(Role.BUILDER,),
        states=(HypothesisState.BUILT,),
        wing=Wing.PIPELINE,
        accent="teal",
        charter="The registered spec is compiled into an executable bundle and the "
        "bundle is hashed. In the MVP the compiler is our own unit-tested harness, "
        "so the Workshop is staffed by library code rather than by an agent.",
        invariant="The bundle's content hash is recorded before it is executed.",
        x=91,
        y=15,
        w=26,
        h=24,
    ),
    Room(
        room_id="execution",
        name="Experiment Floor",
        roles=(Role.SYSTEM,),
        states=(HypothesisState.EXECUTED,),
        wing=Wing.PIPELINE,
        accent="green",
        charter="The bundle runs sandboxed, once per registered seed, and emits hashed "
        "artifacts, telemetry and an environment manifest.",
        invariant="Sockets, subprocesses and writes outside the workdir are denied by "
        "an audit hook and logged. Infrastructure failures may be retried; scientific "
        "failures never are, and become research objects.",
        x=120,
        y=15,
        w=26,
        h=24,
    ),
    Room(
        room_id="analysis",
        name="Analysis Room",
        roles=(Role.ANALYST,),
        states=(HypothesisState.ANALYZED,),
        wing=Wing.PIPELINE,
        accent="blue",
        charter="Effects, intervals and a verdict, computed by library code from the "
        "run results. The Analyst writes the claim; it does not write the numbers.",
        invariant="No statistic passes through a language model. Prose slots reject "
        "numerals, so a figure cannot enter a report except by being computed.",
        x=120,
        y=48,
        w=26,
        h=24,
    ),
    Room(
        room_id="challenge",
        name="Challenge Chamber",
        roles=(Role.SKEPTIC,),
        states=(HypothesisState.CHALLENGED,),
        wing=Wing.PIPELINE,
        accent="orange",
        charter="Detectors and the Skeptic raise typed objections, each carrying the "
        "experiment that would tell it apart from the claim it disputes.",
        invariant="An objection without a discriminating test is not an objection. An "
        "open critical objection blocks promotion to an institutional claim.",
        x=91,
        y=48,
        w=26,
        h=24,
    ),
    Room(
        room_id="blind",
        name="Blind Testing Room",
        roles=(Role.REPLICATOR,),
        states=(HypothesisState.REPLICATED,),
        wing=Wing.PIPELINE,
        accent="indigo",
        charter="The Replicator re-registers and re-runs the design without reading the "
        "original run. It is given the spec and nothing else.",
        invariant="Blindness is proven by the query audit log rather than promised: the "
        "Replicator's reads are recorded, and it never read a row from the original run.",
        x=62,
        y=48,
        w=26,
        h=24,
    ),
    Room(
        room_id="review",
        name="Review Room",
        roles=(Role.REVIEWER,),
        states=(HypothesisState.REVIEWED,),
        wing=Wing.PIPELINE,
        accent="slate",
        charter="A Reviewer scores the claim and admits it or does not.",
        invariant="",
        unbuilt_because="The Reviewer has a contract, an input view and a validator, "
        "and nothing dispatches it. Arm.reviewer is hashed into every registered "
        "protocol and set on the institutional arms, and mechanisms_for does not "
        "carry it into the kernel, so flipping it changes nothing the institution "
        "does. The reviews table is empty in every ledger this project has produced. "
        "The switch is reported as dead at the top of this page, where it was found "
        "by building the page rather than by reviewing anything.",
        x=33,
        y=48,
        w=26,
        h=24,
    ),
    Room(
        room_id="record",
        name="Records Room",
        roles=(),
        states=(
            HypothesisState.INSTITUTIONAL,
            HypothesisState.REFUTED,
            HypothesisState.INCONCLUSIVE,
            HypothesisState.REVISED,
        ),
        wing=Wing.PIPELINE,
        accent="white",
        charter="Where a hypothesis leaves the pipeline. Four doors, drawn the same "
        "size, because refuted and inconclusive are terminal successes of the process.",
        invariant="Every terminal transition emits a follow-up opportunity, which is "
        "how a refutation becomes the next generation's question rather than a dead end.",
        x=4,
        y=48,
        w=26,
        h=24,
    ),
    Room(
        room_id="vault",
        name="The Vault",
        roles=(Role.CUSTODIAN,),
        states=(),
        wing=Wing.SEALED,
        accent="magenta",
        charter="The Custodian holds the holdout split in a separate process and "
        "answers a bounded number of preregistered queries against it.",
        invariant="A CHECK constraint refuses a holdout metric computed by anyone but "
        "the Custodian, so an agent-authored number about the test split cannot enter "
        "the database at all. There is no corridor into this room.",
        x=152,
        y=15,
        w=44,
        h=22,
    ),
    Room(
        room_id="treasury",
        name="Resource Room",
        roles=(Role.DIRECTOR,),
        states=(HypothesisState.ABANDONED_BUDGET,),
        wing=Wing.OFF_PIPELINE,
        accent="gold",
        charter="Budgets, spend and what a correct claim costs. A programme that runs "
        "out of money reaches abandoned_budget with its registration and forecasts "
        "intact, and a decision row naming what beat it.",
        invariant="Budget is enforced at dispatch, hierarchically, and a refusal is an "
        "event. The --max-usd guard caps the total across programmes at item boundaries.",
        x=152,
        y=96,
        w=44,
        h=22,
    ),
    Room(
        room_id="archive",
        name="Archive Room",
        roles=(),
        states=(),
        wing=Wing.OFF_PIPELINE,
        accent="olive",
        charter="Genealogy, recall and follow-ups: what the institution already "
        "believes, and what each terminal result left worth asking.",
        invariant="An inferred claim with no parent evidence row is rejected, and a "
        "speculation cannot be promoted to evidence.",
        x=152,
        y=69,
        w=44,
        h=22,
    ),
    Room(
        room_id="oracle",
        name="The Oracle",
        roles=(),
        states=(),
        wing=Wing.SEALED,
        accent="crimson",
        charter="The planted ground truth of the question bank: the true effect of "
        "every intervention, including the many that are exactly zero.",
        invariant="The institution never reads this room. Ground truth lives where no "
        "role-scoped view can join it, and an isolation test proves it. Only the scorer "
        "holds a key — which is why you can see inside and the institution cannot.",
        x=152,
        y=42,
        w=44,
        h=22,
    ),
)
"""Every room in the station.

The coverage of roles and states is not asserted here; :func:`unrepresented_roles`
and :func:`unrepresented_states` compute it and a test fails on either being
non-empty, so a role added to ``db/enums.py`` breaks the build until a room
claims it.
"""


def corridor() -> tuple[Room, ...]:
    """The pipeline rooms in the order a hypothesis walks them.

    Ordered by the position of the first non-terminal state each room owns, so
    the walk is the enum's declaration order and cannot be sorted by hand into
    disagreeing with it.
    """
    order = {state: index for index, state in enumerate(PIPELINE_STATES)}
    walkable = [room for room in ROOMS if any(state in order for state in room.states)]
    return tuple(sorted(walkable, key=lambda r: min(order[s] for s in r.states if s in order)))


def room_named(room_id: str) -> Room:
    for room in ROOMS:
        if room.room_id == room_id:
            return room
    raise KeyError(f"no room {room_id!r}; the station is {[r.room_id for r in ROOMS]}")


def room_owning(state: HypothesisState) -> Room:
    for room in ROOMS:
        if state in room.states:
            return room
    raise KeyError(f"no room owns {state.value!r}")


def unrepresented_roles() -> frozenset[Role]:
    """Roles the institution declares and the station does not house."""
    housed = {role for room in ROOMS for role in room.roles}
    return frozenset(set(Role) - housed)


def unrepresented_states() -> frozenset[HypothesisState]:
    """States the machine declares and no room owns."""
    owned = {state for room in ROOMS for state in room.states}
    return frozenset(set(HypothesisState) - owned)


# ------------------------------------------------------------------ switchboard

_PROBE = Arm(arm_id="probe", label="probe", isolates="probe", kind=ArmKind.INSTITUTIONAL)
"""An arm with every switch at its default, used to ask what each switch does."""

_IDENTITY_FIELDS = frozenset({"arm_id", "label", "isolates", "kind"})

HANDLED_OUTSIDE_THE_KERNEL: dict[str, str] = {
    "iterations": "Read by the direct-agent path, which does not build a Mechanisms "
    "at all: B2 is B1 with the loop, and runner.py reads arm.iterations to decide "
    "how many times round it goes.",
    "model_dependent": "Not a switch. A label carried into the report so that an arm "
    "whose behaviour is dominated by the language model is never quietly averaged in "
    "with the arms that differ in mechanism.",
}
"""Declared switches that legitimately never reach ``mechanisms_for``.

Each entry is a claim that the field is acted on somewhere else, and a test
checks the other half of the claim — that ``mechanisms_for`` really does ignore
it — so this cannot grow into a place to excuse a switch that does nothing.
"""


def declared_switches() -> frozenset[str]:
    """Every field an arm declares that is not its identity.

    Read off ``Arm.as_dict`` rather than listed, because that dict is what the
    protocol hashes: a switch registered in six protocols and nowhere else is
    exactly the thing this function exists to be able to find.
    """
    return frozenset(set(_PROBE.as_dict()) - _IDENTITY_FIELDS)


def _flipped(value: object) -> bool | int:
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    raise TypeError(f"cannot flip a switch of type {type(value).__name__}")


def _with(arm: Arm, field: str, value: bool | int) -> Arm:
    """One field changed, typed. ``dataclasses.replace`` cannot be told that a
    dynamically-named keyword is the field it names, so the cast lives here in
    one line rather than spreading ``Any`` through the probe."""
    return replace(arm, **{field: value})  # type: ignore[arg-type]


def unread_switches() -> frozenset[str]:
    """Switches ``mechanisms_for`` does not carry into the kernel.

    Measured by flipping one field at a time on a probe arm and asking whether
    the resulting :class:`~nullius.kernel.Mechanisms` changes. A switch the
    translation ignores cannot change what the institution does through that
    path, whatever the protocol recorded about it.

    This is the same shape of check as M20's ``differs_only_by_model``, one
    layer lower down. M20 asked which switches act only through the model.
    This asks which act through nothing.
    """
    unread: set[str] = set()
    for field in sorted(declared_switches()):
        probe = _with(_PROBE, field, _flipped(getattr(_PROBE, field)))
        if mechanisms_for(probe) == mechanisms_for(_PROBE):
            unread.add(field)
    return frozenset(unread)


def dead_switches() -> frozenset[str]:
    """Switches that are registered in a protocol and read by nothing.

    An arm is a claim about which mechanisms are present. A field in that claim
    that no code consumes is a mechanism the ladder reports as varying while it
    does not vary — the failure M20 recorded for memory, which at least reached
    the Theorist's view before being discarded.
    """
    return unread_switches() - frozenset(HANDLED_OUTSIDE_THE_KERNEL)
