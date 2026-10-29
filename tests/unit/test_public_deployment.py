"""Public access is explicit; rejected requests cannot touch storage or providers."""

from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import ValidationError

from github_radar.api.app import create_app
from github_radar.api.deployment import PUBLIC_READ_PATHS
from github_radar.api.readiness import database_ready
from github_radar.config import Settings
from tests.unit.test_workspace import AUTH, TOKEN, workspace_settings


def public_settings(**overrides):
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        deployment_mode="public",
        public_allowed_hosts=("radar.test",),
        **overrides,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"github_token": "secret"},
        {"ai_api_key": "secret"},
        {"workspace_admin_token": TOKEN},
    ],
)
def test_public_rejects_credentials(overrides):
    with pytest.raises(ValidationError, match="must not contain") as error:
        public_settings(**overrides)
    assert "secret" not in str(error.value) and TOKEN not in str(error.value)


@pytest.mark.parametrize("hosts", [(), ("*",), ("*.example.com",), ("radar.test:80",)])
def test_public_requires_exact_hosts(hosts):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, deployment_mode="public", public_allowed_hosts=hosts)  # type: ignore[call-arg]


def test_credentials_not_in_repr_or_validation_error():
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        github_token=TOKEN,
        ai_api_key=TOKEN,
        database_url=f"postgresql+asyncpg://user:{TOKEN}@localhost/db",
    )
    assert TOKEN not in repr(settings)
    with pytest.raises(ValidationError) as error:
        Settings(  # type: ignore[call-arg]
            _env_file=None, database_url=f"invalid://user:{TOKEN}@localhost/db"
        )
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize(
    "method", ["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE"]
)
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/ai/generate",
        "/api/v1/ai/status",
        "/api/v1/ai/artifacts",
        "/api/v1/ai/repository/octo/alpha",
        "/api/v1/workspace/session",
        "/api/v1/workspace/repositories",
        "/api/v1/workspace/developers/1",
        "/api/v1/workspace/repositories/1",
        "/api/v1/docs",
        "/api/v1/openapi.json",
        "/redoc",
        "/workspace",
        "/ai",
        "/api/v1/unreviewed",
        "/api/v1/%77orkspace/session",
    ],
)
async def test_public_forbidden_before_database(method, path):
    factory = Mock(side_effect=AssertionError("database reached"))
    # Even accidentally injected admin configuration must not open public access.
    settings = public_settings().model_copy(
        update={"workspace_admin_token": workspace_settings().workspace_admin_token}
    )
    app = create_app(settings, session_factory=factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://radar.test"
    ) as client:
        response = await client.request(
            method,
            path,
            headers={**AUTH, "X-Forwarded-For": "127.0.0.1", "Origin": "http://radar.test"},
        )
    assert response.status_code == (404 if method in {"GET", "HEAD"} else 405)
    assert response.headers["Cache-Control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers
    factory.assert_not_called()


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.test", "X-Forwarded-Host": "radar.test"},
        {"Host": "radar.test.evil"},
        {"Host": "user@radar.test"},
        {"Host": " radar.test"},
        {"Host": "radar.test\t"},
        [("Host", "radar.test"), ("Host", "radar.test")],
    ],
)
async def test_public_host_not_forwarded_or_ambiguous(headers):
    factory = Mock()
    app = create_app(public_settings(), session_factory=factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://radar.test"
    ) as client:
        response = await client.get("/api/v1/health", headers=headers)
    assert response.status_code == 400
    factory.assert_not_called()


async def test_public_health_capabilities_and_no_cors():
    factory = Mock()
    app = create_app(public_settings(), session_factory=factory)
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://radar.test"
    ) as client:
        response = await client.get("/api/v1/health", headers={"Origin": "https://evil.test"})
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers
        assert (await client.get("/api/v1/capabilities")).json() == {"mode": "public"}
        assert (await client.post("/api/v1/health")).status_code == 405
    factory.assert_not_called()


def test_every_api_route_has_explicit_classification():
    app = create_app(workspace_settings(), session_factory=Mock())
    reads = set()
    for path, operations in app.openapi()["paths"].items():
        methods = {method.upper() for method in operations}
        if path.startswith("/api/v1/workspace/"):
            assert methods <= {"GET", "PUT", "DELETE"}
        elif path.startswith("/api/v1/ai/"):
            assert methods == ({"POST"} if path.endswith("/generate") else {"GET"})
        else:
            assert methods == {"GET"}
            reads.add(path)
    assert reads == {path.replace(":path", "") for path in PUBLIC_READ_PATHS}


@pytest.mark.parametrize(
    ("peer", "headers", "expected"),
    [
        ("127.0.0.1", {}, 401),
        ("127.0.0.1", {"Authorization": "Bearer wrong"}, 401),
        ("203.0.113.2", AUTH, 403),
        ("127.0.0.1", {**AUTH, "Forwarded": "for=127.0.0.1"}, 403),
        ("127.0.0.1", {**AUTH, "Origin": "https://evil.test"}, 403),
    ],
)
async def test_local_ai_requires_existing_admin_boundary(peer, headers, expected):
    factory = Mock(side_effect=AssertionError("database reached"))
    app = create_app(workspace_settings(), session_factory=factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(peer, 123)),
        base_url="http://localhost",
    ) as client:
        response = await client.post("/api/v1/ai/generate", headers=headers)
    assert response.status_code == expected
    factory.assert_not_called()


async def test_root_path_cannot_bypass_admin_boundary():
    factory = Mock()
    app = create_app(workspace_settings(), session_factory=factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, root_path="/prefix"), base_url="http://localhost"
    ) as client:
        response = await client.post("/prefix/api/v1/ai/generate")
    assert response.status_code == 401
    factory.assert_not_called()


async def test_database_failure_is_generic_and_not_logged(caplog):
    secret = "PRIVATE_NOTE_AND_DATABASE_PASSWORD"
    factory = Mock(side_effect=RuntimeError(secret))
    app = create_app(public_settings(), session_factory=factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://radar.test"
    ) as client:
        response = await client.get("/api/v1/repositories")
        ready = await client.get("/api/v1/ready")
    assert response.status_code == 500 and ready.status_code == 503
    assert secret not in response.text + ready.text + caplog.text
    assert "internal details suppressed" in caplog.text


async def test_lifespan_requires_frontend_and_database(tmp_path, monkeypatch):
    factory = Mock()
    app = create_app(public_settings(), session_factory=factory, web_dist=tmp_path)
    with pytest.raises(RuntimeError, match="built frontend"):
        async with app.router.lifespan_context(app):
            pytest.fail("public startup succeeded without frontend")
    (tmp_path / "index.html").write_text("built")
    probe = AsyncMock(return_value=False)
    monkeypatch.setattr("github_radar.api.app.database_ready", probe)
    with pytest.raises(RuntimeError, match="readiness failed"):
        async with app.router.lifespan_context(app):
            pytest.fail("public startup succeeded without database")
    probe.return_value = True
    async with app.router.lifespan_context(app):
        pass
    local = create_app(workspace_settings(), session_factory=factory, web_dist=tmp_path)
    async with local.router.lifespan_context(local):
        pass


async def test_readiness_schema_version_and_failure():
    session = AsyncMock()
    factory = Mock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=session)))
    session.scalars.return_value = Mock(all=Mock(return_value=["0006"]))
    assert await database_ready(factory, public=False)
    session.scalars.return_value.all.return_value = ["0005"]
    assert not await database_ready(factory, public=False)
    session.scalars.side_effect = RuntimeError("connection secret")
    assert not await database_ready(factory, public=False)


@pytest.mark.parametrize("readonly, allowed", [("off", True), ("on", False), ("on", True)])
async def test_public_readiness_requires_readonly_and_role_audit(readonly, allowed, monkeypatch):
    session = AsyncMock()
    factory = Mock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=session)))
    session.scalars.return_value = Mock(all=Mock(return_value=["0006"]))
    session.scalar.return_value = readonly
    audit = AsyncMock(side_effect=None if allowed else ValueError("unsafe grants"))
    monkeypatch.setattr("github_radar.api.readiness.audit_application_role", audit)
    assert await database_ready(factory, public=True) == (readonly == "on" and allowed)
    assert audit.await_count == (1 if readonly == "on" else 0)


async def test_public_reads_never_include_private_state():
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from github_radar.storage.db import make_session_factory
    from github_radar.storage.models import Base
    from tests.unit.test_api_routes import _seed

    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = make_session_factory(engine)
        async with factory() as session:
            await _seed(session)
        local = create_app(workspace_settings(), session_factory=factory)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=local), base_url="http://localhost", headers=AUTH
        ) as client:
            for kind in ("developers", "repositories"):
                entity = (await client.get(f"/api/v1/workspace/{kind}?saved=all")).json()["items"][
                    0
                ]
                response = await client.put(
                    f"/api/v1/workspace/{kind}/{entity['id']}",
                    json={
                        "saved": True,
                        "notes": "PRIVATE_NOTE_SENTINEL",
                        "relationship_status": "connected",
                        "version": None,
                    },
                )
                assert response.status_code == 200
        public = create_app(public_settings(), session_factory=factory)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=public), base_url="http://radar.test"
        ) as client:
            for route in PUBLIC_READ_PATHS - {"/api/v1/ready"}:
                path = route.replace("{ref:path}", "octo/alpha").replace("{login}", "alice")
                path = path.replace("{name}", "mcp")
                response = await client.get(
                    path, params={"q": "octo", "topic_a": "mcp", "topic_b": "ai"}
                )
                assert response.status_code == 200, (path, response.text)
                assert "PRIVATE_NOTE_SENTINEL" not in response.text
                assert TOKEN not in response.text and '"workspace"' not in response.text
    finally:
        await engine.dispose()


def test_launcher_requires_explicit_public_mode_and_disables_proxy_and_access_logs(monkeypatch):
    import importlib

    from typer.testing import CliRunner

    cli = importlib.import_module("github_radar.cli.app")
    settings = workspace_settings().model_copy(
        update={
            "database_url": "postgresql+asyncpg://unused@localhost/db",
        }
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    run = Mock()
    monkeypatch.setattr("uvicorn.run", run)
    runner = CliRunner()
    assert runner.invoke(cli.app, ["serve", "--host", "0.0.0.0"]).exit_code == 1
    run.assert_not_called()
    settings = public_settings(database_url="postgresql+asyncpg://unused@localhost/db")
    assert runner.invoke(cli.app, ["serve", "--host", "0.0.0.0"]).exit_code == 0
    assert run.call_args.kwargs["proxy_headers"] is False
    assert run.call_args.kwargs["access_log"] is False
    assert run.call_args.kwargs["limit_concurrency"] == 20


async def test_public_websocket_rejected():
    from github_radar.api.deployment import DeploymentBoundary

    inner, send, receive = AsyncMock(), AsyncMock(), AsyncMock()
    await DeploymentBoundary(inner, public=True, hosts=("radar.test",))(
        {"type": "websocket"},
        receive,
        send,
    )
    send.assert_awaited_once_with({"type": "websocket.close", "code": 1008})
    inner.assert_not_awaited()


def test_invalid_database_port_does_not_leak_secret():
    with pytest.raises(ValidationError) as error:
        Settings(  # type: ignore[call-arg]
            _env_file=None, database_url=f"postgresql+asyncpg://user@localhost:{TOKEN}/db"
        )
    assert TOKEN not in str(error.value)
