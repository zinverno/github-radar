"""The github-radar Typer CLI.

Commands:
    init-db             create database schema via Alembic migrations
    discover            search GitHub and store repositories
    update              re-observe tracked repositories
    repos               list tracked repositories
    repo                show one repository in detail
    stats               dataset statistics
    trending            rank tracked repositories by momentum
    topics              aggregate tracked topics
    topic               topic repositories + developer intelligence
    developers          list developer intelligence (relevance/activity/e…)   
    developer           show one developer's full intelligence digest
    emerging-developers list developers classified EMERGING
    ecosystem           whole tracked ecosystem graph (nodes, edges, hubs, bridges)
    bridges             bridge intelligence: develop / cross-topic / repos
    related-topics      a topic's graph footprint and related topics
    related-repos       a repository's graph footprint and related repositories
    graph               ecosystem graph analytics (includes bridges)
    ai                  LLM synthesis of measured facts (repo/topic/developer/…)
    rate-limit          show current GitHub API quota
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import typer
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from github_radar.ai.cache import SqlArtifactStore
from github_radar.ai.confidence import (
    bridge_level,
    developer_level,
    ecosystem_level,
    repo_level,
    topic_aggregate_level,
)
from github_radar.ai.errors import AIConfigurationError, AIError
from github_radar.ai.evidence import (
    ContributorDigest,
    RepositoryDigest,
    TopicRepoDigest,
    bridge_evidence,
    developer_evidence,
    ecosystem_evidence,
    repository_evidence,
    topic_evidence,
)
from github_radar.ai.models import (
    AIArtifact,
    ArtifactType,
    ConfidenceLevel,
    EvidenceBundle,
)
from github_radar.ai.openai_compatible import make_provider
from github_radar.ai.prompts import PROMPT_VERSIONS
from github_radar.ai.provider import AIProvider
from github_radar.ai.service import (
    RunBudget,
    SynthesisRequest,
    SynthesisResult,
    synthesize,
)
from github_radar.ai.textual import TextualEvidence
from github_radar.analytics import (
    WEIGHTS,
    DeveloperProfileMetrics,
    FieldDelta,
    ProfileFieldDelta,
    RepositoryReport,
    aggregate_topics,
    compute_contribution_delta,
    compute_metrics,
    compute_momentum,
    rank_repositories,
)
from github_radar.analytics.trends import classify_trend
from github_radar.config import Settings, SettingsError, get_settings
from github_radar.discovery import DiscoveryReport, RepositoryDiscoveryService
from github_radar.github import (
    GitHubClient,
    GitHubConfigurationError,
    RateLimitExceeded,
)
from github_radar.graph.bridges import (
    DeveloperBridge,
    compute_developer_bridge,
    developer_bridges,
    repository_bridges,
)
from github_radar.graph.loader import load_graph
from github_radar.graph.metrics import NodeCentrality
from github_radar.graph.model import EcosystemGraph
from github_radar.graph.reports import (
    DEFAULT_CENTRALITY_LIMIT,
    CrossTopicBridgeReport,
    DeveloperGraphReport,
    EcosystemGraphReport,
    RepositoryGraphReport,
    TopicGraphReport,
    build_cross_topic_bridge_report,
    build_developer_report,
    build_ecosystem_report,
    build_repository_report,
    build_topic_report,
)
from github_radar.services import (
    DeveloperDataset,
    DeveloperIntelligenceService,
    DeveloperReport,
    RepositoryUpdateService,
)
from github_radar.storage import repositories as storage
from github_radar.storage.ai_artifacts import serialize_evidence
from github_radar.storage.db import make_engine, make_session_factory
from github_radar.util import utcnow

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"

app = typer.Typer(
    name="github-radar",
    help="GitHub ecosystem intelligence: discover, track and analyze "
    "repositories over time.",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _fmt(value: object, dash: str = "—") -> str:
    if value is None:
        return dash
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _print_table(headers: list[str], rows: list[list[object]]) -> None:
    rendered = [[_fmt(c) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in rendered:
        widths = [max(w, len(c)) for w, c in zip(widths, row)]
    fmt_ = "  ".join(f"{{:<{w}}}" for w in widths)
    print()
    print(fmt_.format(*headers).rstrip())
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in rendered:
        print(fmt_.format(*row).rstrip())


# ---------------------------------------------------------------------------
# Infrastructure helpers
# ---------------------------------------------------------------------------

def _make_client(settings: Settings, *, require_auth: bool) -> GitHubClient:
    if require_auth:
        settings.require_github_token()
    return GitHubClient(settings)


async def _engine_and_session(
    settings: Settings,
) -> tuple[AsyncEngine, AsyncSession]:
    database_url = settings.require_database_url()
    engine = make_engine(database_url)
    factory = make_session_factory(engine)
    return engine, factory()


def _run(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except (SettingsError, GitHubConfigurationError) as exc:
        typer.secho(
            f"Configuration error: {exc}\n  Configure the missing value in your "
            ".env file (see .env.example).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    except AIConfigurationError as exc:
        typer.secho(
            f"AI is not configured: {exc}\n"
            "  Set AI_API_KEY, AI_BASE_URL and AI_MODEL in your .env file "
            "(see .env.example) to use AI synthesis commands.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=1)
    except AIError as exc:
        typer.secho(
            f"AI synthesis failed: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    except RateLimitExceeded as exc:
        reset = (
            exc.reset_at.isoformat(timespec="seconds") if exc.reset_at else "unknown"
        )
        typer.secho(
            f"GitHub rate limit reached ({exc.resource}).\n"
            f"  Remaining: {exc.remaining if exc.remaining is not None else 'unknown'}\n"
            f"  Window resets at: {reset}\n"
            f"  Some data may already be saved; run again later to continue.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)


async def _housekeeping(
    client: GitHubClient, engine: AsyncEngine, session: AsyncSession
) -> None:
    await client.close()
    await session.close()
    await engine.dispose()


# ---------------------------------------------------------------------------
# init-db
# ---------------------------------------------------------------------------

@app.command("init-db")
def init_db() -> None:
    """Create or upgrade the database schema (runs Alembic migrations)."""
    settings = get_settings()
    database_url = settings.require_database_url()
    if not ALEMBIC_INI.exists():
        typer.secho(
            f"alembic.ini not found at {ALEMBIC_INI}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    typer.secho("Applying Alembic migrations...", fg=typer.colors.CYAN)
    alembic_command.upgrade(cfg, "head")
    typer.secho("Database schema is up to date.", fg=typer.colors.GREEN)


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------

@app.command()
def discover(
    query: str = typer.Argument(..., help="Search query text (e.g. 'mcp')."),
    language: str | None = typer.Option(
        None, "--language", "-l", help="Restrict by primary language, e.g. python."
    ),
    topic: str | None = typer.Option(
        None, "--topic", "-t", help="Include only results with this topic."
    ),
    min_stars: int | None = typer.Option(
        None, "--min-stars", "-m", help="Minimum star count."
    ),
    created_after: str | None = typer.Option(
        None,
        "--created-after",
        help="Only repositories created on/after this date (YYYY-MM-DD).",
    ),
    limit: int | None = typer.Option(
        None, "--limit", help="Maximum repositories to store in this run."
    ),
    contributors_limit: int | None = typer.Option(
        None, "--contributors-limit", help="Max contributor rows per repository."
    ),
    sort: str | None = typer.Option(
        None,
        "--sort",
        help="Search sort field: stars, best-match, forks, updated, ...",
    ),
    order: str | None = typer.Option(
        None, "--order", help="asc or desc."
    ),
) -> None:
    """Search GitHub repositories and store what is discovered."""
    settings = get_settings()

    created: date | None = None
    if created_after:
        try:
            created = date.fromisoformat(created_after)
        except ValueError:
            typer.secho(
                f"Invalid --created-after date: {created_after!r}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)

    if limit is None:
        limit = settings.discover_default_limit
    if limit <= 0 or limit > settings.discover_max_limit:
        typer.secho(
            f"--limit must be between 1 and {settings.discover_max_limit} "
            f"(got {limit}).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    eff_contributors = contributors_limit or settings.contributors_limit_per_repo

    _run(
        _discover_run(
            settings=settings,
            query=query,
            language=language,
            topic=topic,
            min_stars=min_stars,
            created_after=created,
            limit=limit,
            contributors_limit=eff_contributors,
            sort=sort or "stars",
            order=order or "desc",
        )
    )


async def _discover_run(
    *,
    settings: Settings,
    query: str,
    language: str | None,
    topic: str | None,
    min_stars: int | None,
    created_after: date | None,
    limit: int,
    contributors_limit: int,
    sort: str,
    order: str,
) -> None:
    client = _make_client(settings, require_auth=True)
    engine, session = await _engine_and_session(settings)
    try:
        service = RepositoryDiscoveryService(
            client,
            session,
            contributors_limit=contributors_limit,
            refresh_days=settings.profile_refresh_days,
        )
        report = await service.discover(
            query=query,
            language=language,
            topic=topic,
            min_stars=min_stars,
            created_after=created_after,
            limit=limit,
            sort=sort,
            order=order,
        )
        _print_report(report)
    finally:
        await _housekeeping(client, engine, session)


def _print_report(report: DiscoveryReport) -> None:
    _print_table(
        ["Metric", "Value"],
        [
            ["Query", report.query],
            ["Candidates seen", report.candidates_seen],
            ["Newly discovered", report.discovered],
            ["Metadata updated", report.updated],
            ["Snapshots inserted", report.snapshots_inserted],
            ["Contributor rows added", report.contributors_added],
            ["Developer profiles fetched", report.developer_profiles_fetched],
        ],
    )
    if report.discovered == 0 and report.updated == 0:
        print("\nNo repositories were stored; try broadening the query.")


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

@app.command()
def update(
    limit: int | None = typer.Option(
        None, "--limit", help="Max repositories to refresh."
    ),
) -> None:
    """Re-observe tracked repositories and store new snapshots."""
    settings = get_settings()
    _run(_update_run(settings, limit=limit))


async def _update_run(settings: Settings, *, limit: int | None) -> None:
    client = _make_client(settings, require_auth=True)
    engine, session = await _engine_and_session(settings)
    try:
        report = await RepositoryUpdateService(
            client, session, refresh_days=settings.profile_refresh_days
        ).update(limit=limit)
        _print_table(
            ["Metric", "Value"],
            [
                ["Repositories to refresh", report.repositories_to_update],
                ["Repositories refreshed", report.repositories_refreshed],
                ["New snapshots", report.snapshots_inserted],
                ["Contributor rows added", report.contributors_added],
                ["Developer profiles fetched", report.developer_profiles_fetched],
            ],
        )
    finally:
        await _housekeeping(client, engine, session)


# ---------------------------------------------------------------------------
# repos
# ---------------------------------------------------------------------------

@app.command()
def repos(
    limit: int | None = typer.Option(None, "--limit", help="Max rows."),
    offset: int = typer.Option(0, "--offset", help="Skip first N rows."),
) -> None:
    """List tracked repositories with their latest counters."""
    settings = get_settings()
    _run(_repos_run(settings, limit=limit, offset=offset))


async def _repos_run(settings: Settings, *, limit: int | None, offset: int) -> None:
    engine, session = await _engine_and_session(settings)
    try:
        rows = await storage.list_repos_with_latest(
            session, limit=limit, offset=offset
        )
        if not rows:
            print("No repositories tracked yet — run 'github-radar discover'.")
            return
        _print_table(
            ["Repository", "Language", "Stars", "Forks", "Pushed at", "First seen"],
            [
                [
                    repo.full_name,
                    repo.primary_language or "",
                    latest.stars if latest else None,
                    latest.forks if latest else None,
                    latest.pushed_at if latest else repo.pushed_at,
                    repo.first_seen_at,
                ]
                for repo, latest in rows
            ],
        )
    finally:
        await session.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# repo
# ---------------------------------------------------------------------------

@app.command("repo")
def show_repo(
    owner_repo: str = typer.Argument(..., help="Repository as OWNER/NAME."),
) -> None:
    """Show metadata, topics, contributors and metrics for ONE repository."""
    settings = get_settings()
    parts = owner_repo.strip("/").split("/")
    if len(parts) != 2:
        typer.secho(
            f"Expected OWNER/NAME, got {owner_repo!r}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    _run(_repo_run(settings, owner=parts[0], name=parts[1]))


async def _repo_run(settings: Settings, *, owner: str, name: str) -> None:
    engine, session = await _engine_and_session(settings)
    try:
        full_name = f"{owner}/{name}"
        row = await storage.get_repository_by_full_name(session, full_name)
        if row is None:
            typer.secho(
                f"{full_name} is not tracked yet.\n"
                f'  Discover it with: github-radar discover --topic {owner} "{name}"',
                fg=typer.colors.YELLOW,
            )
            return

        topics = await storage.topics_for_repository(session, row.id)
        contributors = await storage.contributors_for_repository(session, row.id)
        latest = await storage.latest_snapshot(session, row.id)
        snapshots = await storage.snapshots_for_repository(session, row.id)
        metrics = compute_metrics([s.to_domain() for s in snapshots])
        reference_now = latest.captured_at if latest is not None else None
        momentum = (
            compute_momentum(metrics, reference_now=reference_now)
            if reference_now is not None
            else None
        )

        flags = ",".join(
            flag
            for flag, on in (
                ("fork", row.is_fork),
                ("archived", row.is_archived),
                ("disabled", row.is_disabled),
                ("template", row.is_template),
            )
            if on
        )

        _print_table(
            ["Field", "Value"],
            [
                ["full_name", row.full_name],
                ["github_id", row.github_id],
                ["owner", row.owner_login],
                ["description", row.description],
                ["url", row.html_url],
                ["homepage", row.homepage],
                ["default_branch", row.default_branch],
                ["language", row.primary_language],
                ["flags", flags or "—"],
                ["visibility", row.visibility],
                ["github created_at", row.created_at],
                ["github updated_at", row.updated_at],
                ["pushed_at", row.pushed_at],
                ["first_seen_at", row.first_seen_at],
                ["last_seen_at", row.last_seen_at],
                ["snapshots stored", len(snapshots)],
            ],
        )

        _print_table(["Topic"], [[t.name] for t in topics])

        if latest is not None:
            _print_table(
                ["Captured at", "Stars", "Forks", "Watchers", "Open issues",
                 "Size (KB)", "Pushed at"],
                [
                    [
                        latest.captured_at, latest.stars, latest.forks,
                        latest.watchers, latest.open_issues, latest.size_kb,
                        latest.pushed_at,
                    ]
                ],
            )
        else:
            print("\nNo snapshot available yet.")

        metric_rows: list[list[object]] = []
        for field, alias in (("stars", "stars"), ("forks", "forks")):
            for label, delta in (
                ("1", metrics.get(field, 1)),
                ("7", metrics.get(field, 7)),
                ("30", metrics.get(field, 30)),
            ):
                metric_rows.append(_metric_row(f"{alias}_delta_{label}d", delta))
        _print_table(
            ["Metric", "Value", "Window days", "Complete"], metric_rows
        )

        if momentum is not None:
            _print_table(
                ["Component", "Raw value", "Weight", "Contribution"],
                [
                    ["stars growth %", f"{momentum.stars_growth_pct:.2f}",
                     WEIGHTS["stars_growth"],
                     f"{momentum.components['stars_growth']:.2f}"],
                    ["forks growth %", f"{momentum.forks_growth_pct:.2f}",
                     WEIGHTS["forks_growth"],
                     f"{momentum.components['forks_growth']:.2f}"],
                    ["recency (0..1)", f"{momentum.recency:.2f}",
                     WEIGHTS["recency"],
                     f"{momentum.components['recency']:.2f}"],
                    ["TOTAL", "", "", f"{momentum.score:.2f}"],
                ],
            )

        _print_table(
            ["Login", "Name", "Contributions", "Last seen"],
            [
                [dev.login, dev.name or "—", contribs, last_seen]
                for dev, contribs, last_seen in contributors
            ],
        )
    finally:
        await session.close()
        await engine.dispose()


def _metric_row(name: str, delta: FieldDelta | None) -> list[object]:
    if delta is None:
        return [name, "unavailable", "—", "—"]
    return [
        name,
        delta.delta,
        f"{delta.window.span_days:.1f}",
        "yes" if delta.window.complete else "partial",
    ]


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

@app.command()
def stats() -> None:
    """Show dataset statistics."""
    settings = get_settings()
    _run(_stats_run(settings))


async def _stats_run(settings: Settings) -> None:
    engine, session = await _engine_and_session(settings)
    try:
        s = await storage.compute_stats(session)
        _print_table(
            ["Metric", "Value"],
            [
                ["repositories tracked", s.repositories],
                ["developers tracked", s.developers],
                ["topics tracked", s.topics],
                ["snapshots stored", s.snapshots],
                ["oldest snapshot", _fmt(s.oldest_snapshot_at)],
                ["newest snapshot", _fmt(s.newest_snapshot_at)],
            ],
        )
    finally:
        await session.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# trending / topics / topic
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _TrackedRepo:
    """One tracked repository with its analytics digest and topic names."""

    report: RepositoryReport
    topics: tuple[str, ...]


async def _collect_tracked(session: AsyncSession) -> list[_TrackedRepo]:
    """Build the per-repository analytics digest used by all three commands.

    ``reference_now`` is always the newest snapshot's ``captured_at`` — a real
    datetime anchored to observed history, never wall-clock time — so momentum
    stays deterministic and reproducible between runs.
    """
    rows = await storage.list_repos_with_latest(session)
    tracked: list[_TrackedRepo] = []
    for repo, latest in rows:
        names = tuple(
            t.name for t in await storage.topics_for_repository(session, repo.id)
        )
        snapshots = await storage.snapshots_for_repository(session, repo.id)
        metrics = compute_metrics([s.to_domain() for s in snapshots])
        reference_now = (
            latest.captured_at if latest is not None else metrics.latest_captured_at
        )
        momentum = (
            compute_momentum(metrics, reference_now=reference_now)
            if reference_now is not None
            else None
        )
        tracked.append(
            _TrackedRepo(
                report=RepositoryReport(
                    full_name=repo.full_name,
                    metrics=metrics,
                    momentum=momentum,
                ),
                topics=names,
            )
        )
    return tracked


@app.command()
def trending(
    limit: int = typer.Option(
        10, "--limit", "-n", help="How many to show."
    ),
) -> None:
    """Rank tracked repositories by momentum score (descending)."""
    settings = get_settings()
    _run(_trending_run(settings, limit=limit))


async def _trending_run(settings: Settings, *, limit: int) -> None:
    engine, session = await _engine_and_session(settings)
    try:
        tracked = await _collect_tracked(session)
        ranked = rank_repositories([item.report for item in tracked])
        _print_table(
            ["Repository", "Stars", "Momentum", "Stars Δ%", "Forks Δ%", "Recency",
             "Confidence"],
            [
                [
                    r.full_name,
                    r.stars,
                    r.score,
                    r.stars_growth_pct,
                    r.forks_growth_pct,
                    r.recency,
                    r.confidence,
                ]
                for r in ranked[:limit]
            ],
        )
    finally:
        await session.close()
        await engine.dispose()


@app.command("topics")
def topics_command(
    limit: int = typer.Option(
        10, "--limit", "-n", help="How many topics to show."
    ),
) -> None:
    """Aggregate tracked topics by repository coverage and momentum."""
    settings = get_settings()
    _run(_topics_run(settings, limit=limit))


async def _topics_run(settings: Settings, *, limit: int) -> None:
    engine, session = await _engine_and_session(settings)
    try:
        tracked = await _collect_tracked(session)
        by_topic: dict[str, list[RepositoryReport]] = {}
        for item in tracked:
            for name in item.topics:
                by_topic.setdefault(name, []).append(item.report)
        aggregates = aggregate_topics(by_topic)
        _print_table(
            ["Topic", "Repos", "Total momentum", "Avg momentum", "Momentum share",
             "Confidence"],
            [
                [
                    a.name,
                    a.repository_count,
                    a.total_momentum,
                    a.avg_momentum,
                    a.share_with_momentum,
                    a.confidence_level,
                ]
                for a in aggregates[:limit]
            ],
        )
    finally:
        await session.close()
        await engine.dispose()


@app.command("topic")
def topic_command(
    topic: str = typer.Argument(..., help="Topic name, e.g. mcp."),
    limit: int = typer.Option(
        10, "--limit", "-n", help="How many to show."
    ),
) -> None:
    """List repositories for a topic, ranked by momentum."""
    settings = get_settings()
    _run(_topic_run(settings, topic=topic, limit=limit))


async def _topic_run(settings: Settings, *, topic: str, limit: int) -> None:
    engine, session = await _engine_and_session(settings)
    try:
        topic_name = storage.normalize_topic(topic)
        tracked = await _collect_tracked(session)
        matched = [item.report for item in tracked if topic_name in item.topics]
        ranked = rank_repositories(matched)
        _print_table(
            ["Repository", "Stars", "Momentum", "Stars Δ%", "Recency",
             "Confidence"],
            [
                [
                    r.full_name,
                    r.stars,
                    r.score,
                    r.stars_growth_pct,
                    r.recency,
                    r.confidence,
                ]
                for r in ranked[:limit]
            ],
        )

        _dataset, reports = await _build_developer_reports(session)
        by_topic: list[tuple[DeveloperReport, float]] = []
        for report in reports:
            relevance = report.relevance_for(topic_name)
            if relevance is not None:
                by_topic.append((report, relevance.score))
        by_topic.sort(key=lambda item: item[1], reverse=True)
        if by_topic:
            _print_table(
                ["Developer", "Followers", "Relevance", "Activity", "Emerging",
                 "Confidence"],
                [
                    [
                        report.login,
                        report.followers,
                        relevance_score,
                        (
                            report.activity.score
                            if report.activity is not None
                            and report.activity.available
                            else None
                        ),
                        report.emerging.label,
                        f"{report.emerging.confidence_level} "
                        f"({report.emerging.confidence_score:.2f})",
                    ]
                    for report, relevance_score in by_topic[:limit]
                ],
            )

        graph, _reference = await _load_graph(session)
        node = graph.topic_by_name(topic_name)
        if node is not None:
            graph_report = build_topic_report(graph, node.topic_id)
            _print_topic_graph(graph_report)
    finally:
        await session.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# developers / developer / emerging-developers
# ---------------------------------------------------------------------------

VALID_WINDOWS_DAYS = (1, 7, 30)


def _parse_window_days(window: str) -> int:
    """Parse ``1d``/``7d``/``30d`` (bare integers are accepted too)."""
    raw = window.strip().lower()
    value = raw[:-1] if raw.endswith("d") else raw
    try:
        days = int(value)
    except ValueError:
        typer.secho(
            f"Invalid --window value: {window!r} " f"(use one of 1d, 7d, 30d).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    if days not in VALID_WINDOWS_DAYS:
        typer.secho(
            f"--window must be one of 1d, 7d, 30d (got {window!r}).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    return days


def _relevance_value(report: DeveloperReport, topic: str | None) -> float | None:
    if topic is not None:
        relevance = report.relevance_for(topic)
        return relevance.score if relevance is not None else None
    scores = [r.score for r in report.topic_relevances]
    return max(scores) if scores else None


def _activity_score(report: DeveloperReport) -> float:
    activity = report.activity
    if activity is not None and activity.available:
        return activity.score
    return -1.0


def _ecosystem_score(report: DeveloperReport) -> float:
    return report.ecosystem.score if report.ecosystem is not None else -1.0


def _relevance_sort_value(report: DeveloperReport, topic: str | None) -> float:
    value = _relevance_value(report, topic)
    return value if value is not None else -1.0


async def _build_developer_reports(
    session: AsyncSession, *, window_days: int = 7
) -> tuple[DeveloperDataset, tuple[DeveloperReport, ...]]:
    service = DeveloperIntelligenceService(session)
    dataset = await service.load_dataset()
    reports = service.build_reports(dataset, window_days=window_days)
    return dataset, reports


@app.command("developers")
def developers_command(
    topic: str | None = typer.Option(
        None, "--topic", "-t", help="Only developers relevant to this topic."
    ),
    sort: str = typer.Option(
        "relevance",
        "--sort",
        help="Sort field: relevance, activity, ecosystem, emerging, followers.",
    ),
    window: str = typer.Option(
        "7d", "--window", help="Activity window: 1d, 7d, or 30d."
    ),
    limit: int = typer.Option(10, "--limit", "-n", help="How many to show."),
    min_confidence: float | None = typer.Option(
        None, "--min-confidence", help="Minimum emerging confidence (0..1)."
    ),
    min_followers: int | None = typer.Option(
        None, "--min-followers", help="Minimum current followers."
    ),
    has_public_contact: bool = typer.Option(
        False,
        "--has-public-contact",
        help="Only developers exposing at least one public contact.",
    ),
    repository: str | None = typer.Option(
        None, "--repository", help="Only developers contributing to a repository."
    ),
    language: str | None = typer.Option(
        None, "--language", help="Only developers associated with this language."
    ),
) -> None:
    """List tracked developers with their intelligence scores."""
    settings = get_settings()
    _run(
        _developers_run(
            settings,
            topic=topic,
            sort=sort,
            window=_parse_window_days(window),
            limit=limit,
            min_confidence=min_confidence,
            min_followers=min_followers,
            has_public_contact=has_public_contact,
            repository=repository,
            language=language,
        )
    )


async def _developers_run(
    settings: Settings,
    *,
    topic: str | None,
    sort: str,
    window: int,
    limit: int,
    min_confidence: float | None,
    min_followers: int | None,
    has_public_contact: bool,
    repository: str | None,
    language: str | None,
) -> None:
    if sort not in ("relevance", "activity", "ecosystem", "emerging", "followers"):
        typer.secho(
            f"--sort must be one of relevance, activity, ecosystem, emerging, "
            f"followers (got {sort!r}).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    if min_confidence is not None and not 0.0 <= min_confidence <= 1.0:
        typer.secho(
            f"--min-confidence must be between 0 and 1 (got {min_confidence}).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    topic = storage.normalize_topic(topic) if topic else None

    engine, session = await _engine_and_session(settings)
    try:
        _dataset, reports = await _build_developer_reports(
            session, window_days=window
        )
        kept = [
            report
            for report in reports
            if _passes_filters(
                report,
                topic=topic,
                language=language,
                repository=repository,
                min_followers=min_followers,
                min_confidence=min_confidence,
                has_public_contact=has_public_contact,
            )
        ]
        if sort == "followers":
            kept.sort(key=lambda r: (r.followers is not None, r.followers), reverse=True)
        elif sort == "activity":
            kept.sort(key=lambda r: -_activity_score(r))
        elif sort == "ecosystem":
            kept.sort(key=lambda r: -_ecosystem_score(r))
        elif sort == "emerging":
            kept.sort(key=lambda r: -r.emerging.score)
        else:
            kept.sort(key=lambda r: -_relevance_sort_value(r, topic))

        if not kept:
            print("No developers matched the filters — run 'github-radar update' "
                  "or relax the filters.")
            return
        _print_table(
            ["Login", "Followers", "Relevance", "Activity", "Ecosystem",
             "Emerging", "Confidence"],
            [
                [
                    report.login,
                    report.followers,
                    _relevance_value(report, topic),
                    (
                        report.activity.score
                        if report.activity is not None and report.activity.available
                        else None
                    ),
                    report.ecosystem.score if report.ecosystem is not None else None,
                    report.emerging.label,
                    f"{report.emerging.confidence_level} "
                    f"({report.emerging.confidence_score:.2f})",
                ]
                for report in kept[:limit]
            ],
        )
    finally:
        await session.close()
        await engine.dispose()


def _passes_filters(
    report: DeveloperReport,
    *,
    topic: str | None,
    language: str | None,
    repository: str | None,
    min_followers: int | None,
    min_confidence: float | None,
    has_public_contact: bool,
) -> bool:
    if topic is not None and report.relevance_for(topic) is None:
        return False
    if language is not None:
        langs = {lang.lower() for lang in report.languages if lang is not None}
        if language.lower() not in langs:
            return False
    if repository is not None:
        wanted = repository.lower()
        if not any(wanted in name.lower() for name in report.associated_repositories):
            return False
    if min_followers is not None:
        if report.followers is None or report.followers < min_followers:
            return False
    if min_confidence is not None:
        if report.emerging.confidence_score < min_confidence:
            return False
    if has_public_contact and not report.public_contacts.available:
        return False
    return True


@app.command("developer")
def developer_command(
    login: str = typer.Argument(..., help="GitHub login of the developer."),
    window: str = typer.Option(
        "7d", "--window", help="Activity window: 1d, 7d, or 30d."
    ),
) -> None:
    """Show one developer's profile, activity and emerging classification."""
    settings = get_settings()
    _run(
        _developer_run(
            settings, login=login.strip().lower(), window=_parse_window_days(window)
        )
    )


async def _developer_run(
    settings: Settings, *, login: str, window: int
) -> None:
    engine, session = await _engine_and_session(settings)
    try:
        dataset, reports = await _build_developer_reports(
            session, window_days=window
        )
        report = next(
            (report for report in reports if report.login.lower() == login),
            None,
        )
        if report is None:
            typer.secho(
                f"No tracked developer named {login!r}.\n"
                f"  Run 'github-radar update' to observe contributor profiles.",
                fg=typer.colors.YELLOW,
            )
            return
        _print_developer_report(dataset, report, window_days=window)

        graph, reference_now = await _load_graph(session)
        node = graph.developer_by_login(report.login)
        if node is not None:
            graph_report = build_developer_report(
                graph,
                node.developer_id,
                reference_now=reference_now,
                window_days=window,
            )
            _print_developer_graph(graph_report)
    finally:
        await session.close()
        await engine.dispose()


def _profile_delta(
    metrics: DeveloperProfileMetrics | None,
    field_name: str,
    days: int,
) -> ProfileFieldDelta | None:
    if metrics is None:
        return None
    if field_name == "followers":
        return metrics.followers_delta(days)
    if field_name == "following":
        return metrics.following_delta(days)
    return metrics.public_repos_delta(days)


def _print_developer_report(
    dataset: DeveloperDataset,
    report: DeveloperReport,
    *,
    window_days: int,
) -> None:
    contacts = report.public_contacts
    _print_table(
        ["Field", "Value"],
        [
            ["login", report.login],
            ["name", report.name or "—"],
            ["followers", report.followers],
            ["first seen", report.first_seen_at],
            ["associated repositories", report.associations],
            ["public contacts",
             ", ".join(contacts.available) if contacts.available else "—"],
        ],
    )

    if contacts.available:
        _print_table(
            ["Channel", "Value"],
            [
                ["github", contacts.github],
                ["public_email", contacts.public_email],
                ["website", contacts.website],
                ["twitter", contacts.twitter],
            ],
        )

    m = report.profile
    headers = ["Metric", "1d", "7d", "30d"]
    rows: list[list[object]] = []
    for field_name in ("followers", "following", "public_repos"):
        cells: list[object] = [field_name]
        for days in (1, 7, 30):
            delta = _profile_delta(m, field_name, days)
            if delta is None or delta.delta is None:
                cells.append("—")
            else:
                marker = "" if delta.complete else "*"
                cells.append(f"{delta.delta:+d}{marker}")
        rows.append(cells)
    _print_table(headers, rows)

    activity = report.activity
    _print_table(
        ["Activity", "Value"],
        [
            ["available",
             "yes" if activity is not None and activity.available else "no"],
            ["score", activity.score if activity is not None else None],
            ["positive delta (cumulative)",
             activity.total_positive_delta if activity is not None else None],
            ["active repositories",
             activity.active_repos if activity is not None else None],
            ["usable relationships",
             activity.usable_repos if activity is not None else None],
            ["observations",
             activity.observations if activity is not None else None],
            ["window completeness",
             activity.complete_share if activity is not None else None],
            ["confidence",
             f"{activity.confidence_level} ({activity.confidence_score:.2f})"
             if activity is not None else None],
        ],
    )

    ecosystem = report.ecosystem
    _print_table(
        ["Ecosystem", "Value"],
        [
            ["score", ecosystem.score if ecosystem is not None else None],
            ["associated repositories",
             ecosystem.associated_repositories if ecosystem is not None else None],
            ["owned repositories",
             ecosystem.owned_repositories if ecosystem is not None else None],
            ["missing signals",
             ", ".join(ecosystem.missing_components) if ecosystem is not None else "—"],
        ],
    )

    emerging = report.emerging
    _print_table(
        ["Emerging", "Value"],
        [
            ["label", emerging.label],
            ["score", f"{emerging.score:.2f}"],
            ["confidence", f"{emerging.confidence_level} "
                           f"({emerging.confidence_score:.2f})"],
            ["evidence points", emerging.evidence_points],
            ["signals", _format_raw_signals(emerging.raw_signals)],
            ["explanation", emerging.explanation],
        ],
    )

    links = dataset.links.get(report.developer_id, ())
    reference_now = dataset.reference_now
    link_rows: list[list[object]] = []
    for link in links:
        repo = dataset.repositories.get(link.repository_id)
        link_delta = compute_contribution_delta(
            link.observations,
            reference_now=reference_now,
            window_days=window_days,
        )
        link_rows.append(
            [
                repo.full_name if repo is not None else link.repository_id,
                link.contributions,
                f"{link.share:.1%}",
                (
                    link_delta.delta
                    if link_delta is not None and link_delta.delta is not None
                    else "—"
                ),
                len(link.observations),
            ]
        )
    _print_table(
        ["Repository", "Contributions", "Share", "Observed Δ",
         "Observations"],
        link_rows,
    )
    if report.topic_relevances:
        _print_table(
            ["Topic", "Relevance", "Repos", "Confidence"],
            [
                [
                    relevance.topic,
                    relevance.score,
                    relevance.repo_count,
                    f"{relevance.confidence_level} ({relevance.confidence_score:.2f})",
                ]
                for relevance in report.topic_relevances
            ],
        )


def _format_raw_signals(signals: dict[str, int | float | bool]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(signals.items()))


@app.command("emerging-developers")
def emerging_developers_command(
    limit: int = typer.Option(10, "--limit", "-n", help="How many to show."),
    window: str = typer.Option(
        "7d", "--window", help="Activity window: 1d, 7d, or 30d."
    ),
    min_confidence: float | None = typer.Option(
        None, "--min-confidence", help="Minimum emerging confidence (0..1)."
    ),
    min_followers: int | None = typer.Option(
        None, "--min-followers", help="Minimum current followers."
    ),
) -> None:
    """List developers classified EMERGING, most-rising first."""
    settings = get_settings()
    _run(
        _emerging_run(
            settings,
            limit=limit,
            window=_parse_window_days(window),
            min_confidence=min_confidence,
            min_followers=min_followers,
        )
    )


async def _emerging_run(
    settings: Settings,
    *,
    limit: int,
    window: int,
    min_confidence: float | None,
    min_followers: int | None,
) -> None:
    engine, session = await _engine_and_session(settings)
    try:
        _dataset, reports = await _build_developer_reports(
            session, window_days=window
        )
        emerging = [
            report
            for report in reports
            if report.emerging.label == "EMERGING"
            and (
                min_confidence is None
                or report.emerging.confidence_score >= min_confidence
            )
            and (
                min_followers is None
                or (report.followers is not None and report.followers >= min_followers)
            )
        ]
        emerging.sort(key=lambda r: r.emerging.score, reverse=True)
        if not emerging:
            print(
                "No developers classified EMERGING with the current constraints — "
                "run 'github-radar update' regularly and re-check."
            )
            return
        _print_table(
            ["Login", "Followers", "Emerging", "Confidence", "Activity", "Repos"],
            [
                [
                    r.login,
                    r.followers,
                    f"{r.emerging.score:.2f}",
                    f"{r.emerging.confidence_level} "
                    f"({r.emerging.confidence_score:.2f})",
                    (
                        r.activity.score
                        if r.activity is not None and r.activity.available
                        else None
                    ),
                    r.associations,
                ]
                for r in emerging[:limit]
            ],
        )
    finally:
        await session.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# graph analytics
# ---------------------------------------------------------------------------

_graph_app = typer.Typer(
    help="Ecosystem graph analytics derived from the tracked dataset.",
    no_args_is_help=True,
)
_bridges_app = typer.Typer(
    help="Bridge intelligence: developers connecting topic ecosystems.",
    no_args_is_help=True,
)

# ``bridges`` is mounted twice — at the top level and inside the ``graph``
# group — so both `github-radar bridges ...` and the older
# `github-radar graph bridges ...` spellings stay working.
app.add_typer(_bridges_app, name="bridges")
_graph_app.add_typer(_bridges_app, name="bridges")
app.add_typer(_graph_app, name="graph")


async def _load_graph(
    session: AsyncSession,
) -> tuple[EcosystemGraph, datetime]:
    """Load the derived graph, anchoring ``reference_now`` on real data.

    Outcomes are reproducible between runs: the anchor is the newest observed
    ``captured_at`` in the dataset, never the wall clock (falling back to the
    wall clock only when nothing has ever been observed).
    """
    reference_now = await storage.newest_observation_at(session)
    reference_now = reference_now or utcnow()
    graph = await load_graph(session, reference_now=reference_now)
    return graph, reference_now


@_bridges_app.command("develop")
def bridges_develop(
    window: str = typer.Option(
        "7d", "--window", help="Activity window: 1d, 7d, or 30d."
    ),
    limit: int = typer.Option(10, "--limit", "-n", help="How many to show."),
    topic: str | None = typer.Option(
        None,
        "--topic",
        "-t",
        help="Only developers with evidence in this topic ecosystem.",
    ),
    min_confidence: float | None = typer.Option(
        None, "--min-confidence", help="Minimum bridge confidence (0..1)."
    ),
    login: str | None = typer.Option(
        None, "--login", help="Show one developer's full bridge explanation."
    ),
) -> None:
    """Rank developers by how strongly they bridge tracked topic ecosystems.

    The bridge score is bounded and explainable: every result shows its
    component breakdown, the topics and repositories behind it, and a data
    coverage confidence. Followers never enter the score; a developer or
    repository with thin evidence is hard-capped and marked as small sample.
    """
    settings = get_settings()
    _run(
        _bridges_develop_run(
            settings,
            window=_parse_window_days(window),
            limit=limit,
            topic=storage.normalize_topic(topic) if topic else None,
            min_confidence=min_confidence,
            login=login.strip().lower() if login else None,
        )
    )


async def _bridges_develop_run(
    settings: Settings,
    *,
    window: int,
    limit: int,
    topic: str | None,
    min_confidence: float | None,
    login: str | None,
) -> None:
    if min_confidence is not None and not 0.0 <= min_confidence <= 1.0:
        typer.secho(
            f"--min-confidence must be between 0 and 1 (got {min_confidence}).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    engine, session = await _engine_and_session(settings)
    try:
        reference_now = await storage.newest_observation_at(session)
        reference_now = reference_now or utcnow()
        graph = await load_graph(session, reference_now=reference_now)

        if login is not None:
            node = graph.developer_by_login(login)
            if node is None:
                typer.secho(
                    f"No tracked developer named {login!r}.\n"
                    f"  Run 'github-radar update' to observe contributor profiles.",
                    fg=typer.colors.YELLOW,
                )
                return
            bridge = compute_developer_bridge(
                graph,
                node.developer_id,
                reference_now=reference_now,
                window_days=window,
            )
            if (
                min_confidence is not None
                and bridge.confidence.score < min_confidence
            ):
                print(
                    f"{bridge.login}'s bridge confidence "
                    f"({bridge.confidence.score:.2f}) is below "
                    f"--min-confidence {min_confidence}."
                )
                return
            _print_developer_bridge(bridge)
            return

        if topic is not None and graph.topic_by_name(topic) is None:
            typer.secho(
                f"No tracked topic named {topic!r}.", fg=typer.colors.YELLOW
            )
            return

        bridges = developer_bridges(
            graph,
            reference_now=reference_now,
            window_days=window,
            topic=topic,
            limit=limit,
            min_confidence=min_confidence,
        )
        if not bridges:
            print(
                "No developer bridges match — run 'github-radar update' or relax "
                "the filters."
            )
            return

        _print_table(
            ["Login", "Bridge", "Topics", "Repos", "Small", "Confidence"],
            [
                [
                    bridge.login,
                    f"{bridge.bridge_score:.3f}",
                    bridge.meaningful_topic_memberships,
                    bridge.distinct_supporting_repositories,
                    "yes" if bridge.small_sample else "",
                    f"{bridge.confidence.score:.2f} "
                    f"({bridge.confidence.level})",
                ]
                for bridge in bridges
            ],
        )
    finally:
        await session.close()
        await engine.dispose()


def _print_developer_bridge(bridge: DeveloperBridge) -> None:
    _print_table(
        ["Field", "Value"],
        [
            ["login", bridge.login],
            ["bridge score", f"{bridge.bridge_score:.3f}"],
            ["meaningful topic memberships", bridge.meaningful_topic_memberships],
            ["distinct supporting repositories", bridge.distinct_supporting_repositories],
            ["small sample (capped)", "yes" if bridge.small_sample else "no"],
            ["confidence",
             f"{bridge.confidence.score:.2f} ({bridge.confidence.level})"],
        ],
    )
    if bridge.repository_evidence:
        _print_table(
            ["Supporting repositories"],
            [[full_name] for full_name in bridge.repository_evidence],
        )

    _print_table(
        ["Component", "Score"],
        [
            [name, f"{value:.3f}"]
            for name, value in sorted(bridge.components.items())
        ],
    )
    _print_table(
        ["Topic", "Repos", "Strength", "Contributions", "Recent Δ",
         "History"],
        [
            [
                evidence.topic,
                evidence.repository_count,
                f"{evidence.topic_strength:.2f}",
                evidence.total_contributions,
                _fmt(evidence.recent_delta),
                f"{evidence.history_coverage:.0%}",
            ]
            for evidence in bridge.topic_evidence
        ],
    )
    print(f"\nConfidence: {bridge.confidence.explanation}")


# ---------------------------------------------------------------------------
# bridges cross-topic / bridges repos
# ---------------------------------------------------------------------------

def _print_cross_topic_report(report: CrossTopicBridgeReport) -> None:
    _print_table(
        ["Field", "Value"],
        [
            ["topic A", report.topic_a],
            ["topic B", report.topic_b],
            ["bridging developers", report.bridge_count],
            ["distinct bridging developers", report.distinct_bridging_developers],
            ["average bridge score", f"{report.average_bridge_score:.3f}"],
            ["shared tracked repositories",
             ", ".join(report.shared_tracked_repositories) or "—"],
        ],
    )
    if report.top_bridges:
        _print_table(
            ["Developer", "Bridge", "Confidence", "A repos", "B repos",
             "Shared tracked"],
            [
                [
                    bridge.login,
                    f"{bridge.bridge_score:.3f}",
                    f"{bridge.confidence.score:.2f} ({bridge.confidence.level})",
                    bridge.topic_a.repository_count,
                    bridge.topic_b.repository_count,
                    ", ".join(bridge.shared_tracked_repositories) or "—",
                ]
                for bridge in report.top_bridges
            ],
        )


@_bridges_app.command("cross-topic")
def bridges_cross_topic(
    topic_a: str = typer.Argument(
        ..., help="First topic ecosystem, e.g. mcp."
    ),
    topic_b: str = typer.Argument(
        ..., help="Second topic ecosystem, e.g. ai."
    ),
    window: str = typer.Option(
        "7d", "--window", help="Activity window: 1d, 7d, or 30d."
    ),
    limit: int = typer.Option(10, "--limit", "-n", help="How many to show."),
) -> None:
    """Developers bridging two topic ecosystems.

    Shows every developer with observed evidence in *both* ecosystems, the
    shared tracked repositories that make the bridge real, and an aggregate of
    the whole bridge ecosystem between the two topics.
    """
    settings = get_settings()
    _run(
        _bridges_cross_topic_run(
            settings,
            topic_a=storage.normalize_topic(topic_a),
            topic_b=storage.normalize_topic(topic_b),
            window=_parse_window_days(window),
            limit=limit,
        )
    )


async def _bridges_cross_topic_run(
    settings: Settings,
    *,
    topic_a: str,
    topic_b: str,
    window: int,
    limit: int,
) -> None:
    engine, session = await _engine_and_session(settings)
    try:
        graph, reference_now = await _load_graph(session)
        for name in (topic_a, topic_b):
            if graph.topic_by_name(name) is None:
                typer.secho(
                    f"No tracked topic named {name!r}.", fg=typer.colors.YELLOW
                )
                return
        report = build_cross_topic_bridge_report(
            graph,
            topic_a,
            topic_b,
            reference_now=reference_now,
            window_days=window,
            limit=limit,
        )
        _print_cross_topic_report(report)
    finally:
        await session.close()
        await engine.dispose()


@_bridges_app.command("repos")
def bridges_repos(
    window: str = typer.Option(
        "7d", "--window", help="Activity window: 1d, 7d, or 30d."
    ),
    limit: int = typer.Option(10, "--limit", "-n", help="How many to show."),
    min_confidence: float | None = typer.Option(
        None, "--min-confidence", help="Minimum bridge confidence (0..1)."
    ),
) -> None:
    """Rank repositories as cross-ecosystem bridges.

    A repository bridges when the people working on it cross into other topic
    ecosystems. The score is explainable and bounded; repositories with thin
    evidence are hard-capped and flagged as small samples.
    """
    settings = get_settings()
    _run(
        _bridges_repos_run(
            settings,
            window=_parse_window_days(window),
            limit=limit,
            min_confidence=min_confidence,
        )
    )


async def _bridges_repos_run(
    settings: Settings,
    *,
    window: int,
    limit: int,
    min_confidence: float | None,
) -> None:
    if min_confidence is not None and not 0.0 <= min_confidence <= 1.0:
        typer.secho(
            f"--min-confidence must be between 0 and 1 (got {min_confidence}).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    engine, session = await _engine_and_session(settings)
    try:
        graph, reference_now = await _load_graph(session)
        bridges = repository_bridges(
            graph,
            reference_now=reference_now,
            window_days=window,
            limit=limit,
            min_confidence=min_confidence,
        )
        if not bridges:
            print(
                "No repository bridges match — run 'github-radar update' or relax "
                "the filters."
            )
            return
        _print_table(
            ["Repository", "Bridge", "Contributors", "Topics", "Cross-repo",
             "Cross-topic", "Small", "Confidence"],
            [
                [
                    bridge.full_name,
                    f"{bridge.bridge_score:.3f}",
                    bridge.contributor_count,
                    bridge.topic_count,
                    bridge.cross_repository_contributors,
                    bridge.cross_topic_contributors,
                    "yes" if bridge.small_sample else "",
                    f"{bridge.confidence.score:.2f} "
                    f"({bridge.confidence.level})",
                ]
                for bridge in bridges
            ],
        )
    finally:
        await session.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# ecosystem / related-topics / related-repos
# ---------------------------------------------------------------------------

def _print_centrality(rows: tuple[NodeCentrality, ...]) -> None:
    _print_table(
        ["Node", "Degree", "Weighted degree"],
        [[item.label, item.degree, item.weighted_degree] for item in rows],
    )


def _print_ecosystem_report(report: EcosystemGraphReport) -> None:
    _print_table(
        ["Measure", "Value"],
        [
            ["developers", report.developer_count],
            ["repositories", report.repository_count],
            ["topics", report.topic_count],
            ["owns edges", report.owns_edges],
            ["contributes_to edges", report.contributes_edges],
            ["tagged_with edges", report.tagged_edges],
            ["connected components", report.component_count],
        ],
    )
    largest = report.largest_component
    if largest is not None:
        _print_table(
            ["Largest component", "Value"],
            [
                ["nodes", largest.node_count],
                ["developers", largest.developer_count],
                ["repositories", largest.repository_count],
                ["topics", largest.topic_count],
                ["developers", ", ".join(largest.developer_logins) or "—"],
                ["repositories",
                 ", ".join(largest.repository_full_names) or "—"],
                ["topics", ", ".join(largest.topic_names) or "—"],
            ],
        )
    if report.top_developer_centrality:
        print("\nTop developer hubs")
        _print_centrality(report.top_developer_centrality)
    if report.top_repository_centrality:
        print("\nTop repository hubs")
        _print_centrality(report.top_repository_centrality)
    if report.top_topic_centrality:
        print("\nTop topic hubs")
        _print_centrality(report.top_topic_centrality)
    if report.top_developer_bridges:
        _print_table(
            ["Login", "Bridge", "Topics", "Repos", "Confidence"],
            [
                [
                    bridge.login,
                    f"{bridge.bridge_score:.3f}",
                    bridge.meaningful_topic_memberships,
                    bridge.distinct_supporting_repositories,
                    f"{bridge.confidence.score:.2f} "
                    f"({bridge.confidence.level})",
                ]
                for bridge in report.top_developer_bridges
            ],
        )


@app.command("ecosystem")
def ecosystem(
    window: str = typer.Option(
        "7d", "--window", help="Activity window: 1d, 7d, or 30d."
    ),
    centrality_limit: int = typer.Option(
        DEFAULT_CENTRALITY_LIMIT,
        "--centrality-limit",
        help="How many hubs to show per node type.",
    ),
    bridge_limit: int = typer.Option(
        5, "--bridge-limit", help="How many top developer bridges to show."
    ),
) -> None:
    """Top-level view of the whole tracked ecosystem graph."""
    settings = get_settings()
    _run(
        _ecosystem_run(
            settings,
            window=_parse_window_days(window),
            centrality_limit=centrality_limit,
            bridge_limit=bridge_limit,
        )
    )


async def _ecosystem_run(
    settings: Settings,
    *,
    window: int,
    centrality_limit: int,
    bridge_limit: int,
) -> None:
    engine, session = await _engine_and_session(settings)
    try:
        graph, reference_now = await _load_graph(session)
        report = build_ecosystem_report(
            graph,
            reference_now=reference_now,
            window_days=window,
            centrality_limit=centrality_limit,
            bridge_limit=bridge_limit,
        )
        _print_ecosystem_report(report)
    finally:
        await session.close()
        await engine.dispose()


def _print_developer_graph(report: DeveloperGraphReport) -> None:
    print("\nGraph footprint")
    _print_table(
        ["Field", "Value"],
        [
            ["login", report.login],
            ["repositories (contributed)", report.reach.contributed_repositories],
            ["repositories (owned)", report.reach.owned_repositories],
            ["distinct repositories", report.reach.reached_repositories],
            ["distinct developers reached", report.reach.reached_developers],
            ["distinct topics reached", report.reach.reached_topics],
            ["centrality (degree)",
             report.centrality.degree if report.centrality else None],
            ["centrality (weighted degree)",
             report.centrality.weighted_degree if report.centrality else None],
        ],
    )
    if report.co_contributors:
        _print_table(
            ["Co-contributor", "Shared repos", "Strength", "Confidence"],
            [
                [
                    row.login,
                    row.shared_repository_count,
                    f"{row.strength:.3f}",
                    (
                        f"{row.confidence.score:.3f} ({row.confidence.level})"
                        if row.confidence is not None
                        else None
                    ),
                ]
                for row in report.co_contributors
            ],
        )
    if report.bridge is not None:
        _print_developer_bridge(report.bridge)


def _print_topic_graph(report: TopicGraphReport) -> None:
    print("\nGraph footprint")
    _print_table(
        ["Field", "Value"],
        [
            ["topic", report.name],
            ["repositories", report.reach.repositories],
            ["contributing developers", report.reach.contributing_developers],
            ["owning developers", report.reach.owning_developers],
            ["associated developers", report.reach.associated_developers],
            ["centrality (degree)",
             report.centrality.degree if report.centrality else None],
            ["centrality (weighted degree)",
             report.centrality.weighted_degree if report.centrality else None],
        ],
    )
    if report.related_topics:
        _print_table(
            ["Related topic", "Shared repos", "Shared devs", "Repo jaccard",
             "Dev jaccard", "Confidence"],
            [
                [
                    row.topic,
                    row.shared_repository_count,
                    row.shared_developer_count,
                    f"{row.repository_overlap.jaccard:.3f}",
                    f"{row.developer_overlap.jaccard:.3f}",
                    f"{row.confidence.score:.3f} ({row.confidence.level})",
                ]
                for row in report.related_topics
            ],
        )


def _print_repository_graph(report: RepositoryGraphReport) -> None:
    print("\nGraph footprint")
    _print_table(
        ["Field", "Value"],
        [
            ["repository", report.full_name],
            ["contributing developers", report.reach.contributing_developers],
            ["owning developers", report.reach.owning_developers],
            ["topics", report.reach.topics],
            ["sibling repositories", report.reach.sibling_repositories],
            ["centrality (degree)",
             report.centrality.degree if report.centrality else None],
            ["centrality (weighted degree)",
             report.centrality.weighted_degree if report.centrality else None],
            ["bridge score",
             f"{report.bridge.bridge_score:.3f}" if report.bridge is not None else None],
        ],
    )
    if report.related_repositories:
        _print_table(
            ["Related repository", "Shared devs", "Shared topics", "Dev overlap",
             "Topic overlap", "Score", "Confidence"],
            [
                [
                    row.full_name,
                    row.shared_developer_count,
                    row.shared_topic_count,
                    f"{row.developer_overlap.jaccard:.3f}",
                    f"{row.topic_overlap.jaccard:.3f}",
                    f"{row.relationship_score:.3f}",
                    f"{row.confidence.score:.3f} ({row.confidence.level})",
                ]
                for row in report.related_repositories
            ],
        )


@app.command("related-topics")
def related_topics_command(
    topic: str = typer.Argument(..., help="Topic name, e.g. mcp."),
    limit: int = typer.Option(
        5, "--limit", "-n", help="How many related topics to show."
    ),
) -> None:
    """Show a topic's graph footprint and the topics it overlaps."""
    settings = get_settings()
    _run(
        _related_topics_run(
            settings, topic=storage.normalize_topic(topic), limit=limit
        )
    )


async def _related_topics_run(
    settings: Settings, *, topic: str, limit: int
) -> None:
    engine, session = await _engine_and_session(settings)
    try:
        graph, _reference = await _load_graph(session)
        node = graph.topic_by_name(topic)
        if node is None:
            typer.secho(
                f"No tracked topic named {topic!r}.", fg=typer.colors.YELLOW
            )
            return
        report = build_topic_report(graph, node.topic_id, related_limit=limit)
        _print_topic_graph(report)
    finally:
        await session.close()
        await engine.dispose()


@app.command("related-repos")
def related_repos_command(
    repository: str = typer.Argument(
        ..., help="Full repository name, e.g. acme/widget."
    ),
    window: str = typer.Option(
        "7d", "--window", help="Activity window: 1d, 7d, or 30d."
    ),
    limit: int = typer.Option(
        5, "--limit", "-n", help="How many related repositories to show."
    ),
) -> None:
    """Show a repository's graph footprint and the repositories it overlaps."""
    settings = get_settings()
    _run(
        _related_repos_run(
            settings,
            repository=repository.strip().lower(),
            window=_parse_window_days(window),
            limit=limit,
        )
    )


async def _related_repos_run(
    settings: Settings, *, repository: str, window: int, limit: int
) -> None:
    engine, session = await _engine_and_session(settings)
    try:
        graph, reference_now = await _load_graph(session)
        node = graph.repository_by_full_name(repository)
        if node is None:
            typer.secho(
                f"No tracked repository named {repository!r}.\n"
                f"  Run 'github-radar update' to observe more repositories.",
                fg=typer.colors.YELLOW,
            )
            return
        report = build_repository_report(
            graph,
            node.repository_id,
            reference_now=reference_now,
            window_days=window,
            related_limit=limit,
        )
        _print_repository_graph(report)
    finally:
        await session.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# rate-limit
# ---------------------------------------------------------------------------

@app.command("rate-limit")
def show_rate_limit() -> None:
    """Show current GitHub API rate limits (core, search, ...)."""
    settings = get_settings()
    client = _make_client(settings, require_auth=False)
    if not settings.github_token:
        typer.secho(
            "No GITHUB_TOKEN configured — showing anonymous limits.",
            fg=typer.colors.YELLOW,
        )

    async def _go() -> None:
        async with client:
            states = await client.get_rate_limits()
        _print_table(
            ["Resource", "Limit", "Remaining", "Resets at"],
            [
                [
                    name,
                    state.limit,
                    state.remaining,
                    _fmt(state.reset_at),
                ]
                for name, state in sorted(states.items())
            ],
        )

    _run(_go())


# ---------------------------------------------------------------------------
# ai synthesis (Phase 5)
# ---------------------------------------------------------------------------

_ai_app = typer.Typer(
    help="LLM synthesis of the measured facts (cached, deterministic confidence).",
    no_args_is_help=True,
)
app.add_typer(_ai_app, name="ai")


def _tokens_label(artifact: AIArtifact) -> str:
    if artifact.input_tokens is None and artifact.output_tokens is None:
        return "unavailable"
    return (
        f"{artifact.input_tokens if artifact.input_tokens is not None else '?'} in / "
        f"{artifact.output_tokens if artifact.output_tokens is not None else '?'} out"
    )


def _print_ai_artifact(
    artifact: AIArtifact, bundle: EvidenceBundle, outcome: SynthesisResult
) -> None:
    """Human-oriented synthesis output: synthesis first, facts last."""
    print(artifact.headline)
    print(f"\n[{artifact.confidence}] {artifact.summary}")
    if artifact.key_points:
        print("\nKey points")
        for point in artifact.key_points:
            refs = ", ".join(point.evidence_ids)
            print(f"  • {point.text}  [{refs}]")
    if artifact.unknowns:
        print("\nUnknowns / limitations")
        for unknown in artifact.unknowns:
            print(f"  • {unknown}")
    else:
        print("\nUnknowns / limitations: the model reported none; thin history is "
              "still reflected by the deterministic confidence level.")

    print("\nMeasured facts (deterministic Phase 2–4 evidence)")
    for item in bundle.items:
        payload = item.payload
        if len(payload) > 600:
            payload = payload[:600].rstrip() + " …"
        print(f"  [{item.id}] {item.kind} — {item.source}: {payload}")

    print("\nArtifact metadata")
    _print_table(
        ["Field", "Value"],
        [
            ["entity", f"{artifact.entity_type} {artifact.entity_key}"],
            ["artifact type", artifact.artifact_type],
            ["window", f"{artifact.window_days}d"],
            ["prompt version", artifact.prompt_version],
            ["provider / model", f"{artifact.provider} / {artifact.model}"],
            ["confidence", artifact.confidence],
            ["generated at", _fmt(artifact.generated_at)],
            ["source", "cached" if outcome.cache_hit else "synthesized now"],
            ["tokens (in/out)", _tokens_label(artifact)],
        ],
    )


def _print_ai_json(
    artifact: AIArtifact, outcome: SynthesisResult, generated_at: datetime
) -> None:
    print(
        json.dumps(
            {
                "cache_hit": outcome.cache_hit,
                "generated_at": generated_at.isoformat(timespec="seconds"),
                "artifact": artifact.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


async def _ai_run(
    *,
    bundle: EvidenceBundle,
    confidence: ConfidenceLevel,
    artifact_type: ArtifactType,
    window_days: int,
    provider: AIProvider,
    store: SqlArtifactStore,
    budget: RunBudget,
    max_evidence_chars: int,
    force: bool,
    json_output: bool,
) -> None:
    request = SynthesisRequest(
        entity_type=bundle.entity_type,
        entity_key=bundle.entity_key,
        artifact_type=artifact_type,
        window_days=window_days,
        bundle=bundle,
        confidence=confidence,
        bundle_json=serialize_evidence(bundle),
        provider=provider,
        store=store,
        budget=budget,
        generated_at=utcnow(),
        force=force,
        max_evidence_chars=max_evidence_chars,
    )
    outcome = await synthesize(request)
    if json_output:
        _print_ai_json(outcome.artifact, outcome, request.generated_at)
    else:
        _print_ai_artifact(outcome.artifact, bundle, outcome)


@_ai_app.command("repo")
def ai_repo(
    owner_repo: str = typer.Argument(..., help="Repository as OWNER/NAME."),
    window: str = typer.Option(
        "7d", "--window", help="Activity window: 1d, 7d, or 30d."
    ),
    trend: bool = typer.Option(
        False, "--trend", help="Synthesize the trajectory (repository-trend-v1)."
    ),
    force: bool = typer.Option(
        False, "--force", help="Bypass the model-result cache and re-synthesize."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the artifact as JSON."
    ),
) -> None:
    """Synthesize an LLM narrative for ONE tracked repository.

    The model explains only the measured facts; it never measures anything
    itself. Results are cached and replayed until the evidence changes.
    """
    settings = get_settings()
    parts = owner_repo.strip("/").split("/")
    if len(parts) != 2:
        typer.secho(
            f"Expected OWNER/NAME, got {owner_repo!r}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    _run(
        _ai_repo_run(
            settings,
            owner=parts[0],
            name=parts[1],
            window=_parse_window_days(window),
            trend=trend,
            force=force,
            json_output=json_output,
        )
    )


async def _ai_repo_run(
    settings: Settings,
    *,
    owner: str,
    name: str,
    window: int,
    trend: bool,
    force: bool,
    json_output: bool,
) -> None:
    client = _make_client(settings, require_auth=True)
    textual = TextualEvidence(client)
    engine, session = await _engine_and_session(settings)
    provider = make_provider(settings)
    store = SqlArtifactStore(session)
    budget = RunBudget(total=settings.ai_max_requests_per_run)
    try:
        full_name = f"{owner}/{name}"
        row = await storage.get_repository_by_full_name(session, full_name)
        if row is None:
            typer.secho(
                f"{full_name} is not tracked yet.\n"
                f'  Discover it with: github-radar discover "{owner}/{name}"',
                fg=typer.colors.YELLOW,
            )
            return

        topics = tuple(
            t.name for t in await storage.topics_for_repository(session, row.id)
        )
        contributor_rows = await storage.contributors_for_repository(session, row.id)
        contributors = tuple(
            ContributorDigest(login=dev.login, contributions=contribs)
            for dev, contribs, _last_seen in contributor_rows
        )
        snapshots = await storage.snapshots_for_repository(session, row.id)
        latest = await storage.latest_snapshot(session, row.id)
        metrics = compute_metrics([s.to_domain() for s in snapshots])
        reference_now = latest.captured_at if latest is not None else None
        momentum = (
            compute_momentum(metrics, reference_now=reference_now)
            if reference_now is not None
            else None
        )
        trend_obj = (
            classify_trend(metrics, momentum, reference_now=reference_now)
            if reference_now is not None
            else None
        )

        readme = await textual.readme(row.owner_login, row.name)
        releases = await textual.releases(row.owner_login, row.name)
        commits = await textual.commits(row.owner_login, row.name)

        digest = RepositoryDigest(
            full_name=row.full_name,
            owner_login=row.owner_login,
            description=row.description,
            homepage=row.homepage,
            primary_language=row.primary_language,
            default_branch=row.default_branch,
            visibility=row.visibility,
            is_fork=row.is_fork,
            is_archived=row.is_archived,
            is_disabled=row.is_disabled,
            is_template=row.is_template,
            github_created_at=row.created_at,
            github_updated_at=row.updated_at,
            pushed_at=row.pushed_at,
            topics=topics,
            contributors=contributors,
            latest_captured_at=reference_now,
            snapshot_count=len(snapshots),
            first_snapshot_at=snapshots[0].captured_at if snapshots else None,
            metrics=metrics,
            momentum=momentum,
            trend=trend_obj,
            textual={"readme": readme, "releases": releases, "commits": commits},
        )
        bundle = repository_evidence(digest, window_days=window)
        await _ai_run(
            bundle=bundle,
            confidence=repo_level(metrics, window),
            artifact_type="repository-trend" if trend else "repository-summary",
            window_days=window,
            provider=provider,
            store=store,
            budget=budget,
            max_evidence_chars=settings.ai_max_evidence_chars,
            force=force,
            json_output=json_output,
        )
    finally:
        await client.close()
        await session.close()
        await engine.dispose()


@_ai_app.command("topic")
def ai_topic(
    topic: str = typer.Argument(..., help="Topic name, e.g. mcp."),
    window: str = typer.Option(
        "7d", "--window", help="Activity window: 1d, 7d, or 30d."
    ),
    force: bool = typer.Option(
        False, "--force", help="Bypass the model-result cache and re-synthesize."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the artifact as JSON."
    ),
) -> None:
    """Synthesize an LLM narrative for ONE tracked topic."""
    settings = get_settings()
    _run(
        _ai_topic_run(
            settings,
            topic=storage.normalize_topic(topic),
            window=_parse_window_days(window),
            force=force,
            json_output=json_output,
        )
    )


async def _ai_topic_run(
    settings: Settings,
    *,
    topic: str,
    window: int,
    force: bool,
    json_output: bool,
) -> None:
    engine, session = await _engine_and_session(settings)
    provider = make_provider(settings)
    store = SqlArtifactStore(session)
    budget = RunBudget(total=settings.ai_max_requests_per_run)
    try:
        tracked = await _collect_tracked(session)
        by_topic: dict[str, list[RepositoryReport]] = {}
        for item in tracked:
            if topic in item.topics:
                by_topic.setdefault(topic, []).append(item.report)
        if not by_topic:
            typer.secho(
                f"No tracked topic named {topic!r}.", fg=typer.colors.YELLOW
            )
            return
        aggregate = next(a for a in aggregate_topics(by_topic) if a.name == topic)

        graph, _reference = await _load_graph(session)
        node = graph.topic_by_name(topic)
        graph_report = (
            build_topic_report(graph, node.topic_id) if node is not None else None
        )

        reference_top_now = (
            await storage.newest_observation_at(session)
        ) or utcnow()
        digests = [
            TopicRepoDigest(
                full_name=item.report.full_name,
                stars=item.report.metrics.latest_counts.get("stars"),
                momentum_score=(
                    item.report.momentum.score
                    if item.report.momentum is not None
                    else None
                ),
                trend_class=_repo_trend_short(item.report, reference_top_now),
                confidence_level=repo_level(item.report.metrics, window),
            )
            for item in tracked
            if topic in item.topics
        ]
        bundle = topic_evidence(
            topic, aggregate, digests, graph_report, window_days=window
        )
        await _ai_run(
            bundle=bundle,
            confidence=topic_aggregate_level(aggregate),
            artifact_type="topic-summary",
            window_days=window,
            provider=provider,
            store=store,
            budget=budget,
            max_evidence_chars=settings.ai_max_evidence_chars,
            force=force,
            json_output=json_output,
        )
    finally:
        await session.close()
        await engine.dispose()


def _repo_trend_short(
    report: RepositoryReport, reference_now: datetime | None
) -> str | None:
    """The trend class label for one repository, or ``None`` if undefined."""
    if reference_now is None or report.momentum is None:
        return None
    trend = classify_trend(report.metrics, report.momentum, reference_now=reference_now)
    return f"{trend.trend} ({trend.label})"


@_ai_app.command("developer")
def ai_developer(
    login: str = typer.Argument(..., help="GitHub login of the developer."),
    window: str = typer.Option(
        "7d", "--window", help="Activity window: 1d, 7d, or 30d."
    ),
    force: bool = typer.Option(
        False, "--force", help="Bypass the model-result cache and re-synthesize."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the artifact as JSON."
    ),
) -> None:
    """Synthesize an LLM narrative for ONE tracked developer."""
    settings = get_settings()
    _run(
        _ai_developer_run(
            settings,
            login=login.strip().lower(),
            window=_parse_window_days(window),
            force=force,
            json_output=json_output,
        )
    )


async def _ai_developer_run(
    settings: Settings,
    *,
    login: str,
    window: int,
    force: bool,
    json_output: bool,
) -> None:
    engine, session = await _engine_and_session(settings)
    provider = make_provider(settings)
    store = SqlArtifactStore(session)
    budget = RunBudget(total=settings.ai_max_requests_per_run)
    try:
        _dataset, reports = await _build_developer_reports(
            session, window_days=window
        )
        report = next(
            (report for report in reports if report.login.lower() == login),
            None,
        )
        if report is None:
            typer.secho(
                f"No tracked developer named {login!r}.\n"
                f"  Run 'github-radar update' to observe contributor profiles.",
                fg=typer.colors.YELLOW,
            )
            return
        graph, reference_now = await _load_graph(session)
        node = graph.developer_by_login(report.login)
        graph_report = (
            build_developer_report(
                graph,
                node.developer_id,
                reference_now=reference_now,
                window_days=window,
            )
            if node is not None
            else None
        )
        bundle = developer_evidence(report, graph_report, window_days=window)
        await _ai_run(
            bundle=bundle,
            confidence=developer_level(report),
            artifact_type="developer-summary",
            window_days=window,
            provider=provider,
            store=store,
            budget=budget,
            max_evidence_chars=settings.ai_max_evidence_chars,
            force=force,
            json_output=json_output,
        )
    finally:
        await session.close()
        await engine.dispose()


@_ai_app.command("ecosystem")
def ai_ecosystem(
    window: str = typer.Option(
        "7d", "--window", help="Activity window: 1d, 7d, or 30d."
    ),
    force: bool = typer.Option(
        False, "--force", help="Bypass the model-result cache and re-synthesize."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the artifact as JSON."
    ),
) -> None:
    """Synthesize an LLM narrative for the whole tracked ecosystem."""
    settings = get_settings()
    _run(
        _ai_ecosystem_run(
            settings,
            window=_parse_window_days(window),
            force=force,
            json_output=json_output,
        )
    )


async def _ai_ecosystem_run(
    settings: Settings,
    *,
    window: int,
    force: bool,
    json_output: bool,
) -> None:
    engine, session = await _engine_and_session(settings)
    provider = make_provider(settings)
    store = SqlArtifactStore(session)
    budget = RunBudget(total=settings.ai_max_requests_per_run)
    try:
        graph, reference_now = await _load_graph(session)
        report = build_ecosystem_report(
            graph,
            reference_now=reference_now,
            window_days=window,
        )
        bundle = ecosystem_evidence(report, window_days=window)
        await _ai_run(
            bundle=bundle,
            confidence=ecosystem_level(report),
            artifact_type="ecosystem-summary",
            window_days=window,
            provider=provider,
            store=store,
            budget=budget,
            max_evidence_chars=settings.ai_max_evidence_chars,
            force=force,
            json_output=json_output,
        )
    finally:
        await session.close()
        await engine.dispose()


@_ai_app.command("bridge")
def ai_bridge(
    topic_a: str = typer.Argument(..., help="First topic ecosystem, e.g. mcp."),
    topic_b: str = typer.Argument(..., help="Second topic ecosystem, e.g. ai."),
    window: str = typer.Option(
        "7d", "--window", help="Activity window: 1d, 7d, or 30d."
    ),
    force: bool = typer.Option(
        False, "--force", help="Bypass the model-result cache and re-synthesize."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the artifact as JSON."
    ),
) -> None:
    """Synthesize an LLM narrative for a two-topic bridge ecosystem."""
    settings = get_settings()
    _run(
        _ai_bridge_run(
            settings,
            topic_a=storage.normalize_topic(topic_a),
            topic_b=storage.normalize_topic(topic_b),
            window=_parse_window_days(window),
            force=force,
            json_output=json_output,
        )
    )


async def _ai_bridge_run(
    settings: Settings,
    *,
    topic_a: str,
    topic_b: str,
    window: int,
    force: bool,
    json_output: bool,
) -> None:
    engine, session = await _engine_and_session(settings)
    provider = make_provider(settings)
    store = SqlArtifactStore(session)
    budget = RunBudget(total=settings.ai_max_requests_per_run)
    try:
        graph, reference_now = await _load_graph(session)
        for name in (topic_a, topic_b):
            if graph.topic_by_name(name) is None:
                typer.secho(
                    f"No tracked topic named {name!r}.", fg=typer.colors.YELLOW
                )
                return
        report = build_cross_topic_bridge_report(
            graph,
            topic_a,
            topic_b,
            reference_now=reference_now,
            window_days=window,
        )
        bundle = bridge_evidence(report, window_days=window)
        await _ai_run(
            bundle=bundle,
            confidence=bridge_level(report),
            artifact_type="bridge-summary",
            window_days=window,
            provider=provider,
            store=store,
            budget=budget,
            max_evidence_chars=settings.ai_max_evidence_chars,
            force=force,
            json_output=json_output,
        )
    finally:
        await session.close()
        await engine.dispose()


@_ai_app.command("status")
def ai_status() -> None:
    """Show AI provider configuration, limits and cached artifacts."""
    settings = get_settings()
    _run(_ai_status_run(settings))


async def _ai_status_run(settings: Settings) -> None:
    engine, session = await _engine_and_session(settings)
    try:
        try:
            base_url = settings.require_ai_config()[0]
            configured = True
        except SettingsError:
            base_url, configured = "", False
        stats = await SqlArtifactStore(session).stats()
        rows: list[list[object]] = [
            ["provider configured", "yes" if configured else "no"],
            ["model", settings.ai_model or "—"],
            ["base URL host", _url_host(base_url) if configured else "—"],
            ["per-run request cap", settings.ai_max_requests_per_run],
            ["max output tokens", settings.ai_max_output_tokens],
            ["max evidence chars", settings.ai_max_evidence_chars],
        ]
        total = sum(stats.values())
        rows.append(["cached artifacts", total])
        for artifact_type, count in sorted(stats.items()):
            rows.append([f"  {artifact_type}", count])
        _print_table(["Setting", "Value"], rows)

        _print_table(
            ["Artifact", "Prompt version"],
            [[name, version] for name, version in sorted(PROMPT_VERSIONS.items())],
        )
    finally:
        await session.close()
        await engine.dispose()


def _url_host(base_url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(base_url).netloc or base_url

def main() -> None:
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app()


if __name__ == "__main__":
    main()