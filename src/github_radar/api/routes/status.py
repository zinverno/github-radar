"""Health, dataset status and the SPA landing-pad overview (explore)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.analytics.trends import rank_repositories
from github_radar.api.dataset import (
    collect_repositories,
    developer_dataset_and_reports,
    ecosystem_graph,
    freshness,
    topic_aggregates,
)
from github_radar.api.dataset import (
    trend_for as repository_trend_for,
)
from github_radar.api.deps import get_session, get_settings, reference_now
from github_radar.api.pagination import MAX_LIMIT
from github_radar.api.serializers import (
    graph_counts,
    serialize_developer_bridge,
    serialize_emerging,
    serialize_topic_aggregate,
    serialize_trend,
)
from github_radar.api.window import parse_window_days
from github_radar.config import Settings
from github_radar.graph.bridges import developer_bridges
from github_radar.graph.reports import build_ecosystem_report
from github_radar.storage import repositories as storage
from github_radar.util import stale_days, utcnow

router = APIRouter()


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    """Trivial liveness probe (no database access)."""
    return {"status": "ok"}


def _ai_configured(settings: Settings) -> tuple[bool, str | None]:
    try:
        base_url = settings.require_ai_config()[0]
    except Exception:
        return False, None
    from urllib.parse import urlparse

    host = urlparse(base_url).netloc or base_url
    return True, host


@router.get("/status", summary="Dataset and configuration status")
async def status(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    """How much history is stored, the reference instant, and what is enabled."""
    from github_radar.ai.cache import SqlArtifactStore

    stats = await storage.compute_stats(session)
    ai_configured, ai_host = _ai_configured(settings)
    artifact_stats = await SqlArtifactStore(session).stats()
    return {
        "reference_time": (await storage.newest_observation_at(session) or utcnow()).isoformat(),
        "freshness": await freshness(session),
        "dataset": {
            "repositories": stats.repositories,
            "developers": stats.developers,
            "topics": stats.topics,
            "snapshots": stats.snapshots,
            "oldest_snapshot_at": (
                stats.oldest_snapshot_at.isoformat() if stats.oldest_snapshot_at else None
            ),
            "newest_snapshot_at": (
                stats.newest_snapshot_at.isoformat() if stats.newest_snapshot_at else None
            ),
        },
        "ai": {
            "configured": ai_configured,
            "base_url_host": ai_host,
            "model": settings.ai_model or None,
            "per_run_request_cap": settings.ai_max_requests_per_run,
            "max_output_tokens": settings.ai_max_output_tokens,
            "max_evidence_chars": settings.ai_max_evidence_chars,
            "cached_artifacts": sum(artifact_stats.values()),
            "cached_by_type": dict(artifact_stats),
        },
    }


@router.get(
    "/explore",
    summary="Dashboard overview: trending, topics, emerging, ecosystem, bridges",
)
async def explore(
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Annotated[str, Query(pattern=r"^(1|7|30)d?$")] = "7d",
    limit: Annotated[int, Query(ge=1)] = 10,
) -> dict[str, object]:
    """One call the dashboard makes on load: the top signals in each dimension."""
    if limit > MAX_LIMIT:
        limit = MAX_LIMIT
    window_days = parse_window_days(window)
    reference = await reference_now(session)

    tracked = await collect_repositories(session)
    ranked = rank_repositories([item.report for item in tracked])
    by_full_name = {item.full_name: item for item in tracked}
    repo_rows = [
        {
            "full_name": row.full_name,
            "stars": row.stars,
            "score": row.score,
            "stars_growth_pct": row.stars_growth_pct,
            "forks_growth_pct": row.forks_growth_pct,
            "recency": row.recency,
            "confidence": row.confidence,
            "stale_days": stale_days(
                by_full_name[row.full_name].report.metrics.latest_captured_at
            ),
            **(
                {
                    "trend": serialize_trend(
                        repository_trend_for(by_full_name[row.full_name].report, reference)
                    )
                }
                if row.full_name in by_full_name
                else {}
            ),
        }
        for row in ranked[:limit]
    ]

    aggregates = await topic_aggregates(session)
    topic_rows = [
        serialize_topic_aggregate(aggregate) for aggregate in aggregates[:limit]
    ]

    _dataset, reports = await developer_dataset_and_reports(
        session, window_days=window_days
    )
    emerging = [
        report
        for report in reports
        if report.emerging.label == "EMERGING"
    ]
    emerging_rows = [
        {
            "login": report.login,
            "name": report.name,
            "followers": report.followers,
            "associated_repositories": report.associations,
            "emerging": serialize_emerging(report.emerging),
        }
        for report in sorted(
            emerging, key=lambda r: r.emerging.score, reverse=True
        )[:limit]
    ]

    graph = await ecosystem_graph(session, reference)
    ecosystem_report = build_ecosystem_report(
        graph,
        reference_now=reference,
        window_days=window_days,
        centrality_limit=5,
        bridge_limit=5,
    )
    bridge_rows = [
        serialize_developer_bridge(bridge)
        for bridge in developer_bridges(
            graph,
            reference_now=reference,
            window_days=window_days,
            limit=limit,
        )
    ]

    return {
        "reference_time": reference.isoformat(),
        "freshness": await freshness(session),
        "window_days": window_days,
        "tracked_repositories": len(tracked),
        "repositories_with_momentum": len(ranked),
        "repositories": repo_rows,
        "topics": topic_rows,
        "developers": {
            "tracked": len(reports),
            "emerging_count": len(emerging),
            "emerging": emerging_rows,
        },
        "ecosystem": {
            **graph_counts(graph),
            "edges": {
                "owns": ecosystem_report.owns_edges,
                "contributes_to": ecosystem_report.contributes_edges,
                "tagged_with": ecosystem_report.tagged_edges,
            },
        },
        "bridges": bridge_rows,
    }


__all__ = ["router"]