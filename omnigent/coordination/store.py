"""Durable ACID storage for AgentNexus Multi-Agent Coordination Data Layer."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from omnigent.coordination.types import (
    AgentMessage,
    CoordinationEvent,
    CoordinationRun,
    CoordinationTask,
    DeliveryAttempt,
    DeliveryMode,
    DeliveryState,
    MessageKind,
    MessageState,
    OutboxItem,
    RunStatus,
    TaskStatus,
)


def get_default_coordination_db_path() -> Path:
    """Return the canonical database path for coordination entities (~/.omnigent/chat.db)."""
    home = Path.home() / ".omnigent"
    home.mkdir(parents=True, exist_ok=True)
    return home / "chat.db"


class CoordinationStore:
    """Thread-safe SQLite store for Multi-Agent coordination domain entities."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(db_path or get_default_coordination_db_path())
        self._local = threading.local()
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            if self.db_path != ":memory:":
                try:
                    conn.execute("PRAGMA journal_mode=WAL;")
                    conn.execute("PRAGMA busy_timeout=5000;")
                except Exception:
                    pass
            self._local.conn = conn
            self._init_schema(conn)
        return self._local.conn

    def _init_schema(self, conn: sqlite3.Connection | None = None) -> None:
        conn = conn or self._get_conn()
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS coordination_runs (
                    run_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    root_session_id TEXT NOT NULL,
                    template TEXT NOT NULL,
                    status TEXT NOT NULL,
                    budget_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_coord_runs_root
                ON coordination_runs (root_session_id, created_at);
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS coordination_tasks (
                    task_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    assignee_session_id TEXT,
                    assignee_role TEXT,
                    dependencies_json TEXT NOT NULL DEFAULT '[]',
                    artifacts_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_coord_tasks_run
                ON coordination_tasks (run_id, status);
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_messages (
                    message_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    root_session_id TEXT NOT NULL,
                    run_id TEXT,
                    task_id TEXT,
                    sender_session_id TEXT NOT NULL,
                    sender_role TEXT NOT NULL,
                    recipient_session_id TEXT NOT NULL,
                    recipient_role TEXT,
                    kind TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    artifacts_json TEXT NOT NULL DEFAULT '[]',
                    correlation_id TEXT,
                    in_reply_to TEXT,
                    idempotency_key TEXT,
                    message_state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_agent_msg_root
                ON agent_messages (root_session_id, recipient_session_id, created_at);
            """)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_msg_idempotency
                ON agent_messages (root_session_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL;
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS coordination_outbox (
                    item_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    target_session_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_coord_outbox_pending
                ON coordination_outbox (status, next_retry_at);
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS delivery_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    target_session_id TEXT NOT NULL,
                    target_harness TEXT,
                    delivery_mode TEXT NOT NULL,
                    delivery_state TEXT NOT NULL,
                    injection_receipt_json TEXT,
                    error TEXT,
                    attempt_count INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_delivery_attempts_msg
                ON delivery_attempts (message_id, created_at);
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS coordination_events (
                    event_id TEXT PRIMARY KEY,
                    root_session_id TEXT NOT NULL,
                    run_id TEXT,
                    task_id TEXT,
                    actor_session_id TEXT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_coord_events_root
                ON coordination_events (root_session_id, created_at);
            """)

    # ── Run Operations ──────────────────────────────────────────

    def create_run(self, run: CoordinationRun) -> CoordinationRun:
        conn = self._get_conn()
        with conn:
            conn.execute(
                """
                INSERT INTO coordination_runs (
                    run_id, title, root_session_id, template, status,
                    budget_json, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.title,
                    run.root_session_id,
                    run.template,
                    run.status,
                    json.dumps(run.budget),
                    json.dumps(run.metadata),
                    run.created_at,
                    run.updated_at,
                ),
            )
        return run

    def get_run(self, run_id: str) -> CoordinationRun | None:
        conn = self._get_conn()
        cur = conn.execute("SELECT * FROM coordination_runs WHERE run_id = ?", (run_id,))
        row = cur.fetchone()
        if not row:
            return None
        return CoordinationRun(
            run_id=row["run_id"],
            title=row["title"],
            root_session_id=row["root_session_id"],
            template=row["template"],
            status=row["status"],
            budget=json.loads(row["budget_json"]),
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_runs(self, root_session_id: str | None = None) -> list[CoordinationRun]:
        conn = self._get_conn()
        if root_session_id:
            cur = conn.execute(
                "SELECT * FROM coordination_runs WHERE root_session_id = ? ORDER BY created_at DESC",
                (root_session_id,),
            )
        else:
            cur = conn.execute("SELECT * FROM coordination_runs ORDER BY created_at DESC")
        return [
            CoordinationRun(
                run_id=r["run_id"],
                title=r["title"],
                root_session_id=r["root_session_id"],
                template=r["template"],
                status=r["status"],
                budget=json.loads(r["budget_json"]),
                metadata=json.loads(r["metadata_json"]),
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in cur.fetchall()
        ]

    # ── Task Operations ─────────────────────────────────────────

    def create_task(self, task: CoordinationTask) -> CoordinationTask:
        conn = self._get_conn()
        with conn:
            conn.execute(
                """
                INSERT INTO coordination_tasks (
                    task_id, run_id, title, status, assignee_session_id,
                    assignee_role, dependencies_json, artifacts_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    task.run_id,
                    task.title,
                    task.status,
                    task.assignee_session_id,
                    task.assignee_role,
                    json.dumps(task.dependencies),
                    json.dumps(task.artifacts),
                    task.created_at,
                    task.updated_at,
                ),
            )
        return task

    def get_task(self, task_id: str) -> CoordinationTask | None:
        conn = self._get_conn()
        cur = conn.execute("SELECT * FROM coordination_tasks WHERE task_id = ?", (task_id,))
        row = cur.fetchone()
        if not row:
            return None
        return CoordinationTask(
            task_id=row["task_id"],
            run_id=row["run_id"],
            title=row["title"],
            status=row["status"],
            assignee_session_id=row["assignee_session_id"],
            assignee_role=row["assignee_role"],
            dependencies=json.loads(row["dependencies_json"]),
            artifacts=json.loads(row["artifacts_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_tasks(self, run_id: str) -> list[CoordinationTask]:
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT * FROM coordination_tasks WHERE run_id = ? ORDER BY created_at ASC",
            (run_id,),
        )
        return [
            CoordinationTask(
                task_id=r["task_id"],
                run_id=r["run_id"],
                title=r["title"],
                status=r["status"],
                assignee_session_id=r["assignee_session_id"],
                assignee_role=r["assignee_role"],
                dependencies=json.loads(r["dependencies_json"]),
                artifacts=json.loads(r["artifacts_json"]),
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in cur.fetchall()
        ]

    # ── Message & Outbox Operations (Transactional) ─────────────

    def save_message_and_outbox(
        self, message: AgentMessage
    ) -> tuple[AgentMessage, OutboxItem]:
        """Atomically persist an AgentMessage and queue its Outbox delivery item."""
        conn = self._get_conn()
        outbox = OutboxItem(
            message_id=message.message_id,
            target_session_id=message.recipient_session_id,
            status="pending",
            payload_json=json.dumps(message.to_dict()),
            created_at=message.created_at,
            updated_at=message.updated_at,
        )
        event = CoordinationEvent(
            root_session_id=message.root_session_id,
            run_id=message.run_id,
            task_id=message.task_id,
            actor_session_id=message.sender_session_id,
            event_type=f"message.{message.intent}",
            payload={"message_id": message.message_id, "recipient": message.recipient_session_id},
            created_at=message.created_at,
        )

        with conn:
            conn.execute(
                """
                INSERT INTO agent_messages (
                    message_id, schema_version, root_session_id, run_id, task_id,
                    sender_session_id, sender_role, recipient_session_id, recipient_role,
                    kind, intent, payload_json, artifacts_json, correlation_id,
                    in_reply_to, idempotency_key, message_state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.schema_version,
                    message.root_session_id,
                    message.run_id,
                    message.task_id,
                    message.sender_session_id,
                    message.sender_role,
                    message.recipient_session_id,
                    message.recipient_role,
                    message.kind,
                    message.intent,
                    json.dumps(message.payload),
                    json.dumps(message.artifacts),
                    message.correlation_id,
                    message.in_reply_to,
                    message.idempotency_key,
                    message.message_state,
                    message.created_at,
                    message.updated_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO coordination_outbox (
                    item_id, message_id, target_session_id, status, payload_json,
                    retry_count, next_retry_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outbox.item_id,
                    outbox.message_id,
                    outbox.target_session_id,
                    outbox.status,
                    outbox.payload_json,
                    outbox.retry_count,
                    outbox.next_retry_at,
                    outbox.created_at,
                    outbox.updated_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO coordination_events (
                    event_id, root_session_id, run_id, task_id, actor_session_id,
                    event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.root_session_id,
                    event.run_id,
                    event.task_id,
                    event.actor_session_id,
                    event.event_type,
                    json.dumps(event.payload),
                    event.created_at,
                ),
            )
        return message, outbox

    def get_message(self, message_id: str) -> AgentMessage | None:
        conn = self._get_conn()
        cur = conn.execute("SELECT * FROM agent_messages WHERE message_id = ?", (message_id,))
        row = cur.fetchone()
        if not row:
            return None
        return AgentMessage(
            message_id=row["message_id"],
            schema_version=row["schema_version"],
            root_session_id=row["root_session_id"],
            run_id=row["run_id"],
            task_id=row["task_id"],
            sender_session_id=row["sender_session_id"],
            sender_role=row["sender_role"],
            recipient_session_id=row["recipient_session_id"],
            recipient_role=row["recipient_role"],
            kind=row["kind"],
            intent=row["intent"],
            payload=json.loads(row["payload_json"]),
            artifacts=json.loads(row["artifacts_json"]),
            correlation_id=row["correlation_id"],
            in_reply_to=row["in_reply_to"],
            idempotency_key=row["idempotency_key"],
            message_state=row["message_state"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_messages(
        self, root_session_id: str, recipient_session_id: str | None = None
    ) -> list[AgentMessage]:
        conn = self._get_conn()
        if recipient_session_id:
            cur = conn.execute(
                """
                SELECT * FROM agent_messages
                WHERE root_session_id = ? AND recipient_session_id = ?
                ORDER BY created_at ASC
                """,
                (root_session_id, recipient_session_id),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM agent_messages WHERE root_session_id = ? ORDER BY created_at ASC",
                (root_session_id,),
            )
        return [
            AgentMessage(
                message_id=r["message_id"],
                schema_version=r["schema_version"],
                root_session_id=r["root_session_id"],
                run_id=r["run_id"],
                task_id=r["task_id"],
                sender_session_id=r["sender_session_id"],
                sender_role=r["sender_role"],
                recipient_session_id=r["recipient_session_id"],
                recipient_role=r["recipient_role"],
                kind=r["kind"],
                intent=r["intent"],
                payload=json.loads(r["payload_json"]),
                artifacts=json.loads(r["artifacts_json"]),
                correlation_id=r["correlation_id"],
                in_reply_to=r["in_reply_to"],
                idempotency_key=r["idempotency_key"],
                message_state=r["message_state"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in cur.fetchall()
        ]

    # ── Outbox Delivery Operations ──────────────────────────────

    def fetch_pending_outbox(self, limit: int = 20) -> list[OutboxItem]:
        conn = self._get_conn()
        import time

        now = time.time()
        cur = conn.execute(
            """
            SELECT * FROM coordination_outbox
            WHERE status IN ('pending', 'leased') AND next_retry_at <= ?
            ORDER BY next_retry_at ASC
            LIMIT ?
            """,
            (now, limit),
        )
        return [
            OutboxItem(
                item_id=r["item_id"],
                message_id=r["message_id"],
                target_session_id=r["target_session_id"],
                status=r["status"],
                payload_json=r["payload_json"],
                retry_count=r["retry_count"],
                next_retry_at=r["next_retry_at"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in cur.fetchall()
        ]

    def record_delivery_attempt(
        self, attempt: DeliveryAttempt, mark_outbox_done: bool = False
    ) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute(
                """
                INSERT INTO delivery_attempts (
                    attempt_id, message_id, target_session_id, target_harness,
                    delivery_mode, delivery_state, injection_receipt_json, error,
                    attempt_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.attempt_id,
                    attempt.message_id,
                    attempt.target_session_id,
                    attempt.target_harness,
                    attempt.delivery_mode,
                    attempt.delivery_state,
                    json.dumps(attempt.injection_receipt) if attempt.injection_receipt else None,
                    attempt.error,
                    attempt.attempt_count,
                    attempt.created_at,
                    attempt.updated_at,
                ),
            )
            if mark_outbox_done:
                conn.execute(
                    "UPDATE coordination_outbox SET status = ? WHERE message_id = ?",
                    (attempt.delivery_state, attempt.message_id),
                )

    # ── Events Timeline Operations ──────────────────────────────

    def record_event(self, event: CoordinationEvent) -> CoordinationEvent:
        conn = self._get_conn()
        with conn:
            conn.execute(
                """
                INSERT INTO coordination_events (
                    event_id, root_session_id, run_id, task_id, actor_session_id,
                    event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.root_session_id,
                    event.run_id,
                    event.task_id,
                    event.actor_session_id,
                    event.event_type,
                    json.dumps(event.payload),
                    event.created_at,
                ),
            )
        return event

    def list_events(
        self, root_session_id: str, since: float | None = None
    ) -> list[CoordinationEvent]:
        conn = self._get_conn()
        if since is not None:
            cur = conn.execute(
                """
                SELECT * FROM coordination_events
                WHERE root_session_id = ? AND created_at > ?
                ORDER BY created_at ASC
                """,
                (root_session_id, since),
            )
        else:
            cur = conn.execute(
                """
                SELECT * FROM coordination_events
                WHERE root_session_id = ?
                ORDER BY created_at ASC
                """,
                (root_session_id,),
            )
        return [
            CoordinationEvent(
                event_id=r["event_id"],
                root_session_id=r["root_session_id"],
                run_id=r["run_id"],
                task_id=r["task_id"],
                actor_session_id=r["actor_session_id"],
                event_type=r["event_type"],
                payload=json.loads(r["payload_json"]),
                created_at=r["created_at"],
            )
            for r in cur.fetchall()
        ]
