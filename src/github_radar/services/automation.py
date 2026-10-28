"""Scheduling policy around the existing bounded collector, never a second collector."""

from __future__ import annotations

import hashlib
import ssl
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from github_radar.config.settings import GitHubRequestLimits, Settings
from github_radar.discovery.planning import (
    DiscoveryConfig,
    DiscoveryPlan,
    ExecutionLimits,
    load_discovery_config,
    plan_discovery,
)
from github_radar.github.client import GitHubClient
from github_radar.services.collection import CollectionService, stored_report
from github_radar.services.quality import load_quality_report
from github_radar.storage.collection import locked_collection_session
from github_radar.storage.models import CollectionRunRow, RepositoryRow, SnapshotRow
from github_radar.util import utcnow

Kind = Literal["refresh", "discover"]
CATALOG = Path(__file__).resolve().parents[3] / "config/automation-discovery.toml"
REFRESH_BUDGET = GitHubRequestLimits(
    total=120,
    repository_details=120,
    search=0,
    contributors=0,
    profiles=0,
)
REFRESH_LIMITS = ExecutionLimits(max_repositories=100, max_seconds=300)
COLLECTOR_ROLE = "github_radar_collector"
WRITE_TABLES = (
    "repositories",
    "repository_snapshots",
    "topics",
    "repository_topics",
    "discovery_provenance",
    "collection_runs",
)
READ_TABLES = (
    *WRITE_TABLES,
    "developers",
    "developer_snapshots",
    "repository_contributors",
    "repository_contributor_snapshots",
    "alembic_version",
)
PRIVATE_TABLES = ("workspace_developers", "workspace_repositories", "ai_artifacts")


class AutomationBlocked(ValueError):
    """A safe, fixed operator-facing reason; never include credential values."""


def discovery_plan(now: datetime) -> tuple[DiscoveryPlan, str]:
    data = CATALOG.read_bytes()
    catalog = DiscoveryConfig.model_validate(tomllib.loads(data.decode("utf-8")))
    plan = plan_discovery(catalog, as_of=now.astimezone(UTC).date())
    if (
        plan.contributors_limit
        or plan.budget.contributors
        or plan.budget.profiles
        or plan.budget.total > 36
        or plan.budget.search > 12
        or plan.budget.repository_details > 24
        or plan.execution.max_seconds > 300
        or plan.execution.max_candidates > 18
        or plan.execution.max_new_repositories > 10
    ):
        raise AutomationBlocked("Discovery catalog exceeds automation safety bounds")
    return plan, hashlib.sha256(data).hexdigest()


def preview(kind: Kind, now: datetime) -> dict[str, Any]:
    if kind == "discover":
        plan, digest = discovery_plan(now)
        return {
            "kind": kind,
            "mode": "dry-run",
            "catalog_sha256": digest,
            "plan": plan.model_dump(mode="json"),
        }
    return {
        "kind": kind,
        "mode": "dry-run",
        "min_refresh_hours": 24,
        "selection": "oldest observation first",
        "budget": REFRESH_BUDGET.model_dump(),
        "execution": REFRESH_LIMITS.model_dump(),
    }


def activation_reasons(kind: Kind, env: Mapping[str, str]) -> list[str]:
    flag = "RADAR_REFRESH_ENABLED" if kind == "refresh" else "RADAR_DISCOVERY_ENABLED"
    checks = {
        flag: "true",
        "RADAR_COLLECTION_LIVE": "true",
        "COLLECTOR_DIRECT_CONNECTION": "true",
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "zinverno/github-radar",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_RUN_ATTEMPT": "1",
    }
    reasons = [f"{key} must be {value}" for key, value in checks.items() if env.get(key) != value]
    if env.get("GITHUB_EVENT_NAME") not in {"schedule", "workflow_dispatch"}:
        reasons.append("Only schedule/workflow_dispatch on main may execute live collection")
    if not env.get("COLLECTOR_GITHUB_TOKEN", "").strip():
        reasons.append("COLLECTOR_GITHUB_TOKEN is missing")
    if not env.get("COLLECTOR_DATABASE_URL", "").strip():
        reasons.append("COLLECTOR_DATABASE_URL is missing")
    if kind == "discover" and not env.get("RADAR_DISCOVERY_PLAN_SHA256"):
        reasons.append("RADAR_DISCOVERY_PLAN_SHA256 is missing")
    return reasons


def acknowledged_runs(value: str) -> frozenset[str]:
    try:
        return frozenset(str(UUID(item.strip())) for item in value.split(",") if item.strip())
    except ValueError:
        raise AutomationBlocked(
            "Acknowledged unfinished run IDs must be comma-separated UUIDs"
        ) from None


@dataclass(frozen=True)
class CollectorCredentials:
    database_url: str = field(repr=False)
    token: str = field(repr=False)
    ca_pem: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> CollectorCredentials:
        value = env.get("COLLECTOR_DATABASE_URL", "")
        try:
            url = make_url(value)
            valid = (
                url.drivername == "postgresql+asyncpg"
                and url.username == COLLECTOR_ROLE
                and url.password
                and url.host
                and url.database
                and not url.query
            )
        except Exception:
            valid = False
        if not valid:
            raise AutomationBlocked(
                "COLLECTOR_DATABASE_URL requires the dedicated collector role, password, "
                "host, database and no query parameters"
            )
        return cls(value, env.get("COLLECTOR_GITHUB_TOKEN", ""), env.get("COLLECTOR_CA_PEM", ""))

    def engine(self) -> AsyncEngine:
        # An explicit context avoids SSLKEYLOGFILE and forbids URL SSL overrides.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_default_certs()
        if self.ca_pem:
            context.load_verify_locations(cadata=self.ca_pem)
        return create_async_engine(
            self.database_url,
            poolclass=NullPool,
            hide_parameters=True,
            connect_args={
                "ssl": context,
                "timeout": 10,
                "command_timeout": 15,
                "server_settings": {
                    "statement_timeout": "15000",
                    "lock_timeout": "5000",
                    "application_name": "github-radar-collector",
                    "search_path": "pg_catalog,public",
                },
            },
        )

    def settings(self, budget: GitHubRequestLimits) -> Settings:
        return Settings(  # type: ignore[call-arg]  # pydantic-settings runtime-only _env_file
            _env_file=None,
            github_token=self.token,
            database_url=self.database_url,
            deployment_mode="local",
            ai_api_key="",
            workspace_admin_token=SecretStr(""),
            github_api_base_url="https://api.github.com",
            github_api_version="2022-11-28",
            http_timeout_seconds=15,
            http_max_retries=3,
            rate_limit_pause_threshold=50,
            search_rate_limit_pause_threshold=2,
            github_request_budget=budget,
        )


async def check_collector_role(session: AsyncSession) -> None:
    row = (
        await session.execute(
            text("""
        SELECT current_user, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
        FROM pg_roles WHERE rolname = current_user
    """)
        )
    ).one()
    if row[0] != COLLECTOR_ROLE or any(row[1:]):
        raise AutomationBlocked("A non-administrative dedicated collector role is required")
    if await session.scalar(text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")):
        raise AutomationBlocked("Collector must not own or create objects in the public schema")
    if (await session.scalars(text("SELECT version_num FROM alembic_version"))).all() != ["0006"]:
        raise AutomationBlocked("Collector requires schema 0006; migrations are operator-only")
    for table in PRIVATE_TABLES:
        if await session.scalar(
            text(
                "SELECT has_table_privilege(current_user, :name, "
                "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                "OR has_any_column_privilege(current_user, :name, "
                "'SELECT,INSERT,UPDATE,REFERENCES')"
            ),
            {"name": f"public.{table}"},
        ):
            raise AutomationBlocked("Collector must have no private workspace or AI table access")
    for table in READ_TABLES:
        privileges = ["SELECT"]
        if table in WRITE_TABLES:
            privileges.append("INSERT")
            if table != "repository_topics":
                privileges.append("UPDATE")
        for privilege in privileges:
            if not await session.scalar(
                text("SELECT has_table_privilege(current_user, :name, :privilege)"),
                {"name": f"public.{table}", "privilege": privilege},
            ):
                raise AutomationBlocked("Collector is missing required public collection grants")
    if not await session.scalar(
        text("SELECT has_table_privilege(current_user, 'public.repository_topics', 'DELETE')")
    ):
        raise AutomationBlocked("Collector needs DELETE on repository_topics for topic refresh")
    for table in ("repositories", "topics", "repository_snapshots"):
        if not await session.scalar(
            text(
                "SELECT has_sequence_privilege(current_user, "
                "pg_get_serial_sequence(:name, 'id'), 'USAGE')"
            ),
            {"name": f"public.{table}"},
        ):
            raise AutomationBlocked("Collector is missing collection sequence usage grants")


def period_start(kind: Kind, now: datetime) -> datetime:
    day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return day if kind == "refresh" else day - timedelta(days=day.weekday())


async def run_scheduled(
    engine: AsyncEngine,
    settings: Settings,
    kind: Kind,
    *,
    plan: DiscoveryPlan | None = None,
    clock: Callable[[], datetime] = utcnow,
    acknowledged: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Ownership covers deduplication, preflight and every existing collector commit."""
    async with locked_collection_session(engine) as session:
        await check_collector_role(session)
        unfinished = set(
            await session.scalars(
                select(CollectionRunRow.id).where(
                    (CollectionRunRow.status == "running")
                    | (CollectionRunRow.finished_at.is_(None))
                )
            )
        )
        if unfinished - acknowledged:
            raise AutomationBlocked("Unfinished collection receipt requires operator review")
        prior = await session.scalar(
            select(CollectionRunRow)
            .where(
                CollectionRunRow.kind == kind,
                CollectionRunRow.started_at >= period_start(kind, clock()),
            )
            .order_by(CollectionRunRow.started_at.desc())
            .limit(1)
        )
        if prior is not None:
            return {
                "status": "skipped",
                "reason": "period already attempted; no automatic retry",
                "prior_run_id": prior.id,
                "prior_status": prior.status,
                "kind": kind,
                "acknowledged_unfinished_runs": sorted(unfinished & acknowledged),
            }
        async with GitHubClient(settings) as client:
            report = await CollectionService(client, session, clock=clock).run(
                kind,
                plan=plan,
                limits=REFRESH_LIMITS,
                min_refresh_hours=24,
                contributors_limit=0,
                fetch_profiles=False,
            )
        return {
            "status": report.status,
            "kind": kind,
            "report": report.model_dump(mode="json"),
            "acknowledged_unfinished_runs": sorted(unfinished & acknowledged),
        }


async def operator_report(session: AsyncSession, now: datetime) -> dict[str, Any]:
    now = now.astimezone(UTC)
    quality = await load_quality_report(
        session,
        catalog=load_discovery_config(CATALOG),
        assessed_at=now,
    )
    receipts = (
        await session.scalars(select(CollectionRunRow).order_by(CollectionRunRow.started_at.desc()))
    ).all()
    successful = [
        r
        for r in receipts
        if r.status == "completed"
        and r.finished_at
        and not stored_report(r).partial
        and stored_report(r).duration_is_final
    ]
    refresh = next((r for r in successful if r.kind == "refresh"), None)
    discovery = next((r for r in receipts if r.kind == "discover"), None)
    latest = (
        select(SnapshotRow.repository_id, func.max(SnapshotRow.captured_at).label("at"))
        .group_by(SnapshotRow.repository_id)
        .subquery()
    )
    due = await session.scalar(
        select(func.count())
        .select_from(RepositoryRow)
        .outerjoin(latest, latest.c.repository_id == RepositoryRow.id)
        .where((latest.c.at.is_(None)) | (latest.c.at <= now - timedelta(hours=24)))
    )
    # UTC day presence is a coverage diagnostic, not a claim of evenly spaced observations.
    since = now.astimezone(UTC).date() - timedelta(days=7)
    coverage: dict[int, set[date]] = {}
    for repo_id, captured in await session.execute(
        select(SnapshotRow.repository_id, SnapshotRow.captured_at).where(
            SnapshotRow.captured_at >= datetime.combine(since, datetime.min.time(), UTC)
        )
    ):
        coverage.setdefault(repo_id, set()).add(captured.astimezone(UTC).date())
    gaps = []
    for repo_id, first in await session.execute(
        select(RepositoryRow.id, RepositoryRow.first_seen_at)
    ):
        start = max(since, first.astimezone(UTC).date())
        missing = [
            start + timedelta(days=i)
            for i in range((now.date() - start).days)
            if start + timedelta(days=i) not in coverage.get(repo_id, set())
        ]
        if missing:
            gaps.append(
                {"repository_id": repo_id, "missing_utc_days": [d.isoformat() for d in missing]}
            )
    return {
        "mode": "read-only",
        "assessed_at": now.isoformat(),
        "last_successful_refresh": refresh.finished_at.isoformat()
        if refresh and refresh.finished_at
        else None,
        "last_discovery": {
            "run_id": discovery.id,
            "status": discovery.status,
            "started_at": discovery.started_at.isoformat(),
        }
        if discovery
        else None,
        "repositories_due": due,
        "freshness": {
            key: quality["coverage"][key]
            for key in (
                "total_repositories",
                "repository_observations",
                "stale_observations",
                "never_observed",
                "windows",
            )
        },
        "missing_observation_days_last_7_complete_utc_days": gaps,
        "collection": {
            key: quality["collection"][key]
            for key in (
                "run_count",
                "requests",
                "request_accounting",
                "status_counts",
                "partial_or_unfinished_runs",
                "budget_exhausted_runs",
            )
        },
        "recent_runs": list(reversed(quality["collection"]["runs"]))[:20],
        "problem_runs": [
            {
                "run_id": r.id,
                "kind": r.kind,
                "status": r.status,
                "stop_reason": stored_report(r).stop_reason,
                "failed": stored_report(r).failed,
            }
            for r in receipts
            if stored_report(r).partial
        ][:20],
        "unfinished_runs": [r.id for r in receipts if r.status == "running" or not r.finished_at],
    }
