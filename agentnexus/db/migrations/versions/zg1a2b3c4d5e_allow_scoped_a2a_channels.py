"""allow multiple Bot A2A sessions when each has a channel scope

Revision ID: zg1a2b3c4d5e
Revises: zf1a2b3c4d5e
Create Date: 2026-09-06 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "zg1a2b3c4d5e"
down_revision: str | None = "zf1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD_CHECK = (
    "(singleton_slot IS NULL AND purpose NOT IN ('primary', 'a2a')) OR "
    "(singleton_slot = purpose AND purpose IN ('primary', 'a2a'))"
)
_NEW_CHECK = (
    "(singleton_slot IS NULL AND purpose != 'primary') OR "
    "(singleton_slot = purpose AND purpose IN ('primary', 'a2a'))"
)


def upgrade() -> None:
    """Permit scoped ``purpose='a2a'`` rows with a NULL singleton slot."""
    with op.batch_alter_table("agentnexus_conversation_metadata") as batch:
        batch.drop_constraint("ck_conversation_metadata_singleton_slot", type_="check")
        batch.create_check_constraint("ck_conversation_metadata_singleton_slot", _NEW_CHECK)


def downgrade() -> None:
    """Restore the singleton-only A2A invariant when no scoped rows remain."""
    bind = op.get_bind()
    scoped_count = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM agentnexus_conversation_metadata "
        "WHERE purpose = 'a2a' AND singleton_slot IS NULL"
    ).scalar_one()
    if scoped_count:
        raise RuntimeError("cannot downgrade while scoped A2A channels still exist")
    with op.batch_alter_table("agentnexus_conversation_metadata") as batch:
        batch.drop_constraint("ck_conversation_metadata_singleton_slot", type_="check")
        batch.create_check_constraint("ck_conversation_metadata_singleton_slot", _OLD_CHECK)
