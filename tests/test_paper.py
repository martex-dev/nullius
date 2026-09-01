"""M16: the paper must not be able to report a flattering subset.

The failure this guards against is ordinary and almost invisible: a project
runs several protocols, one produces the good result, and the write-up quietly
becomes about that one. These tests are about what the document is structurally
unable to do.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

from nullius.benchmark.protocol import PROTOCOL_VERSIONS
from nullius.paper.model import assemble, results_path
from nullius.paper.render import (
    FLAWS,
    LIMITATIONS,
    environment,
    render_findings,
    write_paper,
)


def _text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    stripped = re.sub(r"<style.*?</style>", "", raw, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", stripped))


def test_every_registered_protocol_appears() -> None:
    """Not the ones that went well. All of them, in registration order."""
    paper = assemble()
    assert [c.version for c in paper.chapters] == sorted(PROTOCOL_VERSIONS, key=int)


def test_a_protocol_registered_but_never_run_is_reported_as_such() -> None:
    """A plan with no result is part of the record too, and reporting it is what
    stops a registered-and-quietly-dropped protocol from vanishing.

    The path is derived from the version rather than looked up in a table. The
    table went stale the moment a sixth protocol was registered, which is the
    third time in this project that something keyed by protocol version was
    maintained beside the registry instead of computed from it.
    """
    paper = assemble()
    unrun = [c for c in paper.chapters if not c.was_run]
    for chapter in unrun:
        assert chapter.verdict == "registered, not yet run"
        assert results_path(chapter.version).exists() is False


def test_refuted_predictions_are_counted_and_shown(tmp_path: Path) -> None:
    """The project has been wrong more often than right, and the document says so
    in its own abstract rather than in a footnote."""
    paper = assemble()
    assert paper.predictions_refuted >= 2

    page = _text(write_paper(tmp_path / "paper.html", paper=paper))
    for chapter in paper.run_chapters:
        assert chapter.protocol.prediction[:60] in page
        assert chapter.verdict in page


def test_the_paper_names_the_provider_on_its_face(tmp_path: Path) -> None:
    """Every number was produced under a mock. A reader should not have to dig."""
    paper = assemble()
    page = _text(write_paper(tmp_path / "paper.html", paper=paper))
    assert paper.provider == "mock"
    assert "mock" in page.lower()


def test_assembling_refuses_inputs_that_do_not_check_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """A paper built from damaged inputs is worse than no paper, because it looks
    like evidence."""
    monkeypatch.setitem(
        PROTOCOL_VERSIONS,
        "99",
        {**PROTOCOL_VERSIONS["1"], "path": Path("benchmark/protocol.v99.lock.json")},
    )
    with pytest.raises(ValueError, match="do not check out"):
        assemble(strict=True)

    lenient = assemble(strict=False)
    assert any("not committed" in problem for problem in lenient.problems)


def test_a_paper_built_from_damaged_inputs_says_so_on_its_face(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        PROTOCOL_VERSIONS,
        "99",
        {**PROTOCOL_VERSIONS["1"], "path": Path("benchmark/protocol.v99.lock.json")},
    )
    page = _text(write_paper(tmp_path / "paper.html", strict=False))
    assert "do not check out" in page


def test_bank_difficulty_is_measured_from_the_locked_truths() -> None:
    """v2 exists because v1's metric could not express the contrasts being
    measured. Both facts are read off the truth locks, not asserted."""
    paper = assemble()
    v1, v2 = paper.banks
    assert (v1.n_items, v2.n_items) == (20, 60)
    assert v2.within_one_se > v1.within_one_se * 3
    assert v2.resolution < v1.resolution
    for bank in (v1, v2):
        assert bank.null_fraction == pytest.approx(0.45, abs=0.02)


def test_the_template_refuses_an_undefined_name() -> None:
    from jinja2 import UndefinedError

    with pytest.raises(UndefinedError):
        environment().from_string("{{ nothing_defines_this }}").render()


def test_nothing_is_left_unsubstituted(tmp_path: Path) -> None:
    raw = write_paper(tmp_path / "paper.html").read_text(encoding="utf-8")
    assert "{{" not in raw and "{%" not in raw


def test_the_prose_sections_are_the_only_hand_written_content() -> None:
    """Declared as data so they can be counted and checked in one place. Each
    flaw names the milestone whose commit records it."""
    assert len(FLAWS) == 6
    assert len(LIMITATIONS) == 5
    for flaw in FLAWS:
        assert flaw.title.endswith(".")
        assert re.search(r"\(M\d+\w*\)$", flaw.body), flaw.title


def test_every_flaw_is_about_a_protocol_that_exists() -> None:
    """A flaw list that outran the record would be the document inventing
    history in the one place it is allowed to use prose."""
    paper = assemble()
    assert len(FLAWS) >= len(paper.run_chapters)


# --------------------------------------------- the findings on the front door


def test_the_findings_render_deterministically() -> None:
    """CI regenerates FINDINGS.md and fails on any difference, so the render has
    to be a pure function of the committed inputs."""
    assert render_findings() == render_findings()


def test_the_committed_findings_are_the_generated_ones() -> None:
    """The repository's front door states results. If it can drift from them it
    will, and a project whose thesis is 'take nobody's word for it' cannot ask a
    reader to take its README's word for it.
    """
    committed = Path("FINDINGS.md")
    assert committed.exists(), "FINDINGS.md is not committed"
    assert committed.read_text(encoding="utf-8") == render_findings()


def test_the_findings_carry_every_protocol_and_every_refutation() -> None:
    paper = assemble()
    text = render_findings(paper)
    for chapter in paper.chapters:
        assert chapter.protocol.protocol_hash[:12] in text
        assert chapter.verdict in text
    assert "refuted" in text


def test_the_findings_name_the_provider_and_the_limitations() -> None:
    text = render_findings()
    assert "mock" in text
    for limitation in LIMITATIONS:
        assert limitation.split(".")[0][:50] in text


def test_the_readme_points_at_the_findings() -> None:
    """A README that states results without linking the generated record is the
    one place this project would be asking to be taken on trust."""
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "FINDINGS.md" in readme
