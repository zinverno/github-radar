"""Developer intelligence: topic relevance, activity, ecosystem score, emerging.

These are deterministic, explainable, snapshot-anchored scores over the raw
historical observations (developer profile snapshots and contributor
observations). Every function takes an explicit ``reference_now`` — the wall
clock is never consulted, so results are reproducible between runs.

Score semantics
===============

* **Topic relevance** — how connected a developer is to the tracked
  repositories of one topic, through owning and contributing.
* **Activity** — how much contribution movement was *observed* inside the
  tracked ecosystem over a window. Cumulative lifetime totals never masquerade
  as activity: only deltas between real observations count.
* **Ecosystem score** — how strongly connected the developer is to tracked
  repositories that presently matter (momentum, trend, ownership, breadth).
* **Emerging** — a label plus score for developers whose tracked-ecosystem
  activity is *increasing*, with small-sample and fame protections.

Confidence is the same concrete thing as Phase 2: a deterministic data-coverage
score in ``[0, 1]`` mapped to a coarse level, never a statistical claim.

Weights, thresholds and dampening scales are module constants so they can be
tuned in one place and their behaviour is regression-tested.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from github_radar.analytics.confidence import (
    ConfidenceLevel,
    level_for_score,
)
from github_radar.analytics.developers import (
    DeveloperProfileMetrics,
    compute_contribution_delta,
)
from github_radar.analytics.momentum import MAX_SCORE as MOMENTUM_MAX
from github_radar.analytics.momentum import MomentumScore
from github_radar.analytics.trends import Trend
from github_radar.domain import ContributorSnapshot

EmergingLabel = Literal[
    "EMERGING",
    "ACTIVE",
    "ESTABLISHED",
    "QUIET",
    "INSUFFICIENT_HISTORY",
]

# ---------------------------------------------------------------------------
# Dampening and floors
# ---------------------------------------------------------------------------

# sqrt-based scale for contribution-delta dampening: a delta of ``ACTIVITY_SCALE``
# contributions maps to 1.0, a delta of 1 to 0.1, so a lone +1 never dominates.
ACTIVITY_SCALE = 100.0

# Topic relevance signals
RELEVANCE_REPO_FLOOR = 5  # repo-count range term saturates here.
SHARE_CAP = 0.25  # per-repo cumulative contribution share is capped at 25%.
OWNED_FLOOR = 3  # ownership term saturates here.

# Activity
ACTIVE_REPOS_FLOOR = 3  # activity confidence saturates here.

# Ecosystem score
ECOSYSTEM_BREADTH_FLOOR = 5

# Emerging
NEWCOMER_DAYS = 30  # first seen within this horizon counts as newcomer.
EMERGING_MOMENTUM = 3.0  # associated repo momentum at/above this counts as growing.
EMERGING_THRESHOLD = 0.30
EMERGING_MIN_CONFIDENCE = 0.30
REQUIRED_EVIDENCE = 2
ACTIVITY_LABEL_THRESHOLD = 0.15
ESTABLISHED_MIN_HISTORY_DAYS = 90.0
ESTABLISHED_MIN_LIFETIME = 200  # cumulative lifetime contributions.

# ---------------------------------------------------------------------------
# Weights (each group sums to 1.0 when every term is available)
# ---------------------------------------------------------------------------

RELEVANCE_WEIGHTS = {
    "repo_range": 0.25,
    "share": 0.30,
    "ownership": 0.20,
    "recent_activity": 0.15,
    "momentum": 0.10,
}

ACTIVITY_WEIGHTS = {
    "observed_delta": 0.50,
    "breadth": 0.30,
    "window_completeness": 0.20,
}

ECOSYSTEM_WEIGHTS = {
    "topic_relevance": 0.25,
    "momentum": 0.25,
    "activity": 0.20,
    "ownership": 0.15,
    "breadth": 0.15,
}

EMERGING_WEIGHTS = {
    "positive_delta": 0.35,
    "growing_repos": 0.25,
    "newcomer": 0.20,
    "follower_growth": 0.10,
    "breadth": 0.10,
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def damped_delta(total: int) -> float:
    """Damped contribution-delta in ``[0, 1]`` (sqrt scale)."""
    return _clamp((max(0.0, float(total)) ** 0.5) / (ACTIVITY_SCALE ** 0.5))


# ---------------------------------------------------------------------------
# Inputs (assembled by the application layer from storage)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoContext:
    """A tracked repository digest used by developer scoring."""

    repository_id: int
    full_name: str
    primary_language: str | None
    owner_developer_id: int | None
    topics: frozenset[str]
    momentum: MomentumScore | None
    trend: Trend | None
    latest_stars: int | None


@dataclass(frozen=True)
class ContributorLink:
    """A developer's latest cumulative link to one tracked repository."""

    repository_id: int
    developer_id: int
    contributions: int
    share: float
    observations: tuple[ContributorSnapshot, ...] = ()


@dataclass(frozen=True)
class DeveloperContext:
    """Per-developer inputs for the scoring functions."""

    developer_id: int
    first_seen_at: datetime
    followers: int | None
    profile_metrics: DeveloperProfileMetrics | None


# ---------------------------------------------------------------------------
# Topic relevance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TopicRelevance:
    """Explainable relevance of one developer to one tracked topic."""

    topic: str
    score: float
    components: dict[str, float]
    confidence_score: float
    confidence_level: ConfidenceLevel
    repo_count: int
    owned_count: int
    contribution_share: float
    recent_contribution_delta: int
    momentum_mean: float
    evidence: tuple[str, ...]


def compute_topic_relevance(
    *,
    developer_id: int,
    topic: str,
    links: Sequence[ContributorLink],
    repos: Mapping[int, RepoContext],
    reference_now: datetime,
    window_days: int = 7,
) -> TopicRelevance | None:
    """Relevance of ``developer_id`` to ``topic``, from owning + contributing.

    The topic must actually contain tracked repositories the developer is
    associated with, or ``None`` is returned. Evidence is per-repository; a
    single huge repository cannot dominate because per-repo contribution share
    is capped (:data:`SHARE_CAP`).
    """
    relevant: dict[int, RepoContext] = {}
    topic_links: list[ContributorLink] = []
    for link in links:
        repo = repos.get(link.repository_id)
        if repo is None or topic not in repo.topics:
            continue
        relevant[repo.repository_id] = repo
        topic_links.append(link)
    # Owned-but-not-contributed topic repos also count, at full ownership.
    if developer_id:
        for repo in repos.values():
            if (
                topic in repo.topics
                and repo.owner_developer_id == developer_id
                and repo.repository_id not in relevant
            ):
                relevant[repo.repository_id] = repo

    if not relevant:
        return None

    repo_count = len(relevant)
    owned_count = sum(
        1
        for repo in relevant.values()
        if repo.owner_developer_id is not None
        and repo.owner_developer_id == developer_id
    )

    range_term = _clamp(repo_count / RELEVANCE_REPO_FLOOR)
    shares = [min(link.share, SHARE_CAP) for link in topic_links]
    share_mean = _mean(shares)
    share_term = share_mean / SHARE_CAP if SHARE_CAP else 0.0
    ownership_term = _clamp(owned_count / OWNED_FLOOR)

    total_delta = 0
    for link in topic_links:
        delta = compute_contribution_delta(
            link.observations,
            reference_now=reference_now,
            window_days=window_days,
        )
        if delta is not None and delta.delta is not None and delta.delta > 0:
            total_delta += delta.delta
    activity_term = damped_delta(total_delta)

    momentum_scores = [
        repo.momentum.score
        for repo in relevant.values()
        if repo.momentum is not None
    ]
    momentum_mean = _mean(momentum_scores)
    momentum_term = momentum_mean / MOMENTUM_MAX if momentum_scores else 0.0

    components = {
        "repo_range": RELEVANCE_WEIGHTS["repo_range"] * range_term,
        "share": RELEVANCE_WEIGHTS["share"] * share_term,
        "ownership": RELEVANCE_WEIGHTS["ownership"] * ownership_term,
        "recent_activity": RELEVANCE_WEIGHTS["recent_activity"] * activity_term,
        "momentum": RELEVANCE_WEIGHTS["momentum"] * momentum_term,
    }
    score = _clamp(sum(components.values()))

    confidence_score = _relevance_confidence(
        repo_count=repo_count,
        links=topic_links,
        span_days=_history_span_days(topic_links),
    )
    return TopicRelevance(
        topic=topic,
        score=score,
        components=components,
        confidence_score=confidence_score,
        confidence_level=level_for_score(confidence_score),
        repo_count=repo_count,
        owned_count=owned_count,
        contribution_share=share_mean,
        recent_contribution_delta=total_delta,
        momentum_mean=momentum_mean,
        evidence=tuple(sorted(repo.full_name for repo in relevant.values())),
    )


def _relevance_confidence(
    *,
    repo_count: int,
    links: Sequence[ContributorLink],
    span_days: float | None,
) -> float:
    """Coverage for one topic-relevance result."""
    with_history = sum(1 for link in links if link.observations)
    sample = _clamp(repo_count / 10.0)
    history_share = with_history / len(links) if links else 0.0
    span = _clamp((span_days or 0.0) / 30.0)
    return _clamp(0.5 * sample + 0.3 * history_share + 0.2 * span)


def _history_span_days(links: Sequence[ContributorLink]) -> float | None:
    spans = [
        span
        for link in links
        if (span := _link_span_days(link)) is not None
    ]
    return max(spans) if spans else None


def _link_span_days(link: ContributorLink) -> float | None:
    if len(link.observations) < 2:
        return None
    first = link.observations[0].captured_at
    last = link.observations[-1].captured_at
    return max(0.0, (last - first).total_seconds() / 86400.0)


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeveloperActivity:
    """Observed contribution movement inside the tracked ecosystem."""

    window_days: int
    available: bool
    score: float
    components: dict[str, float]
    confidence_score: float
    confidence_level: ConfidenceLevel
    total_positive_delta: int
    active_repos: int
    usable_repos: int
    observations: int
    complete_share: float


def compute_activity(
    *,
    links: Sequence[ContributorLink],
    reference_now: datetime,
    window_days: int = 7,
) -> DeveloperActivity | None:
    """Activity from *observed* contribution deltas over the window.

    Returns:
    * ``None`` when the developer has no contributor observation history at all;
    * an ``available=False`` result when history exists but no relationship has
      a usable window (delta cannot be computed) — this is *not* zero activity;
    * an ``available=True`` result with the raw numbers kept visible otherwise.
    """
    if not links:
        return None
    if not any(link.observations for link in links):
        return None

    active_repos = 0
    usable_repos = 0
    complete_repos = 0
    total_positive = 0
    obs_count = 0
    for link in links:
        obs_count += len(link.observations)
        delta = compute_contribution_delta(
            link.observations,
            reference_now=reference_now,
            window_days=window_days,
        )
        if delta is None or delta.delta is None:
            continue
        usable_repos += 1
        if delta.delta > 0:
            active_repos += 1
            total_positive += delta.delta
        if delta.complete:
            complete_repos += 1

    if usable_repos == 0:
        confidence_score = _activity_confidence(
            active_repos=0,
            span_days=_max_span_days(links),
            obs_count=obs_count,
            complete_share=0.0,
        )
        return DeveloperActivity(
            window_days=window_days,
            available=False,
            score=0.0,
            components={
                "observed_delta": 0.0,
                "breadth": 0.0,
                "window_completeness": 0.0,
            },
            confidence_score=confidence_score,
            confidence_level=level_for_score(confidence_score),
            total_positive_delta=0,
            active_repos=0,
            usable_repos=0,
            observations=obs_count,
            complete_share=0.0,
        )

    complete_share = complete_repos / usable_repos
    breadth = active_repos / usable_repos
    components = {
        "observed_delta": ACTIVITY_WEIGHTS["observed_delta"] * damped_delta(total_positive),
        "breadth": ACTIVITY_WEIGHTS["breadth"] * breadth,
        "window_completeness": ACTIVITY_WEIGHTS["window_completeness"] * complete_share,
    }
    score = _clamp(sum(components.values()))

    confidence_score = _activity_confidence(
        active_repos=active_repos,
        span_days=_max_span_days(links),
        obs_count=obs_count,
        complete_share=complete_share,
    )
    return DeveloperActivity(
        window_days=window_days,
        available=True,
        score=score,
        components=components,
        confidence_score=confidence_score,
        confidence_level=level_for_score(confidence_score),
        total_positive_delta=total_positive,
        active_repos=active_repos,
        usable_repos=usable_repos,
        observations=obs_count,
        complete_share=complete_share,
    )


def _max_span_days(links: Sequence[ContributorLink]) -> float:
    spans = [
        span for link in links if (span := _link_span_days(link)) is not None
    ]
    return max(spans) if spans else 0.0


def _activity_confidence(
    *,
    active_repos: int,
    span_days: float,
    obs_count: int,
    complete_share: float,
) -> float:
    return _clamp(
        0.4 * _clamp(active_repos / ACTIVE_REPOS_FLOOR)
        + 0.3 * _clamp(span_days / 30.0)
        + 0.3 * complete_share
    )


# ---------------------------------------------------------------------------
# Ecosystem score
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EcosystemScore:
    """How strongly connected the developer is to currently-mattering repos."""

    score: float
    components: dict[str, float]
    missing_components: tuple[str, ...]
    associated_repositories: int
    owned_repositories: int
    topic_relevance_mean: float
    momentum_mean: float


def compute_ecosystem_score(
    *,
    developer_id: int,
    links: Sequence[ContributorLink],
    repos: Mapping[int, RepoContext],
    reference_now: datetime,
    window_days: int = 7,
) -> EcosystemScore | None:
    """Bounded ecosystem connection score.

    Deterministic per-repository momentum/trend and observed activity feed it;
    absolute popularity (followers, stars) does not.
    """
    assoc_repo_ids = {
        link.repository_id for link in links
    } | {
        rid for rid, repo in repos.items() if repo.owner_developer_id == developer_id
    }
    if not assoc_repo_ids:
        return None
    assoc_repos = [repos[rid] for rid in assoc_repo_ids if rid in repos]
    if not assoc_repos:
        return None
    owned = sum(1 for repo in assoc_repos if repo.owner_developer_id == developer_id)

    topics = {t for repo in assoc_repos for t in repo.topics}
    relevance_scores: list[float] = []
    for topic in sorted(topics):
        relevance = compute_topic_relevance(
            developer_id=developer_id,
            topic=topic,
            links=links,
            repos=repos,
            reference_now=reference_now,
            window_days=window_days,
        )
        if relevance is not None:
            relevance_scores.append(relevance.score)
    relevance_mean = _mean(relevance_scores)

    momentum_scores = [
        repo.momentum.score for repo in assoc_repos if repo.momentum is not None
    ]
    momentum_mean = _mean(momentum_scores)
    momentum_term = momentum_mean / MOMENTUM_MAX if momentum_scores else 0.0

    activity = compute_activity(
        links=links,
        reference_now=reference_now,
        window_days=window_days,
    )
    activity_score = activity.score if activity is not None and activity.available else 0.0

    ownership_term = _clamp(owned / OWNED_FLOOR)
    breadth_term = _clamp(len(assoc_repos) / ECOSYSTEM_BREADTH_FLOOR)

    missing = [
        name
        for name, present in (
            ("momentum", bool(momentum_scores)),
            ("activity", activity is not None and activity.available),
            ("topic_relevance", bool(relevance_scores)),
        )
        if not present
    ]

    components = {
        "topic_relevance": ECOSYSTEM_WEIGHTS["topic_relevance"] * relevance_mean,
        "momentum": ECOSYSTEM_WEIGHTS["momentum"] * momentum_term,
        "activity": ECOSYSTEM_WEIGHTS["activity"] * activity_score,
        "ownership": ECOSYSTEM_WEIGHTS["ownership"] * ownership_term,
        "breadth": ECOSYSTEM_WEIGHTS["breadth"] * breadth_term,
    }
    return EcosystemScore(
        score=_clamp(sum(components.values())),
        components=components,
        missing_components=tuple(missing),
        associated_repositories=len(assoc_repos),
        owned_repositories=owned,
        topic_relevance_mean=relevance_mean,
        momentum_mean=momentum_mean,
    )


# ---------------------------------------------------------------------------
# Emerging
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmergingDeveloper:
    """Deterministic emerging classification plus its raw signals."""

    label: EmergingLabel
    score: float
    components: dict[str, float]
    raw_signals: dict[str, int | float | bool]
    confidence_score: float
    confidence_level: ConfidenceLevel
    evidence_points: int
    explanation: str


def compute_emerging(
    *,
    developer: DeveloperContext,
    links: Sequence[ContributorLink],
    repos: Mapping[int, RepoContext],
    reference_now: datetime,
    window_days: int = 7,
) -> EmergingDeveloper:
    """Classify a developer within the tracked ecosystem.

    Emerging is *activity that is increasing*, not lifetime output and not
    fame. Protections: a single +1 contribution saturates the delta term only
    slightly (sqrt dampening) and cannot reach :data:`REQUIRED_EVIDENCE`
    evidence points; absolute follower counts play no role (only observed
    follower *growth* does); developers with only a weak observation get
    ``INSUFFICIENT_HISTORY``.
    """
    has_history = bool(links) and any(link.observations for link in links)
    if not has_history:
        return EmergingDeveloper(
            label="INSUFFICIENT_HISTORY",
            score=0.0,
            components={},
            raw_signals={},
            confidence_score=0.0,
            confidence_level="LOW",
            evidence_points=0,
            explanation=(
                "No contributor observation history; activity cannot be "
                "measured."
            ),
        )

    activity = compute_activity(
        links=links,
        reference_now=reference_now,
        window_days=window_days,
    )
    obs_count = sum(len(link.observations) for link in links)
    span_days = _max_span_days(links)

    confidence_score = _activity_confidence(
        active_repos=activity.active_repos if activity is not None else 0,
        span_days=span_days,
        obs_count=obs_count,
        complete_share=activity.complete_share if activity is not None else 0.0,
    )

    total_positive = activity.total_positive_delta if activity is not None else 0
    active_repos = activity.active_repos if activity is not None else 0

    assoc_ids = {
        link.repository_id for link in links
    } | {
        rid
        for rid, repo in repos.items()
        if repo.owner_developer_id == developer.developer_id
    }
    assoc_repos = [repos[rid] for rid in assoc_ids if rid in repos]

    growing_repos = sum(
        1
        for repo in assoc_repos
        if (repo.momentum is not None and repo.momentum.score >= EMERGING_MOMENTUM)
        or (repo.trend is not None and repo.trend.trend == "rising")
    )
    growing_share = growing_repos / len(assoc_repos) if assoc_repos else 0.0

    is_newcomer = (
        reference_now - developer.first_seen_at
    ).total_seconds() / 86400.0 <= NEWCOMER_DAYS

    follower_growth_pct = _follower_growth_pct(developer.profile_metrics, window_days)
    has_follower_growth = follower_growth_pct is not None and follower_growth_pct > 0
    follower_term = 0.0
    if has_follower_growth and follower_growth_pct is not None:
        follower_term = (max(0.0, float(follower_growth_pct)) / 100.0) ** 0.5

    breadth_term = _clamp(active_repos / 2.0)

    raw_signals: dict[str, int | float | bool] = {
        "total_positive_delta": total_positive,
        "active_repos": active_repos,
        "growing_repos": growing_repos,
        "associated_repos": len(assoc_repos),
        "newcomer": is_newcomer,
        "follower_growth_pct": follower_growth_pct if follower_growth_pct is not None else 0.0,
        "observations": obs_count,
    }

    components = {
        "positive_delta": EMERGING_WEIGHTS["positive_delta"] * damped_delta(total_positive),
        "growing_repos": EMERGING_WEIGHTS["growing_repos"] * _clamp(growing_share),
        "newcomer": EMERGING_WEIGHTS["newcomer"] * (1.0 if is_newcomer else 0.0),
        "follower_growth": EMERGING_WEIGHTS["follower_growth"] * follower_term,
        "breadth": EMERGING_WEIGHTS["breadth"] * breadth_term,
    }
    score = _clamp(sum(components.values()))

    evidence_points = active_repos
    if is_newcomer:
        evidence_points += 1
    if has_follower_growth:
        evidence_points += 1

    label, explanation = _classify_emerging(
        score=score,
        confidence=confidence_score,
        evidence_points=evidence_points,
        has_positive_delta=total_positive > 0,
        activity=activity,
        span_days=span_days,
        lifetime=sum(link.contributions for link in links),
    )

    return EmergingDeveloper(
        label=label,
        score=score,
        components=components,
        raw_signals=raw_signals,
        confidence_score=confidence_score,
        confidence_level=level_for_score(confidence_score),
        evidence_points=evidence_points,
        explanation=explanation,
    )


def _follower_growth_pct(
    profile_metrics: DeveloperProfileMetrics | None,
    window_days: int,
) -> float | None:
    if profile_metrics is None:
        return None
    delta = profile_metrics.followers_delta(window_days)
    if delta is None or delta.delta is None or delta.base_value is None:
        return None
    if delta.base_value <= 0:
        return None
    return delta.delta / delta.base_value * 100.0


def _classify_emerging(
    *,
    score: float,
    confidence: float,
    evidence_points: int,
    has_positive_delta: bool,
    activity: DeveloperActivity | None,
    span_days: float,
    lifetime: int,
) -> tuple[EmergingLabel, str]:
    if confidence < EMERGING_MIN_CONFIDENCE:
        return (
            "INSUFFICIENT_HISTORY",
            f"Confidence {confidence:.2f} is below {EMERGING_MIN_CONFIDENCE:.2f}; "
            "history is too thin to label.",
        )
    if score >= EMERGING_THRESHOLD and evidence_points >= REQUIRED_EVIDENCE:
        if not has_positive_delta:
            return (
                "ACTIVE",
                "Score is high but relies on non-contribution signals; no "
                "observed contribution delta.",
            )
        return (
            "EMERGING",
            f"Score {score:.2f} at/above {EMERGING_THRESHOLD:.2f} with "
            f"{evidence_points} evidence points; activity is increasing.",
        )
    if activity is not None and activity.available and score >= ACTIVITY_LABEL_THRESHOLD:
        return (
            "ACTIVE",
            f"Observed contribution activity (score {score:.2f}) without "
            "meeting the emerging criteria.",
        )
    if span_days >= ESTABLISHED_MIN_HISTORY_DAYS or lifetime >= ESTABLISHED_MIN_LIFETIME:
        return (
            "ESTABLISHED",
            "Long observation history or high lifetime cumulative output; not "
            "rising fast enough to be emerging.",
        )
    return (
        "QUIET",
        "Association history exists but no recent positive contribution "
        "movement was observed.",
    )


__all__ = [
    "ACTIVITY_LABEL_THRESHOLD",
    "ACTIVITY_SCALE",
    "ACTIVITY_WEIGHTS",
    "ContributorLink",
    "DeveloperActivity",
    "DeveloperContext",
    "ECOSYSTEM_BREADTH_FLOOR",
    "ECOSYSTEM_WEIGHTS",
    "EMERGING_MIN_CONFIDENCE",
    "EMERGING_MOMENTUM",
    "EMERGING_THRESHOLD",
    "EMERGING_WEIGHTS",
    "EmergingDeveloper",
    "EmergingLabel",
    "EcosystemScore",
    "NEWCOMER_DAYS",
    "OWNED_FLOOR",
    "RELEVANCE_REPO_FLOOR",
    "RELEVANCE_WEIGHTS",
    "REQUIRED_EVIDENCE",
    "RepoContext",
    "SHARE_CAP",
    "TopicRelevance",
    "compute_activity",
    "compute_ecosystem_score",
    "compute_emerging",
    "compute_topic_relevance",
    "damped_delta",
]