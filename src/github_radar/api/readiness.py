"""Bounded, read-only schema/connection checks; never runs migrations."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

SCHEMA_REVISION = "0006"


async def database_ready(factory: async_sessionmaker[Any], *, public: bool) -> bool:
    try:
        async with asyncio.timeout(3):
            async with factory() as session:
                revisions = await session.scalars(text("SELECT version_num FROM alembic_version"))
                if revisions.all() != [SCHEMA_REVISION]:
                    return False
                if public:
                    if await session.scalar(text("SHOW transaction_read_only")) != "on":
                        return False
                    for name in ("workspace_developers", "workspace_repositories"):
                        if await session.scalar(
                            text("SELECT has_table_privilege(current_user, :name, 'SELECT')"),
                            {"name": name},
                        ):
                            return False
                await session.execute(text("SELECT id FROM repositories LIMIT 1"))
        return True
    except Exception:
        return False
