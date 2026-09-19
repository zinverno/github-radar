"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-18

Hand-written initial schema for github-radar Phase 1.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # developers
    # ------------------------------------------------------------------ #
    op.create_table(
        "developers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("github_id", sa.BigInteger(), nullable=False),
        sa.Column("login", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("html_url", sa.Text(), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("company", sa.Text(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("blog", sa.Text(), nullable=True),
        sa.Column("public_email", sa.Text(), nullable=True),
        sa.Column("twitter_username", sa.Text(), nullable=True),
        sa.Column("followers", sa.Integer(), nullable=True),
        sa.Column("following", sa.Integer(), nullable=True),
        sa.Column("public_repos", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "profile_fetched_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_developers_github_id", "developers", ["github_id"], unique=True)
    op.create_index("ix_developers_login", "developers", ["login"], unique=False)

    # ------------------------------------------------------------------ #
    # topics
    # ------------------------------------------------------------------ #
    op.create_table(
        "topics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_topics_name", "topics", ["name"], unique=True)

    # ------------------------------------------------------------------ #
    # repositories
    # ------------------------------------------------------------------ #
    op.create_table(
        "repositories",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("github_id", sa.BigInteger(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
        sa.Column("owner_login", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("html_url", sa.Text(), nullable=True),
        sa.Column("homepage", sa.Text(), nullable=True),
        sa.Column("default_branch", sa.Text(), nullable=True),
        sa.Column("primary_language", sa.Text(), nullable=True),
        sa.Column("is_fork", sa.Boolean(), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("is_disabled", sa.Boolean(), nullable=False),
        sa.Column("is_template", sa.Boolean(), nullable=True),
        sa.Column("visibility", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "pushed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["developers.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_repositories_github_id", "repositories", ["github_id"], unique=True
    )
    op.create_index(
        "ix_repositories_full_name", "repositories", ["full_name"], unique=True
    )
    op.create_index(
        "ix_repositories_primary_language",
        "repositories",
        ["primary_language"],
        unique=False,
    )
    op.create_index(
        "ix_repositories_owner_login", "repositories", ["owner_login"], unique=False
    )
    op.create_index(
        "ix_repositories_last_seen_at", "repositories", ["last_seen_at"], unique=False
    )

    # ------------------------------------------------------------------ #
    # repository_topics
    # ------------------------------------------------------------------ #
    op.create_table(
        "repository_topics",
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("repository_id", "topic_id"),
        sa.UniqueConstraint(
            "repository_id", "topic_id", name="uq_repository_topics"
        ),
    )

    # ------------------------------------------------------------------ #
    # repository_contributors
    # ------------------------------------------------------------------ #
    op.create_table(
        "repository_contributors",
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("developer_id", sa.BigInteger(), nullable=False),
        sa.Column("contributions", sa.Integer(), nullable=False),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["developer_id"], ["developers.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("repository_id", "developer_id"),
        sa.UniqueConstraint(
            "repository_id", "developer_id", name="uq_repository_contributors"
        ),
    )
    op.create_index(
        "ix_repository_contributors_repo_contributions",
        "repository_contributors",
        ["repository_id", "contributions"],
        unique=False,
    )

    # ------------------------------------------------------------------ #
    # repository_snapshots
    # ------------------------------------------------------------------ #
    op.create_table(
        "repository_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "captured_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column("forks", sa.Integer(), nullable=False),
        sa.Column("watchers", sa.Integer(), nullable=True),
        sa.Column("open_issues", sa.Integer(), nullable=True),
        sa.Column("size_kb", sa.Integer(), nullable=True),
        sa.Column(
            "pushed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_repository_snapshots_repo_captured",
        "repository_snapshots",
        ["repository_id", "captured_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_repository_snapshots_repo_captured", table_name="repository_snapshots"
    )
    op.drop_table("repository_snapshots")
    op.drop_index(
        "ix_repository_contributors_repo_contributions",
        table_name="repository_contributors",
    )
    op.drop_table("repository_contributors")
    op.drop_table("repository_topics")
    op.drop_index("ix_repositories_last_seen_at", table_name="repositories")
    op.drop_index("ix_repositories_owner_login", table_name="repositories")
    op.drop_index("ix_repositories_primary_language", table_name="repositories")
    op.drop_index("ix_repositories_full_name", table_name="repositories")
    op.drop_index("ix_repositories_github_id", table_name="repositories")
    op.drop_table("repositories")
    op.drop_index("ix_topics_name", table_name="topics")
    op.drop_table("topics")
    op.drop_index("ix_developers_login", table_name="developers")
    op.drop_index("ix_developers_github_id", table_name="developers")
    op.drop_table("developers")