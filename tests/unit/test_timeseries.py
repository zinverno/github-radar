"""SnapshotSeries time-series utilities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from github_radar.analytics.timeseries import SnapshotSeries
from github_radar.domain import RepositorySnapshot

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def snap(*, offset_days: float, stars: int) -> RepositorySnapshot:
    return RepositorySnapshot(
        captured_at=T0 + timedelta(days=offset_days),
        stars=stars,
        forks=0,
        watchers=None,
        open_issues=None,
        size_kb=1,
        pushed_at=None,
    )


def test_series_sorts_and_dedupes_by_captured_at() -> None:
    series = SnapshotSeries(
        [snap(offset_days=5, stars=2), snap(offset_days=0, stars=1), snap(offset_days=5, stars=9)]
    )
    assert len(series) == 2
    assert [s.stars for s in series] == [1, 9]
    assert series.first is not None and series.first.stars == 1
    assert series.last is not None and series.last.stars == 9


def test_at_or_before_and_at_or_after() -> None:
    series = SnapshotSeries(
        [snap(offset_days=0, stars=1), snap(offset_days=3, stars=2), snap(offset_days=6, stars=3)]
    )
    before = series.at_or_before(T0 + timedelta(days=2))
    assert before is not None and before.stars == 1
    exact = series.at_or_before(T0 + timedelta(days=3))
    assert exact is not None and exact.stars == 2
    assert series.at_or_before(T0 - timedelta(days=1)) is None
    after = series.at_or_after(T0 + timedelta(days=5))
    assert after is not None and after.stars == 3
    assert series.at_or_after(T0 + timedelta(days=99)) is None


def test_previous_of() -> None:
    series = SnapshotSeries(
        [snap(offset_days=0, stars=1), snap(offset_days=3, stars=2)]
    )
    newest = series.at_or_before(T0 + timedelta(days=3))
    assert newest is not None
    previous = series.previous_of(newest)
    assert previous is not None and previous.stars == 1


def test_nearest_to_resolves_ties_toward_older() -> None:
    series = SnapshotSeries(
        [snap(offset_days=0, stars=1), snap(offset_days=10, stars=2)]
    )
    match = series.nearest_to(T0 + timedelta(days=5))
    assert match is not None
    # Both candidates are 5 days away; the older snapshot wins.
    assert match.snapshot.stars == 1
    assert match.gap == timedelta(days=5)


def test_empty_series_lookups() -> None:
    series = SnapshotSeries([])
    assert len(series) == 0
    assert series.first is None
    assert series.last is None
    assert series.at_or_before(T0) is None
    assert series.at_or_after(T0) is None
    assert series.nearest_to(T0) is None