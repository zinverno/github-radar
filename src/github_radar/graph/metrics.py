"""Deterministic graph metrics over the ecosystem graph.

Everything here is a pure function of an :class:`EcosystemGraph` — no storage,
no wall clock. The outputs are the structural metrics of Phase 4:

* **Reach** — how many nodes of each type a node touches through its direct
  evidence edges (repositories, developers, topics).
* **Centrality** — degree / weighted-degree centrality computed per node type.
  Neighbourhoods are homogeneous (developer ↔ developer only, repository ↔
  repository only, topic ↔ topic only) so degrees are comparable within a type.
  Developer neighbourhood = shared **associated** repositories (contributed or
  owned). Repository neighbourhood = a shared associate (contributor or owner).
  Topic neighbourhood = shared tagged repositories. Weights are counts of the
  shared evidence (shared repositories, shared associates, co-occurring
  repositories), which keeps every centrality deterministic.
* **Connected components** — components of the full undirected graph (all
  direct edges: ``OWNS``, ``CONTRIBUTES_TO``, ``TAGGED_WITH``), deterministically
  ordered so identical graphs produce identical component lists.

Reach and connectivity deliberately describe *structure* (what is connected to
what, how broadly); they never imply a social claim. A connected component is
just "these nodes are transitively reachable through tracked repositories and
topics".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from github_radar.graph.model import EcosystemGraph, NodeKey, NodeType

# Node-type ordering shared with ``EcosystemGraph.nodes()``; used everywhere a
# deterministic tie-break needs a stable type order.
NODE_TYPE_ORDER: dict[str, int] = {"DEVELOPER": 0, "REPOSITORY": 1, "TOPIC": 2}


@dataclass(frozen=True)
class DeveloperReach:
    """Structural reach of one developer node in the ecosystem graph."""

    developer_id: int
    login: str
    contributed_repositories: int
    owned_repositories: int
    reached_repositories: int
    reached_developers: int
    reached_topics: int

    @property
    def node_key(self) -> NodeKey:
        return ("DEVELOPER", self.developer_id)


@dataclass(frozen=True)
class RepositoryReach:
    """Structural reach of one repository node."""

    repository_id: int
    full_name: str
    contributing_developers: int
    owning_developers: int
    topics: int
    sibling_repositories: int

    @property
    def node_key(self) -> NodeKey:
        return ("REPOSITORY", self.repository_id)


@dataclass(frozen=True)
class TopicReach:
    """Structural reach of one topic node."""

    topic_id: int
    name: str
    repositories: int
    contributing_developers: int
    owning_developers: int
    associated_developers: int

    @property
    def node_key(self) -> NodeKey:
        return ("TOPIC", self.topic_id)


@dataclass(frozen=True)
class NodeCentrality:
    """Degree and weighted-degree centrality for one node.

    ``degree`` is the number of distinct adjacent nodes of the same type;
    ``weighted_degree`` is the sum of shared-evidence counts across those
    neighbours. Both are deterministic and bounded only by the graph size.
    """

    node_type: NodeType
    node_id: int
    label: str
    degree: int
    weighted_degree: float


@dataclass(frozen=True)
class ConnectedComponent:
    """One connected component of the undirected ecosystem graph."""

    node_count: int
    developer_count: int
    repository_count: int
    topic_count: int
    developer_logins: tuple[str, ...] = ()
    repository_full_names: tuple[str, ...] = ()
    topic_names: tuple[str, ...] = ()
    # Deterministic tie-break key (type order, id) of the smallest node.
    canonical_node: NodeKey = ("DEVELOPER", 0)


def developer_reach(graph: EcosystemGraph, developer_id: int) -> DeveloperReach:
    """Reach of one developer: repos, peers, and topics reachable directly.

    Missing nodes report zero reach rather than raising, so callers can render
    an honest "not in the graph" result.
    """
    node = graph.developer(developer_id)
    if node is None:
        return DeveloperReach(
            developer_id=developer_id,
            login=str(developer_id),
            contributed_repositories=0,
            owned_repositories=0,
            reached_repositories=0,
            reached_developers=0,
            reached_topics=0,
        )

    contributed = graph.repositories_for_developer(developer_id)
    owned = graph.repositories_owned_by(developer_id)
    associated = graph.associated_repositories(developer_id)

    reached_developers: set[int] = set()
    reached_topics: set[int] = set()
    for repository_id in associated:
        reached_developers.update(graph.associated_developers(repository_id))
        reached_topics.update(graph.topics_for_repository(repository_id))
    reached_developers.discard(developer_id)

    return DeveloperReach(
        developer_id=developer_id,
        login=node.login,
        contributed_repositories=len(contributed),
        owned_repositories=len(owned),
        reached_repositories=len(associated),
        reached_developers=len(reached_developers),
        reached_topics=len(reached_topics),
    )


def repository_reach(graph: EcosystemGraph, repository_id: int) -> RepositoryReach:
    """Reach of one repository: developers, topics, and sibling repositories."""
    node = graph.repository(repository_id)
    if node is None:
        return RepositoryReach(
            repository_id=repository_id,
            full_name=f"repo-{repository_id}",
            contributing_developers=0,
            owning_developers=0,
            topics=0,
            sibling_repositories=0,
        )

    owner = graph.owner_of(repository_id)
    associated = graph.associated_developers(repository_id)

    siblings: set[int] = set()
    for developer_id in associated:
        siblings.update(graph.repositories_for_developer(developer_id))
        siblings.update(graph.repositories_owned_by(developer_id))
    siblings.discard(repository_id)

    return RepositoryReach(
        repository_id=repository_id,
        full_name=node.full_name,
        contributing_developers=len(graph.developers_for_repository(repository_id)),
        owning_developers=1 if owner is not None else 0,
        topics=len(graph.topics_for_repository(repository_id)),
        sibling_repositories=len(siblings),
    )


def topic_reach(graph: EcosystemGraph, topic_id: int) -> TopicReach:
    """Reach of one topic: its repositories and their developers."""
    node = graph.topic(topic_id)
    repositories = graph.repositories_for_topic(topic_id)

    contributing: set[int] = set()
    owning: set[int] = set()
    for repository_id in repositories:
        contributing.update(graph.developers_for_repository(repository_id))
        owner = graph.owner_of(repository_id)
        if owner is not None:
            owning.add(owner)

    return TopicReach(
        topic_id=topic_id,
        name=node.name if node is not None else f"topic-{topic_id}",
        repositories=len(repositories),
        contributing_developers=len(contributing),
        owning_developers=len(owning),
        associated_developers=len(contributing | owning),
    )


def _developer_neighbours(
    graph: EcosystemGraph, developer_id: int
) -> dict[int, int]:
    """Neighbouring developers → number of shared associated repositories."""
    weights: dict[int, int] = {}
    for repository_id in graph.associated_repositories(developer_id):
        for other in graph.associated_developers(repository_id):
            if other != developer_id:
                weights[other] = weights.get(other, 0) + 1
    return weights


def _repository_sibling_weight(
    graph: EcosystemGraph, repository_a: int, repository_b: int
) -> int:
    """Shared-associate weight between two repositories.

    Counts distinct developers associated with both repositories, plus one when
    they share the same (non-null) owner.
    """
    shared = (
        graph.associated_developers(repository_a)
        & graph.associated_developers(repository_b)
    )
    weight = len(shared)
    owner_a = graph.owner_of(repository_a)
    owner_b = graph.owner_of(repository_b)
    if owner_a is not None and owner_a == owner_b:
        weight += 1
    return weight


def _repository_neighbours(
    graph: EcosystemGraph, repository_id: int
) -> dict[int, int]:
    """Neighbouring repositories → shared-associate weight."""
    weights: dict[int, int] = {}
    for developer_id in graph.associated_developers(repository_id):
        for other in graph.repositories_for_developer(developer_id):
            if other != repository_id:
                weights[other] = _repository_sibling_weight(
                    graph, repository_id, other
                )
        for other in graph.repositories_owned_by(developer_id):
            if other != repository_id:
                weights[other] = _repository_sibling_weight(
                    graph, repository_id, other
                )
    return weights


def _topic_neighbours(
    graph: EcosystemGraph, topic_id: int
) -> dict[int, int]:
    """Neighbouring topics → number of co-occurring repositories."""
    weights: dict[int, int] = {}
    for repository_id in graph.repositories_for_topic(topic_id):
        for other in graph.topics_for_repository(repository_id):
            if other != topic_id:
                weights[other] = weights.get(other, 0) + 1
    return weights


def _centrality(
    node_type: NodeType,
    entries: Sequence[tuple[int, str, dict[int, int]]],
) -> tuple[NodeCentrality, ...]:
    """Build centrality rows from ``(node_id, label, neighbour→weight)``."""
    results = [
        NodeCentrality(
            node_type=node_type,
            node_id=node_id,
            label=label,
            degree=len(weights),
            weighted_degree=float(sum(weights.values())),
        )
        for node_id, label, weights in entries
    ]
    results.sort(
        key=lambda item: (-item.weighted_degree, -item.degree, item.label)
    )
    return tuple(results)


def developer_centrality(
    graph: EcosystemGraph,
) -> tuple[NodeCentrality, ...]:
    """Degree / weighted-degree centrality for all developer nodes.

    Neighbours are developers sharing at least one associated repository;
    weight is the number of shared associated repositories. Sorted by
    ``(-weighted_degree, -degree, label)``.
    """
    return _centrality(
        "DEVELOPER",
        [
            (
                developer_id,
                node.login,
                _developer_neighbours(graph, developer_id),
            )
            for developer_id, node in sorted(graph.developer_nodes.items())
        ],
    )


def repository_centrality(
    graph: EcosystemGraph,
) -> tuple[NodeCentrality, ...]:
    """Degree / weighted-degree centrality for all repository nodes.

    Neighbours are repositories sharing an associate (contributor or owner);
    weight is the shared-associate count (plus one for a shared owner).
    """
    return _centrality(
        "REPOSITORY",
        [
            (
                repository_id,
                node.full_name,
                _repository_neighbours(graph, repository_id),
            )
            for repository_id, node in sorted(graph.repository_nodes.items())
        ],
    )


def topic_centrality(
    graph: EcosystemGraph,
) -> tuple[NodeCentrality, ...]:
    """Degree / weighted-degree centrality for all topic nodes.

    Neighbours are topics co-occurring on at least one repository; weight is
    the number of co-occurring repositories.
    """
    return _centrality(
        "TOPIC",
        [
            (
                topic_id,
                node.name,
                _topic_neighbours(graph, topic_id),
            )
            for topic_id, node in sorted(graph.topic_nodes.items())
        ],
    )


def developer_centrality_of(
    graph: EcosystemGraph, developer_id: int
) -> NodeCentrality | None:
    """Centrality row for one developer, or ``None`` when it is not in the graph."""
    node = graph.developer(developer_id)
    if node is None:
        return None
    return _centrality_of(
        "DEVELOPER",
        developer_id,
        node.login,
        _developer_neighbours(graph, developer_id),
    )


def repository_centrality_of(
    graph: EcosystemGraph, repository_id: int
) -> NodeCentrality | None:
    """Centrality row for one repository, or ``None`` when it is not in the graph."""
    node = graph.repository(repository_id)
    if node is None:
        return None
    return _centrality_of(
        "REPOSITORY",
        repository_id,
        node.full_name,
        _repository_neighbours(graph, repository_id),
    )


def topic_centrality_of(
    graph: EcosystemGraph, topic_id: int
) -> NodeCentrality | None:
    """Centrality row for one topic, or ``None`` when it is not in the graph."""
    node = graph.topic(topic_id)
    if node is None:
        return None
    return _centrality_of(
        "TOPIC",
        topic_id,
        node.name,
        _topic_neighbours(graph, topic_id),
    )


def _centrality_of(
    node_type: NodeType,
    node_id: int,
    label: str,
    weights: dict[int, int],
) -> NodeCentrality:
    return NodeCentrality(
        node_type=node_type,
        node_id=node_id,
        label=label,
        degree=len(weights),
        weighted_degree=float(sum(weights.values())),
    )


# ---------------------------------------------------------------------------
# Connected components
# ---------------------------------------------------------------------------


class _UnionFind:
    """Union-find over ``NodeKey`` values (path-compressed, union by rank-free)."""

    def __init__(self) -> None:
        self._parent: dict[NodeKey, NodeKey] = {}

    def _find(self, item: NodeKey) -> NodeKey:
        parent = self._parent.get(item)
        if parent is None or parent == item:
            self._parent[item] = item
            return item
        root = self._find(parent)
        self._parent[item] = root
        return root

    def union(self, a: NodeKey, b: NodeKey) -> None:
        root_a = self._find(a)
        root_b = self._find(b)
        if root_a != root_b:
            self._parent[root_b] = root_a

    def groups(self) -> dict[NodeKey, list[NodeKey]]:
        grouped: dict[NodeKey, list[NodeKey]] = {}
        for item in self._parent:
            grouped.setdefault(self._find(item), []).append(item)
        return grouped


def connected_components(graph: EcosystemGraph) -> tuple[ConnectedComponent, ...]:
    """All connected components under the direct evidence edges.

    Uses every ``OWNS``, ``CONTRIBUTES_TO`` and ``TAGGED_WITH`` edge as an
    undirected link. Components are deterministically ordered by
    ``(-node_count, canonical node)`` where the canonical node is the smallest
    ``(type-order, id)`` key inside the component — identical graphs therefore
    yield identical components.
    """
    uf = _UnionFind()
    for node in graph.nodes():
        uf.union(node.key, node.key)
    for edge in graph.edges():
        uf.union(edge.source, edge.target)

    components: list[ConnectedComponent] = []
    for members in uf.groups().values():
        key_set = set(members)
        developer_keys = sorted(
            (key for key in key_set if key[0] == "DEVELOPER"),
            key=lambda key: key[1],
        )
        repository_keys = sorted(
            (key for key in key_set if key[0] == "REPOSITORY"),
            key=lambda key: key[1],
        )
        topic_keys = sorted(
            (key for key in key_set if key[0] == "TOPIC"),
            key=lambda key: key[1],
        )

        developer_logins: list[str] = []
        for key in developer_keys:
            dev_node = graph.developer(key[1])
            if dev_node is not None:
                developer_logins.append(dev_node.login)

        repository_full_names: list[str] = []
        for key in repository_keys:
            repo_node = graph.repository(key[1])
            if repo_node is not None:
                repository_full_names.append(repo_node.full_name)

        topic_names: list[str] = []
        for key in topic_keys:
            topic_node = graph.topic(key[1])
            if topic_node is not None:
                topic_names.append(topic_node.name)

        canonical = min(
            key_set, key=lambda key: (NODE_TYPE_ORDER[key[0]], key[1])
        )
        components.append(
            ConnectedComponent(
                node_count=len(key_set),
                developer_count=len(developer_keys),
                repository_count=len(repository_keys),
                topic_count=len(topic_keys),
                developer_logins=tuple(developer_logins),
                repository_full_names=tuple(repository_full_names),
                topic_names=tuple(topic_names),
                canonical_node=canonical,
            )
        )

    components.sort(
        key=lambda item: (
            -item.node_count,
            NODE_TYPE_ORDER[item.canonical_node[0]],
            item.canonical_node[1],
        )
    )
    return tuple(components)


__all__ = [
    "ConnectedComponent",
    "DeveloperReach",
    "NodeCentrality",
    "RepositoryReach",
    "TopicReach",
    "connected_components",
    "developer_centrality",
    "developer_centrality_of",
    "developer_reach",
    "repository_centrality",
    "repository_centrality_of",
    "repository_reach",
    "topic_centrality",
    "topic_centrality_of",
    "topic_reach",
]
