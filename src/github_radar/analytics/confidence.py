"""Deterministic confidence model: repositories, windows, topics.

Confidence deliberately means something narrow and concrete here: *how much of
the data a metric actually had to work with*. It is not a statistical p-value
and makes no probabilistic claims — it is a deterministic score in ``[0, 1]``
blended from coverage terms, mapped onto a coarse LOW / MEDIUM / HIGH level.
Different consumers favour different histories, so confidence and momentum are
kept as *separate* numbers: a high-momentum score backed by thin history is
possible, but it is loudly labelled.

Everything is anchored on snapshot ``captured_at`` timestamps — never on
wall-clock time — so confidence is reproducible between runs without a new
snapshot.

Constants are module level so they can be tuned and so threshold changes are
(indirectly) regression-tested.

Term weights
============

Repo terminality weights (sum to 1.0 over the three terms below)::

    confidence = count_term + span_term + gap_term

:class:`Confidence` values carry each raw term for explainability.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from github_radar.analytics.metrics import RepoMetrics
from github_radar.analytics.momentum import MomentumScore

ConfidenceLevel = Literal["LOW", "MEDIUM", "HIGH"]

# ---------------------------------------------------------------------------
# Thresholds and constants
# ---------------------------------------------------------------------------

HIGH_CONFIDENCE_THRESHOLD = 0.75
MEDIUM_CONFIDENCE_THRESHOLD = 0.45

MIN_SNAPSHOTS_FOR_FULL_HISTORY = 5  # count term saturates here.

# Weight split (must sum to 1.0).
W_COUNT = 0.34
W_SPAN = 0.33
W_GAP = 0.33

# Topic-level weights (must sum to 1.0), applied to per-repo aggregates.
TOPIC_W_COUNT = 0.50
TOPIC_W_MOMENTUM = 0.30
TOPIC_W_WINDOW = 0.20

TOPIC_MIN_REPOS_FOR_HIGH = 10  # topics smaller than this cannot reach HIGH.


def level_for_score(score: float) -> ConfidenceLevel:
    """Map a confidence score onto the coarse level."""
    if score >= HIGH_CONFIDENCE_THRESHOLD:
        return "HIGH"
    if score >= MEDIUM_CONFIDENCE_THRESHOLD:
        return "MEDIUM"
    return "LOW"


@dataclass(frozen=True)
class Confidence:
    """A numeric confidence score plus the raw signals behind it."""

    level: ConfidenceLevel
    score: float
    snapshot_count: int
    history_days: float
    window_span_days: float | None
    base_gap_days: float | None
    count_term: float
    span_term: float
    gap_term: float


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _snapshot_count_term(count: int) -> float:
    return W_COUNT * _clamp(count / MIN_SNAPSHOTS_FOR_FULL_HISTORY, 0.0, 1.0)


def _span_term(window_span_days: float | None, window_days: int) -> float:
    if window_span_days is None:
        return 0.0
    return W_SPAN * _clamp(window_span_days / window_days, 0.0, 1.0)


def _gap_term(base_gap_days: float | None, window_days: int) -> float:
    if base_gap_days is None:
        return 0.0
    return W_GAP * _clamp(1.0 - abs(base_gap_days) / (window_days / 2.0), 0.0, 1.0)


def _history_days(metrics: RepoMetrics) -> float:
    first = metrics.series.first
    last = metrics.series.last
    if first is None or last is None:
        return 0.0
    return (last.captured_at - first.captured_at).total_seconds() / 86400.0


def repo_window_confidence(
    metrics: RepoMetrics,
    *,
    window_days: int,
) -> Confidence:
    """Confidence in one repository's growth over ``window_days``.

    Snapshot-anchored: the study instant is the newest ``captured_at``; the
    window base is the newest snapshot at-or-before ``study - window_days``.
    A base that is missing or that already covers the whole window contributes
    zero span/gap terms, so thin or skewed history scores LOW.
    """
    study = metrics.latest_captured_at
    if study is None or len(metrics.series) < 2:
        count = len(metrics.series)
        return Confidence(
            level="LOW",
            score=0.0,
            snapshot_count=count,
            history_days=_history_days(metrics),
            window_span_days=None,
            base_gap_days=None,
            count_term=_snapshot_count_term(count),
            span_term=0.0,
            gap_term=0.0,
        )

    target = study - timedelta(days=window_days)
    base = metrics.series.at_or_before(target)

    window_span_days: float | None = None
    base_gap_days: float | None = None
    if base is not None:
        window_span_days = (study - base.captured_at).total_seconds() / 86400.0
        base_gap_days = (target - base.captured_at).total_seconds() / 86400.0

    score = 0.0
    if base is not None:
        score = (
            _snapshot_count_term(len(metrics.series))
            + _span_term(window_span_days, window_days)
            + _gap_term(base_gap_days, window_days)
        )

    return Confidence(
        level=level_for_score(score),
        score=score,
        snapshot_count=len(metrics.series),
        history_days=_history_days(metrics),
        window_span_days=window_span_days,
        base_gap_days=base_gap_days,
        count_term=_snapshot_count_term(len(metrics.series)),
        span_term=_span_term(window_span_days, window_days),
        gap_term=_gap_term(base_gap_days, window_days),
    )


def confidence_level_for_repo_window(
    metrics: RepoMetrics,
    *,
    window_days: int,
) -> ConfidenceLevel:
    """Coarse confidence level for one repository's ``window_days`` growth."""
    return repo_window_confidence(metrics, window_days=window_days).level


def format_confidence(confidence: Confidence) -> str:
    """Render a confidence value as ``LEVEL (0.82)``."""
    return f"{confidence.level} ({confidence.score:.2f})"


# ---------------------------------------------------------------------------
# Repository reports and topic-level confidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepositoryReport:
    """The per-repository analytics digest consumed by topic aggregation."""

    full_name: str
    metrics: RepoMetrics
    momentum: MomentumScore | None

    @property
    def has_latest_stars_window(self) -> bool:
        """Whether a 7-day star window could be computed at all."""
        return self.metrics.stars_7d is not None

    @property
    def has_complete_stars_window(self) -> bool:
        """Whether the 7-day star window is complete (not a fallback base)."""
        delta = self.metrics.stars_7d
        return delta is not None and delta.window.complete

    @property
    def has_partial_stars_window(self) -> bool:
        """Whether the 7-day star window exists but is incomplete."""
        delta = self.metrics.stars_7d
        return delta is not None and not delta.window.complete


def _share(
    reports: Sequence[RepositoryReport],
    predicate: Callable[[RepositoryReport], bool],
) -> float:
    """Fraction of reports satisfying ``predicate`` (0.0 for an empty set)."""
    if not reports:
        return 0.0
    hits = sum(1 for report in reports if predicate(report))
    return hits / len(reports)


@dataclass(frozen=True)
class TopicConfidenceRule:
    """Confidence aggregation for a topic from per-repository reports."""

    repository_count: int
    share_with_momentum: float
    share_complete_windows: float
    share_partial_windows: float
    score: float
    level: ConfidenceLevel

    @classmethod
    def from_reports(
        cls,
        reports: Sequence[RepositoryReport],
        *,
        topic_size_floor: int = TOPIC_MIN_REPOS_FOR_HIGH,
    ) -> TopicConfidenceRule:
        """Aggregate per-repository confidence into a topic-level one."""
        count = len(reports)
        if count == 0:
            return cls(
                repository_count=0,
                share_with_momentum=0.0,
                share_complete_windows=0.0,
                share_partial_windows=0.0,
                score=0.0,
                level="LOW",
            )
        share_with_momentum = _share(
            reports, lambda r: r.momentum is not None
        )
        share_complete_windows = _share(
            reports, lambda r: r.has_complete_stars_window
        )
        share_partial_windows = _share(
            reports, lambda r: r.has_partial_stars_window
        )
        score = (
            TOPIC_W_COUNT * _clamp(count / topic_size_floor, 0.0, 1.0)
            + TOPIC_W_MOMENTUM * share_with_momentum
            + TOPIC_W_WINDOW * share_complete_windows
        )
        level = level_for_score(score)
        if count < topic_size_floor and level == "HIGH":
            level = "MEDIUM"
        return cls(
            repository_count=count,
            share_with_momentum=share_with_momentum,
            share_complete_windows=share_complete_windows,
            share_partial_windows=share_partial_windows,
            score=score,
            level=level,
        )


TopicConfidence = TopicConfidenceRule  # backward-compatible alias


def topic_confidence(
    reports: Sequence[RepositoryReport],
    *,
    topic_size_floor: int = TOPIC_MIN_REPOS_FOR_HIGH,
) -> TopicConfidenceRule:
    """Compute the topic-level confidence rule for ``reports``."""
    return TopicConfidenceRule.from_reports(
        reports,
        topic_size_floor=topic_size_floor,
    )


def confidence_level_for_topic(rule: TopicConfidenceRule) -> ConfidenceLevel:
    """Coarse confidence level for an already-aggregated topic rule."""
    return rule.level


__all__ = [
    "Confidence",
    "ConfidenceLevel",
    "HIGH_CONFIDENCE_THRESHOLD",
    "MEDIUM_CONFIDENCE_THRESHOLD",
    "MIN_SNAPSHOTS_FOR_FULL_HISTORY",
    "RepositoryReport",
    "TOPIC_MIN_REPOS_FOR_HIGH",
    "TopicConfidence",
    "TopicConfidenceRule",
    "W_COUNT",
    "W_GAP",
    "W_SPAN",
    "TOPIC_W_COUNT",
    "TOPIC_W_MOMENTUM",
    "TOPIC_W_WINDOW",
    "confidence_level_for_repo_window",
    "confidence_level_for_topic",
    "format_confidence",
    "level_for_score",
    "repo_window_confidence",
    "topic_confidence",
]