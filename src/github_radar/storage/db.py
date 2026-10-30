"""Database engine/session helpers for async SQLAlchemy + psycopg."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from github_radar.storage.connection import connection_options, direct_url
from github_radar.storage.deadlines import (
    DeadlineConnection,
    finish_cleanup,
    require_reviewed_driver,
)

logger = logging.getLogger(__name__)


class ManagedSession(AsyncSession):
    async def __aexit__(self, *args: Any) -> None:
        await finish_cleanup(self.close())


class ManagedConnection(AsyncConnection):
    async def __aexit__(self, *args: Any) -> None:
        await finish_cleanup(self.close())


class ManagedEngine(AsyncEngine):
    _connection_cls = ManagedConnection

    async def dispose(self, close: bool = True) -> None:
        await finish_cleanup(super().dispose(close=close))


def make_engine(
    database_url: str,
    *,
    echo: bool = False,
    public: bool = False,
    collector: bool = False,
    ca_pem: str = "",
    null_pool: bool = False,
) -> AsyncEngine:
    """Create an async engine with connection health checks enabled."""
    require_reviewed_driver()
    if public and collector:
        raise ValueError("Database engine profiles are mutually exclusive")
    if collector:
        identity = direct_url(database_url)
        if not identity.query:
            # The collector's query-free URL contract fixes these settings in code.
            database_url = identity.set(
                query={"sslmode": "verify-full", "channel_binding": "require"}
            ).render_as_string(hide_password=False)
    url, connect_args = connection_options(database_url)
    io_seconds = 5 if public else 15 if collector else 30
    connect_args.update(
        async_creator_fn=DeadlineConnection.connect,
        io_seconds=io_seconds,
        connect_seconds=10,
        ca_pem=ca_pem,
        options=f"-c timezone=UTC -c statement_timeout={io_seconds * 1000} -c lock_timeout=5000",
    )
    if public:
        connect_args.update(
            application_name="github-radar-public",
            options="-c timezone=UTC -c default_transaction_read_only=on "
            "-c statement_timeout=5000 -c lock_timeout=2000 "
            "-c idle_in_transaction_session_timeout=10000 -c search_path=pg_catalog,public",
        )
    elif collector:
        connect_args.update(
            application_name="github-radar-collector",
            options="-c timezone=UTC -c statement_timeout=15000 -c lock_timeout=5000 "
            "-c idle_in_transaction_session_timeout=30000 -c search_path=pg_catalog,public",
        )
    engine = create_async_engine(
        url,
        pool_pre_ping=True,
        echo=echo,
        hide_parameters=True,
        connect_args=connect_args,
        **(
            {"pool_size": 2, "max_overflow": 0, "pool_timeout": 3}
            if public
            else {"poolclass": NullPool}
            if collector or null_pool
            else {}
        ),
    )
    return ManagedEngine(engine.sync_engine)


def make_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=ManagedSession, expire_on_commit=False)


async def ping(engine: AsyncEngine) -> bool:
    """Return True when the database answers ``SELECT 1``."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - deliberate probe, caller decides policy
        logger.error("Database connectivity check failed; connection details suppressed")
        return False
    return True


__all__ = ["make_engine", "make_session_factory", "ping"]
