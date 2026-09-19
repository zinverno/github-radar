"""Deterministic growth/activity metrics computed from snapshots.

All functions operate on :class:`github_radar.domain.RepositorySnapshot`
values sorted by ``captured_at``. Metrics return ``None`` rather than
fabricating a value whenever the snapshot history is not old or long enough.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from github_radar.domain import RepositorySnapshot

# The time windows (in days) for which we compute deltas by default.
DELTA_WINDOWS_DAYS = (1, 7, 30)

# Fields for which deltas are computed.
DELTA_FIELDS = ("stars", "forks", "watchers", "open_issues")


def sorted_snapshots(
    snapshots: Sequence[RepositorySnapshot],
) -> list[RepositorySnapshot]:
    """Return snapshots ordered by captured_at, newest duplicate wins."""
    by_time: dict[datetime, RepositorySnapshot] = {}
    for snap in snapshots:
        by_time[snap.captured_at] = snap
    return [by_time[t] for t in sorted(by_time)]


@dataclass(frozen=True)
class TimeWindow:
    """The actual span of snapshots a delta was computed over."""

    span_days: float
    complete: bool
    start: datetime
    end: datetime


@dataclass(frozen=True)
class FieldDelta:
    """Change of a single counter between two snapshots."""

    field: str
    delta: int
    base_value: int
    current_value: int
    per_day: float
    window: TimeWindow


def _delta(
    snapshots: list[RepositorySnapshot],
    field: str,
    days: int,
) -> FieldDelta | None:
    if len(snapshots) < 2:
        return None
    newest = snapshots[-1]
    base = snapshots[0]
    complete = True
    cutoff = newest.captured_at - timedelta(days=days)
    for candidate in reversed(snapshots[:-1]):
        if candidate.captured_at <= cutoff:
            base = candidate
            break
    else:
        # No observation is old enough for a complete window; fall back to the
        # earliest snapshot and flag the window as incomplete.
        base = snapshots[0]
        complete = False
    if base is newest:
        return None

    base_value = getattr(base, field)
    current_value = getattr(newest, field)
    if base_value is None or current_value is None:
        return None
    span_days = (newest.captured_at - base.captured_at).total_seconds() / 86400.0
    span_days = max(span_days, 1e-9)
    delta = current_value - base_value
    return FieldDelta(
        field=field,
        delta=delta,
        base_value=base_value,
        current_value=current_value,
        per_day=delta / span_days,
        window=TimeWindow(
            span_days=span_days,
            complete=complete,
            start=base.captured_at,
            end=newest.captured_at,
        ),
    )


@dataclass(frozen=True)
class RepoMetrics:
    """All deterministic metrics for one repository derived from snapshots."""

    latest_captured_at: datetime | None
    latest_pushed_at: datetime | None
    latest_counts: dict[str, int | None]
    deltas: dict[tuple[str, int], FieldDelta]

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


def compute_metrics(
    snapshots_: Sequence[RepositorySnapshot],
    *,
    windows: tuple[int, ...] = DELTA_WINDOWS_DAYS,
) -> RepoMetrics:
    """Compute deltas and latest counts from a series of snapshots."""
    series = sorted_snapshots(snapshots_)
    if not series:
        return RepoMetrics(
            latest_captured_at=None,
            latest_pushed_at=None,
            latest_counts={},
            deltas={},
        )
    newest = series[-1]
    deltas: dict[tuple[str, int], FieldDelta] = {}
    for field in DELTA_FIELDS:
        for days in windows:
            delta = _delta(series, field, days)
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
            "size_kb": newest.size_kb,
        },
        deltas=deltas,
    )


__all__ = [
    "DELTA_FIELDS",
    "DELTA_WINDOWS_DAYS",
    "FieldDelta",
    "RepoMetrics",
    "TimeWindow",
    "compute_metrics",
    "sorted_snapshots",
]