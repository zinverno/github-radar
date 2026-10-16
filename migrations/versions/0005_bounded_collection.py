"""Bounded collection receipts, provenance and contributor-fetch checkpoints.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("repositories", sa.Column("contributors_fetched_at", sa.DateTime(timezone=True)))
    op.add_column("repositories", sa.Column("contributors_fetched_limit", sa.Integer()))
    op.create_table(
        "collection_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("report_json", sa.Text(), nullable=False),
        sa.Column("requests_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "discovery_provenance",
        sa.Column(
            "repository_id",
            sa.BigInteger(),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("category", sa.Text(), primary_key=True),
        sa.Column("recipe", sa.Text(), primary_key=True),
        sa.Column("query_hash", sa.Text(), primary_key=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_id", sa.Text(), sa.ForeignKey("collection_runs.id"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("discovery_provenance")
    op.drop_table("collection_runs")
    op.drop_column("repositories", "contributors_fetched_limit")
    op.drop_column("repositories", "contributors_fetched_at")
