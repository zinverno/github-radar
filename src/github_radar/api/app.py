"""FastAPI application factory for the web API.

The API is mounted under ``/api/v1`` with its OpenAPI schema at
``/api/v1/openapi.json`` (docs at ``/api/v1/docs``). Tests build the app with
an explicit ``session_factory`` over their own sqlite engine; ``serve`` builds
it with the real PostgreSQL engine from settings.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from github_radar.api.deployment import DeploymentBoundary
from github_radar.api.errors import register_exception_handlers
from github_radar.api.readiness import database_ready
from github_radar.api.routes import api_router
from github_radar.api.workspace_access import PRIVATE_HEADERS, WorkspaceAccess
from github_radar.config import Settings
from github_radar.storage.db import make_engine, make_session_factory

logger = logging.getLogger(__name__)

_DEFAULT_WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"

_DAY0_CSS = """
html{color-scheme:dark}
body{margin:0;font:14px/1.5 system-ui,sans-serif;background:#0f1115;color:#e6e6e6;
padding:32px 20px;display:flex;justify-content:center}
main{width:min(880px,100%)}
h1{font-size:20px;margin:0 0 4px}
p{margin:6px 0}
code{background:#1c212b;border-radius:4px;padding:1px 6px}
a{color:#7aa2f7}
"""

_SPA_BAD_API_NOTE = (
    "\n\n(If you were hitting an API route: the dataset that backs the web UI is "
    "not installed yet. Track repositories with `github-radar discover`, load "
    "history with `github-radar update`, then start `github-radar serve`.)"
)


def _spa_fallback(dist_dir: Path) -> Any:
    """Return the SPA fallback endpoint for ``dist_dir``.

    Serves built assets when they exist, ``index.html`` for client-side routes,
    a day-0 placeholder when the frontend is not built, and JSON 404s for any
    unmatched ``/api/*`` path so API clients never receive HTML.
    """
    index_path = dist_dir / "index.html"

    def fallback(request: Request) -> Any:
        path = request.url.path.lstrip("/")
        if path in ("api", "api/") or path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "code": "not_found",
                        "message": "Unknown API route under /api/.",
                        "details": {"path": request.url.path},
                    }
                },
            )
        candidate = (dist_dir / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist_dir):
            return FileResponse(
                candidate, headers=PRIVATE_HEADERS if candidate.suffix == ".html" else None,
            )
        if index_path.is_file():
            return FileResponse(index_path, headers=PRIVATE_HEADERS)
        return HTMLResponse(_day0_placeholder(), status_code=503, headers=PRIVATE_HEADERS)

    return fallback


def _day0_placeholder() -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>github-radar</title><style>{_DAY0_CSS}</style></head><body><main>"
        "<h1>github-radar</h1>"
        "<p>The API is running, but the web frontend has not been built yet.</p>"
        "<p>Run <code>npm run build</code> inside <code>web/</code> (or "
        "<code>github-radar serve</code> now to keep using the API).</p>"
        "<p>API docs: <a href='/api/v1/docs'>/api/v1/docs</a> · "
        "OpenAPI: <a href='/api/v1/openapi.json'>/api/v1/openapi.json</a></p>"
        f"{_SPA_BAD_API_NOTE}</main></body></html>"
    )


def create_app(
    settings: Settings | None = None,
    *,
    engine: AsyncEngine | None = None,
    session_factory: async_sessionmaker[Any] | None = None,
    web_dist: Path | None = None,
) -> FastAPI:
    """Assemble the FastAPI application.

    ``session_factory`` wins over ``engine``; when neither is given the app
    builds (and later disposes) its own engine from ``settings.database_url``.
    Tests always supply their own sqllite-backed session factory.
    """
    settings = settings or Settings()
    owned_engine = engine is None and session_factory is None
    resolved_engine = engine
    resolved_factory = session_factory
    if resolved_factory is None:
        if resolved_engine is None:
            resolved_engine = make_engine(
                settings.require_database_url(), public=settings.deployment_mode == "public",
            )
        resolved_factory = make_session_factory(resolved_engine)

    dist = web_dist if web_dist is not None else _DEFAULT_WEB_DIST

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            if settings.deployment_mode == "public":
                if not (dist / "index.html").is_file():
                    raise RuntimeError("Public mode requires a built frontend")
                if not await database_ready(resolved_factory, public=True, timeout=15):
                    raise RuntimeError("Public readiness failed; check schema and role grants")
            yield
        finally:
            if owned_engine and resolved_engine is not None:
                await resolved_engine.dispose()

    app = FastAPI(
        title="github-radar API",
        version="0.1.0",
        docs_url="/api/v1/docs" if settings.deployment_mode == "local" else None,
        redoc_url="/redoc" if settings.deployment_mode == "local" else None,
        openapi_url="/api/v1/openapi.json" if settings.deployment_mode == "local" else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = resolved_engine
    app.state.session_factory = resolved_factory
    app.state.web_dist = dist

    register_exception_handlers(app)
    app.add_middleware(WorkspaceAccess, token=settings.workspace_admin_token.get_secret_value())
    app.add_middleware(DeploymentBoundary, public=settings.deployment_mode == "public",
                       hosts=settings.public_allowed_hosts)
    app.include_router(api_router, prefix="/api/v1")

    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=assets_dir),
            name="assets",
        )
    app.add_route("/{path:path}", _spa_fallback(dist), methods=["GET", "HEAD"])

    return app


__all__ = ["create_app"]
