"""Developer intelligence application layer: dataset building and reports.

This is the thin layer between storage and analytics: it loads the raw
observations (repositories, developer profiles, contributor links) into the
dataclasses the pure analytics modules consume, and assembles per-developer and
per-topic reports for the CLI. It never turns the wall clock into a data point:
``reference_now`` is the newest real observation ``captured_at`` in the dataset
(unless the caller pins it explicitly).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.analytics.developers import (
    DeveloperProfileMetrics,
    compute_profile_deltas,
)
from github_radar.analytics.intelligence import (
    ContributorLink,
    DeveloperActivity,
    DeveloperContext,
    EcosystemScore,
    EmergingDeveloper,
    RepoContext,
    TopicRelevance,
    compute_activity,
    compute_ecosystem_score,
    compute_emerging,
    compute_topic_relevance,
)
from github_radar.analytics.metrics import compute_metrics
from github_radar.analytics.momentum import compute_momentum
from github_radar.analytics.trends import classify_trend
from github_radar.domain import (
    ContributorSnapshot,
    DeveloperSnapshot,
    PublicContactMethods,
)
from github_radar.storage import repositories as storage
from github_radar.storage.models import DeveloperRow
from github_radar.util import utcnow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeveloperIdentity:
    """Display-level identity and public contacts for one tracked developer."""

    developer_id: int
    github_id: int
    login: str
    name: str | None
    first_seen_at: datetime
    followers: int | None
    public_contacts: PublicContactMethods

    @classmethod
    def from_row(cls, row: DeveloperRow) -> DeveloperIdentity:
        return cls(
            developer_id=row.id,
            github_id=row.github_id,
            login=row.login,
            name=row.name,
            first_seen_at=row.first_seen_at,
            followers=row.followers,
            public_contacts=PublicContactMethods(
                github=row.html_url or f"https://github.com/{row.login}",
                public_email=row.public_email,
                website=row.blog,
                twitter=row.twitter_username,
            ),
        )


@dataclass(frozen=True)
class DeveloperDataset:
    """The snapshot of tracked-ecosystem state analytics runs against."""

    reference_now: datetime
    repositories: Mapping[int, RepoContext]
    links: Mapping[int, tuple[ContributorLink, ...]]
    identities: Mapping[int, DeveloperIdentity]
    profiles: Mapping[int, DeveloperProfileMetrics | None]
    contexts: Mapping[int, DeveloperContext]
    topics: frozenset[str]

    @property
    def developer_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self.identities))


@dataclass(frozen=True)
class DeveloperReport:
    """One developer's full intelligence digest."""

    developer_id: int
    github_id: int
    login: str
    name: str | None
    followers: int | None
    first_seen_at: datetime
    public_contacts: PublicContactMethods
    profile: DeveloperProfileMetrics | None
    activity: DeveloperActivity | None
    ecosystem: EcosystemScore | None
    emerging: EmergingDeveloper
    topic_relevances: tuple[TopicRelevance, ...]
    associated_repositories: tuple[str, ...]
    languages: frozenset[str | None]
    associations: int

    def relevance_for(self, topic: str) -> TopicRelevance | None:
        for relevance in self.topic_relevances:
            if relevance.topic == topic:
                return relevance
        return None


@dataclass(frozen=True)
class TopicDeveloperAggregate:
    """Aggregate of the developer intelligence for a topic."""

    topic: str
    developer_count: int
    mean_relevance: float
    max_relevance: float
    emerging_count: int
    active_count: int
    established_count: int
    quiet_count: int
    insufficient_history_count: int


class DeveloperIntelligenceService:
    """Builds the developer dataset and reports from stored observations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load_dataset(
        self,
        *,
        reference_now: datetime | None = None,
    ) -> DeveloperDataset:
        """Load the full tracked ecosystem into analytics-ready structures.

        ``reference_now`` defaults to the newest observation ``captured_at``
        anywhere in the dataset (contributor or repository snapshots), falling
        back to the wall clock only when the dataset has no observations at all
        — in which case there is nothing to report anyway.
        """
        anchor = reference_now or await storage.newest_observation_at(self.session)
        reference_now = anchor or utcnow()

        repositories = await self._load_repository_contexts(reference_now)
        links = await self._load_contributor_links()
        identities, profiles, contexts = await self._load_developers(
            links, reference_now=reference_now
        )
        topics = frozenset(
            topic
            for repo in repositories.values()
            for topic in repo.topics
        )
        return DeveloperDataset(
            reference_now=reference_now,
            repositories=repositories,
            links=links,
            identities=identities,
            profiles=profiles,
            contexts=contexts,
            topics=topics,
        )

    async def _load_repository_contexts(
        self, reference_now: datetime
    ) -> dict[int, RepoContext]:
        contexts: dict[int, RepoContext] = {}
        rows = await storage.list_repos_with_latest(self.session)
        for repo, latest in rows:
            names = tuple(
                t.name
                for t in await storage.topics_for_repository(self.session, repo.id)
            )
            snapshots = await storage.snapshots_for_repository(self.session, repo.id)
            metrics = compute_metrics([s.to_domain() for s in snapshots])
            momentum = (
                compute_momentum(metrics, reference_now=reference_now)
                if metrics.latest_captured_at is not None
                else None
            )
            trend = classify_trend(metrics, momentum, reference_now=reference_now)
            contexts[repo.id] = RepoContext(
                repository_id=repo.id,
                full_name=repo.full_name,
                primary_language=repo.primary_language,
                owner_developer_id=repo.owner_id,
                topics=frozenset(names),
                momentum=momentum,
                trend=trend,
                latest_stars=(
                    latest.stars
                    if latest is not None
                    else metrics.latest_counts.get("stars")
                ),
            )
        return contexts

    async def _load_contributor_links(self) -> dict[int, tuple[ContributorLink, ...]]:
        records = await storage.list_contributor_links(self.session)
        if not records:
            return {}

        rows = await storage.all_contributor_snapshots(self.session)
        by_relationship: dict[tuple[int, int], list[ContributorSnapshot]] = {}
        for row in rows:
            snap = row.to_domain()
            by_relationship.setdefault(
                (snap.developer_id, snap.repository_id), []
            ).append(snap)

        totals_by_repo: dict[int, int] = {}
        for record in records:
            totals_by_repo[record.repository_id] = (
                totals_by_repo.get(record.repository_id, 0) + record.contributions
            )

        links_by_dev: dict[int, list[ContributorLink]] = {}
        for record in records:
            observations = by_relationship.get(
                (record.developer_id, record.repository_id), []
            )
            total = totals_by_repo.get(record.repository_id, 0)
            share = record.contributions / total if total > 0 else 0.0
            links_by_dev.setdefault(record.developer_id, []).append(
                ContributorLink(
                    repository_id=record.repository_id,
                    developer_id=record.developer_id,
                    contributions=record.contributions,
                    share=share,
                    observations=tuple(
                        sorted(observations, key=lambda obs: obs.captured_at)
                    ),
                )
            )
        return {
            dev_id: tuple(sorted(links, key=lambda link: link.repository_id))
            for dev_id, links in sorted(links_by_dev.items())
        }

    async def _load_developers(
        self,
        links: Mapping[int, tuple[ContributorLink, ...]],
        *,
        reference_now: datetime,
    ) -> tuple[
        dict[int, DeveloperIdentity],
        dict[int, DeveloperProfileMetrics | None],
        dict[int, DeveloperContext],
    ]:
        if not links:
            return {}, {}, {}
        rows = await storage.get_developers_by_id(self.session, list(links))

        snap_rows = await storage.all_developer_snapshots(self.session)
        snaps_by_dev: dict[int, list[DeveloperSnapshot]] = {}
        for snap_row in snap_rows:
            snap = snap_row.to_domain()
            snaps_by_dev.setdefault(snap.developer_id, []).append(snap)

        identities: dict[int, DeveloperIdentity] = {}
        profiles: dict[int, DeveloperProfileMetrics | None] = {}
        contexts: dict[int, DeveloperContext] = {}
        for dev_row in rows:
            identities[dev_row.id] = DeveloperIdentity.from_row(dev_row)
            profile = compute_profile_deltas(
                snaps_by_dev.get(dev_row.id, ()),
                reference_now=reference_now,
            )
            profiles[dev_row.id] = profile
            contexts[dev_row.id] = DeveloperContext(
                developer_id=dev_row.id,
                first_seen_at=dev_row.first_seen_at,
                followers=dev_row.followers,
                profile_metrics=profile,
            )
        return identities, profiles, contexts

    def build_reports(
        self,
        dataset: DeveloperDataset,
        *,
        window_days: int = 7,
    ) -> tuple[DeveloperReport, ...]:
        """Assemble the digest for every tracked developer."""
        reports: list[DeveloperReport] = []
        for developer_id in dataset.developer_ids:
            report = self.developer_report(
                dataset,
                developer_id,
                window_days=window_days,
            )
            if report is not None:
                reports.append(report)
        return tuple(reports)

    def developer_report(
        self,
        dataset: DeveloperDataset,
        developer_id: int,
        *,
        window_days: int = 7,
    ) -> DeveloperReport | None:
        """The digest for one developer, built purely from ``dataset``."""
        identity = dataset.identities.get(developer_id)
        context = dataset.contexts.get(developer_id)
        if identity is None or context is None:
            return None

        links = dataset.links.get(developer_id, ())
        repositories = dict(dataset.repositories)

        activity = compute_activity(
            links=links,
            reference_now=dataset.reference_now,
            window_days=window_days,
        )
        ecosystem = compute_ecosystem_score(
            developer_id=developer_id,
            links=links,
            repos=repositories,
            reference_now=dataset.reference_now,
            window_days=window_days,
        )
        emerging = compute_emerging(
            developer=context,
            links=links,
            repos=repositories,
            reference_now=dataset.reference_now,
            window_days=window_days,
        )

        relevances: list[TopicRelevance] = []
        for topic in sorted(dataset.topics):
            relevance = compute_topic_relevance(
                developer_id=developer_id,
                topic=topic,
                links=links,
                repos=repositories,
                reference_now=dataset.reference_now,
                window_days=window_days,
            )
            if relevance is not None:
                relevances.append(relevance)
        relevances.sort(key=lambda item: item.score, reverse=True)

        associated = {
            link.repository_id for link in links
        } | {
            rid
            for rid, repo in repositories.items()
            if repo.owner_developer_id == developer_id
        }
        associated_repos = tuple(
            repositories[rid].full_name for rid in sorted(associated) if rid in repositories
        )
        languages = frozenset(
            repositories[rid].primary_language
            for rid in associated
            if rid in repositories
        )

        followers = identity.followers
        profile = dataset.profiles.get(developer_id)
        if followers is None and profile is not None:
            followers = profile.latest_followers

        return DeveloperReport(
            developer_id=developer_id,
            github_id=identity.github_id,
            login=identity.login,
            name=identity.name,
            followers=followers,
            first_seen_at=identity.first_seen_at,
            public_contacts=identity.public_contacts,
            profile=profile,
            activity=activity,
            ecosystem=ecosystem,
            emerging=emerging,
            topic_relevances=tuple(relevances),
            associated_repositories=associated_repos,
            languages=languages,
            associations=len(associated),
        )

    def topic_developer_aggregates(
        self,
        dataset: DeveloperDataset,
        reports: Sequence[DeveloperReport],
        *,
        topic: str | None = None,
    ) -> tuple[TopicDeveloperAggregate, ...]:
        """Aggregate developer intelligence per tracked topic.

        Only developers with a computed relevance to a topic contribute to that
        topic's aggregate; labels are counted across those developers.
        """
        per_topic: dict[str, list[tuple[DeveloperReport, TopicRelevance]]] = {}
        for report in reports:
            for relevance in report.topic_relevances:
                if topic is not None and relevance.topic != topic:
                    continue
                per_topic.setdefault(relevance.topic, []).append(
                    (report, relevance)
                )

        aggregates: list[TopicDeveloperAggregate] = []
        for name, entries in sorted(per_topic.items()):
            scores = [relevance.score for _, relevance in entries]
            aggregates.append(
                TopicDeveloperAggregate(
                    topic=name,
                    developer_count=len(entries),
                    mean_relevance=sum(scores) / len(scores) if scores else 0.0,
                    max_relevance=max(scores) if scores else 0.0,
                    emerging_count=sum(
                        1
                        for report, _ in entries
                        if report.emerging.label == "EMERGING"
                    ),
                    active_count=sum(
                        1
                        for report, _ in entries
                        if report.emerging.label == "ACTIVE"
                    ),
                    established_count=sum(
                        1
                        for report, _ in entries
                        if report.emerging.label == "ESTABLISHED"
                    ),
                    quiet_count=sum(
                        1
                        for report, _ in entries
                        if report.emerging.label == "QUIET"
                    ),
                    insufficient_history_count=sum(
                        1
                        for report, _ in entries
                        if report.emerging.label == "INSUFFICIENT_HISTORY"
                    ),
                )
            )
        aggregates.sort(
            key=lambda item: (item.developer_count, item.mean_relevance),
            reverse=True,
        )
        return tuple(aggregates)


def filter_reports(
    reports: Sequence[DeveloperReport],
    *,
    topic: str | None = None,
    language: str | None = None,
    repository: str | None = None,
    min_followers: int | None = None,
    min_confidence: float | None = None,
    has_public_contact: bool = False,
) -> tuple[DeveloperReport, ...]:
    """Apply the ``developers`` command filters.

    ``topic`` keeps developers with a computed relevance to it; ``language`` and
    ``repository`` require an association to a tracked repository matching the
    filter; ``min_confidence`` gates on the emerging classification confidence.
    """
    wanted_repository = repository.lower() if repository else None
    wanted_language = language.lower() if language else None
    filtered: list[DeveloperReport] = []
    for report in reports:
        if topic is not None and report.relevance_for(topic) is None:
            continue
        if wanted_language is not None:
            langs = {
                lang.lower() for lang in report.languages if lang is not None
            }
            if wanted_language not in langs:
                continue
        if wanted_repository is not None:
            if not any(
                name.lower() == wanted_repository
                or wanted_repository in name.lower()
                for name in report.associated_repositories
            ):
                continue
        if min_followers is not None:
            if report.followers is None or report.followers < min_followers:
                continue
        if has_public_contact and not report.public_contacts.available:
            continue
        if min_confidence is not None:
            if report.emerging.confidence_score < min_confidence:
                continue
        filtered.append(report)
    return tuple(filtered)


__all__ = [
    "DeveloperDataset",
    "DeveloperIdentity",
    "DeveloperIntelligenceService",
    "DeveloperReport",
    "TopicDeveloperAggregate",
    "filter_reports",
]