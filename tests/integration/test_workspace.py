"""Workspace persistence, concurrent writes and additive migrations on guarded PostgreSQL."""

import asyncio
import os

import httpx
import pytest
from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from github_radar.api.app import create_app
from github_radar.storage.db import make_engine, make_session_factory
from github_radar.storage.models import (
    Base,
    DeveloperRow,
    RepositoryRow,
    WorkspaceDeveloperRow,
    WorkspaceRepositoryRow,
)
from github_radar.util import utcnow
from tests.integration.safety import validate_test_database_url
from tests.integration.test_bounded_collection import database as database
from tests.integration.test_postgres_flow import _drop_test_schema, _run_alembic
from tests.unit.test_api_routes import _seed
from tests.unit.test_workspace import AUTH, PREFIX, exercise_workspace, workspace_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL unset"),
]


@pytest.fixture
async def client(database):
    factory = make_session_factory(database)
    async with factory() as session:
        await _seed(session)
    app = create_app(workspace_settings(), session_factory=factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1",
        headers=AUTH,
    ) as client:
        yield client


async def test_postgres_workspace_lifecycle(client):
    await exercise_workspace(client)


async def test_concurrent_creation_and_updates_preserve_winner(client, database):
    dev = (await client.get(PREFIX + "/developers?saved=all")).json()["items"][0]
    path = PREFIX + f"/developers/{dev['id']}"
    body = {"saved": True, "notes": "first", "relationship_status": "interested", "version": None}
    created = await asyncio.gather(client.put(path, json=body), client.put(path, json=body))
    assert sorted(r.status_code for r in created) == [200, 409]
    version = next(r.json()["version"] for r in created if r.status_code == 200)
    edits = [{**body, "version": version, "notes": note} for note in ("second", "third")]
    responses = await asyncio.gather(*(client.put(path, json=edit) for edit in edits))
    assert sorted(r.status_code for r in responses) == [200, 409]
    winner = next(r.json() for r in responses if r.status_code == 200)
    async with make_session_factory(database)() as session:
        row = await session.get(WorkspaceDeveloperRow, dev["id"])
        assert row is not None and row.notes == winner["notes"] and row.version == winner["version"]
    # A new app/session sees committed private state, independent of browser/process memory.
    app = create_app(workspace_settings(), session_factory=make_session_factory(database))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
        headers=AUTH,
    ) as fresh:
        saved = (await fresh.get(PREFIX + "/developers")).json()
        assert saved["items"][0]["workspace"]["notes"] == winner["notes"]


@pytest.mark.parametrize(
    ("entity", "table"),
    [(DeveloperRow, WorkspaceDeveloperRow), (RepositoryRow, WorkspaceRepositoryRow)],
)
async def test_database_enforces_foreign_key_status_and_notes_limit(database, entity, table):
    factory = make_session_factory(database)
    async with factory() as session:
        await _seed(session)
        entity_id = await session.scalar(select(entity.id).limit(1))
    for changes in (
        {"entity_id": 999999},
        {"relationship_status": "invalid"},
        {"notes": "x" * 10001},
        {"version": ""},
        {"saved": None},
        {"notes": None},
        {"updated_at": None},
    ):
        async with factory() as session:
            values = dict(
                entity_id=entity_id,
                saved=True,
                notes="",
                version="a" * 32,
                relationship_status="interested",
                updated_at=utcnow(),
            )
            session.add(table(**{**values, **changes}))
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()


@pytest.mark.parametrize(
    ("entity", "table"),
    [(DeveloperRow, WorkspaceDeveloperRow), (RepositoryRow, WorkspaceRepositoryRow)],
)
async def test_private_entry_cascades_only_when_parent_deleted(database, entity, table):
    factory = make_session_factory(database)
    async with factory() as session:
        await _seed(session)
        entity_id = await session.scalar(select(entity.id).limit(1))
        session.add(table(
            entity_id=entity_id, saved=True, notes="private", relationship_status="connected",
            version="a" * 32, updated_at=utcnow(),
        ))
        await session.commit()
        await session.execute(delete(entity).where(entity.id == entity_id))
        await session.commit()
    async with factory() as session:
        assert await session.get(table, entity_id) is None


def test_workspace_migration_preserves_existing_observations():
    test_url = os.environ["TEST_DATABASE_URL"]
    validate_test_database_url(test_url=test_url, app_url=os.environ.get("DATABASE_URL"))
    _drop_test_schema()
    try:
        result = _run_alembic("upgrade", "0005")
        assert result.returncode == 0, result.stderr

        async def seed_old():
            engine = make_engine(test_url)
            try:
                async with make_session_factory(engine)() as session:
                    await _seed(session)
                async with engine.connect() as conn:
                    return await legacy_rows(conn)
            finally:
                await engine.dispose()

        before = asyncio.run(seed_old())
        result = _run_alembic("upgrade", "0006")
        assert result.returncode == 0, result.stderr

        async def check_new():
            engine = make_engine(test_url)
            try:
                async with engine.connect() as conn:
                    assert await legacy_rows(conn) == before
                    revision = await conn.scalar(text("SELECT version_num FROM alembic_version"))
                    assert revision == "0006"

                    def check_schema(sync_conn):
                        schema = inspect(sync_conn)
                        for table in (WorkspaceDeveloperRow, WorkspaceRepositoryRow):
                            name = table.__tablename__
                            primary_key = schema.get_pk_constraint(name)["constrained_columns"]
                            assert primary_key == ["entity_id"]
                            fk, = schema.get_foreign_keys(name)
                            assert fk["referred_table"] == name.removeprefix("workspace_")
                            assert fk["referred_columns"] == ["id"]
                            assert fk["options"]["ondelete"] == "CASCADE"
                            assert len(schema.get_check_constraints(name)) == 3
                            columns = schema.get_columns(name)
                            assert all(not column["nullable"] for column in columns)

                    await conn.run_sync(check_schema)
                async with make_session_factory(engine)() as session:
                    for entity, table in (
                        (DeveloperRow, WorkspaceDeveloperRow),
                        (RepositoryRow, WorkspaceRepositoryRow),
                    ):
                        entity_id = await session.scalar(select(entity.id).limit(1))
                        session.add(table(
                            entity_id=entity_id,
                            saved=True,
                            notes="migration note",
                            relationship_status="interested",
                            version="b" * 32,
                            updated_at=utcnow(),
                        ))
                    await session.commit()
                    assert (
                        await session.scalar(
                            select(func.count()).select_from(WorkspaceDeveloperRow)
                        )
                        == 1
                    )
            finally:
                await engine.dispose()

        asyncio.run(check_new())
        result = _run_alembic("downgrade", "0005")
        assert result.returncode == 0, result.stderr

        async def check_old():
            engine = make_engine(test_url)
            try:
                async with engine.connect() as conn:
                    assert await legacy_rows(conn) == before
                    for name in ("workspace_developers", "workspace_repositories"):
                        table = await conn.scalar(text("SELECT to_regclass(:name)"), {"name": name})
                        assert table is None
            finally:
                await engine.dispose()

        asyncio.run(check_old())
    finally:
        _drop_test_schema()


async def legacy_rows(conn):
    """Compare every stored column, including history, rather than only row counts."""
    return {
        name: (await conn.scalars(text(
            f'SELECT row_to_json(t)::text FROM "{name}" t ORDER BY row_to_json(t)::text'
        ))).all()
        for name in sorted(Base.metadata.tables)
        if not name.startswith("workspace_")
    }
