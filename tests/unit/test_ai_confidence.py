"""Deterministic artifact confidence — never a model self-assessment."""

from __future__ import annotations

from github_radar.ai.confidence import (
    bridge_level,
    developer_level,
    ecosystem_level,
    repo_level,
    strongest_level,
    topic_aggregate_level,
)
from github_radar.ai.models import ConfidenceLevel
from github_radar.analytics.topics import TopicAggregate
from github_radar.graph.bridges import (
    CrossTopicBridge,
    DeveloperBridge,
    TopicBridgeEvidence,
)
from github_radar.graph.confidence import GraphConfidence
from github_radar.graph.reports import CrossTopicBridgeReport, EcosystemGraphReport
from tests.unit.ai_fixtures import developer_report, repo_digest, topic_aggregate


def _graph_confidence(level: ConfidenceLevel, score: float = 0.9) -> GraphConfidence:
    return GraphConfidence(
        score=score,
        level=level,
        distinct_repositories=4,
        history_coverage=1.0,
        recent_coverage=1.0,
        sample_size=10,
        terms={"evidence": 1.0},
        explanation="deterministic coverage",
    )


def _topic_evidence(
    topic: str, repository_count: int, repositories: tuple[str, ...]
) -> TopicBridgeEvidence:
    return TopicBridgeEvidence(
        topic=topic,
        repository_count=repository_count,
        repositories=repositories,
        topic_strength=0.9,
        total_contributions=40,
        recent_delta=5,
        recent_activity_available=True,
        history_coverage=1.0,
    )


def _developer_bridge(topic_pair: tuple[str, str], level: ConfidenceLevel) -> DeveloperBridge:
    topic_a, topic_b = topic_pair
    return DeveloperBridge(
        developer_id=1,
        login="alice",
        bridge_score=0.8,
        components={"repos": 0.8},
        missing_components=(),
        meaningful_topic_memberships=2,
        distinct_supporting_repositories=3,
        small_sample=False,
        topic_evidence=(
            _topic_evidence(topic_a, 3, ("acme/a", "acme/b", "acme/c")),
            _topic_evidence(topic_b, 2, ("other/x", "other/y")),
        ),
        repository_evidence=("acme/a", "other/x"),
        confidence=_graph_confidence(level),
    )


def _cross_topic_bridge(topic_pair: tuple[str, str], level: ConfidenceLevel) -> CrossTopicBridge:
    topic_a, topic_b = topic_pair
    return CrossTopicBridge(
        developer_id=1,
        login="alice",
        topic_a=_topic_evidence(topic_a, 3, ("acme/a", "acme/b", "acme/c")),
        topic_b=_topic_evidence(topic_b, 2, ("other/x", "other/y")),
        shared_tracked_repositories=("acme/a", "other/x"),
        bridge_score=0.8,
        components={"repos": 0.8},
        missing_components=(),
        small_sample=False,
        confidence=_graph_confidence(level),
    )


def test_strongest_level_wins_high() -> None:
    assert strongest_level(["LOW", "HIGH", "MEDIUM"]) == "HIGH"
    assert strongest_level(["LOW", "LOW"]) == "LOW"
    assert strongest_level([]) == "LOW"


def test_repo_level_reflects_window_confidence() -> None:
    level = repo_level(repo_digest().metrics, window_days=1)  # fresh window → higher
    assert level in {"LOW", "MEDIUM", "HIGH"}
    older = repo_level(repo_digest().metrics, window_days=365)
    assert older != "HIGH"


def test_topic_aggregate_level_passthrough() -> None:
    assert topic_aggregate_level(topic_aggregate()) == "HIGH"
    thin = TopicAggregate(
        name="thin",
        repository_count=0,
        total_momentum=0.0,
        avg_momentum=0.0,
        share_with_momentum=0.0,
        confidence_score=0.0,
        confidence_level="LOW",
    )
    assert topic_aggregate_level(thin) == "LOW"


def test_developer_level_uses_strongest_coverage() -> None:
    # activity is HIGH (available), emerging is MEDIUM → overall HIGH.
    assert developer_level(developer_report()) == "HIGH"


def test_ecosystem_level_low_on_empty_graph() -> None:
    empty = EcosystemGraphReport(
        developer_count=0,
        repository_count=0,
        topic_count=0,
        owns_edges=0,
        contributes_edges=0,
        tagged_edges=0,
        component_count=0,
        largest_component=None,
    )
    assert ecosystem_level(empty) == "LOW"


def test_ecosystem_level_medium_with_nodes_high_with_bridge() -> None:
    with_nodes = EcosystemGraphReport(
        developer_count=5,
        repository_count=4,
        topic_count=2,
        owns_edges=4,
        contributes_edges=3,
        tagged_edges=2,
        component_count=1,
        largest_component=None,
    )
    assert ecosystem_level(with_nodes) == "MEDIUM"

    with_bridge = EcosystemGraphReport(
        developer_count=5,
        repository_count=4,
        topic_count=2,
        owns_edges=4,
        contributes_edges=3,
        tagged_edges=2,
        component_count=1,
        largest_component=None,
        top_developer_bridges=(_developer_bridge(("mcp", "ai-agents"), "HIGH"),),
    )
    assert ecosystem_level(with_bridge) == "HIGH"


def test_bridge_level_low_without_bridges() -> None:
    report = CrossTopicBridgeReport(
        topic_a="mcp",
        topic_b="ai-agents",
        bridge_count=0,
        distinct_bridging_developers=0,
        average_bridge_score=0.0,
    )
    assert bridge_level(report) == "LOW"


def test_bridge_level_dominated_by_strongest_bridge() -> None:
    report = CrossTopicBridgeReport(
        topic_a="mcp",
        topic_b="ai-agents",
        bridge_count=2,
        distinct_bridging_developers=2,
        average_bridge_score=0.6,
        top_bridges=(
            _cross_topic_bridge(("mcp", "ai-agents"), "LOW"),
            _cross_topic_bridge(("mcp", "ai-agents"), "HIGH"),
        ),
    )
    assert bridge_level(report) == "HIGH"