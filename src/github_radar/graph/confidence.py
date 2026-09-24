"""Lightweight, deterministic graph-confidence semantics.

Confidence in the graph is the same concrete thing it has been since Phase 2:
a deterministic data-coverage score in ``[0, 1]`` mapped onto the coarse
``LOW`` / ``MEDIUM`` / ``HIGH`` level. It makes no statistical claim. For a
derived relationship or bridge it answers "how much evidence does this rest
on", blending:

* the number of repositories supplying evidence;
* the share of relationships with real observation history;
* recent contribution coverage (how much of the evidence has an observed
  activity window);
* the topic sample size (how many distinct topics support a bridge).

A bridge based on 3 repositories across two topics with observed activity must
score far more strongly than one old cumulative-contribution observation
(which keeps history-coverage and recent-coverage at zero).
"""

from __future__ import annotations

from dataclasses import dataclass

from github_radar.analytics.confidence import ConfidenceLevel, level_for_score

# Terms saturate at these values (module constants for tuning/regression tests).
GUI_REPO_FLOOR = 5  # evidence repositories saturate here.
GUI_HISTORY_WEIGHT = 0.40
GUI_RECENT_WEIGHT = 0.25
GUI_SAMPLE_WEIGHT = 0.15
GUI_REPO_WEIGHT = 0.20

# Below this many distinct evidence repositories a derived relationship is
# never allowed to reach HIGH confidence.
MIN_REPOS_FOR_HIGH = 3


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class GraphConfidence:
    """A data-coverage confidence value plus the signals behind it."""

    score: float
    level: ConfidenceLevel
    distinct_repositories: int
    history_coverage: float
    recent_coverage: float
    sample_size: int
    terms: dict[str, float]
    explanation: str

    @property
    def available(self) -> bool:
        return self.distinct_repositories > 0


def graph_confidence(
    *,
    distinct_repositories: int,
    history_coverage: float,
    recent_coverage: float = 0.0,
    sample_size: int = 0,
) -> GraphConfidence:
    """Data-coverage confidence for a graph-derived relationship.

    ``history_coverage`` and ``recent_coverage`` are fractions in ``[0, 1]``:
    the share of evidence relationships that have observation history and the
    share that have an usable recent-activity window, respectively. Missing
    coverage contributes zero — it is never guessed from what is present.
    """
    distinct = max(0, distinct_repositories)
    repo_term = GUI_REPO_WEIGHT * _clamp(distinct / GUI_REPO_FLOOR)
    history_term = GUI_HISTORY_WEIGHT * _clamp(history_coverage)
    recent_term = GUI_RECENT_WEIGHT * _clamp(recent_coverage)
    sample_term = GUI_SAMPLE_WEIGHT * _clamp(sample_size / 5.0)

    score = repo_term + history_term + recent_term + sample_term
    level = level_for_score(score)
    if level == "HIGH" and distinct < MIN_REPOS_FOR_HIGH:
        level = "MEDIUM"

    if distinct == 0:
        explanation = "No repository evidence; confidence is LOW by definition."
    else:
        explanation = (
            f"{distinct} evidence repositories, history coverage "
            f"{history_coverage:.2f}, recent coverage {recent_coverage:.2f}, "
            f"sample size {sample_size}."
        )
    return GraphConfidence(
        score=score,
        level=level,
        distinct_repositories=distinct,
        history_coverage=history_coverage,
        recent_coverage=recent_coverage,
        sample_size=sample_size,
        terms={
            "repositories": repo_term,
            "history": history_term,
            "recent": recent_term,
            "sample": sample_term,
        },
        explanation=explanation,
    )


__all__ = ["GraphConfidence", "MIN_REPOS_FOR_HIGH", "graph_confidence"]