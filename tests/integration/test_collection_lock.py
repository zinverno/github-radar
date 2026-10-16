"""Real PostgreSQL ownership loss with fake HTTP and deterministic barriers."""

from __future__ import annotations

import asyncio
import importlib
import json
import os

import httpx
import pytest
import typer
from sqlalchemy import event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection

from github_radar.services.collection import CollectionService, stored_report
from github_radar.storage.db import make_session_factory
from github_radar.storage.models import CollectionRunRow, RepositoryRow, SnapshotRow
from tests.conftest import BASE_URL
from tests.integration.safety import validate_test_database_url
from tests.integration.test_bounded_collection import database as database
from tests.unit.test_collection import mock_repo, mock_search, plan

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL unset"),
]


@pytest.fixture(autouse=True)
def exact_test_database():
    url = os.environ.get("TEST_DATABASE_URL", "")
    validate_test_database_url(test_url=url, app_url=os.environ.get("DATABASE_URL"))
    assert make_url(url).database == "github_radar_test"


@pytest.mark.parametrize("loss", ["terminate", "invalidate", "unlock"])
@pytest.mark.parametrize("pause_at", ["http", "terminal"])
async def test_lost_owner_cannot_write_or_complete(
    database, settings, api_mock, monkeypatch, capsys, loss, pause_at
):
    cli = importlib.import_module("github_radar.cli.app")
    settings.database_url = os.environ["TEST_DATABASE_URL"]
    one = mock_repo(api_mock)
    two = mock_repo(api_mock, 2, "two")
    three = mock_repo(api_mock, 3, "three")
    search_calls = 0
    ready_a, resume_a = asyncio.Event(), asyncio.Event()
    ready_b, resume_b = asyncio.Event(), asyncio.Event()
    services = []
    connections_after_loss = []

    def capture_service(*args, **kwargs):
        service = CollectionService(*args, **kwargs)
        services.append(service)
        return service

    monkeypatch.setattr(cli, "CollectionService", capture_service)

    async def search(request):
        nonlocal search_calls
        search_calls += 1
        items = ([one, two] if pause_at == "http" else [one]) if search_calls == 1 else [three]
        return httpx.Response(200, json={"items": items})

    async def paused_a(request):
        ready_a.set()
        await resume_a.wait()
        return httpx.Response(200, json=two)

    async def paused_b(request):
        ready_b.set()
        await resume_b.wait()
        return httpx.Response(200, json=three)

    checkpoint = CollectionService._checkpoint

    async def pause_terminal(self, *, finished=False):
        if finished and self is services[0] and pause_at == "terminal":
            ready_a.set()
            await resume_a.wait()
        await checkpoint(self, finished=finished)

    monkeypatch.setattr(CollectionService, "_checkpoint", pause_terminal)
    api_mock.get(BASE_URL + "/search/repositories").mock(side_effect=search)
    api_mock.get(BASE_URL + "/repos/octo/two").mock(side_effect=paused_a)
    api_mock.get(BASE_URL + "/repos/octo/three").mock(side_effect=paused_b)
    task_a = asyncio.create_task(cli._collection_run(settings, "discover", plan=plan()))
    tasks = [task_a]
    try:
        await asyncio.wait_for(ready_a.wait(), timeout=10)
        run_a = services[0].report.run_id
        async with make_session_factory(database)() as session:
            assert list(await session.scalars(select(RepositoryRow.github_id))) == [1]
            row = await session.get(CollectionRunRow, run_a)
            assert row is not None and row.status == "running"
        async with database.connect() as control:
            holder = await control.execute(text("""
                SELECT a.pid, a.state FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid
                WHERE l.locktype='advisory' AND l.classid=0 AND l.objid=6742565
                  AND l.granted AND a.datname=current_database() AND a.usename=current_user
            """))
            pid, state = holder.one()
            if loss == "terminate":
                assert await control.scalar(
                    text("SELECT pg_terminate_backend(:pid, 5000)"), {"pid": pid}
                )
            else:
                pinned = services[0].session.bind
                assert isinstance(pinned, AsyncConnection)
                if loss == "invalidate":
                    event.listen(
                        pinned.engine.sync_engine, "connect",
                        lambda *args: connections_after_loss.append(True),
                    )
                    await pinned.invalidate()
                    with pytest.raises(RuntimeError, match="ownership"):
                        await pinned.execute(text("SELECT 1"))
                else:
                    assert await pinned.scalar(text("SELECT pg_advisory_unlock(6742565)"))
                    await pinned.commit()
        task_b = asyncio.create_task(cli._collection_run(settings, "discover", plan=plan(count=1)))
        tasks.append(task_b)
        await asyncio.wait_for(ready_b.wait(), timeout=10)
        resume_a.set()
        outcome = (await asyncio.wait_for(
            asyncio.gather(task_a, return_exceptions=True), timeout=10
        ))[0]
        async with make_session_factory(database)() as session:
            assert list(await session.scalars(select(RepositoryRow.github_id))) == [1]
            row = await session.get(CollectionRunRow, run_a)
            assert row is not None and row.status == "running"
            assert stored_report(row).partial
        assert isinstance(outcome, typer.Exit) and outcome.exit_code == 1
        output = capsys.readouterr()
        assert '"status": "completed"' not in output.out
        assert "ownership" in output.err and run_a in output.err
        assert state == "idle", "HTTP awaits must not hold an open database transaction"
        assert connections_after_loss == []
        resume_b.set()
        await asyncio.wait_for(task_b, timeout=10)
        report_b = json.loads(capsys.readouterr().out)
        assert report_b["status"] == "completed" and report_b["new_repositories_saved"] == 1
        async with make_session_factory(database)() as session:
            assert set(await session.scalars(select(RepositoryRow.github_id))) == {1, 3}
            assert len(list(await session.scalars(select(SnapshotRow)))) == 2
            row_a = await session.get(CollectionRunRow, run_a)
            row_b = await session.get(CollectionRunRow, report_b["run_id"])
            assert row_a is not None and row_a.status == "running"
            assert row_b is not None and row_b.status == "completed"
    finally:
        resume_a.set()
        resume_b.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("outcome", ["completed", "auth", "cancelled", "cancelled-terminal"])
async def test_collection_releases_ownership(
    database, settings, api_mock, capsys, monkeypatch, outcome
):
    cli = importlib.import_module("github_radar.cli.app")
    settings.database_url = os.environ["TEST_DATABASE_URL"]
    item = mock_repo(api_mock)
    route = mock_search(api_mock, [item])
    if outcome == "auth":
        route.mock(return_value=httpx.Response(401))
    elif outcome == "cancelled":
        async def cancel(request):
            raise asyncio.CancelledError
        route.mock(side_effect=cancel)
    elif outcome == "cancelled-terminal":
        checkpoint = CollectionService._checkpoint

        async def cancel_terminal(self, *, finished=False):
            if finished:
                raise asyncio.CancelledError
            await checkpoint(self, finished=finished)

        monkeypatch.setattr(CollectionService, "_checkpoint", cancel_terminal)
    try:
        await cli._collection_run(settings, "discover", plan=plan(count=1))
        assert outcome == "completed"
    except typer.Exit as exc:
        assert outcome == "auth" and exc.exit_code == 1
    except asyncio.CancelledError:
        assert outcome.startswith("cancelled")
    report = json.loads(capsys.readouterr().out)
    assert report["partial"] == (outcome != "completed")
    async with database.connect() as check:
        assert await check.scalar(text("SELECT pg_try_advisory_lock(6742565)"))
        assert await check.scalar(text("SELECT pg_advisory_unlock(6742565)"))
