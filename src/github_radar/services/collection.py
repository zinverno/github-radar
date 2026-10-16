"""Serial bounded collection with per-operation commits and durable run receipts."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.discovery.planning import DiscoveryPlan, ExecutionLimits
from github_radar.discovery.service import RepositoryDiscoveryService
from github_radar.github.budget import RequestBudget, RequestBudgetExceeded
from github_radar.github.client import GitHubClient
from github_radar.github.errors import GitHubAuthenticationError, GitHubError, RateLimitExceeded
from github_radar.github.repositories import get_contributors, get_repository, search_repositories
from github_radar.services.developers import DeveloperSyncService
from github_radar.services.update import RepositoryUpdateService
from github_radar.storage import collection as collection_storage
from github_radar.storage import repositories as storage
from github_radar.storage.models import (
    CollectionRunRow,
    ContributorSnapshotRow,
    DeveloperRow,
    DeveloperSnapshotRow,
    RepositoryRow,
)
from github_radar.util import utcnow

RunKind = Literal["discover", "enrich", "refresh"]
Outcome = Literal["completed", "skipped", "failed", "budget_exhausted"]


class Operation(BaseModel):
    kind: str
    target: str
    outcome: Outcome
    reason: str = ""


class CollectionReport(BaseModel):
    run_id: str
    kind: RunKind
    status: Literal["running", "completed", "partial"] = "running"
    partial: bool = True
    stop_reason: str | None = None
    candidates_examined: int = 0
    unique_repositories_discovered: int = 0
    existing_repositories_reused: int = 0
    new_repositories_saved: int = 0
    repositories_refreshed: int = 0
    observations_inserted: int = 0
    contributor_observations_inserted: int = 0
    contributor_batches_reused: int = 0
    contributor_profiles_fetched: int = 0
    contributor_profiles_reused: int = 0
    developer_observations_inserted: int = 0
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    budget_exhausted: bool = False
    api_requests_consumed: dict[str, int] = Field(default_factory=dict)
    request_accounting: Literal["reserved_before_transport"] = "reserved_before_transport"
    duration_seconds: float = 0
    duration_is_final: bool = False
    operations: list[Operation] = Field(default_factory=list)

    def note(self, kind: str, target: str, outcome: Outcome, reason: str = "") -> None:
        self.operations.append(Operation(kind=kind, target=target, outcome=outcome, reason=reason))
        if outcome in {"completed", "skipped", "failed"}:
            setattr(self, outcome, getattr(self, outcome) + 1)


class CollectionStopped(Exception):
    """A runtime/candidate/repository boundary stopped this invocation."""


class CollectionService:
    """One client, one shared budget, one in-flight operation (concurrency = 1)."""

    def __init__(
        self,
        client: GitHubClient,
        session: AsyncSession,
        *,
        profile_refresh_days: int = 7,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        if profile_refresh_days < 0:
            raise ValueError("profile_refresh_days must be nonnegative")
        self.client = client
        self.session = session
        self.profile_refresh_days = profile_refresh_days
        self.clock = clock
        self.report: CollectionReport
        self._started = 0.0
        self._active_kind = "run"
        self._active_target = "run"

    async def run(
        self,
        kind: RunKind,
        *,
        plan: DiscoveryPlan | None = None,
        limits: ExecutionLimits | None = None,
        repositories: tuple[str, ...] = (),
        contributors_limit: int = 30,
        fetch_profiles: bool = True,
        min_refresh_hours: int = 24,
    ) -> CollectionReport:
        if kind not in {"discover", "enrich", "refresh"}:
            raise ValueError("Unknown collection operation")
        if self.client.budget.used["total"] or self.client.before_attempt is not None:
            raise ValueError("Start a collection run with a fresh client")
        if not 0 <= contributors_limit <= 1000 or not 0 <= min_refresh_hours <= 87600:
            raise ValueError("Invalid contributor/refresh bounds")
        if kind == "discover":
            if plan is None:
                raise ValueError("Discovery requires a reviewed plan")
            limits = plan.execution
            self.client.budget = RequestBudget(plan.budget)
        limits = limits or ExecutionLimits()
        if kind == "enrich" and (not repositories or len(repositories) > limits.max_repositories):
            raise ValueError("Enrichment requires explicit repositories within max_repositories")
        inputs = {
            "plan": plan.model_dump(mode="json") if plan else None,
            "execution": limits.model_dump(),
            "repositories": repositories,
            "budget": self.client.budget.limits.model_dump(),
            "contributors_limit": contributors_limit,
            "fetch_profiles": fetch_profiles,
            "profile_refresh_days": self.profile_refresh_days,
            "min_refresh_hours": min_refresh_hours,
        }
        self.report = CollectionReport(run_id=str(uuid4()), kind=kind)
        self._started = time.monotonic()
        self.session.add(
            CollectionRunRow(
                id=self.report.run_id,
                kind=kind,
                started_at=self.clock(),
                status="running",
                input_json=json.dumps(inputs),
                report_json=self.report.model_dump_json(),
                requests_json=json.dumps(self.client.budget.used),
            )
        )
        await self.session.commit()
        self.client.before_attempt = self._reserve_attempt
        cancelled = False
        try:
            async with asyncio.timeout(limits.max_seconds):
                if kind == "discover":
                    assert plan is not None
                    await self._discover(plan)
                elif kind == "refresh":
                    await self._refresh(limits, min_refresh_hours)
                else:
                    await self._enrich(repositories, contributors_limit, fetch_profiles)
        except collection_storage.CollectionLockError:
            # The durable receipt stays unfinished; recovery must not use an unowned backend.
            raise
        except (RequestBudgetExceeded, RateLimitExceeded, CollectionStopped, TimeoutError) as exc:
            await self._rollback_report()
            self.report.stop_reason = str(exc) or "runtime deadline reached"
            self.report.budget_exhausted = isinstance(
                exc, (RequestBudgetExceeded, TimeoutError, CollectionStopped)
            )
            self.report.note(
                self._active_kind,
                self._active_target,
                "budget_exhausted" if self.report.budget_exhausted else "skipped",
                self.report.stop_reason,
            )
        except asyncio.CancelledError:
            cancelled = True
            await self._rollback_report()
            self.report.stop_reason = "interrupted"
        except Exception as exc:
            await self._rollback_report()
            self.report.stop_reason = str(exc) if type(exc) is GitHubError else type(exc).__name__
            self.report.note(
                self._active_kind, self._active_target, "failed", self.report.stop_reason
            )
        finally:
            self.client.before_attempt = None
        self.report.partial = bool(self.report.failed or self.report.stop_reason)
        self.report.status = "partial" if self.report.partial else "completed"
        await self._checkpoint(finished=True)
        if cancelled:
            raise asyncio.CancelledError
        return self.report

    async def _reserve_attempt(self, used: dict[str, int]) -> None:
        # No network may occur between staging domain writes and their atomic receipt.
        if self.session.new or self.session.dirty or self.session.deleted:
            raise RuntimeError("HTTP attempted with uncommitted domain changes")
        await self.session.execute(
            update(CollectionRunRow)
            .where(CollectionRunRow.id == self.report.run_id)
            .values(requests_json=json.dumps(used))
        )
        await self.session.commit()

    async def _checkpoint(self, *, finished: bool = False) -> None:
        self.report.api_requests_consumed = self.client.budget.used
        self.report.duration_seconds = round(time.monotonic() - self._started, 3)
        self.report.duration_is_final = finished
        await self.session.execute(
            update(CollectionRunRow)
            .where(CollectionRunRow.id == self.report.run_id)
            .values(
                report_json=self.report.model_dump_json(),
                status=self.report.status,
                finished_at=self.clock() if finished else None,
            )
        )
        await self.session.commit()

    async def _rollback_report(self) -> None:
        await self.session.rollback()
        saved = await self.session.scalar(
            select(CollectionRunRow.report_json).where(CollectionRunRow.id == self.report.run_id)
        )
        assert saved is not None
        self.report = CollectionReport.model_validate_json(saved)

    async def _failure(self, kind: str, target: str, exc: Exception) -> None:
        if isinstance(exc, (RequestBudgetExceeded, RateLimitExceeded, GitHubAuthenticationError)):
            raise exc
        await self._rollback_report()
        self.report.note(kind, target, "failed", type(exc).__name__)
        await self._checkpoint()

    async def _discover(self, plan: DiscoveryPlan) -> None:
        service = RepositoryDiscoveryService(self.client, self.session, per_page=plan.per_page)
        seen: set[int] = set()
        for cohort in plan.cohorts:
            self._active_kind, self._active_target = "search", f"{cohort.category}/{cohort.recipe}"
            remaining = plan.execution.max_candidates - self.report.candidates_examined
            if remaining <= 0:
                raise CollectionStopped("max_candidates reached before all cohorts completed")
            async for candidate in search_repositories(
                self.client,
                query=cohort.query,
                sort=cohort.sort,
                order=cohort.order,
                per_page=min(plan.per_page, cohort.limit, remaining),
                limit=min(cohort.limit, remaining),
            ):
                self.report.candidates_examined += 1
                duplicate = candidate.github_id in seen
                if not duplicate:
                    seen.add(candidate.github_id)
                    self.report.unique_repositories_discovered += 1
                await self._checkpoint()
                try:
                    self._active_kind, self._active_target = "repository", candidate.full_name
                    row = await storage.get_repository_by_github_id(
                        self.session, candidate.github_id
                    )
                    if duplicate:
                        self.report.note(
                            "repository", candidate.full_name, "skipped", "duplicate ID"
                        )
                    else:
                        if row is None and (
                            self.report.new_repositories_saved
                            >= plan.execution.max_new_repositories
                        ):
                            raise CollectionStopped("max_new_repositories reached")
                        row, created = await service.stage_lightweight(candidate, now=self.clock())
                        if created:
                            self.report.new_repositories_saved += 1
                            self.report.observations_inserted += 1
                        else:
                            self.report.existing_repositories_reused += 1
                        self.report.note(
                            "repository",
                            candidate.full_name,
                            "completed",
                            "saved" if created else "reused",
                        )
                    if row is not None:
                        await collection_storage.note_provenance(
                            self.session,
                            row.id,
                            category=cohort.category,
                            recipe=cohort.recipe,
                            query=cohort.query,
                            run_id=self.report.run_id,
                            now=self.clock(),
                        )
                    await self._checkpoint()
                except (GitHubError, ValueError, SQLAlchemyError) as exc:
                    await self._failure("repository", candidate.full_name, exc)
                self._active_kind, self._active_target = (
                    "search",
                    f"{cohort.category}/{cohort.recipe}",
                )
            if remaining < cohort.limit:
                raise CollectionStopped("max_candidates truncated a cohort")

    async def _refresh(self, limits: ExecutionLimits, min_refresh_hours: int) -> None:
        rows = await collection_storage.stale_repositories(
            self.session,
            cutoff=self.clock() - timedelta(hours=min_refresh_hours),
            limit=limits.max_repositories,
        )
        targets = [(row.id, row.full_name) for row in rows]
        service = RepositoryUpdateService(self.client, self.session)
        for row_id, name in targets:
            self._active_kind, self._active_target = "refresh", name
            try:
                row = await self.session.get(RepositoryRow, row_id)
                assert row is not None
                inserted = await service.stage_observation(row, now=self.clock())
                self.report.repositories_refreshed += 1
                self.report.observations_inserted += int(inserted)
                self.report.note("refresh", name, "completed")
                await self._checkpoint()
            except (GitHubError, ValueError, SQLAlchemyError) as exc:
                await self._failure("refresh", name, exc)

    async def _enrich(
        self,
        repositories: tuple[str, ...],
        contributors_limit: int,
        fetch_profiles: bool,
    ) -> None:
        seen_repositories: set[int] = set()
        seen_profiles: set[int] = set()
        profiles = DeveloperSyncService(
            self.client, self.session, refresh_days=self.profile_refresh_days
        )
        for reference in repositories:
            self._active_kind, self._active_target = "contributors", reference
            try:
                row = (
                    await storage.get_repository_by_github_id(self.session, int(reference))
                    if reference.isdecimal()
                    else await storage.get_repository_by_full_name(self.session, reference)
                )
                if row is None:
                    raise ValueError("Repository is not tracked")
                if row.github_id in seen_repositories:
                    self.report.note("enrichment", reference, "skipped", "duplicate ID")
                    await self._checkpoint()
                    continue
                seen_repositories.add(row.github_id)
                now = self.clock()
                fresh = (
                    row.contributors_fetched_at is not None
                    and row.contributors_fetched_at.replace(tzinfo=UTC)
                    > now - timedelta(days=self.profile_refresh_days)
                    and (row.contributors_fetched_limit or 0) >= contributors_limit
                )
                if fresh:
                    contributors = await collection_storage.cached_contributors(
                        self.session, row.id, limit=contributors_limit
                    )
                    self.report.contributor_batches_reused += 1
                elif contributors_limit:
                    detail = await get_repository(self.client, row.owner_login, row.name)
                    if detail.github_id != row.github_id:
                        raise ValueError("Repository identity changed before enrichment")
                    contributors = await get_contributors(
                        self.client, row.owner_login, row.name, limit=contributors_limit
                    )
                    # Duplicate contributor IDs must not duplicate observations or profile work.
                    contributors = list({c.developer.github_id: c for c in contributors}.values())
                    count_query = (
                        select(func.count())
                        .select_from(ContributorSnapshotRow)
                        .where(
                            ContributorSnapshotRow.repository_id == row.id,
                            ContributorSnapshotRow.captured_at == now,
                        )
                    )
                    before = await self.session.scalar(count_query) or 0
                    await storage.sync_contributors(self.session, row, contributors, now=now)
                    row.contributors_fetched_at = now
                    row.contributors_fetched_limit = contributors_limit
                    after = await self.session.scalar(count_query) or 0
                    self.report.contributor_observations_inserted += after - before
                else:
                    contributors = []
                self.report.note(
                    "contributors", reference, "completed", "reused" if fresh else "saved"
                )
                await self._checkpoint()
                if not fetch_profiles:
                    continue
                for contributor in contributors:
                    identity = contributor.developer.github_id
                    if identity in seen_profiles:
                        continue
                    seen_profiles.add(identity)
                    try:
                        self._active_kind = "profile"
                        self._active_target = contributor.developer.login
                        captured_at = self.clock()
                        existing_snapshot = await self.session.scalar(
                            select(DeveloperSnapshotRow.id)
                            .join(
                                DeveloperRow, DeveloperRow.id == DeveloperSnapshotRow.developer_id
                            )
                            .where(
                                DeveloperRow.github_id == identity,
                                DeveloperSnapshotRow.captured_at == captured_at,
                            )
                        )
                        fetched = await profiles.stage_profile(contributor, now=captured_at)
                        self.report.contributor_profiles_fetched += int(fetched)
                        self.report.developer_observations_inserted += int(
                            fetched and existing_snapshot is None
                        )
                        self.report.contributor_profiles_reused += int(not fetched)
                        self.report.note(
                            "profile",
                            contributor.developer.login,
                            "completed",
                            "fetched" if fetched else "reused",
                        )
                        await self._checkpoint()
                    except (GitHubError, ValueError, SQLAlchemyError) as exc:
                        await self._failure("profile", contributor.developer.login, exc)
            except (GitHubError, ValueError, SQLAlchemyError) as exc:
                await self._failure("enrichment", reference, exc)


def stored_report(row: CollectionRunRow) -> CollectionReport:
    report = CollectionReport.model_validate_json(row.report_json)
    report.api_requests_consumed = json.loads(row.requests_json)
    if row.status == "running":
        report.partial = True
        report.stop_reason = "unfinished: may still be running or may have been interrupted"
    return report
