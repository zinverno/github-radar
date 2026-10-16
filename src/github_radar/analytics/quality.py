"""Read-only diagnostics of existing scores, never replacement scoring rules."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from itertools import combinations
from typing import Any

from github_radar.analytics.confidence import RepositoryReport, repo_window_confidence
from github_radar.analytics.developers import compute_contribution_delta, compute_profile_deltas
from github_radar.analytics.intelligence import (
    ContributorLink,
    DeveloperContext,
    RepoContext,
    compute_activity,
    compute_emerging,
)
from github_radar.analytics.metrics import DELTA_WINDOWS_DAYS, compute_metrics
from github_radar.analytics.momentum import compute_momentum
from github_radar.analytics.topics import aggregate_topics
from github_radar.analytics.trends import classify_trend
from github_radar.domain import DeveloperSnapshot, RepositorySnapshot
from github_radar.graph.bridges import compute_developer_bridge
from github_radar.graph.model import EcosystemGraph


@dataclass(frozen=True)
class Provenance:
    category: str
    recipe: str
    query: str
    run_id: str


@dataclass(frozen=True)
class RepositoryEvidence:
    repository_id: int
    github_id: int
    full_name: str
    language: str | None
    created_at: datetime | None
    first_seen_at: datetime
    archived: bool
    fork: bool
    snapshots: tuple[RepositorySnapshot, ...]
    topics: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()


@dataclass(frozen=True)
class DeveloperEvidence:
    developer_id: int
    github_id: int
    login: str
    first_seen_at: datetime
    followers: int | None
    snapshots: tuple[DeveloperSnapshot, ...] = ()


def ratio(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def star_band(stars: int | None) -> str:
    if stars is None:
        return "unknown"
    for maximum, label in ((10, "0-9"), (100, "10-99"), (1000, "100-999"), (10000, "1000-9999")):
        if stars < maximum:
            return label
    return "10000+"


def age_band(age_days: float | None) -> str:
    if age_days is None:
        return "unknown"
    if age_days < 0:
        return "future_created_at"
    for maximum, label in ((30, "under-30d"), (365, "30-364d"), (1095, "365-1094d")):
        if age_days < maximum:
            return label
    return "1095d+"


def repository_quality(
    repositories: Sequence[RepositoryEvidence],
    *,
    catalog: Mapping[str, str],
    assessed_at: datetime,
    stale_after_hours: int = 24,
    top: int = 10,
) -> dict[str, Any]:
    """Count stable identities once; completeness is separate from computability."""
    if assessed_at.tzinfo is None or stale_after_hours < 0 or top < 1:
        raise ValueError("Use an aware assessment time, nonnegative staleness and positive top")
    repos = sorted(repositories, key=lambda repo: repo.github_id)
    if len({repo.github_id for repo in repos}) != len(repos):
        raise ValueError("Repository evidence must be unique by stable GitHub ID")
    categories: dict[str, set[int]] = {name: set() for name in catalog}
    cohorts: dict[tuple[str, str], set[int]] = {}
    by_topic: dict[str, list[RepositoryReport]] = {}
    rows: list[dict[str, Any]] = []
    for repo in repos:
        metrics = compute_metrics(repo.snapshots)
        latest = metrics.latest_captured_at
        if latest is not None and latest > assessed_at:
            raise ValueError("Assessment time precedes stored observations; not a historical query")
        momentum = compute_momentum(metrics, reference_now=latest) if latest else None
        trend = classify_trend(metrics, momentum, reference_now=latest or assessed_at)
        confidence = repo_window_confidence(metrics, window_days=7)
        windows = {}
        for days in DELTA_WINDOWS_DAYS:
            delta = metrics.get("stars", days)
            windows[str(days)] = {
                "status": "unavailable"
                if delta is None
                else ("complete" if delta.window.complete else "partial"),
                "delta": delta.delta if delta else None,
                "growth_pct": delta.growth_pct if delta else None,
                "span_days": delta.window.span_days if delta else None,
                "base_gap_hours": delta.base_gap_hours if delta else None,
            }
        names = sorted({p.category for p in repo.provenance})
        for p in repo.provenance:
            categories.setdefault(p.category, set()).add(repo.github_id)
            cohorts.setdefault((p.category, p.recipe), set()).add(repo.github_id)
        for topic in sorted(set(repo.topics)):
            by_topic.setdefault(topic, []).append(
                RepositoryReport(repo.full_name, metrics, momentum)
            )
        age = (assessed_at - repo.created_at).total_seconds() / 86400 if repo.created_at else None
        observation_age = (assessed_at - latest).total_seconds() / 3600 if latest else None
        rows.append(
            {
                "github_id": repo.github_id,
                "full_name": repo.full_name,
                "categories": names,
                "github_topics": sorted(set(repo.topics)),
                "language": repo.language or "unknown",
                "created_at": (repo.created_at.isoformat() if repo.created_at else None),
                "first_seen_at": repo.first_seen_at.isoformat(),
                "age_days": age,
                "age_band": age_band(age),
                "stars": metrics.latest_counts.get("stars"),
                "star_band": star_band(metrics.latest_counts.get("stars")),
                "archived": repo.archived,
                "fork": repo.fork,
                "snapshot_count": len(metrics.series),
                "latest_observation_at": latest.isoformat() if latest else None,
                "observation_age_hours": observation_age,
                "stale": observation_age is not None and observation_age >= stale_after_hours,
                "windows": windows,
                "momentum": asdict(momentum) if momentum else None,
                "confidence_7d": asdict(confidence),
                "existing_trend": asdict(trend),
                "trend_supported_by_complete_7d": windows["7"]["status"] == "complete",
                "provenance": [
                    asdict(p)
                    for p in sorted(
                        repo.provenance, key=lambda p: (p.category, p.recipe, p.query, p.run_id)
                    )
                ],
            }
        )
    ranked = sorted(
        (r for r in rows if r["momentum"] is not None),
        key=lambda r: (-r["momentum"]["score"], r["github_id"]),
    )
    top_rows = ranked[:top]
    category_counts = {name: len(ids) for name, ids in sorted(categories.items())}
    category_union = set().union(*categories.values()) if categories else set()
    topic_rows = []
    for aggregate in aggregate_topics(by_topic):
        reports = by_topic[aggregate.name]
        available = sum(r.momentum is not None for r in reports)
        topic_rows.append(
            {
                **asdict(aggregate),
                "momentum_available": available,
                "complete_7d_windows": sum(r.has_complete_stars_window for r in reports),
                "observed_momentum_total": aggregate.total_momentum if available else None,
                "note": "Existing aggregate averages include missing momentum as zero; "
                "use availability to distinguish missing from observed zero.",
            }
        )
    return {
        "total_repositories": len(rows),
        "repository_observations": sum(r["snapshot_count"] for r in rows),
        "snapshot_counts": {
            label: sum(
                (r["snapshot_count"] >= 3 if label == "3+" else r["snapshot_count"] == int(label))
                for r in rows
            )
            for label in ("0", "1", "2", "3+")
        },
        "languages": dict(sorted(Counter(r["language"] for r in rows).items())),
        "age_bands": dict(sorted(Counter(r["age_band"] for r in rows).items())),
        "lifetime_star_bands": dict(sorted(Counter(r["star_band"] for r in rows).items())),
        "archived": sum(r["archived"] for r in rows),
        "forked": sum(r["fork"] for r in rows),
        "stale_observations": sum(r["stale"] for r in rows),
        "never_observed": sum(r["snapshot_count"] == 0 for r in rows),
        "windows": {
            str(days): {
                status: sum(r["windows"][str(days)]["status"] == status for r in rows)
                for status in ("complete", "partial", "unavailable")
            }
            for days in DELTA_WINDOWS_DAYS
        },
        "momentum": {"available": len(ranked), "unavailable": len(rows) - len(ranked)},
        "curated_categories": {
            "counts": category_counts,
            "catalog_queries": dict(sorted(catalog.items())),
            "outside_current_catalog": sorted(set(categories) - set(catalog)),
            "with_provenance": len(category_union),
            "without_provenance": len(rows) - len(category_union),
            "multiple_categories": sum(len(r["categories"]) > 1 for r in rows),
            "membership_count": sum(category_counts.values()),
            "cohorts": [
                {"category": c, "recipe": r, "repositories": len(ids)}
                for (c, r), ids in sorted(cohorts.items())
            ],
            "overlaps": [
                {
                    "categories": [a, b],
                    "repositories": len(categories[a] & categories[b]),
                    "github_ids": sorted(categories[a] & categories[b]),
                }
                for a, b in combinations(sorted(categories), 2)
            ],
        },
        "github_topics": sorted(topic_rows, key=lambda r: r["name"]),
        "ranking_diagnostics": {
            "ranked_repositories": len(ranked),
            "top_count": len(top_rows),
            "top_github_ids": [r["github_id"] for r in top_rows],
            "top_star_bands": dict(sorted(Counter(r["star_band"] for r in top_rows).items())),
            "top_age_bands": dict(sorted(Counter(r["age_band"] for r in top_rows).items())),
            "top_category_memberships": dict(
                sorted(Counter(category for r in top_rows for category in r["categories"]).items())
            ),
            "largest_category_share": ratio(max(category_counts.values(), default=0), len(rows)),
            "ranked_with_partial_7d": sum(
                r["windows"]["7"]["status"] != "complete" for r in ranked
            ),
            "high_confidence_with_fewer_than_3_snapshots": sum(
                r["confidence_7d"]["level"] == "HIGH" and r["snapshot_count"] < 3 for r in rows
            ),
            "ranked_small_base_stars_under_10": sum(
                (r["stars"] - r["windows"]["7"]["delta"]) < 10 for r in ranked
            ),
            "assessment": "descriptive_only" if ranked else "insufficient_history",
            "note": "Composition and small-base counts are evidence to inspect, not proof "
            "of bias. Scores and weights are unchanged; ties use stable GitHub IDs.",
        },
        "repositories": rows,
    }


def developer_quality(
    developers: Sequence[DeveloperEvidence],
    *,
    graph: EcosystemGraph,
    repos: Mapping[int, RepoContext],
    reference_now: datetime,
    top: int = 10,
) -> dict[str, Any]:
    """Expose score components, lifetime/history separation and single-repo evidence."""
    rows: list[dict[str, Any]] = []
    for developer in sorted(developers, key=lambda d: d.github_id):
        links = []
        for rid in sorted(graph.repositories_for_developer(developer.developer_id)):
            evidence = graph.contribution_evidence(developer.developer_id, rid)
            assert evidence is not None
            links.append(
                ContributorLink(
                    rid,
                    developer.developer_id,
                    evidence.contributions,
                    evidence.share,
                    evidence.observations,
                )
            )
        profile = compute_profile_deltas(developer.snapshots, reference_now=reference_now)
        context = DeveloperContext(
            developer.developer_id, developer.first_seen_at, developer.followers, profile
        )
        activity = compute_activity(links=links, reference_now=reference_now)
        emerging = compute_emerging(
            developer=context, links=links, repos=repos, reference_now=reference_now
        )
        bridge = compute_developer_bridge(
            graph, developer.developer_id, reference_now=reference_now, window_days=7
        )
        windows = {}
        for days in DELTA_WINDOWS_DAYS:
            deltas = [
                compute_contribution_delta(
                    link.observations, reference_now=reference_now, window_days=days
                )
                for link in links
            ]
            windows[str(days)] = {
                "usable_relationships": sum(d is not None and d.available for d in deltas),
                "complete_relationships": sum(d is not None and d.complete for d in deltas),
            }
        rows.append(
            {
                "github_id": developer.github_id,
                "login": developer.login,
                "followers": developer.followers,
                "profile_observations": len(developer.snapshots),
                "contributor_observations": sum(len(link.observations) for link in links),
                "contributor_repositories": len(links),
                "associated_repositories": len(
                    graph.associated_repositories(developer.developer_id)
                ),
                "lifetime_contributions": sum(link.contributions for link in links),
                "windows": windows,
                "activity_available": activity is not None and activity.available,
                "observed_positive_delta_7d": (
                    activity.total_positive_delta if activity and activity.available else None
                ),
                "activity": asdict(activity) if activity else None,
                "emerging": asdict(emerging),
                "bridge": asdict(bridge),
            }
        )
    ranked = sorted(
        (r for r in rows if r["emerging"]["label"] != "INSUFFICIENT_HISTORY"),
        key=lambda r: (-r["emerging"]["score"], r["github_id"]),
    )[:top]
    return {
        "total_developers": len(rows),
        "profile_observations": sum(r["profile_observations"] for r in rows),
        "contributor_observations": sum(r["contributor_observations"] for r in rows),
        "with_usable_contributor_history": {
            str(days): sum(r["windows"][str(days)]["usable_relationships"] > 0 for r in rows)
            for days in DELTA_WINDOWS_DAYS
        },
        "with_complete_contributor_history": {
            str(days): sum(r["windows"][str(days)]["complete_relationships"] > 0 for r in rows)
            for days in DELTA_WINDOWS_DAYS
        },
        "labels": dict(sorted(Counter(r["emerging"]["label"] for r in rows).items())),
        "ranking_diagnostics": {
            "assessment": "descriptive_only" if ranked else "insufficient_history",
            "top_github_ids": [r["github_id"] for r in ranked],
            "top_with_10000_plus_followers": sum((r["followers"] or 0) >= 10000 for r in ranked),
            "all_with_10000_plus_followers": sum((r["followers"] or 0) >= 10000 for r in rows),
            "top_single_repository": sum(r["associated_repositories"] == 1 for r in ranked),
            "single_repository_emerging": sum(
                r["associated_repositories"] == 1 and r["emerging"]["label"] == "EMERGING"
                for r in rows
            ),
            "single_repository_bridge_max": max(
                (r["bridge"]["bridge_score"] for r in rows if r["associated_repositories"] == 1),
                default=None,
            ),
            "note": "Absolute follower counts are descriptive, not score inputs. Observed "
            "follower growth does contribute to emerging scores and evidence. "
            "A single repository can satisfy emerging evidence; inspect breadth.",
        },
        "developers": rows,
    }
