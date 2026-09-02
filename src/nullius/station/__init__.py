"""The station: the institution drawn as the facility it is.

One room per department, laid out from ``db/enums.py`` rather than beside it, so
every institutional role is stationed somewhere and every state of the research
machine is owned by exactly one room. Adding a role or a state to the enum
breaks this build until a room claims it.

The page is generated from the same record the paper is assembled from, and
refuses on the same terms: a drawing whose inputs no longer check out is worse
than no drawing, because it looks like evidence and it is prettier than the
paper. Every figure names the artifact it was read out of, and nothing is
animated that no row records.
"""

from __future__ import annotations

from nullius.station.ledger import LedgerView, open_ledger
from nullius.station.map import (
    PIPELINE_STATES,
    ROOMS,
    TERMINAL_DOORS,
    Backing,
    Room,
    Wing,
    corridor,
    dead_switches,
    unrepresented_roles,
    unrepresented_states,
)
from nullius.station.model import (
    Figure,
    Occupancy,
    Station,
    Token,
    assemble,
    engaged_rooms,
    route_for,
)
from nullius.station.render import (
    ACCENTS,
    CANNOT_SHOW,
    PRINCIPLES,
    Principle,
    environment,
    plan,
    render_station,
    write_station,
)

__all__ = [
    "ACCENTS",
    "CANNOT_SHOW",
    "PIPELINE_STATES",
    "PRINCIPLES",
    "ROOMS",
    "TERMINAL_DOORS",
    "Backing",
    "Figure",
    "LedgerView",
    "Occupancy",
    "Principle",
    "Room",
    "Station",
    "Token",
    "Wing",
    "assemble",
    "corridor",
    "dead_switches",
    "engaged_rooms",
    "environment",
    "open_ledger",
    "plan",
    "render_station",
    "route_for",
    "unrepresented_roles",
    "unrepresented_states",
    "write_station",
]
