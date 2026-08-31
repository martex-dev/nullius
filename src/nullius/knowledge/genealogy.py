"""Research genealogy: how ideas descended from one another.

A recursive query over ``hypotheses.parent_id``, which is enough because the
edge was recorded at intake rather than reconstructed later. `docs/02` §6 — a
genealogy assembled at the end is a summary; one written as the work happens
is a memory.

What it is *for* is the Lakatosian distinction. A branch that keeps producing
supported claims is progressive; one that keeps producing refutations and
inconclusive results is degenerating. Both are visible at a glance in the
tree, and from M9 the allocator can act on the difference rather than a person
noticing it.

:func:`descendants` uses a recursive CTE, because a subtree can be wide and
walking it in Python would mean a query per level. :func:`ancestors` walks
iteratively, because a lineage is one row per level. :func:`tree` loads the
programme's hypotheses once and assembles the whole forest in memory, which is
cheaper than either at the sizes involved. Different shapes, different tools.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.orm import Session

from nullius.db.enums import TERMINAL_STATES, DerivationKind, HypothesisState
from nullius.db.tables import Claim, Hypothesis

__all__ = ["GenealogyNode", "ancestors", "descendants", "lineage_summary", "render", "tree"]


@dataclass(slots=True)
class GenealogyNode:
    """One hypothesis and everything descended from it."""

    hypothesis_id: uuid.UUID
    statement: str
    state: HypothesisState
    derivation: DerivationKind
    depth: int
    confidence: str | None = None
    children: list[GenealogyNode] = field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def productive(self) -> bool:
        """Whether this line produced an institutional claim.

        The unit of a progressive branch: not that it ran, but that something
        survived.
        """
        return self.state is HypothesisState.INSTITUTIONAL

    def walk(self) -> list[GenealogyNode]:
        """This node and every descendant, depth first."""
        out = [self]
        for child in self.children:
            out.extend(child.walk())
        return out


def descendants(session: Session, hypothesis_id: uuid.UUID) -> list[Hypothesis]:
    """Every hypothesis descended from this one, nearest first."""
    base = (
        sa.select(Hypothesis.hypothesis_id, sa.literal(0).label("depth"))
        .where(Hypothesis.hypothesis_id == hypothesis_id)
        .cte("descendants", recursive=True)
    )
    base = base.union_all(
        sa.select(Hypothesis.hypothesis_id, (base.c.depth + 1).label("depth")).where(
            Hypothesis.parent_id == base.c.hypothesis_id
        )
    )
    rows = session.execute(
        sa.select(base.c.hypothesis_id, base.c.depth).where(base.c.depth > 0).order_by(base.c.depth)
    ).all()
    return _load(session, [row[0] for row in rows])


def ancestors(session: Session, hypothesis_id: uuid.UUID) -> list[Hypothesis]:
    """Every hypothesis this one descended from, nearest first.

    Walked iteratively rather than by recursive CTE: a lineage is one row per
    level and rarely more than a handful deep, so the query would cost more to
    plan than the walk costs to run. The cycle guard is defensive only —
    ``parent_id`` is set at intake and never edited.
    """
    current = session.get(Hypothesis, hypothesis_id)
    chain: list[Hypothesis] = []
    seen: set[uuid.UUID] = set()

    while current is not None and current.parent_id is not None:
        if current.parent_id in seen:  # pragma: no cover - defensive
            break
        seen.add(current.parent_id)
        parent = session.get(Hypothesis, current.parent_id)
        if parent is None:
            break
        chain.append(parent)
        current = parent
    return chain


def _load(session: Session, ids: list[uuid.UUID]) -> list[Hypothesis]:
    if not ids:
        return []
    found = {
        h.hypothesis_id: h
        for h in session.scalars(sa.select(Hypothesis).where(Hypothesis.hypothesis_id.in_(ids)))
    }
    return [found[i] for i in ids if i in found]


def tree(session: Session, program_id: uuid.UUID) -> list[GenealogyNode]:
    """The programme's full genealogy, as roots with nested children."""
    rows = list(
        session.scalars(
            sa.select(Hypothesis)
            .where(Hypothesis.program_id == program_id)
            .order_by(Hypothesis.created_at.asc())
        )
    )
    confidences = {
        claim.hypothesis_id: claim.confidence.value
        for claim in session.scalars(sa.select(Claim).where(Claim.program_id == program_id))
        if claim.hypothesis_id is not None
    }

    nodes = {
        row.hypothesis_id: GenealogyNode(
            hypothesis_id=row.hypothesis_id,
            statement=row.statement,
            state=row.state,
            derivation=row.derivation,
            depth=0,
            confidence=confidences.get(row.hypothesis_id),
        )
        for row in rows
    }

    roots: list[GenealogyNode] = []
    for row in rows:
        node = nodes[row.hypothesis_id]
        parent = nodes.get(row.parent_id) if row.parent_id else None
        if parent is None:
            roots.append(node)
        else:
            node.depth = parent.depth + 1
            parent.children.append(node)

    # Depth is assigned as parents are seen, which is correct only if parents
    # precede children. They do, since a child cannot be created before its
    # parent, but fixing it up costs nothing and removes the assumption.
    for root in roots:
        _assign_depth(root, 0)
    return roots


def _assign_depth(node: GenealogyNode, depth: int) -> None:
    node.depth = depth
    for child in node.children:
        _assign_depth(child, depth + 1)


@dataclass(frozen=True, slots=True)
class LineageSummary:
    """Whether a branch is going anywhere."""

    root_id: uuid.UUID
    size: int
    terminal: int
    institutional: int
    refuted: int
    inconclusive: int

    @property
    def progressive(self) -> bool:
        """A branch producing claims that survive, rather than only questions."""
        return self.institutional > 0 and self.institutional >= self.refuted

    def __str__(self) -> str:
        verdict = "progressive" if self.progressive else "degenerating"
        return (
            f"{self.size} hypotheses, {self.institutional} institutional, "
            f"{self.refuted} refuted, {self.inconclusive} inconclusive — {verdict}"
        )


def lineage_summary(root: GenealogyNode) -> LineageSummary:
    """Count what a branch has actually produced."""
    nodes = root.walk()
    return LineageSummary(
        root_id=root.hypothesis_id,
        size=len(nodes),
        terminal=sum(1 for n in nodes if n.terminal),
        institutional=sum(1 for n in nodes if n.state is HypothesisState.INSTITUTIONAL),
        refuted=sum(1 for n in nodes if n.state is HypothesisState.REFUTED),
        inconclusive=sum(1 for n in nodes if n.state is HypothesisState.INCONCLUSIVE),
    )


_MARKS = {
    HypothesisState.INSTITUTIONAL: "*",
    HypothesisState.REFUTED: "x",
    HypothesisState.INCONCLUSIVE: "?",
    HypothesisState.SHELVED: "-",
    HypothesisState.ABANDONED_BUDGET: "$",
}


def render(roots: list[GenealogyNode], *, width: int = 64) -> str:
    """The genealogy as text, for the CLI and for reports."""
    lines: list[str] = []

    def emit(node: GenealogyNode, prefix: str, last: bool) -> None:
        mark = _MARKS.get(node.state, " ")
        connector = "" if not prefix and node.depth == 0 else ("└── " if last else "├── ")
        statement = node.statement[:width].rstrip()
        lines.append(
            f"{prefix}{connector}[{mark}] {statement}"
            + (f"  ({node.confidence})" if node.confidence else "")
        )
        child_prefix = prefix + ("" if node.depth == 0 else ("    " if last else "│   "))
        for index, child in enumerate(node.children):
            emit(child, child_prefix, index == len(node.children) - 1)

    for root in roots:
        emit(root, "", True)
    return "\n".join(lines)
