"""snapshot observation policy

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-18

Adds a unique constraint on ``(repository_id, captured_at)`` so snapshot rows
are *idempotently* observable.  ``insert_snapshot_if_changed`` then implements
the observation policy as an upsert on that pair: re-observing a repo at the
same instant replaces the row instead of duplicating it, and observing at a
new instant appends.  Together with the dedupe-by-delta policy this keeps the
snapshots table low-noise and makes observation runs reproducible without
accumulating identical rows.

The non-unique index ``ix_repository_snapshots_repo_captured`` created in 0001
is redundant once the unique constraint exists (PostgreSQL backs a unique
constraint with a unique index), so it is dropped to keep the schema clean.
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "ix_repository_snapshots_repo_captured",
        table_name="repository_snapshots",
    )
    op.create_unique_constraint(
        "uq_repository_snapshots_repo_captured",
        "repository_snapshots",
        ["repository_id", "captured_at"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_repository_snapshots_repo_captured",
        "repository_snapshots",
        type_="unique",
    )
    op.create_index(
        "ix_repository_snapshots_repo_captured",
        "repository_snapshots",
        ["repository_id", "captured_at"],
        unique=False,
    )