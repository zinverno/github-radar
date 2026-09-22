"""Deterministic analytics: deltas, windows, per-day rates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from github_radar.analytics import (
    compute_metrics,
    compute_momentum,
    sorted_snapshots,
)
from github_radar.domain import RepositorySnapshot

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def snap(
    *,
    days_from_origin: float,
    stars: int,
    forks: int = 0,
    watchers: int | None = None,
    open_issues: int | None = None,
) -> RepositorySnapshot:
    return RepositorySnapshot(
        captured_at=T0 + timedelta(days=days_from_origin),
        stars=stars,
        forks=forks,
        watchers=watchers,
        open_issues=open_issues,
        size_kb=100,
        pushed_at=None,
    )


def test_sorted_snapshots_orders_and_dedupes() -> None:
    series = [snap(days_from_origin=5, stars=2), snap(days_from_origin=0, stars=1)]
    ordered = sorted_snapshots([series[0], series[1], series[0]])
    assert [s.stars for s in ordered] == [1, 2]
    assert [s.captured_at for s in ordered] == sorted(
        s.captured_at for s in ordered
    )


def test_complete_7d_window_delta() -> None:
    series = [
        snap(days_from_origin=0, stars=100, forks=10),
        snap(days_from_origin=2, stars=110, forks=12),
        snap(days_from_origin=8, stars=130, forks=15),
    ]
    metrics = compute_metrics(series)
    seven = metrics.stars_7d
    assert seven is not None
    # Base is the newest snapshot older than the 7-day cutoff (day 1 → day 0).
    assert seven.delta == 30
    assert seven.base_value == 100
    assert seven.current_value == 130
    assert seven.window.complete is True
    assert seven.window.span_days == pytest.approx(8.0, abs=1e-6)
    assert seven.per_day == pytest.approx(30 / 8.0, abs=1e-6)


def test_incomplete_window_uses_earliest_available() -> None:
    series = [
        snap(days_from_origin=0, stars=100),
        snap(days_from_origin=1, stars=105),
    ]
    metrics = compute_metrics(series)
    seven = metrics.stars_7d
    assert seven is not None
    assert seven.delta == 5
    assert seven.window.complete is False
    assert seven.window.span_days == pytest.approx(1.0, abs=1e-6)


def test_single_snapshot_yields_no_deltas() -> None:
    metrics = compute_metrics([snap(days_from_origin=0, stars=100)])
    assert metrics.stars_7d is None
    assert metrics.stars_1d is None
    assert metrics.stars_30d is None
    assert metrics.latest_counts["stars"] == 100


def test_empty_snapshot_series() -> None:
    metrics = compute_metrics([])
    assert metrics.stars_7d is None
    assert metrics.latest_pushed_at is None


def test_forks_and_watchers_deltas() -> None:
    series = [
        snap(days_from_origin=0, stars=100, forks=10, watchers=5),
        snap(days_from_origin=8, stars=130, forks=15, watchers=8),
    ]
    metrics = compute_metrics(series)
    assert metrics.forks_7d is not None
    assert metrics.forks_7d.delta == 5
    watchers_delta = metrics.get("watchers", 7)
    assert watchers_delta is not None
    assert watchers_delta.delta == 3


def test_30d_absent_when_history_too_short() -> None:
    series = [
        snap(days_from_origin=0, stars=100),
        snap(days_from_origin=2, stars=110),
    ]
    metrics = compute_metrics(series)
    assert metrics.stars_30d is not None  # incomplete window, still reportable
    assert metrics.stars_30d.window.complete is False


def test_unchanged_observation_at_both_ends_is_true_zero_growth() -> None:
    """A genuinely observed, unchanged repo yields a *valid* zero delta."""
    series = [
        snap(days_from_origin=0, stars=100, forks=10),
        snap(days_from_origin=7, stars=100, forks=10),
    ]
    metrics = compute_metrics(series)
    seven = metrics.stars_7d
    assert seven is not None
    assert seven.delta == 0
    assert seven.growth_pct == 0.0
    assert seven.per_day == 0.0
    assert seven.window.complete is True
    assert seven.window.span_days == pytest.approx(7.0, abs=1e-6)

    momentum = compute_momentum(metrics, reference_now=T0 + timedelta(days=7))
    assert momentum is not None
    assert momentum.stars_growth_pct == 0.0


def test_missing_observation_is_unavailable_not_zero() -> None:
    """One observation only: the window is *missing*, never a fabricated zero."""
    metrics = compute_metrics([snap(days_from_origin=0, stars=100, forks=10)])
    assert metrics.stars_7d is None
    assert metrics.stars_30d is None
    momentum = compute_momentum(metrics, reference_now=T0)
    assert momentum is None