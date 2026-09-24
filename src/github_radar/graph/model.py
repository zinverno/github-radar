"""In-memory ecosystem graph model (Phase 4).

This module defines the graph representation used by Phase 4 analytics. It is
*derived* from the relational source of truth (PostgreSQL) at load time — it is
not a separate persistent graph store, and no graph database is involved. The
model stays independent from the CLI: reports are assembled from it by the
application layer (:mod:`github_radar.graph.reports`) and rendered by the CLI.

The graph is deliberately a **co-contribution** model, not a social graph.
Two developers are connected only when they both contributed to at least one
tracked repository in common ("these developers contributed to at least one
tracked repository in common" — nothing more is implied).

Node types
==========

* ``DEVELOPER`` — a tracked developer.
* ``REPOSITORY`` — a tracked repository.
* ``TOPIC`` — a tracked topic name.

Direct evidence edges
=====================

* ``OWNS`` — Developer → Repository (the repository's ``owner_id`` relationship).
* ``CONTRIBUTES_TO`` — Developer → Repository, carrying cumulative
  contributions, per-repository share, and the raw contributor observation
  history (from which windowed recent activity is derived).
* ``TAGGED_WITH`` — Repository → Topic.

Duplicate relational rows never produce duplicate graph edges: every edge
family is keyed on its natural identifiers, so re-adding the same relationship
collapses into the existing edge.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from github_radar.analytics.momentum import MomentumScore
from github_radar.domain import ContributorSnapshot

NodeType = Literal["DEVELOPER", "REPOSITORY", "TOPIC"]
EdgeType = Literal["OWNS", "CONTRIBUTES_TO", "TAGGED_WITH"]

# A node key is ``(node_type, node_id)``.
NodeKey = tuple[NodeType, int]


@dataclass(frozen=True)
class GraphNode:
    """A node in the ecosystem graph (typed key + display label)."""

    node_type: NodeType
    node_id: int
    label: str

    @property
    def key(self) -> NodeKey:
        return (self.node_type, self.node_id)


@dataclass(frozen=True)
class DeveloperNode:
    """A tracked developer node with display metadata."""

    developer_id: int
    login: str
    followers: int | None = None

    @property
    def node(self) -> GraphNode:
        return GraphNode("DEVELOPER", self.developer_id, self.login)


@dataclass(frozen=True)
class RepositoryNode:
    """A tracked repository node with its per-repository signals."""

    repository_id: int
    full_name: str
    primary_language: str | None = None
    momentum: MomentumScore | None = None
    owner_id: int | None = None

    @property
    def node(self) -> GraphNode:
        return GraphNode("REPOSITORY", self.repository_id, self.full_name)


@dataclass(frozen=True)
class TopicNode:
    """A tracked topic node."""

    topic_id: int
    name: str

    @property
    def node(self) -> GraphNode:
        return GraphNode("TOPIC", self.topic_id, self.name)


@dataclass(frozen=True)
class ContributionEvidence:
    """Raw evidence for one Developer → Repository contributor link.

    ``contributions`` is GitHub's *cumulative* lifetime count, never recent
    activity. ``observations`` is the contributor snapshot history from which
    windowed recent deltas are derived (missing history stays missing).
    """

    contributions: int
    share: float
    observations: tuple[ContributorSnapshot, ...] = ()


@dataclass(frozen=True)
class GraphEdgeEvidence:
    """Evidence bundled with one direct graph edge."""

    edge_type: EdgeType
    # CONTRIBUTES_TO
    contributions: int | None = None
    share: float | None = None
    observations: tuple[ContributorSnapshot, ...] = ()


@dataclass(frozen=True)
class GraphEdge:
    """A direct, evidence-backed graph edge."""

    edge_type: EdgeType
    source: NodeKey
    target: NodeKey
    evidence: GraphEdgeEvidence | None = None


class EcosystemGraph:
    """The in-memory derived ecosystem graph.

    Construction is incremental (``add_*`` methods collapse duplicates) and
    read access is typed, so analytics never touch ORM objects. Filtering
    produces a new sub-graph sharing the same evidence objects.
    """

    def __init__(self) -> None:
        self._developers: dict[int, DeveloperNode] = {}
        self._repositories: dict[int, RepositoryNode] = {}
        self._topics: dict[int, TopicNode] = {}

        self._dev_repos: dict[int, set[int]] = {}
        self._repo_devs: dict[int, set[int]] = {}
        self._ownership: dict[int, set[int]] = {}
        self._repo_topics: dict[int, set[int]] = {}
        self._topic_repos: dict[int, set[int]] = {}

        self._contribution_evidence: dict[tuple[int, int], ContributionEvidence] = {}
        self._owner_of: dict[int, int | None] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_developer(self, node: DeveloperNode) -> None:
        self._developers[node.developer_id] = node

    def add_repository(self, node: RepositoryNode) -> None:
        self._repositories[node.repository_id] = node
        previous = self._owner_of.get(node.repository_id)
        if previous != node.owner_id:
            self._owner_of[node.repository_id] = node.owner_id
            if previous is not None:
                self._ownership.get(previous, set()).discard(node.repository_id)
            if node.owner_id is not None:
                self._ownership.setdefault(node.owner_id, set()).add(
                    node.repository_id
                )

    def add_topic(self, node: TopicNode) -> None:
        self._topics[node.topic_id] = node

    def add_ownership(self, developer_id: int, repository_id: int) -> None:
        """Add an OWNS edge (idempotent for the same developer/repository pair)."""
        self._ownership.setdefault(developer_id, set()).add(repository_id)

    def add_contribution(
        self,
        developer_id: int,
        repository_id: int,
        *,
        contributions: int,
        share: float,
        observations: Sequence[ContributorSnapshot] = (),
    ) -> None:
        """Add a CONTRIBUTES_TO edge with evidence (idempotent per pair).

        Re-adding the same ``(developer_id, repository_id)`` relationship does
        not create a second edge: the evidence is replaced (newest wins), which
        keeps the graph free of duplicates even when the relational source is
        re-read.
        """
        self._dev_repos.setdefault(developer_id, set()).add(repository_id)
        self._repo_devs.setdefault(repository_id, set()).add(developer_id)
        self._contribution_evidence[(developer_id, repository_id)] = (
            ContributionEvidence(
                contributions=contributions,
                share=share,
                observations=tuple(
                    sorted(observations, key=lambda obs: obs.captured_at)
                ),
            )
        )

    def add_repo_topic(self, repository_id: int, topic_id: int) -> None:
        """Add a TAGGED_WITH edge (idempotent per repository/topic pair)."""
        self._repo_topics.setdefault(repository_id, set()).add(topic_id)
        self._topic_repos.setdefault(topic_id, set()).add(repository_id)

    # ------------------------------------------------------------------
    # Node access
    # ------------------------------------------------------------------

    @property
    def developer_nodes(self) -> Mapping[int, DeveloperNode]:
        return self._developers

    @property
    def repository_nodes(self) -> Mapping[int, RepositoryNode]:
        return self._repositories

    @property
    def topic_nodes(self) -> Mapping[int, TopicNode]:
        return self._topics

    def developer(self, developer_id: int) -> DeveloperNode | None:
        return self._developers.get(developer_id)

    def repository(self, repository_id: int) -> RepositoryNode | None:
        return self._repositories.get(repository_id)

    def topic(self, topic_id: int) -> TopicNode | None:
        return self._topics.get(topic_id)

    def developer_by_login(self, login: str) -> DeveloperNode | None:
        wanted = login.lower()
        for node in self._developers.values():
            if node.login.lower() == wanted:
                return node
        return None

    def repository_by_full_name(self, full_name: str) -> RepositoryNode | None:
        wanted = full_name.lower()
        for node in self._repositories.values():
            if node.full_name.lower() == wanted:
                return node
        return None

    def topic_by_name(self, name: str) -> TopicNode | None:
        wanted = name.lower()
        for node in self._topics.values():
            if node.name.lower() == wanted:
                return node
        return None

    @property
    def topic_names(self) -> frozenset[str]:
        return frozenset(node.name for node in self._topics.values())

    @property
    def developer_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self._developers))

    @property
    def repository_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self._repositories))

    @property
    def topic_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self._topics))

    def nodes(self) -> list[GraphNode]:
        """All nodes, deterministically ordered (type, then id)."""
        nodes = [
            developer.node for developer in self._developers.values()
        ]
        nodes += [repository.node for repository in self._repositories.values()]
        nodes += [topic.node for topic in self._topics.values()]
        order = {"DEVELOPER": 0, "REPOSITORY": 1, "TOPIC": 2}
        return sorted(nodes, key=lambda node: (order[node.node_type], node.node_id))

    # ------------------------------------------------------------------
    # Edge access
    # ------------------------------------------------------------------

    def contribution_evidence(
        self, developer_id: int, repository_id: int
    ) -> ContributionEvidence | None:
        return self._contribution_evidence.get((developer_id, repository_id))

    def repositories_for_developer(
        self, developer_id: int
    ) -> frozenset[int]:
        """Repositories the developer CONTRIBUTES_TO."""
        return frozenset(self._dev_repos.get(developer_id, ()))

    def developers_for_repository(
        self, repository_id: int
    ) -> frozenset[int]:
        """Developers that CONTRIBUTE_TO the repository."""
        return frozenset(self._repo_devs.get(repository_id, ()))

    def repositories_owned_by(
        self, developer_id: int
    ) -> frozenset[int]:
        """Repositories the developer OWNS."""
        return frozenset(self._ownership.get(developer_id, ()))

    def owner_of(self, repository_id: int) -> int | None:
        return self._owner_of.get(repository_id)

    def topics_for_repository(self, repository_id: int) -> frozenset[int]:
        return frozenset(self._repo_topics.get(repository_id, ()))

    def repositories_for_topic(self, topic_id: int) -> frozenset[int]:
        return frozenset(self._topic_repos.get(topic_id, ()))

    def associated_repositories(
        self, developer_id: int
    ) -> frozenset[int]:
        """Repositories the developer contributes to or owns."""
        return (
            self.repositories_for_developer(developer_id)
            | self.repositories_owned_by(developer_id)
        )

    def associated_developers(self, repository_id: int) -> frozenset[int]:
        """Developers that contribute to or own the repository."""
        developers = set(self.developers_for_repository(repository_id))
        owner = self.owner_of(repository_id)
        if owner is not None:
            developers.add(owner)
        return frozenset(developers)

    def edges(self) -> list[GraphEdge]:
        """All direct evidence edges, deterministically ordered."""
        edges: list[GraphEdge] = []
        for developer_id in self.developer_ids:
            for repository_id in sorted(
                self.repositories_owned_by(developer_id)
            ):
                edges.append(
                    GraphEdge(
                        edge_type="OWNS",
                        source=("DEVELOPER", developer_id),
                        target=("REPOSITORY", repository_id),
                    )
                )
        for developer_id in self.developer_ids:
            for repository_id in sorted(
                self.repositories_for_developer(developer_id)
            ):
                evidence = self.contribution_evidence(
                    developer_id, repository_id
                )
                edges.append(
                    GraphEdge(
                        edge_type="CONTRIBUTES_TO",
                        source=("DEVELOPER", developer_id),
                        target=("REPOSITORY", repository_id),
                        evidence=(
                            GraphEdgeEvidence(
                                edge_type="CONTRIBUTES_TO",
                                contributions=(
                                    evidence.contributions
                                    if evidence is not None
                                    else None
                                ),
                                share=evidence.share if evidence is not None else None,
                                observations=(
                                    evidence.observations
                                    if evidence is not None
                                    else ()
                                ),
                            )
                        ),
                    )
                )
        for repository_id in self.repository_ids:
            for topic_id in sorted(self.topics_for_repository(repository_id)):
                edges.append(
                    GraphEdge(
                        edge_type="TAGGED_WITH",
                        source=("REPOSITORY", repository_id),
                        target=("TOPIC", topic_id),
                    )
                )
        return edges

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def filter(
        self,
        *,
        topics: Iterable[str] | None = None,
        language: str | None = None,
        repositories: Iterable[str] | None = None,
        developers: Iterable[int] | None = None,
        min_momentum: float | None = None,
    ) -> EcosystemGraph:
        """Return a new sub-graph restricted by the given filters.

        * ``topics`` — keep repositories tagged with any of these topic names
          (all of the kept repositories' topics are retained, so related-topic
          analytics stay meaningful inside the sub-graph; the topic filter
          therefore chooses *repositories*, not topic nodes).
        * ``language`` — keep repositories with this primary language.
        * ``repositories`` — keep only these repositories (full names).
        * ``developers`` — keep only these developer ids.
        * ``min_momentum`` — drop repositories whose momentum is unavailable or
          below this value.

        Evidence objects are shared with the parent graph (cheap, immutable).
        """
        wanted_topics: set[str] | None = None
        if topics is not None:
            wanted_topics = {name.strip().lower() for name in topics}

        wanted_repos: set[str] | None = None
        if repositories is not None:
            wanted_repos = {name.strip().lower() for name in repositories}
        wanted_devs: set[int] | None = (
            set(developers) if developers is not None else None
        )

        kept_repos: set[int] = set()
        for repository_id, node in self._repositories.items():
            if wanted_topics is not None:
                topic_names_here = {
                    self._topics[tid].name.lower()
                    for tid in self.topics_for_repository(repository_id)
                    if tid in self._topics
                }
                if not (topic_names_here & wanted_topics):
                    continue
            if wanted_repos is not None and node.full_name.lower() not in wanted_repos:
                continue
            if min_momentum is not None:
                momentum = node.momentum
                if momentum is None or momentum.score < min_momentum:
                    continue
            kept_repos.add(repository_id)

        filtered = EcosystemGraph()
        for repository_id in sorted(kept_repos):
            node = self._repositories[repository_id]
            filtered.add_repository(node)
            for topic_id in sorted(self.topics_for_repository(repository_id)):
                topic = self._topics.get(topic_id)
                if topic is None:
                    continue
                filtered.add_topic(topic)
                filtered.add_repo_topic(repository_id, topic_id)
            for developer_id in sorted(self.associated_developers(repository_id)):
                if wanted_devs is not None and developer_id not in wanted_devs:
                    continue
                developer = self._developers.get(developer_id)
                if developer is None:
                    continue
                filtered.add_developer(developer)
                if self.owner_of(repository_id) == developer_id:
                    filtered.add_ownership(developer_id, repository_id)
                evidence = self.contribution_evidence(
                    developer_id, repository_id
                )
                if evidence is not None:
                    filtered.add_contribution(
                        developer_id,
                        repository_id,
                        contributions=evidence.contributions,
                        share=evidence.share,
                        observations=evidence.observations,
                    )

        return filtered


__all__ = [
    "ContributionEvidence",
    "DeveloperNode",
    "EcosystemGraph",
    "GraphEdge",
    "GraphEdgeEvidence",
    "GraphNode",
    "NodeKey",
    "NodeType",
    "RepositoryNode",
    "TopicNode",
]