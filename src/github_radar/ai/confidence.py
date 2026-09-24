"""Deterministic artifact confidence.

The AI layer never asks the model how confident it is. Every artifact's
``confidence`` is derived from the *data-coverage* confidence already computed
by Phases 2–4 — the same LOW / MEDIUM / HIGH conventions used across the
project — so a confident-sounding sentence backed by thin history is impossible:
thin history always yields LOW or MEDIUM regardless of what the model wrote.
"""

from __future__ import annotations

from collections.abc import Iterable

from github_radar.analytics.confidence import (
    ConfidenceLevel,
    TopicConfidenceRule,
    repo_window_confidence,
)
from github_radar.analytics.metrics import RepoMetrics
from github_radar.analytics.topics import TopicAggregate
from github_radar.graph.confidence import GraphConfidence
from github_radar.graph.reports import CrossTopicBridgeReport, EcosystemGraphReport
from github_radar.services.intelligence import DeveloperReport

_LEVEL_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def stronger(
    level: ConfidenceLevel, candidate: ConfidenceLevel
) -> ConfidenceLevel:
    return candidate if _LEVEL_ORDER[candidate] > _LEVEL_ORDER[level] else level


def strongest_level(levels: Iterable[ConfidenceLevel]) -> ConfidenceLevel:
    """The most conservative—i.e. highest-coverage—level of ``levels``."""
    result: ConfidenceLevel = "LOW"
    for level in levels:
        result = stronger(result, level)
    return result


def repo_level(metrics: RepoMetrics, window_days: int) -> ConfidenceLevel:
    """Coverage confidence of a repository's windowed growth metrics."""
    return repo_window_confidence(metrics, window_days=window_days).level


def topic_rule_level(rule: TopicConfidenceRule) -> ConfidenceLevel:
    """Coverage confidence of a topic-level aggregation rule."""
    return rule.level


def topic_aggregate_level(aggregate: TopicAggregate) -> ConfidenceLevel:
    """Coverage confidence carried by a :class:`TopicAggregate`."""
    return aggregate.confidence_level


def developer_level(report: DeveloperReport) -> ConfidenceLevel:
    """Coverage confidence of a developer synthesis.

    Uses the strongest observed deterministic coverage among the activity and
    emerging classifications (each is itself a bounded, small-sample-aware
    score); with no usable signal at all the level stays LOW.
    """
    levels: list[ConfidenceLevel] = []
    activity = report.activity
    if activity is not None and activity.available:
        levels.append(activity.confidence_level)
    levels.append(report.emerging.confidence_level)
    return strongest_level(levels)


def graph_confidence_level(confidence: GraphConfidence | None) -> ConfidenceLevel:
    """Coverage confidence of a graph-derived ecosystem reading."""
    if confidence is None or not confidence.available:
        return "LOW"
    return confidence.level


def ecosystem_level(report: EcosystemGraphReport) -> ConfidenceLevel:
    """Coverage confidence of the whole-ecosystem synthesis.

    Deterministic rule: every node-type that is present adds a MEDIUM signal;
    every top developer bridge adds its own coverage level. The strongest
    signal wins, so a cold dataset (no nodes, no edges) can only ever be LOW
    while a well-evidenced graph can reach HIGH through its strongest bridges.
    """
    signals: list[ConfidenceLevel] = []
    for present in (
        report.developer_count > 0,
        report.repository_count > 0,
        report.topic_count > 0,
        report.tagged_edges > 0,
    ):
        if present:
            signals.append("MEDIUM")
    signals.extend(bridge.confidence.level for bridge in report.top_developer_bridges)
    return strongest_level(signals)


def bridge_level(report: CrossTopicBridgeReport) -> ConfidenceLevel:
    """Coverage confidence of a two-topic bridge synthesis.

    The strongest single bridge confidence dominates the aggregate so a lone,
    well-evidenced bridge is never hidden behind an average of thin ones.
    """
    levels = [bridge.confidence.level for bridge in report.top_bridges]
    return strongest_level(levels) if levels else "LOW"


__all__ = [
    "bridge_level",
    "developer_level",
    "ecosystem_level",
    "graph_confidence_level",
    "repo_level",
    "strongest_level",
    "topic_aggregate_level",
    "topic_rule_level",
]