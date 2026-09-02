"""M23: a switch is not connected until it changes what the machine does.

M22's probe asks whether a declared switch reaches the kernel — whether flipping
it changes the :class:`~nullius.kernel.Mechanisms` the arm translates to. It
found ``reviewer``, which reaches nothing.

``conservative_escalation`` passes that probe. It is a field on ``Mechanisms``,
so flipping it changes the object, and the kernel reads it and hands it to
:meth:`~nullius.kernel.Kernel._replicate`. There it stops: the parameter is
accepted and never used, and neither call to ``_escalate`` passed it at all. So
the switch crossed every boundary a structural check watches and still did
nothing, and protocol v6 adjudicated a prediction about conservative sizing
against an arm that sized exactly like the arm it was being compared to.

Two checks, because neither can do the other's job:

* :func:`test_the_conservative_arm_buys_more_seeds_than_the_point_estimate_arm`
  runs both arms and compares what they bought. Only an execution can prove a
  switch is connected; nothing static can.
* :func:`test_no_kernel_method_accepts_an_argument_it_ignores` is the cheap
  guard that would have caught this before the ladder ran, and it runs in
  milliseconds rather than minutes.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from nullius.bank.items import BANK_V2
from nullius.bank.lock import V2_LOCK_PATH
from nullius.benchmark.arms import arm_named
from nullius.benchmark.runner import run_arm
from nullius.design.power import seeds_to_resolve

#: Two items the v6 ladder escalated past the mandatory five and well short of
#: the ceiling, so there is room in both directions for the sizing rule to show
#: itself. An item clamped at ``adaptive_seed_ceiling`` would buy the same
#: number under either rule and prove nothing.
ESCALATING_ITEMS = ("C27", "C53")


@pytest.mark.slow
def test_the_conservative_arm_buys_more_seeds_than_the_point_estimate_arm(
    tmp_path: Path,
) -> None:
    """The test that would have caught it, and the only kind that could.

    B8 and B9 differ in one boolean. Under the bug they bought identical seeds
    on all one hundred and eighty outcomes of the v6 ladder — not similar
    numbers, identical ones, which is what a switch that goes nowhere looks
    like from the outside.
    """
    items = [item for item in BANK_V2 if item.item_id in ESCALATING_ITEMS]
    bought = {}
    for arm_id in ("B8", "B9"):
        run = run_arm(
            arm_named(arm_id),
            database=tmp_path / f"{arm_id}.sqlite",
            workroot=tmp_path / arm_id,
            items=items,
            truth_lock=V2_LOCK_PATH,
        )
        bought[arm_id] = sum(outcome.n_seeds for outcome in run.outcomes)

    assert bought["B9"] > bought["B8"], (
        "conservative sizing bought no more data than the point estimate; the "
        f"switch is declared and not connected (B8 {bought['B8']}, B9 {bought['B9']})"
    )


def test_the_upper_bound_asks_for_more_seeds_than_the_point_estimate() -> None:
    """The unit underneath, so a failure above can be localised.

    ``seeds_to_resolve`` was correct throughout; M17 built and measured it. What
    was wrong was the wiring, and keeping this assertion separate is what makes
    the distinction visible rather than leaving one failing test to mean either.
    """
    shared = {"estimate": 0.0020, "sd": 0.00348, "mde": 0.005}
    assert seeds_to_resolve(**shared, observations=5) > seeds_to_resolve(**shared)


def test_no_kernel_method_accepts_an_argument_it_ignores() -> None:
    """A parameter bound and never read is a wire that ends in the air.

    Enforced by lint across the whole package, and asserted here as well because
    this is where it cost something: the kernel is the one module where an
    ignored argument is silently a mechanism that does not exist. Neither check
    replaces the execution above — an argument can be read, passed on, and still
    reach nothing — but this one is the one that runs before a six-hour ladder
    rather than after it.
    """
    import nullius.kernel

    tree = ast.parse(Path(inspect.getfile(nullius.kernel)).read_text(encoding="utf-8"))
    ignored: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        args = node.args
        declared = {
            arg.arg
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)
            if arg.arg not in ("self", "cls") and not arg.arg.startswith("_")
        }
        used = {
            inner.id
            for inner in ast.walk(node)
            if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load)
        }
        # A nested def can close over the outer function's parameters, so the
        # names it loads count as uses of them.
        ignored += [f"{node.name}({name})" for name in sorted(declared - used)]

    assert not ignored, f"kernel arguments accepted and never read: {ignored}"
