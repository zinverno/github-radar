"""Developer profile and contributor-observation analytics.

Phase 3 makes developer intelligence historical. Two kinds of observations feed
it:

* developer profile observations (:class:`~github_radar.domain.DeveloperSnapshot`)
  — the public counters (followers, following, public_repos) at a fetch instant;
* contributor observations (:class:`~github_radar.domain.ContributorSnapshot`) —
  GitHub's *cumulative* contribution count for one developer in one repository
  at a fetch instant.

All deltas are computed between real observations anchored on ``captured_at``
and an explicit ``reference_now`` (never the wall clock). A delta is only
reported when both ends of a window were observed; otherwise it is ``None`` —
missing history is never dressed up as zero activity. A change in the
cumulative contribution count between two observations is a truthful sign of
activity in that window; a single observation is not.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from github_radar.analytics.history import ObservationSeries
from github_radar.analytics.history import window_delta as _window_delta
from github_radar.domain import ContributorSnapshot, DeveloperSnapshot

# The default windows over which developer profile deltas are reported.
PROFILE_DELTA_WINDOWS_DAYS = (1, 7, 30)


@dataclass(frozen=True)
class ProfileFieldDelta:
    """Change of one public profile counter over a window."""

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


@dataclass(frozen=True)
class DeveloperProfileMetrics:
    """Deterministic deltas from one developer's profile observation history."""

    reference_now: datetime
    observations: int
    first_captured_at: datetime | None
    last_captured_at: datetime | None
    latest_followers: int | None
    latest_following: int | None
    latest_public_repos: int | None
    deltas: Mapping[tuple[str, int], ProfileFieldDelta] = field(default_factory=dict)

    def followers_delta(self, window_days: int) -> ProfileFieldDelta | None:
        return self.deltas.get(("followers", window_days))

    def following_delta(self, window_days: int) -> ProfileFieldDelta | None:
        return self.deltas.get(("following", window_days))

    def public_repos_delta(self, window_days: int) -> ProfileFieldDelta | None:
        return self.deltas.get(("public_repos", window_days))


def _profile_metric(observation: DeveloperSnapshot, field: str) -> int | None:
    value: int | None = getattr(observation, field)
    return value


def _metric_for_field(
    field: str,
) -> Callable[[DeveloperSnapshot], int | None]:
    def metric(observation: DeveloperSnapshot) -> int | None:
        return _profile_metric(observation, field)

    return metric


def compute_profile_deltas(
    observations: Sequence[DeveloperSnapshot],
    *,
    reference_now: datetime,
    windows: Sequence[int] = PROFILE_DELTA_WINDOWS_DAYS,
) -> DeveloperProfileMetrics | None:
    """Build windowed deltas for a developer's public counters.

    Returns ``None`` when there is no profile history at all (the developer was
    never profile-fetched). Fields with unusable windows get ``None`` deltas,
    never a fabricated zero.
    """
    if not observations:
        return None
    series = ObservationSeries(observations, captured_at=lambda obs: obs.captured_at)
    latest = series.last
    assert latest is not None
    deltas: dict[tuple[str, int], ProfileFieldDelta] = {}
    for metric_name in ("followers", "following", "public_repos"):
        for days in windows:
            delta = _window_delta(
                observations,
                reference_now=reference_now,
                window_days=days,
                metric_value=_metric_for_field(metric_name),
                captured_at=lambda obs: obs.captured_at,
            )
            if delta is None:
                continue
            deltas[(metric_name, days)] = ProfileFieldDelta(
                window_days=delta.window_days,
                base_value=delta.base_value,
                current_value=delta.current_value,
                delta=delta.delta,
                base_captured_at=delta.base_captured_at,
                current_captured_at=delta.current_captured_at,
                span_days=delta.span_days,
                base_gap_days=delta.base_gap_days,
                complete=delta.complete,
                observations=delta.observations,
            )
    return DeveloperProfileMetrics(
        reference_now=reference_now,
        observations=len(series),
        first_captured_at=series.first.captured_at if series.first else None,
        last_captured_at=latest.captured_at,
        latest_followers=latest.followers,
        latest_following=latest.following,
        latest_public_repos=latest.public_repos,
        deltas=deltas,
    )


@dataclass(frozen=True)
class ContributionDelta:
    """Change of one repository/developer contribution link over a window.

    ``contributions`` is GitHub's cumulative count; the delta is the observed
    change between two observations. ``incomplete_base``/``complete`` describe
    how close the base observation sits to the window start.
    """

    window_days: int
    base_contributions: int | None
    current_contributions: int | None
    delta: int | None
    base_captured_at: datetime | None
    current_captured_at: datetime | None
    span_days: float | None
    base_gap_days: float | None
    complete: bool
    observations: int

    @property
    def available(self) -> bool:
        """A genuine delta (both window ends were observed)."""
        return self.delta is not None


@dataclass(frozen=True)
class ContributionHistory:
    """A developer's observation history for one repository."""

    repository_id: int
    developer_id: int
    observations: tuple[ContributorSnapshot, ...]

    @property
    def span_days(self) -> float | None:
        if len(self.observations) < 2:
            return None
        first = self.observations[0].captured_at
        last = self.observations[-1].captured_at
        return max(0.0, (last - first).total_seconds() / 86400.0)


def compute_contribution_delta(
    observations: Sequence[ContributorSnapshot],
    *,
    reference_now: datetime,
    window_days: int,
) -> ContributionDelta | None:
    """Delta of one relationship over ``window_days`` (or ``None`` when bare)."""
    if not observations:
        return None
    delta = _window_delta(
        observations,
        reference_now=reference_now,
        window_days=window_days,
        metric_value=lambda obs: obs.contributions,
        captured_at=lambda obs: obs.captured_at,
    )
    if delta is None:
        return None
    return ContributionDelta(
        window_days=delta.window_days,
        base_contributions=delta.base_value,
        current_contributions=delta.current_value,
        delta=delta.delta,
        base_captured_at=delta.base_captured_at,
        current_captured_at=delta.current_captured_at,
        span_days=delta.span_days,
        base_gap_days=delta.base_gap_days,
        complete=delta.complete,
        observations=delta.observations,
    )


__all__ = [
    "ContributionDelta",
    "ContributionHistory",
    "PROFILE_DELTA_WINDOWS_DAYS",
    "DeveloperProfileMetrics",
    "ProfileFieldDelta",
    "compute_contribution_delta",
    "compute_profile_deltas",
]