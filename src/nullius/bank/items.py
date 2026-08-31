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

__all__ = ["BANK_V1", "BANK_V2", "BankItem", "validate_bank"]

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


#: Bank v2. Sixty items, and the reason is M10's own result rather than an
#: appetite for more data.
#:
#: The v1 ladder could not separate any two institutional arms. The diagnosis
#: written at the time — "the bank is too easy" — was wrong, and measuring it
#: properly says so: thirteen of v1's twenty items already sat within two
#: experiment standard errors of a verdict boundary, and the single item B4 got
#: wrong was the third hardest in the bank. The real limits were that twenty
#: items make the primary metric move in steps of 0.05, so no difference
#: smaller than one item can be seen at all, and that only six items sat in the
#: band where two arms could plausibly disagree.
#:
#: So v2 changes the two things that were actually wrong. Sixty items put the
#: metric's resolution at 0.017, and **thirty** of them sit within one
#: experiment standard error of a boundary, against six in v1.
#:
#: The headroom that makes this possible is the gap between the two
#: measurements. The oracle sees forty seeds of twenty thousand samples and
#: resolves an effect to about 0.0008; an experiment gets five seeds of two
#: thousand and resolves it to about 0.005. Every item here is at least three
#: *oracle* standard errors from its boundary — so its ground truth is not in
#: doubt — while many sit well inside one *experiment* standard error of it.
#: The truth is unambiguous and the question is still genuinely hard, which is
#: the only arrangement under which a hard bank is also a fair one.
#:
#: Composition is 45% true nulls, as ``docs/04-evaluation.md`` specifies. Every
#: parameter below was found by measuring a 311-point sweep of the generator
#: and selecting on the result, never by choosing a number that looked right.
#:
#: **v1 is not replaced.** ``benchmark/protocol.lock.json`` hashes v1's items
#: and its truth lock, and M10's results are bound to both. A bank version is
#: part of a preregistration, so changing it means registering again rather
#: than editing — the same rule that governs an experiment's ``spec_hash``.
BANK_V2: tuple[BankItem, ...] = (
    # ---- The claimed effect is really there (11) ---------------------
    _item(
        "C01",
        "E-01",
        "at or above the claimed effect; comfortably clear of any boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.3975,
    ),
    _item(
        "C02",
        "E-02",
        "at or above the claimed effect; comfortably clear of any boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.3375,
    ),
    _item(
        "C03",
        "E-03",
        "at or above the claimed effect; comfortably clear of any boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.285,
    ),
    _item(
        "C04",
        "E-04",
        "at or above the claimed effect; comfortably clear of any boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.2375,
    ),
    _item(
        "C05",
        "E-05",
        "at or above the claimed effect; comfortably clear of any boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.1975,
    ),
    _item(
        "C06",
        "E-06",
        "at or above the claimed effect; comfortably clear of any boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.1625,
    ),
    _item(
        "C07",
        "E-07",
        "at or above the claimed effect; comfortably clear of any boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.135,
    ),
    _item(
        "C08",
        "E-08",
        "at or above the claimed effect; comfortably clear of any boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.1125,
    ),
    _item(
        "C09",
        "E-09",
        "at or above the claimed effect; one to two standard errors out",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.0975,
    ),
    _item(
        "C10",
        "E-10",
        "at or above the claimed effect; inside one experiment standard error of a boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.0875,
    ),
    _item(
        "C11",
        "E-11",
        "at or above the claimed effect; inside one experiment standard error of a boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.085,
    ),
    # ---- Real, but smaller than claimed (12) - the calibration cases --
    _item(
        "C12",
        "E-12",
        "real but smaller than claimed; inside one experiment standard error of a boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.075,
    ),
    _item(
        "C13",
        "E-13",
        "real but smaller than claimed; inside one experiment standard error of a boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.0725,
    ),
    _item(
        "C14",
        "E-14",
        "real but smaller than claimed; inside one experiment standard error of a boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.07,
    ),
    _item(
        "C15",
        "E-15",
        "real but smaller than claimed; inside one experiment standard error of a boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.0675,
    ),
    _item(
        "C16",
        "E-16",
        "real but smaller than claimed; inside one experiment standard error of a boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.065,
    ),
    _item(
        "C17",
        "E-17",
        "real but smaller than claimed; inside one experiment standard error of a boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.0625,
    ),
    _item(
        "C18",
        "E-18",
        "real but smaller than claimed; inside one experiment standard error of a boundary",
        shift="causal",
        shift_strength=1.025,
    ),
    _item(
        "C19",
        "E-19",
        "real but smaller than claimed; inside one experiment standard error of a boundary",
        shift="causal",
        shift_strength=1.0425,
    ),
    _item(
        "C20",
        "E-20",
        "real but smaller than claimed; inside one experiment standard error of a boundary",
        shift="causal",
        shift_strength=1.0775,
    ),
    _item(
        "C21",
        "E-21",
        "real but smaller than claimed; inside one experiment standard error of a boundary",
        shift="causal",
        shift_strength=1.1125,
    ),
    _item(
        "C22",
        "E-22",
        "real but smaller than claimed; inside one experiment standard error of a boundary",
        shift="causal",
        shift_strength=1.1475,
    ),
    _item(
        "C23",
        "E-23",
        "real but smaller than claimed; inside one experiment standard error of a boundary",
        shift="causal",
        shift_strength=1.165,
    ),
    # ---- No effect at all (27) - 45% of the bank, per docs/04 --------
    _item(
        "C24",
        "E-24",
        "inside the null band; inside one experiment standard error of a boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.05,
    ),
    _item(
        "C25",
        "E-25",
        "inside the null band; inside one experiment standard error of a boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.0475,
    ),
    _item(
        "C26",
        "E-26",
        "inside the null band; inside one experiment standard error of a boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.045,
    ),
    _item(
        "C27",
        "E-27",
        "inside the null band; inside one experiment standard error of a boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.0425,
    ),
    _item(
        "C28",
        "E-28",
        "inside the null band; inside one experiment standard error of a boundary",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.04,
    ),
    _item(
        "C29",
        "E-29",
        "inside the null band; one to two standard errors out",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.0375,
    ),
    _item(
        "C30",
        "E-30",
        "inside the null band; one to two standard errors out",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.035,
    ),
    _item(
        "C31",
        "E-31",
        "inside the null band; one to two standard errors out",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.0325,
    ),
    _item(
        "C32",
        "E-32",
        "inside the null band; one to two standard errors out",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.03,
    ),
    _item(
        "C33",
        "E-33",
        "inside the null band; one to two standard errors out",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.0275,
    ),
    _item(
        "C34",
        "E-34",
        "inside the null band; one to two standard errors out",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.025,
    ),
    _item(
        "C35",
        "E-35",
        "inside the null band; one to two standard errors out",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.0225,
    ),
    _item(
        "C36",
        "E-36",
        "inside the null band; one to two standard errors out",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.0175,
    ),
    _item(
        "C37",
        "E-37",
        "inside the null band; one to two standard errors out",
        shift="spurious",
        shift_strength=2.0,
        spurious_strength=0.01,
    ),
    _item(
        "C38",
        "E-38",
        "inside the null band; one to two standard errors out",
        shift="causal",
        shift_strength=0.5175,
    ),
    _item(
        "C39",
        "E-39",
        "inside the null band; one to two standard errors out",
        shift="causal",
        shift_strength=0.5875,
    ),
    _item(
        "C40",
        "E-40",
        "inside the null band; one to two standard errors out",
        shift="causal",
        shift_strength=0.64,
    ),
    _item(
        "C41",
        "E-41",
        "inside the null band; one to two standard errors out",
        shift="causal",
        shift_strength=0.71,
    ),
    _item(
        "C42",
        "E-42",
        "inside the null band; inside one experiment standard error of a boundary",
        shift="causal",
        shift_strength=0.7625,
    ),
    _item(
        "C43",
        "E-43",
        "inside the null band; inside one experiment standard error of a boundary",
        shift="causal",
        shift_strength=0.815,
    ),
    _item(
        "C44",
        "E-44",
        "inside the null band; inside one experiment standard error of a boundary",
        shift="causal",
        shift_strength=0.85,
    ),
    _item(
        "C45",
        "E-45",
        "inside the null band; inside one experiment standard error of a boundary",
        shift="causal",
        shift_strength=0.8675,
    ),
    _item(
        "C46",
        "E-46",
        "inside the null band; inside one experiment standard error of a boundary",
        shift="causal",
        shift_strength=0.885,
    ),
    _item(
        "C47",
        "E-47",
        "inside the null band; inside one experiment standard error of a boundary",
        shift="causal",
        shift_strength=0.9025,
    ),
    _item(
        "C48",
        "E-48",
        "inside the null band; inside one experiment standard error of a boundary",
        shift="causal",
        shift_strength=0.92,
    ),
    _item(
        "C49",
        "E-49",
        "inside the null band; inside one experiment standard error of a boundary",
        shift="causal",
        shift_strength=0.9375,
    ),
    _item(
        "C50",
        "E-50",
        "inside the null band; inside one experiment standard error of a boundary",
        shift="causal",
        shift_strength=0.955,
    ),
    # ---- Real, and pointing the other way (10) -----------------------
    _item(
        "C51",
        "E-51",
        "against the claimed direction; inside one experiment standard error of a boundary",
        shift="causal",
        shift_strength=1.2175,
    ),
    _item(
        "C52",
        "E-52",
        "against the claimed direction; inside one experiment standard error of a boundary",
        shift="causal",
        shift_strength=1.235,
    ),
    _item(
        "C53",
        "E-53",
        "against the claimed direction; one to two standard errors out",
        shift="causal",
        shift_strength=1.305,
    ),
    _item(
        "C54",
        "E-54",
        "against the claimed direction; comfortably clear of any boundary",
        shift="causal",
        shift_strength=1.4275,
    ),
    _item(
        "C55",
        "E-55",
        "against the claimed direction; comfortably clear of any boundary",
        shift="causal",
        shift_strength=1.6025,
    ),
    _item(
        "C56",
        "E-56",
        "against the claimed direction; comfortably clear of any boundary",
        shift="causal",
        shift_strength=1.8125,
    ),
    _item(
        "C57",
        "E-57",
        "against the claimed direction; comfortably clear of any boundary",
        shift="causal",
        shift_strength=2.0925,
    ),
    _item(
        "C58",
        "E-58",
        "against the claimed direction; comfortably clear of any boundary",
        shift="causal",
        shift_strength=2.4075,
    ),
    _item(
        "C59",
        "E-59",
        "against the claimed direction; comfortably clear of any boundary",
        shift="causal",
        shift_strength=2.775,
    ),
    _item(
        "C60",
        "E-60",
        "against the claimed direction; comfortably clear of any boundary",
        shift="causal",
        shift_strength=3.195,
    ),
)
