"""Recency metrics: deterministic age/staleness against a reference time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from github_radar.analytics.recency import (
    compute_recency,
    days_between,
    recency_score,
)

T0 = datetime(2024, 1, 1, tzinfo=UTC)
NOW = T0 + timedelta(days=30)


def test_days_between_never_negative() -> None:
    assert days_between(NOW, T0) == 30.0
    assert days_between(T0, NOW) == 0.0


def test_recency_score_bounds() -> None:
    assert recency_score(T0, reference_now=T0, half_life_days=90.0) == 1.0
    assert recency_score(None, reference_now=NOW, half_life_days=90.0) == 0.0
    assert (
        recency_score(T0, reference_now=NOW, half_life_days=90.0)
        == 1.0 - 30.0 / 90.0
    )
    assert recency_score(
        NOW - timedelta(days=200), reference_now=NOW, half_life_days=90.0
    ) == 0.0


def test_compute_recency_is_deterministic() -> None:
    metrics = compute_recency(
        created_at=T0,
        updated_at=T0 + timedelta(days=5),
        pushed_at=T0 + timedelta(days=10),
        reference_now=NOW,
    )
    assert metrics.reference_now == NOW
    assert metrics.repository_age_days == 30.0
    assert metrics.days_since_github_update == 25.0
    assert metrics.days_since_push == 20.0


def test_compute_recency_falls_back_to_pushed_at() -> None:
    metrics = compute_recency(
        created_at=None,
        updated_at=None,
        pushed_at=T0,
        reference_now=NOW,
    )
    assert metrics.days_since_push == 30.0


def test_compute_recency_prefers_latest_pushed_at() -> None:
    metrics = compute_recency(
        created_at=None,
        updated_at=None,
        pushed_at=T0,
        latest_pushed_at=T0 + timedelta(days=28),
        reference_now=NOW,
    )
    assert metrics.days_since_push == pytest.approx(2.0, abs=1e-6)
    assert metrics.pushed_at == T0 + timedelta(days=28)