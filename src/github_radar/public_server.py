"""Render entrypoint: explicit public configuration, one bounded process, no startup writes."""

from __future__ import annotations

import logging
import os
import re

import uvicorn

from github_radar.api.app import create_app
from github_radar.config import Settings
from github_radar.storage.connection import direct_url, tls_options


def public_settings() -> tuple[Settings, int]:
    for key, value in os.environ.items():
        key = key.upper()
        if (
            value
            and key != "DATABASE_URL"
            and (
                key.startswith(("COLLECTOR_", "WORKSPACE_", "PG"))
                or key.endswith(("_TOKEN", "_API_KEY", "_SECRET", "_PASSWORD", "DATABASE_URL"))
            )
        ):
            raise ValueError("Public process contains forbidden credentials")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    if settings.deployment_mode != "public":
        raise ValueError("Public entrypoint requires explicit public mode")
    url = direct_url(settings.require_database_url())
    tls, _ = tls_options(url)
    if url.username != "radar_public" or not url.password or not tls:
        raise ValueError("Public entrypoint requires the dedicated reader and verified TLS")
    port = os.environ.get("PORT", "")
    if not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535:
        raise ValueError("PORT must be an explicit TCP port")
    return settings, int(port)


def main() -> None:
    try:
        settings, port = public_settings()
        app = create_app(settings)
    except Exception:
        raise SystemExit("Public startup refused: check configuration, TLS and PORT") from None
    logging.basicConfig(level=logging.WARNING)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        workers=1,
        proxy_headers=False,
        access_log=False,
        server_header=False,
        log_level="warning",
        limit_concurrency=4,
        backlog=32,
        timeout_keep_alive=2,
        timeout_graceful_shutdown=20,
        h11_max_incomplete_event_size=16384,
    )


if __name__ == "__main__":
    main()
