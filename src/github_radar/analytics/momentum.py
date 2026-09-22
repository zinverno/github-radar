"""Deterministic, explainable repository momentum.

Momentum is a weighted sum of three explainable, deterministic components
derived from snapshot history (see :mod:`github_radar.analytics.metrics`):

* ``stars_growth`` — relative star growth over ``WINDOW_DAYS`` as a
  percentage-point value, capped at ``MAX_GROWTH_PCT``.
* ``forks_growth`` — relative fork growth over the same window, capped the
  same way.
* ``recency`` — a freshness factor in ``[0, 1]`` that decays linearly from
  ``1.0`` (pushed at the reference instant) to ``0.0`` over
  ``RECENCY_HALF_LIFE_DAYS``.

Each component contributes ``WEIGHTS[key] * value`` points, so the value is
simply "how many points this explainable factor contributes".  Because every
growth value is capped at ``MAX_GROWTH_PCT`` and recency is bounded to
``[0, 1]``, each component — and therefore the whole score — has a hard,
documented maximum: ``MAX_SCORE``.

Everything is anchored on snapshot ``captured_at``/``pushed_at`` timestamps and
the caller-supplied ``reference_now``; the wall clock is never consulted, so
the result is reproducible between runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from github_radar.analytics.metrics import RepoMetrics
from github_radar.analytics.recency import recency_score

# Relative growth is capped at this many percentage points so the component
# contribution stays bounded.
MAX_GROWTH_PCT = 100.0

# Recency decays to zero over this many days since the last push.
RECENCY_HALF_LIFE_DAYS = 90.0

# Component weights ("points per unit of value").  A growth unit is one
# percentage point; a recency unit is one unit of freshness in ``[0, 1]``.
WEIGHTS = {
    "stars_growth": 0.05,
    "forks_growth": 0.03,
    "recency": 1.0,
}

MAX_SCORE = (
    WEIGHTS["stars_growth"] * MAX_GROWTH_PCT
    + WEIGHTS["forks_growth"] * MAX_GROWTH_PCT
    + WEIGHTS["recency"] * 1.0
)

# Growth window (days) used to score momentum.
WINDOW_DAYS = 7


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _growth_value(delta: int | None, base: int | None) -> float:
    """Relative growth as a capped percentage-point value.

    Returns ``0.0`` when the base is missing or zero (no meaningful growth) and
    caps the relative growth at ``MAX_GROWTH_PCT`` percentage points.
    """
    if delta is None or base is None or base <= 0:
        return 0.0
    value = delta / base * 100.0
    return _clamp(value, 0.0, MAX_GROWTH_PCT)


def _recency_value(metrics: RepoMetrics, reference_now: datetime) -> float:
    """Freshness in ``[0, 1]`` from the last push vs. ``reference_now``.

    Delegates to :func:`github_radar.analytics.recency.recency_score` so the
    decay definition is shared with trend classification.
    """
    return recency_score(
        metrics.latest_pushed_at,
        reference_now=reference_now,
        half_life_days=RECENCY_HALF_LIFE_DAYS,
    )


@dataclass(frozen=True)
class MomentumScore:
    """The explainable momentum score for one repository."""

    score: float
    stars_growth_pct: float
    forks_growth_pct: float
    recency: float
    components: dict[str, float]

    @property
    def max_score(self) -> float:
        return MAX_SCORE

    @property
    def within_bounds(self) -> bool:
        return 0.0 <= self.score <= MAX_SCORE


def compute_momentum(
    metrics: RepoMetrics,
    *,
    reference_now: datetime,
) -> MomentumScore | None:
    """Score repo momentum, or ``None`` when the growth window is unusable.

    Growth is anchored on the exact windowed deltas in ``metrics``; a score
    requires a usable base for both stars and forks over ``WINDOW_DAYS``.
    When a component cannot be determined (missing delta, zero base), momentum
    is not fabricated - ``None`` is returned instead.
    """
    stars = metrics.get("stars", WINDOW_DAYS)
    forks = metrics.get("forks", WINDOW_DAYS)
    if stars is None or forks is None:
        return None
    if stars.delta is None or stars.base_value is None or stars.base_value <= 0:
        return None
    if forks.delta is None or forks.base_value is None or forks.base_value <= 0:
        return None

    stars_growth_pct = _growth_value(stars.delta, stars.base_value)
    forks_growth_pct = _growth_value(forks.delta, forks.base_value)
    recency = _recency_value(metrics, reference_now)

    components = {
        "stars_growth": WEIGHTS["stars_growth"] * stars_growth_pct,
        "forks_growth": WEIGHTS["forks_growth"] * forks_growth_pct,
        "recency": WEIGHTS["recency"] * recency,
    }
    return MomentumScore(
        score=sum(components.values()),
        stars_growth_pct=stars_growth_pct,
        forks_growth_pct=forks_growth_pct,
        recency=recency,
        components=components,
    )


__all__ = [
    "MAX_GROWTH_PCT",
    "MAX_SCORE",
    "RECENCY_HALF_LIFE_DAYS",
    "WEIGHTS",
    "WINDOW_DAYS",
    "MomentumScore",
    "compute_momentum",
]
