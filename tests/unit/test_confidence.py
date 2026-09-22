"""Confidence model: repository windows, levels, topic aggregation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from github_radar.analytics import (
    RepositoryReport,
    TopicConfidenceRule,
    compute_metrics,
    compute_momentum,
    confidence_level_for_repo_window,
    confidence_level_for_topic,
    format_confidence,
    level_for_score,
    repo_window_confidence,
    topic_confidence,
)
from github_radar.domain import RepositorySnapshot

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def snap(*, offset_days: float, stars: int, forks: int = 10) -> RepositorySnapshot:
    return RepositorySnapshot(
        captured_at=T0 + timedelta(days=offset_days),
        stars=stars,
        forks=forks,
        watchers=None,
        open_issues=None,
        size_kb=1,
        pushed_at=T0 + timedelta(days=offset_days),
    )


def make_report(*, name: str, series: list[RepositorySnapshot]) -> RepositoryReport:
    metrics = compute_metrics(series)
    if metrics.latest_captured_at is not None:
        momentum = compute_momentum(metrics, reference_now=metrics.latest_captured_at)
    else:
        momentum = None
    return RepositoryReport(full_name=name, metrics=metrics, momentum=momentum)


def test_empty_metrics_are_low_confidence() -> None:
    metrics = compute_metrics([])
    confidence = repo_window_confidence(metrics, window_days=7)
    assert confidence.level == "LOW"
    assert confidence.score == 0.0


def test_single_snapshot_is_low_confidence() -> None:
    metrics = compute_metrics([snap(offset_days=0, stars=100)])
    confidence = repo_window_confidence(metrics, window_days=7)
    assert confidence.level == "LOW"


def test_observed_zero_growth_is_distinct_from_missing_history() -> None:
    # Two genuine observations with unchanged counters: confidence is computed
    # from the series, so it is NOT LOW merely because the second row exists —
    # the covered 7-day window carries real span/gap terms.
    observed = compute_metrics([snap(offset_days=0, stars=100), snap(offset_days=7, stars=100)])
    observed_confidence = repo_window_confidence(observed, window_days=7)
    assert observed_confidence.level == "HIGH"
    assert observed.stars_7d is not None
    assert observed.stars_7d.delta == 0

    # A single observation is all a missing re-poll leaves behind -> LOW, and
    # no 7-day window at all.
    single = compute_metrics([snap(offset_days=0, stars=100)])
    assert single.stars_7d is None
    assert repo_window_confidence(single, window_days=7).level == "LOW"


def test_complete_covered_window_scores_high() -> None:
    series = [snap(offset_days=d, stars=100 + d) for d in (0, 1, 2, 3, 4, 5, 7)]
    metrics = compute_metrics(series)
    confidence = repo_window_confidence(metrics, window_days=7)
    assert confidence.snapshot_count == 7
    assert confidence.base_gap_days == 0.0
    assert confidence.window_span_days == 7.0
    assert confidence.level == "HIGH"
    assert confidence.score >= 0.75


def test_window_without_base_scores_low() -> None:
    series = [snap(offset_days=0, stars=100), snap(offset_days=1, stars=101)]
    metrics = compute_metrics(series)
    confidence = repo_window_confidence(metrics, window_days=7)
    assert confidence.base_gap_days is None
    assert confidence.window_span_days is None
    assert confidence.level == "LOW"


def test_level_for_score_thresholds() -> None:
    assert level_for_score(0.2) == "LOW"
    assert level_for_score(0.5) == "MEDIUM"
    assert level_for_score(0.8) == "HIGH"


def test_format_confidence() -> None:
    metrics = compute_metrics([snap(offset_days=0, stars=100)])
    rendered = format_confidence(repo_window_confidence(metrics, window_days=7))
    assert rendered.startswith("LOW (")


def test_confidence_level_for_repo_window_helper() -> None:
    series = [snap(offset_days=0, stars=100), snap(offset_days=1, stars=101)]
    metrics = compute_metrics(series)
    assert confidence_level_for_repo_window(metrics, window_days=7) == "LOW"


def test_topic_confidence_empty_reports() -> None:
    rule = topic_confidence([])
    assert rule.repository_count == 0
    assert rule.score == 0.0
    assert rule.level == "LOW"


def test_small_topic_cannot_reach_high() -> None:
    series = [snap(offset_days=d, stars=100 + d) for d in (0, 1, 2, 3, 4, 5, 7)]
    reports = [make_report(name="octo/repo", series=series)]
    rule = topic_confidence(reports, topic_size_floor=10)
    assert rule.repository_count == 1
    assert rule.share_with_momentum == 1.0
    assert rule.share_complete_windows == 1.0
    assert rule.share_partial_windows == 0.0
    assert rule.level != "HIGH"


def test_large_complete_topic_reaches_high() -> None:
    series = [snap(offset_days=d, stars=100 + d) for d in (0, 1, 2, 3, 4, 5, 7)]
    reports = [
        make_report(name=f"octo/repo-{i}", series=series) for i in range(10)
    ]
    rule = TopicConfidenceRule.from_reports(reports, topic_size_floor=10)
    assert rule.repository_count == 10
    assert rule.level == "HIGH"
    assert confidence_level_for_topic(rule) == "HIGH"