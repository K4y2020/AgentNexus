"""add coordination artifacts

Revision ID: b7c8d9e0f1a2
Revises: a9b8c7d6e5f4
Create Date: 2026-09-01 00:00:00.000000

Persists control-plane artifact metadata (plan, patch, diff, report, test
result) as durable rows linked to runs/tasks, so messages and workflow stages
can trace artifacts by id instead of embedding full blobs in JSON.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "a9b8c7d6e5f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the coordination artifact metadata table."""
    op.create_table(
        "coordination_artifacts",
        sa.Column(
            "workspace_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            nullable=False,
            server_default="0",
        ),
        sa.Column("artifact_id", sa.String(length=64), primary_key=True),
        sa.Column("root_session_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("producer_session_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=True),
        sa.Column("uri", sa.String(length=2048), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="published",
        ),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Index(
            "ix_coordination_artifacts_root",
            "workspace_id",
            "root_session_id",
            "created_at",
        ),
    )


def downgrade() -> None:
    """Drop the coordination artifact metadata table."""
    op.drop_table("coordination_artifacts")
