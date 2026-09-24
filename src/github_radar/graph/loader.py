"""Build an :class:`EcosystemGraph` from the relational source of truth.

The graph is derived from PostgreSQL (developers, repositories, contributor
links, contributor snapshots, repository topics). It is *not* a separate
persistent store: every ``github-radar graph`` command re-derives it fresh, so
it can never drift from the tracked dataset.

``reference_now`` is anchored to the newest observed ``captured_at`` — a real
datetime taken from the observation history, never the wall clock — so repo
momentum on the graph nodes is reproducible between runs.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.analytics import compute_metrics, compute_momentum
from github_radar.domain import ContributorSnapshot
from github_radar.graph.model import (
    DeveloperNode,
    EcosystemGraph,
    RepositoryNode,
    TopicNode,
)
from github_radar.storage import repositories as storage
from github_radar.util import utcnow


async def load_graph(
    session: AsyncSession,
    *,
    reference_now: datetime | None = None,
) -> EcosystemGraph:
    """Load every developer, repository, topic and contributor link from storage.

    Ownership edges come from ``repositories.owner_id``; repository momentum is
    computed from the repository snapshot history anchored at ``reference_now``
    (defaulting to the newest observation anywhere in the dataset).
    """
    anchor = reference_now or await storage.newest_observation_at(session)
    reference_now = anchor or utcnow()

    graph = EcosystemGraph()

    rows = await storage.list_repos_with_latest(session)
    topic_rows = await storage.topics_by_repository_ids(
        session, [repo.id for repo, _latest in rows]
    )
    snapshots_by_repo = await storage.snapshots_by_repository_ids(
        session, [repo.id for repo, _latest in rows]
    )

    for repo, _latest in rows:
        snapshots = snapshots_by_repo.get(repo.id, [])
        metrics = compute_metrics([s.to_domain() for s in snapshots])
        momentum = (
            compute_momentum(metrics, reference_now=reference_now)
            if metrics.latest_captured_at is not None
            else None
        )
        graph.add_repository(
            RepositoryNode(
                repository_id=repo.id,
                full_name=repo.full_name,
                primary_language=repo.primary_language,
                momentum=momentum,
                owner_id=repo.owner_id,
            )
        )
        if repo.owner_id is not None:
            graph.add_ownership(repo.owner_id, repo.id)
        for topic in topic_rows.get(repo.id, ()):
            graph.add_topic(TopicNode(topic_id=topic.id, name=topic.name))
            graph.add_repo_topic(repo.id, topic.id)

    links = await storage.list_contributor_links(session)
    if links:
        snap_rows = await storage.all_contributor_snapshots(session)
        by_relationship: dict[tuple[int, int], list[ContributorSnapshot]] = {}
        for snap_row in snap_rows:
            snap = snap_row.to_domain()
            by_relationship.setdefault(
                (snap.developer_id, snap.repository_id), []
            ).append(snap)

        totals_by_repo: dict[int, int] = {}
        for record in links:
            totals_by_repo[record.repository_id] = (
                totals_by_repo.get(record.repository_id, 0) + record.contributions
            )

        contributor_ids = {record.developer_id for record in links}
        owner_ids: set[int] = set()
        for repository_id in graph.repository_ids:
            owner_id = graph.owner_of(repository_id)
            if owner_id is not None:
                owner_ids.add(owner_id)
        developer_rows = await storage.get_developers_by_id(
            session, sorted(contributor_ids | owner_ids)
        )

        for record in links:
            total = totals_by_repo.get(record.repository_id, 0)
            share = record.contributions / total if total > 0 else 0.0
            observations = by_relationship.get(
                (record.developer_id, record.repository_id), []
            )
            graph.add_contribution(
                record.developer_id,
                record.repository_id,
                contributions=record.contributions,
                share=share,
                observations=tuple(
                    sorted(observations, key=lambda obs: obs.captured_at)
                ),
            )

        for row in developer_rows:
            graph.add_developer(
                DeveloperNode(
                    developer_id=row.id,
                    login=row.login,
                    followers=row.followers,
                )
            )

    return graph


__all__ = ["load_graph"]