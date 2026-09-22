"""Deterministic growth metrics computed from snapshot series.

All computations anchor on ``captured_at`` (the observation time), never on
wall-clock time, so results are reproducible between runs without a new
snapshot.  Each window's delta uses the newest snapshot at-or-before the
window target as its base; when no snapshot is old enough the window uses
the earliest available snapshot and is reported as *incomplete* rather than
fabricated.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from github_radar.analytics.timeseries import SnapshotSeries
from github_radar.domain import RepositorySnapshot

# The fields whose growth we report.
DELTA_FIELDS = ("stars", "forks", "watchers", "open_issues")

# The windows (in days) over which growth is reported.
DELTA_WINDOWS_DAYS = (1, 7, 30)

# A window base is only "complete" when its gap from the target is at most this
# fraction of the window.
MAX_GAP_FRACTION = 0.5

_HOURS_PER_DAY = 24.0


@dataclass(frozen=True)
class TimeWindow:
    """The actual window a delta spans, and whether it is complete."""

    span_days: float
    complete: bool
    start: datetime | None
    end: datetime | None


@dataclass(frozen=True)
class FieldDelta:
    """Change of a single field over a window (base → current)."""

    field: str
    window_days: int
    base_value: int | None
    current_value: int | None
    delta: int | None
    per_day: float | None
    window: TimeWindow
    growth_pct: float | None
    base_gap_hours: float | None


@dataclass(frozen=True)
class RepoMetrics:
    """Deterministic metrics for one repository from its snapshot series."""

    latest_captured_at: datetime | None
    latest_pushed_at: datetime | None
    latest_counts: dict[str, int | None]
    deltas: dict[tuple[str, int], FieldDelta]
    series: SnapshotSeries

    def get(self, field: str, days: int) -> FieldDelta | None:
        return self.deltas.get((field, days))

    @property
    def stars_1d(self) -> FieldDelta | None:
        return self.get("stars", 1)

    @property
    def stars_7d(self) -> FieldDelta | None:
        return self.get("stars", 7)

    @property
    def stars_30d(self) -> FieldDelta | None:
        return self.get("stars", 30)

    @property
    def forks_1d(self) -> FieldDelta | None:
        return self.get("forks", 1)

    @property
    def forks_7d(self) -> FieldDelta | None:
        return self.get("forks", 7)

    @property
    def forks_30d(self) -> FieldDelta | None:
        return self.get("forks", 30)


def sorted_snapshots(
    snapshots: Sequence[RepositorySnapshot],
) -> list[RepositorySnapshot]:
    """Snapshots ordered by ``captured_at``, newest duplicate wins."""
    return list(SnapshotSeries(snapshots))


def _window_delta(
    series: list[RepositorySnapshot],
    field: str,
    days: int,
) -> FieldDelta | None:
    """Delta for ``field`` over ``days``, or ``None`` when no base exists."""
    if len(series) < 2:
        return None
    newest = series[-1]
    target = newest.captured_at - timedelta(days=days)

    base: RepositorySnapshot | None = None
    for snap in series[:-1]:
        if snap.captured_at <= target:
            base = snap
    complete = True
    if base is None:
        base = series[0]
        complete = False
    if base is newest:
        return None

    span_hours = max(
        (newest.captured_at - base.captured_at).total_seconds() / 3600.0, 1e-9
    )
    span_days = span_hours / _HOURS_PER_DAY
    gap_hours = max(
        (target - base.captured_at).total_seconds() / 3600.0, 0.0
    )
    if complete and gap_hours > days * _HOURS_PER_DAY * MAX_GAP_FRACTION:
        complete = False

    base_value = getattr(base, field)
    current_value = getattr(newest, field)
    if base_value is None or current_value is None:
        return None

    delta = current_value - base_value
    growth_pct = (delta / base_value * 100.0) if base_value else None
    return FieldDelta(
        field=field,
        window_days=days,
        base_value=base_value,
        current_value=current_value,
        delta=delta,
        per_day=delta / span_days,
        window=TimeWindow(
            span_days=span_days,
            complete=complete,
            start=base.captured_at,
            end=newest.captured_at,
        ),
        growth_pct=growth_pct,
        base_gap_hours=gap_hours,
    )


def compute_metrics(
    snapshots: Sequence[RepositorySnapshot],
    *,
    windows: tuple[int, ...] = DELTA_WINDOWS_DAYS,
) -> RepoMetrics:
    series = SnapshotSeries(snapshots)
    if not series:
        return RepoMetrics(
            latest_captured_at=None,
            latest_pushed_at=None,
            latest_counts={},
            deltas={},
            series=series,
        )
    newest = series.last
    assert newest is not None
    items = [s for s in series]
    deltas: dict[tuple[str, int], FieldDelta] = {}
    for field in DELTA_FIELDS:
        for days in windows:
            delta = _window_delta(items, field, days)
            if delta is not None:
                deltas[(field, days)] = delta
    return RepoMetrics(
        latest_captured_at=newest.captured_at,
        latest_pushed_at=newest.pushed_at,
        latest_counts={
            "stars": newest.stars,
            "forks": newest.forks,
            "watchers": newest.watchers,
            "open_issues": newest.open_issues,
        },
        deltas=deltas,
        series=series,
    )


__all__ = [
    "DELTA_FIELDS",
    "DELTA_WINDOWS_DAYS",
    "MAX_GAP_FRACTION",
    "FieldDelta",
    "RepoMetrics",
    "TimeWindow",
    "compute_metrics",
    "sorted_snapshots",
]
