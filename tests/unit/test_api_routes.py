"""ASGI tests for the web API (FastAPI over an in-memory sqlite).

The app factory needs no PostgreSQL: tests hand ``create_app`` an engine and
session factory over a ``sqlite+aiosqlite`` in-memory database (StaticPool).
Everything else — analytics, graph, AI cache — is the same code in production.

Contract under test:
* the error envelope is always ``{"error": {"code", "message", "details"}}``;
* unknown ``/api/*`` paths return JSON 404s, never the SPA fallback;
* pagination bounds are rejected with typed codes, not silently clamped;
* windows outside ``1d|7d|30d`` are rejected;
* null means unavailable (an empty-day count is never invented);
* AI reads never generate: only ``POST /ai/generate`` can spend provider budget.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import BigInteger
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from github_radar.api.app import create_app
from github_radar.config import Settings
from github_radar.domain import Contributor, Developer, Repository, RepositorySnapshot
from github_radar.storage import repositories as storage
from github_radar.storage.db import make_session_factory
from github_radar.storage.models import Base
from github_radar.util import utcnow


# sqlite only autoincrements an exact ``INTEGER PRIMARY KEY``; the models declare
# BigInteger primary keys for PostgreSQL. Compile BigInteger as INTEGER on
# sqlite so ``create_all`` yields self-incrementing tables for tests.
@compiles(BigInteger, "sqlite")
def _sqlite_bigint_as_integer(element: Any, compiler: Any, **kw: Any) -> str:
    return "INTEGER"

# A non-empty postgres URL is required by the Settings validator, but the app
# never opens it because we inject our own engine/ session factory.
TEST_SETTINGS = Settings(
    github_token="test-token",
    database_url="postgresql+asyncpg://u:p@localhost:5432/radar",
    github_api_base_url="https://api.github.com",
    http_max_retries=2,
    rate_limit_pause_threshold=0,
)


def _repo_domain(
    *,
    github_id: int,
    name: str,
    stars: int,
    topics: tuple[str, ...],
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


async def _seed(session: AsyncSession) -> None:
    """Three repositories with enough history to exercise analytics.

    ``octo/alpha`` grows (HIGH confidence), ``octo/beta`` is flat, and
    ``octo/gamma`` has a single observation (no window data — must be null).
    """
    now = utcnow()
    await storage.sync_profile(session, Developer(github_id=42, login="octo"), now=now)

    specs = [
        (11, "alpha", 15, ("mcp", "ai")),
        (12, "beta", 20, ("mcp",)),
        (13, "gamma", 5, ("agency",)),
    ]
    for github_id, name, latest_stars, topics in specs:
        repo = _repo_domain(github_id=github_id, name=name, stars=latest_stars, topics=topics)
        row, _ = await storage.upsert_repository(session, repo, now=now)
        await storage.set_repository_topics(session, row, list(topics), now=now)

        points: list[tuple[int, datetime]] = []
        if name == "alpha":
            points = [(10, now - timedelta(days=14)), (12, now - timedelta(days=7)), (15, now)]
        elif name == "beta":
            points = [(20, now - timedelta(days=8)), (21, now)]
        else:
            points = [(5, now)]
        for stars, captured_at in points:
            repo = _repo_domain(github_id=github_id, name=name, stars=stars, topics=topics)
            await storage.insert_snapshot_if_changed(
                session, row.id, RepositorySnapshot.from_repository(repo, captured_at)
            )

    alpha = await storage.get_repository_by_full_name(session, "octo/alpha")
    beta = await storage.get_repository_by_full_name(session, "octo/beta")
    assert alpha is not None and beta is not None
    for row, contributor, github_id in (
        (alpha, "alice", 200),
        (alpha, "bob", 201),
        (beta, "bob", 201),
    ):
        await storage.sync_contributors(
            session,
            row,
            [
                Contributor(
                    developer=Developer(github_id=github_id, login=contributor),
                    contributions=15 if contributor == "alice" else 4,
                )
            ],
            now=now,
        )
    await session.commit()


async def _seed_stale(session: AsyncSession) -> None:
    """Two repositories observed 40 days ago: a stale, small dataset.

    ``octo/delta`` has a single observation (momentum unavailable, null always)
    while ``octo/zeta`` has a full window with flat stars but growing forks — a
    genuine ``stars_growth_pct == 0`` that must stay distinct from null.
    """
    now = utcnow()
    past = now - timedelta(days=40)
    older = now - timedelta(days=48)
    await storage.sync_profile(session, Developer(github_id=42, login="octo"), now=now)

    delta = _repo_domain(github_id=21, name="delta", stars=8, topics=("delta-topic",))
    delta_row, _ = await storage.upsert_repository(session, delta, now=now)
    await storage.set_repository_topics(session, delta_row, ["delta-topic"], now=now)
    await storage.insert_snapshot_if_changed(
        session, delta_row.id, RepositorySnapshot.from_repository(delta, past)
    )

    zeta = _repo_domain(github_id=22, name="zeta", stars=10, topics=("zero-topic",))
    zeta_row, _ = await storage.upsert_repository(session, zeta, now=now)
    await storage.set_repository_topics(session, zeta_row, ["zero-topic"], now=now)
    await storage.insert_snapshot_if_changed(
        session,
        zeta_row.id,
        RepositorySnapshot.from_repository(
            _repo_domain(github_id=22, name="zeta", stars=10, topics=("zero-topic",), forks=2),
            older,
        ),
    )
    await storage.insert_snapshot_if_changed(
        session,
        zeta_row.id,
        RepositorySnapshot.from_repository(
            _repo_domain(github_id=22, name="zeta", stars=10, topics=("zero-topic",), forks=4),
            past,
        ),
    )
    await session.commit()


@pytest.fixture(scope="module")
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="module")
async def app(engine: AsyncEngine) -> Any:
    # Seed once through the same storage helpers the CLI uses.
    factory = make_session_factory(engine)
    async with factory() as session:
        await _seed(session)
    return create_app(
        settings=TEST_SETTINGS,
        engine=engine,
        session_factory=factory,
        web_dist=Path("/nonexistent/dist"),
    )


@pytest.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture(scope="module")
async def engine_stale() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="module")
async def app_stale(engine_stale: AsyncEngine) -> Any:
    factory = make_session_factory(engine_stale)
    async with factory() as session:
        await _seed_stale(session)
    return create_app(
        settings=TEST_SETTINGS,
        engine=engine_stale,
        session_factory=factory,
        web_dist=Path("/nonexistent/dist"),
    )


@pytest.fixture
async def client_stale(app_stale: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app_stale)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


def _error_shape(body: Any) -> str:
    assert isinstance(body, dict)
    assert "error" in body
    inner = body["error"]
    assert isinstance(inner, dict)
    assert isinstance(inner.get("code"), str)
    assert isinstance(inner.get("message"), str)
    assert isinstance(inner.get("details"), dict)
    return str(inner["code"])


async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_unknown_api_route_returns_typed_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/definitely-not-real")
    assert response.status_code == 404
    assert _error_shape(response.json()) == "not_found"


async def test_openapi_schema_is_mounted(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for route in (
        "/api/v1/health",
        "/api/v1/repositories",
        "/api/v1/repositories/summary",
        "/api/v1/topics/{name}",
        "/api/v1/developers/emerging",
        "/api/v1/developers/high-activity",
        "/api/v1/ecosystem",
        "/api/v1/ecosystem/nodes",
        "/api/v1/ecosystem/edges",
        "/api/v1/bridges",
        "/api/v1/bridges/cross-topic",
        "/api/v1/search",
        "/api/v1/ai/status",
        "/api/v1/ai/artifacts",
        "/api/v1/ai/generate",
    ):
        assert route in paths


async def test_pagination_rejects_bounds(client: httpx.AsyncClient) -> None:
    for limit in ("0", "101"):
        response = await client.get("/api/v1/repositories", params={"limit": limit})
        assert response.status_code == 400
        assert _error_shape(response.json()) == "invalid_limit"
    response = await client.get("/api/v1/repositories", params={"offset": "-1"})
    assert response.status_code == 400
    assert _error_shape(response.json()) == "invalid_offset"


async def test_invalid_window_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/v1/repositories/summary", params={"window": "5d"}
    )
    # The OpenAPI pattern restricts windows to 1d|7d|30d, so an out-of-range
    # value is a request-validation error in the uniform envelope.
    assert response.status_code == 422
    assert _error_shape(response.json()) == "validation_error"


async def test_repositories_list_page_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/repositories", params={"limit": 2, "offset": 0})
    body = response.json()
    assert response.status_code == 200
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert body["total"] == 3
    assert body["has_more"] is True
    assert len(body["items"]) == 2
    full_names = {item["full_name"] for item in body["items"]}
    assert full_names <= {"octo/alpha", "octo/beta", "octo/gamma"}
    assert body["has_more"] is True
    # The second page contains the remaining repository.
    page_two = await client.get("/api/v1/repositories", params={"limit": 2, "offset": 2})
    assert page_two.json()["total"] == 3
    assert len(page_two.json()["items"]) == 1


async def test_repositories_summary_null_contract(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/repositories/summary")
    body = response.json()
    assert response.status_code == 200
    assert body["repository_count"] == 3
    assert body["healthy_count"] == 2  # alpha + beta grew over a window
    assert body["average_daily_stars"] is not None
    assert body["average_daily_forks"] is not None
    assert isinstance(body["trend_classes"], dict)
    assert body["high_confidence_count"] >= 1


async def test_repository_detail_by_id_and_name(client: httpx.AsyncClient) -> None:
    listed = await client.get("/api/v1/repositories", params={"limit": 1})
    repository_id = listed.json()["items"][0]["id"]
    by_id = await client.get(f"/api/v1/repositories/{repository_id}")
    by_name = await client.get("/api/v1/repositories/octo/beta")
    assert by_id.status_code == 200
    assert by_name.status_code == 200
    detail = by_name.json()
    assert detail["full_name"] == "octo/beta"
    assert detail["snapshot_count"] == 2
    assert {"mcp"} <= set(detail["topics"])
    assert detail["stars_7d"] == 1  # beta grew 20 -> 21 over the window
    assert detail["confidence"] is not None


async def test_repository_detail_null_when_no_window_data(
    client: httpx.AsyncClient,
) -> None:
    detail = (await client.get("/api/v1/repositories/octo/gamma")).json()
    assert detail["snapshot_count"] == 1
    assert detail["stars_7d"] is None  # null, never zero
    # ``new`` is a trend class of its own, not a null.
    assert detail["trend"]["trend"] == "new"
    assert detail["confidence"]["level"] == "LOW"


async def test_unknown_repository_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/repositories/octo/absent")
    assert response.status_code == 404
    assert _error_shape(response.json()) == "not_found"


async def test_topics_list_and_detail(client: httpx.AsyncClient) -> None:
    listed = await client.get("/api/v1/topics")
    body = listed.json()
    assert listed.status_code == 200
    names = {entry["name"] for entry in body["items"]}
    assert {"mcp", "ai", "agency"} <= names

    detail = await client.get("/api/v1/topics/mcp")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["name"] == "mcp"
    assert payload["repository_count"] == 2
    assert payload["aggregate"]["repository_count"] == 2
    assert len(payload["repositories"]) == 2
    assert payload["graph"] is not None

    missing = await client.get("/api/v1/topics/uncertain")
    assert missing.status_code == 404


async def test_search_substring(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/search", params={"q": "octo/alp"})
    assert response.status_code == 200
    body = response.json()
    assert [item["full_name"] for item in body["repositories"]] == ["octo/alpha"]

    topics = await client.get("/api/v1/search", params={"q": "mc"})
    assert any(t["name"] == "mcp" for t in topics.json()["topics"])

    blank = await client.get("/api/v1/search", params={"q": "  "})
    assert blank.status_code == 400
    assert _error_shape(blank.json()) == "bad_request"


async def test_ecosystem_projects_nodes_and_edges(client: httpx.AsyncClient) -> None:
    report = await client.get("/api/v1/ecosystem")
    assert report.status_code == 200
    body = report.json()
    assert body["repository_count"] == 3
    assert body["developer_count"] == 3  # owner octo + contributors alice, bob
    assert body["topic_count"] == 3  # mcp, ai, agency
    assert body["node_counts"]["developers"] == 3
    assert body["node_counts"]["topics"] == 3
    assert body["edge_counts"]["owns"] == 3
    assert body["edge_counts"]["contributes_to"] == 3
    assert body["edge_counts"]["tagged_with"] == 4  # mcp, ai + mcp + agency

    nodes = await client.get(
        "/api/v1/ecosystem/nodes", params={"node_type": "REPOSITORY"}
    )
    body = nodes.json()
    assert body["total"] == 3
    assert all(entry["node_type"] == "REPOSITORY" for entry in body["items"])

    filtered_edges = await client.get(
        "/api/v1/ecosystem/edges", params={"edge_type": "owns"}
    )
    assert filtered_edges.status_code == 200
    assert filtered_edges.json()["total"] == 3


async def test_developers_list_and_filters(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/developers")
    body = response.json()
    assert response.status_code == 200
    logins = {entry["login"] for entry in body["items"]}
    assert {"alice", "bob"} <= logins

    by_topic = await client.get("/api/v1/developers", params={"topic": "mcp"})
    logins = {entry["login"] for entry in by_topic.json()["items"]}
    assert {"alice", "bob"} <= logins

    min_followers = await client.get(
        "/api/v1/developers", params={"min_followers": 1}
    )
    assert min_followers.status_code == 200

    unknown_topic = await client.get("/api/v1/developers", params={"topic": "zzz"})
    assert unknown_topic.status_code == 200
    assert unknown_topic.json()["items"] == []


async def test_developer_emerging_and_detail(client: httpx.AsyncClient) -> None:
    emerging = await client.get("/api/v1/developers/emerging")
    assert emerging.status_code == 200
    assert isinstance(emerging.json()["items"], list)

    detail = await client.get("/api/v1/developers/alice")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["login"] == "alice"
    assert payload["public_contacts"]["available"] == ["github"]  # github is always available

    missing = await client.get("/api/v1/developers/nobody-real")
    assert missing.status_code == 404


async def test_bridges_endpoints(client: httpx.AsyncClient) -> None:
    bridges = await client.get("/api/v1/bridges")
    assert bridges.status_code == 200
    assert isinstance(bridges.json()["items"], list)

    repos = await client.get("/api/v1/bridges/repos")
    assert repos.status_code == 200

    cross = await client.get(
        "/api/v1/bridges/cross-topic",
        params={"topic_a": "mcp", "topic_b": "ai", "window": "7d"},
    )
    assert cross.status_code == 200


async def test_ai_status_unconfigured_and_reads_are_free(
    client: httpx.AsyncClient,
) -> None:
    status = await client.get("/api/v1/ai/status")
    assert status.status_code == 200
    payload = status.json()
    assert payload["configured"] is False
    assert isinstance(payload["cached_artifacts"], int)

    artifacts = await client.get("/api/v1/ai/artifacts")
    assert artifacts.status_code == 200
    assert artifacts.json()["items"] == []


async def test_ai_generate_refuses_when_unconfigured(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/ai/generate",
        params={"entity_type": "repository", "entity_key": "octo/alpha"},
    )
    assert response.status_code == 503
    assert _error_shape(response.json()) == "ai_configuration_error"


async def test_spa_fallback_serves_built_index(tmp_path: Path, engine: AsyncEngine) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>built</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    factory = make_session_factory(engine)
    app = create_app(
        settings=TEST_SETTINGS,
        session_factory=factory,
        web_dist=dist,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        root = await http.get("/")
        assert root.status_code == 200
        assert root.text == "<html>built</html>"

        route = await http.get("/repositories/some-client-route")
        assert route.status_code == 200
        assert route.text == "<html>built</html>"

        asset = await http.get("/assets/app.js")
        assert asset.status_code == 200
        assert asset.text == "console.log(1)"

        api404 = await http.get("/api/v1/nope")
        assert api404.status_code == 404
        assert _error_shape(api404.json()) == "not_found"


async def test_spa_fallback_placeholder_when_unbuilt(
    tmp_path: Path, engine: AsyncEngine
) -> None:
    factory = make_session_factory(engine)
    empty = tmp_path / "missing"
    app = create_app(settings=TEST_SETTINGS, session_factory=factory, web_dist=empty)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/")
        assert response.status_code == 503
        assert "github-radar" in response.text
        assert "/api/v1/docs" in response.text


async def test_validation_error_uses_uniform_envelope(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/search", params={"q": "x" * 500})
    assert response.status_code == 422
    assert _error_shape(response.json()) == "validation_error"


# ---------------------------------------------------------------------------
# Real-data acceptance regressions
# ---------------------------------------------------------------------------


async def test_explore_distinguishes_tracked_from_momentum_eligible(
    client: httpx.AsyncClient,
) -> None:
    """The explore KPI must not conflate tracked repos with momentum-eligible ones."""
    response = await client.get("/api/v1/explore", params={"limit": 10})
    assert response.status_code == 200
    body = response.json()
    assert body["tracked_repositories"] == 3
    assert body["repositories_with_momentum"] == 2  # alpha + beta; gamma has one snapshot
    assert len(body["repositories"]) == 2
    for row in body["repositories"]:
        assert "trend" in row
        assert row["stale_days"] == 0
    assert body["freshness"]["stale_days"] == 0
    # Topic aggregates: a topic with no momentum must be null, never 0.00.
    by_name = {topic["name"]: topic for topic in body["topics"]}
    assert by_name["mcp"]["avg_momentum"] is not None
    assert by_name["agency"]["share_with_momentum"] == 0.0
    assert by_name["agency"]["avg_momentum"] is None
    assert by_name["agency"]["total_momentum"] is None


async def test_topic_detail_includes_members_without_momentum(
    client: httpx.AsyncClient,
) -> None:
    """Every topic member is listed; a member without momentum is null, not absent."""
    detail = await client.get("/api/v1/topics/agency")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["repository_count"] == 1
    assert len(payload["repositories"]) == 1
    row = payload["repositories"][0]
    assert row["full_name"] == "octo/gamma"
    assert row["score"] is None
    assert row["stars_growth_pct"] is None
    assert row["momentum"] is None
    assert row["trend"]["trend"] == "new"
    assert payload["aggregate"]["avg_momentum"] is None
    assert payload["aggregate"]["total_momentum"] is None
    assert payload["aggregate"]["share_with_momentum"] == 0.0


async def test_topic_with_momentum_keeps_real_averages(
    client: httpx.AsyncClient,
) -> None:
    detail = await client.get("/api/v1/topics/mcp")
    payload = detail.json()
    assert payload["repository_count"] == 2
    assert len(payload["repositories"]) == 2
    assert payload["aggregate"]["share_with_momentum"] == 1.0
    assert payload["aggregate"]["avg_momentum"] is not None
    assert all(row["score"] is not None for row in payload["repositories"])


async def test_repositories_sort_and_filters(client: httpx.AsyncClient) -> None:
    by_stars = await client.get(
        "/api/v1/repositories", params={"sort": "stars", "order": "desc"}
    )
    names = [item["full_name"] for item in by_stars.json()["items"]]
    assert names == ["octo/beta", "octo/alpha", "octo/gamma"]

    ascending = await client.get(
        "/api/v1/repositories", params={"sort": "stars", "order": "asc"}
    )
    assert [item["full_name"] for item in ascending.json()["items"]] == [
        "octo/gamma",
        "octo/alpha",
        "octo/beta",
    ]

    by_name = await client.get("/api/v1/repositories", params={"sort": "name", "order": "asc"})
    assert [item["full_name"] for item in by_name.json()["items"]] == [
        "octo/alpha",
        "octo/beta",
        "octo/gamma",
    ]

    by_momentum = await client.get("/api/v1/repositories", params={"sort": "momentum"})
    assert by_momentum.json()["items"][0]["full_name"] == "octo/alpha"

    trend_new = await client.get("/api/v1/repositories", params={"trend": "new"})
    assert trend_new.json()["total"] == 1
    assert trend_new.json()["items"][0]["full_name"] == "octo/gamma"

    language_python = await client.get("/api/v1/repositories", params={"language": "python"})
    assert language_python.json()["total"] == 3
    language_go = await client.get("/api/v1/repositories", params={"language": "go"})
    assert language_go.json()["total"] == 0

    min_stars = await client.get("/api/v1/repositories", params={"min_stars": 16})
    assert [item["full_name"] for item in min_stars.json()["items"]] == ["octo/beta"]

    archived_only = await client.get("/api/v1/repositories", params={"archived": "true"})
    assert archived_only.json()["total"] == 0
    non_archived = await client.get("/api/v1/repositories", params={"archived": "false"})
    assert non_archived.json()["total"] == 3


async def test_repositories_rejects_invalid_sort_filters(
    client: httpx.AsyncClient,
) -> None:
    for params, code in (
        ({"sort": "size"}, "bad_request"),
        ({"order": "sideways"}, "bad_request"),
        ({"trend": "surprising"}, "bad_request"),
    ):
        response = await client.get("/api/v1/repositories", params=params)
        assert response.status_code == 400
        assert _error_shape(response.json()) == code


async def test_graph_nodes_carry_centrality_scores(client: httpx.AsyncClient) -> None:
    nodes = await client.get(
        "/api/v1/ecosystem/nodes", params={"node_type": "REPOSITORY"}
    )
    body = nodes.json()
    assert body["total"] == 3
    items = body["items"]
    assert all("degree" in item and "weighted_degree" in item for item in items)
    # Deterministic ordering: weighted degree descending (then degree, label).
    weighted = [item["weighted_degree"] for item in items]
    assert weighted == sorted(weighted, reverse=True)
    assert all(item["degree"] >= 0 for item in items)

    all_nodes = await client.get("/api/v1/ecosystem/nodes")
    for item in all_nodes.json()["items"]:
        assert set(item) >= {"node_type", "node_id", "label", "degree", "weighted_degree"}


async def test_freshness_and_stale_days_reflect_wall_clock(
    client_stale: httpx.AsyncClient,
) -> None:
    status = await client_stale.get("/api/v1/status")
    payload = status.json()
    assert payload["freshness"]["stale_days"] >= 39
    assert payload["freshness"]["newest_observation_at"] is not None

    explore = await client_stale.get("/api/v1/explore")
    body = explore.json()
    assert body["tracked_repositories"] == 2
    assert body["repositories_with_momentum"] == 1  # only zeta has a window

    listed = await client_stale.get("/api/v1/repositories")
    rows = listed.json()["items"]
    delta = next(item for item in rows if item["full_name"] == "octo/delta")
    assert delta["stale_days"] >= 39
    assert delta["momentum"] is None

    detail = await client_stale.get("/api/v1/repositories/octo/delta")
    assert detail.json()["stale_days"] >= 39
    assert detail.json()["stars_7d"] is None


async def test_zero_growth_stays_distinct_from_unavailable(
    client_stale: httpx.AsyncClient,
) -> None:
    """A computed ``stars_growth_pct == 0`` is not the same as 'no data'."""
    zeta = await client_stale.get("/api/v1/repositories/octo/zeta")
    payload = zeta.json()
    assert payload["stars_7d"] == 0  # a real value, not null
    assert payload["momentum"] is not None
    assert payload["momentum"]["stars_growth_pct"] == 0.0
    assert payload["momentum"]["score"] > 0  # forks + recency still contribute