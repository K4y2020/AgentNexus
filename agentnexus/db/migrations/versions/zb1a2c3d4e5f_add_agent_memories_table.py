"""add agent_memories table

Revision ID: zb1a2c3d4e5f
Revises: za6b2c4d5e6f
Create Date: 2026-09-02 00:00:00.000000

Adds the ``agent_memories`` table that backs persistent teammate memory. The
table follows the tenant-partition pattern used by every app table and keeps
``agent_id`` as an application-owned relationship (no DB foreign key, Rule
R032).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from agentnexus.db.db_models import Uuid16

revision: str = "zb1a2c3d4e5f"
down_revision: str | None = "za6b2c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ``agent_memories`` table."""
    op.create_table(
        "agent_memories",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        # UUID PK stored as 16 raw bytes (Uuid16 → BINARY(16) on MySQL,
        # BLOB/BYTEA elsewhere).
        sa.Column("id", Uuid16(), nullable=False),
        # Relates to agents.id. No DB foreign key (Rule R032).
        sa.Column("agent_id", Uuid16(), nullable=False),
        # Opaque free text stored compressed (CompressedText → LargeBinary).
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_agent_memories_agent_scope",
        "agent_memories",
        ["workspace_id", "agent_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the ``agent_memories`` table."""
    op.drop_index("ix_agent_memories_agent_scope", table_name="agent_memories")
    op.drop_table("agent_memories")
