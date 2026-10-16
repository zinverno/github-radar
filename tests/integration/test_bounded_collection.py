"""Run the same fake-GitHub recovery scenarios against the guarded test database."""

from __future__ import annotations

import asyncio
import importlib
import os

import httpx
import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from github_radar.storage.db import make_engine
from github_radar.storage.models import Base
from tests.conftest import BASE_URL, contributors_payload, user_payload
from tests.integration.safety import validate_test_database_url
from tests.integration.test_postgres_flow import _drop_test_schema, _run_alembic
from tests.unit.test_collection import (  # noqa: F401
    mock_repo,
    mock_search,
    plan,
    test_budget_exhaustion_during_pagination_preserves_first_commit,
    test_cross_recipe_deduplication_and_restart,
    test_empty_contributors_checkpoint_and_expiry,
    test_enrichment_budget_and_ttl_restart,
    test_execution_limits_are_partial,
    test_hard_shutdown_leaves_honest_unfinished_receipt,
    test_identity_mismatch_rolls_back,
    test_incomplete_search_is_not_success,
    test_interruption_and_restart,
    test_never_observed_first_and_stable_refresh_tie_break,
    test_retry_costs_are_durable,
    test_runtime_deadline_stops_http,
    test_same_instant_enrichment_counts_only_new_observations,
    test_same_instant_refresh_and_old_history_are_preserved,
    test_stable_identity_rename_and_name_collision,
    test_stale_first_uses_observations_and_minimum_interval,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("TEST_DATABASE_URL") is None, reason="TEST_DATABASE_URL unset"
    ),
]


@pytest.fixture
async def database():
    test_url = os.environ.get("TEST_DATABASE_URL", "")
    validate_test_database_url(test_url=test_url, app_url=os.environ.get("DATABASE_URL"))
    engine = make_engine(test_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        yield engine
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def test_live_cli_uses_test_database_and_fake_github(
    database, settings, api_mock, monkeypatch, tmp_path
):
    cli = importlib.import_module("github_radar.cli.app")
    settings.database_url = os.environ["TEST_DATABASE_URL"]
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    item = mock_repo(api_mock)
    mock_search(api_mock, [item])
    reviewed = tmp_path / "plan.json"
    reviewed.write_text(plan(count=1).model_dump_json(), encoding="utf-8")
    result = await asyncio.to_thread(
        CliRunner().invoke, cli.app, ["discover-execute", str(reviewed)]
    )
    assert result.exit_code == 0, result.output
    assert '"new_repositories_saved": 1' in result.output
    api_mock.get(f"{BASE_URL}/repos/octo/one/contributors").mock(
        return_value=httpx.Response(200, json=contributors_payload([("alice", 42, 10)]))
    )
    api_mock.get(f"{BASE_URL}/users/alice").mock(
        return_value=httpx.Response(200, json=user_payload(login="alice", github_id=42))
    )
    enriched = await asyncio.to_thread(
        CliRunner().invoke, cli.app,
        ["enrich", "octo/one", "--max-profiles", "1", "--contributors-limit", "1"],
    )
    assert enriched.exit_code == 0, enriched.output
    assert '"contributor_profiles_fetched": 1' in enriched.output
    for command in (
        ["repos"], ["repo", "octo/one"], ["stats"], ["trending"], ["topics"],
        ["topic", "mcp"], ["developers"], ["developer", "alice"],
        ["emerging-developers"], ["ecosystem"], ["bridges", "develop"],
        ["bridges", "repos"], ["bridges", "cross-topic", "mcp", "python"],
        ["related-topics", "mcp"], ["related-repos", "octo/one"], ["ai", "status"],
    ):
        read = await asyncio.to_thread(CliRunner().invoke, cli.app, command)
        assert read.exit_code == 0, (command, read.output)
    summary = await asyncio.to_thread(CliRunner().invoke, cli.app, ["collection-runs"])
    assert summary.exit_code == 0
    assert '"status": "completed"' in summary.output
    settings.github_request_budget = settings.github_request_budget.model_copy(update={"total": 0})
    result = await asyncio.to_thread(
        CliRunner().invoke, cli.app, ["refresh", "--min-refresh-hours", "0"]
    )
    assert result.exit_code == 1, result.output
    assert '"budget_exhausted": true' in result.output
    async with database.connect() as lock:
        await lock.execute(text("SELECT pg_advisory_lock(6742565)"))
        try:
            refused = await asyncio.to_thread(
                CliRunner().invoke, cli.app, ["discover-execute", str(reviewed)]
            )
            assert refused.exit_code == 1
            assert "Another collection process" in refused.output
        finally:
            await lock.execute(text("SELECT pg_advisory_unlock(6742565)"))


def test_0005_upgrade_preserves_existing_observations():
    test_url = os.environ["TEST_DATABASE_URL"]
    validate_test_database_url(test_url=test_url, app_url=os.environ.get("DATABASE_URL"))
    _drop_test_schema()
    result = _run_alembic("upgrade", "0004")
    assert result.returncode == 0, result.stderr

    async def seed_legacy():
        engine = make_engine(test_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text("""
                    INSERT INTO repositories
                    (github_id, owner_login, name, full_name, is_fork, is_archived, is_disabled,
                     first_seen_at, last_seen_at)
                    VALUES (42, 'octo', 'legacy', 'octo/legacy', false, false, false,
                            '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z')
                """)
                )
                await connection.execute(
                    text("""
                    INSERT INTO repository_snapshots (repository_id, captured_at, stars, forks)
                    SELECT id, '2026-10-01T00:00:00Z', 100, 2 FROM repositories WHERE github_id = 42
                """)
                )
        finally:
            await engine.dispose()

    async def check_preserved():
        engine = make_engine(test_url)
        try:
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(text("SELECT stars FROM repository_snapshots"))
                ) == 100
                assert (await connection.scalar(text("SELECT count(*) FROM repositories"))) == 1
                assert (await connection.scalar(text("SELECT count(*) FROM collection_runs"))) == 0
        finally:
            await engine.dispose()

    try:
        asyncio.run(seed_legacy())
        result = _run_alembic("upgrade", "head")
        assert result.returncode == 0, result.stderr
        asyncio.run(check_preserved())
        result = _run_alembic("downgrade", "0004")
        assert result.returncode == 0, result.stderr
        result = _run_alembic("upgrade", "head")
        assert result.returncode == 0, result.stderr
        asyncio.run(check_preserved())
    finally:
        _drop_test_schema()
