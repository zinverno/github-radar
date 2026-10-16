"""Collection persistence; transaction ownership stays with the caller."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import event, func, or_, select, text
from sqlalchemy.engine import ExceptionContext
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import Session

from github_radar.domain import Contributor, Developer
from github_radar.storage.models import (
    ContributorRow,
    DeveloperRow,
    DiscoveryProvenanceRow,
    RepositoryRow,
    SnapshotRow,
)


class CollectionLockError(RuntimeError):
    """Collection ownership is unavailable or irreversibly lost for this run."""


@asynccontextmanager
async def locked_collection_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Pin ownership and incremental transactions to one backend on a dedicated engine."""
    async with engine.connect() as connection:
        if not await connection.scalar(text("SELECT pg_try_advisory_lock(6742565)")):
            raise CollectionLockError(
                "Another collection process holds the database collection lock"
            )
        # The Session must own its transactions, not join the acquisition transaction.
        await connection.commit()
        lost = False

        def refuse_reconnect(*args: object) -> None:
            nonlocal lost
            lost = True
            raise CollectionLockError("Collection lock ownership lost; reconnection refused")

        def handle_disconnect(context: ExceptionContext) -> Exception | None:
            nonlocal lost
            if isinstance(context.original_exception, CollectionLockError):
                return context.original_exception
            if context.is_disconnect:
                lost = True
                return CollectionLockError("Collection lock ownership lost; backend disconnected")
            return None

        def require_owner(session: Session) -> None:
            nonlocal lost
            if lost or connection.closed or connection.invalidated:
                lost = True
                raise CollectionLockError("Collection lock ownership lost")
            # Use the Session's transaction so the check and commit share the pinned backend.
            owned = session.connection().scalar(text("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_locks
                    WHERE locktype='advisory' AND pid=pg_backend_pid()
                      AND classid=0 AND objid=6742565 AND objsubid=1 AND granted
                      AND mode='ExclusiveLock'
                )
            """))
            if not owned:
                lost = True
                raise CollectionLockError("Collection lock ownership lost")

        event.listen(engine.sync_engine, "do_connect", refuse_reconnect)
        event.listen(engine.sync_engine, "handle_error", handle_disconnect, retval=True)
        try:
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                event.listen(session.sync_session, "before_commit", require_owner)
                yield session
        finally:
            try:
                # Never reconnect for cleanup or unlock on a replacement backend.
                if not lost and not connection.closed and not connection.invalidated:
                    released = await connection.scalar(text("SELECT pg_advisory_unlock(6742565)"))
                    if not released:
                        raise CollectionLockError("Collection lock ownership lost before release")
                    await connection.commit()
            finally:
                event.remove(engine.sync_engine, "do_connect", refuse_reconnect)
                event.remove(engine.sync_engine, "handle_error", handle_disconnect)


async def note_provenance(
    session: AsyncSession,
    repository_id: int,
    *,
    category: str,
    recipe: str,
    query: str,
    run_id: str,
    now: datetime,
) -> None:
    digest = hashlib.sha256(query.encode()).hexdigest()
    key = (repository_id, category, recipe, digest)
    row = await session.get(DiscoveryProvenanceRow, key)
    if row is None:
        session.add(
            DiscoveryProvenanceRow(
                repository_id=repository_id,
                category=category,
                recipe=recipe,
                query_hash=digest,
                query=query,
                first_seen_at=now,
                last_seen_at=now,
                last_run_id=run_id,
            )
        )
    else:
        row.last_seen_at = now
        row.last_run_id = run_id


async def stale_repositories(
    session: AsyncSession,
    *,
    cutoff: datetime,
    limit: int,
) -> Sequence[RepositoryRow]:
    latest = (
        select(SnapshotRow.repository_id, func.max(SnapshotRow.captured_at).label("observed_at"))
        .group_by(SnapshotRow.repository_id)
        .subquery()
    )
    result = await session.execute(
        select(RepositoryRow)
        .outerjoin(latest, latest.c.repository_id == RepositoryRow.id)
        .where(or_(latest.c.observed_at.is_(None), latest.c.observed_at <= cutoff))
        .order_by(latest.c.observed_at.asc().nullsfirst(), RepositoryRow.github_id.asc())
        .limit(limit)
    )
    return result.scalars().all()


async def cached_contributors(
    session: AsyncSession,
    repository_id: int,
    *,
    limit: int,
) -> list[Contributor]:
    rows = await session.execute(
        select(ContributorRow, DeveloperRow)
        .join(DeveloperRow, DeveloperRow.id == ContributorRow.developer_id)
        .join(RepositoryRow, RepositoryRow.id == ContributorRow.repository_id)
        .where(ContributorRow.repository_id == repository_id)
        .where(ContributorRow.last_seen_at == RepositoryRow.contributors_fetched_at)
        .order_by(ContributorRow.contributions.desc(), DeveloperRow.github_id.asc())
        .limit(limit)
    )
    return [
        Contributor(
            developer=Developer(github_id=developer.github_id, login=developer.login),
            contributions=link.contributions,
        )
        for link, developer in rows
    ]
