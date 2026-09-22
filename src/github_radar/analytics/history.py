"""Ordered observation-series helpers shared by developer analytics.

Phase 2 built :class:`github_radar.analytics.timeseries.SnapshotSeries` around
``RepositorySnapshot``. Developer analytics need the same de-duplicated,
captured_at-anchored lookups for developer profile observations and contributor
observations, so this module provides a small generic series over any object
exposing a ``captured_at`` attribute. All lookups are anchored on
``captured_at`` — never wall-clock time — keeping every derived metric
reproducible between runs.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta


class ObservationSeries[T]:
    """A sorted, de-duplicated series keyed on ``captured_at``.

    Newest duplicate wins, matching :class:`SnapshotSeries` behaviour, so a
    same-instant re-observation collapses into one entry.
    """

    __slots__ = ("_items", "_times")

    def __init__(
        self,
        items: Sequence[T],
        captured_at: Callable[[T], datetime],
    ) -> None:
        by_time: dict[datetime, T] = {}
        for item in items:
            by_time[captured_at(item)] = item
        self._items: list[T] = [by_time[t] for t in sorted(by_time)]
        self._times: tuple[datetime, ...] = tuple(
            captured_at(item) for item in self._items
        )

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[T]:
        return iter(self._items)

    def __repr__(self) -> str:
        return f"ObservationSeries({len(self._items)} observations)"

    @property
    def first(self) -> T | None:
        """The oldest observation, or ``None`` when the series is empty."""
        return self._items[0] if self._items else None

    @property
    def last(self) -> T | None:
        """The newest observation, or ``None`` when the series is empty."""
        return self._items[-1] if self._items else None

    def at_or_before(self, target: datetime) -> T | None:
        """The newest observation captured at or before ``target``."""
        if not self._items:
            return None
        idx = bisect_right(self._times, target)
        if idx == 0:
            return None
        return self._items[idx - 1]

    def at_or_after(self, target: datetime) -> T | None:
        """The oldest observation captured at or after ``target``."""
        if not self._items:
            return None
        idx = bisect_left(self._times, target)
        if idx >= len(self._items):
            return None
        return self._items[idx]


@dataclass(frozen=True)
class ObservationDelta:
    """Change of one metric over a window (base → current), with coverage."""

    window_days: int
    base_value: int | None
    current_value: int | None
    delta: int | None
    base_captured_at: datetime | None
    current_captured_at: datetime | None
    span_days: float | None
    base_gap_days: float | None
    complete: bool
    observations: int


def window_delta[T](
    items: Sequence[T],
    *,
    reference_now: datetime,
    window_days: int,
    metric_value: Callable[[T], int | None],
    captured_at: Callable[[T], datetime],
    max_gap_fraction: float = 0.5,
) -> ObservationDelta | None:
    """Change of ``metric_value`` over ``window_days`` ending at the reference.

    The current observation is the newest at-or-before ``reference_now`` (or the
    oldest if the reference predates all observations); the base is the newest
    observation at-or-before ``reference_now - window``. Without a usable base
    the delta is ``None`` — never a fabricated zero. ``complete`` is false when
    the base is absent or its gap from the window target exceeds
    ``max_gap_fraction`` of the window.
    """
    series = ObservationSeries(items, captured_at=captured_at)
    if not series:
        return None
    current = series.at_or_before(reference_now)
    if current is None:
        current = series.first
    if current is None:
        return None
    target = reference_now - timedelta(days=window_days)
    base = series.at_or_before(target)

    observations = len(series)
    if base is None or base is current:
        return ObservationDelta(
            window_days=window_days,
            base_value=None,
            current_value=metric_value(current),
            delta=None,
            base_captured_at=None,
            current_captured_at=captured_at(current),
            span_days=None,
            base_gap_days=None,
            complete=False,
            observations=observations,
        )

    span_hours = max(
        (captured_at(current) - captured_at(base)).total_seconds() / 3600.0, 1e-9
    )
    span_days = span_hours / 24.0
    gap_hours = max((target - captured_at(base)).total_seconds() / 3600.0, 0.0)
    base_gap_days = gap_hours / 24.0
    complete = base_gap_days <= window_days * max_gap_fraction

    base_value = metric_value(base)
    current_value = metric_value(current)
    change: int | None = None
    if base_value is not None and current_value is not None:
        change = current_value - base_value

    return ObservationDelta(
        window_days=window_days,
        base_value=base_value,
        current_value=current_value,
        delta=change,
        base_captured_at=captured_at(base),
        current_captured_at=captured_at(current),
        span_days=span_days,
        base_gap_days=base_gap_days,
        complete=complete,
        observations=observations,
    )


__all__ = [
    "ObservationDelta",
    "ObservationSeries",
    "window_delta",
]