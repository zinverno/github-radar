"""Explainable set-overlap metrics for graph relationships.

Every overlap exposes the raw intersection and union counts alongside the
normalized score — a bare number is never shown. Empty sets are safe: when the
union is empty the score is ``0.0`` and the counts are both ``0`` (no overlap
is expressed by empty evidence, not by a fabricated value).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Overlap[T]:
    """A Jaccard-style intersection/union/normalized overlap over two sets."""

    intersection: frozenset[T]
    union: frozenset[T]
    jaccard: float

    @property
    def intersection_count(self) -> int:
        return len(self.intersection)

    @property
    def union_count(self) -> int:
        return len(self.union)

    @property
    def count(self) -> int:
        """Alias for the intersection count (shared element count)."""
        return len(self.intersection)


def jaccard[T](left: Iterable[T], right: Iterable[T]) -> Overlap[T]:
    """Intersection / union of ``left`` and ``right``, safe for empty sets.

    The score is ``intersection / union`` when the union is non-empty and
    ``0.0`` when both sets are empty (nothing overlaps when there is nothing
    to compare). The raw sets are always retained so callers can show evidence.
    """
    a = frozenset(left)
    b = frozenset(right)
    intersection = a & b
    union = a | b
    score = (len(intersection) / len(union)) if union else 0.0
    return Overlap(intersection=intersection, union=union, jaccard=score)


__all__ = ["Overlap", "jaccard"]
