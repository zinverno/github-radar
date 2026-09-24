"""Deterministic evidence bundles for every AI artifact type.

Each builder converts Phase 2–4 analytics (plus bounded, allowlisted metadata)
into a :class:`~github_radar.ai.models.EvidenceBundle` with stable ``E1..``
ids. Builders are **pure**: they never query the database or the network, which
keeps fingerprinting deterministic and lets unit tests feed canned analytic
objects. The service layer gathers those analytic objects.

Safety guarantees encoded here:
* allowlist only — the fields we explicitly render;
* developer evidence never includes e-mails, blogs, twitter handles, company,
  location or bio (contact/PII fields are excluded by construction);
* untrusted repository text is wrapped in ``<untrusted-data>`` markers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from github_radar.ai.models import (
    EvidenceBundle,
    EvidenceItem,
    build_bundle,
)
from github_radar.ai.safety import wrap_untrusted
from github_radar.analytics.confidence import (
    ConfidenceLevel,
    repo_window_confidence,
)
from github_radar.analytics.intelligence import (
    DeveloperActivity,
    EcosystemScore,
    EmergingDeveloper,
)
from github_radar.analytics.metrics import FieldDelta, RepoMetrics
from github_radar.analytics.momentum import MAX_SCORE, MomentumScore
from github_radar.analytics.topics import TopicAggregate
from github_radar.analytics.trends import Trend
from github_radar.graph.metrics import NodeCentrality
from github_radar.graph.reports import (
    CrossTopicBridgeReport,
    DeveloperGraphReport,
    EcosystemGraphReport,
    TopicGraphReport,
)
from github_radar.services.intelligence import DeveloperReport

MAX_TOP_CONTRIBUTORS = 5
MAX_TOPIC_REPOS = 10
MAX_ECOSYSTEM_HUBS = 6
MAX_BRIDGES = 5
MAX_RELATED = 5


@dataclass(frozen=True)
class ContributorDigest:
    """One top contributor (display data only — no contact/PII fields)."""

    login: str
    contributions: int


@dataclass(frozen=True)
class RepositoryDigest:
    """Everything the repository artifact may render — assembled by the CLI."""

    full_name: str
    owner_login: str
    description: str | None
    homepage: str | None
    primary_language: str | None
    default_branch: str | None
    visibility: str | None
    is_fork: bool
    is_archived: bool
    is_disabled: bool
    is_template: bool | None
    github_created_at: datetime | None
    github_updated_at: datetime | None
    pushed_at: datetime | None
    topics: tuple[str, ...]
    contributors: tuple[ContributorDigest, ...]
    latest_captured_at: datetime | None
    snapshot_count: int
    first_snapshot_at: datetime | None
    metrics: RepoMetrics
    momentum: MomentumScore | None
    trend: Trend | None
    textual: Mapping[str, str]


def _num(value: int | None) -> str:
    return "unavailable" if value is None else f"{value:,}"


def _delta_line(label: str, delta: FieldDelta | None) -> str:
    if delta is None or delta.delta is None:
        return f"{label}: unavailable (need two snapshots spanning the window)"
    complete = "complete" if delta.window.complete else "partial"
    growth = (
        f", growth {delta.growth_pct:.1%}" if delta.growth_pct is not None else ""
    )
    base = "no prior baseline" if delta.base_gap_hours is None else (
        f"baseline {delta.base_value:,}"
    )
    return (
        f"{label}: {delta.delta:+,} over ~{delta.window.span_days:.0f} days "
        f"({complete}, {base}{growth})"
    )


def repository_evidence(
    digest: RepositoryDigest,
    *,
    window_days: int,
    generated_at: datetime | None = None,
) -> EvidenceBundle:
    """Evidence bundle for ``repository-summary-v1`` / ``repository-trend-v1``."""
    metrics = digest.metrics
    flags: list[str] = []
    for flag, on in (
        ("fork", digest.is_fork),
        ("archived", digest.is_archived),
        ("disabled", digest.is_disabled),
        ("template", bool(digest.is_template)),
    ):
        if on:
            flags.append(flag)

    contributors = ", ".join(
        f"{c.login} ({c.contributions:,} cumulative)"
        for c in digest.contributors[:MAX_TOP_CONTRIBUTORS]
    ) or "none stored"

    confidence = repo_window_confidence(metrics, window_days=window_days)

    items: list[EvidenceItem] = [
        EvidenceItem(
            id="",
            kind="identity",
            source="repository metadata",
            observed_at=digest.latest_captured_at,
            payload=(
                f"{digest.full_name} — described as {digest.description or 'n/a'}; "
                f"homepage {digest.homepage or 'n/a'}; primary language "
                f"{digest.primary_language or 'n/a'}; default branch "
                f"{digest.default_branch or 'n/a'}; visibility "
                f"{digest.visibility or 'n/a'}; flags: {', '.join(flags) or 'none'}; "
                f"created on GitHub {_fmt_date(digest.github_created_at)}; last "
                f"pushed {_fmt_date(digest.pushed_at)}."
            ),
        ),
        EvidenceItem(
            id="",
            kind="state",
            source="latest snapshot",
            observed_at=digest.latest_captured_at,
            payload=(
                "latest observed counters (stars, forks, watchers, open issues): "
                f"{_num(metrics.latest_counts.get('stars'))}, "
                f"{_num(metrics.latest_counts.get('forks'))}, "
                f"{_num(metrics.latest_counts.get('watchers'))}, "
                f"{_num(metrics.latest_counts.get('open_issues'))}; "
                f"captured at {_fmt_ts(digest.latest_captured_at)}."
            ),
            meta=(("snapshots", str(digest.snapshot_count)),),
        ),
        EvidenceItem(
            id="",
            kind="window-deltas",
            source="deterministic snapshot deltas",
            observed_at=digest.latest_captured_at,
            payload=_render_deltas(metrics),
        ),
        _momentum_item(digest.momentum, digest.latest_captured_at),
        _trend_item(digest.trend, digest.latest_captured_at),
        EvidenceItem(
            id="",
            kind="confidence",
            source="data-coverage confidence model",
            observed_at=digest.latest_captured_at,
            payload=(
                f"deterministic data-coverage confidence: {confidence.level} "
                f"(score {confidence.score:.2f}) from {confidence.snapshot_count} "
                f"snapshots spanning {confidence.history_days:.0f} days over the "
                f"{window_days}-day window."
            ),
        ),
        EvidenceItem(
            id="",
            kind="topics",
            source="stored topic tags",
            observed_at=digest.github_updated_at,
            payload=(
                f"tagged topics: {', '.join(digest.topics) or 'none stored'}."
            ),
        ),
        EvidenceItem(
            id="",
            kind="contributors",
            source="stored contributor rows",
            observed_at=digest.latest_captured_at,
            payload=f"top stored contributors by cumulative count: {contributors}.",
        ),
        EvidenceItem(
            id="",
            kind="history",
            source="snapshot history",
            observed_at=digest.latest_captured_at,
            payload=(
                f"history: {digest.snapshot_count} repository snapshots from "
                f"{_fmt_date(digest.first_snapshot_at)} to "
                f"{_fmt_date(digest.latest_captured_at)}."
            ),
        ),
    ]

    readme = digest.textual.get("readme")
    if readme:
        items.append(
            EvidenceItem(
                id="",
                kind="readme",
                source="repository README (text supplied by the repository owner)",
                payload=wrap_untrusted("README", readme),
            )
        )
    releases = digest.textual.get("releases")
    if releases:
        items.append(
            EvidenceItem(
                id="",
                kind="releases",
                source="latest GitHub releases (text supplied by maintainers)",
                payload=wrap_untrusted("RELEASES", releases),
            )
        )
    commits = digest.textual.get("commits")
    if commits:
        items.append(
            EvidenceItem(
                id="",
                kind="commits",
                source="latest commit messages (text supplied by contributors)",
                payload=wrap_untrusted("COMMITS", commits),
            )
        )

    return build_bundle(
        entity_type="repository",
        entity_key=digest.full_name,
        window_days=window_days,
        items=items,
        generated_at=generated_at,
    )


def _render_deltas(metrics: RepoMetrics) -> str:
    lines = [
        _delta_line("stars 1d", metrics.get("stars", 1)),
        _delta_line("stars 7d", metrics.get("stars", 7)),
        _delta_line("stars 30d", metrics.get("stars", 30)),
        _delta_line("forks 7d", metrics.get("forks", 7)),
        _delta_line("forks 30d", metrics.get("forks", 30)),
        _delta_line("open issues 7d", metrics.get("open_issues", 7)),
    ]
    return " ".join(lines)


def _momentum_item(
    momentum: MomentumScore | None, observed_at: datetime | None
) -> EvidenceItem:
    if momentum is None:
        payload = "momentum score: unavailable (no snapshot history)."
    else:
        payload = (
            f"bounded momentum score {momentum.score:.2f} "
            f"(theoretical max {MAX_SCORE:.1f}): stars-growth "
            f"{momentum.stars_growth_pct:.1%}, forks-growth "
            f"{momentum.forks_growth_pct:.1%}, recency {momentum.recency:.2f}; "
            "components: "
            + ", ".join(
                f"{name}={value:.2f}"
                for name, value in sorted(momentum.components.items())
            )
        )
    return EvidenceItem(
        id="",
        kind="momentum",
        source="deterministic momentum model",
        observed_at=observed_at,
        payload=payload,
    )


def _trend_item(trend: Trend | None, observed_at: datetime | None) -> EvidenceItem:
    if trend is None:
        payload = "trend classification: unavailable (no snapshot history)."
    else:
        payload = (
            f"trend classification: {trend.trend} ({trend.label}); "
            f"{trend.explanation}"
        )
    return EvidenceItem(
        id="", kind="trend", source="deterministic trend model",
        observed_at=observed_at, payload=payload,
    )


@dataclass(frozen=True)
class TopicRepoDigest:
    """One repository's contribution to a topic aggregate."""

    full_name: str
    stars: int | None
    momentum_score: float | None
    trend_class: str | None
    confidence_level: ConfidenceLevel | None


def topic_evidence(
    topic: str,
    aggregate: TopicAggregate,
    reports: Sequence[TopicRepoDigest],
    graph: TopicGraphReport | None,
    *,
    window_days: int,
    generated_at: datetime | None = None,
) -> EvidenceBundle:
    """Evidence bundle for ``topic-summary-v1``."""
    count = aggregate.repository_count
    total = aggregate.total_momentum
    average = aggregate.avg_momentum
    share = aggregate.share_with_momentum
    level = aggregate.confidence_level

    repo_lines = [
        (
            f"{r.full_name}: stars {_num(r.stars)}, momentum "
            f"{_fmt2(r.momentum_score)}, trend {r.trend_class or 'n/a'}, "
            f"confidence {r.confidence_level or 'LOW'}"
        )
        for r in reports[:MAX_TOPIC_REPOS]
    ]
    repo_text = "; ".join(repo_lines) or "no tracked repositories"

    items: list[EvidenceItem] = [
        EvidenceItem(
            id="",
            kind="aggregate",
            source="deterministic topic aggregation",
            payload=(
                f"tracked topic {topic!r}: {count} tagged repositories; total "
                f"momentum {_fmt2(total)}, average {_fmt2(average)}; share of "
                f"repositories with computable momentum {share:.0%}; data-coverage "
                f"confidence {level}."
            ),
        ),
        EvidenceItem(
            id="",
            kind="repositories",
            source="per-repository momentum reports",
            payload=f"repositories contributing to the topic: {repo_text}.",
        ),
    ]
    if graph is not None:
        items.append(
            EvidenceItem(
                id="",
                kind="graph-footprint",
                source="ecosystem graph (topic node)",
                payload=_render_topic_graph(graph),
            )
        )
    return build_bundle(
        entity_type="topic",
        entity_key=topic,
        window_days=window_days,
        items=items,
        generated_at=generated_at,
    )


def developer_evidence(
    report: DeveloperReport,
    graph: DeveloperGraphReport | None,
    *,
    window_days: int,
    generated_at: datetime | None = None,
) -> EvidenceBundle:
    """Evidence bundle for ``developer-summary-v1``.

    Contact/PII fields (public email, blog, twitter, company, location, bio)
    are excluded by construction — the allowlist below never touches them.
    """
    items: list[EvidenceItem] = [
        EvidenceItem(
            id="",
            kind="identity",
            source="tracked developer profile",
            observed_at=report.first_seen_at,
            payload=(
                f"developer {report.login} (display name {report.name or 'n/a'}); "
                f"followers {_num(report.followers)}; tracked since "
                f"{_fmt_date(report.first_seen_at)}; associated with "
                f"{report.associations} repository(ies): "
                f"{', '.join(report.associated_repositories[:8]) or 'n/a'}."
            ),
        ),
        EvidenceItem(
            id="",
            kind="activity",
            source="deterministic activity model",
            payload=_render_activity(report.activity),
        ),
        EvidenceItem(
            id="",
            kind="ecosystem",
            source="deterministic ecosystem score",
            payload=_render_ecosystem(report.ecosystem),
        ),
        EvidenceItem(
            id="",
            kind="emerging",
            source="deterministic emerging classification",
            payload=_render_emerging(report.emerging),
        ),
    ]

    relevances = sorted(
        report.topic_relevances, key=lambda r: r.score, reverse=True
    )[:MAX_RELATED]
    if relevances:
        items.append(
            EvidenceItem(
                id="",
                kind="topic-relevance",
                source="deterministic topic relevance scores",
                payload="top topic relevances: "
                + "; ".join(
                    (
                        f"{r.topic} score {r.score:.2f} over {r.repo_count} "
                        f"repos (confidence {r.confidence_level})"
                    )
                    for r in relevances
                )
                + ".",
            )
        )

    if graph is not None:
        items.append(
            EvidenceItem(
                id="",
                kind="graph-footprint",
                source="ecosystem graph (developer node)",
                payload=_render_developer_graph(graph),
            )
        )
    return build_bundle(
        entity_type="developer",
        entity_key=report.login,
        window_days=window_days,
        items=items,
        generated_at=generated_at,
    )


def _render_activity(activity: DeveloperActivity | None) -> str:
    if activity is None:
        return "activity: unavailable (no contribution observations)."
    if not activity.available:
        return "activity: unavailable (no usable contribution observations)."
    return (
        f"observed activity score {activity.score:.2f}; positive cumulative "
        f"contribution delta {activity.total_positive_delta}; active repositories "
        f"{activity.active_repos} of {activity.usable_repos} usable; "
        f"{activity.observations} observations; window completeness "
        f"{activity.complete_share:.0%}; data-coverage confidence "
        f"{activity.confidence_level} ({activity.confidence_score:.2f})."
    )


def _render_ecosystem(ecosystem: EcosystemScore | None) -> str:
    if ecosystem is None:
        return "ecosystem score: unavailable."
    return (
        f"ecosystem score {ecosystem.score:.2f}; associated repositories "
        f"{ecosystem.associated_repositories}; owned repositories "
        f"{ecosystem.owned_repositories}; missing signals: "
        f"{', '.join(ecosystem.missing_components) or 'none'}."
    )


def _render_emerging(emerging: EmergingDeveloper) -> str:
    return (
        f"classification: {emerging.label} (score {emerging.score:.2f}, "
        f"data-coverage confidence {emerging.confidence_level} "
        f"({emerging.confidence_score:.2f})); evidence points "
        f"{emerging.evidence_points}; explanation: {emerging.explanation}."
    )


def _render_developer_graph(graph: DeveloperGraphReport) -> str:
    reach = graph.reach
    parts = [
        f"repositories contributed to {reach.contributed_repositories}",
        f"owned {reach.owned_repositories}",
        f"distinct repositories reached {reach.reached_repositories}",
        f"distinct developers reached {reach.reached_developers}",
        f"distinct topics reached {reach.reached_topics}",
    ]
    if graph.centrality is not None:
        parts.append(
            f"degree centrality {graph.centrality.degree} (weighted "
            f"{graph.centrality.weighted_degree})"
        )
    if graph.bridge is not None:
        parts.append(
            f"bridge score {graph.bridge.bridge_score:.3f} across "
            f"{graph.bridge.meaningful_topic_memberships} topic memberships "
            f"(confidence {graph.bridge.confidence.score:.2f} "
            f"{graph.bridge.confidence.level}); small-sample capped: "
            f"{graph.bridge.small_sample}"
        )
    if graph.co_contributors:
        top = "; ".join(
            (
                f"{c.login} ({c.shared_repository_count} shared repos, "
                f"strength {c.strength:.3f})"
            )
            for c in graph.co_contributors[:3]
        )
        parts.append(f"strongest co-contributors: {top}.")
    return "graph footprint: " + "; ".join(parts) + "."


def _render_topic_graph(graph: TopicGraphReport) -> str:
    reach = graph.reach
    parts = [
        f"repositories {reach.repositories}",
        f"contributing developers {reach.contributing_developers}",
        f"owning developers {reach.owning_developers}",
        f"associated developers {reach.associated_developers}",
    ]
    if graph.centrality is not None:
        parts.append(
            f"degree centrality {graph.centrality.degree} (weighted "
            f"{graph.centrality.weighted_degree})"
        )
    if graph.related_topics:
        top = "; ".join(
            (
                f"{r.topic} ({r.shared_repository_count} shared repos, "
                f"repo jaccard {r.repository_overlap.jaccard:.2f})"
            )
            for r in graph.related_topics[:3]
        )
        parts.append(f"related topics with shared evidence: {top}.")
    return "graph footprint: " + "; ".join(parts) + "."


def ecosystem_evidence(
    report: EcosystemGraphReport,
    *,
    window_days: int,
    generated_at: datetime | None = None,
) -> EvidenceBundle:
    """Evidence bundle for ``ecosystem-summary-v1``."""
    parts = [
        f"developers {report.developer_count}",
        f"repositories {report.repository_count}",
        f"topics {report.topic_count}",
        f"owns edges {report.owns_edges}",
        f"contributes_to edges {report.contributes_edges}",
        f"tagged_with edges {report.tagged_edges}",
        f"connected components {report.component_count}",
    ]
    if report.largest_component is not None:
        largest = report.largest_component
        parts.append(
            "largest component "
            f"{largest.node_count} nodes ({largest.developer_count} developers, "
            f"{largest.repository_count} repositories, {largest.topic_count} topics)"
        )

    items: list[EvidenceItem] = [
        EvidenceItem(
            id="",
            kind="scale",
            source="ecosystem graph (whole tracked dataset)",
            payload="tracked ecosystem scale: " + "; ".join(parts) + ".",
        )
    ]
    if report.top_developer_centrality:
        items.append(
            EvidenceItem(
                id="",
                kind="developer-hubs",
                source="degree centrality (developers)",
                payload=_render_hubs(report.top_developer_centrality[:MAX_ECOSYSTEM_HUBS]),
            )
        )
    if report.top_repository_centrality:
        items.append(
            EvidenceItem(
                id="",
                kind="repository-hubs",
                source="degree centrality (repositories)",
                payload=_render_hubs(report.top_repository_centrality[:MAX_ECOSYSTEM_HUBS]),
            )
        )
    if report.top_topic_centrality:
        items.append(
            EvidenceItem(
                id="",
                kind="topic-hubs",
                source="degree centrality (topics)",
                payload=_render_hubs(report.top_topic_centrality[:MAX_ECOSYSTEM_HUBS]),
            )
        )
    if report.top_developer_bridges:
        items.append(
            EvidenceItem(
                id="",
                kind="bridges",
                source="developer bridge intelligence",
                payload="top developer bridges: "
                + "; ".join(
                    (
                        f"{b.login} bridge {b.bridge_score:.3f} over "
                        f"{b.meaningful_topic_memberships} topic memberships "
                        f"({b.distinct_supporting_repositories} repos, "
                        f"confidence {b.confidence.score:.2f} {b.confidence.level})"
                    )
                    for b in report.top_developer_bridges[:MAX_BRIDGES]
                )
                + ".",
            )
        )
    return build_bundle(
        entity_type="ecosystem",
        entity_key="ecosystem",
        window_days=window_days,
        items=items,
        generated_at=generated_at,
    )


def _render_hubs(rows: Sequence[NodeCentrality]) -> str:
    return "; ".join(
        (
            f"{item.label}: degree {item.degree}, weighted degree "
            f"{item.weighted_degree}"
        )
        for item in rows
    ) + "."


def bridge_evidence(
    report: CrossTopicBridgeReport,
    *,
    window_days: int,
    generated_at: datetime | None = None,
) -> EvidenceBundle:
    """Evidence bundle for ``bridge-summary-v1``."""
    shared = ", ".join(report.shared_tracked_repositories) or "none"
    items: list[EvidenceItem] = [
        EvidenceItem(
            id="",
            kind="bridge-aggregate",
            source="cross-topic bridge intelligence",
            payload=(
                f"bridge ecosystem between {report.topic_a} and {report.topic_b}: "
                f"{report.distinct_bridging_developers} distinct bridging developers "
                f"across {report.bridge_count} bridge instances, average bridge "
                f"score {report.average_bridge_score:.3f}; shared tracked "
                f"repositories: {shared}."
            ),
        )
    ]
    if report.top_bridges:
        lines: list[str] = []
        for bridge in report.top_bridges[:MAX_BRIDGES]:
            lines.append(
                f"{bridge.login}: bridge {bridge.bridge_score:.3f}, confidence "
                f"{bridge.confidence.score:.2f} ({bridge.confidence.level}); "
                f"{bridge.topic_a.repository_count} repos in {bridge.topic_a.topic}, "
                f"{bridge.topic_b.repository_count} repos in {bridge.topic_b.topic}; "
                f"shared evidence repositories: "
                f"{', '.join(bridge.shared_tracked_repositories) or 'none'}."
            )
        items.append(
            EvidenceItem(
                id="",
                kind="bridges",
                source="per-developer bridge calculations",
                payload=" ".join(lines),
            )
        )
    return build_bundle(
        entity_type="bridge",
        entity_key=f"{report.topic_a}⇄{report.topic_b}",
        window_days=window_days,
        items=items,
        generated_at=generated_at,
    )


def _fmt2(value: float | int | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _fmt_date(value: datetime | None) -> str:
    return "unknown" if value is None else value.date().isoformat()


def _fmt_ts(value: datetime | None) -> str:
    return "unknown" if value is None else value.isoformat(timespec="seconds")


__all__ = [
    "ContributorDigest",
    "RepositoryDigest",
    "TopicRepoDigest",
    "bridge_evidence",
    "developer_evidence",
    "ecosystem_evidence",
    "repository_evidence",
    "topic_evidence",
]