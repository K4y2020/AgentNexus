"""add model_fact item type

Revision ID: za6b2c4d5e6f
Revises: za5b2c4d5e6f
Create Date: 2026-09-02 00:00:00.000000

``model_fact`` is the next stable integer code in
``omnigent.db.enum_codecs.ITEM_TYPE`` (12). The type column's CHECK was
created by the enums-to-smallint migration with codes 1-11, so admit the
new code before the relay can persist per-turn model facts.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "za6b2c4d5e6f"
down_revision: str | None = "za5b2c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK_NAME = "ck_conversation_items_type"
_WITH_MODEL_FACT = "type IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)"
_WITHOUT_MODEL_FACT = "type IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)"


def upgrade() -> None:
    """Admit code 12 in the conversation item type CHECK."""
    with op.batch_alter_table("conversation_items") as batch_op:
        batch_op.drop_constraint(_CHECK_NAME, type_="check")
        batch_op.create_check_constraint(_CHECK_NAME, _WITH_MODEL_FACT)


def downgrade() -> None:
    """Restore the pre-model-fact CHECK (no code 12)."""
    with op.batch_alter_table("conversation_items") as batch_op:
        batch_op.drop_constraint(_CHECK_NAME, type_="check")
        batch_op.create_check_constraint(_CHECK_NAME, _WITHOUT_MODEL_FACT)
