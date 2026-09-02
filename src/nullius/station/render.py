"""Drawing the station.

Everything numeric comes from :mod:`nullius.station.model`, which reads it from
committed protocols, committed results, locked truths and — where one is given —
a ladder's ledger. The prose here is the only hand-written content on the page,
and it is declared as data for the same reason ``paper/render.py`` declares its
flaws and limitations that way: so it can be read, counted and checked in one
place rather than woven through a template where nobody will find it again.

**What the art may and may not do.** The geometry is generated, not drawn. Rooms
are laid out from :data:`~nullius.station.map.ROOMS`, the corridor is the enum's
declaration order, and the furniture inside each room is placed by a hash of the
room's own id so that two builds of the same record produce the same picture. No
binary asset ships: every wall, lamp, console and figure below is vector shapes,
gradients and filters, which keeps the page a single file and the diff readable.

**What the animation may not do.** Agents do not converse in this architecture,
so nothing here draws them conversing. A token's route is the arm's switches and
its exit is the recorded verdict; the pacing is display and the page says so.
Depicting a meeting would make the picture disagree with the system it is a
picture of, which is the one failure a diagram of an institution cannot survive.

**Why every label is measured.** Two captions from different rooms landed on top
of each other in M22 and rendered as one unreadable string, because each was
positioned by hand against a coordinate that happened to be free at the time.
Every piece of text on the map now goes through :func:`_label`, which measures
it, truncates it to its container and records the box it occupies. A test
asserts no two of those boxes intersect. Fixing the two that collided would have
left the next pair to be found by eye.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from nullius.station.map import ROOMS, TERMINAL_DOORS, Room, corridor, room_named
from nullius.station.model import Station, assemble, payload

__all__ = [
    "ACCENTS",
    "CANNOT_SHOW",
    "FURNITURE",
    "PRINCIPLES",
    "UNUSED_EXITS",
    "WORLD",
    "Label",
    "Principle",
    "environment",
    "plan",
    "render_station",
    "write_station",
]


@dataclass(frozen=True, slots=True)
class Principle:
    """One thing the walls of this station mean."""

    title: str
    body: str


#: Why the map is shaped the way it is. Four claims, each about a mechanism the
#: repository enforces rather than a habit it hopes for.
PRINCIPLES: tuple[Principle, ...] = (
    Principle(
        "Norms are invariants, not instructions.",
        "A model asked not to rewrite its hypothesis after seeing results will rewrite "
        "its hypothesis after seeing results. So preregistration is a content hash "
        "written before dispatch and checked by a foreign key; the test split lives "
        "only inside the Custodian's process; a CHECK constraint makes it impossible "
        "for an agent-authored number about the holdout to enter the database at all. "
        "The walls in this drawing are those constraints, and the two rooms with no "
        "corridor into them are the two you genuinely cannot walk into.",
    ),
    Principle(
        "Agents do not converse.",
        "There is a ledger. Every action is a typed state view, a validated artifact "
        "and an append-only event, which is why nothing on this map is drawn holding a "
        "meeting. The Director dispatching tasks that fan out to a queue is a real "
        "event and is drawn; a conference between roles is not one and is not.",
    ),
    Principle(
        "Refutation is a success.",
        "Every way out of the pipeline is drawn the same width as the way in. Refuted "
        "and inconclusive are terminal successes of the process and are reported with "
        "the same prominence as an institutional claim — nearly half the question bank "
        "has a true effect of exactly zero, so a system that always finds something "
        "scores badly.",
    ),
    Principle(
        "No number passes through a language model.",
        "Every statistic in this station is computed by library code and every figure "
        "on this page names the artifact it was read out of. Nothing here was typed, "
        "and the build refuses to run when a protocol fails to verify or a results file "
        "fails to re-score from its own per-item rows.",
    ),
)

#: What this page is not able to show, said where a reader meets it rather than
#: in a footnote. Each of these is a property of the record, not of the drawing.
CANNOT_SHOW: tuple[str, ...] = (
    "Timing. The ledger records when a hypothesis was registered, executed and "
    "analysed, and nothing else about when. Tokens move at a pace chosen for legibility; "
    "their route and their exit are read from the record, their speed is not.",
    "The rooms after Analysis, as states. The pipeline's back half runs — bundles are "
    "built, objections are raised, replications are recorded, claims are promoted — but "
    "no code path writes the states that would record a hypothesis passing through them, "
    "so every terminal door on this map has been used zero times.",
    "Anything about a language model. Every result on this page was produced under a "
    "mock provider whose response does not depend on its input. The institution's "
    "machinery is real and so are the verdicts; the prose each role emits is canned.",
    "A second implementation. Every arm is the same pipeline with switches thrown, which "
    "is what makes the ablation mean anything and also means an arm cannot be shown "
    "doing something structurally different from its neighbour.",
)

#: Said beside the exits when none of them has ever been used. A row of zeroes
#: reads as a broken renderer unless the drawing says it meant them.
UNUSED_EXITS = "every exit unused — no hypothesis has been recorded reaching a terminal state"

#: Room accent, as a hue the whole page agrees on: map, panel and chart alike.
ACCENTS: dict[str, str] = {
    "violet": "#a78bfa",
    "amber": "#fbbf24",
    "cyan": "#22d3ee",
    "teal": "#2dd4bf",
    "green": "#4ade80",
    "blue": "#60a5fa",
    "orange": "#fb923c",
    "indigo": "#818cf8",
    "slate": "#8b98ac",
    "white": "#e8edf5",
    "magenta": "#f472b6",
    "gold": "#facc15",
    "olive": "#a3b346",
    "crimson": "#f87171",
}


# ------------------------------------------------------------------- the world

#: Plan units to drawing units. ``ROOMS`` lays the station out in a 200 by 124
#: grid, which is the right size for reasoning about adjacency and far too small
#: to draw in: at one unit to the pixel a room is twenty-six pixels across and
#: the people in it are specks. Everything below multiplies by this, so the map
#: is authored once in :mod:`~nullius.station.map` and drawn at a size that has
#: somewhere to put a console.
SCALE = 12.0

#: Wall thickness. Thick enough to have an inner edge that catches the light and
#: a doorway that reads as a gap rather than as a dashed line.
WALL = 9.0

#: Margin around the plan, where the exit plates and the outer lamps live.
MARGIN_X = 236.0
MARGIN_TOP = 70.0
MARGIN_BOTTOM = 120.0

WORLD: tuple[float, float, float, float] = (
    -MARGIN_X,
    -MARGIN_TOP,
    200.0 * SCALE + 2 * MARGIN_X,
    124.0 * SCALE + MARGIN_TOP + MARGIN_BOTTOM,
)
"""The drawing's viewBox: the plan, scaled, with room around it for the exits."""

#: Advance width of one character of the map's monospace stack, in ems. Every
#: label is emitted with an explicit ``textLength`` equal to the width computed
#: from this, so what the layout reserves is what the browser draws — on any
#: platform, at any zoom, whichever fallback font is actually resolved.
EM_ADVANCE = 0.6

#: The nameplate and the chips under it, in units below the floor's top edge.
#: Fixed rather than proportional because they hold text, and text does not
#: scale with the room it is in.
NAMEPLATE_TOP = 8.0
NAMEPLATE_H = 40.0
CHIP_TOP = 54.0
CHIP_H = 23.0
CHIP_LINES = 2

#: Where the interior starts: below two lines of chips, whatever the room's
#: height. Anchoring the furniture here rather than to a fraction of the floor
#: is what keeps a short room's back row from being drawn under its own labels.
CONTENT_TOP = CHIP_TOP + CHIP_LINES * (CHIP_H + 4.0) + 10.0

#: The rest of the interior, as fractions of what is left below that. The
#: off-corridor rooms are shorter than the pipeline rooms and the same layout
#: has to read in both, so the bands are proportional from here down.
BACK_AT, BACK_H = 0.0, 0.24
LANE_AT = 0.38
FRONT_AT, FRONT_H = 0.46, 0.22
FEET_AT = 0.99
AGENT_H = 58.0


def _rng(seed: str) -> Callable[[float, float], float]:
    """A tiny deterministic generator, so two builds draw the same station.

    Seeded from the room's own id rather than from a clock: the furniture is
    decoration, and decoration that moved between builds would make a diff of
    the page unreadable for the parts that are not.
    """
    state = 2166136261
    for character in seed:
        state = ((state ^ ord(character)) * 16777619) & 0xFFFFFFFF

    def nxt(low: float, high: float) -> float:
        nonlocal state
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        return low + (high - low) * (state / 0x7FFFFFFF)

    return nxt


# ------------------------------------------------------------------- geometry


@dataclass(frozen=True, slots=True)
class Box:
    """An axis-aligned rectangle in world units."""

    x: float
    y: float
    w: float
    h: float

    def intersects(self, other: Box) -> bool:
        return (
            self.x < other.x + other.w
            and other.x < self.x + self.w
            and self.y < other.y + other.h
            and other.y < self.y + self.h
        )

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


@dataclass(frozen=True, slots=True)
class Label:
    """One piece of text on the map, measured before it is placed.

    ``length`` is emitted as the element's ``textLength``, which is what makes
    :attr:`box` a promise rather than an estimate: the browser is told the width
    to lay the string out in, so the space reserved here is the space taken.
    """

    text: str
    x: float
    y: float
    size: float
    length: float
    box: Box
    fill: str
    owner: str
    weight: int = 400
    opacity: float = 1.0
    anchor: str = "start"
    letter_spacing: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "size": round(self.size, 2),
            "length": round(self.length, 2),
            "box": {k: round(v, 2) for k, v in self.box.as_dict().items()},
            "fill": self.fill,
            "owner": self.owner,
            "weight": self.weight,
            "opacity": self.opacity,
            "anchor": self.anchor,
        }


def _measure(text: str, size: float) -> float:
    return len(text) * size * EM_ADVANCE


def _truncate(text: str, size: float, max_width: float) -> str:
    """Cut a string to fit its container, with an ellipsis where it was cut.

    A label wider than the plate it sits on is the overflow bug in its general
    form. Truncating is the only option that keeps the box a true statement.
    """
    # The tolerance is not cosmetic. A caller that sizes a plate from
    # ``_measure`` and then hands back ``plate - padding`` gets a float one ulp
    # under what it started with, and the label truncates itself to fit a box
    # that was built around it. That is how BUILDER became BUILD… on a chip
    # sized for BUILDER.
    if _measure(text, size) <= max_width + 1e-6:
        return text
    room_for = int(max_width / (size * EM_ADVANCE))
    if room_for <= 1:
        return "…"
    return text[: room_for - 1] + "…"


def _label(
    text: str,
    *,
    x: float,
    y: float,
    size: float,
    fill: str,
    owner: str,
    max_width: float,
    weight: int = 400,
    opacity: float = 1.0,
    anchor: str = "start",
) -> Label:
    """Place one string, measured and clipped to its container.

    ``y`` is the baseline. The box is the em-box around it, which is what the
    collision check compares — text that shares a line but not a column is fine,
    and text that shares both is the bug this exists to make impossible.
    """
    text = _truncate(text, size, max_width)
    length = _measure(text, size)
    left = x if anchor == "start" else (x - length if anchor == "end" else x - length / 2)
    return Label(
        text=text,
        x=x,
        y=y,
        size=size,
        length=length,
        box=Box(left, y - size * 0.8, length, size * 1.12),
        fill=fill,
        owner=owner,
        weight=weight,
        opacity=opacity,
        anchor=anchor,
    )


def _inner(room: Room) -> Box:
    """The floor: the room's rectangle inset by the thickness of its walls."""
    return Box(
        room.x * SCALE + WALL,
        room.y * SCALE + WALL,
        room.w * SCALE - 2 * WALL,
        room.h * SCALE - 2 * WALL,
    )


def _shell(room: Room) -> Box:
    return Box(room.x * SCALE, room.y * SCALE, room.w * SCALE, room.h * SCALE)


def _usable(floor: Box) -> float:
    """How much floor is left once the nameplate and the chips have had theirs."""
    return floor.h - CONTENT_TOP - 6.0


def _band(floor: Box, at: float) -> float:
    """A point in the interior, measured down from where the labels stop."""
    return floor.y + CONTENT_TOP + _usable(floor) * at


def lane_y(room: Room) -> float:
    """Where the corridor crosses this room, in world units."""
    return _band(_inner(room), LANE_AT)


# ------------------------------------------------------------------- furniture


@dataclass(frozen=True, slots=True)
class Kit:
    """What is in a room: the fixtures against the back wall, and the ones the
    people stand at. Art, not a claim — but a room with nothing in it reads as a
    room that does nothing, which would be a claim, and a false one."""

    back: tuple[str, ...]
    front: tuple[str, ...]


#: Furniture appropriate to the work. A room absent from this mapping gets
#: :data:`DEFAULT_KIT`, so adding a room to the map draws something rather than
#: leaving an empty box.
FURNITURE: dict[str, Kit] = {
    "drafting": Kit(("board", "shelf", "board"), ("desk", "desk")),
    "screening": Kit(("board", "cabinet", "shelf"), ("desk", "desk")),
    "registry": Kit(("cabinet", "cabinet", "rack"), ("console", "console")),
    "workshop": Kit(("rack", "rack", "bench"), ("bench", "crate")),
    "execution": Kit(("rack", "rack", "rack"), ("table", "table")),
    "analysis": Kit(("screen", "screen", "screen"), ("console", "console")),
    "challenge": Kit(("board", "screen", "cabinet"), ("console", "desk")),
    "blind": Kit(("cabinet", "rack", "cabinet"), ("console", "console")),
    "review": Kit(("shelf", "board", "shelf"), ("desk", "desk")),
    "record": Kit(("shelf", "shelf", "shelf"), ("plinth", "plinth")),
    "vault": Kit(("cabinet", "cabinet", "cabinet", "cabinet"), ("terminal",)),
    "treasury": Kit(("cabinet", "rack", "cabinet"), ("console", "crate")),
    "archive": Kit(("shelf", "shelf", "shelf", "shelf"), ("shelf", "crate")),
    "oracle": Kit(("cabinet", "cabinet", "cabinet"), ("plinth", "plinth")),
}

DEFAULT_KIT = Kit(("rack", "cabinet", "shelf"), ("console", "desk"))


def _row(
    kinds: tuple[str, ...],
    floor: Box,
    top: float,
    height: float,
    nxt: Callable[[float, float], float],
) -> list[dict[str, Any]]:
    """Space a row of fixtures evenly along the floor, with a little jitter."""
    if not kinds:
        return []
    pad = 18.0
    span = floor.w - 2 * pad
    cell = span / len(kinds)
    out: list[dict[str, Any]] = []
    for index, kind in enumerate(kinds):
        # Capped as well as proportioned: a room with one thing in it should
        # hold one thing, not one thing stretched across the whole floor.
        width = min(cell * (0.62 + nxt(0.0, 0.16)), 168.0)
        out.append(
            {
                "kind": kind,
                "x": floor.x + pad + index * cell + (cell - width) / 2,
                "y": top,
                "w": width,
                "h": height,
            }
        )
    return out


def _fixtures(room: Room) -> list[dict[str, Any]]:
    """Everything drawn on the floor of one room.

    Five at the least, because a room with three hairlines in it does not read
    as a place and the whole point of the drawing is that these are places.
    """
    nxt = _rng(room.room_id)
    kit = FURNITURE.get(room.room_id, DEFAULT_KIT)
    floor = _inner(room)
    usable = _usable(floor)
    back = _row(kit.back, floor, _band(floor, BACK_AT), usable * BACK_H, nxt)
    front = _row(kit.front, floor, _band(floor, FRONT_AT), usable * FRONT_H, nxt)
    for fixture in back:
        fixture["depth"] = "back"
    for fixture in front:
        fixture["depth"] = "front"
    return back + front


def _sprites(room: Room) -> list[dict[str, Any]]:
    """One figure per role that works in this room, at a workstation.

    A room with no role — the record, the archive, the oracle — gets no figure,
    because nobody is stationed there. Drawing one would be the smallest
    possible lie and it would be the one a reader believes fastest.
    """
    out: list[dict[str, Any]] = []
    if not room.roles:
        return out
    nxt = _rng(room.room_id + "-staff")
    floor = _inner(room)
    stations = [f for f in _fixtures(room) if f["depth"] == "front"] or _fixtures(room)
    for index, role in enumerate(room.roles):
        station = stations[index % len(stations)] if stations else None
        if station is not None and len(room.roles) <= len(stations):
            x = float(station["x"]) + float(station["w"]) / 2
        else:
            step = floor.w / (len(room.roles) + 1)
            x = floor.x + step * (index + 1)
        out.append(
            {
                "role": role.value,
                "x": round(x, 2),
                "y": round(_band(floor, FEET_AT), 2),
                "h": AGENT_H,
                "delay": round(nxt(0.0, 2.6), 2),
                "period": round(3.0 + nxt(0.0, 1.8), 2),
            }
        )
    return out


# ------------------------------------------------------------ walls and doors

_SIDES: dict[str, tuple[float, float]] = {
    "top": (0.0, -1.0),
    "bottom": (0.0, 1.0),
    "left": (-1.0, 0.0),
    "right": (1.0, 0.0),
}


def _doorways(walk: list[Room]) -> dict[str, list[dict[str, Any]]]:
    """Where the corridor breaks each room's wall.

    Read off consecutive rooms in the walk rather than declared, so a room that
    moves in the plan takes its doorways with it.
    """
    out: dict[str, list[dict[str, Any]]] = {room.room_id: [] for room in ROOMS}
    for first, second in pairwise(walk):
        one, two = _shell(first), _shell(second)
        if abs(one.y - two.y) < 1.0:
            side_a, side_b = ("right", "left") if two.x > one.x else ("left", "right")
        else:
            side_a, side_b = ("bottom", "top") if two.y > one.y else ("top", "bottom")
        for room, side in ((first, side_a), (second, side_b)):
            out[room.room_id].append({"side": side, "at": lane_y(room), "span": 62.0})
    return out


def _is_outer(room: Room, side: str) -> bool:
    """Whether nothing sits on the far side of this wall."""
    shell = _shell(room)
    dx, dy = _SIDES[side]
    probe = Box(
        shell.x + (shell.w + 6.0) * max(dx, 0.0) - 70.0 * max(-dx, 0.0) - (6.0 if dx else 0.0),
        shell.y + (shell.h + 6.0) * max(dy, 0.0) - 70.0 * max(-dy, 0.0) - (6.0 if dy else 0.0),
        70.0 if dx else shell.w,
        70.0 if dy else shell.h,
    )
    return not any(_shell(other).intersects(probe) for other in ROOMS if other is not room)


def _exit_side(room: Room, doorways: list[dict[str, Any]]) -> str:
    """Which wall the terminal exits are cut into.

    The one facing away from the middle of the station, among the walls that
    have nothing behind them and no corridor already through them. Computed, so
    that moving a room in the plan moves its exits to the wall that still faces
    out.
    """
    shell = _shell(room)
    centre = (shell.x + shell.w / 2 - 100.0 * SCALE, shell.y + shell.h / 2 - 62.0 * SCALE)
    taken = {door["side"] for door in doorways}
    best, score = "bottom", -1e9
    for side, (dx, dy) in _SIDES.items():
        if side in taken or not _is_outer(room, side):
            continue
        outward = dx * centre[0] + dy * centre[1]
        if outward > score:
            best, score = side, outward
    return best


def _exits(station: Station, doorways: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The terminal doors, cut into the station's outer wall.

    Every one the same size as every other, because the project reports a
    refutation with the same prominence as an institutional claim and a plan
    that drew a narrower door for it would be arguing with that in the one
    medium where nobody reads the caption.
    """
    out: list[dict[str, Any]] = []
    for occupied in station.occupancy:
        room = occupied.room
        if not occupied.doors:
            continue
        side = _exit_side(room, doorways[room.room_id])
        shell, floor = _shell(room), _inner(room)
        vertical = side in ("left", "right")
        run = floor.h if vertical else floor.w
        count = len(occupied.doors)
        gap = 16.0
        extent = (run - gap * (count - 1)) / count
        start = floor.y if vertical else floor.x
        for index, (state, uses) in enumerate(occupied.doors):
            at = start + index * (extent + gap)
            if side == "left":
                rect = Box(shell.x, at, WALL, extent)
                plate = Box(shell.x - 236.0, at - 4.0, 224.0, extent + 8.0)
            elif side == "right":
                rect = Box(shell.x + shell.w - WALL, at, WALL, extent)
                plate = Box(shell.x + shell.w + 12.0, at - 4.0, 224.0, extent + 8.0)
            elif side == "top":
                rect = Box(at, shell.y, extent, WALL)
                plate = Box(at - 4.0, shell.y - 62.0, extent + 8.0, 52.0)
            else:
                rect = Box(at, shell.y + shell.h - WALL, extent, WALL)
                plate = Box(at - 4.0, shell.y + shell.h + 12.0, extent + 8.0, 52.0)
            out.append(
                {
                    "room_id": room.room_id,
                    "state": state,
                    "count": uses,
                    "used": uses > 0,
                    "side": side,
                    "accent": room.accent,
                    # ``w`` is the doorway's extent along the wall. The exits are
                    # equal by construction and a test reads this to say so.
                    "w": round(extent, 6),
                    "rect": rect.as_dict(),
                    "plate": plate.as_dict(),
                    "vertical": vertical,
                }
            )
    return out


# -------------------------------------------------------------------- labelling


def _chips(
    items: list[tuple[str, str]],
    floor: Box,
    owner: str,
    accent: str,
) -> tuple[list[dict[str, Any]], list[Label]]:
    """Role and state chips, wrapped so that none of them leaves the room.

    Two lines at most. The chip band is sized for two, and a third line would
    walk into the furniture — so anything that does not fit is dropped and the
    count said instead, which is a true statement where an overlapping one is
    not.
    """
    size = 13.0
    pad = 9.0
    lines = CHIP_LINES
    chips: list[dict[str, Any]] = []
    labels: list[Label] = []
    x, line = floor.x, 0
    for kind, text in items:
        shown = _truncate(text, size, floor.w - 2 * pad)
        drawn = _measure(shown, size)
        width = drawn + 2 * pad
        if x + width > floor.x + floor.w and x > floor.x:
            line += 1
            x = floor.x
            if line >= lines:
                break
        top = floor.y + CHIP_TOP + line * (CHIP_H + 4.0)
        chips.append(
            {
                "kind": kind,
                "x": round(x, 2),
                "y": round(top, 2),
                "w": round(width, 2),
                "h": CHIP_H,
                "accent": accent,
            }
        )
        labels.append(
            _label(
                shown,
                x=x + pad,
                y=top + CHIP_H * 0.71,
                size=size,
                fill="chip",
                owner=owner,
                max_width=drawn,
            )
        )
        x += width + 7.0
    return chips, labels


def _room_labels(
    station: Station, exits: list[dict[str, Any]]
) -> tuple[list[Label], dict[str, list[dict[str, Any]]]]:
    """Every string on the map, measured, and the chips they sit on."""
    labels: list[Label] = []
    chips: dict[str, list[dict[str, Any]]] = {}

    for occupied in station.occupancy:
        room = occupied.room
        floor = _inner(room)
        labels.append(
            _label(
                room.name,
                x=floor.x,
                y=floor.y + NAMEPLATE_TOP + NAMEPLATE_H * 0.66,
                size=25.0,
                fill="ink",
                owner=room.room_id,
                max_width=floor.w - 34.0,
                weight=650,
            )
        )
        items: list[tuple[str, str]] = [(role.value, role.value.upper()) for role in room.roles]
        if not room.roles:
            items.append(("none", "NO ACTOR STATIONED"))
        terminal = {state.value for state in TERMINAL_DOORS}
        items += [
            ("state", f"state · {state.value}")
            for state in room.states
            if state.value not in terminal
        ]
        room_chips, chip_labels = _chips(items, floor, room.room_id, room.accent)
        chips[room.room_id] = room_chips
        labels += chip_labels

    for door in exits:
        plate = door["plate"]
        centre = plate["x"] + plate["w"] / 2
        if door["vertical"]:
            labels.append(
                _label(
                    str(door["count"]),
                    x=plate["x"] + 14.0,
                    y=plate["y"] + plate["h"] * 0.62,
                    size=26.0,
                    fill="ink",
                    owner=door["room_id"],
                    max_width=46.0,
                    weight=700,
                )
            )
            labels.append(
                _label(
                    door["state"],
                    x=plate["x"] + 62.0,
                    y=plate["y"] + plate["h"] * 0.6,
                    size=14.0,
                    fill="dim",
                    owner=door["room_id"],
                    max_width=plate["w"] - 72.0,
                )
            )
        else:
            labels.append(
                _label(
                    str(door["count"]),
                    x=centre,
                    y=plate["y"] + 24.0,
                    size=24.0,
                    fill="ink",
                    owner=door["room_id"],
                    max_width=plate["w"] - 10.0,
                    weight=700,
                    anchor="middle",
                )
            )
            labels.append(
                _label(
                    door["state"],
                    x=centre,
                    y=plate["y"] + 43.0,
                    size=13.0,
                    fill="dim",
                    owner=door["room_id"],
                    max_width=plate["w"] - 8.0,
                    anchor="middle",
                )
            )
    return labels, chips


def _map_labels(station: Station, exits: list[dict[str, Any]]) -> list[Label]:
    """The captions that belong to the station rather than to a room."""
    world_x, world_y, world_w, _ = WORLD
    labels = [
        _label(
            f"NULLIUS · RESEARCH STATION · PROTOCOL v{station.chapter.version} "
            f"· ARM {station.arm.arm_id} · {station.provider.upper()} PROVIDER",
            x=world_x + 26.0,
            y=world_y + 42.0,
            size=22.0,
            fill="dim",
            owner="map",
            max_width=world_w * 0.62,
        ),
        _label(
            station.mode.upper(),
            x=world_x + world_w - 26.0,
            y=world_y + 42.0,
            size=22.0,
            fill="dim",
            owner="map",
            max_width=world_w * 0.3,
            anchor="end",
        ),
        _label(
            "no corridor crosses this line",
            x=149.0 * SCALE + 24.0,
            y=124.0 * SCALE + 74.0,
            size=17.0,
            fill="faint",
            owner="map",
            max_width=420.0,
        ),
    ]
    if exits and not any(door["used"] for door in exits):
        labels.append(
            _label(
                UNUSED_EXITS,
                x=world_x + 26.0,
                y=124.0 * SCALE + 74.0,
                size=17.0,
                fill="faint",
                owner="map",
                max_width=149.0 * SCALE - 60.0,
            )
        )
    return labels


# ------------------------------------------------------------------- assembly


def plan(station: Station) -> dict[str, Any]:
    """The drawing's geometry, computed from the map and the record.

    Nothing here decides what is true; it decides where true things are drawn.
    Every coordinate is in world units — the plan, scaled up to a size that has
    somewhere to put a console.
    """
    walk = [*corridor(), room_named("record")]
    waypoints = [
        {
            "room_id": room.room_id,
            "x": _shell(room).x + _shell(room).w / 2,
            "y": lane_y(room),
        }
        for room in walk
    ]
    doorways = _doorways(walk)
    exits = _exits(station, doorways)
    labels, chips = _room_labels(station, exits)
    labels += _map_labels(station, exits)

    shells = {room.room_id: _shell(room).as_dict() for room in ROOMS}
    floors = {room.room_id: _inner(room).as_dict() for room in ROOMS}

    return {
        "world": {"x": WORLD[0], "y": WORLD[1], "w": WORLD[2], "h": WORLD[3]},
        "waypoints": waypoints,
        "corridor_ids": [w["room_id"] for w in waypoints],
        # ``doors`` keeps the name and the ``w`` it had when the exits were a
        # strip along the bottom wall: it is what a test reads to check that
        # every way out is drawn the same size as every other.
        "doors": exits,
        "doorways": doorways,
        "shells": shells,
        "floors": floors,
        "chips": chips,
        "labels": [label.as_dict() for label in labels],
        "boxes": [label.box for label in labels],
        "fixtures": {room.room_id: _fixtures(room) for room in ROOMS},
        "sprites": {room.room_id: _sprites(room) for room in ROOMS},
        "lamps": [
            {
                "room_id": room.room_id,
                "x": _shell(room).x + _shell(room).w / 2,
                "y": _shell(room).y + _shell(room).h / 2,
                "r": max(_shell(room).w, _shell(room).h) * 0.62,
                "accent": room.accent,
            }
            for room in ROOMS
        ],
    }


def overlapping_labels(station: Station) -> list[tuple[str, str]]:
    """Pairs of labels whose boxes intersect. Empty is the only passing answer.

    Two captions from different rooms landed on top of each other in M22 and
    rendered as one unreadable string. This is the rule that replaced fixing
    them one at a time.
    """
    boxes: list[Box] = plan(station)["boxes"]
    labels = plan(station)["labels"]
    clashes: list[tuple[str, str]] = []
    for i, first in enumerate(boxes):
        for j in range(i + 1, len(boxes)):
            if first.intersects(boxes[j]):
                clashes.append((labels[i]["text"], labels[j]["text"]))
    return clashes


# -------------------------------------------------------------------- rendering


def _polyline(points: list[dict[str, Any]]) -> str:
    """An SVG path through a list of waypoints, cornered rather than curved."""
    if not points:
        return ""
    head = f"M{points[0]['x']:.2f} {points[0]['y']:.2f}"
    return head + "".join(f" L{p['x']:.2f} {p['y']:.2f}" for p in points[1:])


def _route_path(station: Station, layout: dict[str, Any]) -> str:
    """The path the arm on display actually walks.

    An arm that engages no rooms — the constant arm, and the two that ask a model
    directly — walks a short segment past the station rather than through it,
    because that is what those arms do and drawing nothing at all would read as a
    rendering failure rather than as the finding it is.
    """
    centres = {w["room_id"]: w for w in layout["waypoints"]}
    route = [centres[room_id] for room_id in station.arm_route if room_id in centres]
    if len(route) < 2:
        bottom = 124.0 * SCALE + 48.0
        left, right = -MARGIN_X + 30.0, 200.0 * SCALE + MARGIN_X - 30.0
        return f"M{left:.0f} {bottom:.0f} L{right:.0f} {bottom:.0f}"
    return _polyline(route)


def _arrivals(station: Station, layout: dict[str, Any]) -> list[float]:
    """How far along the route each room sits, as a fraction of its length.

    Used to swell a token as it reaches a room. The keyframes are the rooms the
    arm actually engages, measured along the path the token actually walks, so
    the pulse marks an arrival rather than a rhythm somebody chose.
    """
    centres = {w["room_id"]: w for w in layout["waypoints"]}
    route = [centres[room_id] for room_id in station.arm_route if room_id in centres]
    if len(route) < 2:
        return []
    spans = [((b["x"] - a["x"]) ** 2 + (b["y"] - a["y"]) ** 2) ** 0.5 for a, b in pairwise(route)]
    total = sum(spans) or 1.0
    out, walked = [0.0], 0.0
    for span in spans:
        walked += span
        out.append(walked / total)
    return out


#: How many recorded passes are drawn in motion at once. Capped for legibility
#: and for the frame budget, and the page states the cap beside the figure
#: rather than letting a subset read as the whole record.
TOKENS_IN_MOTION = 18

#: Seconds a token takes to walk the station, and how many ghosts trail it.
TOKEN_SECONDS = 30.0
TRAIL = 4

_BACKING_COLOURS = {"live": "var(--good)", "empty": "var(--warn)", "unbuilt": "var(--bad)"}

#: What a label's declared role means in ink. Named rather than inlined so the
#: template has one way to colour text and the palette lives in one place.
_LABEL_FILLS = {
    "ink": "var(--plate-ink)",
    "dim": "var(--plate-dim)",
    "chip": "var(--plate-dim)",
    "faint": "var(--plate-faint)",
}


def _token_colour(token: dict[str, Any]) -> str:
    if token["abstained"]:
        return "var(--warn)"
    return "var(--good)" if token["correct"] else "var(--bad)"


def _pulse(arrivals: list[float]) -> tuple[str, str]:
    """``values`` and ``keyTimes`` for a token that swells at each room."""
    if not arrivals:
        return "1;1", "0;1"
    times: list[float] = []
    values: list[str] = []
    for fraction in arrivals:
        for offset, value in ((-0.018, "1"), (0.0, "1.55"), (0.018, "1")):
            at = min(1.0, max(0.0, fraction + offset))
            if times and at <= times[-1]:
                continue
            times.append(at)
            values.append(value)
    if times[0] > 0.0:
        times.insert(0, 0.0)
        values.insert(0, "1")
    if times[-1] < 1.0:
        times.append(1.0)
        values.append("1")
    return ";".join(values), ";".join(f"{t:.4f}" for t in times)


def _moving(station: Station, arrivals: list[float]) -> list[dict[str, Any]]:
    """The passes drawn in motion, evenly spaced along the record.

    Sampled across the recorded outcomes rather than taken from the front, so the
    drawing is not a picture of whichever items the bank happens to list first.
    """
    passes = station.tokens
    if not passes:
        return []
    step = max(1, len(passes) // TOKENS_IN_MOTION)
    chosen = list(passes)[::step][:TOKENS_IN_MOTION]
    pulse, key_times = _pulse(arrivals)
    out: list[dict[str, Any]] = []
    for index, token in enumerate(chosen):
        row = token.as_dict()
        row["dur"] = f"{TOKEN_SECONDS:.1f}"
        row["begin"] = f"{-TOKEN_SECONDS * index / max(1, len(chosen)):.2f}"
        row["colour"] = _token_colour(row)
        # ``pulse`` rather than ``values``: Jinja resolves an attribute before a
        # key, so a dict key named after a dict method renders as the method.
        # This is the second time it has happened in this template — the first
        # put ``<built-in method items>`` in a table cell — hence the test.
        row["pulse"] = pulse
        row["key_times"] = key_times
        row["trail"] = [
            {"begin": f"{float(row['begin']) - 0.16 * (n + 1):.2f}", "opacity": 0.5 - 0.1 * n}
            for n in range(TRAIL)
        ]
        out.append(row)
    return out


def switchboard_rooms(station: Station) -> list[Room]:
    """The columns of the ladder table: every room any arm engages.

    Derived from the arms rather than listed, so a room that becomes reachable
    — the Review room, once something dispatches a Reviewer — gains a column
    without anybody remembering to add one.
    """
    from nullius.station.model import engaged_rooms

    reachable = {room_id for run in station.chapter.runs for room_id in engaged_rooms(run.arm)}
    return [room for room in ROOMS if room.room_id in reachable]


def _switchboard(station: Station) -> list[dict[str, Any]]:
    """Every arm in the protocol on display, and the rooms it engages."""
    from nullius.station.model import engaged_rooms

    columns = [room.room_id for room in switchboard_rooms(station)]
    rows: list[dict[str, Any]] = []
    for run in station.chapter.runs:
        engaged = engaged_rooms(run.arm)
        rows.append(
            {
                "arm_id": run.arm.arm_id,
                "label": run.arm.label,
                "kind": run.arm.kind.value,
                "model_dependent": run.arm.model_dependent,
                "cells": [room_id in engaged for room_id in columns],
            }
        )
    return rows


def environment() -> Environment:
    env = Environment(
        loader=PackageLoader("nullius.station", "templates"),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["accent"] = lambda name: ACCENTS.get(name, ACCENTS["white"])
    env.globals["backing_colour"] = lambda name: _BACKING_COLOURS.get(str(name), "var(--warn)")
    env.globals["token_colour"] = _token_colour
    env.globals["label_fill"] = lambda name: _LABEL_FILLS.get(str(name), "var(--plate-ink)")
    return env


def _context(station: Station) -> dict[str, object]:
    layout = plan(station)
    arrivals = _arrivals(station, layout)
    return {
        "station": station,
        "plan": layout,
        "world": layout["world"],
        "corridor_d": _polyline(layout["waypoints"]),
        "route_d": _route_path(station, layout),
        "switchboard_rooms": switchboard_rooms(station),
        "terminal_states": [state.value for state in TERMINAL_DOORS],
        "moving": _moving(station, arrivals),
        "switchboard": _switchboard(station),
        "principles": PRINCIPLES,
        "cannot_show": CANNOT_SHOW,
        "unused_exits": UNUSED_EXITS,
        "accents": ACCENTS,
        "rooms": station.occupancy,
        "wall": WALL,
        "payload": payload(station),
    }


def render_station(station: Station | None = None, **kwargs: Any) -> str:
    """The station as one self-contained HTML document."""
    station = station or assemble(**kwargs)
    return environment().get_template("station.html").render(**_context(station))


def write_station(out: Path, *, station: Station | None = None, **kwargs: Any) -> Path:
    """Render the station to ``out`` and return the path written."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_station(station, **kwargs), encoding="utf-8")
    return out
