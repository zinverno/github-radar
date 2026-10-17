"""Private single-admin watchlists and relationship notes.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for entity in ("developer", "repository"):
        target = "developers" if entity == "developer" else "repositories"
        op.create_table(
            f"workspace_{target}",
            sa.Column(
                "entity_id",
                sa.BigInteger(),
                sa.ForeignKey(f"{target}.id", ondelete="CASCADE"),
                primary_key=True,
                autoincrement=False,
            ),
            sa.Column("saved", sa.Boolean(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("relationship_status", sa.Text(), nullable=False),
            sa.Column("version", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("length(notes) <= 10000", name=f"ck_workspace_{entity}_notes"),
            sa.CheckConstraint("length(version) = 32", name=f"ck_workspace_{entity}_version"),
            sa.CheckConstraint(
                "relationship_status IN ('interested','contacted','connected','collaborating','paused')",
                name=f"ck_workspace_{entity}_status",
            ),
        )


def downgrade() -> None:
    op.drop_table("workspace_repositories")
    op.drop_table("workspace_developers")
