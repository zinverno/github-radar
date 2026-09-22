"""Developer intelligence service layer: reports, topic aggregates, filters.

These tests build a :class:`DeveloperDataset` by hand and call the report
assembly/filtering logic directly — no database is involved.
"""

from __future__ import annotations

from datetime import UTC, datetime

from github_radar.analytics import (
    ContributorLink,
    DeveloperContext,
    RepoContext,
    TrendClass,
)
from github_radar.analytics.momentum import MomentumScore
from github_radar.analytics.trends import Trend
from github_radar.domain import ContributorSnapshot, PublicContactMethods
from github_radar.services import (
    DeveloperDataset,
    DeveloperIdentity,
    DeveloperIntelligenceService,
    filter_reports,
)

T0 = datetime(2024, 1, 1, tzinfo=UTC)
REF = T0


def contribs(repository_id: int, developer_id: int) -> tuple[ContributorSnapshot, ...]:
    return (
        ContributorSnapshot(
            repository_id=repository_id,
            developer_id=developer_id,
            captured_at=T0,
            contributions=100,
        ),
    )


def context(developer_id: int, *, first_seen: datetime = T0) -> DeveloperContext:
    return DeveloperContext(
        developer_id=developer_id,
        first_seen_at=first_seen,
        followers=1000,
        profile_metrics=None,
    )


def identity(
    developer_id: int,
    login: str,
    *,
    followers: int | None = 1000,
    contacts: PublicContactMethods | None = None,
) -> DeveloperIdentity:
    return DeveloperIdentity(
        developer_id=developer_id,
        github_id=developer_id,
        login=login,
        name=login.capitalize(),
        first_seen_at=T0,
        followers=followers,
        public_contacts=contacts or PublicContactMethods(None, None, None, None),
    )


def repo(
    repository_id: int,
    *,
    full_name: str,
    topics: frozenset[str],
    owner_developer_id: int | None = None,
    language: str = "python",
) -> RepoContext:
    return RepoContext(
        repository_id=repository_id,
        full_name=full_name,
        primary_language=language,
        owner_developer_id=owner_developer_id,
        topics=topics,
        momentum=momentum(4.0),
        trend=trend("rising"),
        latest_stars=500,
    )


def momentum(score: float) -> MomentumScore:
    return MomentumScore(
        score=score,
        stars_growth_pct=50.0,
        forks_growth_pct=20.0,
        recency=0.9,
        components={"stars": 0.4, "forks": 0.3, "recency": 0.3},
    )


def trend(t: TrendClass) -> Trend:
    return Trend(
        trend=t,
        label=t.capitalize(),
        explanation="test",
        growth_pct=50.0,
        recency=0.9,
    )


def link(developer_id: int, repository_id: int) -> ContributorLink:
    return ContributorLink(
        repository_id=repository_id,
        developer_id=developer_id,
        contributions=100,
        share=1.0,
        observations=contribs(repository_id, developer_id),
    )


def make_dataset() -> DeveloperDataset:
    repos = {
        1: repo(1, full_name="org/ai-tool", topics=frozenset({"ai"})),
        2: repo(2, full_name="org/ai-lib", topics=frozenset({"ai", "mcp"})),
        3: repo(3, full_name="other/cli-thing", topics=frozenset({"cli"})),
    }
    links = {
        1: (link(1, 1), link(1, 2)),
        2: (link(2, 3),),
    }
    ada = identity(1, "ada", contacts=PublicContactMethods(
        github="https://github.com/ada",
        public_email=None,
        website="https://ada.dev",
        twitter=None,
    ))
    grace = identity(2, "grace", followers=None)
    identities = {1: ada, 2: grace}
    contexts = {1: context(1), 2: context(2)}
    return DeveloperDataset(
        reference_now=REF,
        repositories=repos,
        links=links,
        identities=identities,
        profiles={},
        contexts=contexts,
        topics=frozenset({"ai", "mcp", "cli"}),
    )


def test_build_reports_returns_one_per_developer() -> None:
    service = DeveloperIntelligenceService.__new__(DeveloperIntelligenceService)
    dataset = make_dataset()
    reports = service.build_reports(dataset)
    assert {report.login for report in reports} == {"ada", "grace"}


def test_developer_report_unknown_id_is_none() -> None:
    service = DeveloperIntelligenceService.__new__(DeveloperIntelligenceService)
    assert service.developer_report(make_dataset(), 999) is None


def test_developer_report_assembles_digest() -> None:
    service = DeveloperIntelligenceService.__new__(DeveloperIntelligenceService)
    report = service.developer_report(make_dataset(), 1)
    assert report is not None
    assert report.login == "ada"
    assert report.associations == 2
    assert set(report.associated_repositories) == {"org/ai-tool", "org/ai-lib"}
    assert report.languages == frozenset({"python"})
    assert report.relevance_for("ai") is not None
    assert report.relevance_for("cli") is None
    assert "website" in report.public_contacts.available
    assert report.emerging.evidence_points >= 0


def test_topics_are_aggregated_per_relevant_developer() -> None:
    service = DeveloperIntelligenceService.__new__(DeveloperIntelligenceService)
    dataset = make_dataset()
    reports = service.build_reports(dataset)
    aggregates = service.topic_developer_aggregates(dataset, reports)

    by_name = {aggregate.topic: aggregate for aggregate in aggregates}
    assert set(by_name) == {"ai", "mcp", "cli"}

    ai = by_name["ai"]
    assert ai.developer_count == 1  # only "ada" has a computed ai relevance
    assert ai.mean_relevance > 0.0
    assert ai.max_relevance > 0.0
    assert sum(
        [
            ai.emerging_count,
            ai.active_count,
            ai.established_count,
            ai.quiet_count,
            ai.insufficient_history_count,
        ]
    ) == ai.developer_count


def test_topic_aggregate_can_filter_to_one_topic() -> None:
    service = DeveloperIntelligenceService.__new__(DeveloperIntelligenceService)
    dataset = make_dataset()
    reports = service.build_reports(dataset)
    aggregates = service.topic_developer_aggregates(
        dataset, reports, topic="ai"
    )
    assert [aggregate.topic for aggregate in aggregates] == ["ai"]


def test_filter_reports_by_topic_and_contact() -> None:
    service = DeveloperIntelligenceService.__new__(DeveloperIntelligenceService)
    reports = service.build_reports(make_dataset())

    only_ada = filter_reports(reports, topic="ai")
    assert [report.login for report in only_ada] == ["ada"]

    with_contact = filter_reports(reports, has_public_contact=True)
    assert {report.login for report in with_contact} == {"ada"}


def test_filter_reports_by_language_repository_and_followers() -> None:
    service = DeveloperIntelligenceService.__new__(DeveloperIntelligenceService)
    reports = service.build_reports(make_dataset())

    python = filter_reports(reports, language="python")
    assert {report.login for report in python} == {"ada", "grace"}

    repo_hits = filter_reports(reports, repository="cli-thing")
    assert {report.login for report in repo_hits} == {"grace"}

    big_enough = filter_reports(reports, min_followers=500)
    assert {report.login for report in big_enough} == {"ada"}


def test_filter_reports_min_confidence_gates_emerging() -> None:
    service = DeveloperIntelligenceService.__new__(DeveloperIntelligenceService)
    reports = service.build_reports(make_dataset())
    high = 1.0
    filtered = filter_reports(reports, min_confidence=high)
    for report in filtered:
        assert report.emerging.confidence_score >= high