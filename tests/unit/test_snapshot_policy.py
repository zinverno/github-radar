"""Unit tests for snapshot semantics and topic normalization."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from github_radar.domain import Repository, RepositorySnapshot
from github_radar.storage import normalize_topic

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def test_topic_normalization() -> None:
    assert normalize_topic("MCP") == "mcp"
    assert normalize_topic("  AI Agents  ") == "ai agents"
    assert normalize_topic("mcp") == "mcp"


def make_snapshot(
    *,
    stars: int,
    forks: int,
    captured_at: datetime = T0,
) -> RepositorySnapshot:
    return RepositorySnapshot(
        captured_at=captured_at,
        stars=stars,
        forks=forks,
        watchers=1,
        open_issues=2,
        size_kb=3,
        pushed_at=T0,
    )


def test_snapshot_same_counters() -> None:
    a = make_snapshot(stars=100, forks=12)
    b = make_snapshot(stars=100, forks=12)
    assert a.same_counters(b) is True


def test_snapshot_different_counters() -> None:
    a = make_snapshot(stars=100, forks=12)
    b = make_snapshot(stars=101, forks=12)
    assert a.same_counters(b) is False


def test_snapshot_differs_on_pushed_at() -> None:
    a = make_snapshot(stars=100, forks=12)
    b = RepositorySnapshot(
        captured_at=T0 + timedelta(days=1),
        stars=100,
        forks=12,
        watchers=1,
        open_issues=2,
        size_kb=3,
        pushed_at=T0 + timedelta(hours=1),
    )
    assert a.same_counters(b) is False


def test_snapshot_from_repository() -> None:
    repo = Repository(
        github_id=1,
        node_id="n",
        owner_login="octo",
        owner_github_id=42,
        name="repo",
        full_name="octo/repo",
        description=None,
        html_url=None,
        homepage=None,
        default_branch="main",
        primary_language="python",
        is_fork=False,
        is_archived=False,
        is_disabled=False,
        is_template=None,
        visibility="public",
        created_at=None,
        updated_at=None,
        pushed_at=None,
        stars=500,
        forks=33,
        watchers=7,
        open_issues=9,
        size_kb=100,
    )
    snapshot = RepositorySnapshot.from_repository(repo, captured_at=T0)
    assert snapshot.stars == 500
    assert snapshot.forks == 33
    assert snapshot.captured_at == T0