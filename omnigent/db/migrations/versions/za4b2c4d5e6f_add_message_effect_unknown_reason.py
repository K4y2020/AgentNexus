"""add message effect_unknown_reason

Revision ID: za4b2c4d5e6f
Revises: za3b2c4d5e6f
Create Date: 2026-09-01 00:00:00.000000

Adds the ``effect_unknown_reason`` marker to ``agent_messages`` so the
control plane can reconcile "delivered into a runner but never confirmed as
consumed" messages without guessing: the reason is set once by the
reconciler and remains visible in the message, run summary, and timeline.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "za4b2c4d5e6f"
down_revision: str | None = "za3b2c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the effect_unknown_reason column (idempotent)."""
    existing = {
        c["name"] for c in sa.inspect(op.get_bind()).get_columns("agent_messages")
    }
    if "effect_unknown_reason" not in existing:
        op.add_column(
            "agent_messages",
            sa.Column("effect_unknown_reason", sa.String(length=512), nullable=True),
        )


def downgrade() -> None:
    """Remove the effect_unknown_reason column."""
    with op.batch_alter_table("agent_messages") as batch_op:
        batch_op.drop_column("effect_unknown_reason")
