"""Scoring the Forecast Ledger.

Every role predicts before the experiment runs; this scores what they said
against what happened. Two proper scoring rules, both minimised by honesty:

**Brier score** for the binary question "will the effect exceed the claimed
size?" — the squared error of a probability. Confident and wrong is punished
quadratically; hedging everything to one half scores a flat 0.25 forever,
which is the point. A role cannot look calibrated by refusing to commit.

**CRPS** for the predictive distribution over the effect itself, computed in
closed form for the Gaussian each role states. It rewards a narrow
distribution centred on the truth and penalises a narrow one centred
elsewhere, so a role cannot buy a good score with false precision.

Scores live in their own table. The forecast row stays strictly append-only,
because a prediction that could be edited after the result is in measures
nothing — and if the score lived on the forecast row, that row would have to
be updatable.
"""

from __future__ import annotations

import math
import uuid

import sqlalchemy as sa
from scipy import stats

from nullius.db.enums import Role
from nullius.db.tables import Forecast, ForecastScore
from nullius.repository import Repository

__all__ = ["brier_score", "crps_gaussian", "score_forecasts"]


def brier_score(probability: float, occurred: bool) -> float:
    """Squared error of a probability. Lower is better; 0.25 is a coin flip."""
    return (probability - (1.0 if occurred else 0.0)) ** 2


def crps_gaussian(mean: float, sd: float, observed: float) -> float:
    """Continuous ranked probability score for a Gaussian forecast.

    Closed form: ``sd * (z(2Φ(z) - 1) + 2φ(z) - 1/√π)`` where ``z`` is the
    standardised observation. Used rather than a sampled approximation so the
    score is exact and reproducible.
    """
    if sd <= 0:
        raise ValueError("a predictive distribution needs positive spread")

    z = (observed - mean) / sd
    return float(
        sd * (z * (2 * stats.norm.cdf(z) - 1) + 2 * stats.norm.pdf(z) - 1 / math.sqrt(math.pi))
    )


def score_forecasts(
    repo: Repository,
    *,
    registration_id: uuid.UUID,
    realised_effect: float,
    mde: float,
    program_id: uuid.UUID | None = None,
) -> list[ForecastScore]:
    """Score every forecast made about one registration.

    Written directly rather than through the repository's event path: these
    are the only columns on an append-only table that are meant to be filled
    in later, and they are filled exactly once. The forecast itself — the part
    that could be gamed — remains immutable.
    """
    forecasts = list(
        repo.session.scalars(sa.select(Forecast).where(Forecast.registration_id == registration_id))
    )
    exceeded = realised_effect >= mde
    system = repo.as_role(Role.SYSTEM)

    scores: list[ForecastScore] = []
    for forecast in forecasts:
        if repo.session.get(ForecastScore, forecast.forecast_id) is not None:
            continue  # judged already; a forecast is scored once
        scores.append(
            system.record_forecast_score(
                forecast_id=forecast.forecast_id,
                registration_id=registration_id,
                role=forecast.role,
                brier_score=brier_score(forecast.p_effect_exceeds_mde, exceeded),
                crps=crps_gaussian(
                    forecast.predictive_mean, forecast.predictive_sd, realised_effect
                ),
                realised_effect=realised_effect,
                exceeded_mde=exceeded,
                program_id=program_id,
            )
        )
    return scores
