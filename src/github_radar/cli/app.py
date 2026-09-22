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
    rate-limit          show current GitHub API quota
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import typer
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

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
from github_radar.config import Settings, SettingsError, get_settings
from github_radar.discovery import DiscoveryReport, RepositoryDiscoveryService
from github_radar.github import (
    GitHubClient,
    GitHubConfigurationError,
    RateLimitExceeded,
)
from github_radar.services import (
    DeveloperDataset,
    DeveloperIntelligenceService,
    DeveloperReport,
    RepositoryUpdateService,
)
from github_radar.storage import repositories as storage
from github_radar.storage.db import make_engine, make_session_factory

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
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app()


if __name__ == "__main__":
    main()