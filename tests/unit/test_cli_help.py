"""CLI command registration and help rendering (no database required)."""

from __future__ import annotations

from typer.testing import CliRunner, Result

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
PHASE4_COMMANDS = [
    "ecosystem",
    "bridges",
    "bridges__develop",
    "bridges__cross-topic",
    "bridges__repos",
    "related-topics",
    "related-repos",
]


def _invoke(command: str) -> Result:
    return runner.invoke(app, command.split("__") + ["--help"])


def test_all_commands_available() -> None:
    for command in (
        PHASE1_COMMANDS + PHASE2_COMMANDS + PHASE3_COMMANDS + PHASE4_COMMANDS
    ):
        result = _invoke(command)
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


def test_graph_bridges_develop_help_renders() -> None:
    result = runner.invoke(app, ["graph", "bridges", "develop", "--help"])
    assert result.exit_code == 0
    assert "bridge" in result.output.lower()
    assert "--min-confidence" in result.output
    assert "--login" in result.output


def test_graph_group_still_lists_bridges() -> None:
    result = runner.invoke(app, ["graph", "--help"])
    assert result.exit_code == 0
    assert "bridges" in result.output


def test_phase4_top_level_help_renders() -> None:
    # The Phase 4 surface must be reachable at the TOP level of the CLI.
    for command in [
        ["ecosystem", "--help"],
        ["bridges", "--help"],
        ["bridges", "develop", "--help"],
        ["bridges", "cross-topic", "--help"],
        ["bridges", "repos", "--help"],
        ["related-topics", "--help"],
        ["related-repos", "--help"],
    ]:
        result = runner.invoke(app, command)
        assert result.exit_code == 0, (
            f"{' '.join(command)} --help failed: {result.output}"
        )


def test_bridges_group_is_mounted_twice() -> None:
    top_level = runner.invoke(app, ["bridges", "--help"])
    nested = runner.invoke(app, ["graph", "bridges", "--help"])
    assert top_level.exit_code == 0
    assert nested.exit_code == 0
    for subcommand in ("develop", "cross-topic", "repos"):
        assert subcommand in top_level.output
        assert subcommand in nested.output


def test_ecosystem_help_mentions_graph() -> None:
    result = runner.invoke(app, ["ecosystem", "--help"])
    assert result.exit_code == 0
    assert "ecosystem graph" in result.output
    assert "--centrality-limit" in result.output


def test_related_commands_help_mentions_graph_footprint() -> None:
    topics = runner.invoke(app, ["related-topics", "--help"])
    assert topics.exit_code == 0
    assert "graph footprint" in topics.output
    repos = runner.invoke(app, ["related-repos", "--help"])
    assert repos.exit_code == 0
    assert "graph footprint" in repos.output