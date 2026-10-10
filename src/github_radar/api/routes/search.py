"""Lightweight substring search across tracked entities."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.api.dataset import (
    collect_repositories,
    developer_dataset_and_reports,
)
from github_radar.api.deps import get_session
from github_radar.api.errors import BadRequestError
from github_radar.api.pagination import MAX_LIMIT

router = APIRouter()


@router.get("/search", summary="Search tracked repositories, topics and developers")
async def search(
    q: Annotated[str, Query(min_length=1, max_length=120)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 10,
) -> dict[str, object]:
    """Case-insensitive substring match on names and logins.

    Matches on the display name only (``full_name`` / topic / login), never on
    free-text descriptions, so results stay predictable and cheap.
    """
    query = q.strip().lower()
    if not query:
        raise BadRequestError("q must not be blank.", details={})

    tracked = await collect_repositories(session)
    repository_hits = [
        {
            "full_name": item.full_name,
            "id": item.repository_id,
            "stars": item.report.metrics.latest_counts.get("stars"),
        }
        for item in tracked
        if query in item.full_name.lower()
    ][:limit]

    topic_rows = await _topic_hits(session, query)
    topics = [{"name": name} for name in topic_rows[:limit]]

    _dataset, reports = await developer_dataset_and_reports(session, window_days=7)
    developer_hits = [
        {
            "login": report.login,
            "name": report.name,
            "followers": report.followers,
        }
        for report in reports
        if query in report.login.lower()
    ][:limit]

    return {
        "query": query,
        "repositories": repository_hits,
        "topics": topics,
        "developers": developer_hits,
    }


async def _topic_hits(session: AsyncSession, query: str) -> list[str]:
    from github_radar.storage import repositories as storage

    rows = await storage.topic_names_like(session, query)
    return [name for name, _ in rows]


__all__ = ["router"]