"""Shared scoring helpers for graph-derived relationships.

Every graph score follows the Phase 2/3 convention: explainable components
(each a weight times a bounded term), a documented module-level weight
dictionary, a bounded final score, and an explicit list of *missing*
components. Missing evidence contributes zero instead of being guessed — a
score whose components are missing can simply never reach the full bound, and
that shortfall stays visible to the caller.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from github_radar.analytics.developers import compute_contribution_delta
from github_radar.graph.model import ContributionEvidence


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def score_components(
    components: dict[str, float],
) -> float:
    """Bounded weighted score from weight×term components (clamped to [0, 1])."""
    return clamp(sum(components.values()))


@dataclass(frozen=True)
class RecentActivity:
    """Observed recent contribution movement for one evidence edge.

    ``available`` is ``False`` when the window could not be measured (no base
    observation) — that is *unavailable* activity, never a zero delta.
    """

    window_days: int
    available: bool
    delta: int | None
    complete: bool
    observations: int


def recent_activity(
    evidence: ContributionEvidence,
    *,
    reference_now: datetime,
    window_days: int,
) -> RecentActivity:
    """Windowed observed activity for one contributor link.

    Delegates to :func:`github_radar.analytics.developers.compute_contribution_delta`
    so the temporal semantics match Phase 3 exactly: a delta exists only when
    both window ends were observed.
    """
    if not evidence.observations:
        return RecentActivity(
            window_days=window_days,
            available=False,
            delta=None,
            complete=False,
            observations=0,
        )
    delta = compute_contribution_delta(
        evidence.observations,
        reference_now=reference_now,
        window_days=window_days,
    )
    if delta is None or delta.delta is None:
        return RecentActivity(
            window_days=window_days,
            available=False,
            delta=None,
            complete=False,
            observations=len(evidence.observations),
        )
    return RecentActivity(
        window_days=window_days,
        available=True,
        delta=delta.delta,
        complete=delta.complete,
        observations=delta.observations,
    )


__all__ = [
    "RecentActivity",
    "clamp",
    "mean",
    "recent_activity",
    "score_components",
]
