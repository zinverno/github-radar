"""Developer profile synchronization.

GitHub's contributor endpoint returns little more than login/avatar/HTML url.
Full public profiles cost an API request each, so we fetch them lazily:

* new developers are fetched immediately;
* previously fetched profiles are only refreshed after ``refresh_days``.

This service never scrapes or infers private data — only the public profile
endpoint is used.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.domain import Contributor
from github_radar.github.client import GitHubClient
from github_radar.github.errors import GitHubNotFoundError
from github_radar.github.users import get_user_profile
from github_radar.storage.repositories import (
    get_developer_by_github_id,
    sync_profile,
)
from github_radar.util import utcnow

logger = logging.getLogger(__name__)


class DeveloperSyncService:
    """Fetch and store public developer profiles for seen contributors."""

    def __init__(
        self,
        client: GitHubClient,
        session: AsyncSession,
        *,
        refresh_days: int = 7,
    ) -> None:
        self.client = client
        self.session = session
        self.refresh_days = refresh_days

    async def sync(self, contributors: Sequence[Contributor]) -> int:
        """Refresh profiles for contributors that need it. Returns count."""
        now = utcnow()
        fetched = 0
        for contributor in contributors:
            if not await self._needs_refresh(contributor.developer.github_id, now):
                continue
            try:
                profile = await get_user_profile(
                    self.client, contributor.developer.login
                )
            except GitHubNotFoundError:
                # The user may have been renamed/deleted; keep the partial row.
                logger.warning(
                    "Profile for %s no longer available", contributor.developer.login
                )
                continue
            await sync_profile(self.session, profile, now=now)
            fetched += 1
        if fetched:
            await self.session.commit()
        return fetched

    async def _needs_refresh(self, github_id: int, now: datetime) -> bool:
        existing = await get_developer_by_github_id(self.session, github_id)
        if existing is None:
            return True
        fetched_at = existing.profile_fetched_at
        if fetched_at is None:
            return True
        threshold = now - timedelta(days=self.refresh_days)
        return fetched_at < threshold


__all__ = ["DeveloperSyncService"]