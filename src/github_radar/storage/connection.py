"""Explicit direct PostgreSQL URLs and verified TLS, shared by asyncpg and backup."""

from __future__ import annotations

import ssl
from pathlib import Path

from sqlalchemy.engine import URL, make_url

LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def direct_url(value: str) -> URL:
    try:
        url = make_url(value)
        valid = (
            url.drivername == "postgresql+asyncpg"
            and url.host
            and url.username
            and url.database
            and "," not in url.host
            and "/" not in url.host
            and "-pooler" not in url.host.lower().split(".")[0]
            and (url.port is None or 1 <= url.port <= 65535)
        )
    except Exception:
        valid = False
    if not valid:
        raise ValueError("Direct PostgreSQL URL requires explicit host, user and database")
    return url


def tls_options(url: URL) -> tuple[bool, str | None]:
    query = url.query
    mode = query.get("sslmode", query.get("ssl"))
    ca = query.get("sslrootcert")
    if (
        set(query) - {"sslmode", "ssl", "sslrootcert"}
        or ("sslmode" in query and "ssl" in query)
        or (mode is not None and mode != "verify-full")
        or (ca is not None and (not isinstance(ca, str) or not Path(ca).is_absolute()))
        or (ca is not None and mode is None)
        or (url.host not in LOOPBACK and mode != "verify-full")
    ):
        raise ValueError("Database TLS requires verify-full; only an absolute CA file is optional")
    return mode == "verify-full", ca


def verified_context(*, cafile: str | None = None, ca_pem: str = "") -> ssl.SSLContext:
    # Construct explicitly: create_default_context also honors SSLKEYLOGFILE.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if cafile:
        context.load_verify_locations(cafile=cafile)
    else:
        context.load_default_certs()
    if ca_pem:
        context.load_verify_locations(cadata=ca_pem)
    return context


def connection_options(value: str) -> tuple[URL, ssl.SSLContext | bool]:
    url = direct_url(value)
    tls, ca = tls_options(url)
    return url.set(query={}), verified_context(cafile=ca) if tls else False
