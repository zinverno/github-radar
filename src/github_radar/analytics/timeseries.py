"""Ordered snapshot time-series utilities.

A :class:`SnapshotSeries` is a single deterministic view over a repository's
snapshot history: de-duplicated by ``captured_at`` (newest duplicate wins) and
sorted oldest→newest. Lookups (nearest, at-or-before, at-or-after, previous)
are all anchored on ``captured_at`` and never on wall-clock time, which keeps
every derived metric reproducible between runs without a new snapshot.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from github_radar.domain import RepositorySnapshot


@dataclass(frozen=True)
class NearestMatch:
    """A snapshot matched to a target time with its signed gap."""

    snapshot: RepositorySnapshot
    target: datetime
    gap: timedelta


class SnapshotSeries:
    """A sorted, de-duplicated series of snapshots keyed by ``captured_at``.

    Operations:

    * :meth:`at_or_before` / :meth:`at_or_after` — window-anchored lookups;
    * :meth:`previous_of` — the immediately-older snapshot;
    * :meth:`nearest_to` — a capped nearest-snapshot match used as the base
      for a growth window.
    """

    __slots__ = ("_items", "_times")

    def __init__(self, snapshots: Sequence[RepositorySnapshot]) -> None:
        by_time: dict[datetime, RepositorySnapshot] = {}
        for snap in snapshots:
            by_time[snap.captured_at] = snap
        self._items: list[RepositorySnapshot] = [
            by_time[t] for t in sorted(by_time)
        ]
        self._times: tuple[datetime, ...] = tuple(
            snap.captured_at for snap in self._items
        )

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[RepositorySnapshot]:
        return iter(self._items)

    def __repr__(self) -> str:
        return f"SnapshotSeries({len(self._items)} snapshots)"

    @property
    def first(self) -> RepositorySnapshot | None:
        """The oldest snapshot, or ``None`` when the series is empty."""
        return self._items[0] if self._items else None

    @property
    def last(self) -> RepositorySnapshot | None:
        """The newest snapshot, or ``None`` when the series is empty."""
        return self._items[-1] if self._items else None

    def at_or_before(self, target: datetime) -> RepositorySnapshot | None:
        """The newest snapshot captured at or before ``target``."""
        if not self._items:
            return None
        idx = bisect_right(self._times, target)
        if idx == 0:
            return None
        return self._items[idx - 1]

    def at_or_after(self, target: datetime) -> RepositorySnapshot | None:
        """The oldest snapshot captured at or after ``target``."""
        if not self._items:
            return None
        idx = bisect_left(self._times, target)
        if idx >= len(self._items):
            return None
        return self._items[idx]

    def previous_of(self, snapshot: RepositorySnapshot) -> RepositorySnapshot | None:
        """The snapshot immediately older than ``snapshot``, or ``None``."""
        if not self._items:
            return None
        idx = bisect_left(self._times, snapshot.captured_at)
        if idx == 0:
            return None
        return self._items[idx - 1]

    def nearest_to(self, target: datetime) -> NearestMatch | None:
        """The snapshot nearest to ``target``, or ``None`` when empty.

        Ties resolve toward the *older* snapshot so a window base never drifts
        forward into a gap that is only partially covered.
        """
        if not self._items:
            return None
        idx = bisect_left(self._times, target)
        candidates: list[NearestMatch] = []
        if idx > 0:
            older = self._items[idx - 1]
            candidates.append(
                NearestMatch(
                    target=target,
                    snapshot=older,
                    gap=target - older.captured_at,
                )
            )
        if idx < len(self._items):
            newer = self._items[idx]
            candidates.append(
                NearestMatch(
                    target=target,
                    snapshot=newer,
                    gap=newer.captured_at - target,
                )
            )
        best: NearestMatch | None = None
        for candidate in candidates:
            if best is None or _prefer(candidate, best):
                best = candidate
        return best


def _prefer(candidate: NearestMatch, best: NearestMatch) -> bool:
    if candidate.gap != best.gap:
        return candidate.gap < best.gap
    return candidate.snapshot.captured_at < best.snapshot.captured_at


__all__ = ["NearestMatch", "SnapshotSeries"]
