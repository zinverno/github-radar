"""Topic-level analytics: momentum aggregation across repositories.

A topic aggregate is deterministic over the participating repository reports:
count, summed momentum, mean momentum, the share of repositories with a
computable momentum, and the topic-level confidence rule (which cannot reach
``HIGH`` below :data:`~github_radar.analytics.confidence.TOPIC_MIN_REPOS_FOR_HIGH`
repositories).  The aggregation is the pure core behind the ``topics`` CLI; the
CLI only gathers the per-repository reports from storage.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from github_radar.analytics.confidence import (
    ConfidenceLevel,
    RepositoryReport,
    TopicConfidenceRule,
)


@dataclass(frozen=True)
class TopicAggregate:
    """Aggregated momentum signals for one topic."""

    name: str
    repository_count: int
    total_momentum: float
    avg_momentum: float
    share_with_momentum: float
    confidence_score: float
    confidence_level: ConfidenceLevel


def _momentum_total(reports: Sequence[RepositoryReport]) -> float:
    return sum(
        report.momentum.score if report.momentum is not None else 0.0
        for report in reports
    )


def aggregate_topics(
    reports_by_topic: Mapping[str, Sequence[RepositoryReport]],
) -> list[TopicAggregate]:
    """Aggregate per-topic momentum, sorted by total momentum descending.

    Each aggregate carries its topic-level confidence rule
    (:class:`~github_radar.analytics.confidence.TopicConfidenceRule`), so
    ``topics`` and ``topic`` can label how much data a score rests on.  Ties
    are broken alphabetically for determinism.
    """
    aggregates: list[TopicAggregate] = []
    for name, reports in reports_by_topic.items():
        count = len(reports)
        total = _momentum_total(reports)
        averages = total / count if count else 0.0
        shares = (
            sum(1 for r in reports if r.momentum is not None) / count
            if count
            else 0.0
        )
        rule = TopicConfidenceRule.from_reports(reports)
        aggregates.append(
            TopicAggregate(
                name=name,
                repository_count=count,
                total_momentum=total,
                avg_momentum=averages,
                share_with_momentum=shares,
                confidence_score=rule.score,
                confidence_level=rule.level,
            )
        )
    aggregates.sort(key=lambda a: (-a.total_momentum, a.name))
    return aggregates


__all__ = ["TopicAggregate", "aggregate_topics"]