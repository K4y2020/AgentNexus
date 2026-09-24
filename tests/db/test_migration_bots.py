"""Schema contract for persistent Bots and Session ownership."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, text

from agentnexus.db.utils import _build_alembic_config


@pytest.fixture()
def db_engine(tmp_path: Path) -> Engine:
    from sqlalchemy import create_engine

    path = tmp_path / "bots-migration.db"
    uri = f"sqlite:///{path.as_posix()}"
    config = _build_alembic_config(uri)
    command.upgrade(config, "head")
    engine = create_engine(uri)
    try:
        yield engine
    finally:
        engine.dispose()


def test_migration_creates_bots_bindings_and_session_columns(db_engine: Engine) -> None:
    inspector = inspect(db_engine)
    assert {"bots", "bot_computer_bindings"}.issubset(inspector.get_table_names())
    columns = {
        column["name"] for column in inspector.get_columns("agentnexus_conversation_metadata")
    }
    assert {"bot_id", "purpose", "singleton_slot"}.issubset(columns)
    unique_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("agentnexus_conversation_metadata")
    }
    assert "uq_conversation_metadata_bot_singleton" in unique_names


def test_migration_downgrade_and_reupgrade(tmp_path: Path) -> None:
    from sqlalchemy import create_engine

    path = tmp_path / "bots-roundtrip.db"
    uri = f"sqlite:///{path.as_posix()}"
    config = _build_alembic_config(uri)
    command.upgrade(config, "head")
    command.downgrade(config, "zb1a2c3d4e5f")
    engine = create_engine(uri)
    try:
        inspector = inspect(engine)
        assert "bots" not in inspector.get_table_names()
        columns = {
            column["name"] for column in inspector.get_columns("agentnexus_conversation_metadata")
        }
        assert "bot_id" not in columns
    finally:
        engine.dispose()
    command.upgrade(config, "head")


def test_coordination_idempotency_backfill(tmp_path: Path) -> None:
    from sqlalchemy import create_engine

    path = tmp_path / "coordination-idempotency.db"
    uri = f"sqlite:///{path.as_posix()}"
    config = _build_alembic_config(uri)
    command.upgrade(config, "zc1a2b3c4d5e")
    engine = create_engine(uri)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO agent_messages "
                "(workspace_id, message_id, schema_version, root_session_id, "
                "sender_session_id, sender_role, recipient_session_id, kind, intent, "
                "payload_json, artifacts_json, message_state, consumption_state, "
                "hop_count, max_hops, created_at, updated_at) VALUES "
                "(0, 'msg_legacy', '1', 'root', 'sender', 'planner', 'target', "
                "'content', 'task.request', X'00007B7D', X'00005B5D', 'queued', "
                "'unconsumed', 0, 8, 1, 1)"
            )
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(uri)
    try:
        with engine.connect() as connection:
            key = connection.execute(
                text("SELECT idempotency_key FROM agent_messages WHERE message_id='msg_legacy'")
            ).scalar_one()
        assert key == "legacy:msg_legacy"
    finally:
        engine.dispose()
