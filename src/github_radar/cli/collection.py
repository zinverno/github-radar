"""Local collection diagnostics. This module has no GitHub client or write path."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from github_radar.config import SettingsError, get_settings
from github_radar.discovery.planning import load_discovery_config
from github_radar.services.quality import load_collection_stats, load_quality_report
from github_radar.storage.db import ManagedSession, make_engine
from github_radar.util import utcnow

app = typer.Typer(
    help="Read-only collection cost and dataset quality diagnostics.", no_args_is_help=True
)
CATALOG = Path(__file__).resolve().parents[3] / "config/discovery.toml"


@app.command("stats")
def stats(json_output: bool = typer.Option(False, "--json")) -> None:
    """Summarize persisted receipts and provisional collection cost estimates."""
    _run(json_output=json_output)


@app.command("quality")
def quality(
    config: Path = typer.Option(
        CATALOG, "--config", help="Catalog to compare with saved provenance."
    ),
    as_of: str | None = typer.Option(
        None, "--as-of", help="Aware ISO timestamp for age/staleness."
    ),
    stale_after_hours: int = typer.Option(24, "--stale-after-hours", min=0, max=87600),
    top: int = typer.Option(10, "--top", min=1, max=1000),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Audit coverage and existing rankings without HTTP or database mutations."""
    try:
        assessed_at = datetime.fromisoformat(as_of) if as_of else utcnow()
        if assessed_at.tzinfo is None:
            raise ValueError("--as-of must include a timezone, for example +00:00")
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--as-of") from exc
    _run(
        json_output=json_output,
        config=config,
        assessed_at=assessed_at,
        stale_after_hours=stale_after_hours,
        top=top,
    )


def _run(
    *,
    json_output: bool,
    config: Path | None = None,
    assessed_at: datetime | None = None,
    stale_after_hours: int = 24,
    top: int = 10,
) -> None:
    try:
        catalog = load_discovery_config(config) if config else None

        async def read() -> dict[str, Any]:
            engine = make_engine(get_settings().require_database_url())
            try:
                async with engine.connect() as connection, connection.begin():
                    await connection.execute(
                        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                    )
                    async with ManagedSession(bind=connection) as session:
                        if catalog is not None:
                            assert assessed_at is not None
                            return await load_quality_report(
                                session,
                                catalog=catalog,
                                assessed_at=assessed_at,
                                stale_after_hours=stale_after_hours,
                                top=top,
                            )
                        return await load_collection_stats(session)
            finally:
                await engine.dispose()

        report = asyncio.run(read())
    except (ValueError, OSError, SettingsError) as exc:
        typer.secho(f"Invalid diagnostic input: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    except SQLAlchemyError as exc:
        typer.secho(
            "Cannot read collection data; check database connectivity and migrations.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1) from exc
    if json_output:
        typer.echo(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        _summary(report)


def _summary(report: dict[str, Any]) -> None:
    if "coverage" in report:
        coverage = report["coverage"]
        typer.echo(f"Dataset quality as of {report['assessed_at']} (read-only)")
        typer.echo(
            f"Repositories: {coverage['total_repositories']}; "
            f"observations: {coverage['repository_observations']}; "
            f"stale: {coverage['stale_observations']}; "
            f"never observed: {coverage['never_observed']}"
        )
        typer.echo(
            f"Curated categories (overlap allowed): {coverage['curated_categories']['counts']}"
        )
        typer.echo(f"GitHub topics: {len(coverage['github_topics'])} (separate from categories)")
        for days, counts in coverage["windows"].items():
            typer.echo(f"{days}d star windows: {counts}")
        typer.echo(f"Momentum: {coverage['momentum']}")
        typer.echo(
            f"Developers with usable contributor history: "
            f"{report['developers']['with_usable_contributor_history']}"
        )
        typer.echo(
            "Confidence measures data coverage, not probability. "
            "Partial windows are not complete-period growth."
        )
        report = report["collection"]
    typer.echo(
        f"Collection runs: {report['run_count']}; "
        f"partial/unfinished: {report['partial_or_unfinished_runs']}; "
        f"budget exhausted: {report['budget_exhausted_runs']}"
    )
    typer.echo(f"HTTP attempt reservations: {report['requests']}")
    typer.echo(
        f"Discovery attempts/new repository: "
        f"{report['discovery']['api_attempts_per_new_repository']}"
    )
    typer.echo("Provisional estimates (not statistically reliable):")
    for projection in report["projections"]:
        typer.echo(
            f"  {projection['target_new_repositories']} new repositories: "
            f"{projection['measured_ratio_estimate_attempts']} estimated attempts; "
            f"{projection['scenario_http_attempt_cap']} scenario cap"
        )
    typer.echo(
        "Use --json for full evidence, ranking diagnostics, assumptions and per-run durations."
    )
