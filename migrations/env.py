"""Alembic migration environment.

Uses the same async engine stack as the application (asyncpg). The database URL
comes from validated application configuration, in order of precedence:

1. ``-x url=...`` passed on the command line;
2. the ``sqlalchemy.url`` main option (e.g. set by ``github-radar init-db``);
3. the ``DATABASE_URL`` environment variable via :func:`github_radar.config.get_settings`;
4. otherwise a clear configuration error.

The ``driver://...`` placeholder in ``alembic.ini`` is never handed to
SQLAlchemy: online migrations always inject the resolved URL into the engine
configuration.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from github_radar.config import get_settings
from github_radar.storage.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Prefix of the placeholder that ships with alembic.ini. It is not a usable
# database URL and must never reach SQLAlchemy.
_PLACEHOLDER_PREFIX = "driver://"


def _resolve_url() -> str:
    x_url = (context.get_x_argument(as_dictionary=True).get("url") or "").strip()
    if x_url and not x_url.startswith(_PLACEHOLDER_PREFIX):
        return x_url
    configured = (config.get_main_option("sqlalchemy.url") or "").strip()
    if configured and not configured.startswith(_PLACEHOLDER_PREFIX):
        return configured
    from_env = get_settings().database_url
    if from_env:
        return from_env
    raise RuntimeError(
        "DATABASE_URL is not set; cannot run an Alembic migration.\n"
        "  Export it as postgresql+asyncpg://user:pass@host:port/dbname,\n"
        "  run `github-radar init-db`, or pass -x url=postgresql+asyncpg://…"
    )


URL = _resolve_url()


def run_migrations_offline() -> None:
    """Run migrations against a generated SQL script (no database)."""
    context.configure(
        url=URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # Build the engine from the *resolved* URL, never from the raw
    # alembic.ini section (which may still carry the driver:// placeholder).
    section = dict(config.get_section(config.config_ini_section, {}))
    section["sqlalchemy.url"] = URL
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()