"""Pagination helpers for list endpoints.

List responses always use the same envelope::

    {"items": [...], "limit": int, "offset": int, "total": int, "has_more": bool}

``limit`` above the hard maximum (or below one) is rejected with a typed error
instead of being silently clamped, so misbehaving clients are visible.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from github_radar.api.errors import BadRequestError

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


def parse_pagination(limit: int | None, offset: int | None) -> tuple[int, int]:
    """Validate and normalize ``limit``/``offset`` query parameters."""
    if limit is None:
        limit = DEFAULT_LIMIT
    if limit < 1:
        raise BadRequestError(
            "limit must be a positive integer.",
            code="invalid_limit",
            details={"limit": "must be >= 1"},
        )
    if limit > MAX_LIMIT:
        raise BadRequestError(
            f"limit must be at most {MAX_LIMIT}.",
            code="invalid_limit",
            details={"limit": f"must be <= {MAX_LIMIT}"},
        )
    if offset is None:
        offset = 0
    if offset < 0:
        raise BadRequestError(
            "offset must be a non-negative integer.",
            code="invalid_offset",
            details={"offset": "must be >= 0"},
        )
    return limit, offset


def page[T](items: Sequence[T], *, limit: int, offset: int, total: int) -> dict[str, Any]:
    """Wrap ``items`` in the standard page envelope."""
    return {
        "items": list(items),
        "limit": limit,
        "offset": offset,
        "total": total,
        "has_more": offset + len(items) < total,
    }


__all__ = ["DEFAULT_LIMIT", "MAX_LIMIT", "page", "parse_pagination"]