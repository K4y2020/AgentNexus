"""add coordination message limits

Revision ID: za5b2c4d5e6f
Revises: za4b2c4d5e6f
Create Date: 2026-09-02 00:00:00.000000

Adds durable hop budget and TTL columns to ``agent_messages`` so the
coordination control plane can enforce plan §14.2 limits after a restart
instead of relying on in-memory request state.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "za5b2c4d5e6f"
down_revision: str | None = "za4b2c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add hop/TTL columns idempotently."""
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agent_messages")}
    if "hop_count" not in existing:
        op.add_column(
            "agent_messages",
            sa.Column("hop_count", sa.Integer(), nullable=False, server_default="0"),
        )
    if "max_hops" not in existing:
        op.add_column(
            "agent_messages",
            sa.Column("max_hops", sa.Integer(), nullable=False, server_default="8"),
        )
    if "ttl_seconds" not in existing:
        op.add_column(
            "agent_messages",
            sa.Column("ttl_seconds", sa.Float(), nullable=True),
        )


def downgrade() -> None:
    """Drop hop/TTL columns."""
    with op.batch_alter_table("agent_messages") as batch_op:
        batch_op.drop_column("hop_count")
        batch_op.drop_column("max_hops")
        batch_op.drop_column("ttl_seconds")
