"""Database engine/session helpers for async SQLAlchemy + asyncpg."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)


def make_engine(database_url: str, *, echo: bool = False, public: bool = False) -> AsyncEngine:
    """Create an async engine with connection health checks enabled."""
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        echo=echo,
        hide_parameters=True,
        **({"pool_size": 2, "max_overflow": 1, "pool_timeout": 3,
            "connect_args": {"timeout": 3, "server_settings": {
                "default_transaction_read_only": "on", "statement_timeout": "5000",
                "application_name": "github-radar-public",
            }}} if public else {}),
    )
    return engine


def make_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


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
