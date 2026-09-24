"""Shared builders for AI unit tests (not collected as a test module)."""

from __future__ import annotations

from datetime import UTC, datetime

from github_radar.ai.evidence import ContributorDigest, RepositoryDigest
from github_radar.ai.models import EvidenceBundle, EvidenceItem, build_bundle
from github_radar.analytics.developers import DeveloperProfileMetrics
from github_radar.analytics.intelligence import (
    DeveloperActivity,
    EcosystemScore,
    EmergingDeveloper,
)
from github_radar.analytics.metrics import RepoMetrics, compute_metrics
from github_radar.analytics.momentum import MomentumScore
from github_radar.analytics.topics import TopicAggregate
from github_radar.analytics.trends import Trend
from github_radar.domain import PublicContactMethods, RepositorySnapshot
from github_radar.services.intelligence import DeveloperReport

REFERENCE_NOW = datetime(2024, 5, 8, tzinfo=UTC)


def captured(day: int) -> datetime:
    return datetime(2024, 5, day, tzinfo=UTC)


def snapshots() -> list[RepositorySnapshot]:
    return [
        RepositorySnapshot(
            captured_at=captured(1),
            stars=100,
            forks=20,
            watchers=9,
            open_issues=2,
            size_kb=10,
            pushed_at=captured(1),
        ),
        RepositorySnapshot(
            captured_at=captured(8),
            stars=112,
            forks=24,
            watchers=10,
            open_issues=3,
            size_kb=11,
            pushed_at=captured(7),
        ),
    ]


def repo_metrics() -> RepoMetrics:
    return compute_metrics(snapshots())


def repo_momentum() -> MomentumScore:
    return MomentumScore(
        score=0.5,
        stars_growth_pct=12.0,
        forks_growth_pct=20.0,
        recency=1.0,
        components={"stars": 0.2, "forks": 0.2, "recency": 0.1},
    )


def repo_trend() -> Trend:
    return Trend(
        trend="rising",
        label="rising",
        explanation="stars and forks grew while a fresh commit was observed.",
        growth_pct=0.12,
        recency=1.0,
    )


def repo_digest(*, textual: dict[str, str] | None = None) -> RepositoryDigest:
    return RepositoryDigest(
        full_name="acme/widget",
        owner_login="acme",
        description="A widget library.",
        homepage=None,
        primary_language="Python",
        default_branch="main",
        visibility="public",
        is_fork=False,
        is_archived=False,
        is_disabled=False,
        is_template=False,
        github_created_at=datetime(2019, 1, 1, tzinfo=UTC),
        github_updated_at=captured(1),
        pushed_at=captured(2),
        topics=("awesome-widget",),
        contributors=(ContributorDigest(login="alice", contributions=42),),
        latest_captured_at=captured(8),
        snapshot_count=2,
        first_snapshot_at=captured(1),
        metrics=repo_metrics(),
        momentum=repo_momentum(),
        trend=repo_trend(),
        textual=textual or {},
    )


def bundle(entity_key: str = "acme/widget", values: tuple[int, ...] = (1,)) -> EvidenceBundle:
    items = [
        EvidenceItem(
            id="",
            kind="measurement",
            source="test",
            payload=f"fact {value}",
            observed_at=REFERENCE_NOW,
        )
        for value in values
    ]
    return build_bundle(
        entity_type="repository", entity_key=entity_key, window_days=7, items=items
    )


def developer_report() -> DeveloperReport:
    profile = DeveloperProfileMetrics(
        reference_now=REFERENCE_NOW,
        observations=2,
        first_captured_at=datetime(2024, 1, 1, tzinfo=UTC),
        last_captured_at=captured(1),
        latest_followers=25,
        latest_following=None,
        latest_public_repos=None,
    )
    return DeveloperReport(
        developer_id=1,
        github_id=101,
        login="alice",
        name="Alice Dev",
        followers=25,
        first_seen_at=datetime(2020, 1, 1, tzinfo=UTC),
        public_contacts=PublicContactMethods(
            github="https://github.com/alice",
            public_email="alice@example.com",
            website="https://alice.dev",
            twitter="alice_tw",
        ),
        profile=profile,
        activity=DeveloperActivity(
            window_days=7,
            available=True,
            score=0.8,
            components={"delta": 0.8},
            confidence_score=0.9,
            confidence_level="HIGH",
            total_positive_delta=4,
            active_repos=2,
            usable_repos=3,
            observations=5,
            complete_share=1.0,
        ),
        ecosystem=EcosystemScore(
            score=0.6,
            components={"relevance": 0.6},
            missing_components=(),
            associated_repositories=3,
            owned_repositories=1,
            topic_relevance_mean=0.5,
            momentum_mean=0.4,
        ),
        emerging=EmergingDeveloper(
            label="EMERGING",
            score=0.7,
            components={"growth": 0.7},
            raw_signals={"positive_delta": 4},
            confidence_score=0.8,
            confidence_level="MEDIUM",
            evidence_points=3,
            explanation="Increasing activity over the window",
        ),
        topic_relevances=(),
        associated_repositories=("acme/widget",),
        languages=frozenset({"Python"}),
        associations=3,
    )


def topic_aggregate() -> TopicAggregate:
    return TopicAggregate(
        name="awesome-widget",
        repository_count=2,
        total_momentum=1.4,
        avg_momentum=0.7,
        share_with_momentum=1.0,
        confidence_score=0.9,
        confidence_level="HIGH",
    )