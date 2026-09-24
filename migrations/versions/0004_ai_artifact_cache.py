"""ai artifact cache

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-24

Phase 5 adds an optional LLM synthesis layer on top of the deterministic
analytics. This migration is purely additive — migrations 0001/0002/0003 are
untouched:

``ai_artifacts`` — the immutable model-result cache. One row per
(entity_type, entity_key, artifact_type, window_days, source_fingerprint,
prompt_version, model):

* ``source_fingerprint`` is the deterministic digest of the evidence the model
  saw, so a summary can only be served when the evidence it is based on is
  still current; changed evidence means a changed fingerprint means a cache
  miss and a fresh synthesis.
* ``result_json`` / ``evidence_json`` are snapshot blobs (never updated once
  written; a re-save with the same key replaces the row atomically).
* ``provider`` / ``model`` / ``input_tokens`` / ``output_tokens`` record *how*
  each artifact was produced so usage accounting is never fabricated.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_artifacts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_key", sa.Text(), nullable=False),
        sa.Column("artifact_type", sa.Text(), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("source_fingerprint", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "generated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_type",
            "entity_key",
            "artifact_type",
            "window_days",
            "source_fingerprint",
            "prompt_version",
            "model",
            name="uq_ai_artifacts_cache_key",
        ),
    )
    op.create_index(
        "ix_ai_artifacts_entity",
        "ai_artifacts",
        ["entity_type", "entity_key"],
        unique=False,
    )
    op.create_index(
        "ix_ai_artifacts_artifact_type",
        "ai_artifacts",
        ["artifact_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_artifacts_artifact_type", table_name="ai_artifacts"
    )
    op.drop_index("ix_ai_artifacts_entity", table_name="ai_artifacts")
    op.drop_table("ai_artifacts")