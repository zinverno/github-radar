"""Analytics package: deterministic, snapshot-anchored ecosystem scoring.

Everything here is a pure function of ``RepositorySnapshot`` history plus an
explicit ``reference_now`` — the wall clock is never consulted, so results are
reproducible between runs without a new snapshot.

Public surface (re-exported so callers — the CLI and tests — only need one
import):

Metrics
    RepoMetrics, FieldDelta, TimeWindow, compute_metrics, sorted_snapshots
    Deterministic per-window growth deltas anchored on ``captured_at``.
Momentum
    WEIGHTS, MAX_SCORE, MomentumScore, compute_momentum
    The bounded, explainable momentum score.
Confidence
    Confidence, repo_window_confidence, RepositoryReport,
    TopicConfidenceRule, topic_confidence, level_for_score
    Deterministic data-coverage scores for repositories and topics.
Trends
    Trend, classify_trend, RepoRanking, rank_repositories
    Repository trend classification and momentum ranking.
Topics
    TopicAggregate, aggregate_topics
    Topic-level momentum aggregation.
Time series
    SnapshotSeries, NearestMatch
    Ordered, de-duplicated snapshot lookups.
Recency
    RecencyMetrics, compute_recency, recency_score, days_between
    Deterministic age/staleness relative to ``reference_now``.
"""

from __future__ import annotations

from github_radar.analytics.confidence import (
    HIGH_CONFIDENCE_THRESHOLD,
    MEDIUM_CONFIDENCE_THRESHOLD,
    MIN_SNAPSHOTS_FOR_FULL_HISTORY,
    TOPIC_MIN_REPOS_FOR_HIGH,
    TOPIC_W_COUNT,
    TOPIC_W_MOMENTUM,
    TOPIC_W_WINDOW,
    W_COUNT,
    W_GAP,
    W_SPAN,
    Confidence,
    ConfidenceLevel,
    RepositoryReport,
    TopicConfidence,
    TopicConfidenceRule,
    confidence_level_for_repo_window,
    confidence_level_for_topic,
    format_confidence,
    level_for_score,
    repo_window_confidence,
    topic_confidence,
)
from github_radar.analytics.metrics import (
    DELTA_FIELDS,
    DELTA_WINDOWS_DAYS,
    FieldDelta,
    RepoMetrics,
    TimeWindow,
    compute_metrics,
    sorted_snapshots,
)
from github_radar.analytics.momentum import (
    MAX_GROWTH_PCT,
    MAX_SCORE,
    RECENCY_HALF_LIFE_DAYS,
    WEIGHTS,
    WINDOW_DAYS,
    MomentumScore,
    compute_momentum,
)
from github_radar.analytics.recency import (
    RecencyMetrics,
    compute_recency,
    days_between,
    recency_score,
    utcnow_dt,
)
from github_radar.analytics.timeseries import (
    NearestMatch,
    SnapshotSeries,
)
from github_radar.analytics.topics import (
    TopicAggregate,
    aggregate_topics,
)
from github_radar.analytics.trends import (
    DECLINING_GROWTH_PCT,
    INACTIVE_RECENCY,
    RISING_GROWTH_PCT,
    TREND_MIN_SNAPSHOTS,
    RepoRanking,
    Trend,
    TrendClass,
    classify_trend,
    rank_repositories,
)

__all__ = [
    "Confidence",
    "ConfidenceLevel",
    "DECLINING_GROWTH_PCT",
    "DELTA_FIELDS",
    "DELTA_WINDOWS_DAYS",
    "FieldDelta",
    "HIGH_CONFIDENCE_THRESHOLD",
    "INACTIVE_RECENCY",
    "MAX_GROWTH_PCT",
    "MAX_SCORE",
    "MEDIUM_CONFIDENCE_THRESHOLD",
    "MIN_SNAPSHOTS_FOR_FULL_HISTORY",
    "MomentumScore",
    "NearestMatch",
    "RECENCY_HALF_LIFE_DAYS",
    "RISING_GROWTH_PCT",
    "RecencyMetrics",
    "RepoMetrics",
    "RepoRanking",
    "RepositoryReport",
    "SnapshotSeries",
    "TOPIC_MIN_REPOS_FOR_HIGH",
    "TOPIC_W_COUNT",
    "TOPIC_W_MOMENTUM",
    "TOPIC_W_WINDOW",
    "TREND_MIN_SNAPSHOTS",
    "TimeWindow",
    "TopicAggregate",
    "TopicConfidence",
    "TopicConfidenceRule",
    "Trend",
    "TrendClass",
    "W_COUNT",
    "W_GAP",
    "W_SPAN",
    "WEIGHTS",
    "WINDOW_DAYS",
    "aggregate_topics",
    "classify_trend",
    "compute_metrics",
    "compute_momentum",
    "compute_recency",
    "confidence_level_for_repo_window",
    "confidence_level_for_topic",
    "days_between",
    "format_confidence",
    "level_for_score",
    "rank_repositories",
    "recency_score",
    "repo_window_confidence",
    "sorted_snapshots",
    "topic_confidence",
    "utcnow_dt",
]