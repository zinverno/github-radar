"""Topic listing and detail endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.analytics import RepositoryReport
from github_radar.analytics.confidence import repo_window_confidence
from github_radar.analytics.topics import aggregate_topics
from github_radar.analytics.trends import rank_repositories
from github_radar.api.dataset import (
    collect_repositories,
    ecosystem_graph,
    trend_for,
)
from github_radar.api.deps import get_session, reference_now
from github_radar.api.errors import NotFoundError
from github_radar.api.pagination import page, parse_pagination
from github_radar.api.serializers import (
    serialize_momentum,
    serialize_topic_aggregate,
    serialize_topic_reach,
    serialize_topic_relationship,
    serialize_trend,
)
from github_radar.api.window import parse_window_days
from github_radar.graph.reports import build_topic_report
from github_radar.storage import repositories as storage

router = APIRouter()


@router.get("/topics", summary="List observed topics with repository counts")
async def list_topics(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int | None, Query()] = None,
    offset: Annotated[int | None, Query()] = None,
) -> dict[str, object]:
    """Paginate the topic vocabulary the dataset has observed, by coverage."""
    parsed_limit, parsed_offset = parse_pagination(limit, offset)
    rows = await storage.topics_with_repository_counts(session)
    total = len(rows)
    selected = rows[parsed_offset : parsed_offset + parsed_limit]
    return page(
        [
            {"name": name, "repository_count": count}
            for name, count in selected
        ],
        limit=parsed_limit,
        offset=parsed_offset,
        total=total,
    )


@router.get("/topics/{name}", summary="Full detail for one topic")
async def topic_detail(
    name: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Annotated[str, Query(pattern=r"^(1|7|30)d?$")] = "7d",
    related_limit: Annotated[int, Query(ge=1)] = 5,
    repo_limit: Annotated[int, Query(ge=1)] = 10,
) -> dict[str, object]:
    """Momentum aggregate, participating repositories and related topics."""
    window_days = parse_window_days(window)
    if related_limit > 20:
        related_limit = 20
    if repo_limit > 100:
        repo_limit = 100

    reference = await reference_now(session)
    topic_name = storage.normalize_topic(name)

    tracked = await collect_repositories(session)
    members = [
        item
        for item in tracked
        if topic_name in {storage.normalize_topic(tag) for tag in item.topics}
    ]
    if not members:
        raise NotFoundError(f"Topic {name!r} is not tracked.", details={"name": name})

    by_topic: dict[str, list[RepositoryReport]] = {}
    for item in tracked:
        for tag in item.topics:
            by_topic.setdefault(storage.normalize_topic(tag), []).append(item.report)
    aggregate = next(
        (
            aggregate
            for aggregate in aggregate_topics(by_topic)
            if aggregate.name == topic_name
        ),
        None,
    )

    ranked_by_name = {
        ranking.full_name: ranking
        for ranking in rank_repositories([item.report for item in members])
    }
    ordered = sorted(
        members,
        key=lambda item: (
            0 if item.full_name in ranked_by_name else 1,
            -ranked_by_name[item.full_name].score
            if item.full_name in ranked_by_name
            else 0.0,
            item.full_name,
        ),
    )

    repo_rows = []
    for member in ordered[:repo_limit]:
        ranked = ranked_by_name.get(member.full_name)
        confidence = repo_window_confidence(member.report.metrics, window_days=window_days)
        repo_rows.append(
            {
                "full_name": member.full_name,
                "id": member.repository_id,
                "stars": member.report.metrics.latest_counts.get("stars"),
                "topics": list(member.topics),
                "score": ranked.score if ranked is not None else None,
                "stars_growth_pct": ranked.stars_growth_pct if ranked is not None else None,
                "forks_growth_pct": ranked.forks_growth_pct if ranked is not None else None,
                "recency": ranked.recency if ranked is not None else None,
                "momentum": serialize_momentum(member.report.momentum),
                "trend": serialize_trend(trend_for(member.report, reference)),
                "confidence": {
                    "level": confidence.level,
                    "score": confidence.score,
                },
            }
        )

    graph = await ecosystem_graph(session, reference)
    graph_report = None
    node = graph.topic_by_name(topic_name)
    if node is not None:
        graph_report = build_topic_report(
            graph,
            node.topic_id,
            related_limit=related_limit,
        )

    return {
        "name": topic_name,
        "id": node.topic_id if node is not None else None,
        "repository_count": len(members),
        "window_days": window_days,
        "aggregate": serialize_topic_aggregate(aggregate) if aggregate else None,
        "repositories": repo_rows,
        "graph": (
            {
                "reach": serialize_topic_reach(graph_report.reach),
                "centrality": graph_report.centrality,
                "related_topics": [
                    serialize_topic_relationship(relationship)
                    for relationship in graph_report.related_topics
                ],
            }
            if graph_report is not None
            else None
        ),
    }


__all__ = ["router"]