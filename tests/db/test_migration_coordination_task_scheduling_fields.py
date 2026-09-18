"""Tests for the coordination task scheduling fields migration (za3b2c4d5e6f).

P4 binds each stage task to explicit acceptance criteria and a hard
deadline. The migration adds ``acceptance_json`` (NOT NULL, defaults to
``[]``) and nullable ``deadline`` to ``coordination_tasks`` while keeping
the run-level ``budget_json`` scheduling fields unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.engine import Engine

from agentnexus.db.utils import (
    _build_alembic_config,
    clear_engine_cache,
    get_or_create_engine,
)

_PRIOR_REVISION = "e5f6a7b8c9d0"


@pytest.fixture
def db_engine(tmp_path: Path) -> Iterator[Engine]:
    """Fresh SQLite DB with the full Alembic chain applied; cleaned up after."""
    db_path = tmp_path / "test.db"
    uri = f"sqlite:///{db_path}"
    engine = get_or_create_engine(uri)
    try:
        yield engine
    finally:
        clear_engine_cache()


def test_scheduling_columns_exist_at_head(db_engine: Engine) -> None:
    """Both scheduling columns are present at head with the right nullability."""
    cols = {c["name"]: c for c in sa.inspect(db_engine).get_columns("coordination_tasks")}
    assert "acceptance_json" in cols, "acceptance_json must exist at head"
    assert "deadline" in cols, "deadline must exist at head"
    assert not cols["acceptance_json"]["nullable"], "acceptance_json must be NOT NULL"
    assert cols["deadline"]["nullable"], "deadline must remain nullable"


def test_scheduling_columns_round_trip(db_engine: Engine) -> None:
    """Raw SQL round-trips acceptance criteria and a deadline value."""
    with db_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO coordination_tasks "
                "(task_id, run_id, title, status, created_at, updated_at, "
                " acceptance_json, deadline) "
                "VALUES (:task_id, :run_id, :title, :status, :created_at, "
                ":updated_at, :acceptance_json, :deadline)"
            ),
            {
                "task_id": "task_sched_round",
                "run_id": "run_sched_round",
                "title": "Scheduled round trip",
                "status": "running",
                "created_at": 1.0,
                "updated_at": 1.0,
                "acceptance_json": '["tests pass", "audit log written"]',
                "deadline": 1234567890.0,
            },
        )
        row = conn.execute(
            sa.text(
                "SELECT acceptance_json, deadline FROM coordination_tasks WHERE task_id = :task_id"
            ),
            {"task_id": "task_sched_round"},
        ).one()
    assert row[0] == '["tests pass", "audit log written"]'
    assert row[1] == 1234567890.0


def test_upgrade_from_prior_revision_adds_columns_and_defaults(
    tmp_path: Path,
) -> None:
    """Old rows survive the upgrade and get the ``[]`` acceptance default."""
    db_path = tmp_path / "upgrade.db"
    uri = f"sqlite:///{db_path}"
    engine = sa.create_engine(uri)
    cfg = _build_alembic_config(uri)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, _PRIOR_REVISION)
            conn.execute(
                sa.text(
                    "INSERT INTO coordination_tasks "
                    "(task_id, run_id, title, status, created_at, updated_at) "
                    "VALUES (:task_id, :run_id, :title, :status, :created_at, "
                    ":updated_at)"
                ),
                {
                    "task_id": "task_legacy_upgrade",
                    "run_id": "run_legacy_upgrade",
                    "title": "Legacy task",
                    "status": "queued",
                    "created_at": 1.0,
                    "updated_at": 1.0,
                },
            )
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
        engine.dispose()

        cols = {c["name"] for c in sa.inspect(engine).get_columns("coordination_tasks")}
        assert "acceptance_json" in cols
        assert "deadline" in cols
        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT task_id, acceptance_json, deadline FROM coordination_tasks "
                    "WHERE task_id = :task_id"
                ),
                {"task_id": "task_legacy_upgrade"},
            ).one()
        assert row[0] == "task_legacy_upgrade"
        assert row[1] == "[]"
        assert row[2] is None
    finally:
        engine.dispose()
        clear_engine_cache()


def test_downgrade_removes_scheduling_columns_keeps_rows(tmp_path: Path) -> None:
    """Downgrade drops the new columns without losing the task rows."""
    db_path = tmp_path / "downgrade.db"
    uri = f"sqlite:///{db_path}"
    engine = get_or_create_engine(uri)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO coordination_tasks "
                    "(task_id, run_id, title, status, created_at, updated_at, "
                    " acceptance_json, deadline) "
                    "VALUES (:task_id, :run_id, :title, :status, :created_at, "
                    ":updated_at, :acceptance_json, :deadline)"
                ),
                {
                    "task_id": "task_sched_downgrade",
                    "run_id": "run_sched_downgrade",
                    "title": "Downgrade survivor",
                    "status": "running",
                    "created_at": 1.0,
                    "updated_at": 1.0,
                    "acceptance_json": '["must deploy"]',
                    "deadline": 1234567890.0,
                },
            )

        cfg = _build_alembic_config(uri)
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, _PRIOR_REVISION)
        engine.dispose()

        cols = {c["name"] for c in sa.inspect(engine).get_columns("coordination_tasks")}
        assert "acceptance_json" not in cols, "acceptance_json must be dropped"
        assert "deadline" not in cols, "deadline must be dropped"
        with engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT task_id, status FROM coordination_tasks WHERE task_id = :task_id"),
                {"task_id": "task_sched_downgrade"},
            ).one()
        assert row[0] == "task_sched_downgrade"
        assert row[1] == "running"
    finally:
        engine.dispose()
        clear_engine_cache()
