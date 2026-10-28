"""Public connections cannot write; an owner connection fails the private-data readiness check."""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from github_radar.api.readiness import database_ready
from github_radar.storage.db import make_engine, make_session_factory
from tests.integration.test_bounded_collection import database as database

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="test URL unset"),
]


async def test_public_connection_is_readonly_and_owner_not_ready(database):
    engine = make_engine(os.environ["TEST_DATABASE_URL"], public=True)
    try:
        async with engine.connect() as conn:
            assert await conn.scalar(text("SHOW transaction_read_only")) == "on"
            with pytest.raises(DBAPIError, match="read-only"):
                await conn.execute(text("DELETE FROM repositories"))
        # Fixtures use the table owner: it must not be accepted for public serving.
        assert not await database_ready(make_session_factory(engine), public=True)
    finally:
        await engine.dispose()
