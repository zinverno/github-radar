"""Repository trend classification and momentum ranking."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from github_radar.analytics import (
    RepositoryReport,
    Trend,
    classify_trend,
    compute_metrics,
    compute_momentum,
    rank_repositories,
    repo_window_confidence,
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


@pytest.mark.parametrize(
    "days,complete", [(7, True), (8, True), (2, False), (0.25, False), (12, False)]
)
@pytest.mark.parametrize("stars,label", [(120, "rising"), (80, "declining"), (100, "steady")])
def test_trend_reports_actual_interval_without_changing_classification(
    days, complete, stars, label
):
    trend = trend_for([snap(offset_days=0, stars=100), snap(offset_days=days, stars=stars)])
    assert trend.trend == label
    assert trend.window_days == 7
    assert trend.window is not None
    assert trend.window.span_days == days
    assert trend.window.complete is complete
    assert trend.window.start == T0
    assert trend.window.end == T0 + timedelta(days=days)
    assert f"over {days:g} observed days" in trend.explanation
    assert "requested 7 days" in trend.explanation
    assert ("partial coverage" in trend.explanation) is not complete
    assert "7-day star growth" not in trend.explanation
    assert trend.growth_pct == stars - 100
    metrics = compute_metrics([
        snap(offset_days=0, stars=100), snap(offset_days=days, stars=stars)
    ])
    momentum = compute_momentum(metrics, reference_now=T0 + timedelta(days=days))
    assert momentum is not None and momentum.score == (2 if stars == 120 else 1)
    if days == 7:
        assert repo_window_confidence(metrics, window_days=7).score == pytest.approx(0.796)
    elif days < 7:
        assert repo_window_confidence(metrics, window_days=7).score == 0


def test_missing_window_is_not_observed_zero():
    missing = trend_for([snap(offset_days=0, stars=100)])
    zero = trend_for([snap(offset_days=0, stars=100), snap(offset_days=2, stars=100)])
    assert missing.growth_pct is None and missing.window is None
    assert zero.growth_pct == 0 and zero.window is not None
    assert "partial coverage" in zero.explanation


def test_zero_baseline_and_inactive_trends_still_expose_partial_coverage():
    zero_base = trend_for([snap(offset_days=0, stars=0), snap(offset_days=2, stars=1)])
    inactive = trend_for([
        snap(offset_days=0, stars=100, pushed_at=T0 - timedelta(days=300)),
        snap(offset_days=2, stars=100, pushed_at=T0 - timedelta(days=300)),
    ])
    assert zero_base.growth_pct is None
    assert "unavailable" in zero_base.explanation.lower()
    assert inactive.trend == "inactive"
    assert all("partial coverage" in t.explanation for t in (zero_base, inactive))


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
