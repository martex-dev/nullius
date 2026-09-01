"""Power analysis for the paired, seed-replicated designs Nullius runs.

Every experiment here compares two arms on the same seeds, so the unit of
analysis is the *paired difference* per seed and the relevant test is a paired
t-test on ``n_seeds`` observations.

This exists so that "underpowered" is a statement made **before** the run, not
an excuse offered after it. A design that cannot detect the effect it claims
to care about is a design that will produce an uninterpretable null, and
`docs/01-critique.md` F16 is about exactly that: nulls that mean "we didn't
look hard enough" contaminating a bank where half the true effects are zero.

Nothing here is ever computed by a language model.
"""

from __future__ import annotations

from scipy import stats

__all__ = [
    "minimum_detectable_effect",
    "power_for",
    "required_seeds",
    "seeds_to_resolve",
]

DEFAULT_ALPHA = 0.05


def power_for(*, effect: float, sd: float, n: int, alpha: float = DEFAULT_ALPHA) -> float:
    """Probability of detecting ``effect`` with ``n`` paired observations.

    Two-sided paired t-test, via the non-central t distribution — not the
    normal approximation, which is optimistic at the sample sizes this project
    actually runs (5 to 20 seeds).
    """
    if n < 2 or sd <= 0:
        return 0.0
    if effect <= 0:
        return alpha

    df = n - 1
    noncentrality = (effect / sd) * (n**0.5)
    critical = stats.t.ppf(1 - alpha / 2, df)

    upper = float(stats.nct.sf(critical, df, noncentrality))
    lower = float(stats.nct.cdf(-critical, df, noncentrality))
    return min(1.0, upper + lower)


def minimum_detectable_effect(
    *, sd: float, n: int, alpha: float = DEFAULT_ALPHA, power: float = 0.8
) -> float:
    """The smallest effect this design can find at ``power``.

    Solved by bisection rather than a closed form: the non-central t has no
    tidy inverse in the effect, and a few dozen iterations cost nothing at
    these sizes.
    """
    if n < 2 or sd <= 0:
        return float("inf")

    low, high = 0.0, sd * 10.0
    for _ in range(80):
        mid = (low + high) / 2
        if power_for(effect=mid, sd=sd, n=n, alpha=alpha) < power:
            low = mid
        else:
            high = mid
    return high


def required_seeds(
    *, effect: float, sd: float, alpha: float = DEFAULT_ALPHA, power: float = 0.8, cap: int = 200
) -> int:
    """Seeds needed to detect ``effect``, or ``cap`` if it is out of reach.

    Used by the Designer to propose a defensible ``n_seeds`` rather than
    guessing, and by the Skeptic to argue that a null was underpowered.
    """
    if effect <= 0 or sd <= 0:
        return cap
    for n in range(2, cap + 1):
        if power_for(effect=effect, sd=sd, n=n, alpha=alpha) >= power:
            return n
    return cap


def seeds_to_resolve(
    *,
    estimate: float,
    sd: float,
    mde: float,
    null_band: float = 0.5,
    alpha: float = DEFAULT_ALPHA,
    cap: int = 200,
) -> int:
    """Seeds needed for the interval to land wholly inside one verdict region.

    Power analysis asks whether an effect can be *detected*. That is not what
    this system's verdicts require. To answer ``no_effect`` the interval must
    fit inside the null band; to answer ``supported`` it must clear the claimed
    effect entirely. Both are exclusion problems, and a design powered to
    detect ``mde`` can still be unable to exclude ``mde/2`` — which is what
    ``nullius benchmark run --bank 3`` measured: a quarter of every arm's
    answers were abstentions, at a seed count the linter had passed as
    adequately powered.

    ``estimate`` is the effect measured on the **development** split, which the
    experiment is entitled to see. It never touches the evaluation split, so
    escalating on it cannot be optional stopping on the quantity the verdict is
    computed from — the Custodian is queried once, afterwards, over whatever
    seeds this returns.

    Returns ``cap`` when the target is out of reach, which is a real answer:
    some questions cannot be settled at any seed count this project will pay
    for, and the honest response is to abstain rather than to keep buying
    seeds.
    """
    if sd <= 0 or mde <= 0:
        return cap
    edge = null_band * mde
    size = abs(estimate)

    if size < edge:
        margin = edge - size
    elif size > mde:
        margin = size - mde
    else:
        margin = min(size - edge, mde - size)

    if margin <= 0:
        return cap
    for n in range(3, cap + 1):
        half_width = float(stats.t.ppf(1 - alpha / 2, n - 1)) * sd / n**0.5
        if half_width < margin:
            return n
    return cap
