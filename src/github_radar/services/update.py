"""Update orchestration: re-observe tracked repositories."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.domain import RepositorySnapshot
from github_radar.github.client import GitHubClient
from github_radar.github.repositories import get_contributors, get_repository
from github_radar.services.developers import DeveloperSyncService
from github_radar.storage import repositories as storage
from github_radar.util import utcnow

logger = logging.getLogger(__name__)


@dataclass
class UpdateReport:
    repositories_to_update: int = 0
    repositories_refreshed: int = 0
    snapshots_inserted: int = 0
    contributors_added: int = 0
    contributor_relationships_observed: int = 0
    developer_profiles_fetched: int = 0
    developer_profiles_reused: int = 0
    developer_snapshots_written: int = 0


class RepositoryUpdateService:
    """Re-fetch tracked repositories, refresh metadata/topics/contributors and
    record a new snapshot whenever observable state changed."""

    def __init__(
        self,
        client: GitHubClient,
        session: AsyncSession,
        *,
        contributors_limit: int = 30,
        refresh_days: int = 7,
    ) -> None:
        self.client = client
        self.session = session
        self.contributors_limit = contributors_limit
        self.refresh_days = refresh_days

    async def update(self, *, limit: int | None = None) -> UpdateReport:
        report = UpdateReport()
        repos = await storage.list_repositories(self.session, limit=limit)
        report.repositories_to_update = len(repos)
        dev_sync = DeveloperSyncService(
            self.client, self.session, refresh_days=self.refresh_days
        )

        for row in repos:
            now = utcnow()
            logger.info("Refreshing %s", row.full_name)
            detail = await get_repository(self.client, row.owner_login, row.name)
            current, _created = await storage.upsert_repository(
                self.session, detail, now=now
            )
            report.repositories_refreshed += 1

            await storage.set_repository_topics(
                self.session, current, detail.topics, now=now
            )
            inserted = await storage.insert_snapshot_if_changed(
                self.session,
                current.id,
                RepositorySnapshot.from_repository(detail, now),
            )
            if inserted is not None:
                report.snapshots_inserted += 1
                logger.info(
                    "New snapshot for %s (stars %d, forks %d)",
                    detail.full_name,
                    detail.stars,
                    detail.forks,
                )

            contributors = await get_contributors(
                self.client,
                detail.owner_login,
                detail.name,
                limit=self.contributors_limit,
            )
            sync_result = await storage.sync_contributors(
                self.session, current, contributors, now=now
            )
            report.contributors_added += sync_result.developers_created
            report.contributor_relationships_observed += sync_result.snapshots_written
            await self.session.commit()

            profile_stats = await dev_sync.sync(contributors)
            report.developer_profiles_fetched += profile_stats.fetched
            report.developer_profiles_reused += profile_stats.reused
            report.developer_snapshots_written += profile_stats.fetched

        logger.info(
            "Update run finished: %d refreshed, %d new snapshots, %d contributor "
            "rows added, %d contributor relationships observed, %d profiles "
            "fetched, %d profiles reused",
            report.repositories_refreshed,
            report.snapshots_inserted,
            report.contributors_added,
            report.contributor_relationships_observed,
            report.developer_profiles_fetched,
            report.developer_profiles_reused,
        )
        return report


__all__ = ["RepositoryUpdateService", "UpdateReport"]