"""Momentum score tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from github_radar.analytics import WEIGHTS, RepoMetrics, compute_metrics, compute_momentum
from github_radar.domain import RepositorySnapshot

T0 = datetime(2024, 1, 1, tzinfo=UTC)
NOW = T0 + timedelta(days=30)


def snap(
    *,
    days_from_origin: float,
    stars: int,
    forks: int,
    pushed_at: datetime | None = T0,
) -> RepositorySnapshot:
    return RepositorySnapshot(
        captured_at=T0 + timedelta(days=days_from_origin),
        stars=stars,
        forks=forks,
        watchers=None,
        open_issues=None,
        size_kb=10,
        pushed_at=pushed_at,
    )


def make_metrics(series: list[RepositorySnapshot]) -> RepoMetrics:
    return compute_metrics(series)


def test_momentum_full_window() -> None:
    series = [
        snap(days_from_origin=0, stars=100, forks=10),
        snap(days_from_origin=8, stars=150, forks=15, pushed_at=NOW - timedelta(days=2)),
    ]
    score = compute_momentum(make_metrics(series), reference_now=NOW)
    assert score is not None
    assert score.stars_growth_pct == 50.0  # +50 stars over base 100
    assert score.forks_growth_pct == 50.0  # +5 over base 10
    # recency: age 2 days → 1 - 2/90
    assert score.recency == pytest.approx(1 - 2 / 90, abs=1e-6)
    expected = (
        WEIGHTS["stars_growth"] * 50.0
        + WEIGHTS["forks_growth"] * 50.0
        + WEIGHTS["recency"] * score.recency
    )
    assert score.score == pytest.approx(expected, abs=1e-6)
    # Components are explainable.
    assert score.components["stars_growth"] == pytest.approx(WEIGHTS["stars_growth"] * 50.0)


def test_momentum_none_without_window() -> None:
    series = [snap(days_from_origin=0, stars=100, forks=10)]
    assert compute_momentum(make_metrics(series), reference_now=NOW) is None


def test_momentum_none_when_base_zero() -> None:
    series = [
        snap(days_from_origin=0, stars=0, forks=0),
        snap(days_from_origin=8, stars=10, forks=1),
    ]
    assert compute_momentum(make_metrics(series), reference_now=NOW) is None


def test_recency_zero_when_no_push() -> None:
    series = [
        snap(days_from_origin=0, stars=100, forks=10, pushed_at=None),
        snap(days_from_origin=8, stars=150, forks=15, pushed_at=None),
    ]
    score = compute_momentum(make_metrics(series), reference_now=NOW)
    assert score is not None
    assert score.recency == 0.0


def test_recency_clamped_at_zero() -> None:
    series = [
        snap(days_from_origin=0, stars=100, forks=10, pushed_at=T0 - timedelta(days=200)),
        snap(days_from_origin=8, stars=150, forks=15, pushed_at=T0 - timedelta(days=190)),
    ]
    score = compute_momentum(make_metrics(series), reference_now=NOW)
    assert score is not None
    assert score.recency == 0.0