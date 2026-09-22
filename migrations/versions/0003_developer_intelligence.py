"""developer intelligence observations

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-22

Phase 3 makes developer and contributor intelligence *historical* instead of
instantaneous:

* ``developer_snapshots`` — append-only observations of a developer's public
  profile counters (followers, following, public_repos). The mutable counters on
  ``developers`` remain the convenient "latest" row; this table is the raw
  history from which follower/repo-count deltas are derived.

* ``repository_contributor_snapshots`` — append-only observations of the
  cumulative contribution count GitHub reports for a developer in a repository.
  GitHub's count is cumulative over the repository's whole history; observing it
  repeatedly is what lets us compute a genuine delta between observations (e.g.
  "+25 contributions between T1 and T2"). It is never "recent activity" by
  itself.

Both tables follow the Phase 2 snapshot observation policy: same-instant
re-observation is idempotent (unique constraints below; the storage layer
upserts on the pair/triple), a new instant appends, and history from other
instants is never mutated. Migrations 0001 and 0002 are untouched.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # developer_snapshots
    # ------------------------------------------------------------------ #
    op.create_table(
        "developer_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("developer_id", sa.BigInteger(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("followers", sa.Integer(), nullable=True),
        sa.Column("following", sa.Integer(), nullable=True),
        sa.Column("public_repos", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["developer_id"], ["developers.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "developer_id", "captured_at", name="uq_developer_snapshots_dev_captured"
        ),
    )

    # ------------------------------------------------------------------ #
    # repository_contributor_snapshots
    # ------------------------------------------------------------------ #
    op.create_table(
        "repository_contributor_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("developer_id", sa.BigInteger(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contributions", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["developer_id"], ["developers.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "repository_id",
            "developer_id",
            "captured_at",
            name="uq_contributor_snapshots_repo_dev_captured",
        ),
    )
    op.create_index(
        "ix_contributor_snapshots_developer_captured",
        "repository_contributor_snapshots",
        ["developer_id", "captured_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_contributor_snapshots_developer_captured",
        table_name="repository_contributor_snapshots",
    )
    op.drop_table("repository_contributor_snapshots")
    op.drop_table("developer_snapshots")