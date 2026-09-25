"""add coordination task scheduling fields

Revision ID: za3b2c4d5e6f
Revises: e5f6a7b8c9d0
Create Date: 2026-09-01 00:00:00.000000

Adds per-task acceptance criteria and a hard deadline to ``coordination_tasks``
so the P4 workflow scheduler can bind stages to explicit acceptance evidence
and stop dispatch when a stage deadline passes, alongside the existing
run-level ``budget.deadline_s``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "za3b2c4d5e6f"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add scheduling fields to coordination_tasks (idempotent)."""
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("coordination_tasks")}
    if "acceptance_json" not in existing:
        mysql = op.get_bind().dialect.name == "mysql"
        op.add_column(
            "coordination_tasks",
            sa.Column(
                "acceptance_json",
                sa.Text(),
                nullable=mysql,
                server_default=None if mysql else "[]",
            ),
        )
        if mysql:
            op.execute(sa.text("UPDATE coordination_tasks SET acceptance_json = '[]'"))
            with op.batch_alter_table("coordination_tasks") as batch_op:
                batch_op.alter_column(
                    "acceptance_json",
                    existing_type=sa.Text(),
                    nullable=False,
                )
    if "deadline" not in existing:
        op.add_column(
            "coordination_tasks",
            sa.Column("deadline", sa.Float(), nullable=True),
        )


def downgrade() -> None:
    """Remove scheduling fields from coordination_tasks."""
    with op.batch_alter_table("coordination_tasks") as batch_op:
        batch_op.drop_column("deadline")
        batch_op.drop_column("acceptance_json")
