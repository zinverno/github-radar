"""Validation and command registration without credentials or network access."""

import importlib

import pytest
from sqlalchemy.exc import OperationalError
from typer.testing import CliRunner

from github_radar.cli.app import app
from github_radar.discovery.planning import load_discovery_config, plan_discovery
from tests.unit.test_discovery_planning import AS_OF, SAMPLE


@pytest.mark.parametrize(
    "command", [["collection"], ["collection", "stats"], ["collection", "quality"]]
)
def test_collection_diagnostic_help(command):
    assert CliRunner().invoke(app, [*command, "--help"]).exit_code == 0


@pytest.mark.parametrize(
    "args",
    [
        ["--as-of", "invalid"],
        ["--as-of", "2026-10-16"],
        ["--top", "0"],
        ["--stale-after-hours", "-1"],
        ["--config", "/nonexistent/catalog.toml"],
    ],
)
def test_bad_quality_inputs_fail_before_database(args, monkeypatch):
    cli = importlib.import_module("github_radar.cli.collection")

    def unexpected(*args, **kwargs):
        pytest.fail("Invalid input reached database")

    monkeypatch.setattr(cli, "make_engine", unexpected)
    assert CliRunner().invoke(app, ["collection", "quality", *args]).exit_code == 2


def test_database_error_is_nonzero_and_redacted(monkeypatch):
    cli = importlib.import_module("github_radar.cli.collection")

    def fail(*args, **kwargs):
        raise OperationalError("private-connection-details", {}, Exception("secret"))

    monkeypatch.setattr(cli, "make_engine", fail)
    result = CliRunner().invoke(app, ["collection", "stats", "--json"])
    assert result.exit_code == 1
    assert "Cannot read" in result.output
    assert "private-connection-details" not in result.output and "secret" not in result.output


def test_expansion_proposal_is_a_bounded_local_plan():
    config = load_discovery_config(SAMPLE.with_name("discovery-expansion-proposal.toml"))
    plan = plan_discovery(config, as_of=AS_OF)
    assert len(plan.cohorts) == 12
    assert plan.candidate_slots == plan.execution.max_candidates == 60
    assert plan.search_requests_without_retries == 12
    assert plan.execution.max_new_repositories == 50 and plan.execution.max_seconds == 300
    assert plan.budget.total == 180 and plan.budget.contributors == plan.budget.profiles == 0
    assert {c.sort for c in plan.cohorts} == {"updated"}
