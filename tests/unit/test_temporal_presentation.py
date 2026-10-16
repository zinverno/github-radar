"""Observation coverage survives public read-only CLI and API presentation."""

import importlib
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from github_radar.api.app import create_app
from github_radar.storage import repositories as storage
from github_radar.storage.db import make_session_factory
from tests.unit.test_collection import NOW, seed
from tests.unit.test_collection import database as database


@pytest.mark.parametrize("days", [None, 2, 7])
@pytest.mark.parametrize("stars", [100, 120])
async def test_cli_api_preserve_missing_zero_and_partial_history(
    database, settings, monkeypatch, capsys, days, stars
):
    rid = await seed(database, at=NOW - timedelta(days=days or 0))
    factory = make_session_factory(database)
    if days is not None:
        async with factory() as session:
            base = await storage.latest_snapshot(session, rid)
            assert base is not None
            await storage.record_observation(
                session, rid, replace(base.to_domain(), captured_at=NOW, stars=stars)
            )
            await session.commit()
    app = create_app(
        settings=settings, engine=database, session_factory=factory,
        web_dist=Path("/nonexistent/dist"),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/repositories/octo/one")
        assert response.status_code == 200, response.text
        detail = response.json()
        listing = (await client.get("/api/v1/repositories")).json()["items"][0]
    trend = detail["trend"]
    assert trend == listing["trend"]
    assert trend["window_days"] == 7
    if days is None:
        assert trend["window"] is None and trend["growth_pct"] is None
        assert detail["metrics"]["stars_7d"] is None and detail["stars_7d"] is None
    else:
        assert trend["window"] == detail["metrics"]["stars_7d"]["window"]
        assert trend["window"]["span_days"] == days
        assert trend["window"]["complete"] is (days == 7)
        assert trend["growth_pct"] == stars - 100
        assert detail["stars_7d"] == stars - 100
        assert f"over {days} observed days" in trend["explanation"]

    cli = importlib.import_module("github_radar.cli.app")
    monkeypatch.setattr(cli, "_engine_and_session", AsyncMock(
        side_effect=lambda _: (SimpleNamespace(dispose=AsyncMock()), factory())
    ))
    await cli._repo_run(settings, owner="octo", name="one")
    output = capsys.readouterr().out
    assert "Observed days" in output
    assert ("unavailable" if days is None else "partial") in output  # 30d stays partial.
    await cli._trending_run(settings, limit=10)
    output = capsys.readouterr().out
    assert "Observed days (7d requested)" in output
    if days is not None:
        assert ("complete" if days == 7 else "partial") in output
