"""add workspace merge operations

Revision ID: a9b8c7d6e5f4
Revises: f9e1b2c3d4e6
Create Date: 2026-09-01 00:00:00.000000

Persists user-confirmed git merge transactions with expected branch heads,
worktree dirty hash, fencing token, preview and result so a preview cannot be
executed after the repository advanced or changed underneath it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9b8c7d6e5f4"
down_revision: str | None = "f9e1b2c3d4e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the workspace merge operation table."""
    op.create_table(
        "workspace_merge_operations",
        sa.Column(
            "workspace_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            nullable=False,
            server_default="0",
        ),
        sa.Column("operation_id", sa.String(length=64), primary_key=True),
        sa.Column("root_session_id", sa.String(length=128), nullable=False),
        sa.Column("holder_session_id", sa.String(length=128), nullable=False),
        sa.Column("repo_path", sa.String(length=2048), nullable=False),
        sa.Column("source_branch", sa.String(length=256), nullable=False),
        sa.Column("target_branch", sa.String(length=256), nullable=False),
        sa.Column("expected_source_head", sa.String(length=64), nullable=True),
        sa.Column("expected_target_head", sa.String(length=64), nullable=True),
        sa.Column("dirty_hash", sa.String(length=64), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="preview",
        ),
        sa.Column(
            "preview_json",
            sa.Text(),
            nullable=False,
            server_default=None if op.get_bind().dialect.name == "mysql" else "{}",
        ),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Index(
            "ix_workspace_merge_ops_root",
            "workspace_id",
            "root_session_id",
            "created_at",
        ),
    )


def downgrade() -> None:
    """Drop the workspace merge operation table."""
    op.drop_table("workspace_merge_operations")
