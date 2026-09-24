"""Bridge intelligence: developers and repositories connecting ecosystems.

A *bridge* is an explainable, bounded score describing how strongly a developer
or repository connects multiple tracked topic ecosystems. It is not a ranking
of popularity: followers never enter the score, and every result exposes its
component breakdown, its topic/repository evidence, and a data-coverage
confidence.

Protections (by construction):

* A developer linked to many topics only because one repository carries dozens
  of tags is held down by the ``repo_span`` term (single-repository diversity
  scores 0 there) and by a small-sample cap when the score rests on one or two
  repositories.
* A developer with one tiny contribution cannot score highly: total
  contributions below :data:`BRIDGE_MIN_LIFETIME` trigger the same cap.
* Followers/popularity are never inputs.
* A bridge requires evidence in *both* topics for a cross-topic pair — a
  developer with no repository in the other ecosystem never appears.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from github_radar.analytics.intelligence import SHARE_CAP, damped_delta
from github_radar.analytics.momentum import MAX_SCORE as MOMENTUM_MAX
from github_radar.graph.confidence import GraphConfidence, graph_confidence
from github_radar.graph.model import EcosystemGraph
from github_radar.graph.relationships import developers_for_topic
from github_radar.graph.scoring import clamp, mean, recent_activity

# Developer bridge weights (sum to 1.0 when every term is available).
BRIDGE_WEIGHTS = {
    "topics": 0.30,           # number of topic memberships (saturates here).
    "topic_strength": 0.25,   # mean per-topic evidence strength.
    "repo_span": 0.20,        # distinct repositories behind the memberships.
    "distribution": 0.10,     # evenness across memberships.
    "recent_activity": 0.10,  # observed positive contribution movement.
    "momentum": 0.05,         # momentum of the supporting repositories.
}

# Cross-topic pair bridge weights (sum to 1.0).
PAIR_BRIDGE_WEIGHTS = {
    "relevance": 0.45,    # mean topic evidence strength (both topics).
    "repo_span": 0.30,    # distinct repositories per side.
    "recent_activity": 0.25,  # observed positive movement in both ecosystems.
}

# Repository bridge weights (sum to 1.0).
REPO_BRIDGE_WEIGHTS = {
    "contributors": 0.30,      # contributor diversity (log-dampened).
    "topics": 0.20,            # topic breadth (log-dampened, saturates early).
    "cross_repo_share": 0.25,  # contributors also active in other repos.
    "cross_topic_share": 0.15, # contributors who are multi-topic themselves.
    "momentum": 0.10,
}

# Saturation floors.
BRIDGE_TOPIC_FLOOR = 4            # memberships saturate here.
BRIDGE_TOPIC_REPO_FLOOR = 2       # per-topic repositories saturate here.
BRIDGE_REPO_SPAN_FLOOR = 3        # distinct supporting repositories saturate here.
BRIDGE_PAIR_REPO_FLOOR = 2        # per-side repositories saturate here.
REPO_BRIDGE_CONTRIB_FLOOR = 20    # contributor count saturates here.
REPO_BRIDGE_TOPIC_FLOOR = 10      # repository topic count saturates here.

# Small-sample protections.
BRIDGE_MIN_DISTINCT_REPOS = 2     # below this, the score is hard-capped.
BRIDGE_MIN_TOPICS = 2             # a bridge needs at least two topics.
BRIDGE_MIN_LIFETIME = 10          # total contributions across supporting repos.
SMALL_SAMPLE_CAP = 0.35           # score ceiling under small-sample evidence.

# Per-topic evidence weights inside one topic's strength.
_TOPIC_STRENGTH_WEIGHTS = {
    "repo_range": 0.60,
    "share": 0.40,
}


@dataclass(frozen=True)
class TopicBridgeEvidence:
    """Why a developer counts as present in one topic's ecosystem."""

    topic: str
    repository_count: int
    repositories: tuple[str, ...]
    topic_strength: float
    total_contributions: int
    recent_delta: int | None
    recent_activity_available: bool
    history_coverage: float


@dataclass(frozen=True)
class DeveloperBridge:
    """Explainable bridge model for one developer across topic ecosystems."""

    developer_id: int
    login: str
    bridge_score: float
    components: dict[str, float]
    missing_components: tuple[str, ...]
    meaningful_topic_memberships: int
    distinct_supporting_repositories: int
    small_sample: bool
    topic_evidence: tuple[TopicBridgeEvidence, ...]
    repository_evidence: tuple[str, ...]
    confidence: GraphConfidence


@dataclass(frozen=True)
class CrossTopicBridge:
    """A developer with evidence in both ecosystems of a topic pair."""

    developer_id: int
    login: str
    topic_a: TopicBridgeEvidence
    topic_b: TopicBridgeEvidence
    shared_tracked_repositories: tuple[str, ...]
    bridge_score: float
    components: dict[str, float]
    missing_components: tuple[str, ...]
    small_sample: bool
    confidence: GraphConfidence


@dataclass(frozen=True)
class RepositoryBridge:
    """Explainable bridge model for one repository across ecosystems."""

    repository_id: int
    full_name: str
    bridge_score: float
    components: dict[str, float]
    missing_components: tuple[str, ...]
    contributor_count: int
    topic_count: int
    cross_repository_contributors: int
    cross_topic_contributors: int
    small_sample: bool
    confidence: GraphConfidence


# ---------------------------------------------------------------------------
# Per-topic evidence
# ---------------------------------------------------------------------------


def _topic_evidence(
    graph: EcosystemGraph,
    developer_id: int,
    topic_name: str,
    topic_id: int,
    *,
    reference_now: datetime,
    window_days: int,
) -> TopicBridgeEvidence | None:
    """Evidence for one developer in one topic, or ``None`` when absent."""
    repo_ids = graph.repositories_for_topic(topic_id)
    supporting: list[int] = []
    contributions = 0
    shares: list[float] = []
    repository_names: list[str] = []
    recent_delta = 0
    recent_available = False
    with_history = 0
    contributed_repos = 0

    for repository_id in sorted(repo_ids):
        evidence = graph.contribution_evidence(developer_id, repository_id)
        owner = graph.owner_of(repository_id)
        if evidence is None and owner != developer_id:
            continue
        node = graph.repository(repository_id)
        if node is not None:
            repository_names.append(node.full_name)
        supporting.append(repository_id)
        if evidence is not None:
            contributed_repos += 1
            contributions += evidence.contributions
            shares.append(clamp(evidence.share, 0.0, SHARE_CAP))
            if evidence.observations:
                with_history += 1
            activity = recent_activity(
                evidence, reference_now=reference_now, window_days=window_days
            )
            if activity.available and activity.delta is not None:
                recent_available = True
                if activity.delta > 0:
                    recent_delta += activity.delta

    if not supporting:
        return None

    history_coverage = with_history / contributed_repos if contributed_repos else 0.0

    repo_term = clamp(len(supporting) / BRIDGE_TOPIC_REPO_FLOOR)
    share_term = (
        clamp(mean(shares) / SHARE_CAP) if shares else 0.0
    )
    strength = clamp(
        _TOPIC_STRENGTH_WEIGHTS["repo_range"] * repo_term
        + _TOPIC_STRENGTH_WEIGHTS["share"] * share_term
    )

    return TopicBridgeEvidence(
        topic=topic_name,
        repository_count=len(supporting),
        repositories=tuple(sorted(repository_names)),
        topic_strength=strength,
        total_contributions=contributions,
        recent_delta=recent_delta if recent_available else None,
        recent_activity_available=recent_available,
        history_coverage=history_coverage,
    )


def _developer_memberships(
    graph: EcosystemGraph,
    developer_id: int,
    *,
    reference_now: datetime,
    window_days: int,
) -> tuple[TopicBridgeEvidence, ...]:
    """Every topic the developer has real repository evidence in."""
    memberships: list[TopicBridgeEvidence] = []
    for repository_id in sorted(graph.associated_repositories(developer_id)):
        for topic_id in sorted(graph.topics_for_repository(repository_id)):
            topic = graph.topic(topic_id)
            if topic is None:
                continue
            evidence = _topic_evidence(
                graph,
                developer_id,
                topic.name,
                topic_id,
                reference_now=reference_now,
                window_days=window_days,
            )
            if evidence is not None and all(
                existing.topic != evidence.topic for existing in memberships
            ):
                memberships.append(evidence)
    memberships.sort(key=lambda item: item.topic)
    return tuple(memberships)


def _distribution_term(repo_counts: list[int]) -> float:
    """Shannon evenness of repository counts across memberships, in ``[0, 1]``.

    ``0.0`` for a single membership (there is nothing to distribute across).
    """
    total = sum(repo_counts)
    if len(repo_counts) < 2 or total <= 0:
        return 0.0
    from math import log

    entropy = -sum(
        (count / total) * log(count / total) for count in repo_counts if count > 0
    )
    return clamp(entropy / log(len(repo_counts)))


def compute_developer_bridge(
    graph: EcosystemGraph,
    developer_id: int,
    *,
    reference_now: datetime,
    window_days: int,
) -> DeveloperBridge:
    """Bridge score for one developer, with components and evidence.

    The score is a bounded weighted sum of explainable terms. Two gates keep it
    honest: it is forced to ``0.0`` below :data:`BRIDGE_MIN_TOPICS` memberships
    (one topic cannot bridge ecosystems), and it is hard-capped at
    :data:`SMALL_SAMPLE_CAP` when the supporting evidence is thin (few
    repositories or a trivial total contribution count). The cap and the
    ``small_sample`` flag are always reported.
    """
    node = graph.developer(developer_id)
    login = node.login if node is not None else str(developer_id)
    memberships = _developer_memberships(
        graph,
        developer_id,
        reference_now=reference_now,
        window_days=window_days,
    )

    supporting_repos = {
        repository_id
        for repository_id in graph.associated_repositories(developer_id)
        if graph.topics_for_repository(repository_id)
    }
    distinct_supporting = len(supporting_repos)
    total_contributions = sum(
        evidence.total_contributions for evidence in memberships
    )

    if not memberships:
        return DeveloperBridge(
            developer_id=developer_id,
            login=login,
            bridge_score=0.0,
            components={},
            missing_components=("topics",),
            meaningful_topic_memberships=0,
            distinct_supporting_repositories=distinct_supporting,
            small_sample=distinct_supporting < BRIDGE_MIN_DISTINCT_REPOS,
            topic_evidence=(),
            repository_evidence=(),
            confidence=graph_confidence(
                distinct_repositories=0,
                history_coverage=0.0,
                recent_coverage=0.0,
                sample_size=0,
            ),
        )

    topic_term = clamp(len(memberships) / BRIDGE_TOPIC_FLOOR)
    strengths = [evidence.topic_strength for evidence in memberships]
    strength_term = mean(strengths)
    repo_span_term = clamp(
        (distinct_supporting - 1) / max(1, BRIDGE_REPO_SPAN_FLOOR - 1)
    )
    distribution_term = _distribution_term(
        [evidence.repository_count for evidence in memberships]
    )

    positive_deltas = [
        evidence.recent_delta
        for evidence in memberships
        if evidence.recent_activity_available and evidence.recent_delta is not None
    ]
    if positive_deltas:
        recent_term = damped_delta(sum(positive_deltas))
        recent_missing = False
    else:
        recent_term = 0.0
        recent_missing = True

    momentum_scores: list[float] = []
    for repository_id in supporting_repos:
        repo_node = graph.repository(repository_id)
        if repo_node is not None and repo_node.momentum is not None:
            momentum_scores.append(repo_node.momentum.score)
    if momentum_scores:
        momentum_term = clamp(mean(momentum_scores) / MOMENTUM_MAX)
        momentum_missing = False
    else:
        momentum_term = 0.0
        momentum_missing = True

    components = {
        "topics": BRIDGE_WEIGHTS["topics"] * topic_term,
        "topic_strength": BRIDGE_WEIGHTS["topic_strength"] * strength_term,
        "repo_span": BRIDGE_WEIGHTS["repo_span"] * repo_span_term,
        "distribution": BRIDGE_WEIGHTS["distribution"] * distribution_term,
        "recent_activity": BRIDGE_WEIGHTS["recent_activity"] * recent_term,
        "momentum": BRIDGE_WEIGHTS["momentum"] * momentum_term,
    }
    missing = tuple(
        name
        for name, present in (
            ("recent_activity", not recent_missing),
            ("momentum", not momentum_missing),
        )
        if not present
    )

    raw_score = clamp(sum(components.values()))
    bridge_eligible = len(memberships) >= BRIDGE_MIN_TOPICS
    score = raw_score if bridge_eligible else 0.0

    small_sample = (
        distinct_supporting < BRIDGE_MIN_DISTINCT_REPOS
        or total_contributions < BRIDGE_MIN_LIFETIME
    )
    if small_sample:
        score = min(score, SMALL_SAMPLE_CAP)

    with_history = 0
    with_recent = 0
    for repository_id in supporting_repos:
        evidence = graph.contribution_evidence(developer_id, repository_id)
        if evidence is None:
            continue
        if evidence.observations:
            with_history += 1
        activity = recent_activity(
            evidence, reference_now=reference_now, window_days=window_days
        )
        if activity.available:
            with_recent += 1

    repository_names: list[str] = []
    for repository_id in supporting_repos:
        repo_node = graph.repository(repository_id)
        if repo_node is not None:
            repository_names.append(repo_node.full_name)
    repository_names.sort()

    return DeveloperBridge(
        developer_id=developer_id,
        login=login,
        bridge_score=score,
        components=components,
        missing_components=missing,
        meaningful_topic_memberships=len(memberships),
        distinct_supporting_repositories=distinct_supporting,
        small_sample=small_sample,
        topic_evidence=memberships,
        repository_evidence=tuple(repository_names),
        confidence=graph_confidence(
            distinct_repositories=len(supporting_repos),
            history_coverage=(
                with_history / len(supporting_repos) if supporting_repos else 0.0
            ),
            recent_coverage=(
                with_recent / len(supporting_repos) if supporting_repos else 0.0
            ),
            sample_size=len(memberships),
        ),
    )


def developer_bridges(
    graph: EcosystemGraph,
    *,
    reference_now: datetime,
    window_days: int,
    topic: str | None = None,
    limit: int | None = None,
    min_confidence: float | None = None,
) -> tuple[DeveloperBridge, ...]:
    """Bridge scores for developers, ranked deterministically.

    With ``topic`` set, only developers with evidence in that topic's ecosystem
    are scored (still bridging *across* their full topic set).
    """
    wanted_topic_id: int | None = None
    if topic is not None:
        topic_node = graph.topic_by_name(topic)
        if topic_node is None:
            return ()
        wanted_topic_id = topic_node.topic_id

    candidates = list(graph.developer_ids)
    if wanted_topic_id is not None:
        candidates = sorted(developers_for_topic(graph, wanted_topic_id))

    bridges: list[DeveloperBridge] = []
    for developer_id in candidates:
        bridge = compute_developer_bridge(
            graph,
            developer_id,
            reference_now=reference_now,
            window_days=window_days,
        )
        if min_confidence is not None and bridge.confidence.score < min_confidence:
            continue
        bridges.append(bridge)
    bridges.sort(key=lambda item: (-item.bridge_score, item.login))
    if limit is not None:
        bridges = bridges[:limit]
    return tuple(bridges)


# ---------------------------------------------------------------------------
# Cross-topic bridge (a specific pair)
# ---------------------------------------------------------------------------


def cross_topic_bridges(
    graph: EcosystemGraph,
    topic_a: str,
    topic_b: str,
    *,
    reference_now: datetime,
    window_days: int,
    limit: int | None = None,
    min_confidence: float | None = None,
) -> tuple[CrossTopicBridge, ...]:
    """Developers with evidence in **both** ecosystems of a topic pair.

    A developer qualifies when they own or contribute to at least one tracked
    repository in each topic. The repositories need not overlap: contributing
    to repo A (tagged ``topic_a``) and repo B (tagged ``topic_b``) is valid
    cross-topic bridge evidence.
    """
    node_a = graph.topic_by_name(topic_a)
    node_b = graph.topic_by_name(topic_b)
    if node_a is None or node_b is None or node_a.topic_id == node_b.topic_id:
        return ()

    devs_a = developers_for_topic(graph, node_a.topic_id)
    devs_b = developers_for_topic(graph, node_b.topic_id)
    candidates = sorted(devs_a & devs_b)

    bridges: list[CrossTopicBridge] = []
    for developer_id in candidates:
        evidence_a = _topic_evidence(
            graph,
            developer_id,
            node_a.name,
            node_a.topic_id,
            reference_now=reference_now,
            window_days=window_days,
        )
        evidence_b = _topic_evidence(
            graph,
            developer_id,
            node_b.name,
            node_b.topic_id,
            reference_now=reference_now,
            window_days=window_days,
        )
        if evidence_a is None or evidence_b is None:
            continue

        shared = sorted(
            set(evidence_a.repositories) & set(evidence_b.repositories)
        )
        total_contributions = (
            evidence_a.total_contributions + evidence_b.total_contributions
        )

        relevance_term = clamp((evidence_a.topic_strength + evidence_b.topic_strength) / 2)
        span_term = clamp(
            min(evidence_a.repository_count, evidence_b.repository_count)
            / BRIDGE_PAIR_REPO_FLOOR
        )

        deltas = [
            evidence.recent_delta
            for evidence in (evidence_a, evidence_b)
            if evidence.recent_activity_available and evidence.recent_delta is not None
        ]
        if deltas:
            recent_term = damped_delta(sum(deltas))
            recent_missing = False
        else:
            recent_term = 0.0
            recent_missing = True

        components = {
            "relevance": PAIR_BRIDGE_WEIGHTS["relevance"] * relevance_term,
            "repo_span": PAIR_BRIDGE_WEIGHTS["repo_span"] * span_term,
            "recent_activity": PAIR_BRIDGE_WEIGHTS["recent_activity"] * recent_term,
        }
        missing = ("recent_activity",) if recent_missing else ()

        raw_score = clamp(sum(components.values()))
        small_sample = total_contributions < BRIDGE_MIN_LIFETIME
        score = min(raw_score, SMALL_SAMPLE_CAP) if small_sample else raw_score

        node = graph.developer(developer_id)
        distinct = len(set(evidence_a.repositories) | set(evidence_b.repositories))
        confidence = graph_confidence(
            distinct_repositories=distinct,
            history_coverage=(evidence_a.history_coverage + evidence_b.history_coverage) / 2,
            recent_coverage=(
                sum(
                    1
                    for evidence in (evidence_a, evidence_b)
                    if evidence.recent_activity_available
                )
                / 2
            ),
            sample_size=2,
        )

        bridges.append(
            CrossTopicBridge(
                developer_id=developer_id,
                login=node.login if node is not None else str(developer_id),
                topic_a=evidence_a,
                topic_b=evidence_b,
                shared_tracked_repositories=tuple(shared),
                bridge_score=score,
                components=components,
                missing_components=missing,
                small_sample=small_sample,
                confidence=confidence,
            )
        )

    bridges.sort(key=lambda item: (-item.bridge_score, item.login))
    if min_confidence is not None:
        bridges = [
            item for item in bridges if item.confidence.score >= min_confidence
        ]
    if limit is not None:
        bridges = bridges[:limit]
    return tuple(bridges)


# ---------------------------------------------------------------------------
# Repository bridge
# ---------------------------------------------------------------------------


def compute_repository_bridge(
    graph: EcosystemGraph,
    repository_id: int,
    *,
    reference_now: datetime,
    window_days: int,
) -> RepositoryBridge:
    """Bridge score for one repository across topics and developer groups.

    Topic breadth is log-dampened so a repository with many arbitrary tags
    cannot win on tag count alone; contributor-derived terms (contributors who
    are themselves active elsewhere or multi-topic) carry most of the weight.
    """
    node = graph.repository(repository_id)
    full_name = node.full_name if node is not None else str(repository_id)
    if node is None:
        return RepositoryBridge(
            repository_id=repository_id,
            full_name=full_name,
            bridge_score=0.0,
            components={},
            missing_components=(),
            contributor_count=0,
            topic_count=0,
            cross_repository_contributors=0,
            cross_topic_contributors=0,
            small_sample=True,
            confidence=graph_confidence(
                distinct_repositories=0,
                history_coverage=0.0,
                recent_coverage=0.0,
            ),
        )

    contributors = sorted(graph.associated_developers(repository_id))
    topic_ids = sorted(graph.topics_for_repository(repository_id))
    contributor_count = len(contributors)
    topic_count = len(topic_ids)

    if contributor_count:
        cross_repo = sum(
            1
            for developer_id in contributors
            if len(graph.associated_repositories(developer_id)) > 1
        )
        cross_topic = 0
        for developer_id in contributors:
            memberships = {
                topic_id
                for repo_id in graph.associated_repositories(developer_id)
                for topic_id in graph.topics_for_repository(repo_id)
            }
            if len(memberships) >= 2:
                cross_topic += 1
        cross_repo_share = cross_repo / contributor_count
        cross_topic_share = cross_topic / contributor_count
    else:
        cross_repo = 0
        cross_topic = 0
        cross_repo_share = 0.0
        cross_topic_share = 0.0

    contributor_term = clamp(
        _log_dampened(contributor_count, REPO_BRIDGE_CONTRIB_FLOOR)
    )
    topic_term = clamp(_log_dampened(topic_count, REPO_BRIDGE_TOPIC_FLOOR))

    momentum_scores = [node.momentum.score] if node.momentum is not None else []
    if momentum_scores:
        momentum_term = clamp(mean(momentum_scores) / MOMENTUM_MAX)
        momentum_missing = False
    else:
        momentum_term = 0.0
        momentum_missing = True

    # With no contributors, the cross-contributor terms are *missing* evidence,
    # not a measured zero — they are reported as missing, never fabricated.
    contributor_terms = (
        REPO_BRIDGE_WEIGHTS["cross_repo_share"] * cross_repo_share,
        REPO_BRIDGE_WEIGHTS["cross_topic_share"] * cross_topic_share,
    ) if contributor_count else (0.0, 0.0)

    components = {
        "contributors": REPO_BRIDGE_WEIGHTS["contributors"] * contributor_term,
        "topics": REPO_BRIDGE_WEIGHTS["topics"] * topic_term,
        "cross_repo_share": contributor_terms[0],
        "cross_topic_share": contributor_terms[1],
        "momentum": REPO_BRIDGE_WEIGHTS["momentum"] * momentum_term,
    }
    missing_parts = []
    if contributor_count == 0:
        missing_parts += ["cross_repo_share", "cross_topic_share"]
    if momentum_missing:
        missing_parts.append("momentum")
    missing = tuple(missing_parts)

    raw_score = clamp(sum(components.values()))
    small_sample = contributor_count < BRIDGE_MIN_DISTINCT_REPOS
    score = min(raw_score, SMALL_SAMPLE_CAP) if small_sample else raw_score

    with_history = 0
    with_recent = 0
    for developer_id in contributors:
        evidence = graph.contribution_evidence(developer_id, repository_id)
        if evidence is None:
            continue
        if evidence.observations:
            with_history += 1
        activity = recent_activity(
            evidence, reference_now=reference_now, window_days=window_days
        )
        if activity.available:
            with_recent += 1

    distinct_connected = len(
        {
            repo_id
            for developer_id in contributors
            for repo_id in graph.associated_repositories(developer_id)
            if repo_id != repository_id
        }
    )

    return RepositoryBridge(
        repository_id=repository_id,
        full_name=full_name,
        bridge_score=score,
        components=components,
        missing_components=missing,
        contributor_count=contributor_count,
        topic_count=topic_count,
        cross_repository_contributors=cross_repo,
        cross_topic_contributors=cross_topic,
        small_sample=small_sample,
        confidence=graph_confidence(
            distinct_repositories=max(1, distinct_connected),
            history_coverage=(
                with_history / contributor_count if contributor_count else 0.0
            ),
            recent_coverage=(
                with_recent / contributor_count if contributor_count else 0.0
            ),
            sample_size=topic_count,
        ),
    )


def repository_bridges(
    graph: EcosystemGraph,
    *,
    reference_now: datetime,
    window_days: int,
    limit: int | None = None,
    min_confidence: float | None = None,
) -> tuple[RepositoryBridge, ...]:
    """Repository bridge scores for every tracked repository, ranked."""
    bridges: list[RepositoryBridge] = []
    for repository_id in graph.repository_ids:
        bridge = compute_repository_bridge(
            graph,
            repository_id,
            reference_now=reference_now,
            window_days=window_days,
        )
        if min_confidence is not None and bridge.confidence.score < min_confidence:
            continue
        bridges.append(bridge)
    bridges.sort(key=lambda item: (-item.bridge_score, item.full_name))
    if limit is not None:
        bridges = bridges[:limit]
    return tuple(bridges)


def _log_dampened(value: int, floor: int) -> float:
    from math import log1p

    if floor <= 0:
        return 1.0
    return log1p(max(0, value)) / log1p(floor)


__all__ = [
    "BRIDGE_MIN_DISTINCT_REPOS",
    "BRIDGE_MIN_LIFETIME",
    "BRIDGE_MIN_TOPICS",
    "BRIDGE_PAIR_REPO_FLOOR",
    "BRIDGE_REPO_SPAN_FLOOR",
    "BRIDGE_TOPIC_FLOOR",
    "BRIDGE_TOPIC_REPO_FLOOR",
    "BRIDGE_WEIGHTS",
    "CrossTopicBridge",
    "DeveloperBridge",
    "PAIR_BRIDGE_WEIGHTS",
    "REPO_BRIDGE_CONTRIB_FLOOR",
    "REPO_BRIDGE_TOPIC_FLOOR",
    "REPO_BRIDGE_WEIGHTS",
    "RepositoryBridge",
    "SMALL_SAMPLE_CAP",
    "TopicBridgeEvidence",
    "compute_developer_bridge",
    "compute_repository_bridge",
    "cross_topic_bridges",
    "developer_bridges",
    "repository_bridges",
]