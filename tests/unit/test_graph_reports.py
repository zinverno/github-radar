"""Unit tests for the Phase 4 graph report builders.

The reports are projections of the ecosystem graph: reach, centrality,
relationships, and bridge signals — nothing more. These tests pin the shapes
and the "missing node is empty, not an exception" contract.
"""

from __future__ import annotations

from github_radar.graph.reports import (
    build_cross_topic_bridge_report,
    build_developer_report,
    build_ecosystem_report,
    build_repository_report,
    build_topic_report,
)
from tests.unit.test_bridges import REFERENCE, _real_bridge_builder
from tests.unit.test_graph_metrics import _metric_builder

# ---------------------------------------------------------------------------
# Ecosystem report
# ---------------------------------------------------------------------------

def test_ecosystem_report_counts_nodes_edges_and_components() -> None:
    graph = _metric_builder().graph
    report = build_ecosystem_report(
        graph, reference_now=REFERENCE, window_days=7
    )
    assert report.developer_count == 4
    assert report.repository_count == 3
    assert report.topic_count == 2
    assert report.owns_edges == 3
    assert report.contributes_edges == 6
    assert report.tagged_edges == 4
    assert report.component_count == 2
    assert report.largest_component is not None
    assert report.largest_component.node_count == 8
    assert report.node_counts["repositories"] == 3
    assert report.edge_counts["tagged_with"] == 4


def test_ecosystem_report_centrality_is_limited_and_sorted() -> None:
    graph = _metric_builder().graph
    report = build_ecosystem_report(
        graph,
        reference_now=REFERENCE,
        window_days=7,
        centrality_limit=2,
        bridge_limit=1,
    )
    assert len(report.top_developer_centrality) == 2
    assert len(report.top_repository_centrality) == 2
    assert len(report.top_topic_centrality) == 2
    assert report.top_developer_centrality[0].label == "bob"
    assert len(report.top_developer_bridges) <= 1


# ---------------------------------------------------------------------------
# Developer report
# ---------------------------------------------------------------------------

def test_developer_report_assembles_reach_centrality_and_contributors() -> None:
    graph = _metric_builder().graph
    report = build_developer_report(
        graph,
        2,
        reference_now=REFERENCE,
        window_days=7,
    )
    assert report.login == "bob"
    assert report.reach.reached_repositories == 3
    assert report.reach.reached_topics == 2
    assert report.centrality is not None
    assert report.centrality.weighted_degree == 3
    assert [row.login for row in report.co_contributors] == ["alice", "carol"]
    assert report.co_contributors[0].shared_repository_count == 2
    assert report.bridge is not None


def test_developer_report_carries_bridge_for_bridging_developer() -> None:
    graph = _real_bridge_builder().graph
    report = build_developer_report(
        graph,
        1,
        reference_now=REFERENCE,
        window_days=7,
    )
    assert report.bridge is not None
    assert report.bridge.bridge_score > 0
    assert report.bridge.meaningful_topic_memberships == 2
    assert report.co_contributors == ()


def test_developer_report_missing_node_is_empty() -> None:
    report = build_developer_report(
        _metric_builder().graph,
        999,
        reference_now=REFERENCE,
        window_days=7,
    )
    assert report.login == "999"
    assert report.reach.reached_repositories == 0
    assert report.centrality is None
    assert report.bridge is None
    assert report.co_contributors == ()


# ---------------------------------------------------------------------------
# Repository report
# ---------------------------------------------------------------------------

def test_repository_report_related_repositories_and_bridge() -> None:
    graph = _metric_builder().graph
    report = build_repository_report(
        graph,
        1,
        reference_now=REFERENCE,
        window_days=7,
    )
    assert report.full_name == "acme/core"
    assert report.reach.contributing_developers == 2
    assert [row.full_name for row in report.related_repositories] == [
        "acme/utils",
        "web/app",
    ]
    related = report.related_repositories[0]
    assert related.shared_developer_count == 2
    assert related.shared_topic_count == 1
    assert related.relationship_score > 0
    assert report.bridge is not None
    assert report.bridge.cross_repository_contributors > 0


def test_repository_report_missing_node_is_empty() -> None:
    report = build_repository_report(
        _metric_builder().graph,
        999,
        reference_now=REFERENCE,
        window_days=7,
    )
    assert report.full_name == "repo-999"
    assert report.reach.sibling_repositories == 0
    assert report.centrality is None
    assert report.bridge is None
    assert report.related_repositories == ()


# ---------------------------------------------------------------------------
# Topic report
# ---------------------------------------------------------------------------

def test_topic_report_reach_and_related_topics() -> None:
    graph = _metric_builder().graph
    ai_node = graph.topic_by_name("ai")
    assert ai_node is not None
    report = build_topic_report(graph, ai_node.topic_id)
    assert report.name == "ai"
    assert report.reach.repositories == 2
    assert report.reach.contributing_developers == 2
    assert report.centrality is not None
    assert len(report.related_topics) == 1
    related = report.related_topics[0]
    assert related.topic == "mcp"
    assert related.shared_repository_count == 1
    assert related.shared_developer_count == 2


def test_topic_report_missing_node_is_empty() -> None:
    report = build_topic_report(_metric_builder().graph, 999)
    assert report.name == "topic-999"
    assert report.reach.repositories == 0
    assert report.related_topics == ()


# ---------------------------------------------------------------------------
# Cross-topic bridge report
# ---------------------------------------------------------------------------

def test_cross_topic_bridge_report_aggregates_the_two_topics() -> None:
    graph = _real_bridge_builder().graph
    report = build_cross_topic_bridge_report(
        graph,
        "mcp",
        "browser-agents",
        reference_now=REFERENCE,
        window_days=7,
    )
    assert report.topic_a == "mcp"
    assert report.topic_b == "browser-agents"
    assert report.bridge_count == 1
    assert report.distinct_bridging_developers == 1
    assert report.average_bridge_score > 0
    assert report.top_bridges[0].login == "multi-topic"
    assert report.shared_tracked_repositories == ()


def test_cross_topic_bridge_report_empty_topics() -> None:
    b = _metric_builder()
    graph = b.graph
    report = build_cross_topic_bridge_report(
        graph,
        "missing-a",
        "browser-agents",
        reference_now=REFERENCE,
        window_days=7,
    )
    assert report.bridge_count == 0
    assert report.distinct_bridging_developers == 0
    assert report.average_bridge_score == 0.0
    assert report.top_bridges == ()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_builders_are_deterministic_across_runs() -> None:
    graph = _metric_builder().graph
    first = build_ecosystem_report(
        graph, reference_now=REFERENCE, window_days=7, centrality_limit=2
    )
    second_graph = _metric_builder().graph
    second = build_ecosystem_report(
        second_graph, reference_now=REFERENCE, window_days=7, centrality_limit=2
    )
    assert first == second

    dev_a = build_developer_report(
        graph, 2, reference_now=REFERENCE, window_days=7
    )
    dev_b = build_developer_report(
        graph, 2, reference_now=REFERENCE, window_days=7
    )
    assert dev_a == dev_b