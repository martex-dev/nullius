"""Cross-item memory: what the institution already believes.

The claims a programme has accumulated, offered to the Theorist when it takes
on a new question. This is the thing whose contribution the B6-versus-B7 arm
of `docs/04-evaluation.md` measures: identical in every other respect, one arm
remembers and one does not.

Two things it deliberately does not carry.

**No ground truth.** Claims are what the institution came to believe, which is
not the same as what is true, and may be wrong. That is the point — memory
that could only ever be right would be an oracle, and the benchmark would stop
measuring anything.

**No confidence inflation.** Each entry carries the computed confidence and
what capped it, so a memory of a contested claim reads as contested. A summary
that dropped the caveats would let weak findings harden into background
assumptions, which is how a research programme quietly becomes a belief
system.

**Scope.** M8 shipped this programme-scoped, which was wrong for the thing it
was built to serve. A programme is *one* research question; the benchmark's
memory arm is about what carries from one question to the next. Programme
scope therefore made B6 and B7 identical by construction — an ablation that
could only ever report no difference. The lab is the institution, so
``scope="lab"`` recalls across every programme the lab has run, and that is
what the benchmark uses. Programme scope is kept as the default because it is
the right answer for a single programme reasoning about itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from nullius.db.enums import CONFIDENCE_ORDER, ClaimConfidence
from nullius.db.tables import Claim, Hypothesis, Program

__all__ = ["Recollection", "recall"]

#: Below this, a claim is not worth carrying forward. A speculative claim in
#: memory is an unearned prior on the next question.
MINIMUM_CONFIDENCE = ClaimConfidence.SUGGESTIVE


@dataclass(frozen=True, slots=True)
class Recollection:
    """One thing the institution believes, and how firmly."""

    claim_id: uuid.UUID
    statement: str
    confidence: str
    primary_metric: str
    direction: str
    mde: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "confidence": self.confidence,
            "metric": self.primary_metric,
            "direction": self.direction,
            "claimed_effect": self.mde,
        }


def recall(
    session: Session,
    *,
    program_id: uuid.UUID,
    exclude_hypothesis: uuid.UUID | None = None,
    limit: int = 10,
    minimum: ClaimConfidence = MINIMUM_CONFIDENCE,
    scope: Literal["program", "lab"] = "program",
) -> list[Recollection]:
    """What the institution has established, strongest first.

    ``exclude_hypothesis`` keeps a question from recalling its own answer,
    which would be memory in name only.

    ``scope="lab"`` widens the recall to every programme run by the lab that
    owns ``program_id``, which is what makes memory carry between research
    questions rather than only within one.
    """
    threshold = CONFIDENCE_ORDER.index(minimum)

    query = sa.select(Claim, Hypothesis).join(
        Hypothesis, Hypothesis.hypothesis_id == Claim.hypothesis_id
    )
    if scope == "lab":
        siblings = (
            sa.select(Program.program_id)
            .where(
                Program.lab_id
                == sa.select(Program.lab_id)
                .where(Program.program_id == program_id)
                .scalar_subquery()
            )
            .scalar_subquery()
        )
        query = query.where(Claim.program_id.in_(siblings))
    else:
        query = query.where(Claim.program_id == program_id)
    if exclude_hypothesis is not None:
        query = query.where(Claim.hypothesis_id != exclude_hypothesis)

    found: list[tuple[int, Recollection]] = []
    for claim, hypothesis in session.execute(query).all():
        rank = CONFIDENCE_ORDER.index(claim.confidence)
        if rank < threshold:
            continue
        found.append(
            (
                rank,
                Recollection(
                    claim_id=claim.claim_id,
                    statement=claim.statement,
                    confidence=claim.confidence.value,
                    primary_metric=hypothesis.primary_metric,
                    direction=hypothesis.direction,
                    mde=hypothesis.mde,
                ),
            )
        )

    found.sort(key=lambda pair: pair[0], reverse=True)
    return [recollection for _, recollection in found[:limit]]
