"""M11: the report must not be able to say more than the ledger supports.

A report is generated last and read by people who will not check it, so the
tests here are about what it is structurally unable to do — assert a
confidence it did not re-derive, render a page for evidence that does not
resolve without saying so, or exit zero on a compromised ledger.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from nullius.db.enums import (
    AssertionKind,
    ClaimConfidence,
    ComputedBy,
    EvidenceKind,
    ObjectionSeverity,
    ObjectionType,
    Polarity,
    Role,
    RunStatus,
    Split,
)
from nullius.db.tables import Claim
from nullius.errors import InvariantViolation
from nullius.report.model import build_dossier, build_overview
from nullius.report.render import environment, write_site
from nullius.repository import Repository
from nullius.store.cas import ContentStore
from tests.conftest import Scaffold

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}


class _Balanced(HTMLParser):
    """Enough of a parser to catch an unclosed tag in a template."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        if not self.stack:
            self.problems.append(f"</{tag}> with nothing open")
        elif self.stack[-1] != tag:
            self.problems.append(f"</{tag}> closed while <{self.stack[-1]}> was open")
            self.stack.pop()
        else:
            self.stack.pop()


def _check_html(text: str) -> None:
    assert "{{" not in text and "{%" not in text, "a template tag reached the output"
    parser = _Balanced()
    parser.feed(text)
    assert not parser.problems, parser.problems
    assert not parser.stack, f"unclosed: {parser.stack}"


def _a_claim(
    repo: Repository, scaffold: Scaffold, *, statement: str = "Pruning helps."
) -> uuid.UUID:
    hypothesis = repo.as_role(Role.THEORIST).create_hypothesis(
        program_id=scaffold.program_id,
        statement=statement,
        mechanism="Unstable features mislead the model.",
        primary_metric="macro_f1",
        direction="increase",
        mde=0.02,
        falsification_condition="The interval fails to exceed the claimed effect.",
        assumptions={"stated": []},
    )
    claim = repo.as_role(Role.ANALYST).create_claim(
        program_id=scaffold.program_id,
        statement=statement,
        kind=AssertionKind.INFERRED_CLAIM,
        hypothesis_id=hypothesis.hypothesis_id,
    )
    repo.commit()
    return claim.claim_id


# ------------------------------------------------------------- the read model


def test_the_report_recomputes_confidence_rather_than_reading_it(
    repo: Repository, scaffold: Scaffold
) -> None:
    """The whole point of M5's rubric was that no input is an opinion.

    So a claim whose stored level was raised by hand, without the ledger facts
    that would justify it, must come back disputed rather than displayed.
    """
    claim_id = _a_claim(repo, scaffold)
    stored = repo.session.get(Claim, claim_id)
    assert stored is not None
    stored.confidence = ClaimConfidence.WELL_SUPPORTED
    repo.session.flush()

    dossier = build_dossier(repo.session, claim_id)
    assert dossier.stored is ClaimConfidence.WELL_SUPPORTED
    assert dossier.recomputed.confidence is ClaimConfidence.SPECULATIVE
    assert dossier.confidence_disputed
    assert any("supports" in problem for problem in dossier.problems)


def test_a_claim_with_no_evidence_says_so(repo: Repository, scaffold: Scaffold) -> None:
    dossier = build_dossier(repo.session, _a_claim(repo, scaffold))
    assert any("no evidence" in problem for problem in dossier.problems)
    assert any("no registration" in problem for problem in dossier.problems)


def _run_with_artifact(
    repo: Repository, scaffold: Scaffold, hypothesis_id: uuid.UUID, digest: str
) -> None:
    """A registration and one completed run whose result names ``digest``."""
    registration = repo.as_role(Role.DESIGNER).register(
        hypothesis_id=hypothesis_id,
        spec={"baseline_arm": "full", "treatment_arm": "prune", "primary_metric": "macro_f1"},
        analysis_plan={"test": "paired_bootstrap"},
        seed_root=7,
        n_seeds=1,
        holdout_query_budget=3,
        program_id=scaffold.program_id,
    )
    bundle = repo.record_code_bundle(
        content_hash="a" * 64, validator_report={"ok": True}, passed=True
    )
    dataset = repo.record_dataset(
        name="synthetic", version="1", content_hash="b" * 64, licence="synthetic"
    )
    run = repo.start_run(
        registration_id=registration.registration_id,
        bundle_id=bundle.bundle_id,
        dataset_id=dataset.dataset_id,
        seed=11,
        environment_hash="c" * 64,
        image_digest="d" * 64,
        isolation_tier="subprocess",
        git_commit="e" * 40,
        program_id=scaffold.program_id,
    )
    # Only the Custodian may write a holdout metric, which the ledger enforces
    # rather than documents.
    repo.as_role(Role.CUSTODIAN).record_result(
        run_id=run.run_id,
        split=Split.HOLDOUT,
        metric="full.macro_f1",
        value=0.5,
        artifact_hash=digest,
        computed_by=ComputedBy.CUSTODIAN,
        program_id=scaffold.program_id,
    )
    repo.finish_run(run.run_id, status=RunStatus.COMPLETED, telemetry={})
    repo.commit()


def test_an_unresolvable_artifact_caps_the_claim_and_is_named(
    repo: Repository, scaffold: Scaffold, store: ContentStore
) -> None:
    """Provenance is a fact about the store, and the report checks it.

    Until M11 the kernel passed ``provenance_complete=True`` as a literal while
    the Custodian recorded holdout artifacts it never wrote, so this cap had
    never once fired anywhere in the system's life.
    """
    claim_id = _a_claim(repo, scaffold)
    claim = repo.session.get(Claim, claim_id)
    assert claim is not None and claim.hypothesis_id is not None
    missing = "f" * 64
    _run_with_artifact(repo, scaffold, claim.hypothesis_id, missing)

    dossier = build_dossier(repo.session, claim_id, store=store)
    assert dossier.unresolved_artifacts == (missing,)
    assert dossier.recomputed.inputs.provenance_complete is False
    assert any("do not resolve" in problem for problem in dossier.problems)


def test_an_artifact_that_is_in_the_store_resolves(
    repo: Repository, scaffold: Scaffold, store: ContentStore
) -> None:
    """The control for the test above: same path, artifact present."""
    claim_id = _a_claim(repo, scaffold)
    claim = repo.session.get(Claim, claim_id)
    assert claim is not None and claim.hypothesis_id is not None
    digest = store.put_json({"per_seed": {"11": {"full": {"macro_f1": 0.5}}}})
    _run_with_artifact(repo, scaffold, claim.hypothesis_id, digest)

    dossier = build_dossier(repo.session, claim_id, store=store)
    assert dossier.unresolved_artifacts == ()
    assert dossier.recomputed.inputs.provenance_complete is True


def test_a_missing_claim_raises_rather_than_rendering_an_empty_page(
    repo: Repository,
) -> None:
    with pytest.raises(KeyError):
        build_dossier(repo.session, uuid.uuid4())


def test_the_overview_leads_with_the_claims_it_cannot_support(
    repo: Repository, scaffold: Scaffold
) -> None:
    """A report that knows a claim is overstated and buries it is worse than
    one that never checked."""
    honest = _a_claim(repo, scaffold, statement="A modest claim about pruning.")
    inflated = _a_claim(repo, scaffold, statement="A grand claim about calibration.")
    row = repo.session.get(Claim, inflated)
    assert row is not None
    row.confidence = ClaimConfidence.SUPPORTED
    repo.session.flush()

    overview = build_overview(repo.session)
    disputed = {c.claim_id for c in overview.disputed_claims}
    assert inflated in disputed
    assert honest not in disputed


# ------------------------------------------------------------------ rendering


def test_the_template_refuses_an_undefined_name() -> None:
    """StrictUndefined, because a report is exactly where a silent blank is
    dangerous: a page that renders an empty cell for a field that no longer
    exists looks complete and is not."""
    from jinja2 import UndefinedError

    template = environment().from_string("{{ nothing_defines_this }}")
    with pytest.raises(UndefinedError):
        template.render()


def test_the_site_renders_valid_pages_with_nothing_left_unsubstituted(
    repo: Repository, scaffold: Scaffold, tmp_path: Path
) -> None:
    _a_claim(repo, scaffold, statement="Pruning helps under covariate shift.")
    site = write_site(repo.session, tmp_path / "site", database="test.sqlite")

    assert site.index.exists()
    assert len(site.claim_pages) == 1
    for page in (site.index, *site.claim_pages):
        _check_html(page.read_text(encoding="utf-8"))


def test_a_claim_page_carries_the_whole_case(
    repo: Repository, scaffold: Scaffold, tmp_path: Path
) -> None:
    """The acceptance criterion: why does the system believe this, in one page.

    One click from the index reaches it, and everything the answer needs is on
    it — what raised the confidence, what capped it, the facts underneath, the
    question it answers, and what was locked before it ran.
    """
    _a_claim(repo, scaffold, statement="Pruning helps under covariate shift.")
    site = write_site(repo.session, tmp_path / "site", database="test.sqlite")
    page = site.claim_pages[0].read_text(encoding="utf-8")

    for heading in (
        "Why the system believes it",
        "The question it answers",
        "What was locked before it ran",
        "The numbers the verdict was computed from",
        "Seeds that executed",
        "Independent replication",
        "Objections",
        "Forecasts, locked before execution",
    ):
        assert heading in page, heading

    assert "Pruning helps under covariate shift." in page
    assert "There is no field anywhere in this" in page

    index = site.index.read_text(encoding="utf-8")
    assert f"claims/{site.claim_pages[0].stem}.html" in index


def test_an_objection_and_its_discriminating_test_reach_the_page(
    repo: Repository, scaffold: Scaffold, tmp_path: Path
) -> None:
    """An objection with no discriminating test is refused at the write path,
    so every one the report shows names a test that could settle it."""
    claim_id = _a_claim(repo, scaffold)
    repo.as_role(Role.SKEPTIC).raise_objection(
        target_type="claims",
        target_id=claim_id,
        objection_type=ObjectionType.CONFOUND,
        severity=ObjectionSeverity.CRITICAL,
        statement="The arms differ in capacity, not only in the intervention.",
        discriminating_test={"action": "match_capacity", "expect": "the gap closes"},
        program_id=scaffold.program_id,
    )
    repo.commit()

    site = write_site(repo.session, tmp_path / "site", database="test.sqlite")
    page = site.claim_pages[0].read_text(encoding="utf-8")
    assert "The arms differ in capacity" in page
    assert "match_capacity" in page

    dossier = build_dossier(repo.session, claim_id)
    assert dossier.recomputed.confidence is ClaimConfidence.CONTESTED
    assert any("critical objection" in reason for reason in dossier.recomputed.capped_by)


def test_rebuilding_removes_a_page_for_a_claim_that_no_longer_exists(
    repo: Repository, scaffold: Scaffold, tmp_path: Path
) -> None:
    """Files persist, and a reader cannot tell a current page from a leftover."""
    claim_id = _a_claim(repo, scaffold)
    out = tmp_path / "site"
    first = write_site(repo.session, out, database="test.sqlite")
    stale = first.claim_pages[0]
    assert stale.exists()

    repo.session.execute(sa.delete(Claim).where(Claim.claim_id == claim_id))
    repo.session.flush()

    second = write_site(repo.session, out, database="test.sqlite")
    assert second.claim_pages == ()
    assert not stale.exists()


def test_the_site_reports_a_ledger_that_does_not_reconcile(
    repo: Repository, scaffold: Scaffold, tmp_path: Path, session: Session
) -> None:
    """A row written without an event must reach the front page.

    The reconciliation check already existed; what M11 adds is that a reader
    who never runs the CLI still sees it.
    """
    _a_claim(repo, scaffold)
    repo.commit()

    # A claim inserted straight into the table, with no event behind it.
    session.add(
        Claim(
            claim_id=uuid.uuid4(),
            program_id=scaffold.program_id,
            hypothesis_id=None,
            statement="Written behind the ledger's back.",
            kind=AssertionKind.INFERRED_CLAIM,
            confidence=ClaimConfidence.SPECULATIVE,
            computed_at=repo._clock.now(),
        )
    )
    session.flush()

    site = write_site(session, tmp_path / "site", database="test.sqlite")
    assert not site.integrity_ok
    assert "does not reconcile" in site.index.read_text(encoding="utf-8")


def test_evidence_pointing_at_nothing_is_refused_before_the_report_sees_it(
    repo: Repository, scaffold: Scaffold
) -> None:
    """The report never has to render unbacked evidence, because the write path
    will not accept it."""
    claim_id = _a_claim(repo, scaffold)
    with pytest.raises(InvariantViolation, match="must name its referent"):
        repo.as_role(Role.ANALYST).add_evidence(
            claim_id=claim_id,
            kind=EvidenceKind.EXPERIMENTAL,
            polarity=Polarity.SUPPORTS,
            strength={"difference": 0.06},
            program_id=scaffold.program_id,
        )


def test_the_analysis_behind_a_claim_reaches_the_dossier(
    repo: Repository, scaffold: Scaffold
) -> None:
    """The interval-to-effect ratio the rubric reads comes off the evidence row,
    not from anything the report computes for itself."""
    parent = _a_claim(repo, scaffold, statement="An earlier finding about pruning.")
    claim_id = _a_claim(repo, scaffold, statement="A claim derived from that one.")
    repo.as_role(Role.ANALYST).add_evidence(
        claim_id=claim_id,
        kind=EvidenceKind.DERIVED,
        polarity=Polarity.SUPPORTS,
        parent_claim_id=parent,
        strength={"difference": 0.06, "ci_low": 0.04, "ci_high": 0.08, "n_seeds": 5},
        program_id=scaffold.program_id,
    )
    repo.commit()

    dossier = build_dossier(repo.session, claim_id)
    assert dossier.analysis["difference"] == 0.06
    assert dossier.recomputed.inputs.effect_to_interval_ratio == pytest.approx(0.06 / 0.04)
    assert Decimal(0) <= dossier.usd
