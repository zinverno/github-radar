"""Small shared helpers without a larger home."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def stale_days(
    since: datetime | None,
    *,
    now: datetime | None = None,
) -> int | None:
    """Whole days elapsed between ``since`` and the wall clock (``None`` if unknown).

    Storage backends may return naive (UTC-implicit) timestamps (SQLite); those
    are treated as UTC so the arithmetic never mixes naive and aware values.
    """
    if since is None:
        return None
    anchor = now or utcnow()
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    elapsed = anchor - since
    if elapsed.total_seconds() <= 0:
        return 0
    return int(elapsed.total_seconds() // 86400)


__all__ = ["stale_days", "utcnow"]