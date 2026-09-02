"""Drawing the station.

Everything numeric comes from :mod:`nullius.station.model`, which reads it from
committed protocols, committed results, locked truths and — where one is given —
a ladder's ledger. The prose here is the only hand-written content on the page,
and it is declared as data for the same reason ``paper/render.py`` declares its
flaws and limitations that way: so it can be read, counted and checked in one
place rather than woven through a template where nobody will find it again.

**What the art may and may not do.** The geometry is generated, not drawn: rooms
are laid out from :data:`~nullius.station.map.ROOMS`, the corridor is the enum's
declaration order, and the fixtures inside each room are placed by a hash of the
room's own id so that two builds of the same record produce the same picture. No
binary asset ships, which keeps the page a single file and the diff readable.

**What the animation may not do.** Agents do not converse in this architecture,
so nothing here draws them conversing. A token's route is the arm's switches and
its exit is the recorded verdict; the pacing is display and the page says so.
Depicting a meeting would make the picture disagree with the system it is a
picture of, which is the one failure a diagram of an institution cannot survive.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from nullius.station.map import ROOMS, TERMINAL_DOORS, Room, corridor, room_named
from nullius.station.model import Station, assemble, payload

__all__ = [
    "ACCENTS",
    "CANNOT_SHOW",
    "PRINCIPLES",
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


# ------------------------------------------------------------------- geometry


def _rng(seed: str) -> Any:
    """A tiny deterministic generator, so two builds draw the same station.

    Seeded from the room's own id rather than from a clock: the fixtures are
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


#: The room's interior, in plan units below its own top edge. The corridor runs
#: between the text block and the console block, so a token passing through a
#: room passes behind the people working in it rather than across the label.
CONSOLE_BAND = 3.2
SPRITE_BAND = 5.2
CORRIDOR_BAND = 8.6
DOOR_BAND = 0.7


def _fixtures(room: Room) -> list[dict[str, float]]:
    """Consoles along the room's inner wall. Placed, not drawn by hand."""
    nxt = _rng(room.room_id)
    count = 3 + int(nxt(0, 2.99))
    step = (room.w - 7.0) / max(1, count - 1)
    out: list[dict[str, float]] = []
    for index in range(count):
        out.append(
            {
                "x": room.x + 3.0 + index * step,
                "y": room.y + room.h - CONSOLE_BAND,
                "w": 3.4 + nxt(0, 1.4),
                "h": 1.7,
            }
        )
    return out


def _sprites(room: Room) -> list[dict[str, Any]]:
    """One figure per role that works in this room, at a console.

    A room with no role — the record, the archive, the oracle — gets no figure,
    because nobody is stationed there. Drawing one would be the smallest
    possible lie and it would be the one a reader believes fastest.
    """
    out: list[dict[str, Any]] = []
    roles = room.roles
    for index, role in enumerate(roles):
        span = room.w - 8.0
        step = span / max(1, len(roles))
        out.append(
            {
                "x": room.x + 4.0 + step * index + step / 2,
                "y": room.y + room.h - SPRITE_BAND,
                "role": role.value,
            }
        )
    return out


def plan(station: Station) -> dict[str, Any]:
    """The drawing's geometry, computed from the map and the record.

    Returns waypoints for the corridor, the doors on each room that owns a
    terminal state, and the fixtures and figures inside every room. Nothing here
    decides what is true; it decides where true things are drawn.
    """
    walk = [*corridor(), room_named("record")]
    waypoints = [
        {
            "room_id": room.room_id,
            "x": room.x + room.w / 2,
            "y": room.y + room.h - CORRIDOR_BAND,
        }
        for room in walk
    ]

    doors: list[dict[str, Any]] = []
    for occupied in station.occupancy:
        room = occupied.room
        count_of_doors = len(occupied.doors)
        if not count_of_doors:
            continue
        # Every exit is the same width. The project reports refutation with the
        # same prominence as an institutional claim, and a plan that drew a
        # narrower door for it would be arguing with that in the one medium
        # where nobody reads the caption.
        gap = 1.4
        width = (room.w - 4.0 - gap * (count_of_doors - 1)) / count_of_doors
        start = room.x + 2.0
        for index, (state, count) in enumerate(occupied.doors):
            doors.append(
                {
                    "room_id": room.room_id,
                    "state": state,
                    "count": count,
                    "x": start + index * (width + gap),
                    "y": room.y + room.h - DOOR_BAND,
                    "w": width,
                    "accent": room.accent,
                }
            )

    return {
        "waypoints": waypoints,
        "corridor_ids": [w["room_id"] for w in waypoints],
        "doors": doors,
        "fixtures": {room.room_id: _fixtures(room) for room in ROOMS},
        "sprites": {room.room_id: _sprites(room) for room in ROOMS},
    }


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
        return "M2 121 L198 121"
    return _polyline(route)


#: How many recorded passes are drawn in motion at once. Capped for legibility
#: and for the frame budget, and the page states the cap beside the figure
#: rather than letting a subset read as the whole record.
TOKENS_IN_MOTION = 30

#: Seconds a token takes to walk the station, and the gap between departures.
TOKEN_SECONDS = 26.0

_BACKING_COLOURS = {"live": "var(--good)", "empty": "var(--warn)", "unbuilt": "var(--bad)"}


def _token_colour(token: Any) -> str:
    if token["abstained"]:
        return "var(--warn)"
    return "var(--good)" if token["correct"] else "var(--bad)"


def _moving(station: Station) -> list[dict[str, Any]]:
    """The passes drawn in motion, evenly spaced along the record.

    Sampled across the recorded outcomes rather than taken from the front, so the
    drawing is not a picture of whichever items the bank happens to list first.
    """
    passes = station.tokens
    if not passes:
        return []
    step = max(1, len(passes) // TOKENS_IN_MOTION)
    chosen = list(passes)[::step][:TOKENS_IN_MOTION]
    out: list[dict[str, Any]] = []
    for index, token in enumerate(chosen):
        row = token.as_dict()
        row["dur"] = f"{TOKEN_SECONDS:.1f}"
        row["begin"] = f"{-TOKEN_SECONDS * index / max(1, len(chosen)):.2f}"
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
    return env


def _context(station: Station) -> dict[str, object]:
    layout = plan(station)
    return {
        "station": station,
        "plan": layout,
        "corridor_d": _polyline(layout["waypoints"]),
        "route_d": _route_path(station, layout),
        "switchboard_rooms": switchboard_rooms(station),
        "terminal_states": [state.value for state in TERMINAL_DOORS],
        "moving": _moving(station),
        "switchboard": _switchboard(station),
        "principles": PRINCIPLES,
        "cannot_show": CANNOT_SHOW,
        "accents": ACCENTS,
        "rooms": station.occupancy,
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
