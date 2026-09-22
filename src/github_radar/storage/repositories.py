"""Persistence operations: upserts, relationships and queries.

Functions in this module accept domain objects and translate them to/from the
SQLAlchemy ORM rows. They operate on a caller-supplied
:class:`sqlalchemy.ext.asyncio.AsyncSession`; committing is the caller's job.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.domain import Contributor, Developer, Repository, RepositorySnapshot
from github_radar.storage.models import (
    ContributorRow,
    ContributorSnapshotRow,
    DeveloperRow,
    DeveloperSnapshotRow,
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

    Every profile fetch records one :class:`DeveloperSnapshotRow` at ``now``
    (idempotent per ``(developer_id, captured_at)``), so follower/repo-count
    deltas become verifiable history instead of a single instantaneous value.
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
        await _record_profile_snapshot(session, row, developer, now=now)
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
    await _record_profile_snapshot(session, existing, developer, now=now)
    await session.flush()
    return existing, False


async def _record_profile_snapshot(
    session: AsyncSession,
    row: DeveloperRow,
    developer: Developer,
    *,
    now: datetime,
) -> DeveloperSnapshotRow:
    """Upsert one profile observation at ``now`` (idempotent per instant)."""
    return await add_developer_snapshot(
        session,
        developer_id=row.id,
        captured_at=now,
        followers=developer.followers,
        following=developer.following,
        public_repos=developer.public_repos,
    )


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

@dataclass(frozen=True)
class ContributorSyncResult:
    """Outcome of one contributor re-observation for a repository."""

    developers_created: int
    snapshots_written: int


async def sync_contributors(
    session: AsyncSession,
    repo_row: RepositoryRow,
    contributors: Sequence[Contributor],
    *,
    now: datetime | None = None,
) -> ContributorSyncResult:
    """Upsert contributor associations AND record one observation per link.

    GitHub only exposes a *cumulative* contribution count per contributor over
    the repository's whole history. ``contributions`` is therefore stored
    verbatim and must never be interpreted as recent activity. Every re-fetch
    additionally records a :class:`ContributorSnapshotRow` at ``now``
    (idempotent per ``(repository, developer, captured_at)``) so the cumulative
    count can be turned into a truthful delta between observations later.
    """
    now = now or utcnow()
    created = 0
    snapshots_written = 0
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
        await record_contributor_snapshot(
            session,
            repository_id=repo_row.id,
            developer_id=developer_row.id,
            captured_at=now,
            contributions=contributor.contributions,
        )
        snapshots_written += 1
    await session.flush()
    return ContributorSyncResult(
        developers_created=created, snapshots_written=snapshots_written
    )


# ---------------------------------------------------------------------------
# Developer & contributor observations (append-only history)
# ---------------------------------------------------------------------------

async def add_developer_snapshot(
    session: AsyncSession,
    *,
    developer_id: int,
    captured_at: datetime,
    followers: int | None,
    following: int | None,
    public_repos: int | None,
) -> DeveloperSnapshotRow:
    """Persist one developer profile observation at ``captured_at``.

    Idempotent per ``(developer_id, captured_at)``: an observation at an
    already-captured instant replaces that row; a new instant appends. History
    from other instants is never touched. The unique constraint enforces the
    same-instant guarantee at the database level.
    """
    returning = (
        select(DeveloperSnapshotRow.id)
        .where(
            DeveloperSnapshotRow.developer_id == developer_id,
            DeveloperSnapshotRow.captured_at == captured_at,
        )
        .limit(1)
    )
    row_id = (await session.execute(returning)).scalar_one_or_none()
    if row_id is None:
        row = DeveloperSnapshotRow(
            developer_id=developer_id,
            captured_at=captured_at,
            followers=followers,
            following=following,
            public_repos=public_repos,
        )
        session.add(row)
    else:
        existing = await session.get(DeveloperSnapshotRow, row_id)
        assert existing is not None
        existing.followers = followers
        existing.following = following
        existing.public_repos = public_repos
        row = existing
    await session.flush()
    return row


async def record_contributor_snapshot(
    session: AsyncSession,
    *,
    repository_id: int,
    developer_id: int,
    captured_at: datetime,
    contributions: int,
) -> ContributorSnapshotRow:
    """Persist one contributor observation at ``captured_at``.

    Same-instant idempotency is per ``(repository_id, developer_id,
    captured_at)``; a new instant appends and historical rows are never
    overwritten. ``contributions`` is GitHub's cumulative value, verbatim.
    """
    returning = (
        select(ContributorSnapshotRow.id)
        .where(
            ContributorSnapshotRow.repository_id == repository_id,
            ContributorSnapshotRow.developer_id == developer_id,
            ContributorSnapshotRow.captured_at == captured_at,
        )
        .limit(1)
    )
    row_id = (await session.execute(returning)).scalar_one_or_none()
    if row_id is None:
        row = ContributorSnapshotRow(
            repository_id=repository_id,
            developer_id=developer_id,
            captured_at=captured_at,
            contributions=contributions,
        )
        session.add(row)
    else:
        existing = await session.get(ContributorSnapshotRow, row_id)
        assert existing is not None
        existing.contributions = contributions
        row = existing
    await session.flush()
    return row


async def developer_snapshots_for(
    session: AsyncSession, developer_id: int
) -> Sequence[DeveloperSnapshotRow]:
    """A developer's profile observation history, oldest first."""
    result = await session.execute(
        select(DeveloperSnapshotRow)
        .where(DeveloperSnapshotRow.developer_id == developer_id)
        .order_by(DeveloperSnapshotRow.captured_at.asc())
    )
    return result.scalars().all()


async def contributor_snapshots_for_relationship(
    session: AsyncSession,
    *,
    repository_id: int,
    developer_id: int,
) -> Sequence[ContributorSnapshotRow]:
    """One relationship's observation history, oldest first."""
    result = await session.execute(
        select(ContributorSnapshotRow)
        .where(
            ContributorSnapshotRow.repository_id == repository_id,
            ContributorSnapshotRow.developer_id == developer_id,
        )
        .order_by(ContributorSnapshotRow.captured_at.asc())
    )
    return result.scalars().all()


async def contributor_snapshots_for_repository(
    session: AsyncSession, repository_id: int
) -> Sequence[ContributorSnapshotRow]:
    """All contributor observations for one repository, oldest first."""
    result = await session.execute(
        select(ContributorSnapshotRow)
        .where(ContributorSnapshotRow.repository_id == repository_id)
        .order_by(ContributorSnapshotRow.captured_at.asc())
    )
    return result.scalars().all()


async def contributor_snapshots_for_developer(
    session: AsyncSession, developer_id: int
) -> Sequence[ContributorSnapshotRow]:
    """All contributor observations for one developer, oldest first."""
    result = await session.execute(
        select(ContributorSnapshotRow)
        .where(ContributorSnapshotRow.developer_id == developer_id)
        .order_by(ContributorSnapshotRow.captured_at.asc())
    )
    return result.scalars().all()


async def contributor_links_for_developer(
    session: AsyncSession, developer_id: int
) -> Sequence[RepositoryRow]:
    """The tracked repositories a developer contributes to."""
    result = await session.execute(
        select(RepositoryRow)
        .join(ContributorRow, ContributorRow.repository_id == RepositoryRow.id)
        .where(ContributorRow.developer_id == developer_id)
        .order_by(RepositoryRow.full_name.asc())
    )
    return result.scalars().all()


@dataclass(frozen=True)
class ContributorLinkRecord:
    """One contributor association with its current cumulative value."""

    repository_id: int
    developer_id: int
    contributions: int
    developer_login: str
    developer_github_id: int


async def list_contributor_links(
    session: AsyncSession,
) -> Sequence[ContributorLinkRecord]:
    """Every ``repository_contributors`` row joined with its developer login."""
    result = await session.execute(
        select(ContributorRow).order_by(
            ContributorRow.developer_id.asc(),
            ContributorRow.repository_id.asc(),
        )
    )
    return [
        ContributorLinkRecord(
            repository_id=row.repository_id,
            developer_id=row.developer_id,
            contributions=row.contributions,
            developer_login=row.developer.login,
            developer_github_id=row.developer.github_id,
        )
        for row in result.scalars().all()
    ]


async def all_contributor_snapshots(
    session: AsyncSession,
) -> Sequence[ContributorSnapshotRow]:
    """Every contributor observation in the dataset, oldest first."""
    result = await session.execute(
        select(ContributorSnapshotRow).order_by(
            ContributorSnapshotRow.captured_at.asc()
        )
    )
    return result.scalars().all()


async def all_developer_snapshots(
    session: AsyncSession,
) -> Sequence[DeveloperSnapshotRow]:
    """Every developer profile observation in the dataset, oldest first."""
    result = await session.execute(
        select(DeveloperSnapshotRow).order_by(
            DeveloperSnapshotRow.captured_at.asc()
        )
    )
    return result.scalars().all()


async def get_developers_by_id(
    session: AsyncSession,
    developer_ids: Sequence[int],
) -> Sequence[DeveloperRow]:
    """Developer rows for ``developer_ids`` (order not preserved)."""
    if not developer_ids:
        return []
    result = await session.execute(
        select(DeveloperRow).where(DeveloperRow.id.in_(developer_ids))
    )
    return result.scalars().all()


async def newest_observation_at(session: AsyncSession) -> datetime | None:
    """The newest contributor/repository snapshot ``captured_at`` dataset-wide.

    Used as the deterministic ``reference_now`` anchor for developer analytics:
    never the wall clock, always a real observed instant.
    """
    newest = await session.scalar(
        select(func.max(ContributorSnapshotRow.captured_at))
    )
    if newest is None:
        newest = await session.scalar(select(func.max(SnapshotRow.captured_at)))
    return newest


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


def _apply_snapshot(row: SnapshotRow, snapshot: RepositorySnapshot) -> None:
    """Overwrite a snapshot row's observed fields from a domain snapshot."""
    row.captured_at = snapshot.captured_at
    row.stars = snapshot.stars
    row.forks = snapshot.forks
    row.watchers = snapshot.watchers
    row.open_issues = snapshot.open_issues
    row.size_kb = snapshot.size_kb
    row.pushed_at = snapshot.pushed_at


async def record_observation(
    session: AsyncSession,
    repository_id: int,
    snapshot: RepositorySnapshot,
) -> SnapshotRow:
    """Persist one observation at ``snapshot.captured_at``, unconditionally.

    This is the *state change* free form of snapshot persistence, for callers
    that have genuinely polled the repository (a future scheduler, for
    example). An unchanged repository observed at two different instants is
    stored as **two** historical rows, so analytics can tell a truly-observed
    zero-growth period apart from missing history.

    Idempotency is per ``(repository_id, captured_at)``: an observation at an
    already-captured instant replaces that row (a raw correction of a bad
    capture), and a *new* instant appends. Historical rows from other instants
    are never touched. The unique constraint enforces the same-instant
    guarantee at the database level.
    """
    returning = (
        select(SnapshotRow.id)
        .where(
            SnapshotRow.repository_id == repository_id,
            SnapshotRow.captured_at == snapshot.captured_at,
        )
        .limit(1)
    )
    row_id = (await session.execute(returning)).scalar_one_or_none()
    if row_id is None:
        row = SnapshotRow(repository_id=repository_id, captured_at=snapshot.captured_at)
        session.add(row)
        _apply_snapshot(row, snapshot)
    else:
        existing = await session.get(SnapshotRow, row_id)
        assert existing is not None
        _apply_snapshot(existing, snapshot)
        row = existing
    await session.flush()
    return row


async def insert_snapshot_if_changed(
    session: AsyncSession,
    repository_id: int,
    snapshot: RepositorySnapshot,
) -> SnapshotRow | None:
    """Insert a snapshot unless the latest one is identical.

    The Phase 1 *state-change-only* policy: a snapshot is written only when
    observable state (counters or pushed_at) actually differs from the newest
    stored one — so a repository that was re-observed but unchanged is *not*
    recorded here. When state differs, writes delegate to
    :func:`record_observation` (same-instant re-observation replaces; a new
    instant appends).

    Periodic pollers that must record *every* observation — including unchanged
    ones — should call :func:`record_observation` directly instead of this
    filter.
    """
    latest = await latest_snapshot(session, repository_id)
    if latest is not None and latest.to_domain().same_counters(snapshot):
        return None
    return await record_observation(session, repository_id, snapshot)


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