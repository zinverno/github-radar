"""Explicit public read allowlist; no proxy or browser claim grants privileges."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

from starlette._utils import get_route_path
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.routing import compile_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from github_radar.api.errors import error_payload
from github_radar.api.workspace_access import PRIVATE_HEADERS

logger = logging.getLogger(__name__)

PUBLIC_READ_PATHS = frozenset(
    {
        "/api/v1/health",
        "/api/v1/ready",
        "/api/v1/capabilities",
        "/api/v1/status",
        "/api/v1/explore",
        "/api/v1/repositories",
        "/api/v1/repositories/summary",
        "/api/v1/repositories/{ref:path}",
        "/api/v1/topics",
        "/api/v1/topics/{name}",
        "/api/v1/developers",
        "/api/v1/developers/emerging",
        "/api/v1/developers/high-activity",
        "/api/v1/developers/{login}",
        "/api/v1/ecosystem",
        "/api/v1/ecosystem/nodes",
        "/api/v1/ecosystem/edges",
        "/api/v1/bridges",
        "/api/v1/bridges/repos",
        "/api/v1/bridges/developers/{login}",
        "/api/v1/bridges/repositories/{ref:path}",
        "/api/v1/bridges/cross-topic",
        "/api/v1/search",
    }
)


class DeploymentBoundary:
    def __init__(self, app: ASGIApp, *, public: bool, hosts: tuple[str, ...]) -> None:
        self.app, self.public, self.hosts = app, public, hosts
        self.read_paths = [compile_path(path)[0] for path in PUBLIC_READ_PATHS]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket" and self.public:
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = get_route_path(scope)
        started = False

        async def secure_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                if self.public:
                    MutableHeaders(scope=message).update(PRIVATE_HEADERS)
                    MutableHeaders(scope=message).update({
                        "Strict-Transport-Security": "max-age=31536000",
                        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                    })
            await send(message)

        async def refuse(status: int, code: str, message: str) -> None:
            await JSONResponse(
                error_payload(code, message), status_code=status, headers=PRIVATE_HEADERS
            )(scope, receive, secure_send)

        if self.public:
            headers = Headers(scope=scope)
            try:
                host = urlsplit("http://" + headers.get("host", ""))
                host.port
                valid = (
                    len(headers.getlist("host")) == 1
                    and re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?", headers.get("host", ""))
                    is not None
                    and host.hostname in self.hosts
                    and not any(
                        (host.username, host.password, host.path, host.query, host.fragment)
                    )
                )
            except ValueError:
                valid = False
            if not valid:
                await refuse(400, "invalid_host", "Request host is not allowed.")
                return
            # Forwarded headers are never used for identity, host or scheme decisions.
            if scope["method"] not in {"GET", "HEAD"}:
                await refuse(405, "read_only", "Public mode permits read-only requests.")
                return
            if path.startswith("/api") and not any(p.fullmatch(path) for p in self.read_paths):
                await refuse(404, "not_found", "This endpoint is unavailable in public mode.")
                return
            if path in {"/docs", "/redoc", "/openapi.json", "/ai", "/workspace"} or path.startswith(
                ("/workspace/", "/ai/")
            ):
                await refuse(404, "not_found", "This page is unavailable in public mode.")
                return
        try:
            await self.app(scope, receive, secure_send)
        except Exception:
            # SQL/driver exception text can contain notes or connection details.
            logger.error("HTTP request failed; internal details suppressed")
            if started:
                raise RuntimeError("HTTP response interrupted") from None
            await refuse(500, "internal_error", "An internal error occurred.")
