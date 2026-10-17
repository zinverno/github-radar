"""Direct-loopback, bearer-token boundary. Proxies and remote deployment are unsupported."""

from __future__ import annotations

import ipaddress
import secrets
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from github_radar.api.errors import error_payload

PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
}


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class WorkspaceAccess:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token.encode("ascii")

    def rejection(self, scope: Scope) -> tuple[int, str, str] | None:
        if not self.token:
            return 503, "workspace_disabled", "The private workspace is not configured."
        headers = Headers(scope=scope)
        peer = scope.get("client")
        try:
            host = urlsplit("http://" + headers.get("host", ""))
            local_host = (
                len(headers.getlist("host")) == 1
                and host.hostname in ("localhost", "127.0.0.1", "::1")
                and not host.username
                and not host.password
                and not host.path
                and not host.query
                and not host.fragment
            )
            host.port  # Reject malformed ports before constructing a request URL.
        except ValueError:
            local_host = False
        forwarded = any(
            name.lower() == "forwarded"
            or name.lower().startswith("x-forwarded-")
            or name.lower() == "x-real-ip"
            for name in headers
        )
        if not peer or not is_loopback(peer[0]) or not local_host or forwarded:
            return 403, "workspace_local_only", "Workspace requires direct local admin access."
        origin = headers.get("origin")
        expected = f"{scope['scheme']}://{headers.get('host')}"
        if (
            len(headers.getlist("origin")) > 1
            or (origin is not None and origin != expected)
            or headers.get("sec-fetch-site") == "cross-site"
        ):
            return 403, "workspace_origin", "Cross-origin workspace requests are not allowed."
        authorizations = headers.getlist("authorization")
        if len(authorizations) != 1 or not secrets.compare_digest(
            authorizations[0].encode("latin-1"), b"Bearer " + self.token
        ):
            return 401, "workspace_unauthorized", "An admin token is required."
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        private_api = path == "/api/v1/workspace" or path.startswith("/api/v1/workspace/")
        shell = path == "/workspace" or path.startswith("/workspace/")
        if scope["type"] != "http" or not (private_api or shell):
            await self.app(scope, receive, send)
            return

        async def private_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.update(PRIVATE_HEADERS)
            await send(message)

        if private_api and (rejection := self.rejection(scope)):
            status, code, message = rejection
            response = JSONResponse(
                error_payload(code, message),
                status_code=status,
                headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
            )
            await response(scope, receive, private_send)
            return
        await self.app(scope, receive, private_send)
