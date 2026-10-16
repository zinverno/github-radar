"""Read-only diagnostics against the guarded PostgreSQL test database only."""

import asyncio
import importlib
import json
import os
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.engine import make_url
from typer.testing import CliRunner

from github_radar.api.deps import reference_now
from github_radar.discovery.planning import load_discovery_config
from github_radar.services.intelligence import DeveloperIntelligenceService
from github_radar.services.quality import load_quality_report
from github_radar.storage import repositories as storage
from github_radar.storage.db import make_session_factory
from github_radar.storage.models import (
    CollectionRunRow,
    ContributorRow,
    ContributorSnapshotRow,
    DeveloperRow,
    DeveloperSnapshotRow,
    SnapshotRow,
)
from tests.conftest import BASE_URL
from tests.integration.safety import validate_test_database_url
from tests.integration.test_bounded_collection import database as database
from tests.unit.test_collection import mock_repo, plan, run, seed
from tests.unit.test_collection_quality import NOW
from tests.unit.test_discovery_planning import SAMPLE

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL unset"),
]


@pytest.fixture(autouse=True)
def exact_test_database():
    url = os.environ.get("TEST_DATABASE_URL", "")
    validate_test_database_url(test_url=url, app_url=os.environ.get("DATABASE_URL"))
    assert make_url(url).database == "github_radar_test"


@pytest.mark.parametrize("command", ["quality", "stats"])
@pytest.mark.parametrize("json_output", [True, False])
async def test_public_diagnostics_are_read_only_and_never_fetch_github(
    database, settings, api_mock, monkeypatch, command, json_output
):
    cli = importlib.import_module("github_radar.cli.app")
    diagnostics = importlib.import_module("github_radar.cli.collection")
    items = [mock_repo(api_mock, i, f"repo-{i}") for i in range(1, 6)]
    api_mock.get(BASE_URL + "/search/repositories").mock(
        side_effect=[
            httpx.Response(200, json={"items": items[:3]}),
            httpx.Response(200, json={"items": [items[0], *items[3:]]}),
        ]
    )
    report = await run(database, settings, plan=plan(count=3, cohorts=2))
    assert report.new_repositories_saved == 5
    prior_calls = len(api_mock.calls)
    settings.database_url = os.environ["TEST_DATABASE_URL"]
    settings.github_token = ""
    monkeypatch.setattr(diagnostics, "get_settings", lambda: settings)
    engines = []
    statements = []
    make_engine = diagnostics.make_engine

    def observe_engine(url):
        engine = make_engine(url)
        engines.append(engine)

        def observe(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", observe)
        return engine

    monkeypatch.setattr(diagnostics, "make_engine", observe_engine)
    args = ["collection", command]
    if command == "quality":
        args += ["--as-of", NOW.isoformat()]
    if json_output:
        args += ["--json"]
    result = await asyncio.to_thread(CliRunner().invoke, cli.app, args)
    assert result.exit_code == 0, result.output
    assert len(api_mock.calls) == prior_calls
    assert statements[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    assert all(s.lstrip().upper().startswith(("SELECT", "SET TRANSACTION")) for s in statements)
    if json_output:
        data = json.loads(result.output)
        costs = data["collection"] if command == "quality" else data
        assert costs["discovery"]["api_attempts_per_new_repository"] == 1.4
        if command == "quality":
            assert data["coverage"]["snapshot_counts"]["1"] == 5
            assert data["coverage"]["curated_categories"]["membership_count"] == 6
            assert data["developers"]["total_developers"] == 0
    else:
        assert "not statistically reliable" in result.output
    async with make_session_factory(database)() as session:
        assert len(list(await session.scalars(select(SnapshotRow)))) == 5
        receipt = await session.get(CollectionRunRow, report.run_id)
        assert receipt is not None
        assert receipt.status == "completed"


async def test_quality_load_is_repeatable_in_read_only_postgres_transaction(database):
    async with database.connect() as conn, conn.begin():
        await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        async with make_session_factory(database)(bind=conn) as session:
            assert await session.scalar(text("SHOW transaction_read_only")) == "on"
            first = await load_quality_report(
                session, catalog=load_discovery_config(SAMPLE), assessed_at=NOW
            )
            second = await load_quality_report(
                session, catalog=load_discovery_config(SAMPLE), assessed_at=NOW
            )
            assert first == second
            assert first["coverage"]["total_repositories"] == 0
            assert first["collection"]["discovery"]["api_attempts_per_new_repository"] is None


@pytest.mark.parametrize("newest_source", ["repository", "profile", "contributor"])
async def test_developer_reference_uses_newest_observation_across_sources(database, newest_source):
    rid = await seed(database, at=NOW)
    async with make_session_factory(database)() as session:
        dev = DeveloperRow(
            github_id=42,
            login="fixture",
            followers=100000,
            first_seen_at=NOW - timedelta(days=30),
            last_seen_at=NOW,
        )
        session.add(dev)
        await session.flush()
        session.add(
            ContributorRow(
                repository_id=rid,
                developer_id=dev.id,
                contributions=10000,
                first_seen_at=NOW - timedelta(days=15),
                last_seen_at=NOW,
            )
        )
        for days in (15, 8):
            at = NOW - timedelta(days=days)
            session.add(
                ContributorSnapshotRow(
                    repository_id=rid, developer_id=dev.id, captured_at=at, contributions=10000
                )
            )
            session.add(DeveloperSnapshotRow(developer_id=dev.id, captured_at=at, followers=100000))
        expected_anchor = NOW
        if newest_source != "repository":
            expected_anchor = NOW + timedelta(days=2)
            if newest_source == "profile":
                session.add(DeveloperSnapshotRow(
                    developer_id=dev.id, captured_at=expected_anchor, followers=120000
                ))
            else:
                session.add(ContributorSnapshotRow(
                    repository_id=rid, developer_id=dev.id,
                    captured_at=expected_anchor, contributions=10000
                ))
        developer_id = dev.id
        await session.commit()
    async with make_session_factory(database)() as session:
        assert await storage.newest_observation_at(session) == expected_anchor
        assert await reference_now(session) == expected_anchor
        service = DeveloperIntelligenceService(session)
        dataset = await service.load_dataset()
        assert dataset.reference_now == expected_anchor
        developer = service.developer_report(dataset, developer_id)
        assert developer is not None and developer.activity is not None
        report = await load_quality_report(
            session, catalog=load_discovery_config(SAMPLE), assessed_at=expected_anchor
        )
        assert report["developer_reference_at"] == expected_anchor.isoformat()
        assert not report["developer_anchor_precedes_repository_observation"]
        dev_report = report["developers"]["developers"][0]
        available = newest_source == "contributor"
        assert dev_report["observed_positive_delta_7d"] == (0 if available else None)
        assert dev_report["activity_available"] is available
        assert developer.activity.available is available
        assert developer.activity.confidence_score == dev_report["activity"]["confidence_score"]
        assert developer.emerging.confidence_score == dev_report["emerging"]["confidence_score"]
        assert developer.emerging.label == dev_report["emerging"]["label"]
        assert report["developers"]["with_complete_contributor_history"]["7"] == int(available)
        assert developer.profile is not None
        followers = developer.profile.followers_delta(7)
        assert followers is not None
        assert followers.delta == (20000 if newest_source == "profile" else None)
        # Explicit observation anchors remain deterministic, including the old zero-growth window.
        prior = await service.load_dataset(reference_now=NOW - timedelta(days=8))
        prior_report = service.developer_report(prior, developer_id)
        assert prior_report is not None and prior_report.activity is not None
        assert prior_report.activity.available and prior_report.activity.total_positive_delta == 0
        assert await service.load_dataset() == dataset
        json.dumps(report, allow_nan=False)


async def test_reference_with_only_profile_history_and_without_observations(database):
    async with make_session_factory(database)() as session:
        assert await storage.newest_observation_at(session) is None
        dev = DeveloperRow(github_id=42, login="profile-only", first_seen_at=NOW, last_seen_at=NOW)
        session.add(dev)
        await session.flush()
        session.add(DeveloperSnapshotRow(developer_id=dev.id, captured_at=NOW, followers=10))
        await session.commit()
        assert await storage.newest_observation_at(session) == NOW
        dataset = await DeveloperIntelligenceService(session).load_dataset()
        assert dataset.reference_now == NOW
        assert not dataset.links  # A profile observation does not fabricate contributor history.
        quality = await load_quality_report(
            session, catalog=load_discovery_config(SAMPLE), assessed_at=NOW
        )
        profile_only = quality["developers"]["developers"][0]
        assert profile_only["activity"] is None
        assert profile_only["observed_positive_delta_7d"] is None
        assert profile_only["emerging"]["label"] == "INSUFFICIENT_HISTORY"
