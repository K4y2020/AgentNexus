"""enforce NOT NULL on idempotency_key and add error_code to delivery_attempts

Revision ID: ze1a2b3c4d5e
Revises: zd1a2b3c4d5e
Create Date: 2026-09-05 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ze1a2b3c4d5e"
down_revision: str | None = "zd1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Backfill any remaining null idempotency keys just in case
    rows = bind.execute(
        sa.text(
            "SELECT workspace_id, message_id FROM agent_messages WHERE idempotency_key IS NULL"
        )
    ).mappings()
    for row in rows:
        bind.execute(
            sa.text(
                "UPDATE agent_messages SET idempotency_key = :key "
                "WHERE workspace_id = :workspace_id AND message_id = :message_id "
                "AND idempotency_key IS NULL"
            ),
            {
                "key": f"legacy:{row['message_id']}",
                "workspace_id": row["workspace_id"],
                "message_id": row["message_id"],
            },
        )

    # 2. Enforce NOT NULL on agent_messages.idempotency_key
    with op.batch_alter_table("agent_messages") as batch_op:
        batch_op.alter_column(
            "idempotency_key",
            existing_type=sa.String(128),
            nullable=False,
        )

    # 3. Add error_code column to delivery_attempts
    with op.batch_alter_table("delivery_attempts") as batch_op:
        batch_op.add_column(sa.Column("error_code", sa.String(64), nullable=True))

    # 4. Backfill existing delivery_attempts error_code from error blob
    try:
        import zstandard

        d_rows = bind.execute(
            sa.text("SELECT attempt_id, error FROM delivery_attempts WHERE error IS NOT NULL")
        ).mappings()
        for d_row in d_rows:
            err = d_row["error"]
            err_text = ""
            if isinstance(err, bytes):
                try:
                    err_text = (
                        zstandard.ZstdDecompressor()
                        .decompress(err[2:])
                        .decode("utf-8", errors="ignore")
                    )
                except Exception:
                    err_text = str(err)
            elif isinstance(err, str):
                err_text = err

            code = "UNKNOWN_ERROR"
            if "not found" in err_text.lower():
                code = "CONVERSATION_NOT_FOUND"
            elif "not bound to a runner" in err_text.lower() or "unbound" in err_text.lower():
                code = "RUNNER_UNBOUND"
            elif (
                "is offline for conversation" in err_text.lower()
                or "runner offline" in err_text.lower()
            ):
                code = "RUNNER_OFFLINE"
            elif "rejected" in err_text.lower():
                code = "RUNNER_REJECTED"
            elif "delivery-unreachable" in err_text.lower():
                code = "DELIVERY_UNREACHABLE"

            bind.execute(
                sa.text(
                    "UPDATE delivery_attempts SET error_code = :code "
                    "WHERE attempt_id = :attempt_id"
                ),
                {"code": code, "attempt_id": d_row["attempt_id"]},
            )
    except Exception:
        # Non-fatal if backfill fails in environments without zstandard
        pass


def downgrade() -> None:
    with op.batch_alter_table("delivery_attempts") as batch_op:
        batch_op.drop_column("error_code")

    with op.batch_alter_table("agent_messages") as batch_op:
        batch_op.alter_column(
            "idempotency_key",
            existing_type=sa.String(128),
            nullable=True,
        )
