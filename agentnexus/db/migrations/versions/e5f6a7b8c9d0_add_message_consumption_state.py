"""add message consumption state

Revision ID: e5f6a7b8c9d0
Revises: c3d4e5f6a7b8
Create Date: 2026-09-01 00:00:00.000000

Separates the delivery state of an A2A message from its consumption state:
``injected`` only proves the Adapter accepted it, while ``consumed`` /
``acknowledged`` / ``rejected`` require an explicit receipt from the target
agent so the UI never claims that a queued or injected message was read.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add consumption state/receipt columns to agent_messages."""
    op.add_column(
        "agent_messages",
        sa.Column(
            "consumption_state",
            sa.String(length=16),
            nullable=False,
            server_default="unconsumed",
        ),
    )
    op.add_column(
        "agent_messages",
        sa.Column("consumption_receipt_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_messages",
        sa.Column("consumed_at", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    """Drop consumption state/receipt columns from agent_messages."""
    with op.batch_alter_table("agent_messages") as batch_op:
        batch_op.drop_column("consumed_at")
        batch_op.drop_column("consumption_receipt_json")
        batch_op.drop_column("consumption_state")
