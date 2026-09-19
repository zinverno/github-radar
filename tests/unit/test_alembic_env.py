"""Alembic configuration regression tests (no live database required).

These exercise the real ``migrations/env.py`` through the Alembic CLI in a
subprocess, guarding against the regression where online migrations fed the
literal ``driver://`` placeholder from ``alembic.ini`` to SQLAlchemy:

* online migrations must honour ``DATABASE_URL`` (and ``-x url=``) instead of
  the placeholder;
* a missing ``DATABASE_URL`` must fail with a clear configuration error, not
  ``Can't load plugin: sqlalchemy.dialects:driver``;
* offline SQL rendering must keep working.

Each test runs from a scratch directory with a copy of the project's
``alembic.ini`` (paths made absolute), so it is independent of the working
directory, of any developer ``.env`` file, and of the local PostgreSQL.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Rearranged in the copy of alembic.ini so Alembic finds the real scripts and
# sources regardless of the current working directory.
SCRIPT_LOCATION = str(PROJECT_ROOT / "migrations")
PREPEND_SYS_PATH = str(PROJECT_ROOT / "src")

# An asyncpg-shaped URL that is unreachable: any online attempt must fail with
# a *connection* error (the URL was used), never a dialect-load error (the
# placeholder was used).
UNREACHABLE_URL = "postgresql+asyncpg://u:p@127.0.0.1:1/radar"

DIALECT_PLUGIN_ERROR = "Can't load plugin: sqlalchemy.dialects:driver"


def _write_alembic_ini(tmp_path: Path) -> Path:
    """Copy alembic.ini into ``tmp_path`` with absolute script paths."""
    ini = (PROJECT_ROOT / "alembic.ini").read_text(encoding="utf-8")
    ini = ini.replace(
        "script_location = migrations",
        f"script_location = {SCRIPT_LOCATION}",
    )
    ini = ini.replace(
        "prepend_sys_path = src",
        f"prepend_sys_path = {PREPEND_SYS_PATH}",
    )
    path = tmp_path / "alembic.ini"
    path.write_text(ini, encoding="utf-8")
    return path


def _run_alembic(
    tmp_path: Path,
    *args: str,
    database_url: str | None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    ini = _write_alembic_ini(tmp_path)
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    if database_url is not None:
        env["DATABASE_URL"] = database_url
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )


def test_online_migration_uses_database_url_not_placeholder(
    tmp_path: Path,
) -> None:
    """DATABASE_URL must override the driver:// placeholder, not the reverse."""
    proc = _run_alembic(
        tmp_path,
        "current",
        database_url=UNREACHABLE_URL,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert DIALECT_PLUGIN_ERROR not in output
    assert "driver://" not in output
    # The asyncpg dialect was loaded and a real connection was attempted.
    assert any(
        token in output for token in ("Connect call failed", "Connection refused")
    )


def test_online_migration_without_database_url_fails_clearly(
    tmp_path: Path,
) -> None:
    """Missing DATABASE_URL must surface a clear configuration error."""
    proc = _run_alembic(tmp_path, "current", database_url=None)
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert DIALECT_PLUGIN_ERROR not in output
    assert "driver://" not in output
    assert "DATABASE_URL is not set" in output
    assert "postgresql+asyncpg://" in output


def test_offline_rendering_uses_database_url(tmp_path: Path) -> None:
    """Offline SQL rendering keeps working when DATABASE_URL is configured."""
    proc = _run_alembic(
        tmp_path,
        "upgrade",
        "head",
        "--sql",
        database_url=UNREACHABLE_URL,
    )
    assert proc.returncode == 0, proc.stderr
    assert "CREATE TABLE developers" in proc.stdout
    assert "CREATE TABLE repository_snapshots" in proc.stdout


def test_offline_rendering_without_database_url_via_x_url(
    tmp_path: Path,
) -> None:
    """``-x url=...`` keeps offline rendering working without DATABASE_URL."""
    proc = _run_alembic(
        tmp_path,
        "-x",
        f"url={UNREACHABLE_URL}",
        "upgrade",
        "head",
        "--sql",
        database_url=None,
    )
    assert proc.returncode == 0, proc.stderr
    assert "CREATE TABLE developers" in proc.stdout
    assert "CREATE TABLE repository_snapshots" in proc.stdout


@pytest.mark.parametrize(
    "args",
    [
        ("current",),
        ("upgrade", "head"),
    ],
)
def test_online_never_contains_placeholder_in_alembic_failure_path(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    """No online path may ever reach SQLAlchemy with the placeholder URL."""
    proc = _run_alembic(tmp_path, *args, database_url=UNREACHABLE_URL)
    output = proc.stdout + proc.stderr
    assert DIALECT_PLUGIN_ERROR not in output
    assert "sqlalchemy.dialects:driver" not in output
    assert "driver://" not in output