"""CLI command registration and help rendering (no database required)."""

from __future__ import annotations

from typer.testing import CliRunner

from github_radar.cli.app import app

runner = CliRunner()

PHASE1_COMMANDS = [
    "init-db",
    "discover",
    "update",
    "repos",
    "repo",
    "stats",
    "rate-limit",
]
PHASE2_COMMANDS = ["trending", "topics", "topic"]
PHASE3_COMMANDS = ["developers", "developer", "emerging-developers"]


def test_all_commands_available() -> None:
    for command in PHASE1_COMMANDS + PHASE2_COMMANDS + PHASE3_COMMANDS:
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0, (
            f"{command!r} did not resolve to a registered command: "
            f"{result.output}"
        )


def test_trending_help_renders() -> None:
    result = runner.invoke(app, ["trending", "--help"])
    assert result.exit_code == 0
    assert "Rank tracked repositories by momentum score" in result.output


def test_topics_help_renders() -> None:
    result = runner.invoke(app, ["topics", "--help"])
    assert result.exit_code == 0
    assert "Aggregate tracked topics by repository coverage and momentum" in result.output


def test_topic_help_renders() -> None:
    result = runner.invoke(app, ["topic", "--help"])
    assert result.exit_code == 0
    assert "List repositories for a topic, ranked by momentum" in result.output


def test_developers_help_renders() -> None:
    result = runner.invoke(app, ["developers", "--help"])
    assert result.exit_code == 0
    assert "List tracked developers with their intelligence scores" in result.output
    assert "--has-public-contact" in result.output


def test_developer_help_renders() -> None:
    result = runner.invoke(app, ["developer", "--help"])
    assert result.exit_code == 0
    assert "Show one developer's profile, activity and emerging" in result.output


def test_emerging_developers_help_renders() -> None:
    result = runner.invoke(app, ["emerging-developers", "--help"])
    assert result.exit_code == 0
    assert "EMERGING" in result.output


def test_phase1_help_still_renders() -> None:
    for command in PHASE1_COMMANDS:
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0, f"{command} --help failed: {result.output}"