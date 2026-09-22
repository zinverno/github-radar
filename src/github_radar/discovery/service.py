"""Repository discovery orchestration.

Flow per search result:

    search result → already tracked?
        yes → refresh mutable metadata from the search payload (free)
        no  → full detail fetch
               ↓ metadata    (detail also carries topics)
               ↓ topics
               ↓ snapshot
               ↓ contributors
               ↓ developer profiles (lazy/throttled)
               ↓ commit

Cost awareness: the detail + contributors fetches happen only for newly
discovered repositories. Known repositories are refreshed from the (already
returned) search payload and never re-fetched during discovery — the
``update`` command exists for that.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.domain import RepositorySnapshot
from github_radar.github.client import GitHubClient
from github_radar.github.repositories import (
    get_contributors,
    get_repository,
    search_repositories,
)
from github_radar.services.developers import DeveloperSyncService
from github_radar.storage import repositories as storage
from github_radar.util import utcnow

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryReport:
    query: str
    requested_limit: int | None
    candidates_seen: int = 0
    discovered: int = 0
    updated: int = 0
    snapshots_inserted: int = 0
    contributors_added: int = 0
    contributor_relationships_observed: int = 0
    developer_profiles_fetched: int = 0
    developer_profiles_reused: int = 0
    developer_snapshots_written: int = 0


class RepositoryDiscoveryService:
    def __init__(
        self,
        client: GitHubClient,
        session: AsyncSession,
        *,
        contributors_limit: int = 30,
        refresh_days: int = 7,
        per_page: int = 100,
    ) -> None:
        self.client = client
        self.session = session
        self.contributors_limit = contributors_limit
        self.refresh_days = refresh_days
        self.per_page = per_page

    async def discover(
        self,
        *,
        query: str,
        language: str | None = None,
        topic: str | None = None,
        min_stars: int | None = None,
        created_after: date | None = None,
        limit: int | None = None,
        sort: str = "stars",
        order: str = "desc",
    ) -> DiscoveryReport:
        """Search GitHub and persist newly discovered repositories."""
        report = DiscoveryReport(query=query, requested_limit=limit)
        dev_sync = DeveloperSyncService(
            self.client, self.session, refresh_days=self.refresh_days
        )

        async for repo in search_repositories(
            self.client,
            query=query,
            language=language,
            topic=topic,
            min_stars=min_stars,
            created_after=created_after,
            limit=limit,
            per_page=self.per_page,
            sort=sort,
            order=order,
        ):
            report.candidates_seen += 1
            now = utcnow()
            existing = await storage.get_repository_by_github_id(
                self.session, repo.github_id
            )

            if existing is not None:
                # Cheap path: refresh mutable metadata from the search payload.
                # No detail/contributors re-fetch and no snapshot here.
                await storage.upsert_repository(self.session, repo, now=now)
                report.updated += 1
                await self.session.commit()
                continue

            logger.info(
                "Discovering new repository %s (candidate %d/%s)",
                repo.full_name,
                report.candidates_seen,
                report.requested_limit or "unlimited",
            )
            detail = await get_repository(self.client, repo.owner_login, repo.name)
            row, _created = await storage.upsert_repository(
                self.session, detail, now=now
            )
            report.discovered += 1

            await storage.set_repository_topics(
                self.session, row, detail.topics, now=now
            )
            inserted = await storage.insert_snapshot_if_changed(
                self.session,
                row.id,
                RepositorySnapshot.from_repository(detail, now),
            )
            if inserted is not None:
                report.snapshots_inserted += 1

            contributors = await get_contributors(
                self.client,
                detail.owner_login,
                detail.name,
                limit=self.contributors_limit,
            )
            sync_result = await storage.sync_contributors(
                self.session, row, contributors, now=now
            )
            report.contributors_added += sync_result.developers_created
            report.contributor_relationships_observed += sync_result.snapshots_written
            await self.session.commit()

            profile_stats = await dev_sync.sync(contributors)
            report.developer_profiles_fetched += profile_stats.fetched
            report.developer_profiles_reused += profile_stats.reused
            report.developer_snapshots_written += profile_stats.fetched

        logger.info(
            "Discovery run finished: %d discovered, %d updated, %d snapshots, "
            "%d contributor rows added, %d contributor relationships observed, "
            "%d profiles fetched, %d profiles reused",
            report.discovered,
            report.updated,
            report.snapshots_inserted,
            report.contributors_added,
            report.contributor_relationships_observed,
            report.developer_profiles_fetched,
            report.developer_profiles_reused,
        )
        return report


__all__ = ["DiscoveryReport", "RepositoryDiscoveryService"]