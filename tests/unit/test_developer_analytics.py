"""Developer analytics: observation series, deltas, scores, classification.

Every scenario is fully deterministic: all observations and the reference time
"now" are explicit timestamps, never the wall clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from github_radar.analytics import (
    EMERGING_THRESHOLD,
    REQUIRED_EVIDENCE,
    SHARE_CAP,
    ContributionHistory,
    ContributorLink,
    DeveloperContext,
    MomentumScore,
    RepoContext,
    Trend,
    TrendClass,
    compute_activity,
    compute_contribution_delta,
    compute_ecosystem_score,
    compute_emerging,
    compute_profile_deltas,
    compute_topic_relevance,
    damped_delta,
)
from github_radar.analytics.history import ObservationSeries
from github_radar.domain import (
    ContributorSnapshot,
    Developer,
    DeveloperSnapshot,
    PublicContactMethods,
)

T0 = datetime(2024, 1, 1, tzinfo=UTC)
REF = T0 + timedelta(days=100)


def day(offset: float) -> datetime:
    return T0 + timedelta(days=offset)


def dsnap(
    developer_id: int,
    *,
    day_offset: float,
    followers: int | None,
    following: int | None = None,
    public_repos: int | None = None,
) -> DeveloperSnapshot:
    return DeveloperSnapshot(
        developer_id=developer_id,
        captured_at=day(day_offset),
        followers=followers,
        following=following,
        public_repos=public_repos,
    )


def osnap(
    repository_id: int,
    developer_id: int,
    *,
    day_offset: float,
    contributions: int,
) -> ContributorSnapshot:
    return ContributorSnapshot(
        repository_id=repository_id,
        developer_id=developer_id,
        captured_at=day(day_offset),
        contributions=contributions,
    )


def repo(
    repository_id: int,
    *,
    topics: frozenset[str] = frozenset(),
    owner_developer_id: int | None = None,
    momentum: float | None = 4.0,
    trend: TrendClass | None = "rising",
) -> RepoContext:
    return RepoContext(
        repository_id=repository_id,
        full_name=f"owner/repo{repository_id}",
        primary_language="python",
        owner_developer_id=owner_developer_id,
        topics=topics,
        momentum=(
            MomentumScore(
                score=momentum,
                stars_growth_pct=50.0,
                forks_growth_pct=20.0,
                recency=0.9,
                components={"stars": 0.4, "forks": 0.3, "recency": 0.3},
            )
            if momentum is not None
            else None
        ),
        trend=(
            Trend(
                trend=trend,
                label=trend.capitalize(),
                explanation="test",
                growth_pct=50.0,
                recency=0.9,
            )
            if trend is not None
            else None
        ),
        latest_stars=100,
    )


def link(
    developer_id: int,
    repository_id: int,
    observations: tuple[ContributorSnapshot, ...],
) -> ContributorLink:
    latest = observations[-1].contributions
    total_share = 0.4
    return ContributorLink(
        repository_id=repository_id,
        developer_id=developer_id,
        contributions=latest,
        share=total_share,
        observations=observations,
    )


def context(developer_id: int, *, first_seen: datetime = REF) -> DeveloperContext:
    return DeveloperContext(
        developer_id=developer_id,
        first_seen_at=first_seen,
        followers=100,
        profile_metrics=None,
    )


# ---------------------------------------------------------------------------
# Observation window + profile deltas
# ---------------------------------------------------------------------------


def test_observation_series_sorts_and_dedupes() -> None:
    series = ObservationSeries(
        [
            dsnap(1, day_offset=5, followers=20),
            dsnap(1, day_offset=0, followers=10),
            dsnap(1, day_offset=0, followers=11),
        ],
        captured_at=lambda obs: obs.captured_at,
    )
    assert len(series) == 2
    assert series.first is not None and series.first.captured_at == day(0)
    assert series.last is not None and series.last.captured_at == day(5)


def test_window_delta_complete() -> None:
    obs = [
        osnap(1, 1, day_offset=90, contributions=10),
        osnap(1, 1, day_offset=95, contributions=40),
        osnap(1, 1, day_offset=100, contributions=100),
    ]
    delta = compute_contribution_delta(
        obs, reference_now=REF, window_days=7
    )
    assert delta is not None
    assert delta.delta == 90
    assert delta.base_contributions == 10
    assert delta.current_contributions == 100
    assert delta.complete is True
    assert delta.available is True


def test_window_delta_without_base_is_none() -> None:
    delta = compute_contribution_delta(
        [osnap(1, 1, day_offset=100, contributions=100)],
        reference_now=REF,
        window_days=7,
    )
    assert delta is not None
    assert delta.delta is None
    assert delta.available is False
    assert delta.complete is False


def test_window_delta_unusable_when_single_observation() -> None:
    assert compute_contribution_delta([], reference_now=REF, window_days=7) is None


def test_profile_deltas_deterministic() -> None:
    observations = [
        dsnap(1, day_offset=0, followers=100),
        dsnap(1, day_offset=2, followers=110),
        dsnap(1, day_offset=8, followers=130),
    ]
    metrics = compute_profile_deltas(
        observations, reference_now=day(8)
    )
    assert metrics is not None
    seven = metrics.followers_delta(7)
    assert seven is not None
    assert seven.delta == 30
    assert seven.complete is True
    one = metrics.followers_delta(1)
    assert one is not None
    assert one.delta == 20
    assert one.complete is False
    assert metrics.latest_followers == 130
    assert metrics.observations == 3


def test_profile_deltas_single_observation_reports_no_deltas() -> None:
    metrics = compute_profile_deltas(
        [dsnap(1, day_offset=0, followers=100)], reference_now=REF
    )
    assert metrics is not None
    assert metrics.latest_followers == 100
    for days in (1, 7, 30):
        delta = metrics.followers_delta(days)
        assert delta is not None
        assert delta.delta is None
        assert delta.complete is False


def test_profile_deltas_empty_history_is_none() -> None:
    assert compute_profile_deltas([], reference_now=REF) is None


def test_contribution_history_span() -> None:
    history = ContributionHistory(
        repository_id=1,
        developer_id=1,
        observations=(
            osnap(1, 1, day_offset=10, contributions=1),
            osnap(1, 1, day_offset=20, contributions=2),
        ),
    )
    assert history.span_days == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Topic relevance
# ---------------------------------------------------------------------------


def test_topic_relevance_scores_and_evidence() -> None:
    repos = {
        1: repo(1, topics=frozenset({"ai"})),
        2: repo(2, topics=frozenset({"ai"})),
    }
    links = [
        link(1, 1, (osnap(1, 1, day_offset=90, contributions=10),
                    osnap(1, 1, day_offset=100, contributions=50))),
        link(1, 2, (osnap(2, 1, day_offset=90, contributions=5),
                    osnap(1, 2, day_offset=100, contributions=20))),
    ]
    relevance = compute_topic_relevance(
        developer_id=1,
        topic="ai",
        links=links,
        repos=repos,
        reference_now=REF,
    )
    assert relevance is not None
    assert relevance.repo_count == 2
    assert relevance.score > 0.0
    assert relevance.recent_contribution_delta > 0
    assert "owner/repo1" in relevance.evidence


def test_topic_relevance_share_is_capped() -> None:
    repos = {1: repo(1, topics=frozenset({"ai"}))}
    big_share = link(
        1, 1, (osnap(1, 1, day_offset=100, contributions=100),)
    )
    big_share = ContributorLink(
        repository_id=1,
        developer_id=1,
        contributions=100,
        share=0.9,
        observations=(osnap(1, 1, day_offset=100, contributions=100),),
    )
    relevance = compute_topic_relevance(
        developer_id=1,
        topic="ai",
        links=[big_share],
        repos=repos,
        reference_now=REF,
    )
    assert relevance is not None
    assert relevance.contribution_share <= SHARE_CAP


def test_topic_relevance_for_unrelated_topic_is_none() -> None:
    repos = {1: repo(1, topics=frozenset({"cli"}))}
    links = [
        link(1, 1, (osnap(1, 1, day_offset=100, contributions=5),))
    ]
    assert (
        compute_topic_relevance(
            developer_id=1,
            topic="ai",
            links=links,
            repos=repos,
            reference_now=REF,
        )
        is None
    )


def test_topic_relevance_counts_owned_repos_without_links() -> None:
    repos = {
        1: repo(1, topics=frozenset({"ai"}), owner_developer_id=9),
        2: repo(2, topics=frozenset({"ai"}), owner_developer_id=1),
    }
    relevance = compute_topic_relevance(
        developer_id=1,
        topic="ai",
        links=[],
        repos=repos,
        reference_now=REF,
    )
    assert relevance is not None
    assert relevance.repo_count == 1
    assert relevance.owned_count == 1
    assert relevance.score > 0.0


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------


def test_activity_none_without_history() -> None:
    assert compute_activity(links=[], reference_now=REF) is None
    assert (
        compute_activity(
            links=[
                ContributorLink(
                    repository_id=1,
                    developer_id=1,
                    contributions=0,
                    share=0.0,
                    observations=(),
                )
            ],
            reference_now=REF,
        )
        is None
    )


def test_activity_unavailable_not_zero() -> None:
    links = [link(1, 1, (osnap(1, 1, day_offset=100, contributions=5),))]
    activity = compute_activity(links=links, reference_now=REF)
    assert activity is not None
    assert activity.available is False
    assert activity.total_positive_delta == 0
    assert activity.active_repos == 0


def test_activity_counts_observed_delta() -> None:
    links = [
        link(1, 1, (osnap(1, 1, day_offset=90, contributions=10),
                    osnap(1, 1, day_offset=100, contributions=40))),
        link(1, 2, (osnap(2, 1, day_offset=90, contributions=5),
                    osnap(1, 2, day_offset=100, contributions=5))),
    ]
    activity = compute_activity(links=links, reference_now=REF)
    assert activity is not None
    assert activity.available is True
    assert activity.active_repos == 1
    assert activity.usable_repos == 2
    assert activity.total_positive_delta == 30
    assert activity.score > 0.0


# ---------------------------------------------------------------------------
# Ecosystem score
# ---------------------------------------------------------------------------


def test_ecosystem_score_none_without_association() -> None:
    repos = {1: repo(1)}
    assert (
        compute_ecosystem_score(
            developer_id=9, links=[], repos=repos, reference_now=REF
        )
        is None
    )
    assert (
        compute_ecosystem_score(
            developer_id=9,
            links=[
                link(1, 1, (osnap(1, 1, day_offset=100, contributions=5),))
            ],
            repos={},
            reference_now=REF,
        )
        is None
    )


def test_ecosystem_score_includes_momentum_and_ownership() -> None:
    repos = {
        1: repo(1, owner_developer_id=1),
        2: repo(2, owner_developer_id=1),
    }
    links = [link(1, 2, (osnap(2, 1, day_offset=100, contributions=5),))]
    score = compute_ecosystem_score(
        developer_id=1, links=links, repos=repos, reference_now=REF
    )
    assert score is not None
    assert score.associated_repositories == 2
    assert score.owned_repositories == 2
    assert score.score > 0.0
    assert score.momentum_mean == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# Emerging classification
# ---------------------------------------------------------------------------


def test_emerging_classifies_rising_activity() -> None:
    repos = {
        1: repo(1),
        2: repo(2),
        3: repo(3),
    }
    links = [
        link(1, 1, (osnap(1, 1, day_offset=85, contributions=10),
                    osnap(1, 1, day_offset=90, contributions=40),
                    osnap(1, 1, day_offset=100, contributions=100))),
        link(1, 2, (osnap(2, 1, day_offset=85, contributions=5),
                    osnap(1, 2, day_offset=90, contributions=20),
                    osnap(1, 2, day_offset=100, contributions=50))),
        link(1, 3, (osnap(3, 1, day_offset=85, contributions=2),
                    osnap(1, 3, day_offset=90, contributions=10),
                    osnap(1, 3, day_offset=100, contributions=30))),
    ]
    metrics = compute_profile_deltas(
        [
            dsnap(1, day_offset=85, followers=40),
            dsnap(1, day_offset=100, followers=80),
        ],
        reference_now=REF,
    )
    dev = DeveloperContext(
        developer_id=1,
        first_seen_at=day(85),
        followers=80,
        profile_metrics=metrics,
    )
    emerging = compute_emerging(
        developer=dev, links=links, repos=repos, reference_now=REF
    )
    assert emerging.label == "EMERGING"
    assert emerging.score >= EMERGING_THRESHOLD
    assert emerging.evidence_points >= REQUIRED_EVIDENCE


def test_emerging_requires_positive_delta() -> None:
    repos = {5: repo(5), 6: repo(6)}
    links = [
        link(1, 5, (osnap(5, 1, day_offset=70, contributions=10),
                    osnap(1, 5, day_offset=90, contributions=10),
                    osnap(1, 5, day_offset=100, contributions=10))),
        link(1, 6, (osnap(6, 1, day_offset=70, contributions=5),
                    osnap(1, 6, day_offset=90, contributions=5),
                    osnap(1, 6, day_offset=100, contributions=5))),
    ]
    metrics = compute_profile_deltas(
        [
            dsnap(1, day_offset=70, followers=10),
            dsnap(1, day_offset=100, followers=30),
        ],
        reference_now=REF,
    )
    dev = DeveloperContext(
        developer_id=1,
        first_seen_at=day(80),
        followers=30,
        profile_metrics=metrics,
    )
    emerging = compute_emerging(
        developer=dev, links=links, repos=repos, reference_now=REF
    )
    assert emerging.label == "ACTIVE"


def test_emerging_quiet_without_recent_movement() -> None:
    repos = {8: repo(8, momentum=None, trend=None)}
    links = [
        link(1, 8, (osnap(8, 1, day_offset=0, contributions=10),
                    osnap(1, 8, day_offset=60, contributions=10))),
    ]
    dev = DeveloperContext(
        developer_id=1,
        first_seen_at=day(-60),
        followers=10,
        profile_metrics=None,
    )
    emerging = compute_emerging(
        developer=dev, links=links, repos=repos, reference_now=REF
    )
    assert emerging.label == "QUIET"


def test_emerging_established_by_lifetime() -> None:
    repos = {7: repo(7, momentum=None, trend=None)}
    links = [
        link(1, 7, (osnap(7, 1, day_offset=0, contributions=195),
                    osnap(1, 7, day_offset=95, contributions=200))),
    ]
    dev = DeveloperContext(
        developer_id=1,
        first_seen_at=day(-60),
        followers=10,
        profile_metrics=None,
    )
    emerging = compute_emerging(
        developer=dev, links=links, repos=repos, reference_now=REF
    )
    assert emerging.label == "ESTABLISHED"


def test_emerging_insufficient_history() -> None:
    emerging = compute_emerging(
        developer=context(1),
        links=[],
        repos={},
        reference_now=REF,
    )
    assert emerging.label == "INSUFFICIENT_HISTORY"
    assert emerging.evidence_points == 0


def test_lone_plus_one_is_not_emerging() -> None:
    repos = {9: repo(9, momentum=None, trend=None)}
    links = [
        link(1, 9, (osnap(9, 1, day_offset=90, contributions=10),
                    osnap(1, 9, day_offset=100, contributions=11))),
    ]
    dev = DeveloperContext(
        developer_id=1,
        first_seen_at=day(-60),
        followers=10,
        profile_metrics=None,
    )
    emerging = compute_emerging(
        developer=dev, links=links, repos=repos, reference_now=REF
    )
    assert emerging.label != "EMERGING"
    assert emerging.evidence_points < REQUIRED_EVIDENCE


def test_damped_delta_diminishing_returns() -> None:
    assert damped_delta(0) == 0.0
    assert damped_delta(100) == pytest.approx(1.0)
    assert damped_delta(1) == pytest.approx(0.1)
    assert damped_delta(1) < damped_delta(4) < damped_delta(100)


# ---------------------------------------------------------------------------
# Public contacts
# ---------------------------------------------------------------------------


def test_public_contact_methods_available() -> None:
    full = PublicContactMethods(
        github="https://github.com/ada",
        public_email="ada@example.com",
        website=None,
        twitter="adadef",
    )
    assert "public_email" in full.available
    assert "twitter" in full.available
    assert "website" not in full.available
    none = PublicContactMethods(None, None, None, None)
    assert none.available == ()


def test_contact_methods_from_developer() -> None:
    registration = Developer(
        github_id=1,
        login="ada",
        name="Ada",
        html_url=None,
        public_email="ada@example.com",
        blog="https://ada.dev",
        twitter_username="adadef",
    )
    contacts = PublicContactMethods.from_developer(registration)
    assert contacts.github == "https://github.com/ada"
    assert contacts.public_email == "ada@example.com"
    assert contacts.website == "https://ada.dev"
    assert "website" in contacts.available