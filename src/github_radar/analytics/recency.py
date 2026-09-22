"""Recency and age metrics for a repository.

All "days since X" values are computed against a caller-supplied reference
time (:class:`datetime.datetime`) so tests stay deterministic and the result
never depends on when the process happened to run.

The single-number freshness factor :func:`recency_score` is the shared
implementation used by momentum and trend classification so both stay
consistent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from github_radar.util import utcnow

_SECONDS_PER_DAY = 86400.0


def days_between(later: datetime, earlier: datetime) -> float:
    """Fractional days separating two datetimes (never negative)."""
    return max(0.0, (later - earlier).total_seconds() / _SECONDS_PER_DAY)


def recency_score(
    pushed_at: datetime | None,
    *,
    reference_now: datetime,
    half_life_days: float,
) -> float:
    """Freshness in ``[0, 1]`` from the last push vs. ``reference_now``.

    A push at the reference instant scores ``1.0``; a push ``half_life_days``
    (or more) ago scores ``0.0``, decaying linearly in between.  With no push
    recorded the score is ``0.0``.
    """
    if pushed_at is None:
        return 0.0
    age_days = days_between(reference_now, pushed_at)
    return max(0.0, min(1.0, 1.0 - age_days / half_life_days))


@dataclass(frozen=True)
class RecencyMetrics:
    """Age and staleness of a repository relative to ``reference_now``."""

    reference_now: datetime
    repository_age_days: float | None
    days_since_push: float | None
    days_since_github_update: float | None
    # Raw inputs are preserved so callers can render or refuse them.
    created_at: datetime | None
    updated_at: datetime | None
    pushed_at: datetime | None


def compute_recency(
    *,
    created_at: datetime | None,
    updated_at: datetime | None,
    pushed_at: datetime | None,
    latest_pushed_at: datetime | None = None,
    reference_now: datetime | None = None,
) -> RecencyMetrics:
    """Compute age/staleness from a repository's own timestamps.

    ``latest_pushed_at`` is the *newest known* pushed time (taken from the
    latest snapshot when available); it falls back to ``pushed_at``.
    """
    now = reference_now or utcnow()
    push = latest_pushed_at if latest_pushed_at is not None else pushed_at
    return RecencyMetrics(
        reference_now=now,
        repository_age_days=(
            days_between(now, created_at) if created_at is not None else None
        ),
        days_since_push=days_between(now, push) if push is not None else None,
        days_since_github_update=(
            days_between(now, updated_at) if updated_at is not None else None
        ),
        created_at=created_at,
        updated_at=updated_at,
        pushed_at=push,
    )


def utcnow_dt() -> datetime:
    """Return the current time in UTC (timezone-aware)."""
    return datetime.now(UTC)


__all__ = [
    "RecencyMetrics",
    "compute_recency",
    "days_between",
    "recency_score",
    "utcnow_dt",
]