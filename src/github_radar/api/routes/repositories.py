"""Repository listing and detail endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.analytics import compute_metrics, compute_momentum
from github_radar.analytics.confidence import repo_window_confidence
from github_radar.analytics.trends import classify_trend
from github_radar.api.dataset import (
    TrackedRepository,
    collect_repositories,
    ecosystem_graph,
    resolve_repository,
    trend_for,
)
from github_radar.api.deps import get_session, reference_now
from github_radar.api.errors import BadRequestError, NotFoundError
from github_radar.api.pagination import page, parse_pagination
from github_radar.api.serializers import (
    iso,
    serialize_metrics,
    serialize_momentum,
    serialize_repository_bridge,
    serialize_repository_reach,
    serialize_repository_relationship,
    serialize_trend,
)
from github_radar.api.window import parse_window_days
from github_radar.graph.reports import build_repository_report
from github_radar.storage import repositories as storage
from github_radar.util import stale_days

router = APIRouter()

_SORT_FIELDS = ("last_seen", "name", "stars", "forks", "momentum", "stars_7d", "trend")

VALID_SORT_HINT = "one of last_seen, name, stars, forks, momentum, stars_7d, trend"

_TREND_FIELDS = ("rising", "steady", "declining", "inactive", "new")

VALID_TREND_HINT = "one of rising, steady, declining, inactive, new"

_TREND_ORDER = {"rising": 4, "steady": 3, "declining": 2, "new": 1, "inactive": 0}


def _normalize_sort(sort: str) -> str:
    if sort not in _SORT_FIELDS:
        raise BadRequestError(
            f"sort must be {VALID_SORT_HINT}, got {sort!r}.",
            details={"sort": sort, "allowed": list(_SORT_FIELDS)},
        )
    return sort


def _split(value: int | float | datetime | None) -> tuple[bool, object]:
    """Sort key that always places ``None`` last regardless of direction."""
    return (value is not None, value)


def _sort_value(
    item: TrackedRepository, sort: str, *, reference_now: datetime
) -> tuple[bool, object]:
    if sort == "last_seen":
        return _split(item.last_seen_at)
    if sort == "name":
        return (True, item.full_name)
    if sort == "stars":
        return _split(item.report.metrics.latest_counts.get("stars"))
    if sort == "forks":
        return _split(item.report.metrics.latest_counts.get("forks"))
    if sort == "momentum":
        return _split(item.report.momentum.score if item.report.momentum is not None else None)
    if sort == "stars_7d":
        return _split(item.report.metrics.stars_7d.delta if item.report.metrics.stars_7d else None)
    trend = trend_for(item.report, reference_now)
    return (True, _TREND_ORDER.get(trend.trend, -1) if trend is not None else -1)


def _passes_filters(
    item: TrackedRepository,
    *,
    reference_now: datetime,
    language: str | None,
    trend: str | None,
    min_stars: int | None,
    max_stars: int | None,
    archived: bool | None,
    fork: bool | None,
) -> bool:
    if language is not None:
        if (item.primary_language or "").lower() != language.lower():
            return False
    if trend is not None:
        current = trend_for(item.report, reference_now)
        if current is None or current.trend != trend:
            return False
    stars = item.report.metrics.latest_counts.get("stars")
    if min_stars is not None and (stars is None or stars < min_stars):
        return False
    if max_stars is not None and (stars is None or stars > max_stars):
        return False
    if archived is not None and item.is_archived != archived:
        return False
    if fork is not None and item.is_fork != fork:
        return False
    return True


@router.get("/repositories", summary="List tracked repositories with filter + sort")
async def list_repositories(
    session: Annotated[AsyncSession, Depends(get_session)],
    sort: Annotated[str, Query()] = "last_seen",
    order: Annotated[str, Query()] = "desc",
    language: Annotated[str | None, Query()] = None,
    trend: Annotated[str | None, Query()] = None,
    min_stars: Annotated[int | None, Query(ge=0)] = None,
    max_stars: Annotated[int | None, Query(ge=0)] = None,
    archived: Annotated[bool | None, Query()] = None,
    fork: Annotated[bool | None, Query()] = None,
    limit: Annotated[int | None, Query()] = None,
    offset: Annotated[int | None, Query()] = None,
) -> dict[str, object]:
    """Paginate tracked repositories with optional filter + sort.

    ``sort`` selects the key, ``order`` its direction (``asc``/``desc``).
    Momentum here is always scored over the fixed 7-day window, so no window
    parameter is accepted — a request cannot ask for a window the score ignores.
    ``limit`` is capped at 100; larger values are rejected rather than clamped.
    """
    _normalize_sort(sort)
    if order not in ("asc", "desc"):
        raise BadRequestError(
            "order must be one of asc, desc.",
            details={"order": order, "allowed": ["asc", "desc"]},
        )
    if trend is not None and trend not in _TREND_FIELDS:
        raise BadRequestError(
            f"trend must be {VALID_TREND_HINT}, got {trend!r}.",
            details={"trend": trend, "allowed": list(_TREND_FIELDS)},
        )
    parsed_limit, parsed_offset = parse_pagination(limit, offset)
    reference = await reference_now(session)
    tracked = await collect_repositories(session)

    kept = [
        item
        for item in tracked
        if _passes_filters(
            item,
            reference_now=reference,
            language=language,
            trend=trend,
            min_stars=min_stars,
            max_stars=max_stars,
            archived=archived,
            fork=fork,
        )
    ]
    kept.sort(
        key=lambda item: (
            _sort_value(item, sort, reference_now=reference),
            item.full_name,
        ),
        reverse=order == "desc",
    )
    total = len(kept)
    selected = kept[parsed_offset : parsed_offset + parsed_limit]
    return page(
        [_summary(item, reference_now=reference) for item in selected],
        limit=parsed_limit,
        offset=parsed_offset,
        total=total,
    )


@router.get("/repositories/summary", summary="Aggregate momentum across tracked repos")
async def repositories_summary(
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Annotated[str, Query(pattern=r"^(1|7|30)d?$")] = "7d",
) -> dict[str, object]:
    """Cross-repository averages: velocity, trend classes and healthy history.

    Averages are computed only over repositories with usable history, so a
    young dataset does not drag everything to zero.
    """
    window_days = parse_window_days(window)
    reference = await reference_now(session)
    tracked = await collect_repositories(session)
    if not tracked:
        return {
            "window_days": window_days,
            "repository_count": 0,
            "healthy_count": 0,
            "average_daily_stars": None,
            "average_daily_forks": None,
            "trend_classes": {},
            "high_confidence_count": 0,
        }

    healthy_stars: list[float] = []
    healthy_forks: list[float] = []
    for item in tracked:
        stars = item.report.metrics.stars_7d
        forks = item.report.metrics.forks_7d
        if (
            stars is not None
            and stars.per_day is not None
            and forks is not None
            and forks.per_day is not None
        ):
            healthy_stars.append(stars.per_day)
            healthy_forks.append(forks.per_day)
    average_daily_stars = sum(healthy_stars) / len(healthy_stars) if healthy_stars else None
    average_daily_forks = sum(healthy_forks) / len(healthy_forks) if healthy_forks else None

    trend_classes: dict[str, int] = {}
    high_confidence = 0
    for item in tracked:
        trend = trend_for(item.report, reference)
        if trend is not None:
            trend_classes[trend.trend] = trend_classes.get(trend.trend, 0) + 1
        confidence = repo_window_confidence(item.report.metrics, window_days=7)
        if confidence.level == "HIGH":
            high_confidence += 1

    return {
        "window_days": window_days,
        "repository_count": len(tracked),
        "healthy_count": len(healthy_stars),
        "average_daily_stars": average_daily_stars,
        "average_daily_forks": average_daily_forks,
        "trend_classes": dict(sorted(trend_classes.items())),
        "high_confidence_count": high_confidence,
    }


def _summary(item: TrackedRepository, *, reference_now: datetime) -> dict[str, object]:
    confidence = repo_window_confidence(item.report.metrics, window_days=7)
    return {
        "id": item.repository_id,
        "full_name": item.full_name,
        "owner_login": item.owner_login,
        "primary_language": item.primary_language,
        "description": item.description,
        "homepage": item.homepage,
        "html_url": item.html_url,
        "is_archived": item.is_archived,
        "is_fork": item.is_fork,
        "pushed_at": iso(item.pushed_at),
        "first_seen_at": iso(item.first_seen_at),
        "last_seen_at": iso(item.last_seen_at),
        "stale_days": stale_days(item.report.metrics.latest_captured_at),
        "topics": list(item.topics),
        "stars": item.report.metrics.latest_counts.get("stars"),
        "forks": item.report.metrics.latest_counts.get("forks"),
        "watchers": item.report.metrics.latest_counts.get("watchers"),
        "open_issues": item.report.metrics.latest_counts.get("open_issues"),
        "momentum": serialize_momentum(item.report.momentum),
        "trend": serialize_trend(trend_for(item.report, reference_now)),
        "confidence": {"level": confidence.level, "score": confidence.score},
    }


@router.get(
    "/repositories/{ref:path}",
    summary="Full detail for one tracked repository",
)
async def repository_detail(
    ref: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Annotated[str, Query(pattern=r"^(1|7|30)d?$")] = "7d",
    related_limit: Annotated[int, Query(ge=1)] = 5,
) -> dict[str, object]:
    """Metadata, topics, snapshot history, growth windows, momentum and graph."""
    window_days = parse_window_days(window)
    if related_limit > 20:
        related_limit = 20

    row = await resolve_repository(session, ref)
    if row is None:
        raise NotFoundError(
            f"Repository {ref!r} is not tracked.",
            details={"ref": ref},
        )

    topics = await storage.topics_for_repository(session, row.id)
    contributors = await storage.contributors_for_repository(session, row.id)
    snapshots = await storage.snapshots_for_repository(session, row.id)

    metrics = compute_metrics([snapshot.to_domain() for snapshot in snapshots])
    latest = metrics.latest_captured_at
    momentum = compute_momentum(metrics, reference_now=latest) if latest else None
    trend = classify_trend(metrics, momentum, reference_now=latest) if latest else None
    confidence = repo_window_confidence(metrics, window_days=7) if latest else None

    reference = await reference_now(session)
    graph = await ecosystem_graph(session, reference)

    graph_report = None
    if row.id in graph.repository_nodes:
        graph_report = build_repository_report(
            graph,
            row.id,
            reference_now=reference,
            window_days=window_days,
            related_limit=related_limit,
        )

    return {
        "id": row.id,
        "github_id": row.github_id,
        "full_name": row.full_name,
        "owner_login": row.owner_login,
        "owner_id": row.owner_id,
        "name": row.name,
        "description": row.description,
        "html_url": row.html_url,
        "homepage": row.homepage,
        "default_branch": row.default_branch,
        "primary_language": row.primary_language,
        "visibility": row.visibility,
        "flags": {
            "fork": row.is_fork,
            "archived": row.is_archived,
            "disabled": row.is_disabled,
            "template": bool(row.is_template),
        },
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
        "pushed_at": iso(row.pushed_at),
        "first_seen_at": iso(row.first_seen_at),
        "last_seen_at": iso(row.last_seen_at),
        "stale_days": stale_days(metrics.latest_captured_at),
        "topics": [topic.name for topic in topics],
        "snapshot_count": len(snapshots),
        "metrics": serialize_metrics(metrics),
        "momentum": serialize_momentum(momentum),
        "trend": serialize_trend(trend),
        "confidence": (
            {
                "level": confidence.level,
                "score": confidence.score,
                "snapshot_count": confidence.snapshot_count,
                "history_days": confidence.history_days,
            }
            if confidence is not None
            else None
        ),
        "stars_7d": (metrics.stars_7d.delta if metrics.stars_7d else None),
        "stars_30d": (metrics.stars_30d.delta if metrics.stars_30d else None),
        "contributors": [
            {
                "login": developer.login,
                "name": developer.name,
                "contributions": contributions,
                "last_seen_at": iso(last_seen),
            }
            for developer, contributions, last_seen in contributors
        ],
        "graph": (
            {
                "reach": serialize_repository_reach(graph_report.reach),
                "centrality": graph_report.centrality,
                "related_repositories": [
                    serialize_repository_relationship(relationship)
                    for relationship in graph_report.related_repositories
                ],
            }
            if graph_report is not None
            else None
        ),
        "bridge": (
            serialize_repository_bridge(graph_report.bridge)
            if graph_report is not None and graph_report.bridge is not None
            else None
        ),
    }


__all__ = ["router"]