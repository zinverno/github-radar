"""Derived relationships over the ecosystem graph.

These are deterministic relationships *derived* from the direct evidence edges
(a developer ↔ developer co-contribution, repository ↔ repository overlap,
topic ↔ topic overlap). Every relationship keeps its raw evidence visible and
never represents a social claim:

* A co-contribution relationship means exactly: *these developers contributed
  to at least one tracked repository in common*.
* Repository overlap is expressed as separate developer/topic overlap values
  (a combined score exists only alongside its components).
* Topic overlap is expressed as separate repository/developer overlap values;
  no opaque combined number is produced.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from math import log1p

from github_radar.analytics.intelligence import SHARE_CAP
from github_radar.analytics.momentum import MAX_SCORE as MOMENTUM_MAX
from github_radar.graph.confidence import GraphConfidence, graph_confidence
from github_radar.graph.model import EcosystemGraph
from github_radar.graph.overlap import Overlap, jaccard
from github_radar.graph.scoring import RecentActivity, clamp, mean, recent_activity

# Co-contribution strength weights (sum to 1.0 when every term is available).
CO_CONTRIBUTION_WEIGHTS = {
    "breadth": 0.40,
    "share": 0.30,
    "momentum": 0.20,
    "recent_activity": 0.10,
}

# Breadth (number of shared repositories) saturates here (log-scaled).
CO_CONTRIB_BREADTH_FLOOR = 5

# Repository relationship combined-score weights.
REPO_RELATIONSHIP_WEIGHTS = {
    "developer_overlap": 0.50,
    "topic_overlap": 0.50,
}

# How deeply shared contributors must overlap before the recent-activity term
# treats a single-positive overlap as meaningful.
_ONE_POSITIVE_TERM = 0.5
_ONE_SIDE_PARTIAL_TERM = 0.25


def _capped_share(value: float) -> float:
    return clamp(value, 0.0, SHARE_CAP)


@dataclass(frozen=True)
class SharedRepositoryEvidence:
    """One repository both developers contributed to, with raw evidence."""

    repository_id: int
    full_name: str
    a_contributions: int
    b_contributions: int
    a_share: float
    b_share: float
    momentum_score: float | None
    a_activity: RecentActivity
    b_activity: RecentActivity


@dataclass(frozen=True)
class CoContributorRelationship:
    """A developer connected to another through shared tracked repositories."""

    developer_id: int
    login: str
    relationship: str = "CO_CONTRIBUTOR"
    shared_repository_count: int = 0
    shared_repositories: tuple[SharedRepositoryEvidence, ...] = ()
    strength: float = 0.0
    components: dict[str, float] | None = None
    missing_components: tuple[str, ...] = ()
    confidence: GraphConfidence | None = None


@dataclass(frozen=True)
class RepositoryRelationship:
    """Two repositories connected through shared contributors and/or topics."""

    repository_id: int
    full_name: str
    shared_developer_count: int
    shared_topic_count: int
    developer_overlap: Overlap[int]
    topic_overlap: Overlap[int]
    relationship_score: float
    components: dict[str, float]
    confidence: GraphConfidence


@dataclass(frozen=True)
class TopicRelationship:
    """Two topics connected through shared repositories and/or developers."""

    topic_id: int
    topic: str
    shared_repository_count: int
    shared_developer_count: int
    repository_overlap: Overlap[int]
    developer_overlap: Overlap[int]
    confidence: GraphConfidence


# ---------------------------------------------------------------------------
# Developer ↔ developer (co-contribution)
# ---------------------------------------------------------------------------


def _shared_evidence(
    graph: EcosystemGraph,
    developer_a: int,
    developer_b: int,
    shared_repository_ids: Sequence[int],
    *,
    reference_now: datetime,
    window_days: int,
) -> tuple[SharedRepositoryEvidence, ...]:
    evidence: list[SharedRepositoryEvidence] = []
    for repository_id in shared_repository_ids:
        repo = graph.repository(repository_id)
        ea = graph.contribution_evidence(developer_a, repository_id)
        eb = graph.contribution_evidence(developer_b, repository_id)
        if ea is None or eb is None:
            continue
        evidence.append(
            SharedRepositoryEvidence(
                repository_id=repository_id,
                full_name=repo.full_name if repo is not None else f"repo-{repository_id}",
                a_contributions=ea.contributions,
                b_contributions=eb.contributions,
                a_share=ea.share,
                b_share=eb.share,
                momentum_score=(
                    repo.momentum.score if repo is not None and repo.momentum else None
                ),
                a_activity=recent_activity(
                    ea, reference_now=reference_now, window_days=window_days
                ),
                b_activity=recent_activity(
                    eb, reference_now=reference_now, window_days=window_days
                ),
            )
        )
    return tuple(evidence)


def compute_co_contribution_strength(
    *,
    shared_evidence: Sequence[SharedRepositoryEvidence],
) -> tuple[float, dict[str, float], tuple[str, ...]]:
    """Weighted co-contribution strength in ``[0, 1]`` plus its components.

    Breadth is log-scaled (so one giant shared repository cannot saturate it),
    per-repo shares are capped, momentum is bounded by :data:`MOMENTUM_MAX`,
    and the recent-activity term only counts windows that were actually
    observed. Missing activity stays missing (it never becomes zero activity,
    and the component is listed in ``missing_components`` when absent).
    """
    if not shared_evidence:
        return 0.0, {}, ("breadth", "share", "momentum", "recent_activity")

    count = len(shared_evidence)
    breadth = clamp(log1p(count) / log1p(CO_CONTRIB_BREADTH_FLOOR))

    shares = [
        clamp(
            min(_capped_share(evidence.a_share), _capped_share(evidence.b_share))
            / SHARE_CAP
        )
        for evidence in shared_evidence
    ]
    share_term = mean(shares)

    momentum_scores = [
        evidence.momentum_score
        for evidence in shared_evidence
        if evidence.momentum_score is not None
    ]
    if momentum_scores:
        momentum_term = clamp(mean(momentum_scores) / MOMENTUM_MAX)
    else:
        momentum_term = 0.0

    co_activity: list[float] = []
    for evidence in shared_evidence:
        term = _pair_activity_term(evidence.a_activity, evidence.b_activity)
        if term is not None:
            co_activity.append(term)
    if co_activity:
        recent_term = clamp(mean(co_activity))
    else:
        recent_term = 0.0

    components = {
        "breadth": CO_CONTRIBUTION_WEIGHTS["breadth"] * breadth,
        "share": CO_CONTRIBUTION_WEIGHTS["share"] * share_term,
        "momentum": CO_CONTRIBUTION_WEIGHTS["momentum"] * momentum_term,
        "recent_activity": CO_CONTRIBUTION_WEIGHTS["recent_activity"] * recent_term,
    }
    missing = tuple(
        name
        for name, present in (
            ("momentum", bool(momentum_scores)),
            ("recent_activity", bool(co_activity)),
        )
        if not present
    )
    return clamp(sum(components.values())), components, missing


def _pair_activity_term(
    a_activity: RecentActivity, b_activity: RecentActivity
) -> float | None:
    """Per-repository co-activity in ``[0, 1]``, or ``None`` when unmeasured.

    Both windows available: 1.0 when both moved, 0.5 when exactly one moved,
    0.0 when neither moved (genuinely observed stillness). One side available:
    partial evidence. No window on either side: ``None`` (never zero).
    """
    if a_activity.available and b_activity.available:
        if a_activity.delta is not None and a_activity.delta > 0:
            if b_activity.delta is not None and b_activity.delta > 0:
                return 1.0
            return _ONE_POSITIVE_TERM
        if b_activity.delta is not None and b_activity.delta > 0:
            return _ONE_POSITIVE_TERM
        return 0.0
    if a_activity.available:
        return (
            _ONE_SIDE_PARTIAL_TERM
            if a_activity.delta is not None and a_activity.delta > 0
            else 0.0
        )
    if b_activity.available:
        return (
            _ONE_SIDE_PARTIAL_TERM
            if b_activity.delta is not None and b_activity.delta > 0
            else 0.0
        )
    return None


def find_co_contributors(
    graph: EcosystemGraph,
    developer_id: int,
    *,
    reference_now: datetime,
    window_days: int,
    limit: int | None = None,
) -> tuple[CoContributorRelationship, ...]:
    """Developers that share at least one tracked repository with ``developer_id``.

    Only relationships backed by real shared repositories are produced; the
    mandatory invariant is that every result references at least one real
    shared repository.
    """
    if not graph.repositories_for_developer(developer_id):
        return ()

    shared_repos_by_other: dict[int, list[int]] = {}
    for repository_id in graph.repository_ids:
        if developer_id not in graph.developers_for_repository(repository_id):
            continue
        for other in sorted(graph.developers_for_repository(repository_id)):
            if other != developer_id:
                shared_repos_by_other.setdefault(other, []).append(repository_id)

    relationships: list[CoContributorRelationship] = []
    for developer_b, repository_ids in sorted(shared_repos_by_other.items()):
        shared_evidence = _shared_evidence(
            graph,
            developer_id,
            developer_b,
            repository_ids,
            reference_now=reference_now,
            window_days=window_days,
        )
        strength, components, missing = compute_co_contribution_strength(
            shared_evidence=shared_evidence
        )
        with_history = sum(
            1 for evidence in shared_evidence if (
                evidence.a_activity.observations > 0
                and evidence.b_activity.observations > 0
            )
        )
        with_recent = sum(
            1
            for evidence in shared_evidence
            if evidence.a_activity.available and evidence.b_activity.available
        )
        other_node = graph.developer(developer_b)
        relationships.append(
            CoContributorRelationship(
                developer_id=developer_b,
                login=other_node.login if other_node is not None else str(developer_b),
                shared_repository_count=len(repository_ids),
                shared_repositories=shared_evidence,
                strength=strength,
                components=components,
                missing_components=missing,
                confidence=graph_confidence(
                    distinct_repositories=len(shared_evidence),
                    history_coverage=(
                        with_history / len(shared_evidence) if shared_evidence else 0.0
                    ),
                    recent_coverage=(
                        with_recent / len(shared_evidence) if shared_evidence else 0.0
                    ),
                ),
            )
        )

    relationships.sort(key=lambda item: (-item.strength, item.login))
    if limit is not None:
        relationships = relationships[:limit]
    return tuple(relationships)


# ---------------------------------------------------------------------------
# Repository ↔ repository
# ---------------------------------------------------------------------------

def related_repositories(
    graph: EcosystemGraph,
    repository_id: int,
    *,
    reference_now: datetime,
    window_days: int,
    limit: int | None = None,
) -> tuple[RepositoryRelationship, ...]:
    """Repositories related to ``repository_id`` through shared evidence.

    Both overlap types are reported separately (developers, topics); a combined
    ``relationship_score`` exists only with its components visible. Related
    repositories require at least one shared developer or one shared topic.
    """
    repo = graph.repository(repository_id)
    if repo is None:
        return ()
    my_devs = graph.associated_developers(repository_id)
    my_topics = graph.topics_for_repository(repository_id)

    relationships: list[RepositoryRelationship] = []
    for other_id in graph.repository_ids:
        if other_id == repository_id:
            continue
        other = graph.repository(other_id)
        if other is None:
            continue
        other_devs = graph.associated_developers(other_id)
        other_topics = graph.topics_for_repository(other_id)

        dev_overlap = jaccard(my_devs, other_devs)
        topic_overlap = jaccard(my_topics, other_topics)
        if dev_overlap.intersection_count == 0 and topic_overlap.intersection_count == 0:
            continue

        components = {
            "developer_overlap": (
                REPO_RELATIONSHIP_WEIGHTS["developer_overlap"] * dev_overlap.jaccard
            ),
            "topic_overlap": (
                REPO_RELATIONSHIP_WEIGHTS["topic_overlap"] * topic_overlap.jaccard
            ),
        }
        score = clamp(sum(components.values()))

        shared_devs = dev_overlap.intersection
        with_history = 0
        with_recent = 0
        for developer_id in shared_devs:
            ea = graph.contribution_evidence(developer_id, repository_id)
            eb = graph.contribution_evidence(developer_id, other_id)
            if ea is None or eb is None:
                continue
            if ea.observations and eb.observations:
                with_history += 1
            ra = recent_activity(ea, reference_now=reference_now, window_days=window_days)
            rb = recent_activity(eb, reference_now=reference_now, window_days=window_days)
            if ra.available and rb.available:
                with_recent += 1

        relationships.append(
            RepositoryRelationship(
                repository_id=other_id,
                full_name=other.full_name,
                shared_developer_count=dev_overlap.intersection_count,
                shared_topic_count=topic_overlap.intersection_count,
                developer_overlap=dev_overlap,
                topic_overlap=topic_overlap,
                relationship_score=score,
                components=components,
                confidence=graph_confidence(
                    distinct_repositories=(
                        dev_overlap.intersection_count + topic_overlap.intersection_count
                    ),
                    history_coverage=(
                        with_history / len(shared_devs) if shared_devs else 0.0
                    ),
                    recent_coverage=(
                        with_recent / len(shared_devs) if shared_devs else 0.0
                    ),
                ),
            )
        )

    relationships.sort(
        key=lambda item: (-item.relationship_score, item.full_name)
    )
    if limit is not None:
        relationships = relationships[:limit]
    return tuple(relationships)


# ---------------------------------------------------------------------------
# Topic ↔ topic
# ---------------------------------------------------------------------------


def developers_for_topic(graph: EcosystemGraph, topic_id: int) -> frozenset[int]:
    """Developers associated with a topic's tracked repositories.

    A developer is in a topic when they contribute to or own at least one
    tracked repository tagged with the topic.
    """
    developers: set[int] = set()
    for repository_id in graph.repositories_for_topic(topic_id):
        developers.update(graph.associated_developers(repository_id))
    return frozenset(developers)


def related_topics(
    graph: EcosystemGraph,
    topic_id: int,
    *,
    limit: int | None = None,
) -> tuple[TopicRelationship, ...]:
    """Topics overlapping ``topic_id`` through repositories or developers.

    Repository and developer overlaps stay separate; nothing is collapsed into
    an opaque number.
    """
    my_repos = graph.repositories_for_topic(topic_id)
    my_devs = developers_for_topic(graph, topic_id)

    relationships: list[TopicRelationship] = []
    for other_id in graph.topic_ids:
        if other_id == topic_id:
            continue
        other = graph.topic(other_id)
        if other is None:
            continue
        other_repos = graph.repositories_for_topic(other_id)
        other_devs = developers_for_topic(graph, other_id)

        repo_overlap = jaccard(my_repos, other_repos)
        dev_overlap = jaccard(my_devs, other_devs)
        if repo_overlap.intersection_count == 0 and dev_overlap.intersection_count == 0:
            continue

        relationships.append(
            TopicRelationship(
                topic_id=other_id,
                topic=other.name,
                shared_repository_count=repo_overlap.intersection_count,
                shared_developer_count=dev_overlap.intersection_count,
                repository_overlap=repo_overlap,
                developer_overlap=dev_overlap,
                confidence=graph_confidence(
                    distinct_repositories=(
                        repo_overlap.intersection_count + dev_overlap.intersection_count
                    ),
                    history_coverage=0.0,
                    recent_coverage=0.0,
                ),
            )
        )

    relationships.sort(
        key=lambda item: (
            -item.shared_developer_count,
            -item.shared_repository_count,
            item.topic,
        )
    )
    if limit is not None:
        relationships = relationships[:limit]
    return tuple(relationships)


__all__ = [
    "CO_CONTRIBUTION_WEIGHTS",
    "CO_CONTRIB_BREADTH_FLOOR",
    "CoContributorRelationship",
    "REPO_RELATIONSHIP_WEIGHTS",
    "RepositoryRelationship",
    "SharedRepositoryEvidence",
    "TopicRelationship",
    "compute_co_contribution_strength",
    "developers_for_topic",
    "find_co_contributors",
    "related_repositories",
    "related_topics",
]