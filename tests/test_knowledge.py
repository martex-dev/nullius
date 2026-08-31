"""M8 acceptance: the institution remembers, and does not re-ask."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from nullius.db.enums import (
    AssertionKind,
    ClaimConfidence,
    DerivationKind,
    HypothesisState,
    Role,
    Verdict,
)
from nullius.errors import InvariantViolation
from nullius.knowledge.followups import seeds_for
from nullius.knowledge.genealogy import ancestors, descendants, lineage_summary, render, tree
from nullius.knowledge.memory import recall
from nullius.knowledge.novelty import (
    NoveltyVerdict,
    assess_novelty,
    fingerprint,
    similarity,
)
from nullius.ledger.rebuild import reconciliation
from nullius.repository import Repository
from tests.conftest import Scaffold

PRUNING = (
    "Dropping the three features whose distributions diverge most between "
    "environments raises deployment macro-F1 relative to using all features."
)
NEARLY_VERBATIM = (
    "Dropping the three features whose distributions diverge most between "
    "environments increases deployment macro-F1 versus using all features."
)
PARAPHRASE = (
    "Removing the three most divergent columns across environments lifts "
    "deployment macro-F1 compared with keeping every column."
)
UNRELATED = (
    "Calibrating predicted probabilities with isotonic regression reduces "
    "expected calibration error on tabular benchmarks."
)


def _hypothesis(
    repo: Repository,
    scaffold: Scaffold,
    statement: str,
    *,
    parent_id: uuid.UUID | None = None,
    derivation: DerivationKind = DerivationKind.ROOT,
    mde: float = 0.02,
) -> uuid.UUID:
    created = repo.as_role(Role.THEORIST).create_hypothesis(
        program_id=scaffold.program_id,
        statement=statement,
        mechanism="Unstable features mislead a model that learned to rely on them.",
        primary_metric="macro_f1",
        direction="increase",
        mde=mde,
        falsification_condition="If the interval does not exceed the claimed effect.",
        parent_id=parent_id,
        derivation=derivation,
    )
    return created.hypothesis_id


# ---------------------------------------------------------------------------
# Acceptance 1 — a duplicate hypothesis is caught at intake
# ---------------------------------------------------------------------------


def test_an_exact_repeat_is_refused(repo: Repository, scaffold: Scaffold) -> None:
    _hypothesis(repo, scaffold, PRUNING)

    with pytest.raises(InvariantViolation, match="already holds an equivalent hypothesis"):
        _hypothesis(repo, scaffold, PRUNING)


def test_a_near_verbatim_repeat_is_refused(repo: Repository, scaffold: Scaffold) -> None:
    """The cheapest and likeliest repeat: the same question, barely reworded."""
    _hypothesis(repo, scaffold, PRUNING)

    with pytest.raises(InvariantViolation, match="already holds an equivalent"):
        _hypothesis(repo, scaffold, NEARLY_VERBATIM)


def test_paraphrase_is_not_caught(repo: Repository, scaffold: Scaffold) -> None:
    """KNOWN GAP, pinned so that closing it is a visible change.

    Lexical overlap cannot separate a genuine paraphrase from an unrelated
    statement — measured, one paraphrase pair scores exactly zero, the same as
    two statements about different subjects. No threshold fixes this;
    embeddings would.

    The consequence is bounded rather than unbounded: a paraphrased hypothesis
    gets through here, but if it compiles to the same experiment it is still
    refused at registration, where ``spec_hash`` is unique. That is the
    backstop that protects the budget.
    """
    _hypothesis(repo, scaffold, PRUNING)
    assert _hypothesis(repo, scaffold, PARAPHRASE), (
        "if this now raises, paraphrase detection has improved — update the "
        "measured table in nullius.knowledge.novelty and delete this test"
    )


def test_a_paraphrased_hypothesis_is_still_stopped_at_registration(
    repo: Repository, scaffold: Scaffold
) -> None:
    """The layered defence: the weak check is not the only one."""
    first = _hypothesis(repo, scaffold, PRUNING)
    second = _hypothesis(repo, scaffold, PARAPHRASE)

    design = {"arms": ["full", "prune"], "k": 3}
    plan = {"test": "paired_bootstrap", "alpha": 0.05}
    designer = repo.as_role(Role.DESIGNER)
    designer.register(
        hypothesis_id=first,
        spec=design,
        analysis_plan=plan,
        seed_root=1,
        n_seeds=5,
        holdout_query_budget=1,
    )

    with pytest.raises(InvariantViolation, match="already registered"):
        designer.register(
            hypothesis_id=second,
            spec=design,
            analysis_plan=plan,
            seed_root=1,
            n_seeds=5,
            holdout_query_budget=1,
        )


def test_nudging_the_effect_size_does_not_defeat_the_check(
    repo: Repository, scaffold: Scaffold
) -> None:
    """The fingerprint buckets the effect, so 0.020 and 0.021 are one claim."""
    _hypothesis(repo, scaffold, PRUNING, mde=0.020)

    with pytest.raises(InvariantViolation, match="already holds an equivalent"):
        _hypothesis(repo, scaffold, PRUNING, mde=0.021)


def test_an_unrelated_hypothesis_is_accepted(repo: Repository, scaffold: Scaffold) -> None:
    """Specificity: a check that refuses everything blocks research."""
    _hypothesis(repo, scaffold, PRUNING)
    assert _hypothesis(repo, scaffold, UNRELATED)


def test_a_repeat_is_permitted_as_an_acknowledged_descendant(
    repo: Repository, scaffold: Scaffold
) -> None:
    """The institution may revisit; it may not pretend the question is new."""
    original = _hypothesis(repo, scaffold, PRUNING)

    revisited = _hypothesis(
        repo,
        scaffold,
        PRUNING,
        parent_id=original,
        derivation=DerivationKind.SPECIALISATION,
    )
    repo.commit()

    assert revisited != original
    assert [h.hypothesis_id for h in ancestors(repo.session, revisited)] == [original]


def test_similarity_is_symmetric_and_bounded() -> None:
    assert similarity(PRUNING, PRUNING) == 1.0
    assert similarity(PRUNING, PARAPHRASE) == similarity(PARAPHRASE, PRUNING)
    assert 0.0 <= similarity(PRUNING, UNRELATED) < 0.3


def test_the_measured_separation_still_holds() -> None:
    """The numbers the threshold was set from, pinned.

    Near-verbatim repeats sit far above the threshold and everything else far
    below, so the exact value between them does not matter — which is the
    property that makes it defensible rather than tuned.
    """
    from nullius.knowledge.novelty import DUPLICATE_SIMILARITY

    assert similarity(PRUNING, NEARLY_VERBATIM) > 0.85
    assert similarity(PRUNING, PARAPHRASE) < 0.30
    assert similarity(PRUNING, UNRELATED) < 0.05
    assert 0.30 < DUPLICATE_SIMILARITY < 0.85


def test_similarity_of_contentless_statements_is_zero() -> None:
    """Two statements made only of filler are not evidence of repetition."""
    assert similarity("the and of to", "a for with within") == 0.0


def test_the_fingerprint_ignores_wording_but_not_structure() -> None:
    base = {"statement": PRUNING, "primary_metric": "macro_f1", "direction": "increase"}
    assert fingerprint(**base, mde=0.02) == fingerprint(**base, mde=0.024)
    assert fingerprint(**base, mde=0.02) != fingerprint(
        **{**base, "direction": "decrease"}, mde=0.02
    )
    assert fingerprint(**base, mde=0.02) != fingerprint(
        **{**base, "primary_metric": "accuracy"}, mde=0.02
    )


def test_novelty_is_scoped_to_the_programme(repo: Repository, scaffold: Scaffold) -> None:
    """Two programmes may legitimately arrive at similar questions."""
    _hypothesis(repo, scaffold, PRUNING)

    report = assess_novelty(
        repo.session,
        program_id=uuid.uuid4(),
        statement=PRUNING,
        primary_metric="macro_f1",
        direction="increase",
        mde=0.02,
    )
    assert report.verdict is NoveltyVerdict.NOVEL


# ---------------------------------------------------------------------------
# Acceptance 2 — second-generation hypotheses derive from first-generation results
# ---------------------------------------------------------------------------


def test_a_terminal_result_opens_a_follow_up_that_a_hypothesis_takes(
    repo: Repository, scaffold: Scaffold
) -> None:
    """The generational loop, end to end and traceable in both directions."""
    first = _hypothesis(repo, scaffold, PRUNING)
    director = repo.as_role(Role.DIRECTOR)
    director.advance_hypothesis(first, HypothesisState.REFUTED)

    seeds = seeds_for(state=HypothesisState.REFUTED, verdict=Verdict.REFUTED)
    assert seeds, "a refutation must leave something worth asking"

    follow_up = director.record_follow_up(
        program_id=scaffold.program_id,
        source_hypothesis_id=first,
        source_state=HypothesisState.REFUTED,
        kind=seeds[0].kind,
        prompt=seeds[0].prompt,
        derivation=seeds[0].derivation,
    )
    assert repo.open_follow_ups(scaffold.program_id) == [follow_up]

    second = _hypothesis(
        repo,
        scaffold,
        "Divergence-based selection reverses sign when the moved columns carry "
        "the invariant relationship rather than an environment-specific one.",
        parent_id=first,
        derivation=seeds[0].derivation,
    )
    repo.as_role(Role.THEORIST).take_follow_up(
        follow_up.follow_up_id, second, program_id=scaffold.program_id
    )
    repo.commit()

    # Forwards: the finding names the question it produced.
    assert repo.open_follow_ups(scaffold.program_id) == []
    taken = repo.session.get(type(follow_up), follow_up.follow_up_id)
    assert taken is not None
    assert taken.taken_by_hypothesis_id == second
    assert taken.source_hypothesis_id == first

    # Backwards: the question names the finding it came from.
    assert [h.hypothesis_id for h in ancestors(repo.session, second)] == [first]
    assert [h.hypothesis_id for h in descendants(repo.session, first)] == [second]

    assert repo.ledger.verify().ok
    assert reconciliation(repo.session).ok


def test_a_follow_up_cannot_be_taken_twice(repo: Repository, scaffold: Scaffold) -> None:
    first = _hypothesis(repo, scaffold, PRUNING)
    follow_up = repo.as_role(Role.DIRECTOR).record_follow_up(
        program_id=scaffold.program_id,
        source_hypothesis_id=first,
        source_state=HypothesisState.NO_EFFECT
        if hasattr(HypothesisState, "NO_EFFECT")
        else HypothesisState.INCONCLUSIVE,
        kind="probe_the_null",
        prompt="Ask whether the null survives a condition where an effect is predicted.",
        derivation=DerivationKind.GENERALISATION,
    )
    theorist = repo.as_role(Role.THEORIST)
    theorist.take_follow_up(follow_up.follow_up_id, first)

    with pytest.raises(InvariantViolation, match="already taken"):
        theorist.take_follow_up(follow_up.follow_up_id, first)


@pytest.mark.parametrize(
    ("verdict", "expected_kind"),
    [
        (Verdict.REFUTED, "find_the_moderator"),
        (Verdict.INCONCLUSIVE, "claim_less"),
        (Verdict.NO_EFFECT, "probe_the_null"),
        (Verdict.SUPPORTED, "test_generalisation"),
    ],
)
def test_each_terminal_outcome_leaves_a_different_question(
    verdict: Verdict, expected_kind: str
) -> None:
    seeds = seeds_for(state=HypothesisState.REVIEWED, verdict=verdict)
    assert [s.kind for s in seeds] == [expected_kind]


def test_an_underpowered_result_asks_for_power_and_nothing_else() -> None:
    """ "We could not tell" leaves exactly one useful question."""
    seeds = seeds_for(
        state=HypothesisState.INCONCLUSIVE, verdict=Verdict.INCONCLUSIVE, underpowered=True
    )
    assert [s.kind for s in seeds] == ["repower"]


def test_an_open_objection_outranks_a_speculative_next_step() -> None:
    """A debt against an existing claim comes before a new idea."""
    seeds = seeds_for(
        state=HypothesisState.CHALLENGED,
        verdict=Verdict.SUPPORTED,
        open_objections=("confound",),
    )
    assert seeds[0].kind == "settle_objection"
    assert "test_generalisation" in {s.kind for s in seeds}


# ---------------------------------------------------------------------------
# Genealogy
# ---------------------------------------------------------------------------


def test_the_genealogy_shows_descent(repo: Repository, scaffold: Scaffold) -> None:
    root = _hypothesis(repo, scaffold, PRUNING)
    child = _hypothesis(
        repo, scaffold, UNRELATED, parent_id=root, derivation=DerivationKind.SPECIALISATION
    )
    grandchild = _hypothesis(
        repo,
        scaffold,
        "Isotonic calibration on shifted subgroups reduces expected calibration error.",
        parent_id=child,
        derivation=DerivationKind.GENERALISATION,
    )
    repo.commit()

    roots = tree(repo.session, scaffold.program_id)
    assert len(roots) == 1
    assert roots[0].hypothesis_id == root
    assert [n.hypothesis_id for n in roots[0].walk()] == [root, child, grandchild]
    assert [n.depth for n in roots[0].walk()] == [0, 1, 2]


def test_a_branch_is_progressive_only_if_something_survived(
    repo: Repository, scaffold: Scaffold
) -> None:
    """The Lakatosian distinction, counted rather than asserted."""
    root = _hypothesis(repo, scaffold, PRUNING)
    child = _hypothesis(
        repo, scaffold, UNRELATED, parent_id=root, derivation=DerivationKind.SPECIALISATION
    )
    director = repo.as_role(Role.DIRECTOR)
    director.advance_hypothesis(root, HypothesisState.REFUTED)
    director.advance_hypothesis(child, HypothesisState.REFUTED)
    repo.commit()

    summary = lineage_summary(tree(repo.session, scaffold.program_id)[0])
    assert summary.size == 2
    assert summary.refuted == 2
    assert not summary.progressive
    assert "degenerating" in str(summary)

    director.advance_hypothesis(child, HypothesisState.INSTITUTIONAL)
    repo.commit()
    assert lineage_summary(tree(repo.session, scaffold.program_id)[0]).progressive


def test_the_genealogy_renders(repo: Repository, scaffold: Scaffold) -> None:
    root = _hypothesis(repo, scaffold, PRUNING)
    _hypothesis(repo, scaffold, UNRELATED, parent_id=root, derivation=DerivationKind.SPECIALISATION)
    repo.as_role(Role.DIRECTOR).advance_hypothesis(root, HypothesisState.INSTITUTIONAL)
    repo.commit()

    text = render(tree(repo.session, scaffold.program_id))
    assert "[*]" in text, "an institutional claim is marked"
    assert "└──" in text or "├──" in text


# ---------------------------------------------------------------------------
# Cross-item memory
# ---------------------------------------------------------------------------


def test_memory_carries_established_claims_but_not_weak_ones(
    repo: Repository, scaffold: Scaffold
) -> None:
    """A speculative claim in memory is an unearned prior on the next question."""
    strong_h = _hypothesis(repo, scaffold, PRUNING)
    weak_h = _hypothesis(repo, scaffold, UNRELATED)

    analyst = repo.as_role(Role.ANALYST)
    strong = analyst.create_claim(
        program_id=scaffold.program_id,
        statement="Pruning shifted, non-causal columns helps under this shift.",
        kind=AssertionKind.INFERRED_CLAIM,
        hypothesis_id=strong_h,
    )
    analyst.create_claim(
        program_id=scaffold.program_id,
        statement="Calibration might help, on very little evidence.",
        kind=AssertionKind.INFERRED_CLAIM,
        hypothesis_id=weak_h,
    )
    repo.commit()

    # Promote only the first; the second stays speculative.
    repo.session.get(type(strong), strong.claim_id).confidence = ClaimConfidence.SUPPORTED
    repo.session.flush()

    remembered = recall(repo.session, program_id=scaffold.program_id)
    assert [r.claim_id for r in remembered] == [strong.claim_id]
    assert remembered[0].confidence == "supported"


def test_memory_never_returns_a_questions_own_answer(repo: Repository, scaffold: Scaffold) -> None:
    hypothesis_id = _hypothesis(repo, scaffold, PRUNING)
    claim = repo.as_role(Role.ANALYST).create_claim(
        program_id=scaffold.program_id,
        statement="Pruning helps under this shift.",
        kind=AssertionKind.INFERRED_CLAIM,
        hypothesis_id=hypothesis_id,
    )
    repo.session.get(type(claim), claim.claim_id).confidence = ClaimConfidence.SUPPORTED
    repo.session.flush()

    assert (
        recall(repo.session, program_id=scaffold.program_id, exclude_hypothesis=hypothesis_id) == []
    )


def test_memory_carries_the_confidence_with_the_claim(repo: Repository, scaffold: Scaffold) -> None:
    """A memory that dropped the caveats would let weak findings harden."""
    hypothesis_id = _hypothesis(repo, scaffold, PRUNING)
    claim = repo.as_role(Role.ANALYST).create_claim(
        program_id=scaffold.program_id,
        statement="Pruning helps, though the design was contested.",
        kind=AssertionKind.INFERRED_CLAIM,
        hypothesis_id=hypothesis_id,
    )
    repo.session.get(type(claim), claim.claim_id).confidence = ClaimConfidence.SUGGESTIVE
    repo.session.flush()

    remembered = recall(repo.session, program_id=scaffold.program_id)
    assert remembered[0].as_dict()["confidence"] == "suggestive"


def test_memory_does_not_cross_programmes_by_default(repo: Repository, scaffold: Scaffold) -> None:
    """A programme reasoning about itself sees only its own findings."""
    hypothesis_id = _hypothesis(repo, scaffold, PRUNING)
    claim = repo.as_role(Role.ANALYST).create_claim(
        program_id=scaffold.program_id,
        statement="Pruning helps under this shift.",
        kind=AssertionKind.INFERRED_CLAIM,
        hypothesis_id=hypothesis_id,
    )
    repo.session.get(type(claim), claim.claim_id).confidence = ClaimConfidence.SUPPORTED
    repo.session.flush()

    sibling = repo.create_program(
        rq_id=scaffold.rq_id,
        lab_id=scaffold.lab_id,
        policy_id=scaffold.policy_id,
        budget_usd=Decimal("25.00"),
        config_hash="0" * 64,
        capability_digest="1" * 64,
    )
    assert recall(repo.session, program_id=sibling.program_id) == []


def test_memory_crosses_programmes_within_a_lab_when_asked(
    repo: Repository, scaffold: Scaffold
) -> None:
    """The scope the benchmark's memory arm depends on.

    A ``Program`` is one research question. Memory that could not cross from
    one question to the next would make B6 and B7 identical by construction,
    and the ablation could only ever report no difference — a fact about the
    harness dressed as a finding about memory.
    """
    hypothesis_id = _hypothesis(repo, scaffold, PRUNING)
    claim = repo.as_role(Role.ANALYST).create_claim(
        program_id=scaffold.program_id,
        statement="Pruning helps under this shift.",
        kind=AssertionKind.INFERRED_CLAIM,
        hypothesis_id=hypothesis_id,
    )
    repo.session.get(type(claim), claim.claim_id).confidence = ClaimConfidence.SUPPORTED
    repo.session.flush()

    sibling = repo.create_program(
        rq_id=scaffold.rq_id,
        lab_id=scaffold.lab_id,
        policy_id=scaffold.policy_id,
        budget_usd=Decimal("25.00"),
        config_hash="0" * 64,
        capability_digest="1" * 64,
    )
    remembered = recall(repo.session, program_id=sibling.program_id, scope="lab")
    assert [r.claim_id for r in remembered] == [claim.claim_id]


def test_lab_scoped_memory_stops_at_the_lab_boundary(repo: Repository, scaffold: Scaffold) -> None:
    """One institution's findings are not another's background assumptions."""
    hypothesis_id = _hypothesis(repo, scaffold, PRUNING)
    claim = repo.as_role(Role.ANALYST).create_claim(
        program_id=scaffold.program_id,
        statement="Pruning helps under this shift.",
        kind=AssertionKind.INFERRED_CLAIM,
        hypothesis_id=hypothesis_id,
    )
    repo.session.get(type(claim), claim.claim_id).confidence = ClaimConfidence.SUPPORTED
    repo.session.flush()

    other_lab = repo.create_lab("Rival Lab", "Independently curious.")
    elsewhere = repo.create_program(
        rq_id=scaffold.rq_id,
        lab_id=other_lab.lab_id,
        policy_id=scaffold.policy_id,
        budget_usd=Decimal("25.00"),
        config_hash="0" * 64,
        capability_digest="1" * 64,
    )
    assert recall(repo.session, program_id=elsewhere.program_id, scope="lab") == []
