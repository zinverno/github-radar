"""Window parameter parsing (``1d`` / ``7d`` / ``30d``).

The windows are the exact snapshot-anchored windows used by the analytics
layers; anything else is rejected with a typed error.
"""

from __future__ import annotations

from github_radar.api.errors import BadRequestError

VALID_WINDOWS_DAYS = (1, 7, 30)


def parse_window_days(window: str | None) -> int:
    """Parse ``1d``/``7d``/``30d`` (bare integers are accepted too)."""
    if window is None:
        return 7
    raw = window.strip().lower()
    value = raw[:-1] if raw.endswith("d") else raw
    try:
        days = int(value)
    except ValueError:
        raise BadRequestError(
            f"Invalid window value {window!r}.",
            code="invalid_window",
            details={"window": "expected one of 1d, 7d, 30d"},
        ) from None
    if days not in VALID_WINDOWS_DAYS:
        raise BadRequestError(
            f"Window must be one of 1d, 7d, 30d (got {window!r}).",
            code="invalid_window",
            details={"window": "expected one of 1d, 7d, 30d"},
        )
    return days


__all__ = ["VALID_WINDOWS_DAYS", "parse_window_days"]