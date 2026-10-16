"""Load quality evidence with batched reads; never collect or alter stored data."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.analytics.intelligence import RepoContext
from github_radar.analytics.metrics import compute_metrics
from github_radar.analytics.quality import (
    DeveloperEvidence,
    Provenance,
    RepositoryEvidence,
    developer_quality,
    repository_quality,
)
from github_radar.analytics.trends import classify_trend
from github_radar.discovery.planning import DiscoveryConfig
from github_radar.domain import DeveloperSnapshot
from github_radar.graph.loader import load_graph
from github_radar.services.collection_diagnostics import collection_stats
from github_radar.storage import repositories as storage
from github_radar.storage.models import CollectionRunRow, DeveloperRow, DiscoveryProvenanceRow


async def load_collection_stats(session: AsyncSession) -> dict[str, Any]:
    receipts = (await session.scalars(select(CollectionRunRow))).all()
    return collection_stats(receipts)


async def load_quality_report(
    session: AsyncSession,
    *,
    catalog: DiscoveryConfig,
    assessed_at: datetime,
    stale_after_hours: int = 24,
    top: int = 10,
) -> dict[str, Any]:
    rows = await storage.list_repos_with_latest(session)
    ids = [repo.id for repo, _ in rows]
    snapshots = await storage.snapshots_by_repository_ids(session, ids)
    topics = await storage.topics_by_repository_ids(session, ids)
    provenance: dict[int, list[Provenance]] = defaultdict(list)
    for p in await session.scalars(select(DiscoveryProvenanceRow)):
        provenance[p.repository_id].append(Provenance(p.category, p.recipe, p.query, p.last_run_id))
    evidence = [
        RepositoryEvidence(
            repo.id,
            repo.github_id,
            repo.full_name,
            repo.primary_language,
            repo.created_at,
            repo.first_seen_at,
            repo.is_archived,
            repo.is_fork,
            tuple(s.to_domain() for s in snapshots.get(repo.id, ())),
            tuple(t.name for t in topics.get(repo.id, ())),
            tuple(provenance[repo.id]),
        )
        for repo, _ in rows
    ]
    coverage = repository_quality(
        evidence,
        catalog={c.name: c.query for c in catalog.categories},
        assessed_at=assessed_at,
        stale_after_hours=stale_after_hours,
        top=top,
    )
    # Match the existing developer/graph anchor, and expose its known precedence explicitly.
    anchor = await storage.newest_observation_at(session)
    if anchor is not None and anchor > assessed_at:
        raise ValueError("Assessment time precedes stored contributor observations")
    reference = anchor or assessed_at
    graph = await load_graph(session, reference_now=reference)
    contexts = {}
    for repo in evidence:
        node = graph.repository(repo.repository_id)
        assert node is not None
        metrics = compute_metrics(repo.snapshots)
        contexts[repo.repository_id] = RepoContext(
            repo.repository_id,
            repo.full_name,
            repo.language,
            node.owner_id,
            frozenset(repo.topics),
            node.momentum,
            classify_trend(metrics, node.momentum, reference_now=reference),
            metrics.latest_counts.get("stars"),
        )
    profiles: dict[int, list[DeveloperSnapshot]] = defaultdict(list)
    for row in await storage.all_developer_snapshots(session):
        if row.captured_at > assessed_at:
            raise ValueError("Assessment time precedes stored developer observations")
        profiles[row.developer_id].append(row.to_domain())
    developers = [
        DeveloperEvidence(
            d.id,
            d.github_id,
            d.login,
            d.first_seen_at,
            d.followers,
            tuple(profiles[d.id]),
        )
        for d in await session.scalars(select(DeveloperRow))
    ]
    newest_repo = max((s.captured_at for repo in evidence for s in repo.snapshots), default=None)
    return {
        "schema_version": 1,
        "mode": "read-only",
        "assessed_at": assessed_at.isoformat(),
        "stale_after_hours": stale_after_hours,
        "time_semantics": "Repository scores use each latest observation. Assessment time "
        "only measures age/staleness; this is not a historical database query.",
        "developer_reference_at": anchor.isoformat() if anchor else None,
        "developer_anchor_precedes_repository_observation": bool(
            anchor and newest_repo and anchor < newest_repo
        ),
        "confidence_semantics": "Deterministic data coverage, not statistical probability.",
        "coverage": coverage,
        "developers": developer_quality(
            developers, graph=graph, repos=contexts, reference_now=reference, top=top
        ),
        "collection": await load_collection_stats(session),
    }
