"""M22: the station must not be able to draw something the record does not say.

A picture is more persuasive than a table and less obviously checkable, which
makes it the most dangerous artifact this project has produced. These tests are
about what the drawing is structurally unable to do: invent a room, invent a
figure, animate an event nobody recorded, or quietly stop covering a role or a
state that the code has since added.
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest

from nullius.benchmark.arms import LADDER_V6, Arm, ArmKind
from nullius.benchmark.protocol import PROTOCOL_VERSIONS
from nullius.benchmark.runner import mechanisms_for
from nullius.db.enums import TERMINAL_STATES, HypothesisState, Role, Verdict
from nullius.station.brief import DEPTS, PEOPLE, numerals
from nullius.station.ledger import open_ledger
from nullius.station.map import (
    HANDLED_OUTSIDE_THE_KERNEL,
    PIPELINE_STATES,
    ROOMS,
    TERMINAL_DOORS,
    Backing,
    Wing,
    corridor,
    dead_switches,
    declared_switches,
    room_named,
    unread_switches,
    unrepresented_roles,
    unrepresented_states,
)
from nullius.station.model import Station, assemble, engaged_rooms, route_for
from nullius.station.render import (
    CANNOT_SHOW,
    DEFAULT_KIT,
    FURNITURE,
    HEIGHTS,
    ITEM_COLUMNS,
    MOUNTED,
    PRINCIPLES,
    SKINS,
    STANDS,
    TONES,
    UNUSED_EXITS,
    WORKABLE,
    Box,
    _arrivals,
    _moving,
    arm_records,
    environment,
    overlapping_labels,
    plan,
    render_station,
    write_station,
)

LEDGERS = sorted(Path(".nullius").glob("benchmark-v*/b*.sqlite"))


def _text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    stripped = re.sub(r"<style.*?</style>|<script.*?</script>", "", raw, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", stripped))


# ------------------------------------------------------- the map cannot drift


def test_every_role_is_stationed_somewhere() -> None:
    """The fourth thing in this project keyed by an enum, and the first derived
    from it. A role added to ``db/enums.py`` breaks this build until a room
    claims it, which is the only mechanism that has ever stopped this drift."""
    assert unrepresented_roles() == frozenset()
    housed = {role for room in ROOMS for role in room.roles}
    assert housed == set(Role)


def test_every_state_is_owned_by_exactly_one_room() -> None:
    assert unrepresented_states() == frozenset()
    owners: dict[HypothesisState, list[str]] = {}
    for room in ROOMS:
        for state in room.states:
            owners.setdefault(state, []).append(room.room_id)
    assert set(owners) == set(HypothesisState)
    for state, rooms in owners.items():
        assert len(rooms) == 1, f"{state.value} is claimed by {rooms}"


def test_the_corridor_is_the_enums_declaration_order() -> None:
    """Reordering ``HypothesisState`` reorders the station, rather than leaving
    a drawing that quietly disagrees with the machine it draws."""
    walked = [state for room in corridor() for state in room.states if state in PIPELINE_STATES]
    assert walked == list(PIPELINE_STATES)


def test_every_terminal_state_has_a_door_and_they_are_the_same_width() -> None:
    """Refuted and inconclusive are terminal successes of the process. A plan
    that drew narrower doors for them would be arguing with the project's own
    design principle in the medium where nobody reads the caption."""
    assert set(TERMINAL_DOORS) == set(TERMINAL_STATES)

    station = assemble()
    drawn = {door["state"]: door for door in plan(station)["doors"]}
    assert set(drawn) == {state.value for state in TERMINAL_STATES}

    by_room: dict[str, set[float]] = {}
    for door in drawn.values():
        by_room.setdefault(door["room_id"], set()).add(round(float(door["w"]), 6))
    for room_id, widths in by_room.items():
        assert len(widths) == 1, f"{room_id} draws its exits at different widths: {widths}"


def test_a_room_with_no_actor_is_drawn_with_nobody_in_it() -> None:
    """The smallest possible lie, and the one a reader believes fastest."""
    station = assemble()
    sprites = plan(station)["sprites"]
    for room in ROOMS:
        assert len(sprites[room.room_id]) == len(room.roles), room.room_id
        assert [s["role"] for s in sprites[room.room_id]] == [r.value for r in room.roles]


def test_the_drawing_is_the_same_twice() -> None:
    """Fixtures are seeded from the room's own id rather than a clock, so a diff
    of the page shows what changed in the record and nothing else."""
    assert render_station() == render_station()


# ------------------------------------------------- the switch that reaches nothing


def test_the_probe_can_tell_a_live_switch_from_a_dead_one() -> None:
    """A test that cannot fail is worse than no test. Before believing that
    flipping ``reviewer`` changes nothing, prove the same probe notices when
    flipping ``custodian`` does."""
    probe = Arm(arm_id="probe", label="probe", isolates="probe", kind=ArmKind.INSTITUTIONAL)
    assert mechanisms_for(replace(probe, custodian=True)) != mechanisms_for(probe)
    assert mechanisms_for(replace(probe, adversary=True)) != mechanisms_for(probe)
    assert "custodian" not in unread_switches()
    assert "adversary" not in unread_switches()


def test_switches_handled_elsewhere_really_are_unread_by_the_kernel() -> None:
    """``HANDLED_OUTSIDE_THE_KERNEL`` is a claim that a switch acts somewhere
    other than through ``mechanisms_for``. This checks the half of that claim
    which can be checked, so the dict cannot grow into a place to excuse a
    switch that does nothing at all."""
    for field, why in HANDLED_OUTSIDE_THE_KERNEL.items():
        assert field in declared_switches(), field
        assert field in unread_switches(), f"{field} does reach the kernel; drop the excuse"
        assert why.strip()


def test_a_dead_switch_is_named_on_the_page_and_locks_the_room_it_would_drive(
    tmp_path: Path,
) -> None:
    """The station reports what it finds rather than what was true when it was
    written. If something wires the Reviewer, ``dead_switches`` empties, the
    banner disappears and the Review room joins the route — and this test keeps
    passing without anybody editing it."""
    station = assemble()
    assert station.dead == tuple(sorted(dead_switches()))

    page = _text(write_station(tmp_path / "station.html", station=station))
    for switch in station.dead:
        assert switch in page, switch

    review = station.occupancy_of("review")
    if "reviewer" in station.dead:
        assert review.backing is Backing.UNBUILT
        assert review.figures == ()
        assert "review" not in route_for(station.arm)
    else:
        assert "review" in route_for(station.arm)


def test_every_declared_switch_is_accounted_for() -> None:
    """Each arm field is live, handled elsewhere with a stated reason, or
    reported as dead. There is no fourth category, and no way to be silent."""
    live = declared_switches() - unread_switches()
    excused = frozenset(HANDLED_OUTSIDE_THE_KERNEL)
    assert live | excused | dead_switches() == declared_switches()
    assert not (live & dead_switches())


# ------------------------------------------------------- nothing here was typed


def test_every_figure_names_the_artifact_it_came_from() -> None:
    """A figure with nowhere to point is a figure somebody typed."""
    for ledger in (None, LEDGERS[0] if LEDGERS else None):
        station = assemble(ledger=ledger)
        seen = 0
        for occupied in station.occupancy:
            for figure in occupied.figures:
                assert figure.source.strip(), f"{occupied.room.room_id}: {figure.label}"
                assert figure.value.strip(), f"{occupied.room.room_id}: {figure.label}"
                seen += 1
        assert seen > 0


#: Milestone references (M20) and arm identifiers (B4) are the only digits the
#: hand-written prose is allowed, because both are names read out of the code.
_ALLOWED_DIGITS = re.compile(r"\bM\d+\w*\b|\bB\d\b")


def _hand_written() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for principle in PRINCIPLES:
        out.append((principle.title, principle.body))
    for index, limitation in enumerate(CANNOT_SHOW):
        out.append((f"cannot_show[{index}]", limitation))
    for room in ROOMS:
        out.append((f"{room.room_id}.charter", room.charter))
        out.append((f"{room.room_id}.invariant", room.invariant))
        out.append((f"{room.room_id}.unbuilt_because", room.unbuilt_because))
    for field, why in HANDLED_OUTSIDE_THE_KERNEL.items():
        out.append((f"handled_outside[{field}]", why))
    for role, (title, plain) in PEOPLE.items():
        out.append((f"people[{role}].title", title))
        out.append((f"people[{role}]", plain))
    for room_id, dept in DEPTS.items():
        out.append((f"depts[{room_id}].plain", dept.plain))
        out.append((f"depts[{room_id}].next_up", dept.next_up))
        for index, step in enumerate(dept.steps):
            out.append((f"depts[{room_id}].steps[{index}]", step))
        out.append((f"depts[{room_id}].desk.lead", dept.desk.lead))
        for section in dept.desk.sections:
            out.append((f"depts[{room_id}].desk[{section.heading}]", section.body))
    return out


def test_no_hand_written_prose_on_the_page_contains_a_figure() -> None:
    """The paper's discipline, applied to a picture. Every quantity reaches the
    page through a ``Figure``, which cannot be built without naming its source;
    the prose is declared as data here so it can be checked in one place."""
    for where, prose in _hand_written():
        remaining = _ALLOWED_DIGITS.sub("", prose)
        assert not re.search(r"\d", remaining), f"{where} states a figure in prose: {prose!r}"


def test_the_hand_written_prose_is_all_of_it() -> None:
    """Anything the template says on its own is a place a number could hide.
    Guard the count so that adding prose is a deliberate act."""
    assert len(PRINCIPLES) == 4
    assert len(CANNOT_SHOW) == 4
    for principle in PRINCIPLES:
        assert principle.title.endswith(".")


def test_the_template_refuses_an_undefined_name() -> None:
    from jinja2 import UndefinedError

    with pytest.raises(UndefinedError):
        environment().from_string("{{ nothing_defines_this }}").render()


def test_nothing_is_left_unsubstituted(tmp_path: Path) -> None:
    raw = _built()
    assert "{{" not in raw and "{%" not in raw


# ------------------------------------------------------------ what it refuses


def test_it_refuses_to_build_from_inputs_that_do_not_check_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A station whose inputs no longer check out is worse than no station,
    because it looks like evidence and it is prettier than the paper."""
    monkeypatch.setitem(
        PROTOCOL_VERSIONS,
        "99",
        {**PROTOCOL_VERSIONS["1"], "path": Path("benchmark/protocol.v99.lock.json")},
    )
    with pytest.raises(ValueError, match="do not check out"):
        assemble(strict=True)


def test_it_builds_from_inputs_that_do_check_out() -> None:
    """The other direction, so the refusal above is not passing because the
    build refuses everything."""
    station = assemble(strict=True)
    assert station.problems == ()
    assert station.chapter.protocol.protocol_hash


def test_a_station_built_from_damaged_inputs_says_so_on_its_face(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        PROTOCOL_VERSIONS,
        "99",
        {**PROTOCOL_VERSIONS["1"], "path": Path("benchmark/protocol.v99.lock.json")},
    )
    page = _text(write_station(tmp_path / "station.html", strict=False))
    assert "do not check out" in page


def test_an_unknown_protocol_or_arm_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ValueError, match="no protocol"):
        assemble(protocol="99")
    with pytest.raises(ValueError, match="no arm"):
        assemble(arm_id="B99")


# ------------------------------------------------------- what the page must say


def test_the_provider_is_read_from_the_results_and_shown(tmp_path: Path) -> None:
    """Every result to date is from a mock. When a live run arrives the page has
    to notice by itself, so this is tested in both directions."""
    station = assemble()
    assert station.provider == "mock"
    assert not station.live_provider
    mock_page = _text(write_station(tmp_path / "mock.html", station=station))
    assert "mock" in mock_page.lower()
    assert "produced under the mock provider" in " ".join(mock_page.split())

    live = replace(station, paper=replace(station.paper, provider="anthropic"))
    assert live.live_provider
    live_page = _text(write_station(tmp_path / "live.html", station=live))
    assert "anthropic" in live_page.lower()
    assert "produced under the mock provider" not in " ".join(live_page.split())


def test_model_dependent_arms_are_labelled(tmp_path: Path) -> None:
    station = assemble()
    page = " ".join(_text(write_station(tmp_path / "station.html", station=station)).split())
    flagged = [run.arm for run in station.chapter.runs if run.arm.model_dependent]
    assert flagged, "the ladder on display has no model-dependent arm to label"
    assert "model-dependent" in page
    for arm in flagged:
        assert arm.arm_id in page


def test_a_contrast_separated_only_by_a_model_mediated_switch_is_not_shown_as_an_interval(
    tmp_path: Path,
) -> None:
    """M20's correction, carried into the drawing. Under a provider whose output
    does not depend on its input the mechanism is delivered and discarded, and
    the interval measures two custody draws."""
    station = assemble()
    detail = station.occupancy_of("analysis").detail
    rows = [*detail["prediction_contrasts"], *detail["comparisons"]]
    mediated = [row for row in rows if not row["interpretable"]]
    assert mediated, "no model-mediated contrast in this protocol to label"
    for row in mediated:
        assert "not interpretable" in row["note"]

    page = " ".join(_text(write_station(tmp_path / "station.html", station=station)).split())
    assert "not interpretable" in page


def test_the_verdict_counts_are_not_the_terminal_states() -> None:
    """Two different enums about two different things. ``Verdict`` is an answer
    about the world; ``HypothesisState`` is where a hypothesis stopped.

    Both vocabularies contain the words ``refuted`` and ``inconclusive``, which
    is precisely why neither may be read off the other: the counts under those
    two words come from different tables and mean different things. The room
    keeps them in separate blocks and says so, and this checks all three.
    """
    station = assemble()
    record = station.occupancy_of("record")
    doors = dict(record.doors)
    verdicts = {figure.label: int(figure.value) for figure in record.figures}

    assert set(verdicts) == {verdict.value for verdict in Verdict}
    assert set(doors) < {state.value for state in TERMINAL_STATES}
    assert set(doors) & set(verdicts), "the overlap this test exists for is gone"

    assert sum(verdicts.values()) == len(station.tokens)
    assert any("not the terminal states" in note for note in record.notes)


def test_no_terminal_state_has_ever_been_recorded() -> None:
    """The finding this build made, pinned so that fixing it is visible rather
    than silent. ``advance_hypothesis`` is called with six of fifteen states
    anywhere in ``src/``; a hypothesis stops at ``analyzed`` in every ledger this
    project has produced, so every exit on the map is unused. If a code path
    starts writing one, this fails and the page starts reporting it."""
    if not LEDGERS:
        pytest.skip("no ladder ledger on this machine; they are outputs, not committed")
    reached: set[str] = set()
    for path in LEDGERS:
        view = open_ledger(path)
        reached |= {state for state, count in view.transitions if count}
    assert reached, "no state transition was recorded at all, which is a different bug"
    assert not reached & {state.value for state in TERMINAL_STATES}


# --------------------------------------------------------- self-contained output


def test_the_page_is_one_file_that_works_offline() -> None:
    """No CDN, no stylesheet, no remote image. It has to render from a clean
    clone with nothing but a browser."""
    raw = _built()
    assert "<link" not in raw.lower()
    assert "@import" not in raw
    for pattern in (r'src\s*=\s*"https?:', r'href\s*=\s*"https?:', r"url\(\s*https?:"):
        assert not re.search(pattern, raw, flags=re.I), pattern
    # `href` appears on <mpath>, which points at a path in this same document.
    for match in re.findall(r'href\s*=\s*"([^"]*)"', raw):
        assert match.startswith("#"), match


def test_it_builds_with_no_ledger_present(tmp_path: Path) -> None:
    """Aggregate mode is the one CI runs, from committed artifacts alone."""
    station = assemble(ledger=None)
    assert station.mode == "aggregate"
    assert station.ledger is None
    for occupied in station.occupancy:
        for figure in occupied.figures:
            assert not figure.source.startswith("ledger"), figure.label
    assert write_station(tmp_path / "station.html", station=station).exists()


def test_a_missing_ledger_is_refused_rather_than_ignored(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        assemble(ledger=tmp_path / "nothing.sqlite")


# ------------------------------------------------------------------ ledger mode


@pytest.mark.skipif(not LEDGERS, reason="ladder ledgers are outputs and are not committed")
def test_ledger_mode_says_it_is_in_ledger_mode(tmp_path: Path) -> None:
    station = assemble(ledger=LEDGERS[0])
    assert station.mode == "ledger"
    page = _text(write_station(tmp_path / "station.html", station=station))
    assert "ledger" in page.lower()


@pytest.mark.skipif(not LEDGERS, reason="ladder ledgers are outputs and are not committed")
def test_reading_a_ledger_cannot_write_to_it() -> None:
    """Opened through a read-only URI, so a bug cannot make this a second write
    path. Proven by trying, rather than by the connection string looking right."""
    path = LEDGERS[0]
    before = path.stat().st_mtime_ns
    open_ledger(path)
    assert path.stat().st_mtime_ns == before

    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection, pytest.raises(sqlite3.OperationalError):
        connection.execute("create table if not exists intrusion (x integer)")


@pytest.mark.skipif(not LEDGERS, reason="ladder ledgers are outputs and are not committed")
def test_the_custody_boundary_is_counted_rather_than_asserted() -> None:
    """A CHECK constraint refuses a holdout metric from anyone but the Custodian.
    The Vault shows that holding on the record in front of it, which is a
    different statement from the constraint existing."""
    custodied = [p for p in LEDGERS if open_ledger(p).seal]
    if not custodied:
        pytest.skip("no custodied arm's ledger on this machine")
    view = open_ledger(custodied[0])
    others = sum(
        n for split, who, n in view.results_by_split if split == "holdout" and who != "custodian"
    )
    assert others == 0
    assert view.seal["holdout/custodian"] > 0


@pytest.mark.skipif(not LEDGERS, reason="ladder ledgers are outputs and are not committed")
def test_an_uncustodied_arm_leaves_the_vault_untouched() -> None:
    """B3 is the arm with no Custodian, and the room it does not enter is empty
    in its ledger. That is the switch being connected rather than recorded."""
    uncustodied = [p for p in LEDGERS if p.stem.startswith("b3")]
    if not uncustodied:
        pytest.skip("no uncustodied arm's ledger on this machine")
    assert open_ledger(uncustodied[0]).seal == {}


# --------------------------------------------------------------- what it draws


def test_an_arm_walks_only_the_rooms_its_switches_engage() -> None:
    """The route is computed from the arm, so an arm with a mechanism switched
    off is drawn bypassing that room rather than visiting it dimly."""
    by_id = {arm.arm_id: arm for arm in LADDER_V6}
    assert route_for(by_id["B0"]) == ()
    assert route_for(by_id["B1"]) == ()
    assert route_for(by_id["B2"]) == ()

    assert "challenge" not in route_for(by_id["B4"])
    assert "challenge" in route_for(by_id["B5"])
    assert "blind" not in route_for(by_id["B5"])
    assert "blind" in route_for(by_id["B6"])

    assert "vault" not in engaged_rooms(by_id["B3"])
    assert "vault" in engaged_rooms(by_id["B4"])
    assert "archive" in engaged_rooms(by_id["B6"])
    assert "archive" not in engaged_rooms(by_id["B7"])


def test_the_two_sealed_rooms_are_on_no_route() -> None:
    """The Vault has no corridor into it and the institution never opens the
    Oracle. A route through either would be the drawing contradicting the
    isolation the project's claims rest on."""
    for arm in LADDER_V6:
        assert "vault" not in route_for(arm)
        assert "oracle" not in route_for(arm)
        assert "oracle" not in engaged_rooms(arm)


def test_every_token_is_a_recorded_outcome() -> None:
    """No animation without a backing row."""
    station = assemble()
    run = next(r for r in station.chapter.runs if r.arm.arm_id == station.arm.arm_id)
    recorded = {(o.item_id, o.replicate, o.verdict.value) for o in run.outcomes}
    assert len(station.tokens) == len(run.outcomes)
    for token in station.tokens:
        assert (token.item_id, token.replicate, token.verdict) in recorded


def test_the_embedded_record_is_readable_json() -> None:
    """The page carries its own record inline, so a reader can check a figure
    against the data the drawing was built from without leaving the file."""
    raw = _built()
    match = re.search(
        r'<script id="station-record" type="application/json">(.*?)</script>', raw, flags=re.S
    )
    assert match
    record = json.loads(match.group(1))
    assert record["mode"] == "aggregate"
    assert record["provider"] == "mock"
    assert len(record["rooms"]) == len(ROOMS)


def _station_for(protocol: str) -> Station:
    return assemble(protocol=protocol)


def test_every_run_protocol_can_be_drawn() -> None:
    """The paper went stale the moment a sixth protocol was registered. The
    station reads the registry rather than a table beside it, so every protocol
    that produced results can be put on the map."""
    for version in sorted(PROTOCOL_VERSIONS, key=int):
        results = Path(
            "benchmark/results.lock.json"
            if version == "1"
            else f"benchmark/results.v{version}.lock.json"
        )
        if not results.exists():
            continue
        station = _station_for(version)
        assert station.chapter.version == version
        assert station.tokens


# ------------------------------------------------------------------ M24: drawn


def test_no_two_labels_on_the_map_overlap() -> None:
    """Two captions from different rooms landed on top of each other in M22 and
    rendered as one unreadable string. Fixing those two would have left the next
    pair to be found by eye.

    Every string on the map now goes through one placement function that
    measures it, clips it to its container and records the box it occupies, and
    that box is a promise rather than an estimate because the same width is
    emitted as the element's ``textLength``. Overlap is therefore a property of
    the layout that can be checked here rather than a rendering accident that
    has to be noticed.
    """
    assert overlapping_labels(assemble()) == []


def test_labels_hold_at_every_zoom() -> None:
    """Boxes are in world units and the camera is a uniform transform, so a pair
    that does not intersect at one scale cannot intersect at another. Checked
    rather than argued, at the scales the viewport actually reaches."""
    boxes = plan(assemble())["boxes"]
    for zoom in (1.0, 2.5, 7.0):
        scaled = [Box(b.x * zoom, b.y * zoom, b.w * zoom, b.h * zoom) for b in boxes]
        for i, first in enumerate(scaled):
            for j in range(i + 1, len(scaled)):
                assert not first.intersects(scaled[j]), f"{i} and {j} collide at {zoom}x"


def test_every_label_fits_the_container_it_was_given() -> None:
    """The overflow bug in its general form: a string wider than the plate it
    sits on. Truncation is the only outcome that keeps the box a true statement,
    so the measured length is never allowed to exceed the reserved width."""
    for label in plan(assemble())["labels"]:
        assert label["length"] <= label["box"]["w"] + 0.01
        assert label["text"], "an empty label reserves space for nothing"


def test_a_room_is_a_place_with_things_in_it() -> None:
    """M22 drew rooms as outlined rectangles with three hairlines in them, which
    reads as a wireframe rather than as somewhere work happens."""
    layout = plan(assemble())
    for room in ROOMS:
        fixtures = layout["fixtures"][room.room_id]
        assert len(fixtures) >= 5, f"{room.room_id} has {len(fixtures)} fixtures"
        floor = layout["floors"][room.room_id]
        for fixture in fixtures:
            assert fixture["x"] >= floor["x"] - 0.01
            assert fixture["x"] + fixture["w"] <= floor["x"] + floor["w"] + 0.01
            assert fixture["y"] >= floor["y"] - 0.01
            assert fixture["y"] + fixture["h"] <= floor["y"] + floor["h"] + 0.01


def test_every_stationed_role_is_a_figure_big_enough_to_read() -> None:
    """A three-pixel dot is not an actor at a post. Each role also has to be
    identifiable without reading its label, which the drawing does with a
    distinct silhouette per role."""
    page = _built()
    layout = plan(assemble())
    seen: set[str] = set()
    for room in ROOMS:
        for sprite in layout["sprites"][room.room_id]:
            assert sprite["h"] >= 16.0, sprite
            seen.add(sprite["role"])
    assert seen == {role.value for role in Role}
    for role in Role:
        assert f"<title>{role.value}</title>" in page, role.value


def test_the_map_is_big_enough_to_draw_in() -> None:
    """The scale was the whole of M24's problem: at one plan unit to the pixel a
    room is twenty-six across and nothing fits inside it."""
    layout = plan(assemble())
    for shell in layout["shells"].values():
        assert shell["w"] >= 260 and shell["h"] >= 180, shell
    assert layout["world"]["w"] >= 2000
    assert layout["world"]["h"] >= 1200


def test_no_key_handed_to_the_template_shadows_a_dict_method() -> None:
    """Twice now a dict key named after a dict attribute has rendered as the
    attribute: ``entry.items`` printed ``<built-in method items>`` into a table
    in M22, and ``token.values`` printed one into an animation in M24. Jinja
    resolves an attribute before a key, so the collision is silent and the page
    still builds."""
    forbidden = {name for name in dir(dict) if not name.startswith("_")}
    station = assemble()

    def walk(node: object, where: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in forbidden, f"{where}.{key} shadows dict.{key}"
                walk(value, f"{where}.{key}")
        elif isinstance(node, list | tuple):
            for index, value in enumerate(node):
                walk(value, f"{where}[{index}]")

    layout = plan(station)
    walk(layout, "plan")
    walk(_moving(station, _arrivals(station, layout)), "moving")
    for occupied in station.occupancy:
        walk(occupied.detail, f"{occupied.room.room_id}.detail")


def test_the_exits_are_doors_in_the_outer_wall() -> None:
    """Cut into a wall that faces outward, chosen from the plan rather than
    named, so moving a room in the map moves its exits to the wall that still
    faces out. The counter plate goes outside the room, never over its floor."""
    layout = plan(assemble())
    shells = layout["shells"]
    for door in layout["doors"]:
        shell = shells[door["room_id"]]
        rect = door["rect"]
        assert rect["x"] >= shell["x"] - 1 and rect["y"] >= shell["y"] - 1
        assert rect["x"] + rect["w"] <= shell["x"] + shell["w"] + 1
        assert rect["y"] + rect["h"] <= shell["y"] + shell["h"] + 1
        plate = door["plate"]
        overlaps = (
            plate["x"] < shell["x"] + shell["w"]
            and shell["x"] < plate["x"] + plate["w"]
            and plate["y"] < shell["y"] + shell["h"]
            and shell["y"] < plate["y"] + plate["h"]
        )
        assert not overlaps, f"{door['state']}'s counter plate sits inside the room"


def test_an_unused_exit_says_it_meant_the_zero() -> None:
    """A row of zeroes reads as a broken renderer unless the drawing says it
    meant them. Every exit on this map is unused, and the map says so."""
    station = assemble()
    doors = plan(station)["doors"]
    assert doors
    page = _built()
    if not any(door["used"] for door in doors):
        assert UNUSED_EXITS.split("—")[0].strip() in page


def test_the_corridor_never_crosses_the_sealed_line() -> None:
    """The Vault and the Oracle have no corridor into them, which is the whole
    point of drawing them. The drawing must not quietly connect one."""
    layout = plan(assemble())
    reached = {point["room_id"] for point in layout["waypoints"]}
    assert "vault" not in reached and "oracle" not in reached
    for room_id in ("vault", "oracle"):
        assert layout["doorways"][room_id] == [], f"{room_id} has a doorway cut into it"


def test_a_token_swells_where_it_arrives_rather_than_on_a_beat() -> None:
    """The pulse keyframes are the fractions of the route at which it passes
    through a room its arm engages, so the animation marks an arrival rather
    than a rhythm somebody chose."""
    station = assemble()
    layout = plan(station)
    arrivals = _arrivals(station, layout)
    assert len(arrivals) == len(station.arm_route)
    assert arrivals[0] == 0.0
    assert arrivals[-1] == pytest.approx(1.0)
    moving = _moving(station, arrivals)
    assert moving
    for token in moving:
        times = [float(t) for t in token["key_times"].split(";")]
        assert times == sorted(times)
        assert times[0] == 0.0
        assert times[-1] == pytest.approx(1.0)
        assert len(times) == len(token["pulse"].split(";"))


# ------------------------------------------------------- M25: a map, not a plan


def _points(path: str) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for a, b in re.findall(r"[ML]\s*(-?[\d.]+)\s+(-?[\d.]+)", path)]


def test_every_room_is_named_by_a_card_beside_it() -> None:
    """The room's label moved off its floor and onto a callout, which is what
    let the interior be an interior. Every room gets exactly one, it carries the
    room's own name, and it never sits over a room."""
    layout = plan(assemble())
    cards = {card["room_id"]: card for card in layout["cards"]}
    assert set(cards) == {room.room_id for room in ROOMS}

    for room in ROOMS:
        card = cards[room.room_id]
        box = card["box"]
        named = [
            label["text"]
            for label in layout["labels"]
            if label["owner"] == room.room_id and label["text"] == room.name.upper()
        ]
        assert named, f"{room.room_id}'s card does not carry its name"
        for shell in layout["shells"].values():
            over = (
                box["x"] < shell["x"] + shell["w"]
                and shell["x"] < box["x"] + box["w"]
                and box["y"] < shell["y"] + shell["h"]
                and shell["y"] < box["y"] + box["h"]
            )
            assert not over, f"{room.room_id}'s card sits over a room"


def test_a_card_stays_on_the_map_and_beside_its_own_room() -> None:
    """A slot off the edge of the viewBox is not a slot, and a card three rooms
    from the thing it names is not a label. Both were produced by an earlier
    search and both are checked here."""
    layout = plan(assemble())
    world = layout["world"]
    for card in layout["cards"]:
        box, shell = card["box"], layout["shells"][card["room_id"]]
        assert box["x"] >= world["x"]
        assert box["y"] >= world["y"]
        assert box["x"] + box["w"] <= world["x"] + world["w"]
        assert box["y"] + box["h"] <= world["y"] + world["h"]
        gap = max(
            shell["x"] - (box["x"] + box["w"]),
            box["x"] - (shell["x"] + shell["w"]),
            shell["y"] - (box["y"] + box["h"]),
            box["y"] - (shell["y"] + shell["h"]),
        )
        assert gap < 340, f"{card['room_id']}'s card is {gap:.0f} from its room"


def test_a_card_says_what_the_room_is_doing() -> None:
    """The status word is read off the assembled record — a locked room is one
    whose feature is unbuilt, a sealed one has no corridor into it, an idle one
    is a room the arm on display does not engage."""
    station = assemble()
    cards = {card["room_id"]: card for card in plan(station)["cards"]}
    assert {card["status"] for card in cards.values()} <= {
        "WORKING",
        "IDLE",
        "LOCKED",
        "SEALED",
        "NO DATA",
    }
    for occupied in station.occupancy:
        status = cards[occupied.room.room_id]["status"]
        if occupied.room.locked:
            assert status == "LOCKED"
        elif not occupied.engaged:
            assert status in ("IDLE", "SEALED")
        elif occupied.backing is Backing.EMPTY:
            assert status == "NO DATA"
        else:
            assert status == "WORKING"


def test_every_agent_works_inside_its_own_room() -> None:
    """Agents work. They do not work somewhere else: the token is what moves
    through the station, and a role in the corridor would say something about
    the architecture that is not true."""
    layout = plan(assemble())
    walkers = 0
    for room in ROOMS:
        chamber = layout["chambers"][room.room_id]
        for sprite in layout["sprites"][room.room_id]:
            stations = sprite["stations"]
            assert len(stations) >= 2, sprite
            assert stations == sorted(stations), sprite
            assert stations[-1] - stations[0] >= 46.0, "this one is shuffling on the spot"
            for station in stations:
                assert chamber["x"] <= station <= chamber["x"] + chamber["w"], sprite["role"]
            assert sprite["s0"] == 0.0
            assert sprite["s1"] == pytest.approx(stations[1] - stations[0], abs=0.02)
            assert float(sprite["dur"]) > 0
            walkers += 1
    assert walkers == sum(len(room.roles) for room in ROOMS)


def test_two_agents_in_a_room_do_not_share_the_same_floor() -> None:
    """Both of Screening's actors used to walk the same lane and draw as one
    shape. They are given separate stretches of floor, and the stations they
    work at come out of their own stretch."""
    layout = plan(assemble())
    for room in ROOMS:
        runs = [
            (sprite["stations"][0], sprite["stations"][-1])
            for sprite in layout["sprites"][room.room_id]
        ]
        for i, (a0, a1) in enumerate(runs):
            for b0, b1 in runs[i + 1 :]:
                assert a1 < b0 or b1 < a0, f"{room.room_id}: two actors cross"


def test_the_dossier_holds_a_panel_for_every_room() -> None:
    """Clicking a room opens its dashboard over the map. The panel has to be
    there for every room, including the ones that are locked or empty."""
    page = _built()
    for room in ROOMS:
        assert f'id="panel-{room.room_id}"' in page, room.room_id
        assert f'data-room="{room.room_id}"' in page, room.room_id
    assert 'id="dossier"' in page
    assert 'id="roster"' in page


# --------------------------------------------- M26: the page has to work offline


@lru_cache(maxsize=1)
def _built() -> str:
    """The rendered page, built once for the whole file.

    Thirty-two tests were each rendering it and writing 1.6MB into their own
    temporary directory, which had grown to be the slowest thing in the suite by
    an order of magnitude. Caching is safe for exactly the reason the page is
    worth having: it is deterministic, and there is a test above that says so."""
    return render_station()


def test_nothing_is_drawn_over_the_rooms_that_could_swallow_a_click() -> None:
    """Clicking a room did nothing at all, and the cause was a layer nobody
    thought of as a layer: the tokens are drawn after the rooms and carry a
    bloom filter whose region is far larger than the dots, so the whole map had
    an invisible sheet of glass over it.

    Every group drawn above the rooms that is not itself a target must decline
    hit testing. Checked in the markup, because the failure is silent — the page
    renders perfectly and simply does not respond.
    """
    page = write_station(Path("site/_m26.html")).read_text(encoding="utf-8")
    Path("site/_m26.html").unlink(missing_ok=True)
    for layer in ('<g id="tokens"', '<g id="exits"', '<g id="labels"', '<g class="roomfront"'):
        start = page.index(layer)
        opening = page[start : page.index(">", start)]
        assert 'pointer-events="none"' in opening, f"{layer} can intercept a click"


def test_the_script_only_reaches_for_things_the_markup_has() -> None:
    """A generated page whose script and markup disagree fails at the one moment
    nobody is watching: in a browser, silently. Every id the script looks up has
    to exist in the document it is shipped in."""
    page = _built()
    script = re.search(r"<script>\n\(function \(\).*?</script>", page, flags=re.S)
    assert script
    wanted = sorted(set(re.findall(r"getElementById\('([^']+)'\)", script.group(0))))
    assert wanted, "the script looks nothing up, which cannot be right"
    for name in wanted:
        assert f'id="{name}"' in page, f"the script wants #{name} and the page has no such thing"


def test_the_page_carries_every_arm_of_the_protocol_on_display() -> None:
    """The dossier's arm switch changes which recorded arm every panel describes,
    so the page has to carry all of them — and each has to be the same arm the
    ladder ran, not a summary of one."""
    station = assemble()
    page = _built()
    body = re.search(
        r'<script id="station-arms" type="application/json">(.*?)</script>', page, flags=re.S
    )
    assert body
    data = json.loads(body.group(1))

    ran = [run.arm.arm_id for run in station.chapter.runs]
    assert [arm["arm_id"] for arm in data["arms"]] == ran
    assert [room["room_id"] for room in data["rooms"]] == [r.room_id for r in ROOMS]

    for arm, run in zip(data["arms"], station.chapter.runs, strict=True):
        assert len(arm["items"]) == len(run.outcomes)
        assert set(arm["rooms"]) == {room.room_id for room in ROOMS}
        assert arm["engaged"] == sorted(engaged_rooms(run.arm))
        assert arm["route"], "every arm needs a path, including the ones that walk no rooms"


def test_an_arms_figures_are_the_ones_that_arm_actually_produced() -> None:
    """The switch shows what the record says about that arm. Assembled per arm
    through the same door the drawn one came through, so it cannot be a
    reconstruction that has drifted."""
    station = assemble()
    records = {arm["arm_id"]: arm for arm in arm_records(station)}
    for run in station.chapter.runs:
        alone = assemble(protocol=station.chapter.version, arm_id=run.arm.arm_id)
        record = records[run.arm.arm_id]
        for occupied in alone.occupancy:
            mine = record["rooms"][occupied.room.room_id]
            assert mine["backing"] == occupied.backing.value
            assert [f["value"] for f in mine["figures"]] == [f.value for f in occupied.figures]


def test_the_item_rows_are_the_recorded_outcomes() -> None:
    """The items table is the arm's own record, one row per bank item per pass.
    Nothing is summarised on the way in."""
    station = assemble()
    records = {arm["arm_id"]: arm for arm in arm_records(station)}
    for run in station.chapter.runs:
        rows = records[run.arm.arm_id]["items"]
        assert len(rows) == len(run.outcomes)
        assert len(records[run.arm.arm_id]["columns"]) == len(ITEM_COLUMNS)
        for row, outcome in zip(rows, run.outcomes, strict=True):
            assert len(row) == len(ITEM_COLUMNS)
            assert row[0] == outcome.item_id
            assert row[1] == outcome.verdict.value
            assert row[2] == outcome.truth_verdict.value
            assert row[3] in ("right", "wrong", "abstained", "halted")
            assert (row[3] == "right") == outcome.correct


def test_every_role_is_drawn_as_its_own_build() -> None:
    """A figure has to be identifiable without reading its label, which means no
    two roles may share a silhouette. Checked by drawing each on its own and
    requiring the markup to differ."""
    from nullius.station.render import environment

    template = environment().get_template("agents.html")
    drawn = {
        role.value: template.module.build(role.value, 74.0, "#ffffff")  # type: ignore[attr-defined]
        for role in Role
    }
    for role, markup in drawn.items():
        assert markup.strip(), role
    assert len({str(markup) for markup in drawn.values()}) == len(Role), (
        "two roles are drawn the same way"
    )
    page = _built()
    for role in Role:
        assert f"<title>{role.value}</title>" in page, role.value


# ------------------------------------------ M28: a room made of its own things


def _kinds(kit: object) -> set[str]:
    rows = (kit.wall, kit.back, kit.mid, kit.front, kit.props)  # type: ignore[attr-defined]
    return {kind for row in rows for kind in row}


def test_every_thing_a_room_asks_for_is_drawn_as_that_thing() -> None:
    """A kit naming a kind the fixture macro has no branch for falls through to
    the fallback rectangle, which is a grey box that looks like furniture from a
    distance. That is exactly how the station came to be twelve rooms of the
    same object at different proportions."""
    source = Path("src/nullius/station/templates/parts.html").read_text(encoding="utf-8")
    drawn = set(re.findall(r"f\.kind == '([a-z]+)'", source))
    asked = set().union(*(_kinds(kit) for kit in [*FURNITURE.values(), DEFAULT_KIT]))
    assert asked <= drawn, f"drawn as a plain box: {sorted(asked - drawn)}"
    assert asked <= set(TONES), f"made of nothing: {sorted(asked - set(TONES))}"


def test_no_two_rooms_are_furnished_the_same_way() -> None:
    """Fourteen rooms of racks and cabinets say the fourteen departments do the
    same work, which is the one thing the map exists to deny."""
    kits = {room_id: _kinds(kit) for room_id, kit in FURNITURE.items()}
    assert set(kits) == {room.room_id for room in ROOMS}
    for room_id, kinds in kits.items():
        others = set().union(*(other for name, other in kits.items() if name != room_id))
        assert kinds - others, f"{room_id} owns nothing nobody else has"


def test_a_room_is_not_drawn_in_one_colour() -> None:
    """M27's rooms were two greys and the room's accent, which reads as a plan
    rather than as a place. A room has to be made of more than one material and
    to hold at least one light that is not its own hue."""
    layout = plan(assemble())
    for room in ROOMS:
        fixtures = layout["fixtures"][room.room_id]
        assert len({TONES[fixture["kind"]] for fixture in fixtures}) >= 3, room.room_id
        assert len({fixture["glow"] for fixture in fixtures}) >= 2, room.room_id
        for fixture in fixtures:
            assert fixture["body"].startswith("var(--"), fixture
            assert fixture["shade"] != fixture["body"], fixture


def test_each_run_of_corridor_knows_which_run_it_is() -> None:
    """Every hallway drew the same rivets, the same arrows and the same number,
    because none of them knew where it was in the walk."""
    halls = plan(assemble())["halls"]
    assert len(halls) >= 8
    assert [hall["index"] for hall in halls] == list(range(1, len(halls) + 1))


# ------------------------------------------- M29: a map with nothing written on it


def test_the_map_carries_no_writing_until_it_is_asked_for() -> None:
    """At rest the drawing is the drawing: rooms, fittings, people. The callouts,
    the captions, the counter plates and the roster are a second drawing on top
    of the first, and they go on when they are asked for."""
    page = _built()
    style = page[page.index("<style>") : page.index("</style>")]
    for rule in (
        "#cards, #exits { opacity:0;",
        "#labels text { opacity:0;",
        ".roster, .hud-tl, .hud-bl { opacity:0;",
    ):
        assert rule in style, rule
    for rule in (
        ".annotated #cards, .annotated #exits { opacity:1; }",
        '.annotated #labels text, #labels text[data-kind="stencil"] { opacity:1; }',
        ".annotated .roster, .annotated .hud-tl, .annotated .hud-bl { opacity:1; }",
    ):
        assert rule in style, rule
    # the number painted on the floor is part of the room, not writing about it
    assert 'data-kind="stencil"' in page


def test_hovering_a_room_is_what_names_it() -> None:
    """With the callouts off, the only thing that says which room this is is the
    peek — so every hook the script fills has to be in the markup, and it has to
    be fed from the same record the dossier reads."""
    page = _built()
    assert 'id="peek"' in page
    for hook in (
        "peek-no",
        "peek-name",
        "peek-roles",
        "peek-backing",
        "peek-status",
        "peek-cue",
    ):
        assert f'"{hook}"' in page, hook
    script = page[page.rindex("<script>") : page.rindex("</script>")]
    assert "function showPeek(" in script
    assert "ARMS[arm].rooms[id]" in script, "the peek is not reading the arm on display"


def test_the_bare_map_is_framed_to_the_building() -> None:
    """The world is sized for the callouts and the counter plates in its margins.
    Fitting to it with those hidden frames a great deal of empty ground."""
    layout = plan(assemble())
    bounds, world = layout["bounds"], layout["world"]
    assert bounds["w"] < world["w"] and bounds["h"] < world["h"]
    assert bounds["x"] >= world["x"] and bounds["y"] >= world["y"]
    assert bounds["x"] + bounds["w"] <= world["x"] + world["w"]
    for shell in layout["shells"].values():
        assert shell["x"] >= bounds["x"] - 0.01
        assert shell["x"] + shell["w"] <= bounds["x"] + bounds["w"] + 0.01
        assert shell["y"] >= bounds["y"] - 0.01
        assert shell["y"] + shell["h"] <= bounds["y"] + bounds["h"] + 0.01
    script = _built()
    assert "BOUNDS = { x:" in script


def test_every_room_is_named_as_a_place() -> None:
    """The map is a building. A department called Analysis is an activity; a
    room called the Analysis Room is somewhere you can stand."""
    keep = {"vault", "oracle"}
    for room in ROOMS:
        if room.room_id in keep:
            continue
        assert room.name.split()[-1] in {
            "Room",
            "Workshop",
            "Floor",
            "Chamber",
        }, f"{room.room_id} is not named for a place: {room.name}"
    page = _built()
    for room in ROOMS:
        assert room.name in page, room.name


# ------------------------------------------- M30: things that stand on the floor


def test_everything_in_a_room_is_a_solid_standing_on_the_floor() -> None:
    """M30 gave every fixture a height. M31 turned the camera on its side, so
    the height is now the whole of what tells a wall of filing drawers from a
    stool -- and what used to be the top face is a sliver of cap."""
    layout = plan(assemble())
    for room in ROOMS:
        for fixture in layout["fixtures"][room.room_id]:
            kind = str(fixture["kind"])
            assert kind in HEIGHTS, f"{kind} has no cap"
            assert 0.0 < fixture["z"] < fixture["h"], fixture
            expected = round(float(fixture["h"]) * (1.0 - HEIGHTS[kind]), 2)
            assert fixture["z"] == expected, fixture
    # anything lying flat on the deck is nearly all cap, and nothing else is
    flat = {kind for kind, cap in HEIGHTS.items() if cap > 0.6}
    assert flat == {"hatch", "cables"}


def test_a_solid_is_drawn_inside_the_box_it_was_given() -> None:
    """A thing that stands up must not stand into the row behind it. The top
    face sits on top of the front face and the two together are the box the row
    allotted, so the height costs the drawing nothing in floor space."""
    layout = plan(assemble())
    for room in ROOMS:
        floor = layout["floors"][room.room_id]
        for fixture in layout["fixtures"][room.room_id]:
            top_face = fixture["h"] - fixture["z"]
            assert top_face > 0, fixture
            assert fixture["y"] + top_face + fixture["z"] <= floor["y"] + floor["h"] + 0.01


def test_the_light_falls_the_same_way_on_everything() -> None:
    """One lighting language, applied once: a top face lit from above, a front
    face in its own shadow, and a shadow on the floor. If a fixture stops going
    through it the room goes back to being a plan of a room."""
    source = Path("src/nullius/station/templates/parts.html").read_text(encoding="utf-8")
    for helper in ("macro topface(", "macro frontface(", "macro solid(", "macro drum("):
        assert helper in source, helper
    assert source.count("url(#topV)") >= 2
    assert source.count("url(#faceV)") >= 2
    page = _built()
    for gradient in ('id="topV"', 'id="faceV"', 'id="ink"'):
        assert gradient in page, gradient
    # the specular pass that used to stand in for shading is gone: the faces do
    # it now, and doing both turned every material milky
    assert "feSpecularLighting" not in page


# ---------------------------------------------- M31: the room from the side


def test_everything_stands_on_the_ground_line_or_hangs_above_it() -> None:
    """A cutaway has one axis left, which is up. A thing either stands on the
    floor of its chamber or is fixed above it, and there is no third case --
    the four bands of depth the top-down map laid its furniture out in are
    gone along with the point of view that needed them."""
    layout = plan(assemble())
    for room in ROOMS:
        ground = layout["grounds"][room.room_id]
        chamber = layout["chambers"][room.room_id]
        standing = 0
        for fixture in layout["fixtures"][room.room_id]:
            kind = str(fixture["kind"])
            base = fixture["y"] + fixture["h"]
            if kind in MOUNTED:
                assert base < ground - 1.0, f"{kind} in {room.room_id} is on the floor"
            else:
                assert abs(base - ground) < 0.02, f"{kind} in {room.room_id} floats"
                standing += 1
            assert fixture["x"] >= chamber["x"] - 0.02
            assert fixture["x"] + fixture["w"] <= chamber["x"] + chamber["w"] + 0.02
            assert fixture["y"] >= chamber["y"] - 0.02
        assert standing >= 4, f"{room.room_id} has almost nothing on its floor"


def test_how_tall_a_thing_is_is_what_says_what_it_is() -> None:
    """Seen from the side there is no other cue. A wall of filing drawers and a
    stool drawn the same height are the same object."""
    layout = plan(assemble())
    for room in ROOMS:
        chamber = layout["chambers"][room.room_id]
        heights = {
            str(f["kind"]): f["h"]
            for f in layout["fixtures"][room.room_id]
            if str(f["kind"]) not in MOUNTED
        }
        assert len(set(heights.values())) >= 3, f"{room.room_id} is one height throughout"
        for kind, height in heights.items():
            assert height == pytest.approx(chamber["h"] * STANDS[kind], abs=0.02)
    assert STANDS["filewall"] > STANDS["stool"] * 2


def test_the_people_stand_on_the_same_floor_as_the_furniture() -> None:
    """Two agents used to be given different lanes so they would not draw as
    one shape. There are no lanes now: there is one floor, and anybody in the
    room is standing on it."""
    layout = plan(assemble())
    for room in ROOMS:
        ground = layout["grounds"][room.room_id]
        for sprite in layout["sprites"][room.room_id]:
            assert sprite["y"] == pytest.approx(ground, abs=0.02), sprite["role"]


def test_the_space_between_the_rooms_is_the_building() -> None:
    """Fourteen lit boxes floating in black says the departments are all there
    is. The plant that lights and cools the place records nothing, so it is
    drawn as structure: no number on it, and nothing on it to click."""
    layout = plan(assemble())
    works = layout["works"]
    assert {str(w["kind"]) for w in works} >= {"deck", "hall", "spine", "run"}
    shells = list(layout["shells"].values())
    for work in works:
        for shell in shells:
            overlap_x = min(work["x"] + work["w"], shell["x"] + shell["w"]) - max(
                work["x"], shell["x"]
            )
            overlap_y = min(work["y"] + work["h"], shell["y"] + shell["h"]) - max(
                work["y"], shell["y"]
            )
            assert overlap_x <= 0.02 or overlap_y <= 0.02, (work["kind"], shell)
    page = _built()
    start = page.index('<g id="works"')
    block = page[start : page.index('<g id="halls"')]
    assert 'pointer-events="none"' in page[start : page.index(">", start)]
    assert "<text" not in block, "the plant is stating something"
    assert "data-room" not in block, "the plant can be clicked"


# ------------------------------------------------ M32: an actor with somewhere to be


def test_an_actor_works_at_something_that_is_actually_there() -> None:
    """An actor pacing between two arbitrary points is a gif. The stations are
    the room's own fixtures, so the route is the room's own layout -- and if a
    stretch of floor has nothing to work at, the actor walks it rather than
    miming at a spot where there is nothing."""
    layout = plan(assemble())
    posted, total = 0, 0
    for room in ROOMS:
        middles = [
            round(fixture["x"] + fixture["w"] / 2, 2)
            for fixture in layout["fixtures"][room.room_id]
            if str(fixture["kind"]) in WORKABLE
        ]
        for sprite in layout["sprites"][room.room_id]:
            total += 1
            at = [station in middles for station in sprite["stations"]]
            assert all(at) or not any(at), f"{sprite['role']} is half at a station"
            posted += 1 if all(at) else 0
    assert total >= 12
    assert posted * 3 >= total * 2, f"only {posted} of {total} actors have a post"


def test_an_actor_in_a_room_this_arm_does_not_engage_stands_still() -> None:
    """The animation has to mean something or it is decoration. What it means
    is that this arm engages this room -- so switching arms changes who is
    working, and an actor in a room the arm leaves out stands at its post."""
    page = _built()
    style = page[page.index("<style>") : page.index("</style>")]
    assert ".roomfront.resting .agent .pace" in style
    assert "animation:none" in style[style.index(".roomfront.resting") :]
    script = page[page.rindex("<script>") : page.rindex("</script>")]
    assert "classList.toggle('resting', !on)" in script
    # and the figure is placed at its first station, so standing still is
    # standing at a post rather than stopped halfway across the floor
    layout = plan(assemble())
    for room in ROOMS:
        for sprite in layout["sprites"][room.room_id]:
            assert sprite["x"] == sprite["stations"][0]
            assert sprite["s0"] == 0.0


def test_every_actor_says_who_it_is() -> None:
    """Fourteen rooms of figures with no names is a diagram of a workforce."""
    page = _built()
    layout = plan(assemble())
    for room in ROOMS:
        for sprite in layout["sprites"][room.room_id]:
            assert sprite["label"] == sprite["role"].upper()
            assert f">{sprite['label']}</text>" in page, sprite["role"]
    assert page.count('<g class="nameplate"') == sum(len(room.roles) for room in ROOMS)


# --------------------------------------------- M33: the department, in plain words


def test_every_department_is_explained_in_plain_words() -> None:
    """A charter is exact and is no use to somebody who has just clicked on a
    room. Every department carries a brief written for a reader who does not
    know what a preregistration is."""
    assert set(DEPTS) == {room.room_id for room in ROOMS}
    assert set(PEOPLE) == {role.value for role in Role}
    for room in ROOMS:
        dept = DEPTS[room.room_id]
        assert len(dept.steps) >= 2, room.room_id
        assert dept.plain.endswith(".") and dept.next_up.endswith(".")
        for step in dept.steps:
            assert step.endswith("."), (room.room_id, step)
    for role, (title, plain) in PEOPLE.items():
        assert title == title.lower(), role
        assert len(plain) > 80, role


def test_the_plain_words_never_state_a_figure() -> None:
    """This module is the page's one piece of hand-written prose about what the
    institution *is*, and the rule that keeps that exception safe is that it may
    not say anything about what the institution *did*. Every quantity on a brief
    is filled in from the record by the page."""
    assert numerals() == []


def test_every_department_has_a_tab_that_is_its_own() -> None:
    """More than a house style applied fourteen times: each room gets the tab
    for the thing that room, and no other, is for."""
    tabs = [desk.tab for dept in DEPTS.values() for desk in dept.desks]
    assert len(set(tabs)) == len(tabs), "two departments claim the same tab"
    page = write_station(Path("site/_m33.html")).read_text(encoding="utf-8")
    Path("site/_m33.html").unlink(missing_ok=True)
    script = page[page.rindex("<script>") : page.rindex("</script>")]
    listed = set(re.findall(r"\['([a-z]+)', '", script[script.index("var TABS") :]))
    for room in ROOMS:
        start = page.index(f'id="panel-{room.room_id}"')
        nxt = page.find('id="panel-', start + 10)
        panel = page[start : nxt if nxt > 0 else len(page)]
        desks = DEPTS[room.room_id].desks
        assert len(desks) >= 2, f"{room.room_id} has only one tab of its own"
        for desk in desks:
            assert desk.tab in listed, f"{desk.tab} is not in the tab strip"
            assert f'data-tab="{desk.tab}"' in panel, f"{room.room_id} has no {desk.tab} section"
            assert len(desk.sections) >= 2, desk.tab


def test_a_dossier_opens_on_the_brief() -> None:
    """Landing anywhere else means the reader's last click decides what the next
    department appears to be about."""
    page = _built()
    script = page[page.rindex("<script>") : page.rindex("</script>")]
    assert "var room = null, tab = 'brief'" in script
    opener = script[script.index("function open(id)") :]
    opener = opener[: opener.index("renderDossier();")]
    assert "tab = 'brief';" in opener
    assert "tab = 'overview'" not in opener
    assert script.index("['brief', 'the brief']") < script.index("['overview'")


def test_the_exact_rule_is_always_one_click_from_the_plain_one() -> None:
    """Plain language is a summary and a summary loses things, so the wording the
    rest of the project is written against is never more than one click away."""
    page = _built()
    assert page.count('<details class="exact">') == len(ROOMS)
    for room in ROOMS:
        assert html.escape(room.charter, quote=False) in page or room.charter in page


def test_the_brief_fills_its_own_numbers_from_the_record() -> None:
    """The prose says what a department is for; the page says what it did. The
    join between them is the only place a figure can enter a brief."""
    page = _built()
    script = page[page.rindex("<script>") : page.rindex("</script>")]
    assert "function fillBrief(" in script
    for hook in ("[data-now-line]", "[data-now]", "[data-done]"):
        assert hook in script, hook
        assert hook.strip("[]").replace("data-", "data-") in page
    body = script[script.index("function fillBrief(") : script.index("function fillNotes(")]
    assert "r.figures.forEach" in body, "the brief is not reading the arm's figures"
    assert "r.status" in body and "r.backing" in body and "r.engaged" in body


# ------------------------------------------------ M34: the room you arrive in


def test_the_station_has_one_room_you_arrive_in() -> None:
    """Fourteen departments and no front door meant opening the map and being
    given a pipeline to guess your way along. The hub is first, it is the
    largest thing on the plan, and it is on no route -- nothing passes through
    it, because it is not a stage of the research."""
    control = room_named("control")
    assert ROOMS[0] is control, "the room you arrive in is not the first one"
    assert control.wing is not Wing.PIPELINE
    assert control not in corridor()

    area = control.w * control.h
    for room in ROOMS:
        if room is control:
            continue
        assert room.w * room.h < area, f"{room.room_id} is as big as the hub"

    # it owns no state of the research machine, so it cannot be mistaken for a
    # stage, and adding it did not take a state off anybody
    assert control.states == ()
    assert unrepresented_states() == frozenset()
    assert unrepresented_roles() == frozenset()


def test_the_hub_is_joined_to_everything_it_reports_on() -> None:
    """It reads as the centre because it is joined to the building, not because
    it is drawn in the middle. Every room that can be walked to has a corridor
    from this one; the two sealed rooms have none, and neither does anything
    else."""
    layout = plan(assemble())
    spurs = layout["spurs"]
    hub = layout["shells"]["control"]
    reached: set[str] = set()
    for room in ROOMS:
        if room.room_id == "control":
            continue
        shell = layout["shells"][room.room_id]
        middle = shell["x"] + shell["w"] / 2
        for spur in spurs:
            if spur["vertical"] and abs(spur["x"] + spur["w"] / 2 - middle) < 1.0:
                reached.add(room.room_id)
    walkable = {r.room_id for r in ROOMS if r.wing is not Wing.SEALED and r.room_id != "control"}
    # the four above and the four below; the rest are served by the run east
    assert len(reached) >= 8, sorted(reached)
    assert reached <= walkable, "a spur reaches into a sealed room"
    east = [spur for spur in spurs if not spur["vertical"]]
    assert len(east) == 1
    assert east[0]["x"] == pytest.approx(hub["x"] + hub["w"], abs=0.02)


def test_no_two_departments_are_set_the_same_way(tmp_path: Path) -> None:
    """One stylesheet applied fifteen times meant reading the title to know
    where you were. A sheet is set in its department's face, in its hue, in one
    of five frames."""
    assert set(SKINS) == {room.room_id for room in ROOMS}
    faces = {face for face, _ in SKINS.values()}
    frames = {frame for _, frame in SKINS.values()}
    assert len(faces) >= 5, faces
    assert len(frames) >= 4, frames
    for face in faces:
        shared = [r for r, (f, _) in SKINS.items() if f == face]
        assert len(shared) <= 4, f"{face} is doing too much work: {shared}"
    page = _built()
    style = page[page.index("<style>") : page.index("</style>")]
    for frame in frames:
        assert f".frame-{frame} " in style, frame
    # and the hue reaches the sheet rather than stopping at the swatch
    script = page[page.rindex("<script>") : page.rindex("</script>")]
    assert "sheet.style.setProperty('--room', m.accent_hex)" in script
    assert "sheet.style.setProperty('--face', m.face)" in script


def test_the_hub_reports_on_the_whole_institution() -> None:
    """Its brief is the only one whose subject is the project rather than a
    department, so its figures are counted across the map and the record rather
    than read off one room."""
    page = _built()
    script = page[page.rindex("<script>") : page.rindex("</script>")]
    body = script[script.index("function fillHub(") : script.index("function fillBrief(")]
    for source in ("ROOMS.length", "ARMS.length", "a.items.length", "a.metrics.forEach"):
        assert source in body, source
    assert "backing === 'unbuilt'" in body, "the hub does not count what is missing"
    assert "open(m.room_id)" in body, "the hub does not point anywhere"
    assert '<div class="jump" data-jump>' in page
    # only the hub carries them
    assert page.count('<div class="grid" data-hub>') == 1
    assert page.count('<div class="jump" data-jump>') == 1


# ------------------------------------ M35: the facility does something, and says what


def test_every_room_says_what_it_is_doing_on_its_own_wall() -> None:
    """The map has always known what each room was doing -- it was on the callout
    card, which M29 took off the map along with all the other writing. A facility
    whose only sign of trouble is a card you have to switch on is a facility that
    always looks like it is going well."""
    station = assemble()
    layout = plan(station)
    boards = layout["boards"]
    assert set(boards) == {room.room_id for room in ROOMS}
    words = set()
    for occupied in station.occupancy:
        room_id = occupied.room.room_id
        board = boards[room_id]
        chamber = layout["chambers"][room_id]
        assert board["x"] >= chamber["x"] - 0.01
        assert board["x"] + board["w"] <= chamber["x"] + chamber["w"] + 0.01
        assert board["y"] >= chamber["y"] - 0.01
        assert board["backing"] == occupied.backing.value
        words.add(board["text"])
    # the facility is not uniformly fine, and the wall says so
    assert {"WORKING", "LOCKED"} <= words, words
    assert len(words) >= 4, words


def test_the_wall_plates_and_the_dossier_never_disagree(tmp_path: Path) -> None:
    """One call decides what a room is doing. The plate on the wall and the word
    in the dossier are the same string from the same place, and the arm switch
    moves both."""
    page = _built()
    script = page[page.rindex("<script>") : page.rindex("</script>")]
    assert "querySelectorAll('[data-board]')" in script
    assert "t.textContent = r.status" in script
    assert "data-boardlamp" in script and "data-stagelamp" in script
    layout = plan(assemble())
    for room in ROOMS:
        if room.room_id == "control":
            continue
        assert f'data-board="{room.room_id}"' in page, room.room_id
        assert layout["boards"][room.room_id]["text"] in page


def test_the_hub_shows_the_walk_it_reports_on() -> None:
    """It reports on a pipeline and did not show one. One block per stage, in the
    order a hypothesis meets them, numbered as the rooms are numbered."""
    layout = plan(assemble())
    stages = layout["pipeline"]
    walk = [*corridor(), room_named("record")]
    assert [stage["room_id"] for stage in stages] == [room.room_id for room in walk]
    order = [room.room_id for room in ROOMS]
    for stage in stages:
        assert stage["no"] == f"{order.index(stage['room_id']) + 1:02d}"
    # it sits in the band between the boards on the wall and the tops of the
    # things standing on the floor, or it is drawn behind what it describes
    fixtures = layout["fixtures"]["control"]
    mounted = max(f["y"] + f["h"] for f in fixtures if str(f["kind"]) in MOUNTED)
    standing = min(f["y"] for f in fixtures if str(f["kind"]) not in MOUNTED)
    for stage in stages:
        assert stage["y"] >= mounted, "the walk is drawn over the wall boards"
        assert stage["y"] + stage["h"] <= standing, "the walk is drawn behind the furniture"


def test_an_actor_wears_the_number_of_the_room_it_works_in() -> None:
    """An actor and its department are one fact seen twice, not two things to
    remember."""
    layout = plan(assemble())
    order = [room.room_id for room in ROOMS]
    for room in ROOMS:
        for sprite in layout["sprites"][room.room_id]:
            assert sprite["no"] == f"{order.index(room.room_id) + 1:02d}"


def test_the_facility_moves_and_can_be_told_to_stop(tmp_path: Path) -> None:
    """Decoration is what a still picture has. A car in the shaft, packets in the
    corridors and screens that change are the building running -- and every one
    of them stops for the pause button and for a reader who has asked their
    machine for less motion."""
    page = _built()
    for moving in ('class="lift"', 'class="packet', 'class="cycle'):
        assert moving in page, moving
    style = page[page.index("<style>") : page.index("</style>")]
    for frames in ("@keyframes ride", "@keyframes send", "@keyframes sendx", "@keyframes flick"):
        assert frames in style, frames
    stopped = style[style.index(".stopped .agent .walk") :]
    stopped = stopped[: stopped.index("}")]
    for moving in (".stopped .lift", ".stopped .packet", ".stopped .cycle"):
        assert moving in stopped, moving
    reduced = style[style.index("prefers-reduced-motion") :]
    for moving in (".lift", ".packet", ".cycle"):
        assert moving in reduced[: reduced.index("animation:none !important")], moving
