"""Developer profile synchronization.

GitHub's contributor endpoint returns little more than login/avatar/HTML url.
Full public profiles cost an API request each, so we fetch them lazily:

* new developers (never fetched) are fetched immediately;
* previously fetched profiles are only refreshed when their recorded
  ``profile_fetched_at`` is older than ``refresh_days`` — otherwise the stored
  profile is **reused** and no request is made;
* ``force=True`` always re-fetches (useful for backfill / debugging).

Every profile fetch records a developer profile observation
(:class:`github_radar.domain.DeveloperSnapshot`), so counter deltas
(followers, public_repos, ...) become verifiable history.

This service never scrapes or infers private data — only the public profile
endpoint is used. No schedule is built here: callers decide when and how often
profiles are refreshed.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ProfileSyncStats:
    """How many profiles were re-fetched versus reused from storage."""

    fetched: int
    reused: int

    @property
    def total(self) -> int:
        return self.fetched + self.reused


class DeveloperSyncService:
    """Fetch and store public developer profiles for seen contributors.

    Refresh policy: a profile is re-fetched when it was never fetched or when
    ``now - profile_fetched_at >= refresh_days``. Profiles that are still fresh
    are reused without a request. This is deterministic given the same
    ``profile_fetched_at`` value and the same ``now``.
    """

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

    async def sync(
        self,
        contributors: Sequence[Contributor],
        *,
        force: bool = False,
    ) -> ProfileSyncStats:
        """Refresh profiles for contributors that need it, reusing fresh ones.

        With ``force=True`` every contributor profile is re-fetched
        (``refresh_days`` is ignored for that run).
        """
        now = utcnow()
        fetched = 0
        reused = 0
        for contributor in contributors:
            if not force and not await self._needs_refresh(
                contributor.developer.github_id, now
            ):
                reused += 1
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
        return ProfileSyncStats(fetched=fetched, reused=reused)

    async def _needs_refresh(self, github_id: int, now: datetime) -> bool:
        existing = await get_developer_by_github_id(self.session, github_id)
        if existing is None:
            return True
        fetched_at = existing.profile_fetched_at
        if fetched_at is None:
            return True
        threshold = now - timedelta(days=self.refresh_days)
        return fetched_at < threshold


__all__ = ["DeveloperSyncService", "ProfileSyncStats"]