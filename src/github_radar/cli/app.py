"""The github-radar Typer CLI.

Commands:
    init-db        create database schema via Alembic migrations
    discover       search GitHub and store repositories
    update         re-observe tracked repositories
    repos          list tracked repositories
    repo           show one repository in detail
    stats          dataset statistics
    rate-limit     show current GitHub API quota
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
    FieldDelta,
    RepositoryReport,
    aggregate_topics,
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
from github_radar.services import RepositoryUpdateService
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
            client, session, contributors_limit=contributors_limit
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
        report = await RepositoryUpdateService(client, session).update(limit=limit)
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