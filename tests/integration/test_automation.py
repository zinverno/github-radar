"""Real locks/role grants and fake GitHub only, in the guarded disposable database."""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from github_radar.services import automation as policy
from github_radar.services.collection import CollectionService, stored_report
from github_radar.storage.collection import CollectionLockError, locked_collection_session
from github_radar.storage.db import make_session_factory
from github_radar.storage.models import CollectionRunRow, SnapshotRow
from tests.conftest import BASE_URL
from tests.integration.safety import validate_test_database_url
from tests.integration.test_bounded_collection import database as database
from tests.unit.test_collection import NOW, mock_repo, mock_search, seed

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="test URL unset"),
]


@pytest.fixture
async def collector(database, settings):
    url = os.environ["TEST_DATABASE_URL"]
    validate_test_database_url(test_url=url, app_url=os.environ.get("DATABASE_URL"))
    assert make_url(url).database == "github_radar_test"
    async with database.begin() as conn:
        assert not await conn.scalar(
            text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": policy.COLLECTOR_ROLE}
        )
        await conn.execute(text("CREATE ROLE github_radar_collector NOLOGIN"))
        await conn.execute(text("GRANT USAGE ON SCHEMA public TO github_radar_collector"))
        await conn.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) PRIMARY KEY)")
        )
        await conn.execute(text("DELETE FROM alembic_version"))
        await conn.execute(text("INSERT INTO alembic_version VALUES ('0006')"))
        for table in policy.READ_TABLES:
            await conn.execute(text(f"GRANT SELECT ON {table} TO github_radar_collector"))
        for table in policy.WRITE_TABLES:
            privileges = "INSERT" if table == "repository_topics" else "INSERT, UPDATE"
            await conn.execute(text(f"GRANT {privileges} ON {table} TO github_radar_collector"))
        await conn.execute(text("GRANT DELETE ON repository_topics TO github_radar_collector"))
        for table in ("repositories", "topics", "repository_snapshots"):
            sequence = await conn.scalar(
                text("SELECT pg_get_serial_sequence(:name, 'id')"), {"name": table}
            )
            await conn.execute(
                text(f"GRANT USAGE ON SEQUENCE {sequence} TO github_radar_collector")
            )
    # Authenticate only to the guarded local cluster; SET ROLE exercises real restricted grants.
    engine = create_async_engine(
        url, poolclass=NullPool, connect_args={"server_settings": {"role": policy.COLLECTOR_ROLE}}
    )
    settings.github_request_budget = policy.REFRESH_BUDGET
    try:
        yield engine
    finally:
        await engine.dispose()
        async with database.begin() as conn:
            await conn.execute(text("DROP OWNED BY github_radar_collector"))
            await conn.execute(text("DROP ROLE github_radar_collector"))


async def invoke(collector, settings, kind="refresh", at=NOW, **kwargs):
    return await policy.run_scheduled(collector, settings, kind, clock=lambda: at, **kwargs)


async def test_collector_grants_exclude_link_updates_and_private_reads(collector):
    async with collector.connect() as conn:
        assert not await conn.scalar(
            text("SELECT has_table_privilege(current_user, 'repository_topics', 'UPDATE')")
        )
        for table in policy.PRIVATE_TABLES:
            with pytest.raises(DBAPIError) as error:
                await conn.execute(text(f"SELECT * FROM {table} LIMIT 1"))
            assert getattr(error.value.orig, "sqlstate", None) == "42501"
            await conn.rollback()


async def test_refresh_records_unchanged_counts_and_deduplicates_period(
    database,
    collector,
    settings,
    api_mock,
):
    await seed(database, at=NOW - timedelta(days=2))
    mock_repo(api_mock)
    first = await invoke(collector, settings)
    assert first["status"] == "completed"
    assert first["report"]["observations_inserted"] == 1
    assert first["report"]["api_requests_consumed"] == {
        "total": 1,
        "repository_details": 1,
        "search": 0,
        "contributors": 0,
        "profiles": 0,
    }
    second = await invoke(collector, settings)
    assert second["status"] == "skipped" and len(api_mock.calls) == 1
    third = await invoke(collector, settings, at=NOW + timedelta(days=1))
    assert third["status"] == "completed" and third["report"]["observations_inserted"] == 1
    async with make_session_factory(database)() as session:
        assert list(await session.scalars(select(SnapshotRow.stars))) == [100, 100, 100]
        assert await session.scalar(select(func.count()).select_from(CollectionRunRow)) == 2


async def test_weekly_discovery_uses_reviewed_plan_and_zero_enrichment(
    collector,
    settings,
    api_mock,
):
    item = mock_repo(api_mock)
    mock_search(api_mock, [item])
    plan, _ = policy.discovery_plan(NOW)
    first = await invoke(collector, settings, "discover", plan=plan)
    assert first["status"] == "completed"
    report = first["report"]
    assert report["candidates_examined"] == 9
    assert report["new_repositories_saved"] == 1
    assert report["api_requests_consumed"] == {
        "total": 10,
        "search": 9,
        "repository_details": 1,
        "contributors": 0,
        "profiles": 0,
    }
    second = await invoke(collector, settings, "discover", plan=plan, at=NOW + timedelta(days=1))
    assert second["status"] == "skipped" and len(api_mock.calls) == 10


@pytest.mark.parametrize("status", [401, 429, 503])
async def test_remote_failure_is_partial_and_never_automatically_retried(
    database,
    collector,
    settings,
    api_mock,
    status,
):
    await seed(database, at=NOW - timedelta(days=2))
    settings.http_max_retries = 0
    api_mock.get(BASE_URL + "/repos/octo/one").mock(return_value=httpx.Response(status))
    result = await invoke(collector, settings)
    assert result["status"] == "partial"
    assert result["report"]["partial"] and result["report"]["observations_inserted"] == 0
    assert (await invoke(collector, settings))["status"] == "skipped"
    assert len(api_mock.calls) == 1


async def test_exhausted_budget_is_durable_without_http(database, collector, settings, api_mock):
    await seed(database, at=NOW - timedelta(days=2))
    settings.github_request_budget = policy.REFRESH_BUDGET.model_copy(update={"total": 0})
    result = await invoke(collector, settings)
    assert result["status"] == "partial" and result["report"]["budget_exhausted"]
    assert not api_mock.calls


async def test_parallel_schedules_obey_database_lock(database, collector, settings, api_mock):
    await seed(database, at=NOW - timedelta(days=2))
    payload = mock_repo(api_mock)
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json=payload)

    api_mock.get(BASE_URL + "/repos/octo/one").mock(side_effect=wait)
    rival = create_async_engine(
        collector.url,
        poolclass=NullPool,
        connect_args={"server_settings": {"role": policy.COLLECTOR_ROLE}},
    )
    task = asyncio.create_task(invoke(collector, settings))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        with pytest.raises(CollectionLockError):
            await invoke(rival, settings)
        assert len(api_mock.calls) == 0  # the single reserved transport is still in flight
    finally:
        release.set()
        await task
        await rival.dispose()
    assert (await invoke(collector, settings))["status"] == "skipped"
    assert len(api_mock.calls) == 1


async def test_cancelled_job_persists_partial(database, collector, settings, api_mock):
    await seed(database, at=NOW - timedelta(days=2))
    entered = asyncio.Event()

    async def wait(request):
        entered.set()
        await asyncio.Future()

    api_mock.get(BASE_URL + "/repos/octo/one").mock(side_effect=wait)
    task = asyncio.create_task(invoke(collector, settings))
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with make_session_factory(database)() as session:
        receipt = (await session.scalars(select(CollectionRunRow))).one()
        assert receipt.status == "partial" and stored_report(receipt).stop_reason == "interrupted"
    assert (await invoke(collector, settings))["status"] == "skipped"


async def test_deadline_is_partial(database, collector, settings, api_mock, monkeypatch):
    await seed(database, at=NOW - timedelta(days=2))

    async def wait(request):
        await asyncio.Future()

    api_mock.get(BASE_URL + "/repos/octo/one").mock(side_effect=wait)
    monkeypatch.setattr(
        policy, "REFRESH_LIMITS", policy.REFRESH_LIMITS.model_copy(update={"max_seconds": 1})
    )
    result = await invoke(collector, settings)
    assert result["status"] == "partial" and result["report"]["budget_exhausted"]


async def test_hard_crash_blocks_until_explicit_ack_without_changing_receipt(
    database,
    collector,
    settings,
    api_mock,
    monkeypatch,
):
    class Crash(BaseException):
        pass

    await seed(database, at=NOW - timedelta(days=2))
    mock_repo(api_mock)
    checkpoint = CollectionService._checkpoint

    async def crash(*args, **kwargs):
        raise Crash()

    monkeypatch.setattr(CollectionService, "_checkpoint", crash)
    with pytest.raises(Crash):
        await invoke(collector, settings)
    monkeypatch.setattr(CollectionService, "_checkpoint", checkpoint)
    async with make_session_factory(database)() as session:
        row = (await session.scalars(select(CollectionRunRow))).one()
        run_id = row.id
        assert row.status == "running" and stored_report(row).partial
        saved = (row.report_json, row.requests_json, row.finished_at)
    with pytest.raises(policy.AutomationBlocked, match="Unfinished"):
        await invoke(collector, settings, at=NOW + timedelta(days=1))
    result = await invoke(
        collector, settings, at=NOW + timedelta(days=1), acknowledged=frozenset({run_id})
    )
    assert result["status"] == "completed"
    assert result["acknowledged_unfinished_runs"] == [run_id]
    async with make_session_factory(database)() as session:
        preserved = await session.get(CollectionRunRow, run_id)
        assert preserved is not None
        assert (preserved.report_json, preserved.requests_json, preserved.finished_at) == saved


@pytest.mark.parametrize("loss", ["terminate", "unlock"])
async def test_disconnect_or_unlock_cannot_finish_or_reconnect(
    database,
    collector,
    settings,
    api_mock,
    loss,
):
    await seed(database, at=NOW - timedelta(days=2))
    payload = mock_repo(api_mock)

    async def lose_owner(request):
        async with database.begin() as conn:
            if loss == "terminate":
                pid = await conn.scalar(
                    text(
                        "SELECT pid FROM pg_locks WHERE "
                        "locktype='advisory' AND objid=6742565 AND granted"
                    )
                )
                assert pid
                await conn.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        return httpx.Response(200, json=payload)

    if loss == "unlock":
        original = CollectionService._reserve_attempt

        async def unlock(self, used):
            await original(self, used)
            await self.session.execute(text("SELECT pg_advisory_unlock(6742565)"))

        # The fixture restores this below so subsequent tests retain production ownership checks.
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(CollectionService, "_reserve_attempt", unlock)
            with pytest.raises(CollectionLockError):
                await invoke(collector, settings)
    else:
        api_mock.get(BASE_URL + "/repos/octo/one").mock(side_effect=lose_owner)
        with pytest.raises(CollectionLockError):
            await invoke(collector, settings)
    async with make_session_factory(database)() as session:
        row = (await session.scalars(select(CollectionRunRow))).one()
        assert row.status == "running" and row.finished_at is None
        assert await session.scalar(select(func.count()).select_from(SnapshotRow)) == 1


@pytest.mark.parametrize(
    "grant",
    [
        "SELECT ON workspace_repositories",
        "SELECT (notes) ON workspace_repositories",
        "INSERT ON workspace_developers",
        "SELECT ON ai_artifacts",
    ],
)
async def test_private_grants_fail_before_http(database, collector, settings, api_mock, grant):
    async with database.begin() as conn:
        await conn.execute(text(f"GRANT {grant} TO github_radar_collector"))
    with pytest.raises(policy.AutomationBlocked, match="private"):
        await invoke(collector, settings)
    assert not api_mock.calls


async def test_admin_and_missing_sequence_grants_refused(database, collector, settings, api_mock):
    with pytest.raises(policy.AutomationBlocked, match="non-administrative"):
        await invoke(database, settings)
    async with database.begin() as conn:
        await conn.execute(
            text("REVOKE USAGE ON ALL SEQUENCES IN SCHEMA public FROM github_radar_collector")
        )
    with pytest.raises(policy.AutomationBlocked, match="sequence"):
        await invoke(collector, settings)
    assert not api_mock.calls


async def test_operator_report_uses_read_only_snapshot_and_exposes_real_gaps(
    database,
    collector,
    settings,
    api_mock,
):
    await seed(database, at=NOW - timedelta(days=3))
    mock_repo(api_mock)
    result = await invoke(collector, settings)
    async with collector.connect() as conn, conn.begin():
        await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        async with AsyncSession(bind=conn) as session:
            report = await policy.operator_report(session, NOW)
            assert await session.scalar(text("SHOW transaction_read_only")) == "on"
    assert report["last_successful_refresh"] is not None
    assert report["repositories_due"] == 0
    assert report["last_discovery"] is None
    gaps = report["missing_observation_days_last_7_complete_utc_days"][0]["missing_utc_days"]
    assert gaps == [(NOW.date() - timedelta(days=i)).isoformat() for i in (2, 1)]
    assert report["recent_runs"][0]["run_id"] == result["report"]["run_id"]
    async with locked_collection_session(collector) as session:
        await policy.check_collector_role(session)
