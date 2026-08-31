"""Institutional novelty: has this been asked before?

Novelty here means *within our own records*, and nothing more.
`docs/01-critique.md` A7 — novelty against world literature is unknowable, and
a system that claims it is claiming something it cannot check. What can be
checked is whether the institution is about to re-ask a question it has
already answered, which is failure mode F13: an institution that explores by
rephrasing.

Two comparisons, because a hypothesis can repeat in two ways:

**Structurally.** Same metric, same direction, same claimed effect size, on
the same target. Two hypotheses with the same fingerprint are the same
experiment however differently they are worded.

**Lexically.** Token overlap between the statements, after stripping the
filler that every hypothesis shares. This catches a reworded repeat that the
fingerprint misses because someone nudged the effect size.

**Measured limitation.** Token overlap catches near-verbatim repeats and
nothing else. On a small hand-built set:

======================  ===============
pair kind               similarity
======================  ===============
near-verbatim repeat    0.87 – 0.90
genuine paraphrase      0.00 – 0.26
unrelated               0.00
======================  ===============

There is no threshold separating paraphrase from unrelated — one paraphrase
pair scored exactly zero, the same as two statements about different subjects.
That is not a tuning problem, it is what lexical overlap can do. Embeddings
would close the gap and arrive with ``pgvector`` when there is a reason to add
the dependency; :func:`test_paraphrase_is_not_caught` pins the gap so closing
it is a visible change.

The check still earns its place, for two reasons. It catches the cheapest and
most likely repeat — the same question asked again in almost the same words.
And it is not the only line of defence: a duplicate *experiment* is already
refused at registration by the uniqueness of ``spec_hash``, which is the
backstop that actually protects the budget, since two differently-worded
hypotheses that compile to the same design are stopped there.

The enforcement is the interesting part. A duplicate is not refused outright —
it is refused *unless the new hypothesis names the old one as its parent*. The
institution may revisit a question; it may not pretend the question is new.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Session

from nullius.db.tables import Hypothesis
from nullius.util.canonical import sha256_of

__all__ = [
    "DUPLICATE_SIMILARITY",
    "NEAR_DUPLICATE_SIMILARITY",
    "NoveltyReport",
    "NoveltyVerdict",
    "assess_novelty",
    "fingerprint",
    "similarity",
]

DUPLICATE_SIMILARITY = 0.75
"""Above this, the statements are the same question in nearly the same words.

Set from the measured gap above, not tuned to a case: near-verbatim repeats
score 0.87 and up, everything else scores 0.26 and below, so anywhere in that
range behaves identically.
"""

NEAR_DUPLICATE_SIMILARITY = 0.55
"""Above this, worth recording as related but not worth refusing."""

#: Words every hypothesis in this domain contains, which therefore carry no
#: information about whether two of them are the same.
_FILLER_WORDS = """
    a an and are as at be been by can could does do for from has have if in into is it
    its may might no not of on or relative that the their then there these this to under
    was were which will with within would improve improves improving increase increases
    decrease decreases effect performance model models data dataset feature features
"""

_FILLER = frozenset(_FILLER_WORDS.split())

_WORD = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> frozenset[str]:
    return frozenset(word for word in _WORD.findall(text.lower()) if word not in _FILLER)


def similarity(first: str, second: str) -> float:
    """Jaccard overlap of informative tokens.

    Symmetric, in ``[0, 1]``, and zero when either side has nothing
    informative left — which is the right answer, since two contentless
    statements are not evidence of repetition.
    """
    left, right = _tokens(first), _tokens(second)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def fingerprint(*, statement: str, primary_metric: str, direction: str, mde: float) -> str:
    """A structural identity for a hypothesis.

    The effect size is bucketed rather than used exactly: claiming 0.020 and
    claiming 0.021 is the same claim, and treating them as different would let
    an institution defeat the check by a rounding error.
    """
    return sha256_of(
        {
            "metric": primary_metric,
            "direction": direction,
            "mde_bucket": round(float(mde), 2),
            "tokens": sorted(_tokens(statement)),
        }
    )


class NoveltyVerdict(StrEnum):
    NOVEL = "novel"
    NEAR_DUPLICATE = "near_duplicate"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class NoveltyReport:
    """Whether this question has been asked here before."""

    verdict: NoveltyVerdict
    closest_id: uuid.UUID | None = None
    closest_statement: str = ""
    score: float = 0.0
    same_fingerprint: bool = False

    @property
    def is_duplicate(self) -> bool:
        return self.verdict is NoveltyVerdict.DUPLICATE

    def __str__(self) -> str:
        if self.verdict is NoveltyVerdict.NOVEL:
            return "novel: nothing comparable in the record"
        return (
            f"{self.verdict.value}: {self.score:.0%} overlap with "
            f"{self.closest_id} ({self.closest_statement[:60]}…)"
        )


def assess_novelty(
    session: Session,
    *,
    program_id: uuid.UUID,
    statement: str,
    primary_metric: str,
    direction: str,
    mde: float,
    exclude: uuid.UUID | None = None,
) -> NoveltyReport:
    """Compare a candidate hypothesis against everything the programme holds.

    Scoped to the programme rather than the whole institution: two programmes
    investigating different questions may legitimately arrive at similar
    hypotheses, and refusing that would stop knowledge transfer rather than
    duplication.
    """
    candidate = fingerprint(
        statement=statement, primary_metric=primary_metric, direction=direction, mde=mde
    )

    query = sa.select(Hypothesis).where(Hypothesis.program_id == program_id)
    if exclude is not None:
        query = query.where(Hypothesis.hypothesis_id != exclude)

    best = NoveltyReport(verdict=NoveltyVerdict.NOVEL)
    for existing in session.scalars(query):
        same = (
            fingerprint(
                statement=existing.statement,
                primary_metric=existing.primary_metric,
                direction=existing.direction,
                mde=existing.mde,
            )
            == candidate
        )
        score = similarity(statement, existing.statement)

        if same or score >= DUPLICATE_SIMILARITY:
            verdict = NoveltyVerdict.DUPLICATE
        elif score >= NEAR_DUPLICATE_SIMILARITY:
            verdict = NoveltyVerdict.NEAR_DUPLICATE
        else:
            continue

        # Keep the strongest match, preferring an exact structural repeat.
        if (
            best.verdict is NoveltyVerdict.NOVEL
            or (same and not best.same_fingerprint)
            or score > best.score
        ):
            best = NoveltyReport(
                verdict=verdict,
                closest_id=existing.hypothesis_id,
                closest_statement=existing.statement,
                score=score,
                same_fingerprint=same,
            )

    return best
