"""Coverage diagnostics preserve missingness and expose existing model limitations."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from github_radar.analytics.intelligence import RepoContext
from github_radar.analytics.quality import (
    DeveloperEvidence,
    Provenance,
    RepositoryEvidence,
    developer_quality,
    repository_quality,
)
from github_radar.domain import ContributorSnapshot, RepositorySnapshot
from tests.unit.test_bridges import GraphBuilder

NOW = datetime(2026, 10, 16, 18, tzinfo=UTC)
CATALOG = {
    "ai-agents": "topic:ai-agents",
    "developer-tools": "topic:developer-tools",
    "mcp": "topic:mcp",
}


def snapshot(days=0, stars=100, forks=10):
    return RepositorySnapshot(
        captured_at=NOW - timedelta(days=days),
        stars=stars,
        forks=forks,
        pushed_at=NOW,
        watchers=5,
        open_issues=0,
        size_kb=100,
    )


def repo(identity=1, *, snapshots=None, categories=("ai-agents",), **overrides):
    base = RepositoryEvidence(
        repository_id=identity,
        github_id=identity,
        full_name=f"owner/repo-{identity}",
        language="Python",
        created_at=NOW - timedelta(days=400),
        first_seen_at=NOW,
        archived=False,
        fork=False,
        snapshots=tuple(snapshots if snapshots is not None else [snapshot()]),
        topics=("unrelated-github-topic",),
        provenance=tuple(Provenance(c, "recent", f"topic:{c}", "pilot") for c in categories),
    )
    return replace(base, **overrides)


def quality(repos, **kwargs):
    return repository_quality(repos, catalog=CATALOG, assessed_at=NOW, **kwargs)


def test_empty_and_pilot_overlap_preserve_missingness():
    empty = quality([])
    assert empty["ranking_diagnostics"]["largest_category_share"] is None
    assert empty["momentum"] == {"available": 0, "unavailable": 0}
    repos = [
        repo(1, categories=("ai-agents", "developer-tools")),
        repo(2),
        repo(3),
        repo(4, categories=("developer-tools",)),
        repo(5, categories=("developer-tools",)),
    ]
    result = quality(repos)
    assert result["total_repositories"] == result["repository_observations"] == 5
    assert result["snapshot_counts"] == {"0": 0, "1": 5, "2": 0, "3+": 0}
    assert result["curated_categories"]["counts"] == {
        "ai-agents": 3,
        "developer-tools": 3,
        "mcp": 0,
    }
    assert result["curated_categories"]["membership_count"] == 6
    assert result["curated_categories"]["multiple_categories"] == 1
    assert result["curated_categories"]["overlaps"][0]["github_ids"] == [1]
    assert result["github_topics"][0]["name"] == "unrelated-github-topic"
    assert result["github_topics"][0]["observed_momentum_total"] is None
    assert result["momentum"] == {"available": 0, "unavailable": 5}
    assert all(
        w == {"complete": 0, "partial": 0, "unavailable": 5} for w in result["windows"].values()
    )
    assert all(r["existing_trend"]["trend"] == "new" for r in result["repositories"])
    assert result == quality(list(reversed(repos)))


def test_recipe_memberships_and_missing_provenance_do_not_double_count():
    one = repo(
        provenance=(
            Provenance("ai-agents", "recent", "q", "one"),
            Provenance("ai-agents", "active", "q2", "two"),
            Provenance("retired-category", "recent", "q3", "three"),
        )
    )
    result = quality([one, repo(2, categories=())])
    categories = result["curated_categories"]
    assert categories["counts"]["ai-agents"] == 1
    assert categories["without_provenance"] == categories["with_provenance"] == 1
    assert categories["outside_current_catalog"] == ["retired-category"]
    assert len(categories["cohorts"]) == 3
    assert len(result["repositories"][0]["provenance"]) == 3
    with pytest.raises(ValueError, match="stable GitHub ID"):
        quality([one, one])


def test_observed_zero_is_not_missing_and_each_window_has_its_own_eligibility():
    result = quality(
        [
            repo(1, snapshots=[]),
            repo(2),
            repo(3, snapshots=[snapshot(30), snapshot(7), snapshot(1), snapshot()]),
            repo(4, snapshots=[snapshot(1), snapshot()]),
        ]
    )
    assert result["windows"]["1"] == {"complete": 2, "partial": 0, "unavailable": 2}
    assert result["windows"]["7"] == {"complete": 1, "partial": 1, "unavailable": 2}
    assert result["windows"]["30"] == {"complete": 1, "partial": 1, "unavailable": 2}
    assert result["repositories"][0]["windows"]["7"]["delta"] is None
    zero = result["repositories"][2]
    assert zero["windows"]["7"]["delta"] == zero["momentum"]["stars_growth_pct"] == 0
    assert zero["existing_trend"]["trend"] == "steady"


def test_partial_decline_and_sparse_high_confidence_are_exposed_without_changing_scores():
    result = quality(
        [
            repo(1, snapshots=[snapshot(0.25, 100), snapshot(0, 80)]),
            repo(2, snapshots=[snapshot(7), snapshot()]),
        ]
    )
    partial, sparse = result["repositories"]
    assert partial["existing_trend"]["trend"] == "declining"
    assert partial["windows"]["7"]["status"] == "partial"
    assert not partial["trend_supported_by_complete_7d"]
    assert partial["confidence_7d"]["level"] == "LOW"
    assert sparse["confidence_7d"]["level"] == "HIGH"
    assert sparse["confidence_7d"]["score"] == pytest.approx(0.796)
    assert result["ranking_diagnostics"]["ranked_with_partial_7d"] == 1
    assert result["ranking_diagnostics"]["high_confidence_with_fewer_than_3_snapshots"] == 1
    assert partial["existing_trend"]["window"]["span_days"] == 0.25
    assert partial["existing_trend"]["window"]["end"] == NOW.isoformat()
    json.dumps(result, allow_nan=False)


def test_small_percentage_base_can_outrank_large_absolute_growth():
    result = quality(
        [
            repo(1, snapshots=[snapshot(7, 100000, 1000), snapshot(0, 101000, 1001)]),
            repo(2, snapshots=[snapshot(7, 1, 1), snapshot(0, 2, 2)]),
            repo(3, snapshots=[snapshot(0, 999999, 5000)]),
        ]
    )
    assert result["ranking_diagnostics"]["top_github_ids"] == [2, 1]
    assert result["ranking_diagnostics"]["ranked_small_base_stars_under_10"] == 1
    assert result["repositories"][2]["momentum"] is None  # Popularity is not observed growth.


def test_age_means_creation_not_discovery_and_staleness_is_separate():
    repos = [
        repo(1, archived=True),
        repo(2, fork=True, created_at=None, language=None),
        repo(3, created_at=NOW - timedelta(days=2), snapshots=[]),
    ]
    result = repository_quality(repos, catalog=CATALOG, assessed_at=NOW + timedelta(hours=24))
    assert result["age_bands"] == {"365-1094d": 1, "under-30d": 1, "unknown": 1}
    assert result["stale_observations"] == 2 and result["never_observed"] == 1
    assert result["archived"] == result["forked"] == 1
    assert result["repositories"][0]["first_seen_at"] == NOW.isoformat()
    assert result["repositories"][0]["existing_trend"]["trend"] == "new"
    with pytest.raises(ValueError, match="precedes"):
        repository_quality(repos, catalog=CATALOG, assessed_at=NOW - timedelta(hours=1))


def test_500_synthetic_repositories_are_deterministic_and_counted_once():
    repos = [
        repo(
            i,
            snapshots=[snapshot(7, i + 1), snapshot(0, i + 2)],
            categories=("ai-agents", "developer-tools"),
        )
        for i in range(1, 501)
    ]
    result = quality(repos, top=20)
    assert result["total_repositories"] == result["windows"]["7"]["complete"] == 500
    assert result["curated_categories"]["membership_count"] == 1000
    assert result["repository_observations"] == 1000
    assert len(result["ranking_diagnostics"]["top_github_ids"]) == 20
    assert result == quality(list(reversed(repos)), top=20)


def test_contribution_history_fame_and_single_repository_diagnostics():
    builder = GraphBuilder()
    builder.add_repo(1, "owner/repo", topics=("one", "two", "three", "four"))
    for identity, observation_count in ((1, 1), (2, 3), (3, 3)):
        builder.add_dev(identity, f"dev{identity}", followers=100000 if identity == 3 else 0)
        builder.add_contrib(identity, 1, 100000, observations=observation_count)
    graph = builder.graph
    reference = NOW
    for identity in (1, 2, 3):
        observations: tuple[ContributorSnapshot, ...] = (
            ContributorSnapshot(1, identity, NOW, 100000),
        )
        if identity != 1:
            observations = (
                ContributorSnapshot(1, identity, NOW - timedelta(days=7), 99900),
                *observations,
            )
        graph.add_contribution(
            identity, 1, contributions=100000, share=1 / 3, observations=observations
        )
    node = graph.repository(1)
    assert node is not None
    repos = {
        1: RepoContext(
            1,
            "owner/repo",
            "Python",
            None,
            frozenset(graph.topic_names),
            node.momentum,
            None,
            100,
        )
    }
    developers = [
        DeveloperEvidence(i, i, f"dev{i}", reference, 100000 if i == 3 else 0)
        for i in graph.developer_ids
    ]
    result = developer_quality(developers, graph=graph, repos=repos, reference_now=reference)
    one, two, three = result["developers"]
    assert one["lifetime_contributions"] == 100000
    assert not one["activity_available"] and one["observed_positive_delta_7d"] is None
    assert one["emerging"]["label"] == "INSUFFICIENT_HISTORY"
    assert two["emerging"] == three["emerging"]  # Absolute followers add no evidence.
    assert two["activity_available"]
    assert result["ranking_diagnostics"]["single_repository_bridge_max"] <= 0.35
    assert all(r["bridge"]["small_sample"] for r in result["developers"])
    graph.add_contribution(
        2,
        1,
        contributions=100000,
        share=1 / 3,
        observations=(
            ContributorSnapshot(1, 2, NOW - timedelta(days=7), 100000),
            ContributorSnapshot(1, 2, NOW, 100000),
        ),
    )
    zero = developer_quality(developers, graph=graph, repos=repos, reference_now=reference)[
        "developers"
    ][1]
    assert zero["activity_available"] and zero["observed_positive_delta_7d"] == 0
