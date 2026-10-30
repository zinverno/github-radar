"""Strict direct PostgreSQL identities and explicit libpq security settings."""

from __future__ import annotations

import os
import re
import ssl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy.engine import URL, make_url

LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def direct_url(value: str) -> URL:
    try:
        parts = urlsplit(value)
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
        url = make_url(value)
        valid = (
            url.drivername == "postgresql+psycopg"
            and url.host
            and url.username
            and url.database
            and not re.search(r"[\s\x00-\x1f\x7f]", value)
            and not re.search(r"%(?![0-9a-fA-F]{2})", value)
            and not parts.fragment
            and parts.netloc.count("@") == 1
            and len(pairs) == len({key for key, _ in pairs})
            and all(value for _, value in pairs)
            and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", url.database)
            and "%" not in url.host
            and "," not in url.host
            and "/" not in url.host
            and "\\" not in url.host
            and "-pooler" not in url.host.lower().split(".")[0]
            and (url.port is None or 1 <= url.port <= 65535)
            and all(
                not re.search(r"[\x00-\x1f\x7f]", item)
                for item in (url.username, url.password or "", url.database)
            )
        )
    except Exception:
        valid = False
    if not valid:
        raise ValueError("Direct PostgreSQL URL requires an unambiguous host, user and database")
    return url


def tls_options(url: URL) -> tuple[bool, str | None]:
    query = url.query
    mode, ca = query.get("sslmode"), query.get("sslrootcert")
    if (
        set(query) - {"sslmode", "sslrootcert", "channel_binding"}
        or (mode is not None and mode != "verify-full")
        or query.get("channel_binding", "require") != "require"
        or (ca is not None and (not isinstance(ca, str) or not Path(ca).is_absolute()))
        or (ca is not None and mode is None)
        or (url.host not in LOOPBACK and mode != "verify-full")
    ):
        raise ValueError("Database requires verify-full TLS and required channel binding")
    # Loopback connections also require TLS/SCRAM; there is no plaintext fallback.
    return True, ca


def trusted_ca(cafile: str | None = None) -> str:
    # Ignore SSL_CERT_FILE/SSL_CERT_DIR overrides and require an actual CA bundle.
    path = cafile or ssl.get_default_verify_paths().openssl_cafile
    if not path or not Path(path).is_file():
        raise ValueError("Database requires a readable trusted CA bundle")
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=path)
    except Exception:
        raise ValueError("Database CA bundle is invalid") from None
    return str(Path(path).resolve())


def reject_libpq_environment() -> None:
    if any(value for key, value in os.environ.items() if key.upper().startswith("PG")):
        raise ValueError("Ambient PG connection settings are forbidden; use explicit configuration")


@contextmanager
def collector_ca(cafile: str, ca_pem: str) -> Iterator[str]:
    if not ca_pem:
        yield cafile
        return
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=ca_pem)
    except Exception:
        raise ValueError("Collector CA certificate is invalid") from None
    # Libpq needs a file; keep its lifetime within each bounded authentication attempt.
    with NamedTemporaryFile(mode="w", prefix="radar-ca-", suffix=".pem") as stream:
        stream.write(Path(cafile).read_text() + "\n" + ca_pem)
        stream.flush()
        yield stream.name


def connection_options(value: str) -> tuple[URL, dict[str, object]]:
    url = direct_url(value)
    _, ca = tls_options(url)
    return url.set(query={}), {
        "sslmode": "verify-full",
        "channel_binding": "require",
        "sslrootcert": trusted_ca(ca),
        "ssl_min_protocol_version": "TLSv1.2",
        "gssencmode": "disable",
        "sslcertmode": "disable",
        "sslkeylogfile": "",
        "passfile": os.devnull,
        "connect_timeout": 10,
        "options": "-c timezone=UTC",
    }
