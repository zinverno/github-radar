"""Topic momentum aggregation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from github_radar.analytics import (
    RepositoryReport,
    aggregate_topics,
    compute_metrics,
    compute_momentum,
)
from github_radar.domain import RepositorySnapshot

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def snap(
    *, offset_days: float, stars: int, forks: int = 10
) -> RepositorySnapshot:
    return RepositorySnapshot(
        captured_at=T0 + timedelta(days=offset_days),
        stars=stars,
        forks=forks,
        watchers=None,
        open_issues=None,
        size_kb=1,
        pushed_at=T0 + timedelta(days=offset_days),
    )


def report(name: str, growth: int) -> RepositoryReport:
    series = [snap(offset_days=0, stars=100), snap(offset_days=7, stars=100 + growth)]
    metrics = compute_metrics(series)
    assert metrics.latest_captured_at is not None
    momentum = compute_momentum(metrics, reference_now=metrics.latest_captured_at)
    return RepositoryReport(full_name=name, metrics=metrics, momentum=momentum)


def test_empty_mapping_yields_no_aggregates() -> None:
    assert aggregate_topics({}) == []


def test_aggregate_totals_averages_and_shares() -> None:
    hot = report("octo/hot", 50)
    mid = report("octo/mid", 20)
    by_topic = {
        "ai": [hot, mid],
        "mcp": [report("octo/only", 30)],
    }
    aggregates = {a.name: a for a in aggregate_topics(by_topic)}
    assert set(aggregates) == {"ai", "mcp"}

    ai = aggregates["ai"]
    assert ai.repository_count == 2
    assert ai.total_momentum > 0.0
    assert hot.momentum is not None and mid.momentum is not None
    assert ai.total_momentum == hot.momentum.score + mid.momentum.score
    assert ai.avg_momentum == ai.total_momentum / 2
    assert ai.share_with_momentum == 1.0
    assert 0.0 <= ai.confidence_score <= 1.0
    assert ai.confidence_level in {"LOW", "MEDIUM", "HIGH"}

    mcp = aggregates["mcp"]
    assert mcp.repository_count == 1


def test_aggregates_sorted_by_total_momentum_descending() -> None:
    by_topic = {
        "small": [report("octo/a", 10)],
        "big": [report("octo/b", 50), report("octo/c", 30)],
    }
    names = [a.name for a in aggregate_topics(by_topic)]
    assert names == ["big", "small"]


def test_repos_without_momentum_count_but_add_zero() -> None:
    bare = RepositoryReport(
        full_name="octo/bare",
        metrics=compute_metrics([snap(offset_days=0, stars=100)]),
        momentum=None,
    )
    assert bare.momentum is None
    aggregates = aggregate_topics({"mcp": [bare]})
    mcp = aggregates[0]
    assert mcp.repository_count == 1
    assert mcp.total_momentum == 0.0
    assert mcp.avg_momentum == 0.0
    assert mcp.share_with_momentum == 0.0