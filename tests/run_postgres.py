"""Run acceptance against freshly created PostgreSQL 16 clusters, never an operator DSN.

Usage: python -m tests.run_postgres [pytest arguments]. With no arguments also
verify Alembic head/check and recovery on a second, fresh fixture.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg
import sqlalchemy

from github_radar.native import native_report
from tests.postgres import run, temporary_postgres

ROOT = Path(__file__).resolve().parents[1]


def command(args: list[str], env: dict[str, str]) -> None:
    result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    # Failure tracebacks must not render fixture URLs. No production secret is an input.
    output = re.sub(
        r"postgresql(?:\+\w+)?://[^\s'\"<>]+",
        "[redacted PostgreSQL URL]",
        result.stdout + result.stderr,
    )
    print(output, end="", flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)


def main() -> None:
    os.umask(0o077)
    path = os.environ["PATH"]
    if directory := os.environ.get("RADAR_TEST_POSTGRES_BIN"):
        path = directory + os.pathsep + path
    os.environ["PATH"] = path
    for key in list(os.environ):
        if key.startswith("PG"):
            del os.environ[key]
    assert run(["postgres", "--version"]).startswith("postgres (PostgreSQL) 16.")
    assert psycopg.__version__ == "3.3.6" and sqlalchemy.__version__ == "2.0.54"
    assert psycopg.pq.__impl__ == "python" and psycopg.pq.version() == 180006
    print(json.dumps(native_report()), flush=True)
    print(
        json.dumps(
            {
                "psycopg": psycopg.__version__,
                "implementation": psycopg.pq.__impl__,
                "libpq": psycopg.pq.version(),
                "sqlalchemy": sqlalchemy.__version__,
            }
        ),
        flush=True,
    )
    env = {
        "PATH": path,
        "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT),
        "DATABASE_URL": "postgresql+psycopg://unused@127.0.0.1/unreachable_application",
        "GITHUB_TOKEN": "",
        "AI_API_KEY": "",
        "WORKSPACE_ADMIN_TOKEN": "",
        "DEPLOYMENT_MODE": "local",
    }
    with temporary_postgres() as cluster:
        env["TEST_DATABASE_URL"] = cluster["owner"]
        command(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--tb=short",
                "-o",
                "faulthandler_timeout=45",
                *sys.argv[1:],
            ],
            env,
        )
    if not sys.argv[1:]:
        with temporary_postgres() as cluster:
            env.update(DATABASE_URL=cluster["owner"], RESTORE_TEST_DATABASE_URL=cluster["restore"])
            for args in (("upgrade", "head"), ("check",)):
                command([sys.executable, "-m", "alembic", *args], env)
            command(
                [
                    sys.executable,
                    "-m",
                    "github_radar.recovery",
                    "--output",
                    str(cluster["directory"] / "verified-recovery.dump"),
                    "--confirm-target",
                    "github_radar_restore_check",
                ],
                env,
            )


if __name__ == "__main__":
    main()
