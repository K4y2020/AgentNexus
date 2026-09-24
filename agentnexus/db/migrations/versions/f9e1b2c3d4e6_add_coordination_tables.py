"""add coordination and workspace lease tables

Revision ID: f9e1b2c3d4e6
Revises: e5d9bc8ac650
Create Date: 2026-09-01 00:00:00.000000

Moves the Multi-Agent Coordination control plane out of a side-channel
native sqlite3 schema into the project's main SQLAlchemy/Alembic lineage.
Adds durable runs, tasks, A2A messages, outbox, delivery attempts, audit
events, and persistent workspace leases, all tenant-partitioned by the
existing ``workspace_id`` column convention.

The migration is idempotent for databases that still carry the earlier
side-channel tables: each legacy table is renamed, recreated with the
canonical ``workspace_id`` composite primary key, and copied into the new
schema before the old table is dropped. This is what lets a server upgrade
preserve existing runs/messages/outbox instead of failing on a name
collision.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "f9e1b2c3d4e6"
down_revision: str | None = "e5d9bc8ac650"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _workspace_id() -> sa.Column:
    """Tenant partition key matching every operational AgentNexus table."""
    return sa.Column(
        "workspace_id",
        sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
        primary_key=True,
        nullable=False,
        server_default="0",
    )


def _json_text(name: str, default: str) -> sa.Column:
    """MySQL cannot assign a literal default to a TEXT column."""
    mysql = op.get_bind().dialect.name == "mysql"
    return sa.Column(name, sa.Text(), nullable=False, server_default=None if mysql else default)


def _table_exists(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _index_exists(table_name: str, index_name: str) -> bool:
    return any(
        idx.get("name") == index_name for idx in sa.inspect(op.get_bind()).get_indexes(table_name)
    )


def _has_workspace_id(name: str) -> bool:
    if not _table_exists(name):
        return False
    return any(
        col["name"] == "workspace_id" for col in sa.inspect(op.get_bind()).get_columns(name)
    )


def _atomic_columns() -> list[str]:
    return [
        "run_id",
        "title",
        "root_session_id",
        "template",
        "status",
        "budget_json",
        "metadata_json",
        "created_at",
        "updated_at",
    ]


def _task_columns() -> list[str]:
    return [
        "task_id",
        "run_id",
        "title",
        "status",
        "assignee_session_id",
        "assignee_role",
        "dependencies_json",
        "artifacts_json",
        "created_at",
        "updated_at",
    ]


def _message_columns() -> list[str]:
    return [
        "message_id",
        "schema_version",
        "root_session_id",
        "run_id",
        "task_id",
        "sender_session_id",
        "sender_role",
        "recipient_session_id",
        "recipient_role",
        "kind",
        "intent",
        "payload_json",
        "artifacts_json",
        "correlation_id",
        "in_reply_to",
        "idempotency_key",
        "message_state",
        "created_at",
        "updated_at",
    ]


def _outbox_columns() -> list[str]:
    return [
        "item_id",
        "message_id",
        "target_session_id",
        "status",
        "payload_json",
        "retry_count",
        "next_retry_at",
        "created_at",
        "updated_at",
    ]


def _attempt_columns() -> list[str]:
    return [
        "attempt_id",
        "message_id",
        "target_session_id",
        "target_harness",
        "delivery_mode",
        "delivery_state",
        "injection_receipt_json",
        "error",
        "attempt_count",
        "created_at",
        "updated_at",
    ]


def _event_columns() -> list[str]:
    return [
        "event_id",
        "root_session_id",
        "run_id",
        "task_id",
        "actor_session_id",
        "event_type",
        "payload_json",
        "created_at",
    ]


def _lease_columns() -> list[str]:
    return [
        "lease_id",
        "workspace_path",
        "holder_session_id",
        "mode",
        "fencing_token",
        "status",
        "acquired_at",
        "expires_at",
        "updated_at",
    ]


_COLUMN_MAP: dict[str, list[str]] = {
    "coordination_runs": _atomic_columns(),
    "coordination_tasks": _task_columns(),
    "agent_messages": _message_columns(),
    "coordination_outbox": _outbox_columns(),
    "delivery_attempts": _attempt_columns(),
    "coordination_events": _event_columns(),
    "workspace_leases": _lease_columns(),
}

# New tables are created first; legacy carriers are then moved into them.
_TABLE_SPECS: dict[str, tuple[list[sa.Column], list[tuple[str, list[str]]]]] = {}


def _ensure_table(
    name: str,
    columns: list[sa.Column],
    indexes: list[tuple[str, list[str]]],
    *,
    unique: bool = False,
    sqlite_where: sa.TextClause | None = None,
    postgresql_where: sa.TextClause | None = None,
) -> None:
    """Create ``name`` if absent, otherwise migrate a legacy equivalent."""
    if not _table_exists(name):
        op.create_table(name, *columns)
    elif not _has_workspace_id(name):
        legacy_name = f"{name}_legacy"
        op.rename_table(name, legacy_name)
        op.create_table(name, *columns)
        column_names = _COLUMN_MAP[name]
        select_list = ", ".join(column_names)
        op.execute(
            sa.text(
                f"INSERT INTO {name} (workspace_id, {select_list}) "
                f"SELECT 0, {select_list} FROM {legacy_name}"
            )
        )
        op.drop_table(legacy_name)
    for index_name, index_columns in indexes:
        if _index_exists(name, index_name):
            continue
        kwargs: dict[str, Any] = {}
        if unique:
            kwargs["unique"] = True
        if sqlite_where is not None:
            kwargs["sqlite_where"] = sqlite_where
        if postgresql_where is not None:
            kwargs["postgresql_where"] = postgresql_where
        op.create_index(index_name, name, index_columns, **kwargs)


def _coordination_tables() -> None:
    _ensure_table(
        "coordination_runs",
        [
            _workspace_id(),
            sa.Column("run_id", sa.String(length=64), primary_key=True),
            sa.Column("title", sa.String(length=512), nullable=False),
            sa.Column("root_session_id", sa.String(length=128), nullable=False),
            sa.Column("template", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            _json_text("budget_json", "{}"),
            _json_text("metadata_json", "{}"),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        ],
        [("ix_coord_runs_root", ["workspace_id", "root_session_id", "created_at"])],
    )

    _ensure_table(
        "coordination_tasks",
        [
            _workspace_id(),
            sa.Column("task_id", sa.String(length=64), primary_key=True),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("title", sa.String(length=512), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("assignee_session_id", sa.String(length=128), nullable=True),
            sa.Column("assignee_role", sa.String(length=64), nullable=True),
            _json_text("dependencies_json", "[]"),
            _json_text("artifacts_json", "[]"),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        ],
        [("ix_coord_tasks_run", ["workspace_id", "run_id", "status"])],
    )

    _ensure_table(
        "agent_messages",
        [
            _workspace_id(),
            sa.Column("message_id", sa.String(length=64), primary_key=True),
            sa.Column("schema_version", sa.String(length=32), nullable=False),
            sa.Column("root_session_id", sa.String(length=128), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=True),
            sa.Column("task_id", sa.String(length=64), nullable=True),
            sa.Column("sender_session_id", sa.String(length=128), nullable=False),
            sa.Column("sender_role", sa.String(length=64), nullable=False),
            sa.Column("recipient_session_id", sa.String(length=128), nullable=False),
            sa.Column("recipient_role", sa.String(length=64), nullable=True),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("intent", sa.String(length=64), nullable=False),
            _json_text("payload_json", "{}"),
            _json_text("artifacts_json", "[]"),
            sa.Column("correlation_id", sa.String(length=128), nullable=True),
            sa.Column("in_reply_to", sa.String(length=128), nullable=True),
            sa.Column("idempotency_key", sa.String(length=128), nullable=True),
            sa.Column("message_state", sa.String(length=16), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        ],
        [
            (
                "idx_agent_msg_root",
                [
                    "workspace_id",
                    "root_session_id",
                    "recipient_session_id",
                    "created_at",
                ],
            ),
        ],
    )
    _ensure_table(
        "agent_messages",
        [],
        [
            (
                "uq_agent_msg_idempotency",
                ["workspace_id", "root_session_id", "idempotency_key"],
            )
        ],
        unique=True,
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    _ensure_table(
        "coordination_outbox",
        [
            _workspace_id(),
            sa.Column("item_id", sa.String(length=64), primary_key=True),
            sa.Column("message_id", sa.String(length=64), nullable=False),
            sa.Column("target_session_id", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("next_retry_at", sa.Float(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        ],
        [("idx_coord_outbox_pending", ["workspace_id", "status", "next_retry_at"])],
    )

    _ensure_table(
        "delivery_attempts",
        [
            _workspace_id(),
            sa.Column("attempt_id", sa.String(length=64), primary_key=True),
            sa.Column("message_id", sa.String(length=64), nullable=False),
            sa.Column("target_session_id", sa.String(length=128), nullable=False),
            sa.Column("target_harness", sa.String(length=64), nullable=True),
            sa.Column("delivery_mode", sa.String(length=32), nullable=False),
            sa.Column("delivery_state", sa.String(length=16), nullable=False),
            sa.Column("injection_receipt_json", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        ],
        [("idx_delivery_attempts_msg", ["workspace_id", "message_id", "created_at"])],
    )

    _ensure_table(
        "coordination_events",
        [
            _workspace_id(),
            sa.Column("event_id", sa.String(length=64), primary_key=True),
            sa.Column("root_session_id", sa.String(length=128), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=True),
            sa.Column("task_id", sa.String(length=64), nullable=True),
            sa.Column("actor_session_id", sa.String(length=128), nullable=True),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            _json_text("payload_json", "{}"),
            sa.Column("created_at", sa.Float(), nullable=False),
        ],
        [("idx_coord_events_root", ["workspace_id", "root_session_id", "created_at"])],
    )

    _ensure_table(
        "workspace_leases",
        [
            _workspace_id(),
            sa.Column("lease_id", sa.String(length=64), primary_key=True),
            sa.Column("workspace_path", sa.String(length=2048), nullable=False),
            sa.Column("holder_session_id", sa.String(length=128), nullable=False),
            sa.Column("mode", sa.String(length=8), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("acquired_at", sa.Float(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        ],
        [("ix_workspace_leases_path", ["workspace_id", "workspace_path", "status"])],
    )


def upgrade() -> None:
    """Create the coordination and workspace lease tables (legacy-safe)."""
    _coordination_tables()


def downgrade() -> None:
    """Drop the coordination and workspace lease tables."""
    for table in (
        "workspace_leases",
        "coordination_events",
        "delivery_attempts",
        "coordination_outbox",
        "agent_messages",
        "coordination_tasks",
        "coordination_runs",
    ):
        if _table_exists(table):
            op.drop_table(table)
