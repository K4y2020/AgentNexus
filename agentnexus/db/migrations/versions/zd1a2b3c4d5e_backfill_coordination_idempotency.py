"""backfill coordination message idempotency keys

Revision ID: zd1a2b3c4d5e
Revises: zc1a2b3c4d5e
Create Date: 2026-09-04 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zd1a2b3c4d5e"
down_revision: str | None = "zc1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Give legacy messages a unique inert key; new writers use logical keys."""
    bind = op.get_bind()
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


def downgrade() -> None:
    """Keep backfilled keys: removing them would re-open duplicate writes."""
