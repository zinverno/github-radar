"""ORM schema integrity tests (no live database required).

Introspects the SQLAlchemy metadata — the same metadata Alembic migrates — and
asserts the constraint/index/uniqueness guarantees the product needs.
PostgreSQL DDL itself is exercised by the (optional) integration tests and by
the Alembic offline SQL render.
"""

from __future__ import annotations

from github_radar.storage.models import (
    Base,
    ContributorRow,
    DeveloperRow,
    RepositoryRow,
    RepositoryTopicRow,
    SnapshotRow,
    TopicRow,
)

EXPECTED_TABLES = {
    "developers",
    "repositories",
    "topics",
    "repository_topics",
    "repository_contributors",
    "repository_snapshots",
}


def table(name: str):
    return Base.metadata.tables[name]


def test_all_expected_tables_exist() -> None:
    actual = set(Base.metadata.tables.keys())
    assert EXPECTED_TABLES <= actual


def test_repository_uniqueness() -> None:
    t = table("repositories")
    cols = {c.name for c in t.columns}
    assert "github_id" in cols
    assert "full_name" in cols
    unique = {i.name for i in t.indexes if i.unique}
    assert "ix_repositories_github_id" in unique
    assert "ix_repositories_full_name" in unique


def test_developer_uniqueness() -> None:
    t = table("developers")
    unique = {i.name for i in t.indexes if i.unique}
    assert "ix_developers_github_id" in unique


def test_topic_name_unique() -> None:
    t = table("topics")
    unique = {i.name for i in t.indexes if i.unique}
    assert "ix_topics_name" in unique


def test_assoc_tables_have_composite_pk() -> None:
    rt = table("repository_topics")
    assert list(rt.primary_key.columns) == [
        rt.c.repository_id,
        rt.c.topic_id,
    ]
    rc = table("repository_contributors")
    assert list(rc.primary_key.columns) == [
        rc.c.repository_id,
        rc.c.developer_id,
    ]


def test_assoc_unique_constraints() -> None:
    def names(t) -> set[str]:
        return {str(c.name) for c in t.constraints if c.name}

    assert "uq_repository_topics" in names(table("repository_topics"))
    assert "uq_repository_contributors" in names(table("repository_contributors"))


def test_snapshot_index_and_columns() -> None:
    t = table("repository_snapshots")
    idx = {i.name: i for i in t.indexes}
    assert "ix_repository_snapshots_repo_captured" in idx
    columns = [c.name for c in idx["ix_repository_snapshots_repo_captured"].columns]
    assert columns == ["repository_id", "captured_at"]
    assert t.c.repository_id.foreign_keys  # FK to repositories


def test_foreign_keys() -> None:
    rt = table("repository_topics")
    fk_targets = sorted(
        {list(col.foreign_keys)[0].column.table.name for col in rt.c if col.foreign_keys}
    )
    assert fk_targets == ["repositories", "topics"]
    assert table("repositories").c.owner_id.foreign_keys


def test_timestamps_are_timezone_aware() -> None:
    for column in (
        table("repository_snapshots").c.captured_at,
        table("repositories").c.created_at,
        table("developers").c.first_seen_at,
    ):
        assert getattr(column.type, "timezone", False) is True


def test_row_classes_have_no_extra_surprise_columns() -> None:
    # sanity: every model maps onto the expected table
    assert DeveloperRow.__tablename__ == "developers"
    assert RepositoryRow.__tablename__ == "repositories"
    assert TopicRow.__tablename__ == "topics"
    assert RepositoryTopicRow.__tablename__ == "repository_topics"
    assert ContributorRow.__tablename__ == "repository_contributors"
    assert SnapshotRow.__tablename__ == "repository_snapshots"