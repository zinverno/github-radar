"""Repository trend classification and momentum ranking."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from github_radar.analytics import (
    RepositoryReport,
    Trend,
    classify_trend,
    compute_metrics,
    compute_momentum,
    rank_repositories,
)
from github_radar.domain import RepositorySnapshot

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def snap(
    *,
    offset_days: float,
    stars: int,
    forks: int = 10,
    pushed_at: datetime | None = None,
) -> RepositorySnapshot:
    return RepositorySnapshot(
        captured_at=T0 + timedelta(days=offset_days),
        stars=stars,
        forks=forks,
        watchers=None,
        open_issues=None,
        size_kb=1,
        pushed_at=pushed_at if pushed_at is not None else T0 + timedelta(days=offset_days),
    )


def trend_for(series: list[RepositorySnapshot]) -> Trend:
    metrics = compute_metrics(series)
    momentum = (
        compute_momentum(metrics, reference_now=metrics.latest_captured_at)
        if metrics.latest_captured_at is not None
        else None
    )
    return classify_trend(
        metrics, momentum, reference_now=metrics.latest_captured_at or T0
    )


def test_new_without_enough_history() -> None:
    trend = trend_for([snap(offset_days=0, stars=100)])
    assert trend.trend == "new"


def test_rising_growth() -> None:
    trend = trend_for(
        [snap(offset_days=0, stars=100), snap(offset_days=7, stars=120)]
    )
    assert trend.trend == "rising"
    assert trend.growth_pct == 20.0


def test_declining_growth() -> None:
    trend = trend_for(
        [snap(offset_days=0, stars=100), snap(offset_days=7, stars=93)]
    )
    assert trend.trend == "declining"


def test_inactive_when_stale() -> None:
    stale = snap(offset_days=0, stars=100, pushed_at=T0 - timedelta(days=300))
    recent = snap(offset_days=7, stars=103, pushed_at=T0 - timedelta(days=293))
    trend = trend_for([stale, recent])
    assert trend.trend == "inactive"


def test_steady_flat_growth() -> None:
    trend = trend_for(
        [snap(offset_days=0, stars=100), snap(offset_days=7, stars=102)]
    )
    assert trend.trend == "steady"


def test_ranking_excludes_repos_without_momentum() -> None:
    series = [snap(offset_days=0, stars=100), snap(offset_days=7, stars=140)]
    metrics = compute_metrics(series)
    assert metrics.latest_captured_at is not None
    momentum = compute_momentum(metrics, reference_now=metrics.latest_captured_at)
    assert momentum is not None
    reports = [
        RepositoryReport(full_name="octo/hot", metrics=metrics, momentum=momentum),
        RepositoryReport(
            full_name="octo/bare",
            metrics=compute_metrics([snap(offset_days=0, stars=100)]),
            momentum=None,
        ),
    ]
    ranked = rank_repositories(reports)
    assert [r.full_name for r in ranked] == ["octo/hot"]
    assert all(r.stars is not None for r in ranked)
    assert all(r.score >= 0.0 for r in ranked)
    assert all(0.0 <= r.confidence <= 1.0 for r in ranked)


def test_ranking_sorts_by_score_descending() -> None:
    base = [snap(offset_days=0, stars=100), snap(offset_days=7, stars=140)]
    low = [snap(offset_days=0, stars=100), snap(offset_days=7, stars=150)]
    reports = [
        RepositoryReport(
            full_name="octo/low",
            metrics=compute_metrics(base),
            momentum=compute_momentum(
                compute_metrics(base), reference_now=T0 + timedelta(days=7)
            ),
        ),
        RepositoryReport(
            full_name="octo/high",
            metrics=compute_metrics(low),
            momentum=compute_momentum(
                compute_metrics(low), reference_now=T0 + timedelta(days=7)
            ),
        ),
    ]
    ranked = rank_repositories(reports)
    assert [r.full_name for r in ranked] == ["octo/high", "octo/low"]
    assert ranked[0].score > ranked[1].score