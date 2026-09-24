"""Unit tests for the deterministic graph metrics (reach, centrality, components).

The shared scenario builds one small ecosystem graph:

* developers: alice (owner+contributor on the acme repos), bob (all three),
  carol (web/app owner+contributor), dave (isolated, no repositories);
* repositories: acme/core (ai), acme/utils (ai, mcp), web/app (mcp, owned by
  carol).

All assertions follow the phase contract: reach counts exact nodes, centrality
weights shared evidence, components are deterministic.
"""

from __future__ import annotations

from github_radar.graph.metrics import (
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
from tests.unit.test_bridges import GraphBuilder


def _metric_builder() -> GraphBuilder:
    b = GraphBuilder()
    b.add_dev(1, "alice")
    b.add_dev(2, "bob")
    b.add_dev(3, "carol")
    b.add_dev(4, "dave")
    b.add_repo(1, "acme/core", topics=("ai",), owner=1)
    b.add_repo(2, "acme/utils", topics=("ai", "mcp"), owner=1)
    b.add_repo(3, "web/app", topics=("mcp",), owner=3)
    b.add_contrib(1, 1, 30)
    b.add_contrib(2, 1, 20)
    b.add_contrib(1, 2, 40)
    b.add_contrib(2, 2, 10)
    b.add_contrib(2, 3, 25)
    b.add_contrib(3, 3, 35)
    return b


# ---------------------------------------------------------------------------
# Reach
# ---------------------------------------------------------------------------

def test_developer_reach_counts_repos_peers_and_topics() -> None:
    graph = _metric_builder().graph
    alice = developer_reach(graph, 1)
    assert alice.login == "alice"
    assert alice.contributed_repositories == 2
    assert alice.owned_repositories == 2
    assert alice.reached_repositories == 2
    assert alice.reached_developers == 1  # bob only
    assert alice.reached_topics == 2  # ai, mcp

    bob = developer_reach(graph, 2)
    assert bob.contributed_repositories == 3
    assert bob.owned_repositories == 0
    assert bob.reached_developers == 2  # alice, carol
    assert bob.reached_topics == 2

    dave = developer_reach(graph, 4)
    assert dave.reached_repositories == 0
    assert dave.reached_developers == 0
    assert dave.reached_topics == 0


def test_developer_reach_missing_node_is_zero() -> None:
    reach = developer_reach(_metric_builder().graph, 999)
    assert reach.login == "999"
    assert reach.reached_repositories == 0
    assert reach.reached_developers == 0


def test_repository_reach_counts_developers_topics_and_siblings() -> None:
    graph = _metric_builder().graph
    core = repository_reach(graph, 1)
    assert core.full_name == "acme/core"
    assert core.contributing_developers == 2
    assert core.owning_developers == 1
    assert core.topics == 1
    assert core.sibling_repositories == 2  # acme/utils, web/app

    app = repository_reach(graph, 3)
    assert app.full_name == "web/app"
    assert app.owning_developers == 1
    assert app.sibling_repositories == 2


def test_repository_reach_missing_node_is_zero() -> None:
    reach = repository_reach(_metric_builder().graph, 999)
    assert reach.full_name == "repo-999"
    assert reach.sibling_repositories == 0


def test_topic_reach_counts_repositories_and_developers() -> None:
    graph = _metric_builder().graph
    ai_node = graph.topic_by_name("ai")
    assert ai_node is not None
    mcp_node = graph.topic_by_name("mcp")
    assert mcp_node is not None
    ai = topic_reach(graph, ai_node.topic_id)
    assert ai.repositories == 2
    assert ai.contributing_developers == 2
    assert ai.owning_developers == 1
    assert ai.associated_developers == 2

    mcp = topic_reach(graph, mcp_node.topic_id)
    assert mcp.repositories == 2
    assert mcp.contributing_developers == 3
    assert mcp.owning_developers == 2
    assert mcp.associated_developers == 3


def test_topic_reach_missing_node_is_zero() -> None:
    reach = topic_reach(_metric_builder().graph, 999)
    assert reach.name == "topic-999"
    assert reach.repositories == 0


# ---------------------------------------------------------------------------
# Centrality
# ---------------------------------------------------------------------------

def test_developer_centrality_weights_shared_repositories() -> None:
    graph = _metric_builder().graph
    rows = developer_centrality(graph)
    by_label = {row.label: row for row in rows}
    assert by_label["bob"].degree == 2  # alice, carol
    assert by_label["bob"].weighted_degree == 3  # 2 shared repos + 1
    assert by_label["alice"].degree == 1
    assert by_label["alice"].weighted_degree == 2
    assert by_label["carol"].degree == 1
    assert by_label["carol"].weighted_degree == 1
    assert by_label["dave"].degree == 0
    assert by_label["dave"].weighted_degree == 0
    assert [row.label for row in rows] == ["bob", "alice", "carol", "dave"]


def test_developer_centrality_of_matches_ranked_row() -> None:
    graph = _metric_builder().graph
    row = developer_centrality_of(graph, 2)
    assert row is not None
    assert row.weighted_degree == 3
    assert developer_centrality_of(graph, 999) is None


def test_repository_centrality_bonus_for_shared_owner() -> None:
    graph = _metric_builder().graph
    rows = repository_centrality(graph)
    by_label = {row.label: row for row in rows}
    # acme/core and acme/utils share both developers (2) and the owner (+1).
    assert by_label["acme/core"].degree == 2
    assert by_label["acme/core"].weighted_degree == 4
    assert by_label["web/app"].degree == 2
    assert by_label["web/app"].weighted_degree == 2
    assert [row.label for row in rows] == [
        "acme/core",
        "acme/utils",
        "web/app",
    ]
    core = repository_centrality_of(graph, 1)
    assert core is not None
    assert core.weighted_degree == 4


def test_topic_centrality_counts_cooccurrence() -> None:
    graph = _metric_builder().graph
    rows = topic_centrality(graph)
    by_label = {row.label: row for row in rows}
    assert by_label["ai"].degree == 1  # co-occurs with mcp on acme/utils
    assert by_label["ai"].weighted_degree == 1
    assert by_label["mcp"].degree == 1
    assert by_label["mcp"].weighted_degree == 1
    mcp_node = graph.topic_by_name("mcp")
    assert mcp_node is not None
    mcp_row = topic_centrality_of(graph, mcp_node.topic_id)
    assert mcp_row is not None
    assert mcp_row.degree == 1


# ---------------------------------------------------------------------------
# Connected components
# ---------------------------------------------------------------------------

def test_connected_components_are_deterministic_and_sized() -> None:
    graph = _metric_builder().graph
    components = connected_components(graph)
    assert len(components) == 2  # one big + dave isolated

    main, isolated = components
    assert main.node_count == 8
    assert main.developer_count == 3
    assert main.repository_count == 3
    assert main.topic_count == 2
    assert main.developer_logins == ("alice", "bob", "carol")
    assert main.repository_full_names == (
        "acme/core",
        "acme/utils",
        "web/app",
    )
    assert main.topic_names == ("ai", "mcp")
    assert main.canonical_node == ("DEVELOPER", 1)

    assert isolated.node_count == 1
    assert isolated.developer_logins == ("dave",)
    assert isolated.canonical_node == ("DEVELOPER", 4)


def test_connected_components_empty_and_singleton_graphs() -> None:
    assert connected_components(EcosystemGraph()) == ()

    b = GraphBuilder()
    b.add_dev(1, "solo")
    components = connected_components(b.graph)
    assert len(components) == 1
    assert components[0].node_count == 1
    assert components[0].developer_logins == ("solo",)
    assert components[0].canonical_node == ("DEVELOPER", 1)


def test_connected_components_order_sizes_first() -> None:
    graph = _metric_builder().graph
    components = connected_components(graph)
    sizes = [component.node_count for component in components]
    assert sizes == sorted(sizes, reverse=True)
    assert components[0].node_count > components[-1].node_count


def test_centrality_and_components_are_identical_across_runs() -> None:
    graph_a = _metric_builder().graph
    graph_b = _metric_builder().graph
    assert developer_centrality(graph_a) == developer_centrality(graph_b)
    assert repository_centrality(graph_a) == repository_centrality(graph_b)
    assert topic_centrality(graph_a) == topic_centrality(graph_b)
    assert connected_components(graph_a) == connected_components(graph_b)