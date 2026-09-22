"""Repository trend classification and momentum-based ranking.

Trend classification is deterministic and snapshot-anchored: a repository is
labelled from its 7-day star growth, its recency against the reference instant,
and how much history exists.  Every label carries its raw signals so the
decision is explainable.

Ranking (``rank_repositories``) is the pure core behind the ``trending`` CLI:
repositories with a computable momentum are sorted by score descending.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from github_radar.analytics.confidence import (
    RepositoryReport,
    repo_window_confidence,
)
from github_radar.analytics.metrics import RepoMetrics
from github_radar.analytics.momentum import (
    RECENCY_HALF_LIFE_DAYS,
    WINDOW_DAYS,
    MomentumScore,
)
from github_radar.analytics.recency import recency_score

TrendClass = Literal["rising", "steady", "declining", "inactive", "new"]

# A repository needs at least this many snapshots to be classified.
TREND_MIN_SNAPSHOTS = 2

# Star-growth (percentage points over the window) boundaries.
RISING_GROWTH_PCT = 5.0
DECLINING_GROWTH_PCT = -5.0

# Recency below this is treated as inactive regardless of growth.
INACTIVE_RECENCY = 0.05


@dataclass(frozen=True)
class Trend:
    """A trend label for one repository plus the signals behind it."""

    trend: TrendClass
    label: str
    explanation: str
    growth_pct: float | None
    recency: float


def _recency(metrics: RepoMetrics, reference_now: datetime) -> float:
    return recency_score(
        metrics.latest_pushed_at,
        reference_now=reference_now,
        half_life_days=RECENCY_HALF_LIFE_DAYS,
    )


def classify_trend(
    metrics: RepoMetrics,
    momentum: MomentumScore | None,
    *,
    reference_now: datetime,
) -> Trend:
    """Classify a repository's trajectory from its snapshot series.

    Precedence: too little history → ``new``; a strongly positive/negative
    7-day star growth wins over recency; otherwise a stale repository is
    ``inactive`` and everything else ``steady``.
    """
    recency = momentum.recency if momentum is not None else _recency(metrics, reference_now)
    if len(metrics.series) < TREND_MIN_SNAPSHOTS:
        return Trend(
            trend="new",
            label="New",
            explanation="Fewer than two snapshots; not enough history to classify.",
            growth_pct=None,
            recency=recency,
        )

    growth = metrics.get("stars", WINDOW_DAYS)
    growth_pct = growth.growth_pct if growth is not None else None

    if growth_pct is not None and growth_pct >= RISING_GROWTH_PCT:
        return Trend(
            trend="rising",
            label="Rising",
            explanation=(
                f"7-day star growth of {growth_pct:+.2f}% is at or above "
                f"{RISING_GROWTH_PCT:+.0f}%."
            ),
            growth_pct=growth_pct,
            recency=recency,
        )
    if growth_pct is not None and growth_pct <= DECLINING_GROWTH_PCT:
        return Trend(
            trend="declining",
            label="Declining",
            explanation=(
                f"7-day star growth of {growth_pct:+.2f}% is at or below "
                f"{DECLINING_GROWTH_PCT:+.0f}%."
            ),
            growth_pct=growth_pct,
            recency=recency,
        )
    if recency < INACTIVE_RECENCY:
        return Trend(
            trend="inactive",
            label="Inactive",
            explanation=(
                f"No recent push activity (recency {recency:.2f}, below "
                f"{INACTIVE_RECENCY:.2f})."
            ),
            growth_pct=growth_pct,
            recency=recency,
        )
    if growth_pct is None:
        return Trend(
            trend="steady",
            label="Steady",
            explanation=(
                "No usable star baseline (zero base) and no strong recent "
                "activity; treated as steady."
            ),
            growth_pct=None,
            recency=recency,
        )
    return Trend(
        trend="steady",
        label="Steady",
        explanation=(
            f"7-day star growth of {growth_pct:+.2f}% between "
            f"{DECLINING_GROWTH_PCT:+.0f}% and {RISING_GROWTH_PCT:+.0f}%."
        ),
        growth_pct=growth_pct,
        recency=recency,
    )


@dataclass(frozen=True)
class RepoRanking:
    """One row of the trending table; fully typed raw values."""

    full_name: str
    stars: int | None
    score: float
    stars_growth_pct: float
    forks_growth_pct: float
    recency: float
    confidence: float


def rank_repositories(
    reports: Sequence[RepositoryReport],
) -> list[RepoRanking]:
    """Rank repositories by momentum score, descending.

    Repositories without a computable momentum are excluded (never fabricated).
    Each row also carries the 7-day window confidence
    (:func:`~github_radar.analytics.confidence.repo_window_confidence`) so the
    CLI can show how much history the score rests on.
    """
    ranked: list[RepoRanking] = []
    for report in reports:
        momentum = report.momentum
        if momentum is None:
            continue
        stars = report.metrics.latest_counts.get("stars")
        ranked.append(
            RepoRanking(
                full_name=report.full_name,
                stars=stars,
                score=momentum.score,
                stars_growth_pct=momentum.stars_growth_pct,
                forks_growth_pct=momentum.forks_growth_pct,
                recency=momentum.recency,
                confidence=repo_window_confidence(
                    report.metrics, window_days=WINDOW_DAYS
                ).score,
            )
        )
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked


__all__ = [
    "DECLINING_GROWTH_PCT",
    "INACTIVE_RECENCY",
    "RISING_GROWTH_PCT",
    "RepoRanking",
    "TREND_MIN_SNAPSHOTS",
    "Trend",
    "TrendClass",
    "classify_trend",
    "rank_repositories",
]