"""Explicit owner-only initialization of an empty database; never called by the server."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection

from github_radar.recovery import require_empty
from github_radar.storage.connection import direct_url
from github_radar.storage.db import make_engine


async def initialize(url: str, confirmation: str) -> None:
    parsed = direct_url(url)
    if (
        not confirmation
        or parsed.database != confirmation
        or parsed.username
        in {
            "radar_public",
            "github_radar_collector",
        }
    ):
        raise ValueError("Confirm the exact new database using its operator-only owner role")
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

    def migrate(connection: Connection) -> None:
        config.attributes["connection"] = connection
        command.upgrade(config, "0006")

    engine = make_engine(url)
    try:
        async with engine.begin() as connection:
            await require_empty(connection)
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-database", required=True)
    args = parser.parse_args()
    try:
        asyncio.run(initialize(os.environ.get("MIGRATION_DATABASE_URL", ""), args.confirm_database))
    except Exception:
        raise SystemExit("Initialization failed; private diagnostics suppressed") from None
    print("Empty database initialized to revision 0006; application roles remain operator-only")


if __name__ == "__main__":
    main()
