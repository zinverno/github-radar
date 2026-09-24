"""Aggregate graph reports (Phase 4).

Every report is a frozen dataclass assembled by a pure builder from an
:class:`EcosystemGraph` plus an explicit reference instant. Builders reuse the
relationship, bridge, reach, centrality and component primitives, so a report
is nothing more than a deterministic projection of the underlying graph — it
adds no analytic content of its own.

Missing nodes are reported as zero-reach / empty result rows instead of
raising, which lets the CLI distinguish "not tracked" from "tracked but
undiscovered" without exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from github_radar.graph.bridges import (
    CrossTopicBridge,
    DeveloperBridge,
    RepositoryBridge,
    compute_developer_bridge,
    compute_repository_bridge,
    cross_topic_bridges,
    developer_bridges,
)
from github_radar.graph.metrics import (
    ConnectedComponent,
    DeveloperReach,
    NodeCentrality,
    RepositoryReach,
    TopicReach,
    connected_components,
    developer_centrality,
    developer_centrality_of,
    developer_reach,
    repository_centrality,
    repository_centrality_of,
    repository_reach,
    topic_centrality,
    topic_centrality_of,
    topic_reach,
)
from github_radar.graph.model import EcosystemGraph
from github_radar.graph.relationships import (
    CoContributorRelationship,
    RepositoryRelationship,
    TopicRelationship,
    find_co_contributors,
    related_repositories,
    related_topics,
)

DEFAULT_CENTRALITY_LIMIT = 8
DEFAULT_RELATIONSHIP_LIMIT = 5


@dataclass(frozen=True)
class EcosystemGraphReport:
    """The whole tracked ecosystem in one digestible report."""

    developer_count: int
    repository_count: int
    topic_count: int
    owns_edges: int
    contributes_edges: int
    tagged_edges: int
    component_count: int
    largest_component: ConnectedComponent | None
    top_developer_centrality: tuple[NodeCentrality, ...] = ()
    top_repository_centrality: tuple[NodeCentrality, ...] = ()
    top_topic_centrality: tuple[NodeCentrality, ...] = ()
    top_developer_bridges: tuple[DeveloperBridge, ...] = ()

    @property
    def node_counts(self) -> dict[str, int]:
        return {
            "developers": self.developer_count,
            "repositories": self.repository_count,
            "topics": self.topic_count,
        }

    @property
    def edge_counts(self) -> dict[str, int]:
        return {
            "owns": self.owns_edges,
            "contributes_to": self.contributes_edges,
            "tagged_with": self.tagged_edges,
        }


@dataclass(frozen=True)
class DeveloperGraphReport:
    """A developer's footprint in the ecosystem graph."""

    developer_id: int
    login: str
    reach: DeveloperReach
    centrality: NodeCentrality | None
    co_contributors: tuple[CoContributorRelationship, ...] = ()
    bridge: DeveloperBridge | None = None


@dataclass(frozen=True)
class RepositoryGraphReport:
    """A repository's footprint in the ecosystem graph."""

    repository_id: int
    full_name: str
    reach: RepositoryReach
    centrality: NodeCentrality | None
    related_repositories: tuple[RepositoryRelationship, ...] = ()
    bridge: RepositoryBridge | None = None


@dataclass(frozen=True)
class TopicGraphReport:
    """A topic's footprint in the ecosystem graph."""

    topic_id: int
    name: str
    reach: TopicReach
    centrality: NodeCentrality | None
    related_topics: tuple[TopicRelationship, ...] = ()


@dataclass(frozen=True)
class CrossTopicBridgeReport:
    """The bridge ecosystem between two topics."""

    topic_a: str
    topic_b: str
    bridge_count: int
    distinct_bridging_developers: int
    average_bridge_score: float
    shared_tracked_repositories: tuple[str, ...] = ()
    top_bridges: tuple[CrossTopicBridge, ...] = ()


def build_ecosystem_report(
    graph: EcosystemGraph,
    *,
    reference_now: datetime,
    window_days: int,
    centrality_limit: int = DEFAULT_CENTRALITY_LIMIT,
    bridge_limit: int = 5,
) -> EcosystemGraphReport:
    """Aggregate the whole graph: sizes, edges, components, central hubs."""
    components = connected_components(graph)

    edge_counts = {"OWNS": 0, "CONTRIBUTES_TO": 0, "TAGGED_WITH": 0}
    for edge in graph.edges():
        edge_counts[edge.edge_type] = edge_counts.get(edge.edge_type, 0) + 1

    centrality_limit = max(0, centrality_limit)
    return EcosystemGraphReport(
        developer_count=len(graph.developer_ids),
        repository_count=len(graph.repository_ids),
        topic_count=len(graph.topic_ids),
        owns_edges=edge_counts["OWNS"],
        contributes_edges=edge_counts["CONTRIBUTES_TO"],
        tagged_edges=edge_counts["TAGGED_WITH"],
        component_count=len(components),
        largest_component=components[0] if components else None,
        top_developer_centrality=developer_centrality(graph)[:centrality_limit],
        top_repository_centrality=repository_centrality(graph)[:centrality_limit],
        top_topic_centrality=topic_centrality(graph)[:centrality_limit],
        top_developer_bridges=developer_bridges(
            graph,
            reference_now=reference_now,
            window_days=window_days,
            limit=max(0, bridge_limit),
        ),
    )


def build_developer_report(
    graph: EcosystemGraph,
    developer_id: int,
    *,
    reference_now: datetime,
    window_days: int,
    co_contributor_limit: int = DEFAULT_RELATIONSHIP_LIMIT,
) -> DeveloperGraphReport:
    """A developer's reach, centrality, co-contributors and bridge score."""
    node = graph.developer(developer_id)
    if node is None:
        return DeveloperGraphReport(
            developer_id=developer_id,
            login=str(developer_id),
            reach=developer_reach(graph, developer_id),
            centrality=None,
        )

    bridge = compute_developer_bridge(
        graph,
        developer_id,
        reference_now=reference_now,
        window_days=window_days,
    )
    return DeveloperGraphReport(
        developer_id=developer_id,
        login=node.login,
        reach=developer_reach(graph, developer_id),
        centrality=developer_centrality_of(graph, developer_id),
        co_contributors=find_co_contributors(
            graph,
            developer_id,
            reference_now=reference_now,
            window_days=window_days,
            limit=max(0, co_contributor_limit),
        ),
        bridge=bridge,
    )


def build_repository_report(
    graph: EcosystemGraph,
    repository_id: int,
    *,
    reference_now: datetime,
    window_days: int,
    related_limit: int = DEFAULT_RELATIONSHIP_LIMIT,
) -> RepositoryGraphReport:
    """A repository's reach, centrality, related repositories and bridge score."""
    node = graph.repository(repository_id)
    if node is None:
        return RepositoryGraphReport(
            repository_id=repository_id,
            full_name=f"repo-{repository_id}",
            reach=repository_reach(graph, repository_id),
            centrality=None,
        )

    bridge = compute_repository_bridge(
        graph,
        repository_id,
        reference_now=reference_now,
        window_days=window_days,
    )
    return RepositoryGraphReport(
        repository_id=repository_id,
        full_name=node.full_name,
        reach=repository_reach(graph, repository_id),
        centrality=repository_centrality_of(graph, repository_id),
        related_repositories=related_repositories(
            graph,
            repository_id,
            reference_now=reference_now,
            window_days=window_days,
            limit=max(0, related_limit),
        ),
        bridge=bridge,
    )


def build_topic_report(
    graph: EcosystemGraph,
    topic_id: int,
    *,
    related_limit: int = DEFAULT_RELATIONSHIP_LIMIT,
) -> TopicGraphReport:
    """A topic's reach, centrality and related topics."""
    node = graph.topic(topic_id)
    if node is None:
        return TopicGraphReport(
            topic_id=topic_id,
            name=f"topic-{topic_id}",
            reach=topic_reach(graph, topic_id),
            centrality=None,
        )

    return TopicGraphReport(
        topic_id=topic_id,
        name=node.name,
        reach=topic_reach(graph, topic_id),
        centrality=topic_centrality_of(graph, topic_id),
        related_topics=related_topics(
            graph,
            topic_id,
            limit=max(0, related_limit),
        ),
    )


def build_cross_topic_bridge_report(
    graph: EcosystemGraph,
    topic_a: str,
    topic_b: str,
    *,
    reference_now: datetime,
    window_days: int,
    limit: int = 10,
) -> CrossTopicBridgeReport:
    """The bridge ecosystem between two topics (aggregate + top bridges)."""
    bridges = cross_topic_bridges(
        graph,
        topic_a,
        topic_b,
        reference_now=reference_now,
        window_days=window_days,
        limit=None,
    )

    developers = {bridge.developer_id for bridge in bridges}
    shared_repos: set[str] = set()
    for bridge in bridges:
        shared_repos.update(bridge.shared_tracked_repositories)

    average = (
        sum(bridge.bridge_score for bridge in bridges) / len(bridges)
        if bridges
        else 0.0
    )
    return CrossTopicBridgeReport(
        topic_a=topic_a,
        topic_b=topic_b,
        bridge_count=len(bridges),
        distinct_bridging_developers=len(developers),
        average_bridge_score=average,
        shared_tracked_repositories=tuple(sorted(shared_repos)),
        top_bridges=tuple(bridges[: max(0, limit)]),
    )


__all__ = [
    "CrossTopicBridgeReport",
    "DEFAULT_CENTRALITY_LIMIT",
    "DEFAULT_RELATIONSHIP_LIMIT",
    "DeveloperGraphReport",
    "EcosystemGraphReport",
    "RepositoryGraphReport",
    "TopicGraphReport",
    "build_cross_topic_bridge_report",
    "build_developer_report",
    "build_ecosystem_report",
    "build_repository_report",
    "build_topic_report",
]