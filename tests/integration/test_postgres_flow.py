"""Optional PostgreSQL integration tests.

These run against a real PostgreSQL database identified by
``TEST_DATABASE_URL`` (must use the ``postgresql+asyncpg://`` scheme). They are
skipped automatically when the variable is unset:

    TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/github_radar_test \
        uv run pytest -m integration

Each test starts from a freshly created schema (tables are dropped afterwards).
The GitHub layer is mocked via respx — these tests never hit the live API.

Destructive fixtures are unconditionally guarded: ``TEST_DATABASE_URL`` must
target an explicitly test-looking database (e.g. ``github_radar_test``) and
must never equal ``DATABASE_URL`` or name the application database
``github_radar``. An unsafe value fails loudly and refuses to run instead of
silently skipping or wiping the normal database.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.safety import validate_test_database_url  # noqa: E402

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

PROJECT_ROOT = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        TEST_DATABASE_URL is None,
        reason="Set TEST_DATABASE_URL to run PostgreSQL integration tests.",
    ),
]


def _app_database_url() -> str | None:
    """Return the configured application database URL, if any."""
    return os.environ.get("DATABASE_URL")


def _require_safe_test_database() -> None:
    """Refuse destructive fixtures unless TEST_DATABASE_URL is safe.

    Unlike the ``skipif`` marker (which covers a *missing* variable), an
    explicitly provided but unsafe target raises loudly instead of skipping.
    """
    validate_test_database_url(
        test_url=os.environ.get("TEST_DATABASE_URL", ""),
        app_url=_app_database_url(),
    )


@pytest.fixture(autouse=True, scope="module")
def _guard_test_database() -> None:
    """Validate TEST_DATABASE_URL before any destructive setup runs."""
    _require_safe_test_database()

from github_radar.analytics import compute_metrics  # noqa: E402
from github_radar.config import Settings  # noqa: E402
from github_radar.discovery import RepositoryDiscoveryService  # noqa: E402
from github_radar.domain import (  # noqa: E402
    Contributor,
    Developer,
    Repository,
    RepositorySnapshot,
)
from github_radar.github.client import GitHubClient  # noqa: E402
from github_radar.services import RepositoryUpdateService  # noqa: E402
from github_radar.storage import repositories as storage  # noqa: E402
from github_radar.storage.db import make_engine, make_session_factory  # noqa: E402
from github_radar.storage.models import Base  # noqa: E402
from github_radar.util import utcnow  # noqa: E402
from tests.conftest import (  # noqa: E402
    BASE_URL,
    contributors_payload,
    repo_detail_payload,
    search_item_payload,
    search_response_payload,
    user_payload,
)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    assert TEST_DATABASE_URL is not None
    eng = make_engine(TEST_DATABASE_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = make_session_factory(engine)
    sess = factory()
    yield sess
    await sess.close()


def repo_domain(
    *,
    github_id: int,
    name: str,
    stars: int,
    topics: tuple[str, ...] = (),
    forks: int = 3,
) -> Repository:
    return Repository(
        github_id=github_id,
        node_id=f"n-{github_id}",
        owner_login="octo",
        owner_github_id=42,
        name=name,
        full_name=f"octo/{name}",
        description="desc",
        html_url=f"https://github.com/octo/{name}",
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
        stars=stars,
        forks=forks,
        watchers=5,
        open_issues=1,
        size_kb=10,
        topics=topics,
    )


async def test_repository_upsert_and_relationships(session: AsyncSession) -> None:
    now = utcnow()

    owner = Developer(github_id=42, login="octo", name="Octo Cat", followers=100)
    dev_row, created = await storage.sync_profile(session, owner)
    assert created is True

    repo = repo_domain(github_id=7, name="servers", stars=100, topics=("mcp", "ai"))
    row, created = await storage.upsert_repository(session, repo, now=now)
    assert created is True
    assert row.owner_id == dev_row.id

    await storage.set_repository_topics(session, row, ["MCP", "ai", "Tools"], now=now)
    topics = await storage.topics_for_repository(session, row.id)
    assert {t.name for t in topics} == {"mcp", "ai", "tools"}

    added = await storage.sync_contributors(
        session,
        row,
        [
            Contributor(
                developer=Developer(
                    github_id=101, login="alice", avatar_url="http://a", html_url="http://b"
                ),
                contributions=42,
            )
        ],
        now=now,
    )
    assert added.developers_created == 1
    assert added.snapshots_written == 1
    contrib_rows = await storage.contributors_for_repository(session, row.id)
    assert contrib_rows[0][1] == 42

    row2, created = await storage.upsert_repository(session, repo, now=now)
    assert created is False
    assert row2.id == row.id

    await storage.sync_contributors(
        session,
        row,
        [
            Contributor(
                developer=Developer(github_id=101, login="alice"), contributions=60
            )
        ],
        now=now,
    )
    contrib_rows = await storage.contributors_for_repository(session, row.id)
    assert contrib_rows[0][1] == 60
    await session.commit()


async def test_snapshot_insertion_and_dedup(session: AsyncSession) -> None:
    now = utcnow()
    repo = repo_domain(github_id=9, name="one", stars=10)
    row, _ = await storage.upsert_repository(session, repo, now=now)

    first = RepositorySnapshot.from_repository(repo, captured_at=now)
    inserted = await storage.insert_snapshot_if_changed(session, row.id, first)
    assert inserted is not None
    assert await storage.latest_snapshot(session, row.id) is not None

    dup = RepositorySnapshot.from_repository(repo, captured_at=now)
    assert await storage.insert_snapshot_if_changed(session, row.id, dup) is None

    # A changed observation at a *new* instant appends to history.
    later = now + timedelta(days=1)
    changed = RepositorySnapshot(
        captured_at=later,
        stars=15,
        forks=first.forks,
        watchers=first.watchers,
        open_issues=first.open_issues,
        size_kb=first.size_kb,
        pushed_at=first.pushed_at,
    )
    assert await storage.insert_snapshot_if_changed(session, row.id, changed) is not None
    snapshots = await storage.snapshots_for_repository(session, row.id)
    assert [s.stars for s in snapshots] == [10, 15]

    # A changed observation at the *same* instant replaces the row with it,
    # keeping history idempotent (unique on (repository_id, captured_at)).
    same_instant = RepositorySnapshot(
        captured_at=later,
        stars=20,
        forks=changed.forks,
        watchers=changed.watchers,
        open_issues=changed.open_issues,
        size_kb=changed.size_kb,
        pushed_at=changed.pushed_at,
    )
    assert await storage.insert_snapshot_if_changed(
        session, row.id, same_instant
    ) is not None
    snapshots = await storage.snapshots_for_repository(session, row.id)
    assert len(snapshots) == 2
    assert [s.stars for s in snapshots] == [10, 20]
    await session.commit()


async def test_unchanged_observation_is_recorded(session: AsyncSession) -> None:
    """record_observation persists *every* poll, even unchanged state."""
    now = utcnow()
    repo = repo_domain(github_id=13, name="flat", stars=100, forks=3)
    row, _ = await storage.upsert_repository(session, repo, now=now)
    first_at = now
    second_at = now + timedelta(days=7)
    third_at = second_at + timedelta(days=7)

    # Unchanged counters observed at TWO different instants → two rows.
    first = await storage.record_observation(
        session, row.id, RepositorySnapshot.from_repository(repo, first_at)
    )
    second = await storage.record_observation(
        session, row.id, RepositorySnapshot.from_repository(repo, second_at)
    )
    assert first is not None and second is not None
    assert first.id != second.id
    rows = await storage.snapshots_for_repository(session, row.id)
    assert len(rows) == 2
    assert [s.captured_at for s in rows] == [first_at, second_at]
    assert [s.stars for s in rows] == [100, 100]

    # Same counters + same exact instant → idempotent, no extra row.
    dup = await storage.record_observation(
        session, row.id, RepositorySnapshot.from_repository(repo, second_at)
    )
    assert dup.id == second.id
    rows = await storage.snapshots_for_repository(session, row.id)
    assert len(rows) == 2

    # A later observation never overwrites earlier history.
    third = await storage.record_observation(
        session, row.id, RepositorySnapshot.from_repository(repo, third_at)
    )
    assert third.id not in (first.id, second.id)
    rows = await storage.snapshots_for_repository(session, row.id)
    assert len(rows) == 3
    assert [s.captured_at for s in rows] == [first_at, second_at, third_at]

    # The Phase 1 filter API still refuses an unchanged re-observation.
    assert (
        await storage.insert_snapshot_if_changed(
            session, row.id, RepositorySnapshot.from_repository(repo, third_at)
        )
        is None
    )

    # Analytics see a true, observed zero-growth 7-day window end to end.
    metrics = compute_metrics([s.to_domain() for s in rows])
    seven = metrics.stars_7d
    assert seven is not None
    assert seven.delta == 0
    assert seven.growth_pct == 0.0
    assert seven.window.complete is True
    await session.commit()


async def test_compute_stats(session: AsyncSession) -> None:
    now = utcnow()
    repo1 = repo_domain(github_id=11, name="alpha", stars=10)
    repo2 = repo_domain(github_id=12, name="beta", stars=20)
    r1, _ = await storage.upsert_repository(session, repo1, now=now)
    r2, _ = await storage.upsert_repository(session, repo2, now=now)
    for r, repo in ((r1, repo1), (r2, repo2)):
        await storage.insert_snapshot_if_changed(
            session, r.id, RepositorySnapshot.from_repository(repo, now)
        )
    await storage.set_repository_topics(session, r1, ["mcp"], now=now)
    await storage.sync_contributors(
        session,
        r1,
        [Contributor(developer=Developer(github_id=777, login="dev"), contributions=1)],
        now=now,
    )
    await session.commit()

    stats = await storage.compute_stats(session)
    assert stats.repositories == 2
    assert stats.developers == 1
    assert stats.topics == 1
    assert stats.snapshots == 2
    assert stats.oldest_snapshot_at is not None
    assert stats.newest_snapshot_at is not None


async def test_mocked_discovery_and_update_preserves_history(
    session: AsyncSession, engine: AsyncEngine, api_mock
) -> None:
    """Full end-to-end: mocked GitHub API + real PostgreSQL."""
    assert TEST_DATABASE_URL is not None
    settings = Settings(
        github_token="test-token",
        database_url=TEST_DATABASE_URL,
        github_api_base_url=BASE_URL,
        http_max_retries=1,
        rate_limit_pause_threshold=0,
        contributors_limit_per_repo=10,
    )

    detail1 = repo_detail_payload(
        github_id=100, name="one", stars=10, topics=("mcp",)
    )
    detail2 = repo_detail_payload(
        github_id=101, name="two", stars=20, topics=("mcp", "agency")
    )
    api_mock.get(f"{BASE_URL}/search/repositories").mock(
        return_value=httpx.Response(
            200,
            json=search_response_payload(
                [
                    search_item_payload(github_id=100, name="one"),
                    search_item_payload(github_id=101, name="two"),
                ]
            ),
        )
    )
    api_mock.get(f"{BASE_URL}/repos/octo/one").mock(
        return_value=httpx.Response(200, json=detail1)
    )
    api_mock.get(f"{BASE_URL}/repos/octo/two").mock(
        return_value=httpx.Response(200, json=detail2)
    )
    api_mock.get(f"{BASE_URL}/repos/octo/one/contributors").mock(
        return_value=httpx.Response(
            200, json=contributors_payload([("alice", 200, 15), ("bob", 201, 4)])
        )
    )
    api_mock.get(f"{BASE_URL}/repos/octo/two/contributors").mock(
        return_value=httpx.Response(
            200, json=contributors_payload([("carol", 202, 9)])
        )
    )
    for login, github_id in (("alice", 200), ("bob", 201), ("carol", 202)):
        api_mock.get(f"{BASE_URL}/users/{login}").mock(
            return_value=httpx.Response(
                200, json=user_payload(login=login, github_id=github_id)
            )
        )

    def make_session() -> AsyncSession:
        return make_session_factory(engine)()

    # First discovery run
    async with GitHubClient(settings) as client:
        sess = make_session()
        try:
            report = await RepositoryDiscoveryService(client, sess).discover(
                query="mcp", limit=50
            )
        finally:
            await sess.close()
    assert report.discovered == 2
    assert report.snapshots_inserted == 2
    assert report.developer_profiles_fetched == 3
    assert report.contributors_added == 3
    assert report.contributor_relationships_observed == 3
    assert report.developer_snapshots_written == 3

    # Second discovery run: same state → updates only, no new snapshots
    async with GitHubClient(settings) as client:
        sess = make_session()
        try:
            report = await RepositoryDiscoveryService(client, sess).discover(
                query="mcp", limit=50
            )
        finally:
            await sess.close()
    assert report.discovered == 0
    assert report.updated == 2
    assert report.snapshots_inserted == 0

    rows = await storage.list_repos_with_latest(session)
    assert len(rows) == 2
    for repo_row, _latest in rows:
        snapshots = await storage.snapshots_for_repository(session, repo_row.id)
        assert len(snapshots) == 1
        topics = await storage.topics_for_repository(session, repo_row.id)
        assert any(t.name == "mcp" for t in topics)

    # Update run: counters unchanged → no new snapshot, profiles still fresh
    async with GitHubClient(settings) as client:
        sess = make_session()
        try:
            update_report = await RepositoryUpdateService(client, sess).update()
        finally:
            await sess.close()
    assert update_report.repositories_refreshed == 2
    assert update_report.snapshots_inserted == 0
    assert update_report.developer_profiles_fetched == 0
    assert update_report.developer_profiles_reused == 3
    assert update_report.contributor_relationships_observed == 3

    # Contributor/profile observations accumulate per fetch instant.
    repo_one = await storage.get_repository_by_github_id(session, 100)
    alice = await storage.get_developer_by_github_id(session, 200)
    assert repo_one is not None and alice is not None
    link_obs = await storage.contributor_snapshots_for_relationship(
        session, repository_id=repo_one.id, developer_id=alice.id
    )
    assert len(link_obs) == 2  # discovery observation + one update observation
    assert link_obs[-1].contributions == 15
    dev_snaps = await storage.developer_snapshots_for(session, alice.id)
    assert len(dev_snaps) == 1  # profile fetched once, then reused while fresh

    # GitHub now reports more stars for repo one → next update snapshots it
    detail1["stargazers_count"] = 50
    detail1["subscribers_count"] = 20
    api_mock.get(f"{BASE_URL}/repos/octo/one").mock(
        return_value=httpx.Response(200, json=detail1)
    )
    async with GitHubClient(settings) as client:
        sess = make_session()
        try:
            update_report = await RepositoryUpdateService(client, sess).update()
        finally:
            await sess.close()
    assert update_report.snapshots_inserted == 1

    repo_one = await storage.get_repository_by_github_id(session, 100)
    assert repo_one is not None
    snapshots = await storage.snapshots_for_repository(session, repo_one.id)
    assert len(snapshots) == 2
    assert snapshots[-1].stars == 50


def _drop_test_schema() -> None:
    """Remove the app schema and the Alembic bookkeeping table."""
    assert TEST_DATABASE_URL is not None
    _require_safe_test_database()

    async def _go() -> None:
        eng = make_engine(TEST_DATABASE_URL)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await eng.dispose()

    asyncio.run(_go())


def _run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    assert TEST_DATABASE_URL is not None
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    env["DATABASE_URL"] = TEST_DATABASE_URL
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def _alembic_head_revision() -> str:
    """The repository's *current* Alembic head, read from the scripts.

    Derived from Alembic metadata (``ScriptDirectory.get_heads``) via the same
    configuration the CLI uses (``alembic.ini`` + an absolute
    ``script_location``), so the expectation tracks future migrations instead of
    hard-coding a revision id.
    """
    config = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(PROJECT_ROOT / "migrations")
    )
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, f"expected a single-head history, got {heads}"
    return heads[0]


def test_alembic_migrations_apply_to_postgres() -> None:
    """Migrations must apply to the real PostgreSQL via DATABASE_URL.

    Guards the regression where online migrations fed the alembic.ini
    ``driver://`` placeholder to SQLAlchemy instead of the configured
    ``DATABASE_URL`` (``NoSuchModuleError: Can't load plugin:
    sqlalchemy.dialects:driver``).

    The expected revision is *not* hard-coded: the test derives the head from
    ``ScriptDirectory`` and only asserts that the migrated database reports
    that same revision with the ``(head)`` marker.
    """
    _drop_test_schema()

    current = _run_alembic("current")
    assert current.returncode == 0, current.stderr
    assert "Can't load plugin" not in current.stdout + current.stderr

    upgrade = _run_alembic("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr

    expected_head = _alembic_head_revision()
    upgraded = _run_alembic("current")
    assert upgraded.returncode == 0, upgraded.stderr
    assert "Can't load plugin" not in upgraded.stdout + upgraded.stderr
    assert expected_head in upgraded.stdout + upgraded.stderr
    assert "(head)" in upgraded.stdout + upgraded.stderr

    assert _run_alembic("downgrade", "base").returncode == 0
    _drop_test_schema()