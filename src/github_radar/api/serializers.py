"""Deterministic serializers: analytics dataclasses → JSON-safe mappings.

The API contract guarantees two things that the whole codebase already builds on:

* **null means unavailable, never zero** — a ``None`` delta, momentum, growth or
  confidence field is serialized as ``null`` (or an explicit label), so a
  missing window never masquerades as zero activity;
* **incomplete is reported, not fabricated** — every ``TimeWindow`` carries its
  ``complete`` flag.

Timestamps serialize as ISO-8601 UTC. All scores keep their raw float values;
the SPA rounds for display.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from github_radar.ai.models import AIArtifact, ArtifactKey
from github_radar.analytics import (
    Confidence,
    FieldDelta,
    MomentumScore,
    ProfileFieldDelta,
    RepoMetrics,
    TimeWindow,
    TopicAggregate,
    TopicRelevance,
)
from github_radar.analytics.developers import DeveloperProfileMetrics
from github_radar.analytics.intelligence import (
    DeveloperActivity,
    EcosystemScore,
    EmergingDeveloper,
    RepoContext,
)
from github_radar.analytics.trends import Trend
from github_radar.graph.bridges import (
    CrossTopicBridge,
    DeveloperBridge,
    RepositoryBridge,
    TopicBridgeEvidence,
)
from github_radar.graph.confidence import GraphConfidence
from github_radar.graph.metrics import (
    ConnectedComponent,
    DeveloperReach,
    NodeCentrality,
    RepositoryReach,
    TopicReach,
)
from github_radar.graph.model import EcosystemGraph
from github_radar.graph.overlap import Overlap
from github_radar.graph.relationships import (
    CoContributorRelationship,
    RepositoryRelationship,
    SharedRepositoryEvidence,
    TopicRelationship,
)
from github_radar.graph.reports import EcosystemGraphReport
from github_radar.graph.scoring import RecentActivity


def iso(value: datetime | None) -> str | None:
    """ISO-8601 UTC string, or ``None`` (timezone-aware, ``Z`` suffix)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _f(value: float | None) -> float | None:
    return None if value is None else float(value)


# ---------------------------------------------------------------------------
# Analytics primitives
# ---------------------------------------------------------------------------


def serialize_window(window: TimeWindow) -> dict[str, Any]:
    return {
        "span_days": _f(window.span_days),
        "complete": window.complete,
        "start": iso(window.start),
        "end": iso(window.end),
    }


def serialize_field_delta(delta: FieldDelta | None) -> dict[str, Any] | None:
    if delta is None:
        return None
    return {
        "field": delta.field,
        "window_days": delta.window_days,
        "base_value": delta.base_value,
        "current_value": delta.current_value,
        "delta": delta.delta,
        "per_day": _f(delta.per_day),
        "growth_pct": _f(delta.growth_pct),
        "base_gap_hours": _f(delta.base_gap_hours),
        "window": serialize_window(delta.window),
        "available": delta.delta is not None,
    }


def serialize_profile_delta(
    delta: ProfileFieldDelta | None,
) -> dict[str, Any] | None:
    if delta is None:
        return None
    return {
        "window_days": delta.window_days,
        "base_value": delta.base_value,
        "current_value": delta.current_value,
        "delta": delta.delta,
        "span_days": _f(delta.span_days),
        "base_gap_days": _f(delta.base_gap_days),
        "complete": delta.complete,
        "observations": delta.observations,
        "base_captured_at": iso(delta.base_captured_at),
        "current_captured_at": iso(delta.current_captured_at),
        "available": delta.delta is not None,
    }


def serialize_metrics(metrics: RepoMetrics) -> dict[str, Any]:
    return {
        "latest_captured_at": iso(metrics.latest_captured_at),
        "latest_pushed_at": iso(metrics.latest_pushed_at),
        "snapshot_count": len(metrics.series),
        "latest_counts": {
            "stars": metrics.latest_counts.get("stars"),
            "forks": metrics.latest_counts.get("forks"),
            "watchers": metrics.latest_counts.get("watchers"),
            "open_issues": metrics.latest_counts.get("open_issues"),
        },
        "deltas": {
            f"{field}/{days}": serialize_field_delta(metrics.get(field, days))
            for field in ("stars", "forks", "watchers", "open_issues")
            for days in (1, 7, 30)
        },
        "stars_7d": serialize_field_delta(metrics.stars_7d),
        "stars_30d": serialize_field_delta(metrics.stars_30d),
        "history_days": _history_days(metrics),
    }


def _history_days(metrics: RepoMetrics) -> float | None:
    first = metrics.series.first
    last = metrics.series.last
    if first is None or last is None:
        return None
    return (last.captured_at - first.captured_at).total_seconds() / 86400.0


def serialize_momentum(momentum: MomentumScore | None) -> dict[str, Any] | None:
    if momentum is None:
        return None
    return {
        "score": momentum.score,
        "max_score": momentum.max_score,
        "stars_growth_pct": momentum.stars_growth_pct,
        "forks_growth_pct": momentum.forks_growth_pct,
        "recency": momentum.recency,
        "within_bounds": momentum.within_bounds,
        "components": dict(momentum.components),
    }


def serialize_confidence(confidence: Confidence) -> dict[str, Any]:
    return {
        "level": confidence.level,
        "score": confidence.score,
        "snapshot_count": confidence.snapshot_count,
        "history_days": _f(confidence.history_days),
        "window_span_days": _f(confidence.window_span_days),
        "base_gap_days": _f(confidence.base_gap_days),
        "count_term": confidence.count_term,
        "span_term": confidence.span_term,
        "gap_term": confidence.gap_term,
    }


def serialize_trend(trend: Trend | None) -> dict[str, Any] | None:
    if trend is None:
        return None
    return {
        "trend": trend.trend,
        "label": trend.label,
        "explanation": trend.explanation,
        "growth_pct": _f(trend.growth_pct),
        "recency": trend.recency,
    }


def serialize_graph_confidence(
    confidence: GraphConfidence | None,
) -> dict[str, Any] | None:
    if confidence is None:
        return None
    return {
        "score": confidence.score,
        "level": confidence.level,
        "distinct_repositories": confidence.distinct_repositories,
        "history_coverage": confidence.history_coverage,
        "recent_coverage": confidence.recent_coverage,
        "sample_size": confidence.sample_size,
        "terms": dict(confidence.terms),
        "explanation": confidence.explanation,
        "available": confidence.available,
    }


def serialize_overlap(overlap: Overlap[Any]) -> dict[str, Any]:
    return {
        "intersection_count": overlap.intersection_count,
        "union_count": overlap.union_count,
        "jaccard": overlap.jaccard,
    }


def serialize_recent_activity(activity: RecentActivity) -> dict[str, Any]:
    return {
        "window_days": activity.window_days,
        "available": activity.available,
        "delta": activity.delta,
        "complete": activity.complete,
        "observations": activity.observations,
    }


# ---------------------------------------------------------------------------
# Topic aggregate
# ---------------------------------------------------------------------------


def serialize_topic_aggregate(aggregate: TopicAggregate) -> dict[str, Any]:
    """Emit momentum totals as ``None`` when nothing is computable.

    ``aggregate_topics`` uses a ``0.0`` placeholder for topics where no member
    repository has a computable momentum.  Rendering that as ``0.00`` would
    imply a real (zero) value, so the API projects ``None`` instead; the pure
    analytics layer (and the CLI) keep the ``0.0`` semantics.
    """
    has_momentum = aggregate.share_with_momentum > 0
    return {
        "name": aggregate.name,
        "repository_count": aggregate.repository_count,
        "total_momentum": aggregate.total_momentum if has_momentum else None,
        "avg_momentum": aggregate.avg_momentum if has_momentum else None,
        "share_with_momentum": aggregate.share_with_momentum,
        "confidence": {
            "score": aggregate.confidence_score,
            "level": aggregate.confidence_level,
        },
    }


# ---------------------------------------------------------------------------
# Developer intelligence
# ---------------------------------------------------------------------------


def serialize_profile(profile: DeveloperProfileMetrics | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {
        "observations": profile.observations,
        "first_captured_at": iso(profile.first_captured_at),
        "last_captured_at": iso(profile.last_captured_at),
        "latest_followers": profile.latest_followers,
        "latest_following": profile.latest_following,
        "latest_public_repos": profile.latest_public_repos,
        "deltas": {
            f"{field}/{days}": serialize_profile_delta(profile.deltas.get((field, days)))
            for field in ("followers", "following", "public_repos")
            for days in (1, 7, 30)
        },
        "followers_7d": serialize_profile_delta(profile.followers_delta(7)),
        "followers_30d": serialize_profile_delta(profile.followers_delta(30)),
    }


def serialize_activity(activity: DeveloperActivity | None) -> dict[str, Any] | None:
    if activity is None:
        return None
    return {
        "window_days": activity.window_days,
        "available": activity.available,
        "score": activity.score,
        "components": dict(activity.components),
        "confidence_score": activity.confidence_score,
        "confidence_level": activity.confidence_level,
        "total_positive_delta": activity.total_positive_delta,
        "active_repos": activity.active_repos,
        "usable_repos": activity.usable_repos,
        "observations": activity.observations,
        "complete_share": activity.complete_share,
    }


def serialize_ecosystem(ecosystem: EcosystemScore | None) -> dict[str, Any] | None:
    if ecosystem is None:
        return None
    return {
        "score": ecosystem.score,
        "components": dict(ecosystem.components),
        "missing_components": list(ecosystem.missing_components),
        "associated_repositories": ecosystem.associated_repositories,
        "owned_repositories": ecosystem.owned_repositories,
        "topic_relevance_mean": ecosystem.topic_relevance_mean,
        "momentum_mean": ecosystem.momentum_mean,
    }


def serialize_emerging(emerging: EmergingDeveloper) -> dict[str, Any]:
    return {
        "label": emerging.label,
        "score": emerging.score,
        "components": dict(emerging.components),
        "raw_signals": dict(emerging.raw_signals),
        "confidence_score": emerging.confidence_score,
        "confidence_level": emerging.confidence_level,
        "evidence_points": emerging.evidence_points,
        "explanation": emerging.explanation,
    }


def serialize_topic_relevance(relevance: TopicRelevance) -> dict[str, Any]:
    return {
        "topic": relevance.topic,
        "score": relevance.score,
        "components": dict(relevance.components),
        "confidence_score": relevance.confidence_score,
        "confidence_level": relevance.confidence_level,
        "repo_count": relevance.repo_count,
        "owned_count": relevance.owned_count,
        "contribution_share": relevance.contribution_share,
        "recent_contribution_delta": relevance.recent_contribution_delta,
        "momentum_mean": relevance.momentum_mean,
        "evidence": list(relevance.evidence),
    }


# ---------------------------------------------------------------------------
# Graph primitives
# ---------------------------------------------------------------------------

_EDGE_TYPE_LABELS = {
    "OWNS": "owns",
    "CONTRIBUTES_TO": "contributes_to",
    "TAGGED_WITH": "tagged_with",
}


def serialize_centrality(centrality: NodeCentrality | None) -> dict[str, Any] | None:
    if centrality is None:
        return None
    return {
        "node_type": centrality.node_type,
        "node_id": centrality.node_id,
        "label": centrality.label,
        "degree": centrality.degree,
        "weighted_degree": centrality.weighted_degree,
    }


def serialize_developer_reach(reach: DeveloperReach) -> dict[str, Any]:
    return {
        "developer_id": reach.developer_id,
        "login": reach.login,
        "contributed_repositories": reach.contributed_repositories,
        "owned_repositories": reach.owned_repositories,
        "reached_repositories": reach.reached_repositories,
        "reached_developers": reach.reached_developers,
        "reached_topics": reach.reached_topics,
    }


def serialize_repository_reach(reach: RepositoryReach) -> dict[str, Any]:
    return {
        "repository_id": reach.repository_id,
        "full_name": reach.full_name,
        "contributing_developers": reach.contributing_developers,
        "owning_developers": reach.owning_developers,
        "topics": reach.topics,
        "sibling_repositories": reach.sibling_repositories,
    }


def serialize_topic_reach(reach: TopicReach) -> dict[str, Any]:
    return {
        "topic_id": reach.topic_id,
        "name": reach.name,
        "repositories": reach.repositories,
        "contributing_developers": reach.contributing_developers,
        "owning_developers": reach.owning_developers,
        "associated_developers": reach.associated_developers,
    }


def serialize_component(component: ConnectedComponent) -> dict[str, Any]:
    return {
        "node_count": component.node_count,
        "developer_count": component.developer_count,
        "repository_count": component.repository_count,
        "topic_count": component.topic_count,
        "developer_logins": list(component.developer_logins),
        "repository_full_names": list(component.repository_full_names),
        "topic_names": list(component.topic_names),
        "canonical_node": list(component.canonical_node),
    }


def serialize_shared_evidence(evidence: SharedRepositoryEvidence) -> dict[str, Any]:
    return {
        "repository_id": evidence.repository_id,
        "full_name": evidence.full_name,
        "a_contributions": evidence.a_contributions,
        "b_contributions": evidence.b_contributions,
        "a_share": evidence.a_share,
        "b_share": evidence.b_share,
        "momentum_score": _f(evidence.momentum_score),
        "a_activity": serialize_recent_activity(evidence.a_activity),
        "b_activity": serialize_recent_activity(evidence.b_activity),
    }


def serialize_co_contributor(
    relationship: CoContributorRelationship,
) -> dict[str, Any]:
    return {
        "developer_id": relationship.developer_id,
        "login": relationship.login,
        "relationship": relationship.relationship,
        "shared_repository_count": relationship.shared_repository_count,
        "shared_repositories": [
            serialize_shared_evidence(item)
            for item in relationship.shared_repositories
        ],
        "strength": relationship.strength,
        "components": dict(relationship.components or {}),
        "missing_components": list(relationship.missing_components),
        "confidence": serialize_graph_confidence(relationship.confidence),
    }


def serialize_repository_relationship(
    relationship: RepositoryRelationship,
) -> dict[str, Any]:
    return {
        "repository_id": relationship.repository_id,
        "full_name": relationship.full_name,
        "shared_developer_count": relationship.shared_developer_count,
        "shared_topic_count": relationship.shared_topic_count,
        "developer_overlap": serialize_overlap(relationship.developer_overlap),
        "topic_overlap": serialize_overlap(relationship.topic_overlap),
        "relationship_score": relationship.relationship_score,
        "components": dict(relationship.components),
        "confidence": serialize_graph_confidence(relationship.confidence),
    }


def serialize_topic_relationship(
    relationship: TopicRelationship,
) -> dict[str, Any]:
    return {
        "topic_id": relationship.topic_id,
        "topic": relationship.topic,
        "shared_repository_count": relationship.shared_repository_count,
        "shared_developer_count": relationship.shared_developer_count,
        "repository_overlap": serialize_overlap(relationship.repository_overlap),
        "developer_overlap": serialize_overlap(relationship.developer_overlap),
        "confidence": serialize_graph_confidence(relationship.confidence),
    }


def serialize_topic_bridge_evidence(
    evidence: TopicBridgeEvidence,
) -> dict[str, Any]:
    return {
        "topic": evidence.topic,
        "repository_count": evidence.repository_count,
        "repositories": list(evidence.repositories),
        "topic_strength": evidence.topic_strength,
        "total_contributions": evidence.total_contributions,
        "recent_delta": evidence.recent_delta,
        "recent_activity_available": evidence.recent_activity_available,
        "history_coverage": evidence.history_coverage,
    }


def serialize_developer_bridge(bridge: DeveloperBridge) -> dict[str, Any]:
    return {
        "developer_id": bridge.developer_id,
        "login": bridge.login,
        "bridge_score": bridge.bridge_score,
        "components": dict(bridge.components),
        "missing_components": list(bridge.missing_components),
        "meaningful_topic_memberships": bridge.meaningful_topic_memberships,
        "distinct_supporting_repositories": bridge.distinct_supporting_repositories,
        "small_sample": bridge.small_sample,
        "topic_evidence": [
            serialize_topic_bridge_evidence(item) for item in bridge.topic_evidence
        ],
        "repository_evidence": list(bridge.repository_evidence),
        "confidence": serialize_graph_confidence(bridge.confidence),
    }


def serialize_repository_bridge(bridge: RepositoryBridge) -> dict[str, Any]:
    return {
        "repository_id": bridge.repository_id,
        "full_name": bridge.full_name,
        "bridge_score": bridge.bridge_score,
        "components": dict(bridge.components),
        "missing_components": list(bridge.missing_components),
        "contributor_count": bridge.contributor_count,
        "topic_count": bridge.topic_count,
        "cross_repository_contributors": bridge.cross_repository_contributors,
        "cross_topic_contributors": bridge.cross_topic_contributors,
        "small_sample": bridge.small_sample,
        "confidence": serialize_graph_confidence(bridge.confidence),
    }


def serialize_cross_topic_bridge(bridge: CrossTopicBridge) -> dict[str, Any]:
    return {
        "developer_id": bridge.developer_id,
        "login": bridge.login,
        "topic_a": serialize_topic_bridge_evidence(bridge.topic_a),
        "topic_b": serialize_topic_bridge_evidence(bridge.topic_b),
        "shared_tracked_repositories": list(bridge.shared_tracked_repositories),
        "bridge_score": bridge.bridge_score,
        "components": dict(bridge.components),
        "missing_components": list(bridge.missing_components),
        "small_sample": bridge.small_sample,
        "confidence": serialize_graph_confidence(bridge.confidence),
    }


def serialize_ecosystem_report(report: EcosystemGraphReport) -> dict[str, Any]:
    return {
        "developer_count": report.developer_count,
        "repository_count": report.repository_count,
        "topic_count": report.topic_count,
        "node_counts": report.node_counts,
        "edge_counts": report.edge_counts,
        "component_count": report.component_count,
        "largest_component": (
            serialize_component(report.largest_component)
            if report.largest_component is not None
            else None
        ),
        "top_developer_centrality": [
            serialize_centrality(item) for item in report.top_developer_centrality
        ],
        "top_repository_centrality": [
            serialize_centrality(item) for item in report.top_repository_centrality
        ],
        "top_topic_centrality": [
            serialize_centrality(item) for item in report.top_topic_centrality
        ],
        "top_developer_bridges": [
            serialize_developer_bridge(item) for item in report.top_developer_bridges
        ],
    }


# ---------------------------------------------------------------------------
# Graph nodes / edges (raw model)
# ---------------------------------------------------------------------------


def serialize_graph_nodes(
    graph: EcosystemGraph,
    *,
    limit: int,
    offset: int,
    scores: dict[tuple[str, int], tuple[int, float]] | None = None,
) -> dict[str, Any]:
    """Project graph nodes; each row carries deterministic centrality scores.

    ``scores`` maps ``(node_type, node_id)`` to ``(degree, weighted_degree)``
    from :mod:`github_radar.graph.metrics`, so views can prioritize by the same
    deterministic scores the CLI reports. Unknown nodes default to ``0``.
    """
    nodes = graph.nodes()
    total = len(nodes)
    selected = nodes[offset : offset + limit]
    items: list[dict[str, Any]] = []
    for node in selected:
        degree, weighted_degree = (
            scores.get((node.node_type, node.node_id), (0, 0.0))
            if scores is not None
            else (0, 0.0)
        )
        items.append(
            {
                "node_type": node.node_type,
                "node_id": node.node_id,
                "label": node.label,
                "degree": degree,
                "weighted_degree": weighted_degree,
            }
        )
    return {
        "items": items,
        "limit": limit,
        "offset": offset,
        "total": total,
        "returned": len(items),
        "truncated": offset + len(items) < total,
    }


def serialize_graph_edges(
    graph: EcosystemGraph,
    *,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    edges = graph.edges()
    total = len(edges)
    selected = edges[offset : offset + limit]
    rows = []
    for edge in selected:
        row: dict[str, Any] = {
            "edge_type": _EDGE_TYPE_LABELS.get(edge.edge_type, edge.edge_type),
            "source": list(edge.source),
            "target": list(edge.target),
        }
        if edge.evidence is not None and edge.evidence.edge_type == "CONTRIBUTES_TO":
            row["contributions"] = edge.evidence.contributions
            row["share"] = edge.evidence.share
            row["observations"] = len(edge.evidence.observations)
        rows.append(row)
    return {
        "items": rows,
        "limit": limit,
        "offset": offset,
        "total": total,
        "returned": len(selected),
        "truncated": offset + len(selected) < total,
    }


def graph_counts(graph: EcosystemGraph) -> dict[str, int]:
    return {
        "developers": len(graph.developer_ids),
        "repositories": len(graph.repository_ids),
        "topics": len(graph.topic_ids),
    }


# ---------------------------------------------------------------------------
# AI artifacts
# ---------------------------------------------------------------------------


def serialize_artifact_key(key: ArtifactKey) -> dict[str, Any]:
    return {
        "entity_type": key.entity_type,
        "entity_key": key.entity_key,
        "artifact_type": key.artifact_type,
        "window_days": key.window_days,
        "source_fingerprint": key.source_fingerprint,
        "prompt_version": key.prompt_version,
        "model": key.model,
        "provider": key.provider,
    }


def serialize_artifact(artifact: AIArtifact) -> dict[str, Any]:
    return {
        "entity_type": artifact.entity_type,
        "entity_key": artifact.entity_key,
        "artifact_type": artifact.artifact_type,
        "window_days": artifact.window_days,
        "prompt_version": artifact.prompt_version,
        "provider": artifact.provider,
        "model": artifact.model,
        "generated_at": iso(artifact.generated_at),
        "source_fingerprint": artifact.source_fingerprint,
        "headline": artifact.headline,
        "summary": artifact.summary,
        "key_points": [
            {
                "text": point.text,
                "evidence_ids": list(point.evidence_ids),
            }
            for point in artifact.key_points
        ],
        "unknowns": list(artifact.unknowns),
        "confidence": artifact.confidence,
        "input_tokens": artifact.input_tokens,
        "output_tokens": artifact.output_tokens,
    }


def serialize_repo_context(context: RepoContext, *, reference_now: datetime) -> dict[str, Any]:
    return {
        "repository_id": context.repository_id,
        "full_name": context.full_name,
        "primary_language": context.primary_language,
        "owner_developer_id": context.owner_developer_id,
        "topics": sorted(context.topics),
        "momentum": serialize_momentum(context.momentum),
        "trend": serialize_trend(context.trend),
        "latest_stars": context.latest_stars,
    }


__all__ = [
    "graph_counts",
    "iso",
    "serialize_activity",
    "serialize_artifact",
    "serialize_artifact_key",
    "serialize_centrality",
    "serialize_co_contributor",
    "serialize_component",
    "serialize_confidence",
    "serialize_cross_topic_bridge",
    "serialize_developer_bridge",
    "serialize_developer_reach",
    "serialize_ecosystem",
    "serialize_ecosystem_report",
    "serialize_emerging",
    "serialize_field_delta",
    "serialize_graph_confidence",
    "serialize_graph_edges",
    "serialize_graph_nodes",
    "serialize_metrics",
    "serialize_momentum",
    "serialize_overlap",
    "serialize_profile",
    "serialize_profile_delta",
    "serialize_recent_activity",
    "serialize_repo_context",
    "serialize_repository_bridge",
    "serialize_repository_reach",
    "serialize_repository_relationship",
    "serialize_shared_evidence",
    "serialize_topic_aggregate",
    "serialize_topic_bridge_evidence",
    "serialize_topic_reach",
    "serialize_topic_relevance",
    "serialize_topic_relationship",
    "serialize_trend",
    "serialize_window",
]