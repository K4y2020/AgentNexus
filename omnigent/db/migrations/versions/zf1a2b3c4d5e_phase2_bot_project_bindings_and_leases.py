"""Phase 2: bot_project_bindings and computer_execution_leases

Revision ID: zf1a2b3c4d5e
Revises: ze1a2b3c4d5e
Create Date: 2026-09-05 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import CompressedText, Uuid16

revision: str = "zf1a2b3c4d5e"
down_revision: str | None = "ze1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. bot_project_bindings table
    op.create_table(
        "bot_project_bindings",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("bot_id", Uuid16(), nullable=False),
        sa.Column("project_id", Uuid16(), nullable=False),
        sa.Column("checkout_root", sa.String(length=2048), nullable=False),
        sa.Column("default_branch", sa.String(length=256), nullable=False, server_default="main"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "bot_id", "project_id", name="uq_bot_project_bindings_bot_project"),
    )
    op.create_index(
        "ix_bot_project_bindings_bot",
        "bot_project_bindings",
        ["workspace_id", "bot_id", "id"],
        unique=False,
    )

    # 2. computer_execution_leases table
    op.create_table(
        "computer_execution_leases",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("computer_id", sa.String(length=128), nullable=False),
        sa.Column("bot_id", Uuid16(), nullable=False),
        sa.Column("session_id", Uuid16(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(length=2048), nullable=False),
        sa.Column("fence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_computer_execution_leases_path",
        "computer_execution_leases",
        ["workspace_id", "path", "expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_computer_execution_leases_run",
        "computer_execution_leases",
        ["workspace_id", "run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_computer_execution_leases_run", table_name="computer_execution_leases")
    op.drop_index("ix_computer_execution_leases_path", table_name="computer_execution_leases")
    op.drop_table("computer_execution_leases")

    op.drop_index("ix_bot_project_bindings_bot", table_name="bot_project_bindings")
    op.drop_table("bot_project_bindings")
