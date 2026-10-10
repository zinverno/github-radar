"""Shared report-building helpers for the API routers.

These mirror the CLI's application layer (``_collect_tracked``,
``_build_developer_reports``, ``_load_graph``) so the API projects exactly the
same analytics the CLI shows. Nothing new is computed here — the analytics
layers stay the single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.analytics import (
    RepositoryReport,
    compute_metrics,
    compute_momentum,
)
from github_radar.analytics.topics import TopicAggregate, aggregate_topics
from github_radar.analytics.trends import Trend, classify_trend
from github_radar.graph.loader import load_graph
from github_radar.graph.model import EcosystemGraph
from github_radar.services.intelligence import (
    DeveloperDataset,
    DeveloperIntelligenceService,
    DeveloperReport,
)
from github_radar.storage import repositories as storage
from github_radar.storage.models import RepositoryRow
from github_radar.util import stale_days, utcnow


async def freshness(session: AsyncSession) -> dict[str, str | int | None]:
    """Wall-clock dataset freshness summary.

    The analytics ``reference_now`` is anchored on the newest observation, so it
    cannot express staleness; this block adds the wall clock so a dataset that
    stopped receiving updates is visible as degrading ``stale_days``.
    """
    newest = await storage.newest_observation_at(session)
    now = utcnow()
    return {
        "observed_at": now.isoformat(),
        "newest_observation_at": newest.isoformat() if newest is not None else None,
        "stale_days": stale_days(newest, now=now),
    }


async def resolve_repository(session: AsyncSession, ref: str) -> RepositoryRow | None:
    """Resolve ``ref`` as an integer id or an ``owner/name`` full name."""
    ref = ref.strip().strip("/")
    if not ref:
        return None
    if ref.isdigit():
        rows = await storage.repositories_by_id(session, [int(ref)])
        return rows.get(int(ref))
    row = await storage.get_repository_by_full_name(session, ref)
    if row is not None:
        return row
    parts = ref.split("/")
    if len(parts) == 2:
        row = await storage.get_repository_by_full_name(session, f"{parts[0]}/{parts[1]}")
        return row
    return None


@dataclass(frozen=True)
class TrackedRepository:
    """One tracked repository with its analytics digest and topic names."""

    repository_id: int
    full_name: str
    owner_login: str
    description: str | None
    homepage: str | None
    primary_language: str | None
    html_url: str | None
    is_archived: bool
    is_fork: bool
    pushed_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    topics: tuple[str, ...]
    report: RepositoryReport


async def collect_repositories(session: AsyncSession) -> list[TrackedRepository]:
    """Per-tracked-repository analytics digest (see CLI ``_collect_tracked``)."""
    rows = await storage.list_repos_with_latest(session)
    tracked: list[TrackedRepository] = []
    for repo, latest in rows:
        names = tuple(
            topic.name for topic in await storage.topics_for_repository(session, repo.id)
        )
        snapshots = await storage.snapshots_for_repository(session, repo.id)
        metrics = compute_metrics([snapshot.to_domain() for snapshot in snapshots])
        reference_now = latest.captured_at if latest is not None else metrics.latest_captured_at
        momentum = (
            compute_momentum(metrics, reference_now=reference_now)
            if reference_now is not None
            else None
        )
        tracked.append(
            TrackedRepository(
                repository_id=repo.id,
                full_name=repo.full_name,
                owner_login=repo.owner_login,
                description=repo.description,
                homepage=repo.homepage,
                primary_language=repo.primary_language,
                html_url=repo.html_url,
                is_archived=repo.is_archived,
                is_fork=repo.is_fork,
                pushed_at=repo.pushed_at,
                first_seen_at=repo.first_seen_at,
                last_seen_at=repo.last_seen_at,
                topics=names,
                report=RepositoryReport(
                    full_name=repo.full_name,
                    metrics=metrics,
                    momentum=momentum,
                ),
            )
        )
    return tracked


async def developer_dataset_and_reports(
    session: AsyncSession,
    *,
    window_days: int = 7,
) -> tuple[DeveloperDataset, tuple[DeveloperReport, ...]]:
    """Load the developer dataset and full reports (see CLI helper)."""
    service = DeveloperIntelligenceService(session)
    dataset = await service.load_dataset()
    reports = service.build_reports(dataset, window_days=window_days)
    return dataset, reports


async def ecosystem_graph(
    session: AsyncSession,
    reference_now: datetime,
) -> EcosystemGraph:
    """Load the derived ecosystem graph anchored on ``reference_now``."""
    return await load_graph(session, reference_now=reference_now)


async def topic_aggregates(
    session: AsyncSession,
) -> list[TopicAggregate]:
    """Topic aggregate reports for every tracked topic, ranked by momentum.

    Returns :class:`~github_radar.analytics.topics.TopicAggregate` objects via
    the pure ``aggregate_topics`` function.
    """
    tracked = await collect_repositories(session)
    grouped: dict[str, list[RepositoryReport]] = {}
    for item in tracked:
        for topic in item.topics:
            grouped.setdefault(topic, []).append(item.report)
    return aggregate_topics(grouped)


def trend_for(report: RepositoryReport, reference_now: datetime) -> Trend | None:
    return classify_trend(report.metrics, report.momentum, reference_now=reference_now)


__all__ = [
    "TrackedRepository",
    "collect_repositories",
    "developer_dataset_and_reports",
    "ecosystem_graph",
    "freshness",
    "topic_aggregates",
    "trend_for",
]