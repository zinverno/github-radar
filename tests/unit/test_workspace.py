"""Private data is protected before body parsing or database access."""

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from github_radar.api.app import create_app
from github_radar.api.routes.workspace import contact_links
from github_radar.config import Settings
from github_radar.domain import PublicContactMethods
from github_radar.storage.db import make_session_factory
from github_radar.storage.models import Base
from tests.unit.test_api_routes import _seed

TOKEN = "test_admin_token_" + "a" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PREFIX = "/api/v1/workspace"


def workspace_settings(token: str = TOKEN) -> Settings:
    return Settings(_env_file=None, workspace_admin_token=SecretStr(token))  # type: ignore[call-arg]


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = make_session_factory(engine)
    async with factory() as session:
        await _seed(session)
    app = create_app(workspace_settings(), session_factory=factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1",
        headers=AUTH,
    ) as client:
        yield client
    await engine.dispose()


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "POST", "PATCH", "HEAD", "OPTIONS"])
@pytest.mark.parametrize("path", [
    "/developers", "/developers/1", "/repositories", "/repositories/1", "/session", "/unknown",
])
async def test_anonymous_never_reaches_database(method, path):
    factory = Mock(side_effect=AssertionError("unauthorized database access"))
    app = create_app(workspace_settings(), session_factory=factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
    ) as client:
        response = await client.request(method, PREFIX + path, content=b"invalid JSON")
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["WWW-Authenticate"] == "Bearer"
    factory.assert_not_called()


@pytest.mark.parametrize(
    ("token", "peer", "host", "headers", "expected"),
    [
        ("", "127.0.0.1", "localhost", AUTH, 503),
        (TOKEN, "203.0.113.2", "localhost", AUTH, 403),
        (TOKEN, "127.0.0.1", "attacker.example", AUTH, 403),
        (TOKEN, "127.0.0.1", "localhost.evil", AUTH, 403),
        (TOKEN, "127.0.0.1", "localhost", {**AUTH, "X-Forwarded-For": "127.0.0.1"}, 403),
        (TOKEN, "127.0.0.1", "localhost", {**AUTH, "Forwarded": "for=127.0.0.1"}, 403),
        (TOKEN, "127.0.0.1", "localhost", {**AUTH, "X-Real-IP": "127.0.0.1"}, 403),
        (TOKEN, "127.0.0.1", "localhost", {**AUTH, "Origin": "https://evil.example"}, 403),
        (TOKEN, "127.0.0.1", "localhost", {**AUTH, "Origin": "null"}, 403),
        (TOKEN, "127.0.0.1", "localhost", {**AUTH, "Sec-Fetch-Site": "cross-site"}, 403),
        (TOKEN, "127.0.0.1", "localhost", {"Authorization": "Bearer wrong"}, 401),
        (TOKEN, "127.0.0.1", "localhost", {"Authorization": "Basic wrong"}, 401),
        (TOKEN, "::1", "[::1]", AUTH, 200),
        (TOKEN, "127.0.0.1", "localhost", {**AUTH, "Origin": "http://localhost"}, 200),
    ],
)
async def test_access_boundary(token, peer, host, headers, expected):
    factory = Mock(side_effect=AssertionError("session probe must not use DB"))
    app = create_app(workspace_settings(token), session_factory=factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(peer, 1234)),
        base_url=f"http://{host}",
    ) as client:
        response = await client.get(PREFIX + "/session", headers=headers)
    assert response.status_code == expected
    assert response.headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in response.headers
    factory.assert_not_called()


async def exercise_workspace(client: httpx.AsyncClient) -> None:
    developers = (await client.get(PREFIX + "/developers?saved=all&topic=mcp")).json()
    developer = developers["items"][0]
    path = PREFIX + f"/developers/{developer['id']}"
    edit = {
        "saved": True,
        "notes": "PRIVATE_SENTINEL <script>alert(1)</script>",
        "relationship_status": "connected",
        "version": None,
    }
    response = await client.put(path, json=edit)
    assert response.status_code == 200, response.text
    version = response.json()["version"]
    assert response.json()["notes"] == edit["notes"]
    assert response.headers["Cache-Control"] == "no-store"
    assert (await client.put(path, json=edit)).status_code == 409
    saved = (await client.get(PREFIX + "/developers?status=connected&topic=mcp")).json()
    assert saved["total"] == 1
    assert saved["items"][0]["workspace"]["notes"] == edit["notes"]
    assert saved["items"][0]["emerging"] == developer["emerging"]
    public = (await client.get("/api/v1/developers?topic=mcp")).json()
    public_dev = next(d for d in public["items"] if d["login"] == developer["login"])
    for field in ("activity", "ecosystem", "emerging", "relevance"):
        assert saved["items"][0][field] == public_dev[field]
    assert (await client.get(PREFIX + "/developers?topic=absent")).json()["total"] == 0
    assert (await client.get(PREFIX + "/developers?status=paused")).json()["total"] == 0
    for url in (
        "/developers",
        "/repositories",
        "/topics",
        "/explore",
        "/search?q=PRIVATE_SENTINEL",
    ):
        response = await client.get("/api/v1" + url, headers={"Authorization": ""})
        assert response.status_code == 200, response.text
        assert "PRIVATE_SENTINEL" not in response.text and '"workspace"' not in response.text
    unsaved = await client.put(path, json={**edit, "saved": False, "version": version})
    assert unsaved.status_code == 200
    assert (await client.get(PREFIX + "/developers")).json()["total"] == 0
    retained = (await client.get(PREFIX + "/developers?saved=unsaved&status=connected")).json()
    assert retained["items"][0]["workspace"]["notes"] == edit["notes"]
    assert (await client.delete(path, params={"version": version})).status_code == 409
    assert (
        await client.delete(path, params={"version": unsaved.json()["version"]})
    ).status_code == 200
    recreated = await client.put(path, json=edit)
    assert recreated.status_code == 200 and recreated.json()["version"] != version
    assert (await client.put(path, json={**edit, "version": version})).status_code == 409
    repos = (await client.get(PREFIX + "/repositories?saved=all&topic=mcp&limit=1")).json()
    assert repos["total"] == 2 and repos["has_more"]
    repo = repos["items"][0]
    repo_path = PREFIX + f"/repositories/{repo['id']}"
    result = await client.put(repo_path, json=edit)
    assert result.status_code == 200
    assert (await client.get(PREFIX + "/repositories?topic=mcp")).json()["total"] == 1
    for url in (
        "/status", "/ai/status", "/ai/artifacts", "/developers",
        f"/developers/{developer['login']}", "/developers/emerging", "/developers/high-activity",
        "/repositories", f"/repositories/{repo['full_name']}", "/repositories/summary",
        "/topics", "/topics/mcp", "/explore", "/search?q=PRIVATE_SENTINEL",
        "/ecosystem", "/ecosystem/nodes", "/ecosystem/edges", "/bridges", "/bridges/repos",
    ):
        public = await client.get("/api/v1" + url, headers={"Authorization": ""})
        assert public.status_code == 200, (url, public.text)
        assert "PRIVATE_SENTINEL" not in public.text and '"workspace"' not in public.text
        assert TOKEN not in public.text
    assert (
        await client.delete(repo_path, params={"version": result.json()["version"]})
    ).status_code == 200
    assert (await client.get(PREFIX + "/repositories?saved=all")).json()["total"] == 3


async def test_workspace_lifecycle(client):
    await exercise_workspace(client)


@pytest.mark.parametrize(
    "change",
    [
        {"notes": "x" * 10001},
        {"notes": "bad\x00note"},
        {"notes": None},
        {"relationship_status": "unknown"},
        {"saved": "true"},
        {"admin": True},
        {"version": "invalid"},
    ],
)
async def test_validation(client, change):
    repo = (await client.get(PREFIX + "/repositories?saved=all")).json()["items"][0]
    body = {"saved": True, "notes": "", "relationship_status": "interested", "version": None}
    response = await client.put(PREFIX + f"/repositories/{repo['id']}", json={**body, **change})
    assert response.status_code == 422
    assert (await client.get(PREFIX + "/repositories")).json()["total"] == 0


async def test_pagination_and_filters(client):
    for query in (
        "limit=0",
        "offset=-1",
        "saved=invalid",
        "status=invalid",
        "min_relevance=nan",
        "min_relevance=1.1",
        "window=4d",
    ):
        response = await client.get(PREFIX + "/developers?" + query)
        assert response.status_code in (400, 422)
    rows = (await client.get(PREFIX + "/developers?saved=all&min_relevance=0.8")).json()
    assert all(d["relevance"] is not None and d["relevance"] >= 0.8 for d in rows["items"])
    body = {"saved": True, "notes": "", "relationship_status": "interested", "version": None}
    assert (await client.put(PREFIX + "/developers/999999", json=body)).status_code == 404


def test_contacts_reject_executable_urls():
    contacts = PublicContactMethods(
        github="javascript:alert(1)",
        website="javascript:alert(1)",
        twitter="bad/name",
        public_email="published@example.test",
    )
    assert contact_links("octo", contacts) == [
        {"label": "GitHub profile", "url": "https://github.com/octo"},
    ]


@pytest.mark.parametrize("token", ["weak", "bad token " * 6, "é" * 40])
def test_invalid_token_configuration_fails_without_exposing_value(token):
    with pytest.raises(ValueError) as exc:
        workspace_settings(token)
    assert token not in str(exc.value)


@pytest.mark.parametrize(
    "path", ["/api/v1/workspace", "/api/v1/workspace/", "/api/v1/%77orkspace/developers"]
)
async def test_namespace_edges_are_protected(path):
    factory = Mock(side_effect=AssertionError("unauthorized database access"))
    app = create_app(workspace_settings(), session_factory=factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
        cookies={"admin_token": TOKEN},
    ) as client:
        response = await client.get(path + "?token=" + TOKEN)
    assert response.status_code == 401
    factory.assert_not_called()


async def test_conflicting_headers_and_missing_peer_are_rejected():
    from github_radar.api.workspace_access import WorkspaceAccess

    app = create_app(workspace_settings(), session_factory=Mock())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        for headers in (
            [("Authorization", "Bearer " + TOKEN), ("Authorization", "Bearer wrong")],
            [*AUTH.items(), ("Origin", "http://localhost"), ("Origin", "https://evil.test")],
            [*AUTH.items(), ("Host", "localhost"), ("Host", "evil.test")],
        ):
            assert (await client.get(PREFIX + "/session", headers=headers)).status_code in (
                401,
                403,
            )
    boundary = WorkspaceAccess(app, TOKEN)
    assert boundary.rejection({"type": "http", "client": None, "headers": []}) is not None


def test_cli_refuses_remote_bind_and_disables_forwarded_header_trust(monkeypatch):
    import importlib

    from typer.testing import CliRunner

    cli = importlib.import_module("github_radar.cli.app")
    settings = workspace_settings()
    settings.database_url = "postgresql+asyncpg://unused:unused@localhost/test_radar"
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    run = Mock()
    monkeypatch.setattr("uvicorn.run", run)
    rejected = CliRunner().invoke(cli.app, ["serve", "--host", "0.0.0.0"])
    assert rejected.exit_code == 1
    run.assert_not_called()
    accepted = CliRunner().invoke(cli.app, ["serve"])
    assert accepted.exit_code == 0
    assert run.call_args.kwargs["proxy_headers"] is False


async def test_unexpected_errors_do_not_expose_notes_or_cache_response():
    factory = Mock(side_effect=RuntimeError("private exception text"))
    app = create_app(workspace_settings(), session_factory=factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://localhost",
        headers=AUTH,
    ) as client:
        response = await client.get(PREFIX + "/developers")
    assert response.status_code == 500
    assert response.headers["Cache-Control"] == "no-store"
    assert "private exception text" not in response.text


async def test_shell_is_empty_of_private_data_and_uncacheable(tmp_path: Path):
    (tmp_path / "index.html").write_text("<div id='root'></div>")
    app = create_app(workspace_settings(), session_factory=Mock(), web_dist=tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
    ) as client:
        response = await client.get("/workspace/developers")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


async def test_disabled_default_guards_every_registered_private_endpoint(monkeypatch):
    monkeypatch.delenv("WORKSPACE_ADMIN_TOKEN", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.workspace_admin_token.get_secret_value() == ""
    factory = Mock(side_effect=AssertionError("disabled workspace accessed database"))
    app = create_app(settings, session_factory=factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=AUTH,
    ) as client:
        paths = app.openapi()["paths"]
        private_paths = {
            path: methods for path, methods in paths.items() if path.startswith(PREFIX)
        }
        assert len(private_paths) == 4
        for registered, methods in private_paths.items():
            for kind in ("developers", "repositories"):
                path = registered.replace("{kind}", kind).replace("{entity_id}", "1")
                for method in methods:
                    result = await client.request(method, path, content=b"invalid JSON")
                    assert result.status_code == 503, (method, path)
                    assert result.json()["error"]["code"] == "workspace_disabled"
    factory.assert_not_called()
