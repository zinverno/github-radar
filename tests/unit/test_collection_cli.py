"""Local command validation, reviewed-plan integrity and explicit execution dispatch."""

import json

import pytest
from typer.testing import CliRunner

from github_radar.config.settings import GitHubRequestLimits
from github_radar.discovery.planning import load_discovery_config, plan_discovery
from tests.unit.test_discovery_planning import AS_OF, SAMPLE
from tests.unit.test_discovery_planning import offline_cli as offline_cli


@pytest.mark.parametrize(
    "command",
    [
        "discover-plan",
        "discover-execute",
        "discover",
        "enrich",
        "refresh",
        "update",
        "collection-runs",
    ],
)
def test_collection_help(offline_cli, command):
    assert CliRunner().invoke(offline_cli.app, [command, "--help"]).exit_code == 0


def test_reviewed_plan_execution_and_dry_run(offline_cli, tmp_path, monkeypatch):
    reviewed = plan_discovery(load_discovery_config(SAMPLE), as_of=AS_OF)
    path = tmp_path / "reviewed.json"
    path.write_text(reviewed.model_dump_json(), encoding="utf-8")
    preview = CliRunner().invoke(offline_cli.app, ["discover-execute", str(path), "--dry-run"])
    assert preview.exit_code == 0
    assert json.loads(preview.output) == reviewed.model_dump(mode="json")
    calls = []

    async def execute(settings, kind, **kwargs):
        calls.append((kind, kwargs["plan"]))

    monkeypatch.setattr(offline_cli, "_collection_run", execute)
    result = CliRunner().invoke(offline_cli.app, ["discover-execute", str(path)])
    assert result.exit_code == 0
    assert calls == [("discover", reviewed)]


@pytest.mark.parametrize(
    "change",
    [
        {"candidate_slots": 999},
        {"per_page": 0},
        {"per_page": True},
        {"execution": {"max_seconds": 0}},
        {"execution": {"max_new_repositories": -1}},
        {"budget": {"total": True}},
        {"budget": {"search": 0}},
    ],
)
def test_tampered_plan_never_executes(offline_cli, tmp_path, change):
    reviewed = plan_discovery(load_discovery_config(SAMPLE), as_of=AS_OF).model_dump(mode="json")
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({**reviewed, **change}), encoding="utf-8")
    result = CliRunner().invoke(offline_cli.app, ["discover-execute", str(path)])
    assert result.exit_code == 2


@pytest.mark.parametrize(
    "args",
    [
        ["enrich", "octo/one", "--max-profiles", "0", "--dry-run"],
        ["refresh", "--dry-run"],
        ["update", "--dry-run"],
        ["discover", "mcp", "--dry-run", "--max-new-repositories", "0"],
    ],
)
def test_all_previews_are_local_only(offline_cli, args):
    result = CliRunner().invoke(offline_cli.app, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["mode"] == "local-only"


@pytest.mark.parametrize(
    "args",
    [
        ["enrich", "octo/one"],
        ["enrich", "octo/one", "--max-profiles", "-1"],
        ["refresh", "--limit", "0"],
        ["refresh", "--max-seconds", "0"],
        ["refresh", "--budget", '{"total":-1}'],
        ["refresh", "--budget", '{"profiles":false}'],
        ["refresh", "--budget", '{"typo":10}'],
        ["discover", "x", "--sort", "invalid"],
    ],
)
def test_invalid_execution_fails_before_io(offline_cli, args):
    result = CliRunner().invoke(offline_cli.app, args)
    assert result.exit_code == 2, result.output


def test_partial_budget_override_preserves_other_caps(offline_cli):
    offline_cli.get_settings().github_request_budget = GitHubRequestLimits(profiles=0)
    result = CliRunner().invoke(
        offline_cli.app,
        [
            "enrich",
            "octo/one",
            "--max-profiles",
            "10",
            "--budget",
            '{"total":5}',
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["budget"]["profiles"] == 0
    assert json.loads(result.output)["budget"]["total"] == 5


def test_local_cli_never_claims_rollback_for_uncertain_database(offline_cli, capsys):
    from sqlalchemy.exc import OperationalError
    from typer import Exit
    async def uncertain():
        raise OperationalError("PRIVATE SQL", {}, RuntimeError("PRIVATE DRIVER"),
                               connection_invalidated=True)
    with pytest.raises(Exit) as error:
        offline_cli._run(uncertain())
    output = capsys.readouterr().err
    assert error.value.exit_code == 1
    assert "outcome uncertain" in output and "do not replay" in output
    assert "PRIVATE" not in output
