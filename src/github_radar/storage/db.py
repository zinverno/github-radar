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


def make_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create an async engine with connection health checks enabled."""
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        echo=echo,
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
        logger.exception("Database connectivity check failed")
        return False
    return True


__all__ = ["make_engine", "make_session_factory", "ping"]