"""The question bank.

Twenty items spanning the declared effect size in both directions, chosen so
that the benchmark measures *calibration* rather than detection — the decision
recorded on 2026-08-30.

Two things are deliberately separated:

``question``
    What an agent sees. It must not name the shift family or its strength,
    because an agent that is told which features move does not have to run an
    experiment to answer. :func:`validate_bank` enforces this.
``generator_params``
    What the harness uses. Never reaches an agent view.

The truth for each item is **measured, not written here**. There is no
"expected verdict" field for anyone to edit until it agrees with a result;
:mod:`nullius.bank.oracle` computes it and ``nullius bank verify`` recomputes
it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["BANK_V1", "BankItem", "validate_bank"]

MDE = 0.02
"""The effect size every bank hypothesis claims. Two macro-F1 points."""

_FORBIDDEN_IN_QUESTIONS = (
    "causal",
    "spurious",
    "noise",
    "shift_strength",
    "prune",
    "divergence",
)
"""Words that would give the answer away if they appeared in a question.

An agent told that the *causal* features are the ones that moved does not need
an experiment. The question may describe the task; it may not describe the
data generating process.
"""


@dataclass(frozen=True, slots=True)
class BankItem:
    """One research question with a knowable answer."""

    item_id: str
    question: str
    generator_params: dict[str, Any]
    mde: float = MDE
    planted_defects: tuple[str, ...] = ()
    notes: str = ""
    """Why this item is in the bank. Harness-only; never shown to an agent."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "question": self.question,
            "generator_params": dict(self.generator_params),
            "mde": self.mde,
            "planted_defects": list(self.planted_defects),
        }

    def agent_view(self) -> dict[str, Any]:
        """Everything an agent may be told about this item.

        Notably absent: the generator parameters, the planted defects, and the
        notes. What is left is a question and a metric — which is what a
        researcher gets.
        """
        return {
            "item_id": self.item_id,
            "question": self.question,
            "primary_metric": "macro_f1",
            "claimed_effect": self.mde,
        }


_QUESTION = (
    "On tabular dataset {label} under distribution change between the training "
    "and deployment environments, does dropping the {k} features whose marginal "
    "distributions differ most between the two environments improve deployment "
    "macro-F1 by at least {mde} relative to training on all features?"
)


def _item(
    item_id: str,
    label: str,
    notes: str,
    *,
    k: int = 3,
    defects: tuple[str, ...] = (),
    **generator_params: Any,
) -> BankItem:
    return BankItem(
        item_id=item_id,
        question=_QUESTION.format(label=label, k=k, mde=MDE),
        generator_params=generator_params,
        planted_defects=defects,
        notes=notes,
    )


#: Bank v1. Twenty items. Every effect size below was located by measuring a
#: sweep, not by intuition, and every verdict is computed by the oracle rather
#: than written here. The composition targets 45% true nulls (docs/04) plus a
#: band of items whose real effect is smaller than the one claimed — the
#: category that turns detection into calibration.
BANK_V1: tuple[BankItem, ...] = (
    # ---- The claimed effect is really there (4) ----------------------------
    _item(
        "B01",
        "D-11",
        "far above: heavy reliance on features that reverse",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=1.6,
    ),
    _item(
        "B02", "D-04", "well above", shift="spurious", shift_strength=2.0, spurious_strength=0.45
    ),
    _item("B03", "D-19", "above", shift="spurious", shift_strength=2.0, spurious_strength=0.15),
    _item(
        "B06",
        "D-15",
        "just above the claimed effect",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.09,
    ),
    # ---- The effect is real and points the other way (4) -------------------
    _item(
        "B04",
        "D-07",
        "far below: pruning removes the only transferable signal",
        shift="causal",
        shift_strength=3.0,
    ),
    _item("B17", "D-33", "well below", shift="causal", shift_strength=2.0),
    _item("B05", "D-23", "below", shift="causal", shift_strength=1.5),
    _item("B07", "D-02", "below, near the boundary", shift="causal", shift_strength=0.25),
    # ---- Real, but smaller than claimed (3) --------------------------------
    # The calibration cases. A system that answers only yes or no gets these
    # wrong however carefully it runs the experiment.
    _item(
        "B08",
        "D-28",
        "positive, smaller than claimed",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.075,
    ),
    _item("B18", "D-05", "negative, smaller than claimed", shift="causal", shift_strength=1.1),
    _item(
        "B20",
        "D-37",
        "positive, well below the claimed effect",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.06,
    ),
    # ---- Nothing is there (9, i.e. 45%) ------------------------------------
    _item(
        "B12",
        "D-22",
        "null, but not a trivial one: nothing shifts, yet the treatment still "
        "drops three features. Costs a little, not enough to matter",
        shift="none",
        shift_strength=0.0,
        label_noise=0.2,
    ),
    _item("B09", "D-31", "null: only irrelevant features move", shift="noise", shift_strength=2.0),
    _item("B10", "D-06", "null: irrelevant, weak", shift="noise", shift_strength=0.5),
    _item("B11", "D-14", "null: irrelevant, strong", shift="noise", shift_strength=4.0),
    _item(
        "B14",
        "D-17",
        "null: irrelevant, more of them",
        shift="noise",
        shift_strength=2.0,
        n_noise=8,
    ),
    _item(
        "B16",
        "D-12",
        "null: irrelevant, fewer of them",
        shift="noise",
        shift_strength=2.0,
        n_shifted=1,
    ),
    _item(
        "B19",
        "D-41",
        "null: irrelevant, wider causal set",
        shift="noise",
        shift_strength=2.0,
        n_causal=5,
    ),
    _item(
        "B21",
        "D-08",
        "null: irrelevant, noisier labels",
        shift="noise",
        shift_strength=2.0,
        label_noise=0.9,
    ),
    _item("B15", "D-25", "null: a shift too small to matter", shift="causal", shift_strength=0.9),
)


@dataclass(frozen=True, slots=True)
class BankReport:
    """Whether the bank is fit to score anything."""

    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.problems

    def __str__(self) -> str:
        if self.ok:
            return "bank: valid"
        return "bank: INVALID\n" + "\n".join(f"  {p}" for p in self.problems)


def validate_bank(items: tuple[BankItem, ...] = BANK_V1) -> BankReport:
    """Structural checks that do not require running the oracle."""
    problems: list[str] = []

    seen: set[str] = set()
    for item in items:
        if item.item_id in seen:
            problems.append(f"{item.item_id}: duplicate item id")
        seen.add(item.item_id)

        lowered = item.question.lower()
        for word in _FORBIDDEN_IN_QUESTIONS:
            if word in lowered:
                problems.append(
                    f"{item.item_id}: the question contains {word!r}, which would tell "
                    "an agent about the data generating process"
                )
        if item.mde <= 0:
            problems.append(f"{item.item_id}: mde must be positive")
        if "n_samples" in item.generator_params:
            problems.append(
                f"{item.item_id}: sets n_samples, which belongs to the experiment rather "
                "than to the data generating process. The oracle must be free to measure "
                "at a scale no experiment is allowed."
            )
        if "seed" in item.generator_params:
            problems.append(
                f"{item.item_id}: sets seed; seeds come from the registration and the oracle"
            )
        if item.generator_params.get("leak_strength"):
            problems.append(
                f"{item.item_id}: sets leak_strength, which exists only for the defect "
                "injector. Ground truth measured on leaked data would be measuring the leak."
            )

    return BankReport(problems=tuple(problems))
