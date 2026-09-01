"""add target sequence to bus tables

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
Create Date: 2026-09-01 00:00:00.000000

Adds a per-target monotonic sequence allocated when a message is queued, so
delivery attempts can be traced in target order without relying on global
created_at ordering across recipients.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add target_sequence to outbox and delivery attempt tables."""
    op.add_column(
        "coordination_outbox",
        sa.Column("target_sequence", sa.Integer(), nullable=True),
    )
    op.add_column(
        "delivery_attempts",
        sa.Column("target_sequence", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Drop target_sequence from outbox and delivery attempt tables."""
    op.drop_column("coordination_outbox", "target_sequence")
    op.drop_column("delivery_attempts", "target_sequence")
