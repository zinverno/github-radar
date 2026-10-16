"""Offline collection scenarios, also exercised against guarded PostgreSQL."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import BigInteger, func, select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.compiler import compiles

from github_radar.config.settings import GitHubRequestLimits
from github_radar.discovery.planning import ExecutionLimits, PlannedCohort, make_plan
from github_radar.domain import RepositorySnapshot
from github_radar.github.client import GitHubClient
from github_radar.github.models import GithubRepository
from github_radar.services.collection import CollectionService, stored_report
from github_radar.services.update import RepositoryUpdateService
from github_radar.storage import repositories as storage
from github_radar.storage.db import make_session_factory
from github_radar.storage.models import (
    Base,
    CollectionRunRow,
    ContributorSnapshotRow,
    DeveloperSnapshotRow,
    DiscoveryProvenanceRow,
    RepositoryRow,
    SnapshotRow,
)
from tests.conftest import BASE_URL, contributors_payload, repo_detail_payload, user_payload

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)


@compiles(BigInteger, "sqlite")
def _sqlite_bigint(element: Any, compiler: Any, **kw: Any) -> str:
    return "INTEGER"


@pytest.fixture
async def database():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


def plan(*, count=2, cohorts=1, budget=None, execution=None, per_page=100):
    return make_plan(
        tuple(
            PlannedCohort(
                category=f"category-{i}",
                recipe=f"recipe-{i}",
                query=f"query-{i}",
                sort="stars",
                order="desc",
                limit=count,
            )
            for i in range(cohorts)
        ),
        as_of=NOW.date(),
        per_page=per_page,
        contributors_limit=10,
        budget=budget or GitHubRequestLimits(),
        execution=execution,
    )


def mock_repo(api_mock, identity=1, name="one", stars=100):
    payload = repo_detail_payload(github_id=identity, name=name, stars=stars, topics=("mcp",))
    api_mock.get(f"{BASE_URL}/repos/octo/{name}").mock(
        return_value=httpx.Response(200, json=payload)
    )
    return payload


def mock_search(api_mock, items, *, link=None):
    return api_mock.get(f"{BASE_URL}/search/repositories").mock(
        return_value=httpx.Response(
            200,
            json={"items": items, "total_count": len(items), "incomplete_results": False},
            headers={"link": link} if link else {},
        )
    )


async def run(database, settings, kind="discover", *, at=NOW, **kwargs):
    async with make_session_factory(database)() as session, GitHubClient(settings) as client:
        return await CollectionService(client, session, clock=lambda: at).run(kind, **kwargs)


async def seed(database, *, identity=1, name="one", at=NOW, stars=100):
    repo = GithubRepository.model_validate(
        repo_detail_payload(
            github_id=identity,
            name=name,
            stars=stars,
            topics=("mcp",),
        )
    ).to_domain()
    async with make_session_factory(database)() as session:
        row, _ = await storage.upsert_repository(session, repo, now=at)
        await storage.record_observation(
            session, row.id, RepositorySnapshot.from_repository(repo, at)
        )
        await session.commit()
        return row.id


async def counts(database):
    async with make_session_factory(database)() as session:
        return {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in (
                RepositoryRow,
                SnapshotRow,
                DiscoveryProvenanceRow,
                ContributorSnapshotRow,
                DeveloperSnapshotRow,
            )
        }


async def test_cross_recipe_deduplication_and_restart(database, settings, api_mock):
    one = mock_repo(api_mock, 1, "one")
    two = mock_repo(api_mock, 2, "two")
    mock_search(api_mock, [one, two])
    first = await run(database, settings, plan=plan(cohorts=2))
    assert not first.partial
    assert first.candidates_examined == 4
    assert first.unique_repositories_discovered == first.new_repositories_saved == 2
    assert first.skipped == 2
    assert first.observations_inserted == 2
    assert first.api_requests_consumed == {
        "total": 4,
        "search": 2,
        "repository_details": 2,
        "contributors": 0,
        "profiles": 0,
    }
    second = await run(database, settings, plan=plan(cohorts=2))
    assert second.existing_repositories_reused == 2
    assert second.new_repositories_saved == second.observations_inserted == 0
    assert second.api_requests_consumed["total"] == 2
    saved = await counts(database)
    assert saved["repositories"] == saved["repository_snapshots"] == 2
    assert saved["discovery_provenance"] == 4
    async with make_session_factory(database)() as session:
        rows = (await session.scalars(select(DiscoveryProvenanceRow))).all()
        assert {row.query for row in rows} == {"query-0", "query-1"}
        assert {row.last_run_id for row in rows} == {second.run_id}


async def test_stable_identity_rename_and_name_collision(database, settings, api_mock):
    await seed(database)
    renamed = repo_detail_payload(github_id=1, name="renamed")
    mock_search(api_mock, [renamed])
    report = await run(database, settings, plan=plan(count=1))
    assert report.existing_repositories_reused == 1
    assert report.api_requests_consumed["repository_details"] == 0
    async with make_session_factory(database)() as session:
        row = await storage.get_repository_by_github_id(session, 1)
        assert row is not None
        assert row.full_name == "octo/renamed"
    replacement = mock_repo(api_mock, 2, "renamed")
    mock_search(api_mock, [replacement])
    report = await run(database, settings, plan=plan(count=1))
    assert report.partial and report.failed == 1
    assert report.new_repositories_saved == 0
    assert (await counts(database))["repositories"] == 1


async def test_identity_mismatch_rolls_back(database, settings, api_mock):
    mock_search(api_mock, [repo_detail_payload(github_id=1, name="one")])
    mock_repo(api_mock, 999, "one")
    report = await run(database, settings, plan=plan(count=1))
    assert report.partial and report.failed == 1
    assert report.new_repositories_saved == report.observations_inserted == 0
    assert (await counts(database))["repositories"] == 0


async def test_budget_exhaustion_during_pagination_preserves_first_commit(
    database, settings, api_mock
):
    one = mock_repo(api_mock)
    search = mock_search(
        api_mock, [one], link=f'<{BASE_URL}/search/repositories?page=2>; rel="next"'
    )
    report = await run(
        database,
        settings,
        plan=plan(
            per_page=1,
            budget=GitHubRequestLimits(total=2),
        ),
    )
    assert search.call_count == 1
    assert report.partial and report.budget_exhausted
    assert report.new_repositories_saved == report.observations_inserted == 1
    assert report.api_requests_consumed["total"] == 2
    assert (await counts(database))["repositories"] == 1


async def test_retry_costs_are_durable(database, settings, api_mock):
    item = mock_repo(api_mock)
    api_mock.get(f"{BASE_URL}/search/repositories").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"items": [item]}),
        ]
    )
    report = await run(database, settings, plan=plan(count=1, budget=GitHubRequestLimits(total=2)))
    assert report.partial and report.budget_exhausted
    assert report.api_requests_consumed["search"] == 2
    assert report.new_repositories_saved == 0
    async with make_session_factory(database)() as session:
        row = await session.get(CollectionRunRow, report.run_id)
        assert row is not None
        assert stored_report(row).api_requests_consumed == report.api_requests_consumed


async def test_enrichment_budget_and_ttl_restart(database, settings, api_mock):
    await seed(database)
    mock_repo(api_mock)
    contributors = api_mock.get(f"{BASE_URL}/repos/octo/one/contributors").mock(
        return_value=httpx.Response(
            200, json=contributors_payload([("alice", 10, 5), ("bob", 11, 2)])
        )
    )
    alice = api_mock.get(f"{BASE_URL}/users/alice").mock(
        return_value=httpx.Response(200, json=user_payload(login="alice", github_id=10))
    )
    bob = api_mock.get(f"{BASE_URL}/users/bob").mock(
        return_value=httpx.Response(200, json=user_payload(login="bob", github_id=11))
    )
    settings.github_request_budget = GitHubRequestLimits(profiles=1)
    first = await run(
        database, settings, "enrich", repositories=("octo/one",), contributors_limit=2
    )
    assert first.partial and first.budget_exhausted
    assert first.contributor_profiles_fetched == first.developer_observations_inserted == 1
    assert first.contributor_observations_inserted == 2
    second = await run(database, settings, "enrich", repositories=("1",), contributors_limit=2)
    assert not second.partial
    assert second.contributor_batches_reused == 1
    assert second.contributor_profiles_fetched == second.contributor_profiles_reused == 1
    assert second.api_requests_consumed["total"] == 1
    assert contributors.call_count == alice.call_count == bob.call_count == 1
    saved = await counts(database)
    assert saved["repository_contributor_snapshots"] == saved["developer_snapshots"] == 2
    third = await run(database, settings, "enrich", repositories=("1",), contributors_limit=2)
    assert not third.partial and third.api_requests_consumed["total"] == 0
    assert third.contributor_profiles_reused == 2


async def test_empty_contributors_checkpoint_and_expiry(database, settings, api_mock):
    await seed(database)
    mock_repo(api_mock)
    route = api_mock.get(f"{BASE_URL}/repos/octo/one/contributors").mock(
        return_value=httpx.Response(204)
    )
    first = await run(database, settings, "enrich", repositories=("1",))
    second = await run(database, settings, "enrich", repositories=("1",))
    assert not first.partial and second.contributor_batches_reused == 1
    assert route.call_count == 1
    await run(database, settings, "enrich", repositories=("1",), at=NOW + timedelta(days=7))
    assert route.call_count == 2


async def test_stale_first_uses_observations_and_minimum_interval(database, settings, api_mock):
    await seed(database, identity=1, name="old", at=NOW - timedelta(days=2))
    await seed(database, identity=2, name="new", at=NOW - timedelta(hours=1))
    async with make_session_factory(database)() as session:
        old = await storage.get_repository_by_github_id(session, 1)
        assert old is not None
        old.last_seen_at = NOW
        await session.commit()
    mock_repo(api_mock, 1, "old")
    first = await run(database, settings, "refresh", limits=ExecutionLimits(max_repositories=1))
    assert not first.partial and first.repositories_refreshed == first.observations_inserted == 1
    assert (await counts(database))["repository_snapshots"] == 3
    second = await run(database, settings, "refresh")
    assert second.repositories_refreshed == second.api_requests_consumed["total"] == 0


async def test_same_instant_refresh_and_old_history_are_preserved(database, settings, api_mock):
    row_id = await seed(database, at=NOW - timedelta(days=1), stars=50)
    mock_repo(api_mock, stars=50)
    async with make_session_factory(database)() as session, GitHubClient(settings) as client:
        row = await session.get(RepositoryRow, row_id)
        assert row is not None
        service = RepositoryUpdateService(client, session)
        assert await service.stage_observation(row, now=NOW)
        await session.commit()
        mock_repo(api_mock, stars=60)
        assert not await service.stage_observation(row, now=NOW)
        await session.commit()
        with pytest.raises(ValueError, match="precedes"):
            await service.stage_observation(row, now=NOW - timedelta(days=1))
        snapshots = await storage.snapshots_for_repository(session, row_id)
        assert [snapshot.stars for snapshot in snapshots] == [50, 60]


async def test_interruption_and_restart(database, settings, api_mock):
    one = mock_repo(api_mock, 1, "one")
    two = mock_repo(api_mock, 2, "two")
    mock_search(api_mock, [one, two])

    async def interrupted(request):
        raise asyncio.CancelledError

    api_mock.get(f"{BASE_URL}/repos/octo/two").mock(side_effect=interrupted)
    with pytest.raises(asyncio.CancelledError):
        await run(database, settings, plan=plan())
    assert (await counts(database))["repositories"] == 1
    async with make_session_factory(database)() as session:
        row = (await session.scalars(select(CollectionRunRow))).one()
        report = stored_report(row)
        assert report.partial and report.status == "partial"
        assert report.new_repositories_saved == 1
        assert report.api_requests_consumed["total"] == 3
    mock_repo(api_mock, 2, "two")
    restarted = await run(database, settings, plan=plan())
    assert not restarted.partial
    assert restarted.existing_repositories_reused == restarted.new_repositories_saved == 1
    assert (await counts(database))["repository_snapshots"] == 2


async def test_hard_shutdown_leaves_honest_unfinished_receipt(
    database, settings, api_mock, monkeypatch
):
    class PowerLoss(BaseException):
        pass

    one = mock_repo(api_mock)
    mock_search(api_mock, [one])

    async def crash(*args, **kwargs):
        raise PowerLoss

    with monkeypatch.context() as patch:
        patch.setattr("github_radar.services.collection.collection_storage.note_provenance", crash)
        with pytest.raises(PowerLoss):
            await run(database, settings, plan=plan(count=1))
    assert (await counts(database))["repositories"] == 0
    async with make_session_factory(database)() as session:
        row = (await session.scalars(select(CollectionRunRow))).one()
        report = stored_report(row)
        assert report.partial and report.status == "running"
        assert report.new_repositories_saved == 0
        assert report.api_requests_consumed["total"] == 2
    restarted = await run(database, settings, plan=plan(count=1))
    assert not restarted.partial and restarted.new_repositories_saved == 1


@pytest.mark.parametrize(
    "limits",
    [
        ExecutionLimits(max_candidates=1),
        ExecutionLimits(max_new_repositories=0),
    ],
)
async def test_execution_limits_are_partial(database, settings, api_mock, limits):
    one = mock_repo(api_mock)
    two = mock_repo(api_mock, 2, "two")
    mock_search(api_mock, [one, two])
    report = await run(database, settings, plan=plan(execution=limits))
    assert report.partial and report.budget_exhausted
    assert report.new_repositories_saved <= limits.max_new_repositories
    assert report.candidates_examined <= limits.max_candidates


async def test_runtime_deadline_stops_http(database, settings, api_mock):
    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json={"items": []})

    api_mock.get(f"{BASE_URL}/search/repositories").mock(side_effect=slow)
    report = await run(database, settings, plan=plan(execution=ExecutionLimits(max_seconds=1)))
    assert report.partial and report.budget_exhausted
    assert report.api_requests_consumed["total"] == 1
    assert report.new_repositories_saved == 0


async def test_incomplete_search_is_not_success(database, settings, api_mock):
    api_mock.get(f"{BASE_URL}/search/repositories").mock(
        return_value=httpx.Response(
            200,
            json={"items": [repo_detail_payload(github_id=1)], "incomplete_results": True},
        )
    )
    report = await run(database, settings, plan=plan(count=1))
    assert report.partial and report.failed == 1
    assert "incomplete" in report.stop_reason
    assert report.new_repositories_saved == 0


async def test_same_instant_enrichment_counts_only_new_observations(database, settings, api_mock):
    await seed(database)
    mock_repo(api_mock)
    api_mock.get(f"{BASE_URL}/repos/octo/one/contributors").mock(
        return_value=httpx.Response(
            200,
            json=contributors_payload([("alice", 10, 5)]),
        )
    )
    api_mock.get(f"{BASE_URL}/users/alice").mock(
        return_value=httpx.Response(
            200,
            json=user_payload(login="alice", github_id=10),
        )
    )
    reports = []
    for _ in range(2):
        async with make_session_factory(database)() as session, GitHubClient(settings) as client:
            reports.append(
                await CollectionService(
                    client,
                    session,
                    profile_refresh_days=0,
                    clock=lambda: NOW,
                ).run("enrich", repositories=("1",))
            )
    assert reports[0].contributor_observations_inserted == 1
    assert reports[0].developer_observations_inserted == 1
    assert not reports[1].partial
    assert reports[1].contributor_observations_inserted == 0
    assert reports[1].developer_observations_inserted == 0
    assert reports[1].contributor_profiles_fetched == 1


async def test_never_observed_first_and_stable_refresh_tie_break(database, settings, api_mock):
    await seed(database, identity=2, name="two", at=NOW - timedelta(days=2))
    await seed(database, identity=1, name="one", at=NOW - timedelta(days=2))
    async with make_session_factory(database)() as session:
        repo = GithubRepository.model_validate(repo_detail_payload(github_id=3, name="three"))
        await storage.upsert_repository(session, repo.to_domain(), now=NOW)
        await session.commit()
    mock_repo(api_mock, 3, "three")
    mock_repo(api_mock, 1, "one")
    first = await run(database, settings, "refresh", limits=ExecutionLimits(max_repositories=1))
    assert first.operations[0].target == "octo/three"
    second = await run(database, settings, "refresh", limits=ExecutionLimits(max_repositories=1))
    assert second.operations[0].target == "octo/one"
