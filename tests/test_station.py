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
from pathlib import Path

import pytest

from nullius.benchmark.arms import LADDER_V6, Arm, ArmKind
from nullius.benchmark.protocol import PROTOCOL_VERSIONS
from nullius.benchmark.runner import mechanisms_for
from nullius.db.enums import TERMINAL_STATES, HypothesisState, Role, Verdict
from nullius.station.ledger import open_ledger
from nullius.station.map import (
    HANDLED_OUTSIDE_THE_KERNEL,
    PIPELINE_STATES,
    ROOMS,
    TERMINAL_DOORS,
    Backing,
    corridor,
    dead_switches,
    declared_switches,
    unread_switches,
    unrepresented_roles,
    unrepresented_states,
)
from nullius.station.model import Station, assemble, engaged_rooms, route_for
from nullius.station.render import (
    CANNOT_SHOW,
    PRINCIPLES,
    UNUSED_EXITS,
    Box,
    _arrivals,
    _moving,
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
    raw = write_station(tmp_path / "station.html").read_text(encoding="utf-8")
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


def test_the_page_is_one_file_that_works_offline(tmp_path: Path) -> None:
    """No CDN, no stylesheet, no remote image. It has to render from a clean
    clone with nothing but a browser."""
    raw = write_station(tmp_path / "station.html").read_text(encoding="utf-8")
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


def test_the_embedded_record_is_readable_json(tmp_path: Path) -> None:
    """The page carries its own record inline, so a reader can check a figure
    against the data the drawing was built from without leaving the file."""
    raw = write_station(tmp_path / "station.html").read_text(encoding="utf-8")
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


def test_every_stationed_role_is_a_figure_big_enough_to_read(tmp_path: Path) -> None:
    """A three-pixel dot is not an actor at a post. Each role also has to be
    identifiable without reading its label, which the drawing does with a
    distinct silhouette per role."""
    page = write_station(tmp_path / "station.html").read_text(encoding="utf-8")
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


def test_an_unused_exit_says_it_meant_the_zero(tmp_path: Path) -> None:
    """A row of zeroes reads as a broken renderer unless the drawing says it
    meant them. Every exit on this map is unused, and the map says so."""
    station = assemble()
    doors = plan(station)["doors"]
    assert doors
    page = write_station(tmp_path / "station.html").read_text(encoding="utf-8")
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


def test_every_agent_walks_a_patrol_inside_its_own_room() -> None:
    """Agents walk. They do not walk out: the token is what moves through the
    station, and a role in the corridor would say something about the
    architecture that is not true."""
    layout = plan(assemble())
    walkers = 0
    for room in ROOMS:
        floor = layout["floors"][room.room_id]
        for sprite in layout["sprites"][room.room_id]:
            points = _points(sprite["d"])
            assert len(points) >= 3, sprite
            assert points[0] == points[-1], "a patrol has to come back"
            for x, y in points:
                assert floor["x"] <= x <= floor["x"] + floor["w"], sprite["role"]
                assert floor["y"] <= y <= floor["y"] + floor["h"], sprite["role"]
            assert float(sprite["dur"]) > 0
            walkers += 1
    assert walkers == sum(len(room.roles) for room in ROOMS)


def test_two_agents_in_a_room_do_not_share_the_same_floor() -> None:
    """Both of Screening's actors walked the same lane and drew as one shape."""
    layout = plan(assemble())
    for room in ROOMS:
        spans = [
            (min(x for x, _ in _points(s["d"])), max(x for x, _ in _points(s["d"])))
            for s in layout["sprites"][room.room_id]
        ]
        for i, (a0, a1) in enumerate(spans):
            for b0, b1 in spans[i + 1 :]:
                assert a1 < b0 or b1 < a0, f"{room.room_id}: two patrols overlap"


def test_the_dossier_holds_a_panel_for_every_room(tmp_path: Path) -> None:
    """Clicking a room opens its dashboard over the map. The panel has to be
    there for every room, including the ones that are locked or empty."""
    page = write_station(tmp_path / "station.html").read_text(encoding="utf-8")
    for room in ROOMS:
        assert f'id="panel-{room.room_id}"' in page, room.room_id
        assert f'data-room="{room.room_id}"' in page, room.room_id
    assert 'id="dossier"' in page
    assert 'id="roster"' in page
