"""Local recovery drill: fresh consistent backup, restore only into an empty allowlisted DB."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncConnection

from github_radar.config import Settings
from github_radar.storage.connection import direct_url, tls_options, trusted_ca
from github_radar.storage.db import make_engine

RESTORE_TARGET = "github_radar_restore_check"


def validate_target(source: str, target: str, confirmation: str) -> URL:
    try:
        url = direct_url(target)
        original = direct_url(source)
        tls_options(url)
        tls_options(original)
        valid = (
            confirmation == RESTORE_TARGET
            and url.database == RESTORE_TARGET
            and original.database != RESTORE_TARGET
            and url.drivername == "postgresql+psycopg"
            and url.host in {"127.0.0.1", "localhost", "::1"}
        )
    except Exception:
        valid = False
    if not valid:
        raise ValueError("Restore requires the explicit local disposable target and confirmation")
    return url


def pg_environment(url: URL) -> dict[str, str]:
    url = direct_url(url.render_as_string(hide_password=False))
    _, ca = tls_options(url)
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    env.update(
        PGHOST=url.host or "",
        PGPORT=str(url.port or 5432),
        PGDATABASE=url.database or "",
        PGUSER=url.username or "",
        PGPASSWORD=url.password or "",
        PGCONNECT_TIMEOUT="5",
        PGOPTIONS="-c timezone=UTC -c statement_timeout=30000 -c lock_timeout=5000",
        PGSSLMODE="verify-full",
        PGCHANNELBINDING="require",
        PGGSSENCMODE="disable",
        PGPASSFILE=os.devnull,
        PGSSLMINPROTOCOLVERSION="TLSv1.2",
    )
    env["PGSSLROOTCERT"] = trusted_ca(ca)
    return env


async def fingerprint(connection: AsyncConnection) -> dict[str, Any]:
    names = (
        await connection.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        )
    ).all()
    tables = {}
    for name in names:
        quoted = connection.dialect.identifier_preparer.quote(name)
        rows = (
            await connection.scalars(
                text(f"SELECT row_to_json(t)::text FROM {quoted} t ORDER BY row_to_json(t)::text")
            )
        ).all()
        tables[name] = {
            "count": len(rows),
            "sha256": hashlib.sha256("\n".join(rows).encode()).hexdigest(),
        }
    return tables


async def require_empty(connection: AsyncConnection) -> None:
    # Refuse pre-existing objects, even if all their tables happen to be empty.
    occupied = await connection.scalar(
        text("""
        SELECT EXISTS (
          SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema'
        ) OR EXISTS (
          SELECT 1 FROM pg_namespace
          WHERE left(nspname, 3) <> 'pg_' AND nspname NOT IN ('public', 'information_schema')
        ) OR EXISTS (
          SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
          WHERE n.nspname = 'public'
        )
    """)
    )
    if occupied:
        raise ValueError("Restore target is not empty; no objects were changed")


def _dump(output: Path, env: dict[str, str], snapshot: str) -> None:
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        subprocess.run(
            ["pg_dump", "--format=custom", "--snapshot", snapshot],
            env=env,
            stdout=stream,
            stderr=subprocess.PIPE,
            check=True,
            timeout=120,
        )


def _restore(output: Path, env: dict[str, str]) -> None:
    # No --create, --clean, owner or ACL replay. No caller-supplied command options.
    subprocess.run(
        [
            "pg_restore",
            "--dbname",
            RESTORE_TARGET,
            "--no-owner",
            "--no-privileges",
            "--no-tablespaces",
            "--single-transaction",
            "--exit-on-error",
            str(output.resolve()),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=True,
        timeout=120,
    )


async def recovery_drill(
    source: str, target: str, confirmation: str, output: Path
) -> dict[str, Any]:
    url = validate_target(source, target, confirmation)
    source_env, target_env = pg_environment(make_url(source)), pg_environment(url)
    source_engine, target_engine = make_engine(source), make_engine(target)
    try:
        async with target_engine.connect() as target_connection:
            await require_empty(target_connection)
        async with source_engine.connect() as connection:
            await connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            snapshot = await connection.scalar(text("SELECT pg_export_snapshot()"))
            before = await fingerprint(connection)
            await asyncio.to_thread(_dump, output, source_env, str(snapshot))
        # Keep this database private and unused by any other process during the drill.
        async with target_engine.connect() as target_connection:
            await require_empty(target_connection)
        await asyncio.to_thread(_restore, output, target_env)
        async with target_engine.connect() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            restored = await fingerprint(connection)
            constraints_valid = not await connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE NOT convalidated)")
            )
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        if before != restored or not constraints_valid:
            raise RuntimeError("Restore verification failed; retain the private archive for review")
        return {
            "target": RESTORE_TARGET,
            "revision": revision,
            "all_table_rows_match": True,
            "constraints_valid": True,
            "counts": {k: v["count"] for k, v in restored.items()},
            "archive_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        }
    finally:
        await source_engine.dispose()
        await target_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm-target", required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(
            recovery_drill(
                Settings().require_database_url(),
                os.environ.get("RESTORE_TEST_DATABASE_URL", ""),
                args.confirm_target,
                args.output,
            )
        )
    except Exception:
        # Connection errors and pg_restore stderr can contain credentials or private rows.
        raise SystemExit(
            "Recovery drill failed; check target, empty database, archive and PG tools. "
            "Private diagnostic details suppressed."
        ) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
