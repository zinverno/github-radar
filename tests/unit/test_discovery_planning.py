"""Offline cohort planning and CLI side-effect boundaries."""

from __future__ import annotations

import importlib
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from github_radar.config import Settings
from github_radar.config.settings import GitHubRequestLimits
from github_radar.discovery.planning import (
    DiscoveryConfig,
    PlannedCohort,
    load_discovery_config,
    make_plan,
    plan_discovery,
)
from github_radar.github.budget import RequestBudget, RequestBudgetExceeded

SAMPLE = Path(__file__).resolve().parents[2] / "config/discovery.toml"
AS_OF = date(2026, 10, 16)


def test_default_cohorts_are_diversified_and_reproducible() -> None:
    config = load_discovery_config(SAMPLE)
    plan = plan_discovery(config, as_of=AS_OF)
    assert plan == plan_discovery(config, as_of=AS_OF)
    assert len(plan.cohorts) == 9
    assert plan.candidate_slots == 27
    assert plan.search_requests_without_retries == 9
    assert {c.limit for c in plan.cohorts} == {3}
    assert [c.category for c in plan.cohorts[:3]] == [c.name for c in config.categories]
    assert "stars:>=1000" in plan.cohorts[0].query
    assert "created:>=2025-10-16" in plan.cohorts[3].query
    assert "stars:10..999" in plan.cohorts[3].query
    assert "pushed:>=2026-09-16" in plan.cohorts[6].query
    assert all("is:public archived:false fork:false" in c.query for c in plan.cohorts)
    assert plan.all_new_enrichment_requests_without_retries == {
        "repository_details": 27,
        "contributors": 27,
        "profiles": 810,
    }
    assert any("profiles: all-new demand 810 exceeds cap 30" in w for w in plan.warnings)


def test_selection_and_unknown_names() -> None:
    config = load_discovery_config(SAMPLE)
    plan = plan_discovery(
        config,
        as_of=AS_OF,
        categories=("ai-agents",),
        recipes=("recent",),
    )
    assert len(plan.cohorts) == 1
    assert plan.cohorts[0].recipe == "recent"
    with pytest.raises(ValueError, match="Unknown selection"):
        plan_discovery(config, as_of=AS_OF, categories=("typo",))
    with pytest.raises(ValueError, match="Unknown selection"):
        plan_discovery(config, as_of=AS_OF, recipes=("typo",))


@pytest.mark.parametrize(
    "change",
    [
        {"categories": []},
        {"recipes": []},
        {"limit_per_cohort": 0},
        {"limit_per_cohort": 1001},
        {"per_page": 101},
        {"contributors_limit": -1},
        {"limit_per_cohort": True},
        {"per_page": 1.5},
        {"unknown": 1},
        {"categories": [{"name": "ai", "query": "   "}]},
        {"categories": [{"name": "ai", "query": "x"}] * 2},
        {"recipes": [{"name": "same"}] * 2},
        {"recipes": [{"name": "bad", "min_stars": 100, "max_stars": 10}]},
        {"recipes": [{"name": "bad", "sort": "created"}]},
        {"recipes": [{"name": "bad", "created_within_days": 0}]},
        {"budget": {"total": -1}},
        {"budget": {"search": True}},
        {"budget": {"profile": 0}},
    ],
)
def test_invalid_config_rejected(change: dict[str, object]) -> None:
    raw = load_discovery_config(SAMPLE).model_dump()
    with pytest.raises(ValidationError):
        DiscoveryConfig.model_validate({**raw, **change})


def test_pagination_costs_and_zero_enrichment() -> None:
    cohort = PlannedCohort(
        category="test",
        recipe="test",
        query="mcp",
        sort="stars",
        order="desc",
        limit=101,
    )
    plan = make_plan(
        (cohort,),
        as_of=AS_OF,
        per_page=100,
        contributors_limit=101,
        budget=GitHubRequestLimits(total=2, search=2, profiles=0),
    )
    assert plan.search_requests_without_retries == 2
    assert plan.all_new_enrichment_requests_without_retries["contributors"] == 202
    assert any("full enrichment is not budgeted" in w for w in plan.warnings)
    for limits in (GitHubRequestLimits(search=1), GitHubRequestLimits(total=1)):
        with pytest.raises(ValueError, match="Plan needs 2 search requests"):
            make_plan((cohort,), as_of=AS_OF, per_page=100, contributors_limit=0, budget=limits)
    plan = make_plan(
        (cohort,),
        as_of=AS_OF,
        per_page=100,
        contributors_limit=0,
        budget=GitHubRequestLimits(),
    )
    assert plan.all_new_enrichment_requests_without_retries["contributors"] == 0
    assert plan.all_new_enrichment_requests_without_retries["profiles"] == 0


def test_duplicate_effective_queries_rejected() -> None:
    raw = load_discovery_config(SAMPLE).model_dump()
    raw["categories"] = [{"name": "a", "query": "x"}, {"name": "b", "query": "x"}]
    with pytest.raises(ValueError, match="Duplicate effective"):
        plan_discovery(DiscoveryConfig.model_validate(raw), as_of=AS_OF)


@pytest.fixture
def offline_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    cli = importlib.import_module("github_radar.cli.app")
    monkeypatch.chdir(tmp_path)
    settings = Settings(github_token="", database_url="")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    def forbidden(*args, **kwargs):
        pytest.fail("Local planning must not create an HTTP client or a database session")

    monkeypatch.setattr(cli, "_make_client", forbidden)
    monkeypatch.setattr(cli, "_engine_and_session", forbidden)
    monkeypatch.setattr(cli, "make_engine", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", forbidden)
    return cli


def test_plan_cli_is_local_only(offline_cli) -> None:
    result = CliRunner().invoke(
        offline_cli.app,
        [
            "discover-plan",
            "--config",
            str(SAMPLE),
            "--as-of",
            AS_OF.isoformat(),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "local-only"
    assert payload["candidate_slots"] == 27


def test_discover_dry_run_needs_no_credentials_or_io(offline_cli) -> None:
    result = CliRunner().invoke(
        offline_cli.app,
        [
            "discover",
            "mcp",
            "--language",
            "python",
            "--topic",
            "ai",
            "--created-after",
            "2026-01-01",
            "--min-stars",
            "10",
            "--contributors-limit",
            "0",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["cohorts"][0]["query"] == (
        "mcp language:python topic:ai stars:>=10 created:>=2026-01-01"
    )
    assert payload["all_new_enrichment_requests_without_retries"]["profiles"] == 0


@pytest.mark.parametrize(
    "args",
    [
        ["discover-plan", "--category", "missing"],
        ["discover-plan", "--as-of", "not-a-date"],
        ["discover-plan", "--as-of", "0001-01-01"],
        ["discover-plan", "--config", "/no/such/discovery.toml"],
        ["discover", "x", "--dry-run", "--sort", "bad"],
        ["discover", "x", "--dry-run", "--contributors-limit", "-1"],
    ],
)
def test_cli_invalid_input_is_local_and_actionable(offline_cli, args: list[str]) -> None:
    result = CliRunner().invoke(offline_cli.app, args)
    assert result.exit_code == 2, result.output
    assert "Invalid value" in result.output


def test_malformed_toml_rejected(offline_cli, tmp_path: Path) -> None:
    config = tmp_path / "invalid.toml"
    config.write_text("[budget", encoding="utf-8")
    result = CliRunner().invoke(offline_cli.app, ["discover-plan", "--config", str(config)])
    assert result.exit_code == 2


def test_live_discover_reports_budget_stop_without_traceback(offline_cli, monkeypatch) -> None:
    async def stopped(**kwargs):
        assert kwargs["contributors_limit"] == 0
        budget = RequestBudget(GitHubRequestLimits(total=0))
        budget.consume("/search/repositories")

    monkeypatch.setattr(offline_cli, "_discover_run", stopped)
    result = CliRunner().invoke(offline_cli.app, ["discover", "x", "--contributors-limit", "0"])
    assert result.exit_code == 1
    assert "request budget exhausted" in result.output
    assert "Enrichment may be incomplete" in result.output
    assert not isinstance(result.exception, RequestBudgetExceeded)
