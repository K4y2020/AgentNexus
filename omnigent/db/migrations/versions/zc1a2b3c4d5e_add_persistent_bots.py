"""add persistent bots and session ownership

Revision ID: zc1a2b3c4d5e
Revises: zb1a2c3d4e5f
Create Date: 2026-09-04 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "zc1a2b3c4d5e"
down_revision: str | None = "zb1a2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bots",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("agent_id", Uuid16(), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("default_model", sa.String(256), nullable=True),
        sa.Column("behavior_mode", sa.String(16), nullable=False, server_default="off"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_bots_status"),
        sa.CheckConstraint(
            "behavior_mode IN ('off', 'advisory', 'lean', 'strict')",
            name="ck_bots_behavior_mode",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_bots_owner", "bots", ["workspace_id", "owner_id", "status", "created_at", "id"]
    )
    op.create_index(
        "ix_bots_agent", "bots", ["workspace_id", "owner_id", "agent_id", "id"]
    )
    op.create_table(
        "bot_computer_bindings",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("bot_id", Uuid16(), nullable=False),
        sa.Column("host_id", Uuid16(), nullable=True),
        sa.Column("home_path", sa.String(2048), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.UniqueConstraint(
            "workspace_id", "bot_id", name="uq_bot_computer_bindings_bot"
        ),
    )

    with op.batch_alter_table("omnigent_conversation_metadata") as batch:
        batch.add_column(sa.Column("bot_id", Uuid16(), nullable=True))
        batch.add_column(
            sa.Column("purpose", sa.String(16), nullable=False, server_default="standalone")
        )
        batch.add_column(sa.Column("singleton_slot", sa.String(16), nullable=True))
        batch.create_check_constraint(
            "ck_conversation_metadata_purpose",
            "purpose IN ('primary', 'topic', 'routine', 'a2a', 'subagent', 'standalone')",
        )
        batch.create_check_constraint(
            "ck_conversation_metadata_singleton_slot",
            "(singleton_slot IS NULL AND purpose NOT IN ('primary', 'a2a')) OR "
            "(singleton_slot = purpose AND purpose IN ('primary', 'a2a'))",
        )
        batch.create_index(
            "ix_conversation_metadata_bot_id",
            ["workspace_id", "bot_id", "purpose", "id"],
            unique=False,
        )
        batch.create_unique_constraint(
            "uq_conversation_metadata_bot_singleton",
            ["workspace_id", "bot_id", "singleton_slot"],
        )

    op.execute(
        "UPDATE omnigent_conversation_metadata SET purpose = 'subagent' WHERE kind = 2"
    )


def downgrade() -> None:
    with op.batch_alter_table("omnigent_conversation_metadata") as batch:
        batch.drop_constraint("uq_conversation_metadata_bot_singleton", type_="unique")
        batch.drop_index("ix_conversation_metadata_bot_id")
        batch.drop_constraint("ck_conversation_metadata_singleton_slot", type_="check")
        batch.drop_constraint("ck_conversation_metadata_purpose", type_="check")
        batch.drop_column("singleton_slot")
        batch.drop_column("purpose")
        batch.drop_column("bot_id")
    op.drop_table("bot_computer_bindings")
    op.drop_index("ix_bots_agent", table_name="bots")
    op.drop_index("ix_bots_owner", table_name="bots")
    op.drop_table("bots")
