"""Persistence operations: upserts, relationships and queries.

Functions in this module accept domain objects and translate them to/from the
SQLAlchemy ORM rows. They operate on a caller-supplied
:class:`sqlalchemy.ext.asyncio.AsyncSession`; committing is the caller's job.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.domain import Contributor, Developer, Repository, RepositorySnapshot
from github_radar.storage.models import (
    ContributorRow,
    DeveloperRow,
    RepositoryRow,
    RepositoryTopicRow,
    SnapshotRow,
    TopicRow,
)
from github_radar.util import utcnow

logger = logging.getLogger(__name__)


def normalize_topic(name: str) -> str:
    """Normalize a topic name for storage (lower-case, trimmed).

    GitHub topics are already lower-case slugs; normalizing protects against
    cosmetic duplicates (e.g. "MCP" vs "mcp") and keeps ``Topic.name`` unique.
    """
    return name.strip().lower()


async def _flush(session: AsyncSession) -> None:
    await session.flush()


# ---------------------------------------------------------------------------
# Developers
# ---------------------------------------------------------------------------

async def _find_developer_by_github_id(
    session: AsyncSession, github_id: int
) -> DeveloperRow | None:
    result = await session.execute(
        select(DeveloperRow).where(DeveloperRow.github_id == github_id)
    )
    return result.scalar_one_or_none()


async def get_developer_by_github_id(
    session: AsyncSession, github_id: int
) -> DeveloperRow | None:
    """Public lookup of a developer row by GitHub user id."""
    return await _find_developer_by_github_id(session, github_id)


async def note_contributor(
    session: AsyncSession,
    developer: Developer,
    *,
    now: datetime | None = None,
) -> tuple[DeveloperRow, bool]:
    """Register a contributor sighting without overwriting full profile data.

    Only the fields actually present in ``developer`` are stored; richer
    profile fields we already hold (bio, company, ...) are never clobbered by
    the partial data the contributors endpoint returns. Returns (row, created).
    """
    now = now or utcnow()
    existing = await _find_developer_by_github_id(session, developer.github_id)
    if existing is not None:
        changed = False
        if developer.login and existing.login != developer.login:
            existing.login = developer.login
            changed = True
        for field in ("avatar_url", "html_url"):
            value = getattr(developer, field)
            if value is not None and getattr(existing, field) != value:
                setattr(existing, field, value)
                changed = True
        if existing.last_seen_at != now:
            existing.last_seen_at = now
            changed = True
        if changed:
            await session.flush()
        return existing, False

    row = DeveloperRow(
        github_id=developer.github_id,
        login=developer.login,
        avatar_url=developer.avatar_url,
        html_url=developer.html_url,
        first_seen_at=now,
        last_seen_at=now,
    )
    session.add(row)
    await _flush(session)
    return row, True


async def sync_profile(
    session: AsyncSession,
    developer: Developer,
    *,
    now: datetime | None = None,
) -> tuple[DeveloperRow, bool]:
    """Create or fully refresh a developer's public profile.

    ``profile_fetched_at`` is stamped so the update service can throttle
    re-fetching of recently seen profiles.
    """
    now = now or utcnow()
    existing = await _find_developer_by_github_id(session, developer.github_id)
    if existing is None:
        row = DeveloperRow(
            github_id=developer.github_id,
            login=developer.login,
            name=developer.name,
            avatar_url=developer.avatar_url,
            html_url=developer.html_url,
            bio=developer.bio,
            company=developer.company,
            location=developer.location,
            blog=developer.blog,
            public_email=developer.public_email,
            twitter_username=developer.twitter_username,
            followers=developer.followers,
            following=developer.following,
            public_repos=developer.public_repos,
            created_at=developer.created_at,
            updated_at=developer.updated_at,
            first_seen_at=now,
            last_seen_at=now,
            profile_fetched_at=now,
        )
        session.add(row)
        await _flush(session)
        return row, True

    existing.login = developer.login
    for field in (
        "name", "avatar_url", "html_url", "bio", "company", "location", "blog",
        "public_email", "twitter_username", "followers", "following",
        "public_repos", "created_at", "updated_at",
    ):
        setattr(existing, field, getattr(developer, field))
    existing.last_seen_at = now
    existing.profile_fetched_at = now
    await session.flush()
    return existing, False


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------

async def get_repository_by_full_name(
    session: AsyncSession, full_name: str
) -> RepositoryRow | None:
    result = await session.execute(
        select(RepositoryRow).where(RepositoryRow.full_name == full_name)
    )
    return result.scalar_one_or_none()


async def get_repository_by_github_id(
    session: AsyncSession, github_id: int
) -> RepositoryRow | None:
    result = await session.execute(
        select(RepositoryRow).where(RepositoryRow.github_id == github_id)
    )
    return result.scalar_one_or_none()


async def upsert_repository(
    session: AsyncSession,
    repo: Repository,
    *,
    now: datetime | None = None,
) -> tuple[RepositoryRow, bool]:
    """Insert a new repository or update its mutable metadata in place.

    Counters are never written here — they belong to snapshots. Returns
    (row, created).
    """
    now = now or utcnow()
    existing = await get_repository_by_github_id(session, repo.github_id)
    if existing is not None:
        for field in (
            "node_id", "name", "description", "html_url", "homepage",
            "default_branch", "primary_language", "is_fork", "is_archived",
            "is_disabled", "is_template", "visibility", "created_at",
            "updated_at", "pushed_at",
        ):
            setattr(existing, field, getattr(repo, field))
        existing.owner_login = repo.owner_login
        existing.full_name = repo.full_name
        if repo.owner_github_id is not None:
            owner = await _find_developer_by_github_id(session, repo.owner_github_id)
            if owner is not None:
                existing.owner_id = owner.id
        existing.last_seen_at = now
        await session.flush()
        return existing, False

    row = RepositoryRow(
        github_id=repo.github_id,
        node_id=repo.node_id,
        owner_id=None,
        owner_login=repo.owner_login,
        name=repo.name,
        full_name=repo.full_name,
        description=repo.description,
        html_url=repo.html_url,
        homepage=repo.homepage,
        default_branch=repo.default_branch,
        primary_language=repo.primary_language,
        is_fork=repo.is_fork,
        is_archived=repo.is_archived,
        is_disabled=repo.is_disabled,
        is_template=repo.is_template,
        visibility=repo.visibility,
        created_at=repo.created_at,
        updated_at=repo.updated_at,
        pushed_at=repo.pushed_at,
        first_seen_at=now,
        last_seen_at=now,
    )
    if repo.owner_github_id is not None:
        owner = await _find_developer_by_github_id(session, repo.owner_github_id)
        if owner is not None:
            row.owner_id = owner.id
    session.add(row)
    await _flush(session)
    return row, True


async def list_repositories(
    session: AsyncSession,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> Sequence[RepositoryRow]:
    stmt = select(RepositoryRow).order_by(RepositoryRow.last_seen_at.desc())
    if offset:
        stmt = stmt.offset(offset)
    if limit:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------

async def get_or_create_topic(
    session: AsyncSession, name: str, *, now: datetime | None = None
) -> TopicRow:
    now = now or utcnow()
    normalized = normalize_topic(name)
    result = await session.execute(select(TopicRow).where(TopicRow.name == normalized))
    topic = result.scalar_one_or_none()
    if topic is not None:
        return topic
    topic = TopicRow(name=normalized, first_seen_at=now, last_seen_at=now)
    session.add(topic)
    await _flush(session)
    return topic


async def set_repository_topics(
    session: AsyncSession,
    repo_row: RepositoryRow,
    topic_names: Sequence[str],
    *,
    now: datetime | None = None,
) -> None:
    """Replace the topic set of a repository with ``topic_names``.

    The repository keeps all its snapshots — this only touches the topic
    association table.
    """
    now = now or utcnow()
    wanted_names = {normalize_topic(name) for name in topic_names if name.strip()}

    topics: dict[str, TopicRow] = {}
    for name in sorted(wanted_names):
        topic = await get_or_create_topic(session, name, now=now)
        topic.last_seen_at = now
        topics[name] = topic
    wanted_ids = {topic.id for topic in topics.values()}

    result = await session.execute(
        select(RepositoryTopicRow).where(
            RepositoryTopicRow.repository_id == repo_row.id
        )
    )
    links = list(result.scalars().all())
    have_ids = {link.topic_id for link in links}
    for link in links:
        if link.topic_id not in wanted_ids:
            await session.delete(link)
    for topic in topics.values():
        if topic.id not in have_ids:
            session.add(
                RepositoryTopicRow(repository_id=repo_row.id, topic_id=topic.id)
            )
    await session.flush()


# ---------------------------------------------------------------------------
# Contributors
# ---------------------------------------------------------------------------

async def sync_contributors(
    session: AsyncSession,
    repo_row: RepositoryRow,
    contributors: Sequence[Contributor],
    *,
    now: datetime | None = None,
) -> int:
    """Upsert contributor associations for a repository.

    GitHub only exposes a *cumulative* contribution count per contributor over
    the repository's whole history. ``contributions`` is therefore stored
    verbatim and must never be interpreted as recent activity.
    """
    now = now or utcnow()
    created = 0
    for contributor in contributors:
        developer_row, dev_created = await note_contributor(
            session, contributor.developer, now=now
        )
        if dev_created:
            created += 1
        link = (
            await session.execute(
                select(ContributorRow).where(
                    ContributorRow.repository_id == repo_row.id,
                    ContributorRow.developer_id == developer_row.id,
                )
            )
        ).scalar_one_or_none()
        if link is None:
            session.add(
                ContributorRow(
                    repository_id=repo_row.id,
                    developer_id=developer_row.id,
                    contributions=contributor.contributions,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
        else:
            link.contributions = contributor.contributions
            link.last_seen_at = now
    await session.flush()
    return created


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

async def latest_snapshot(
    session: AsyncSession, repository_id: int
) -> SnapshotRow | None:
    result = await session.execute(
        select(SnapshotRow)
        .where(SnapshotRow.repository_id == repository_id)
        .order_by(SnapshotRow.captured_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def snapshots_for_repository(
    session: AsyncSession, repository_id: int
) -> Sequence[SnapshotRow]:
    result = await session.execute(
        select(SnapshotRow)
        .where(SnapshotRow.repository_id == repository_id)
        .order_by(SnapshotRow.captured_at.asc())
    )
    return result.scalars().all()


async def insert_snapshot_if_changed(
    session: AsyncSession,
    repository_id: int,
    snapshot: RepositorySnapshot,
) -> SnapshotRow | None:
    """Insert a snapshot unless the latest one is identical.

    Policy (documented in docs/ARCHITECTURE.md): a snapshot is *only* written
    when observable state (counters or pushed_at) actually differs from the
    newest stored one. This keeps the historical table low-noise while still
    capturing every meaningful change.
    """
    latest = await latest_snapshot(session, repository_id)
    if latest is not None and latest.to_domain().same_counters(snapshot):
        return None
    row = SnapshotRow(
        repository_id=repository_id,
        captured_at=snapshot.captured_at,
        stars=snapshot.stars,
        forks=snapshot.forks,
        watchers=snapshot.watchers,
        open_issues=snapshot.open_issues,
        size_kb=snapshot.size_kb,
        pushed_at=snapshot.pushed_at,
    )
    session.add(row)
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# Queries for the CLI
# ---------------------------------------------------------------------------

async def latest_snapshots_by_repository(
    session: AsyncSession, repository_ids: Sequence[int]
) -> dict[int, SnapshotRow]:
    """Return {repository_id: latest SnapshotRow} for the requested set."""
    if not repository_ids:
        return {}
    latest_ids = (
        select(func.max(SnapshotRow.id))
        .where(SnapshotRow.repository_id.in_(repository_ids))
        .group_by(SnapshotRow.repository_id)
    )
    result = await session.execute(
        select(SnapshotRow).where(SnapshotRow.id.in_(latest_ids))
    )
    return {row.repository_id: row for row in result.scalars().all()}


async def list_repos_with_latest(
    session: AsyncSession,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[tuple[RepositoryRow, SnapshotRow | None]]:
    repos = await list_repositories(session, limit=limit, offset=offset)
    if not repos:
        return []
    latest = await latest_snapshots_by_repository(
        session, [repo.id for repo in repos]
    )
    return [(repo, latest.get(repo.id)) for repo in repos]


async def topics_for_repository(
    session: AsyncSession, repository_id: int
) -> Sequence[TopicRow]:
    result = await session.execute(
        select(TopicRow)
        .join(RepositoryTopicRow, RepositoryTopicRow.topic_id == TopicRow.id)
        .where(RepositoryTopicRow.repository_id == repository_id)
        .order_by(TopicRow.name.asc())
    )
    return result.scalars().all()


async def contributors_for_repository(
    session: AsyncSession,
    repository_id: int,
    *,
    limit: int = 50,
) -> list[tuple[DeveloperRow, int, datetime]]:
    result = await session.execute(
        select(DeveloperRow, ContributorRow.contributions, ContributorRow.last_seen_at)
        .join(ContributorRow, ContributorRow.developer_id == DeveloperRow.id)
        .where(ContributorRow.repository_id == repository_id)
        .order_by(ContributorRow.contributions.desc())
        .limit(limit)
    )
    rows: list[tuple[DeveloperRow, int, datetime]] = []
    for developer, contributions, last_seen in result.all():
        rows.append((developer, contributions, last_seen))
    return rows


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class DatasetStats:
    def __init__(
        self,
        repositories: int,
        developers: int,
        topics: int,
        snapshots: int,
        oldest_snapshot_at: datetime | None,
        newest_snapshot_at: datetime | None,
    ) -> None:
        self.repositories = repositories
        self.developers = developers
        self.topics = topics
        self.snapshots = snapshots
        self.oldest_snapshot_at = oldest_snapshot_at
        self.newest_snapshot_at = newest_snapshot_at


async def compute_stats(session: AsyncSession) -> DatasetStats:
    repo_count = await session.scalar(select(func.count(RepositoryRow.id)))
    dev_count = await session.scalar(select(func.count(DeveloperRow.id)))
    topic_count = await session.scalar(select(func.count(TopicRow.id)))
    snap_count = await session.scalar(select(func.count(SnapshotRow.id)))
    oldest = await session.scalar(select(func.min(SnapshotRow.captured_at)))
    newest = await session.scalar(select(func.max(SnapshotRow.captured_at)))
    return DatasetStats(
        repositories=repo_count or 0,
        developers=dev_count or 0,
        topics=topic_count or 0,
        snapshots=snap_count or 0,
        oldest_snapshot_at=oldest,
        newest_snapshot_at=newest,
    )