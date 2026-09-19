"""An intentionally simple, experimental momentum score.

The score is explicitly *not* scientific: it is a transparent, documented
weighted blend of three explainable components computed from the 7-day
snapshot window:

    momentum = w_stars * stars_growth_pct
             + w_forks * forks_growth_pct
             + w_recency * recency

Component definitions
---------------------
* ``stars_growth_pct`` — relative star growth over the 7-day window:
      (stars_delta_7d / stars_base_7d) * 100
  where ``stars_base_7d`` is the star count at the window start. It rewards
  fast relative growth, not absolute size.
* ``forks_growth_pct`` — the same formula applied to forks.
* ``recency`` — how recently the repository was pushed to:
      1 - min(days_since_last_push / 90, 1)
  clamped to [0, 1]. Rewards active projects; 0 for repos untouched for 90+
  days.

All raw metrics (deltas, base values, per-day rates) remain accessible from
the returned :class:`MomentumScore` so the score is fully explainable.

If the 7-day star window is unavailable the score is ``None`` — we never
fabricate a value from insufficient history.

Replaceability: swap this function (or its weights) freely; nothing else in
the codebase depends on its internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from github_radar.analytics.metrics import RepoMetrics

# How many days of inactivity places recency at 0.
RECENCY_HALF_LIFE_DAYS = 90.0

# Public weights so the score stays explainable and tunable.
WEIGHTS = {"stars_growth": 1.0, "forks_growth": 0.5, "recency": 2.0}

WINDOW_DAYS = 7


@dataclass(frozen=True)
class MomentumScore:
    """The experimental momentum score and its raw components."""

    score: float
    stars_growth_pct: float
    forks_growth_pct: float
    recency: float
    window_days: int
    window_complete: bool
    # Weighted components, for explainability.
    components: dict[str, float]
    weights: dict[str, float]


def _days_between(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / 86400.0


def compute_momentum(
    metrics: RepoMetrics,
    *,
    reference_now: datetime | None = None,
) -> MomentumScore | None:
    """Compute the momentum score, or ``None`` when history is insufficient."""
    stars = metrics.get("stars", WINDOW_DAYS)
    if stars is None or stars.base_value <= 0:
        return None

    stars_growth_pct = stars.delta / stars.base_value * 100.0

    forks = metrics.get("forks", WINDOW_DAYS)
    if forks is not None and forks.base_value > 0:
        forks_growth_pct = forks.delta / forks.base_value * 100.0
    else:
        # No comparable fork baseline; treat forks as neutral rather than
        # excluding the component silently.
        forks_growth_pct = 0.0

    now = reference_now or datetime.now(UTC)
    if metrics.latest_pushed_at is not None:
        days_since_push = max(0.0, _days_between(now, metrics.latest_pushed_at))
        recency = max(0.0, 1.0 - days_since_push / RECENCY_HALF_LIFE_DAYS)
    else:
        recency = 0.0

    components = {
        "stars_growth": WEIGHTS["stars_growth"] * stars_growth_pct,
        "forks_growth": WEIGHTS["forks_growth"] * forks_growth_pct,
        "recency": WEIGHTS["recency"] * recency,
    }
    score = sum(components.values())
    return MomentumScore(
        score=score,
        stars_growth_pct=stars_growth_pct,
        forks_growth_pct=forks_growth_pct,
        recency=recency,
        window_days=WINDOW_DAYS,
        window_complete=stars.window.complete and (forks is not None and forks.window.complete),
        components=components,
        weights=dict(WEIGHTS),
    )


__all__ = [
    "RECENCY_HALF_LIFE_DAYS",
    "WEIGHTS",
    "WINDOW_DAYS",
    "MomentumScore",
    "compute_momentum",
]